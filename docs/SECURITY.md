# Security and privacy

## Supported boundary

This is a personal localhost application plus a paired bridge on controlled local Wi-Fi. Main HTTP binds loopback and refuses direct `--lan`. The bridge is **unencrypted HTTP**: pairing does not protect against network observation or alteration. Public tunnels, untrusted networks, accounts and hosted multi-user use remain unsupported and need established TLS/authentication infrastructure.

Local programs/users with data-directory access and paired devices are trusted. Host/Origin checks do not sandbox malicious local software. Paired devices can edit all notebooks. The AI permission boundary is the exposed MCP API and its separate human UI; it is not a defense against a local process directly rewriting files or driving the user interface.

## Controls and limits

| Threat | Implemented control / boundary |
| --- | --- |
| Foreign webpage / CSRF | Exact Origin/Host checks, JSON-only POSTs, no permissive CORS; native no-Origin clients remain supported. |
| Unpaired LAN client | Bridge cookie authentication, Host/Origin checks, narrow route allowlist; no MCP, shutdown or pairing-administration forwarding. |
| Copied/guessed invitation | One-use random code, ten-minute expiry, guessing limit, maximum pending invitations/devices; QR is another representation of the same secret. |
| Lost device | Hashed sessions, inactivity expiry, explicit revocation, open-stream validity checks about once per second. Legacy token remains one revocable device. |
| AI changing student work | Server provenance for all shapes; exact human-approved proposals bound to current store and expiry; no model authorization flag. Pages/images/locks always protected. |
| Lost AI response | Request-hashed stable operation IDs; mutation and receipt share one atomic snapshot. Changed retries fail. Safe UI undo checks newer edits. No universal exactly-once claim. |
| Stale writing | Revision conflicts and three-way shape merge; losing local variant retained for recovery instead of silently discarded. |
| Malformed notebook | Allowed records/shapes, matching IDs, finite numbers, dimensions, depth/parent cycle limits, bounded packed/plain strokes, PNG framing/CRC/IHDR/deflate validation. Imported AI provenance is stripped. |
| Decoder/exhaustion | HTTP body/deadline limits; 64 worker connections per service, 16 board streams, bounded queues; two media workers, 30-second timeout, decoded-image/size/page limits. Python JSON/file operations remain proportional to bounded notebook size. |
| Historical evidence growth | Configurable evidence ceiling defaults to 512 MiB, no silent pruning. AI receipts cap at 5000/notebook and refuse further operations instead of dropping history. |
| Interrupted save / corruption | File+directory synchronization where supported, atomic snapshot/receipt replacement, rolling backups, explicit metadata reconstruction; derived projections are not multi-file transactions. |

Native PDF decoders remain a dependency risk; subprocess bounds reduce impact but are not an OS security sandbox. Tests cover specified failures, not every malformed document or independent penetration testing. Windows locking is implemented with `msvcrt`, but Windows execution is still unverified.

## Data and retention

| Location | Contents and policy |
| --- | --- |
| `data/board.json`, `data/notebooks/<id>/board.json` | Writing, embedded images, AI receipts and before/after undo data; no automatic notebook deletion. |
| `data/notebooks.json` | Names, stable IDs and archive/trash state. Metadata changes preserve a prior index. |
| `data/backups/` | Rolling snapshots and explicitly preserved damaged/index copies; see Recovery for exact retention. |
| `data/token`, `data/ipad-token`, `data/ipad-devices.json` | Private MCP/pairing credentials and hashed device registry. Keep all through upgrade. |
| `data/config.json` | Private controller/interpreter/course configuration. |
| `data/checks/`, `data/activity.jsonl` | Archived review evidence/index and writing activity. No silent historical-evidence deletion. |
| `data/import-staging/` | Uploaded original media and rendered pages; explicit cancellation or one-hour expiry removes only staging. |
| `thumbnail.json` per notebook | Last saved preview; a derived artifact that can become stale. |
| Browser storage/cookies | View choices, per-document journals, preserved conflicts and pairing cookie. Browser eviction/clearing can remove them. |
| External Math Learning System | Canonical attempts, session/audit records and shared error log; never replaced by a board ledger. |

Notebook JSON includes selected writing/assets and AI operation history, so share deliberately. Canonical review history and credentials are excluded. The history panel omits filesystem evidence paths and never writes the canonical files. Use the canonical learning system's policies for historical evidence deletion, not handwritten ledger edits. A privacy export should include a private full data backup and separately maintained learning records; it is not permission to distribute them.

## Distribution audit

`scripts/privacy_audit.py` scans intended working sources and all reachable Git blobs for private paths, credential patterns and exact active local secret values, reporting only categories/counts/object identifiers. It never prints matched values, rewrites history or publishes anything. The recorded local audit scanned 114 reachable blobs with no findings; rerun before any later publication. Pattern scans cannot prove absence, and unreachable objects/external remotes are outside that check. Packages use an explicit source allowlist and per-file SHA-256 manifest; no Git directory or personal data is bundled.

## Reporting

Use the [repository issues](https://github.com/AntonioZannella/gpt-board/issues) for nonsensitive, synthetic reproductions. For security-sensitive details, use an already established private owner channel. If none exists, request one without posting secrets or exploit details publicly. A dedicated verified private reporting contact is still pending; no response-time promise is made. Do not attach real handwriting, courses, pairing URLs, tokens or configuration to public reports.
