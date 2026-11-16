"""Isolated real-PG routing vertical slice; root supplies an owned empty database."""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import DomainInstance, PerceptionSignal, Task, Observation, Job, Membership, CloudCall
from generic_agents.worker import process_once
from generic_agents import signal_routing as routing
from tests.test_perception_signals import setup, signals


def endpoint(release):
    return '/api/workspace/instances/'+release['instance_id']+'/signal-routing'


def enable_route(client, max_tasks=10):
    release,obj,old,body,oldjob=setup(client,run=False)
    base=endpoint(release); opt=client.get(base+'/options')
    assert opt.status_code==200,opt.text
    options=opt.json()
    command=dict(request_id=str(uuid4()),expected_version=options['instance_version'],options_hash=options['options_hash'],
        release_id=release['id'],source_id=body['source_id'],extractor_id=body['extractor_id'],entity_ids=[obj['id']],
        business_id=release['available_business_ids'][0],max_tasks=max_tasks,reason='仅核查后续登记候选，不授权设备或事实写入',confirm_human_review_only=True)
    reply=client.post(base+'/enable',json=command)
    assert reply.status_code==201,reply.text
    return release,obj,body,oldjob,command,reply.json()


def new_signal(client, release, body, *, record=None, processing_version=None):
    value={**body,'source_record_id':record or str(uuid4())}
    if processing_version is not None:value['processing_version']=processing_version
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/reported-observations',json=value)
    assert response.status_code==201,response.text
    observation=response.json()
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['observation_id'].as_string()==observation['id']))
        jobid=job.id
    assert process_once(jobid,kinds=['process_reported_observation'],deliver=False)
    row=next(x for x in signals(client,release) if x['basis'] and x['basis']['observation_id']==observation['id'])
    return observation,row


def route_once(workspace, signal):
    with Session(engine()) as db:
        routing.dispatch(db,workspace,signal['id']);db.commit()


def row_signal(identity):
    with Session(engine()) as db:
        row=db.get(PerceptionSignal,identity)
        return copy.deepcopy(row.data),row.version,row.workspace_id


def tasks_for(release):
    with Session(engine()) as db:
        return list(db.scalars(select(Task.id).where(Task.data['instance_id'].as_string()==release['instance_id'])))


def test_new_registered_signal_creates_real_human_task_and_recovers_original_receipts(client):
    release,obj,body,oldjob,command,enabled=enable_route(client)
    observation,signal=new_signal(client,release,body)
    data,_,workspace=row_signal(signal['id']);assert data['route_intent']['observation_id']==observation['id']
    route_once(workspace,signal)
    current=next(x for x in signals(client,release) if x['id']==signal['id'])
    receipt=current['route_receipt'];assert receipt['status']=='created'
    task=client.get('/api/workspace/tasks/'+receipt['task_id'])
    assert task.status_code==200,task.text
    assert task.json()['status']=='workflow_waiting' and task.json()['auto_routing']['intent_hash']==data['route_intent']['intent_hash']
    assert receipt['source_time_verified'] is False and receipt['device_actions'] is False and receipt['cloud_calls']==0
    ids=tasks_for(release);route_once(workspace,signal);assert tasks_for(release)==ids
    restored=client.get(endpoint(release)+'/requests/'+command['request_id']).json()
    assert restored['receipt']==enabled['receipt'] and restored['current_matches'] is False
    assert client.post(endpoint(release)+'/enable',json=command).json()['receipt']==enabled['receipt']
    with Session(engine()) as db:assert not list(db.scalars(select(CloudCall.id)))


def test_authorization_does_not_adopt_registration_created_before_enable(client):
    release,obj,body,oldjob,_,_=enable_route(client)
    assert process_once(oldjob,kinds=['process_reported_observation'],deliver=False)
    row=signals(client,release)[0]
    assert row['route_intent'] is None and row['route_receipt'] is None and not tasks_for(release)


def test_total_budget_is_atomic_and_exhausted_notification_never_retries(client):
    release,obj,body,_,_,_=enable_route(client,max_tasks=1)
    first=new_signal(client,release,body)[1];second=new_signal(client,release,body)[1]
    workspace=row_signal(first['id'])[2]
    route_once(workspace,first);route_once(workspace,second)
    rows={x['id']:x for x in signals(client,release)}
    assert rows[first['id']]['route_receipt']['status']=='created'
    assert rows[second['id']]['route_receipt']['status']=='blocked'
    route_once(workspace,second);assert len(tasks_for(release))==1


def test_paused_intent_is_terminal_and_cancelled_task_never_recreated(client):
    release,obj,body,_,_,enabled=enable_route(client)
    _,first=new_signal(client,release,body);workspace=row_signal(first['id'])[2]
    route_once(workspace,first)
    taskid=row_signal(first['id'])[0]['route_receipt']['task_id']
    task=client.get('/api/workspace/tasks/'+taskid).json()
    response=client.post('/api/workspace/tasks/'+taskid+'/cancel',json={'version':task['version'],'reason':'停止核查，保留原交接','request_id':str(uuid4())})
    assert response.status_code==200,response.text
    route_once(workspace,first);assert tasks_for(release)==[taskid]
    _,pending=new_signal(client,release,body)
    listing=client.get(endpoint(release)).json();route=listing['routes'][0]
    pause=dict(request_id=str(uuid4()),expected_version=listing['instance_version'],reason='明确暂停，不重放')
    reply=client.post(endpoint(release)+'/'+route['id']+'/pause',json=pause)
    assert reply.status_code==200,reply.text
    route_once(workspace,pending)
    assert row_signal(pending['id'])[0]['route_receipt']['status']=='blocked' and tasks_for(release)==[taskid]


def test_current_role_revocation_blocks_pending_intent_without_creating_task(client):
    release,obj,body,_,_,enabled=enable_route(client)
    _,signal=new_signal(client,release,body);workspace=row_signal(signal['id'])[2]
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==workspace,Membership.user_id==enabled['routes'][0]['actor_id']))
        membership.role='viewer';db.commit()
    route_once(workspace,signal)
    assert row_signal(signal['id'])[0]['route_receipt']['status']=='blocked' and not tasks_for(release)


def test_corrupt_evidence_blocks_new_task_and_corrupt_intent_is_quarantined(client):
    release,obj,body,_,_,_=enable_route(client)
    observation,signal=new_signal(client,release,body);workspace=row_signal(signal['id'])[2]
    with Session(engine()) as db:
        row=db.get(Observation,observation['id']);row.data={**row.data,'input_hash':'0'*64};db.commit()
    route_once(workspace,signal)
    assert row_signal(signal['id'])[0]['route_receipt']['status']=='blocked' and not tasks_for(release)
    _,second=new_signal(client,release,body)
    with Session(engine()) as db:
        row=db.get(PerceptionSignal,second['id']);row.data={**row.data,'route_intent':{'schema':routing.INTENT,'intent_hash':'a'*64}};db.commit()
    route_once(workspace,second)
    data,version,_=row_signal(second['id']);assert data['route_failure']['code']=='routing_integrity' and 'route_receipt' not in data
    route_once(workspace,second);assert row_signal(second['id'])[1]==version
    _,third=new_signal(client,release,body);route_once(workspace,third)
    assert row_signal(third['id'])[0]['route_receipt']['status']=='created'


def test_nested_capacity_failure_rolls_back_task_attachment_budget_and_no_ghost_id(client,monkeypatch):
    release,obj,body,_,_,_=enable_route(client)
    _,signal=new_signal(client,release,body);original,_,workspace=row_signal(signal['id'])
    def overflow(*args):routing.fail('routing_capacity')
    monkeypatch.setattr(routing,'save_state',overflow)
    route_once(workspace,signal)
    data,_,_=row_signal(signal['id'])
    assert data['route_receipt']['status']=='blocked' and data['route_receipt']['task_id'] is None
    assert data['task_ids']==original['task_ids']==[] and not tasks_for(release)
    with Session(engine()) as db:
        instance=db.get(DomainInstance,release['instance_id']);assert instance.data['signal_routes']['routes'][0]['task_ids']==[]


def test_same_nonce_changed_body_or_actor_conflicts_and_stale_options_rejected(client):
    release,obj,body,_,command,enabled=enable_route(client)
    changed={**command,'reason':'different explicit authorization'}
    assert client.post(endpoint(release)+'/enable',json=changed).status_code==409
    with Session(engine()) as db:
        instance=db.get(DomainInstance,release['instance_id']);state=routing.state(instance)
        state['routes'][0]['paused_at']=routing.now().isoformat();routing.save_state(instance,state);db.commit()
    options=client.get(endpoint(release)+'/options').json()
    stale={**command,'request_id':str(uuid4()),'expected_version':options['instance_version'],'options_hash':command['options_hash']}
    assert client.post(endpoint(release)+'/enable',json=stale).status_code==409


def test_reprocessing_old_source_record_after_enable_is_not_a_new_registration(client):
    release,obj,body,oldjob,_,_=enable_route(client)
    _,signal=new_signal(client,release,body,record=body['source_record_id'],processing_version='routing-reprocessed-v2')
    data,_,_=row_signal(signal['id'])
    assert data.get('route_intent') is None and not tasks_for(release)


def test_duplicate_source_notifications_beyond_one_hundred_and_cancelled_task_are_not_recreated(client):
    from generic_agents.models import Event
    from generic_agents.perception_signals import receive
    release,obj,body,_,_,_=enable_route(client)
    _,signal=new_signal(client,release,body);workspace=row_signal(signal['id'])[2]
    route_once(workspace,signal)
    taskid=row_signal(signal['id'])[0]['route_receipt']['task_id']
    task=client.get('/api/workspace/tasks/'+taskid).json()
    response=client.post('/api/workspace/tasks/'+taskid+'/cancel',json={'version':task['version'],'reason':'原任务不再继续','request_id':str(uuid4())})
    assert response.status_code==200,response.text
    with Session(engine()) as db:
        first=db.get(PerceptionSignal,signal['id']);event=db.get(Event,first.event_id)
        for _ in range(102):
            duplicate=Event(workspace_id=workspace,kind=event.kind,idempotency_key='routing-duplicate:'+str(uuid4()),
                status='received',received_at=routing.now(),attempts=1,data=copy.deepcopy(event.data))
            db.add(duplicate);db.flush();assert receive(db,duplicate)
        last=db.scalar(select(PerceptionSignal).where(PerceptionSignal.event_id==duplicate.id));identity=last.id
        db.commit()
    route_once(workspace,{'id':identity})
    data,_,_=row_signal(identity)
    assert data['route_receipt']['status']=='skipped_existing_task' and data['route_receipt']['task_id']==taskid
    assert tasks_for(release)==[taskid]


def test_human_rejection_is_locked_until_automatic_task_receipt_commit(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import text,update
    from sqlalchemy.exc import DBAPIError
    from generic_agents import workspace_api
    release,obj,body,_,_,_=enable_route(client)
    observation,signal=new_signal(client,release,body);workspace=row_signal(signal['id'])[2]
    real_create=workspace_api.create_task;observed=[]
    def concurrent_reject():
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout='100ms'"))
            with pytest.raises(DBAPIError):
                db.execute(update(Observation).where(Observation.id==observation['id']).values(status='failed'))
                db.commit()
            db.rollback()
        return True
    def create(*args,**kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:observed.append(pool.submit(concurrent_reject).result(timeout=5))
        return real_create(*args,**kwargs)
    monkeypatch.setattr(workspace_api,'create_task',create)
    route_once(workspace,signal)
    assert observed==[True] and row_signal(signal['id'])[0]['route_receipt']['status']=='created'


def test_damaged_grant_does_not_block_original_signal_receiving_or_event_ack(client):
    release,obj,body,_,_,_=enable_route(client)
    with Session(engine()) as db:
        instance=db.get(DomainInstance,release['instance_id']);value=copy.deepcopy(instance.data['signal_routes'])
        value['routes'][0]['grant']['grant_hash']='0'*64;instance.data={**instance.data,'signal_routes':value};db.commit()
    observation,signal=new_signal(client,release,body)
    data,_,_=row_signal(signal['id'])
    assert signal['integrity_issue'] is None and signal['route_failure']['code']=='routing_integrity'
    assert data.get('route_intent') is None and not tasks_for(release)


def test_frozen_nonce_is_actor_bound_before_fresh_cas_and_options(client):
    from generic_agents.security import Principal
    from fastapi import HTTPException
    release,obj,body,_,command,enabled=enable_route(client)
    with Session(engine()) as db:
        instance=db.get(DomainInstance,release['instance_id'])
        other=Principal(str(uuid4()),instance.workspace_id,'operator','synthetic-foreign-principal')
        with pytest.raises(HTTPException) as error:routing.command(db,other,instance.id,'enable',command)
        assert error.value.status_code==409 and error.value.detail['code']=='routing_nonce_conflict'
        with pytest.raises(HTTPException) as error:routing.saved_request(instance,other,command['request_id'])
        assert error.value.status_code==404


def test_current_original_rejection_before_dispatch_creates_terminal_block(client):
    release,obj,body,_,_,_=enable_route(client)
    observation,signal=new_signal(client,release,body);workspace=row_signal(signal['id'])[2]
    with Session(engine()) as db:
        row=db.get(Observation,observation['id']);version=row.version
    response=client.post('/api/observations/'+observation['id']+'/verify',json={
        'expected_version':version,'reason':'原登记与可查依据不一致','decision':'rejected'})
    assert response.status_code==200,response.text
    route_once(workspace,signal)
    assert row_signal(signal['id'])[0]['route_receipt']['status']=='blocked' and not tasks_for(release)


def material_route(client):
    from tests.test_instance_workspace import cited_configuration,prepared,enable,object_row
    from tests.test_vertical_slice import manifest,publish
    from datetime import datetime,timezone,timedelta
    config,material=cited_configuration(client);release,_,_=prepared(client,config);enable(client,release);obj=object_row(client,release)
    source=publish(client,manifest('source',name='route_material_source',allowed_agent_roles=['perception'],execution={'adapter':'upload'},
        specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    extractor=publish(client,manifest('tool',name='route_material_rules',allowed_agent_roles=['perception'],execution={'adapter':'builtin','handler':'numeric_rules'},
        specification={'rules':[{'id':'high','field':'公开登记值','operator':'gt','threshold':50}]},data_policy={'cloud_allowed':False}))
    opt=client.get(endpoint(release)+'/options').json()
    response=client.post(endpoint(release)+'/enable',json=dict(request_id=str(uuid4()),expected_version=opt['instance_version'],options_hash=opt['options_hash'],
        release_id=release['id'],source_id=source['id'],extractor_id=extractor['id'],entity_ids=[obj['id']],business_id=release['available_business_ids'][0],
        max_tasks=10,reason='引用原资料的人工核查授权',confirm_human_review_only=True))
    assert response.status_code==201,response.text
    clock=datetime.now(timezone.utc)-timedelta(seconds=10)
    body=dict(source_id=source['id'],source_record_id=str(uuid4()),entity_id=obj['id'],extractor_id=extractor['id'],attributes={'公开登记值':65},
        observed_at=clock.isoformat(),business_day=str((clock+timedelta(hours=8)).date()),startup_mode='manual')
    return release,body,material


def test_archived_material_blocks_new_task_even_when_route_authorization_is_unchanged(client):
    from generic_agents.models import SetupMaterial
    release,body,material=material_route(client)
    _,signal=new_signal(client,release,body);workspace=row_signal(signal['id'])[2]
    with Session(engine()) as db:
        row=db.get(SetupMaterial,material['id']);row.archived=True;row.version+=1;db.commit()
    route_once(workspace,signal)
    assert row_signal(signal['id'])[0]['route_receipt']['status']=='blocked' and not tasks_for(release)


def test_current_material_archive_writer_cannot_commit_between_task_check_and_creation(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import text,update
    from sqlalchemy.exc import DBAPIError
    from generic_agents.models import SetupMaterial
    from generic_agents import workspace_api
    release,body,material=material_route(client)
    _,signal=new_signal(client,release,body);workspace=row_signal(signal['id'])[2]
    original=workspace_api.create_task;observed=[]
    def archive():
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout='100ms'"))
            with pytest.raises(DBAPIError):
                db.execute(update(SetupMaterial).where(SetupMaterial.id==material['id']).values(archived=True));db.commit()
            db.rollback()
        return True
    def create(*args,**kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:observed.append(pool.submit(archive).result(timeout=5))
        return original(*args,**kwargs)
    monkeypatch.setattr(workspace_api,'create_task',create)
    route_once(workspace,signal)
    assert observed==[True] and row_signal(signal['id'])[0]['route_receipt']['status']=='created'
    with Session(engine()) as db:assert db.get(SetupMaterial,material['id']).archived is False
