"""Local server startup must not depend on reverse DNS."""
import unittest
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch

from http_limits import BoundedHTTPServer


class HTTPStartupTests(unittest.TestCase):
    def test_bind_does_not_resolve_dns(self):
        with patch("socket.getfqdn", side_effect=AssertionError("Unexpected DNS lookup")), \
                BoundedHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertGreater(server.server_port, 0)
            self.assertEqual(server.server_port, server.socket.getsockname()[1])
