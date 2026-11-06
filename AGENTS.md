# GPT Board development rules

## Agreed release order

Follow the user's November 3, 2026 plan: (0) repository cleanup, (1) security,
(2) portability, (3) installation/onboarding, (4) separate engine and content,
(5) usage metrics and privacy, (6) polish and launch. Handwriting benchmarks
are Phase 7, after users. This roadmap is not permission to implement every
phase without a task request. Time estimates are provisional.

## Scope and implementation

- Freeze the current architecture. Make focused fixes; do not rewrite whole systems.
- Prefer straightforward, readable Python wherever practical. Keep browser-local
  drawing and pointer input in JavaScript to preserve responsiveness.
- Use established libraries when they reduce complexity or security risk.
- Help Antonio understand a changed Python file. Preferred learning order:
  build.py, load_session.py, usage.py, board_storage.py, server.py,
  board_review.py, ipad_server.py. usage.py currently reports Codex session
  usage; it is not product MAU analytics.
- Keep changes small and reviewable. Antonio wants to write his own commit
  messages; do not create commits with invented user-authored messages.

## Preserve the working personal installation

- Preserve notebooks, IDs, tokens, pairing, review evidence, and course data.
- Do not restore an older data backup as part of a code rollback.
- Keep data/, sessions/, code-backups/, and local secrets out of public Git history.
- Retain compatibility launchers until their callers are checked.
- Never claim Windows support from Mac-only tests. Run cross-platform tests
  and test on a real Windows machine before launch.

## Math reviews

Follow the applicable shared GPT Board review protocol and user instructions.
Antonio's installation must continue using the existing Math Learning System
and canonical Math Error Log, with fresh read_board evidence and confirmed
record_review saves. Do not create a separate log for Antonio. A future
standalone logging option is for users without that integration.
