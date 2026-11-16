"""Provided text review must never imply whole-document or action authority."""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents import same_goal_advice_source_scope_v309 as s
from generic_agents.contracts import digest
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene
from tests import test_same_goal_advice_runtime_v308 as rt


def example():
    v=contract_scene();cloud=copy.deepcopy(v.preview['cloud_request']);value=copy.deepcopy(v.opinion)
    source=cloud['decision_input']['sources'][0];source['readability']='partial'
    assert source['text'].strip()
    next(row for row in value['source_reviews'] if row['source_ref']==source['source_ref'])['assessment']='reviewed'
    cloud['input_hash']=digest(cloud['decision_input'])
    return cloud,value


def test_partial_review_preserves_model_value_and_explicitly_limits_scope():
    cloud,value=example();before=copy.deepcopy(value)
    assert c.check_model_value(cloud['decision_input'],value)==value
    record=s.record(cloud,value,capture_sha256='a'*64)
    assert record['sources'][0]['review_scope']=='provided_excerpt_only'
    assert record['input_hash']==cloud['input_hash'] and record['model_value_hash']==digest(value)
    assert record['capture_sha256']=='a'*64 and record['record_hash']==c.record_hash(record)
    assert all(record[name] is False for name in ('full_document_verified','source_authenticity_verified','can_dispatch'))
    assert all(row['full_document_verified'] is False and row['source_authenticity_verified'] is False for row in record['sources'])
    assert value==before


@pytest.mark.parametrize('readability,text',[('unreadable','text'),('readable',None),('partial',None),
    ('readable',''),('partial',' \n\t'),('readable',123),('partial',b'text')])
def test_reviewed_requires_actual_nonempty_readable_supplied_text(readability,text):
    cloud,value=example();source=cloud['decision_input']['sources'][0]
    source.update(readability=readability,text=text)
    with pytest.raises(ValueError):s.provided_scopes(cloud['decision_input'],value)


@pytest.mark.parametrize('assessment',['unreadable','incomparable'])
def test_unreviewed_source_is_never_reported_as_reviewed_excerpt(assessment):
    cloud,value=example();value['source_reviews'][0]['assessment']=assessment
    assert s.provided_scopes(cloud['decision_input'],value)[0]['review_scope']=='not_reviewed'


@pytest.mark.parametrize('fault',['duplicate','missing','unknown','input_hash','capture_hash'])
def test_scope_cannot_cover_another_input_capture_or_source_inventory(fault):
    cloud,value=example()
    if fault=='duplicate':value['source_reviews'].append(copy.deepcopy(value['source_reviews'][0]))
    elif fault=='missing':value['source_reviews'].pop()
    elif fault=='unknown':value['source_reviews'][0]['source_ref']='unprovided_source'
    elif fault=='input_hash':cloud['input_hash']='f'*64
    with pytest.raises(ValueError):s.record(cloud,value,capture_sha256='invalid' if fault=='capture_hash' else 'a'*64)


def test_explicit_legacy_rule_still_refuses_partial_reviewed():
    cloud,value=example()
    with pytest.raises(ValueError):c.check_model_value_original_v302(cloud['decision_input'],value)


def test_ready_scope_binds_actual_capture_and_cannot_be_downgraded_to_legacy(monkeypatch):
    t=rt.scene.__wrapped__(monkeypatch);original_opinion=rt._opinion
    def model(cloud):
        value=original_opinion(cloud)
        for source,row in zip(cloud['sources'],value['source_reviews']):
            if source['readability']=='partial':
                row['assessment']='reviewed';row['reason']='Reviewed the provided excerpt only; the whole document remains unverified.'
        return value
    monkeypatch.setattr(rt,'_opinion',model)
    result=rt._succeeded(t)
    assert rt.s.process(t.db,t.p,t.send_job)=='succeeded'
    t.send_job.status='succeeded';t.send_job.lease_until=None
    assert rt.s.cold_result(t.db,t.p,t.task,t.plan)==result
    event=next(row for row in t.rows[rt.s.Audit] if row.action==rt.s.RESULT_READY)
    scope=event.data['source_review_scope']
    assert scope['capture_sha256']==t.call.data[rt.s.STATE_KEY]['capture']['raw_sha256']
    assert scope['model_value_hash']==digest(result['model_value'])
    assert any(row['review_scope']=='provided_excerpt_only' for row in scope['sources'])
    assert all(result[name] is False for name in c.FLAGS)
    old={name:value for name,value in event.data.items() if name!='source_review_scope'}
    old['record_hash']=c.record_hash(old);event.data=old
    t.plan.data={**t.plan.data,rt.s.RESULT_KEY:rt.s._marker(event,old)}
    with pytest.raises(HTTPException):rt.s.cold_result(t.db,t.p,t.task,t.plan)
