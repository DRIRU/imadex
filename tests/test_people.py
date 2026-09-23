import base64
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from people import People, image_revision
from detection import Detector
from PIL import Image


class PeopleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data = app.DATA
        app.DATA = Path(self.temp.name)
        app.initialize()
        self.people = People(app.db)
        with app.db() as c:
            folder = c.execute("INSERT INTO folders(path) VALUES('fixture')").lastrowid
            for name in ['one', 'two', 'three']:
                c.execute('INSERT INTO images(folder_id,path,name,digest,size,mtime,extension) VALUES(?,?,?,?,?,?,?)', (folder, name, name, name, 10, 1, 'png'))
            self.images = [{'id':r['id'], 'revision':image_revision(r)} for r in c.execute('SELECT * FROM images')]
        self.person = self.people.mutate({'action':'create','name':'Sam'})['id']

    def tearDown(self):
        app.DATA = self.old_data
        self.temp.cleanup()

    def assign(self, person=None, images=None):
        return self.people.mutate({'action':'assign','person_id':person or self.person,'images':images or self.images})

    def person_row(self, key=None):
        return next(p for p in self.people.listing()['items'] if p['id']==(key or self.person))

    def test_migration_restart_many_people_and_idempotent_labels(self):
        self.assign();self.assign();app.initialize()
        another = self.people.mutate({'action':'create','name':'Sam'})['id']
        self.assign(another)
        self.assertEqual(self.person_row()['count'],3)
        self.assertEqual(len(People(app.db).image(self.images[0]['id'])['items']),2)
        self.people.mutate({'action':'unassign','person_id':self.person,'images':[self.images[0]]})
        self.assertEqual(self.person_row()['count'],2)
        self.assertEqual(self.person_row(another)['count'],3)
        with app.db() as c:
            self.assertEqual(c.execute("SELECT version FROM project_schema WHERE name='people'").fetchone()[0],1)
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0],1)

    def test_stale_missing_and_atomic_batch_validation(self):
        self.assign()
        with app.db() as c:
            c.execute("UPDATE images SET digest='changed' WHERE id=?", (self.images[0]['id'],))
            c.execute('UPDATE images SET missing=1 WHERE id=?', (self.images[1]['id'],))
        self.assertEqual(self.person_row()['count'],1)
        self.assertEqual(self.people.image(self.images[0]['id'])['items'][0]['stale'],1)
        other=self.people.mutate({'action':'create','name':'Other'})['id']
        with self.assertRaisesRegex(ValueError,'changed'):
            self.assign(other,[self.images[2],self.images[0]])
        self.assertEqual(self.person_row(other)['count'],0)
        with self.assertRaises(ValueError):self.assign(images=self.images*34)

    def test_merge_delete_cover_and_concurrent_edit(self):
        self.assign()
        target=self.people.mutate({'action':'create','name':'Target'})['id']
        self.assign(target,[self.images[0]])
        source=self.person_row();destination=self.person_row(target)
        self.people.mutate({'action':'cover','person_id':target,'version':destination['version'],'image_id':self.images[0]['id']})
        with self.assertRaisesRegex(ValueError,'changed'):
            self.people.mutate({'action':'rename','person_id':target,'version':destination['version'],'name':'Stale'})
        self.people.mutate({'action':'merge','person_id':self.person,'version':source['version'],'target_id':target,'target_version':self.person_row(target)['version']})
        merged=self.person_row(target)
        self.assertEqual((merged['count'],merged['label_count']),(3,3))
        self.people.mutate({'action':'delete','person_id':target,'version':merged['version']})
        with app.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM image_people').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM images').fetchone()[0],3)

    def test_people_search_and_pagination(self):
        for n in range(63):self.people.mutate({'action':'create','name':f'Person {n}'})
        self.assertEqual(len(self.people.listing()['items']),60)
        self.assertEqual(len(self.people.listing(offset=60)['items']),4)
        self.assertEqual(self.people.listing('sam')['total'],1)
        with self.assertRaises(ValueError):self.people.mutate({'action':'create','name':' '})

    def test_rename_preserves_labels_and_bad_inputs_are_rejected(self):
        self.assign()
        with app.db() as c:
            c.execute("UPDATE images SET name='renamed.png',path='renamed.png' WHERE id=?", (self.images[0]['id'],))
        self.assertEqual(self.person_row()['count'],3)
        with self.assertRaises(ValueError):self.assign(images=[None])
        with self.assertRaises(ValueError):self.assign(person=999)
        with self.assertRaises(ValueError):
            self.people.mutate({'action':'cover','person_id':self.person,'version':self.person_row()['version'],'image_id':999})

    def test_detection_stale_source_and_disabled_queue(self):
        d=Detector(app.DATA,app.db,lambda row:Image.new('RGB',(100,80)))
        with self.assertRaises(ValueError):d.action({'action':'queue'})
        d.action({'action':'enable','enabled':True});d.action({'action':'queue'})
        def change_mid_inference(picture):
            with app.db() as c:c.execute("UPDATE images SET digest='new-content' WHERE id=?",(self.images[0]['id'],))
            return [{'x':0,'y':0,'width':1,'height':1,'confidence':1}]
        with patch.object(d,'detect',side_effect=change_mid_inference):d.process_one()
        self.assertEqual(d.status(self.images[0]['id'])['image']['status'],'unprocessed')
        d.action({'action':'enable','enabled':False})
        self.assertFalse(d.process_one())

    def test_http_filter_and_authentication(self):
        self.assign(images=[self.images[0]])
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        app.configure_access(server,'https://photos.example.com','test-password-long-enough')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        headers={'Authorization':'Basic '+base64.b64encode(b'frame:test-password-long-enough').decode()}
        try:
            for path in ['/api/people','/api/images/1/people','/api/detection','/people.js']:
                with self.assertRaises(HTTPError) as error:urlopen(base+path)
                self.assertEqual(error.exception.code,401)
            with urlopen(Request(base+f'/api/images?person={self.person}&q=one&format=png',headers=headers)) as r:
                data=json.load(r);self.assertEqual(data['total'],1);self.assertIn('revision',data['items'][0])
            with urlopen(Request(base+f'/api/images?person={self.person}&q=two',headers=headers)) as r:self.assertEqual(json.load(r)['total'],0)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base+'/api/people',data=b'{"action":"create","name":"Wrong"}',headers={**headers,'Content-Type':'application/json','Origin':'https://evil.test'}))
            self.assertEqual(error.exception.code,403)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_detection_retry_resume_clear_and_no_labels(self):
        detector=Detector(app.DATA,app.db,lambda row:Image.new('RGB',(100,80)))
        self.assertFalse(detector.status()['enabled'])
        detector.action({'action':'enable','enabled':True});detector.action({'action':'queue'})
        regions=[{'x':.1,'y':.2,'width':.3,'height':.4,'confidence':.9}]
        with patch.object(detector,'detect',side_effect=ValueError('offline')):detector.process_one()
        self.assertEqual(detector.status()['failed'],1)
        detector.action({'action':'queue'})
        resumed=Detector(app.DATA,app.db,lambda row:Image.new('RGB',(100,80)))
        with patch.object(resumed,'detect',return_value=regions):
            while resumed.process_one():pass
        self.assertEqual(resumed.status()['pending'],0)
        self.assertEqual(resumed.status(self.images[0]['id'])['image']['regions'],regions)
        self.assertEqual(self.person_row()['count'],0)
        resumed.action({'action':'ignore','image_id':self.images[0]['id'],'revision':self.images[0]['revision']})
        self.assertEqual(resumed.status(self.images[0]['id'])['image']['regions'],[])
        self.assign()
        resumed.action({'action':'clear'})
        self.assertEqual(resumed.status()['total'],0)
        self.assertEqual(self.person_row()['count'],3)

    def test_detection_cancellation_does_not_republish_results(self):
        d=Detector(app.DATA,app.db,lambda row:Image.new('RGB',(100,80)))
        d.action({'action':'enable','enabled':True});d.action({'action':'queue'})
        def clear_mid_inference(picture):
            d.action({'action':'clear'});return [{'x':0,'y':0,'width':1,'height':1,'confidence':1}]
        with patch.object(d,'detect',side_effect=clear_mid_inference):d.process_one()
        self.assertEqual(d.status()['total'],0)


if __name__=='__main__':unittest.main()
