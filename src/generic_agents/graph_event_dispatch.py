"""The original processing transaction creates intents; workers recover them."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import digest
from .graph_event_models import GraphEventProducer, GraphEventIntent
from .graph_event_admission import freeze_intent, admit, read_intent
from .leases import ownership, attach
from .db import engine
from .models import Job

KIND = 'admit_graph_source_event'
FIELDS = {'actor_id', 'intent_id', 'producer_id', 'intent_hash'}


def job_body(job):
    if not FIELDS <= set(job.data) or set(job.data) - FIELDS - {
            'finished_at', 'error', 'graph_event_retries', 'recovery_only', 'lease_recoveries'}:
        raise HTTPException(409, '原消息后台任务正文无法核对')
    if ('recovery_only' in job.data and type(job.data['recovery_only']) is not bool
            or 'lease_recoveries' in job.data and (type(job.data['lease_recoveries']) is not int
                or job.data['lease_recoveries'] < 0)):
        raise HTTPException(409, '原消息后台恢复状态无法核对')
    history = job.data.get('graph_event_retries', [])
    if type(history) is not list or len(history) > 32:
        raise HTTPException(409, '原消息恢复历史无法核对')
    return {key: job.data[key] for key in FIELDS}


def capture_source(db, p, observation, event):
    """Freeze only this actual processing result, never scan historical events.

    Active-contract failures remain explicit and roll back this processing
    transaction. The existing Invocation recovery retains the original call;
    retrying does not grant permission to issue another model request.
    """
    # Native receipt capture has an independent real Run/Audit protocol. It is
    # never made a processing source by supplying a missing extractor.
    if any(key in observation.data for key in ('native_instance_id', 'native_packet', 'native_packet_hash')):
        from .graph_event_native_capture_v136 import native_observation
        native_observation(db, p, observation, event)
        return
    db.flush()
    producers = db.scalars(select(GraphEventProducer).where(
        GraphEventProducer.workspace_id == p.workspace_id,
        GraphEventProducer.status == 'active',
        GraphEventProducer.data['contract']['source_id'].as_string() == observation.source_id,
        GraphEventProducer.data['contract']['extractor_id'].as_string() == observation.data.get('extractor_id'),
        GraphEventProducer.data['contract']['event_kind'].as_string() == event.kind)
        .order_by(GraphEventProducer.id).with_for_update(read=True)
        .execution_options(populate_existing=True)).all()
    for producer in producers:
        intent, _ = freeze_intent(db, p, producer, event, observation)
        queue_intent(db, p, producer, intent)


def queue_intent(db, p, producer, intent):
    key = 'typed-graph-source:' + intent.id
    existing = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == key))
    data = {'actor_id': p.user_id, 'intent_id': intent.id,
        'producer_id': producer.id, 'intent_hash': intent.data['intent_hash']}
    if existing is None:
        db.add(Job(workspace_id=p.workspace_id, kind=KIND, idempotency_key=key, status='queued', data=data))
    elif existing.kind != KIND or digest(job_body(existing)) != digest(data):
        raise HTTPException(409, '原消息接纳任务与冻结来源不一致，原处理回执保留')


def process(db, p, job):
    lease = ownership(db)
    if (job.kind != KIND or job.workspace_id != p.workspace_id or job.status != 'running'
            or lease.get('job_id') != job.id or lease.get('lease_token') != job.lease_token
            or job.data['actor_id'] != p.user_id):
        raise HTTPException(409, '消息接纳仅由原后台任务和有效租约处理')
    expected = job_body(job); identity = job.id; token = job.lease_token
    # The outer worker already holds its lease row. Release that transaction
    # before starting a clean independently recoverable admission transaction.
    db.commit()
    with Session(engine(), expire_on_commit=False) as receiving:
        attach(receiving, identity, token)
        receiving.info['worker_actor'] = p
        # attach fences/locks this Job before any source or stream lock. Its
        # commit check reuses that same held row, never takes a new lock after
        # the cursor. A crash after this commit is recovered by original intent.
        current = receiving.get(Job, identity)
        if current.kind != KIND or current.workspace_id != p.workspace_id or job_body(current) != expected:
            raise HTTPException(409, '原消息接纳任务已变化')
        intent = receiving.get(GraphEventIntent, expected['intent_id'])
        if (intent is None or intent.workspace_id != p.workspace_id or intent.actor_id != p.user_id
                or intent.producer_id != expected['producer_id']
                or current.idempotency_key != 'typed-graph-source:' + intent.id):
            raise HTTPException(409, '原消息来源或处理账号无法核对')
        original = read_intent(receiving, p, intent.id)
        if original['intent_hash'] != expected['intent_hash']:
            raise HTTPException(409, '原消息接纳正文已变化，不能换内容恢复')
        receipt, _ = admit(receiving, p, intent.id)
        result = {'admission_id': receipt.id, 'sequence': receipt.sequence}
        receiving.commit()
        return result
