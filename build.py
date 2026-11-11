#!/usr/bin/env python3
"""Build the dependency-free browser frontend; no Node toolchain needed."""
from pathlib import Path

from board_storage import ROOT, atomic_write

FILES = ('app.js', 'ink-picker.js', 'image-cache.js', 'model.js', 'sync.js', 'recovery.js', 'journal-db.js', 'panels.js', 'file-options.js', 'pages.js', 'pdf-links.js', 'trackpad.js', 'follow.js', 'swipe-diagnostics.js', 'style.css', 'index.html')


def build(root=ROOT):
    root = Path(root)
    for name in FILES:
        atomic_write(root / 'dist/native' / name, (root / 'src' / name).read_bytes())
    atomic_write(root / 'dist/index.html', (root / 'src/index.html').read_bytes())
    print('Built GPT Board with Python; browser drawing uses JavaScript.')


if __name__ == '__main__':
    build()
