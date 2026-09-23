"""Series fixtures, direct Git oracles and a separate list-based score reference."""
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_patches as fixture
from test_patches import ROOT, ENV
from git_branch_atlas.git import GitError
from git_branch_atlas.patches import Budget, Incomplete
from git_branch_atlas.series import (ALGORITHM, evidence, features, format_series_report,
                                     ordered, score, series_compare)


def reference(left_lines, left_paths, right_lines, right_paths):
    # Independent list matching, no production parser, Counter, or score helper.
    def normalize(lines):
        return [sign + bytes(v for v in line if v not in (9, 10, 11, 12, 13, 32))
                for sign, line in lines]
    a, b = normalize(left_lines), normalize(right_lines)
    remaining = list(b)
    common = 0
    for line in a:
        if line in remaining:
            common += 1
            remaining.remove(line)
    if common == 0:
        return 0
    from fractions import Fraction
    line_fraction = Fraction(2 * common, len(a) + len(b))
    paths = set(left_paths) | set(right_paths)
    path_fraction = Fraction(sum(p in left_paths and p in right_paths for p in paths), len(paths))
    return int(8000 * line_fraction + 2000 * path_fraction)


class SeriesTests(unittest.TestCase):
    setUp = fixture.PatchTests.setUp
    git = fixture.PatchTests.git
    node = fixture.PatchTests.node
    oracle = fixture.PatchTests.oracle

    def review(self, base, a, other_base, b, **kwargs):
        return series_compare(self.repo, base, a, other_base, b, **kwargs)

    def by_oid(self, report, side):
        return {c['oid']: c for c in report[side]['commits']}

    def test_100_seeded_histories_membership_patch_ids_and_independent_scores(self):
        for seed in range(100):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                root = self.node({})
                bases = [root, self.node({'base': b'unrelated base\n'}, (root,))]
                expected, tips = {}, []
                for side, base in zip(('left', 'right'), bases):
                    names = ['exact', 'edited', 'foreign-' + side]
                    rng.shuffle(names)
                    files = {} if side == 'left' else {'base': b'unrelated base\n'}
                    parent = base
                    expected[side] = {}
                    for name in names:
                        values = [b'common alpha', b'common beta', f'seed {seed}'.encode()]
                        if name == 'edited':
                            values += [(side + str(seed)).encode()] * rng.randint(1, 3)
                        if name.startswith('foreign'):
                            values = [(side + '-unique-' + str(seed)).encode()]
                        # Whitespace changes leave exact IDs and feature scoring stable.
                        if side == 'right':
                            values = [v.replace(b' ', b'\t') for v in values]
                        files[name] = b'\n'.join(values) + b'\n'
                        parent = self.node(files, (parent,))
                        expected[side][parent] = ([(b'+', v) for v in values], [name.encode()])
                    tips.append(parent)
                report = self.review(bases[0], tips[0], bases[1], tips[1])
                self.assertTrue(report['complete'])
                self.assertEqual(report['algorithm'], ALGORITHM)
                matched = {side: {o for g in report['matches'] for o in g[side]} for side in ('left', 'right')}
                for side, base, tip in zip(('left', 'right'), bases, tips):
                    actual = report[side]['commits']
                    direct = self.git('rev-list', '--reverse', tip, '^' + base, '--').decode().splitlines()
                    self.assertEqual([c['oid'] for c in actual], direct)
                    for item in actual:
                        self.assertEqual(item['patch_id'], self.oracle(item['oid']))
                        if item['oid'] in matched[side]:
                            self.assertEqual(item['status'], 'patch_id_match')
                            continue
                        other = 'right' if side == 'left' else 'left'
                        rows = []
                        for oid, data in expected[other].items():
                            if oid in matched[other]:
                                continue
                            value = reference(*expected[side][item['oid']], *data)
                            if value >= 5000:
                                rows.append((value, oid))
                        rows.sort(key=lambda row: (-row[0], row[1]))
                        self.assertEqual([(c['score'], c['oid']) for c in item['candidates']], rows)
                        self.assertEqual(item['status'], 'heuristic_candidates' if rows else 'no_candidate')
                self.assertEqual(report['comparison_count'], 4)

    def test_feature_parser_and_reference_repetitions_signs_whitespace_paths(self):
        rng = random.Random(301)
        for _ in range(300):
            inputs = []
            parsed = []
            for side in range(2):
                lines = [(rng.choice([b'+', b'-']), rng.choice([b'a a', b'bb', b'cc', b'aa', b'']))
                         for _ in range(rng.randint(1, 10))]
                paths = rng.sample([b'a', b'b', b'weird\n\xff'], rng.randint(1, 3))
                diff = b'diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@ fake context\n context\n' + b'\n'.join(sign + line for sign, line in lines)
                parsed.append(features(diff, paths, Budget(self.repo, 10, 100000)))
                inputs.append((lines, paths))
            self.assertEqual(score(*parsed), reference(*inputs[0], *inputs[1]))
            self.assertEqual(score(*parsed), score(*reversed(parsed)))

    def test_duplicates_revert_reapply_message_only_reorder_and_unrelated(self):
        root = self.node({})
        first = self.node({'f': b'yes\n'}, (root,))
        undo = self.node({}, (first,))
        again = self.node({'f': b'yes\n'}, (undo,))
        other_root = self.node({'base': b'different\n'})
        other = self.node({'base': b'different\n', 'f': b'yes\n'}, (other_root,))
        result = self.review(root, again, other_root, other)
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(result['matches'][0]['left'], [first, again])
        self.assertEqual(result['matches'][0]['right'], [other])
        self.assertEqual(self.by_oid(result, 'left')[undo]['status'], 'no_candidate')
        # Same object may legitimately occur in both explicit ranges.
        same = self.review(root, again, root, again)
        self.assertEqual(len(same['matches']), 2)
        self.assertEqual(same['comparison_count'], 0)
        # A base need not be an ancestor: standard reachability difference semantics.
        unrelated = self.review(other_root, again, root, other)
        self.assertTrue(unrelated['complete'])
        self.assertEqual({c['oid'] for c in unrelated['left']['commits']}, {root, first, undo, again})

    def test_merges_topology_and_exclusions(self):
        root = self.node({'f': b'base\n'})
        a = self.node({'f': b'one\n'}, (root,))
        b = self.node({'f': b'two\n'}, (root,))
        merge = self.node({'f': b'both\n'}, (a, b))
        report = self.review(root, merge, root, root)
        self.assertEqual([c['oid'] for c in report['left']['commits']], sorted([a, b]) + [merge])
        self.assertEqual(report['left']['commits'][-1]['reason'], 'merge')
        self.assertEqual(ordered([merge+' '+a+' '+b, b+' '+root, a+' '+root]),
                         [(o, [root]) for o in sorted([a, b])] + [(merge, [a, b])])
        cases = [({'f': b'a\0b'}, None, 'binary'), ({'f': b'base\n'}, {'f': '100755'}, 'mode change'),
                 ({'f': b'target'}, {'f': '120000'}, 'symlink'),
                 ({'sub': root.encode()}, {'sub': '160000'}, 'submodule'),
                 ({'f': b'base\n'}, None, 'empty')]
        for files, modes, reason in cases:
            oid = self.node(files, (root,), modes)
            result = self.review(root, oid, root, root)
            self.assertTrue(result['complete'])
            item = result['left']['commits'][0]
            self.assertEqual(item['status'], 'excluded')
            self.assertIn(reason, item['reason'])

    def test_ties_top_five_threshold_evidence_and_limits(self):
        root = self.node({})
        a = self.node({'f': b'common\nold\n'}, (root,))
        parent, files, candidates = root, {}, []
        for i in range(7):
            files['f' + str(i)] = b'common\nnew\n'
            parent = self.node(files, (parent,))
            candidates.append(parent)
        result = self.review(root, a, root, parent, threshold=4000)
        item = result['left']['commits'][0]
        self.assertEqual(item['candidate_count'], 7)
        self.assertEqual(item['top_tie_count'], 7)
        self.assertTrue(item['ambiguous'])
        self.assertEqual(item['candidates_omitted'], 2)
        self.assertEqual([c['oid'] for c in item['candidates']], sorted(candidates)[:5])
        self.assertEqual({c['rank'] for c in item['candidates']}, {1})
        self.assertEqual({c['score'] for c in item['candidates']}, {4000})
        ev = item['candidates'][0]['evidence']
        self.assertEqual(ev['source_only']['items'][0]['text'], '+old')
        self.assertEqual(ev['counterpart_only']['items'][0]['text'], '+new')
        self.assertEqual(self.review(root, a, root, parent, threshold=4001)['left']['commits'][0]['status'], 'no_candidate')
        for kwargs in [dict(max_count=1), dict(max_bytes=1), dict(max_comparisons=0), dict(max_comparisons=2)]:
            limited = self.review(root, a, root, parent, **kwargs)
            self.assertFalse(limited['complete'])
            for side in ('left', 'right'):
                self.assertNotIn('no_candidate', [c['status'] for c in limited[side]['commits']])
        with self.assertRaisesRegex(GitError, 'output byte limit'):
            format_series_report(result, True, 1024)
        with patch('git_branch_atlas.series.FEATURE_LIMIT', 1):
            limited = self.review(root, a, root, parent)
            self.assertFalse(limited['complete'])
            self.assertIn('feature limit', limited['left']['commits'][0]['reason'])
        for kwargs in [dict(max_count=1001), dict(seconds=float('nan')), dict(threshold=0), dict(max_comparisons=-1)]:
            with self.assertRaises(GitError):
                self.review(root, a, root, parent, **kwargs)
        with patch('git_branch_atlas.series.check_time', side_effect=Incomplete('runtime limit')):
            limited = self.review(root, a, root, parent)
            self.assertFalse(limited['complete'])

    def test_unusual_paths_hostile_config_terminal_escaping_and_unchanged_repository(self):
        root = self.node({})
        names = ['line\nname', 'tab\tname', 'esc\x1b', '東京', os.fsdecode(b'bad-\xff')]
        a = self.node({n: b'shared\n\x1b[31mleft\n' for n in names}, (root,))
        b = self.node({n: b'shared\n\x1b[31mright\n' for n in names}, (root,))
        normal = self.review(root, a, root, b)
        marker = Path(self.temp.name) / 'executed'
        helper = Path(self.temp.name) / 'helper'
        helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
        helper.chmod(0o755)
        for k in ('diff.external', 'diff.evil.command', 'diff.evil.textconv'):
            self.git('config', k, str(helper))
        (self.repo / '.git/info/attributes').write_text('* diff=evil\n')
        (self.repo / 'staged').write_text('stage')
        self.git('add', 'staged')
        (self.repo / 'staged').write_text('dirty')
        def snapshot():
            return {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in self.repo.rglob('*') if p.is_file()}
        before = snapshot()
        report = self.review(root, a, root, b)
        self.assertEqual(normal, report)
        self.assertEqual(before, snapshot())
        self.assertFalse(marker.exists())
        self.assertNotIn('\x1b', format_series_report(report))
        self.assertEqual(json.loads(format_series_report(report, True)), report)
        a_features = features(b'@@ -1 +1 @@\n+' + b'x'*200 + b'\n', [b'a'], Budget(self.repo, 1, 10000))
        ev = evidence(a_features, features(b'', [b'b'], Budget(self.repo, 1, 10000)))
        self.assertTrue(ev['source_only']['items'][0]['truncated'])

    def test_missing_objects_shallow_and_no_lazy_transport(self):
        root = self.node({})
        a = self.node({'f': b'one\n'}, (root,))
        b = self.node({'f': b'two\n'}, (root,))
        self.git('update-ref', 'refs/heads/main', a)
        shallow = Path(self.temp.name) / 'shallow'
        self.git('clone', '--depth=1', self.repo.as_uri(), str(shallow))
        result = series_compare(shallow, 'HEAD', 'HEAD', 'HEAD', 'HEAD')
        self.assertFalse(result['complete'])
        self.assertIn('shallow', result['warnings'][0])
        blob = self.git('rev-parse', a + ':f').decode()
        marker = Path(self.temp.name) / 'downloaded'
        helper = Path(self.temp.name) / 'git-remote-atlastest'
        helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
        helper.chmod(0o755)
        self.git('config', 'remote.origin.url', 'atlastest::fake')
        self.git('config', 'remote.origin.promisor', 'true')
        self.git('config', 'protocol.atlastest.allow', 'always')
        (self.repo / '.git/objects' / blob[:2] / blob[2:]).unlink()
        with patch.dict(os.environ, PATH=self.temp.name + os.pathsep + os.environ['PATH']):
            result = self.review(root, a, root, b)
        self.assertFalse(result['complete'])
        self.assertFalse(marker.exists())
        self.assertEqual(result['right']['commits'][0]['status'], 'incomplete_search')
        (self.repo / '.git/objects' / root[:2] / root[2:]).unlink()
        result = self.review(b, b, b, b)
        self.assertFalse(result['complete'])

    def test_context_changes_perfect_heuristic_and_no_final_newline(self):
        left_base = self.node({'f': b'context one\nold\n'})
        right_base = self.node({'f': b'context two\nold\n'})
        a = self.node({'f': b'context one\nnew\n'}, (left_base,))
        b = self.node({'f': b'context two\nnew\n'}, (right_base,))
        result = self.review(left_base, a, right_base, b)
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['left']['commits'][0]['candidates'][0]['score'], 10000)
        self.assertEqual(result['left']['commits'][0]['status'], 'heuristic_candidates')
        root = self.node({})
        no_lf = self.node({'f': b'old'}, (root,))
        no_lf_edit = self.node({'f': b'new'}, (no_lf,))
        result = self.review(root, no_lf, no_lf, no_lf_edit)
        self.assertEqual(result['left']['commits'][0]['status'], 'no_candidate')
        # A rename is delete/add, not an inferred identity between files.
        renamed = self.node({'g': b'old'}, (no_lf,))
        renamed2 = self.node({'g': b'old'}, (no_lf,))
        self.assertEqual(len(self.review(no_lf, renamed, no_lf, renamed2)['matches']), 1)

    def test_empty_bare_sha256_revisions_and_provisional_rankings(self):
        sha = Path(self.temp.name) / 'sha'
        sha.mkdir()
        subprocess.run(['git', '-C', str(sha), 'init', '--object-format=sha256', '-b', 'main'],
                       check=True, capture_output=True, env=ENV)
        self.repo = sha
        self.git('config', 'user.name', 'Series')
        self.git('config', 'user.email', 'series@example.invalid')
        root = self.node({})
        a = self.node({'f': b'alpha\nbeta\nold\n'}, (root,))
        b = self.node({'f': b'alpha\nbeta\nnew\n'}, (root,))
        b2 = self.node({'f': b'alpha\nbeta\nnew\n', 'g': b'foreign\n'}, (b,))
        result = self.review(root, a, root, b2, max_comparisons=1)
        self.assertFalse(result['complete'])
        item = result['left']['commits'][0]
        self.assertEqual(len(item['oid']), 64)
        self.assertEqual(item['status'], 'heuristic_candidates')
        self.assertFalse(item['search_complete'])
        self.assertEqual(item['candidates'][0]['score'], 7333)
        self.assertTrue(self.review(root, root, root, root, max_comparisons=0)['complete'])
        self.git('update-ref', 'refs/heads/main', a)
        self.git('tag', 'annotated', a, '-m', 'tag')
        self.assertEqual(self.review(root, 'annotated', root, b)['left']['tip']['oid'], a)
        self.git('tag', 'main', b)
        for bad in ('--all', 'main', 'missing', 'a'*4097):
            with self.assertRaises(GitError):
                self.review(root, bad, root, b)
        bare = Path(self.temp.name) / 'bare.git'
        self.git('clone', '--bare', str(self.repo), str(bare))
        self.assertTrue(series_compare(bare, root, a, root, b)['complete'])
        result = subprocess.run([sys.executable, '-m', 'git_branch_atlas', 'series', root, a],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('LEFT_BASE LEFT_TIP', result.stderr)

    def test_cli_rebase_and_range_diff_disagreements(self):
        root = self.node({})
        self.git('update-ref', 'refs/heads/main', root)
        (self.repo / 'feature').write_text('alpha\nbeta\nold\n')
        self.git('add', 'feature')
        self.git('commit', '-m', 'feature')
        original = self.git('rev-parse', 'HEAD').decode()
        self.git('branch', 'before', original)
        self.git('checkout', '-b', 'base', root)
        (self.repo / 'base').write_text('base\n')
        self.git('add', 'base')
        self.git('commit', '-m', 'new base')
        new_base = self.git('rev-parse', 'HEAD').decode()
        self.git('checkout', 'main')
        self.git('rebase', '--onto', new_base, root)
        copied = self.git('rev-parse', 'HEAD').decode()
        exact = self.review(root, original, new_base, copied)
        self.assertEqual(exact['matches'][0]['left'], [original])
        self.assertIn(' = ', self.git('range-diff', '--no-color', root+'..'+original, new_base+'..'+copied).decode())
        self.git('commit', '--amend', '-m', 'a totally different message')
        message_only = self.git('rev-parse', 'HEAD').decode()
        self.assertEqual(len(self.review(root, original, new_base, message_only)['matches']), 1)
        self.assertIn(' ! ', self.git('range-diff', '--no-color', root+'..'+original, new_base+'..'+message_only).decode())
        (self.repo / 'feature').write_text('alpha\nbeta\nnew\n')
        self.git('add', 'feature')
        self.git('commit', '--amend', '--no-edit')
        edited = self.git('rev-parse', 'HEAD').decode()
        report = self.review(root, original, new_base, edited)
        self.assertEqual(report['left']['commits'][0]['candidates'][0]['score'], 7333)
        # range-diff is an observation, not the scoring oracle.
        observed = self.git('range-diff', '--no-color', root+'..'+original, new_base+'..'+edited).decode()
        self.assertIn('feature', observed)
        for extra, code in [([], 0), (['--max-comparisons', '0'], 1), (['--max-output-bytes', '1024'], 2)]:
            result = subprocess.run([sys.executable, '-m', 'git_branch_atlas', '--repo', str(self.repo),
                                     'series', root, original, new_base, edited, '--json', *extra],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, code, result.stderr)
            if code == 2:
                self.assertEqual(result.stdout, '')
            else:
                self.assertEqual(json.loads(result.stdout)['complete'], code == 0)


if __name__ == '__main__':
    unittest.main()
