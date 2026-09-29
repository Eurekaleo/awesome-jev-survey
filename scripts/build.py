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
STAGE_FINDINGS = {'contract': ['F1'], 'readout': ['F5'], 'probability': ['F2'], 'control': ['F3'], 'use': ['F4', 'F6']}
MECHANISM = {
    'hosted_service': ['Text state + typed questions', 'Undisclosed hosted model', 'Undisclosed; distribution over the declared options', 'Answer + probabilities + confidence'],
    'encoder_head': ['State ⊕ question ⊕ options', 'Bidirectional encoder (ModernBERT, mmBERT)', 'Per-option score → softmax over the legal set', 'Distribution over options'],
    'frozen_decoder_readout': ['Prompt listing options with single-token labels', 'Frozen causal LM', 'Next-token logits of the labels, renormalised; optional calibration', 'Distribution over options'],
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
         watch='The model reads option names <em>and</em> descriptions; a name can outweigh its description.', finding='F1',
         url='https://docs.typesafe.ai/primitives/choice'),
    dict(name='Score', returns='A position on ordered levels (it may fall between levels), probabilities per level and confidence.',
         semantics='Two to ten described levels. Ordinal, not a regression: the vendor warns against interpolating exact magnitudes.',
         watch='Level descriptions are judged one by one; the numeric position is an expectation, not a measurement.', finding='F2',
         url='https://docs.typesafe.ai/primitives/score'),
    dict(name='Noul', returns='The probability that the answer is yes. No separate confidence field.',
         semantics='A proposition probability, optionally with descriptions of what counts as true and false.',
         watch='A Noul and a two-option Choice over the same question need not agree (community audit).', finding='F1',
         url='https://docs.typesafe.ai/primitives/noul'),
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
                                                   'repository_types')}
        self.stats = J.compute_stats(d)
        self.cfg = d['config']

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


# --------------------------------------------------------------------------------------------- sections
def render_primitives(x):
    out = []
    for p in PRIMITIVES:
        f = x.T['findings'][p['finding']]
        out.append(f'''<article class="primitive"><div class="prim-head"><code>{p["name"].lower()}</code><h3>{p["name"]}</h3></div>
<dl><div><dt>Returns</dt><dd>{esc(p["returns"])}</dd></div><div><dt>Semantics</dt><dd>{esc(p["semantics"])}</dd></div>
<div><dt>Watch</dt><dd>{p["watch"]} <a class="finding-chip" href="#finding-{f["id"]}">{f["id"]} · {esc(f["short"])}</a></dd></div></dl>
<a class="doc-link" href="{esc(p["url"])}" target="_blank" rel="noopener noreferrer">Documentation ↗</a></article>''')
    return '\n'.join(out)


def render_vendor_claims(x):
    out = []
    for cid in ('v-price-latency', 'v-workflow-evals', 'v-types'):
        c = x.C[cid]
        out.append(f'<li><strong>{esc(c["headline"])}</strong><span>{esc(c["limitations"][0] if c["limitations"] else "")}</span>'
                   f'<a href="{esc(c["source_url"])}" target="_blank" rel="noopener noreferrer">{esc(c["locator"])} ↗</a></li>')
    return '\n'.join(out)


def render_stages(x):
    primary = [p for p in x.papers if p['tier'] in ('core', 'peripheral')]
    out = []
    for s in x.tax['stages']:
        studies = [p for p in primary if s['id'] in p['stages']]
        chips = ''.join(f'<a class="finding-chip" href="#finding-{f}">{f} · {esc(x.T["findings"][f]["short"])}</a>' for f in STAGE_FINDINGS[s['id']])
        names = ', '.join(esc(J.label(p)) for p in studies[:4]) + (f' and {len(studies) - 4} more' if len(studies) > 4 else '')
        out.append(f'''<li class="stage" id="stage-{s["id"]}"><div class="stage-top"><span class="stage-n">{s["n"]}</span></div>
<h3>{esc(s["label"])}</h3><p class="stage-q">{esc(s["question"])}</p><p class="stage-covers">{esc(s["covers"])}</p>
<p class="stage-evidence"><span>Evidence to look for</span>{esc(s["evidence"])}</p><div class="stage-chips">{chips}</div>
<p class="stage-studies">{names}</p></li>''')
    return '\n'.join(out)


def render_timeline(x):
    import datetime as dt
    last = max(p['published_at'][:10] for p in x.papers if p['tier'] in ('core', 'peripheral'))
    first = dt.date(2026, 9, 15)  # launch
    days = [(first + dt.timedelta(days=i)).isoformat() for i in range((dt.date.fromisoformat(last) - first).days + 1)]
    events = defaultdict(list)
    events['2026-09-15'].append(('event', 'Jev released in early access (vendor launch post)', None, None))
    for p in sorted([p for p in x.papers if p['tier'] in ('core', 'peripheral')], key=lambda p: p['published_at']):
        rel = p['model_relationship'][0]
        events[p['published_at'][:10]].append(('paper', J.label(p), rel, p))
    cols, rows = [], []
    for day in days:
        items = []
        for kind, text, rel, p in events.get(day, []):
            if kind == 'event':
                items.append(f'<li class="tl-event">{esc(text)}</li>')
            else:
                peri = ' tl-peripheral' if p['tier'] == 'peripheral' else ''
                items.append(f'<li class="tl-paper rel-{rel}{peri}"><button type="button" data-open-paper="{esc(p["id"])}"><span class="tl-dot" aria-hidden="true"></span>'
                             f'<span>{esc(text)}</span></button></li>')
                rows.append(f'<tr><td>{esc(J.fmt_date(day))}</td><td>{esc(p["title"])}</td><td>{esc(p["tier"])}</td><td>{esc(REL_PRIMARY_LABEL.get(rel, rel))}</td></tr>')
        d = int(day[-2:])
        cols.append(f'<div class="tl-day{" tl-empty" if not items else ""}"><div class="tl-date"><b>{d}</b><span>{dt.date.fromisoformat(day).strftime("%b")}</span></div><ul>{"".join(items)}</ul></div>')
    legend = ''.join(f'<span class="lg rel-{k}"><i aria-hidden="true"></i>{esc(v)}</span>' for k, v in REL_PRIMARY_LABEL.items())
    legend += '<span class="lg lg-event"><i aria-hidden="true"></i>Context event</span><span class="lg lg-peri"><i aria-hidden="true"></i>Peripheral (dashed ring)</span>'
    table = ('<table class="viz-table" data-table="timeline" hidden><caption class="sr-only">Submission dates of core and peripheral studies</caption>'
             '<thead><tr><th scope="col">Date (arXiv v1)</th><th scope="col">Title</th><th scope="col">Tier</th><th scope="col">Primary relationship</th></tr></thead><tbody>'
             + ''.join(rows) + '</tbody></table>')
    return f'<div class="tl-legend">{legend}</div><div class="timeline" data-chart="timeline">{"".join(cols)}</div>{table}'


def avail_cell(status, note=None):
    g, cls, lab = AVAIL.get(status, ('?', 'av-na', status))
    title = esc(lab + (f': {note}' if note else ''))
    return f'<span class="av {cls}" role="img" aria-label="{title}" title="{title}">{g}</span>'


def render_atlas(x):
    fams = x.tax['method_families']
    primary = [p for p in x.papers if p['tier'] in ('core', 'peripheral')]
    tabs = []
    panels = []
    for i, f in enumerate(fams):
        sel = i == 0
        tabindex = '' if sel else ' tabindex="-1"'
        tabs.append(f'<button role="tab" id="atlas-tab-{f["id"]}" aria-selected="{str(sel).lower()}" aria-controls="atlas-{f["id"]}"'
                    f'{tabindex} data-atlas="{f["id"]}"><span>{f["n"]}</span>{esc(f["label"])}</button>')
        mech = ''.join(f'<li class="{"mech-key" if j == 2 else ""}"><span>{MECH_LABELS[j]}</span>{esc(t)}</li>' for j, t in enumerate(MECHANISM[f['id']]))
        repo_rows = []
        for rid in f['repos']:
            r = x.R.get(rid)
            if not r:
                continue
            a = r.get('availability') or {}
            cells = ''.join(f'<span class="mini-av"><em>{lab}</em>{avail_cell(a.get(k, "not_assessed"))}</span>'
                            for k, lab in (('code', 'code'), ('weights', 'weights'), ('training_code', 'train'), ('evaluation', 'eval')))
            repo_rows.append(f'<li><a href="{esc(r["url"])}" target="_blank" rel="noopener noreferrer">{esc(r["full_name"])} ↗</a>'
                             f'<span class="repo-meta">{esc(x.T["repository_types"][r["type"]]["label"])}{" · " + esc(r["base_model"]) if r.get("base_model") else ""}</span>'
                             f'<span class="mini-avs">{cells}</span></li>')
        papers = [p for p in primary if f['id'] in p['method_families']]
        plinks = ''.join(f'<button type="button" class="paper-chip" data-open-paper="{esc(p["id"])}">{esc(J.label(p))}</button>' for p in papers) or '<span class="muted">No core study uses this family as its main object.</span>'
        reps = ''.join(f'<li>{esc(r)}</li>' for r in f['representatives'])
        panels.append(f'''<article class="atlas-panel{' is-active' if sel else ''}" role="tabpanel" id="atlas-{f["id"]}" aria-labelledby="atlas-tab-{f["id"]}" data-panel="{f["id"]}">
<div class="atlas-visual"><p class="atlas-kicker">{f["n"]} / {esc(f["label"].upper())}</p><ol class="mech">{mech}</ol>
</div>
<div class="atlas-copy"><h3>{esc(f["label"])}</h3><p class="atlas-principle">{esc(f["principle"])}</p>
<dl class="atlas-facts"><div><dt>What is public</dt><dd>{esc(f["disclosed"])}</dd></div><div><dt>Where evidence stops</dt><dd>{esc(f["boundary"])}</dd></div>
<div><dt>Representatives</dt><dd><ul class="reps">{reps}</ul></dd></div></dl>
<div class="atlas-repos"><span class="aside-caption">REPOSITORIES</span><ul>{"".join(repo_rows)}</ul></div>
<div class="atlas-papers"><span class="aside-caption">STUDIES IN THIS FAMILY</span><div>{plinks}</div></div></div></article>''')
    return f'<div class="atlas-tabs" role="tablist" aria-label="Method families">{"".join(tabs)}</div><div class="atlas-panels">{"".join(panels)}</div>'


def claim_item(x, c, relation=None):
    """Compact, expandable evidence line: headline + source; the full claim and its limits on expand."""
    et = x.T['evidence_types'][c['evidence_type']]['label']
    lim = f'<p class="claim-lim"><b>Limits</b> {esc(" ".join(c["limitations"]))}</p>' if c['limitations'] else ''
    return (f'<li class="claim claim-{relation or "plain"}"><details><summary><span class="claim-head">{esc(c["headline"])}</span>'
            f'<span class="claim-src">{esc(x.subject_label(c["subject"]))}<span class="et et-{c["evidence_type"]}">{esc(et)}</span></span></summary>'
            f'<div class="claim-body"><p>{esc(c["claim_text"])}</p>{lim}<p class="claim-loc">{x.subject_link(c["subject"])} · {esc(c["locator"])}</p></div></details></li>')


def render_findings(x):
    out = []
    for f in x.tax['findings']:
        sup = [c for c in x.claims if any(l['id'] == f['id'] and l['relation'] == 'supports' for l in c['findings'])]
        qual = [c for c in x.claims if any(l['id'] == f['id'] and l['relation'] in ('qualifies', 'challenges') for l in c['findings'])]
        order = {'author_reported_experiment': 0, 'community_report': 1, 'vendor_documentation': 2, 'vendor_reported_result': 3, 'code_inspection': 4}
        sup.sort(key=lambda c: order[c['evidence_type']])
        first, rest = sup[:4], sup[4:]
        rest_html = ''
        if rest:
            rest_html = f'<details class="more"><summary>More supporting evidence</summary><ul class="claims">{"".join(claim_item(x, c, "supports") for c in rest)}</ul></details>'
        qual_html = ''
        if qual:
            qual_html = f'<p class="claims-label claims-label-q">Qualified or limited by</p><ul class="claims">{"".join(claim_item(x, c, "qualifies") for c in qual)}</ul>'
        out.append(f'''<article class="finding" id="finding-{f["id"]}"><div class="finding-side"><span class="finding-n">{f["id"]}</span><span class="finding-short">{esc(f["short"])}</span></div>
<div class="finding-main"><h3>{esc(f["title"])}</h3><p class="finding-body">{esc(f["body"])}</p>
<div class="finding-evidence"><div><p class="claims-label">Supported by</p><ul class="claims">{"".join(claim_item(x, c, "supports") for c in first)}</ul>{rest_html}</div>
<div>{qual_html}</div></div></div></article>''')
    return '\n'.join(out)


def render_matrix(x):
    rows = []
    fids = [f['id'] for f in x.tax['findings']]
    head = ''.join(f'<th scope="col"><a href="#finding-{fid}" title="{esc(x.T["findings"][fid]["title"])}">{fid}</a><span>{esc(x.T["findings"][fid]["short"])}</span></th>' for fid in fids)
    for p in sorted([p for p in x.papers if p['tier'] in ('core', 'peripheral')], key=lambda p: p['published_at']):
        cells = []
        for fid in fids:
            rels = {l['relation'] for cid in p['claims'] for l in x.C[cid]['findings'] if l['id'] == fid}
            if 'supports' in rels:
                cells.append('<td class="m-sup"><span aria-hidden="true">●</span><span class="sr-only">supports</span></td>')
            elif rels:
                cells.append('<td class="m-qual"><span aria-hidden="true">◐</span><span class="sr-only">qualifies</span></td>')
            else:
                cells.append('<td></td>')
        tl = x.T['test_levels'][p['test_level']]['label']
        fam = ' <span class="fam-tag">study family</span>' if p.get('study_family_id') else ''
        rows.append(f'<tr><th scope="row"><button type="button" data-open-paper="{esc(p["id"])}">{esc(J.label(p))}</button>{fam}<span>{esc(J.fmt_date(p["published_at"]))} · {esc(tl)}{" · peripheral" if p["tier"] == "peripheral" else ""}</span></th>{"".join(cells)}</tr>')
    return f'<table class="matrix"><thead><tr><th scope="col">Study</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def render_scope_groups(x):
    order = ['single_request', 'amortized_question', 'end_to_end', 'batch', 'simulation', 'author_estimate', 'vendor_claim']
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
    reported_cal = [('c-24574-ece', 'CSS annotation, 15 tasks'), ('c-24574-empathy', 'CSS annotation, empathy task'), ('c-24052-recalibration', 'Crash narratives'),
                    ('r-assay-001', 'Community audit (pre-registered)'), ('r-acento', 'Community audit, Spanish'), ('r-ood', 'Community audit, unknowable rule')]
    cal_rows = ''.join(f'<tr><th scope="row">{esc(lab)}</th><td>{esc(C[c]["headline"])}</td><td>{cite(c)}</td></tr>' for c, lab in reported_cal)
    panels = f'''
<div class="lab-panel" role="tabpanel" id="lab-binding" aria-labelledby="lab-tab-binding" data-lab-panel="binding">
  <div class="lab-explainer"><p class="lab-tag tag-illus">Illustrative</p><h3>Keep the rubric. Move the name.</h3>
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
  <div class="lab-reported"><p class="lab-tag tag-reported">Reported</p><h3>What the swap did (n = 1,200)</h3>
    <div class="lg-dots"><span><i style="--c:#86b6ef"></i>AUROC as shipped</span><span><i style="--c:var(--s2)"></i>AUROC after name–rubric swap</span></div>{dumbbell_svg(x)}
    <ul class="facts"><li><b>32.50%</b> of hosted answers flipped vs ~2% under neutral names; <b>24×</b> the test–retest floor.</li>
    <li>Type-error rate: <b>0%</b> in every arm — by construction.</li><li>Random-string names: flips fall back to the neutral regime.</li>
    <li>Order swaps are a different test: one community audit saw 0 argmax flips in 400 on hosted Jev.</li></ul>
    <p class="cites">{cite("c-26758-hosted-swap")}{cite("c-26758-open-inversion")}{cite("r-calibration-audit")}</p></div>
</div>
<div class="lab-panel" role="tabpanel" id="lab-confidence" aria-labelledby="lab-tab-confidence" data-lab-panel="confidence" hidden>
  <div class="lab-explainer"><p class="lab-tag tag-illus">Illustrative</p><h3>One distribution, several summaries.</h3>
    <p>Choice answers return a probability per option; “confidence” compresses that shape into one number. Move the sliders: different summaries disagree about how sure the same distribution is.</p>
    <div class="conf-demo" data-conf-demo><noscript><p class="fine">Interactive sliders need JavaScript.</p></noscript></div></div>
  <div class="lab-reported"><p class="lab-tag tag-reported">Reported</p><h3>What is known about Jev’s <code>confidence</code></h3>
    <ul class="facts"><li>The vendor documents it as a statistic of the returned distribution; Noul answers have none.</li>
    <li>Its exact formula is not stated in prose. The documentation’s interactive demo shows one normalised max-probability formula, labelled as how <em>the demo</em> computes it.</li>
    <li>In one study, native confidence ranked items almost exactly like the maximum label probability (Spearman 0.948–0.999).</li>
    <li>A community audit read confidence as a probability of being correct and found ECE 0.035–0.18 depending on the dataset.</li></ul>
    <p class="cites">{cite("v-confidence")}{cite("c-26550-confidence")}{cite("r-ood")}</p></div>
</div>
<div class="lab-panel" role="tabpanel" id="lab-calibration" aria-labelledby="lab-tab-calibration" data-lab-panel="calibration" hidden>
  <div class="lab-explainer"><p class="lab-tag tag-illus">Illustrative</p><h3>Reliability is a population property.</h3>
    <p>Bin predictions by confidence and compare with how often they were right. The curves are hand-made to show three regimes; they are not Jev measurements.</p>
    <div class="cal-demo" data-cal-demo><noscript><p class="fine">The interactive reliability diagram needs JavaScript.</p></noscript></div></div>
  <div class="lab-reported"><p class="lab-tag tag-reported">Reported</p><h3>Measured calibration varies by task</h3>
    <div class="table-scroll" tabindex="0" role="region" aria-label="Reported calibration results"><table class="facts-table"><thead><tr><th scope="col">Setting</th><th scope="col">Reported</th><th scope="col">Source</th></tr></thead><tbody>{cal_rows}</tbody></table></div>
    <p class="fine">Different datasets, bins and metrics: read each row on its own; do not average across rows.</p></div>
</div>
<div class="lab-panel" role="tabpanel" id="lab-closed" aria-labelledby="lab-tab-closed" data-lab-panel="closed" hidden>
  <div class="lab-explainer"><p class="lab-tag tag-illus">Illustrative</p><h3>A typed answer must pick something.</h3>
    <p>If the right answer is not among the options, the distribution still sums to one. Toggle the abstain option to see where the probability mass goes.</p>
    <div class="closed-demo" data-closed-demo><noscript><p class="fine">The interactive example needs JavaScript.</p></noscript></div></div>
  <div class="lab-reported"><p class="lab-tag tag-reported">Reported</p><h3>Removing the exit</h3>
    <ul class="facts"><li>A community audit removed the “unknown” option from an unanswerable benchmark: accuracy on those items went from 0.950 to 0.000, with 0.79 confidence and a stereotype rate rising from 0.03 to 0.79.</li>
    <li>Forced forms invite invented answers in generative models too (PhantomFill, background).</li>
    <li>The vendor lists contradictory instructions and indirection among known weak spots.</li></ul>
    <p class="cites">{cite("r-calibration-audit")}<a class="cite" href="#paper-2607.20492" data-open-paper="arxiv:2607.20492">PhantomFill · background ↗</a>{cite("v-jaggedness")}</p></div>
</div>'''
    return panels


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
        out.append(f'''<article class="fm"><span class="fm-stage">{esc(x.T["stages"][fm["stage"]]["label"])}</span><h3>{esc(fm["title"])}</h3><p>{esc(fm["desc"])}</p>
<p class="fm-mit"><b>Mitigation</b> {esc(fm["mitigation"])}</p><ul class="fm-src">{"".join(srcs)}</ul></article>''')
    return '\n'.join(out)


def render_control_explorer(x):
    return '''<div class="control-explorer viz-root" data-control-explorer>
  <div class="ce-copy"><p class="lab-tag tag-illus">Illustrative · synthetic scores</p><h3>How a threshold spends the escalation budget</h3>
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
        fam = ' · one study family' if any(p.get('study_family_id') for p in ps) else ''
        claims = ''.join(f'<li><strong>{esc(x.C[c]["headline"])}</strong></li>' for c in card['claims'])
        studies = ''.join(f'<button type="button" class="paper-chip" data-open-paper="{esc(p["id"])}">{esc(J.label(p))}</button>' for p in ps)
        out.append(f'''<article class="app"><div class="app-head"><h3>{esc(app["label"])}</h3><span>{esc(" · ".join(levels))}{fam}</span></div>
<p class="app-decides"><span>The typed model decides</span>{esc(card["decides"])}</p><ul class="app-claims">{claims}</ul>
<p class="app-caution"><b>Caution</b> {esc(card["caution"])}</p><div class="app-studies">{studies}</div></article>''')
    return '\n'.join(out)


def render_openness(x):
    pri = [r for r in x.repos if 'priority' in r['sets']]
    type_order = ['model', 'adapter', 'sdk', 'system', 'evaluation', 'benchmark', 'project_page', 'guide', 'catalogue']
    pri.sort(key=lambda r: (type_order.index(r['type']), r['full_name'].lower()))
    present = {r['type'] for r in pri}
    chips = '<button type="button" aria-pressed="true" data-eco-filter="">All</button>' + ''.join(
        f'<button type="button" aria-pressed="false" data-eco-filter="{t}">{esc(x.T["repository_types"][t]["label"])}</button>' for t in type_order if t in present)
    cols = [('code', 'Code'), ('weights', 'Weights'), ('training_code', 'Training'), ('inference_code', 'Inference'), ('evaluation', 'Evaluation'), ('data', 'Data'), ('raw_predictions', 'Raw predictions')]
    head = ''.join(f'<th scope="col">{c}</th>' for _, c in cols)
    rows = []
    for r in pri:
        a = r['availability']
        fam = x.T['method_families'][r['method_family']]['label'] if r.get('method_family') else '—'
        lic = r.get('license') or '—'
        rows.append(f'<tr data-type="{esc(r["type"])}"><th scope="row"><a href="{esc(r.get("readme_url") or r["url"])}" target="_blank" rel="noopener noreferrer">{esc(r["full_name"])}</a>'
                    f'<span>{esc(x.T["repository_types"][r["type"]]["label"])} · {esc(fam)}</span></th>'
                    + ''.join(f'<td>{avail_cell(a.get(k, "not_assessed"))}</td>' for k, _ in cols)
                    + f'<td class="lic">{esc(lic)}</td></tr>')
    legend = ''.join(f'<span>{avail_cell(k)} {esc(AVAIL[k][2])}</span>' for k in ('available', 'partial', 'restricted', 'project_page_only', 'not_located', 'not_applicable'))
    return f'''<div class="eco-filters" role="group" aria-label="Filter resources by type">{chips}</div>
<div class="table-scroll eco-scroll" tabindex="0" role="region" aria-label="Openness of priority resources"><table class="eco-table"><thead><tr><th scope="col">Resource</th>{head}<th scope="col">Licence</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<div class="av-legend">{legend}<span class="av-note">Licence is the field GitHub detects; NOASSERTION means “not identified”, not “no licence”.</span></div>'''


def render_lineage(x):
    out = []
    for rel in x.d['review-relations']['relations']:
        t = rel['type']
        if t in ('companion_repository', 'evaluates', 'prior_survey', 'depends_on_unmerged', 'author_name_match') or rel.get('term') == 'survey':
            continue
        title = {'study_family': 'Study family', 'derived_from': 'Derived from', 'uses_resources_from': 'Uses resources from',
                 'contrasts_with': 'Compatible, not equivalent', 'name_collision': 'Same name, different thing', 'author_name_match': 'Author-name match'}[t]
        if t == 'name_collision':
            head = f'“{esc(rel["term"])}”'
        elif t in ('study_family', 'author_name_match'):
            head = ' · '.join(esc(x.subject_label(m)) for m in rel['members'])
        else:
            head = f'{esc(x.subject_label(rel["source"]))} → {esc(x.subject_label(rel["target"]))}'
        out.append(f'<article class="lineage lineage-{t}"><span class="aside-caption">{esc(title.upper())}</span><h3>{head}</h3><p>{esc(rel["note"])}</p></article>')
    return '\n'.join(out)


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
    topics = ''.join(f'<span class="topic">{esc(t["topics"][k]["label"])}</span>' for k in p['topics'][:3])
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
<span class="paper-year">{p["year"]}</span><div class="paper-main"><p class="paper-lead"><span class="tier-badge tier-{p["tier"]}">{tier_lab}</span>{esc(lead or "")}</p>
<a class="paper-title" href="{esc(p["url"])}" target="_blank" rel="noopener noreferrer">{esc(p["title"])}</a><p class="paper-authors">{esc(auth)}</p>
<p class="paper-blurb">{esc(blurb or "")}</p><div class="paper-meta"><span class="venue">{esc(venue)}</span><span>{esc(J.fmt_date(p["published_at"]))}</span>{"".join(chips)}{topics}</div></div>
<button class="evidence-btn" type="button" data-open-paper="{esc(p["id"])}" aria-label="Open evidence for {esc(p["title"])}">Evidence<svg class="icon"><use href="#i-arrow"/></svg></button></li>'''


def render_rows(x):
    order = {'core': 0, 'peripheral': 1, 'background': 2}
    ps = sorted(x.papers, key=lambda p: (order[p['tier']], p['published_at']), reverse=False)
    ps = sorted(ps, key=lambda p: (order[p['tier']], -int(p['published_at'][:10].replace('-', ''))))
    return '\n'.join(paper_row(x, p) for p in ps)


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


def render_method_blocks(x):
    repo = J.safe_url(x.cfg.get('repository_url'))
    branch = x.cfg.get('repository_default_branch') or 'main'
    doc = lambda path: f'{repo.rstrip("/")}/blob/{branch}/{path}' if repo else path
    prior = x.d['review-relations']['related_surveys'][0]
    further = x.d['review-relations']['related_surveys'][1:]
    further_html = ''.join(
        f'<p class="fine">Related survey: <a href="{esc(rs["url"])}" target="_blank" rel="noopener noreferrer"><em>{esc(rs["title"])}</em></a> (arXiv preprint, 2026) {esc(rs["description"])}</p>'
        for rs in further)
    files = [('data/references.bib', 'references.bib'), ('data/exports/papers.csv', 'papers.csv'), ('data/exports/claims.csv', 'claims.csv'),
             ('data/exports/repositories.csv', 'repositories.csv'), ('data/search-runs.json', 'search log (JSON)')]
    dl = ''.join(f'<li><a href="{f}"{" download" if not f.endswith(".json") else ""}>{esc(lab)}</a></li>' for f, lab in files)
    return f'''<article class="mblock"><h3>Scope &amp; sources</h3><ul class="check-list">
<li><b>Core studies</b> evaluate Jev, build a Jev-like typed decision model, or depend on one inside a system.</li>
<li><b>Background</b> references cover adjacent work: calibration, selective prediction, structured output, routing and judging.</li>
<li>Studies are found through arXiv and GitHub searches; queries and screening decisions are published with the data.</li></ul></article>
<article class="mblock"><h3>Reading the evidence</h3><ul class="check-list">
<li>Core studies are read in full; every evidence record points to the section or table it comes from.</li>
<li>Values are as reported by authors, the vendor or community repositories; they were not re-run.</li>
<li>Openness is recorded field by field — code, weights, data, predictions — because each can be released on its own.</li></ul>
<p class="fine">Details: <a href="{esc(doc("docs/methodology.md"))}">methodology</a> · <a href="{esc(doc("docs/limitations.md"))}">limitations</a></p></article>
<article class="mblock"><h3>Data</h3><ul class="dl-list">{dl}</ul>
<p class="fine">Related survey: <a href="{esc(prior["url"])}" target="_blank" rel="noopener noreferrer"><em>Decisions, Not Tokens</em></a> (working draft, 2026) covers machine-native decision models more broadly, from classical classifiers to Jev.</p>{further_html}</article>'''


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
        out.append(f'<a class="contrib-card" href="{esc(href)}"{ext}><strong>{esc(title)}</strong><span>{esc(desc)}</span><svg class="icon"><use href="#i-north"/></svg></a>')
    note = '' if repo else '<p class="fine">The public repository link appears here once the repository is published (set <code>repository_url</code> in <code>site.config.json</code>). Until then the cards open the contribution guide.</p>'
    return ''.join(out) + note


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
    write('repositories.csv', ['id', 'full_name', 'sets', 'type', 'method_family', 'commit', 'readme_url', 'license', 'audit_depth', 'selected_source_read',
                               'code', 'weights', 'training_code', 'inference_code', 'evaluation', 'data', 'raw_predictions', 'summary', 'boundary'],
          [[r['id'], r['full_name'], r['sets'], r['type'], r.get('method_family'), r.get('commit'), r.get('readme_url'), r.get('license'), r['audit_depth'],
            r['selected_source_read']] + [(r.get('availability') or {}).get(k) for k in ('code', 'weights', 'training_code', 'inference_code', 'evaluation', 'data', 'raw_predictions')]
           + [r.get('summary'), r.get('boundary')] for r in x.repos])


def export_bibtex(x):
    head = f'% Jev Survey bibliography — generated by scripts/build.py from data/papers.json. Do not edit.\n% {x.stats["papers"]} records: {x.stats["core"]} core, {x.stats["peripheral"]} peripheral, {x.stats["background"]} background. Cutoff {x.stats["cutoff"]}.\n\n'
    order = {'core': 0, 'peripheral': 1, 'background': 2}
    ps = sorted(x.papers, key=lambda p: (order[p['tier']], p['arxiv_id']))
    (ROOT / 'data' / 'references.bib').write_text(head + '\n'.join(J.bibtex_for_paper(p) for p in ps), encoding='utf-8')
    extra = [J.bibtex_for_source(s) for s in x.d['sources']['sources']]
    rs = x.d['review-relations']['related_surveys'][0]
    extra.append(f'@misc{{{rs["bibtex_key"]},\n  title = {{{{{J.bib_text(rs["title"])}}}}},\n  author = {{Anonymous Authors}},\n  year = {{2026}},\n  howpublished = {{Public working draft on GitHub, dated 21 September 2026}},\n  url = {{{rs["url"]}}},\n  note = {{Commit {rs["commit"][:10]}; accessed 2026-09-23}}\n}}\n')
    for rs in x.d['review-relations']['related_surveys'][1:]:
        extra.append(f'@misc{{{rs["bibtex_key"]},\n  title = {{{{{J.bib_text(rs["title"])}}}}},\n  author = {{{rs["authors"].replace(", ", " and ")}}},\n  year = {{2026}},\n  eprint = {{2609.30216}},\n  archivePrefix = {{arXiv}},\n  primaryClass = {{cs.SE}},\n  url = {{{rs["url"]}}}\n}}\n')
    for r in x.repos:
        if 'priority' in r['sets'] or r['type'] == 'guide':
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
    primary = [p for p in x.papers if p['tier'] in ('core', 'peripheral')]
    fam_counts = {f['id']: sum(1 for p in x.papers if f['id'] in p['method_families']) for f in t['method_families']}
    stage_counts = {st['id']: sum(1 for p in x.papers if st['id'] in p['stages']) for st in t['stages']}
    rel_counts = {r['id']: sum(1 for p in x.papers if r['id'] in p['model_relationship']) for r in t['model_relationships']}
    topic_counts = {tp['id']: sum(1 for p in x.papers if tp['id'] in p['topics']) for tp in t['topics']}
    values = {
        'core': s['core'], 'peripheral': s['peripheral'], 'background': s['background'], 'papers': s['papers'], 'claims': s['claims'],
        'priority_repos': s['priority_repos'], 'findings': s['findings'], 'core_plus': s['core_plus'],
        'cutoff_long': J.fmt_date(s['cutoff']), 'cutoff_upper': J.fmt_date(s['cutoff']).upper(),
        'canonical': f'<link rel="canonical" href="{esc(site_url)}">' if site_url else '',
        'og_image': esc((site_url.rstrip('/') + '/assets/og-image.png') if site_url else 'assets/og-image.png'),
        'repo_href': esc(repo) if repo else '#method', 'repo_label': 'Repository' if repo else 'Data &amp; code',
        'footer_credit': esc(cfg.get('footer_credit') or ''),
        'meta_author': ''.join(f'<meta name="author" content="{esc(a["name"])}">' for a in authors[:1]),
        'byline': ('By ' + ', '.join(f'<a href="{esc(J.safe_url(a.get("url")) or "#top")}" rel="author">{esc(a["name"])}</a>' for a in authors)) if authors else '',
        'md_href': esc(repo_file('blob', 'paper/survey.md')), 'data_href': esc(repo_file('tree', 'data')),
        'license_href': esc(repo_file('blob', 'LICENSE')), 'content_license_href': esc(repo_file('blob', 'LICENSE-CONTENT.md')),
        'hero_art': (ROOT / 'site' / 'hero.svg').read_text(encoding='utf-8'),
        'primitives': render_primitives(x), 'vendor_claims': render_vendor_claims(x), 'stages': render_stages(x), 'timeline': render_timeline(x),
        'atlas': render_atlas(x), 'findings_list': render_findings(x), 'evidence_matrix': render_matrix(x), 'scope_groups': render_scope_groups(x),
        'lab_panels': render_lab(x), 'failure_modes': render_failure_modes(x), 'control_explorer': render_control_explorer(x),
        'applications': render_applications(x), 'openness': render_openness(x), 'lineage': render_lineage(x), 
        'tier_buttons': render_tier_buttons(x), 'stage_options': options(t['stages'], stage_counts), 'family_options': options(t['method_families'], fam_counts),
        'rel_options': options(t['model_relationships'], rel_counts), 'topic_options': options(t['topics'], topic_counts),
        'year_options': ''.join(f'<option value="{y}">{y}</option>' for y in s['years']),
        'paper_rows': render_rows(x), 'method_blocks': render_method_blocks(x), 'contribute': render_contribute(x),
    }
    css_js = b''.join((ROOT / 'site' / f).read_bytes() for f in ('site.css', 'site.js', 'lab.js', 'catalog.mjs') if (ROOT / 'site' / f).exists())
    values['build_hash'] = hashlib.sha256(css_js).hexdigest()[:10]

    def sub(m):
        key = m.group(1)
        if key not in values:
            raise KeyError(f'template placeholder {{{{{key}}}}} has no value')
        return str(values[key])
    html = re.sub(r'\{\{([a-z_]+)\}\}', sub, tpl)
    return html


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
               'docs/methodology.md', 'docs/evidence-audit.md', 'paper/survey.md', 'paper/main.tex']
    before = {t: (ROOT / t).read_bytes() if (ROOT / t).exists() else None for t in targets}
    (ROOT / 'site' / 'data').mkdir(parents=True, exist_ok=True)
    (ROOT / 'site' / 'data' / 'drawer.json').write_text(json.dumps(drawer_data(x), ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    export_bibtex(x)
    export_csvs(x)
    html = render_page(x)
    (ROOT / 'index.html').write_text(html, encoding='utf-8')
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'render-readme.py')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'render-docs.py')], check=True)
    # manuscript text and tables (figures and PDF need matplotlib/LaTeX: scripts/build-paper.py [--pdf])
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build-paper.py'), '--no-figures'], check=True, capture_output=True)
    changed = [t for t in targets if before[t] != ((ROOT / t).read_bytes() if (ROOT / t).exists() else None)]
    size = lambda p: (ROOT / p).stat().st_size
    print(f'built index.html {size("index.html") / 1024:.1f} KB · drawer.json {size("site/data/drawer.json") / 1024:.1f} KB · '
          f'{x.stats["papers"]} records, {x.stats["claims"]} claims, {x.stats["repos_unique"]} repositories')
    if args.check and changed:
        print('generated files out of date:', ', '.join(changed))
        sys.exit(1)


if __name__ == '__main__':
    main()
