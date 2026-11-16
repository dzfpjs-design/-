"""隔离PG及实际网关HTTP路径；假供应商响应不代表模型质量或真实云调用。"""
import copy,json
from uuid import uuid4
from datetime import timedelta
import httpx
import pytest
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Memory,Capability,Job,CloudCall,Membership,now
from generic_agents.contracts import digest
from generic_agents.worker import process_once
from generic_agents import native_method_suggestions as methods
from tests.test_native_goals import setup,define
from tests.test_native_cases import accept,another,trust,case_body,url as experience_url
from tests.test_vertical_slice import publish,manifest


def url(task):return '/api/workspace/tasks/'+task['id']+'/experience/suggestions'
def cloud_profile(client,provider='deepseek'):
    return publish(client,manifest('foundation_model',name='private_model_name_not_for_cloud',allowed_agent_roles=['business'],
        execution={'provider':provider,'model':'deepseek-chat' if provider=='deepseek' else 'qwen-plus',
            'secret_ref':'DEEPSEEK_API_KEY' if provider=='deepseek' else 'DASHSCOPE_API_KEY','max_tokens':2048},
        data_policy={'cloud_allowed':True}))
def seed(client):
    release,obj,task,base,body=setup(client)
    body['goal_spec']['purpose']='PRIVATE_GOAL_SENTINEL';body['reason']='PRIVATE_NOTE_SENTINEL'
    define(client,base,body)
    response=accept(client,task);assert response.status_code==200,response.text
    with Session(engine()) as db:
        row=db.get(Task,task['id']);row.title='PRIVATE_TASK_NAME_SENTINEL';db.commit()
    return release,obj,task,body['goal_spec'],cloud_profile(client)
def preview(client,task,cap):
    response=client.post(url(task)+'/preview',json={'model_profile_id':cap['id']});assert response.status_code==200,response.text
    return response.json()
def common(version,**extra):return {'request_id':str(uuid4()),'expected_version':version,'reason':'本地核对结构并保存，不证明现场',**extra}
def recovery_body(version,**extra):return {'expected_version':version,'reason':'核对原请求编号与原目标，不另建云调用',**extra}
def start(client,task,cap):
    view=preview(client,task,cap)
    body=common(view['version'],model_profile_id=cap['id'],preview_hash=view['preview_hash'],cloud_authorized=True)
    response=client.post(url(task)+'/start',json=body);assert response.status_code==200,response.text
    return response.json()['suggestion'],body,view
def get(client,task,row):
    response=client.get(url(task)+'/'+row['id']);assert response.status_code==200,response.text
    return response.json()
def action(row,**extra):return common(row['task_version'],expected_suggestion_version=row['version'],record_hash=row['record_hash'],**extra)
def run(row):
    with Session(engine()) as db:job=db.get(Memory,row['id']).data['job_id']
    assert process_once(job,deliver=False)
def transport(monkeypatch,sent,mutate=None,hook=None,syntax=None):
    """明确模拟公开合同的完整响应；不是实际模型收益，原JSON不由服务端补项。"""
    from generic_agents.config import settings
    isolated_settings=settings()
    # Only this synthetic provider seam receives finite test capacity. Keep
    # actual reserve/settle and per-workspace daily accounting; monkeypatch
    # restores this cached instance without changing the runner's zero env.
    monkeypatch.setattr(isolated_settings,'cloud_daily_call_limit',25)
    monkeypatch.setattr(isolated_settings,'cloud_daily_token_limit',1000000)
    monkeypatch.setenv('DEEPSEEK_API_KEY','unit-test-nonsecret-placeholder')
    monkeypatch.setenv('DASHSCOPE_API_KEY','unit-test-nonsecret-placeholder')
    original=httpx.Client.post
    def post(self,target,**kwargs):
        if str(target) not in {'https://api.deepseek.com/chat/completions',
                'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'}:
            return original(self,target,**kwargs)
        payload=copy.deepcopy(kwargs['json']);sent.append(payload)
        context=json.loads(payload['messages'][-1]['content'])['input']
        result={'steps':[context['allowed_pairs'][0]]}
        if context['contract_version']=='native-method-projection-v2':
            required={(alias,'field_verification') for alias in context['goal']['required_aliases']}
            required.update((item['alias'],'units') for item in context['fields'] if item['type'] in {'integer','number'} and item['flags']['units']=='unknown')
            if context['goal']['time_constraint']=='required':required.update((alias,'source_time') for alias in context['goal']['required_aliases'])
            result={'steps':[pair for pair in context['allowed_pairs'] if (pair['alias'],pair['template']) in required]}
        if mutate:mutate(result)
        if hook:hook()
        return httpx.Response(200,json={'choices':[{'message':{'content':syntax if syntax is not None else json.dumps(result)}}],
            'usage':{'total_tokens':91,'prompt_tokens':62,'completion_tokens':29}},headers={'x-request-id':'fixture-response-'+str(len(sent))})
    monkeypatch.setattr(httpx.Client,'post',post)
def save(client,task,row,title='人工保存的核查标题'):
    body=action(row,title=title,summary=row['summary'],limits=row['limits'],
        steps=[{k:item[k] for k in ['id','kind','field','instruction']} for item in row['steps']])
    response=client.post(url(task)+'/'+row['id']+'/save',json=body);assert response.status_code==200,response.text
    return response.json()['suggestion'],body
def enable_policy(client,task,cap,limit=1):
    view=preview(client,task,cap);current=client.get(url(task)+'/policy').json()['policy']
    body=common(view['version'],enabled=True,expected_policy_version=current['version'],policy_record_hash=current['record_hash'],
        model_profile_id=cap['id'],projection_contract_hash=view['projection_contract_hash'],preview_hash=view['preview_hash'],
        generation_contract_hash=view['generation_contract']['generation_contract_hash'],
        cloud_authorized=True,daily_limit=limit,valid_days=1)
    response=client.post(url(task)+'/policy',json=body);assert response.status_code==200,response.text
    return response.json()['policy'],body


def test_exact_preview_actual_http_paid_result_saved_edit_import_and_uuid_recovery(client,monkeypatch):
    release,obj,task,spec,cap=seed(client);sent=[];transport(monkeypatch,sent)
    row,body,view=start(client,task,cap)
    repeated=client.post(url(task)+'/start',json=body);assert repeated.status_code==200 and repeated.json()['suggestion']['id']==row['id']
    run(row);row=get(client,task,row)
    assert row['status']=='needs_review' and row['has_saved_response'] and row['call']['usage']['total_tokens']==91
    assert row['call']['cost']['status']=='unknown' and row['call']['elapsed_ms'] is not None
    serialized=json.dumps(sent,ensure_ascii=False)
    for marker in ['PRIVATE_GOAL_SENTINEL','PRIVATE_NOTE_SENTINEL','PRIVATE_TASK_NAME_SENTINEL',cap['id'],task['id'],obj['id'],cap['name']]:assert marker not in serialized
    assert row['cloud_input']==view['cloud_input'] and len(sent)==1
    with Session(engine()) as db:
        source=db.get(Task,task['id']);assert source.data['native_goal_acceptance_hash']
        assert db.scalar(select(CloudCall)).status=='succeeded'
        assert db.get(Memory,row['id']).data['native_package_id']==source.package_id
        assert 'package_id' not in db.get(Memory,row['id']).data
    row,_=save(client,task,row)
    acceptance=client.get('/api/workspace/tasks/'+task['id']+'/goal').json()['acceptance']
    body=action(row,saved_edit_hash=row['saved_edit']['hash'],acceptance_hash=digest(acceptance))
    response=client.post(url(task)+'/'+row['id']+'/import',json=body);assert response.status_code==200,response.text
    imported=response.json();assert imported['suggestion']['status']=='imported' and imported['experience']['draft']['status']=='draft'
    again=client.post(url(task)+'/'+row['id']+'/import',json=body);assert again.status_code==200 and again.json()['experience']['draft']['id']==imported['experience']['draft']['id']
    recovery=recovery_body(imported['version'],operation='import',suggestion_id=row['id'],original_request=body,action='probe')
    recovered=client.post(url(task)+'/requests/recover',json=recovery);assert recovered.status_code==200 and recovered.json()['saved'] and recovered.json()['experience']
    with Session(engine()) as db:
        assert db.scalar(select(func_count_memory(TYPE=methods.TYPE)))==1
        assert db.get(Memory,imported['experience']['draft']['id']).data['suggestion_origin']['suggestion_id']==row['id']
        assert len(list(db.scalars(select(CloudCall))))==1


def func_count_memory(TYPE):
    from sqlalchemy import func
    return func.count(Memory.id).filter(Memory.data['type'].as_string()==TYPE)


@pytest.mark.parametrize('change',[lambda result:result['steps'][0].update(alias='F99'),lambda result:result['steps'][0].update(pattern_id='execute_device'),
    lambda result:result.update(free_text='PRIVATE_UNTRUSTED_SENTINEL'),lambda result:result['steps'].append(copy.deepcopy(result['steps'][0]))])
def test_illegal_json_semantics_keeps_paid_raw_and_never_imports(client,monkeypatch,change):
    _,_,task,_,cap=seed(client);sent=[];transport(monkeypatch,sent,mutate=change)
    row,_,_=start(client,task,cap);run(row);row=get(client,task,row)
    assert row['status']=='failed' and row['has_saved_response'] and row['raw_model_content_hash']
    assert not row['permissions']['import']['allowed'] and len(sent)==1
    response=client.post(url(task)+'/'+row['id']+'/retry',json=action(row))
    assert response.status_code==409 and len(sent)==1


def test_syntax_failure_has_actual_call_usage_unknown_response_never_auto_repeats(client,monkeypatch):
    _,_,task,_,cap=seed(client);sent=[];transport(monkeypatch,sent,syntax='{not valid JSON')
    row,_,_=start(client,task,cap);run(row);row=get(client,task,row)
    assert row['status']=='response_unknown' and not row['has_saved_response']
    assert row['call']['usage']['total_tokens']==91
    with Session(engine()) as db:
        memory=db.get(Memory,row['id']);job=db.get(Job,memory.data['job_id']);job.status='queued';job.available_at=now();db.commit()
    run(row);assert len(sent)==1
    response=client.post(url(task)+'/'+row['id']+'/retry',json=action(get(client,task,row)))
    assert response.status_code==409 and len(sent)==1


def test_saved_paid_response_recovers_locally_after_crash_without_another_cloud_call(client,monkeypatch):
    _,_,task,_,cap=seed(client);sent=[];transport(monkeypatch,sent)
    original=methods.normalize
    def crash(*args):raise RuntimeError('fixture crash after paid raw checkpoint')
    monkeypatch.setattr(methods,'normalize',crash)
    row,_,_=start(client,task,cap);run(row);row=get(client,task,row)
    assert row['status']=='failed' and row['has_saved_response']
    monkeypatch.setattr(methods,'normalize',original)
    response=client.post(url(task)+'/'+row['id']+'/retry',json=action(row));assert response.status_code==200,response.text
    assert response.json()['suggestion']['status']=='needs_review' and len(sent)==1


def test_cancel_during_actual_call_retains_paid_response_without_creating_draft(client,monkeypatch):
    _,_,task,_,cap=seed(client);sent=[];holder={}
    def cancel():
        current=get(client,task,holder['row'])
        response=client.post(url(task)+'/'+current['id']+'/cancel',json=action(current));assert response.status_code==200,response.text
    transport(monkeypatch,sent,hook=cancel)
    row,_,_=start(client,task,cap);holder['row']=row;run(row);row=get(client,task,row)
    assert row['status']=='cancelled' and row['has_saved_response'] and not row['permissions']['import']['allowed']
    assert client.get(experience_url(task)).json()['draft'] is None


def test_pending_abandon_target_and_exact_preview_model_change_never_queue(client):
    _,_,task,_,cap=seed(client);view=preview(client,task,cap)
    body=common(view['version'],model_profile_id=cap['id'],preview_hash=view['preview_hash'],cloud_authorized=True)
    response=client.post(url(task)+'/requests/recover',json=recovery_body(view['version'],operation='start',original_request=body,action='abandon'))
    assert response.status_code==200 and response.json()['abandoned']
    assert client.post(url(task)+'/start',json=body).status_code==409
    view=preview(client,task,cap)
    with Session(engine()) as db:
        model=db.get(Capability,cap['id']);data=copy.deepcopy(model.data);data['execution']['max_tokens']=4096;model.data=data;model.content_hash=digest(data);db.commit()
    response=client.post(url(task)+'/start',json=common(view['version'],model_profile_id=cap['id'],preview_hash=view['preview_hash'],cloud_authorized=True))
    assert response.status_code==409
    with Session(engine()) as db:assert not list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==methods.TYPE)))


def test_explicit_existing_draft_cas_and_submitted_draft_cannot_be_overwritten(client,monkeypatch):
    _,_,task,_,cap=seed(client);sent=[];transport(monkeypatch,sent)
    row,_,_=start(client,task,cap);run(row);row,_=save(client,task,get(client,task,row))
    source=client.get(experience_url(task)).json();field=source['field_options'][0]['key']
    response=client.post(experience_url(task)+'/draft',json=common(source['version'],acceptance_hash=source['source']['acceptance_hash'],
        title='原人工草稿',summary='人工原稿保留',limits='没有自动批准',steps=[{'id':'original','kind':'source_identity','field':field,'instruction':'人工原稿'}]))
    assert response.status_code==200,response.text;original=response.json();case=original['draft'];row=get(client,task,row)
    body=action(row,saved_edit_hash=row['saved_edit']['hash'],acceptance_hash=original['source']['acceptance_hash'])
    assert client.post(url(task)+'/'+row['id']+'/import',json=body).status_code==409
    body=action(row,saved_edit_hash=row['saved_edit']['hash'],acceptance_hash=original['source']['acceptance_hash'],
        case_id=case['id'],expected_case_version=case['version'],content_hash=case['content_hash'],replace_existing=True)
    assert client.post(experience_url(task)+'/submit',json=case_body(original)).status_code==200
    row=get(client,task,row);body['expected_version']=row['task_version']
    assert client.post(url(task)+'/'+row['id']+'/import',json=body).status_code==409
    assert client.get(experience_url(task)).json()['draft']['title']=='原人工草稿'


def test_automatic_scope_china_day_quota_survives_disable_enable_and_failure(client,monkeypatch):
    release,_,a,spec,cap=seed(client);sent=[];transport(monkeypatch,sent)
    policy,_=enable_policy(client,a,cap,1)
    _,b=another(client,release,spec);assert accept(client,b).status_code==200
    listing=client.get(url(b)).json();assert len(listing['items'])==1 and listing['items'][0]['automatic']
    first=listing['items'][0];run(first);assert get(client,b,first)['status']=='needs_review'
    policy_state=client.get(url(a)+'/policy').json()
    response=client.post(url(a)+'/policy',json=common(policy_state['version'],enabled=False,
        expected_policy_version=policy_state['policy']['version'],policy_record_hash=policy_state['policy']['record_hash']))
    assert response.status_code==200,response.text
    enable_policy(client,a,cap,1)
    _,c=another(client,release,spec);assert accept(client,c).status_code==200
    items=client.get(url(c)).json()['items'];assert len(items)==1 and items[0]['status']=='failed' and '限额' in items[0]['error']
    assert len(sent)==1
    with Session(engine()) as db:
        source=db.get(Task,c['id']);assert source.data['native_goal_acceptance']
        assert list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==methods.POLICY)))


def test_disable_before_dispatch_and_expiry_preserve_acceptance_without_cloud(client,monkeypatch):
    release,_,a,spec,cap=seed(client);sent=[];transport(monkeypatch,sent)
    policy,_=enable_policy(client,a,cap,5)
    _,b=another(client,release,spec);assert accept(client,b).status_code==200
    first=client.get(url(b)).json()['items'][0]
    current=client.get(url(a)+'/policy').json()
    response=client.post(url(a)+'/policy',json=common(current['version'],enabled=False,expected_policy_version=current['policy']['version'],policy_record_hash=current['policy']['record_hash']))
    assert response.status_code==200;run(first);assert get(client,b,first)['status']=='failed' and not sent
    policy,_=enable_policy(client,a,cap,5)
    clock=methods.now;monkeypatch.setattr(methods,'now',lambda:clock()+timedelta(days=2))
    _,c=another(client,release,spec);assert accept(client,c).status_code==200
    item=client.get(url(c)).json()['items'][0];assert item['status']=='failed' and '到期' in item['error'] and not sent


def test_real_sql_automatic_hook_fault_savepoint_preserves_original_acceptance(client,monkeypatch):
    release,_,a,spec,cap=seed(client);enable_policy(client,a,cap)
    def sql_failure(db,p,task):db.execute(text('select missing_native_suggestion_table()'))
    monkeypatch.setattr(methods,'schedule_automatic',sql_failure)
    _,b=another(client,release,spec);response=accept(client,b);assert response.status_code==200,response.text
    with Session(engine()) as db:
        task=db.get(Task,b['id']);assert task.data['native_goal_acceptance_hash']==digest(task.data['native_goal_acceptance'])
        assert task.data['native_suggestion_hook_error']
        assert db.get(Memory,task.data['native_goal_data_receipt_id'])
    listing=client.get(url(b));assert listing.status_code==200,listing.text
    assert listing.json()['items'][0]['status']=='failed'
    assert '原正式验收' in listing.json()['items'][0]['error']


def test_readonly_and_cross_task_target_recovery_do_not_create_or_modify(client,monkeypatch):
    release,_,a,spec,cap=seed(client);row,_,_=start(client,a,cap)
    _,b=another(client,release,spec);assert accept(client,b).status_code==200
    assert client.get(url(b)+'/'+row['id']).status_code==409
    response=client.post(url(b)+'/requests/recover',json=recovery_body(client.get(url(b)).json()['version'],operation='cancel',suggestion_id=row['id'],original_request=action(row),action='abandon'))
    assert response.status_code==409
    with Session(engine()) as db:member=db.scalar(select(Membership));member.role='viewer';db.commit()
    listing=client.get(url(a));assert listing.status_code==200 and not listing.json()['permissions']['start']['allowed']
    current=get(client,a,row);assert not current['can_manage']
    assert client.post(url(a)+'/'+row['id']+'/cancel',json=action(current)).status_code==403


def test_policy_requires_exact_preview_and_admin_future_authorization(client):
    _,_,a,_,cap=seed(client);view=preview(client,a,cap)
    body=common(view['version'],enabled=True,expected_policy_version=None,policy_record_hash=None,model_profile_id=cap['id'],
        projection_contract_hash=view['projection_contract_hash'],preview_hash=view['preview_hash'],cloud_authorized=True,daily_limit=1,valid_days=1)
    with Session(engine()) as db:
        model=db.get(Capability,cap['id']);original_data=copy.deepcopy(model.data);original_hash=model.content_hash
        data=copy.deepcopy(model.data);data['execution']['max_tokens']=4096;model.data=data;model.content_hash=digest(data);db.commit()
    assert client.post(url(a)+'/policy',json=body).status_code==409
    with Session(engine()) as db:
        model=db.get(Capability,cap['id']);model.data=original_data;model.content_hash=original_hash
        member=db.scalar(select(Membership));member.role='operator';db.commit()
    view=preview(client,a,cap);body.update(expected_version=view['version'],preview_hash=view['preview_hash'],request_id=str(uuid4()))
    assert client.post(url(a)+'/policy',json=body).status_code==403


def test_real_actor_revocation_during_call_preserves_paid_raw_and_blocks_import(client,monkeypatch):
    _,_,a,_,cap=seed(client);sent=[]
    def revoke():
        with Session(engine()) as db:member=db.scalar(select(Membership));member.role='viewer';db.commit()
    transport(monkeypatch,sent,hook=revoke)
    row,_,_=start(client,a,cap);run(row)
    current=get(client,a,row)
    assert current['has_saved_response'] and current['call']['usage']['total_tokens']==91
    assert current['status']=='failed' and not current['permissions']['import']['allowed']
    assert len(sent)==1


def test_upstream_withdrawal_blocks_future_suggestion_import_and_dispatch(client,monkeypatch):
    from tests.test_native_cases import pair,confirm
    _,_,a,b,view,_,_=pair(client)
    response,_=confirm(client,b,view);assert response.status_code==200,response.text
    assert accept(client,b).status_code==200
    cap=cloud_profile(client);sent=[];transport(monkeypatch,sent)
    row,_,_=start(client,b,cap);run(row);row,_=save(client,b,get(client,b,row))
    original=client.get(experience_url(a)).json()
    response=client.post(experience_url(a)+'/revoke',json=case_body(original));assert response.status_code==200,response.text
    row=get(client,b,row);assert row['source_error'] and not row['permissions']['import']['allowed']
    accepted=client.get('/api/workspace/tasks/'+b['id']+'/goal').json()['acceptance']
    response=client.post(url(b)+'/'+row['id']+'/import',json=action(row,saved_edit_hash=row['saved_edit']['hash'],acceptance_hash=digest(accepted)))
    assert response.status_code==409 and len(sent)==1
    assert client.get(experience_url(b)).status_code==200


def test_full_suggestion_history_paginates_without_silent_truncation(client):
    _,_,a,_,cap=seed(client)
    ids=[]
    for _ in range(4):row,_,_=start(client,a,cap);ids.append(row['id'])
    first=client.get(url(a)+'?limit=2').json();assert len(first['items'])==2 and first['next_cursor']
    second=client.get(url(a)+'?limit=2&after='+first['next_cursor']).json();assert len(second['items'])==2 and second['next_cursor'] is None
    assert {item['id'] for item in first['items']+second['items']}==set(ids)


def test_policy_author_revocation_blocks_unsent_auto_and_disable_needs_no_credentials(client,monkeypatch):
    release,_,a,spec,cap=seed(client);policy,_=enable_policy(client,a,cap)
    _,b=another(client,release,spec);assert accept(client,b).status_code==200
    row=client.get(url(b)).json()['items'][0]
    with Session(engine()) as db:member=db.scalar(select(Membership));member.role='operator';db.commit()
    run(row);assert get(client,b,row)['status']=='failed'
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    current=client.get(url(a)+'/policy').json()
    response=client.post(url(a)+'/policy',json=common(current['version'],enabled=False,
        expected_policy_version=current['policy']['version'],policy_record_hash=current['policy']['record_hash']))
    assert response.status_code==200,response.text
    assert not response.json()['policy']['enabled']


@pytest.mark.parametrize('stop',['policy','instance'])
def test_policy_disabled_after_budget_reserve_before_send_blocks_http_and_keeps_call_link(client,monkeypatch,stop):
    from generic_agents import cloud_budget
    release,_,a,spec,cap=seed(client);sent=[];transport(monkeypatch,sent)
    enable_policy(client,a,cap)
    _,b=another(client,release,spec);assert accept(client,b).status_code==200
    row=client.get(url(b)).json()['items'][0];original=cloud_budget.reserve
    def reserve(*args):
        call_id=original(*args)
        if stop=='policy':
            current=client.get(url(a)+'/policy').json()
            response=client.post(url(a)+'/policy',json=common(current['version'],enabled=False,
                expected_policy_version=current['policy']['version'],policy_record_hash=current['policy']['record_hash']))
        else:
            current=next(item for item in client.get('/api/instances').json() if item['id']==release['instance_id'])
            response=client.post('/api/instances/'+release['instance_id']+'/pause',json={'version':current['version']})
        assert response.status_code==200,response.text
        return call_id
    monkeypatch.setattr(cloud_budget,'reserve',reserve)
    run(row);current=get(client,b,row)
    assert current['status']=='failed' and not current['has_saved_response'] and not sent
    with Session(engine()) as db:
        source=db.get(Memory,row['id']);call=db.get(CloudCall,source.data['cloud_call_id'])
        assert call.status=='failed' and call.data['native_suggestion']['suggestion_id']==row['id']
        assert not source.data.get('dispatch_authorized')


@pytest.mark.parametrize('order',[(0,1),(1,0),(0,0)])
def test_same_actual_file_set_reordering_or_duplicates_never_proves_independent_measurements(client,monkeypatch,order):
    """真实上传/提取回执(供应商替身)/人工核查；原证明列表保持不变。"""
    from generic_agents.models import Observation,User
    from generic_agents.security import Principal
    from generic_agents import native_cases as cases
    release,obj,task,base,body=setup(client,source='observations')
    source=publish(client,manifest('source',name='file_set_reports',allowed_agent_roles=['perception'],
        execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    cap=cloud_profile(client,'qwen');field=body['goal_spec']['required_fields'][0]
    rule=publish(client,manifest('specialist_model',name='file_set_extractor_fixture',allowed_agent_roles=['perception'],
        execution={'adapter':'cloud_extractor','model_profile_id':cap['id']},dependencies=[cap['id']],
        specification={'extraction_instruction':'测试供应商替身仅返回已声明字段，不声称模型质量'},data_policy={'cloud_allowed':True},
        output_schema={'type':'object','required':['attributes','anomalies'],'properties':{'attributes':{'type':'object'},'anomalies':{'type':'array'}}}))
    monkeypatch.setattr('generic_agents.gateway.cloud_json',lambda *args,**kwargs:({'attributes':{field:obj['attributes'][field],'登记值':1},'anomalies':[]},{'fixture':True}))
    files=[]
    for content in ['独立原字节A','独立原字节B']:
        response=client.post('/api/evidence?cloud_model_id='+cap['id'],content=content.encode(),headers={'Content-Type':'text/plain'})
        assert response.status_code==201,response.text;files.append(response.json()['id'])
    first=(0,) if order==(0,0) else (0,1)
    ids=[]
    for index,positions in enumerate([first,order]):
        clock=now()-timedelta(minutes=2-index)
        response=client.post('/api/observations',json={'source_id':source['id'],'source_record_id':str(uuid4()),
            'entity_id':obj['id'],'extractor_id':rule['id'],'observed_at':clock.isoformat(),
            'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual',
            'attributes':{field:obj['attributes'][field],'登记值':1},'evidence_ids':[files[i] for i in positions]})
        assert response.status_code==201,response.text;identity=response.json()['id'];ids.append(identity)
        with Session(engine()) as db:job=db.scalar(select(Job.id).where(Job.idempotency_key=='extract:'+identity))
        assert process_once(job,deliver=True)
        with Session(engine()) as db:
            obs=db.get(Observation,identity)
            verify={'request_id':str(uuid4()),'expected_version':obs.version,'decision':'confirmed','reason':'核对上传原件与提取回执，不证明独立采样',
                'expected_input_hash':digest({'extracted':obs.data['extracted'],'evidence_ids':obs.data['evidence_ids'],'invocation_id':obs.data['invocation_id']})}
        response=client.post('/api/observations/'+identity+'/verify',json=verify);assert response.status_code==200,response.text
    define(client,base,body);response=accept(client,task)
    assert response.status_code==200,json.dumps({'reply':response.json(),'evaluation':client.get(base).json()['check']['evaluation']},ensure_ascii=False)
    with Session(engine()) as db:
        p=Principal(db.scalar(select(User.id)),client.headers['X-Workspace-ID'],'admin','test');current=db.get(Task,task['id'])
        evidence=current.data['native_goal_acceptance']['evidence']
        refs=cases._refs(db,p,current,evidence,field)
        assert len(refs)==2 and len({ref['original_fingerprint'] for ref in refs})==1
        # 保存的原证明顺序没有被规范化重写。
        assert db.get(Observation,ids[1]).data['evidence_ids']==[files[i] for i in order]
        assert methods.projection(db,p,current)['cloud_input']['fields'][0]['flags']['comparability']=='unknown'


def test_mixed_registered_field_types_have_deterministic_projection_across_process_hash_seeds(client,monkeypatch):
    import os,subprocess,sys
    from ipaddress import ip_address
    from sqlalchemy.engine import make_url
    from tests import test_instance_workspace as workspace
    database_url=make_url(os.environ['GA_DATABASE_URL'])
    if not (database_url.database or '').startswith('ga_test_'):
        owned_port=os.environ.get('GA_F11_DUAL_TESTS_OWNED','')
        assert owned_port.isascii() and owned_port.isdecimal() and 1<=int(owned_port)<=65535
        assert database_url.database=='ga_f11_dual_tests' and database_url.port==int(owned_port)
        assert ip_address(database_url.host or '').is_loopback
        actual=engine().url
        assert (actual.host,actual.port,actual.database)==(database_url.host,database_url.port,database_url.database)
        with engine().connect() as connection:
            assert connection.scalar(text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"))=='owned-disposable-f11-dual'
    monkeypatch.setattr(workspace,'OBJECTS',[{'name':'文字档案','fields':[{'label':'共享字段','kind':'文本','required':True}]},
        {'name':'数值档案','fields':[{'label':'共享字段','kind':'整数','required':True},{'label':'名称','kind':'文本','required':True}]}])
    release,_,_=workspace.prepared(client);workspace.enable(client,release);objects=[]
    field=release['snapshot']['object_types'][0]['fields'][0]['key']
    for kind,value in zip(release['snapshot']['object_types'],['原文值',12]):
        attributes={field:value}
        if len(kind['fields'])>1:attributes[kind['fields'][1]['key']]='数值条目'
        response=client.post('/api/instances/'+release['instance_id']+'/objects',json={'release_id':release['id'],
            'object_type':kind['key'],'attributes':attributes});assert response.status_code==201,response.text
        objects.append(response.json()['id'])
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json={'release_id':release['id'],
        'business_id':release['available_business_ids'][0],'entity_ids':objects,'title':'同字段不同类型的真实原任务','request_id':str(uuid4())})
    assert response.status_code==201,response.text;task=response.json()
    define(client,'/api/workspace/tasks/'+task['id']+'/goal',common(task['version'],goal_spec={
        'purpose':'核查两种对象的共享字段，未知类型不能猜测','entity_ids':objects,'required_fields':[field],
        'subgoals':[{'id':'check','description':'原任务资料核查','criterion_ids':['enough','human']}],
        'criteria':[{'id':'enough','kind':'evidence_sufficient','description':'原资料满足要求'},
            {'id':'human','kind':'human_confirmation','description':'原任务最终人工验收'}],
        'evidence':{'source':'archive','minimum_verification':'recorded','time':{'mode':'archive','timezone':'Asia/Shanghai'}},'observation_mode':'existing_only'}))
    assert accept(client,task).status_code==200
    cap=cloud_profile(client);expected=preview(client,task,cap)
    assert expected['cloud_input']['fields'][0]['type']=='unknown'
    assert 'units' not in expected['field_mapping'][0]['templates']
    with Session(engine()) as db:actor=db.scalar(select(Membership.user_id))
    script="""import json,sys
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task
from generic_agents.security import Principal
from generic_agents.native_method_suggestions import projection
scope=json.loads(sys.argv[1])
with Session(engine()) as db:
 result=projection(db,Principal(scope['actor'],scope['workspace'],'admin','test'),db.get(Task,scope['task']))
 print(json.dumps({key:result[key] for key in ['cloud_input','field_mapping','patterns']},sort_keys=True))
engine().dispose()
"""
    scope=json.dumps({'actor':actor,'workspace':client.headers['X-Workspace-ID'],'task':task['id']})
    results=[]
    for seed_value in ['1','987']:
        child=subprocess.run([sys.executable,'-c',script,scope],env={**os.environ,'PYTHONHASHSEED':seed_value},
            text=True,capture_output=True,timeout=20,check=True)
        results.append(json.loads(child.stdout))
    assert results[0]==results[1]=={key:expected[key] for key in ['cloud_input','field_mapping','patterns']}


def test_full_fifty_suggestion_page_counts_actual_unique_lineage_edges_not_repeated_reads(client):
    from tests.test_native_cases import pair,confirm
    from generic_agents.models import User
    from generic_agents.security import Principal
    _,_,a,b,view,_,_=pair(client)
    response,_=confirm(client,b,view);assert response.status_code==200,response.text
    assert accept(client,b).status_code==200;cap=cloud_profile(client)
    with Session(engine()) as db:
        p=Principal(db.scalar(select(User.id)),client.headers['X-Workspace-ID'],'admin','test')
        task=db.get(Task,b['id']);request,_=methods.preview(db,p,task,cap['id'])
        request={**request,'provider':'deepseek','model':'deepseek-chat'}
        for _ in range(50):methods.new_record(db,p,task,request)
        db.commit()
    response=client.get(url(b)+'?limit=50');assert response.status_code==200,response.text
    page=response.json();assert len(page['items'])==50 and page['next_cursor'] is None
    assert page['permissions']['start']['allowed']
    assert all(item['source_error'] is None for item in page['items'])


@pytest.mark.parametrize('change',['pause','new_release','disabled_business'])
def test_future_suggestions_require_current_active_business_scope_but_history_and_stop_remain(client,monkeypatch,change):
    from generic_agents.models import DomainInstance
    from tests import test_instance_workspace as workspace
    release,_,a,spec,cap=seed(client);sent=[];transport(monkeypatch,sent)
    enable_policy(client,a,cap);_,b=another(client,release,spec);assert accept(client,b).status_code==200
    row=client.get(url(b)).json()['items'][0];old_preview=preview(client,a,cap)
    if change=='pause':
        current=next(item for item in client.get('/api/instances').json() if item['id']==release['instance_id'])
        response=client.post('/api/instances/'+release['instance_id']+'/pause',json={'version':current['version']})
        assert response.status_code==200,response.text
    elif change=='new_release':
        configuration=workspace.configuration();configuration['instanceProfile']['name']='下一业务版本'
        revised,_,_=workspace.prepared(client,configuration,instance_id=release['instance_id'])
        assert revised['id']!=release['id'];workspace.enable(client,revised)
    else:
        with Session(engine()) as db:
            instance=db.get(DomainInstance,release['instance_id']);instance.data={**instance.data,'enabled_business_ids':[]};instance.version+=1;db.commit()
    run(row);current=get(client,b,row)
    assert current['status']=='failed' and current['source_error'] and not sent
    assert not current['permissions']['import']['allowed']
    assert client.get(experience_url(b)).status_code==200
    assert client.post(url(a)+'/preview',json={'model_profile_id':cap['id']}).status_code==409
    page=client.get(url(a)).json();assert not page['permissions']['start']['allowed'] and not page['policy']['permissions']['enable']['allowed']
    response=client.post(url(a)+'/start',json=common(page['version'],model_profile_id=cap['id'],preview_hash=old_preview['preview_hash'],cloud_authorized=True))
    assert response.status_code==409
    policy=client.get(url(a)+'/policy').json()
    response=client.post(url(a)+'/policy',json=common(policy['version'],enabled=False,
        expected_policy_version=policy['policy']['version'],policy_record_hash=policy['policy']['record_hash']))
    assert response.status_code==200,response.text
    response=client.post(url(b)+'/'+row['id']+'/cancel',json=action(get(client,b,row)))
    assert response.status_code==200 and response.json()['suggestion']['status']=='cancelled'
    with Session(engine()) as db:assert db.scalar(select(CloudCall.id)) is None
