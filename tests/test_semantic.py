import hashlib
import io
import sys
import tempfile
import time
import performance
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from semantic import SemanticIndex, COLLECTION, DIMENSIONS, fingerprint, normalized
from PIL import Image
from qdrant_client import models


def vector(axis):
    result = [0.0] * DIMENSIONS
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
        self.index.performance.action({'action':'start','seconds':10})
        self.index.index_pending()
        diagnostic = self.index.performance.status()['jobs'][0]
        self.assertEqual(diagnostic['counts']['new_success'],2)
        self.assertEqual(diagnostic['counts']['reused'],1)
        self.assertEqual(diagnostic['counts']['visited'],3)
        self.assertEqual(self.index.status()['ready'], 3)
        self.assertEqual(self.encoder_mock.call_count, 2)
        result = self.index.search('red')
        self.assertTrue(result['items'][0]['name'].startswith('red'))
        self.assertEqual(result['items'][-1]['name'], 'blue.png')
        self.index.index_pending()
        self.assertEqual(self.index.performance.status()['jobs'][0]['counts']['skipped'],3)
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

    def test_delayed_inference_and_storage_are_attributed_separately(self):
        self.index.performance.action({'action':'start','seconds':10})
        def encode(picture):
            with performance.span('embedding'):
                time.sleep(.04);return vector(0)
        original=self.index.client.upsert
        def persist(*args,**kwargs):
            time.sleep(.03);return original(*args,**kwargs)
        self.encoder_mock.side_effect=encode
        with patch.object(self.index.client,'upsert',side_effect=persist):self.index.index_pending()
        job=self.index.performance.status()['jobs'][0];stages={s['stage']:s for s in job['stages']}
        self.assertGreaterEqual(stages['embedding']['total_ms'],80)
        self.assertGreaterEqual(stages['vector_upsert']['total_ms'],90)
        self.assertEqual(stages['embedding']['count'],2)
        self.assertEqual(stages['vector_upsert']['count'],3)
        self.assertLess(sum(s['total_ms'] for s in stages.values() if s['stage']!='image_total'),job['wall_ms']+1)
        self.assertEqual(self.index.status()['ready'],3)

    def test_diagnostics_count_concurrent_revision_discard(self):
        self.index.performance.action({'action':'start','seconds':10})
        def changed(picture):
            with app.db() as c:c.execute("UPDATE images SET digest='changed' WHERE name='blue.png'")
            return vector(0)
        self.encoder_mock.side_effect=changed;self.index.index_pending()
        job=self.index.performance.status()['jobs'][0]
        self.assertEqual(job['counts']['stale'],1)
        self.assertEqual(job['counts']['success'],2)
        with app.db() as c:blue=c.execute("SELECT id FROM images WHERE name='blue.png'").fetchone()[0]
        self.assertEqual(self.index.client.retrieve(self.index.collection,[blue]),[])

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

    def test_similar_reuses_vector_excludes_source_and_respects_filters(self):
        self.index.index_pending()
        with app.db() as conn:
            source = conn.execute("SELECT id FROM images WHERE name='red.png'").fetchone()[0]
            conn.execute("UPDATE images SET favorite=1 WHERE name='blue.png'")
        with patch.object(self.index, 'encode_text', side_effect=AssertionError('No inference needed')):
            result = self.index.similar(source)
            self.assertEqual(result['total'], 2)
            self.assertEqual(result['items'][0]['name'], 'red-copy.png')
            self.assertNotIn(source, [r['id'] for r in result['items']])
            filtered = self.index.similar(source, 'missing=0 AND favorite=1')
            self.assertEqual(filtered['items'][0]['name'], 'blue.png')
        Image.new('RGB', (40,30), 'green').save(self.photos / 'red.png')
        self.scan()
        with self.assertRaises(ValueError): self.index.similar(source)

    def test_visual_duplicate_candidates_review_and_content_invalidation(self):
        # Re-encoding a different file with the same visual vector suggests a pair;
        # byte-identical copies belong to the existing exact-checksum view.
        Image.new('RGB',(41,31),'red').save(self.photos/'red-copy.png')
        self.scan();self.index.index_pending()
        result=self.index.near_duplicates()
        self.assertEqual(len(result['pairs']),1)
        pair=result['pairs'][0]
        import gallery
        organization=gallery.Gallery(app.db)
        organization.mutate({'action':'review_duplicate','decision':'different',
            'photos':[{'id':p['id'],'revision':p['revision']} for p in (pair['left'],pair['right'])]})
        self.assertEqual(self.index.near_duplicates()['pairs'],[])
        Image.new('RGB',(42,32),'red').save(self.photos/'red-copy.png')
        self.scan();self.index.index_pending()
        self.assertEqual(len(self.index.near_duplicates()['pairs']),1)

    def test_failed_images_are_visible_and_retryable(self):
        with patch.object(self.index, 'load_image', side_effect=ValueError('Drive disconnected')):
            self.index.index_pending()
        self.assertEqual(self.index.status()['failed'], 3)
        self.assertEqual(self.index.search('red')['total'], 0)
        self.index.queue(retry_failed=True)
        self.index.index_pending()
        self.assertEqual(self.index.status()['ready'], 3)

    def test_embedding_job_history_counts_failures_and_omits_unchanged_checks(self):
        with patch.object(self.index,'load_image',side_effect=ValueError('Unavailable')):self.index.index_pending()
        with app.db() as conn:
            job=conn.execute("SELECT * FROM job_history WHERE kind='embeddings' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual((job['status'],job['processed'],job['failed_count']),('failed',3,3))
            self.assertGreaterEqual(job['finished'],job['started'])
        self.index.queue(retry_failed=True);self.index.index_pending();self.index.index_pending()
        with app.db() as conn:
            jobs=conn.execute("SELECT * FROM job_history WHERE kind='embeddings' ORDER BY id").fetchall()
            self.assertEqual(len(jobs),2);self.assertEqual(jobs[-1]['status'],'complete')
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
        self.index.performance.action({'action':'start','seconds':10})
        class SlowRead(io.BytesIO):
            def read(inner,*args):time.sleep(.025);return super().read(*args)
        with patch('drive.access_token',return_value='SECRET-TOKEN'),patch('drive.urlopen',return_value=SlowRead(raw)):
            with self.index.performance.job('embeddings'),self.index.load_image(row):pass
        diagnostic=self.index.performance.status()['jobs'][0]
        stages={s['stage']:s for s in diagnostic['stages']}
        self.assertGreaterEqual(stages['source_read']['total_ms'],25)
        self.assertIn('drive_auth',stages);self.assertEqual(diagnostic['metadata']['source'],'drive')
        with patch('drive.request', return_value=io.BytesIO(raw)) as request:
            with self.index.load_image(row) as picture:
                self.assertEqual(picture.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(request.call_args.args[1], 'files/drive-image')
        with patch('drive.request', return_value=io.BytesIO(raw)):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.index.load_image({**row, 'digest':'wrong-checksum'})

    def test_invalid_vectors_rejected(self):
        for item in [[0.0] * DIMENSIONS, [float('nan')] * DIMENSIONS, [1.0] * 3]:
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
