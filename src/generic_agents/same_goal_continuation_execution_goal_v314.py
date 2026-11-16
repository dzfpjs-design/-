"""Cold acceptance gate for independently approved continuation executions."""
from sqlalchemy import or_

from . import same_goal_continuation_execution_contracts_v314 as c
from . import same_goal_continuation_execution_store_v314 as store
from .contracts import digest
from .goals import snapshot
from .models import Audit, Event, Invocation, Memory

need, checked = store.need, store.checked


def _transport(db, p, task, index):
    """Look for positive transport remnants even when the Task index is empty."""
    from .same_goal_continuation_execution_runtime_v314 import DISPATCHED, HALTED

    ids = list(index)
    approval = Invocation.data['approval']
    calls = store._rows(db, p, Invocation, or_(
        (approval['schema'].as_string() == c.APPROVAL) & (approval['task_id'].as_string() == task.id),
        approval['plan_id'].as_string().in_(ids),
        Invocation.idempotency_key.in_([store._key(identity) for identity in ids])))
    audits = store._rows(db, p, Audit, Audit.action.in_([store.OUTPUT, store.RECEIVED, DISPATCHED, HALTED]),
        or_(Audit.data['task_id'].as_string() == task.id, Audit.object_id.in_(ids)))
    indexed_calls, indexed_audits = {}, {}
    for call in calls:
        value = call.data['approval']
        identity = value['plan_id']
        need(identity in index and identity not in indexed_calls and value['task_id'] == task.id
            and value['schema'] == c.APPROVAL and call.idempotency_key == store._key(identity))
        job = index[identity]['job']
        need(job is not None and value['job_id'] == job.id and call.data['job_id'] == job.id)
        indexed_calls[identity] = call
    for event in audits:
        key = (event.object_id, event.action)
        need(event.object_id in index and event.data['task_id'] == task.id and key not in indexed_audits)
        if event.action == DISPATCHED:
            call = indexed_calls.get(event.object_id)
            need(call is not None and event.data['invocation_id'] == call.id)
        indexed_audits[key] = event
    return indexed_calls, indexed_audits


def _deliveries(db, p, task, index):
    from . import same_goal_continuation_result_receiver_v314 as receiver

    ids = list(index)
    jobs = [row['job'].id for row in index.values() if row['job'] is not None]
    prefix = 'continuation-read-result-v314:'
    def scope(model):
        return or_(model.data['task_id'].as_string() == task.id,
            model.data['plan_id'].as_string().in_(ids), model.data['job_id'].as_string().in_(jobs))
    events = store._rows(db, p, Event, or_(
        Event.idempotency_key.in_([prefix + identity for identity in ids]),
        scope(Event) & or_(Event.kind == receiver.KIND, Event.data['schema'].as_string() == receiver.EVENT_SCHEMA,
            Event.idempotency_key.startswith(prefix))))
    event_ids = [event.id for event in events]
    memories = store._rows(db, p, Memory, or_(
        scope(Memory) & (Memory.data['type'].as_string() == receiver.MEMORY_TYPE),
        Memory.data['event_id'].as_string().in_(event_ids)))
    acknowledgements = store._rows(db, p, Audit,
        or_(Audit.action == receiver.RECEIVED, Audit.data['schema'].as_string() == receiver.ACK_SCHEMA),
        or_(scope(Audit), Audit.object_id.in_(event_ids), Audit.data['event_id'].as_string().in_(event_ids)))
    result = {}
    for kind, rows in (('event', events), ('memory', memories), ('ack', acknowledgements)):
        for row in rows:
            identity = row.data['plan_id']
            need(identity in index and row.data['task_id'] == task.id,
                '数据接收原件缺少对应办理记录')
            job = index[identity]['job']
            need(job is not None and row.data['job_id'] == job.id)
            delivery = result.setdefault(identity, {})
            need(kind not in delivery, '办理结果存在重复数据接收原件')
            delivery[kind] = row
    return result


def _delivery_proof(task, saved, output, receipt, dispatch, delivery):
    from . import same_goal_continuation_result_receiver_v314 as receiver

    event = delivery.get('event')
    if event is None:
        need(not delivery and receipt is None, '数据接收记录缺少原事件')
        return
    plan, job = saved['plan'], saved['job']
    need(output is not None and dispatch is not None
        and event.kind == receiver.KIND and event.idempotency_key == 'continuation-read-result-v314:' + plan.id
        and event.data == {'schema': receiver.EVENT_SCHEMA, 'task_id': task.id, 'plan_id': plan.id,
            'job_id': job.id, 'submission_hash': saved['proposal']['record_hash'],
            'output_ref': store._ref(output), 'output_hash': output.data['output_hash'],
            'data_actor_id': dispatch.data['data_actor_id']},
        '数据接收事件与原输出、岗位或固定事件编号不一致')
    if receipt is None:
        need(set(delivery) == {'event'} and event.status in {'pending_delivery', 'blocked_delivery'}
            and event.received_at is None, '已接收数据缺少独立凭证')
        return
    ack = receipt.data['ack']
    need(set(delivery) == {'event', 'memory', 'ack'} and event.status == 'received'
        and event.received_at is not None and ack['event_id'] == event.id)
    memory, audit = delivery['memory'], delivery['ack']
    need(memory.id == ack['memory_id'] and memory.data['type'] == receiver.MEMORY_TYPE
        and memory.data['event_id'] == event.id and audit.action == receiver.RECEIVED
        and audit.object_id == event.id and audit.data['event_id'] == event.id
        and ack['receipt_audit_ref'] == store._ref(audit))


def _stopped_before_claim(saved, call, output, receipt):
    job = saved['job']
    need(call is None and output is None and receipt is None)
    if saved['start'] is None:
        need(job is None)
    else:
        need(job is not None and job.status == 'cancelled' and type(job.attempts) is int
            and job.attempts == 0 and job.lease_until is None and job.lease_token is None,
            '已取消办理仍存在领取或执行记录，不能结清')


def _review_proof(saved, call, output, receipt, decision):
    job = saved['job']
    reviews = [event for event in saved['events'].values() if event.action == c.ACTION_REVIEW]
    need(len(reviews) == 1 and reviews[0].data['request']['decision'] == decision
        and job is not None and job.status == 'succeeded' and job.lease_until is None
        and type(job.attempts) is int and job.attempts > 0 and call is not None and call.status == 'succeeded'
        and output is not None and output.data['invocation_id'] == call.id and receipt is not None,
        '办理结果缺少成功执行、独立数据接收或人工复核')
    return {'invocation_id': call.id, 'output_ref': store._ref(output),
        'receipt_ref': store._ref(receipt), 'review_ref': store._ref(reviews[0])}


def _halt_proof(task, saved, halt, call, output, receipt):
    plan, job, start = saved['plan'], saved['job'], saved['start']
    need(plan.status == 'cancel_requested' and job is not None and start is not None
        and call is None and output is None and receipt is None
        and type(job.attempts) is int and job.attempts > 0
        and type(job.lease_token) is str and bool(job.lease_token)
        and halt.actor_id == start.actor_id == job.data['actor_id']
        and halt.data == {'task_id': task.id, 'plan_id': plan.id, 'job_id': job.id,
            'submission_hash': saved['proposal']['record_hash'], 'lease_token': job.lease_token,
            'reason': 'cancelled_before_sdk'},
        '已保存后台停止凭证与原办理、租约或实际调用不一致')
    if job.status == 'cancelled' and job.lease_until is None:
        return {'halt_ref': store._ref(halt)}
    return None


def _phase(db, p, task, saved, call, audits, delivery, at):
    from .same_goal_continuation_execution_runtime_v314 import DISPATCHED, HALTED

    plan, job = saved['plan'], saved['job']
    phase = plan.status
    output, receipt = store._results(db, p, task, saved, verify=True)
    for action, event in ((store.OUTPUT, output), (store.RECEIVED, receipt)):
        seen = audits.get((plan.id, action))
        need((event is None and seen is None) or (event is not None and seen is not None and event.id == seen.id))
    _delivery_proof(task, saved, output, receipt, audits.get((plan.id, DISPATCHED)), delivery)
    halt = audits.get((plan.id, HALTED))
    if halt is not None:
        return phase, _halt_proof(task, saved, halt, call, output, receipt)
    if phase in {'adopted', 'result_returned'}:
        decision = 'adopt' if phase == 'adopted' else 'return'
        return phase, _review_proof(saved, call, output, receipt, decision)
    if phase in {'returned', 'cancelled'}:
        _stopped_before_claim(saved, call, output, receipt)
    if phase == 'queued' and job is not None and job.status == 'failed':
        if ((job.lease_until is None or job.lease_until <= at) and output is None and receipt is None
                and (call is None or call.status == 'failed')):
            return 'failed', None
    return phase, None


def _successors(index, phases):
    links = {}
    for identity, saved in index.items():
        proposal = saved['proposal']
        previous_id = proposal['supersedes_plan_id']
        if previous_id is None:
            continue
        need(previous_id != identity and previous_id in index and previous_id not in links,
            '办理重办关联存在孤立、重复后继或循环')
        previous = index[previous_id]
        original = previous['proposal']
        need(phases[previous_id] in {'returned', 'result_returned', 'cancelled', 'failed'}
            and original['advice_plan_id'] == proposal['advice_plan_id']
            and original['continuation_ref']['id'] == proposal['continuation_ref']['id']
            and original['source_hash'] == proposal['source_hash']
            and digest(original['grant']['basis']['goal']) == digest(proposal['grant']['basis']['goal'])
            and (previous['start'] is None or original['grant_ref']['id'] != proposal['grant_ref']['id']),
            '办理重办关联的原范围、状态或工具授权无法核对')
        links[previous_id] = identity
    return links


def _adopted_successor(identity, links, phases):
    visited = {identity}
    target = links.get(identity)
    while target is not None:
        need(target not in visited, '办理重办关联存在循环')
        visited.add(target)
        if phases[target] == 'adopted':
            return target
        target = links.get(target)
    return None


@checked
def goal_gate(db, p, task):
    """Return current settlement and a stable cold proof hash for acceptance."""
    with db.no_autoflush:
        rows = store._inventory(db, p, task)
        index = {row['plan'].id: row for row in rows}
        need(len(index) == len(rows))
        calls, audits = _transport(db, p, task, index)
        deliveries = _deliveries(db, p, task, index)
        if not index:
            return {'all_reviewed': True, 'runs': [], 'basis_hash': digest([])}
        goal = snapshot(task)
        goal_pin = {'hash': goal['hash'], 'version': goal['version'], 'snapshot_hash': digest(goal),
            'native_context_hash': task.data['native_goal_context_hash']}
        at = store._clock(db)
        phases, reviews = {}, {}
        for identity, saved in index.items():
            proposal = saved['proposal']
            need(digest(proposal['grant']['basis']['goal']) == digest(goal_pin),
                '办理记录的原目标依据与当前验收范围不一致')
            phases[identity], reviews[identity] = _phase(
                db, p, task, saved, calls.get(identity), audits, deliveries.get(identity, {}), at)
        links = _successors(index, phases)
        runs, proof = [], []
        for identity, saved in sorted(index.items()):
            phase = phases[identity]
            replacement = _adopted_successor(identity, links, phases)
            witness = reviews[identity]
            halted = witness is not None and 'halt_ref' in witness
            settled = phase in {'adopted', 'cancelled'} or replacement is not None or halted
            run = {'plan_id': identity, 'phase': phase, 'settled': settled,
                'supersedes_plan_id': saved['proposal']['supersedes_plan_id'],
                'adopted_successor_id': replacement}
            if halted:
                run['halted_before_sdk'] = True
            runs.append(run)
            proof.append({**run, 'version': saved['plan'].version,
                'submission_hash': saved['proposal']['record_hash'],
                'operations_hash': digest(saved['plan'].data['operations']),
                'review': witness})
        return {'all_reviewed': all(run['settled'] for run in runs),
            'runs': runs, 'basis_hash': digest(proof)}


def require_settled(db, p, task):
    result = goal_gate(db, p, task)
    need(result['all_reviewed'], '仍有办理执行、复核或退回重办尚未结清，暂不能验收目标')
    return result
