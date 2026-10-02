"""Install optional OCR in data/ocr-env without changing the embedding runtime."""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def main():
    data = Path(os.environ.get('IMAGE_INDEX_DATA', BASE / 'data'))
    environment = data / 'ocr-env'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(BASE / 'requirements-ocr.txt')], check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    subprocess.run([str(python), '-X', 'utf8', str(BASE / 'ocr_worker.py'), '--probe'], check=True)
    print('OCR runtime verified. Enable text extraction in Setup when ready.')


if __name__ == '__main__':main()
