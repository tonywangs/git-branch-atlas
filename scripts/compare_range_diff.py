#!/usr/bin/env python3
"""Record selected offline range-diff observations; its mapping is not an oracle."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from git_branch_atlas.series import series_compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='',
               GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
    with tempfile.TemporaryDirectory(prefix='atlas-range-diff-') as temp:
        repo = Path(temp)
        def git(*argv, data=None):
            return subprocess.check_output(['git', '-C', str(repo), *argv], env=env,
                                           input=data, stderr=subprocess.PIPE).strip()
        git('init', '-b', 'main')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        def node(files, parent=None, message='Feature'):
            entries = []
            for name, content in sorted(files.items()):
                oid = git('hash-object', '-w', '--stdin', data=content)
                entries.append(b'100644 blob ' + oid + b'\t' + name.encode() + b'\0')
            tree = git('mktree', '-z', data=b''.join(entries)).decode()
            return git('commit-tree', tree, *(['-p', parent] if parent else []), '-m', message).decode()
        root = node({}, message='Root')
        base = node({'base': b'new base\n'}, root, 'Base')
        old = node({'f': b'alpha\nbeta\nold\n'}, root)
        rebased = node({'base': b'new base\n', 'f': b'alpha\nbeta\nold\n'}, base)
        message = node({'base': b'new base\n', 'f': b'alpha\nbeta\nold\n'}, base, 'Different message')
        edited = node({'base': b'new base\n', 'f': b'alpha\nbeta\nnew\n'}, base)
        foreign = node({'g': b'unrelated\n'}, root, 'Unrelated')
        # Identical lines at a different path: the scorer intentionally exposes this false-positive risk.
        moved = node({'g': b'alpha\nbeta\nold\n'}, root, 'Other file')
        undo = node({}, old, 'Revert')
        reapply = node({'f': b'alpha\nbeta\nold\n'}, undo, 'Reapply')
        first = node({'a': b'first\n'}, root, 'A')
        ab = node({'a': b'first\n', 'b': b'second\n'}, first, 'B')
        second = node({'b': b'second\n'}, root, 'B')
        ba = node({'a': b'first\n', 'b': b'second\n'}, second, 'A')
        cases = [('rebase', root, old, base, rebased),
                 ('message_only', root, old, base, message),
                 ('edited', root, old, base, edited),
                 ('unrelated', root, old, root, foreign),
                 ('same_lines_other_path', root, old, root, moved),
                 ('duplicate_reapply', root, reapply, base, rebased),
                 ('reordered', root, ab, root, ba)]
        observations = []
        for name, lb, lt, rb, rt in cases:
            report = series_compare(repo, lb, lt, rb, rt)
            assert report['complete']
            text = git('range-diff', '--no-color', '--no-dual-color', lb+'..'+lt, rb+'..'+rt).decode()
            observations.append(dict(fixture=name, endpoints=[lb, lt, rb, rt],
                                     matches=report['matches'],
                                     left=[dict(oid=c['oid'], status=c['status'],
                                                candidates=[dict(oid=v['oid'], score=v['score'])
                                                            for v in c.get('candidates', [])])
                                           for c in report['left']['commits']],
                                     range_diff=text))
        assert len(observations[0]['matches']) == 1 and ' = ' in observations[0]['range_diff']
        assert len(observations[1]['matches']) == 1 and ' ! ' in observations[1]['range_diff']
        assert observations[2]['left'][0]['candidates'][0]['score'] == 7333
        assert observations[3]['left'][0]['status'] == 'no_candidate'
        assert observations[4]['left'][0]['candidates'][0]['score'] == 8000
        assert len(observations[5]['matches'][0]['left']) == 2
        assert len(observations[6]['matches']) == 2
        result = dict(git=git('--version').decode(), fixture_seed='fixed bytes, messages and timestamp 1767225600',
                      algorithm=report['algorithm'], threshold=report['threshold'], observations=observations,
                      limitations=['range-diff is not ground truth', 'small synthetic fixtures; no accuracy estimate',
                                   'range-diff display and pairing may vary with Git version'])
        output = json.dumps(result, indent=2) + '\n'
        if args.output:
            args.output.write_text(output)
        print(output, end='')


if __name__ == '__main__':
    main()
