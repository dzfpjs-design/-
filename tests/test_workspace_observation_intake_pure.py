"""Pure saved-day/ACK contracts; no service, database or model is invoked."""
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import workspace_observation_intake as intake
from generic_agents.contracts import digest


AT = datetime(2026, 10, 25, 0, 1, tzinfo=timezone.utc)


def source(zone='Asia/Shanghai'):
    data = {'specification': {'timezone': zone}, 'data_policy': {}}
    return NS(id=str(uuid4()), workspace_id=str(uuid4()), kind='source', status='published',
        data=data, content_hash=digest(data), validated_hash=digest(data))


def observation(src, *, day='2026-10-15', status='processed'):
    return NS(id=str(uuid4()), workspace_id=src.workspace_id, source_id=src.id,
        business_day=day, status=status, version=2,
        data={'input_hash': 'a'*64, 'submitted_by': str(uuid4())})


def acknowledged(row):
    return {(row.id, 'observation.data'): NS(id=str(uuid4()), status='received')}


def test_late_saved_day_is_not_rebased_and_processed_is_not_itself_ack():
    src = source(); row = observation(src)
    before = deepcopy(row.__dict__)
    pending = intake.daily_value(src, row.business_day, [row], AT)
    assert pending['business_day'] == '2026-10-15' and not pending['provisional']
    assert pending['status'] == 'incomplete' and pending['unreceived_ids'] == [row.id]
    received = intake.daily_value(src, row.business_day, [row], AT, acknowledged(row))
    assert received['status'] == 'complete' and received['unreceived_ids'] == []
    assert received['revision_hash'] != pending['revision_hash']
    assert received['physical_state_verified'] is received['source_clock_verified'] is False
    assert row.__dict__ == before


@pytest.mark.parametrize('stage', ['received', 'queued', 'running', 'cancelled', 'unknown', 'failed'])
def test_every_non_success_stage_remains_visible_and_incomplete(stage):
    src = source(); row = observation(src, status=stage)
    value = intake.daily_value(src, row.business_day, [row], AT)
    assert value['status'] == 'incomplete' and value['records'][0]['status'] == stage
    assert value['failed_ids'] == ([row.id] if stage == 'failed' else [])
    assert value['pending_ids'] == ([] if stage == 'failed' else [row.id])


def test_explicit_utc_and_china_have_distinct_midnight_but_do_not_change_saved_day():
    at = datetime(2026, 10, 24, 16, 1, tzinfo=timezone.utc)
    china = source('Asia/Shanghai'); utc = source('UTC')
    a = observation(china, day='2026-10-24'); b = observation(utc, day='2026-10-24')
    first = intake.daily_value(china, a.business_day, [a], at, acknowledged(a))
    second = intake.daily_value(utc, b.business_day, [b], at, acknowledged(b))
    assert first['status'] == 'complete' and first['provisional'] is False
    assert second['status'] == 'incomplete' and second['provisional'] is True
    assert first['business_day'] == second['business_day'] == '2026-10-24'


@pytest.mark.parametrize('zone', [None, '', 'Invalid/Zone'])
def test_invalid_source_timezone_is_explicitly_incomplete_without_fallback(zone):
    src = source(zone); row = observation(src)
    value = intake.daily_value(src, row.business_day, [row], AT, acknowledged(row))
    assert value['timezone'] == zone and value['status'] == 'incomplete'
    assert 'source_timezone_requires_review' in value['limits']


def test_source_metadata_is_not_a_fabricated_ingestion_pin_or_renewed_authority():
    src = source(); row = observation(src)
    src.status = 'revoked'
    value = intake.daily_value(src, row.business_day, [row], AT, acknowledged(row))
    assert value['source_definition_basis'] == 'current_inventory_metadata_only'
    assert value['producer_actor_basis'] == 'first_saved_submitter_internal_inventory_only'
    assert value['producer_actor_id'] == row.data['submitted_by']
    src.validated_hash = None
    value = intake.daily_value(src, row.business_day, [row], AT, acknowledged(row))
    assert value['status'] == 'incomplete' and value['source_integrity_verified'] is False


def test_simulation_is_labeled_and_never_physical_state():
    src = source(); row = observation(src)
    src.data['specification']['simulation_environment_id'] = str(uuid4())
    src.content_hash = src.validated_hash = digest(src.data)
    value = intake.daily_value(src, row.business_day, [row], AT, acknowledged(row))
    assert value['simulation'] is True and value['physical_state_verified'] is False


@pytest.mark.parametrize('change', ['workspace', 'source', 'day', 'candidate'])
def test_foreign_or_specialized_rows_cannot_enter_ordinary_daily(change):
    src = source(); row = observation(src)
    if change == 'workspace': row.workspace_id = str(uuid4())
    if change == 'source': row.source_id = str(uuid4())
    if change == 'day': row.business_day = '2026-10-14'
    if change == 'candidate': row.data['candidate_only'] = True
    with pytest.raises(HTTPException):
        intake.daily_value(src, '2026-10-15', [row], AT)


@pytest.mark.parametrize('identity', [None, '', 'not-a-workspace', str(uuid4()).upper()])
def test_invalid_scope_cannot_connect_or_deliver(monkeypatch, identity):
    monkeypatch.setattr(intake, 'engine', lambda: pytest.fail('invalid scope connected'))
    with pytest.raises(ValueError): intake.reconcile_daily(identity)
    with pytest.raises(ValueError): intake.deliver_pending(identity)


def test_stop_before_root_does_no_database_work(monkeypatch):
    stop = Event(); stop.set()
    monkeypatch.setattr(intake, 'engine', lambda: pytest.fail('stopped root connected'))
    assert intake.reconcile_daily(str(uuid4()), _stop_event=stop) == 0
    from generic_agents import worker
    monkeypatch.setattr(worker, 'deliver_events', lambda **kwargs: pytest.fail('stopped root delivered'))
    intake.deliver_pending(str(uuid4()), _stop_event=stop)


def test_ack_is_scoped_bounded_by_known_kind_and_stops_between_receivers(monkeypatch):
    from generic_agents import worker
    scope = str(uuid4()); stop = Event(); calls = []
    def receive(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2: stop.set()
    monkeypatch.setattr(worker, 'deliver_events', receive)
    intake.deliver_pending(scope, _stop_event=stop)
    assert calls == [{'workspace_id': scope, 'event_kind': kind} for kind in intake.ACK_KINDS[:2]]
