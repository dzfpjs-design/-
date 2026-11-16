"""匿名目标/发送授权/旧付费恢复；供应商替身不代表云模型质量。"""
import copy,json
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Task,Memory,CloudCall,Membership,Job
from generic_agents.security import Principal
from generic_agents import native_method_suggestions as methods
from generic_agents import gateway
from tests import test_native_method_suggestions as old
from tests.test_instance_workspace import configuration,save,enable,task_row
from tests.test_native_goals import define
from tests.test_native_cases import accept


def structured(client,kinds=('文本','整数'),time_required=False):
    revision=save(client,configuration(),review_automation=True);member=client.get('/api/instances/members').json()[0]
    body={'revision':revision,'role_bindings':{'核验负责人':member['id']},'objects':[{'name':'PRIVATE_OBJECT',
        'fields':[{'label':'PRIVATE_FIELD_'+str(i),'kind':kind,'required':True} for i,kind in enumerate(kinds)]}]}
    check=client.post('/api/instances/check',json=body);assert check.status_code==200,check.text
    response=client.post('/api/instances',json={**body,'confirmed_configuration_hash':check.json()['configuration_hash'],'confirm_configuration':True})
    assert response.status_code==201,response.text;release=response.json();enable(client,release)
    object_type=release['snapshot']['object_types'][0];fields=object_type['fields']
    values={field['key']:(13+i if kinds[i]=='整数' else 'PRIVATE_VALUE_'+str(i)) for i,field in enumerate(fields)}
    response=client.post('/api/instances/'+release['instance_id']+'/objects',json={'release_id':release['id'],'object_type':object_type['key'],'attributes':values})
    assert response.status_code==201,response.text;obj=response.json();task,_=task_row(client,release,obj)
    if time_required:
        from tests.test_vertical_slice import publish,manifest
        from tests.test_native_cases import reported
        source=publish(client,manifest('source',name='v2_declared_time_report',allowed_agent_roles=['perception'],
            execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
        rule=publish(client,manifest('tool',name='v2_declared_time_rule',allowed_agent_roles=['perception'],
            execution={'adapter':'builtin','handler':'numeric_rules'},
            specification={'rules':[{'id':'high','field':'fixture_value','operator':'gt','threshold':50}]},data_policy={'cloud_allowed':False}))
        reported(client,release,obj,source,rule,{**values,'fixture_value':1})
    spec={'purpose':'PRIVATE_PURPOSE','entity_ids':[obj['id']],'required_fields':list(values),
        'criteria':[{'id':'enough','kind':'evidence_sufficient','description':'PRIVATE_DESCRIPTION'},
                    {'id':'value','kind':'field_value','field':fields[0]['key'],'operator':'eq','expected':values[fields[0]['key']],'description':'PRIVATE_COMPARISON'},
                    {'id':'human','kind':'human_confirmation','description':'PRIVATE_FINAL'}],
        'subgoals':[{'id':'check','description':'PRIVATE_SUBGOAL','criterion_ids':['enough','value','human']}],
        'evidence':{'source':'observations' if time_required else 'archive','minimum_verification':'confirmed' if time_required else 'recorded','time':{'mode':'latest' if time_required else 'archive','timezone':'Asia/Shanghai',**({'max_age_seconds':86400} if time_required else {})}},
        'observation_mode':'existing_only'}
    define(client,'/api/workspace/tasks/'+task['id']+'/goal',old.common(task['version'],goal_spec=spec))
    response=accept(client,task);assert response.status_code==200,response.text
    return task,old.cloud_profile(client),spec,obj


def principal(db):
    member=db.scalar(select(Membership));return Principal(member.user_id,member.workspace_id,member.role,'test')


def legacy(client,task,cap,monkeypatch=None,sent=None,paid=False):
    """构造升级前确实可保存的v1结构与真实预算/响应链，不伪造新版授权。"""
    with Session(engine()) as db:
        p=principal(db);source=db.get(Task,task['id']);profile=methods.model(db,p,cap['id'])
        value=methods.projection(db,p,source,version=methods.V1)
        request={**value,'task_id':source.id,'task_version':source.version,'origin_hash':digest(value['source_proof']),
            'model_profile_id':profile.id,'model_hash':methods.model_hash(profile),'projection_contract_hash':methods.V1_CONTRACT_HASH}
        request['preview_hash']=digest(request)
        request.update(provider=profile.data['execution']['provider'],model=profile.data['execution']['model'])
        row=methods.new_record(db,p,source,request);identity=row.id;db.commit()
        if paid:
            old.transport(monkeypatch,sent)
            def reserved(call_id):
                call=db.get(CloudCall,call_id)
                call.data={**call.data,'native_suggestion':{'suggestion_id':row.id,'job_id':row.data['job_id'],'task_id':source.id,'request_hash':row.data['request_hash']}}
                row.data={**row.data,'cloud_call_id':call_id};methods.seal(row);db.commit()
            messages=[{'role':'user','content':json.dumps({'task':'choose_1_to_5_allowed_pairs_only',
                'output_shape':{'steps':[{'alias':'F1','template':'source_identity','pattern_id':'identity_current_v1'}]},'input':request['cloud_input']},ensure_ascii=False)}]
            value,receipt=gateway.cloud_json(profile,messages,{},p,on_reserved=reserved)
            row.status='failed';row.data={**row.data,'raw_response':methods.signed({'value':value,'receipt':receipt}),'inflight':True,'error':'已付响应后进程退出'}
            job=db.get(Job,row.data['job_id']);job.status='failed';methods.seal(row);db.commit()
    return old.get(client,task,{'id':identity})


def test_preview_target_priority_capacity_and_fixed_rules_are_signed_without_values(client):
    task,cap,spec,obj=structured(client)
    view=old.preview(client,task,cap);cloud=view['cloud_input'];contract=view['generation_contract']
    assert cloud['contract_version']=='native-method-projection-v2'
    assert cloud['goal']=={'required_aliases':['F1','F2'],'field_criteria':[{'kind':'field_value','alias':'F1'}],
        'minimum_verification':'recorded','time_constraint':'none'}
    assert cloud['fields'][0]['priority_tags']==['goal_required']
    assert cloud['fields'][1]['priority_tags']==['goal_required','numeric_unit_unknown']
    assert view['capacity']=={'required_count':3,'max_steps':5}
    assert contract['version']=='native-method-generation-v2' and contract['prompt_version']=='native-method-prompt-v2'
    assert all(len(contract[key])==64 for key in ['prompt_hash','output_contract_hash','generation_contract_hash'])
    assert isinstance(contract['rules'],list) and contract['rules'] and all(isinstance(rule,str) for rule in contract['rules'])
    serialized=json.dumps(cloud,ensure_ascii=False)
    for marker in ['PRIVATE_',spec['required_fields'][0],obj['id'],task['id'],'"expected"','"operator"']:assert marker not in serialized


def test_real_time_requirement_counts_field_time_even_when_declaration_is_known(client):
    task,cap,_,_=structured(client,kinds=('文本',),time_required=True)
    view=old.preview(client,task,cap)
    assert view['cloud_input']['goal']['time_constraint']=='required'
    assert view['cloud_input']['fields'][0]['flags']['declared_time']=='known'
    assert view['cloud_input']['fields'][0]['priority_tags']==['goal_required']
    assert view['capacity']['required_count']==2


def test_unknown_declared_time_without_real_time_goal_does_not_invent_time_requirement(client):
    task,cap,_,_=structured(client,kinds=('文本',))
    view=old.preview(client,task,cap)
    assert view['cloud_input']['fields'][0]['flags']['declared_time']=='unknown'
    assert view['cloud_input']['fields'][0]['priority_tags']==['goal_required']
    assert view['cloud_input']['goal']['time_constraint']=='none' and view['capacity']['required_count']==1


@pytest.mark.parametrize('kinds,time_required',[(('文本',)*6,False),(('文本','整数','整数','整数'),False),(('文本',)*3,True)])
def test_real_required_checks_over_five_block_before_job_or_budget_without_truncation(client,kinds,time_required):
    task,cap,_,_=structured(client,kinds=kinds,time_required=time_required)
    before=client.get('/api/workspace/tasks/'+task['id']+'/goal').json()['acceptance']
    response=client.post(old.url(task)+'/preview',json={'model_profile_id':cap['id']})
    assert response.status_code==409 and '超过建议容量' in response.json()['detail']
    assert client.get('/api/workspace/tasks/'+task['id']+'/goal').json()['acceptance']==before
    with Session(engine()) as db:
        assert not list(db.scalars(select(CloudCall)))
        assert not list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==methods.TYPE)))


def test_actual_gateway_wire_exactly_matches_saved_dispatch_hash_and_has_no_single_choice_example(client,monkeypatch):
    task,cap,_,_=structured(client);sent=[];old.transport(monkeypatch,sent)
    row,_,view=old.start(client,task,cap);old.run(row);row=old.get(client,task,row)
    assert row['status']=='needs_review' and len(sent)==1
    assert digest(sent[0]['messages'])==view['dispatch_messages_hash']==row['dispatch_messages_hash']
    content=json.loads(sent[0]['messages'][-1]['content'])
    assert 'output_shape' not in content and content['input']==view['cloud_input']
    assert 'PRIVATE_' not in json.dumps(sent)
    with Session(engine()) as db:
        saved=db.get(Memory,row['id']).data['request'];call=db.scalar(select(CloudCall))
        assert saved['dispatch_messages_hash']==call.data['request_hash']
        assert saved['dispatch_request_messages']==sent[0]['messages'] and saved['dispatch_schema']=={}
        assert saved['generation_contract']==view['generation_contract']


@pytest.mark.parametrize('change',['prompt','output','gateway_prefix'])
def test_changed_fixed_generation_contract_stops_old_preview_and_unsent_job(client,monkeypatch,change):
    _,_,task,_,cap=old.seed(client);row,body,view=old.start(client,task,cap);sent=[];old.transport(monkeypatch,sent)
    if change=='prompt':monkeypatch.setattr(methods,'GENERATION_RULES',[*methods.GENERATION_RULES,'新的固定规则'])
    elif change=='output':monkeypatch.setattr(methods,'OUTPUT_CONTRACT',{**methods.OUTPUT_CONTRACT,'description':'新的输出合同'})
    else:monkeypatch.setattr(gateway,'CLOUD_JSON_SYSTEM_PREFIX','修改了固定网关前缀')
    second={**body,'request_id':str(uuid4()),'expected_version':row['task_version']}
    response=client.post(old.url(task)+'/start',json=second);assert response.status_code==409
    old.run(row);current=old.get(client,task,row)
    assert current['status']=='failed' and not sent
    with Session(engine()) as db:assert not list(db.scalars(select(CloudCall)))


@pytest.mark.parametrize('mismatch',['messages','options'])
def test_reserved_ledger_hash_mismatch_rejects_http_not_just_local_request_data(client,monkeypatch,mismatch):
    _,_,task,_,cap=old.seed(client);row,_,_=old.start(client,task,cap);sent=[];old.transport(monkeypatch,sent)
    from generic_agents import cloud_budget
    if mismatch=='messages':
        reserve=cloud_budget.reserve
        def altered(*args):
            identity=reserve(*args)
            with Session(engine()) as db:
                call=db.get(CloudCall,identity);call.data={**call.data,'request_hash':'0'*64};db.commit()
            return identity
        monkeypatch.setattr(cloud_budget,'reserve',altered)
    else:
        # 模拟派发器传错实际参数；发布配置/用户授权保持原值，不能用自身摘要证明自身。
        from types import SimpleNamespace
        def changed_dispatch(cap,*args,**kwargs):
            data=copy.deepcopy(cap.data);data['execution']['max_tokens']+=1
            actual=SimpleNamespace(id=cap.id,workspace_id=cap.workspace_id,data=data)
            return gateway.cloud_json(actual,*args,**kwargs)
        monkeypatch.setattr(methods,'cloud_json',changed_dispatch)
    old.run(row);result=old.get(client,task,row)
    assert result['status']=='failed' and not sent
    with Session(engine()) as db:
        call=db.scalar(select(CloudCall));assert call.status=='failed'
        if mismatch=='options':assert call.data['dispatch_options_hash']!=digest(old.preview(client,task,cap)['dispatch_options'])


def test_v1_paid_raw_restores_locally_using_v1_without_new_call_or_v2_tags(client,monkeypatch):
    _,_,task,_,cap=old.seed(client);sent=[];row=legacy(client,task,cap,monkeypatch,sent,paid=True)
    assert row['generation_contract']['version']=='native-method-generation-v1' and row['dispatch_messages_hash'] is None
    assert row['generation_contract']['prompt_hash'] is None and 'goal' not in row['cloud_input']
    monkeypatch.setattr(methods,'PATTERNS',{**methods.PATTERNS,'source_identity':('identity_current_v1','新版文字不能改写旧付费原答')})
    response=client.post(old.url(task)+'/'+row['id']+'/retry',json=old.action(row));assert response.status_code==200,response.text
    result=response.json()['suggestion'];assert result['status']=='needs_review' and len(sent)==1
    assert result['steps'][0]['kind']=='source_identity' and 'priority_tags' not in result['cloud_input']['fields'][0]
    assert result['steps'][0]['instruction']==methods.V1_PATTERNS['source_identity'][1]


def test_v1_saved_start_uuid_replay_probe_and_abandon_never_upgrade_authorization(client):
    _,_,task,_,cap=old.seed(client);row=legacy(client,task,cap)
    with Session(engine()) as db:
        parent=db.get(Task,task['id']);p=principal(db);request=db.get(Memory,row['id']).data['request']
        body=old.common(parent.version,model_profile_id=cap['id'],preview_hash=request['preview_hash'],cloud_authorized=True)
        entry={'request_id':body['request_id'],'actor_id':p.user_id,'hash':digest({'operation':'start','target':None,'body':body}),
            'operation':'start','target':None,'status':'saved','at':'2026-10-01T00:00:00+00:00','result':row['id']}
        parent.data={**parent.data,'native_suggestion_commands':[entry],'native_suggestion_commands_hash':digest([entry])}
        parent.version+=1;db.commit()
    current=old.get(client,task,row)
    recovery=old.recovery_body(current['task_version'],operation='start',original_request=body,action='probe')
    response=client.post(old.url(task)+'/requests/recover',json=recovery)
    assert response.status_code==200 and response.json()['saved'] and response.json()['suggestion']['id']==row['id']
    response=client.post(old.url(task)+'/start',json=body)
    assert response.status_code==200 and response.json()['suggestion']['cloud_input']['contract_version']==methods.V1
    unknown={**body,'request_id':str(uuid4()),'expected_version':current['task_version']}
    recovery.update(original_request=unknown,action='probe')
    assert not client.post(old.url(task)+'/requests/recover',json=recovery).json()['saved']
    recovery['action']='abandon';assert client.post(old.url(task)+'/requests/recover',json=recovery).status_code==200
    assert client.post(old.url(task)+'/start',json=unknown).status_code==409
    with Session(engine()) as db:
        assert not list(db.scalars(select(CloudCall)))
        assert len(list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==methods.TYPE))))==1


def test_v1_unsent_stops_and_unknown_version_fails_without_cloud(client,monkeypatch):
    _,_,task,_,cap=old.seed(client);row=legacy(client,task,cap);sent=[];old.transport(monkeypatch,sent)
    old.run(row);current=old.get(client,task,row)
    assert current['status']=='failed' and '重新预览' in current['error'] and not sent
    with Session(engine()) as db:
        saved=db.get(Memory,row['id']);data=copy.deepcopy(saved.data)
        data['request']['cloud_input']['contract_version']='unknown-version';data['request_hash']=digest(data['request'])
        saved.data=data;methods.seal(saved);db.commit()
    assert client.get(old.url(task)+'/'+row['id']).status_code==409


def test_v2_policy_requires_exact_generation_contract_and_legacy_policy_can_disable(client):
    _,_,task,_,cap=old.seed(client);view=old.preview(client,task,cap)
    body=old.common(view['version'],enabled=True,model_profile_id=cap['id'],projection_contract_hash=view['projection_contract_hash'],
        preview_hash=view['preview_hash'],generation_contract_hash='0'*64,cloud_authorized=True,daily_limit=1,valid_days=1)
    assert client.post(old.url(task)+'/policy',json=body).status_code==409
    body['generation_contract_hash']=view['generation_contract']['generation_contract_hash']
    response=client.post(old.url(task)+'/policy',json=body);assert response.status_code==200,response.text
    with Session(engine()) as db:
        p=principal(db);parent=db.get(Task,task['id']);policy=methods.policy_row(db,p,parent)
        data=copy.deepcopy(policy.data);data['projection_contract_hash']=methods.V1_CONTRACT_HASH
        data.pop('generation_contract',None);data.pop('generation_contract_hash',None);policy.data=data;methods.seal(policy);db.commit()
    state=client.get(old.url(task)+'/policy').json();policy=state['policy']
    assert policy['enabled'] and not policy['effective_enabled'] and policy['requires_reauthorization'] and '重新授权' in policy['authorization_error']
    response=client.post(old.url(task)+'/policy',json=old.common(state['version'],enabled=False,
        expected_policy_version=policy['version'],policy_record_hash=policy['record_hash']))
    assert response.status_code==200 and not response.json()['policy']['enabled']


def test_legacy_policy_uuid_body_null_new_field_does_not_change_exact_old_journal(client):
    _,_,task,_,cap=old.seed(client)
    policy,current_body=old.enable_policy(client,task,cap)
    with Session(engine()) as db:
        parent=db.get(Task,task['id']);p=principal(db);saved=methods.policy_row(db,p,parent)
        data=copy.deepcopy(saved.data);data['projection_contract_hash']=methods.V1_CONTRACT_HASH
        data.pop('generation_contract');data.pop('generation_contract_hash');saved.data=data;methods.seal(saved)
        previous=methods.PolicyChange.model_validate(current_body).model_dump();previous.pop('generation_contract_hash')
        previous['projection_contract_hash']=methods.V1_CONTRACT_HASH
        body=previous
        entry={'request_id':body['request_id'],'actor_id':p.user_id,'hash':digest({'operation':'policy','target':None,'body':previous}),
            'operation':'policy','target':None,'status':'saved','at':'2026-10-01T00:00:00+00:00','result':None}
        parent.data={**parent.data,'native_suggestion_commands':[entry],'native_suggestion_commands_hash':digest([entry])};db.commit()
    current=client.get(old.url(task)+'/policy').json()
    recovery=old.recovery_body(current['version'],operation='policy',original_request=body,action='probe')
    result=client.post(old.url(task)+'/requests/recover',json=recovery);assert result.status_code==200 and result.json()['saved']
    replay=client.post(old.url(task)+'/policy',json=body)
    assert replay.status_code==200 and replay.json()['policy']['version']==policy['version']
    assert replay.json()['policy']['requires_reauthorization'] and not replay.json()['policy']['effective_enabled']
    assert client.post(old.url(task)+'/policy',json={**body,'generation_contract_hash':methods.generation_contract()['generation_contract_hash']}).status_code==409
    unknown={**body,'request_id':str(uuid4()),'expected_version':current['version']};recovery['original_request']=unknown;recovery['action']='abandon'
    assert client.post(old.url(task)+'/requests/recover',json=recovery).status_code==200
    assert client.post(old.url(task)+'/policy',json=unknown).status_code==409


def test_known_observation_time_still_requires_each_alias_time_check(client):
    from tests.test_native_cases import observation_pair
    a,_,_,_,_=observation_pair(client);cap=old.cloud_profile(client)
    view=old.preview(client,a,cap)
    assert view['cloud_input']['goal']['time_constraint']=='required'
    assert view['cloud_input']['fields'][0]['flags']['declared_time']=='known'
    assert 'required_time_unknown' not in view['cloud_input']['fields'][0]['priority_tags']
    assert view['capacity']['required_count']==2


def test_legal_partial_raw_is_failed_but_explicit_complete_human_edit_can_import_without_counting_raw_pass(client,monkeypatch):
    task,cap,_,_=structured(client);sent=[]
    old.transport(monkeypatch,sent,mutate=lambda value:value.update(steps=[{'alias':'F1','template':'source_identity','pattern_id':'identity_current_v1'}]))
    row,_,_=old.start(client,task,cap);old.run(row);row=old.get(client,task,row)
    assert row['status']=='failed' and row['error_code']=='insufficient_required_checks'
    assert row['raw_quality']['status']=='insufficient' and row['raw_quality']['required_count']==3 and row['raw_quality']['covered_count']==0
    assert len(row['missing_required_pairs'])==3 and len(row['steps'])==1 and row['permissions']['save']['allowed']
    assert not row['permissions']['import']['allowed'] and not row['permissions']['local_recover']['allowed']
    incomplete=old.action(row,title='人工尚未补齐',summary='本地自由说明不出云',limits='仍保留原标准',
        steps=[{key:item[key] for key in ['id','kind','field','instruction']} for item in row['steps']])
    assert client.post(old.url(task)+'/'+row['id']+'/save',json=incomplete).status_code==409
    current=old.get(client,task,row);assert current['version']==row['version'] and current['raw_model_content_hash']==row['raw_model_content_hash']
    aliases={item['alias']:item['field'] for item in row['field_mapping']}
    complete=old.action(row,title='人工对照原标准补齐',summary='人工核查，不证明模型通过',limits='未知单位仍未知',
        steps=[{'id':'human'+str(i),'kind':pair['template'],'field':aliases[pair['alias']],'instruction':'请查看本次当前原件，不猜未知事实'}
            for i,pair in enumerate(row['missing_required_pairs'])])
    response=client.post(old.url(task)+'/'+row['id']+'/save',json=complete);assert response.status_code==200,response.text
    edited=response.json()['suggestion'];assert edited['status']=='needs_review' and edited['saved_edit']['quality']['status']=='passed'
    assert edited['raw_quality']==row['raw_quality'] and edited['error_code']=='insufficient_required_checks' and edited['permissions']['import']['allowed']
    source=client.get('/api/workspace/tasks/'+task['id']+'/experience').json()
    response=client.post(old.url(task)+'/'+row['id']+'/import',json=old.action(edited,
        saved_edit_hash=edited['saved_edit']['hash'],acceptance_hash=source['source']['acceptance_hash']))
    assert response.status_code==200 and response.json()['experience']['draft']['status']=='draft' and len(sent)==1
    assert response.json()['suggestion']['raw_quality']['status']=='insufficient'


def test_illegal_raw_still_cannot_use_partial_human_salvage(client,monkeypatch):
    _,_,task,_,cap=old.seed(client);sent=[]
    old.transport(monkeypatch,sent,mutate=lambda value:value['steps'][0].update(alias='F99'))
    row,_,_=old.start(client,task,cap);old.run(row);row=old.get(client,task,row)
    assert row['status']=='failed' and not row['permissions']['save']['allowed']
    assert row['raw_quality'] is None and row['missing_required_pairs']==[] and row['error_code'] is None
    field=row['field_mapping'][0]['field']
    body=old.action(row,title='不能覆盖非法原答',summary='保留错误',limits='不绕过结构检查',
        steps=[{'id':'check','kind':'field_verification','field':field}])
    assert client.post(old.url(task)+'/'+row['id']+'/save',json=body).status_code==409 and len(sent)==1


@pytest.mark.parametrize('mode',[None,'disabled','enabled'])
def test_explicit_deepseek_thinking_wire_is_signed_and_old_profile_omits_it(client,monkeypatch,mode):
    from tests.test_vertical_slice import publish,manifest
    _,_,task,_,_=old.seed(client)
    execution={'provider':'deepseek','model':'deepseek-flash','secret_ref':'DEEPSEEK_API_KEY','max_tokens':2048}
    if mode is not None:execution['thinking_mode']=mode
    cap=publish(client,manifest('foundation_model',name='explicit_thinking_'+str(mode).lower(),allowed_agent_roles=['business'],execution=execution,data_policy={'cloud_allowed':True}))
    sent=[];old.transport(monkeypatch,sent);row,_,view=old.start(client,task,cap);old.run(row)
    result=old.get(client,task,row);assert result['status']=='needs_review' and len(sent)==1
    options={key:value for key,value in sent[0].items() if key!='messages'}
    assert options==view['dispatch_options']==result['dispatch_options']
    assert ('thinking' in options)==(mode is not None)
    if mode is not None:assert options['thinking']=={'type':mode}


@pytest.mark.parametrize('provider,mode',[('qwen','disabled'),('deepseek','unknown'),('deepseek',None),('deepseek',{'type':'disabled'})])
def test_invalid_thinking_is_rejected_at_publish_and_runtime_before_budget(client,monkeypatch,provider,mode):
    from tests.test_vertical_slice import manifest
    body=manifest('foundation_model',name='reject_thinking',allowed_agent_roles=['business'],execution={
        'provider':provider,'model':'qwen-plus' if provider=='qwen' else 'deepseek-flash',
        'secret_ref':'DASHSCOPE_API_KEY' if provider=='qwen' else 'DEEPSEEK_API_KEY','max_tokens':2048,'thinking_mode':mode},data_policy={'cloud_allowed':True})
    created=client.post('/api/capabilities',json=body);assert created.status_code==201
    row=created.json();response=client.post('/api/capabilities/'+row['id']+'/validate',json={'expected_version':row['version'],'reason':'拒绝未知或跨供应商参数'})
    assert response.status_code==422 and 'thinking_mode' in response.json()['detail']
    with pytest.raises(gateway.ModelBlocked):gateway.cloud_json_options(body['execution'])
    with Session(engine()) as db:assert not list(db.scalars(select(CloudCall)))
