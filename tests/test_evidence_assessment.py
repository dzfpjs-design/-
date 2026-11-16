"""Real persisted object scopes, rasters, worker checks and manual gap handoff."""
import copy
from uuid import uuid4
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import EvidenceAssessment,Entity,Evidence,Memory,CloudCall,Membership
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.worker import process_once
from tests.test_geo_codec import scenes,pytestmark
from tests.test_geo_pairs import pairs
from tests.test_workflow_geo_pairs import configured
from tests.test_workflow_abilities import finish_job
from tests.test_instance_workspace import configuration,prepared,enable,object_row,task_row,complete


def request(release,entities,**kw):
 return {'instance_id':release['instance_id'],'entity_ids':entities,'purpose':'核对公开资料是否足以判断当前状态','goal':'current_state','required_modalities':['imagery','audio','video'],'lookback_hours':24,'request_id':str(uuid4()),**kw}

def run(client,body):
 r=client.post('/api/evidence-assessments',json=body);assert r.status_code==201,r.text;r=r.json()
 assert process_once(r['job_id'],deliver=False)
 result=client.get('/api/evidence-assessments/'+r['id']);assert result.status_code==200,result.text;return result.json()

def paired(client,pairs):
 a,b,cap,release,sources,body=configured(client,pairs)
 t=client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json=body).json();t=finish_job(client,t)
 return a,release,body,t


def test_real_pair_unknown_time_coverage_review_and_missing_modalities(client,pairs):
 a,r,body,t=paired(client,pairs);result=run(client,request(r,body['entity_ids']))
 assert result['status']=='completed';value=result['result'];assert len(value['records'])==1
 proof=value['records'][0];assert proof['integrity_verified'] and proof['metrics']['unknown_pixels']>0 and proof['review_state']=='waiting'
 gaps=value['objects'][0]['gaps'];assert {'source_time','coverage','domain_quality','human_review','missing_saved_processing'}<={g['kind'] for g in gaps}
 assert {g['modality'] for g in gaps if g['kind']=='missing_saved_processing'}=={'audio','video'}
 assert not value['objects'][0]['current_state_sufficient'] and value['objects'][0]['state_conclusion'] is None and value['cloud_calls']==0
 assert client.get('/api/evidence-assessments/'+result['id']+'/freshness').json()['metadata_versions_unchanged']
 completed=complete(client,t,note='已核对公开像素候选，不作为当前状态结论');assert completed.status_code==200,completed.text
 assert not client.get('/api/evidence-assessments/'+result['id']+'/freshness').json()['metadata_versions_unchanged']
 again=run(client,request(r,body['entity_ids'],parent_id=result['id']));assert again['result']['records'][0]['review_state']=='confirmed'
 assert client.get('/api/evidence-assessments/'+result['id']).json()==result
 with Session(engine()) as db:assert db.scalar(select(CloudCall.id)) is None


def test_real_original_damage_is_a_gap_not_current_state_success(client,pairs):
 a,r,body,t=paired(client,pairs)
 with Session(engine()) as db:e=db.get(Evidence,a['evidence_id']);p=settings().storage_dir/e.storage_key;original=p.read_bytes();p.write_bytes(b'broken')
 result=run(client,request(r,body['entity_ids'],required_modalities=['imagery']));assert result['status']=='completed'
 assert not result['result']['records'][0]['integrity_verified'];assert any(g['kind']=='integrity' for g in result['result']['objects'][0]['gaps'])
 p.write_bytes(original)
 recovered=run(client,request(r,body['entity_ids'],required_modalities=['imagery'],parent_id=result['id']));assert recovered['result']['records'][0]['integrity_verified']
 assert not recovered['result']['objects'][0]['current_state_sufficient']


def test_requests_idempotent_cancel_and_actual_object_change_worker_failure(client):
 r,_,_=prepared(client,configuration());enable(client,r);obj=object_row(client,r);body=request(r,[obj['id']])
 first=client.post('/api/evidence-assessments',json=body).json();assert client.post('/api/evidence-assessments',json=body).json()==first
 changed={**body,'lookback_hours':48};assert client.post('/api/evidence-assessments',json=changed).status_code==409
 cancel=client.post('/api/evidence-assessments/'+first['id']+'/cancel',json={'version':first['version']});assert cancel.status_code==200
 assert process_once(first['job_id'],deliver=False);assert client.get('/api/evidence-assessments/'+first['id']).json()['status']=='cancelled'
 pending=client.post('/api/evidence-assessments',json={**body,'request_id':str(uuid4())}).json()
 with Session(engine()) as db:o=db.get(Entity,obj['id']);o.data={**o.data,'changed_after_request':'source version mutation'};o.version+=1;db.commit()
 assert process_once(pending['job_id'],deliver=False);failed=client.get('/api/evidence-assessments/'+pending['id']).json();assert failed['status']=='failed' and failed['request']['purpose']==body['purpose'] and 'result' not in failed


def test_two_objects_no_processing_does_not_mean_no_anomaly_and_review_handoff(client):
 r,_,_=prepared(client,configuration());enable(client,r);obj=object_row(client,r);other=object_row(client,r,1)
 result=run(client,request(r,[obj['id'],other['id']],required_modalities=['imagery']))
 assert len(result['result']['objects'])==2 and all(x['status']=='insufficient_for_current_state' for x in result['result']['objects'])
 value={'release_id':r['id'],'business_id':r['available_business_ids'][0],'entity_ids':[obj['id'],other['id']],'title':'核查缺少采集资料','request_id':str(uuid4()),'assessment_id':result['id']}
 task=client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=value);assert task.status_code==201,task.text;task=task.json()
 history=client.get('/api/evidence-assessments/'+result['id']).json()['sent_tasks'];assert len(history)==1 and history[0]['task_id']==task['id']
 assert task['evidence_assessment']['result_hash']==result['result_hash'] and task['evidence_assessment']['historical_not_current_state']
 assert client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=value).json()['id']==task['id']
 assert client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json={**value,'request_id':str(uuid4()),'entity_ids':[obj['id']]}).status_code==409
 step=complete(client,task,note='需要现场核实采集时间与范围');assert step.status_code==200
 task=step.json();step=complete(client,task,note='已保存资料补充要求，尚不能判断当前状态');assert step.status_code==200
 assert step.json()['evidence_assessment']==task['evidence_assessment']


def test_automatic_handoff_and_cross_instance_objects_are_blocked(client,pairs):
 a,r,body,t=paired(client,pairs);result=run(client,request(r,body['entity_ids'],required_modalities=['imagery']))
 task={**body,'request_id':str(uuid4()),'assessment_id':result['id']};response=client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=task);assert response.status_code==409
 c=configuration();c['instanceProfile']['name']='另一独立实例';other,_,_=prepared(client,c);enable(client,other);obj=object_row(client,other)
 assert client.post('/api/evidence-assessments',json=request(r,[obj['id']])).status_code==409


def test_receipt_tamper_readonly_role_and_validation(client):
 r,_,_=prepared(client,configuration());enable(client,r);obj=object_row(client,r);body=request(r,[obj['id']]);result=run(client,body)
 assert client.post('/api/evidence-assessments',json={**body,'required_modalities':['video','video'],'request_id':str(uuid4())}).status_code==422
 assert client.post('/api/evidence-assessments',json={**body,'lookback_hours':True,'request_id':str(uuid4())}).status_code==422
 with Session(engine()) as db:row=db.get(EvidenceAssessment,result['id']);value=copy.deepcopy(row.data);value['result']['objects'][0]['current_state_sufficient']=True;row.data=value;db.commit()
 assert client.get('/api/evidence-assessments/'+result['id']).status_code==409
 # Authentication refresh establishes actual viewer membership; no fake role headers.
 with Session(engine()) as db:m=db.scalar(select(Membership).where(Membership.workspace_id==client.headers['X-Workspace-ID']));m.role='viewer';db.commit()
 assert client.post('/api/evidence-assessments',json={**body,'request_id':str(uuid4())}).status_code==403


def test_actual_native_audio_chain_is_saved_evidence_not_verified_acquisition_clock(client):
 from tests.test_workflow_segmented_audio import configured as configured_audio
 task,original,cap=configured_audio(client);task=finish_job(client,task)
 releases=client.get('/api/instances/'+task['instance_id']+'/releases').json();release=next(r for r in releases if r['id']==task['instance_release_id'])
 result=run(client,request(release,task['entity_ids'],required_modalities=['audio']))
 assert result['status']=='completed',result.get('error')
 records=result['result']['records'];assert len(records)>=2 and all(r['integrity_verified'] for r in records)
 segmented=next(r for r in records if r['kind']=='segments');assert segmented['metrics']['saved_asr_segments']==2 and not segmented['source_time_verified']
 assert result['result']['objects'][0]['status']=='insufficient_for_current_state'
 assert any(g['kind']=='source_time' for g in result['result']['objects'][0]['gaps'])


def test_corrupt_derived_result_keeps_original_conditions_and_recovers_as_new_run(client):
 r,_,_=prepared(client,configuration());enable(client,r);obj=object_row(client,r);body=request(r,[obj['id']]);old=run(client,body)
 with Session(engine()) as db:
  row=db.get(EvidenceAssessment,old['id']);value=copy.deepcopy(row.data);value['result']['objects'][0]['processing_records']=999;row.data=value;db.commit()
 listed=client.get('/api/evidence-assessments',params={'instance_id':r['instance_id']});assert listed.status_code==200
 damaged=listed.json()[0];assert damaged['status']=='blocked_integrity' and 'result' not in damaged and damaged['request']==old['request']
 assert client.get('/api/evidence-assessments/'+old['id']+'/export').status_code==409
 new=run(client,{**body,'request_id':str(uuid4()),'parent_id':old['id']});assert new['id']!=old['id'] and new['result']['objects'][0]['processing_records']==0
 with Session(engine()) as db:assert db.get(EvidenceAssessment,old['id']).data['result']['objects'][0]['processing_records']==999
