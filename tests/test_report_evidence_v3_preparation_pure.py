"""Synthetic, opt-in wire preparation only; root executes all tests.

No journal bytes/provider authenticity or user workflow is simulated as proven.
The fixtures explicitly supply trusted expected bindings, as the future
pipeline must derive from its frozen plan and verified original journal.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest

from generic_agents import report_evidence_preparation as maps
from generic_agents import report_evidence_reduction as reduce
from generic_agents import report_evidence_wire_v3 as wire
from generic_agents.cloud_input_profiles import input_policy, request_options
from generic_agents.contracts import digest
from generic_agents.gateway import CLOUD_JSON_SYSTEM_PREFIX, cloud_json_options, prepare_cloud_json
from generic_agents.report_evidence_plan import MapResult, build_evidence_source
from generic_agents.report_evidence_semantics import FullMergeResultV4


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure wire preparation cannot dispatch, connect or spawn')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def canonical(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def reordered(value):
    if type(value) is dict:
        return {key: reordered(value[key]) for key in reversed(list(value))}
    if type(value) is list:
        return [reordered(item) for item in value]
    return value


def uuid(number):
    return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))


def capability(provider='deepseek', maximum=32768, *, nested=True):
    execution = {'provider': provider, 'model': 'deepseek-v4-pro' if provider == 'deepseek' else 'qwen-plus',
                 'max_tokens': 8192}
    if provider == 'deepseek':
        execution['thinking_mode'] = 'disabled'
    if nested:
        execution['report_evidence_output'] = {'schema': 'report-evidence-output-v1', 'max_tokens': maximum}
    else:
        assert maximum == 8192  # legacy fixture never widens the generic limit
    return SimpleNamespace(data={'execution': execution, 'data_policy': {'redact_fields': []}})


def scene(provider='deepseek', maximum=32768, *, version=maps.GENERATION_V3):
    cap = capability(provider, maximum, nested=version == maps.GENERATION_V3)
    catalog = build_evidence_source('a' * 64, [
        ['w:p1', ' 保留＋与+、标点和末尾空白。 '], ['w:p2', ''], ['o:p1', '同源OCR合成文字。']])
    prep = maps.build_map_preparation(cap, catalog, {'object_names': []}, ' 合成要求。 ', generation_version=version)
    inputs = []
    if version != maps.GENERATION_V3:
        return cap, catalog, prep, inputs
    for position, chunk in enumerate(prep['plan']['chunks']):
        value = {'schema': wire.MAP_WIRE, 'claims': [{'kind': '职责', 'statement': ' 合成声明＋+。 ',
            'span_refs': [chunk['span_refs'][0]], 'status': 'explicit', 'explanation': ''}]}
        binding = {'schema': 'report-evidence-journal-binding-v1', 'actor_id': uuid(1),
            'workspace_id': uuid(2), 'draft_id': uuid(3), 'stage': chunk['chunk_id'],
            'reserved_call_id': uuid(10 + position), 'source_hash': prep['catalog_descriptor']['source_hash'],
            'catalog_hash': prep['catalog_descriptor']['catalog_hash'],
            'generation_hash': digest({'published_model_hash': 'b' * 64, 'contract': prep['generation_contract']}),
            'dispatch_hash': digest({'model_id': uuid(4), 'authorized': chunk,
                'map_preparation_hash': prep['preparation_hash'], 'map_plan_hash': prep['plan']['plan_hash']}),
            'messages_hash': chunk['messages_hash']}
        item = {'wire': value, 'binding': binding, 'expected_binding': copy.deepcopy(binding),
            # Deliberately distinct original-byte fixture, not canonical value digest.
            'wire_sha256': hashlib.sha256(json.dumps(value, ensure_ascii=True, indent=2).encode()).hexdigest(),
            'allowed_span_refs': copy.deepcopy(chunk['span_refs'])}
        item['projection'] = wire.build_map_projection(value, catalog=catalog,
            **{key: item[key] for key in ('binding', 'expected_binding', 'wire_sha256', 'allowed_span_refs')})
        inputs.append(item)
    return cap, catalog, prep, inputs


def reproject(item, catalog):
    item['projection'] = wire.build_map_projection(item['wire'], catalog=catalog,
        **{key: item[key] for key in ('binding', 'expected_binding', 'wire_sha256', 'allowed_span_refs')})


def seal(value):
    value['preparation_hash'] = digest({key: item for key, item in value.items() if key != 'preparation_hash'})
    return value


@pytest.mark.parametrize('version', [maps.GENERATION_V1, maps.GENERATION_V2])
def test_old_generation_and_gateway_messages_rebuild_without_new_fields(version):
    cap, catalog, prep, _ = scene(maximum=8192, version=version)
    execution = cap.data['execution']
    spec = {'schema': version, 'task': maps._task(version),
        'output_schema': canonical(MapResult.model_json_schema()),
        'gateway_prefix_hash': digest(CLOUD_JSON_SYSTEM_PREFIX),
        'dispatch_options': request_options(execution, cloud_json_options(execution), full_report=True),
        'input_policy': {'single_batch': True, **input_policy(execution, full_report=True)},
        'max_prepared_bytes': 100000, 'max_chunks': 24}
    assert prep['generation_contract'] == spec
    assert maps.GENERATION == maps.GENERATION_V2
    assert hashlib.sha256(maps.EVIDENCE_MAP_TASK_V1.encode()).hexdigest() == '573772f882562bc644ad129944878273b7963c7d69a04b09e9ab09f39d1e2189'
    for chunk in prep['plan']['chunks']:
        actual = maps.rebuild_map_request(reordered(prep), reordered(catalog), cap, chunk['chunk_id'])
        trusted = {'schema': 'report-evidence-map-task-v1' if version == maps.GENERATION_V1 else 'report-evidence-map-task-v2',
            'task': maps._task(version), 'purpose': 'candidate_only', 'source_policy': 'untrusted_evidence',
            'full_report_confirmed': False}
        assert json.loads(actual['messages'][1]['content']) == trusted
        assert actual['messages_hash'] == chunk['messages_hash']
    results = {chunk['chunk_id']: {'schema': 'report-evidence-map-v1', 'claims': [{
        'key': 'legacy_fact', 'kind': '职责', 'statement': '旧合成声明', 'span_refs': [chunk['span_refs'][0]],
        'status': 'explicit', 'explanation': ''}]} for chunk in prep['plan']['chunks']}
    old = reduce.build_reduction_preparation(cap, prep, catalog, results)
    assert old['schema'] == reduce.PREPARATION and old['generation_contract']['schema'] == reduce.GENERATION_V1
    assert set(old) == {'schema', 'map_preparation_hash', 'map_results_hash', 'input_items_hash',
        'generation_contract', 'generation_contract_hash', 'payload', 'payload_hash', 'messages_hash',
        'input_summary', 'call_count', 'requires_separate_authorization', 'can_apply', 'can_publish', 'preparation_hash'}
    assert old['generation_contract']['output_schema'] == canonical(FullMergeResultV4.model_json_schema())
    assert reduce.check_reduction_preparation(reordered(old), reordered(catalog), cap, reordered(prep), reordered(results)) == old
    actual = reduce.rebuild_reduction_request(old, catalog, cap, prep, results)
    assert actual['messages_hash'] == old['messages_hash']
    assert json.loads(actual['messages'][1]['content'])['schema'] == 'report-evidence-reduction-task-v1'


def test_new_map_is_explicit_wire_v3_and_freezes_exact_schema_messages_and_id_policy():
    cap, catalog, prep, _ = scene()
    spec = prep['generation_contract']
    assert spec['schema'] == maps.GENERATION_V3
    assert {key: spec[key] for key in ('wire_schema', 'projection_schema', 'id_policy', 'evidence_stage')} == {
        'wire_schema': wire.MAP_WIRE, 'projection_schema': wire.MAP_PROJECTION,
        'id_policy': wire.DERIVATION, 'evidence_stage': 'map-v3'}
    assert spec['output_schema'] == canonical(wire.MapResultWireV3.model_json_schema())
    assert spec['dispatch_options']['max_tokens'] == spec['input_policy']['max_output_tokens'] == 8192
    assert 'key' not in spec['output_schema']['$defs']['MapClaimWireV3']['properties']
    assert maps.check_map_preparation(reordered(prep), reordered(catalog), cap) == prep
    request = maps.rebuild_map_request(prep, catalog, cap, 'map-1')
    assert json.loads(request['messages'][1]['content'])['schema'] == 'report-evidence-map-task-v3'
    assert 'map_plan_hash' not in json.loads(request['messages'][2]['content'])  # no circular plan hash
    user = json.loads(request['messages'][2]['content'])
    payload = {key: user[key] for key in ('schema', 'source_hash', 'catalog_hash', 'spans')}
    expected, _, _ = prepare_cloud_json(cap,
        maps.map_messages(spec, payload, prep['context'], prep['user_instruction']), spec['output_schema'])
    assert request['messages'] == expected
    assert request['messages'][0]['content'] == CLOUD_JSON_SYSTEM_PREFIX + json.dumps(spec['output_schema'], ensure_ascii=False)


@pytest.mark.parametrize('provider,maximum', [('deepseek', 131072), ('qwen', 32768)])
def test_small_complete_v2_reduction_has_configured_output_but_no_claim_of_fit(provider, maximum):
    cap, catalog, prep, inputs = scene(provider, maximum)
    before = copy.deepcopy([cap.data, catalog, prep, inputs])
    value = reduce.build_reduction_preparation(cap, prep, catalog, map_inputs=inputs)
    assert value['schema'] == reduce.PREPARATION_V2
    spec = value['generation_contract']; payload = value['payload']
    assert spec['schema'] == reduce.GENERATION_V2 and spec['evidence_stage'] == 'reduce-v2'
    assert spec['output_schema'] == canonical(wire.ReduceResultWireV5.model_json_schema())
    assert spec['dispatch_options']['max_tokens'] == spec['input_policy']['max_output_tokens'] == maximum
    assert spec['map_plan_hash'] == prep['plan']['plan_hash'] == value['map_plan_hash']
    assert spec['map_preparation_hash'] == prep['preparation_hash']
    assert spec['map_inputs_hash'] == digest(value['map_proofs']) == value['map_inputs_hash']
    assert spec['model_hash'] == digest(cap.data)  # minimal preparation cap, not published authority
    assert value['input_order'] == ['map-1/claims/0', 'map-2/claims/0']
    assert payload['input_claims'] == [item['wire']['claims'][0] for item in inputs]
    assert all('key' not in claim for claim in payload['input_claims'])
    assert payload['input_claims_hash'] == digest(payload['input_claims'])
    assert payload['input_order_hash'] == digest(value['input_order'])
    assert value['input_items_hash'] != payload['input_claims_hash']
    assert 'wire' not in value and all('wire' not in proof and 'claims' not in proof for proof in value['map_proofs'])
    assert value['output_capacity'] == {'schema': 'report-evidence-output-capacity-v1',
        'max_output_tokens': maximum, 'max_wire_value_bytes': 262144, 'completion_required': 'stop',
        'input_claim_count': 2, 'required_reconciliation_entries': 2, 'token_estimate': None,
        'fit_guaranteed': False, 'overflow_behavior': 'preserve_response_and_fail'}
    assert reduce.check_reduction_preparation(reordered(value), reordered(catalog), cap, reordered(prep),
        map_inputs=reordered(inputs)) == value
    actual = reduce.rebuild_reduction_request(value, catalog, cap, prep, map_inputs=inputs)
    assert actual['messages_hash'] == value['messages_hash'] == digest(actual['messages'])
    assert json.loads(actual['messages'][1]['content'])['schema'] == 'report-evidence-reduction-task-v2'
    assert json.loads(actual['messages'][2]['content']) == payload
    assert [cap.data, catalog, prep, inputs] == before


@pytest.mark.parametrize('change', ['missing_first', 'missing_tail', 'reordered', 'duplicate', 'extra'])
def test_entire_original_map_plan_is_required_before_reduction(change):
    cap, catalog, prep, inputs = scene()
    if change == 'missing_first': inputs.pop(0)
    elif change == 'missing_tail': inputs.pop()
    elif change == 'reordered': inputs.reverse()
    elif change == 'duplicate': inputs[1] = copy.deepcopy(inputs[0])
    else: inputs.append(copy.deepcopy(inputs[-1]))
    with pytest.raises(ValueError):
        reduce.build_reduction_preparation(cap, prep, catalog, map_inputs=inputs)


@pytest.mark.parametrize('change', ['batch_union', 'messages', 'scope', 'generation', 'call', 'projection'])
def test_per_map_expected_binding_allowlist_and_original_projection_are_not_replaceable(change):
    cap, catalog, prep, inputs = scene(); item = inputs[1]
    if change == 'batch_union':
        item['allowed_span_refs'] = list(catalog['index']); reproject(item, catalog)
    elif change == 'projection':
        item['projection']['claims'][0]['key'] = 'self_created'
        item['projection']['projection_hash'] = digest({key: value for key, value in item['projection'].items() if key != 'projection_hash'})
    else:
        field, value = {'messages': ('messages_hash', 'f' * 64), 'scope': ('actor_id', uuid(88)),
            'generation': ('generation_hash', 'f' * 64), 'call': ('reserved_call_id', inputs[0]['binding']['reserved_call_id'])}[change]
        item['binding'][field] = item['expected_binding'][field] = value
        reproject(item, catalog)
    with pytest.raises(ValueError):
        reduce.build_reduction_preparation(cap, prep, catalog, map_inputs=inputs)


def test_unchanged_expected_binding_rejects_an_actual_scope_change():
    cap, catalog, prep, inputs = scene()
    inputs[0]['binding']['workspace_id'] = uuid(99)
    with pytest.raises(ValueError):
        reduce.build_reduction_preparation(cap, prep, catalog, map_inputs=inputs)


@pytest.mark.parametrize('change', ['fit', 'id_policy', 'plan', 'proof', 'input_order', 'wire_schema'])
def test_public_rehash_cannot_replace_original_preparation_or_projection_contract(change):
    cap, catalog, prep, inputs = scene()
    value = reduce.build_reduction_preparation(cap, prep, catalog, map_inputs=inputs)
    if change == 'fit': value['output_capacity']['fit_guaranteed'] = True
    elif change == 'id_policy': value['generation_contract']['id_policy'] = 'self_policy'
    elif change == 'plan': value['map_plan_hash'] = 'f' * 64
    elif change == 'proof': value['map_proofs'][0]['wire_sha256'] = 'f' * 64
    elif change == 'input_order': value['input_order'].reverse()
    else: value['generation_contract']['wire_result_schema'] = 'report-evidence-candidate-v4'
    seal(value)
    with pytest.raises(ValueError):
        reduce.check_reduction_preparation(value, catalog, cap, prep, map_inputs=inputs)


@pytest.mark.parametrize('version', ['unregistered', maps.GENERATION_V1, maps.GENERATION_V2])
def test_unknown_or_mixed_map_generation_cannot_be_rehashed_into_v3(version):
    cap, catalog, prep, _ = scene()
    prep['generation_contract']['schema'] = version; seal(prep)
    with pytest.raises(ValueError): maps.check_map_preparation(prep, catalog, cap)


def test_new_and_old_reducer_inputs_are_disjoint_and_unknown_versions_refused():
    cap, catalog, prep, inputs = scene()
    for version in (reduce.GENERATION_V1, 'unregistered'):
        with pytest.raises(ValueError):
            reduce.build_reduction_preparation(cap, prep, catalog, map_inputs=inputs, generation_version=version)
    with pytest.raises(ValueError): reduce.build_reduction_preparation(cap, prep, catalog, {}, map_inputs=inputs)
    old_cap, old_catalog, old_prep, _ = scene(maximum=8192, version=maps.GENERATION_V2)
    with pytest.raises(ValueError): reduce.build_reduction_preparation(old_cap, old_prep, old_catalog, map_inputs=inputs)
    with pytest.raises(ValueError):
        reduce.build_reduction_preparation(old_cap, old_prep, old_catalog, {}, generation_version=reduce.GENERATION_V2)


def test_all_empty_maps_do_not_authorize_paid_reduction():
    cap, catalog, prep, inputs = scene()
    for item in inputs:
        item['wire']['claims'] = []
        item['wire_sha256'] = hashlib.sha256(json.dumps(item['wire']).encode()).hexdigest()
        reproject(item, catalog)
    assert reduce.reduction_inputs_v2(prep, catalog, inputs)['input_order'] == []
    with pytest.raises(ValueError, match='全部Map'):
        reduce.build_reduction_preparation(cap, prep, catalog, map_inputs=inputs)
