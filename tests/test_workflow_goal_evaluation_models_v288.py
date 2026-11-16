"""Late-model metadata/default contracts only; no DB or persistence evidence.

SQL compilation and actual registered ORM callbacks exercise the declared
guard. Root must separately create the table in disposable PostgreSQL and
verify real INSERT, UPDATE/DELETE rejection, uniqueness and rollback. These
tests do not install a table, run a migration or authenticate source records.
"""
import copy
from datetime import datetime, timezone

import pytest
from sqlalchemy import DDL, CheckConstraint, UniqueConstraint, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import AddConstraint, CreateTable

from generic_agents.models import DataRun, Task
from generic_agents.typed_payload_orm_v131 import CANONICAL, FORMAT, CanonicalJSONB, constraint_name
from generic_agents import workflow_goal_evaluation_models_v288 as subject


def uid(number):
    return f'00000000-0000-4000-8000-{number:012d}'


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))


def test_table_has_independent_fk_and_original_and_new_query_columns():
    table = subject.GoalEvaluationV288.__table__
    targets = {column.name: {key.target_fullname for key in column.foreign_keys}
        for column in table.c if column.foreign_keys}
    assert targets == {
        'workspace_id': {'workspaces.id'}, 'task_id': {'tasks.id'},
        'instance_id': {'domain_instances.id'}, 'release_id': {'domain_instance_releases.id'},
        'package_id': {'capabilities.id'}, 'actor_id': {'users.id'},
        'original_query_id': {'data_query_runs.id'}, 'evaluation_query_id': {'data_query_runs.id'},
        'evidence_id': {'evidence_objects.id'}, 'invocation_id': {'invocations.id'}, 'job_id': {'jobs.id'},
    }
    assert all(key.ondelete is None for column in table.c for key in column.foreign_keys)
    assert 'native_goal_query_id' not in table.c
    assert 'current_goal_evaluation_v288' not in Task.__table__.c
    assert not any('v288' in column.name for column in DataRun.__table__.c)
    assert table.name == 'workflow_goal_evaluations_v288'


def test_identity_constraints_pin_generation_original_nonce_and_new_query():
    table = subject.GoalEvaluationV288.__table__
    uniques = {tuple(column.name for column in item.columns)
        for item in table.constraints if isinstance(item, UniqueConstraint)}
    assert uniques == {
        ('workspace_id', 'task_id', 'goal_version', 'generation'),
        ('workspace_id', 'request_id'), ('workspace_id', 'request_key'),
        ('evaluation_query_id',), ('evidence_id',), ('invocation_id',),
    }
    checks = ' '.join(sql(AddConstraint(item)) for item in table.constraints if isinstance(item, CheckConstraint))
    assert 'version = 1' in checks and 'goal_version >= 1 AND generation >= 1' in checks
    assert 'original_query_id <> evaluation_query_id' in checks
    assert 'id = invocation_id AND request_id = job_id' in checks
    ddl = sql(CreateTable(table))
    assert 'UNIQUE (workspace_id, task_id, goal_version, generation)' in ddl
    assert 'FOREIGN KEY(original_query_id) REFERENCES data_query_runs (id)' in ddl
    assert 'FOREIGN KEY(evaluation_query_id) REFERENCES data_query_runs (id)' in ddl


def test_payload_uses_existing_typed_canonical_owner_and_projection():
    table = subject.GoalEvaluationV288.__table__
    assert isinstance(table.c.data.type, CanonicalJSONB)
    assert table.c.data.type.owner_table == table.name
    assert FORMAT in table.c and CANONICAL in table.c
    assert table.c[FORMAT].info['typed_payload_internal'] is True
    assert table.c[CANONICAL].info['typed_payload_internal'] is True
    assert any(item.name == constraint_name(table) for item in table.constraints)
    rendered = sql(select(subject.GoalEvaluationV288.data))
    assert 'json_build_object' in rendered and table.name in rendered
    assert FORMAT in rendered and CANONICAL in rendered
    assert table.c[FORMAT].default is not None and table.c[CANONICAL].default is not None


def test_orm_and_pg_guards_are_registered_for_both_update_and_delete():
    model = subject.GoalEvaluationV288
    assert event.contains(model, 'before_insert', subject._check_columns)
    assert event.contains(model, 'before_update', subject._immutable_record)
    assert event.contains(model, 'before_delete', subject._immutable_record)
    with pytest.raises(ValueError, match='append-only'):
        subject._immutable_record(None, None, None)
    guards = list(model.__table__.dispatch.after_create)
    assert len(guards) == 2 and all(isinstance(item, DDL) for item in guards)
    function = sql(DDL(subject.PG_GUARD_FUNCTION).against(model.__table__))
    trigger = sql(DDL(subject.PG_GUARD_TRIGGER).against(model.__table__))
    assert 'RETURNS trigger' in function and "ERRCODE = '55000'" in function
    assert 'RAISE EXCEPTION' in function
    assert 'BEFORE UPDATE OR DELETE ON workflow_goal_evaluations_v288' in trigger
    assert 'FOR EACH ROW' in trigger and 'ga_goal_evaluation_v288_reject_mutation()' in trigger
    assert all(item._ddl_if.dialect == 'postgresql' for item in guards)


def projection_record():
    # Deliberately only column projection data: the contract checker is an
    # explicit boundary in this mapping test, exercised fully in its own file.
    return {'evaluation_id': uid(14), 'scope': {'workspace_id': uid(1), 'task_id': uid(2), 'instance_id': uid(3),
            'release_id': uid(4), 'package_id': uid(5), 'goal_snapshot_hash': 'a' * 64,
            'source_binding_hash': 'b' * 64, 'graph_hash': 'c' * 64},
        'goal_snapshot': {'version': 1, 'hash': 'd' * 64},
        'preparation_policy': {'policy_hash': 'e' * 64}, 'generation': 2,
        'original_query_ref': {'query_id': uid(10)}, 'evaluation_query_ref': {'query_id': uid(11)},
        'evidence_ref': {'evidence_id': uid(12)},
        'producer': {'actor_id': uid(13), 'invocation_id': uid(14), 'job_id': uid(15)},
        'request_id': uid(15), 'request_key': 'original-evaluation-request',
        'record_hash': 'f' * 64, 'cut_at': '2026-11-01T12:05:00+00:00',
        'cut_checked_at': '2026-11-01T12:05:01+00:00', 'simulation_only': True, 'test_only': True}


def test_row_values_detaches_storage_and_mirrors_only_validated_original_pins(monkeypatch):
    record = projection_record(); calls = []
    def checked(raw, **kwargs):
        calls.append(copy.deepcopy(kwargs)); return copy.deepcopy(raw)
    monkeypatch.setattr(subject.contracts, 'check_record', checked)
    values = subject.row_values(record)
    assert len(calls) == 1 and calls[0] == {'expected_scope': record['scope'],
        'goal_snapshot': record['goal_snapshot'], 'preparation_policy': record['preparation_policy'],
        'trusted_now': record['cut_checked_at']}
    assert values['data'] == record and values['data'] is not record
    assert values['version'] == 1 and values['generation'] == 2
    assert values['id'] == values['invocation_id'] == uid(14)
    assert values['request_id'] == values['job_id'] == uid(15)
    assert values['original_query_id'] == uid(10) and values['evaluation_query_id'] == uid(11)
    assert values['actor_id'] == uid(13) and values['invocation_id'] == uid(14) and values['job_id'] == uid(15)
    assert values['cut_at'] == datetime(2026, 11, 1, 12, 5, tzinfo=timezone.utc)
    model = subject.GoalEvaluationV288(**values)
    subject._check_columns(None, None, model)
    values['data']['generation'] = 900
    assert record['generation'] == 2


@pytest.mark.parametrize('column,value', [
    ('task_id', uid(100)), ('workspace_id', uid(100)), ('goal_version', 2),
    ('id', uid(100)), ('request_id', uid(100)),
    ('generation', True), ('version', 2), ('original_query_id', uid(11)),
    ('evaluation_query_id', uid(10)), ('record_hash', '0' * 64),
    ('actor_id', uid(100)), ('simulation_only', False), ('test_only', False),
    ('cut_at', datetime(2026, 11, 1, 12, 5)),
])
def test_insert_columns_cannot_disagree_with_the_sealed_payload(monkeypatch, column, value):
    record = projection_record()
    monkeypatch.setattr(subject.contracts, 'check_record', lambda raw, **kwargs: copy.deepcopy(raw))
    values = subject.row_values(record); values[column] = value
    model = subject.GoalEvaluationV288(**values)
    with pytest.raises(subject.contracts.GoalEvaluationContractError):
        subject._check_columns(None, None, model)
