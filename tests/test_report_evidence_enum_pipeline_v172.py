"""Synthetic kernel/real temporary-FS integration, NOT API/PG/provider proof.

The row and call IDs are public test inputs, not fabricated durable business
records. Capture, native envelope/receipt, Journal, projection and preparation
functions run for real. Only the frozen-catalog input and explicitly recorded
DB settlement/audit/checkpoint boundaries are substituted. Root owns execution.
"""
import copy
import hashlib
import os
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_enum_pipeline_v172 as family
from generic_agents import report_evidence_enum_reduction_v172 as reducer
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_claim_enum_native_v172 as native
from generic_agents.contracts import digest
from generic_agents.report_object_semantics_v166 import CANDIDATE, SCHEMA
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_evidence_enum_reduction_v172 import scene, identity


def raw(value):
    return canonical_json(value).encode('utf-8')


def files(root):
    return {str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob('*') if path.is_file()}


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Kernel/FS tests must never invoke a provider or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(pipeline, 'cloud_json', forbidden)


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    def create(*, empty=False):
        cap, catalog, prep, examples = scene(empty=empty)
        # Discard scene's synthetic descriptor/projection/binding. Every binding
        # and every durable artifact below is built by the actual pipeline/FS.
        wires = {chunk['chunk_id']: copy.deepcopy(item['wire'])
            for chunk, item in zip(prep['plan']['chunks'], examples)}
        preview = {'source_hash': catalog['descriptor']['source_hash'],
            'preparation_cap': copy.deepcopy(cap.data), 'published_model_hash': digest(cap.data),
            'body': {'model_id': identity(4)}, 'map_preparation': copy.deepcopy(prep),
            'provider': 'qwen', 'model': native.MODEL}
        stages = []
        for index, chunk in enumerate(prep['plan']['chunks']):
            stage = pipeline._empty_stage('running', chunk_id=chunk['chunk_id'])
            stage.update(reserved_call_id=identity(100 + index), inflight=True)
            stages.append(stage)
        row = NS(id=identity(3), actor_id=identity(1), workspace_id=identity(2),
            version=1, status='running', data={'preview': preview, 'maps': stages,
                'reduction': pipeline._empty_stage('not_started', preview=None, start_receipt=None),
                'phase': 'map', 'model_result_hash': None, 'review_value': None,
                'review_history': [], 'recovery_history': [], 'resume_history': [],
                'error': None, 'error_code': None})
        principal = NS(user_id=row.actor_id, workspace_id=row.workspace_id, role='operator')
        captures, journal1, journal2 = tmp_path / 'captures', tmp_path / 'map-journal', tmp_path / 'reduce-journal'
        events, settlements, checkpoints = [], [], []
        monkeypatch.setattr(bridge, 'root', lambda: captures)
        monkeypatch.setattr(pipeline, '_journal_root', lambda actual=None, key=None:
            journal2 if key == 'reduce' else journal1)

        def frozen_catalog(actual):
            assert actual['source_hash'] == catalog['descriptor']['source_hash']
            assert actual['map_preparation']['catalog_descriptor'] == catalog['descriptor']
            return copy.deepcopy(catalog)

        def settlement(db, actor, actual, key, receipt, valid):
            assert actual is row and actor is principal and valid is True
            assert receipt['call_id'] == pipeline._stage(row, key)['reserved_call_id']
            assert receipt['provider'] == 'qwen' and receipt['model'] == native.MODEL
            settlements.append((key, receipt['call_id'], copy.deepcopy(receipt)))

        monkeypatch.setattr(pipeline, '_catalog', frozen_catalog)
        monkeypatch.setattr(pipeline, '_settle', settlement)
        monkeypatch.setattr(pipeline, 'audit', lambda db, actor, action, object_id, data:
            events.append((action, object_id, copy.deepcopy(data))))
        monkeypatch.setattr(pipeline, 'checkpoint', lambda db: checkpoints.append(row.version))

        def record(key, value, *, mode='complete', publish=True):
            binding = pipeline.journal_binding(row, key, pipeline._stage(row, key)['reserved_call_id'])
            ctx = bridge.context(pipeline, row, key)
            assert ctx['binding'] == binding
            body = raw({'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': raw(value).decode('utf-8')}}],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 12, 'total_tokens': 20}})
            with capture.prepare(captures, ctx) as sink:
                sink.mark_request_started()
                if mode != 'started-only':
                    sink.begin_response(200, None, 'synthetic-original-' + key)
                    for offset in range(0, len(body), 4096):
                        sink.append(body[offset:offset + 4096])
                    if mode == 'complete':
                        sink.finish(eof=True)
                # A partial case deliberately has a whole valid JSON body but
                # NO original EOF. Closing must not manufacture completion.
            state = capture.load(captures, ctx)
            result = NS(body=body, context=ctx, binding=binding, state=state)
            if mode == 'complete':
                policy = copy.deepcopy(pipeline._spec(row, key)['input_policy'])
                assert policy.pop('single_batch') is True
                original, receipt, checked = bridge.response_context(ctx, 'qwen', native.MODEL,
                    policy, family._stage_name(key, row.data['preview']))
                assert original.encode('utf-8') == body and checked == state
                result.original, result.receipt = original, receipt
                if publish:
                    adapter = pipeline._journal_for(row, key)
                    descriptor = adapter.write_response(pipeline._stage_journal_root(row, key),
                        binding, original, receipt)
                    pipeline._stage(row, key)['journal'] = descriptor
                    result.descriptor = descriptor
            return result

        def consume_maps():
            artifacts = {}
            for key, value in wires.items():
                artifacts[key] = record(key, value)
                pipeline._consume(object(), principal, row, key)
            pipeline._finish_maps(object(), principal, row)
            return artifacts

        return NS(row=row, principal=principal, cap=cap, catalog=catalog, preparation=prep,
            wires=wires, record=record, consume_maps=consume_maps, captures=captures,
            journal1=journal1, journal2=journal2, events=events, settlements=settlements,
            checkpoints=checkpoints, root=tmp_path)
    return create


def candidate(inputs):
    # This entire candidate is synthetic. Its workflow completeness and optional
    # fields exercise the kernel; they do not claim source entailment or quality.
    result = {'schema': CANDIDATE, 'source_hash': inputs['source_hash'],
        'catalog_hash': inputs['catalog_hash'], 'domain': '公开合成接线样本',
        'global_claims': copy.deepcopy(inputs['input_claims']),
        'objects': [{'name': '合成核对条目', 'fields': [{'label': '数量', 'kind': '整数',
            'required': False, 'unit': None, 'citations': [], 'inference_reason': '仅为接线结构假设。'}],
            'citations': [], 'inference_reason': '仅为接线结构假设，不是原文质量结论。',
            'business_identity': {'status': 'not_established', 'field_labels': [], 'citations': [],
                'explanation': '合成条目的业务标识尚未明确。'}}],
        'relationships': [], 'businesses': [{'operation': 'upsert', 'original_name': '',
            'name': '合成人工核对', 'trigger': '提出核对', 'output': '核对意见',
            'steps': [{'name': '核对', 'purpose': '检查合成记录', 'agent': '人工确认',
                'role': '核查人', 'output': '核对意见', 'next_index': None, 'branch_condition': '',
                'else_index': None, 'citations': [], 'inference_reason': '仅合成结构，不授予权限。'}],
            'citations': [], 'inference_reason': '只用于测试内核消费，不是原文完整理解。',
            'participants': [], 'interactions': [], 'conditions': [], 'coordination': []}],
        'questions': ['原文语义与实际权限仍须独立核对。']}
    return {'result': result, 'reconciliation': [{'input_position': index,
        'disposition': 'kept', 'output_kind': 'global_claims', 'output_index': index,
        'reason': '完整保留这个合成原声明及其全部引用。'} for index in range(len(inputs['input_order']))]}


def test_capture_journal_kernel_reduce_and_optional_human_value_use_actual_new_family(kernel):
    case = kernel(); original_wires = copy.deepcopy(case.wires)
    assert pipeline._family(case.row.data['preview']) is family
    artifacts = case.consume_maps()
    assert case.row.status == 'awaiting_merge'
    maps = family.map_inputs(pipeline, case.row, case.catalog)
    for item, stage in zip(maps, case.row.data['maps']):
        actual = artifacts[stage['chunk_id']]
        assert item['binding'] == pipeline.journal_binding(case.row, stage['chunk_id'], stage['reserved_call_id'])
        assert item['descriptor'] == actual.descriptor and item['http_body'].encode('utf-8') == actual.body
        assert item['metadata']['response_body_sha256'] == hashlib.sha256(actual.body).hexdigest()
        assert item['metadata']['arguments_sha256'] != item['metadata']['response_body_sha256']
        assert item['wire'] == original_wires[stage['chunk_id']]
        assert stage['result_hash'] == digest(item['projection']) and stage['status'] == 'succeeded'
    prepared = reducer.build_reduction_preparation(case.cap, case.preparation, case.catalog, maps)
    inputs = reducer.reduction_inputs_v3(case.preparation, case.catalog, case.cap, maps)
    value = candidate(inputs)
    case.row.data['reduction'] = pipeline._empty_stage('running',
        preview={'reduction_preparation': prepared}, start_receipt=None)
    case.row.data['reduction'].update(reserved_call_id=identity(199), inflight=True)
    case.row.data['phase'] = 'reduce'; case.row.status = 'running'
    original = case.record('reduce', value)
    assert pipeline._journal_for(case.row, 'reduce')._DESCRIPTOR.endswith('-v2')
    pipeline._consume(object(), case.principal, case.row, 'reduce')
    proof = family.stage_projection(pipeline, case.row, 'reduce', case.catalog)
    assert proof['result'] == value['result'] and proof['reconciliation'] == value['reconciliation']
    assert proof['http_body_sha256'] == hashlib.sha256(original.body).hexdigest()
    assert case.row.data['model_result_hash'] == digest(value['result'])
    assert case.row.status == 'review' and case.row.data['reduction']['status'] == 'succeeded'
    manual = family.manual_value_from_candidate(proof['result'], case.catalog,
        inputs['allowed_span_refs'], inputs['context'])
    assert manual['schema'] == SCHEMA and manual['result'] == value['result']
    assert manual['result']['objects'][0]['fields'][0]['required'] is False
    assert manual['result']['objects'][0]['fields'][0]['kind'] == '整数'
    assert all(item['acknowledge_platform_identity'] is False for item in manual['object_identity_confirmations'])
    assert proof['reduction_projection_metadata']['semantic_quality_verified'] is False
    assert [key for key, _, _ in case.settlements] == [*case.wires, 'reduce']


@pytest.mark.parametrize('mode', ['partial', 'started-only'])
def test_partial_or_unknown_original_capture_cannot_be_parsed_or_automatically_repaid(kernel, mode):
    case = kernel(); key = next(iter(case.wires))
    artifact = case.record(key, case.wires[key], mode=mode, publish=False)
    before_files, before_row = files(case.root), copy.deepcopy(case.row.data)
    assert artifact.state['ready'] is False
    with pytest.raises(ValueError): pipeline._native_preflight(case.row, key)
    with pytest.raises(HTTPException) as error:
        pipeline._run_stage(object(), case.principal, case.row, key)
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'evidence_response_unknown'
    assert case.row.data == before_row and files(case.root) == before_files
    assert not case.settlements and not case.events and not case.checkpoints


def test_every_empty_map_is_saved_but_never_creates_or_prepares_a_reduce_call(kernel):
    case = kernel(empty=True); case.consume_maps()
    assert all(stage['status'] == 'succeeded' and stage['result_count'] == 0 for stage in case.row.data['maps'])
    assert case.row.status == 'needs_clarification' and case.row.data['model_result_hash'] is None
    assert case.row.data['reduction']['preview'] is None and case.row.data['reduction']['reserved_call_id'] is None
    maps = family.map_inputs(pipeline, case.row, case.catalog)
    before = files(case.root)
    with pytest.raises(ValueError):
        reducer.build_reduction_preparation(case.cap, case.preparation, case.catalog, maps)
    assert files(case.root) == before
    assert len(case.settlements) == len(case.wires)
    assert case.events[-1][2]['new_reduce_call'] is False


@pytest.mark.parametrize('fault', ['actor', 'workspace', 'model', 'source'])
def test_actual_journal_binding_rejects_a_different_scope_without_rewriting_original_files(kernel, fault):
    case = kernel(); key = next(iter(case.wires))
    case.record(key, case.wires[key]); before = files(case.root)
    if fault == 'actor': case.row.actor_id = identity(501)
    elif fault == 'workspace': case.row.workspace_id = identity(502)
    elif fault == 'model': case.row.data['preview']['body']['model_id'] = identity(503)
    else: case.row.data['preview']['source_hash'] = 'f' * 64
    with pytest.raises((ValueError, HTTPException)):
        family.stage_projection(pipeline, case.row, key, case.catalog)
    assert files(case.root) == before and not case.settlements


def make_half_journal(case, key, original, *, conflict=False):
    adapter = pipeline._journal_for(case.row, key)
    binding, expected, descriptor = bridge._expected(adapter, original.binding, original.original, original.receipt)
    root = pipeline._stage_journal_root(case.row, key)
    with adapter._stage(root, binding, True) as stage:
        adapter._new_file(stage, 'slot.json', adapter._json_bytes(adapter._slot_value(binding)))
        directory = adapter._directory(stage, binding['reserved_call_id'], True)
        try:
            adapter._new_file(directory, 'wire.bin', expected['wire.bin'] + (b' ' if conflict else b''))
        finally:
            os.close(directory)
    return descriptor


def test_complete_capture_can_fill_only_missing_same_call_journal_and_reconsume_without_repay(kernel):
    case = kernel(); key = next(iter(case.wires))
    original = case.record(key, case.wires[key], publish=False)
    descriptor = make_half_journal(case, key, original)
    existing = files(case.root)
    preflight = pipeline._native_preflight(case.row, key)
    assert preflight[3] == descriptor and files(case.root) == existing and not case.settlements
    adapter = pipeline._journal_for(case.row, key)
    written = bridge.write_missing_journal(adapter, pipeline._stage_journal_root(case.row, key),
        original.binding, original.original, original.receipt)
    assert written == descriptor
    assert all(files(case.root)[name] == content for name, content in existing.items())
    pipeline._consume(object(), case.principal, case.row, key)
    assert pipeline._stage(case.row, key)['reserved_call_id'] == original.binding['reserved_call_id']
    assert pipeline._stage(case.row, key)['status'] == 'succeeded'
    assert len(case.settlements) == 1
    before_files, before_row, before_events = files(case.root), copy.deepcopy(case.row.data), copy.deepcopy(case.events)
    pipeline._run_stage(object(), case.principal, case.row, key)
    assert case.row.data == before_row and files(case.root) == before_files and case.events == before_events
    assert len(case.settlements) == 1


def test_conflicting_half_journal_is_never_overwritten_or_settled_by_native_preflight(kernel):
    case = kernel(); key = next(iter(case.wires))
    original = case.record(key, case.wires[key], publish=False)
    make_half_journal(case, key, original, conflict=True)
    before = files(case.root)
    with pytest.raises(ValueError): pipeline._native_preflight(case.row, key)
    with pytest.raises(ValueError):
        bridge.write_missing_journal(pipeline._journal_for(case.row, key),
            pipeline._stage_journal_root(case.row, key), original.binding, original.original, original.receipt)
    assert files(case.root) == before and not case.settlements and not case.events


@pytest.mark.parametrize('fault', ['unknown-delivery', 'wrong-endpoint', 'old-endpoint'])
def test_capture_accepts_only_exact_enum_transport_endpoint_and_keeps_original_slot(kernel, fault):
    case = kernel(); key = next(iter(case.wires))
    original = case.record(key, case.wires[key])
    context = bridge.context(pipeline, case.row, key)
    assert context['delivery'] == 'qwen-public-claim-enum-native-v172'
    assert context['endpoint'] == native.ENDPOINT
    assert capture._context(context) == context
    before = files(case.root); invalid = copy.deepcopy(context)
    if fault == 'unknown-delivery': invalid['delivery'] += '-unknown'
    elif fault == 'wrong-endpoint': invalid['endpoint'] = native.ENDPOINT + '/extra'
    else: invalid['endpoint'] = capture.ENDPOINT
    with pytest.raises(capture.CaptureError, match='capture_context_invalid'):
        capture.load(case.captures, invalid)
    assert files(case.root) == before
    assert capture.load(case.captures, context)['ready'] is True
    assert original.body in before.values()

