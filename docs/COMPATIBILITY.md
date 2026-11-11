# Compatibility and verification boundaries

| Environment | Evidence / claim |
| --- | --- |
| Local macOS 26.5.1 arm64, Python 3.12.0 | Backend/Node suites, clean source installation, subprocess media conversions, OS locks and release-install workflows executed locally. |
| Local Chromium 158 / Playwright 1.65.0 | Automated disposable drawing/save/reload, multiple devices/tabs, recovery, AI targeting/approval/undo, page lifecycle and import/export workflows. Browser build recorded in test artifacts. |
| Local WebKit 27.2 / Playwright 1.65.0 | Same automated workflows. WebKit is complementary evidence, not physical Safari or Apple Pencil certification. |
| Ubuntu/macOS, Python 3.11/3.12, Node 24 CI | Configured; hosted runs not observed for this uncommitted candidate. |
| Windows | Cross-platform lock implementation and documented interpreter path only. Actual install, locking, networking and browser behavior unverified. |
| Physical iPad/iPadOS/Safari/Apple Pencil | User testing required; see HUMAN_CHECKS.md. No new hardware check claimed. |

Core app: Python 3.11+ and a modern browser with Canvas 2D, modules, EventSource and dialog support. Browser recovery uses localStorage; BroadcastChannel improves active-tab ownership. Without BroadcastChannel, recent journals wait 30 seconds before recovery. A full offline start is unsupported.

Optional product dependencies are pinned in requirements.txt; development tools in requirements-dev.txt. The native PDFium renderer runs in separate processes, not HTTP threads. A new unsupported PDF must be reported with a synthetic/minimal reproducer and its specific parser outcome; do not generalize support from one successful file.

Tests distinguish simulation from hardware. Synthetic latency/heap/frame statistics describe the named machine and workload; no baseline comparison or performance gain is implied. Human authorship, interview understanding, user studies and learning outcomes are not automated-test results.
