"""Actual V290 chain/negative-query/requeue functions over explicit IO spies.

Rows, Audit inventories, locks, lease and authority are synthetic boundaries.
These tests prove no real PostgreSQL, published release, IAM, provider, SDK,
acquisition or end-to-end recovery. Root separately verifies the original Job.
"""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import workflow_resense_pre_dispatch_recovery_v290 as subject
from generic_agents.contracts import digest
from generic_agents.models import Acquisition, Audit, Invocation, Job, Observation, ObservationPlan, Plan


def ident(value): return str(UUID(int=value))


def scene(monkeypatch, *, count=0):
    p = NS(user_id=ident(1), workspace_id=ident(2), role='operator')
    task = NS(id=ident(3), workspace_id=p.workspace_id, data={})
    key = 'graph:' + task.id + ':resense_activation:1'
    job = NS(id=ident(4), workspace_id=p.workspace_id, idempotency_key=key,
        status='failed', attempts=1, version=1, lease_token=ident(5), lease_until=None,
        data={'input_hash': 'a' * 64, 'actor_id': p.user_id, 'error': '原失败保留',
            'finished_at': '2026-11-01T12:00:00+00:00'})
    transport = {'job_id': job.id, 'invocation_key': key, 'input_hash': 'a' * 64,
        'attempt': 1, 'cancel_epoch': 0, 'phase': 'blocked', 'reason': '原失败保留',
        'dispatch_committed': True, 'dispatch_input_hash': 'a' * 64,
        'dispatch_ownership': {'job_id': job.id, 'lease_token': job.lease_token}}
    base = {'schema': subject.SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'job_id': job.id, 'actor_id': p.user_id, 'activation_id': 'resense_activation',
        'invocation_key': key, 'attempt': 1, 'input_hash': 'a' * 64,
        'graph_hash': 'b' * 64, 'cancel_epoch': 0, 'activation_version': 0,
        'capability_id': ident(6), 'capability_hash': 'c' * 64, 'release_id': ident(7),
        'source_binding_hash': 'd' * 64, 'context_hash': 'e' * 64,
        'reader_job_id': ident(8), 'reader_invocation_id': ident(9),
        'policy_id': ident(10), 'gap_id': ident(11), 'initial_intent_audit_id': ident(12),
        'request_id': ident(13), 'body_hash': 'f' * 64, 'job_data_hash': digest(job.data),
        'failure': subject._failure(job, transport), 'created_at': '2026-11-01T12:01:00+00:00'}
    certificate = subject._seal(base)
    events = {subject.ACTION: [NS(id=ident(14), actor_id=p.user_id, data=copy.deepcopy(certificate))],
        subject.CONTINUED: [], subject.INTENT_ACTION: []}
    dispatches = [NS(id=ident(12), actor_id=p.user_id,
        data={'activation_id': base['activation_id'], 'job_id': job.id})]
    recovered = []; histories = []; intents = []
    task.data['graph_retry_requests'] = {}
    def retain(value):
        task.data['graph_retry_requests'][value['request_id']] = {'actor_id': p.user_id,
            'body_hash': value['body_hash'], 'activation_id': base['activation_id'], 'job_id': job.id}
        recovered.append(NS(actor_id=p.user_id,
            data={'activation_id': base['activation_id'], 'request_id': value['request_id']}))
    retain(certificate)
    previous = certificate['record_hash']
    for index in range(1, count + 1):
        next_transport = copy.deepcopy(transport)
        next_transport.update(phase='running', reason=None,
            dispatch_ownership={'job_id': job.id, 'lease_token': ident(20 + index)})
        intent = subject._seal({'schema': subject.INTENT, 'version': index,
            'original_record_hash': certificate['record_hash'], 'previous_hash': previous,
            **{key: certificate[key] for key in ('job_id', 'task_id', 'workspace_id', 'actor_id', 'input_hash')},
            'dispatch_audit_id': ident(30 + index), 'lease_token': ident(20 + index),
            'job_attempts': min(index + 1, 3), 'recovery_attempt': index, 'transport': next_transport,
            'created_at': '2026-11-01T12:02:00+00:00'})
        intents.append(intent); previous = intent['record_hash']
        events[subject.INTENT_ACTION].append(NS(id=ident(40 + index), actor_id=p.user_id, data=copy.deepcopy(intent)))
        dispatches.append(NS(id=intent['dispatch_audit_id'], actor_id=p.user_id,
            data={'activation_id': base['activation_id'], 'job_id': job.id}))
        if index <= subject.MAX_CONTINUATIONS:
            value = subject._seal({'schema': subject.CONTINUATION,
                **{key: certificate[key] for key in subject.IDENTITY}, 'version': index,
                'original_record_hash': certificate['record_hash'],
                'previous_hash': (histories[-1] if histories else certificate)['record_hash'],
                'intent_head_hash': intent['record_hash'], 'intent_count': index, 'failure_proof': None,
                'request_id': ident(50 + index),
                'body_hash': str(index) * 64, 'job_data_hash': '0' * 64,
                'failure': {**copy.deepcopy(certificate['failure']), 'job_attempts': min(index + 1, 3)},
                'created_at': '2026-11-01T12:03:00+00:00'})
            histories.append(value)
            events[subject.CONTINUED].append(NS(id=ident(60 + index), actor_id=p.user_id, data=copy.deepcopy(value)))
            retain(value)
    job.data[subject.KEY] = certificate
    if histories: job.data[subject.CONTINUATIONS] = histories
    if intents: job.data[subject.INTENTS] = intents
    current = copy.deepcopy(intents[-1]['transport'] if intents else transport)
    current.update(phase='blocked', reason='本次失败保留')
    class DB:
        def scalars(self, statement):
            compiled = statement.compile(compile_kwargs={'literal_binds': True})
            sql = str(compiled)
            assert 'workflow.graph.recovery_requested' in sql and base['activation_id'] in sql
            return list(recovered)
    db = DB()
    monkeypatch.setattr(subject, '_audits', lambda actual_db, actual_p, action, object_id, limit:
        copy.deepcopy(events[action]) if actual_db is db and actual_p is p and object_id == job.id
        else pytest.fail('unexpected Audit scope'))
    monkeypatch.setattr(subject, '_matching_dispatches', lambda *args: copy.deepcopy(dispatches))
    return NS(p=p, task=task, job=job, certificate=certificate, transport=transport,
        current=current, events=events, dispatches=dispatches, recovered=recovered, db=db,
        intents=intents, histories=histories)


@pytest.mark.parametrize('count', [0, 1, 2, 3])
def test_actual_ledger_requires_original_and_every_subsequent_intent_audit(monkeypatch, count):
    s = scene(monkeypatch, count=count)
    before = copy.deepcopy((s.job.data, s.current))
    certificate, histories, intents, head = subject._ledger(s.db, s.p, s.task, s.job, s.current)
    assert certificate == s.certificate and len(intents) == count
    assert len(histories) == min(count, 2)
    assert head == (s.intents[-1] if s.intents else s.certificate)['record_hash']
    assert (s.job.data, s.current) == before


@pytest.mark.parametrize('change', [
    lambda s: s.events[subject.ACTION].clear(),
    lambda s: s.dispatches.pop(0),
    lambda s: s.dispatches.append(NS(id=ident(900), actor_id=s.p.user_id, data=s.dispatches[-1].data)),
    lambda s: s.events[subject.INTENT_ACTION].clear(),
    lambda s: s.events[subject.CONTINUED].clear(),
    lambda s: s.task.data['graph_retry_requests'].clear(),
    lambda s: s.recovered.clear(),
    lambda s: s.current.update(dispatch_ownership={'job_id': s.job.id, 'lease_token': ident(901)}),
])
def test_missing_or_additional_independent_custody_cannot_be_rehashed_into_permission(monkeypatch, change):
    s = scene(monkeypatch, count=1); change(s)
    with pytest.raises(HTTPException): subject._ledger(s.db, s.p, s.task, s.job, s.current)


@pytest.mark.parametrize('change', [
    lambda s, value: value.update(previous_hash='0' * 64),
    lambda s, value: value.update(dispatch_audit_id=s.certificate['initial_intent_audit_id']),
    lambda s, value: value.update(lease_token=s.transport['dispatch_ownership']['lease_token']),
    lambda s, value: value['transport'].update(dispatch_authorization={'lease_token': ident(800)}),
    lambda s, value: value['transport'].update(invocation_key='a-different-original-request'),
])
def test_even_resealed_intent_cannot_change_original_chain_or_claim_an_authorization(monkeypatch, change):
    s = scene(monkeypatch, count=1); value = copy.deepcopy(s.intents[0]); change(s, value)
    value = subject._seal({key: item for key, item in value.items() if key != 'record_hash'})
    with pytest.raises(HTTPException): subject.checked_intent_chain(s.certificate, [value])


def test_requeue_preserves_original_job_failure_and_attempt_without_creating_an_sdk(monkeypatch):
    s = scene(monkeypatch)
    # A real prepare would freeze this exact current data hash before requeue.
    record = copy.deepcopy(s.certificate)
    original_data = {key: value for key, value in s.job.data.items() if key != subject.KEY}
    s.job.data = copy.deepcopy(original_data)
    record['job_data_hash'] = digest(s.job.data)
    record = subject._seal({key: value for key, value in record.items() if key != 'record_hash'})
    captured = []
    monkeypatch.setattr(subject, 'audit', lambda db, p, action, identity, data:
        captured.append((action, identity, copy.deepcopy(data))))
    subject.requeue(s.db, s.p, s.job, record)
    assert s.job.status == 'queued' and s.job.attempts == 1 and s.job.version == 2
    assert s.job.lease_token is s.job.lease_until is None
    assert s.job.data['error'] == original_data['error'] and s.job.data['finished_at'] == original_data['finished_at']
    assert s.job.data[subject.KEY]['failure']['transport'] == s.transport
    assert s.job.idempotency_key == s.transport['invocation_key']
    assert captured == [(subject.ACTION, s.job.id, record)]


@pytest.mark.parametrize('fault', [None, 'sdk', 'replacement_job', 'plan', 'acquisition',
    'observation_plan', 'observation', 'authorization', 'receiver'])
def test_actual_negative_inventory_rejects_each_effect_and_does_not_filter_sdk_status(monkeypatch, fault):
    from generic_agents import workflow_local_transaction_capacity_v201 as capacity
    # The separate reverse-footprint test exercises the actual complete scan.
    monkeypatch.setattr(capacity, '_resense_footprint_empty', lambda *args: True)
    p = NS(workspace_id=ident(2)); task = NS(id=ident(3)); job = NS(id=ident(4), idempotency_key='original-key')
    activation = {'id': 'resense_activation'}; context = {'gap': {'gap_id': ident(11)}}
    mapping = {Invocation.__tablename__: 'sdk', Job.__tablename__: 'replacement_job',
        Plan.__tablename__: 'plan', Acquisition.__tablename__: 'acquisition',
        ObservationPlan.__tablename__: 'observation_plan', Observation.__tablename__: 'observation'}
    calls = []
    class AbsenceDB:
        def scalar(self, statement):
            table = next(iter(statement.selected_columns)).table.name
            compiled = str(statement.compile(compile_kwargs={'literal_binds': True}))
            assert p.workspace_id in compiled and 'LIMIT 1' in compiled
            if table == Audit.__tablename__:
                if 'workflow.graph.dispatch_authorized' in compiled: kind = 'authorization'
                else:
                    assert 'workflow_service.context_source_v196' in compiled
                    kind = 'receiver'
            else: kind = mapping[table]
            if kind == 'sdk':
                assert 'original-key' in compiled and 'approval' in compiled and 'activation_id' in compiled
                assert 'invocations.status' not in compiled  # Every status, including unknown, blocks.
            calls.append(kind)
            return ident(999) if kind == fault else None
    if fault:
        with pytest.raises(HTTPException): subject._no_effects(AbsenceDB(), p, task, job, activation, context)
        assert calls[-1] == fault
    else:
        subject._no_effects(AbsenceDB(), p, task, job, activation, context)
        assert calls == ['sdk', 'replacement_job', 'plan', 'acquisition', 'observation_plan',
            'observation', 'authorization', 'receiver']


@pytest.mark.parametrize('fault', [None, 'bound', 'ref', 'parent_failed', 'parent_running', 'parent_pin', 'control'])
def test_failed_api_cold_dependencies_need_full_original_proofs_but_never_a_worker_lease(monkeypatch, fault):
    from generic_agents import workflow_job_dependencies_v264 as dependencies, leases
    p = NS(user_id=ident(1), workspace_id=ident(2))
    task = NS(id=ident(3), workspace_id=p.workspace_id, data={})
    job = NS(id=ident(4), workspace_id=p.workspace_id, kind='execute_full_semantic_graph_node',
        status='failed', lease_token=ident(5), lease_until=None,
        data={'task_id': task.id, 'actor_id': p.user_id, 'inputs': {}, 'invocation_key': 'original', 'activation_id': 'resense'})
    parent = {'job_id': ident(6), 'pin': 'actual-parent-IO-boundary'}
    base = {'schema': dependencies.SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        **dependencies._child_pin(job), 'bound_by': p.user_id, 'control_record_hash': 'a' * 64,
        'compact_enrollment_hash': None, 'parents': [parent]}
    value = {**base, 'record_hash': digest(base)}
    job.data[dependencies.KEY] = value
    task.data[dependencies.REFS] = {job.id: value['record_hash']}
    if fault == 'ref': task.data[dependencies.REFS].clear()
    proof = NS(actor_id=p.user_id, data=copy.deepcopy(value))
    class DB:
        def scalars(self, statement):
            compiled = str(statement.compile(compile_kwargs={'literal_binds': True}))
            assert dependencies.BOUND in compiled and job.id in compiled and p.workspace_id in compiled
            return [] if fault == 'bound' else [proof]
    monkeypatch.setattr(leases, 'ownership', lambda *args: pytest.fail('Cold API must not use a synthetic worker lease'))
    monkeypatch.setattr(dependencies, 'checked_control', lambda *args, **kwargs: None if fault == 'control' else {'original': True})
    monkeypatch.setattr(dependencies, 'graph_parent_ids', lambda *args: [parent['job_id']])
    monkeypatch.setattr(dependencies, 'parent_pin', lambda *args: {} if fault == 'parent_pin' else copy.deepcopy(parent))
    monkeypatch.setattr(dependencies, '_row', lambda *args: NS(status='failed' if fault == 'parent_failed'
        else 'running' if fault == 'parent_running' else 'succeeded'))
    if fault:
        with pytest.raises(HTTPException): subject._cold_dependencies(DB(), p, task, job)
    else:
        subject._cold_dependencies(DB(), p, task, job)
        assert job.status == 'failed' and job.lease_until is None


def test_v290_grantless_context_reader_avoids_gap_lock_and_preserves_the_full_runtime_guard(monkeypatch):
    from generic_agents import workflow_task_context_v196 as reader, native_goals, perception, sensing_selection
    task = NS(id=ident(1), data={'native_goal_context': {'original': True}})
    snapshot = {'original_goal': True}
    query = {'fields': ['temperature'], 'entity_ids': [ident(2)]}
    delegation = {'task_id': task.id, 'goal_snapshot': snapshot}
    run = NS(id=ident(3), status='insufficient', data={'request': query, 'goal_delegation': delegation,
        'data_gap_ids': [ident(4)], 'native_goal_context_hash': digest(task.data['native_goal_context']),
        'native_goal_request_hash': digest(query)})
    request = {'gap_id': ident(4), 'query_id': run.id, 'entity_id': ident(2), 'request': copy.deepcopy(query),
        'query_request_hash': digest(query), 'goal_delegation': copy.deepcopy(delegation), 'fields': ['temperature']}
    gap = NS(id=ident(4), query_id=run.id, entity_id=ident(2), status='unresolved',
        data={'request': copy.deepcopy(query), 'business_task_id': task.id})
    policy = NS(id=ident(5), approved_by=ident(6), data={'approved_hash': 'a' * 64,
        'selection_requirement': {'original': True}, 'fields': ['temperature'], 'entity_ids': [ident(2)]})
    pins = [{'source_id': ident(7), 'source_hash': 'b' * 64}]
    value = {'producer': {'node_id': 'context_reader', 'activation_id': 'original-reader'},
        'gap': {'gap_id': gap.id, 'entity_id': gap.entity_id, 'observation_request': request,
            'observation_request_hash': digest(request)},
        'policy': {'policy_id': policy.id, 'approved_by': policy.approved_by,
            'approved_hash': policy.data['approved_hash'], 'source_pins': pins}}
    p = NS(); db = NS(); calls = []
    # SDK/PRODUCED/Goal/Q0 producer custody is the explicit IO boundary here.
    monkeypatch.setattr(reader, '_checked_producer_context', lambda *args, **kwargs: run)
    monkeypatch.setattr(reader.graph_scope, '_frozen', lambda *args: (task, NS(), NS()))
    from generic_agents import workflow_graph
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *args: {'schema': subject.BINDING})
    monkeypatch.setattr(reader, 'scoped', lambda db, cls, identity, principal: gap if cls is reader.DataGap else policy)
    monkeypatch.setattr(native_goals, 'snapshot', lambda *args: copy.deepcopy(snapshot))
    monkeypatch.setattr(reader, '_source_pins', lambda *args: copy.deepcopy(pins))
    monkeypatch.setattr(perception, 'authorized_monitor', lambda *args: calls.append('current-monitor'))
    monkeypatch.setattr(sensing_selection, 'validate_policy', lambda *args: calls.append('current-policy'))
    monkeypatch.setattr(reader.requests, 'guard', lambda *args, **kwargs: pytest.fail('API must not acquire Gap lock'))
    monkeypatch.setattr(reader.requests, 'effective', lambda *args, **kwargs: pytest.fail('API must not grant effective dispatch'))
    before = copy.deepcopy(value)
    assert reader.read_original_context_v290(db, p, value, task=task) == value
    assert value == before and calls == ['current-monitor', 'current-policy']
    with pytest.raises(pytest.fail.Exception, match='API must not acquire Gap lock'):
        reader.checked_context(db, p, value, task=task, dispatch=False)


@pytest.mark.parametrize('fault', [None, 'missing_diagnostic_audit', 'stale_diagnostic', 'wrong_attempt'])
def test_pre_intent_failed_claim_needs_its_actual_independent_failure_audit(monkeypatch, fault):
    from generic_agents import worker_failure_diagnostics_v289 as failures
    s = scene(monkeypatch)
    diagnostic = failures.attempt_record(s.job.id, s.job.workspace_id, 2, failures.capture_failure(ValueError()))
    saved = {**diagnostic, 'audit_id': ident(701)}
    at = datetime(2026, 11, 1, 12, 2, tzinfo=timezone.utc)
    s.events[failures.ACTION] = [NS(id=saved['audit_id'], actor_id=s.p.user_id, data=copy.deepcopy(diagnostic), created_at=at)]
    failure = {**copy.deepcopy(s.certificate['failure']), 'job_attempts': 2, 'diagnostic': saved}
    continuation = subject._seal({'schema': subject.CONTINUATION,
        **{key: s.certificate[key] for key in subject.IDENTITY}, 'version': 1,
        'original_record_hash': s.certificate['record_hash'], 'previous_hash': s.certificate['record_hash'],
        'intent_head_hash': s.certificate['record_hash'], 'intent_count': 0,
        'failure_proof': {'audit_id': saved['audit_id'], 'record_hash': diagnostic['record_hash'], 'created_at': at.isoformat()},
        'request_id': ident(702), 'body_hash': '7' * 64, 'job_data_hash': '8' * 64,
        'failure': failure, 'created_at': '2026-11-01T12:03:00+00:00'})
    s.job.data[subject.CONTINUATIONS] = [continuation]
    s.events[subject.CONTINUED] = [NS(actor_id=s.p.user_id, data=copy.deepcopy(continuation))]
    s.task.data['graph_retry_requests'][continuation['request_id']] = {'actor_id': s.p.user_id,
        'body_hash': continuation['body_hash'], 'activation_id': continuation['activation_id'], 'job_id': s.job.id}
    s.recovered.append(NS(actor_id=s.p.user_id,
        data={'activation_id': continuation['activation_id'], 'request_id': continuation['request_id']}))
    if fault == 'missing_diagnostic_audit': s.events[failures.ACTION].clear()
    if fault == 'stale_diagnostic': s.events[failures.ACTION][0].created_at = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    if fault == 'wrong_attempt': s.events[failures.ACTION][0].data['attempt'] = 1
    if fault:
        with pytest.raises(HTTPException): subject._ledger(s.db, s.p, s.task, s.job, s.current)
    else:
        certificate, history, intents, head = subject._ledger(s.db, s.p, s.task, s.job, s.current)
        assert len(history) == 1 and not intents and head == certificate['record_hash']


def test_first_recorded_intent_can_follow_a_proven_pre_intent_failed_claim(monkeypatch):
    s = scene(monkeypatch, count=1)
    value = copy.deepcopy(s.intents[0]); value.update(recovery_attempt=2, job_attempts=3)
    value = subject._seal({key: item for key, item in value.items() if key != 'record_hash'})
    assert subject.checked_intent_chain(s.certificate, [value]) == value['record_hash']


@pytest.mark.parametrize('witness', ['http_audit', 'exchange', 'event', 'receipt_memory'])
def test_reverse_orphan_footprint_blocks_recovery_without_any_forward_effect_pointers(witness):
    from generic_agents.models import Event, Memory
    from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177
    from generic_agents.http_observation_exchange_v177 import ACTION as changed
    p = NS(workspace_id=ident(2)); task = NS(id=ident(3)); job = NS(id=ident(4), idempotency_key='original-key')
    activation = {'id': 'resense_activation'}
    context = {'gap': {'gap_id': ident(11), 'entity_id': ident(12)},
        'query': {'query_id': ident(13)}, 'policy': {'policy_id': ident(14), 'source_pins': []}}
    reverse = {'state': {'data': {'freeze': {'parent_pins': {'task_id': task.id,
        'gap_id': context['gap']['gap_id'], 'query_id': context['query']['query_id']}}}}}
    inventories = {}
    if witness == 'http_audit': inventories[Audit] = [NS(action=changed, data=reverse)]
    if witness == 'exchange': inventories[HTTPObservationExchangeV177] = [NS(data=reverse)]
    if witness == 'event': inventories[Event] = [NS(kind='observation.data', data=reverse)]
    if witness == 'receipt_memory': inventories[Memory] = [NS(data={'type': 'observation_evidence', **reverse})]
    class DB:
        def scalar(self, statement): return None  # Explicit forward-inventory IO boundary.
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity']
            compiled = str(statement.compile(compile_kwargs={'literal_binds': True}))
            assert p.workspace_id in compiled and 'LIMIT' in compiled
            return inventories.get(model, [])
    with pytest.raises(HTTPException): subject._no_effects(DB(), p, task, job, activation, context)
