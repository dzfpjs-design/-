"""Generated domain -> real DB outbox -> human review/receipts. Inference mocked here."""
import copy,json
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Memory,Entity,CloudCall,VideoAlert
from generic_agents.security import Principal
from generic_agents.local_video import receive_video_events
from generic_agents.instance_compiler import signature_value
from tests.test_media import media_runtime,channel,capture,finish
from tests.test_local_specialists import ready,mocked_runtime
from tests.test_instance_workspace import prepared,enable,object_row,configuration,complete


def linked(client):
    installation=ready(client);ch=channel(client);finish(client,capture(client,ch,installation).json())
    ch=client.get('/api/media/channels').json()[0]
    ch=client.post('/api/media/channels/'+ch['id']+'/register',json={'version':ch['version']}).json()
    release,_,_=prepared(client);enable(client,release);obj=object_row(client,release)
    body={'channel_version':ch['version'],'entity_id':obj['id'],'entity_version':obj['version'],
        'reason':'公开合成视频的场景关联，候选框不代表档案身份','confirm_scene_association':True,
        'instance_release_id':release['id'],'business_id':release['available_business_ids'][0]}
    response=client.put('/api/media/channels/'+ch['id']+'/domain-binding',json=body)
    assert response.status_code==200,response.text
    return installation,ch,release,obj,body,response.json()


def pending(client,ch,installation):
    _,video=finish(client,capture(client,ch,installation).json())
    assert video['status']=='succeeded',video.get('error')
    task=client.get('/api/workspace/tasks/'+video['alerts'][0]['task_id']).json()
    assert task['status']=='workflow_pending';return task,video


def test_generated_object_name_explicit_business_route_and_complete_review(client,media_runtime,account):
    installation,ch,r,obj,body,binding=linked(client)
    option=next(x for x in client.get('/api/media/domain-options').json() if x['id']==obj['id'])
    assert option['label']=='公开资料场景' and option['type']=='观察场景'
    assert option['instance']['businesses'][0]['name']=='资料核验'
    assert client.put('/api/media/channels/'+ch['id']+'/domain-binding',json={**body,'version':binding['version'],'business_id':None}).status_code==422
    t,video=pending(client,ch,installation)
    assert t['instance_id']==r['instance_id'] and t['business_name']=='资料核验'
    assert t['object_snapshots'][0]['attributes']==obj['attributes'] and t['perception_event']['source_clock_utc']=='unknown'
    assert complete(client,t).status_code==409
    response=client.post('/api/workspace/tasks/'+t['id']+'/start-review',json={'version':t['version']})
    assert response.status_code==200,response.text;t=response.json()
    saved=client.put('/api/workspace/tasks/'+t['id']+'/note',json={'version':t['version'],'step_id':'check','note':'候选线索待核查','perception_decision':'uncertain'})
    assert saved.status_code==200;t=saved.json()
    assert client.get('/api/workspace/tasks/'+t['id']).json()['workflow_runtime']['drafts']['check']['perception_decision']=='uncertain'
    first=complete(client,t);assert first.status_code==200,first.text;t=first.json()
    assert complete(client,t).status_code==422
    last=complete(client,t,perception_decision='not_abnormal');assert last.status_code==200,last.text;t=last.json()
    assert t['status']=='workflow_completed' and t['perception_review']['decision']=='not_abnormal'
    restart=client.post('/api/workspace/tasks/'+t['id']+'/start-review',json={'version':t['version']})
    assert restart.status_code==409
    assert client.get('/api/workspace/tasks/'+t['id']).json()['status']=='workflow_completed'
    with Session(engine()) as db:
        original=db.get(Entity,obj['id']);assert original.version==obj['version'] and original.data==obj['attributes']
        m=db.get(Memory,t['workflow_runtime']['data_receipt_id']);assert m.status=='candidate'
        assert m.data['perception_review']['decision']=='not_abnormal' and not m.data['entity_state_updated']
        alert=db.get(VideoAlert,video['alerts'][0]['id']);assert alert.data['business_review']['task_id']==t['id']
        count=len(db.scalars(select(Task)).all());receive_video_events(db,Principal(t['review_started']['actor'],account[2],'admin','test'),video['id']);db.commit()
        assert len(db.scalars(select(Task)).all())==count and not db.scalars(select(CloudCall)).all()
    assert len(client.get('/api/workspace/instances/'+r['instance_id']+'/observations').json())==9
    receipts=client.post('/api/media/daily/reconcile').json()
    daily=client.get('/api/workspace/instances/'+r['instance_id']+'/daily').json();assert len(daily)==1
    assert len(daily[0]['records'])==1 and daily[0]['records'][0]['business_target']['business_id']==body['business_id']
    assert not daily[0]['source_observation_day_verified']


def test_pause_before_capture_prevents_new_read_and_preserves_old_original(client,media_runtime):
    from generic_agents.worker import process_once
    installation,ch,r,obj,body,binding=linked(client)
    old=client.get('/api/media/sessions').json()[0]
    queued=capture(client,ch,installation).json();i=next(x for x in client.get('/api/instances').json() if x['id']==r['instance_id'])
    assert client.post('/api/instances/'+i['id']+'/pause',json={'version':i['version']}).status_code==200
    assert process_once(queued['job_id'],deliver=False)
    saved=next(x for x in client.get('/api/media/sessions').json() if x['id']==queued['id'])
    assert saved['status']=='failed' and '实例' in saved['error'] and saved['video_run_id'] is None
    assert client.get('/api/workspace/instances/'+i['id']+'/tasks').json()==[]
    assert client.get('/api/videos/'+old['video_run_id']+'/original').status_code==200


def test_pause_after_capture_preserves_original_and_blocks_new_processing_and_delivery(client,media_runtime):
    from generic_agents.worker import process_once
    installation,ch,r,obj,body,binding=linked(client)
    queued=capture(client,ch,installation).json();assert process_once(queued['job_id'],deliver=False)
    saved=next(x for x in client.get('/api/media/sessions').json() if x['id']==queued['id']);assert saved['status']=='captured'
    i=next(x for x in client.get('/api/instances').json() if x['id']==r['instance_id'])
    assert client.post('/api/instances/'+i['id']+'/pause',json={'version':i['version']}).status_code==200
    video=client.get('/api/videos/'+saved['video_run_id']).json();assert process_once(video['job_id'],deliver=False)
    video=client.get('/api/videos/'+video['id']).json();assert video['status']=='failed'
    assert not video['alerts'] and '授权' in video['error']
    assert client.get('/api/workspace/instances/'+i['id']+'/tasks').json()==[]
    assert client.get('/api/videos/'+video['id']+'/original').status_code==200


def test_instance_upgrade_stops_old_binding_and_old_received_task_keeps_flow(client,media_runtime):
    installation,ch,r,obj,body,binding=linked(client);t,video=pending(client,ch,installation)
    c=configuration();c['flows']['资料核验']['steps'][0]['name']='更新的核验步骤'
    f=c['flows']['资料核验'];f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    new,_,_=prepared(client,c,r['instance_id']);enable(client,new)
    ch=next(x for x in client.get('/api/media/channels').json() if x['id']==ch['id'])
    response=capture(client,ch,installation);assert response.status_code==409 and '版本' in response.json()['detail']
    assert len(client.get('/api/media/sessions').json())==2
    assert client.get('/api/videos/'+video['id']+'/original').status_code==200
    detail=client.get('/api/workspace/tasks/'+t['id']).json();assert detail['business']['flow']['steps'][0]['name']=='核对资料'
    response=client.post('/api/workspace/tasks/'+t['id']+'/start-review',json={'version':t['version']})
    assert response.status_code==200,response.text


def test_media_corruption_prevents_review_and_retains_task(client,media_runtime):
    from generic_agents.models import Evidence
    from generic_agents.config import settings
    installation,ch,r,obj,body,binding=linked(client);t,video=pending(client,ch,installation)
    with Session(engine()) as db:
        ev=db.get(Evidence,t['perception_event']['original_evidence_id']);path=settings().storage_dir/ev.storage_key
        raw=path.read_bytes();path.write_bytes(raw+b'tampered')
    try:
        assert client.post('/api/workspace/tasks/'+t['id']+'/start-review',json={'version':t['version']}).status_code==409
        assert client.get('/api/workspace/tasks/'+t['id']).json()['status']=='workflow_pending'
    finally:path.write_bytes(raw)
