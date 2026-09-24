#!/usr/bin/env python3
"""Build real Git fixtures and compare offline Chromium with CLI JSON.

Requires Node + Playwright (see docs/html-review.md). Uses only temporary repos.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))
from test_patches import PatchTests
from git_branch_atlas.html_report import format_series_html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    fixture = PatchTests()
    fixture.setUp()
    try:
        root = fixture.node({})
        exact = fixture.node({'exact': b'exact\n'}, (root,))
        undo = fixture.node({}, (exact,))
        duplicate = fixture.node({'exact': b'exact\n'}, (undo,))
        hostile = '</script><img src="https://invalid.example/x" onerror="globalThis.PWNED=1">東京\x1b\u202e'
        hostile_path = hostile.replace('/', '∕')
        files = {'exact': b'exact\n', hostile_path: ('alpha\nbeta\n'+hostile+'left\n').encode()}
        edited = fixture.node(files, (duplicate,))
        excluded = fixture.node({**files, 'binary': b'\0binary'}, (edited,))
        base2 = fixture.node({'base': b'new base\n'}, (root,))
        # Reordered relative to left, on a changed base, with two competing edits.
        rfiles = {'base': b'new base\n', hostile_path: ('alpha\nbeta\n'+hostile+'right\n').encode()}
        r1 = fixture.node(rfiles, (base2,))
        r2 = fixture.node({**rfiles, 'exact': b'exact\n'}, (r1,))
        r3 = fixture.node({**rfiles, 'exact': b'exact\n', 'competitor': b'alpha\nbeta\nother\n'}, (r2,))
        def snapshot():
            return {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in fixture.repo.rglob('*') if p.is_file()}
        common = b''.join(f'common {i}\n'.encode() for i in range(30))
        long_left = common + b''.join((f'left {i} ' + 'x'*200 + '\n').encode() for i in range(20))
        long_right = common + b''.join((f'right {i} ' + 'y'*200 + '\n').encode() for i in range(20))
        bounded_left = fixture.node({'f': long_left}, (root,))
        bounded_right, bounded_files = root, {}
        for i in range(7):
            bounded_files['f'+str(i)] = long_right
            bounded_right = fixture.node(bounded_files, (bounded_right,))
        split_a = fixture.node({'split': b'alpha\n'}, (root,))
        split_b = fixture.node({'split': b'alpha\nbeta\n'}, (split_a,))
        split_undo = fixture.node({'split': b'alpha\n'}, (split_b,))
        split_again = fixture.node({'split': b'alpha\nbeta\n'}, (split_undo,))
        squash = fixture.node({'split': b'alpha\nbeta\n'}, (root,))
        group_edited = fixture.node({'split': b'alpha\nbeta\ngamma\n'}, (split_a,))
        # Hostile, long evidence with both byte and item omissions for groups.
        group_long_a = fixture.node({'f': common}, (root,))
        group_long_b = fixture.node({'f': long_right + hostile.encode()}, (group_long_a,))
        before = snapshot()
        with tempfile.TemporaryDirectory(prefix='atlas-html-') as tmp:
            directory = Path(tmp)
            reports = []
            for name, extra in [('review', []), ('exhausted', ['--max-comparisons','0']),
                                ('sampled', ['--max-count','1']), ('bytes', ['--max-diff-bytes','1']), ('bounded', []), ('empty', []),
                                ('split', ['--groups']), ('squash', ['--groups']),
                                ('group-edited', ['--groups']), ('group-bounded', ['--groups']),
                                ('group-exhausted', ['--groups','--max-group-comparisons','0']),
                                ('group-omitted', ['--groups','--max-group-candidates','1']),
                                ('group-sampled', ['--groups','--max-groups','1'])]:
                revisions = [root, bounded_left, root, bounded_right] if name == 'bounded' else ([root]*4 if name == 'empty' else [root, excluded, base2, r3])
                if name in ('split','group-exhausted','group-omitted','group-sampled'):
                    revisions = [root,squash,root,split_again]
                elif name == 'squash': revisions = [root,split_again,root,squash]
                elif name == 'group-edited': revisions = [root,squash,root,group_edited]
                elif name == 'group-bounded': revisions = [root,bounded_left,root,group_long_b]
                command = [sys.executable, '-m', 'git_branch_atlas', '--repo', str(fixture.repo),
                           'series', *revisions, '--threshold', '3000', *extra]
                j = subprocess.run([*command, '--json'], cwd=ROOT, capture_output=True)
                h = subprocess.run([*command, '--html'], cwd=ROOT, capture_output=True)
                assert j.returncode == h.returncode == (0 if name in ('review', 'bounded', 'empty', 'split', 'squash', 'group-edited', 'group-bounded') else 1), (j.stderr,h.stderr)
                report = json.loads(j.stdout)
                (directory / (name+'.json')).write_bytes(j.stdout)
                (directory / (name+'.html')).write_bytes(h.stdout)
                reports.append(name)
            assert snapshot() == before, 'report generation changed repository'
            # Extra serializer-only payload: messages are not collected by schema v1.
            report = json.loads((directory/'review.json').read_text())
            report['left']['base']['revision'] = hostile + '\x00 @SCRIPT@ @DATA@'
            report['left']['commits'][0]['subject'] = hostile
            (directory/'hostile.json').write_text(json.dumps(report))
            (directory/'hostile.html').write_text(format_series_html(report)+'\n')
            reports.append('hostile')
            result = subprocess.run(['node', str(ROOT/'scripts/verify_html_browser.cjs'), str(directory), *reports],
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr)
            evidence = json.loads(result.stdout)
            evidence['fixture'] = 'Real Git: changed base, reordered exact patch, revert/reapply duplicate, competing edited patches, binary exclusion, exhausted comparisons/bytes/commits; extra hostile metadata serialization; split/squash, overlap, edited aggregate, group evidence bounds and exhausted group limits'
            evidence['repository_unchanged'] = True
            text = json.dumps(evidence, indent=2)+'\n'
            if args.output:
                args.output.write_text(text)
            print(text, end='')
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
