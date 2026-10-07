"""Shared helpers for the Jev survey build: loading, statistics, escaping and exports.

Standard library only. Every script imports this module so that the website,
README, BibTeX, CSV exports and manuscript tables read the same numbers.
"""
import datetime as dt
import html
import json
import pathlib
import re
from collections import Counter
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION = '0.3.1'  # release of the survey, data and site (keep in step with CITATION.cff and CHANGELOG.md)
DATA = ROOT / 'data'
FILES = ['papers', 'claims', 'repositories', 'taxonomy', 'review-relations', 'sources', 'search-runs']


def load_all():
    d = {name: json.loads((DATA / f'{name}.json').read_text(encoding='utf-8')) for name in FILES}
    cfg_path = ROOT / 'site.config.json'
    d['config'] = json.loads(cfg_path.read_text(encoding='utf-8')) if cfg_path.exists() else {}
    return d


def esc(value):
    return html.escape('' if value is None else str(value), quote=True)


def safe_url(value):
    """Allow http(s) and site-relative links only; anything else becomes None."""
    if not value:
        return None
    value = str(value).strip()
    parsed = urlparse(value)
    if parsed.scheme in ('http', 'https') and parsed.netloc:
        return value
    if not parsed.scheme and not value.startswith(('//', '\\')):
        return value
    return None


def csv_safe(value):
    """Neutralise spreadsheet formula injection (cells starting with = + - @, tab or CR)."""
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        value = '; '.join(str(v) for v in value)
    elif isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    s = str(value)
    return "'" + s if s[:1] in ('=', '+', '-', '@', '\t', '\r') else s


def fmt_date(iso, style='long'):
    if not iso:
        return ''
    d = dt.date.fromisoformat(iso[:10])
    return f'{d.day} {d.strftime("%b")} {d.year}' if style == 'long' else d.isoformat()


def label(paper):
    return paper.get('short_title') or paper['title']


def by_id(items, key='id'):
    return {x[key]: x for x in items}


def compute_stats(d):
    papers = d['papers']['papers']
    claims = d['claims']['claims']
    repos = d['repositories']['repositories']
    tax = d['taxonomy']
    tiers = Counter(p['tier'] for p in papers)
    primary = [p for p in papers if p['tier'] in ('core', 'peripheral')]
    core = [p for p in papers if p['tier'] == 'core']
    stage_counts = {s['id']: sum(1 for p in primary if s['id'] in p['stages']) for s in tax['stages']}
    family_counts = {f['id']: sum(1 for p in primary if f['id'] in p['method_families']) for f in tax['method_families']}
    rel_counts = {r['id']: sum(1 for p in papers if r['id'] in p['model_relationship']) for r in tax['model_relationships']}
    open_counts = {f: Counter(p['openness'][f]['status'] for p in primary) for f in ('code', 'weights', 'data', 'predictions', 'recomputable', 'reproduction')}
    finding_counts = {f['id']: Counter(link['relation'] for c in claims for link in c['findings'] if link['id'] == f['id']) for f in tax['findings']}
    sets = Counter(s for r in repos for s in r['sets'])
    runs = by_id(d['search-runs']['runs'])
    snap_run = runs['snapshot-arxiv-2026-09-23']
    inc = runs.get('increment-arxiv-2026-09-23T0818Z', {})
    gh = runs['snapshot-github-2026-09-23']
    return dict(
        papers=len(papers), core=tiers['core'], peripheral=tiers['peripheral'], background=tiers['background'],
        core_plus=len(primary),
        claims=len(claims), claims_by_type=dict(Counter(c['evidence_type'] for c in claims)),
        claims_papers=sum(1 for c in claims if c['subject'].startswith('arxiv:')),
        findings=len(tax['findings']), stages=stage_counts, families=family_counts, relationships=rel_counts,
        openness={k: dict(v) for k, v in open_counts.items()}, finding_counts={k: dict(v) for k, v in finding_counts.items()},
        repos_unique=len(repos), priority_repos=sets['priority'],
        catalogues=gh['catalogues_audited'], readmes=gh['readmes_fetched'],
        source_files_read=sum(len(r['selected_source_read']) for r in repos),
        source_repos_read=sum(1 for r in repos if r['selected_source_read']),
        outgoing_candidates=gh['outgoing_candidates_all'],
        github_discovery=gh['unique_results'],
        arxiv_queries=snap_run['accepted_queries'], arxiv_hits=snap_run['hits_before_dedup'], arxiv_screened=snap_run['unique_screened'],
        increment_queries=len(inc.get('expansion', [])), increment_new=inc.get('new_candidates', 0),
        increment_added=inc.get('decisions', {}).get('include_background', 0),
        sources=len(d['sources']['sources']), cutoff=d['papers']['meta']['cutoff'],
        years=sorted({p['year'] for p in papers}, reverse=True),
    )


BIB_ESCAPES = {'&': r'\&', '%': r'\%', '#': r'\#', '_': r'\_'}


def bib_text(s):
    s = str(s)
    for k, v in BIB_ESCAPES.items():
        s = s.replace(k, v)
    return s


def bibtex_for_paper(p):
    fields = [('title', '{' + bib_text(p['title']) + '}'), ('author', ' and '.join(p['authors'])), ('year', str(p['year'])),
              ('eprint', p['arxiv_id']), ('archivePrefix', 'arXiv'), ('primaryClass', p['categories'][0] if p['categories'] else ''),
              ('url', p['url'])]
    if p.get('doi'):
        fields.append(('doi', p['doi']))
    if p.get('venue_source') in ('arxiv_comment', 'arxiv_journal_ref'):
        fields.append(('note', bib_text(p['venue'])))
    body = ',\n'.join(f'  {k} = {{{v}}}' for k, v in fields if v)
    return f'@misc{{{p["bibtex_key"]},\n{body}\n}}\n'


def bibtex_for_source(s):
    fields = [('title', '{' + bib_text(s['title']) + '}'), ('author', '{' + (s.get('author') or s['publisher']) + '}'),
              ('howpublished', bib_text(s['publisher'])), ('year', (s.get('date') or s['accessed'])[:4]), ('url', s['url']),
              ('note', bib_text(f"Accessed {s['accessed']}"))]
    body = ',\n'.join(f'  {k} = {{{v}}}' for k, v in fields if v)
    return f'@misc{{{s["bibtex_key"]},\n{body}\n}}\n'


def bibtex_for_repo(r, key):
    title = r['full_name'] + (f" (commit {r['commit'][:10]})" if r.get('commit') else '')
    fields = [('title', '{' + bib_text(title) + '}'), ('author', '{' + r['full_name'].split('/')[0] + '}'), ('howpublished', 'GitHub repository'),
              ('year', (r.get('commit_date') or r.get('accessed_at') or '2026')[:4]),
              ('url', r.get('readme_url') or r['url']), ('note', bib_text(f"Accessed {(r.get('accessed_at') or '')[:10]}; README{' and selected source' if r['selected_source_read'] else ''} read, not executed"))]
    body = ',\n'.join(f'  {k} = {{{v}}}' for k, v in fields if v)
    return f'@misc{{{key},\n{body}\n}}\n'


def repo_key(repo_id):
    return 'gh_' + re.sub(r'[^a-z0-9]+', '_', repo_id.split(':', 1)[1]).strip('_')
