"""Independent fixture expectations, Git plumbing oracles and resource regressions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from git_branch_atlas.git import GitError
from git_branch_atlas.patches import Budget, DIFF, Incomplete, format_patch_report, patch_compare

ROOT = Path(__file__).resolve().parents[1]
ENV = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
ENV.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='atlas-patches-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Patch Test')
        self.git('config', 'user.email', 'patch@example.invalid')
        self.sequence = 0

    def git(self, *args, data=None):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], input=data, env=ENV,
                                       stderr=subprocess.PIPE).strip()

    def node(self, files, parents=(), modes=None):
        records = []
        for name, data in sorted(files.items()):
            mode = (modes or {}).get(name, '100644')
            oid = data if mode == '160000' else self.git('hash-object', '-w', '--stdin', data=data)
            records.append(mode.encode() + (b' commit ' if mode == '160000' else b' blob ') + oid + b'\t' + os.fsencode(name) + b'\0')
        tree = self.git('mktree', '-z', data=b''.join(records)).decode()
        self.sequence += 1
        return self.git('commit-tree', tree, *[s for p in parents for s in ('-p', p)],
                        '-m', f'fixture {self.sequence}').decode()

    def report(self, a, b, **kwargs):
        return patch_compare(self.repo, a, b, **kwargs)

    def oracle(self, oid):
        diff = self.git('diff-tree', '--root', '--no-commit-id', '-r', '-p', *DIFF, oid, '--') + b'\n'
        return self.git('patch-id', '--stable', data=diff).split()[0].decode()

    def groups(self, report):
        return {(tuple(g['left']), tuple(g['right'])) for g in report['matches']}

    def test_100_seeded_histories_with_independent_groups_plumbing_and_cherry(self):
        for seed in range(100):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                root = self.node({})
                common = ['shared' + str(i) for i in range(rng.randint(1, 3))]
                sequences = {}
                expected = {'left': {}, 'right': {}}
                for side in ('left', 'right'):
                    names = common + [side + str(seed)]
                    rng.shuffle(names)
                    parent, files = root, {}
                    for name in names:
                        # Independently known equivalent insertions, including whitespace normalization.
                        files[name] = (f'value {seed}\n' if side == 'left' else f'value\t{seed}\n').encode()
                        parent = self.node(files, (parent,))
                        expected[side].setdefault(name, []).append(parent)
                        if name == common[0] and rng.choice([True, False]):
                            del files[name]
                            parent = self.node(files, (parent,))
                            expected[side].setdefault('revert:' + name, []).append(parent)
                            files[name] = f'value {seed}\n'.encode()
                            parent = self.node(files, (parent,))
                            expected[side][name].append(parent)
                    sequences[side] = parent
                report = self.report(sequences['left'], sequences['right'])
                self.assertTrue(report['complete'])
                matching = expected['left'].keys() & expected['right'].keys()
                groups = {(tuple(sorted(expected['left'][k])), tuple(sorted(expected['right'][k]))) for k in matching}
                self.assertEqual(self.groups(report), groups)
                for group in report['matches']:
                    for oid in group['left'] + group['right']:
                        self.assertEqual(group['patch_id'], self.oracle(oid))
                for side, other in [('left', 'right'), ('right', 'left')]:
                    unmatched = {oid for key in expected[side].keys() - expected[other].keys() for oid in expected[side][key]}
                    self.assertEqual({v['oid'] for v in report[side]['unmatched']}, unmatched)
                    cherry = self.git('cherry', sequences[other], sequences[side]).decode().splitlines()
                    self.assertEqual({line[2:] for line in cherry if line.startswith('+')}, unmatched)

    def test_revert_reapply_does_not_describe_current_contents(self):
        root = self.node({})
        add = self.node({'f': b'yes\n'}, (root,))
        undo = self.node({}, (add,))
        again = self.node({'f': b'yes\n'}, (undo,))
        other = self.node({'f': b'yes\n'}, (root,))
        gone = self.node({}, (other,))
        report = self.report(again, gone)
        self.assertIn((tuple(sorted([add, again])), (other,)), self.groups(report))
        self.assertNotEqual(self.git('rev-parse', again + '^{tree}'), self.git('rev-parse', gone + '^{tree}'))
        self.assertIn('current branch contents', report['notice'])

    def test_roots_renames_modes_binary_symlink_submodule_empty_merge(self):
        root = self.node({'old': b'text\n'})
        unrelated = self.node({'old': b'text\n'})
        self.assertEqual(self.groups(self.report(root, unrelated)), {((root,), (unrelated,))})
        rename = self.node({'new': b'text\n'}, (root,))
        rename2 = self.node({'new': b'text\n'}, (unrelated,))
        self.assertEqual(self.groups(self.report(rename, rename2)), {((root,), (unrelated,)), ((rename,), (rename2,))})
        cases = [(self.node({'old': b'a\0b'}, (root,)), 'binary'),
                 (self.node({'old': b'text\n'}, (root,), {'old': '100755'}), 'mode change'),
                 (self.node({'old': b'target'}, (root,), {'old': '120000'}), 'symlink'),
                 (self.node({'module': root.encode()}, (root,), {'module': '160000'}), 'submodule'),
                 (self.node({'old': b'text\n'}, (root,)), 'empty'),
                 (self.node({'old': b'text\n'}, (root, unrelated)), 'merge')]
        for oid, reason in cases:
            with self.subTest(reason=reason):
                result = self.report(oid, root)
                self.assertTrue(result['complete'])
                self.assertTrue(any(reason in v['reason'] for v in result['left']['excluded']))
                expected_unmatched = {unrelated} if reason == 'merge' else set()
                self.assertEqual({v['oid'] for v in result['left']['unmatched']}, expected_unmatched)

    def test_limits_never_imply_unmatched(self):
        root = self.node({})
        a = self.node({'f': b'one\n'}, (root,))
        a2 = self.node({'f': b'two\n'}, (a,))
        b = self.node({'g': b'other\n'}, (root,))
        for kwargs in [dict(max_count=1), dict(max_bytes=1)]:
            result = self.report(a2, b, **kwargs)
            self.assertFalse(result['complete'])
            for side in ('left', 'right'):
                self.assertEqual(result[side]['unmatched'], [])
            self.assertTrue(result['left']['unclassified'])
        result = self.report(a2, b)
        with self.assertRaisesRegex(GitError, 'output byte limit'):
            format_patch_report(result, True, 1024)
        for kwargs in [dict(seconds=float('nan')), dict(seconds=float('inf')), dict(max_count=10001)]:
            with self.assertRaises(GitError):
                self.report(a, b, **kwargs)
        with self.assertRaises(Incomplete):
            Budget(self.repo, 0.000000001, 10).run(['status'])

    def test_actual_subprocess_deadline(self):
        helper = Path(self.temp.name) / 'git'
        helper.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n')
        helper.chmod(0o755)
        with patch.dict(os.environ, PATH=self.temp.name + os.pathsep + os.environ['PATH']):
            with self.assertRaisesRegex(Incomplete, 'runtime'):
                Budget(self.repo, 0.1, 100).run(['unused'])

    def test_missing_blobs_ancestors_shallow_and_offline_transport(self):
        root = self.node({})
        a = self.node({'f': b'one\n'}, (root,))
        b = self.node({'f': b'two\n'}, (root,))
        self.git('update-ref', 'refs/heads/main', a)
        self.git('update-ref', 'refs/heads/other', b)
        shallow = Path(self.temp.name) / 'shallow'
        self.git('clone', '--depth=1', self.repo.as_uri(), str(shallow))
        result = patch_compare(shallow, 'HEAD', 'HEAD')
        self.assertFalse(result['complete'])
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
            result = self.report(a, b)
        self.assertFalse(marker.exists())
        self.assertFalse(result['complete'])
        self.assertEqual(result['right']['unmatched'], [])
        self.assertTrue(result['left']['unclassified'])
        (self.repo / '.git/objects' / root[:2] / root[2:]).unlink()
        self.assertFalse(self.report(b, b)['complete'])

    def test_hostile_config_attributes_unusual_paths_and_preservation(self):
        root = self.node({})
        names = ['space name', 'line\nname', 'tab\tname', 'escape\x1bname', 'back\\slash', '東京', os.fsdecode(b'byte-\xff')]
        files = {name: b'hello\n' for name in names}
        a, b = self.node(files, (root,)), self.node(files, (root,))
        expected = self.report(a, b)
        self.git('update-ref', 'refs/heads/main', a)
        self.git('update-ref', 'refs/heads/control\u202e', b)
        marker = Path(self.temp.name) / 'called'
        helper = Path(self.temp.name) / 'helper'
        helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
        helper.chmod(0o755)
        for key, value in [('diff.external', str(helper)), ('diff.evil.command', str(helper)),
                           ('diff.evil.textconv', str(helper)), ('diff.algorithm', 'histogram'),
                           ('diff.renames', 'true'), ('diff.context', '0'), ('diff.noprefix', 'true'),
                           ('diff.ignoreSubmodules', 'all'), ('diff.relative', 'true'), ('patchid.stable', 'false')]:
            self.git('config', key, value)
        (self.repo / '.gitattributes').write_text('* diff=evil\n')
        (self.repo / '.git/info/attributes').write_text('* diff=evil\n')
        (self.repo / 'staged').write_text('staged')
        self.git('add', 'staged')
        (self.repo / 'staged').write_text('dirty')
        def snapshot():
            return {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in self.repo.rglob('*') if p.is_file()}
        before = snapshot()
        self.assertEqual(self.report(a, b), expected)
        terminal = format_patch_report(self.report('main', 'control\u202e'))
        self.assertNotIn('\u202e', terminal)
        self.assertIn('\\u202e', terminal)
        self.assertFalse(marker.exists())
        self.assertEqual(before, snapshot())
        (self.repo / '.git/info/attributes').write_text('* -diff\n')
        self.assertEqual(self.report(a, b), expected)
        inside = self.repo / 'interior'
        inside.mkdir()
        self.assertEqual(patch_compare(inside, a, b), expected)
        self.assertFalse(marker.exists())
        self.git('tag', 'main', b)
        with self.assertRaises(GitError):
            self.report('main', b)

    def test_sha256_and_partial_matching_groups(self):
        sha = Path(self.temp.name) / 'sha256'
        sha.mkdir()
        subprocess.run(['git', '-C', str(sha), 'init', '--object-format=sha256', '-b', 'main'],
                       check=True, capture_output=True, env=ENV)
        self.repo = sha
        self.git('config', 'user.name', 'Patch Test')
        self.git('config', 'user.email', 'patch@example.invalid')
        root = self.node({})
        a = self.node({'f': b'yes\n'}, (root,))
        b = self.node({'f': b'yes\n'}, (root,))
        report = self.report(a, b)
        self.assertEqual(len(report['left']['oid']), 64)
        self.assertEqual(report['matches'][0]['patch_id'], self.oracle(a))
        a2 = self.node({}, (a,))
        a3 = self.node({'f': b'yes\n'}, (a2,))
        partial = self.report(a3, b, max_count=1)
        self.assertFalse(partial['complete'])
        self.assertEqual(self.groups(partial), {((a3,), (b,))})
        self.assertEqual(partial['left']['unmatched'], [])

    def test_cli_cherry_pick_rebase_and_bare_install_use_case(self):
        root = self.node({})
        self.git('update-ref', 'refs/heads/main', root)
        (self.repo / 'feature').write_text('feature\n')
        self.git('add', 'feature')
        self.git('commit', '-m', 'feature')
        original = self.git('rev-parse', 'HEAD').decode()
        self.git('checkout', '-b', 'topic', root)
        (self.repo / 'base').write_text('base\n')
        self.git('add', 'base')
        self.git('commit', '-m', 'base')
        base = self.git('rev-parse', 'HEAD').decode()
        self.git('cherry-pick', original)
        copied = self.git('rev-parse', 'HEAD').decode()
        self.assertEqual(self.groups(self.report('main', 'topic')), {((original,), (copied,))})
        self.git('branch', 'before-rebase')
        self.git('rebase', '--onto', root, base, 'topic')
        rebased = self.git('rev-parse', 'HEAD').decode()
        self.assertEqual(self.groups(self.report('before-rebase', 'topic')), {((copied,), (rebased,))})
        bare = Path(self.temp.name) / 'bare.git'
        self.git('clone', '--bare', str(self.repo), str(bare))
        for target in (self.repo, bare):
            run = subprocess.run([sys.executable, '-m', 'git_branch_atlas', '--repo', str(target),
                                  'patches', 'before-rebase', 'topic', '--json'], cwd=ROOT,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(json.loads(run.stdout)['matches'])
        run = subprocess.run([sys.executable, '-m', 'git_branch_atlas', '--repo', str(self.repo),
                              'patches', 'before-rebase', 'topic', '--max-diff-bytes', '1', '--json'],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertFalse(json.loads(run.stdout)['complete'])


if __name__ == '__main__':
    unittest.main()
