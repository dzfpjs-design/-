"""Actual retry/helper bodies over explicit SQL, graph and authority IO spies.

V285 manifest, full V286 binding, service eligibility, original identity/hash,
nonce/version checks and the new failure marker run as production functions.
The fixed rows, graph input resolution, ActorGuard/live authority and SELECT
results are spies, not real locks, PG, IAM, provider, dispatch or E2E evidence.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, or_, select

from generic_agents import workflow_graph_api as api
from generic_agents import workflow_local_pre_dispatch_recovery_v287 as subject
from generic_agents import workflow_decision_tool_planner_v285 as planner
from generic_agents import workflow_graph_scope as scope
from generic_agents import workflow_graph_goal_guard_v133 as goals
from generic_agents import leases, relations
from generic_agents.contracts import digest
from generic_agents.models import Audit, Invocation, Job
from tests.test_authored_decision_workflow_v286 import command, cold_binding, ident
from tests.test_workflow_decision_tool_planner_v285 import case


def sql(statement):
    return str(statement.compile(compile_kwargs={'literal_binds': True}))


def scene(monkeypatch):
    value = case(); cap = value.cap
    cap.kind = 'tool'; cap.status = 'published'; cap.release_version = cap.data['version']
    cap.validated_hash = cap.content_hash
    body = command()
    body['tool_bindings']['planner'].update(capability_id=cap.id, capability_hash=cap.content_hash)
    body['tool_bindings']['resense'].update({key: value.target[key]
        for key in ('capability_id', 'capability_hash')})
    body['candidate_specs'][0]['capability_pins'][0].update(value.target)
    binding = cold_binding(body); graph = binding['graph']
    node = next(row for row in graph['nodes'] if row['id'] == 'planner')
    p = NS(workspace_id=ident(601), user_id=node['actor_id'], role='operator')
    inputs = {'decision_result': copy.deepcopy(value.result)}
    activation = {'id': 'planner_activation', 'node_id': 'planner', 'version': 0, 'status': 'ready',
        'epoch': 0, 'inputs_snapshot': {'arguments': copy.deepcopy(inputs)}}
    state = {'status': 'waiting', 'cancel_epoch': 0, 'graph_hash': digest(graph), 'commands': [],
        'eligible_ability_ids': [activation['id']], 'activations': {activation['id']: activation}}
    task = NS(id=ident(602), workspace_id=p.workspace_id, version=1, data={'graph_runtime': state})
    key = 'graph:' + task.id + ':' + activation['id'] + ':1'
    job = NS(id=ident(603), workspace_id=p.workspace_id, kind=scope.KIND, status='failed', attempts=1,
        version=3, lease_token=ident(604), lease_until=None, available_at=None, idempotency_key=key,
        data={'task_id': task.id, 'actor_id': p.user_id, 'activation_id': activation['id'],
            'activation_version': 0, 'graph_hash': state['graph_hash'], 'cancel_epoch': 0,
            'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs), 'invocation_key': key,
            'attempt': 1, 'recovery_only': False, 'lease_recoveries': 0,
            'error': '后台执行已停止，请核查数据、能力及授权后恢复', 'finished_at': '2026-11-01T12:00:00+00:00'})
    item = {'job_id': job.id, 'invocation_key': key, 'input_hash': digest(inputs), 'attempt': 1,
        'cancel_epoch': 0, 'phase': 'blocked', 'reason': job.data['error']}
    task.data[api.runtime.TRANSPORT] = {activation['id']: item}
    release = NS(id=ident(605), package_id=ident(606))
    package = NS(id=release.package_id, data={'dependencies': [cap.id]})
    business = {'name': binding['business_name']}
    request = api.Retry(request_id=ident(607), activation_id=activation['id'], version=0,
        cancel_epoch=0, expected_attempt=1, confirm_repeat_read=False)
    events = []; calls = []; faults = set()

    queries = {}
    def query(name, statement): queries[sql(statement)] = name
    query('api_call', select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key).with_for_update())
    query('any_call', select(Invocation.id).where(Invocation.workspace_id == p.workspace_id,
        or_(Invocation.idempotency_key == key, Invocation.data['job_id'].as_string() == job.id)).limit(1))
    query('other_job', select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == scope.KIND,
        Job.id != job.id, Job.data['task_id'].as_string() == task.id,
        Job.data['activation_id'].as_string() == activation['id']).limit(1))
    query('dispatch_or_prior_retry', select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action.in_(['workflow.graph.dispatch',
            'workflow.graph.dispatch_authorized', 'workflow.graph.node_completed', 'workflow.graph.recovery_requested']),
        or_(Audit.data['job_id'].as_string() == job.id,
            Audit.data['activation_id'].as_string() == activation['id'])).limit(1))
    query('prior_local_recovery', select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == job.id, Audit.action == subject.ACTION).limit(1))
    query('effect_audit', select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action.in_(['workflow.graph.dispatch',
            'workflow.graph.dispatch_authorized', 'workflow.graph.node_completed']),
        or_(Audit.data['job_id'].as_string() == job.id,
            Audit.data['activation_id'].as_string() == activation['id'])).limit(1))
    query('graph_call', select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key))
    found = {name: None for name in queries.values()}
    audit_queries = {sql(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == job.id, Audit.action == action).limit(limit)): action
        for action, limit in ((subject.ACTION, 2), (subject.CONTINUED, subject.MAX_CONTINUATIONS + 1))}
    audit_queries[sql(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.graph.recovery_requested',
        Audit.data['activation_id'].as_string() == activation['id']).limit(subject.MAX_CONTINUATIONS + 2))] = 'workflow.graph.recovery_requested'
    recovery_query = sql(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.status.in_(['running', 'unknown', 'failed', 'blocked_evidence', 'blocked_model', 'succeeded', 'not_executed']),
        or_(Invocation.data['job_id'].as_string() == job.id, Invocation.idempotency_key == key))
        .order_by(Invocation.created_at.desc()).with_for_update())

    class ScalarRows(list):
        def all(self): return list(self)

    class IO:
        info = {}
        commits = 0
        def scalar(self, statement):
            current_lease = select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
                Job.kind == scope.KIND, Job.status == 'running', Job.lease_token == self.info.get('worker_lease', {}).get('lease_token'),
                Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True)
            if sql(statement) == sql(current_lease):
                assert statement.get_execution_options().get('populate_existing') is True
                calls.append('current-lease')
                return None if 'lease' in faults else job
            assert sql(statement) in queries, 'unrecognized SQL is rejected'
            name = queries[sql(statement)]; calls.append(name)
            return found[name]
        def scalars(self, statement):
            if sql(statement) == recovery_query:
                calls.append('real-recover-calls')
                return ScalarRows([])
            assert sql(statement) in audit_queries, 'unrecognized certificate SQL is rejected'
            action = audit_queries[sql(statement)]
            return ScalarRows(event for event in events if event.action == action)
        def add(self, row): pytest.fail('retry created a row or SDK Invocation')
        def flush(self): pytest.fail('retry attempted to create a replacement Job')
        def commit(self): self.commits += 1

    db = IO()
    def context(actual_db, actual_p, identity, lock=False):
        assert actual_db is db and actual_p is p and identity == task.id
        calls.append('task-lock' if lock else 'initial')
        return task, release, business, graph
    def scoped(actual_db, cls, identity, actual_p, lock=False):
        assert actual_db is db and actual_p is p and cls is Job and identity == job.id and lock is True
        calls.append('job-lock')
        return job
    def actor_guard(actual_db, actual_p):
        assert actual_db is db and actual_p is p
        calls.append('actor')
        if 'actor' in faults: raise HTTPException(403, 'synthetic actor denial')
    def live(*args, **kwargs):
        calls.append('live')
        if 'live' in faults: raise HTTPException(409, 'synthetic current approval/live denial')
    def goal_progress(*args, **kwargs):
        calls.append('goal')
        if 'goal' in faults: raise HTTPException(409, 'synthetic stopped or expired Goal denial')
    def save(actual_db, actual_p, action, object_id, data):
        assert actual_db is db and actual_p is p
        events.append(NS(action=action, object_id=object_id, actor_id=p.user_id, data=copy.deepcopy(data)))
    monkeypatch.setattr(api, 'context', context)
    monkeypatch.setattr(api, 'scoped', scoped)
    monkeypatch.setattr(relations, 'lock_current_actor', actor_guard)
    monkeypatch.setattr(api.runtime, 'live', live)
    monkeypatch.setattr(goals, 'progress', goal_progress)
    monkeypatch.setattr(api.runtime, 'business_binding', lambda *args: binding)
    monkeypatch.setattr(scope, '_frozen', lambda *args:
        (task, release, business, graph, state, activation, node))
    monkeypatch.setattr(api.runtime.graph_api, 'ability_inputs', lambda *args: copy.deepcopy(inputs))
    monkeypatch.setattr(subject, 'published', lambda actual_db, actual_p, identity, *args:
        cap if identity == cap.id else package if identity == package.id else pytest.fail('unexpected capability'))
    monkeypatch.setattr(subject, 'audit', save)
    monkeypatch.setattr(api, 'audit', save)
    monkeypatch.setattr(api.runtime, 'public_view', lambda actual_task, *args, **kwargs:
        {'task_id': actual_task.id, 'job_id': job.id, 'status': job.status})
    original_fenced = leases.fenced
    # The real listener installation is a separate PG/worker proof. This pure
    # fixture labels its kernel witness boundary explicitly.
    monkeypatch.setattr(leases, 'fenced', lambda actual_db: actual_db.info.get('synthetic_fence') is True)
    return NS(db=db, p=p, task=task, release=release, business=business, graph=graph,
        node=node, activation=activation, job=job, item=item, cap=cap, binding=binding,
        request=request, events=events, calls=calls, found=found, faults=faults, original_fenced=original_fenced)


def retry(s): return api.retry(s.task.id, s.request, s.p, s.db)


def test_actual_retry_requeues_only_original_local_job_and_preserves_original_failure(monkeypatch):
    s = scene(monkeypatch)
    before = copy.deepcopy(s.job.data); original_key = s.job.idempotency_key
    assert s.job.lease_token is not None and s.job.lease_until is None
    result = retry(s)
    assert result == {'task_id': s.task.id, 'job_id': s.job.id, 'status': 'queued'}
    assert s.job.idempotency_key == original_key and s.job.attempts == 1
    assert s.job.data['recovery_only'] is False and s.job.lease_token is s.job.lease_until is None
    record = s.job.data[subject.KEY]
    assert record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'})
    assert record['request_id'] == str(s.request.request_id) and record['job_data_hash'] == digest(before)
    assert record['failure'] == {'status': 'failed', 'error': before['error'], 'finished_at': before['finished_at'],
        'job_version': 3, 'attempts': 1, 'transport': s.item}
    assert s.job.data['error'] == before['error'] and s.job.data['finished_at'] == before['finished_at']
    assert s.calls[:4] == ['initial', 'job-lock', 'task-lock', 'actor']
    assert s.calls[-2:] == ['live', 'goal'] and s.db.commits == 1
    assert [event.action for event in s.events] == [subject.ACTION, 'workflow.graph.recovery_requested']
    assert s.events[0].data == record
    assert s.task.data[api.runtime.TRANSPORT][s.request.activation_id]['phase'] == 'queued'


def test_original_nonce_replay_returns_saved_response_without_requeue_or_second_audit(monkeypatch):
    s = scene(monkeypatch); retry(s)
    before = copy.deepcopy((s.job.data, s.task.data)); version = s.job.version
    count = len(s.events); s.calls.clear()
    retry(s)
    assert s.calls == ['initial', 'task-lock', 'actor']
    assert (s.job.data, s.task.data) == before and s.job.version == version
    assert len(s.events) == count and s.db.commits == 1


def test_saved_nonce_cannot_replace_original_body_and_cross_operation_nonce_is_not_reused(monkeypatch):
    s = scene(monkeypatch); retry(s)
    s.request = s.request.model_copy(update={'confirm_repeat_read': True})
    with pytest.raises(HTTPException): retry(s)
    assert s.db.commits == 1 and len(s.events) == 2
    other = scene(monkeypatch)
    other.task.data['graph_runtime']['commands'] = [{'request_id': str(other.request.request_id)}]
    with pytest.raises(HTTPException): retry(other)
    assert other.calls == ['initial'] and not other.events


@pytest.mark.parametrize('proof', ['any_call', 'other_job', 'dispatch_or_prior_retry', 'prior_local_recovery'])
def test_independent_retained_row_or_audit_blocks_recovery_even_without_transport_marker(monkeypatch, proof):
    s = scene(monkeypatch); s.found[proof] = ident(699)
    before = copy.deepcopy(s.job.data)
    with pytest.raises(HTTPException): retry(s)
    assert s.job.status == 'failed' and s.job.data == before and not s.events and s.db.commits == 0


@pytest.mark.parametrize('key,value', [('dispatch_committed', False), ('dispatch_ownership', None),
    ('dispatch_authorization', {}), ('invocation_id', None), ('invocation_status', None), ('previous', [])])
def test_any_transport_dispatch_or_previous_marker_is_ambiguous_even_if_null_false_or_empty(monkeypatch, key, value):
    s = scene(monkeypatch); s.item[key] = value
    with pytest.raises(HTTPException): retry(s)
    assert not s.events and s.job.status == 'failed'


@pytest.mark.parametrize('fault', ['attempts', 'lease_until', 'attached_owner', 'lease_recoveries',
    'input_hash', 'old_marker', 'job_dispatch', 'wrong_actor', 'old_version', 'prior_retry', 'foreign_op'])
def test_narrow_first_attempt_current_identity_and_fixed_local_operation_are_required(monkeypatch, fault):
    s = scene(monkeypatch)
    if fault == 'attempts': s.job.attempts = 3
    elif fault == 'lease_until': s.job.lease_until = 'retained deadline'
    elif fault == 'attached_owner': s.db.info = {'worker_lease': {'job_id': s.job.id, 'lease_token': 'active'}}
    elif fault == 'lease_recoveries': s.job.data['lease_recoveries'] = 1
    elif fault == 'input_hash': s.job.data['input_hash'] = '0' * 64
    elif fault == 'old_marker': s.job.data[subject.KEY] = None
    elif fault == 'job_dispatch': s.job.data['dispatch_committed'] = False
    elif fault == 'wrong_actor': s.p.user_id = ident(698)
    elif fault == 'old_version': s.activation['version'] = 1
    elif fault == 'prior_retry': s.task.data['graph_retry_requests'] = {ident(697): {'activation_id': s.activation['id']}}
    else:
        s.cap.data['execution']['operation'] = 'plan_business_decision_v277'
        s.cap.content_hash = s.cap.validated_hash = digest(s.cap.data)
    with pytest.raises(HTTPException): retry(s)
    assert s.job.status == 'failed' and not s.events and s.db.commits == 0


@pytest.mark.parametrize('fault', ['actor', 'live', 'goal'])
def test_original_actor_and_current_approval_live_denials_happen_before_persisting_recovery(monkeypatch, fault):
    s = scene(monkeypatch); s.faults.add(fault)
    with pytest.raises(HTTPException): retry(s)
    assert subject.KEY not in s.job.data and s.job.status == 'failed' and not s.events


def test_existing_successful_service_invocation_keeps_original_recovery_only_path(monkeypatch):
    s = scene(monkeypatch); s.found['api_call'] = NS(status='succeeded', data={})
    retry(s)
    assert s.job.status == 'queued' and s.job.data['recovery_only'] is True
    assert subject.KEY not in s.job.data and 'any_call' not in s.calls
    assert [event.action for event in s.events] == ['workflow.graph.recovery_requested']


def test_existing_unknown_service_invocation_is_never_reclassified_as_predispatch(monkeypatch):
    s = scene(monkeypatch); s.found['api_call'] = NS(status='unknown', data={})
    with pytest.raises(HTTPException): retry(s)
    assert subject.KEY not in s.job.data and s.job.status == 'failed' and not s.events


def claimed(s, *, attempts=2):
    """Synthetic lease row; actual fence/listener behavior belongs to PG tests."""
    s.job.status = 'running'; s.job.attempts = attempts; s.job.lease_token = ident(680)
    s.job.lease_until = datetime.now(timezone.utc) + timedelta(minutes=3)
    s.job.data = {**s.job.data, 'recovery_only': True}
    s.db.info = {'worker_lease': {'job_id': s.job.id, 'lease_token': s.job.lease_token}, 'synthetic_fence': True}


def blocked_before_sdk(s, *, attempts=2):
    s.job.status = 'blocked'; s.job.attempts = attempts; s.job.lease_until = None; s.job.version += 1
    reason = '恢复次数已达上限且未找到成功回执，不能自动重新执行'
    s.job.data = {**s.job.data, 'recovery_only': True, 'error': reason,
        'finished_at': '2026-11-01T12:01:00+00:00'}
    item = s.task.data[api.runtime.TRANSPORT][s.request.activation_id]
    s.task.data[api.runtime.TRANSPORT][s.request.activation_id] = {**item, 'phase': 'blocked', 'reason': reason}
    s.db.info = {}


def test_worker_exception_checks_original_certificate_without_changing_it_or_attempts(monkeypatch):
    s = scene(monkeypatch); retry(s); claimed(s)
    original = copy.deepcopy(s.job.data[subject.KEY]); before = copy.deepcopy(s.job.data)
    count = len(s.events); commits = s.db.commits
    assert subject.can_start_after_pre_dispatch_recovery(s.db, s.p, s.task, s.job) is True
    assert s.job.data == before and s.job.data[subject.KEY] == original
    assert s.job.attempts == 2 and s.job.data['recovery_only'] is True
    assert len(s.events) == count and s.db.commits == commits


def test_normal_new_nonce_continues_existing_certificate_and_preserves_both_actual_failures(monkeypatch):
    s = scene(monkeypatch); retry(s)
    original = copy.deepcopy(s.job.data[subject.KEY]); original_request = s.request
    blocked_before_sdk(s)
    failure = copy.deepcopy(s.job.data); s.request = s.request.model_copy(update={'request_id': ident(608)})
    retry(s)
    history = s.job.data[subject.CONTINUATIONS]
    assert len(history) == 1 and history[0]['original_record_hash'] == original['record_hash']
    assert history[0]['failure']['status'] == 'blocked' and history[0]['failure']['error'] == failure['error']
    assert history[0]['failure']['finished_at'] == failure['finished_at']
    assert history[0]['failure']['attempts'] == 2 and history[0]['job_data_hash'] == digest(failure)
    assert s.job.data[subject.KEY] == original and s.job.attempts == 2
    assert [event.action for event in s.events].count(subject.ACTION) == 1
    assert [event.action for event in s.events].count(subject.CONTINUED) == 1
    saved = copy.deepcopy((s.job.data, s.task.data)); count = len(s.events)
    s.request = original_request
    retry(s)
    assert (s.job.data, s.task.data) == saved and len(s.events) == count
    claimed(s, attempts=3)
    assert subject.can_start_after_pre_dispatch_recovery(s.db, s.p, s.task, s.job) is True
    assert s.job.attempts == 3 and s.job.data[subject.KEY] == original


def test_continuations_are_bounded_without_resetting_worker_attempts_or_original_proof(monkeypatch):
    s = scene(monkeypatch); retry(s); original = copy.deepcopy(s.job.data[subject.KEY])
    for index in range(subject.MAX_CONTINUATIONS):
        blocked_before_sdk(s, attempts=3)
        s.request = s.request.model_copy(update={'request_id': ident(620 + index)})
        retry(s)
        assert s.job.attempts == 3 and s.job.data[subject.KEY] == original
    blocked_before_sdk(s, attempts=3)
    s.request = s.request.model_copy(update={'request_id': ident(630)})
    before = copy.deepcopy(s.job.data); count = len(s.events)
    with pytest.raises(HTTPException): retry(s)
    assert s.job.data == before and s.job.status == 'blocked' and len(s.events) == count


@pytest.mark.parametrize('damage', ['original_audit', 'original_nonce', 'nonce_body', 'common_audit',
    'extra_continuation_audit', 'lease', 'actor', 'live', 'goal', 'any_call', 'effect_audit', 'marker', 'counter',
    'foreign_op', 'dispatch_marker'])
def test_worker_proof_refuses_damaged_history_current_authority_or_any_retained_effect(monkeypatch, damage):
    s = scene(monkeypatch); retry(s); claimed(s)
    if damage == 'original_audit': s.events[:] = [event for event in s.events if event.action != subject.ACTION]
    elif damage == 'original_nonce': s.task.data['graph_retry_requests'] = {}
    elif damage == 'nonce_body': s.task.data['graph_retry_requests'][str(s.request.request_id)]['body_hash'] = '0' * 64
    elif damage == 'common_audit': s.events[:] = [event for event in s.events if event.action != 'workflow.graph.recovery_requested']
    elif damage == 'extra_continuation_audit':
        s.events.append(NS(action=subject.CONTINUED, object_id=s.job.id, actor_id=s.p.user_id, data={}))
    elif damage in {'lease', 'actor', 'live', 'goal'}: s.faults.add(damage)
    elif damage in {'any_call', 'effect_audit'}: s.found[damage] = ident(690)
    elif damage == 'marker': s.job.data[subject.KEY]['body_hash'] = '0' * 64
    elif damage == 'foreign_op': s.cap.data['execution']['operation'] = 'resense_from_context'
    elif damage == 'dispatch_marker': s.task.data[api.runtime.TRANSPORT][s.request.activation_id]['dispatch_committed'] = False
    else: s.job.attempts = 4
    assert subject.can_start_after_pre_dispatch_recovery(s.db, s.p, s.task, s.job) is False


def test_real_kernel_fenced_check_rejects_only_a_forged_ownership_dictionary(monkeypatch):
    s = scene(monkeypatch); retry(s); claimed(s)
    monkeypatch.setattr(leases, 'fenced', s.original_fenced)
    assert subject.can_start_after_pre_dispatch_recovery(s.db, s.p, s.task, s.job) is False


def test_removed_continuation_cannot_discard_its_independent_audit_and_become_initial_again(monkeypatch):
    s = scene(monkeypatch); retry(s); blocked_before_sdk(s)
    s.request = s.request.model_copy(update={'request_id': ident(640)}); retry(s)
    del s.job.data[subject.CONTINUATIONS]
    claimed(s, attempts=3)
    assert subject.can_start_after_pre_dispatch_recovery(s.db, s.p, s.task, s.job) is False


@pytest.mark.parametrize('damaged', [False, True])
def test_actual_recover_calls_and_graph_guard_reach_first_sdk_boundary_only_with_complete_worker_proof(monkeypatch, damaged):
    from generic_agents import gateway, job_recovery, workspace_api
    from generic_agents import layered_approval_store_v175 as approvals
    from generic_agents import workflow_job_dependencies_v264 as dependencies
    from generic_agents import workflow_provenance_wait_v197 as provenance
    from generic_agents import threshold_policy_execution_v153 as threshold
    s = scene(monkeypatch); retry(s); claimed(s)
    s.job.data['recovery_only'] = False  # claim initially writes False at attempts 2.
    # Unrelated threshold ownership IO is a labelled boundary; the real
    # recover_calls graph branch and exact SQL remain active below.
    monkeypatch.setattr(threshold, 'recover', lambda *args: False)
    assert job_recovery.recover_calls(s.db, s.p, s.job) == 0
    assert s.job.data['recovery_only'] is True
    if damaged: s.events[:] = [event for event in s.events if event.action != subject.ACTION]
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *args: (s.task, s.release, s.business))
    monkeypatch.setattr(approvals, 'guard', lambda *args, **kwargs: None)
    monkeypatch.setattr(dependencies, 'require_ready', lambda *args: None)
    monkeypatch.setattr(provenance, 'preflight', lambda *args: False)  # actual producer proof has its own suite
    monkeypatch.setattr(api.runtime, 'published', lambda db, p, identity, *args:
        s.cap if identity == s.cap.id else NS(data={'dependencies': [s.cap.id]}))
    monkeypatch.setattr(goals, 'reserve', lambda *args: s.calls.append('reserve-original-key'))
    monkeypatch.setattr(api.runtime, 'modality_inputs', lambda *args: None)
    def save(db, p, action, object_id, data):
        s.events.append(NS(action=action, object_id=object_id, actor_id=p.user_id, data=copy.deepcopy(data)))
    monkeypatch.setattr(api.runtime, 'audit', save)
    class FirstSDKBoundary(Exception): pass
    def invoke(db, p, capability_id, role, inputs, key, *, approval):
        assert (db, p, capability_id, role, inputs, key) == (
            s.db, s.p, s.cap.id, 'business', s.job.data['inputs'], s.job.idempotency_key)
        assert approval['job_id'] == s.job.id and approval['input_hash'] == s.job.data['input_hash']
        s.calls.append('first-local-sdk')
        raise FirstSDKBoundary()
    monkeypatch.setattr(gateway, 'invoke', invoke)
    if damaged:
        assert api.runtime.process(s.db, s.p, s.job) == 'blocked'
        assert 'first-local-sdk' not in s.calls and 'reserve-original-key' not in s.calls
    else:
        with pytest.raises(FirstSDKBoundary): api.runtime.process(s.db, s.p, s.job)
        assert s.calls[-2:] == ['reserve-original-key', 'first-local-sdk']
    assert s.job.attempts == 2 and s.job.idempotency_key.endswith(':1')
