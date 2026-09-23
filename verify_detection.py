"""Real YuNet smoke test. Downloads a public-domain NASA fixture, never catalogs it."""
import io
from pathlib import Path
from urllib.request import urlopen
from PIL import Image, ImageOps
from detection import Detector


def main():
    # Source/license: https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.astronaut
    url = 'https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png'
    with urlopen(url, timeout=30) as response:
        raw = response.read(2 * 1024 * 1024)
    detector = Detector(Path(__file__).resolve().parent / 'data', None, None)
    with Image.open(io.BytesIO(raw)) as original:
        portrait = original.convert('RGB')
    assert len(detector.detect(portrait)) >= 1
    pair = Image.new('RGB', (1024, 512))
    pair.paste(portrait, (0, 0)); pair.paste(portrait, (512, 0))
    regions = detector.detect(pair)
    assert len(regions) >= 2
    for r in regions:
        assert 0 <= r['x'] < r['x'] + r['width'] <= 1
        assert 0 <= r['y'] < r['y'] + r['height'] <= 1
    # Emulate a camera file whose pixels require EXIF rotation before detection.
    rotated = portrait.transpose(Image.Transpose.ROTATE_90)
    exif = rotated.getexif(); exif[274] = 6
    encoded = io.BytesIO(); rotated.save(encoded, format='JPEG', exif=exif)
    encoded.seek(0)
    with Image.open(encoded) as image:
        assert len(detector.detect(ImageOps.exif_transpose(image).convert('RGB'))) >= 1
    assert detector.detect(Image.new('RGB', (512, 512), 'blue')) == []
    print('PASS actual YuNet: portrait, multiple regions, normalized geometry, EXIF rotation, blank image.')


if __name__ == '__main__':
    main()
