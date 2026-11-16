"""Pixel-only annotation contracts; no model, DB, Task or quality-pass proof."""
import copy
import hashlib
import struct
from uuid import uuid4
import zlib

import pytest
from pydantic import ValidationError
from generic_agents import pixel_annotation_contracts_v256 as contract


def region(label, x0=0, y0=0, x1=2, y1=2):
    return {'label': label, 'rectangle': {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1}}


def body():
    return {'schema': 'pixel-annotation-save-v256', 'request_id': '标注/原申请?保持-' + uuid4().hex,
        'expected_run_version': 1, 'result_hash': 'ab' * 32, 'supersedes_annotation_id': None,
        'note': '  核对原像素，只标注已经判断的区域。  ', 'regions': [region('change')]}


def test_closed_original_body_preserves_unicode_note_order_and_explicit_supersession():
    value = body(); value['supersedes_annotation_id'] = str(uuid4())
    value['regions'] = [region('change'), region('unknown', 1, 0, 2, 1)]
    checked = contract.SaveAnnotation.model_validate(value)
    assert checked.model_dump(mode='json') == value
    assert checked.note == value['note'] and checked.regions[1].label == 'unknown'


@pytest.mark.parametrize('field,value', [('schema', 'another-family'), ('request_id', 'short'),
    ('request_id', 'x' * 101), ('request_id', 'invalid-NUL-\x00-identifier'), ('request_id', 'surrogate-\ud800-identifier'),
    ('expected_run_version', True), ('expected_run_version', 1.0), ('expected_run_version', '1'),
    ('expected_run_version', 0), ('result_hash', 'AB' * 32), ('result_hash', 'a' * 63),
    ('result_hash', 'a' * 64 + '\n'), ('supersedes_annotation_id', 'not-uuid'),
    ('supersedes_annotation_id', 'ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF'), ('supersedes_annotation_id', uuid4()),
    ('note', ''), ('note', '  \n  '), ('note', 'x' * 2001), ('note', 'has\x00NUL'),
    ('note', 'has\ud800surrogate'), ('regions', ())])
def test_request_rejects_nonclosed_or_unpersistable_values(field, value):
    value_body = body(); value_body[field] = value
    with pytest.raises(ValidationError): contract.SaveAnnotation.model_validate(value_body)


@pytest.mark.parametrize('field', ['schema', 'request_id', 'expected_run_version', 'result_hash',
    'supersedes_annotation_id', 'note', 'regions'])
def test_all_save_fields_are_explicit_in_the_original_body(field):
    value = body(); del value[field]
    with pytest.raises(ValidationError): contract.SaveAnnotation.model_validate(value)


@pytest.mark.parametrize('coordinate', ['x0', 'y0', 'x1', 'y1'])
@pytest.mark.parametrize('value', [True, 1.0, '1'])
def test_coordinates_never_coerce_bool_float_or_string_to_pixel_integer(coordinate, value):
    value_body = body(); value_body['regions'][0]['rectangle'][coordinate] = value
    with pytest.raises(ValidationError): contract.SaveAnnotation.model_validate(value_body)


@pytest.mark.parametrize('bad_region', [region('negative'), region('confirmed'), region('CHANGE'),
    region('change', -1, 0, 2, 2), region('change', 0, 0, 16000001, 2),
    region('change', 2, 0, 2, 2), region('change', 3, 0, 2, 2), region('change', 0, 2, 2, 2),
    {**region('change'), 'probability': .9}, {'label': 'change', 'rectangle': {**region('change')['rectangle'], 'z': 0}}])
def test_region_contract_rejects_empty_geometry_unknown_labels_and_extra_fields(bad_region):
    value = body(); value['regions'] = [bad_region]
    with pytest.raises(ValidationError): contract.SaveAnnotation.model_validate(value)


def test_region_count_and_repeated_area_budgets_include_overlaps_before_drawing():
    value = body(); value['regions'] = [region('change')] * 257
    with pytest.raises(ValidationError): contract.SaveAnnotation.model_validate(value)
    with pytest.raises(ValueError): contract.rasterize(2, 2, value['regions'])
    value['regions'] = [region('unknown', 0, 0, 4000, 4000)] * 4
    assert len(contract.SaveAnnotation.model_validate(value).regions) == 4
    value['regions'].append(region('unknown', 0, 0, 4000, 4000))
    with pytest.raises(ValidationError): contract.SaveAnnotation.model_validate(value)
    with pytest.raises(ValueError): contract.rasterize(4000, 4000, value['regions'])


def test_unmarked_pixels_are_unknown_not_unchanged_or_false_negatives():
    value = body(); value['regions'] = []
    parsed = contract.SaveAnnotation.model_validate(value)
    mask = contract.rasterize(3, 2, parsed.model_dump(mode='json')['regions'])
    assert mask['labels'] == bytes([255]) * 6 and mask['known_valid_mask'] == bytes(6)
    assert mask['counts'] == {'changed': 0, 'unchanged': 0, 'unknown': 6, 'known_valid': 0, 'total': 6}
    assert set(mask) == {'labels', 'known_valid_mask', 'counts', 'labels_sha256', 'known_mask_sha256'}
    assert not any(key in mask for key in ['accuracy', 'probability', 'f1'])


def test_ordered_overlap_and_explicit_unknown_erase_only_the_target_pixels():
    regions = [region('change', 0, 0, 3, 2), region('unchanged', 2, 0, 3, 2), region('unknown', 1, 0, 2, 1)]
    original = copy.deepcopy(regions); mask = contract.rasterize(3, 2, regions)
    assert mask['labels'] == bytes([1, 255, 0, 1, 1, 0])
    assert mask['known_valid_mask'] == bytes([1, 0, 1, 1, 1, 1])
    assert mask['counts']['changed'] == 3 and mask['counts']['unchanged'] == 2
    assert mask['counts']['unknown'] == 1 and mask['counts']['known_valid'] == 5
    reverse = contract.rasterize(3, 2, list(reversed(regions)))
    assert reverse['labels'] == bytes([1]) * 6 and reverse['labels_sha256'] != mask['labels_sha256']
    assert regions == original


def test_original_invalid_pixels_always_remain_unknown_after_all_paints():
    original_valid = bytes([1, 0, 1, 0, 1, 1])
    mask = contract.rasterize(3, 2, [region('change', 0, 0, 3, 2)], valid_mask=original_valid)
    assert mask['labels'] == bytes([1, 255, 1, 255, 1, 1]) and mask['known_valid_mask'] == original_valid
    assert mask['counts'] == {'changed': 4, 'unchanged': 0, 'unknown': 2, 'known_valid': 4, 'total': 6}
    for key, raw in [('labels_sha256', mask['labels']), ('known_mask_sha256', mask['known_valid_mask'])]:
        assert mask[key] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('valid', [b'', bytes([0, 1]), bytes([0, 2, 1, 1]), bytes([0, 255, 1, 1]),
    bytearray([0, 1, 1, 1]), [False, True, True, True]])
def test_source_validity_has_one_immutable_zero_or_one_byte_per_original_pixel(valid):
    with pytest.raises(ValueError): contract.rasterize(2, 2, [], valid_mask=valid)


@pytest.mark.parametrize('width,height', [(True, 2), (2.0, 2), ('2', 2), (0, 1), (1, -1), (4001, 4000), (16000001, 1)])
def test_dimension_and_pixel_budget_fail_before_mask_allocation(width, height):
    with pytest.raises(ValueError): contract.rasterize(width, height, [])
    with pytest.raises(ValueError): contract.mask_png(width, height, b'')


@pytest.mark.parametrize('bad', [region('change', 0, 0, 3, 2), region('change', 0, 0, 2, 3),
    region('change', 0, 0, 0, 1), region('change', False, 0, 1, 1),
    {'label': 'change', 'rectangle': {'x0': 0, 'y0': 0, 'x1': 1, 'y1': 1, 'extra': 0}}])
def test_plain_stdlib_rasterizer_also_enforces_geometry_without_pydantic(bad):
    with pytest.raises(ValueError): contract.rasterize(2, 2, [bad])


def test_legitimate_long_original_dimension_above_100000_is_not_downscaled():
    width, height = 200000, 32  # A valid V249 minimum-side image, 6.4m pixels.
    mask = contract.rasterize(width, height, [region('unchanged', width - 1, 0, width, height)])
    assert len(mask['labels']) == width * height and mask['labels'][-1] == 0
    assert mask['labels'][width - 1::width] == bytes(height)
    assert mask['counts'] == {'changed': 0, 'unchanged': height, 'unknown': width * height - height,
        'known_valid': height, 'total': width * height}


def read_gray8(raw):
    assert raw[:8] == b'\x89PNG\r\n\x1a\n'
    offset = 8; chunks = []
    while offset < len(raw):
        length, = struct.unpack('>I', raw[offset:offset + 4]); kind = raw[offset + 4:offset + 8]
        data = raw[offset + 8:offset + 8 + length]; crc, = struct.unpack('>I', raw[offset + 8 + length:offset + 12 + length])
        assert crc == zlib.crc32(kind + data) & 0xffffffff
        chunks.append((kind, data)); offset += 12 + length
    assert offset == len(raw) and [kind for kind, data in chunks] == [b'IHDR', b'IDAT', b'IEND']
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunks[0][1])
    assert (depth, color, compression, filtering, interlace) == (8, 0, 0, 0, 0) and chunks[-1][1] == b''
    scanlines = zlib.decompress(chunks[1][1]); assert len(scanlines) == height * (width + 1)
    pixels = bytearray()
    for y in range(height):
        start = y * (width + 1); assert scanlines[start] == 0
        pixels.extend(scanlines[start + 1:start + 1 + width])
    return width, height, bytes(pixels)


@pytest.mark.parametrize('width,height', [(1, 6), (6, 1), (2, 3), (3, 2), (5, 5)])
def test_png_gray8_crc_scanlines_and_pixel_bytes_are_exact_without_image_libraries(width, height):
    pixels = bytes([0, 1, 255][i % 3] for i in range(width * height))
    encoded = contract.mask_png(width, height, pixels)
    assert read_gray8(encoded) == (width, height, pixels)
    assert encoded == contract.mask_png(width, height, pixels)


@pytest.mark.parametrize('pixels', [b'', b'\x00\x01', bytes([0, 1, 2, 255]), bytearray([0, 1, 255, 255])])
def test_png_does_not_coerce_or_rescale_bad_mask_bytes(pixels):
    with pytest.raises(ValueError): contract.mask_png(2, 2, pixels)
