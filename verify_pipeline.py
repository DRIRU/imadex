"""Compare serial vs overlapped Drive reads using temporary stores and simulated I/O."""
import argparse
import hashlib
import io
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-models',action='store_true')
    parser.add_argument('--provider',choices=['cpu','cuda','auto'],default='cpu')
    parser.add_argument('--model',choices=['siglip2','clip'],default='siglip2')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--request-delay',type=float,default=.08)
    parser.add_argument('--upsert-delay',type=float,default=.04)
    args=parser.parse_args()
    if not 2<=args.repeats<=10 or not 0<=args.request_delay<=2 or not 0<=args.upsert_delay<=2:
        parser.error('Use 2–10 repeats and delays between 0 and 2 seconds.')
    os.environ['IMAGE_INDEX_MODEL']=args.model;os.environ['IMAGE_INDEX_PROVIDER']=args.provider
    import app
    import performance
    import numpy as np
    from PIL import Image
    from semantic import SemanticIndex,DIMENSIONS
    from qdrant_client import models
    original_data=app.DATA
    with tempfile.TemporaryDirectory(prefix='imadex-pipeline-') as temporary:
        app.DATA=Path(temporary)/'data';app.initialize()
        photos=Path(temporary)/'fixtures';photos.mkdir()
        for i in range(8):Image.new('RGB',(640,480),(i*27,80,160)).save(photos/f'{i}.png')
        with app.db() as c:folder=c.execute('INSERT INTO folders(path) VALUES(?)',(str(photos),)).lastrowid
        app.scan_lock.acquire();app.scan(folder);raws={}
        with app.db() as c:
            for row in c.execute('SELECT * FROM images').fetchall():
                raw=Path(row['path']).read_bytes();key='fixture-'+str(row['id']);raws[key]=raw
                c.execute('UPDATE images SET drive_id=?,digest=? WHERE id=?',(key,hashlib.md5(raw).hexdigest(),row['id']))
        with patch.dict(os.environ,{'QDRANT_URL':'','QDRANT_COLLECTION':'','QDRANT_API_KEY':''}):
            index=SemanticIndex(app.DATA,app.db,model_cache=original_data/'models')
        def request(data,route,params):
            time.sleep(args.request_delay);return io.BytesIO(raws[route.split('/')[-1]])
        def encode(picture):
            with performance.span('embedding'):
                time.sleep(.02);value=[0.0]*DIMENSIONS;value[picture.getpixel((0,0))[0]]=1.0;return value
        upsert=index.client.upsert
        def persist(*a,**kw):time.sleep(args.upsert_delay);return upsert(*a,**kw)
        encoder=patch.object(index,'encode_image',side_effect=encode) if not args.real_models else patch.dict(os.environ,{})
        try:
            with patch('drive.request',side_effect=request),patch.object(index.client,'upsert',side_effect=persist),encoder:
                index.index_pending() # Load/warm the same encoder before comparison.
                ids=[row['id'] for row in index.rows()]
                reference={p.id:p.vector for p in index.client.retrieve(index.collection,ids,with_vectors=True)}
                durations={'serial':[],'optimized':[]};summaries={}
                for repeat in range(args.repeats):
                    for mode in (['serial','optimized'] if repeat%2==0 else ['optimized','serial']):
                        index.client.delete(index.collection,points_selector=models.PointIdsList(points=ids),wait=True)
                        with app.db() as c:c.execute('DELETE FROM image_embeddings')
                        index.prefetch_workers=0 if mode=='serial' else 2
                        index.write_batch_size=1 if mode=='serial' else 4
                        index.performance.action({'action':'start','seconds':600})
                        start=time.perf_counter();index.index_pending();durations[mode].append((time.perf_counter()-start)*1000)
                        index.performance.events.join()
                        actual=index.client.retrieve(index.collection,ids,with_vectors=True)
                        assert len(actual)==8 and index.status()['ready']==8
                        assert all(np.allclose(p.vector,reference[p.id],atol=1e-6) for p in actual)
                        summaries[mode]=index.performance.status()['jobs'][0]
                medians={k:round(statistics.median(v),3) for k,v in durations.items()}
                print(json.dumps({'workload':'real_encoder_simulated_drive' if args.real_models else 'synthetic_encoder_simulated_drive',
                    'model':args.model,'provider':args.provider,'images_per_run':8,'repeats':args.repeats,
                    'simulated_request_ms':args.request_delay*1000,'simulated_upsert_call_ms':args.upsert_delay*1000,
                    'median_ms':medians,'samples_ms':durations,'speedup':round(medians['serial']/medians['optimized'],3),
                    'summaries':summaries,'note':'Simulated Drive latency; local Qdrant still commits each point. This is not a personal Drive/CUDA speed prediction.'},indent=2))
        finally:index.close();app.DATA=original_data


if __name__=='__main__':main()
