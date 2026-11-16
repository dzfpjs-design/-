"""Native ASR/VAD and registered video receipts reused without a second inference."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import Invocation,CloudCall,Job
from generic_agents.db import engine
from generic_agents.worker import process_once
from tests.test_workflow_audio import configured as audio
from tests.test_workflow_segmented_audio import configured as segments
from tests.test_workflow_abilities import finish_job
from tests.test_instance_workspace import complete
from tests.test_instance_perception import linked,pending
from tests.test_media import media_runtime
from tests.test_local_specialists import mocked_runtime

def sync(client,base):
    r=client.get(base+'/native-receipts');assert r.status_code==200,r.text
    result=client.post(base+'/native-receipts/sync',json={'preview_hash':r.json()['preview_hash']});assert result.status_code==200,result.text
    return client.get(base+'/native-receipts').json()

def test_actual_asr_machine_text_is_preserved_and_not_replaced_by_review(client):
    source,cap,release,attached,body=audio(client);base='/api/workspace/instances/'+release['instance_id']
    response=client.post(base+'/tasks',json=body);assert response.status_code==201,response.text;task=finish_job(client,response.json())
    value=sync(client,base);assert len(value['records'])==1
    row=value['records'][0];assert row['record']['modality']=='audio' and '9点' in row['record']['attributes']['机器转写文字'] and row['data_received']
    assert complete(client,task,note='核对公开转写，不替代机器文字').status_code==200
    value=sync(client,base);assert len(value['records'])==1 and value['records'][0]['record']['review_state']=='confirmed'
    with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1 and not db.scalars(select(CloudCall)).all()

def test_actual_task_vad_and_segments_enter_distinct_common_receipts(client):
    task,original,cap=segments(client);task=finish_job(client,task);base='/api/workspace/instances/'+task['instance_id']
    listing=sync(client,base);records=[r['record'] for r in listing['records']]
    assert len(records)==2 and {r['kind'] for r in records}=={'activity','segments'}
    assert any('分段机器转写文字' in r['attributes'] for r in records) and all(r['candidate_only'] and r['cloud_calls']==0 for r in records)

def test_bound_video_auto_queue_uses_original_video_frames_without_another_inference(client,media_runtime,mocked_runtime):
    installation,channel,release,obj,body,binding=linked(client);task,video=pending(client,channel,installation)
    base='/api/workspace/instances/'+release['instance_id']
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.kind=='sync_native_receipts',Job.data['original_video_run_id'].as_string()==video['id']));assert job is not None;identity=job.id
    assert process_once(identity,deliver=False)
    listing=sync(client,base);assert len(listing['records'])==1
    row=listing['records'][0];assert row['record']['modality']=='video' and row['record']['attributes']['已保存画面数']>0 and row['data_received']
    with Session(engine()) as db:assert not db.scalars(select(CloudCall)).all()
