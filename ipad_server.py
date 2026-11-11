#!/usr/bin/env python3
"""Paired local Wi-Fi bridge for iPad. Never forwards the MCP endpoint."""
import html
import http.client
import json
import logging
import os
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from board_storage import ROOT, setup_logging, write_json
from file_lock import file_lock
from http_body import BodyError, read_body
from http_limits import BoundedHTTPServer
from pairing import Pairing

log = logging.getLogger('gpt-board.ipad')


def wifi_host():
    if os.environ.get('IPAD_HOST'):
        return os.environ['IPAD_HOST']
    # Preserve the Mac's Wi-Fi address without requiring an external Python package.
    for interface in ('en0', 'en1', 'en2'):
        try:
            address = subprocess.check_output(['/usr/sbin/ipconfig', 'getifaddr', interface], stderr=subprocess.DEVNULL, text=True).strip()
            socket.inet_aton(address)
            return address
        except (OSError, subprocess.CalledProcessError):
            pass
    addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    for entry in addresses:
        if not entry[4][0].startswith('127.'):
            return entry[4][0]
    raise RuntimeError('Connect the Mac to Wi-Fi, then run python3 ipad_server.py again.')


def local_hostname():
    """Use the operating system's Bonjour name, never advertise a competing one."""
    name = socket.gethostname()
    if sys.platform == 'darwin':
        try:
            name = subprocess.check_output(['/usr/sbin/scutil', '--get', 'LocalHostName'],
                                           text=True, stderr=subprocess.DEVNULL, timeout=2).strip() + '.local'
        except (OSError, subprocess.SubprocessError):
            pass
    if re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.local', name):
        return name.lower()
    return None


PAIR_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pair with GPT Board</title>
<body style="font:17px -apple-system,system-ui,sans-serif;max-width:440px;margin:12vh auto;padding:0 20px;color:#26374e">
<h1 style="font-size:24px">Pair this device</h1>
<p>On your Mac, open GPT Board and choose <b>Connect iPad</b> (or run <code>python3 board.py pair</code>).
Type the code it shows. A code works once and expires after ten minutes.</p>
<p style="color:#a02b38" role="alert">%s</p>
<form method="post" action="/pair"><label for="code">Pairing code</label><br>
<input id="code" name="code" required autocomplete="off" autocapitalize="characters" spellcheck="false"
 style="font:inherit;letter-spacing:3px;padding:12px;margin:8px 0;width:100%%;box-sizing:border-box">
<button style="font:inherit;padding:12px 18px;background:#285f9f;color:#fff;border:0;border-radius:8px">Pair</button>
</form></body></html>"""


def device_name(user_agent):
    for label in ('iPad', 'iPhone', 'Macintosh', 'Android', 'Windows'):
        if label in user_agent:
            return 'Mac or iPad (Safari)' if label == 'Macintosh' else label  # iPadOS Safari reports itself as a Mac
    return 'Browser'


class Bridge(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # Pairing keys must not appear in request logs.

    def reply(self, status, body=b'', headers=None):
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(body)

    def pair_page(self, status, message=''):
        page = (PAIR_PAGE % html.escape(message)).encode()
        self.reply(status, page, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'})

    def pair(self, url):
        """Exchange a one-time code (typed into the form, or in a link) for this device's own cookie."""
        if self.command == 'POST':
            form = parse_qs(read_body(self.rfile, self.headers, self.connection).decode('utf-8', 'replace'))
        else:
            form = parse_qs(url.query)
        code = form.get('code', [''])[0]
        if not code:
            return self.pair_page(200)
        token = self.server.pairing.redeem(code, device_name(self.headers.get('User-Agent', '')))
        if not token:
            return self.pair_page(403, 'That code is wrong or has expired. Make a new one on your Mac.')
        # The browser caps cookie lifetimes; the server ends a session 30 days after its last use.
        self.reply(303, headers={'Set-Cookie': f'board_ipad={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=34560000',
                                 'Location': '/', 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    def do_GET(self):
        self.proxy()

    def do_POST(self):
        self.proxy()

    def do_HEAD(self):
        self.proxy()

    def proxy(self):
        upstream = None
        sent = False
        try:
            config = self.server
            url = urlsplit(self.path)
            host = self.headers.get('Host')
            if host not in config.authorities or self.headers.get('x-forwarded-for') or self.headers.get('cf-connecting-ip'):
                return self.reply(403, b'Local Wi-Fi access only.')
            if self.headers.get('Origin') and self.headers['Origin'] != 'http://' + host:
                return self.reply(403)
            if url.path == '/pair':
                return self.pair(url)
            cookies = [c.strip() for c in self.headers.get('Cookie', '').split(';')]
            token = next((c[len('board_ipad='):] for c in cookies if c.startswith('board_ipad=')), '')
            if not token or not config.pairing.device(token):
                return self.pair_page(403)
            if not (url.path in ('/', '/index.html', '/api/board', '/api/events', '/api/ack', '/api/notebooks', '/api/ai', '/api/files', '/api/import', '/api/export-pdf', '/api/reviews', '/api/thumbnail')
                    or url.path.startswith(('/assets/', '/native/'))):
                return self.reply(404)
            body = read_body(self.rfile, self.headers, self.connection)
            excluded = {'host', 'cookie', 'origin', 'referer', 'connection', 'transfer-encoding', 'content-length', 'proxy-connection'}
            headers = {key: value for key, value in self.headers.items() if key.lower() not in excluded}
            headers.update(Host=f'localhost:{config.upstream}', Connection='close')
            # Staged PDF assembly has a 120-second worker limit. Keep the bridge
            # alive long enough to return its result (ordinary requests stay bounded).
            timeout = 130 if url.path == '/api/export-pdf' else 35
            upstream = http.client.HTTPConnection('127.0.0.1', config.upstream, timeout=timeout)
            upstream.request(self.command, self.path, body=body or None, headers=headers)
            response = upstream.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ('connection', 'transfer-encoding', 'cache-control', 'server', 'date'):
                    self.send_header(key, value)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            sent = True
            self.close_connection = True
            if self.command != 'HEAD':
                if response.getheader('Content-Type', '').split(';')[0] == 'text/event-stream':
                    # Check revocation even when the upstream stream is idle.
                    first = response.read1(65536)
                    if first:
                        self.wfile.write(first); self.wfile.flush()
                    last_check = 0
                    while True:
                        if time.monotonic() - last_check > 1:
                            if not config.pairing.device(token):
                                break
                            last_check = time.monotonic()
                        if not select.select([response.fp], [], [], .25)[0]:
                            continue
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk); self.wfile.flush()
                else:
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except BodyError as exc:
            if not sent:
                self.reply(exc.status, json.dumps({'error': exc.reason}).encode(),
                           {'Content-Type': 'application/json'})
        except (OSError, ValueError, http.client.HTTPException):
            if not sent:
                self.reply(502, b'Start the board on your Mac with python3 server.py.')
        finally:
            if upstream:
                upstream.close()


def main():
    host = wifi_host()
    port = int(os.environ.get('IPAD_PORT', '8788'))
    server = BoundedHTTPServer((host, port), Bridge)
    server.daemon_threads = True
    server.authority = f'{host}:{port}'
    server.origin = 'http://' + server.authority
    # The Mac's Bonjour name survives a change of Wi-Fi address, so a device
    # paired through it stays paired. Both names reach this same bridge.
    name = local_hostname()
    server.authorities = {server.authority} | ({f'{name}:{port}'} if name else set())
    if name:
        server.origin = f'http://{name}:{port}'
    server.upstream = int(os.environ.get('BOARD_PORT', '8787'))
    server.pairing = Pairing(ROOT / 'data')
    setup_logging()
    # Lets the board's Connect iPad screen show the address to type.
    state = ROOT / 'data/ipad-bridge.json'
    write_json(state, {'origin': server.origin, 'host': host, 'port': port,
                       'fallback_origin': 'http://' + server.authority,
                       'names': [server.origin] + sorted('http://' + a for a in server.authorities if 'http://' + a != server.origin)})
    log.info('iPad bridge: %s (Python)', server.origin)
    if os.environ.get('SHOW_PAIRING_LINK') == '1':
        # Deliberately printed, not logged: the user asked to see the one-time
        # code on this terminal, and the log formatter would redact it.
        code = server.pairing.invite()
        print(f'Pair iPad (once, within 10 minutes): {server.origin}/pair?code={code}', flush=True)
    def watch_upstream():
        failures = 0
        while not stopped.wait(5):
            try:
                socket.create_connection(('127.0.0.1', server.upstream), timeout=1).close()
                failures = 0
            except OSError:
                failures += 1
                if failures >= 3:
                    log.info('Board is unavailable; closing orphaned iPad bridge. Restart with board.py start.')
                    server.shutdown()
                    return
    advertisement = None
    if sys.platform == 'darwin' and Path('/usr/bin/dns-sd').is_file():
        advertisement = subprocess.Popen(['/usr/bin/dns-sd', '-R', 'GPT Board', '_gptboard._tcp', 'local', str(port)],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def terminate(*_):
        raise KeyboardInterrupt
    previous_term = signal.signal(signal.SIGTERM, terminate)
    stopped = threading.Event()
    threading.Thread(target=watch_upstream, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        signal.signal(signal.SIGTERM, previous_term)
        if advertisement is not None:
            advertisement.terminate()
            try: advertisement.wait(timeout=2)
            except subprocess.TimeoutExpired:
                advertisement.kill(); advertisement.wait(timeout=2)
        server.server_close()
        state.unlink(missing_ok=True)


if __name__ == '__main__':
    with file_lock(ROOT / 'data/bridge.lock', blocking=False):
        main()
