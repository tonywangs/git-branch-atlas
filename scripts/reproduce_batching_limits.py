#!/usr/bin/env python3
"""Run the complete batching-limit experiment and its verification sequentially."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    env = os.environ.copy()
    env.setdefault('NODE_PATH','/tmp/atlas-browser/node_modules')
    env.setdefault('PLAYWRIGHT_BROWSERS_PATH','/tmp/atlas-browser-binaries')
    commands = [
        [sys.executable,'-m','unittest','discover','-s','tests','-v'],
        [sys.executable,'scripts/verify_batching_candidate.py'],
        [sys.executable,'scripts/verify_group_reuse.py','--baseline','experiments/group_batching/baseline',
         '--seeds','200','--stress','--output',str(out/'parity.json')],
        [sys.executable,'scripts/verify_install.py'],
        [sys.executable,'scripts/verify_html.py','--output',str(out/'browser.json')],
        [sys.executable,'scripts/measure_batching_limits.py','--output',str(out/'paired.json')],
        [sys.executable,'scripts/check_batching_limits.py','--results',str(out/'paired.json')],
        [sys.executable,'scripts/check_publication.py'],
        ['git','diff','--check'],
    ]
    completed = []
    for i, command in enumerate(commands):
        print('Running: '+ ' '.join(command),flush=True)
        # Keep subprocess evidence separate from run metadata; never capture agents.
        target = out/('tests.log' if i == 0 else f'check-{i}.txt')
        with target.open('w') as stream:
            result = subprocess.run(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        completed.append(dict(command=command,exit_code=result.returncode))
        (out/'checks.json').write_text(json.dumps(completed,indent=2)+'\n')
        if result.returncode:
            raise SystemExit(f'Check failed; see {target}')

if __name__ == '__main__': main()
