#!/usr/bin/env python3
"""Run Python backend tests and the browser/client compatibility checks."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'], cwd=ROOT, check=True)
if not shutil.which('node'):
    raise SystemExit('Python tests passed. Install Node to also run the JavaScript drawing/client contract tests.')
subprocess.run(['node', '--test', 'tests/model.test.js', 'tests/server.test.mjs', 'tests/records.test.mjs', 'tests/review-log.test.mjs'], cwd=ROOT, env={**os.environ, 'BOARD_PYTHON': sys.executable}, check=True)
