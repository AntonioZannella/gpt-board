"""Connection, process and recovery boundaries against disposable services."""

import http.client
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from test_reliability import Fixture

from file_lock import file_lock
from ipad_server import Bridge


class Boundaries(Fixture):
    def test_revocation_closes_an_already_open_bridge_stream(self):
        bridge = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
        bridge.daemon_threads = True
        bridge.authorities = {f"127.0.0.1:{bridge.server_port}"}
        bridge.upstream = self.server.server_port
        bridge.pairing = self.app.pairing
        threading.Thread(
            target=bridge.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
        token = self.app.pairing.redeem(self.app.pairing.invite(), "Synthetic device")
        connection = http.client.HTTPConnection(
            "127.0.0.1", bridge.server_port, timeout=4
        )
        try:
            connection.request(
                "GET",
                "/api/events?tab=revocation",
                headers={"Cookie": "board_ipad=" + token},
            )
            response = connection.getresponse()
            self.assertEqual(response.readline(), b": connected\n")
            response.readline()
            self.app.pairing.revoke(self.app.pairing.devices()[0]["id"])
            self.assertEqual(
                response.read(), b"", "revocation closes an idle open stream"
            )
        finally:
            connection.close()
            bridge.shutdown()
            bridge.server_close()

    def test_stream_limit_stop_refusal_and_selected_target_disconnect(self):
        for i in range(16):
            self.open_tab("tab-" + str(i))
        status, _, _ = self.http("GET", "/api/events?tab=overflow")
        self.assertEqual(status, 503)
        self.assertEqual(self.http("POST", "/api/shutdown", {})[0], 409)
        self.assertEqual(
            self.http(
                "POST",
                "/api/ai",
                {
                    "action": "target",
                    "tab": "tab-0",
                    "notebook": "default",
                    "frame": "welcome-page",
                },
            )[0],
            200,
        )
        self.close_tab("tab-0")
        response = self.app.call_tool(
            "add_shapes",
            {"operation_id": "stale-selected-target", "shapes": [{"text": "no"}]},
        )
        self.assertTrue(response["isError"])
        self.assertIn("disconnected", response["content"][0]["text"])

    def test_create_failure_leaves_recoverable_orphan_not_an_overwritable_name(self):
        import board_storage

        original = board_storage.write_json

        def fail_index(path, value):
            if Path(path).name == "notebooks.json":
                raise OSError("Synthetic index failure")
            return original(path, value)

        with (
            patch("board_storage.write_json", side_effect=fail_index),
            self.assertRaises(OSError),
        ):
            self.storage.create("Recovered original")
        before = self.storage.path("recovered-original", "board.json").read_bytes()
        self.assertIn(
            "recovered-original",
            [b["id"] for b in self.storage.recover_index()["notebooks"]],
        )
        self.storage.recover_index(False)
        second = self.storage.create("Recovered original")
        self.assertEqual(second["id"], "recovered-original-2")
        self.assertEqual(
            before, self.storage.path("recovered-original", "board.json").read_bytes()
        )

    def test_restore_keeps_retry_history_and_failure_keeps_current_bytes(self):
        self.app.ai.execute(
            "default",
            "add",
            {
                "operation_id": "before-restore",
                "frame": "welcome-page",
                "shapes": [{"text": "Original feedback"}],
            },
        )
        name = self.storage.backups("default")[0]
        before = self.storage.path("default", "board.json").read_bytes()
        with (
            patch(
                "board_storage.atomic_write",
                side_effect=OSError("Synthetic restore failure"),
            ),
            self.assertRaises(OSError),
        ):
            self.storage.restore("default", name)
        self.assertEqual(
            before, self.storage.path("default", "board.json").read_bytes()
        )
        self.storage.restore("default", name)
        receipt = self.app.ai.execute(
            "default",
            "add",
            {
                "operation_id": "before-restore",
                "frame": "welcome-page",
                "shapes": [{"text": "Original feedback"}],
            },
        )
        self.assertEqual(receipt["status"], "reverted_by_restore")


class ProcessLocks(unittest.TestCase):
    def test_competing_process_is_refused_and_exit_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            script = 'from file_lock import file_lock; import sys\nwith file_lock(sys.argv[1], blocking=False): print("acquired")\n'
            with file_lock(path):
                result = subprocess.run(
                    [sys.executable, "-c", script, str(path)],
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
            result = subprocess.run(
                [sys.executable, "-c", script, str(path)],
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
