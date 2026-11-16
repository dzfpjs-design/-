"""Full-coverage tiling and unknown-region contracts, without database access."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/generic_agents'))
import local_changer_pair_runner as runner

NUMPY = importlib.util.find_spec('numpy') is not None


class TileStarts(unittest.TestCase):
    def test_small_region_has_one_origin_and_large_regions_include_last_pixel(self):
        self.assertEqual(runner.starts(32, 512, 448), [0])
        for length in (513, 530, 960, 961, 4096):
            windows = runner.starts(length, 512, 448)
            self.assertEqual(windows[0], 0)
            self.assertEqual(windows[-1] + 512, length)
            self.assertTrue(all(b <= a + 512 for a, b in zip(windows, windows[1:])))


@unittest.skipUnless(NUMPY, 'requires isolated native NumPy')
class TiledContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.np = np
        cls.recipe = json.loads((ROOT / 'runtime-packs/geo-changer-candidate-v1.json').read_text())

    def inputs(self, height=530, width=540):
        before = self.np.zeros((height, width, 3), dtype='uint8')
        before[:, :, 0] = self.np.arange(width, dtype='uint32') % 256
        after = before.copy()
        valid = self.np.ones((height, width), dtype=bool)
        return before, after, valid

    def test_overlapping_tiles_cover_original_dimensions_without_resizing_or_input_mutation(self):
        before, after, valid = self.inputs()
        original = before.copy()
        seen = []
        def infer(model, a, b):
            seen.append(a.shape)
            self.assertTrue(self.np.array_equal(a, b))
            return a[:, :, 0].astype('float32') / 255
        result, count = runner.tiled_probabilities(None, before, after, valid, self.recipe, infer)
        self.assertEqual(count, 4)
        self.assertEqual(seen, [(512, 512, 3)] * 4)
        self.np.testing.assert_allclose(result, before[:, :, 0].astype('float32') / 255, rtol=1e-6)
        self.assertTrue(self.np.array_equal(before, original))

    def test_unknown_pixels_stay_nan_while_every_valid_pixel_is_processed(self):
        before, after, valid = self.inputs()
        valid[:100, :100] = False
        result, _ = runner.tiled_probabilities(None, before, after, valid, self.recipe,
            lambda model, a, b: self.np.full(a.shape[:2], .75, dtype='float32'))
        self.assertTrue(bool(self.np.isnan(result[~valid]).all()))
        self.np.testing.assert_array_equal(result[valid], .75)

    def test_empty_coverage_wrong_shape_dtype_or_excess_budget_does_not_run_model(self):
        before, after, valid = self.inputs(64, 96)
        calls = []
        def infer(*args):
            calls.append(True)
            raise AssertionError('must reject before inference')
        cases = [(before, after, valid & False, self.recipe),
                 (before, after[:, :-1], valid, self.recipe),
                 (before.astype('float32'), after, valid, self.recipe),
                 (before, after, valid.astype('uint8'), self.recipe),
                 (before, after, valid, {**self.recipe, 'max_tiles': 0}),
                 (before, after, valid, {**self.recipe, 'max_comparison_pixels': 100})]
        for a, b, mask, recipe in cases:
            with self.subTest(shape=b.shape), self.assertRaises(ValueError):
                runner.tiled_probabilities(None, a, b, mask, recipe, infer)
        self.assertEqual(calls, [])

    def test_bad_model_output_is_rejected_without_a_no_change_result(self):
        before, after, valid = self.inputs(64, 96)
        for value in (self.np.full((64, 96), self.np.nan, dtype='float32'),
                      self.np.full((64, 96), 1.1, dtype='float32'),
                      self.np.zeros((64, 96), dtype='float64'),
                      self.np.zeros((64, 95), dtype='float32')):
            with self.subTest(shape=value.shape), self.assertRaises(ValueError):
                runner.tiled_probabilities(None, before, after, valid, self.recipe, lambda *args: value)

    def test_cancellation_between_tiles_stops_further_inference(self):
        before, after, valid = self.inputs()
        calls, guards = [], []
        def guard():
            guards.append(True)
            if len(guards) == 2:
                raise ValueError('cancelled')
        def infer(model, a, b):
            calls.append(True)
            return self.np.zeros(a.shape[:2], dtype='float32')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            runner.tiled_probabilities(None, before, after, valid, self.recipe, infer, guard)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
