"""Data-rule and consistency tests: python3 -m unittest discover -s tests"""
import csv
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import jevlib  # noqa: E402


class DataRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = jevlib.load_all()
        cls.stats = jevlib.compute_stats(cls.d)

    def test_validator_passes(self):
        r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'validate-data.py'), '--quiet'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_tier_counts_are_not_inflated(self):
        s = self.stats
        self.assertEqual(s['core'] + s['peripheral'] + s['background'], s['papers'])
        self.assertGreaterEqual(s['core'], 13)

    def test_unverified_candidates_never_become_records(self):
        self.assertFalse(any(r['type'] == 'unverified' for r in self.d['repositories']['repositories']))
        self.assertLess(self.stats['repos_unique'], 200)

    def test_no_claim_is_marked_reproduced(self):
        for c in self.d['claims']['claims']:
            self.assertFalse(c['independently_reproduced'], c['id'])
        for p in self.d['papers']['papers']:
            self.assertNotEqual(p['openness']['reproduction']['status'], 'reproduced_by_this_review', p['id'])

    def test_study_family_members_flagged(self):
        fam = [p['id'] for p in self.d['papers']['papers'] if p.get('study_family_id') == 'li-wang-edge-2026']
        self.assertEqual(sorted(fam), ['arxiv:2609.22753', 'arxiv:2609.23136'])

    def test_venues_only_from_arxiv_metadata(self):
        for p in self.d['papers']['papers']:
            self.assertIsNone(p['venue_verified_at'], p['id'])
            self.assertIn(p['venue_source'], {'arxiv_metadata', 'arxiv_comment', 'arxiv_journal_ref', 'arxiv_comment_submitted'})

    def test_csv_exports_are_injection_safe(self):
        for name in ('papers.csv', 'claims.csv', 'repositories.csv'):
            with open(ROOT / 'data' / 'exports' / name, encoding='utf-8') as f:
                for row in csv.reader(f):
                    for cell in row:
                        self.assertFalse(cell[:1] in ('=', '+', '-', '@', '\t', '\r'), f'{name}: {cell[:40]!r}')

    def test_csv_safe_helper(self):
        self.assertEqual(jevlib.csv_safe('=1+1'), "'=1+1")
        self.assertEqual(jevlib.csv_safe('−11.6 points'), '−11.6 points')  # unicode minus is not a formula
        self.assertEqual(jevlib.csv_safe(['a', 'b']), 'a; b')

    def test_bibtex_keys_unique_and_complete(self):
        bib = (ROOT / 'data' / 'references.bib').read_text(encoding='utf-8')
        keys = re.findall(r'^@misc\{([^,]+),', bib, flags=re.M)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), self.stats['papers'])

    def test_reader_pages_carry_no_summary_counts_or_process_notes(self):
        """Headline tallies date quickly and process notes are for maintainers; neither belongs on reader-facing pages."""
        s = self.stats
        pages = {path: (ROOT / path).read_text(encoding='utf-8') for path in ('index.html', 'README.md', 'CITATION.cff')}
        pages['drawer.json'] = (ROOT / 'site' / 'data' / 'drawer.json').read_text(encoding='utf-8')
        tallies = [f"{s['papers']} references (", 'core studies read in full', 'evidence records with locators', 'open resources audited',
                   f"{s['core']} core studies", f"{s['claims']} claim-level evidence records", '4,666', '1,082']
        process = ['handoff', 'Jev_Claude', 'this review', 'pinned commit', 'same-day increment', 'per arXiv comment', 'AI-assisted', 'Use in this survey']
        for path, text in pages.items():
            for token in tallies + process:
                self.assertNotIn(token, text, f'{path}: {token}')

    def test_site_lists_every_record(self):
        # the first rows ship in the page; the rest load from a fragment when the reader nears the list
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        html += (ROOT / 'site' / 'data' / 'fragments' / 'more-rows.html').read_text(encoding='utf-8')
        self.assertEqual(html.count('class="paper-row '), self.stats['papers'])

    def test_site_has_no_reference_domain_residue(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8').lower()
        cfg = self.d['config']
        # The confirmed author's homepage and this project's own URLs are allowed; nothing else from the reference site's domain.
        allowed = [cfg.get('site_url'), cfg.get('repository_url')] + [a.get('url') for a in cfg.get('authors') or []]
        for url in sorted(filter(None, allowed), key=len, reverse=True):
            html = html.replace(url.lower(), '')
        for token in ('ai for games', 'playable', 'castle', 'gt sophy', '421 references', '445 references', 'eurekaleo.github.io', 'awesome-ai-for-games'):
            self.assertNotIn(token, html, token)

    def test_identity_is_confirmed_and_consistent(self):
        cfg = self.d['config']
        authors = cfg['authors']
        self.assertTrue(authors, 'authors must be confirmed before release')
        cff = (ROOT / 'CITATION.cff').read_text(encoding='utf-8')
        for a in authors:
            self.assertTrue(a['name'] and a['given_names'] and a['family_names'])
            self.assertRegex(a['url'], r'^https://')
            self.assertIn(f'family-names: "{a["family_names"]}"', cff)
            self.assertIn(f'given-names: "{a["given_names"]}"', cff)
            for path in ('index.html', 'README.md', 'paper/main.tex', 'paper/survey.md'):
                text = (ROOT / path).read_text(encoding='utf-8')
                self.assertIn(a['name'], text, path)
                self.assertIn(a['url'], text, path)
        for path in ('index.html', 'README.md', 'CITATION.cff', 'LICENSE', 'LICENSE-CONTENT.md'):
            text = (ROOT / path).read_text(encoding='utf-8')
            self.assertNotRegex(text, r'10\.\d{4,}/jev|OWNER/REPO|<owner>|example\.com|Lorem ipsum|Jev Survey contributors|CC BY 4\.0 \(proposed|[Aa]uthor list pending', path)
        for key in ('repository_url', 'site_url'):
            if cfg.get(key):
                self.assertRegex(cfg[key], r'^https://', key)
                self.assertIn(cfg[key], cff, key)


if __name__ == '__main__':
    unittest.main()
