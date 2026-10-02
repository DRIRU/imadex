"""Real OCR + catalog search smoke test using only generated temporary fixtures."""
import tempfile
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import app
import ocr
from semantic import SemanticIndex


def main():
    original = app.DATA
    with tempfile.TemporaryDirectory(prefix='imadex-ocr-verification-') as tmp:
        app.DATA = Path(tmp)/'catalog';app.initialize()
        pictures = Path(tmp)/'pictures';pictures.mkdir()
        font_path = Path('C:/Windows/Fonts/arial.ttf')
        font = ImageFont.truetype(str(font_path),48) if font_path.exists() else ImageFont.truetype('DejaVuSans.ttf',48)
        image = Image.new('RGB',(1000,240),'white');draw = ImageDraw.Draw(image)
        draw.text((30,40),'INVOICE ACME 2026',font=font,fill='black')
        draw.text((30,130),'Payment received',font=font,fill='black');image.save(pictures/'generic.png')
        with app.db() as c:folder = c.execute('INSERT INTO folders(path) VALUES(?)',(str(pictures),)).lastrowid
        app.scan_lock.acquire();app.scan(folder)
        # load_image does not load embedding models or connect to Qdrant.
        loader = SemanticIndex.__new__(SemanticIndex);loader.data = app.DATA
        service = ocr.OCR(original,app.db,loader.load_image)
        with app.db() as c:row = dict(c.execute('SELECT * FROM images').fetchone())
        try:
            started = time.monotonic()
            assert service.process(row,0), service.status(row['id'])['image']
            result = service.status(row['id'])['image'];print('Text:',result['text'])
            assert 'ACME' in result['text'].upper() and '2026' in result['text']
            clause,values = ocr.text_filter('ACME')
            with app.db() as c:assert c.execute('SELECT count(*) FROM images WHERE '+clause,values).fetchone()[0] == 1
            print('PASS: real CPU OCR, persisted text and catalog search; %.2f seconds including process/model startup.'%(time.monotonic()-started))
        finally:service.close();app.DATA = original


if __name__ == '__main__':main()
