import os
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accel

CPU = 'CPUExecutionProvider'
CUDA = 'CUDAExecutionProvider'


class AccelTests(unittest.TestCase):
    def test_cpu_mode_never_uses_cuda(self):
        with patch.dict(os.environ, {'IMAGE_INDEX_PROVIDER': 'cpu'}):
            self.assertEqual(accel.providers(), [CPU])
            self.assertEqual(accel.label(), CPU)

    def test_auto_falls_back_to_cpu_without_cuda(self):
        with patch.dict(os.environ, {'IMAGE_INDEX_PROVIDER': 'auto'}), \
                patch('onnxruntime.get_available_providers', return_value=[CPU]):
            self.assertEqual(accel.providers(), [CPU])

    def test_auto_prefers_cuda_with_cpu_fallback(self):
        with patch.dict(os.environ, {'IMAGE_INDEX_PROVIDER': 'auto'}), \
                patch('onnxruntime.get_available_providers', return_value=[CUDA, CPU]), \
                patch('onnxruntime.preload_dlls', create=True) as preload, patch.object(accel,'_preloaded',False):
            self.assertEqual(accel.providers(), [CUDA, CPU])
            self.assertEqual(accel.label(), CUDA + ',' + CPU)
            preload.assert_called_once()

    def test_explicit_cuda_requires_the_gpu_build(self):
        with patch.dict(os.environ, {'IMAGE_INDEX_PROVIDER': 'cuda'}), \
                patch('onnxruntime.get_available_providers', return_value=[CPU]):
            with self.assertRaises(ValueError):
                accel.providers()
            self.assertEqual(accel.active(), [CPU])

    def test_loaded_session_reports_cpu_and_explicit_gpu_rejects_fallback(self):
        session=SimpleNamespace(get_providers=lambda:[CPU])
        wrapper=SimpleNamespace(model=SimpleNamespace(model=session))
        self.assertEqual(accel.session_providers(wrapper),[CPU])
        self.assertEqual(accel.session_providers(None),[])
        with patch.dict(os.environ,{'IMAGE_INDEX_PROVIDER':'cuda'}):
            with self.assertRaises(ValueError):accel.require_requested_provider(wrapper)
        with patch.dict(os.environ,{'IMAGE_INDEX_PROVIDER':'auto'}):accel.require_requested_provider(wrapper)


if __name__ == '__main__':
    unittest.main()
