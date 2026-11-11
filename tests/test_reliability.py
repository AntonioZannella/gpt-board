"""Regression tests for the November 2026 readiness review.

Each test reproduces a defect that review confirmed, or pins a rule added to
fix one. Everything runs in temporary directories with synthetic data.
"""
import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

import board_storage
import pairing
from board_review import ReviewService
from board_storage import (
    BACKUP_STAMP,
    BoardStorage,
    ClientError,
    DamagedFileError,
    StaleTabError,
    write_json,
)
from server import BoardApp, Handler, launch_options

PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII='


def shape(name, kind='draw', **extra):
    return {'id': 'shape:' + name, 'typeName': 'shape', 'type': kind, 'parentId': 'page:page',
            'x': 0, 'y': 0, 'props': {}, **extra}


class Fixture(unittest.TestCase):
    """A board server on a free port over an empty data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = BoardApp(self.root)
        self.storage = self.app.storage
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server.app = self.app
        self.app.port = self.server.server_port
        self.host = f'127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True).start()
        self.streams = {}
        self.storage.metadata()  # what the first request of a new installation does

    def tearDown(self):
        for conn, response in self.streams.values():
            response.close()
            conn.close()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def http(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        sent = {'Content-Type': 'application/json'} if body is not None else {}
        sent.update(headers or {})
        conn.request(method, path, json.dumps(body).encode() if body is not None else None, sent)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        try:
            return response.status, json.loads(data), response
        except ValueError:
            return response.status, data, response

    def save(self, tab, put=(), remove=(), base=None, notebook='default'):
        body = {'tab': tab, 'notebook': notebook, 'changes': {'put': list(put), 'remove': list(remove)},
                'summary': {'shapeCount': 0}}
        if base is not None:
            body['base'] = base
        return self.http('POST', '/api/board', body)[:2]

    def open_tab(self, tab, notebook='default'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        conn.request('GET', f'/api/events?tab={tab}&notebook={notebook}')
        response = conn.getresponse()
        self.assertEqual(response.readline(), b': connected\n')
        response.readline()
        self.streams[tab] = (conn, response)
        return response

    def close_tab(self, tab):
        conn, response = self.streams.pop(tab)
        response.close()
        conn.close()
        deadline = time.monotonic() + 3
        while tab in self.app.tabs and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertNotIn(tab, self.app.tabs)

    def answer(self, stream, reply):
        """Play a board tab: read one operation from its stream and acknowledge it."""
        message = json.loads(stream.readline()[6:])
        stream.readline()
        status, body, _ = self.http('POST', '/api/ack', {'opId': message['opId'], **reply})
        self.assertEqual(status, 200)
        return message, body

    def tool(self, name, arguments, stream=None, reply=None):
        """Call an MCP tool; if a stream is given, answer the operation as that tab."""
        result = {}

        def call():
            request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}
            result.update(self.http('POST', self.app.mcp_path, request)[1]['result'])
        thread = threading.Thread(target=call)
        thread.start()
        message = self.answer(stream, reply or {'ok': True, 'ids': []})[0] if stream else None
        thread.join(15)
        return result, message


class FreshInstallTests(Fixture):
    def test_first_notebook_exists_and_first_creation_works(self):
        # Review finding: the list named a default notebook that had no file,
        # so creating the first notebook failed.
        listed = self.http('GET', '/api/notebooks')[1]['notebooks']
        self.assertEqual([b['id'] for b in listed], ['default'])
        status, board, response = self.http('GET', '/api/board')
        self.assertEqual(status, 200)
        self.assertTrue(any(r.get('type') == 'frame' for r in board['store'].values()), 'the starter has a page')
        self.assertEqual(self.http('POST', '/api/notebooks', {'action': 'create', 'name': 'First'})[0], 201)
        self.assertEqual(self.http('POST', '/api/notebooks', {'action': 'create', 'name': 'Ideas', 'kind': 'whiteboard'})[0], 201)
        # Write, then reopen with a fresh storage object, as after a restart.
        rev = response.getheader('X-Board-Rev')
        self.assertEqual(self.save('t', [shape('first-stroke', parentId='shape:welcome-page')], base=rev)[0], 200)
        self.assertIn('shape:first-stroke', BoardStorage(self.root).read('default')['store'])

    def test_clean_copy_builds_starts_saves_and_reloads(self):
        # A copy of the source alone: no data/, no dist/, no personal files.
        with tempfile.TemporaryDirectory() as clean:
            clean = Path(clean)
            for path in list(SOURCE.glob('*.py')) + [SOURCE / 'mcp_schema.json']:
                shutil.copy(path, clean / path.name)
            shutil.copytree(SOURCE / 'src', clean / 'src')
            subprocess.run([sys.executable, 'build.py'], cwd=clean, check=True, capture_output=True)
            port = self.server.server_port + 1 if self.server.server_port < 65000 else 20000
            child = subprocess.Popen([sys.executable, 'server.py', '--port', str(port)], cwd=clean,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                child.stdout.readline()  # the startup line
                def fetch(method, path, body=None):
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                    conn.request(method, path, json.dumps(body).encode() if body else None,
                                 {'Content-Type': 'application/json'} if body else {})
                    response = conn.getresponse()
                    data = response.read()
                    conn.close()
                    return response, data
                self.assertIn(b'/native/app.js', fetch('GET', '/')[1])
                for asset in ('app.js', 'model.js', 'sync.js', 'style.css'):
                    self.assertEqual(fetch('GET', '/native/' + asset)[0].status, 200)
                self.assertEqual(json.loads(fetch('GET', '/api/health')[1])['review'], False,
                                 'review logging is off until configured; drawing must not need it')
                response, _ = fetch('GET', '/api/board')
                body = {'tab': 't', 'base': response.getheader('X-Board-Rev'), 'summary': {'shapeCount': 1},
                        'changes': {'put': [shape('hello', parentId='shape:welcome-page')], 'remove': []}}
                self.assertEqual(fetch('POST', '/api/board', body)[0].status, 200)
                self.assertIn('shape:hello', json.loads(fetch('GET', '/api/board')[1])['store'])
                mcp = '/mcp/' + (clean / 'data/token').read_text().strip()
                tools = json.loads(fetch('POST', mcp, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})[1])['result']['tools']
                self.assertNotIn('record_review', [t['name'] for t in tools])
            finally:
                child.terminate()
                child.wait(5)
                child.stdout.close()


class OriginTests(Fixture):
    def test_other_websites_cannot_read_or_write(self):
        # Review finding: a POST with Origin: https://untrusted.example created a notebook.
        create = {'action': 'create', 'name': 'Injected'}
        evil = {'Origin': 'https://untrusted.example'}
        self.assertEqual(self.http('POST', '/api/notebooks', create, evil)[0], 403)
        self.assertEqual(self.http('GET', '/api/board', headers=evil)[0], 403)
        self.assertEqual(self.http('POST', self.app.mcp_path, {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, evil)[0], 403)
        # A form on another site can send text/plain without a preflight.
        self.assertEqual(self.http('POST', '/api/notebooks', create, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(len(self.storage.metadata()['notebooks']), 1, 'nothing was created')
        # The board's own page, and programs that send no Origin, still work.
        self.assertEqual(self.http('POST', '/api/notebooks', create, {'Origin': 'http://' + self.host})[0], 201)
        self.assertEqual(self.http('POST', self.app.mcp_path, {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})[0], 200)

    def test_lan_mode_is_gone_and_json_must_be_json(self):
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            launch_options(['--lan'])
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        conn.request('POST', '/api/board', b'{"tab":"t","summary":{"shapeCount":NaN}}', {'Content-Type': 'application/json'})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()


class ConflictTests(Fixture):
    def test_stale_copy_cannot_overwrite_another_devices_change(self):
        # Review finding: device A moved a shape to x=50; device B, still
        # holding x=0, changed its text; the saved x went back to 0.
        rev0 = self.storage.revision('default')
        status, body = self.save('a', [shape('note', 'text', props={'text': 'old'})], base=rev0)
        rev1 = body['rev']
        status, body = self.save('a', [shape('note', 'text', x=50, props={'text': 'old'})], base=rev1)
        self.assertEqual(status, 200)
        status, body = self.save('b', [shape('note', 'text', x=0, props={'text': 'new'})], base=rev1)
        self.assertEqual(status, 409)
        self.assertTrue(body['conflict'])
        self.assertEqual(self.storage.read('default')['store']['shape:note']['x'], 50, 'the saved move survives')
        # The refusal carries what the tab needs to merge and retry.
        self.assertEqual(body['rev'], self.storage.revision('default'))
        self.assertEqual(next(s for s in body['shapes'] if s['id'] == 'shape:note')['x'], 50)
        status, _ = self.save('b', [shape('other')], base=body['rev'])
        self.assertEqual(status, 200)

    def test_tabs_without_a_revision_may_only_add(self):
        rev = self.storage.revision('default')
        self.save('a', [shape('mine')], base=rev)
        self.assertEqual(self.save('old-tab', [shape('new-stroke')])[0], 200)
        self.assertEqual(self.save('old-tab', [shape('mine', x=9)])[0], 409)
        self.assertEqual(self.save('old-tab', remove=['shape:mine'])[0], 409)
        self.assertEqual(self.storage.read('default')['store']['shape:mine']['x'], 0)
        with self.assertRaises(StaleTabError):
            self.storage.save_snapshot('default', board_storage.empty_snapshot('x'), {'shapeCount': 0}, base='wrong')

    def test_other_tabs_are_told_and_can_fetch_the_shapes(self):
        other = self.open_tab('other')
        self.open_tab('writer')
        status, body = self.save('writer', [shape('s1')], base=self.storage.revision('default'))
        self.assertEqual(other.readline(), b'event: changed\n')  # a named event: older tabs ignore it
        self.assertEqual(json.loads(other.readline()[6:]), {'rev': body['rev']})
        status, latest, _ = self.http('GET', '/api/board?shapes=1')
        self.assertEqual(status, 200)
        self.assertEqual(latest['rev'], body['rev'])
        self.assertIn('shape:s1', [s['id'] for s in latest['shapes']])
        self.assertTrue(all(s['typeName'] == 'shape' for s in latest['shapes']))

    def test_malformed_shapes_are_refused_and_nothing_is_written(self):
        rev = self.storage.revision('default')
        before = self.storage.path('default', 'board.json').read_bytes()
        bad = [shape('a', x='left'), shape('a', type=''), shape('a', parentId='shape:missing'),
               shape('a', parentId='shape:a'), {**shape('a'), 'id': 'shape:' + 'x' * 200}]
        for item in bad:
            self.assertEqual(self.save('t', [item], base=rev)[0], 400, item)
        loop = [shape('p', 'geo', parentId='shape:q'), shape('q', 'geo', parentId='shape:p')]
        self.assertEqual(self.save('t', loop, base=rev)[0], 400)
        self.assertEqual(self.storage.path('default', 'board.json').read_bytes(), before)


class AITargetTests(Fixture):
    def setUp(self):
        super().setUp()
        self.storage.create('Physics')

    def test_requests_never_fall_back_to_a_different_notebook(self):
        # Review finding: when the active tab disconnected, the request went
        # to whichever tab was left, possibly another notebook.
        calc = self.open_tab('calc-tab')
        self.open_tab('physics-tab', 'physics')
        self.save('physics-tab', [shape('p1')], base=self.storage.revision('physics'), notebook='physics')
        self.close_tab('physics-tab')  # the last-used notebook is now closed
        result, _ = self.tool('read_board', {})
        self.assertTrue(result['isError'])
        self.assertIn('physics', result['content'][0]['text'])
        self.assertEqual(self.app.tabs['calc-tab']['queue'].qsize(), 0)
        result, _ = self.tool('read_board', {'notebook': 'default'}, calc)
        self.assertEqual(json.loads(result['content'][0]['text'])['target']['notebook'], 'default')
        # A replacement tab on the SAME notebook may receive implicit requests.
        physics = self.open_tab('physics-2', 'physics')
        self.save('calc-tab', [shape('c1')], base=self.storage.revision('default'))
        self.close_tab('calc-tab')  # the tab used last
        calc_again = self.open_tab('calc-again')
        result, _ = self.tool('read_board', {}, calc_again)
        self.assertEqual(json.loads(result['content'][0]['text'])['target']['notebook'], 'default')
        # Naming the notebook routes there, and the receipt says so.
        result, _ = self.tool('read_board', {'notebook': 'physics'}, physics,
                                    {'ok': True, 'summary': {'frames': []}})
        self.assertEqual(json.loads(result['content'][-1]['text'])['target']['notebook'], 'physics')
        result, _ = self.tool('read_board', {'notebook': 'not-open'})
        self.assertIn('not open in any board tab', result['content'][0]['text'])

    def test_late_read_acknowledgement_is_reported_without_blocking_next_read(self):
        stream = self.open_tab('slow')
        with patch('server.TAB_REPLY_TIMEOUT_SECONDS', .2):
            outcome = {}
            def call():
                outcome.update(self.app.call_tool('read_board', {}))
            thread = threading.Thread(target=call)
            # send_to_tab reads its default timeout at definition time; pass through call_tool's real path
            with patch.object(self.app, 'send_to_tab', lambda op, args, timeout=.2, notebook=None:
                              BoardApp.send_to_tab(self.app, op, args, .2, notebook)):
                thread.start()
                message = json.loads(stream.readline()[6:])
                stream.readline()
                thread.join(5)
        self.assertTrue(outcome['isError'])
        self.assertIn('nothing was changed', outcome['content'][0]['text'])
        status, body, _ = self.http('POST', '/api/ack', {'opId': message['opId'], 'ok': True, 'ids': ['dup']})
        self.assertEqual(status, 200)
        self.assertEqual(body, {'ok': True, 'delivered': False})
        self.assertEqual(self.app.pending, {})

    def test_student_writing_is_protected_in_code(self):
        ink = shape('ink', parentId='shape:welcome-page')
        student_text = shape('student-text', 'text', parentId='shape:welcome-page')
        ai_note = shape('note', 'text', parentId='shape:welcome-page', meta={'ai': True})
        self.save('t', [ink, student_text, ai_note], base=self.storage.revision('default'))
        for index, short in enumerate(('ink', 'student-text')):
            args = {'notebook': 'default', 'frame': 'shape:welcome-page', 'operation_id': f'proposal-{index}',
                    'ids': [short], 'user_authorized': True}
            result = self.app.call_tool('delete_shapes', args)
            self.assertTrue(result['isError'])
            self.assertIn('approval_required', result['content'][0]['text'])
            self.assertIn('shape:' + short, self.storage.read('default')['store'])
        result = self.app.call_tool('delete_shapes', {'notebook': 'default', 'frame': 'welcome-page',
            'operation_id': 'remove-ai-note', 'ids': ['note']})
        self.assertFalse(result['isError'])
        self.assertNotIn('shape:note', self.storage.read('default')['store'])


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = BoardStorage(self.tmp.name)
        self.storage.metadata()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name):
        return self.storage.save_changes('default', {'put': [shape(name)], 'remove': []}, {'shapeCount': 1},
                                         self.storage.revision('default'))

    def test_saves_keep_restorable_backups(self):
        # Review finding: atomic writes protect one save, but an unwanted
        # deletion had no history to return to.
        self.put('keep-me')
        with patch('board_storage.BACKUP_INTERVAL_SECONDS', 0):
            self.storage.save_changes('default', {'put': [], 'remove': ['shape:keep-me']}, {'shapeCount': 0},
                                      self.storage.revision('default'))
        names = self.storage.backups('default')
        self.assertTrue(names)
        self.assertNotIn('shape:keep-me', self.storage.read('default')['store'])
        after_delete = self.storage.path('default', 'board.json').read_bytes()
        kept = self.storage.restore('default', names[-1])
        self.assertIn('shape:keep-me', self.storage.read('default')['store'])
        # Restoring is itself undoable: what it replaced was kept.
        self.assertEqual((self.storage.backup_dir('default') / kept).read_bytes(), after_delete)
        with self.assertRaises(ClientError):
            self.storage.restore('default', '../../token')

    def test_backups_are_spaced_and_pruned(self):
        self.put('a')
        self.put('b')
        self.put('c')
        self.assertLessEqual(len(self.storage.backups('default')), 1, 'at most one backup per interval')
        folder = self.storage.backup_dir('default')
        folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        old = [now - timedelta(days=40, hours=h) for h in range(3)]
        recent = [now - timedelta(days=2, hours=h) for h in range(1, 9)]
        for moment in old + recent:
            (folder / f'board-{moment.strftime(BACKUP_STAMP)}.json').write_text('{}')
        self.storage.prune_backups('default', now)
        left = self.storage.backups('default')
        self.assertFalse(any(name < f'board-{(now - timedelta(days=30)).strftime(BACKUP_STAMP)}' for name in left),
                         'backups past the retention window are removed')
        self.assertGreaterEqual(len(left), board_storage.BACKUPS_RECENT)
        self.assertLess(len(left), len(old) + len(recent))

    def test_restore_reads_backup_before_retention_prunes_it(self):
        self.put('original')
        original = self.storage.path('default', 'board.json').read_bytes()
        folder = self.storage.backup_dir('default')
        folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        # Five old backups survive as the newest five, until restore creates
        # a sixth. The selected oldest backup then falls outside retention.
        for name in self.storage.backups('default'):
            (folder / name).unlink()
        for offset in range(board_storage.BACKUPS_RECENT):
            moment = now - timedelta(days=40, hours=offset)
            (folder / f'board-{moment.strftime(BACKUP_STAMP)}.json').write_bytes(original)
        chosen = self.storage.backups('default')[0]
        self.storage.restore('default', chosen)
        self.assertEqual(self.storage.path('default', 'board.json').read_bytes(), original)


class DamageTests(Fixture):
    def test_damaged_files_are_reported_and_left_untouched(self):
        # Review finding: unreadable JSON was treated like a missing file and
        # quietly replaced with a fresh collection.
        self.save('t', [shape('precious')], base=self.storage.revision('default'))
        with patch('board_storage.BACKUP_INTERVAL_SECONDS', 0):
            self.save('t', [shape('later')], base=self.storage.revision('default'))
        index = self.root / 'data/notebooks.json'
        good_index = index.read_bytes()
        index.write_text('{"notebooks": [{"id": "defau')  # a truncated write
        with self.assertRaises(DamagedFileError):
            self.storage.metadata()
        with self.assertLogs('gpt-board', 'ERROR'):
            status, body, _ = self.http('GET', '/api/notebooks')
        self.assertEqual(status, 500)
        self.assertIn('RECOVERY', body['error'])
        self.assertEqual(index.read_text(), '{"notebooks": [{"id": "defau', 'the damaged file is not replaced')
        index.write_bytes(good_index)

        board = self.storage.path('default', 'board.json')
        board.write_text('not json')
        with self.assertLogs('gpt-board', 'ERROR'):
            self.assertEqual(self.http('GET', '/api/board')[0], 500)
            self.assertEqual(self.save('t', [shape('x')], base='anything')[0], 500)
        self.assertEqual(board.read_text(), 'not json')
        # Creating another notebook still works, and a backup brings this one back.
        self.assertEqual(self.http('POST', '/api/notebooks', {'action': 'create', 'name': 'Other'})[0], 201)
        kept = self.storage.restore('default', self.storage.backups('default')[-1])
        self.assertTrue(kept.startswith('damaged-'))
        self.assertEqual((self.storage.backup_dir('default') / kept).read_text(), 'not json')
        self.assertIn('shape:precious', self.storage.read('default')['store'])


class ExportImportTests(unittest.TestCase):
    def test_round_trip_keeps_content_and_never_reuses_an_id(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            source = BoardStorage(first)
            source.metadata()
            source.save_changes('default', {'put': [shape('ink', parentId='shape:welcome-page')], 'remove': []},
                                {'shapeCount': 1}, source.revision('default'))
            document = json.loads(json.dumps(source.export_notebook('default')))  # as written to a file
            document['snapshot']['_imports'] = {'untrusted-old-job': {'pages': ['old-page']}}
            self.assertEqual((document['format'], document['version']), ('gpt-board-notebook', 1))
            target = BoardStorage(second)
            item = target.import_notebook(document)
            again = target.import_notebook(document)
            self.assertEqual((item['id'], again['id']), ('welcome', 'welcome-2'))
            imported = target.read('welcome'); original = source.read('default')
            for item in original['store'].values():
                if item.get('typeName') == 'shape':
                    item.setdefault('meta', {}).pop('ai', None)
            self.assertEqual(imported, original, 'content and IDs survive; imported AI ownership is not trusted')
            self.assertEqual(target.inspect_export(document)['strokes'], 1)
            self.assertEqual(len(target.metadata()['notebooks']), 3)
            for broken in ({}, {**document, 'version': 99}, {**document, 'notebook': {'name': ''}},
                           {**document, 'snapshot': {'store': {'shape:a': {'id': 'shape:b', 'typeName': 'shape'}}, 'schema': 1}},
                           {**document, 'snapshot': {'store': {'shape:a': shape('a', parentId='shape:gone')}, 'schema': 1}}):
                with self.assertRaises(ClientError):
                    target.import_notebook(broken)
            self.assertEqual(len(target.metadata()['notebooks']), 3)


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pairing = pairing.Pairing(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_code_works_once_expires_and_devices_are_revocable(self):
        code = self.pairing.invite()
        token = self.pairing.redeem(code.lower(), 'iPad')  # typed in lower case
        self.assertTrue(token)
        self.assertIsNone(self.pairing.redeem(code, 'second use'))
        self.assertNotIn(token, (Path(self.tmp.name) / 'ipad-devices.json').read_text(), 'only a hash is stored')
        device = self.pairing.device(token)
        self.assertEqual(device['name'], 'iPad')
        self.assertIsNone(self.pairing.device('guess'))
        expired = self.pairing.invite()
        with patch('pairing.time.time', return_value=time.time() + pairing.INVITE_SECONDS + 1):
            self.assertIsNone(self.pairing.redeem(expired, 'late'))
        self.assertTrue(self.pairing.revoke(device['id']))
        self.assertIsNone(self.pairing.device(token))
        with patch('pairing.time.time', return_value=time.time() + pairing.SESSION_SECONDS + 1):
            self.assertEqual(self.pairing.devices(), [], 'unused sessions end')

    def test_guessing_cancels_outstanding_codes(self):
        code = self.pairing.invite()
        for _ in range(pairing.MAX_BAD_CODES):
            self.assertIsNone(self.pairing.redeem('AAAAAAAA', 'guess'))
        self.assertIsNone(self.pairing.redeem(code, 'too late'))

    def test_existing_ipad_stays_paired_and_can_be_revoked(self):
        (Path(self.tmp.name) / 'ipad-token').write_text('legacy-secret')
        self.assertEqual(self.pairing.device('legacy-secret')['id'], 'legacy')
        self.pairing.revoke('legacy')
        self.assertIsNone(self.pairing.device('legacy-secret'), 'revoking survives the migration check')


CONTROLLER = '''import json, sys, time
payload = json.load(sys.stdin)
mode = payload["section"]
if mode == "slow":
    time.sleep(5)
if mode == "refuse":
    sys.exit("Controller busy: retry")
print(json.dumps({"status": "already_saved" if mode == "again" else "saved",
                  "review_id": "wrong" if mode == "garbled" else payload["review_id"], "dir": sys.argv[2]}))
'''


class ReviewTests(unittest.TestCase):
    """The review protocol against a stand-in controller: no real course records."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.storage = BoardStorage(root)
        (root / 'controller.py').write_text(CONTROLLER)
        write_json(root / 'data/config.json', {'review': {
            'controller': str(root / 'controller.py'), 'python': sys.executable, 'courses': {'Test Course': str(root / 'course')}}})
        self.service = ReviewService(self.storage)
        self.check = self.service.archive('Page 1', PNG, 'default')

    def tearDown(self):
        self.tmp.cleanup()

    def review(self, **changes):
        return {'check_id': self.check['check_id'], 'course': 'Test Course', 'section': '1', 'page': '1', 'problem': '1',
                'original_response': 'x = 2', 'result': 'right', 'read_confidence': 'high', 'assistance': 'none',
                'errors': [], **changes}

    def test_unconfigured_board_offers_no_review_tool(self):
        with tempfile.TemporaryDirectory() as empty:
            service = ReviewService(BoardStorage(empty))
            self.assertFalse(service.enabled)
            self.assertEqual([t['name'] for t in service.tools], ['read_board', 'add_shapes', 'update_shapes', 'delete_shapes'])
            self.assertNotIn('record_review', json.dumps(service.tools))
            with self.assertRaises(ClientError):
                service.record(self.review())

    def test_outcomes_retries_and_failures(self):
        error = {key: 'text' for key in self.service.error_fields} | {'error_id': 'e1', 'type': 'ALGEBRA', 'confidence': 'high'}
        self.assertEqual(self.service.record(self.review())['status'], 'saved')
        self.assertEqual(self.service.record(self.review(result='wrong', errors=[error]))['status'], 'saved')
        self.assertEqual(self.service.record(self.review(result='unresolved', read_confidence='low'))['status'], 'saved')
        self.assertEqual(self.service.record(self.review(section='again'))['status'], 'already_saved')
        receipt = self.service.record(self.review())
        self.assertEqual(receipt['review_id'], self.service.payload(self.review())['review_id'], 'same review, same ID')
        for changes, expected in (({'course': 'Unknown'}, 'not configured'),
                                  ({'result': 'wrong'}, 'require'),
                                  ({'read_confidence': 'low'}, 'unresolved'),
                                  ({'check_id': 'check-' + '0' * 36}, 'not found'),
                                  ({'section': 'refuse'}, 'Controller busy'),
                                  ({'section': 'garbled'}, 'no valid save confirmation')):
            with self.assertRaises(ClientError) as caught:
                self.service.record(self.review(**changes))
            self.assertIn(expected, caught.exception.reason)
        with (patch('board_review.subprocess.run', side_effect=subprocess.TimeoutExpired('x', 30)),
              self.assertRaises(ClientError) as caught):
            self.service.record(self.review())
        self.assertIn('timed out', caught.exception.reason)

    def test_changed_evidence_image_is_refused(self):
        Path(self.check['file']).write_bytes(b'different image')
        with self.assertRaises(ClientError) as caught:
            self.service.payload(self.review())
        self.assertIn('fingerprint', caught.exception.reason)


if __name__ == '__main__':
    unittest.main()
