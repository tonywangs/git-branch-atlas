#!/usr/bin/env python3
"""Reproduce singleton bypass measurements, correctness and application checks."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    commands = [
        [sys.executable,'-m','unittest','discover','-s','tests','-v'],
        [sys.executable,'scripts/verify_singleton_bypass.py','--seeds','200','--stress','--output',str(out/'parity.json')],
        [sys.executable,'scripts/verify_singleton_apps.py','--output-dir',str(out/'apps')],
        [sys.executable,'scripts/measure_singleton_bypass.py','--output',str(out/'paired.json')],
        [sys.executable,'scripts/check_singleton_bypass.py','--results',str(out/'paired.json')],
        [sys.executable,'scripts/check_publication.py'],
        ['git','diff','--check'],
    ]
    checks = []
    for i,command in enumerate(commands):
        print('Running: '+' '.join(command),flush=True)
        target = out/('tests.log' if i==0 else f'check-{i}.txt')
        with target.open('w') as stream:
            result = subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        checks.append(dict(command=command,exit_code=result.returncode))
        (out/'checks.json').write_text(json.dumps(checks,indent=2)+'\n')
        if result.returncode: raise SystemExit(f'Check failed: {target}')


if __name__ == '__main__': main()
