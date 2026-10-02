"""Personal gallery organization. All mutations affect local metadata only."""
import json
import re
import time
from datetime import date, datetime, timezone
from people import image_revision


def capture_day(value):
    value=str(value or '').strip()
    # EXIF uses YYYY:MM:DD; Drive can return an ISO timestamp.
    match=re.match(r'^(\d{4})[:-](\d{2})[:-](\d{2})',value)
    if not match:return None
    try:return date(*map(int,match.groups())).isoformat()
    except ValueError:return None


def image_day(taken,mtime,basis):
    captured=capture_day(taken)
    if basis=='capture':return captured
    if basis=='best' and captured:return captured
    try:return datetime.fromtimestamp(float(mtime),timezone.utc).date().isoformat()
    except (TypeError,ValueError,OverflowError,OSError):return None


def date_filters(params,clauses,values):
    basis=params.get('date_basis','best')
    if basis not in ('best','capture','modified'):raise ValueError('Choose capture, modified, or best date.')
    # Old local catalogs used EXIF DateTime (editing time); a rescan reads DateTimeOriginal.
    expr="image_day(CASE WHEN drive_id IS NOT NULL OR taken_source='exif-original' THEN taken ELSE '' END,mtime,'"+basis+"')"
    for key,operator in [('from','>='),('to','<=')]:
        if params.get(key):
            day=date.fromisoformat(params[key]).isoformat()
            clauses.append(expr+operator+'?');values.append(day)
    if params.get('from') and params.get('to') and params['from']>params['to']:raise ValueError('Start date must be before end date.')
    if params.get('undated')=='1':clauses.append(expr+' IS NULL')
    if params.get('album'):
        clauses.append('id IN (SELECT image_id FROM album_images WHERE album_id=?)');values.append(int(params['album']))
    return expr


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS albums(id INTEGER PRIMARY KEY,name TEXT NOT NULL,created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS album_images(album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
        image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,PRIMARY KEY(album_id,image_id));
    CREATE TABLE IF NOT EXISTS saved_searches(id INTEGER PRIMARY KEY,name TEXT NOT NULL,filters TEXT NOT NULL,created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS metadata_edits(id INTEGER PRIMARY KEY,created REAL NOT NULL,before_state TEXT NOT NULL,
        after_state TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS duplicate_reviews(left_id INTEGER NOT NULL,right_id INTEGER NOT NULL,
        left_revision TEXT NOT NULL,right_revision TEXT NOT NULL,decision TEXT NOT NULL,updated REAL NOT NULL,
        PRIMARY KEY(left_id,right_id));
    INSERT OR IGNORE INTO project_schema VALUES('gallery',1);
    ''')


class Gallery:
    def __init__(self,db):self.db=db

    @staticmethod
    def name(payload):
        name=str(payload.get('name','')).strip()
        if not 1<=len(name)<=100:raise ValueError('Enter a name of 1–100 characters.')
        return name

    def listing(self):
        with self.db() as c:
            albums=[dict(r) for r in c.execute('''SELECT a.*,COUNT(i.id) AS count FROM albums a LEFT JOIN album_images m ON m.album_id=a.id
                LEFT JOIN images i ON i.id=m.image_id AND i.missing=0 GROUP BY a.id ORDER BY a.name COLLATE NOCASE''')]
            searches=[dict(r) for r in c.execute('SELECT * FROM saved_searches ORDER BY name COLLATE NOCASE')]
            for search in searches:search['filters']=json.loads(search['filters'])
            last=c.execute('SELECT id FROM metadata_edits WHERE undone=0 ORDER BY id DESC LIMIT 1').fetchone()
        return {'albums':albums,'searches':searches,'undo_id':last[0] if last else None}

    def snapshot(self,c,image_id):
        row=c.execute('SELECT * FROM images WHERE id=? AND missing=0',(image_id,)).fetchone()
        if row is None:raise ValueError('An image is unavailable. Refresh your selection.')
        return {'id':row['id'],'revision':image_revision(row),'tags':row['tags'],'favorite':row['favorite'],
                'albums':[r[0] for r in c.execute('SELECT album_id FROM album_images WHERE image_id=? ORDER BY album_id',(image_id,))]}

    def mutate(self,payload):
        action=payload.get('action')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if action=='create_album':
                key=c.execute('INSERT INTO albums(name,created) VALUES(?,?)',(self.name(payload),time.time())).lastrowid
                return {'ok':True,'id':key}
            if action in ('rename_album','delete_album','delete_search'):
                key=int(payload['id']);table='saved_searches' if action=='delete_search' else 'albums'
                if not c.execute('SELECT id FROM '+table+' WHERE id=?',(key,)).fetchone():raise ValueError('Item no longer exists.')
                if action=='rename_album':c.execute('UPDATE albums SET name=? WHERE id=?',(self.name(payload),key))
                else:c.execute('DELETE FROM '+table+' WHERE id=?',(key,))
                return {'ok':True}
            if action=='save_search':
                raw=payload.get('filters')
                if not isinstance(raw,dict):raise ValueError('Search filters required.')
                allowed={'q','mode','sort','format','view','folder','person','album','date_basis','from','to','undated','similar'}
                filters={key:str(value) for key,value in raw.items() if key in allowed}
                if any(len(value)>500 for value in filters.values()):raise ValueError('Search filter is too long.')
                for key in ('folder','person','album','similar'):
                    if filters.get(key) and int(filters[key])<1:raise ValueError('Invalid filter identifier.')
                date_filters(filters,[],[])
                key=c.execute('INSERT INTO saved_searches(name,filters,created) VALUES(?,?,?)',(self.name(payload),json.dumps(filters),time.time())).lastrowid
                return {'ok':True,'id':key}
            if action=='bulk':
                photos=payload.get('photos')
                if not isinstance(photos,list) or not 1<=len(photos)<=100:raise ValueError('Select 1–100 photos.')
                if any(not isinstance(p,dict) or not isinstance(p.get('id'),int) for p in photos):raise ValueError('Invalid photo selection.')
                ids=[p['id'] for p in photos]
                if len(set(ids))!=len(ids):raise ValueError('Duplicate photo selection.')
                before=[self.snapshot(c,key) for key in ids]
                if any(p.get('revision')!=s['revision'] for p,s in zip(photos,before)):raise ValueError('An image changed. Refresh your selection.')
                if not any(key in payload for key in ('tags','favorite','album_id')):raise ValueError('Choose an edit.')
                if 'favorite' in payload and not isinstance(payload['favorite'],bool):raise ValueError('favorite must be true or false')
                if 'tags' in payload and (not isinstance(payload['tags'],str) or len(payload['tags'])>1000):raise ValueError('Tags must be text of at most 1000 characters.')
                album=None
                if 'album_id' in payload:
                    album=int(payload['album_id'])
                    if not c.execute('SELECT id FROM albums WHERE id=?',(album,)).fetchone():raise ValueError('Album no longer exists.')
                    if payload.get('album_action','add') not in ('add','remove'):raise ValueError('Choose add or remove.')
                for key in ids:
                    if 'tags' in payload:c.execute('UPDATE images SET tags=? WHERE id=?',(payload['tags'].strip(),key))
                    if 'favorite' in payload:c.execute('UPDATE images SET favorite=? WHERE id=?',(int(payload['favorite']),key))
                    if album is not None:
                        if payload.get('album_action','add')=='add':c.execute('INSERT OR IGNORE INTO album_images VALUES(?,?)',(album,key))
                        else:c.execute('DELETE FROM album_images WHERE album_id=? AND image_id=?',(album,key))
                after=[self.snapshot(c,key) for key in ids]
                edit=c.execute('INSERT INTO metadata_edits(created,before_state,after_state) VALUES(?,?,?)',(time.time(),json.dumps(before),json.dumps(after))).lastrowid
                return {'ok':True,'undo_id':edit,'count':len(ids)}
            if action=='review_duplicate':
                photos=payload.get('photos')
                if not isinstance(photos,list) or len(photos)!=2:raise ValueError('Choose two photos to compare.')
                if any(not isinstance(p,dict) or not isinstance(p.get('id'),int) for p in photos):raise ValueError('Invalid photo pair.')
                photos=sorted(photos,key=lambda p:p['id'])
                if photos[0]['id']==photos[1]['id']:raise ValueError('Choose two different photos.')
                snapshots=[self.snapshot(c,p['id']) for p in photos]
                if any(p.get('revision')!=s['revision'] for p,s in zip(photos,snapshots)):raise ValueError('Photo changed. Compare the updated previews first.')
                decision=payload.get('decision')
                if decision not in ('confirmed','different','reset'):raise ValueError('Choose confirmed, different, or reset.')
                if decision=='reset':c.execute('DELETE FROM duplicate_reviews WHERE left_id=? AND right_id=?',tuple(p['id'] for p in photos))
                else:c.execute('INSERT OR REPLACE INTO duplicate_reviews VALUES(?,?,?,?,?,?)',
                    (photos[0]['id'],photos[1]['id'],snapshots[0]['revision'],snapshots[1]['revision'],decision,time.time()))
                return {'ok':True}
            if action=='undo':
                edit=c.execute('SELECT * FROM metadata_edits WHERE id=? AND undone=0',(int(payload['id']),)).fetchone()
                if edit is None:raise ValueError('This edit has already been undone or no longer exists.')
                before,after=json.loads(edit['before_state']),json.loads(edit['after_state'])
                if any(self.snapshot(c,s['id'])!=s for s in after):raise ValueError('Photos changed since this edit. Undo would overwrite newer changes.')
                for s in before:
                    if any(not c.execute('SELECT id FROM albums WHERE id=?',(key,)).fetchone() for key in s['albums']):raise ValueError('An album was deleted. This edit cannot be restored.')
                for s in before:
                    c.execute('UPDATE images SET tags=?,favorite=? WHERE id=?',(s['tags'],s['favorite'],s['id']))
                    c.execute('DELETE FROM album_images WHERE image_id=?',(s['id'],))
                    c.executemany('INSERT INTO album_images VALUES(?,?)',((key,s['id']) for key in s['albums']))
                c.execute('UPDATE metadata_edits SET undone=1 WHERE id=?',(edit['id'],))
                return {'ok':True,'count':len(before)}
            raise ValueError('Unknown gallery action')
