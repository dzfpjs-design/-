"""Pure raw-command boundaries; these do not prove IAM, reception or IO."""
import hashlib
import json
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents.observation_data_delivery_v245 import MAX_BODY,_iso,parse


def body():
    return {'schema':'observation-data-delivery-retry-command-v245','request_id':str(uuid4()),
        'expected_context_hash':'a'*64,'reason':'已修复原数据接收原因，仅恢复原事件投递','confirm_retry':True}


def raw(value):return json.dumps(value,ensure_ascii=False,indent=2).encode('utf8')+b'\n'


def test_original_utf8_bytes_and_explicit_consent_are_preserved():
    request=raw(body());typed,exact,hashed=parse(request)
    assert exact.encode('utf8')==request and hashed==hashlib.sha256(request).hexdigest()
    assert typed.model_dump()['confirm_retry'] is True
    compact=json.dumps(typed.model_dump(),ensure_ascii=False,separators=(',',':')).encode('utf8')
    assert compact!=request and parse(compact)[2]!=hashed


@pytest.mark.parametrize('field,value',[
    ('confirm_retry',1),('confirm_retry','true'),('confirm_retry',False),
    ('reason','  \n '),('reason','原记录\x00恢复'),('reason',17),
    ('request_id','not-a-uuid'),('expected_context_hash','F'*64),
    ('new_observation_id',str(uuid4())),('device_actions',1)])
def test_closed_command_rejects_coercion_extra_fields_and_implicit_approval(field,value):
    value_body=body();value_body[field]=value
    with pytest.raises(HTTPException) as failure:parse(raw(value_body))
    assert failure.value.status_code==422


@pytest.mark.parametrize('raw_request',[
    b'\xff',b'null',b'[]',b'{"confirm_retry":true,"confirm_retry":true}',
    b'{"reason":NaN}',b'{"reason":"\\ud800"}'])
def test_invalid_original_json_never_reaches_a_command(raw_request):
    with pytest.raises(HTTPException) as failure:parse(raw_request)
    assert failure.value.status_code==422


@pytest.mark.parametrize('raw_request',[b'',b' '* (MAX_BODY+1)])
def test_raw_size_guard_is_bounded(raw_request):
    with pytest.raises(HTTPException) as failure:parse(raw_request)
    assert failure.value.status_code==413


def test_database_time_is_canonical_utc_and_never_guessed():
    stamp=datetime(2026,10,28,8,0,tzinfo=timezone(timedelta(hours=8)))
    assert _iso(stamp)=='2026-10-28T00:00:00+00:00' and _iso(None) is None
    for value in [datetime(2026,10,28),False,'2026-10-28T00:00:00Z']:
        with pytest.raises(HTTPException) as failure:_iso(value)
        assert failure.value.status_code==409
