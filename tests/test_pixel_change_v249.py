"""Pure input and local receipt boundaries; no fabricated model success."""
import copy
import io
from uuid import uuid4
import pytest
from PIL import Image
from pydantic import ValidationError
from generic_agents.pixel_change_contracts_v249 import Create, Command
from generic_agents import pixel_change_codec_v249 as codec


def create_body():
    return {'schema': 'pixel-change-create-v249', 'request_id': 'original/request?中文-123',
        'instance_version': 1, 'release_id': str(uuid4()), 'before_image_id': str(uuid4()),
        'before_sha256': 'a' * 64, 'after_image_id': str(uuid4()), 'after_sha256': 'b' * 64,
        'model_installation_id': str(uuid4()), 'model_generation': 1, 'model_recipe_hash': 'c' * 64,
        'pixel_correspondence_confirmed': True, 'full_image_present_confirmed': True,
        'input_order': 'before_after', 'threshold_text': '0.5'}


@pytest.mark.parametrize('key,value', [
    ('pixel_correspondence_confirmed', False), ('pixel_correspondence_confirmed', 1),
    ('full_image_present_confirmed', False), ('full_image_present_confirmed', 'true'),
    ('threshold_text', .5), ('threshold_text', '0.0'), ('threshold_text', '1.0'),
    ('threshold_text', '0.1234567'), ('threshold_text', 'NaN'), ('threshold_text', ' 0.5'),
    ('instance_version', True), ('model_generation', 1.0), ('release_id', 'opaque'),
    ('input_order', 'auto'), ('schema', 'geo-pair')])
def test_create_rejects_implicit_ack_numeric_coercion_and_geospatial_contract(key, value):
    with pytest.raises(ValidationError):
        Create.model_validate({**create_body(), key: value})


def test_complete_request_preserves_original_nonce_and_threshold_token():
    body = create_body(); body['threshold_text'] = '0.500'
    assert Create.model_validate(body).model_dump() == body
    default = {k: v for k, v in body.items() if k != 'threshold_text'}
    assert Create.model_validate(default).threshold_text == '0.5'
    with pytest.raises(ValidationError):
        Create.model_validate({**body, 'crs': 'EPSG:4326'})
    with pytest.raises(ValidationError):
        Command.model_validate({'request_id': 'cancel-request-123', 'version': True})


def png(mode='RGB', size=(80, 64), color=80, format='PNG'):
    output = io.BytesIO(); Image.new(mode, size, color).save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize('raw', [png('L'), png('RGBA'), png(size=(31, 80)), png(format='JPEG'), b'not-a-PNG'])
def test_only_true_bounded_rgb_png_is_accepted(raw):
    with pytest.raises((ValueError, OSError)):
        codec.png_metadata(raw)


def test_author_sized_pixel_image_needs_no_invented_georeference():
    assert codec.png_metadata(png(size=(730, 544))) == (730, 544)


def test_sixteen_bit_rgb_ihdr_is_rejected_before_pillow_can_truncate_precision():
    raw = bytearray(png()); raw[24] = 16
    with pytest.raises(ValueError, match='8位RGB'):
        codec.png_metadata(bytes(raw))


def receipt():
    return {'schema': 'local-changer-comparison-v1', 'learned_model': True, 'backend': 'cpu', 'cloud_calls': 0,
        'width': 80, 'height': 64, 'total_pixels': 5120, 'common_valid_pixels': 5120, 'unknown_pixels': 0,
        'probability_threshold': .5, 'aligned_input_sha256': {'before': 'a', 'after': 'b', 'valid': 'c'},
        'weights_sha256': codec.RECIPE['weights']['sha256'],
        'graph_sha256': codec.RECIPE['files']['local_changer_model.py'], 'candidate_only': True,
        'semantics': 'learned_binary_change_candidates_only', 'object_state_updated': False,
        'resizing': 'none', 'cloud_shadow_screening': 'not_applied',
        'candidate_pixels': 12, 'tiles_inferred': 1, 'inference_seconds': .1}


@pytest.mark.parametrize('key,value', [('learned_model', 1), ('cloud_calls', False), ('width', True),
    ('unknown_pixels', 1), ('weights_sha256', 'd' * 64), ('candidate_pixels', True),
    ('candidate_pixels', 5121), ('tiles_inferred', 0), ('inference_seconds', float('inf'))])
def test_wrong_model_or_incomplete_output_is_never_a_success(key, value):
    value_dict = {**receipt(), key: value}
    with pytest.raises(ValueError):
        codec.validate_receipt(value_dict, 80, 64, .5, receipt()['aligned_input_sha256'])


def test_exact_native_receipt_is_validated_without_running_or_substituting_model():
    value = receipt(); before = copy.deepcopy(value)
    codec.validate_receipt(value, 80, 64, .5, value['aligned_input_sha256'])
    assert value == before
