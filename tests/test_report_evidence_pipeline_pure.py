"""Pure synthetic evidence workflow contracts; not paid/PG/runtime evidence."""
import copy
from datetime import datetime, timezone
import json
import socket
import subprocess
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_preparation as preparation
from generic_agents.contracts import digest
from generic_agents.security import Principal
from tests.test_report_full_reading_source_pure import snapshot, identity


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('pure contracts cannot connect, dispatch or launch')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(pipeline, 'cloud_json', forbidden)
    monkeypatch.setattr(pipeline, '_journal_root', lambda: tmp_path.resolve() / 'journal')


def scene():
    # The shared source-only helper intentionally reuses MaterialRef objects.
    # Persisted JSONB (and the production freezer's independently built refs)
    # has value equality without Python container aliases. Keep the original
    # strict state guard; give this fixture that actual JSON representation.
    source = json.loads(json.dumps(snapshot(), ensure_ascii=False, allow_nan=False))
    p = Principal(identity(11), identity(3), 'operator', '')
    data = {'execution': {'provider': 'qwen', 'model': 'qwen-plus', 'max_tokens': 8192,
        'secret_ref': 'not-exposed-in-preview', 'endpoint': 'https://not-sent.invalid'},
        'data_policy': {'redact_fields': [], 'cloud_allowed': True}, 'private_extra': 'not-exposed'}
    cap = NS(data=data, content_hash=digest(data))
    body = pipeline.PreviewCommand(request_id='fe_' + str(uuid4()), revision=1, model_id=identity(12),
        material_refs=[source['selection']['original_ref']], selection=source['selection'], note='仅合成范围。')
    # Legacy keyed-result fixtures stay explicitly V2 after the API defaults to V3.
    value = pipeline.build_preview(p, body, {'businesses': ['原业务']}, cap, source,
        generation_version=preparation.GENERATION_V2)
    command = pipeline.StartCommand(**body.model_dump(mode='json'), preview_hash=value['preview_hash'], consent=True)
    row = NS(id=identity(30), actor_id=p.user_id, workspace_id=p.workspace_id, request_id=body.request_id,
        version=1, status='queued', created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    row.data = pipeline._new_data(p, row.id, command, value, identity(31))
    return p, cap, body, row


def map_value(row, key):
    ref = pipeline._authorized(row, key)['span_refs'][0]
    return {'schema': 'report-evidence-map-v1', 'claims': [{'key': 'claim_1', 'kind': '职责',
        'statement': '仅合成声明；引用存在不证明解释正确。', 'span_refs': [ref], 'status': 'explicit', 'explanation': ''}]}


def store(row, key, value, *, succeeded=True):
    stage = pipeline._stage(row, key); call_id = str(uuid4()); stage['reserved_call_id'] = call_id
    receipt = {'provider': row.data['preview']['provider'], 'model': row.data['preview']['model'],
        'call_id': call_id, 'request_id': None, 'usage': {'total_tokens': 5}}
    descriptor = pipeline.journal.write_response(pipeline._journal_root(), pipeline.journal_binding(row, key, call_id),
        json.dumps(value, ensure_ascii=False), receipt)
    stage['journal'] = descriptor
    if succeeded:
        count = len(value['claims']) if key != 'reduce' else sum(len(value['result'][kind]) for kind in
            ('global_claims', 'objects', 'relationships', 'businesses', 'questions'))
        stage.update(status='succeeded', result_hash=digest(value), result_count=count, inflight=False)
    row.version += 1
    return descriptor


def mapped_row():
    p, cap, body, row = scene()
    for stage in row.data['maps']:
        store(row, stage['chunk_id'], map_value(row, stage['chunk_id']))
    row.status = 'awaiting_merge'; row.data['phase'] = 'awaiting_reduce'
    return p, cap, body, row


def reduced_row():
    p, cap, body, row = mapped_row()
    command = pipeline.ReducePreviewCommand(revision=row.version, request_id='fer_' + str(uuid4()))
    preview = pipeline.reduce_preview(row, p, command)
    start = pipeline.ReduceCommand(**command.model_dump(mode='json'), preview_hash=preview['preview_hash'], consent=True)
    row.data['reduction'].update(preview=preview, start_receipt=pipeline._reduce_receipt(row, p, start, preview))
    row.version += 1
    prepared = preview['reduction_preparation']; items = prepared['payload']['input_items']; claims, entries = [], []
    for number, (key, item) in enumerate(items.items()):
        claims.append({**copy.deepcopy(item), 'key': f'global_{number}'})
        entries.append({'input_id': key, 'disposition': 'kept', 'output_kind': 'global_claims',
            'output_index': number, 'reason': '合成协议完整保留原声明与引用。'})
    result = {'schema': 'report-evidence-candidate-v4', 'domain': '合成私人候选',
        'source_hash': row.data['preview']['source_hash'],
        'catalog_hash': row.data['preview']['map_preparation']['catalog_descriptor']['catalog_hash'],
        'global_claims': claims, 'objects': [], 'relationships': [], 'businesses': [],
        'questions': ['结构与字面证据不代表真实质量通过。']}
    merged = {'result': result, 'reconciliation': entries}
    store(row, 'reduce', merged); row.status = 'needs_clarification'; row.data['phase'] = 'review'
    row.data['model_result_hash'] = digest(result)
    return p, cap, body, row


def test_compact_preview_uses_all_four_column_projection_text_and_no_cap_secrets():
    p, _, _, row = scene(); value = row.data['preview']
    assert pipeline.check_preview(value, p) == value
    projection = pipeline.scrub_tree(pipeline.project_full_source(value['source_snapshot'])[0])
    catalog = pipeline._catalog(value)
    assert catalog['descriptor']['source_blocks_hash'] == digest([[block[0], block[3]] for block in projection['blocks']])
    assert catalog['descriptor']['ordered_source_refs'] == ['w:p1', 'w:p2', 'o:p1', 'o:p2']
    assert 'source_snapshot' not in value['map_preparation']
    assert 'payload' not in value and all('payload' not in chunk for chunk in value['map_preparation']['plan']['chunks'])
    assert set(value['preparation_cap']) == {'execution', 'data_policy'}
    assert all(secret not in json.dumps(value) for secret in ['not-exposed-in-preview', 'not-sent.invalid', 'not-exposed'])
    assert value['can_apply'] is value['can_publish'] is False


def test_immutable_privacy_v1_does_not_follow_future_legacy_filter(monkeypatch):
    p, _, _, row = scene(); original = copy.deepcopy(row.data['preview'])
    monkeypatch.setattr(pipeline.legacy.old(), 'scrub_tree', lambda value: {'changed': True})
    monkeypatch.setattr(pipeline.legacy.old(), 'scrub', lambda value: 'changed')
    assert pipeline.check_preview(original, p) == original
    assert pipeline.scrub('contact a@example.com token=hidden-value') == 'contact [邮箱已移除] [凭证已移除]'


def test_original_model_hash_is_independent_of_minimal_preparation_view():
    p, cap, body, row = scene(); changed = copy.deepcopy(cap.data); changed['private_extra'] = 'new unpublished revision'
    other = NS(data=changed, content_hash=digest(changed))
    second = pipeline.build_preview(p, body, {'businesses': ['原业务']}, other, row.data['preview']['source_snapshot'],
        generation_version=preparation.GENERATION_V2)
    assert second['map_preparation'] == row.data['preview']['map_preparation']
    assert second['published_model_hash'] != row.data['preview']['published_model_hash']
    assert second['preview_hash'] != row.data['preview']['preview_hash']


@pytest.mark.parametrize('mutate', [lambda v: v.update(published_model_hash='no'),
    lambda v: v['preparation_cap']['execution'].update(secret_ref='hidden'),
    lambda v: v['map_preparation']['plan']['chunks'][0]['span_refs'].pop(),
    lambda v: v.update(can_apply=True), lambda v: v.update(source_hash='f' * 64)])
def test_preview_outer_rehash_does_not_allow_changed_plan_source_or_authority(mutate):
    p, _, _, row = scene(); value = row.data['preview']; mutate(value)
    value['preview_hash'] = digest({key: item for key, item in value.items() if key != 'preview_hash'})
    with pytest.raises(HTTPException) as error:
        pipeline.check_preview(value, p)
    assert error.value.detail['code'] == 'evidence_preview_integrity'


@pytest.mark.parametrize('command', [pipeline.StartCommand, pipeline.ReduceCommand, pipeline.ResumeCommand])
@pytest.mark.parametrize('consent', [1, 'true', False, None])
def test_every_paid_action_requires_literal_true(command, consent):
    _, _, body, row = scene()
    fields = body.model_dump(mode='json') if command is pipeline.StartCommand else {
        'revision': row.version, 'request_id': ('fer_' if command is pipeline.ReduceCommand else 'fes_') + str(uuid4())}
    with pytest.raises(ValidationError):
        command(**fields, preview_hash='a' * 64, consent=consent)


def test_stage_results_and_full_v4_are_derived_from_journal_not_duplicated_in_state():
    p, _, _, row = reduced_row(); original = copy.deepcopy(row.data)
    view = pipeline.draft_view(row, p)
    assert all(stage['result']['claims'] for stage in view['maps'])
    assert view['model_result']['global_claims'] and view['reduction']['reconciliation']
    assert 'model_result' not in row.data and all('result' not in stage for stage in row.data['maps'])
    assert row.data == original and pipeline.check_row(row, p) is row
    assert view['can_apply'] is view['can_publish'] is False


@pytest.mark.parametrize('change', ['map_hash', 'map_count', 'final_hash', 'journal_binding', 'raw_wire', 'reserved_type'])
def test_readback_fails_closed_on_mutated_original_binding_or_result(change):
    p, _, _, row = reduced_row()
    if change == 'map_hash': row.data['maps'][0]['result_hash'] = 'f' * 64
    if change == 'map_count': row.data['maps'][0]['result_count'] += 1
    if change == 'final_hash': row.data['model_result_hash'] = 'f' * 64
    if change == 'journal_binding': row.data['maps'][0]['journal']['binding']['actor_id'] = identity(99)
    if change == 'reserved_type': row.data['maps'][0]['reserved_call_id'] = True
    if change == 'raw_wire':
        stage = row.data['maps'][0]; root = pipeline._journal_root()
        (root / row.workspace_id / row.id / stage['chunk_id'] / stage['reserved_call_id'] / 'wire.bin').write_bytes(b'{}')
    with pytest.raises(HTTPException) as error:
        pipeline.check_row(row, p)
    assert error.value.detail['code'] == 'evidence_state_integrity'


def test_review_keeps_all_global_claims_original_immutable_and_nonce_cas_capacity(monkeypatch):
    p, _, _, row = reduced_row(); original = pipeline.draft_view(row, p)['model_result']
    value = copy.deepcopy(original); value['global_claims'][0]['statement'] += ' 人工另行核对。'
    command = pipeline.ReviewCommand(revision=row.version, request_id=uuid4(), value={'result': value, 'note': '人工末字🙂'})
    receipt = pipeline.record_review(row, p, command); after = copy.deepcopy(row.data)
    assert pipeline.review_reply(row, p, receipt)['current_matches']
    assert pipeline.draft_view(row, p)['model_result'] == original
    assert pipeline.record_review(row, Principal(p.user_id, p.workspace_id, 'viewer', ''), command) == receipt
    assert row.data == after
    with pytest.raises(HTTPException):
        pipeline.record_review(row, p, command.model_copy(update={'request_id': uuid4()}))
    fresh = pipeline.ReviewCommand(revision=row.version, request_id=uuid4(), value={'result': value, 'note': 'new'})
    monkeypatch.setattr(pipeline, 'MAX_STATE_BYTES', len(json.dumps(row.data, ensure_ascii=False).encode()))
    with pytest.raises(HTTPException) as error:
        pipeline.record_review(row, p, fresh)
    assert error.value.status_code == 413 and row.data == after


def test_owner_scope_is_exact_even_for_admin_and_zero_budget_preview_has_no_authority():
    p, cap, body, row = scene()
    with pytest.raises(HTTPException) as error:
        pipeline.draft_view(row, Principal(identity(99), p.workspace_id, 'admin', ''))
    assert error.value.status_code == 404
    preview = pipeline.build_preview(p, body, {}, cap, row.data['preview']['source_snapshot'], can_start=False,
        generation_version=preparation.GENERATION_V2)
    assert pipeline.check_preview(preview, p) == preview and preview['can_start'] is False


def test_reserved_unknown_or_pre_reserved_inflight_can_never_be_replanned_as_unstarted():
    p, _, _, row = scene(); row.status = 'failed'; row.data['phase'] = 'failed'
    command = pipeline.ResumePreviewCommand(revision=row.version, request_id='fes_' + str(uuid4()))
    first = row.data['maps'][0]; first['inflight'] = True
    with pytest.raises(HTTPException) as error:
        pipeline.resume_preview(row, p, command)
    assert error.value.detail['code'] == 'evidence_resume_blocked'
    first['reserved_call_id'] = str(uuid4())
    with pytest.raises(HTTPException) as error:
        pipeline.resume_preview(row, p, command)
    assert error.value.detail['code'] == 'evidence_resume_blocked'


def test_resume_is_remaining_only_while_free_recovery_cannot_pay_for_future_pending(monkeypatch):
    p, _, _, row = scene(); first = row.data['maps'][0]
    store(row, first['chunk_id'], map_value(row, first['chunk_id']), succeeded=False)
    row.status = 'failed'; row.data['phase'] = 'failed'
    command = pipeline.ResumePreviewCommand(revision=row.version, request_id='fes_' + str(uuid4()))
    preview = pipeline.resume_preview(row, p, command)
    assert preview['saved_chunks'] == [first['chunk_id']] and preview['completed_chunks'] == []
    assert [chunk['chunk_id'] for chunk in preview['remaining_chunks']] == [stage['chunk_id'] for stage in row.data['maps'][1:]]
    assert preview['input_summary']['call_count'] == len(row.data['maps']) - 1
    monkeypatch.setattr(pipeline, 'owned', lambda *args: row)
    with pytest.raises(HTTPException) as error:
        pipeline.recover_saved_route(UUID(row.id), pipeline.RecoveryCommand(revision=row.version, request_id=uuid4()), p, object())
    assert error.value.detail['code'] == 'evidence_saved_response_incomplete'


def test_resume_disjoint_complete_coverage_and_original_messages_are_checked_even_after_rehash():
    p, _, _, row = scene()
    # A synthetic interruption after the first committed Map, before next reserve.
    first = row.data['maps'][0]; store(row, first['chunk_id'], map_value(row, first['chunk_id']))
    row.status = 'failed'; row.data['phase'] = 'failed'
    preview = pipeline.resume_preview(row, p, pipeline.ResumePreviewCommand(revision=row.version, request_id='fes_' + str(uuid4())))
    assert preview['completed_chunks'] == [row.data['maps'][0]['chunk_id']]
    preview['remaining_chunks'][0]['messages_hash'] = 'f' * 64
    preview['preview_hash'] = digest({key: item for key, item in preview.items() if key != 'preview_hash'})
    with pytest.raises(ValueError):
        pipeline._check_resume_preview(preview, row, p)


@pytest.mark.parametrize('complete', [False, True])
def test_legacy_failure_callback_never_takes_workspace_after_candidate_lock(monkeypatch, complete):
    p, _, _, row = mapped_row() if complete else scene()
    row.status = 'running'; row.data['phase'] = 'map'
    def forbidden(*args, **kwargs):
        raise AssertionError('failure dispatch already holds candidate; workspace lock reverses order')
    monkeypatch.setattr(pipeline.legacy, 'workspace_draft', forbidden)
    monkeypatch.setattr(pipeline, 'owned', lambda db, actor, identity, lock=False: row)
    monkeypatch.setattr(pipeline, 'audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, 'checkpoint', lambda db: None)
    job = NS(id=row.data['job_id'], kind=pipeline.legacy.KIND, workspace_id=p.workspace_id,
        data={'report_draft_id': row.id, 'actor_id': p.user_id})
    pipeline.record_job_failure(object(), p, job, 'synthetic stop')
    assert row.status == ('awaiting_merge' if complete else 'failed')
    assert row.data['phase'] == ('awaiting_reduce' if complete else 'failed')


@pytest.mark.parametrize('field', ['id', 'kind', 'workspace_id', 'actor_id'])
def test_stale_or_foreign_job_failure_cannot_stop_the_current_explicit_resume(monkeypatch, field):
    p, _, _, row = scene(); before = copy.deepcopy(row.data)
    job = NS(id=row.data['job_id'], kind=pipeline.legacy.KIND, workspace_id=p.workspace_id,
        data={'report_draft_id': row.id, 'actor_id': p.user_id})
    if field == 'actor_id': job.data[field] = identity(99)
    else: setattr(job, field, 'different-kind' if field == 'kind' else identity(99))
    monkeypatch.setattr(pipeline, 'owned', lambda *args, **kwargs: row)
    pipeline.record_job_failure(object(), p, job, 'stale original executor')
    assert row.data == before and row.status == 'queued' and row.version == 1
