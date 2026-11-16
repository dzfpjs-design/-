"""Real PG relationship claims, authority, lineage and bounded data-agent traversal."""
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Entity,Relation,now
from generic_agents.business_tools import evidence_hash
from .test_vertical_slice import publish,manifest,entity,explicit_goal


def package(client,cardinality='many'):
    return publish(client,manifest(specification={'timezone':'Asia/Shanghai','entity_schemas':{
        'site':{'type':'object','required':['name','temperature'],'additionalProperties':False,
            'properties':{'name':{'type':'string'},'temperature':{'type':'number'}}}},
        'relation_types':{'linked':{'title':'关联对象','description':'合成关联核查',
            'source_types':['site'],'target_types':['site'],'cardinality':cardinality}}}))


def claim(pkg,a,b):
    return {'package_id':pkg['id'],'relation_type':'linked','source_entity_id':a['id'],'target_entity_id':b['id'],
        'statement':'合成对象登记记录支持关联，由操作者核查','reason':'登记合成关联核查记录',
        'evidence_refs':[{'kind':'entity','id':a['id']},{'kind':'entity','id':b['id']}],
        'valid_from':(now()-timedelta(days=1)).isoformat(),'attributes':{}}


def save(client,pkg,a,b,confirm=True):
    r=client.post('/api/relations',json=claim(pkg,a,b)); assert r.status_code==201,r.text
    row=r.json()
    if confirm: row=review(client,row,'confirmed')
    return row


def review(client,row,decision):
    r=client.post('/api/relations/'+row['id']+'/review',json={'expected_version':row['version'],
        'decision':decision,'reason':'逐项核对来源与关系声明'}); assert r.status_code==200,r.text
    return r.json()


def spec(pkg,root,**kw):
    return {'package_id':pkg['id'],'relation_types':['linked'],'root_ids':[root['id']],**kw}


def query(client,pkg,root,scope,**kw):
    r=client.post('/api/data/queries',json={'question':'核对关联对象登记资料','entity_ids':[e['id'] for e in scope],
        'fields':['temperature'],'mode':'structured','relation_query':spec(pkg,root,**kw)})
    assert r.status_code==200,r.text
    return r.json()


def test_operator_claim_confirm_correct_withdraw_and_reopen(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);row=save(client,pkg,a,b,False)
    assert query(client,pkg,a,[a,b])['status']=='insufficient'
    row=review(client,row,'confirmed');run=query(client,pkg,a,[a,b]);result=run['data']['result']
    assert run['status']=='sufficient' and result['selected_entity_ids']==[b['id']]
    assert result['relations']['relation_evidence'][0]['verification']=='confirmed'
    assert [s['id'] for s in result['query_plan']['steps']]==['relations','fields']
    edited=client.patch('/api/relations/'+row['id'],json={**claim(pkg,a,b),'expected_version':row['version'],
        'statement':'修正关联依据说明，必须重新核查'}); assert edited.status_code==200,edited.text
    row=edited.json(); assert row['status']=='proposed'
    assert query(client,pkg,a,[a,b])['status']=='insufficient'
    row=review(client,row,'confirmed');row=review(client,row,'withdrawn')
    assert query(client,pkg,a,[a,b])['status']=='insufficient'
    history=client.get('/api/relations/'+row['id']+'/history').json()
    assert len(history)==5 and history[2]['data']['before']['data']['statement']!=row['data']['statement']
    token=client.post('/api/tokens').json()['token']
    with TestClient(app) as reopened:
        reopened.headers['Authorization']='Bearer '+token
        assert reopened.get('/api/relations/'+row['id']).json()==row
        assert reopened.get('/api/relations/'+row['id']+'/history').json()==history
        original=next(x for x in reopened.get('/api/data/queries').json() if x['id']==run['id'])
        from generic_agents.temporal import instant
        assert instant(original['created_at'])==instant(run['created_at'])
        assert {k:v for k,v in original.items() if k!='created_at'}=={k:v for k,v in run.items() if k!='created_at'}


def test_source_revision_invalidates_confirmed_claim_and_reconfirmation(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);row=save(client,pkg,a,b)
    before=query(client,pkg,a,[a,b])['data']['result']
    r=client.patch('/api/entities/'+a['id'],json={'expected_version':1,'reason':'更新依据',
        'attributes':{'name':'改过的依据','temperature':25}});assert r.status_code==200
    after=query(client,pkg,a,[a,b])['data']['result']
    assert after['status']=='insufficient' and evidence_hash(before)!=evidence_hash(after)
    assert client.post('/api/relations/'+row['id']+'/review',json={'expected_version':row['version'],
        'decision':'confirmed','reason':'旧来源不能沿用'}).status_code==409
    a=r.json(); edited=client.patch('/api/relations/'+row['id'],json={**claim(pkg,a,b),
        'expected_version':row['version']});assert edited.status_code==200,edited.text
    review(client,edited.json(),'confirmed');assert query(client,pkg,a,[a,b])['status']=='sufficient'


def test_outside_scope_endpoint_never_disclosed(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);row=save(client,pkg,a,b)
    run=query(client,pkg,a,[a]);result=run['data']['result']
    assert run['status']=='insufficient' and not result['relations']['relation_evidence']
    assert b['id'] not in str(result) and row['id'] not in str(result)


def test_outside_scope_supporting_record_never_disclosed(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);c=entity(client,pkg)
    body=claim(pkg,a,b);body['evidence_refs'].append({'kind':'entity','id':c['id']})
    r=client.post('/api/relations',json=body);assert r.status_code==201;review(client,r.json(),'confirmed')
    run=query(client,pkg,a,[a,b]);assert run['status']=='insufficient' and c['id'] not in str(run['data']['result'])


def test_bounded_multi_hop_and_cycles(client):
    pkg=package(client);a,b,c,d=[entity(client,pkg) for _ in range(4)]
    for x,y in [(a,b),(b,c),(c,a),(c,d)]:save(client,pkg,x,y)
    one=query(client,pkg,a,[a,b,c,d]);assert one['status']=='sufficient'
    first=one['data']['result']; traversal=first['relations']
    assert first['selected_entity_ids']==[b['id']]
    assert len(traversal['relation_evidence'])==1 and traversal['spec']['max_hops']==1
    assert not traversal['truncated'] and not traversal['relation_gaps']
    all_=query(client,pkg,a,[a,b,c,d],max_hops=3)
    assert all_['status']=='sufficient'
    assert set(all_['data']['result']['selected_entity_ids'])=={b['id'],c['id'],d['id']}
    assert len(all_['data']['result']['relations']['relation_evidence'])==4
    incoming=query(client,pkg,d,[a,b,c,d],direction='incoming',max_hops=3)
    assert incoming['status']=='sufficient'
    assert set(incoming['data']['result']['selected_entity_ids'])=={a['id'],b['id'],c['id']}


def test_cardinality_conflict_preserved(client):
    pkg=package(client,'one_per_source');a,b,c=[entity(client,pkg) for _ in range(3)]
    save(client,pkg,a,b);save(client,pkg,a,c)
    run=query(client,pkg,a,[a,b,c]);assert run['status']=='conflict'
    assert len(run['data']['result']['relations']['relation_evidence'])==2
    assert len(run['data']['result']['conflicts'][0]['relation_ids'])==2


def test_reverse_query_does_not_silently_ignore_an_out_of_scope_cardinality_claim(client):
    pkg=package(client,'one_per_target');a,b,c=[entity(client,pkg) for _ in range(3)]
    save(client,pkg,a,b);hidden=save(client,pkg,c,b)
    run=query(client,pkg,a,[a,b]);assert run['status']=='insufficient'
    result=run['data']['result']
    assert any('唯一性' in g['reason'] for g in result['gaps'])
    assert c['id'] not in str(result) and hidden['id'] not in str(result)


def test_expired_or_unproved_historical_relation_reports_gap(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);row=save(client,pkg,a,b)
    historical=query(client,pkg,a,[a,b],at=(now()-timedelta(hours=1)).isoformat())
    assert historical['status']=='insufficient'
    assert any('历史' in g['reason'] for g in historical['data']['result']['gaps'])
    body={**claim(pkg,a,b),'expected_version':row['version'],'valid_until':(now()-timedelta(hours=1)).isoformat()}
    edited=client.patch('/api/relations/'+row['id'],json=body);assert edited.status_code==200
    assert client.post('/api/relations/'+row['id']+'/review',json={'expected_version':edited.json()['version'],
        'reason':'不能确认过期关系为当前','decision':'confirmed'}).status_code==409


def test_invalid_registration_and_claim_contracts(client):
    value=manifest(name='invalid_relation',specification={'entity_schemas':{'site':{'type':'object'}},'relation_types':{
        'bad':{'title':'错误','description':'错误','source_types':['absent'],'target_types':['site']}}})
    row=client.post('/api/capabilities',json=value).json()
    assert client.post('/api/capabilities/'+row['id']+'/validate',json={'expected_version':1,'reason':'校验'}).status_code==422
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg)
    for changes in [{'target_entity_id':a['id']},{'relation_type':'undefined'},{'attributes':{'invented':1}},
            {'valid_from':'2026-10-05T01:00:00'},{'evidence_refs':[{'kind':'entity','id':a['id']}]*2}]:
        r=client.post('/api/relations',json={**claim(pkg,a,b),**changes});assert r.status_code==422,r.text


def test_claim_content_corruption_cannot_keep_confirmation(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);row=save(client,pkg,a,b)
    with Session(engine()) as db:
        record=db.get(Relation,row['id']);record.data={**record.data,'statement':'篡改已确认声明'};db.commit()
    assert query(client,pkg,a,[a,b])['status']=='insufficient'


def test_stale_review_and_endpoint_identity_correction_rejected(client):
    pkg=package(client);a,b,c=[entity(client,pkg) for _ in range(3)];row=save(client,pkg,a,b)
    assert client.post('/api/relations/'+row['id']+'/review',json={'expected_version':1,'decision':'withdrawn',
        'reason':'旧版本'}).status_code==409
    assert client.patch('/api/relations/'+row['id'],json={**claim(pkg,a,c),
        'expected_version':row['version']}).status_code==422


def test_cross_workspace_and_viewer_write_denied(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);row=save(client,pkg,a,b)
    from generic_agents.models import User,Workspace,Membership
    from generic_agents.security import password_hash
    name,password=uuid4().hex,uuid4().hex
    with Session(engine()) as db:
        user=User(username=name,password_hash=password_hash(password));ws=Workspace(name='隔离');db.add_all([user,ws]);db.flush()
        db.add_all([Membership(user_id=user.id,workspace_id=ws.id,role='admin'),
            Membership(user_id=user.id,workspace_id=a['workspace_id'],role='viewer')]);db.commit();isolated=ws.id
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':name,'password':password}).json()
        other.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':isolated})
        assert other.get('/api/relations/'+row['id']).status_code==404
        assert other.post('/api/relations',json=claim(pkg,a,b)).status_code==404
        assert other.get('/api/relations').json()['items']==[]
        other.headers['X-Workspace-ID']=a['workspace_id']
        assert other.post('/api/relations',json=claim(pkg,a,b)).status_code==403
        assert other.post('/api/relations/'+row['id']+'/review',json={'expected_version':row['version'],
            'decision':'withdrawn','reason':'只读越权'}).status_code==403


def test_read_budget_is_truthful(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg)
    template=save(client,pkg,a,b)
    with Session(engine()) as db:
        row=db.get(Relation,template['id'])
        for _ in range(50):db.add(Relation(workspace_id=row.workspace_id,package_id=row.package_id,
            relation_type=row.relation_type,source_entity_id=row.source_entity_id,target_entity_id=row.target_entity_id,
            status=row.status,valid_from=row.valid_from,data=row.data))
        db.commit()
    run=query(client,pkg,a,[a,b]);r=run['data']['result']['relations']
    assert run['status']=='insufficient' and r['truncated'] and len(r['relation_evidence'])==50


def test_business_goal_reuses_relation_scope_and_change_invalidates_evidence(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);row=save(client,pkg,a,b)
    goal=explicit_goal(b,source='archive');goal['entity_ids']=[a['id'],b['id']]
    goal['relation_query']=spec(pkg,a)
    r=client.post('/api/tasks',json={'title':'关联对象证据核对','package_id':pkg['id'],'entity_ids':goal['entity_ids'],
        'fields':['temperature'],'goal_spec':goal});assert r.status_code==201,r.text
    task=r.json()
    from generic_agents.goals import query_goal,evaluate
    from generic_agents.models import Task
    from generic_agents.security import Principal
    with Session(engine()) as db:
        t=db.get(Task,task['id']);p=Principal(t.owner_id,t.workspace_id,'admin','test')
        evidence=query_goal(db,p,t,learning=False);assert evidence['status']=='sufficient'
        assert evidence['selected_entity_ids']==[b['id']]
        assert all('name' not in ref['snapshot']['data'] for edge in evidence['relations']['relation_evidence'] for ref in edge['evidence'])
        assert query_goal(db,p,t,learning=False)['status']=='sufficient'
        assert db.get(Relation,row['id']).data['evidence_snapshot'][0]['snapshot']['data'].get('name')
        from generic_agents.business_tools import bind_inputs
        from types import SimpleNamespace
        cap=SimpleNamespace(data={'execution':{'input_bindings':{'entity_ids':'entity_ids'}},
            'input_schema':{'type':'object','required':['entity_ids'],'properties':{
                'entity_ids':{'type':'array','items':{'type':'string'}}}}})
        assert bind_inputs(cap,{},t,evidence,'test-related-tool')['entity_ids']==[b['id']]
        assert evaluate(t,type('Plan',(),{'id':'none'})(),evidence)['ready_for_acceptance']
    review(client,row,'withdrawn')
    with Session(engine()) as db:
        assert query_goal(db,p,db.get(Task,task['id']),learning=False)['status']=='insufficient'


def planned_relation_task(client,monkeypatch):
    from .planning_helpers import post_plan
    from .test_goal_time import model
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);row=save(client,pkg,a,b)
    goal=explicit_goal(b,mode='existing_only',source='archive');goal['entity_ids']=[a['id'],b['id']];goal['relation_query']=spec(pkg,a)
    task=client.post('/api/tasks',json={'title':'关系绑定的业务计划','package_id':pkg['id'],
        'entity_ids':goal['entity_ids'],'fields':['temperature'],'goal_spec':goal}).json()
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a,**k:({'summary':'测试合成关系协议',
        'steps':[{'title':'读取','agent':'data','action':'query_evidence'},{'title':'报告','agent':'business','action':'report'}]},
        {'provider':'test','call_id':'explicit-test-fixture','usage':{'total_tokens':0}}))
    m=model(client);r=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],
        'reason':'核查关系绑定','model_profile_id':m['id']});assert r.status_code==200,r.text
    return task,r.json(),row


def test_relation_change_blocks_old_plan_approval(client,monkeypatch):
    task,plan,row=planned_relation_task(client,monkeypatch);review(client,row,'withdrawn')
    r=client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'旧关系已撤回'})
    assert r.status_code==409 and '关系' in r.json()['detail']


def test_reconfirmed_relation_cannot_validate_an_old_report(client,monkeypatch):
    from generic_agents.worker import process_once
    task,plan,row=planned_relation_task(client,monkeypatch)
    r=client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'批准合成取证'});assert r.status_code==200,r.text
    job=next(j for j in client.get('/api/overview').json()['jobs'] if j['kind']=='execute_plan')
    assert process_once(job['id'])
    task=next(t for t in client.get('/api/overview').json()['tasks'] if t['id']==task['id']);assert task['status']=='awaiting_acceptance'
    row=review(client,row,'withdrawn');row=review(client,row,'confirmed')
    r=client.post('/api/tasks/'+task['id']+'/accept',json={'expected_version':task['version'],'reason':'旧报告不能跟随关系新版'})
    assert r.status_code==409 and '证据' in r.json()['detail']


def test_observation_rejection_and_file_integrity_invalidate_relation(client):
    from generic_agents.models import Observation
    from generic_agents.contracts import digest
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg)
    source=publish(client,manifest('source',name='relation_obs',execution={'adapter':'upload'},specification={'timezone':'UTC'}))
    uploaded=client.post('/api/evidence',content=b'supporting synthetic relation record',headers={'Content-Type':'text/plain'})
    assert uploaded.status_code==201,uploaded.text
    with Session(engine()) as db:
        o=Observation(workspace_id=a['workspace_id'],source_id=source['id'],entity_id=a['id'],source_record_id='relation_support',
            processing_version='1',observed_at=now(),business_day=str(now().date()),status='processed',
            data={'extracted':{'attributes':{'temperature':24}},'evidence_ids':[uploaded.json()['id']]})
        db.add(o);db.commit();oid=o.id
    body=claim(pkg,a,b);body['evidence_refs']=[{'kind':'observation','id':oid}]
    r=client.post('/api/relations',json=body);assert r.status_code==201,r.text;row=review(client,r.json(),'confirmed')
    assert query(client,pkg,a,[a,b])['status']=='sufficient'
    with Session(engine()) as db:
        o=db.get(Observation,oid);o.data={**o.data,'human_verification':{'decision':'rejected',
            'input_hash':digest({'extracted':o.data['extracted'],'evidence_ids':o.data['evidence_ids'],'invocation_id':None})}};db.commit()
    assert query(client,pkg,a,[a,b])['status']=='insufficient'

    with Session(engine()) as db:
        o=db.get(Observation,oid);o.data={**o.data,'human_verification':{}};db.commit()
    r=client.patch('/api/relations/'+row['id'],json={**body,'expected_version':row['version']});assert r.status_code==200
    row=review(client,r.json(),'confirmed')
    from generic_agents.config import settings
    from generic_agents.models import Evidence
    with Session(engine()) as db:
        e=db.get(Evidence,uploaded.json()['id']);(settings().storage_dir/e.storage_key).write_bytes(b'altered')
    assert query(client,pkg,a,[a,b])['status']=='insufficient'


def test_second_business_package_reuses_same_relation_and_query_contract(client):
    schema={'type':'object','required':['name'],'properties':{'name':{'type':'string'}}}
    pkg=publish(client,manifest(namespace='asset_maintenance',name='assets',specification={
        'entity_schemas':{'facility':schema,'equipment':{'type':'object','required':['name','load'],
            'properties':{'name':{'type':'string'},'load':{'type':'number'}}}},
        'relation_types':{'installed':{'title':'安装设备','description':'合成资产安装关系',
            'source_types':['facility'],'target_types':['equipment'],'cardinality':'many'}}}))
    def create(kind,attrs):
        r=client.post('/api/entities',json={'package_id':pkg['id'],'entity_type':kind,
            'external_id':uuid4().hex,'attributes':attrs});assert r.status_code==201,r.text;return r.json()
    a=create('facility',{'name':'合成设施'});b=create('equipment',{'name':'合成设备','load':0.75})
    r=client.post('/api/relations',json={**claim(pkg,a,b),'relation_type':'installed'});assert r.status_code==201,r.text
    review(client,r.json(),'confirmed')
    r=client.post('/api/data/queries',json={'question':'合成设施安装设备负载','entity_ids':[a['id'],b['id']],
        'fields':['load'],'mode':'structured','relation_query':{'package_id':pkg['id'],
            'root_ids':[a['id']],'relation_types':['installed']}});assert r.status_code==200,r.text
    assert r.json()['status']=='sufficient' and r.json()['data']['result']['evidence'][0]['attributes']=={'load':0.75}
    bad=client.post('/api/relations',json={**claim(pkg,b,a),'relation_type':'installed'})
    assert bad.status_code==422


@pytest.mark.parametrize('trust_first',[False,True])
@pytest.mark.parametrize('change',['withdraw','add_relation'])
def test_relation_lineage_guards_memory_review_retrieval_and_evolution(client,monkeypatch,trust_first,change):
    from generic_agents.worker import process_once
    from generic_agents.learning import eligible,propose_candidate,candidate_valid
    from generic_agents.security import Principal
    from generic_agents.models import Memory,LearningCandidate
    from fastapi import HTTPException
    task,plan,row=planned_relation_task(client,monkeypatch)
    assert client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'合成批准'}).status_code==200
    job=next(j for j in client.get('/api/overview').json()['jobs'] if j['kind']=='execute_plan');assert process_once(job['id'])
    task=next(t for t in client.get('/api/overview').json()['tasks'] if t['id']==task['id'])
    r=client.post('/api/tasks/'+task['id']+'/accept',json={'expected_version':task['version'],'reason':'核查合成关系证据验收'});assert r.status_code==200,r.text
    memory=next(m for m in client.get('/api/overview').json()['memories'] if m.get('task_id')==task['id'])
    if trust_first:
        r=client.post('/api/memories/'+memory['id']+'/review',json={'expected_version':memory['version'],
            'decision':'trust','reason':'核查合成经验可复用'});assert r.status_code==200,r.text
        with Session(engine()) as db:
            p=Principal(task['owner_id'],task['workspace_id'],'admin','test')
            assert [m.id for m in eligible(db,p,task['package_id'])]==[memory['id']]
            candidate=propose_candidate(db,p,task['package_id'],[memory['id']],'关系来源进化');db.commit();cid=candidate.id
    if change=='withdraw':review(client,row,'withdrawn')
    else:
        pkg={'id':row['package_id']}
        a=next(e for e in client.get('/api/entities').json() if e['id']==row['source_entity_id'])
        c=entity(client,pkg);save(client,pkg,a,c)
    blocked=next(m for m in client.get('/api/overview').json()['memories'] if m['id']==memory['id'])
    assert blocked['relation_reuse_blocked']
    if trust_first:
        with Session(engine()) as db:
            assert eligible(db,p,task['package_id'])==[]
            assert db.get(Memory,memory['id']).status=='trusted'  # Historical human decision survives.
            with pytest.raises(HTTPException):candidate_valid(db,p,db.get(LearningCandidate,cid))
            with pytest.raises(HTTPException):propose_candidate(db,p,task['package_id'],[memory['id']],'不能用已撤回关系进化')
    else:
        r=client.post('/api/memories/'+memory['id']+'/review',json={'expected_version':memory['version'],
            'decision':'trust','reason':'旧案例不得复用'});assert r.status_code==409
