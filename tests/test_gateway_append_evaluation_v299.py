"""Real gateway routing with explicit SQL/approval/fence adapter IO spies.

Not a real command/lease/SDK/query/evaluation persistence proof. Dedicated
PG and Root-owned original Task acceptance supply those stronger layers.
"""
import copy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import gateway, workflow_graph_scope, layered_approval_store_v175
from generic_agents import workflow_goal_evaluation_append_v299 as append
from tests.test_gateway_local_goal_evaluation_diagnostics_v293 import scene,invoke,identity


def append_scene(monkeypatch,*,failure=None):
    s=scene(monkeypatch,None)
    s.approval.update(schema='goal-evaluation-append-approval-v297',command_ref={'id':identity(),'record_hash':'b'*64})
    monkeypatch.setattr(workflow_graph_scope,'owned_graph_job',lambda *a:None)
    monkeypatch.setattr(layered_approval_store_v175,'before_workflow_dispatch',lambda *a:pytest.fail('append cannot claim a graph dispatch'))
    @contextmanager
    def nested():
        s.events.append('append-savepoint')
        try:yield
        except BaseException:
            s.events.append('append-savepoint-rollback');raise
    s.db.begin_nested=nested
    def validate(*args):
        db,p,cap,role,inputs,key,approval=args
        assert db is s.db and p is s.p and cap is s.cap and role=='data'
        assert inputs is s.inputs and key==s.key and approval is s.approval
        s.events.append('actual-append-authority-boundary')
    def execute(*args):
        s.calls.append('fixed-append-execute')
        if failure:raise failure
        return {'ok':True}
    monkeypatch.setattr(append,'validate_call_approval',validate)
    monkeypatch.setattr(append,'execute',execute)
    return s


def test_fixed_append_can_use_original_evaluation_cap_without_a_fake_graph_job(monkeypatch):
    s=append_scene(monkeypatch)
    row=invoke(s)
    assert row.status=='succeeded' and row.data['approval']==s.approval
    assert s.calls==['fixed-append-execute'] and 'append-savepoint' in s.events
    assert 'actual-append-authority-boundary' in s.events and 'original-graph-dispatch-boundary' not in s.events
    assert s.db.commits==2


def test_append_local_failure_keeps_unknown_and_original_key_without_graph_fallback(monkeypatch):
    s=append_scene(monkeypatch,failure=HTTPException(409,'private-error-never-copied'))
    row=invoke(s)
    assert row.status=='unknown' and row.idempotency_key==s.key
    assert 'append-savepoint-rollback' in s.events and len(s.calls)==1
    assert 'private-error-never-copied' not in str(row.data)


def test_non_append_approval_still_requires_actual_graph_worker(monkeypatch):
    s=scene(monkeypatch,None)
    monkeypatch.setattr(workflow_graph_scope,'owned_graph_job',lambda *a:None)
    row=invoke(s)
    assert row.status!='succeeded' and s.calls==[]


def test_same_key_append_revalidates_authority_before_returning_original_snapshot(monkeypatch):
    s=append_scene(monkeypatch)
    original=invoke(s);s.db.previous=original
    before=copy.deepcopy((original.status,original.data));calls=len(s.calls)
    def revoked(*args):raise HTTPException(403,'revoked')
    monkeypatch.setattr(append,'validate_call_approval',revoked)
    with pytest.raises(HTTPException) as result:invoke(s)
    assert result.value.status_code==403 and len(s.calls)==calls
    assert (original.status,original.data)==before


def test_same_key_cannot_change_append_approval_or_original_command(monkeypatch):
    s=append_scene(monkeypatch)
    row=invoke(s);s.db.previous=row
    s.approval=copy.deepcopy(s.approval);s.approval['command_ref']['id']=identity()
    with pytest.raises(HTTPException):invoke(s)
    assert len(s.calls)==1
