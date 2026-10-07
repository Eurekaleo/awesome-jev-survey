#!/usr/bin/env python3
"""Render README.md from data/*.json (called by scripts/build.py).

README.md is generated: edit data/*.json or this template, then rebuild.
"""
import pathlib
import html
import re
import sys
from collections import defaultdict
from urllib.parse import quote

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jevlib as J  # noqa: E402

AVAIL_MD = {'available': '●', 'partial': '◐', 'restricted': '◑', 'project_page_only': '◔', 'claimed_not_located': '○ claimed',
            'not_located': '○', 'not_applicable': '–', 'not_assessed': '·', 'not_attempted': '·', 'no': '○'}
GROUPS = [('commercial_jev', 'Studies of hosted Jev'), ('independent_jev_like', 'Open and independent Jev-like models'),
          ('downstream_system', 'Systems built on typed decisions')]
# README artwork lives in assets/readme/ (static SVGs); each card links to the section heading of the same name.
ICON = {'About the survey': 'about', 'Repository guide': 'guide', 'Findings': 'findings', 'Studies of hosted Jev': 'hosted',
        'Open and independent Jev-like models': 'open', 'Systems built on typed decisions': 'systems', 'Peripheral study': 'peripheral',
        'Open ecosystem': 'ecosystem', 'Background references': 'background', 'Related reviews and catalogues': 'related',
        'Method': 'method', 'Contributing': 'contribute', 'Contributors': 'contributors', 'Star history': 'star', 'Citation and licence': 'cite'}
CARDS = [('findings', 'Findings', 'Cross-study syntheses'), ('hosted', 'Studies of hosted Jev', 'Studies of TypeSafe’s model'),
         ('open', 'Open and independent Jev-like models', 'Same shape, other mechanisms'),
         ('systems', 'Systems built on typed decisions', 'Built on typed decisions'),
         ('ecosystem', 'Open ecosystem', 'Models, benchmarks and tools'), ('background', 'Background references', 'Calibration, deferral, routing')]
ISSUE_FORMS = [('add-paper.yml', 'Suggest a study'), ('correction.yml', 'Correct a record'), ('reproduction.yml', 'Report a reproduction'),
               ('resource-update.yml', 'Update code, weights or availability')]


LICENCE = {'NOASSERTION': 'Other'}  # GitHub's API value for a licence file it cannot classify; GitHub itself shows "Other"
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
CODE_LINK = {'available': 'code', 'partial': 'code, partial', 'project_page_only': 'project page', 'restricted': 'code, restricted',
             'claimed_not_located': 'code claimed, not found'}


def md(s):
    return (s or '').replace('|', '\\|').replace('\n', ' ')


def code_link(code):
    """Link pill after a study's name, e.g. ` [[code](url)]`; nothing when no code was located."""
    label = CODE_LINK.get(code['status'])
    if not label:
        return ''
    if code.get('url') and code['status'] != 'claimed_not_located':
        return f' [[{label}]({code["url"]})]'
    return f' <sub>({label})</sub>'


def releases():
    """(version, date, summary) per CHANGELOG.md release, newest first; the summary is the first bullet up to its colon."""
    out = []
    for block in re.split(r'^## ', (J.ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'), flags=re.M)[1:]:
        head, _, body = block.partition('\n')
        m = re.match(r'(\S+)\s+—\s+(\d{4}-\d{2}-\d{2})', head)
        first = next((ln[2:] for ln in body.splitlines() if ln.startswith('- ')), body.strip().split('\n')[0])
        if m and first:
            summary = first.split(':')[0].rstrip('.').strip()
            out.append((m.group(1), m.group(2), summary + '.'))
    return out


def anchor(h):
    return ''.join(c for c in h.lower().replace(' ', '-') if c.isalnum() or c == '-')


def themed(stem, attrs):
    """An image that follows the reader's GitHub theme: `{stem}-dark.svg` / `{stem}-light.svg`, on one line."""
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="{stem}-dark.svg">'
            f'<source media="(prefers-color-scheme: light)" srcset="{stem}-light.svg"><img src="{stem}-light.svg" {attrs}></picture>')


# Timeline of the studies (same design as paper/figures/timeline.png), drawn here as SVG so it needs no plotting
# library, follows the reader's theme and is rebuilt with the data.
REL_COL = {'commercial_jev': ('#2a78d6', '#5c9ef0'), 'independent_jev_like': ('#eb6834', '#f08a50'), 'downstream_system': ('#1baf7a', '#3fc79a')}
REL_LAB = {'commercial_jev': 'Evaluates hosted Jev', 'independent_jev_like': 'Independent Jev-like model', 'downstream_system': 'Uses Jev inside a system'}
TL_INK = {'light': dict(text='#55636c', grid='#eaeef2', axis='#c3cacc', event='#8b979e'),
          'dark': dict(text='#9aa4b2', grid='#21262d', axis='#3d444d', event='#6e7681')}
TL_START = '2026-09-15'
TL_EVENTS = [('2026-09-15', ['Jev launch']), ('2026-09-21', ['Prior survey', 'draft dated']),
             ('2026-10-02', ['Vendor weak-spots', 'page revised']), (None, ['Cutoff'])]


def timeline_svg(P, cutoff, theme):
    import datetime as dt
    start, cut = dt.date.fromisoformat(TL_START), dt.date.fromisoformat(cutoff[:10])
    span = (cut - start).days
    day = lambda iso: (dt.date.fromisoformat(iso[:10]) - start).days  # noqa: E731
    order = list(REL_COL)
    stacks = defaultdict(list)
    for p in sorted([p for p in P if p['tier'] in ('core', 'peripheral')], key=lambda p: (order.index(p['model_relationship'][0]), p['published_at'])):
        stacks[day(p['published_at'])].append(p)
    top = max(len(v) for v in stacks.values())
    ink, k = TL_INK[theme], 0 if theme == 'light' else 1
    W, left, right, head, row, foot = 900, 44, 22, 58, 17, 34
    H = head + row * (top + 1) + foot
    base = H - foot
    x = lambda d: left + (d + 0.8) * (W - left - right) / (span + 1.6)  # noqa: E731
    font = "font-family=\"-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif\""
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
         f'aria-label="Timeline of the studies: one dot per study on the day of its first arXiv version, from Jev’s launch to the cutoff">']
    for t in range(0, span + 1, 2):
        label = (start + dt.timedelta(days=t))
        s.append(f'<path d="M{x(t):.1f} {head - 6}V{base}" stroke="{ink["grid"]}"/>')
        s.append(f'<text x="{x(t):.1f}" y="{base + 22}" fill="{ink["text"]}" {font} font-size="12" text-anchor="middle">{label.day} {label.strftime("%b")}</text>')
    for when, lines in TL_EVENTS:
        d = span if when is None else day(when)
        s.append(f'<path d="M{x(d):.1f} {head - 6}V{base}" stroke="{ink["event"]}" stroke-dasharray="4 3"/>')
        for i, line in enumerate(lines):
            s.append(f'<text x="{x(d):.1f}" y="{head - 14 - 14 * (len(lines) - 1 - i)}" fill="{ink["text"]}" {font} font-size="11.5" text-anchor="middle">{line}</text>')
    s.append(f'<path d="M{left} {base}H{W - right}" stroke="{ink["axis"]}"/>')
    s.append(f'<text transform="translate(16 {(head + base) / 2:.0f}) rotate(-90)" fill="{ink["text"]}" {font} font-size="12" text-anchor="middle">Studies per day</text>')
    for d, ps in sorted(stacks.items()):
        for i, p in enumerate(ps):
            c = REL_COL[p['model_relationship'][0]][k]
            fill = 'none' if p['tier'] == 'peripheral' else c
            s.append(f'<circle cx="{x(d):.1f}" cy="{base - row * (i + 0.65):.1f}" r="5.6" fill="{fill}" stroke="{c}" stroke-width="1.6"/>')
    # legend in the empty days just after the launch line
    legend = [(REL_COL[r][k], REL_COL[r][k], REL_LAB[r]) for r in order] + [('none', ink['text'], 'Peripheral (open marker)')]
    for i, (fill, stroke, label) in enumerate(legend):
        y = head + 18 + 19 * i
        s.append(f'<circle cx="{x(0) + 22:.1f}" cy="{y}" r="5.6" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
                 f'<text x="{x(0) + 34:.1f}" y="{y + 4.5}" fill="{ink["text"]}" {font} font-size="12.5">{label}</text>')
    s.append('</svg>')
    return '\n'.join(s) + '\n'


def badge(label, message, color, alt, href=None, logo=None):
    esc = lambda t: quote(t.replace('-', '--').replace('_', '__'), safe='')  # noqa: E731 - shields.io path escaping
    src = f'https://img.shields.io/badge/{esc(label)}-{esc(message)}-{color}?style=flat-square' + (f'&logo={logo}&logoColor=white' if logo else '')
    img = f'<img src="{src}" alt="{alt}">'
    return f'  <a href="{href}">{img}</a>' if href else f'  {img}'


def main():
    d = J.load_all()
    s = J.compute_stats(d)
    cfg = d['config']
    tax = d['taxonomy']
    P = d['papers']['papers']
    C = J.by_id(d['claims']['claims'])
    R = d['repositories']['repositories']
    T = {k: J.by_id(tax[k]) for k in ('topics', 'test_levels', 'model_relationships', 'background_groups', 'repository_types', 'method_families', 'applications', 'ecosystem_roles')}
    site = J.safe_url(cfg.get('site_url'))
    repo = J.safe_url(cfg.get('repository_url'))
    L = []
    a = L.append

    top = []  # set once the list sections start; each later section is preceded by a link back to the guide

    def heading(title):
        if top:
            a('<p align="right"><sub><a href="#repository-guide">↑ Back to guide</a></sub></p>')
            a('')
        a(themed(f'assets/readme/section-icons/{ICON[title]}', 'alt="" width="36" align="left"'))
        a('')
        a(f'## {title}')
        a('')

    a('<!-- Generated by scripts/render-readme.py from data/*.json. Edit the data, then run `python3 scripts/build.py`. -->')
    a('')
    a('<div align="center">')
    # the banner follows the reader's GitHub theme
    banner = ['  <picture>',
              '    <source media="(prefers-color-scheme: dark)" srcset="assets/readme/hero-banner-dark.svg">',
              '    <source media="(prefers-color-scheme: light)" srcset="assets/readme/hero-banner-light.svg">',
              '    <img src="assets/readme/hero-banner-light.svg" width="1000" alt="Awesome Jev — an evidence survey of Jev and typed decision models. '
              'Every answer type-checks. The evidence decides the rest.">',
              '  </picture>']
    if site:
        a(f'  <a href="{site}">')
        L.extend('  ' + x for x in banner)
        a('  </a>')
    else:
        L.extend(banner)
    a('')
    a('  <p><strong><em>⭐ Star us if you find this useful!</em></strong></p>')
    a('')
    if site:
        a(badge('explore', 'project website', '3a45c8', 'Explore the project website', site))
    a(badge('survey', 'PDF', 'B31B1B', 'Read the survey (PDF)', 'paper/main.pdf', logo='adobeacrobatreader'))
    a(badge('data', 'JSON · CSV · BibTeX', '13897a', 'Data: JSON, CSV and BibTeX', 'data/'))
    a(badge('release', f'v{J.VERSION} · {J.fmt_date(s["cutoff"])}', 'c95a22', f'Version {J.VERSION}, updated {J.fmt_date(s["cutoff"])}', 'CHANGELOG.md'))
    a(badge('licence', 'MIT · CC BY 4.0', '737a8f', 'Code MIT, content CC BY 4.0', '#citation-and-licence'))
    a('  <a href="https://awesome.re"><img src="https://awesome.re/badge-flat2.svg" alt="Awesome"></a>')
    a('')
    authors = [x for x in cfg.get('authors') or [] if x.get('name')]
    if authors:
        a('  <p>' + ' · '.join(f'<a href="{J.safe_url(x.get("url"))}">{x["name"]}</a>' if J.safe_url(x.get('url')) else x['name'] for x in authors) + '</p>')
        affs = [x for x in cfg.get('affiliations') or [] if x]
        if affs:
            a('  <p><sub>' + ' &nbsp;·&nbsp; '.join(x if isinstance(x, str) else x.get('name', '') for x in affs) + '</sub></p>')
    a('</div>')
    a('')
    heading('About the survey')
    a('Jev is a hosted model from TypeSafe AI, released on 15 September 2026 as its first “System One” model. Software sends a text state and typed questions; '
      'the model answers from the declared options with probabilities and never writes free text. Open projects now copy its shape. This repository reads '
      'the studies of TypeSafe’s Jev and Jev-like typed decision models, audits the open resources and traces every number to its source.')
    a('')
    a('> [!IMPORTANT]')
    a('> **Four questions guide the survey.**')
    a('>')
    for q, text in (('Meaning', 'what do the probabilities mean?'), ('Action', 'when should software act on them?'),
                    ('Failure', 'where do typed decisions fail?'), ('Openness', 'what do open implementations release?')):
        a(f'> - **{q}:** {text}')
    a('')
    a('> [!NOTE]')
    a('> Numbers are as reported by paper authors, the vendor or repository maintainers. Nothing here is a unified leaderboard, and nothing was re-run.')
    a('')
    for theme in ('light', 'dark'):
        (J.ROOT / 'assets' / 'readme' / f'timeline-{theme}.svg').write_text(timeline_svg(P, s['cutoff'], theme), encoding='utf-8')
    a('<p align="center">' + themed('assets/readme/timeline', 'width="900" alt="Timeline of the studies, one dot per study on the day of its first '
                                    'arXiv version from Jev’s launch on 15 September 2026 to the cutoff, coloured by whether a study evaluates hosted Jev, '
                                    'builds an independent Jev-like model or uses Jev inside a system"') + '</p>')
    a('<p align="center"><sub>The evidence so far: one dot per study on the day of its first arXiv version, coloured by how it relates to Jev.</sub></p>')
    a('')
    if site:
        a('**Explore:** ' + ' · '.join(f'[{t}]({site}#{k})' for k, t in (('map', 'Evidence map'), ('findings', 'Findings'), ('failures', 'Failure modes'),
                                                                        ('ecosystem', 'Open ecosystem'), ('literature', 'Literature search')))
          + ' · [Survey (PDF)](paper/main.pdf)')
        a('')
    news = releases()[:3]
    if news:
        a('**What’s new**')
        a('')
        for version, date, summary in news:
            a(f'- **{date} · v{version}** — {summary}')
        a('')
        a('Every release is listed in the [changelog](CHANGELOG.md).')
        a('')
    slug = repo.rstrip('/').split('github.com/')[-1] if repo and 'github.com/' in repo else ''
    sections = ['Findings'] + [g for _, g in GROUPS] + ['Peripheral study', 'Open ecosystem', 'Background references', 'Related reviews and catalogues',
                                                        'Method', 'Contributing'] + (['Contributors', 'Star history'] if slug else []) + ['Citation and licence']
    heading('Repository guide')
    a(' · '.join(f'[{h}](#{anchor(h)})' for h in sections))
    a('')
    a('## Choose a section')
    a('')
    a('Select a card to jump directly to its section.')
    a('')
    a('<p align="center">')
    for i, (key, title, sub) in enumerate(CARDS):
        if i == 3:
            a('  <br>')
        card = themed(f'assets/readme/card-{key}', f'width="246" alt="{title}: {sub}"')
        a(f'  <a href="#{anchor(title)}">{card}</a>')
    a('</p>')
    a('')
    a('## Reading the tables')
    a('')
    a('Study tables give the date of the first arXiv version and the headline as its authors report it. The links after a study’s name point to its '
      'released material; no link means none was located, which is not the same as absent. ¹ marks studies that share authors with others here: '
      'read them together, not as independent replications.')
    a('')
    a('Open-model availability uses these marks:')
    a('')
    a('| Mark | Meaning |')
    a('| :-: | --- |')
    for mark, meaning in (('●', 'Available'), ('◐', 'Partially available'), ('◑', 'Restricted'), ('◔', 'Project page only'),
                          ('○', 'Not located — not the same as absent'), ('–', 'Not applicable')):
        a(f'| {mark} | {meaning} |')
    a('')
    a('---')
    a('')
    heading('Findings')
    top.append(True)
    a('Each finding is a synthesis across studies; open one to read it with its key evidence.')
    a('')
    papers = J.by_id(P)
    for f in tax['findings']:
        a('<details>')
        a(f'<summary><b>{f["id"]} · {f["title"]}</b></summary>')
        a('')
        a(f['body'])
        a('')
        a('**Key evidence**')
        a('')
        for cid in J.KEY_EVIDENCE[f['id']]:
            p = papers[C[cid]['subject']]
            a(f'- **[{md(p["short_title"])}]({p["url"]})** — {md(C[cid]["headline"])}')
        a('')
        a('</details>')
        a('')
    for rel, title in GROUPS:
        ps = sorted([p for p in P if p['tier'] == 'core' and p['model_relationship'][0] == rel], key=lambda p: (p['published_at'], p['title']), reverse=True)
        if not ps:
            continue
        heading(title)
        by_month = defaultdict(list)
        for p in ps:
            by_month[p['published_at'][:7]].append(p)
        for month in sorted(by_month, reverse=True):
            a(f'### {MONTHS[int(month[5:7]) - 1]} {month[:4]}')
            a('')
            a('| Posted | Study | Headline (as reported) |')
            a('| --- | --- | --- |')
            for p in by_month[month]:
                head = C[p['claims'][0]]['headline'] if p['claims'] else ''
                fam = '¹' if p.get('study_family_id') else ''
                full = f'<br><sub>{md(p["title"])}</sub>' if p['title'] != p['short_title'] else ''
                day = f'{int(p["published_at"][8:10])}&nbsp;{MONTHS[int(month[5:7]) - 1][:3]}'
                # the ¹ sits inside the bold: right after a closing ** it would stop GitHub from closing the bold
                a(f'| {day} | **[{md(p["short_title"])}]({p["url"]}){fam}**{code_link(p["openness"]["code"])}{full} | {md(head)} |')
            a('')
    heading('Peripheral study')
    for p in [p for p in P if p['tier'] == 'peripheral']:
        a(f'- [{md(p["title"])}]({p["url"]}) — {md(p["relationship"])} {md(p["caveats"][0])}')
    a('')
    heading('Open ecosystem')
    a('Availability follows each project’s own README; nothing here was run. ● available · ◐ partial · ◑ restricted · ◔ project page only · ○ not located · – not applicable.')
    a('')
    by_role = defaultdict(list)
    for r in R:
        by_role[r['ecosystem']].append(r)
    fam_order = [f['id'] for f in tax['method_families']]
    a(f'### {T["ecosystem_roles"]["open_model"]["label"]}')
    a('')
    a('| Resource | Weights | Training | Evaluation | Data | Licence |')
    a('| --- | :-: | :-: | :-: | :-: | --- |')
    for r in sorted(by_role['open_model'], key=lambda r: (fam_order.index(r['method_family']) if r.get('method_family') else 99, r['full_name'].lower())):
        av = r.get('availability') or {}
        fam = T['method_families'][r['method_family']]['label'] if r.get('method_family') else '—'
        a(f'| **[{r["full_name"]}]({r.get("readme_url") or r["url"]})**<br><sub>{fam} · base: {md(r.get("base_model") or "—")}</sub> | '
          + ' | '.join(AVAIL_MD.get(av.get(k, 'not_assessed'), '·') for k in ('weights', 'training_code', 'evaluation', 'data')) + f' | {LICENCE.get(r.get("license"), r.get("license")) or "—"} |')
    a('')
    # Long secondary lists are folded (as in Awesome-LLM) so the findings and study tables stay the main thread.
    def folded(summary, lines):
        a('<details>')
        a(f'<summary>{summary}</summary>')
        a('')
        L.extend(lines)
        a('')
        a('</details>')
        a('')

    a(f'### {T["ecosystem_roles"]["benchmark"]["label"]}')
    a('')
    folded('Show the list', [f'- [{r["full_name"]}]({r["url"]}) — {md(r["summary"])} *{md(r["boundary"])}*'
                             for r in sorted(by_role['benchmark'], key=lambda r: r['full_name'].lower())])
    a(f'### {T["ecosystem_roles"]["built_with"]["label"]}')
    a('')
    a('Grouped by application; open a group to see its projects.')
    a('')
    groups = defaultdict(list)
    for r in by_role['built_with']:
        groups[r.get('domain')].append(r)
    for app in tax['applications'] + [dict(id=None, label='Other')]:
        rs = sorted(groups.get(app['id'], []), key=lambda r: r['full_name'].lower())
        if rs:
            folded(f'<b>{html.escape(app["label"])}</b>', [f'- [{r["full_name"]}]({r["url"]}) — {md(r["summary"])}' for r in rs])
    a(f'### {T["ecosystem_roles"]["tooling"]["label"]}')
    a('')
    folded('Show the list', [f'- [{r["full_name"]}]({r["url"]}) — {md(r["summary"])}'
                             for r in sorted(by_role['tooling'], key=lambda r: (r['type'], r['full_name'].lower()))])
    heading('Background references')
    a('Earlier work the survey builds on, grouped by theme; open a theme to see its papers.')
    a('')
    for g in tax['background_groups']:
        items = [p for p in P if p['tier'] == 'background' and p['background_group'] == g['id']]
        if not items:
            continue
        lines = []
        for p in sorted(items, key=lambda p: p['published_at'], reverse=True):
            venue = f' · {p["venue"]}' if p['venue_source'] in ('arxiv_comment', 'arxiv_journal_ref') else ''
            lines.append(f'- [{md(p["title"])}]({p["url"]}) ({p["year"]}{venue}) — {md(p["role"])}')
        folded(f'<b>{html.escape(g["label"])}</b>', lines)
    heading('Related reviews and catalogues')
    for r in d['review-relations']['related_surveys']:
        a(f'- [{md(r.get("short") or r["title"])}]({r["url"]}) ({md(r.get("kind_label") or "")}) — {md(r.get("scope") or "")}.')
    a('')
    heading('Method')
    a('- Scope, search and screening: [docs/methodology.md](docs/methodology.md). Limitations: [docs/limitations.md](docs/limitations.md).')
    a('- How evidence records and openness fields are defined: [docs/evidence-audit.md](docs/evidence-audit.md).')
    a('- Survey manuscript: [PDF](paper/main.pdf) · [Markdown](paper/survey.md).')
    a('- Data: [`data/`](data/) (JSON records), [`data/exports/`](data/exports/) (CSV) and [`data/references.bib`](data/references.bib); screening lists in [`research/`](research/).')
    a('')
    heading('Contributing')
    a('New studies, corrections, code or weights updates and reproduction reports are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)'
      + (f' or [open an issue]({repo.rstrip("/")}/issues/new/choose).' if repo else '.'))
    a('')
    if repo:
        a(' · '.join(f'[{label}]({repo.rstrip("/")}/issues/new?template={form})' for form, label in ISSUE_FORMS))
        a('')
    if slug:
        heading('Contributors')
        a('Thanks to everyone who has added studies, corrections, code and reproduction reports.')
        a('')
        a(f'<a href="{repo.rstrip("/")}/graphs/contributors"><img src="https://contrib.rocks/image?repo={slug}" alt="Contributors to {slug}"></a>')
        a('')
        heading('Star history')
        chart = f'https://api.star-history.com/svg?repos={slug}&type=Date'
        a('<p align="center">')
        a(f'  <a href="https://star-history.com/#{slug}&Date">')
        a('    <picture>')
        a(f'      <source media="(prefers-color-scheme: dark)" srcset="{chart}&theme=dark">')
        a(f'      <source media="(prefers-color-scheme: light)" srcset="{chart}">')
        a(f'      <img alt="Star history of {slug}" src="{chart}" width="800">')
        a('    </picture>')
        a('  </a>')
        a('</p>')
        a('')
    heading('Citation and licence')
    a('Citation metadata is in [CITATION.cff](CITATION.cff) (GitHub shows it under “Cite this repository”).')
    a('')
    if authors:
        first = authors[0]
        names = ' and '.join(f'{x.get("family_names") or x["name"]}, {x.get("given_names") or ""}'.rstrip(', ') for x in authors)
        key = (first.get('family_names') or first['name'].split()[-1]).lower() + s['cutoff'][:4] + 'jevsurvey'
        a('```bibtex')
        a(f'@misc{{{key},')
        a(f'  author       = {{{names}}},')
        a('  title        = {Jev and Typed Decision Models: An Empirical Survey of Calibration, Selective Control, and Open Implementations},')
        a(f'  year         = {{{s["cutoff"][:4]}}},')
        if site or repo:
            a(f'  howpublished = {{\\url{{{site or repo}}}}},')
        a(f'  note         = {{Version {J.VERSION}, updated {J.fmt_date(s["cutoff"])}. Working draft, not peer-reviewed}}')
        a('}')
        a('```')
        a('')
    a('Licences: the code (scripts, site, tests, workflows) is under the [MIT License](LICENSE); original text, figures and curated data are under '
      '[CC BY 4.0](LICENSE-CONTENT.md). Third-party material — paper metadata, quoted README facts, vendor documentation — keeps its own terms; '
      'see [NOTICE.md](NOTICE.md).')
    a('')
    (J.ROOT / 'README.md').write_text('\n'.join(L), encoding='utf-8')


if __name__ == '__main__':
    main()
