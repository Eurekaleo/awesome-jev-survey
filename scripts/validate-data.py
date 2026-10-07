#!/usr/bin/env python3
"""Validate the canonical data in data/*.json.

    python3 scripts/validate-data.py          # exit 1 on any error
    python3 scripts/validate-data.py --quiet  # only print problems

Checks: unique IDs and BibTeX keys; controlled vocabularies; references between
papers, claims, repositories, relations and synthesis entries; missing values
carry a reason; openness rules (a URL for located code, no self-declared
reproduction without a run log); URL schemes; and that no screened-out record
sits in data/papers.json.
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jevlib  # noqa: E402

errors, warnings = [], []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def iso(value, where):
    try:
        dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        err(f'{where}: not an ISO date: {value!r}')


def check_url(u, where, required=False):
    if u is None:
        if required:
            err(f'{where}: missing URL')
        return
    if not jevlib.safe_url(u) or not str(u).startswith(('https://', 'http://')):
        err(f'{where}: URL must be absolute http(s): {u!r}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    d = jevlib.load_all()
    tax = d['taxonomy']
    V = {k: {x['id'] for x in tax[k]} for k in ('stages', 'method_families', 'topics', 'applications', 'model_relationships',
                                               'test_levels', 'measurement_scopes', 'evidence_types', 'findings',
                                               'background_groups', 'repository_types')}
    open_status = set(tax['openness_statuses'])
    papers = d['papers']['papers']
    claims = d['claims']['claims']
    repos = d['repositories']['repositories']
    sources = d['sources']['sources']

    # --- papers ---------------------------------------------------------
    seen = {}
    for field in ('id', 'arxiv_id', 'bibtex_key'):
        vals = [p[field] for p in papers]
        dup = {v for v in vals if vals.count(v) > 1}
        if dup:
            err(f'papers: duplicate {field}: {sorted(dup)}')
    claim_ids = {c['id'] for c in claims}
    for p in papers:
        w = p['id']
        if p['tier'] not in ('core', 'peripheral', 'background'):
            err(f'{w}: bad tier {p["tier"]}')
        for f in ('title', 'authors', 'published_at', 'updated_at', 'retrieved_at', 'url', 'versioned_id', 'bibtex_key'):
            if not p.get(f):
                err(f'{w}: missing {f}')
        iso(p['published_at'], w)
        iso(p['updated_at'], w)
        check_url(p['url'], w, required=True)
        check_url(p.get('pdf_url'), w)
        if p['publication_type'] not in ('preprint', 'conference', 'journal', 'other'):
            err(f'{w}: bad publication_type')
        if p.get('venue_verified_at') and p['venue_source'] != 'verified':
            err(f'{w}: venue_verified_at set without verification record')
        for key, vocab in (('topics', 'topics'), ('stages', 'stages'), ('method_families', 'method_families'),
                           ('applications', 'applications'), ('model_relationship', 'model_relationships')):
            bad = set(p.get(key) or []) - V[vocab]
            if bad:
                err(f'{w}: unknown {key} {sorted(bad)}')
        if not p.get('model_relationship'):
            err(f'{w}: model_relationship is empty')
        for f, rec in p['openness'].items():
            if rec['status'] not in open_status:
                err(f'{w}: openness.{f} unknown status {rec["status"]}')
        if p['openness']['reproduction']['status'] == 'reproduced_by_this_review' and not p['openness']['reproduction'].get('url'):
            err(f'{w}: reproduction claimed without a run-log URL')
        for c in p['claims']:
            if c not in claim_ids:
                err(f'{w}: unknown claim {c}')
        if p['tier'] in ('core', 'peripheral'):
            for f in ('short_title', 'relationship', 'summary', 'task', 'main_finding', 'caveats', 'survey_use', 'evidence_locator', 'test_level'):
                if not p.get(f):
                    err(f'{w}: core/peripheral record missing {f}')
            if p['test_level'] not in V['test_levels']:
                err(f'{w}: bad test_level')
            if p['openness']['code']['status'] == 'available' and not p['openness']['code'].get('url'):
                err(f'{w}: code marked available without URL')
            for mv in p['model_versions']:
                if mv.get('version') is None and not mv.get('reason'):
                    err(f'{w}: model version is null without a reason')
            if not p['claims']:
                err(f'{w}: core/peripheral record has no evidence records')
        else:
            if p.get('background_group') not in V['background_groups']:
                err(f'{w}: bad background_group')
            if not p.get('role'):
                err(f'{w}: background record missing role')
        seen[p['arxiv_id']] = p

    fam = {}
    for p in papers:
        if p.get('study_family_id'):
            fam.setdefault(p['study_family_id'], []).append(p['id'])

    # --- claims ---------------------------------------------------------
    ids = [c['id'] for c in claims]
    if len(ids) != len(set(ids)):
        err('claims: duplicate id')
    subjects = {p['id'] for p in papers} | {r['id'] for r in repos} | {s['id'] for s in sources}
    for c in claims:
        w = c['id']
        if c['subject'] not in subjects:
            err(f'{w}: unknown subject {c["subject"]}')
        if c['evidence_type'] not in V['evidence_types']:
            err(f'{w}: bad evidence_type')
        if c['measurement_scope'] not in V['measurement_scopes']:
            err(f'{w}: bad measurement_scope')
        if c['test_level'] not in V['test_levels']:
            err(f'{w}: bad test_level')
        check_url(c['source_url'], w, required=True)
        if not c.get('locator'):
            err(f'{w}: missing locator')
        if c['independently_reproduced']:
            err(f'{w}: independently_reproduced must stay false unless a run log is linked')
        for fld in ('sample_size', 'model_version', 'hardware'):
            v = c[fld]
            val = v.get('n') if fld == 'sample_size' else v.get('value')
            if val is None and not v.get('reason'):
                err(f'{w}: {fld} is null without a reason')
        if not c['findings']:
            warn(f'{w}: claim linked to no finding')
        for link in c['findings']:
            if link['id'] not in V['findings'] or link['relation'] not in ('supports', 'qualifies', 'challenges'):
                err(f'{w}: bad finding link {link}')
        if c['subject'].startswith('arxiv:') and w not in next((p['claims'] for p in papers if p['id'] == c['subject']), []):
            err(f'{w}: not listed on its paper')

    # --- repositories ---------------------------------------------------
    roles = {x['id'] for x in tax['ecosystem_roles']}
    rid = [r['id'] for r in repos]
    if len(rid) != len(set(rid)):
        err('repositories: duplicate id')
    for r in repos:
        w = r['id']
        if r['id'] != 'gh:' + r['full_name'].lower():
            err(f'{w}: id must be gh:<owner/name lowercased>')
        if r['type'] not in V['repository_types']:
            err(f'{w}: bad type {r["type"]}')
        check_url(r['url'], w, required=True)
        check_url(r.get('readme_url'), w)
        if r.get('method_family') and r['method_family'] not in V['method_families']:
            err(f'{w}: bad method_family')
        for pid in r.get('related_papers') or []:
            if pid not in seen and pid.replace('arxiv:', '') not in seen:
                err(f'{w}: unknown related paper {pid}')
        if r.get('ecosystem') not in roles:
            err(f'{w}: bad ecosystem role {r.get("ecosystem")}')
        if r.get('domain') and r['domain'] not in V['applications']:
            err(f'{w}: unknown domain {r["domain"]}')
        check_url(r.get('weights_url'), w)
        check_url(r.get('data_url'), w)
        if r['executed']:
            err(f'{w}: executed=true requires a run log (not supported in this release)')
        if 'priority' in r['sets'] and not r.get('availability'):
            err(f'{w}: priority resource without availability record')
        if r['license'] == 'NOASSERTION' and 'NOASSERTION' in (r.get('summary') or ''):
            warn(f'{w}: summary interprets NOASSERTION')

    # --- relations & synthesis -----------------------------------------
    known = subjects | {r['id'] for r in repos}
    for rel in d['review-relations']['relations']:
        for key in ('source', 'target'):
            if rel.get(key) and rel[key] not in known:
                err(f'relation {rel["type"]}: unknown {key} {rel[key]}')
        for m in rel.get('members', []) + rel.get('targets', []):
            if m not in known:
                err(f'relation {rel["type"]}: unknown member {m}')
        if rel['type'] == 'study_family':
            got = sorted(fam.get(rel['id'], []))
            if got != sorted(rel['members']):
                err(f'study family {rel["id"]}: papers {got} vs relation members {rel["members"]}')
    for entry in tax['failure_modes'] + tax['application_cards']:
        for c in entry['claims']:
            if c not in claim_ids:
                err(f'synthesis {entry["id"]}: unknown claim {c}')
        for pid in entry.get('papers', []) + entry.get('background', []):
            if pid not in subjects:
                err(f'synthesis {entry["id"]}: unknown paper {pid}')
    for s in sources:
        check_url(s['url'], s['id'], required=True)

    screening = json.loads((jevlib.ROOT / 'research' / 'screening.json').read_text())['records']
    for r in screening:
        if r['decision'] == 'excluded' and r['arxiv_id'] in seen:
            err(f'arxiv:{r["arxiv_id"]} is in data/papers.json but marked excluded in research/screening.json')
    stats = jevlib.compute_stats(d)
    if any(r['type'] == 'unverified' for r in repos):
        err('unverified outgoing candidates must not enter repositories.json')

    if not args.quiet or errors or warnings:
        print(f'papers {stats["papers"]} (core {stats["core"]}, peripheral {stats["peripheral"]}, background {stats["background"]}); '
              f'claims {stats["claims"]}; repositories {stats["repos_unique"]}')
    for w in warnings:
        print('WARN ', w)
    for e in errors:
        print('ERROR', e)
    if errors:
        print(f'{len(errors)} error(s)')
        sys.exit(1)
    if not args.quiet:
        print('data valid')


if __name__ == '__main__':
    main()
