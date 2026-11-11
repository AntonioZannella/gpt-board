import queue
import tempfile
import time
import unittest
from pathlib import Path

from board_storage import ClientError
from server import BoardApp


class FollowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app=BoardApp(Path(self.tmp.name))
        for tab in ('ipad','mac'):
            self.app.tabs[tab]={'tab':tab,'notebook':'default','queue':queue.Queue(maxsize=1)}
        self.body={'tab':'ipad','frame':'page-2','kind':'notebook','tablet':True,'visible':True}

    def test_presence_uses_connected_notebook_and_broadcasts_once(self):
        self.app.update_tablet_view({**self.body,'notebook':'untrusted'})
        self.assertEqual(self.app.tablet_view()['notebook'],'default')
        self.assertEqual(self.app.tabs['mac']['queue'].get_nowait()['frame'],'page-2')
        self.app.update_tablet_view(self.body)
        self.assertTrue(self.app.tabs['mac']['queue'].empty())
        self.assertTrue(self.app.tabs['ipad']['queue'].empty())

    def test_poll_recovers_a_full_queue_and_ignores_hidden_or_stale_tablets(self):
        self.app.tabs['mac']['queue'].put({})
        self.app.update_tablet_view(self.body)
        self.assertEqual(self.app.tablet_view()['frame'],'page-2')
        self.app.tabs['ipad']['view']['updated']=time.monotonic()-31
        self.assertIsNone(self.app.tablet_view())
        self.app.update_tablet_view(self.body)
        self.app.update_tablet_view({**self.body,'visible':False})
        self.assertIsNone(self.app.tablet_view())

    def test_disconnected_tablet_cannot_leave_stale_presence(self):
        self.app.update_tablet_view(self.body)
        del self.app.tabs['ipad']
        self.assertIsNone(self.app.tablet_view())
        with self.assertRaises(ClientError):self.app.update_tablet_view(self.body)

    def test_invalid_presence_is_rejected(self):
        for bad in ({'tablet':False},{'kind':'other'},{'frame':[]},{'visible':'yes'}):
            with self.assertRaises(ClientError):self.app.update_tablet_view({**self.body,**bad})
