"""Self-contained adapter contract; the fixture cannot touch real course records."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_reliability import PNG

from board_review import ReviewService
from board_storage import BoardStorage, ClientError, write_json


class CorrectionChains(unittest.TestCase):
    def test_fresh_corrections_retry_resolution_and_original_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = root / "course"
            course.mkdir()
            (course / "TEST_FIXTURE").touch()
            storage = BoardStorage(root)
            write_json(
                root / "data/config.json",
                {
                    "review": {
                        "controller": str(
                            Path(__file__).parent / "fixtures/review_controller.py"
                        ),
                        "python": sys.executable,
                        "courses": {"Synthetic Course": str(course)},
                    }
                },
            )
            review = ReviewService(storage)

            def args(**changes):
                check = review.archive(
                    "Original page", PNG, "fixture", frame_id="stable-page"
                )
                return {
                    "check_id": check["check_id"],
                    "course": "Synthetic Course",
                    "section": "1",
                    "page": "1",
                    "problem": "1",
                    "original_response": "2 + 2 = 5",
                    "result": "wrong",
                    "read_confidence": "high",
                    "assistance": "unknown",
                    "errors": [
                        {
                            "error_id": "e1",
                            "type": "ALGEBRA",
                            "skill": "addition",
                            "first_wrong_step": "2 + 2 = 5",
                            "original_work": "2 + 2 = 5",
                            "cause": "unknown",
                            "correction": "2 + 2 = 4",
                            "reasoning": "Two pairs make four.",
                            "follow_up": "Try 3 + 3 without help.",
                            "confidence": "high",
                        }
                    ],
                    **changes,
                }

            wrong = args()
            first = review.record(wrong)
            self.assertEqual(review.record(wrong)["status"], "already_saved")
            with self.assertRaises(ClientError):
                review.record({**wrong, "original_response": "Changed"})
            retry = args(
                original_response="3 + 3 = 6",
                result="right",
                errors=[],
                assistance="helped",
                resolves=[
                    {
                        "review_id": first["review_id"],
                        "error_id": "e1",
                        "reason": "Fresh variation checked",
                    }
                ],
            )
            second = review.record(retry)
            correction = args(
                original_response="Actually unreadable",
                result="unresolved",
                read_confidence="low",
                errors=[],
                supersedes={
                    "review_id": second["review_id"],
                    "reason": "Recognition correction",
                },
            )
            third = review.record(correction)
            rows = json.loads((course / "fixture-reviews.json").read_text())
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["original_response"], "2 + 2 = 5")
            self.assertEqual(rows[1]["assistance"], "helped")
            self.assertEqual(rows[2]["supersedes"]["review_id"], second["review_id"])
            self.assertNotEqual(third["review_id"], second["review_id"])
            with self.assertRaises(ClientError):
                review.record(args(result="wrong", resolves=retry["resolves"]))
            with self.assertRaises(ClientError):
                review.record(
                    args(
                        result="right",
                        errors=[],
                        resolves=[
                            {"review_id": "missing", "error_id": "e1", "reason": "bad"}
                        ],
                    )
                )
            with self.assertRaises(ClientError):
                review.record(
                    {
                        **correction,
                        "supersedes": {
                            "review_id": third["review_id"],
                            "reason": "self",
                        },
                    }
                )
