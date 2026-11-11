"""Test-only protocol oracle. NEVER a product learning ledger or MLS substitute.

Only a temporary course directory containing the TEST_FIXTURE sentinel is
accepted. This fixture tests argument transport and immutable correction links.
The optional real-controller integration separately checks canonical behavior.
"""

import json
import sys
from pathlib import Path

course = Path(sys.argv[2])
if not (course / "TEST_FIXTURE").is_file():
    sys.exit("Fixture refuses a non-test course")
payload = json.load(sys.stdin)
path = course / "fixture-reviews.json"
rows = json.loads(path.read_text()) if path.exists() else []
by_id = {r["review_id"]: r for r in rows}
key = payload["review_id"]
if key in by_id:
    if by_id[key] != payload:
        sys.exit("Changed retry requires a new review with supersedes")
    status = "already_saved"
else:
    superseded = {r["supersedes"]["review_id"] for r in rows if r.get("supersedes")}
    if payload.get("supersedes"):
        target = payload["supersedes"]["review_id"]
        if target not in by_id or target in superseded:
            sys.exit("Correction target missing or superseded")
    for link in payload.get("resolves", []):
        target = by_id.get(link["review_id"])
        if (
            not target
            or link["review_id"] in superseded
            or link["error_id"] not in {e["error_id"] for e in target["errors"]}
        ):
            sys.exit("Resolution target missing or superseded")
    rows.append(payload)
    path.write_text(json.dumps(rows))
    status = "saved"
print(
    json.dumps(
        {
            "status": status,
            "review_id": key,
            "result": payload["result"],
            "error_count": len(payload["errors"]),
        }
    )
)
