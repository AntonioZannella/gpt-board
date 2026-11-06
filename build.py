#!/usr/bin/env python3
"""Build the dependency-free browser frontend; no Node toolchain needed."""
from pathlib import Path

from board_storage import ROOT, atomic_write


def build(root=ROOT):
    root = Path(root)
    for name in ('app.js', 'model.js', 'style.css', 'index.html'):
        atomic_write(root / 'dist/native' / name, (root / 'src' / name).read_bytes())
    atomic_write(root / 'dist/index.html', (root / 'src/index.html').read_bytes())
    print('Built GPT Board with Python; browser drawing uses JavaScript.')


if __name__ == '__main__':
    build()
