#!/usr/bin/env python3
"""Measure what a first visit downloads, and enforce a page-weight budget.

    python3 scripts/measure.py            # print a report and write reports/performance.json
    python3 scripts/measure.py --budget   # exit 1 if a budget is exceeded (CI)

This is a static byte count (raw and gzip) of the files the page requests
before any interaction. It is not a Lighthouse score or a field measurement;
fonts from Google Fonts are listed but not counted. drawer.json and lab.js are
loaded lazily and reported separately.
"""
import argparse
import gzip
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUDGET_GZIP_KB = {'initial_total': 110, 'index.html': 80}


def size(path):
    b = (ROOT / path).read_bytes()
    return len(b), len(gzip.compress(b, 9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--budget', action='store_true')
    args = ap.parse_args()
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    initial = ['index.html']
    initial += [m.split('?')[0] for m in re.findall(r'(?:href|src)="(site/[^"]+\.(?:css|js))', html)]
    initial += ['site/catalog.mjs']  # static import of site.js
    initial += sorted(set(re.findall(r'src="(assets/[^"]+)"', html)))
    initial += sorted(set(re.findall(r'href="(assets/favicon\.svg)"', html)))
    lazy = ['site/lab.js', 'site/field.js', 'site/data/drawer.json'] + sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'site' / 'data' / 'fragments').glob('*.html'))
    rows = {p: size(p) for p in dict.fromkeys(initial + lazy)}
    tot_raw = sum(rows[p][0] for p in dict.fromkeys(initial))
    tot_gz = sum(rows[p][1] for p in dict.fromkeys(initial))
    js = (ROOT / 'site' / 'site.js').read_text(encoding='utf-8')
    forbidden = re.findall(r'(?:src|data-src)="([^"]*(?:\.pdf|repository_candidates\.csv))"', html)
    forbidden += re.findall(r'<link[^>]+rel="preload"[^>]+href="([^"]+\.(?:pdf|csv|json))"', html)
    forbidden += re.findall(r"fetch\(['\"]([^'\"]*(?:papers\.json|claims\.json|repository_candidates[^'\"]*))", js)
    report = dict(
        method='Static byte count of files requested on first load (raw and gzip -9). Not a Lighthouse or field measurement.',
        initial={p: dict(raw_kb=round(rows[p][0] / 1024, 1), gzip_kb=round(rows[p][1] / 1024, 1)) for p in dict.fromkeys(initial)},
        initial_total=dict(raw_kb=round(tot_raw / 1024, 1), gzip_kb=round(tot_gz / 1024, 1)),
        lazy={p: dict(raw_kb=round(rows[p][0] / 1024, 1), gzip_kb=round(rows[p][1] / 1024, 1)) for p in lazy},
        external=['Google Fonts CSS + WOFF2 for Newsreader, Public Sans, IBM Plex Mono (display=swap)'],
        forbidden_on_first_load=forbidden, budget_gzip_kb=BUDGET_GZIP_KB)
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'reports' / 'performance.json').write_text(json.dumps(report, indent=2) + '\n')
    for p, v in report['initial'].items():
        print(f'  first load  {p:28s} {v["raw_kb"]:7.1f} KB raw  {v["gzip_kb"]:6.1f} KB gzip')
    for p, v in report['lazy'].items():
        print(f'  lazy        {p:28s} {v["raw_kb"]:7.1f} KB raw  {v["gzip_kb"]:6.1f} KB gzip')
    print(f'  first-load total: {report["initial_total"]["raw_kb"]} KB raw, {report["initial_total"]["gzip_kb"]} KB gzip (+ web fonts)')
    problems = []
    if forbidden:
        problems.append(f'first load references {forbidden}')
    if report['initial_total']['gzip_kb'] > BUDGET_GZIP_KB['initial_total']:
        problems.append('initial total over budget')
    if report['initial']['index.html']['gzip_kb'] > BUDGET_GZIP_KB['index.html']:
        problems.append('index.html over budget')
    for p in problems:
        print('BUDGET', p)
    if args.budget and problems:
        sys.exit(1)


if __name__ == '__main__':
    main()
