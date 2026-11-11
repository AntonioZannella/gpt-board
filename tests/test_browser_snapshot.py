import copy
import unittest

from browser_snapshot import browser_snapshot, image_bytes


class BrowserSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'store': {
            'asset:a': {'id': 'asset:a', 'typeName': 'asset', 'props': {'src': 'data:image/png;base64,YWJj', 'w': 10}},
            'shape:a': {'id': 'shape:a', 'typeName': 'shape', 'type': 'image', 'props': {'assetId': 'asset:a'}},
        }, 'schema': {}}

    def test_lightweight_response_preserves_saved_assets(self):
        before = copy.deepcopy(self.snapshot)
        light = browser_snapshot(self.snapshot, 'default', 'rev1')
        self.assertEqual(self.snapshot, before)
        self.assertEqual(light['store']['shape:a'], before['store']['shape:a'])
        self.assertEqual(light['store']['asset:a']['props']['src'], '/api/board?notebook=default&asset=asset%3Aa&v=rev1')
        self.assertEqual(image_bytes(self.snapshot, 'asset:a'), (b'abc', 'image/png'))

    def test_missing_non_asset_and_external_sources_are_not_served(self):
        self.assertIsNone(image_bytes(self.snapshot, 'missing'))
        self.assertIsNone(image_bytes(self.snapshot, 'shape:a'))
        self.snapshot['store']['asset:a']['props']['src'] = 'https://example.com/image.png'
        self.assertIsNone(image_bytes(self.snapshot, 'asset:a'))
        self.assertEqual(browser_snapshot(self.snapshot, 'default', 'r'), self.snapshot)
