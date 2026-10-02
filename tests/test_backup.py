import sys
import sqlite3
import tempfile
import unittest
import zipfile
import os
from unittest.mock import patch
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import backup


class BackupTests(unittest.TestCase):
    def test_local_vectors_restore_and_live_copy_is_rejected(self):
        from qdrant_client import QdrantClient,models
        with tempfile.TemporaryDirectory() as temporary,patch.dict(os.environ,{'QDRANT_URL':''}):
            old=app.DATA;root=Path(temporary);app.DATA=root/'data'
            try:
                app.initialize();client=QdrantClient(path=str(app.DATA/'vectors'))
                client.create_collection('fixture',vectors_config=models.VectorParams(size=3,distance=models.Distance.COSINE))
                client.upsert('fixture',[models.PointStruct(id=1,vector=[1.0,0.0,0.0],payload={'kind':'fixture'})])
                try:
                    with self.assertRaisesRegex(ValueError,'Stop the app'):backup.create(app.DATA,root/'busy.zip',include_vectors=True)
                    self.assertFalse((root/'busy.zip').exists())
                finally:client.close()
                backup.create(app.DATA,root/'vectors.zip',include_vectors=True);backup.restore(root/'vectors.zip',root/'restored')
                restored=QdrantClient(path=str(root/'restored/vectors'))
                try:self.assertEqual(restored.retrieve('fixture',[1],with_vectors=True)[0].vector,[1.0,0.0,0.0])
                finally:restored.close()
            finally:app.DATA=old

    def test_roundtrip_restores_metadata_excludes_secrets_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            old=app.DATA;root=Path(temporary);app.DATA=root/'data'
            try:
                app.initialize()
                with app.db() as c:c.execute("INSERT INTO albums(name,created) VALUES('Family',0)")
                (app.DATA/'google-token.json').write_text('SECRET')
                archive=root/'backup.zip';backup.create(app.DATA,archive)
                backup.restore(archive,root/'restored')
                with closing(sqlite3.connect(root/'restored/catalog.sqlite3')) as c:self.assertEqual(c.execute('SELECT name FROM albums').fetchone()[0],'Family')
                self.assertFalse((root/'restored/google-token.json').exists())
                with self.assertRaises(ValueError):backup.restore(archive,root/'restored')
            finally:app.DATA=old

    def test_corruption_rejected_before_target_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            old=app.DATA;root=Path(temporary);app.DATA=root/'data'
            try:
                app.initialize();backup.create(app.DATA,root/'valid.zip')
                with zipfile.ZipFile(root/'valid.zip') as source,zipfile.ZipFile(root/'bad.zip','w') as target:
                    target.writestr('manifest.json',source.read('manifest.json'));target.writestr('catalog.sqlite3',b'corrupt')
                with self.assertRaises(ValueError):backup.restore(root/'bad.zip',root/'restored')
                self.assertFalse((root/'restored').exists())
            finally:app.DATA=old


if __name__=='__main__':unittest.main()
