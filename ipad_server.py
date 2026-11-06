#!/usr/bin/env python3
"""Paired local Wi-Fi bridge for iPad. Never forwards the MCP endpoint."""
import http.client
import json
import logging
import os
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from board_storage import ROOT, secret, setup_logging
from http_body import BodyError, read_body

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
            if self.headers.get('Host') != config.authority or self.headers.get('x-forwarded-for') or self.headers.get('cf-connecting-ip'):
                return self.reply(403, b'Local Wi-Fi access only.')
            if url.path == '/pair' and parse_qs(url.query).get('key', [None])[0] == config.token:
                return self.reply(303, headers={'Set-Cookie': f'board_ipad={config.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000', 'Location': '/', 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
            if not any(cookie.strip() == 'board_ipad=' + config.token for cookie in self.headers.get('Cookie', '').split(';')):
                return self.reply(403, b'Open the iPad pairing link from your Mac.')
            if self.headers.get('Origin') and self.headers['Origin'] != config.origin:
                return self.reply(403)
            if not (url.path in ('/', '/index.html', '/native/app.js', '/native/model.js', '/native/style.css', '/api/board', '/api/events', '/api/ack', '/api/notebooks') or url.path.startswith('/assets/')):
                return self.reply(404)
            body = read_body(self.rfile, self.headers, self.connection)
            excluded = {'host', 'cookie', 'origin', 'referer', 'connection', 'transfer-encoding', 'content-length', 'proxy-connection'}
            headers = {key: value for key, value in self.headers.items() if key.lower() not in excluded}
            headers.update(Host=f'localhost:{config.upstream}', Connection='close')
            upstream = http.client.HTTPConnection('127.0.0.1', config.upstream, timeout=35)
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
    server = ThreadingHTTPServer((host, port), Bridge)
    server.daemon_threads = True
    server.authority = f'{host}:{port}'
    server.origin = 'http://' + server.authority
    server.upstream = int(os.environ.get('BOARD_PORT', '8787'))
    server.token = secret(ROOT / 'data/ipad-token', 24)
    setup_logging(server.token)
    log.info('iPad bridge: %s (Python)', server.origin)
    if os.environ.get('SHOW_PAIRING_LINK') == '1':
        # Deliberately printed, not logged: the user asked to see the secret
        # link on this terminal, and the log formatter would redact it.
        print(f'Pair iPad: {server.origin}/pair?key={server.token}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
