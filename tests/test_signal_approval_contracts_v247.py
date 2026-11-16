"""Closed original commands and private handoff boundary, no IO acceptance."""
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from generic_agents.signal_approval_contracts_v247 import Enable,Pause,Retry
from generic_agents import signal_approval_routing_v247 as service


def enable(**changes):
    return {'schema':'signal-approval-enable-v247','request_id':str(uuid4()),'expected_version':1,
        'release_id':str(uuid4()),'source_id':str(uuid4()),'extractor_id':str(uuid4()),
        'entity_ids':[str(uuid4())],'business_id':'business_1234567890abcdef','valid_for_hours':24,
        'max_tasks':10,'reason':'明确只建立审批草稿，仍由人逐级核对并决定启动','options_hash':'a'*64,
        'confirm_approval_drafts':True}|changes


@pytest.mark.parametrize('changes',[{'valid_for_hours':0},{'valid_for_hours':25},{'valid_for_hours':True},
    {'valid_for_hours':1.0},{'max_tasks':True},{'max_tasks':0},{'max_tasks':101},{'expected_version':True},
    {'expected_version':'1'},{'confirm_approval_drafts':False},{'confirm_approval_drafts':1},
    {'confirm_approval_drafts':'true'},{'reason':'  '},{'reason':'\x00'},{'source_contract':{}},
    {'approved':True},{'observation_id':str(uuid4())},{'signal_approval_handoff':{}},
    {'entity_ids':[]},{'entity_ids':[str(uuid4())]*21}])
def test_command_has_no_type_coercion_or_public_source_approval_bypass(changes):
    with pytest.raises(ValidationError):Enable.model_validate(enable(**changes))


def test_command_schema_and_unique_explicit_entities_are_required():
    body=enable();body.pop('schema')
    with pytest.raises(ValidationError):Enable.model_validate(body)
    body=enable();body['entity_ids']*=2
    with pytest.raises(ValidationError):Enable.model_validate(body)
    assert Enable.model_validate(enable()).model_dump(mode='json')['confirm_approval_drafts'] is True


def test_original_whitespace_bytes_and_model_hash_are_distinct_and_not_rewritten():
    body=enable();raw=json.dumps(body,ensure_ascii=False,indent=2)+'\n'
    decoded=service.parse_raw('enable',raw)
    assert decoded==body and service.raw_hash(raw)!=service.raw_hash(raw.rstrip())
    assert service.raw_hash(raw)!=service.digest(decoded)
    assert raw.endswith('\n')


@pytest.mark.parametrize('replacement',['"expected_version": true','"expected_version": 1.0',
    '"expected_version": 1, "expected_version": 2'])
def test_raw_duplicate_and_ambiguous_versions_are_rejected(replacement):
    raw=json.dumps(enable()).replace('"expected_version": 1',replacement)
    with pytest.raises((HTTPException,ValidationError)):service.parse_raw('enable',raw)


def test_arbitrary_private_token_or_json_cannot_open_normal_newtask_seam():
    db=object();p=NS();instance=NS();release=NS()
    for token in [None,True,{},NS(secret=service._SECRET,session=db),
            service._Handoff(object(),db,str(uuid4()),'a'*64)]:
        with pytest.raises(HTTPException) as failure:
            service.validate_handoff(db,p,token,NS(),instance,release)
        assert failure.value.status_code==409


def test_pause_and_retry_keep_different_closed_schemas():
    body={'schema':'signal-approval-pause-v247','request_id':str(uuid4()),'expected_version':1,'reason':'先暂停检查'}
    assert Pause.model_validate(body).model_dump(mode='json')==body
    with pytest.raises(ValidationError):Retry.model_validate(body)
