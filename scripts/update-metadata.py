#!/usr/bin/env python3
"""Incremental arXiv search and metadata refresh for the Jev survey.

Writes a dated working directory under research/increments/ (not committed)
and never modifies data/. Records already in data/papers.json or
research/screening.json are skipped; screening decisions for the new
candidates are made by a person afterwards (see docs/methodology.md).

Usage:
  python3 scripts/update-metadata.py                 # accepted + expansion queries, refresh known IDs
  python3 scripts/update-metadata.py --no-expansion  # rerun only the 17 accepted snapshot queries
  python3 scripts/update-metadata.py --until 202610312359

Standard library only. Requests are sequential and spaced >3 s apart, as the
arXiv API terms ask.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
NS = {'a': 'http://www.w3.org/2005/Atom', 'o': 'http://a9.com/-/spec/opensearch/1.1/', 'x': 'http://arxiv.org/schemas/atom'}
API = 'https://export.arxiv.org/api/query?'
UA = {'User-Agent': 'JevSurveyLiteratureUpdate/1.0 (academic review; sequential requests)',
      'Accept': 'application/atom+xml, application/xml;q=0.9, */*;q=0.8'}
MAX_PLAUSIBLE = 2000  # a narrow review query returning more than this is rejected, not screened

# The 17 queries accepted in the 2026-09-23 search (listed in docs/methodology.md).
# Strings are reproduced exactly; only the run date differs.
ACCEPTED = [
    ('Q01_jev', 'all:jev'),
    ('Q02_typesafe', 'all:typesafe'),
    ('Q03_system_one', 'all:"System One" AND submittedDate:[202601010000 TO {until}]'),
    ('Q04_typed_decision', 'all:"typed decision"'),
    ('Q05_calibrated_decisions', 'all:"calibrated decisions"'),
    ('Q06_jevqa', 'all:jevqa'),
    ('Q07_jev_survey', '(all:jev OR all:typesafe OR all:"System One") AND (ti:survey OR ti:review)'),
    ('Q08_decision_survey', '(all:"decision model" OR all:"decision models") AND (ti:survey OR ti:review) AND submittedDate:[202501010000 TO {until}]'),
    ('Q09_rlcd', 'all:RLCD AND (cat:cs.AI OR cat:cs.CL OR cat:cs.LG)'),
    ('Q10_recent_decision_models', '(ti:"decision model" OR abs:"decision model" OR abs:"decision-only" OR abs:"typed decisions" OR abs:"typed questions") AND submittedDate:[202609140000 TO {until}]'),
    ('Q11_recent_systemone', '(all:"system-one" OR all:systemone OR all:"TypeSafe AI") AND submittedDate:[202609140000 TO {until}]'),
    ('Q12a_openjev', 'ti:openjev OR abs:openjev'),
    ('Q12b_nanojev', 'ti:nanojev OR abs:nanojev'),
    ('Q12c_jevlite', 'ti:jevlite OR abs:jevlite'),
    ('Q12d_jevlike', 'ti:jevlike OR abs:jevlike'),
    ('Q13_rlcd_expanded', 'all:"Reinforcement Learning for Calibrated Decisions"'),
    ('Q14_typed_surveys', '(ti:survey OR ti:review OR ti:overview) AND (all:"typed decision" OR all:"decision-only" OR all:"non-generative") AND submittedDate:[202001010000 TO {until}]'),
]

# Expansion queries added in the first increment. They widen coverage beyond the
# brand name (interface vocabulary, open-model names, related framing) and are
# logged separately so the snapshot denominator stays interpretable.
# Status 'retired' marks queries that returned implausibly broad totals on
# 2026-09-23; they are replaced by narrow title/abstract variants and are only
# replayed from saved pages when resuming that run.
EXPANSION = [
    ('X01_machine_native', 'all:"machine-native" AND submittedDate:[202601010000 TO {until}]', 'active'),
    ('X02_decision_head', '(abs:"decision head" OR abs:"decision heads") AND submittedDate:[202606010000 TO {until}]', 'active'),
    ('X03_open_model_names', '(all:laya OR all:anyjev OR all:semif OR all:djev OR all:localjev OR all:"this-that-model" OR all:"visual jev") AND submittedDate:[202609010000 TO {until}]', 'retired'),
    ('X03a_laya', '(ti:laya OR abs:laya) AND submittedDate:[202609010000 TO {until}]', 'active'),
    ('X03b_anyjev', 'ti:anyjev OR abs:anyjev', 'active'),
    ('X03c_semif', 'ti:semif OR abs:semif', 'active'),
    ('X03d_djev', 'ti:djev OR abs:djev', 'active'),
    ('X03e_localjev', 'ti:localjev OR abs:localjev', 'active'),
    ('X03f_this_that', 'ti:"this-that-model" OR abs:"this-that-model"', 'active'),
    ('X03g_visual_jev', 'ti:"visual jev" OR abs:"visual jev"', 'active'),
    ('X04_system1_model', '(all:"System 1 model" OR all:"System-1 model" OR all:"system one model" OR all:"System One models") AND submittedDate:[202609010000 TO {until}]', 'active'),
    ('X05_typed_outputs', '(abs:"typed output" OR abs:"typed outputs" OR abs:"typed probabilistic" OR abs:"option probabilities") AND submittedDate:[202606010000 TO {until}]', 'active'),
    ('X06_confidence_gating', '(abs:"confidence-gated" OR abs:"confidence gated" OR abs:"selective escalation" OR abs:"escalate when") AND submittedDate:[202606010000 TO {until}]', 'active'),
    ('X07_benchmark_names', '(all:jevbench OR all:callscreenbench OR all:"sysone" OR all:"workflow evals")', 'active'),
    ('X08_decision_models_plural', '(ti:"decision models" OR abs:"decision models") AND (cat:cs.CL OR cat:cs.AI OR cat:cs.LG) AND submittedDate:[202609140000 TO {until}]', 'active'),
    ('X09_structured_decisions', '(abs:"structured decisions" OR abs:"fast structured decisions" OR abs:"smart if-statements" OR abs:"semantic if") AND submittedDate:[202606010000 TO {until}]', 'retired'),
    ('X09a_structured_decisions', 'abs:"structured decisions" AND submittedDate:[202606010000 TO {until}]', 'active'),
    ('X09b_smart_if', 'abs:"smart if-statements" OR abs:"smart if statements"', 'active'),
    ('X09c_semantic_if', 'abs:"semantic if" OR abs:"semantic ifs"', 'active'),
]


def clean(s):
    return ' '.join((s or '').split())


def parse_entry(e):
    aid = e.findtext('a:id', '', NS).split('/abs/')[-1]
    return dict(
        arxiv_id=re.sub(r'v\d+$', '', aid), versioned_id=aid,
        title=clean(e.findtext('a:title', '', NS)),
        authors=[clean(a.findtext('a:name', '', NS)) for a in e.findall('a:author', NS)],
        published=e.findtext('a:published', '', NS), updated=e.findtext('a:updated', '', NS),
        abstract=clean(e.findtext('a:summary', '', NS)),
        categories=[c.attrib['term'] for c in e.findall('a:category', NS)],
        comment=clean(e.findtext('x:comment', '', NS)), journal_ref=clean(e.findtext('x:journal_ref', '', NS)),
        doi=clean(e.findtext('x:doi', '', NS)), url='https://arxiv.org/abs/' + aid, pdf_url='https://arxiv.org/pdf/' + aid)


def _get(url):
    """GET one API page. Prefer curl; fall back to urllib.

    Some Python builds (observed: Anaconda 3.10 / OpenSSL 1.1) are refused with
    HTTP 406 by export.arxiv.org regardless of headers, while curl and the macOS
    system Python are served normally. Sending one request per page (not a
    urllib attempt followed by curl) also keeps within the API rate limit.
    """
    if shutil.which('curl'):
        out = subprocess.run(['curl', '-sS', '--max-time', '60', '-A', UA['User-Agent'], '-H', 'Accept: ' + UA['Accept'],
                              '-w', '\n%{http_code}', url], capture_output=True)
        body, _, code = out.stdout.rpartition(b'\n')
        if out.returncode != 0 or code.strip() != b'200':
            raise RuntimeError(f'HTTP {code.decode().strip() or "?"} (curl exit {out.returncode})')
        return body
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def fetch(params, raw_path, resume=False):
    url = API + urllib.parse.urlencode(params)
    if resume and raw_path.exists() and raw_path.stat().st_size > 0:
        return url, ET.fromstring(raw_path.read_bytes()), True
    for attempt in range(5):
        try:
            data = _get(url)
            raw_path.write_bytes(data)
            return url, ET.fromstring(data), False
        except Exception as exc:
            if attempt == 4:
                raise
            limited = any(code in str(exc) for code in ('429', '503'))
            wait = (90 if limited else 10) * (attempt + 1)  # rate limits need a real pause
            print(f'  retry in {wait}s after {exc!r}', flush=True)
            time.sleep(wait)


def run_query(key, query, raw_dir, logs, found, resume=False):
    start = 0
    while True:
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        params = dict(search_query=query, start=start, max_results=100, sortBy='submittedDate', sortOrder='descending')
        entry = dict(query_id=key, query=query, start=start, retrieved_at_utc=stamp)
        try:
            url, root, cached = fetch(params, raw_dir / f'{key}_{start}.xml', resume)
            if cached:  # page saved by the interrupted run; keep its real retrieval time
                mtime = (raw_dir / f'{key}_{start}.xml').stat().st_mtime
                entry.update(retrieved_at_utc=dt.datetime.fromtimestamp(mtime, dt.timezone.utc).isoformat(), reused_page=True)
            total = int(root.findtext('o:totalResults', '0', NS))
            entry.update(url=url, total_results=total)
            if total > MAX_PLAUSIBLE:
                entry.update(status='rejected_implausibly_broad', returned=0, ids=[])
                logs.append(entry)
                print(f'{key}: REJECTED total={total}', flush=True)
                return
            papers = [parse_entry(x) for x in root.findall('a:entry', NS)]
            for p in papers:
                found.setdefault(p['arxiv_id'], {**p, 'queries': []})['queries'].append(key)
            entry.update(status='ok', returned=len(papers), ids=[p['arxiv_id'] for p in papers])
            logs.append(entry)
            print(f'{key}: total={total} page_start={start} returned={len(papers)}', flush=True)
            start += len(papers)
            if start >= total or not papers:
                return
        except Exception as exc:
            entry.update(status='error', error=repr(exc))
            logs.append(entry)
            print(f'{key}: ERROR {exc!r}', flush=True)
            return
        finally:
            if not entry.get('reused_page'):
                time.sleep(3.2)


def refresh_ids(ids, raw_dir):
    out = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        _, root, _ = fetch(dict(id_list=','.join(chunk), max_results=len(chunk)), raw_dir / f'id_refresh_{i}.xml')
        for e in root.findall('a:entry', NS):
            p = parse_entry(e)
            out[p['arxiv_id']] = p
        time.sleep(3.2)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--until', default=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d2359'),
                    help='upper submittedDate bound (YYYYMMDDHHMM); default: end of today UTC')
    ap.add_argument('--no-expansion', action='store_true')
    ap.add_argument('--no-refresh', action='store_true', help='skip id_list refresh of bibliography IDs')
    ap.add_argument('--resume', metavar='RUN_ID', help='continue an interrupted run, reusing pages already saved')
    args = ap.parse_args()

    run_id = args.resume or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H%MZ')
    run_dir = ROOT / 'research' / 'increments' / run_id
    raw_dir = run_dir / 'api'
    raw_dir.mkdir(parents=True, exist_ok=True)

    papers = json.loads((ROOT / 'data' / 'papers.json').read_text())['papers']
    known_bib = {p['arxiv_id'] for p in papers if p.get('arxiv_id')}
    screened = {r['arxiv_id'] for r in json.loads((ROOT / 'research' / 'screening.json').read_text())['records']}
    # decisions from local runs that have not been merged into research/screening.json yet
    for prev in sorted((ROOT / 'research' / 'increments').glob('*/screening.json')):
        screened |= {r['arxiv_id'] for r in json.loads(prev.read_text())}

    logs, found = [], {}
    queries = [(k, q, 'accepted_snapshot_query') for k, q in ACCEPTED]
    if not args.no_expansion:
        queries += [(k, q, 'expansion_query') for k, q, status in EXPANSION
                    if status == 'active' or (args.resume and (raw_dir / f'{k}_0.xml').exists())]
    for key, template, kind in queries:
        before = len(logs)
        run_query(key, template.format(until=args.until), raw_dir, logs, found, resume=bool(args.resume))
        for entry in logs[before:]:
            entry['kind'] = kind

    new = {k: v for k, v in found.items() if k not in screened and k not in known_bib}
    refreshed = {} if args.no_refresh or not known_bib else refresh_ids(sorted(known_bib), raw_dir)
    changes = []
    for p in papers:
        r = refreshed.get(p.get('arxiv_id'))
        if not r:
            continue
        # a journal reference is stored as the venue when arXiv supplies one
        mine = dict(p, journal_ref=p.get('venue') if p.get('venue_source') == 'arxiv_journal_ref' else None)
        diff = {f: [mine.get(f), r.get(f)] for f in ('versioned_id', 'title', 'journal_ref', 'doi')
                if (mine.get(f) or '') != (r.get(f) or '')}
        if diff:
            changes.append(dict(arxiv_id=p['arxiv_id'], changes=diff))

    summary = dict(
        run_id=run_id, retrieved_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(), until=args.until,
        accepted_queries=sum(1 for _, _, k in queries if k == 'accepted_snapshot_query'),
        expansion_queries=sum(1 for _, _, k in queries if k == 'expansion_query'),
        hits_before_dedup=sum(e.get('returned', 0) for e in logs if e.get('status') == 'ok'),
        unique_records=len(found), previously_screened=len([k for k in found if k in screened or k in known_bib]),
        new_candidates=len(new), refreshed_ids=len(refreshed), metadata_changes=len(changes),
        errors=[e for e in logs if e.get('status') == 'error'],
        rejected=[e['query_id'] for e in logs if e.get('status') == 'rejected_implausibly_broad'])
    (run_dir / 'query_log.json').write_text(json.dumps(logs, ensure_ascii=False, indent=2))
    (run_dir / 'records.json').write_text(json.dumps(list(found.values()), ensure_ascii=False, indent=2))
    (run_dir / 'new_candidates.json').write_text(json.dumps(list(new.values()), ensure_ascii=False, indent=2))
    (run_dir / 'metadata_changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
    (run_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != 'errors'}, indent=2))
    if summary['errors']:
        print(f"{len(summary['errors'])} query pages failed; see query_log.json", file=sys.stderr)
    print(f'New candidates to screen: {run_dir / "new_candidates.json"}')


if __name__ == '__main__':
    main()
