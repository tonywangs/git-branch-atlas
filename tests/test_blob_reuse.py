"""Cache behavior, original streaming-limit parity, failures and process cleanup."""
import os
from pathlib import Path
import subprocess
import time
import unittest
from unittest.mock import patch

from git_branch_atlas.patches import Budget, Incomplete, inspect_patch
import test_patches as fixture


class BlobReuseTests(unittest.TestCase):
    setUp = fixture.PatchTests.setUp
    git = fixture.PatchTests.git
    node = fixture.PatchTests.node

    def budget(self, maximum=1000000, **kwargs):
        return Budget(self.repo, 30, maximum, cache_blobs=True, **kwargs)

    def blob(self, data):
        return self.git('hash-object','-w','--stdin',data=data).decode()

    def read(self, budget, oid, cap=1000000):
        return budget.run(['cat-file','blob',oid],inspection=True,cap=cap)

    def test_hits_charge_bytes_and_avoid_git_but_check_deadline(self):
        value = b'alpha\x00beta\n'
        oid = self.blob(value)
        budget = self.budget()
        real = subprocess.Popen
        with patch('git_branch_atlas.patches.subprocess.Popen', wraps=real) as popen:
            self.assertEqual(self.read(budget,oid),value)
            self.assertEqual(self.read(budget,oid),value)
            self.assertEqual(popen.call_count,1)
            self.assertEqual(budget.bytes,2*len(value))
            budget.deadline = time.monotonic()-1
            with self.assertRaisesRegex(Incomplete,'runtime'):
                self.read(budget,oid)
            self.assertEqual(popen.call_count,1)
        self.assertEqual(self.budget()._blob_cache_bytes,0)

    def test_lru_entry_payload_and_admission_bounds(self):
        budget = self.budget()
        budget.BLOB_CACHE_BYTES = 12
        budget.BLOB_CACHE_ENTRIES = 2
        budget.BLOB_CACHE_ITEM_BYTES = 10
        a,b,c = [self.blob(v) for v in (b'aaaaa',b'bbbbb',b'ccccccc')]
        self.read(budget,a); self.read(budget,b); self.read(budget,a)
        self.read(budget,c)
        self.assertEqual(list(budget._blob_cache),[a,c])
        self.assertEqual(budget._blob_cache_bytes,12)
        self.read(budget,self.blob(b'x'*11))
        self.assertEqual(list(budget._blob_cache),[a,c])
        # Empty objects still consume entry slots and can never grow without bound.
        self.read(budget,self.blob(b''))
        self.assertLessEqual(len(budget._blob_cache),2)
        self.assertEqual(budget._blob_cache_bytes,sum(map(len,budget._blob_cache.values())))

    def test_byte_and_output_boundaries_match_uncached_stream(self):
        value = b'v'*100000
        oid = self.blob(value)
        # Include exact-budget, overflow-by-one, read-chunk, and output-cap cases.
        for remaining,cap in [(0,100000),(1,100000),(65535,100000),(99999,100000),
                              (100000,100000),(100001,100000),(100001,1),(100001,65536)]:
            with self.subTest(remaining=remaining,cap=cap):
                cached = self.budget(300000)
                self.read(cached,oid)
                cached.max_bytes = len(value)+remaining
                plain = Budget(self.repo,30,cached.max_bytes)
                plain.bytes = len(value)
                def outcome(budget):
                    try:
                        return self.read(budget,oid,cap),budget.bytes
                    except Incomplete as exc:
                        return str(exc),budget.bytes
                a,b = outcome(cached),outcome(plain)
                # Output-only overflow counts a whole OS read before checking the
                # cap; pipe chunk size is not deterministic even in the baseline.
                if cap < min(remaining,len(value)):
                    self.assertEqual(a[0],b[0])
                    self.assertTrue(len(value)+cap < a[1] <= len(value)+cap+65536)
                    self.assertTrue(len(value)+cap < b[1] <= len(value)+cap+65536)
                else:
                    self.assertEqual(a,b)

    def test_missing_blob_and_partial_failed_output_never_cached(self):
        budget = self.budget()
        missing = 'a'*40
        for _ in range(2):
            with self.assertRaisesRegex(Incomplete,'Git failed'):
                self.read(budget,missing)
        self.assertFalse(budget._blob_cache)
        helper = Path(self.temp.name)/'git'
        helper.write_text('#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b"truncated")\nsys.exit(1)\n')
        helper.chmod(0o755)
        with patch.dict(os.environ,PATH=self.temp.name+os.pathsep+os.environ['PATH']):
            budget = self.budget()
            with self.assertRaisesRegex(Incomplete,'Git failed'):
                self.read(budget,missing)
            self.assertEqual(budget.bytes,len(b'truncated'))
            self.assertFalse(budget._blob_cache)

    def test_oversized_patch_and_stderr_timeout_children_reaped(self):
        root = self.node({})
        commit = self.node({'big':b'x\n'*10000},(root,))
        # Content fits, but raw metadata + diff exceed inspection budget.
        budget = self.budget(30000)
        with self.assertRaisesRegex(Incomplete,'inspection byte'):
            inspect_patch(budget,commit,30000)
        helper = Path(self.temp.name)/'git'
        real = subprocess.Popen
        children = []
        def popen(*args,**kwargs):
            child = real(*args,**kwargs)
            children.append(child)
            return child
        fdpath = Path('/proc/self/fd')
        before = len(list(fdpath.iterdir())) if fdpath.exists() else None
        for program,reason in [('import os; os.write(2,b"e"*70000)','output'),
                               ('import time; time.sleep(10)','runtime')]:
            helper.write_text('#!/usr/bin/env python3\n'+program+'\n')
            helper.chmod(0o755)
            with patch.dict(os.environ,PATH=self.temp.name+os.pathsep+os.environ['PATH']), \
                 patch('git_branch_atlas.patches.subprocess.Popen',side_effect=popen):
                budget = Budget(self.repo,0.2,100000,cache_blobs=True)
                with self.assertRaisesRegex(Incomplete,reason):
                    self.read(budget,'b'*40)
                self.assertFalse(budget._blob_cache)
            self.assertIsNotNone(children[-1].poll())
            self.assertTrue(children[-1].stdout.closed and children[-1].stderr.closed)
        if before is not None:
            self.assertEqual(len(list(fdpath.iterdir())),before)

    def test_non_object_arguments_and_disabled_budget_are_not_cached(self):
        oid = self.blob(b'ok')
        budget = Budget(self.repo,30,10000)
        self.read(budget,oid)
        self.assertFalse(budget._blob_cache)
        budget = self.budget()
        budget.run(['rev-parse','--git-dir'])
        self.assertFalse(budget._blob_cache)


if __name__=='__main__':
    unittest.main()
