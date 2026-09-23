import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from semantic import SemanticIndex


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.created = None

    def collection_exists(self, name):
        return False

    def create_collection(self, name, **kwargs):
        self.created = name

    def close(self):
        pass


class QdrantConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = app.DATA
        app.DATA = Path(self.temp.name)
        app.initialize()
        self.recorded = {}
        self.factory = self._factory
        self.patch = patch('semantic.QdrantClient', self.factory)
        self.patch.start()

    def _factory(self, **kwargs):
        client = FakeClient(**kwargs)
        self.recorded['client'] = client
        return client

    def tearDown(self):
        self.patch.stop()
        app.DATA = self.old
        self.temp.cleanup()

    def test_external_server_uses_url_and_api_key(self):
        environment = {'QDRANT_URL': 'http://qdrant:6333', 'QDRANT_API_KEY': 'secret', 'QDRANT_COLLECTION': ''}
        with patch.dict(os.environ, environment):
            index = SemanticIndex(app.DATA, app.db)
            index.close()
        client = self.recorded['client']
        self.assertEqual(client.kwargs.get('url'), 'http://qdrant:6333')
        self.assertEqual(client.kwargs.get('api_key'), 'secret')
        self.assertNotIn('path', client.kwargs)
        self.assertEqual(client.created, 'images_clip_b32_v1')
        self.assertIn('Qdrant server', index.database)

    def test_custom_collection_name(self):
        environment = {'QDRANT_URL': 'http://qdrant:6333', 'QDRANT_COLLECTION': 'my_images'}
        with patch.dict(os.environ, environment):
            SemanticIndex(app.DATA, app.db).close()
        self.assertEqual(self.recorded['client'].created, 'my_images')

    def test_local_mode_is_the_default(self):
        environment = {'QDRANT_URL': '', 'QDRANT_API_KEY': '', 'QDRANT_COLLECTION': ''}
        with patch.dict(os.environ, environment):
            index = SemanticIndex(app.DATA, app.db)
            index.close()
        client = self.recorded['client']
        self.assertIn('path', client.kwargs)
        self.assertNotIn('url', client.kwargs)
        self.assertEqual(index.database, 'Qdrant local')


if __name__ == '__main__':
    unittest.main()
