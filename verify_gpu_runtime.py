"""Verify real CUDA embedding sessions in an isolated environment; preserves the app's .venv."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parent


def main(prepare=False):
    environment=BASE/'data/gpu-verification-env'
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if prepare:
        if not python.exists():subprocess.run([sys.executable,'-m','venv',str(environment)],check=True)
        subprocess.run([str(python),'-m','pip','install','-r',str(BASE/'requirements-gpu.txt')],check=True,cwd=BASE)
        subprocess.run([str(python),'-m','pip','check'],check=True,cwd=BASE)
    if not python.exists():raise ValueError('Prepare the isolated runtime first with --prepare.')
    probe="import importlib.metadata as m,onnxruntime as ort; assert 'CUDAExecutionProvider' in ort.get_available_providers(), 'GPU runtime unavailable'; assert m.version('fastembed-gpu')=='0.8.1'; assert m.version('onnxruntime-gpu')=='1.30.0'; print('Isolated GPU dependencies verified',flush=True)"
    subprocess.run([str(python),'-X','utf8','-c',probe],check=True,cwd=BASE)
    for model in ('siglip2','clip'):
        env=dict(os.environ,IMAGE_INDEX_PROVIDER='cuda',IMAGE_INDEX_MODEL=model,QDRANT_URL='',QDRANT_COLLECTION='',QDRANT_API_KEY='')
        subprocess.run([str(python),'-X','utf8',str(BASE/'verify_embeddings.py')],env=env,check=True,cwd=BASE)
    print('PASS: both encoders loaded real CUDA sessions and passed ranking/resume in isolated temporary catalogs.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare',action='store_true',help='Install pinned GPU dependencies into data/gpu-verification-env first.')
    args=parser.parse_args();main(args.prepare)
