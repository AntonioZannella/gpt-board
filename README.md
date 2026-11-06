# GPT Board

![gpt-board demo](docs/demo.gif)

**Status:** Personal study tool, early stage. Setup is tied to my machine; not ready for other users yet.

GPT Board is a personal handwriting workspace with saved notebooks, infinite whiteboards, and an MCP connection that lets an AI assistant read and work with the board.

Built with AI assistance; designed and maintained by the project author.

The JavaScript frontend was built with AI assistance.

## Current status

This is a working personal installation being prepared for a local-first beta. Public installation, cross-platform support, and multi-user hosting are not ready yet. The current setup includes a personal Math Learning System integration; copying this repository alone does not reproduce that setup or include its notebooks.

## Architecture

- **Browser board:** a Canvas 2D frontend handles drawing, handwriting input, page navigation and rendering locally in the browser. It saves edits through HTTP requests to the Python server.
- **Python server:** a Python 3.10+ standard-library backend serves the board, persists notebooks and whiteboards, and exposes the MCP tools. Server-sent events (SSE) deliver operations to the active board tab; the tab returns results through HTTP requests.
- **MCP client:** a connected AI client requests board images or calls drawing tools through the server. Reading and drawing require an open board tab. Review recording also depends on a separately configured Math Learning System.
- **Optional iPad bridge:** a paired Python Wi-Fi proxy connects an iPad browser to the same board server. It forwards the board routes and event stream, but does not expose the MCP endpoint.

## MCP tools

These are the tools declared in `mcp_schema.json` and handled by the Python server:

| Tool | What it does |
| --- | --- |
| `read_board` | Returns a fresh image of an exact page frame, lists frames when no frame is supplied, or reads an explicit whole-board overview. |
| `add_shapes` | Adds shapes, text, sticky notes or arrows, with optional automatic placement. |
| `update_shapes` | Moves, resizes, recolors or edits existing shapes by ID, changing only the supplied fields. |
| `delete_shapes` | Deletes shapes by ID; tool instructions restrict deletion to AI-created shapes unless the user asks otherwise. |
| `record_review` | Saves one checked problem, including correct or unresolved results, with fresh exact-frame evidence to the configured Math Learning System and error log. |

## Trade-offs

- **Standard library only:** nothing to install beyond Python to run the backend, and I wanted to learn Python itself rather than a framework. Browser drawing and input stay in JavaScript.
- **SSE over WebSockets:** the server only needs to push edits one way to the board tab; the tab sends its responses over HTTP. SSE runs over plain HTTP, the browser reconnects on its own, and Python's standard library has no WebSocket support.
- **MCP:** it is a standard protocol, so compatible AI clients, such as Codex or Claude, can read and draw on the board through the same tools without custom board code for each client. Each client still needs an MCP connection configured for this server.

## Using the existing installation

Open <http://localhost:8787> on the computer running GPT Board. On the iPad, use your existing paired board address. Existing launch commands are listed below; if the services are already running, keep using them without launching another copy.

Choose **Notebooks** for paged material or **Whiteboards** for an infinite canvas. Select a saved board from the picker, or use **New**. Write with Pencil or mouse, use fingers to pan and pinch, and wait for **Saved** before closing. Use **Select** and **Delete selected** to remove selected writing; Undo restores it.

## Data and privacy in this version

**Security:** The server is meant for localhost only; do not expose it to a network you don't trust.

Notebook files, imported page images, connection tokens, and local review evidence are stored in `data/` on the computer running the server. The browser stores view positions and collection preferences. The iPad connects to that computer through the paired bridge; this is not an iPad-only storage setup.

When a connected AI requests board content, the requested image or board data is sent to that AI service and is subject to that service's policies. Review tools can also save evidence locally and write to the configured Math Learning System. Keep MCP tokens and pairing links private.

Git ignores `data/`, `sessions/`, `code-backups/`, local agent settings, environment secrets, dependencies, and generated files. Ignoring a file does not delete or back it up, and does not prevent someone from force-adding it. Review files before publishing. Personal course content and review records must remain private.

## Implementation and existing commands

The frontend is our own Canvas 2D engine. It has no tldraw, React, Vite, or other third-party runtime dependencies. The HTTP/MCP backend, iPad Wi-Fi bridge, persistence, build command, and session loader now run on Python 3.10+ using only its standard library.

## Run and build

- `python3 build.py`: copies the frontend into `dist/` atomically.
- `python3 server.py`: serves the board and GPT MCP connection on port 8787.
- `python3 ipad_server.py`: starts the paired iPad bridge on the Mac’s Wi-Fi address, port 8788.
- `SHOW_PAIRING_LINK=1 python3 ipad_server.py`: prints the private pairing URL when starting the bridge. Keep that URL private. Existing paired devices retain their cookie and token.
- `python3 load_session.py 1`: loads an existing prebuilt session after backing up the previous default board. Close board tabs first, then reopen them. This utility does not start a tutoring-controller session.
- `python3 -m unittest discover -s tests -p 'test_*.py' -v`: Python backend tests.
- `python3 test.py`: all Python tests plus the JavaScript drawing/client contract checks (Node needed only for those checks).

### Full-suite test prerequisite

The review-log integration test needs the separate Math Learning System controller (`mls.py` and its imported code). Those files are not included in this repository. By default, the test looks for `../Math Learning System/mls.py`; a fresh clone in another directory will fail that test unless you set `MLS_TEST_CONTROLLER` to your installed controller:

```bash
MLS_TEST_CONTROLLER="$HOME/Downloads/claude code/Math Learning System/mls.py" python3 test.py
```

Adjust that path for your installation. The test creates a temporary course and error log and does not use your real course records. `MLS_TEST_PYTHON` selects the controller's Python interpreter if needed. The Python-only command above does not require this external controller; it is not a substitute for the full suite. The full suite currently runs 16 Python tests and 8 JavaScript tests.

The existing `npm start`, `npm run build`, `npm run ipad`, `npm run session -- 1`, and `npm test` commands also work; they invoke Python. The old JavaScript server/bridge/build/session entry points are small compatibility launchers, with no backend implementation remaining in JavaScript. Node is not needed to run the app with the Python commands.

## Python backend

- `server.py`: HTTP routes, server-sent events, active-tab operation routing, and MCP requests.
- `board_storage.py`: notebook and whiteboard storage, atomic file replacement, collection metadata, and handwriting activity tracking.
- `board_review.py`: exact-frame evidence archives, stable review IDs, validation, and calls to the existing canonical Math Learning System controller. It does not replace or hand-edit the learning ledger or error log.
- `mcp_schema.json`: preserved GPT tool definitions and review instructions; configured courses are inserted at startup.
- `ipad_server.py`: paired Wi-Fi proxy with the same allowed routes and authentication boundaries.
- `build.py` and `load_session.py`: build and prebuilt-board loading utilities.
- `usage.py`: the existing Python reporting utility.

The backend keeps the existing `data/` files, notebook IDs, tokens, port numbers, HTTP routes, and MCP tool schemas. `PORT`, `IPAD_HOST`, `IPAD_PORT`, `BOARD_PORT`, `MLS_CONTROLLER`, `MLS_PYTHON`, and `MLS_COURSES_JSON` remain supported. Review logging depends on an existing Math Learning System installation and initialized courses. The bundled defaults are installation-specific; configure explicit course mappings for your installation.

PDF pages already imported into notebooks keep their original embedded images and frame records. Existing external PDF-generation scripts are not rewritten by this migration. Browser rendering and input remain JavaScript because they run inside Safari; native Pencil side-tap support would still require Swift.

## Frontend

- `src/app.js`: canvas rendering, pointer input, camera, history, selection, erasing, local view persistence, autosave, and GPT operation bridge.
- `src/model.js`: notebook interchange adapter, packed stroke codec, parent transforms, bounds, hit testing helpers, and partial-stroke cutting.
- `src/style.css` and `src/index.html`: the native UI.

Existing notebook records and image assets are read directly. Rendering and editing do not load the old drawing library. Keeping the existing JSON structure preserves notebook identities, frame IDs, and the server's review evidence workflow. Existing records are preserved until edited; new strokes use the same interoperable packed-point representation.

## Interaction

Pencil/mouse writes. Finger gestures scroll and pinch; the Move page tool supports mouse panning. Select supports clicking and box selection, Shift to extend selection, and dragging selected items. Delete and Undo work on the whole selection. Eraser supports whole strokes or partial cuts. Printed page images and page frames remain protected during selection and erasing. Keyboard shortcuts: Delete/Backspace, Cmd/Ctrl+Z, Shift+Cmd/Ctrl+Z, and Cmd/Ctrl+A.

The view is saved per notebook in browser local storage. Existing saved views from the previous frontend migrate on first open. Autosave runs after edits settle; save failures retry. Wait for “Saved” before closing. The browser warns if a save is still pending. Image decoding is limited to a small cache so long notebooks do not retain all page bitmaps in memory.

## Validation and limits

Verified: imports of 502 existing stroke segments; drawing, box selection, batch deletion/Undo, partial erasing, pinch bounds/reversal, exact refresh restoration; GPT add/update/delete, PNG overview export, and exact-frame region export. Server persistence and review integration use the existing backend.

This is the first independent implementation, not an exact recreation of every tldraw feature. Text layout and geometric shapes use our renderer; arrows created through GPT have fixed endpoints rather than live attachment constraints. Interactive shape rotation/resizing and rich-text editing are not included. Physical Apple Pencil/iPad behavior still needs device validation; desktop and simulated-touch checks cannot replace that.

The previous frontend source, package files, tests, build, and a snapshot of notebook JSON are retained under `code-backups/before-native-engine-*`. Installed old dependencies can remain on disk, but are not referenced by the new build. Never overwrite newer notebook data as part of a code-only rollback.

## Whiteboards

Use the Whiteboards tab beside Notebooks for separate saved infinite canvases. New opens a name dialog; the picker switches among saved whiteboards. Both collections use the native pen, highlighter, eraser, selection/delete, and history tools. Whiteboards pan freely in all directions, zoom from 10–800%, and show a faint dot grid. Fit drawing brings the content into view; Reset view returns to the origin at 100%. Each board remembers its own view, and each collection remembers the last board opened on this device.

Collection metadata uses `kind: "whiteboard"` in `data/notebooks.json`; existing records with no kind remain notebooks. IDs are unique across both collections; snapshots continue using the existing per-ID storage and GPT protocol. `GET /api/notebooks` returns notebooks; `?kind=whiteboard` returns whiteboards. Creating a whiteboard sends `kind: "whiteboard"` to the existing create endpoint. The URL includes `section=whiteboards`. No existing notebook data is migrated or relabeled.

Verified collection separation, colliding names, independent saves, drawing/panning, board switching, and pixel-identical canvas restoration after refresh. Actual iPad gestures still require device validation.

## Python migration validation and rollback

Eight Python tests cover concurrent collection creation, atomic-save failure recovery, legacy-compatible review identity, session backup/validation, frontend builds, iPad large saves and live operation acknowledgements, disconnected tabs, and route restrictions. Six existing JavaScript drawing/client tests run against the Python services, including isolated real-controller review logging and the iPad frontend-asset regression. All test data and synthetic reviews use temporary directories.

Before installing, browser checks verified drawing, Python autosave, reload persistence, and an existing PDF notebook using copied data. The previous backend and command files are retained under `code-backups/before-python-backend-*`. For a rollback, stop the Python services, restore the backed-up code files and run the old Node server and iPad bridge. Never restore older notebook data over new writing as part of a code rollback.

## Smooth ink update

Live strokes use a transparent canvas above the static page. Raw/coalesced samples remain in memory until release, then are packed once; the page is not repainted on every pen move. Cached, pressure-filtered curved outlines replace overlapping straight line segments. Stroke history stores an added stroke instead of cloning every page at pen-down. Empty coalesced batches, short down/up dashes, capture loss, cancellation, and a missed initial down with confirmed pen contact are handled; hover never starts ink.

Autosave waits for an idle moment and sends shape changes. Python validates and merges those changes under the storage lock, preserving schema, PDF assets, and untouched records before the existing atomic write. Existing full-snapshot clients remain compatible. Browser input/rendering stay JavaScript for low latency; merge, persistence, services, build, and the audit launcher are Python.

`python3 test.py` runs nine Python checks and eight JavaScript checks. `python3 tests/browser_ink_audit.py` starts an isolated, disposable browser fixture on localhost:8796. Open its printed URL and press **Run ink regression checks**. The browser audit covers 108 rapid/interrupted strokes, endpoints, hover/palm exclusion, undo/redo, select/delete/undo, save/read-back, zero static-layer repaints during a long stroke, and exclusion of a 3 MB asset from save payloads. It never edits real notebooks. Physical Pencil timing still needs confirmation on the iPad.

For this update, code backups are under `code-backups/before-smooth-ink-*`. Restore code only, rebuild with Python, and restart the server if rolling back; never replace current notebook data with an older copy.

## Single-page notebooks and Safari input repair

Notebooks show one sheet at a time. Swipe left for the next page and right for
the previous page; the page picker, arrow buttons, and Page Up/Page Down keys
also work. Fit page shows the whole sheet. Pinch to zoom and drag to move within
the sheet; when zoomed, a horizontal swipe at the corresponding page edge turns
the page. Whiteboards remain infinite canvases. Existing page IDs, positions in
storage, PDF images, handwriting, and exact-frame AI exports are preserved.

The app saves the current page, position, and zoom immediately as the camera
changes. Startup reads that saved view before fitting the canvas. Zero-size
background layouts are ignored, and legacy views can restore even if their old
frame ID is unavailable. Views remain specific to the browser and site address.

Safari cannot select toolbar/status text while writing. Canvas touch handlers
prevent default browser gestures, and explicitly identified stylus Touch Events
provide a fallback when the pointer stream is absent. Mixed touch/pointer
streams are deduplicated, fingers still pan/pinch, and pen pressure can recover
a missing pointer-down without relying on the buttons bit. Browser-local input
and rendering remain JavaScript to avoid network latency.

For disposable browser checks, run `python3 tests/browser_ink_audit.py` for
whiteboards, or `python3 tests/browser_ink_audit.py --port 8797 --notebook` for
notebook input, page swipes, legacy view migration, hidden/resize layout, and
reload restoration. Use the printed URL. These tests do not replace physical
iPad/Pencil validation.

## Page reload fallback and swipe motion

The notebook URL now includes the current frame ID in a `page` parameter.
Reload restores that page even if browser storage is unavailable or contains an
older page. Zoom and pan still use browser storage. The address changes only
when the selected page changes, avoiding per-frame history writes on Safari.
An invalid or removed page ID falls back to the saved view or first page.

A horizontal page swipe follows the finger and previews the adjacent sheet.
Release slides to the destination; short or canceled swipes return to the current
page, and the first/last page provides restrained edge resistance. Page buttons
and the page picker animate too. Pinch, resize, or a new Pencil contact cancels
the visual transition immediately. Reduced-motion preferences disable settling
animation. These temporary canvas layers never alter notebook content or frame
coordinates and never receive pointer events.

## Resume the last notebook

Opening the main board address resumes the last successfully opened notebook
or whiteboard. A small first-party cookie backs up the selected board ID and
collection if browser local storage is unavailable. The cookie is separated by
port for local installations and contains no notebook content or connection
tokens. Changing tools or drawing does not repeatedly write this preference.

Specific links containing `?notebook=...` still open the named notebook. Use the
main address without a notebook query for a bookmark that should resume your
last board. Empty notebooks also keep their ID in the address when reloaded.

`python3 tests/browser_resume_audit.py` provides disposable multi-notebook checks
for switching, reload, reopening the main address, changing collections, and
browser-storage failure.

## iPad Home Screen launches

A fresh launch in standalone/Home Screen mode resumes the last successfully
opened notebook or whiteboard, even if the icon was originally saved with a
specific notebook URL. The last page ID is also included in the small session
cookie. Deliberate in-app notebook/collection choices carry a one-shot `open=1`
flag, removed after loading, so resuming cannot override those choices. Normal
browser links, refreshes, and history navigation retain their explicit target.
The resume browser fixture simulates standalone launches with an older icon
address, including disabled local storage; physical iPad testing remains needed.
