"""Real failure boundaries, with private data replaced by temporary fixtures."""

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_reliability import PNG, shape

from ai_operations import AIOperations
from board_storage import (
    BoardStorage,
    ClientError,
    DamagedFileError,
    write_json,
)
from media import Media


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = BoardStorage(self.root)
        self.storage.metadata()
        self.ai = AIOperations(self.storage)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, op="safe-retry-001", **kwargs):
        return self.ai.execute(
            "default",
            "add",
            {
                "operation_id": op,
                "frame": "welcome-page",
                "shapes": [{"type": "text", "text": "Synthetic feedback", **kwargs}],
            },
        )

    def test_lost_response_restart_and_undo_never_reapply(self):
        result = self.add()
        original = self.storage.path("default", "board.json").read_bytes()
        fresh = AIOperations(BoardStorage(self.root))
        retry = fresh.execute(
            "default",
            "add",
            {
                "operation_id": "safe-retry-001",
                "frame": "welcome-page",
                "shapes": [{"type": "text", "text": "Synthetic feedback"}],
            },
        )
        self.assertEqual(result, retry)
        self.assertEqual(
            original, self.storage.path("default", "board.json").read_bytes()
        )
        fresh.undo("default", "safe-retry-001")
        self.assertEqual(
            self.add()["status"],
            "undone",
            "retry after undo is still the same operation",
        )
        with self.assertRaises(ClientError):
            self.add(text="Different content")

    def test_failure_before_commit_and_projection_failure_after_commit(self):
        before = self.storage.path("default", "board.json").read_bytes()
        with (
            patch(
                "board_storage.atomic_write", side_effect=OSError("Synthetic failure")
            ),
            self.assertRaises(OSError),
        ):
            self.add()
        self.assertEqual(
            before, self.storage.path("default", "board.json").read_bytes()
        )
        import board_storage

        real_write = board_storage.atomic_write

        def fail_summary(path, content):
            if Path(path).name == "summary.json":
                raise OSError("Synthetic projection failure")
            return real_write(path, content)

        with patch("board_storage.atomic_write", side_effect=fail_summary):
            result = self.add()
        self.assertEqual(result, self.add(), "receipt and shapes survived together")

    def test_all_student_shapes_require_real_approval_and_stale_proposals_fail(self):
        note = shape("student-note", "text", parentId="shape:welcome-page")
        self.storage.save_changes(
            "default",
            {"put": [note], "remove": []},
            {},
            self.storage.revision("default"),
        )
        args = {
            "operation_id": "approve-student",
            "frame": "welcome-page",
            "ids": ["student-note"],
            "user_authorized": True,
        }
        result = self.ai.execute("default", "delete", args)
        self.assertEqual(result["status"], "approval_required")
        self.assertIn("shape:student-note", self.storage.read("default")["store"])
        self.ai.approve("default", "approve-student")
        self.assertNotIn("shape:student-note", self.storage.read("default")["store"])
        self.ai.undo("default", "approve-student")
        self.ai.execute("default", "delete", {**args, "operation_id": "stale-proposal"})
        self.add("changed-after-proposal")
        with self.assertRaisesRegex(ClientError, "changed"):
            self.ai.approve("default", "stale-proposal")

    def test_page_target_bounds_and_undo_preserve_newer_work(self):
        with self.assertRaisesRegex(ClientError, "beyond"):
            self.add(x=900, y=900, w=500)
        with self.assertRaisesRegex(ClientError, "missing"):
            self.ai.execute(
                "default",
                "add",
                {
                    "operation_id": "wrong-page-1",
                    "frame": "gone",
                    "shapes": [{"text": "x"}],
                },
            )
        result = self.add()
        item = self.storage.read("default")["store"]["shape:" + result["done"][0]]
        self.assertEqual(item["parentId"], "shape:welcome-page")
        item["x"] += 1
        self.storage.save_changes(
            "default",
            {"put": [item], "remove": []},
            {},
            self.storage.revision("default"),
        )
        with self.assertRaisesRegex(ClientError, "changed"):
            self.ai.undo("default", "safe-retry-001")

    def test_missing_index_orphan_recovery_is_explicit_and_keeps_original(self):
        self.storage.create("Synthetic second notebook")
        (self.root / "data/notebooks.json").unlink()
        with self.assertRaises(DamagedFileError):
            self.storage.metadata()
        preview = self.storage.recover_index()
        self.assertEqual(len(preview["notebooks"]), 2)
        self.assertFalse((self.root / "data/notebooks.json").exists())
        self.storage.recover_index(False)
        (self.root / "data/notebooks.json").write_text("{broken")
        self.storage.recover_index(False)
        copies = list((self.root / "data/backups").glob("index-before-recovery-*"))
        self.assertEqual(copies[0].read_text(), "{broken")

    def test_semantic_damage_and_dangling_parents_preserve_originals(self):
        before = self.storage.path("default", "board.json").read_bytes()
        for bad in (
            shape("bad", "unknown"),
            shape("bad", "text", props={"w": -1}),
            shape("bad", "draw", props={"segments": [{"path": "YQ=="}]}),
            shape(
                "bad",
                "draw",
                props={"segments": [{"points": [{"x": float("nan"), "y": 0}]}]},
            ),
        ):
            with self.assertRaises(ClientError):
                self.storage.save_changes(
                    "default",
                    {"put": [bad], "remove": []},
                    {},
                    self.storage.revision("default"),
                )
            self.assertEqual(
                before, self.storage.path("default", "board.json").read_bytes()
            )
        with self.assertRaises(ClientError):
            self.storage.save_changes(
                "default",
                {"put": [], "remove": ["shape:welcome-page"]},
                {},
                self.storage.revision("default"),
            )
        write_json(
            self.storage.path("default", "board.json"), {"store": [], "schema": {}}
        )
        with self.assertRaises(DamagedFileError):
            self.storage.read("default")

    def test_notebook_trash_restore_keeps_content_and_id(self):
        before = self.storage.path("default", "board.json").read_bytes()
        for action in ("archive", "restore", "trash", "restore"):
            self.assertEqual(self.storage.manage("default", action)["id"], "default")
            self.assertEqual(
                before, self.storage.path("default", "board.json").read_bytes()
            )
        self.assertEqual(
            self.storage.manage("default", "rename", "Renamed")["name"], "Renamed"
        )

    def test_import_cancel_roundtrip_and_pdf_output(self):
        media = Media(self.storage)
        job = media.prepare("original.png", PNG.split(",")[1])
        media.render(job["id"], 0)
        rev = self.storage.revision("default")
        result = media.commit(job["id"], "default", rev)
        self.assertEqual(result, media.commit(job["id"], "default", rev))
        self.assertEqual(len(result["pages"]), 1)
        pdf = media.export_pdf({"pages": [{"png": PNG, "w": 1000, "h": 1294}]})
        self.assertTrue(pdf.startswith(b"%PDF"))
        new_job = media.prepare("original.pdf", base64.b64encode(pdf).decode())
        self.assertEqual(new_job["pages"], 1)
        media.render(new_job["id"], 0)
        media.cancel(new_job["id"])
        before = self.storage.path("default", "board.json").read_bytes()
        with self.assertRaises(ClientError):
            media.commit(new_job["id"], "default", self.storage.revision("default"))
        self.assertEqual(
            before, self.storage.path("default", "board.json").read_bytes()
        )

    def test_media_worker_timeout_and_invalid_file(self):
        media = Media(self.storage)
        with self.assertRaises(ClientError):
            media.prepare("bad.pdf", base64.b64encode(b"not pdf").decode())
        with (
            patch(
                "media.subprocess.run",
                side_effect=subprocess.TimeoutExpired("worker", 30),
            ),
            self.assertRaisesRegex(ClientError, "timed out"),
        ):
            media.prepare("photo.png", PNG.split(",")[1])
        self.assertEqual(list(media.folder.iterdir()), [])

    def test_corrupt_pairing_is_not_silently_replaced(self):
        from pairing import Pairing

        path = self.root / "data/ipad-devices.json"
        path.write_text("{broken")
        with self.assertRaises(DamagedFileError):
            Pairing(path.parent).invite()
        self.assertEqual(path.read_text(), "{broken")

    def test_evidence_limit_preserves_old_images_and_history_reads_are_read_only(self):
        from board_review import ReviewService

        course = self.root / "course"
        course.mkdir()
        write_json(course / "course.json", {"course": "Synthetic"})
        row = {
            "kind": "board_review",
            "review_id": "br-test",
            "result": "unresolved",
            "assistance": "unknown",
            "evidence": {"image": "private-path"},
        }
        ledger = course / "attempts.jsonl"
        ledger.write_text(json.dumps(row) + "\n")
        write_json(
            self.root / "data/config.json",
            {
                "review": {
                    "controller": "not-run",
                    "courses": {"Synthetic": str(course)},
                    "max_evidence_bytes": 100,
                }
            },
        )
        service = ReviewService(self.storage)
        service.archive("Synthetic page", PNG, "default")
        before = {
            p: p.read_bytes()
            for p in (self.root / "data/checks").rglob("*")
            if p.is_file()
        }
        with self.assertRaisesRegex(ClientError, "limit"):
            service.archive("Second read", PNG, "default")
        self.assertEqual(
            before,
            {
                p: p.read_bytes()
                for p in (self.root / "data/checks").rglob("*")
                if p.is_file()
            },
        )
        original = ledger.read_bytes()
        history = service.history("Synthetic")
        self.assertEqual(history["total"], 1)
        self.assertNotIn("evidence", history["reviews"][0])
        self.assertEqual(original, ledger.read_bytes())
        self.assertFalse((course / "state.json").exists())

    def test_invalid_richtext_scale_and_receipts_are_semantic_damage(self):
        for record in (
            shape("bad", "text", props={"richText": {"content": 5}}),
            shape("bad", "draw", props={"scaleX": "oops"}),
            shape("bad", "text", meta=[]),
        ):
            with self.assertRaises(ClientError):
                self.storage.save_changes(
                    "default",
                    {"put": [record], "remove": []},
                    {},
                    self.storage.revision("default"),
                )
        value = self.storage.read("default")
        value["_operations"] = []
        write_json(self.storage.path("default", "board.json"), value)
        with self.assertRaises(DamagedFileError):
            self.storage.read("default")

    def test_export_page_count_and_import_bounds(self):
        media = Media(self.storage)
        with self.assertRaises(ClientError):
            media.export_pdf({"pages": [{"png": PNG, "w": 1000, "h": 1294}] * 501})
        with self.assertRaises(ClientError):
            media.prepare("unsupported.svg", base64.b64encode(b"<svg/>").decode())
