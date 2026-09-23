import json
import base64
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from PIL import Image


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data = app.DATA
        app.DATA = Path(self.temp.name) / 'catalog'
        app.initialize()
        self.root = Path(self.temp.name) / 'pictures'
        self.root.mkdir()
        with app.db() as conn:
            self.folder = conn.execute('INSERT INTO folders(path) VALUES(?)', (str(self.root),)).lastrowid

    def tearDown(self):
        app.DATA = self.old_data
        self.temp.cleanup()

    def scan(self):
        app.scan_lock.acquire()
        app.scan(self.folder)

    def test_scan_duplicates_metadata_preservation_and_missing(self):
        Image.new('RGB', (120, 80), 'red').save(self.root / 'first.jpg')
        (self.root / 'copy.jpg').write_bytes((self.root / 'first.jpg').read_bytes())
        (self.root / 'broken.jpg').write_bytes(b'not an image')
        self.scan()
        with app.db() as conn:
            rows = conn.execute('SELECT * FROM images').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['digest'], rows[1]['digest'])
            self.assertEqual(rows[0]['width'], 120)
            conn.execute("UPDATE images SET favorite=1,tags='holiday'")
        self.scan()
        with app.db() as conn:
            self.assertEqual(conn.execute('SELECT SUM(favorite) FROM images').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT tags FROM images').fetchone()[0], 'holiday')
        (self.root / 'copy.jpg').unlink()
        self.scan()
        with app.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM images WHERE missing=1').fetchone()[0], 1)

    def test_drive_scan_and_failure_keep_prior_images(self):
        records = [{'id':'abc', 'name':'sky.jpg', 'mimeType':'image/jpeg', 'size':'123',
            'modifiedTime':'2026-01-01T00:00:00Z', 'md5Checksum':'abcd', 'imageMediaMetadata':{'width':800,'height':600}}]
        with patch('drive.walk', return_value=iter(records)):
            app.scan_drive(self.folder, 'root')
        with app.db() as conn:
            row = conn.execute('SELECT * FROM images').fetchone()
            self.assertEqual(row['drive_id'], 'abc')
            conn.execute("UPDATE images SET favorite=1,tags='blue'")
        records[0]['name'] = 'renamed.jpg'
        with patch('drive.walk', return_value=iter(records)):
            app.scan_drive(self.folder, 'root')
        with patch('drive.walk', side_effect=ValueError('Disconnected')):
            with self.assertRaises(ValueError):
                app.scan_drive(self.folder, 'root')
        with app.db() as conn:
            row = conn.execute('SELECT * FROM images').fetchone()
            self.assertEqual((row['name'],row['favorite'],row['tags'],row['missing']), ('renamed.jpg',1,'blue',0))

    def test_http_search_and_cross_origin_protection(self):
        Image.new('RGB', (10, 20), 'blue').save(self.root / 'blue.png')
        self.scan()
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/api/images?q=blue') as response:
                self.assertEqual(json.load(response)['total'], 1)
            for path, headers in [('/api/library', {'Host':'evil.example'}), ('/api/library', {'Origin':'https://evil.example'})]:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + path, headers=headers))
                self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + '/data/google-token.json')
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_oauth_invalid_state(self):
        with self.assertRaises(ValueError):
            app.drive.callback(app.DATA, {'state':'invalid','code':'wrong'})

    def test_tunnel_requires_auth_for_pages_api_and_images(self):
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        password = 'test-only-password-123456'
        app.configure_access(server, 'https://photos.example.com', password)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        authorization = 'Basic ' + base64.b64encode(('frame:' + password).encode()).decode()
        try:
            for path in ['/', '/api/library', '/thumb/1', '/image/1', '/app.js']:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + path, headers={'Host':'photos.example.com'}))
                self.assertEqual(caught.exception.code, 401)
            # Rewriting Host to localhost at a proxy must never bypass authentication.
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + '/api/library')
            self.assertEqual(caught.exception.code, 401)
            headers = {'Host':'photos.example.com','Authorization':authorization,'Origin':'https://photos.example.com'}
            with urlopen(Request(base + '/api/library', headers=headers)) as response:
                self.assertIn('stats', json.load(response))
            for changes in [{'Authorization':'Basic wrong'}, {'Origin':'https://evil.example'}, {'Host':'evil.example'}]:
                with self.assertRaises(HTTPError):
                    urlopen(Request(base + '/api/library', headers={**headers,**changes}))
            with urlopen(Request(base + '/api/drive', headers=headers)) as response:
                self.assertFalse(json.load(response)['can_connect'])
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base + '/api/drive/connect', data=b'{}', headers={**headers,'Content-Type':'application/json'}))
            self.assertEqual(caught.exception.code, 400)
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base + '/api/scan', data=b'{}', headers={'Host':'photos.example.com','Content-Type':'application/json'}))
            self.assertEqual(caught.exception.code, 401)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_tunnel_configuration_rejects_http_and_weak_passwords(self):
        from types import SimpleNamespace
        for origin,password in [('http://photos.example.com','long-enough-password'),('https://photos.example.com','short'),('https://photos.example.com/path','long-enough-password')]:
            with self.assertRaises(ValueError):
                app.configure_access(SimpleNamespace(), origin, password)


if __name__ == '__main__':
    unittest.main()
