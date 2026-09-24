"""Endpoint and exhaustive candidate oracles independent of production enumeration."""
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_patches as fixture
from test_patches import ROOT
from test_series import reference
from git_branch_atlas.git import GitError
from git_branch_atlas.patches import Incomplete
from git_branch_atlas.series import series_compare, format_series_report
from git_branch_atlas.html_report import format_series_html


class GroupTests(unittest.TestCase):
    setUp = fixture.PatchTests.setUp
    git = fixture.PatchTests.git
    node = fixture.PatchTests.node

    def review(self, base, left, other_base, right, **kwargs):
        return series_compare(self.repo, base, left, other_base, right, include_groups=True, **kwargs)

    def snapshot(self):
        return {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in self.repo.rglob('*') if p.is_file()}

    def test_200_seeded_endpoint_and_exhaustive_candidate_oracles(self):
        for seed in range(200):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                states, parents = {}, {}
                def node(files, ps=()):
                    oid = self.node(files, ps)
                    states[oid], parents[oid] = dict(files), list(ps)
                    return oid
                root = node({})
                tips = []
                for side in ('left', 'right'):
                    tip = root
                    for i in range(rng.randint(2, 6)):
                        files = dict(states[tip])
                        name = rng.choice(['a', 'b', 'c'])
                        if rng.randrange(4) == 0:
                            files.pop(name, None)
                        else:
                            files[name] = rng.choice([b'alpha\n', b'beta\n', b'alpha \n', b'gamma\n'])
                        tip = node(files, (tip,))
                    if seed % 5 == 0:
                        branch = node({'d': b'branch\n'}, (root,))
                        tip = node({**states[tip], 'd': b'branch\n'}, (tip, branch))
                        tip = node({**states[tip], 'e': b'after merge\n'}, (tip,))
                    tips.append(tip)
                threshold = rng.choice([3000, 5000, 10000])
                report = self.review(root, tips[0], root, tips[1], threshold=threshold,
                                     max_groups=100, max_group_candidates=2000)
                result = report['group_comparison']
                self.assertTrue(report['complete'], result['warnings'])
                expected_windows, singles = {}, {}
                # Exhaust all permutations; adjacency is checked against fixture parent
                # records, never production windows/order. Rev-list only selects membership.
                for side, tip in zip(('left', 'right'), tips):
                    members = self.git('rev-list', tip, '^'+root).decode().splitlines()
                    self.assertEqual(set(members), {c['oid'] for c in report[side]['commits']})
                    singles[side] = [o for o in members if len(parents[o]) == 1 and states[o] != states[parents[o][0]]]
                    for size in (2, 3, 4):
                        for seq in itertools.permutations(members, size):
                            if all(len(parents[o]) == 1 for o in seq) and all(parents[b] == [a] for a, b in zip(seq, seq[1:])):
                                expected_windows[side, seq] = (parents[seq[0]][0], seq[-1])
                self.assertEqual({(g['side'], tuple(g['members'])) for g in result['groups']}, set(expected_windows))
                self.assertEqual(result['structural_windows'], len(expected_windows))
                def changes(base, tip):
                    old, new = states[base], states[tip]
                    names = [n for n in sorted(old.keys() | new.keys()) if old.get(n) != new.get(n)]
                    lines = []
                    for n in names:
                        if n in old: lines.append((b'-', old[n].rstrip(b'\n')))
                        if n in new: lines.append((b'+', new[n].rstrip(b'\n')))
                    return lines, [n.encode() for n in names]
                def patch_id(base, tip):
                    # Independent Git endpoint diff, not concatenated member patches,
                    # production inspect_patch, or the production DIFF option constant.
                    diff = self.git('diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                                    '--text', '--full-index', '--unified=3', base, tip, '--')
                    return self.git('patch-id', '--stable', data=diff+b'\n').split()[0].decode()
                expected_candidates = set()
                pid_cache = {}
                def pid(base, tip):
                    if (base, tip) not in pid_cache: pid_cache[base, tip] = patch_id(base, tip)
                    return pid_cache[base, tip]
                possible = 0
                for g in result['groups']:
                    base, tip = expected_windows[g['side'], tuple(g['members'])]
                    self.assertEqual((g['base_oid'], g['tip_oid'], g['base_kind']), (base, tip, 'commit'))
                    if states[base] == states[tip]:
                        self.assertEqual(g['reason'], 'empty aggregate patch')
                        continue
                    self.assertEqual(g['status'], 'eligible')
                    self.assertEqual(g['patch_id'], pid(base, tip))
                    other = 'right' if g['side'] == 'left' else 'left'
                    for single in singles[other]:
                        possible += 1
                        sb = parents[single][0]
                        value = reference(*changes(sb, single), *changes(base, tip))
                        agreement = pid(sb, single) == pid(base, tip)
                        if agreement or value >= threshold:
                            expected_candidates.add((other, single, g['id'], value, agreement))
                self.assertEqual(result['comparison_count'], possible)
                self.assertEqual({(c['single_side'], c['single_oid'], c['group_id'], c['score'], c['normalized_patch_agreement']) for c in result['candidates']}, expected_candidates)
                self.assertEqual(result['candidate_count'], len(expected_candidates))

    def test_known_split_squash_edited_overlaps_duplicates_and_reverts(self):
        root = self.node({})
        single = self.node({'f': b'alpha\nbeta\n'}, (root,))
        a = self.node({'f': b'alpha\n'}, (root,))
        b = self.node({'f': b'alpha\nbeta\n'}, (a,))
        undo = self.node({'f': b'alpha\n'}, (b,))
        again = self.node({'f': b'alpha\nbeta\n'}, (undo,))
        for reverse in (False, True):
            tips = (again, single) if reverse else (single, again)
            report = self.review(root, tips[0], root, tips[1])
            g = report['group_comparison']
            exact = [c for c in g['candidates'] if c['normalized_patch_agreement']]
            self.assertEqual(len(exact), 2) # [a,b], [a,b,undo,again]
            self.assertTrue(all(c['ambiguous'] for c in exact))
            groups = {x['id']: x for x in g['groups']}
            self.assertEqual({tuple(groups[c['group_id']]['members']) for c in exact}, {(a,b), (a,b,undo,again)})
            self.assertTrue(any(x.get('reason') == 'empty aggregate patch' for x in g['groups']))
            self.assertTrue(all(not any(c['evidence'][k]['items'] for k in ('source_only','counterpart_only','paths_source_only','paths_counterpart_only')) for c in exact))
            baseline = series_compare(self.repo, root, tips[0], root, tips[1])
            for key in ('left', 'right', 'matches', 'comparison_count', 'algorithm'):
                self.assertEqual(report[key], baseline[key])
            self.assertEqual(baseline['schema_version'], 1)
            self.assertNotIn('group_comparison', baseline)
            self.assertEqual(json.loads(format_series_report(report, True)), report)
            self.assertIn('normalized_patch_agreement=True', format_series_report(report))
        edited = self.node({'f': b'alpha\nbeta\ngamma\n'}, (a,))
        result = self.review(root, single, root, edited)['group_comparison']
        c = result['candidates'][0]
        self.assertFalse(c['normalized_patch_agreement'])
        self.assertEqual(c['score'], 8400)
        self.assertEqual(c['evidence']['counterpart_only']['items'][0]['text'], '+gamma')
        # Intervening change must not disappear by skipping a middle commit.
        middle = self.node({'f': b'alpha\n', 'intervening': b'unrelated\n'}, (a,))
        end = self.node({'f': b'alpha\nbeta\n', 'intervening': b'unrelated\n'}, (middle,))
        result = self.review(root, single, root, end)['group_comparison']
        self.assertFalse(any(c['normalized_patch_agreement'] for c in result['candidates']))
        self.assertNotIn([a,end], [g['members'] for g in result['groups']])

    def test_unsupported_intermediate_histories_merges_roots_and_modes(self):
        root = self.node({})
        single = self.node({'f': b'ok\n'}, (root,))
        for files, modes in [({'f': b'\0binary'}, None), ({'f': b'target'}, {'f':'120000'}),
                             ({'sub': root.encode()}, {'sub':'160000'})]:
            a = self.node(files, (root,), modes)
            b = self.node({'f': b'ok\n'}, (a,))
            result = self.review(root, single, root, b)['group_comparison']
            self.assertEqual(result['groups'][0]['status'], 'excluded')
            self.assertIn('unsupported member', result['groups'][0]['reason'])
            self.assertEqual(result['candidates'], [])
        a = self.node({'f': b'ok\n'}, (single,), {'f':'100755'})
        b = self.node({'f': b'ok\n'}, (a,))
        result = self.review(root, single, root, b)['group_comparison']
        self.assertTrue(all(g['status']=='excluded' for g in result['groups']))
        fork = self.node({'g': b'other\n'}, (root,))
        merge = self.node({'f': b'ok\n','g':b'other\n'}, (single,fork))
        after = self.node({'f': b'ok\n','g':b'other\n','h':b'last\n'}, (merge,))
        result = self.review(root, single, root, after)['group_comparison']
        self.assertEqual(result['groups'], [])
        self.assertTrue(any(c['reason']=='merge' for c in result['excluded_members']))
        # Root-inclusive range: endpoint is the actual empty tree, including SHA-256.
        for sha256 in (False, True):
            if sha256:
                repo = Path(self.temp.name)/'sha256';repo.mkdir();self.repo=repo
                self.git('init','--object-format=sha256','-b','main')
                self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
            a = self.node({'a': b'one\n'})
            b = self.node({'a': b'one\n','b':b'two\n'},(a,))
            other_root = self.node({})
            single = self.node({'a':b'one\n','b':b'two\n'},(other_root,))
            result = self.review(other_root, b, other_root, single)['group_comparison']
            self.assertTrue(result['complete'],result['warnings'])
            self.assertEqual(result['groups'][0]['base_kind'],'empty_tree')
            self.assertEqual(result['groups'][0]['base_oid'], self.git('hash-object','-t','tree','--stdin',data=b'').decode())
            self.assertTrue(result['candidates'][0]['normalized_patch_agreement'])

    def test_bounds_exhaustion_shallow_and_preservation(self):
        root = self.node({})
        single = self.node({'f': b'alpha\nbeta\n'}, (root,))
        tip = root
        for i in range(6):
            tip = self.node({'f': b'alpha\n' if i%2==0 else b'alpha\nbeta\n'}, (tip,))
        before = self.snapshot()
        for kwargs, warning in [(dict(max_groups=0),'enumeration'), (dict(max_group_comparisons=0),'comparison'),
                                (dict(max_group_candidates=0),'output'), (dict(max_bytes=1),'inspection'),
                                (dict(max_count=2),'enumeration')]:
            report = self.review(root,single,root,tip,**kwargs)
            self.assertFalse(report['complete'])
            g=report['group_comparison'];self.assertFalse(g['complete'])
            self.assertTrue(any(warning in w for w in g['warnings']),g['warnings'])
            if 'max_groups' in kwargs: self.assertGreater(g['windows_omitted'],0)
            if 'max_group_comparisons' in kwargs: self.assertGreater(g['comparisons_unsearched'],0)
            if 'max_group_candidates' in kwargs: self.assertGreater(g['candidates_omitted'],0)
        self.assertEqual(before,self.snapshot())
        for kwargs in [dict(max_groups=-1),dict(max_groups=6001),dict(max_group_comparisons=100001),dict(max_group_candidates=2001)]:
            with self.assertRaises(GitError):self.review(root,single,root,tip,**kwargs)
        with patch('git_branch_atlas.group_compare.check_time',side_effect=Incomplete('runtime limit')):
            report=self.review(root,single,root,tip)
            self.assertIn('runtime limit',report['group_comparison']['warnings'])
        two=self.node({'x':b'one\ntwo\n'},(root,))
        four=self.node({'x':b'one\ntwo\n','y':b'three\nfour\n'},(two,))
        with patch('git_branch_atlas.series.FEATURE_LIMIT',3):
            bounded=self.review(root,single,root,four)['group_comparison']
            self.assertFalse(bounded['complete'])
            self.assertEqual(bounded['groups'][0]['reason'],'changed-line feature limit')
        self.git('update-ref','refs/heads/main',tip)
        shallow=Path(self.temp.name)/'shallow'
        self.git('clone','--depth=1',self.repo.as_uri(),str(shallow))
        report=series_compare(shallow,'HEAD','HEAD','HEAD','HEAD',include_groups=True)
        self.assertFalse(report['group_comparison']['complete'])

    def test_context_perfect_score_not_agreement_and_multiple_singletons(self):
        lb=self.node({'f':b'left context\nold\nend\n'})
        rb=self.node({'f':b'right context\nold\nend\n'})
        single=self.node({'f':b'left context\nnew\nend\n'},(lb,))
        middle=self.node({'f':b'right context\ntemporary\nend\n'},(rb,))
        tip=self.node({'f':b'right context\nnew\nend\n'},(middle,))
        report=self.review(lb,single,rb,tip)
        candidate=report['group_comparison']['candidates'][0]
        self.assertEqual(candidate['score'],10000)
        self.assertFalse(candidate['normalized_patch_agreement'])
        self.assertFalse(candidate['evidence']['source_only']['items'])
        self.assertFalse(candidate['evidence']['counterpart_only']['items'])
        # Duplicate singleton applications all compete with the same aggregate.
        root=self.node({})
        a=self.node({'f':b'one\ntwo'},(root,))
        undo=self.node({},(a,))
        again=self.node({'f':b'one\ntwo'},(undo,))
        part=self.node({'f':b'one\n'},(root,))
        end=self.node({'f':b'one\ntwo'},(part,))
        report=self.review(root,again,root,end)
        groups=report['group_comparison']
        gid=next(g['id'] for g in groups['groups'] if g['members']==[part,end])
        candidates=[c for c in groups['candidates'] if c['group_id']==gid and c['normalized_patch_agreement']]
        self.assertEqual({c['single_oid'] for c in candidates},{a,again})
        self.assertTrue(all(c['ambiguous'] for c in candidates))
        # A merge before the window is a legitimate resolved base, not a member.
        fork=self.node({'x':b'fork\n'},(root,))
        merge=self.node({'x':b'fork\n'},(root,fork))
        p=self.node({'x':b'fork\n','f':b'one\n'},(merge,))
        q=self.node({'x':b'fork\n','f':b'one\ntwo'},(p,))
        result=self.review(root,a,merge,q)['group_comparison']
        self.assertEqual(result['groups'][0]['base_oid'],merge)
        self.assertTrue(result['candidates'][0]['normalized_patch_agreement'])

    def test_dirty_repository_hostile_config_missing_intermediate_blob(self):
        root=self.node({})
        a=self.node({'f':b'alpha\nbeta\n'},(root,))
        b=self.node({'f':b'alpha\n'},(root,))
        c=self.node({'f':b'alpha\nbeta\n'},(b,))
        normal=self.review(root,a,root,c)
        marker=Path(self.temp.name)/'executed'
        helper=Path(self.temp.name)/'helper'
        helper.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n');helper.chmod(0o755)
        for key in ('diff.external','diff.evil.command','diff.evil.textconv'):
            self.git('config',key,str(helper))
        (self.repo/'.git/info/attributes').write_text('* diff=evil\n')
        (self.repo/'staged').write_text('stage');self.git('add','staged')
        (self.repo/'staged').write_text('dirty');(self.repo/'untracked').write_text('keep')
        before=self.snapshot()
        self.assertEqual(self.review(root,a,root,c),normal)
        self.assertEqual(self.snapshot(),before)
        self.assertFalse(marker.exists())
        # Endpoint trees are supported, but an unavailable intermediate blob
        # must withhold the group rather than claiming endpoint agreement alone.
        blob=self.git('rev-parse',b+':f').decode()
        (self.repo/'.git/objects'/blob[:2]/blob[2:]).unlink()
        missing=self.review(root,a,root,c)
        self.assertFalse(missing['complete'])
        self.assertEqual(missing['group_comparison']['candidates'],[])
        self.assertEqual(missing['group_comparison']['groups'][0]['status'],'unclassified')

    def test_cli_html_injection_evidence_bounds_and_shared_budget(self):
        root = self.node({})
        hostile = b'</script><img src=x onerror=alert(1)>\x1b\n'
        a = self.node({'f': b'alpha\n'+hostile}, (root,))
        b = self.node({'f': b'alpha\n'}, (root,))
        c = self.node({'f': b'alpha\n'+hostile}, (b,))
        command = [sys.executable,'-m','git_branch_atlas','--repo',str(self.repo),'series',root,a,root,c,'--groups']
        j=subprocess.run([*command,'--json'],cwd=ROOT,capture_output=True,text=True)
        h=subprocess.run([*command,'--html'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(j.returncode,0,j.stderr);self.assertEqual(h.returncode,0,h.stderr)
        report=json.loads(j.stdout)
        self.assertEqual(report['schema_version'],2)
        import re
        embedded=re.search(r'<script id="report-data" type="application/json">(.*?)</script>',h.stdout,re.S)
        self.assertEqual(json.loads(embedded[1]),report)
        self.assertNotIn('<img',h.stdout)
        self.assertIn('Split and squash candidates',h.stdout)
        self.assertNotIn('\x1b',format_series_report(report))
        for formatter in (format_series_html,lambda r,n:format_series_report(r,True,n)):
            with self.assertRaises(GitError):formatter(report,1024)
        baseline=series_compare(self.repo,root,a,root,c)
        limited=self.review(root,a,root,c,max_bytes=baseline['inspection_bytes_read'])
        self.assertEqual(limited['left'],baseline['left'])
        self.assertTrue(limited['individual_complete'])
        self.assertFalse(limited['group_comparison']['complete'])
        self.assertLessEqual(limited['inspection_bytes_read'],baseline['inspection_bytes_read']+1)
        for argv in [['graph','--groups'],['series',root,a,root,c,'--max-groups','1']]:
            invalid=subprocess.run([sys.executable,'-m','git_branch_atlas',*argv],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(invalid.returncode,2)
            self.assertEqual(invalid.stdout,'')



if __name__=='__main__': unittest.main()
