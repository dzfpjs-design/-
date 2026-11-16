"""Closed commands and wall-clock semantics; no execution/source claims."""
import copy
import json
from datetime import date, datetime, timezone
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import business_automation_contracts as c, business_automation_service as service


def selection():
    return {'release_id':str(uuid4()),'business_id':'动态业务_甲','trigger_id':'new_data',
        'provider_id':c.PROVIDER,'entity_id':str(uuid4()),'camera_id':str(uuid4()),
        'limits':{'max_task_starts_per_day':None,'min_interval_seconds':0}}


def enable():
    return {'schema':'business-automation-enable-v1','request_id':str(uuid4()),'selection':selection(),
        'expected_preview_hash':'a'*64,'confirm_continuous':True}


def raw(value): return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()


def test_dynamic_business_and_unlimited_optional_task_limit_are_explicit():
    body=enable(); assert c.parse(raw(body),'enable')==body
    body['selection']['trigger_id']='daily_time'
    assert c.parse(raw(body),'enable')['selection']['business_id']=='动态业务_甲'


@pytest.mark.parametrize('path,value',[
    (('confirm_continuous',),1),(('confirm_continuous',),'true'),(('confirm_continuous',),False),
    (('selection','limits','max_task_starts_per_day'),True),
    (('selection','limits','max_task_starts_per_day'),0),
    (('selection','limits','max_task_starts_per_day'),1001),
    (('selection','limits','min_interval_seconds'),.5),
    (('selection','limits','min_interval_seconds'),-1),
    (('selection','limits','min_interval_seconds'),86401),
    (('selection','provider_id'),'caller_module'),(('selection','trigger_id'),'manual'),
    (('selection','business_id'),' '),(('selection','release_id'),'arbitrary'),
    (('expected_preview_hash',),'A'*64),
])
def test_wrong_types_bounds_or_unregistered_values_are_rejected(path,value):
    body=enable();at=body
    for key in path[:-1]:at=at[key]
    at[path[-1]]=value
    with pytest.raises(HTTPException) as error:c.parse(raw(body),'enable')
    assert error.value.status_code==422


@pytest.mark.parametrize('target,field',[((), 'now'),(('selection',),'source'),
    (('selection','limits'),'cloud_budget'),(('selection',),'timezone')])
def test_caller_cannot_supply_fact_clock_or_unknown_fields(target,field):
    body=enable();at=body
    for key in target:at=at[key]
    at[field]='not-allowed'
    with pytest.raises(HTTPException):c.parse(raw(body),'enable')


@pytest.mark.parametrize('value',[b'[]',b'{"x":NaN}',b'{"schema":"x","schema":"y"}',b'\xff',b''])
def test_invalid_wire_is_not_coerced(value):
    with pytest.raises(HTTPException):c.parse(value,'enable')


def test_body_limit_applies_to_utf8_bytes():
    with pytest.raises(HTTPException) as error:c.parse(b' '* (c.MAX_BODY+1),'enable')
    assert error.value.status_code==413


@pytest.mark.parametrize('start,hhmm,expected',[
    ('2026-10-28T00:59:59+00:00','09:00','2026-10-28T01:00:00+00:00'),
    ('2026-10-28T01:00:00+00:00','09:00','2026-10-29T01:00:00+00:00'),
    ('2026-10-28T15:59:59+00:00','00:00','2026-10-28T16:00:00+00:00'),
    ('2026-10-28T16:00:00+00:00','00:00','2026-10-29T16:00:00+00:00'),
    ('2026-10-27T18:00:00-07:00','09:00','2026-10-29T01:00:00+00:00'),
])
def test_china_due_is_strictly_future_independent_of_process_timezone(start,hhmm,expected):
    assert service.first_due(datetime.fromisoformat(start),hhmm).isoformat()==expected


@pytest.mark.parametrize('value',['9:00','25:00','00:60','09:00:00',None])
def test_bad_published_clock_is_blocked(value):
    with pytest.raises(HTTPException):service.first_due(datetime.now(timezone.utc),value)


def test_preview_digest_has_no_runtime_clock_or_counts():
    body=selection();pins={'source':'fixed'};first=service.preview_digest(body,pins)
    assert first==service.preview_digest(copy.deepcopy(body),copy.deepcopy(pins))
    body['limits']['min_interval_seconds']=1
    assert first!=service.preview_digest(body,pins)


@pytest.mark.parametrize('value',[
    datetime.fromisoformat('2026-10-28T08:05:00+08:00'),
    datetime.fromisoformat('2026-10-27T17:05:00-07:00'),
    datetime.fromisoformat('2026-10-28T00:05:00+00:00'),
    '2026-10-28T08:05:00+08:00','2026-10-28T00:05:00Z',
])
def test_real_database_datetime_and_saved_offset_timestamp_have_identical_utc_meaning(value):
    actual=service.instant(value)
    assert actual==datetime(2026,10,28,0,5,tzinfo=timezone.utc)
    assert actual.tzinfo is timezone.utc


@pytest.mark.parametrize('value',[
    datetime(2026,10,28,8,5),date(2026,10,28),'2026-10-28T08:05:00','2026-10-28',None,True,1,{},[],
])
def test_database_time_boundary_never_invents_offset_or_coerces_non_time_values(value):
    with pytest.raises(HTTPException) as error:service.instant(value)
    assert error.value.status_code==422
