#!/usr/bin/env python3
"""GPT Board command line: start, check, pair, back up and move notebooks.

    python3 board.py start            build if needed, run the board and iPad bridge
    python3 board.py status           what is running, and what is misconfigured
    python3 board.py pair             one-time code for pairing an iPad
    python3 board.py devices          paired devices
    python3 board.py revoke ID        unpair one device
    python3 board.py backups ID       saved backups of a notebook
    python3 board.py restore ID FILE  bring a backup back (the current file is kept)
    python3 board.py export ID FILE   write a notebook to a portable file
    python3 board.py import FILE      add a notebook from such a file
"""
import argparse
import http.client
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

if sys.version_info < (3, 11):  # noqa: UP036 - explain the prerequisite before imports
    raise SystemExit(f'GPT Board needs Python 3.11 or newer; this is {sys.version.split()[0]}.')

from board_review import ReviewService
from board_storage import (
    ROOT,
    BoardStorage,
    ClientError,
    DamagedFileError,
    read_json,
    write_json,
)
from build import FILES, build
from file_lock import file_lock
from pairing import Pairing

PORT = int(os.environ.get('PORT', '8787'))
WIFI_CHECK_SECONDS = 15
STARTUP_TIMEOUT_SECONDS = 5
STOP_TIMEOUT_SECONDS = 5


def health(port=PORT):
    """What answers on the board port: a health dict, 'other' or None."""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        conn.request('GET', '/api/health')
        response = conn.getresponse()
        body = response.read(4096)
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    try:
        value = json.loads(body)
        if (response.status == 200 and isinstance(value, dict) and value.get('app') == 'gpt-board'
                and isinstance(value.get('version'), str) and isinstance(value.get('tabs'), int)):
            return value
    except ValueError:
        pass
    # Releases before 1.2.0 have no health route but name themselves here.
    return 'old' if 'GPTBoard' in (response.getheader('Server') or '') else 'other'


def stale_build(root=ROOT):
    root = Path(root)
    pairs = [(root / 'dist/native' / name, root / 'src' / name) for name in FILES]
    pairs.append((root / 'dist/index.html', root / 'src/index.html'))
    return any(not target.is_file() or target.read_bytes() != source.read_bytes() for target, source in pairs)


def bridge_running(bridge):
    """A saved bridge address is not proof its process is still answering."""
    if (not isinstance(bridge, dict) or not isinstance(bridge.get('host'), str)
            or not isinstance(bridge.get('port'), int) or not 1 <= bridge['port'] <= 65535):
        return False
    conn = http.client.HTTPConnection(bridge['host'], bridge['port'], timeout=1)
    try:
        conn.request('GET', '/pair')  # no code: just read the public pairing form
        response = conn.getresponse()
        return response.status == 200 and b'<title>Pair with GPT Board</title>' in response.read(8192)
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def wait_until_ready(child, probe, description):
    """Wait for a child to answer, or explain why startup failed."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while True:
        code = child.poll()
        if code is not None:
            raise RuntimeError(f'{description} exited with code {code}. Check its message above.')
        if probe():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f'{description} did not respond within {STARTUP_TIMEOUT_SECONDS} seconds. '
                               'Check its startup messages and port settings.')
        time.sleep(.1)


def stop_child(child):
    """Reap a child, with a bounded wait even if it stops responding."""
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=STOP_TIMEOUT_SECONDS)


def wifi_address():
    from ipad_server import wifi_host
    try:
        return wifi_host()
    except (OSError, RuntimeError):
        return None


def enable_keep_awake():
    connection = http.client.HTTPConnection('127.0.0.1', PORT, timeout=3)
    try:
        connection.request('POST', '/api/pairing', json.dumps({'action': 'keep-awake', 'enabled': True}),
                           {'Content-Type': 'application/json'})
        response = connection.getresponse()
        response.read()
        if response.status != 200:
            raise RuntimeError('Could not enable keep-awake. Use Connect iPad on the Mac to check it.')
    finally:
        connection.close()


def start(args):
    try:
        with file_lock(ROOT / 'data/supervisor.lock', blocking=False):
            return start_locked(args)
    except (BlockingIOError, PermissionError):
        raise SystemExit('A GPT Board supervisor already owns this installation. Run board.py status; do not start another.') from None


def start_locked(args):
    running = health()
    if running == 'other':
        raise SystemExit(f'Port {PORT} is used by another program. Close it, or run with PORT=8790 python3 board.py start')
    if running == 'old':
        raise SystemExit('An older GPT Board server is running. Wait for Saved in every board tab, stop that server '
                         '(Ctrl-C in its terminal), then run this command again.')
    if running:
        if getattr(args, 'keep_awake', False):
            enable_keep_awake()
        print(f'GPT Board {running["version"]} is already running: http://localhost:{PORT}')
        return status(args)
    if not getattr(args, 'no_build', False) and stale_build():
        build()
    children = {}
    address = None

    def start_bridge():
        nonlocal address
        old = children.pop('bridge', None)
        if old:
            stop_child(old)
        address = wifi_address()
        if not address:
            print('No Wi-Fi address found, so the iPad bridge is off. It starts when Wi-Fi returns.')
            return
        try:
            port = int(os.environ.get('IPAD_PORT', '8788'))
            children['bridge'] = subprocess.Popen([sys.executable, str(ROOT / 'ipad_server.py')],
                                                  env={**os.environ, 'BOARD_PORT': str(PORT), 'IPAD_HOST': address})
            wait_until_ready(children['bridge'], lambda: bridge_running({'host': address, 'port': port}),
                             'The iPad bridge')
        except (OSError, RuntimeError, ValueError) as exc:
            child = children.pop('bridge', None)
            if child:
                stop_child(child)
            address = None  # retry on the next Wi-Fi check
            print(f'iPad bridge unavailable: {exc} The Mac board remains available; retrying in {WIFI_CHECK_SECONDS} seconds.')

    def request_stop(*_):
        raise KeyboardInterrupt

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        children['board'] = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', str(PORT)])
        wait_until_ready(children['board'], lambda: isinstance(health(), dict), 'The board server')
        if getattr(args, 'keep_awake', False):
            enable_keep_awake()
        start_bridge()
        print(f'\nBoard on this computer:  http://localhost:{PORT}')
        if address:
            print('iPad: open GPT Board here, choose Connect iPad, and follow the steps.')
        print('Press Ctrl-C in this terminal to stop.\n')
        if not args.no_open:
            webbrowser.open(f'http://localhost:{PORT}')
        while True:
            code = children['board'].poll()
            if code is not None:
                if code == 0:
                    print('Board stopped; closing the supervised bridge.')
                    return 0
                raise RuntimeError(f'The board server stopped with code {code}. Check its message above, '
                                   'then run python3 board.py start again.')
            time.sleep(WIFI_CHECK_SECONDS)
            bridge = children.get('bridge')
            # A new Wi-Fi network gives the Mac a new address; the bridge has
            # to listen there, or the iPad would keep trying a dead address.
            if wifi_address() != address or (bridge and bridge.poll() is not None):
                print('Wi-Fi address changed or the bridge stopped; restarting the iPad bridge.')
                start_bridge()
    except KeyboardInterrupt:
        print('\nStopping. Anything showing Saved is on disk.')
        return 0
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f'GPT Board could not keep running: {exc}') from None
    finally:
        # Install the handler before creating children, and cover readiness,
        # bridge startup and browser opening with this same cleanup path.
        try:
            for child in reversed(list(children.values())):
                stop_child(child)
        finally:
            signal.signal(signal.SIGTERM, previous_term)


def status(args):
    """Redacted diagnostics: no tokens, codes, notebook content or course paths."""
    ok = True

    def line(good, text):
        nonlocal ok
        ok = ok and good is not False
        print(('  ok   ' if good else '  --   ' if good is None else '  FIX  ') + text)

    print('GPT Board status')
    line(True, f'Python {sys.version.split()[0]}')
    line(not stale_build(), 'Frontend build is current' if not stale_build() else 'Frontend build is missing or out of date: run python3 build.py')
    running = health()
    if isinstance(running, dict):
        line(True, f'Board server {running["version"]} at http://localhost:{PORT} with {running["tabs"]} open tab(s)')
    elif running == 'old':
        line(False, 'An older board server is running; restart it to use this version (wait for Saved first)')
    elif running == 'other':
        line(False, f'Port {PORT} is used by another program')
    else:
        line(None, 'Board server is not running: python3 board.py start')
    storage = BoardStorage(ROOT)
    try:
        books = storage.metadata()['notebooks']
        line(True, f'{len(books)} notebook(s) and whiteboard(s) listed')
    except DamagedFileError as exc:
        line(False, exc.reason)
    bridge = read_json(ROOT / 'data/ipad-bridge.json')
    address = wifi_address()
    if not bridge:
        line(None, 'iPad bridge is not running')
    elif bridge.get('host') != address:
        line(False, f'iPad bridge listens on {bridge.get("host")} but Wi-Fi is now {address}: restart with python3 board.py start')
    elif not bridge_running(bridge):
        line(False, 'iPad bridge is not responding at its saved address: restart with python3 board.py start')
    else:
        line(True, 'iPad bridge at ' + ' or '.join(bridge.get('names', [])))
    paired = read_json(ROOT / 'data/ipad-devices.json')  # read only: status never changes pairing data
    line(None if paired is None else True,
         f'{len(paired["devices"])} paired device(s)' if paired else 'Pairing list is created when the iPad bridge first starts')
    review = ReviewService(storage)
    if not review.enabled:
        line(None, 'Review logging is not configured (optional; see README)')
    else:
        missing = [label for label, folder in review.courses.items() if not Path(folder).is_dir()]
        line(Path(review.controller).is_file() and not missing,
             f'Review logging: {len(review.courses)} course(s)'
             + ('' if Path(review.controller).is_file() else '; controller file not found')
             + (f'; course folder missing for {", ".join(missing)}' if missing else ''))
    return 0 if ok else 1


def stop(args):
    """Stop only this recognized board, and only after every tab has closed."""
    if not isinstance(health(), dict):
        raise SystemExit('No current GPT Board server found. No process was stopped.')
    conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=3)
    try:
        conn.request('POST', '/api/shutdown', '{}', {'Content-Type': 'application/json'})
        response = conn.getresponse(); body = json.loads(response.read())
        if response.status != 200:
            raise SystemExit(body.get('error', 'Stop refused.'))
        print('Board stopped. Its supervisor will stop the bridge; no notebook data was changed.')
    finally:
        conn.close()


def pair(args):
    bridge = read_json(ROOT / 'data/ipad-bridge.json')
    if not bridge:
        raise SystemExit('Start the board first: python3 board.py start')
    code = Pairing(ROOT / 'data').invite()
    print('On the iPad, on the same Wi-Fi, open one of:')
    for name in bridge.get('names', [bridge['origin']]):
        print('   ' + name)
    print(f'and type this code within 10 minutes (it works once):\n\n   {code[:4]} {code[4:]}\n')


def devices(args):
    for device in Pairing(ROOT / 'data').devices():
        seen = time.strftime('%Y-%m-%d %H:%M', time.localtime(device['seen']))
        print(f'{device["id"]:10} {device["name"]:36} last seen {seen}')


def revoke(args):
    print('Unpaired.' if Pairing(ROOT / 'data').revoke(args.id) else 'No such device. See: python3 board.py devices')


def backups(args):
    names = BoardStorage(ROOT).backups(args.notebook)
    print('\n'.join(names) if names else 'No backups yet. One is kept at most every 10 minutes while a notebook is edited.')


def restore(args):
    kept = BoardStorage(ROOT).restore(args.notebook, args.file)
    print(f'Restored {args.file}. What was there before is kept as {kept}. Reload open board tabs.')


def export(args):
    write_json(args.file, BoardStorage(ROOT).export_notebook(args.notebook))
    print(f'Wrote {args.file}. It contains that notebook\'s pages and writing; share it only if you mean to.')


def import_(args):
    storage = BoardStorage(ROOT)
    document = read_json(args.file)
    preview = storage.inspect_export(document)
    print(f'{preview["name"]} ({preview["kind"]}): {preview["pages"]} page(s), {preview["strokes"]} stroke(s), {preview["shapes"]} shape(s)')
    if args.dry_run:
        return
    item = storage.import_notebook(document)
    print(f'Imported as "{item["id"]}". Existing notebooks were not changed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('start')
    command.add_argument('--keep-awake', action='store_true', help='prevent Mac idle sleep while the server runs; display may sleep')
    command.add_argument('--no-build', action='store_true', help='serve the existing built frontend without replacing it')
    command.add_argument('--no-open', action='store_true', help='do not open a browser window')
    command.set_defaults(run=start)
    for name, run in (('status', status), ('doctor', status), ('stop', stop), ('pair', pair), ('devices', devices)):
        commands.add_parser(name).set_defaults(run=run)
    command = commands.add_parser('revoke')
    command.add_argument('id')
    command.set_defaults(run=revoke)
    command = commands.add_parser('backups')
    command.add_argument('notebook')
    command.set_defaults(run=backups)
    command = commands.add_parser('restore')
    command.add_argument('notebook')
    command.add_argument('file', help='a name printed by: python3 board.py backups ID')
    command.set_defaults(run=restore)
    command = commands.add_parser('export')
    command.add_argument('notebook')
    command.add_argument('file')
    command.set_defaults(run=export)
    command = commands.add_parser('recover-index')
    command.add_argument('--dry-run', action='store_true')
    command.set_defaults(run=lambda args: print(json.dumps(BoardStorage(ROOT).recover_index(args.dry_run), indent=2)))
    command = commands.add_parser('import')
    command.add_argument('file')
    command.add_argument('--dry-run', action='store_true', help='describe the file without importing it')
    command.set_defaults(run=import_)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (ClientError, DamagedFileError) as exc:
        raise SystemExit(exc.reason) from None


if __name__ == '__main__':
    raise SystemExit(main())
