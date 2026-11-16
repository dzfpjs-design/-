"""Exact masks/probabilities, unknown pixels and georeferencing; no app data."""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/generic_agents'))
import local_changer_geo_exporter as exporter

GEO = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'rasterio', 'pyproj'))


@unittest.skipUnless(GEO, 'requires the isolated geospatial environment')
class ExportContract(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import rasterio
        from PIL import Image
        self.np, self.rasterio, self.Image = np, rasterio, Image
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.aligned, self.learned, self.output = [self.root / name for name in ('aligned', 'learned', 'out')]
        for path in (self.aligned, self.learned, self.output):
            path.mkdir()
        self.probability = np.full((48, 64), .2, dtype='float32')
        self.probability[:, 32:] = .8
        self.probability[:2] = np.nan
        self.valid = np.isfinite(self.probability)
        np.save(self.learned / 'probability.npy', self.probability, allow_pickle=False)
        for name in ('before', 'after'):
            Image.new('RGB', (64, 48), (120, 130, 140)).save(self.aligned / (name + '.png'))
        Image.fromarray(self.valid.astype('uint8') * 255).save(self.aligned / 'valid.png')
        candidate = self.valid & (self.probability >= .5)
        overlay = np.zeros((48, 64, 4), dtype='uint8')
        overlay[candidate] = [240, 100, 39, 190]
        Image.fromarray(overlay).save(self.learned / 'candidate.png')
        quantized = np.zeros((48, 64), dtype='uint16')
        quantized[self.valid] = np.rint(self.probability[self.valid] * 65534 + 1).astype('uint16')
        Image.fromarray(quantized).save(self.learned / 'probability.png')
        self.request = {'width': 64, 'height': 48, 'total_pixels': 48 * 64,
                        'common_valid_pixels': 46 * 64, 'unknown_pixels': 2 * 64,
                        'candidate_pixels': 46 * 32, 'probability_threshold': .5,
                        'transform': [.5, .1, 500000, .05, -.5, 2680000],
                        'crs_wkt': rasterio.crs.CRS.from_epsg(32650).to_wkt(),
                        'weights_sha256': 'test_only_not_a_trained_quality_result',
                        'aligned_input_sha256': {name: hashlib.sha256((self.aligned / (name + '.png')).read_bytes()).hexdigest()
                                                  for name in ('before', 'after', 'valid')}}
        self.recipe = json.loads((ROOT / 'runtime-packs/geo-rgb-v1.json').read_text())

    def run_export(self, request=None):
        return exporter.export(self.aligned, self.learned, request or self.request, self.output, self.recipe)

    def test_nonzero_candidate_and_unknown_masks_keep_exact_values_and_rotated_affine(self):
        result = self.run_export()
        self.assertTrue(result['exact_probability_readback'])
        with self.rasterio.open(self.output / 'probability.tif') as src:
            self.assertTrue(self.np.array_equal(src.read(1), self.probability, equal_nan=True))
            self.assertEqual(list(src.transform)[:6], self.request['transform'])
            self.assertEqual(src.crs.to_epsg(), 32650)
        with self.rasterio.open(self.output / 'change-mask.tif') as src:
            labels = src.read(1)
            self.assertTrue(bool((labels[:2] == 255).all()))
            self.assertTrue(bool((labels[2:, :32] == 0).all()))
            self.assertTrue(bool((labels[2:, 32:] == 1).all()))
            self.assertEqual(src.nodata, 255)
            self.assertEqual(list(src.transform)[:6], self.request['transform'])
            self.assertEqual(src.crs.to_epsg(), 32650)

    def test_unknown_probability_cannot_be_saved_as_no_change(self):
        changed = self.probability.copy(); changed[0, 0] = 0
        self.np.save(self.learned / 'probability.npy', changed, allow_pickle=False)
        with self.assertRaisesRegex(ValueError, 'unknown pixels'):
            self.run_export()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_saved_mask_or_probability_image_tampering_is_rejected(self):
        self.Image.new('RGBA', (64, 48), (0, 0, 0, 0)).save(self.learned / 'candidate.png')
        with self.assertRaisesRegex(ValueError, 'do not match the exact'):
            self.run_export()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_result_counts_and_original_alignment_hash_are_checked(self):
        for changed in ({**self.request, 'candidate_pixels': 0},
                        {**self.request, 'aligned_input_sha256': {**self.request['aligned_input_sha256'], 'before': 'changed'}}):
            with self.subTest(request=changed), self.assertRaises(ValueError):
                self.run_export(changed)
            self.assertEqual(list(self.output.iterdir()), [])

    def test_old_saved_result_is_never_overwritten(self):
        path = self.output / 'probability.tif'; path.write_bytes(b'old saved result')
        with self.assertRaisesRegex(ValueError, 'must not overwrite'):
            self.run_export()
        self.assertEqual(path.read_bytes(), b'old saved result')

    def test_object_array_is_rejected_without_executing_pickle(self):
        sentinel = self.root / 'executed'
        class Payload:
            def __reduce__(self):
                return (eval, (f"__import__('pathlib').Path({str(sentinel)!r}).touch()",))
        array = self.np.empty((1, 1), dtype=object); array[0, 0] = Payload()
        self.np.save(self.learned / 'probability.npy', array, allow_pickle=True)
        with self.assertRaises(ValueError):
            self.run_export()
        self.assertFalse(sentinel.exists())
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
