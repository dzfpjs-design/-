"""Real wait/audit/file logic with explicit synthetic registry/source/SQL seams.

Root executes. No provider, reservation, new Job or Invocation is available.
"""
import copy
import hashlib
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from generic_agents import models as m, evidence, native_goals, relations, workflow_graph
from generic_agents import workflow_evidence_consent_wait_v199 as subject
from generic_agents.contracts import digest
from tests.test_business_reasoning_v197 import manifest
from tests.test_workflow_provenance_wait_v197 import scene as old_scene, IO as OldIO, consumer_inputs
from tests.test_workflow_task_context_v196 import identity, same_sql
from tests.test_report_business_flow_wire_v184 import no_external


class IO(OldIO):
    def scalar(self, statement):
        s = self.scene
        choices = [(select(m.User).where(m.User.id == s.p.user_id).execution_options(populate_existing=True), s.user),
            (select(m.Membership).where(m.Membership.workspace_id == s.p.workspace_id,
                m.Membership.user_id == s.p.user_id).execution_options(populate_existing=True), s.member)]
        for cap in (s.cap, s.model):
            choices.append((select(m.Capability).where(m.Capability.workspace_id == s.p.workspace_id,
                m.Capability.id == cap.id).execution_options(populate_existing=True), cap))
        for file in s.files:
            choices.append((select(m.Evidence).where(m.Evidence.workspace_id == s.p.workspace_id,
                m.Evidence.id == file.id).execution_options(populate_existing=True), file))
        choices.extend([(select(m.Job).where(m.Job.workspace_id == s.p.workspace_id, m.Job.id == s.job.id,
            m.Job.kind == subject.scope.KIND, m.Job.status == subject.PHASE).with_for_update()
            .execution_options(populate_existing=True), s.job if s.job.status == subject.PHASE else None)])
        for query, result in choices:
            if same_sql(statement, query): return result
        return super().scalar(statement)

    def scalars(self, statement):
        s = self.scene
        query = select(m.Audit).where(m.Audit.workspace_id == s.job.workspace_id,
            m.Audit.object_id == s.job.id, m.Audit.action.in_([subject.WAIT, subject.WAKE]))
        if same_sql(statement, query):
            return [v for v in self.events if v.object_id == s.job.id and v.action in {subject.WAIT, subject.WAKE}]
        for file in s.files:
            query = select(m.Audit).where(m.Audit.workspace_id == file.workspace_id,
                m.Audit.object_id == file.id, m.Audit.action.in_(['evidence.upload', 'evidence.cloud_authorize']))
            if same_sql(statement, query): return [v for v in self.events if v.object_id == file.id and v.action in {'evidence.upload', 'evidence.cloud_authorize'}]
        for workspace in (None, s.p.workspace_id):
            query = select(m.Job).where(m.Job.kind == subject.scope.KIND, m.Job.status == subject.PHASE,
                m.Job.available_at <= func.clock_timestamp()).order_by(m.Job.available_at, m.Job.id).limit(64)
            if workspace is not None: query = query.where(m.Job.workspace_id == workspace)
            if same_sql(statement, query): return [s.job] if s.job.status == subject.PHASE else []
        return super().scalars(statement)


@pytest.fixture
def scene(monkeypatch, tmp_path):
    s = old_scene.__wrapped__(monkeypatch)
    previous = s.db; s.db = IO(); s.db.rows = previous.rows; s.db.scene = s
    s.model = m.Capability(id=identity('consent-model'), workspace_id=s.p.workspace_id, kind='foundation_model',
        status='published', release_version='1.0.0', data=manifest())
    s.model.content_hash = s.model.validated_hash = digest(s.model.data)
    s.cap.data = {**manifest(), 'kind': 'tool', 'name': 'consent_business_reasoning',
        'input_schema': subject.reasoning.input_schema(), 'output_schema': subject.reasoning.output_schema(),
        'dependencies': [s.model.id], 'specification': {'schema': subject.reasoning.CONTRACT, 'instruction': '仅比较已授权真实资料，不替人审批'}}
    s.files = []
    for i in range(2):
        content = ('真实合成文本证据 ' + str(i)).encode('utf-8'); sha = hashlib.sha256(content).hexdigest()
        file = m.Evidence(id=identity('consent-evidence-' + str(i)), workspace_id=s.p.workspace_id,
            version=1, sha256=sha, media_type='text/plain', size_bytes=len(content),
            storage_key=s.p.workspace_id + '/' + sha + '.bin', data={'modality': 'text', 'cloud_model_ids': []})
        path = tmp_path / file.storage_key; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        s.files.append(file)
    monkeypatch.setattr(evidence, 'settings', lambda: NS(storage_dir=tmp_path))
    monkeypatch.setattr(evidence, 'scoped', lambda db, model, identifier, p: next(v for v in s.files if v.id == identifier))
    inputs = {'question': '依据最新真实观测比较方案', 'evidence_ids': [v.id for v in s.files],
        'candidates': [{'candidate_ref': 'proposal_1', 'summary': '待核对提案', 'context': None}]}
    consumer_inputs(s, {'adapter': 'business_reasoning', 'model_profile_id': s.model.id,
        'model_profile_hash': s.model.content_hash, 'side_effect': False}, inputs, subject.reasoning.input_schema())
    s.cap.workspace_id = s.p.workspace_id; s.cap.kind = 'tool'; s.cap.status = 'published'
    s.cap.release_version = '1.0.0'; s.cap.validated_hash = s.cap.content_hash
    s.user = NS(id=s.p.user_id, active=True); s.member = NS(role=s.p.role); s.authority_calls = []
    def published(db, p, identifier, kind=None):
        cap = next(v for v in (s.cap, s.model) if v.id == identifier)
        assert p.workspace_id == s.p.workspace_id
        if cap.status != 'published' or cap.content_hash != digest(cap.data) or cap.validated_hash != cap.content_hash or kind and cap.kind != kind:
            raise HTTPException(409, 'synthetic registry rejected')
        return cap
    monkeypatch.setattr(subject.reasoning, 'published', published)
    def current_actor(db, p):
        if not s.user.active or s.member.role != p.role: raise HTTPException(403, 'synthetic IAM revoked')
        s.authority_calls.append('actor')
    def current_source(*args):
        if getattr(s, 'source_changed', False): raise HTTPException(409, 'synthetic current source changed')
        s.authority_calls.append('source')
    monkeypatch.setattr(relations, 'lock_current_actor', current_actor)
    monkeypatch.setattr(workflow_graph, 'live', current_source)
    monkeypatch.setattr(native_goals, 'guard', lambda *a, **k: s.authority_calls.append('goal'))
    s.wait = lambda: subject.preflight(s.db, s.p, s.task, s.job, s.node, s.cap, s.inputs)
    s.schedule = lambda: subject.scheduler_once(s.db, workspace_id=s.p.workspace_id)
    return s


def grant(s, index=0, *, upload=False):
    file = s.files[index]; file.version += 1; file.data = {**file.data, 'cloud_model_ids': [s.model.id]}
    data = ({'sha256': file.sha256, 'media_type': file.media_type, 'cloud_model_id': s.model.id, 'consent': True}
        if upload else {'model_id': s.model.id, 'sha256': file.sha256, 'version': file.version, 'reason': '明确同意该真实文本发给选定模型'})
    event = m.Audit(id=identity('consent-grant-' + str(index) + '-' + str(file.version)), workspace_id=s.p.workspace_id,
        actor_id=s.p.user_id, action='evidence.upload' if upload else 'evidence.cloud_authorize', object_id=file.id, data=data)
    s.db.events.append(event); return event


def test_actual_wait_freezes_files_before_call_and_only_full_real_consent_wakes_same_job(scene):
    s = scene; original = (s.job.id, s.job.idempotency_key, s.job.attempts, s.job.available_at, s.job.data['inputs'])
    assert s.wait() is True and s.job.status == subject.PHASE
    record = s.job.data[subject.KEY]['history'][0]
    assert record['missing_evidence_ids'] == [v.id for v in s.files]
    assert s.db.present_call is None and s.db.additions == [m.Audit] and s.db.commits == 0
    assert s.schedule() == 0 and s.job.status == subject.PHASE
    grant(s); assert s.schedule() == 0
    grant(s, 1, upload=True); assert s.schedule() == 1 and s.job.status == 'queued'
    assert (s.job.id, s.job.idempotency_key, s.job.attempts, s.job.available_at, s.job.data['inputs']) == original
    assert {'source', 'actor', 'goal'} <= set(s.authority_calls)
    assert [v.action for v in s.db.events if v.object_id == s.job.id] == [subject.WAIT, subject.WAKE]
    s.job.status = 'running'; s.job.attempts = 4
    assert subject.can_start_after_wait(s.db, s.task, s.job) is True
    assert s.wait() is False and s.db.present_call is None


def test_permission_json_without_authorization_audit_is_still_unconsented(scene):
    s = scene
    for file in s.files: file.data['cloud_model_ids'] = [s.model.id]
    assert s.wait() is True and s.schedule() == 0
    assert s.job.status == subject.PHASE and s.db.present_call is None


@pytest.mark.parametrize('fault', ['wait_audit', 'ledger', 'record', 'file_bytes', 'file_sha', 'model_hash', 'model_unpublished', 'source', 'iam', 'input', 'invocation', 'dispatch'])
def test_corrupt_or_revoked_authority_never_wakes_and_is_visibly_blocked(scene, fault):
    s = scene; s.wait(); grant(s); grant(s, 1)
    if fault == 'wait_audit': s.db.events = [v for v in s.db.events if v.action != subject.WAIT]
    elif fault == 'ledger': s.job.data.pop(subject.KEY)
    elif fault == 'record': s.job.data[subject.KEY]['history'][0]['capability_hash'] = 'f' * 64
    elif fault == 'file_bytes':
        path = evidence.settings().storage_dir / s.files[0].storage_key; path.write_bytes(b'changed')
    elif fault == 'file_sha': s.files[0].sha256 = 'f' * 64
    elif fault == 'model_hash': s.model.content_hash = 'f' * 64
    elif fault == 'model_unpublished': s.model.status = 'draft'
    elif fault == 'source': s.source_changed = True
    elif fault == 'iam': s.user.active = False
    elif fault == 'input': s.job.data['inputs']['question'] = 'changed'
    elif fault == 'invocation': s.db.present_call = NS(id=identity('existing-call'))
    else: s.task.data['graph_jobs'][s.activation['id']]['dispatch_committed'] = True
    assert s.schedule() == 0 and s.job.status == 'blocked'
    assert s.task.data['graph_jobs'][s.activation['id']]['phase'] == 'blocked'


@pytest.mark.parametrize('fault', ['epoch', 'goal_cancel', 'stop'])
def test_stopped_tasks_preserve_wait_and_never_create_an_authorization(scene, fault):
    s = scene; s.wait(); before = copy.deepcopy(s.job.data[subject.KEY]); grant(s); grant(s, 1)
    if fault == 'epoch': s.task.data['graph_runtime']['cancel_epoch'] += 1
    elif fault == 'goal_cancel': s.task.data['native_goal_cancelled'] = True
    else: s.task.status = 'workflow_stopped'
    assert s.schedule() == 0 and s.job.status == 'cancelled'
    assert s.job.data[subject.KEY] == before and s.db.present_call is None


def test_wake_audit_loss_or_consent_retraction_cannot_enable_a_fourth_first_dispatch(scene):
    s = scene; s.wait(); grant(s); grant(s, 1); assert s.schedule() == 1
    s.job.status = 'running'; s.job.attempts = 4
    s.files[0].data['cloud_model_ids'] = []
    assert subject.can_start_after_wait(s.db, s.task, s.job) is False
    with pytest.raises(HTTPException): s.wait()
    s.files[0].data['cloud_model_ids'] = [s.model.id]
    s.db.events = [v for v in s.db.events if v.action != subject.WAKE]
    assert subject.can_start_after_wait(s.db, s.task, s.job) is False


def test_non_reasoning_capability_is_untouched(scene):
    s = scene; s.cap.data['execution'] = {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False}
    assert s.wait() is False and subject.KEY not in s.job.data and not s.db.events


def test_woken_original_job_cannot_adopt_a_different_goal_context_before_first_call(scene):
    s = scene
    assert s.wait() is True
    for index in range(len(s.files)): grant(s,index)
    assert s.schedule() == 1
    s.job.status = 'running'
    s.task.data = {**s.task.data,'native_goal_context':{'different_goal_context':True}}
    with pytest.raises(HTTPException): s.wait()
    assert s.job.status == 'running'
    assert subject.can_start_after_wait(s.db,s.task,s.job) is False
