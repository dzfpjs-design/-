"""Real retry route and native graph; explicit persistence/IAM IO boundaries.

No PG, actual member, dispatch, device or successful service is represented as
verified. The route, DTO, nonce digest and kernel validation are real. The fake
DB only returns the single scoped original Invocation and records writes.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException

from generic_agents import workflow_graph_api as api
from generic_agents import workflow_service_v195 as service
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import relations, workflow_graph_goal_guard_v133 as goals
from generic_agents.contracts import digest
from generic_agents.models import Task, Job, Invocation, Audit
from tests.test_full_semantic_graph_v1 import base, guards, control, edge, object_schema
from tests.test_report_business_flow_wire_v184 import no_external


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def identifier(name):
    return str(uuid5(NAMESPACE_URL, 'public-service-retry-v195:' + name))


class IO:
    """Only row selection and persistence; never a SQL/transaction proof."""
    def __init__(self, call, workspace_id, key):
        self.call, self.workspace_id, self.key = call, workspace_id, key
        self.added, self.selects = [], []
        self.commits = self.flushes = 0

    def scalar(self, statement):
        assert statement.column_descriptions[0]['entity'] is Invocation
        assert statement._for_update_arg is not None
        parameters = list(statement.compile().params.values())
        assert self.workspace_id in parameters and self.key in parameters
        self.selects.append((self.workspace_id, self.key))
        return self.call

    def scalars(self, statement):
        # These V195 scenarios deliberately describe pre-V264 legacy Tasks.
        # Expose their genuinely empty in-memory control-audit inventory to the
        # newer reader; this fixture still proves neither SQL nor transactions.
        from generic_agents.workflow_job_dependencies_v264 import CONTROL_AUDIT
        assert statement.column_descriptions[0]['entity'] is Audit
        assert statement._for_update_arg is None and self.key.startswith('graph:')
        parameters = list(statement.compile().params.values())
        task_id = self.key.split(':', 2)[1]
        assert self.workspace_id in parameters and task_id in parameters and CONTROL_AUDIT in parameters
        assert not any(isinstance(row, Audit) and row.action == CONTROL_AUDIT for row in self.added)
        return []

    def add(self, row): self.added.append(row)

    def flush(self):
        self.flushes += 1
        for index, row in enumerate(self.added):
            if row.id is None: row.id = identifier('new-row-' + str(index))

    def commit(self): self.commits += 1


def scene(monkeypatch, *, adapter='workflow_service', status='waiting_receipt', state=True):
    p = NS(user_id=identifier('actor'), workspace_id=identifier('workspace'), role='operator')
    inputs = {'recipient_ids': [identifier('recipient')], 'message': '核对原任务，不重新发送。'}
    node = {'id': 'notice', 'kind': 'ability', 'source_step_id': 'notice_source',
        'guards': guards(), 'actor_id': p.user_id, 'execution': {
            'capability_id': identifier('capability'), 'capability_hash': 'a' * 64,
            'agent_role': 'business', 'input_bindings': {}, 'arguments': copy.deepcopy(inputs),
            'input_schema': service.input_schema('in_app_notice'),
            'output_schema': service.output_schema('in_app_notice'), 'adapter': adapter,
            'release_version': '1.0.0', 'max_attempts': 3}}
    graph = kernel.validate_graph(base([node, control('end', 'end')],
        [edge('notice', 'end')], 'notice', ['notice']))
    runtime_state = kernel.new_run(graph, {}, 10)
    activation = next(item for item in runtime_state['activations'].values() if item['node_id'] == node['id'])
    task = Task(id=identifier('task'), workspace_id=p.workspace_id, owner_id=p.user_id,
        package_id=identifier('package'), title='公开原服务恢复', status='workflow_waiting', version=4,
        created_at=AT, data={'graph_runtime': runtime_state})
    key = 'graph:' + task.id + ':' + activation['id'] + ':1'
    job = Job(id=identifier('job'), workspace_id=p.workspace_id, kind=api.runtime.KIND,
        idempotency_key=key, status='failed', version=2, attempts=2, created_at=AT,
        available_at=AT, lease_token=identifier('expired-current-lease'),
        lease_until=AT - timedelta(seconds=1), data={'actor_id': p.user_id, 'task_id': task.id,
            'activation_id': activation['id'], 'graph_hash': runtime_state['graph_hash'],
            'cancel_epoch': runtime_state['cancel_epoch'], 'activation_version': activation['version'],
            'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs), 'invocation_key': key,
            'attempt': 1, 'recovery_only': False})
    original_owner = {'job_id': job.id, 'lease_token': identifier('original-dispatch-lease')}
    transport = {'job_id': job.id, 'invocation_key': key, 'phase': 'blocked', 'attempt': 1,
        'input_hash': digest(inputs), 'cancel_epoch': runtime_state['cancel_epoch'],
        'dispatch_committed': True, 'dispatch_ownership': copy.deepcopy(original_owner),
        'dispatch_authorization': {**original_owner, 'actor_id': p.user_id,
            'epoch': runtime_state['cancel_epoch'], 'input_hash': digest(inputs),
            'capability_hash': node['execution']['capability_hash']}, 'reason': '原核对暂时受阻'}
    task.data = {**task.data, api.runtime.TRANSPORT: {activation['id']: transport}}
    call = Invocation(id=identifier('invocation'), workspace_id=p.workspace_id,
        capability_id=node['execution']['capability_id'], idempotency_key=key, status=status,
        version=1, created_at=AT, data={'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs),
            'role': 'business', 'capability_hash': node['execution']['capability_hash'],
            **original_owner, 'side_effect': adapter == 'workflow_service'})
    call.data = {**call.data, 'approval': service.make_approval(task, job, node, inputs)}
    db = IO(call, p.workspace_id, key)
    if state:
        # Build the actual closed state/audit rather than inventing a marker.
        service._record(db, p, {'task': task, 'job': job,
            'capability': NS(id=call.capability_id, content_hash=node['execution']['capability_hash']),
            'binding': {'binding_hash': 'b' * 64, 'simulation_only': False},
            'delegation': {'operation': 'in_app_notice'}}, call, inputs, key, call.data['approval'],
            {'notice_id': call.id, 'notice_hash': 'c' * 64}, AT.isoformat())
    db.added.clear(); db.flushes = 0
    checks = []
    release, business = NS(id=identifier('release')), {'name': '原正式任务的公开合成业务'}
    def context_boundary(current_db, current_p, identity, lock=False):
        assert current_db is db and current_p is p and identity == task.id
        kernel.check_run(graph, task.data['graph_runtime'])
        checks.append(('source-and-native-context', lock))
        return task, release, business, graph
    monkeypatch.setattr(api, 'context', context_boundary)
    def job_boundary(current_db, model, identity, current_p, lock=False):
        assert current_db is db and model is Job and identity == job.id and current_p is p and lock is True
        assert job.workspace_id == p.workspace_id
        checks.append(('original-job-lock', job.id))
        return job
    monkeypatch.setattr(api, 'scoped', job_boundary)
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *args: checks.append(('current-IAM', p.user_id)))
    monkeypatch.setattr(api.runtime, 'live', lambda *args: checks.append(('current-source-approval', task.id)))
    def view_boundary(current_task, current_release, current_business, current_p, *, db):
        assert current_task is task and current_release is release and current_business is business and current_p is p
        kernel.check_run(graph, task.data['graph_runtime'])
        return {'id': task.id, 'graph_jobs': copy.deepcopy(task.data[api.runtime.TRANSPORT]),
            'graph_retry_requests': copy.deepcopy(task.data.get('graph_retry_requests', {}))}
    monkeypatch.setattr(api.runtime, 'public_view', view_boundary)
    def new_attempt_boundary(*args): checks.append(('new-read-attempt', task.id))
    monkeypatch.setattr(goals, 'new_attempt', new_attempt_boundary)
    body = api.Retry(request_id=identifier('retry-request'), activation_id=activation['id'],
        version=activation['version'], cancel_epoch=runtime_state['cancel_epoch'], expected_attempt=1,
        confirm_repeat_read=True)
    return NS(db=db, p=p, task=task, job=job, call=call, node=node, graph=graph,
        activation=activation, inputs=inputs, key=key, transport=transport, body=body, checks=checks)


@pytest.mark.parametrize('status', ['succeeded', 'waiting_receipt', 'unknown'])
def test_service_recovery_only_requeues_original_job_and_keeps_all_dispatch_provenance(monkeypatch, status):
    s = scene(monkeypatch, status=status)
    s.body = s.body.model_copy(update={'confirm_repeat_read': False})
    before_call, before_job = copy.deepcopy(s.call.data), copy.deepcopy(s.job.data)
    before_transport, before_graph = copy.deepcopy(s.transport), copy.deepcopy(s.task.data['graph_runtime'])
    result = api.retry(s.task.id, s.body, s.p, s.db)
    current = s.task.data[api.runtime.TRANSPORT][s.activation['id']]
    assert current == {**before_transport, 'phase': 'queued', 'reason': None}
    assert s.job.id == before_transport['job_id'] and s.job.idempotency_key == s.key
    assert s.job.status == 'queued' and s.job.lease_token is None and s.job.lease_until is None
    assert s.job.data == {**before_job, 'recovery_only': True}
    assert s.call.status == status and s.call.data == before_call
    assert s.task.data['graph_runtime'] == before_graph
    assert not any(isinstance(row, (Job, Invocation)) for row in s.db.added)
    assert not any(row[0] == 'new-read-attempt' for row in s.checks)
    saved = result['graph_retry_requests'][str(s.body.request_id)]
    assert saved == {'actor_id': s.p.user_id,
        'body_hash': digest({'actor_id': s.p.user_id, 'body': s.body.model_dump(mode='json')}),
        'activation_id': s.activation['id'], 'job_id': s.job.id}
    assert s.checks[:4] == [('source-and-native-context', False), ('original-job-lock', s.job.id),
        ('source-and-native-context', True), ('current-IAM', s.p.user_id)]
    assert ('current-source-approval', s.task.id) in s.checks
    assert s.db.commits == 1


@pytest.mark.parametrize('fault', ['missing_call', 'running', 'failed', 'blocked_evidence', 'not_executed',
    'unknown_without_state', 'unknown_wrong_marker', 'unknown_incomplete_state', 'unknown_extra_state'])
def test_service_can_never_enter_old_read_only_new_attempt_even_when_user_confirms_repeat(monkeypatch, fault):
    s = scene(monkeypatch)
    if fault == 'missing_call': s.db.call = None
    elif fault.startswith('unknown_'):
        s.call.status = 'unknown'
        value = copy.deepcopy(s.call.data['service_state'])
        if fault == 'unknown_without_state': del s.call.data['service_state']
        elif fault == 'unknown_wrong_marker': value['schema'] = 'other-family'
        elif fault == 'unknown_incomplete_state': del value['state_hash']
        else: value['extra'] = True
        if fault != 'unknown_without_state': s.call.data = {**s.call.data, 'service_state': value}
    else: s.call.status = fault
    old = copy.deepcopy((s.task.data, s.job.data, s.call.data))
    with pytest.raises(HTTPException) as rejected: api.retry(s.task.id, s.body, s.p, s.db)
    assert rejected.value.status_code == 409
    assert (s.task.data, s.job.data, s.call.data) == old
    assert s.job.status == 'failed' and s.db.commits == 0 and not s.db.added
    assert not any(row[0] == 'new-read-attempt' for row in s.checks)


def test_service_same_nonce_replays_original_result_without_requeue_and_changed_body_conflicts(monkeypatch):
    s = scene(monkeypatch)
    first = api.retry(s.task.id, s.body, s.p, s.db)
    version, commits, added = s.job.version, s.db.commits, len(s.db.added)
    assert api.retry(s.task.id, s.body, s.p, s.db) == first
    assert (s.job.version, s.db.commits, len(s.db.added)) == (version, commits, added)
    changed = s.body.model_copy(update={'confirm_repeat_read': False})
    with pytest.raises(HTTPException) as rejected: api.retry(s.task.id, changed, s.p, s.db)
    assert rejected.value.status_code == 409 and s.db.commits == commits


def test_current_authority_is_still_required_before_service_job_is_requeued(monkeypatch):
    s = scene(monkeypatch)
    def revoked(*args): raise HTTPException(403, '原成员或任务授权已撤销')
    monkeypatch.setattr(api.runtime, 'live', revoked)
    with pytest.raises(HTTPException) as rejected: api.retry(s.task.id, s.body, s.p, s.db)
    assert rejected.value.status_code == 403
    assert s.job.status == 'failed' and s.db.commits == 0 and not s.db.added


def test_service_marker_is_only_a_recovery_candidate_not_a_replacement_audit_or_execution(monkeypatch):
    s = scene(monkeypatch, status='unknown')
    # Even if independent state provenance is absent, the route only schedules
    # the same worker. Actual reconcile must reject its missing audit later.
    assert not s.db.added
    api.retry(s.task.id, s.body, s.p, s.db)
    assert s.call.status == 'unknown' and s.job.data['recovery_only'] is True
    assert all(isinstance(row, Audit) and row.action == 'workflow.graph.recovery_requested'
        for row in s.db.added)
    assert len(s.db.added) == 1


def test_old_builtin_failed_read_keeps_its_explicit_new_attempt_and_new_key(monkeypatch):
    s = scene(monkeypatch, adapter='builtin', status='failed', state=False)
    old_call = copy.deepcopy(s.call.data)
    api.retry(s.task.id, s.body, s.p, s.db)
    jobs = [row for row in s.db.added if isinstance(row, Job)]
    assert len(jobs) == 1 and jobs[0].id != s.job.id
    assert jobs[0].data['invocation_key'] == 'graph:' + s.task.id + ':' + s.activation['id'] + ':2'
    assert jobs[0].data['inputs'] == kernel.ability_inputs(s.graph, s.task.data['graph_runtime'], s.activation['id'])
    assert jobs[0].data['input_hash'] == digest(s.inputs)
    assert s.call.data == old_call and ('new-read-attempt', s.task.id) in s.checks


@pytest.mark.parametrize('status', ['running', 'unknown'])
def test_old_builtin_unknown_or_running_still_cannot_request_a_new_attempt(monkeypatch, status):
    s = scene(monkeypatch, adapter='builtin', status=status, state=False)
    with pytest.raises(HTTPException) as rejected: api.retry(s.task.id, s.body, s.p, s.db)
    assert rejected.value.status_code == 409 and s.db.commits == 0 and not s.db.added
