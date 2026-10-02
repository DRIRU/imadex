"""Bounded private Drive thumbnail cache. Never stores original image downloads."""
import hashlib
import io
import threading
import time
import re
from pathlib import Path
from PIL import Image, ImageOps
import drive
from people import image_revision


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS thumbnail_cache(key TEXT PRIMARY KEY,image_id INTEGER NOT NULL,
        revision TEXT NOT NULL,bytes INTEGER NOT NULL,used REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS cache_settings(id INTEGER PRIMARY KEY CHECK(id=1),budget INTEGER NOT NULL);
    INSERT OR IGNORE INTO cache_settings VALUES(1,268435456);
    ''')


class ThumbnailCache:
    def __init__(self,data,db):
        self.path=Path(data)/'drive-thumbnails';self.path.mkdir(parents=True,exist_ok=True)
        self.data,self.db=data,db
        self.lock=threading.RLock()
        with self.db() as c:
            known={r[0] for r in c.execute('SELECT key FROM thumbnail_cache')}
            for path in self.path.iterdir():
                match=re.fullmatch(r'([0-9a-f]{64})\.(jpg|tmp)',path.name)
                if path.is_file() and match and (match[2]=='tmp' or match[1] not in known):path.unlink()
            self.prune(c,c.execute('SELECT budget FROM cache_settings').fetchone()[0])

    def status(self):
        with self.db() as c:
            return {'budget':c.execute('SELECT budget FROM cache_settings').fetchone()[0],
                    'used':c.execute('SELECT coalesce(sum(bytes),0) FROM thumbnail_cache').fetchone()[0]}

    def prune(self,c,budget):
        rows=c.execute('SELECT * FROM thumbnail_cache ORDER BY used DESC').fetchall()
        used=0
        for row in rows:
            path=self.path/(row['key']+'.jpg')
            if used+row['bytes']>budget or not path.exists():
                path.unlink(missing_ok=True);c.execute('DELETE FROM thumbnail_cache WHERE key=?',(row['key'],))
            else:used+=row['bytes']

    def action(self,payload):
        with self.lock,self.db() as c:
            if payload.get('action')=='budget':
                budget=int(payload['budget'])
                if not 0<=budget<=2*1024**3:raise ValueError('Thumbnail budget must be between 0 and 2 GB.')
                c.execute('UPDATE cache_settings SET budget=?',(budget,));self.prune(c,budget)
            elif payload.get('action')=='clear':
                for row in c.execute('SELECT key FROM thumbnail_cache').fetchall():
                    (self.path/(row['key']+'.jpg')).unlink(missing_ok=True)
                c.execute('DELETE FROM thumbnail_cache')
            else:raise ValueError('Unknown thumbnail action')
        return {'ok':True}

    def get(self,row):
        signature=image_revision(row)
        key=hashlib.sha256((str(row['id'])+':'+signature).encode()).hexdigest()
        path=self.path/(key+'.jpg')
        with self.lock:
            with self.db() as c:
                if c.execute('SELECT 1 FROM thumbnail_cache WHERE key=?',(key,)).fetchone() and path.exists():
                    c.execute('UPDATE thumbnail_cache SET used=? WHERE key=?',(time.time(),key))
                    return path.read_bytes()
            with drive.thumbnail(self.data,row['drive_id']) as response:raw=response.read(5*1024**2+1)
            if len(raw)>5*1024**2:raise ValueError('Drive thumbnail exceeded the 5 MB preview limit.')
            with Image.open(io.BytesIO(raw)) as source:
                image=ImageOps.exif_transpose(source).convert('RGB');image.thumbnail((640,640))
                output=io.BytesIO();image.save(output,format='JPEG',quality=85);raw=output.getvalue()
            with self.db() as c:
                budget=c.execute('SELECT budget FROM cache_settings').fetchone()[0]
                for old in c.execute('SELECT key FROM thumbnail_cache WHERE image_id=? AND key<>?',(row['id'],key)).fetchall():
                    (self.path/(old['key']+'.jpg')).unlink(missing_ok=True);c.execute('DELETE FROM thumbnail_cache WHERE key=?',(old['key'],))
                if budget>=len(raw):
                    pending=path.with_suffix('.tmp');pending.write_bytes(raw);pending.replace(path)
                    c.execute('INSERT OR REPLACE INTO thumbnail_cache VALUES(?,?,?,?,?)',(key,row['id'],signature,len(raw),time.time()))
                    self.prune(c,budget)
            return raw
