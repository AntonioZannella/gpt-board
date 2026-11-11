from unittest.mock import patch

from test_reliability import PNG, Fixture

from board_storage import write_json
from browser_snapshot import image_bytes


class AssetReadTests(Fixture):
    def test_asset_endpoint_reads_only_requested_record(self):
        snapshot = self.storage.read('default')
        record = {'id': 'asset:test', 'typeName': 'asset', 'type': 'image',
                  'props': {'src': PNG, 'w': 1, 'h': 1}}
        snapshot['store'][record['id']] = record
        write_json(self.storage.path('default', 'board.json'), snapshot)
        with patch.object(self.storage, 'read', wraps=self.storage.read) as read:
            status, raw, response = self.http('GET', '/api/board?asset=asset%3Atest')
            self.assertEqual(status, 200)
            self.assertEqual(raw, image_bytes(snapshot, 'asset:test')[0])
            self.assertEqual(response.getheader('Content-Type'), 'image/png')
            read.assert_called_once_with('default', record_id='asset:test')
        self.assertEqual(self.http('GET', '/api/board?asset=asset%3Amissing')[0], 404)
        self.assertEqual(self.http('GET', '/api/board?asset=shape%3Awelcome-page')[0], 404)
