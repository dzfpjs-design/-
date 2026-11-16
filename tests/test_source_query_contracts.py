"""V2 closed wire and honest deterministic material answers, no authority mock."""
import copy
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import source_query_contracts as c
from generic_agents import source_query_answer as a
from generic_agents.contracts import DataRequest


def scope():
    return {'instance_id':str(uuid4()),'release_id':str(uuid4()),'business_ids':['业务甲'],
        'entity_ids':[str(uuid4())],'batch_day':'2026-10-28','source_kinds':['supplement_captures'],
        'document_search_ids':[]}


def request():
    return {'schema':'data-query-create-v2','request_id':str(uuid4()),'question':'本次补采是否完整？',
        'expected_catalog_hash':'a'*64,'source_scope':scope(),'parent_query_id':None}


@pytest.mark.parametrize('change', [lambda v:v.update(mode='cloud'),lambda v:v.pop('parent_query_id'),
    lambda v:v.update(question='  '),lambda v:v.update(request_id=True),
    lambda v:v['source_scope'].update(sql='SELECT *'),lambda v:v['source_scope'].update(batch_day='2026-02-30'),
    lambda v:v['source_scope'].update(entity_ids=[]),lambda v:v['source_scope'].update(source_kinds=['supplement_captures']*2)])
def test_closed_source_scope_rejects_ambiguity_and_arbitrary_parameters(change):
    value=request();change(value)
    with pytest.raises(HTTPException) as error:c.parse(json.dumps(value).encode(),'create')
    assert error.value.status_code==422


def test_original_raw_semantics_keep_order_and_reject_duplicate_keys():
    value=request();value['source_scope']['business_ids']=['乙','甲']
    raw=json.dumps(value,ensure_ascii=False).encode()
    assert c.parse(raw,'create')==value
    with pytest.raises(HTTPException):c.parse(raw[:-1]+b',"question":"duplicate"}','create')
    with pytest.raises(HTTPException):c.parse(b'x'*(c.MAX_BODY+1),'create')
    legacy=DataRequest(question='核查字段',mode='structured').model_dump()
    assert 'source_scope' not in legacy and 'request_id' not in legacy and 'schema' not in legacy


def test_human_question_note_and_cancel_reason_keep_original_spacing():
    value=request();value['question']='  本批资料是否完整？\n'
    assert c.parse(json.dumps(value).encode(),'create')['question']==value['question']
    for operation,field in [('note','text'),('cancel','reason')]:
        command={'schema':'data-query-'+operation+'-v2','request_id':str(uuid4()),'version':1,
            field:'  人工解释或停止原因。\n'}
        assert c.parse(json.dumps(command).encode(),operation)[field]==command[field]


def bundle():
    ref={'key':'capture-one','kind':'capture','label':'本次真实片段','version_label':'固定回执',
        'task_id':str(uuid4()),'plan_id':str(uuid4()),'run_id':str(uuid4()),'source_path':None,'excerpt':None,
        'metadata_verified':True,'physical_recheck':'not_checked','limitations':['拍摄时间未知']}
    record={'key':'record-one','kind':'capture','label':'摄像头补充片段','business_ids':['业务甲'],
        'entity_ids':[str(uuid4())],'batch_day':'2026-10-28','received_at':None,'scene_observed_at':None,
        'scene_time_trust':'unknown','capture':{'status':'partial','requested_seconds':8,'actual_seconds':2.4,
            'reason':'capture_deadline'},'processing':{'status':'succeeded','frames_saved':3},
        'delivery':{'status':'received'},'references':[ref],'text':None,'limitations':[]}
    return {'schema':'material-source-bundle-v2','scope':scope(),'providers':[{'id':'capture','version':'1'}],
        'records':[record],'snapshot':{},'coverage':{'scan_complete':True,'records_checked':1,
            'limit':200,'truncated':False,'expected_sources_verified':False},'limitations':[]}


def test_partial_2_4_of_8_with_independent_ack_is_not_full_capture_or_current_scene():
    value=bundle();old=copy.deepcopy(value)
    result=a.execute_registered(a.TOOL,'为什么8秒补采未完成？',value,'2026-10-28T12:00:00+00:00')
    assert result['answer_status']=='partial' and result['intent']=='incomplete_capture'
    texts=' '.join(row['text'] for row in result['findings'])
    assert '2.4秒' in texts and '8秒' in texts and '独立接收' in texts
    assert 'partial' not in texts and 'succeeded' not in texts
    assert result['coverage']['expected_sources_verified'] is False
    assert all(row['reference_keys']==['capture-one'] for row in result['findings'])
    result['references'][0]['label']='changed'
    assert value==old


def test_batch_day_and_received_receipt_never_answer_scene_clock():
    value=bundle();value['records'][0]['received_at']='2026-10-28T08:00:00+08:00'
    result=a.answer('画面是哪天拍的？',value,'now')
    assert result['intent']=='time_provenance'
    assert '未核实' in result['findings'][0]['text'] and '不能代替' in result['findings'][0]['text']
    assert result['answer_status']=='partial'


def test_no_completed_search_is_explicitly_uncovered_not_document_has_no_content():
    result=a.answer('报告有什么要求？',bundle(),'now')
    assert result['answer_status']=='needs_clarification' and result['references']==[]
    assert '尚未覆盖' in result['summary'] and '没有内容' in result['summary']


@pytest.mark.parametrize('question',['判断物种数量是否下降','删除全部资料','推荐设备并马上执行'])
def test_unsupported_question_does_not_create_a_domain_or_action_answer(question):
    result=a.answer(question,bundle(),'now')
    assert result['intent']=='unsupported' and result['answer_status']=='unsupported'
    assert not result['findings']


def test_missing_actual_video_duration_is_unknown_not_zero():
    value=bundle();value['records'][0]['capture'].update(status='failed',actual_seconds=None)
    result=a.answer('采集是否完整？',value,'now')
    assert '没有可核实' in result['findings'][0]['text']
    assert '实际保存0秒' not in str(result)


def test_natural_overview_with_review_and_evidence_is_one_supported_user_job():
    result=a.answer('本批次资料是否完整，哪些仍需人工核对，依据是什么？',bundle(),'now')
    assert result['intent']=='receipt_overview' and result['answer_status']=='partial'
    codes={item['code'] for item in result['findings']}
    assert {'capture_status','processing_status','independent_receipt','scene_time_boundary','human_review_boundary'} <= codes
    assert all(item['reference_keys'] for item in result['findings'])
    assert '3张画面' in ' '.join(item['text'] for item in result['findings'])


def test_related_questions_select_different_facts_not_the_same_generic_list():
    value=bundle()
    captures=a.answer('本次补采是否完整？',value,'now')
    processing=a.answer('本次处理进度如何？',value,'now')
    evidence=a.answer('依据是什么？',value,'now')
    assert 'capture_status' in {item['code'] for item in captures['findings']}
    assert 'processing_status' not in {item['code'] for item in captures['findings']}
    assert 'processing_status' in {item['code'] for item in processing['findings']}
    assert 'capture_status' not in {item['code'] for item in processing['findings']}
    assert 'fixed_source' in {item['code'] for item in evidence['findings']}
    assert 'capture_status' not in {item['code'] for item in evidence['findings']}
    assert a.intent('这些资料处理到什么进度？')=='processing_status'
    assert a.intent('这些资料的出处是什么？')=='evidence_lookup'


def test_no_incomplete_marker_only_describes_selected_checked_records():
    value=bundle();value['records'][0]['capture'].update(status='captured',actual_seconds=8)
    result=a.answer('本次补采是否完整？',value,'now')
    finding=next(item for item in result['findings'] if item['code']=='no_incomplete_marker')
    assert '所选记录' in finding['text'] and '不能保证全量' in finding['text']
    assert finding['reference_keys'] and result['answer_status']=='partial'


@pytest.mark.parametrize('question',['现在着火了吗？','资料能说明树种长势如何？'])
def test_professional_state_inference_cannot_be_disguised_as_material_overview(question):
    result=a.answer(question,bundle(),'now')
    assert result['intent']=='unsupported' and not result['findings']


def document_bundle():
    value=bundle();record=value['records'][0]
    record.update(key='document-one',kind='document_excerpt',label='设计报告正文节选',capture=None,
        processing={'status':'parsed','frames_saved':None},text='摄像设备应保存采集片段及其接收回执。')
    record['references'][0].update(kind='document_excerpt',key='document-one:excerpt',excerpt=record['text'])
    value['snapshot']={'entries':[{'provider_id':'document_searches','source_ref':{'keyword':'摄像'},
        'record':copy.deepcopy(record)}]}
    return value


def test_specific_unsearched_document_condition_does_not_quote_unrelated_camera_hits_as_answer():
    value=document_bundle();before=copy.deepcopy(value)
    result=a.answer('报告中审批有什么要求？',value,'now')
    assert result['intent']=='document_conditions' and result['answer_status']=='needs_clarification'
    assert not result['findings'] and not result['references']
    assert '相应正文检索' in result['summary'] and '不表示报告没有' in result['summary']
    assert value==before


def test_matching_document_keyword_only_returns_fixed_excerpt_with_explicit_search_boundary():
    value=document_bundle();result=a.answer('报告中有哪些摄像相关要求？',value,'now')
    assert result['intent']=='document_conditions' and result['answer_status']=='answered'
    assert result['findings'][0]['text']==value['records'][0]['text']
    assert result['findings'][0]['reference_keys']==['document-one:excerpt']
    assert '所选正式搜索' in result['summary'] and '不等于完整报告' in result['summary']


def test_broad_report_question_is_selected_excerpt_view_not_successful_full_requirements_understanding():
    value=document_bundle();result=a.answer('这份报告有哪些要求？',value,'now')
    assert result['answer_status']=='partial' and result['references']
    assert '所选正式搜索' in result['summary']


def test_source_specific_record_date_cannot_be_promoted_to_document_creation_or_scene_time():
    value=document_bundle();value['records'][0]['batch_day']='2026-10-20'
    result=a.answer('资料的拍摄日期是什么时候？',value,'now')
    assert result['intent']=='time_provenance' and result['answer_status']=='partial'
    assert '文档版本登记日期为2026-10-20' in result['findings'][0]['text']
    assert '不是文档形成日期或现场拍摄时间' in result['findings'][0]['text']


@pytest.mark.parametrize('question',['哪些没读满？','还有哪些资料未读满？','读满了吗？'])
def test_common_capture_completeness_questions_do_not_require_internal_intent_terms(question):
    result=a.answer(question,bundle(),'now')
    assert result['intent']=='incomplete_capture' and any('2.4秒' in item['text'] for item in result['findings'])


@pytest.mark.parametrize('question',['是今天拍的吗？','画面是当天拍的吗？','什么时候拍的？'])
def test_common_scene_date_questions_stay_unknown_instead_of_using_received_date(question):
    result=a.answer(question,bundle(),'now')
    assert result['intent']=='time_provenance' and result['answer_status']=='partial'
    assert '未核实' in result['findings'][0]['text'] and '不能代替' in result['findings'][0]['text']
