#!/usr/bin/env python3
"""GPT Board HTTP/SSE/MCP server; media decoders run in bounded workers."""
import argparse
import gzip
import json
import logging
import os
import queue
import re
import select
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlsplit

from ai_operations import AIOperations
from board_review import ReviewService
from board_storage import (
    LOCK,
    ROOT,
    BoardSizeError,
    BoardStorage,
    ClientError,
    ConflictError,
    DamagedFileError,
    StaleTabError,
    check_shape,
    decode_png,
    dumps,
    finite,
    read_json,
    secret,
    setup_logging,
)
from browser_snapshot import browser_snapshot, image_bytes
from file_lock import file_lock
from http_body import BodyError, read_body
from http_limits import BoundedHTTPServer
from media import Media
from pairing import INVITE_SECONDS, Pairing

log = logging.getLogger('gpt-board')

# MCP protocol versions this server speaks, newest first. initialize echoes a
# supported client version and otherwise offers the newest.
MCP_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26')
SERVER_VERSION = '1.3.0rc1'
DEFAULT_PORT = 8787
ALLOWED_HOSTS = ('localhost', '127.0.0.1')
TAB_REPLY_TIMEOUT_SECONDS = 5  # a board tab's answer to a drawing operation
READ_BOARD_TIMEOUT_SECONDS = 8  # exporting an image takes the tab longer
SSE_POLL_SECONDS = 0.25  # how often an idle stream checks for a closed tab
SSE_PING_INTERVAL_SECONDS = 20
SSE_QUEUE_SIZE = 32  # operations waiting for one tab's event stream


# Student handwriting. The AI may not change or delete these unless the tool
# call says the user explicitly asked (user_authorized). Pages and imported
# page images can never be changed through the AI tools.
STUDENT_INK = ('draw', 'highlight')
PAGE_MATERIAL = ('frame', 'image')


def accepts_gzip(header):
    for part in header.lower().split(','):
        coding, *parameters = part.strip().split(';')
        if coding.strip() != 'gzip':
            continue
        quality = 1.0
        for parameter in parameters:
            key, _, value = parameter.strip().partition('=')
            if key.strip() == 'q':
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        return 0 < quality <= 1
    return False


def reject_constant(name):
    # json.loads would otherwise accept NaN and Infinity, which are not JSON.
    raise BodyError('Invalid JSON.')


def check_png(value):
    # One PNG check for every route: a missing image is fine, anything else
    # must be a valid PNG data URL or the request gets a 400.
    if value is not None:
        try:
            decode_png(value)
        except ValueError:
            raise BodyError('Invalid PNG image.') from None


def text(value, error=False):
    return {'content': [{'type': 'text', 'text': value}], 'isError': error}


class BoardApp:
    def __init__(self, root=ROOT):
        self.storage = BoardStorage(root)
        self.review = ReviewService(self.storage)
        self.ai = AIOperations(self.storage)
        self.media = Media(self.storage)
        self.ai_target = None
        self.startup_timings = {}
        self.acknowledged = {}
        self.mcp_path = '/mcp/' + secret(self.storage.data / 'token', 16)
        self.port = int(os.environ.get('PORT', DEFAULT_PORT))
        self.allowed_hosts = set(ALLOWED_HOSTS)
        self.pairing = Pairing(self.storage.data)
        self.tabs, self.pending = {}, {}
        self.active_tab = None
        self.active_notebook = None
        self.lock = threading.RLock()
        self.awake_process = None

    def choose_tab(self, notebook=None):
        """Pick the board tab an AI request goes to; (tab, None) or (None, why not).

        A request never silently lands in a different notebook than intended:
        with `notebook` it goes only to a tab showing that notebook, and
        without it only to the tab used last, or, if that tab has gone, to
        another tab showing the very same notebook."""
        if not self.tabs:
            return None, f'Board tab is not open. Ask the user to open http://localhost:{self.port}'
        open_books = sorted({t['notebook'] for t in self.tabs.values()})
        if self.ai_target and notebook is None:
            selected = self.tabs.get(self.ai_target['tab'])
            if not selected or selected['notebook'] != self.ai_target['notebook']:
                return None, 'The selected AI target disconnected. Select a target in the board or pass an explicit notebook.'
            return selected, None
        active = self.tabs.get(self.active_tab)
        if notebook is not None:
            wanted = [t for t in self.tabs.values() if t['notebook'] == notebook]
            if not wanted:
                return None, f'Notebook "{notebook}" is not open in any board tab. Open notebooks: {", ".join(open_books)}.'
            return (active if active and active['notebook'] == notebook else wanted[-1]), None
        if active:
            return active, None
        if self.active_notebook is not None:
            wanted = [t for t in self.tabs.values() if t['notebook'] == self.active_notebook]
            if wanted:
                return wanted[-1], None
            return None, (f'The last-used notebook "{self.active_notebook}" is no longer open. '
                          f'Open notebooks: {", ".join(open_books)}. Pass notebook to choose one.')
        if len(open_books) == 1:
            return next(iter(self.tabs.values())), None
        return None, ('The board tab used last has disconnected and several notebooks are open: '
                      f'{", ".join(open_books)}. Pass notebook to choose one.')

    def send_to_tab(self, op, args, timeout=TAB_REPLY_TIMEOUT_SECONDS, notebook=None, target_tab=None):
        with self.lock:
            if target_tab is not None:
                target = self.tabs.get(target_tab)
                if target is None or target['notebook'] != notebook:
                    return {'ok': False, 'error': 'The iPad changed notebooks or disconnected. Retry the current-page read.'}
            else:
                target, problem = self.choose_tab(notebook)
            if not target:
                return {'ok': False, 'error': problem}
            op_id = str(uuid.uuid4())
            answer = queue.Queue(maxsize=1)
            self.pending[op_id] = answer
            where = {'notebook': target['notebook'], 'tab': target['tab']}
            try:
                target['queue'].put_nowait({'opId': op_id, 'op': op, **args})
            except queue.Full:
                self.pending.pop(op_id, None)
                return {'ok': False, 'error': 'Board tab is busy. Wait for it to catch up, then retry.'}
        try:
            result = answer.get(timeout=timeout)
            return {**result, **where}
        except queue.Empty:
            # The tab undoes an edit whose answer arrives after this point (see
            # /api/ack), so retrying the same request cannot apply it twice.
            return {'ok': False, 'error': f'Board tab did not answer within {timeout} s, so nothing was changed. Open the board on the iPad or Mac, then retry.'}
        finally:
            with self.lock:
                self.pending.pop(op_id, None)

    def notify_changed(self, notebook, rev, sender):
        """Tell the other tabs showing this notebook that it has a new revision."""
        with self.lock:
            for tab in self.tabs.values():
                if tab['notebook'] == notebook and tab['tab'] != sender:
                    try:
                        tab['queue'].put_nowait({'event': 'changed', 'rev': rev})
                    except queue.Full:
                        pass  # it finds out at its next save instead

    def tablet_view(self):
        """Most recently active connected tablet; presence never touches ink data."""
        now = time.monotonic()
        with self.lock:
            views = [tab['view'] for tab in self.tabs.values()
                     if tab.get('view') and now - tab['view']['updated'] < 30]
            return max(views, key=lambda view: view['updated']) if views else None

    def update_tablet_view(self, body):
        tab_id, frame, kind = body.get('tab'), body.get('frame'), body.get('kind')
        if (not isinstance(tab_id, str) or body.get('tablet') is not True
                or kind not in ('notebook', 'whiteboard')
                or not isinstance(body.get('visible'), bool)
                or (frame is not None and (not isinstance(frame, str) or len(frame) > 200))):
            raise ClientError('Invalid tablet presence.')
        with self.lock:
            client = self.tabs.get(tab_id)
            if client is None:
                raise ClientError('Reconnect this board tab.')
            if not body['visible']:
                client.pop('view', None)
                return
            view = {'tab': tab_id, 'notebook': client['notebook'], 'kind': kind,
                    'frame': frame, 'updated': time.monotonic()}
            previous = client.get('view')
            client['view'] = view
            if previous and all(previous[k] == view[k] for k in ('notebook', 'kind', 'frame')):
                return
            for other in self.tabs.values():
                if other is client:
                    continue
                try:
                    other['queue'].put_nowait({'event': 'view', **view})
                except queue.Full:
                    pass  # followers also poll, so a full queue cannot lose the page forever

    def preview_ink(self, body):
        """Ephemeral preview only; normal revision-checked saves remain authoritative."""
        shape = body.get('shape')
        if not isinstance(shape, dict) or shape.get('type') not in ('draw', 'highlight'):
            raise ClientError('Invalid ink preview.')
        if len(dumps(shape)) > 100_000:
            raise ClientError('Ink preview is too large.')
        check_shape(shape)
        with self.lock:
            client = self.tabs.get(body.get('tab'))
            if client is None:
                raise ClientError('Reconnect this board tab.')
            now = time.monotonic()
            if now - client.get('last_ink_preview', 0) < .06:
                return
            client['last_ink_preview'] = now
            for other in self.tabs.values():
                if other is client or other['notebook'] != client['notebook']:
                    continue
                # Reserve queue capacity for real saves and AI operations.
                if other['queue'].qsize() >= 8:
                    continue
                try:
                    other['queue'].put_nowait({'event': 'ink', 'tab': client['tab'], 'shape': shape})
                except queue.Full:
                    pass

    def protected(self, notebook, ids, authorized):
        """IDs the AI may not change: pages and page images always, and the
        student's own handwriting unless the user explicitly asked."""
        try:
            store = (self.storage.read(notebook) or {}).get('store', {})
        except (ClientError, DamagedFileError):
            store = {}
        refused = []
        for short in ids:
            shape = store.get(f'shape:{short}') if isinstance(short, str) else None
            kind = shape.get('type') if isinstance(shape, dict) else None
            if kind in PAGE_MATERIAL or (kind in STUDENT_INK and not authorized):
                refused.append(short)
        return refused

    def set_keep_awake(self, enabled):
        if not isinstance(enabled, bool):
            raise ClientError('Keep-awake must be true or false.')
        with self.lock:
            if enabled:
                if sys.platform != 'darwin':
                    raise ClientError('Keep-awake is available on macOS.')
                if self.awake_process is None or self.awake_process.poll() is not None:
                    self.awake_process = subprocess.Popen(
                        ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif self.awake_process is not None:
                if self.awake_process.poll() is None:
                    self.awake_process.terminate()
                try:
                    self.awake_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.awake_process.kill()
                    self.awake_process.wait(timeout=2)
                self.awake_process = None

    def pairing_status(self):
        bridge = read_json(self.storage.data / 'ipad-bridge.json')
        running = False
        if isinstance(bridge, dict):
            try:
                socket.create_connection((bridge['host'], bridge['port']), timeout=.5).close()
                running = True
            except (OSError, KeyError, TypeError):
                pass
        return {'bridge': bridge if running else None, 'devices': self.pairing.devices(),
                'keep_awake_supported': sys.platform == 'darwin',
                'keep_awake': self.awake_process is not None and self.awake_process.poll() is None}

    def call_tool(self, name, args=None):
        args = {} if args is None else args
        if not isinstance(args, dict):
            return text('Provide structured tool arguments.', True)
        if name == 'record_review':
            try:
                started = time.perf_counter()
                review = self.review.record(args)
                return text(dumps({**review, 'timing_ms': {'record_review': round((time.perf_counter() - started) * 1000)}}))
            except ClientError as exc:
                return text(exc.reason, True)
            except Exception:
                log.exception('record_review failed')
                return text('The review failed unexpectedly and was not saved. Retry the same review.', True)
        notebook = args.get('notebook')
        if notebook is not None and not isinstance(notebook, str):
            return text('notebook must be a notebook ID string.', True)
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
            started = time.perf_counter()
            target_tab = None
            # A plain check should capture the visible iPad page in one call.
            # Explicit pages, overviews, notebook choices and listings still win.
            view = self.tablet_view()
            implicit_ipad = (not args.get('frame') and not args.get('overview')
                             and not args.get('list_frames') and view and view.get('frame')
                             and (notebook is None or notebook == view['notebook']))
            if args.get('frame') == '@ipad' or implicit_ipad:
                if not view or not view.get('frame'):
                    return text('No current iPad page is available. Keep GPT Board visible on the iPad, or specify an exact frame.', True)
                if notebook is not None and notebook != view['notebook']:
                    return text('The iPad is showing a different notebook. Use its notebook or an exact frame.', True)
                notebook, target_tab = view['notebook'], view['tab']
                request.pop('frame', None)
                request['current_page'] = True
                request['overview'] = False
            if target_tab is None:
                result = self.send_to_tab('export', request, READ_BOARD_TIMEOUT_SECONDS, notebook)
            else:
                result = self.send_to_tab('export', request, READ_BOARD_TIMEOUT_SECONDS, notebook, target_tab=target_tab)
            capture_ms = round((time.perf_counter() - started) * 1000)
            if target_tab is not None and result.get('ok') and not (result.get('summary', {}).get('frame_id') and result.get('summary', {}).get('frame')):
                return text('Refresh GPT Board on the iPad to enable current-page reads. No review was archived.', True)
            if not result.get('ok'):
                return text(result.get('error', 'Board operation failed.'), True)
            summary = result.get('summary') or {}
            if not isinstance(summary, dict):
                return text('Invalid board summary.', True)
            if 'region' in args and not valid_region(summary.get('region')):
                return text('Refresh the GPT Board tab to enable close-up reads. This tab did not confirm the requested slice, so no cropped review evidence was archived.', True)
            content = []
            png = result.get('png')
            if png is not None:
                try:
                    decode_png(png)
                except ValueError:
                    return text('Invalid PNG image from board tab.', True)
            if png:
                content.append({'type': 'image', 'data': png.split(',', 1)[1], 'mimeType': 'image/png'})
            summary = {**summary, 'target': {'notebook': result['notebook'], 'tab': result['tab']}}
            if self.review.instructions:
                summary['review_instructions'] = self.review.instructions
            if png and (isinstance(args.get('frame'), str) or target_tab is not None) and self.review.enabled:
                try:
                    check = self.review.archive(summary.get('frame') or args.get('frame'), png, result['notebook'], summary.get('region'), summary.get('frame_id'), summary.get('revision'))
                    context = {key: check[key] for key in ('check_id', 'notebook', 'frame')}
                    context['captured_at'] = check['ts']
                    context.update({key: check[key] for key in ('frame_id', 'revision') if key in check})
                    if check.get('region'):
                        context['region'] = check['region']
                    summary['review_context'] = context
                except Exception:
                    log.exception('Check image not saved')
                    summary['review_warning'] = 'The review image could not be archived. Do not claim this review is logged; retry read_board before record_review.'
            summary['timing_ms'] = {'capture': capture_ms, 'total_board_read': round((time.perf_counter() - started) * 1000)}
            content.append({'type': 'text', 'text': dumps(summary)})
            return {'content': content}
        if name in ('add_shapes', 'update_shapes', 'delete_shapes'):
            operation = name.split('_')[0]
            request = dict(args)
            if not request.get('notebook') and self.ai_target:
                if self.ai_target['tab'] not in self.tabs:
                    return text('The selected AI target disconnected. Select it again.', True)
                request.update(notebook=self.ai_target['notebook'])
                request.setdefault('frame', self.ai_target['frame'])
            if not request.get('notebook'):
                return text('Choose an AI target in the board, or pass notebook and frame explicitly.', True)
            if request.get('tab'):
                target = self.tabs.get(request['tab'])
                if not target or target['notebook'] != request['notebook']:
                    return text('That tab target is stale. Select the intended target again.', True)
            try:
                result = self.ai.execute(request['notebook'], operation, request)
                self.notify_changed(request['notebook'], self.storage.revision(request['notebook']), None)
                return text(dumps(result), not result['ok'])
            except ClientError as exc:
                return text(exc.reason, True)
            except OSError:
                return text('Save confirmation failed. Retry the identical operation_id and request to determine the outcome.', True)
        return text(f'Unknown tool: {name}', True)

    def rpc(self, message):
        if (not isinstance(message, dict) or message.get('jsonrpc') != '2.0'
                or not isinstance(message.get('method'), str)
                or isinstance(message.get('id'), (dict, list, bool, float))):
            return {'jsonrpc': '2.0', 'id': None,
                    'error': {'code': -32600, 'message': 'Invalid Request'}}
        if 'params' in message and not isinstance(message['params'], dict):
            return {'jsonrpc': '2.0', 'id': message.get('id'),
                    'error': {'code': -32602, 'message': 'Invalid params'}}
        if 'id' not in message:
            return None
        method, params = message.get('method'), message.get('params') or {}
        response = {'jsonrpc': '2.0', 'id': message['id']}
        if method == 'initialize':
            requested = params.get('protocolVersion')
            version = requested if requested in MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSIONS[0]
            result = {'protocolVersion': version, 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'gpt-board', 'version': SERVER_VERSION},
                      'instructions': "This is the user's personal notebook board. For a check, call read_board once with no arguments to capture the current connected iPad page and evidence; frame '@ipad' explicitly requires the iPad. Use list_frames:true only when locating another page. The image shows handwriting. Use add_shapes / update_shapes / delete_shapes to draw. Default reads prefer the visible iPad; otherwise requests go to the browser tab used most recently. Every result names the notebook it acted on in target.notebook. Check that it is the notebook the user means, and pass notebook to choose another open one. " + self.review.instructions}
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

    response_started = False

    def log_message(self, *_):
        # Avoid logging secret MCP URLs or large notebook payloads.
        pass

    def send_response(self, code, message=None):
        self.response_started = True
        super().send_response(code, message)

    def fail(self, status, body):
        self.close_connection = True
        # Once a status line is out (an SSE stream, or a reply cut off midway),
        # a second one would corrupt the stream; just close the connection.
        if not self.response_started:
            self.respond(status, body)

    def respond(self, status, body=b'', content_type='application/json', headers=None):
        if not isinstance(body, bytes):
            body = dumps(body).encode()
        compressed = (status == 200 and len(body) >= 4096
                      and content_type in ('application/json', 'application/javascript', 'text/javascript', 'text/css', 'text/html')
                      and accepts_gzip(self.headers.get('Accept-Encoding', '')))
        if compressed:
            body = gzip.compress(body, compresslevel=3, mtime=0)
        self.send_response(status)
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if status >= 400:
            # A refused request may still have an unread body on the wire.
            # Closing stops those bytes being read as the next request.
            self.close_connection = True
            self.send_header('Connection', 'close')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def body(self, object_only=True):
        value = json.loads(read_body(self.rfile, self.headers, self.connection), parse_constant=reject_constant)
        if object_only and not isinstance(value, dict):
            raise BodyError('JSON body must be an object.')
        return value

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_HEAD(self):
        self.handle_request()

    def handle_request(self):
        self.response_started = False
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except BodyError as exc:
            self.fail(exc.status, {'error': exc.reason})
        except ClientError as exc:
            self.fail(400, {'error': exc.reason})
        except DamagedFileError as exc:
            log.error('%s', exc.reason)
            self.fail(500, {'error': exc.reason})
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            if urlsplit(self.path).path == self.app.mcp_path:
                self.fail(400, {'jsonrpc': '2.0', 'id': None,
                                'error': {'code': -32700, 'message': 'Parse error'}})
            else:
                self.fail(400, {'error': 'Invalid JSON.'})
        except Exception:
            # The formatter set up in main() redacts the MCP token from the path.
            log.exception('Unhandled error on %s %s', self.command, urlsplit(self.path).path)
            self.fail(500, {'error': 'Internal server error.'})

    def route(self):
        app = self.app
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        arg = lambda key, default=None: query.get(key, [default])[0]
        remote = self.headers.get('cf-connecting-ip') or self.headers.get('x-forwarded-for') or not any(re.fullmatch(re.escape(host) + r'(:\d+)?', self.headers.get('Host', '')) for host in app.allowed_hosts)
        if remote:
            return self.respond(404, b'')
        # A web page on another site can make this browser send requests here.
        # Browsers label those with the page's Origin, so anything not from the
        # board's own address is refused. MCP clients are programs, not web
        # pages, and send no Origin.
        origin = self.headers.get('Origin')
        if origin is not None and origin != 'http://' + self.headers.get('Host', ''):
            return self.respond(403, {'error': 'Requests from other websites are not allowed.'})
        if self.command == 'POST' and self.headers.get_content_type() != 'application/json':
            return self.respond(415, {'error': 'Send JSON with Content-Type: application/json.'})
        if url.path == '/api/health' and self.command == 'GET':
            with app.lock:
                tabs = len(app.tabs)
            return self.respond(200, {'ok': True, 'app': 'gpt-board', 'version': SERVER_VERSION, 'tabs': tabs,
                                      'review': app.review.enabled})
        if url.path == '/api/reviews' and self.command == 'GET':
            if not arg('course'):
                return self.respond(200, {'enabled': app.review.enabled, 'courses': list(app.review.courses)})
            try:
                offset = int(arg('offset', '0'))
            except ValueError:
                raise ClientError('Invalid history offset.') from None
            return self.respond(200, app.review.history(arg('course'), offset))
        if url.path == '/api/shutdown' and self.command == 'POST':
            if app.tabs:
                return self.respond(409, {'error': 'Wait for Saved and close all board tabs before stopping. No live tab was interrupted.'})
            self.respond(200, {'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if url.path == '/api/ai':
            if self.command == 'GET':
                notebook = app.storage.require(arg('notebook', 'default'))
                return self.respond(200, {'target': app.ai_target, 'tabs': [
                    {'tab': t['tab'], 'notebook': t['notebook']} for t in app.tabs.values()],
                    'operations': app.ai.list(notebook)})
            if self.command == 'POST':
                body = self.body()
                notebook = app.storage.require(body.get('notebook'))
                action = body.get('action')
                if action == 'target':
                    tab = app.tabs.get(body.get('tab'))
                    if not tab or tab['notebook'] != notebook:
                        raise ClientError('That tab is no longer open.')
                    from ai_operations import frame_for
                    frame = body.get('frame')
                    if frame != 'whiteboard':
                        frame = frame_for(app.storage.read(notebook)['store'], frame)['id']
                    app.ai_target = {'tab': tab['tab'], 'notebook': notebook, 'frame': frame}
                    return self.respond(200, {'target': app.ai_target})
                if action not in ('approve', 'undo'):
                    raise ClientError('Unsupported AI panel action.')
                # This route is available to same-origin user UI and paired
                # devices, never exposed as an MCP tool or approval flag.
                result = getattr(app.ai, action)(notebook, body.get('operation_id'))
                app.notify_changed(notebook, app.storage.revision(notebook), None)
                return self.respond(200, result)
        if url.path == '/api/pairing':
            # Reached only from this computer: the iPad bridge does not forward it.
            if self.command == 'GET':
                return self.respond(200, app.pairing_status())
            if self.command == 'POST':
                body = self.body()
                if body.get('action') == 'keep-awake':
                    app.set_keep_awake(body.get('enabled'))
                    return self.respond(200, app.pairing_status())
                if body.get('action') == 'invite':
                    info = {'code': app.pairing.invite(), 'expires_in': INVITE_SECONDS, **app.pairing_status()}
                    if info.get('bridge'):
                        try:
                            import segno
                            info['qr'] = segno.make(info['bridge']['origin'] + '/pair?code=' + info['code'], micro=False).svg_data_uri(scale=5)
                        except ImportError:
                            info['qr_unavailable'] = 'Install requirements.txt for QR pairing; the code and copy link still work.'
                    return self.respond(201, info)
                if body.get('action') == 'revoke' and isinstance(body.get('id'), str):
                    app.pairing.revoke(body['id'])
                    return self.respond(200, app.pairing_status())
                return self.respond(400, {'error': 'Unsupported pairing action.'})
        if url.path == app.mcp_path:
            if self.command != 'POST':
                return self.respond(405, b'', headers={'Allow': 'POST'})
            body = self.body(object_only=False)
            if isinstance(body, list) and len(body) > 16:
                return self.respond(400, {'error': 'MCP batches support at most 16 requests.'})
            if body == []:
                return self.respond(200, app.rpc(None))
            result = [r for msg in body if (r := app.rpc(msg)) is not None] if isinstance(body, list) else app.rpc(body)
            return self.respond(202, b'') if result is None or result == [] else self.respond(200, result)
        if url.path == '/api/notebooks':
            if self.command == 'GET':
                kind = arg('kind', 'notebook')
                if kind not in ('notebook', 'whiteboard'):
                    return self.respond(400, {'error': 'Invalid collection type.'})
                books = [b for b in app.storage.metadata()['notebooks'] if b.get('kind', 'notebook') == kind and (arg('all') == '1' or b.get('status', 'active') == 'active')]
                if arg('previews'):
                    books = [{**b, 'thumbnail': app.storage.path(b['id'], 'thumbnail.json').is_file()} for b in books]
                return self.respond(200, {'notebooks': books})
            if self.command == 'POST':
                body = self.body()
                if body.get('action') != 'create':
                    return self.respond(200, {'notebook': app.storage.manage(body.get('notebook'), body.get('action'), body.get('name'))})
                try:
                    return self.respond(201, {'notebook': app.storage.create(body.get('name'), body.get('kind', 'notebook'))})
                except ClientError as exc:
                    return self.respond(400, {'error': exc.reason})
        if url.path == '/api/thumbnail':
            if self.command == 'GET':
                notebook = app.storage.require(arg('notebook'))
                preview = app.storage.read(notebook, 'thumbnail.json')
                if not preview:
                    return self.respond(404, {'error': 'No saved thumbnail.'})
                return self.respond(200, decode_png(preview['png']), 'image/png')
            if self.command == 'POST':
                body = self.body()
                notebook = app.storage.require(body.get('notebook'))
                if not isinstance(body.get('png'), str) or len(body['png']) > 256 * 1024:
                    raise ClientError('Thumbnail is too large.')
                check_png(body['png'])
                with LOCK:
                    if body.get('base') != app.storage.revision(notebook):
                        raise ClientError('Preview is stale; save before refreshing it.')
                    from board_storage import write_json
                    write_json(app.storage.path(notebook, 'thumbnail.json'), {'rev': body['base'], 'png': body['png']})
                return self.respond(200, {'ok': True})
        if url.path == '/api/import' and self.command == 'POST':
            body = self.body()
            action = body.get('action')
            if action == 'prepare':
                result = app.media.prepare(body.get('name'), body.get('data'))
            elif action == 'render':
                result = app.media.render(body.get('job'), body.get('page'))
            elif action == 'cancel':
                result = app.media.cancel(body.get('job'))
            elif action == 'commit':
                result = app.media.commit(body.get('job'), body.get('notebook'), body.get('base'), body.get('targets'))
                app.notify_changed(body['notebook'], app.storage.revision(body['notebook']), None)
            else:
                raise ClientError('Unsupported import action.')
            return self.respond(200, result)
        if url.path == '/api/export-pdf' and self.command == 'POST':
            body = self.body()
            result = app.media.export_stage(body) if body.get('action') else app.media.export_pdf(body)
            return self.respond(200, result, 'application/pdf' if isinstance(result, bytes) else 'application/json')
        if url.path == '/api/files':
            if self.command == 'GET':
                return self.respond(200, app.storage.export_notebook(app.storage.require(arg('notebook'))))
            if self.command == 'POST':
                body = self.body()
                preview = app.storage.inspect_export(body.get('document'))
                if body.get('preview'):
                    return self.respond(200, preview)
                return self.respond(201, {'notebook': app.storage.import_notebook(body['document'])})
        if url.path == '/api/events' and self.command == 'POST':
            body = self.body()
            if body.get('action') == 'startup':
                tab, timings = body.get('tab'), body.get('timings')
                if not isinstance(tab, str) or len(tab)>120 or not isinstance(timings, dict) or len(dumps(timings))>8000:
                    raise ClientError('Invalid startup timings.')
                with app.lock:
                    app.startup_timings[tab] = timings
                    while len(app.startup_timings)>16:
                        app.startup_timings.pop(next(iter(app.startup_timings)))
            elif body.get('action') == 'ink':
                app.preview_ink(body)
            else:
                app.update_tablet_view(body)
            return self.respond(200, {'ok': True})
        if url.path == '/api/events' and self.command == 'GET' and arg('startup') == '1':
            return self.respond(200, app.startup_timings)
        if url.path == '/api/events' and self.command == 'GET' and arg('presence') == '1':
            return self.respond(200, {'view': app.tablet_view()})
        if url.path == '/api/events' and self.command == 'GET':
            try:
                notebook = app.storage.require(arg('notebook', 'default'))
            except ClientError as exc:
                return self.respond(404, {'error': exc.reason})
            return self.events(arg('tab', 'legacy-' + str(uuid.uuid4())), notebook)
        if url.path == '/api/ack' and self.command == 'POST':
            body = self.body()
            op_id = body.pop('opId', None)
            if not isinstance(op_id, str) or not op_id:
                raise BodyError('Provide a valid operation ID.')
            if 'summary' in body and not isinstance(body['summary'], dict):
                raise BodyError('Invalid board summary.')
            check_png(body.get('png'))
            with app.lock:
                target = app.pending.pop(op_id, None)
                if target:
                    target.put_nowait(body)
                    app.acknowledged[op_id] = time.monotonic()
                app.acknowledged = {key: when for key, when in app.acknowledged.items() if time.monotonic() - when < 60}
            # delivered: False means the AI request already timed out; the tab
            # then undoes that edit so a retry cannot apply it twice.
            return self.respond(200, {'ok': True, 'delivered': op_id in app.acknowledged})
        if url.path == '/api/board':
            if self.command == 'GET':
                try:
                    notebook = app.storage.require(arg('notebook', 'default'))
                    if arg('asset'):
                        asset_id = arg('asset')
                        record = app.storage.read(notebook, record_id=asset_id)
                        image = image_bytes({'store': {asset_id: record or {}}}, asset_id)
                        if image is None:
                            return self.respond(404, {'error': 'Image not found.'})
                        return self.respond(200, image[0], image[1])
                    with LOCK:
                        rev = app.storage.revision(notebook)
                        if arg('shapes') and arg('since') == rev:
                            return self.respond(200, {'rev': rev})
                        snapshot = app.storage.read(notebook)
                    if arg('lazy') and snapshot:
                        snapshot = browser_snapshot(snapshot, notebook, rev)
                    if arg('shapes'):
                        # For a tab catching up: the shapes without the large page images.
                        shapes = [r for r in (snapshot or {}).get('store', {}).values()
                                  if isinstance(r, dict) and r.get('typeName') == 'shape']
                        return self.respond(200, {'rev': rev, 'shapes': shapes})
                    return self.respond(200, snapshot, headers={'X-Board-Rev': rev})
                except ClientError as exc:
                    return self.respond(404, {'error': exc.reason})
            if self.command == 'POST':
                body = self.body()
                tab = body.get('tab')
                if not isinstance(tab, str) or not tab:
                    return self.respond(409, {'error': 'Reload this board tab.'})
                try:
                    notebook = app.storage.require(body.get('notebook', 'default'))
                except ClientError as exc:
                    return self.respond(404, {'error': exc.reason})
                snapshot, summary, png = body.get('snapshot'), body.get('summary'), body.get('png')
                if not isinstance(summary, dict) or not finite(summary.get('shapeCount')) or summary['shapeCount'] < 0:
                    return self.respond(400, {'error': 'Invalid board save; existing board was preserved.'})
                check_png(png)
                base = body.get('base')
                if base is not None and not isinstance(base, str):
                    return self.respond(400, {'error': 'Invalid base revision.'})
                try:
                    if 'changes' in body:
                        rev = app.storage.save_changes(notebook, body['changes'], summary, base)
                    else:
                        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('store'), dict) or not snapshot.get('schema'):
                            return self.respond(400, {'error': 'Invalid board save; existing board was preserved.'})
                        rev = app.storage.save_snapshot(notebook, snapshot, summary, png, base)
                except ConflictError as exc:
                    # Nothing was written. The tab merges these shapes with its own edits and retries.
                    return self.respond(409, {'error': exc.reason, 'conflict': True, 'rev': exc.rev, 'shapes': exc.shapes})
                except StaleTabError as exc:
                    return self.respond(409, {'error': exc.reason})
                except ClientError as exc:
                    return self.respond(413 if isinstance(exc, BoardSizeError) else 400, {'error': exc.reason})
                with app.lock:
                    app.active_tab = tab
                    app.active_notebook = notebook
                    if tab in app.tabs:
                        app.tabs[tab]['notebook'] = notebook
                app.notify_changed(notebook, rev, tab)
                return self.respond(200, {'ok': True, 'rev': rev})
        if self.command not in ('GET', 'HEAD'):
            return self.respond(405, b'')
        dist = (app.storage.root / 'dist').resolve()
        path = (dist / (unquote(url.path).lstrip('/') or 'index.html')).resolve()
        if not path.is_relative_to(dist) or not path.is_file():
            return self.respond(404, b'Not found. Did you run python3 build.py?', 'text/plain')
        mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.png': 'image/png', '.json': 'application/json'}
        # no-cache: after an update, a reload always gets the current frontend.
        return self.respond(200, path.read_bytes(), mime.get(path.suffix, 'application/octet-stream'), {'Cache-Control': 'no-cache'})

    def events(self, tab, notebook):
        if len(self.app.tabs) >= 16 and tab not in self.app.tabs:
            return self.respond(503, {'error': 'Too many open board tabs (limit 16). Close an unused tab and retry.'})
        if len(tab) > 120:
            return self.respond(400, {'error': 'Tab ID is too long.'})
        client = {'queue': queue.Queue(maxsize=SSE_QUEUE_SIZE), 'notebook': notebook, 'tab': tab}
        with self.app.lock:
            if not tab.startswith('legacy-'):
                self.app.tabs.pop(tab, None)  # a reconnecting tab moves to the end: most recent last
                self.app.tabs[tab] = client
                if self.app.active_tab is None:
                    self.app.active_tab = tab  # the first tab after startup; afterwards only a save changes it
                    self.app.active_notebook = notebook
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
                if self.app.tabs.get(tab) is not client and not tab.startswith('legacy-'):
                    break
                # HTTPServer has no Node-style request close event. Detect disconnected
                # browsers promptly so operations cannot route to a stale tab.
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                    break
                try:
                    event = client['queue'].get(timeout=SSE_POLL_SECONDS)
                    # Named events (such as "changed") are ignored by older tabs,
                    # which only listen for unnamed operation messages.
                    name = event.pop('event', None) if isinstance(event, dict) else None
                    data = ((f'event: {name}\n' if name else '') + 'data: ' + dumps(event) + '\n\n').encode()
                except queue.Empty:
                    if time.monotonic() - last_ping < SSE_PING_INTERVAL_SECONDS:
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
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', DEFAULT_PORT)))
    parser.add_argument('--lan', nargs='?', const='auto', help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if not 1 <= options.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if options.lan:
        # Direct LAN mode let anyone on the network read and change notebooks
        # without pairing. The paired bridge is the one supported way in.
        parser.error('--lan was removed because it had no authentication. '
                     'Use the paired iPad bridge instead: python3 board.py start')
    return options


def main():
    options = launch_options()
    app = BoardApp()
    app.port = options.port
    setup_logging(app.mcp_path.rsplit('/', 1)[-1])
    server = BoundedHTTPServer(('127.0.0.1', app.port), Handler)
    server.daemon_threads = True
    server.app = app
    log.info('Board: http://localhost:%s (Python)', app.port)
    if not app.review.enabled:
        log.info('Review logging is not configured; drawing and AI reads work without it (see README).')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.set_keep_awake(False)
        server.server_close()


if __name__ == '__main__':
    with file_lock(ROOT / 'data/server.lock', blocking=False):
        main()
