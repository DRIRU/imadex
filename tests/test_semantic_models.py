import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import semantic


class ModelSelectionTests(unittest.TestCase):
    def test_siglip2_is_the_default(self):
        self.assertEqual(semantic.DEFAULT_MODEL, 'siglip2')
        self.assertEqual(semantic.MODELS['siglip2']['dimensions'], 768)
        self.assertEqual(semantic.DIMENSIONS, 768)
        self.assertEqual(semantic.COLLECTION, 'images_siglip2_base_v1')

    def test_selection_returns_matching_configuration(self):
        clip = semantic.select('clip')
        self.assertEqual((clip['dimensions'], clip['collection']), (512, 'images_clip_b32_v1'))
        siglip = semantic.select('SIGLIP2 ')
        self.assertEqual(siglip['dimensions'], 768)
        self.assertEqual(siglip['vision'], siglip['text'])
        self.assertEqual(semantic.select(None)['collection'], semantic.MODELS[semantic.DEFAULT_MODEL]['collection'])

    def test_unknown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            semantic.select('resnet')

    def test_active_constants_come_from_the_selection(self):
        active = semantic.MODELS[semantic.DEFAULT_MODEL]
        self.assertEqual(semantic.VISION_MODEL, active['vision'])
        self.assertEqual(semantic.TEXT_MODEL, active['text'])
        self.assertEqual(semantic.MODEL_VERSION, active['version'])
        self.assertEqual(semantic.COLLECTION, active['collection'])
        self.assertEqual(semantic.DIMENSIONS, active['dimensions'])


if __name__ == '__main__':
    unittest.main()
