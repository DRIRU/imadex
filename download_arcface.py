"""Download the ArcFace (InsightFace w600k_r50) model used by face recognition.

The InsightFace pretrained weights are licensed for non-commercial research use
only. Running this script downloads them for local, personal use.

The archive download is resumable: an interrupted run continues from the saved
partial file in `data/models/`.
"""
import argparse
import hashlib
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import app
from recognition import MODEL_FILE

URL = 'https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip'
SHA256 = '4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43'


def download(url, destination):
    resume = destination.stat().st_size if destination.exists() else 0
    request = Request(url, headers={'Range': 'bytes=%d-' % resume} if resume else {})
    with urlopen(request, timeout=60) as response:
        if resume and response.status == 206:
            mode = 'ab'
        else:
            mode, resume = 'wb', 0
        total = resume + int(response.headers.get('Content-Length', 0))
        received, start, last = resume, time.time(), time.time()
        with destination.open(mode) as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
                received += len(chunk)
                if time.time() - last >= 2:
                    last = time.time()
                    rate = (received - resume) / 1048576 / max(0.1, time.time() - start)
                    portion = '%5.1f%%' % (100 * received / total) if total else '  ?  '
                    print('%s  %6.1f/%.1f MB  %.2f MB/s' % (portion, received / 1048576, total / 1048576, rate), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true', help='Replace an existing model file')
    parser.add_argument('--url', default=URL, help='Override the download URL (for a mirror)')
    args = parser.parse_args()
    target = app.DATA / 'models' / MODEL_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.force:
        print('Model already present:', target)
        return
    archive = app.DATA / 'models' / 'buffalo_l.zip'
    print('Note: InsightFace ArcFace pretrained weights are for non-commercial research use only.')
    if archive.exists():
        print('Resuming partial download:', archive)
    print('Downloading', args.url, flush=True)
    try:
        download(args.url, archive)
        print('Downloaded', archive)
        with zipfile.ZipFile(archive) as zipped:
            member = next((name for name in zipped.namelist() if name.endswith(MODEL_FILE)), None)
            if member is None:
                sys.exit(MODEL_FILE + ' was not found in the downloaded archive.')
            with zipped.open(member) as source, target.open('wb') as destination:
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
        archive.unlink()
    except KeyboardInterrupt:
        print('\nInterrupted. Re-run to resume from', archive, flush=True)
        return
    print('Saved', target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if args.url == URL and digest != SHA256:
        target.unlink(missing_ok=True)
        sys.exit('Checksum mismatch. Delete the partial archive and retry, or use --url with a trusted mirror.')
    print('SHA-256', digest)
    try:
        import onnxruntime
        session = onnxruntime.InferenceSession(str(target), providers=['CPUExecutionProvider'])
        print('Verified: input', session.get_inputs()[0].shape, 'output', session.get_outputs()[0].shape)
    except Exception as error:
        sys.exit('The downloaded model could not be loaded: ' + str(error))


if __name__ == '__main__':
    main()
