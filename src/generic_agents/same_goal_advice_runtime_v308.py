"""Fixed Plan-only paid advice protocol and same-answer worker recovery.

Live authority belongs to same_goal_advice_live_v308. This protocol uses the
shared first-HTTP capture journal, real Gateway Invocation and actual ledger.
Historical parsing never grants another POST, permission or Goal acceptance.
"""
import base64
import copy
import hashlib
import sys
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import business_reasoning_v197 as journal
from . import business_goal_ready_runtime_v288 as receipts
from . import cloud_business_reasoning_v197 as transport
from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_execution_v308 as execution
from . import same_goal_advice_projection_v307 as projection
from . import same_goal_advice_registration_v304 as registration
from . import same_goal_advice_store_v302 as store
from . import leases
from .business_flow_review_store import _bytes, _same
from .contracts import digest
from .models import Audit, Capability, CloudCall, Invocation, Job, Plan, Task, uid
from .relations import lock_current_actor
from .services import audit, scoped

ADAPTER = c.ADAPTER
STATE_KEY = 'same_goal_advice_v308'
STATE = 'same-goal-advice-state-v308'
PREPARED = 'same_goal_advice.prepared_v308'
RESERVED = 'same_goal_advice.reserved_v308'
CAPTURED = 'same_goal_advice.captured_v308'
TERMINAL = 'same_goal_advice.sdk_reconciled_v308'
RESULT_READY = 'same_goal_advice.result_ready_v308'
RESULT_KEY = 'advice_result_v308'
READY = 'same-goal-advice-result-ready-v308'
RECONCILED = 'same-goal-advice-sdk-terminal-v308'
MAX_INPUT_BYTES = 1024 * 1024
MAX_STATE_BYTES = 24 * 1024 * 1024
ERROR = '原业务建议、实际首答、费用或独立执行证明无法核对；原号保留，未重新发送或取得执行权限'
FOREIGN = ('business_reasoning_v197', 'business_decision_v276', 'business_decision_v284',
    'business_goal_ready_v288', 'service_state')
_call, _events = journal._call, journal._events
_cloud_call, _closed_receipt = receipts._cloud_call, receipts._closed_receipt
checked = store.checked


def need(value, message=ERROR, code=409):
    if not value: raise HTTPException(code, message)


def _inputs(value):
    _bytes(value, MAX_INPUT_BYTES)
    return c._parse(c.InvocationInput, value)


def input_schema(): return c.input_schema()
def output_schema(): return c.output_schema()
def model_value_schema(): return c.model_value_schema()
def validate_manifest(db, p, manifest): return registration.validate_manifest(db, p, manifest)


def _current(db, p, cap, inputs, key):
    from .same_goal_advice_live_v308 import current
    return current(db, p, cap, inputs, key)


def qualify_evidence(db, p, task, inputs):
    from .same_goal_advice_live_v308 import qualify
    return qualify(db, p, task, inputs)


def _evidence(db, p, ids, model):
    from .same_goal_advice_live_v308 import evidence
    return evidence(db, p, ids, model)


def evidence_ids(inputs):
    # InvocationInput deliberately contains no client-chosen Evidence IDs.
    need(False, '证据编号只能从原执行快照取得，不能由调用输入替代')


def evidence_ids_from_request(frozen):
    snapshot = frozen['execution_snapshot']; source = snapshot['source_record']
    ids = [source['data_evidence_ref']['evidence_id'], source['document']['evidence_ref']['evidence_id']]
    need(len(set(ids)) == 2 and ids == [pin['evidence_id'] for pin in frozen['evidence_pins']])
    return ids


def _history(db, p, cap, inputs, key, call):
    from .same_goal_advice_live_v308 import historical
    return historical(db, p, cap, inputs, key, call)


def _rebuilt(context, capability_snapshot, model_snapshot):
    prep, snapshot, preview = context['prep'], context['snapshot'], context['preview'].data
    c.check_plan(snapshot, preparation=prep, manifest=capability_snapshot)
    projected = projection.derive(preview['frame_snapshot'], prep,
        evidence_parts=preview['evidence_parts'], evidence_pins=snapshot['preview']['evidence_pins'])
    messages = projection.messages(projected)
    limit = prep['budget']['max_tokens']
    prepared = transport.prepare(SimpleNamespace(data=copy.deepcopy(model_snapshot)), messages,
        model_value_schema(), output_token_limit=limit)
    need(_same(projected, preview['projection']) and _same(projected['cloud_request'], snapshot['preview']['cloud_request'])
        and _same(prepared, preview['prepared']) and _same(model_snapshot, preview['model_snapshot']))
    return projected, messages, prepared, limit


@checked
def prepare_request(db, p, cap, inputs, key):
    value = _inputs(inputs)
    actual, model, task, job, plan, snapshot = _current(db, p, cap, value, key)
    call = _call(db, p, key)
    context = _history(db, p, actual, value, key, call)
    need(context['task'].id == task.id and context['job'].id == job.id and context['plan'].id == plan.id
        and _same(context['snapshot'], snapshot))
    prep = context['prep']; preview = context['preview']; start = context['start']
    projected, messages, prepared, limit = _rebuilt(context, actual.data, model.data)
    frozen = {'schema': STATE + '/request', 'inputs': value, 'preparation': copy.deepcopy(prep),
        'execution_snapshot': copy.deepcopy(snapshot), 'preview_record': copy.deepcopy(preview.data),
        'cloud_projection': projected, 'messages': messages, 'model_value_schema': model_value_schema(),
        'prepared': prepared, 'output_token_limit': limit,
        'evidence_pins': copy.deepcopy(snapshot['preview']['evidence_pins']),
        'capability_snapshot': copy.deepcopy(actual.data), 'model_snapshot': copy.deepcopy(model.data),
        'pin': {'workspace_id': p.workspace_id, 'actor_id': job.data['actor_id'], 'invocation_id': call.id,
            'task_id': task.id, 'job_id': job.id, 'plan_id': plan.id,
            'execution_snapshot_hash': snapshot['record_hash'], 'preparation_snapshot_hash': prep['record_hash'],
            'start_audit_id': start.id, 'start_hash': digest(start.data), 'preview_audit_id': preview.id,
            'preview_record_hash': preview.data['record_hash'], 'preview_hash': snapshot['preview']['preview_hash'],
            'capability_id': actual.id, 'capability_hash': actual.content_hash,
            'model_profile_id': model.id, 'model_profile_hash': model.content_hash,
            'request_key_hash': digest(key), 'input_hash': digest(value),
            'creator_lease_hash': digest(call.data['lease_token'])}}
    frozen['request_hash'] = digest(frozen); _bytes(frozen, MAX_STATE_BYTES)
    return model, frozen


def _anchor(frozen):
    return {'schema': STATE, **copy.deepcopy(frozen['pin']), 'request_hash': frozen['request_hash'],
        'prepared_messages_hash': frozen['prepared']['request_hash'],
        'dispatch_options_hash': frozen['prepared']['dispatch_options_hash'],
        'projection_hash': frozen['cloud_projection']['projection_hash'],
        'mapping_hash': frozen['cloud_projection']['mapping_hash'], 'output_token_limit': frozen['output_token_limit']}


@checked
def _checked_state_for_actor(db, p, cap, inputs, key, call, *, actor_id):
    state = call.data.get(STATE_KEY)
    need(type(state) is dict and set(state) == {'schema', 'request', 'call_id', 'capture'} and state['schema'] == STATE
        and not any(name in call.data for name in FOREIGN))
    frozen = state['request']; _bytes(state, MAX_STATE_BYTES)
    need(type(frozen) is dict and set(frozen) == {'schema', 'inputs', 'preparation', 'execution_snapshot',
        'preview_record', 'cloud_projection', 'messages', 'model_value_schema', 'prepared', 'output_token_limit',
        'evidence_pins', 'capability_snapshot', 'model_snapshot', 'pin', 'request_hash'}
        and frozen['schema'] == STATE + '/request'
        and frozen['request_hash'] == digest({name: item for name, item in frozen.items() if name != 'request_hash'}))
    context = _history(db, p, cap, _inputs(inputs), key, call)
    task, job, plan = context['task'], context['job'], context['plan']
    prep, snapshot = context['prep'], context['snapshot']; preview, start = context['preview'], context['start']
    pin = frozen['pin']; shape = c._manifest(frozen['capability_snapshot'])
    projected, messages, prepared, limit = _rebuilt(context, shape, frozen['model_snapshot'])
    need(_same(frozen['preparation'], prep) and _same(frozen['execution_snapshot'], snapshot)
        and _same(frozen['preview_record'], preview.data) and _same(frozen['cloud_projection'], projected)
        and _same(frozen['messages'], messages) and _same(frozen['prepared'], prepared)
        and _same(frozen['model_value_schema'], model_value_schema()) and frozen['output_token_limit'] == limit
        and _same(frozen['evidence_pins'], snapshot['preview']['evidence_pins'])
        and _same(frozen['inputs'], inputs) and pin == {
            'workspace_id': p.workspace_id, 'actor_id': actor_id, 'invocation_id': call.id,
            'task_id': task.id, 'job_id': job.id, 'plan_id': plan.id,
            'execution_snapshot_hash': snapshot['record_hash'], 'preparation_snapshot_hash': prep['record_hash'],
            'start_audit_id': start.id, 'start_hash': digest(start.data), 'preview_audit_id': preview.id,
            'preview_record_hash': preview.data['record_hash'], 'preview_hash': snapshot['preview']['preview_hash'],
            'capability_id': cap.id, 'capability_hash': digest(shape),
            'model_profile_id': prep['model_pin']['id'], 'model_profile_hash': digest(frozen['model_snapshot']),
            'request_key_hash': digest(key), 'input_hash': digest(inputs),
            'creator_lease_hash': digest(call.data['lease_token'])}
        and shape['execution']['model_profile_id'] == pin['model_profile_id']
        and shape['execution']['model_profile_hash'] == pin['model_profile_hash']
        and cap.id == call.capability_id and call.workspace_id == p.workspace_id
        and cap.content_hash == call.data['capability_hash'] == pin['capability_hash']
        and _same(call.data['capability_snapshot'], shape) and call.data['input_hash'] == pin['input_hash']
        and call.data['job_id'] == job.id and job.data['actor_id'] == actor_id
        and call.data['role'] == 'business' and call.data['side_effect'] is False
        and call.idempotency_key == key == job.idempotency_key
        and _same(call.data['inputs'], inputs) and _same(inputs, execution.input_for(plan, snapshot))
        and _same(call.data['approval'], execution.approval_for(plan, snapshot, job)))
    _events(db, p, call, PREPARED, _anchor(frozen), actor_id=actor_id)
    if state['call_id'] is not None:
        ledger = _cloud_call(db, p, state['call_id'])
        need(ledger.capability_id == pin['model_profile_id'] and ledger.data.get('actor_id') == actor_id
            and ledger.data.get('request_hash') == prepared['request_hash']
            and ledger.data.get('dispatch_options_hash') == prepared['dispatch_options_hash']
            and _same(ledger.data.get('gateway_dispatch', {}).get('identity'), {'suggestion_id': call.id,
                'job_id': job.id, 'task_id': task.id, 'request_hash': frozen['request_hash'],
                'workspace_id': p.workspace_id, 'actor_id': actor_id, 'capability_id': pin['model_profile_id']}))
        _events(db, p, call, RESERVED, {**_anchor(frozen), 'call_id': state['call_id']}, actor_id=actor_id)
    if state['capture'] is not None:
        capture = state['capture']
        need(state['call_id'] is not None and type(capture) is dict and set(capture) == {'raw_base64', 'raw_sha256', 'receipt'})
        raw = base64.b64decode(capture['raw_base64'], validate=True)
        need(hashlib.sha256(raw).hexdigest() == capture['raw_sha256']
            and capture['receipt']['call_id'] == state['call_id']
            and capture['receipt']['provider'] == frozen['model_snapshot']['execution']['provider']
            and capture['receipt']['model'] == frozen['model_snapshot']['execution']['model'])
        _events(db, p, call, CAPTURED, {**_anchor(frozen), 'call_id': state['call_id'],
            'raw_sha256': capture['raw_sha256'], 'receipt_hash': digest(capture['receipt'])}, actor_id=actor_id)
    return copy.deepcopy(state)


def _checked_state(db, p, cap, inputs, key, call):
    return _checked_state_for_actor(db, p, cap, inputs, key, call, actor_id=p.user_id)


@checked
def check_result(frozen, model_value):
    _bytes(model_value, transport.MAX_RESPONSE_BYTES)
    need(_same(frozen['model_value_schema'], model_value_schema()))
    return c.derive_result(frozen['execution_snapshot'], model_value,
        preparation=frozen['preparation'], manifest=frozen['capability_snapshot'])


def execute(db, p, cap, inputs, key):
    from .business_reasoning_capture_v275 import execute as capture
    call = _call(db, p, key)
    if call.data.get(STATE_KEY) is not None:
        return capture(db, p, cap, inputs, key, protocol=sys.modules[__name__])
    actual, _model, task, job, plan, snapshot = _current(db, p, cap, inputs, key)
    owner = leases.ownership(db)
    need(leases.fenced(db) and owner.get('job_id') == job.id and bool(owner.get('lease_token')))
    from .same_goal_advice_spending_v308 import scope
    with scope(workspace_id=p.workspace_id, actor_id=p.user_id, task_id=task.id, plan_id=plan.id,
            job_id=job.id, invocation_id=call.id, execution_hash=snapshot['record_hash'], lease_token=owner['lease_token']):
        return capture(db, p, actual, inputs, key, protocol=sys.modules[__name__])


def _parsed(state):
    need(state['capture'] is not None and state['call_id'] is not None)
    capture = state['capture']; raw = base64.b64decode(capture['raw_base64'], validate=True).decode('utf-8', errors='surrogateescape')
    value, receipt = transport.parse_response(raw, state['request']['model_value_schema'], capture['receipt'])
    return check_result(state['request'], value), receipt


def _money_custody(db, p, cap, inputs, key, call, state):
    """Post-capture financial originals; never a prerequisite for raw custody."""
    from .same_goal_advice_spending_v308 import checked_original_hold
    need(state['capture'] is not None and state['call_id'] is not None)
    context = _history(db, p, cap, inputs, key, call)
    frozen = state['request']; preview = context['preview'].data
    need(_same(context['snapshot'], frozen['execution_snapshot'])
        and _same(preview['prepared'], frozen['prepared']))
    financial = {**context, 'invocation': call,
        'prepared': copy.deepcopy(frozen['prepared']),
        'quote': copy.deepcopy(preview['money_quote']),
        'price_ref': copy.deepcopy(preview['price_basis_ref']),
        'frozen_request_hash': frozen['request_hash']}
    return checked_original_hold(db, p, financial, _cloud_call(db, p, state['call_id']), require_intent=True)


@checked
def _settle_capture(p, cap, inputs, key, receipt, status):
    from .db import engine
    from .cloud_budget import settle_paid_response
    with Session(engine()) as db:
        call = _call(db, p, key, lock=True); state = _checked_state(db, p, cap, inputs, key, call)
        need(state['capture'] is not None and state['call_id'] == receipt.get('call_id'))
        _money_custody(db, p, cap, inputs, key, call, state)
        capture, frozen = state['capture'], state['request']
        raw = base64.b64decode(capture['raw_base64'], validate=True).decode('utf-8', errors='surrogateescape')
        proven = transport.response_receipt(raw, capture['receipt']); critical = ('call_id', 'provider', 'model', 'usage', 'request_id')
        need(_same({name: proven[name] for name in critical if name in proven},
            {name: receipt[name] for name in critical if name in receipt}))
        ledger = _cloud_call(db, p, state['call_id'], lock=True)
        _checked_state(db, p, cap, inputs, key, call)
        if ledger.status not in {'succeeded', 'failed'}:
            need(ledger.status == 'reserved' and ledger.reserved_tokens == frozen['prepared']['token_bound'])
            pin = frozen['pin']; model = frozen['model_snapshot']['execution']
            settle_paid_response(db, ledger.id, copy.deepcopy(receipt), {'workspace_id': p.workspace_id,
                'capability_id': pin['model_profile_id'], 'actor_id': pin['actor_id'],
                'request_hash': frozen['prepared']['request_hash'], 'dispatch_options_hash': frozen['prepared']['dispatch_options_hash'],
                'provider': model['provider'], 'model': model['model']}, status)
            _closed_receipt(ledger, frozen, receipt); db.commit()
        else: _closed_receipt(ledger, frozen, receipt)


@checked
def recover(db, p, cap, inputs, key, invocation=None):
    call = _call(db, p, key); need(invocation is None or invocation.id == call.id)
    state = _checked_state(db, p, cap, _inputs(inputs), key, call)
    if state['capture'] is None:
        from .gateway import ModelBlocked
        raise ModelBlocked('原业务建议没有可核对首答；发送及费用待核查，禁止自动重发')
    _money_custody(db, p, cap, _inputs(inputs), key, call, state)
    capture = state['capture']; raw = base64.b64decode(capture['raw_base64'], validate=True).decode('utf-8', errors='surrogateescape')
    receipt = transport.response_receipt(raw, capture['receipt'])
    try: result, receipt = _parsed(state)
    except Exception:
        _settle_capture(p, cap, inputs, key, receipt, 'failed'); raise
    _settle_capture(p, cap, inputs, key, receipt, 'succeeded')
    _closed_receipt(_cloud_call(db, p, state['call_id']), state['request'], receipt)
    return result


def _fresh(db, p, model, identity, *, lock=False):
    query = select(model).where(model.workspace_id == p.workspace_id, model.id == identity)
    if lock: query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True)); need(row is not None)
    return row


def _lease(db, p, job):
    owner = leases.ownership(db)
    need(leases.fenced(db) and set(owner) == {'job_id', 'lease_token'} and owner['job_id'] == job.id
        and owner['lease_token'] == job.lease_token and bool(owner['lease_token'])
        and job.kind == c.SEND_KIND and job.status == 'running' and job.data['schema'] == execution.JOB_SCHEMA
        and job.workspace_id == p.workspace_id and job.data['actor_id'] == p.user_id)
    need(db.scalar(select(Job.id).where(Job.id == job.id, Job.workspace_id == p.workspace_id,
        Job.kind == c.SEND_KIND, Job.status == 'running', Job.lease_token == owner['lease_token'],
        Job.lease_until > func.clock_timestamp())) == job.id)


def _owned(db, p, job, *, write=True):
    if write: store._reader(db, p, write=True)
    task = _fresh(db, p, Task, job.data['task_id'], lock=True)
    plan = _fresh(db, p, Plan, job.data['plan_id'], lock=True)
    job = _fresh(db, p, Job, job.id, lock=True); _lease(db, p, job)
    if write: lock_current_actor(db, p)
    prep, prepare_job = store._saved(db, p, task, plan)
    value = execution.checked(db, p, task, plan, prep, prepare_job)
    need(value is not None and value['job'].id == job.id and prep['business_actor_id'] == p.user_id)
    return task, plan, prep, value


def _ref(event): return {'id': event.id, 'record_hash': digest(event.data)}


def _terminal(db, p, task, plan, value, call, result):
    need(call.status in {'failed', 'unknown', 'blocked_model', 'blocked_evidence'})
    origins = store._rows(db, p, Audit, Audit.object_id == call.id,
        Audit.action.in_(['capability.invoke', 'worker.invocation_recovered']))
    need(origins and all(event.actor_id == p.user_id and event.data.get('status') == call.status
        and (event.action != 'worker.invocation_recovered' or event.data.get('job_id') == value['job'].id) for event in origins)
        and not store._rows(db, p, Audit, Audit.action == TERMINAL, Audit.object_id == call.id))
    state = call.data[STATE_KEY]
    record = {'schema': RECONCILED, 'workspace_id': p.workspace_id, 'task_id': task.id, 'plan_id': plan.id,
        'actor_id': p.user_id, 'send_job_id': value['job'].id, 'invocation_id': call.id,
        'execution_snapshot_hash': value['snapshot']['record_hash'], 'original_status': call.status,
        'origin_audits': sorted([_ref(event) for event in origins], key=lambda ref: ref['id']),
        'request_hash': state['request']['request_hash'], 'cloud_call_id': state['call_id'],
        'capture_sha256': state['capture']['raw_sha256'], 'result_hash': digest(result)}
    record['record_hash'] = c.record_hash(record); event = Audit(id=uid(), workspace_id=p.workspace_id,
        actor_id=p.user_id, action=TERMINAL, object_id=call.id, data=record)
    db.add(event); call.status = 'succeeded'; call.version += 1
    call.data = {**call.data, 'output': copy.deepcopy(result), 'same_goal_advice_recovery_v308': _ref(event)}


def _sdk_result(db, p, task, plan, value, call, cap):
    actor = value['job'].data['actor_id']; snapshot = value['snapshot']; inputs = execution.input_for(plan, snapshot)
    state = _checked_state_for_actor(db, p, cap, inputs, call.idempotency_key, call, actor_id=actor)
    _money_custody(db, p, cap, inputs, call.idempotency_key, call, state)
    result, receipt = _parsed(state)
    _closed_receipt(_cloud_call(db, p, state['call_id']), state['request'], receipt)
    need(call.status == 'succeeded' and _same(call.data.get('output'), result))
    originals = store._rows(db, p, Audit, Audit.action == 'capability.invoke', Audit.object_id == call.id)
    terminals = store._rows(db, p, Audit, Audit.action == TERMINAL, Audit.object_id == call.id)
    if terminals:
        need(len(terminals) == 1 and terminals[0].actor_id == actor)
        event = terminals[0]; record = event.data
        expected = {'schema': RECONCILED, 'workspace_id': p.workspace_id, 'task_id': task.id, 'plan_id': plan.id,
            'actor_id': actor, 'send_job_id': value['job'].id, 'invocation_id': call.id,
            'execution_snapshot_hash': snapshot['record_hash'], 'original_status': record['original_status'],
            'origin_audits': record['origin_audits'], 'request_hash': state['request']['request_hash'],
            'cloud_call_id': state['call_id'], 'capture_sha256': state['capture']['raw_sha256'], 'result_hash': digest(result)}
        need(record['original_status'] in {'failed', 'unknown', 'blocked_model', 'blocked_evidence'}
            and record['record_hash'] == c.record_hash(record)
            and _same(record, {**expected, 'record_hash': c.record_hash(expected)})
            and call.data.get('same_goal_advice_recovery_v308') == _ref(event))
        origins = store._rows(db, p, Audit, Audit.object_id == call.id,
            Audit.action.in_(['capability.invoke', 'worker.invocation_recovered']))
        need(record['origin_audits'] == sorted([_ref(item) for item in origins], key=lambda ref: ref['id'])
            and origins and all(item.actor_id == actor and item.data.get('status') == record['original_status']
                and (item.action != 'worker.invocation_recovered' or item.data.get('job_id') == value['job'].id) for item in origins))
    else:
        need(len(originals) == 1 and originals[0].actor_id == actor and originals[0].data == {'status': 'succeeded'}
            and 'same_goal_advice_recovery_v308' not in call.data)
    return result, state


@checked
def saved_result(db, p, task, job, call, cap, inputs):
    store._reader(db, p)
    actual = _call(db, p, call.idempotency_key); need(actual.id == call.id); call = actual
    context = _history(db, p, cap, _inputs(inputs), call.idempotency_key, call)
    need(context['task'].id == task.id and context['job'].id == job.id
        and context['job'].status == 'succeeded' and context['job'].lease_until is None)
    value = {'job': context['job'], 'snapshot': context['snapshot']}
    return _sdk_result(db, p, context['task'], context['plan'], value, call, cap)[0]


def _ready_original_value(task, plan, prep, value, call, result, state):
    record = {'schema': READY, 'workspace_id': task.workspace_id, 'task_id': task.id, 'plan_id': plan.id,
        'actor_id': prep['business_actor_id'], 'send_job_id': value['job'].id, 'invocation_id': call.id,
        'start_ref': _ref(value['start']), 'execution_snapshot_hash': value['snapshot']['record_hash'],
        'preview_ref': {'id': value['preview'].id, 'record_hash': value['preview'].data['record_hash']},
        'request_hash': state['request']['request_hash'], 'capture_sha256': state['capture']['raw_sha256'],
        'cloud_call_id': state['call_id'], 'result_hash': digest(result)}
    return {**record, 'record_hash': c.record_hash(record)}


def _ready_value(task, plan, prep, value, call, result, state):
    from .same_goal_advice_source_scope_v309 import record as source_review_scope
    record=_ready_original_value(task,plan,prep,value,call,result,state)
    record['source_review_scope']=source_review_scope(
        state['request']['execution_snapshot']['preview']['cloud_request'],result['model_value'],
        capture_sha256=state['capture']['raw_sha256'])
    record['record_hash']=c.record_hash(record)
    return record


def _marker(event, record):
    return {'schema': READY, 'result_ref': {'id': record['invocation_id'], 'record_hash': record['result_hash']},
        'send_job_id': record['send_job_id'], 'ready_audit_ref': _ref(event)}


def _ready(db, p, task, plan, prep, value, call, cap):
    result, state = _sdk_result(db, p, task, plan, value, call, cap)
    events = store._rows(db, p, Audit, Audit.action == RESULT_READY, Audit.object_id == plan.id)
    need(len(events) == 1); event = events[0]
    if 'source_review_scope' in event.data:
        record=_ready_value(task,plan,prep,value,call,result,state)
    else:
        # An older READY audit is not silently reinterpreted or rewritten.
        c.check_model_value_original_v302(state['request']['execution_snapshot']['preview']['cloud_request']['decision_input'],
            result['model_value'])
        record=_ready_original_value(task,plan,prep,value,call,result,state)
    need(event.actor_id == prep['business_actor_id'] and _same(event.data, record)
        and _same(plan.data.get(RESULT_KEY), _marker(event, record)))
    return copy.deepcopy(result)


@checked
def process(db, p, job):
    with db.no_autoflush:
        task, plan, prep, value = _owned(db, p, job)
        if plan.status == 'cancelled': return 'cancelled'
        snapshot = value['snapshot']; inputs = execution.input_for(plan, snapshot)
        cap = store._fresh(db, p, Capability, prep['advice_capability_ref']['id'])
        key = value['job'].idempotency_key
        if plan.data.get(RESULT_KEY) is not None:
            need(len(value['calls']) == 1)
            _ready(db, p, task, plan, prep, value, value['calls'][0], cap)
            if plan.status != 'ready_for_review': plan.status = 'ready_for_review'; plan.version += 1
            return 'succeeded'
        calls = value['calls']
    if calls:
        need(len(calls) == 1); call = calls[0]
    else:
        need(value['job'].data.get('recovery_only') is not True,
            '原发送任务当前仅允许核对已有首答；没有原调用，未创建调用或重新发送')
        from .gateway import invoke
        call = invoke(db, p, cap.id, 'business', inputs, key, approval=execution.approval_for(plan, snapshot, value['job']))
    if call.status != 'succeeded':
        # The original Gateway can retain an Invocation FOR UPDATE. Only the
        # existing fenced commit releases it before historical settlement.
        db.commit(); result = recover(db, p, cap, inputs, key, invocation=call)
        with db.no_autoflush:
            task, plan, prep, value = _owned(db, p, job)
            call = _call(db, p, key, lock=True)
            _checked_state(db, p, cap, inputs, key, call)
            _terminal(db, p, task, plan, value, call, result)
            db.flush()
    with db.no_autoflush:
        task, plan, prep, value = _owned(db, p, job)
        call = _call(db, p, key, lock=True)
        result, state = _sdk_result(db, p, task, plan, value, call, cap)
        if plan.status == 'cancelled': return 'cancelled'
        need(plan.data.get(RESULT_KEY) is None
            and not store._rows(db, p, Audit, Audit.action == RESULT_READY, Audit.object_id == plan.id))
        record = _ready_value(task, plan, prep, value, call, result, state)
        event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
            action=RESULT_READY, object_id=plan.id, data=record)
        db.add(event); plan.data = {**plan.data, RESULT_KEY: _marker(event, record)}
        plan.status = 'ready_for_review'; plan.version += 1
        db.flush(); _ready(db, p, task, plan, prep, value, call, cap)
        return 'succeeded'


@checked
def fail_job(db, p, job, reason):
    with db.no_autoflush:
        _task, plan, _prep, _value = _owned(db, p, job, write=False)
        if plan.status == 'cancelled': return 'cancelled'
        if plan.status != 'needs_review': plan.status = 'needs_review'; plan.version += 1
        return 'failed'


@checked
def cold_result(db, p, task, plan):
    with db.no_autoflush:
        store._reader(db, p)
        task = _fresh(db, p, Task, task.id); plan = _fresh(db, p, Plan, plan.id)
        prep, prepare_job = store._saved(db, p, task, plan)
        value = execution.checked(db, p, task, plan, prep, prepare_job)
        need(value is not None and value['job'].status == 'succeeded' and value['job'].lease_until is None
            and len(value['calls']) == 1)
        cap = store._fresh(db, p, Capability, prep['advice_capability_ref']['id'])
        return _ready(db, p, task, plan, prep, value, value['calls'][0], cap)
