# iPad connection and writing recovery

## Everyday connection

Open **Connect iPad** on the Mac. The preferred URL uses the Mac's system Bonjour name (`.local`); the numeric Wi-Fi URL remains available as a fallback. The bridge accepts only its configured addresses and still requires per-device pairing. Some networks block Bonjour or communication between clients.

A different hostname is a different browser origin. If you already paired with the number address, keep using that URL until all writing shows **Saved**. Pair once at the stable hostname afterward. Browser recovery data does not automatically move between origins. Do not clear website data to troubleshoot unsent writing.

For Wi-Fi changes, start with `python3 board.py start`: the existing supervisor restarts the bridge when the Mac's address changes. The standalone `ipad_server.py` process does not supervise network changes.

## Keep the Mac available

In **Connect iPad**, enable **Keep this Mac awake while GPT Board is running**. This creates a process-bound macOS idle-sleep assertion. The display can sleep. Turning the option off or stopping the backend releases it. It is off after a backend restart unless requested again.

For supervised startup: `python3 board.py start --keep-awake`. Use `--no-build` when intentionally preserving an existing built frontend while source UI work is in progress.

This does not override closing the lid, deliberate sleep, shutdown, or loss of power. It can increase battery use while enabled.

## Saving and recovery

Completed edits are journaled in IndexedDB before they are sent to the Mac. Each tab has its own journal. If IndexedDB is unavailable, the browser attempts the localStorage fallback; suspension also triggers a synchronous fallback. Neither path claims retention until its write succeeds.

- **Saved**: the Mac acknowledged the current writing.
- **Kept on this device**: the browser confirmed local retention; the Mac has not necessarily saved it.
- **Keep this tab open**: local retention has not been confirmed.

Reopening the same origin and notebook replays orphaned journals using the existing three-way merge. An exact acknowledgment clears only the journal generation it confirms. Conflicting or malformed originals are retained. **Export recovery** downloads the current notebook's pending entries and original journal data, including IndexedDB records. The export is recovery evidence, not a portable full notebook.

The open board supports disconnected writing. This change does **not** promise a cold launch while the Mac is unreachable: the application and notebook still load from the Mac. Storage can be cleared by the user or operating system. Keep backups; do not treat browser persistence as a guarantee.

## Scope of this improvement

Existing per-device HttpOnly-cookie authentication, SSE notifications, shape revision checks, rolling backups, and Pencil sample handling remain in place. No JWT migration, competing mDNS service, background-sync guarantee, VPN installation, or certificate trust change is needed for these improvements. HTTPS/offline app-shell distribution and a native WKWebView wrapper are separate deployment work.

## Verification

Automated tests cover delayed journal commits, stale acknowledgments, storage denial, hostname fallback, and keep-awake process cleanup. Isolated real-browser checks cover IndexedDB reopening, separate tabs, lost save acknowledgment without duplicate strokes, writing during simulated save failure, reloading the unsent writing, and automatic sync after service returns. Physical iPad interruption and Wi-Fi-change checks remain useful acceptance tests.
