"""Real local quality, neural comparison and independent business data reception.

The synthetic inputs prove software behavior, not classification or domain accuracy.
"""
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,GeoPair,Entity,Memory
from generic_agents.worker import process_once,deliver_events
from tests.test_geo_quality_v351 import source_files,prepared
from tests.test_workflow_learned_geo_pairs import business
from tests.test_learned_geo_pairs import configured,rasters
from tests.test_workflow_scenes import attach
from tests.test_workflow_abilities import finish_job


def test_quality_sources_survive_real_business_and_independent_result_reception(client,business,source_files):
    _,_,_,release,_,creation=business
    value,_=prepared(client,source_files)
    response=client.post('/api/geo/quality/assessments',json=value);assert response.status_code==201,response.text
    identity=response.json()['id'];assert process_once(identity,deliver=False)
    report=client.get('/api/geo/quality/assessments/'+identity).json();assert report['status']=='succeeded',report
    deliver_events(event_kind='data.geo_quality_report_v352')
    quality_receipt=client.get('/api/geo/quality/assessments/'+identity).json()['data_reception']
    assert quality_receipt['state']=='received'
    scenes={}
    for item in report['result']['derived_scenes']:
        row=client.get('/api/geo/scenes/'+item['scene_id']).json();assert process_once(row['job_id'],deliver=False)
        scenes[item['side']]=client.get('/api/geo/scenes/'+row['id']).json()
    linked=[]
    for side in ('before','after'):
        r=attach(client,release,scenes[side],roi=[0,0,64,64]);assert r.status_code==201,r.text
        linked.append(r.json())
    body={**creation,'comparison_scene_ids':[x['id'] for x in linked],'request_id':str(uuid4()),'title':'质量掩膜与本地模型业务测试'}
    base='/api/workspace/instances/'+release['instance_id'];r=client.post(base+'/tasks',json=body);assert r.status_code==201,r.text
    task=finish_job(client,r.json());assert task['status']=='workflow_waiting',task
    receipt=task['workflow_runtime']['receipts'][0];output=receipt['output'];run=output['geo_pair_id']
    result=output['spatial_comparison'];assert result['learned_model'] is True and result['cloud_calls']==0
    assert result['unknown_pixels']==report['result']['native']['unknown_or_excluded_pixels']
    assert result['cloud_shadow_screening']=='not_applied'
    with Session(engine()) as db:
        pair=db.get(GeoPair,run);assert all(x['quality_origin_v351']['quality_job_id']==identity
            and x['quality_origin_v351']['data_reception_hash']==quality_receipt['receipt_hash'] for x in pair.data['sources'])
        jobs=list(db.scalars(select(Job).where(Job.kind=='sync_native_receipts',Job.data['original_task_id'].as_string()==task['id'])))
        assert len(jobs)==1 and jobs[0].status=='queued';qid=jobs[0].id
        original=db.get(Entity,creation['entity_ids'][0]).version
    assert process_once(qid,deliver=False);deliver_events()
    listing=client.get(base+'/native-receipts');assert listing.status_code==200,listing.text
    item=next(x for x in listing.json()['records'] if x.get('record',{}).get('source_key')=='GeoPair:'+run)
    assert item['data_received'] and not item['integrity_error'],item
    reception=item['data_reception'];assert reception['learned_model'] is True and reception['candidate_only'] is True
    assert not reception['physical_state_verified'] and not reception['entity_state_updated']
    with Session(engine()) as db:
        assert db.get(Entity,creation['entity_ids'][0]).version==original
        assert db.scalar(select(Memory).where(Memory.data['data_reception']['run_id'].as_string()==run)) is not None
