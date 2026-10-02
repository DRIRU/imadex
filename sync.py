"""Incremental selected-root Drive synchronization with transactional checkpoints."""
import json
import threading
import time
from datetime import datetime

import drive


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS sync_settings(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,interval INTEGER NOT NULL);
    INSERT OR IGNORE INTO sync_settings VALUES(1,0,300);
    CREATE TABLE IF NOT EXISTS sync_sources(folder_id INTEGER PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
        cursor TEXT,updated REAL,failures INTEGER NOT NULL DEFAULT 0,next_run REAL NOT NULL DEFAULT 0,error TEXT);
    CREATE TABLE IF NOT EXISTS drive_nodes(folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
        file_id TEXT NOT NULL,metadata TEXT NOT NULL,PRIMARY KEY(folder_id,file_id));
    CREATE TABLE IF NOT EXISTS job_history(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,source_id INTEGER,
        started REAL NOT NULL,finished REAL,status TEXT NOT NULL,processed INTEGER NOT NULL DEFAULT 0,error TEXT);
    INSERT OR IGNORE INTO project_schema VALUES('sync',1);
    ''')
    columns={r[1] for r in c.execute('PRAGMA table_info(job_history)')}
    if 'failed_count' not in columns:c.execute('ALTER TABLE job_history ADD COLUMN failed_count INTEGER NOT NULL DEFAULT 0')


def upsert_image(c, folder_id, file):
    media = file.get('imageMediaMetadata', {})
    mtime = datetime.fromisoformat(file['modifiedTime'].replace('Z', '+00:00')).timestamp()
    c.execute('''INSERT INTO images(folder_id,path,name,extension,size,mtime,width,height,taken,camera,digest,drive_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET folder_id=excluded.folder_id,
        name=excluded.name,extension=excluded.extension,size=excluded.size,mtime=excluded.mtime,
        width=excluded.width,height=excluded.height,taken=excluded.taken,camera=excluded.camera,digest=excluded.digest,missing=0''',
        (folder_id, 'gdrive:'+file['id'], file['name'], file['mimeType'].split('/')[-1], int(file.get('size',0)),
         mtime, media.get('width',0),media.get('height',0),media.get('time',''),media.get('cameraModel',''),
         file.get('md5Checksum',file['id']), file['id']))
    c.execute("UPDATE images SET taken_source='drive' WHERE path=?",('gdrive:'+file['id'],))


class Cancelled(Exception):
    pass


class Synchronizer:
    def __init__(self, data, db, scan_lock, on_updated=lambda:None):
        self.data,self.db,self.scan_lock,self.on_updated = data,db,scan_lock,on_updated
        self.wake,self.stop = threading.Event(),threading.Event()
        self.lock=threading.RLock()
        self.thread=None
        self.progress={'running':False,'message':'Automatic sync is paused','processed':0}

    def status(self):
        with self.db() as c:
            settings=dict(c.execute('SELECT * FROM sync_settings').fetchone())
            sources=[dict(r) for r in c.execute('SELECT s.folder_id,s.updated,s.failures,s.next_run,s.error FROM sync_sources s')]
            jobs=[dict(r) for r in c.execute('SELECT * FROM job_history ORDER BY id DESC LIMIT 30')]
        with self.lock:
            return {**self.progress,**settings,'enabled':bool(settings['enabled']),'sources':sources,'jobs':jobs}

    def action(self,payload):
        with self.db() as c:
            if payload.get('action')=='settings':
                enabled=payload.get('enabled')
                if not isinstance(enabled,bool):raise ValueError('enabled must be true or false')
                interval=int(payload.get('interval',300))
                if not 30<=interval<=86400:raise ValueError('Choose a polling interval between 30 seconds and one day.')
                c.execute('UPDATE sync_settings SET enabled=?,interval=?',(int(enabled),interval))
            elif payload.get('action')=='retry':
                c.execute('UPDATE sync_sources SET next_run=0,failures=0,error=NULL')
            else:raise ValueError('Unknown sync action')
        self.wake.set()
        return {'ok':True}

    def check(self):
        with self.db() as c:
            enabled=c.execute('SELECT enabled FROM sync_settings').fetchone()[0]
        if not enabled or self.stop.is_set():raise Cancelled()

    def membership(self,node,root,nodes):
        seen=set()
        queue=[node]
        while queue:
            key=queue.pop()
            if key in seen:continue
            seen.add(key)
            if key==root:return True
            file=nodes.get(key)
            if file and not file.get('trashed'):queue.extend(file.get('parents',[]))
        return False

    def resolve(self,file,root,nodes):
        """Resolve unknown ancestor chains before deciding whether a change belongs to this root."""
        queue=list(file.get('parents',[]));seen=set()
        while queue:
            self.check()
            key=queue.pop()
            if key==root:return True
            if key in seen:continue
            seen.add(key)
            parent=nodes.get(key)
            if parent is None:
                try:parent=drive.metadata(self.data,key,drive.FILE_FIELDS)
                except drive.DriveError as error:
                    if error.code==404:continue
                    raise
                nodes[key]=parent
            if not parent.get('trashed'):queue.extend(parent.get('parents',[]))
        return False

    def publish(self,folder_id,root,nodes,cursor):
        self.check()
        with self.db() as c:
            other_roots=[r['path'].split(':')[1] for r in c.execute("SELECT path FROM folders WHERE id<>? AND path LIKE 'gdrive:%'",(folder_id,))]
        for other in other_roots:
            if self.membership(other,root,nodes) or self.resolve(nodes[root],other,nodes):
                raise ValueError('Selected Drive folders now overlap. Choose independent roots before synchronizing; catalog/checkpoint preserved.')
        current={key:file for key,file in nodes.items() if not file.get('trashed') and self.membership(key,root,nodes)}
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            # Pause wins over a queued network response before catalog/checkpoint commit.
            if not c.execute('SELECT enabled FROM sync_settings').fetchone()[0]:raise Cancelled()
            c.execute('DELETE FROM drive_nodes WHERE folder_id=?',(folder_id,))
            c.executemany('INSERT INTO drive_nodes VALUES(?,?,?)',((folder_id,key,json.dumps(file)) for key,file in current.items()))
            c.execute('UPDATE images SET missing=1 WHERE folder_id=? AND drive_id IS NOT NULL',(folder_id,))
            images=[file for file in current.values() if file.get('mimeType','').startswith('image/')]
            for file in images:upsert_image(c,folder_id,file)
            c.execute('UPDATE sync_sources SET cursor=?,updated=?,failures=0,error=NULL,next_run=? WHERE folder_id=?',
                      (cursor,time.time(),time.time()+c.execute('SELECT interval FROM sync_settings').fetchone()[0],folder_id))
            c.execute('UPDATE folders SET scanned_at=? WHERE id=?',(time.time(),folder_id))
        self.on_updated()
        return len(images)

    def sync_source(self,folder_id,root,allow_reset=True):
        with self.db() as c:
            source=c.execute('SELECT * FROM sync_sources WHERE folder_id=?',(folder_id,)).fetchone()
            nodes={r['file_id']:json.loads(r['metadata']) for r in c.execute('SELECT * FROM drive_nodes WHERE folder_id=?',(folder_id,))}
        cursor=source['cursor']
        processed=0
        if not cursor:
            cursor=drive.start_cursor(self.data) # Capture before listing, so changes during bootstrap are not lost.
            nodes={}
            for file in drive.walk_nodes(self.data,root):
                self.check();nodes[file['id']]=file
                with self.lock:self.progress.update(message='Initial Drive listing: '+file['name'],processed=len(nodes))
            self.publish(folder_id,root,nodes,cursor)
            processed=len(nodes)
        while True:
            self.check()
            try:page=drive.changes_page(self.data,cursor)
            except drive.DriveError as error:
                if error.code==410:
                    if not allow_reset:raise ValueError('Drive repeatedly rejected a fresh change checkpoint. Reconnect or retry later.') from error
                    with self.db() as c:c.execute('UPDATE sync_sources SET cursor=NULL WHERE folder_id=?',(folder_id,))
                    return self.sync_source(folder_id,root,allow_reset=False)
                raise
            for change in page.get('changes',[]):
                self.check();key=change.get('fileId')
                if not key:continue # Drive-level changes have no file identifier.
                if key==root and (change.get('removed') or change.get('file',{}).get('trashed')):
                    raise ValueError('Selected root is unavailable. Check Drive access before retrying; existing catalog preserved.')
                if change.get('removed'):
                    nodes.pop(key,None)
                else:
                    file=change.get('file')
                    if not file:continue
                    known=key in nodes
                    if known or key==root or self.resolve(file,root,nodes):
                        nodes[key]=file
                        if not known and file.get('mimeType')=='application/vnd.google-apps.folder' and not file.get('trashed'):
                            for child in drive.walk_nodes(self.data,key):
                                self.check();nodes[child['id']]=child
                processed+=1
            next_cursor=page.get('nextPageToken') or page.get('newStartPageToken')
            if not next_cursor:raise ValueError('Drive returned no change checkpoint. Current checkpoint preserved.')
            self.publish(folder_id,root,nodes,next_cursor)
            cursor=next_cursor
            with self.lock:self.progress.update(message=f'Drive changes processed: {processed}',processed=processed)
            if not page.get('nextPageToken'):return processed

    def run_once(self):
        if not drive.state(self.data)['connected']:
            with self.lock:self.progress.update(message='Connect Google Drive to start automatic sync')
            return
        if not self.scan_lock.acquire(blocking=False):return
        try:
            with self.db() as c:
                for folder in c.execute("SELECT * FROM folders WHERE path LIKE 'gdrive:%'").fetchall():
                    c.execute('INSERT OR IGNORE INTO sync_sources(folder_id) VALUES(?)',(folder['id'],))
                sources=c.execute("SELECT f.id,f.path FROM folders f JOIN sync_sources s ON s.folder_id=f.id WHERE s.next_run<=?",(time.time(),)).fetchall()
            for source in sources:
                self.check();start=time.time()
                with self.db() as c:key=c.execute("INSERT INTO job_history(kind,source_id,started,status) VALUES('drive-sync',?,?,'running')",(source['id'],start)).lastrowid
                with self.lock:self.progress.update(running=True,processed=0,message='Synchronizing Drive…')
                try:
                    count=self.sync_source(source['id'],source['path'].split(':')[1])
                    state,error='complete',None
                except Cancelled:
                    count,state,error=0,'paused',None
                except Exception as exc:
                    count,state,error=0,'failed',str(exc)
                    with self.db() as c:
                        failures=c.execute('SELECT failures FROM sync_sources WHERE folder_id=?',(source['id'],)).fetchone()[0]+1
                        c.execute('UPDATE sync_sources SET failures=?,error=?,next_run=? WHERE folder_id=?',
                            (failures,error,time.time()+min(3600,30*2**min(failures,7)),source['id']))
                with self.db() as c:
                    c.execute('UPDATE job_history SET finished=?,status=?,processed=?,error=?,failed_count=? WHERE id=?',(time.time(),state,count,error,int(state=='failed'),key))
                    c.execute('DELETE FROM job_history WHERE id NOT IN (SELECT id FROM job_history ORDER BY id DESC LIMIT 200)')
                with self.lock:self.progress.update(running=False,message=error or ('Sync paused' if state=='paused' else 'Drive is up to date'))
        finally:
            self.scan_lock.release()

    def start(self):
        with self.db() as c:c.execute("UPDATE job_history SET status='interrupted',finished=? WHERE status='running' AND kind='drive-sync'",(time.time(),))
        def work():
            while not self.stop.is_set():
                try:
                    self.check();self.run_once()
                except Cancelled:pass
                except Exception as error:
                    with self.lock:self.progress.update(running=False,message=str(error))
                self.wake.wait(5);self.wake.clear()
        self.thread=threading.Thread(target=work,daemon=True,name='drive-sync');self.thread.start()

    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=5)
