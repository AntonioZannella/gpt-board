"""Security regressions; no personal notebooks or live server are used."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from board_storage import empty_snapshot, write_json
from server import BoardApp, Handler, launch_options


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

    def test_events_and_board_validate_notebook_and_png_alike(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        for notebook in ('../x', 'Missing', '%5B%5D'):
            conn.request('GET', '/api/events?tab=bad-notebook&notebook=' + notebook)
            response = conn.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
        conn.close()
        self.assertNotIn('bad-notebook', self.app.tabs)
        for png in (5, [], {}, True, '', 'data:image/png;base64,'):
            board = {'tab': 't', 'snapshot': empty_snapshot('F'), 'summary': {'shapeCount': 0}, 'png': png}
            for path, body in (('/api/board', board), ('/api/ack', {'opId': 'x', 'png': png})):
                status, data = self.request(path, json.dumps(body))
                self.assertEqual((status, json.loads(data)), (400, {'error': 'Invalid PNG image.'}))

    def test_initialize_negotiates_the_protocol_version(self):
        import server
        def negotiated(params):
            return self.app.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': params})['result']['protocolVersion']
        for version in server.MCP_PROTOCOL_VERSIONS:
            self.assertEqual(negotiated({'protocolVersion': version}), version)
        for params in ({'protocolVersion': '1999-01-01'}, {'protocolVersion': 5}, {}):
            self.assertEqual(negotiated(params), server.MCP_PROTOCOL_VERSIONS[0])

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
        with patch.multiple(http_body, MAX_BODY_BYTES=8, MAX_CHUNKS=2):
            for data, expected in [(b'9\r\n', 413), (b'5\r\n12345\r\n4\r\n', 413),
                                   (b'-1\r\n', 400), (b'nope\r\n', 400), (b'\r\n', 400),
                                   (b'0x2\r\n', 400), (b' 2\r\n', 400), (b'2\n{}\r\n', 400),
                                   (b'1\r\naXX', 400), (b'3\r\na', 400),
                                   (b'1\r\na\r\n1\r\nb\r\n1\r\nc\r\n0\r\n\r\n', 413)]:
                headers = Message(); headers['Transfer-Encoding'] = 'chunked'
                with self.assertRaises(http_body.BodyError) as caught:
                    http_body.read_body(BytesIO(data), headers)
                self.assertEqual(caught.exception.status, expected)
            self.assertEqual(http_body.read_body(BytesIO(b'2\r\n{}\r\n0\r\n\r\n'), headers), b'{}')
        headers['Content-Length'] = '2'
        with self.assertRaises(http_body.BodyError):
            http_body.read_body(BytesIO(b'{}'), headers)
        for name, values, status in [('Content-Length', ['2', '2'], 400), ('Transfer-Encoding', ['gzip'], 501),
                                     ('Transfer-Encoding', ['gzip, chunked'], 501), ('Content-Length', ['10'], 400)]:
            headers = Message()
            for value in values:
                headers[name] = value
            with self.assertRaises(http_body.BodyError) as caught:
                http_body.read_body(BytesIO(b'{}'), headers)  # the last case is a truncated body
            self.assertEqual(caught.exception.status, status)

    def test_slow_body_times_out_as_a_whole(self):
        import socket
        from email.message import Message

        import http_body
        server_side, client = socket.socketpair()
        try:
            headers = Message(); headers['Content-Length'] = '10'
            client.sendall(b'12')  # then never the rest
            started = time.monotonic()
            with server_side.makefile('rb') as reader, self.assertRaises(http_body.BodyError) as caught:
                http_body.read_body(reader, headers, server_side, timeout=.2)
            self.assertEqual(caught.exception.status, 408)
            self.assertLess(time.monotonic() - started, 2)
            status, _ = self.request('/api/ack', b'', {'Transfer-Encoding': 'gzip'})
            self.assertEqual(status, 501)
        finally:
            server_side.close()
            client.close()

    def test_full_tab_queue_returns_without_blocking_or_leaking_pending(self):
        import queue
        import time

        from server import SSE_QUEUE_SIZE
        messages = queue.Queue(maxsize=SSE_QUEUE_SIZE)
        for n in range(SSE_QUEUE_SIZE):
            messages.put_nowait({'n': n})
        self.app.tabs['slow'] = {'queue': messages, 'notebook': 'default'}
        started = time.monotonic()
        result = self.app.send_to_tab('add', {})
        self.assertFalse(result['ok'])
        self.assertIn('busy', result['error'])
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(self.app.pending, {})
        self.assertEqual(messages.qsize(), SSE_QUEUE_SIZE)
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
            _, data = self.request(self.app.mcp_path, json.dumps(body))
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

    def test_only_fixed_reasons_reach_clients(self):
        status, body = self.request('/api/notebooks', json.dumps({'action': 'create', 'name': ''}))
        self.assertEqual((status, json.loads(body)), (400, {'error': 'Enter a notebook name.'}))
        with patch.object(self.app.storage, 'create', side_effect=ValueError('/private/path/notebooks.json')):
            status, body = self.request('/api/notebooks', json.dumps({'action': 'create', 'name': 'x'}))
        self.assertEqual(status, 500)
        self.assertNotIn(b'private', body)
        with patch.object(self.app.review, 'record', side_effect=KeyError('/private/path')):
            result = self.app.call_tool('record_review', {})
        self.assertTrue(result['isError'])
        self.assertNotIn('private', result['content'][0]['text'])


    def test_error_after_sse_starts_closes_without_a_second_response(self):
        import socket
        sock = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3)
        try:
            sock.sendall(b'GET /api/events?tab=t1 HTTP/1.1\r\nHost: localhost\r\n\r\n')
            received = b''
            while b': connected' not in received:
                received += sock.recv(4096)
            for _ in range(100):
                if 't1' in self.app.tabs:
                    break
                time.sleep(.01)
            with self.assertLogs('gpt-board', 'ERROR'):
                self.app.tabs['t1']['queue'].put({'bad': float('nan')})  # dumps refuses NaN
                while chunk := sock.recv(4096):
                    received += chunk
        finally:
            sock.close()
        self.assertEqual(received.count(b'HTTP/1.1'), 1)
        self.assertNotIn(b'Internal server error', received)

class LoggingTests(unittest.TestCase):
    def test_log_lines_never_contain_tokens_or_pairing_keys(self):
        import io
        import logging

        from board_storage import RedactingFormatter
        token, pairing = 'a1b2c3d4e5f60718', 'f00dfeedbeefcafe'
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RedactingFormatter([token]))
        logger = logging.getLogger('gpt-board.redaction-test')
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.INFO)
        try:
            logger.error('Unhandled error on POST %s', '/mcp/' + token)
            logger.info('Pair iPad: http://10.0.0.8:8788/pair?key=%s', pairing)
            try:
                raise RuntimeError('token was ' + token)
            except RuntimeError:
                logger.exception('failure')
        finally:
            logger.removeHandler(handler)
        output = stream.getvalue()
        self.assertNotIn(token, output)
        self.assertNotIn(pairing, output)
        self.assertIn('/mcp/[redacted]', output)
        self.assertIn('key=[redacted]', output)
