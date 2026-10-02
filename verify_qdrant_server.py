"""Verify an explicitly selected Qdrant server with a unique temporary collection."""
import argparse
import os
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def main(url,real_models=False):
    import app
    import semantic
    from PIL import Image
    from qdrant_client import QdrantClient
    from gallery import Gallery
    from people import image_revision
    collection='imadex_verification_'+uuid.uuid4().hex
    old_data=app.DATA
    with tempfile.TemporaryDirectory(prefix='imadex-qdrant-verification-') as temporary,patch.dict(os.environ,{'QDRANT_URL':url,'QDRANT_COLLECTION':collection}):
        app.DATA=Path(temporary)/'data';app.initialize()
        photos=Path(temporary)/'photos';photos.mkdir()
        for name in ('red','blue'):Image.new('RGB',(40,30),name).save(photos/(name+'.png'))
        with app.db() as c:folder=c.execute('INSERT INTO folders(path) VALUES(?)',(str(photos),)).lastrowid
        app.scan_lock.acquire();app.scan(folder)
        index=None
        try:
            index=semantic.SemanticIndex(app.DATA,app.db,model_cache=old_data/'models')
            def vector(axis):return [float(i==axis) for i in range(semantic.DIMENSIONS)]
            with ExitStack() as stack:
                if not real_models:
                    stack.enter_context(patch.object(index,'encode_image',side_effect=lambda picture:vector(0 if picture.getpixel((0,0))[0] else 1)))
                    stack.enter_context(patch.object(index,'encode_text',return_value=vector(0)))
                index.index_pending();assert index.status()['ready']==2,index.status()
                result=index.search('a solid red image');assert result['items'][0]['name']=='red.png',result
                red=result['items'][0]['id'];blue=result['items'][1]['id']
                with app.db() as c:c.execute('UPDATE images SET favorite=1 WHERE id=?',(blue,))
                assert index.similar(red,'missing=0 AND favorite=1')['items'][0]['id']==blue
                album=Gallery(app.db).mutate({'action':'create_album','name':'Fixture'})['id']
                Gallery(app.db).mutate({'action':'bulk','photos':[{'id':red,'revision':image_revision(result['items'][0])}],'album_id':album})
                assert index.search('red','missing=0 AND id IN (SELECT image_id FROM album_images WHERE album_id=?)',(album,))['total']==1
                index.index_pending();assert index.status()['processed']==0
            index.close();index=None
            index=semantic.SemanticIndex(app.DATA,app.db)
            with patch.object(index,'encode_image',side_effect=AssertionError('Should recover server vectors')):
                index.index_pending();assert index.status()['ready']==2
            with app.db() as c:c.execute('UPDATE images SET missing=1 WHERE id=?',(blue,))
            index.index_pending();assert index.client.count(collection).count==1
            print(f'PASS: live server, {semantic.DIMENSIONS}-d vector writes/ranking, gallery filters, image similarity, unchanged resume, reopen and missing-vector cleanup'+(' with real local encoder inference.' if real_models else ' with synthetic vectors.'),flush=True)
        finally:
            if index:index.close()
            # Only the unique collection created by this invocation is removed.
            cleanup=QdrantClient(url=url,api_key=os.environ.get('QDRANT_API_KEY') or None)
            try:
                if cleanup.collection_exists(collection):cleanup.delete_collection(collection)
            finally:cleanup.close();app.DATA=old_data


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',required=True,help='Explicit HTTP(S) server address. Uses QDRANT_API_KEY if configured.')
    parser.add_argument('--real-models',action='store_true',help='Also run the selected local encoder (uses the project model cache).')
    args=parser.parse_args();main(args.url,args.real_models)
