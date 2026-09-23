import os
import sys
import unittest
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
                patch('onnxruntime.get_available_providers', return_value=[CUDA, CPU]):
            self.assertEqual(accel.providers(), [CUDA, CPU])
            self.assertEqual(accel.label(), CUDA + ',' + CPU)

    def test_explicit_cuda_requires_the_gpu_build(self):
        with patch.dict(os.environ, {'IMAGE_INDEX_PROVIDER': 'cuda'}), \
                patch('onnxruntime.get_available_providers', return_value=[CPU]):
            with self.assertRaises(ValueError):
                accel.providers()
            self.assertEqual(accel.active(), [CPU])


if __name__ == '__main__':
    unittest.main()
