import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from semantic import COLLECTION, DIMENSIONS, SemanticIndex


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
        self.assertEqual(client.created, COLLECTION)
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

    def test_existing_collection_wrong_dimension_is_preserved_and_rejected(self):
        from qdrant_client import models
        info=SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=models.VectorParams(size=DIMENSIONS+1,distance=models.Distance.COSINE))))
        with patch.object(FakeClient,'collection_exists',return_value=True),patch.object(FakeClient,'get_collection',return_value=info,create=True):
            with self.assertRaisesRegex(ValueError,'incompatible'):SemanticIndex(app.DATA,app.db)
        self.assertIsNone(self.recorded['client'].created)

    def test_credentials_in_url_are_rejected_without_echoing_secrets(self):
        for url in ('https://user:secret@qdrant.example','https://qdrant.example?key=secret'):
            with patch.dict(os.environ,{'QDRANT_URL':url}):
                with self.assertRaises(ValueError) as raised:SemanticIndex(app.DATA,app.db)
                self.assertNotIn('secret',str(raised.exception))


if __name__ == '__main__':
    unittest.main()
