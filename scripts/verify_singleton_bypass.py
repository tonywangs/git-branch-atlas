#!/usr/bin/env python3
"""Three-way singleton candidate parity, logical limits and independent endpoints."""
import argparse
import hashlib
import importlib.util
import importlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
import test_patches
from test_patches import PatchTests
from experiments.batching_limits.baseline.git_branch_atlas.series import series_compare as production_compare
from experiments.singleton_bypass.candidate.git_branch_atlas.series import series_compare


def baseline(directory):
    path = directory/'git_branch_atlas'
    manifest = json.loads((path.parents[1]/'baseline.json').read_text())['files']
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.glob('*.py')} == manifest, 'frozen source changed'
    spec = importlib.util.spec_from_file_location('frozen_atlas', path/'__init__.py', submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return importlib.import_module('frozen_atlas.series').series_compare


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def snapshot(repo):
    return {str(p.relative_to(repo)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in repo.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=200)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--stress', action='store_true', help='Seeded larger payloads and alternating packed/loose objects')
    parser.add_argument('--baseline', type=Path, default=ROOT/'experiments/group_batching/baseline')
    args = parser.parse_args()
    assert 1 <= args.seeds <= 10000
    old_compare = baseline(args.baseline.resolve())
    production = ROOT/'experiments/batching_limits/baseline/git_branch_atlas'
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in production.glob('*.py')} == json.loads((production.parents[1]/'baseline.json').read_text())['files']
    candidate = ROOT/'experiments/singleton_bypass/candidate/git_branch_atlas'
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in candidate.glob('*.py')} == json.loads((candidate.parents[1]/'baseline.json').read_text())['files']
    # Fixture commit IDs and timestamps reproducible across machines and runs.
    test_patches.ENV.update(GIT_AUTHOR_DATE='1700000000 +0000', GIT_COMMITTER_DATE='1700000000 +0000')
    rows = []
    for seed in range(args.seeds):
        f = PatchTests()
        f.setUp()
        try:
            rng = random.Random(seed)
            root = f.node({})
            path = rng.choice(['plain', 'tab\tname', 'line\nname', 'space name', 'quote"name', '東京', 'invalid-\udcff'])
            a = (f'alpha {seed}\n'*rng.randint(1,3)).encode()
            b = (f'beta {seed}\n'*rng.randint(1,3)).encode()
            if args.stress:
                # Bounded varied payloads, including batch-overflow-sized histories.
                line = ''.join(rng.choices('abcdef0123456789', k=127)).encode()+b'\n'
                a += line * (2048 if seed%20 == 0 else rng.randint(1,256))
                b += line * rng.randint(1,64)
            left = f.node({path:a+b}, (root,))
            # A split, its revert and duplicate, overlapping 2/4-member candidates.
            r1 = f.node({path:a}, (root,))
            r2 = f.node({path:a+b}, (r1,))
            r3 = f.node({path:a}, (r2,))
            right = f.node({path:a+b}, (r3,))
            case = seed%6
            if case == 0:
                middle = f.node({path:a+b, 'binary':b'\0unsupported'}, (right,))
                right = f.node({path:a+b}, (middle,))
            elif case == 1:
                middle = f.node({path:a+b, 'link':b'target'}, (right,), {'link':'120000'})
                right = f.node({path:a+b}, (middle,))
            elif case == 2:
                middle = f.node({path:a+b}, (right,), {path:'100755'})
                right = f.node({path:a+b}, (middle,))
            elif case == 3:
                branch = f.node({'branch':b'branch\n'}, (root,))
                right = f.node({path:a+b,'branch':b'branch\n'}, (right,branch))
            elif case == 4:
                right = f.node({path:a+b+b'edited\n'}, (right,))
            else:
                right = f.node({path:a+b}, (right,)) # empty member
            if seed%2:
                left,right = right,left
            f.git('update-ref', 'refs/heads/main', left)
            f.git('read-tree', left) # Populate index; comparison must preserve it.
            (f.repo/'untracked').write_text('leave this alone\n')
            storage = 'loose'
            if args.stress and seed%2:
                f.git('update-ref', 'refs/heads/right', right)
                f.git('repack', '-ad')
                f.git('prune-packed')
                storage = 'packed'
                assert b'packs: 0' not in f.git('count-objects','-v')
            before = snapshot(f.repo)
            common = dict(include_groups=True, seconds=600, max_groups=100, max_group_candidates=2000,
                          threshold=rng.choice([3000,5000,10000]))
            argv = (f.repo,root,left,root,right)
            expected = old_compare(*argv, **common)
            assert production_compare(*argv, **common) == expected, (seed,'production full')
            actual = series_compare(*argv, **common)
            assert actual == expected, (seed,'full',digest(actual),digest(expected))
            assert actual['complete'], (seed,actual['warnings'])
            endpoint_hashes = []
            # Git diff is independent of inspect_patch/diff-tree and window construction.
            for g in actual['group_comparison']['groups']:
                if g['status'] != 'eligible':
                    continue
                diff = f.git('diff','--no-ext-diff','--no-textconv','--no-renames','--text',
                             '--full-index','--unified=3',g['base_oid'],g['tip_oid'],'--')+b'\n'
                pid = f.git('patch-id','--stable',data=diff).split()[0].decode()
                assert g['patch_id']==pid, (seed,g['id'])
                endpoint_hashes.append(hashlib.sha256(diff).hexdigest())
            limits = [dict(max_groups=1),dict(max_group_comparisons=1),dict(max_group_candidates=1),
                      dict(max_bytes=max(1,actual['inspection_bytes_read']//2)),dict(max_count=2),
                      dict(max_comparisons=0),dict(max_bytes=actual['inspection_bytes_read']-1)]
            limited = dict(common, **limits[seed%len(limits)])
            limited_old = old_compare(*argv, **limited)
            assert production_compare(*argv, **limited) == limited_old, (seed,'production limited')
            limited_new = series_compare(*argv, **limited)
            assert limited_old == limited_new, (seed,'limited',limited)
            assert snapshot(f.repo)==before, (seed,'repository changed')
            rows.append(dict(seed=seed,storage=storage,case=['binary','symlink','mode','merge','edited','empty'][case],
                             path=path,full_sha256=digest(actual),limited_sha256=digest(limited_new),
                             limit=limits[seed%len(limits)],endpoint_sha256=endpoint_hashes,
                             candidates=actual['group_comparison']['candidate_count']))
        finally:
            f.doCleanups()
        if seed%20==19:
            print(f'{seed+1} histories verified',file=sys.stderr,flush=True)
    evidence = dict(implementations=['unbatched','production','singleton'],
                    source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for directory in (production, candidate, args.baseline.resolve()/'git_branch_atlas') for p in directory.glob('*.py')},
                    seeds=args.seeds, stress=args.stress, result='passed', exact_report_equality=True,
                    repository_contents_index_refs_configuration_unchanged=True,
                    oracle='Independent git diff endpoint patches -> git patch-id --stable', rows=rows)
    text = json.dumps(evidence,indent=2)+'\n'
    if args.output:
        args.output.write_text(text)
    print(text,end='')


if __name__=='__main__':
    main()
