#!/usr/bin/env python3
"""Draw the README artwork: banner, section cards and section icons, each in a light and a dark version.

    python3 scripts/draw-readme-art.py              # rewrite assets/readme/*.svg (no dependencies)
    python3 scripts/draw-readme-art.py --og-image   # also render assets/og-image.png (needs Chrome, Chromium or Edge; Pillow optional)

The artwork is static: run this by hand after changing a card's text, a colour or an icon, and commit the SVGs.
The README links them through <picture> so GitHub shows the version matching the reader's theme. The timeline is
not drawn here: it depends on the data, so scripts/render-readme.py rebuilds it with the README.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jevlib as J  # noqa: E402

OUT = J.ROOT / 'assets' / 'readme'
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
SERIF = "'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
BG, SURF, LINE, INK2, MUTED = '#0f1220', '#171c2e', '#2a3148', '#b8bdd0', '#8d93a9'
INDIGO, ORANGE, TEAL, AMBER, SKY = '#9aa3ff', '#f08a50', '#4fc5b0', '#e3a94f', '#9bd7d3'

# Banner palettes: the site's own dark and light themes (site/site.css)
THEMES = {
    'dark': dict(bg0='#141a30', bg1=BG, glow=INDIGO, glow_op='.16', line='#fff', line_op='.035', edge_op='.08', title='#f7f8fc', ink2=INK2,
                 muted=MUTED, accent=ORANGE, bar_a=ORANGE, bar='#3c6f6a', axis_op='.28'),
    'light': dict(bg0='#ffffff', bg1='#eef0ea', glow='#3a45c8', glow_op='.08', line='#151a2d', line_op='.05', edge_op='.12', title='#151a2d',
                  ink2='#454c63', muted='#737a8f', accent='#c95a22', bar_a='#d0612a', bar='#a9d6cc', axis_op='.3'),
}
BANNER_LABEL = 'Awesome Jev: Jev and typed decision models. Every answer type-checks. The evidence decides the rest.'

# Icon glyphs on a 24x24 grid; "CUR" is replaced by the stroke colour
GLYPHS = {
    'about': '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><circle cx="12" cy="7.6" r=".6" fill="CUR"/>',
    'guide': '<rect x="4" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.6"/>'
             '<rect x="4" y="13.5" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.6"/>',
    'findings': '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/><path d="M8 10.5h5"/>',
    'hosted': '<path d="M7 18h10.5a4 4 0 0 0 .4-8A6 6 0 0 0 6.4 9.3 4.4 4.4 0 0 0 7 18Z"/>',
    'open': '<path d="M4 9l8-4 8 4-8 4-8-4Z"/><path d="M4 9v7l8 4 8-4V9"/><path d="M12 13v7"/>',
    'systems': '<circle cx="6" cy="6.5" r="2.4"/><circle cx="18" cy="6.5" r="2.4"/><circle cx="12" cy="17.5" r="2.4"/>'
               '<path d="M7.2 8.6l3.6 6.8M16.8 8.6l-3.6 6.8M8.4 6.5h7.2"/>',
    'peripheral': '<circle cx="12" cy="12" r="8.5" stroke-dasharray="2.6 2.4"/><circle cx="12" cy="12" r="2.6"/>',
    'ecosystem': '<path d="M5 19V11M10 19V6M15 19v-6M20 19v-3"/>',
    'background': '<path d="M4.5 5.5c2.6-1 5-.8 7.5.8v13c-2.5-1.6-4.9-1.8-7.5-.8v-13Z"/><path d="M19.5 5.5c-2.6-1-5-.8-7.5.8v13c2.5-1.6 4.9-1.8 7.5-.8v-13Z"/>',
    'related': '<path d="M10 14a3.5 3.5 0 0 0 5 0l3.5-3.5a3.5 3.5 0 0 0-5-5L12.4 6.6"/><path d="M14 10a3.5 3.5 0 0 0-5 0L5.5 13.5a3.5 3.5 0 0 0 5 5l1.1-1.1"/>',
    'method': '<path d="M9.5 4h5M10.5 4v5.2L5.6 17.4A1.8 1.8 0 0 0 7.2 20h9.6a1.8 1.8 0 0 0 1.6-2.6l-4.9-8.2V4"/><path d="M8 14.5h8"/>',
    'contribute': '<circle cx="12" cy="12" r="8.5"/><path d="M12 8v8M8 12h8"/>',
    'contributors': '<circle cx="9" cy="8.5" r="3"/><path d="M3.5 19c.6-3.3 2.8-5 5.5-5s4.9 1.7 5.5 5"/>'
                    '<circle cx="16.5" cy="9.5" r="2.4"/><path d="M15.6 14.2c2.4-.2 4.4 1.3 4.9 4.3"/>',
    'star': '<path d="M12 4.2l2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.6-4.8 2.6.9-5.4-3.9-3.8 5.4-.8L12 4.2Z"/>',
    'cite': '<path d="M5 17.5c0-4.5 1.2-7.5 4.5-9.5M13 17.5c0-4.5 1.2-7.5 4.5-9.5"/><circle cx="7" cy="16" r="2.2"/><circle cx="15" cy="16" r="2.2"/>',
}
# Accent per icon: (dark-theme colour, light-theme colour); the light ones come from the site's light palette
ACCENT = dict(indigo=(INDIGO, '#3a45c8'), orange=(ORANGE, '#c95a22'), teal=(TEAL, '#13897a'), amber=(AMBER, '#a26512'),
              sky=(SKY, '#1f7a86'), grey=(INK2, '#59606f'))
ICON_ACCENT = dict(about='sky', guide='indigo', findings='amber', hosted='indigo', open='orange', systems='teal', peripheral='grey',
                   ecosystem='sky', background='grey', related='indigo', method='teal', contribute='orange', contributors='teal',
                   star='amber', cite='grey')
# Tile and card chrome per theme
CHROME = {
    'dark': dict(tile=SURF, tile_line=LINE, card=SURF, card_line='#323a54', card_tile='#1f2640', title='#f4f6fb', sub='#97a0b8'),
    'light': dict(tile='#f6f8fa', tile_line='#d0d7de', card='#ffffff', card_line='#d0d7de', card_tile='#f1f3f7', title='#1f2328', sub='#59636e'),
}
# Section cards: (file key and icon, title, subtitle); render-readme.py links each card to its section
CARDS = [('findings', 'Findings', 'Cross-study syntheses'),
         ('hosted', 'Hosted Jev', 'Studies of TypeSafe’s model'),
         ('open', 'Open Jev-like models', 'Same shape, other mechanisms'),
         ('systems', 'Systems', 'Built on typed decisions'),
         ('ecosystem', 'Open ecosystem', 'Models, benchmarks and tools'),
         ('background', 'Background', 'Calibration, deferral, routing')]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def hero_body(t):
    diag = ''.join(f'<path d="M{x} 380 L{x + 260} 0"/>' for x in range(560, 1100, 34))
    bars = [(690, 92, 'A', '.81'), (770, 196, 'B', '.11'), (850, 238, 'C', '.05'), (930, 262, 'D', '.03')]
    bar_svg = ''
    for x, y, lab, p in bars:
        bar_svg += f'<rect x="{x}" y="{y}" width="52" height="{300 - y}" rx="7" fill="{t["bar_a"] if lab == "A" else t["bar"]}"/>'
        bar_svg += (f'<text x="{x + 26}" y="{y - 10}" fill="{t["accent"] if lab == "A" else t["ink2"]}" font-family="{MONO}" '
                    f'font-size="13" text-anchor="middle">{p}</text>')
        bar_svg += f'<text x="{x + 26}" y="326" fill="{t["muted"]}" font-family="{MONO}" font-size="14" text-anchor="middle">{lab}</text>'
    return f'''<g stroke="{t["line"]}" stroke-opacity="{t["line_op"]}">{diag}</g>
<path d="M56 74h34" stroke="{t["accent"]}" stroke-width="2"/>
<text x="102" y="79" fill="{t["ink2"]}" font-family="{SANS}" font-size="14" letter-spacing="2.4">AWESOME JEV · AN EVIDENCE SURVEY</text>
<text x="54" y="160" fill="{t["title"]}" font-family="{SANS}" font-size="64" font-weight="800" letter-spacing="-1.5">Jev and <tspan fill="{t["accent"]}" font-family="{SERIF}" font-style="italic" font-weight="400" font-size="74" letter-spacing="0">typed</tspan></text>
<text x="56" y="222" fill="{t["title"]}" font-family="{SANS}" font-size="44" font-weight="400">decision models</text>
<text x="57" y="278" fill="{t["ink2"]}" font-family="{SANS}" font-size="21">Every answer type-checks.</text>
<text x="57" y="308" fill="{t["ink2"]}" font-family="{SANS}" font-size="21">The evidence decides the rest.</text>
<text x="57" y="348" fill="{t["muted"]}" font-family="{MONO}" font-size="12.5">calibration · selective control · failure modes · open implementations</text>
<text x="664" y="58" fill="{t["muted"]}" font-family="{MONO}" font-size="12.5">choice  answer ∈ {{A, B, C, D}}</text>
<path d="M664 300h300" stroke="{t["line"]}" stroke-opacity="{t["axis_op"]}" stroke-width="1.5"/>
{bar_svg}
<path d="M664 150h300" stroke="{t["accent"]}" stroke-width="2" stroke-dasharray="7 5"/>
<text x="972" y="156" fill="{t["accent"]}" font-family="{SERIF}" font-style="italic" font-size="20">τ</text>'''


def background(t, w, h, rx):
    return f'''<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{t["bg0"]}"/><stop offset="1" stop-color="{t["bg1"]}"/></linearGradient>
<radialGradient id="r" cx=".8" cy=".35" r=".5"><stop offset="0" stop-color="{t["glow"]}" stop-opacity="{t["glow_op"]}"/><stop offset="1" stop-color="{t["glow"]}" stop-opacity="0"/></radialGradient>
<clipPath id="c"><rect width="{w}" height="{h}" rx="{rx}"/></clipPath></defs>
<g clip-path="url(#c)"><rect width="{w}" height="{h}" fill="url(#g)"/><rect width="{w}" height="{h}" fill="url(#r)"/></g>'''


def banner(t):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 380" width="1000" height="380" role="img" aria-label="{BANNER_LABEL}">\n'
            f'{background(t, 1000, 380, 18)}\n<g clip-path="url(#c)">{hero_body(t)}</g>\n'
            f'<rect x=".5" y=".5" width="999" height="379" rx="17.5" fill="none" stroke="{t["line"]}" stroke-opacity="{t["edge_op"]}"/>\n</svg>\n')


def og_svg(site):
    """Share image (og:image, 1200x630): the dark banner with the site address; no counts or dates, so it does not go stale."""
    t = THEMES['dark']
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630">\n{background(t, 1200, 630, 0)}\n'
            f'<g transform="translate(0 62) scale(1.15)">{hero_body(t)}</g>\n'
            f'<path d="M62 556h1076" stroke="#fff" stroke-opacity=".08"/>'
            f'<text x="62" y="596" fill="{t["ink2"]}" font-family="{SANS}" font-size="20">{site}</text>'
            f'<text x="1138" y="596" fill="{t["muted"]}" font-family="{SANS}" font-size="16" text-anchor="end">Schematic artwork, not measured data</text>\n</svg>\n')


def glyph(name, color, scale, dx, dy, width=1.8):
    body = GLYPHS[name].replace('CUR', color)
    return (f'<g transform="translate({dx} {dy}) scale({scale})" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</g>')


def icon(name, theme):
    ch, c = CHROME[theme], ACCENT[ICON_ACCENT[name]][0 if theme == 'dark' else 1]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="36" height="36" aria-hidden="true">'
            f'<rect x=".5" y=".5" width="35" height="35" rx="9" fill="{ch["tile"]}" stroke="{ch["tile_line"]}"/>{glyph(name, c, 1, 6, 6)}</svg>\n')


def card(i, key, title, sub, theme):
    ch, c = CHROME[theme], ACCENT[ICON_ACCENT[key]][0 if theme == 'dark' else 1]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 246 70" width="246" height="70" role="img" aria-label="{title}: {sub}">'
            f'<defs><clipPath id="r"><rect width="246" height="70" rx="9.5"/></clipPath></defs>'
            f'<rect width="246" height="70" rx="9.5" fill="{ch["card"]}"/><rect width="4" height="70" fill="{c}" clip-path="url(#r)"/>'
            f'<rect x=".5" y=".5" width="245" height="69" rx="9" fill="none" stroke="{ch["card_line"]}"/>'
            f'<rect x="19" y="17" width="36" height="36" rx="9" fill="{ch["card_tile"]}"/>{glyph(key, c, .75, 28, 26)}'
            f'<text x="64" y="33" fill="{ch["title"]}" font-family="{SANS}" font-size="14.5" font-weight="700">{title}</text>'
            f'<text x="64" y="51" fill="{ch["sub"]}" font-family="{SANS}" font-size="11">{sub}</text>'
            f'<text x="232" y="18" fill="{c}" font-family="{SANS}" font-size="10" font-weight="700" text-anchor="end">0{i}</text></svg>\n')


def find_browser():
    names = ['google-chrome', 'chromium', 'chromium-browser', 'chrome', 'msedge', 'microsoft-edge']
    paths = [r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
             '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    return next((shutil.which(n) for n in names if shutil.which(n)), None) or next((p for p in paths if pathlib.Path(p).exists()), None)


def render_og(site):
    browser = find_browser()
    if not browser:
        sys.exit('--og-image needs Chrome, Chromium or Edge')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        write(tmp / 'og.svg', og_svg(site))
        write(tmp / 'og.html', '<!doctype html><html><head><style>html,body{margin:0;background:#0f1220}img{display:block}</style></head>'
                               '<body><img src="og.svg" width="1200" height="630"></body></html>')
        shot = tmp / 'og.png'
        subprocess.run([browser, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', '--window-size=1200,630',
                        f'--user-data-dir={tmp / "profile"}', f'--screenshot={shot}', (tmp / 'og.html').as_uri()], check=True, capture_output=True)
        dest = J.ROOT / 'assets' / 'og-image.png'
        try:  # a 256-colour palette keeps the file small without visible banding
            from PIL import Image
            Image.open(shot).convert('RGB').quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG).save(dest, optimize=True)
        except ImportError:
            shutil.copyfile(shot, dest)
    print(f'rendered {dest.relative_to(J.ROOT).as_posix()} with {pathlib.Path(browser).name}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--og-image', action='store_true', help='also render assets/og-image.png with a headless browser')
    args = ap.parse_args()
    for theme, t in THEMES.items():
        write(OUT / f'hero-banner-{theme}.svg', banner(t))
        for name in ICON_ACCENT:
            write(OUT / 'section-icons' / f'{name}-{theme}.svg', icon(name, theme))
        for i, (key, title, sub) in enumerate(CARDS, 1):
            write(OUT / f'card-{key}-{theme}.svg', card(i, key, title, sub, theme))
    print(f'wrote {2 * (1 + len(ICON_ACCENT) + len(CARDS))} SVGs under {OUT.relative_to(J.ROOT).as_posix()}')
    if args.og_image:
        site = (J.load_all()['config'].get('site_url') or '').replace('https://', '').rstrip('/')
        render_og(site)


if __name__ == '__main__':
    main()
