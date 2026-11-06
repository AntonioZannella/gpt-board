"""Persistent notebook records shared by the HTTP server and local utilities."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
import re
import secrets
import struct
import tempfile
import threading
import unicodedata
import zlib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()
MAX_BOARD_BYTES = 48 * 1024 * 1024


class ClientError(ValueError):
    """A request the server refuses. `reason` is a fixed sentence, safe to
    send back to the browser or MCP client (no paths or library details)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class BoardSizeError(ClientError):
    pass


def dumps(value):
    # Compact UTF-8 matches JSON.stringify for review identity and record fingerprints.
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def decode_png(value):
    """Validate the data URL and PNG chunk framing before writing any files."""
    error = 'Invalid PNG image.'
    if not isinstance(value, str) or not value.startswith('data:image/png;base64,'):
        raise ValueError(error)
    try:
        image = base64.b64decode(value.split(',', 1)[1], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError(error) from None
    if not image.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError(error)
    offset, seen_header, seen_data = 8, False, False
    while offset + 12 <= len(image):
        size = int.from_bytes(image[offset:offset + 4], 'big')
        end = offset + 12 + size
        if end > len(image):
            raise ValueError(error)
        kind = image[offset + 4:offset + 8]
        data = image[offset + 8:end - 4]
        checksum = int.from_bytes(image[end - 4:end], 'big')
        if zlib.crc32(kind + data) != checksum:
            raise ValueError(error)
        if not seen_header:
            if kind != b'IHDR' or size != 13 or 0 in struct.unpack('>II', data[:8]):
                raise ValueError(error)
            seen_header = True
        elif kind == b'IHDR':
            raise ValueError(error)
        if kind == b'IDAT':
            seen_data = True
        if kind == b'IEND':
            if size or end != len(image) or not seen_data:
                raise ValueError(error)
            return image
        offset = end
    raise ValueError(error)


def read_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return fallback


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content if isinstance(content, bytes) else content.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path, value):
    atomic_write(path, dumps(value))


def append_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, path.open('a', encoding='utf-8') as stream:
        stream.write(dumps(value) + '\n')
        stream.flush()


def stamp():
    now = datetime.now(UTC)
    return {'t': now.timestamp(), 'ts': now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')}


def secret(path, size):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    value = secrets.token_hex(size)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
    return value


def blank_snapshot(source, name):
    keep = {key: copy.deepcopy(record) for key, record in (source or {}).get('store', {}).items()
            if isinstance(record, dict) and record.get('typeName') in ('document', 'page')}
    for record in keep.values():
        if record['typeName'] == 'page':
            record['name'] = name
    return {'store': keep, 'schema': (source or {}).get('schema')}


def empty_snapshot(name):
    return {'store': {
        'document:document': {'id': 'document:document', 'typeName': 'document', 'name': '', 'meta': {}},
        'page:page': {'id': 'page:page', 'typeName': 'page', 'name': name, 'index': 'a1', 'meta': {}}
    }, 'schema': {'schemaVersion': 2, 'sequences': {}}}


def ink_by_frame(store):
    store = store or {}
    frames = {s['id']: s.get('props', {}).get('name', s['id']) for s in store.values()
              if isinstance(s, dict) and s.get('type') == 'frame'}
    ink = {}
    for shape in store.values():
        if not isinstance(shape, dict) or shape.get('type') != 'draw':
            continue
        current = shape
        for _ in range(20):
            if not current:
                break
            parent = current.get('parentId')
            if parent in frames:
                ink.setdefault(frames[parent], []).append(shape['id'] + dumps(shape.get('props')))
                break
            current = store.get(parent)
    return {frame: hashlib.sha1('|'.join(sorted(values)).encode()).hexdigest() for frame, values in ink.items()}


class BoardStorage:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.data = self.root / 'data'
        (self.data / 'notebooks').mkdir(parents=True, exist_ok=True)
        self.last_ink = {}

    def metadata(self):
        with LOCK:
            existing = read_json(self.data / 'notebooks.json')
            if existing and existing.get('notebooks'):
                return existing
            board = read_json(self.data / 'board.json', {}) or {}
            page = next((r for r in board.get('store', {}).values() if isinstance(r, dict) and r.get('typeName') == 'page'), {})
            initial = {'notebooks': [{'id': 'default', 'name': page.get('name') or 'My Notebook'}]}
            write_json(self.data / 'notebooks.json', initial)
            return initial

    def require(self, notebook):
        if not isinstance(notebook, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', notebook) or not any(b['id'] == notebook for b in self.metadata()['notebooks']):
            raise ClientError('Notebook not found.')
        return notebook

    def path(self, notebook, name):
        # IDs must also be safe for callers preparing a new notebook before registering it.
        if not isinstance(notebook, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', notebook):
            raise ClientError('Invalid notebook ID.')
        return (self.data if notebook == 'default' else self.data / 'notebooks' / notebook) / name

    def read(self, notebook, name='board.json', fallback=None):
        return read_json(self.path(notebook, name), fallback)

    def create(self, name, kind='notebook'):
        if kind not in ('notebook', 'whiteboard'):
            raise ClientError('Invalid collection type.')
        name = str(name or '').strip()[:80]
        if not name:
            raise ClientError('Enter a notebook name.')
        with LOCK:
            meta = self.metadata()
            base = re.sub('[^a-z0-9]+', '-', unicodedata.normalize('NFKD', name.lower())).strip('-')[:48] or 'notebook'
            notebook, n = base, 2
            ids = {b['id'] for b in meta['notebooks']}
            while notebook in ids:
                notebook, n = f'{base}-{n}', n + 1
            source = self.read('default') or (empty_snapshot(name) if kind == 'whiteboard' else None)
            snapshot = blank_snapshot(source, name)
            if not snapshot['schema'] or not snapshot['store']:
                raise ClientError('The current notebook cannot be used as a template.')
            write_json(self.path(notebook, 'board.json'), snapshot)
            item = {'id': notebook, 'name': name, 'kind': kind}
            meta['notebooks'].append(item)
            write_json(self.data / 'notebooks.json', meta)
            return item

    def save_changes(self, notebook, changes, summary):
        """Merge browser shape edits in Python, keeping PDF assets off the input thread."""
        if not isinstance(changes, dict) or set(changes) != {'put', 'remove'}:
            raise ClientError('Invalid shape changes')
        put, remove = changes['put'], changes['remove']
        if not isinstance(put, list) or not isinstance(remove, list):
            raise ClientError('Invalid shape changes')
        if any(not isinstance(s, dict) or s.get('typeName') != 'shape' or
               not isinstance(s.get('id'), str) or not s['id'].startswith('shape:') or
               not isinstance(s.get('props'), dict) for s in put):
            raise ClientError('Only shape records may be updated')
        if any(not isinstance(key, str) or not key.startswith('shape:') for key in remove):
            raise ClientError('Only shape records may be removed')
        with LOCK:
            snapshot = self.read(self.require(notebook))
            if not snapshot or not isinstance(snapshot.get('store'), dict):
                raise ClientError('Notebook unavailable')
            records = snapshot['store']
            ids = [s['id'] for s in put] + remove
            if len(ids) != len(set(ids)):
                raise ClientError('Duplicate shape changes')
            if any(key in records and records[key].get('typeName') != 'shape' for key in ids):
                raise ClientError('Cannot replace non-shape records')
            for key in remove:
                records.pop(key, None)
            for shape in put:
                records[shape['id']] = shape
            self.save(notebook, snapshot, summary)

    def save(self, notebook, snapshot, summary, png=None):
        image = decode_png(png) if png is not None else None
        snapshot_bytes = dumps(snapshot).encode('utf-8')
        summary_bytes = dumps(summary).encode('utf-8')
        if len(snapshot_bytes) + len(summary_bytes) + len(image or b'') > MAX_BOARD_BYTES:
            raise BoardSizeError('Board save exceeds the 48 MiB limit; existing board was preserved.')
        with LOCK:
            self.require(notebook)
            ink = ink_by_frame(snapshot['store'])
            previous = self.last_ink.get(notebook)
            if previous is None:
                previous = ink_by_frame((self.read(notebook) or {}).get('store'))
            # Write before advertising a successful save or activity event.
            atomic_write(self.path(notebook, 'board.json'), snapshot_bytes)
            atomic_write(self.path(notebook, 'summary.json'), summary_bytes)
            if png:
                atomic_write(self.path(notebook, 'board.png'), image)
            for frame, fingerprint in ink.items():
                if fingerprint != previous.get(frame):
                    append_json(self.data / 'activity.jsonl', {**stamp(), 'notebook': notebook, 'frame': frame})
            self.last_ink[notebook] = ink
