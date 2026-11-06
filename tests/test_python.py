"""Python backend regression tests. All writes stay in temporary fixtures."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from board_storage import BoardStorage, empty_snapshot, write_json, read_json, atomic_write, dumps
from board_review import ReviewService
from server import BoardApp, Handler
from ipad_server import Bridge
from http.server import ThreadingHTTPServer
from load_session import load_session
from build import build


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = BoardStorage(self.root)
        write_json(self.root / 'data/board.json', empty_snapshot('Original'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_concurrent_creation_and_restart_preserve_collections(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(lambda _: self.storage.create('Same name', 'whiteboard'), range(20)))
        self.assertEqual(len({b['id'] for b in items}), 20)
        after = BoardStorage(self.root)
        self.assertEqual(len(after.metadata()['notebooks']), 21)
        self.assertEqual(after.read('default'), empty_snapshot('Original'))
        for item in items:
            self.assertEqual(after.read(item['id'])['store']['page:page']['name'], 'Same name')

    def test_failed_atomic_replacement_keeps_original(self):
        target = self.root / 'data/board.json'
        original = target.read_bytes()
        with patch('board_storage.os.replace', side_effect=OSError('disk fault')):
            with self.assertRaises(OSError):
                atomic_write(target, b'broken')
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(target.parent.glob('.board.json-*')), [])

    def test_review_identity_matches_existing_javascript_receipts(self):
        service = ReviewService(self.storage)
        image = 'data:image/png;base64,aGVsbG8='
        check = service.archive('Test page', image, 'default', {'top': 0, 'height': 200})
        args = {'check_id': check['check_id'], 'course': 'Calc 3', 'section': '13.8', 'page': '7', 'problem': 'é1', 'original_response': 'Unclear synthetic sample', 'result': 'unresolved', 'read_confidence': 'low', 'assistance': 'unknown', 'errors': []}
        payload = service.payload(args)
        expected = '["' + check['check_id'] + '","Calc 3","13.8","7","é1"]'
        self.assertEqual(payload['review_id'], 'br-' + hashlib.sha256(expected.encode()).hexdigest()[:32])
        self.assertEqual(payload['evidence']['image'], check['file'])
        self.assertEqual(service.payload(args), payload)
        self.assertEqual(Path(check['file']).read_bytes(), b'hello')

    def test_session_loader_backs_up_and_rejects_bad_sessions(self):
        sessions = self.root / 'sessions'; sessions.mkdir()
        original = (self.root / 'data/board.json').read_bytes()
        write_json(sessions / 'Calc3 S1 sample.tldr', {'schema': {'schemaVersion': 2}, 'records': [{'id': 'shape:frame', 'type': 'frame'}]})
        load_session('1', self.root)
        self.assertEqual(next((self.root / 'data/backups').glob('*.json')).read_bytes(), original)
        loaded = (self.root / 'data/board.json').read_bytes()
        write_json(sessions / 'Calc3 S2 bad.tldr', {'records': ['broken']})
        with self.assertRaises(ValueError):
            load_session('2', self.root)
        self.assertEqual((self.root / 'data/board.json').read_bytes(), loaded)

    def test_python_build_preserves_frontend_exactly(self):
        (self.root / 'src').mkdir()
        for name in ('app.js', 'model.js', 'style.css', 'index.html'):
            (self.root / 'src' / name).write_text('fixture ' + name)
        build(self.root)
        for name in ('app.js', 'model.js', 'style.css', 'index.html'):
            self.assertEqual((self.root / 'dist/native' / name).read_bytes(), (self.root / 'src' / name).read_bytes())
        self.assertEqual((self.root / 'dist/index.html').read_bytes(), (self.root / 'src/index.html').read_bytes())


class LiveProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.app = BoardApp(self.root)
        write_json(self.root / 'data/board.json', empty_snapshot('Fixture'))
        self.board = ThreadingHTTPServer(('127.0.0.1', 0), Handler); self.board.daemon_threads = True; self.board.app = self.app
        self.app.port = self.board.server_port
        self.bridge = ThreadingHTTPServer(('127.0.0.1', 0), Bridge); self.bridge.daemon_threads = True
        self.bridge.authority = f'127.0.0.1:{self.bridge.server_port}'; self.bridge.origin = 'http://' + self.bridge.authority
        self.bridge.upstream = self.board.server_port; self.bridge.token = 'test-pairing'
        self.streams = []
        for server in (self.board, self.bridge):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def tearDown(self):
        for stream, response in self.streams:
            response.close(); stream.close()
        for server in (self.bridge, self.board):
            server.shutdown(); server.server_close()
        self.tmp.cleanup()

    def request(self, path, body=None, bridge=False, headers=None):
        server = self.bridge if bridge else self.board
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        actual = {'Cookie': 'board_ipad=test-pairing'} if bridge else {}
        actual.update(headers or {})
        conn.request('POST' if body is not None else 'GET', path, dumps(body).encode() if body is not None else None, actual)
        response = conn.getresponse(); status = response.status; data = response.read(); conn.close()
        return status, data

    def stream(self, tab, bridge=False):
        server = self.bridge if bridge else self.board
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        conn.request('GET', '/api/events?tab=' + tab, headers={'Cookie': 'board_ipad=test-pairing'} if bridge else {})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.readline(), b': connected\n')
        self.assertEqual(response.readline(), b'\n')
        self.streams.append((conn, response))
        return conn, response

    def test_ipad_large_save_and_live_operation_roundtrip(self):
        _, stream = self.stream('ipad-test', bridge=True)
        snapshot = empty_snapshot('Fixture')
        snapshot['store']['asset:large'] = {'id': 'asset:large', 'typeName': 'asset', 'props': {'src': 'a' * (2 * 1024 * 1024)}}
        status, _ = self.request('/api/board', {'tab': 'ipad-test', 'snapshot': snapshot, 'summary': {'shapeCount': 0}}, bridge=True)
        self.assertEqual(status, 200)
        status, body = self.request('/api/board', bridge=True)
        self.assertEqual(json.loads(body), snapshot)
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(self.request, self.app.mcp_path, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'add_shapes', 'arguments': {'shapes': []}}})
            message = json.loads(stream.readline()[6:]); stream.readline()
            self.assertEqual(message['op'], 'add')
            self.assertEqual(self.request('/api/ack', {'opId': message['opId'], 'ok': True, 'ids': ['fixture']}, bridge=True)[0], 200)
            status, body = pending.result(timeout=5)
            result = json.loads(body)['result']
            self.assertEqual(json.loads(result['content'][0]['text'])['done'], ['fixture'])

    def test_incremental_ink_save_preserves_pdf_and_rejects_invalid_edits(self):
        snapshot = empty_snapshot('Fixture')
        snapshot['store']['asset:pdf'] = {'id': 'asset:pdf', 'typeName': 'asset', 'props': {'src': 'x' * 100000}}
        status, _ = self.request('/api/board', {'tab': 'test', 'snapshot': snapshot, 'summary': {'shapeCount': 0}})
        self.assertEqual(status, 200)
        def save(changes):
            return self.request('/api/board', {'tab': 'test', 'changes': changes, 'summary': {'shapeCount': 1}}, bridge=True)[0]
        ink = {'id': 'shape:ink', 'typeName': 'shape', 'type': 'draw', 'props': {'segments': []}}
        other = {**ink, 'id': 'shape:other'}
        self.assertEqual(save({'put': [ink], 'remove': []}), 200)
        self.assertEqual(save({'put': [other], 'remove': []}), 200)
        saved = self.app.storage.read('default')
        self.assertEqual(saved['schema'], snapshot['schema'])
        self.assertEqual(saved['store']['asset:pdf'], snapshot['store']['asset:pdf'])
        self.assertEqual(saved['store']['shape:ink'], ink)
        for bad in [{'put': [], 'remove': ['asset:pdf']}, {'put': [snapshot['store']['asset:pdf']], 'remove': []}, {'put': [ink], 'remove': ['shape:ink']}, {'put': [None], 'remove': []}]:
            self.assertEqual(save(bad), 400)
            self.assertEqual(self.app.storage.read('default'), saved)
        self.assertEqual(save({'put': [], 'remove': ['shape:ink']}), 200)
        saved = self.app.storage.read('default')
        self.assertNotIn('shape:ink', saved['store'])
        self.assertIn('shape:other', saved['store'])
        self.assertEqual(saved['store']['asset:pdf'], snapshot['store']['asset:pdf'])

    def test_disconnected_tab_is_removed_before_next_operation(self):
        conn, response = self.stream('gone')
        response.close(); conn.close()
        deadline = time.monotonic() + 3
        while 'gone' in self.app.tabs and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertNotIn('gone', self.app.tabs)

    def test_tunnel_restriction_and_static_traversal(self):
        self.assertEqual(self.request('/api/notebooks', headers={'x-forwarded-for': '1.2.3.4'})[0], 404)
        self.assertEqual(self.request('/%2e%2e/data/token')[0], 404)
        self.assertEqual(self.request('/api/notebooks', bridge=True, headers={'Origin': 'https://unrelated.example'})[0], 403)
        self.assertEqual(self.request(self.app.mcp_path, bridge=True)[0], 404)
        status, data = self.request(self.app.mcp_path, {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, headers={'x-forwarded-for': '1.2.3.4'})
        self.assertEqual(status, 404)


if __name__ == '__main__':
    unittest.main()
