"""Real numeric extraction and durable local refresh in disposable PostgreSQL."""
import copy
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,ObjectState,Entity,Observation,CloudCall,DomainInstance,Membership
from generic_agents.state_refresh import KIND,checked,process
from generic_agents.security import Principal
from generic_agents.worker import deliver_events,process_once
from tests.test_mixed_evidence import setup


def jobs(obj):
    with Session(engine()) as db:return [j.id for j in db.scalars(select(Job).where(Job.kind==KIND,
        Job.data['state_request']['entity_id'].as_string()==obj['id']).order_by(Job.created_at))]

def start(client):
    r,obj,obs,body=setup(client)
    with Session(engine()) as db:assert db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id'])) is None
    deliver_events(observation_id=obs['id']);identities=jobs(obj);assert len(identities)==2
    assert process_once(identities[-1],deliver=False)
    return r,obj,obs,body,identities[-1]

def test_background_updates_without_state_page_and_receipts_are_persisted(client):
    r,obj,obs,_,identity=start(client)
    with Session(engine()) as db:
        state=db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id']));job=db.get(Job,identity)
        assert state.data['fields']['公开登记值']['value']==27.5 and state.version==1
        assert job.status=='succeeded' and checked(job)['trigger_observation_id']==obs['id']
        assert job.data['state_result']['state_hash']==state.data['state_hash']
        assert not state.data['current_state_verified'] and not job.data['state_result']['entity_updated']
        assert db.get(Entity,obj['id']).version==obj['version'] and not list(db.scalars(select(CloudCall)))
        expiry=db.get(Job,job.data['next_expiry_job_id']);assert expiry.status=='queued' and expiry.available_at>datetime.now(timezone.utc)
    deliver_events(observation_id=obs['id']);assert len(jobs(obj))==3
    view=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state").json()
    assert view['background']['latest']['id']==identity and view['background']['next_expiry']['status']=='queued'
    assert view['state']['state_version']==1


def test_saved_review_triggers_automatic_withdrawal_and_confirmation_recovery(client):
    r,obj,obs,_,first=start(client)
    for decision,reason in [('rejected','公开样例登记依据撤回'),('confirmed','重新核对原登记凭证')]:
        with Session(engine()) as db:version=db.get(Observation,obs['id']).version
        response=client.post('/api/observations/'+obs['id']+'/verify',json={'expected_version':version,'decision':decision,'reason':reason})
        assert response.status_code==200,response.text
        with Session(engine()) as db:job=db.scalar(select(Job).where(Job.kind==KIND,Job.data['state_request']['reason'].as_string()=='人工核查结果已保存').order_by(Job.created_at.desc()));identity=job.id
        assert process_once(identity,deliver=False)
        with Session(engine()) as db:
            state=db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id']))
            assert bool(state.data['fields'])==(decision=='confirmed')
            assert not state.data['current_state_verified']
    with Session(engine()) as db:assert db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id'])).version==3


def test_time_expiry_rechecks_without_new_source_or_capture(client,monkeypatch):
    _,obj,_,_,first=start(client)
    with Session(engine()) as db:
        original=db.get(Job,first);expiry=db.get(Job,original.data['next_expiry_job_id']);future=expiry.available_at
        expiry.available_at=datetime.now(timezone.utc)-timedelta(seconds=1);identity=expiry.id;db.commit()
    monkeypatch.setattr('generic_agents.perception.now',lambda:future+timedelta(seconds=1))
    assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        state=db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id']));job=db.get(Job,identity)
        assert job.status=='succeeded' and state.version==2
        assert {'field':'公开登记值','reason':'stale'} in state.data['gaps']
        assert not list(db.scalars(select(Job).where(Job.kind==KIND,Job.status=='queued',Job.data['state_request']['due_state_hash'].as_string().is_not(None))))
        assert not list(db.scalars(select(CloudCall)))


def test_manual_request_replay_and_scope_changes_do_not_replace_original(client):
    r,obj,_,_,_=start(client);url=f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/refresh-state";body={'request_id':str(uuid4())}
    response=client.post(url,json=body);assert response.status_code==200,response.text
    identity=response.json()['id'];assert client.post(url,json=body).json()['id']==identity
    assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        job=db.get(Job,identity);saved=copy.deepcopy(job.data);entity=db.get(Entity,obj['id']);entity.version+=1;db.commit()
    assert client.post(url,json=body).status_code==409
    with Session(engine()) as db:assert db.get(Job,identity).data==saved


def test_pause_or_revocation_blocks_queued_refresh_without_silent_fallback(client,account):
    r,obj,obs,_=setup(client);deliver_events(observation_id=obs['id']);identity=jobs(obj)[-1]
    with Session(engine()) as db:instance=db.get(DomainInstance,r['instance_id']);instance.status='paused';db.commit()
    assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        assert db.get(Job,identity).status=='failed'
        assert db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id'])) is None
        instance=db.get(DomainInstance,r['instance_id']);instance.status='partially_active';db.commit()
    url=f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/refresh-state"
    identity=client.post(url,json={'request_id':str(uuid4())}).json()['id']
    with Session(engine()) as db:m=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));m.role='viewer';db.commit()
    assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        assert db.get(Job,identity).status=='failed' and not list(db.scalars(select(CloudCall)))
        assert db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id'])) is None
    assert client.post(url,json={'request_id':str(uuid4())}).status_code==403


def test_trigger_tampering_cannot_refresh(client,account):
    r,obj,obs,_=setup(client);deliver_events(observation_id=obs['id']);identity=jobs(obj)[-1]
    with Session(engine()) as db:
        job=db.get(Job,identity);data=copy.deepcopy(job.data);data['state_request']['entity_id']='different-object';job.data=data;db.commit()
    assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        assert db.get(Job,identity).status=='failed'
        assert db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id'])) is None


def test_lease_recovery_preserves_one_future_expiry_and_scope(client,account):
    _,obj,_,_,identity=start(client)
    with Session(engine()) as db:
        job=db.get(Job,identity);before=job.data['next_expiry_job_id'];p=Principal(job.data['actor_id'],account[2],'admin','test')
        process(db,p,job);process(db,p,job);db.commit()
        assert job.data['next_expiry_job_id']==before and db.get(Job,before).status=='queued'
        assert db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id'])).version==1
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):process(db,Principal(p.user_id,str(uuid4()),'admin','test'),job)


def test_broken_background_receipt_is_not_shown_as_success(client):
    r,obj,_,_,identity=start(client)
    with Session(engine()) as db:
        row=db.get(Job,identity);data=copy.deepcopy(row.data);data['state_result']['state_version']=999;row.data=data;db.commit()
    value=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state").json()
    assert value['background']['latest']['status']=='blocked_integrity'
    assert value['background']['latest']['result'] is None
    assert value['state']['state_version']==1


def test_corrupt_data_handoff_does_not_write_foreign_state(client):
    from generic_agents.models import Event
    _,obj,obs,_=setup(client)
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind=='observation.data'));data=copy.deepcopy(event.data)
        data['entity_id']=str(uuid4());event.data=data;identity=event.id;db.commit()
    deliver_events(observation_id=obs['id'])
    with Session(engine()) as db:
        assert db.get(Event,identity).status=='blocked_delivery'
        assert len(list(db.scalars(select(Job).where(Job.kind==KIND))))==1
        assert db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id'])) is None


def test_corrupt_state_cache_recomputed_from_original_without_reusing_bad_values(client):
    from generic_agents.models import Audit
    r,obj,_,_,_=start(client)
    with Session(engine()) as db:
        state=db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id']));data=copy.deepcopy(state.data)
        data['fields']['公开登记值']['value']=999;data['current_state_verified']=True;state.data=data;db.commit()
    view=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state").json()['state']
    assert view['fields']['公开登记值']['value']==27.5 and not view['current_state_verified']
    assert view['state_version']==2 and view['history'][-1]['integrity_failed']
    assert view['history'][-1]['fields']=={}
    with Session(engine()) as db:
        evidence=db.scalar(select(Audit).where(Audit.action=='object_state.cache_integrity_failed'))
        assert evidence.data['rejected_snapshot']['fields']['公开登记值']['value']==999
        assert db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id'])).data['fields']['公开登记值']['value']==27.5
