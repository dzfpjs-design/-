"""Actual lease callbacks/local request guards at explicit SQL IO boundaries.

No PostgreSQL or renew/takeover timing is claimed here.  Real two-connection
renewal and rollback after a takeover are separate Root-owned acceptance.
"""
import copy
from datetime import datetime, timedelta, timezone
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from generic_agents import leases
from generic_agents import native_goals, native_goal_time, agents, data_requirements
from generic_agents.contracts import digest
from generic_agents.models import Job, Task, DataRun, User


JOB = '11111111-1111-1111-1111-111111111111'
TOKEN = 'original-owned-token'
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('lease IO-boundary unit tests cannot contact services')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(subprocess, 'Popen', deny)


class Connection:
    def __init__(self):
        self.row = NS(lease_token=TOKEN, status='running', lease_until=NOW + timedelta(seconds=30),
            kind='check_native_business_goal', data={'task_id': 'original-task'})
        self.actor_allowed = True
        self.sql = []

    def execute(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.sql.append(sql)
        entity = statement.column_descriptions[0]['entity']
        if entity is Job:
            assert statement.compile().params['id_1'] == JOB
            row = self.row
        else:
            assert entity is User
            row = NS(id='actor') if self.actor_allowed else None
        return NS(first=lambda: row)

    def scalar(self, statement):
        assert 'clock_timestamp' in str(statement)
        return NOW


class Session:
    def __init__(self):
        self.info = {}
        self.transaction = False
        self.connection_value = Connection()

    def in_transaction(self):
        return self.transaction

    def connection(self):
        return self.connection_value


def attached(monkeypatch):
    db = Session(); callbacks = {}

    def listen(target, event, callback):
        assert target is db
        assert event not in callbacks
        callbacks[event] = callback

    monkeypatch.setattr(leases.event, 'listen', listen)
    leases.attach(db, JOB, TOKEN)
    return db, callbacks


def begin(db, callbacks):
    callbacks['after_begin'](db, NS(nested=False), db.connection_value)


def last_job_sql(db):
    return next(sql for sql in reversed(db.connection_value.sql) if 'jobs.' in sql)


def test_recovery_stays_locked_activation_allows_begin_and_commit_remains_exclusive(monkeypatch):
    db, callbacks = attached(monkeypatch)
    with leases.local_goal_phase(db, JOB, TOKEN) as phase:
        begin(db, callbacks)
        assert 'FOR UPDATE' in last_job_sql(db)
        callbacks['before_commit'](db)
        assert 'FOR UPDATE' in last_job_sql(db)
        phase.activate()
        begin(db, callbacks)
        assert 'FOR UPDATE' not in last_job_sql(db)
        assert 'kind' in last_job_sql(db) and 'data' in last_job_sql(db)
        callbacks['before_commit'](db)
        assert 'FOR UPDATE' in last_job_sql(db)
    assert phase.closed is True and phase.active is False
    assert leases._LOCAL_GOAL_PHASE not in db.info
    begin(db, callbacks)
    assert 'FOR UPDATE' in last_job_sql(db)


@pytest.mark.parametrize('fault', ['wrong_kind', 'non_dict', 'time_only', 'time_hash_only', 'both_time',
    'null_time', 'false_time'])
def test_central_actual_db_family_and_key_presence_reject_fake_local_phase(monkeypatch, fault):
    db, callbacks = attached(monkeypatch)
    with leases.local_goal_phase(db, JOB, TOKEN) as phase:
        phase.activate()
        row = db.connection_value.row
        if fault == 'wrong_kind': row.kind = 'dispatch_acquisition'
        elif fault == 'non_dict': row.data = []
        elif fault == 'time_hash_only': row.data['time_trigger_hash'] = 'a' * 64
        elif fault == 'both_time': row.data.update(time_trigger={}, time_trigger_hash='a' * 64)
        elif fault == 'false_time': row.data['time_trigger'] = False
        else: row.data['time_trigger'] = None
        with pytest.raises(leases.LeaseLost):
            begin(db, callbacks)
    assert leases._LOCAL_GOAL_PHASE not in db.info


@pytest.mark.parametrize('fault', ['token', 'status', 'expired', 'equal_expiry', 'missing_expiry', 'missing_row'])
def test_nonlocking_begin_keeps_all_original_token_status_and_db_clock_fences(monkeypatch, fault):
    db, callbacks = attached(monkeypatch)
    with leases.local_goal_phase(db, JOB, TOKEN) as phase:
        phase.activate()
        row = db.connection_value.row
        if fault == 'token': row.lease_token = 'new-owner-token'
        elif fault == 'status': row.status = 'succeeded'
        elif fault == 'expired': row.lease_until = NOW - timedelta(seconds=1)
        elif fault == 'equal_expiry': row.lease_until = NOW
        elif fault == 'missing_expiry': row.lease_until = None
        else: db.connection_value.row = None
        with pytest.raises(leases.LeaseLost):
            begin(db, callbacks)


def test_actor_revocation_still_rejects_nonlocking_begin_and_final_commit(monkeypatch):
    db, callbacks = attached(monkeypatch)
    db.info['worker_actor'] = NS(user_id='actor', workspace_id='workspace', role='operator')
    with leases.local_goal_phase(db, JOB, TOKEN) as phase:
        phase.activate()
        db.connection_value.actor_allowed = False
        with pytest.raises(leases.ExecutionUnauthorized):
            begin(db, callbacks)
        with pytest.raises(leases.ExecutionUnauthorized):
            callbacks['before_commit'](db)
        assert 'FOR UPDATE' in last_job_sql(db)


def test_takeover_after_begin_is_rejected_at_the_original_exclusive_commit_fence(monkeypatch):
    db, callbacks = attached(monkeypatch)
    with leases.local_goal_phase(db, JOB, TOKEN) as phase:
        phase.activate()
        begin(db, callbacks)
        db.connection_value.row.lease_token = 'actual-successor'
        with pytest.raises(leases.LeaseLost):
            callbacks['before_commit'](db)
        assert 'FOR UPDATE' in last_job_sql(db)


def test_activation_requires_committed_original_recovery_and_exact_ownership(monkeypatch):
    db, _ = attached(monkeypatch)
    with leases.local_goal_phase(db, JOB, TOKEN) as phase:
        db.transaction = True
        with pytest.raises(ValueError): phase.activate()
        db.transaction = False
        db.info['worker_lease']['lease_token'] = 'different-token'
        with pytest.raises(ValueError): phase.activate()
        assert phase.active is False
    with pytest.raises(ValueError): phase.activate()


def test_context_cleanup_occurs_after_handler_failure_and_does_not_change_constants(monkeypatch):
    db, callbacks = attached(monkeypatch)
    with pytest.raises(RuntimeError, match='handler failed'):
        with leases.local_goal_phase(db, JOB, TOKEN) as phase:
            phase.activate()
            begin(db, callbacks)
            raise RuntimeError('handler failed')
    assert leases._LOCAL_GOAL_PHASE not in db.info
    begin(db, callbacks)
    assert 'FOR UPDATE' in last_job_sql(db)
    assert leases.LEASE_SECONDS == 180 and leases.HEARTBEAT_SECONDS == 30


def test_json_flag_is_not_a_server_phase_and_nested_begin_keeps_original_behavior(monkeypatch):
    db, callbacks = attached(monkeypatch)
    db.info[leases._LOCAL_GOAL_PHASE] = {'active': True, 'kind': 'check_native_business_goal'}
    begin(db, callbacks)
    assert 'FOR UPDATE' in last_job_sql(db)
    before = len(db.connection_value.sql)
    callbacks['after_begin'](db, NS(nested=True), db.connection_value)
    assert len(db.connection_value.sql) == before


def local_request():
    return {'mode': 'structured', 'model_profile_id': None, 'tool_steps': [],
        'allow_external_tools': False, 'question': '本地原资料核对', 'entity_ids': [], 'fields': []}


@pytest.mark.parametrize('fault', ['cloud', 'model', 'missing_model', 'tool', 'tool_tuple', 'missing_tools',
    'external', 'numeric_external', 'missing_external', 'non_dict'])
@pytest.mark.parametrize('existing', [False, True])
def test_bad_primary_request_cannot_reach_either_data_agent_call(monkeypatch, fault, existing):
    request = local_request()
    if fault == 'cloud': request['mode'] = 'cloud'
    elif fault == 'model': request['model_profile_id'] = 'published-cloud-model'
    elif fault == 'missing_model': request.pop('model_profile_id')
    elif fault == 'tool': request['tool_steps'] = [{'capability_id': 'external-tool'}]
    elif fault == 'tool_tuple': request['tool_steps'] = ()
    elif fault == 'missing_tools': request.pop('tool_steps')
    elif fault == 'external': request['allow_external_tools'] = True
    elif fault == 'numeric_external': request['allow_external_tools'] = 0
    elif fault == 'missing_external': request.pop('allow_external_tools')
    else: request = None
    # A malformed non-object fresh request fails before the central guard's
    # agent boundary while setting resolved_time; use it only on saved runs.
    if request is None and not existing:
        with pytest.raises(HTTPException): native_goals._local_primary_request(request)
        return
    c = goal_case(monkeypatch, request, existing=existing)
    with pytest.raises(HTTPException) as error:
        native_goals.process(c.db, c.p, c.job)
    assert error.value.status_code == 409 and c.agent_calls == [] and c.db.commits == 0


def goal_case(monkeypatch, request, *, existing):
    p = NS(user_id='actor', workspace_id='workspace', role='operator')
    task = NS(id='task', status='workflow_waiting', data={'native_goal_query_id': 'run' if existing else None,
        'goal_spec': {'purpose': '核对已确认原资料', 'entity_ids': [], 'required_fields': [], 'evidence': {}},
        'goal_window': {'mode': 'archive'}})
    context = {'actual_context_boundary': True}; snapshot = {'original_goal_snapshot_boundary': True}
    run = NS(id='run', data={'request': request, 'native_goal_request_hash': digest(request),
        'goal_delegation': {'task_id': task.id, 'goal_snapshot': snapshot}})
    job = NS(data={'task_id': task.id, 'context_hash': digest(context)})
    agent_calls = []

    class DB:
        def __init__(self): self.rows = []; self.commits = 0
        def add(self, row): self.rows.append(row)
        def flush(self):
            for row in self.rows:
                if row.id is None: row.id = 'run'
        def commit(self): self.commits += 1

    db = DB()

    def scoped(actual_db, model, identity, principal, *args):
        assert actual_db is db and principal is p
        if model is Task:
            assert identity == task.id
            return task
        assert model is DataRun and identity == run.id
        return run

    def data_agent(*args, **kwargs):
        agent_calls.append((args, kwargs))
        raise AssertionError('forbidden primary request reached DataAgent')

    monkeypatch.setattr(native_goals, 'scoped', scoped)
    # These cases exercise the ordinary primary-request guard, with no
    # conflict-fork command or append candidate in the explicit storage IO.
    # Their separate suites authenticate those full durable source families.
    from generic_agents import goal_conflict_observation_service_v259 as conflicts
    from generic_agents import goal_evaluation_maturity_v299 as maturity
    def no_conflict_commands(actual_db,principal,identity):
        assert actual_db is db and principal is p and identity==task.id
        return []
    def no_append_candidate(actual_db,principal,actual_task,actual_job):
        assert actual_db is db and principal is p and actual_task is task and actual_job is job
        assert maturity.KEY not in actual_job.data
        return False
    monkeypatch.setattr(conflicts,'_inventory',no_conflict_commands)
    monkeypatch.setattr(maturity,'handles',no_append_candidate)
    monkeypatch.setattr(native_goals, 'guard', lambda *args, **kwargs: context)
    monkeypatch.setattr(native_goals, 'snapshot', lambda *_: snapshot)
    monkeypatch.setattr(native_goal_time, 'timeout', lambda *_: False)
    monkeypatch.setattr(native_goal_time, 'admitted', lambda *_: True)
    monkeypatch.setattr(native_goal_time, 'expire', lambda *_: False)
    monkeypatch.setattr(agents, 'data_agent', data_agent)
    monkeypatch.setattr(data_requirements, 'prepare_request',
        lambda *args, **kwargs: copy.deepcopy(request))
    return NS(db=db, p=p, job=job, agent_calls=agent_calls)


def test_valid_local_request_is_preserved_without_granting_tool_or_cloud_permission():
    request = local_request(); before = copy.deepcopy(request)
    assert native_goals._local_primary_request(request) is request
    assert request == before
