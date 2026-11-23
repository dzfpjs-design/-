"""Real DB clock: policy expiring after the long recheck is still refused."""
import copy
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as old
from generic_agents import same_goal_advice_unlimited_authority_v2 as authority
from generic_agents import same_goal_advice_unlimited_plan_candidate_v2 as subject
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents.db import engine
from generic_agents.goal_evaluation_provider_v288 import KEY
from generic_agents.models import Plan, Task
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'), reason='isolated synthetic checkpoint only')
def test_policy_expires_during_final_real_authority_check(monkeypatch, reject_non_loopback_network):
    case=restore_case(monkeypatch)
    with Session(engine()) as db:
        task=db.get(Task,case.task_id);now=db.scalar(select(func.clock_timestamp()));expiry=now+timedelta(seconds=150)
        body={'request_id':str(uuid4()),'expected_goal_hash':task.data['goal_hash'],
            'expected_mature_head_hash':task.data[KEY]['head_hash'],
            'limits':{'max_tokens':512,'max_request_bytes':50000,'max_evidence_items':2},
            'expires_at':expiry.isoformat(),'reason':'仅合成资料的自然到期检查'}
        issued=policy.authorize(db,case.principal,task.id,body);db.commit()
        original=db.scalar(select(Plan).where(Plan.task_id==task.id,Plan.data['schema'].as_string()==old.PLAN))
        request=copy.deepcopy(original.data['preparation_snapshot']['request'])
        request['request_id']=str(uuid4());request['reason']='核验自然到期，不创建任务'
    original_authority=authority.current_policy;seen=[]
    def delayed(db, principal, task_id, policy_ref):
        result=original_authority(db,principal,task_id,policy_ref);seen.append(True)
        if len(seen)==2:
            clock=db.scalar(select(func.clock_timestamp()))
            assert clock < expiry, '必须在到期前进入最终实时校验'
            print('POLICY_FINAL_RECHECK_ENTERED_BEFORE_EXPIRY',flush=True)
            db.execute(select(func.pg_sleep((expiry-clock).total_seconds()+0.25)))
        return result
    monkeypatch.setattr(authority,'current_policy',delayed)
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as error:
            subject.derive_candidate(db,case.principal,case.task_id,request,issued['policy_ref'])
        assert error.value.status_code==409 and '到期' in str(error.value.detail)
        assert db.in_transaction()
    assert len(seen)==2
    print('REAL_POLICY_EXPIRY_DURING_RECHECK_REFUSED',flush=True)
