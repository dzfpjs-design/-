"""Public synthetic pure protocol values, never paid/source/IAM acceptance.

The original fixtures and receipt-shaped dictionaries below do not constitute
real captures, accepted consumers or database permissions. Root executes tests.
"""
import copy
import hashlib
import json
import socket
import subprocess
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_business_catalogue_allocation_preparation_v213 as preparation
from generic_agents import report_business_catalogue_allocation_plan_v213 as planning
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents import report_business_catalogue_wire_v211 as catalogue
from generic_agents import report_business_catalogue_partial_v212 as partial
from generic_agents import report_indexed_inputs_v178 as indexed
from generic_agents import report_saved_map_design_preparation_v176 as original
from generic_agents.contracts import digest
from generic_agents.gateway import prepare_cloud_json
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_saved_map_design_preparation_v176 import source_scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure allocation native tests cannot connect, execute or spend')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def scene(partial_origin=False):
    cap, source = source_scene()
    cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    snapshot, claims, _ = original._source(source); count = len(claims)
    manifest = indexed.build_indexed_inputs(claims, snapshot['snapshot_hash'])
    arguments = {'indexed': manifest, 'input_claims': claims,
        'source_snapshot_hash': snapshot['snapshot_hash'], 'catalog': source['catalog']}
    machine = {'schema': catalogue.WIRE, 'input_count': count, 'businesses': [],
        'allocation_groups': [{'kind': 'background', 'business_keys': [],
            'input_positions': list(range(count)), 'reason': '公开合成来源的纯协议测试，不证明业务含义。',
            'question_keys': []}], 'questions': [], 'merge_suggestions': []}
    if partial_origin:
        machine['businesses'] = [{'key': 'ops', 'name': 'SYNTHETIC_NOT_LITERAL_NAME_213',
            'purpose': '人工待核', 'boundary': '仅本样本', 'seed_positions': [0],
            'name_basis': 'explicit', 'reason': '公开合成错误明示标记'}]
        machine['allocation_groups'][0].update(kind='business', business_keys=['ops'])
    machine_bytes = raw(machine)
    projection = (partial.build_partial_projection(machine_bytes, **arguments) if partial_origin
        else catalogue.build_catalogue_projection(machine_bytes, **arguments))
    consumer_id, call_id, job_id, request_id = [str(uuid4()) for _ in range(4)]
    metadata = {'schema': 'report-business-catalogue-draft-origin-v211', 'consumer_id': consumer_id,
        'consumer_version': 3, 'producer_id': snapshot['producer']['id'], 'revision': 2,
        'source_snapshot_hash': snapshot['snapshot_hash'], 'indexed_inputs_hash': manifest['inputs_hash'],
        'input_claims_hash': manifest['input_claims_hash'], 'input_count': count,
        'projection_hash': projection['projection_hash'], 'call_id': call_id,
        'capture_hash': 'a' * 64, 'response_body_sha256': 'b' * 64,
        'arguments_sha256': hashlib.sha256(machine_bytes).hexdigest()}
    if partial_origin:
        metadata['origin_mode'] = native.PARTIAL_MODE
    origin = {'metadata': metadata, 'source_context': copy.deepcopy(source), 'indexed_inputs': manifest,
        'raw_arguments': machine_bytes.decode('utf-8'), 'projection': projection, 'request_hash': 'c' * 64,
        'capture_proof': {'receipt': {'call_id': call_id, 'arguments_sha256': metadata['arguments_sha256']},
            'capture': {'capture_hash': metadata['capture_hash'], 'body_sha256': metadata['response_body_sha256']},
            'descriptor': {'test_only': 'not an actual paid capture'},
            'binding': {'draft_id': consumer_id, 'reserved_call_id': call_id,
                'actor_id': snapshot['actor_id'], 'workspace_id': snapshot['workspace_id']}},
        'job_identity': {'id': job_id, 'kind': 'read_saved_map_design_v176',
            'workspace_id': snapshot['workspace_id'], 'actor_id': snapshot['actor_id'],
            'consumer_id': consumer_id, 'request_id': request_id,
            'idempotency_key': 'saved-map-design:' + request_id}}
    document = {'schema': 'report-business-catalogue-edit-document-v211',
        'businesses': [{'key': 'ops', 'name': '用户明确调整的核对业务', 'purpose': '核对本批原要求。',
            'boundary': '只限公开样本，不编造职责。'}], 'allocation_groups': [],
        'questions': copy.deepcopy(projection['draft_seed']['questions']) if partial_origin else []}
    reference = {'id': str(uuid4()), 'workspace_id': snapshot['workspace_id'], 'actor_id': snapshot['actor_id'],
        'version': 4, 'record_hash': 'd' * 64, 'origin_hash': digest(origin), 'document_hash': digest(document)}
    plan = planning.build_plan(**arguments, input_order=snapshot['input_order'], draft_ref=reference,
        document=document, max_batch_items=1, name_origin=projection if partial_origin else None)
    kwargs = {'plan': plan, 'batch_ordinal': 0, 'document': document, 'draft_ref': reference,
        'draft_origin': origin, 'name_origin': projection if partial_origin else None, 'note': '完整保留本批原引用😀'}
    evidence = native.build_allocation_evidence(source, **kwargs)
    return cap, source, kwargs, evidence


def native_arguments(evidence):
    return {'plan': evidence['plan'], 'batch_ordinal': evidence['batch_ordinal'], 'allocation_evidence': evidence}


def prepared(cap, evidence):
    schema = wire.output_schema(evidence['plan'], evidence['batch_ordinal'])
    requested = [{'role': 'system', 'content': canonical_json(native.task_envelope())},
        {'role': 'user', 'content': canonical_json(native.payload_for(evidence))}]
    messages, _, images = prepare_cloud_json(cap, requested, schema)
    assert images == 0
    options = native.dispatch_options(cap.data['execution'], {'model': native.MODEL,
        'max_tokens': 8192, 'response_format': {'type': 'json_object'}})
    return messages, schema, options


def candidate(evidence):
    batch = evidence['plan']['batches'][evidence['batch_ordinal']]
    return {'schema': wire.WIRE, 'decisions': {key: {'disposition': 'assign', 'business_keys': ['ops'],
        'reason': '基于本条公开引用作待核对归属。', 'questions': []} for key in batch['decision_keys']}}


def response(value, finish='stop'):
    return raw({'choices': [{'finish_reason': finish, 'message': {'role': 'assistant',
        'content': json.dumps(value, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}})


def test_v213_registration_is_new_partial_scope_and_never_relabels_the_old_catalogue():
    cap, _, _, _ = scene(); execution = cap.data['execution']; before = copy.deepcopy(execution)
    policy = native.input_policy(execution)
    assert policy['full_report'] is False and policy['max_input_bytes'] == 900000
    assert policy['context_token_bound'] == 1000000 and policy['max_output_tokens'] <= 32768
    assert native.STAGE == 'catalogue-allocation-v213' and native.MODEL == 'qwen3.8-max-0902'
    assert native.OUTPUT_DECLARATION != 'report-business-catalogue-native-output-v211'
    assert execution == before
    for field, value in [('schema', 'report-business-catalogue-native-output-v211'),
                         ('reduce_transport', 'qwen-public-business-catalogue-native-v211'),
                         ('max_tokens', 32769)]:
        bad = copy.deepcopy(execution); bad['report_evidence_output'][field] = value
        with pytest.raises(ValueError): native.input_policy(bad)
    with pytest.raises(ValueError): native.stage_policy('catalogue-v211')


def test_shape_proof_is_not_source_proof_and_only_unique_items_is_removed_for_provider():
    cap, _, _, evidence = scene(); _, schema, _ = prepared(cap, evidence)
    shape = native.schema_shape_proof(schema, native.STAGE)
    actual = native.identity_schema_proof(schema, native.STAGE, **native_arguments(evidence))
    assert shape['source_binding_verified'] is False and shape['current_authority_verified'] is False
    assert actual['source_binding_verified'] is True and actual['current_authority_verified'] is False
    assert shape['schema'] != actual['schema']
    assert actual['schema_policy']['provider_support_verified'] is False
    assert actual['schema_policy']['removed_assertions']
    first = next(iter(schema['properties']['decisions']['properties']))
    assert schema['properties']['decisions']['properties'][first]['oneOf'][0]['properties']['business_keys']['uniqueItems'] is True
    provider = actual['provider_schema']['properties']['decisions']['properties'][first]['oneOf']
    assert 'uniqueItems' not in provider[0]['properties']['business_keys']
    assert 'contains' in provider[2]['properties']['questions'] and provider[2]['properties']['questions']['minItems'] == 1
    with pytest.raises(TypeError): native.identity_schema_proof(schema, native.STAGE)


def test_actual_http_has_only_exact_batch_disclosure_and_note_not_server_evidence_or_other_batches():
    cap, source, kwargs, evidence = scene(); before = canonical_json([cap.data, source, kwargs])
    messages, schema, options = prepared(cap, evidence)
    value = native.prepare_request(messages, schema, options, native.STAGE, **native_arguments(evidence))
    payload = json.loads(value['request']['messages'][-1]['content'])
    assert set(payload) == {'schema', 'disclosure', 'note'} and payload == native.payload_for(evidence)
    assert len(payload['disclosure']['claims']) == 1 and len(evidence['plan']['source']['input_claims']) > 1
    assert payload['note'] == kwargs['note']
    def keys(value):
        if isinstance(value, dict):
            yield from value
            for child in value.values(): yield from keys(child)
        elif isinstance(value, list):
            for child in value: yield from keys(child)
    assert not {'source_context', 'draft_origin', 'input_order', 'input_id', 'indexed_inputs',
        'base_document', 'allocation_evidence', 'catalog', 'user_instruction', 'context'}.intersection(keys(value['request']))
    assert value['body'] == canonical_json(value['request']).encode('utf-8')
    assert value['body_sha256'] == hashlib.sha256(value['body']).hexdigest()
    assert value['body_bytes'] <= 900000 and canonical_json([cap.data, source, kwargs]) == before


@pytest.mark.parametrize('fault', ['missing_claim', 'changed_span', 'other_batch', 'full_catalog', 'extra_system', 'changed_note'])
def test_outgoing_weakening_or_unquoted_disclosure_is_rejected_before_a_request_can_be_built(fault):
    cap, source, _, evidence = scene(); messages, schema, options = prepared(cap, evidence)
    payload = json.loads(messages[-1]['content'])
    if fault == 'missing_claim': payload['disclosure']['claims'].pop()
    elif fault == 'changed_span': payload['disclosure']['spans'][0][1] += '不在原引用里的补写'
    elif fault == 'other_batch': payload['disclosure'] = planning.batch_disclosure(evidence['plan'], 1)
    elif fault == 'full_catalog': payload['catalog'] = source['catalog']
    elif fault == 'changed_note': payload['note'] += '未经原报价核对的要求'
    else: messages.insert(2, {'role': 'system', 'content': json.dumps(source, ensure_ascii=False)})
    if fault != 'extra_system': messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
    with pytest.raises(ValueError): native.prepare_request(messages, schema, options, native.STAGE, **native_arguments(evidence))


def test_complete_and_partial_origins_cannot_skip_or_borrow_name_diagnostics_even_after_rename():
    _, _, _, evidence = scene(True)
    assert evidence['draft_origin']['metadata']['origin_mode'] == native.PARTIAL_MODE
    assert evidence['document']['businesses'][0]['name'] != evidence['name_origin']['raw_wire']['businesses'][0]['name']
    for fault in ('none', 'removed_issue', 'changed_origin', 'false_mode'):
        bad = copy.deepcopy(evidence)
        if fault == 'none': bad['name_origin'] = None
        elif fault == 'removed_issue': bad['name_origin']['issues'] = []
        elif fault == 'changed_origin': bad['draft_origin']['raw_arguments'] += ' '
        else: bad['draft_origin']['metadata'].pop('origin_mode')
        with pytest.raises(ValueError): native.check_allocation_evidence(bad)
    _, _, _, complete = scene()
    complete['name_origin'] = copy.deepcopy(evidence['name_origin'])
    with pytest.raises(ValueError): native.check_allocation_evidence(complete)


def test_embedded_plan_self_consistency_cannot_replace_the_actual_original_input_order_or_draft_origin():
    _, _, _, evidence = scene(); bad = copy.deepcopy(evidence)
    supplied = copy.deepcopy(evidence['plan']['source']); supplied['input_order'] = list(reversed(supplied['input_order']))
    bad['plan'] = planning.build_plan(**supplied, draft_ref=evidence['draft_ref'], document=evidence['document'],
        selection=evidence['plan']['selection'], max_batch_items=evidence['plan']['max_batch_items'])
    assert planning.check_embedded_plan(bad['plan']) == bad['plan']
    with pytest.raises(ValueError): native.check_allocation_evidence(bad)
    bad = copy.deepcopy(evidence); bad['draft_ref']['origin_hash'] = 'f' * 64
    with pytest.raises(ValueError): native.check_allocation_evidence(bad)


def test_complete_stop_content_yields_only_unconfirmed_new_batch_projection_and_retains_raw_wire():
    _, _, _, evidence = scene(); value = candidate(evidence)
    projection = native.parse_arguments(response(value), 200, native.STAGE,
        response_complete=True, http_content_encoding=None, **native_arguments(evidence))
    assert projection['schema'] == wire.PROJECTION and projection['raw_wire'] == value
    assert 'wire' not in projection and 'result' not in projection and 'manual_value' not in projection
    assert len(projection['decisions']) == len(value['decisions'])
    assert native.inspect_response(response(value), 200, native.STAGE,
        response_complete=True, http_content_encoding=None)['metadata']['delivery'] == native.DELIVERY
    bad = copy.deepcopy(value); bad['decisions'].pop(next(iter(bad['decisions'])))
    for body, complete in [(response(value, 'length'), True), (response(value), False), (response(bad), True)]:
        with pytest.raises(ValueError): native.parse_arguments(body, 200, native.STAGE,
            response_complete=complete, http_content_encoding=None, **native_arguments(evidence))


def test_preparation_rebuilds_the_whole_actual_http_and_checks_external_source_without_spending():
    cap, source, kwargs, _ = scene(); before = canonical_json([cap.data, source, kwargs])
    value = preparation.build_preparation(cap, source, **kwargs)
    assert set(value) == preparation._FIELDS and value['call_count'] == 1
    assert value['consumer_authorized'] is False and value['can_apply'] is False
    spec = preparation.stage_spec(value); assert spec['input_policy']['full_report'] is False
    actual = preparation.rebuild_request(value, cap, source, **kwargs)
    assert actual['request_hash'] == value['request_hash']
    assert actual['input_summary']['prepared_request_bytes'] == len(actual['request_body'])
    assert actual['input_summary']['token_bound'] == len(actual['request_body']) + spec['dispatch_options']['max_tokens'] + 1024
    assert value['output_capacity']['fit_guaranteed'] is False
    assert canonical_json([cap.data, source, kwargs]) == before
    tampered = copy.deepcopy(value); tampered['payload']['disclosure']['claims'].pop()
    tampered['payload_hash'] = digest(tampered['payload'])
    tampered['preparation_hash'] = digest({k: v for k, v in tampered.items() if k != 'preparation_hash'})
    with pytest.raises(ValueError): preparation.check_preparation(tampered, cap, source, **kwargs)


def test_output_schema_local_validation_is_strict_and_no_evidence_or_extra_stage_is_accepted():
    cap, _, _, evidence = scene(); messages, schema, options = prepared(cap, evidence)
    valid = candidate(evidence); assert not list(Draft202012Validator(schema).iter_errors(valid))
    with pytest.raises(TypeError): native.prepare_request(messages, schema, options, native.STAGE,
        plan=evidence['plan'], batch_ordinal=0)
    with pytest.raises(ValueError): native.prepare_request(messages, schema, options, 'catalogue-v211', **native_arguments(evidence))
    for key, value in [('n', True), ('stream', True), ('max_tokens', 32769), ('model', 'other')]:
        weakened = {**options, key: value}
        with pytest.raises(ValueError): native.prepare_request(messages, schema, weakened, native.STAGE, **native_arguments(evidence))
