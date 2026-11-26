"""The history guard must fail clearly, without deleting or returning partial rows."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit, User, Workspace, uid
from generic_agents import same_goal_advice_store_v302 as store


def test_limit_explains_pending_review_and_preserves_workspace_records(account):
    username, _, workspace_id = account
    with Session(engine()) as db:
        actor = db.scalar(select(User).where(User.username == username))
        other = Workspace(name='另一合成工作区')
        db.add(other)
        db.flush()
        db.add_all([Audit(id=uid(), workspace_id=workspace_id, actor_id=actor.id,
            action='synthetic.inventory.limit', object_id=str(i), data={})
            for i in range(store.MAX_ROWS + 1)])
        db.add(Audit(id=uid(), workspace_id=other.id, actor_id=actor.id,
            action='synthetic.inventory.limit', object_id='other', data={}))
        db.commit()
        p = SimpleNamespace(workspace_id=workspace_id)
        condition = Audit.action == 'synthetic.inventory.limit'
        assert len(store._rows(db, p, Audit, condition, Audit.object_id != '1000')) == store.MAX_ROWS
        with pytest.raises(HTTPException) as failure:
            store._rows(db, p, Audit, condition)
        assert failure.value.status_code == 409
        assert failure.value.detail == store.INVENTORY_LIMIT_MESSAGE
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id == workspace_id,
            condition)) == store.MAX_ROWS + 1
        assert len(store._rows(db, SimpleNamespace(workspace_id=other.id), Audit, condition)) == 1
