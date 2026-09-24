#!/usr/bin/env python3
"""Exercise the experimental four-request candidate's existing failure contract."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'tests')]
import test_batching
from git_branch_atlas import batching

# These tests are independent of the normal 16-item chunk shape. The same
# oversized output, framing, process failure and cancellation paths must hold.
NAMES = [
    'test_request_and_payload_caps_disable_speculation',
    'test_truncated_wrong_duplicate_and_missing_patch_ids_discard_batch',
    'test_streaming_failures_timeout_cancellation_and_child_cleanup',
    'test_ordered_accounting_deadline_and_near_limit_fallback',
    'test_sha256_repository',
]
with patch.object(batching,'REQUESTS',4), patch.object(test_batching,'REQUESTS',4):
    suite = unittest.TestSuite(test_batching.BatchingTests(name) for name in NAMES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): sys.exit(1)
