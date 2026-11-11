"""Persistent notebook records shared by the HTTP server and local utilities."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import struct
import sys
import tempfile
import threading
import unicodedata
import zlib
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()
MAX_BOARD_BYTES = 48 * 1024 * 1024
NOTEBOOK_ID = r'[a-z0-9][a-z0-9-]{0,63}'
EXPORT_FORMAT = 'gpt-board-notebook'
EXPORT_VERSION = 1
# Rolling backups: at most one per interval while a notebook is being edited.
# Keep the newest few, plus the last backup of each recent day.
BACKUP_INTERVAL_SECONDS = 10 * 60
BACKUPS_RECENT = 5
BACKUP_DAYS = 14
BACKUP_STAMP = '%Y%m%dT%H%M%S%fZ'  # microseconds: two backups never share a name


class ClientError(ValueError):
    """A request the server refuses. `reason` is a fixed sentence, safe to
    send back to the browser or MCP client (no paths or library details)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class BoardSizeError(ClientError):
    pass


class StaleTabError(ClientError):
    """The client did not prove which revision it was editing."""

    def __init__(self):
        super().__init__('Reload this board tab.')


class ConflictError(ClientError):
    """The notebook changed since this client last read it. Carries the
    current revision and shape records so the client can merge and retry."""

    def __init__(self, rev, shapes):
        super().__init__('The notebook changed on another device.')
        self.rev = rev
        self.shapes = shapes


class DamagedFileError(Exception):
    """A data file exists but cannot be read. It is left untouched: the
    server refuses to replace it with something fresh. See docs/RECOVERY.md."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


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
    if len(image) > 24 * 1024 * 1024:
        raise ValueError(error)
    offset, seen_header, seen_data = 8, False, False
    inflater = zlib.decompressobj()
    decoded = 0
    expected = None
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
            width, height = struct.unpack('>II', data[:8])
            if width * height > 24_000_000 or max(width, height) > 16384:
                raise ValueError(error)
            depth, color, compression, filtering, interlace = data[8:]
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
            allowed = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if not channels or depth not in allowed[color] or compression or filtering or interlace not in (0, 1):
                raise ValueError(error)
            passes = [(0, 0, 1, 1)] if interlace == 0 else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
            expected = 0
            for x, y, dx, dy in passes:
                w, h = max(0, (width-x+dx-1)//dx), max(0, (height-y+dy-1)//dy)
                if w and h:
                    expected += h * (1 + (w * channels * depth + 7)//8)
            if expected > 192 * 1024 * 1024:
                raise ValueError(error)
            seen_header = True
        elif kind == b'IHDR':
            raise ValueError(error)
        if kind == b'IDAT':
            seen_data = True
            try:
                pending = data
                while pending:
                    output = inflater.decompress(pending, min(65536, expected - decoded + 1))
                    decoded += len(output)
                    if decoded > expected or inflater.unused_data:
                        raise ValueError(error)
                    pending = inflater.unconsumed_tail
            except zlib.error:
                raise ValueError(error) from None
        if kind == b'IEND':
            if size or end != len(image) or not seen_data or not inflater.eof or decoded != expected:
                raise ValueError(error)
            return image
        offset = end
    raise ValueError(error)


def read_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return fallback


def load_json(path, what):
    """None when the file is absent; DamagedFileError when it exists but is
    not valid JSON. read_json cannot tell those two cases apart."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        raise DamagedFileError(f'{what} could not be read and was left untouched. See docs/RECOVERY.md.') from None
    try:
        return json.loads(text)
    except ValueError:
        raise DamagedFileError(f'{what} is damaged and was left untouched. See docs/RECOVERY.md.') from None


def finite(value):
    return ((isinstance(value, int) and not isinstance(value, bool))
            or (isinstance(value, float) and math.isfinite(value)))


def check_shape(shape):
    """Structural checks for one shape record from a browser, AI or import."""
    if (not isinstance(shape, dict) or shape.get('typeName') != 'shape'
            or not isinstance(shape.get('id'), str) or not shape['id'].startswith('shape:')
            or not 6 < len(shape['id']) <= 120 or not isinstance(shape.get('props'), dict)):
        raise ClientError('Only shape records may be updated')
    if not isinstance(shape.get('type'), str) or not 0 < len(shape['type']) <= 32:
        raise ClientError('Shape type is missing or invalid.')
    for key in ('x', 'y', 'rotation', 'opacity'):
        if key in shape and not finite(shape[key]):
            raise ClientError('Shape position values must be finite numbers.')
    if 'parentId' not in shape or not isinstance(shape['parentId'], str):
        raise ClientError('Shape parent is invalid.')
    from validation import shape_details
    shape_details(shape)


def check_parents(shapes, records):
    """Every parent must exist, and following parents must end at a page
    rather than loop (a loop would hang code that walks up the chain)."""
    for shape in shapes:
        seen, current = set(), shape
        while current.get('typeName') == 'shape' and 'parentId' in current:
            if current['id'] in seen:
                raise ClientError('Shape parents form a loop.')
            seen.add(current['id'])
            if len(seen) > 24:
                raise ClientError('Shape parent nesting exceeds 24 levels.')
            current = records.get(current['parentId'])
            if not isinstance(current, dict) or current.get('typeName') not in ('shape', 'page'):
                raise ClientError('Shape parent does not exist.')


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
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
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


# MCP URL paths, pairing keys and codes, and the iPad cookie all carry secrets.
SECRET_PATTERN = re.compile(r'(/mcp/|[?&]key=|[?&]code=|board_ipad=)[^\s&;\'"]+')


class RedactingFormatter(logging.Formatter):
    """Masks secrets in the whole formatted record, tracebacks included."""

    def __init__(self, values=()):
        super().__init__('%(asctime)s %(levelname)s %(message)s')
        self.secrets = [value for value in values if value]

    def format(self, record):
        text = SECRET_PATTERN.sub(r'\1[redacted]', super().format(record))
        for value in self.secrets:
            text = text.replace(value, '[redacted]')
        return text


def setup_logging(*values):
    # stdout, because the launchers and tests wait for the first startup line there.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter(values))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def blank_snapshot(source, name):
    keep = {key: copy.deepcopy(record) for key, record in (source or {}).get('store', {}).items()
            if isinstance(record, dict) and record.get('typeName') in ('document', 'page')}
    for record in keep.values():
        if record['typeName'] == 'page':
            record['name'] = name
    return {'store': keep, 'schema': (source or {}).get('schema')}


def starter_snapshot():
    """The first notebook of a fresh installation: one page with a short
    guided exercise. Original text only; nothing personal is bundled."""
    snapshot = empty_snapshot('Welcome')
    lines = ('Welcome to GPT Board\n\n'
             '1. Write anything on this page with a mouse or Apple Pencil.\n'
             '2. Wait for the status at the bottom to say Saved.\n'
             '3. Reload the page. Your writing is still here.\n'
             '4. Use + Page to add paper, and + New for another notebook.\n'
             '5. Optional: connect an AI assistant (see README) and ask it to read this page.')
    paragraphs = [{'type': 'paragraph', 'content': [{'type': 'text', 'text': line}] if line else []}
                  for line in lines.split('\n')]
    base = {'typeName': 'shape', 'rotation': 0, 'isLocked': False, 'opacity': 1, 'meta': {}}
    snapshot['store']['shape:welcome-page'] = {
        **base, 'id': 'shape:welcome-page', 'type': 'frame', 'parentId': 'page:page', 'index': 'a1',
        'x': 0, 'y': 0, 'props': {'w': 1000, 'h': 1294, 'name': 'Page 1'}}
    snapshot['store']['shape:welcome-text'] = {
        **base, 'id': 'shape:welcome-text', 'type': 'text', 'parentId': 'shape:welcome-page', 'index': 'a2',
        'x': 60, 'y': 60, 'meta': {'ai': True},
        'props': {'w': 880, 'h': 300, 'color': 'blue', 'size': 's', 'richText': {'type': 'doc', 'content': paragraphs}}}
    return snapshot


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
        self.read_cache = OrderedDict()  # at most two notebooks / 48 MiB of source JSON
        self.revisions = {}  # notebook -> ((mtime, size), revision): avoids rehashing an unchanged file

    def metadata(self):
        with LOCK:
            index = self.data / 'notebooks.json'
            existing = load_json(index, 'The notebook list (data/notebooks.json)')
            if existing is not None:
                books = existing.get('notebooks') if isinstance(existing, dict) else None
                if not isinstance(books, list) or any(not isinstance(b, dict) or not isinstance(b.get('id'), str) for b in books):
                    raise DamagedFileError('The notebook list (data/notebooks.json) is damaged and was left untouched. See docs/RECOVERY.md.')
                ids = [b['id'] for b in books]
                if (not books or len(ids) != len(set(ids)) or any(not re.fullmatch(NOTEBOOK_ID, b['id'])
                        or not isinstance(b.get('name'), str) or not b['name'].strip()
                        or b.get('kind', 'notebook') not in ('notebook', 'whiteboard')
                        or b.get('status', 'active') not in ('active', 'archived', 'trash') for b in books)):
                    raise DamagedFileError('Notebook metadata is invalid. Preserve it and run board.py recover-index --dry-run.')
                orphaned = {p.parent.name for p in (self.data / 'notebooks').glob('*/board.json')} - set(ids)
                if orphaned:
                    raise DamagedFileError('Unindexed saved notebooks were preserved. Run board.py recover-index --dry-run before continuing.')
                return existing
            if any((self.data / 'notebooks').glob('*/board.json')):
                raise DamagedFileError('Notebook index is missing but saved notebooks exist. Run board.py recover-index --dry-run.')
            # First run. The list and the default notebook's file must agree,
            # so create the starter notebook before advertising it.
            board = load_json(self.data / 'board.json', 'The default notebook')
            if board is None:
                board = starter_snapshot()
                write_json(self.data / 'board.json', board)
            page = next((r for r in board.get('store', {}).values() if isinstance(r, dict) and r.get('typeName') == 'page'), {})
            from validation import snapshot as validate_snapshot
            try:
                validate_snapshot(board)
            except ClientError as exc:
                raise DamagedFileError('Default notebook is damaged; no index was created. ' + exc.reason) from None
            initial = {'notebooks': [{'id': 'default', 'name': page.get('name') or 'My Notebook'}]}
            write_json(index, initial)
            return initial

    def require(self, notebook):
        if not isinstance(notebook, str) or not re.fullmatch(NOTEBOOK_ID, notebook) or not any(b['id'] == notebook for b in self.metadata()['notebooks']):
            raise ClientError('Notebook not found.')
        return notebook

    def path(self, notebook, name):
        # IDs must also be safe for callers preparing a new notebook before registering it.
        if not isinstance(notebook, str) or not re.fullmatch(NOTEBOOK_ID, notebook):
            raise ClientError('Invalid notebook ID.')
        return (self.data if notebook == 'default' else self.data / 'notebooks' / notebook) / name

    def read(self, notebook, name='board.json', fallback=None, *, record_id=None):
        """Read an isolated snapshot, or one record without copying every stroke."""
        if name != 'board.json':
            return read_json(self.path(notebook, name), fallback)
        path = self.path(notebook, name)
        def signature():
            stat = path.stat()
            return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        with LOCK:
            try:
                key = signature()
            except FileNotFoundError:
                self.read_cache.pop(notebook, None)
                return fallback
            cached = self.read_cache.get(notebook)
            if cached and cached[0] == key:
                self.read_cache.move_to_end(notebook)
                # Callers merge edits into their copy; never expose cached mutable records.
                return copy.deepcopy(cached[1]['store'].get(record_id) if record_id is not None else cached[1])
            self.read_cache.pop(notebook, None)
            value = load_json(path, 'This notebook file')
            if value is None:
                return fallback
            try:
                from validation import snapshot as validate_snapshot
                validate_snapshot(value)
            except ClientError as exc:
                raise DamagedFileError('Notebook structure is damaged; original preserved. See docs/RECOVERY.md. ' + exc.reason) from None
            try:
                unchanged = signature() == key
            except FileNotFoundError:
                unchanged = False
            if unchanged and key[-1] <= MAX_BOARD_BYTES:
                self.read_cache[notebook] = (key, value)
                # The cache owns this object; only the requested data leaves it.
                result = value['store'].get(record_id) if record_id is not None else value
                while len(self.read_cache) > 2 or sum(row[0][-1] for row in self.read_cache.values()) > MAX_BOARD_BYTES:
                    self.read_cache.popitem(last=False)
                return copy.deepcopy(result)
            return value['store'].get(record_id) if record_id is not None else value

    def revision(self, notebook):
        """A fingerprint of the saved notebook file. A save must name the
        revision it was based on, so a stale copy cannot overwrite newer work."""
        path = self.path(notebook, 'board.json')
        try:
            stat = path.stat()
        except FileNotFoundError:
            return 'none'
        key = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        cached = self.revisions.get(notebook)
        if not cached or cached[0] != key:
            cached = (key, hashlib.sha256(path.read_bytes()).hexdigest()[:16])
            self.revisions[notebook] = cached
        return cached[1]

    def new_id(self, name, meta):
        base = re.sub('[^a-z0-9]+', '-', unicodedata.normalize('NFKD', name.lower())).strip('-')[:48] or 'notebook'
        notebook, n = base, 2
        ids = {b['id'] for b in meta['notebooks']} | {p.name for p in (self.data / 'notebooks').iterdir() if p.is_dir()}
        while notebook in ids:
            notebook, n = f'{base}-{n}', n + 1
        return notebook

    def create(self, name, kind='notebook'):
        if kind not in ('notebook', 'whiteboard'):
            raise ClientError('Invalid collection type.')
        name = str(name or '').strip()[:80]
        if not name:
            raise ClientError('Enter a notebook name.')
        with LOCK:
            meta = self.metadata()
            notebook = self.new_id(name, meta)
            # The default notebook supplies the schema when it has one; a new
            # installation, or a default without a usable schema, starts empty.
            try:
                template = self.read('default')
            except DamagedFileError:
                template = None
            snapshot = blank_snapshot(template, name)
            if not snapshot['schema'] or not snapshot['store']:
                snapshot = empty_snapshot(name)
            write_json(self.path(notebook, 'board.json'), snapshot)
            item = {'id': notebook, 'name': name, 'kind': kind}
            meta['notebooks'].append(item)
            write_json(self.data / 'notebooks.json', meta)
            return item

    def save_changes(self, notebook, changes, summary, base=None):
        """Merge browser shape edits in Python, keeping PDF assets off the input thread.

        `base` is the revision the browser last synchronized with. If the
        notebook has moved on, nothing is written: the browser receives the
        current shapes, merges, and retries. Returns the new revision."""
        if not isinstance(changes, dict) or set(changes) != {'put', 'remove'}:
            raise ClientError('Invalid shape changes')
        put, remove = changes['put'], changes['remove']
        if not isinstance(put, list) or not isinstance(remove, list):
            raise ClientError('Invalid shape changes')
        for shape in put:
            check_shape(shape)
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
            if base is None:
                # A tab from before revisions existed cannot prove what it last
                # saw, so it may add new strokes but not change or remove any.
                if remove or any(key in records for key in ids):
                    raise StaleTabError()
            elif base != self.revision(notebook):
                raise ConflictError(self.revision(notebook), [r for r in records.values() if isinstance(r, dict) and r.get('typeName') == 'shape'])
            for key in remove:
                records.pop(key, None)
            for shape in put:
                records[shape['id']] = shape
            check_parents([r for r in records.values() if r.get('typeName') == 'shape'], records)
            return self.save(notebook, snapshot, summary)

    def save_snapshot(self, notebook, snapshot, summary, png=None, base=None):
        """Replace a whole notebook. Refused unless `base` names the current revision."""
        with LOCK:
            self.require(notebook)
            if base != self.revision(notebook):
                raise StaleTabError()
            current = self.read(notebook)
            if current.get('_operations'):
                snapshot['_operations'] = current['_operations']
            else:
                snapshot.pop('_operations', None)
            if current.get('_imports'):
                snapshot['_imports'] = current['_imports']
            else:
                snapshot.pop('_imports', None)
            from validation import snapshot as validate_snapshot
            validate_snapshot(snapshot)
            return self.save(notebook, snapshot, summary, png)

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
            self.backup(notebook)
            # Write before advertising a successful save or activity event.
            atomic_write(self.path(notebook, 'board.json'), snapshot_bytes)
            # board.json is authoritative, including operation receipts. These
            # projections are repairable; a failure cannot uncommit the writing.
            try:
                atomic_write(self.path(notebook, 'summary.json'), summary_bytes)
                if png:
                    atomic_write(self.path(notebook, 'board.png'), image)
                for frame, fingerprint in ink.items():
                    if fingerprint != previous.get(frame):
                        append_json(self.data / 'activity.jsonl', {**stamp(), 'notebook': notebook, 'frame': frame})
            except OSError:
                logging.getLogger(__name__).warning('Notebook saved; summary/preview/activity projection needs repair.')
            self.last_ink[notebook] = ink
            return self.revision(notebook)

    # --- Rolling backups and restore -------------------------------------

    def backup_dir(self, notebook):
        self.path(notebook, 'board.json')  # validates the ID
        return self.data / 'backups' / 'notebooks' / notebook

    def backups(self, notebook):
        """Backup file names for one notebook, oldest first."""
        return sorted(p.name for p in self.backup_dir(notebook).glob('board-*.json'))

    def backup(self, notebook, force=False):
        """Keep the file that a save is about to replace. A hard link costs no
        copying: atomic_write puts the new version in a new file, so the old
        contents stay reachable under the backup name."""
        source = self.path(notebook, 'board.json')
        if not source.exists():
            return None
        now = datetime.now(UTC)
        names = self.backups(notebook)
        if names and not force:
            try:
                newest = datetime.strptime(names[-1], f'board-{BACKUP_STAMP}.json').replace(tzinfo=UTC)
                if (now - newest).total_seconds() < BACKUP_INTERVAL_SECONDS:
                    return None
            except ValueError:
                pass
        folder = self.backup_dir(notebook)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f'board-{now.strftime(BACKUP_STAMP)}.json'
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)  # filesystems without hard links
        self.prune_backups(notebook, now)
        return target.name

    def prune_backups(self, notebook, now):
        names = self.backups(notebook)
        keep = set(names[-BACKUPS_RECENT:])
        cutoff = (now - timedelta(days=BACKUP_DAYS)).strftime(BACKUP_STAMP)
        last_of_day = {}
        for name in names:
            stamp_text = name[len('board-'):-len('.json')]
            if stamp_text >= cutoff:
                last_of_day[stamp_text[:8]] = name  # names are sorted, so the last one wins
        keep.update(last_of_day.values())
        for name in names:
            if name not in keep:
                (self.backup_dir(notebook) / name).unlink(missing_ok=True)

    def restore(self, notebook, name):
        """Replace a notebook with one of its backups. The current file is
        backed up first (or set aside if damaged), so a restore can be undone."""
        with LOCK:
            self.require(notebook)
            source = self.backup_dir(notebook) / name
            if name not in self.backups(notebook):
                raise ClientError('Backup not found.')
            snapshot = load_json(source, 'That backup')
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('store'), dict):
                raise ClientError('That backup is not a notebook file.')
            from validation import snapshot as validate_snapshot
            validate_snapshot(snapshot)
            # Saving the current file below can prune the selected old backup.
            # Read it now so retention cannot interrupt a valid restore.
            restored_bytes = source.read_bytes()
            # Restoring content must not resurrect a previously executed retry.
            live = read_json(self.path(notebook, 'board.json'), {})
            if isinstance(live, dict) and isinstance(live.get('_operations'), dict) and live['_operations']:
                operations = copy.deepcopy(live['_operations'])
                for operation in operations.values():
                    operation['status'] = 'reverted_by_restore'
                    operation['result']['status'] = 'reverted_by_restore'
                snapshot['_operations'] = operations
                restored_bytes = dumps(snapshot).encode()
            current = self.path(notebook, 'board.json')
            try:
                self.read(notebook)
                kept = self.backup(notebook, force=True)
            except DamagedFileError:
                folder = self.backup_dir(notebook)
                kept = f'damaged-{datetime.now(UTC).strftime(BACKUP_STAMP)}.json'
                shutil.copy2(current, folder / kept)
            atomic_write(current, restored_bytes)
            self.last_ink.pop(notebook, None)
            return kept

    # --- Export and import -----------------------------------------------

    def export_notebook(self, notebook):
        with LOCK:
            item = next(b for b in self.metadata()['notebooks'] if b['id'] == self.require(notebook))
            snapshot = self.read(notebook)
            if not snapshot:
                raise ClientError('Notebook unavailable')
            return {'format': EXPORT_FORMAT, 'version': EXPORT_VERSION,
                    'notebook': {'id': item['id'], 'name': item['name'], 'kind': item.get('kind', 'notebook')},
                    'snapshot': snapshot}

    def inspect_export(self, document):
        """Validate an exported notebook and describe it without writing anything."""
        if not isinstance(document, dict) or document.get('format') != EXPORT_FORMAT:
            raise ClientError('This is not a GPT Board notebook export.')
        if document.get('version') != EXPORT_VERSION:
            raise ClientError('This export uses a format version this GPT Board does not understand.')
        info, snapshot = document.get('notebook'), document.get('snapshot')
        if (not isinstance(info, dict) or not isinstance(info.get('name'), str) or not info['name'].strip()
                or info.get('kind', 'notebook') not in ('notebook', 'whiteboard')):
            raise ClientError('The export has no valid notebook name or kind.')
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('store'), dict) or not snapshot.get('schema'):
            raise ClientError('The export has no notebook contents.')
        from validation import snapshot as validate_snapshot
        validate_snapshot(snapshot)
        store = snapshot['store']
        if len(dumps(snapshot).encode('utf-8')) > MAX_BOARD_BYTES:
            raise BoardSizeError('The export exceeds the 48 MiB notebook limit.')
        for key, record in store.items():
            if not isinstance(record, dict) or record.get('id') != key or not isinstance(record.get('typeName'), str):
                raise ClientError('The export contains a malformed record.')
        shapes = [r for r in store.values() if r['typeName'] == 'shape']
        for shape in shapes:
            check_shape(shape)
        check_parents(shapes, store)
        if not any(r['typeName'] == 'page' for r in store.values()):
            raise ClientError('The export has no page record.')
        return {'name': info['name'].strip()[:80], 'kind': info.get('kind', 'notebook'),
                'pages': sum(s.get('type') == 'frame' for s in shapes),
                'strokes': sum(s.get('type') in ('draw', 'highlight') for s in shapes),
                'shapes': len(shapes)}

    def import_notebook(self, document):
        """Add an exported notebook as a new notebook. An existing ID is never
        reused or overwritten: the import gets its own ID from its name."""
        preview = self.inspect_export(document)
        with LOCK:
            meta = self.metadata()
            notebook = self.new_id(preview['name'], meta)
            imported = copy.deepcopy(document['snapshot'])
            imported.pop('_operations', None)
            imported.pop('_imports', None)
            for record in imported['store'].values():
                if record.get('typeName') == 'shape':
                    record.setdefault('meta', {}).pop('ai', None)
            write_json(self.path(notebook, 'board.json'), imported)
            item = {'id': notebook, 'name': preview['name'], 'kind': preview['kind']}
            meta['notebooks'].append(item)
            write_json(self.data / 'notebooks.json', meta)
            return item

    def manage(self, notebook, action, name=None):
        """Only reversible metadata changes; notebook bytes and IDs stay put."""
        with LOCK:
            self.require(notebook)
            meta = self.metadata()
            item = next(b for b in meta['notebooks'] if b['id'] == notebook)
            if action == 'rename':
                if not isinstance(name, str) or not name.strip() or len(name) > 80:
                    raise ClientError('Enter a name of 1–80 characters.')
                item['name'] = name.strip()
            elif action in ('up', 'down'):
                positions = [i for i, b in enumerate(meta['notebooks']) if b.get('kind', 'notebook') == item.get('kind', 'notebook')]
                position = positions.index(meta['notebooks'].index(item))
                destination = position + (-1 if action == 'up' else 1)
                if 0 <= destination < len(positions):
                    a, b = positions[position], positions[destination]
                    meta['notebooks'][a], meta['notebooks'][b] = meta['notebooks'][b], meta['notebooks'][a]
            elif action in ('archive', 'trash', 'restore'):
                item['status'] = {'archive': 'archived', 'trash': 'trash', 'restore': 'active'}[action]
            else:
                raise ClientError('Unsupported notebook action.')
            atomic_write(self.data / 'backups/notebooks-index-before.json', (self.data / 'notebooks.json').read_bytes())
            write_json(self.data / 'notebooks.json', meta)
            return item

    def recover_index(self, dry_run=True):
        """Explicit recovery; never hide orphaned folders or overwrite damage."""
        from validation import snapshot as validate_snapshot
        found, damaged = [], []
        paths = [self.data / 'board.json', *sorted((self.data / 'notebooks').glob('*/board.json'))]
        old = read_json(self.data / 'notebooks.json', {})
        known = {b['id']: b for b in old.get('notebooks', []) if isinstance(b, dict) and isinstance(b.get('id'), str)} if isinstance(old, dict) else {}
        for path in paths:
            if not path.exists():
                continue
            notebook = 'default' if path.parent == self.data else path.parent.name
            try:
                self.path(notebook, 'board.json')
                value = load_json(path, 'Recovered notebook')
                validate_snapshot(value)
                page = next(r for r in value['store'].values() if r.get('typeName') == 'page')
                item = known.get(notebook, {'id': notebook, 'name': page.get('name') or notebook,
                    'kind': 'notebook' if any(r.get('type') == 'frame' for r in value['store'].values()) else 'whiteboard'})
                found.append(item)
            except (ClientError, DamagedFileError, StopIteration):
                damaged.append(notebook)
        result = {'notebooks': found, 'damaged': damaged, 'dry_run': dry_run}
        if not dry_run:
            if damaged or not found:
                raise ClientError('Recovery found damaged notebooks or no valid notebooks. Repair from backups first; no index was replaced.')
            index = self.data / 'notebooks.json'
            if index.exists():
                atomic_write(self.data / 'backups' / ('index-before-recovery-' + datetime.now(UTC).strftime(BACKUP_STAMP) + '.json'), index.read_bytes())
            write_json(index, {'notebooks': found})
        return result
