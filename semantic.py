"""Local image/text inference (SigLIP 2 or CLIP) and persistent Qdrant vector search."""
import hashlib
import io
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from qdrant_client import QdrantClient, models

import accel
import drive

MODELS = {
    'clip': {'vision': 'Qdrant/clip-ViT-B-32-vision', 'text': 'Qdrant/clip-ViT-B-32-text', 'dimensions': 512,
             'version': 'clip-vit-b32-fastembed-0.8.1-rgb-v1', 'collection': 'images_clip_b32_v1'},
    'siglip2': {'vision': 'google/siglip2-base-patch16-224', 'text': 'google/siglip2-base-patch16-224',
                'dimensions': 768, 'version': 'siglip2-base-patch16-224-fastembed-0.8.1-rgb-v1',
                'collection': 'images_siglip2_base_v1'},
}
DEFAULT_MODEL = 'siglip2'
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def select(name):
    key = (name or DEFAULT_MODEL).strip().lower()
    if key not in MODELS:
        raise ValueError('Unknown image model "' + str(name) + '". Choose one of: ' + ', '.join(sorted(MODELS)))
    return MODELS[key]


ACTIVE = select(os.environ.get('IMAGE_INDEX_MODEL'))
VISION_MODEL = ACTIVE['vision']
TEXT_MODEL = ACTIVE['text']
MODEL_VERSION = ACTIVE['version']
COLLECTION = ACTIVE['collection']
DIMENSIONS = ACTIVE['dimensions']


class ModelUnavailable(ValueError):
    pass


def fingerprint(row):
    # Content checksums ignore renames. Drive's fallback ID also needs size/mtime.
    content = row['digest']
    if row['drive_id'] and content == row['drive_id']:
        content += ':' + str(row['size']) + ':' + str(row['mtime'])
    return hashlib.sha256((MODEL_VERSION + ':' + content).encode()).hexdigest()


def normalized(vector):
    value = np.asarray(vector, dtype=np.float32)
    if value.shape != (DIMENSIONS,) or not np.isfinite(value).all():
        raise ValueError('The embedding model returned an invalid vector.')
    length = np.linalg.norm(value)
    if length <= 0:
        raise ValueError('The embedding model returned an empty vector.')
    return (value / length).tolist()


class SemanticIndex:
    def __init__(self, data, db, model_cache=None):
        self.data, self.db = Path(data), db
        self.model_cache = Path(model_cache) if model_cache else self.data / 'models'
        self.vector_lock = threading.RLock()
        self.model_lock = threading.RLock()
        self.state_lock = threading.Lock()
        self.job_lock = threading.Lock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.thread = None
        self.image_model = self.text_model = None
        self.query_cache = OrderedDict()
        self.progress = {'running': False, 'processed': 0, 'message': 'Visual index ready', 'error': None}
        with db() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS image_embeddings(
                image_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, model TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, updated REAL NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS embeddings_fingerprint ON image_embeddings(fingerprint)')
        self.collection = os.environ.get('QDRANT_COLLECTION') or COLLECTION
        self.database, self.client = self._connect()
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(size=DIMENSIONS, distance=models.Distance.COSINE))

    def _connect(self):
        url = os.environ.get('QDRANT_URL', '').strip()
        if url:
            return ('Qdrant server (' + url + ')', QdrantClient(url=url, api_key=os.environ.get('QDRANT_API_KEY') or None,
                timeout=float(os.environ.get('QDRANT_TIMEOUT', 60))))
        return ('Qdrant local', QdrantClient(path=str(self.data / 'vectors'), force_disable_check_same_thread=True))

    def update(self, **values):
        with self.state_lock:
            self.progress.update(values)

    def ensure_models(self, vision=True, text=True):
        from fastembed import ImageEmbedding, TextEmbedding
        with self.model_lock:
            options = {'cache_dir': str(self.model_cache), 'threads': min(4, os.cpu_count() or 1), 'providers': accel.providers()}
            try:
                if vision and self.image_model is None:
                    self.update(message='Loading image model (first use downloads model weights)…')
                    self.image_model = ImageEmbedding(VISION_MODEL, **options)
                if text and self.text_model is None:
                    self.update(message='Loading text model (first use downloads model weights)…')
                    self.text_model = TextEmbedding(TEXT_MODEL, **options)
            except Exception as error:
                raise ModelUnavailable('Could not load CLIP. Check the internet connection/model cache and retry: ' + str(error)) from error

    def encode_image(self, picture):
        with self.model_lock:
            self.ensure_models(text=False)
            return normalized(next(self.image_model.embed([picture], batch_size=1)))

    def encode_text(self, text):
        text = text.strip()
        if not text or len(text) > 500:
            raise ValueError('Enter a description between 1 and 500 characters.')
        with self.model_lock:
            if text in self.query_cache:
                self.query_cache.move_to_end(text)
                return self.query_cache[text]
            self.ensure_models(vision=False)
            vector = normalized(next(self.text_model.embed([text], batch_size=1)))
            self.query_cache[text] = vector
            if len(self.query_cache) > 128:
                self.query_cache.popitem(last=False)
            return vector

    def load_image(self, row):
        if row['size'] > MAX_IMAGE_BYTES:
            raise ValueError('Image exceeds the 64 MB embedding limit.')
        if row['drive_id']:
            stream = drive.request(self.data, 'files/' + row['drive_id'], {'alt': 'media', 'supportsAllDrives': 'true'})
        else:
            stream = Path(row['path']).open('rb')
        with stream:
            raw = stream.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('Image exceeds the 64 MB embedding limit.')
        if row['drive_id']:
            if row['digest'] != row['drive_id'] and hashlib.md5(raw).hexdigest() != row['digest']:
                raise ValueError('Image changed in Drive. Rescan the folder before indexing.')
        elif hashlib.sha256(raw).hexdigest() != row['digest']:
            raise ValueError('Image changed on disk. Rescan the folder before indexing.')
        with Image.open(io.BytesIO(raw)) as image:
            image.seek(0)
            picture = ImageOps.exif_transpose(image)
            if picture.mode in ('RGBA', 'LA') or (picture.mode == 'P' and 'transparency' in picture.info):
                rgba = picture.convert('RGBA')
                picture = Image.new('RGB', rgba.size, 'white')
                picture.paste(rgba, mask=rgba.getchannel('A'))
            return picture.convert('RGB')

    def record(self, image_id, signature, status, error=None):
        with self.db() as conn:
            conn.execute('''INSERT INTO image_embeddings VALUES(?,?,?,?,?,?) ON CONFLICT(image_id) DO UPDATE SET
                fingerprint=excluded.fingerprint,model=excluded.model,status=excluded.status,error=excluded.error,updated=excluded.updated''',
                (image_id, signature, MODEL_VERSION, status, error, time.time()))

    def rows(self):
        with self.db() as conn:
            return [dict(row) for row in conn.execute('''SELECT i.*,e.fingerprint AS embedded_fingerprint,e.status AS embedding_status,
                e.error AS embedding_error FROM images i LEFT JOIN image_embeddings e ON e.image_id=i.id WHERE i.missing=0''')]

    def status(self):
        rows = self.rows()
        ready = failed = 0
        errors = []
        for row in rows:
            if row['embedded_fingerprint'] != fingerprint(row):
                continue
            if row['embedding_status'] == 'ready':
                ready += 1
            elif row['embedding_status'] == 'failed':
                failed += 1
                errors.append(row['name'] + ': ' + str(row['embedding_error']))
        with self.state_lock:
            progress = dict(self.progress)
        return {**progress, 'total': len(rows), 'ready': ready, 'pending': len(rows) - ready - failed,
                'failed': failed, 'errors': errors[-20:], 'model': MODEL_VERSION, 'dimensions': DIMENSIONS,
                'database': self.database, 'provider': accel.label()}

    def queue(self, retry_failed=False):
        if retry_failed:
            with self.db() as conn:
                conn.execute("UPDATE image_embeddings SET status='pending',error=NULL WHERE status='failed'")
        self.wake.set()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.work, daemon=True, name='image-embeddings')
            self.thread.start()
        self.queue()

    def work(self):
        while not self.stop.is_set():
            self.wake.wait()
            self.wake.clear()
            if self.stop.is_set():
                break
            try:
                self.index_pending()
            except Exception as error:
                self.update(running=False, error=str(error), message='Visual indexing stopped. Retry after fixing the error.')

    def index_pending(self):
        if not self.job_lock.acquire(blocking=False):
            return
        self.update(running=True, processed=0, error=None, message='Checking image embeddings…')
        processed = 0
        try:
            # Remove vectors for unavailable files, including interrupted previous scans.
            with self.db() as conn:
                gone = [r[0] for r in conn.execute('''SELECT e.image_id FROM image_embeddings e
                    LEFT JOIN images i ON i.id=e.image_id WHERE i.id IS NULL OR i.missing=1''')]
            if gone:
                with self.vector_lock:
                    self.client.delete(self.collection, points_selector=models.PointIdsList(points=gone))
                with self.db() as conn:
                    conn.executemany('DELETE FROM image_embeddings WHERE image_id=?', ((item,) for item in gone))
            for row in self.rows():
                if self.stop.is_set():
                    break
                signature = fingerprint(row)
                if row['embedded_fingerprint'] == signature and row['embedding_status'] == 'failed':
                    continue
                with self.vector_lock:
                    points = self.client.retrieve(self.collection, [row['id']], with_payload=True)
                if points and points[0].payload.get('fingerprint') == signature:
                    if row['embedding_status'] != 'ready' or row['embedded_fingerprint'] != signature:
                        self.record(row['id'], signature, 'ready')
                    continue
                self.record(row['id'], signature, 'pending')
                self.update(message='Embedding ' + row['name'])
                try:
                    # Identical image bytes can reuse an existing vector.
                    with self.db() as conn:
                        duplicate = conn.execute("SELECT image_id FROM image_embeddings WHERE fingerprint=? AND status='ready' AND image_id<>? LIMIT 1", (signature, row['id'])).fetchone()
                    reused = []
                    if duplicate:
                        with self.vector_lock:
                            reused = self.client.retrieve(self.collection, [duplicate[0]], with_vectors=True)
                    if reused and reused[0].payload.get('fingerprint') == signature:
                        vector = reused[0].vector
                    else:
                        with self.load_image(row) as picture:
                            vector = self.encode_image(picture)
                    # Never publish an embedding if a concurrent rescan changed its source.
                    with self.db() as conn:
                        current = conn.execute('SELECT * FROM images WHERE id=? AND missing=0', (row['id'],)).fetchone()
                    if current is None or fingerprint(current) != signature:
                        continue
                    with self.vector_lock:
                        self.client.upsert(self.collection, [models.PointStruct(id=row['id'], vector=vector,
                            payload={'fingerprint': signature, 'model': MODEL_VERSION})], wait=True)
                    self.record(row['id'], signature, 'ready')
                except ModelUnavailable:
                    raise
                except Exception as error:
                    self.record(row['id'], signature, 'failed', str(error))
                processed += 1
                self.update(processed=processed)
            self.update(message=f'Visual index updated · {processed} images processed')
        finally:
            self.update(running=False)
            self.job_lock.release()

    def search(self, query, where='missing=0', values=(), offset=0, limit=80):
        if not query.strip() or len(query.strip()) > 500:
            raise ValueError('Enter a description between 1 and 500 characters.')
        with self.db() as conn:
            candidates = [dict(row) for row in conn.execute('SELECT * FROM images WHERE ' + where, values)]
            records = {r['image_id']: dict(r) for r in conn.execute("SELECT * FROM image_embeddings WHERE status='ready'")}
        allowed = {r['id']: r for r in candidates if r['id'] in records and records[r['id']]['fingerprint'] == fingerprint(r)}
        if not allowed:
            return {'items': [], 'total': 0, 'semantic': True, 'indexed': 0, 'unindexed': len(candidates)}
        vector = self.encode_text(query)
        with self.vector_lock:
            hits = self.client.query_points(self.collection, query=vector,
                query_filter=models.Filter(must=[models.HasIdCondition(has_id=list(allowed))]),
                limit=limit, offset=offset, with_payload=True).points
        items = []
        for hit in hits:
            row = allowed[hit.id]
            if hit.payload.get('fingerprint') != fingerprint(row):
                continue
            row['score'] = float(hit.score)
            items.append(row)
        return {'items': items, 'total': len(allowed), 'semantic': True, 'indexed': len(allowed), 'unindexed': len(candidates) - len(allowed)}

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
        if not self.thread or not self.thread.is_alive():
            with self.vector_lock:
                self.client.close()
