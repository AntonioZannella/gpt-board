#!/usr/bin/env python3
"""Serve a disposable browser ink regression notebook; never touches user data."""
import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from board_storage import empty_snapshot, write_json
from build import build
from server import BoardApp, Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8796)
    parser.add_argument('--notebook', action='store_true', help='Test single-page navigation and view restoration')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='gpt-board-ink-') as directory:
        root = Path(directory)
        shutil.copytree(source / 'src', root / 'src')
        build(root)
        snapshot = empty_snapshot('Ink regression fixture')
        snapshot['store']['asset:large-fixture'] = {'id': 'asset:large-fixture', 'typeName': 'asset', 'props': {'src': 'x' * 3000000}}
        if args.notebook:
            for index in range(3):
                frame_id = f'shape:sheet-{index + 1}'
                snapshot['store'][frame_id] = {
                    'id': frame_id, 'typeName': 'shape', 'type': 'frame',
                    'parentId': 'page:page', 'index': f'a{index}',
                    'x': 0, 'y': index * 1420, 'rotation': 0,
                    'props': {'w': 1000, 'h': 1300, 'name': f'Sheet {index + 1}'},
                }
                label_id = f'shape:label-{index + 1}'
                snapshot['store'][label_id] = {
                    'id': label_id, 'typeName': 'shape', 'type': 'text',
                    'parentId': frame_id, 'index': 'a1', 'x': 50, 'y': 80,
                    'props': {'w': 850, 'h': 100, 'size': 'xl', 'color': 'black',
                              'text': f'Sheet {index + 1} — handwriting test'},
                }
        write_json(root / 'data/board.json', snapshot)
        write_json(root / 'data/notebooks.json', {'notebooks': [{'id': 'default', 'name': 'Ink regression fixture', 'kind': 'notebook' if args.notebook else 'whiteboard'}]})
        html = (root / 'dist/index.html').read_text().replace('<script type="module" src="/native/app.js"></script>', '<aside style="position:fixed;right:12px;bottom:12px;color:#183247;background:white;padding:12px;max-width:600px;z-index:10"><button id="run-audit" style="background:#305f88;color:white">Run ink regression checks</button><pre id="audit-result">Ready — isolated test notebook</pre></aside><script type="module" src="/ink-audit.js"></script>')
        (root / 'dist/ink-audit.html').write_text(html)
        shutil.copyfile(source / ('tests/browser_notebook_audit.js' if args.notebook else 'tests/browser_ink_audit.js'), root / 'dist/ink-audit.js')
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        server.daemon_threads = True
        server.app = BoardApp(root)
        server.app.port = args.port
        section = '' if args.notebook else '&section=whiteboards'
        print(f'Open http://localhost:{args.port}/ink-audit.html?notebook=default{section} and press Run ink regression checks.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
