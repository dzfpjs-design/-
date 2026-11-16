"""Mounted ASGI quality routes with explicit Session/public-store doubles.

These are HTTP protocol, dispatch and recovery tests, not evidence that a real
database, material, label, comparison or shadow inventory has been verified.
Root's independent PG/HTTP journeys own that acceptance. No main api is loaded.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from generic_agents import db as database
from generic_agents import quality_review_api_v152 as routes
from generic_agents import quality_review_store_v152 as store
from generic_agents.models import Audit
from generic_agents.quality_review_models_v152 import ThresholdQualityCommand, ThresholdQualityReview
from generic_agents.quality_review_protocol_v152 import ROOT
from generic_agents.security import Principal, authenticated


ACTOR='152abc00-0000-4000-8000-000000000001'
PEER='152abc00-0000-4000-8000-000000000002'
OTHER='152abc00-0000-4000-8000-000000000003'
WORKSPACE='152abc00-0000-4000-8000-000000000004'
EVALUATION='152abc00-0000-4000-8000-000000000005'
SHADOW='152abc00-0000-4000-8000-000000000006'
REQUEST='152abc00-0000-4000-8000-000000000007'
SESSION='152abc00-0000-4000-8000-000000000008'
STATE_ROOT='/api/local-rules/evolution/threshold-quality-requests/'


def body(request_id=REQUEST):
    return {'schema':'threshold-quality-draft-v152','request_id':request_id,
        'evaluation_id':EVALUATION,'expected_evaluation_version':7,
        'shadow_id':SHADOW,'expected_shadow_version':3,'title':'合成路由协议稿',
        'scope_statement':'','sampling_statement':' 保留原人工说明 ',
        'limitations':'','unresolved_items':[],'representative_confirmation':False,'label_sources':[]}


def control(version,reason='明确撤回；这不是新的执行许可'):
    return {'schema':'threshold-quality-control-v152','request_id':str(uuid4()),
        'expected_version':version,'reason':reason,'confirm':True}


def wire(value):
    return json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')


def sha(raw):return hashlib.sha256(raw).hexdigest()


class MemorySession:
    """The production SQL inventory reader is deliberately replaced below."""
    def __init__(self):
        self.reviews={};self.commands=[];self.audits=[];self.commits=0;self.flushes=0
        self.nested=[];self.nonce_locks=[];self.actor_locks=[];self.seals=[]

    def add(self,row):
        if isinstance(row,ThresholdQualityReview):self.reviews[row.id]=row
        elif isinstance(row,ThresholdQualityCommand):self.commands.append(row)
        elif isinstance(row,Audit):
            if row.id is None:row.id=str(uuid4())
            self.audits.append(row)
        else:raise AssertionError('Unexpected persistence outside the synthetic quality route seam')

    def flush(self):self.flushes+=1
    def commit(self):self.commits+=1

    @contextmanager
    def begin_nested(self):
        self.nested.append('enter')
        try:yield
        except BaseException:
            self.nested.append('error');raise
        else:self.nested.append('success')


@pytest.fixture
def mounted(monkeypatch):
    # HTTP is in-process ASGI. A stray real transport, engine or subprocess is
    # an error, never a silently successful fallback for a missing dependency.
    import socket
    import subprocess
    import sqlalchemy
    def forbidden(*args,**kwargs):raise AssertionError('Mounted protocol tests forbid external effects')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket.socket,'connect_ex',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(sqlalchemy,'create_engine',forbidden)
    monkeypatch.setattr(database,'engine',forbidden)
    db=MemorySession();state=SimpleNamespace(role='admin',actor=ACTOR,source_error=None,inventory_error=None,
        hidden=False,context_reads=0,view_reads=0)

    def principal():return Principal(state.actor,WORKSPACE,state.role,SESSION)
    def current_actor(actual,p):
        assert actual is db;db.actor_locks.append((p.user_id,p.workspace_id,p.role))
    def inventory(actual,p):
        assert actual is db and p.workspace_id==WORKSPACE
        if state.inventory_error is not None:raise state.inventory_error
        return list(db.reviews.values()),list(db.commands)
    def get_review(actual,p,identity,lock=False):
        assert actual is db and type(lock) is bool
        if identity not in db.reviews:raise HTTPException(404,'Synthetic quality review missing')
        return db.reviews[identity]
    def evidence_context(actual,p,evaluation_id,shadow_id,*,lock=False):
        assert actual is db and lock is True
        state.context_reads+=1
        if state.source_error is not None:raise state.source_error
        assert (evaluation_id,shadow_id)==(EVALUATION,SHADOW)
        return {'evaluation':SimpleNamespace(id=EVALUATION,version=7),
            'shadow':SimpleNamespace(id=SHADOW,version=3),
            'context':{'synthetic_protocol_only':True}}
    def validate_refs(actual,p,draft,context,*,lock=False):
        assert actual is db and draft['label_sources']==[] and lock is True
        return []
    def seal(actual,p,row,operation,command_id):
        assert actual is db
        if operation!='create':row.version+=1
        row.record_hash=store.hash_value(store.snapshot(row));actual.add(row)
        actual.seals.append((row.id,operation,command_id))
    def freeze(actual,p,row,*,lock=True):
        assert actual is db and lock is True
        if state.source_error is not None:raise state.source_error
        return {'synthetic_protocol_only':True,'approved_by':p.user_id},[],{}
    def view(actual,p,row):
        assert actual is db;state.view_reads+=1
        return {'id':row.id,'version':row.version,'status':row.status,
            'machine_value':None if state.hidden else 19.0,
            'current_issue':'Synthetic current source unavailable' if state.hidden else None,
            'production_eligible':False}

    monkeypatch.setattr(routes,'lock_current_actor',current_actor)
    monkeypatch.setattr(store,'inventory',inventory)
    monkeypatch.setattr(store,'get_review',get_review)
    monkeypatch.setattr(store,'evidence_context',evidence_context)
    monkeypatch.setattr(store,'validate_refs',validate_refs)
    monkeypatch.setattr(store,'seal_review',seal)
    monkeypatch.setattr(store,'freeze_data',freeze)
    monkeypatch.setattr(store,'view',view)
    monkeypatch.setattr(store,'lock_nonce',lambda actual,p,nonce:db.nonce_locks.append((p.user_id,nonce)))
    app=FastAPI();app.include_router(routes.router,prefix='/api/local-rules')
    app.dependency_overrides[authenticated]=principal
    app.dependency_overrides[database.session]=lambda:db
    with TestClient(app,raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client,db=db,state=state)


def post(h,path,raw):
    return h.client.post(path,content=raw,headers={'content-type':'application/json'})


def lookup(h,raw,path=ROOT,request_id=REQUEST):
    return h.client.get(STATE_ROOT+request_id,params={'expected_sha256':sha(raw),
        'original_method':'POST','original_path':path})


def saved_command(h,path,raw):
    response=post(h,path,raw);assert response.status_code==(201 if path==ROOT else 200),response.text
    return response.json()


def clone_command(command,actor):
    """Explicitly synthetic inventory input, not a signed real SQL command."""
    return SimpleNamespace(actor_id=actor,request_id=command.request_id,method=command.method,
        path=command.path,wire_sha256=command.wire_sha256,outcome=command.outcome,
        target_id=command.target_id,data=deepcopy(command.data),operation=command.operation)


def test_create_keeps_original_utf8_wire_and_lost_reply_lookup_uses_original_saved_version(mounted):
    h=mounted;raw=wire(body());saved=saved_command(h,ROOT,raw)
    command=h.db.commands[0]
    assert command.data['wire_text'].encode('utf-8')==raw and command.wire_sha256==sha(raw)
    assert command.fingerprint==routes.command_fingerprint('POST',ROOT,WORKSPACE,ACTOR,sha(raw))
    assert len(h.db.audits)==1 and h.db.audits[0].data==store.command_pin(command)
    # Public-store double now has a later head: lookup must report the saved
    # command version while detail reads remain current.
    row=h.db.reviews[saved['id']];row.version=4;row.status='revoked';h.state.hidden=True
    before=(h.db.commits,len(h.db.commands),len(h.db.audits),h.state.context_reads)
    response=lookup(h,raw);assert response.status_code==200
    assert response.json()=={'schema':'threshold-quality-request-state-v152','status':'saved',
        'target_id':saved['id'],'version':1,'http_status':201,'detail':None}
    detail=h.client.get(ROOT+'/'+saved['id']);assert detail.status_code==200
    assert detail.json()['version']==4 and detail.json()['machine_value'] is None
    assert before==(h.db.commits,len(h.db.commands),len(h.db.audits),h.state.context_reads)


@pytest.mark.parametrize('change',['body','wire-only','path'])
def test_existing_nonce_rejects_changed_body_raw_bytes_or_operation_before_new_work(mounted,change):
    h=mounted;raw=wire(body());saved=saved_command(h,ROOT,raw)
    path=ROOT
    if change=='body':changed=body();changed['title']='另一份人稿';raw=wire(changed)
    elif change=='wire-only':raw=json.dumps(body(),ensure_ascii=False,separators=(',',':')).encode('utf-8')
    else:path=ROOT+'/'+saved['id']+'/approve'
    before=(h.db.commits,len(h.db.commands),len(h.db.audits),h.state.context_reads)
    response=post(h,path,raw);assert response.status_code==409
    assert before==(h.db.commits,len(h.db.commands),len(h.db.audits),h.state.context_reads)


def test_same_wire_replay_after_revoke_returns_current_safe_view_without_reapproval(mounted):
    h=mounted;saved=saved_command(h,ROOT,wire(body()));identity=saved['id']
    approved_raw=wire(control(1,'明确批准此合成协议稿'))
    approved=saved_command(h,ROOT+'/'+identity+'/approve',approved_raw)
    assert approved['status']=='approved' and approved['version']==2
    revoked=saved_command(h,ROOT+'/'+identity+'/revoke',wire(control(2)))
    assert revoked['status']=='revoked' and revoked['version']==3
    h.state.hidden=True;before=(h.db.commits,len(h.db.commands),len(h.db.audits),len(h.db.seals))
    response=post(h,ROOT+'/'+identity+'/approve',approved_raw)
    assert response.status_code==200
    assert response.json()=={'id':identity,'version':3,'status':'revoked','machine_value':None,
        'current_issue':'Synthetic current source unavailable','production_eligible':False}
    assert before==(h.db.commits,len(h.db.commands),len(h.db.audits),len(h.db.seals))
    original=json.loads(approved_raw)
    state=lookup(h,approved_raw,ROOT+'/'+identity+'/approve',original['request_id'])
    assert state.json()['version']==2


def test_owner_nonce_wins_over_another_admin_same_nonce_same_wire(mounted):
    h=mounted;raw=wire(body());saved=saved_command(h,ROOT,raw)
    h.db.commands.insert(0,clone_command(h.db.commands[0],PEER))
    response=lookup(h,raw);assert response.status_code==200
    assert response.json()['target_id']==saved['id'] and response.json()['version']==1
    replay=post(h,ROOT,raw);assert replay.status_code==201 and replay.json()['id']==saved['id']
    assert len(h.db.seals)==1 and h.db.commits==1


def test_admin_without_own_command_can_read_one_other_author_saved_request(mounted):
    h=mounted;raw=wire(body());saved=saved_command(h,ROOT,raw)
    h.db.commands[:]=[clone_command(h.db.commands[0],PEER)]
    response=lookup(h,raw);assert response.status_code==200
    assert response.json()['status']=='saved' and response.json()['target_id']==saved['id']


def test_admin_without_own_command_does_not_guess_between_two_other_authors(mounted):
    h=mounted;raw=wire(body());saved_command(h,ROOT,raw);original=h.db.commands[0]
    h.db.commands[:]=[clone_command(original,PEER),clone_command(original,OTHER)]
    response=lookup(h,raw);assert response.status_code==409
    assert h.db.commits==1 and len(h.db.audits)==1


def test_operator_does_not_discover_another_authors_nonce(mounted):
    h=mounted;raw=wire(body());saved_command(h,ROOT,raw)
    h.db.commands[:]=[clone_command(h.db.commands[0],PEER)];h.state.role='operator'
    response=lookup(h,raw);assert response.status_code==200
    assert response.json()=={'schema':'threshold-quality-request-state-v152','status':'unknown',
        'target_id':None,'version':None,'http_status':None,'detail':None}


def test_unknown_lookup_does_not_create_a_command_or_evidence(mounted):
    h=mounted;response=lookup(h,wire(body()));assert response.status_code==200
    assert response.json()['status']=='unknown'
    assert not h.db.commands and not h.db.audits and not h.db.commits and not h.state.context_reads


@pytest.mark.parametrize('kind',['source','foreign-inventory','source-http'])
def test_source_or_integrity_failure_is_not_permanently_signed_as_human_rejection(mounted,kind):
    h=mounted
    h.state.source_error=(store.QualitySourceError() if kind=='source' else store.QualityInventoryError()
        if kind=='foreign-inventory' else HTTPException(409,'Synthetic source receipt damaged'))
    raw=wire(body());response=post(h,ROOT,raw);assert response.status_code==409
    assert not h.db.commands and not h.db.audits and not h.db.reviews and not h.db.commits
    assert lookup(h,raw).json()['status']=='unknown'
    h.state.source_error=None
    assert post(h,ROOT,raw).status_code==201


@pytest.mark.parametrize('method',['get','post'])
def test_own_inventory_failure_never_becomes_unknown_or_a_new_rejection(mounted,method):
    h=mounted;h.state.inventory_error=store.QualityInventoryError()
    response=lookup(h,wire(body())) if method=='get' else post(h,ROOT,wire(body()))
    assert response.status_code==409
    assert not h.db.commands and not h.db.audits and not h.db.commits


@pytest.mark.parametrize('invalid',['extra-field','float-version','boolean-version'])
def test_valid_nonce_invalid_business_dto_is_saved_as_exact_rejected_request(mounted,invalid):
    h=mounted;original=body()
    if invalid=='extra-field':original['client_approved']=True
    elif invalid=='float-version':original['expected_evaluation_version']=7.0
    else:original['expected_shadow_version']=True
    raw=wire(original);first=post(h,ROOT,raw);assert first.status_code==422
    assert h.state.context_reads==0 and len(h.db.commands)==1 and h.db.commits==1
    command=h.db.commands[0];assert command.outcome=='rejected' and command.data['wire_text'].encode()==raw
    replay=post(h,ROOT,raw);assert replay.status_code==422 and replay.json()==first.json()
    response=lookup(h,raw);assert response.status_code==200
    assert response.json()['status']=='rejected' and response.json()['http_status']==422
    assert len(h.db.commands)==len(h.db.audits)==h.db.commits==1


@pytest.mark.parametrize('raw',[b'[]',b'{"request_id":"bad"}',b'{"request_id":1}',
    ('{"request_id":"'+REQUEST+'","request_id":"'+REQUEST+'"}').encode(),
    ('{"request_id":"'+REQUEST+'","value":NaN}').encode()])
def test_invalid_original_wire_cannot_allocate_a_recoverable_nonce(mounted,raw):
    h=mounted;response=post(h,ROOT,raw);assert response.status_code==422
    assert not h.db.commands and not h.db.audits and not h.db.commits and not h.db.nonce_locks


@pytest.mark.parametrize('role',['viewer','operator'])
def test_control_permissions_are_real_http_role_checks_not_fake_task_flags(mounted,role):
    h=mounted;saved=saved_command(h,ROOT,wire(body()));h.state.role=role
    before=(len(h.db.commands),h.db.commits)
    response=post(h,ROOT+'/'+saved['id']+'/approve',wire(control(1)))
    assert response.status_code==403 and before==(len(h.db.commands),h.db.commits)
    assert h.db.reviews[saved['id']].status=='draft'


def test_source_bad_revoke_still_preserves_original_approval_and_reason(mounted):
    h=mounted;saved=saved_command(h,ROOT,wire(body()));identity=saved['id']
    saved_command(h,ROOT+'/'+identity+'/approve',wire(control(1)))
    h.state.source_error=store.QualitySourceError();h.state.hidden=True
    raw=wire(control(2,'原件暂不可核，管理员明确撤回'))
    response=post(h,ROOT+'/'+identity+'/revoke',raw);assert response.status_code==200
    assert response.json()['status']=='revoked' and response.json()['machine_value'] is None
    row=h.db.reviews[identity]
    assert row.approved_by==ACTOR and row.approval_hash is not None
    assert h.db.commands[-1].data['wire_text'].encode()==raw and h.state.context_reads==1


def test_current_version_conflict_is_rejected_without_mutating_the_saved_head(mounted):
    h=mounted;saved=saved_command(h,ROOT,wire(body()));identity=saved['id'];raw=wire(control(99))
    response=post(h,ROOT+'/'+identity+'/reject',raw);assert response.status_code==409
    row=h.db.reviews[identity];assert row.version==1 and row.status=='draft' and len(h.db.seals)==1
    request_id=json.loads(raw)['request_id']
    state=lookup(h,raw,ROOT+'/'+identity+'/reject',request_id)
    assert state.json()['status']=='rejected' and state.json()['version'] is None


def test_lookup_requires_original_closed_method_path_and_sha(mounted):
    h=mounted;raw=wire(body());saved_command(h,ROOT,raw)
    response=h.client.get(STATE_ROOT+REQUEST,params={'expected_sha256':'A'*64,
        'original_method':'POST','original_path':ROOT});assert response.status_code==422
    response=h.client.get(STATE_ROOT+REQUEST,params={'expected_sha256':sha(raw),
        'original_method':'PUT','original_path':ROOT});assert response.status_code==422
    response=h.client.get(STATE_ROOT+REQUEST,params={'expected_sha256':'0'*64,
        'original_method':'POST','original_path':ROOT});assert response.status_code==409
    assert h.db.commits==1 and len(h.db.commands)==1
