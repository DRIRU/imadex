"""Run a real ArcFace smoke test without touching the personal catalog."""
import io
import urllib.request

from PIL import Image

import app
import recognition
from recognition import Recognizer

FIXTURE = 'https://raw.githubusercontent.com/deepinsight/insightface/master/python-package/insightface/data/images/t1.jpg'


def main():
    model = app.DATA / 'models' / recognition.MODEL_FILE
    if not model.exists():
        print('ArcFace model not found. Run: python download_arcface.py')
        return
    with urllib.request.urlopen(FIXTURE, timeout=60) as response:
        image = Image.open(io.BytesIO(response.read())).convert('RGB')
    recognizer = Recognizer(app.DATA, app.db, lambda row: image)
    faces = recognizer.encode(image)
    assert faces, 'No faces were detected in the fixture image.'
    vectors = [face['embedding'] for face in faces]
    for vector in vectors:
        assert vector.shape == (recognition.DIMENSIONS,), vector.shape
        assert abs(float((vector ** 2).sum()) - 1.0) < 1e-3, 'Embedding is not unit length.'
    repeat = recognizer.encode(image)
    same = float(vectors[0] @ repeat[0]['embedding'])
    print(f'detected faces: {len(faces)}; self-similarity: {same:.4f}', flush=True)
    assert same > 0.99, same
    if len(vectors) > 1:
        print('cross-face similarity:', round(float(vectors[0] @ vectors[1]), 4))
    print('PASS: real 512-dimensional ArcFace embeddings are stable and normalized.')


if __name__ == '__main__':
    main()
