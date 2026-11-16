"""Pure end-contract/history and real temporary capture-filesystem negatives.

Synthetic protocol/call-shaped values do not prove a paid source, actor, Worker
or database success. Root owns execution and the genuine H interruption case.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import or_, select

from generic_agents import models as m
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_business_catalogue_allocation_end_v213 as end
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents.contracts import digest
from tests.test_report_business_catalogue_allocation_consumer_v213 import frozen


def uid(): return str(uuid4())


def values():
    p, preview = frozen(); clock = datetime(2026, 10, 28, 1, tzinfo=timezone.utc)
    row = NS(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, request_id=uid(),
        producer_id=preview['source_context']['snapshot']['producer']['id'], model_id=preview['body']['model_id'],
        grant_hash=preview['budget']['grant_hash'], revision=preview['revision'], version=3,
        status='failed', job_id=uid(), reserved_call_id=uid(), data={'preview': preview, 'projection': None,
            'manual_value': None, 'allocation_raw_arguments_b64': None, 'error': end.MESSAGE})
    request = {'request_id': uid(), 'expected_version': 1, 'confirm': True, 'retain_reserved_budget': True}
    body, exact, sha = consumer.read_wire(json.dumps(request).encode(), end.EndInterruptedBody)
    budget = {'id': uid(), 'day': '2026-10-28', 'tokens': preview['preparation']['input_summary']['token_bound'] + 100, 'calls': 2}
    spec=preview['preparation']['generation_contract'];prep=preview['preparation']
    actual_data={'day':'2026-10-28','actor_id':p.user_id,'request_hash':prep['request_hash'],
        'dispatch_options_hash':digest(spec['dispatch_options']),'delivery':spec['delivery'],
        'request_endpoint':spec['endpoint'],'request_body_bytes':prep['input_summary']['prepared_request_bytes'],
        'input_policy_hash':digest(spec['input_policy']),'schema_proof_hash':digest(spec['schema_proof']),
        'gateway_dispatch':{'schema':'cloud-dispatch-v130','identity':consumer.dispatch_identity(row),
            'reservation_request_hash':prep['request_hash'],'attempt_id':uid()},'receipt':{},'finished_at':clock.isoformat()}
    actual_call=NS(id=row.reserved_call_id,workspace_id=p.workspace_id,capability_id=row.model_id,status='failed',
        reserved_tokens=prep['input_summary']['token_bound'],charged_tokens=prep['input_summary']['token_bound'],data=actual_data)
    row.data['call_pin']=consumer._call_pin(actual_call)
    call = {'id': row.reserved_call_id, 'status': 'reserved',
        'reserved_tokens': preview['preparation']['input_summary']['token_bound'],
        'charged_tokens': preview['preparation']['input_summary']['token_bound'], 'data_hash': '1'*64}
    job_data={'actor_id':p.user_id,'consumer_id':row.id,'generation_profile':consumer.ALLOCATION,'recovery_only':True}
    actual_job=NS(id=row.job_id,kind=consumer.JOB,workspace_id=p.workspace_id,status='failed',attempts=3,
        lease_token=None,lease_until=None,data=job_data,idempotency_key='saved-map-design:'+row.request_id)
    job = {'id': row.job_id, 'status': 'running', 'attempts': 3, 'lease_token': uid(),
        'lease_until': (clock-timedelta(seconds=5)).isoformat(), 'data_hash': digest(job_data)}
    marker = {'schema': end.END_SCHEMA, 'consumer_id': row.id, 'command_id': uid(), 'job_id': row.job_id,
        'call_id': row.reserved_call_id, 'expected_version': 1, 'before_version': 1, 'association_was_missing': True,
        'ended_version': 3, 'ended_at': clock.isoformat(), 'preview_hash': preview['preview_hash'],
        'usage_unknown': True, 'retain_reserved_budget': True,
        'capture': capture._dto(consumer.runtime()._context(row), state='missing'),
        'financial_before': {'call': call, 'budget': budget},
        'financial_after': {'call': end._call_finance(actual_call), 'budget': copy.deepcopy(budget)},
        'job_before': job, 'job_after': {**job, 'status': 'failed', 'lease_token': None, 'lease_until': None}}
    marker['marker_hash'] = digest(marker); row.data['interrupted_end'] = marker
    command = consumer.Command(id=marker['command_id'], workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=body.request_id, consumer_id=row.id, operation=end.OPERATION, method='POST',
        path=end.PREFIX+'/'+row.id+'/'+end.OPERATION, exact_body=exact, body_sha256=sha,
        data={'schema': consumer.COMMAND, 'result': {**consumer.result(row, end.OPERATION), 'interrupted_end': copy.deepcopy(marker)}})
    command.command_hash = consumer.h(consumer._command_pin(command))
    event = m.Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, action=end.END_AUDIT,
        object_id=command.id, data=end._audit_data(p, row, command, marker))
    class AuditOnly:
        def __init__(self): self.events=[event]; self.reads=[]; self.current_daily_head={'tokens':1,'calls':999};self.call=actual_call;self.job=actual_job
        def scalars(self, statement):
            expected=select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==end.END_AUDIT,
                m.Audit.object_id==command.id,or_(m.Audit.actor_id==p.user_id,m.Audit.data['actor_id'].as_string()==p.user_id))
            assert str(statement.compile())==str(expected.compile())
            assert statement.compile().params==expected.compile().params
            self.reads.append(m.Audit);return self.events
        def scalar(self, statement):
            entity=statement.column_descriptions[0]['entity'];params=set(statement.compile().params.values())
            assert p.workspace_id in params
            if entity is m.CloudCall:assert actual_call.id in params;return self.call
            if entity is m.Job:assert actual_job.id in params;return self.job
            raise AssertionError('History cannot read current source or daily-budget head')
    ref={'id':command.id,'request_id':command.request_id,'operation':end.OPERATION,'wire_sha256':sha}
    return p,row,command,event,AuditOnly(),ref


@pytest.mark.parametrize('field,value', [('confirm',1),('confirm',False),('retain_reserved_budget',1),
    ('retain_reserved_budget',False),('expected_version',True),('expected_version','1'),('extra','not allowed')])
def test_end_body_never_coerces_confirmation_refund_or_version(field,value):
    body={'request_id':uid(),'expected_version':1,'confirm':True,'retain_reserved_budget':True};body[field]=value
    with pytest.raises(HTTPException) as error:consumer.read_wire(json.dumps(body).encode(),end.EndInterruptedBody)
    assert error.value.status_code==422


def test_end_history_keeps_its_original_budget_witness_when_later_calls_change_the_daily_head():
    p,row,command,event,db,ref=values();assert end.check_parent_command(db,p,row,command,ref=ref)==row.data['interrupted_end']
    db.current_daily_head={'tokens':999999,'calls':1000};row.version+=1
    assert end.check_parent_command(db,p,row,command,ref=ref)==event.data['marker']
    assert db.reads==[m.Audit,m.Audit]


@pytest.mark.parametrize('fault',['audit_missing','actor_moved','refund','delta','capture_complete','raw_change','hidden_command','hidden_result'])
def test_end_resealing_current_rows_does_not_replace_the_independent_original_end_audit(fault):
    p,row,command,event,db,ref=values();marker=row.data['interrupted_end']
    if fault=='audit_missing':db.events=[]
    elif fault=='actor_moved':event.actor_id=uid()
    elif fault=='refund':marker['financial_after']['budget']['tokens']-=1
    elif fault=='delta':marker['association_was_missing']=False
    elif fault=='capture_complete':marker['capture']['response_complete']=True
    elif fault=='raw_change':command.exact_body+=' '
    marker['marker_hash']=digest({k:v for k,v in marker.items() if k!='marker_hash'})
    command.data={**command.data,'result':{**command.data['result'],'interrupted_end':copy.deepcopy(marker)}}
    if fault=='hidden_command':command.data['hidden']='not part of the closed end contract'
    if fault=='hidden_result':command.data['result']['hidden']='not part of the receipt'
    command.command_hash=consumer.h(consumer._command_pin(command))
    with pytest.raises((HTTPException,ValueError)):end.check_parent_command(db,p,row,command,ref=ref)


def test_deleted_consumer_and_command_remain_visible_from_the_end_audit():
    p,_row,_command,event,db,_ref=values()
    with pytest.raises(HTTPException):end.check_inventory(db,p,{}, {},[event])


@pytest.mark.parametrize('fault',['call_reset','charge_changed','call_data_changed','job_revived','job_attempts_changed'])
def test_original_fixed_call_or_job_reset_cannot_hide_behind_the_old_end_marker(fault):
    p,row,command,_event,db,ref=values()
    if fault=='call_reset':db.call.status='reserved'
    elif fault=='charge_changed':db.call.charged_tokens-=1
    elif fault=='call_data_changed':db.call.data={**db.call.data,'new_receipt':'not original'}
    elif fault=='job_revived':db.job.status='running';db.job.lease_token=uid()
    elif fault=='job_attempts_changed':db.job.attempts+=1
    with pytest.raises((HTTPException,ValueError)):end.check_parent_command(db,p,row,command,ref=ref)


def test_old_family_and_other_actor_are_not_endable():
    p,row,*_=values();row.actor_id=uid()
    with pytest.raises(HTTPException):end._family(row,p)
    row.actor_id=p.user_id;row.data['preview']['preparation']={'schema':consumer.preparation.PREPARATION}
    with pytest.raises(HTTPException):end._family(row,p)


@pytest.mark.parametrize('fault',['succeeded','receipt','usage','confirmed_receipt','known_usage_flag','refund'])
def test_unknown_finance_gate_does_not_relabel_a_known_receipt_or_refunded_call(fault):
    call=NS(status='failed',reserved_tokens=17,charged_tokens=17,data={'receipt':{}})
    if fault=='succeeded':call.status='succeeded'
    elif fault=='receipt':call.data['receipt']={'call_id':uid()}
    elif fault=='usage':call.data['usage']={'total_tokens':17}
    elif fault=='confirmed_receipt':call.data['confirmed_receipt']={'verified':True}
    elif fault=='known_usage_flag':call.data['usage_unavailable']=False
    elif fault=='refund':call.charged_tokens=0
    before=copy.deepcopy(call.__dict__)
    with pytest.raises(HTTPException):end._unknown_call(call)
    assert call.__dict__==before


def test_missing_capture_requires_present_storage_and_complete_capture_cannot_be_ended(tmp_path,monkeypatch):
    _p,row,*_=values();call=NS(id=row.reserved_call_id)
    monkeypatch.setattr(end,'settings',lambda:NS(storage_dir=str(tmp_path)))
    monkeypatch.setattr(end.bridge,'root',lambda:tmp_path/'captures')
    assert end._capture(row,call)['state']=='missing'
    monkeypatch.setattr(end,'settings',lambda:NS(storage_dir=str(tmp_path/'lost-disk')))
    with pytest.raises(HTTPException) as error:end._capture(row,call)
    assert error.value.status_code==409
    monkeypatch.setattr(end,'settings',lambda:NS(storage_dir=str(tmp_path)))
    ctx=consumer.runtime()._context(row)
    with capture.prepare(tmp_path/'captures',ctx) as sink:
        assert end._capture(row,call)['state']=='partial'
        sink.mark_request_started();sink.begin_response(200,None,'test-original');sink.append(b'{}');sink.finish(eof=True)
    with pytest.raises(HTTPException) as complete_error:end._capture(row,call)
    assert complete_error.value.status_code==409


def test_corrupt_or_quarantined_capture_is_not_reported_as_absent(tmp_path,monkeypatch):
    _p,row,*_=values();call=NS(id=row.reserved_call_id);root=tmp_path/'captures'
    monkeypatch.setattr(end,'settings',lambda:NS(storage_dir=str(tmp_path)))
    monkeypatch.setattr(end.bridge,'root',lambda:root)
    with capture.prepare(root,consumer.runtime()._context(row)):pass
    (root/row.workspace_id/row.id/'reduce'/'unexpected.bin').write_bytes(b'preserve original')
    with pytest.raises(HTTPException) as error:end._capture(row,call)
    assert error.value.status_code==409 and (root/row.workspace_id/row.id/'reduce'/'unexpected.bin').exists()
