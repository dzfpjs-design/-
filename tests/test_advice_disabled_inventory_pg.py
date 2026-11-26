"""Real isolated PG and real login; all task/ledger contents are synthetic."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, Job, Plan, Task, User
from generic_agents import same_goal_advice_expired_cancel_candidate_v2 as cancel
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from tests.test_same_goal_advice_expired_cancel_candidate_v2_pg import expired, body
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def availability(client, task_id):
    return client.get('/api/workspace/tasks/' + task_id + '/business-advice-v2/availability')


def another_user(client, workspace):
    username, password = uuid4().hex, uuid4().hex
    result = client.post('/api/users', json={'username':username,'password':password,'role':'operator'})
    assert result.status_code == 201
    other = TestClient(app)
    auth = other.post('/api/login', json={'username':username,'password':password})
    assert auth.status_code == 200
    other.headers.update({'X-CSRF-Token':auth.json()['csrf_token'], 'X-Workspace-ID':workspace})
    return other


def remove_plan(db, s):
    db.delete(db.get(Job,s['job_id'])); db.delete(db.get(Audit,s['accepted_id'])); db.delete(db.get(Plan,s['plan_id']))


def test_pending_plan_and_policy_only_visible_to_other_authorized_user(synthetic_custody, client, account, monkeypatch):
    s=synthetic_custody; monkeypatch.setattr(settings(),'v2_advice_setup_test_enabled',False)
    with another_user(client,account[2]) as other:
        assert availability(other,s['task_id']).json()=={'enabled':False,'has_unfinished':True}
        with Session(engine()) as db: remove_plan(db,s); db.commit()
        # Other user did not issue this policy and has no local browser cache.
        r=availability(other,s['task_id'])
        assert r.status_code==200 and r.json()=={'enabled':False,'has_unfinished':True}
        assert set(r.json()) == {'enabled','has_unfinished'}
        other.headers['X-Workspace-ID']=str(uuid4())
        denied=availability(other,s['task_id']); assert denied.status_code in (403,404)
        assert 'has_unfinished' not in denied.json()


def test_no_originals_is_empty_and_cancelled_policy_is_verified_closed(expired, client, monkeypatch):
    s=expired; monkeypatch.setattr(settings(),'v2_advice_setup_test_enabled',False)
    assert availability(client,s['task_id']).json()['has_unfinished'] is True
    with Session(engine()) as db:
        cancel.cancel(db,s['principal'],s['task_id'],s['plan_id'],body());db.commit()
    assert availability(client,s['task_id']).json()=={'enabled':False,'has_unfinished':False}
    with Session(engine()) as db:
        # Dedicated synthetic rows only: remove the complete chain to exercise
        # the empty state after the prior cancellation has been authenticated.
        db.delete(db.scalar(select(Audit).where(Audit.action==v2.CANCELLED_EXPIRED)))
        remove_plan(db,s)
        db.delete(db.scalar(select(Audit).where(Audit.action==policy.AUTHORIZATION)))
        db.delete(db.get(Audit,s['policy_id']));db.commit()
    assert availability(client,s['task_id']).json()=={'enabled':False,'has_unfinished':False}


@pytest.mark.parametrize('target',['policy','authorization','invalid_body','cancellation'])
def test_tampered_original_never_reports_empty(expired, client, monkeypatch, target):
    s=expired;monkeypatch.setattr(settings(),'v2_advice_setup_test_enabled',False)
    with Session(engine()) as db:
        cancel.cancel(db,s['principal'],s['task_id'],s['plan_id'],body());db.commit()
    with Session(engine()) as db:
        if target=='policy':row=db.get(Audit,s['policy_id'])
        elif target in ('authorization','invalid_body'):row=db.scalar(select(Audit).where(Audit.action==policy.AUTHORIZATION))
        else:row=db.scalar(select(Audit).where(Audit.action==v2.CANCELLED_EXPIRED))
        if target=='invalid_body':row.data={**copy.deepcopy(row.data),'body':{'unexpected':'synthetic'}}
        else:row.data={**copy.deepcopy(row.data),'tampered_synthetic':True}
        db.commit()
    response=availability(client,s['task_id'])
    assert response.status_code in (403,409)


def test_task_lock_serializes_disabled_check_with_policy_writer(synthetic_custody, client, monkeypatch):
    s=synthetic_custody;monkeypatch.setattr(settings(),'v2_advice_setup_test_enabled',False)
    started=Event()
    def read():
        started.set(); return availability(client,s['task_id'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(engine()) as writer:
            writer.scalar(select(Task).where(Task.id==s['task_id']).with_for_update())
            remove_plan(writer,s)  # After commit only the original policy remains.
            future=pool.submit(read); assert started.wait(3)
            with pytest.raises(TimeoutError): future.result(timeout=.35)
            writer.commit()
        result=future.result(timeout=15)
    assert result.status_code==200 and result.json()['has_unfinished'] is True


def test_verified_settled_terminal_is_checked_and_tampering_fails(history, client, monkeypatch):
    """One explicitly synthetic loopback call; the network sandbox blocks real providers."""
    import json, os
    from sqlalchemy import text
    from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
    from generic_agents import same_goal_advice_loopback_dispatch_candidate_v2 as dispatch
    from generic_agents import same_goal_advice_terminal_candidate_v2 as terminal
    from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
    from tests.synthetic_deepseek_loopback_v2 import provider
    from tests.test_same_goal_advice_runtime_v308 import _opinion
    s, created, owned, reserved, _, _ = history; p=s['principal']
    with Session(engine()) as db:
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:
            storage.install(db);db.commit()
        saved=db.scalar(select(Audit).where(Audit.action==preview.ACTION,Audit.object_id==created['plan_id']))
        opinion=_opinion(saved.data['preview_record']['cloud_request']['decision_input'])
    with Session(engine()) as db: terminal.install(db);db.commit()
    args=(p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'])
    with provider(int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']),content=json.dumps(opinion),
        usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args)
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,args[-1],first['intent_audit_id']);db.commit()
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        monkeypatch.setattr(settings(),'v2_history_compat_test_enabled',True)
        monkeypatch.setattr(settings(),'v2_advice_setup_test_enabled',False)
        # Although the terminal is verified, the policy remains valid and can
        # authorize another v2 plan, so old writes must still be blocked.
        response=availability(client,s['task_id'])
        assert response.status_code==200 and response.json()=={'enabled':False,'has_unfinished':True}
        with Session(engine()) as db:
            event=db.get(Audit,proof['terminal_audit_id']);event.data={**event.data,'charged_tokens':4};db.commit()
        assert availability(client,s['task_id']).status_code==409
        assert len(server.requests)==1


# Fixture graph used by the settled test; every dependency is a test-only
# synthetic service or ledger fixture and runs only under the PG sandbox.
from tests.test_v1_history_reserved_candidate_pg import history
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import synthetic_source_expiry
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_advice_v2_recovery_console_candidate_pg import spool_root
