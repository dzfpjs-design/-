"""Native CPU checks; run with the isolated model Python, without PostgreSQL.

No learned-quality claims are made from these deterministic and synthetic cases.
The backend environment may collect these tests but skips unavailable Torch.
"""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
NATIVE = importlib.util.find_spec('torch') is not None
sys.path.insert(0, str(ROOT / 'src'))
spec = importlib.util.spec_from_file_location('changer_converter', ROOT / 'scripts/convert_changer_weights.py')
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


class CheckpointBoundary(unittest.TestCase):
    def test_untrusted_pickle_is_rejected_before_loading_and_creates_no_output(self):
        import pickle
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sentinel = folder / 'executed'
            class Payload:
                def __reduce__(self):
                    return (eval, (f"__import__('pathlib').Path({str(sentinel)!r}).touch()",))
            (folder / converter.NAME).write_bytes(pickle.dumps(Payload()))
            with patch.object(converter, 'ROOT', folder), self.assertRaisesRegex(ValueError, 'fixed official'):
                converter.convert()
            self.assertFalse(sentinel.exists())
            self.assertFalse((folder / 'changer-r18.safetensors').exists())

    def test_same_size_untrusted_artifact_is_rejected_by_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / converter.NAME
            with path.open('wb') as target:
                target.truncate(converter.SIZE)
            with patch.object(converter, 'ROOT', Path(tmp)), self.assertRaisesRegex(ValueError, 'fixed official'):
                converter.convert()
            self.assertFalse((Path(tmp) / 'changer-r18.safetensors').exists())


@unittest.skipUnless(NATIVE, 'requires the isolated native Torch environment')
class GraphBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        import torch
        from generic_agents import local_changer_model as graph
        cls.np, cls.torch, cls.graph = np, torch, graph
        torch.set_num_threads(2)

    def image(self, height=33, width=47):
        return self.np.full((height, width, 3), 127, dtype=self.np.uint8)

    def test_rgb_normalization_precedes_padding_and_does_not_mutate_input(self):
        image = self.image()
        old = image.copy()
        value, shape = self.graph.normalized_rgb(image)
        self.assertEqual(shape, (33, 47))
        self.assertEqual(tuple(value.shape), (1, 3, 64, 64))
        self.torch.testing.assert_close(value[0, :, 0, 0], self.torch.tensor([
            (127 - 123.675) / 58.395, (127 - 116.28) / 57.12, (127 - 103.53) / 57.375]))
        self.assertEqual(int(self.torch.count_nonzero(value[:, :, 33:, :])), 0)
        self.assertEqual(int(self.torch.count_nonzero(value[:, :, :, 47:])), 0)
        self.assertTrue(self.np.array_equal(image, old))

    def test_unsupported_rgb_and_sizes_are_rejected_without_resizing(self):
        for image in (self.image(31, 47), self.image(33, 513), self.image().astype('float32'),
                      self.image()[:, :, :1], self.image().reshape(-1)):
            with self.subTest(shape=image.shape), self.assertRaises(ValueError):
                self.graph.normalized_rgb(image)

    def test_exchange_swaps_even_columns_or_channels_without_changing_inputs(self):
        a = self.torch.arange(2 * 3 * 4).reshape(1, 2, 3, 4).float()
        b = a + 100
        for axis in (1, 3):
            x, y = self.graph.exchange(a, b, axis)
            for i in range(a.shape[axis]):
                source = b if i % 2 == 0 else a
                self.torch.testing.assert_close(x.select(axis, i), source.select(axis, i))
            self.torch.testing.assert_close(x + y, a + b)
        self.assertEqual(float(a.max()), 23)
        self.assertEqual(float(b.min()), 100)

    def test_zero_displacement_is_identity_and_outside_grid_is_zero(self):
        value = self.torch.arange(20).reshape(1, 1, 4, 5).float()
        flow = self.torch.zeros((1, 2, 4, 5))
        self.torch.testing.assert_close(self.graph.DualAlignment.warp(value, flow), value)
        flow[:] = 100
        self.assertEqual(int(self.torch.count_nonzero(self.graph.DualAlignment.warp(value, flow))), 0)

    def model(self, operation):
        torch = self.torch
        class Output(torch.nn.Module):
            def forward(self, a, b):
                return operation(a, b)
        return Output().eval()

    def test_softmax_is_change_class_probability_with_padding_cropped(self):
        model = self.model(lambda a, b: self.torch.cat((self.torch.zeros_like(a[:, :1]), self.torch.ones_like(a[:, :1])), 1))
        result = self.graph.probabilities(model, self.image(), self.image())
        self.assertEqual(result.shape, (33, 47))
        self.np.testing.assert_allclose(result, 1 / (1 + self.np.exp(-1)), rtol=1e-6)

    def test_training_misalignment_bad_shape_and_nonfinite_logits_are_rejected(self):
        good = self.model(lambda a, b: a[:, :2])
        good.train()
        with self.assertRaisesRegex(ValueError, 'evaluation-mode'):
            self.graph.probabilities(good, self.image(), self.image())
        good.eval()
        with self.assertRaisesRegex(ValueError, 'same pixel dimensions'):
            self.graph.probabilities(good, self.image(), self.image(34, 47))
        for operation in (lambda a, b: a, lambda a, b: a[:, :2, :-1],
                          lambda a, b: a[:, :2].double(), lambda a, b: a[:, :2] * float('nan')):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.graph.probabilities(self.model(operation), self.image(), self.image())

    def test_scoped_checkpoint_allowlist_is_restored_when_loading_fails(self):
        source = converter.ROOT / converter.NAME
        if not source.exists():
            self.skipTest('fixed original checkpoint not prepared')
        prior = self.torch.serialization.get_safe_globals()
        with patch.object(self.torch, 'load', side_effect=RuntimeError('load rejected')):
            with self.assertRaisesRegex(RuntimeError, 'load rejected'):
                converter.convert()
        self.assertEqual(self.torch.serialization.get_safe_globals(), prior)

    def test_pinned_learned_weights_strictly_match_and_cpu_forward_is_finite(self):
        path = converter.ROOT / 'changer-r18.safetensors'
        if not path.exists():
            self.skipTest('converted local checkpoint not prepared')
        from safetensors.torch import load_file
        self.assertEqual(converter.fingerprint(path), '507932767138639cc64079bcfd5df2861aeeef9c26e924b799e2d4de92bce2a6')
        state = load_file(path, device='cpu')
        model = self.graph.ChangerExR18().eval()
        model.load_state_dict(state, strict=True)
        self.assertEqual(len(state), 173)
        before = self.image(128, 160)
        after = before.copy()
        after[32:80, 60:110] = [220, 180, 130]
        result = self.graph.probabilities(model, before, after)
        self.assertEqual(result.shape, (128, 160))
        self.assertTrue(bool(self.np.isfinite(result).all()))
        self.assertGreaterEqual(float(result.min()), 0)
        self.assertLessEqual(float(result.max()), 1)
        proof = {'schema': 'changer-native-graph-v1', 'backend': 'cpu',
                 'graph_sha256': hashlib.sha256((ROOT / 'src/generic_agents/local_changer_model.py').read_bytes()).hexdigest(),
                 'weights_sha256': converter.fingerprint(path), 'tensor_count': len(state),
                 'strict_parameter_match': True, 'shape': list(result.shape), 'finite': True,
                 'probability_min': float(result.min()), 'probability_max': float(result.max()),
                 'candidate_pixels_at_0_5': int((result >= .5).sum()),
                 'input_kind': 'synthetic_RGB_engineering_case', 'cloud_calls': 0,
                 'domain_quality_verified': False, 'upstream_numerical_equivalence_verified': False,
                 'platform_enabled': False}
        self.__class__.proof = proof


if __name__ == '__main__':
    unittest.main(verbosity=2)
