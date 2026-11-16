"""Owned-PG recovery: receive saved observations without deriving paused work."""
import copy
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import worker
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Observation,Event,Memory,Job,Task,CloudCall,Audit
from tests.test_perception_signals import setup
from tests.test_instance_workspace import configuration,save,OBJECTS,enable,object_row


def register(client,release,body):
    value={**body,'source_record_id':str(uuid4())}
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/reported-observations',json=value)
    assert response.status_code==201,response.text
    row=response.json()
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['observation_id'].as_string()==row['id']))
        return row,job.id


def pending_paused_pair(client,monkeypatch):
    # Extraction commits normally; suppress only delivery so pause can occur
    # between durable producer completion and the first actual inbox receipt.
    with monkeypatch.context() as patch:
        patch.setattr(worker,'deliver_events',lambda *args,**kwargs:None)
        release,obj,success,body,_=setup(client)
        failure,failed_job=register(client,release,body)
        instance=next(x for x in client.get('/api/instances').json() if x['id']==release['instance_id'])
        response=client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']})
        assert response.status_code==200,response.text
        assert worker.process_once(failed_job,kinds=['process_reported_observation'],deliver=False)
    with Session(engine()) as db:
        assert db.get(Observation,success['id']).status=='processed'
        assert db.get(Observation,failure['id']).status=='failed'
        ids={kind:db.scalar(select(Event.id).where(Event.kind==kind,Event.data['observation_id'].as_string()==identity))
            for kind,identity in [('observation.data',success['id']),('observation.failed',failure['id'])]}
        assert all(ids.values())
        assert all(db.get(Event,identity).status=='pending_delivery' for identity in ids.values())
        assert not list(db.scalars(select(Memory.id).where(Memory.data['event_id'].as_string().in_(ids.values()))))
        workspace=db.get(Event,ids['observation.data']).workspace_id
    return release,body,ids,workspace


def assert_receipts(db,ids,instance_id):
    for kind,identity in ids.items():
        event=db.get(Event,identity)
        rows=list(db.scalars(select(Memory).where(Memory.data['event_id'].as_string()==identity)))
        assert event.status=='received' and event.received_at is not None and event.attempts==1 and len(rows)==1
        assert rows[0].status=='candidate' and rows[0].data['not_authoritative'] is True
        receipt=event.data['derivation_receipt']
        assert rows[0].data['derivation_receipt']==receipt and receipt['instance_id']==instance_id
        assert receipt['schema']=='observation-derivation-skipped-v1' and receipt['instance_status']=='paused'
        assert receipt['skipped']==['data_gaps','state_refresh','goal_queries'] and receipt['code']=='instance_not_active'
        assert receipt['cloud_calls']==0 and receipt['device_actions'] is False and receipt['current_state_updated'] is False
        assert receipt['receipt_hash']==digest({k:v for k,v in receipt.items() if k!='receipt_hash'})
        audit=db.scalar(select(Audit).where(Audit.action=='observation.derivation_skipped',Audit.object_id==identity))
        assert audit is not None and audit.data==receipt


def test_paused_data_and_failed_receipts_persist_once_without_new_work_or_resume_backfill(client,monkeypatch):
    release,body,ids,workspace=pending_paused_pair(client,monkeypatch)
    with Session(engine()) as db:before_jobs=set(db.scalars(select(Job.id)))
    worker.deliver_events(workspace_id=workspace)
    with Session(engine()) as db:
        assert_receipts(db,ids,release['instance_id'])
        assert set(db.scalars(select(Job.id)))==before_jobs
        assert not list(db.scalars(select(Task.id))) and not list(db.scalars(select(CloudCall.id)))
    worker.deliver_events(workspace_id=workspace)
    with Session(engine()) as db:
        assert_receipts(db,ids,release['instance_id'])
        assert set(db.scalars(select(Job.id)))==before_jobs
        memory_count=len(list(db.scalars(select(Memory.id))))
    enable(client,release)  # Reactivation is explicit; old skipped receipts stay final.
    worker.deliver_events(workspace_id=workspace)
    with Session(engine()) as db:
        assert_receipts(db,ids,release['instance_id'])
        assert set(db.scalars(select(Job.id)))==before_jobs and len(list(db.scalars(select(Memory.id))))==memory_count
        assert not list(db.scalars(select(Task.id))) and not list(db.scalars(select(CloudCall.id)))


def test_paused_receipts_do_not_poison_later_active_event_in_same_batch(client,monkeypatch):
    release,body,ids,workspace=pending_paused_pair(client,monkeypatch)
    revision=save(client,configuration(),review_automation=True)
    member=client.get('/api/instances/members').json()[0]
    body_for_generation={'revision':revision,'objects':OBJECTS,'role_bindings':{'核验负责人':member['id']}}
    checked=client.post('/api/instances/check',json=body_for_generation)
    assert checked.status_code==200,checked.text
    response=client.post('/api/instances',json={**body_for_generation,
        'confirmed_configuration_hash':checked.json()['configuration_hash'],'confirm_configuration':True,
        'generation_intent':'new_instance','request_id':str(uuid4())})
    assert response.status_code==201,response.text
    active=response.json()
    assert active['instance_id']!=release['instance_id']
    enable(client,active);obj=object_row(client,active)
    with monkeypatch.context() as patch:
        patch.setattr(worker,'deliver_events',lambda *args,**kwargs:None)
        success,job=register(client,active,{**body,'entity_id':obj['id']})
        assert worker.process_once(job,kinds=['process_reported_observation'],deliver=False)
    with Session(engine()) as db:
        next_event=db.scalar(select(Event.id).where(Event.kind=='observation.data',Event.data['observation_id'].as_string()==success['id']))
        assert next_event and db.get(Event,next_event).status=='pending_delivery'
        before=set(db.scalars(select(Job.id)))
    worker.deliver_events(workspace_id=workspace)
    with Session(engine()) as db:
        assert_receipts(db,ids,release['instance_id'])
        event=db.get(Event,next_event)
        assert event.status=='received' and 'derivation_receipt' not in event.data
        assert len(list(db.scalars(select(Memory.id).where(Memory.data['event_id'].as_string()==next_event))))==1
        new_jobs=list(db.scalars(select(Job).where(Job.id.not_in(before))))
        assert new_jobs and all(j.kind=='refresh_object_state' and j.data['state_request']['instance_id']==active['instance_id'] for j in new_jobs)
        assert not list(db.scalars(select(Task.id))) and not list(db.scalars(select(CloudCall.id)))
