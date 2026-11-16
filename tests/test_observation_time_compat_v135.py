"""Real original observation receipts across PostgreSQL session time zones.

Root alone runs these tests on its disposable synthetic database. No new helper
is imported: the same test can run against the retained V134 source to show an
actual API failure, rather than a collection failure or a forged old receipt.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import event as sql_event, select, text
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, Event, Invocation, Job, Observation, PerceptionSignal, Task, now
from tests.test_signal_graph_routing_v134 import (
    routing_scene, waiting_release, graph_seed, v7_scene, reading_scene,
    local_runtime, full_source_scene, run_signal_graph_journey, enable, report,
    deliver, pause, human, task_detail, path, wire, reply, effects,
    _old_path, _old_enable_body, _old_pause, _signals,
)
from tests.test_observation_submission_checkpoint_v128 import _database_snapshot
from tests.test_typed_payload_journey_v131 import _canonical
from tests.test_perception_signals import setup as legacy_reported_setup
from tests.test_signal_routing import new_signal
from tests.test_signal_dual_handoffs import dual_scene


ZONES = ('UTC', 'America/Los_Angeles', 'Asia/Shanghai')
LOS_ANGELES = ZONES[1]


@contextmanager
def session_timezone(zone):
    """Set the real SQL session setting on every new transaction of this Engine."""
    target = engine(); configured = []

    def begin(connection):
        actual = connection.scalar(text("SELECT set_config('TimeZone', :zone, true)"), {'zone': zone})
        assert actual == zone
        configured.append(actual)

    sql_event.listen(target, 'begin', begin)
    try:
        yield configured
    finally:
        sql_event.remove(target, 'begin', begin)


def database_snapshot():
    # The snapshot itself always serializes SQL timestamps in UTC. It retains
    # every column and table; business receipt strings are never normalized.
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            return _database_snapshot(connection)


def instant(value):
    assert type(value) is str
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None
    return parsed


def task_equal(actual, expected):
    # This is the one DTO-only SQL-rendered field. All frozen receipt time
    # strings, graphs, goals, origins, versions and hashes remain byte-exact.
    assert set(actual) == set(expected)
    assert instant(actual['created_at']) == instant(expected['created_at'])
    assert _canonical({key: value for key, value in actual.items() if key != 'created_at'}) == _canonical(
        {key: value for key, value in expected.items() if key != 'created_at'})


def _display_time(left, right, key, *, advancing=False):
    assert (key in left) == (key in right)
    if key not in left: return
    if left[key] is None or right[key] is None:
        assert left[key] is right[key] is None
        return
    current, original = instant(left[key]), instant(right[key])
    if advancing:
        assert current >= original
        # A public clock/current-gate timestamp is a fresh display, never a
        # frozen receipt field. Keep its presence/type and check time order.
        left[key] = right[key] = 'v135-verified-current-clock'
    else:
        assert current == original
        left[key] = right[key] = original.astimezone(timezone.utc).isoformat()


def goal_equal(actual, expected):
    left, right = copy.deepcopy(actual), copy.deepcopy(expected)
    assert set(left) == set(right)
    for key in ('check_worker',):
        if type(left.get(key)) is not dict: continue
        worker, original = left[key], right[key]
        for field in ('queued_at', 'lease_until'):
            _display_time(worker, original, field)
        _display_time(worker, original, 'database_now', advancing=True)
        # last_activity_at can instead be a frozen Job JSON string. Only the
        # genuine SQL-created_at winner may be compared as a SQL presentation.
        if worker.get('job_id') and worker.get('last_activity_at') is not None:
            with Session(engine()) as db:
                job = db.get(Job, worker['job_id']); assert job is not None
                stored = [instant(job.data[field]) for field in ('started_at', 'finished_at', 'updated_at')
                    if type(job.data.get(field)) is str]
                sql_wins = not stored or job.created_at >= max(stored)
                if sql_wins:
                    assert instant(worker['last_activity_at']) == job.created_at
                    _display_time(worker, original, 'last_activity_at')
    if type(left.get('data_query')) is dict and type(left['data_query'].get('worker')) is dict:
        worker, original = left['data_query']['worker'], right['data_query']['worker']
        _display_time(worker, original, 'lease_until')
        _display_time(worker, original, 'database_now', advancing=True)
    assert len(left['reviewable_records']) == len(right['reviewable_records'])
    for item, original in zip(left['reviewable_records'], right['reviewable_records']):
        _display_time(item, original, 'observed_at')
    if type(left.get('check')) is dict:
        _display_time(left['check'], right['check'], 'current_gate_checked_at', advancing=True)
    assert _canonical(left) == _canonical(right)


def response_equal(endpoint, actual, expected):
    if endpoint.startswith('/api/workspace/tasks/') and endpoint.count('/') == 4:
        task_equal(actual, expected)
    elif '/graph/requests/' in endpoint:
        assert set(actual) == set(expected)
        task_equal(actual['task'], expected['task'])
        assert _canonical({key: value for key, value in actual.items() if key != 'task'}) == _canonical(
            {key: value for key, value in expected.items() if key != 'task'})
    elif endpoint.endswith('/goal'):
        goal_equal(actual, expected)
    elif endpoint.endswith('/goal/export'):
        left, right = copy.deepcopy(actual), copy.deepcopy(expected)
        goal_equal(left.pop('goal'), right.pop('goal'))
        assert len(left['original_acquisitions']['items']) == len(right['original_acquisitions']['items'])
        for item, original in zip(left['original_acquisitions']['items'], right['original_acquisitions']['items']):
            _display_time(item, original, 'created_at')
            assert len(item['observations']['items']) == len(original['observations']['items'])
            for observation, saved in zip(item['observations']['items'], original['observations']['items']):
                _display_time(observation, saved, 'observed_at')
        assert _canonical(left) == _canonical(right)
    else:
        assert _canonical(actual) == _canonical(expected)


def cross_read(client, endpoints):
    before = database_snapshot()
    with session_timezone(LOS_ANGELES) as configured:
        expected = {endpoint: reply(client.get(endpoint)) for endpoint in endpoints}
        assert configured
    assert database_snapshot() == before
    for zone in ZONES:
        with session_timezone(zone) as configured:
            for endpoint in endpoints:
                response_equal(endpoint, reply(client.get(endpoint)), expected[endpoint])
            assert configured
        assert database_snapshot() == before
    return expected


def signal_path(value, signal_id):
    return '/api/workspace/instances/' + value['release']['instance_id'] + '/signals/' + signal_id + '/export'


def cheap_scene(client):
    """Only the real legacy human release/numeric rule; no document generation."""
    with session_timezone(LOS_ANGELES):
        release, obj, _, template, _ = legacy_reported_setup(client)
    me = reply(client.get('/api/me'))
    return {'client': client, 'release': release, 'object': obj,
        'source': {'id': template['source_id']}, 'extractor': {'id': template['extractor_id']},
        'workspace_id': me['workspace_id'], 'actor_id': me['user_id'], 'template': template}


def registered_report(value):
    clock = now() - timedelta(seconds=1)
    with Session(engine()) as db:
        zone = db.get(Capability, value['source']['id']).data['specification'].get('timezone', 'UTC')
    body = {**value['template'], 'observed_at': clock.isoformat(),
        'business_day': clock.astimezone(ZoneInfo(zone)).date().isoformat()}
    observation, signal = new_signal(value['client'], value['release'], body)
    return observation, signal['id']


def original_anchor(value, observation_id, signal_id, mint_zone=LOS_ANGELES):
    """Verify real old-form receipt bytes without invoking the new resolver."""
    with Session(engine()) as db:
        row = db.get(Observation, observation_id); signal = db.get(PerceptionSignal, signal_id)
        anomaly = db.get(Event, signal.event_id)
        data_event = db.scalar(select(Event).where(Event.workspace_id == value['workspace_id'],
            Event.idempotency_key == 'data:' + row.id))
        assert anomaly.kind == 'perception.anomaly' and anomaly.idempotency_key == 'anomaly:' + row.id
        assert anomaly.status == data_event.status == 'received'
        original_time = anomaly.data['observed_at']
        assert original_time == signal.data['basis']['reported_at']
        assert instant(data_event.data['observed_at']) == instant(original_time)
        assert instant(original_time) == row.observed_at
        assert instant(original_time).utcoffset() == row.observed_at.astimezone(ZoneInfo(mint_zone)).utcoffset()
        source = db.get(Capability, row.source_id); extractor = db.get(Capability, row.data['extractor_id'])
        # This is the exact published V134 hash preimage. The value is only
        # asserted against the already-saved receipt, never written or signed.
        legacy = {'source_id': row.source_id, 'record': row.source_record_id,
            'processing_version': row.processing_version, 'entity_id': row.entity_id,
            'observed_at': original_time, 'business_day': row.business_day,
            'original_request': row.data.get('original_request'), 'input_hash': row.data.get('input_hash'),
            'attributes': row.data.get('attributes'), 'extracted': row.data.get('extracted'),
            'evidence_ids': row.data.get('evidence_ids', []), 'invocation_id': row.data.get('invocation_id'),
            'extractor_id': row.data.get('extractor_id'), 'source_hash': digest(source.data),
            'extractor_hash': digest(extractor.data)}
        assert signal.data['basis']['observation_hash'] == digest(legacy)
        assert signal.data['basis']['event_hash'] == digest(anomaly.data)
        assert signal.data['basis_hash'] == digest(signal.data['basis'])
        call = db.get(Invocation, row.data['invocation_id'])
        assert call.status == 'succeeded' and call.data['output'] == row.data['extracted']
        return {'event_id': anomaly.id, 'data_event_id': data_event.id,
            'reported_at': original_time, 'observation_hash': digest(legacy),
            'event_hash': digest(anomaly.data), 'basis_hash': signal.data['basis_hash']}


def test_real_la_graph_goal_and_original_draft_survive_each_sql_timezone(routing_scene):
    value = routing_scene; client = value['client']
    with Session(engine()) as db:
        original_cloud = set(db.scalars(select(CloudCall.id)))
    with session_timezone(LOS_ANGELES) as configured:
        first = run_signal_graph_journey(value)
        assert first['acceptance']['response']['acceptance']['evaluation']['completed']
        first_pause_body, _ = pause(value, {'receipt': first['grant_receipt']})
        _, second_command, second_enabled = enable(value)
        observation, _, _, second_signal = report(value)
        delivery = deliver(value, second_signal)
        assert delivery['status'] == 'created' and delivery['task_id'] != first['task_id']
        second_pause_body, _ = pause(value, second_enabled)
        second = task_detail(value, delivery['task_id'])
        second, draft = human(client, second, 'a', draft=True)
        assert second['signal_graph_route_v134']['status'] == 'paused'
        assert next(row for row in second['graph_pending_humans'] if row['node_id'] == 'a')['draft'] == draft['draft']
        assert configured
    original_first = original_anchor(value, first['observation']['id'], first['signal_id'])
    original_second = original_anchor(value, observation['id'], second_signal)
    draft_path = '/api/workspace/tasks/' + second['id'] + '/graph/requests/' + draft['request_id']
    endpoints = [path(value), path(value) + '/requests/' + first['grant_command']['request_id'],
        path(value) + '/requests/' + first_pause_body['request_id'],
        path(value) + '/requests/' + second_command['request_id'],
        path(value) + '/requests/' + second_pause_body['request_id'],
        signal_path(value, first['signal_id']), signal_path(value, second_signal), draft_path]
    for identity in (first['task_id'], second['id']):
        prefix = '/api/workspace/tasks/' + identity
        endpoints.extend((prefix, prefix + '/goal', prefix + '/goal/export'))
    readbacks = cross_read(client, endpoints)
    saved_draft = readbacks[draft_path]
    assert saved_draft['found'] and saved_draft['request'] == {
        'request_id': draft['request_id'], 'op': 'draft', 'actor_id': value['actor_id'],
        'body_hash': digest({'activation_id': draft['activation_id'], 'draft': draft['draft'],
            'actor_id': value['actor_id'], 'expected_version': draft['version'],
            'cancel_epoch': draft['cancel_epoch']})}
    assert readbacks['/api/workspace/tasks/' + first['task_id'] + '/goal']['acceptance'] == first['acceptance']['response']['acceptance']
    assert original_anchor(value, first['observation']['id'], first['signal_id']) == original_first
    assert original_anchor(value, observation['id'], second_signal) == original_second
    with Session(engine()) as db:
        assert set(db.scalars(select(CloudCall.id))) == original_cloud
        assert not db.get(Task, second['id']).data.get('native_goal_acceptance')

    # New records minted under each actual session setting must also be readable
    # under every setting. The paused grant creates no additional graph Tasks.
    for mint_zone in ZONES:
        with session_timezone(mint_zone):
            fresh, _, _, signal_id = report(value)
        anchor = original_anchor(value, fresh['id'], signal_id, mint_zone)
        cross_read(client, [signal_path(value, signal_id)])
        assert original_anchor(value, fresh['id'], signal_id, mint_zone) == anchor
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Task.id).where(Task.workspace_id == value['workspace_id'],
            Task.data['instance_id'].as_string() == value['release']['instance_id'])))) == 2
        assert set(db.scalars(select(CloudCall.id))) == original_cloud


def test_actual_registered_time_and_independent_anchors_fail_closed_without_resealing(client):
    value = cheap_scene(client)
    # The independently saved processing Events may express one instant with
    # different offsets. The selected anomaly and its original Signal basis
    # stay untouched; the data Event is not allowed to replace that authority.
    with session_timezone(LOS_ANGELES):
        equivalent, equivalent_signal = registered_report(value)
    equivalent_anchor = original_anchor(value, equivalent['id'], equivalent_signal)
    with Session(engine()) as db:
        data_event = db.get(Event, equivalent_anchor['data_event_id'])
        changed = copy.deepcopy(data_event.data)
        changed['observed_at'] = instant(equivalent_anchor['reported_at']).astimezone(timezone.utc).isoformat()
        assert changed['observed_at'] != equivalent_anchor['reported_at']
        assert instant(changed['observed_at']) == instant(equivalent_anchor['reported_at'])
        data_event.data = changed
        db.commit()
    cross_read(client, [signal_path(value, equivalent_signal)])
    assert original_anchor(value, equivalent['id'], equivalent_signal) == equivalent_anchor
    # Each fault has its own real input, successful extraction, original Events
    # and Signal. Corrupt rows remain corrupt; no original receipt is repaired,
    # re-signed or used as a positive fixture for a later fault.
    faults = ('wrong_instant', 'naive_time', 'null_time', 'missing_time', 'offset_replacement',
        'basis_event', 'event_payload', 'no_anchor', 'conflicting_anchors')
    for fault in faults:
        with session_timezone(LOS_ANGELES):
            observation, signal_id = registered_report(value)
            assert reply(client.get(signal_path(value, signal_id)))['signal']['integrity_issue'] is None
        anchor = original_anchor(value, observation['id'], signal_id)
        work_before = effects()
        with Session(engine()) as db:
            row = db.get(Observation, observation['id']); signal = db.get(PerceptionSignal, signal_id)
            anomaly = db.get(Event, anchor['event_id']); data_event = db.get(Event, anchor['data_event_id'])
            if fault == 'wrong_instant':
                row.observed_at += timedelta(seconds=1)
            elif fault in {'naive_time', 'null_time', 'missing_time', 'offset_replacement'}:
                changed = copy.deepcopy(anomaly.data)
                if fault == 'naive_time': changed['observed_at'] = instant(anchor['reported_at']).replace(tzinfo=None).isoformat()
                elif fault == 'null_time': changed['observed_at'] = None
                elif fault == 'missing_time': changed.pop('observed_at')
                else:
                    changed['observed_at'] = instant(anchor['reported_at']).astimezone(timezone.utc).isoformat()
                    assert changed['observed_at'] != anchor['reported_at']
                    assert instant(changed['observed_at']) == instant(anchor['reported_at'])
                anomaly.data = changed
            elif fault == 'basis_event':
                # A real, same-observation processing Event is still not the
                # original anomaly Event named by this frozen Signal basis.
                signal.event_id = data_event.id
            elif fault == 'event_payload':
                changed = copy.deepcopy(anomaly.data)
                field = next(iter(changed['extracted']['attributes']))
                changed['extracted']['attributes'][field] += 1
                anomaly.data = changed
            elif fault == 'no_anchor':
                anomaly.idempotency_key = 'v135_unbound_anomaly:' + uuid4().hex
                data_event.idempotency_key = 'v135_unbound_data:' + uuid4().hex
            else:
                changed = copy.deepcopy(data_event.data)
                changed['observed_at'] = (instant(anchor['reported_at']) + timedelta(seconds=1)).isoformat()
                assert instant(changed['observed_at']) != instant(anchor['reported_at'])
                data_event.data = changed
            db.commit()
        assert effects() == work_before
        damaged = database_snapshot()
        for zone in ZONES:
            with session_timezone(zone):
                response = client.get(signal_path(value, signal_id))
                assert response.status_code == 409, (fault, zone, response.text)
                listed = next(item for item in _signals(value) if item['id'] == signal_id)
                assert listed['integrity_issue'] and listed['basis'] is None
            assert database_snapshot() == damaged
        with Session(engine()) as db:
            signal = db.get(PerceptionSignal, signal_id)
            assert signal.data['basis_hash'] == anchor['basis_hash']
            assert signal.data['basis']['observation_hash'] == anchor['observation_hash']
        assert effects() == work_before


def test_real_old_single_and_dual_task_receipts_remain_readable_across_timezones(dual_scene):
    from generic_agents import signal_routing, signal_dual_handoffs
    from generic_agents.signal_dual_models import SignalDualHandoff
    client = dual_scene['admin']; me = reply(client.get('/api/me'))
    value = {**dual_scene, 'client': client, 'actor_id': me['user_id'],
        'human_business_ids': [dual_scene['command']['response_business_id'],
            dual_scene['command']['verification_business_id']],
        'template': {'source_id': dual_scene['source']['id'], 'extractor_id': dual_scene['extractor']['id'],
            'entity_id': dual_scene['object']['id'], 'attributes': {'公开登记值': 65}, 'startup_mode': 'manual'}}
    endpoint = _old_path(value)
    with session_timezone(LOS_ANGELES):
        _old_pause(value, dual_scene['enabled'], dual=True)
        single_command = _old_enable_body(value)
        single = reply(client.post(endpoint + '/enable', json=single_command), status=201)
        observation, signal_id = registered_report(value)
        with Session(engine()) as db:
            signal_routing.dispatch(db, value['workspace_id'], signal_id); db.commit()
        single_signal = next(row for row in _signals(value) if row['id'] == signal_id)
        single_task = single_signal['route_receipt']['task_id']
        assert single_signal['route_receipt']['status'] == 'created'
        assert task_detail(value, single_task)['execution_mode'] == 'confirmed_human_workflow'
        _old_pause(value, single)
        dual_command = _old_enable_body(value, dual=True)
        dual = reply(client.post(endpoint + '/enable-dual', json=dual_command), status=201)
        dual_observation, dual_signal_id = registered_report(value)
        with Session(engine()) as db:
            parent = db.scalar(select(SignalDualHandoff).where(
                SignalDualHandoff.workspace_id == value['workspace_id'],
                SignalDualHandoff.observation_id == dual_observation['id']))
            assert parent is not None; parent_id = parent.id
        for slot in ('response', 'verification'):
            with Session(engine()) as db:
                signal_dual_handoffs.dispatch_slot(db, value['workspace_id'], parent_id, slot); db.commit()
        with Session(engine()) as db:
            parent = db.get(SignalDualHandoff, parent_id)
            dual_tasks = [parent.response_task_id, parent.verification_task_id]
            assert all(dual_tasks) and len(set(dual_tasks)) == 2
        _old_pause(value, dual, dual=True)
    first_anchor = original_anchor(value, observation['id'], signal_id)
    second_anchor = original_anchor(value, dual_observation['id'], dual_signal_id)
    endpoints = [endpoint + '/requests/' + single_command['request_id'],
        endpoint + '/dual/requests/' + dual_command['request_id'],
        signal_path(value, signal_id), signal_path(value, dual_signal_id),
        '/api/workspace/signal-handoffs/' + parent_id]
    endpoints.extend('/api/workspace/tasks/' + identity for identity in [single_task, *dual_tasks])
    readbacks = cross_read(client, endpoints)
    assert readbacks[signal_path(value, signal_id)]['signal']['route_receipt']['task_id'] == single_task
    assert all(readbacks['/api/workspace/tasks/' + identity]['execution_mode'] == 'confirmed_human_workflow'
        for identity in [single_task, *dual_tasks])
    assert original_anchor(value, observation['id'], signal_id) == first_anchor
    assert original_anchor(value, dual_observation['id'], dual_signal_id) == second_anchor
