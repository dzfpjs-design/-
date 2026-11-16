"""Real failed manual input, append-only correction and data acknowledgement."""
import copy
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Observation,Job,Event,CloudCall
from generic_agents.worker import process_once,deliver_events
from tests.test_input_failures import bad,refresh_jobs


def url(r,obj,obs):return f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state-inputs/{obs['id']}/correct"
def body(client,obs,value=39.5):
    with Session(engine()) as db:version=db.get(Observation,obs['id']).version
    return {'request_id':str(uuid4()),'expected_version':version,'reason':'原空值另存公开工程更正，非现场测量','values':{'公开登记值':value}}
def state(client,r,obj):return client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state").json()['state']
def run(child,deliver=True):
    with Session(engine()) as db:job=db.scalar(select(Job).where(Job.idempotency_key=='extract:'+child));identity=job.id
    if deliver:
        assert process_once(identity,deliver=False);deliver_events(observation_id=child)
    else:
        from unittest.mock import patch
        with patch('generic_agents.worker.deliver_events'):
            assert process_once(identity,deliver=False)


def test_correction_is_saved_separately_and_only_delivered_success_closes_old_gap(client):
    r,obj,old,raw,_=bad(client)
    with Session(engine()) as db:original=copy.deepcopy(db.get(Observation,old['id']).data['original_request'])
    request=body(client,old);response=client.post(url(r,obj,old),json=request);assert response.status_code==201,response.text
    child=response.json()['observation_id'];assert child!=old['id']
    assert client.post(url(r,obj,old),json=request).json()['observation_id']==child
    assert client.post(url(r,obj,old),json={**request,'reason':'cannot substitute'}).status_code==409
    assert len(state(client,r,obj)['input_issues'])==2
    run(child,deliver=False)
    assert state(client,r,obj)['input_issues'][0]['observation_id']==old['id']
    deliver_events(observation_id=child);refresh_jobs(obj)
    view=state(client,r,obj)
    assert view['fields']['公开登记值']['value']==39.5 and not view['input_issues'] and view['status']=='observed'
    assert view['corrected_inputs'][0]['correction_observation_id']==child and not view['current_state_verified']
    assert client.post(url(r,obj,old),json=body(client,old)).status_code==409
    with Session(engine()) as db:
        parent=db.get(Observation,old['id']);new=db.get(Observation,child)
        assert parent.status=='failed' and parent.data['original_request']==original and new.data['original_request']['attributes']=={'公开登记值':39.5}
        assert len(list(db.scalars(select(Event).where(Event.kind=='observation.failed'))))==1
        assert not list(db.scalars(select(CloudCall)))


def test_invalid_values_or_other_fields_do_not_create_a_correction(client):
    r,obj,old,raw,_=bad(client)
    for values in [{'公开登记值':None},{'公开登记值':True},{'公开登记值':'39.5'},{'unrelated':39.5}]:
        request={**body(client,old),'values':values};assert client.post(url(r,obj,old),json=request).status_code==422
    with Session(engine()) as db:assert len(list(db.scalars(select(Observation))))==1


def test_correction_scope_and_original_ledger_cannot_be_replaced(client):
    r,obj,old,raw,_=bad(client);request=body(client,old)
    from tests.test_instance_workspace import object_row
    other=object_row(client,r)
    assert client.post(url(r,other,old),json=request).status_code==404
    response=client.post(url(r,obj,old),json=request);assert response.status_code==201
    child=response.json()['observation_id'];run(child)
    with Session(engine()) as db:
        row=db.get(Observation,old['id']);data=copy.deepcopy(row.data);data['correction_requests'][0]['reason']='changed';row.data=data;db.commit()
    view=state(client,r,obj);assert view['input_issues'] and not view['corrected_inputs']
    assert client.post(url(r,obj,old),json=request).status_code==409


def test_rejected_correction_does_not_close_original_failure(client):
    r,obj,old,raw,_=bad(client);response=client.post(url(r,obj,old),json=body(client,old));child=response.json()['observation_id'];run(child)
    with Session(engine()) as db:version=db.get(Observation,child).version
    response=client.post('/api/observations/'+child+'/verify',json={'expected_version':version,'reason':'工程核查拒绝此新值','decision':'rejected'})
    assert response.status_code==200,response.text
    view=state(client,r,obj);assert view['input_issues'] and not view['corrected_inputs'] and view['status']=='insufficient'


def test_same_request_in_parallel_creates_one_record_and_uuid_cannot_cross_originals(client):
    r,obj,old,raw,_=bad(client);request=body(client,old)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:client.post(url(r,obj,old),json=request),range(2)))
    assert all(r.status_code==201 for r in responses),[r.text for r in responses]
    assert len({r.json()['observation_id'] for r in responses})==1
    with Session(engine()) as db:
        parent=db.get(Observation,old['id']);duplicate=Observation(workspace_id=parent.workspace_id,source_id=parent.source_id,source_record_id='distinct-original',processing_version=parent.processing_version,entity_id=parent.entity_id,observed_at=parent.observed_at,business_day=parent.business_day,status='failed',data=copy.deepcopy(parent.data))
        duplicate.data={k:v for k,v in duplicate.data.items() if not k.startswith('correction_')}
        from generic_agents.contracts import digest
        duplicate.data['original_request']['source_record_id']=duplicate.source_record_id;duplicate.data['input_hash']=digest(duplicate.data['original_request']);db.add(duplicate);db.flush();oid=duplicate.id;db.commit()
    request['expected_version']=1
    assert client.post(url(r,obj,{'id':oid}),json=request).status_code==409
    with Session(engine()) as db:assert len(list(db.scalars(select(Observation))))==3


def test_revoked_operator_cannot_correct_and_existing_correction_is_not_replayed(client,account):
    r,obj,old,raw,_=bad(client);request=body(client,old)
    assert client.post(url(r,obj,old),json=request).status_code==201
    with Session(engine()) as db:
        from generic_agents.models import Membership
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='viewer';db.commit()
    assert client.post(url(r,obj,old),json=request).status_code==403


def test_rejected_correction_can_be_replaced_without_confusing_same_time_values(client):
    r,obj,old,raw,_=bad(client);response=client.post(url(r,obj,old),json=body(client,old));first=response.json()['observation_id'];run(first)
    with Session(engine()) as db:version=db.get(Observation,first).version
    assert client.post('/api/observations/'+first+'/verify',json={'expected_version':version,'reason':'拒绝第一次更正，保留原审核','decision':'rejected'}).status_code==200
    response=client.post(url(r,obj,old),json=body(client,old,40.5));assert response.status_code==201,response.text
    second=response.json()['observation_id'];run(second)
    view=state(client,r,obj)
    assert view['fields']['公开登记值']['value']==40.5 and not view['input_issues'] and view['status']=='observed'
    assert view['superseded_records'][0]['observation_id']==first
    with Session(engine()) as db:
        assert db.get(Observation,first).data['human_verification']['decision']=='rejected'
        assert len(db.get(Observation,old['id']).data['correction_requests'])==2


def test_three_saved_corrections_are_the_limit_without_deleting_rejected_history(client):
    r,obj,old,raw,_=bad(client);children=[]
    for value in [39.5,40.5,41.5]:
        response=client.post(url(r,obj,old),json=body(client,old,value));assert response.status_code==201
        child=response.json()['observation_id'];children.append(child);run(child)
        with Session(engine()) as db:version=db.get(Observation,child).version
        assert client.post('/api/observations/'+child+'/verify',json={'expected_version':version,'reason':'隔离工程验证：拒绝并保留此更正','decision':'rejected'}).status_code==200
    assert client.post(url(r,obj,old),json=body(client,old,42.5)).status_code==409
    with Session(engine()) as db:
        parent=db.get(Observation,old['id']);assert len(parent.data['correction_requests'])==3 and parent.status=='failed'
        assert all(db.get(Observation,c).data['human_verification']['decision']=='rejected' for c in children)
