# Current source verification — November 11, 2026

The updated source passes 114 Python tests and 67 JavaScript tests on macOS; one optional external-controller integration is skipped. Hosted CI status is shown on the repository Actions page. The native wrapper previously built and ran on a physical iPad; the latest color-picker Pencil behavior still needs physical verification. This is not Windows or universal-device certification.

## Historical candidate record

# Local candidate verification

Version: 1.3.0rc1, uncommitted. Platform: macOS 26.5.1 arm64, Python 3.12.0. All writes used isolated synthetic notebooks/courses. Existing personal services were not restarted, and personal learning records were not used as test fixtures.

The initial uncommitted 1.2.0 baseline independently passed 56 Python and 16 JavaScript tests. During implementation the expanded backend suite passed 75 Python tests and 19 JavaScript tests, followed by two additional annotation-layout regressions. The source browser workflow passed on Chromium 158.0.8127.18 and WebKit 27.2 through Playwright 1.65.0. The final extracted-package results are recorded below after execution.

## Reproduce from the extracted package

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/ruff check --isolated .
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python test.py
.venv/bin/python -m playwright install chromium webkit
.venv/bin/python tests/browser_smoke.py --engine chromium
.venv/bin/python tests/browser_smoke.py --engine webkit
```

Node is required for the JavaScript tests, not normal application use. Without `MLS_TEST_CONTROLLER`, the one optional external-controller integration reports a skip. To enable it, set that variable to the existing canonical `mls.py` file; tests create temporary initialized courses and separate fixture logs. Never supply real course directories to tests.

## Coverage and practical boundaries

- Startup from source with no data; original starter write/reload; competing process locks and safe stop; missing/corrupt index and damaged snapshots; interrupted saves/restores and retained backups.
- Origin/LAN rejection, pairing migration/revocation including an already-open stream, body/decode/connection limits and timed media workers.
- Per-document journals, active-tab ownership, reload/reconnect, denied/quota browser storage and newer-server conflicts with separate recovery-copy export.
- Stable AI IDs/receipts, response loss/restart, target/page selection, provenance and real UI approval, stale proposals, undo against newer work, restore tombstones and text layout/export bounds.
- Page duplicate/reorder/trash/restore; notebook portability; JSON/PNG/PDF/image workflows; cancel/timeout/atomic PDF import and import commit retry.
- Evidence hash validation, correction/resolution links and preserved assistance/history in both the self-contained fixture and optional real canonical controller.

Browser artifacts include screenshots, trace, synthetic video, downloaded PNG/PDF and server logs. They are development evidence, not part of the public source package. Only the reviewed synthetic demo is packaged. CI always uploads artifacts; no hosted CI result is claimed.

Read-only validation of 56 existing snapshot files found no semantic compatibility failures; contents were not copied into the distribution. Synthetic PNG/PDF exports were rendered and visually inspected. Physical paper output, real iPad/Pencil/VoiceOver, long real study sessions and Windows installation remain unverified.

## Final extracted-package run

Observed results on November 9, 2026:

| Check | Result |
| --- | --- |
| Source ZIP extraction and every file digest | Passed; no personal data directory in the package. Stored ZIP permissions restored, as by normal `unzip`. |
| Fresh task-local virtual environment and dependency install | Passed; `pip check`: no broken requirements. |
| Full suite with the optional real controller | **75 Python + 21 JavaScript passed, zero skipped.** Temporary courses/logs only. |
| Chromium package smoke | All workflows passed. |
| WebKit package smoke | All workflows passed. |
| Ruff from the extracted package directory | All checks passed. |
| Export inspection | Both four-page PDFs rendered; 612 × 791.9 pt notebook pages and a 612 × 612 pt imported image page. PNG/PDF note wrapping and original sample inspected. |
| Synthetic sustained/reconnect run | 600 strokes plus ten blocked-save strokes retained; final count 10,640, reconnect-to-Saved 1795.5 ms in one run. |
| Original evaluation assets | Seven generated PNG cards opened/hashed; independent quality labels and model execution still pending. |

The final ZIP includes the finalized documentation, refreshed synthetic demo and separately exercised evaluation-card generator. Its application, dependency and test files are byte-identical to the extracted package that passed the suite and both browser runs; the final manifest is verified again. The final archive digest is supplied beside the downloadable ZIP. Full reruns were not used to manufacture a hosted CI or physical-device claim.

Use a normal archive extractor such as `unzip` to preserve executable file attributes. All documented application commands invoke Python explicitly. Run Ruff from the extracted project directory so its first-party import classification matches the documented workflow.
