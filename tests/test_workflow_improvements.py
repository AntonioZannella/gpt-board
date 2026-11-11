"""Use disposable notebooks to verify import, export, AI ink and preview isolation."""
import copy
import io
import queue
import tempfile
import unittest
from pathlib import Path

from test_reliability import PNG, shape

from ai_operations import AIOperations
from board_storage import BoardStorage, ClientError
from media import Media
from server import BoardApp


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = BoardStorage(self.root)
        self.storage.metadata()
        self.media = Media(self.storage)
    def tearDown(self): self.tmp.cleanup()
    def staged(self):
        job=self.media.prepare('page.png', PNG.split(',')[1])
        self.media.render(job['id'],0)
        return job['id']
    def test_replace_preserves_frame_ink_and_receipts(self):
        old=self.staged()
        frame=self.media.commit(old,'default',self.storage.revision('default'))['pages'][0]
        self.media.cancel(old)
        board=self.storage.read('default')
        ink=shape('student',parentId=frame,props={'segments':[{'type':'free','points':[{'x':2,'y':3},{'x':8,'y':10}]}]})
        board['store'][ink['id']]=ink
        self.storage.save('default',board,{})
        before=self.storage.read('default')
        new=self.staged()
        self.media.commit(new,'default',self.storage.revision('default'),[frame])
        after=self.storage.read('default')
        self.assertEqual(before['store'][frame],after['store'][frame])
        self.assertEqual(ink,after['store'][ink['id']])
        self.assertEqual(len([r for r in after['store'].values() if r.get('parentId')==frame and r.get('type')=='image']),1)
        self.assertIn(old,after['_imports'])
    def test_stale_or_invalid_replacement_is_atomic(self):
        job=self.staged(); before=self.storage.path('default','board.json').read_bytes()
        for base,targets in [('stale',None),(self.storage.revision('default'),['missing'])]:
            with self.assertRaises(ClientError): self.media.commit(job,'default',base,targets)
            self.assertEqual(before,self.storage.path('default','board.json').read_bytes())
    def test_staged_export_over_fifty_pages_and_cleanup(self):
        from pypdf import PdfReader
        job=self.media.export_stage({'action':'begin'})['id']
        for page in range(51): self.media.export_stage({'action':'page','job':job,'page':page,'png':PNG,'w':1000,'h':1294})
        pdf=self.media.export_stage({'action':'finish','job':job,'count':51})
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages),51)
        self.media.export_stage({'action':'cancel','job':job})
        with self.assertRaises(ClientError): self.media.job(job)
    def test_missing_export_page_is_refused(self):
        job=self.media.export_stage({'action':'begin'})['id']
        with self.assertRaises(ClientError): self.media.export_stage({'action':'finish','job':job,'count':2})
    def test_pen_proposal_is_editable_and_undoable(self):
        ai=AIOperations(self.storage)
        args={'operation_id':'ink-preview-001','frame':'welcome-page','preview':True,'shapes':[{'type':'draw','x':50,'y':50,'w':80,'h':60,'segments':[{'type':'free','points':[{'x':0,'y':0,'z':.5},{'x':40,'y':30,'z':.8}]}]}]}
        before=copy.deepcopy(self.storage.read('default')['store'])
        self.assertEqual(ai.execute('default','add',args)['status'],'approval_required')
        self.assertEqual(before,self.storage.read('default')['store'])
        result=ai.approve('default',args['operation_id'])
        key='shape:'+result['done'][0]
        record=self.storage.read('default')['store'][key]
        self.assertEqual(record['type'],'draw');self.assertEqual(record['props']['segments'],args['shapes'][0]['segments'])
        ai.undo('default',args['operation_id'])
        self.assertNotIn(key,self.storage.read('default')['store'])
    def test_ink_preview_is_not_saved_and_queue_is_bounded(self):
        app=BoardApp(self.root)
        app.tabs={'sender':{'tab':'sender','notebook':'default','queue':queue.Queue()},'viewer':{'tab':'viewer','notebook':'default','queue':queue.Queue()}}
        before=app.storage.path('default','board.json').read_bytes()
        ink=shape('live',parentId='shape:welcome-page',props={'segments':[{'type':'free','points':[{'x':0,'y':0},{'x':3,'y':4}]}]})
        app.preview_ink({'tab':'sender','shape':ink})
        self.assertEqual(app.tabs['viewer']['queue'].get_nowait()['event'],'ink')
        self.assertEqual(before,app.storage.path('default','board.json').read_bytes())
        with self.assertRaises(ClientError): app.preview_ink({'tab':'missing','shape':ink})

class CompressionTests(unittest.TestCase):
    def test_large_snapshot_gzip_round_trip(self):
        import gzip

        from test_reliability import Fixture
        fixture=Fixture(); fixture.setUp()
        try:
            import http.client
            conn=http.client.HTTPConnection('127.0.0.1',fixture.server.server_port)
            # A large validated notebook payload exercises the ordinary GET path.
            board=fixture.storage.read('default')
            for i in range(60): board['store']['shape:compression-'+str(i)]=shape('compression-'+str(i))
            fixture.storage.save('default',board,{})
            conn.request('GET','/api/board?lazy=1',headers={'Accept-Encoding':'gzip'})
            response=conn.getresponse();raw=response.read()
            self.assertEqual(response.getheader('Content-Encoding'),'gzip')
            import json
            self.assertEqual(len(json.loads(gzip.decompress(raw))['store']),len(board['store']))
            self.assertLess(len(raw),len(gzip.decompress(raw)))
            conn.close()
        finally: fixture.tearDown()
