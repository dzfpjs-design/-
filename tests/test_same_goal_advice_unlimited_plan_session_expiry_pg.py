"""A real API login session naturally expires during the final DB fence."""
import copy
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents import same_goal_advice_contracts_v302 as old
from generic_agents import same_goal_advice_unlimited_plan_candidate_v2 as subject
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents.db import engine
from generic_agents.goal_evaluation_provider_v288 import KEY
from generic_agents.models import AuthSession, Membership, Plan, Task, User
from generic_agents.security import Principal, password_hash
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'), reason='isolated synthetic checkpoint only')
def test_actual_login_session_expires_after_first_final_fence(monkeypatch, reject_non_loopback_network):
    case=restore_case(monkeypatch)
    with Session(engine()) as db:
        task=db.get(Task,case.task_id);now=db.scalar(select(func.clock_timestamp()))
        body={'request_id':str(uuid4()),'expected_goal_hash':task.data['goal_hash'],
            'expected_mature_head_hash':task.data[KEY]['head_hash'],
            'limits':{'max_tokens':512,'max_request_bytes':50000,'max_evidence_items':2},
            'expires_at':(now+timedelta(minutes=10)).isoformat(),'reason':'隔离合成会话的自然到期检查'}
        issued=policy.authorize(db,case.principal,task.id,body);db.commit()
        original=db.scalar(select(Plan).where(Plan.task_id==task.id,Plan.data['schema'].as_string()==old.PLAN))
        request=copy.deepcopy(original.data['preparation_snapshot']['request'])
        request['request_id']=str(uuid4());request['reason']='核验会话自然到期，不创建任务'
        username='v2_session_'+uuid4().hex[:12];password=uuid4().hex
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        user_id=user.id;db.add(Membership(user_id=user_id,workspace_id=case.workspace_id,role='admin'));db.commit()
    # This is an actual isolated application login. A short test-server
    # session lifetime is selected before login; no saved timestamp is edited.
    with monkeypatch.context() as local:
        local.setattr(settings(),'session_hours',150/3600)
        with TestClient(app) as client:
            login=client.post('/api/login',json={'username':username,'password':password})
            assert login.status_code==200,login.text
    with Session(engine()) as db:
        session=db.scalar(select(AuthSession).where(AuthSession.user_id==user_id,AuthSession.revoked.is_(False)))
        assert session is not None;session_id,expiry=session.id,session.expires_at
    principal=Principal(user_id,case.workspace_id,'admin',session_id)
    original_guard=subject.v2store._lock_current_reader;entered=[]
    def delayed(db,actor,**kwargs):
        result=original_guard(db,actor,**kwargs);entered.append(True)
        clock=db.scalar(select(func.clock_timestamp()))
        assert clock < expiry, '必须在会话到期前进入最后核对'
        print('SESSION_FINAL_FENCE_ENTERED_BEFORE_EXPIRY',flush=True)
        db.execute(select(func.pg_sleep((expiry-clock).total_seconds()+0.25)))
        return result
    monkeypatch.setattr(subject.v2store,'_lock_current_reader',delayed)
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as error:
            subject.derive_candidate(db,principal,case.task_id,request,issued['policy_ref'])
        assert error.value.status_code==409 and '会话' in str(error.value.detail)
        assert db.in_transaction()
    assert entered
    print('REAL_LOGIN_SESSION_EXPIRY_DURING_FENCE_REFUSED',flush=True)
