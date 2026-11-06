# GPT Board

GPT Board saves handwriting in paged notebooks and infinite whiteboards, with Model Context Protocol (MCP) tools that let an AI assistant read and edit the board. MCP is a standard way for an AI app to call tools, here to read and draw on the board.<br>
For personal handwritten study notes, using a mouse or a paired iPad.<br>
Stack: Python standard-library server, plain JavaScript and Canvas 2D frontend, and MCP.

![GPT Board demo: sketching a triangle while the AI adds a note](docs/demo/gpt-board-demo.gif)

## Run it

Needs Python 3.11 or newer (uses datetime.UTC). Tested on 3.12. You also need a browser. From the repository root, run:

```bash
python3 --version
python3 build.py
python3 server.py
```

Use `python3 --version` to check your interpreter version. The app needs no Python packages or frontend build dependencies. Open <http://localhost:8787>, choose **Notebooks** or **Whiteboards**, and wait for **Saved** before closing the board.

If the board is already running, use the existing server instead of starting another copy. Drawing works without an AI connection. AI tools need a separately configured MCP client and an open board tab; review logging also needs a separately configured Math Learning System. The iPad bridge is optional and needs pairing. [Existing commands and configuration](docs/DETAILS.md#run-and-build) cover those installation-specific pieces.

## How it works

- The browser handles drawing, selection, erasing, page navigation, and rendering with Canvas 2D.
- The Python server saves notebooks through atomic file replacement and exposes the MCP tools.
- Server-sent events deliver operations to one connected tab; the tab sends results back over HTTP. The server prefers the tab that saved most recently and falls back to another registered tab if it is disconnected.
- The optional paired iPad bridge forwards board routes, but does not expose the MCP endpoint.
- **Standard library only:** nothing to install beyond Python to run the backend.
- **SSE over WebSockets:** the server only needs to push edits one way to the board tab; the tab sends its responses over HTTP. SSE runs over plain HTTP, the browser reconnects on its own, and Python's standard library has no WebSocket support.

[Trade-offs](docs/DETAILS.md#trade-offs)

Python implements the backend; `server.js`, `ipad-server.js`, `load-session.js`, and `build-native.mjs` only launch their Python counterparts, while browser drawing and input stay in JavaScript.

[Architecture, tool definitions, and interaction details](docs/DETAILS.md#architecture)

## Tests

[![CI](https://github.com/AntonioZannella/gpt-board/actions/workflows/ci.yml/badge.svg)](https://github.com/AntonioZannella/gpt-board/actions/workflows/ci.yml)

The test command is tested on Python 3.12 and Node 24. From the repository root, run:

```bash
python3 test.py
```

This runs the Python backend tests and JavaScript drawing/client contract checks. The review-log integration test is skipped unless `MLS_TEST_CONTROLLER` points to the separately installed Math Learning System controller. Test servers and synthetic records use temporary directories.

The CI workflow runs Ruff and `python test.py`, covering the Python backend and JavaScript drawing/client checks. The external-controller integration is skipped in CI because the controller is not part of this repository. The interactive browser audits are separate: [ink and notebook checks](docs/DETAILS.md#single-page-notebooks-and-safari-input-repair) and [notebook resume checks](docs/DETAILS.md#resume-the-last-notebook).

## Limitations

- This is a personal study tool at an early stage. Setup is tied to the existing installation; public installation, cross-platform support, and multi-user hosting are not ready yet.
- The current Math Learning System integration has installation-specific course defaults. Copying this repository does not include its controller, course records, or notebooks.
- The frontend is a custom Canvas 2D engine. Arrows have fixed endpoints rather than live attachment constraints; interactive shape rotation/resizing and rich-text editing are not included.
- Physical Apple Pencil/iPad behavior still needs device validation. Desktop and simulated-touch checks cannot replace it.
- The server is meant for localhost. Do not expose it to a network you do not trust.

## Data and privacy

Notebook files, imported page images, connection tokens, and review evidence are stored locally in `data/`. The browser stores view positions and collection preferences. When a connected AI requests board content, that content is sent to the AI service and is subject to its policies.

`data/`, `sessions/`, `code-backups/`, local settings, and secrets are ignored by Git. Ignoring a file does not remove it from existing history or prevent force-adding it. Keep pairing links, tokens, course content, and review records private.

## Authorship and project notes

I design the features and test them, using AI coding tools for implementation. I am learning the Python backend line by line as I maintain it; browser drawing and input run in JavaScript.

[Project history, migration notes, validation notes, and rollback instructions](docs/DETAILS.md) are kept separately from this overview. Historical validation counts describe earlier checks, not a new benchmark or a current guarantee.
