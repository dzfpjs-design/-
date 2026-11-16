"""Real PostgreSQL/control path; mocked video inference is not model quality proof."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Observation, Task, Entity, VideoDomainBinding, Memory, VideoDaily, CloudCall
from generic_agents.local_video import receive_video_events
from generic_agents.security import Principal
from generic_agents import video_daily
from tests.test_media import media_runtime, channel, capture, finish
from tests.test_local_specialists import mocked_runtime, ready
from tests.test_vertical_slice import manifest, publish, entity


def prepared(client):
    install=ready(client);ch=channel(client);finish(client,capture(client,ch,install).json())
    ch=client.get('/api/media/channels').json()[0]
    r=client.post('/api/media/channels/'+ch['id']+'/register',json={'version':ch['version']});assert r.status_code==200,r.text
    return r.json(),install,entity(client,publish(client,manifest()))


def bind(client,ch,obj,**extra):
    return client.put('/api/media/channels/'+ch['id']+'/domain-binding',json={'channel_version':ch['version'],
        'entity_id':obj['id'],'entity_version':obj['version'],'confirm_scene_association':True,
        'reason':'确认合成场景关联，不将检测框作为真实对象身份',**extra})


def test_candidates_unknown_clock_business_drafts_and_no_authoritative_mutation(client,media_runtime,account):
    ch,install,obj=prepared(client);r=bind(client,ch,obj);assert r.status_code==200,r.text
    s,d=finish(client,capture(client,ch,install).json());assert d['status']=='succeeded',d.get('error')
    state=client.get('/api/media/domain-state/'+obj['id']).json()
    assert len(state['observations'])==9 and len(state['tasks'])==1 and state['not_authoritative']
    assert d['alerts'][0]['task_id']==state['tasks'][0]['id']
    with Session(engine()) as db:
        observations=db.scalars(select(Observation)).all();assert len(observations)==9
        assert all(o.observed_at is None and o.business_day is None and o.status=='extracted_unclocked' for o in observations)
        original=db.get(Entity,obj['id']);assert original.data==obj['data']
        assert original.version==obj['version']
        tasks=db.scalars(select(Task)).all();assert len(tasks)==1 and tasks[0].status=='draft'
        assert not tasks[0].data['evidence']['device_action_authorized']
        # Receiving the same outbox again never creates duplicate business/data records.
        receive_video_events(db,Principal(tasks[0].owner_id,account[2],'admin','test'),d['id']);db.commit()
        assert len(db.scalars(select(Observation)).all())==9 and len(db.scalars(select(Task)).all())==1
        assert not db.scalars(select(CloudCall)).all()
    perception=client.get('/api/entities/'+obj['id']+'/perception-state').json()
    assert perception['status']=='insufficient' and not perception['fields'] and len(perception['unclocked_candidates'])==9
    candidate=state['observations'][0]
    with Session(engine()) as db: candidate_version=db.get(Observation,candidate['id']).version
    assert client.post('/api/observations/'+candidate['id']+'/verify',json={'expected_version':candidate_version,
        'decision':'confirmed','reason':'检查画面不能创造缺失的拍摄时间'}).status_code==409
    from generic_agents.data_requirements import query
    with Session(engine()) as db:
        p=Principal(tasks[0].owner_id,account[2],'admin','test')
        selected=query(db,p,[obj['id']],['temperature'],{'source':'observations','minimum_verification':'unverified_extraction',
            'time':{'mode':'latest','max_age_seconds':30}},learning=False)
        assert not selected['observation_evidence']
        assert any(x.get('source')=='unclocked_video_candidates' and x['count']==9 for x in selected['excluded_evidence'])


def test_entity_changed_after_queue_prevents_new_domain_observations_but_preserves_video(client,media_runtime):
    ch,install,obj=prepared(client);bind(client,ch,obj);queued=capture(client,ch,install).json()
    with Session(engine()) as db:
        row=db.get(Entity,obj['id']);row.version+=1;db.commit()
    _,detail=finish(client,queued);assert detail['status']=='succeeded'
    assert detail['alerts'][0]['domain_delivery']=='blocked_binding_changed'
    assert client.get('/api/videos/'+detail['id']+'/original').status_code==200
    with Session(engine()) as db: assert not db.scalars(select(Observation)).all()


def test_association_frozen_at_request_and_revoke_prevents_late_domain_writes(client,media_runtime):
    ch,install,obj=prepared(client)
    first=capture(client,ch,install).json()
    binding=bind(client,ch,obj).json();finish(client,first)
    assert not client.get('/api/media/domain-state/'+obj['id']).json()['observations']
    ch=client.get('/api/media/channels').json()[0];second=capture(client,ch,install).json()
    r=client.post('/api/media/domain-bindings/'+binding['id']+'/disable',json={'version':binding['version']});assert r.status_code==200
    _,d=finish(client,second);assert d['status']=='succeeded' and d['delivery']['data_received']==9
    assert d['alerts'][0]['domain_delivery']=='blocked_binding_changed'
    with Session(engine()) as db: assert not db.scalars(select(Observation)).all() and not db.scalars(select(Task)).all()


def test_binding_cas_history_and_other_workspace_target_rejected(client,media_runtime):
    ch,install,obj=prepared(client);b=bind(client,ch,obj).json()
    assert bind(client,ch,obj).status_code==409
    assert bind(client,ch,obj,version=b['version']-1).status_code==422
    other=entity(client,publish(client,manifest(name='other')))
    r=bind(client,ch,other,version=b['version']);assert r.status_code==200,r.text
    assert r.json()['generation']==2 and r.json()['history'][0]['entity_id']==obj['id']
    assert bind(client,ch,other,version=b['version']).status_code==409
    with Session(engine()) as db:
        row=db.get(Entity,other['id']);row.version+=1;db.commit()
    assert bind(client,ch,other,version=r.json()['version']).status_code==409
    from generic_agents.models import Workspace
    with Session(engine()) as db:
        workspace=Workspace(name='隔离对象验证');db.add(workspace);db.flush()
        foreign=Entity(workspace_id=workspace.id,namespace='inspection',entity_type='site',external_id='other-workspace',
            schema_capability_id=other['schema_capability_id'],data=other['data']);db.add(foreign);db.commit()
        target={'id':foreign.id,'version':foreign.version}
    assert bind(client,ch,target,version=r.json()['version']).status_code==404


def test_daily_includes_manual_upload_automatic_failures_revisions_and_no_empty_success(client,media_runtime):
    assert client.post('/api/media/daily/reconcile').json()==[]
    ch,install,obj=prepared(client);bind(client,ch,obj);finish(client,capture(client,ch,install).json())
    # Upload is a separate manual-data source, never dropped from daily collection.
    from tests.test_local_video import RAW,CFG
    import json
    response=client.post('/api/videos',data={'installation_id':install['id'],'configuration':json.dumps(CFG)},files={'file':('upload.mp4',RAW,'video/mp4')})
    assert response.status_code==201
    from generic_agents.worker import process_once
    process_once(response.json()['job_id'],deliver=False)
    ch=client.get('/api/media/channels').json()[0]
    failed=capture(client,ch,install).json()
    from generic_agents.models import StreamSession
    with Session(engine()) as db:
        row=db.get(StreamSession,failed['id']);row.status='failed';row.data={**row.data,'monitor_id':'synthetic-policy-test'};db.commit()
    result=client.post('/api/media/daily/reconcile').json();assert len(result)==2
    source=next(x for x in result if x['source_key'].startswith('channel:'))
    assert source['manual_attempts']==2 and source['automatic_attempts']==1
    assert source['status']=='incomplete' and len(source['failed_or_partial_ids'])==1 and source['business_tasks']==1
    upload=next(x for x in result if x['source_key']=='uploaded_video');assert upload['uploads']==1 and upload['unique_originals']==1
    assert source['source_observation_day_verified'] is False and upload['status']=='collected_with_limits'
    again=client.post('/api/media/daily/reconcile').json();assert result==again
    with Session(engine()) as db:
        assert len(db.scalars(select(Memory).where(Memory.data['type'].as_string()=='video_daily_summary')).all())==2
        for daily in db.scalars(select(VideoDaily)):assert db.get(Memory,daily.data['data_memory_id']).data['receipt_hash']==daily.data['receipt_hash']


def test_old_pending_upload_receipt_is_revised_after_late_processing(client,media_runtime):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from generic_agents.models import VideoRun
    from generic_agents.worker import process_once
    from tests.test_local_video import RAW,CFG
    import json
    install=ready(client)
    response=client.post('/api/videos',data={'installation_id':install['id'],'configuration':json.dumps(CFG)},files={'file':('delayed.mp4',RAW,'video/mp4')})
    run=response.json();first=client.post('/api/media/daily/reconcile').json()[0];assert len(first['pending_ids'])==1
    with Session(engine()) as db:
        row=db.get(VideoRun,run['id']);row.created_at-=timedelta(days=10)
        daily=db.get(VideoDaily,first['id']);daily.business_day=str(row.created_at.astimezone(ZoneInfo('Asia/Shanghai')).date());db.commit()
    process_once(run['job_id'],deliver=False)
    result=client.post('/api/media/daily/reconcile').json()[0]
    assert result['id']==first['id'] and result['version']==first['version']+1
    assert not result['pending_ids'] and result['status']=='collected_with_limits' and result['saved_frames']==9
