"""Candidate singleton fallback and unchanged multi-request transport contracts."""
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch

import test_patches as fixture
from experiments.singleton_bypass.candidate.git_branch_atlas import batching, patches
from git_branch_atlas import batching as production_batching, patches as production


class SingletonTests(unittest.TestCase):
    setUp = fixture.PatchTests.setUp
    git = fixture.PatchTests.git
    node = fixture.PatchTests.node

    def budget(self, module=patches):
        return module.Budget(self.repo, 30, 8*batching.PAYLOAD_BYTES, cache_blobs=True)

    def test_singleton_success_and_exact_logical_limit_accounting(self):
        root = self.node({'f':b'first\n'})
        child = self.node({'f':b'last\n'}, (root,))
        for request in ((root,None),(child,None),(child,root)):
            reference = self.budget(production)
            reference._patch_batch = production_batching.prefetch(reference,[request])
            expected = production.inspect_patch(reference,request[0],reference.max_bytes,base=request[1])
            for maximum in (reference.bytes-1, reference.bytes, reference.bytes+1, 8*batching.PAYLOAD_BYTES):
                def outcome(module, transport):
                    b = self.budget(module)
                    b._patch_batch = transport.prefetch(b,[request])
                    b.max_bytes = maximum
                    try: result = module.inspect_patch(b,request[0],maximum,base=request[1])
                    except module.Incomplete as exc: result = str(exc)
                    return result,b.bytes
                self.assertEqual(outcome(patches,batching),outcome(production,production_batching))
            b = self.budget()
            with patch.object(b,'run',wraps=b.run) as run:
                b._patch_batch = batching.prefetch(b,[request])
                self.assertEqual(patches.inspect_patch(b,request[0],b.max_bytes,base=request[1]),expected)
            self.assertFalse(any('--stdin' in c.args[0] for c in run.call_args_list))
            self.assertEqual(b.bytes,reference.bytes)

    def test_bypass_has_no_process_or_state_even_when_exhausted(self):
        root = self.node({'f':b'x\n'})
        for exhausted in ('none','bytes','deadline','disabled'):
            b = self.budget()
            if exhausted == 'bytes': b.bytes = b.max_bytes
            if exhausted == 'deadline': b.deadline = time.monotonic()-1
            if exhausted == 'disabled': b._batch_disabled = True
            before = dict(b.__dict__)
            with patch.object(b,'run',side_effect=AssertionError('must not spawn')):
                self.assertEqual(batching.prefetch(b,[(root,None)]),{})
            self.assertEqual(b.__dict__,before)
            if exhausted in ('bytes','deadline'):
                with self.assertRaises(patches.Incomplete): patches.inspect_patch(b,root,b.max_bytes)

    def test_oversized_singleton_uses_ordinary_success_path(self):
        root = self.node({'f':b'x\n'*batching.PAYLOAD_BYTES})
        b = self.budget(); old = self.budget(production)
        b._patch_batch = batching.prefetch(b,[(root,None)])
        old._patch_batch = production_batching.prefetch(old,[(root,None)])
        self.assertTrue(old._batch_disabled)
        self.assertFalse(getattr(b,'_batch_disabled',False))
        self.assertEqual(patches.inspect_patch(b,root,b.max_bytes),production.inspect_patch(old,root,old.max_bytes))
        self.assertEqual(b.bytes,old.bytes)

    def test_filtered_chunks_tail_repeated_requests_and_close(self):
        root = self.node({'f':b'x\n'})
        b = self.budget()
        with patch.object(b,'run',side_effect=AssertionError('filtered singleton must not spawn')):
            self.assertEqual(list(batching.batches(b,range(16),lambda i:(root,None) if i==7 else None)),list(range(16)))
        with patch.object(batching,'prefetch',wraps=batching.prefetch) as prefetch:
            self.assertEqual(list(batching.batches(b,range(17),lambda i:(root,None))),list(range(17)))
            self.assertEqual([len(c.args[1]) for c in prefetch.call_args_list],[16,1])
        self.assertEqual(b._patch_batch,{})
        # Repeated pairs are still a multi-request batch, with byte-identical hints.
        requests = [(root,None)]*2
        self.assertEqual(batching.prefetch(b,requests),production_batching.prefetch(self.budget(production),requests))
        iterator = batching.batches(b,range(17),lambda i:(root,None))
        next(iterator); self.assertTrue(b._patch_batch)
        iterator.close(); self.assertEqual(b._patch_batch,{})

    def test_singleton_process_failures_truncation_timeout_and_cleanup(self):
        root = self.node({'f':b'x\n'})
        helper = Path(self.temp.name)/'git'
        real_git = shutil.which('git')
        real_popen = subprocess.Popen
        children = []
        def popen(*a,**kw):
            child = real_popen(*a,**kw); children.append(child); return child
        programs = [
            ('patch-id',"os.write(1,b'truncated')"),
            ('patch-id',"sys.exit(1)"),
            ('patch-id',"os.write(2,b'e'*70000)"),
            ('diff-tree',"os.write(1,b'x'*2000000)"),
            ('patch-id',"time.sleep(10)"),
        ]
        fdpath = Path('/proc/self/fd')
        before = len(list(fdpath.iterdir())) if fdpath.exists() else None
        for command,program in programs:
            helper.write_text('#!/usr/bin/env python3\nimport os,sys,time\n'+
                f'if {command!r} in sys.argv and ({command!r} != "diff-tree" or "-p" in sys.argv):\n    '+program+'\n'+
                f'else: os.execv({real_git!r},[{real_git!r},*sys.argv[1:]])\n')
            helper.chmod(0o755)
            with self.subTest(command=command,program=program), \
                 patch.dict(os.environ,PATH=self.temp.name+os.pathsep+os.environ['PATH']), \
                 patch.object(patches.subprocess,'Popen',side_effect=popen):
                b = self.budget(); b.deadline = time.monotonic()+1
                b._patch_batch = batching.prefetch(b,[(root,None)])
                self.assertEqual(b._patch_batch,{})
                with self.assertRaises(patches.Incomplete): patches.inspect_patch(b,root,batching.PAYLOAD_BYTES)
            self.assertTrue(all(c.poll() is not None and c.stdout.closed and c.stderr.closed for c in children))
        # Cancel while an actual child is live, after bypassing speculation.
        with patch.object(patches.subprocess,'Popen',side_effect=popen), \
             patch.object(selectors.DefaultSelector,'select',side_effect=KeyboardInterrupt):
            b = self.budget(); b._patch_batch = batching.prefetch(b,[(root,None)])
            with self.assertRaises(KeyboardInterrupt): patches.inspect_patch(b,root,b.max_bytes)
        self.assertTrue(all(c.poll() is not None and c.stdout.closed and c.stderr.closed for c in children))
        if before is not None: self.assertEqual(len(list(fdpath.iterdir())),before)

    def test_existing_multi_request_regressions(self):
        import test_batching
        names = ['test_root_empty_merge_repeated_endpoints_and_unusual_filenames',
                 'test_sha256_repository',
                 'test_truncated_wrong_duplicate_and_missing_patch_ids_discard_batch']
        with patch.multiple(test_batching, prefetch=batching.prefetch, batches=batching.batches,
                            Budget=patches.Budget, inspect_patch=patches.inspect_patch,
                            Incomplete=patches.Incomplete):
            result = unittest.TestResult()
            unittest.TestSuite(test_batching.BatchingTests(n) for n in names).run(result)
        self.assertEqual(result.testsRun,3)
        self.assertTrue(result.wasSuccessful(),result.errors+result.failures)


if __name__ == '__main__': unittest.main()
