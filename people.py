"""Manual photo labels. No identity inference or face vectors."""
import hashlib
import time


def revision(digest, drive_id, size, mtime):
    value = str(digest)
    if drive_id and digest == drive_id:
        value += ':' + str(size) + ':' + str(mtime)
    return hashlib.sha256(value.encode()).hexdigest()


def image_revision(row):
    return revision(row['digest'], row['drive_id'], row['size'], row['mtime'])


LIVE = 'i.missing=0 AND ip.revision=content_revision(i.digest,i.drive_id,i.size,i.mtime)'


def migrate(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS project_schema(name TEXT PRIMARY KEY, version INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS people(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cover_image_id INTEGER REFERENCES images(id) ON DELETE SET NULL,
        version INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS image_people(
        image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        revision TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(image_id,person_id));
    CREATE INDEX IF NOT EXISTS people_memberships ON image_people(person_id);
    INSERT OR IGNORE INTO project_schema VALUES('people',1);
    ''')


class People:
    def __init__(self, db):
        self.db = db

    def listing(self, query='', offset=0, limit=60):
        query = str(query).strip()[:100]
        offset, limit = max(0, int(offset)), min(100, max(1, int(limit)))
        with self.db() as c:
            total = c.execute('SELECT count(*) FROM people WHERE instr(lower(name),lower(?))>0', (query,)).fetchone()[0]
            rows = c.execute('SELECT * FROM people WHERE instr(lower(name),lower(?))>0 ORDER BY name COLLATE NOCASE,id LIMIT ? OFFSET ?', (query, limit, offset)).fetchall()
            items = []
            for row in rows:
                person = dict(row)
                members = [r[0] for r in c.execute('SELECT i.id FROM images i JOIN image_people ip ON ip.image_id=i.id WHERE ip.person_id=? AND ' + LIVE + ' ORDER BY i.mtime DESC,i.id DESC', (row['id'],))]
                person['count'] = len(members)
                person['cover_image_id'] = row['cover_image_id'] if row['cover_image_id'] in members else (members[0] if members else None)
                person['label_count'] = c.execute('SELECT count(*) FROM image_people WHERE person_id=?', (row['id'],)).fetchone()[0]
                items.append(person)
            return {'items': items, 'total': total}

    def image(self, image_id):
        with self.db() as c:
            row = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(image_id),)).fetchone()
            if row is None:
                raise ValueError('Image unavailable. Refresh the gallery.')
            signature = image_revision(row)
            items = [dict(p) for p in c.execute('SELECT p.*,ip.revision<>? AS stale FROM people p JOIN image_people ip ON p.id=ip.person_id WHERE ip.image_id=? ORDER BY p.name,p.id', (signature, int(image_id)))]
            return {'items': items, 'revision': signature}

    def mutate(self, payload):
        action = payload.get('action')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if action == 'create':
                name = self.name(payload.get('name'))
                now = time.time()
                key = c.execute('INSERT INTO people(name,created,updated) VALUES(?,?,?)', (name, now, now)).lastrowid
                return {'id': key}
            person_id = int(payload['person_id'])
            person = c.execute('SELECT * FROM people WHERE id=?', (person_id,)).fetchone()
            if person is None:
                raise ValueError('Person no longer exists. Refresh the people list.')
            if action in ('assign', 'unassign'):
                items = payload.get('images')
                if not isinstance(items, list) or not 1 <= len(items) <= 100:
                    raise ValueError('Select between 1 and 100 photos.')
                for item in items:
                    if not isinstance(item, dict):
                        raise ValueError('Each photo needs an ID and current revision.')
                    row = c.execute('SELECT * FROM images WHERE id=? AND missing=0', (int(item['id']),)).fetchone()
                    if row is None or image_revision(row) != item.get('revision'):
                        raise ValueError('A selected photo changed or is unavailable. Refresh and select it again.')
                    if action == 'assign':
                        c.execute('INSERT INTO image_people VALUES(?,?,?,?) ON CONFLICT(image_id,person_id) DO UPDATE SET revision=excluded.revision', (row['id'], person_id, image_revision(row), time.time()))
                    else:
                        c.execute('DELETE FROM image_people WHERE image_id=? AND person_id=?', (row['id'], person_id))
            else:
                if int(payload.get('version', -1)) != person['version']:
                    raise ValueError('Person changed since you opened this view. Refresh and try again.')
                if action == 'rename':
                    c.execute('UPDATE people SET name=? WHERE id=?', (self.name(payload.get('name')), person_id))
                elif action == 'cover':
                    image_id = int(payload['image_id'])
                    if not c.execute('SELECT 1 FROM images i JOIN image_people ip ON ip.image_id=i.id WHERE i.id=? AND ip.person_id=? AND ' + LIVE, (image_id, person_id)).fetchone():
                        raise ValueError('Choose a currently labeled photo from this album.')
                    c.execute('UPDATE people SET cover_image_id=? WHERE id=?', (image_id, person_id))
                elif action == 'delete':
                    c.execute('DELETE FROM people WHERE id=?', (person_id,))
                elif action == 'merge':
                    target = c.execute('SELECT * FROM people WHERE id=?', (int(payload['target_id']),)).fetchone()
                    if target is None or target['id'] == person_id:
                        raise ValueError('Select a different existing person to keep.')
                    if int(payload.get('target_version', -1)) != target['version']:
                        raise ValueError('The destination person changed. Refresh and try again.')
                    # Preserve only current memberships from the source; never revive stale labels.
                    c.execute('INSERT INTO image_people SELECT ip.image_id,?,ip.revision,ip.created FROM image_people ip JOIN images i ON i.id=ip.image_id WHERE ip.person_id=? AND ' + LIVE + ' ON CONFLICT(image_id,person_id) DO UPDATE SET revision=excluded.revision', (target['id'], person_id))
                    c.execute('DELETE FROM people WHERE id=?', (person_id,))
                    c.execute('UPDATE people SET version=version+1,updated=? WHERE id=?', (time.time(), target['id']))
                else:
                    raise ValueError('Unknown people action.')
            c.execute('UPDATE people SET version=version+1,updated=? WHERE id=?', (time.time(), person_id))
            return {'ok': True}

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
            raise ValueError('Use a name between 1 and 100 characters.')
        return value.strip()
