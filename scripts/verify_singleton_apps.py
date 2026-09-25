#!/usr/bin/env python3
"""Exercise isolated installed CLI and offline Chromium for both source trees."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    env = os.environ.copy()
    env.setdefault('NODE_PATH','/tmp/atlas-browser/node_modules')
    env.setdefault('PLAYWRIGHT_BROWSERS_PATH','/tmp/atlas-browser-binaries')
    checks = []
    for kind, package in [('production',ROOT/'git_branch_atlas'),
                          ('singleton',ROOT/'experiments/singleton_bypass/candidate/git_branch_atlas')]:
        with tempfile.TemporaryDirectory(prefix='atlas-singleton-apps-') as tmp:
            source = Path(tmp)
            for name in ('pyproject.toml','README.md'):
                shutil.copyfile(ROOT/name,source/name)
            for name in ('scripts','tests'):
                shutil.copytree(ROOT/name,source/name,ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(package,source/'git_branch_atlas',ignore=shutil.ignore_patterns('__pycache__'))
            for label,command in [
                ('installation',[sys.executable,'scripts/verify_install.py']),
                ('browser',[sys.executable,'scripts/verify_html.py','--output',str(out/f'{kind}-browser.json')]),
            ]:
                result = subprocess.run(command,cwd=source,env=env,capture_output=True,text=True)
                if result.returncode:
                    print(result.stdout+result.stderr,file=sys.stderr)
                    raise SystemExit(result.returncode)
                if label == 'installation':
                    (out/f'{kind}-installation.json').write_text(result.stdout)
                checks.append(dict(kind=kind,check=label,command=command,exit_code=result.returncode,
                    source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in package.glob('*.py')}))
                print(f'{kind} {label}: passed',flush=True)
    (out/'apps-checks.json').write_text(json.dumps(checks,indent=2)+'\n')


if __name__ == '__main__': main()
