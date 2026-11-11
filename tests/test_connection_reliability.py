import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ipad_server
import server


class ConnectionReliabilityTests(unittest.TestCase):
    def test_bonjour_uses_system_name_not_dns_assigned_hostname(self):
        with patch.object(ipad_server.sys, 'platform', 'darwin'), patch.object(ipad_server.socket, 'gethostname', return_value='unrelated.example'), patch.object(ipad_server.subprocess, 'check_output', return_value='My-Mac\n'):
            self.assertEqual(ipad_server.local_hostname(), 'my-mac.local')

    def test_invalid_and_unavailable_hostname_leave_ip_fallback(self):
        with patch.object(ipad_server.sys, 'platform', 'darwin'), patch.object(ipad_server.socket, 'gethostname', return_value='localhost'), patch.object(ipad_server.subprocess, 'check_output', side_effect=OSError):
            self.assertIsNone(ipad_server.local_hostname())
        with patch.object(ipad_server.sys, 'platform', 'darwin'), patch.object(ipad_server.subprocess, 'check_output', return_value='bad/name'):
            self.assertIsNone(ipad_server.local_hostname())

    def test_keep_awake_is_opt_in_idempotent_and_releases_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=server.BoardApp(Path(tmp))
            child=Mock();child.poll.return_value=None
            self.assertIsNone(app.awake_process)
            with patch.object(server.sys,'platform','darwin'), patch.object(server.subprocess,'Popen',return_value=child) as spawn:
                app.set_keep_awake(True);app.set_keep_awake(True)
                spawn.assert_called_once()
                self.assertEqual(spawn.call_args.args[0][:3], ['/usr/bin/caffeinate','-i','-w'])
                app.set_keep_awake(False)
                child.terminate.assert_called_once();child.wait.assert_called_once()
                self.assertIsNone(app.awake_process)
            with self.assertRaises(server.ClientError):app.set_keep_awake('yes')

    def test_export_bridge_waits_for_worker_but_ordinary_requests_stay_short(self):
        import io
        for path, expected in [('/api/export-pdf', 130), ('/api/board', 35)]:
            with self.subTest(path=path):
                bridge = object.__new__(ipad_server.Bridge)
                bridge.server = Mock(authorities={'board.local'}, upstream=8787)
                bridge.path = path
                bridge.headers = {'Host': 'board.local', 'Cookie': 'board_ipad=paired'}
                bridge.command = 'POST'
                bridge.rfile = io.BytesIO()
                bridge.wfile = io.BytesIO()
                bridge.connection = Mock()
                bridge.send_response = Mock()
                bridge.send_header = Mock()
                bridge.end_headers = Mock()
                response = Mock(status=200)
                response.getheaders.return_value = []
                response.getheader.return_value = 'application/pdf'
                response.read1.return_value = b''
                with patch.object(ipad_server, 'read_body', return_value=b'{}'), patch.object(ipad_server.http.client, 'HTTPConnection') as connection:
                    connection.return_value.getresponse.return_value = response
                    bridge.proxy()
                    connection.assert_called_once_with('127.0.0.1', 8787, timeout=expected)

    def test_compression_quality_fractions_are_not_treated_as_zero(self):
        for header in ['gzip', 'br, gzip;q=0.5', 'GZIP; q = 0.8']:
            self.assertTrue(server.accepts_gzip(header), header)
        for header in ['', 'br', 'gzip;q=0', 'gzip;q=0.0', 'gzip;q=invalid']:
            self.assertFalse(server.accepts_gzip(header), header)
