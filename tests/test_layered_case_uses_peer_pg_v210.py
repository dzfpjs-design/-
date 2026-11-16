"""Independent real-reference coverage, recovery and provenance adversaries.

Root alone runs these on disposable PostgreSQL. Successful sources, Cases,
Uses, parsing and ACKs are all real normal APIs/workers. Deliberate corruption
is confined to negative tests; no successful receipt or model call is stubbed.
"""
import copy
import hashlib
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, runtime_documents as native
from generic_agents import runtime_document_receipts as delivery
from generic_agents import runtime_task_documents as sources
from generic_agents import layered_case_uses_v210 as uses, layered_case_use_origin_v210 as origin
from generic_agents.db import engine
from tests.test_business_data_gateway_pg import scene
from tests.test_layered_cases_pg_v209 import accepted_case_source, change as change_case
from tests.test_layered_case_uses_pg_v210 import (
    case_use_target, create_use, ready_use, stage_reference, source_inventory)
from tests.test_runtime_documents_pg_v205 import run

BASE = '/api/workspace/layered-case-uses-v210'
CASES = '/api/workspace/layered-cases-v209'


def good(response, status=200):
    assert response.status_code == status, response.text[:1400]
    return response.json()


def submit_source(f, selectors):
    context = good(f.author_client.get('/api/runtime-task-documents/tasks/' + f.target_task_id + '/context'))
    port = context['input_ports'][0]
    previous = context['source_set_heads'][port['key']]
    return f.author_client.post('/api/runtime-task-documents/tasks/' + f.target_task_id + '/source-sets', json={
        'request_id': str(uuid4()), 'expected_task_version': context['task_version'],
        'expected_approval_version': context['approval_version'],
        'prior_set_id': previous['source_set_id'] if previous else None,
        'prior_set_hash': previous['source_set_hash'] if previous else None,
        'input_key': port['key'], 'selectors': selectors})


def multiline_case(f):
    context = good(f.author_client.get(CASES + '/tasks/' + f.task_id + '/context'))
    value = copy.deepcopy(f.source_case['revision']['value'])
    value['experience'] = '第一段😀\n' + '😀' * 1301
    created = good(f.author_client.post(CASES + '/tasks/' + f.task_id + '/cases', json={
        'request_id': str(uuid4()), 'expected_source_task_version': context['task_version'],
        'source_fingerprint': context['source_fingerprint'], 'value': value}), 201)['case']
    submitted = good(change_case(f.author_client, created, 'submit'))['case']
    return good(change_case(f.reviewer_client, submitted, 'review', decision='approve'))['case']


def test_true_multiline_astral_reference_requires_every_block_and_nonoverlapping_chunk(case_use_target):
    f = case_use_target
    f.source_case = multiline_case(f)
    original = source_inventory(f)
    _, _, result = create_use(f)
    use = ready_use(f, result)
    raw = f.author_client.get(use['document']['original_url']).content.decode('utf-8')
    for value in f.source_case['revision']['value'].values():
        assert value in raw
    assert '\n' in f.source_case['revision']['value']['experience']
    groups = {}
    for index, selector in enumerate(use['selectors']):
        groups.setdefault(selector['block_id'], []).append(index)
    pieces = next(indices for indices in groups.values() if len(indices) == 2)
    first, second = (use['selectors'][index] for index in pieces)
    assert first['char_span'] == {'start': 0, 'end': 1200}
    assert second['char_span'] == {'start': 1200, 'end': 1301}
    assert len(use['selectors']) <= 20

    missing = copy.deepcopy(use['selectors'][:-1])
    duplicate = copy.deepcopy(use['selectors'])
    duplicate[-1] = copy.deepcopy(duplicate[-2])
    overlap = copy.deepcopy(use['selectors'])
    overlap[pieces[1]]['char_span']['start'] -= 1
    for invalid in (missing, duplicate, overlap):
        response = submit_source(f, invalid)
        assert response.status_code == 409, response.text[:1400]
    saved = good(submit_source(f, copy.deepcopy(use['selectors'])), 201)
    assert len(saved['entries']) == len(use['selectors'])
    assert good(f.author_client.get('/api/runtime-task-documents/source-sets/' + saved['id'])) == saved
    assert source_inventory(f) == original


@pytest.mark.parametrize('pause_first', [False, True])
def test_withdraw_retains_historical_source_and_stops_new_approval_even_after_pause(case_use_target, pause_first):
    f = case_use_target
    _, _, result = create_use(f)
    use = ready_use(f, result)
    source = stage_reference(f, use)
    original = source_inventory(f)
    if pause_first:
        instance = next(item for item in good(f.author_client.get('/api/instances'))
            if item['id'] == f.release['instance_id'])
        good(f.author_client.post('/api/instances/' + instance['id'] + '/pause', json={'version': instance['version']}))
    withdrawn = good(f.author_client.post(BASE + '/uses/' + use['id'] + '/withdraw', json={
        'request_id': str(uuid4()), 'expected_use_version': use['version'],
        'reason': '停止新的参考采用，已保存来源和原件保持可核对'}))
    assert withdrawn['status'] == 'withdrawn'
    assert good(f.author_client.get('/api/runtime-task-documents/source-sets/' + source['id'])) == source
    current = good(f.author_client.get(f.target_approval_path))
    value = copy.deepcopy(current['value'])
    value['graph_inputs'][source['input_key']] = source['ref']
    response = f.author_client.post(f.target_approval_path + '/revise', json={
        'request_id': str(uuid4()), 'expected_version': current['version'],
        'reason': '核对已撤回来源不能重新用于本轮审批', 'value': value})
    assert response.status_code == 409, response.text[:1400]
    if not pause_first:
        assert '案例参考已撤回' in response.text
    assert source_inventory(f) == original


def test_other_operator_can_retry_the_real_cancelled_native_job_without_breaking_use(case_use_target):
    f = case_use_target
    _, _, result = create_use(f)
    use = result['use']
    version_id = use['document']['version_id']
    original = source_inventory(f)
    with Session(engine()) as db:
        original_job_id = db.get(native.Version, version_id).data['origin']['job_id']
    version = good(f.author_client.get('/api/runtime-documents/versions/' + version_id))
    good(f.author_client.post('/api/runtime-documents/versions/' + version_id + '/cancel', json={
        'request_id': str(uuid4()), 'expected_version': version['version'],
        'reason': '先取消原本地解析以核对授权恢复路径'}))
    assert run(f.s, version_id) == 'cancelled'  # The real original queued Job settles the cancellation.
    version = good(f.reviewer_client.get('/api/runtime-documents/versions/' + version_id))
    assert version['can_retry']
    good(f.reviewer_client.post('/api/runtime-documents/versions/' + version_id + '/retry', json={
        'request_id': str(uuid4()), 'expected_version': version['version'],
        'reason': '现任有权账号沿同一原Job恢复真实本地解析'}))
    assert run(f.s, version_id) == 'succeeded'
    delivery.consume_once(f.s.workspace)
    ready = good(f.author_client.get(BASE + '/uses/' + use['id']))
    assert ready['stage']['code'] == 'ready' and ready['selectors'] and ready['document']['ack_verified']
    with Session(engine()) as db:
        version = db.get(native.Version, version_id)
        job = db.get(m.Job, original_job_id)
        assert version.data['origin']['job_id'] == job.id
        assert job.data['actor_id'] == f.reviewer_id and job.data['runtime_document_authorization_id']
        assert version.actor_id != job.data['actor_id']
    assert source_inventory(f) == original


def test_orphan_upload_and_generation_traces_never_rebuild_a_missing_use(case_use_target):
    f = case_use_target
    body, response, result = create_use(f)
    original = source_inventory(f)
    identity = result['use_id']
    with Session(engine()) as db:
        commands = list(db.scalars(select(uses.Command).where(uses.Command.use_id == identity)))
        for command in commands:
            db.delete(command)
        db.flush()
        db.delete(db.get(uses.Use, identity))
        db.commit()
    with Session(engine()) as db:
        before = tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == f.s.workspace))
            for model in (native.Version, m.Job, m.Evidence, uses.Use, uses.Command))
    recovery = f.author_client.get(BASE + '/requests/' + body['request_id'], params={
        'expected_sha256': hashlib.sha256(response.request.content).hexdigest()})
    assert recovery.status_code == 409
    assert f.author_client.post(BASE + '/tasks/' + f.target_task_id + '/uses',
        content=response.request.content, headers={'Content-Type': 'application/json'}).status_code == 409
    assert f.author_client.get(BASE + '/tasks/' + f.target_task_id + '/context').status_code == 409
    with Session(engine()) as db:
        after = tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == f.s.workspace))
            for model in (native.Version, m.Job, m.Evidence, uses.Use, uses.Command))
    assert after == before
    assert source_inventory(f) == original


def test_complete_use_proof_is_memoized_per_scope_but_cycle_or_changed_row_is_not_skipped(case_use_target, monkeypatch):
    f = case_use_target
    _, _, result = create_use(f)
    use = ready_use(f, result)
    checked_cases = []
    actual = origin.approved_case

    def observe(*args, **kwargs):
        checked_cases.append(args[2])
        return actual(*args, **kwargs)

    monkeypatch.setattr(origin, 'approved_case', observe)  # Observe the real verifier; never return a stub.
    with Session(engine()) as db:
        task = db.get(m.Task, f.target_task_id)
        row = db.get(uses.Use, use['id'])
        with native.verification_scope(db), origin.verification_scope(db) as walk:
            entries = [{'id': 's' + str(index), 'kind': 'native', **sources.resolve(
                db, f.s.p, selector, f.release['instance_id'], task=task)}
                for index, selector in enumerate(use['selectors'], 1)]
            proof = origin.checked_coverage(db, f.s.p, task, entries)
            assert len(proof) == 1 and proof[0]['use_id'] == use['id']
            assert checked_cases == [f.source_case['id']]
            with walk.entering(row.id), pytest.raises(HTTPException) as error:
                origin.checked_use(db, f.s.p, row)
            assert error.value.status_code == 409
            row.version += 1
            with db.no_autoflush, pytest.raises(HTTPException) as changed:
                origin.checked_use(db, f.s.p, row)
            assert changed.value.status_code == 409
        assert 'layered_case_use_verification_v210' not in db.info
