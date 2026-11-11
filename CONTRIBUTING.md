# Contributing

Read AGENTS.md, README, SECURITY and RECOVERY before changing persistence or input. Preserve the architecture and existing user data. Use readable Python for backend/utilities and JavaScript for browser-local input; do not route pen movement over the network.

## Isolated setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python test.py
.venv/bin/ruff check --isolated .
.venv/bin/python -m playwright install chromium webkit
.venv/bin/python tests/browser_smoke.py --engine chromium
```

Node is needed for JavaScript contracts. Windows substitutes `.venv\Scripts\python.exe`; run actual Windows checks before claiming support. All tests use temporary notebooks/courses; do not add a personal data directory to a fixture. Browser artifacts can contain test writing; keep them synthetic. CI retains traces/screenshots/logs for seven days.

A regression should first reproduce an observable failure, then verify preserved data and the corrected result. Test the release ZIP as well as the checkout when changing installation. Update the original numbered readiness row and describe actual limitations. Run benchmarks before proposing storage/rendering optimization.

## Useful small contributions

- Add an accessible shortcut to open Files, with a focused browser check for dialog focus and Escape.
- Reproduce one real Pencil failure on synthetic paper, then add a targeted browser simulation where it is meaningful.
- Add a new malformed packed-stroke fixture showing rejection without data changes.
- Improve one recovery message based on an observed first-time user's confusion, with consent and no analytics added by default.

These are candidates, not preclaimed contributions. Do not fabricate issues, commit activity, user counts or authorship. Antonio writes his own commit messages. Never commit private notebooks, sessions, tokens, configuration, evidence or historical code/data backups.
