"""ONNX Runtime execution-provider selection with automatic CPU fallback.

Controlled by the IMAGE_INDEX_PROVIDER environment variable:

- ``auto`` (default): use CUDA when the installed onnxruntime offers it, else CPU.
- ``cpu``: always CPU.
- ``cuda`` / ``gpu``: require CUDA; raise if the CPU-only onnxruntime is installed.

This affects image/text and ArcFace inference. Qdrant local mode and the pip OpenCV
build remain CPU-only.
"""
import os
import threading

CPU = 'CPUExecutionProvider'
CUDA = 'CUDAExecutionProvider'
_preload_lock = threading.Lock()
_preloaded = False


def preload_runtime(runtime):
    """Load pip-provided CUDA/cuDNN libraries once, before creating GPU sessions."""
    global _preloaded
    with _preload_lock:
        if not _preloaded and callable(getattr(runtime, 'preload_dlls', None)):
            runtime.preload_dlls()
            _preloaded = True


def mode():
    value = os.environ.get('IMAGE_INDEX_PROVIDER', 'auto').strip().lower() or 'auto'
    if value not in ('auto', 'cpu', 'none', 'cuda', 'gpu'):
        raise ValueError('IMAGE_INDEX_PROVIDER must be auto, cpu, or cuda.')
    return value


def providers():
    choice = mode()
    if choice in ('cpu', 'none'):
        return [CPU]
    import onnxruntime
    available = onnxruntime.get_available_providers()
    if choice in ('auto', 'gpu', 'cuda') and CUDA in available:
        preload_runtime(onnxruntime)
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


def session_providers(model):
    """Inspect a loaded ONNX session through FastEmbed's model wrappers."""
    seen = set()
    for _ in range(6):
        if model is None or id(model) in seen:
            return []
        seen.add(id(model))
        if callable(getattr(model, 'get_providers', None)):
            return list(model.get_providers())
        model = getattr(model, 'model', None)
    return []


def require_requested_provider(model):
    actual = session_providers(model)
    if mode() in ('cuda', 'gpu') and CUDA not in actual:
        raise ValueError('CUDA was requested but the loaded session fell back to CPU. Check CUDA/cuDNN and restart, or select cpu.')
    return actual
