"""Actual local ranking and manifest validation; no DB, transport or model doubles.

JSON Schema integer accepts 1.0. It remains a float in the saved parameters and
Invocation inputs; only the implementation's temporary slice index may be int.
These tests do not establish worker, HTTP, persistence or model acceptance.
"""
import copy
import json
import math
from pathlib import Path

import pytest
from fastapi import HTTPException

from generic_agents.contracts import DataToolStep
from generic_agents.data_tools import builtin
from generic_agents.retrieval import fulltext, ordered
from generic_agents.services import validate


EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'
CAPABILITY_ID = '11111111-1111-4111-8111-111111111111'


def manifest(name='data_fulltext.json'):
    return json.loads((EXAMPLES / name).read_text(encoding='utf-8'))


def inputs(top_k=1.0, *, semantic=False):
    value = {
        'records': [
            {'values': {'name': 'alpha alpha vegetation', 'source_number': -0.0},
                'entity_ids': ['object-a'], 'evidence_refs': ['entity:object-a:1']},
            {'values': {'name': 'alpha forest', 'source_number': 1.0},
                'entity_ids': ['object-b'], 'evidence_refs': ['entity:object-b:1']},
            {'values': {'name': 'unrelated water'},
                'entity_ids': ['object-c'], 'evidence_refs': ['entity:object-c:1']},
        ],
        'field': 'name', 'text': 'alpha', 'top_k': top_k, 'min_score': -0.0,
    }
    if not semantic:
        value['match_mode'] = 'all_terms'
    return value


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False)


@pytest.mark.parametrize('top_k', [1, 1.0, 2.0, 20.0])
@pytest.mark.parametrize('entry', ['fulltext', 'registered_builtin'])
def test_schema_valid_integral_float_runs_the_actual_fulltext_without_retyping_inputs(top_k, entry):
    spec = manifest()
    value = inputs(top_k)
    parameters = {key: item for key, item in value.items() if key != 'records'}
    step = DataToolStep.model_validate({'id': 'search', 'capability_id': CAPABILITY_ID,
        'input_from': 'evidence', 'parameters': parameters}).model_dump()
    assert type(step['parameters']['top_k']) is type(top_k)
    assert wire(step['parameters']) == wire(parameters)
    validate(spec['input_schema'], value)
    before = wire(value)

    result = fulltext(value) if entry == 'fulltext' else builtin('data_fulltext_search', value)

    validate(spec['output_schema'], result)
    assert result['summary']['method'] == 'bounded_fulltext_bm25'
    assert result['summary']['matched_records'] == 2
    assert result['summary']['returned_records'] == min(top_k, 2)
    assert len(result['records']) == len(result['summary']['ranking']) == min(top_k, 2)
    assert result['summary']['ranked_subset'] is True
    assert type(result['summary']['top_k']) is type(top_k)
    assert wire(result['summary']['top_k']) == wire(top_k)
    assert all(record in value['records'] for record in result['records'])
    assert all(record is not source for record in result['records'] for source in value['records'])
    assert wire(value) == before, 'the saved input is not normalized to make slicing work'
    assert math.copysign(1, value['min_score']) == -1


@pytest.mark.parametrize('name', ['data_fulltext.json', 'data_semantic.json'])
def test_shared_actual_ranking_boundary_accepts_the_two_manifests_integral_float(name):
    # Both real callers use ordered(). This covers their common local selector,
    # without pretending to invoke an embedding model or inventing its receipt.
    value = inputs(1.0, semantic=name == 'data_semantic.json')
    validate(manifest(name)['input_schema'], value)
    before = wire(value)
    result = ordered(value, [0.9, 0.8, 0.0], {'method': 'local_selector_unit_test'})
    assert result['records'] == [value['records'][0]]
    assert type(result['summary']['top_k']) is float
    assert wire(result['summary']['top_k']) == '1.0'
    assert wire(value) == before


@pytest.mark.parametrize('top_k', [0, 0.0, -1, 21, 1.5, True, False, '1.0', None])
def test_actual_manifest_validation_rejects_invalid_count_before_tool_execution(top_k):
    # The registered runtime validates before dispatch. A local int() conversion
    # must not be used to make invalid booleans, fractions or strings legal.
    value = inputs(top_k)
    before = copy.deepcopy(value)
    with pytest.raises(HTTPException) as caught:
        validate(manifest()['input_schema'], value)
    assert caught.value.status_code == 422
    assert any(item['field'] == 'top_k' for item in caught.value.detail)
    assert value == before
