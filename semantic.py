"""Local image/text inference (SigLIP 2 or CLIP) and persistent Qdrant vector search."""
import hashlib
import io
import os
import threading
import time
from urllib.parse import urlsplit
from collections import OrderedDict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, CancelledError
from contextlib import contextmanager

import numpy as np
from PIL import Image, ImageOps
from qdrant_client import QdrantClient, models

import accel
import drive
import performance

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
    key = (name or '').strip().lower() or DEFAULT_MODEL
    if key not in MODELS:
        raise ValueError('Unknown image model "' + str(name) + '". Choose one of: ' + ', '.join(sorted(MODELS)))
    return MODELS[key]


ACTIVE = select(os.environ.get('IMAGE_INDEX_MODEL'))
MODEL_NAME = next(key for key,value in MODELS.items() if value is ACTIVE)
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
        self.download_state = {'running': False, 'bytes': 0, 'error': None}
        self.download_lock = threading.Lock()
        self.performance = performance.Recorder(data, db)
        self.activity = lambda: {}
        self.queued_at = None
        self.image_calls = 0
        self.prefetch_workers = int(os.environ.get('IMAGE_INDEX_PREFETCH','2'))
        self.write_batch_size = int(os.environ.get('IMAGE_INDEX_WRITE_BATCH','4'))
        self.max_decode_pixels = int(os.environ.get('IMAGE_INDEX_MAX_PIXELS','64000000'))
        if not 0<=self.prefetch_workers<=2 or not 1<=self.write_batch_size<=16 or not 1<=self.max_decode_pixels<=100000000:
            raise ValueError('Prefetch must be 0–2, write batch 1–16, and pixel limit 1–100000000.')
        with db() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS image_embeddings(
                image_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, model TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, updated REAL NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS embeddings_fingerprint ON image_embeddings(fingerprint)')
        self.collection = os.environ.get('QDRANT_COLLECTION') or COLLECTION
        self.database, self.client = self._connect()
        try:
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(self.collection, vectors_config=models.VectorParams(size=DIMENSIONS, distance=models.Distance.COSINE))
            else:
                vectors = self.client.get_collection(self.collection).config.params.vectors
                if isinstance(vectors, dict) or vectors.size != DIMENSIONS or vectors.distance != models.Distance.COSINE:
                    raise ValueError(f'Collection {self.collection} is incompatible with {MODEL_VERSION} ({DIMENSIONS}-d cosine). Choose a different QDRANT_COLLECTION or leave it unset. Existing vectors were preserved.')
        except Exception:
            self.client.close()
            raise

    def _connect(self):
        url = os.environ.get('QDRANT_URL', '').strip()
        if url:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('QDRANT_URL must be an HTTP(S) server address without credentials or query parameters. Use QDRANT_API_KEY for authentication.')
            return ('Qdrant server', QdrantClient(url=url, api_key=os.environ.get('QDRANT_API_KEY') or None,
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
                    with performance.span('image_model_load'):
                        loaded = ImageEmbedding(VISION_MODEL, **options)
                        accel.require_requested_provider(loaded)
                    self.image_model = loaded
                if text and self.text_model is None:
                    self.update(message='Loading text model (first use downloads model weights)…')
                    with performance.span('text_model_load'):
                        loaded = TextEmbedding(TEXT_MODEL, **options)
                        accel.require_requested_provider(loaded)
                    self.text_model = loaded
            except Exception as error:
                if isinstance(error, UnicodeDecodeError):
                    raise ModelUnavailable('The tokenizer requires UTF-8. Restart with start.ps1 or python -X utf8 app.py, then retry.') from error
                raise ModelUnavailable(f'Could not load {MODEL_VERSION}. Check the model cache, internet connection, and runtime configuration, then retry: ' + str(error)) from error

    def model_status(self):
        from fastembed import ImageEmbedding, TextEmbedding
        towers = []
        for kind, cls, name, loaded in [('image', ImageEmbedding, VISION_MODEL, self.image_model), ('text', TextEmbedding, TEXT_MODEL, self.text_model)]:
            description = next(m for m in cls.list_supported_models() if m['model'] == name)
            # Check the actual cached ONNX file, not merely the presence of a repo directory.
            model_file = description['model_file']
            repository = description.get('sources', {}).get('hf')
            cache_files = []
            if repository:
                for root in (self.model_cache / ('models--' + repository.replace('/', '--')), self.model_cache / repository.replace('/', '_')):
                    if root.exists():
                        cache_files.extend(p for p in root.rglob(Path(model_file).name) if p.is_file())
            towers.append({'kind': kind, 'model': name, 'cached': bool(cache_files), 'loaded': loaded is not None,
                           'estimated_bytes': int(description.get('size_in_GB', 0) * 1_000_000_000),
                           'provider': ','.join(accel.session_providers(loaded)) or 'Not loaded'})
        with self.state_lock:
            download = dict(self.download_state)
        return {'towers': towers, 'download': download, 'requested_provider': accel.mode()}

    def prepare_models(self):
        """Explicit background preparation with cache-byte progress while the UI stays responsive."""
        if not self.download_lock.acquire(blocking=False):
            return
        def prepare():
            finished = threading.Event()
            def cache_bytes():
                count = 0
                for path in self.model_cache.rglob('*'):
                    try:
                        if path.is_file(): count += path.stat().st_size
                    except FileNotFoundError:
                        pass # Downloads rename temporary files while we observe them.
                return count
            def monitor():
                while not finished.is_set():
                    count = cache_bytes()
                    with self.state_lock:
                        self.download_state['bytes'] = max(0, count - initial)
                    finished.wait(1)
            try:
                initial = cache_bytes()
                with self.state_lock:
                    self.download_state.update(running=True, bytes=0, error=None)
                threading.Thread(target=monitor, daemon=True).start()
                with self.performance.job('model_prepare',encoder=MODEL_NAME,runtime=accel.mode()),performance.timed_lock(self.model_lock,'model_lock_wait'):
                    self.ensure_models()
            except Exception as error:
                with self.state_lock:
                    self.download_state['error'] = str(error)
            finally:
                finished.set()
                with self.state_lock:
                    self.download_state['running'] = False
                self.download_lock.release()
        threading.Thread(target=prepare, daemon=True, name='prepare-models').start()

    def encode_image(self, picture):
        with performance.timed_lock(self.model_lock, 'model_lock_wait'):
            self.ensure_models(text=False)
            performance.metadata(image_providers=accel.session_providers(self.image_model),text_providers=accel.session_providers(self.text_model))
            first = self.image_calls == 0;self.image_calls += 1
            with performance.span('embedding', first_call=first):
                vector = next(self.image_model.embed([picture], batch_size=1))
            with performance.span('normalization'):
                return normalized(vector)

    def encode_text(self, text):
        text = text.strip()
        if not text or len(text) > 500:
            raise ValueError('Enter a description between 1 and 500 characters.')
        with performance.timed_lock(self.model_lock, 'model_lock_wait'):
            if text in self.query_cache:
                self.query_cache.move_to_end(text)
                return self.query_cache[text]
            self.ensure_models(vision=False)
            performance.metadata(image_providers=accel.session_providers(self.image_model),text_providers=accel.session_providers(self.text_model))
            with performance.span('text_embedding'):
                vector = normalized(next(self.text_model.embed([text], batch_size=1)))
            self.query_cache[text] = vector
            if len(self.query_cache) > 128:
                self.query_cache.popitem(last=False)
            return vector

    def load_image(self, row):
        return self.decode_image(row,self.read_image(row))

    def read_image(self, row, cancel=None):
        if row['size'] > MAX_IMAGE_BYTES:
            raise ValueError('Image exceeds the 64 MB embedding limit.')
        performance.metadata(source='drive' if row['drive_id'] else 'local',bytes=int(row['size'] or 0))
        if cancel is not None and cancel.is_set():raise CancelledError()
        with performance.span('source_open'):
            if row['drive_id']:
                stream = drive.request(self.data, 'files/' + row['drive_id'], {'alt': 'media', 'supportsAllDrives': 'true'})
            else:
                stream = Path(row['path']).open('rb')
        with performance.span('source_read'), stream:
            buffer=io.BytesIO()
            while buffer.tell()<=MAX_IMAGE_BYTES:
                if cancel is not None and cancel.is_set():raise CancelledError()
                chunk=stream.read(min(64*1024,MAX_IMAGE_BYTES+1-buffer.tell()))
                if not chunk:break
                buffer.write(chunk)
            raw=buffer.getvalue()
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('Image exceeds the 64 MB embedding limit.')
        return raw

    def decode_image(self, row, raw):
        performance.metadata(source='drive' if row['drive_id'] else 'local',bytes=len(raw))
        with performance.span('checksum'):
            if row['drive_id']:
                if row['digest'] != row['drive_id'] and hashlib.md5(raw).hexdigest() != row['digest']:
                    raise ValueError('Image changed in Drive. Rescan the folder before indexing.')
            elif hashlib.sha256(raw).hexdigest() != row['digest']:
                raise ValueError('Image changed on disk. Rescan the folder before indexing.')
        with performance.span('decode'), Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>self.max_decode_pixels:
                raise ValueError('Image exceeds the configured decoded pixel limit.')
            image.seek(0)
            picture = ImageOps.exif_transpose(image)
            if picture.mode in ('RGBA', 'LA') or (picture.mode == 'P' and 'transparency' in picture.info):
                rgba = picture.convert('RGBA')
                picture = Image.new('RGB', rgba.size, 'white')
                picture.paste(rgba, mask=rgba.getchannel('A'))
            result = picture.convert('RGB')
            performance.metadata(width=result.width,height=result.height,pixels=result.width*result.height,bytes=len(raw))
            return result

    def record(self, image_id, signature, status, error=None):
        self.record_many([(image_id,signature,status,error)])

    def record_many(self, records):
        if not records:return
        with performance.span('sqlite_commit'), self.db() as conn:
            self._record_many(conn,records)

    @staticmethod
    def _record_many(conn, records):
        conn.executemany('''INSERT INTO image_embeddings VALUES(?,?,?,?,?,?) ON CONFLICT(image_id) DO UPDATE SET
                fingerprint=excluded.fingerprint,model=excluded.model,status=excluded.status,error=excluded.error,updated=excluded.updated''',
                [(image_id,signature,MODEL_VERSION,status,error,time.time()) for image_id,signature,status,error in records])

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
                'database': self.database, 'collection': self.collection,
                'provider': ','.join(dict.fromkeys(accel.session_providers(self.image_model) + accel.session_providers(self.text_model))) or 'Not loaded'}

    def queue(self, retry_failed=False):
        with self.state_lock:
            if self.queued_at is None:self.queued_at = time.perf_counter()
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
                with self.state_lock:
                    queued_at = self.queued_at;self.queued_at = None
                self.index_pending(queue_wait_ms=max(0,time.perf_counter()-queued_at)*1000 if queued_at is not None else 0)
            except Exception as error:
                self.update(running=False, error=str(error), message='Visual indexing stopped. Retry after fixing the error.')

    def index_pending(self, queue_wait_ms=0):
        if not self.job_lock.acquire(blocking=False):
            return
        self.update(running=True, processed=0, error=None, message='Checking image embeddings…')
        processed = failures = 0
        job_error=None;job=None
        try:
            with self.db() as conn:
                job=conn.execute("INSERT INTO job_history(kind,started,status) VALUES('embeddings',?,'running')",(time.time(),)).lastrowid
            with self.performance.job('embeddings',job_id=job,encoder=MODEL_NAME,backend=self.database,batch_size=1,runtime=accel.mode(),
                    prefetch_workers=self.prefetch_workers,write_batch_size=self.write_batch_size,
                    prefetch_budget_bytes=self.prefetch_workers*MAX_IMAGE_BYTES,max_decode_pixels=self.max_decode_pixels) as timing:
                timing.queue_wait_ms = queue_wait_ms
                if self.performance.enabled:
                    try:performance.metadata(**self.activity())
                    except Exception:pass # Optional diagnostic metadata cannot stop indexing.
                try:
                    self._index_rows(timing)
                finally:
                    processed, failures = timing.index_counts
            self.update(message=f'Visual index updated · {processed} images processed')
        except Exception as error:
            job_error=str(error)
            raise
        finally:
            try:
                if job is not None:
                    with self.db() as conn:
                        state='failed' if job_error or failures else 'paused' if self.stop.is_set() else 'complete'
                        conn.execute('UPDATE job_history SET finished=?,status=?,processed=?,failed_count=?,error=? WHERE id=?',
                            (time.time(),state,processed,failures,job_error,job))
                        if not processed and not job_error and state=='complete':conn.execute('DELETE FROM job_history WHERE id=?',(job,))
                        conn.execute('DELETE FROM job_history WHERE id NOT IN (SELECT id FROM job_history ORDER BY id DESC LIMIT 200)')
            finally:
                self.update(running=False)
                self.job_lock.release()

    def _index_rows(self, timing):
        timing.index_counts = (0,0)
        try:
            with performance.span('cleanup'):
                with self.db() as conn:
                    gone = [r[0] for r in conn.execute('''SELECT e.image_id FROM image_embeddings e
                        LEFT JOIN images i ON i.id=e.image_id WHERE i.id IS NULL OR i.missing=1''')]
                if gone:
                    with self.vector_lock:
                        self.client.delete(self.collection, points_selector=models.PointIdsList(points=gone))
                    with self.db() as conn:
                        conn.executemany('DELETE FROM image_embeddings WHERE image_id=?', ((item,) for item in gone))
            with performance.span('catalog'):rows = self.rows()
            for offset in range(0,len(rows),self.write_batch_size):
                if self.stop.is_set():break
                self._index_group(rows[offset:offset+self.write_batch_size],timing)
        finally:
            if timing.index_counts[1]:timing.outcome = 'failed'
            elif self.stop.is_set():timing.outcome = 'interrupted'

    def _index_group(self, rows, timing):
        with performance.timed_lock(self.vector_lock,'vector_lock_wait'),performance.span('vector_lookup'):
            points = {p.id:p for p in self.client.retrieve(self.collection,[r['id'] for r in rows],with_payload=True)}
        entries=[];repairs=[]
        for row in rows:
            signature=fingerprint(row)
            with performance.image(row['id'],measure=False):
                performance.count('visited')
                if row['embedded_fingerprint']==signature and row['embedding_status']=='failed':
                    performance.count('failed_skipped');continue
                if row['id'] in points and points[row['id']].payload.get('fingerprint')==signature:
                    if row['embedding_status']!='ready' or row['embedded_fingerprint']!=signature:
                        repairs.append((row['id'],signature,'ready',None))
                    performance.count('skipped');continue
                entries.append({'row':row,'signature':signature,'started':self.performance.clock(),
                    'vector':None,'reused':False,'error':None,'meta':{}})
        self.record_many(repairs)
        if not entries:return
        # Pending state is durable before any vectors in this group are published.
        self.record_many([(e['row']['id'],e['signature'],'pending',None) for e in entries])
        with performance.span('duplicate_lookup'),self.db() as conn:
            for e in entries:
                duplicate=conn.execute("SELECT image_id FROM image_embeddings WHERE fingerprint=? AND status='ready' AND image_id<>? LIMIT 1",
                    (e['signature'],e['row']['id'])).fetchone()
                if duplicate:
                    with performance.timed_lock(self.vector_lock,'vector_lock_wait'),performance.span('duplicate_vector'):
                        reused=self.client.retrieve(self.collection,[duplicate[0]],with_vectors=True)
                    if reused and reused[0].payload.get('fingerprint')==e['signature']:
                        e['vector']=reused[0].vector;e['reused']=True
        vectors={};fatal=None
        with self._prefetched_reads(entries,timing) as take:
            for e in entries:
                if self.stop.is_set():break
                row=e['row']
                with performance.image(row['id'],measure=False),performance.span('image_prepare'):
                    self.update(message='Embedding '+row['name'])
                    try:
                        if e['vector'] is None and e['signature'] in vectors:
                            e['vector']=vectors[e['signature']];e['reused']=True
                        if e['vector'] is None:
                            raw=take(row) if row['drive_id'] and self.prefetch_workers else None
                            try:
                                with self.decode_image(row,raw) if raw is not None else self.load_image(row) as picture:
                                    if self.stop.is_set():break
                                    e['meta']={'width':picture.width,'height':picture.height,'pixels':picture.width*picture.height}
                                    e['vector']=self.encode_image(picture)
                                performance.count('inferred')
                            finally:raw=None
                        vectors[e['signature']]=e['vector']
                    except Exception as error:
                        if self.stop.is_set():break
                        e['error']=str(error)
                        if isinstance(error,ModelUnavailable):fatal=error;break
        self._publish_group([e for e in entries if e['vector'] is not None or e['error'] is not None],timing)
        if fatal:raise fatal

    @contextmanager
    def _prefetched_reads(self, entries, timing):
        # Prefetch only compressed Drive bytes. Exactly one image is decoded at a time.
        unique={};waiting={};pool=None
        for e in entries:
            if e['vector'] is None and e['row']['drive_id']:
                unique.setdefault(e['signature'],e['row'])
        iterator=iter(unique.values())
        def fill():
            if pool is None or self.stop.is_set():return
            while len(waiting)<self.prefetch_workers:
                row=next(iterator,None)
                if row is None:break
                waiting[row['id']]=pool.submit(performance.prefetch_call,timing,row['id'],self.read_image,row,self.stop)
        def take(row):
            future=waiting.pop(row['id'],None)
            if future is None:return self.read_image(row,self.stop)
            try:
                with performance.span('prefetch_wait'):return future.result()
            finally:fill()
        try:
            if self.prefetch_workers and unique:
                pool=ThreadPoolExecutor(max_workers=self.prefetch_workers,thread_name_prefix='drive-prefetch')
                fill()
            yield take
        finally:
            for future in waiting.values():future.cancel()
            if pool:pool.shutdown(wait=True,cancel_futures=True)
            waiting.clear()

    def _current_group(self, conn, entries):
        if not entries:return {}
        ids=[e['row']['id'] for e in entries]
        return {r['id']:r for r in conn.execute('SELECT * FROM images WHERE missing=0 AND id IN ('+','.join('?' for _ in ids)+')',ids)}

    def _upsert_entries(self, entries):
        if not entries:return
        with performance.timed_lock(self.vector_lock,'vector_lock_wait'),performance.span('vector_upsert'):
            self.client.upsert(self.collection,[models.PointStruct(id=e['row']['id'],vector=e['vector'],
                payload={'fingerprint':e['signature'],'model':MODEL_VERSION}) for e in entries],wait=True)

    def _publish_group(self, entries, timing):
        if not entries:return
        with performance.span('revision_check'),self.db() as conn:current=self._current_group(conn,entries)
        live=[e for e in entries if e['row']['id'] in current and fingerprint(current[e['row']['id']])==e['signature']]
        publish=[e for e in live if e['error'] is None]
        confirmed=set()
        try:
            self._upsert_entries(publish)
            confirmed.update(e['row']['id'] for e in publish)
        except Exception:
            # A server/local failure may have persisted some points. Recover those before
            # isolating failures with single-point retries; never mark unconfirmed work ready.
            recovered={}
            try:
                with performance.timed_lock(self.vector_lock,'vector_lock_wait'),performance.span('vector_lookup'):
                    recovered={p.id:p for p in self.client.retrieve(self.collection,[e['row']['id'] for e in publish],with_payload=True)}
            except Exception:pass
            for e in publish:
                point=recovered.get(e['row']['id'])
                if point and point.payload.get('fingerprint')==e['signature']:
                    confirmed.add(e['row']['id']);continue
                try:
                    with performance.span('revision_check'),self.db() as conn:
                        latest=self._current_group(conn,[e]).get(e['row']['id'])
                    if latest is None or fingerprint(latest)!=e['signature']:continue
                    self._upsert_entries([e])
                    confirmed.add(e['row']['id'])
                except Exception as error:e['error']=str(error)
        outcomes=[];records=[]
        with performance.span('sqlite_commit'),self.db() as conn:
            # Serialize the final revision check with catalog writers, after awaited upsert.
            conn.execute('BEGIN IMMEDIATE')
            with performance.span('revision_check'):current=self._current_group(conn,entries)
            for e in entries:
                row=current.get(e['row']['id'])
                outcome='stale' if row is None or fingerprint(row)!=e['signature'] or (e['error'] is None and e['row']['id'] not in confirmed) else 'failed' if e['error'] is not None else 'ready'
                outcomes.append((e,outcome))
                if outcome!='stale':records.append((e['row']['id'],e['signature'],outcome,e['error']))
            self._record_many(conn,records)
        processed,failures=timing.index_counts
        for e,outcome in outcomes:
            with performance.image(e['row']['id'],measure=False):
                performance.metadata(source='drive' if e['row']['drive_id'] else 'local',bytes=int(e['row']['size'] or 0),**e['meta'])
                with performance.span('image_total',start_time=e['started']):
                    if outcome=='stale':performance.count('stale');continue
                    processed+=1
                    if outcome=='failed':failures+=1;performance.count('failed')
                    else:
                        performance.count('success');performance.count('reused' if e['reused'] else 'new_success')
        timing.index_counts=processed,failures
        self.update(processed=processed)

    def search(self, query, where='missing=0', values=(), offset=0, limit=80):
        if not query.strip() or len(query.strip()) > 500:
            raise ValueError('Enter a description between 1 and 500 characters.')
        with self.performance.job('search'):
            return self.rank(lambda: self.encode_text(query), where, values, offset, limit)

    def similar(self, image_id, where='missing=0', values=(), offset=0, limit=80):
        """Use an existing general-image vector; never downloads another model."""
        image_id = int(image_id)
        with self.db() as conn:
            source = conn.execute('SELECT * FROM images WHERE id=? AND missing=0', (image_id,)).fetchone()
        if source is None:
            raise ValueError('Source image is unavailable.')
        with self.vector_lock:
            points = self.client.retrieve(self.collection, [image_id], with_vectors=True, with_payload=True)
        if not points or points[0].payload.get('fingerprint') != fingerprint(source):
            raise ValueError('Index this image before finding similar images.')
        return self.rank(points[0].vector, where+' AND id<>?', (*values,image_id), offset, limit)

    def rank(self, vector, where, values, offset, limit):
        with self.db() as conn:
            candidates = [dict(row) for row in conn.execute('SELECT * FROM images WHERE ' + where, values)]
            records = {r['image_id']: dict(r) for r in conn.execute("SELECT * FROM image_embeddings WHERE status='ready'")}
        allowed = {r['id']: r for r in candidates if r['id'] in records and records[r['id']]['fingerprint'] == fingerprint(r)}
        if not allowed:
            return {'items': [], 'total': 0, 'semantic': True, 'indexed': 0, 'unindexed': len(candidates)}
        if callable(vector): vector = vector()
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

    def near_duplicates(self, where='missing=0', values=(), offset=0, threshold=0.98, batch=40):
        """Bounded candidate discovery; high vector similarity is only a review suggestion."""
        from people import image_revision
        if not 0.8<=threshold<=1:raise ValueError('Similarity threshold must be between 0.8 and 1.')
        offset=max(0,int(offset));batch=min(40,max(1,int(batch)))
        with self.db() as c:
            candidates=[dict(r) for r in c.execute('SELECT * FROM images WHERE '+where+' ORDER BY id',values)]
            records={r['image_id']:dict(r) for r in c.execute("SELECT * FROM image_embeddings WHERE status='ready'")}
            reviews={(r['left_id'],r['right_id']):dict(r) for r in c.execute('SELECT * FROM duplicate_reviews')}
        allowed={r['id']:r for r in candidates if r['id'] in records and records[r['id']]['fingerprint']==fingerprint(r)}
        ids=list(allowed);pairs=[]
        with self.vector_lock:
            sources=self.client.retrieve(self.collection,ids[offset:offset+batch],with_vectors=True,with_payload=True)
            for source in sources:
                left=allowed[source.id]
                if source.payload.get('fingerprint')!=fingerprint(left):continue
                hits=self.client.query_points(self.collection,query=source.vector,
                    query_filter=models.Filter(must=[models.HasIdCondition(has_id=ids)]),limit=12,with_payload=True,score_threshold=threshold).points
                for hit in hits:
                    if hit.id<=source.id:continue
                    right=allowed[hit.id]
                    if right['digest']==left['digest'] or hit.payload.get('fingerprint')!=fingerprint(right):continue
                    revs=image_revision(left),image_revision(right)
                    reviewed=reviews.get((source.id,hit.id))
                    if reviewed and (reviewed['left_revision'],reviewed['right_revision'])==revs:continue
                    pairs.append({'left':{**left,'revision':revs[0]},'right':{**right,'revision':revs[1]},'score':float(hit.score)})
        end=offset+batch
        return {'pairs':pairs,'total_indexed':len(ids),'next_offset':end if end<len(ids) else None,
                'threshold':threshold,'message':'Vector similarity suggests candidates; compare the photos before deciding. Exact checksum duplicates are excluded.'}

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
        if not self.thread or not self.thread.is_alive():
            self.performance.close()
            with self.vector_lock:
                self.client.close()
