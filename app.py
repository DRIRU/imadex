"""Local image catalog. Run: python app.py --port 8765"""
import argparse
import hashlib
import json
import mimetypes
import os
import sqlite3
import threading
import time
import re
import base64
import hmac
from contextlib import contextmanager
from datetime import datetime
import drive
import people
import recognition
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('IMAGE_INDEX_DATA', BASE / 'data'))
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.avif'}
scan_lock = threading.Lock()
status_lock = threading.Lock()
scan_status = {'running': False, 'processed': 0, 'errors': [], 'message': 'Ready to index'}
semantic_index = None
face_detector = None
recognizer = None


def configure_access(server, public_origin=None, password=None):
    server.public_origin = None
    server.access_password = None
    if public_origin:
        parsed = urlparse(public_origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Public origin must be an HTTPS origin, e.g. https://photos.example.com')
        if not password or len(password) < 16:
            raise ValueError('Set FRAME_PASSWORD to a private password of at least 16 characters before enabling tunnel access.')
        server.public_origin = 'https://' + parsed.netloc.lower()
        server.access_password = password


@contextmanager
def db():
    conn = sqlite3.connect(DATA / 'catalog.sqlite3', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function("content_revision", 4, people.revision, deterministic=True)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize():
    (DATA / 'thumbnails').mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS folders (
          id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, scanned_at REAL);
        CREATE TABLE IF NOT EXISTS images (
          id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL, path TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL, extension TEXT, size INTEGER, mtime REAL,
          width INTEGER, height INTEGER, taken TEXT, camera TEXT,
          digest TEXT, favorite INTEGER DEFAULT 0, tags TEXT DEFAULT '', missing INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS images_folder ON images(folder_id);
        CREATE INDEX IF NOT EXISTS images_digest ON images(digest);
        ''')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(images)')}
        if 'drive_id' not in columns:
            conn.execute('ALTER TABLE images ADD COLUMN drive_id TEXT')
        people.migrate(conn)
        from detection import migrate as migrate_detection
        migrate_detection(conn)
        recognition.migrate(conn)


def scan_drive(folder_id, root_id):
    seen = set()
    processed = 0
    for file in drive.walk(DATA, root_id):
        media = file.get('imageMediaMetadata', {})
        key = 'gdrive:' + file['id']
        seen.add(key)
        with db() as conn:
            conn.execute('''INSERT INTO images(folder_id,path,name,extension,size,mtime,width,height,taken,camera,digest,drive_id)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET name=excluded.name,extension=excluded.extension,
              size=excluded.size,mtime=excluded.mtime,width=excluded.width,height=excluded.height,taken=excluded.taken,
              camera=excluded.camera,digest=excluded.digest,missing=0''',
              (folder_id,key,file['name'],file['mimeType'].split('/')[-1],int(file.get('size',0)),
               datetime.fromisoformat(file['modifiedTime'].replace('Z','+00:00')).timestamp(),
               media.get('width',0),media.get('height',0),media.get('time',''),media.get('cameraModel',''),
               file.get('md5Checksum',file['id']),file['id']))
        processed += 1
        set_status(processed=processed, message=f"Indexing {file['name']}")
    with db() as conn:
        for row in conn.execute('SELECT id,path FROM images WHERE folder_id=?', (folder_id,)).fetchall():
            if row['path'] not in seen:
                conn.execute('UPDATE images SET missing=1 WHERE id=?', (row['id'],))
        conn.execute('UPDATE folders SET scanned_at=? WHERE id=?', (time.time(), folder_id))
    set_status(message=f'Scan complete · {processed:,} images checked')


def set_status(**values):
    with status_lock:
        scan_status.update(values)


def scan(folder_id):
    try:
        with db() as conn:
            folder = conn.execute('SELECT * FROM folders WHERE id=?', (folder_id,)).fetchone()
            if folder['path'].startswith('gdrive:'):
                scan_drive(folder_id, folder['path'].split(':')[1])
                return
            root = Path(folder['path'])
            if not root.is_dir():
                raise ValueError('Folder is unavailable. Connect the drive and try again.')
            known = {r['path']: dict(r) for r in conn.execute('SELECT * FROM images WHERE folder_id=?', (folder_id,))}
        seen, errors, processed = set(), [], 0
        def walk_error(error):
            raise error
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink() and (Path(directory) / d).resolve() != DATA.resolve()]
            for name in files:
                path = Path(directory) / name
                if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                    continue
                key = str(path.resolve())
                seen.add(key)
                try:
                    info = path.stat()
                    old = known.get(key)
                    if old and old['size'] == info.st_size and old['mtime'] == info.st_mtime and (DATA / 'thumbnails' / (old['digest'] + '.jpg')).exists():
                        with db() as conn:
                            conn.execute('UPDATE images SET missing=0 WHERE id=?', (old['id'],))
                    else:
                        with path.open('rb') as stream:
                            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                        with Image.open(path) as source:
                            exif = source.getexif()
                            taken = str(exif.get(306, ''))
                            camera = str(exif.get(272, ''))
                            picture = ImageOps.exif_transpose(source)
                            width, height = picture.size
                            picture.thumbnail((640, 640))
                            if picture.mode in ('RGBA', 'LA') or (picture.mode == 'P' and 'transparency' in picture.info):
                                rgba = picture.convert('RGBA')
                                picture = Image.new('RGB', rgba.size, '#17191e')
                                picture.paste(rgba, mask=rgba.getchannel('A'))
                            picture.convert('RGB').save(DATA / 'thumbnails' / (digest + '.jpg'), quality=85)
                        with db() as conn:
                            conn.execute('''INSERT INTO images(folder_id,path,name,extension,size,mtime,width,height,taken,camera,digest)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                            folder_id=excluded.folder_id,name=excluded.name,extension=excluded.extension,size=excluded.size,
                            mtime=excluded.mtime,width=excluded.width,height=excluded.height,taken=excluded.taken,
                            camera=excluded.camera,digest=excluded.digest,missing=0''',
                            (folder_id,key,name,path.suffix.lower()[1:],info.st_size,info.st_mtime,width,height,taken,camera,digest))
                except Exception as error:
                    errors.append(f'{name}: {error}')
                processed += 1
                set_status(processed=processed, errors=errors[-20:], message=f'Indexing {name}')
        with db() as conn:
            for key, row in known.items():
                if key not in seen:
                    conn.execute('UPDATE images SET missing=1 WHERE id=?', (row['id'],))
            conn.execute('UPDATE folders SET scanned_at=? WHERE id=?', (time.time(), folder_id))
        set_status(message=f'Scan complete · {processed:,} images checked', errors=errors[-20:])
    except Exception as error:
        set_status(message=f'Scan failed: {error}')
    finally:
        set_status(running=False)
        scan_lock.release()
        if semantic_index is not None:
            semantic_index.queue()
        if recognizer is not None:
            recognizer.schedule()


def start_scan(folder_id):
    if not scan_lock.acquire(blocking=False):
        raise ValueError('A scan is already running. Please wait for it to finish.')
    set_status(running=True, processed=0, errors=[], message='Starting scan…')
    threading.Thread(target=scan, args=(folder_id,), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, data, code=200):
        content = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(content)

    def trusted(self):
        host = self.headers.get('Host', '').lower()
        valid = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origins = {'http://' + item for item in valid}
        public = getattr(self.server, 'public_origin', None)
        if public:
            valid.add(urlparse(public).netloc)
            origins.add(public)
        origin = self.headers.get('Origin')
        return host in valid and (not origin or origin in origins)

    def authenticated(self):
        password = getattr(self.server, 'access_password', None)
        if password:
            expected = 'Basic ' + base64.b64encode(('frame:' + password).encode()).decode()
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode()):
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Basic realm="Frame personal library", charset="UTF-8"')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return False
        return True

    def on_pc(self):
        local = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        return (self.headers.get('Host', '') in local
                and self.headers.get('Origin', 'http://' + self.headers.get('Host', '')) in {'http://' + host for host in local}
                and self.headers.get('X-Forwarded-Proto', 'http') == 'http')

    def send_file(self, path, content_type=None):
        try:
            stream = path.open('rb')
        except OSError:
            self.respond({'error': 'File is unavailable. Rescan the folder.'}, 404)
            return
        with stream:
            self.send_response(200)
            self.send_header('Content-Type', content_type or mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'private, max-age=300' if content_type or path.suffix.lower() in EXTENSIONS else 'no-cache')
            self.end_headers()
            try:
                while chunk := stream.read(256 * 1024):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        if not self.trusted():
            return self.respond({'error': 'Host or origin is not allowed'}, 403)
        if not self.authenticated():
            return
        url = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == '/oauth/callback':
                drive.callback(DATA, params)
                self.send_response(303)
                self.send_header('Location', '/')
                self.end_headers()
                return
            if url.path == '/api/drive':
                return self.respond({**drive.state(DATA), 'can_connect': self.on_pc()})
            if url.path == '/api/people':
                return self.respond(people.People(db).listing(params.get('q', ''), params.get('offset', 0)))
            if re.fullmatch(r'/api/images/\d+/people', url.path):
                return self.respond(people.People(db).image(int(url.path.split('/')[3])))
            if url.path == '/api/detection':
                return self.respond(face_detector.status(params.get('image_id')) if face_detector else {'enabled': False, 'available': False})
            if url.path == '/api/recognition':
                return self.respond(recognizer.status(params.get('image_id')) if recognizer else {'enabled': False, 'available': False})
            if url.path == '/api/recognition/faces':
                if recognizer is None:
                    return self.respond({'error': 'Face recognition is not started.'}, 503)
                return self.respond(recognizer.image_faces(int(params['image_id'])))
            if url.path == '/api/recognition/suggestions':
                if recognizer is None:
                    return self.respond({'error': 'Face recognition is not started.'}, 503)
                return self.respond(recognizer.suggestions(params.get('offset', 0)))
            if url.path == '/api/embeddings':
                if semantic_index is None:
                    return self.respond({'error': 'Embedding service is not started. Run the app using start.ps1.'}, 503)
                return self.respond(semantic_index.status())
            with db() as conn:
                if url.path == '/api/status':
                    with status_lock:
                        return self.respond(dict(scan_status))
                if url.path == '/api/library':
                    folders = [dict(r) for r in conn.execute('''SELECT f.*,COUNT(i.id) AS count FROM folders f
                    LEFT JOIN images i ON i.folder_id=f.id AND i.missing=0 GROUP BY f.id''')]
                    stats = dict(conn.execute('''SELECT COUNT(*) AS total,COALESCE(SUM(size),0) AS bytes,
                    COALESCE(SUM(favorite),0) AS favorites,
                    COUNT(*)-COUNT(DISTINCT digest) AS duplicates FROM images WHERE missing=0''').fetchone())
                    formats = [r[0] for r in conn.execute('SELECT DISTINCT extension FROM images WHERE missing=0 ORDER BY extension')]
                    return self.respond({'folders': folders, 'stats': stats, 'formats': formats})
                if url.path == '/api/images':
                    clauses, values = ['missing=0'], []
                    semantic_query = params.get('mode') == 'semantic' and bool(params.get('q', '').strip())
                    for word in ([] if semantic_query else params.get('q', '').split()):
                        clauses.append('(name LIKE ? OR path LIKE ? OR tags LIKE ? OR camera LIKE ?)')
                        values.extend(['%' + word + '%'] * 4)
                    if params.get('folder'):
                        clauses.append('folder_id=?'); values.append(int(params['folder']))
                    if params.get('format'):
                        clauses.append('extension=?'); values.append(params['format'])
                    if params.get('view') == 'favorites':
                        clauses.append('favorite=1')
                    if params.get('view') == 'duplicates':
                        clauses.append('digest IN (SELECT digest FROM images WHERE missing=0 GROUP BY digest HAVING COUNT(*)>1)')
                    if params.get('person'):
                        clauses.append('id IN (SELECT ip.image_id FROM image_people ip JOIN images i ON i.id=ip.image_id WHERE ip.person_id=? AND ' + people.LIVE + ')')
                        values.append(int(params['person']))
                    if params.get('view') == 'review':
                        from detection import MODEL as detection_model
                        clauses.append('id IN (SELECT image_id FROM detections WHERE model=?)')
                        values.append(detection_model)
                        clauses.append("id IN (SELECT image_id FROM detections WHERE status='ready' AND region_count>0 AND revision=content_revision(images.digest,images.drive_id,images.size,images.mtime)) AND NOT EXISTS (SELECT 1 FROM image_people ip WHERE ip.image_id=images.id AND ip.revision=content_revision(images.digest,images.drive_id,images.size,images.mtime))")
                    where = ' AND '.join(clauses)
                    order = {'newest': 'mtime DESC,id DESC', 'oldest': 'mtime ASC,id ASC', 'name': 'name COLLATE NOCASE,id', 'largest': 'size DESC,id DESC'}.get(params.get('sort'), 'mtime DESC,id DESC')
                    if params.get('view') == 'duplicates':
                        order = 'digest,' + order
                    offset = max(0, int(params.get('offset', 0)))
                    if semantic_query:
                        if semantic_index is None:
                            return self.respond({'error': 'Embedding service is unavailable. Start the app with start.ps1.'}, 503)
                        result = semantic_index.search(params['q'], where, values, offset)
                        for row in result['items']:
                            row['revision'] = people.image_revision(row)
                        return self.respond(result)
                    total = conn.execute('SELECT COUNT(*) FROM images WHERE ' + where, values).fetchone()[0]
                    rows = [dict(r) for r in conn.execute('SELECT * FROM images WHERE ' + where + ' ORDER BY ' + order + ' LIMIT 80 OFFSET ?', [*values, offset])]
                    for row in rows:
                        row['revision'] = people.image_revision(row)
                    return self.respond({'items': rows, 'total': total})
                if url.path.startswith('/image/') or url.path.startswith('/thumb/'):
                    row = conn.execute('SELECT * FROM images WHERE id=?', (int(url.path.rsplit('/', 1)[1]),)).fetchone()
                    if not row:
                        return self.respond({'error': 'Image not found'}, 404)
                    thumb = url.path.startswith('/thumb/')
                    if row['drive_id']:
                        remote = drive.thumbnail(DATA, row['drive_id']) if thumb else drive.request(DATA, 'files/' + row['drive_id'], {'alt': 'media', 'supportsAllDrives': 'true'})
                        with remote:
                            self.send_response(200)
                            self.send_header('Content-Type', remote.headers.get('Content-Type', 'application/octet-stream'))
                            self.send_header('Cache-Control', 'private, max-age=300')
                            self.send_header('X-Content-Type-Options', 'nosniff')
                            self.send_header('Content-Security-Policy', "sandbox; default-src 'none'")
                            if remote.headers.get('Content-Length'):
                                self.send_header('Content-Length', remote.headers['Content-Length'])
                            self.end_headers()
                            try:
                                while chunk := remote.read(256 * 1024):
                                    self.wfile.write(chunk)
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                        return
                    return self.send_file(DATA / 'thumbnails' / (row['digest'] + '.jpg') if thumb else Path(row['path']), 'image/jpeg' if thumb else None)
            static = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/mobile.css': 'mobile.css', '/semantic.css': 'semantic.css', '/favicon.svg': 'favicon.svg', '/people.js': 'people.js', '/people.css': 'people.css'}
            if url.path in static:
                return self.send_file(BASE / 'web' / static[url.path])
            self.respond({'error': 'Not found'}, 404)
        except (ValueError, TypeError, OSError) as error:
            self.respond({'error': str(error)}, 400)

    def do_POST(self):
        if not self.trusted() or self.headers.get('Content-Type') != 'application/json':
            return self.respond({'error': 'Requests require an allowed origin and JSON content'}, 403)
        if not self.authenticated():
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                raise ValueError('Invalid request size')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('JSON object required')
            if self.path == '/api/people':
                return self.respond(people.People(db).mutate(payload))
            if self.path == '/api/detection':
                if face_detector is None:
                    raise ValueError('Detection service unavailable')
                return self.respond(face_detector.action(payload))
            if self.path == '/api/recognition':
                if recognizer is None:
                    raise ValueError('Recognition service unavailable')
                return self.respond(recognizer.action(payload))
            if self.path == '/api/embeddings':
                if semantic_index is None:
                    raise ValueError('Embedding service is not started. Run the app using start.ps1.')
                semantic_index.queue(retry_failed=True)
                return self.respond({'ok': True})
            if self.path == '/api/drive/connect':
                if not self.on_pc():
                    raise ValueError('Connect Google Drive on the PC running Frame at http://127.0.0.1:' + str(self.server.server_port) + '. Once connected, you can browse from your phone.')
                return self.respond({'url': drive.authorize(DATA, self.server.server_port)})
            with db() as conn:
                if self.path == '/api/folders':
                    if payload.get('source') == 'drive':
                        value = str(payload['path']).strip()
                        match = re.search(r'/folders/([\w-]+)', value)
                        file_id = match[1] if match else value
                        if not re.fullmatch(r'[\w-]+', file_id):
                            raise ValueError('Enter a Google Drive folder link or folder ID.')
                        folder = drive.metadata(DATA, file_id, 'id,name,mimeType')
                        if folder['mimeType'] != 'application/vnd.google-apps.folder':
                            raise ValueError('Please choose a folder, not a file.')
                        key = 'gdrive:' + file_id + ':' + folder['name']
                        if conn.execute('SELECT id FROM folders WHERE path LIKE ?', ('gdrive:' + file_id + ':%',)).fetchone():
                            raise ValueError('This folder is already in your library.')
                        cursor = conn.execute('INSERT INTO folders(path) VALUES(?)', (key,))
                        conn.commit()
                        return self.respond({'id': cursor.lastrowid}, 201)
                    root = Path(payload['path']).expanduser().resolve()
                    if not root.is_dir():
                        raise ValueError('Folder not found. Enter a full folder path.')
                    for folder in conn.execute('SELECT path FROM folders'):
                        existing = Path(folder['path'])
                        if root == existing or root in existing.parents or existing in root.parents:
                            raise ValueError('This folder or an overlapping parent/subfolder is already in your library.')
                    cursor = conn.execute('INSERT INTO folders(path) VALUES(?)', (str(root),))
                    folder_id = cursor.lastrowid
                    conn.commit()
                    return self.respond({'id': folder_id}, 201)
                if self.path == '/api/scan':
                    folder_id = int(payload['folder_id'])
                    if not conn.execute('SELECT id FROM folders WHERE id=?', (folder_id,)).fetchone():
                        raise ValueError('Folder not found')
                    start_scan(folder_id)
                    return self.respond({'ok': True})
                if self.path == '/api/image':
                    image_id = int(payload['id'])
                    if not conn.execute('SELECT id FROM images WHERE id=?', (image_id,)).fetchone():
                        raise ValueError('Image not found')
                    if 'favorite' in payload:
                        conn.execute('UPDATE images SET favorite=? WHERE id=?', (int(bool(payload['favorite'])), image_id))
                    if 'tags' in payload:
                        conn.execute('UPDATE images SET tags=? WHERE id=?', (str(payload['tags'])[:2000], image_id))
                    conn.commit()
                    return self.respond({'ok': True})
            self.respond({'error': 'Not found'}, 404)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.IntegrityError) as error:
            self.respond({'error': str(error)}, 400)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default=os.environ.get('IMAGE_INDEX_HOST', '127.0.0.1'), help='Bind address. Defaults to loopback; use 0.0.0.0 only inside a container.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--public-origin', default=os.environ.get('FRAME_PUBLIC_ORIGIN'), help='Exact HTTPS tunnel origin. Requires FRAME_PASSWORD.')
    args = parser.parse_args()
    initialize()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        configure_access(server, args.public_origin, os.environ.get('FRAME_PASSWORD'))
    except ValueError as error:
        server.server_close()
        parser.error(str(error))
    from semantic import SemanticIndex
    semantic_index = SemanticIndex(DATA, db)
    semantic_index.start()
    from detection import Detector
    face_detector = Detector(DATA, db, semantic_index.load_image)
    face_detector.start()
    recognizer = recognition.Recognizer(DATA, db, semantic_index.load_image)
    recognizer.start()
    print(f'Image library: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    finally:
        face_detector.close()
        recognizer.close()
        semantic_index.close()
