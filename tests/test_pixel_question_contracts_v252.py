"""Pure contracts and fixed-manifest boundaries. No paid/cloud/DB claim."""
import copy,json
from pathlib import Path
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from generic_agents.contracts import digest
from generic_agents.services import validate
from generic_agents import pixel_question_contracts_v252 as c
from generic_agents import pixel_question_catalog_v252 as catalog

ROOT=Path(__file__).resolve().parents[1]

def fixture():return json.loads((ROOT/'.agentic-engineering/fixtures/pixel-question-v252.json').read_text())

def scope():
    f=fixture();return {'instance_id':f['options']['instance_id'],'authorization_release_id':f['submit']['release_id'],'task_id':None,'entity_id':None}

def test_public_fixture_full_typed_body_preserves_unicode_nonce_and_hashes():
    f=fixture();body=c.Submit.model_validate(f['submit']);assert '/' in body.request_id and '?' in body.request_id
    assert digest(body.model_dump(mode='json'))==f['query']['request_hash']
    assert c.preview_input(body)==f['preview_input']
    assert digest({k:v for k,v in f['options'].items() if k!='options_hash'})==f['options']['options_hash']
    assert digest({k:v for k,v in f['preview'].items() if k!='preview_hash'})==f['preview']['preview_hash']
    assert set(f['list']['records'][0])=={'id','version','status','created_at','question','scope','model_profile_id','has_saved_answer','has_saved_plan'}

@pytest.mark.parametrize('field,value',[('business_id','invented-business'),('confirm_cloud',1),('request_id','short'),('model_profile_id','model-name'),('question',' ')])
def test_typed_submit_rejects_unapproved_or_ambiguous_inputs(field,value):
    f=fixture()['submit'];f[field]=value
    with pytest.raises(ValidationError):c.Submit.model_validate(f)

def test_fixed_manifest_is_complete_and_cannot_change_source_or_handler():
    fixed=catalog.manifest().model_dump(mode='json');catalog.validate_manifest(fixed)
    assert digest(fixed['input_schema'])==digest(c.tool_input_schema())
    assert digest(fixed['output_schema'])==digest(c.answer_schema())
    assert fixed['execution']['side_effect'] is False and fixed['execution']['query_tool'] is True
    for field,value in [('handler','data_record_aggregate'),('side_effect',True)]:
        changed=copy.deepcopy(fixed);changed['execution'][field]=value
        with pytest.raises(HTTPException):catalog.validate_manifest(changed)

def test_candidate_has_no_model_scope_and_server_injects_original_scope():
    f=fixture();result=c.validate_intent(f['model_candidate'],f['submit']['question'],scope())
    assert result['scope']==scope() and result['operation']=='count'
    forged=copy.deepcopy(f['model_candidate']);forged['ir']['scope']=scope()
    with pytest.raises(HTTPException):c.validate_intent(forged,f['submit']['question'],scope())

@pytest.mark.parametrize('mutation',[lambda x:x['ir']['grounding'].update(operation_quote='不是原问题'),
    lambda x:x['ir'].update(metric='candidate_pixels'),lambda x:x['ir'].update(group_by='entity_id'),
    lambda x:x['ir']['filters'].update(human_completion='has_completed_receipt')])
def test_ir_rejects_hallucinated_operation_metric_group_or_human_filter(mutation):
    f=fixture();v=copy.deepcopy(f['model_candidate']);mutation(v)
    # The human filter still has a reception quote; it must not be enough to
    # ground a completely different requested human completion condition.
    with pytest.raises((ValueError,HTTPException)):c.validate_intent(v,f['submit']['question'],scope())

@pytest.mark.parametrize('question',['今天有多少条影像成果？','这些候选变化的面积是多少亩？','现场火灾有多少处？'])
def test_unavailable_time_area_and_field_truth_are_never_inferred(question):
    v=copy.deepcopy(fixture()['model_candidate']);v['ir']['grounding']={'operation_quote':'多少','metric_quote':None,'time_quote':None,'filter_quotes':[]};v['ir']['filters']['reception']='any'
    with pytest.raises(ValueError):c.validate_intent(v,question,scope(),reference_day='2026-10-30')

def test_strict_response_union_never_accepts_answer_or_sql_fields():
    v=copy.deepcopy(fixture()['model_candidate']);v['answer']=27
    with pytest.raises(HTTPException):validate(c.model_result_schema(),v)
    v=copy.deepcopy(fixture()['model_candidate']);v['ir']['sql']='SELECT anything'
    with pytest.raises(HTTPException):validate(c.model_result_schema(),v)

def test_unknown_recovery_does_not_claim_unexecuted_or_invent_query():
    f=fixture()['recovery_unknown'];assert f['state']=='unknown' and f['query'] is None and f['found'] is False

def test_original_three_tool_catalog_manifests_are_not_replaced_by_the_facet():
    from generic_agents.native_query_catalog import manifests,METHODS
    fixed=manifests();assert len(fixed)==3 and METHODS==['keyword','aggregate','fulltext']
    assert all(not catalog.applies(v.model_dump(mode='json')) for v in fixed)


def language_candidate(question,*,operation='count',metric=None,operation_quote='统计',metric_quote=None,reception='any',filter_quotes=None):
    result=copy.deepcopy(fixture()['model_candidate'])
    result['ir'].update(operation=operation,metric=metric)
    result['ir']['filters']={'reception':reception,'human_completion':'any'}
    result['ir']['grounding']={'operation_quote':operation_quote,'metric_quote':metric_quote,
        'time_quote':None,'filter_quotes':filter_quotes or []}
    return result


def test_exact_waiting_question_accepts_statistics_word_and_no_default_registration_day():
    question='统计本任务等待数据管理智能体接收的影像分析成果数量。'
    value=language_candidate(question,reception='waiting_data',filter_quotes=['等待数据管理智能体接收'])
    plan=c.validate_intent(value,question,scope(),reference_day='2026-10-30')
    assert plan['operation']=='count' and plan['metric'] is None
    assert plan['time']['registration_days'] is None and plan['grounding']['time_quote'] is None
    assert plan['scope']==scope() and plan['filters']['reception']=='waiting_data'


def test_task_scope_phrase_never_grounds_invented_registration_day():
    question='统计本任务等待数据管理智能体接收的影像分析成果数量。'
    value=language_candidate(question,reception='waiting_data',filter_quotes=['等待数据管理智能体接收'])
    value['ir']['time']['registration_days']=['2026-10-30'];value['ir']['grounding']['time_quote']='本任务'
    original=copy.deepcopy(value)
    with pytest.raises(ValueError,match='登记日期'):c.validate_intent(value,question,scope(),reference_day='2026-10-30')
    assert value==original  # no correcting or rewriting the saved model reply


@pytest.mark.parametrize('question,quote',[
    ('统计有几份候选变化像素分析成果。','统计'),
    ('统计候选变化像素分析成果的数量。','统计'),
    ('候选变化像素分析成果共有多少份？','多少'),
    ('候选像素分析报告目前一共有几份？','几份'),
    ('已接收的候选像素分析报告数量是多少？','数量')])
def test_report_count_is_allowed_even_when_report_name_mentions_pixels(question,quote):
    value=language_candidate(question,operation_quote=quote,reception='received' if '已接收' in question else 'any',
        filter_quotes=['已接收'] if '已接收' in question else [])
    plan=c.validate_intent(value,question,scope())
    assert plan['operation']=='count' and plan['metric'] is None


@pytest.mark.parametrize('question,quote',[
    ('候选变化像素总数有多少？','多少'),
    ('统计候选变化像素数量。','统计'),
    ('候选变化像素一共有多少？','多少'),
    ('统计有多少候选变化像素。','统计'),
    ('统计候选变化像素总数和分析成果数量。','统计'),
    ('总共多少候选像素的成果份数？','多少')])
def test_pixel_quantity_cannot_be_disguised_as_a_metric_null_report_count(question,quote):
    value=language_candidate(question,operation_quote=quote)
    with pytest.raises(ValueError):c.validate_intent(value,question,scope())


@pytest.mark.parametrize('word',['汇总','累计','加总','总量'])
def test_sum_aliases_still_require_actual_pixel_metric_and_received_only(word):
    question=word+'本任务已接收成果的候选像素总量。'
    value=language_candidate(question,operation='sum',metric='candidate_pixels',operation_quote=word,
        metric_quote='候选像素',reception='received',filter_quotes=['已接收'])
    plan=c.validate_intent(value,question,scope())
    assert plan['operation']=='sum' and plan['metric']=='candidate_pixels' and plan['filters']['reception']=='received'
    wrong=copy.deepcopy(value);wrong['ir']['filters']['reception']='waiting_data'
    with pytest.raises(ValueError,match='只使用已接收'):c.validate_intent(wrong,question,scope())


def test_record_quantity_is_not_silently_replaced_by_a_pixel_sum():
    question='汇总有几份候选变化像素分析成果。'
    value=language_candidate(question,operation='sum',metric='candidate_pixels',operation_quote='汇总',metric_quote='候选变化像素',reception='received')
    with pytest.raises(ValueError):c.validate_intent(value,question,scope())


def test_planning_examples_are_closed_valid_and_scope_labels_are_not_dates():
    from generic_agents.pixel_question_service_v252 import _planning_examples,_messages
    examples=_planning_examples()
    for row in examples:
        validate(c.model_result_schema(),row['response'])
        if row['response']['status']=='candidate':
            plan=c.validate_intent(row['response'],row['question'],scope(),reference_day='2026-10-30')
            assert plan['time']['registration_days'] is None and plan['grounding']['time_quote'] is None
    f=fixture();messages=_messages({**f['submit'],'instance_id':f['options']['instance_id']},f['options']['catalog'],'2026-10-30')
    rules=json.loads(messages[0]['content'])
    assert any('registration_days必须为null' in rule for rule in rules['rules'])
    assert any('本任务' in rule and '绝不是日期' in rule for rule in rules['rules'])
    assert rules['examples']==examples


def test_unreceived_pixels_are_unknown_not_a_successful_zero_sum():
    # Pure synthetic metadata tests arithmetic/schema only; native PG separately
    # proves real producer/ACK. This does not fabricate a production receipt.
    from tests.test_pixel_question_sources_v252 import snapshot,record
    from generic_agents.pixel_question_sources_v252 import compute
    frozen=snapshot([record(state='waiting_data')])
    question='累计已接收成果的候选像素总量。'
    value=language_candidate(question,operation='sum',metric='candidate_pixels',operation_quote='累计',metric_quote='候选像素',
        reception='received',filter_quotes=['已接收'])
    selected={'instance_id':frozen['instance_id'],'authorization_release_id':frozen['records'][0]['producer_release_id'],
        'task_id':None,'entity_id':None}
    plan=c.validate_intent(value,question,selected)
    result=compute(frozen,plan)
    assert result['state_counts']['registered_waiting']==1 and result['state_counts']['received_results']==0
    assert result['coverage']['matched_records']==0 and result['references']==[]
    assert result['groups'][0]['value_text'] is None and result['groups'][0]['numerator'] is None
    assert result['groups'][0]['value_kind']=='none' and 'no_received_matching_results' in result['limitations']


@pytest.mark.parametrize('operation,metric,quote,metric_quote',[
    ('count',None,'统计',None),('sum','candidate_pixels','累计','候选像素')])
def test_single_ir_never_silently_answers_half_of_record_count_and_pixel_total(operation,metric,quote,metric_quote):
    question='统计分析成果数量和候选像素的累计总和。'
    value=language_candidate(question,operation=operation,metric=metric,operation_quote=quote,metric_quote=metric_quote,
        reception='received' if metric else 'any')
    with pytest.raises(ValueError,match='分开提问'):c.validate_intent(value,question,scope())

@pytest.mark.parametrize('question',[
    '累计等待数据管理智能体接收的候选像素总量。',
    '累计未接收成果的候选像素总量。',
    '累计候选像素总量。'])
def test_numeric_ir_cannot_change_waiting_or_unspecified_original_scope_to_received(question):
    value=language_candidate(question,operation='sum',metric='candidate_pixels',operation_quote='累计',
        metric_quote='候选像素',reception='received')
    original=copy.deepcopy(value)
    with pytest.raises(ValueError,match='接收'):c.validate_intent(value,question,scope())
    assert value==original


def test_explicit_completed_data_agent_reception_is_a_valid_received_phrase():
    question='累计已经由数据管理智能体接收成果的候选像素总量。'
    value=language_candidate(question,operation='sum',metric='candidate_pixels',operation_quote='累计',metric_quote='候选像素',
        reception='received',filter_quotes=['已经由数据管理智能体接收'])
    plan=c.validate_intent(value,question,scope())
    assert plan['filters']['reception']=='received' and plan['operation']=='sum'


def test_explicit_received_pixel_total_accepts_total_count_word_as_sum_alias():
    question='请汇总本任务已接收的影像分析成果中，候选变化像素的总数。'
    value=language_candidate(question,operation='sum',metric='candidate_pixels',operation_quote='总数',
        metric_quote='候选变化像素',reception='received',filter_quotes=['已接收'])
    plan=c.validate_intent(value,question,scope())
    assert plan['operation']=='sum' and plan['metric']=='candidate_pixels'
    assert plan['filters']['reception']=='received' and plan['time']['registration_days'] is None


def test_report_total_count_remains_count_with_no_pixel_metric():
    question='统计本任务已接收影像分析成果的总数。'
    value=language_candidate(question,operation_quote='总数',reception='received',filter_quotes=['已接收'])
    plan=c.validate_intent(value,question,scope())
    assert plan['operation']=='count' and plan['metric'] is None


def test_pixel_total_count_word_never_allows_metric_null_report_count():
    question='请汇总本任务已接收的影像分析成果中，候选变化像素的总数。'
    value=language_candidate(question,operation_quote='总数',reception='received',filter_quotes=['已接收'])
    with pytest.raises(ValueError,match='像素数量'):c.validate_intent(value,question,scope())
