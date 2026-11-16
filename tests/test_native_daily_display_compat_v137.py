"""Actual RGB reception/daily delivery across real PostgreSQL time zones.

Only Root executes these tests. The comparison fixture runs the local raster
worker; the time-zone seam changes the real SQL session setting, never original
packet clocks, receipt hashes or saved human decisions.
"""
import copy
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import event as sql_event, select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.native_daily_display_compat_v137 import reuse_saved_daily
from generic_agents.native_receipts import checked_daily
from tests.test_geo_codec import scenes, pytestmark
from tests.test_geo_pairs import pairs
from tests.test_native_geo_pair_reception_v137 import received_pair, good, readonly
from tests.test_native_receipts import bridge

ZONES = ('America/Los_Angeles', 'UTC', 'Asia/Shanghai')


@contextmanager
def session_timezone(zone):
    target = engine(); applied = []

    def begin(connection):
        actual = connection.scalar(text("SELECT set_config('TimeZone', :zone, true)"), {'zone': zone})
        assert actual == zone
        applied.append(actual)

    sql_event.listen(target, 'begin', begin)
    try:
        yield applied
    finally:
        sql_event.remove(target, 'begin', begin)


def database_rows():
    # Every public table/column, with only SQL timestamp rendering fixed for
    # the snapshot itself. Payload text, canonical TEXT and stored hashes stay
    # untouched. No heavyweight checkpoint-fixture module is imported.
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            names = sorted(connection.scalars(text("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'")))
            assert str(m.Observation.__tablename__) in names and str(m.Memory.__tablename__) in names
            quote = connection.dialect.identifier_preparer.quote
            return {name: sorted(connection.scalars(text('SELECT row_to_json(t)::text FROM public.' + quote(name) + ' AS t')))
                for name in names}


def latest_daily(db, value):
    memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == value['principal'].workspace_id,
        m.Memory.data['schema'].as_string() == 'native-multimodal-daily-v1',
        m.Memory.data['instance_id'].as_string() == value['release']['instance_id'])
        .order_by(m.Memory.created_at.desc()).limit(1))
    assert memory is not None
    saved = checked_daily(db, value['principal'], memory)
    original = db.get(m.Event, memory.data['event_id'])
    return memory, saved, original


def settled_pair(client, pairs):
    value = received_pair(client, pairs)
    # The first summary can precede observation.data delivery. Materialize the
    # genuine received state once, then establish the zero-increment baseline.
    bridge(client, value['base'])
    with Session(engine()) as db:
        memory, saved, original = latest_daily(db, value)
        assert saved['records'][0]['data_received'] is True
        value.update(daily_id=memory.id, daily_key=original.idempotency_key,
            saved_daily=copy.deepcopy(saved))
    return value


def test_real_rgb_repeat_sync_in_three_timezones_reuses_original_daily_without_any_db_increment(client, pairs):
    with session_timezone(ZONES[0]) as applied:
        value = settled_pair(client, pairs)
        assert applied
    baseline = database_rows()
    raw = digest(value['saved_daily'])
    for zone in ZONES:
        with session_timezone(zone) as applied:
            saved, listing = bridge(client, value['base'])
            assert saved['created'] == 0 and saved['already_saved'] == 1
            assert listing['records'][0]['data_received'] is True
            assert listing['records'][0]['integrity_error'] is None
            with Session(engine()) as db:
                memory, current, original = latest_daily(db, value)
                assert memory.id == value['daily_id']
                assert original.idempotency_key == value['daily_key']
                assert digest(current) == raw
                observation = db.get(m.Observation, value['observation_id'])
                candidate = {**current, 'records': [copy.deepcopy(current['records'][0])]}
                candidate['records'][0]['created_at'] = observation.created_at.isoformat()
                reused = readonly(lambda: reuse_saved_daily(db, value['principal'], memory, candidate))
                assert reused['key'] == value['daily_key'] and digest(reused['value']) == raw
            assert applied
        assert database_rows() == baseline


def test_actual_human_draft_creates_new_daily_but_source_clocks_declarations_and_typed_values_never_fold(client, pairs):
    with session_timezone(ZONES[0]):
        value = settled_pair(client, pairs)
    original = copy.deepcopy(value['saved_daily'])
    with session_timezone('UTC'):
        task = value['task']
        draft = {'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
            'note': '明确保留工程候选，正在人工核对A/B范围，尚未认定现场状态。'}
        good(client.put('/api/workspace/tasks/' + task['id'] + '/note', json=draft))
        before_sync = database_rows()
        _, listing = bridge(client, value['base'])
        assert listing['records'][0]['record']['review_state'] == 'draft_saved'
        after_sync = database_rows()
        assert len(after_sync[str(m.Event.__tablename__)]) == len(before_sync[str(m.Event.__tablename__)]) + 1
        assert len(after_sync[str(m.Memory.__tablename__)]) == len(before_sync[str(m.Memory.__tablename__)]) + 1
        with Session(engine()) as db:
            memory, current, event = latest_daily(db, value)
            assert memory.id != value['daily_id'] and event.idempotency_key != value['daily_key']
            old = db.get(m.Memory, value['daily_id'])
            assert digest(checked_daily(db, value['principal'], old)) == digest(original)
            observation = db.get(m.Observation, value['observation_id'])
            for fault in ('source_time', 'source_time_offset', 'observed_at', 'declaration',
                    'typed_float', 'typed_boolean', 'negative_zero', 'wrong_instant', 'naive', 'duplicate_id'):
                candidate = copy.deepcopy(current)
                record = candidate['records'][0]
                record['created_at'] = observation.created_at.isoformat()
                if fault == 'source_time':
                    record['record']['source_times'][0] = {'value': '2026-01-01T00:00:00+00:00', 'basis': 'user_declared_unverified'}
                elif fault == 'source_time_offset':
                    # Same instant still cannot normalize a source/basis field.
                    record['record']['source_times'] = [{'value': '2025-12-31T16:00:00-08:00', 'basis': 'unknown'}]
                elif fault == 'observed_at':
                    record['record']['observed_at'] = observation.created_at.isoformat()
                elif fault == 'declaration':
                    record['data_reception']['declaration_status'] = 'declared'
                elif fault == 'typed_float':
                    record['record']['attributes']['差异候选像素数'] = 6.0
                elif fault == 'typed_boolean':
                    candidate['not_authoritative'] = 1
                elif fault == 'negative_zero':
                    record['record']['cloud_calls'] = -0.0
                elif fault == 'wrong_instant':
                    record['created_at'] = (observation.created_at + timedelta(seconds=1)).isoformat()
                elif fault == 'naive':
                    record['created_at'] = observation.created_at.replace(tzinfo=None).isoformat()
                else:
                    candidate['records'].append(copy.deepcopy(record))
                assert readonly(lambda: reuse_saved_daily(db, value['principal'], memory, candidate)) is None, fault
    # The correctly saved new state also stabilizes across another SQL zone.
    baseline = database_rows()
    with session_timezone('Asia/Shanghai'):
        bridge(client, value['base'])
    assert database_rows() == baseline


def test_actual_broken_summary_repairs_once_then_preserves_original_repair_key_and_typed_integrity(client, pairs):
    with session_timezone(ZONES[0]):
        value = settled_pair(client, pairs)
        with Session(engine()) as db:
            damaged = db.get(m.Memory, value['daily_id'])
            data = copy.deepcopy(damaged.data)
            data['records'][0]['record']['attributes']['差异候选像素数'] += 99
            damaged.data = data; db.commit()
        bridge(client, value['base'])
        with Session(engine()) as db:
            memory, saved, original = latest_daily(db, value)
            assert memory.id != value['daily_id']
            assert original.idempotency_key.endswith(':repair:' + value['daily_id'])
            repair_id, repair_key = memory.id, original.idempotency_key
            expected = copy.deepcopy(saved)
    baseline = database_rows()
    for zone in ('UTC', 'Asia/Shanghai'):
        with session_timezone(zone):
            bridge(client, value['base'])
            with Session(engine()) as db:
                memory, saved, original = latest_daily(db, value)
                assert memory.id == repair_id and original.idempotency_key == repair_key
                assert digest(saved) == digest(expected)
                transaction = db.begin_nested()
                try:
                    # Python True == 1 used to let checked_daily accept this.
                    # The new reuse boundary must cold-check typed bytes too.
                    original.data = {**original.data, 'not_authoritative': 1}; db.flush()
                    with pytest.raises(HTTPException) as rejected:
                        readonly(lambda: reuse_saved_daily(db, value['principal'], memory, saved))
                    assert rejected.value.status_code == 409
                finally:
                    transaction.rollback()
        assert database_rows() == baseline
