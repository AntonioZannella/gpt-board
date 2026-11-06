#!/usr/bin/env python3
"""GPT Board HTTP, SSE and MCP server. Python standard library only."""
import argparse
import base64
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import select
import socket
import time
import re
import sys
import threading
from urllib.parse import urlsplit, parse_qs, unquote
import uuid
from board_storage import ROOT, LOCK, BoardStorage, dumps, secret
from board_review import ReviewService


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def text(value, error=False):
    return {'content': [{'type': 'text', 'text': value}], 'isError': error}


class BoardApp:
    def __init__(self, root=ROOT):
        self.storage = BoardStorage(root)
        self.review = ReviewService(self.storage)
        self.mcp_path = '/mcp/' + secret(self.storage.data / 'token', 16)
        self.port = int(os.environ.get('PORT', '8787'))
        self.allowed_hosts = {'localhost', '127.0.0.1'}
        self.tabs, self.pending = {}, {}
        self.active_tab = None
        self.lock = threading.RLock()

    def send_to_tab(self, op, args, timeout=5):
        with self.lock:
            target = self.tabs.get(self.active_tab) or next(iter(self.tabs.values()), None)
            if not target:
                return {'ok': False, 'error': f'Board tab is not open. Ask the user to open http://localhost:{self.port}'}
            op_id = str(uuid.uuid4())
            answer = queue.Queue(maxsize=1)
            self.pending[op_id] = answer
            notebook = target['notebook']
            target['queue'].put({'opId': op_id, 'op': op, **args})
        try:
            result = answer.get(timeout=timeout)
            return {**result, 'notebook': notebook}
        except queue.Empty:
            return {'ok': False, 'error': f'Board tab did not answer within {timeout} s. Open the board on the iPad or Mac, then retry.'}
        finally:
            with self.lock:
                self.pending.pop(op_id, None)

    def call_tool(self, name, args=None):
        args = {} if args is None else args
        if not isinstance(args, dict):
            return text('Provide structured tool arguments.', True)
        if name == 'record_review':
            try:
                return text(dumps(self.review.record(args)))
            except Exception as exc:
                return text(str(exc), True)
        if name == 'read_board':
            if 'frame' in args and (not isinstance(args['frame'], str) or not args['frame'].strip()):
                return text('Provide a nonempty frame name.', True)
            region = args.get('region')
            def valid_region(r):
                return isinstance(r, dict) and finite(r.get('top')) and r['top'] >= 0 and finite(r.get('height')) and r['height'] > 0
            if 'region' in args and (not args.get('frame') or not valid_region(region) or set(region) - {'top', 'height'}):
                return text('A region requires an exact frame, top >= 0, and height > 0.', True)
            request = {key: args[key] for key in ('frame', 'region') if key in args}
            request.update(overview=args.get('overview') is True, include_details=args.get('include_details') is True)
            result = self.send_to_tab('export', request, 8)
            if not result.get('ok'):
                return text(result.get('error', 'Board operation failed.'), True)
            summary = result.get('summary') or {}
            if 'region' in args and not valid_region(summary.get('region')):
                return text('Refresh the GPT Board tab to enable close-up reads. This tab did not confirm the requested slice, so no cropped review evidence was archived.', True)
            content = []
            png = result.get('png')
            if png:
                content.append({'type': 'image', 'data': png.split(',', 1)[1], 'mimeType': 'image/png'})
            summary = {**summary, 'review_instructions': self.review.instructions}
            if png and isinstance(args.get('frame'), str):
                try:
                    check = self.review.archive(summary.get('frame') or args['frame'], png, result['notebook'], summary.get('region'))
                    context = {key: check[key] for key in ('check_id', 'notebook', 'frame')}
                    context['captured_at'] = check['ts']
                    if check.get('region'):
                        context['region'] = check['region']
                    summary['review_context'] = context
                except Exception as exc:
                    print('check image not saved:', exc, file=sys.stderr)
                    summary['review_warning'] = 'The review image could not be archived. Do not claim this review is logged; retry read_board before record_review.'
            content.append({'type': 'text', 'text': dumps(summary)})
            return {'content': content}
        if name in ('add_shapes', 'update_shapes', 'delete_shapes'):
            op = name.split('_')[0]
            result = self.send_to_tab(op, {'ids': args.get('ids') or []} if op == 'delete' else {'shapes': args.get('shapes') or []})
            if not result.get('ok'):
                return text(result.get('error', 'Board operation failed.'), True)
            out = {'done': result.get('ids')}
            if result.get('errors'):
                out['errors'] = result['errors']
            return text(dumps(out))
        return text(f'Unknown tool: {name}', True)

    def rpc(self, message):
        if 'id' not in message:
            return None
        method, params = message.get('method'), message.get('params') or {}
        response = {'jsonrpc': '2.0', 'id': message['id']}
        if method == 'initialize':
            result = {'protocolVersion': params.get('protocolVersion', '2025-06-18'), 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'gpt-board', 'version': '1.1.0'},
                      'instructions': "This is the user's personal notebook board. Use read_board to see the active notebook (the image shows handwriting). Use add_shapes / update_shapes / delete_shapes to draw. The active notebook is the one in the browser tab used most recently; there are no canvas IDs. " + self.review.instructions}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': self.review.tools}
        elif method == 'tools/call':
            result = self.call_tool(params.get('name'), params.get('arguments'))
        else:
            return {**response, 'error': {'code': -32601, 'message': f'Method not found: {method}'}}
        return {**response, 'result': result}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'GPTBoard/Python'

    @property
    def app(self):
        return self.server.app

    def log_message(self, *_):
        # Avoid logging secret MCP URLs or large notebook payloads.
        pass

    def respond(self, status, body=b'', content_type='application/json', headers=None):
        if not isinstance(body, bytes):
            body = dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def body(self):
        # Accept both fetch Content-Length requests and chunked clients.
        if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            chunks = []
            while True:
                size = int(self.rfile.readline().split(b';', 1)[0], 16)
                if not size:
                    while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                        pass
                    break
                chunks.append(self.rfile.read(size))
                if self.rfile.read(2) != b'\r\n':
                    raise ValueError('Invalid chunk framing.')
            data = b''.join(chunks)
        else:
            data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        return json.loads(data)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_HEAD(self):
        self.handle_request()

    def handle_request(self):
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            self.respond(500, {'error': str(exc)})

    def route(self):
        app = self.app
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        arg = lambda key, default=None: query.get(key, [default])[0]
        remote = self.headers.get('cf-connecting-ip') or self.headers.get('x-forwarded-for') or not any(re.fullmatch(re.escape(host) + r'(:\d+)?', self.headers.get('Host', '')) for host in app.allowed_hosts)
        if remote:
            return self.respond(404, b'')
        if url.path == app.mcp_path:
            if self.command != 'POST':
                return self.respond(405, b'', headers={'Allow': 'POST'})
            body = self.body()
            result = [r for msg in body if (r := app.rpc(msg)) is not None] if isinstance(body, list) else app.rpc(body)
            return self.respond(202, b'') if result is None or result == [] else self.respond(200, result)
        if url.path == '/api/notebooks':
            if self.command == 'GET':
                kind = arg('kind', 'notebook')
                if kind not in ('notebook', 'whiteboard'):
                    return self.respond(400, {'error': 'Invalid collection type.'})
                return self.respond(200, {'notebooks': [b for b in app.storage.metadata()['notebooks'] if b.get('kind', 'notebook') == kind]})
            if self.command == 'POST':
                body = self.body()
                if body.get('action') != 'create':
                    return self.respond(400, {'error': 'Unsupported notebook action.'})
                try:
                    return self.respond(201, {'notebook': app.storage.create(body.get('name'), body.get('kind', 'notebook'))})
                except ValueError as exc:
                    return self.respond(400, {'error': str(exc)})
        if url.path == '/api/events' and self.command == 'GET':
            return self.events(arg('tab', 'legacy-' + str(uuid.uuid4())), arg('notebook', 'default'))
        if url.path == '/api/ack' and self.command == 'POST':
            body = self.body()
            op_id = body.pop('opId', None)
            with app.lock:
                target = app.pending.pop(op_id, None)
                if target:
                    target.put_nowait(body)
            return self.respond(200, {'ok': True})
        if url.path == '/api/board':
            if self.command == 'GET':
                try:
                    notebook = app.storage.require(arg('notebook', 'default'))
                    return self.respond(200, app.storage.read(notebook))
                except ValueError as exc:
                    return self.respond(404, {'error': str(exc)})
            if self.command == 'POST':
                body = self.body()
                tab = body.get('tab')
                if not isinstance(tab, str) or not tab:
                    return self.respond(409, {'error': 'Reload this board tab.'})
                try:
                    notebook = app.storage.require(body.get('notebook', 'default'))
                except ValueError as exc:
                    return self.respond(404, {'error': str(exc)})
                snapshot, summary, png = body.get('snapshot'), body.get('summary'), body.get('png')
                if not isinstance(summary, dict) or not finite(summary.get('shapeCount')) or summary['shapeCount'] < 0 or (png is not None and (not isinstance(png, str) or not png.startswith('data:image/png;base64,'))):
                    return self.respond(400, {'error': 'Invalid board save; existing board was preserved.'})
                if 'changes' in body:
                    try:
                        app.storage.save_changes(notebook, body['changes'], summary)
                    except ValueError as exc:
                        return self.respond(400, {'error': str(exc)})
                else:
                    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('store'), dict) or not snapshot.get('schema'):
                        return self.respond(400, {'error': 'Invalid board save; existing board was preserved.'})
                    app.storage.save(notebook, snapshot, summary, png)
                with app.lock:
                    app.active_tab = tab
                    if tab in app.tabs:
                        app.tabs[tab]['notebook'] = notebook
                return self.respond(200, {'ok': True})
        if self.command not in ('GET', 'HEAD'):
            return self.respond(405, b'')
        dist = (app.storage.root / 'dist').resolve()
        path = (dist / (unquote(url.path).lstrip('/') or 'index.html')).resolve()
        if not path.is_relative_to(dist) or not path.is_file():
            return self.respond(404, b'Not found. Did you run python3 build.py?', 'text/plain')
        mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.png': 'image/png', '.json': 'application/json'}
        return self.respond(200, path.read_bytes(), mime.get(path.suffix, 'application/octet-stream'))

    def events(self, tab, notebook):
        client = {'queue': queue.Queue(), 'notebook': notebook}
        with self.app.lock:
            if not tab.startswith('legacy-'):
                self.app.tabs[tab] = client
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b': connected\n\n'); self.wfile.flush()
            last_ping = time.monotonic()
            while True:
                # HTTPServer has no Node-style request close event. Detect disconnected
                # browsers promptly so operations cannot route to a stale tab.
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                    break
                try:
                    event = client['queue'].get(timeout=.25)
                    data = ('data: ' + dumps(event) + '\n\n').encode()
                except queue.Empty:
                    if time.monotonic() - last_ping < 20:
                        continue
                    last_ping = time.monotonic()
                    data = b': ping\n\n'
                self.wfile.write(data); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            with self.app.lock:
                if self.app.tabs.get(tab) is client:
                    self.app.tabs.pop(tab, None)


def launch_options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8787')))
    parser.add_argument('--lan', nargs='?', const='auto', metavar='IPV4',
                        help='Enable LAN access; optionally specify this Mac’s Wi-Fi IPv4 address.')
    options = parser.parse_args(argv)
    if not 1 <= options.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if options.lan:
        from ipad_server import wifi_host
        try:
            address = ipaddress.IPv4Address(wifi_host() if options.lan == 'auto' else options.lan)
            if address.is_unspecified or address.is_multicast:
                raise ValueError('Choose a specific interface address')
            options.lan = str(address)
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
    return options


def main():
    options = launch_options()
    app = BoardApp()
    app.port = options.port
    if options.lan:
        app.allowed_hosts.add(options.lan)
    server = ThreadingHTTPServer(('0.0.0.0' if options.lan else '127.0.0.1', app.port), Handler)
    server.daemon_threads = True
    server.app = app
    print(f'Board: http://localhost:{app.port} (Python)', flush=True)
    if options.lan:
        print(f'LAN board: http://{options.lan}:{app.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
