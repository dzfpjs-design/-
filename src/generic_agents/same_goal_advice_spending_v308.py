"""Fixed same-Goal amount holds inside the ONE real cloud reservation.

No JSON marker supplies a scope or current authority. The fixed live runtime
rechecks the real Start/SDK/Job/source/lease; this module verifies its actual
quota, price, dispatch and CloudCall ledgers. Unknown outcomes consume the
single hold. No refund, second reservation/POST or provider-bill claim exists.
"""
import asyncio
import copy
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from pydantic import TypeAdapter
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import cloud_dispatch_v130 as dispatch
from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_money_v305 as money
from . import same_goal_advice_price_v307 as prices
from . import same_goal_advice_quota_v304 as quota
from . import same_goal_advice_store_v302 as store
from .contracts import digest
from .models import Audit, Capability, CloudCall, Invocation, Job, Membership, Plan, Task, User, uid
from .security import Principal

KEY = 'same_goal_advice_spending_v308'
SCHEMA = 'same-goal-advice-money-reservation-v308'
INTENT_SCHEMA = 'same-goal-advice-money-transport-intent-v308'
HOLD = 'same_goal_advice.money_reserved_v308'
INTENT = 'same_goal_advice.money_transport_entered_v308'
ERROR = '原业务建议的一次金额预留、价格、实际调用或当前派发租约无法核对；未重复预留或发送'
_SCOPE_KEYS = frozenset({'workspace_id','actor_id','task_id','plan_id','job_id',
    'invocation_id','execution_hash','lease_token'})
_CURRENT = ContextVar('same_goal_advice_spending_v308', default=None)
_WITNESS = object()


def need(value):
    if not value:
        from .gateway import ModelBlocked
        raise ModelBlocked(ERROR)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try: return function(*args, **kwargs)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration, OverflowError):
            need(False)
    return wrapped


def _owner():
    try: task = asyncio.current_task()
    except RuntimeError: task = None
    return threading.get_ident(), task


@dataclass(slots=True)
class _Scope:
    refs: dict = field(repr=False)
    owner: tuple = field(repr=False)
    closed: bool = False


@dataclass(slots=True)
class _Reservation:
    witness: object = field(repr=False)
    scope: _Scope = field(repr=False)
    db: object = field(repr=False)
    transaction: object = field(repr=False)
    data: dict = field(repr=False)
    dispatch_identity: dict = field(repr=False)
    attempt_id: str
    bound: bool = False


def _active():
    value = _CURRENT.get()
    if value is None: return None
    need(type(value) is _Scope and not value.closed and value.owner == _owner())
    return value


def current_scope():
    """Server-only refs, including the installed owner token; never a DTO."""
    value = _active()
    return copy.deepcopy(value.refs) if value is not None else None


@contextmanager
def scope(workspace_id, actor_id, task_id, plan_id, job_id, invocation_id, execution_hash, lease_token):
    need(_CURRENT.get() is None)
    refs = {'workspace_id':workspace_id,'actor_id':actor_id,'task_id':task_id,'plan_id':plan_id,
        'job_id':job_id,'invocation_id':invocation_id,'execution_hash':execution_hash,'lease_token':lease_token}
    for key, value in refs.items():
        TypeAdapter(c.Hash if key == 'execution_hash' else c.ID).validate_python(value)
    state = _Scope(copy.deepcopy(refs), _owner())
    token = _CURRENT.set(state)
    try: yield
    finally:
        state.closed = True
        _CURRENT.reset(token)


def _clock(db):
    value = db.scalar(select(func.clock_timestamp()))
    need(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _row(db, p, model, identity, *, lock=False):
    query = select(model).where(model.workspace_id == p.workspace_id, model.id == identity)
    if lock: query = query.with_for_update()
    value = db.scalar(query.execution_options(populate_existing=True))
    need(value is not None)
    return value


def _locks(db, p, refs):
    # Must run before cloud_budget acquires its daily-budget Job lock.
    return [_row(db,p,model,refs[key],lock=True) for model,key in
        ((Task,'task_id'),(Plan,'plan_id'),(Job,'job_id'),(Invocation,'invocation_id'))]


def _live(db,p,cap,bound,request_hash):
    # Only this literal server module may supply current authority.
    from .same_goal_advice_live_v308 import spending_context
    return spending_context(db,p,cap,bound,request_hash)


def _policy(db,p,context,now):
    prep,snapshot,task = context['prep'],context['snapshot'],context['task']
    budget = c._parse(c.Budget,snapshot['preview']['budget'])
    reference = budget['quota_policy_ref']
    event = _row(db,p,Audit,reference['id'],lock=True)
    need(event.action == store.QUOTA_POLICY and event.object_id == task.id
        and digest(event.data) == reference['record_hash'])
    value,actual = quota._policy_shape(event,now)
    expected = {key:item for key,item in prep['scope'].items() if key != 'analysis_plan_id'}
    need(value['scope'] == expected and value['source_business_capability_ref'] == prep['source_business_capability_ref']
        and value['advice_capability_ref'] == prep['advice_capability_ref'] and value['model_pin'] == prep['model_pin']
        and quota.instant(value['expires_at']) > now and digest(actual) == digest(budget)
        and digest(prep['budget']) == digest(budget)
        and budget['max_actual_calls'] == budget['reserved_calls'] == budget['reserved_steps'] == 1)
    store._member(db,p,event.actor_id,admin=True)
    return budget


def _dispatch(p,cap,refs,context,*,reserved):
    attempt = dispatch.current_attempt()
    need(attempt is not None and not attempt.transport_started
        and attempt.stage == ('authorization' if reserved else 'reservation')
        and attempt.reservation_state == ('confirmed' if reserved else 'unknown'))
    identity = {'suggestion_id':refs['invocation_id'],'job_id':refs['job_id'],'task_id':refs['task_id'],
        'request_hash':context['frozen_request_hash'],'workspace_id':p.workspace_id,
        'actor_id':p.user_id,'capability_id':cap.id}
    need(attempt.identity == identity)
    TypeAdapter(c.ID).validate_python(attempt.attempt_id)
    TypeAdapter(c.Hash).validate_python(context['frozen_request_hash'])
    need((type(attempt.call_id) is str and bool(attempt.call_id)) if reserved else attempt.call_id is None)
    return attempt,identity


def _context(db,p,cap,bound,request_hash,*,reserved):
    active = _active(); need(active is not None)
    refs = active.refs
    need(type(p) is Principal and p.workspace_id == refs['workspace_id'] and p.user_id == refs['actor_id'])
    _locks(db,p,refs)
    value = _live(db,p,cap,bound,request_hash)
    task,plan,job,sdk = (value[key] for key in ('task','plan','job','invocation'))
    snapshot,prep,start,preview = (value[key] for key in ('snapshot','prep','start','preview'))
    now = _clock(db)
    need(task.id == refs['task_id'] and plan.id == refs['plan_id'] and plan.task_id == task.id
        and job.id == refs['job_id'] and sdk.id == refs['invocation_id']
        and all(row.workspace_id == p.workspace_id for row in (task,plan,job,sdk,start,preview))
        and snapshot['record_hash'] == refs['execution_hash'] == c.record_hash(snapshot)
        and snapshot['scope'] == prep['scope']
        and sdk.status == job.status == 'running' and job.kind == c.SEND_KIND
        and job.data['actor_id'] == p.user_id and job.data['task_id'] == task.id and job.data['plan_id'] == plan.id
        and job.lease_token == sdk.data['lease_token'] == refs['lease_token']
        and job.lease_until is not None and job.lease_until > now
        and sdk.data['job_id'] == job.id and sdk.data['role'] == 'business'
        and sdk.capability_id == prep['advice_capability_ref']['id']
        and sdk.data['capability_hash'] == prep['advice_capability_ref']['content_hash']
        == digest(sdk.data['capability_snapshot'])
        and value['model'].id == cap.id == prep['model_pin']['id']
        and cap.content_hash == prep['model_pin']['content_hash'] == digest(cap.data))
    inputs = c._parse(c.InvocationInput,sdk.data['inputs'])
    need(inputs == {'schema':c.INPUT,'plan_ref':{'id':plan.id,'record_hash':snapshot['record_hash']},
        'preview_hash':snapshot['preview']['preview_hash']} and sdk.data['input_hash'] == digest(inputs))
    actual_start = _row(db,p,Audit,start.id)
    need(actual_start.action == 'same_goal_advice.started_v308' and actual_start.object_id == plan.id
        and actual_start.data['record_hash'] == c.record_hash(actual_start.data)
        and digest(actual_start.data['snapshot']) == digest(snapshot)
        and actual_start.data['job_ref']['id'] == job.id
        and actual_start.data['preview_ref'] == {'id':preview.id,'record_hash':preview.data['record_hash']}
        and digest(preview.data['preview_record']) == digest(snapshot['preview']))
    budget = _policy(db,p,value,now)
    price = prices.current(db,p,prep['model_pin'],basis_ref=value['price_ref'])
    need(price['basis_ref'] == value['price_ref'])
    quote = money.check_quote(value['quote'],price['price'],now=now,prepared=value['prepared'],
        model_pin=prep['model_pin'],model_snapshot=cap.data,budget=budget)
    need(type(bound) is int and bound == value['prepared']['token_bound'] == quote['total_token_bound']
        and request_hash == value['prepared']['request_hash'] == quote['request_hash'])
    attempt,identity = _dispatch(p,cap,refs,value,reserved=reserved)
    return value,budget,quote,attempt,identity


def _inventory(db,p,refs,context,budget):
    policy = budget['quota_policy_ref']['id']; start = context['start'].id
    events = store._rows(db,p,Audit,Audit.action.in_([HOLD,INTENT]),or_(
        Audit.object_id == refs['plan_id'],Audit.data['invocation_id'].as_string() == refs['invocation_id'],
        Audit.data['job_id'].as_string() == refs['job_id'],Audit.data['start_ref']['id'].as_string() == start,
        Audit.data['quota_policy_ref']['id'].as_string() == policy))
    calls = store._rows(db,p,CloudCall,or_(
        CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string() == refs['invocation_id'],
        CloudCall.data['gateway_dispatch']['identity']['job_id'].as_string() == refs['job_id'],
        CloudCall.data[KEY]['plan_id'].as_string() == refs['plan_id'],
        CloudCall.data[KEY]['quota_policy_ref']['id'].as_string() == policy,
        (CloudCall.capability_id == context['prep']['model_pin']['id'])
            & (CloudCall.data['request_hash'].as_string() == context['prepared']['request_hash'])))
    return events,calls


@checked
def prepare_reservation(db,p,cap,token_bound,request_hash):
    active = _active()
    if active is None: return None
    with db.no_autoflush:
        context,budget,quote,attempt,identity = _context(db,p,cap,token_bound,request_hash,reserved=False)
        events,calls = _inventory(db,p,active.refs,context,budget)
        need(not events and not calls)
        value = {**context,'spending_budget':budget,'spending_quote':quote}
        return _Reservation(_WITNESS,active,db,db.get_transaction(),value,identity,attempt.attempt_id)


def _ledger(db,p,cap,call,context,identity,attempt_id):
    actual = _row(db,p,CloudCall,call.id,lock=True)
    prepared = context['prepared']
    need(actual is call and actual.status == 'reserved' and actual.workspace_id == p.workspace_id
        and actual.capability_id == cap.id and type(actual.reserved_tokens) is int
        and actual.reserved_tokens == actual.charged_tokens == prepared['token_bound']
        and actual.data['actor_id'] == p.user_id and actual.data['request_hash'] == prepared['request_hash'])
    expected = {'schema':dispatch.SCHEMA,'identity':identity,'attempt_id':attempt_id,
        'reservation_request_hash':prepared['request_hash']}
    need(digest(actual.data['gateway_dispatch']) == digest(expected)
        and actual.data.get('dispatch_options_hash',prepared['dispatch_options_hash']) == prepared['dispatch_options_hash'])
    day = actual.data['day']
    need(type(day) is str and date.fromisoformat(day).isoformat() == day)
    daily = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,Job.kind == 'cloud_budget',
        Job.idempotency_key == 'cloud-budget:'+day).with_for_update().execution_options(populate_existing=True))
    need(daily is not None and daily.status == 'internal' and daily.data['day'] == day
        and type(daily.data['tokens']) is int and daily.data['tokens'] >= actual.reserved_tokens
        and type(daily.data['calls']) is int and daily.data['calls'] >= 1)
    return actual


def _hold(context,call,refs,identity,attempt_id,at):
    # PostgreSQL returns the same timestamptz instant in each session's time
    # zone. The original reservation is written by _clock in UTC, so rebuild
    # its immutable bytes in UTC too; do not rewrite the saved Audit or ledger.
    need(type(at) is datetime and at.tzinfo is not None and at.utcoffset() is not None)
    at = at.astimezone(timezone.utc)
    quote,budget = context['spending_quote'],context['spending_budget']
    return {'schema':SCHEMA,'workspace_id':refs['workspace_id'],'actor_id':refs['actor_id'],
        'task_id':refs['task_id'],'plan_id':refs['plan_id'],'job_id':refs['job_id'],'invocation_id':refs['invocation_id'],
        'execution_hash':refs['execution_hash'],'creator_lease_hash':digest(refs['lease_token']),
        'start_ref':{'id':context['start'].id,'record_hash':digest(context['start'].data)},
        'preview_ref':{'id':context['preview'].id,'record_hash':context['preview'].data['record_hash']},
        'quota_policy_ref':copy.deepcopy(budget['quota_policy_ref']),'price_basis_ref':copy.deepcopy(context['price_ref']),
        'model_pin':copy.deepcopy(context['prep']['model_pin']),'cloud_call_id':call.id,
        'dispatch_identity':copy.deepcopy(identity),'attempt_id':attempt_id,
        'reservation_request_hash':context['prepared']['request_hash'],
        'dispatch_options_hash':context['prepared']['dispatch_options_hash'],
        'prepared_hash':digest(context['prepared']),'quote_hash':quote['record_hash'],
        'currency':quote['currency'],'reserved_minor_units':quote['maximum_cost_minor_units'],
        'configured_ceiling_minor_units':budget['max_cost_minor_units'],
        'amount_basis':'conservative_quote_upper_bound','provider_bill_verified':False,'reserved_at':at.isoformat()}


@checked
def bind_reservation(db,p,cap,call,context):
    active = _active()
    if active is None:
        need(context is None); return None
    need(type(context) is _Reservation and context.witness is _WITNESS and context.scope is active
        and context.db is db and context.transaction is db.get_transaction() and not context.bound)
    with db.no_autoflush:
        value = context.data; budget = value['spending_budget']; refs = active.refs
        attempt,identity = _dispatch(p,cap,refs,value,reserved=False)
        need(identity == context.dispatch_identity and attempt.attempt_id == context.attempt_id)
        actual = _ledger(db,p,cap,call,value,identity,attempt.attempt_id)
        events,calls = _inventory(db,p,refs,value,budget)
        need(not events and len(calls) == 1 and calls[0].id == call.id and KEY not in actual.data)
        now = _clock(db); need(value['job'].lease_token == refs['lease_token'] and value['job'].lease_until > now)
        record = _hold(value,actual,refs,identity,attempt.attempt_id,now)
        event = Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,
            action=HOLD,object_id=refs['plan_id'],created_at=now,data=record)
        db.add(event)
        actual.data = {**actual.data,'dispatch_options_hash':value['prepared']['dispatch_options_hash'],
            KEY:{'schema':SCHEMA,'plan_id':refs['plan_id'],'quota_policy_ref':budget['quota_policy_ref'],
                'hold_ref':{'id':event.id,'record_hash':digest(record)},'intent_ref':None}}
        context.bound = True


def _principal(db,refs):
    row = db.execute(select(User.id,Membership.role).join(Membership,Membership.user_id == User.id).where(
        User.id == refs['actor_id'],User.active.is_(True),Membership.workspace_id == refs['workspace_id'],
        Membership.role.in_(['admin','operator']))).one_or_none()
    need(row is not None)
    return Principal(refs['actor_id'],refs['workspace_id'],row.role,'worker-spending-v308')


@checked
def before_transport():
    active = _active()
    if active is None: return None
    from .db import engine
    from . import same_goal_advice_runtime_v308 as sender
    refs = active.refs
    with Session(engine()) as db:
        p = _principal(db,refs)
        attempt = dispatch.current_attempt(); need(attempt is not None)
        locked = _locks(db,p,refs)
        model = _row(db,p,Capability,attempt.identity['capability_id'])
        # Take only the locator/bounds from the real SDK. The fixed live
        # resolver below authenticates its complete frozen request and Audits.
        saved_prepared = locked[-1].data[sender.STATE_KEY]['request']['prepared']
        context,budget,quote,attempt,identity = _context(db,p,model,
            saved_prepared['token_bound'],saved_prepared['request_hash'],reserved=True)
        call = _row(db,p,CloudCall,attempt.call_id,lock=True)
        actual = _ledger(db,p,model,call,context,identity,attempt.attempt_id)
        events,calls = _inventory(db,p,refs,context,budget)
        holds = [event for event in events if event.action == HOLD]
        need(len(holds) == 1 and not [event for event in events if event.action == INTENT]
            and len(calls) == 1 and calls[0].id == actual.id)
        event = holds[0]
        checked_context = {**context,'spending_budget':budget,'spending_quote':quote}
        expected = _hold(checked_context,actual,refs,identity,attempt.attempt_id,event.created_at)
        need(event.object_id == refs['plan_id'] and event.actor_id == p.user_id
            and digest(event.data) == digest(expected)
            and actual.data[KEY] == {'schema':SCHEMA,'plan_id':refs['plan_id'],'quota_policy_ref':budget['quota_policy_ref'],
                'hold_ref':{'id':event.id,'record_hash':digest(expected)},'intent_ref':None})
        sdk = context['invocation']; state = sdk.data[sender.STATE_KEY]
        need(state['call_id'] == actual.id and state['capture'] is None)
        reserved = store._rows(db,p,Audit,Audit.action == sender.RESERVED,Audit.object_id == sdk.id)
        need(len(reserved) == 1 and reserved[0].actor_id == p.user_id
            and digest(reserved[0].data) == digest({**sender._anchor(state['request']),'call_id':actual.id}))
        now = _clock(db)
        need(context['job'].lease_token == refs['lease_token'] and context['job'].lease_until > now)
        data = {'schema':INTENT_SCHEMA,'workspace_id':p.workspace_id,'actor_id':p.user_id,
            'task_id':refs['task_id'],'plan_id':refs['plan_id'],'job_id':refs['job_id'],'invocation_id':sdk.id,
            'execution_hash':refs['execution_hash'],'current_lease_hash':digest(refs['lease_token']),
            'start_ref':expected['start_ref'],'quota_policy_ref':budget['quota_policy_ref'],
            'hold_ref':{'id':event.id,'record_hash':digest(expected)},'cloud_call_id':actual.id,
            'dispatch_identity':identity,'attempt_id':attempt.attempt_id,'checked_at':now.isoformat(),
            'transport_outcome':'not-yet-known','provider_bill_verified':False}
        intent = Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,
            action=INTENT,object_id=refs['plan_id'],created_at=now,data=data)
        db.add(intent)
        actual.data = {**actual.data,KEY:{**actual.data[KEY],
            'intent_ref':{'id':intent.id,'record_hash':digest(data)}}}
        db.commit()


def _original_financials(db,p,context,at):
    """Protected original financial events; no current role, price, or files."""
    prep,snapshot,task = context['prep'],context['snapshot'],context['task']
    budget = c._parse(c.Budget,snapshot['preview']['budget'])
    policy = _row(db,p,Audit,budget['quota_policy_ref']['id'])
    need(policy.action == store.QUOTA_POLICY and policy.object_id == task.id
        and digest(policy.data) == budget['quota_policy_ref']['record_hash'])
    original,actual = quota._policy_shape(policy,at)
    need(digest(actual) == digest(budget) and digest(prep['budget']) == digest(budget)
        and original['scope'] == {key:value for key,value in prep['scope'].items() if key != 'analysis_plan_id'}
        and original['source_business_capability_ref'] == prep['source_business_capability_ref']
        and original['advice_capability_ref'] == prep['advice_capability_ref']
        and original['model_pin'] == prep['model_pin'] and quota.instant(original['expires_at']) > at)
    event = _row(db,p,Audit,context['price_ref']['id'])
    value = prices._parse(prices.Registration,event.data)
    priced_at = context['preview'].created_at
    need(event.action == prices.PRICE_BASIS and event.object_id == prep['model_pin']['id']
        and digest(event.data) == context['price_ref']['record_hash']
        and event.actor_id == value['actor_id'] and value['workspace_id'] == p.workspace_id
        and value['model_pin'] == value['body']['model_pin'] == prep['model_pin']
        and value['body_hash'] == digest(value['body']) and value['request_id'] == value['body']['request_id']
        and event.created_at is not None and event.created_at.tzinfo is not None
        and priced_at is not None and priced_at.tzinfo is not None and event.created_at <= priced_at <= at
        and prices._time(value['http_receipt']['requested_at']) <= prices._time(value['checked_at'])
        and value['http_receipt']['fetched_at'] == value['checked_at']
        and prices._time(value['checked_at']) <= event.created_at
        and prices._time(value['expires_at']) == prices._time(value['checked_at'])
            + timedelta(seconds=value['body']['validity_seconds']))
    original_ref = value['official_evidence_ref']; rates = value['parsed_rates']; receipt = value['http_receipt']
    need(receipt['sha256'] == original_ref['sha256'] and receipt['size_bytes'] == original_ref['size_bytes']
        and receipt['content_type'].split(';',1)[0].strip().lower() == 'text/html')
    unsigned = {'schema':money.PRICE,'basis_ref':context['price_ref'],
        'official_original':{'url':prices.URL,'sha256':receipt['sha256'],'size_bytes':receipt['size_bytes']},
        'provider':'deepseek','model':money.MODEL,'currency':'CNY','rate_unit_tokens':1_000_000,
        'input_cache_miss_minor_per_million':rates['input_cache_miss_minor_per_million'],
        'output_minor_per_million':rates['output_minor_per_million'],
        'cache_discount_assumed':False,'off_peak_discount_assumed':False,
        'checked_at':value['checked_at'],'expires_at':value['expires_at'],
        'database_price_authorization_verified':False,'provider_price_lock_guaranteed':False}
    original_price = money.seal_price(unsigned,now=priced_at)
    preview = context['preview'].data
    need(digest(original_price) == digest(preview['price_snapshot'])
        and context['price_ref'] == preview['price_basis_ref']
        and digest(context['quote']) == digest(preview['money_quote'])
        and money._instant(context['quote']['quoted_at']) == priced_at)
    quote = money.check_quote(context['quote'],original_price,now=priced_at,prepared=context['prepared'],
        model_pin=prep['model_pin'],model_snapshot=preview['model_snapshot'],budget=budget)
    return budget,quote


def _intent_data(context,refs,hold,hold_value,call,identity,attempt_id,at):
    # Intent Audit timestamps are written by _clock in UTC, but the original
    # timestamptz may be returned in the reader session's local time zone.
    need(type(at) is datetime and at.tzinfo is not None and at.utcoffset() is not None)
    at = at.astimezone(timezone.utc)
    return {'schema':INTENT_SCHEMA,'workspace_id':refs['workspace_id'],'actor_id':refs['actor_id'],
        'task_id':refs['task_id'],'plan_id':refs['plan_id'],'job_id':refs['job_id'],
        'invocation_id':refs['invocation_id'],'execution_hash':refs['execution_hash'],
        'current_lease_hash':digest(refs['lease_token']),'start_ref':hold_value['start_ref'],
        'quota_policy_ref':context['spending_budget']['quota_policy_ref'],
        'hold_ref':{'id':hold.id,'record_hash':digest(hold_value)},'cloud_call_id':call.id,
        'dispatch_identity':identity,'attempt_id':attempt_id,'checked_at':at.isoformat(),
        'transport_outcome':'not-yet-known','provider_bill_verified':False}


@checked
def checked_original_hold(db,p,context,cloud_call,*,require_intent=True):
    """Post-capture/adoption/settlement custody ONLY, never a sending guard.

    Context must come from RootLive.historical's actual Start/Preview/SDK proof.
    Do not put this before raw first-answer storage: missing money evidence may
    block adoption, but may never discard a paid raw or trigger another POST.
    Original financial Audits anchor the old quote; no current IAM, lease,
    official file, selected price, refund or provider invoice is inferred here.
    """
    need(type(require_intent) is bool)
    with db.no_autoflush:
        task,plan,job,sdk = (context[key] for key in ('task','plan','job','invocation'))
        need(all(row.workspace_id == p.workspace_id for row in (task,plan,job,sdk))
            and plan.task_id == task.id and job.data['task_id'] == task.id and job.data['plan_id'] == plan.id
            and sdk.data['job_id'] == job.id and context['snapshot']['record_hash'] == c.record_hash(context['snapshot']))
        refs = {'workspace_id':p.workspace_id,'actor_id':job.data['actor_id'],'task_id':task.id,
            'plan_id':plan.id,'job_id':job.id,'invocation_id':sdk.id,
            'execution_hash':context['snapshot']['record_hash'],'lease_token':sdk.data['lease_token']}
        call = _row(db,p,CloudCall,cloud_call.id)
        need(call.capability_id == context['prep']['model_pin']['id']
            and call.status in {'reserved','failed','succeeded'} and type(call.reserved_tokens) is int
            and call.reserved_tokens == context['prepared']['token_bound']
            and call.data['actor_id'] == refs['actor_id']
            and call.data['request_hash'] == context['prepared']['request_hash']
            and call.data['dispatch_options_hash'] == context['prepared']['dispatch_options_hash'])
        budget = c._parse(c.Budget,context['snapshot']['preview']['budget'])
        stock,calls = _inventory(db,p,refs,context,budget)
        holds = [row for row in stock if row.action == HOLD]; intents = [row for row in stock if row.action == INTENT]
        need(len(holds) == 1 and len(intents) <= 1 and (not require_intent or len(intents) == 1)
            and len(calls) == 1 and calls[0].id == call.id)
        hold = holds[0]; at = hold.created_at
        need(at is not None and at.tzinfo is not None and at <= _clock(db))
        actual_budget,quote = _original_financials(db,p,context,at)
        value = {**context,'spending_budget':actual_budget,'spending_quote':quote}
        identity = {'suggestion_id':sdk.id,'job_id':job.id,'task_id':task.id,
            'request_hash':context['frozen_request_hash'],'workspace_id':p.workspace_id,
            'actor_id':refs['actor_id'],'capability_id':call.capability_id}
        gateway = call.data['gateway_dispatch']
        need(set(gateway) == {'schema','identity','attempt_id','reservation_request_hash'}
            and gateway['schema'] == dispatch.SCHEMA and gateway['identity'] == identity
            and gateway['reservation_request_hash'] == context['prepared']['request_hash'])
        TypeAdapter(c.ID).validate_python(gateway['attempt_id'])
        expected = _hold(value,call,refs,identity,gateway['attempt_id'],at)
        need(hold.actor_id == refs['actor_id'] and hold.object_id == plan.id
            and digest(hold.data) == digest(expected))
        marker = {'schema':SCHEMA,'plan_id':plan.id,'quota_policy_ref':actual_budget['quota_policy_ref'],
            'hold_ref':{'id':hold.id,'record_hash':digest(expected)},'intent_ref':None}
        if intents:
            intent = intents[0]
            need(intent.created_at is not None and intent.created_at.tzinfo is not None
                and at <= intent.created_at <= _clock(db) and intent.actor_id == refs['actor_id']
                and intent.object_id == plan.id)
            data = _intent_data(value,refs,hold,expected,call,identity,gateway['attempt_id'],intent.created_at)
            need(digest(intent.data) == digest(data))
            marker['intent_ref'] = {'id':intent.id,'record_hash':digest(data)}
        need(call.data[KEY] == marker)
        return {'schema':SCHEMA+'/original','purpose':'historical-financial-custody-not-send-permission',
            'hold_ref':copy.deepcopy(marker['hold_ref']),'intent_ref':copy.deepcopy(marker['intent_ref']),
            'cloud_call_id':call.id,'currency':expected['currency'],
            'reserved_minor_units':expected['reserved_minor_units'],
            'amount_basis':'conservative_quote_upper_bound','provider_bill_verified':False,
            'money_released':False,'cloud_send_authorized':False}


