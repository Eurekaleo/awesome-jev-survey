#!/usr/bin/env python3
"""Build every generated artefact from data/*.json.

    python3 scripts/build.py            # validate, export, render site + README
    python3 scripts/build.py --check    # also fail if generated files would change (CI)

Outputs
  index.html                      static, pre-rendered site (GitHub Pages serves the repo root)
  site/data/drawer.json           per-record evidence for the drawer and client exports (lazy-loaded)
  data/references.bib             bibliography of all records
  data/exports/*.csv              papers, claims, repositories (formula-injection safe)
  paper/references.bib            manuscript bibliography (records + official sources + repositories)
  README.md                       via scripts/render-readme.py
"""
import argparse
import csv
import hashlib
import io
import json
import pathlib
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jevlib as J  # noqa: E402
from jevlib import esc  # noqa: E402

ROOT = J.ROOT
REL_PRIMARY_LABEL = {'commercial_jev': 'Evaluates hosted Jev', 'independent_jev_like': 'Builds an independent model',
                     'downstream_system': 'Uses Jev inside a system'}
STAGE_FINDINGS = {'contract': ['F1', 'F10'], 'readout': ['F5', 'F9'], 'probability': ['F2', 'F8'], 'control': ['F3'], 'use': ['F4', 'F6', 'F7']}
STAGE_SHORT = {'contract': 'What is asked?', 'readout': 'How is a distribution made?', 'probability': 'What does it mean?',
               'control': 'Act, ask or escalate?', 'use': 'Does the workflow improve?'}
CHAPTERS = [('title', 'Title'), ('object', 'The object'), ('map', 'The map'), ('findings', 'Findings'), ('failures', 'Failure modes'),
            ('applications', 'Applications'), ('methods', 'Methods'), ('ecosystem', 'Ecosystem'), ('literature', 'Literature'), ('about', 'About')]
# The highlighted number on each finding card: (evidence record, figure, what it measures). Values must match the record.
FINDING_STATS = {
    'F1': ('c-39496-rejection', '99% → 7%', 'right when the answer is on the menu; correct rejections when it is missing, an explicit “None” notwithstanding'),
    'F2': ('c-01006-boundary', '+0.21–0.33', 'confidence above accuracy on news past the model’s knowledge boundary, even after recalibration'),
    'F3': ('c-26550-cascade', '41%', 'of GPT-6’s fee for a frozen accept-or-escalate cascade that beat GPT-6 by 0.9 points'),
    'F4': ('c-06625-cost', '1.2–3.3×', 'the input tokens billed per decision against a batch-priced LLM, leaving no cost advantage'),
    'F5': ('c-02267-points', '9 of 11', 'agent-harness decision points where hosted Jev beat an open Jev-like model on identical inputs'),
    'F6': ('c-01834-lookahead', '31% → 87%', 'ALFWorld games solved once code supplies the lookahead the model cannot simulate'),
    'F7': ('c-02267-audit', '23.9% → 4.3%', 'a reported saving after the authors audited their own analysis pipeline'),
    'F8': ('c-03935-mode', '89% vs 36%', 'probability placed on the most likely option, against its true chance'),
    'F9': ('c-03935-exact', '97% vs 44%', 'answers within one of an exact number, against answers exactly right'),
    'F10': ('c-04985-injection', '100%', 'success of injections that slip false facts into the state, on one decision task'),
}
# Evidence shown first under each finding (the rest sits under "More supporting evidence").
KEY_EVIDENCE = {
    'F1': ['c-26758-hosted-swap', 'c-02586-labels', 'c-38827-neutral'], 'F2': ['c-24574-ece', 'c-37647-threshold', 'c-00346-oos'],
    'F3': ['c-02048-cascade', 'c-26550-live', 'c-24574-cascade'], 'F4': ['c-22753-cache', 'c-00437-variant', 'c-02267-audit'],
    'F5': ['c-02076-frozen', 'c-02267-drift', 'c-36116-jevbench'], 'F6': ['c-03935-tau', 'c-02046-jevdb', 'c-36059-memory'],
    'F7': ['c-02267-drift', 'c-31142-rerun', 'c-02293-board'], 'F8': ['c-33209-negation', 'c-37470-interfaces', 'c-01006-boundary'],
    'F9': ['c-39496-rejection', 'c-01834-simulation', 'c-06354-graph'], 'F10': ['c-28613-injection', 'c-30243-flips', 'c-04985-backdoor'],
}
MECHANISM = {
    'hosted_service': ['Text state + typed questions', 'Undisclosed hosted model', 'Undisclosed; distribution over the declared options', 'Answer + probabilities + confidence'],
    'encoder_head': ['State ⊕ question ⊕ options', 'Bidirectional encoder or encoder–decoder', 'Per-option score → softmax over the legal set', 'Distribution over options'],
    'frozen_decoder_readout': ['Prompt listing options with short identifiers', 'Frozen causal LM', 'Next-token logits of the identifiers, renormalised; optional debiasing', 'Distribution over options'],
    'finetuned_decoder': ['Prompt with declared options', 'Decoder + LoRA or full fine-tune', 'Restricted softmax at a designated position or pointer head', 'Distribution (+ fitted temperature)'],
    'diffusion_readout': ['Canvas seeded with the answer template', 'Discrete diffusion LM', 'One denoising step; read each answer slot', 'Distribution + project-defined confidence'],
    'generative_adapter': ['Prompt + JSON schema', 'General LLM API', 'Generated (verbalised) probabilities, validated and retried', 'Jev-shaped response'],
}
MECH_LABELS = ['Input', 'Backbone', 'Readout', 'Output']
AVAIL = {  # glyph, class, label
    'available': ('●', 'av-yes', 'Available'), 'partial': ('◐', 'av-part', 'Partial'), 'restricted': ('◑', 'av-restricted', 'Restricted'),
    'project_page_only': ('◔', 'av-part', 'Project page only'), 'claimed_not_located': ('○', 'av-claimed', 'Claimed, not located'),
    'not_located': ('○', 'av-no', 'Not located'), 'not_applicable': ('–', 'av-na', 'Not applicable'), 'not_assessed': ('·', 'av-na', 'Not assessed'),
    'not_attempted': ('·', 'av-na', 'Not attempted'), 'no': ('○', 'av-no', 'Not recomputable from located material'),
}
PRIMITIVES = [
    dict(name='Choice', returns='The chosen option, a probability for every option and a confidence value.',
         semantics='A distribution over named options, each defined by a description. The launch post states support for up to 255 options.',
         watch='The model reads option names <em>and</em> descriptions; a short label can outweigh its definition, and the vendor notes a lean toward the first option.', finding='F1',
         url='https://docs.typesafe.ai/primitives/choice'),
    dict(name='Score', returns='A position on ordered levels (it may fall between levels), probabilities per level and confidence.',
         semantics='Two to ten described levels. Ordinal, not a regression: the vendor warns against interpolating exact magnitudes.',
         watch='Middle levels can absorb errors, and decisions can use only part of the scale.', finding='F1',
         url='https://docs.typesafe.ai/primitives/score'),
    dict(name='Noul', returns='The probability that the answer is yes. No separate confidence field.',
         semantics='A proposition probability, optionally with descriptions of what counts as true and false.',
         watch='A yes/no question and a two-option Choice over the same proposition need not agree; a statement and its negation need not sum to one.', finding='F8',
         url='https://docs.typesafe.ai/primitives/noul'),
]
ECO_ORDER = ['open_model', 'benchmark', 'built_with', 'tooling']
NEWS = [
    ('2026-10-07', 'Version 0.3.0: studies posted through early October; findings on probability coherence, computation and security; an audited open ecosystem of models, benchmarks and applications; a redesigned site.'),
    ('2026-09-24', 'Version 0.2.0: four new core studies from the 24 September arXiv listing, new application areas and a new failure mode.'),
    ('2026-09-23', 'Version 0.1.0: first public release of the survey, its data and this site.'),
]


class Ctx:
    def __init__(self, d):
        self.d = d
        self.papers = d['papers']['papers']
        self.P = J.by_id(self.papers)
        self.claims = d['claims']['claims']
        self.C = J.by_id(self.claims)
        self.repos = d['repositories']['repositories']
        self.R = J.by_id(self.repos)
        self.sources = J.by_id(d['sources']['sources'])
        self.tax = d['taxonomy']
        self.T = {k: J.by_id(self.tax[k]) for k in ('stages', 'method_families', 'topics', 'applications', 'model_relationships',
                                                   'test_levels', 'measurement_scopes', 'evidence_types', 'findings', 'background_groups',
                                                   'repository_types', 'ecosystem_roles')}
        self.stats = J.compute_stats(d)
        self.cfg = d['config']
        self.primary = [p for p in self.papers if p['tier'] in ('core', 'peripheral')]
        self.fragments = {}

    def subject_label(self, sid):
        if sid in self.P:
            return J.label(self.P[sid])
        if sid in self.R:
            return self.R[sid]['full_name']
        if sid in self.sources:
            return 'TypeSafe: ' + self.sources[sid]['title']
        return sid

    def subject_link(self, sid):
        if sid in self.P:
            return f'<a class="src" href="#paper-{esc(self.P[sid]["arxiv_id"])}" data-open-paper="{esc(sid)}">{esc(J.label(self.P[sid]))}</a>'
        url = self.R[sid]['url'] if sid in self.R else self.sources[sid]['url'] if sid in self.sources else None
        return f'<a class="src" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(self.subject_label(sid))} ↗</a>'


def chip(x, fid):
    f = x.T['findings'][fid]
    return f'<a class="finding-chip" href="#finding-{f["id"]}">{f["id"]} · {esc(f["short"])}</a>'


# --------------------------------------------------------------------------------------------- frame
def render_pager(x):
    return ''.join(f'<a href="#{cid}" data-chapter="{cid}"><span class="pg-dot" aria-hidden="true"></span><span class="pg-label">{esc(lab)}</span></a>'
                   for cid, lab in CHAPTERS)


def render_contents(x):
    return '<ol>' + ''.join(f'<li><a href="#{cid}" data-chapter="{cid}"><span>{i:02d}</span>{esc(lab)}</a></li>'
                            for i, (cid, lab) in enumerate(CHAPTERS, 1)) + '</ol>'


def render_hero_diagram(x):
    bars = [('returns', 0.81, True), ('billing', 0.15, False), ('other', 0.04, False)]
    levels = [('calm', 0.12), ('upset', 0.70), ('furious', 0.18)]
    b = ''.join(f'<li class="{"is-top" if top else ""}" style="--p:{p}"><span>{lab}</span><b>{p:.2f}</b></li>' for lab, p, top in bars)
    lv = ''.join(f'<li class="{"is-top" if p == max(v for _, v in levels) else ""}" style="--p:{p}"><b>{p:.2f}</b><span>{lab}</span></li>' for lab, p in levels)
    return f'''<figure class="hero-diagram card" aria-labelledby="hd-cap">
<div class="hd-state"><span class="hd-tag">state</span><p>“Shoes arrived in the wrong size. Can someone call me?”</p></div>
<div class="hd-arrow" aria-hidden="true"><svg class="icon"><use href="#i-arrow"/></svg></div>
<div class="hd-answers">
<div class="hd-q"><p><code>choice</code> team</p><ul class="hd-bars">{b}</ul></div>
<div class="hd-q"><p><code>score</code> anger</p><ul class="hd-levels">{lv}</ul></div>
<div class="hd-q"><p><code>noul</code> wants a person</p><div class="hd-gauge" style="--p:.92"><b>0.92</b></div></div>
</div>
<figcaption id="hd-cap">Illustrative values: one state, three typed questions, a distribution for each — no generated text.</figcaption></figure>'''


def render_pipeline(x):
    out = []
    for s in x.tax['stages']:
        chips = ''.join(f'<a href="#finding-{f}">{f}</a>' for f in STAGE_FINDINGS[s['id']])
        out.append(f'<li><a class="pl-step" href="#stage-{s["id"]}"><svg class="icon pl-icon"><use href="#st-{s["id"]}"/></svg>'
                   f'<span class="pl-n">{s["n"]}</span><b>{esc(s["label"])}</b><span>{esc(STAGE_SHORT[s["id"]])}</span></a><span class="pl-f">{chips}</span></li>')
    return ''.join(out)


# --------------------------------------------------------------------------------------------- object & map
def render_primitives(x):
    out = []
    for p in PRIMITIVES:
        out.append(f'''<article class="primitive card"><div class="prim-head"><code>{p["name"].lower()}</code><h3>{p["name"]}</h3></div>
<dl><div><dt>Returns</dt><dd>{esc(p["returns"])}</dd></div><div><dt>Semantics</dt><dd>{esc(p["semantics"])}</dd></div>
<div><dt>Watch</dt><dd>{p["watch"]} {chip(x, p["finding"])}</dd></div></dl>
<a class="doc-link" href="{esc(p["url"])}" target="_blank" rel="noopener noreferrer">Documentation ↗</a></article>''')
    return '\n'.join(out)


def render_vendor_claims(x):
    out = []
    for cid in ('v-price-latency', 'v-workflow-evals', 'v-workflow-dashboard', 'v-types'):
        c = x.C[cid]
        out.append(f'<li><strong>{esc(c["headline"])}</strong><span>{esc(c["limitations"][0] if c["limitations"] else "")}</span>'
                   f'<a href="{esc(c["source_url"])}" target="_blank" rel="noopener noreferrer">{esc(x.sources[c["subject"]]["title"])} · {esc(c["locator"])} ↗</a></li>')
    return '\n'.join(out)


def render_stages(x):
    out = []
    for s in x.tax['stages']:
        chips = ''.join(chip(x, f) for f in STAGE_FINDINGS[s['id']])
        out.append(f'''<li class="stage card" id="stage-{s["id"]}"><div class="stage-top"><svg class="icon"><use href="#st-{s["id"]}"/></svg><span class="stage-n">{s["n"]}</span></div>
<h3>{esc(s["label"])}</h3><p class="stage-q">{esc(s["question"])}</p><p class="stage-covers">{esc(s["covers"])}</p>
<p class="stage-evidence"><span>Evidence to look for</span>{esc(s["evidence"])}</p><div class="stage-chips">{chips}</div>
<a class="more-link" href="#literature" data-filter-stage="{s["id"]}">Studies at this stage <svg class="icon"><use href="#i-arrow"/></svg></a></li>''')
    return '\n'.join(out)


def render_timeline(x):
    import datetime as dt
    last = max(p['published_at'][:10] for p in x.primary)
    first = dt.date(2026, 9, 15)  # launch
    days = [(first + dt.timedelta(days=i)).isoformat() for i in range((dt.date.fromisoformat(last) - first).days + 1)]
    by_day = defaultdict(list)
    for p in sorted(x.primary, key=lambda p: (p['published_at'], J.label(p))):
        by_day[p['published_at'][:10]].append(p)
    cols, rows = [], []
    for day in days:
        d = dt.date.fromisoformat(day)
        dots = []
        for p in by_day.get(day, []):
            rel = p['model_relationship'][0]
            peri = ' tl-peripheral' if p['tier'] == 'peripheral' else ''
            dots.append(f'<li><button type="button" class="tl-dot rel-{rel}{peri}" data-open-paper="{esc(p["id"])}" title="{esc(J.label(p))}">'
                        f'<span class="sr-only">{esc(J.label(p))}</span></button></li>')
            rows.append(f'<tr><td>{esc(J.fmt_date(day))}</td><td>{esc(p["title"])}</td><td>{esc(REL_PRIMARY_LABEL.get(rel, rel))}</td></tr>')
        launch = ' tl-launch' if day == '2026-09-15' else ''
        lab = f'<span class="tl-d">{d.day}</span>' + (f'<span class="tl-m">{d.strftime("%b")}</span>' if d.day in (15, 1) or day == days[0] else '')
        cols.append(f'<div class="tl-col{launch}"><ul>{"".join(dots)}</ul><div class="tl-date">{lab}</div></div>')
    legend = ''.join(f'<span class="lg rel-{k}"><i aria-hidden="true"></i>{esc(v)}</span>' for k, v in REL_PRIMARY_LABEL.items())
    legend += '<span class="lg lg-launch"><i aria-hidden="true"></i>15 Sep: Jev released</span>'
    table = ('<div class="table-scroll" data-table="timeline" hidden><table class="viz-table"><caption class="sr-only">First-submission dates of the core studies</caption>'
             '<thead><tr><th scope="col">Date (arXiv v1)</th><th scope="col">Title</th><th scope="col">Primary relationship</th></tr></thead><tbody>'
             + ''.join(rows) + '</tbody></table></div>')
    return f'<div class="tl-legend">{legend}</div><div class="table-scroll tl-scroll" data-chart="timeline"><div class="timeline">{"".join(cols)}</div></div>{table}'


# --------------------------------------------------------------------------------------------- findings
def ev_item(x, c, relation):
    et = x.T['evidence_types'][c['evidence_type']]['label']
    sid = c['subject']
    if sid in x.P:
        src = f'<button type="button" class="ev-src" data-open-paper="{esc(sid)}">{esc(J.label(x.P[sid]))}</button>'
    else:
        url = x.R[sid]['url'] if sid in x.R else x.sources[sid]['url']
        src = f'<a class="ev-src" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(x.subject_label(sid))} ↗</a>'
    return f'<li class="ev ev-{relation}"><span class="ev-h">{esc(c["headline"])}</span><span class="ev-meta">{src}<span class="et et-{c["evidence_type"]}">{esc(et)}</span></span></li>'


def render_findings(x):
    order = {'author_reported_experiment': 0, 'community_report': 1, 'vendor_documentation': 2, 'vendor_reported_result': 3, 'code_inspection': 4}
    stage_of = {f: s for s, fs in STAGE_FINDINGS.items() for f in fs}
    out = []
    for f in x.tax['findings']:
        sup = [c for c in x.claims if any(l['id'] == f['id'] and l['relation'] == 'supports' for l in c['findings'])]
        qual = [c for c in x.claims if any(l['id'] == f['id'] and l['relation'] in ('qualifies', 'challenges') for l in c['findings'])]
        cid, big, what = FINDING_STATS[f['id']]
        sc = x.C[cid]
        assert any(l['id'] == f['id'] for l in sc['findings']), (f['id'], cid)
        key = KEY_EVIDENCE[f['id']]
        assert all(any(c['id'] == k for c in sup) for k in key), (f['id'], key)
        first = [x.C[k] for k in key]
        rest = sorted([c for c in sup if c['id'] not in key and c['id'] != cid], key=lambda c: (order[c['evidence_type']], -int(re.sub(r'\D', '', c['subject'])[:9] or 0)))
        more = f'<details class="more"><summary>More supporting evidence</summary><ul class="ev-list">{"".join(ev_item(x, c, "sup") for c in rest)}</ul></details>' if rest else ''
        q = f'<p class="ev-label ev-label-q">Limited or qualified by</p><ul class="ev-list">{"".join(ev_item(x, c, "qual") for c in qual)}</ul>' if qual else ''
        stage = x.T['stages'][stage_of[f['id']]]['label']
        out.append(f'''<article class="finding card" id="finding-{f["id"]}">
<p class="kicker"><span>{f["id"]}</span> {esc(f["short"])} <em>· {esc(stage)}</em></p>
<h3>{esc(f["title"])}</h3>
<div class="stat"><p class="big">{esc(big)}</p><p class="stat-what">{esc(what)}</p><p class="stat-src">{x.subject_link(sc["subject"])}</p></div>
<p class="finding-body">{esc(f["body"])}</p>
<p class="ev-label">Supported by</p><ul class="ev-list">{"".join(ev_item(x, c, "sup") for c in first)}</ul>{more}{q}</article>''')
    return '\n'.join(out)


def render_matrix(x):
    fids = [f['id'] for f in x.tax['findings']]
    head = ''.join(f'<th scope="col"><button type="button" data-heat-sort="{fid}" aria-pressed="false" title="{esc(x.T["findings"][fid]["title"])}">'
                   f'<b>{fid}</b><span>{esc(x.T["findings"][fid]["short"])}</span></button></th>' for fid in fids)
    rows = []
    for p in sorted(x.primary, key=lambda p: (p['published_at'], J.label(p))):
        cells, data = [], []
        for fid in fids:
            rels = {l['relation'] for cid in p['claims'] for l in x.C[cid]['findings'] if l['id'] == fid}
            v = 2 if 'supports' in rels else 1 if rels else 0
            data.append(f'data-{fid.lower()}="{v}"')
            cells.append({2: '<td class="h-sup"><span class="sr-only">supports</span></td>', 1: '<td class="h-qual"><span class="sr-only">qualifies</span></td>', 0: '<td></td>'}[v])
        fam = '<span class="fam-tag" title="Shares authors with another study here">family</span>' if p.get('study_family_id') else ''
        rows.append(f'<tr data-date="{esc(p["published_at"][:10])}" {" ".join(data)}><th scope="row"><button type="button" data-open-paper="{esc(p["id"])}">{esc(J.label(p))}</button>{fam}'
                    f'<span>{esc(J.fmt_date(p["published_at"]))}</span></th>{"".join(cells)}</tr>')
    return (f'<table class="heat"><thead><tr><th scope="col"><button type="button" data-heat-sort="date" aria-pressed="true"><b>Study</b><span>by date</span></button></th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def render_scope_groups(x):
    order = ['single_request', 'amortized_question', 'batch', 'end_to_end', 'simulation', 'author_estimate', 'vendor_claim']
    groups = defaultdict(list)
    for c in x.claims:
        if c['measurement_scope'] in order:
            groups[c['measurement_scope']].append(c)
    out = []
    for s in order:
        if not groups[s]:
            continue
        m = x.T['measurement_scopes'][s]
        items = ''.join(f'<li><strong>{esc(c["headline"])}</strong><span>{x.subject_link(c["subject"])}</span></li>' for c in groups[s])
        out.append(f'<article class="scope scope-{s}"><h4>{esc(m["label"])}</h4><p>{esc(m["desc"])}</p><ul>{items}</ul></article>')
    return '\n'.join(out)


# --------------------------------------------------------------------------------------------- failures
def dumbbell_svg(x):
    rows = [('Hosted Jev (TypeSafe)', x.C['c-26758-hosted-swap']['value']), ('Open marker-readout head', x.C['c-26758-open-inversion']['value'])]
    w, left, right, top, rowh = 520, 190, 24, 30, 54
    xs = lambda v: left + v * (w - left - right)
    ticks = ''.join(f'<line x1="{xs(t):.1f}" x2="{xs(t):.1f}" y1="{top - 10}" y2="{top + rowh * len(rows) - 14}" class="grid"/>'
                    f'<text x="{xs(t):.1f}" y="{top + rowh * len(rows) + 2}" class="tick" text-anchor="middle">{t:.1f}</text>' for t in (0, 0.5, 1.0))
    chance = f'<text x="{xs(0.5):.1f}" y="{top - 16}" class="tick" text-anchor="middle">chance</text>'
    marks = []
    for i, (lab, v) in enumerate(rows):
        y = top + i * rowh + 10
        a, b = v['aligned'], v['swapped']
        marks.append(f'<text x="0" y="{y + 4}" class="row-label">{esc(lab)}</text>'
                     f'<line x1="{xs(a):.1f}" x2="{xs(b):.1f}" y1="{y}" y2="{y}" class="db-line"/>'
                     f'<circle cx="{xs(a):.1f}" cy="{y}" r="6" class="db-a"><title>{esc(lab)}: aligned AUROC {a}</title></circle>'
                     f'<circle cx="{xs(b):.1f}" cy="{y}" r="6" class="db-b"><title>{esc(lab)}: swapped AUROC {b}</title></circle>'
                     f'<text x="{xs(a):.1f}" y="{y - 12}" class="db-val" text-anchor="middle">{a:.4f}</text>'
                     f'<text x="{xs(b):.1f}" y="{y + 22}" class="db-val" text-anchor="middle">{b:.4f}</text>')
    h = top + rowh * len(rows) + 10
    return (f'<svg class="dumbbell" viewBox="0 0 {w} {h}" role="img" aria-labelledby="db-title db-desc"><title id="db-title">AUROC before and after the name–rubric swap</title>'
            f'<desc id="db-desc">Hosted Jev falls from 0.8146 to 0.5806; an open marker-readout head falls from 0.9376 to 0.2315. n = 1,200 items (2609.26758, §4.6 and §4.2).</desc>'
            f'{ticks}{chance}{"".join(marks)}</svg>')


def render_lab(x):
    C = x.C
    cite = lambda cid: f'<a class="cite" href="{esc(C[cid]["source_url"])}" target="_blank" rel="noopener noreferrer">{esc(x.subject_label(C[cid]["subject"]))} · {esc(C[cid]["locator"])} ↗</a>'
    reported_cal = [('c-37647-broad', '37 datasets, pooled Choice answers'), ('c-24574-ece', 'Social-science annotation, 15 tasks'), ('c-24574-empathy', 'Social-science annotation, empathy task'),
                    ('c-01006-boundary', 'News past the knowledge boundary'), ('r-chaosnli', 'Community audit, human disagreement'), ('c-24052-recalibration', 'Crash narratives'),
                    ('r-assay-001', 'Community audit (pre-registered)'), ('r-acento', 'Community audit, Spanish'), ('r-ood', 'Community audit, unknowable rule')]
    cal_rows = ''.join(f'<tr><th scope="row">{esc(lab)}</th><td>{esc(C[c]["headline"])}</td><td>{cite(c)}</td></tr>' for c, lab in reported_cal)
    return f'''
<div class="lab-panel card" role="tabpanel" id="lab-binding" aria-labelledby="lab-tab-binding" data-lab-panel="binding">
  <div class="lab-explainer"><p class="tag tag-illus">Illustrative</p><h3>Keep the rubric. Move the name.</h3>
    <p>Each option is a name plus a rubric that defines it. The study behind this panel changes only which name sits in front of which rubric; question, state, rubric wording and the set of names stay byte-identical.</p>
    <div class="binding-demo" data-binding-demo>
      <div class="bd-options" aria-live="polite"><div class="bd-opt" data-slot="0"><span class="bd-name">no</span><span class="bd-rubric">The request does <b>not</b> meet the refund policy.</span></div>
      <div class="bd-opt" data-slot="1"><span class="bd-name">yes</span><span class="bd-rubric">The request <b>meets</b> the refund policy.</span></div></div>
      <div class="bd-controls" role="group" aria-label="Intervention">
        <button type="button" aria-pressed="true" data-bd="aligned">As shipped</button><button type="button" aria-pressed="false" data-bd="order">Swap order</button>
        <button type="button" aria-pressed="false" data-bd="rebind">Rebind names</button><button type="button" aria-pressed="false" data-bd="neutral">Neutral names</button>
        <button type="button" aria-pressed="false" data-bd="random">Random strings</button></div>
      <p class="bd-explain" data-bd-explain>Names and rubrics as the task ships them.</p>
    </div></div>
  <div class="lab-reported"><p class="tag tag-reported">Reported</p><h3>What the swap did (n = 1,200)</h3>
    <div class="lg-dots"><span><i style="--c:var(--s1)"></i>AUROC as shipped</span><span><i style="--c:var(--s2)"></i>AUROC after name–rubric swap</span></div>{dumbbell_svg(x)}
    <ul class="facts"><li><b>32.50%</b> of hosted answers flipped against about 2% under neutral names: <b>24×</b> the test–retest floor.</li>
    <li>Type-error rate: <b>0%</b> in every arm — by construction.</li><li>In open models, the short label beat the written definition, and deleting every definition changed nothing.</li>
    <li>Order is a different test: one community audit saw no flips on hosted Jev, while four rotations changed 37% of answers in another study.</li></ul>
    <p class="cites">{cite("c-26758-hosted-swap")}{cite("c-26758-open-inversion")}{cite("c-02586-labels")}{cite("c-30454-order")}</p></div>
</div>
<div class="lab-panel card" role="tabpanel" id="lab-confidence" aria-labelledby="lab-tab-confidence" data-lab-panel="confidence" hidden>
  <div class="lab-explainer"><p class="tag tag-illus">Illustrative</p><h3>One distribution, several summaries.</h3>
    <p>Choice answers return a probability per option; “confidence” compresses that shape into one number. Move the sliders: different summaries disagree about how sure the same distribution is.</p>
    <div class="conf-demo" data-conf-demo><noscript><p class="fine">Interactive sliders need JavaScript.</p></noscript></div></div>
  <div class="lab-reported"><p class="tag tag-reported">Reported</p><h3>What is known about Jev’s <code>confidence</code></h3>
    <ul class="facts"><li>The vendor documents it as a statistic of the returned distribution; yes/no answers have none.</li>
    <li>Its exact formula is not published. In one judging study, native confidence detected errors about as well as the largest label probability (AUROC 0.745–0.876).</li>
    <li>It is not a signal of missing knowledge: with no answer-relevant information the model still put up to 0.80 on a salient option.</li>
    <li>Where 100 annotators disagree, Choice probabilities stayed high (mean 0.81 against 0.47 agreement).</li></ul>
    <p class="cites">{cite("v-confidence")}{cite("c-26550-confidence")}{cite("c-01006-boundary")}{cite("r-chaosnli")}</p></div>
</div>
<div class="lab-panel card" role="tabpanel" id="lab-calibration" aria-labelledby="lab-tab-calibration" data-lab-panel="calibration" hidden>
  <div class="lab-explainer"><p class="tag tag-illus">Illustrative</p><h3>Reliability is a population property.</h3>
    <p>Bin predictions by confidence and compare with how often they were right. The curves are hand-made to show three regimes; they are not Jev measurements.</p>
    <div class="cal-demo" data-cal-demo><noscript><p class="fine">The interactive reliability diagram needs JavaScript.</p></noscript></div></div>
  <div class="lab-reported"><p class="tag tag-reported">Reported</p><h3>Measured calibration varies by task</h3>
    <div class="table-scroll" tabindex="0" role="region" aria-label="Reported calibration results"><table class="facts-table"><thead><tr><th scope="col">Setting</th><th scope="col">Reported</th><th scope="col">Source</th></tr></thead><tbody>{cal_rows}</tbody></table></div>
    <p class="fine">Different datasets, bins and metrics: read each row on its own; do not average across rows.</p></div>
</div>
<div class="lab-panel card" role="tabpanel" id="lab-closed" aria-labelledby="lab-tab-closed" data-lab-panel="closed" hidden>
  <div class="lab-explainer"><p class="tag tag-illus">Illustrative</p><h3>A typed answer must pick something.</h3>
    <p>If the right answer is not among the options, the distribution still sums to one. Toggle the abstain option to see where the probability mass goes.</p>
    <div class="closed-demo" data-closed-demo><noscript><p class="fine">The interactive example needs JavaScript.</p></noscript></div></div>
  <div class="lab-reported"><p class="tag tag-reported">Reported</p><h3>Removing, and ignoring, the exit</h3>
    <ul class="facts"><li>With an explicit “None” option, Jev rejected only 7% of arithmetic menus that lacked the right value, although yes/no checks of the same candidates were right 99% of the time.</li>
    <li>When a reference label was removed from the options, Jev noticed 24.8% of the time; an open encoder noticed far more often but also rejected most valid menus.</li>
    <li>A community audit removed the “unknown” option from an unanswerable benchmark: accuracy on those items fell from 0.950 to 0.000 at 0.79 confidence.</li></ul>
    <p class="cites">{cite("c-39496-rejection")}{cite("c-03387-coverage")}{cite("r-calibration-audit")}</p></div>
</div>'''


def render_fm_filters(x):
    present = [s for s in x.tax['stages'] if any(fm['stage'] == s['id'] for fm in x.tax['failure_modes'])]
    return '<button type="button" aria-pressed="true" data-fm-filter="">All stages</button>' + ''.join(
        f'<button type="button" aria-pressed="false" data-fm-filter="{s["id"]}">{esc(s["label"])}</button>' for s in present)


def render_failure_modes(x):
    out = []
    for fm in x.tax['failure_modes']:
        srcs = []
        for cid in fm['claims']:
            c = x.C[cid]
            et = x.T['evidence_types'][c['evidence_type']]['label']
            srcs.append(f'<li>{x.subject_link(c["subject"])}<span class="et et-{c["evidence_type"]}">{esc(et)}</span></li>')
        for pid in fm.get('background', []):
            srcs.append(f'<li>{x.subject_link(pid)}<span class="et et-background">Background</span></li>')
        out.append(f'''<article class="fm card" data-stage="{esc(fm["stage"])}"><p class="kicker">{esc(x.T["stages"][fm["stage"]]["label"])}</p><h3>{esc(fm["title"])}</h3><p>{esc(fm["desc"])}</p>
<p class="fm-mit"><b>Mitigation</b> {esc(fm["mitigation"])}</p><ul class="fm-src">{"".join(srcs)}</ul></article>''')
    return '\n'.join(out)


# --------------------------------------------------------------------------------------------- applications
def render_control_explorer(x):
    return '''<div class="control-explorer card viz-root" data-control-explorer>
  <div class="ce-copy"><p class="tag tag-illus">Illustrative · synthetic scores</p><h3>How a threshold spends the escalation budget</h3>
  <p>A cheap typed model answers every item and reports a confidence; items below the threshold τ go to a strong model assumed correct 95% of the time and 50× more expensive. The data are simulated to show the trade-off, not measured.</p>
  <div class="ce-controls" data-ce-controls></div></div>
  <figure class="ce-chart" data-ce-chart><noscript><p class="fine">The interactive explorer needs JavaScript. In short: raising τ lowers the error rate among accepted items but sends more items — and cost — to the fallback, and the benefit depends on whether confidence actually ranks errors low.</p></noscript></figure>
</div>'''


def render_applications(x):
    out = []
    for card in x.tax['application_cards']:
        app = x.T['applications'][card['id']]
        ps = [x.P[pid] for pid in card['papers']]
        levels = sorted({x.T['test_levels'][p['test_level']]['label'] for p in ps})
        fam = ' · includes a study family' if any(p.get('study_family_id') for p in ps) else ''
        claims = ''.join(f'<li>{esc(x.C[c]["headline"])}</li>' for c in card['claims'])
        studies = ''.join(f'<button type="button" class="paper-chip" data-open-paper="{esc(p["id"])}">{esc(J.label(p))}</button>' for p in ps)
        out.append(f'''<article class="app card"><p class="kicker">{esc(" · ".join(levels))}{fam}</p><h3>{esc(app["label"])}</h3>
<p class="app-decides"><span>The typed model decides</span>{esc(card["decides"])}</p><ul class="app-claims">{claims}</ul>
<p class="app-caution"><b>Caution</b> {esc(card["caution"])}</p><div class="app-studies">{studies}</div></article>''')
    return '\n'.join(out)


# --------------------------------------------------------------------------------------------- methods
def avail_cell(status, note=None):
    g, cls, lab = AVAIL.get(status, ('?', 'av-na', status))
    title = esc(lab + (f': {note}' if note else ''))
    return f'<span class="av {cls}" role="img" aria-label="{title}" title="{title}">{g}</span>'


def render_atlas(x):
    fams = x.tax['method_families']
    tabs, panels = [], []
    for i, f in enumerate(fams):
        sel = i == 0
        tabindex = '' if sel else ' tabindex="-1"'
        tabs.append(f'<button role="tab" id="atlas-tab-{f["id"]}" aria-selected="{str(sel).lower()}" aria-controls="atlas-{f["id"]}"'
                    f'{tabindex} data-atlas="{f["id"]}"><span>{f["n"]}</span>{esc(f["label"])}</button>')
        mech = ''.join(f'<li class="{"mech-key" if j == 2 else ""}"><span>{MECH_LABELS[j]}</span>{esc(t)}</li>' for j, t in enumerate(MECHANISM[f['id']]))
        has_models = any(r.get('method_family') == f['id'] and r['ecosystem'] == 'open_model' for r in x.repos)
        papers = [p for p in x.primary if f['id'] in p['method_families']]
        plinks = ''.join(f'<button type="button" class="paper-chip" data-open-paper="{esc(p["id"])}">{esc(J.label(p))}</button>' for p in papers) or '<span class="muted">No core study uses this family as its main object.</span>'
        reps = ''.join(f'<li>{esc(r)}</li>' for r in f['representatives'])
        panels.append(f'''<article class="atlas-panel card{' is-active' if sel else ''}" role="tabpanel" id="atlas-{f["id"]}" aria-labelledby="atlas-tab-{f["id"]}" data-panel="{f["id"]}">
<div class="atlas-visual"><p class="kicker">{f["n"]} · {esc(f["label"])}</p><ol class="mech">{mech}</ol></div>
<div class="atlas-copy"><h3>{esc(f["label"])}</h3><p class="atlas-principle">{esc(f["principle"])}</p>
<dl class="atlas-facts"><div><dt>What is public</dt><dd>{esc(f["disclosed"])}</dd></div><div><dt>Where evidence stops</dt><dd>{esc(f["boundary"])}</dd></div>
<div><dt>Representatives</dt><dd><ul class="reps">{reps}</ul></dd></div></dl>
{f'<a class="more-link" href="#ecosystem" data-eco-family="{f["id"]}">Open models and readouts in this family <svg class="icon"><use href="#i-arrow"/></svg></a>' if has_models else ''}
<div class="atlas-papers"><p class="kicker">Studies in this family</p><div>{plinks}</div></div></div></article>''')
    return f'<div class="atlas-tabs" role="tablist" aria-label="Method families">{"".join(tabs)}</div><div class="atlas-panels">{"".join(panels)}</div>'


# --------------------------------------------------------------------------------------------- ecosystem
def repo_claims(x):
    out = defaultdict(list)
    for c in x.claims:
        if c['subject'].startswith('gh:'):
            out[c['subject']].append(c)
    return out


def render_eco_tabs(x):
    out = []
    for i, role in enumerate(ECO_ORDER):
        sel = i == 0
        tabindex = '' if sel else ' tabindex="-1"'
        out.append(f'<button role="tab" id="eco-tab-{role}" aria-selected="{str(sel).lower()}" aria-controls="eco-{role}"{tabindex} data-eco="{role}">'
                   f'{esc(x.T["ecosystem_roles"][role]["label"])}</button>')
    return ''.join(out)


def render_ecosystem(x):
    rc = repo_claims(x)
    fam_order = [f['id'] for f in x.tax['method_families']]
    by_role = defaultdict(list)
    for r in x.repos:
        by_role[r['ecosystem']].append(r)

    def reports(r):
        cs = rc.get(r['id'], [])
        return ''.join(f'<p class="eco-report"><span>Reports</span>{esc(c["headline"])}</p>' for c in cs)

    # open models: matrix
    models = sorted(by_role['open_model'], key=lambda r: (fam_order.index(r['method_family']) if r.get('method_family') else 99, r['full_name'].lower()))
    fams_present = [f for f in x.tax['method_families'] if any(r.get('method_family') == f['id'] for r in models)]
    chips = '<button type="button" aria-pressed="true" data-eco-fam="">All</button>' + ''.join(
        f'<button type="button" aria-pressed="false" data-eco-fam="{f["id"]}">{esc(f["label"])}</button>' for f in fams_present)
    if any(not r.get('method_family') for r in models):
        chips += '<button type="button" aria-pressed="false" data-eco-fam="none">Not classified</button>'
    cols = [('weights', 'Weights'), ('training_code', 'Training'), ('inference_code', 'Inference'), ('evaluation', 'Evaluation'), ('data', 'Data'), ('raw_predictions', 'Predictions')]
    rows = []
    for r in models:
        a = r.get('availability') or {}
        fam = x.T['method_families'][r['method_family']]['label'] if r.get('method_family') else 'Not classified'
        rows.append(f'<tr data-fam="{esc(r.get("method_family") or "none")}"><th scope="row"><a href="{esc(r.get("readme_url") or r["url"])}" target="_blank" rel="noopener noreferrer">{esc(r["full_name"])}</a>'
                    f'<span>{esc(fam)}{" · " + esc(r["base_model"]) if r.get("base_model") else ""}</span><p>{esc(r["summary"])}</p></th>'
                    + ''.join(f'<td>{avail_cell(a.get(k, "not_assessed"))}</td>' for k, _ in cols) + f'<td class="lic">{esc(r.get("license") or "—")}</td></tr>')
    legend = ''.join(f'<span>{avail_cell(k)} {esc(AVAIL[k][2])}</span>' for k in ('available', 'partial', 'restricted', 'project_page_only', 'not_located', 'not_applicable'))
    head = ''.join(f'<th scope="col">{c}</th>' for _, c in cols)
    p1 = f'''<div class="eco-panel" role="tabpanel" id="eco-open_model" aria-labelledby="eco-tab-open_model" data-eco-panel="open_model">
<p class="eco-desc">{esc(x.T["ecosystem_roles"]["open_model"]["desc"])}</p><div class="chips" role="group" aria-label="Filter by method family">{chips}</div>
<div class="card table-scroll eco-scroll" tabindex="0" role="region" aria-label="Open models and readouts"><table class="eco-table"><thead><tr><th scope="col">Resource</th>{head}<th scope="col">Licence</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<div class="av-legend">{legend}<span class="av-note">Licence is the field GitHub detects; NOASSERTION means “not identified”, not “no licence”.</span></div></div>'''

    # benchmarks: cards
    benches = sorted(by_role['benchmark'], key=lambda r: (r['type'] != 'benchmark', r['full_name'].lower()))
    items = ''.join(f'''<article class="eco-card card"><p class="kicker">{esc(x.T["repository_types"][r["type"]]["label"])}{" · " + esc(x.T["applications"][r["domain"]]["label"]) if r.get("domain") else ""}</p>
<h4><a href="{esc(r.get("readme_url") or r["url"])}" target="_blank" rel="noopener noreferrer">{esc(r["full_name"])} ↗</a></h4><p>{esc(r["summary"])}</p>{reports(r)}<p class="eco-bound"><b>Boundary</b> {esc(r["boundary"])}</p></article>'''
                    for r in benches)
    p2 = f'''<div class="eco-panel" role="tabpanel" id="eco-benchmark" aria-labelledby="eco-tab-benchmark" data-eco-panel="benchmark" hidden>
<p class="eco-desc">{esc(x.T["ecosystem_roles"]["benchmark"]["desc"])}</p><div class="eco-cards">{items}</div></div>'''

    # built with: grouped by domain
    groups = defaultdict(list)
    for r in by_role['built_with']:
        groups[r.get('domain')].append(r)
    blocks = []
    for app in x.tax['applications'] + [dict(id=None, label='Other')]:
        rs = sorted(groups.get(app['id'], []), key=lambda r: r['full_name'].lower())
        if not rs:
            continue
        lis = ''.join(f'<li><a href="{esc(r.get("readme_url") or r["url"])}" target="_blank" rel="noopener noreferrer">{esc(r["full_name"])} ↗</a><p>{esc(r["summary"])}</p>{reports(r)}</li>' for r in rs)
        blocks.append(f'<article class="eco-group card"><h4>{esc(app["label"])}</h4><ul>{lis}</ul></article>')
    p3 = f'''<div class="eco-panel" role="tabpanel" id="eco-built_with" aria-labelledby="eco-tab-built_with" data-eco-panel="built_with" hidden>
<p class="eco-desc">{esc(x.T["ecosystem_roles"]["built_with"]["desc"])} Most report demonstrations or the builders’ own measurements.</p><div class="eco-groups">{"".join(blocks)}</div></div>'''

    # tooling
    tools = sorted(by_role['tooling'], key=lambda r: (r['type'], r['full_name'].lower()))
    lis = ''.join(f'<li><span class="kicker">{esc(x.T["repository_types"][r["type"]]["label"])}{" · official" if r["affiliation"].startswith("official") else ""}</span>'
                  f'<a href="{esc(r.get("readme_url") or r["url"])}" target="_blank" rel="noopener noreferrer">{esc(r["full_name"])} ↗</a><p>{esc(r["summary"])}</p></li>' for r in tools)
    p4 = f'''<div class="eco-panel" role="tabpanel" id="eco-tooling" aria-labelledby="eco-tab-tooling" data-eco-panel="tooling" hidden>
<p class="eco-desc">{esc(x.T["ecosystem_roles"]["tooling"]["desc"])}</p><ul class="tool-list card">{lis}</ul></div>'''
    frags = {'eco-benchmark': p2, 'eco-built_with': p3, 'eco-tooling': p4}
    x.fragments.update({k: re.sub(r'^<div[^>]*>|</div>$', '', v.strip()) for k, v in frags.items()})
    stubs = ''.join(f'<div class="eco-panel" role="tabpanel" id="eco-{role}" aria-labelledby="eco-tab-{role}" data-eco-panel="{role}" data-fragment="site/data/fragments/eco-{role}.html" hidden>'
                    f'<p class="fine">Loading…</p></div>' for role in ('benchmark', 'built_with', 'tooling'))
    return p1 + stubs


def render_lineage(x):
    out = []
    titles = {'study_family': 'Study family', 'same_author_group': 'Same author group', 'derived_from': 'Derived from', 'uses_resources_from': 'Uses resources from',
              'contrasts_with': 'Compatible, not equivalent', 'name_collision': 'Same name, different thing'}
    for rel in x.d['review-relations']['relations']:
        t = rel['type']
        if t not in titles or rel.get('term') == 'survey':
            continue
        if t == 'name_collision':
            head = f'“{esc(rel["term"])}”'
        elif t in ('study_family', 'same_author_group'):
            head = ' · '.join(esc(x.subject_label(m)) for m in rel['members'])
        else:
            head = f'{esc(x.subject_label(rel["source"]))} → {esc(x.subject_label(rel["target"]))}'
        out.append(f'<article class="lineage card lineage-{t}"><p class="kicker">{esc(titles[t])}</p><h3>{head}</h3><p>{esc(rel["note"])}</p></article>')
    return '\n'.join(out)


# --------------------------------------------------------------------------------------------- literature
def paper_row(x, p):
    t = x.T
    authors = p['authors'][:3]
    auth = ', '.join(authors) + (' et al.' if len(p['authors']) > 3 else '')
    tier_lab = {'core': 'Core', 'peripheral': 'Peripheral', 'background': 'Background'}[p['tier']]
    op = p['openness']
    open_flags = [k for k in ('code', 'weights', 'predictions') if op[k]['status'] in ('available', 'partial', 'project_page_only')]
    if p['tier'] != 'background' and op['code']['status'] in ('not_located', 'claimed_not_located'):
        open_flags.append('none')
    venue = p['venue'] if p['venue_source'] != 'arxiv_metadata' else 'arXiv preprint'
    rels = ' '.join(p['model_relationship'])
    chips = []
    if p['tier'] != 'background':
        for k, lab in (('code', 'Code'), ('weights', 'Weights'), ('predictions', 'Predictions')):
            st = op[k]['status']
            if st in ('available', 'partial', 'project_page_only'):
                chips.append(f'<span class="res res-{st}">{lab}{"" if st == "available" else " (" + AVAIL[st][2].lower() + ")"}</span>')
        if op['code']['status'] in ('not_located', 'claimed_not_located'):
            chips.append(f'<span class="res res-none">{"Code announced, not found" if op["code"]["status"] == "claimed_not_located" else "Code not located"}</span>')
    lead = p.get('short_title') if p['tier'] != 'background' else x.T['background_groups'][p['background_group']]['label']
    blurb = p.get('main_finding') if p['tier'] != 'background' else p.get('role')
    return f'''<li class="paper-row tier-{p["tier"]}" id="paper-{esc(p["arxiv_id"])}" data-id="{esc(p["id"])}" data-tier="{p["tier"]}" data-year="{p["year"]}" data-date="{esc(p["published_at"][:10])}"
 data-stages="{esc(" ".join(p["stages"]))}" data-families="{esc(" ".join(p["method_families"]))}" data-rel="{esc(rels)}" data-topics="{esc(" ".join(p["topics"]))}" data-open="{esc(" ".join(open_flags))}" data-title="{esc(p["title"].lower())}">
<div class="paper-main"><p class="paper-lead"><span class="tier-badge tier-{p["tier"]}">{tier_lab}</span>{esc(lead or "")}</p>
<a class="paper-title" href="{esc(p["url"])}" target="_blank" rel="noopener noreferrer">{esc(p["title"])}</a><p class="paper-authors">{esc(auth)}</p>
<p class="paper-blurb">{esc(blurb or "")}</p><div class="paper-meta"><span class="venue">{esc(venue)}</span><span>{esc(J.fmt_date(p["published_at"]))}</span>{"".join(chips)}</div></div>
<button class="evidence-btn" type="button" data-open-paper="{esc(p["id"])}" aria-label="Open evidence for {esc(p["title"])}">Evidence<svg class="icon"><use href="#i-arrow"/></svg></button></li>'''


def render_rows(x):
    order = {'core': 0, 'peripheral': 1, 'background': 2}
    ps = sorted(x.papers, key=lambda p: (order[p['tier']], -int(p['published_at'][:10].replace('-', '')), p['title']))
    first = 20  # the explorer shows 20 rows at a time; the rest arrive as a fragment before the reader gets there
    x.fragments['more-rows'] = '\n'.join(paper_row(x, p) for p in ps[first:])
    return '\n'.join(paper_row(x, p) for p in ps[:first])


def render_tier_buttons(x):
    b = [('', 'All references'), ('core', 'Core studies'), ('peripheral', 'Peripheral'), ('background', 'Background')]
    return ''.join(f'<button class="filter-button{" active" if k == "" else ""}" type="button" data-tier-filter="{k}" aria-pressed="{str(k == "").lower()}"><span>{esc(lab)}</span></button>' for k, lab in b)


def options(items, counts=None):
    out = []
    for i in items:
        if counts is not None and not counts[i['id']]:
            continue  # no record uses this value; an empty option would only return nothing
        out.append(f'<option value="{esc(i["id"])}">{esc(i["label"])}</option>')
    return ''.join(out)


# --------------------------------------------------------------------------------------------- about
def render_method_blocks(x):
    repo = J.safe_url(x.cfg.get('repository_url'))
    branch = x.cfg.get('repository_default_branch') or 'main'
    doc = lambda path: f'{repo.rstrip("/")}/blob/{branch}/{path}' if repo else path
    files = [('data/references.bib', 'references.bib'), ('data/exports/papers.csv', 'papers.csv'), ('data/exports/claims.csv', 'claims.csv'),
             ('data/exports/repositories.csv', 'repositories.csv'), ('data/search-runs.json', 'search log (JSON)')]
    dl = ''.join(f'<li><a href="{f}"{" download" if not f.endswith(".json") else ""}>{esc(lab)}</a></li>' for f, lab in files)
    return f'''<article class="card pad"><h3>Scope &amp; sources</h3><ul class="check-list">
<li><b>Core studies</b> evaluate Jev, build a Jev-like typed decision model, or depend on one inside a system.</li>
<li><b>Background</b> references cover adjacent and earlier work: classifiers, calibration, selective prediction, structured output, routing, judging and related reviews.</li>
<li>Studies come from repeated arXiv searches; repositories from GitHub searches and curated lists, each checked against its own README. Queries and screening decisions are published with the data.</li></ul></article>
<article class="card pad"><h3>Reading the evidence</h3><ul class="check-list">
<li>Core studies are read in full, and revised versions are re-read; every evidence record points to the section or table it comes from.</li>
<li>Values are as reported by authors, the vendor or community repositories; they were not re-run.</li>
<li>Openness is recorded field by field — code, weights, data, predictions — because each can be released on its own.</li></ul>
<p class="fine">Details: <a href="{esc(doc("docs/methodology.md"))}">methodology</a> · <a href="{esc(doc("docs/limitations.md"))}">limitations</a> · <a href="{esc(doc("docs/evidence-audit.md"))}">evidence audit</a></p></article>
<article class="card pad"><h3>Data</h3><ul class="dl-list">{dl}</ul><p class="fine">Code under the MIT License; text, figures and data under CC BY 4.0.</p></article>'''


def render_related_work(x):
    rs = x.d['review-relations']['related_surveys']
    cols = [('scope', 'Scope'), ('unit', 'Unit of analysis'), ('records', 'What is recorded'), ('release', 'Released material'), ('versioning', 'Versioning')]
    this = dict(scope=f'Studies, repositories and vendor documents on Jev and Jev-like models, 15 Sep 2026 to {J.fmt_date(x.stats["cutoff"])}',
                unit='Studies, evidence records, repositories', records='Claim-level evidence with locators, sample sizes and versions; openness field by field; failure modes',
                release='JSON, CSV, BibTeX, website, manuscript', versioning='Dated, versioned releases')
    head = ''.join(f'<th scope="col">{c}</th>' for _, c in cols)
    rows = [f'<tr class="is-this"><th scope="row">This survey</th>' + ''.join(f'<td>{esc(this[k])}</td>' for k, _ in cols) + '</tr>']
    for r in rs:
        name = f'<a href="{esc(r["url"])}" target="_blank" rel="noopener noreferrer">{esc(r.get("short") or r["title"])} ↗</a>'
        rows.append(f'<tr><th scope="row">{name}<span>{esc(r.get("kind_label") or "")}</span></th>' + ''.join(f'<td>{esc(r.get(k) or "")}</td>' for k, _ in cols) + '</tr>')
    return f'<table class="rel-table"><thead><tr><th scope="col">Review or catalogue</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def render_news(x):
    return ''.join(f'<li><time datetime="{d}">{esc(J.fmt_date(d))}</time><p>{esc(t)}</p></li>' for d, t in NEWS)


def site_bibtex(x):
    authors = ' and '.join(f'{a["family_names"]}, {a["given_names"]}' for a in x.cfg.get('authors') or [] if a.get('family_names'))
    url = J.safe_url(x.cfg.get('site_url')) or ''
    return (f'@misc{{luo2026jevsurvey,\n  title        = {{Jev and Typed Decision Models: An Empirical Survey of Calibration,\n                  Selective Control, and Open Implementations}},\n'
            f'  author       = {{{authors}}},\n  year         = {{2026}},\n  howpublished = {{\\url{{{url}}}}},\n'
            f'  note         = {{Version {J.VERSION}, updated {J.fmt_date(x.stats["cutoff"])}. Working draft}}\n}}')


def render_contribute(x):
    repo = J.safe_url(x.cfg.get('repository_url'))
    kinds = [('add-paper', 'Suggest a study', 'A paper or preprint within scope, with its canonical link and relationship to Jev.'),
             ('correction', 'Correct a record', 'A wrong number, locator, version or availability status — with the source.'),
             ('resource-update', 'Update code or weights', 'New releases, commits or licences for a model, adapter or evaluation.'),
             ('reproduction', 'Report a reproduction', 'A rerun of a reported result, with protocol, versions and run log.')]
    out = []
    for key, title, desc in kinds:
        href = f'{repo.rstrip("/")}/issues/new?template={key}.yml' if repo else f'CONTRIBUTING.md#{key}'
        ext = ' target="_blank" rel="noopener noreferrer"' if repo else ''
        out.append(f'<a class="contrib-card card" href="{esc(href)}"{ext}><strong>{esc(title)}</strong><span>{esc(desc)}</span><svg class="icon"><use href="#i-north"/></svg></a>')
    return ''.join(out)


# --------------------------------------------------------------------------------------------- data outputs
def drawer_data(x):
    out = {}
    for p in x.papers:
        claims = []
        for cid in p['claims']:
            c = x.C[cid]
            claims.append(dict(id=cid, headline=c['headline'], text=c['claim_text'], locator=c['locator'], type=x.T['evidence_types'][c['evidence_type']]['label'],
                               scope=x.T['measurement_scopes'][c['measurement_scope']]['label'], test=x.T['test_levels'][c['test_level']]['label'],
                               n=c['sample_size'], version=c['model_version'], limitations=c['limitations'],
                               findings=[f'{l["id"]} {l["relation"]}' for l in c['findings']]))
        rec = dict(id=p['id'], arxiv=p['arxiv_id'], vid=p['versioned_id'], title=p['title'], short=p.get('short_title'), authors=p['authors'],
                   tier=p['tier'], year=p['year'], published=p['published_at'][:10], updated=p['updated_at'][:10],
                   venue=p['venue'], venue_source=p['venue_source'], venue_note=p.get('venue_note'), doi=p.get('doi'), url=p['url'], pdf=p['pdf_url'],
                   relationship=p.get('relationship'), summary=p.get('summary'), task=p.get('task'), datasets=p.get('datasets'), baselines=p.get('baselines'),
                   finding=p.get('main_finding'), caveats=p.get('caveats'), locator=p.get('evidence_locator'),
                   test=x.T['test_levels'][p['test_level']]['label'] if p.get('test_level') else None, versions=p.get('model_versions'),
                   openness={k: dict(v, label=AVAIL.get(v['status'], ('', '', v['status']))[2]) for k, v in p['openness'].items()},
                   claims=claims, role=p.get('role'), group=x.T['background_groups'][p['background_group']]['label'] if p.get('background_group') else None,
                   topics=[x.T['topics'][k]['label'] for k in p['topics']], stages=[x.T['stages'][k]['label'] for k in p['stages']],
                   families=[x.T['method_families'][k]['label'] for k in p['method_families']],
                   rel=[x.T['model_relationships'][k]['label'] for k in p['model_relationship']],
                   family=p.get('study_family_id'),
                   bibtex=J.bibtex_for_paper(p), key=p['bibtex_key'])
        out[p['id']] = rec
    return out


def export_csvs(x):
    exp = ROOT / 'data' / 'exports'
    exp.mkdir(parents=True, exist_ok=True)

    def write(name, header, rows):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator='\n')
        w.writerow(header)
        for r in rows:
            w.writerow([J.csv_safe(v) for v in r])
        (exp / name).write_text(buf.getvalue(), encoding='utf-8')
    write('papers.csv', ['id', 'tier', 'title', 'short_title', 'authors', 'published', 'versioned_id', 'venue', 'venue_source', 'url', 'topics', 'stages',
                         'method_families', 'model_relationship', 'test_level', 'code', 'weights', 'data', 'predictions', 'recomputable', 'reproduction',
                         'main_finding', 'caveats', 'bibtex_key'],
          [[p['id'], p['tier'], p['title'], p.get('short_title'), p['authors'], p['published_at'][:10], p['versioned_id'], p['venue'], p['venue_source'], p['url'],
            p['topics'], p['stages'], p['method_families'], p['model_relationship'], p.get('test_level')] +
           [p['openness'][k]['status'] for k in ('code', 'weights', 'data', 'predictions', 'recomputable', 'reproduction')] +
           [p.get('main_finding') or p.get('role'), p.get('caveats'), p['bibtex_key']] for p in x.papers])
    write('claims.csv', ['id', 'subject', 'headline', 'claim_text', 'evidence_type', 'locator', 'source_url', 'metric', 'value', 'unit', 'baseline', 'task',
                         'sample_n', 'sample_unit_or_reason', 'model', 'model_version', 'hardware', 'test_level', 'measurement_scope', 'findings', 'limitations'],
          [[c['id'], c['subject'], c['headline'], c['claim_text'], c['evidence_type'], c['locator'], c['source_url'], c['metric'], c['value'], c['unit'], c['baseline'],
            c['task'], c['sample_size'].get('n'), c['sample_size'].get('unit') or c['sample_size'].get('reason'), c['model'],
            c['model_version'].get('value') or f"null ({c['model_version'].get('reason')})", c['hardware'].get('value') or f"null ({c['hardware'].get('reason')})",
            c['test_level'], c['measurement_scope'], [f"{l['id']}:{l['relation']}" for l in c['findings']], c['limitations']] for c in x.claims])
    write('repositories.csv', ['id', 'full_name', 'sets', 'ecosystem', 'type', 'domain', 'method_family', 'base_model', 'commit', 'readme_url', 'license', 'audit_depth',
                               'selected_source_read', 'code', 'weights', 'training_code', 'inference_code', 'evaluation', 'data', 'raw_predictions', 'summary', 'boundary'],
          [[r['id'], r['full_name'], r['sets'], r['ecosystem'], r['type'], r.get('domain'), r.get('method_family'), r.get('base_model'), r.get('commit'), r.get('readme_url'),
            r.get('license'), r['audit_depth'], r['selected_source_read']]
           + [(r.get('availability') or {}).get(k) for k in ('code', 'weights', 'training_code', 'inference_code', 'evaluation', 'data', 'raw_predictions')]
           + [r.get('summary'), r.get('boundary')] for r in x.repos])


def export_bibtex(x):
    head = f'% Jev Survey bibliography — generated by scripts/build.py from data/papers.json. Do not edit.\n% Cutoff {x.stats["cutoff"]}.\n\n'
    order = {'core': 0, 'peripheral': 1, 'background': 2}
    ps = sorted(x.papers, key=lambda p: (order[p['tier']], p['arxiv_id']))
    (ROOT / 'data' / 'references.bib').write_text(head + '\n'.join(J.bibtex_for_paper(p) for p in ps), encoding='utf-8')
    extra = [J.bibtex_for_source(s) for s in x.d['sources']['sources']]
    for rs in x.d['review-relations']['related_surveys']:
        if rs.get('commit') and rs.get('manuscript_commit'):
            checked = (rs.get('checked') or '2026-09-23')[:10]
            extra.append(f'@misc{{{rs["bibtex_key"]},\n  title = {{{{{J.bib_text(rs["title"])}}}}},\n  author = {{Anonymous Authors}},\n  year = {{2026}},\n  howpublished = {{Public working draft on GitHub, dated 21 September 2026}},\n  url = {{{rs["url"]}}},\n  note = {{Commit {rs["commit"][:10]}; checked {checked}}}\n}}\n')
    for r in x.repos:  # every audited repository can be cited from the manuscript
        extra.append(J.bibtex_for_repo(r, J.repo_key(r['id'])))
    (ROOT / 'paper' / 'references.bib').write_text(head.replace('data/papers.json', 'data/*.json') + '\n'.join(J.bibtex_for_paper(p) for p in ps) + '\n' + '\n'.join(extra), encoding='utf-8')


# --------------------------------------------------------------------------------------------- page
def render_page(x):
    tpl = (ROOT / 'site' / 'index.template.html').read_text(encoding='utf-8')
    s = x.stats
    cfg = x.cfg
    repo = J.safe_url(cfg.get('repository_url'))
    site_url = J.safe_url(cfg.get('site_url'))
    authors = [a for a in cfg.get('authors') or [] if a.get('name')]
    branch = cfg.get('repository_default_branch') or 'main'

    def repo_file(kind, path):
        """Repository view of a file or folder once the repository exists; a relative path before that."""
        return f'{repo.rstrip("/")}/{kind}/{branch}/{path}' if repo else (path + '/' if kind == 'tree' else path)
    t = x.tax
    fam_counts = {f['id']: sum(1 for p in x.papers if f['id'] in p['method_families']) for f in t['method_families']}
    stage_counts = {st['id']: sum(1 for p in x.papers if st['id'] in p['stages']) for st in t['stages']}
    rel_counts = {r['id']: sum(1 for p in x.papers if r['id'] in p['model_relationship']) for r in t['model_relationships']}
    topic_counts = {tp['id']: sum(1 for p in x.papers if tp['id'] in p['topics']) for tp in t['topics']}
    values = {
        'cutoff_long': J.fmt_date(s['cutoff']),
        'canonical': f'<link rel="canonical" href="{esc(site_url)}">' if site_url else '',
        'og_image': esc((site_url.rstrip('/') + '/assets/og-image.png') if site_url else 'assets/og-image.png'),
        'repo_href': esc(repo) if repo else '#about', 'repo_label': 'GitHub' if repo else 'Data &amp; code',
        'footer_credit': esc(cfg.get('footer_credit') or ''),
        'meta_author': ''.join(f'<meta name="author" content="{esc(a["name"])}">' for a in authors[:1]),
        'byline': ('By ' + ', '.join(f'<a href="{esc(J.safe_url(a.get("url")) or "#top")}" rel="author">{esc(a["name"])}</a>' for a in authors)) if authors else '',
        'md_href': esc(repo_file('blob', 'paper/survey.md')), 'data_href': esc(repo_file('tree', 'data')),
        'license_href': esc(repo_file('blob', 'LICENSE')), 'content_license_href': esc(repo_file('blob', 'LICENSE-CONTENT.md')),
        'pager': render_pager(x), 'contents': render_contents(x), 'hero_diagram': render_hero_diagram(x), 'pipeline': render_pipeline(x),
        'primitives': render_primitives(x), 'vendor_claims': render_vendor_claims(x), 'stages': render_stages(x), 'timeline': render_timeline(x),
        'atlas': render_atlas(x), 'findings_list': render_findings(x), 'evidence_matrix': render_matrix(x), 'scope_groups': render_scope_groups(x),
        'lab_panels': render_lab(x), 'fm_filters': render_fm_filters(x), 'failure_modes': render_failure_modes(x), 'control_explorer': render_control_explorer(x),
        'applications': render_applications(x), 'eco_tabs': render_eco_tabs(x), 'ecosystem': render_ecosystem(x), 'lineage': render_lineage(x),
        'tier_buttons': render_tier_buttons(x), 'stage_options': options(t['stages'], stage_counts), 'family_options': options(t['method_families'], fam_counts),
        'rel_options': options(t['model_relationships'], rel_counts), 'topic_options': options(t['topics'], topic_counts),
        'year_options': ''.join(f'<option value="{y}">{y}</option>' for y in s['years']),
        'paper_rows': render_rows(x), 'method_blocks': render_method_blocks(x), 'related_work': render_related_work(x), 'news': render_news(x),
        'site_bibtex': esc(site_bibtex(x)), 'contribute': render_contribute(x),
    }
    css_js = b''.join((ROOT / 'site' / f).read_bytes() for f in ('site.css', 'site.js', 'lab.js', 'catalog.mjs', 'field.js') if (ROOT / 'site' / f).exists())
    values['build_hash'] = hashlib.sha256(css_js).hexdigest()[:10]

    def sub(m):
        key = m.group(1)
        if key not in values:
            raise KeyError(f'template placeholder {{{{{key}}}}} has no value')
        return str(values[key])
    return re.sub(r'\{\{([a-z_]+)\}\}', sub, tpl)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='fail if generated files differ from the committed ones')
    args = ap.parse_args()
    v = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'validate-data.py'), '--quiet'])
    if v.returncode:
        sys.exit('validation failed; nothing built')
    x = Ctx(J.load_all())
    targets = ['index.html', 'site/data/drawer.json', 'data/references.bib', 'paper/references.bib', 'README.md',
               'data/exports/papers.csv', 'data/exports/claims.csv', 'data/exports/repositories.csv', 'data/README.md',
               'docs/methodology.md', 'docs/evidence-audit.md', 'paper/survey.md', 'paper/main.tex',
               'site/data/fragments/more-rows.html', 'site/data/fragments/eco-benchmark.html', 'site/data/fragments/eco-built_with.html',
               'site/data/fragments/eco-tooling.html']
    before = {t: (ROOT / t).read_bytes() if (ROOT / t).exists() else None for t in targets}
    (ROOT / 'site' / 'data').mkdir(parents=True, exist_ok=True)
    (ROOT / 'site' / 'data' / 'drawer.json').write_text(json.dumps(drawer_data(x), ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    export_bibtex(x)
    export_csvs(x)
    html = render_page(x)
    (ROOT / 'index.html').write_text(html, encoding='utf-8')
    fdir = ROOT / 'site' / 'data' / 'fragments'
    fdir.mkdir(parents=True, exist_ok=True)
    for name, body in sorted(x.fragments.items()):
        (fdir / f'{name}.html').write_text(body + '\n', encoding='utf-8')
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'render-readme.py')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'render-docs.py')], check=True)
    # manuscript text and tables (figures and PDF need matplotlib/LaTeX: scripts/build-paper.py [--pdf])
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build-paper.py'), '--no-figures'], check=True, capture_output=True)
    changed = [t for t in targets if before[t] != ((ROOT / t).read_bytes() if (ROOT / t).exists() else None)]
    size = lambda p: (ROOT / p).stat().st_size
    print(f'built index.html {size("index.html") / 1024:.1f} KB · drawer.json {size("site/data/drawer.json") / 1024:.1f} KB')
    if args.check and changed:
        print('generated files out of date:', ', '.join(changed))
        sys.exit(1)


if __name__ == '__main__':
    main()
