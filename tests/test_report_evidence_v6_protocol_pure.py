"""Pure V6 preparation/projection checks; not journal authenticity or quality.

Only public synthetic fixtures are used. Root separately exercises normal API,
real reservation/journal durability, old history and provider conformance.
"""
import copy
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest

from generic_agents import report_evidence_preparation as maps
from generic_agents import report_evidence_reduction_v3 as reduce
from generic_agents import report_evidence_ref_schema_v2 as compiler2
from generic_agents import report_evidence_ref_schema_v3 as compiler3
from generic_agents import report_evidence_structured_tool_v2 as tool2
from generic_agents import report_evidence_structured_tool_v3 as tool3
from generic_agents import report_evidence_projection_v5 as projection
from generic_agents import report_evidence_saved_facts_v2 as facts
from generic_agents import report_evidence_pipeline_v5 as adapter
from generic_agents import cloud_evidence_profiles as profiles
from generic_agents.contracts import digest
from generic_agents.report_evidence_map_slots_v5 import MapSlotsWireV5, SLOT_NAMES
from tests.test_report_evidence_v5_preparation_pure import cap as old_cap
from tests.test_report_evidence_projection_v5_pure import scene as old_projection_scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('V6 pure protocol cannot connect or execute')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def cap():
    value = old_cap()
    value.data['execution']['report_evidence_output'] = {'schema': 'report-evidence-output-v4',
        'max_tokens': 32768, 'map_transport': tool3.DELIVERY, 'reduce_transport': tool3.DELIVERY}
    return value


def reorder(value):
    if type(value) is dict: return {key: reorder(item) for key, item in reversed(list(value.items()))}
    if type(value) is list: return [reorder(item) for item in value]
    return value


def scene():
    model = cap(); wire, example = old_projection_scene((0, 79)); catalog = example['catalog']
    prepared = maps.build_map_preparation(model, catalog, {'businesses': [], 'object_names': []},
        ' 完整合成要求＋+。 ', generation_version=maps.GENERATION_V6)
    inputs = []
    for i, chunk in enumerate(prepared['plan']['chunks']):
        original = copy.deepcopy(wire)
        for name in ('slot_001', 'slot_080'):
            original['slots'][name]['claim']['span_refs'] = [chunk['span_refs'][0]]
        arguments = json.dumps(original, ensure_ascii=False)
        body = json.dumps({'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
            {'id': 'synthetic_' + str(i), 'type': 'function', 'function': {
                'name': 'submit_report_evidence_map_slots', 'arguments': arguments}}]}}]}, ensure_ascii=False).encode()
        metadata = tool3.inspect_response(body, 200, 'map-v6')['metadata']
        binding = copy.deepcopy(example['binding'])
        binding.update(stage=chunk['chunk_id'], reserved_call_id=f'20000000-0000-4000-8000-{i + 1:012x}',
            messages_hash=chunk['messages_hash'], generation_hash=digest({'contract': prepared['generation_contract']}),
            dispatch_hash=digest({'authorized': chunk}))
        descriptor = copy.deepcopy(example['descriptor'])
        descriptor.update(binding=copy.deepcopy(binding), binding_hash=digest(binding),
            wire_bytes=len(body), wire_sha256=metadata['response_body_sha256'])
        descriptor['descriptor_hash'] = digest({key: value for key, value in descriptor.items() if key != 'descriptor_hash'})
        policy = copy.deepcopy(prepared['generation_contract']['input_policy']); policy.pop('single_batch')
        item = {'wire': original, 'binding': binding, 'expected_binding': copy.deepcopy(binding),
            'descriptor': descriptor, 'metadata': metadata, 'evidence_profile': policy,
            'allowed_span_refs': copy.deepcopy(chunk['span_refs'])}
        item['projection'] = projection.build_map_projection(original, **{key: value for key, value in item.items()
            if key != 'wire'}, catalog=catalog, protocol_version=6)
        inputs.append(item)
    return model, catalog, prepared, inputs


def test_v6_compiler_provider_bytes_match_only_the_publicly_probed_two_arm_rewrite():
    local = MapSlotsWireV5.model_json_schema(); before = copy.deepcopy(local)
    expected = copy.deepcopy(compiler2.compile_ref_schema(local)['provider_schema'])
    expected['$def']['SlotValue']['anyOf'] = [copy.deepcopy(expected['$def'][name]) for name in ('AbsentSlot', 'PresentSlot')]
    value = compiler3.compile_ref_schema(local)
    assert value['provider_schema'] == expected and local == before
    assert value['schema'] == 'report-evidence-strict-ref-arm-proof-v3'
    assert value['audit']['schema'] == 'report-evidence-strict-ref-arm-audit-v3'
    assert value['audit']['counts']['inlined_arms'] == 2
    assert compiler3.check_ref_schema_proof(value, local) == value
    assert all(node == {'$ref': '#/$def/SlotValue'} for node in expected['$def']['Fixed80Slots']['properties'].values())


def test_explicit_output_v4_selects_new_stages_without_changing_old_generation_defaults():
    model = cap(); execution = model.data['execution']
    assert profiles.validate_evidence_output(execution) == execution
    assert profiles.declared_map_transport(execution) == tool3.DELIVERY
    assert profiles.evidence_policy(execution, 'map-v6')['max_output_tokens'] == 8192
    assert profiles.evidence_policy(execution, 'reduce-v4')['max_output_tokens'] == 32768
    assert profiles.evidence_policy(execution, 'reduce-v4')['transport_timeout_seconds'] == 600
    assert maps.GENERATION == maps.GENERATION_V2 and reduce.GENERATION == reduce.GENERATION_V3
    old = old_cap(); before = copy.deepcopy(old.data)
    assert profiles.declared_map_transport(old.data['execution']) == tool2.DELIVERY
    assert maps.map_generation(old, generation_version=maps.GENERATION_V5)['delivery'] == tool2.DELIVERY
    assert old.data == before and maps.EVIDENCE_MAP_TASK_V6 == maps.EVIDENCE_MAP_TASK_V5


@pytest.mark.parametrize('mode', ['map_transport', 'reduce_transport', 'schema', 'stage', 'bool', 'extra'])
def test_mixed_profiles_do_not_fall_back_to_old_or_new_stages(mode):
    value = cap().data['execution']; stage = 'map-v6'; declaration = value['report_evidence_output']
    if mode in ('map_transport', 'reduce_transport'): declaration[mode] = tool2.DELIVERY
    elif mode == 'schema': declaration['schema'] = 'report-evidence-output-v3'
    elif mode == 'stage': stage = 'map-v5'
    elif mode == 'bool': declaration['max_tokens'] = True
    else: declaration['fallback'] = True
    with pytest.raises(ValueError): profiles.evidence_policy(value, stage)


def test_full_map_plan_and_request_cold_rebuild_survive_jsonb_key_order_without_changing_text():
    model, catalog, prepared, inputs = scene(); original = copy.deepcopy(prepared)
    assert maps.check_map_preparation(reorder(prepared), reorder(catalog), model) == prepared
    for chunk in prepared['plan']['chunks']:
        request = maps.rebuild_map_request(prepared, catalog, model, chunk['chunk_id'])
        assert request['request_hash'] == chunk['request_hash'] != chunk['messages_hash']
        assert request['input_summary']['prepared_request_bytes'] == len(request['request_body'])
        assert 'report-evidence-map-task-v6' in request['messages'][1]['content']
        assert json.loads(request['messages'][-1]['content'])['user_instruction'] == ' 完整合成要求＋+。 '
    assert prepared == original and adapter.applies({'map_preparation': prepared}) and adapter.ref_arms({'map_preparation': prepared})
    assert all(item['projection']['slot_positions'] == [0, 79] for item in inputs)


def test_gateway_cannot_mutate_frozen_source_even_when_returning_valid_json(monkeypatch):
    model, catalog, prepared, _ = scene(); original = maps.prepare_cloud_json
    def changed(*args, **kwargs):
        messages, options, images = original(*args, **kwargs)
        value = json.loads(messages[-1]['content']); value['spans'][0][1] += ' altered'
        messages[-1]['content'] = json.dumps(value, ensure_ascii=False)
        return messages, options, images
    monkeypatch.setattr(maps, 'prepare_cloud_json', changed)
    with pytest.raises(ValueError, match='网关改变'):
        maps.rebuild_map_request(prepared, catalog, model, prepared['plan']['chunks'][0]['chunk_id'])


@pytest.mark.parametrize('field', ['schema', 'evidence_stage', 'delivery', 'schema_proof'])
def test_generation_cold_checker_rejects_mixed_versions_even_with_publicly_resigned_hashes(field):
    model = cap(); value = maps.map_generation(model, generation_version=maps.GENERATION_V6)
    if field == 'schema': value[field] = maps.GENERATION_V5
    elif field == 'evidence_stage': value[field] = 'map-v5'
    elif field == 'delivery': value[field] = tool2.DELIVERY
    else: value[field] = compiler2.compile_ref_schema(value['output_schema'])
    with pytest.raises(ValueError): maps.check_map_generation(value, model)


def test_reduce_generation4_binds_complete_gen6_plan_profiles_and_all_original_claims():
    model, catalog, prepared, items = scene(); original = copy.deepcopy(items)
    inputs = reduce.reduction_inputs_v3(prepared, catalog, model, items)
    value = reduce.build_reduction_preparation(model, prepared, catalog, items)
    spec = value['generation_contract']; request = reduce.rebuild_reduction_request(value, catalog, model, prepared, items)
    assert spec['schema'] == reduce.GENERATION_V4 and spec['evidence_stage'] == 'reduce-v4'
    assert spec['delivery'] == tool3.DELIVERY and spec['dispatch_options']['max_tokens'] == 32768
    assert spec['schema_proof']['schema'] == compiler3.PROOF_SCHEMA
    assert value['payload']['input_claims'] == inputs['input_claims']
    assert len(inputs['input_claims']) == len(items) * 2 and len(set(inputs['input_order'])) == len(inputs['input_order'])
    assert value['map_inputs_hash'] == digest(inputs['map_proofs'])
    assert request['request_hash'] == value['request_hash'] != value['messages_hash']
    assert 'report-evidence-reduction-task-v4' in request['messages'][1]['content']
    assert value['output_capacity']['max_wire_value_bytes'] == 1024 * 1024
    assert reduce.check_reduction_preparation(reorder(value), catalog, model, prepared, items) == value
    assert items == original
    for changed in (items[:-1], list(reversed(items))):
        with pytest.raises(ValueError): reduce.reduction_inputs_v3(prepared, catalog, model, changed)


def test_projection_and_saved_facts_require_explicit_matching_generation_not_metadata_guessing():
    model, catalog, prepared, items = scene(); item = items[0]
    args = {key: copy.deepcopy(value) for key, value in item.items() if key not in {'wire', 'projection'}}
    with pytest.raises(ValueError): projection.build_map_projection(item['wire'], **args, catalog=catalog)
    context = {key: item['binding'][key] for key in ('actor_id', 'workspace_id', 'draft_id', 'stage', 'source_hash', 'catalog_hash', 'messages_hash')}
    context.update(draft_revision=3, stage_status='failed', generation_schema=maps.GENERATION_V6,
        ocr_revision_kinds={'o:p1': 'raw'})
    result = facts.project_saved_facts_v2(context, catalog, item['allowed_span_refs'], item['wire'],
        journal=item['descriptor'], tool_metadata=item['metadata'])
    assert result['schema'] == 'report-evidence-saved-facts-v2' and result['original_protocol_valid'] is True
    assert result['slot_origin']['slot_positions'] == [0, 79]
    assert result['eligible_for_reduce'] is result['can_apply'] is result['can_publish'] is False
    context['generation_schema'] = maps.GENERATION_V5
    with pytest.raises(ValueError): facts.project_saved_facts_v2(context, catalog, item['allowed_span_refs'], item['wire'],
        journal=item['descriptor'], tool_metadata=item['metadata'])
