"""Disposable PG persistence/consent proof with explicit graph-source IO seam.

Root runs; this is not a real V175 approved Task or a provider/production proof.
Uses actual immutable text files, Evidence consent API primitive, lease fence,
ORM and separate Sessions. No model, device, new Job or Invocation is invoked.
"""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, leases, workflow_graph, native_goals
from generic_agents import workflow_evidence_consent_wait_v199 as subject
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import digest
from generic_agents.evidence import save_evidence, authorize_cloud, evidence_bytes
from tests.test_business_reasoning_v197 import manifest
from tests.test_workflow_provenance_wait_pg_v197 import scene as previous_scene


@pytest.fixture
def scene(account, client, monkeypatch):
    # The reused fixture first verifies an actual isolated ga_test_ PG database
    # and ordinary authenticated account; it labels source authority as a seam.
    s = previous_scene.__wrapped__(account, client, monkeypatch)
    with Session(s.bound, expire_on_commit=False) as db:
        cap = db.get(m.Capability, s.cap_id); task = db.get(m.Task, s.task_id); job = db.get(m.Job, s.job_id)
        data = manifest(); model = m.Capability(workspace_id=s.p.workspace_id, namespace='consent_pg',
            name='synthetic_consent_model_' + str(uuid4()), kind='foundation_model', status='published',
            release_version='1.0.0', data=data, content_hash=digest(data), validated_hash=digest(data))
        db.add(model); db.flush()
        files = [save_evidence(db, s.p, ('合成观测资料 ' + str(i) + '；仅用于原任务授权等待测试。').encode('utf-8'), 'text/plain') for i in range(2)]
        inputs = {'question': '比较本次明确授权资料', 'evidence_ids': [v.id for v in files],
            'candidates': [{'candidate_ref': 'proposal_1', 'summary': '需人工核对的提案', 'context': None}]}
        cap.data = {**manifest(), 'kind': 'tool', 'name': 'synthetic_consent_reasoner',
            'allowed_agent_roles': ['business'], 'execution': {'adapter': 'business_reasoning',
                'model_profile_id': model.id, 'model_profile_hash': model.content_hash, 'side_effect': False},
            'input_schema': subject.reasoning.input_schema(), 'output_schema': subject.reasoning.output_schema(),
            'dependencies': [model.id], 'specification': {'schema': subject.reasoning.CONTRACT, 'instruction': '只比较证据，不审批或派设备'}}
        cap.content_hash = cap.validated_hash = digest(cap.data)
        graph = copy.deepcopy(s.graph); node = next(v for v in graph['nodes'] if v['kind'] == 'ability')
        node['execution'].update(capability_hash=cap.content_hash, agent_role='business', adapter='business_reasoning',
            input_schema=copy.deepcopy(cap.data['input_schema']), output_schema=copy.deepcopy(cap.data['output_schema']), arguments=copy.deepcopy(inputs))
        state = kernel.new_run(graph, {}, 10); activation = state['eligible_ability_ids'][0]
        job.data = {**job.data, 'inputs': inputs, 'input_hash': digest(inputs), 'graph_hash': state['graph_hash'], 'activation_id': activation}
        task.data = {**task.data, 'graph_runtime': state, 'graph_jobs': {activation: {'job_id': job.id,
            'invocation_key': job.data['invocation_key'], 'cancel_epoch': 0, 'input_hash': digest(inputs), 'phase': 'queued'}}}
        db.commit(); s.model_id = model.id; s.file_ids = [v.id for v in files]
        s.graph = graph; s.inputs = inputs; s.activation_id = activation
    def source_live(db, p, task, release, business):
        assert task.id == s.task_id and release.id == s.release_id and business['id'] == 'wait_business'
        assert p.user_id == s.p.user_id and p.workspace_id == s.p.workspace_id
        assert db.get(m.Task, task.id).data['graph_runtime']['graph_hash'] == digest(s.graph)
    monkeypatch.setattr(workflow_graph, 'live', source_live)
    monkeypatch.setattr(native_goals, 'guard', lambda db, p, task, key: task.data['native_goal_context'])
    return s


def waiting(s):
    with Session(s.bound) as db:
        leases.attach(db, s.job_id, s.token); db.info['worker_actor'] = s.p
        task, job, cap = db.get(m.Task, s.task_id), db.get(m.Job, s.job_id), db.get(m.Capability, s.cap_id)
        node = next(v for v in s.graph['nodes'] if v['kind'] == 'ability')
        assert subject.preflight(db, s.p, task, job, node, cap, copy.deepcopy(s.inputs)) is True
        db.commit()


def counts(s):
    with Session(s.bound) as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model).where(model.workspace_id == s.p.workspace_id))
            for model in (m.Job, m.Invocation, m.CloudCall, m.Acquisition)}


def test_pg_original_wait_only_requeues_after_real_consent_and_survives_new_session(scene):
    s = scene; baseline = counts(s); waiting(s)
    with Session(s.bound) as db:
        job = db.get(m.Job, s.job_id); attempts = job.attempts; available = job.available_at; key = job.idempotency_key
        assert job.status == subject.PHASE and subject.scheduler_once(db, workspace_id=s.p.workspace_id) == 0
        db.commit()
    with Session(s.bound) as db:
        for file_id in s.file_ids:
            file = db.get(m.Evidence, file_id); assert file.data['cloud_model_ids'] == []
            authorize_cloud(db, s.p, file_id, s.model_id, file.version, '明确同意此原件用于当前模型业务分析')
        db.commit()
    with Session(s.bound) as db:
        assert subject.scheduler_once(db, workspace_id=s.p.workspace_id) == 1
        db.commit()
    with Session(s.bound) as db:
        task, job = db.get(m.Task, s.task_id), db.get(m.Job, s.job_id)
        assert (job.status, job.attempts, job.available_at, job.idempotency_key) == ('queued', attempts, available, key)
        assert subject.can_start_after_wait(db, task, job) is True
        assert task.data['graph_jobs'][s.activation_id]['phase'] == 'queued'
        for file_id in s.file_ids:
            row, content = evidence_bytes(db, s.p, file_id); assert content.decode('utf-8').startswith('合成观测资料 ')
        assert list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id, m.Audit.object_id == job.id,
            m.Audit.action == subject.WAKE)))
    assert counts(s) == baseline


def test_pg_wait_cannot_recover_with_permission_marker_and_missing_original_wait_audit(scene):
    s = scene; baseline = counts(s); waiting(s)
    with Session(s.bound) as db:
        for file_id in s.file_ids:
            file = db.get(m.Evidence, file_id); file.data = {**file.data, 'cloud_model_ids': [s.model_id]}
        event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.object_id == s.job_id, m.Audit.action == subject.WAIT)); db.delete(event); db.commit()
    with Session(s.bound) as db:
        assert subject.scheduler_once(db, workspace_id=s.p.workspace_id) == 0
        assert db.get(m.Job, s.job_id).status == 'blocked'; db.commit()
    assert counts(s) == baseline
