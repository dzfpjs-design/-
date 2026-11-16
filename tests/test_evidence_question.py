"""Cloud fixtures verify contracts and payment checkpoint handling, not model quality."""
import copy,json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import EvidenceQuestion,EvidenceAssessment,Entity,Capability,Job
from generic_agents.worker import process_once
from generic_agents.gateway import ModelBlocked
from tests.test_vertical_slice import publish,manifest
from tests.test_instance_workspace import prepared,configuration,enable,object_row


def setup(client):
 release,_,_=prepared(client,configuration());enable(client,release);obj=object_row(client,release);cap=publish(client,manifest('foundation_model',name='business_qwen',allowed_agent_roles=['business'],execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY','max_tokens':1024},data_policy={'cloud_allowed':True}))
 return release,obj,cap

def proposal(obj,**kw):return {'entity_ids':[obj['id']],'goal':'current_state','required_modalities':['video'],'lookback_hours':48,'purpose':'核对过去两天资料是否足以判断当前状态','explanation':'核查资料充分性，不直接判断园区事实','clarifications':[],'unsupported_parts':[],**kw}

def start(client,r,obj,cap,**kw):
 body={'instance_id':r['instance_id'],'entity_ids':[obj['id']],'question':'请核查过去48小时的视频资料是否足以判断当前状态','model_profile_id':cap['id']}
 preview=client.post('/api/evidence-questions/preview',json=body);assert preview.status_code==200,preview.text
 body={**body,'preview_hash':preview.json()['preview_hash'],'authorize_cloud_text':True,'request_id':str(uuid4()),**kw}
 response=client.post('/api/evidence-questions',json=body);assert response.status_code==201,response.text
 return response.json(),body,preview.json()

def fixture(monkeypatch,value,calls,hook=None):
 def cloud(cap,messages,schema,p):
  calls.append(copy.deepcopy(messages))
  if hook:hook()
  return copy.deepcopy(value),{'provider':'explicit_test_fixture','usage':{'total_tokens':73}}
 monkeypatch.setattr('generic_agents.evidence_question.cloud_json',cloud)

def get(client,row):return client.get('/api/evidence-questions/'+row['id']).json()

def test_exact_scope_preview_draft_confirmation_and_local_assessment(client,monkeypatch):
 r,obj,cap=setup(client);row,body,preview=start(client,r,obj,cap);calls=[];fixture(monkeypatch,proposal(obj),calls)
 assert client.post('/api/evidence-questions',json=body).json()['id']==row['id']
 assert process_once(row['job_id'],deliver=False);row=get(client,row);assert row['status']=='needs_confirmation'
 assert len(calls)==1 and json.loads(calls[0][1]['content'])==preview['cloud_input']
 assert row['model_receipt']['provider']=='explicit_test_fixture'
 criteria={'goal':'current_state','required_modalities':['imagery','video'],'lookback_hours':24,'purpose':'人工改为一天的影像与视频资料核查'}
 saved=client.put('/api/evidence-questions/'+row['id']+'/draft',json={'version':row['version'],'criteria':criteria});assert saved.status_code==200,saved.text;row=saved.json()
 action={'version':row['version'],'request_id':str(uuid4())};response=client.post('/api/evidence-questions/'+row['id']+'/confirm',json=action);assert response.status_code==200,response.text;response=response.json()
 assert client.post('/api/evidence-questions/'+row['id']+'/confirm',json=action).json()['assessment_id']==response['assessment_id']
 assessment=client.get('/api/evidence-assessments/'+response['assessment_id']).json();assert assessment['request']['lookback_hours']==24 and assessment['question_reference']['original_question']==body['question']
 assert process_once(assessment['job_id'],deliver=False);result=client.get('/api/evidence-assessments/'+assessment['id']).json();assert not result['result']['objects'][0]['current_state_sufficient'] and result['result']['cloud_calls']==0
 assert client.post('/api/evidence-questions/'+row['id']+'/confirm',json={**action,'request_id':str(uuid4())}).status_code==409


def test_private_text_filtered_and_no_unapproved_call(client,monkeypatch):
 r,obj,cap=setup(client);monkeypatch.setenv('DASHSCOPE_API_KEY','exact-configured-key-123456789')
 body={'instance_id':r['instance_id'],'entity_ids':[obj['id']],'question':'48小时视频资料，密码:raw-private-value 联系13800138000 exact-configured-key-123456789','model_profile_id':cap['id']}
 p=client.post('/api/evidence-questions/preview',json=body);assert p.status_code==200
 text=json.dumps(p.json()['cloud_input'],ensure_ascii=False);assert not any(v in text for v in ['raw-private-value','13800138000','exact-configured-key-123456789'])
 assert client.post('/api/evidence-questions',json={**body,'preview_hash':p.json()['preview_hash'],'authorize_cloud_text':False,'request_id':str(uuid4())}).status_code==422
 assert client.post('/api/evidence-questions',json={**body,'question':'已换问题','preview_hash':p.json()['preview_hash'],'authorize_cloud_text':True,'request_id':str(uuid4())}).status_code==409
 with Session(engine()) as db:assert db.scalar(select(EvidenceQuestion.id)) is None


def test_paid_response_with_out_of_scope_ids_cannot_apply_or_repeat(client,monkeypatch):
 r,obj,cap=setup(client);row,body,p=start(client,r,obj,cap);calls=[];fixture(monkeypatch,proposal(obj,entity_ids=[str(uuid4())]),calls)
 assert process_once(row['job_id'],deliver=False);bad=get(client,row);assert bad['status']=='failed' and '对象范围' in bad['error'] and bad['model_receipt']['usage']['total_tokens']==73
 # Requeue same saved checkpoint: parsing fails again without another cloud call.
 with Session(engine()) as db:j=db.get(Job,row['job_id']);j.status='queued';j.lease_token=None;db.commit()
 assert process_once(row['job_id'],deliver=False);assert len(calls)==1
 assert client.post('/api/evidence-questions/'+row['id']+'/confirm',json={'version':bad['version'],'request_id':str(uuid4())}).status_code==409


def test_model_clarifications_and_unsupported_parts_need_saved_human_decisions(client,monkeypatch):
 r,obj,cap=setup(client);row,body,p=start(client,r,obj,cap);calls=[];fixture(monkeypatch,proposal(obj,goal='unsupported',required_modalities=[],lookback_hours=None,clarifications=['需要明确时效'],unsupported_parts=['盗伐语义识别未注册']),calls)
 assert process_once(row['job_id'],deliver=False);row=get(client,row);assert row['status']=='unsupported' and 'draft' not in row
 criteria={'goal':'current_state','required_modalities':['video'],'lookback_hours':48,'purpose':'只核查资料充分性，不给盗伐结论'}
 response=client.put('/api/evidence-questions/'+row['id']+'/draft',json={'version':row['version'],'criteria':criteria});assert response.status_code==200;row=response.json()
 action={'version':row['version'],'request_id':str(uuid4())};assert client.post('/api/evidence-questions/'+row['id']+'/confirm',json=action).status_code==409
 assert client.post('/api/evidence-questions/'+row['id']+'/confirm',json={**action,'acknowledge_clarifications':True,'acknowledge_unsupported_parts':True}).status_code==200


def test_cancellation_preserves_paid_response_and_source_change_blocks_confirmation(client,monkeypatch):
 r,obj,cap=setup(client);row,body,p=start(client,r,obj,cap);calls=[]
 def cancel():
  latest=get(client,row);res=client.post('/api/evidence-questions/'+row['id']+'/cancel',json={'version':latest['version']});assert res.status_code==200,res.text
 fixture(monkeypatch,proposal(obj),calls,hook=cancel);assert process_once(row['job_id'],deliver=False);cancelled=get(client,row)
 assert cancelled['status']=='cancelled' and cancelled['model_receipt']['usage']['total_tokens']==73 and 'proposal' not in cancelled
 row,_,_=start(client,r,obj,cap);fixture(monkeypatch,proposal(obj),calls);assert process_once(row['job_id'],deliver=False);row=get(client,row)
 with Session(engine()) as db:object_=db.get(Entity,obj['id']);object_.version+=1;object_.data={**object_.data,'later_change':'changed'};db.commit()
 assert client.post('/api/evidence-questions/'+row['id']+'/confirm',json={'version':row['version'],'request_id':str(uuid4())}).status_code==409


def test_unknown_paid_response_not_automatically_repeated_and_retry_cost_acknowledged(client,monkeypatch):
 r,obj,cap=setup(client);row,body,p=start(client,r,obj,cap);calls=[]
 def failing(*args):calls.append(1);raise ModelBlocked('模拟协议网络超时，可能计费')
 monkeypatch.setattr('generic_agents.evidence_question.cloud_json',failing)
 assert process_once(row['job_id'],deliver=False);failed=get(client,row);assert failed['status']=='failed' and failed['inflight']
 with Session(engine()) as db:j=db.get(Job,row['job_id']);j.status='queued';j.lease_token=None;db.commit()
 assert process_once(row['job_id'],deliver=False);assert len(calls)==1
 retry={**body,'parent_id':row['id'],'request_id':str(uuid4())};assert client.post('/api/evidence-questions',json=retry).status_code==409
 assert client.post('/api/evidence-questions',json={**retry,'acknowledge_possible_cost':True}).status_code==201


def test_empty_question_and_unapproved_business_profile_rejected(client):
 r,obj,cap=setup(client);body={'instance_id':r['instance_id'],'entity_ids':[obj['id']],'question':'   ','model_profile_id':cap['id']}
 assert client.post('/api/evidence-questions/preview',json=body).status_code==422
 with Session(engine()) as db:
  profile=db.get(Capability,cap['id']);profile.data={**profile.data,'allowed_agent_roles':['data']};db.commit()
 assert client.post('/api/evidence-questions/preview',json={**body,'question':'核查资料'}).status_code==409


def test_preview_matches_gateway_additional_field_redaction(client):
 r,obj,cap=setup(client)
 cap=publish(client,manifest('foundation_model',name='redacted_business_qwen',allowed_agent_roles=['business'],execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY','max_tokens':1024},data_policy={'cloud_allowed':True,'redact_fields':['question']}))
 body={'instance_id':r['instance_id'],'entity_ids':[obj['id']],'question':'这个问题不得外发','model_profile_id':cap['id']}
 response=client.post('/api/evidence-questions/preview',json=body);assert response.status_code==200,response.text
 assert '这个问题不得外发' not in json.dumps(response.json()['cloud_input'],ensure_ascii=False)


def test_saved_paid_response_resumes_normalization_without_second_cloud_call(client,monkeypatch):
 from generic_agents.evidence_question import normalize
 r,obj,cap=setup(client);row,_,_=start(client,r,obj,cap);calls=[];fixture(monkeypatch,proposal(obj),calls)
 def stop_after_paid_response(*args):raise ModelBlocked('隔离测试：付费响应已保存，尚未完成条件解析')
 monkeypatch.setattr('generic_agents.evidence_question.normalize',stop_after_paid_response)
 assert process_once(row['job_id'],deliver=False);failed=get(client,row);assert failed['status']=='failed' and failed['model_receipt']['usage']['total_tokens']==73
 monkeypatch.setattr('generic_agents.evidence_question.normalize',normalize)
 with Session(engine()) as db:
  job=db.get(Job,row['job_id']);job.status='queued';job.lease_token=None;job.data={**job.data,'recovery_only':True};db.commit()
 assert process_once(row['job_id'],deliver=False);recovered=get(client,row)
 assert recovered['status']=='needs_confirmation' and recovered['model_receipt']==failed['model_receipt'] and len(calls)==1
