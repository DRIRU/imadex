"""ONNX Runtime execution-provider selection with automatic CPU fallback.

Controlled by the IMAGE_INDEX_PROVIDER environment variable:

- ``auto`` (default): use CUDA when the installed onnxruntime offers it, else CPU.
- ``cpu``: always CPU.
- ``cuda`` / ``gpu``: require CUDA; raise if the CPU-only onnxruntime is installed.

This only affects CLIP/ArcFace inference. Qdrant local mode and the pip OpenCV
build remain CPU-only.
"""
import os

CPU = 'CPUExecutionProvider'
CUDA = 'CUDAExecutionProvider'


def mode():
    return (os.environ.get('IMAGE_INDEX_PROVIDER', 'auto').strip().lower() or 'auto')


def providers():
    choice = mode()
    if choice in ('cpu', 'none'):
        return [CPU]
    import onnxruntime
    available = onnxruntime.get_available_providers()
    if choice in ('auto', 'gpu', 'cuda') and CUDA in available:
        return [CUDA, CPU]
    if choice in ('gpu', 'cuda'):
        raise ValueError('IMAGE_INDEX_PROVIDER=' + choice + ' was requested, but CUDAExecutionProvider is unavailable. '
            'Install onnxruntime-gpu with a matching CUDA/cuDNN runtime, or set IMAGE_INDEX_PROVIDER=cpu.')
    return [CPU]


def active():
    try:
        return providers()
    except ValueError:
        return [CPU]


def label():
    return ','.join(active())
