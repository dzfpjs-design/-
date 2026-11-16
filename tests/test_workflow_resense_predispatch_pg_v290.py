"""Focused disposable-PG retry persistence, not a business/SDK journey.

Authentication/CSRF, capability registry, original Job/Task row locks, V290
prepare/requeue, SQL absence/footprint inventories, cold dependency BOUND/REFS,
parent terminal status and independent Audit commits are actual implementations.
The seeded initial failed Job/intent, graph activation/release and reader proof are synthetic;
graph input/view, release/source authority, 175/Goal authority and (only in the
dependency test) control/parent-pin custody are explicit IO spies. No worker,
SDK, model, network provider, acquisition, actual approval or user acceptance is
claimed. Root executes this file; it does not choose a DB or load env files.
"""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import workflow_graph_api as api
from generic_agents import workflow_resense_pre_dispatch_recovery_v290 as recovery
from generic_agents import workflow_service_context_v196 as resense
from generic_agents import workflow_task_context_v196 as reader
from generic_agents import workflow_graph_goal_guard_v133 as goals
from generic_agents import layered_approval_store_v175 as approval
from generic_agents import workflow_job_dependencies_v264 as dependencies
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.http_observation_exchange_v177 import ACTION as HTTP_CHANGED
from tests.test_authored_goal_data_workflow_v288 import command, cold_binding, reseal
from tests.test_workflow_service_registration_v195 import owned_postgres_only


@pytest.fixture(autouse=True)
def disposable_only(owned_postgres_only):
    assert engine().url.database.startswith('ga_test_')


def publish(client, body):
    response = client.post('/api/capabilities', json=body)
    assert response.status_code == 201, response.text[:400]
    row = response.json()
    for action in ('validate', 'publish'):
        response = client.post('/api/capabilities/' + row['id'] + '/' + action,
            json={'expected_version': row['version'], 'reason': '独立PG恢复协议核验登记'})
        assert response.status_code == 200, response.text[:400]
        row = response.json()
    return row


@pytest.fixture
def pgcase(client, account, monkeypatch):
    identity = lambda: str(uuid4())
    tool = publish(client, Manifest(kind='tool', namespace='resense_recovery_pg_v290',
        name='context_' + uuid4().hex[:10], version=resense.RELEASE_VERSION,
        owner='独立PG协议核验岗位', description='只登记原上下文补采契约，本测试不执行设备动作',
        allowed_agent_roles=['business'], input_schema=resense.input_schema(), output_schema=resense.output_schema(),
        execution={'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': resense.OP, 'side_effect': True}, data_policy={'cloud_allowed': False},
        budget={'max_attempts': 1, 'max_concurrency': 1}).model_dump(mode='json'))
    package = publish(client, {'kind': 'domain_package', 'namespace': 'resense_recovery_pg_v290',
        'name': 'package_' + uuid4().hex[:10], 'version': '1.0.0', 'owner': '独立PG协议核验岗位',
        'description': '仅用于恢复协议行归属，不代表采用或启用业务实例', 'dependencies': [tool['id']],
        'specification': {'entity_schemas': {'sample': {'type': 'object', 'properties': {
            'temperature': {'type': 'number'}}}}}})
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(m.User).where(m.User.username == account[0]))
        actor_id, workspace = user.id, account[2]
        body = command()
        for pin in body['tool_bindings'].values(): pin['actor_id'] = actor_id
        body['tool_bindings']['resense'].update(capability_id=tool['id'], capability_hash=tool['content_hash'])
        body['candidate_specs'][0]['capability_pins'][0].update(capability_id=tool['id'],
            capability_hash=tool['content_hash'], release_version=resense.RELEASE_VERSION)
        binding = cold_binding(body); binding['workspace_id'] = workspace; binding = reseal(binding)
        graph = binding['graph']; node = next(value for value in graph['nodes'] if value['id'] == 'resense')
        instance = m.DomainInstance(workspace_id=workspace, namespace='pg_recovery_' + uuid4().hex[:12],
            status='draft', data={'synthetic_protocol_fixture': True})
        db.add(instance); db.flush()
        release = m.InstanceRelease(workspace_id=workspace, instance_id=instance.id, release_number=1,
            configuration_hash=digest({'synthetic': instance.id}), package_id=package['id'], actor_id=actor_id,
            status='draft', data={'synthetic_protocol_fixture': True})
        db.add(release); db.flush()
        task = m.Task(workspace_id=workspace, title='独立PG原号恢复协议行', owner_id=actor_id,
            package_id=package['id'], status='workflow_running', data={'synthetic_protocol_fixture': True})
        db.add(task); db.flush()
        context = {'schema': reader.CONTEXT,
            'scope': {'workspace_id': workspace, 'task_id': task.id, 'instance_id': instance.id,
                'release_id': release.id, 'package_id': package['id'], 'business_id': 'protocol-business',
                'graph_hash': digest(graph), 'source_binding_hash': binding['binding_hash'],
                'goal_snapshot_hash': '1' * 64, 'native_goal_context_hash': '2' * 64},
            'query': {'query_id': identity(), 'request_hash': '3' * 64, 'goal_delegation_hash': '4' * 64,
                'version_at_read': 1, 'result_hash_at_read': '5' * 64},
            'gap': {'gap_id': identity(), 'entity_id': identity(), 'version_at_read': 1,
                'fields': ['temperature_c'], 'observation_request': {}, 'observation_request_hash': digest({})},
            'policy': {'policy_id': identity(), 'approved_hash': '6' * 64, 'approved_by': actor_id,
                'version_at_read': 1, 'source_pins': [{'source_id': tool['id'], 'source_hash': tool['content_hash']}]},
            'producer': {'capability_id': identity(), 'capability_hash': '7' * 64, 'invocation_id': identity(),
                'job_id': identity(), 'actor_id': actor_id, 'activation_id': 'synthetic-reader',
                'node_id': 'context_reader', 'cancel_epoch': 0, 'input_hash': '8' * 64, 'request_key_hash': '9' * 64},
            **reader.FLAGS}
        context['context_hash'] = digest(context); inputs = {'context': context}
        resense._inputs(inputs)  # Real closed wire schema; no producer authority is inferred.
        activation = {'id': 'resense_activation', 'node_id': 'resense', 'version': 0, 'status': 'ready',
            'epoch': 0, 'inputs_snapshot': {'arguments': copy.deepcopy(inputs)}}
        state = {'status': 'waiting', 'cancel_epoch': 0, 'graph_hash': digest(graph), 'commands': [],
            'eligible_ability_ids': [activation['id']], 'activations': {activation['id']: activation}}
        key = 'graph:' + task.id + ':' + activation['id'] + ':1'; token = identity()
        job = m.Job(workspace_id=workspace, kind='execute_full_semantic_graph_node', status='failed',
            attempts=1, lease_token=token, lease_until=None, idempotency_key=key, data={
                'task_id': task.id, 'actor_id': actor_id, 'activation_id': activation['id'], 'activation_version': 0,
                'graph_hash': digest(graph), 'cancel_epoch': 0, 'inputs': inputs, 'input_hash': digest(inputs),
                'invocation_key': key, 'attempt': 1, 'recovery_only': False, 'lease_recoveries': 0,
                'error': '后台执行已停止，请核查数据、能力及授权后恢复', 'finished_at': m.now().isoformat()})
        db.add(job); db.flush()
        transport = {'job_id': job.id, 'invocation_key': key, 'input_hash': digest(inputs), 'attempt': 1,
            'cancel_epoch': 0, 'phase': 'blocked', 'reason': job.data['error'], 'dispatch_committed': True,
            'dispatch_ownership': {'job_id': job.id, 'lease_token': token}, 'dispatch_input_hash': digest(inputs)}
        task.data = {'synthetic_protocol_fixture': True, 'graph_runtime': state,
            'graph_jobs': {activation['id']: transport}}
        original_audit = m.Audit(workspace_id=workspace, actor_id=actor_id, action='workflow.graph.dispatch',
            object_id=task.id, data={'activation_id': activation['id'], 'job_id': job.id})
        db.add(original_audit); db.commit()
        task_id, job_id, release_id, intent_id = task.id, job.id, release.id, original_audit.id
        original_job = copy.deepcopy(job.data)
    business = {'name': binding['business_name']}; calls = []
    def locked_context(db, p, identity, lock=False):
        query = select(m.Task).where(m.Task.id == identity, m.Task.workspace_id == p.workspace_id)
        if lock: query = query.with_for_update().execution_options(populate_existing=True)
        actual = db.scalar(query); assert actual is not None
        return actual, db.get(m.InstanceRelease, release_id), business, graph
    def view(actual, release, business, p, db=None):
        current = db.get(m.Job, job_id)
        return {'task_id': actual.id, 'job_id': current.id, 'status': current.status}
    def source_original(db, p, supplied, *, task):
        assert p.workspace_id == workspace and task.id == task_id and supplied == context
        calls.append('source-proof-IO-spy'); return copy.deepcopy(supplied)
    monkeypatch.setattr(api, 'context', locked_context)
    monkeypatch.setattr(api.runtime, 'business_binding', lambda *args: copy.deepcopy(binding))
    monkeypatch.setattr(api.runtime, 'public_view', view)
    monkeypatch.setattr(api.runtime, 'live', lambda *args: calls.append('live-authority-IO-spy'))
    monkeypatch.setattr(goals, 'progress', lambda *args: calls.append('goal-authority-IO-spy'))
    monkeypatch.setattr(approval, 'guard', lambda *args, **kwargs: calls.append('175-authority-IO-spy'))
    monkeypatch.setattr(reader, 'read_original_context_v290', source_original)
    monkeypatch.setattr(api.runtime.graph_api, 'ability_inputs', lambda *args: copy.deepcopy(inputs))
    return NS(client=client, workspace=workspace, actor_id=actor_id, task_id=task_id, job_id=job_id,
        intent_id=intent_id, context=context, original_job=original_job, graph=graph,
        activation=activation, calls=calls, request={'request_id': identity(), 'activation_id': activation['id'],
            'version': 0, 'cancel_epoch': 0, 'expected_attempt': 1, 'confirm_repeat_read': False})


def endpoint(s): return '/api/workspace/tasks/' + s.task_id + '/graph/retry'


def test_normal_http_requeue_commits_original_job_and_independent_audit_without_sdk(pgcase):
    s = pgcase
    with Session(engine()) as db:
        original_jobs = {row.id: (row.kind, row.idempotency_key, row.status, row.attempts, row.version)
            for row in db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace))}
    response = s.client.post(endpoint(s), json=s.request)
    assert response.status_code == 200, response.text[:500]
    with Session(engine()) as db:
        job = db.get(m.Job, s.job_id); task = db.get(m.Task, s.task_id)
        assert job.status == 'queued' and job.attempts == 1 and job.lease_token is job.lease_until is None
        assert job.idempotency_key == s.original_job['invocation_key']
        assert job.data['error'] == s.original_job['error'] and job.data['finished_at'] == s.original_job['finished_at']
        record = job.data[recovery.KEY]
        assert record['failure']['job_attempts'] == 1 and record['failure']['transport']['dispatch_committed'] is True
        assert record['job_data_hash'] == digest(s.original_job) and record['initial_intent_audit_id'] == s.intent_id
        original = db.get(m.Audit, s.intent_id)
        assert original.data == {'activation_id': s.activation['id'], 'job_id': job.id}
        proof = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace,
            m.Audit.action == recovery.ACTION, m.Audit.object_id == job.id)))
        assert len(proof) == 1 and proof[0].data == record and proof[0].actor_id == s.actor_id
        assert task.data['graph_retry_requests'][s.request['request_id']]['job_id'] == job.id
        before = (job.version, copy.deepcopy(job.data), copy.deepcopy(task.data))
        for model in (m.Invocation, m.Acquisition, m.Plan, m.Observation, m.CloudCall):
            assert db.scalar(select(func.count()).select_from(model).where(model.workspace_id == s.workspace)) == 0
        current_jobs = {row.id: (row.kind, row.idempotency_key, row.status, row.attempts, row.version)
            for row in db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace))}
        assert set(current_jobs) == set(original_jobs)
        # Registry validation may own other ordinary workspace Jobs. Recovery
        # preserves those jobs and changes only this original activation Job.
        assert all(current_jobs[identity] == pin for identity, pin in original_jobs.items() if identity != s.job_id)
        activation_jobs = list(db.scalars(select(m.Job.id).where(m.Job.workspace_id == s.workspace,
            m.Job.data['task_id'].as_string() == s.task_id,
            m.Job.data['activation_id'].as_string() == s.activation['id'])))
        assert activation_jobs == [s.job_id]
    recovered = s.client.get('/api/workspace/tasks/' + s.task_id + '/graph/requests/' + s.request['request_id'])
    assert recovered.status_code == 200 and recovered.json()['found'] is True
    assert s.client.post(endpoint(s), json=s.request).status_code == 200
    with Session(engine()) as db:
        job = db.get(m.Job, s.job_id); task = db.get(m.Task, s.task_id)
        assert (job.version, job.data, task.data) == before
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.workspace_id == s.workspace,
            m.Audit.action == recovery.ACTION, m.Audit.object_id == job.id)) == 1


@pytest.mark.parametrize('fault', [None, 'missing_bound', 'wrong_refs', 'parent_failed'])
def test_real_cold_dependency_rows_accept_failed_child_without_manufacturing_lease(pgcase, monkeypatch, fault):
    s = pgcase
    with Session(engine()) as db:
        job = db.get(m.Job, s.job_id); task = db.get(m.Task, s.task_id)
        parent = m.Job(workspace_id=s.workspace, kind='execute_full_semantic_graph_node',
            idempotency_key='explicit-parent-IO-spy:' + str(uuid4()), status='failed' if fault == 'parent_failed' else 'succeeded',
            data={'synthetic_parent_custody_boundary': True})
        db.add(parent); db.flush(); pin = {'job_id': parent.id, 'synthetic_pin': True}
        base = {'schema': dependencies.SCHEMA, 'workspace_id': s.workspace, 'task_id': task.id,
            **dependencies._child_pin(job), 'bound_by': s.actor_id, 'control_record_hash': 'a' * 64,
            'compact_enrollment_hash': None, 'parents': [pin]}
        value = {**base, 'record_hash': digest(base)}
        job.data = {**job.data, dependencies.KEY: value}
        task.data = {**task.data, dependencies.REFS: {job.id: 'b' * 64 if fault == 'wrong_refs' else value['record_hash']}}
        if fault != 'missing_bound':
            db.add(m.Audit(workspace_id=s.workspace, actor_id=s.actor_id, action=dependencies.BOUND,
                object_id=job.id, data=value))
        db.commit(); parent_id = parent.id
    monkeypatch.setattr(dependencies, 'checked_control', lambda *args, **kwargs: {'explicit_control_IO_spy': True})
    monkeypatch.setattr(dependencies, 'graph_parent_ids', lambda *args: [parent_id])
    monkeypatch.setattr(dependencies, 'parent_pin', lambda *args: copy.deepcopy(pin))
    p = NS(workspace_id=s.workspace, user_id=s.actor_id)
    with Session(engine()) as db:
        task = db.get(m.Task, s.task_id); job = db.get(m.Job, s.job_id)
        assert job.status == 'failed' and db.info.get('worker_lease') is None
        if fault:
            with pytest.raises(HTTPException): recovery._cold_dependencies(db, p, task, job)
        else: recovery._cold_dependencies(db, p, task, job)
        assert job.status == 'failed' and job.attempts == 1 and job.lease_until is None


@pytest.mark.parametrize('witness', ['http_change_audit', 'event', 'receipt_memory'])
def test_normal_http_recovery_rejects_actual_reverse_orphan_witness_without_forward_rows(pgcase, witness):
    s = pgcase
    reverse = {'state': {'data': {'freeze': {'parent_pins': {'task_id': s.task_id,
        'gap_id': s.context['gap']['gap_id'], 'query_id': s.context['query']['query_id']}}}}}
    with Session(engine()) as db:
        if witness == 'http_change_audit':
            db.add(m.Audit(workspace_id=s.workspace, actor_id=s.actor_id, action=HTTP_CHANGED,
                object_id=str(uuid4()), data=reverse))
        elif witness == 'event':
            db.add(m.Event(workspace_id=s.workspace, kind='observation.data', idempotency_key='orphan:' + str(uuid4()), data=reverse))
        else:
            db.add(m.Memory(workspace_id=s.workspace, status='candidate', text='独立PG孤立接收证据',
                data={'type': 'observation_evidence', **reverse}))
        db.commit()
    response = s.client.post(endpoint(s), json=s.request)
    assert response.status_code == 409, response.text[:500]
    with Session(engine()) as db:
        job = db.get(m.Job, s.job_id)
        assert job.status == 'failed' and job.attempts == 1 and recovery.KEY not in job.data
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.workspace_id == s.workspace,
            m.Audit.action == recovery.ACTION)) == 0
        for model in (m.Invocation, m.Acquisition, m.Plan, m.Observation):
            assert db.scalar(select(func.count()).select_from(model).where(model.workspace_id == s.workspace)) == 0
