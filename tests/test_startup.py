"""Startup regressions use fake children and temporary files, never live services."""
import contextlib
import io
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import board


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(board, 'ROOT', Path(temp)))
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.health = self.stack.enter_context(patch.object(board, 'health', return_value=None))
        self.stack.enter_context(patch.object(board, 'stale_build', return_value=False))
        self.wifi = self.stack.enter_context(patch.object(board, 'wifi_address', return_value=None))
        self.spawn = self.stack.enter_context(patch.object(board.subprocess, 'Popen'))
        self.open_browser = self.stack.enter_context(patch.object(board.webbrowser, 'open'))
        self.signal_handler = self.stack.enter_context(patch.object(board.signal, 'signal', return_value=signal.SIG_DFL))
        self.args = SimpleNamespace(no_open=False)

    def child(self, exit_code=None):
        child = Mock()
        child.poll.return_value = exit_code
        child.wait.return_value = exit_code or 0
        return child

    def test_existing_frontend_can_be_preserved_with_keep_awake(self):
        self.args.no_build = True
        self.args.keep_awake = True
        self.args.no_open = True
        self.spawn.return_value = self.child(0)
        with patch.object(board, 'stale_build', return_value=True), patch.object(board, 'build') as build, patch.object(board, 'wait_until_ready'), patch.object(board, 'enable_keep_awake') as awake:
            self.assertEqual(board.start(self.args), 0)
            build.assert_not_called()
            awake.assert_called_once()

    def test_failed_server_is_not_announced_or_opened(self):
        child = self.child(1)
        self.spawn.return_value = child
        with self.assertRaisesRegex(SystemExit, 'exited with code 1'):
            board.start(self.args)
        self.assertNotIn('Board on this computer:', self.output.getvalue())
        self.open_browser.assert_not_called()
        child.wait.assert_called_once_with(timeout=board.STOP_TIMEOUT_SECONDS)
        self.signal_handler.assert_called_with(signal.SIGTERM, signal.SIG_DFL)

    def test_readiness_timeout_stops_the_started_child(self):
        child = self.child()
        self.spawn.return_value = child
        with (patch.object(board.time, 'monotonic', side_effect=[0, 6]),
              self.assertRaisesRegex(SystemExit, 'did not respond')):
            board.start(self.args)
        child.terminate.assert_called_once()
        child.wait.assert_called_once()
        self.open_browser.assert_not_called()

    def test_interrupt_and_sigterm_during_startup_cleanup(self):
        for use_term in (False, True):
            with self.subTest(sigterm=use_term):
                child = self.child()
                self.spawn.return_value = child
                self.signal_handler.reset_mock()

                def interrupt(*_, use_term=use_term):
                    if use_term:
                        handler = self.signal_handler.call_args_list[0].args[1]
                        handler(signal.SIGTERM, None)
                    raise KeyboardInterrupt

                with patch.object(board, 'wait_until_ready', side_effect=interrupt):
                    self.assertEqual(board.start(self.args), 0)
                child.terminate.assert_called_once()
                child.wait.assert_called_once()
                self.signal_handler.assert_called_with(signal.SIGTERM, signal.SIG_DFL)

    def test_interrupt_while_bridge_starts_stops_both_children(self):
        main, bridge = self.child(), self.child()
        self.spawn.side_effect = [main, bridge]
        self.wifi.return_value = '192.0.2.1'
        with patch.object(board, 'wait_until_ready', side_effect=[None, KeyboardInterrupt]):
            self.assertEqual(board.start(self.args), 0)
        for child in (main, bridge):
            child.terminate.assert_called_once()
            child.wait.assert_called_once()
        self.open_browser.assert_not_called()

    def test_bridge_spawn_failure_keeps_board_supervised_until_stop(self):
        main = self.child()
        self.spawn.side_effect = [main, OSError('bridge spawn failed')]
        self.wifi.return_value = '192.0.2.1'
        with (patch.object(board, 'wait_until_ready'),
              patch.object(board.time, 'sleep', side_effect=KeyboardInterrupt)):
            self.assertEqual(board.start(self.args), 0)
        self.assertIn('iPad bridge unavailable: bridge spawn failed', self.output.getvalue())
        self.assertIn('Board on this computer:', self.output.getvalue())
        main.terminate.assert_called_once()
        main.wait.assert_called_once()
        self.assertEqual(self.spawn.call_args_list[0].args[0][0], sys.executable)
        self.assertEqual(self.spawn.call_args_list[1].kwargs['env']['IPAD_HOST'], '192.0.2.1')

    def test_unexpected_server_exit_stops_bridge_and_returns_failure(self):
        main, bridge = self.child(7), self.child()
        self.spawn.side_effect = [main, bridge]
        self.wifi.return_value = '192.0.2.1'
        with (patch.object(board, 'wait_until_ready'),
              self.assertRaisesRegex(SystemExit, 'stopped with code 7')):
            board.start(self.args)
        bridge.terminate.assert_called_once()
        bridge.wait.assert_called_once()
        main.wait.assert_called_once()

    def test_running_older_server_is_not_replaced(self):
        self.health.return_value = 'old'
        with self.assertRaisesRegex(SystemExit, 'older GPT Board server'):
            board.start(self.args)
        self.spawn.assert_not_called()
        self.signal_handler.assert_not_called()


class DiagnosticTests(unittest.TestCase):
    def test_stale_build_checks_the_served_index_too(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'src').mkdir()
            (root / 'dist/native').mkdir(parents=True)
            for name in board.FILES:
                (root / 'src' / name).write_text('same')
                (root / 'dist/native' / name).write_text('same')
            self.assertTrue(board.stale_build(root), 'missing served index must trigger a build')
            (root / 'dist/index.html').write_text('same')
            self.assertFalse(board.stale_build(root))
            (root / 'dist/index.html').write_text('old')
            self.assertTrue(board.stale_build(root), 'stale served index must trigger a build')

    def test_health_handles_other_programs_and_closes_failed_requests(self):
        conn = Mock()
        response = conn.getresponse.return_value
        response.status = 200
        response.getheader.return_value = 'AnotherApp'
        with patch.object(board.http.client, 'HTTPConnection', return_value=conn):
            for body in (b'[]', b'null', b'{"app":"gpt-board"}', b'not json'):
                response.read.return_value = body
                self.assertEqual(board.health(), 'other')
            conn.request.side_effect = ConnectionError('unreachable')
            self.assertIsNone(board.health())
        self.assertEqual(conn.close.call_count, 5)

    def test_bridge_probe_reads_form_without_pairing(self):
        conn = Mock()
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = b'<title>Pair with GPT Board</title>'
        with patch.object(board.http.client, 'HTTPConnection', return_value=conn):
            self.assertTrue(board.bridge_running({'host': '192.0.2.1', 'port': 8788}))
            conn.request.assert_called_with('GET', '/pair')
            response.read.return_value = b'<title>Unrelated server</title>'
            self.assertFalse(board.bridge_running({'host': '192.0.2.1', 'port': 8788}))
            conn.request.side_effect = ConnectionError('unreachable')
            self.assertFalse(board.bridge_running({'host': '192.0.2.1', 'port': 8788}))
        self.assertEqual(conn.close.call_count, 3)

    def test_status_does_not_trust_stale_bridge_state(self):
        saved = {'host': '192.0.2.1', 'port': 8788, 'names': ['http://192.0.2.1:8788']}
        output = io.StringIO()
        with (patch.object(board, 'health', return_value={'version': '1.2.0', 'tabs': 0}),
              patch.object(board, 'stale_build', return_value=False),
              patch.object(board, 'wifi_address', return_value='192.0.2.1'),
              patch.object(board, 'read_json', side_effect=[saved, None]),
              patch.object(board, 'BoardStorage') as storage,
              patch.object(board, 'ReviewService', return_value=SimpleNamespace(enabled=False)),
              patch.object(board, 'bridge_running', return_value=False),
              contextlib.redirect_stdout(output)):
            storage.return_value.metadata.return_value = {'notebooks': []}
            self.assertEqual(board.status(None), 1)
        self.assertIn('FIX  iPad bridge is not responding', output.getvalue())

    def test_shutdown_wait_is_bounded(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('fixture', board.STOP_TIMEOUT_SECONDS), 0]
        board.stop_child(child)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)


if __name__ == '__main__':
    unittest.main()
