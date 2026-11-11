"""Bound live HTTP workers before accepting another connection."""

import threading
from http.server import ThreadingHTTPServer
from socketserver import TCPServer


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(64)
        super().__init__(*args, **kwargs)

    def server_bind(self):
        # HTTPServer resolves a reverse-DNS name here. These local servers use
        # explicit origins/Host validation and never need that blocking lookup.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 2\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        request.settimeout(40)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()
