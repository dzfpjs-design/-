"""本地确定性方法链；不代表模型收益，不调用云或现场设备。"""
import copy
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import Memory,Task,Job,CloudCall,DomainInstance,Membership,Workspace
from generic_agents import native_method_suggestions as methods
from tests import test_native_method_suggestions as old
from tests.test_native_method_generation_v2 import structured
from tests.test_native_goals import setup,define
from tests.test_native_cases import accept,case_body,another,command,confirm


def seed(client):
    release,obj,task,base,body=setup(client)
    define(client,base,body);assert accept(client,task).status_code==200
    return release,obj,task


def preview(client,task):
    response=client.post(old.url(task)+'/local/preview',json={})
    assert response.status_code==200,response.text
    return response.json()


def start(client,task):
    shown=preview(client,task)
    body=old.common(shown['version'],preview_hash=shown['preview_hash'])
    response=client.post(old.url(task)+'/local/start',json=body)
    assert response.status_code==200,response.text
    return response.json()['suggestion'],body,shown


def imported(client,task):
    row,_,_=start(client,task);row,_=old.save(client,task,row)
    view=client.get('/api/workspace/tasks/'+task['id']+'/experience').json()
    body=old.action(row,saved_edit_hash=row['saved_edit']['hash'],acceptance_hash=view['source']['acceptance_hash'])
    response=client.post(old.url(task)+'/'+row['id']+'/import',json=body);assert response.status_code==200,response.text
    return response.json()['suggestion'],response.json()['experience']


def trusted(client,task):
    row,view=imported(client,task)
    root='/api/workspace/tasks/'+task['id']+'/experience'
    response=client.post(root+'/submit',json=case_body(view));assert response.status_code==200,response.text
    view=response.json();response=client.post(root+'/review',json={**case_body(view),'decision':'trust','valid_until':None})
    assert response.status_code==200,response.text
    return row,response.json()


def test_local_exact_order_set_preview_create_and_refresh_without_model_job_or_call(client,monkeypatch):
    task,_,_,_=structured(client,time_required=True)
    monkeypatch.setattr(methods,'cloud_json',lambda *a,**k:pytest.fail('本地清单不能调用云'))
    shown=preview(client,task)
    assert shown['source_kind']=='local_rules' and shown['capacity']=={'required_count':5,'max_steps':5}
    assert [step['kind'] for step in shown['steps']]==['field_verification','source_time','field_verification','units','source_time']
    assert shown['local_quality']['status']=='passed' and shown['local_quality']['covered_count']==5
    contract=shown['local_rules_contract'];assert contract['version']=='native-method-local-rules-v1'
    assert digest({k:v for k,v in contract.items() if k!='rules_contract_hash'})==contract['rules_contract_hash']
    with Session(engine()) as db:assert not list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==methods.TYPE)))
    row,body,_=start(client,task)
    assert row['status']=='needs_review' and row['steps']==shown['steps'] and row['local_quality']==shown['local_quality']
    for key in ['cloud_input','generation_contract','dispatch_messages_hash','dispatch_options','raw_quality','call','provider','model','raw_model_content_hash']:
        assert row[key] is None
    assert not row['has_saved_response'] and not row['automatic']
    assert not row['permissions']['retry']['allowed'] and not row['permissions']['local_recover']['allowed']
    assert old.get(client,task,row)['steps']==row['steps']
    assert client.post(old.url(task)+'/local/start',json=body).json()['suggestion']['id']==row['id']
    with Session(engine()) as db:
        stored=db.get(Memory,row['id']);assert 'job_id' not in stored.data and 'cloud_input' not in stored.data['request']
        assert not list(db.scalars(select(Job).where(Job.kind==methods.JOB))) and not list(db.scalars(select(CloudCall)))


def test_local_non_numeric_and_unknown_time_do_not_invent_units_or_time(client):
    _,_,task=seed(client);view=preview(client,task)
    assert view['local_input']['fields'][0]['flags']['declared_time']=='unknown'
    assert view['capacity']['required_count']==1 and [step['kind'] for step in view['steps']]==['field_verification']
    with Session(engine()) as db:
        from generic_agents.models import Capability
        assert db.scalar(select(func.count()).select_from(Capability).where(Capability.kind=='foundation_model'))==0


def test_local_capacity_rejects_six_before_persistence_and_goal_is_not_changed(client):
    task,_,_,_=structured(client,kinds=('文本',)*3,time_required=True)
    acceptance=client.get('/api/workspace/tasks/'+task['id']+'/goal').json()['acceptance']
    response=client.post(old.url(task)+'/local/preview',json={});assert response.status_code==409
    assert '超过建议容量' in response.json()['detail']
    assert client.get('/api/workspace/tasks/'+task['id']+'/goal').json()['acceptance']==acceptance
    with Session(engine()) as db:
        assert not list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==methods.TYPE)))
        assert not list(db.scalars(select(Job).where(Job.kind==methods.JOB))) and not list(db.scalars(select(CloudCall)))


def test_local_saved_candidate_missing_edit_rejects_complete_save_import_and_case_lineage(client):
    task,_,_,_=structured(client);row,_,_=start(client,task)
    body=old.action(row,title='人工保留当前核查',summary='核对当前资料，不照搬旧值',limits='缺单位时仍未知',
        steps=[{k:step[k] for k in ['id','kind','field','instruction']} for step in row['steps']])
    incomplete={**body,'steps':body['steps'][:1]}
    assert client.post(old.url(task)+'/'+row['id']+'/save',json=incomplete).status_code==409
    assert old.get(client,task,row)['version']==row['version']
    response=client.post(old.url(task)+'/'+row['id']+'/save',json=body);assert response.status_code==200,response.text
    saved=response.json()['suggestion'];assert saved['saved_edit']['quality']['status']=='passed'
    assert saved['local_quality']==row['local_quality'] and saved['steps']==row['steps']
    experience=client.get('/api/workspace/tasks/'+task['id']+'/experience').json()
    command=old.action(saved,saved_edit_hash=saved['saved_edit']['hash'],acceptance_hash=experience['source']['acceptance_hash'])
    response=client.post(old.url(task)+'/'+row['id']+'/import',json=command);assert response.status_code==200,response.text
    result=response.json();assert result['experience']['draft']['status']=='draft' and result['suggestion']['status']=='imported'
    with Session(engine()) as db:
        candidate=db.get(Memory,row['id']);case=db.get(Memory,result['experience']['draft']['id'])
        assert case.data['suggestion_origin']=={'source_kind':'local_rules','suggestion_id':row['id'],
            'request_hash':candidate.data['request_hash'],'local_result_hash':candidate.data['local_result']['hash'],
            'saved_edit_hash':saved['saved_edit']['hash']}
        assert not list(db.scalars(select(Job).where(Job.kind==methods.JOB))) and not list(db.scalars(select(CloudCall)))
    assert client.post(old.url(task)+'/'+row['id']+'/import',json=command).json()['experience']['draft']['id']==result['experience']['draft']['id']


def test_local_start_cas_nonce_exact_recovery_and_abandon(client):
    _,_,task=seed(client);row,body,shown=start(client,task)
    recovery=old.recovery_body(row['task_version'],operation='local_start',original_request=body,action='probe')
    response=client.post(old.url(task)+'/requests/recover',json=recovery)
    assert response.status_code==200 and response.json()['saved'] and response.json()['suggestion']['id']==row['id']
    assert client.post(old.url(task)+'/local/start',json={**body,'reason':'篡改原请求'}).status_code==409
    assert client.post(old.url(task)+'/local/start',json={**body,'request_id':str(uuid4())}).status_code==409
    unknown={**body,'request_id':str(uuid4()),'expected_version':row['task_version']}
    recovery.update(original_request=unknown,action='probe');assert not client.post(old.url(task)+'/requests/recover',json=recovery).json()['saved']
    recovery['action']='abandon';assert client.post(old.url(task)+'/requests/recover',json=recovery).json()['abandoned']
    assert client.post(old.url(task)+'/local/start',json=unknown).status_code==409
    recovery['suggestion_id']=row['id'];assert client.post(old.url(task)+'/requests/recover',json=recovery).status_code==422


@pytest.mark.parametrize('corruption',['result_order','result_choice','proposal','contract','raw_response','cloud_call_id','job_id','provider','model','dispatch_options','usage','source_null','source_empty','source_unknown'])
def test_local_saved_contract_result_and_forbidden_cloud_proof_fail_closed(client,corruption):
    task,_,_,_=structured(client);row,_,_=start(client,task)
    with Session(engine()) as db:
        saved=db.get(Memory,row['id']);data=copy.deepcopy(saved.data)
        if corruption in {'result_order','result_choice'}:
            result=data['local_result'];result.pop('hash')
            if corruption=='result_order':result['value']['steps'].reverse()
            else:result['value']['steps'][0]={'alias':'F1','template':'source_identity','pattern_id':'identity_current_v1'}
            data['local_result']=methods.signed(result)
        elif corruption=='proposal':data['proposal']['steps'][0]['instruction']='损坏的原清单'
        elif corruption=='contract':
            request=data['request'];request['local_rules_contract']['rules'][0]='未登记的自洽规则'
            c=request['local_rules_contract'];c['rules_contract_hash']=digest({k:v for k,v in c.items() if k!='rules_contract_hash'})
            data['request_hash']=digest(request)
        elif corruption.startswith('source_'):
            data['request']['source_kind']={'source_null':None,'source_empty':'','source_unknown':'other'}[corruption]
            data['request_hash']=digest(data['request'])
        else:data[corruption]=None
        saved.data=data;methods.seal(saved);db.commit()
    assert client.get(old.url(task)+'/'+row['id']).status_code==409


def test_local_source_pause_blocks_new_save_but_static_history_and_cancel_survive(client):
    release,_,task=seed(client);row,_,_=start(client,task)
    with Session(engine()) as db:
        instance=db.get(DomainInstance,release['instance_id']);instance.status='paused';db.commit()
    response=client.post(old.url(task)+'/local/preview',json={});assert response.status_code==409
    current=old.get(client,task,row);assert current['source_error'] and not current['permissions']['save']['allowed']
    assert current['local_quality']==row['local_quality']
    body=old.action(current,title='不得绕过暂停',summary='只保留历史',limits='不执行',steps=[{k:s[k] for k in ['id','kind','field','instruction']} for s in row['steps']])
    assert client.post(old.url(task)+'/'+row['id']+'/save',json=body).status_code==409
    response=client.post(old.url(task)+'/'+row['id']+'/cancel',json=old.action(current));assert response.status_code==200
    assert response.json()['suggestion']['local_quality']==row['local_quality']


def test_local_preview_is_strict_empty_json_and_cloud_authorization_cannot_be_transplanted(client):
    _,_,task=seed(client)
    assert client.post(old.url(task)+'/local/preview',json={'model_profile_id':'unused'}).status_code==422
    shown=preview(client,task)
    body=old.common(shown['version'],preview_hash=shown['preview_hash'],cloud_authorized=True)
    assert client.post(old.url(task)+'/local/start',json=body).status_code==422


def test_local_unit_input_self_consistent_rehash_cannot_change_unknown_to_known(client):
    task,_,_,_=structured(client);row,_,_=start(client,task)
    with Session(engine()) as db:
        saved=db.get(Memory,row['id']);data=copy.deepcopy(saved.data);request=data['request']
        item=request['local_input']['fields'][1];item['flags']['units']='known';item['priority_tags']=['goal_required']
        for pattern in request['patterns']:
            if pattern['alias']==item['alias']:pattern['basis_flags']=copy.deepcopy(item['flags'])
        request['capacity']['required_count']=2;request['preview_hash']=digest({k:v for k,v in request.items() if k!='preview_hash'})
        data['request_hash']=digest(request);choices=methods.deterministic_choices(request['local_input'])
        data['local_result']=methods.signed({'value':choices,'quality':methods.quality(choices,request)})
        data['proposal']=methods.signed(methods.normalize(choices,request));saved.data=data;methods.seal(saved);db.commit()
    response=client.get(old.url(task)+'/'+row['id']);assert response.status_code==409
    assert '冻结输入' in response.json()['detail']


def test_local_viewer_history_is_static_but_new_operations_and_cross_scope_are_denied(client):
    _,_,task=seed(client);row,body,_=start(client,task)
    with Session(engine()) as db:
        member=db.scalar(select(Membership));member.role='viewer';db.commit()
    current=old.get(client,task,row);assert current['local_quality']==row['local_quality'] and not current['can_manage']
    listing=client.get(old.url(task)).json();assert not listing['permissions']['local_start']['allowed']
    assert client.post(old.url(task)+'/local/preview',json={}).status_code==403
    assert client.post(old.url(task)+'/local/start',json=body).status_code==403
    with Session(engine()) as db:
        member=db.scalar(select(Membership));member.role='admin'
        workspace=Workspace(name='本地核查隔离工作区');db.add(workspace);db.flush()
        db.add(Membership(user_id=member.user_id,workspace_id=workspace.id,role='admin'));foreign=workspace.id;db.commit()
    client.headers['X-Workspace-ID']=foreign
    assert client.get(old.url(task)+'/'+row['id']).status_code==404
    assert client.post(old.url(task)+'/local/start',json=body).status_code==404


def test_local_approved_method_is_consumed_against_independent_current_refs_not_old_values(client):
    release,_,a=seed(client);row,view=trusted(client,a)
    with Session(engine()) as db:
        source_task=db.get(Task,a['id']);spec=copy.deepcopy(source_task.data['goal_spec']);source_entities=set(source_task.data['entity_ids'])
    _,b=another(client,release,spec);root='/api/workspace/tasks/'+b['id']+'/experience'
    current=client.get(root).json();case=current['available_cases'][0]
    response=client.post(root+'/adopt',json=command(current,case_id=case['id'],expected_case_version=case['version'],
        content_hash=case['content_hash'],basis_hash=current['current_basis_hash']));assert response.status_code==200,response.text
    use=response.json();assert not use['use']['all_confirmed']
    assert accept(client,b).status_code==409
    use=client.get(root).json();item=use['use']['items'][0]
    assert item['current_refs'] and all(ref['entity_id'] not in source_entities for ref in item['current_refs'])
    response,_=confirm(client,b,use,item=item['id']);assert response.status_code==200,response.text
    response=accept(client,b);assert response.status_code==200,response.text
    assert response.json()['acceptance']['experience_proof']['items'][0]['kind']=='field_verification'
    assert client.get(old.url(a)+'/'+row['id']).status_code==200
    with Session(engine()) as db:assert not list(db.scalars(select(CloudCall)))


@pytest.mark.parametrize('phase',['draft','candidate','trusted'])
def test_bad_local_origin_blocks_case_read_and_future_actions_but_trusted_can_revoke(client,phase):
    release,_,a=seed(client);row,view=imported(client,a);root='/api/workspace/tasks/'+a['id']+'/experience'
    if phase in {'candidate','trusted'}:
        response=client.post(root+'/submit',json=case_body(view));assert response.status_code==200,response.text;view=response.json()
    if phase=='trusted':
        response=client.post(root+'/review',json={**case_body(view),'decision':'trust','valid_until':None});assert response.status_code==200,response.text;view=response.json()
    with Session(engine()) as db:
        candidate=db.get(Memory,row['id']);data=copy.deepcopy(candidate.data);data['local_result']['quality']['covered_count']=0
        candidate.data=data;methods.seal(candidate);spec=copy.deepcopy(db.get(Task,a['id']).data['goal_spec']);db.commit()
    assert client.get(root).status_code==409
    action='submit' if phase=='draft' else 'review' if phase=='candidate' else None
    if action:
        body=case_body(view)
        if action=='review':body.update(decision='trust',valid_until=None)
        assert client.post(root+'/'+action,json=body).status_code==409
    else:
        _,b=another(client,release,spec)
        other=client.get('/api/workspace/tasks/'+b['id']+'/experience').json()
        assert other['available_cases']==[]
        refusal=client.post('/api/workspace/tasks/'+b['id']+'/experience/adopt',json=command(other,
            case_id=view['draft']['id'],expected_case_version=view['draft']['version'],content_hash=view['draft']['content_hash'],basis_hash=other['current_basis_hash']))
        assert refusal.status_code==409
        response=client.post(root+'/revoke',json=case_body(view));assert response.status_code==200,response.text
        revoked=response.json()['draft'];assert revoked['status']=='revoked' and revoked['source_origin_error'] and not revoked['can_adopt']
        assert client.get(root).status_code==200
        with Session(engine()) as db:
            assert db.get(Memory,revoked['id']).status=='revoked'


def test_local_import_origin_survives_manual_archive_and_explicit_restore(client):
    from tests import test_native_draft_revisions as revisions
    _,_,task=seed(client);row,view=imported(client,task)
    original=copy.deepcopy(view['draft']);manual=revisions.saved(client,task,view,'改成新的人工稿')
    with Session(engine()) as db:assert 'suggestion_origin' not in db.get(Memory,manual['draft']['id']).data
    old_version=revisions.detail(client,task,original['version'])['item']
    response=client.post('/api/workspace/tasks/'+task['id']+'/experience/restore',json=revisions.restore_body(manual,old_version))
    assert response.status_code==200,response.text
    assert response.json()['draft']['title']==original['title']
    with Session(engine()) as db:assert db.get(Memory,original['id']).data['suggestion_origin']['local_result_hash']==db.get(Memory,row['id']).data['local_result']['hash']


@pytest.mark.parametrize('corruption',['missing_kind','unknown_kind','result_hash'])
def test_local_case_origin_marker_cannot_fall_back_to_legacy_cloud(client,corruption):
    from generic_agents import native_cases as cases
    _,_,task=seed(client);_,view=imported(client,task)
    with Session(engine()) as db:
        case=db.get(Memory,view['draft']['id']);data=copy.deepcopy(case.data);origin=data['suggestion_origin']
        if corruption=='missing_kind':origin.pop('source_kind')
        elif corruption=='unknown_kind':origin['source_kind']='unregistered'
        else:origin['local_result_hash']='0'*64
        case.data=data;cases._seal_case(case);db.commit()
    assert client.get('/api/workspace/tasks/'+task['id']+'/experience').status_code==409
    assert client.post('/api/workspace/tasks/'+task['id']+'/experience/submit',json=case_body(view)).status_code==409


def test_local_current_source_history_corruption_is_not_hidden_by_historical_permission_mode(client):
    from generic_agents.models import Observation
    task,_,_,_=structured(client,time_required=True);row,_,_=start(client,task)
    with Session(engine()) as db:
        observation=db.scalar(select(Observation));assert observation.data['verification_history']
        data=copy.deepcopy(observation.data);data['verification_history']=[];observation.data=data;db.commit()
    response=client.get(old.url(task)+'/'+row['id']);assert response.status_code==409
    assert client.post(old.url(task)+'/local/preview',json={}).status_code==409


def test_local_original_acceptor_retraction_keeps_history_but_blocks_other_admin_future_use(client,account):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents.models import User
    from generic_agents.security import password_hash
    _,_,task=seed(client);row,_,_=start(client,task);row,_=old.save(client,task,row)
    username,password='local_admin_'+uuid4().hex,uuid4().hex
    with Session(engine()) as db:
        member=db.scalar(select(Membership));member.role='viewer'
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=account[2],role='admin'));db.commit()
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':password});assert login.status_code==200
        other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        response=other.get(old.url(task)+'/'+row['id']);assert response.status_code==200,response.text
        current=response.json();assert current['local_quality']==row['local_quality'] and current['source_error']
        assert not current['permissions']['save']['allowed'] and not current['permissions']['import']['allowed']
        assert other.post(old.url(task)+'/local/preview',json={}).status_code==403
        body=old.action(current,title='当前管理者不可借旧授权',summary='只读保留',limits='不执行',
            steps=[{k:s[k] for k in ['id','kind','field','instruction']} for s in row['steps']])
        assert other.post(old.url(task)+'/'+row['id']+'/save',json=body).status_code==403


def test_local_imported_case_static_history_remains_after_instance_pause(client):
    release,_,task=seed(client);row,view=imported(client,task)
    with Session(engine()) as db:
        db.get(DomainInstance,release['instance_id']).status='paused';db.commit()
    response=client.get('/api/workspace/tasks/'+task['id']+'/experience');assert response.status_code==200,response.text
    assert response.json()['draft']['title']==view['draft']['title']
    assert old.get(client,task,row)['local_quality']==row['local_quality']
    assert client.post(old.url(task)+'/local/preview',json={}).status_code==409


def test_local_retry_cannot_turn_deterministic_candidate_into_cloud_job(client):
    _,_,task=seed(client);row,_,_=start(client,task)
    response=client.post(old.url(task)+'/'+row['id']+'/retry',json=old.action(row))
    assert response.status_code==409
    with Session(engine()) as db:
        assert not list(db.scalars(select(Job).where(Job.kind==methods.JOB))) and not list(db.scalars(select(CloudCall)))


@pytest.mark.parametrize('corruption',['missing_hash','boolean_version','extra_cloud_key'])
def test_malformed_local_import_proof_refuses_future_but_http_revoke_commits(client,corruption):
    _,_,task=seed(client);row,view=trusted(client,task)
    with Session(engine()) as db:
        candidate=db.get(Memory,row['id']);data=copy.deepcopy(candidate.data)
        value={k:v for k,v in data['local_import'].items() if k!='hash'}
        if corruption=='missing_hash':value.pop('case_record_hash')
        elif corruption=='boolean_version':value['case_version']=True
        else:value['cloud_call_id']='not-real'
        data['local_import']=methods.signed(value);candidate.data=data;methods.seal(candidate);db.commit()
    root='/api/workspace/tasks/'+task['id']+'/experience'
    assert client.get(root).status_code==409
    body=case_body(view);response=client.post(root+'/revoke',json=body);assert response.status_code==200,response.text
    revoked=response.json()['draft'];assert revoked['status']=='revoked' and revoked['source_origin_error'] and not revoked['can_adopt']
    assert client.get(root).status_code==200
    assert client.post(root+'/revoke',json=body).status_code==200
    with Session(engine()) as db:assert db.get(Memory,revoked['id']).status=='revoked'
