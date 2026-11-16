"""Business-scoped existing-record checks and durable human review, real numeric extractor."""
from uuid import uuid4
import copy
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import DataRun,DataGap,Job,Observation,Task,Entity,CloudCall,Acquisition,Membership,DomainInstance
from generic_agents.contracts import digest
from generic_agents.worker import process_once
from tests.test_input_failures import bad
from tests.test_observation_corrections import body as correction_body,run,url


def setup(client):
    r,obj,old,raw,_=bad(client)
    child=client.post(url(r,obj,old),json=correction_body(client,old)).json()['observation_id'];run(child)
    base='/api/workspace/instances/'+r['instance_id']+'/data-needs'
    options=client.get(base+'/options');assert options.status_code==200,options.text
    current=next(o for o in options.json()['objects'] if o['id']==obj['id']);assert current['field_labels'] and all(not name.startswith('field_') for name in current['field_labels'].values())
    request={'request_id':str(uuid4()),'release_id':r['id'],'business_id':r['available_business_ids'][0],
        'entity_id':obj['id'],'purpose':'核对公开工程登记已有的值，非现场验证','fields':['公开登记值'],'max_age_hours':24}
    return r,obj,child,base,request


def create(client,base,request):
    response=client.post(base,json=request);assert response.status_code==201,response.text
    return response.json()


def jobs(query_id):
    with Session(engine()) as db:ids=list(db.scalars(select(Job.id).where(Job.kind=='check_workspace_data_need',Job.status=='queued',Job.data['query_id'].as_string()==query_id)))
    assert ids
    for identifier in ids:assert process_once(identifier,deliver=False)


def review(client,record):
    return {'request_id':str(uuid4()),'expected_version':record['version'],'expected_input_hash':record['review_input_hash'],
        'reason':'对照公开工程原输入与提取回执一致，不确认现场状态','decision':'confirmed'}


def test_existing_record_confirm_then_fixed_need_resolves_without_parent_or_device(client):
    r,obj,child,base,request=setup(client);need=create(client,base,request)
    assert create(client,base,request)['id']==need['id']
    jobs(need['id']);detail=client.get(base+'/'+need['id']).json()
    assert detail['status']=='needs_review' and detail['business_received'] and not detail['parent_goal_completed']
    record=detail['records'][0];assert record['original_values']==record['extracted_values']=={'公开登记值':39.5}
    body=review(client,record);response=client.post('/api/observations/'+child+'/verify',json=body);assert response.status_code==200,response.text
    version=response.json()['version'];assert client.post('/api/observations/'+child+'/verify',json=body).json()['version']==version
    assert client.post('/api/observations/'+child+'/verify',json={**body,'reason':'substitute'}).status_code==409
    jobs(need['id']);detail=client.get(base+'/'+need['id']).json();assert detail['status']=='resolved'
    assert detail['records'][0]['review_receipt']['reason']==body['reason'] and len(detail['records'][0]['review_history'])==1
    assert not detail['parent_goal_completed'] and detail['background']['cloud_calls']==0
    with Session(engine()) as db:
        tasks=list(db.scalars(select(Task)));assert len(tasks)==1 and tasks[0].status=='coordination_completed'
        assert db.get(Entity,obj['id']).version==1 and not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Acquisition)))
        assert len(db.get(Observation,child).data['verification_history'])==1
        assert not list(db.scalars(select(Job).where(Job.kind.in_(['route_data_gap','recheck_data_gap']))))


def test_unknown_request_replay_and_create_conditions_cannot_be_replaced(client):
    r,obj,child,base,request=setup(client);need=create(client,base,request)
    assert client.post(base,json={**request,'purpose':'changed'}).status_code==409
    assert client.post(base,json={**request,'request_id':str(uuid4()),'fields':['guessed']}).status_code==422
    command={'request_id':str(uuid4()),'expected_version':need['version'],'reason':'核对现有保存记录'}
    first=client.post(base+'/'+need['id']+'/check',json=command);assert first.status_code==200,first.text
    assert client.post(base+'/'+need['id']+'/check',json=command).json()['version']==first.json()['version']
    assert client.post(base+'/'+need['id']+'/check',json={**command,'reason':'changed'}).status_code==409


def test_changed_scope_does_not_read_new_records_or_execute_job(client):
    r,obj,child,base,request=setup(client);need=create(client,base,request)
    with Session(engine()) as db:
        row=db.get(DomainInstance,r['instance_id']);row.data={**row.data,'enabled_business_ids':[]};db.commit()
    detail=client.get(base+'/'+need['id']).json();assert not detail['scope_valid'] and detail['records']==[]
    jobs(need['id'])
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.kind=='check_workspace_data_need'));assert job.status=='failed'
        assert not list(db.scalars(select(Task)))


def test_input_hash_and_rights_block_review_without_mutating_original(client):
    r,obj,child,base,request=setup(client);need=create(client,base,request);jobs(need['id'])
    record=client.get(base+'/'+need['id']).json()['records'][0];command=review(client,record)
    assert client.post('/api/observations/'+child+'/verify',json={**command,'expected_input_hash':'0'*64}).status_code==409
    assert client.post('/api/observations/'+child+'/verify',json={**command,'expected_input_hash':None}).status_code==422
    with Session(engine()) as db:
        row=db.scalar(select(Membership));row.role='viewer';db.commit()
    assert client.post('/api/observations/'+child+'/verify',json=command).status_code==403
    assert client.post(base,json={**request,'request_id':str(uuid4())}).status_code==403


def test_marker_tampering_and_legacy_route_cannot_widen_into_acquisition(client,monkeypatch):
    r,obj,child,base,request=setup(client);need=create(client,base,request)
    from generic_agents import data_gaps
    monkeypatch.setattr(data_gaps,'propose_acquisitions',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not acquire')),raising=False)
    with Session(engine()) as db:
        from generic_agents.security import Principal
        gap=db.scalar(select(DataGap));p=Principal(gap.data['actor_id'],gap.workspace_id,'admin','isolated')
        assert data_gaps.route(db,p,gap.id)=='succeeded';db.commit()
        assert gap.status=='needs_review'
        data=copy.deepcopy(gap.data);data['workspace_existing_check']['gap_request_hash']='0'*64;gap.data=data;db.commit()
    jobs(need['id'])
    with Session(engine()) as db:assert db.scalar(select(Job).where(Job.kind=='check_workspace_data_need')).status=='failed'


def test_archive_missing_field_uses_record_coordination_not_sensor(client):
    r,obj,child,base,request=setup(client)
    need=create(client,base,{**request,'source':'archive'});jobs(need['id'])
    detail=client.get(base+'/'+need['id']).json()
    assert detail['status']=='needs_existing_evidence' and detail['business_received'] and detail['records']==[]
    with Session(engine()) as db:assert not list(db.scalars(select(Acquisition)))


def test_closed_need_checked_again_reopens_when_original_latest_condition_no_longer_met(client):
    r,obj,child,base,request=setup(client);need=create(client,base,request);jobs(need['id'])
    detail=client.get(base+'/'+need['id']).json();client.post('/api/observations/'+child+'/verify',json=review(client,detail['records'][0]));jobs(need['id'])
    assert client.get(base+'/'+need['id']).json()['status']=='resolved'
    # Existing generic rejection is a legitimate new human decision; only explicit check reopens closed need.
    with Session(engine()) as db:version=db.get(Observation,child).version
    response=client.post('/api/observations/'+child+'/verify',json={'expected_version':version,'decision':'rejected','reason':'复查发现这份工程登记依据不足'})
    assert response.status_code==200,response.text
    old=client.get(base+'/'+need['id']).json()
    command={'request_id':str(uuid4()),'expected_version':old['version'],'reason':'按原条件复查，保留此前结论'}
    assert client.post(base+'/'+need['id']+'/check',json=command).status_code==200
    jobs(need['id']);assert client.get(base+'/'+need['id']).json()['status']!='resolved'
    with Session(engine()) as db:assert db.scalar(select(Task)).status=='draft'


def test_valid_archive_field_is_recorded_and_export_keeps_saved_scope(client):
    r,obj,child,base,request=setup(client)
    with Session(engine()) as db:field=next(iter(db.get(Entity,obj['id']).data))
    need=create(client,base,{**request,'source':'archive','fields':[field]});jobs(need['id'])
    detail=client.get(base+'/'+need['id']).json();assert detail['status']=='sufficient' and detail['archive_evidence'][0]['fields'][field]
    export=client.get(base+'/'+need['id']+'/export');assert export.status_code==200
    assert export.json()['context_hash']==digest(export.json()['context']) and not export.json()['physical_state_verified']


def test_saved_request_cannot_change_scope_and_read_only_jobs_do_not_use_tools(client,monkeypatch):
    r,obj,child,base,request=setup(client);need=create(client,base,request)
    from generic_agents import agents
    original=agents.data_agent
    def verify(*args,**kwargs):
        assert kwargs.get('cached_only') is True and kwargs.get('learning') is False
        return original(*args,**kwargs)
    monkeypatch.setattr(agents,'data_agent',verify);jobs(need['id'])
    with Session(engine()) as db:
        row=db.get(DataRun,need['id']);data=copy.deepcopy(row.data);data['request']['fields']=['changed'];row.data=data;db.commit()
    assert client.get(base+'/'+need['id']).status_code==409


def test_removed_gap_marker_still_cannot_fall_into_legacy_device_route(client):
    import pytest
    from fastapi import HTTPException
    from generic_agents.security import Principal
    from generic_agents.data_gaps import route,recheck
    r,obj,child,base,request=setup(client);need=create(client,base,request)
    with Session(engine()) as db:
        gap=db.scalar(select(DataGap));gap.data={k:v for k,v in gap.data.items() if not k.startswith('workspace_existing_check')};db.commit()
        p=Principal(gap.data['actor_id'],gap.workspace_id,'admin','isolated')
        with pytest.raises(HTTPException):route(db,p,gap.id)
        with pytest.raises(HTTPException):recheck(db,p,gap.id)
        assert not list(db.scalars(select(Acquisition)))
