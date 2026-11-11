# Changelog

## Follow and drawing responsiveness — November 11, 2026

- Follow the iPad's whiteboard viewport, including panning and zooming, with bounded presence updates and screen-size adaptation.
- Preserve active Pencil strokes during AI reads; ask for a fresh read if the board changes during capture.
- Yield between screenshot-rendering batches and encode PNGs asynchronously to reduce input blocking. Physical iPad freeze resolution remains to be verified.
- Expand the README to cover drawing controls, library/page management, native file sharing, recovery and AI review workflows.

## Unreleased — November 11, 2026

- Add the optional Swift iPad wrapper with Apple Pencil double-tap, native file sharing, app icon, and connection recovery.
- Add Mac Follow iPad mode, live stroke previews, faster page-image reads, and direct current-iPad MCP reads.
- Improve trackpad paging and Pencil drawing; replace the native color popup with a Pencil-aware ink palette that closes on an outside tap.
- Support PDF page links, mapped background replacement, selected-page raster export, and notebook duplication. PDF import/export supports up to 500 pages within documented size limits.
- Harden image retries, revision-cache invalidation, compression negotiation, and long-export bridge timeouts.
- Expand regression coverage and separate private signing settings from the shared Xcode project.

These changes are source updates, not a new tagged release or App Store publication. See [workflow details](docs/WORKFLOW_UPDATE.md) and [verification](docs/RELEASE_TESTS.md).

## 1.3.0rc1 development baseline — November 9, 2026

Historical local validation record; the changes below are now included in the source tree. See READINESS_CHECKLIST.md for current evidence and remaining acceptance criteria; the section below describes the earlier baseline only.

- Replace the shared delayed outbox with per-document atomic browser journals, ownership probes, exact-version cleanup, storage-failure status and exportable conflicting local versions.
- Commit AI shape changes and stable request-hashed receipts together in Python. Add explicit notebook/page targets, full non-AI provenance protection, scoped UI approval, collision-aware placement and undo that preserves later work. Remove acknowledgement-driven mutation rollback.
- Validate record semantics, parent graphs, packed/plain strokes and bounded PNG streams; preserve missing/corrupt metadata, detect orphaned snapshots and provide explicit backed-up index recovery.
- Add OS-owned cross-process locks, safe stop, orphan bridge shutdown, open-stream revocation, copy/QR invitations and connection/decoder/queue/evidence boundaries.
- Add supported PDF/PNG/JPEG import staging with progress/cancel, JSON import preview, printable raster PDF/PNG export, and annotation visibility selection.
- Add page duplicate/rename/reorder/trash/restore and notebook search/rename/archive/trash/restore/previews. Extract recovery/pages/dialog modules and add readable typed-note/review interfaces.
- Add self-contained correction-chain tests, optional real-controller correction/resolution checks, automated Chromium/WebKit workflows and CI artifacts.
- Add privacy/history audit, synthetic benchmarks, contributor templates, human/device/evaluation protocols, original sample and a source-only local release packager with manifest verification.

Guarantees remain bounded: no full offline startup, no universal exactly-once claim, no untrusted-network/TLS hosting, no physical Pencil/Windows certification, no measured learning gains, no human authorship fabrication. Library dependencies and platform exceptions are explained in DETAILS.md.

## Historical uncommitted 1.2.0 baseline (before this candidate)

Prepared November 9, 2026. These changes are local and uncommitted; no release artifact, public deployment or successful hosted CI run is implied.

### Reliability and access

- Initialize a real Welcome snapshot on clean installations and allow first notebook creation.
- Reject foreign Origins and non-JSON POSTs on the main server; remove unauthenticated direct `--lan` startup.
- Add short-lived one-use pairing codes, individual device sessions/revocation and legacy token migration.
- Require save revisions; reconcile other tabs with a three-way shape merge, preserving the already-saved server copy on same-shape conflicts.
- Retain pending shape changes in browser storage when available and replay them after loading the saved notebook.
- Preserve notebook identity after the last-used AI tab disconnects; allow an explicit notebook and report successful targets. A follow-up regression corrected the remaining single-other-notebook fallback.
- Recover the AI operation queue after failures, retry acknowledgements and attempt rollback after late acknowledgements. Durable operation receipts remain deferred.
- Enforce handwriting authorization and protect page frames/images in AI edits. Separate save, connection and recent AI activity status.
- Add rolling backups and explicit restore. Preserve the selected backup's bytes before retention pruning during restore; test restoring the oldest retained backup.
- Report damaged notebook/index JSON without overwriting the malformed file; add structural shape/parent checks and archived review-image hash verification.

### Setup and portability

- Add `board.py` startup, status/doctor, pairing, device and notebook maintenance commands.
- Verify startup health before announcing success, clean up children during failed/interrupted startup, retry an unavailable bridge, probe bridge liveness in diagnostics, and rebuild a missing/stale served `dist/index.html`. Twelve launcher regressions and a real disposable startup/SIGTERM check cover these fixes.
- Configure review integration through private `data/config.json` or `MLS_*` values. Without configuration, drawing works and the review tool is absent.
- Add versioned notebook JSON export/import with preview and unique notebook IDs, blank-page creation, PNG download and stable frame lookup.
- Format frontend source and extract pure synchronization/permission/status decisions into `src/sync.js`.
- Update README, architecture/operation details, recovery/security documentation and the exact-numbered 60-item readiness mapping.
- Remove the obsolete `gpt-board-clean-test` local launcher entry; preserve other launch configurations.

### Verification

- Local isolated run: 56 Python tests and 16 JavaScript tests passed with the real external controller operating only on temporary fixtures; zero skips. Public mode skips the optional controller test explicitly.
- Ruff 0.17.4 passed after focused lint cleanup.
- User-authorized local activation completed: the current frontend was rebuilt, both services started, the Mac board loaded with Saved / Connected, and the existing pairing credential worked. Original notebook, token, configuration and evidence files matched the pre-start backup; physical iPad verification remains pending.
- CI now configures Ubuntu/macOS with Python 3.11/3.12 and Node 24, read-only repository permission and a ten-minute job timeout. Hosted results are pending.
- A new disposable browser smoke check verified notebook/page creation, drawing, save and reload; the live personal notebooks were not used as test fixtures.
- Prior implementation handoff reports an isolated browser exercise on port 8791 covering save/reload, device merge, offline replay, AI edits and pairing; that is separate from physical-device evidence.

### Upgrade and remaining limits

Wait for Saved everywhere, stop the old server and bridge, run `python3 board.py start`, then reload all tabs. Preserve private data/configuration and existing learning records. See [Recovery](docs/RECOVERY.md) for backups and code-only rollback.

Physical iPad/Apple Pencil, `.local` and Windows checks are still required. TLS, QR pairing, public PDF/image import, PDF export and notebook rename/trash remain deferred. Same-shape conflicts keep the server copy; offline queues and AI rollback are not crash-proof. See all remaining acceptance criteria in the [readiness checklist](docs/READINESS_CHECKLIST.md).
