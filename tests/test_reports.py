"""Real Git fixtures plus an independent parent-set ancestry oracle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

from git_branch_atlas.reports import compare

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ENV = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
FIXTURE_ENV.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')


class ReportsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='atlas-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'répo 東京'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Atlas Test')
        self.git('config', 'user.email', 'atlas@example.invalid')
        self.parents = {}
        self.tree = self.git('mktree', input='')

    def git(self, *args, input=None):
        result = subprocess.run(['git', '-C', str(self.repo), *args], input=input,
                                text=True, capture_output=True, check=True, env=FIXTURE_ENV)
        return result.stdout.strip()

    def node(self, *parents, subject=None):
        subject = subject or f'node {len(self.parents)}'
        args = ['commit-tree', self.tree, '-m', subject]
        for parent in parents:
            args += ['-p', parent]
        oid = self.git(*args)
        self.parents[oid] = set(parents)
        return oid

    def tip(self, oid, name='main'):
        self.git('update-ref', 'refs/heads/' + name, oid)

    def atlas(self, *args, repo=None, env=None):
        return subprocess.run([sys.executable, '-m', 'git_branch_atlas', '--repo', str(repo or self.repo),
                               '--no-color', *args], cwd=ROOT, text=True, capture_output=True, env=env)

    def report(self, *args, repo=None):
        result = self.atlas(*args, '--json', repo=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def ancestors(self, oid):
        seen, pending = set(), [oid]
        while pending:
            node = pending.pop()
            if node not in seen:
                seen.add(node)
                pending.extend(self.parents[node])
        return seen

    def assert_oracle(self, a, b):
        report = compare(self.repo, a, b, 1000)
        left, right = self.ancestors(a), self.ancestors(b)
        common = left & right
        bases = {node for node in common if not any(node != other and node in self.ancestors(other)
                                                   for other in common)}
        self.assertEqual(report['left']['unique_count'], len(left - right))
        self.assertEqual(report['right']['unique_count'], len(right - left))
        self.assertEqual({c['oid'] for c in report['left']['commits']}, left - right)
        self.assertEqual({c['oid'] for c in report['right']['commits']}, right - left)
        self.assertEqual(set(report['merge_bases']), bases)
        self.assertEqual(report['unrelated'], not common)

    def test_ancestry_oracle_named_topologies_and_seeded_dags(self):
        root = self.node()
        a, b = self.node(root), self.node(root)
        merged = self.node(a, b)
        criss_a, criss_b = self.node(a, b), self.node(b, a)
        unrelated = self.node()
        for left, right in [(root, a), (a, b), (merged, a), (merged, b),
                            (criss_a, criss_b), (root, unrelated), (a, a)]:
            with self.subTest(left=left, right=right):
                self.assert_oracle(left, right)
        self.assertEqual(len(compare(self.repo, criss_a, criss_b, 1)['merge_bases']), 2)
        rng = random.Random(20260923)
        nodes = list(self.parents)
        for _ in range(32):
            parents = rng.sample(nodes, rng.choice([0, 1, 1, 2, 3]))
            nodes.append(self.node(*parents))
        for _ in range(24):
            self.assert_oracle(*rng.sample(nodes, 2))

    def test_cherry_pick_is_unique_despite_equal_patch(self):
        root = self.node()
        self.tip(root)
        (self.repo / 'change').write_text('same patch\n')
        self.git('add', 'change')
        self.git('commit', '-m', 'patch')
        patch = self.git('rev-parse', 'HEAD')
        self.parents[patch] = {root}
        base = self.node(root)
        self.tip(base, 'topic')
        self.git('switch', 'topic')
        self.git('cherry-pick', patch)
        picked = self.git('rev-parse', 'HEAD')
        self.parents[picked] = {base}
        self.assertNotEqual(picked, patch)
        self.assertEqual(self.git('diff', patch, picked), '')
        self.assert_oracle(patch, picked)
        report = self.report('compare', 'main', 'topic')
        self.assertEqual(report['left']['unique_count'], 1)
        self.assertEqual(report['right']['unique_count'], 2)

    def test_summary_upstreams_worktrees_and_preservation(self):
        root = self.node()
        tip = self.node(root)
        self.tip(tip)
        self.tip(root, 'topic')
        self.git('branch', '--set-upstream-to=topic', 'main')
        path = Path(self.temp.name) / 'linked 東京 space\ncarriage\rquote"\\tab\t'
        self.git('worktree', 'add', str(path), 'topic')
        self.git('worktree', 'lock', '--reason', 'hold\x1b[31m\nnow', str(path))
        gone = Path(self.temp.name) / 'gone'
        self.git('worktree', 'add', '--detach', str(gone), root)
        shutil.rmtree(gone)
        (self.repo / 'staged').write_text('indexed contents')
        self.git('add', 'staged')
        (self.repo / 'staged').write_text('unstaged contents')
        (self.repo / 'untracked').write_bytes(b'local\x00data')
        (path / 'private').write_text('do not inspect')
        def snapshot():
            return {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in Path(self.temp.name).rglob('*') if p.is_file()}
        before = snapshot()
        report = self.report('summary')
        main = next(b for b in report['branches'] if b['name'] == 'main')
        self.assertEqual((main['ahead'], main['behind']), (1, 0))
        self.assertEqual(main['upstream']['state'], 'present')
        linked = next(w for w in report['worktrees'] if w['path'] == str(path))
        self.assertTrue(linked['locked'])
        self.assertEqual(linked['lock_reason'], 'hold\x1b[31m\nnow')
        self.assertTrue(next(w for w in report['worktrees'] if w['path'] == str(gone))['prunable'])
        self.assertEqual(self.report('summary', repo=path)['head']['ref'], 'refs/heads/topic')
        self.report('compare', 'main', 'topic')
        self.assertEqual(self.atlas('graph').returncode, 0)
        terminal = self.atlas('summary').stdout
        self.assertIn('\\x1b[31m\\x0anow', terminal)
        self.assertNotIn('\x1b', terminal)
        self.assertEqual(snapshot(), before)
        self.git('config', 'branch.main.remote', 'origin')
        self.git('config', 'branch.main.merge', 'refs/heads/missing')
        self.git('config', 'remote.origin.url', 'https://example.invalid/repo')
        self.git('config', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')
        branch = next(b for b in self.report('summary')['branches'] if b['name'] == 'main')
        self.assertEqual(branch['upstream']['state'], 'missing')
        self.assertIsNone(branch['ahead'])
        self.git('config', '--unset', 'remote.origin.fetch')
        branch = next(b for b in self.report('summary')['branches'] if b['name'] == 'main')
        self.assertEqual(branch['upstream']['state'], 'unmapped')

    def test_unborn_bare_detached_and_unrelated(self):
        self.assertEqual(self.report('summary')['head']['state'], 'unborn')
        self.assertEqual(self.atlas('compare', 'HEAD', 'HEAD').returncode, 2)
        root = self.node()
        other = self.node()
        self.tip(root)
        self.tip(other, 'other')
        self.assertTrue(self.report('compare', 'main', 'other')['unrelated'])
        self.git('checkout', '--detach', root)
        self.assertEqual(self.report('summary')['head']['state'], 'detached')
        bare = Path(self.temp.name) / 'bare.git'
        self.git('clone', '--bare', str(self.repo), str(bare))
        self.assertTrue(self.report('summary', repo=bare)['bare'])
        self.assertTrue(self.report('compare', 'main', 'other', repo=bare)['unrelated'])
        self.assertEqual(self.atlas('graph', repo=bare).returncode, 0)

    def test_shallow_and_missing_history_are_not_definitive(self):
        root = self.node()
        tip = self.node(root)
        self.tip(tip)
        shallow = Path(self.temp.name) / 'shallow'
        self.git('clone', '--depth=1', self.repo.as_uri(), str(shallow))
        report = self.report('summary', repo=shallow)
        self.assertFalse(report['history']['complete'])
        self.assertTrue(report['warnings'])
        self.assertIsNone(report['branches'][0]['ahead'])
        result = self.atlas('compare', 'HEAD', 'HEAD', repo=shallow)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Shallow', result.stderr)
        self.git('commit-graph', 'write', '--reachable')
        (self.repo / '.git/objects' / root[:2] / root[2:]).unlink()
        for args in [('compare', 'main', 'main'), ('summary',), ('graph',)]:
            result = self.atlas(*args)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn('Incomplete', result.stderr)

    def test_offline_promisor_cannot_invoke_transport(self):
        root = self.node()
        tip = self.node(root)
        self.tip(tip)
        marker = Path(self.temp.name) / 'transport-called'
        helper = Path(self.temp.name) / 'git-remote-atlastest'
        helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
        helper.chmod(0o755)
        self.git('config', 'remote.origin.url', 'atlastest::fake')
        self.git('config', 'remote.origin.promisor', 'true')
        self.git('config', 'remote.origin.partialclonefilter', 'blob:none')
        self.git('config', 'protocol.atlastest.allow', 'always')
        (self.repo / '.git/objects' / root[:2] / root[2:]).unlink()
        before = {str(p): p.read_bytes() for p in (self.repo / '.git').rglob('*') if p.is_file()}
        env = os.environ.copy()
        env['PATH'] = self.temp.name + os.pathsep + env['PATH']
        result = self.atlas('compare', 'main', 'main', env=env)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(marker.exists())
        after = {str(p): p.read_bytes() for p in (self.repo / '.git').rglob('*') if p.is_file()}
        self.assertEqual(after, before)

    def test_ambiguity_limits_controls_and_invalid_arguments(self):
        root = self.node(subject='root\x1b[31m\u202e')
        a = self.node(root, subject='a\x07alert')
        tip = self.node(a)
        self.tip(tip)
        self.tip(root, 'base')
        self.git('tag', 'main', root)
        result = self.atlas('compare', 'main', 'base')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unambiguously', result.stderr)
        report = self.report('compare', 'refs/heads/main', 'base', '--max-count', '1')
        self.assertTrue(report['left']['truncated'])
        self.assertEqual(report['left']['unique_count'], 2)
        self.assertEqual(len(report['left']['commits']), 1)
        for args in [('compare', 'refs/heads/main', 'base'), ('graph',), ('summary',)]:
            result = self.atlas(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('\x1b', result.stdout)
            self.assertNotIn('\x07', result.stdout)
            self.assertNotIn('\u202e', result.stdout)
        for args in [('compare',), ('compare', 'HEAD'), ('summary', 'HEAD'), ('--json',),
                     ('compare', 'HEAD', 'HEAD', '--all'), ('--max-count', '0'),
                     ('compare', '--', '--all', 'HEAD'), ('compare', 'no\x1b[31m', 'HEAD')]:
            result = self.atlas(*args)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('\x1b', result.stderr)

    def test_replace_refs_ignored_and_grafts_rejected(self):
        root = self.node()
        tip = self.node(root)
        unrelated = self.node()
        self.tip(tip)
        self.git('replace', tip, unrelated)
        self.assert_oracle(tip, root)
        (self.repo / '.git/info/grafts').write_text(tip + '\n')
        result = self.atlas('compare', tip, root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('grafts', result.stderr)

    def test_graph_validates_tag_history_beyond_display_limit(self):
        root = self.node()
        tip = self.node(root)
        self.git('tag', 'only', tip)
        (self.repo / '.git/objects' / root[:2] / root[2:]).unlink()
        result = self.atlas('graph', '--all', '--max-count', '1')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Incomplete', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_option_order_notes_and_tag_only_unborn_graph(self):
        root = self.node()
        tip = self.node(root)
        self.tip(tip)
        self.git('notes', 'add', '-m', 'note must not become a commit row', tip)
        self.git('config', 'log.showNotes', 'true')
        result = self.atlas('compare', '--max-count', '1', tip, root, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['left']['commits']), 1)
        self.git('tag', 'only', tip)
        self.git('update-ref', '-d', 'refs/heads/main')
        result = self.atlas('graph', '--all')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('tag: only', result.stdout)
        self.assertEqual(self.atlas('graph', '--base', 'missing').returncode, 2)

    def test_sha256_object_ids(self):
        repo = Path(self.temp.name) / 'sha256'
        repo.mkdir()
        probe = subprocess.run(['git', '-C', str(repo), 'init', '--object-format=sha256', '-b', 'main'],
                               capture_output=True, env=FIXTURE_ENV)
        if probe.returncode:
            self.skipTest('installed Git lacks SHA-256 support')
        self.repo = repo
        self.git('config', 'user.name', 'Atlas Test')
        self.git('config', 'user.email', 'atlas@example.invalid')
        self.tree = self.git('mktree', input='')
        root = self.node()
        tip = self.node(root)
        self.tip(tip)
        self.assertEqual(len(tip), 64)
        self.assert_oracle(tip, root)
        self.assertEqual(self.report('summary')['branches'][0]['oid'], tip)

    def test_missing_tip_and_broken_ref_fail_without_partial_json(self):
        root = self.node()
        self.tip(root)
        (self.repo / '.git/refs/heads/broken').write_text('not-an-oid\n')
        result = self.atlas('summary', '--json')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        (self.repo / '.git/refs/heads/broken').unlink()
        (self.repo / '.git/objects' / root[:2] / root[2:]).unlink()
        result = self.atlas('summary', '--json')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')

    @unittest.skipIf(os.name == 'nt', 'POSIX byte filenames')
    def test_non_utf8_worktree_path_roundtrips_in_json(self):
        root = self.node()
        self.tip(root)
        path = Path(self.temp.name) / os.fsdecode(b'bytes-\xff')
        self.git('worktree', 'add', '--detach', str(path), root)
        report = self.report('summary')
        self.assertTrue(any(os.fsencode(tree['path']) == os.fsencode(path) for tree in report['worktrees']))
        self.assertIn('\\udcff', self.atlas('summary').stdout)

    def test_caller_git_environment_does_not_override_repo(self):
        self.tip(self.node())
        env = os.environ.copy()
        env['GIT_DIR'] = '/nonexistent/git-dir'
        env['GIT_CONFIG_COUNT'] = '1'
        env['GIT_CONFIG_KEY_0'] = 'alias.log'
        env['GIT_CONFIG_VALUE_0'] = '!false'
        self.assertEqual(self.atlas('summary', env=env).returncode, 0)


if __name__ == '__main__':
    unittest.main()
