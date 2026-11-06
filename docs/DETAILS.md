# GPT Board: details

These sections moved here from the [README](../README.md).

## Validation and limits

Verified: imports of 502 existing stroke segments; drawing, box selection, batch deletion/Undo, partial erasing, pinch bounds/reversal, exact refresh restoration; GPT add/update/delete, PNG overview export, and exact-frame region export. Server persistence and review integration use the existing backend.

This is the first independent implementation, not an exact recreation of every tldraw feature. Text layout and geometric shapes use our renderer; arrows created through GPT have fixed endpoints rather than live attachment constraints. Interactive shape rotation/resizing and rich-text editing are not included. Physical Apple Pencil/iPad behavior still needs device validation; desktop and simulated-touch checks cannot replace that.

The previous frontend source, package files, tests, build, and a snapshot of notebook JSON are retained under `code-backups/before-native-engine-*`. Installed old dependencies can remain on disk, but are not referenced by the new build. Never overwrite newer notebook data as part of a code-only rollback.

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
