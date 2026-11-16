"""Real local rules, isolated DB: report integrity is not physical truth."""
import copy
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Observation,Job,Entity,ObjectState,CloudCall
from generic_agents.worker import process_once,deliver_events
from tests.test_mixed_evidence import setup as extracted_setup


def setup(client):
    # These tests assess already received records. Keep the shared extraction
    # fixture unacknowledged so V245's pending-reception tests remain honest.
    result=extracted_setup(client)
    deliver_events(observation_id=result[2]['id'],event_kind='observation.data')
    return result


def state(client,r,obj):
    response=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state")
    assert response.status_code==200,response.text
    return response.json()['state']


def test_verified_report_is_stable_persisted_and_not_physical_truth(client):
    r,obj,obs,_=setup(client)
    first=state(client,r,obj);again=state(client,r,obj)
    receipt=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state-records/{obs['id']}").json()
    assert receipt['original_attributes']==receipt['extracted_attributes']=={'公开登记值':27.5}
    assert receipt['receipt_verified'] and not receipt['physical_state_verified']
    assert first['fields']['公开登记值']['value']==27.5
    assert first['state_hash']==again['state_hash'] and first['state_version']==again['state_version']==1
    assert first['evaluated_at']==again['evaluated_at']
    assert not first['requirements_defined'] and '名称' not in first['required_fields']
    assert first['physical_state']=='unknown' and not first['current_state_verified']
    assert not first['fields']['公开登记值']['source_time_verified']
    with Session(engine()) as db:
        saved=db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id']))
        assert saved.data['state_hash']==first['state_hash'] and saved.version==1
        assert db.get(Entity,obj['id']).version==obj['version']
        assert not db.scalars(select(CloudCall)).all()


def test_corrupt_result_is_excluded_and_recovery_retains_history(client):
    r,obj,obs,_=setup(client);first=state(client,r,obj)
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);original=copy.deepcopy(row.data)
        altered=copy.deepcopy(original);altered['extracted']['attributes']['公开登记值']=999
        row.data=altered;db.commit()
    broken=state(client,r,obj)
    assert client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state-records/{obs['id']}").status_code==409
    assert not broken['fields'] and broken['status']=='insufficient'
    assert broken['excluded_records'][0]['reason']=='integrity_failed'
    assert broken['state_version']==2 and broken['history'][0]['state_hash']==first['state_hash']
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);row.data=original;db.commit()
    recovered=state(client,r,obj)
    assert recovered['fields']['公开登记值']['value']==27.5 and recovered['state_version']==3
    assert len(recovered['history'])==2
    assert state(client,r,obj)['state_version']==3


def test_same_clock_conflict_does_not_choose_value_or_clear_acquisition_gap(client):
    r,obj,obs,body=setup(client)
    field_key=r['snapshot']['object_types'][0]['fields'][0]['key']
    for label in ['已登记甲','已登记乙']:
        second={**body,'source_record_id':str(uuid4()),'attributes':{field_key:label}}
        response=client.post('/api/observations',json=second);assert response.status_code==201,response.text
        with Session(engine()) as db:
            job=db.scalar(select(Job).where(Job.data['observation_id'].as_string()==response.json()['id']));identity=job.id
        assert process_once(identity,deliver=False)
        deliver_events(observation_id=response.json()['id'],event_kind='observation.data')
    result=state(client,r,obj);field=result['fields'][field_key]
    assert result['status']=='conflict' and field['value'] is None and field['conflicted']
    assert sorted(c['value'] for c in field['candidates'])==['已登记乙','已登记甲']
    assert {'field':field_key,'reason':'conflict'} in result['gaps']
    # An explicit monitor must still propose a bounded supplement, not treat conflict as sufficient.
    from datetime import datetime,timezone,timedelta
    proposed=client.post('/api/monitor-policies',json={'title':'核对登记冲突','entity_ids':[obj['id']],
        'source_ids':[body['source_id']],'fields':[field_key],'max_age_seconds':3600,
        'cooldown_seconds':3600,'max_acquisitions_per_day':2,'max_duration_seconds':100,
        'valid_until':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()})
    assert proposed.status_code==201,proposed.text
    monitor=proposed.json()
    approved=client.post(f"/api/monitor-policies/{monitor['id']}/approve",
        json={'expected_version':monitor['version'],'reason':'限定字段和次数的补充核查'})
    assert approved.status_code==200,approved.text
    response=client.post(f"/api/monitor-policies/{monitor['id']}/scan")
    assert response.status_code==200 and response.json()['acquisition_ids']


def test_original_clock_tamper_blocks_values_and_other_instance_object(client):
    r,obj,obs,_=setup(client)
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);data=copy.deepcopy(row.data)
        data['original_request']['observed_at']='2001-01-01T00:00:00+00:00';row.data=data
        foreign=Entity(workspace_id=db.get(Entity,obj['id']).workspace_id,namespace='other_instance',entity_type='place',
            schema_capability_id=r['package_id'],external_id=str(uuid4()),data={})
        db.add(foreign);db.flush();foreign_id=foreign.id;db.commit()
    assert not state(client,r,obj)['fields']
    assert client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{foreign_id}/state").status_code==409
    assert client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{foreign_id}/state-records/{obs['id']}").status_code==409


def test_more_recent_valid_report_can_recover_from_rejected_historical_value(client):
    from datetime import datetime,timezone,timedelta
    r,obj,obs,body=setup(client)
    current=client.get('/api/overview').json()['observations'][0]
    response=client.post(f"/api/observations/{obs['id']}/verify",json={'expected_version':current['version'],
        'decision':'rejected','reason':'原登记撤回，不判断现场状态'})
    assert response.status_code==200,response.text
    assert state(client,r,obj)['status']=='insufficient'
    clock=datetime.now(timezone.utc)-timedelta(seconds=5)
    second={**body,'source_record_id':str(uuid4()),'observed_at':clock.isoformat(),
        'business_day':str((clock+timedelta(hours=8)).date())}
    new=client.post('/api/observations',json=second);assert new.status_code==201,new.text
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['observation_id'].as_string()==new.json()['id']));identity=job.id
    assert process_once(identity,deliver=False)
    deliver_events(observation_id=new.json()['id'],event_kind='observation.data')
    result=state(client,r,obj)
    assert result['status']=='observed' and result['excluded_records']
    assert result['physical_state']=='unknown' and not result['gaps']


def test_concurrent_first_assessments_share_one_durable_version(client,account):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from generic_agents.security import Principal
    from generic_agents.perception import object_state
    r,obj,_,_=setup(client);barrier=Barrier(2)
    with Session(engine()) as db:
        who=db.get(Observation,db.scalar(select(Observation.id))).data['submitted_by']
    p=Principal(who,account[2],'admin','test')
    def read():
        with Session(engine()) as db:
            barrier.wait(timeout=10)
            value=object_state(db,p,obj['id']);db.commit();return value
    with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(lambda _:read(),range(2)))
    assert values[0]['state_version']==values[1]['state_version']==1
    assert values[0]['state_hash']==values[1]['state_hash']
    with Session(engine()) as db:
        assert len(db.scalars(select(ObjectState).where(ObjectState.entity_id==obj['id'])).all())==1


def test_drill_down_rechecks_snapshot_after_original_review_changes(client):
    r,obj,obs,_=setup(client);before=state(client,r,obj)
    current=client.get('/api/overview').json()['observations'][0]
    reviewed=client.post(f"/api/observations/{obs['id']}/verify",json={'expected_version':current['version'],
        'decision':'rejected','reason':'原登记撤回，不再采用'})
    assert reviewed.status_code==200,reviewed.text
    response=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state-records/{obs['id']}",
        params={'expected_state_hash':before['state_hash']})
    assert response.status_code==409 and '依据已变化' in response.text
    assert state(client,r,obj)['state_version']==2 and not state(client,r,obj)['fields']


def test_confirmed_monitor_freshness_is_applied_per_field(client):
    from datetime import datetime,timezone,timedelta
    from tests.test_vertical_slice import manifest,publish,entity
    from generic_agents.security import Principal
    from generic_agents.perception import object_state
    package=publish(client,manifest());obj=entity(client,package)
    source=publish(client,manifest('source',name='declared_monitor_source',allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[],'timezone':'UTC'}))
    clock=datetime.now(timezone.utc)-timedelta(minutes=30)
    posted=client.post('/api/observations',json={'source_id':source['id'],'source_record_id':str(uuid4()),
        'entity_id':obj['id'],'observed_at':clock.isoformat(),'business_day':str(clock.date()),
        'attributes':{'name':'公开登记','temperature':27.5},'extractor_id':source['id'],'startup_mode':'manual'})
    assert posted.status_code==201,posted.text
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['observation_id'].as_string()==posted.json()['id']));identity=job.id;who=posted.json()['data']['submitted_by']
    assert process_once(identity,deliver=False)
    for field,age in [('name',600),('temperature',3600)]:
        proposal=client.post('/api/monitor-policies',json={'title':'核查'+field,'entity_ids':[obj['id']],
            'source_ids':[source['id']],'fields':[field],'max_age_seconds':age,'cooldown_seconds':3600,
            'max_acquisitions_per_day':2,'max_duration_seconds':100,
            'valid_until':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()})
        assert proposal.status_code==201,proposal.text
        row=proposal.json();approved=client.post(f"/api/monitor-policies/{row['id']}/approve",
            json={'expected_version':row['version'],'reason':'限定字段核查期限'})
        assert approved.status_code==200,approved.text
    with Session(engine()) as db:
        result=object_state(db,Principal(who,obj['workspace_id'],'admin','test'),obj['id']);db.commit()
    assert result['field_freshness_seconds']=={'name':600,'temperature':3600}
    assert {'field':'name','reason':'stale'} in result['gaps']
    assert not any(g['field']=='temperature' for g in result['gaps'])
