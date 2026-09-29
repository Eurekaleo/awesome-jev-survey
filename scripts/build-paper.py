#!/usr/bin/env python3
"""Build the survey manuscript from paper/src/survey.src.md and data/*.json.

    python3 scripts/build-paper.py          # survey.md, main.tex, sections/*.tex, figures
    python3 scripts/build-paper.py --pdf    # also compile paper/main.pdf with latexmk (XeLaTeX)

Source syntax: Markdown headings (#, ##, ###), paragraphs, "- " and "1. " lists,
**bold**, *italic*, `code`, [text](url), citations [@key; @key2], data
placeholders {{stat:name}}, {{table:name}}, {{figure:name}}, {{references}}, and
::: meta / ::: abstract blocks. Tables and figures are generated from the data,
so the manuscript cannot drift from the website.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jevlib as J  # noqa: E402

ROOT = J.ROOT
PAPER = ROOT / 'paper'
SRC = PAPER / 'src' / 'survey.src.md'
VERSION = J.VERSION


# ---------------------------------------------------------------- data context
def context():
    d = J.load_all()
    s = J.compute_stats(d)
    P = d['papers']['papers']
    core = [p for p in P if p['tier'] == 'core']
    prim = [p for p in P if p['tier'] in ('core', 'peripheral')]
    hosted = re.compile(r'^(Jev$|Jev \(|TypeSafe)')  # hosted Jev only; excludes JevLite, Visual Jev and other open models
    jev_q = [p for p in prim if any(hosted.match(v['model']) for v in p['model_versions'])]
    jev_v = [p for p in jev_q if any(hosted.match(v['model']) and v.get('version') for v in p['model_versions'])]
    oc = Counter(p['openness']['code']['status'] for p in prim)
    extra = dict(
        version=VERSION, cutoff_long=J.fmt_date(s['cutoff']),
        rel_commercial_core=sum('commercial_jev' in p['model_relationship'] for p in core),
        rel_independent_core=sum('independent_jev_like' in p['model_relationship'] for p in core),
        rel_downstream_core=sum('downstream_system' in p['model_relationship'] for p in core),
        jev_querying=len(jev_q), jev_versioned=len(jev_v), jev_unversioned=len(jev_q) - len(jev_v),
        open_code_available=oc['available'], open_code_partial=oc['partial'] + oc['project_page_only'], open_code_claimed=oc['claimed_not_located'],
        open_weights_available=sum(p['openness']['weights']['status'] == 'available' for p in prim),
        open_predictions_available=sum(p['openness']['predictions']['status'] == 'available' for p in prim))
    s.update(extra)
    keys = {}
    for p in P:
        keys[p['bibtex_key']] = ('paper', p)
    for src in d['sources']['sources']:
        keys[src['bibtex_key']] = ('source', src)
    for r in d['repositories']['repositories']:
        keys[J.repo_key(r['id'])] = ('repo', r)
    for rs in d['review-relations']['related_surveys']:
        keys[rs['bibtex_key']] = ('survey', rs)
    return d, s, keys


# ---------------------------------------------------------------- tables (as rows) and figures
AV_WORD = {'available': 'available', 'partial': 'partial', 'restricted': 'restricted', 'project_page_only': 'page only',
           'claimed_not_located': 'claimed', 'not_located': 'not located', 'not_applicable': 'n/a', 'not_assessed': '·', 'not_attempted': 'no', 'no': 'no'}


def tables(d, s):
    tax = d['taxonomy']
    T = {k: J.by_id(tax[k]) for k in ('test_levels', 'method_families', 'stages', 'measurement_scopes')}
    P = d['papers']['papers']
    C = J.by_id(d['claims']['claims'])
    prim = sorted([p for p in P if p['tier'] in ('core', 'peripheral')], key=lambda p: p['published_at'])
    out = {}
    out['stages'] = dict(caption='Five places a claim can live: an analytical organisation, not a model architecture.',
                         head=['Stage', 'Question', 'Evidence to look for', 'Studies'], widths='p{0.16\\linewidth}XXr',
                         rows=[[st['label'], st['question'], st['evidence'], str(s['stages'][st['id']])] for st in tax['stages']])
    out['families'] = dict(caption='Six readout families. Compatible interfaces do not imply equivalent mechanisms.',
                           head=['Family', 'Principle', 'Public', 'Boundary'], widths='p{0.15\\linewidth}XXX',
                           rows=[[f['label'], f['principle'], f['disclosed'], f['boundary']] for f in tax['method_families']])
    rows = []
    for p in prim:
        head = C[p['claims'][0]]['headline'] if p['claims'] else ''
        mv = '; '.join(v.get('version') or 'not reported' for v in p['model_versions'] if re.match(r'(Jev|TypeSafe|JevLite|Visual|this|werr)', v['model']))
        rows.append([p['short_title'] + (' *' if p['tier'] == 'peripheral' else '') + (' †' if p.get('study_family_id') else ''), p['published_at'][5:10],
                     T['test_levels'][p['test_level']]['label'], mv or '—', head, f"@{p['bibtex_key']}"])
    out['core'] = dict(caption='Core and peripheral studies (* peripheral; † same study family). Headline values are author-reported.',
                       head=['Study', 'v1 (2026)', 'Test level', 'Version', 'Headline (as reported)', 'Ref.'], widths='p{0.2\\linewidth}p{0.055\\linewidth}p{0.085\\linewidth}p{0.11\\linewidth}Xp{0.075\\linewidth}', rows=rows)
    rows = []
    for scope in ('single_request', 'amortized_question', 'end_to_end', 'simulation', 'author_estimate', 'vendor_claim'):
        for c in [c for c in d['claims']['claims'] if c['measurement_scope'] == scope]:
            ref = f"@{next(p['bibtex_key'] for p in P if p['id'] == c['subject'])}" if c['subject'].startswith('arxiv:') else (
                f"@{next(x['bibtex_key'] for x in d['sources']['sources'] if x['id'] == c['subject'])}" if c['subject'].startswith('src:') else '')
            rows.append([T['measurement_scopes'][scope]['label'], c['headline'], ref])
    out['scopes'] = dict(caption='Speed and cost figures grouped by measurement scope. Figures in different groups are not comparable.',
                         head=['Scope', 'Reported figure', 'Ref.'], widths='p{0.2\\linewidth}Xp{0.1\\linewidth}', rows=rows)
    rows = []
    for fm in tax['failure_modes']:
        refs = []
        for cid in fm['claims']:
            sub = C[cid]['subject']
            if sub.startswith('arxiv:'):
                refs.append('@' + next(p['bibtex_key'] for p in P if p['id'] == sub))
            elif sub.startswith('gh:'):
                refs.append('@' + J.repo_key(sub))
            else:
                refs.append('@' + next(x['bibtex_key'] for x in d['sources']['sources'] if x['id'] == sub))
        rows.append([fm['title'], T['stages'][fm['stage']]['label'], fm['mitigation'], ' '.join(dict.fromkeys(refs))])
    out['failures'] = dict(caption='Failure modes reported in the first wave, with mitigations (not yet evaluated at scale).',
                           head=['Failure mode', 'Stage', 'Mitigation', 'Sources'], widths='p{0.2\\linewidth}p{0.12\\linewidth}Xp{0.16\\linewidth}', rows=rows)
    out['openness'] = dict(caption='Openness of core and peripheral studies: six separate fields ("not located" is not "absent").',
                           head=['Study', 'Code', 'Weights', 'Data', 'Predictions', 'Recomputable', 'Reproduced'], widths='Xllllll',
                           rows=[[p['short_title']] + [AV_WORD[p['openness'][k]['status']] for k in ('code', 'weights', 'data', 'predictions', 'recomputable', 'reproduction')] for p in prim])
    runs = J.by_id(d['search-runs']['runs'])
    q = [[x['id'], x['query'], str(x['total'])] for x in runs['snapshot-arxiv-2026-09-23']['queries']]
    q += [[x['id'], x['query'], (str(x['total']) + (' (rejected)' if x['status'] != 'ok' else ''))] for x in runs['increment-arxiv-2026-09-23T0818Z']['expansion']]
    out['queries'] = dict(caption='arXiv API queries: 17 accepted snapshot queries (Q) and the increment’s expansion queries (X).',
                          head=['ID', 'Query', 'Hits'], widths='>{\\raggedright\\arraybackslash}p{0.27\\linewidth}Xp{0.1\\linewidth}', rows=q, long=True, code_cols=[0, 1])
    return out


FIGS = {
    'timeline': 'Submission dates of the core and peripheral studies (arXiv v1) against context events, by primary relationship to Jev.',
    'matrix': 'Which study bears on which finding, derived from the evidence records (filled: supports; half: qualifies).',
    'openness': 'Openness of the core and peripheral studies across six independent fields.',
}


def figures(d):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 8.5, 'axes.edgecolor': '#c3cacc', 'axes.linewidth': 0.8,
                         'xtick.color': '#55636c', 'ytick.color': '#16222b', 'svg.fonttype': 'none', 'pdf.fonttype': 42})
    out = PAPER / 'figures'
    out.mkdir(parents=True, exist_ok=True)
    P = d['papers']['papers']
    prim = sorted([p for p in P if p['tier'] in ('core', 'peripheral')], key=lambda p: p['published_at'])
    C = J.by_id(d['claims']['claims'])
    col = {'commercial_jev': '#2a78d6', 'independent_jev_like': '#eb6834', 'downstream_system': '#1baf7a'}
    lab = {'commercial_jev': 'Evaluates hosted Jev', 'independent_jev_like': 'Independent Jev-like model', 'downstream_system': 'Uses Jev inside a system'}

    # Figure 1: timeline as a dot plot, one row per study (no label collisions)
    cut = int(d['papers']['meta']['cutoff'][8:10])  # data cutoff (September)
    fig, ax = plt.subplots(figsize=(7.2, 1.4 + 0.155 * len(prim)))
    days = list(range(15, cut + 1))
    for i, p in enumerate(prim):
        rel = p['model_relationship'][0]
        dday = int(p['published_at'][8:10]) + int(p['published_at'][11:13]) / 24
        ax.plot([15, dday], [i, i], color='#eef3f3', linewidth=1, zorder=1)
        ax.scatter([dday], [i], s=48, color='white' if p['tier'] == 'peripheral' else col[rel], edgecolors=col[rel], linewidths=1.6, zorder=3)
    for dday, text in ((15, 'Jev launch'), (17, 'Vendor limits page\nlast reviewed'), (21, 'Prior survey\ndraft dated'), (cut, 'Cutoff')):
        ax.axvline(dday, color='#8b979e', linewidth=0.9, zorder=2)
        ax.text(dday, -1.1, text, ha='center', va='bottom', fontsize=6.4, color='#55636c')
    ax.set_ylim(len(prim) - 0.4, -2.4)
    ax.set_xticks(days)
    ax.set_xticklabels([f'{x} Sep' for x in days])
    ax.set_yticks(range(len(prim)))
    ax.set_yticklabels([p['short_title'] for p in prim], fontsize=7)
    for side in ('right', 'top'):
        ax.spines[side].set_visible(False)
    ax.grid(axis='x', color='#e1e6e7', linewidth=0.6)
    ax.set_axisbelow(True)
    handles = [Line2D([0], [0], marker='o', color='none', markerfacecolor=col[k], markeredgecolor=col[k], markersize=6, label=lab[k]) for k in col]
    handles.append(Line2D([0], [0], marker='o', color='none', markerfacecolor='white', markeredgecolor='#55636c', markersize=6, label='Peripheral (open marker)'))
    ax.set_xlim(14.4, cut + 0.6)
    ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(0.4, 1.17), frameon=False, fontsize=7, ncol=2, handletextpad=0.3, columnspacing=1.4)
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(out / f'timeline.{ext}', dpi=200)
    plt.close(fig)

    # Figure 2: study x finding matrix
    F = [f['id'] for f in d['taxonomy']['findings']]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for i, p in enumerate(prim):
        for j, fid in enumerate(F):
            rels = {l['relation'] for cid in p['claims'] for l in C[cid]['findings'] if l['id'] == fid}
            if 'supports' in rels:
                ax.scatter(j, i, s=70, color='#1d716c', zorder=3)
            elif rels:
                ax.scatter(j, i, s=70, color='#a8521a', marker='o', zorder=3, facecolors='none', linewidths=1.6)
    ax.set_xticks(range(len(F)))
    import textwrap
    ax.set_xticklabels([f['id'] + '\n' + '\n'.join(textwrap.wrap(f['short'], 12)) for f in d['taxonomy']['findings']], fontsize=6.4)
    ax.set_yticks(range(len(prim)))
    ax.set_yticklabels([p['short_title'] for p in prim], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlim(-0.6, len(F) - 0.4)
    for side in ('right', 'top'):
        ax.spines[side].set_visible(False)
    ax.grid(color='#e1e6e7', linewidth=0.8)
    ax.set_axisbelow(True)
    handles = [Line2D([0], [0], marker='o', color='none', markerfacecolor='#1d716c', markeredgecolor='#1d716c', markersize=7, label='supports'),
               Line2D([0], [0], marker='o', color='none', markerfacecolor='none', markeredgecolor='#a8521a', markeredgewidth=1.6, markersize=7, label='qualifies only')]
    ax.legend(handles=handles, loc='lower center', bbox_to_anchor=(0.5, 1.0), frameon=False, ncol=2, fontsize=7)
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(out / f'matrix.{ext}', dpi=200)
    plt.close(fig)

    # Figure 3: openness grid
    fields = [('code', 'Code'), ('weights', 'Weights'), ('data', 'Data'), ('predictions', 'Raw preds.'), ('recomputable', 'Recomputable'), ('reproduction', 'Reproduced')]
    style = {'available': ('o', '#1d716c', True), 'partial': ('o', '#1d716c', 'half'), 'project_page_only': ('o', '#1d716c', 'half'),
             'restricted': ('s', '#9a7424', True), 'claimed_not_located': ('o', '#a8521a', False), 'not_located': ('o', '#8b979e', False),
             'not_applicable': ('_', '#a8b3b8', True), 'not_attempted': ('_', '#a8b3b8', True), 'no': ('o', '#8b979e', False), 'not_assessed': ('_', '#a8b3b8', True)}
    fig, ax = plt.subplots(figsize=(7.2, 4.3))
    for i, p in enumerate(prim):
        for j, (k, _) in enumerate(fields):
            m, c, fill = style[p['openness'][k]['status']]
            if fill == 'half':
                ax.plot(j, i, marker='o', markersize=8, color=c, fillstyle='left', markerfacecoloralt='white', markeredgewidth=1.4, linestyle='none')
            else:
                ax.plot(j, i, marker=m, markersize=8, color=c, markerfacecolor=c if fill else 'none', markeredgewidth=1.6, linestyle='none')
    ax.set_xticks(range(len(fields)))
    ax.set_xticklabels([f for _, f in fields], fontsize=7.2)
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(prim)))
    ax.set_yticklabels([p['short_title'] for p in prim], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlim(-0.6, len(fields) - 0.4)
    for side in ('right', 'bottom'):
        ax.spines[side].set_visible(False)
    ax.grid(color='#e1e6e7', linewidth=0.8)
    ax.set_axisbelow(True)
    handles = [Line2D([0], [0], marker='o', color='#1d716c', linestyle='none', label='available'),
               Line2D([0], [0], marker='o', color='#1d716c', fillstyle='left', markerfacecoloralt='white', linestyle='none', label='partial / page only'),
               Line2D([0], [0], marker='s', color='#9a7424', linestyle='none', label='restricted'),
               Line2D([0], [0], marker='o', color='#a8521a', markerfacecolor='none', linestyle='none', label='claimed, not located'),
               Line2D([0], [0], marker='o', color='#8b979e', markerfacecolor='none', linestyle='none', label='not located / no'),
               Line2D([0], [0], marker='_', color='#a8b3b8', linestyle='none', label='n/a / not attempted')]
    ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(0.5, -0.02), frameon=False, ncol=3, fontsize=6.8)
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(out / f'openness.{ext}', dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------- inline rendering
INLINE = re.compile(r'(`[^`]+`)|(\[@[^\]]+\])|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)')


class Cites:
    def __init__(self, keys):
        self.keys = keys
        self.order = []

    def num(self, k):
        if k not in self.keys:
            raise KeyError(f'unknown citation key {k}')
        if k not in self.order:
            self.order.append(k)
        return self.order.index(k) + 1


def split_keys(tok):
    return [k.strip().lstrip('@') for k in tok[1:-1].split(';')]


def md_inline(text, cites):
    def rep(m):
        t = m.group(0)
        if t.startswith('[@'):
            ks = split_keys(t)
            return '[' + ', '.join(f'[{cites.num(k)}](#ref-{cites.num(k)})' for k in ks) + ']'
        return t
    return INLINE.sub(rep, text)


TEX_ESC = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}


def smart_quotes(s):
    s = re.sub(r'(^|[\s(\[{—–/])"', r'\1“', s)
    return s.replace('"', '”')


def tex_escape(s):
    return ''.join(TEX_ESC.get(ch, ch) for ch in smart_quotes(s))


def tex_inline(text):
    out, pos = [], 0
    for m in INLINE.finditer(text):
        out.append(tex_escape(text[pos:m.start()]))
        t = m.group(0)
        if t.startswith('`'):
            out.append(r'\texttt{' + tex_escape(t[1:-1]) + '}')
        elif t.startswith('[@'):
            out.append(r'\citep{' + ','.join(split_keys(t)) + '}')
        elif t.startswith('['):
            lt, url = re.match(r'\[([^\]]+)\]\(([^)]+)\)', t).groups()
            out.append(r'\href{' + url.replace('%', r'\%').replace('#', r'\#') + '}{' + tex_inline(lt) + '}')
        elif t.startswith('**'):
            out.append(r'\textbf{' + tex_inline(t[2:-2]) + '}')
        else:
            out.append(r'\emph{' + tex_inline(t[1:-1]) + '}')
        pos = m.end()
    out.append(tex_escape(text[pos:]))
    return ''.join(out)


# ---------------------------------------------------------------- block parser
def blocks(src):
    """Yield (kind, payload) blocks from the source."""
    lines = src.split('\n')
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.startswith(':::'):
            kind = ln[3:].strip()
            j = i + 1
            body = []
            while not lines[j].startswith(':::'):
                body.append(lines[j])
                j += 1
            yield (kind, ' '.join(b.strip() for b in body if b.strip()))
            i = j + 1
            continue
        m = re.match(r'^(#{1,3}) (.*)$', ln)
        if m:
            yield ('h' + str(len(m.group(1))), m.group(2).strip())
            i += 1
            continue
        m = re.match(r'^\{\{(table|figure):([a-z_]+)\}\}$', ln.strip()) or re.match(r'^\{\{(references)\}\}$', ln.strip())
        if m:
            yield (m.group(1), m.group(2) if m.lastindex and m.lastindex > 1 else None)
            i += 1
            continue
        if re.match(r'^(- |\d+\. )', ln):
            ordered = bool(re.match(r'^\d+\. ', ln))
            items = []
            while i < len(lines) and re.match(r'^(- |\d+\. )', lines[i]):
                items.append(re.sub(r'^(- |\d+\. )', '', lines[i]).strip())
                i += 1
            yield ('ol' if ordered else 'ul', items)
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r'^(#{1,3} |:::|\{\{|- |\d+\. )', lines[i]):
            para.append(lines[i].strip())
            i += 1
        yield ('p', ' '.join(para))


def subst_stats(text, s):
    def rep(m):
        v = s[m.group(1)]
        return f'{v:,}' if isinstance(v, int) else str(v)
    return re.sub(r'\{\{stat:([a-z_]+)\}\}', rep, text)


def cell_md(v, cites):
    v = str(v)
    if v.startswith('@'):
        return ', '.join(f'[[{cites.num(k)}](#ref-{cites.num(k)})]' for k in v.replace('@', '').split())
    return md_inline(v, cites).replace('|', '\\|')


def cell_tex(v, code=False):
    v = str(v)
    if v.startswith('@'):
        return r'\citep{' + ','.join(v.replace('@', '').split()) + '}'
    return r'\texttt{\footnotesize ' + tex_escape(v) + '}' if code else tex_inline(v)


def ref_md(n, key, kind, obj):
    if kind == 'paper':
        au = obj['authors']
        who = ', '.join(au[:6]) + (', et al.' if len(au) > 6 else '')
        venue = f" {obj['venue']} (per arXiv {'journal reference' if obj['venue_source'] == 'arxiv_journal_ref' else 'comment'})." if obj['venue_source'] in ('arxiv_comment', 'arxiv_journal_ref') else ''
        return f'{who} ({obj["year"]}). *{obj["title"]}*. arXiv:{obj["versioned_id"]}.{venue} <{obj["url"]}>'
    if kind == 'source':
        return f'{obj.get("author") or obj["publisher"]} ({(obj.get("date") or obj["accessed"])[:4]}). *{obj["title"]}*. {obj["publisher"]}. <{obj["url"]}> (accessed {obj["accessed"]}).'
    if kind == 'repo':
        return f'{obj["full_name"]}. GitHub repository, commit `{(obj.get("commit") or "")[:10]}`. <{obj.get("readme_url") or obj["url"]}> (accessed {(obj.get("accessed_at") or "")[:10]}).'
    return f'Anonymous Authors (2026). *{obj["title"]}*. Public working draft, dated 21 September 2026. <{obj["url"]}>.'


# ---------------------------------------------------------------- renderers
def render(args):
    d, s, keys = context()
    src = subst_stats(SRC.read_text(encoding='utf-8'), s)
    authors = [a for a in d['config'].get('authors') or [] if a.get('name')]
    src = src.replace('{{authors}}', ', '.join(f'[{a["name"]}]({a["url"]})' if J.safe_url(a.get('url')) else a['name'] for a in authors)
                      or 'Author list pending confirmation')
    tabs = tables(d, s)
    if not args.no_figures:
        figures(d)
    cites = Cites(keys)
    md, sections = [], []
    title = meta = abstract = ''
    cur = None
    tcount = fcount = 0
    tex_sections = []
    for kind, payload in blocks(src):
        if kind == 'h1':
            title = payload
            md.append(f'# {payload}\n')
            continue
        if kind == 'meta':
            meta = payload
            md.append(f'*{payload}*\n')
            continue
        if kind == 'abstract':
            abstract = payload
            md.append(f'> **Abstract.** {md_inline(payload, cites)}\n')
            continue
        if kind == 'h2':
            name = payload
            if name == 'References':
                md.append('## References\n')
                cur = None
                continue
            slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
            cur = dict(name=name, slug=slug, appendix=name.startswith('Appendix'), tex=[])
            tex_sections.append(cur)
            md.append(f'## {name}\n')
            head = name.split('·', 1)[-1].strip() if cur['appendix'] else name
            cur['tex'].append(('\\section{' + tex_inline(head) + '}\\label{sec:' + slug + '}'))
            continue
        if kind == 'h3':
            md.append(f'### {payload}\n')
            cur['tex'].append('\\subsection{' + tex_inline(payload) + '}')
            continue
        if kind == 'p':
            md.append(md_inline(payload, cites) + '\n')
            cur['tex'].append(tex_inline(payload))
            continue
        if kind in ('ul', 'ol'):
            md.append('\n'.join((f'{i + 1}. ' if kind == 'ol' else '- ') + md_inline(it, cites) for i, it in enumerate(payload)) + '\n')
            env = 'enumerate' if kind == 'ol' else 'itemize'
            cur['tex'].append(f'\\begin{{{env}}}[leftmargin=*,itemsep=2pt]\n' + '\n'.join('  \\item ' + tex_inline(it) for it in payload) + f'\n\\end{{{env}}}')
            continue
        if kind == 'table':
            t = tabs[payload]
            tcount += 1
            md.append(f'**Table {tcount}.** {t["caption"]}\n')
            md.append('| ' + ' | '.join(t['head']) + ' |\n| ' + ' | '.join('---' for _ in t['head']) + ' |\n' +
                      '\n'.join('| ' + ' | '.join(cell_md(c, cites) for c in r) + ' |' for r in t['rows']) + '\n')
            code_cols = set(t.get('code_cols', []))
            body = '\n'.join(' & '.join(cell_tex(c, i in code_cols) for i, c in enumerate(r)) + r' \\' for r in t['rows'])
            head = ' & '.join(r'\textbf{' + tex_escape(h) + '}' for h in t['head']) + r' \\'
            if t.get('long'):
                cur['tex'].append('{\\footnotesize\n\\begin{xltabular}{\\linewidth}{' + t['widths'] + '}\n\\caption{' + tex_inline(t['caption']) + '}\\label{tab:' + payload + '}\\\\\n\\toprule\n' + head +
                                  '\n\\midrule\n\\endfirsthead\n\\toprule\n' + head + '\n\\midrule\n\\endhead\n' + body + '\n\\bottomrule\n\\end{xltabular}}')
            else:
                cur['tex'].append('\\begin{table}[tbp]\n\\centering\\footnotesize\n\\caption{' + tex_inline(t['caption']) + '}\\label{tab:' + payload + '}\n\\begin{tabularx}{\\linewidth}{' +
                                  t['widths'] + '}\n\\toprule\n' + head + '\n\\midrule\n' + body + '\n\\bottomrule\n\\end{tabularx}\n\\end{table}')
            continue
        if kind == 'figure':
            fcount += 1
            md.append(f'![Figure {fcount}: {FIGS[payload]}](figures/{payload}.png)\n\n**Figure {fcount}.** {FIGS[payload]}\n')
            cur['tex'].append('\\begin{figure}[tbp]\n\\centering\n\\includegraphics[width=\\linewidth]{figures/' + payload + '.pdf}\n\\caption{' + tex_inline(FIGS[payload]) + '}\\label{fig:' + payload + '}\n\\end{figure}')
            continue
        if kind == 'references':
            refs_at = len(md)
            md.append('{{REFS}}')
            continue
    # reference list for the Markdown version (numbered by first citation)
    ref_lines = [f'{i + 1}. <a id="ref-{i + 1}"></a>{ref_md(i + 1, k, *keys[k])}' for i, k in enumerate(cites.order)]
    md = [x if x != '{{REFS}}' else '\n'.join(ref_lines) + '\n' for x in md]
    header = ('<!-- Generated by scripts/build-paper.py from paper/src/survey.src.md and data/*.json. Edit the source, then rebuild. -->\n\n')
    (PAPER / 'survey.md').write_text(header + '\n'.join(md), encoding='utf-8')

    # LaTeX
    secdir = PAPER / 'sections'
    secdir.mkdir(exist_ok=True)
    for f in secdir.glob('*.tex'):
        f.unlink()
    inputs, app_inputs = [], []
    for i, sec in enumerate(tex_sections, 1):
        fname = f'{i:02d}-{sec["slug"]}.tex'
        (secdir / fname).write_text('% Generated by scripts/build-paper.py — edit paper/src/survey.src.md\n' + '\n\n'.join(sec['tex']) + '\n', encoding='utf-8')
        (app_inputs if sec['appendix'] else inputs).append(f'\\input{{sections/{fname[:-4]}}}')
    head, _, rest = meta.partition(' · ')  # author line on its own, then version and cutoff
    meta_tex = (r'\large ' + tex_inline(head) + r'\\[5pt]\normalsize ' + tex_inline(rest)) if rest else tex_inline(meta)
    main = MAIN_TEX.replace('@@TITLE@@', tex_inline(title)).replace('@@META@@', meta_tex).replace('@@ABSTRACT@@', tex_inline(abstract)) \
        .replace('@@INPUTS@@', '\n'.join(inputs)).replace('@@APPENDIX@@', '\n'.join(app_inputs))
    (PAPER / 'main.tex').write_text(main, encoding='utf-8')
    print(f'survey.md and main.tex written: {len(tex_sections)} sections, {tcount} tables, {fcount} figures, {len(cites.order)} cited references')
    return cites


MAIN_TEX = r'''% Generated by scripts/build-paper.py. Compile with: latexmk -xelatex main.tex
\documentclass[10.5pt,a4paper]{article}
\usepackage[margin=2.2cm]{geometry}
\usepackage{fontspec}
\setmainfont{texgyrepagella}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\setsansfont{FiraSans}[Extension=.otf,UprightFont=*-Regular,BoldFont=*-SemiBold,ItalicFont=*-Italic]
\setmonofont{DejaVuSansMono}[Extension=.ttf,Scale=0.86,BoldFont=*-Bold]
\usepackage{microtype}
\usepackage{booktabs,tabularx,xltabular,array}
\usepackage{graphicx}
\usepackage[numbers,sort&compress]{natbib}
\usepackage{xcolor}
\usepackage[font=small,labelfont=bf]{caption}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\hypersetup{colorlinks=true,linkcolor={teal!60!black},citecolor={teal!60!black},urlcolor={teal!60!black}}
\renewcommand{\arraystretch}{1.18}
\setlength{\tabcolsep}{4pt}
\newcolumntype{X}{>{\raggedright\arraybackslash}X}
\setcounter{topnumber}{3}\renewcommand{\topfraction}{0.9}\renewcommand{\textfraction}{0.08}\renewcommand{\floatpagefraction}{0.8}
\title{\vspace{-1.2em}@@TITLE@@}
\author{@@META@@}
\date{}
\begin{document}
\maketitle
\begin{abstract}\noindent
@@ABSTRACT@@
\end{abstract}

@@INPUTS@@

\bibliographystyle{unsrtnat}
\bibliography{references}

\appendix
@@APPENDIX@@
\end{document}
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', action='store_true', help='compile paper/main.pdf with latexmk -xelatex')
    ap.add_argument('--no-figures', action='store_true')
    args = ap.parse_args()
    render(args)
    if args.pdf:
        r = subprocess.run(['latexmk', '-xelatex', '-interaction=nonstopmode', '-halt-on-error', 'main.tex'], cwd=PAPER, capture_output=True, text=True)
        log = (PAPER / 'main.log').read_text(encoding='utf-8', errors='replace') if (PAPER / 'main.log').exists() else ''
        undefined = sorted(set(re.findall(r"(?:Citation|Reference) `([^']+)' on page", log)))
        print('latexmk exit', r.returncode, '| undefined refs/cites:', undefined or 'none',
              '| overfull boxes:', len(re.findall(r'Overfull \\hbox', log)))
        if r.returncode:
            print(r.stdout[-3000:])
            sys.exit(1)


if __name__ == '__main__':
    main()
