"""Real disposable PostgreSQL wait inventory, not a V175 execution proof.

The account/client fixtures establish ordinary authentication. Only the live
graph/source/approval lookup is an explicit synthetic IO seam; the real kernel,
lease checks, ORM, SQL, wait/wake inventory and independent Audit stay intact.
Root runs this file. No provider, acquisition or normal approved Task is run.
"""
import copy
import math
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import leases, models as m, workflow_graph
from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents import workflow_task_context_v196 as reader
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.goals import snapshot as goal_snapshot
from generic_agents.security import Principal
from tests.test_full_semantic_graph_v1 import ability, base, control, edge


def authority(db, account):
    user = db.scalar(select(m.User).where(m.User.username == account[0]))
    assert user is not None and user.active is True
    member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == account[2],
        m.Membership.user_id == user.id))
    assert member is not None and member.role == 'admin'
    return Principal(user.id, member.workspace_id, member.role, 'provenance-pg-authenticated-fixture')


@pytest.fixture
def scene(account, client, monkeypatch):
    bound = engine()
    assert bound.dialect.name == 'postgresql'
    with bound.connect() as connection:
        name = connection.scalar(text('SELECT current_database()'))
    assert name == bound.url.database and name.startswith('ga_test_'), \
        'This test may only use the disposable ga_test_ conftest database.'
    assert client.headers['X-Workspace-ID'] == account[2]
    s = NS(bound=bound, account=account, authority_reads=[])
    with Session(bound, expire_on_commit=False) as db:
        s.p = authority(db, account)
        package_value = {'kind': 'domain_package', 'description': 'Synthetic graph authority seam only'}
        package = m.Capability(workspace_id=s.p.workspace_id, namespace='provenance_pg',
            name='wait_package', release_version='1.0.0', kind='domain_package', status='published',
            content_hash=digest(package_value), validated_hash=digest(package_value), data=package_value)
        cap_value = {'kind': 'tool', 'allowed_agent_roles': ['data'],
            'execution': {'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': False},
            'input_schema': reader.input_schema(), 'output_schema': reader.output_schema()}
        cap = m.Capability(workspace_id=s.p.workspace_id, namespace='provenance_pg', name='task_context',
            release_version='1.0.0', kind='tool', status='published', content_hash=digest(cap_value),
            validated_hash=digest(cap_value), data=cap_value)
        db.add_all([package, cap]); db.flush()
        instance = m.DomainInstance(workspace_id=s.p.workspace_id, namespace='provenance_pg',
            status='active', data={'synthetic_source_authority': True})
        entity = m.Entity(workspace_id=s.p.workspace_id, namespace='provenance_pg', entity_type='sample',
            external_id='wait-sample', schema_capability_id=package.id, data={'test_only': True})
        db.add_all([instance, entity]); db.flush()
        business = {'id': 'wait_business', 'name': '原资料检查等待',
            'execution_mode': 'confirmed_graph_workflow'}
        release = m.InstanceRelease(workspace_id=s.p.workspace_id, instance_id=instance.id,
            package_id=package.id, actor_id=s.p.user_id, release_number=1, status='active',
            configuration_hash=digest({'businesses': [business]}),
            data={'snapshot': {'businesses': [business]}})
        db.add(release); db.flush()
        instance.active_release_id = release.id
        inputs = {'policy_pin': None}
        node = ability('read_context', {})
        node['actor_id'] = s.p.user_id
        node['execution'].update(capability_id=cap.id, capability_hash=cap.content_hash,
            input_schema=copy.deepcopy(cap_value['input_schema']),
            output_schema=copy.deepcopy(cap_value['output_schema']), arguments=copy.deepcopy(inputs))
        graph = base([node, control('end', 'end')], [edge('read_context', 'end')],
            'read_context', ['read_context'])
        state = kernel.new_run(graph, {}, 10)
        activation_id = state['eligible_ability_ids'][0]
        assert kernel.ability_inputs(graph, state, activation_id) == inputs
        spec = {'entity_ids': [entity.id], 'required_fields': ['temperature_c'],
            'observation_mode': 'allow_new', 'deadline': None, 'max_tool_calls': 12}
        window = {'mode': 'latest', 'reference_at': '2026-01-01T00:00:00+00:00'}
        task = m.Task(workspace_id=s.p.workspace_id, package_id=package.id, owner_id=s.p.user_id,
            title='仅验证 PostgreSQL 来源等待库存', status='workflow_running', data={
                'instance_id': instance.id, 'instance_release_id': release.id, 'business_id': business['id'],
                'entity_ids': [entity.id], 'goal_spec': spec, 'goal_hash': digest(spec),
                'goal_confirmed_by': s.p.user_id, 'goal_version': 1, 'goal_window': window,
                'workflow_runtime': {'receipts': []}, 'graph_runtime': state,
                'human_buffers': {'text': '原稿\n不覆盖', 'numbers': [1, 1.0, -0.0]}})
        db.add(task); db.flush()
        context = {'instance_id': instance.id, 'release_id': release.id, 'business_id': business['id'],
            'entity_ids': [entity.id], 'goal_snapshot': goal_snapshot(task)}
        clock = db.scalar(select(func.clock_timestamp()))
        dependency = m.Job(workspace_id=s.p.workspace_id, kind='check_native_business_goal',
            idempotency_key='native-goal:' + str(uuid4()), status='queued', attempts=0,
            available_at=clock - timedelta(seconds=1), data={'task_id': task.id,
                'actor_id': s.p.user_id, 'context_hash': digest(context)})
        db.add(dependency); db.flush()
        pin = {'job_id': dependency.id, 'idempotency_key': dependency.idempotency_key,
            'context_hash': digest(context), 'actor_id': s.p.user_id}
        key = 'graph:' + str(uuid4())
        job = m.Job(workspace_id=s.p.workspace_id, kind=waits.scope.KIND,
            idempotency_key=key, status='running', attempts=1,
            available_at=clock - timedelta(seconds=1), lease_token=str(uuid4()),
            lease_until=clock + timedelta(minutes=20), data={
                'task_id': task.id, 'actor_id': s.p.user_id, 'activation_id': activation_id,
                'activation_version': 0, 'graph_hash': state['graph_hash'], 'cancel_epoch': 0,
                'inputs': inputs, 'input_hash': digest(inputs), 'invocation_key': key,
                'original_budget': {'max_attempts': 2, 'reserved_cost': 0.0, 'deadline': None}})
        db.add(job); db.flush()
        task.data = {**task.data, 'native_goal_context': context, 'native_goal_context_hash': digest(context),
            'native_goal_query_id': None, 'native_goal_check_job_pin': pin,
            'native_goal_check_job_pin_hash': digest(pin), 'graph_jobs': {activation_id: {
                'job_id': job.id, 'invocation_key': key, 'cancel_epoch': 0,
                'input_hash': digest(inputs), 'phase': 'queued'}}}
        db.commit()
        s.task_id, s.job_id, s.dependency_id = task.id, job.id, dependency.id
        s.cap_id, s.release_id = cap.id, release.id
        s.activation_id, s.key, s.token = activation_id, key, job.lease_token
        s.inputs, s.graph = copy.deepcopy(inputs), copy.deepcopy(graph)

    # These two IO seams substitute source/approval graph authority only. They
    # re-read the *passed* real Session; no detached first-Session state is used.
    def graph_lookup(actual_release, actual_business):
        assert actual_release.id == s.release_id and actual_release.workspace_id == s.p.workspace_id
        assert actual_business['id'] == 'wait_business'
        return copy.deepcopy(s.graph)

    def source_authority(db, principal, task_id, activation_id):
        actual_p = authority(db, account)
        assert (principal.user_id, principal.workspace_id, principal.role) == \
            (actual_p.user_id, actual_p.workspace_id, actual_p.role)
        task = db.scalar(select(m.Task).where(m.Task.workspace_id == principal.workspace_id,
            m.Task.id == task_id).execution_options(populate_existing=True))
        release = db.get(m.InstanceRelease, task.data['instance_release_id'])
        assert task.id == s.task_id and release.id == s.release_id
        actual_business = release.data['snapshot']['businesses'][0]
        actual_graph = graph_lookup(release, actual_business)
        actual_state = kernel.check_run(actual_graph, task.data['graph_runtime'])
        activation = actual_state['activations'][activation_id]
        actual_node = next(item for item in actual_graph['nodes'] if item['id'] == activation['node_id'])
        s.authority_reads.append(task.id)
        return task, release, actual_business, actual_graph, actual_state, activation, actual_node

    monkeypatch.setattr(waits.scope, '_frozen', source_authority)
    monkeypatch.setattr(workflow_graph, 'graph_for', graph_lookup)
    return s


def begin_wait(s, db):
    # Real leases.attach also performs the DB-clock/token/role commit fence.
    leases.attach(db, s.job_id, s.token)
    db.info['worker_actor'] = s.p
    task, job, cap = db.get(m.Task, s.task_id), db.get(m.Job, s.job_id), db.get(m.Capability, s.cap_id)
    node = next(value for value in s.graph['nodes'] if value['id'] == 'read_context')
    assert waits.preflight(db, s.p, task, job, node, cap, copy.deepcopy(s.inputs)) is True


def commit_wait(s):
    with Session(s.bound) as db:
        begin_wait(s, db)
        db.commit()


def snapshot(s):
    with Session(s.bound) as db:
        task, job = db.get(m.Task, s.task_id), db.get(m.Job, s.job_id)
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.object_id == job.id, m.Audit.action.in_([waits.WAIT, waits.WAKE]))))
        return {'task': {'id': task.id, 'version': task.version, 'status': task.status, 'data': copy.deepcopy(task.data)},
            'job': {'id': job.id, 'version': job.version, 'status': job.status, 'attempts': job.attempts,
                'key': job.idempotency_key, 'available_at': job.available_at.isoformat(),
                'lease_token': job.lease_token, 'lease_until': job.lease_until.isoformat(), 'data': copy.deepcopy(job.data)},
            'events': sorted([{'id': row.id, 'actor_id': row.actor_id, 'action': row.action,
                'object_id': row.object_id, 'data': copy.deepcopy(row.data)} for row in events], key=lambda value: value['id']),
            'job_ids': sorted(db.scalars(select(m.Job.id).where(m.Job.workspace_id == s.p.workspace_id))),
            'invocation_ids': sorted(db.scalars(select(m.Invocation.id).where(m.Invocation.workspace_id == s.p.workspace_id))),
            'acquisition_ids': sorted(db.scalars(select(m.Acquisition.id).where(m.Acquisition.workspace_id == s.p.workspace_id)))}


def settle_dependency(s):
    # This is a narrow persisted dependency-state setup, not execution of the
    # native Goal/DataAgent/175 workflow. The scheduler must read these real rows.
    with Session(s.bound) as db:
        task, dependency = db.get(m.Task, s.task_id), db.get(m.Job, s.dependency_id)
        run = m.DataRun(workspace_id=s.p.workspace_id, owner_id=s.p.user_id, status='insufficient',
            question='等待测试的原检查结果边界', data={'native_goal_task_id': task.id,
                'native_goal_context_hash': task.data['native_goal_context_hash']})
        db.add(run); db.flush()
        dependency.status = 'succeeded'
        task.data = {**task.data, 'native_goal_query_id': run.id}
        db.commit()


def test_wait_and_independent_audit_share_the_real_transaction_and_rollback(scene):
    s = scene; before = snapshot(s)
    with Session(s.bound) as db:
        begin_wait(s, db)
        db.flush()
        assert db.scalar(select(func.count()).select_from(m.Audit).where(
            m.Audit.object_id == s.job_id, m.Audit.action == waits.WAIT)) == 1
        # Separate READ COMMITTED connection cannot see either uncommitted row.
        assert snapshot(s) == before
        db.rollback()
    assert snapshot(s) == before
    assert s.authority_reads == [s.task_id]


def test_commit_close_dispose_restores_exact_original_wait_and_then_wakes_same_job(scene):
    s = scene; before = snapshot(s); commit_wait(s)
    waiting = snapshot(s); ledger = waiting['job']['data'][waits.KEY]
    record = ledger['history'][0]
    assert waiting['job']['status'] == waits.PHASE and ledger['active_hash'] == record['record_hash']
    assert record['dependencies'][0]['job_id'] == s.dependency_id
    assert record['record_hash'] == digest({k: v for k, v in record.items() if k != 'record_hash'})
    assert len(waiting['events']) == 1 and waiting['events'][0]['action'] == waits.WAIT
    assert waiting['events'][0]['data'] == waits._wait_audit(NS(id=s.job_id), record)
    assert waiting['events'][0]['actor_id'] == s.p.user_id
    assert waiting['job_ids'] == before['job_ids']
    assert waiting['invocation_ids'] == before['invocation_ids'] == []
    assert waiting['acquisition_ids'] == before['acquisition_ids'] == []
    s.bound.dispose()
    cold = snapshot(s)
    assert digest(cold) == digest(waiting)
    numbers = cold['task']['data']['human_buffers']['numbers']
    assert type(numbers[0]) is int and type(numbers[1]) is float
    assert type(numbers[2]) is float and math.copysign(1, numbers[2]) == -1
    with Session(s.bound) as db:
        task, job = db.get(m.Task, s.task_id), db.get(m.Job, s.job_id)
        assert waits._inventory(db, task, job) == ledger
        assert waits.schedule_once(db, workspace_id=s.p.workspace_id) == 0
        db.commit()
    assert snapshot(s) == waiting
    settle_dependency(s)
    with Session(s.bound) as db:
        assert 'worker_lease' not in db.info
        assert waits.schedule_once(db, workspace_id=s.p.workspace_id) == 1
        db.commit()
    s.bound.dispose()
    after = snapshot(s)
    assert after['job']['status'] == 'queued'
    for key in ('id', 'attempts', 'key', 'available_at', 'lease_token', 'lease_until'):
        assert after['job'][key] == waiting['job'][key]
    for key in ('inputs', 'input_hash', 'invocation_key', 'graph_hash', 'cancel_epoch', 'original_budget'):
        assert digest(after['job']['data'][key]) == digest(waiting['job']['data'][key])
    assert after['job_ids'] == waiting['job_ids'] and after['invocation_ids'] == [] and after['acquisition_ids'] == []
    final_ledger = after['job']['data'][waits.KEY]
    assert final_ledger['active_hash'] is None and final_ledger['history'] == ledger['history']
    assert final_ledger['woken_hashes'] == [record['record_hash']]
    assert sorted(event['action'] for event in after['events']) == sorted([waits.WAIT, waits.WAKE])
    assert after['task']['data']['graph_jobs'][s.activation_id]['phase'] == 'queued'
    with Session(s.bound) as db:
        assert waits.schedule_once(db, workspace_id=s.p.workspace_id) == 0
        db.commit()
    assert snapshot(s) == after


@pytest.mark.parametrize('fault', ['missing_wait_audit', 'dependency_failed', 'goal_revoked', 'epoch_cancelled'])
def test_cold_damage_or_revocation_stops_the_original_wait_with_persisted_readable_reason(scene, fault):
    s = scene; commit_wait(s); before = snapshot(s)
    with Session(s.bound) as db:
        if fault == 'missing_wait_audit':
            row = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
                m.Audit.object_id == s.job_id, m.Audit.action == waits.WAIT))
            assert row is not None
            db.delete(row)
        elif fault == 'dependency_failed':
            db.get(m.Job, s.dependency_id).status = 'failed'
        else:
            task = db.get(m.Task, s.task_id); data = copy.deepcopy(task.data)
            if fault == 'goal_revoked': data['native_goal_cancelled'] = True
            else: data['graph_runtime']['cancel_epoch'] += 1
            task.data = data
        db.commit()
    s.bound.dispose()
    with Session(s.bound) as db:
        assert waits.schedule_once(db, workspace_id=s.p.workspace_id) == 0
        db.commit()
    s.bound.dispose()
    after = snapshot(s)
    status = 'cancelled' if fault in {'goal_revoked', 'epoch_cancelled'} else 'blocked'
    assert after['job']['status'] == status
    transport = after['task']['data']['graph_jobs'][s.activation_id]
    assert transport['phase'] == status and type(transport['reason']) is str and transport['reason']
    assert ('原' in transport['reason']) and transport['reason'] == after['job']['data']['error']
    assert after['job']['data'][waits.KEY] == before['job']['data'][waits.KEY]
    assert after['job']['attempts'] == before['job']['attempts']
    assert after['job']['data']['original_budget'] == before['job']['data']['original_budget']
    assert after['job_ids'] == before['job_ids'] and after['invocation_ids'] == [] and after['acquisition_ids'] == []
    assert not any(event['action'] == waits.WAKE for event in after['events'])


def test_real_wake_inventory_allows_original_never_dispatched_attempt_four_without_reset(scene):
    s = scene; commit_wait(s); settle_dependency(s)
    with Session(s.bound) as db:
        assert waits.schedule_once(db, workspace_id=s.p.workspace_id) == 1
        db.commit()
    with Session(s.bound) as db:
        job = db.get(m.Job, s.job_id)
        job.status = 'running'; job.attempts = 4
        job.data = {**job.data, 'recovery_only': True}
        task = db.get(m.Task, s.task_id); data = copy.deepcopy(task.data)
        data['graph_jobs'][s.activation_id]['phase'] = 'running'; task.data = data
        db.commit()
    s.bound.dispose(); before = snapshot(s)
    with Session(s.bound) as db:
        task, job = db.get(m.Task, s.task_id), db.get(m.Job, s.job_id)
        assert waits.can_start_after_wait(db, task, job) is True
        assert not db.new and not db.dirty and not db.deleted
    assert snapshot(s) == before and before['job']['attempts'] == 4
    # An actual stored dispatch marker destroys only this no-dispatch exception.
    with Session(s.bound) as db:
        task = db.get(m.Task, s.task_id); data = copy.deepcopy(task.data)
        data['graph_jobs'][s.activation_id]['dispatch_committed'] = True; task.data = data
        db.commit()
    with Session(s.bound) as db:
        assert waits.can_start_after_wait(db, db.get(m.Task, s.task_id), db.get(m.Job, s.job_id)) is False
    after = snapshot(s)
    assert after['job_ids'] == before['job_ids'] and after['invocation_ids'] == [] and after['acquisition_ids'] == []
