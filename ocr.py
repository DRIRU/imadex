"""Opt-in, resumable text extraction. Catalog-only results; no originals are saved."""
import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from people import image_revision
from ocr_worker import MODEL

LIVE = "o.model=? AND o.revision=content_revision(images.digest,images.drive_id,images.size,images.mtime)"


def migrate(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS ocr_settings(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0);
    INSERT OR IGNORE INTO ocr_settings(id) VALUES(1);
    CREATE TABLE IF NOT EXISTS image_ocr(image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
        revision TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,text TEXT NOT NULL DEFAULT '',
        error TEXT,updated REAL NOT NULL,seconds REAL NOT NULL DEFAULT 0);
    INSERT OR IGNORE INTO project_schema VALUES('ocr',1);
    ''')


def text_filter(word):
    # Literal substring matching, including percent/underscore characters.
    return "EXISTS (SELECT 1 FROM image_ocr o WHERE o.image_id=images.id AND o.status='ready' AND " + LIVE + " AND instr(lower(o.text),lower(?))>0)", [MODEL, word]


class OCR:
    def __init__(self, data, db, load_image, runner=None):
        self.data, self.db, self.load_image = Path(data), db, load_image
        self.runner = runner or self.extract
        self.lock = threading.RLock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.thread = None
        self.epoch = 0
        self.running = False
        self.error = ''
        self.single = []

    def python(self):
        return self.data / 'ocr-env' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def available(self):return self.python().exists()

    def extract(self, picture):
        picture.thumbnail((1600, 1600))
        stream = io.BytesIO();picture.save(stream, format='PNG')
        if stream.tell() > 12 * 1024 * 1024:raise ValueError('OCR preview exceeds input limit')
        result = subprocess.run([str(self.python()), '-X', 'utf8', str(Path(__file__).parent / 'ocr_worker.py')],
                                input=stream.getvalue(), capture_output=True, timeout=60,
                                env=dict(os.environ, OMP_NUM_THREADS='2'))
        if result.returncode:raise ValueError('OCR runtime failed. Run setup_ocr.py again; check model/runtime compatibility.')
        output = json.loads(result.stdout.decode('utf-8'))
        if output.get('model') != MODEL or not isinstance(output.get('text'), str):raise ValueError('Invalid OCR worker result')
        return output['text'][:32000]

    def status(self, image_id=None):
        with self.lock, self.db() as conn:
            enabled = bool(conn.execute('SELECT enabled FROM ocr_settings WHERE id=1').fetchone()[0])
            counts = dict(conn.execute('''SELECT count(*) AS total,
                COALESCE(sum(o.status='ready' AND ''' + LIVE + '''),0) AS ready,
                COALESCE(sum(o.status='failed' AND ''' + LIVE + '''),0) AS failed
                FROM images LEFT JOIN image_ocr o ON o.image_id=images.id WHERE missing=0''', (MODEL, MODEL)).fetchone())
            result = dict(counts, enabled=enabled, available=self.available(), running=self.running,
                          model=MODEL, error=self.error, provider='CPU', languages='English / Chinese',
                          pending=counts['total']-counts['ready']-counts['failed'],queued=list(self.single))
            if image_id is not None:
                row = conn.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(image_id),)).fetchone()
                if not row:raise ValueError('Image unavailable')
                text = conn.execute('SELECT * FROM image_ocr WHERE image_id=? AND revision=? AND model=?',
                                    (row['id'], image_revision(row), MODEL)).fetchone()
                result['image'] = dict(text) if text else {'status':'pending','text':''}
            return result

    def action(self, payload):
        action = payload.get('action')
        with self.lock, self.db() as conn:
            if action == 'settings':
                enabled = payload.get('enabled')
                if not isinstance(enabled, bool):raise ValueError('enabled must be true or false')
                if enabled and not self.available():raise ValueError('Run setup_ocr.py on the PC first.')
                conn.execute('UPDATE ocr_settings SET enabled=? WHERE id=1', (int(enabled),))
                self.epoch += 1;self.single.clear();self.error = ''
            elif action == 'retry':
                conn.execute("DELETE FROM image_ocr WHERE status='failed'");self.error = ''
            elif action == 'clear':
                conn.execute('UPDATE ocr_settings SET enabled=0 WHERE id=1')
                conn.execute('DELETE FROM image_ocr');self.epoch += 1;self.single.clear();self.error = ''
            elif action == 'image':
                if not self.available():raise ValueError('Run setup_ocr.py on the PC first.')
                image_id = int(payload.get('image_id', 0))
                row = conn.execute('SELECT * FROM images WHERE id=? AND missing=0', (image_id,)).fetchone()
                if not row or payload.get('revision') != image_revision(row):raise ValueError('Photo changed or unavailable. Refresh before extracting text.')
                if len(self.single) >= 100:raise ValueError('OCR queue is full; try again after extraction finishes.')
                if image_id not in self.single:self.single.append(image_id)
            else:raise ValueError('Unknown OCR action')
        self.wake.set()
        return {'ok':True}

    def next_image(self):
        with self.lock, self.db() as conn:
            while self.single:
                row = conn.execute('SELECT * FROM images WHERE id=? AND missing=0', (self.single.pop(0),)).fetchone()
                if row:return dict(row)
            if not conn.execute('SELECT enabled FROM ocr_settings WHERE id=1').fetchone()[0]:return None
            row = conn.execute('''SELECT images.* FROM images WHERE missing=0 AND NOT EXISTS
                (SELECT 1 FROM image_ocr o WHERE o.image_id=images.id AND ''' + LIVE + ''') ORDER BY id LIMIT 1''', (MODEL,)).fetchone()
            return dict(row) if row else None

    def process(self, row, epoch):
        started = time.monotonic();revision = image_revision(row)
        try:
            with self.load_image(row) as picture:text = self.runner(picture)
            status, error = 'ready', None
        except Exception as exc:
            text, status, error = '', 'failed', str(exc)[:500]
        with self.lock, self.db() as conn:
            current = conn.execute('SELECT * FROM images WHERE id=? AND missing=0', (row['id'],)).fetchone()
            if self.stop.is_set() or epoch != self.epoch or not current or image_revision(current) != revision:return None
            conn.execute('''INSERT INTO image_ocr VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(image_id) DO UPDATE SET
                revision=excluded.revision,model=excluded.model,status=excluded.status,text=excluded.text,
                error=excluded.error,updated=excluded.updated,seconds=excluded.seconds''',
                (row['id'],revision,MODEL,status,text[:32000],error,time.time(),time.monotonic()-started))
        return status == 'ready'

    def run(self):
        while not self.stop.is_set():
            self.wake.wait(30);self.wake.clear()
            if not self.available():continue
            job = None;processed = failed = 0;cancelled = False
            try:
                while not self.stop.is_set():
                    with self.lock:
                        row = self.next_image();epoch = self.epoch
                        if not row:break
                        self.running = True
                    if job is None:
                        with self.db() as conn:
                            job = conn.execute("INSERT INTO job_history(kind,started,status) VALUES('ocr',?,'running')", (time.time(),)).lastrowid
                    outcome = self.process(row, epoch)
                    if outcome is True:processed += 1
                    elif outcome is False:failed += 1
                    else:cancelled = True
            except Exception as exc:self.error = str(exc)[:500]
            finally:
                with self.lock:self.running = False
                if job is not None:
                    with self.db() as conn:
                        conn.execute('UPDATE job_history SET finished=?,status=?,processed=?,failed_count=?,error=? WHERE id=?',
                                     (time.time(),'failed' if self.error or failed else 'interrupted' if cancelled else 'complete',processed,failed,self.error or None,job))

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name='ocr');self.thread.start();self.wake.set()

    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=65)
