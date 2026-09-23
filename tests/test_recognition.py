import base64
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import recognition
from people import People, image_revision
from recognition import Recognizer, ModelUnavailable
from PIL import Image
import numpy as np


def basis(index):
    vector = np.zeros(recognition.DIMENSIONS, dtype=np.float32)
    vector[index] = 1.0
    return vector


def near(vector, cos):
    other = basis(1)
    other = other - np.dot(other, vector) * vector
    other /= np.linalg.norm(other)
    return (cos * vector + (1.0 - cos ** 2) ** 0.5 * other).astype(np.float32)


def face(vector, quality=100.0):
    return {'region': {'x': 0.1, 'y': 0.1, 'width': 0.3, 'height': 0.3}, 'landmarks': [[0, 0]] * 5,
        'confidence': 0.9, 'quality': quality, 'embedding': vector.astype(np.float32)}


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data = app.DATA
        app.DATA = Path(self.temp.name)
        app.initialize()
        with app.db() as c:
            folder = c.execute("INSERT INTO folders(path) VALUES('fixture')").lastrowid
            for name in ['one', 'two', 'three']:
                c.execute('INSERT INTO images(folder_id,path,name,digest,size,mtime,extension) VALUES(?,?,?,?,?,?,?)',
                    (folder, name, name, name, 10, 1, 'png'))
            self.images = [dict(r) for r in c.execute('SELECT * FROM images ORDER BY id')]
        self.rec = Recognizer(app.DATA, app.db, lambda row: Image.new('RGB', (100, 80)), self.temp.name + '/missing.onnx')
        self.people = People(app.db)
        self.rec.action({'action': 'enable', 'enabled': True})

    def tearDown(self):
        app.DATA = self.old_data
        self.temp.cleanup()

    def process(self, image, vector):
        self.rec.encode = lambda picture, v=vector: [face(v)]
        self.rec.action({'action': 'detect', 'image_id': image['id'], 'revision': image_revision(image)})
        self.assertTrue(self.rec.process_one())

    def person_counts(self):
        return {p['id']: p['count'] for p in self.people.listing()['items']}

    def test_auto_grouping_links_high_confidence_matches(self):
        self.process(self.images[0], basis(0))
        self.process(self.images[1], basis(0))
        counts = self.person_counts()
        self.assertEqual(list(counts.values()), [2])
        self.assertEqual(self.rec.status()['faces'], 2)
        self.assertEqual(self.rec.status()['suggestions'], 0)
        statuses = [f['status'] for f in self.rec.image_faces(self.images[1]['id'])['items']]
        self.assertEqual(statuses, ['auto'])

    def test_low_confidence_is_suggested_and_reviewable(self):
        self.process(self.images[0], basis(0))
        self.process(self.images[1], near(basis(0), 0.42))
        self.assertEqual(self.person_counts(), {1: 1})
        pending = self.rec.suggestions()
        self.assertEqual(pending['total'], 1)
        self.assertEqual(pending['items'][0]['person_id'], 1)
        self.assertAlmostEqual(pending['items'][0]['match_score'], 0.42, places=4)
        self.assertEqual(self.rec.image_faces(self.images[1]['id'])['items'][0]['status'], 'suggested')
        self.rec.action({'action': 'confirm', 'face_id': pending['items'][0]['id']})
        self.assertEqual(self.person_counts(), {1: 2})
        self.assertEqual(self.rec.image_faces(self.images[1]['id'])['items'][0]['status'], 'confirmed')

    def test_rejecting_suggestion_keeps_album_unchanged(self):
        self.process(self.images[0], basis(0))
        self.process(self.images[1], near(basis(0), 0.42))
        suggestion = self.rec.suggestions()['items'][0]
        self.rec.action({'action': 'reject', 'face_id': suggestion['id']})
        self.assertEqual(self.person_counts(), {1: 1})
        self.assertEqual(self.rec.status()['suggestions'], 0)
        self.assertEqual(self.rec.image_faces(self.images[1]['id'])['items'][0]['status'], 'rejected')

    def test_distinct_face_creates_new_unknown_person(self):
        self.process(self.images[0], basis(0))
        self.process(self.images[1], basis(1))
        counts = self.person_counts()
        self.assertEqual(sorted(counts.values()), [1, 1])
        names = sorted(p['name'] for p in self.people.listing()['items'])
        self.assertEqual(names, ['Unknown 1', 'Unknown 2'])

    def test_clear_removes_recognition_but_keeps_manual_labels(self):
        manual = self.people.mutate({'action': 'create', 'name': 'Sam'})['id']
        self.people.mutate({'action': 'assign', 'person_id': manual,
            'images': [{'id': self.images[0]['id'], 'revision': image_revision(self.images[0])}]})
        self.process(self.images[1], basis(0))
        self.rec.action({'action': 'clear'})
        self.assertEqual(self.rec.status()['faces'], 0)
        self.assertEqual(self.rec.status()['total'], 0)
        self.assertEqual(self.person_counts()[manual], 1)

    def test_stale_source_is_not_published(self):
        def change_mid_inference(picture):
            with app.db() as c:
                c.execute("UPDATE images SET digest='new-content' WHERE id=?", (self.images[0]['id'],))
            return [face(basis(0))]
        self.rec.action({'action': 'detect', 'image_id': self.images[0]['id'], 'revision': image_revision(self.images[0])})
        self.rec.encode = change_mid_inference
        self.rec.process_one()
        self.assertEqual(self.rec.status(self.images[0]['id'])['image'], [])
        self.assertEqual(self.rec.status()['faces'], 0)

    def test_disabled_queue_and_threshold_validation(self):
        self.rec.action({'action': 'enable', 'enabled': False})
        with self.assertRaisesRegex(ValueError, 'Enable'):
            self.rec.action({'action': 'queue'})
        with self.assertRaises(ValueError):
            self.rec.action({'action': 'settings', 'auto_threshold': 0.3, 'review_threshold': 0.4})
        self.rec.action({'action': 'settings', 'auto_threshold': 0.6, 'review_threshold': 0.35})
        self.assertAlmostEqual(self.rec.status()['auto_threshold'], 0.6)

    def test_missing_model_is_reported(self):
        with self.assertRaises(ModelUnavailable):
            self.rec._session_model()

    def test_alignment_maps_landmarks_to_template(self):
        from recognition import align, similarity, ARCFACE_DST
        shifted = ARCFACE_DST + np.array([12.0, 7.0])
        matrix = similarity(shifted, ARCFACE_DST)
        projected = (matrix[:, :2] @ shifted.T).T + matrix[:, 2]
        self.assertTrue(np.allclose(projected, ARCFACE_DST, atol=0.5))
        self.assertEqual(align(np.zeros((200, 200, 3), np.uint8), shifted).shape, (112, 112, 3))

    def test_http_endpoints_require_authentication(self):
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        app.configure_access(server, 'https://photos.example.com', 'test-password-long-enough')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        headers = {'Authorization': 'Basic ' + base64.b64encode(b'frame:test-password-long-enough').decode()}
        previous = app.recognizer
        app.recognizer = self.rec
        try:
            for path in ['/api/recognition', '/api/recognition/suggestions']:
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + path)
                self.assertEqual(error.exception.code, 401)
            with urlopen(Request(base + '/api/recognition', headers=headers)) as response:
                data = json.load(response)
                self.assertIn('auto_threshold', data)
            with urlopen(Request(base + f"/api/recognition/faces?image_id={self.images[0]['id']}", headers=headers)) as response:
                self.assertIn('items', json.load(response))
        finally:
            app.recognizer = previous
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
