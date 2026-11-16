"""Real PostgreSQL permission/read-only boundaries; no forged successful proof."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func,text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,Invocation,Membership
from generic_agents.security import Principal
from generic_agents import auto_evaluation_investigation_v356 as investigation
from tests.test_native_goals import setup


def test_missing_original_is_unverifiable_and_does_not_send_or_modify(client,account,monkeypatch):
    from generic_agents import gateway
    _,_,task,_,_=setup(client)
    monkeypatch.setattr(gateway,'invoke',lambda *a,**k:pytest.fail('investigation must never invoke a capability'))
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]))
        p=Principal(member.user_id,member.workspace_id,member.role,'test')
        before=(db.scalar(select(func.count()).select_from(Job)),db.scalar(select(func.count()).select_from(Invocation)))
        result=investigation.investigate(db,p,task['id'],str(uuid4()))
        after=(db.scalar(select(func.count()).select_from(Job)),db.scalar(select(func.count()).select_from(Invocation)))
        assert result['status']=='unverifiable' and '原请求' in result['message']
        assert before==after and not db.new and not db.dirty and not db.deleted


def test_non_manager_cannot_investigate_even_missing_original(client,account):
    _,_,task,_,_=setup(client)
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]))
        p=Principal(member.user_id,member.workspace_id,'viewer','test')
        with pytest.raises(HTTPException):investigation.investigate(db,p,task['id'],str(uuid4()))
