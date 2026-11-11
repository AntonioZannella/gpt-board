# Mac-backed workflow update — November 11, 2026

The iPad remains a client of the Mac server. No offline startup or separate iPad notebook database was introduced.

## User-facing controls

- **More → Files:** PDF/image upload (up to 16 MiB), new notebook or append, and explicit background-replacement mapping. PDFs can have up to 500 pages, subject to the 32 MiB rendered staging and 48 MiB notebook limits. Replacement preserves frame IDs and handwriting, refuses substantially different page proportions, and remaps internal PDF links to the chosen existing frames.
- **More → Files → PDF:** all pages or ranges such as `1-5, 8, 12-14`, with an AI annotation checkbox. Each rendered page is uploaded separately. The server assembles up to 500 pages with bounded temporary storage; export refuses a notebook revision change rather than silently mixing versions. PDF export is rasterized and includes handwriting; it is not an editable PDF ink layer.
- **More → Files → notebook JSON:** a portable editable backup, restored with preview under a new notebook ID. The browser accepts JSON files up to 64 MiB; the decoded notebook must fit the 48 MiB board limit.
- **More → Library:** search, rename, duplicate, reorder, archive, reversible Trash and Restore. **Manage pages** provides page reorder, duplication and reversible Trash.
- **More → Recovery / Help:** preserved unsent journals/conflicts and a recovered local-copy export. Existing IndexedDB retention, fallback journal, conflict merge and canonical math review rules remain in place.
- **More → AI:** select the target, inspect proposed changes, approve them, and undo an AI operation. AI ink is marked separately and obeys Show AI notes. Tools now accept real draw/highlight segments, with pressure points and optional `preview:true`. This is editable stroke support, not a handwriting-font generator or a promise of exact personal handwriting imitation.

## Sync and app behavior

- Throttled live ink previews travel separately from completed-stroke saves. They are never written to the notebook, are dropped under queue pressure, disappear when the durable shape arrives, and expire after six seconds if abandoned. Recovery and review reads continue using the authoritative saved data.
- Existing lazy page images, adjacent-page preloading, fast completed-stroke save, stationary-pointer swipe handling and iPad-follow behavior are retained.
- The toolbar-free native app supports JavaScript rename/confirmation dialogs and PDF/image/JSON document opening from Files/share actions (native handoff limit 16 MiB; larger JSON backups use the web Files picker).
- Failed initial loads retry while the app is active. An already-loaded board is never automatically reloaded by this retry path, preserving unsent writing. The failure-only connection prompt discovers the Mac using Bonjour where the Wi-Fi permits multicast. Discovery does not bypass per-device pairing. Numeric address entry remains available for networks blocking discovery.
- `python3 scripts/update_ipad.py` lists devices; rerun with `--device IDENTIFIER` to build, replace the installed bundle and launch. This does not delete app data or bypass Apple's Personal Team signing expiration.

## Verification

- 103 Python regression tests passed; 59 JavaScript tests passed and one optional external MLS integration test was skipped.
- Added targeted tests for page/ink preservation, stale replacement rejection, exports above 50 pages, incomplete-export rejection, AI pen preview/approval/undo, live-preview isolation and page ranges.
- In a disposable browser notebook: imported a PDF as a new notebook, drew a stroke, exported a PDF, and rendered the downloaded result to verify both background and stroke.
- Signed iPad build succeeded and installed over the existing app; device presence returned to the existing notebook/page. Mac showed Saved and Connected after deployment. Bonjour advertisement was observed.
- Physical Apple Pencil, live ink latency, native Files/share interaction and Bonjour across the user's Wi-Fi require user acceptance; build and browser tests cannot substitute for those hardware checks.

## Preservation

No personal notebook was replaced, deleted, imported, or rewritten by these tests. The prior served frontend was copied into the Codex workflow-audit work folder before deployment. Existing uncommitted repository work was retained. Canonical Math Learning System ledgers were not edited.
