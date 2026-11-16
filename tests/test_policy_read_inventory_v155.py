"""Real inventory/binding logic with mapped structural rows and Session IO only.

No PG, actual approval, Job execution or reception is proved here. The synthetic
history reuses V154's explicit shape fixture; all inventory, binding, pin and
read-projection methods under test remain real. A real unbound Session supplies
actual transaction events, while only its scalar/scalars IO is replaced.
"""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from generic_agents.models import Audit, Event, Invocation, Job, Observation
from generic_agents import threshold_policy_execution_v153 as execution
from generic_agents import threshold_policy_store_v153 as store
from generic_agents import threshold_policy_views_v153 as views
from generic_agents.threshold_policy_models_v152 import (
    ThresholdPolicyGrant as Grant, ThresholdPolicyCommand as Command,
    ThresholdPolicySelection as Selection,
)
from generic_agents.threshold_policy_protocol_v152 import ROOT
from generic_agents.threshold_policy_read_inventory_v155 import PolicyReadInventory
from generic_agents.threshold_policy_read_projection_v153 import PolicyReadProjection
from generic_agents.threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_policy_inventory_integrity_v154 import identity, policy_case


STAMP = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def selection_history(case, status, index):
    grant = case.grants[0]
    scope = grant.data['scope']
    source_key = 'structural-input-' + str(index)
    sid, oid, jid, cid = (identity(1000 + index), identity(1100 + index),
                          identity(1200 + index), identity(1300 + index))
    original = {
        'source_id': scope['source_id'], 'source_record_id': source_key,
        'processing_version': 'v1', 'entity_id': scope['entity_id'], 'observed_at': None,
        'business_day': '2026-10-21', 'extractor_id': scope['candidate_capability_id'],
        'startup_mode': 'manual', 'attributes': {'temperature': 19.0},
    }
    body = {
        'schema': 'threshold-policy-input-v152', 'request_id': identity(1400 + index),
        'expected_version': 1, 'expected_choice': 'candidate',
        'source_record_id': source_key, 'processing_version': 'v1',
        'observed_at': None, 'business_day': original['business_day'],
        'attributes': deepcopy(original['attributes']),
    }
    raw = canonical_json(body).encode('utf-8')
    path = ROOT + '/' + grant.id + '/inputs'
    wire = {'request_id': body['request_id'], 'method': 'POST', 'path': path,
        'wire_text': raw.decode('utf-8'), 'wire_sha256': wire_sha256(raw),
        'fingerprint': command_fingerprint('POST', path, grant.workspace_id, grant.actor_id, wire_sha256(raw))}
    data = {
        'schema': 'threshold-policy-selection-record-v153',
        'grant_pin': {'id': grant.id, 'version': 1, 'record_hash': case.states[0].data['record_hash'], 'epoch': 1},
        'review_pin': deepcopy(grant.data['review']['pin']), 'scope_hash': store.hash_value(scope),
        'extractor_manifest': deepcopy(grant.data['candidate_manifest']),
        'baseline_manifest': deepcopy(grant.data['baseline_manifest']),
        'wire_meta': wire, 'command_id': cid, 'original_request': original,
        'call_key': 'threshold-policy-call:' + grant.id + ':' + sid,
        'reservation': None, 'invocation': None, 'result': None, 'error': None, 'late': False,
    }
    row = Selection(id=sid, workspace_id=grant.workspace_id, policy_id=grant.id,
        actor_id=grant.actor_id, source_id=scope['source_id'], source_record_id=source_key,
        observation_id=oid, job_id=jid, choice='candidate', status='queued', version=1,
        created_at=STAMP, data=data)
    snapshots = [execution.snapshot(row)]
    call = None
    if status in {'reserved', 'completed', 'failed'}:
        row.status = 'reserved'; row.version = 2
        row.data = deepcopy(row.data)
        row.data['reservation'] = {'job_id': jid, 'lease_token': identity(1500 + index),
            'inputs_hash': store.hash_value({'attributes': original['attributes']}),
            'extractor_id': scope['candidate_capability_id'], 'extractor_hash': scope['candidate_hash'],
            'role': 'perception', 'reserved_at': STAMP.isoformat()}
        snapshots.append(execution.snapshot(row))
    if status in {'completed', 'failed'}:
        call = Invocation(id=identity(1600 + index), workspace_id=grant.workspace_id,
            capability_id=scope['candidate_capability_id'], idempotency_key=row.data['call_key'],
            status='succeeded' if status == 'completed' else 'unknown', version=1, created_at=STAMP,
            data={'role': 'perception', 'approval': None, 'side_effect': False,
                'input_hash': store.hash_value({'attributes': original['attributes']}),
                'capability_hash': scope['candidate_hash'], 'job_id': jid,
                'lease_token': row.data['reservation']['lease_token'],
                'output': {'matched': True} if status == 'completed' else None,
                'error': None if status == 'completed' else 'synthetic unknown call'})
        row.status = status; row.version = 3; row.data = deepcopy(row.data)
        row.data['invocation'] = execution._call_pin(call)
        if status == 'completed':
            row.data['result'] = {'actual_matched': True, 'baseline_projection_matched': False,
                'extracted_hash': store.hash_value(call.data['output']), 'completed_at': STAMP.isoformat()}
        else:
            row.data['error'] = 'synthetic stopped unknown result'
        snapshots.append(execution.snapshot(row))
    elif status == 'stopped':
        row.status = 'stopped'; row.version = 2; row.data = deepcopy(row.data)
        row.data['error'] = 'synthetic paused input'
        snapshots.append(execution.snapshot(row))
    row.record_hash = store.hash_value(execution.snapshot(row))
    obs = Observation(id=oid, workspace_id=grant.workspace_id, version=1, created_at=STAMP,
        source_id=scope['source_id'], source_record_id=source_key, processing_version='v1',
        entity_id=scope['entity_id'], observed_at=None, business_day=original['business_day'],
        status='processed' if status == 'completed' else 'failed' if status in {'failed', 'stopped'} else 'received',
        data={'threshold_policy_selection_id': sid, 'threshold_policy_id': grant.id,
            'submitted_by': grant.actor_id, 'original_request': deepcopy(original),
            'input_hash': store.hash_value(original), 'attributes': deepcopy(original['attributes']),
            'extractor_id': scope['candidate_capability_id']})
    if status == 'completed':
        obs.data = {**obs.data, 'invocation_id': call.id, 'extracted': deepcopy(call.data['output'])}
    job = Job(id=jid, workspace_id=grant.workspace_id, version=1, created_at=STAMP,
        kind='process_reported_observation', idempotency_key='extract:' + oid,
        status='succeeded' if status == 'completed' else 'failed' if status in {'failed', 'stopped'} else 'queued',
        attempts=1, available_at=STAMP, lease_until=None, lease_token=None,
        data={'threshold_policy_selection_id': sid, 'threshold_policy_id': grant.id,
            'observation_id': oid, 'actor_id': grant.actor_id, 'extractor_id': scope['candidate_capability_id']})
    command = Command(id=cid, workspace_id=grant.workspace_id, actor_id=grant.actor_id,
        request_id=body['request_id'], method='POST', path=path, wire_sha256=wire['wire_sha256'],
        fingerprint=wire['fingerprint'], operation='input', target_id=sid, policy_id=grant.id,
        outcome='saved', version=1, created_at=STAMP,
        data={'schema': 'threshold-policy-command-record-v153', 'wire_text': wire['wire_text'],
              'result_snapshot': deepcopy(snapshots[0])})
    command.command_hash = store.hash_value(store.command_payload(command))
    command_audit = Audit(id=identity(1700 + index), workspace_id=grant.workspace_id,
        actor_id=grant.actor_id, action=store.COMMAND_ACTION, object_id=cid,
        version=1, created_at=STAMP, data=store.command_pin(command))
    states, previous = [], None
    for offset, saved in enumerate(snapshots):
        operation = 'create' if offset == 0 else 'reserve' if saved['status'] == 'reserved' else {
            'completed': 'complete', 'failed': 'fail', 'stopped': 'stop'}[saved['status']]
        fingerprint = store.hash_value(saved)
        states.append(Audit(id=identity(1800 + index * 10 + offset), workspace_id=grant.workspace_id,
            actor_id=grant.actor_id, action=execution.STATE, object_id=sid, version=1, created_at=STAMP,
            data={'schema': 'threshold-policy-selection-state-v153', 'selection_id': sid,
                'workspace_id': grant.workspace_id, 'actor_id': grant.actor_id,
                'version': saved['version'], 'status': saved['status'], 'record_hash': fingerprint,
                'previous_hash': previous, 'operation': operation,
                'command_id': cid if offset == 0 else None, 'snapshot': deepcopy(saved)}))
        previous = fingerprint
    return row, obs, job, call, command, command_audit, states


@pytest.fixture
def read_case(monkeypatch):
    case = policy_case()
    case.entries = [selection_history(case, status, index)
        for index, status in enumerate(('queued', 'reserved', 'completed', 'failed', 'stopped'), 1)]
    case.io = []
    db = Session()

    def begin_io():
        if db.get_transaction() is None:
            db.begin()

    def scalars(statement):
        begin_io()
        entity = statement.column_descriptions[0]['entity']
        params = statement.compile().params
        case.io.append(('many', entity))
        if entity is Selection: return iter([item[0] for item in case.entries])
        if entity is Command: return iter([item[4] for item in case.entries])
        if entity is Invocation:
            key = params.get('idempotency_key_1')
            return iter([item[3] for item in case.entries if item[3] is not None
                and (key == 'threshold-policy-call:' or item[3].idempotency_key == key)])
        if entity is Event: return iter([])
        if entity is Audit:
            action = params.get('action_1')
            if action == execution.STATE: return iter([row for item in case.entries for row in item[6]])
            if action == store.STATE_ACTION: return iter(case.states)
            if action == store.COMMAND_ACTION:
                target = params.get('object_id_1')
                return iter([item[5] for item in case.entries if item[5].object_id == target])
        pytest.fail('unexpected read in the explicit Selection IO seam')

    def scalar(statement):
        begin_io()
        entity = statement.column_descriptions[0]['entity']
        params = statement.compile().params
        case.io.append(('one', entity))
        target = params.get('id_1')
        if entity is Grant:
            assert target == case.grants[0].id
            return case.grants[0]
        for item in case.entries:
            for row in item[:4]:
                if row is not None and type(row) is entity and row.id == target: return row
        pytest.fail('unexpected scalar read in the explicit Selection IO seam')

    monkeypatch.setattr(db, 'scalars', scalars)
    monkeypatch.setattr(db, 'scalar', scalar)
    try:
        yield db, case
    finally:
        db.close()


def rejected(action):
    with pytest.raises(Exception) as error:
        action()
    assert getattr(error.value, 'status_code', None) == 409


def loaded(db, case):
    # Simulate the initial real Grant SELECT: it creates a real Session root
    # transaction through the IO seam, never a synthetic transaction object.
    from sqlalchemy import select
    read = PolicyReadInventory(db, case.principal)
    db.scalar(select(Grant).where(Grant.id == case.grants[0].id))
    read.selection_data(db, case.principal)
    return read


def test_default_inventory_stays_cold_and_returns_original_tuple_without_a_collector(read_case):
    db, case = read_case
    first = execution._selection_records(db, case.principal)
    second = execution._selection_records(db, case.principal)
    assert type(first) is tuple and len(first) == 2 and type(first[0]) is list and type(first[1]) is dict
    assert first[0] == second[0] == [item[0] for item in case.entries]
    assert case.io.count(('many', Selection)) == 2
    assert case.io.count(('one', Observation)) == 2 * len(case.entries)


def test_one_display_reuses_complete_inventory_and_bindings_without_dropping_any_row(read_case):
    db, case = read_case
    with loaded(db, case) as read:
        p, grant = case.principal, case.grants[0]
        rows, chains = read.selection_data(db, p)
        values, used = store.selection_usage(db, p, grant, _read_inventory=read)
        assert len(rows) == len(chains) == used == 5
        assert [row.status for row in values] == ['queued', 'reserved', 'completed', 'failed', 'stopped']
        projection = PolicyReadProjection(db, p, grant, values, _read_inventory=read).finish(db, p, grant, True, True)
        before = list(case.io)
        for item in case.entries:
            assert projection.binding(db, p, grant, item[0]) == item[1:4]
            output = views.selection_view(db, p, item[0], checked=True, _readiness=projection)
            assert output['status'] == item[0].status
            assert output['invocation_status'] == (item[3].status if item[3] is not None else None)
        # Modern reception still independently queries the actual Event seam.
        assert case.io[len(before):] == [('many', Event)] * len(values)
        assert case.io.count(('many', Selection)) == 1
        assert case.io.count(('one', Observation)) == len(values)
        read.check(db, p)


def test_a_warm_display_does_not_cache_a_default_cold_inventory(read_case):
    db, case = read_case
    with loaded(db, case):
        execution._selection_records(db, case.principal)
        assert case.io.count(('many', Selection)) == 2
        assert case.io.count(('one', Observation)) == 2 * len(case.entries)


@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'role', 'session_id'])
def test_identity_change_cannot_reuse_display_inventory(read_case, field):
    db, case = read_case
    with loaded(db, case) as read:
        changed = SimpleNamespace(**vars(case.principal))
        setattr(changed, field, 'different-' + getattr(changed, field))
        rejected(lambda: read.selection_data(db, changed))


def test_other_session_and_commit_after_binding_reject_reuse(read_case):
    db, case = read_case
    with loaded(db, case) as read:
        with Session() as other:
            other.begin()
            rejected(lambda: read.selection_data(other, case.principal))
        db.commit(); db.begin()
        rejected(lambda: read.selection_data(db, case.principal))


def test_fresh_session_binds_its_first_real_transaction_and_rejects_commit_before_first_read(read_case):
    db, case = read_case
    assert db.get_transaction() is None
    with PolicyReadInventory(db, case.principal) as read:
        db.commit()  # Session creates and ends a real logical transaction.
        db.begin()
        rejected(lambda: read.selection_data(db, case.principal))


@pytest.mark.parametrize('target', ['grant', 'selection', 'grant-state', 'selection-state',
    'observation', 'job', 'invocation', 'command', 'command-audit'])
@pytest.mark.parametrize('change', ['typed', 'field'])
def test_all_pinned_models_reject_typed_swaps_and_dto_field_changes(read_case, target, change):
    db, case = read_case
    with loaded(db, case) as read:
        entry = case.entries[2]
        models = {'grant': case.grants[0], 'selection': entry[0], 'grant-state': case.states[0],
            'selection-state': entry[6][0], 'observation': entry[1], 'job': entry[2],
            'invocation': entry[3], 'command': entry[4], 'command-audit': entry[5]}
        row = models[target]
        if change == 'typed': row.version = True
        elif target == 'grant': row.status = 'paused'
        elif target == 'selection': row.status = 'stopped'
        elif target == 'observation': row.observed_at = STAMP
        elif target == 'job': row.status = 'cancelled'
        elif target == 'invocation': row.data = {**row.data, 'side_effect': True}
        elif target == 'command': row.data = {**row.data, 'wire_text': row.data['wire_text'] + ' '}
        else: row.data = {**row.data, 'extra': True}
        rejected(lambda: read.binding(db, case.principal, case.grants[0], entry[0]))
        rejected(lambda: read.check(db, case.principal))


def test_float_integer_and_sql_time_json_string_are_distinct_complete_pins(read_case):
    db, case = read_case
    with loaded(db, case) as read:
        observation = case.entries[0][1]
        observation.data = deepcopy(observation.data)
        observation.data['attributes']['temperature'] = 19
        rejected(lambda: read.check(db, case.principal))
    # A separate assembly may pin a SQL datetime but cannot accept its JSON
    # string spelling as an equivalent typed column value.
    case.entries[0][1].data['attributes']['temperature'] = 19.0
    with loaded(db, case) as read:
        case.entries[0][2].available_at = STAMP.isoformat()
        rejected(lambda: read.check(db, case.principal))


def test_original_negative_zero_in_a_bound_model_cannot_become_positive_zero(read_case):
    db, case = read_case
    job = case.entries[0][2]
    job.data = {**job.data, 'progress': -0.0}
    with loaded(db, case) as read:
        job.data = {**job.data, 'progress': 0.0}
        rejected(lambda: read.binding(db, case.principal, case.grants[0], case.entries[0][0]))


def test_failed_final_row_inventory_never_publishes_partially_collected_bindings(read_case):
    db, case = read_case
    del case.entries[-1][5].data['result_hash']
    from sqlalchemy import select
    with PolicyReadInventory(db, case.principal) as read:
        db.scalar(select(Grant).where(Grant.id == case.grants[0].id))
        rejected(lambda: read.selection_data(db, case.principal))
        assert read._rows is None and read._bindings == {}
        rejected(lambda: read.binding(db, case.principal, case.grants[0], case.entries[0][0]))


def test_closed_assembly_is_not_reusable_in_another_display(read_case):
    db, case = read_case
    read = loaded(db, case)
    read.close()
    rejected(lambda: read.selection_data(db, case.principal))
