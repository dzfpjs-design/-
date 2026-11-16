"""Raw float32-to-mask exactness only, not trained quality or API acceptance."""
import json
import pytest
np = pytest.importorskip('numpy')
from PIL import Image
from generic_agents.pixel_change_output_runner_v249 import validate


def prepared(tmp_path):
    a = np.full((32, 48), .2, dtype='float32'); a[:, 24:] = .8
    np.save(tmp_path / 'probability.npy', a, allow_pickle=False)
    Image.fromarray(np.rint(a * 65534 + 1).astype('uint16')).save(tmp_path / 'probability.png')
    overlay = np.zeros((32, 48, 4), dtype='uint8'); overlay[a >= .5] = [240, 100, 39, 190]
    Image.fromarray(overlay).save(tmp_path / 'candidate.png')
    Image.new('L', (48, 32), 255).save(tmp_path / 'valid.png')
    return a, {'width': 48, 'height': 32, 'threshold_text': '0.5', 'candidate_pixels': 32 * 24}


def test_binary_mask_is_exact_raw_probability_threshold_and_full_validity(tmp_path):
    a, expected = prepared(tmp_path); result = validate(tmp_path, expected)
    mask = np.asarray(Image.open(tmp_path / 'binary-mask.png'))
    assert np.array_equal(mask, (a >= .5).astype('uint8') * 255)
    assert set(result) == {'valid.png', 'probability.npy', 'probability.png', 'candidate.png', 'binary-mask.png'}


@pytest.mark.parametrize('damage', ['nan', 'inf', 'range', 'float64', 'overlay', 'preview', 'valid', 'count'])
def test_bad_probability_or_disagreeing_image_never_derives_a_binary_result(tmp_path, damage):
    a, expected = prepared(tmp_path)
    if damage in {'nan', 'inf', 'range', 'float64'}:
        if damage == 'float64': a = a.astype('float64')
        else: a[0, 0] = {'nan': float('nan'), 'inf': float('inf'), 'range': 1.1}[damage]
        np.save(tmp_path / 'probability.npy', a, allow_pickle=False)
    elif damage == 'count': expected['candidate_pixels'] += 1
    else:
        name = {'overlay': 'candidate.png', 'preview': 'probability.png', 'valid': 'valid.png'}[damage]
        Image.new('L', (48, 32), 0).save(tmp_path / name)
    with pytest.raises(ValueError): validate(tmp_path, expected)
