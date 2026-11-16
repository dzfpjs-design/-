"""Operator clarification and paid-response recovery, on disposable PostgreSQL."""
import copy
import json
from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import DataRun,Job,Membership,Entity,now
from generic_agents.worker import process_once
from generic_agents.leases import LeaseLost
from .test_relations import package,save,spec
from .test_vertical_slice import entity
from .test_cloud_boundary import profile


def setup(client):
    pkg=package(client);root=entity(client,pkg);target=entity(client,pkg);save(client,pkg,root,target)
    return pkg,root,target,profile(client)


def enqueue(client,pkg,a,b,model,question='查询指定起点的关联对象温度',**kw):
    r=client.post('/api/data/queries',json={'question':question,'entity_ids':[a['id'],b['id']],
        'fields':['temperature'],'mode':'cloud','model_profile_id':model['id'],**kw})
    assert r.status_code==200,r.text
    return r.json()


def get(client,row):
    r=client.get('/api/data/queries/'+row['id']);assert r.status_code==200,r.text;return r.json()


def response(pkg,a,b,**kw):
    return {'entity_ids':[a['id'],b['id']],'fields':['temperature'],'purpose':'核查起点关联对象档案温度',
        'query_intent':'relations','clarifications':[],'relation_query':spec(pkg,a),**kw}


def fixture_model(monkeypatch,value,calls=None):
    def cloud(model,messages,schema,p):
        if calls is not None:calls.append(copy.deepcopy(messages))
        return copy.deepcopy(value),{'provider':'explicit_test_fixture','usage':{'total_tokens':55}}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud)


def draft(client,row,**kw):
    need=row['data'].get('need',{})
    d={'question':row['question'],'fields':['temperature'],'relation_query':need.get('relation_query'),
        'evidence_requirement':{'source':'archive','minimum_verification':'recorded','time':{'mode':'archive'}},**kw}
    r=client.put('/api/data/queries/'+row['id']+'/draft',json={'expected_version':row['version'],
        'reason':'核对草稿并保存操作者工作','draft':d});assert r.status_code==200,r.text;return r.json()


def confirm(client,row):
    return client.post('/api/data/queries/'+row['id']+'/confirm',json={
        'expected_version':row['version'],'reason':'核对对象、方向、字段及来源，批准限定范围查询'})


def test_proposal_draft_reopen_confirm_and_immutable_parent(client,monkeypatch):
    html=client.get('/');script=client.get('/assets/query-needs.js')
    assert html.status_code==script.status_code==200 and '/assets/query-needs.js' in html.text
    assert script.headers['cache-control']=='private, no-store'
    pkg,a,b,m=setup(client);calls=[];fixture_model(monkeypatch,response(pkg,a,b),calls)
    row=enqueue(client,pkg,a,b,m);assert row['status']=='queued';assert process_once()
    row=get(client,row);assert row['status']=='needs_confirmation' and not row['data'].get('result')
    assert row['data']['need_checkpoint']['receipt']['usage']['total_tokens']==55
    sent=json.dumps(calls,ensure_ascii=False)
    assert a['data']['name'] in sent and 'relation_types' in sent and 'temperature' in sent
    assert 'observations' not in json.loads(calls[0][1]['content']) # no raw observation content
    saved=draft(client,row);restored=get(client,row);assert restored['data']['resolution_draft']==saved['data']['resolution_draft']
    assert client.put('/api/data/queries/'+row['id']+'/draft',json={'expected_version':row['version'],'reason':'旧编辑器','draft':{}}).status_code==409
    r=confirm(client,saved);assert r.status_code==200,r.text;child=r.json()['query'];source=r.json()['source']
    assert source['status']=='resolved' and source['data']['need_checkpoint']==row['data']['need_checkpoint']
    assert child['status']=='sufficient' and child['data']['result']['selected_entity_ids']==[b['id']]
    assert [s['executor'] for s in child['data']['result']['query_plan']['steps']]==['scoped_relation_traversal','scoped_resource_query']
    assert child['data']['parent_query_id']==row['id'] and len(calls)==1
    assert confirm(client,source).status_code==409
    with TestClient(app) as reopened:
        reopened.headers.update(client.headers);reopened.cookies.update(client.cookies)
        assert get(reopened,child)['data']==child['data']


@pytest.mark.parametrize('variant',['omitted','ambiguous','unsupported'])
def test_no_field_only_answer_for_relationship_or_unsupported_need(client,monkeypatch,variant):
    pkg,a,b,m=setup(client)
    value=response(pkg,a,b,relation_query=None)
    if variant=='omitted':value={k:v for k,v in value.items() if k in {'entity_ids','fields','purpose'}}
    if variant=='ambiguous':value['clarifications']=['请选择两个同名对象中的关系起点']
    if variant=='unsupported':value['query_intent']='unsupported'
    fixture_model(monkeypatch,value);row=enqueue(client,pkg,a,b,m);assert process_once();row=get(client,row)
    assert row['status']=='needs_clarification' and not row['data'].get('result')
    assert row['data']['need_checkpoint']


@pytest.mark.parametrize('variant',['scope','field','root','type','code','explicit_change'])
def test_model_cannot_change_authority_or_invent_query_tools(client,monkeypatch,variant):
    pkg,a,b,m=setup(client);value=response(pkg,a,b);kw={}
    if variant=='scope':value['entity_ids']=['outside']
    if variant=='field':value['fields']=['secret_unregistered']
    if variant=='root':value['relation_query']['root_ids']=['outside']
    if variant=='type':value['relation_query']['relation_types']=['invented']
    if variant=='code':value['sql']='select * from users'
    if variant=='explicit_change':kw['relation_query']=spec(pkg,b)
    fixture_model(monkeypatch,value);row=enqueue(client,pkg,a,b,m,**kw);assert process_once();row=get(client,row)
    assert row['status']=='blocked_model' and not row['data'].get('result') and row['data']['need_checkpoint']


def test_structured_ambiguity_no_cloud_and_partial_draft_failure_keeps_human_work(client,monkeypatch):
    pkg,a,b,m=setup(client);fixture_model(monkeypatch,{},[])
    row=enqueue(client,pkg,a,b,m,mode='structured');assert row['status']=='needs_clarification'
    row=draft(client,row,relation_query={'package_id':pkg['id'],'root_ids':[]})
    r=confirm(client,row);assert r.status_code==422
    assert get(client,row)['data']['resolution_draft']['relation_query']['root_ids']==[]
    row=draft(client,get(client,row),relation_query=spec(pkg,a));r=confirm(client,row)
    assert r.status_code==200 and r.json()['query']['status']=='sufficient'


def test_confirmation_rechecks_withdrawn_relation(client,monkeypatch):
    pkg,a,b,m=setup(client);fixture_model(monkeypatch,response(pkg,a,b));row=enqueue(client,pkg,a,b,m);process_once();row=draft(client,get(client,row))
    from .test_relations import review
    relation=client.get('/api/relations').json()['items'][0];review(client,relation,'withdrawn')
    r=confirm(client,row);assert r.status_code==200 and r.json()['query']['status']=='insufficient'
    assert not r.json()['query']['data']['result']['selected_entity_ids']


def test_saved_response_recovered_once_after_crash_and_at_attempt_limit(client,monkeypatch):
    pkg,a,b,m=setup(client);calls=[];fixture_model(monkeypatch,response(pkg,a,b),calls)
    row=enqueue(client,pkg,a,b,m)
    import generic_agents.query_needs as needs
    original=needs.checkpoint;stages=[]
    def crash(db):
        original(db);stages.append(1)
        if len(stages)==2:raise LeaseLost('explicit_crash_after_committed_model_response')
    monkeypatch.setattr(needs,'checkpoint',crash);assert process_once()
    assert get(client,row)['status']=='running' and get(client,row)['data']['need_checkpoint']
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['query_id'].as_string()==row['id']))
        job.lease_until=now()-timedelta(seconds=1);job.attempts=3;db.commit();job_id=job.id
    monkeypatch.setattr(needs,'checkpoint',original);assert process_once(job_id)
    result=get(client,row);assert result['status']=='needs_confirmation' and result['data']['resumed_model_result']
    assert len(calls)==1


def test_no_uncached_cloud_call_at_recovery_limit(client,monkeypatch):
    pkg,a,b,m=setup(client);calls=[];fixture_model(monkeypatch,response(pkg,a,b),calls);row=enqueue(client,pkg,a,b,m)
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['query_id'].as_string()==row['id']));job.attempts=3;db.commit();job_id=job.id
    assert process_once(job_id);assert not calls and get(client,row)['status']=='blocked_model'


def test_cancel_before_or_during_cloud_preserves_late_receipt(client,monkeypatch):
    pkg,a,b,m=setup(client);row=enqueue(client,pkg,a,b,m);calls=[]
    def cloud(*args):
        calls.append(1);latest=get(client,row)
        r=client.post('/api/data/queries/'+row['id']+'/cancel',json={'expected_version':latest['version'],'reason':'停止后续查询'});assert r.status_code==200
        return response(pkg,a,b),{'provider':'explicit_fixture','usage':{'total_tokens':55}}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud);assert process_once();saved=get(client,row)
    assert saved['status']=='cancelled' and not saved['data'].get('result')
    assert saved['data']['need_checkpoint']['receipt']['usage']['total_tokens']==55
    second=enqueue(client,pkg,a,b,m);r=client.post('/api/data/queries/'+second['id']+'/cancel',json={'expected_version':second['version'],'reason':'取消未开始的请求'});assert r.status_code==200
    assert process_once();assert len(calls)==1 and get(client,second)['status']=='cancelled'


def test_directory_change_during_model_wait_blocks_and_preserves_receipt(client,monkeypatch):
    pkg,a,b,m=setup(client);row=enqueue(client,pkg,a,b,m)
    def cloud(*args):
        with Session(engine()) as db:
            e=db.get(Entity,a['id']);e.version+=1;db.commit()
        return response(pkg,a,b),{'provider':'explicit_fixture','usage':{'total_tokens':55}}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud);assert process_once();saved=get(client,row)
    assert saved['status']=='blocked_model' and saved['data']['need_checkpoint'] and '变化' in saved['data']['error']


@pytest.mark.parametrize('mutation',['tampered','entity_changed'])
def test_changed_cached_response_never_calls_cloud_again(client,monkeypatch,mutation):
    pkg,a,b,m=setup(client);calls=[];fixture_model(monkeypatch,response(pkg,a,b),calls)
    row=enqueue(client,pkg,a,b,m);assert process_once()
    with Session(engine()) as db:
        run=db.get(DataRun,row['id']);run.status='queued'
        if mutation=='tampered':
            value=copy.deepcopy(run.data);value['need_checkpoint']['value']['fields']=['name'];run.data=value
        else:
            obj=db.get(Entity,a['id']);obj.version+=1
        job=db.scalar(select(Job).where(Job.data['query_id'].as_string()==row['id']));job.status='queued';db.commit();job_id=job.id
    assert process_once(job_id)
    assert get(client,row)['status']=='blocked_model' and len(calls)==1


def test_statistics_clarification_requires_human_rephrasing(client,monkeypatch):
    pkg,a,b,m=setup(client);calls=[];fixture_model(monkeypatch,response(pkg,a,b),calls)
    row=enqueue(client,pkg,a,b,m,question='关联设备温度的平均值')
    assert row['status']=='needs_clarification' and not calls
    row=draft(client,row,relation_query=spec(pkg,a));assert confirm(client,row).status_code==409
    row=draft(client,get(client,row),question='按关系核查关联设备登记温度',relation_query=spec(pkg,a))
    r=confirm(client,row);assert r.status_code==200 and r.json()['query']['status']=='sufficient'
    assert r.json()['query']['data']['human_confirmation']['source_question']=='关联设备温度的平均值'


def test_revoked_worker_actor_does_not_save_or_activate_model_response(client,monkeypatch,account):
    pkg,a,b,m=setup(client);row=enqueue(client,pkg,a,b,m)
    def cloud(*args):
        with Session(engine()) as db:
            membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));membership.role='viewer';db.commit()
        return response(pkg,a,b),{'provider':'explicit_fixture','usage':{'total_tokens':55}}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud);assert process_once();saved=get(client,row)
    assert saved['status']=='failed' and not saved['data'].get('need_checkpoint') and not saved['data'].get('result')
    assert client.put('/api/data/queries/'+row['id']+'/draft',json={'expected_version':saved['version'],'reason':'越权','draft':{}}).status_code==403
