import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from PIL import Image
import app
import ocr
from people import image_revision


class OCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.old = app.DATA
        app.DATA = Path(self.tmp.name);app.initialize()
        with app.db() as c:
            c.execute("INSERT INTO folders(path) VALUES('fixture')")
            c.execute("INSERT INTO images(folder_id,path,name,digest,size,mtime) VALUES(1,'fixture.png','picture.png','original',100,1)")
        self.service = ocr.OCR(app.DATA, app.db, lambda row:Image.new('RGB',(80,40)), runner=lambda image:'Invoice ACME 100% paid')
        self.available = patch.object(self.service,'available',return_value=True);self.available.start()

    def tearDown(self):
        self.service.close();self.available.stop();app.DATA = self.old;self.tmp.cleanup()

    def row(self):
        with app.db() as c:return dict(c.execute('SELECT * FROM images').fetchone())

    def matches(self, word):
        clause,values = ocr.text_filter(word)
        with app.db() as c:return c.execute('SELECT count(*) FROM images WHERE missing=0 AND '+clause,values).fetchone()[0]

    def test_text_persists_and_revision_or_model_changes_hide_it(self):
        self.assertTrue(self.service.process(self.row(),0));self.assertEqual(self.matches('ACME'),1)
        self.assertEqual(self.matches('100%'),1);self.assertEqual(self.matches('_'),0)
        reopened = ocr.OCR(app.DATA,app.db,None)
        self.assertIn('Invoice',reopened.status(1)['image']['text'])
        with app.db() as c:c.execute("UPDATE image_ocr SET model='old'")
        self.assertEqual(self.matches('ACME'),0);self.assertEqual(reopened.status()['pending'],1)
        self.service.process(self.row(),0)
        with app.db() as c:c.execute("UPDATE images SET digest='changed'")
        self.assertEqual(self.matches('ACME'),0);self.assertEqual(reopened.status(1)['image']['status'],'pending')

    def test_clear_during_extraction_discards_result_and_preserves_other_metadata(self):
        self.service.action({'action':'settings','enabled':True})
        row = self.row();epoch = self.service.epoch
        def clear(image):
            self.service.action({'action':'clear'});return 'private result'
        self.service.runner = clear
        self.assertFalse(self.service.process(row,epoch))
        self.assertEqual(self.service.status()['ready'],0);self.assertFalse(self.service.status()['enabled'])
        self.assertEqual(self.row()['digest'],'original')

    def test_changed_or_missing_during_extraction_discards_result(self):
        def changed(image):
            with app.db() as c:c.execute('UPDATE images SET missing=1')
            return 'obsolete'
        self.service.runner = changed;self.assertFalse(self.service.process(self.row(),0))
        self.assertEqual(self.matches('obsolete'),0)

    def test_failed_jobs_do_not_loop_and_retry_is_explicit(self):
        self.service.action({'action':'settings','enabled':True})
        self.service.runner = lambda image:(_ for _ in ()).throw(ValueError('test failure'))
        self.assertFalse(self.service.process(self.row(),self.service.epoch))
        self.assertEqual(self.service.status()['failed'],1);self.assertIsNone(self.service.next_image())
        self.service.action({'action':'retry'});self.assertIsNotNone(self.service.next_image())

    def test_single_photo_validates_revision_and_works_while_paused(self):
        with self.assertRaises(ValueError):self.service.action({'action':'image','image_id':1,'revision':'stale'})
        self.service.action({'action':'image','image_id':1,'revision':image_revision(self.row())})
        self.assertEqual(self.service.next_image()['id'],1);self.assertIsNone(self.service.next_image())
        with self.assertRaises(ValueError):self.service.action({'action':'settings','enabled':'true'})

    def test_background_resume_tracks_jobs_without_reprocessing_ready_images(self):
        import time
        self.service.action({'action':'settings','enabled':True})
        completed = threading.Event()
        self.service.runner = lambda image:(completed.set() or 'background text')
        self.service.start();self.assertTrue(completed.wait(3))
        deadline = time.monotonic()+3
        while self.service.status()['ready'] != 1 and time.monotonic()<deadline:time.sleep(.02)
        self.service.close();self.assertEqual(self.service.status()['ready'],1)
        with app.db() as c:
            job = c.execute("SELECT * FROM job_history WHERE kind='ocr'").fetchone()
            self.assertEqual(job['status'],'complete');self.assertEqual(job['processed'],1)
        reopened = ocr.OCR(app.DATA,app.db,None)
        self.assertTrue(reopened.status()['enabled']);self.assertIsNone(reopened.next_image())

    def test_authenticated_api_text_filters_and_no_semantic_regression(self):
        self.service.process(self.row(),0)
        server = ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        app.configure_access(server,'https://photos.example.test','long-private-password')
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        old = app.ocr_service;app.ocr_service = self.service
        import base64
        auth = 'Basic '+base64.b64encode(b'frame:long-private-password').decode()
        def request(path,auth_value=auth):
            return urlopen(Request('http://127.0.0.1:'+str(server.server_port)+path,headers={'Authorization':auth_value}))
        try:
            with self.assertRaises(HTTPError) as error:request('/api/ocr','')
            self.assertEqual(error.exception.code,401)
            error.exception.close()
            with request('/api/images?mode=ocr&q=acme') as r:self.assertEqual(json.load(r)['total'],1)
            with request('/api/images?mode=text&q=acme&view=favorites') as r:self.assertEqual(json.load(r)['total'],0)
            with request('/api/ocr?image_id=1') as r:self.assertIn('Invoice',json.load(r)['image']['text'])
            with app.db() as c:c.execute('UPDATE images SET missing=1')
            with request('/api/images?mode=ocr&q=acme') as r:self.assertEqual(json.load(r)['total'],0)
        finally:
            server.shutdown();server.server_close();thread.join();app.ocr_service = old


if __name__ == '__main__':unittest.main()
