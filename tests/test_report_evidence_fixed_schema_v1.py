"""Pure fixed-class schema reuse; no source, provider or authority is mocked."""
import copy
import hashlib
import json
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from generic_agents import report_evidence_fixed_schema_v1 as fixed
from generic_agents import report_evidence_qwen_json_schema_v1 as native
from generic_agents.report_evidence_map_slots_v5 import MapSlotsWireV5, SLOT_NAMES
from generic_agents.report_evidence_reduction_wire_v6 import ReductionWireV6


CASES = (('map-v7', MapSlotsWireV5), ('reduce-v5', ReductionWireV6))


@pytest.fixture(autouse=True)
def isolated_schema_memo():
    fixed._cached_schema.cache_clear()
    yield
    fixed._cached_schema.cache_clear()


def options():
    return {'model': native.MODEL, 'max_tokens': 512,
        'enable_thinking': False, 'stream': False, 'n': 1}


def reordered(value):
    if isinstance(value, dict):
        return {key: reordered(value[key]) for key in reversed(value)}
    if isinstance(value, list):
        return [reordered(item) for item in value]
    return value


def find_node(value, key):
    if isinstance(value, dict):
        if key in value:
            return value
        for child in value.values():
            found = find_node(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_node(child, key)
            if found is not None:
                return found
    return None


@pytest.mark.parametrize('stage,model', CASES)
def test_schema_matches_the_uncached_fixed_default_and_is_generated_once(stage, model):
    expected = model.model_json_schema()
    with patch.object(model, 'model_json_schema', wraps=model.model_json_schema) as generate:
        for _ in range(5):
            local = fixed.fixed_report_schema(model)
            assert local == expected
            assert native._bytes(local) == native._bytes(expected)
            result = native.strict_options(local, options(), stage)
            assert result['schema_proof']['provider_schema'] == expected
        assert generate.call_count == 1


def test_exact_class_whitelist_rejects_same_name_subclass_and_unrelated_models():
    class OtherWire(BaseModel):
        value: str

    class UnapprovedMap(MapSlotsWireV5):
        pass

    called = []
    impostor = type(MapSlotsWireV5.__name__, (), {
        'model_json_schema': staticmethod(lambda: called.append('generated'))})
    for model in (impostor, UnapprovedMap, OtherWire, None, {}, 'MapSlotsWireV5'):
        with pytest.raises(ValueError, match='fixed_report_schema_model'):
            fixed.fixed_report_schema(model)
    assert not called
    assert fixed._cached_schema.cache_info().currsize == 0


@pytest.mark.parametrize('stage,model', CASES)
def test_nested_return_proof_and_options_mutation_never_poison_the_schema_memo(stage, model):
    expected = model.model_json_schema()
    local = fixed.fixed_report_schema(model)
    local['properties'].clear()
    local['required'].append('unapproved_field')
    local['$defs'].clear()
    assert fixed.fixed_report_schema(model) == expected
    clean = fixed.fixed_report_schema(model)
    result = native.strict_options(clean, options(), stage)
    proof_before = native.identity_schema_proof(clean, stage)
    result['schema_proof']['provider_schema']['properties'].clear()
    result['options']['response_format']['json_schema']['schema']['$defs'].clear()
    clean['required'].clear()
    again = native.strict_options(fixed.fixed_report_schema(model), options(), stage)
    assert again['schema_proof'] == proof_before
    assert again['options']['response_format']['json_schema']['schema'] == expected
    assert fixed.fixed_report_schema(model) == expected


def test_map_and_reduce_entries_remain_separate_and_stage_checks_still_run():
    mapping = fixed.fixed_report_schema(MapSlotsWireV5)
    reduction = fixed.fixed_report_schema(ReductionWireV6)
    assert mapping != reduction
    assert fixed._cached_schema.cache_info().currsize == 2
    for stage, local in (('map-v7', reduction), ('reduce-v5', mapping)):
        with pytest.raises(ValueError):
            native.identity_schema_proof(local, stage)
    for stage in ('map-v6', 'reduce-v4', None, True):
        with pytest.raises(ValueError):
            native.strict_options(mapping, options(), stage)
    assert fixed.fixed_report_schema(MapSlotsWireV5) == mapping
    assert fixed.fixed_report_schema(ReductionWireV6) == reduction


@pytest.mark.parametrize('stage,model', CASES)
def test_warm_cache_never_accepts_a_changed_or_self_resealed_caller_schema(stage, model):
    original = fixed.fixed_report_schema(model)
    proof = native.identity_schema_proof(original, stage)
    assert native.check_schema_proof(reordered(proof), reordered(original), stage) == proof
    for change in ('required', 'const', 'default', 'extra'):
        local = copy.deepcopy(original)
        if change == 'required':
            assert local['required']
            local['required'].pop()
        elif change == 'const':
            node = find_node(local, 'const')
            assert node is not None
            node['const'] = 'unapproved_value'
        elif change == 'default':
            local['default'] = {}
        else:
            local['unapproved_keyword'] = True
        altered = copy.deepcopy(proof)
        altered.update(provider_schema=copy.deepcopy(local),
            local_schema_hash=native._hash(local), provider_schema_hash=native._hash(local))
        altered['proof_hash'] = native._hash({key: value for key, value in altered.items()
            if key != 'proof_hash'})
        before = copy.deepcopy(local)
        with pytest.raises(ValueError):
            native.identity_schema_proof(local, stage)
        with pytest.raises(ValueError):
            native.check_schema_proof(altered, local, stage)
        assert local == before
        assert fixed.fixed_report_schema(model) == original


@pytest.mark.parametrize('stage,model', CASES)
def test_default_schema_proof_and_original_native_request_bytes_stay_exact(stage, model):
    original = model.model_json_schema()
    policy = native.stage_policy(stage)
    proof_value = {'schema': native.PROOF_SCHEMA, 'delivery': native.DELIVERY,
        'stage': stage, 'payload_origin': native.PAYLOAD_ORIGIN,
        'local_schema_hash': native._hash(original), 'provider_schema': copy.deepcopy(original),
        'provider_schema_hash': native._hash(original)}
    expected_proof = {**proof_value, 'proof_hash': native._hash(proof_value)}
    base = options()
    messages = [{'role': 'system', 'content': native._OLD_PREFIX + native._bytes(original).decode('utf-8')},
        {'role': 'user', 'content': '公开合成协议输入；不证明来源、授权或事实。'}]
    before = copy.deepcopy(messages)
    expected_request = {**base, 'response_format': {'type': 'json_schema', 'json_schema': {
        'name': policy['schema_name'], 'strict': True, 'schema': copy.deepcopy(original)}},
        'messages': [{'role': 'system', 'content': native.system_prefix(stage)}, copy.deepcopy(messages[1])]}
    expected_body = native._bytes(expected_request)
    result = native.prepare_request(messages, fixed.fixed_report_schema(model), base, stage)
    assert result['schema_proof'] == expected_proof
    assert result['request'] == expected_request
    assert result['body'] == expected_body
    assert result['body_sha256'] == hashlib.sha256(expected_body).hexdigest()
    assert result['body_bytes'] == len(expected_body)
    assert messages == before
    assert base == options()


def test_fixed_schema_reuse_never_replaces_fresh_wire_value_validation():
    fixed.fixed_report_schema(MapSlotsWireV5)
    wire = {'schema': 'report-evidence-map-slots-v5',
        'slots': {name: {'state': 'absent'} for name in SLOT_NAMES}}

    def envelope(value):
        return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps(value, ensure_ascii=False)}}]},
            ensure_ascii=False).encode('utf-8')

    assert native.parse_arguments(envelope(wire), 200, 'map-v7',
        response_complete=True, http_content_encoding=None) == wire
    incomplete = copy.deepcopy(wire)
    incomplete['slots'].pop(SLOT_NAMES[-1])
    with pytest.raises(ValueError):
        native.parse_arguments(envelope(incomplete), 200, 'map-v7',
            response_complete=True, http_content_encoding=None)
