"""Closed opt-in command and literal planning; no sources or authority mocked."""
import copy
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import document_question_contracts as wire,document_question_service as service
from generic_agents import source_query_contracts as old


def request():
    return {'schema':'document-question-create-v1','request_id':str(uuid4()),'question':'报告对摄像有哪些要求？',
        'expected_catalog_hash':'a'*64,'selection':{'instance_id':str(uuid4()),'release_id':str(uuid4()),
            'business_ids':['业务乙','业务甲'],'document_version_id':str(uuid4()),'parse_attempt_id':str(uuid4()),
            'raw_sha256':'b'*64,'parse_hash':'c'*64},'parent_query_id':None}


@pytest.mark.parametrize('change',[lambda x:x.update(keyword='审批'),lambda x:x.update(model_profile_id=str(uuid4())),
    lambda x:x['selection'].update(source={'status':'parsed'}),lambda x:x['selection'].update(business_ids=[]),
    lambda x:x['selection'].update(parse_hash=None),lambda x:x.update(question='  '),
    lambda x:x.update(request_id=True),lambda x:x.pop('parent_query_id')])
def test_closed_automatic_command_never_admits_search_inputs_or_source_facts(change):
    value=request();change(value)
    with pytest.raises(HTTPException) as error:wire.parse(json.dumps(value).encode())
    assert error.value.status_code==422


def test_exact_original_text_and_selection_order_remain_and_old_post_stays_closed():
    value=request();value['question']='  报告对摄像有哪些要求？\n';raw=json.dumps(value,ensure_ascii=False).encode()
    assert wire.parse(raw)==value
    with pytest.raises(HTTPException):wire.parse(raw[:-1]+b',"question":"duplicate"}')
    with pytest.raises(HTTPException):old.parse(raw,'create')
    with pytest.raises(HTTPException) as error:wire.parse(b'x'*(old.MAX_BODY+1))
    assert error.value.status_code==413


@pytest.mark.parametrize('question,keyword',[('报告对摄像有哪些要求？','摄像'),('报告中审批有什么条件？','审批'),
    ('报告有哪些要求？',None),('报告对摄像和审批有哪些要求？',None),('现在着火了吗？',None)])
def test_local_single_literal_planning_does_not_choose_one_of_several_topics(question,keyword):
    assert service.planner(question)=={'id':'literal-report-keyword','version':'1','keyword':keyword}


def test_three_nonce_roles_are_stable_distinct_and_bound_to_original_question_and_selection():
    value=request();parent=service._derived(value['request_id'],'parent',value)
    basis={'selection_hash':'d'*64,'planner':service.planner(value['question'])}
    child=service._derived(value['request_id'],'search',basis)
    assert len({value['request_id'],parent,child})==3
    assert parent==service._derived(value['request_id'],'parent',copy.deepcopy(value))
    changed=copy.deepcopy(value);changed['question']='报告对审批有哪些要求？'
    assert parent!=service._derived(value['request_id'],'parent',changed)
    assert child!=service._derived(value['request_id'],'search',{**basis,'selection_hash':'e'*64})
