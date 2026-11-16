"""Full reading preview boundaries; pure synthetic sources, no paid IO."""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import report_full_reading_pipeline as pipeline
from generic_agents.contracts import digest
from generic_agents.security import Principal
from tests.test_report_full_reading_source_pure import snapshot, identity


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('pure preview must not dispatch or connect')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(pipeline, 'cloud_json', forbidden)


def scene():
    source = snapshot()
    p = Principal(identity(11), identity(3), 'operator', '')
    cap = NS(content_hash='a' * 64, data={'execution': {'provider': 'qwen',
        'model': 'qwen-plus', 'max_tokens': 8192}, 'data_policy': {'redact_fields': []}})
    body = pipeline.PreviewCommand(request_id='fr_' + str(uuid4()), revision=1,
        model_id=identity(12), material_refs=[source['selection']['original_ref']],
        selection=source['selection'], note='完整来源，不截断。')
    return p, cap, body, source


def preview():
    p, cap, body, source = scene()
    return p, pipeline.build_preview(p, body, {'instanceProfile': {'goal': '人工核对'}}, cap, source)


def test_complete_frozen_preview_and_budget_zero_inspection_are_not_authority():
    p, cap, body, source = scene()
    value = pipeline.build_preview(p, body, {}, cap, source, can_start=False)
    assert pipeline.check_preview(value, p) == value
    refs = [ref for batch in value['batches'] for ref in batch['source_refs']]
    assert refs == ['w:p1', 'w:p2', 'o:p1', 'o:p2']
    assert value['input_summary']['maximum_total_calls'] == 2
    assert value['can_start'] is value['can_apply'] is value['can_publish'] is False
    assert value['start_blocker'] and value['source_snapshot'] == source
    assert value['generation_contract']['input_policy']['single_batch'] is True


@pytest.mark.parametrize('mutate', [
    lambda v: v['batches'][0]['source_refs'].pop(),
    lambda v: v['batches'][0]['payload']['blocks'].pop(),
    lambda v: v['batches'][0]['payload']['blocks'].reverse(),
    lambda v: v.update(projection={'blocks': []}),
    lambda v: v['input_summary'].update(maximum_total_calls=99),
    lambda v: v['generation_contract'].update(trusted_task='replace authority'),
    lambda v: v.update(can_apply=True),
])
def test_outer_rehash_does_not_allow_changed_coverage_messages_or_authority(mutate):
    p, value = preview(); mutate(value)
    value['preview_hash'] = digest({k: v for k, v in value.items() if k != 'preview_hash'})
    with pytest.raises(HTTPException) as error:
        pipeline.check_preview(value, p)
    assert error.value.detail['code'] == 'full_preview_integrity'


def test_actor_scope_is_exact_even_when_outer_preview_is_rehashed():
    p, value = preview()
    other = Principal(identity(99), p.workspace_id, 'admin', '')
    with pytest.raises(HTTPException):
        pipeline.check_preview(value, other)


def test_database_object_key_reordering_does_not_change_authorized_messages():
    p, value = preview()
    def reordered(item):
        if isinstance(item, dict):
            return {k: reordered(v) for k, v in reversed(list(item.items()))}
        return [reordered(v) for v in item] if isinstance(item, list) else item
    persisted = reordered(value)
    assert pipeline.check_preview(persisted, p) == value


@pytest.mark.parametrize('consent', [1, 'true', False, None])
def test_start_and_merge_require_literal_true(consent):
    _, _, body, _ = scene()
    with pytest.raises(ValidationError):
        pipeline.StartCommand(**body.model_dump(mode='json'), preview_hash='a' * 64, consent=consent)
    with pytest.raises(ValidationError):
        pipeline.MergeCommand(revision=1, request_id='fm_' + str(uuid4()), preview_hash='a' * 64, consent=consent)


def test_explicit_capacity_cannot_trigger_truncation_or_more_than_two_extractions(monkeypatch):
    p, cap, body, source = scene()
    base = pipeline.input_policy
    monkeypatch.setattr(pipeline, 'input_policy', lambda *a, **kw: {**base(*a, **kw), 'max_input_bytes': 1})
    before = copy.deepcopy(source)
    with pytest.raises(HTTPException) as error:
        pipeline.build_preview(p, body, {}, cap, source)
    assert error.value.detail['code'] == 'full_input_capacity'
    assert source == before


def test_extraction_citation_index_contains_only_that_batch_sources():
    _, value = preview()
    first = value['batches'][0]
    first['source_refs'] = ['w:p1', 'w:p2']
    assert set(pipeline._stage_sources(value, first['batch_id'])) == {'w:p1', 'w:p2'}
    assert set(pipeline._stage_sources(value, 'merge')) == {'w:p1', 'w:p2', 'o:p1', 'o:p2'}


def frozen_row(p, value):
    body = pipeline.StartCommand(**value['body'], preview_hash=value['preview_hash'], consent=True)
    row = NS(id=identity(30), workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=body.request_id, status='queued', version=1)
    row.data = {'schema': pipeline.STATE, 'phase': 'extract', 'preview': value,
        'start_command_hash': pipeline.command_hash(p, body),
        'start_receipt': pipeline.start_receipt(p, row.id, body, value),
        'extractions': [{'batch_id': batch['batch_id'], 'status': 'pending'} for batch in value['batches']],
        'merge': {'status': 'not_started'}, 'model_result': None, 'model_result_hash': None,
        'review_value': None, 'review_history': [], 'recovery_history': [],
        'error': None, 'error_code': None, 'job_id': identity(31)}
    return row, body


@pytest.mark.parametrize('version', [pipeline.MESSAGES_V1, pipeline.MESSAGES_V2])
def test_frozen_version_survives_jsonb_order_and_dispatch_rebuild(monkeypatch, version):
    p, cap, body, source = scene()
    config = {'businesses': ['既有业务仅作参考']}
    value = pipeline.build_preview(p, body, config, cap, source, messages_version=version)
    row, _ = frozen_row(p, value)
    def reordered(item):
        if isinstance(item, dict):
            return {k: reordered(v) for k, v in reversed(list(item.items()))}
        return [reordered(v) for v in item] if isinstance(item, list) else item
    row.data = reordered(row.data)
    original = copy.deepcopy(row.data)
    monkeypatch.setattr(pipeline.legacy.old(), 'model', lambda *args: cap)
    monkeypatch.setattr(pipeline.legacy, 'workspace_draft', lambda *args: NS(version=1, data=config))
    monkeypatch.setattr(pipeline, 'freeze_full_source', lambda *args: copy.deepcopy(source))
    assert pipeline.check_row(row, p) is row
    assert pipeline._fresh_cap(object(), p, row, row.data['extractions'][0]['batch_id']) is cap
    assert row.data == original
    assert row.data['preview']['generation_contract']['messages_version'] == version


def test_new_default_changes_only_task_generation_not_output_schema_or_source():
    p, cap, body, source = scene()
    old = pipeline.build_preview(p, body, {}, cap, source, messages_version=pipeline.MESSAGES_V1)
    current = pipeline.build_preview(p, body, {}, cap, source)
    first, second = old['generation_contract'], current['generation_contract']
    assert second['messages_version'] == pipeline.MESSAGES_V2
    assert old['body'] == current['body'] and old['source_hash'] == current['source_hash']
    assert {k: v for k, v in first.items() if k not in {'messages_version', 'trusted_task', 'merge_task'}} == {
        k: v for k, v in second.items() if k not in {'messages_version', 'trusted_task', 'merge_task'}}
    assert old['preview_hash'] != current['preview_hash']
    assert old['batches'][0]['messages_hash'] != current['batches'][0]['messages_hash']
    assert old['batches'][0]['payload'] == current['batches'][0]['payload']
    assert pipeline.check_preview(old, p) == old and pipeline.check_preview(current, p) == current


@pytest.mark.parametrize('version', ['', 'report-full-reading-messages-v3', None, True])
def test_unknown_message_version_is_never_prepared_or_accepted(version):
    p, cap, body, source = scene()
    with pytest.raises(ValueError):
        pipeline.generation(cap, messages_version=version)
    value = pipeline.build_preview(p, body, {}, cap, source)
    value['generation_contract']['messages_version'] = version
    with pytest.raises(ValueError):
        pipeline.check_generation(value['generation_contract'])
    value['generation_contract_hash'] = digest(value['generation_contract'])
    value = pipeline.seal({k: v for k, v in value.items() if k != 'preview_hash'}, 'preview_hash')
    with pytest.raises(HTTPException) as error:
        pipeline.check_preview(value, p)
    assert error.value.detail['code'] == 'full_preview_integrity'


def test_v1_label_cannot_authorize_v2_task_even_with_outer_hashes_resealed():
    p, value = preview()
    value['generation_contract']['messages_version'] = pipeline.MESSAGES_V1
    value['generation_contract_hash'] = digest(value['generation_contract'])
    value = pipeline.seal({k: v for k, v in value.items() if k != 'preview_hash'}, 'preview_hash')
    with pytest.raises(HTTPException) as error:
        pipeline.check_preview(value, p)
    assert error.value.detail['code'] == 'full_preview_integrity'


def test_saved_v1_nonce_replays_before_fresh_permissions_or_live_v2_repreview(monkeypatch):
    p, cap, body, source = scene()
    value = pipeline.build_preview(p, body, {}, cap, source, messages_version=pipeline.MESSAGES_V1)
    row, command = frozen_row(p, value)
    original = copy.deepcopy(row.data)
    monkeypatch.setattr(pipeline.legacy, 'by_nonce', lambda *args: row)
    def forbidden(*args, **kwargs):
        raise AssertionError('saved nonce replay must not rebuild, reserve or commit')
    monkeypatch.setattr(pipeline, 'require', forbidden)
    monkeypatch.setattr(pipeline, 'make_preview', forbidden)
    revoked = Principal(p.user_id, p.workspace_id, 'viewer', '')
    reply = pipeline.start_route(command, revoked, object())
    assert reply['receipt'] == original['start_receipt'] and row.data == original
    assert reply['draft']['preview']['generation_contract']['messages_version'] == pipeline.MESSAGES_V1


def test_unstored_v1_preview_requires_new_explicit_preview_instead_of_silent_upgrade(monkeypatch):
    p, cap, body, source = scene()
    old = pipeline.build_preview(p, body, {}, cap, source, messages_version=pipeline.MESSAGES_V1)
    current = pipeline.build_preview(p, body, {}, cap, source)
    command = pipeline.StartCommand(**old['body'], preview_hash=old['preview_hash'], consent=True)
    monkeypatch.setattr(pipeline.legacy, 'by_nonce', lambda *args: None)
    monkeypatch.setattr(pipeline.legacy, 'workspace_draft', lambda *args: NS(version=1, data={}))
    monkeypatch.setattr(pipeline.legacy, 'guard_existing_paid', lambda *args: None)
    monkeypatch.setattr(pipeline, 'make_preview', lambda *args: current)
    # No DB API is present: a write before rejection would fail this test.
    with pytest.raises(HTTPException) as error:
        pipeline.start_route(command, p, object())
    assert error.value.status_code == 409 and error.value.detail['code'] == 'preview_changed'


@pytest.mark.parametrize('version', [pipeline.MESSAGES_V1, pipeline.MESSAGES_V2])
def test_merge_prepares_frozen_task_version_after_all_extractions(version):
    p, cap, body, source = scene()
    value = pipeline.build_preview(p, body, {}, cap, source, messages_version=version)
    row, _ = frozen_row(p, value)
    result = {'schema': 'report-business-candidate-v3', 'domain': '合成版本兼容核对',
        'objects': [], 'relationships': [], 'businesses': [], 'questions': ['资料含义仍待人工核对。']}
    for stage in row.data['extractions']:
        call = str(uuid4())
        raw = pipeline.seal({'value': copy.deepcopy(result), 'receipt': {'call_id': call}}, 'hash')
        stage.update(status='succeeded', result=copy.deepcopy(result), result_hash=digest(result),
            reserved_call_id=call, raw_response=raw)
    row.status = 'awaiting_merge'; row.version = 2
    assert pipeline.check_row(row, p) is row
    command = pipeline.MergePreviewCommand(revision=2, request_id='fm_' + str(uuid4()))
    merged = pipeline.merge_preview(row, p, command, cap)
    prepared, _ = pipeline.prepared_stage(cap, value['generation_contract'], merged['payload'], merge=True)
    assert merged['messages_hash'] == digest(prepared)
    assert merged['generation_contract_hash'] == value['generation_contract_hash']
    assert pipeline._check_merge_preview(merged, row, p) is None
