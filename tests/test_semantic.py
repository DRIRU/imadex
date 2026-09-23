import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from semantic import SemanticIndex, COLLECTION, fingerprint, normalized
from PIL import Image
from qdrant_client import models


def vector(axis):
    result = [0.0] * 512
    result[axis] = 1.0
    return result


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_data = app.DATA
        app.DATA = Path(self.temporary.name) / 'data'
        app.initialize()
        self.photos = Path(self.temporary.name) / 'photos'
        self.photos.mkdir()
        with app.db() as conn:
            self.folder = conn.execute('INSERT INTO folders(path) VALUES(?)', (str(self.photos),)).lastrowid
        Image.new('RGB', (40, 30), 'red').save(self.photos / 'red.png')
        Image.new('RGB', (40, 30), 'blue').save(self.photos / 'blue.png')
        (self.photos / 'red-copy.png').write_bytes((self.photos / 'red.png').read_bytes())
        self.scan()
        self.index = SemanticIndex(app.DATA, app.db)
        self.encoder = patch.object(self.index, 'encode_image', side_effect=lambda picture: vector(0 if picture.getpixel((0, 0))[0] > 0 else 1))
        self.encoder_mock = self.encoder.start()
        self.text = patch.object(self.index, 'encode_text', side_effect=lambda text: vector(0 if text == 'red' else 1))
        self.text.start()

    def scan(self):
        app.scan_lock.acquire()
        app.scan(self.folder)

    def tearDown(self):
        self.encoder.stop()
        self.text.stop()
        self.index.close()
        app.DATA = self.old_data
        self.temporary.cleanup()

    def test_real_vector_store_ranking_duplicates_and_resume(self):
        self.index.index_pending()
        self.assertEqual(self.index.status()['ready'], 3)
        self.assertEqual(self.encoder_mock.call_count, 2)
        result = self.index.search('red')
        self.assertTrue(result['items'][0]['name'].startswith('red'))
        self.assertEqual(result['items'][-1]['name'], 'blue.png')
        self.index.index_pending()
        self.assertEqual(self.encoder_mock.call_count, 2)
        self.index.close()
        reopened = SemanticIndex(app.DATA, app.db)
        try:
            with patch.object(reopened, 'encode_image', side_effect=AssertionError('Should resume from disk')):
                reopened.index_pending()
            self.assertEqual(reopened.status()['ready'], 3)
        finally:
            reopened.close()

    def test_changed_file_excluded_until_reembedded_and_missing_removed(self):
        self.index.index_pending()
        Image.new('RGB', (40, 30), 'blue').save(self.photos / 'red.png')
        (self.photos / 'red-copy.png').unlink()
        self.scan()
        self.assertEqual(self.index.search('red')['total'], 1)
        self.assertEqual(self.index.status()['pending'], 1)
        self.index.index_pending()
        self.assertEqual(self.index.status()['ready'], 2)
        self.assertEqual(self.index.client.count(COLLECTION).count, 2)

    def test_favorites_filter_and_pagination(self):
        self.index.index_pending()
        with app.db() as conn:
            conn.execute("UPDATE images SET favorite=1 WHERE name='blue.png'")
        result = self.index.search('red', 'missing=0 AND favorite=1')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['name'], 'blue.png')
        page1 = self.index.search('red', limit=1)
        page2 = self.index.search('red', offset=1, limit=1)
        self.assertNotEqual(page1['items'][0]['id'], page2['items'][0]['id'])

    def test_failed_images_are_visible_and_retryable(self):
        with patch.object(self.index, 'load_image', side_effect=ValueError('Drive disconnected')):
            self.index.index_pending()
        self.assertEqual(self.index.status()['failed'], 3)
        self.assertEqual(self.index.search('red')['total'], 0)
        self.index.queue(retry_failed=True)
        self.index.index_pending()
        self.assertEqual(self.index.status()['ready'], 3)
        self.assertEqual(self.index.status()['failed'], 0)

    def test_recovers_vector_written_before_catalog_record(self):
        with app.db() as conn:
            row = conn.execute('SELECT * FROM images ORDER BY id LIMIT 1').fetchone()
        self.index.client.upsert(COLLECTION, [models.PointStruct(id=row['id'], vector=vector(0), payload={'fingerprint': fingerprint(row)})])
        self.index.index_pending()
        self.assertEqual(self.index.status()['ready'], 3)

    def test_drive_pixels_read_without_persisting_original(self):
        raw = (self.photos / 'red.png').read_bytes()
        row = {'size':len(raw),'drive_id':'drive-image','digest':hashlib.md5(raw).hexdigest()}
        with patch('drive.request', return_value=io.BytesIO(raw)) as request:
            with self.index.load_image(row) as picture:
                self.assertEqual(picture.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(request.call_args.args[1], 'files/drive-image')
        with patch('drive.request', return_value=io.BytesIO(raw)):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.index.load_image({**row, 'digest':'wrong-checksum'})

    def test_invalid_vectors_rejected(self):
        for item in [[0.0] * 512, [float('nan')] * 512, [1.0] * 3]:
            with self.assertRaises(ValueError):
                normalized(item)

    def test_manual_person_filter_intersects_vector_search(self):
        from people import People, image_revision, LIVE
        people = People(app.db)
        person = people.mutate({'action':'create','name':'Manual label'})['id']
        with app.db() as conn:
            row = conn.execute("SELECT * FROM images WHERE name='blue.png'").fetchone()
        people.mutate({'action':'assign','person_id':person,'images':[{'id':row['id'],'revision':image_revision(row)}]})
        self.index.index_pending()
        where = 'missing=0 AND id IN (SELECT ip.image_id FROM image_people ip JOIN images i ON i.id=ip.image_id WHERE ip.person_id=? AND '+LIVE+')'
        result = self.index.search('red', where, [person])
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['name'],'blue.png')


if __name__ == '__main__':
    unittest.main()
