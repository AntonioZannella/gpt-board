#!/usr/bin/env python3
"""Load one existing prebuilt Calc 3 session, backing up the previous board."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from board_storage import ROOT, write_json


def load_session(number, root=ROOT):
    root = Path(root)
    sessions = sorted((root / 'sessions').glob('*.tldr'))
    source = next((f for f in sessions if f.name.startswith(f'Calc3 S{number} ')), None)
    if source is None:
        raise ValueError(f'No session {number}. Have: ' + ', '.join(f.name for f in sessions))
    session = json.loads(source.read_text())
    records, schema = session.get('records'), session.get('schema')
    if not isinstance(records, list) or not schema or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in records):
        raise ValueError('Invalid session snapshot; current board was preserved.')
    board = root / 'data/board.json'
    if board.exists():
        backups = root / 'data/backups'
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S-%fZ')
        shutil.copy2(board, backups / f'board-{stamp}.json')
    write_json(board, {'store': {r['id']: r for r in records}, 'schema': schema})
    for name in ('summary.json', 'board.png'):
        (root / 'data' / name).unlink(missing_ok=True)
    print(f'Loaded {source.name} ({sum(r.get("type") == "frame" for r in records)} frames). Old board saved in data/backups/. Now open http://localhost:8787')


def main():
    parser = argparse.ArgumentParser(description=__doc__ + ' Close board tabs before loading, then reopen.')
    parser.add_argument('number', help='Existing Calc 3 session number')
    args = parser.parse_args()
    try:
        load_session(args.number)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
