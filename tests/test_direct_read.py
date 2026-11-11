import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from server import BoardApp

PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII='

class DirectReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=BoardApp(Path(self.tmp.name))
        self.app.tabs['ipad']={'tab':'ipad','notebook':'default','queue':queue.Queue(),
            'view':{'tab':'ipad','notebook':'default','frame':'shape:old-presence','kind':'notebook','updated':time.monotonic()}}
        self.app.review=Mock(enabled=True,instructions='Review protocol')
        self.app.review.archive.return_value={'check_id':'check-1','notebook':'default','frame':'Actual page',
            'ts':'2026-11-11','frame_id':'actual-page'}
        self.app.send_to_tab=Mock(return_value={'ok':True,'png':PNG,'notebook':'default','tab':'ipad',
            'summary':{'frame':'Actual page','frame_id':'actual-page'}})

    def test_direct_read_targets_ipad_and_archives_actual_captured_page(self):
        result=self.app.call_tool('read_board',{'frame':'@ipad'})
        call=self.app.send_to_tab.call_args
        self.assertTrue(call.args[1]['current_page'])
        self.assertNotIn('frame',call.args[1])
        self.assertEqual(call.kwargs['target_tab'],'ipad')
        self.assertEqual(self.app.review.archive.call_args.args[0],'Actual page')
        summary=json.loads(result['content'][-1]['text'])
        self.assertEqual(summary['review_context']['check_id'],'check-1')
        self.assertEqual(summary['target']['notebook'],'default')
        self.assertIn('total_board_read',summary['timing_ms'])

    def test_missing_stale_or_wrong_notebook_refuses_without_guessing(self):
        for change,args in [('wrong',{'frame':'@ipad','notebook':'other'}),('stale',{'frame':'@ipad'})]:
            if change=='stale':self.app.tabs['ipad']['view']['updated']-=40
            self.assertTrue(self.app.call_tool('read_board',args)['isError'])
        self.app.send_to_tab.assert_not_called()
        self.app.review.archive.assert_not_called()

    def test_old_frontend_without_frame_identity_is_not_archived(self):
        self.app.send_to_tab.return_value['summary']={'frames':['Page 1']}
        self.assertTrue(self.app.call_tool('read_board',{'frame':'@ipad'})['isError'])
        self.app.review.archive.assert_not_called()

    def test_targeted_transport_never_falls_back_to_another_device(self):
        result=BoardApp.send_to_tab(self.app,'export',{},notebook='default',target_tab='gone')
        self.assertFalse(result['ok'])

    def test_default_read_captures_ipad_and_archives_in_one_call(self):
        result = self.app.call_tool('read_board', {})
        self.app.send_to_tab.assert_called_once()
        self.assertEqual(self.app.send_to_tab.call_args.kwargs['target_tab'], 'ipad')
        self.assertTrue(self.app.send_to_tab.call_args.args[1]['current_page'])
        self.app.review.archive.assert_called_once()
        self.assertIn('review_context', json.loads(result['content'][-1]['text']))

    def test_listing_overview_and_explicit_notebook_do_not_follow_ipad(self):
        for args in [{'list_frames': True}, {'overview': True}, {'notebook': 'other'}, {'frame': 'Other page'}]:
            with self.subTest(args=args):
                self.app.call_tool('read_board', args)
                self.assertNotIn('target_tab', self.app.send_to_tab.call_args.kwargs)
                self.assertNotIn('current_page', self.app.send_to_tab.call_args.args[1])

    def test_stale_ipad_preserves_default_listing_fallback(self):
        self.app.tabs['ipad']['view']['updated'] -= 40
        self.app.call_tool('read_board', {})
        self.assertNotIn('target_tab', self.app.send_to_tab.call_args.kwargs)

    def test_default_read_also_requires_actual_frame_identity(self):
        self.app.send_to_tab.return_value['summary'] = {'frames': ['Page 1']}
        self.assertTrue(self.app.call_tool('read_board', {})['isError'])
        self.app.review.archive.assert_not_called()
