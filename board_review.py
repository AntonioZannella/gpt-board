"""Archived board evidence and the canonical Math Learning System CLI bridge.

Review logging is optional. It is on only when a controller and at least one
course are configured, in data/config.json or the MLS_* environment variables:

    {"review": {"controller": "/path/to/mls.py",
                "python": "python3",
                "courses": {"Course label": "/path/to/that/course/store"}}}

Without that, drawing and the AI drawing tools work and record_review is not offered.
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from board_storage import (
    LOCK,
    ROOT,
    ClientError,
    append_json,
    atomic_write,
    decode_png,
    dumps,
    read_json,
    stamp,
)

REVIEW_LOCK = threading.Lock()
log = logging.getLogger('gpt-board.review')


class ReviewService:
    def __init__(self, storage):
        self.storage = storage
        config = (read_json(storage.data / 'config.json') or {}).get('review') or {}
        courses = json.loads(os.environ['MLS_COURSES_JSON']) if os.environ.get('MLS_COURSES_JSON') else config.get('courses')
        self.courses = courses if isinstance(courses, dict) else {}
        limit = config.get('max_evidence_bytes', 512 * 1024 * 1024)
        self.max_evidence_bytes = limit if isinstance(limit, int) and limit > 0 else 512 * 1024 * 1024
        self.controller = os.environ.get('MLS_CONTROLLER') or config.get('controller')
        self.python = os.environ.get('MLS_PYTHON') or config.get('python') or 'python3'
        self.enabled = bool(self.courses and self.controller)
        schema = json.loads((ROOT / 'mcp_schema.json').read_text())
        review = next(t for t in schema['tools'] if t['name'] == 'record_review')
        self.error_fields = review['inputSchema']['properties']['errors']['items']['required']
        if self.enabled:
            self.instructions = schema['instructions']
            self.tools = schema['tools']
            review['description'] = review['description'].split('Configured courses: ')[0] + 'Configured courses: ' + ', '.join(self.courses)
            review['inputSchema']['properties']['course']['enum'] = list(self.courses)
        else:
            # No learning system configured: offer the board tools only.
            self.instructions = ''
            self.tools = [t for t in schema['tools'] if t is not review]
            for tool in self.tools:
                tool['description'] = tool['description'].split(' When checking answers')[0]

    def archive(self, frame, png, notebook, region=None, frame_id=None, revision=None):
        when = stamp()
        check_id = 'check-' + str(uuid.uuid4())
        name = re.sub(r'[^\w.-]+', '_', frame, flags=re.ASCII) + '-' + re.sub(r'[:.]', '', when['ts'][11:23]) + '-' + check_id[-8:] + '.png'
        path = self.storage.data / 'checks' / when['ts'][:10] / name
        image = decode_png(png)
        row = {**when, 'check_id': check_id, 'notebook': notebook, 'frame': frame,
               'file': str(path), 'sha256': hashlib.sha256(image).hexdigest()}
        if region:
            row['region'] = region
        if frame_id:
            row['frame_id'] = frame_id
        if revision:
            row['revision'] = revision
        with LOCK:
            used = sum(p.stat().st_size for p in (self.storage.data / 'checks').rglob('*.png'))
            if used + len(image) > self.max_evidence_bytes:
                raise ClientError('Review image storage limit reached. Preserve/export the archive and deliberately increase review.max_evidence_bytes; no history was deleted.')
            atomic_write(path, image)
            append_json(self.storage.data / 'checks/index.jsonl', row)
        return row

    def payload(self, args):
        if not self.enabled:
            raise ClientError('Review logging is not configured on this board.')
        if not isinstance(args, dict):
            raise ClientError('Provide a structured review.')
        def nonempty(value):
            return isinstance(value, str) and value.strip() and len(value) <= 12000
        for key in ('check_id', 'course', 'section', 'page', 'problem', 'original_response'):
            if not nonempty(args.get(key)):
                raise ClientError(f'Provide a nonempty {key} (at most 12000 characters).')
        if not isinstance(self.courses.get(args['course']), str):
            raise ClientError('Course is not configured for board reviews.')
        if args.get('result') not in ('right', 'wrong', 'partial', 'unresolved'):
            raise ClientError('Invalid review result.')
        if args.get('read_confidence') not in ('low', 'medium', 'high'):
            raise ClientError('Invalid read_confidence.')
        if args.get('assistance') not in ('none', 'helped', 'unknown'):
            raise ClientError('Invalid assistance value.')
        errors = args.get('errors')
        if not isinstance(errors, list) or len(errors) > 50:
            raise ClientError('Provide an errors array with at most 50 entries.')
        if (args['result'] in ('right', 'unresolved')) == bool(errors):
            raise ClientError('Right/unresolved reviews require no errors; wrong/partial reviews require confirmed errors.')
        if args['read_confidence'] == 'low' and args['result'] != 'unresolved':
            raise ClientError('Unclear handwriting must be unresolved, without attributed student errors.')
        ids = set()
        for error in errors:
            if not isinstance(error, dict):
                raise ClientError('Each error must be an object.')
            for key in self.error_fields:
                if not nonempty(error.get(key)):
                    raise ClientError(f'Each error requires {key}.')
            if error['type'] not in ('WORD', 'TOOL', 'ALGEBRA', 'COPY') or error['confidence'] not in ('low', 'medium', 'high'):
                raise ClientError('Invalid error type or confidence.')
            if error['error_id'] in ids:
                raise ClientError('error_id must be unique within the review.')
            ids.add(error['error_id'])
        if not re.fullmatch(r'check-[a-f0-9-]{36}', args['check_id']):
            raise ClientError('Use a check_id returned by an exact-frame read_board call.')
        index = self.storage.data / 'checks/index.jsonl'
        check = None
        with LOCK:
            if index.exists():
                with index.open() as stream:
                    for line in stream:
                        if line.strip():
                            row = json.loads(line)
                            if row.get('check_id') == args['check_id']:
                                check = row
                                break
        if not check or not Path(check['file']).is_file():
            raise ClientError('Archived check image not found. Read the exact frame again before recording this review.')
        # The review must point at the very image that was read.
        if check.get('sha256') and hashlib.sha256(Path(check['file']).read_bytes()).hexdigest() != check['sha256']:
            raise ClientError('The archived check image no longer matches its recorded fingerprint. Read the exact frame again before recording this review.')
        identity = [args[k] for k in ('check_id', 'course', 'section', 'page', 'problem')]
        result = {key: args[key] for key in ('course', 'section', 'page', 'problem', 'original_response', 'result', 'read_confidence', 'assistance', 'errors')}
        result['review_id'] = 'br-' + hashlib.sha256(dumps(identity).encode()).hexdigest()[:32]
        result['evidence'] = {'check_id': check['check_id'], 'notebook': check['notebook'], 'frame': check['frame'], 'image': check['file'], 'captured_at': check['ts']}
        for key in ('resolves', 'supersedes'):
            if key in args:
                result[key] = args[key]
        if 'supersedes' in result:
            link = result['supersedes']
            if not isinstance(link, dict) or set(link) != {'review_id', 'reason'} or not all(nonempty(v) for v in link.values()):
                raise ClientError('supersedes requires a review_id and a reason.')
            if link['review_id'] == result['review_id']:
                raise ClientError('A correction requires a fresh read and a new review ID.')
        if 'resolves' in result:
            links = result['resolves']
            if result['result'] != 'right' or not isinstance(links, list) or not 1 <= len(links) <= 50:
                raise ClientError('Only a right review may resolve prior errors (1–50 links).')
            for link in links:
                if not isinstance(link, dict) or set(link) != {'review_id', 'error_id', 'reason'} or not all(nonempty(v) for v in link.values()):
                    raise ClientError('Each resolves link requires review_id, error_id and reason.')
        if len(dumps(result).encode()) > 256 * 1024:
            raise ClientError('Review is too large; record one problem at a time.')
        return result

    def record(self, args):
        payload = self.payload(args)
        command = [self.python, self.controller, '--dir', self.courses[payload['course']], 'board-review', '--file', '-']
        with REVIEW_LOCK, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                child = subprocess.run(command, input=dumps(payload).encode(), stdout=out, stderr=err, timeout=30, check=False)
            except subprocess.TimeoutExpired as exc:
                raise ClientError('Review controller timed out. Logging is not confirmed; retry the same review.') from exc
            except OSError as exc:
                log.exception('Review controller could not start')
                raise ClientError('Review controller could not start. Check MLS_CONTROLLER and MLS_PYTHON.') from exc
            if out.tell() > 1024 * 1024 or err.tell() > 1024 * 1024:
                raise ClientError('Review controller output exceeded its limit.')
            out.seek(0); err.seek(0)
            stdout, stderr = out.read().decode(), err.read().decode()
        if child.returncode:
            # The controller's own refusal ("Controller busy...", "superseded by...")
            # tells the AI what to do next; only its last line goes back, capped.
            log.warning('Review controller exited %s; private output omitted.', child.returncode)
            output = (stderr or stdout).lower()
            if 'busy' in output:
                detail = 'Controller busy: retry the identical review.'
            elif 'superseded' in output:
                detail = 'The linked review was superseded. Inspect canonical history and use its current review.'
            elif 'conflict' in output or 'different payload' in output:
                detail = 'Review identity conflicts with saved content. Use fresh evidence and supersedes for a correction.'
            else:
                detail = 'Controller refused or could not persist this review. Check the initialized course/error-log configuration and retry the identical review.'
            raise ClientError('Review was not confirmed saved: ' + detail)
        try:
            receipt = json.loads(stdout)
            if receipt['status'] not in ('saved', 'already_saved') or receipt['review_id'] != payload['review_id']:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise ClientError('Review controller returned no valid save confirmation. Retry the same review rather than claiming success.') from exc
        return receipt

    def history(self, course, offset=0):
        """Read canonical records directly, without running state/export commands
        that can rebuild mastery projections. Never writes a course or log."""
        if course not in self.courses:
            raise ClientError('Choose a configured course.')
        if not isinstance(offset, int) or offset < 0:
            raise ClientError('Invalid history offset.')
        rows = []
        folder = Path(self.courses[course])
        config = read_json(folder / 'course.json')
        if not isinstance(config, dict) or config.get('course') != course:
            raise ClientError('The course mapping does not point to a matching initialized course.')
        path = folder / 'attempts.jsonl'
        if path.exists():
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    if len(line) > 512 * 1024:
                        raise ClientError('A history record is oversized. Inspect it through the canonical learning system.')
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        raise ClientError('Canonical history has an unreadable record. It was left untouched.') from None
                    if row.get('kind') == 'board_review':
                        # Evidence paths and unrelated session records are not
                        # exposed by this board-only view.
                        row = {k: v for k, v in row.items() if k != 'evidence'}
                        rows.append(row)
        return {'course': course, 'total': len(rows), 'offset': offset,
                'reviews': list(reversed(rows))[offset:offset + 100],
                'notice': 'Historical checks and correction links, not current work or mastery. Due practice stays in the canonical study workflow.'}
