"""Money guards over explicit live/IAM/quota/DB transaction IO boundaries.

Actual Start/Preview contracts, runtime frozen request/anchors, money arithmetic,
scope lifetime, v130 reservation phases, reverse ledger queries, hold/intent
control flow run. No real PG, source authorization, price registration, HTTP,
CloudCall creation, provider billing or target-worker completion is claimed.
"""
import copy
from contextlib import contextmanager, nullcontext
from contextvars import copy_context
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_spending_v308 as spend
from generic_agents import same_goal_advice_runtime_v308 as runtime
from generic_agents import cloud_dispatch_v130 as dispatch
from generic_agents.contracts import digest
from generic_agents.gateway import ModelBlocked
from generic_agents.models import Audit, Capability, CloudCall, Invocation, Job, Plan, Task
from generic_agents.security import Principal
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_runtime_v308 import scene as runtime_scene, _prepared

ACTUAL_POLICY = spend._policy
ACTUAL_CHECKED_ORIGINAL_HOLD = spend.checked_original_hold


def uid(): return str(uuid4())


@pytest.fixture
def scene(monkeypatch):
    t = runtime_scene.__wrapped__(monkeypatch)
    # The protocol fixture spies financial custody; this suite exercises its
    # real implementation, including every retained-proof negative below.
    monkeypatch.setattr(spend, 'checked_original_hold', ACTUAL_CHECKED_ORIGINAL_HOLD)
    # This is a fresh original SDK creation boundary; retained/unknown SDKs
    # must never enter a new spending scope or be assigned a new creator token.
    t.call.data = {**t.call.data, 'lease_token':t.send_job.lease_token}
    frozen = _prepared(t)
    t.locks.clear()  # Start/prepare fixture locks are outside this reserve.
    context = {**t.execution,'task':t.task,'plan':t.plan,'prep':t.prep,
        'invocation':t.call,'model':t.model,'prepared':frozen['prepared'],
        'quote':t.execution['preview'].data['money_quote'],
        'price_ref':t.execution['preview'].data['price_basis_ref'],
        'frozen_request_hash':frozen['request_hash']}
    t.context=context;t.frozen=frozen;t.live_allowed=True;t.live_reads=0;t.session_commits=0
    t.rows.update({Task:[t.task],Plan:[t.plan],Invocation:[t.call],CloudCall:[],
        Capability:[t.model,t.advice]})
    t.refs={'workspace_id':t.p.workspace_id,'actor_id':t.p.user_id,'task_id':t.task.id,'plan_id':t.plan.id,
        'job_id':t.send_job.id,'invocation_id':t.call.id,'execution_hash':context['snapshot']['record_hash'],
        'lease_token':t.send_job.lease_token}
    t.dispatch_identity={'suggestion_id':t.call.id,'job_id':t.send_job.id,
        'task_id':t.task.id,'request_hash':frozen['request_hash']}
    t.bound=t.frozen['prepared']['token_bound'];t.message_hash=t.frozen['prepared']['request_hash']

    class DB:
        no_autoflush=nullcontext()
        def __init__(self):self.tx=object()
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def get_transaction(self):return self.tx
        def scalar(self,statement):
            if 'clock_timestamp' in str(statement):return t.clock
            cls=statement.column_descriptions[0]['entity']
            assert t.p.workspace_id in statement.compile().params.values()
            rows=[row for row in t.rows[cls] if predicate(statement.whereclause,row)]
            assert len(rows)<=1
            if statement._for_update_arg is not None:t.locks.append((cls,True))
            return rows[0] if rows else None
        def scalars(self,statement):
            cls=statement.column_descriptions[0]['entity']
            assert t.p.workspace_id in statement.compile().params.values()
            return [row for row in t.rows[cls] if predicate(statement.whereclause,row)]
        def add(self,row):
            assert type(row) is Audit
            t.rows[Audit].append(row)
        def commit(self):t.session_commits+=1;self.tx=object()
        def flush(self):t.flushes+=1
    t.db=DB()
    def live(db,p,model,bound,request_hash):
        t.live_reads+=1
        assert p==t.p and model.id==t.model.id
        if not t.live_allowed:raise HTTPException(409,'explicit current source/IAM/Start/lease denial')
        return context  # Full source, installed fence and SDK authority are an explicit IO boundary.
    monkeypatch.setattr(spend,'_live',live)
    def policy(db,p,actual,now):
        assert actual is context and now==t.clock
        return copy.deepcopy(t.prep['budget'])  # Actual quota Audit/member IO; separately exercised below.
    monkeypatch.setattr(spend,'_policy',policy)
    monkeypatch.setattr(spend,'_principal',lambda db,refs:t.p)  # Actual active DB role lookup IO.
    monkeypatch.setattr(spend,'Session',lambda _engine:DB())
    from generic_agents import db as actual_db
    monkeypatch.setattr(actual_db,'engine',lambda:None)
    t.atomic_context=None;t.ledger=None
    def reserve(p,model,bound,request_hash):
        t.atomic_context=spend.prepare_reservation(t.db,p,model,bound,request_hash)
        attempt=dispatch.current_attempt();day=t.clock.date().isoformat()
        # This is the existing cloud_budget's actual insert/accounting IO
        # boundary, not a CloudCall produced by the monetary module itself.
        daily=Job(id=uid(),workspace_id=t.p.workspace_id,kind='cloud_budget',status='internal',
            idempotency_key='cloud-budget:'+day,data={'day':day,'tokens':bound,'calls':1})
        t.rows[Job].append(daily);t.daily=daily
        ledger=CloudCall(id=uid(),workspace_id=t.p.workspace_id,capability_id=model.id,status='reserved',
            reserved_tokens=bound,charged_tokens=bound,data={'day':day,'actor_id':p.user_id,
                'request_hash':request_hash,'gateway_dispatch':{'schema':dispatch.SCHEMA,
                    'identity':attempt.identity,'attempt_id':attempt.attempt_id,'reservation_request_hash':request_hash}})
        t.rows[CloudCall].append(ledger);t.ledger=ledger
        spend.bind_reservation(t.db,p,model,ledger,t.atomic_context)
        t.db.commit()  # Explicit external reserve transaction commit IO.
        return ledger.id
    t.reserve=reserve
    def reserved():
        state=t.call.data[runtime.STATE_KEY];state['call_id']=t.ledger.id
        t.call.data={**t.call.data,runtime.STATE_KEY:state}
        t.event(runtime.RESERVED,{**runtime._anchor(frozen),'call_id':t.ledger.id})
    t.reserved=reserved
    return t


@contextmanager
def attempt(t):
    with spend.scope(**t.refs):
        with dispatch.dispatch_scope(t.dispatch_identity,t.p,t.model) as value:
            yield value


def reserve(t):
    return dispatch.reserve_tracked(t.reserve,t.p,t.model,t.bound,t.message_hash)


def events(t,action):return [row for row in t.rows[Audit] if row.action==action]


def test_no_scope_is_exact_legacy_noop_without_database_or_context_creation():
    class Forbidden:
        def __getattribute__(self,name):pytest.fail('legacy hook attempted IO')
    assert spend.prepare_reservation(Forbidden(),None,None,None,None) is None
    assert spend.bind_reservation(Forbidden(),None,None,None,None) is None
    assert spend.before_transport() is None and spend.current_scope() is None


def test_real_reservation_phase_binds_one_hold_and_before_http_one_conservative_intent(scene):
    t=scene
    with attempt(t) as actual:
        call_id=reserve(t);t.reserved()
        assert actual.call_id==call_id and actual.reservation_state=='confirmed' and not actual.transport_started
        hold=events(t,spend.HOLD)[0]
        assert hold.data['reservation_request_hash']==t.message_hash
        assert hold.data['dispatch_identity']['request_hash']==t.frozen['request_hash']!=t.message_hash
        assert hold.data['cloud_call_id']==call_id and hold.data['attempt_id']==actual.attempt_id
        assert hold.data['reserved_minor_units']==t.context['quote']['maximum_cost_minor_units']
        assert hold.data['provider_bill_verified'] is False
        assert t.refs['lease_token'] not in repr(hold.data)
        spend.before_transport()
        assert len(events(t,spend.HOLD))==len(events(t,spend.INTENT))==1
        assert events(t,spend.INTENT)[0].data['transport_outcome']=='not-yet-known'
        assert t.ledger.data[spend.KEY]['intent_ref']['id']==events(t,spend.INTENT)[0].id
        assert t.session_commits==2  # Existing reserve + separate final transport-intent transaction.
        # This hook never marks an actual POST. The transport does that next.
        assert not actual.transport_started
        with pytest.raises(ModelBlocked):spend.before_transport()
    assert spend.current_scope() is None
    assert t.locks[:4]==[(Task,True),(Plan,True),(Job,True),(Invocation,True)]


@pytest.mark.parametrize('fault',['live-denial','wrong-scope-token','wrong-execution','wrong-message-hash',
    'wrong-dispatch-hash','sdk-unknown','job-expired'])
def test_current_identity_source_lease_and_exact_hash_fail_before_cloud_budget_io(scene,fault):
    t=scene
    if fault=='live-denial':t.live_allowed=False
    elif fault=='wrong-scope-token':t.refs['lease_token']=uid()
    elif fault=='wrong-execution':t.refs['execution_hash']='0'*64
    elif fault=='wrong-message-hash':t.message_hash='0'*64
    elif fault=='wrong-dispatch-hash':t.dispatch_identity['request_hash']='0'*64
    elif fault=='sdk-unknown':t.call.status='unknown'
    else:t.send_job.lease_until=t.clock-timedelta(seconds=1)
    with attempt(t):
        with pytest.raises((ModelBlocked,HTTPException)):reserve(t)
    assert not events(t,spend.HOLD) and t.rows[CloudCall]==[]


@pytest.mark.parametrize('orphan',['independent-hold','transport-intent','ledger-no-marker','ledger-no-dispatch'])
def test_reverse_surviving_receipts_never_mean_safe_to_reserve_again(scene,orphan):
    t=scene
    if orphan in {'independent-hold','transport-intent'}:
        t.rows[Audit].append(Audit(id=uid(),workspace_id=t.p.workspace_id,actor_id=t.p.user_id,
            action=spend.HOLD if orphan=='independent-hold' else spend.INTENT,
            object_id=t.plan.id,data={'job_id':t.send_job.id}))
    else:
        data={'actor_id':t.p.user_id,'request_hash':t.message_hash}
        if orphan=='ledger-no-marker':
            data['gateway_dispatch']={'identity':{'suggestion_id':t.call.id}}
        t.rows[CloudCall].append(CloudCall(id=uid(),workspace_id=t.p.workspace_id,
            capability_id=t.model.id,status='failed',reserved_tokens=t.bound,charged_tokens=t.bound,data=data))
    with attempt(t):
        with pytest.raises(ModelBlocked):reserve(t)
    assert not events(t,spend.HOLD) or orphan=='independent-hold'


@pytest.mark.parametrize('fault',['missing-hold','missing-marker','changed-amount','missing-reserved-sdk-audit',
    'SDK-not-bound','current-denial','expired-lease','daily-budget-mismatch'])
def test_before_http_requires_original_hold_ledger_durable_sdk_and_current_qualification(scene,fault):
    t=scene
    with attempt(t):
        reserve(t);t.reserved();before=len(events(t,spend.INTENT))
        if fault=='missing-hold':t.rows[Audit].remove(events(t,spend.HOLD)[0])
        elif fault=='missing-marker':t.ledger.data.pop(spend.KEY)
        elif fault=='changed-amount':events(t,spend.HOLD)[0].data['reserved_minor_units']=0
        elif fault=='missing-reserved-sdk-audit':
            t.rows[Audit]=[row for row in t.rows[Audit] if row.action!=runtime.RESERVED]
        elif fault=='SDK-not-bound':t.call.data[runtime.STATE_KEY]['call_id']=uid()
        elif fault=='current-denial':t.live_allowed=False
        elif fault=='expired-lease':t.send_job.lease_until=t.clock-timedelta(seconds=1)
        else:t.daily.data['tokens']=0
        with pytest.raises((ModelBlocked,HTTPException)):spend.before_transport()
        assert len(events(t,spend.INTENT))==before


def test_reservation_context_cannot_be_bound_after_its_original_transaction_or_scope(scene):
    t=scene
    with attempt(t):
        reserve(t)
        with pytest.raises(ModelBlocked):spend.bind_reservation(t.db,t.p,t.model,t.ledger,t.atomic_context)
    with spend.scope(**t.refs):
        with pytest.raises(ModelBlocked):spend.bind_reservation(t.db,t.p,t.model,t.ledger,t.atomic_context)


def test_closed_copied_context_does_not_fall_back_to_legacy_and_nested_scope_is_refused(scene):
    t=scene
    with spend.scope(**t.refs):
        copied=copy_context()
        with pytest.raises(ModelBlocked):
            with spend.scope(**t.refs):pass
    with pytest.raises(ModelBlocked):copied.run(spend.current_scope)
    assert spend.current_scope() is None


def test_foreign_async_owner_cannot_spend_an_inherited_scope(scene):
    import asyncio
    t=scene
    async def trial():
        with spend.scope(**t.refs):
            async def child():
                with pytest.raises(ModelBlocked):spend.current_scope()
            await asyncio.create_task(child())
    asyncio.run(trial())


def test_actual_quota_reader_never_treats_an_unbacked_budget_ref_as_a_real_policy(scene):
    t=scene
    with pytest.raises(ModelBlocked):ACTUAL_POLICY(t.db,t.p,t.context,t.clock)
    reference=t.prep['budget']['quota_policy_ref']
    t.rows[Audit].append(Audit(id=reference['id'],workspace_id=t.p.workspace_id,actor_id=t.p.user_id,
        action=spend.store.QUOTA_POLICY,object_id=t.task.id,created_at=t.clock,data={'plausible_marker':True}))
    with pytest.raises(ModelBlocked):ACTUAL_POLICY(t.db,t.p,t.context,t.clock)


def history_financial_io(monkeypatch,t):
    # Actual protected policy/official-price Audit decoding is its own IO
    # boundary here. Scope/ledger/HOLD/INTENT reconstruction below is real.
    monkeypatch.setattr(spend,'_original_financials',lambda *args:(
        copy.deepcopy(t.prep['budget']),copy.deepcopy(t.context['quote'])))
    monkeypatch.setattr(spend,'_live',lambda *args:pytest.fail('paid history must not reacquire current authority'))
    monkeypatch.setattr(spend.prices,'current',lambda *args,**kwargs:pytest.fail('paid history must not renew prices'))


def test_paid_historical_hold_uses_original_sdk_creator_not_reclaimed_job_token_or_role(scene,monkeypatch):
    t=scene
    with attempt(t):reserve(t);t.reserved();spend.before_transport()
    original=copy.deepcopy(t.ledger.data)
    t.send_job.status='succeeded';t.send_job.lease_token=uid();t.send_job.lease_until=None
    t.call.status='succeeded';t.ledger.status='succeeded';t.ledger.charged_tokens=100
    t.daily.data['tokens']=100  # Real settlement may already release token excess.
    history_financial_io(monkeypatch,t)
    viewer=Principal(uid(),t.p.workspace_id,'viewer','historical-reader-IO')
    result=spend.checked_original_hold(t.db,viewer,t.context,t.ledger)
    assert result['reserved_minor_units']==t.context['quote']['maximum_cost_minor_units']
    assert result['provider_bill_verified'] is result['money_released'] is result['cloud_send_authorized'] is False
    assert t.ledger.data==original and spend.current_scope() is None


@pytest.mark.parametrize('fault',['hold-deleted','intent-deleted','marker-deleted','amount-rewritten',
    'hold-time-rewritten','intent-time-rewritten'])
def test_historical_proof_damage_blocks_adoption_without_new_reserve_or_deleting_paid_state(scene,monkeypatch,fault):
    t=scene
    with attempt(t):reserve(t);t.reserved();spend.before_transport()
    if fault=='hold-deleted':t.rows[Audit].remove(events(t,spend.HOLD)[0])
    elif fault=='intent-deleted':t.rows[Audit].remove(events(t,spend.INTENT)[0])
    elif fault=='marker-deleted':t.ledger.data.pop(spend.KEY)
    elif fault=='amount-rewritten':events(t,spend.HOLD)[0].data['reserved_minor_units']=0
    elif fault=='hold-time-rewritten':
        event=events(t,spend.HOLD)[0];event.data['reserved_at']=(event.created_at+timedelta(seconds=1)).isoformat()
    else:
        event=events(t,spend.INTENT)[0];event.data['checked_at']=(event.created_at+timedelta(seconds=1)).isoformat()
    history_financial_io(monkeypatch,t)
    counts={model:len(rows) for model,rows in t.rows.items()}
    with pytest.raises(ModelBlocked):spend.checked_original_hold(t.db,t.p,t.context,t.ledger)
    assert {model:len(rows) for model,rows in t.rows.items()}==counts

