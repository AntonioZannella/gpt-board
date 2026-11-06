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
