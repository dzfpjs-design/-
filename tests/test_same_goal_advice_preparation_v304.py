"""Real preparation custody/control flow over explicit lease/source/file IO.

Actual Plan snapshots, ACCEPTED/Job references, SQL predicates, independent
upload/preparation Audits, artifact hashes and cold/view gates run. Installed
fence, live released scope, source proof and filesystem writes are declared
IO spies. This does not claim PG, worker commits or product durability.
"""
import copy
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_preparation_v304 as s
from generic_agents.contracts import digest
from generic_agents.models import CloudCall
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene, ref

AT = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
ACTUAL_FENCED = s.leases.fenced


@pytest.fixture
def scene(monkeypatch, request):
    template = contract_scene(); prep = copy.deepcopy(template.preparation); scope = prep['scope']
    raw = '明确批准的合成摘录😀；仅本地准备，不代表事实确认。'.encode('utf-8')
    sha = hashlib.sha256(raw).hexdigest(); kind = getattr(request, 'param', 'source_set')
    original_id = ref('original_packet_evidence')['id']; packet_id = ref('original_packet')['id']
    source_ref = prep['request']['document_source']['source_set_ref']
    if kind == 'packet':
        prep['request']['document_source'] = {'kind': 'document_packet_v207', 'packet': {
            'id': packet_id, 'version': 1, 'evidence_id': original_id, 'sha256': sha, **source_ref}}
        prep['request_hash'] = digest(prep['request'])
        prep = s.c.seal_preparation({key: value for key, value in prep.items() if key != 'record_hash'}, manifest=template.manifest)
    p = NS(workspace_id=scope['workspace_id'], user_id=prep['data_actor_id'], role='operator')
    task = s.Task(id=scope['task_id'], workspace_id=p.workspace_id, package_id=scope['package_id'],
        owner_id=prep['requested_by'], status='workflow_active', version=1, data={'retained_original_task': True})
    cap = s.store.Capability(id=prep['advice_capability_ref']['id'], workspace_id=p.workspace_id,
        content_hash=digest(template.manifest), validated_hash=digest(template.manifest), release_version='1.0.0',
        kind='tool', status='published', data=template.manifest)
    plan_id = scope['analysis_plan_id']; job_id = ref('actual_preparation_job')['id']; key = s.store._key(task.id, plan_id)
    audit_id = ref('actual_accepted')['id']; token = ref('actual_t1')['id']
    job = s.Job(id=job_id, workspace_id=p.workspace_id, kind=s.c.PREPARE_KIND, status='running',
        attempts=1, lease_token=token, lease_until=AT + timedelta(minutes=3), idempotency_key=key,
        version=1, data=s.store._job_data(plan_id, prep, key))
    plan = s.Plan(id=plan_id, workspace_id=p.workspace_id, task_id=task.id, status='queued', version=1,
        data={'schema': s.c.PLAN, 'preparation_snapshot': prep,
            'jobs': {'prepare': {'id': job.id, 'kind': job.kind, 'schema': s.store.JOB_SCHEMA, 'key': key}},
            'operations': [{'operation': 'prepare', 'request_id': prep['request']['request_id'],
                'body': copy.deepcopy(prep['request']), 'body_hash': prep['request_hash'],
                'actor_id': prep['requested_by'], 'audit_id': audit_id}], 'document_preparation': None, 'reviews': []})
    accepted = s.Audit(id=audit_id, workspace_id=p.workspace_id, actor_id=prep['requested_by'],
        action=s.store.ACCEPTED, object_id=plan.id, data=s.store._accepted(plan.id, prep, job.id, key))
    approved = {**source_ref, 'submission_id': ref('actual_source_submission')['id'],
        'submission_hash': '9' * 64, 'input_key': 'runtime_document_sources'}
    packet_receipt = {'packet_id': packet_id, 'evidence_id': original_id, 'sha256': sha, 'task_id': task.id,
        'producer_job_id': ref('original_packet_job')['id'], 'invocation_id': ref('original_packet_call')['id'],
        **source_ref, 'producer_hash': 'b' * 64} if kind == 'packet' else None
    evidence_ref = {'evidence_id': original_id, 'sha256': sha, 'size_bytes': len(raw), 'media_type': 'text/plain'} if kind == 'packet' else None
    resolved = {'selection': copy.deepcopy(prep['request']['document_source']), 'approved_selection': approved,
        'source_set': {'source_snapshot_hash': 'a' * 64}, 'content': raw, 'sha256': sha, 'size_bytes': len(raw),
        'packet_receipt': packet_receipt, 'evidence_ref': evidence_ref}
    state = NS(p=p, task=task, plan=plan, job=job, prep=prep, template=template, resolved=resolved,
        rows={s.Task: [task], s.Plan: [plan], s.Job: [job], s.Audit: [accepted], s.Evidence: [],
            # This fixture has no paid execution or CloudCall. Keep the real
            # scoped orphan inventory query, with an explicit empty IO table.
            s.store.Capability: [cap], s.store.Invocation: [], CloudCall: [], s.sources.packets.Packet: []}, contents={}, locks=[], flushes=0,
        source_reads=0, saves=0, nonce_calls=[], role_valid=True, scope_valid=True, source_valid=True, lease_valid=True)
    state.external_command_savepoint = False
    class DB:
        info = {'worker_lease': {'job_id': job.id, 'lease_token': token}}
        protected = 0
        @property
        def no_autoflush(self):
            @contextmanager
            def protect():
                self.protected += 1
                try: yield
                finally: self.protected -= 1
            return protect()
        def scalars(self, statement):
            assert self.protected
            model = statement.column_descriptions[0]['entity']; assert model in state.rows
            if statement._for_update_arg is not None: state.locks.append((model, statement.compile().params))
            return [item for item in state.rows[model] if predicate(statement.whereclause, item)]
        def scalar(self, statement):
            assert self.protected
            if statement.column_descriptions[0]['entity'] is s.Task:
                rows = self.scalars(statement); return rows[0] if len(rows) == 1 else None
            assert statement.column_descriptions[0]['expr'] is s.Job.id
            assert 'clock_timestamp' in str(statement) and statement._for_update_arg is None
            params = statement.compile().params
            assert p.workspace_id in params.values() and job.id in params.values() and p.user_id in params.values()
            assert self.info['worker_lease']['lease_token'] in params.values() and 'running' in params.values()
            return job.id if (state.lease_valid and job.status == 'running'
                and job.lease_token == self.info['worker_lease']['lease_token'] and job.lease_until > AT) else None
        def add(self, item):
            assert type(item) in state.rows and item.id is not None
            state.rows[type(item)].append(item)
        def flush(self): state.flushes += 1
        def commit(self): pytest.fail('Preparation handler tried an intermediate commit')
        @contextmanager
        def begin_nested(self):
            if not state.external_command_savepoint: pytest.fail('Preparation handler tried a hidden flush/savepoint')
            yield  # Only the separately invoked original cancel command owns this boundary.
    state.db = DB()
    def reader(db, principal, *, write=False):
        assert db is state.db and principal is p; s.need(state.role_valid)
    def current_scope(db, principal, actual_task, body, identity):
        assert db is state.db and principal is p and actual_task is task and body == prep['request'] and identity == plan.id
        s.need(state.scope_valid)
        return {**{key: copy.deepcopy(value) for key, value in prep.items()
            if key not in {'schema', 'request', 'request_hash', 'requested_by', 'record_hash'}}, 'manifest': template.manifest}
    def source(db, principal, actual_task, selection):
        assert db is state.db and principal is p and actual_task is task and selection == prep['request']['document_source']
        state.source_reads += 1; s.need(state.source_valid); return copy.deepcopy(state.resolved)
    def save(db, principal, content, media_type):
        assert db is state.db and principal is p and content == raw and media_type == 'text/plain'
        state.saves += 1; identifier = ref('new_document_evidence_' + str(state.saves))['id']
        file = s.Evidence(id=identifier, workspace_id=p.workspace_id, version=1, sha256=sha,
            size_bytes=len(raw), media_type=media_type, data={'uploaded_by': p.user_id, 'cloud_model_ids': []})
        state.rows[s.Evidence].append(file); state.contents[identifier] = content
        state.rows[s.Audit].append(s.Audit(id=ref('actual_upload_' + str(state.saves))['id'], workspace_id=p.workspace_id,
            actor_id=p.user_id, action='evidence.upload', object_id=identifier,
            data={'media_type': media_type, 'sha256': sha, 'cloud_model_id': None, 'consent': False}))
        return file
    def read(db, principal, identifier):
        assert db is state.db and principal is p
        files = [file for file in state.rows[s.Evidence] if file.id == identifier and file.workspace_id == p.workspace_id]
        s.need(len(files) == 1); return files[0], state.contents[identifier]
    monkeypatch.setattr(s.store, '_reader', reader)  # Actual current membership IO.
    monkeypatch.setattr(s.store, '_scope', current_scope)  # Actual release/Goal/head/quota/live original roles IO.
    monkeypatch.setattr(s, 'lock_current_actor', lambda db, principal: s.need(state.role_valid))
    monkeypatch.setattr(s.store, 'lock_current_actor', lambda db, principal: s.need(state.role_valid))
    def nonce_io(db, principal, *, action, task_id, body, subject_id=None, advice_id=None):
        # Only these explicit normal cancellation commands enter the new PG
        # nonce-lock/foreign-original IO; local runner/cold reads never do.
        assert db is state.db and principal is p and task_id == task.id and subject_id == plan.id
        assert action == s.store.CANCELLED and advice_id is None and type(body) is dict and 'request_id' in body
        state.nonce_calls.append({'action': action, 'task_id': task_id, 'subject_id': subject_id,
            'body': copy.deepcopy(body), 'advice_id': advice_id})
    monkeypatch.setattr(s.store.command_nonce, 'acquire', nonce_io)
    monkeypatch.setattr(s.sources, 'resolve_documents', source)  # Full approved SourceSet/V207 producer source IO.
    monkeypatch.setattr(s.leases, 'fenced', lambda db: db is state.db)  # Installed transaction listener IO.
    monkeypatch.setattr(s.evidence, 'save_evidence', save)  # Actual immutable filesystem/SQL registration IO.
    monkeypatch.setattr(s.evidence, 'evidence_bytes', read)  # Actual original file read; byte acceptance runs.
    if kind == 'packet':
        file = s.Evidence(id=original_id, workspace_id=p.workspace_id, version=3, sha256=sha,
            size_bytes=len(raw), media_type='text/plain', data={'uploaded_by': p.user_id,
                'cloud_model_ids': [prep['model_pin']['id']], 'original_packet_metadata': True})
        state.rows[s.Evidence].append(file); state.contents[file.id] = raw
        state.rows[s.sources.packets.Packet].append(s.sources.packets.Packet(id=packet_id, workspace_id=p.workspace_id,
            task_id=task.id, actor_id=p.user_id, evidence_id=file.id))
        state.rows[s.Audit].append(s.Audit(id=ref('original_packet_upload')['id'], workspace_id=p.workspace_id,
            actor_id=p.user_id, action='evidence.upload', object_id=file.id,
            data={'media_type': 'text/plain', 'sha256': sha, 'cloud_model_id': None, 'consent': False}))
    return state


def complete(value):
    assert s.run(value.db, value.p, value.job) == 'succeeded'
    value.job.status = 'succeeded'; value.job.lease_until = None  # Explicit real-worker terminal IO boundary.
    return s.cold_read(value.db, value.p, value.task, value.plan)


@pytest.mark.parametrize('scene', ['source_set', 'packet'], indirect=True)
def test_actual_sealed_plan_and_receipts_prepare_once_without_changing_original_task_or_granting_cloud(scene):
    x = scene; before = copy.deepcopy(x.task.data); prep = copy.deepcopy(x.prep); original_files = copy.deepcopy([file.data for file in x.rows[s.Evidence]])
    result = complete(x)
    assert x.nonce_calls == []
    assert [model for model, _ in x.locks[:3]] == [s.Task, s.Plan, s.Job]
    assert x.plan.status == 'awaiting_consent' and x.plan.data['preparation_snapshot'] == prep and x.task.data == before
    assert result['content'] == x.resolved['content'] and result['marker'] == x.plan.data['document_preparation']
    assert len([row for row in x.rows[s.Audit] if row.action == s.PREPARED]) == 1
    if x.resolved['selection']['kind'] == 'approved_source_set':
        assert x.saves == 1 and x.rows[s.Evidence][0].data['cloud_model_ids'] == []
        assert result['document']['receipt_ref'] == result['receipt_ref']
    else:
        assert x.saves == 0 and [file.data for file in x.rows[s.Evidence]] == original_files
        assert result['document']['receipt_ref'] == {'id': x.resolved['packet_receipt']['packet_id'],
            'record_hash': x.resolved['packet_receipt']['producer_hash']}
        assert result['document']['receipt_ref'] != result['receipt_ref']
    result['receipt']['selection']['kind'] = 'caller-mutated'
    assert s.cold_read(x.db, x.p, x.task, x.plan)['receipt']['selection'] == prep['request']['document_source']


def test_stored_running_artifact_is_not_user_ready_and_same_job_reclaim_reuses_original_creator_receipt(scene):
    x = scene; assert s.run(x.db, x.p, x.job) == 'succeeded'
    marker = copy.deepcopy(x.plan.data['document_preparation']); original = copy.deepcopy(x.plan.data['preparation_snapshot'])
    with pytest.raises(HTTPException): s.cold_read(x.db, x.p, x.task, x.plan)
    with x.db.no_autoflush:
        assert s.store.get(x.db, x.p, x.task.id, x.plan.id)['phase'] == 'preparing'
    x.job.lease_token = ref('new_t2')['id']; x.db.info['worker_lease']['lease_token'] = x.job.lease_token
    assert s.run(x.db, x.p, x.job) == 'succeeded' and x.saves == 1
    assert x.plan.data['document_preparation'] == marker and x.plan.data['preparation_snapshot'] == original
    x.job.status = 'succeeded'; x.job.lease_until = None
    result = s.cold_read(x.db, x.p, x.task, x.plan)
    assert result['receipt']['creator_lease_hash'] != digest(x.job.lease_token)
    with x.db.no_autoflush:
        assert s.store.get(x.db, x.p, x.task.id, x.plan.id)['phase'] == 'awaiting_consent'


@pytest.mark.parametrize('retained', [False, True])
def test_only_complete_retained_artifact_can_finish_original_job_after_head_or_quota_expiry(scene, monkeypatch, retained):
    x = scene
    if retained:
        assert s.run(x.db, x.p, x.job) == 'succeeded'
        x.job.lease_token = ref('reclaimed_after_head_change')['id']
        x.db.info['worker_lease']['lease_token'] = x.job.lease_token
    before = copy.deepcopy(x.plan.data); version = x.plan.version
    counts = {model: len(rows) for model, rows in x.rows.items()}; checks = []
    def expired(*args):
        checks.append('current_head_or_policy')
        raise HTTPException(409, 'Explicit current head changed or quota expired')
    monkeypatch.setattr(s.store, '_scope', expired)
    if retained:
        assert s.run(x.db, x.p, x.job) == 'succeeded'
        assert checks == [] and x.saves == 1
        x.job.status = 'succeeded'; x.job.lease_until = None
        assert s.cold_read(x.db, x.p, x.task, x.plan)['marker'] == before['document_preparation']
    else:
        with pytest.raises(HTTPException): s.run(x.db, x.p, x.job)
        assert checks == ['current_head_or_policy'] and x.saves == 0
    assert x.plan.data == before and x.plan.version == version
    assert {model: len(rows) for model, rows in x.rows.items()} == counts


def test_orphan_retained_artifact_cannot_fall_through_to_current_scope_and_create_again(scene, monkeypatch):
    x = scene; assert s.run(x.db, x.p, x.job) == 'succeeded'
    x.rows[s.Audit] = [event for event in x.rows[s.Audit] if event.action != s.PREPARED]
    monkeypatch.setattr(s.store, '_scope', lambda *args: pytest.fail('Orphan artifact fell through into creation'))
    with pytest.raises(HTTPException): s.run(x.db, x.p, x.job)
    assert x.saves == 1 and len(x.rows[s.Evidence]) == 1


@pytest.mark.parametrize('fault', ['fence_dict_only', 'expired_lease', 'wrong_token', 'wrong_actor', 'bad_kind',
    'missing_accepted', 'changed_snapshot', 'goal_head_or_release_changed', 'withdrawn_role', 'bad_original_source'])
def test_no_evidence_is_saved_without_original_custody_current_scope_and_actual_fence(scene, monkeypatch, fault):
    x = scene
    if fault == 'fence_dict_only': monkeypatch.setattr(s.leases, 'fenced', ACTUAL_FENCED)
    elif fault == 'expired_lease': x.lease_valid = False
    elif fault == 'wrong_token': x.db.info['worker_lease']['lease_token'] = ref('other_token')['id']
    elif fault == 'wrong_actor': x.p.user_id = ref('other_actor')['id']
    elif fault == 'bad_kind': x.job.kind = s.c.SEND_KIND
    elif fault == 'missing_accepted': x.rows[s.Audit].clear()
    elif fault == 'changed_snapshot': x.prep['instruction'] = 'changed outside accepted snapshot'
    elif fault == 'goal_head_or_release_changed': x.scope_valid = False
    elif fault == 'withdrawn_role': x.role_valid = False
    else: x.source_valid = False
    with pytest.raises(HTTPException): s.run(x.db, x.p, x.job)
    assert x.saves == 0 and x.plan.data['document_preparation'] is None


@pytest.mark.parametrize('fault', ['missing_receipt', 'missing_marker', 'orphan_evidence', 'missing_evidence',
    'missing_upload', 'duplicate_receipt', 'receipt_rehashed', 'wrong_upload_actor', 'bad_bytes', 'source_changed'])
def test_retained_database_artifact_needs_full_independent_proof_and_never_silently_saves_again(scene, fault):
    x = scene; complete(x)
    receipt = next(row for row in x.rows[s.Audit] if row.action == s.PREPARED)
    if fault == 'missing_receipt': x.rows[s.Audit].remove(receipt)
    elif fault == 'missing_marker': x.plan.data['document_preparation'] = None; x.plan.status = 'running'
    elif fault == 'orphan_evidence':
        x.rows[s.Audit].remove(receipt); x.plan.data['document_preparation'] = None; x.plan.status = 'running'
    elif fault == 'missing_evidence': x.rows[s.Evidence].clear()
    elif fault == 'missing_upload': x.rows[s.Audit] = [row for row in x.rows[s.Audit] if row.action != 'evidence.upload']
    elif fault == 'duplicate_receipt':
        x.rows[s.Audit].append(s.Audit(id=ref('duplicate_receipt')['id'], workspace_id=x.p.workspace_id,
            action=s.PREPARED, object_id=x.plan.id, actor_id=x.p.user_id, data=copy.deepcopy(receipt.data)))
    elif fault == 'receipt_rehashed':
        receipt.data['approved_selection']['submission_hash'] = 'f' * 64
        receipt.data['record_hash'] = s.c.record_hash(receipt.data)
        x.plan.data['document_preparation'] = s._marker(receipt.data)
    elif fault == 'wrong_upload_actor': next(row for row in x.rows[s.Audit] if row.action == 'evidence.upload').actor_id = ref('wrong_actor')['id']
    elif fault == 'bad_bytes': x.contents[x.rows[s.Evidence][0].id] += b'corrupt'
    else: x.source_valid = False
    with pytest.raises(HTTPException): s.cold_read(x.db, x.p, x.task, x.plan)
    x.job.status = 'running'; x.job.lease_until = AT + timedelta(minutes=3)
    with pytest.raises(HTTPException): s.run(x.db, x.p, x.job)
    assert x.saves == 1


def test_current_consent_change_is_not_a_historical_artifact_drift_or_a_preparation_grant(scene):
    x = scene; result = complete(x); file = x.rows[s.Evidence][0]
    file.version += 1; file.data = {**file.data, 'cloud_model_ids': [x.prep['model_pin']['id']]}
    assert s.cold_read(x.db, x.p, x.task, x.plan)['marker'] == result['marker']
    x.scope_valid = False  # Cold history does not use a later live head/release gate.
    assert s.cold_read(x.db, x.p, x.task, x.plan)['content'] == result['content']
    x.role_valid = False
    with pytest.raises(HTTPException): s.cold_read(x.db, x.p, x.task, x.plan)


def test_succeeded_without_artifact_is_never_misreported_as_awaiting_consent(scene):
    x = scene; x.job.status = 'succeeded'; x.job.lease_until = None
    with pytest.raises(HTTPException): s.cold_read(x.db, x.p, x.task, x.plan)
    assert not x.rows[s.Evidence] and x.saves == 0


def test_official_cancel_before_claim_is_not_a_failed_or_successful_preparation(scene):
    x = scene
    body = {'request_id': ref('cancel_before_claim')['id'], 'expected_plan_version': x.plan.version,
        'reason': '取消尚未开始的准备，保留原任务'}
    x.external_command_savepoint = True
    with x.db.no_autoflush:
        assert s.store.cancel(x.db, x.p, x.task.id, x.plan.id, body)['phase'] == 'cancelled'
    x.external_command_savepoint = False
    assert x.nonce_calls == [{'action': s.store.CANCELLED, 'task_id': x.task.id,
        'subject_id': x.plan.id, 'body': body, 'advice_id': None}]
    before = copy.deepcopy(x.plan.data)
    assert s.run(x.db, x.p, x.job) == 'cancelled'
    assert x.saves == 0 and x.source_reads == 0 and x.plan.data == before


def test_failed_worker_bookkeeping_uses_original_fence_without_granting_a_recovery_viewer_or_rewriting_artifacts(scene):
    x = scene; assert s.run(x.db, x.p, x.job) == 'succeeded'
    before = copy.deepcopy(x.plan.data); old_version = x.plan.version
    x.p.role = 'viewer'; x.role_valid = False; x.scope_valid = False
    assert s.fail_job(x.db, x.p, x.job, 'generic worker failure') == 'failed'
    assert x.plan.status == 'failed' and x.plan.version == old_version + 1 and x.plan.data == before
    assert s.fail_job(x.db, x.p, x.job, 'same original failure') == 'failed'
    assert x.plan.version == old_version + 1 and x.saves == 1
    x.db.info['worker_lease']['lease_token'] = ref('not_owned')['id']
    with pytest.raises(HTTPException): s.fail_job(x.db, x.p, x.job, 'cannot overwrite without lease')


def test_completed_documents_remain_readable_after_a_normal_cancellation_without_becoming_a_new_send_grant(scene):
    x = scene; original = complete(x); body = {'request_id': ref('cancel_after_completion')['id'],
        'expected_plan_version': x.plan.version, 'reason': '保留已保存原件，取消后续外发'}
    x.external_command_savepoint = True
    with x.db.no_autoflush:
        view = s.store.cancel(x.db, x.p, x.task.id, x.plan.id, body)
    x.external_command_savepoint = False
    assert view['phase'] == 'cancelled' and view['cloud_send_available'] is False
    assert s.cold_read(x.db, x.p, x.task, x.plan)['marker'] == original['marker']
