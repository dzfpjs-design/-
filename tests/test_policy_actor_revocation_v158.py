"""Real actor/cleanup logic with mapped history and explicit Session IO only.

The synthetic approval satisfies the original structural fixture, not actual
quality provenance. An unbound Session owns real mapped writes; flush captures
the DB boundary only. These tests prove no PG locks, lease or persistence.
"""
import pytest
from sqlalchemy.orm import Session

from generic_agents.models import Audit, Invocation, Job, Membership, Observation, User
from generic_agents import threshold_policy_execution_v153 as execution
from generic_agents import threshold_policy_store_v153 as store
from generic_agents.leases import LeaseLost
from generic_agents.threshold_policy_models_v152 import (
    ThresholdPolicyGrant as Grant, ThresholdPolicyCommand as Command,
    ThresholdPolicySelection as Selection,
)
from generic_agents.typed_payload_codec_v131 import canonical_json
from generic_agents.worker import ActorRevoked, actor
from tests.test_policy_inventory_integrity_v154 import ReadOnlyInventoryIO, identity, policy_case
from tests.test_policy_read_inventory_v155 import STAMP, selection_history


class CleanupIO:
    """Whitelist the concrete reads used by resolve, bindings, actor and stop."""
    def __init__(self, db, case, revoked):
        self.db, self.case, self.entry = db, case, case.entry
        self.user = User(id=case.principal.user_id, username='structural-actor',
            password_hash='unused-structural-value', active=revoked != 'inactive')
        self.member = None if revoked == 'missing-membership' else Membership(
            id=identity(8500), workspace_id=case.principal.workspace_id,
            user_id=case.principal.user_id, role='admin')
        self.actor_reads = 0
        self.lock_error = None
        self.lock_reads = 0
        self.flushes = 0

    def params(self, statement):
        # Typed SELECT columns bind codec-envelope keys; only WHERE binds
        # describe the concrete IO selector this whitelist must enforce.
        assert statement.whereclause is not None
        values = statement.whereclause.compile().params
        assert values.get('workspace_id_1') == self.case.principal.workspace_id
        return statement.column_descriptions[0], values

    def get(self, model, key, **options):
        assert model is User and key == self.user.id and not options
        self.actor_reads += 1
        return self.user

    def scalar(self, statement):
        desc, params = self.params(statement)
        entity = desc['entity']
        row, obs, job, call, command, _, states = self.entry
        if entity is Membership:
            assert params.get('user_id_1') == self.user.id
            return self.member
        if entity is Grant:
            assert desc['expr'] is Grant and params.get('id_1') == row.policy_id
            return self.case.grants[0]
        if entity is Observation:
            assert desc['expr'] is Observation and params.get('id_1') == obs.id
            return obs
        if entity is Job:
            if params.get('id_1') == job.id:
                assert desc['expr'] is Job
                return job
            trigger = {'kind': 'selection', 'selection_id': row.id,
                'version': row.version, 'record_hash': row.record_hash}
            assert params.get('idempotency_key_1') == 'policy-watch:' + row.policy_id + ':' + store.hash_value(trigger)
            return None
        assert desc['name'] == 'id', 'only known scalar ownership traces are allowed'
        assert obs.id in params.values() or job.id in params.values() or [job.id] in params.values()
        if entity is Audit:
            assert params.get('action_1') == execution.STATE and 'snapshot' in params.values()
            assert 'observation_id' in params.values() or 'job_id' in params.values()
            return states[0].id
        if entity is Command:
            assert params.get('operation_1') == 'input' and params.get('outcome_1') == 'saved'
            assert 'result_snapshot' in params.values()
            assert 'observation_id' in params.values() or 'job_id' in params.values()
            return command.id
        if entity is Invocation:
            assert call is None and 'job_id' in params.values()
            assert params.get('idempotency_key_1') == 'threshold-policy-call:'
            return None
        pytest.fail('unexpected scalar ownership read')

    def scalars(self, statement):
        desc, params = self.params(statement)
        entity = desc['entity']
        row, obs, job, call, command, issued, states = self.entry
        if entity is Grant: return iter(self.case.grants)
        if entity is Selection:
            assert set(params) <= {'workspace_id_1', 'job_id_1', 'observation_id_1'}
            if 'job_id_1' in params: assert params['job_id_1'] == job.id
            if 'observation_id_1' in params: assert params['observation_id_1'] == obs.id
            return iter([row])
        if entity is Job:
            assert obs.id in params.values() and 'observation_id' in params.values()
            return iter([job])
        if entity is Command:
            assert params.get('operation_1') == 'input'
            return iter([command])
        if entity is Invocation:
            assert call is None and params.get('idempotency_key_1') in {'threshold-policy-call:', row.data['call_key']}
            return iter([])
        if entity is Audit:
            action = params.get('action_1')
            if action == execution.STATE: values = states
            elif action == store.STATE_ACTION: values = self.case.states
            elif action == store.COMMAND_ACTION: values = [issued]
            else: pytest.fail('unexpected audit action')
            target = params.get('object_id_1')
            return iter([value for value in values if target is None or value.object_id == target])
        pytest.fail('unexpected collection read')

    def execute(self, statement):
        entities = tuple(item['entity'] for item in statement.column_descriptions)
        if entities == (Command, Audit):
            return ReadOnlyInventoryIO(self.case).execute(statement)
        assert entities == (User, Membership), 'only the actual current-actor lock read is allowed'
        assert self.user.active and self.member is not None
        self.lock_reads += 1
        assert self.lock_error is not None, 'revoked actor must not enter current-policy checks'
        raise self.lock_error

    def flush(self, objects=None):
        assert objects is None
        self.flushes += 1
        # DB-default capture only. Original _shape, hashes, transitions, audit
        # creation and queue construction all run in the real product code.
        for index, row in enumerate(self.db.new, 1):
            if row.id is None: row.id = identity(9000 + index)
            if row.version is None: row.version = 1
            if row.created_at is None: row.created_at = STAMP


@pytest.fixture
def cleanup_case(monkeypatch):
    case = policy_case()
    case.entry = selection_history(case, 'queued', 1)
    case.commands.append(case.entry[4])
    case.command_audits.append(case.entry[5])
    db = Session()
    db.begin()

    def install(revoked):
        io = CleanupIO(db, case, revoked)
        for name in ('get', 'scalar', 'scalars', 'execute', 'flush'):
            monkeypatch.setattr(db, name, getattr(io, name))
        return io

    try:
        yield db, case, install
    finally:
        db.close()


@pytest.mark.parametrize('revoked', ['inactive', 'missing-membership'])
def test_actual_actor_raises_only_the_dedicated_revocation_type(cleanup_case, revoked):
    db, case, install = cleanup_case
    io = install(revoked)
    with pytest.raises(ActorRevoked, match='任务执行授权已撤销') as error:
        actor(db, case.principal.workspace_id, case.principal.user_id)
    assert type(error.value) is ActorRevoked and isinstance(error.value, ValueError)
    assert io.actor_reads == 1 and io.lock_reads == 0 and io.flushes == 0


def test_actual_active_actor_keeps_original_principal_role(cleanup_case):
    db, case, install = cleanup_case
    io = install('active')
    current = actor(db, case.principal.workspace_id, case.principal.user_id)
    assert (current.user_id, current.workspace_id, current.role, current.session_id) == (
        case.principal.user_id, case.principal.workspace_id, 'admin', 'worker')
    assert io.actor_reads == 1 and io.flushes == 0


@pytest.mark.parametrize('revoked', ['inactive', 'missing-membership'])
def test_real_fail_stops_revoked_actor_after_full_ownership_checks(cleanup_case, revoked):
    db, case, install = cleanup_case
    io = install(revoked)
    row, obs, job, call, command, _, states = case.entry
    original = canonical_json({'input': row.data['original_request'], 'command': command.data})
    previous = row.record_hash
    assert execution.fail(db, case.principal, job, 'structural worker cleanup') is True
    assert row.status == 'stopped' and row.version == 2 and row.data['late'] is False
    assert obs.status == 'failed' and obs.data['failure_type'] == 'threshold_policy_stopped'
    assert '执行授权已撤销' in row.data['error'] and '不重新计算' in row.data['error']
    assert row.data['invocation'] is None and call is None
    assert canonical_json({'input': row.data['original_request'], 'command': command.data}) == original
    assert row.record_hash == store.hash_value(execution.snapshot(row))
    audits = [value for value in db.new if isinstance(value, Audit)]
    stopped = [value for value in audits if value.action == execution.STATE]
    assert len(stopped) == 1 and stopped[0].data['operation'] == 'stop'
    assert stopped[0].data['previous_hash'] == previous
    assert stopped[0].data['snapshot'] == execution.snapshot(row)
    monitors = [value for value in db.new if isinstance(value, Job)]
    assert len(monitors) == 1 and monitors[0].kind == 'check_threshold_policy_v153'
    assert monitors[0].data['trigger'] == {'kind': 'selection', 'selection_id': row.id,
        'version': row.version, 'record_hash': row.record_hash}
    assert len([value for value in audits if value.action == 'threshold_policy.monitor_queued_v153']) == 1
    assert io.actor_reads == 1 and io.lock_reads == 0 and io.flushes == 2


@pytest.mark.parametrize('fault', ['command', 'state'])
def test_real_inventory_damage_propagates_before_actor_or_cleanup_writes(cleanup_case, fault):
    db, case, install = cleanup_case
    io = install('inactive')
    if fault == 'command': case.entry[4].command_hash = '0' * 64
    else: case.entry[6][0].data = {**case.entry[6][0].data, 'previous_hash': '0' * 64}
    row, obs, job, *_ = case.entry
    before = canonical_json([execution.snapshot(row), obs.data])
    with pytest.raises(store.PolicyInventoryError):
        execution.fail(db, case.principal, job, 'must not hide broken ownership')
    assert canonical_json([execution.snapshot(row), obs.data]) == before
    assert io.actor_reads == 0 and io.flushes == 0 and not db.new


@pytest.mark.parametrize('error_kind', [ValueError, LeaseLost], ids=['other-value-error', 'lease-lost'])
def test_current_policy_io_errors_are_not_misclassified_as_actor_revocation(cleanup_case, error_kind):
    db, case, install = cleanup_case
    io = install('active')
    io.lock_error = error_kind('unrelated current-policy lock boundary failure')
    row, obs, job, *_ = case.entry
    before = canonical_json([execution.snapshot(row), obs.data])
    with pytest.raises(error_kind) as error:
        execution.fail(db, case.principal, job, 'must propagate')
    assert error.value is io.lock_error
    assert canonical_json([execution.snapshot(row), obs.data]) == before
    assert io.actor_reads == io.lock_reads == 1 and io.flushes == 0 and not db.new
