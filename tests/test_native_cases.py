"""Native methods are independent from legacy trusted cases and tighten real acceptance."""
import copy
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task, Memory, Entity, Membership, User, CloudCall, Acquisition, now
from generic_agents.contracts import digest
from tests.test_native_goals import setup, define, drain
from tests.test_instance_workspace import object_row, task_row, complete, prepared, enable


def url(task): return '/api/workspace/tasks/'+task['id']+'/experience'


def ready(client,task):
    current=client.get('/api/workspace/tasks/'+task['id']).json()
    while current['status']=='workflow_waiting':
        response=complete(client,current);assert response.status_code==200,response.text;current=response.json()
    drain(task)
    return client.get('/api/workspace/tasks/'+task['id']+'/goal').json()


def accept(client,task):
    goal=ready(client,task)
    response=client.post('/api/workspace/tasks/'+task['id']+'/goal/accept',json={
        'request_id':str(uuid4()),'expected_version':goal['version'],'basis_hash':goal['check']['evaluation']['basis_hash'],
        'reason':'已核对当前原资料与原业务流程，保存正式人工验收'})
    return response


def another(client,release,spec,index=0):
    obj=object_row(client,release,index)
    task,_=task_row(client,release,obj)
    goal_spec=copy.deepcopy(spec);goal_spec['entity_ids']=[obj['id']]
    define(client,'/api/workspace/tasks/'+task['id']+'/goal',{'request_id':str(uuid4()),'expected_version':task['version'],
        'reason':'确认独立后续任务的当前资料要求','goal_spec':goal_spec})
    ready(client,task)
    return obj,task


def command(view,**extra):
    return {'request_id':str(uuid4()),'expected_version':view['version'],'reason':'对照原依据核查并保存',**extra}


def case_body(view):
    case=view['draft']
    return command(view,case_id=case['id'],expected_case_version=case['version'],content_hash=case['content_hash'])


def trust(client,task,kinds=('source_identity',),valid_until=None,reviewer=None):
    view=client.get(url(task)).json()
    field=view['field_options'][0]['key']
    body=command(view,acceptance_hash=view['source']['acceptance_hash'],title='后续核查原件与业务对象的对应关系',
        summary='先核对当前原件来源，再按原目标核查，不复制旧任务事实',limits='仅本业务版本适用，不证明现场状态',
        steps=[{'id':'item'+str(i),'kind':kind,'field':field,'instruction':'请对照本次当前资料核查；不明确时保存未知'} for i,kind in enumerate(kinds)])
    response=client.post(url(task)+'/draft',json=body);assert response.status_code==200,response.text;view=response.json()
    response=client.post(url(task)+'/submit',json=case_body(view));assert response.status_code==200,response.text;view=response.json()
    response=(reviewer or client).post(url(task)+'/review',json={**case_body(view),'decision':'trust','valid_until':valid_until});assert response.status_code==200,response.text
    return response.json()


def pair(client,kinds=('source_identity',),valid_until=None,reviewer=None,*,prepared_source=None):
    release,obj,a,base,body=prepared_source if prepared_source is not None else setup(client)
    define(client,base,body);response=accept(client,a);assert response.status_code==200,response.text
    trusted=trust(client,a,kinds,valid_until,reviewer)
    obj_b,b=another(client,release,body['goal_spec'])
    view=client.get(url(b)).json();case=view['available_cases'][0]
    adoption=command(view,case_id=case['id'],expected_case_version=case['version'],content_hash=case['content_hash'],basis_hash=view['current_basis_hash'])
    response=client.post(url(b)+'/adopt',json=adoption);assert response.status_code==200,response.text
    return release,obj,a,b,response.json(),trusted,adoption


def confirm(client,task,view,item='item0',conclusion='confirmed'):
    body=command(view,use_hash=view['use']['use_hash'],basis_hash=view['use']['current_basis_hash'],item_id=item,conclusion=conclusion)
    return client.post(url(task)+'/confirm',json=body),body


def test_real_method_consumption_blocks_acceptance_then_freezes_current_proof(client):
    _,_,a,b,view,trusted,adoption=pair(client)
    assert not view['use']['all_confirmed']
    response=accept(client,b);assert response.status_code==409
    view=client.get(url(b)).json();saved,confirmation=confirm(client,b,view);assert saved.status_code==200,saved.text
    assert saved.json()['use']['all_confirmed']
    response=accept(client,b);assert response.status_code==200,response.text
    accepted=response.json()['acceptance'];proof=accepted['experience_proof']
    assert proof['use_hash']==saved.json()['use']['use_hash']
    assert proof['case_snapshot']['case_record_hash']==proof['case_record_hash']
    assert proof['items'][0]['confirmation']['basis_hash']==accepted['basis_hash']
    assert proof['items'][0]['confirmation']['refs']==proof['items'][0]['current_refs']
    assert accepted['experience_proof_hash']==digest(proof)
    with Session(engine()) as db:
        t=db.get(Task,b['id']);outcome=db.get(Memory,t.data['native_goal_data_receipt_id'])
        assert outcome.data['acceptance']==accepted
        cases=list(db.scalars(select(Memory).where(Memory.data['type'].as_string()=='native_procedure_case')))
        assert len(cases)==1 and cases[0].task_id==a['id'] and 'package_id' not in cases[0].data
        from generic_agents.learning import eligible
        from generic_agents.security import Principal
        member=db.scalar(select(Membership))
        assert eligible(db,Principal(member.user_id,member.workspace_id,member.role,'test'),cases[0].data['native_package_id'])==[]
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Acquisition)))
    old=client.post('/api/memories/'+trusted['draft']['id']+'/review',json={'expected_version':trusted['draft']['version'],'reason':'旧审核不得混入','decision':'trust'})
    assert old.status_code==409
    revoke=client.post(url(a)+'/revoke',json=case_body(client.get(url(a)).json()));assert revoke.status_code==200,revoke.text
    historical=client.get(url(b));assert historical.status_code==200 and historical.json()['use']['all_confirmed']
    assert historical.json()['use']['items'][0]['confirmation']['refs']==proof['items'][0]['current_refs']


def test_current_basis_change_requires_recheck_and_preserves_original_confirmations(client):
    _,_,_,b,view,_,_=pair(client)
    saved,body=confirm(client,b,view);assert saved.status_code==200,saved.text
    entity_id=saved.json()['use']['items'][0]['current_refs'][0]['entity_id'];field=saved.json()['use']['items'][0]['field']
    with Session(engine()) as db:
        entity=db.get(Entity,entity_id);entity.data={**entity.data,field:'当次档案的新登记'};entity.version+=1;db.commit()
    changed=client.get(url(b)).json()
    assert changed['use']['items'][0]['confirmation']['stale'] and not changed['use']['all_confirmed']
    assert len(changed['history']['confirmations'])==1
    stale={**body,'request_id':str(uuid4()),'expected_version':changed['version']}
    assert client.post(url(b)+'/confirm',json=stale).status_code==409
    response=accept(client,b);assert response.status_code==409
    changed=client.get(url(b)).json();saved,_=confirm(client,b,changed);assert saved.status_code==200,saved.text
    assert accept(client,b).status_code==200


def test_revocation_blocks_confirmation_and_future_adoption_disengage_keeps_history(client):
    release,_,a,b,view,_,_=pair(client)
    response=client.post(url(a)+'/revoke',json=case_body(client.get(url(a)).json()));assert response.status_code==200,response.text
    view=client.get(url(b)).json();assert view['use']['blocked_reason'] and not view['permissions']['confirm']['allowed']
    response,_=confirm(client,b,view);assert response.status_code==409
    assert accept(client,b).status_code==409
    view=client.get(url(b)).json()
    response=client.post(url(b)+'/disengage',json=command(view,use_hash=view['use']['use_hash'],reason='经验已撤回，明确解除额外清单，仍按原目标验收'))
    assert response.status_code==200,response.text
    assert response.json()['use'] is None and len(response.json()['history']['disengagements'])==1
    assert accept(client,b).status_code==200
    with Session(engine()) as db: spec=copy.deepcopy(db.get(Task,b['id']).data['goal_spec'])
    _,d=another(client,release,spec);v=client.get(url(d)).json();assert not v['available_cases']


def test_duplicate_uuid_recovers_exact_saved_result_and_abandon_blocks_late_original(client):
    _,_,_,b,view,_,adoption=pair(client)
    repeated=client.post(url(b)+'/adopt',json=adoption);assert repeated.status_code==200,repeated.text
    assert len(repeated.json()['history']['uses'])==1
    assert client.post(url(b)+'/adopt',json={**adoption,'reason':'不能更换原UUID正文'}).status_code==409
    response=client.post(url(b)+'/requests/recover',json={'operation':'adopt','original_request':adoption,
        'expected_version':view['version'],'reason':'核对原采用是否已保存','action':'probe'})
    assert response.status_code==200 and response.json()['saved']
    bad=command(view,use_hash=view['use']['use_hash'],basis_hash='0'*64,item_id='item0',conclusion='confirmed')
    assert client.post(url(b)+'/confirm',json=bad).status_code==409
    recovery={'operation':'confirm','original_request':bad,'expected_version':view['version'],'reason':'明确作废未保存请求，保留清单','action':'abandon'}
    response=client.post(url(b)+'/requests/recover',json=recovery);assert response.status_code==200,response.text
    assert response.json()['abandoned']
    assert client.post(url(b)+'/confirm',json=bad).status_code==409
    assert len(client.get(url(b)).json()['history']['confirmations'])==0


@pytest.mark.parametrize('kind',['source_time','comparability'])
def test_unknown_context_cannot_be_confirmed_but_unknown_is_saved(client,kind):
    _,_,_,b,view,_,_=pair(client,(kind,))
    assert not view['use']['items'][0]['evidence_ready']
    response,_=confirm(client,b,view);assert response.status_code==409
    response,_=confirm(client,b,view,conclusion='unknown');assert response.status_code==200,response.text
    assert not response.json()['use']['all_confirmed']
    assert accept(client,b).status_code==409


def test_numeric_unknown_units_remain_unknown_and_text_units_are_rejected(client):
    release,_,_=prepared(client);enable(client,release)
    obj=object_row(client,release,1)
    field=next(item['key'] for item in release['snapshot']['object_types'][1]['fields'] if item['label']=='数量')
    with Session(engine()) as db:
        entity=db.get(Entity,obj['id']);entity.data={**entity.data,field:2};entity.version+=1;db.commit()
    task,_=task_row(client,release,obj)
    spec={'purpose':'核对本次数量档案','entity_ids':[obj['id']],'required_fields':[field],
        'subgoals':[{'id':'check','description':'核对本次档案','criterion_ids':['enough','human']}],
        'criteria':[{'id':'enough','kind':'evidence_sufficient','description':'本次资料齐全'}, {'id':'human','kind':'human_confirmation','description':'人工验收'}],
        'evidence':{'source':'archive','minimum_verification':'recorded','time':{'mode':'archive','timezone':'Asia/Shanghai'}}}
    define(client,'/api/workspace/tasks/'+task['id']+'/goal',{'request_id':str(uuid4()),'expected_version':task['version'],'reason':'确认数值资料要求','goal_spec':spec})
    assert accept(client,task).status_code==200
    trusted=trust(client,task,('units',))
    obj_b,b=another(client,release,spec,1)
    with Session(engine()) as db:
        entity=db.get(Entity,obj_b['id']);entity.data={**entity.data,field:3};entity.version+=1;db.commit()
    v=client.get(url(b)).json();case=v['available_cases'][0]
    response=client.post(url(b)+'/adopt',json=command(v,case_id=case['id'],expected_case_version=case['version'],content_hash=case['content_hash'],basis_hash=v['current_basis_hash']))
    assert response.status_code==200,response.text;v=response.json()
    assert v['use']['items'][0]['current_refs'][0]['value']==3 and not v['use']['items'][0]['evidence_ready']
    assert confirm(client,b,v)[0].status_code==409
    assert confirm(client,b,v,conclusion='unknown')[0].status_code==200
    # A new text case cannot quietly reinterpret text as measured units.
    _,_,text,base,body=setup(client);define(client,base,body);assert accept(client,text).status_code==200
    v=client.get(url(text)).json()
    response=client.post(url(text)+'/draft',json=command(v,acceptance_hash=v['source']['acceptance_hash'],title='错误单位模板',steps=[{'id':'units','kind':'units','field':v['field_options'][0]['key']}]))
    assert response.status_code==422


@pytest.mark.parametrize('target',['source_acceptance','outcome','case_content','case_status','case_scope','usage','confirmation'])
def test_partial_proof_tampering_never_authorizes_consumption(client,target):
    _,_,a,b,view,_,_=pair(client)
    if target=='confirmation':
        response,_=confirm(client,b,view);assert response.status_code==200;view=response.json()
    with Session(engine()) as db:
        source=db.get(Task,a['id']);current=db.get(Task,b['id']);case=db.get(Memory,view['use']['case_id'])
        if target=='source_acceptance':
            data=copy.deepcopy(source.data);data['native_goal_acceptance']['reason']='损坏';source.data=data
        elif target=='outcome':
            row=db.get(Memory,source.data['native_goal_data_receipt_id']);data=copy.deepcopy(row.data);data['workflow_receipts']=[];row.data=data
        elif target=='case_content':
            data=copy.deepcopy(case.data);data['content']['steps'][0]['field']='其它字段';case.data=data
        elif target=='case_status': case.status='trusted' if case.status!='trusted' else 'revoked'
        elif target=='case_scope':
            data=copy.deepcopy(case.data);data['scope']['instance_id']='其它实例';case.data=data
        elif target=='usage':
            data=copy.deepcopy(current.data);data['native_experience']['uses'][0]['snapshot']['case_id']='其它经验';current.data=data
        else:
            data=copy.deepcopy(current.data);data['native_experience']['confirmations'][0]['refs'][0]['value']='旧值冒充';current.data=data
        db.commit()
    response=client.get(url(b))
    assert response.status_code==409 or response.status_code==200 and response.json()['use']['blocked_reason']
    goal=client.get('/api/workspace/tasks/'+b['id']+'/goal')
    if goal.status_code==200:
        response=client.post('/api/workspace/tasks/'+b['id']+'/goal/accept',json={
            'request_id':str(uuid4()),'expected_version':goal.json()['version'],'basis_hash':view['use']['current_basis_hash'],'reason':'损坏证明不能验收'})
        assert response.status_code==409


def test_future_expiry_is_honored_without_modifying_case_or_approval(client,monkeypatch):
    expires=now()+timedelta(hours=1)
    _,_,a,b,view,_,_=pair(client,valid_until=expires.isoformat())
    from generic_agents import native_cases
    monkeypatch.setattr(native_cases,'now',lambda:expires+timedelta(seconds=1))
    v=client.get(url(b)).json();assert '到期' in v['use']['blocked_reason'] and confirm(client,b,v)[0].status_code==409


def test_real_approval_role_revocation_blocks_use_and_allows_explicit_withdrawal(client,account):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents.security import password_hash
    prepared_source=setup(client)
    username,password='reviewer_'+uuid4().hex,uuid4().hex
    with Session(engine()) as db:
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        member=Membership(user_id=user.id,workspace_id=account[2],role='admin');db.add(member);db.commit();identity=member.id
    with TestClient(app) as reviewer:
        login=reviewer.post('/api/login',json={'username':username,'password':password});assert login.status_code==200
        reviewer.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        _,_,a,b,view,_,_=pair(client,reviewer=reviewer,prepared_source=prepared_source)
    with Session(engine()) as db:
        member=db.get(Membership,identity);member.role='viewer';db.commit()
    v=client.get(url(b)).json();assert '权限' in v['use']['blocked_reason']
    assert confirm(client,b,v)[0].status_code==409
    original=client.get(url(a)).json()
    assert original['permissions']['revoke']['allowed']
    response=client.post(url(a)+'/revoke',json=case_body(original));assert response.status_code==200,response.text


def reported(client,release,obj,source,rule,attrs,clock=None,verify=True):
    from generic_agents.models import Job,Observation
    from generic_agents.worker import process_once
    clock=clock or now()-timedelta(seconds=1)
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/reported-observations',json={
        'source_id':source['id'],'source_record_id':str(uuid4()),'entity_id':obj['id'],'extractor_id':rule['id'],
        'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual','attributes':attrs})
    assert response.status_code==201,response.text;identity=response.json()['id']
    with Session(engine()) as db: job=db.scalar(select(Job.id).where(Job.idempotency_key=='extract:'+identity))
    assert process_once(job,deliver=False)
    if verify:
        with Session(engine()) as db:
            row=db.get(Observation,identity)
            body={'request_id':str(uuid4()),'expected_version':row.version,
                'expected_input_hash':digest({'extracted':row.data['extracted'],'evidence_ids':row.data.get('evidence_ids',[]),'invocation_id':row.data['invocation_id']}),
                'decision':'confirmed','reason':'核对本次JSON原件与本地提取回执，不证明现场'}
        response=client.post('/api/observations/'+identity+'/verify',json=body);assert response.status_code==200,response.text
    return identity


def observation_pair(client,kinds=('source_identity',),two=False):
    from tests.test_vertical_slice import publish,manifest
    release,obj,a,base,body=setup(client,source='observations')
    source=publish(client,manifest('source',name='procedure_report',allowed_agent_roles=['perception'],execution={'adapter':'upload'},
        specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    rule=publish(client,manifest('tool',name='procedure_rule',allowed_agent_roles=['perception'],execution={'adapter':'builtin','handler':'numeric_rules'},
        specification={'rules':[{'id':'high','field':'公开登记值','operator':'gt','threshold':50}]},data_policy={'cloud_allowed':False}))
    field=body['goal_spec']['required_fields'][0]
    aid=reported(client,release,obj,source,rule,{field:obj['attributes'][field],'公开登记值':1})
    define(client,base,body);assert accept(client,a).status_code==200
    trusted=trust(client,a,kinds)
    obj_b,b=another(client,release,body['goal_spec'])
    bid=reported(client,release,obj_b,source,rule,{field:obj_b['attributes'][field],'公开登记值':2,'单位说明':'米'})
    if two: reported(client,release,obj_b,source,rule,{field:obj_b['attributes'][field],'公开登记值':2,'单位说明':'千克'},clock=now()-timedelta(hours=1))
    drain(b);v=client.get(url(b)).json();case=v['available_cases'][0]
    response=client.post(url(b)+'/adopt',json=command(v,case_id=case['id'],expected_case_version=case['version'],content_hash=case['content_hash'],basis_hash=v['current_basis_hash']))
    assert response.status_code==200,response.text
    return a,b,response.json(),aid,bid


def test_observation_time_uses_current_allowed_observations_not_archive_duplicate(client):
    a,b,v,aid,bid=observation_pair(client,('source_time',))
    item=v['use']['items'][0]
    assert item['evidence_ready'] and len(item['current_refs'])==1
    assert item['current_refs'][0]['kind']=='observation' and item['current_refs'][0]['source_id']==bid
    assert item['current_refs'][0]['source_id']!=aid
    assert confirm(client,b,v)[0].status_code==200
    assert accept(client,b).status_code==200


def test_damaged_current_observation_review_history_cannot_confirm_method(client):
    from generic_agents.models import Observation
    a,b,v,aid,bid=observation_pair(client)
    response,_=confirm(client,b,v);assert response.status_code==200,response.text
    confirmed=response.json()
    with Session(engine()) as db:
        row=db.get(Observation,bid)
        assert row.data['verification_history'] and row.data['verification_history_hash']
        data=copy.deepcopy(row.data);data['verification_history']=[];row.data=data
        task=db.get(Task,b['id']);saved_version=task.version
        saved_confirmations=copy.deepcopy(task.data['native_experience']['confirmations'])
        db.commit()
    current=client.get(url(b));assert current.status_code==200,current.text
    view=current.json()
    assert not view['use']['all_confirmed'] and '核查历史摘要' in view['use']['blocked_reason']
    assert not view['permissions']['confirm']['allowed']
    retry={**command(view),'use_hash':confirmed['use']['use_hash'],
        'basis_hash':confirmed['use']['current_basis_hash'],'item_id':'item0','conclusion':'confirmed'}
    response=client.post(url(b)+'/confirm',json=retry)
    assert response.status_code==409 and '核查历史摘要' in response.json()['detail']
    goal=client.get('/api/workspace/tasks/'+b['id']+'/goal');assert goal.status_code==200,goal.text
    assert not goal.json()['check']['evaluation']['ready_for_acceptance']
    assert goal.json()['reviewable_records']==[]
    assert accept(client,b).status_code==409
    with Session(engine()) as db:
        task=db.get(Task,b['id'])
        assert task.version==saved_version
        assert task.data['native_experience']['confirmations']==saved_confirmations
        assert not task.data.get('native_goal_acceptance')


def test_two_actual_originals_with_unknown_different_units_do_not_prove_comparability(client):
    a,b,v,aid,bid=observation_pair(client,('comparability',),two=True)
    item=v['use']['items'][0]
    assert len(item['current_refs'])==2 and len({ref['original_fingerprint'] for ref in item['current_refs']})==2
    assert not item['evidence_ready']
    assert confirm(client,b,v)[0].status_code==409
    assert confirm(client,b,v,conclusion='unknown')[0].status_code==200
    assert accept(client,b).status_code==409


def test_actual_source_reported_json_damage_blocks_reuse_but_can_be_revoked(client):
    from generic_agents.models import Observation
    a,b,v,aid,bid=observation_pair(client)
    with Session(engine()) as db:
        original=db.get(Observation,aid);data=copy.deepcopy(original.data)
        data['original_request']['attributes']['公开登记值']=999
        original.data=data;db.commit()
    refreshed=client.get(url(b));assert refreshed.status_code==200,refreshed.text
    assert refreshed.json()['use']['blocked_reason']
    assert confirm(client,b,refreshed.json())[0].status_code==409
    original=client.get(url(a)).json();assert original['permissions']['revoke']['allowed']
    response=client.post(url(a)+'/revoke',json=case_body(original));assert response.status_code==200,response.text


def test_real_saved_source_image_bytes_are_checked_before_future_reuse(client):
    from generic_agents.models import Evidence
    from generic_agents.config import settings
    from tests.test_workflow_images import upload,create
    release,obj,old,base,body=setup(client)
    image=upload(client,release);assert image.status_code==201,image.text
    response=create(client,release,obj,[image.json()['id']]);assert response.status_code==201,response.text
    a=response.json();define(client,'/api/workspace/tasks/'+a['id']+'/goal',{
        **body,'request_id':str(uuid4()),'expected_version':a['version']})
    assert accept(client,a).status_code==200
    trusted=trust(client,a)
    with Session(engine()) as db:
        row=db.get(Evidence,image.json()['id']);path=settings().storage_dir/row.storage_key;path.write_bytes(b'damaged image bytes')
    view=client.get(url(a));assert view.status_code==200,view.text
    assert view.json()['source']['scope_error'] and view.json()['permissions']['revoke']['allowed']
    assert client.post(url(a)+'/revoke',json=case_body(view.json())).status_code==200


@pytest.mark.parametrize('accepted',[False,True])
def test_viewer_cannot_write_or_infer_manage_permission(client,accepted):
    _,_,_,b,view,_,_=pair(client)
    if accepted:
        response,_=confirm(client,b,view);assert response.status_code==200,response.text
        assert accept(client,b).status_code==200
        view=client.get(url(b)).json()
    saved_confirmations=copy.deepcopy(view['history']['confirmations'])
    with Session(engine()) as db:
        member=db.scalar(select(Membership));member.role='viewer';db.commit()
    read=client.get(url(b));assert read.status_code==200,read.text
    current=read.json()
    assert not current['can_manage'] and not current['permissions']['confirm']['allowed']
    assert '只读权限' in current['permissions']['confirm']['reason']
    assert 'Role does not permit' not in current['permissions']['confirm']['reason']
    assert all('只读权限' in item['confirm_reason'] for item in current['use']['items'])
    assert current['history']['confirmations']==saved_confirmations
    if accepted:
        assert current['use']['historical'] and current['use']['all_confirmed']
        assert current['use']['blocked_reason'] is None
        assert current['use']['items'][0]['confirmation']['conclusion']=='confirmed'
    assert confirm(client,b,view)[0].status_code==403


def test_source_current_archive_change_keeps_original_acceptance_reusable(client):
    _,obj,a,b,view,_,_=pair(client)
    with Session(engine()) as db:
        source=db.get(Entity,obj['id']);field=next(iter(source.data));source.data={**source.data,field:'A今日档案更新，不修改昨日验收'};source.version+=1;db.commit()
    refreshed=client.get(url(b));assert refreshed.status_code==200,refreshed.text
    assert not refreshed.json()['use']['blocked_reason']
    assert confirm(client,b,refreshed.json())[0].status_code==200


def test_disengaging_extra_checks_never_relaxes_original_field_standard(client):
    release,_,a,b,view,_,_=pair(client)
    with Session(engine()) as db: spec=copy.deepcopy(db.get(Task,b['id']).data['goal_spec'])
    field=spec['required_fields'][0]
    spec['criteria'].append({'id':'value','kind':'field_value','field':field,'operator':'eq','expected':'永远不等于当前档案','description':'仍须满足原业务值标准'})
    spec['subgoals'][0]['criterion_ids'].append('value')
    _,c=another(client,release,spec)
    v=client.get(url(c)).json();case=v['available_cases'][0]
    response=client.post(url(c)+'/adopt',json=command(v,case_id=case['id'],expected_case_version=case['version'],content_hash=case['content_hash'],basis_hash=v['current_basis_hash']));assert response.status_code==200,response.text
    v=response.json();response=client.post(url(c)+'/disengage',json=command(v,use_hash=v['use']['use_hash'],reason='明确解除额外方法，但不解除原目标'))
    assert response.status_code==200,response.text
    assert accept(client,c).status_code==409


def test_upstream_withdrawal_propagates_to_derived_case_but_preserves_accepted_history(client):
    release,_,a,b,v,_,_=pair(client)
    saved,_=confirm(client,b,v);assert saved.status_code==200,saved.text
    assert accept(client,b).status_code==200
    derived=trust(client,b)
    with Session(engine()) as db: spec=copy.deepcopy(db.get(Task,b['id']).data['goal_spec'])
    _,c=another(client,release,spec)
    before=client.get(url(c)).json();candidate=next(row for row in before['available_cases'] if row['id']==derived['draft']['id'])
    assert candidate['can_adopt']
    revoke=client.post(url(a)+'/revoke',json=case_body(client.get(url(a)).json()));assert revoke.status_code==200,revoke.text
    historical=client.get(url(b));assert historical.status_code==200,historical.text
    assert historical.json()['use']['all_confirmed'] and historical.json()['source']['future_reuse_error']
    current=client.get(url(c)).json();candidate=next(row for row in current['available_cases'] if row['id']==derived['draft']['id'])
    assert not candidate['can_adopt'] and '上游' in candidate['adopt_reason']
    response=client.post(url(c)+'/adopt',json=command(current,case_id=candidate['id'],expected_case_version=candidate['version'],
        content_hash=candidate['content_hash'],basis_hash=current['current_basis_hash']))
    assert response.status_code==409 and '上游' in response.json()['detail']


def test_formal_lineage_ignores_disengaged_attempts_and_memoizes_actual_ancestor(client,monkeypatch):
    _,_,a,b,v,_,_=pair(client)
    for _ in range(4):
        response=client.post(url(b)+'/disengage',json=command(v,use_hash=v['use']['use_hash'],reason='明确解除本次方法尝试，保留记录'))
        assert response.status_code==200,response.text;v=response.json();case=v['available_cases'][0]
        response=client.post(url(b)+'/adopt',json=command(v,case_id=case['id'],expected_case_version=case['version'],
            content_hash=case['content_hash'],basis_hash=v['current_basis_hash']))
        assert response.status_code==200,response.text;v=response.json()
    response,_=confirm(client,b,v);assert response.status_code==200,response.text
    assert accept(client,b).status_code==200
    from generic_agents import native_cases
    from generic_agents.security import Principal
    original=native_cases._origin;calls=[]
    def tracked(db,p,task,lock=False):
        calls.append(task.id);return original(db,p,task,lock)
    monkeypatch.setattr(native_cases,'_origin',tracked)
    with Session(engine()) as db:
        member=db.scalar(select(Membership));p=Principal(member.user_id,member.workspace_id,member.role,'test')
        task=db.get(Task,b['id']);work={'rows':{},'done':set(),'edges':0}
        native_cases._future_lineage(db,p,task,work=work)
        native_cases._future_lineage(db,p,task,work=work)
        assert work['edges']==1 and len(work['edge_keys'])==1 and calls.count(a['id'])==1
        assert len(task.data['native_experience']['uses'])==5
        # 重复读取不占新的边预算，但同一边的当前版本/撤回仍每次核对。
        row=next(iter(work['rows'].values()));row.status='revoked';row.version+=1;native_cases._seal_case(row)
        from fastapi import HTTPException
        with pytest.raises(HTTPException,match='上游程序经验已撤回'):
            native_cases._future_lineage(db,p,task,work=work)


def test_future_lineage_unique_edge_budget_rejects_the_first_edge_beyond_hundred(client):
    """内核预算边界夹具，不声称构造了101次真实正式验收。"""
    from generic_agents import native_cases
    from generic_agents.security import Principal
    from fastapi import HTTPException
    _,_,_,b,v,_,_=pair(client)
    response,_=confirm(client,b,v);assert response.status_code==200,response.text
    assert accept(client,b).status_code==200
    with Session(engine()) as db:
        member=db.scalar(select(Membership));p=Principal(member.user_id,member.workspace_id,member.role,'test')
        task=db.get(Task,b['id'])
        work={'rows':{},'done':set(),'edges':100,'edge_keys':{('budget-fixture-'+str(i),'use','hash') for i in range(100)}}
        with pytest.raises(HTTPException,match='100项核查预算'):
            native_cases._future_lineage(db,p,task,work=work)
        assert work['edges']==101 and not work['rows']


@pytest.mark.parametrize('operation',['case_revoke','approval_role'])
def test_final_acceptance_holds_real_case_and_approval_locks_until_commit(client,operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic,sleep
    from sqlalchemy import text
    from generic_agents import native_cases,native_goals
    from generic_agents.security import Principal
    from generic_agents.workspace_api import saved_execution
    _,_,a,b,v,_,_=pair(client)
    response,_=confirm(client,b,v);assert response.status_code==200,response.text
    ready(client,b)
    original=client.get(url(a)).json()
    started=Event();pid={}
    def revoke():
        with Session(engine()) as other:
            pid['id']=other.scalar(text('select pg_backend_pid()'));started.set()
            if operation=='approval_role':
                member=other.scalar(select(Membership));member.role='viewer';other.flush()
            else:
                member=other.scalar(select(Membership));p=Principal(member.user_id,member.workspace_id,member.role,'test')
                source,_,_=saved_execution(other,p,a['id'],True)
                native_cases.execute(other,p,source,native_cases.CaseCommand.model_validate(case_body(original)),'revoke')
            other.commit()
    with Session(engine()) as db,ThreadPoolExecutor(max_workers=1) as pool:
        member=db.scalar(select(Membership));p=Principal(member.user_id,member.workspace_id,member.role,'test')
        task,_,_=saved_execution(db,p,b['id'],True)
        evidence,result=native_goals.assessment(db,p,task)
        body=native_goals.GoalAcceptance(request_id=str(uuid4()),expected_version=task.version,
            basis_hash=result['basis_hash'],reason='真实事务保持当次批准与方法锁，提交后才允许撤回')
        native_goals.accept(db,p,task,body)
        accept_pid=db.scalar(text('select pg_backend_pid()'))
        future=pool.submit(revoke);assert started.wait(3)
        deadline=monotonic()+3;blockers=[]
        while monotonic()<deadline:
            blockers=db.scalar(text('select pg_blocking_pids(:pid)'),{'pid':pid['id']})
            if accept_pid in blockers:break
            sleep(.02)
        try: assert accept_pid in blockers and not future.done()
        finally: db.commit()
        future.result(timeout=3)
    with Session(engine()) as db:
        task=db.get(Task,b['id']);assert task.data['native_goal_acceptance']['experience_proof']['all_confirmed']
        assert task.data['native_goal_acceptance_hash']==digest(task.data['native_goal_acceptance'])


@pytest.mark.parametrize('operation',['ancestor_case_revoke','ancestor_approval_role'])
def test_same_session_read_then_accept_relocks_real_ancestor_until_commit(client,account,operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic,sleep
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from generic_agents.api import app
    from generic_agents import native_cases,native_goals
    from generic_agents.security import Principal,password_hash
    from generic_agents.workspace_api import saved_execution
    # 编译原流程时只有真实执行管理员，避免无序members列表随机指派后建审核人。
    release,_,a,base,definition=setup(client)
    define(client,base,definition);response=accept(client,a);assert response.status_code==200,response.text
    username,password='ancestor_reviewer_'+uuid4().hex,uuid4().hex
    with Session(engine()) as db:
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        ancestor_user_id=user.id
        member=Membership(user_id=ancestor_user_id,workspace_id=account[2],role='admin');db.add(member);db.commit()
        ancestor_member_id=member.id
    with TestClient(app) as reviewer:
        login=reviewer.post('/api/login',json={'username':username,'password':password});assert login.status_code==200
        reviewer.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        trust(client,a,reviewer=reviewer)
    _,b=another(client,release,definition['goal_spec'])
    v=client.get(url(b)).json();case=v['available_cases'][0]
    response=client.post(url(b)+'/adopt',json=command(v,case_id=case['id'],expected_case_version=case['version'],
        content_hash=case['content_hash'],basis_hash=v['current_basis_hash']))
    assert response.status_code==200,response.text;v=response.json()
    response,_=confirm(client,b,v);assert response.status_code==200,response.text
    assert accept(client,b).status_code==200
    derived=trust(client,b)
    with Session(engine()) as db: spec=copy.deepcopy(db.get(Task,b['id']).data['goal_spec'])
    _,c=another(client,release,spec)
    view=client.get(url(c)).json();case=next(row for row in view['available_cases'] if row['id']==derived['draft']['id'])
    response=client.post(url(c)+'/adopt',json=command(view,case_id=case['id'],expected_case_version=case['version'],
        content_hash=case['content_hash'],basis_hash=view['current_basis_hash']));assert response.status_code==200,response.text
    response,_=confirm(client,c,response.json());assert response.status_code==200,response.text
    ready(client,c);original=client.get(url(a)).json()
    started=Event();pid={}
    def revoke():
        with Session(engine()) as other:
            pid['id']=other.scalar(text('select pg_backend_pid()'));started.set()
            if operation=='ancestor_approval_role':
                member=other.get(Membership,ancestor_member_id);member.role='viewer';other.flush()
            else:
                member=other.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.user_id!=ancestor_user_id))
                p=Principal(member.user_id,member.workspace_id,member.role,'test')
                source,_,_=saved_execution(other,p,a['id'],True)
                native_cases.execute(other,p,source,native_cases.CaseCommand.model_validate(case_body(original)),'revoke')
            other.commit()
    with Session(engine()) as db,ThreadPoolExecutor(max_workers=1) as pool:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.user_id!=ancestor_user_id))
        p=Principal(member.user_id,member.workspace_id,member.role,'test')
        task,_,_=saved_execution(db,p,c['id'],True)
        _,result=native_goals.assessment(db,p,task)
        readonly_work=db.info['native_experience_future_work']
        assert original['draft']['id'] in readonly_work['rows']
        body=native_goals.GoalAcceptance(request_id=str(uuid4()),expected_version=task.version,
            basis_hash=result['basis_hash'],reason='只读核查后正式验收必须重新锁定真实祖先与审核权限')
        native_goals.accept(db,p,task,body)
        assert db.info['native_experience_future_work'] is not readonly_work
        accept_pid=db.scalar(text('select pg_backend_pid()'))
        future=pool.submit(revoke);assert started.wait(3)
        deadline=monotonic()+3;blockers=[]
        while monotonic()<deadline:
            blockers=db.scalar(text('select pg_blocking_pids(:pid)'),{'pid':pid['id']})
            if accept_pid in blockers:break
            sleep(.02)
        try: assert accept_pid in blockers and not future.done()
        finally: db.commit()
        future.result(timeout=3)
    with Session(engine()) as db:
        task=db.get(Task,c['id'])
        assert task.data['native_goal_acceptance']['experience_proof']['all_confirmed']
        assert task.data['native_goal_acceptance_hash']==digest(task.data['native_goal_acceptance'])


def test_future_case_capacity_does_not_hide_frozen_accepted_use_or_feedback(client):
    _,_,a,b,v,_,_=pair(client)
    response,_=confirm(client,b,v);assert response.status_code==200
    assert accept(client,b).status_code==200
    with Session(engine()) as db:
        source=db.get(Memory,v['use']['case_id'])
        for _ in range(101):
            db.add(Memory(workspace_id=source.workspace_id,task_id=source.task_id,status='trusted',text='容量隔离夹具，不是已核经验',
                data={'type':'native_procedure_case','scope':copy.deepcopy(source.data['scope'])}))
        db.commit()
    historical=client.get(url(b));assert historical.status_code==200,historical.text
    assert historical.json()['use']['all_confirmed'] and historical.json()['available_cases']==[]
    response=client.post(url(b)+'/feedback',json=command(historical.json(),use_hash=historical.json()['use']['use_hash'],
        helpful='uncertain',note='只验证历史保存，不宣称业务收益'))
    assert response.status_code==200,response.text
