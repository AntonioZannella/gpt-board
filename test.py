#!/usr/bin/env python3
"""Run backend tests and all browser/client regression tests."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def browser_tests(root=ROOT):
    """Discover both JavaScript module formats in a stable order."""
    tests = Path(root) / 'tests'
    return sorted(str(path.relative_to(root))
                  for pattern in ('*.test.js', '*.test.mjs')
                  for path in tests.glob(pattern))


def main():
    subprocess.run(
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'],
        cwd=ROOT, check=True,
    )
    if not shutil.which('node'):
        raise SystemExit('Python tests passed. Install Node to also run the JavaScript drawing/client contract tests.')
    subprocess.run(
        ['node', '--test', *browser_tests()], cwd=ROOT,
        env={**os.environ, 'BOARD_PYTHON': sys.executable}, check=True,
    )


if __name__ == '__main__':
    main()
