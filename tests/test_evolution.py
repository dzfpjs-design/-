"""Protocol tests on real PostgreSQL; synthetic labels are not business-value proof."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.db import engine
from generic_agents.models import Memory, Observation, Task, LearningCandidate, EvaluationSet
from generic_agents.worker import process_once
from .test_vertical_slice import manifest, publish, explicit_goal
from .planning_helpers import post_plan


def post(client,path,value,status=200):
    response=post_plan(client,path,json=value) if path.endswith('/plan') else client.post(path,json=value)
    assert response.status_code==status,response.text
    return response.json()


def latest(client,key,object_id):
    return next(r for r in client.get('/api/learning/overview').json()[key] if r['id']==object_id)


def make_entity(client,package,attributes,stale=False):
    return post(client,'/api/entities',{'package_id':package['id'],'entity_type':'site','external_id':uuid4().hex,
        'attributes':{'name':'明确标记的合成评测对象',**attributes},
        'valid_until':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat() if stale else None},201)


def verified_case(client, package, model, obj, field, monkeypatch):
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:({'summary':'测试替身计划','steps':[
        {'title':'证据报告','agent':'business','action':'report'}]}, {'provider':'explicit_test_fixture'}))
    task=post(client,'/api/tasks',{'title':'合成协议验收 '+field,'package_id':package['id'],
        'entity_ids':[obj['id']],'fields':[field],'goal_spec':explicit_goal(obj,fields=[field])},201)
    plan=post(client,'/api/tasks/'+task['id']+'/plan',{'expected_version':1,'reason':'合成测试','model_profile_id':model['id']})
    post(client,'/api/plans/'+plan['id']+'/approve',{'expected_version':plan['version'],'reason':'只核对合成证据'})
    assert process_once()
    saved=next(t for t in client.get('/api/overview').json()['tasks'] if t['id']==task['id'])
    post(client,'/api/tasks/'+task['id']+'/accept',{'expected_version':saved['version'],'reason':'仅协议验收，不证明行业质量'})
    memory=next(m for m in client.get('/api/overview').json()['memories'] if m.get('task_id')==task['id'])
    post(client,'/api/memories/'+memory['id']+'/review',{'expected_version':memory['version'],'reason':'可用于合成检索测试','decision':'trust'})
    assert process_once()  # The actual durable post-review proposal job.
    return next(m for m in client.get('/api/overview').json()['memories'] if m['id']==memory['id'])


def setup(client,monkeypatch,config=None):
    package=publish(client,manifest(specification={'entity_schemas':{'site':{'type':'object',
        'required':['name'],'properties':{'name':{'type':'string'},'temperature':{'type':'number'},'pressure':{'type':'number'}}}}}))
    model=publish(client,manifest('foundation_model',name='test_model',execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY'},data_policy={'cloud_allowed':True}))
    train=verified_case(client,package,model,make_entity(client,package,{'temperature':24}),'temperature',monkeypatch)
    target=verified_case(client,package,model,make_entity(client,package,{'temperature':25}),'temperature',monkeypatch)
    distractor=verified_case(client,package,model,make_entity(client,package,{'pressure':9}),'pressure',monkeypatch)
    candidate=post(client,'/api/learning/candidates',{'package_id':package['id'],'title':'温度经验选择候选','memory_ids':[train['id']],
        **({'config':config} if config is not None else {})},201)
    normal=make_entity(client,package,{'temperature':20});missing=make_entity(client,package,{})
    stale=make_entity(client,package,{'temperature':20},True);conflict=make_entity(client,package,{'temperature':20})
    source=publish(client,manifest('source',name='synthetic_source',execution={'adapter':'upload'}))
    with Session(engine()) as db:
        stamp=datetime.now(timezone.utc)
        for index,value in enumerate([70,71]):
            db.add(Observation(workspace_id=normal['workspace_id'],source_id=source['id'],source_record_id=str(index),
                entity_id=conflict['id'],observed_at=stamp,business_day=str(stamp.date()),processing_version='1',status='processed',
                data={'extracted':{'attributes':{'temperature':value},'anomalies':[]}}))
        db.commit()
    test_set=post(client,'/api/learning/sets',{'package_id':package['id'],'title':'合成冻结检索集'},201)
    for category,obj,status in [('normal',normal,'sufficient'),('no_answer',missing,'insufficient'),
        ('stale',stale,'insufficient'),('conflict',conflict,'conflict'),('ambiguous',None,'insufficient')]:
        test_set=post(client,'/api/learning/sets/'+test_set['id']+'/cases',{'expected_version':test_set['version'],'reason':'明确合成标签',
            'case':{'name':category,'category':category,'entity_ids':[obj['id']] if obj else [],'fields':['temperature'],
                'expected_status':status,'expected_memory_ids':[target['id']] if category=='normal' else [],
                'label_reason':'合成来源已知；对象未明确时不能擅自选择'}})
    test_set=post(client,'/api/learning/sets/'+test_set['id']+'/freeze',{'expected_version':test_set['version'],'reason':'核对五类固定回归用例'})
    return package,train,target,distractor,candidate,test_set,normal


def evaluate(client,candidate,test_set):
    run=post(client,'/api/learning/candidates/'+candidate['id']+'/evaluate',{'expected_version':candidate['version'],
        'set_id':test_set['id'],'reason':'比较冻结检索质量'},202)
    assert process_once()
    return latest(client,'candidates',candidate['id']),latest(client,'runs',run['id'])


def query(client,obj,question):
    return post(client,'/api/data/queries',{'question':question,'mode':'structured','entity_ids':[obj['id']],'fields':['temperature']})


def feedback(client,deployment,target,count=3,stage='shadow'):
    trials=[t for t in client.get('/api/learning/overview').json()['trials'] if t['deployment_id']==deployment['id'] and t['data']['stage']==stage]
    for trial in trials:
        post(client,'/api/learning/trials/'+trial['id']+'/review',{'expected_version':trial['version'],'reason':'合成标注只核查第一条经验相关性',
            'expected_status':'sufficient','expected_memory_ids':[target['id']]})
    return trials


def shadow(client,candidate,test_set):
    candidate,run=evaluate(client,candidate,test_set)
    assert run['status']=='passed' and run['data']['metrics']['baseline_correct']==4
    assert run['data']['metrics']['candidate_correct']==5
    return post(client,'/api/learning/candidates/'+candidate['id']+'/shadow',{'expected_version':candidate['version'],
        'run_id':run['id'],'reason':'只在合成业务包内影子验证'})


def canary(client,deployment,obj,target):
    for i in range(3):query(client,obj,'影子核查不同请求 '+str(i))
    feedback(client,deployment,target)
    deployment=latest(client,'deployments',deployment['id'])
    return post(client,'/api/learning/deployments/'+deployment['id']+'/canary',{'expected_version':deployment['version'],
        'reason':'限额与比例验证','percent':25,'max_requests':100})


def test_complete_evaluation_shadow_canary_publication_and_rollback(client,monkeypatch):
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch)
    assert query(client,obj,'默认基线')['data']['result']['trusted_cases'][0]['id']==distractor['id']
    deployment=shadow(client,candidate,test_set)
    assert client.post('/api/learning/deployments/'+deployment['id']+'/publish',json={'expected_version':deployment['version'],'reason':'不能跳阶段'}).status_code==409
    deployment=canary(client,deployment,obj,target)
    served=set()
    for i in range(60):
        run=query(client,obj,'有限发布分组 '+str(i))
        served.add(run['data']['result']['learning_decisions'][0]['served'])
        if i>=2 and len(served)==2:break
    assert served=={'baseline','candidate'}
    feedback(client,deployment,target,stage='canary')
    deployment=latest(client,'deployments',deployment['id'])
    post(client,'/api/learning/deployments/'+deployment['id']+'/publish',{'expected_version':deployment['version'],'reason':'已核对合成候选与对照'})
    result=query(client,obj,'发布后检索')['data']['result']
    assert result['trusted_cases'][0]['id']==target['id'] and result['learning_decisions'][0]['served']=='candidate'
    before=client.get('/api/overview').json()['tasks']
    deployment=latest(client,'deployments',deployment['id'])
    post(client,'/api/learning/deployments/'+deployment['id']+'/rollback',{'expected_version':deployment['version'],'reason':'演练回滚'})
    assert query(client,obj,'回滚后基线')['data']['result']['trusted_cases'][0]['id']==distractor['id']
    assert client.get('/api/overview').json()['tasks']==before
    assert len(client.get('/api/learning/overview').json()['runs'])==1


def test_no_gain_or_source_leakage_cannot_advance(client,monkeypatch):
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch,{'field_weights':{},'max_cases':10})
    candidate,run=evaluate(client,candidate,test_set)
    assert run['status']=='failed'
    assert client.post('/api/learning/candidates/'+candidate['id']+'/shadow',json={'expected_version':candidate['version'],'run_id':run['id'],'reason':'无改善'}).status_code==409
    leaked=post(client,'/api/learning/candidates',{'package_id':package['id'],'title':'泄漏的候选','memory_ids':[target['id']]},201)
    assert client.post('/api/learning/candidates/'+leaked['id']+'/evaluate',json={'expected_version':leaked['version'],
        'set_id':test_set['id'],'reason':'应拒绝泄漏'},).status_code==409
    assert client.post('/api/learning/candidates',json={'package_id':package['id'],'title':'越界配置','memory_ids':[train['id']],
        'config':{'field_weights':{},'approval_required':False}}).status_code==422


def test_revoke_training_source_immediately_restores_baseline(client,monkeypatch):
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch)
    deployment=shadow(client,candidate,test_set)
    post(client,'/api/learning/memories/'+train['id']+'/revoke',{'expected_version':train['version'],'reason':'原经验纠正'})
    result=query(client,obj,'撤回后查询')['data']['result']
    assert train['id'] not in [m['id'] for m in result['trusted_cases']]
    assert latest(client,'deployments',deployment['id'])['status']=='rolled_back'
    assert result['learning_decisions']==[]


def test_negative_online_feedback_rolls_back_without_changing_saved_results(client,monkeypatch):
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch)
    deployment=canary(client,shadow(client,candidate,test_set),obj,target)
    saved=query(client,obj,'人工发现反例')
    trial=next(t for t in client.get('/api/learning/overview').json()['trials'] if t['data']['stage']=='canary')
    post(client,'/api/learning/trials/'+trial['id']+'/review',{'expected_version':trial['version'],'reason':'合成反例要求压力案例，候选先选了温度案例',
        'expected_status':'sufficient','expected_memory_ids':[distractor['id']]})
    assert latest(client,'deployments',deployment['id'])['status']=='rolled_back'
    stored=next(r for r in client.get('/api/data/queries').json() if r['id']==saved['id'])
    assert stored['data']==saved['data'] and stored['status']==saved['status']
    assert datetime.fromisoformat(stored['created_at'])==datetime.fromisoformat(saved['created_at'])


def test_frozen_content_and_labels_are_durable_and_immutable(client,monkeypatch):
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch)
    assert client.post('/api/learning/sets/'+test_set['id']+'/cases/'+test_set['data']['cases'][0]['case_id']+'/remove',
        json={'expected_version':test_set['version'],'reason':'冻结后不能删标签'}).status_code==409
    with Session(engine()) as db:
        row=db.get(EvaluationSet,test_set['id']);row.data={**row.data,'tampered':True};db.commit()
    assert client.post('/api/learning/candidates/'+candidate['id']+'/evaluate',json={'expected_version':candidate['version'],
        'set_id':test_set['id'],'reason':'篡改被冻结记录'}).status_code==409


def test_trial_idempotency_and_canary_budget_stop_new_exposures(client,monkeypatch):
    from generic_agents.services import query_evidence
    from generic_agents.security import Principal
    from generic_agents.models import LearningDeployment, LearningTrial
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch)
    deployment=canary(client,shadow(client,candidate,test_set),obj,target)
    with Session(engine()) as db:
        p=Principal(db.get(Memory,train['id']).data['reviewed_by'],obj['workspace_id'],'admin','test')
        first=query_evidence(db,p,[obj['id']],['temperature'],context='稳定请求',route_key='fixed-retry');db.commit()
        before=db.get(LearningDeployment,deployment['id']).data['request_count']
        again=query_evidence(db,p,[obj['id']],['temperature'],context='稳定请求',route_key='fixed-retry');db.commit()
        assert again['learning_decisions']==first['learning_decisions']
        assert db.get(LearningDeployment,deployment['id']).data['request_count']==before
        row=db.get(LearningDeployment,deployment['id']);row.data={**row.data,'max_requests':before};db.commit()
    result=query(client,obj,'额度用完后的不同请求')['data']['result']
    assert result['learning_decisions']==[]
    assert latest(client,'deployments',deployment['id'])['status']=='rolled_back'


def test_viewer_cannot_change_learning_or_see_other_workspace(client,account,monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents.models import User, Workspace, Membership
    from generic_agents.security import password_hash
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch)
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));membership.role='viewer'
        user=User(username=uuid4().hex,password_hash=password_hash('AnotherValidationAccount-2026'))
        space=Workspace(name='另一个隔离工作区');db.add_all([user,space]);db.flush()
        db.add(Membership(workspace_id=space.id,user_id=user.id,role='admin'));db.commit();username=user.username
    assert client.post('/api/learning/candidates',json={'package_id':package['id'],'title':'越权','memory_ids':[train['id']]}).status_code==403
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':'AnotherValidationAccount-2026'})
        other.headers['X-CSRF-Token']=login.json()['csrf_token']
        assert other.get('/api/learning/overview').json()['candidates']==[]
        assert other.post('/api/learning/candidates/'+candidate['id']+'/evaluate',json={'expected_version':candidate['version'],
            'set_id':test_set['id'],'reason':'不能越过工作区'}).status_code==404


def test_removed_label_survives_adding_another_draft_case(client,monkeypatch):
    package,*_=setup(client,monkeypatch)
    row=post(client,'/api/learning/sets',{'package_id':package['id'],'title':'标签恢复验证'},201)
    def add(row,name):
        return post(client,'/api/learning/sets/'+row['id']+'/cases',{'expected_version':row['version'],'reason':'保存合成标签',
            'case':{'name':name,'category':'ambiguous','entity_ids':[],'fields':['temperature'],
                'expected_status':'insufficient','label_reason':'对象没有选定'}})
    row=add(row,'原标签');case=row['data']['cases'][0]
    row=post(client,'/api/learning/sets/'+row['id']+'/cases/'+case['case_id']+'/remove',
        {'expected_version':row['version'],'reason':'保留恢复记录'})
    row=add(row,'新标签')
    row=post(client,'/api/learning/sets/'+row['id']+'/restore',{'expected_version':row['version'],'reason':'恢复人工成果'})
    assert [c['name'] for c in row['data']['cases']]==['新标签','原标签']
    assert row['data']['cases'][1]==case


def test_withdrawn_evaluation_label_blocks_release(client,monkeypatch):
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch)
    candidate,run=evaluate(client,candidate,test_set)
    post(client,'/api/learning/memories/'+target['id']+'/revoke',{'expected_version':target['version'],'reason':'评测标签依据撤回'})
    assert client.post('/api/learning/candidates/'+candidate['id']+'/shadow',json={
        'expected_version':candidate['version'],'run_id':run['id'],'reason':'旧评测不能继续批准'}).status_code==409


def test_withdrawn_evaluation_label_stops_live_use(client,monkeypatch):
    package,train,target,distractor,candidate,test_set,obj=setup(client,monkeypatch)
    deployment=shadow(client,candidate,test_set)
    post(client,'/api/learning/memories/'+target['id']+'/revoke',{'expected_version':target['version'],'reason':'发布后评测来源撤回'})
    result=query(client,obj,'旧评测依据失效')['data']['result']
    assert result['learning_decisions']==[]
    assert result['trusted_cases'][0]['id']==distractor['id']
    assert latest(client,'deployments',deployment['id'])['status']=='rolled_back'
