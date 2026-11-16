"""Two real pure protocol families with public synthetic SQL/HTTP boundaries.

Actual preparation, wire, native, inventory and request functions are used.
SQL rows, current IAM/admission, paid settlement and HTTP response are explicit
dependency fixtures; temporary capture bytes are synthetic. These tests prove
neither real PG ownership/provider transport nor paid/semantic quality. Root
runs the independent API/PG/Worker journey, without replaying old calls.
"""
import asyncio
import copy
import hashlib
import json
import socket
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from generic_agents import report_saved_map_design_consumer_v176 as app
from generic_agents import report_saved_map_design_preparation_v178 as indexed_prep
from generic_agents import report_design_grouped_native_v178 as indexed_native
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.contracts import digest
from generic_agents.gateway import ModelBlocked
from generic_agents.leases import LeaseLost, ExecutionUnauthorized
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Membership, SetupReportDraft, User
from generic_agents.report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
from generic_agents.report_saved_map_design_models_v176 import ReportSavedMapDesignCommandV176 as Command
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests import test_report_saved_map_design_consumer_v176 as old

identity=old.identity


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Synthetic consumer tests cannot connect or launch a process')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket.socket,'connect_ex',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)


def scene(*,indexed=True):
    if not indexed:return old.scene()
    p,_,cap,source,_,_,_,_,_=old.scene()
    cap.data['execution']['report_evidence_output']={'schema':indexed_native.OUTPUT_DECLARATION,
        'max_tokens':32768,'reduce_transport':indexed_native.DELIVERY,'endpoint':indexed_native.ENDPOINT}
    cap.content_hash=digest(cap.data)
    nonce=identity(180)
    body=app.PreviewIndexedBody(producer_id=source['snapshot']['producer']['id'],revision=7,
        model_id=cap.id,note=' 原编号新方案，保留人工说明。 ',generation_profile='indexed-v178',activity_id=nonce)
    prep=indexed_prep.build_preparation(cap,source,note=body.note)
    budget={'schema':'report-saved-map-design-budget-v176','call_limit':1,
        'token_bound':prep['input_summary']['token_bound'],'maximum_cost_usd':3,
        'pricing_basis_hash':'b'*64,'grant_hash':'d'*64}
    preview={'schema':app.PREVIEW,'actor_id':p.user_id,'workspace_id':p.workspace_id,
        'body':body.model_dump(),'source_context':copy.deepcopy(source),'capability':app._cap_value(cap),
        'preparation':prep,'budget':budget,**app.FALSE_FLAGS,
        'generation_profile':'indexed-v178','activity_id':nonce}
    preview['preview_hash']=app.h(preview)
    start={**body.model_dump(),'request_id':nonce,'source_snapshot_hash':source['snapshot']['snapshot_hash'],
        'preview_hash':preview['preview_hash'],'consent':True}
    raw=json.dumps(start,ensure_ascii=False).encode('utf-8')
    _,exact,sha=app.read_wire(raw,app.StartIndexedBody)
    row=Consumer(id=identity(181),workspace_id=p.workspace_id,actor_id=p.user_id,request_id=nonce,
        producer_id=body.producer_id,model_id=body.model_id,grant_hash=budget['grant_hash'],revision=body.revision,
        version=1,status='queued',job_id=identity(182),reserved_call_id=None,data={
            'schema':app.STATE,'preview':preview,'dispatch_started':False,'dispatch_identity':None,
            'call_pin':None,'response':None,'projection':None,'manual_value':None,'error':None,
            'command_refs':[{'id':identity(183),'request_id':nonce,'operation':'start','wire_sha256':sha}]})
    command=Command(id=identity(183),workspace_id=p.workspace_id,actor_id=p.user_id,request_id=nonce,
        consumer_id=row.id,operation='start',method='POST',path=app.PREFIX+'/start-indexed',exact_body=exact,
        body_sha256=sha,data={'schema':app.COMMAND,'result':app.result(row,'start')})
    command.command_hash=app.h(app._command_pin(command))
    audits=[Audit(id=identity(184),workspace_id=p.workspace_id,actor_id=p.user_id,action=app.COMMAND_AUDIT,
        object_id=command.id,data=app._command_anchor(command)),
        Audit(id=identity(185),workspace_id=p.workspace_id,actor_id=p.user_id,action=app.STATE_AUDIT,
            object_id=row.id,data=app._state_anchor(row,None))]
    job=Job(id=row.job_id,workspace_id=p.workspace_id,kind=app.JOB,status='queued',version=1,
        idempotency_key='saved-map-design:'+nonce,data={'actor_id':p.user_id,'consumer_id':row.id},lease_token=identity(186))
    return p,body,cap,source,row,command,audits,job,raw


def io_scene(*,indexed=True):
    values=scene(indexed=indexed);p,_,_,_,row,command,audits,job,_=values
    return values,old.InventoryIO(p,[row],[command],audits,[job])


def call_for(p,row):
    prep=row.data['preview']['preparation'];family=app._family(prep);spec=prep['generation_contract']
    return CloudCall(id=identity(188),workspace_id=p.workspace_id,capability_id=row.model_id,status='reserved',
        reserved_tokens=prep['input_summary']['token_bound'],charged_tokens=prep['input_summary']['token_bound'],data={
            'actor_id':p.user_id,'request_hash':prep['request_hash'],'dispatch_options_hash':digest(spec['dispatch_options']),
            'delivery':family.native.DELIVERY,'request_endpoint':family.native.ENDPOINT,
            'request_body_bytes':prep['input_summary']['prepared_request_bytes'],'input_policy_hash':digest(spec['input_policy']),
            'schema_proof_hash':digest(spec['schema_proof']),'gateway_dispatch':{
                'schema':'cloud-dispatch-v130','identity':app.dispatch_identity(row),'attempt_id':identity(189),
                'reservation_request_hash':prep['request_hash']}})


def reserve(db,p,row,job):
    app._transition(db,p,row,{**row.data,'dispatch_started':True,'dispatch_identity':app.dispatch_identity(row)},'running')
    call=call_for(p,row);db.calls.append(call);app._associate(db,p,row,call)
    job.status='running';db.info['worker_lease']={'job_id':job.id,'lease_token':job.lease_token}
    return call


def paid_response(row,*,finish='stop',count_delta=0,drop_count=False):
    prep=row.data['preview']['preparation'];family=app._family(prep);source=row.data['preview']['source_context']
    claims=[copy.deepcopy(source['snapshot']['input_items'][key]) for key in prep['input_order']]
    result={'schema':'report-object-semantic-candidate-wire-v166','source_hash':prep['source_hash'],
        'catalog_hash':prep['catalog_hash'],'domain':'公共合成来源待核方案','global_claims':claims,
        'objects':[],'relationships':[],'businesses':[],'questions':[]}
    wire={'result':result,'reconciliation_groups':[{'output_kind':'global_claims','output_index':i,
        'disposition':'kept','input_positions':[i],'reason':'保留原声明及其引用，结构正确不代表真实语义质量。'}
        for i in range(len(claims))]}
    if family.id=='indexed-v178' and not drop_count:wire['input_count']=len(claims)+count_delta
    content=json.dumps(wire,ensure_ascii=False)
    body=json.dumps({'choices':[{'finish_reason':finish,'message':{'role':'assistant','content':content}}],
        'usage':{'prompt_tokens':9,'completion_tokens':12,'total_tokens':21}},ensure_ascii=False).encode('utf-8')
    inspected=family.native.inspect_response(body,200,family.native.STAGE,response_complete=True,http_content_encoding=None)
    receipt={**inspected['metadata'],'call_id':row.reserved_call_id,'provider':'qwen',
        'model':family.native.MODEL,'capture_hash':'a'*64,'usage':inspected['usage']}
    state={'response_complete':True,'ready':True,'content_encoding_safe':True,
        'body_sha256':hashlib.sha256(body).hexdigest(),'capture_hash':'a'*64,
        'http_status':200,'http_content_encoding':None}
    return {'body':body,'receipt':receipt,'capture':state,'descriptor':{'synthetic':True},'binding':app.journal_binding(row)}


def settlement_spy(monkeypatch,db,row):
    from generic_agents import cloud_budget
    calls=[]
    def settle(actual_db,call_id,receipt,expected,status):
        assert actual_db is db and call_id==row.reserved_call_id==receipt['call_id']
        assert expected['request_hash']==row.data['preview']['preparation']['request_hash']
        assert expected['workspace_id']==row.workspace_id and expected['actor_id']==row.actor_id
        assert expected['model']==indexed_native.MODEL
        calls.append(status)
    monkeypatch.setattr(cloud_budget,'settle_paid_response',settle)
    return calls


@pytest.mark.parametrize('fault',['missing_profile','missing_activity','wrong_profile','different_nonce','bool_revision','extra'])
def test_indexed_wire_is_closed_required_and_binds_the_activity_to_the_original_nonce(fault):
    *_,raw=scene();body=json.loads(raw)
    if fault=='missing_profile':body.pop('generation_profile')
    elif fault=='missing_activity':body.pop('activity_id')
    elif fault=='wrong_profile':body['generation_profile']='saved-v176'
    elif fault=='different_nonce':body['request_id']=identity(999)
    elif fault=='bool_revision':body['revision']=True
    else:body['budget_override']=0
    with pytest.raises(HTTPException) as caught:app.read_wire(json.dumps(body).encode(),app.StartIndexedBody)
    assert caught.value.status_code==422


def test_old_and_new_bodies_routes_and_nonce_replays_cannot_cross_families():
    old_values=scene(indexed=False);new_values=scene();p=new_values[0]
    for raw,model in [(new_values[-1],app.StartBody),(old_values[-1],app.StartIndexedBody)]:
        with pytest.raises(HTTPException):app.read_wire(raw,model)
    command=new_values[5]
    assert app._replay({command.id:command},p,command.request_id,'start',app.PREFIX+'/start-indexed',
        command.exact_body,command.body_sha256)==command.data['result']
    with pytest.raises(HTTPException):app._replay({command.id:command},p,command.request_id,'start',
        app.PREFIX+'/start',command.exact_body,command.body_sha256)
    assert command.data['result']['schema']==app.RESULT and command.data['result']['version']==1
    assert app.dispatch_identity(old_values[4])['task_id']=='saved-map-design-v176'
    assert app.dispatch_identity(new_values[4])['task_id']=='saved-map-design-v178'


def test_indexed_start_uses_original_table_fk_order_and_exact_nonce_replay_without_second_admission(monkeypatch):
    p,body,cap,source,row,_,_,_,raw=scene();authority=[]
    def admit(actual_db,principal,producer,revision,model,*,lock,consumer=None,note='',generation_profile,activity_id):
        assert principal is p and (producer,revision,model)==(body.producer_id,body.revision,body.model_id)
        assert lock is True and consumer is None and note==body.note
        assert generation_profile=='indexed-v178' and activity_id==body.activity_id
        authority.append('admit')
        return {'source_context':source,'capability':cap,'revision':revision,'budget':row.data['preview']['budget']}
    monkeypatch.setattr(app,'runtime',lambda:NS(admit=admit))
    monkeypatch.setattr(app,'lock_current_actor',lambda actual_db,principal:authority.append('current-actor'))
    async def request_body():return raw
    request=NS(method='POST',url=NS(path=app.PREFIX+'/start-indexed'),body=request_body)
    db=old.StartFlushIO(p,body.activity_id,row.grant_hash)
    result=asyncio.run(app.start_indexed_route(request,p,db))
    assert db.phases==[(Job,),(Consumer,),(Command,),(Audit,Audit)] and db.commits==1 and db.rollbacks==0
    assert result['schema']==app.RESULT and result['status']=='queued' and len(db.jobs)==1 and db.calls==[]
    assert db.commands[0].path==app.PREFIX+'/start-indexed' and db.commands[0].exact_body.encode('utf-8')==raw
    assert db.rows[0].data['preview']['activity_id']==body.activity_id
    before=(list(db.events),list(authority))
    assert asyncio.run(app.start_indexed_route(request,p,db))==result
    assert (db.events,authority)==before and len(db.jobs)==1 and len(db.commands)==1
    recovered=app.original_request(body.activity_id,p,db,expected_sha256=hashlib.sha256(raw).hexdigest())
    assert recovered['found'] is True and recovered['result']==result
    with pytest.raises(HTTPException) as caught:asyncio.run(app.start_route(request,p,db))
    assert caught.value.status_code==422 and db.commits==1


@pytest.mark.parametrize('indexed',[False,True])
def test_prepare_keeps_the_old_admit_call_or_explicit_new_activity_and_returns_original_outer_schema(indexed,monkeypatch):
    p,body,cap,source,row,*_=scene(indexed=indexed);seen=[]
    def admit(db,principal,producer,revision,model,*,lock,consumer=None,note='',**profile):
        assert principal is p and producer==body.producer_id and revision==body.revision and model==body.model_id
        assert lock is False and consumer is None and note==body.note
        seen.append(profile)
        return {'source_context':source,'capability':cap,'revision':body.revision,'budget':row.data['preview']['budget']}
    monkeypatch.setattr(app,'runtime',lambda:NS(admit=admit))
    actual=app.prepare(object(),p,body)
    assert actual==row.data['preview'] and app.check_preview(actual,p) is actual
    result=app.preview_view(actual)
    assert result['schema']==app.PREVIEW
    if indexed:
        assert seen==[{'generation_profile':'indexed-v178','activity_id':body.activity_id}]
        assert result['generation_profile']=='indexed-v178' and result['activity_id']==body.activity_id
    else:
        assert seen==[{}] and not {'generation_profile','activity_id'} & set(result)


def test_full_inventory_can_cold_read_both_families_together_without_current_source_or_admission(monkeypatch):
    old_case=scene(indexed=False);new_case=scene();p=new_case[0]
    db=old.InventoryIO(p,[old_case[4],new_case[4]],[old_case[5],new_case[5]],old_case[6]+new_case[6],[old_case[7],new_case[7]])
    def forbidden(*args,**kwargs):raise AssertionError('Historical inventory cannot renew source/permissions or dispatch')
    monkeypatch.setattr(app,'runtime',forbidden)
    records,commands,calls=app._inventory(db,p)
    assert set(records)=={old_case[4].id,new_case[4].id} and len(commands)==2 and calls=={}
    assert app.view(new_case[4])['activity_id']==new_case[1].activity_id
    assert not {'generation_profile','activity_id'} & set(app.view(old_case[4]))
    assert db.commits==0 and db.added==[]


@pytest.mark.parametrize('fault',['path','extra_legacy_field','missing_activity','call_old_delivery','call_old_task'])
def test_cold_inventory_rejects_mixed_new_path_body_or_actual_call_even_when_local_hashes_are_resealed(fault):
    values,db=io_scene();p,_,_,_,row,command,audits,job,_=values
    if fault.startswith('call_'):
        call=reserve(db,p,row,job)
        if fault=='call_old_delivery':call.data['delivery']=app.native.DELIVERY
        else:call.data['gateway_dispatch']['identity']['task_id']='saved-map-design-v176'
    elif fault=='path':
        command.path=app.PREFIX+'/start';command.command_hash=app.h(app._command_pin(command))
        audits[0].data=app._command_anchor(command)
    elif fault=='extra_legacy_field':
        row.data['preview']['body']['unknown_legacy_override']=True
    else:row.data['preview'].pop('activity_id')
    with pytest.raises(app.InventoryError):app._inventory(db,p)


@pytest.mark.parametrize('fault',['delivery','endpoint','schema_proof','task_id'])
def test_actual_indexed_call_requires_its_own_frozen_native_and_dispatch_pins(fault):
    p,_,_,_,row,*_=scene();call=call_for(p,row)
    assert app._check_call(row,call) is call
    if fault=='delivery':call.data['delivery']=app.native.DELIVERY
    elif fault=='endpoint':call.data['request_endpoint']+='?other=true'
    elif fault=='schema_proof':call.data['schema_proof_hash']='0'*64
    else:call.data['gateway_dispatch']['identity']['task_id']='saved-map-design-v176'
    with pytest.raises(app.InventoryError):app._check_call(row,call)


def test_paid_indexed_response_uses_actual_indexed_wire_and_full_original_claims_not_old_decoder(monkeypatch):
    values,db=io_scene();p,_,_,_,row,_,_,job,_=values;reserve(db,p,row,job)
    response=paid_response(row);settled=settlement_spy(monkeypatch,db,row)
    monkeypatch.setattr(app,'runtime',lambda:NS(read_response=lambda *args,**kwargs:response))
    data,status=app._read_paid(db,p,row,recover_saved=True)
    assert status=='review' and settled==['succeeded']
    assert data['projection']['schema']=='report-design-grouped-projection-v178'
    assert data['projection']['input_count']==4 and data['projection']['wire']['input_count']==4
    assert data['projection']['result']['global_claims']==[
        row.data['preview']['source_context']['snapshot']['input_items'][key]
        for key in row.data['preview']['preparation']['input_order']]
    assert data['manual_value']['schema']=='report-object-semantic-review-v166'
    assert data['response']['body_sha256']==hashlib.sha256(response['body']).hexdigest()


@pytest.mark.parametrize('fault',['length','wrong_count','old_wire'])
def test_invalid_complete_original_response_settles_that_call_failed_without_old_family_fallback(fault,monkeypatch):
    values,db=io_scene();p,_,_,_,row,_,_,job,_=values;reserve(db,p,row,job)
    response=paid_response(row,finish='length' if fault=='length' else 'stop',
        count_delta=1 if fault=='wrong_count' else 0,drop_count=fault=='old_wire')
    settled=settlement_spy(monkeypatch,db,row)
    monkeypatch.setattr(app,'runtime',lambda:NS(read_response=lambda *args,**kwargs:response))
    data,status=app._read_paid(db,p,row,recover_saved=True)
    assert status=='failed' and settled==['failed'] and data['projection'] is None and data['manual_value'] is None
    assert data['response']['body_sha256']==hashlib.sha256(response['body']).hexdigest()
    assert data['error'] and row.reserved_call_id==response['receipt']['call_id']


def test_worker_original_intent_reclaim_is_read_only_and_unknown_never_reserves_or_posts(monkeypatch):
    values,db=io_scene();p,_,_,_,row,_,_,job,_=values
    app._transition(db,p,row,{**row.data,'dispatch_started':True,'dispatch_identity':app.dispatch_identity(row)},'running')
    job.status='running';db.info['worker_lease']={'job_id':job.id,'lease_token':job.lease_token};read=[]
    def read_response(actual_db,principal,actual,*,recover_saved):
        assert actual_db is db and principal is p and actual.id==row.id and recover_saved is True
        read.append(row.id);return None
    def forbidden(*args,**kwargs):raise AssertionError('An original unknown is not another admission/reservation/POST')
    monkeypatch.setattr(app,'runtime',lambda:NS(read_response=read_response,admit=forbidden,dispatch=forbidden))
    result=app.run(db,p,job)
    assert result['status']=='response_unknown' and read==[row.id] and row.reserved_call_id is None
    assert result['generation_profile']=='indexed-v178' and db.commits==1


def test_first_dispatch_schema_failure_reads_and_settles_the_same_completed_capture(monkeypatch):
    values,db=io_scene();p,body,cap,source,row,_,_,job,_=values
    job.status='running';db.info['worker_lease']={'job_id':job.id,'lease_token':job.lease_token}
    dispatched=[];reads=[];response={};settled=settlement_spy(monkeypatch,db,row)
    def admit(actual_db,principal,producer,revision,model,*,lock,consumer=None,note='',generation_profile,activity_id):
        assert actual_db is db and principal is p and lock is True and note==body.note
        assert generation_profile=='indexed-v178' and activity_id==body.activity_id
        return {'source_context':source,'capability':cap,'revision':revision,'budget':row.data['preview']['budget']}
    def dispatch(actual_db,principal,actual_job,detached,*,request,spec,on_reserved):
        assert actual_db is db and principal is p and actual_job is job
        assert spec['evidence_stage']=='reduce-v178' and request['request_hash']==row.data['preview']['preparation']['request_hash']
        dispatched.append(detached.id);call=call_for(p,row);db.calls.append(call);on_reserved(call.id)
        response.update(paid_response(row,finish='length'))
        raise ModelBlocked('original completed envelope was length')
    def read_response(actual_db,principal,actual,*,recover_saved):
        assert actual_db is db and principal is p and actual.id==row.id and recover_saved is True
        reads.append(row.id);return response
    monkeypatch.setattr(app,'runtime',lambda:NS(admit=admit,dispatch=dispatch,read_response=read_response))
    monkeypatch.setattr(app,'checkpoint',lambda actual_db:None)
    result=app.run(db,p,job)
    assert result['status']=='failed' and dispatched==reads==[row.id] and settled==['failed']
    assert len(db.calls)==1 and result['reserved_call_id']==db.calls[0].id and db.commits==1


def test_failure_cleanup_can_finish_original_paid_failure_and_never_claims_a_new_call(monkeypatch):
    values,db=io_scene();p,_,_,_,row,_,_,job,_=values;reserve(db,p,row,job)
    response=paid_response(row,finish='length');settled=settlement_spy(monkeypatch,db,row)
    monkeypatch.setattr(app,'runtime',lambda:NS(read_response=lambda *args,**kwargs:response))
    app.record_job_failure(db,p,job)
    assert row.status=='failed' and settled==['failed'] and row.data['projection'] is None
    assert len(db.calls)==1 and db.commits==0  # caller still owns this failure transaction


@pytest.mark.parametrize('error',[LeaseLost('lost'),ExecutionUnauthorized('revoked'),
    OperationalError('SELECT',{},Exception('offline')),HTTPException(403,'revoked'),app.InventoryError()])
def test_failure_cleanup_propagates_lease_iam_database_and_inventory_failures(error,monkeypatch):
    values,db=io_scene();p,_,_,_,row,_,_,job,_=values;reserve(db,p,row,job)
    def fail(*args,**kwargs):raise error
    monkeypatch.setattr(app,'runtime',lambda:NS(read_response=fail))
    with pytest.raises(type(error)) as caught:app.record_job_failure(db,p,job)
    assert caught.value is error and row.status=='running' and db.commits==0


class ReadIO(old.InventoryIO):
    def __init__(self,p,rows,commands,audits,jobs,*,allowed=True,producer=True,at):
        super().__init__(p,rows,commands,audits,jobs);self.allowed=allowed;self.producer=producer;self.at=at;self.iam_reads=0
    def execute(self,statement,params=None):
        if hasattr(statement,'text'):return super().execute(statement,params)
        expected=select(User,Membership).join(Membership,Membership.user_id==User.id).where(
            User.id==self.p.user_id,User.active.is_(True),Membership.workspace_id==self.p.workspace_id,
            Membership.role==self.p.role,Membership.role.in_(['admin','operator','viewer']))
        if getattr(self.p,'session_id',None):
            expected=expected.add_columns(AuthSession).join(AuthSession,AuthSession.user_id==User.id).where(
                AuthSession.id==self.p.session_id,AuthSession.revoked.is_(False),AuthSession.expires_at>self.at)
        left,right=statement.compile(),expected.compile()
        assert str(left)==str(right) and set(left.params)==set(right.params)
        for key,actual in left.params.items():
            frozen=right.params[key]
            if isinstance(actual,datetime) or isinstance(frozen,datetime):
                assert type(actual) is type(frozen) is datetime
                assert actual.tzinfo is not None and frozen.tzinfo is not None
                assert actual.utcoffset()==frozen.utcoffset() and actual==frozen
            else:assert canonical_json(actual)==canonical_json(frozen)
        assert statement._for_update_arg is None
        self.iam_reads+=1;return NS(first=lambda:tuple() if self.allowed else None)
    def scalar(self,statement):
        producer_id=self.rows[0].producer_id
        expected=select(SetupReportDraft).where(SetupReportDraft.id==producer_id,
            SetupReportDraft.workspace_id==self.p.workspace_id,SetupReportDraft.actor_id==self.p.user_id)
        if old.matches_statement(statement,expected):
            old.equal_statement(statement,expected)
            return NS(id=producer_id,actor_id=self.p.user_id,workspace_id=self.p.workspace_id) if self.producer else None
        return super().scalar(statement)


def reader_scene(monkeypatch,*,allowed=True,producer=True):
    before=scene(indexed=False);current=scene();p=current[0];p.role='viewer';p.session_id=identity(190)
    at=datetime(2026,10,23,tzinfo=timezone.utc);monkeypatch.setattr(app,'now',lambda:at)
    db=ReadIO(p,[before[4],current[4]],[before[5],current[5]],before[6]+current[6],[before[7],current[7]],
        allowed=allowed,producer=producer,at=at)
    return current,db


def test_owner_viewer_list_keeps_both_families_and_all_inventory_checks_without_row_locks(monkeypatch):
    values,db=reader_scene(monkeypatch);p=values[0]
    result=app.list_route(values[4].producer_id,p,db)
    assert result['schema']=='report-saved-map-design-list-v178' and result['producer_id']==values[4].producer_id
    assert len(result['items'])==2 and result['items'][1]['generation_profile']=='indexed-v178'
    assert 'generation_profile' not in result['items'][0] and db.iam_reads==1 and db.added==[] and db.commits==0
    db.audits.pop()
    with pytest.raises(app.InventoryError):app.list_route(values[4].producer_id,p,db)


@pytest.mark.parametrize('fault',['iam','producer'])
def test_owner_list_never_exposes_another_producer_or_a_revoked_http_identity(fault,monkeypatch):
    values,db=reader_scene(monkeypatch,allowed=fault!='iam',producer=fault!='producer')
    with pytest.raises(HTTPException) as caught:app.list_route(values[4].producer_id,values[0],db)
    assert caught.value.status_code==(403 if fault=='iam' else 404) and db.locks==[] and db.commits==0


def capture_context(row):
    prep=row.data['preview']['preparation'];spec=prep['generation_contract'];policy=copy.deepcopy(spec['input_policy'])
    assert policy.pop('single_batch') is True
    return {'binding':app.journal_binding(row),'request_hash':prep['request_hash'],
        'endpoint':spec['endpoint'],'delivery':spec['delivery'],'schema_proof_hash':digest(spec['schema_proof']),
        'profile_hash':digest(policy),'token_bound':prep['input_summary']['token_bound'],'max_bytes':spec['max_response_bytes']}


@pytest.mark.parametrize('complete',[False,True])
def test_capture_summary_reads_real_synthetic_capture_bytes_without_raw_answer_or_provider_extensions(tmp_path,monkeypatch,complete):
    p,_,_,_,row,*_=scene();row.reserved_call_id=identity(188)
    root=tmp_path/'captures';monkeypatch.setattr(bridge,'root',lambda:root);ctx=capture_context(row)
    secret='PRIVATE_SYNTHETIC_ANSWER_NOT_A_PUBLIC_SUMMARY'
    body=json.dumps({'choices':[{'finish_reason':'length','message':{'role':'assistant','content':secret}}],
        'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5,'private_provider_extension':secret}}).encode()
    with capture.prepare(root,ctx) as writer:
        writer.mark_request_started();writer.begin_response(200,None,'synthetic-request');writer.append(body)
        if complete:writer.finish(eof=True)
    result=app._capture_summary(row)
    assert set(result)=={'schema','available','status','finish_reason','http_status','response_complete',
        'response_bytes','arguments_bytes','usage','problem'}
    assert result['available'] is True and result['response_complete'] is complete and result['response_bytes']==len(body)
    assert secret not in canonical_json(result) and 'private_provider_extension' not in canonical_json(result)
    if complete:
        assert result['status']=='complete' and result['finish_reason']=='length'
        assert result['usage']=={'prompt_tokens':2,'completion_tokens':3,'total_tokens':5} and result['problem']
    else:assert result['status']=='partial' and result['usage'] is None and result['problem']


def test_missing_capture_is_explicit_and_summary_get_still_requires_current_owner_inventory(tmp_path,monkeypatch):
    values,db=reader_scene(monkeypatch);p=values[0];row=values[4]
    monkeypatch.setattr(bridge,'root',lambda:tmp_path/'missing')
    result=app.capture_summary_route(row.id,p,db)
    assert result['available'] is False and result['status']=='missing' and db.iam_reads==1
    assert db.commits==0 and db.added==[]
    with pytest.raises(HTTPException) as caught:app.capture_summary_route(identity(999),p,db)
    assert caught.value.status_code==404
