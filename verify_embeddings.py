"""Run a real CLIP/Qdrant smoke test without touching the personal catalog."""
import tempfile
from pathlib import Path
import app
from PIL import Image
from semantic import SemanticIndex


def main():
    old_data = app.DATA
    with tempfile.TemporaryDirectory(prefix='frame-clip-verification-') as temporary:
        app.DATA = Path(temporary) / 'data'
        app.initialize()
        pictures = Path(temporary) / 'pictures'
        pictures.mkdir()
        for name in ['red', 'blue', 'green']:
            Image.new('RGB', (400, 300), name).save(pictures / (name + '.png'))
        with app.db() as conn:
            folder = conn.execute('INSERT INTO folders(path) VALUES(?)', (str(pictures),)).lastrowid
        app.scan_lock.acquire()
        app.scan(folder)
        index = SemanticIndex(app.DATA, app.db, model_cache=old_data / 'models')
        try:
            index.index_pending()
            status = index.status()
            assert status['ready'] == 3, status
            for color in ['red', 'blue', 'green']:
                result = index.search('a solid ' + color + ' image')
                names = [(row['name'], round(row['score'], 4)) for row in result['items']]
                print(color + ': ' + str(names), flush=True)
                assert names[0][0] == color + '.png', names
            index.index_pending()
            assert index.status()['processed'] == 0
            print('PASS: real 512-dimensional CLIP embeddings, cosine ranking, and incremental resume.', flush=True)
        finally:
            index.close()
            app.DATA = old_data


if __name__ == '__main__':
    main()
