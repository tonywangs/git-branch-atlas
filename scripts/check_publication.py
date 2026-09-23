#!/usr/bin/env python3
"""Check project file limits, including new files, without staging anything."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
files = sorted({Path(name.decode('utf-8', 'surrogateescape')) for name in paths.split(b'\0') if name})
total = 0
for name in files:
    path = ROOT / name
    if not path.exists():
        continue
    assert not path.is_symlink(), f'Unexpected symlink: {name}'
    size = path.stat().st_size
    assert size <= 10 * 1024 * 1024, f'File exceeds 10 MiB: {name}'
    assert path.suffix != '.log' or name.as_posix() == 'results/tests.log', f'Excluded log: {name}'
    total += size
assert len(files) <= 1000, 'More than 1,000 project files'
assert total <= 32 * 1024 * 1024, 'Tree exceeds 32 MiB'
print(json.dumps({'files': len(files), 'bytes': total, 'result': 'passed'}))
