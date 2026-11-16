"""Real isolated PG workflows; mocked cloud output is not model quality evidence."""
import copy,json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import SignalAdvice,Entity,Job,Task,PerceptionSignal,Membership
from generic_agents.worker import process_once
from tests.test_perception_signals import setup as signal_setup,signals
from tests.test_vertical_slice import publish,manifest

def setup(client):
    release,obj,_,_,_=signal_setup(client)
    signal=signals(client,release)[0]
    cap=publish(client,manifest('foundation_model',name='signal_advice_qwen',allowed_agent_roles=['business'],
        execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY','max_tokens':2048},data_policy={'cloud_allowed':True,'redact_fields':['私密备注']}))
    return release,obj,signal,cap
def preview(client,r,s,cap):
    body={'instance_id':r['instance_id'],'signal_id':s['id'],'model_profile_id':cap['id']}
    result=client.post('/api/signal-advice/preview',json=body);assert result.status_code==200,result.text
    return body,result.json()
def start(client,r,s,cap):
    body,p=preview(client,r,s,cap)
    body={**body,'preview_hash':p['preview_hash'],'authorize_cloud_text':True,'request_id':str(uuid4())}
    result=client.post('/api/signal-advice',json=body);assert result.status_code==201,result.text
    return result.json(),body,p
def get(client,row):
    response=client.get('/api/signal-advice/'+row['id']);assert response.status_code==200,response.text
    return response.json()
def cloud(monkeypatch,calls,change=None,hook=None):
    def run(cap,messages,schema,p):
        context=json.loads(messages[1]['content']);calls.append(copy.deepcopy(context))
        result={'entity_id':context['fixed_entity_id'],'summary':'登记规则有待核线索，先核查已保存资料，不确认现场状态',
            'findings':[{'text':'线索为待核查记录','basis':[{'ref_id':'signal','quote':context['references'][0]['text'][:200]}]}],
            'action':{'route':'evidence_check','business_id':None,'task_id':None,'criteria':{'goal':'available_evidence','required_modalities':['archive','observations'],'lookback_hours':24,'purpose':'核对已有登记依据，不按拍摄时间统计'},'purpose':'先核对已保存资料'},
            'clarifications':[],'unsupported_actions':[]}
        if change:change(result,context)
        if hook:hook()
        return result,{'provider':'explicit_contract_test_fixture','usage':{'total_tokens':87}}
    monkeypatch.setattr('generic_agents.signal_advice.cloud_json',run)
def confirmation(row):return {'version':row['version'],'request_id':str(uuid4()),'acknowledge_limits':True}

def test_preview_paid_advice_edit_actual_query_and_durable_idempotent_dispatch(client,monkeypatch):
    r,obj,s,cap=setup(client);row,body,p=start(client,r,s,cap);calls=[];cloud(monkeypatch,calls)
    assert client.post('/api/signal-advice',json=body).json()['id']==row['id']
    assert process_once(row['job_id'],deliver=False);row=get(client,row)
    assert row['status']=='needs_confirmation' and calls==[p['cloud_input']] and not row.get('dispatch')
    action={k:v for k,v in row['draft'].items() if k!='hash'};action['criteria']['required_modalities']=['imagery','archive','observations'];action['criteria']['purpose']='人工核对三种保存资料'
    response=client.put('/api/signal-advice/'+row['id']+'/draft',json={'version':row['version'],'action':action});assert response.status_code==200,response.text;row=response.json()
    body=confirmation(row);result=client.post('/api/signal-advice/'+row['id']+'/confirm',json=body);assert result.status_code==200,result.text
    assert client.post('/api/signal-advice/'+row['id']+'/confirm',json=body).json()['dispatch']==result.json()['dispatch']
    assert client.post('/api/signal-advice/'+row['id']+'/confirm',json={**body,'request_id':str(uuid4())}).status_code==409
    assessment=client.get('/api/evidence-assessments/'+result.json()['dispatch']['assessment_id']).json();assert assessment['request']['purpose']=='人工核对三种保存资料'
    assert process_once(assessment['job_id'],deliver=False);saved=client.get('/api/evidence-assessments/'+assessment['id']).json();assert saved['status']=='completed' and saved['result']['cloud_calls']==0
    supplement=client.post('/api/supplement-plans',json={'assessment_id':assessment['id'],'request_id':str(uuid4())})
    # Archive/observation gaps must remain unsupported, never a KeyError/device action.
    assert supplement.status_code in {200,201},supplement.text
    with Session(engine()) as db:assert db.get(Entity,obj['id']).version==1 and not list(db.scalars(select(Task)))
    assert client.get('/api/signal-advice/'+row['id']+'/export').json()==get(client,row)

def test_bad_quote_scope_and_unknown_paid_response_never_retry_or_dispatch(client,monkeypatch):
    r,obj,s,cap=setup(client);row,_,_=start(client,r,s,cap);calls=[]
    cloud(monkeypatch,calls,lambda value,context:value['findings'][0]['basis'][0].update(quote='不存在于预览的确定现场结论'))
    assert process_once(row['job_id'],deliver=False);bad=get(client,row);assert bad['status']=='failed' and '引用' in bad['error'] and bad['model_receipt']['usage']['total_tokens']==87
    with Session(engine()) as db:job=db.get(Job,row['job_id']);job.status='queued';job.lease_token=None;db.commit()
    assert process_once(row['job_id'],deliver=False);assert len(calls)==1
    assert client.post('/api/signal-advice/'+row['id']+'/confirm',json=confirmation(bad)).status_code==409
    unknown,_,_=start(client,r,s,cap)
    with Session(engine()) as db:record=db.get(SignalAdvice,unknown['id']);record.data={**record.data,'inflight':True};db.commit()
    assert process_once(unknown['job_id'],deliver=False);assert '可能已计费' in get(client,unknown)['error'] and len(calls)==1

def test_source_change_keeps_paid_response_blocks_dispatch_and_cancel_keeps_receipt(client,monkeypatch):
    r,obj,s,cap=setup(client);row,_,_=start(client,r,s,cap);calls=[];cloud(monkeypatch,calls)
    assert process_once(row['job_id'],deliver=False);row=get(client,row)
    with Session(engine()) as db:signal=db.get(PerceptionSignal,s['id']);signal.version+=1;signal.data={**signal.data,'note':'变更后的保存说明'};db.commit()
    response=client.post('/api/signal-advice/'+row['id']+'/confirm',json=confirmation(row));assert response.status_code==409 and '已变化' in response.text
    assert get(client,row)['model_receipt']==row['model_receipt']
    cancelled,_,_=start(client,r,s,cap)
    def stop():
        current=get(client,cancelled);res=client.post('/api/signal-advice/'+cancelled['id']+'/cancel',json={'version':current['version']});assert res.status_code==200,res.text
    cloud(monkeypatch,calls,hook=stop);assert process_once(cancelled['job_id'],deliver=False)
    saved=get(client,cancelled);assert saved['status']=='cancelled' and saved['model_receipt']['usage']['total_tokens']==87 and 'proposal' not in saved

def test_context_redaction_consent_and_cross_scope(client,monkeypatch):
    r,obj,s,cap=setup(client);monkeypatch.setenv('DASHSCOPE_API_KEY','exact-configured-key-123456789')
    with Session(engine()) as db:
        signal=db.get(PerceptionSignal,s['id']);signal.data={**signal.data,'note':'电话13800138000 密码:raw-secret exact-configured-key-123456789'};db.commit()
    body,p=preview(client,r,s,cap);text=json.dumps(p['cloud_input'],ensure_ascii=False)
    assert not any(v in text for v in ['13800138000','raw-secret','exact-configured-key-123456789'])
    assert client.post('/api/signal-advice',json={**body,'preview_hash':p['preview_hash'],'authorize_cloud_text':False,'request_id':str(uuid4())}).status_code==422
    assert client.post('/api/signal-advice/preview',json={**body,'instance_id':str(uuid4())}).status_code in {404,409}
    with Session(engine()) as db:
        member=db.scalar(select(Membership));member.role='viewer';db.commit()
    assert client.post('/api/signal-advice/preview',json=body).status_code==403

def test_dynamic_manual_business_dispatch_does_not_execute_model_or_mutate_object(client,monkeypatch):
    r,obj,s,cap=setup(client);row,_,p=start(client,r,s,cap);calls=[]
    assert p['cloud_input']['allowed_business_ids']
    def manual(value,context):value['action']={'route':'manual_business','business_id':context['allowed_business_ids'][0],'task_id':None,'criteria':None,'purpose':'公开登记线索人工核查'}
    cloud(monkeypatch,calls,manual);assert process_once(row['job_id'],deliver=False);row=get(client,row)
    body=confirmation(row);response=client.post('/api/signal-advice/'+row['id']+'/confirm',json=body);assert response.status_code==200,response.text
    assert client.post('/api/signal-advice/'+row['id']+'/confirm',json=body).json()['dispatch']['task_id']==response.json()['dispatch']['task_id']
    task=client.get('/api/workspace/tasks/'+response.json()['dispatch']['task_id']).json();assert task['status']=='workflow_waiting' and task['perception_signal']['signal_id']==s['id']
    assert len(calls)==1
    with Session(engine()) as db:assert db.get(Entity,obj['id']).version==1

def test_cloud_scope_and_extra_actions_are_rejected_before_dispatch(client,monkeypatch):
    r,obj,s,cap=setup(client);row,_,_=start(client,r,s,cap);calls=[]
    cloud(monkeypatch,calls,lambda value,context:value.update(entity_id=str(uuid4())))
    assert process_once(row['job_id'],deliver=False);saved=get(client,row);assert saved['status']=='failed' and '对象范围' in saved['error']
    row,_,_=start(client,r,s,cap)
    def illegal(value,context):value['action']={'route':'manual_business','business_id':'outside_business','task_id':None,'criteria':None,'purpose':'绕过已确认业务'}
    cloud(monkeypatch,calls,illegal);assert process_once(row['job_id'],deliver=False);saved=get(client,row);assert saved['status']=='failed' and '全人工业务' in saved['error']
    with Session(engine()) as db:assert not list(db.scalars(select(Task)))

def test_unknown_cost_retry_needs_explicit_ack_and_late_source_change_keeps_paid_response(client,monkeypatch):
    r,obj,s,cap=setup(client);row,body,p=start(client,r,s,cap)
    with Session(engine()) as db:record=db.get(SignalAdvice,row['id']);record.data={**record.data,'inflight':True};db.commit()
    result=client.post('/api/signal-advice',json={**body,'parent_id':row['id'],'request_id':str(uuid4())});assert result.status_code==409 and '计费' in result.text
    def change():
        with Session(engine()) as db:signal=db.get(PerceptionSignal,s['id']);signal.version+=1;signal.data={**signal.data,'note':'云调用中说明变更'};db.commit()
    fresh=client.post('/api/signal-advice',json={**body,'parent_id':row['id'],'acknowledge_possible_cost':True,'request_id':str(uuid4())});assert fresh.status_code==201,fresh.text;fresh=fresh.json();calls=[];cloud(monkeypatch,calls,hook=change)
    assert process_once(fresh['job_id'],deliver=False);saved=get(client,fresh);assert saved['status']=='failed' and saved['model_receipt']['usage']['total_tokens']==87 and 'proposal' not in saved

def test_original_group_identity_does_not_assert_comparable_measurements():
    from generic_agents.signal_advice import source_comparisons
    rows=[{'integrity_verified':True,'source_sha256':['first']},
          {'integrity_verified':True,'source_sha256':['second']},
          {'integrity_verified':True,'source_sha256':['first']},
          {'integrity_verified':False,'source_sha256':['first']},
          {'integrity_verified':True,'source_sha256':[]},
          {'integrity_verified':True,'source_sha256':['first','second']}]
    views=source_comparisons(rows)
    assert views[0]['source_group']==views[2]['source_group']
    assert views[0]['source_group']!=views[1]['source_group']!=views[5]['source_group']
    assert views[3]['source_group'] is None and views[4]['source_group'] is None
    assert all(v['measurement_comparability']=='not_verified' for v in views)
    assert 'first' not in json.dumps(views) and 'second' not in json.dumps(views)

def test_cloud_preview_keeps_source_distinctions_without_original_hashes(client,monkeypatch):
    r,obj,s,cap=setup(client)
    rows=[{'integrity_verified':True,'source_sha256':[identity],
           'name':'同名影像','kind':'segmentation','metrics':{'candidate_masks':count,'candidate_pixels':0,'unknown_pixels':None},'modality':'imagery',
           'record_stage':'processed','source_time_verified':False,'domain_quality_verified':False}
          for identity,count in [('uploaded-original-secret-sha',3),('map-original-secret-sha',4),('map-original-secret-sha',4)]]
    monkeypatch.setattr('generic_agents.signal_advice.collect',lambda *args:(rows,[],False))
    _,p=preview(client,r,s,cap)
    records=[json.loads(v['text']) for v in p['cloud_input']['references'] if v['id'].startswith('record:')]
    assert records[0]['source_group']!=records[1]['source_group']==records[2]['source_group']
    assert [v['metrics']['candidate_masks'] for v in records]==[3,4,4]
    assert all(set(v['metrics'])=={'candidate_masks'} for v in records)
    assert all(v['record_stage']=='processed' and v['measurement_comparability']=='not_verified' for v in records)
    text=json.dumps(p['cloud_input'],ensure_ascii=False)
    assert not any(v in text for v in ['uploaded-original-secret-sha','map-original-secret-sha'])
    assert '不能仅凭差值认定' in text and '不是独立的重复观测' in text

def test_local_basis_audit_is_in_exact_preview_and_frozen_paid_context(client,monkeypatch):
    r,obj,s,cap=setup(client)
    rows=[{'integrity_verified':True,'source_sha256':[sha],'name':'公开原图','status':'succeeded','kind':'segmentation','metrics':{'candidate_masks':count},'source_time_verified':False} for sha,count in [('public_A',3),('public_B',4)]]
    monkeypatch.setattr('generic_agents.signal_advice.collect',lambda *args:(rows,[],False))
    row,_,p=start(client,r,s,cap);calls=[];cloud(monkeypatch,calls);assert process_once(row['job_id'],deliver=False)
    saved=get(client,row);audit=saved['request']['cloud_input']['basis_audit']
    assert audit==p['cloud_input']['basis_audit']==calls[0]['basis_audit']
    assert audit['comparisons'][0]['relation']=='different_originals'
    assert saved['request']['rule_version']=='signal-business-advice-v4'
    assert client.get('/api/signal-advice/'+saved['id']+'/export').json()==saved
