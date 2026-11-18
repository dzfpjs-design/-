"""Disposable-PG tests for an unconnected, non-dispatching transaction primitive."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from generic_agents import cloud_scoped_extra_reservation as scoped
from generic_agents.cloud_budget import BudgetDenied, finish_tx, reserve
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.leases import ExecutionUnauthorized, LeaseLost, attach
from generic_agents.models import Audit, Capability, CloudCall, Job, Membership, User, now
from generic_agents.security import Principal


@pytest.fixture
def scenario(account):
    username, _, workspace = account
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == username))
        data = {'execution':{'provider':'synthetic'}, 'data_policy':{'cloud_allowed':True}}
        cap = Capability(workspace_id=workspace, namespace='synthetic', name='model', release_version='1',
            kind='foundation_model', status='published', data=data, content_hash=digest(data), validated_hash=digest(data))
        lease = str(uuid4())
        worker = Job(workspace_id=workspace, kind='synthetic_scoped_task', status='running',
            idempotency_key='synthetic-worker:' + str(uuid4()), lease_token=lease,
            lease_until=now() + timedelta(minutes=10), data={'actor_id':user.id})
        db.add_all([cap, worker]); db.flush()
        permit = Job(workspace_id=workspace, kind='cloud_extra_allowance_v1', status='internal',
            idempotency_key=scoped.permit_key(worker.id,'synthetic-stage'), data={})
        db.add(permit); db.flush()
        manifest = {'schema':'cloud-extra-allowance-manifest-v1','workspace_id':workspace,
            'actor_id':user.id,'issuer_id':user.id,'job_id':worker.id,'job_kind':worker.kind,
            'permit_id':permit.id,'capability_id':cap.id,'capability_hash':cap.content_hash,
            'scope_kind':'synthetic_only','stage_id':'synthetic-stage',
            'scope_hash':digest({'stage':'synthetic-stage'}),'request_hash':digest({'input':'synthetic'}),
            'options_hash':digest({'output':100}),'token_bound':50,'max_tokens':50,'max_calls':1,
            'not_before':(now()-timedelta(minutes=1)).isoformat(),
            'expires_at':(now()+timedelta(minutes=5)).isoformat()}
        audit = Audit(workspace_id=workspace,actor_id=user.id,object_id=permit.id,
            action='cloud.extra_allowance.issued_v1',data={'schema':'cloud-extra-allowance-issue-v1',
                'permit_id':permit.id,'manifest_hash':digest(manifest)})
        db.add(audit); db.flush()
        permit.data={'schema':'cloud-extra-allowance-record-v1','manifest':manifest,
            'manifest_hash':digest(manifest),'audit_id':audit.id,'used_call_id':None}
        db.commit()
        return {'workspace':workspace,'actor':user.id,'cap':cap.id,'job':worker.id,
            'lease':lease,'permit':permit.id,'audit':audit.id,'manifest':manifest}


def principal(scene):
    return Principal(scene['actor'], scene['workspace'], 'admin', 'synthetic-worker')


def open_worker(scene):
    db = Session(engine(), expire_on_commit=False)
    attach(db, scene['job'], scene['lease'])
    db.info['worker_actor'] = principal(scene)
    return db


def adapter(db, p, job, cap, manifest):
    # A test-only stand-in for a future server-owned adapter. There is no
    # registered production adapter, and this synthetic value has no access
    # to or relationship with a real report or device.
    return (manifest['scope_kind'] == 'synthetic_only' and manifest['stage_id'] in {'synthetic-stage','synthetic-stage-2'}
            and manifest['scope_hash'] == digest({'stage':manifest['stage_id']})
            and manifest['job_id'] == job.id and manifest['actor_id'] == p.user_id
            and manifest['capability_id'] == cap.id)


def invoke(db, scene, **overrides):
    m = scene['manifest']
    args = {'principal':principal(scene),'permit_id':scene['permit'],'capability_id':scene['cap'],
            'token_bound':m['token_bound'],'request_hash':m['request_hash'],'options_hash':m['options_hash']}
    return scoped.reserve_scoped_tx(db, **{**args, **overrides})


def counts(scene):
    with Session(engine()) as db:
        calls = db.scalar(select(func.count()).select_from(CloudCall).where(CloudCall.workspace_id==scene['workspace']))
        dispatches = db.scalar(select(func.count()).select_from(Job).where(Job.workspace_id==scene['workspace'],
            Job.kind=='cloud_extra_dispatch_v1'))
        budget = db.scalar(select(Job).where(Job.workspace_id==scene['workspace'],Job.kind=='cloud_budget'))
        used = db.get(Job,scene['permit']).data['used_call_id']
        return calls, dispatches, None if budget is None else dict(budget.data), used


def second_stage(scene):
    with Session(engine(), expire_on_commit=False) as db:
        manifest = {**scene['manifest'], 'stage_id':'synthetic-stage-2',
            'scope_hash':digest({'stage':'synthetic-stage-2'}),
            'request_hash':digest({'input':'another-synthetic-stage'})}
        permit = Job(workspace_id=scene['workspace'],kind='cloud_extra_allowance_v1',status='internal',
            idempotency_key=scoped.permit_key(scene['job'],manifest['stage_id']),data={})
        db.add(permit);db.flush();manifest['permit_id']=permit.id
        audit = Audit(workspace_id=scene['workspace'],actor_id=scene['actor'],object_id=permit.id,
            action='cloud.extra_allowance.issued_v1',data={'schema':'cloud-extra-allowance-issue-v1',
                'permit_id':permit.id,'manifest_hash':digest(manifest)})
        db.add(audit);db.flush()
        permit.data={'schema':'cloud-extra-allowance-record-v1','manifest':manifest,
            'manifest_hash':digest(manifest),'audit_id':audit.id,'used_call_id':None}
        db.commit()
        return {**scene,'permit':permit.id,'audit':audit.id,'manifest':manifest}


def separate_issuer(scene):
    """Give this synthetic permit a different administrator as issuer."""
    with Session(engine(), expire_on_commit=False) as db:
        issuer = User(username='issuer_' + uuid4().hex, password_hash='synthetic')
        db.add(issuer); db.flush()
        db.add(Membership(user_id=issuer.id, workspace_id=scene['workspace'], role='admin'))
        permit = db.get(Job, scene['permit'])
        data = dict(permit.data); manifest = dict(data['manifest'])
        manifest['issuer_id'] = issuer.id
        data['manifest'] = manifest; data['manifest_hash'] = digest(manifest)
        permit.data = data
        audit = db.get(Audit, scene['audit'])
        audit.actor_id = issuer.id
        audit.data = {**audit.data, 'manifest_hash':data['manifest_hash']}
        db.commit()
        return issuer.id


def test_no_production_adapter_and_no_unfenced_session_can_reserve(scenario):
    with Session(engine()) as db:
        with pytest.raises(scoped.ScopedReservationDenied): invoke(db,scenario)
        db.rollback()
    with open_worker(scenario) as db:
        with pytest.raises(scoped.ScopedReservationDenied): invoke(db,scenario)
        db.rollback()
    assert counts(scenario)==(0,0,None,None)


@pytest.fixture
def synthetic_adapter(monkeypatch):
    monkeypatch.setattr(scoped, '_task_adapter', lambda kind: adapter if kind=='synthetic_only' else None)


def test_atomic_reserve_records_original_budget_and_existing_settlement(scenario,synthetic_adapter,monkeypatch):
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',10)
    with open_worker(scenario) as db:
        call_id = invoke(db,scenario)
        db.commit()
    calls, dispatches, budget, used = counts(scenario)
    assert (calls,dispatches,used)==(1,1,call_id) and budget['tokens']==50 and budget['calls']==1
    with Session(engine()) as db:
        cap=db.get(Capability,scenario['cap'])
        with pytest.raises(BudgetDenied): reserve(principal(scenario),cap,1,digest({'other':'request'}))
    with Session(engine()) as db:
        assert finish_tx(db,call_id,{'usage':{'total_tokens':17}},'succeeded')
        db.commit()
    assert counts(scenario)[2]['tokens']==17
    with open_worker(scenario) as db:
        with pytest.raises(scoped.ScopedReservationDenied): invoke(db,scenario)
        db.rollback()
    assert counts(scenario)[:2]==(1,1)


def test_rollback_preserves_permit_budget_and_call(scenario,synthetic_adapter):
    with open_worker(scenario) as db:
        invoke(db,scenario)
        db.rollback()
    assert counts(scenario)==(0,0,None,None)


@pytest.mark.parametrize('changed', ['request','options','bound','audit','role','user','capability','expired','worker'])
def test_drift_or_authority_changes_fail_closed(scenario,synthetic_adapter,changed):
    with Session(engine()) as db:
        if changed=='audit':
            a=db.get(Audit,scenario['audit']);a.data={**a.data,'manifest_hash':'0'*64}
        elif changed=='role':
            m=db.scalar(select(Membership).where(Membership.workspace_id==scenario['workspace'],
                Membership.user_id==scenario['actor']));m.role='viewer'
        elif changed=='user': db.get(User,scenario['actor']).active=False
        elif changed=='capability': db.get(Capability,scenario['cap']).status='draft'
        elif changed=='expired':
            permit=db.get(Job,scenario['permit']);data=dict(permit.data);manifest=dict(data['manifest'])
            manifest['expires_at']=(now()-timedelta(seconds=1)).isoformat()
            data['manifest']=manifest;data['manifest_hash']=digest(manifest);permit.data=data
        elif changed=='worker':
            worker=db.get(Job,scenario['job']);worker.lease_token=str(uuid4())
        db.commit()
    db=open_worker(scenario)
    try:
        override = {'request':{'request_hash':'0'*64},'options':{'options_hash':'0'*64},
                    'bound':{'token_bound':51}}.get(changed,{})
        with pytest.raises((scoped.ScopedReservationDenied, ExecutionUnauthorized, LeaseLost)):
            invoke(db,scenario,**override)
        db.rollback()
    finally: db.close()
    assert counts(scenario)==(0,0,None,None)


def test_cross_workspace_or_actor_cannot_use_other_workers_permit(scenario,synthetic_adapter):
    for different in (Principal(str(uuid4()),scenario['workspace'],'admin','synthetic-worker'),
                      Principal(scenario['actor'],str(uuid4()),'admin','synthetic-worker')):
        with open_worker(scenario) as db:
            db.info['worker_actor']=different
            with pytest.raises((scoped.ScopedReservationDenied,ExecutionUnauthorized,LeaseLost)):
                invoke(db,scenario,principal=different)
            db.rollback()
    assert counts(scenario)==(0,0,None,None)


@pytest.mark.parametrize('change', ['role', 'active'])
def test_cached_distinct_issuer_revocation_is_refreshed(scenario, synthetic_adapter, change):
    issuer_id = separate_issuer(scenario)
    with open_worker(scenario) as worker:
        cached_user = worker.get(User, issuer_id)
        cached_member = worker.scalar(select(Membership).where(
            Membership.user_id == issuer_id, Membership.workspace_id == scenario['workspace']))
        assert cached_user.active and cached_member.role == 'admin'
        worker.commit()  # The real worker Session uses expire_on_commit=False.
        with Session(engine()) as revoker:
            if change == 'role':
                member = revoker.scalar(select(Membership).where(
                    Membership.user_id == issuer_id, Membership.workspace_id == scenario['workspace']))
                member.role = 'viewer'
            else:
                revoker.get(User, issuer_id).active = False
            revoker.commit()
        # Keep these objects strongly referenced: FOR UPDATE must refresh them.
        assert cached_user.active and cached_member.role == 'admin'
        with pytest.raises(scoped.ScopedReservationDenied):
            invoke(worker, scenario)
        worker.rollback()
    assert counts(scenario) == (0, 0, None, None)


@pytest.mark.parametrize('change', ['role', 'active'])
def test_distinct_issuer_revocation_wins_row_lock_race(scenario, synthetic_adapter, change):
    issuer_id = separate_issuer(scenario)
    ready, start = Event(), Event()
    state = {}

    def reserve_after_cache():
        with open_worker(scenario) as worker:
            cached_user = worker.get(User, issuer_id)
            cached_member = worker.scalar(select(Membership).where(
                Membership.user_id == issuer_id, Membership.workspace_id == scenario['workspace']))
            assert cached_user.active and cached_member.role == 'admin'
            worker.commit()
            state['pid'] = worker.scalar(select(func.pg_backend_pid()))
            ready.set()
            if not start.wait(10):
                raise AssertionError('revocation transaction did not start')
            try:
                invoke(worker, scenario)
                worker.commit()
                return 'accepted'
            except scoped.ScopedReservationDenied:
                worker.rollback()
                return 'denied'

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reserve_after_cache)
        assert ready.wait(10)
        try:
            with Session(engine()) as revoker:
                revoker_pid = revoker.scalar(select(func.pg_backend_pid()))
                if change == 'role':
                    row = revoker.scalar(select(Membership).where(
                        Membership.user_id == issuer_id, Membership.workspace_id == scenario['workspace']).with_for_update())
                    row.role = 'viewer'
                else:
                    row = revoker.scalar(select(User).where(User.id == issuer_id).with_for_update())
                    row.active = False
                start.set()
                deadline = monotonic() + 5
                with Session(engine()) as observer:
                    while monotonic() < deadline:
                        blockers = observer.scalar(select(func.pg_blocking_pids(state['pid'])))
                        if revoker_pid in blockers:
                            break
                        sleep(0.02)
                    else:
                        raise AssertionError('worker did not wait for the issuer revocation lock')
                revoker.commit()
        finally:
            start.set()
        assert future.result(timeout=10) == 'denied'
    assert counts(scenario) == (0, 0, None, None)


def test_concurrent_workers_same_lease_can_only_consume_once(scenario,synthetic_adapter):
    barrier=Barrier(2)
    def run():
        with open_worker(scenario) as db:
            barrier.wait(timeout=10)
            try:
                result=invoke(db,scenario);db.commit();return ('saved',result)
            except scoped.ScopedReservationDenied:
                db.rollback();return ('denied',None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:run(),range(2)))
    assert sorted(x[0] for x in results)==['denied','saved']
    assert counts(scenario)[:2]==(1,1) and counts(scenario)[2]['tokens']==50


def test_one_worker_has_distinct_one_use_permits_for_distinct_stages(scenario,synthetic_adapter):
    other=second_stage(scenario)
    for stage in (scenario,other):
        with open_worker(stage) as db:
            invoke(db,stage);db.commit()
    for stage in (scenario,other):
        with open_worker(stage) as db:
            with pytest.raises(scoped.ScopedReservationDenied):invoke(db,stage)
            db.rollback()
    calls,dispatches,budget,_=counts(scenario)
    assert (calls,dispatches)==(2,2) and budget['tokens']==100 and budget['calls']==2
    assert scoped.permit_key(scenario['job'],'synthetic-stage') != scoped.permit_key(scenario['job'],'synthetic-stage-2')


def test_same_session_reuses_one_listener_and_clears_private_checks(scenario,synthetic_adapter):
    other=second_stage(scenario)
    with open_worker(scenario) as db:
        invoke(db,scenario)
        state=db.info[scoped._SESSION_KEY]
        assert len(state['checks'])==1
        db.commit()
        assert state['checks']=={}
        invoke(db,other)
        assert db.info[scoped._SESSION_KEY] is state and len(state['checks'])==1
        db.commit()
        assert state['checks']=={}
    assert counts(scenario)[:2]==(2,2)


def test_rollback_clears_listener_private_state_for_same_session(scenario,synthetic_adapter):
    with open_worker(scenario) as db:
        invoke(db,scenario);state=db.info[scoped._SESSION_KEY]
        assert len(state['checks'])==1
        db.rollback()
        assert state['checks']=={} and counts(scenario)==(0,0,None,None)
        invoke(db,scenario);db.commit()
        assert state['checks']=={}
    assert counts(scenario)[:2]==(1,1)


def test_close_rolls_back_and_releases_pending_private_check(scenario,synthetic_adapter):
    db=open_worker(scenario)
    invoke(db,scenario);state=db.info[scoped._SESSION_KEY]
    assert len(state['checks'])==1
    db.close()
    assert state['checks']=={} and counts(scenario)==(0,0,None,None)


def test_second_reservation_in_same_transaction_is_rejected(scenario,synthetic_adapter):
    other=second_stage(scenario)
    with open_worker(scenario) as db:
        invoke(db,scenario)
        with pytest.raises(scoped.ScopedReservationDenied):invoke(db,other)
        db.rollback()
    assert counts(scenario)==(0,0,None,None)


def test_concurrent_distinct_stages_are_independent(scenario,synthetic_adapter):
    other=second_stage(scenario);barrier=Barrier(2)
    def run(stage):
        with open_worker(stage) as db:
            barrier.wait(timeout=10);result=invoke(db,stage);db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,(scenario,other)))
    assert len(set(results))==2
    assert counts(scenario)[:2]==(2,2) and counts(scenario)[2]['tokens']==100


def test_database_rejects_a_second_permit_for_the_same_job_and_stage(scenario):
    with Session(engine()) as db:
        db.add(Job(workspace_id=scenario['workspace'],kind='cloud_extra_allowance_v1',status='internal',
            idempotency_key=scoped.permit_key(scenario['job'],'synthetic-stage'),data={}))
        with pytest.raises(IntegrityError):db.commit()
        db.rollback()


def test_raw_unpaired_jsonb_permit_update_is_rejected(scenario,synthetic_adapter):
    with Session(engine()) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE jobs SET data=jsonb_set(data, '{used_call_id}', to_jsonb('forged'::text)) WHERE id=:id"),
                       {'id':scenario['permit']})
        db.rollback()
    assert counts(scenario)==(0,0,None,None)


@pytest.mark.parametrize('stage', ['', ' x', 'x ', 'a\u0000b', 'e\u0301', 'x'*121])
def test_stage_identity_rejects_ambiguous_or_unbounded_inputs(scenario,stage):
    with pytest.raises(scoped.ScopedReservationDenied):scoped.permit_key(scenario['job'],stage)


@pytest.mark.parametrize('target', ['permit','audit','budget','dispatch','call','worker',
                                   'worker_status','worker_token','worker_expiry'])
def test_mutation_before_commit_rolls_back_whole_reservation(scenario,synthetic_adapter,target):
    with open_worker(scenario) as db:
        call_id=invoke(db,scenario)
        if target=='permit': row=db.get(Job,scenario['permit'])
        elif target=='audit': row=db.get(Audit,scenario['audit'])
        elif target=='budget': row=db.scalar(select(Job).where(Job.workspace_id==scenario['workspace'],Job.kind=='cloud_budget'))
        elif target=='dispatch': row=db.scalar(select(Job).where(Job.workspace_id==scenario['workspace'],Job.kind=='cloud_extra_dispatch_v1'))
        elif target=='call': row=db.get(CloudCall,call_id)
        else: row=db.get(Job,scenario['job'])
        if target == 'worker_status': row.status = 'failed'
        elif target == 'worker_token': row.lease_token = str(uuid4())
        elif target == 'worker_expiry': row.lease_until = now() - timedelta(seconds=1)
        else: row.data={**row.data,'synthetic_tampering':True}
        with pytest.raises(scoped.ScopedReservationDenied): db.commit()
        db.rollback()
    assert counts(scenario)==(0,0,None,None)


@pytest.mark.parametrize('target',['permit','audit','dispatch'])
def test_deletion_before_commit_rolls_back_whole_reservation(scenario,synthetic_adapter,target):
    with open_worker(scenario) as db:
        invoke(db,scenario)
        if target=='permit':row=db.get(Job,scenario['permit'])
        elif target=='audit':row=db.get(Audit,scenario['audit'])
        else:row=db.scalar(select(Job).where(Job.workspace_id==scenario['workspace'],Job.kind=='cloud_extra_dispatch_v1'))
        db.delete(row)
        with pytest.raises(scoped.ScopedReservationDenied):db.commit()
        db.rollback()
    assert counts(scenario)==(0,0,None,None)


def test_call_identity_tampering_is_rejected_by_database_and_rolls_back(scenario,synthetic_adapter):
    # The typed-payload pairing constraint binds the row identity. The ORM
    # precommit check does not independently compare CloudCall.id, so this
    # guarantee depends on that PostgreSQL constraint being installed.
    with open_worker(scenario) as db:
        call_id = invoke(db,scenario)
        call = db.get(CloudCall,call_id)
        call.id = str(uuid4())
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    assert counts(scenario)==(0,0,None,None)


def test_duplicate_issuance_audit_fails_closed(scenario,synthetic_adapter):
    with Session(engine()) as db:
        original=db.get(Audit,scenario['audit'])
        db.add(Audit(workspace_id=original.workspace_id,actor_id=original.actor_id,
            object_id=original.object_id,action=original.action,data=dict(original.data)))
        db.commit()
    with open_worker(scenario) as db:
        with pytest.raises(scoped.ScopedReservationDenied): invoke(db,scenario)
        db.rollback()
    assert counts(scenario)==(0,0,None,None)


def test_expired_between_reservation_and_commit_rolls_back(scenario,synthetic_adapter):
    with Session(engine()) as db:
        permit=db.get(Job,scenario['permit']);data=dict(permit.data);manifest=dict(data['manifest'])
        manifest['expires_at']=(now()+timedelta(seconds=1)).isoformat()
        data['manifest']=manifest;data['manifest_hash']=digest(manifest);permit.data=data
        audit=db.get(Audit,scenario['audit']);audit.data={**audit.data,'manifest_hash':data['manifest_hash']}
        db.commit()
    with open_worker(scenario) as db:
        invoke(db,scenario)
        sleep(1.1)
        with pytest.raises(scoped.ScopedReservationDenied): db.commit()
        db.rollback()
    assert counts(scenario)==(0,0,None,None)
