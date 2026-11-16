"""Disposable PG counter/inventory regression with an explicit source IO seam.

The fixture stores synthetic historical Task/Job/Invocation/proof rows in real
PostgreSQL. It is not an approved normal Task, a verified V288 publication,
source-producer authority, device result or recovery proof. Only the cold source
authority checker is replaced; capacity counting, bounded causal inventory and
read-only execution use their actual implementation and the real database.
Root separately verifies the retained real unknown call against every source
proof. No test dispatches an adapter or changes a normal production record.
"""
import copy
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import workflow_local_transaction_capacity_v201 as subject
from generic_agents import workflow_service_context_v196 as service
from generic_agents import workflow_task_context_v196 as reader
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.security import Principal


def identity(): return str(uuid4())


def authority(db, account):
    user = db.scalar(select(m.User).where(m.User.username == account[0]))
    member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == account[2],
        m.Membership.user_id == user.id))
    assert user.active and member.role == 'admin'
    return Principal(user.id, account[2], member.role, 'resense-cpu-pg-synthetic-history')


@contextmanager
def readonly(s):
    with s.bound.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            with Session(bind=connection, autoflush=False, expire_on_commit=False,
                    join_transaction_mode='rollback_only') as db:
                yield db
        finally:
            if transaction.is_active: transaction.rollback()


@pytest.fixture
def historical(account, client, monkeypatch):
    bound = engine()
    assert bound.dialect.name == 'postgresql'
    with bound.connect() as connection:
        name = connection.scalar(text('SELECT current_database()'))
    assert name == bound.url.database and name.startswith('ga_test_')
    assert client.headers['X-Workspace-ID'] == account[2]
    s = NS(bound=bound, account=account, source_reads=[])
    with Session(bound, expire_on_commit=False) as db:
        s.p = authority(db, account)
        package_data = {'synthetic_publication_boundary_only': True}
        package = m.Capability(workspace_id=s.p.workspace_id, namespace='capacity_pg_v289',
            name='source_package', kind='domain_package', status='published', release_version='1.0.0',
            data=package_data, content_hash=digest(package_data), validated_hash=digest(package_data))
        value = Manifest(kind='tool', namespace='capacity_pg_v289', name='resense',
            version=service.RELEASE_VERSION, owner='合成历史证明测试', description='仅核对CPU容量及库存，不产生采集',
            allowed_agent_roles=['business'], execution={'adapter': 'workflow_service',
                'service_actor': 'execution_service', 'operation': service.OP, 'side_effect': True},
            input_schema=service.input_schema(), output_schema=service.output_schema(),
            data_policy={'cloud_allowed': False, 'external_allowed': False}, dependencies=[],
            budget={'max_attempts': 1, 'max_concurrency': 1}).model_dump(mode='json')
        service.validate_manifest(value)
        cap = m.Capability(workspace_id=s.p.workspace_id, namespace='capacity_pg_v289', name='resense',
            kind='tool', status='published', release_version=service.RELEASE_VERSION,
            data=value, content_hash=digest(value), validated_hash=digest(value))
        source_data = {'execution': {'adapter': 'http', 'side_effect': True,
            'endpoint': 'https://synthetic.invalid/source'}, 'budget': {'max_concurrency': 1}}
        source = m.Capability(workspace_id=s.p.workspace_id, namespace='capacity_pg_v289', name='source',
            kind='source', status='published', release_version='1.0.0', data=source_data,
            content_hash=digest(source_data), validated_hash=digest(source_data))
        db.add_all([package, cap, source]); db.flush()
        entity = m.Entity(workspace_id=s.p.workspace_id, namespace='capacity_pg_v289', entity_type='sample',
            external_id='synthetic-capacity-entity', schema_capability_id=package.id, data={})
        policy = m.MonitorPolicy(workspace_id=s.p.workspace_id, approved_by=s.p.user_id, status='active',
            data={'approved_hash': 'c' * 64})
        instance = m.DomainInstance(workspace_id=s.p.workspace_id, namespace='capacity_pg_v289',
            status='partially_active', data={})
        query = m.DataRun(workspace_id=s.p.workspace_id, owner_id=s.p.user_id,
            question='合成历史容量数据范围', status='insufficient', data={})
        db.add_all([entity, policy, instance, query]); db.flush()
        release = m.InstanceRelease(workspace_id=s.p.workspace_id, instance_id=instance.id,
            package_id=package.id, actor_id=s.p.user_id, release_number=1, status='active',
            configuration_hash=digest({}), data={'snapshot': {}})
        gap = m.DataGap(workspace_id=s.p.workspace_id, query_id=query.id, entity_id=entity.id,
            status='received', data={})
        db.add_all([release, gap]); db.flush()
        task = m.Task(workspace_id=s.p.workspace_id, owner_id=s.p.user_id, package_id=package.id,
            title='合成已终结编排器记录，非正常验收证明', status='workflow_failed', data={})
        db.add(task); db.flush()
        context = {'schema': reader.CONTEXT, 'scope': {'workspace_id': s.p.workspace_id,
            'task_id': task.id, 'instance_id': instance.id, 'release_id': release.id,
            'package_id': package.id, 'business_id': 'synthetic_business', 'graph_hash': 'a' * 64,
            'source_binding_hash': 'b' * 64, 'goal_snapshot_hash': 'd' * 64,
            'native_goal_context_hash': 'e' * 64},
            'query': {'query_id': query.id, 'request_hash': 'f' * 64, 'goal_delegation_hash': '1' * 64,
                'version_at_read': 1, 'result_hash_at_read': '2' * 64},
            'gap': {'gap_id': gap.id, 'entity_id': entity.id, 'version_at_read': 1,
                'fields': ['temperature'], 'observation_request': {'task_id': task.id, 'gap_id': gap.id,
                    'query_id': query.id}, 'observation_request_hash': '3' * 64},
            'policy': {'policy_id': policy.id, 'approved_hash': 'c' * 64, 'approved_by': s.p.user_id,
                'version_at_read': 1, 'source_pins': [{'source_id': source.id, 'source_hash': source.content_hash}]},
            'producer': {'capability_id': identity(), 'invocation_id': identity(), 'job_id': identity(),
                'actor_id': s.p.user_id, 'capability_hash': '4' * 64, 'activation_id': 'reader',
                'node_id': 'context_reader', 'cancel_epoch': 0, 'input_hash': '5' * 64,
                'request_key_hash': '6' * 64}, 'permissions_verified': False, 'can_dispatch': False}
        context['context_hash'] = digest(context)
        inputs = {'context': context}; service._inputs(inputs)
        key = 'graph:' + task.id + ':resense:1'; token = identity()
        job = m.Job(workspace_id=s.p.workspace_id, kind='execute_full_semantic_graph_node', status='blocked',
            attempts=1, idempotency_key=key, data={'task_id': task.id, 'actor_id': s.p.user_id,
                'activation_id': 'resense', 'graph_hash': 'a' * 64, 'cancel_epoch': 0, 'attempt': 1,
                'activation_version': 0, 'invocation_key': key, 'input_hash': digest(inputs), 'inputs': inputs})
        db.add(job); db.flush()
        node = {'execution': {'capability_id': cap.id, 'capability_hash': cap.content_hash}}
        task.data = {'instance_id': instance.id, 'instance_release_id': release.id,
            'business_id': 'synthetic_business', 'native_goal_query_id': query.id, 'entity_ids': [entity.id],
            'graph_runtime': {'graph_hash': 'a' * 64, 'activations': {'resense': {'node_id': 'resense', 'epoch': 0}}}}
        approval = service.shared.make_approval(task, job, node, inputs)
        call = m.Invocation(workspace_id=s.p.workspace_id, capability_id=cap.id, idempotency_key=key,
            status='unknown', data={'role': 'business', 'side_effect': True, 'capability_hash': cap.content_hash,
                'capability_snapshot': value, 'inputs': inputs, 'input_hash': digest(inputs), 'job_id': job.id,
                'lease_token': token, 'approval': approval})
        db.add(call); db.flush()
        authorization = {'epoch': 0, 'job_id': job.id, 'lease_token': token, 'input_hash': digest(inputs),
            'capability_hash': cap.content_hash, 'actor_id': s.p.user_id, 'authorized_at': '2026-11-01T00:00:00+00:00'}
        task.data = {**task.data, 'graph_jobs': {'resense': {'job_id': job.id, 'invocation_key': key,
            'cancel_epoch': 0, 'attempt': 1, 'input_hash': digest(inputs), 'dispatch_input_hash': digest(inputs),
            'dispatch_committed': True, 'dispatch_ownership': {'job_id': job.id, 'lease_token': token},
            'phase': 'blocked', 'invocation_id': call.id, 'dispatch_authorization': authorization}}}
        current = {'task': task, 'job': job, 'binding': {'binding_hash': 'b' * 64}}
        proof = service._proof_value(s.p, current, call, inputs, key, approval)
        call.data = {**call.data, service.PROOF_KEY: proof}
        db.add_all([m.Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id,
            action='workflow.graph.dispatch', object_id=task.id, data={'activation_id': 'resense', 'job_id': job.id}),
            m.Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id,
                action='workflow.graph.dispatch_authorized', object_id=task.id, data={'activation_id': 'resense',
                    'job_id': job.id, 'epoch': 0, 'input_hash': digest(inputs)}),
            m.Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id, action=service.PROOF_AUDIT,
                object_id=call.id, data=service._proof_anchor(proof))])
        db.commit()
        s.cap_id, s.call_id, s.job_id, s.task_id = cap.id, call.id, job.id, task.id
        s.source_id, s.entity_id, s.policy_id = source.id, entity.id, policy.id
        s.query_id, s.gap_id, s.context = query.id, gap.id, copy.deepcopy(context)

    def cold_source_boundary(db, p, actual_cap, actual_call, actual_job, actual_task,
            transport, handler, actual_inputs):
        # This is the sole unproved publication/producer/Goal/policy boundary.
        # Its proof rows above exercise persistence, never establish authority.
        assert p.workspace_id == s.p.workspace_id and handler is service
        assert (actual_cap.id, actual_call.id, actual_job.id, actual_task.id) == (
            s.cap_id, s.call_id, s.job_id, s.task_id)
        assert actual_inputs == {'context': s.context}
        assert actual_call.data[service.PROOF_KEY]['proof_hash'] == proof['proof_hash']
        s.source_reads.append(actual_call.id)
        return copy.deepcopy(s.context)

    monkeypatch.setattr(subject, '_resense_source_proof', cold_source_boundary)
    return s


def counter(s):
    with readonly(s) as db:
        p = authority(db, s.account)
        cap = db.get(m.Capability, s.cap_id)
        before = copy.deepcopy((db.get(m.Invocation, s.call_id).data, db.get(m.Job, s.job_id).data,
            db.get(m.Task, s.task_id).data))
        result = subject.count_active(db, p, cap)
        assert not db.new and not db.dirty and not db.deleted
        assert (db.get(m.Invocation, s.call_id).data, db.get(m.Job, s.job_id).data,
            db.get(m.Task, s.task_id).data) == before
        return result


def test_real_pg_counter_excludes_only_parent_cpu_and_preserves_unknown_records(historical):
    s = historical
    assert counter(s) == 0 and s.source_reads == [s.call_id]
    with Session(s.bound) as db:
        assert db.get(m.Invocation, s.call_id).status == 'unknown'
        assert db.get(m.Job, s.job_id).status == 'blocked'
        assert 'service_state' not in db.get(m.Invocation, s.call_id).data


@pytest.mark.parametrize('field', ['service_state', 'output'])
def test_retained_state_or_output_never_uses_nostate_cpu_exception(historical, field):
    s = historical
    with Session(s.bound) as db:
        call = db.get(m.Invocation, s.call_id); call.data = {**call.data, field: None}; db.commit()
    assert counter(s) == 1


@pytest.mark.parametrize('status', ['queued', 'running', 'succeeded'])
def test_parent_job_must_be_actually_ended(historical, status):
    s = historical
    with Session(s.bound) as db:
        db.get(m.Job, s.job_id).status = status; db.commit()
    assert counter(s) == 1 and s.source_reads == []


@pytest.mark.parametrize('kind', ['plan', 'acquisition', 'dispatch', 'observation', 'event', 'receipt', 'audit'])
@pytest.mark.parametrize('ref', ['task', 'gap', 'query'])
def test_real_pg_causal_footprint_on_any_original_axis_holds_cpu(historical, kind, ref):
    s = historical
    key, value = {'task': ('task_id', s.task_id), 'gap': ('gap_id', s.gap_id),
        'query': ('query_id', s.query_id)}[ref]
    with Session(s.bound) as db:
        payload = {key: value}
        if kind == 'plan':
            row = m.ObservationPlan(workspace_id=s.p.workspace_id, policy_id=s.policy_id,
                entity_id=s.entity_id, status='selecting', data={'observation_request': payload})
        elif kind == 'acquisition':
            row = m.Acquisition(workspace_id=s.p.workspace_id, capability_id=s.source_id, entity_id=s.entity_id,
                idempotency_key='synthetic-acq:' + identity(), status='unknown',
                data={'observation_request': payload, 'policy_id': s.policy_id})
        elif kind == 'dispatch':
            row = m.Job(workspace_id=s.p.workspace_id, kind='dispatch_acquisition',
                idempotency_key='synthetic-dispatch:' + identity(), status='failed', data=payload)
        elif kind == 'observation':
            row = m.Observation(workspace_id=s.p.workspace_id, source_id=s.source_id, entity_id=s.entity_id,
                source_record_id='synthetic-observation:' + identity(), processing_version='1',
                status='processed', data=payload)
        elif kind == 'event':
            row = m.Event(workspace_id=s.p.workspace_id, kind='observation.data',
                idempotency_key='synthetic-data:' + identity(), status='received', data=payload)
        elif kind == 'receipt':
            row = m.Memory(workspace_id=s.p.workspace_id, status='candidate',
                text='合成孤儿收件库存，非实际收件',
                data={'type': 'observation_evidence', 'data_reception': payload})
        else:
            row = m.Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id,
                action='acquisition.dispatch', object_id=identity(), data=payload)
        db.add(row); db.commit()
    assert counter(s) == 1


def test_ambiguous_same_policy_entity_plan_without_original_request_holds_cpu(historical):
    s = historical
    with Session(s.bound) as db:
        db.add(m.ObservationPlan(workspace_id=s.p.workspace_id, policy_id=s.policy_id,
            entity_id=s.entity_id, status='selecting', data={})); db.commit()
    assert counter(s) == 1


def test_foreign_workspace_causal_marker_does_not_poison_original_inventory(historical):
    s = historical
    with Session(s.bound) as db:
        outsider = m.Workspace(name='独立外工作区'); db.add(outsider); db.flush()
        db.add(m.Event(workspace_id=outsider.id, kind='observation.data',
            idempotency_key='foreign-synthetic:' + identity(), status='received',
            data={'task_id': s.task_id, 'gap_id': s.gap_id, 'query_id': s.query_id}))
        db.commit()
    assert counter(s) == 0


def test_pg_inventory_overflow_is_not_truncated_into_empty(historical, monkeypatch):
    s = historical
    monkeypatch.setattr(subject, 'MAX_FOOTPRINT', 2)
    with Session(s.bound) as db:
        db.add_all([m.Event(workspace_id=s.p.workspace_id, kind='observation.data',
            idempotency_key='unrelated-synthetic:' + identity(), status='received',
            data={'unrelated': identity()}) for _ in range(3)])
        db.commit()
    assert counter(s) == 1


def test_actual_unknown_http_source_still_holds_independent_capacity(historical):
    s = historical
    with Session(s.bound) as db:
        db.add(m.Invocation(workspace_id=s.p.workspace_id, capability_id=s.source_id,
            idempotency_key='synthetic-source:' + identity(), status='unknown', data={'side_effect': True}))
        db.commit()
    assert counter(s) == 0
    with readonly(s) as db:
        source = db.get(m.Capability, s.source_id)
        assert subject.count_active(db, s.p, source) == 1
        assert not db.new and not db.dirty and not db.deleted


def test_cpu_counter_does_not_release_existing_source_sensor_or_device_holds(historical):
    s = historical
    with Session(s.bound, expire_on_commit=False) as db:
        package = db.get(m.Task, s.task_id).package_id
        another_entity = m.Entity(workspace_id=s.p.workspace_id, namespace='capacity_pg_v289',
            entity_type='sample', external_id='other-resource-entity', schema_capability_id=package, data={})
        db.add(another_entity); db.flush()
        independent = m.Acquisition(workspace_id=s.p.workspace_id, entity_id=another_entity.id,
            capability_id=s.source_id, idempotency_key='other-source-acq:' + identity(),
            status='unknown', data={'observation_request': {'task_id': identity(),
                'gap_id': identity(), 'query_id': identity()}})
        device = m.SensorDevice(workspace_id=s.p.workspace_id, title='合成独立设备',
            identity_scheme='test', identity_issuer='capacity-pg', identity_reference=identity(),
            status='approved', approved_by=s.p.user_id, data={})
        asset = m.SensorAsset(workspace_id=s.p.workspace_id, source_id=s.source_id,
            external_id=identity(), title='合成独立资产', status='enabled', data={})
        db.add_all([independent, device, asset]); db.flush()
        holds = [m.SourceReservation(workspace_id=s.p.workspace_id, source_id=s.source_id,
                acquisition_id=independent.id),
            m.SensorReservation(workspace_id=s.p.workspace_id, asset_id=asset.id, acquisition_id=independent.id),
            m.DeviceReservation(workspace_id=s.p.workspace_id, device_id=device.id, acquisition_id=independent.id)]
        db.add_all(holds); db.commit()
        retained = [(type(row), row.id, row.acquisition_id) for row in holds]
        independent_id = independent.id
    assert counter(s) == 0
    with readonly(s) as db:
        assert all(db.get(model, row_id).acquisition_id == acquisition_id
            for model, row_id, acquisition_id in retained)
        assert db.get(m.Acquisition, independent_id).status == 'unknown'
        assert not db.new and not db.dirty and not db.deleted


def http_exchange_orphan_audit(workspace_id, actor_id, parent_pins):
    """Only the surviving Audit: real action/nesting, no fabricated exchange authority."""
    exchange_id, acquisition_id = identity(), identity()
    request_key = 'synthetic-orphan-http-exchange:' + exchange_id
    state = {'id': exchange_id, 'workspace_id': workspace_id, 'acquisition_id': acquisition_id,
        'source_id': identity(), 'environment_id': identity(), 'instance_id': identity(),
        'release_id': identity(), 'dispatch_job_id': identity(), 'actor_id': actor_id,
        'observation_id': None, 'status': 'prepared', 'version': 1,
        'data': {'schema': 'http-observation-exchange-v177', 'freeze': {
            'schema': 'http-observation-exchange-freeze-v177',
            'original_request_key': request_key, 'parent_pins': copy.deepcopy(parent_pins)}}}
    return m.Audit(workspace_id=workspace_id, actor_id=actor_id,
        action='http.observation.exchange.changed_v177', object_id=exchange_id,
        data={'schema': 'http-observation-exchange-audit-v177', 'exchange_id': exchange_id,
            'acquisition_id': acquisition_id, 'original_request_key': request_key,
            'version': 1, 'operation': 'create', 'actor_id': actor_id,
            'previous_state_hash': None, 'state_hash': digest(state), 'state': state})


@pytest.mark.parametrize('axis', ['task_id', 'gap_id', 'query_id'])
def test_real_http_exchange_orphan_audit_nested_parent_pin_holds_cpu(historical, axis):
    """The sole effect witness survives after all forward effect rows are absent."""
    s = historical
    assert counter(s) == 0
    value = {'task_id': s.task_id, 'gap_id': s.gap_id, 'query_id': s.query_id}[axis]
    with Session(s.bound, expire_on_commit=False) as db:
        row = http_exchange_orphan_audit(s.p.workspace_id, s.p.user_id, {axis: value})
        db.add(row); db.commit()
        audit_id, original = row.id, copy.deepcopy(row.data)
    assert counter(s) == 1
    with readonly(s) as db:
        assert db.get(m.Audit, audit_id).data == original
        assert db.get(m.Invocation, s.call_id).status == 'unknown'
        assert db.get(m.Job, s.job_id).status == 'blocked'
        assert not db.new and not db.dirty and not db.deleted


def test_foreign_real_http_exchange_orphan_audit_does_not_poison_cpu(historical):
    s = historical
    with Session(s.bound, expire_on_commit=False) as db:
        outsider = m.Workspace(name='HTTP交换独立外工作区'); db.add(outsider); db.flush()
        row = http_exchange_orphan_audit(outsider.id, s.p.user_id,
            {'task_id': s.task_id, 'gap_id': s.gap_id, 'query_id': s.query_id})
        db.add(row); db.commit()
        audit_id, original = row.id, copy.deepcopy(row.data)
    assert counter(s) == 0
    with readonly(s) as db:
        assert db.get(m.Audit, audit_id).data == original
        assert db.get(m.Invocation, s.call_id).status == 'unknown'
        assert not db.new and not db.dirty and not db.deleted
