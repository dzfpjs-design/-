"""Pure supplied-source consistency; NEVER real V174 ownership or paid evidence.

Actual V172 synthetic Map/native/projection builders provide public inputs.
The V174 snapshot below is explicitly authored in memory, not loaded from a DB.
Root independently verifies the authenticated cold source and target runtime.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from generic_agents import report_saved_map_design_preparation_v176 as subject
from generic_agents import report_design_grouped_native_v176 as native
from generic_agents import report_evidence_enum_reduction_v172 as old
from generic_agents.contracts import digest
from generic_agents.report_design_grouped_wire_v174 import DesignGroupedWireV174
from generic_agents.report_evidence_plan import EvidenceCapacityExceeded
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_evidence_enum_reduction_v172 import scene as map_scene, identity


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure preparation cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def seal(snapshot):
    snapshot['snapshot_hash'] = digest({k: v for k, v in snapshot.items() if k != 'snapshot_hash'})


def source_scene():
    original_cap, catalog, map_preparation, maps = map_scene()
    inputs = old.reduction_inputs_v3(map_preparation, catalog, original_cap, maps)
    actor, workspace, producer = identity(1), identity(2), identity(3)
    ledger = lambda call_id, status: {'id': call_id, 'workspace_id': workspace,
        'capability_id': identity(4), 'status': status, 'reserved_tokens': 100,
        'charged_tokens': 100, 'data_hash': digest({'synthetic': call_id}),
        'receipt_hash': digest({'synthetic_receipt': call_id})}
    job = lambda job_id, status, key: {'id': job_id, 'kind': 'read_report_draft',
        'status': status, 'idempotency_key': key, 'data_hash': digest({'synthetic_job': job_id})}
    failure_binding = {'schema': 'report-evidence-journal-binding-v2', 'actor_id': actor,
        'workspace_id': workspace, 'draft_id': producer, 'stage': 'reduce',
        'reserved_call_id': identity(90), 'source_hash': inputs['source_hash'],
        'catalog_hash': inputs['catalog_hash'], 'generation_hash': 'a' * 64,
        'dispatch_hash': 'b' * 64, 'messages_hash': 'c' * 64}
    value = {'schema': 'report-design-saved-source-v174', 'scope': 'all-successful-v172-maps-only',
        'actor_id': actor, 'workspace_id': workspace,
        'producer': {'id': producer, 'revision': 6, 'status': 'failed', 'phase': 'failed',
            'request_id': 'fe_' + identity(6), 'state_hash': 'd' * 64,
            'start_command_hash': 'e' * 64, 'start_receipt_hash': 'f' * 64},
        'source_hash': inputs['source_hash'], 'catalog_hash': inputs['catalog_hash'],
        'catalog_descriptor': copy.deepcopy(catalog['descriptor']),
        'source_snapshot_hash': digest({'synthetic_original': True}), 'materials': [],
        'context': copy.deepcopy(inputs['context']), 'context_hash': digest(inputs['context']),
        'user_instruction': inputs['user_instruction'], 'user_instruction_hash': digest(inputs['user_instruction']),
        'producer_model': {'capability_id': identity(4), 'published_model_hash': 'b' * 64,
            'provider': 'qwen', 'model': native.MODEL, 'preparation_cap': copy.deepcopy(original_cap.data)},
        'map_preparation_hash': inputs['map_preparation_hash'], 'map_plan_hash': inputs['map_plan_hash'],
        'map_generation_hash': digest(map_preparation['generation_contract']),
        'map_proofs': copy.deepcopy(inputs['map_proofs']), 'map_inputs_hash': inputs['map_inputs_hash'],
        'map_calls': [{'chunk_id': item['binding']['stage'], 'binding': copy.deepcopy(item['binding']),
            'cloud_call': ledger(item['binding']['reserved_call_id'], 'succeeded')} for item in maps],
        'map_job': job(identity(7), 'succeeded', 'report-evidence:fe_' + identity(6)),
        'input_order': copy.deepcopy(inputs['input_order']), 'input_order_hash': digest(inputs['input_order']),
        'input_items': copy.deepcopy(inputs['input_items']), 'input_items_hash': inputs['input_items_hash'],
        'input_claims_hash': inputs['input_claims_hash'], 'claim_count': len(inputs['input_order']),
        'allowed_span_refs': copy.deepcopy(inputs['allowed_span_refs']),
        'allowed_span_refs_hash': digest(inputs['allowed_span_refs']), 'spans': copy.deepcopy(inputs['spans']),
        'known_producer_failure': {'binding': failure_binding, 'journal_descriptor_hash': 'a' * 64,
            'capture_hash': 'b' * 64, 'http_body_sha256': 'c' * 64, 'http_body_bytes': 123,
            'receipt_hash': 'd' * 64, 'http_status': 200, 'finish_reason': 'length',
            'response_complete': True, 'original_inflight': True, 'arguments_adopted': False,
            'cloud_call': ledger(identity(90), 'failed'),
            'job': job(identity(91), 'failed', 'evidence-reduce:fer_' + identity(92))},
        'original_reduction_adopted': False, 'semantic_quality_verified': False,
        'consumer_authorized': False, 'can_apply': False, 'can_publish': False}
    seal(value)
    context = {'snapshot': value, 'catalog': catalog, 'context': copy.deepcopy(inputs['context']),
               'allowed_span_refs': copy.deepcopy(inputs['allowed_span_refs'])}
    cap = SimpleNamespace(data={'execution': {'provider': 'qwen', 'model': native.MODEL,
        'max_tokens': 8192, 'report_evidence_output': {'schema': native.OUTPUT_DECLARATION,
            'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}},
        'data_policy': {'redact_fields': []}})
    return cap, context


def test_new_preparation_and_actual_body_are_one_call_and_never_authorization():
    cap, context = source_scene(); before = canonical_json([cap.data, context])
    value = subject.build_preparation(cap, context, note='原声明仍待人工核实。')
    assert value['schema'] == subject.PREPARATION
    assert value['source_snapshot_hash'] == context['snapshot']['snapshot_hash']
    assert value['source_snapshot_hash'] != context['snapshot']['source_snapshot_hash']
    spec = subject.stage_spec(value)
    assert spec['schema'] == subject.GENERATION and spec['evidence_stage'] == native.STAGE
    assert spec['output_schema'] == DesignGroupedWireV174.model_json_schema()
    assert spec['schema_proof'] == native.identity_schema_proof(spec['output_schema'], native.STAGE)
    request = subject.rebuild_request(value, cap, context, note='原声明仍待人工核实。')
    assert request['request_body'] == canonical_json(request['request']).encode('utf-8')
    assert request['request_hash'] == hashlib.sha256(request['request_body']).hexdigest() == value['request_hash']
    assert request['messages_hash'] == value['messages_hash'] != value['request_hash']
    summary = value['input_summary']
    assert summary['prepared_request_bytes'] == len(request['request_body'])
    assert summary['token_bound'] == len(request['request_body']) + 32768 + 1024
    assert value['call_count'] == 1 and value['requires_separate_authorization'] is True
    for key in ('semantic_quality_verified', 'consumer_authorized', 'can_apply', 'can_publish'):
        assert value[key] is False
    assert canonical_json([cap.data, context]) == before


def test_all_saved_claims_duplicates_original_instruction_and_only_cited_spans_are_kept():
    cap, context = source_scene(); snapshot = context['snapshot']
    value = subject.build_preparation(cap, context, note=' 新指示是资料而非权限。 ')
    payload = value['payload']
    ordered = [snapshot['input_items'][key] for key in snapshot['input_order']]
    assert payload['input_claims'] == ordered and len(ordered) == 4
    assert ordered[0] == ordered[1] and len(value['input_order']) == 4
    assert payload['spans'] == snapshot['spans']
    assert '未被原声明引用的旁支' not in json.dumps(payload, ensure_ascii=False)
    assert payload['context']['counter'] == 1.0
    assert canonical_json(payload['context']) == canonical_json(snapshot['context'])
    assert payload['user_instruction'] == snapshot['user_instruction']
    assert payload['design_note'] == ' 新指示是资料而非权限。 '
    assert 'map_preparation' not in value and 'maps' not in value
    assert value['output_capacity']['required_input_positions'] == 4
    assert value['output_capacity']['fit_guaranteed'] is False


@pytest.mark.parametrize('fault', ['extra_context', 'missing_context', 'snapshot_hash',
    'typed_context', 'missing_item', 'bad_ref', 'source_flags', 'failed_finish', 'map_scope'])
def test_malformed_or_inconsistent_supplied_source_is_rejected_without_repair(fault):
    cap, context = source_scene(); value = context['snapshot']
    if fault == 'extra_context': context['consumer_authorized'] = True
    elif fault == 'missing_context': context.pop('catalog')
    elif fault == 'snapshot_hash': value['snapshot_hash'] = '0' * 64
    elif fault == 'typed_context':
        value['context']['counter'] = 1; value['context_hash'] = digest(value['context']); seal(value)
    elif fault == 'missing_item':
        value['input_items'].pop(value['input_order'][0]); seal(value)
    elif fault == 'bad_ref':
        context['allowed_span_refs'].append('w:p1/s999'); value['allowed_span_refs'] = copy.deepcopy(context['allowed_span_refs'])
        value['allowed_span_refs_hash'] = digest(value['allowed_span_refs']); seal(value)
    elif fault == 'source_flags': value['consumer_authorized'] = True; seal(value)
    elif fault == 'failed_finish': value['known_producer_failure']['finish_reason'] = 'stop'; seal(value)
    else: value['map_calls'][0]['binding']['workspace_id'] = identity(999); seal(value)
    before = canonical_json([cap.data, context])
    with pytest.raises(ValueError): subject.build_preparation(cap, context)
    assert canonical_json([cap.data, context]) == before


@pytest.mark.parametrize('fault', ['payload', 'schema', 'body_hash', 'count', 'authority'])
def test_resealed_preparation_cannot_replace_a_cold_rebuild(fault):
    cap, context = source_scene(); value = subject.build_preparation(cap, context)
    if fault == 'payload': value['payload']['input_claims'].pop()
    elif fault == 'schema': value['generation_contract']['output_schema']['properties'].pop('reconciliation_groups')
    elif fault == 'body_hash': value['request_hash'] = '0' * 64
    elif fault == 'count': value['call_count'] = 2
    else: value['can_apply'] = True
    value['payload_hash'] = digest(value['payload'])
    value['generation_contract_hash'] = digest(value['generation_contract'])
    value['preparation_hash'] = digest({k: v for k, v in value.items() if k != 'preparation_hash'})
    with pytest.raises(ValueError): subject.check_preparation(value, cap, context)


def test_new_note_current_model_or_cold_snapshot_change_require_a_new_preview():
    cap, context = source_scene(); value = subject.build_preparation(cap, context, note='原说明')
    with pytest.raises(ValueError): subject.check_preparation(value, cap, context, note='修改说明')
    changed_cap = copy.deepcopy(cap); changed_cap.data['execution']['report_evidence_output']['max_tokens'] = 16384
    with pytest.raises(ValueError): subject.check_preparation(value, changed_cap, context, note='原说明')
    changed = copy.deepcopy(context); changed['snapshot']['producer']['revision'] += 1; seal(changed['snapshot'])
    with pytest.raises(ValueError): subject.check_preparation(value, cap, changed, note='原说明')


def test_actual_gateway_redaction_cannot_silently_change_frozen_source_text():
    cap, context = source_scene()
    cap.data['data_policy']['redact_fields'] = ['statement']
    with pytest.raises(ValueError): subject.build_preparation(cap, context)


def test_exact_whole_body_capacity_fails_instead_of_splitting_or_trimming(monkeypatch):
    cap, context = source_scene(); before = canonical_json([cap.data, context])
    value = subject.build_preparation(cap, context)
    monkeypatch.setattr(subject, 'MAX_REQUEST_BYTES', value['input_summary']['prepared_request_bytes'] - 1)
    with pytest.raises(EvidenceCapacityExceeded): subject.build_preparation(cap, context)
    assert canonical_json([cap.data, context]) == before


def test_mutable_results_do_not_alias_supplied_source_or_each_other():
    cap, context = source_scene(); before = canonical_json([cap.data, context])
    value = subject.build_preparation(cap, context)
    spec = subject.stage_spec(value)
    checked = subject.check_preparation(value, cap, context)
    rebuilt = subject.rebuild_request(value, cap, context)
    spec['input_policy']['max_input_bytes'] = 1
    checked['payload']['context']['counter'] = 99
    rebuilt['request']['messages'].clear()
    assert value['generation_contract']['input_policy']['max_input_bytes'] == 900000
    assert value['payload']['context']['counter'] == 1.0
    assert canonical_json([cap.data, context]) == before
