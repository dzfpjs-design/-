"""Actual fulltext manifest/schema admission, without DB, dispatch or models.

JSON Schema numeric equality does not authorize changing a saved token's Python
type or zero sign. These are admission oracles, not DOM or persistence evidence.
"""
import copy
import json
import math
from pathlib import Path

import pytest
from fastapi import HTTPException

from generic_agents.data_tools import validate_manifest
from generic_agents.services import validate


def fulltext_manifest():
    path = Path(__file__).resolve().parents[1] / 'examples' / 'data_fulltext.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    value['input_schema']['properties']['min_score']['enum'] = [0, 1]
    value['input_schema']['properties']['top_k']['enum'] = [1, 2]
    validate_manifest(value)
    return value


def original_inputs():
    # Authored JSON bytes establish the original float kind and negative zero.
    return json.loads('{"records":[{"values":{"name":"alpha"},'
        '"entity_ids":["original-object"],"evidence_refs":["entity:original-object:1"]}],'
        '"field":"name","text":"alpha","top_k":1.0,"min_score":-0.0,'
        '"match_mode":"all_terms"}')


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False)


def test_actual_fulltext_numeric_enums_accept_saved_integral_floats_without_retyping():
    manifest = fulltext_manifest()
    value = original_inputs()
    before, manifest_before = wire(value), wire(manifest)

    validate(manifest['input_schema'], value)

    assert type(value['min_score']) is float
    assert math.copysign(1, value['min_score']) == -1
    assert type(value['top_k']) is float
    assert wire(value['min_score']) == '-0.0'
    assert wire(value['top_k']) == '1.0'
    assert wire(value) == before
    assert wire(manifest) == manifest_before


def test_actual_enum_zero_is_also_valid_but_does_not_rewrite_the_original_negative_float():
    schema = fulltext_manifest()['input_schema']
    original = original_inputs()
    selected = copy.deepcopy(original)
    selected['min_score'] = 0
    selected['top_k'] = 1

    validate(schema, original)
    validate(schema, selected)

    assert wire(original['min_score']) == '-0.0'
    assert wire(selected['min_score']) == '0'
    assert type(original['top_k']) is float
    assert type(selected['top_k']) is int
    assert wire(original) != wire(selected), 'schema equality is not typed input identity'


@pytest.mark.parametrize(('field', 'value'), [
    ('min_score', 0.5),   # In the base range, outside the declared enum.
    ('min_score', 51),    # Outside the actual fulltext score bound.
    ('top_k', 3),        # In the base range, outside this declared enum.
    ('top_k', 0),        # Below the actual fulltext count bound.
    ('top_k', 1.5),      # Not an integer under the registered input schema.
    ('min_score', '0'), ('top_k', '1'),
    ('min_score', False), ('min_score', True),
    ('top_k', False), ('top_k', True),
])
def test_actual_schema_rejects_nonmembers_bounds_strings_and_boolean_numeric_aliases(field, value):
    schema = fulltext_manifest()['input_schema']
    original = original_inputs()
    original[field] = value
    before = wire(original)

    with pytest.raises(HTTPException) as caught:
        validate(schema, original)

    assert caught.value.status_code == 422
    assert any(error['field'] == field for error in caught.value.detail)
    assert wire(original) == before, 'rejection must not coerce or repair the input'
