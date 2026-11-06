"""Archived board evidence and the canonical Math Learning System CLI bridge."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import uuid
from board_storage import ROOT, LOCK, dumps, atomic_write, append_json, stamp

REVIEW_LOCK = threading.Lock()


class ReviewService:
    def __init__(self, storage):
        self.storage = storage
        self.courses = json.loads(os.environ['MLS_COURSES_JSON']) if os.environ.get('MLS_COURSES_JSON') else {
            'Calc 3': str(Path.home() / 'Downloads/Courses/Calc 3/tutor/mls')}
        self.controller = os.environ.get('MLS_CONTROLLER', str(ROOT.parent / 'Math Learning System/mls.py'))
        schema = json.loads((ROOT / 'mcp_schema.json').read_text())
        self.instructions = schema['instructions']
        self.tools = schema['tools']
        review = next(t for t in self.tools if t['name'] == 'record_review')
        review['description'] = review['description'].split('Configured courses: ')[0] + 'Configured courses: ' + ', '.join(self.courses)
        review['inputSchema']['properties']['course']['enum'] = list(self.courses)
        self.error_fields = review['inputSchema']['properties']['errors']['items']['required']

    def archive(self, frame, png, notebook, region=None):
        when = stamp()
        check_id = 'check-' + str(uuid.uuid4())
        name = re.sub(r'[^\w.-]+', '_', frame, flags=re.ASCII) + '-' + re.sub(r'[:.]', '', when['ts'][11:23]) + '-' + check_id[-8:] + '.png'
        path = self.storage.data / 'checks' / when['ts'][:10] / name
        image = base64.b64decode(png.split(',', 1)[1])
        row = {**when, 'check_id': check_id, 'notebook': notebook, 'frame': frame,
               'file': str(path), 'sha256': hashlib.sha256(image).hexdigest()}
        if region:
            row['region'] = region
        with LOCK:
            atomic_write(path, image)
            append_json(self.storage.data / 'checks/index.jsonl', row)
        return row

    def payload(self, args):
        if not isinstance(args, dict):
            raise ValueError('Provide a structured review.')
        def nonempty(value):
            return isinstance(value, str) and value.strip() and len(value) <= 12000
        for key in ('check_id', 'course', 'section', 'page', 'problem', 'original_response'):
            if not nonempty(args.get(key)):
                raise ValueError(f'Provide a nonempty {key} (at most 12000 characters).')
        if not isinstance(self.courses.get(args['course']), str):
            raise ValueError('Course is not configured for board reviews.')
        if args.get('result') not in ('right', 'wrong', 'partial', 'unresolved'):
            raise ValueError('Invalid review result.')
        if args.get('read_confidence') not in ('low', 'medium', 'high'):
            raise ValueError('Invalid read_confidence.')
        if args.get('assistance') not in ('none', 'helped', 'unknown'):
            raise ValueError('Invalid assistance value.')
        errors = args.get('errors')
        if not isinstance(errors, list) or len(errors) > 50:
            raise ValueError('Provide an errors array with at most 50 entries.')
        if (args['result'] in ('right', 'unresolved')) == bool(errors):
            raise ValueError('Right/unresolved reviews require no errors; wrong/partial reviews require confirmed errors.')
        if args['read_confidence'] == 'low' and args['result'] != 'unresolved':
            raise ValueError('Unclear handwriting must be unresolved, without attributed student errors.')
        ids = set()
        for error in errors:
            if not isinstance(error, dict):
                raise ValueError('Each error must be an object.')
            for key in self.error_fields:
                if not nonempty(error.get(key)):
                    raise ValueError(f'Each error requires {key}.')
            if error['type'] not in ('WORD', 'TOOL', 'ALGEBRA', 'COPY') or error['confidence'] not in ('low', 'medium', 'high'):
                raise ValueError('Invalid error type or confidence.')
            if error['error_id'] in ids:
                raise ValueError('error_id must be unique within the review.')
            ids.add(error['error_id'])
        if not re.fullmatch(r'check-[a-f0-9-]{36}', args['check_id']):
            raise ValueError('Use a check_id returned by an exact-frame read_board call.')
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
            raise ValueError('Archived check image not found. Read the exact frame again before recording this review.')
        identity = [args[k] for k in ('check_id', 'course', 'section', 'page', 'problem')]
        result = {key: args[key] for key in ('course', 'section', 'page', 'problem', 'original_response', 'result', 'read_confidence', 'assistance', 'errors')}
        result['review_id'] = 'br-' + hashlib.sha256(dumps(identity).encode()).hexdigest()[:32]
        result['evidence'] = {'check_id': check['check_id'], 'notebook': check['notebook'], 'frame': check['frame'], 'image': check['file'], 'captured_at': check['ts']}
        for key in ('resolves', 'supersedes'):
            if key in args:
                result[key] = args[key]
        if len(dumps(result).encode()) > 256 * 1024:
            raise ValueError('Review is too large; record one problem at a time.')
        return result

    def record(self, args):
        payload = self.payload(args)
        command = [os.environ.get('MLS_PYTHON', 'python3'), self.controller, '--dir', self.courses[payload['course']], 'board-review', '--file', '-']
        with REVIEW_LOCK, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                child = subprocess.run(command, input=dumps(payload).encode(), stdout=out, stderr=err, timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('Review controller timed out. Logging is not confirmed; retry the same review.') from exc
            except OSError as exc:
                raise ValueError('Review controller could not start: ' + str(exc)) from exc
            if out.tell() > 1024 * 1024 or err.tell() > 1024 * 1024:
                raise ValueError('Review controller output exceeded its limit.')
            out.seek(0); err.seek(0)
            stdout, stderr = out.read().decode(), err.read().decode()
        if child.returncode:
            raise ValueError('Review was not confirmed saved: ' + (stderr.strip() or stdout.strip() or f'controller exited {child.returncode}'))
        try:
            receipt = json.loads(stdout)
            if receipt['status'] not in ('saved', 'already_saved') or receipt['review_id'] != payload['review_id']:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('Review controller returned no valid save confirmation. Retry the same review rather than claiming success.') from exc
        return receipt
