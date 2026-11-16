"""Real PG barriers: current authority is linearized through each commit.

Creation uses Actor -> Instance as of V242; already locked authority finishes
before revocation. Legacy retirement retains its original post-Instance check.
"""
import time
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import DomainInstance, Membership, Task, Job, Invocation, CloudCall
from generic_agents.graph_event_models import GraphEventIntent, GraphEventAdmission
from generic_agents.task_creation_recovery_v120 import TaskCreationRetirement
from tests.test_instance_workspace import prepared, enable, object_row


@pytest.fixture
def waiting_release(client):
    """New ordinary published human flow per case, no backup or F11 harness.

    These tests isolate the public Task/retirement authority locks. They do
    not need a waiting graph, report transport, model or copied authority.
    """
    release,_,_=prepared(client)
    enable(client,release)
    return {'client':client,'release':release,'object':object_row(client,release)}


def _paths(value):
    base='/api/workspace/instances/'+value['release']['instance_id']
    return base+'/graph-task-inputs',base+'/tasks',base+'/task-requests/lookup'


def _body(value):
    release=value['release']
    return {'release_id':release['id'],'business_id':release['available_business_ids'][0],
        'entity_ids':[value['object']['id']],'title':'核对真实普通任务授权锁',
        'request_id':str(uuid4()),'graph_inputs':{}}


def _counts():
    with Session(engine()) as db:
        return {model.__tablename__:db.scalar(select(func.count()).select_from(model))
            for model in (Task,Job,Invocation,CloudCall,GraphEventIntent,GraphEventAdmission,TaskCreationRetirement)}


@pytest.mark.parametrize('operation',['create','retire'])
def test_actual_actor_revocation_while_waiting_for_instance_lock_blocks_new_write(waiting_release,operation):
    value=waiting_release;client=value['client'];_,create_path,lookup_path=_paths(value)
    body=_body(value); me=client.get('/api/me').json();before=_counts()
    if operation=='create':
        # V242 deliberately linearizes normal creation Actor -> Instance.
        # Revocation must wait for an already-authorized creation rather than
        # deadlocking this test's main thread behind its own Instance barrier.
        with Session(engine()) as blocker,ThreadPoolExecutor(max_workers=2) as pool:
            blocker.get(DomainInstance,value['release']['instance_id'],with_for_update=True)
            future=pool.submit(client.post,create_path,json=body)
            def revoke():
                with Session(engine()) as db:
                    db.execute(text("SET LOCAL lock_timeout='8s'"))
                    member=db.scalar(select(Membership).where(Membership.user_id==me['user_id'],Membership.workspace_id==me['workspace_id']))
                    member.role='viewer';db.commit()
            try:
                deadline=time.monotonic()+5
                while True:
                    with engine().connect() as connection:
                        waiting=connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query ILIKE '%domain_instances%'"))
                    if waiting:break
                    assert not future.done()
                    assert time.monotonic()<deadline
                    time.sleep(.02)
                revocation=pool.submit(revoke)
                deadline=time.monotonic()+5
                while True:
                    with engine().connect() as connection:
                        waiting=connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query ILIKE '%UPDATE memberships%'"))
                    if waiting:break
                    assert not revocation.done(),'revocation bypassed held actor authorization'
                    assert time.monotonic()<deadline
                    time.sleep(.02)
            finally:blocker.rollback()
            response=future.result(timeout=10);revocation.result(timeout=10)
        assert response.status_code==201,response.text
        next_body={**body,'request_id':'after_revoke_'+uuid4().hex}
        assert client.post(create_path,json=next_body).status_code==403
        recovered=client.post(lookup_path,json=body)
        assert recovered.status_code==200 and recovered.json()['found'] is True
        return
    path=create_path if operation=='create' else lookup_path.rsplit('/',1)[0]+'/retire'
    payload=body if operation=='create' else {'original':body,'reason':'并发撤权不得停止原请求'}
    with Session(engine()) as blocker,ThreadPoolExecutor(max_workers=1) as pool:
        blocker.get(DomainInstance,value['release']['instance_id'],with_for_update=True)
        future=pool.submit(client.post,path,json=payload)
        try:
            deadline=time.monotonic()+5
            while True:
                with engine().connect() as connection:
                    waiting=connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query ILIKE '%domain_instances%'"))
                if waiting:break
                assert not future.done(),'request did not reach the real instance lock barrier'
                assert time.monotonic()<deadline,'real instance lock was not observed'
                time.sleep(.02)
            with Session(engine()) as db:
                member=db.scalar(select(Membership).where(Membership.user_id==me['user_id'],Membership.workspace_id==me['workspace_id']))
                assert member.role=='admin';member.role='viewer';db.commit()
        finally:
            blocker.rollback()
        response=future.result(timeout=10)
    assert response.status_code==403,response.text
    assert _counts()==before
    recovered=client.post(lookup_path,json=body)
    assert recovered.status_code==200 and recovered.json()['found'] is False
