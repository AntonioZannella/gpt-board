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
