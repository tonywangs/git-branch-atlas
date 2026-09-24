"""Batch framing, endpoint orientation, resource bounds and fallback lifecycle."""
import os
from pathlib import Path
import subprocess
import time
import unittest
from unittest.mock import patch

from git_branch_atlas.batching import PAYLOAD_BYTES, REQUESTS, batches, prefetch
from git_branch_atlas.patches import Budget, DIFF, Incomplete, inspect_patch
import test_patches as fixture


class BatchingTests(unittest.TestCase):
    setUp = fixture.PatchTests.setUp
    git = fixture.PatchTests.git
    node = fixture.PatchTests.node

    def budget(self):
        return Budget(self.repo, 30, 8*PAYLOAD_BYTES, cache_blobs=True)

    def direct(self, request):
        tip, base = request
        endpoints = [tip] if base is None else [base, tip]
        diff = self.budget().run(['diff-tree','--root','--no-commit-id','-r','-p',*DIFF,*endpoints,'--'])
        ids = self.budget().run(['patch-id','--stable'], data=diff).split()
        return (diff,ids[0].decode()) if ids else None

    def test_root_empty_merge_repeated_endpoints_and_unusual_filenames(self):
        paths = ['tab\tname','line\nname','quote"name','東京','invalid-\udcff','atlas-batch-0001']
        root = self.node({p:b'first\n' for p in paths})
        child = self.node({p:b'atlas-batch-0001\ncommit '+b'a'*40+b'\nlast' for p in paths}, (root,))
        empty = self.node({p:b'first\n' for p in paths}, (root,))
        merge = self.node({'merge':b'merged\n'}, (root,child))
        # Repeated tip with different explicit fake parents; root separately.
        requests = [(child,root),(child,empty),(child,root),(root,None),(empty,None),(merge,None)]
        actual = prefetch(self.budget(), requests)
        self.assertEqual(actual, {r:v for r in requests if (v:=self.direct(r)) is not None})
        self.assertEqual(prefetch(self.budget(),[(empty,None),(merge,None)]),{})
        # --stdin mutates fake parents: unsafe mixed forms must fall back.
        self.assertEqual(prefetch(self.budget(),[(child,root),(child,None)]),{})

    def test_sha256_repository(self):
        self.git('init','--object-format=sha256',str(self.repo/'sha256'))
        self.repo = self.repo/'sha256'
        self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        root = self.node({'a':b'one\n'})
        child = self.node({'a':b'two\n'},(root,))
        requests = [(root,None),(child,root)]
        self.assertEqual(prefetch(self.budget(),requests),{r:self.direct(r) for r in requests})

    def test_request_and_payload_caps_disable_speculation(self):
        root = self.node({'a':b'x\n'})
        budget = self.budget()
        with patch.object(budget,'run',side_effect=AssertionError('must not spawn')):
            self.assertEqual(prefetch(budget,[(root,None)]*(REQUESTS+1)),{})
            self.assertEqual(prefetch(budget,[('main',None)]),{})
            budget.max_bytes = PAYLOAD_BYTES-1
            self.assertEqual(prefetch(budget,[(root,None)]),{})
        huge = self.node({'huge':b'x\n'*PAYLOAD_BYTES})
        budget = self.budget()
        self.assertEqual(prefetch(budget,[(huge,None)]),{})
        self.assertTrue(budget._batch_disabled)
        self.assertEqual(budget.bytes,0)
        with patch.object(budget,'run',side_effect=AssertionError('disabled')):
            self.assertEqual(prefetch(budget,[(root,None)]),{})

    def test_truncated_wrong_duplicate_and_missing_patch_ids_discard_batch(self):
        root = self.node({'a':b'one\n'})
        child = self.node({'a':b'two\n'},(root,))
        requests = [(root,None),(child,None)]
        for mode in ('diff-truncated','diff-extra','id-truncated','id-duplicate','id-wrong','stderr','failure'):
            with self.subTest(mode=mode):
                budget = self.budget()
                original = budget.run
                def run(args, **kw):
                    if mode in ('stderr','failure'):
                        raise Incomplete('Git failed')
                    out = original(args, **kw)
                    if args[0]=='diff-tree':
                        if mode=='diff-truncated': return out[:-1]
                        if mode=='diff-extra': return out+b'extra'
                    else:
                        if mode=='id-truncated': return out[:-20]
                        if mode=='id-duplicate': return out+out.splitlines()[0]+b'\n'
                        if mode=='id-wrong': return out.replace(b'0000000000000000000000000000000000000001',b'f'*40)
                    return out
                with patch.object(budget,'run',side_effect=run):
                    self.assertEqual(prefetch(budget,requests),{})
                self.assertTrue(budget._batch_disabled)
                self.assertEqual(budget.bytes,0)
                self.assertEqual(inspect_patch(budget,child,budget.max_bytes),
                                 inspect_patch(self.budget(),child,budget.max_bytes))

    def test_streaming_failures_timeout_cancellation_and_child_cleanup(self):
        root = self.node({'a':b'x\n'})
        helper = Path(self.temp.name)/'git'
        real = subprocess.Popen
        children = []
        def popen(*a,**kw):
            child = real(*a,**kw); children.append(child); return child
        programs = ["import os; os.write(1,b'truncated')",
                    "import os; os.write(1,b'x'*2000000)",
                    "import os; os.write(2,b'e'*70000)",
                    "import sys; sys.exit(1)",
                    "import time; time.sleep(10)"]
        fdpath = Path('/proc/self/fd')
        before = len(list(fdpath.iterdir())) if fdpath.exists() else None
        for program in programs:
            helper.write_text('#!/usr/bin/env python3\n'+program+'\n'); helper.chmod(0o755)
            with patch.dict(os.environ,PATH=self.temp.name+os.pathsep+os.environ['PATH']), \
                 patch('git_branch_atlas.patches.subprocess.Popen',side_effect=popen):
                budget = self.budget(); budget.deadline = time.monotonic()+0.3
                self.assertEqual(prefetch(budget,[(root,None)]),{})
                self.assertTrue(budget._batch_disabled)
            self.assertIsNotNone(children[-1].poll())
            self.assertTrue(children[-1].stdout.closed and children[-1].stderr.closed)
        # Inject cancellation during a real live child's selector read.
        with patch.dict(os.environ,PATH=self.temp.name+os.pathsep+os.environ['PATH']), \
             patch('git_branch_atlas.patches.subprocess.Popen',side_effect=popen):
            # DefaultSelector is EpollSelector on Linux; patch its concrete class.
            import selectors
            with patch.object(selectors.DefaultSelector,'select',side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    prefetch(self.budget(),[(root,None)])
        self.assertIsNotNone(children[-1].poll())
        self.assertTrue(children[-1].stdout.closed and children[-1].stderr.closed)
        if before is not None: self.assertEqual(len(list(fdpath.iterdir())),before)

    def test_ordered_accounting_deadline_and_near_limit_fallback(self):
        root = self.node({'a':b'first\n'})
        child = self.node({'a':b'last\n'},(root,))
        reference = self.budget()
        expected = inspect_patch(reference,child,reference.max_bytes)
        used = reference.bytes
        for maximum in (used-1,used,used+1,8*PAYLOAD_BYTES):
            with self.subTest(maximum=maximum):
                cached = self.budget()
                cached._patch_batch = prefetch(cached,[(child,None)])
                self.assertTrue(cached._patch_batch)
                cached.max_bytes = maximum
                plain = self.budget(); plain.max_bytes = maximum
                def outcome(budget):
                    try: return inspect_patch(budget,child,budget.max_bytes),budget.bytes
                    except Incomplete as exc: return str(exc),budget.bytes
                self.assertEqual(outcome(cached),outcome(plain))
        cached = self.budget()
        cached._patch_batch = prefetch(cached,[(child,None)])
        with patch.object(cached,'run',wraps=cached.run) as run:
            self.assertEqual(inspect_patch(cached,child,cached.max_bytes),expected)
            self.assertFalse(any(c.args[0][0]=='patch-id' for c in run.call_args_list))
            self.assertEqual(cached.bytes,used)
        cached.deadline = time.monotonic()-1
        with self.assertRaisesRegex(Incomplete,'runtime'):
            inspect_patch(cached,child,cached.max_bytes)

    def test_batch_generator_bounds_and_closes_hints(self):
        root = self.node({'a':b'x\n'})
        budget = self.budget()
        iterator = batches(budget,range(33),lambda _: (root,None))
        with patch('git_branch_atlas.batching.prefetch',wraps=prefetch) as run:
            self.assertEqual(next(iterator),0)
            self.assertEqual(len(run.call_args.args[1]),REQUESTS)
            self.assertTrue(budget._patch_batch)
            iterator.close()
            self.assertFalse(budget._patch_batch)
        with patch('git_branch_atlas.batching.prefetch',return_value={}) as run:
            self.assertEqual(list(batches(budget,range(33),lambda _: (root,None))),list(range(33)))
            self.assertEqual([len(c.args[1]) for c in run.call_args_list],[16,16,1])


if __name__=='__main__': unittest.main()
