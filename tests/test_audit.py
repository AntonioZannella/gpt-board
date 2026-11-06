"""Security regressions; no personal notebooks or live server are used."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from server import BoardApp, Handler, launch_options
from board_storage import empty_snapshot, write_json


class LaunchTests(unittest.TestCase):
    def test_local_default_and_explicit_or_detected_lan(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertIsNone(launch_options([]).lan)
            self.assertEqual(launch_options([]).port, 8787)
            self.assertEqual(launch_options(['--port', '8788']).port, 8788)
            self.assertEqual(launch_options(['--lan', '10.0.0.8']).lan, '10.0.0.8')
            with patch('ipad_server.wifi_host', return_value='10.0.0.9'):
                self.assertEqual(launch_options(['--lan']).lan, '10.0.0.9')
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(BoardApp(root).allowed_hosts, {'localhost', '127.0.0.1'})


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = BoardApp(self.tmp.name)
        write_json(Path(self.tmp.name) / 'data/board.json', empty_snapshot('Fixture'))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, body=b'', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request('POST', path, body, headers or {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_mcp_obeys_host_check_and_accepts_both_loopback_names(self):
        message = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        for host in ('localhost', '127.0.0.1'):
            status, body = self.request(self.app.mcp_path, message, {'Host': host})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['result']['tools'])
        for headers in ({'Host': 'evil.example'}, {'Host': 'localhost.evil.example'},
                        {'Host': 'localhost', 'X-Forwarded-For': '1.2.3.4'}):
            self.assertEqual(self.request(self.app.mcp_path, message, headers)[0], 404)

    def test_body_lengths_and_chunks_are_bounded_before_reading(self):
        from email.message import Message
        from io import BytesIO
        import http_body
        for length in ('-1', 'oops', '+1'):
            self.assertEqual(self.request('/api/ack', b'', {'Content-Length': length})[0], 400)
        self.assertEqual(self.request('/api/ack', b'', {'Content-Length': str(http_body.MAX_BODY_BYTES + 1)})[0], 413)
        with patch.multiple(http_body, MAX_BODY_BYTES=8, MAX_CHUNK_BYTES=6):
            for data, expected in [(b'7\r\n', 413), (b'5\r\n12345\r\n4\r\n', 413),
                                   (b'-1\r\n', 400), (b'nope\r\n', 400),
                                   (b'1\r\naXX', 400), (b'3\r\na', 400)]:
                headers = Message(); headers['Transfer-Encoding'] = 'chunked'
                with self.assertRaises(http_body.BodyError) as caught:
                    http_body.read_body(BytesIO(data), headers)
                self.assertEqual(caught.exception.status, expected)
            self.assertEqual(http_body.read_body(BytesIO(b'2\r\n{}\r\n0\r\n\r\n'), headers), b'{}')
        headers['Content-Length'] = '2'
        with self.assertRaises(http_body.BodyError):
            http_body.read_body(BytesIO(b'{}'), headers)

    def test_full_tab_queue_returns_without_blocking_or_leaking_pending(self):
        import queue
        import time
        from server import MAX_PENDING_MESSAGES
        messages = queue.Queue(maxsize=MAX_PENDING_MESSAGES)
        for n in range(MAX_PENDING_MESSAGES):
            messages.put_nowait({'n': n})
        self.app.tabs['slow'] = {'queue': messages, 'notebook': 'default'}
        started = time.monotonic()
        result = self.app.send_to_tab('add', {})
        self.assertFalse(result['ok'])
        self.assertIn('busy', result['error'])
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(self.app.pending, {})
        self.assertEqual(messages.qsize(), MAX_PENDING_MESSAGES)
        self.assertEqual(messages.get_nowait(), {'n': 0})

    def test_invalid_json_has_stable_parse_errors_without_exception_details(self):
        for data in (b'{secret', b'\xff', b'[' * 2000):
            status, body = self.request(self.app.mcp_path, data)
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body), {'jsonrpc': '2.0', 'id': None,
                                              'error': {'code': -32700, 'message': 'Parse error'}})
            status, body = self.request('/api/ack', data)
            self.assertEqual((status, json.loads(body)), (400, {'error': 'Invalid JSON.'}))

    def test_non_objects_bad_rpc_params_and_png_fail_without_writing(self):
        for body in ([], ['x'], None, 'text', 4):
            for path in ('/api/board', '/api/ack', '/api/notebooks'):
                self.assertEqual(self.request(path, json.dumps(body))[0], 400)
        for body in (None, 'text', 4, []):
            status, data = self.request(self.app.mcp_path, json.dumps(body))
            self.assertEqual(json.loads(data)['error']['code'], -32600)
        for params in ([], 'x', 1):
            result = self.app.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': params})
            self.assertEqual(result['error']['code'], -32602)
        original = self.app.storage.read('default')
        for png in ('no comma', 'data:image/png;base64,???', 'data:image/png;base64,aGVsbG8=', 5):
            body = {'tab': 'test', 'snapshot': empty_snapshot('Changed'),
                    'summary': {'shapeCount': 0}, 'png': png}
            self.assertEqual(self.request('/api/board', json.dumps(body))[0], 400)
            self.assertEqual(self.app.storage.read('default'), original)
            with patch.object(self.app, 'send_to_tab', return_value={'ok': True, 'png': png}):
                self.assertTrue(self.app.call_tool('read_board')['isError'])

    def test_save_limit_preserves_board_for_full_and_incremental_saves(self):
        from board_storage import dumps
        original = self.app.storage.read('default')
        summary = {'shapeCount': 0}
        size = len(dumps(original).encode()) + len(dumps(summary).encode())
        body = {'tab': 'test', 'snapshot': original, 'summary': summary}
        with patch('board_storage.MAX_BOARD_BYTES', size):
            self.assertEqual(self.request('/api/board', json.dumps(body))[0], 200)
            before = {p.name: p.read_bytes() for p in self.app.storage.data.glob('*.json')}
            body['snapshot'] = empty_snapshot('Longer notebook title')
            self.assertEqual(self.request('/api/board', json.dumps(body))[0], 413)
            body.pop('snapshot')
            body['changes'] = {'put': [{'id': 'shape:large', 'typeName': 'shape',
                                       'type': 'geo', 'props': {'text': 'x' * 100}}], 'remove': []}
            self.assertEqual(self.request('/api/board', json.dumps(body))[0], 413)
            after = {p.name: p.read_bytes() for p in self.app.storage.data.glob('*.json')}
            self.assertEqual(before, after)
            self.assertEqual(self.app.storage.read('default'), original)
