"""Real Pillow bytes and deterministic measurements, no model-quality claim."""
import hashlib
import io
from PIL import Image
import pytest
from generic_agents import daily_frame_quality_v233 as q


def image(value, size=(64, 64), mode='L', format='PNG'):
    stream = io.BytesIO(); Image.new(mode, size, value).save(stream, format=format)
    return stream.getvalue()


@pytest.mark.parametrize('level', [0, 64, 128, 255])
def test_uniform_pixels_have_exact_explainable_luma_quantiles(level):
    result = q.inspect_image(image(level), 'image/png'); visual = result['visual']
    assert visual['mean_luma'] == level
    assert [visual[key] for key in ('p10_luma', 'p50_luma', 'p90_luma')] == [level]*3
    assert visual['contrast_p90_p10'] == 0 and visual['readability_verified'] is False
    assert visual['dark_fraction'] == (1 if level < 16 else 0)
    assert visual['bright_fraction'] == (1 if level >= 240 else 0)
    assert 'possible_low_contrast' in visual['candidates']
    assert all(check['status'] != 'passed' for check in result['checks'])


def test_large_real_image_is_analyzed_with_a_declared_bounded_thumbnail():
    visual = q.inspect_image(image((50, 100, 150), (2048, 1024), mode='RGB'), 'image/png')['visual']
    assert (visual['width'], visual['height']) == (2048, 1024)
    assert (visual['analyzed_width'], visual['analyzed_height']) == (1024, 512)
    assert visual['method'] == 'bounded-luma-v233' and visual['readability_verified'] is False


@pytest.mark.parametrize('content,media', [(b'', 'image/png'), (b'bad image', 'image/jpeg'),
    (image(0), 'image/jpeg'), (image(0), 'text/plain'), ('not bytes', 'image/png')])
def test_bad_or_mismatched_image_does_not_claim_readability(content, media):
    result = q.inspect_image(content, media)
    assert result['visual'] is None and result['checks'][0]['status'] == 'blocked' and result['gaps']


def test_capacity_is_a_real_decode_gate_without_downplaying_the_original_size(monkeypatch):
    content = image(64, (100, 100))
    monkeypatch.setattr(q, 'MAX_PIXELS', 9999)
    assert q.inspect_image(content, 'image/png')['visual'] is None
    monkeypatch.setattr(q, 'MAX_PIXELS', 16000000)
    monkeypatch.setattr(q, 'MAX_BYTES', len(content)-1)
    assert q.inspect_image(content, 'image/png')['visual'] is None


def test_single_frame_result_never_certifies_capture_time_coverage_or_state():
    content = image(90); sha = hashlib.sha256(content).hexdigest()
    view = {'task_id': 'task', 'task_title': '资料整理', 'entity': {'id': 'entity', 'label': '测试对象'},
        'image': {'id': 'image', 'media_type': 'image/png'}, 'limitations': ['只有单帧']}
    bundle = {'content': content, 'view': view, 'source_hash': 'a'*64, 'frame_sha256': sha,
        'pin': {'image_snapshot': {'id': 'image', 'sha256': sha}}}
    record = q.assessment_record(bundle, {}, assessed_at='2026-10-28T00:00:00+00:00')
    result = record['daily_frame_result_v233']
    assert record['modality'] == 'video' and record['kind'] == 'daily_task_frame'
    assert record['metrics']['saved_frames'] == 1 and record['source_times'][0]['value'] is None
    assert result['source_time_verified'] is result['domain_quality_verified'] is result['current_state_sufficient'] is False
    assert result['state_conclusion'] is None and result['entity_state_updated'] is False and result['cloud_calls'] == 0
    assert {'source_time', 'coverage', 'domain_quality'} <= {gap['kind'] for gap in result['gaps']}
