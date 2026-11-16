"""Real issued graph/goal gates; source/provider seams stay synthetic.

Root alone executes this file against its owned PostgreSQL.  No successful
Task, goal, capability, Invocation, lease or authority check is fabricated.
"""
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit, Invocation, Job, Task, now
from generic_agents.worker import process_once
from tests.test_graph_wait_journey_v118 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime,
    full_source_scene, reply, human,
)


def _calls():
    with Session(engine()) as db:
        return set(db.scalars(select(Invocation.id)))


def _task(value, *, calls=12, steps=12, deadline=None, expected_jobs=1):
    client, release, obj = value['client'], value['release'], value['object']
    kind = next(row for row in release['snapshot']['object_types'] if row['key'] == obj['entity_type'])
    field = next(row['key'] for row in kind['fields'] if row['key'] in obj['attributes'])
    spec = {'purpose': 'V133按原目标限制核对已登记对象，结果仍须人工核查',
        'entity_ids': [obj['id']], 'required_fields': [field],
        'criteria': [{'id': 'enough', 'kind': 'evidence_sufficient', 'description': '原资料充分'},
            {'id': 'human', 'kind': 'human_confirmation', 'description': '人工明确验收'}],
        'subgoals': [{'id': 'original', 'description': '核对原业务', 'criterion_ids': ['enough', 'human']}],
        'evidence': {'source': 'archive', 'minimum_verification': 'recorded',
            'time': {'mode': 'archive', 'timezone': 'Asia/Shanghai'}},
        'observation_mode': 'existing_only', 'max_steps': steps, 'max_tool_calls': calls,
        'stop_on_conflict': True, 'deadline': deadline}
    body = {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [obj['id']], 'title': 'V133完整图原目标边界', 'request_id': str(uuid4()),
        'graph_inputs': {'watched_entity': obj['id']}, 'goal_spec': spec}
    path = '/api/workspace/instances/' + release['instance_id'] + '/tasks'
    task = reply(client.post(path, json=body), status=201)
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert len(task['graph_jobs']) == expected_jobs
    return task, body


def _stop(client, task):
    base = '/api/workspace/tasks/' + task['id'] + '/goal'
    goal = reply(client.get(base))
    return reply(client.post(base + '/stop', json={'request_id': str(uuid4()),
        'expected_version': goal['version'], 'reason': '明确停止原目标，保留既有输入和调用历史'}))


@pytest.mark.parametrize('condition', ['zero', 'stopped', 'expired'])
def test_real_graph_worker_cannot_call_past_original_goal(waiting_release, monkeypatch, condition):
    value = waiting_release; before = _calls(); deadline = now() + timedelta(minutes=5)
    task, body = _task(value, calls=0 if condition == 'zero' else 12,
        deadline=deadline.isoformat() if condition == 'expired' else None)
    if condition == 'stopped':
        _stop(value['client'], task)
    elif condition == 'expired':
        from generic_agents import native_goals
        monkeypatch.setattr(native_goals, 'now', lambda: deadline + timedelta(seconds=1))
    job_id = next(iter(task['graph_jobs'].values()))['job_id']
    assert process_once(job_id, deliver=False)
    assert _calls() == before, 'preflight must fail before even creating a running Invocation'
    with Session(engine()) as db:
        job = db.get(Job, job_id); row = db.get(Task, task['id'])
        assert job.status in {'failed', 'blocked', 'cancelled'}
        assert not row.data['workflow_runtime']['receipts']
        assert row.data['goal_spec']['max_tool_calls'] == body['goal_spec']['max_tool_calls']
    assert value['client'].get('/api/workspace/tasks/' + task['id']).status_code == 200


def test_real_graph_human_completion_respects_shared_step_budget_but_preserves_draft(waiting_release):
    value = waiting_release; client = value['client']; task, _ = _task(value, steps=1)
    job_id = next(iter(task['graph_jobs'].values()))['job_id']
    assert process_once(job_id, deliver=False)
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert len(task['workflow_runtime']['receipts']) == 1
    activation = next(row for row in task['graph_pending_humans'] if row['node_id'] == 'a')
    body = {'request_id': str(uuid4()), 'activation_id': activation['activation_id'],
        'version': activation['version'], 'cancel_epoch': task['graph_runtime']['cancel_epoch'],
        'output': {'ok': True}}
    denied = client.post('/api/workspace/tasks/' + task['id'] + '/graph/complete', json=body)
    assert denied.status_code == 409, denied.text
    draft = {**{key: val for key, val in body.items() if key != 'output'},
        'request_id': str(uuid4()), 'draft': {'ok': True}}
    saved = reply(client.put('/api/workspace/tasks/' + task['id'] + '/graph/draft', json=draft))
    assert len(saved['workflow_runtime']['receipts']) == 1
    assert saved['graph_runtime']['activations'][activation['activation_id']]['draft'] == {'ok': True}


def test_real_completed_human_original_nonce_replays_after_goal_stop_without_progress(waiting_release):
    value = waiting_release; client = value['client']; task, _ = _task(value)
    task, body = human(client, task, 'a')
    _stop(client, task)
    with Session(engine()) as db:
        before = copy.deepcopy(db.get(Task, task['id']).data)
        jobs = set(db.scalars(select(Job.id)))
    calls = _calls(); path = '/api/workspace/tasks/' + task['id'] + '/graph/complete'
    assert client.post(path, json=body).status_code == 200
    assert client.get('/api/workspace/tasks/' + task['id'] + '/graph/requests/' + body['request_id']).status_code == 200
    with Session(engine()) as db:
        assert db.get(Task, task['id']).data == before
        assert set(db.scalars(select(Job.id))) == jobs
    assert _calls() == calls


@pytest.mark.parametrize('condition', ['stopped', 'expired'])
def test_actual_running_invocation_is_not_permission_at_final_dispatch(waiting_release, monkeypatch, condition):
    from generic_agents import workflow_graph_scope, native_goals
    value = waiting_release; client = value['client']; deadline = now() + timedelta(minutes=5)
    task, _ = _task(value, deadline=deadline.isoformat() if condition == 'expired' else None)
    job_id = next(iter(task['graph_jobs'].values()))['job_id']; calls = _calls()
    original = workflow_graph_scope.before_dispatch; reached = []

    def change_goal_then_real_fence(db, p, key, cap):
        # The gateway has committed the real running Invocation. Change the
        # real goal, then run the unchanged production fence; never fake IO.
        reached.append(key)
        if condition == 'stopped': _stop(client, task)
        else: monkeypatch.setattr(native_goals, 'now', lambda: deadline + timedelta(seconds=1))
        return original(db, p, key, cap)

    monkeypatch.setattr(workflow_graph_scope, 'before_dispatch', change_goal_then_real_fence)
    assert process_once(job_id, deliver=False); assert len(reached) == 1
    with Session(engine()) as db:
        new = list(db.scalars(select(Invocation).where(Invocation.id.not_in(calls))))
        assert len(new) == 1 and new[0].status != 'succeeded' and 'output' not in new[0].data
        row = db.get(Task, task['id'])
        assert not row.data['workflow_runtime']['receipts']
        assert all('dispatch_authorization' not in item for item in row.data['graph_jobs'].values())
        assert row.data['graph_goal_reservations_v133']['items'], 'an attempted slot is never silently refunded'


def test_actual_success_returned_after_goal_stop_is_history_without_graph_progress(waiting_release, monkeypatch):
    from generic_agents import gateway
    value = waiting_release; client = value['client']; task, _ = _task(value)
    job_id = next(iter(task['graph_jobs'].values()))['job_id']; before = _calls()
    original = gateway.invoke; completed = []

    def real_call_then_stop(*args, **kwargs):
        call = original(*args, **kwargs)
        assert call.status == 'succeeded'; completed.append(call.id)
        _stop(client, task)
        return call

    monkeypatch.setattr(gateway, 'invoke', real_call_then_stop)
    assert process_once(job_id, deliver=False); assert len(completed) == 1
    with Session(engine()) as db:
        row = db.get(Task, task['id']); call = db.get(Invocation, completed[0])
        assert call.status == 'succeeded' and call.data['output']
        transport = next(iter(row.data['graph_jobs'].values()))
        assert transport['phase'] == 'late_result_preserved' and transport['invocation_id'] == call.id
        assert 'dispatch_authorization' in transport
        assert not row.data['workflow_runtime']['receipts']
        assert row.data['graph_runtime']['activations'][next(iter(row.data['graph_jobs']))]['status'] != 'completed'
    assert _calls() == before | set(completed)
    restored = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert restored['graph_goal_gate_v133']['status'] == 'stopped'


def _two_ability_release(value):
    """Explicit real v4 successor; no issued release/Task/Job is edited."""
    from generic_agents.contracts import digest
    from tests.test_full_semantic_instance_journey_v113 import generate
    from tests.test_report_full_semantic_graph_binding_v3 import edge, ref
    client = value['client']; old = value['adopt_body']; path = value['adopt_path']
    parent = reply(client.get(path + '/requests/' + old['request_id']))['receipt']
    current = reply(client.get('/api/setup/draft'))
    command = copy.deepcopy({key: val for key, val in old.items()
        if key not in {'request_id', 'preview_hash', 'consent', 'semantic_mode'}})
    command.update(schema='report-full-semantic-graph-revision-preview-command-v4',
        parent={'id': parent['id'], 'receipt_hash': parent['receipt_hash']},
        configuration_revision=current['revision'], configuration_hash=digest(current['configuration']))
    graph = command['graph']; extra = copy.deepcopy(next(node for node in graph['nodes'] if node['id'] == 'b'))
    extra.update(id='b_parallel', source_step_id=None); graph['nodes'].append(extra)
    # The original human branch still leads to that same source step. Its
    # additional registered read is explicitly adopted before the human work.
    next(row for row in graph['edges'] if row['id'] == 'f_a')['to'] = extra['id']
    graph['edges'].append(edge('parallel_before_a', extra['id'], 'a'))
    command['node_actions'][extra['id']] = command['node_actions']['b']
    owner = next(row for row in command['owner_bindings'] if row['owner_path'] == '/global_claims/0')
    owner['reason'] = '本合成工作区明确在原人工分支前增加同一已登记只读统计，与原统计分支并行；不冒充原文事实或现场动作'
    next(row for row in owner['clauses'] if row['source_field'] == 'statement')['targets'].append(ref('node', extra['id']))
    preview = reply(client.post(path + '/revisions/preview', json=command))
    body = {**command, 'schema': 'report-full-semantic-graph-revision-command-v4',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True,
        'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(path + '/revisions/adopt', json=body))
    assert accepted['receipt']['parent_id'] == parent['id']
    current = reply(client.get('/api/setup/draft')); compilation = copy.deepcopy(value['compilation'])
    compilation.update(revision=current['revision'], role_bindings=command['role_bindings'])
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert not checked['general_issues'], checked['general_issues']
    release = generate(client, compilation, checked, instance_id=value['release']['instance_id'])
    instance = next(row for row in reply(client.get('/api/instances')) if row['id'] == release['instance_id'])
    reply(client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    return {**value, 'release': release, 'adopt_body': body}


def test_actual_parallel_graph_workers_share_one_reserved_call_before_any_invocation(waiting_release, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from generic_agents import gateway
    from generic_agents.goal_queries import call_count
    from generic_agents.native_step_execution import step_count
    from generic_agents.security import Principal
    from generic_agents.workflow_graph_goal_guard_v133 import pending_steps
    value = _two_ability_release(waiting_release); before = _calls()
    task, _ = _task(value, calls=1, expected_jobs=2)
    transports = list(task['graph_jobs'].values()); first, second = transports[0]['job_id'], transports[1]['job_id']
    first_key = transports[0]['invocation_key']; entered = threading.Event(); release = threading.Event()
    original = gateway.invoke

    def hold_before_real_invocation(db, p, capability_id, agent_role, inputs, key, **kwargs):
        if key == first_key:
            entered.set(); assert release.wait(20), 'test gate was not released'
        return original(db, p, capability_id, agent_role, inputs, key, **kwargs)

    monkeypatch.setattr(gateway, 'invoke', hold_before_real_invocation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(process_once, first, deliver=False)
        try:
            assert entered.wait(20), 'first real worker did not reach its committed preflight'
            assert _calls() == before
            # An independently read real reservation is visible to both shared
            # counters even while no Invocation exists. This is not authority.
            me = reply(value['client'].get('/api/me'))
            p = Principal(me['user_id'], me['workspace_id'], me['role'], 'v133-readonly-budget-probe')
            with Session(engine()) as db:
                row = db.get(Task, task['id'])
                assert call_count(db, p, row) == 1 and step_count(row) == 1 and pending_steps(row) == 1
            two = pool.submit(process_once, second, deliver=False)
            assert two.result(timeout=20)
            assert _calls() == before
            with Session(engine()) as db:
                assert db.get(Job, second).status in {'failed', 'blocked'}
        finally:
            release.set()
        assert one.result(timeout=20)
    assert len(_calls() - before) == 1
    with Session(engine()) as db:
        row = db.get(Task, task['id'])
        assert len(row.data['workflow_runtime']['receipts']) == 1
        assert call_count(db, p, row) == 1, 'actual Invocation replaces the same reserved key, never double counts'
        assert pending_steps(row) == 0, 'the actual workflow receipt already counts the reserved step'


def _actual_failed_attempt(value, monkeypatch, *, steps=1):
    """Run the real original fence, then inject a transport failure before IO."""
    from fastapi import HTTPException
    from generic_agents import workflow_graph_scope
    task, _ = _task(value, calls=2, steps=steps)
    job_id = next(iter(task['graph_jobs'].values()))['job_id']
    before = _calls(); original = workflow_graph_scope.before_dispatch
    with monkeypatch.context() as patch:
        def failed_after_real_authorization(*args, **kwargs):
            original(*args, **kwargs)
            raise HTTPException(409, '合成传输故障；实际授权已保存，未产生工具输出')
        patch.setattr(workflow_graph_scope, 'before_dispatch', failed_after_real_authorization)
        assert process_once(job_id, deliver=False)
    with Session(engine()) as db:
        calls = list(db.scalars(select(Invocation).where(Invocation.id.not_in(before))))
        assert len(calls) == 1 and calls[0].status == 'blocked_evidence' and 'output' not in calls[0].data
        row = db.get(Task, task['id']); job = db.get(Job, job_id)
        assert not row.data['workflow_runtime']['receipts']
        assert row.data['graph_goal_reservations_v133']['items']
        assert job.status in {'failed', 'blocked'}
        return task, job_id, calls[0].id


def test_real_reservation_inventory_cannot_be_erased_or_resealed_to_complete_an_extra_step(waiting_release, monkeypatch):
    """Each mutation keeps all unrelated actual records, then is restored exactly.

    This is a cold-authority negative test, not a successful state fabricated
    by the fixture. Every attempted completion goes through the real API.
    """
    from generic_agents.contracts import digest
    value = waiting_release; client = value['client']
    task, job_id, call_id = _actual_failed_attempt(value, monkeypatch)
    path = '/api/workspace/tasks/' + task['id']
    current = reply(client.get(path))
    activation = next(row for row in current['graph_pending_humans'] if row['node_id'] == 'a')
    with Session(engine()) as db:
        original = copy.deepcopy(db.get(Task, task['id']).data)
        original_job = copy.deepcopy(db.get(Job, job_id).data)
        original_call = copy.deepcopy(db.get(Invocation, call_id).data)
        entry = db.scalar(select(Audit).where(Audit.object_id == task['id'],
            Audit.action == 'workflow.graph.goal_reserved'))
        assert entry is not None
        audit_id = entry.id; original_audit = copy.deepcopy(entry.data); audit_action = entry.action
    calls = _calls()
    mutations = ['missing_ledger', 'empty_items_resealed', 'changed_actor_resealed',
        'missing_audit', 'changed_audit', 'changed_job_input', 'changed_job_actor', 'changed_call_key']
    for mutation in mutations:
        with Session(engine()) as db:
            row = db.get(Task, task['id']); data = copy.deepcopy(original)
            ledger = data['graph_goal_reservations_v133']; key = next(iter(ledger['items']))
            if mutation == 'missing_ledger': data.pop('graph_goal_reservations_v133')
            elif mutation == 'empty_items_resealed': ledger['items'] = {}
            elif mutation == 'changed_actor_resealed':
                item = ledger['items'][key]; item['actor_id'] = str(uuid4())
                item['reservation_hash'] = digest({k: v for k, v in item.items() if k != 'reservation_hash'})
            if mutation in {'empty_items_resealed', 'changed_actor_resealed'}:
                ledger['ledger_hash'] = digest({k: v for k, v in ledger.items() if k != 'ledger_hash'})
            row.data = data
            entry = db.get(Audit, audit_id)
            if mutation == 'missing_audit': entry.action = 'v133.synthetic.removed_reservation'
            elif mutation == 'changed_audit': entry.data = {**entry.data, 'reservation_hash': '0' * 64}
            job = db.get(Job, job_id)
            if mutation == 'changed_job_input': job.data = {**original_job, 'input_hash': '0' * 64}
            elif mutation == 'changed_job_actor': job.data = {**original_job, 'actor_id': str(uuid4())}
            if mutation == 'changed_call_key': db.get(Invocation, call_id).idempotency_key = 'v133:wrong:' + str(uuid4())
            db.commit()
        try:
            command = {'request_id': str(uuid4()), 'activation_id': activation['activation_id'],
                'version': activation['version'], 'cancel_epoch': current['graph_runtime']['cancel_epoch'],
                'output': {'ok': True}}
            denied = client.post(path + '/graph/complete', json=command)
            assert denied.status_code == 409, (mutation, denied.text)
            with Session(engine()) as db:
                assert not db.get(Task, task['id']).data['workflow_runtime']['receipts']
            assert _calls() == calls
        finally:
            with Session(engine()) as db:
                db.get(Task, task['id']).data = copy.deepcopy(original)
                db.get(Job, job_id).data = copy.deepcopy(original_job)
                call = db.get(Invocation, call_id); call.data = copy.deepcopy(original_call)
                call.idempotency_key = original_job['invocation_key']
                entry = db.get(Audit, audit_id); entry.action = audit_action; entry.data = copy.deepcopy(original_audit)
                db.commit()
    # The intact failed attempt also consumes the original one-step budget.
    assert client.post(path + '/graph/complete', json={**command, 'request_id': str(uuid4())}).status_code == 409


def test_pre_v133_failed_attempt_is_not_resigned_but_actual_history_and_draft_remain_readable(waiting_release, monkeypatch):
    value = waiting_release; client = value['client']
    task, job_id, call_id = _actual_failed_attempt(value, monkeypatch, steps=3)
    with Session(engine()) as db:
        row = db.get(Task, task['id']); data = copy.deepcopy(row.data)
        data.pop('graph_goal_reservations_v133'); row.data = data
        entry = db.scalar(select(Audit).where(Audit.object_id == task['id'],
            Audit.action == 'workflow.graph.goal_reserved'))
        # Model a genuinely pre-reservation record by removing only the new
        # independent anchor. Its old Job/Invocation/dispatch proof are intact.
        db.delete(entry); db.commit()
    path = '/api/workspace/tasks/' + task['id']; current = reply(client.get(path)); calls = _calls()
    activation = next(row for row in current['graph_pending_humans'] if row['node_id'] == 'a')
    base = {'request_id': str(uuid4()), 'activation_id': activation['activation_id'],
        'version': activation['version'], 'cancel_epoch': current['graph_runtime']['cancel_epoch']}
    saved = reply(client.put(path + '/graph/draft', json={**base, 'draft': {'ok': True}}))
    assert saved['graph_runtime']['activations'][activation['activation_id']]['draft'] == {'ok': True}
    denied = client.post(path + '/graph/complete', json={**base, 'request_id': str(uuid4()),
        'version': saved['graph_runtime']['activations'][activation['activation_id']]['version'], 'output': {'ok': True}})
    assert denied.status_code == 409, denied.text
    with Session(engine()) as db:
        row = db.get(Task, task['id'])
        assert 'graph_goal_reservations_v133' not in row.data
        assert db.get(Invocation, call_id).status == 'blocked_evidence'
        assert db.get(Job, job_id) is not None and not row.data['workflow_runtime']['receipts']
    assert _calls() == calls


def test_pre_v133_successful_receipt_remains_usable_without_manufacturing_a_reservation(waiting_release):
    value = waiting_release; client = value['client']; task, _ = _task(value)
    job_id = next(iter(task['graph_jobs'].values()))['job_id']
    assert process_once(job_id, deliver=False)
    with Session(engine()) as db:
        row = db.get(Task, task['id']); data = copy.deepcopy(row.data)
        assert len(data['workflow_runtime']['receipts']) == 1
        data.pop('graph_goal_reservations_v133'); row.data = data
        entry = db.scalar(select(Audit).where(Audit.object_id == task['id'],
            Audit.action == 'workflow.graph.goal_reserved'))
        db.delete(entry); db.commit()
    calls = _calls(); current = reply(client.get('/api/workspace/tasks/' + task['id']))
    current, original = human(client, current, 'a')
    assert len(current['workflow_runtime']['receipts']) == 2
    assert client.post('/api/workspace/tasks/' + task['id'] + '/graph/complete', json=original).status_code == 200
    with Session(engine()) as db:
        assert 'graph_goal_reservations_v133' not in db.get(Task, task['id']).data
    assert _calls() == calls


def test_real_zero_call_goal_cannot_be_downgraded_by_stripping_all_task_goal_markers(waiting_release):
    value = waiting_release; client = value['client']; task, _ = _task(value, calls=0)
    job_id = next(iter(task['graph_jobs'].values()))['job_id']; calls = _calls()
    with Session(engine()) as db:
        row = db.get(Task, task['id'])
        assert db.scalar(select(Audit.id).where(Audit.workspace_id == row.workspace_id,
            Audit.object_id == row.id, Audit.action == 'native_goal.defined'))
        row.data = {key: copy.deepcopy(val) for key, val in row.data.items()
            if not key.startswith(('native_goal_', 'goal_')) and key != 'graph_goal_reservations_v133'}
        db.commit()
    assert process_once(job_id, deliver=False)
    assert _calls() == calls
    with Session(engine()) as db:
        row = db.get(Task, task['id'])
        assert not row.data['workflow_runtime']['receipts']
        assert db.get(Job, job_id).status in {'failed', 'blocked'}
    restored = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert restored['graph_goal_gate_v133']['status'] == 'unavailable'
