"""Real DB/API/outbox; model execution mocked here, native browser evidence separate."""
import io,copy
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from PIL import Image
from generic_agents.db import engine
from generic_agents.models import Observation,Job,Entity,CloudCall,Event,Memory,Capability
from generic_agents.worker import process_once
from generic_agents.local_observations import schedule
from generic_agents.config import settings
from tests.test_local_specialists import mocked_runtime,ready
from tests.test_instance_workspace import prepared,enable,object_row,configuration
from tests.test_evidence_assessment import request,run

def setup(client):
    release,_,_=prepared(client);enable(client,release);obj=object_row(client,release)
    installation=ready(client)
    cap=client.post('/api/specialists/installations/'+installation['id']+'/register',json={'version':installation['version']})
    assert cap.status_code==200
    data=io.BytesIO();Image.new('RGB',(20,20),'white').save(data,format='PNG')
    upload=client.post('/api/evidence',content=data.getvalue(),headers={'Content-Type':'image/png'});assert upload.status_code==201
    base='/api/workspace/instances/'+release['instance_id']
    body={'request_id':uuid4().hex,'entity_id':obj['id'],'extractor_id':cap.json()['capability_id'],'evidence_id':upload.json()['id']}
    response=client.post(base+'/local-observations',json=body);assert response.status_code==201,response.text
    return release,obj,base,body,response.json()

def finish(client,base,row):
    with Session(engine()) as db:job=db.scalar(select(Job).where(Job.idempotency_key=='extract:'+row['id']));identity=job.id
    assert process_once(identity,deliver=False)
    response=client.get(base+'/local-observations/'+row['id']);assert response.status_code==200,response.text
    return response.json()

def test_local_image_enters_common_observation_receipt_daily_and_query_without_clock_or_cloud(client,mocked_runtime):
    release,obj,base,body,row=setup(client)
    assert row['source_clock'] is None and row['candidate_only'] and row['status']=='received'
    assert client.post(base+'/local-observations',json=body).json()['id']==row['id']
    assert client.get(base+'/local-observation-requests/'+body['request_id']).json()['id']==row['id']
    complete=finish(client,base,row)
    assert complete['status']=='processed' and complete['data_received'] and complete['output']['detection']['cloud_calls']==0
    daily=client.get(base+'/local-observation-daily').json();assert daily[0]['processed']==1 and daily[0]['not_authoritative']
    result=run(client,request(release,[obj['id']],required_modalities=['observations'],goal='available_evidence'))['result']
    assert result['records'][0]['integrity_verified'] and result['records'][0]['source_times'][0]['value'] is None
    assert client.get(base+'/local-observations/'+row['id']+'/export').json()['observation']==complete
    with Session(engine()) as db:
        assert db.get(Entity,obj['id']).data==obj['attributes'] and db.scalar(select(CloudCall.id)) is None
        p=db.get(Observation,row['id']);assert p.observed_at is None and p.data['candidate_only']
        assert db.scalar(select(Job).where(Job.idempotency_key=='extract:'+p.id)).kind=='process_local_observation'
    schedule();schedule()
    assert client.get(base+'/local-observation-daily').json()==daily

def test_confirmed_review_is_visible_in_joint_query_but_never_promotes_local_counts_to_state(client,mocked_runtime,account):
    release,obj,base,_,row=setup(client);complete=finish(client,base,row)
    checked=client.post('/api/observations/'+row['id']+'/verify',json={'expected_version':complete['version'],'decision':'confirmed','reason':'核对公开候选，仅作资料依据'})
    assert checked.status_code==200,checked.text
    assert client.get(base+'/local-observation-daily').json()[0]['reviewed']==1
    result=run(client,request(release,[obj['id']],required_modalities=['observations'],goal='available_evidence'))['result']
    assert result['records'][0]['review_state']=='confirmed' and not result['objects'][0]['current_state_sufficient']
    from generic_agents.perception import object_state
    from generic_agents.security import Principal
    with Session(engine()) as db:
        state=object_state(db,Principal('test',account[2],'admin','test'),obj['id'],['detected_candidate_counts'])
        assert state['fields']=={} and state['status']=='insufficient';db.rollback()

def test_wrong_instance_or_changed_scalar_cannot_rebind_saved_local_result(client,mocked_runtime):
    release,obj,base,_,row=setup(client);finish(client,base,row)
    config=configuration();config['instanceProfile']['name']='另一个隔离核验实例'
    other,_,_=prepared(client,config);assert other['instance_id']!=release['instance_id']
    assert client.get('/api/workspace/instances/'+other['instance_id']+'/local-observations/'+row['id']).status_code==404
    with Session(engine()) as db:
        obs=db.get(Observation,row['id']);obs.business_day='2000-01-01';db.commit()
    value=client.get(base+'/local-observations/'+row['id']).json();assert value['integrity_error'] and value['output'] is None
    assert client.get(base+'/local-observations/'+row['id']+'/export').status_code==409
    with Session(engine()) as db:
        obs=db.get(Observation,row['id']);obs.business_day=obs.data['original_request']['business_day'];db.commit()
    for key,value in [('attributes',{'invented':999}),('evidence_ids',None),('extractor_id','changed-model')]:
        with Session(engine()) as db:
            obs=db.get(Observation,row['id']);original=obs.data[key]
            obs.data={**obs.data,key:original+original if key=='evidence_ids' else value};db.commit()
        value=client.get(base+'/local-observations/'+row['id']).json();assert value['integrity_error'] and value['output'] is None
        assert client.get(base+'/local-observations/'+row['id']+'/export').status_code==409
        with Session(engine()) as db:
            obs=db.get(Observation,row['id']);obs.data={**obs.data,key:original};db.commit()

def test_local_file_rejects_manual_field_invention_and_corrupt_original(client,mocked_runtime):
    release,obj,base,body,row=setup(client)
    with Session(engine()) as db:
        from generic_agents.models import Evidence
        ev=db.get(Evidence,body['evidence_id']);path=settings().storage_dir/ev.storage_key
        invented=copy.deepcopy(db.get(Observation,row['id']).data['original_request'])
    invented.update({'source_record_id':uuid4().hex,'attributes':{'invented_field':999}})
    assert client.post('/api/observations',json=invented).status_code==422
    original=path.read_bytes()
    try:
        path.write_bytes(b'bad fixture file')
        value=finish(client,base,row);assert value['status']=='failed' and value['integrity_error']
    finally:path.write_bytes(original)
    response=client.post('/api/observations/'+row['id']+'/retry',json={'expected_version':value['version'],'reason':'已恢复原图'})
    assert response.status_code==200,response.text
    assert finish(client,base,row)['status']=='processed'

def test_paused_instance_retains_history_and_blocks_new_local_input(client,mocked_runtime):
    release,obj,base,body,row=setup(client);finish(client,base,row)
    instance=next(r for r in client.get('/api/instances').json() if r['id']==release['instance_id'])
    assert client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']}).status_code==200
    assert client.get(base+'/local-observation-models').json()==[]
    assert client.get(base+'/local-observations').json()['records'][0]['id']==row['id']
    assert client.get(base+'/local-observations/'+row['id']).json()['output']
    assert client.post(base+'/local-observations',json={**body,'request_id':uuid4().hex}).status_code==409
