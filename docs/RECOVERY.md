# Recovery and upgrades

Run commands from the GPT Board folder with the interpreter used to start it. Preserve `data/`, browser website data and the existing Math Learning System/error log. Never restore old notebook data as part of code rollback.

## Upgrade or roll back code

1. On every device, finish the stroke and wait for **Saved**. If saving fails, preserve the open tab and use Recovery to export its journal first.
2. Close all tabs; stop the board and bridge in their owning terminal or use `python3 board.py stop`. An older running service may lack the new safe-shutdown endpoint; in that case use Ctrl-C in its owning terminal after saving/closing tabs. Do not interrupt live writing. This candidate was prepared without restarting the personal services.
3. Copy the complete current `data/` directory to a private backup location outside rolling backups; also preserve current source. Keep the canonical learning system and shared log in their normal backup process.
4. Replace only application source/support files, never `data/`, `sessions/`, tokens, course stores or the error log. Install `requirements.txt` in a venv if using media/QR features. Build/start: `.venv/bin/python board.py start`.
5. Reload every board tab, verify notebook/page/writing, then test the existing paired iPad. New code preserves pairing files and legacy-token identity. Old tabs must not remain open against the new release.

For rollback, stop writers and restore only the prior code/build. Test the old code against a *copy* of current data first. Old releases do not understand all new receipt/provenance guarantees. Keep current data and preserved source snapshots; do not use `git reset --hard` or an old data backup as a shortcut.

## Unsaved work and disconnection

**Saved** means the Mac accepted the authoritative snapshot. **Kept on this device** means that completed edit's browser-storage write succeeded, not that a Mac backup exists. Storage denial/quota leaves the tab explicitly unsafe to close.

Keep the Mac awake, reconnect the same Wi-Fi, and run `board.py status` in another terminal. Let the existing tab retry. If it was already closed, return to the same browser/origin/notebook after the server is reachable. Each document has an independent versioned journal; active tabs do not consume one another's queues. Reload/reconnect merges with newer server state.

Conflicts preserve the saved server version and retain the local journal. Open **Recovery / Help** to download the raw journal or **a separate notebook with recovered local versions**. Open Files to preview/import that recovery notebook under a new ID. Inspect both versions before deciding what to keep. No automatic browser-history pruning is performed. Broken journal bytes are left in browser storage; preserve the browser profile if normal export cannot parse them.

Do not clear website data, change origins/ports or switch to private browsing during recovery. Browser eviction, hardware failure or interruption before a stroke ends can still lose local work. There is no offline-start cache: the server must be reachable to load the saved notebook and application.

## Uncertain AI edits

Retry the **identical operation_id and payload**. The notebook contains the receipt with the edit, so an already-committed operation returns its saved result. A different payload with the same ID is refused. Inspect/undo it in AI. Undo refuses to overwrite later changes. A pending protected edit requires real UI approval and expires after ten minutes; stale proposals must be recreated. Do not use model-supplied permission flags.

## Restore one notebook

Stop all writers, preserve current `data/`, then:

```sh
python3 board.py backups NOTEBOOK_ID
python3 board.py restore NOTEBOOK_ID ACTUAL_BACKUP_FILENAME.json
```

Use the ID from the notebook URL/index and an actual filename returned by `backups`. Restore validates the backup, preserves current content as a forced backup or `damaged-<timestamp>.json`, then atomically replaces only the selected notebook. Damaged current bytes are copied aside before replacement, so a failed restore leaves the current file present. Current AI receipt history is retained with `reverted_by_restore` status. Course records, historical checks and other notebooks are unchanged.

The authoritative board may be newer than summary/activity/preview projections after a secondary write failure; these projections never override it. Restore does not claim to repair historical activity gaps. Check the writing after restarting. To reverse a restore, use the valid current-content backup it printed, with writers stopped again.

Rolling backups keep pre-save snapshots at most once per ten minutes of active editing: newest five plus the last snapshot of each day in fourteen days. Forced backups bypass spacing but still participate in normal retention. Copy a backup elsewhere if it must survive later pruning. Same-disk backups are not disaster protection.

## Missing or damaged index/JSON

Invalid JSON, record semantics, missing indexes with saved folders and orphaned notebook folders produce explicit errors. Originals stay untouched. With writers stopped and a private data backup:

```sh
python3 board.py recover-index --dry-run
python3 board.py recover-index
```

Preview scans existing snapshots and preserves known metadata when available. Apply refuses damaged snapshots instead of dropping them. It saves any existing index under a timestamped backup, then rebuilds the list. A wholly lost index may have lost titles, archive state or whiteboard classification; review the proposed list before applying. Repair damaged snapshots from valid backups first. When no valid backup exists, preserve damaged bytes for manual repair.

A notebook created before an interrupted index write remains in its folder, cannot be overwritten by reusing its name, and is recovered through this command. A missing snapshot cannot be recreated from index metadata. Pairing corruption is refused and its file is preserved; restore its private known-good backup instead of deleting credentials.

## Export, privacy and cancellation

**Files** offers notebook JSON preview/import with a new ID, or use `board.py export ID file.json` and `board.py import file.json --dry-run`. Run index-changing CLI maintenance with writers stopped. JSON contains selected notebook writing/assets and AI history, not MCP tokens or canonical learning records. Imported permissions/receipts are stripped; internal page/shape IDs remain.

A media import's Cancel button deletes staging before commit; a decoder already running has a bounded exit deadline. Closing a dialog cancels staging. Abandoned stages expire after one hour. Completed imports are ordinary pages: Trash/restore them if necessary. Exporting is not a backup of configuration or learning history.

## Connection diagnostics

| Symptom | Action |
| --- | --- |
| No connection after sleep | Wake Mac; keep tab/journal; check `board.py status` and retry. |
| Changed Wi-Fi address | Supervisor rebinds bridge; use the current address in Connect iPad. Keep the old origin for recovery of its browser journal. |
| `.local` fails | Try the displayed numeric address; validate `.local` on hardware separately. |
| Expired/reused pairing code | Create a fresh one-use code. Ten wrong guesses invalidate outstanding invitations. |
| Revoked device stream | The bridge checks session validity about once per second and closes an open stream. Subsequent reads/writes are refused. |
| Another process owns port/lock | Do not kill an unknown process; status identifies the board. A valid old process is reported rather than replaced. |
| Bridge orphaned after board exit | It exits after three failed five-second upstream checks; then start normally. |
| AI unavailable | Check local MCP configuration and selected notebook/frame. Board Connected is not AI authentication. |
| Media unavailable | Install pinned requirements into the same interpreter's environment; encrypted/damaged/oversized documents are refused. |
| Evidence limit reached | Preserve/export checks; deliberately raise `review.max_evidence_bytes` if appropriate. Never silently prune canonical evidence. |
