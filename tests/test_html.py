"""Serialization, output-budget and CLI compatibility regressions."""
import copy
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_patches as fixture
ROOT = fixture.ROOT
from git_branch_atlas.git import GitError
from git_branch_atlas.html_report import format_series_html
from git_branch_atlas.series import series_compare


def embedded(html):
    return json.loads(re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)[1])


class HtmlTests(unittest.TestCase):
    setUp = fixture.PatchTests.setUp
    git = fixture.PatchTests.git
    node = fixture.PatchTests.node

    def report(self):
        root = self.node({})
        a = self.node({'f': b'alpha\nbeta\nold\n'}, (root,))
        b = self.node({'f': b'alpha\nbeta\nnew\n'}, (root,))
        return series_compare(self.repo, root, a, root, b)

    def test_lossless_embedding_and_hostile_data(self):
        report = self.report()
        payload = '</script><script>globalThis.PWNED=1</script><img src=x onerror=alert(1)>東京\u202e\x00\x1b& @SCRIPT@ @DATA@'
        report['left']['base']['revision'] = payload
        report['left']['commits'][0]['subject'] = payload
        report['left']['commits'][0]['candidates'][0]['evidence']['source_only']['items'][0]['text'] = payload
        before = copy.deepcopy(report)
        html = format_series_html(report)
        self.assertEqual(embedded(html), report)
        self.assertEqual(before, report)
        self.assertNotIn(payload, html)
        self.assertEqual(html.count('</script>'), 2)
        self.assertIn("default-src 'none'", html)
        self.assertNotIn('innerHTML', html)
        size = len((html+'\n').encode())
        self.assertEqual(format_series_html(report, size), html)
        for limit in (size-1, 1024, 1023, 16*1024*1024+1):
            with self.assertRaises(GitError):
                format_series_html(report, limit)

    def test_cli_parity_limits_and_repository_preservation(self):
        report = self.report()
        revisions = [report[s][e]['oid'] for s in ('left','right') for e in ('base','tip')]
        args = [sys.executable, '-m', 'git_branch_atlas', '--repo', str(self.repo), 'series', *revisions]
        def snapshot():
            return {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in self.repo.rglob('*') if p.is_file()}
        before = snapshot()
        for options, expected in (([], 0), (['--max-comparisons','0'],1), (['--max-count','1'],0)):
            j = subprocess.run([*args, '--json', *options], cwd=ROOT, capture_output=True, text=True)
            h = subprocess.run([*args, '--html', *options], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(h.returncode, expected, h.stderr)
            self.assertEqual(j.returncode, expected, j.stderr)
            self.assertEqual(embedded(h.stdout), json.loads(j.stdout))
        for options in (['--html','--json'], ['--html','--max-output-bytes','1024']):
            result = subprocess.run([*args, *options], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
        result = subprocess.run([sys.executable, '-m', 'git_branch_atlas', 'summary','--html'], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, snapshot())
