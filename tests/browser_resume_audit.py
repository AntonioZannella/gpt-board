"""Serve disposable notebooks for last-notebook/reopen regression checks."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from board_storage import empty_snapshot, write_json
from build import build
from server import BoardApp, Handler

source = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='gpt-board-resume-') as directory:
    root = Path(directory)
    shutil.copytree(source / 'src', root / 'src')
    build(root)
    entries = [
        {'id': 'alpha', 'name': 'Notebook Alpha', 'kind': 'notebook'},
        {'id': 'beta', 'name': 'Notebook Beta', 'kind': 'notebook'},
        {'id': 'ideas', 'name': 'Ideas whiteboard', 'kind': 'whiteboard'},
    ]
    for entry in entries:
        write_json(root / 'data/notebooks' / entry['id'] / 'board.json', empty_snapshot(entry['name']))
    write_json(root / 'data/notebooks.json', {'notebooks': entries})
    html = (root / 'dist/index.html').read_text().replace('<script type="module" src="/native/app.js"></script>', '<aside style="position:fixed;right:12px;bottom:12px;color:#183247;background:white;padding:12px;max-width:600px;z-index:10"><button id="run-resume">Run notebook resume checks</button><pre id="resume-result">Ready — disposable notebooks</pre></aside><script type="module" src="/resume-audit.js"></script>')
    (root / 'dist/index.html').write_text(html)
    shutil.copyfile(source / 'tests/browser_resume_audit.js', root / 'dist/resume-audit.js')
    server = ThreadingHTTPServer(('127.0.0.1', 8796), Handler)
    server.daemon_threads = True
    server.app = BoardApp(root)
    server.app.port = 8796
    print('Open http://127.0.0.1:8796/ and run notebook resume checks.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
