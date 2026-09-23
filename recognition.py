"""Optional ArcFace face recognition. Embeddings are grouped into people albums.

Uses the InsightFace ArcFace ONNX model (w600k_r50). The model file is obtained
separately because its pretrained weights are licensed for non-commercial
research use only; see README.md and download_arcface.py.
"""
import json
import threading
import time
from pathlib import Path

import numpy as np

import accel
from detection import detect_faces, load_model
from people import image_revision

MODEL = 'arcface-w600k-r50-v1'
MODEL_FILE = 'w600k_r50.onnx'
DIMENSIONS = 512
DETECT_LIMIT = 1280
MIN_FACE = 40
ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float64)


class ModelUnavailable(ValueError):
    pass


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS recognition_settings(
        id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0,
        auto_threshold REAL NOT NULL DEFAULT 0.50,review_threshold REAL NOT NULL DEFAULT 0.35);
    INSERT OR IGNORE INTO recognition_settings(id,enabled) VALUES(1,0);
    CREATE TABLE IF NOT EXISTS faces(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
        revision TEXT NOT NULL,model TEXT NOT NULL,region TEXT NOT NULL,landmarks TEXT NOT NULL,
        embedding BLOB NOT NULL,quality REAL NOT NULL,
        person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
        match_score REAL,status TEXT NOT NULL,created REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS faces_image ON faces(image_id);
    CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id);
    CREATE TABLE IF NOT EXISTS face_jobs(
        image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
        revision TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,error TEXT,
        faces INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL);
    INSERT OR IGNORE INTO project_schema VALUES('recognition',1);
    ''')
    columns = {row[1] for row in c.execute('PRAGMA table_info(people)')}
    if 'auto' not in columns:
        c.execute('ALTER TABLE people ADD COLUMN auto INTEGER NOT NULL DEFAULT 0')


def pack(vector):
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack(blob):
    return np.frombuffer(blob, dtype=np.float32)


def similarity(source, target):
    source, target = np.asarray(source, np.float64), np.asarray(target, np.float64)
    source_mean, target_mean = source.mean(0), target.mean(0)
    source_delta, target_delta = source - source_mean, target - target_mean
    covariance = target_delta.T @ source_delta / len(source)
    u, singular, vt = np.linalg.svd(covariance)
    signs = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        signs[1] = -1
    rotation = u @ np.diag(signs) @ vt
    variance = float((source_delta ** 2).sum() / len(source))
    if variance <= 0:
        raise ValueError('Face landmarks are degenerate.')
    scale = float((singular * signs).sum() / variance)
    return np.hstack([scale * rotation, (target_mean - scale * rotation @ source_mean).reshape(2, 1)]).astype(np.float32)


def align(frame, landmarks, size=112):
    import cv2
    matrix = similarity(landmarks, ARCFACE_DST)
    return cv2.warpAffine(frame, matrix, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


class Recognizer:
    def __init__(self, data, db, load_image, model_path=None):
        self.data, self.db, self.load_image = Path(data), db, load_image
        self.model_path = Path(model_path) if model_path else self.data / 'models' / MODEL_FILE
        self.lock = threading.RLock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.thread = None
        self.detector = None
        self.session = None
        self.running = False
        self.generation = 0
        with db() as c:
            migrate(c)

    def _enabled(self):
        with self.db() as c:
            return bool(c.execute('SELECT enabled FROM recognition_settings').fetchone()[0])

    def _detector_model(self):
        if self.detector is None:
            self.detector = load_model(self.data)
        return self.detector

    def _session_model(self):
        if self.session is None:
            if not self.model_path.exists():
                raise ModelUnavailable('ArcFace model missing. Run "python download_arcface.py" or place '
                    + MODEL_FILE + ' in data/models/. Its pretrained weights are for non-commercial research use only.')
            import onnxruntime
            self.session = onnxruntime.InferenceSession(str(self.model_path), providers=accel.providers())
        return self.session

    def embed(self, frame, faces):
        import cv2
        session = self._session_model()
        results = []
        for face in faces:
            if min(face['width'], face['height']) < MIN_FACE:
                continue
            try:
                aligned = align(frame, face['landmarks'])
                blob = cv2.dnn.blobFromImage(aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
                output = np.asarray(session.run(None, {session.get_inputs()[0].name: blob})[0], dtype=np.float32).reshape(-1)
                length = float(np.linalg.norm(output))
                if output.shape != (DIMENSIONS,) or not np.isfinite(output).all() or length <= 0:
                    raise ValueError('The recognition model returned an invalid vector.')
            except ModelUnavailable:
                raise
            except ValueError:
                continue
            results.append({**face, 'embedding': output / length})
        return results

    def encode(self, picture):
        return self.embed(*detect_faces(self._detector_model(), picture, DETECT_LIMIT))

    def status(self, image_id=None):
        with self.db() as c:
            settings = dict(c.execute('SELECT * FROM recognition_settings').fetchone())
            counts = dict(c.execute("""SELECT count(*) AS total,coalesce(sum(j.status='pending'),0) AS pending,
                coalesce(sum(j.status='failed'),0) AS failed FROM face_jobs j JOIN images i ON i.id=j.image_id
                WHERE i.missing=0 AND j.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime) AND j.model=?""", (MODEL,)).fetchone())
            faces = c.execute("""SELECT count(*) FROM faces f JOIN images i ON i.id=f.image_id
                WHERE i.missing=0 AND f.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime) AND f.model=?""", (MODEL,)).fetchone()[0]
            suggestions = c.execute("""SELECT count(*) FROM faces f JOIN images i ON i.id=f.image_id
                WHERE f.status='suggested' AND i.missing=0 AND f.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime) AND f.model=?""", (MODEL,)).fetchone()[0]
            result = {**settings, 'enabled': bool(settings['enabled']), 'available': self.model_path.exists(),
                'running': self.running, **counts, 'faces': faces, 'suggestions': suggestions,
                'model': MODEL, 'dimensions': DIMENSIONS, 'provider': accel.label()}
            if image_id is not None:
                row = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(image_id),)).fetchone()
                if row is None:
                    raise ValueError('Image unavailable')
                result['image'] = self._image_faces(c, row)
            return result

    def image_faces(self, image_id):
        with self.db() as c:
            row = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(image_id),)).fetchone()
            if row is None:
                raise ValueError('Image unavailable. Refresh the gallery.')
            return {'items': self._image_faces(c, row), 'revision': image_revision(row)}

    def _image_faces(self, c, row):
        signature, items = image_revision(row), []
        for record in c.execute('SELECT * FROM faces WHERE image_id=? AND model=? ORDER BY quality DESC,id', (row['id'], MODEL)):
            face = dict(record)
            face['region'] = json.loads(face.pop('region'))
            face['landmarks'] = json.loads(face.pop('landmarks'))
            face.pop('embedding', None)
            person = c.execute('SELECT id,name FROM people WHERE id=?', (face['person_id'],)).fetchone() if face['person_id'] else None
            face['person_name'] = person['name'] if person else None
            face['current'] = face['revision'] == signature
            items.append(face)
        return items

    def suggestions(self, offset=0, limit=60):
        offset, limit = max(0, int(offset)), min(100, max(1, int(limit)))
        with self.db() as c:
            total = c.execute("""SELECT count(*) FROM faces f JOIN images i ON i.id=f.image_id
                WHERE f.status='suggested' AND i.missing=0 AND f.model=?
                AND f.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime)""", (MODEL,)).fetchone()[0]
            rows = c.execute("""SELECT f.id,f.image_id,f.region,f.match_score,p.id AS person_id,p.name AS person_name,i.name AS image_name
                FROM faces f JOIN images i ON i.id=f.image_id JOIN people p ON p.id=f.person_id
                WHERE f.status='suggested' AND i.missing=0 AND f.model=?
                AND f.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime)
                ORDER BY f.match_score DESC,f.id LIMIT ? OFFSET ?""", (MODEL, limit, offset)).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item['region'] = json.loads(item.pop('region'))
                items.append(item)
            return {'items': items, 'total': total}

    def _enqueue(self, c):
        for row in c.execute('SELECT * FROM images WHERE missing=0').fetchall():
            signature = image_revision(row)
            old = c.execute('SELECT * FROM face_jobs WHERE image_id=?', (row['id'],)).fetchone()
            if old and old['revision'] == signature and old['model'] == MODEL and old['status'] == 'ready':
                continue
            c.execute("""INSERT INTO face_jobs(image_id,revision,model,status,faces,updated) VALUES(?,?,?,'pending',0,?)
                ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision,model=excluded.model,status='pending',faces=0,error=NULL,updated=excluded.updated""",
                (row['id'], signature, MODEL, time.time()))
            c.execute('DELETE FROM faces WHERE image_id=?', (row['id'],))

    def schedule(self):
        if not self._enabled():
            return
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            self._enqueue(c)
        self.wake.set()

    def action(self, payload):
        action = payload.get('action')
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            settings = c.execute('SELECT * FROM recognition_settings').fetchone()
            if action == 'enable':
                if not isinstance(payload.get('enabled'), bool):
                    raise ValueError('enabled must be true or false')
                c.execute('UPDATE recognition_settings SET enabled=?', (int(payload['enabled']),))
                self.generation += 1
            elif action == 'settings':
                auto, review = float(payload.get('auto_threshold')), float(payload.get('review_threshold'))
                if not 0 < review < auto <= 1:
                    raise ValueError('Require 0 < review threshold < auto threshold ≤ 1.')
                c.execute('UPDATE recognition_settings SET auto_threshold=?,review_threshold=?', (auto, review))
            elif action == 'clear':
                self.generation += 1
                c.execute('DELETE FROM faces')
                c.execute('DELETE FROM face_jobs')
            elif action == 'ignore':
                row = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(payload['image_id']),)).fetchone()
                if row is None or image_revision(row) != payload.get('revision'):
                    raise ValueError('Photo changed. Reopen it before reviewing.')
                self.generation += 1
                c.execute('DELETE FROM faces WHERE image_id=?', (row['id'],))
                c.execute("UPDATE face_jobs SET status='ready',faces=0,error=NULL WHERE image_id=?", (row['id'],))
            elif action in ('detect', 'queue'):
                if not settings['enabled']:
                    raise ValueError('Enable face recognition first.')
                if action == 'detect':
                    row = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(payload['image_id']),)).fetchone()
                    if row is None or image_revision(row) != payload.get('revision'):
                        raise ValueError('Photo changed or unavailable. Reopen it.')
                    c.execute("""INSERT INTO face_jobs(image_id,revision,model,status,faces,updated) VALUES(?,?,?,'pending',0,?)
                        ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision,model=excluded.model,status='pending',faces=0,error=NULL,updated=excluded.updated""",
                        (row['id'], image_revision(row), MODEL, time.time()))
                    c.execute('DELETE FROM faces WHERE image_id=?', (row['id'],))
                else:
                    self._enqueue(c)
            elif action in ('confirm', 'reject', 'assign', 'unlink'):
                face = c.execute("""SELECT f.*,i.digest,i.drive_id,i.size,i.mtime FROM faces f JOIN images i ON i.id=f.image_id
                    WHERE f.id=? AND i.missing=0 AND f.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime)""",
                    (int(payload['face_id']),)).fetchone()
                if face is None:
                    raise ValueError('Face match changed or is unavailable. Refresh.')
                if action == 'confirm':
                    person_id = face['person_id']
                    if person_id is None:
                        raise ValueError('This match has no suggested person.')
                    self._link(c, face['image_id'], person_id, face['revision'])
                    c.execute("UPDATE faces SET status='confirmed' WHERE id=?", (face['id'],))
                elif action == 'reject':
                    if face['person_id']:
                        c.execute('DELETE FROM image_people WHERE image_id=? AND person_id=?', (face['image_id'], face['person_id']))
                    c.execute("UPDATE faces SET status='rejected',person_id=NULL,match_score=NULL WHERE id=?", (face['id'],))
                elif action == 'assign':
                    person = c.execute('SELECT * FROM people WHERE id=?', (int(payload['person_id']),)).fetchone()
                    if person is None:
                        raise ValueError('Person no longer exists.')
                    if face['person_id'] and face['person_id'] != person['id']:
                        c.execute('DELETE FROM image_people WHERE image_id=? AND person_id=?', (face['image_id'], face['person_id']))
                    self._link(c, face['image_id'], person['id'], face['revision'])
                    c.execute("UPDATE faces SET status='confirmed',person_id=?,match_score=NULL WHERE id=?", (person['id'], face['id']))
                else:
                    if face['person_id']:
                        c.execute('DELETE FROM image_people WHERE image_id=? AND person_id=?', (face['image_id'], face['person_id']))
                    c.execute("UPDATE faces SET status='rejected',person_id=NULL,match_score=NULL WHERE id=?", (face['id'],))
            else:
                raise ValueError('Unknown recognition action')
        self.wake.set()
        return {'ok': True}

    def _link(self, c, image_id, person_id, revision):
        c.execute('INSERT INTO image_people VALUES(?,?,?,?) ON CONFLICT(image_id,person_id) DO UPDATE SET revision=excluded.revision',
            (image_id, person_id, revision, time.time()))
        c.execute('UPDATE people SET version=version+1,updated=? WHERE id=?', (time.time(), person_id))

    def _best(self, c, vector, auto, review):
        best_score, best_person = -1.0, None
        rows = c.execute("""SELECT f.person_id,f.embedding FROM faces f JOIN images i ON i.id=f.image_id
            WHERE f.person_id IS NOT NULL AND f.status IN ('auto','new','confirmed') AND f.model=? AND i.missing=0""", (MODEL,)).fetchall()
        for row in rows:
            score = float(np.dot(vector, unpack(row['embedding'])))
            if score > best_score:
                best_score, best_person = score, row['person_id']
        if best_person is None or best_score < review:
            return None, None, 'new'
        return best_person, best_score, ('auto' if best_score >= auto else 'suggested')

    def _new_person(self, c):
        now = time.time()
        number = c.execute("SELECT count(*) FROM people WHERE name LIKE 'Unknown %'").fetchone()[0] + 1
        return c.execute('INSERT INTO people(name,auto,created,updated) VALUES(?,?,?,?)',
            ('Unknown ' + str(number), 1, now, now)).lastrowid

    def _store(self, c, image_id, revision, face, auto, review):
        person_id, score, status = self._best(c, face['embedding'], auto, review)
        if status == 'new':
            person_id, score = self._new_person(c), None
        c.execute("""INSERT INTO faces(image_id,revision,model,region,landmarks,embedding,quality,person_id,match_score,status,created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (image_id, revision, MODEL, json.dumps(face['region']),
             json.dumps([[round(float(x), 2), round(float(y), 2)] for x, y in face['landmarks']]),
             pack(face['embedding']), float(face['quality']), person_id, score, status, time.time()))
        if status in ('auto', 'new'):
            c.execute('INSERT INTO image_people VALUES(?,?,?,?) ON CONFLICT(image_id,person_id) DO UPDATE SET revision=excluded.revision',
                (image_id, person_id, revision, time.time()))

    def process_one(self):
        with self.lock, self.db() as c:
            settings = c.execute('SELECT * FROM recognition_settings').fetchone()
            if not settings['enabled']:
                return False
            job = c.execute("""SELECT j.* FROM face_jobs j JOIN images i ON i.id=j.image_id
                WHERE j.status='pending' AND i.missing=0 AND j.model=?
                AND j.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime) ORDER BY j.image_id LIMIT 1""", (MODEL,)).fetchone()
            if job is None:
                return False
            row = c.execute('SELECT * FROM images WHERE id=?', (job['image_id'],)).fetchone()
            generation, auto, review = self.generation, settings['auto_threshold'], settings['review_threshold']
        self.running = True
        try:
            with self.load_image(row) as picture:
                faces = self.encode(picture)
            error = None
        except ModelUnavailable:
            self.running = False
            raise
        except Exception as exc:
            faces, error = [], str(exc)
        finally:
            self.running = False
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (row['id'],)).fetchone()
            if generation == self.generation and current and image_revision(current) == job['revision']:
                c.execute('DELETE FROM faces WHERE image_id=?', (row['id'],))
                if not error:
                    for face in faces:
                        self._store(c, row['id'], job['revision'], face, auto, review)
                c.execute('UPDATE face_jobs SET status=?,faces=?,error=?,updated=? WHERE image_id=? AND revision=?',
                    ('failed' if error else 'ready', len(faces), error, time.time(), row['id'], job['revision']))
        return True

    def start(self):
        def work():
            while not self.stop.is_set():
                self.wake.wait()
                self.wake.clear()
                while not self.stop.is_set():
                    try:
                        if not self.process_one():
                            break
                    except ModelUnavailable:
                        break
                    except Exception:
                        break
        self.thread = threading.Thread(target=work, daemon=True, name='face-recognition')
        self.thread.start()
        self.wake.set()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
