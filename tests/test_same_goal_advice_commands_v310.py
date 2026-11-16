"""Lost-response receipts run real local store/audit readers with explicit IO.

These tests prove command identity, not PostgreSQL, current source, paid HTTP
or browser persistence. The retained original preparation fixture declares its
scope/actor IO boundaries; actual Plan, Job and Audit inventory logic runs.
"""
import copy
from contextlib import nullcontext

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_commands_v310 as s
from generic_agents.contracts import digest
from tests.test_same_goal_advice_store_v302 import scene as store_scene
from tests.test_same_goal_advice_contracts_v302 import ref


@pytest.fixture
def scene(monkeypatch):
    t=store_scene.__wrapped__(monkeypatch)
    t.db.no_autoflush=nullcontext()
    t.first=t.prepare();t.plan=t.rows[s.Plan][0]
    t.read=lambda nonce=None,**kw:s.read(t.db,t.p,t.task.id,nonce or t.body['request_id'],**kw)
    return t


def test_prepare_receipt_proves_saved_actor_and_original_body_without_second_operation(scene):
    t=scene;before=copy.deepcopy(t.plan.data);count=len(t.rows[s.Audit])
    receipt=t.read()
    assert receipt['schema']==s.SCHEMA and receipt['found'] is True
    assert receipt['operation']=='prepare' and receipt['task_id']==t.task.id
    assert receipt['plan_id']==t.plan.id and receipt['actor_id']==t.p.user_id
    assert receipt['body_hash']==digest(t.body) and receipt['result']==t.first
    assert receipt['result_status']=='available'
    assert t.read()==receipt and t.plan.data==before and len(t.rows[s.Audit])==count


def test_cancel_receipt_uses_its_actual_nonce_actor_and_reason(scene):
    t=scene;body={'request_id':ref('cancel_ui_original')['id'],'expected_plan_version':t.plan.version,
        'reason':'取消本次整理，保留已保存的原资料'}
    result=s.store.cancel(t.db,t.p,t.task.id,t.plan.id,body)
    receipt=t.read(body['request_id'])
    assert receipt['operation']=='cancel' and receipt['request_id']==body['request_id']
    assert receipt['body_hash']==digest(body) and receipt['result']==result
    assert t.read()['operation']=='prepare'


def test_missing_original_remains_missing_and_creates_no_replacement(scene):
    t=scene;count=len(t.rows[s.Audit])
    with pytest.raises(HTTPException) as caught:t.read(ref('not_yet_saved')['id'])
    assert caught.value.status_code==404 and len(t.rows[s.Audit])==count


@pytest.mark.parametrize('fault',['plan','operation'])
def test_specialized_receipt_cannot_resolve_another_plan_or_command(scene,fault):
    t=scene
    with pytest.raises(HTTPException) as caught:
        t.read(**({'plan_id':ref('other_plan')['id']} if fault=='plan' else {'operation':'start'}))
    assert caught.value.status_code==409


def test_saved_command_survives_unavailable_result_without_a_fake_success_value(scene,monkeypatch):
    t=scene
    def broken(*args):raise HTTPException(409,'original result proof unavailable')
    monkeypatch.setattr(s.store,'_view',broken)
    receipt=t.read()
    assert receipt['found'] is True and receipt['body_hash']==digest(t.body)
    assert receipt['result'] is None and receipt['result_status']=='needs_check'


def test_saved_command_keeps_exact_receipt_when_financial_original_blocks_result(scene,monkeypatch):
    t=scene;before=copy.deepcopy(t.plan.data);count=len(t.rows[s.Audit])
    def blocked(*args):raise s.ModelBlocked('original HOLD/INTENT proof differs')
    monkeypatch.setattr(s.store,'_view',blocked)
    receipt=t.read()
    assert receipt['found'] is True and receipt['actor_id']==t.p.user_id
    assert receipt['body_hash']==digest(t.body) and receipt['request_id']==t.body['request_id']
    assert receipt['result'] is None and receipt['result_status']=='needs_check'
    assert t.plan.data==before and len(t.rows[s.Audit])==count


def test_financial_block_before_command_custody_is_not_a_positive_receipt(scene,monkeypatch):
    t=scene
    def blocked(*args):raise s.ModelBlocked('unproven original command inventory')
    monkeypatch.setattr(s.store,'_saved_base',blocked)
    with pytest.raises(s.ModelBlocked):t.read()


@pytest.mark.parametrize('code',[401,403,500,503])
def test_authentication_and_operational_errors_are_not_hidden_as_valid_result_state(scene,monkeypatch,code):
    t=scene
    def refused(*args):raise HTTPException(code,'explicit IO refused')
    monkeypatch.setattr(s.store,'_view',refused)
    with pytest.raises(HTTPException) as caught:t.read()
    assert caught.value.status_code==code


def test_positive_orphan_audit_is_not_a_false_not_found(scene):
    t=scene;nonce=ref('lost_positive_receipt')['id']
    event=s.Audit(id=ref('orphan_command_audit')['id'],workspace_id=t.p.workspace_id,
        actor_id=t.p.user_id,action=s.execution.STARTED,object_id=ref('missing_plan')['id'],
        data={'task_id':t.task.id,'request':{'request_id':nonce}})
    t.rows[s.Audit].append(event)
    with pytest.raises(HTTPException) as caught:t.read(nonce)
    assert caught.value.status_code==409


def test_current_reader_is_required_before_any_original_is_exposed(scene,monkeypatch):
    t=scene
    def refused(*args,**kwargs):raise HTTPException(403,'membership revoked')
    monkeypatch.setattr(s.store,'_reader',refused)
    with pytest.raises(HTTPException) as caught:t.read()
    assert caught.value.status_code==403
