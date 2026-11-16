"""Independent Data-agent receipt for a genuinely completed registered read."""
import copy

from sqlalchemy import select

from .contracts import digest
from .models import Audit, Event, Job, Memory, Membership, User, now, uid
from .security import Principal
from . import same_goal_continuation_execution_contracts_v314 as c
from . import same_goal_continuation_execution_store_v314 as store
from . import same_goal_continuation_execution_runtime_v314 as runtime

KIND = 'business.continuation_read_result_v314'
EVENT_SCHEMA = 'same-goal-continuation-result-event-v314'
ACK_SCHEMA = 'same-goal-continuation-data-ack-v314'
RECEIVED = 'same_goal_continuation.data_ack_v314'
MEMORY_TYPE = 'continuation_read_result_v314'
need = runtime.need


def _principal(db, workspace_id, identity):
    row = db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id)
        .where(User.id == identity, User.active.is_(True), Membership.workspace_id == workspace_id)).one_or_none()
    need(row is not None, '原数据接收岗位暂不可用，已保存计算结果保留，等待恢复收件')
    user, member = row
    return Principal(user.id, workspace_id, member.role, 'continuation-data-receiver-v314')


def enqueue(db, p, task, saved, output, *, retry_blocked=False):
    dispatched = store._rows(db, p, Audit, Audit.action == runtime.DISPATCHED, Audit.object_id == saved['plan'].id)
    need(len(dispatched) == 1)
    value = {'schema': EVENT_SCHEMA, 'task_id': task.id, 'plan_id': saved['plan'].id,
        'job_id': saved['job'].id, 'submission_hash': saved['proposal']['record_hash'],
        'output_ref': copy.deepcopy(output['output_ref']), 'output_hash': output['output_hash'],
        'data_actor_id': dispatched[0].data['data_actor_id']}
    key = 'continuation-read-result-v314:' + saved['plan'].id
    rows = store._rows(db, p, Event, Event.idempotency_key == key)
    if rows:
        need(len(rows) == 1 and rows[0].kind == KIND and rows[0].data == value)
        if retry_blocked and rows[0].status == 'blocked_delivery':
            event = rows[0]
            need(event.received_at is None)
            audits, memories = _receipt_rows(db, p, event)
            need(not audits and not memories)
            event.status = 'pending_delivery'
            db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
                action='same_goal_continuation.data_retry_v314', object_id=event.id,
                data={'task_id': task.id, 'plan_id': saved['plan'].id,
                    'event_hash': digest(value), 'previous_attempts': event.attempts}))
            db.flush()
        return rows[0]
    event = Event(id=uid(), workspace_id=p.workspace_id, kind=KIND, status='pending_delivery',
        idempotency_key=key, data=value)
    db.add(event); db.flush()
    return event


def _receipt_rows(db, p, event):
    audits = store._rows(db, p, Audit, Audit.action == RECEIVED, Audit.object_id == event.id)
    memories = store._rows(db, p, Memory, Memory.data['type'].as_string() == MEMORY_TYPE,
        Memory.data['event_id'].as_string() == event.id)
    need(len(audits) == len(memories) <= 1)
    return audits, memories


def checked_receipt(db, p, task, saved, output_audit, ack):
    fields = {'schema', 'event_id', 'memory_id', 'receipt_audit_ref', 'task_id', 'plan_id', 'job_id',
        'invocation_id', 'submission_hash', 'output_hash', 'received_at'}
    need(type(ack) is dict and set(ack) == fields and ack['schema'] == ACK_SCHEMA)
    events = store._rows(db, p, Event, Event.id == ack['event_id'])
    need(len(events) == 1); event = events[0]
    audits, memories = _receipt_rows(db, p, event)
    need(len(audits) == 1)
    audit, memory = audits[0], memories[0]
    dispatches = store._rows(db, p, Audit, Audit.action == runtime.DISPATCHED, Audit.object_id == saved['plan'].id)
    need(len(dispatches) == 1 and dispatches[0].data['data_actor_id'] == audit.actor_id)
    expected = {'schema': EVENT_SCHEMA, 'task_id': task.id, 'plan_id': saved['plan'].id,
        'job_id': saved['job'].id, 'submission_hash': saved['proposal']['record_hash'],
        'output_ref': store._ref(output_audit), 'output_hash': output_audit.data['output_hash'],
        'data_actor_id': audit.actor_id}
    need(event.kind == KIND and event.status == 'received' and event.received_at is not None
        and event.data == expected and saved['job'].status == 'succeeded'
        and memory.status == 'candidate' and memory.data['type'] == MEMORY_TYPE
        and memory.data['event_id'] == event.id and memory.data['output_ref'] == store._ref(output_audit)
        and memory.data['output_hash'] == output_audit.data['output_hash']
        and memory.data['output'] == output_audit.data['output']
        and memory.data['task_id'] == task.id and memory.data['plan_id'] == saved['plan'].id
        and memory.data['job_id'] == saved['job'].id
        and all(memory.data.get(key) is False for key in ('physical_state_verified', 'auto_publish', 'entity_state_updated'))
        and ack['receipt_audit_ref'] == store._ref(audit))
    value = {key: item for key, item in ack.items() if key != 'receipt_audit_ref'}
    need(audit.data == value and ack['task_id'] == task.id and ack['plan_id'] == saved['plan'].id
        and ack['job_id'] == saved['job'].id and ack['invocation_id'] == output_audit.data['invocation_id']
        and ack['submission_hash'] == saved['proposal']['record_hash']
        and ack['output_hash'] == output_audit.data['output_hash']
        and ack['memory_id'] == memory.id and ack['received_at'] == event.received_at.isoformat())
    return copy.deepcopy(ack)


def receive(db, event):
    need(event.kind == KIND and event.data.get('schema') == EVENT_SCHEMA)
    p = _principal(db, event.workspace_id, event.data['data_actor_id'])
    task, saved = runtime._load(db, p, event.data['task_id'], event.data['plan_id'], lock=True)
    job = saved['job']
    if job.status != 'succeeded': return False
    output, previous = store._results(db, p, task, saved)
    need(output is not None and event.data['output_ref'] == store._ref(output)
        and event.data['output_hash'] == output.data['output_hash'])
    calls = store._invocations(db, p, saved); need(len(calls) == 1)
    runtime.checked_output(db, p, task, saved, job, calls[0])
    audits, memories = _receipt_rows(db, p, event)
    need(not audits and not memories and previous is None,
        '此结果已有独立接收原件或缺失回执指针，未另建一份接收结果')
    at = now()
    memory = Memory(id=uid(), workspace_id=p.workspace_id, status='candidate',
        text='只读汇总结果已收存；复核状态与原办理记录关联保留', data={'type': MEMORY_TYPE,
            'event_id': event.id, 'task_id': task.id, 'plan_id': saved['plan'].id, 'job_id': job.id,
            'output_ref': store._ref(output), 'output_hash': output.data['output_hash'],
            'output': copy.deepcopy(output.data['output']), 'physical_state_verified': False,
            'auto_publish': False, 'entity_state_updated': False})
    ack = {'schema': ACK_SCHEMA, 'event_id': event.id, 'memory_id': memory.id,
        'task_id': task.id, 'plan_id': saved['plan'].id, 'job_id': job.id,
        'invocation_id': calls[0].id, 'submission_hash': saved['proposal']['record_hash'],
        'output_hash': output.data['output_hash'], 'received_at': at.isoformat()}
    audit = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=RECEIVED, object_id=event.id, created_at=at, data=copy.deepcopy(ack))
    db.add(memory); db.add(audit)
    event.status = 'received'; event.received_at = at; event.attempts += 1
    db.flush()
    ack['receipt_audit_ref'] = store._ref(audit)
    store.attach_receipt(db, p, task.id, saved['plan'].id, job, calls[0], ack)
    return True
