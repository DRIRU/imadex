"""Compare diagnostics modes in a temporary catalog/vector store, never personal data."""
import argparse
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-models',action='store_true',help='Use cached/downloaded local encoder instead of a 20 ms synthetic encoder.')
    parser.add_argument('--provider',choices=['auto','cpu','cuda'],default='cpu')
    parser.add_argument('--model',choices=['siglip2','clip'],default='siglip2')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--model-cache',type=Path,default=Path(__file__).parent/'data/models')
    args=parser.parse_args()
    if not 2<=args.repeats<=10:parser.error('repeats must be 2–10')
    os.environ['IMAGE_INDEX_MODEL']=args.model;os.environ['IMAGE_INDEX_PROVIDER']=args.provider
    import app
    import performance
    import numpy as np
    from PIL import Image
    from semantic import SemanticIndex, DIMENSIONS
    from qdrant_client import models
    previous=app.DATA
    with tempfile.TemporaryDirectory(prefix='imadex-performance-') as temporary:
        app.DATA=Path(temporary)/'data';app.initialize()
        photos=Path(temporary)/'fixtures';photos.mkdir()
        for i,color in enumerate(['red','blue','green','yellow','purple','orange']):Image.new('RGB',(640,480),color).save(photos/f'{i}.png')
        with app.db() as c:folder=c.execute('INSERT INTO folders(path) VALUES(?)',(str(photos),)).lastrowid
        app.scan_lock.acquire();app.scan(folder)
        with patch.dict(os.environ,{'QDRANT_URL':'','QDRANT_COLLECTION':'','QDRANT_API_KEY':''}):index=SemanticIndex(app.DATA,app.db,model_cache=args.model_cache)
        def synthetic(picture):
            with performance.span('embedding'):
                time.sleep(.02);value=[0.0]*DIMENSIONS;value[sum(picture.getpixel((0,0)))%DIMENSIONS]=1.0
                return value
        encoder=patch.object(index,'encode_image',side_effect=synthetic) if not args.real_models else patch.dict(os.environ,{})
        try:
            with encoder:
                index.index_pending() # Warm models; startup is intentionally outside the comparison.
                assert index.status()['ready']==6
                with app.db() as c:ids=[r[0] for r in c.execute('SELECT id FROM images')]
                reference={r.id:r.vector for r in index.client.retrieve(index.collection,ids,with_vectors=True)}
                durations={key:[] for key in ('off','summary','detail')};summaries=[]
                # Rotate order to reduce a simple warmup/order bias.
                for repeat in range(args.repeats):
                    modes=list(durations);modes=modes[repeat%3:]+modes[:repeat%3]
                    for mode in modes:
                        index.performance.action({'action':'settings','enabled':False})
                        index.client.delete(index.collection,points_selector=models.PointIdsList(points=ids),wait=True)
                        with app.db() as c:c.execute('DELETE FROM image_embeddings')
                        if mode=='summary':index.performance.action({'action':'settings','enabled':True})
                        elif mode=='detail':index.performance.action({'action':'start','seconds':600})
                        start=time.perf_counter();index.index_pending();durations[mode].append((time.perf_counter()-start)*1000)
                        index.performance.events.join()
                        actual=index.client.retrieve(index.collection,ids,with_vectors=True)
                        assert len(actual)==6 and index.status()['ready']==6
                        assert all(np.allclose(r.vector,reference[r.id],atol=1e-6) for r in actual)
                        if mode!='off':
                            summary=index.performance.status()['jobs'][0]
                            assert summary['counts']['new_success']==6
                            summaries.append(summary)
                medians={mode:round(statistics.median(samples),3) for mode,samples in durations.items()}
                overhead={mode:round((medians[mode]/medians['off']-1)*100,2) for mode in ('summary','detail')}
                print(json.dumps({'workload':'real_encoder' if args.real_models else 'synthetic_20ms_encoder','model':args.model,'requested_provider':args.provider,
                    'images_per_run':6,'repeats':args.repeats,'median_ms':medians,'samples_ms':durations,'overhead_percent':overhead,
                    'target_under_5_percent':all(v<5 for v in overhead.values()),'last_job':summaries[-1]},indent=2))
        finally:index.close();app.DATA=previous


if __name__=='__main__':main()
