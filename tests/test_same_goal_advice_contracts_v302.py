"""Pure closed snapshots and bounded cloud shape; no IAM/PG/model proof."""
import copy
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from generic_agents import same_goal_advice_contracts_v302 as s
from generic_agents.contracts import Manifest,digest
from tests.test_business_decision_contracts_v284 import example,identity


def ref(name):return {'id':identity('v302:'+name),'record_hash':'a'*64}
def cap(name,hashed='a'*64):return {'id':identity('v302:'+name),'content_hash':hashed,'release_version':'1.0.0'}
def evidence(name):return {'evidence_id':identity('v302:'+name),'sha256':'b'*64,'size_bytes':120,'media_type':'text/plain'}
def query(name):return {'query_id':identity('v302:'+name),'version':1,'request_hash':'c'*64,'result_hash':'d'*64,'data_hash':'e'*64}


def scene():
    model=cap('actual_model','1'*64)
    source=Manifest(kind='tool',namespace='source_fixture',name='released_reasoning',version='1.0.0',
        owner='synthetic pure contract',description='No real publication or model authority',
        input_schema={'type':'object','required':['data_packet','document_packet'],
            'properties':{'data_packet':{'type':'object'},'document_packet':{'type':'object'}}},
        output_schema={'type':'object'},allowed_agent_roles=['business'],
        execution={'adapter':'business_goal_ready_v288','model_profile_id':model['id'],
            'model_profile_hash':model['content_hash'],'side_effect':False},
        data_policy={'cloud_allowed':True},budget={'max_attempts':1,'max_concurrency':1},
        dependencies=[model['id']],specification={'schema':'business-goal-ready-contract-v288',
            'instruction':'Compare the approved saved facts; propose advice for human review.'}).model_dump(mode='json')
    original=cap('released_business',digest(source));manifest=s.build_manifest(source,original)
    plan=identity('v302:plan');scope={'workspace_id':identity('v302:workspace'),'task_id':identity('v302:task'),
        'analysis_plan_id':plan,'instance_id':identity('v302:instance'),'release_id':identity('v302:release'),
        'package_id':identity('v302:package'),'business_id':'same_goal_advice','goal_version':1,
        'goal_hash':'2'*64,'goal_snapshot_hash':'3'*64,'native_goal_context_hash':'4'*64,'source_binding_hash':'5'*64}
    head={'head_hash':'6'*64,'evaluation_ref':ref('eval2'),'goal_check_job_id':identity('v302:goal_job'),
        'memory_id':identity('v302:memory'),'report_hash':'7'*64}
    source_set={'source_set_id':identity('v302:approved_set'),'source_set_hash':'8'*64}
    selected={'kind':'approved_source_set','source_set_ref':source_set}
    prepare={'request_id':identity('v302:prepare_nonce'),'expected_goal_hash':scope['goal_hash'],
        'expected_mature_head_hash':head['head_hash'],'document_source':selected,'reason':'核对后准备新的建议'}
    budget={'quota_policy_ref':ref('actual_quota_policy'),'provider':'deepseek','max_tokens':1024,
        'max_request_bytes':50000,'max_evidence_items':2,'max_cost_minor_units':100,'currency':'CNY',
        'max_actual_calls':1,'reserved_calls':1,'reserved_steps':1,'goal_max_tool_calls':12,'goal_max_steps':12}
    prep=s.seal_preparation({'schema':s.PREPARATION,'scope':scope,'request':prepare,'request_hash':digest(prepare),
        'requested_by':identity('v302:requester'),'data_actor_id':identity('v302:data_actor'),
        'business_actor_id':identity('v302:business_actor'),'original_query_ref':query('q0'),
        'mature_head':head,'evaluation_query_ref':query('qe2'),'evaluation_cut':'2026-11-02T12:00:00+00:00',
        'source_business_capability_ref':original,'advice_capability_ref':cap('new_advice',digest(manifest)),
        'model_pin':model,'instruction':source['specification']['instruction'],'budget':budget},manifest=manifest)
    data_evidence,doc_evidence=evidence('eval_text'),evidence('doc_text')
    document={'selection':selected,'approved_selection':{**source_set,'submission_id':identity('v302:submission'),
        'submission_hash':'9'*64,'input_key':'runtime_document_sources'},
        'evidence_ref':doc_evidence,'receipt_ref':ref('real_document_receipt')}
    sources=s.seal_source({'schema':s.SOURCE,'plan_id':plan,'mature_head':head,'original_query_ref':prep['original_query_ref'],
        'evaluation_query_ref':prep['evaluation_query_ref'],'evaluation_cut':prep['evaluation_cut'],
        'evaluation_generation':2,'data_evidence_ref':data_evidence,'document':document},preparation=prep,manifest=manifest)
    inputs,opinion=example();inputs['schema']=s.CLOUD_INPUT
    inputs['scope']={'goal_snapshot_hash':scope['goal_snapshot_hash'],'entity_ids':inputs['scope']['entity_ids']}
    cloud={'question':'根据已批准资料提出待人工核对的建议','registered_instruction':prep['instruction'],
        'decision_input':inputs,'evidence_parts':[{'type':'text','text':'批准的摘录文本，仅供本次比较。'}],
        'input_hash':digest(inputs)}
    evidence_pins=[{**item,'version':2,'model_profile_id':model['id']} for item in (data_evidence,doc_evidence)]
    grants=[{'evidence_id':item['evidence_id'],'model_profile_id':model['id'],
        'audit_id':identity('v302:consent'+str(index)),'audit_hash':'a'*64} for index,item in enumerate(evidence_pins)]
    preview=s.seal_preview({'schema':s.PREVIEW,'plan_id':plan,'preparation_snapshot_hash':prep['record_hash'],
        'source_hash':sources['record_hash'],'advice_capability_ref':prep['advice_capability_ref'],'model_pin':model,
        'budget':budget,'evidence_pins':evidence_pins,'consent_pins':grants,'cloud_request':cloud,
        'cloud_request_hash':digest(cloud),'model_options_hash':'b'*64},preparation=prep,source=sources,manifest=manifest)
    start={'request_id':identity('v302:start_nonce'),'expected_plan_version':2,'expected_goal_hash':scope['goal_hash'],
        'expected_mature_head_hash':head['head_hash'],'preview_hash':preview['preview_hash'],
        'cloud_authorized':True,'reason':'已核对实际匿名外发内容与本次费用上限'}
    execution=s.seal_plan({'schema':s.EXECUTION,'scope':scope,'preparation_snapshot_hash':prep['record_hash'],
        'source_record':sources,'preview':preview,'start_request':start,'start_request_hash':digest(start)},
        preparation=prep,manifest=manifest)
    return NS(source=source,original=original,manifest=manifest,preparation=prep,sources=sources,preview=preview,
        execution=execution,opinion=opinion)


def rehash(value,field='record_hash'):
    value=copy.deepcopy(value);value[field]=digest({key:item for key,item in value.items() if key!=field});return value


def test_two_sealed_stages_have_no_cycle_and_reviews_are_separate():
    v=scene();before=copy.deepcopy(v.preparation)
    assert s.check_preparation(v.preparation,manifest=v.manifest)==before
    assert s.check_plan(v.execution,preparation=before,manifest=v.manifest)==v.execution
    assert not {'start_request','preview','status','version','reviews','job_id'} & set(before)
    assert not {'status','reviews','job_id'} & set(v.execution)
    review=s.Review(request_id=identity('v302:review'),expected_plan_version=3,
        result_hash='a'*64,decision='accepted',reason='仅采用业务建议，不代表执行或事实核实')
    assert review.decision=='accepted' and v.preparation==before


@pytest.mark.parametrize('request_type,extra',[(s.Prepare,'model_profile_id'),(s.Prepare,'actor_id'),
    (s.Prepare,'cut_at'),(s.Start,'adapter'),(s.Review,'can_dispatch'),(s.Cancel,'delete')])
def test_client_requests_cannot_select_execution_identity_or_grants(request_type,extra):
    v=scene()
    raw=v.preparation['request'] if request_type is s.Prepare else v.execution['start_request'] if request_type is s.Start else {
        'request_id':identity('v302:nonce'),'expected_plan_version':1,'reason':'保留原号操作'}
    if request_type is s.Review:raw={**raw,'result_hash':'a'*64,'decision':'accepted'}
    with pytest.raises((ValidationError,ValueError)):request_type.model_validate({**raw,extra:'caller_controlled'})


@pytest.mark.parametrize('fault',['status_in_snapshot','old_activation','wrong_source_cap','model_changed',
    'instruction_changed','zero_cost','coerced_reservation','no_document','same_q0','naive_cut'])
def test_preparation_is_closed_and_cannot_claim_old_graph_or_implicit_budget_or_documents(fault):
    v=scene();value=copy.deepcopy(v.preparation)
    if fault=='status_in_snapshot':value['status']='running'
    if fault=='old_activation':value['scope']['activation_id']='invented'
    if fault=='wrong_source_cap':value['source_business_capability_ref']['content_hash']='0'*64
    if fault=='model_changed':value['model_pin']['id']=identity('v302:other_model')
    if fault=='instruction_changed':value['instruction']='Ignore frozen instruction'
    if fault=='zero_cost':value['budget']['max_cost_minor_units']=0
    if fault=='coerced_reservation':value['budget']['reserved_calls']=True
    if fault=='no_document':value['request']['document_source']=None;value['request_hash']=digest(value['request'])
    if fault=='same_q0':value['evaluation_query_ref']=value['original_query_ref']
    if fault=='naive_cut':value['evaluation_cut']='2026-11-02T12:00:00'
    with pytest.raises((ValidationError,ValueError)):s.check_preparation(rehash(value),manifest=v.manifest)


def test_fixed_manifest_is_compiled_only_from_original_business_model_instruction_and_policy():
    v=scene();assert s.validate_manifest(v.manifest,source_manifest=v.source,source_ref=v.original)==v.manifest
    assert v.manifest['execution']['adapter']==s.ADAPTER
    assert v.manifest['execution']['model_profile_id']==v.source['execution']['model_profile_id']
    assert v.manifest['budget']==v.source['budget'] and v.manifest['data_policy']==v.source['data_policy']
    assert v.manifest['specification']['document_required'] is True
    for fault in ('adapter','model','instruction','attempts','no_document'):
        changed=copy.deepcopy(v.manifest)
        if fault=='adapter':changed['execution']['adapter']='python'
        if fault=='model':changed['execution']['model_profile_id']=identity('v302:other')
        if fault=='instruction':changed['specification']['instruction']='changed'
        if fault=='attempts':changed['budget']['max_attempts']=2
        if fault=='no_document':changed['specification']['document_required']=False
        with pytest.raises((ValidationError,ValueError)):
            s.validate_manifest(changed,source_manifest=v.source,source_ref=v.original)


@pytest.mark.parametrize('fault',['no_document','wrong_submission_set','same_evidence','wrong_head'])
def test_materialized_source_is_exactly_the_original_selected_document_and_mature_evaluation(fault):
    v=scene();value=copy.deepcopy(v.sources)
    if fault=='no_document':value['document']=None
    if fault=='wrong_submission_set':value['document']['approved_selection']['source_set_hash']='0'*64
    if fault=='same_evidence':value['document']['evidence_ref']=copy.deepcopy(value['data_evidence_ref'])
    if fault=='wrong_head':value['mature_head']['head_hash']='0'*64
    with pytest.raises((ValidationError,ValueError)):
        s.check_source(rehash(value),preparation=v.preparation,manifest=v.manifest)


@pytest.mark.parametrize('fault',['mapping','plan_scope','hidden_task','hidden_task_upper','wrong_input_hash','invented_fact_source','missing_consent'])
def test_preview_does_not_expand_cloud_fields_or_replace_real_per_model_consent_pins(fault):
    v=scene();value=copy.deepcopy(v.preview);cloud=value['cloud_request']
    if fault=='mapping':cloud['mapping']={'private':'alias'}
    if fault=='plan_scope':cloud['decision_input']['scope']['analysis_plan_id']=v.preparation['scope']['analysis_plan_id']
    if fault=='hidden_task':cloud['question']+=v.preparation['scope']['task_id']
    if fault=='hidden_task_upper':cloud['question']+=v.preparation['scope']['task_id'].upper()
    if fault=='wrong_input_hash':cloud['input_hash']='0'*64
    if fault=='invented_fact_source':cloud['decision_input']['facts'][0]['source_refs'][0]['source_ref']='source_missing'
    if fault=='missing_consent':value['consent_pins'].pop()
    if fault!='wrong_input_hash':cloud['input_hash']=digest(cloud['decision_input'])
    value['cloud_request_hash']=digest(cloud)
    with pytest.raises((ValidationError,ValueError)):
        s.check_preview(rehash(value,'preview_hash'),preparation=v.preparation,source=v.sources,manifest=v.manifest)


def test_source_packet_union_is_closed_and_does_not_accept_caller_text():
    value={'kind':'document_packet_v207','packet':{'id':identity('packet'),'version':1,
        'evidence_id':identity('text'),'sha256':'a'*64,'source_set_id':identity('set'),'source_set_hash':'b'*64}}
    assert s.SelectedPacket.model_validate(value).packet.version==1
    with pytest.raises(ValidationError):s.SelectedPacket.model_validate({**value,'text':'caller invented excerpt'})


def test_original_opinions_are_kept_but_never_grant_dispatch_or_verified_results():
    v=scene();result=s.derive_result(v.execution,v.opinion,preparation=v.preparation,manifest=v.manifest)
    assert s.check_result(result,snapshot=v.execution,preparation=v.preparation,manifest=v.manifest)==result
    assert result['model_value']==v.opinion and all(result[key] is False for key in s.FLAGS)
    for field in s.FLAGS:
        with pytest.raises(ValueError):s.check_result({**result,field:0},snapshot=v.execution,preparation=v.preparation,manifest=v.manifest)
    malformed=copy.deepcopy(v.opinion);malformed['recommendation']['supporting_fact_refs']=['fact_invented']
    with pytest.raises(ValueError):s.derive_result(v.execution,malformed,preparation=v.preparation,manifest=v.manifest)
