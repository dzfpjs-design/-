"""Owned empty-PG evidence workflow; real synthetic reserve/callback/settlement.

No provider HTTP or original report is read. Source provenance and OCR persistence
use the existing two-page fixture with only explicitly synthetic local IO. Root
alone executes this file with its external owned-DB plugin and blank cloud keys.
"""
import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import cloud_budget
from generic_agents.api import app
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_preparation as preparation
from generic_agents.cloud_input_profiles import input_policy, request_options, measure_input
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.gateway import cloud_json_options, prepare_cloud_json
from generic_agents.leases import LeaseLost
from generic_agents.models import Audit, CloudCall, Job, SetupMaterial, SetupReportDraft
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene, example_candidate


BASE = '/api/setup/report-evidence-readings'


def capture(scene, name, value):
    """Optional root-owned bridge of this empty-PG scene's public serializers."""
    directory = os.environ.get('GA_F11_DTO_CAPTURE_DIR')
    if not directory:
        return
    assert re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', name)
    assert re.fullmatch(pipeline.FE, scene['body']['request_id'])
    root = Path(directory)
    assert root.is_absolute() and not root.is_symlink()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')
    assert len(content) <= 8 * 1024 * 1024
    path = root / (scene['body']['request_id'] + '-' + name + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(content)


def reply(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def evidence_scene(reading_scene, monkeypatch):
    # These historical integration cases retain their original keyed V2 wire.
    # The new V3 suite exercises the product default without this fixture.
    monkeypatch.setattr(pipeline, 'CURRENT_MAP_GENERATION', preparation.GENERATION_V2)
    fence(); scene = reading_scene
    body = copy.deepcopy(scene['body']); body['request_id'] = 'fe_' + str(uuid4())
    return {**scene, 'body': body}


def counts():
    with Session(engine()) as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (CloudCall, SetupReportDraft, Job, Audit)}


def start(scene):
    preview = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    command = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(scene['client'].post(BASE, json=command), 202)
    capture(scene, 'account', reply(scene['client'].get('/api/me')))
    capture(scene, 'preview', preview); capture(scene, 'start-command', command); capture(scene, 'queued', accepted)
    return preview, command, accepted


def get(scene, identity):
    return reply(scene['client'].get(BASE + '/' + identity))


def reclaim_stopped_lease(scene, stopped, provider, *, label='map'):
    """Actual new claim after a stopped executor; no sleep or fake lock result."""
    from generic_agents.worker import process_once
    assert stopped['status'] == 'running'
    capture(scene, label + '-lease-stopped', stopped)
    calls = list(provider.calls)
    journals = [stage['journal'] for stage in stopped['maps']] + [stopped['reduction']['journal']]
    with Session(engine()) as db:
        job = db.get(Job, stopped['job_id']); clock = db.scalar(select(func.clock_timestamp()))
        assert job.status == 'running' and job.lease_until > clock
        token, recoveries = job.lease_token, job.data['lease_recoveries']
        fees = {identity: (call.status, call.reserved_tokens, call.charged_tokens, copy.deepcopy(call.data))
            for identity in calls for call in [db.get(CloudCall, identity)]}
        # This owned synthetic fixture simulates elapsed time after the worker
        # exited. claim(), its new token and attach() remain production code.
        job.lease_until = clock - timedelta(seconds=1); db.commit()
    assert process_once(stopped['job_id'])
    restored = get(scene, stopped['id'])
    assert provider.calls == calls
    assert [stage['journal'] for stage in restored['maps']] + [restored['reduction']['journal']] == journals
    with Session(engine()) as db:
        job = db.get(Job, stopped['job_id'])
        assert job.lease_token != token and job.data['lease_recoveries'] == recoveries + 1
        assert job.status in {'succeeded', 'failed'} and job.lease_until is None
        for identity, frozen in fees.items():
            call = db.get(CloudCall, identity)
            assert (call.status, call.reserved_tokens, call.charged_tokens, call.data) == frozen
    capture(scene, label + '-lease-recovered', restored)
    return restored


def merged_value(payload):
    def span_citations(value):
        if type(value) is dict:
            if set(value) == {'source_ref', 'quote'}:
                return {'span_ref': value['source_ref'] + '/s1'}
            return {key: span_citations(item) for key, item in value.items()}
        return [span_citations(item) for item in value] if type(value) is list else value
    result = span_citations(example_candidate())
    result.update(schema='report-evidence-candidate-v4', source_hash=payload['source_hash'],
        catalog_hash=payload['catalog_hash'], global_claims=[])
    reconciliation = []
    for number, (key, claim) in enumerate(payload['input_items'].items()):
        result['global_claims'].append({**copy.deepcopy(claim), 'key': f'global_{number}'})
        reconciliation.append({'input_id': key, 'disposition': 'kept', 'output_kind': 'global_claims',
            'output_index': number, 'reason': '合成协议逐条保留原声明，不代表实际模型质量。'})
    return {'result': result, 'reconciliation': reconciliation}


class SyntheticProvider:
    """Create actual CloudCalls, then invoke real callbacks; never call network."""
    def __init__(self, scene, monkeypatch, mode='success'):
        self.scene = scene; self.mode = mode; self.calls = []; self.receipts = []; self.payloads = []
        monkeypatch.setattr(pipeline, 'cloud_json', self)

    def __call__(self, cap, messages, schema, principal, **callbacks):
        assert callbacks['full_report'] is True
        payload = json.loads(messages[-1]['content']); reducing = payload['schema'] == 'report-evidence-reduction-payload-v1'
        prepared, _, images = prepare_cloud_json(cap, messages, schema); assert images == 0
        policy = {'single_batch': True, **input_policy(cap.data['execution'], full_report=True)}
        options = request_options(cap.data['execution'], cloud_json_options(cap.data['execution']), full_report=True)
        summary = measure_input(prepared, policy, options['max_tokens'])
        call_id = cloud_budget.reserve(principal, cap, summary['token_bound'], digest(prepared))
        with Session(engine()) as db:
            call = db.get(CloudCall, call_id)
            call.data = {**call.data, 'dispatch_options_hash': digest(options), 'input_policy_hash': digest(policy), 'synthetic_only': True}
            db.commit()
        self.calls.append(call_id); self.payloads.append(copy.deepcopy(payload)); response_receipt = None
        try:
            callbacks['on_reserved'](call_id)
            if self.mode == 'unknown':
                raise TimeoutError('synthetic no-response window')
            if reducing:
                value = merged_value(payload)
                if self.mode == 'invalid_reduce':
                    value['reconciliation'].pop()
            else:
                refs = [row[0] for row in payload['spans']]
                ref = refs[0]
                if self.mode == 'outside_batch' and ref.startswith('w:'):
                    ref = 'o:p1/s1'
                value = {'schema': 'report-evidence-map-v1', 'claims': [] if self.mode == 'empty' else [{
                    'key': 'claim_1', 'kind': '职责', 'statement': '合成协议声明，原文存在并不证明此解释正确。',
                    'span_refs': [ref], 'status': 'explicit', 'explanation': ''}]}
                if self.mode == 'invalid_map_kind':
                    value['claims'][0]['kind'] = '未登记的新分类'
            response_receipt = {'provider': cap.data['execution']['provider'], 'model': cap.data['execution']['model'],
                'call_id': call_id, 'request_id': 'synthetic-' + str(uuid4()), 'usage': {'total_tokens': 20}}
            self.receipts.append(copy.deepcopy(response_receipt))
            callbacks['on_response'](json.dumps(value, ensure_ascii=False), response_receipt)
            if self.mode == 'after_raw':
                raise LeaseLost('synthetic after original journal+DB checkpoint')
            return copy.deepcopy(value), response_receipt
        finally:
            # Same real independent gateway finish fallback; a callback-settled
            # call is left unchanged. No usage or paid ledger validator mocked.
            cloud_budget.finish(call_id, response_receipt)


def map_all(scene, accepted):
    from generic_agents.worker import process_once
    assert process_once(accepted['draft']['job_id'])
    value = get(scene, accepted['draft']['id'])
    assert value['status'] == 'awaiting_merge' and value['phase'] == 'awaiting_reduce', value.get('error')
    assert all(stage['status'] == 'succeeded' and stage['raw_response_saved'] for stage in value['maps'])
    capture(scene, 'completed-map', value)
    return value


def reduce_all(scene, view):
    from generic_agents.worker import process_once
    identity = view['id']; body = {'revision': view['revision'], 'request_id': 'fer_' + str(uuid4())}
    preview = reply(scene['client'].post(BASE + '/' + identity + '/reduce-preview', json=body))
    command = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(scene['client'].post(BASE + '/' + identity + '/reduce', json=command), 202)
    capture(scene, 'reduce-preview', preview); capture(scene, 'reduce-command', command); capture(scene, 'reduce-accepted', accepted)
    assert process_once(accepted['draft']['job_id'])
    final = get(scene, identity); capture(scene, 'reduced', final)
    return preview, command, accepted, final


def test_actual_full_source_maps_reduce_review_receipts_cas_and_original_journal(evidence_scene, monkeypatch):
    scene = evidence_scene; provider = SyntheticProvider(scene, monkeypatch)
    preview, command, accepted = start(scene)
    assert preview['input_summary']['map_call_count'] == 2
    assert preview['input_summary']['maximum_total_calls'] == 3
    view = map_all(scene, accepted)
    reduced_preview, reduced_body, reduced_start, final = reduce_all(scene, view)
    assert final['status'] == 'review' and final['model_result']['global_claims'] and final['model_result']['businesses']
    assert len(provider.calls) == 3
    assert {entry['input_id'] for entry in final['reduction']['reconciliation']} == set(reduced_preview['reduction_preparation']['payload']['input_items'])
    expected_raw = []
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], final['id'])
        assert 'model_result' not in row.data
        for stage in row.data['maps']:
            assert set(stage) == pipeline.STAGE_KEYS | {'chunk_id'}
            assert all(key not in stage for key in ['result', 'value', 'raw_response', 'content'])
            expected_raw.append(pipeline.journal.read_response(pipeline._journal_root(),
                pipeline.journal_binding(row, stage['chunk_id'], stage['reserved_call_id']), stage['journal'])['content'])
    original = copy.deepcopy(final['model_result']); value = copy.deepcopy(original)
    value['global_claims'][0]['statement'] += ' 人工私稿补充核对。'
    review = {'revision': final['revision'], 'request_id': str(uuid4()), 'value': {'result': value, 'note': '人工保存末字🙂'}}
    saved = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=review))
    capture(scene, 'model-result', final['model_result']); capture(scene, 'review-command', review); capture(scene, 'review-reply', saved)
    capture(scene, 'status', reply(scene['client'].get(BASE + '/' + final['id'] + '/status')))
    capture(scene, 'list', reply(scene['client'].get(BASE)))
    capture(scene, 'by-request', reply(scene['client'].get(BASE + '/by-request/' + scene['body']['request_id'])))
    assert saved['current_matches'] and saved['draft']['model_result'] == original
    assert saved['draft']['review_value'] == review['value']
    assert reply(scene['client'].get(BASE + '/' + final['id'] + '/review-receipts/' + review['request_id'])) == saved
    before = counts()
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].post(BASE + '/' + final['id'] + '/reduce', json=reduced_body), 202)['receipt'] == reduced_start['receipt']
    assert reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=review)) == saved
    assert counts() == before
    stale = {**review, 'request_id': str(uuid4())}
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json=stale).status_code == 409
    changed = copy.deepcopy(review); changed['value']['note'] += '改体'
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json=changed).status_code == 409
    assert get(scene, final['id']) == saved['draft']
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], final['id'])
        assert expected_raw == [pipeline.journal.read_response(pipeline._journal_root(),
            pipeline.journal_binding(row, stage['chunk_id'], stage['reserved_call_id']), stage['journal'])['content'] for stage in row.data['maps']]
        assert all(db.get(CloudCall, call_id).charged_tokens == 20 for call_id in provider.calls)


def test_zero_budget_preview_and_source_change_before_send_never_call_provider(evidence_scene, monkeypatch):
    from generic_agents.config import settings
    from generic_agents.worker import process_once
    scene = evidence_scene; provider = SyntheticProvider(scene, monkeypatch)
    before = counts(); monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 0)
    preview = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    assert preview['can_start'] is False and counts() == before
    assert scene['client'].post(BASE, json={**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}).status_code == 409
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 3)
    _, _, accepted = start(scene)
    with Session(engine()) as db:
        material = db.get(SetupMaterial, scene['original']['id']); material.archived = True; db.commit()
    assert process_once(accepted['draft']['job_id'])
    view = get(scene, accepted['draft']['id'])
    assert view['status'] == 'failed' and provider.calls == []
    assert counts()[CloudCall.__tablename__] == 0


@pytest.mark.parametrize('mode', ['empty', 'outside_batch'])
def test_empty_or_invalid_batch_stops_without_hidden_reduce_or_later_map(evidence_scene, monkeypatch, mode):
    from generic_agents.worker import process_once
    scene = evidence_scene; provider = SyntheticProvider(scene, monkeypatch, mode)
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id']); view = get(scene, accepted['draft']['id'])
    if mode == 'empty':
        assert view['status'] == 'needs_clarification' and view['model_result'] is None and len(provider.calls) == 2
        assert all(stage['result_count'] == 0 for stage in view['maps'])
    else:
        assert view['status'] == 'failed' and view['maps'][0]['raw_response_saved'] and len(provider.calls) == 1
        assert view['maps'][0]['receipt'] == provider.receipts[0]
        body = {'revision': view['revision'], 'request_id': 'fes_' + str(uuid4())}
        assert scene['client'].post(BASE + '/' + view['id'] + '/resume-preview', json=body).status_code == 409
    before = counts(); body = {'revision': view['revision'], 'request_id': 'fer_' + str(uuid4())}
    assert scene['client'].post(BASE + '/' + view['id'] + '/reduce-preview', json=body).status_code == 409
    assert counts() == before


@pytest.mark.parametrize('mode', ['unknown', 'fresh_rejected', 'binding_checkpoint_lost'])
def test_any_real_reservation_failure_stays_unknown_and_cannot_be_repaid(evidence_scene, monkeypatch, mode):
    from generic_agents.worker import process_once
    scene = evidence_scene; provider = SyntheticProvider(scene, monkeypatch, 'unknown' if mode == 'unknown' else 'success')
    if mode == 'fresh_rejected':
        real_fresh = pipeline._fresh_cap; calls = 0
        def fresh(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise HTTPException(409, {'code': 'evidence_source_changed', 'message': '合成预留后授权失败'})
            return real_fresh(*args, **kwargs)
        monkeypatch.setattr(pipeline, '_fresh_cap', fresh)
    if mode == 'binding_checkpoint_lost':
        real_checkpoint = pipeline.checkpoint; fired = False
        def checkpoint(db):
            nonlocal fired
            row = db.scalar(select(SetupReportDraft).where(SetupReportDraft.request_id == scene['body']['request_id']))
            if row and row.data['maps'][0]['reserved_call_id'] and not fired:
                fired = True; raise LeaseLost('合成绑定预留事务失去租约')
            return real_checkpoint(db)
        monkeypatch.setattr(pipeline, 'checkpoint', checkpoint)
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id']); view = get(scene, accepted['draft']['id'])
    if mode == 'binding_checkpoint_lost':
        view = reclaim_stopped_lease(scene, view, provider)
    assert view['status'] == 'response_unknown' and len(provider.calls) == 1
    assert not view['maps'][0]['raw_response_saved']
    if mode != 'binding_checkpoint_lost':
        assert view['maps'][0]['reserved_call_id'] == provider.calls[0]
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, view['id']); assert row.data['maps'][0]['inflight'] is True
    before = counts()
    assert scene['client'].post(BASE + '/' + view['id'] + '/resume-preview', json={
        'revision': view['revision'], 'request_id': 'fes_' + str(uuid4())}).status_code == 409
    assert scene['client'].post(BASE + '/' + view['id'] + '/recover-saved', json={
        'revision': view['revision'], 'request_id': str(uuid4())}).status_code == 409
    assert counts() == before


@pytest.mark.parametrize('window', ['after_raw', 'journal_before_db'])
def test_known_call_journal_survives_callback_db_rollback_and_explicit_resume_only_sends_remaining(evidence_scene, monkeypatch, window):
    from generic_agents.worker import process_once
    scene = evidence_scene; provider = SyntheticProvider(scene, monkeypatch, 'after_raw' if window == 'after_raw' else 'success')
    real_settle = pipeline._settle
    if window == 'journal_before_db':
        fired = False
        def lost(*args, **kwargs):
            nonlocal fired
            if not fired:
                fired = True; raise LeaseLost('合成磁盘原答已完成而DB回滚')
            return real_settle(*args, **kwargs)
        monkeypatch.setattr(pipeline, '_settle', lost)
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id']); failed = get(scene, accepted['draft']['id'])
    failed = reclaim_stopped_lease(scene, failed, provider)
    assert failed['status'] == 'failed' and len(provider.calls) == 1
    original = copy.deepcopy(failed['maps'][0]['journal'])
    assert failed['maps'][0]['raw_response_saved'] and failed['maps'][0]['result'] is None
    if window == 'journal_before_db':
        with Session(engine()) as db:
            assert db.get(SetupReportDraft, failed['id']).data['maps'][0]['journal'] is None
    assert scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json={
        'revision': failed['revision'], 'request_id': str(uuid4())}).status_code == 409
    body = {'revision': failed['revision'], 'request_id': 'fes_' + str(uuid4())}
    preview = reply(scene['client'].post(BASE + '/' + failed['id'] + '/resume-preview', json=body))
    capture(scene, 'resume-preview', preview)
    assert preview['saved_chunks'] == ['map-1'] and [chunk['chunk_id'] for chunk in preview['remaining_chunks']] == ['map-2']
    assert preview['input_summary']['call_count'] == 1
    command = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    resumed = reply(scene['client'].post(BASE + '/' + failed['id'] + '/resume', json=command), 202)
    capture(scene, 'resume-command', command); capture(scene, 'resume-reply', resumed)
    assert resumed['draft']['maps'][0]['status'] == 'succeeded'
    provider.mode = 'success'; assert process_once(resumed['draft']['job_id'])
    finished = get(scene, failed['id'])
    capture(scene, 'resume-completed', finished)
    assert finished['status'] == 'awaiting_merge' and len(provider.calls) == 2
    assert finished['maps'][0]['journal'] == original and finished['maps'][0]['reserved_call_id'] == provider.calls[0]
    before = counts()
    assert reply(scene['client'].post(BASE + '/' + failed['id'] + '/resume', json=command), 202)['receipt'] == resumed['receipt']
    assert reply(scene['client'].get(BASE + '/' + failed['id'] + '/resume-receipts/' + body['request_id']))['receipt'] == resumed['receipt']
    assert counts() == before


def test_last_map_raw_can_be_recovered_free_and_reduce_raw_recovery_has_no_extra_calls(evidence_scene, monkeypatch):
    from generic_agents.worker import process_once
    scene = evidence_scene; provider = SyntheticProvider(scene, monkeypatch)
    real_consume = pipeline._consume; stopped = False
    def final_map(db, p, row, key):
        nonlocal stopped
        if key == 'map-2' and not stopped:
            stopped = True; raise LeaseLost('合成最后Map原答已存但未消费')
        return real_consume(db, p, row, key)
    monkeypatch.setattr(pipeline, '_consume', final_map)
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id']); failed = get(scene, accepted['draft']['id'])
    failed = reclaim_stopped_lease(scene, failed, provider)
    assert failed['status'] == 'failed' and len(provider.calls) == 2
    body = {'revision': failed['revision'], 'request_id': str(uuid4())}
    restored = reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=body))
    capture(scene, 'map-recovery-command', body); capture(scene, 'map-recovery-reply', restored)
    assert restored['draft']['status'] == 'awaiting_merge' and len(provider.calls) == 2
    assert reply(scene['client'].get(BASE + '/' + failed['id'] + '/recovery-receipts/' + body['request_id'])) == restored
    provider.mode = 'after_raw'
    _, _, _, reduction_failed = reduce_all(scene, restored['draft'])
    reduction_failed = reclaim_stopped_lease(scene, reduction_failed, provider, label='reduce')
    assert reduction_failed['status'] == 'failed' and reduction_failed['reduction']['raw_response_saved'] and len(provider.calls) == 3
    body2 = {'revision': reduction_failed['revision'], 'request_id': str(uuid4())}
    recovered = reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=body2))
    capture(scene, 'reduce-recovery-command', body2); capture(scene, 'reduce-recovery-reply', recovered)
    assert recovered['draft']['status'] == 'review' and len(provider.calls) == 3
    before = counts()
    assert reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=body2)) == recovered
    assert counts() == before


def test_last_map_consumed_checkpoint_then_lease_loss_finishes_without_rewriting_original(evidence_scene, monkeypatch):
    from generic_agents.worker import process_once
    scene = evidence_scene; provider = SyntheticProvider(scene, monkeypatch)
    real_consume = pipeline._consume; stopped = False
    def consumed(db, p, row, key):
        nonlocal stopped
        result = real_consume(db, p, row, key)
        if key == 'map-2' and not stopped:
            stopped = True; raise LeaseLost('合成末批消费已提交、待汇总状态尚未提交')
        return result
    monkeypatch.setattr(pipeline, '_consume', consumed)
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id'])
    view = get(scene, accepted['draft']['id'])
    view = reclaim_stopped_lease(scene, view, provider, label='tail')
    assert view['status'] == 'awaiting_merge' and view['phase'] == 'awaiting_reduce'
    assert len(provider.calls) == 2 and all(stage['status'] == 'succeeded' for stage in view['maps'])
    assert [stage['receipt'] for stage in view['maps']] == provider.receipts
    with Session(engine()) as db:
        assert all(db.get(CloudCall, call_id).charged_tokens == 20 for call_id in provider.calls)


@pytest.mark.parametrize('role', ['operator', 'admin'])
def test_private_pending_draft_and_all_nonce_reads_are_owner_scoped_even_for_admin(evidence_scene, role):
    scene = evidence_scene; _, _, accepted = start(scene); original = accepted['draft']
    username, password = 'other_evidence_' + uuid4().hex[:12], 'Synthetic-evidence-' + uuid4().hex
    user = reply(scene['client'].post('/api/users', json={'username': username, 'password': password, 'role': role}), 201)
    with TestClient(app) as other:
        login = reply(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        assert reply(other.get('/api/me'))['user_id'] == user['id']
        assert other.get(BASE + '/' + original['id']).status_code == 404
        assert other.get(BASE + '/' + original['id'] + '/status').status_code == 404
        assert other.get(BASE + '/by-request/' + scene['body']['request_id']).status_code == 404
        assert other.get(BASE + '/' + original['id'] + '/review-receipts/' + str(uuid4())).status_code == 404
        assert other.get(BASE + '/' + original['id'] + '/recovery-receipts/' + str(uuid4())).status_code == 404
        assert other.get(BASE + '/' + original['id'] + '/resume-receipts/fes_' + str(uuid4())).status_code == 404
        assert other.put(BASE + '/' + original['id'] + '/review', json={'revision': original['revision'],
            'request_id': str(uuid4()), 'value': {'result': {}, 'note': 'must not overwrite'}}).status_code == 404
        assert reply(other.get(BASE))['items'] == []
    assert get(scene, original['id']) == original and counts()[CloudCall.__tablename__] == 0


def test_unaccepted_old_map_preview_requires_new_preview_and_v2_dispatch_matches_it(evidence_scene, monkeypatch):
    from generic_agents import report_evidence_preparation as preparation
    scene = evidence_scene; before = counts()
    real_build = pipeline.build_preview
    # Produce an old, unaccepted preview using the exact frozen V1 producer.
    with monkeypatch.context() as old:
        def build_v1(*args, **kwargs):
            return real_build(*args, **{**kwargs, 'generation_version': preparation.GENERATION_V1})
        old.setattr(pipeline, 'build_preview', build_v1)
        preview = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    command = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    rejected = reply(scene['client'].post(BASE, json=command), 409)
    assert rejected['detail']['code'] == 'preview_changed' and counts() == before
    assert scene['client'].get(BASE + '/by-request/' + scene['body']['request_id']).status_code == 404
    scene['body']['request_id'] = 'fe_' + str(uuid4())
    provider = SyntheticProvider(scene, monkeypatch)
    fresh, _, accepted = start(scene); mapped = map_all(scene, accepted)
    assert fresh['map_preparation']['generation_contract']['schema'] == preparation.GENERATION_V2
    assert mapped['preview'] == fresh and len(provider.calls) == len(fresh['map_preparation']['plan']['chunks'])
    with Session(engine()) as db:
        for chunk, call_id in zip(fresh['map_preparation']['plan']['chunks'], provider.calls):
            call = db.get(CloudCall, call_id)
            assert call.data['request_hash'] == chunk['messages_hash']
            assert call.reserved_tokens == chunk['input_summary']['token_bound']


def test_old_v1_bad_raw_and_original_nonce_readback_remain_failed_and_cannot_resume_paid(evidence_scene, monkeypatch):
    from generic_agents import report_evidence_preparation as preparation
    from generic_agents.worker import process_once
    scene = evidence_scene; real_build = pipeline.build_preview
    provider = SyntheticProvider(scene, monkeypatch, mode='invalid_map_kind')
    # This only models the historical V1 producer in an empty synthetic DB.
    # Original source, lease, gateway, journal and fee validators remain real.
    with monkeypatch.context() as old:
        def build_v1(*args, **kwargs):
            return real_build(*args, **{**kwargs, 'generation_version': preparation.GENERATION_V1})
        old.setattr(pipeline, 'build_preview', build_v1)
        old.setattr(pipeline, 'CURRENT_MAP_GENERATION', preparation.GENERATION_V1)
        _, command, accepted = start(scene)
        assert process_once(accepted['draft']['job_id'])
        failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'failed' and len(provider.calls) == 1
    assert failed['maps'][0]['raw_response_saved'] and failed['maps'][0]['result'] is None
    assert all(not stage['raw_response_saved'] for stage in failed['maps'][1:])
    identity = failed['id']; before = counts()
    with Session(engine()) as db:
        original = db.get(SetupReportDraft, identity)
        state_before = copy.deepcopy(original.data)
        call_before = (db.get(CloudCall, provider.calls[0]).status,
            db.get(CloudCall, provider.calls[0]).charged_tokens, copy.deepcopy(db.get(CloudCall, provider.calls[0]).data))
        descriptor = pipeline._descriptor(original, 'map-1')
        wire_before = pipeline.journal.read_response(pipeline._journal_root(),
            pipeline.journal_binding(original, 'map-1', provider.calls[0]), descriptor)['content']
    assert get(scene, identity) == failed
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].get(BASE + '/by-request/' + scene['body']['request_id']))['draft'] == failed
    resume = reply(scene['client'].post(BASE + '/' + identity + '/resume-preview', json={
        'revision': failed['revision'], 'request_id': 'fes_' + str(uuid4())}), 409)
    assert resume['detail']['code'] == 'evidence_task_version_changed'
    reduce = reply(scene['client'].post(BASE + '/' + identity + '/reduce-preview', json={
        'revision': failed['revision'], 'request_id': 'fer_' + str(uuid4())}), 409)
    assert reduce['detail']['code'] == 'evidence_task_version_changed'
    recovered = reply(scene['client'].post(BASE + '/' + identity + '/recover-saved', json={
        'revision': failed['revision'], 'request_id': str(uuid4())}), 409)
    assert recovered['detail']['code'] == 'evidence_saved_response_incomplete'
    assert counts() == before and len(provider.calls) == 1
    with Session(engine()) as db:
        original = db.get(SetupReportDraft, identity); call = db.get(CloudCall, provider.calls[0])
        assert original.data == state_before and original.status == 'failed'
        assert (call.status, call.charged_tokens, call.data) == call_before
        assert pipeline.journal.read_response(pipeline._journal_root(),
            pipeline.journal_binding(original, 'map-1', provider.calls[0]), descriptor)['content'] == wire_before
