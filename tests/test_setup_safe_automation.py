"""M48 隔离后端：安全默认和服务器绑定确认，不执行云或设备。"""
import copy,json,os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit,Membership,CloudCall,SetupAnalysis,SetupDraft,SetupRevision,Workspace,User
from generic_agents.instance_compiler import identifier,signature_value
import generic_agents.setup_understanding as understanding
from tests.test_setup import config
from tests.test_setup_understanding import prepared,start,process
from tests.test_setup_objects_runtime import fake_v2,TEXT
from tests.test_instance_workspace import configuration,save,OBJECTS

SOURCE=Path(understanding.__file__).resolve().parents[1]
if os.getenv('GA_TEST_FROZEN_SOURCE'):
    assert SOURCE==Path(os.environ['GA_TEST_FROZEN_SOURCE']).resolve(), 'tests must import the requested frozen source'
ACTIONS={'observe','state','data','supplement','write','learn'}


def suggested(name='人工资料核验',original=''):
    return {'businesses':[{'operation':'upsert','original_name':original,'name':name,
        'trigger':'每天收到异常数据时启动（自由说明不是授权）','output':'人工核验记录',
        'citations':[],'inference_reason':'人工配置',
        'steps':[{'key':'verify','name':'对照资料','purpose':'对照原文','agent':'人工确认','role':'核验负责人','output':'核验记录',
            'citations':[],'inference_reason':'人工配置','condition':'','next_key':'end','else_key':'end'}]}]}


def confirm_flow(c,name):
    flow=c['flows'][name];flow['confirmedAt']='2026-10-12T08:00:00+00:00'
    flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    return c


def test_applied_new_business_is_manual_only_even_when_trigger_text_mentions_schedule_and_exception():
    assert Path(understanding.__file__).resolve().is_relative_to(SOURCE)
    initial=config();result=understanding.apply_configuration(initial,suggested(),['人工资料核验'])
    policy=result['automation']['policies']['人工资料核验']
    assert policy['triggers']==['人工发起']
    assert set(policy['rules'])==ACTIONS
    assert all(r['mode']=='仅给出建议' and not r['resources'] for r in policy['rules'].values())
    assert policy['flow_review']['status']=='conservative_safe'
    assert initial==config()


def test_new_manual_business_without_policy_gets_exact_safe_default(client):
    c=configuration();c.pop('automation');revision=save(client,c)
    current=client.get('/api/setup/draft').json();p=current['configuration']['automation']['policies']['资料核验']
    assert p['triggers']==['人工发起'] and set(p['rules'])==ACTIONS
    assert all(r['mode']=='仅给出建议' and r['resources']==[] for r in p['rules'].values())
    assert scope(current)['status']=='conservative_safe'
    assert checked(client,revision,current['configuration'])['businesses'][0]['available']


def test_existing_and_renamed_business_policy_is_kept_but_needs_review():
    c=configuration();old=copy.deepcopy(c['automation']['policies']['资料核验'])
    old.update(triggers=['定时运行','新观测到达'],time='08:00',human_note='保留人工安排')
    c['automation']['policies']['资料核验']=copy.deepcopy(old)
    for name in ['资料核验','更名资料核验']:
        out=understanding.apply_configuration(c,suggested(name,'资料核验'),[name])
        policy=out['automation']['policies'][name]
        assert {k:v for k,v in policy.items() if k!='flow_review'}==old
        assert policy['flow_review']['status']=='needs_review'
        if name!='资料核验':assert out['automation']['policies']['资料核验']==old


def changed_saved(client):
    c=configuration();install_legacy(client,c)
    c['flows']['资料核验']['output']='新核验结论';confirm_flow(c,'资料核验')
    revision=save(client,c)
    return client.get('/api/setup/draft').json(),revision


def install_legacy(client,c):
    # 仅隔离测试构造发布前已存在记录；新的API首次策略不视为legacy。
    view=client.get('/api/setup/draft').json()
    with Session(engine()) as db:
        row=db.get(SetupDraft,view['id']);row.data=copy.deepcopy(c);db.commit()


def scope(view):
    return next(x for x in view['automation_review']['businesses'] if x['business_name']=='资料核验')


def review(client,view,request=None):
    body=request or {'revision':view['revision'],'business_id':scope(view)['business_id'],'request_id':str(uuid4())}
    return client.post('/api/setup/automation/review',json=body),body


def checked(client,revision,c):
    return client.post('/api/instances/check',json={'revision':revision,'objects':OBJECTS,
        'role_bindings':{'核验负责人':client.get('/api/instances/members').json()[0]['id']}}).json()


def test_changed_legacy_flow_is_blocked_until_explicit_review_then_replay_cannot_confirm_later_change(client):
    view,revision=changed_saved(client);row=scope(view)
    assert row['status']=='needs_review'
    first=checked(client,revision,view['configuration']);assert not first['businesses'][0]['available']
    assert any('第4步' in x for x in first['businesses'][0]['issues'])
    response,body=review(client,view);assert response.status_code==200,response.text
    saved=response.json();assert scope(saved)['status']=='confirmed'
    assert saved['review_receipt']['saved_revision']==saved['revision']
    assert checked(client,saved['revision'],saved['configuration'])['businesses'][0]['available']
    same=client.post('/api/setup/automation/review',json=body)
    assert same.status_code==200 and same.json()['revision']==saved['revision'] and same.json()['review_receipt']['replayed']
    c=copy.deepcopy(saved['configuration']);c['automation']['policies']['资料核验']['triggers']=['定时运行'];c['automation']['policies']['资料核验']['time']='09:00'
    changed=save(client,c);again=client.post('/api/setup/automation/review',json=body)
    assert again.status_code==200 and again.json()['revision']==changed
    assert scope(again.json())['status']=='needs_review'
    with Session(engine()) as db:assert len(db.scalars(select(Audit).where(Audit.action=='setup.automation.review')).all())==1


@pytest.mark.parametrize('forgery',['deleted','confirmed','hashes','legacy'])
def test_client_cannot_strip_or_forge_server_review_metadata(client,forgery):
    view,_=changed_saved(client);c=copy.deepcopy(view['configuration']);policy=c['automation']['policies']['资料核验']
    if forgery=='deleted':policy.pop('flow_review')
    elif forgery=='confirmed':policy['flow_review']['status']='confirmed'
    elif forgery=='hashes':policy['flow_review'].update(status='confirmed',flow_hash='a'*64,policy_hash='b'*64)
    else:policy['flow_review']={'status':'legacy_reviewed'}
    r=client.put('/api/setup/draft',json={'revision':view['revision'],'configuration':c});assert r.status_code==200,r.text
    assert scope(r.json())['status']=='needs_review'
    assert not checked(client,r.json()['revision'],r.json()['configuration'])['businesses'][0]['available']


def test_unchanged_legacy_policy_remains_compatible_and_objects_only_do_not_invalidate_it(client):
    c=configuration();install_legacy(client,c);view=client.get('/api/setup/draft').json()
    assert scope(view)['status']=='legacy_reviewed'
    assert 'flow_review' not in view['configuration']['automation']['policies']['资料核验']
    c['instanceObjects']=[{'name':'新档案','fields':[{'label':'编号','kind':'文本','required':True}]}]
    revision=save(client,c);current=client.get('/api/setup/draft').json()
    assert scope(current)['status']=='legacy_reviewed' and checked(client,revision,c)['businesses'][0]['available']


@pytest.mark.parametrize('removed',['automation','policy'])
def test_removed_then_recreated_policy_cannot_launder_pending_review_into_legacy(client,removed):
    view,_=changed_saved(client);original=copy.deepcopy(view['configuration']['automation']['policies']['资料核验'])
    c=copy.deepcopy(view['configuration'])
    if removed=='automation':c.pop('automation')
    else:c['automation']['policies'].pop('资料核验')
    revision=save(client,c);now=client.get('/api/setup/draft').json()
    assert scope(now)['status']=='needs_review'
    assert now['configuration']['automation']['policies']['资料核验']['rules']=={}
    refused,_=review(client,now);assert refused.status_code==409
    original.pop('flow_review');c=copy.deepcopy(now['configuration']);c['automation']['policies']['资料核验']=original
    revision=save(client,c);rebuilt=client.get('/api/setup/draft').json()
    assert scope(rebuilt)['status']=='needs_review' and not checked(client,revision,c)['businesses'][0]['available']


@pytest.mark.parametrize('history',['new','business_recreated'])
def test_new_client_policy_never_becomes_legacy_and_cannot_claim_confirmation(client,history):
    c=configuration()
    if history=='business_recreated':
        install_legacy(client,c);empty=copy.deepcopy(c);empty.update(businesses=[],flows={},automation={'version':1,'policies':{}})
        save(client,empty)
    p=c['automation']['policies']['资料核验'];p.update(triggers=['定时运行'],time='08:00',flow_review={'status':'confirmed'})
    revision=save(client,c);current=client.get('/api/setup/draft').json()
    assert scope(current)['status']=='needs_review' and not checked(client,revision,c)['businesses'][0]['available']


def test_removed_business_keeps_historical_human_draft_and_retained_legacy_policy_cannot_launder(client):
    c=configuration();c['automation']['policies']['资料核验']['human_note']='旧人工规则保留在版本历史'
    install_legacy(client,c);before=client.get('/api/setup/draft').json()
    empty=copy.deepcopy(c);empty.update(businesses=[],flows={},automation={'version':1,'policies':{}})
    save(client,empty);current=client.get('/api/setup/draft').json()
    assert current['configuration']['automation']['policies']=={} and 'flowRecovery' not in current['configuration']
    with Session(engine()) as db:
        historical=db.scalar(select(SetupRevision).where(SetupRevision.draft_id==before['id'],SetupRevision.version==before['revision']))
        assert historical.data==c
    # 客户端仍保留旧policy旁支时，同名重新加入也不能被判断为未修改的legacy。
    retained=copy.deepcopy(c);retained.update(businesses=[],flows={})
    save(client,retained);save(client,c);rebuilt=client.get('/api/setup/draft').json()
    assert scope(rebuilt)['status']=='needs_review'


def test_removed_policy_rebuilt_as_safe_still_requires_explicit_review(client):
    from generic_agents.setup_automation import safe_policy
    view,_=changed_saved(client);c=copy.deepcopy(view['configuration']);c.pop('automation');save(client,c)
    current=client.get('/api/setup/draft').json();c=current['configuration'];c['automation']['policies']['资料核验']=safe_policy()
    save(client,c);rebuilt=client.get('/api/setup/draft').json()
    assert scope(rebuilt)['status']=='needs_review'
    response,_=review(client,rebuilt);assert response.status_code==200 and scope(response.json())['status']=='confirmed'


def test_confirmation_timestamp_and_random_step_ids_are_not_new_flow_semantics(client):
    view,_=changed_saved(client);response,_=review(client,view);assert response.status_code==200,response.text
    saved=response.json();c=copy.deepcopy(saved['configuration']);flow=c['flows']['资料核验']
    mapping={s['id']:'step-'+str(n) for n,s in enumerate(flow['steps'])}
    for s in flow['steps']:
        s['id']=mapping[s['id']]
        for k in ['nextId','elseId']:s[k]=mapping.get(s.get(k),s.get(k))
    confirm_flow(c,'资料核验');flow['confirmedAt']='2026-10-12T09:00:00+00:00'
    save(client,c);assert scope(client.get('/api/setup/draft').json())['status']=='confirmed'


def test_review_probe_exact_body_actor_scope_and_definite_cas_failure(client,account):
    view,_=changed_saved(client);key=str(uuid4());sid=scope(view)['business_id']
    assert client.get('/api/setup/automation/review/requests/'+key,params={'business_id':sid}).status_code==404
    stale={'revision':view['revision']-1,'business_id':sid,'request_id':key}
    failed=client.post('/api/setup/automation/review',json=stale);assert failed.status_code==409
    assert failed.json()['detail']=='配置版本已更新，本次自动化确认未提交。请读取最新配置后重新核对。'
    ok,body=review(client,view);assert ok.status_code==200,ok.text
    probe=client.get('/api/setup/automation/review/requests/'+body['request_id'],params={'business_id':sid})
    assert probe.status_code==200 and probe.json()['review_receipt']['replayed']
    assert client.post('/api/setup/automation/review',json={**body,'revision':body['revision']+1}).status_code==409
    assert client.get('/api/setup/automation/review/requests/'+body['request_id'],params={'business_id':identifier('business','其他业务')}).status_code==404
    with Session(engine()) as db:
        m=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));m.role='viewer';db.commit()
    assert client.post('/api/setup/automation/review',json=body).status_code==403


def test_review_nonce_cannot_move_to_another_workspace_and_probe_discloses_no_receipt(client,account):
    view,_=changed_saved(client);response,body=review(client,view);assert response.status_code==200
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]))
        other=Workspace(name='另一隔离工作区');db.add(other);db.flush()
        db.add(Membership(user_id=member.user_id,workspace_id=other.id,role='admin'));db.commit();other_id=other.id
    client.headers['X-Workspace-ID']=other_id
    c=configuration();save(client,c);current=client.get('/api/setup/draft').json()
    moved=client.post('/api/setup/automation/review',json={**body,'revision':current['revision']})
    assert moved.status_code==409 and scope(client.get('/api/setup/draft').json())['status']=='needs_review'
    assert client.get('/api/setup/automation/review/requests/'+body['request_id'],params={'business_id':body['business_id']}).status_code==404
    with Session(engine()) as db:
        assert len(db.scalars(select(Audit).where(Audit.action=='setup.automation.review')).all())==1


def test_review_receipt_cannot_be_replayed_or_probed_by_another_actor(client,account):
    from generic_agents.api import app
    from generic_agents.security import password_hash
    view,_=changed_saved(client);response,body=review(client,view);assert response.status_code==200
    username='review_'+uuid4().hex;password=uuid4().hex
    with Session(engine()) as db:
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=account[2],role='admin'));db.commit()
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':password});assert login.status_code==200
        other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert other.post('/api/setup/automation/review',json=body).status_code==409
        assert other.get('/api/setup/automation/review/requests/'+body['request_id'],params={'business_id':body['business_id']}).status_code==404


def test_same_nonce_concurrent_cross_workspace_reviews_commit_exactly_once(client,account):
    from generic_agents.api import app
    c=configuration();save(client,c);first=client.get('/api/setup/draft').json()
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]))
        workspace=Workspace(name='并发隔离工作区');db.add(workspace);db.flush()
        db.add(Membership(user_id=member.user_id,workspace_id=workspace.id,role='admin'));db.commit();workspace_id=workspace.id
    with TestClient(app) as other:
        other.cookies.update(client.cookies);other.headers.update(dict(client.headers));other.headers['X-Workspace-ID']=workspace_id
        save(other,c);second=other.get('/api/setup/draft').json();assert first['revision']==second['revision']
        body={'revision':first['revision'],'business_id':scope(first)['business_id'],'request_id':str(uuid4())}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda connection:connection.post('/api/setup/automation/review',json=body),[client,other]))
        assert sorted(r.status_code for r in responses)==[200,409]
        assert sorted(scope(connection.get('/api/setup/draft').json())['status'] for connection in [client,other])==['confirmed','needs_review']
    with Session(engine()) as db:
        assert len(db.scalars(select(Audit).where(Audit.action=='setup.automation.review')).all())==1


def test_actual_v3_paid_apply_objects_and_business_safe_default_and_undo_are_durable(client,monkeypatch):
    body,_=prepared(client,TEXT);body.update(contract_version=3,request_id=str(uuid4()))
    calls=fake_v2(monkeypatch);row,_=start(client,body);process(row)
    response=client.get('/api/setup/understanding/'+row['id']);assert response.json()['status']=='review'
    before=client.get('/api/setup/draft').json()
    with Session(engine()) as db:raw=copy.deepcopy(db.get(SetupAnalysis,row['id']).data['raw_response'])
    applied=client.post('/api/setup/understanding/'+row['id']+'/apply',json={'version':response.json()['version'],
        'revision':body['revision'],'names':['异常复核'],'object_names':['设备']});assert applied.status_code==200,applied.text
    cfg=applied.json()['configuration'];policy=cfg['automation']['policies']['异常复核']
    assert policy['triggers']==['人工发起'] and all(x['mode']=='仅给出建议' for x in policy['rules'].values())
    assert cfg['instanceObjects'][0]['name']=='设备'
    assert client.get('/api/setup/draft').json()['configuration']==cfg
    with Session(engine()) as db:
        assert db.get(SetupAnalysis,row['id']).data['raw_response']==raw and len(db.scalars(select(CloudCall)).all())==1
    undo=client.post('/api/setup/understanding/'+row['id']+'/undo',json={'version':applied.json()['analysis']['version'],
        'revision':applied.json()['revision'],'names':['异常复核'],'object_names':['设备']})
    assert undo.status_code==200 and undo.json()['configuration']==before['configuration'] and len(calls)==1
