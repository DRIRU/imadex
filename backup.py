"""Verified local backups. Restore only into a new directory, then point IMAGE_INDEX_DATA there."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from contextlib import closing


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def create(data,output,include_vectors=False):
    data,output=Path(data).resolve(),Path(output).resolve()
    if not (data/'catalog.sqlite3').is_file():raise ValueError('Catalog not found.')
    if output.exists():raise ValueError('Backup output already exists. Choose a new filename.')
    if output.is_relative_to(data):raise ValueError('Save the backup outside the active data directory.')
    vector_lock=None;locked=False
    try:
        if include_vectors:
            if os.environ.get('QDRANT_URL'):raise ValueError('External Qdrant requires its own server snapshot. This command backs up local vectors only.')
            if not (data/'vectors').is_dir():raise ValueError('Local vector store not found.')
            import portalocker
            # Acquire Qdrant's actual store lock. Refuses to copy a store held by the app.
            vector_lock=(data/'vectors/.lock').open('a+')
            try:portalocker.lock(vector_lock,portalocker.LockFlags.EXCLUSIVE|portalocker.LockFlags.NON_BLOCKING)
            except portalocker.exceptions.LockException as error:raise ValueError('Stop the app before backing up local vectors; the store is currently in use.') from error
            locked=True
        with tempfile.TemporaryDirectory(prefix='imadex-backup-') as temporary:
            staging=Path(temporary)
            with closing(sqlite3.connect(data/'catalog.sqlite3')) as source,closing(sqlite3.connect(staging/'catalog.sqlite3')) as target:source.backup(target)
            if locked:shutil.copytree(data/'vectors',staging/'vectors',ignore=shutil.ignore_patterns('.lock'))
            files={str(p.relative_to(staging).as_posix()):digest(p) for p in staging.rglob('*') if p.is_file()}
            manifest={'version':1,'created':time.time(),'files':files,'vectors_included':locked,
                'excluded':['OAuth credentials/tokens','model weights','regenerable thumbnails'],
                'restore_note':'Reconnect Google Drive. Without vector data, queue indexing to rebuild the selected model collection.'}
            (staging/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
            output.parent.mkdir(parents=True,exist_ok=True)
            with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED) as archive:
                for path in staging.rglob('*'):
                    if path.is_file():archive.write(path,path.relative_to(staging).as_posix())
        return manifest
    finally:
        if vector_lock:
            if locked:portalocker.unlock(vector_lock)
            vector_lock.close()


def restore(archive_path,target):
    target=Path(target).resolve()
    if target.exists():raise ValueError('Restore target must be a new directory. The active catalog is never overwritten.')
    with zipfile.ZipFile(archive_path) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('version')!=1:raise ValueError('Unsupported backup version.')
        expected={'manifest.json',*manifest['files']}
        entries=archive.namelist()
        if len(entries)!=len(set(entries)) or set(entries)!=expected:raise ValueError('Unexpected backup contents.')
        for name in entries:
            parts=PurePosixPath(name)
            if parts.is_absolute() or '\\' in name or ':' in name or '..' in parts.parts or not (target/name).resolve().is_relative_to(target):raise ValueError('Unsafe backup entry.')
        with tempfile.TemporaryDirectory(prefix='imadex-restore-') as temporary:
            staging=Path(temporary)
            for name,checksum in manifest['files'].items():
                path=staging/name;path.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(name) as source,path.open('wb') as destination:shutil.copyfileobj(source,destination)
                if digest(path)!=checksum:raise ValueError('Backup checksum mismatch: '+name)
            if not (staging/'catalog.sqlite3').is_file():raise ValueError('Backup has no catalog.')
            with closing(sqlite3.connect(staging/'catalog.sqlite3')) as c:
                if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Catalog integrity check failed.')
            # Verify everything before creating the user-specified target.
            shutil.copytree(staging,target)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    save=sub.add_parser('create');save.add_argument('--data',default=os.environ.get('IMAGE_INDEX_DATA','data'));save.add_argument('--output',required=True);save.add_argument('--include-local-vectors',action='store_true')
    load=sub.add_parser('restore');load.add_argument('--archive',required=True);load.add_argument('--target',required=True)
    args=parser.parse_args()
    result=create(args.data,args.output,args.include_local_vectors) if args.action=='create' else restore(args.archive,args.target)
    print(json.dumps({'ok':True,'vectors_included':result['vectors_included'],'note':result['restore_note']},indent=2))
