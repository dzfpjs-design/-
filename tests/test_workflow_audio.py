"""Registered actual ASR in an owned business task; no sample-result fallback."""
import copy,json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import AudioRun,Memory,Invocation,CloudCall,Evidence
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.worker import process_once
from generic_agents.instance_compiler import signature_value
from tests.test_audio import upload
from tests.test_instance_workspace import configuration,prepared,object_row,complete
from tests.test_workflow_abilities import enable,finish_job

def configured(client):
    source=upload(client).json();assert process_once(source['job_id'],deliver=False);source=client.get('/api/audio/'+source['id']).json();assert source['status']=='succeeded'
    response=client.post('/api/audio/'+source['id']+'/register-business',json={'version':source['version']});assert response.status_code==200,response.text;cap=response.json()
    assert client.post('/api/audio/'+source['id']+'/register-business',json={'version':source['version']}).json()==cap
    c=configuration();flow=c['flows']['资料核验'];flow['steps'][0].update(name='本地音频转写',agent='多模感知智能体',role='',execution={'capability_id':cap['capability_id'],'automation_action':'observe','arguments':{},'bindings':{}});c['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'};flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    release,_,check=prepared(client,c);assert release['available_business_ids'],check;enable(client,release)
    base='/api/workspace/instances/'+release['instance_id'];candidate=client.get(base+'/audio-candidates').json()[0]
    body={'source_run_id':source['id'],'source_snapshot_hash':candidate['snapshot_hash'],'request_id':str(uuid4())}
    response=client.post(base+'/input-audio',json=body);assert response.status_code==201,response.text;input_audio=response.json();assert client.post(base+'/input-audio',json=body).json()==input_audio
    assert client.get(base+'/input-audio/requests/'+body['request_id'],params={'source_snapshot_hash':body['source_snapshot_hash']}).json()==input_audio
    assert client.get(base+'/input-audio/requests/'+body['request_id'],params={'source_snapshot_hash':'0'*64}).status_code==409
    obj=object_row(client,release);value={'release_id':release['id'],'business_id':release['available_business_ids'][0],'entity_ids':[obj['id']],'title':'公开音频实际业务核查','audio_ids':[input_audio['id']],'request_id':str(uuid4())}
    return source,cap,release,input_audio,value

def test_actual_registered_audio_task_freezes_input_and_saves_real_receipts(client):
    source,cap,release,source_input,value=configured(client);url='/api/workspace/instances/'+release['instance_id']+'/tasks'
    assert client.post(url,json={**value,'audio_ids':[]}).status_code==422
    # Resource human corrections cannot replace the task's own machine execution.
    r=client.put('/api/audio/'+source['id']+'/review',json={'version':source['version'],'text':'资源页人工文字不得冒充新业务推理','confirmed':True});assert r.status_code==200
    response=client.post(url,json=value);assert response.status_code==201,response.text;task=finish_job(client,response.json());assert task['status']=='workflow_waiting',task['workflow_runtime'].get('automatic')
    receipt=task['workflow_runtime']['receipts'][0];output=receipt['output'];result=output['audio_transcription'];run_id=output['audio_run_id'];base='/api/workspace/tasks/'+task['id']+'/audio/'+run_id
    assert task['audio_snapshots']==[source_input] and receipt['audio_snapshots']==[source_input] and receipt['candidate_only']
    assert run_id!=source['id'] and '9点' in result['transcription']['text'] and '资源页' not in result['transcription']['text'] and result['cloud_calls']==0
    assert client.post(url,json=value).json()['id']==task['id'] and client.get(base+'/original').status_code==200
    with Session(engine()) as db:
        ev=db.get(Evidence,source['evidence_id']);path=settings().storage_dir/ev.storage_key;raw=path.read_bytes();path.write_bytes(b'broken')
    assert complete(client,task,note='损坏原件不能推进').status_code==409 and client.get(base+'/export').status_code==409
    path.write_bytes(raw)
    response=complete(client,task,note='已核对公开原音频及转写，专业术语和业务含义待核实');assert response.status_code==200,response.text;task=response.json()
    assert client.get(base+'/export').json()['review_receipts'][-1]['note']=='已核对公开原音频及转写，专业术语和业务含义待核实'
    with Session(engine()) as db:
        m=db.get(Memory,task['workflow_runtime']['data_receipt_id']);assert m.status=='candidate' and m.data['audio_snapshots']==[source_input] and not m.data['entity_state_updated']
        assert len(db.scalars(select(Invocation)).all())==1 and not db.scalars(select(CloudCall)).all()
    row=client.get('/api/audio/'+run_id).json();assert client.post('/api/audio/'+run_id+'/retry',json={'version':row['version']}).status_code==409
    assert client.put('/api/audio/'+run_id+'/review',json={'version':row['version'],'text':'绕过业务核查','confirmed':True}).status_code==409
    assert client.get('/api/instances/'+release['instance_id']+'/objects').json()[0]['version']==1

def test_audio_source_scope_duplicate_and_cancel(client):
    source,cap,release,source_input,value=configured(client);url='/api/workspace/instances/'+release['instance_id']+'/tasks'
    assert client.post(url,json={**value,'audio_ids':[source_input['id'],source_input['id']]}).status_code==422
    other,_,_=prepared(client,configuration());enable(client,other);obj=object_row(client,other)
    assert client.post('/api/workspace/instances/'+other['instance_id']+'/tasks',json={**value,'release_id':other['id'],'business_id':other['available_business_ids'][0],'entity_ids':[obj['id']]}).status_code==409
    response=client.post(url,json=value);assert response.status_code==201;task=response.json();job=task['workflow_runtime']['automatic']['job_id']
    assert client.post('/api/workspace/tasks/'+task['id']+'/cancel',json={'version':task['version'],'reason':'取消公开语音工程任务','retained_note':'音频依据保留'}).status_code==200
    assert process_once(job,deliver=False)
    with Session(engine()) as db:assert not db.scalars(select(Invocation)).all() and len(db.scalars(select(AudioRun)).all())==1

def test_audio_manifest_rejects_changed_forced_bindings(client):
    from generic_agents.workflow_audio import manifest
    value=manifest().model_dump();value['execution']['input_bindings']['audio_ref']='object_attributes'
    response=client.post('/api/capabilities',json=value);assert response.status_code==201,response.text;cap=response.json()
    response=client.post('/api/capabilities/'+cap['id']+'/validate',json={'expected_version':cap['version']});assert response.status_code==422,response.text
