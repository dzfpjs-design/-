"""Independent V207 source/consent wait: same Job, no Invocation or paid intent."""
import copy
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from . import models as m, workflow_document_packet_v207 as packet
from . import workflow_evidence_consent_wait_v199 as consent, workflow_provenance_wait_v197 as original
from . import business_reasoning_v197 as reasoning
from . import business_reasoning_protocols_v276 as protocols
from .contracts import digest
from .db import engine
from .services import scoped, audit

KEY = 'workflow_document_packet_wait_v207'
PHASE = 'waiting_document_packet_v207'
WAIT = 'workflow.document_packet.wait_v207'
WAKE = 'workflow.document_packet.wake_v207'
TRANSPORT_HASH = 'document_packet_wait_hash_v207'
IDENTITIES = original.IDENTITY_KEYS
need, same = packet.need, packet.same
ERROR = '原运行资料、生产任务、批准输入或模型授权无法核对；原任务保留且未产生新调用'


def snapshot(db, p, task, ids, model):
    """Pending means only an intact, committed producer's original Job tail."""
    proofs = []; pending = False
    for row in packet.selected_packets(db, p, task, ids):
        identity = row.evidence_id; values = packet._inventory(db, p, row.invocation_id)
        producer = values[3]; activation, transport = values[6:8]
        need(activation['status'] == 'completed' and transport.get('phase') == 'succeeded')
        need(producer.status in {'running', 'succeeded'})
        pending = pending or producer.status == 'running'
        proofs.append({'packet_id': row.id, 'packet_hash': digest(packet.packet_proof(row)), 'evidence_id': identity,
            'sha256': row.sha256, 'producer_job_id': producer.id, 'invocation_id': row.invocation_id,
            'source_set_id': row.source_set_id, 'source_set_hash': row.data['selected']['source_set_hash']})
    pins, grants = consent._files(db, p, ids, model)
    return proofs, pending, pins, grants


def snapshot_inputs(db, p, task, cap, inputs, model):
    """Keep both original packet producer tails behind the same durable wait."""
    if cap.data['execution'].get('adapter') not in {protocols.DECISION, protocols.REVIEW, protocols.READY}:
        return snapshot(db, p, task, inputs['evidence_ids'], model)
    if cap.data['execution'].get('adapter') == protocols.READY:
        from . import business_goal_ready_sources_v288 as sources
    else:
        from . import business_decision_sources_v276 as sources
    value, pending = sources.wait_snapshot(db, p, task, inputs)
    proofs = [{'schema': sources.SCHEMA, 'sources_hash': value['sources_hash'],
        'data_packet': value['data']['packet'], 'document_packet': value['documents']['packet']}]
    pins, grants = consent._files(db, p, value['evidence_ids'], model)
    return proofs, bool(pending), pins, grants


def identity(job): return {key: copy.deepcopy(job.data[key]) for key in IDENTITIES}


def inventory(db, p, task, job):
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == job.id, m.Audit.action.in_([WAIT, WAKE]))))
    value = job.data.get(KEY)
    if value is None:
        need(not events and TRANSPORT_HASH not in task.data['graph_jobs'][job.data['activation_id']])
        return {'history': [], 'active_hash': None, 'wakes': []}
    need(type(value) is dict and set(value) == {'history', 'active_hash', 'wakes'}
        and type(value['history']) is list and 0 < len(value['history']) <= 32 and type(value['wakes']) is list
        and len(value['wakes']) <= 32 and len(canonical_bytes(value)) <= 262144)
    previous = None; hashes = []; used = set()
    for number, record in enumerate(value['history'], 1):
        need(record['schema'] == KEY and record['version'] == number and record['previous_hash'] == previous
            and record['job_id'] == job.id and same(record['identity'], identity(job))
            and type(record['attempts_at_wait']) is int and 0 <= record['attempts_at_wait'] <= job.attempts
            and record['record_hash'] == digest({key: item for key, item in record.items() if key != 'record_hash'}))
        anchors = [event for event in events if event.action == WAIT and event.data.get('record_hash') == record['record_hash']]
        need(len(anchors) == 1 and anchors[0].actor_id == record['identity']['actor_id'] and same(anchors[0].data, record))
        used.add(anchors[0].id); hashes.append(record['record_hash']); previous = record['record_hash']
    woken = []
    for proof in value['wakes']:
        need(proof['record_hash'] in hashes and proof['record_hash'] not in woken
            and proof['wake_hash'] == digest({key: item for key, item in proof.items() if key != 'wake_hash'}))
        anchors = [event for event in events if event.action == WAKE and event.data.get('record_hash') == proof['record_hash']]
        record = next(item for item in value['history'] if item['record_hash'] == proof['record_hash'])
        need(len(anchors) == 1 and anchors[0].actor_id == record['identity']['actor_id'] and same(anchors[0].data, proof))
        used.add(anchors[0].id); woken.append(proof['record_hash'])
    need(used == {event.id for event in events} and value['active_hash'] in {None, hashes[-1]}
        and value['active_hash'] not in woken and set(hashes) == set(woken) | ({value['active_hash']} if value['active_hash'] else set())
        and task.data['graph_jobs'][job.data['activation_id']].get(TRANSPORT_HASH) == hashes[-1])
    return copy.deepcopy(value)


def canonical_bytes(value):
    from .typed_payload_codec_v131 import canonical_json
    return canonical_json(value).encode()


def compare(record, proofs, pins, grants, cap, model):
    need(record['capability_id'] == cap.id and record['capability_hash'] == cap.content_hash
        and record['model_id'] == model.id and record['model_hash'] == model.content_hash and same(record['packets'], proofs))
    consent._check_pins({'evidence_pins': record['evidence_pins']}, pins, grants)


def preflight(db, p, task, job, node, cap, inputs):
    """None legacy; True original wait; False V207 qualification is complete."""
    protocol = protocols.for_adapter(cap.data['execution'].get('adapter'))
    if protocol is None: return None
    value = protocol._inputs(inputs)
    if protocol.ADAPTER not in {protocols.DECISION, protocols.REVIEW, protocols.READY} and not packet.uses_family(db, p, task, value['evidence_ids']): return None
    task, job, actual_node = original._locked_preflight(db, p, task, job, cap, value)
    need(same(actual_node, node)); consent._no_intent(db, task, job)
    actual_task, node, actual, model = consent._current(db, p, task, job)
    ledger = inventory(db, p, task, job); need(ledger['active_hash'] is None)
    proofs, pending, pins, grants = snapshot_inputs(db, p, task, actual, value, model)
    if ledger['history']:
        last = ledger['history'][-1]; compare(last, proofs, pins, grants, actual, model)
        wake = next((item for item in ledger['wakes'] if item['record_hash'] == last['record_hash']), None)
        need(wake is not None); consent._check_wake(wake, grants)
    if not pending and all(grants[item['evidence_id']] for item in pins): return False
    need(len(ledger['history']) < 32, '原资料等待记录达到容量，保留原号供人工核对')
    record = {'schema': KEY, 'version': len(ledger['history']) + 1,
        'previous_hash': ledger['history'][-1]['record_hash'] if ledger['history'] else None,
        'job_id': job.id, 'identity': identity(job), 'attempts_at_wait': job.attempts,
        'capability_id': actual.id, 'capability_hash': actual.content_hash, 'model_id': model.id,
        'model_hash': model.content_hash, 'packets': proofs, 'evidence_pins': pins,
        'reason': 'producer' if pending else 'consent'}
    record['record_hash'] = digest(record); ledger['history'].append(record); ledger['active_hash'] = record['record_hash']
    need(len(canonical_bytes(ledger)) <= 262144)
    job.data = {**job.data, KEY: ledger}
    transports = copy.deepcopy(task.data['graph_jobs']); transports[job.data['activation_id']] = {
        **transports[job.data['activation_id']], 'phase': PHASE, TRANSPORT_HASH: record['record_hash'],
        'reason': '原资料包生产任务尚未终结' if pending else '请先将这份具体资料包明确授权给本次固定模型；尚无调用或付费预留'}
    task.data = {**task.data, 'graph_jobs': transports}; task.version += 1
    audit(db, p, WAIT, job.id, record); job.status = PHASE
    return True


def workspace(value):
    need(type(value) is str and str(UUID(value)) == value, '运行资料等待必须明确指定工作区')
    return value


def scheduler_once(db, *, workspace_id, limit=32):
    workspace(workspace_id); need(type(limit) is int and 1 <= limit <= 32)
    candidates = list(db.scalars(select(m.Job).where(m.Job.workspace_id == workspace_id,
        m.Job.kind == 'execute_full_semantic_graph_node', m.Job.status == PHASE,
        m.Job.available_at <= func.clock_timestamp()).order_by(m.Job.available_at, m.Job.id).limit(limit)))
    count = 0
    for candidate in candidates:
        task = db.scalar(select(m.Task).where(m.Task.workspace_id == workspace_id, m.Task.id == candidate.data.get('task_id'))
            .with_for_update().execution_options(populate_existing=True))
        job = db.scalar(select(m.Job).where(m.Job.workspace_id == workspace_id, m.Job.id == candidate.id,
            m.Job.status == PHASE).with_for_update().execution_options(populate_existing=True))
        if job is None: continue
        try:
            need(task is not None)
            if consent._cancelled(task, job):
                original._reject(task, job, 'cancelled', '原任务已停止，运行资料等待保留且未调用模型'); continue
            p = consent._actor(db, workspace_id, job.data['actor_id'])
            ledger = inventory(db, p, task, job); need(ledger['active_hash'] is not None)
            record = ledger['history'][-1]; consent._no_intent(db, task, job)
            need(job.attempts == record['attempts_at_wait'])
            _, node, cap, model = consent._current(db, p, task, job)
            proofs, pending, pins, grants = snapshot_inputs(db, p, task, cap, job.data['inputs'], model)
            compare(record, proofs, pins, grants, cap, model)
            if pending or any(not grants[item['evidence_id']] for item in pins): continue
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError):
            original._reject(task, job, 'blocked', ERROR); continue
        wake = {'record_hash': record['record_hash'], 'grants': [grants[item['evidence_id']][0] for item in pins]}
        wake['wake_hash'] = digest(wake); ledger['wakes'].append(wake); ledger['active_hash'] = None
        need(len(canonical_bytes(ledger)) <= 262144)
        job.data = {**job.data, KEY: ledger}; job.status = 'queued'
        transports = copy.deepcopy(task.data['graph_jobs']); transports[job.data['activation_id']] = {
            **transports[job.data['activation_id']], 'phase': 'queued', 'reason': '原资料及该模型授权已核对；沿原任务继续，执行前仍重核'}
        task.data = {**task.data, 'graph_jobs': transports}; task.version += 1
        audit(db, p, WAKE, job.id, wake); count += 1
    return count


def can_start_after_wait(db, task, job):
    try:
        task = scoped(db, m.Task, task.id, consent._actor(db, task.workspace_id, job.data['actor_id']))
        p = consent._actor(db, task.workspace_id, job.data['actor_id'])
        job = scoped(db, m.Job, job.id, p)
        need(job.status in {'queued', 'running'} and not consent._cancelled(task, job))
        ledger = inventory(db, p, task, job); need(ledger['history'] and ledger['active_hash'] is None)
        record = ledger['history'][-1]; consent._no_intent(db, task, job)
        _, node, cap, model = consent._current(db, p, task, job)
        proofs, pending, pins, grants = snapshot_inputs(db, p, task, cap, job.data['inputs'], model)
        compare(record, proofs, pins, grants, cap, model)
        wake = next(item for item in ledger['wakes'] if item['record_hash'] == record['record_hash'])
        consent._check_wake(wake, grants)
        return not pending and all(grants[item['evidence_id']] for item in pins)
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, StopIteration): return False


def poll_once(workspace_id):
    workspace(workspace_id)
    with Session(engine()) as db:
        result = scheduler_once(db, workspace_id=workspace_id); db.commit(); return result
