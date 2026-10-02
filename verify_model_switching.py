"""Exercise SigLIP 2 → CLIP → SigLIP 2 with real models and a shared temporary catalog."""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def child(data,cache,expected):
    import app
    from semantic import SemanticIndex
    app.DATA=Path(data);app.initialize()
    index=SemanticIndex(app.DATA,app.db,model_cache=cache)
    try:
        started=time.monotonic();index.index_pending();status=index.status()
        assert status['ready']==2,status
        assert status['processed']==expected,status
        for color in ('red','blue'):
            result=index.search('a solid '+color+' image')
            assert result['items'][0]['name']==color+'.png',result
        print(f"PASS {status['model']}: {status['processed']} processed; {time.monotonic()-started:.2f}s; {status['provider']}",flush=True)
    finally:index.close()


def main():
    import app
    from PIL import Image
    cache=app.DATA/'models'
    with tempfile.TemporaryDirectory(prefix='imadex-switch-verification-') as temporary:
        root=Path(temporary);app.DATA=root/'data';app.initialize()
        photos=root/'photos';photos.mkdir()
        for color in ('red','blue'):Image.new('RGB',(400,300),color).save(photos/(color+'.png'))
        with app.db() as c:folder=c.execute('INSERT INTO folders(path) VALUES(?)',(str(photos),)).lastrowid
        app.scan_lock.acquire();app.scan(folder)
        for model,count in [('siglip2',2),('clip',2),('siglip2',0)]:
            env=dict(os.environ,IMAGE_INDEX_MODEL=model,QDRANT_URL='',QDRANT_COLLECTION='',QDRANT_API_KEY='')
            subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--child',str(app.DATA),str(cache),str(count)],env=env,check=True)
        print('PASS: model switching preserves distinct collections and reuses the original SigLIP 2 vectors.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--child',nargs=3);args=parser.parse_args()
    if args.child:child(args.child[0],args.child[1],int(args.child[2]))
    else:main()
