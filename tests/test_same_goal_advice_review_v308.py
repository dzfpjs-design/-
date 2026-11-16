"""Human review storage contract; result/PG/auth IO boundaries are explicit."""
import copy
from datetime import datetime,timezone

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_review_v308 as s
from generic_agents import same_goal_advice_runtime_v308 as runtime
from generic_agents.contracts import digest
from tests.test_same_goal_advice_runtime_v308 import scene as runtime_scene
from tests.test_same_goal_advice_contracts_v302 import ref

_actual_fresh=s.store._fresh


@pytest.fixture
def scene(monkeypatch):
    t=runtime_scene.__wrapped__(monkeypatch)
    t.result={'actual_result_IO':'original_answer','can_dispatch':False}
    t.plan.status='ready_for_review';t.send_job.status='succeeded';t.send_job.lease_until=None
    t.plan.data={**t.plan.data,s.store.RESULT_KEY:{'result_ref':{'id':t.call.id,'record_hash':digest(t.result)},
        'ready_audit_ref':ref('original_result_ready_audit')}}
    t.body={'request_id':ref('human_review_nonce')['id'],'expected_plan_version':t.plan.version,
        'result_hash':digest(t.result),'decision':'accepted','reason':'对照原事实与资料审核后采用此建议'}
    monkeypatch.setattr(s,'lock_current_actor',lambda *args:None)
    monkeypatch.setattr(s.store,'_saved',lambda db,p,task,plan:(t.prep,t.job))
    monkeypatch.setattr(s.store,'_fresh',_actual_fresh)
    def cold(db,p,task,plan):
        assert t.send_job.status=='succeeded' and t.send_job.lease_until is None
        return copy.deepcopy(t.result)
    monkeypatch.setattr(runtime,'cold_result',cold)
    original_add=t.db.add
    def add(row):
        if row.created_at is None:row.created_at=datetime.now(timezone.utc)
        original_add(row)
    t.db.add=add
    t.review=lambda body=None:s.review(t.db,t.p,t.task.id,t.plan.id,body or t.body)
    return t


@pytest.mark.parametrize('decision',['accepted','rejected','needs_revision'])
def test_review_keeps_original_answer_and_saves_one_human_receipt(scene,decision):
    t=scene;t.body['decision']=decision
    original=copy.deepcopy(t.call.data);version=t.plan.version
    result=t.review()
    assert result['decision']==decision and result['can_dispatch'] is False
    assert result['goal_completion_verified'] is False
    assert t.plan.status=='reviewed' and t.plan.version==version+1
    assert t.call.data==original and t.commits==0
    assert t.review()==result and len([a for a in t.rows[s.Audit] if a.action==s.ACTION])==1


@pytest.mark.parametrize('change',['hash','version','status','permission'])
def test_stale_or_unauthorized_review_does_not_save(scene,change):
    t=scene
    if change=='hash':t.body['result_hash']='0'*64
    elif change=='version':t.body['expected_plan_version']-=1
    elif change=='status':t.plan.status='generating'
    elif change=='permission':t.allow_reader=False
    before=copy.deepcopy(t.plan.data)
    with pytest.raises(HTTPException):t.review()
    assert t.plan.data==before and not [a for a in t.rows[s.Audit] if a.action==s.ACTION]


def test_saved_review_is_not_overwritten_by_second_decision(scene):
    t=scene;original=t.review()
    with pytest.raises(HTTPException):t.review({**t.body,'decision':'rejected'})
    assert s.checked(t.db,t.p,t.task,t.plan,t.prep)==[original]


def test_result_integrity_failure_prevents_review(scene,monkeypatch):
    t=scene
    def broken(*args):raise HTTPException(409,'actual paid answer receipt IO damaged')
    monkeypatch.setattr(runtime,'cold_result',broken)
    with pytest.raises(HTTPException):t.review()
    assert not t.plan.data['reviews'] and t.plan.status=='ready_for_review'


def test_review_receipt_cannot_be_deleted_to_reopen_decision(scene):
    t=scene;t.review();t.plan.data={**t.plan.data,'reviews':[]};t.plan.status='ready_for_review'
    with pytest.raises(HTTPException):s.checked(t.db,t.p,t.task,t.plan,t.prep)
