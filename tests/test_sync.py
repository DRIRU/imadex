import io
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import drive
from sync import Synchronizer, Cancelled
from thumbnails import ThumbnailCache


def node(key,parent=None,folder=False,**extra):
    return dict(id=key,name=key,mimeType='application/vnd.google-apps.folder' if folder else 'image/jpeg',
                parents=[parent] if parent else [],modifiedTime='2026-10-02T00:00:00Z',size='100',md5Checksum=key,**extra)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=app.DATA;app.DATA=Path(self.tmp.name);app.initialize()
        with app.db() as c:
            self.folder=c.execute("INSERT INTO folders(path) VALUES('gdrive:root:Photos')").lastrowid
            c.execute('INSERT INTO sync_sources(folder_id) VALUES(?)',(self.folder,))
            c.execute('UPDATE sync_settings SET enabled=1')
        self.worker=Synchronizer(app.DATA,app.db,threading.Lock())

    def tearDown(self):
        app.DATA=self.old;self.tmp.cleanup()

    def bootstrap(self):
        with patch('drive.start_cursor',return_value='before'),patch('drive.walk_nodes',return_value=[node('root',folder=True),node('sub','root',True),node('photo','sub')]),patch('drive.changes_page',return_value={'newStartPageToken':'after'}):
            self.worker.sync_source(self.folder,'root')

    def checkpoint(self):
        with app.db() as c:return c.execute('SELECT cursor FROM sync_sources').fetchone()[0]

    def images(self):
        with app.db() as c:return [dict(r) for r in c.execute('SELECT * FROM images')]

    def test_bootstrap_captures_before_listing_and_consumes_racing_change(self):
        events=[]
        with patch('drive.start_cursor',side_effect=lambda _:events.append('cursor') or 'before'),patch('drive.walk_nodes',side_effect=lambda *_:events.append('listing') or [node('root',folder=True)]),patch('drive.changes_page',return_value={'changes':[{'fileId':'photo','file':node('photo','root')}],'newStartPageToken':'after'}):
            self.worker.sync_source(self.folder,'root')
        self.assertEqual(events,['cursor','listing']);self.assertEqual(self.checkpoint(),'after');self.assertEqual(len(self.images()),1)

    def test_move_folder_out_marks_descendants_missing_and_move_back_recovers(self):
        self.bootstrap()
        with patch('drive.changes_page',return_value={'changes':[{'fileId':'sub','file':node('sub','outside',True)}],'newStartPageToken':'moved'}):
            self.worker.sync_source(self.folder,'root')
        self.assertEqual(self.images()[0]['missing'],1)
        with patch('drive.changes_page',return_value={'changes':[{'fileId':'sub','file':node('sub','root',True)}],'newStartPageToken':'back'}),patch('drive.walk_nodes',return_value=[node('sub','root',True),node('photo','sub')]):
            self.worker.sync_source(self.folder,'root')
        self.assertEqual(self.images()[0]['missing'],0)

    def test_page_failure_keeps_successful_checkpoint_and_catalog(self):
        self.bootstrap()
        pages=[{'changes':[{'fileId':'photo','file':dict(node('photo','sub'),name='renamed')}],'nextPageToken':'page2'},drive.DriveError(403)]
        with patch('drive.changes_page',side_effect=pages):
            with self.assertRaises(drive.DriveError):self.worker.sync_source(self.folder,'root')
        self.assertEqual(self.checkpoint(),'page2');self.assertEqual(self.images()[0]['name'],'renamed');self.assertEqual(self.images()[0]['missing'],0)

    def test_pause_during_response_does_not_publish(self):
        self.bootstrap()
        def pause(*_):
            self.worker.action({'action':'settings','enabled':False})
            return {'changes':[{'fileId':'photo','removed':True}],'newStartPageToken':'paused'}
        with patch('drive.changes_page',side_effect=pause):
            with self.assertRaises(Cancelled):self.worker.sync_source(self.folder,'root')
        self.assertEqual(self.checkpoint(),'after');self.assertEqual(self.images()[0]['missing'],0)

    def test_unrelated_ancestor_and_drive_level_change_excluded(self):
        self.bootstrap()
        page={'changes':[{'changeType':'drive'},{'fileId':'other','file':node('other','external')}],'newStartPageToken':'new'}
        with patch('drive.changes_page',return_value=page),patch('drive.metadata',return_value=node('external',folder=True)):
            self.worker.sync_source(self.folder,'root')
        self.assertEqual(len(self.images()),1);self.assertEqual(self.checkpoint(),'new')

    def test_removed_root_preserves_library(self):
        self.bootstrap()
        with patch('drive.changes_page',return_value={'changes':[{'fileId':'root','removed':True}],'newStartPageToken':'gone'}):
            with self.assertRaises(ValueError):self.worker.sync_source(self.folder,'root')
        self.assertEqual(self.checkpoint(),'after');self.assertEqual(self.images()[0]['missing'],0)

    def test_expired_token_rebuilds_once_and_repeated_rejection_is_bounded(self):
        self.bootstrap()
        with patch('drive.changes_page',side_effect=[drive.DriveError(410),{'newStartPageToken':'fresh'}]),patch('drive.start_cursor',return_value='reset'),patch('drive.walk_nodes',return_value=[node('root',folder=True),node('photo','root')]):
            self.worker.sync_source(self.folder,'root')
        self.assertEqual(self.checkpoint(),'fresh');self.assertEqual(self.images()[0]['missing'],0)
        with patch('drive.changes_page',side_effect=drive.DriveError(410)) as pages,patch('drive.start_cursor',return_value='reset'),patch('drive.walk_nodes',return_value=[node('root',folder=True),node('photo','root')]):
            with self.assertRaisesRegex(ValueError,'repeatedly'):self.worker.sync_source(self.folder,'root')
        self.assertEqual(pages.call_count,2)

    def test_overlapping_roots_preserve_checkpoint(self):
        self.bootstrap()
        with app.db() as c:c.execute("INSERT INTO folders(path) VALUES('gdrive:sub:Nested')")
        with patch('drive.changes_page',return_value={'newStartPageToken':'overlap'}):
            with self.assertRaisesRegex(ValueError,'overlap'):self.worker.sync_source(self.folder,'root')
        self.assertEqual(self.checkpoint(),'after');self.assertEqual(self.images()[0]['missing'],0)

    def test_failure_is_recorded_with_backoff_and_manual_retry(self):
        with patch('drive.state',return_value={'connected':True}),patch.object(self.worker,'sync_source',side_effect=ValueError('Reconnect Drive')):
            self.worker.run_once()
        status=self.worker.status();self.assertEqual(status['jobs'][0]['status'],'failed');self.assertEqual(status['sources'][0]['failures'],1)
        self.assertGreater(status['sources'][0]['next_run'],status['jobs'][0]['finished'])
        self.worker.action({'action':'retry'});self.assertEqual(self.worker.status()['sources'][0]['next_run'],0)

    def test_thumbnail_revision_budget_and_disabled_cache(self):
        self.bootstrap();cache=ThumbnailCache(app.DATA,app.db);row=self.images()[0]
        output=io.BytesIO();Image.new('RGB',(900,900),'red').save(output,format='PNG');raw=output.getvalue()
        with patch('drive.thumbnail',side_effect=lambda *_:io.BytesIO(raw)) as fetch:
            first=cache.get(row);self.assertEqual(cache.get(row),first);self.assertEqual(fetch.call_count,1)
            changed=dict(row,digest='changed');cache.get(changed);self.assertEqual(fetch.call_count,2)
            self.assertEqual(len(list(cache.path.glob('*.jpg'))),1)
            cache.action({'action':'budget','budget':0});self.assertEqual(cache.status()['used'],0)
            cache.get(changed);cache.get(changed);self.assertEqual(fetch.call_count,4);self.assertEqual(list(cache.path.glob('*.jpg')),[])
        with Image.open(io.BytesIO(first)) as image:self.assertEqual(image.size,(640,640))

    def test_thumbnail_restart_removes_only_owned_orphans(self):
        self.bootstrap();cache=ThumbnailCache(app.DATA,app.db)
        (cache.path/('a'*64+'.jpg')).write_bytes(b'orphan')
        (cache.path/('b'*64+'.tmp')).write_bytes(b'interrupted')
        (cache.path/'unrelated.txt').write_text('keep')
        ThumbnailCache(app.DATA,app.db)
        self.assertEqual([p.name for p in cache.path.iterdir()],['unrelated.txt'])


if __name__=='__main__':unittest.main()
