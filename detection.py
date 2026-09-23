"""Optional CPU face-region detection. Regions never assign person labels."""
import hashlib
import json
import threading
from pathlib import Path
from urllib.request import urlopen

import numpy as np
from people import image_revision

MODEL = 'yunet-2023mar-v1'
SHA256 = '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'
MODEL_URL = 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS detection_settings(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL);
    INSERT OR IGNORE INTO detection_settings VALUES(1,0);
    CREATE TABLE IF NOT EXISTS detections(image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
        revision TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,regions TEXT NOT NULL DEFAULT '[]',
        region_count INTEGER NOT NULL DEFAULT 0,error TEXT);
    INSERT OR IGNORE INTO project_schema VALUES('detection',1);
    ''')


class Detector:
    def __init__(self, data, db, load_image):
        self.data, self.db, self.load_image = Path(data), db, load_image
        self.lock = threading.RLock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.thread = None
        self.model = None
        self.running = False
        self.generation = 0

    def status(self, image_id=None):
        with self.db() as c:
            enabled = bool(c.execute('SELECT enabled FROM detection_settings').fetchone()[0])
            counts = dict(c.execute("SELECT count(*) AS total,coalesce(sum(status='pending'),0) AS pending,coalesce(sum(status='failed'),0) AS failed FROM detections d JOIN images i ON i.id=d.image_id WHERE i.missing=0 AND d.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime) AND d.model=?", (MODEL,)).fetchone())
            result = {'enabled': enabled, 'available': True, 'running': self.running, **counts, 'model': MODEL}
            if image_id is not None:
                row = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(image_id),)).fetchone()
                if row is None:
                    raise ValueError('Image unavailable')
                job = c.execute('SELECT * FROM detections WHERE image_id=? AND revision=? AND model=?', (row['id'], image_revision(row), MODEL)).fetchone()
                result['image'] = dict(job) if job else {'status': 'unprocessed', 'regions': '[]'}
                result['image']['regions'] = json.loads(result['image']['regions']) if enabled else []
            return result

    def action(self, payload):
        action = payload.get('action')
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if action == 'enable':
                if not isinstance(payload.get('enabled'), bool):
                    raise ValueError('enabled must be true or false')
                c.execute('UPDATE detection_settings SET enabled=?', (int(payload['enabled']),))
                self.generation += 1
            elif action == 'clear':
                self.generation += 1
                c.execute('DELETE FROM detections')
            elif action == 'ignore':
                row = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(payload['image_id']),)).fetchone()
                if row is None or image_revision(row) != payload.get('revision'):
                    raise ValueError('Photo changed. Reopen it before reviewing.')
                self.generation += 1
                c.execute("UPDATE detections SET status='ready',regions='[]',region_count=0,error=NULL WHERE image_id=?", (row['id'],))
            elif action in ('detect', 'queue'):
                if not c.execute('SELECT enabled FROM detection_settings').fetchone()[0]:
                    raise ValueError('Enable local face detection first.')
                if action == 'detect':
                    rows = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(payload['image_id']),)).fetchall()
                    if not rows or image_revision(rows[0]) != payload.get('revision'):
                        raise ValueError('Photo changed or unavailable. Reopen it.')
                else:
                    rows = c.execute('SELECT * FROM images WHERE missing=0').fetchall()
                for row in rows:
                    old = c.execute('SELECT * FROM detections WHERE image_id=?', (row['id'],)).fetchone()
                    if action == 'queue' and old and old['revision'] == image_revision(row) and old['model'] == MODEL and old['status'] == 'ready':
                        continue
                    c.execute("INSERT INTO detections(image_id,revision,model,status) VALUES(?,?,?,'pending') ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision,model=excluded.model,status='pending',regions='[]',region_count=0,error=NULL", (row['id'], image_revision(row), MODEL))
            else:
                raise ValueError('Unknown detection action')
        self.wake.set()
        return {'ok': True}

    def detect(self, picture):
        import cv2
        cv2.setNumThreads(2)
        if self.model is None:
            path = self.data / 'models' / 'face_detection_yunet_2023mar.onnx'
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
                with urlopen(MODEL_URL, timeout=30) as response:
                    raw = response.read(1024 * 1024)
                if hashlib.sha256(raw).hexdigest() != SHA256:
                    raise ValueError('Detector download checksum mismatch. Retry later.')
                pending = path.with_suffix('.part')
                pending.write_bytes(raw)
                pending.replace(path)
            self.model = cv2.FaceDetectorYN.create(str(path), '', (320, 320), 0.85, 0.3, 5000)
        picture = picture.copy()
        picture.thumbnail((1280, 1280))
        pixels = cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)
        height, width = pixels.shape[:2]
        self.model.setInputSize((width, height))
        _, faces = self.model.detect(pixels)
        regions = []
        for face in ([] if faces is None else faces):
            x, y, w, h = (float(v) for v in face[:4])
            x1, y1 = max(0, min(1, x / width)), max(0, min(1, y / height))
            x2, y2 = max(0, min(1, (x+w) / width)), max(0, min(1, (y+h) / height))
            if x2 > x1 and y2 > y1 and np.isfinite(face).all():
                regions.append({'x': x1, 'y': y1, 'width': x2-x1, 'height': y2-y1, 'confidence': float(face[-1])})
        return regions

    def process_one(self):
        with self.lock, self.db() as c:
            if not c.execute('SELECT enabled FROM detection_settings').fetchone()[0]:
                return False
            job = c.execute("SELECT d.* FROM detections d JOIN images i ON i.id=d.image_id WHERE d.status='pending' AND i.missing=0 AND d.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime) AND d.model=? ORDER BY d.image_id LIMIT 1", (MODEL,)).fetchone()
            if job is None:
                return False
            row = c.execute('SELECT * FROM images WHERE id=?', (job['image_id'],)).fetchone()
            generation = self.generation
        self.running = True
        try:
            with self.load_image(row) as picture:
                regions = self.detect(picture)
            error = None
        except Exception as exc:
            regions, error = [], str(exc)
        finally:
            self.running = False
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (row['id'],)).fetchone()
            if generation == self.generation and current and image_revision(current) == job['revision']:
                c.execute('UPDATE detections SET status=?,regions=?,region_count=?,error=? WHERE image_id=? AND revision=?', ('failed' if error else 'ready', json.dumps(regions), len(regions), error, row['id'], job['revision']))
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
                    except Exception:
                        break  # Persisted jobs remain retryable on the next queue request.
        self.thread = threading.Thread(target=work, daemon=True, name='face-detection')
        self.thread.start()
        self.wake.set()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
