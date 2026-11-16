"""Mounted policy HTTP protocol with explicit storage/execution doubles.

No main API, real DB, approval evidence, Job or Invocation is established by
this file. Root's real pipeline tests verify those authorities separately.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from generic_agents import db as database
from generic_agents import quality_review_store_v152 as quality
from generic_agents import threshold_policy_api_v153 as routes
from generic_agents import threshold_policy_store_v153 as store
from generic_agents import threshold_policy_execution_v153 as execution
from generic_agents import threshold_shadow_store_v151 as shadow
from generic_agents.models import Audit
from generic_agents.security import Principal, authenticated
from generic_agents.threshold_policy_models_v152 import ThresholdPolicyGrant as Grant, ThresholdPolicyCommand as Command, ThresholdPolicySelection as Selection
from generic_agents.threshold_policy_protocol_v152 import ROOT


ACTOR='153abc00-0000-4000-8000-000000000001'
PEER='153abc00-0000-4000-8000-000000000002'
WS='153abc00-0000-4000-8000-000000000003'
REVIEW='153abc00-0000-4000-8000-000000000004'
REQUEST='153abc00-0000-4000-8000-000000000005'
INPUT_REQUEST='153abc00-0000-4000-8000-000000000006'


def encode(value):return json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')
def sha(raw):return hashlib.sha256(raw).hexdigest()


def create_body():
    return {'schema':'threshold-policy-create-v152','request_id':REQUEST,'review_id':REVIEW,
        'expected_review_version':2,'title':'仅验证原策略请求协议','reason':' 保留人工原文 ',
        'max_candidate_inputs':3,'expires_at':'2026-10-23T08:00:00+08:00','confirm_use':True}


def control(version):
    return {'schema':'threshold-policy-control-v152','request_id':str(uuid4()),
        'expected_version':version,'reason':' 明确停止候选；保留原记录 ','confirm':True}


def input_body(version=1):
    return {'schema':'threshold-policy-input-v152','request_id':INPUT_REQUEST,'expected_version':version,
        'expected_choice':'candidate','source_record_id':'合成协议原记录','processing_version':'v1',
        'observed_at':None,'business_day':'2026-10-21','attributes':{'count':19.0}}


class SessionDouble:
    def __init__(self):
        self.grants={};self.selections={};self.commands=[];self.audits=[];self.commits=0;self.flushed=0
        self.seals=[];self.nonces=[];self.monitors=[];self.submits=0;self.nested=[]
    def add(self,row):
        if isinstance(row,Grant):self.grants[row.id]=row
        elif isinstance(row,Selection):self.selections[row.id]=row
        elif isinstance(row,Command):self.commands.append(row)
        elif isinstance(row,Audit):
            if row.id is None:row.id=str(uuid4())
            self.audits.append(row)
        else:raise AssertionError('Unexpected protocol-double write')
    def flush(self):self.flushed+=1
    def commit(self):self.commits+=1
    def scalar(self,query):
        entity=query.column_descriptions[0].get('entity')
        if entity is Selection:return self.selections.get(query.compile().params['id_1'])
        assert entity is None,'Only actual clock lookup or synthetic Selection read is allowed'
        return datetime(2026,10,21,tzinfo=timezone.utc)
    @contextmanager
    def begin_nested(self):
        self.nested.append('enter')
        try:yield
        except BaseException:self.nested.append('error');raise
        else:self.nested.append('success')


@pytest.fixture
def mounted(monkeypatch):
    import socket
    import subprocess
    import sqlalchemy
    def deny(*args,**kwargs):raise AssertionError('Mounted route tests forbid external effects')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)
    monkeypatch.setattr(subprocess,'Popen',deny);monkeypatch.setattr(sqlalchemy,'create_engine',deny)
    monkeypatch.setattr(database,'engine',deny)
    db=SessionDouble();state=SimpleNamespace(role='admin',actor=ACTOR,broken=False,inventory_error=None,submit_error=None)
    scope={key:str(uuid4()) for key in ('instance_id','release_id','entity_id','source_id','baseline_id','candidate_capability_id')}
    scope.update(workspace_id=WS,field='count',field_label='数量',unit=None,rule_id='over',operator='gt')
    review=SimpleNamespace(id=REVIEW,workspace_id=WS,actor_id=ACTOR,evaluation_id=str(uuid4()),shadow_id=str(uuid4()),
        status='approved',version=2,approved_by=ACTOR,approval_hash='a'*64,record_hash='b'*64,
        data={'frozen':{'purpose':'mechanism_check','scope':scope,'criteria':{'max_additional_reviews':0},
            'baseline_manifest':{},'candidate_manifest':{}}})
    def inventory(actual,p):
        assert actual is db and p.workspace_id==WS
        if state.inventory_error is not None:raise state.inventory_error
        return list(db.grants.values()),list(db.commands)
    def get_grant(actual,p,identity,lock=False,check_selections=True):
        assert actual is db
        if identity not in db.grants:raise HTTPException(404,'Synthetic missing policy')
        return db.grants[identity]
    def seal(actual,p,row,operation,command_id=None,monitor_basis=None):
        assert actual is db
        if operation!='create':row.version+=1
        row.record_hash=store.hash_value(store.snapshot(row));db.add(row);db.seals.append(operation)
    def view(actual,p,row):
        # The production display now accepts an identity and inventories it
        # itself, avoiding a second full inventory solely for response assembly.
        if type(row) is str:row=get_grant(actual,p,row,check_selections=False)
        return {'id':row.id,'version':row.version,'status':row.status,
            'value':None if state.broken else 19.0,'production_eligible':False}
    def fresh(actual,p,row,**kwargs):
        if state.broken:raise store.PolicySourceError()
        return row
    def current_choice(actual,p,row,**kwargs):
        fresh(actual,p,row)
        if row.status=='paused':raise HTTPException(409,'Synthetic paused policy')
        return 'baseline' if row.status=='rolled_back' else 'candidate'
    def submit(actual,p,grant,body,command_id,wire_meta):
        db.submits+=1
        if state.submit_error is not None:raise state.submit_error
        row=Selection(id=str(uuid4()),workspace_id=WS,policy_id=grant.id,actor_id=p.user_id,
            source_id=scope['source_id'],source_record_id=body.source_record_id,
            observation_id=str(uuid4()),job_id=str(uuid4()),choice=body.expected_choice,status='queued',version=1,
            record_hash='c'*64,data={'synthetic_protocol_only':True,'wire_meta':deepcopy(wire_meta),'command_id':command_id})
        db.add(row);return row
    def selection_view(actual,p,row,checked=False):
        return {'id':row.id,'policy_id':row.policy_id,'version':row.version,'status':row.status,
            'value':None if state.broken else 19.0,'production_eligible':False}
    monkeypatch.setattr(routes,'lock_current_actor',lambda actual,p:None)
    monkeypatch.setattr(store,'inventory',inventory);monkeypatch.setattr(store,'get_grant',get_grant)
    monkeypatch.setattr(store,'seal_grant',seal);monkeypatch.setattr(store,'view',view)
    monkeypatch.setattr(store,'settings',lambda:SimpleNamespace(threshold_policy_environment='verification'))
    monkeypatch.setattr(store,'fresh_candidate',fresh);monkeypatch.setattr(store,'fresh_baseline',fresh)
    monkeypatch.setattr(store,'current_choice',current_choice)
    monkeypatch.setattr(store,'selection_usage',lambda actual,p,g:([],len(db.selections)))
    monkeypatch.setattr(store,'lock_nonce',lambda actual,p,nonce:db.nonces.append((p.user_id,nonce)))
    monkeypatch.setattr(store,'lock_scope',lambda *args:None);monkeypatch.setattr(store,'lock_source_key',lambda *args:None)
    monkeypatch.setattr(quality,'get_review',lambda *args,**kwargs:review)
    monkeypatch.setattr(quality,'require_approved_review',lambda *args,**kwargs:(review,deepcopy(scope)))
    monkeypatch.setattr(shadow,'frontier',lambda *args:{'schema':'threshold-shadow-frontier-v151','observation_ids':[],'source_record_ids':[]})
    monkeypatch.setattr(execution,'submit_input',submit);monkeypatch.setattr(execution,'selection_view',selection_view)
    monkeypatch.setattr(execution,'ensure_monitor',lambda actual,p,g:db.monitors.append(g.id))
    app=FastAPI();app.include_router(routes.router,prefix='/api/local-rules')
    app.dependency_overrides[authenticated]=lambda:Principal(state.actor,WS,state.role,'protocol-session')
    app.dependency_overrides[database.session]=lambda:db
    with TestClient(app,raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client,db=db,state=state)


def post(h,path,raw):return h.client.post(path,content=raw,headers={'content-type':'application/json'})


def saved(h,path,raw,status):
    response=post(h,path,raw);assert response.status_code==status,response.text
    return response.json()


def lookup(h,path,raw):
    return h.client.get('/api/local-rules/evolution/threshold-policy-requests/'+json.loads(raw)['request_id'],
        params={'expected_sha256':sha(raw),'original_method':'POST','original_path':path})


def test_original_create_wire_and_lost_reply_readback_keep_saved_version_but_replay_is_current(mounted):
    h=mounted;raw=encode(create_body());first=saved(h,ROOT,raw,201);command=h.db.commands[0]
    assert command.data['wire_text'].encode()==raw and command.wire_sha256==sha(raw)
    assert command.fingerprint==routes.command_fingerprint('POST',ROOT,WS,ACTOR,sha(raw))
    assert h.db.audits[0].data==store.command_pin(command)
    row=h.db.grants[first['id']];row.status='rolled_back';row.version=3;h.state.broken=True
    recovered=lookup(h,ROOT,raw);assert recovered.status_code==200
    assert recovered.json()=={'schema':'threshold-policy-request-state-v153','status':'saved',
        'target_id':row.id,'policy_id':row.id,'version':1,'http_status':201,'detail':None}
    replay=saved(h,ROOT,raw,201);assert replay['status']=='rolled_back' and replay['version']==3 and replay['value'] is None
    assert h.db.commits==len(h.db.commands)==len(h.db.monitors)==1


def test_input_202_recovery_keeps_original_version_one_and_reads_current_same_selection(mounted):
    h=mounted;grant=saved(h,ROOT,encode(create_body()),201);path=ROOT+'/'+grant['id']+'/inputs';raw=encode(input_body())
    initial=saved(h,path,raw,202);row=h.db.selections[initial['id']];row.status='completed';row.version=3
    response=lookup(h,path,raw);assert response.status_code==200
    assert response.json()['version']==1 and response.json()['http_status']==202 and response.json()['target_id']==row.id
    current=saved(h,path,raw,202);assert current['id']==row.id and current['version']==3 and current['status']=='completed'
    detail=h.client.get(ROOT+'/'+grant['id']+'/selections/'+row.id);assert detail.status_code==200 and detail.json()==current
    assert h.db.submits==1 and len(h.db.selections)==1 and h.db.commits==2


def test_same_nonce_other_admin_cannot_ambiguous_or_block_original_owner(mounted):
    h=mounted;raw=encode(create_body());first=saved(h,ROOT,raw,201);original=h.db.commands[0]
    peer=SimpleNamespace(**{key:deepcopy(getattr(original,key)) for key in (
        'request_id','method','path','wire_sha256','operation','target_id','policy_id','outcome','data')},actor_id=PEER)
    h.db.commands.insert(0,peer)
    state=lookup(h,ROOT,raw);assert state.status_code==200 and state.json()['target_id']==first['id']
    assert saved(h,ROOT,raw,201)['id']==first['id'] and h.db.commits==1


@pytest.mark.parametrize('change',['body','path'])
def test_same_nonce_changed_body_or_path_is_rejected_without_new_work(mounted,change):
    h=mounted;raw=encode(create_body());grant=saved(h,ROOT,raw,201);path=ROOT
    if change=='body':value=create_body();value['reason']='另一份正文';raw=encode(value)
    else:path=ROOT+'/'+grant['id']+'/rollback'
    response=post(h,path,raw);assert response.status_code==409
    assert h.db.commits==len(h.db.commands)==len(h.db.monitors)==1 and not h.db.submits


@pytest.mark.parametrize('failure',['source','inventory'])
def test_source_or_inventory_failure_never_signs_permanent_rejected_input(mounted,failure):
    h=mounted;grant=saved(h,ROOT,encode(create_body()),201);path=ROOT+'/'+grant['id']+'/inputs';raw=encode(input_body())
    if failure=='source':h.state.broken=True
    else:h.state.inventory_error=store.PolicyInventoryError()
    response=post(h,path,raw);assert response.status_code==409
    assert len(h.db.commands)==h.db.commits==1 and not h.db.submits and not h.db.selections
    h.state.broken=False;h.state.inventory_error=None
    assert lookup(h,path,raw).json()['status']=='unknown'
    assert post(h,path,raw).status_code==202 and h.db.submits==1


def test_source_bad_pause_and_rollback_remain_available_without_refreshing_review(mounted):
    h=mounted;grant=saved(h,ROOT,encode(create_body()),201);h.state.broken=True
    paused=saved(h,ROOT+'/'+grant['id']+'/pause',encode(control(1)),200)
    assert paused['status']=='paused' and paused['value'] is None
    rolled=saved(h,ROOT+'/'+grant['id']+'/rollback',encode(control(2)),200)
    assert rolled['status']=='rolled_back' and rolled['version']==3
    assert h.db.seals==['create','pause','rollback'] and len(h.db.monitors)==1


@pytest.mark.parametrize('reason',['paused','expired','quota'])
def test_semantic_input_409_is_saved_as_rejected_and_original_nonce_is_recoverable(mounted,monkeypatch,reason):
    h=mounted;grant=saved(h,ROOT,encode(create_body()),201)
    path=ROOT+'/'+grant['id']+'/inputs'
    if reason=='paused':
        saved(h,ROOT+'/'+grant['id']+'/pause',encode(control(1)),200)
        raw=encode(input_body(version=2));detail='Synthetic paused policy'
    else:
        raw=encode(input_body());original_choice=store.current_choice
        detail={'expired':'Synthetic policy expiry reached','quota':'Synthetic policy input quota exhausted'}[reason]
        def semantic_rejection(actual,p,row,**kwargs):
            # Preserve the public fresh-source double's original failure type;
            # only a currently valid source gets this ordinary semantic 409.
            original_choice(actual,p,row,**kwargs)
            raise HTTPException(409,detail)
        monkeypatch.setattr(store,'current_choice',semantic_rejection)
    before=(h.db.commits,len(h.db.commands),len(h.db.audits))
    first=post(h,path,raw);assert first.status_code==409 and first.json()=={'detail':detail}
    assert (h.db.commits,len(h.db.commands),len(h.db.audits))==tuple(value+1 for value in before)
    assert not h.db.submits and not h.db.selections
    command=h.db.commands[-1]
    assert command.outcome=='rejected' and command.operation=='input' and command.policy_id==grant['id']
    assert command.wire_sha256==sha(raw) and command.data['wire_text'].encode()==raw
    recovered=lookup(h,path,raw);assert recovered.status_code==200
    assert recovered.json()=={'schema':'threshold-policy-request-state-v153','status':'rejected',
        'target_id':grant['id'],'policy_id':grant['id'],'version':None,'http_status':409,'detail':detail}
    after=(h.db.commits,len(h.db.commands),len(h.db.audits))
    # Even if the current semantic condition later changes, a saved rejection
    # is the original nonce's permanent outcome, not permission to re-dispatch.
    h.db.grants[grant['id']].status='active'
    monkeypatch.setattr(store,'current_choice',lambda *args,**kwargs:'candidate')
    replay=post(h,path,raw);assert replay.status_code==409 and replay.json()==first.json()
    assert after==(h.db.commits,len(h.db.commands),len(h.db.audits)) and not h.db.submits


@pytest.mark.parametrize('role',['operator','viewer'])
def test_only_admin_can_create_policy_even_with_a_synthetic_approved_review(mounted,role):
    h=mounted;h.state.role=role
    response=post(h,ROOT,encode(create_body()));assert response.status_code==403
    assert not h.db.commands and not h.db.commits and not h.db.grants


def test_strict_schema_error_is_recoverable_as_original_rejected_request(mounted):
    h=mounted;value=create_body();value['expected_review_version']=True;raw=encode(value)
    first=post(h,ROOT,raw);assert first.status_code==422
    replay=post(h,ROOT,raw);assert replay.status_code==422 and replay.json()==first.json()
    state=lookup(h,ROOT,raw);assert state.status_code==200 and state.json()['status']=='rejected'
    assert state.json()['http_status']==422 and h.db.commands[0].data['wire_text'].encode()==raw
    assert h.db.commits==len(h.db.commands)==len(h.db.audits)==1 and not h.db.grants


def test_operator_may_submit_current_input_but_cannot_pause_grant(mounted):
    h=mounted;grant=saved(h,ROOT,encode(create_body()),201);h.state.role='operator'
    assert post(h,ROOT+'/'+grant['id']+'/inputs',encode(input_body())).status_code==202
    response=post(h,ROOT+'/'+grant['id']+'/pause',encode(control(1)));assert response.status_code==403
    assert h.db.submits==1 and len(h.db.commands)==2 and h.db.grants[grant['id']].status=='active'


def test_exact_23505_input_conflict_is_semantic_409_without_selection_or_second_intake(mounted):
    h=mounted;grant=saved(h,ROOT,encode(create_body()),201);path=ROOT+'/'+grant['id']+'/inputs';raw=encode(input_body())
    h.state.submit_error=IntegrityError('synthetic duplicate',{},SimpleNamespace(sqlstate='23505'))
    response=post(h,path,raw);assert response.status_code==409
    assert h.db.nested[-1]=='error' and not h.db.selections and h.db.submits==1
    state=lookup(h,path,raw);assert state.json()['status']=='rejected' and state.json()['http_status']==409
    h.state.submit_error=None
    assert post(h,path,raw).status_code==409 and h.db.submits==1 and h.db.commits==2


def test_unknown_nonce_get_does_not_create_authority_or_start_intake(mounted):
    h=mounted;response=lookup(h,ROOT,encode(create_body()));assert response.status_code==200
    assert response.json()=={'schema':'threshold-policy-request-state-v153','status':'unknown',
        'target_id':None,'policy_id':None,'version':None,'http_status':None,'detail':None}
    assert not h.db.commands and not h.db.audits and not h.db.monitors and not h.db.commits and not h.db.submits
