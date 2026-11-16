"""Actual append query/cut/SAVED/readback over explicit authority/DB IO spies.

The prior query fixture runs actual local data_agent and cut admission. Here
sealed-command/IAM/SDK/registry/current producer checks are explicit spies;
none of these tests proves actual PG, append publication, SDK or user journey.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import models as m, workspace_api
from generic_agents import goal_evaluation_append_query_v299 as subject
from generic_agents import goal_evaluation_query_v288 as old
from generic_agents import goal_evaluation_query_guard_v288 as guard
from generic_agents import workflow_goal_evaluation_append_v299 as producer
from generic_agents.contracts import Manifest, digest
from generic_agents.goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
from generic_agents.temporal import instant
from tests.test_goal_evaluation_query_v288 import scene as old_scene, same_sql, Rows, uid, CUT


@pytest.fixture
def scene(monkeypatch):
    s = old_scene.__wrapped__(monkeypatch)
    s.task.package_id = uid(5)
    s.task.data.update(instance_id=uid(3), instance_release_id=uid(4), business_id='same_goal_append')
    data = Manifest(kind='tool', namespace='append_query_test_v299', name='original_evaluation_cap',
        version='1.0.0', owner='显式SDK与命令授权IO边界', description='固定本地评价能力',
        allowed_agent_roles=['data'], execution={'adapter': 'workflow_service', 'service_actor': 'reader_service',
            'operation': old.OP, 'side_effect': True}).model_dump(mode='json')
    s.cap = NS(id=uid(70), workspace_id=s.p.workspace_id, data=data, kind='tool',
        content_hash=digest(data), validated_hash=digest(data))
    release = NS(id=uid(4), instance_id=uid(3), package_id=uid(5))
    selected = {'release': release, 'binding': {'graph': {'nodes': []},
        'binding_hash': 'a' * 64, 'preparation_policy': s.policy}}
    key = 'goal-evaluation-append:' + s.task.id + ':' + uid(91)
    inputs = {'local_plan': {'explicit_original_plan_IO_boundary': True}}
    approval = {'schema': 'explicit_command_approval_IO_boundary', 'command_ref': {'id': uid(92), 'record_hash': 'b' * 64}}
    command_data = {'record_hash': 'b' * 64, 'inputs': inputs, 'input_hash': digest(inputs),
        'actor_id': s.p.user_id, 'invocation_key': key, 'original_query_ref': old.query_ref(s.parent),
        'accepted_at': (instant(CUT) - timedelta(seconds=2)).isoformat()}
    command = NS(id=uid(92), workspace_id=s.p.workspace_id, data=command_data)
    s.job.workspace_id = s.p.workspace_id; s.job.status = 'running'; s.job.lease_token = s.token
    s.job.lease_until = s.trusted + timedelta(minutes=1); s.job.kind = 'append_goal_evaluation_v297'
    s.job.idempotency_key = key; s.job.data = {'task_id': s.task.id, 'actor_id': s.p.user_id,
        'invocation_key': key, 'inputs': inputs, 'input_hash': digest(inputs), 'approval': approval}
    s.call = NS(id=s.evaluation_id, workspace_id=s.p.workspace_id, capability_id=s.cap.id,
        idempotency_key=key, status='running', created_at=instant(CUT) - timedelta(seconds=1),
        data={'job_id': s.job.id, 'role': 'data', 'side_effect': True, 'capability_hash': s.cap.content_hash,
            'capability_snapshot': copy.deepcopy(data), 'inputs': inputs, 'input_hash': digest(inputs),
            'lease_token': s.token, 'approval': approval})
    s.current = {'task': s.task, 'job': s.job, 'cap': s.cap, 'command': command, 'record': command_data,
        'selected': selected, 'inputs': inputs, 'key': key, 'approval': approval}
    for model, row in ((m.Job, s.job), (m.Invocation, s.call), (m.Capability, s.cap), (Command, command)):
        s.db.rows[model, row.id] = row
    s.authority = []; s.flushes = []
    def runner(db, principal, job, **kwargs):
        assert db is s.db and principal in (s.p, s.reader) and job.id == s.job.id
        assert kwargs['call'].id == s.call.id and kwargs['cap'].id == s.cap.id
        assert kwargs['inputs'] == inputs and kwargs['key'] == key and kwargs['approval'] == approval
        s.authority.append(kwargs['require_lease']); return s.current
    def validate(*_args): return s.current
    monkeypatch.setattr(producer, 'runner_authority', runner)
    monkeypatch.setattr(producer, 'validate_call_approval', validate)
    original_scalars = s.db.scalars
    def scalars(statement):
        for conditions in ((m.Audit.object_id == getattr(s, 'row_id', uid(100)),),
                (m.Audit.data['evaluation_id'].as_string() == s.evaluation_id,)):
            expected = select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
                m.Audit.action.in_([old.SAVED, subject.SAVED]), *conditions).limit(2)
            if same_sql(statement, expected): return Rows(s.db.events)
        return original_scalars(statement)
    monkeypatch.setattr(s.db, 'scalars', scalars)
    monkeypatch.setattr(s.db, 'flush', lambda: s.flushes.append(copy.deepcopy(
        [(model.__name__, row.data) for (model, _id), row in s.db.rows.items() if model is m.DataRun])))
    s.create_append = lambda cut=CUT: subject.create_frozen(s.db, s.p, s.current, s.call, cut_at=cut)
    return s


def test_actual_append_query_uses_fixed_command_authority_real_local_cut_and_flushes_without_graph_creator(scene):
    s = scene; before = copy.deepcopy((s.parent.data, s.task.data)); row = s.create_append(); s.row_id = row.id
    frozen = subject.checked_frozen(s.db, s.reader, s.task, row.id, evaluation_id=s.call.id)
    marker = frozen['provenance']
    assert marker['schema'] == subject.SCHEMA and marker['producer_family'] == 'append_command_v297'
    assert marker['command_ref']['id'] == s.current['command'].id
    assert marker['creator']['scope_hash'] == old.hashed(marker['scope'])
    assert not {'activation_id', 'node_id', 'cancel_epoch'} & set(marker['creator'])
    assert frozen['result']['result']['evaluated_at'] == CUT and s.db.events[0].action == subject.SAVED
    assert len(s.flushes) == 2 and 'result' in s.flushes[-1][-1][1]
    assert s.creator_calls == 0 and s.authority == [True, False]
    assert (s.parent.data, s.task.data) == before
    assert s.create_append().id == row.id and s.db.additions == [m.DataRun, m.Audit]


def test_old_snapshot_entry_dispatches_exact_append_schema_and_ignores_mutable_job_lease_after_creation(scene):
    s = scene; row = s.create_append(); s.row_id = row.id
    original = copy.deepcopy(row.data)
    s.job.lease_token = uid(999); s.db.info['worker_lease']['lease_token'] = uid(999)
    frozen = old.checked_frozen(s.db, s.reader, s.task, row.id, evaluation_id=s.call.id)
    assert frozen['provenance']['creator']['lease_token'] == s.call.data['lease_token'] != s.job.lease_token
    assert row.data == original and s.db.additions == [m.DataRun, m.Audit]


@pytest.mark.parametrize('fault', ['saved_missing', 'wrong_audit_family', 'changed_result', 'sdk_snapshot', 'future_cut'])
def test_append_query_cannot_use_plausibility_or_wrong_family_instead_of_saved_original(scene, fault):
    s = scene
    if fault == 'future_cut':
        with pytest.raises(HTTPException): s.create_append(s.trusted + timedelta(seconds=1))
        assert s.db.additions == []
        return
    row = s.create_append(); s.row_id = row.id
    if fault == 'saved_missing': s.db.events.clear()
    if fault == 'wrong_audit_family': s.db.events[0].action = old.SAVED
    if fault == 'changed_result':
        changed = copy.deepcopy(row.data)
        changed['result']['result']['observation_evidence'][0]['attributes']['temperature'] = 99
        row.data = changed
    if fault == 'sdk_snapshot': s.call.data['capability_snapshot']['description'] = 'changed without registered cap'
    with pytest.raises(HTTPException): subject.checked_frozen(s.db, s.reader, s.task, row.id, evaluation_id=s.call.id)
    assert s.db.additions == [m.DataRun, m.Audit]


def test_append_marker_cannot_enter_generic_query_write_even_after_new_saved_anchor_is_only_witness(scene, monkeypatch):
    s = scene; row = s.create_append(); s.row_id = row.id
    original_scalars = s.db.scalars
    def source_inventory(statement):
        expected = select(guard.GoalEvaluationV288).where(
            guard.GoalEvaluationV288.workspace_id == s.p.workspace_id,
            guard.GoalEvaluationV288.evaluation_query_id == row.id).limit(2)
        if same_sql(statement, expected): return Rows()
        return original_scalars(statement)
    monkeypatch.setattr(s.db, 'scalars', source_inventory)
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *_args: (
        s.task, s.current['selected']['release'], {'id': s.task.data['business_id']}))
    row.created_at = s.trusted
    view = guard.visible(s.db, s.reader, row)
    assert view['family'] == 'goal_evaluation_v299' and view['reference_only'] is True
    assert 'result' not in view and view['context']['task_id'] == s.task.id
    # Removing marker must still leave a restricted query through new SAVED.
    row.data = {'request': row.data['request'], 'result': row.data['result']}
    with pytest.raises(HTTPException): guard.reject_generic_write(s.db, s.reader, row)
    assert s.db.additions == [m.DataRun, m.Audit]
