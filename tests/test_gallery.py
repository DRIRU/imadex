import sys
import tempfile
import unittest
import base64
import json
import threading
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import gallery


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=app.DATA;app.DATA=Path(self.tmp.name);app.initialize();self.gallery=gallery.Gallery(app.db)
        with app.db() as c:
            folder=c.execute("INSERT INTO folders(path) VALUES('fixture')").lastrowid
            for n,taken in [('one','2024:02:29 10:00:00'),('two',''),('three','invalid')]:
                c.execute('INSERT INTO images(folder_id,path,name,digest,size,mtime,taken) VALUES(?,?,?,?,?,?,?)',(folder,n,n,n,10,0,taken))
            c.execute("UPDATE images SET taken_source='exif-original'")
        self.photos=self.selection()

    def tearDown(self):app.DATA=self.old;self.tmp.cleanup()

    def selection(self):
        with app.db() as c:return [{'id':r['id'],'revision':gallery.image_revision(r)} for r in c.execute('SELECT * FROM images')]

    def album(self):return self.gallery.mutate({'action':'create_album','name':'Holiday'})['id']

    def test_dates_exif_iso_fallback_and_unknown(self):
        self.assertEqual(gallery.capture_day('2024:02:29 10:00:00'),'2024-02-29')
        self.assertEqual(gallery.capture_day('2026-10-02T08:00:00Z'),'2026-10-02')
        self.assertIsNone(gallery.capture_day('2023:02:29'))
        self.assertIsNone(gallery.image_day('',0,'capture'))
        self.assertEqual(gallery.image_day('',0,'best'),'1970-01-01')
        self.assertEqual(gallery.image_day('2024:02:29',0,'modified'),'1970-01-01')
        clauses,values=['missing=0'],[]
        gallery.date_filters({'date_basis':'capture','from':'2024-01-01','to':'2024-12-31'},clauses,values)
        with app.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM images WHERE '+' AND '.join(clauses),values).fetchone()[0],1)
        with self.assertRaises(ValueError):gallery.date_filters({'from':'2025-01-01','to':'2024-01-01'},[],[])

    def test_album_bulk_and_undo_survive_restart(self):
        album=self.album();edit=self.gallery.mutate({'action':'bulk','photos':self.photos,'tags':'family','favorite':True,'album_id':album})
        self.assertEqual(self.gallery.listing()['albums'][0]['count'],3)
        app.initialize();gallery.Gallery(app.db).mutate({'action':'undo','id':edit['undo_id']})
        with app.db() as c:
            self.assertEqual(c.execute('SELECT sum(favorite) FROM images').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM album_images').fetchone()[0],0)
        with self.assertRaises(ValueError):self.gallery.mutate({'action':'undo','id':edit['undo_id']})

    def test_stale_batch_is_atomic(self):
        with app.db() as c:c.execute("UPDATE images SET digest='changed' WHERE id=?",(self.photos[-1]['id'],))
        with self.assertRaises(ValueError):self.gallery.mutate({'action':'bulk','photos':self.photos,'favorite':True})
        with app.db() as c:self.assertEqual(c.execute('SELECT sum(favorite) FROM images').fetchone()[0],0)

    def test_undo_rejects_newer_edit_without_partial_restore(self):
        edit=self.gallery.mutate({'action':'bulk','photos':self.photos,'favorite':True})
        with app.db() as c:c.execute("UPDATE images SET tags='newer' WHERE id=?",(self.photos[-1]['id'],))
        with self.assertRaises(ValueError):self.gallery.mutate({'action':'undo','id':edit['undo_id']})
        with app.db() as c:self.assertEqual(c.execute('SELECT sum(favorite) FROM images').fetchone()[0],3)

    def test_album_delete_retains_images_and_rejects_invalid_restore(self):
        album=self.album();self.gallery.mutate({'action':'bulk','photos':self.photos,'album_id':album})
        edit=self.gallery.mutate({'action':'bulk','photos':self.photos,'album_id':album,'album_action':'remove'})
        self.gallery.mutate({'action':'delete_album','id':album})
        with self.assertRaises(ValueError):self.gallery.mutate({'action':'undo','id':edit['undo_id']})
        with app.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM images').fetchone()[0],3)

    def test_saved_filters_are_persistent_validated_and_not_sql(self):
        key=self.gallery.mutate({'action':'save_search','name':'Favorites','filters':{'view':'favorites','q':"' OR 1=1 --",'from':'2024-01-01','sql':'DROP TABLE images'}})['id']
        app.initialize();saved=self.gallery.listing()['searches'][0]
        self.assertEqual(saved['filters']['view'],'favorites');self.assertNotIn('sql',saved['filters'])
        self.gallery.mutate({'action':'delete_search','id':key});self.assertEqual(self.gallery.listing()['searches'],[])
        with self.assertRaises(ValueError):self.gallery.mutate({'action':'save_search','name':'bad','filters':{'date_basis':'nonsense'}})

    def test_new_routes_and_pwa_assets_require_auth_and_dates_filter_over_http(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        password='fixture-password-1234';app.configure_access(server,'https://photos.example.com',password)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        auth={'Authorization':'Basic '+base64.b64encode(('frame:'+password).encode()).decode()}
        try:
            for path in ('/api/gallery','/api/setup','/api/sync','/api/timeline','/api/near-duplicates','/sw.js','/manifest.webmanifest'):
                with self.assertRaises(HTTPError) as raised:urlopen(base+path)
                self.assertEqual(raised.exception.code,401)
            with urlopen(Request(base+'/api/images?date_basis=capture&from=2024-02-01&to=2024-02-29',headers=auth)) as response:
                self.assertEqual(json.load(response)['total'],1)
            with urlopen(Request(base+'/api/timeline?date_basis=capture',headers=auth)) as response:
                groups=json.load(response)['months'];self.assertEqual(groups,[{'month':'2024-02','count':1},{'month':None,'count':2}])
            with urlopen(Request(base+'/api/setup',headers=auth)) as response:
                setup=json.load(response);self.assertNotIn(password,json.dumps(setup));self.assertTrue(setup['access']['authenticated'])
            body=json.dumps({'action':'create_album','name':'HTTP album'}).encode()
            request=Request(base+'/api/gallery',data=body,headers={**auth,'Content-Type':'application/json'})
            with urlopen(request) as response:self.assertTrue(json.load(response)['ok'])
            with urlopen(Request(base+'/manifest.webmanifest',headers=auth)) as response:self.assertEqual(json.load(response)['display'],'standalone')
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
