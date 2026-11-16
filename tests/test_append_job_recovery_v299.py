"""Real recover_calls loop for a typed append approval, with explicit SQL IO.

The actual query predicate, running-to-unknown transition and recovery audit
run. SQL/authority lookups are spies: this is not real PG durability, a worker
lease, append reconciliation, a Goal head or permission to retry an SDK call.
"""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest

from generic_agents import job_recovery as recovery
from generic_agents import threshold_policy_execution_v153 as policy
from generic_agents.contracts import digest
from generic_agents.models import Capability, Invocation, Job
from tests.test_goal_evaluation_append_command_v297 import predicate


AT = datetime(2026, 11, 2, 17, tzinfo=timezone.utc)


def uid(number): return str(UUID(int=number))


def scene(monkeypatch, statuses=('running',), *, side_effect_source='invocation'):
    p = NS(user_id=uid(1), workspace_id=uid(2), role='operator')
    key = 'goal-evaluation-append:' + uid(3) + ':' + uid(4)
    approval = {'schema': 'goal-evaluation-append-approval-v297',
        'command_ref': {'id': uid(5), 'record_hash': 'a' * 64}, 'workspace_id': p.workspace_id,
        'task_id': uid(3), 'actor_id': p.user_id, 'goal_version': 1, 'goal_hash': 'b' * 64,
        'scope_hash': 'c' * 64, 'source_binding_hash': 'd' * 64,
        'capability_id': uid(6), 'capability_hash': 'e' * 64, 'input_hash': 'f' * 64}
    job = Job(id=uid(7), workspace_id=p.workspace_id, kind='append_goal_evaluation_v297',
        status='running', attempts=2, lease_token=uid(8),
        data={'schema': 'goal-evaluation-append-job-v297', 'task_id': uid(3), 'actor_id': p.user_id,
            'invocation_key': key, 'command_ref': copy.deepcopy(approval['command_ref']),
            'approval': copy.deepcopy(approval), 'lease_recoveries': 1})
    cap = NS(id=uid(6), workspace_id=p.workspace_id, data={'execution': {
        'adapter': 'workflow_service', 'service_actor': 'reader_service',
        'operation': 'create_goal_evaluation_packet_v288', 'side_effect': True}})
    calls = []
    for index, status in enumerate(statuses):
        data = {'job_id': job.id, 'lease_token': uid(9), 'approval': copy.deepcopy(approval),
            'input_hash': 'f' * 64, 'capability_hash': 'e' * 64, 'role': 'data',
            'inputs': {'local_plan': {'saved_original': True}},
            'workflow_goal_evaluation_append_v299': {'saved_query_and_journal': 'original-only'},
            'output': {'evaluation_ref': {'id': uid(10), 'record_hash': 'a' * 64}}}
        if side_effect_source == 'invocation': data['side_effect'] = True
        if side_effect_source in {'invocation', 'snapshot'}:
            data['capability_snapshot'] = copy.deepcopy(cap.data)
        calls.append(Invocation(id=uid(20 + index), workspace_id=p.workspace_id,
            capability_id=cap.id, idempotency_key=key + (':' + str(index) if index else ''),
            status=status, version=5, created_at=AT, data=data))
    state = NS(p=p, job=job, approval=approval, cap=cap, calls=calls, audits=[], lookups=[], queries=[])
    class DB:
        def scalars(self, statement):
            assert statement.column_descriptions[0]['entity'] is Invocation
            assert statement._for_update_arg is not None
            params = statement.compile().params
            assert p.workspace_id in params.values() and job.id in params.values()
            state.queries.append(statement)
            selected = [call for call in state.calls if predicate(statement.whereclause, call)]
            return NS(all=lambda: sorted(selected, key=lambda row: row.created_at, reverse=True))
        def add(self, row): pytest.fail('Recovery created a Plan, SDK or other replacement row')
        def commit(self): pytest.fail('The original worker owns the recovery transaction')
        def flush(self): pytest.fail('Recovery must not invent an independent persistence boundary')
    state.db = DB()
    def unrelated_policy(db, actor, actual):
        assert db is state.db and actor is p and actual is job
        assert actual.kind == 'append_goal_evaluation_v297'
        return False
    monkeypatch.setattr(policy, 'recover', unrelated_policy)
    def scoped(db, model, identifier, actor, *args, **kwargs):
        assert db is state.db and actor is p
        # A typed append approval deliberately contains no legacy plan_id.
        # Any Plan/Task recovery lookup is the original production regression.
        assert model is Capability and identifier == cap.id
        state.lookups.append((model, identifier))
        return cap
    monkeypatch.setattr(recovery, 'scoped', scoped)
    def audit(db, actor, action, object_id, value):
        assert db is state.db and actor is p
        state.audits.append((action, object_id, copy.deepcopy(value)))
    monkeypatch.setattr(recovery, 'audit', audit)
    monkeypatch.setattr(recovery, 'now', lambda: AT)
    return state


@pytest.mark.parametrize('side_effect_source', ['invocation', 'snapshot', 'published_manifest'])
def test_real_loop_preserves_original_sdk_and_audits_running_append_as_unknown(monkeypatch, side_effect_source):
    s = scene(monkeypatch, side_effect_source=side_effect_source)
    call = s.calls[0]; before = copy.deepcopy(call.data); original_job = copy.deepcopy(s.job.data)
    identity = (call.id, call.capability_id, call.idempotency_key, call.created_at)
    assert 'plan_id' not in s.approval and s.job.kind != 'execute_full_semantic_graph_node'
    assert recovery.recover_calls(s.db, s.p, s.job) == 1
    assert call.status == 'unknown' and call.version == 6
    assert (call.id, call.capability_id, call.idempotency_key, call.created_at) == identity
    assert all(call.data[key] == value for key, value in before.items())
    assert call.data['recovery'] == {'recovered_at': AT.isoformat(), 'previous_lease_token': before['lease_token'],
        'recovering_job_id': s.job.id, 'reason': 'expired_worker_lease'}
    assert s.audits == [('worker.invocation_recovered', call.id, {'status': 'unknown', 'job_id': s.job.id})]
    assert s.lookups == [(Capability, s.cap.id)] and s.job.data == original_job
    assert 'recovery_only' not in s.job.data
    assert not {'activation_id', 'graph_hash', 'cancel_epoch', 'scope'} & set(s.job.data)
    assert len(s.calls) == 1 and len(s.queries) == 1
    # A second inspection adopts no result, creates no duplicate audit and
    # never rewrites the first independent recovery event or raw journal.
    unknown = copy.deepcopy(call.data)
    assert recovery.recover_calls(s.db, s.p, s.job) == 1
    assert call.data == unknown and call.version == 6 and len(s.audits) == 1


@pytest.mark.parametrize('status', ['unknown', 'failed', 'blocked_evidence', 'blocked_model', 'succeeded', 'not_executed'])
def test_original_terminal_or_unknown_append_remains_unchanged_without_plan_recovery(monkeypatch, status):
    s = scene(monkeypatch, statuses=(status,)); call = s.calls[0]
    before = (call.status, call.version, digest(call.data)); original_job = copy.deepcopy(s.job.data)
    assert recovery.recover_calls(s.db, s.p, s.job) == 1
    assert (call.status, call.version, digest(call.data)) == before
    assert s.job.data == original_job and not s.audits and not s.lookups
    assert len(s.calls) == 1 and 'plan_id' not in call.data['approval']


def test_actual_workspace_and_original_job_sql_predicate_excludes_unrelated_sdk_rows(monkeypatch):
    s = scene(monkeypatch, statuses=('running', 'succeeded'))
    owned = list(s.calls)
    other_job = copy.deepcopy(s.calls[0]); other_job.id = uid(70); other_job.data = {
        **other_job.data, 'job_id': uid(71)}
    other_workspace = copy.deepcopy(s.calls[0]); other_workspace.id = uid(72); other_workspace.workspace_id = uid(73)
    s.calls.extend([other_job, other_workspace])
    before = [(call.status, call.version, digest(call.data)) for call in (owned[1], other_job, other_workspace)]
    assert recovery.recover_calls(s.db, s.p, s.job) == 2
    assert owned[0].status == 'unknown'
    assert [(call.status, call.version, digest(call.data)) for call in (owned[1], other_job, other_workspace)] == before
    assert len(s.audits) == 1 and s.audits[0][1] == owned[0].id and len(s.calls) == 4


def test_no_sdk_is_absence_only_and_does_not_manufacture_graph_or_plan_authority(monkeypatch):
    s = scene(monkeypatch, statuses=()); before = copy.deepcopy(s.job.data)
    assert recovery.recover_calls(s.db, s.p, s.job) == 0
    assert s.job.data == before and not s.audits and not s.lookups and not s.calls
    assert s.job.kind == 'append_goal_evaluation_v297' and 'recovery_only' not in s.job.data
