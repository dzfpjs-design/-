"""The dedicated quality worker never claims unrelated or foreign jobs."""
import copy
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Workspace,Job,GeoScene
from generic_agents.geo_quality_scoped_worker_v353 import tick,scope
from tests.test_geo_quality_v351 import source_files,prepared
from tests.test_learned_geo_pairs import configured,rasters


def test_scoped_quality_tick_processes_real_quality_and_leaves_other_jobs(client,source_files,account):
    body,_=prepared(client,source_files);r=client.post('/api/geo/quality/assessments',json=body);assert r.status_code==201,r.text
    identity=r.json()['id'];workspace=account[2]
    with Session(engine()) as db:
        other=Workspace(name='Separate workspace');db.add(other);db.flush()
        foreign=Job(workspace_id=other.id,kind='prepare_geo_quality_v352',idempotency_key='foreign_'+uuid4().hex,data={})
        unrelated=Job(workspace_id=workspace,kind='check_native_business_goal',idempotency_key='unrelated_'+uuid4().hex,data={})
        db.add_all([foreign,unrelated]);db.flush();ids=[foreign.id,unrelated.id];db.commit()
    assert tick(workspace)>=3
    report=client.get('/api/geo/quality/assessments/'+identity).json()
    assert report['status']=='succeeded' and report['data_reception']['state']=='received'
    assert all(client.get('/api/geo/scenes/'+x['scene_id']).json()['status']=='ready' for x in report['result']['derived_scenes'])
    scenes={x['side']:client.get('/api/geo/scenes/'+x['scene_id']).json() for x in report['result']['derived_scenes']}
    before,after=scenes['before'],scenes['after'];body={'request_id':'quality_scoped_pair_1234',
        'before_scene_id':before['id'],'after_scene_id':after['id'],'before_version':before['version'],
        'after_version':after['version'],'roi':[0,0,64,64],'alignment':'same_grid','threshold':10,'rgb_comparability_confirmed':True}
    pair=client.post('/api/geo/pairs',json=body);assert pair.status_code==201,pair.text
    assert tick(workspace)>=1
    done=client.get('/api/geo/pairs/'+pair.json()['id']).json();assert done['status']=='succeeded',done
    assert done['result']['unknown_pixels']==report['result']['native']['unknown_or_excluded_pixels']
    with Session(engine()) as db:
        assert all(db.get(Job,x).status=='queued' and db.get(Job,x).attempts==0 for x in ids)
        original=db.get(GeoScene,report['result']['derived_scenes'][0]['scene_id']);data=copy.deepcopy(original.data)
        fake=GeoScene(workspace_id=workspace,evidence_id=original.evidence_id,status='queued',data=data);db.add(fake);db.flush()
        job=Job(workspace_id=workspace,kind='parse_geo_scene',idempotency_key='fake_'+uuid4().hex,
            data={'scene_id':fake.id,'generation':data['generation'],'actor_id':db.get(Job,identity).data['actor_id']})
        db.add(job);db.flush();fake.data={**data,'job_id':job.id};fake_job=job.id;db.commit()
    tick(workspace)
    with Session(engine()) as db:
        assert db.get(Job,fake_job).status=='queued' and db.get(Job,fake_job).attempts==0
        assert all(db.get(Job,x).status=='queued' and db.get(Job,x).attempts==0 for x in ids)


def test_worker_rejects_invalid_scope_before_database():
    for value in (None,'','all','{00000000-0000-0000-0000-000000000001}'):
        with pytest.raises(ValueError):scope(value)


def test_scoped_worker_runs_real_local_model_for_received_quality_pair(client,source_files,configured,account):
    body,_=prepared(client,source_files);r=client.post('/api/geo/quality/assessments',json=body);assert r.status_code==201,r.text
    identity=r.json()['id'];tick(account[2]);report=client.get('/api/geo/quality/assessments/'+identity).json()
    assert report['data_reception']['state']=='received'
    scenes={x['side']:client.get('/api/geo/scenes/'+x['scene_id']).json() for x in report['result']['derived_scenes']}
    before,after=scenes['before'],scenes['after'];model={**configured,'request_id':'quality_model_scoped_123',
        'before_scene_id':before['id'],'after_scene_id':after['id'],'before_version':before['version'],
        'after_version':after['version'],'roi':[0,0,64,64]}
    result=client.post('/api/geo/learned-pairs',json=model);assert result.status_code==201,result.text
    assert tick(account[2])>=1
    done=client.get('/api/geo/pairs/'+result.json()['id']).json();assert done['status']=='succeeded',done
    assert done['result']['learned_model'] is True and done['result']['cloud_calls']==0
    assert done['result']['unknown_pixels']==report['result']['native']['unknown_or_excluded_pixels']
