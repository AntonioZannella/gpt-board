"""iPad pairing: a short one-time code becomes a per-device session.

The Mac shows a code (Connect iPad, or `python3 board.py pair`). Typing it on
the iPad within ten minutes gives that iPad its own secret cookie. Each paired
device is listed and can be revoked on its own; a code cannot be reused.

Only a SHA-256 hash of each device secret is stored, so reading this file does
not let anyone impersonate a device.
"""
import hashlib
import secrets
import threading
import time
from pathlib import Path

from board_storage import DamagedFileError, load_json, write_json
from file_lock import file_lock

INVITE_SECONDS = 10 * 60
SESSION_SECONDS = 30 * 24 * 3600  # since the device was last seen
MAX_BAD_CODES = 10  # then every outstanding code is cancelled
TOUCH_SECONDS = 3600  # how often "last seen" is written back
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # no 0/O or 1/I/L
CODE_LENGTH = 8
THREAD_LOCK = threading.Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalize(code):
    return ''.join(ch for ch in str(code).upper() if ch in CODE_ALPHABET)


class Pairing:
    def __init__(self, data):
        self.data = Path(data)
        self.path = self.data / 'ipad-devices.json'

    def update(self, change):
        """Run change(state) with the file locked, then save the result."""
        self.data.mkdir(parents=True, exist_ok=True)
        with THREAD_LOCK, file_lock(self.data / 'ipad-devices.lock'):
            state = load_json(self.path, 'Pairing state')
            if state is None:
                state = {'invites': [], 'devices': [], 'bad_codes': 0}
            if not isinstance(state, dict) or not all(isinstance(state.get(k), list) for k in ('invites', 'devices')):
                raise DamagedFileError('Pairing state is damaged; original preserved. Restore its backup.')
            before = repr(state)
            now = time.time()
            # The token from before per-device sessions keeps working as one
            # listed, revocable device, so an already paired iPad stays paired.
            legacy = self.data / 'ipad-token'
            if not state.get('legacy_migrated') and legacy.is_file():
                state['devices'].append({'id': 'legacy', 'name': 'iPad paired before this update',
                                         'hash': digest(legacy.read_text().strip()), 'created': now, 'seen': now})
                state['legacy_migrated'] = True
            state['invites'] = [i for i in state['invites'] if i['expires'] > now]
            state['devices'] = [d for d in state['devices'] if d['seen'] + SESSION_SECONDS > now]
            result = change(state, now)
            if repr(state) != before:
                write_json(self.path, state)
            return result

    def invite(self):
        """A new one-time code, valid for INVITE_SECONDS."""
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))

        def change(state, now):
            if len(state['invites']) >= 20 or len(state['devices']) >= 50:
                raise ValueError('Too many pending invitations or paired devices. Revoke unused devices.')
            state['invites'].append({'hash': digest(code), 'expires': now + INVITE_SECONDS})
            state['bad_codes'] = 0
            return code
        return self.update(change)

    def redeem(self, code, name):
        """Exchange a valid code for a new device secret; None if it is wrong or expired."""
        wanted = digest(normalize(code))

        def change(state, now):
            match = next((i for i in state['invites'] if secrets.compare_digest(i['hash'], wanted)), None)
            if not match:
                state['bad_codes'] = state.get('bad_codes', 0) + 1
                if state['bad_codes'] >= MAX_BAD_CODES:
                    state['invites'] = []  # guessing stops here; make a new code on the Mac
                return None
            state['invites'].remove(match)
            token = secrets.token_hex(24)
            state['devices'].append({'id': secrets.token_hex(4), 'name': str(name)[:60],
                                     'hash': digest(token), 'created': now, 'seen': now})
            return token
        return self.update(change)

    def device(self, token):
        """The paired device for this cookie secret, or None."""
        wanted = digest(token)

        def change(state, now):
            match = next((d for d in state['devices'] if secrets.compare_digest(d['hash'], wanted)), None)
            if match and now - match['seen'] > TOUCH_SECONDS:
                match['seen'] = now
            return match
        return self.update(change)

    def devices(self):
        return self.update(lambda state, now: [
            {key: d[key] for key in ('id', 'name', 'created', 'seen')} for d in state['devices']])

    def revoke(self, device_id):
        def change(state, now):
            kept = [d for d in state['devices'] if d['id'] != device_id]
            removed = len(kept) != len(state['devices'])
            state['devices'] = kept
            return removed
        return self.update(change)
