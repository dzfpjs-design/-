"""Actual scoped codec/inventory/review code with explicit SQL/transport fixtures.

These tests do not prove real PG, provider quality, source IAM or browser UX.
Root separately runs the owned API/worker/restart journey.
"""
import asyncio
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from generic_agents import report_scoped_design_consumer_v181 as app
from generic_agents.models import Audit, CloudCall, Job
from generic_agents.contracts import digest
from generic_agents.report_scoped_design_models_v181 import ReportScopedDesignAttemptV181 as Attempt
from generic_agents.report_scoped_design_models_v181 import ReportScopedDesignAttemptCommandV181 as Command
from tests import test_report_scoped_design_v181 as protocol
from tests.test_report_saved_map_design_consumer_v176 import equal_statement, matches_statement

identity=protocol.identity

@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Synthetic boundary: no network or process')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket.socket,'connect_ex',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)

def scene():
    cap,source,plan=protocol.scene();p=NS(user_id=source['snapshot']['actor_id'],workspace_id=source['snapshot']['workspace_id'],role='operator')
    cap.id=identity(811);cap.workspace_id=p.workspace_id;cap.status='published';cap.content_hash=digest(cap.data)
    body=app.PreviewBody(plan_id=identity(812),expected_plan_version=2,plan_hash=plan['plan_hash'],
        scope_id='custom:alpha',revision=7,model_id=cap.id,note=' 单业务原始说明。 ',activity_id=identity(813))
    prep=app.preparation.build_preparation(cap,source,plan,body.scope_id,note=body.note)
    budget={'schema':'report-scoped-design-budget-v181','call_limit':1,'token_bound':prep['input_summary']['token_bound'],
        'maximum_cost_usd':3,'conservative_cost_usd':0.2,'pricing_basis_hash':'b'*64,'grant_hash':'c'*64}
    preview={'schema':app.PREVIEW,'actor_id':p.user_id,'workspace_id':p.workspace_id,'body':body.model_dump(),
        'source_context':source,'scope_plan':plan,'capability':app._cap_value(cap),'preparation':prep,'budget':budget,**app.FALSE_FLAGS}
    preview['preview_hash']=app.h(preview)
    start={**body.model_dump(),'request_id':body.activity_id,'consent':True,'source_snapshot_hash':source['snapshot']['snapshot_hash'],
        'preview_hash':preview['preview_hash']}
    raw=protocol.raw(start);parsed,exact,sha=app.read_wire(raw,app.StartBody)
    row=Attempt(id=identity(814),workspace_id=p.workspace_id,actor_id=p.user_id,request_id=body.activity_id,
        plan_id=body.plan_id,plan_version=2,plan_hash=body.plan_hash,scope_id=body.scope_id,
        producer_id=source['snapshot']['producer']['id'],model_id=cap.id,grant_hash=budget['grant_hash'],revision=7,
        version=1,status='queued',job_id=identity(815),reserved_call_id=None,data={'schema':app.STATE,'preview':preview,
            'dispatch_started':False,'dispatch_identity':None,'call_pin':None,'response':None,'projection':None,
            'manual_value':None,'error':None,'command_refs':[{'id':identity(816),'request_id':body.activity_id,
                'operation':'start','wire_sha256':sha}]})
    command=Command(id=identity(816),workspace_id=p.workspace_id,actor_id=p.user_id,request_id=body.activity_id,
        plan_id=row.plan_id,attempt_id=row.id,attempt_version=1,body_hash=app.h(parsed.model_dump()),operation='start',
        method='POST',path=app.PREFIX+'/start',exact_body=exact,wire_sha256=sha,data={'schema':app.COMMAND,'result':app.result(row,'start')})
    command.command_hash=app.h(app._command_pin(command))
    anchors=[Audit(id=identity(817),workspace_id=p.workspace_id,actor_id=p.user_id,action=app.COMMAND_AUDIT,
        object_id=command.id,data=app._command_anchor(command)),Audit(id=identity(818),workspace_id=p.workspace_id,
        actor_id=p.user_id,action=app.STATE_AUDIT,object_id=row.id,data=app._state_anchor(row,None))]
    job=Job(id=row.job_id,workspace_id=p.workspace_id,kind=app.JOB,status='queued',version=1,
        idempotency_key='scoped-design-v181:'+row.request_id,data={'actor_id':p.user_id,'attempt_id':row.id},lease_token=identity(819))
    return NS(p=p,body=body,cap=cap,source=source,plan=plan,prep=prep,row=row,command=command,anchors=anchors,job=job,raw=raw)

class IO:
    def __init__(self,c):
        self.c=c;self.rows=[c.row];self.commands=[c.command];self.audits=c.anchors;self.calls=[];self.jobs=[c.job];self.locks=[];self.info={};self.commits=0
    def execute(self,statement,params):
        assert statement.text in {'SELECT pg_advisory_xact_lock(:k)','SELECT pg_advisory_xact_lock_shared(:k)'}
        assert type(params['k']) is int;self.locks.append(statement.text)
    def scalars(self,statement):
        p=self.c.p
        choices=[(select(Attempt).where(Attempt.workspace_id==p.workspace_id,Attempt.actor_id==p.user_id),self.rows),
            (select(Command).where(Command.workspace_id==p.workspace_id,Command.actor_id==p.user_id),self.commands),
            (select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.actor_id==p.user_id,Audit.action.in_([app.STATE_AUDIT,app.COMMAND_AUDIT])),self.audits),
            (select(Attempt).where(Attempt.workspace_id==p.workspace_id),self.rows),
            (select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action.in_([app.STATE_AUDIT,app.COMMAND_AUDIT])),self.audits)]
        for r in self.rows:choices.append((select(CloudCall).where(CloudCall.workspace_id==p.workspace_id,
            CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string()==r.id),self.calls))
        for expected,result in choices:
            if matches_statement(statement,expected):equal_statement(statement,expected);return list(result)
        raise AssertionError('Unexpected SQL')
    def scalar(self,statement):
        p=self.c.p
        for j in self.jobs:
            expected=select(Job).where(Job.workspace_id==p.workspace_id,Job.id==j.id)
            if matches_statement(statement,expected):equal_statement(statement,expected);return j
        for nonce in {r.request_id for r in self.commands}|{identity(899)}:
            expected=select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==nonce)
            if matches_statement(statement,expected):equal_statement(statement,expected);return next((r for r in self.commands if r.request_id==nonce),None)
        raise AssertionError('Unexpected scalar SQL')
    def add(self,row):
        if isinstance(row,Audit):row.id=identity(900+len(self.audits));self.audits.append(row)
        elif isinstance(row,Command):self.commands.append(row)
        else:raise AssertionError('No new Job, Attempt or provider call allowed')
    def commit(self):self.commits+=1

def reserved(c,db):
    r=c.row;prep=c.prep;spec=prep['generation_contract'];app._transition(db,c.p,r,{**r.data,'dispatch_started':True,'dispatch_identity':app.dispatch_identity(r)},'running')
    call=CloudCall(id=identity(820),workspace_id=r.workspace_id,capability_id=r.model_id,status='reserved',
        reserved_tokens=prep['input_summary']['token_bound'],charged_tokens=prep['input_summary']['token_bound'],data={
        'actor_id':r.actor_id,'request_hash':prep['request_hash'],'dispatch_options_hash':digest(spec['dispatch_options']),
        'delivery':app.native.DELIVERY,'request_endpoint':app.native.ENDPOINT,'request_body_bytes':prep['input_summary']['prepared_request_bytes'],
        'input_policy_hash':digest(spec['input_policy']),'schema_proof_hash':digest(spec['schema_proof']),
        'gateway_dispatch':{'schema':'cloud-dispatch-v130','identity':app.dispatch_identity(r),'attempt_id':identity(821),'reservation_request_hash':prep['request_hash']}})
    db.calls=[call];app._associate(db,c.p,r,call);c.job.status='running';db.info['worker_lease']={'job_id':c.job.id,'lease_token':c.job.lease_token}
    return call

def paid(c):
    answer=protocol.response(c.prep,c.source);raw=protocol.envelope(answer);inspected=app.native.inspect_response(raw,200,app.native.STAGE,response_complete=True,http_content_encoding=None)
    receipt={**inspected['metadata'],'call_id':c.row.reserved_call_id,'provider':'qwen','model':app.native.MODEL,'capture_hash':'a'*64,'usage':inspected['usage']}
    return {'body':raw,'receipt':receipt,'capture':{'response_complete':True,'ready':True,'content_encoding_safe':True,
        'body_sha256':hashlib.sha256(raw).hexdigest(),'capture_hash':'a'*64,'http_status':200,'http_content_encoding':None},
        'descriptor':{'synthetic':True},'binding':app.journal_binding(c.row)}

@pytest.mark.parametrize('fault',['plan','version','scope','nonce','consent','legacy','extra','duplicate'])
def test_wire_cannot_borrow_another_scope_or_original_nonce(fault):
    c=scene();v=json.loads(c.raw)
    if fault=='plan':v['plan_id']='not-a-uuid'
    elif fault=='version':v['expected_plan_version']=True
    elif fault=='scope':v['scope_id']=''
    elif fault=='nonce':v['request_id']=identity(898)
    elif fault=='consent':v['consent']=1
    elif fault=='legacy':v['generation_profile']='indexed-v179'
    elif fault=='extra':v['can_apply']=True
    raw=protocol.raw(v)
    if fault=='duplicate':raw=raw[:-1]+b',"consent":true}'
    with pytest.raises(HTTPException):app.read_wire(raw,app.StartBody)

def test_cold_inventory_checks_actual_scope_preparation_and_separate_commands():
    c=scene();db=IO(c);records,commands,calls=app._inventory(db,c.p)
    assert list(records)==[c.row.id] and list(commands)==[c.command.id] and calls=={}
    assert app.view(c.row)['partial_scope'] is True and app.view(c.row)['can_apply'] is False

@pytest.mark.parametrize('fault',['plan_hash','scope_id','plan_version','body_hash','command_version','missing_command','missing_audit','mutated_preview','wrong_job'])
def test_damaged_attempt_or_command_cannot_be_interpreted_as_absence(fault):
    c=scene();db=IO(c)
    if fault in {'plan_hash','scope_id','plan_version'}:setattr(c.row,fault,99 if fault=='plan_version' else 'x')
    elif fault=='body_hash':c.command.body_hash='0'*64
    elif fault=='command_version':c.command.attempt_version=999
    elif fault=='missing_command':db.commands=[]
    elif fault=='missing_audit':db.audits.pop()
    elif fault=='mutated_preview':c.row.data['preview']['body']['scope_id']='custom:beta'
    elif fault=='wrong_job':c.job.kind='read_saved_map_design_v176'
    with pytest.raises(app.InventoryError):app._inventory(db,c.p)

def test_reserved_call_binds_original_scope_and_detects_second_call():
    c=scene();db=IO(c);call=reserved(c,db)
    assert app._inventory(db,c.p)[2][c.row.id] is call
    db.calls.append(call)
    with pytest.raises(app.InventoryError):app._inventory(db,c.p)

def test_real_wire_projection_is_partial_and_paid_response_settles_once(monkeypatch):
    c=scene();db=IO(c);reserved(c,db);answer=paid(c);settlements=[]
    monkeypatch.setattr(app,'runtime',lambda:NS(read_response=lambda *a,**kw:answer))
    from generic_agents import cloud_budget
    monkeypatch.setattr(cloud_budget,'settle_paid_response',lambda *args:settlements.append(args[-1]))
    data,status=app._read_paid(db,c.p,c.row,recover_saved=True)
    assert status=='review' and data['projection']['partial_scope'] is True
    assert data['projection']['aggregate_verified'] is False and data['projection']['can_apply'] is False
    assert settlements==['succeeded'] and len(data['manual_value']['result']['businesses'])==1
    app._transition(db,c.p,c.row,data,status)
    app._read_paid(db,c.p,c.row,recover_saved=True)
    assert settlements==['succeeded']

def test_human_review_is_saved_separately_and_original_nonce_reads_exact_result(monkeypatch):
    c=scene();db=IO(c);reserved(c,db);answer=paid(c)
    monkeypatch.setattr(app,'runtime',lambda:NS(read_response=lambda *a,**kw:answer))
    from generic_agents import cloud_budget
    monkeypatch.setattr(cloud_budget,'settle_paid_response',lambda *a:None)
    data,status=app._read_paid(db,c.p,c.row,recover_saved=True);app._transition(db,c.p,c.row,data,status)
    original=app.h(c.row.data['projection']);value=copy.deepcopy(c.row.data['manual_value']);value['note']='人工修改保存后仍须业务审核。'
    raw=protocol.raw({'request_id':identity(899),'expected_version':c.row.version,'value':value,'confirmed':False})
    async def body():return raw
    req=NS(method='POST',url=NS(path=app.PREFIX+'/'+c.row.id+'/save-review'),body=body)
    monkeypatch.setattr(app,'lock_current_actor',lambda *a:None)
    result=asyncio.run(app.save_review_route(c.row.id,req,c.p,db));assert result['operation']=='save-review'
    assert c.row.data['manual_value']['note']==value['note'] and app.h(c.row.data['projection'])==original
    assert len(db.commands)==2 and len(db.calls)==1 and db.commits==1
    assert app._inventory(db,c.p)[0][c.row.id] is c.row
    again=asyncio.run(app.save_review_route(c.row.id,req,c.p,db));assert again==result and db.commits==1
    probe=app.original_request(identity(899),c.p,db,hashlib.sha256(raw).hexdigest())
    assert probe['found'] is True and probe['result']==result

def test_unknown_original_worker_never_enters_dispatch_again(monkeypatch):
    c=scene();db=IO(c);reserved(c,db);calls=[]
    def read(*a,**kw):calls.append('same-original-read');return None
    def forbidden(*a,**kw):raise AssertionError('An unknown original may not send again')
    monkeypatch.setattr(app,'runtime',lambda:NS(read_response=read,dispatch=forbidden))
    result=app.run(db,c.p,c.job)
    assert result['status']=='response_unknown' and calls==['same-original-read'] and len(db.calls)==1

def test_invalid_human_input_is_recoverable_validation_error_not_server_failure():
    c=scene()
    with pytest.raises(HTTPException) as caught:app._manual({'invalid':'原稿须保留'},c.row.data['preview'])
    assert caught.value.status_code==422 and c.row.data['manual_value'] is None

