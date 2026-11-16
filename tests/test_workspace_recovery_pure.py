"""Explicit paused-delivery gate, with no services/database/process effects."""
from types import SimpleNamespace as Row
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import worker,instance_domain
from generic_agents.models import Entity,DomainInstance
from generic_agents.contracts import digest

uid=lambda:str(uuid4())


def seams(monkeypatch,status='paused',active=True):
    workspace=uid();actor=uid();entity=Row(id=uid(),workspace_id=workspace)
    event=Row(id=uid(),workspace_id=workspace)
    original=Row(id=uid(),entity_id=entity.id,data={'submitted_by':actor})
    instance=Row(id=uid(),status=status,active_release_id=uid() if active else None)
    release=Row(id=uid(),instance_id=instance.id);locks=[];audits=[]
    def scoped(db,model,key,p,lock=False):
        assert p.workspace_id==workspace and p.user_id==actor
        if model is Entity:assert key==entity.id;return entity
        assert model is DomainInstance and key==instance.id and lock is True
        locks.append(key);return instance
    monkeypatch.setattr(worker,'scoped',scoped)
    monkeypatch.setattr(worker,'audit',lambda *args:audits.append(args))
    monkeypatch.setattr(instance_domain,'generated_release',lambda *args:release)
    return event,original,instance,locks,audits


@pytest.mark.parametrize('status,active',[('paused',True),('draft',False),('partially_active',False)])
def test_inactive_instance_is_locked_and_returns_honest_skipped_receipt(monkeypatch,status,active):
    event,original,instance,locks,audits=seams(monkeypatch,status,active)
    receipt=worker.observation_derivation_hold(None,event,original)
    assert locks==[instance.id] and len(audits)==1
    assert receipt['instance_status']==status and receipt['instance_id']==instance.id
    assert receipt['cloud_calls']==0 and receipt['device_actions'] is False and receipt['current_state_updated'] is False
    assert receipt['receipt_hash']==digest({k:v for k,v in receipt.items() if k!='receipt_hash'})


def test_active_or_legacy_path_is_not_relabelled_as_skipped(monkeypatch):
    event,original,instance,locks,audits=seams(monkeypatch,'partially_active',True)
    assert worker.observation_derivation_hold(None,event,original) is None and locks==[instance.id] and not audits
    locks.clear();monkeypatch.setattr(instance_domain,'generated_release',lambda *args:None)
    assert worker.observation_derivation_hold(None,event,original) is None and not locks and not audits


def test_ambiguous_or_wrong_scope_origin_stays_blocked_not_swallowed(monkeypatch):
    event,original,instance,locks,audits=seams(monkeypatch)
    def ambiguous(*args):raise HTTPException(409,'对象来源版本存在歧义，不能猜测接收实例')
    monkeypatch.setattr(instance_domain,'generated_release',ambiguous)
    with pytest.raises(HTTPException):worker.observation_derivation_hold(None,event,original)
    assert not locks and not audits
    def wrong_scope(*args,**kwargs):raise HTTPException(404,'对象不属于当前工作区')
    monkeypatch.setattr(worker,'scoped',wrong_scope)
    with pytest.raises(HTTPException):worker.observation_derivation_hold(None,event,original)
    assert not audits
