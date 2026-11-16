"""Data-path integration with real definition proofs and in-memory SQL adapters."""
import copy
import importlib.util
import operator
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import models as m
from generic_agents import evidence_assessment as assessment, native_receipts as native
from generic_agents import state_refresh as refresh, workspace_needs as needs
from generic_agents import instance_domain, instance_api, entity_release_compat as compat
from generic_agents.contracts import digest, DataRequest

_spec=importlib.util.spec_from_file_location('_perception_definition_fixture',Path(__file__).with_name('test_entity_release_compat_pure.py'))
_fixture=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixture)


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail('perception data pure tests forbid SQL/network/services/models')
    monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket.socket,'connect_ex',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(subprocess,'run',forbidden)
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine,'connect',forbidden);monkeypatch.setattr(Engine,'begin',forbidden)
    monkeypatch.setattr(Session,'execute',forbidden);monkeypatch.setattr(Session,'commit',forbidden)


@pytest.fixture
def world(monkeypatch):
    w=_fixture.World(monkeypatch);db=w.db;original_scalars=db.scalars
    w.instance.data={'enabled_business_ids':['business']}
    for release in (w.r1,w.r2):release.data['snapshot']['businesses']=[{'id':'business','name':'资料核对'}];w.reseal(release)
    w.entity=m.Entity(**vars(w.entity),created_at=w.r1.created_at,observed_at=None,valid_until=None)
    db.rows[m.Entity][w.entity.id]=w.entity
    for model in (m.Task,m.VideoRun,m.VideoFrame,m.Observation,m.Event,m.Job,m.DataRun,m.DataGap):db.rows[model]={}
    w.events=[];w.saved=[]
    def scalars(query):
        model=query.column_descriptions[0]['entity']
        if model in {m.InstanceRelease,m.Audit}:return original_scalars(query)
        rows=list(db.rows[model].values())
        for condition in query._where_criteria:
            if getattr(condition,'operator',None)==operator.eq and getattr(condition.left,'key',None) in {'id','workspace_id','namespace','schema_capability_id','entity_id','run_id','kind','idempotency_key','status'}:
                value=condition.right.value;key=condition.left.key
                rows=[row for row in rows if getattr(row,key,None)==value]
        ordering=tuple(query._order_by_clauses)
        if rows and ordering:
            first=ordering[0];column=getattr(first,'element',first);key=getattr(column,'key',None)
            if key and hasattr(rows[0],key):rows.sort(key=lambda row:getattr(row,key),reverse=str(first).endswith(' DESC'))
        return rows[:query._limit_clause.value] if query._limit_clause is not None else rows
    def scalar(query):
        rows=scalars(query);return rows[0] if rows else None
    def execute(statement,*args):
        if getattr(getattr(statement,'table',None),'name',None)==m.Job.__tablename__:
            values={key:value for key,value in statement.compile().params.items() if value is not None}
            job=m.Job(**{'id':'refresh-job','version':1,'status':'queued','created_at':w.r2.created_at,**values})
            db.rows[m.Job][job.id]=job;w.saved.append(job)
        return NS()
    def add(row):
        row.id=row.id or 'new-'+type(row).__name__+'-'+str(len(w.saved));row.version=row.version or 1;row.created_at=row.created_at or w.r2.created_at
        db.rows.setdefault(type(row),{})[row.id]=row;w.saved.append(row)
    db.scalars=scalars;db.scalar=scalar;db.execute=execute;db.add=add;db.flush=lambda:None;db.info={}
    def active(db_arg,p,identity,lock=False):
        instance=db.scoped(db_arg,m.DomainInstance,identity,p)
        if instance.status!='partially_active' or not instance.active_release_id:raise HTTPException(409,'实例未启用')
        release=db.scoped(db_arg,m.InstanceRelease,instance.active_release_id,p)
        db.published(db_arg,p,release.package_id,'domain_package')
        return instance,release
    for module in (refresh,native,assessment,needs,instance_domain):monkeypatch.setattr(module,'scoped',db.scoped)
    for module in (native,assessment,needs,instance_api):monkeypatch.setattr(module,'active',active)
    monkeypatch.setattr(refresh,'lock',lambda *a:None);monkeypatch.setattr(refresh,'audit',lambda *a:None)
    monkeypatch.setattr(needs,'audit',lambda *a:None);monkeypatch.setattr(needs,'queue',lambda *a:None)
    from generic_agents import services,data_requirements,learning
    monkeypatch.setattr(services,'scoped',db.scoped);monkeypatch.setattr(data_requirements,'scoped',db.scoped)
    def memory_forbidden(*a,**k):pytest.fail('cross-release need must not read old Memory')
    monkeypatch.setattr(learning,'eligible',memory_forbidden);monkeypatch.setattr(learning,'select_cases',memory_forbidden)
    w.original=copy.deepcopy({'schema':w.entity.schema_capability_id,'version':w.entity.version,'data':w.entity.data})
    return w


def test_assessment_accepts_original_id_under_real_unchanged_R2_definition(world):
    instance,release,entities=assessment.scoped_objects(world.db,world.p,world.instance.id,[world.entity.id])
    assert instance is world.instance and release is world.r2 and entities==[world.entity]
    assert {'schema':world.entity.schema_capability_id,'version':world.entity.version,'data':world.entity.data}==world.original


@pytest.mark.parametrize('change',['unit','source-retired','cross-instance'])
def test_new_assessment_still_rejects_definition_permission_or_instance_changes(world,change):
    if change=='unit':
        kind=world.r2.data['snapshot']['object_types'][0];kind['fields'][1]['unit']='公顷'
        world.r2.data['snapshot']['entity_schemas'][kind['key']]=_fixture.schema_for(kind);world.reseal(world.r2)
    elif change=='source-retired':world.db.rows[m.Capability][world.r1.package_id].status='retired'
    else:world.r1.instance_id='different-instance'
    with pytest.raises(HTTPException):assessment.scoped_objects(world.db,world.p,world.instance.id,[world.entity.id])


def test_state_refresh_freezes_current_R2_and_only_definition_hash(world):
    job=refresh.enqueue(world.db,world.p,world.entity,'仅核对已存登记','original-trigger')
    assert job is not None
    request=refresh.checked(job);proof=compat.require_entity(world.db,world.p,world.instance,world.r2,world.entity)
    assert request['release_id']==world.r2.id and request['entity_id']==world.entity.id
    assert request['entity_version']==world.original['version'] and request['object_definition_hash']==digest(proof)
    assert 'source_package_id' not in repr(job.data) and 'definition' not in request
    assert world.entity.schema_capability_id==world.original['schema']


def state_job(world,*,legacy=False,release=None):
    release=release or world.r2
    request={'schema':refresh.SCHEMA,'entity_id':world.entity.id,'entity_version':world.entity.version,
      'instance_id':world.instance.id,'release_id':release.id,'authorized_by':world.p.user_id,'reason':'合成状态核对',
      'trigger_observation_id':None,'trigger_observation_version':None,'due_state_hash':None}
    if not legacy:request['object_definition_hash']=digest(compat.require_entity(world.db,world.p,world.instance,release,world.entity))
    return m.Job(id='job',workspace_id=world.p.workspace_id,version=1,status='running',data={
      'actor_id':world.p.user_id,'state_request':request,'state_request_hash':digest(request)})


def test_state_job_rechecks_R2_without_rewriting_original_entity(world,monkeypatch):
    from generic_agents import perception
    job=state_job(world);checks=[]
    def object_state(db,p,identity):
        checks.append(identity);return {'state_hash':'s'*64,'state_version':2,'status':'unknown',
          'checked_at':datetime.now(timezone.utc).isoformat(),'fields':{}}
    monkeypatch.setattr(perception,'object_state',object_state)
    refresh.process(world.db,world.p,job)
    assert checks==[world.entity.id] and refresh.checked(job)['release_id']==world.r2.id
    assert job.data['state_result']['entity_updated'] is False and job.data['state_result']['device_actions']==0
    assert world.entity.schema_capability_id==world.original['schema']


@pytest.mark.parametrize('change',['old-job','source-retired','definition-hash','entity-version'])
def test_state_job_never_rebases_inflight_or_stale_authority(world,monkeypatch,change):
    from generic_agents import perception
    job=state_job(world,legacy=change=='old-job',release=world.r1 if change=='old-job' else world.r2)
    if change=='source-retired':world.db.rows[m.Capability][world.r1.package_id].status='retired'
    elif change=='definition-hash':
        job.data['state_request']['object_definition_hash']='0'*64
        job.data['state_request_hash']=digest(job.data['state_request'])
    elif change=='entity-version':world.entity.version+=1
    monkeypatch.setattr(perception,'object_state',lambda *a:pytest.fail('stale job cannot reconcile state'))
    with pytest.raises(HTTPException):refresh.process(world.db,world.p,job)
    assert 'state_result' not in job.data


def test_legacy_state_job_keeps_saved_format_when_its_original_release_is_current(world,monkeypatch):
    from generic_agents import perception
    world.instance.active_release_id=world.r1.id;job=state_job(world,legacy=True,release=world.r1)
    before=copy.deepcopy(job.data['state_request'])
    monkeypatch.setattr(perception,'object_state',lambda *a:{'state_hash':'s'*64,'state_version':1,
      'status':'unknown','checked_at':datetime.now(timezone.utc).isoformat(),'fields':{}})
    refresh.process(world.db,world.p,job)
    assert job.data['state_request']==before and 'object_definition_hash' not in job.data['state_request']


def test_generic_noninstance_entity_does_not_gain_a_state_job(world):
    world.entity.schema_capability_id='generic-unmanaged-package'
    assert refresh.enqueue(world.db,world.p,world.entity,'仅核对','generic-trigger') is None
    assert not world.saved


def test_default_native_candidates_include_compatible_original_objects(world,monkeypatch):
    selected=[]
    def collect(db,p,request,**kwargs):selected.extend(request['entity_ids']);return [],[],False
    monkeypatch.setattr(native,'collect',collect)
    native.candidates(world.db,world.p,world.instance.id)
    assert selected==[world.entity.id]


def historical_native(world,monkeypatch):
    ev=NS(id='video-evidence',sha256='a'*64,created_at=world.r1.created_at)
    video=m.VideoRun(id='video-run',workspace_id=world.p.workspace_id,version=1,status='succeeded',
      evidence_id=ev.id,created_at=world.r1.created_at,data={'name':'合成历史视频','input_sha256':ev.sha256,
      'source_context':{'domain_binding':{'instance_id':world.instance.id,'entity_id':world.entity.id}},
      'result':{},'result_hash':digest({}),'configuration':{'recorded_at':None}})
    world.db.rows[m.VideoRun][video.id]=video
    from generic_agents import evidence
    monkeypatch.setattr(evidence,'evidence_bytes',lambda *a:(ev,b''));monkeypatch.setattr(native,'evidence_bytes',lambda *a:(ev,b''))
    record={'key':'VideoRun:'+video.id,'integrity_verified':True,'entity_ids':[world.entity.id],
      'metrics':{'saved_frames':0},'task_id':None,'modality':'video','kind':'video','source_times':[{'value':None,'basis':'unknown'}],
      'source_sha256':[ev.sha256],'name':'合成历史视频','review_state':'unreviewed'}
    value=native.packet(world.db,world.p,record,world.entity.id)
    source=m.Capability(id='native-source',workspace_id=world.p.workspace_id,namespace='platform_native_receipts',kind='source',
      status='published',version=1,data={'specification':{'managed_instance':world.instance.id}})
    source.content_hash=digest(source.data);source.validated_hash=source.content_hash;world.db.rows[m.Capability][source.id]=source
    row=m.Observation(id='saved-native',workspace_id=world.p.workspace_id,version=1,source_id=source.id,
      source_record_id=value['source_key']+':'+world.entity.id,processing_version=digest(value)[:40],entity_id=world.entity.id,
      observed_at=None,business_day=value['business_day'],status='processed',created_at=world.r1.created_at,
      data={'native_packet':value,'native_packet_hash':digest(value),'candidate_only':True,'native_instance_id':world.instance.id,
      'source_hash':digest(source.data),'extracted':{'attributes':value['attributes']},'evidence_ids':value['evidence_ids']})
    return row,video,ev,source


def test_historical_native_machine_values_survive_active_release_change_and_pause(world,monkeypatch):
    row,_,_,_=historical_native(world,monkeypatch);world.instance.status='paused'
    result=native.view(world.db,world.p,row)
    assert result['integrity_error'] is None and result['record']['attributes']=={'已保存画面数':0}
    assert result['record']['source_machine_hash']==row.data['native_packet']['source_machine_hash']


def test_history_retains_original_values_when_current_definition_changes_but_blocks_new_sync(world,monkeypatch):
    row,_,_,_=historical_native(world,monkeypatch)
    kind=world.r2.data['snapshot']['object_types'][0];kind['fields'][1]['unit']='公顷'
    world.r2.data['snapshot']['entity_schemas'][kind['key']]=_fixture.schema_for(kind);world.reseal(world.r2)
    assert native.view(world.db,world.p,row)['integrity_error'] is None
    with pytest.raises(HTTPException):native.candidates(world.db,world.p,world.instance.id,[world.entity.id])


def test_compatible_R2_sync_deduplicates_original_machine_receipt_without_rewriting_it(world,monkeypatch):
    row,_,_,source=historical_native(world,monkeypatch);world.db.rows[m.Observation][row.id]=row
    before=copy.deepcopy(row.data);reconciled=[]
    monkeypatch.setattr(native,'source',lambda *a:source)
    monkeypatch.setattr(native,'reconcile',lambda db,p,identity:reconciled.append(identity))
    result=native.sync(world.db,world.p,world.instance.id,[world.entity.id])
    assert result['created']==0 and result['already_saved']==1 and result['observation_ids']==[row.id]
    assert row.data==before and reconciled==[world.instance.id]


def test_historical_video_receipt_beyond_new_scan_page_uses_exact_source_only(world,monkeypatch):
    row,video,_,_=historical_native(world,monkeypatch)
    for index in range(assessment.MAX_SOURCES+2):
        other=m.VideoRun(id='new-video-'+str(index),workspace_id=world.p.workspace_id,version=1,status='succeeded',
          evidence_id=video.evidence_id,created_at=world.r2.created_at,data=copy.deepcopy(video.data))
        world.db.rows[m.VideoRun][other.id]=other
    records,truncated=native.candidates(world.db,world.p,world.instance.id,[world.entity.id])
    assert truncated and len(records)==assessment.MAX_SOURCES and all(r['key']!=row.data['native_packet']['source_key'] for r in records)
    result=native.view(world.db,world.p,row)
    assert result['integrity_error'] is None and result['record']['source_key']=='VideoRun:'+video.id


def test_historical_nonvideo_selects_original_task_before_the_task_page_limit(world,monkeypatch):
    original=m.Task(id='original-task',workspace_id=world.p.workspace_id,version=1,status='workflow_waiting',
      title='原影像核对',package_id=world.r1.package_id,created_at=world.r1.created_at,
      data={'instance_id':world.instance.id,'entity_ids':[world.entity.id]})
    world.db.rows[m.Task][original.id]=original
    for index in range(assessment.MAX_TASKS+2):
        task=m.Task(id='new-task-'+str(index),workspace_id=world.p.workspace_id,version=1,status='workflow_waiting',
          title='其它核对',package_id=world.r2.package_id,created_at=world.r2.created_at,
          data={'instance_id':world.instance.id,'entity_ids':[world.entity.id]})
        world.db.rows[m.Task][task.id]=task
    source=m.GeoRun(id='original-geo',workspace_id=world.p.workspace_id,version=1,status='succeeded',
      created_at=world.r1.created_at,data={'workflow_task_id':original.id})
    world.db.rows[m.GeoRun]={source.id:source}
    from generic_agents import geo_daily
    monkeypatch.setattr(geo_daily,'record',lambda *a,**k:{'sources':[{'observed_at':None,'time_basis':'unknown','sha256':'a'*64}],
      'candidate_masks':1,'candidate_pixels':None,'unknown_pixels':None,'integrity_verified':True,
      'integrity_issue':None,'review_state':'unreviewed'})
    request={'instance_id':world.instance.id,'release_id':world.r1.id,'entity_ids':[world.entity.id],
      'object_refs':[assessment.ref(world.entity)],'required_modalities':['imagery']}
    records,_,cut=assessment.collect(world.db,world.p,request,historical=True,source_key='GeoRun:'+source.id)
    assert not cut and len(records)==1 and records[0]['task_id']==original.id


def test_exact_source_selector_does_not_grant_new_operation_or_cross_object_history(world,monkeypatch):
    row,video,_,_=historical_native(world,monkeypatch)
    request={'instance_id':world.instance.id,'release_id':world.r2.id,'entity_ids':[world.entity.id],
      'object_refs':[assessment.ref(world.entity)],'required_modalities':['video']}
    with pytest.raises(HTTPException,match='历史核对'):
        assessment.collect(world.db,world.p,request,source_key='VideoRun:'+video.id)
    video.data['source_context']['domain_binding']['entity_id']='another-object'
    result=native.view(world.db,world.p,row)
    assert result['integrity_error'] and result['record'] is None


@pytest.mark.parametrize('change',['original-tamper','ledger-retired','entity-source-retired'])
def test_history_does_not_call_unavailable_or_corrupt_source_intact(world,monkeypatch,change):
    row,_,evidence,source=historical_native(world,monkeypatch)
    if change=='original-tamper':evidence.sha256='b'*64
    elif change=='ledger-retired':source.status='retired'
    else:world.db.rows[m.Capability][world.r1.package_id].status='retired'
    result=native.view(world.db,world.p,row)
    assert result['integrity_error'] and result['record'] is None


def test_workspace_need_catalog_includes_compatible_original_entity(world):
    _,release,entities,_=needs.objects(world.db,world.p,world.instance.id)
    assert release is world.r2 and entities==[world.entity]


def create_need(world):
    field=_fixture.identifier('field','数量')
    body=needs.NeedCreate(request_id='synthetic-need-request',release_id=world.r2.id,business_id='business',
      entity_id=world.entity.id,purpose='核对已保存档案字段',fields=[field],source='archive')
    return needs.create(world.db,world.p,world.instance.id,body)


def test_workspace_need_runs_actual_structured_agent_without_reading_old_memory(world):
    row=create_need(world);c=needs.context(row)
    assert c['release_id']==world.r2.id and c['entity_id']==world.entity.id
    assert c['object_definition_hash']==digest(compat.require_entity(world.db,world.p,world.instance,world.r2,world.entity))
    assert row.data['result']['trusted_cases']==[] and row.status=='sufficient'
    assert world.entity.schema_capability_id==world.original['schema']


def test_need_guard_rechecks_new_proof_and_does_not_rebase_old_context(world):
    row=create_need(world);assert needs.guard(world.db,world.p,row)[2] is world.entity
    value=row.data['workspace_need_context'];value['object_definition_hash']='0'*64
    row.data['workspace_need_context_hash']=digest(value)
    with pytest.raises(HTTPException):needs.guard(world.db,world.p,row)


def test_gap_evaluation_uses_actual_agent_and_query_memory_optout_for_cross_release_need(world,monkeypatch):
    from generic_agents import data_gaps
    row=create_need(world);request=copy.deepcopy(row.data['request'])
    gap=m.DataGap(id='gap',workspace_id=world.p.workspace_id,version=1,query_id=row.id,entity_id=world.entity.id,
      status='received',data={'request':request})
    marker={'query_id':row.id,'context_hash':digest(needs.context(row)),'gap_request_hash':digest(request)}
    gap.data.update(workspace_existing_check=marker,workspace_existing_check_hash=digest(marker))
    world.db.rows[m.DataGap][gap.id]=gap;monkeypatch.setattr(data_gaps,'scoped',world.db.scoped)
    state=data_gaps.evaluate(world.db,world.p,gap,'fresh-gap-query',existing_only=True)
    assert state['status']=='sufficient' and state['result']['trusted_cases']==[]


def test_actual_gap_recheck_cannot_read_old_memory_and_persists_its_own_child_result(world,monkeypatch):
    from generic_agents import data_gaps
    row=create_need(world);request=copy.deepcopy(row.data['request'])
    gap=m.DataGap(id='gap',workspace_id=world.p.workspace_id,version=1,query_id=row.id,entity_id=world.entity.id,
      status='received',data={'request':request})
    marker={'query_id':row.id,'context_hash':digest(needs.context(row)),'gap_request_hash':digest(request)}
    gap.data.update(workspace_existing_check=marker,workspace_existing_check_hash=digest(marker))
    world.db.rows[m.DataGap][gap.id]=gap;monkeypatch.setattr(data_gaps,'scoped',world.db.scoped)
    assert data_gaps.recheck(world.db,world.p,gap.id,'pure-existing-check',existing_only=True) is gap
    assert gap.status=='resolved'
    child=world.db.rows[m.DataRun][gap.data['latest_recheck_query_id']]
    assert child.id!=row.id and child.data['result']['trusted_cases']==[] and child.status=='sufficient'


def test_need_process_and_detail_use_real_structured_query_with_old_memory_disabled(world):
    row=create_need(world);c=needs.context(row)
    job=m.Job(data={'query_id':row.id,'context_hash':digest(c),'existing_records_only':True})
    needs.process(world.db,world.p,job)
    assert job.data['need_result']['result_status']=='sufficient'
    result=needs.view(world.db,world.p,row,detail=True)
    assert result['scope_valid'] and result['archive_evidence'][0]['version']==world.entity.version


def test_generic_gap_keeps_legacy_agent_keyword_shape_without_extra_scope_reads(world,monkeypatch):
    from generic_agents import data_gaps,agents
    calls=[]
    def data_agent(db,p,request,query_id,**kwargs):calls.append(kwargs);return {'status':'sufficient'}
    monkeypatch.setattr(agents,'data_agent',data_agent)
    gap=NS(data={'request':{'mode':'structured'}},query_id='generic')
    data_gaps.evaluate(world.db,world.p,gap,'generic-query',existing_only=True)
    assert calls==[{'cached_only':True,'emit_gaps':False,'learning':False}]


def test_new_native_sync_remains_blocked_when_instance_is_paused(world):
    world.instance.status='paused'
    with pytest.raises(HTTPException):native.candidates(world.db,world.p,world.instance.id,[world.entity.id])
