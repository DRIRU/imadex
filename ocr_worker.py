"""Isolated CPU OCR process. Image bytes arrive on stdin and are never saved."""
import hashlib
import json
import sys
from pathlib import Path

MODEL = 'rapidocr-1.4.4-ppocr4-cpu-1600-v1'
MODEL_HASHES = {
    'ch_PP-OCRv4_det_infer.onnx': 'd2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9',
    'ch_PP-OCRv4_rec_infer.onnx': '48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b',
    'ch_ppocr_mobile_v2.0_cls_infer.onnx': 'e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c',
}


def main():
    import importlib.metadata as metadata
    import rapidocr_onnxruntime as package
    from rapidocr_onnxruntime import RapidOCR
    assert metadata.version('rapidocr-onnxruntime') == '1.4.4', 'Unexpected OCR package version'
    models = Path(package.__file__).parent / 'models'
    for name, digest in MODEL_HASHES.items():
        if hashlib.sha256((models / name).read_bytes()).hexdigest() != digest:
            raise ValueError('OCR model checksum failed; reinstall the isolated OCR runtime.')
    if '--probe' in sys.argv:
        print(json.dumps({'model': MODEL, 'verified': True}));return
    raw = sys.stdin.buffer.read(12 * 1024 * 1024 + 1)
    if len(raw) > 12 * 1024 * 1024:raise ValueError('OCR image exceeds input limit')
    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1,
                      det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False)
    for session in (engine.text_det.infer.session, engine.text_cls.infer.session, engine.text_rec.session.session):
        if session.get_providers() != ['CPUExecutionProvider']:
            raise ValueError('OCR worker must use the isolated CPU runtime')
    results, _ = engine(raw)
    text = '\n'.join(str(row[1]) for row in (results or []) if float(row[2]) >= 0.5)[:32000]
    print(json.dumps({'text': text, 'model': MODEL}, ensure_ascii=False))


if __name__ == '__main__':main()
