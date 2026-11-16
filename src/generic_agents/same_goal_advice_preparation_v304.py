"""Fenced, local-only preparation of one original advice Plan's documents.

SourceSet selections produce a new text Evidence. A real V207 Packet retains
its original Evidence/receipt; the independent preparation Audit records only
this Plan's checked adoption. Worker completion commits, not this module.
Rollback-only content-addressed blobs are storage cache, never receipts.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import evidence, leases
from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_sources_v302 as sources
from . import same_goal_advice_store_v302 as store
from .contracts import digest
from .models import Audit, Evidence, Job, Plan, Task, uid
from .relations import lock_current_actor

SCHEMA = 'same-goal-advice-document-preparation-v304'
KEY = 'same_goal_advice_document_preparation_v304'
PREPARED = 'same_goal_advice.documents_prepared_v304'
ERROR = '原计划资料准备、真实后台租约或独立保存证明无法核对；未另造资料、调用模型或取得授权'
_RECEIPT_FIELDS = {'schema', 'receipt_id', 'workspace_id', 'task_id', 'plan_id',
    'preparation_snapshot_hash', 'prepare_job_id', 'prepare_job_key', 'actor_id', 'creator_lease_hash',
    'selection', 'approved_selection', 'source_snapshot_hash', 'render_sha256', 'size_bytes',
    'evidence_ref', 'original_packet_receipt', 'upload_audit_ref', 'record_hash'}
_MARKER_FIELDS = {'schema', 'receipt_ref', 'evidence_ref', 'prepare_job_id', 'preparation_snapshot_hash'}
checked = sources.checked


def need(value):
    if not value: raise HTTPException(409, ERROR)


def _fresh(db, p, model, identity, *, lock=False):
    query = select(model).where(model.workspace_id == p.workspace_id, model.id == identity)
    if lock: query = query.with_for_update()
    rows = list(db.scalars(query.limit(2).execution_options(populate_existing=True)))
    need(len(rows) == 1)
    return rows[0]


def _lease(db, p, job):
    owner = leases.ownership(db)
    need(leases.fenced(db) and set(owner) == {'job_id', 'lease_token'}
        and owner['job_id'] == job.id and bool(owner['lease_token'])
        and job.workspace_id == p.workspace_id and job.kind == c.PREPARE_KIND
        and job.status == 'running' and job.lease_token == owner['lease_token']
        and job.data['schema'] == store.JOB_SCHEMA and job.data['actor_id'] == p.user_id)
    with db.no_autoflush:
        actual = db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
            Job.kind == c.PREPARE_KIND, Job.status == 'running', Job.lease_token == owner['lease_token'],
            Job.lease_until > func.clock_timestamp(), Job.data['actor_id'].as_string() == p.user_id))
    need(actual == job.id)
    return owner


def _fingerprint(event):
    return digest({key: getattr(event, key) for key in
        ('id', 'workspace_id', 'actor_id', 'action', 'object_id', 'data')})


def _upload(db, p, file, actor):
    rows = store._rows(db, p, Audit, Audit.action == 'evidence.upload', Audit.object_id == file.id)
    need(len(rows) == 1 and rows[0].actor_id == actor
        and digest(rows[0].data) == digest({'media_type': 'text/plain', 'sha256': file.sha256,
            'cloud_model_id': None, 'consent': False}))
    return rows[0]


def _file_marker(plan, job, event, record):
    return {'schema': SCHEMA + '/evidence', 'plan_id': plan.id, 'prepare_job_id': job.id,
        'preparation_snapshot_hash': record['preparation_snapshot_hash'],
        'receipt_id': event.id, 'receipt_hash': record['record_hash']}


def _marker(record):
    return {'schema': SCHEMA, 'receipt_ref': {'id': record['receipt_id'], 'record_hash': record['record_hash']},
        'evidence_ref': copy.deepcopy(record['evidence_ref']), 'prepare_job_id': record['prepare_job_id'],
        'preparation_snapshot_hash': record['preparation_snapshot_hash']}


def _inventory(db, p, task, plan, job):
    events = store._rows(db, p, Audit, Audit.action == PREPARED, or_(Audit.object_id == plan.id,
        Audit.data['plan_id'].as_string() == plan.id, Audit.data['prepare_job_id'].as_string() == job.id))
    files = store._rows(db, p, Evidence, or_(Evidence.data[KEY]['plan_id'].as_string() == plan.id,
        Evidence.data[KEY]['prepare_job_id'].as_string() == job.id))
    return events, files


def _read(db, p, task, plan, prep, job, *, running_job=None):
    events, files = _inventory(db, p, task, plan, job)
    marker = plan.data['document_preparation']
    if marker is None and not events and not files:
        need(job.status != 'succeeded')
        return None
    need(type(marker) is dict and set(marker) == _MARKER_FIELDS and len(events) == 1)
    event = events[0]; value = event.data
    need(type(value) is dict and set(value) == _RECEIPT_FIELDS and value['schema'] == SCHEMA
        and value['record_hash'] == c.record_hash(value) and value['receipt_id'] == event.id
        and event.object_id == plan.id and event.actor_id == prep['data_actor_id'] == value['actor_id']
        and value['workspace_id'] == p.workspace_id and value['task_id'] == task.id and value['plan_id'] == plan.id
        and value['prepare_job_id'] == job.id and value['prepare_job_key'] == job.idempotency_key
        and value['preparation_snapshot_hash'] == prep['record_hash']
        and digest(marker) == digest(_marker(value)))
    c._parse(c.Ref, {'id': event.id, 'record_hash': value['creator_lease_hash']})
    if running_job is not None:
        need(running_job is job); _lease(db, p, job)
    else:
        need(job.status == 'succeeded' and job.lease_until is None)
    selection = prep['request']['document_source']
    need(selection is not None and digest(value['selection']) == digest(selection))
    resolved = sources.resolve_documents(db, p, task, selection)
    need(digest(value['approved_selection']) == digest(resolved['approved_selection'])
        and value['source_snapshot_hash'] == resolved['source_set']['source_snapshot_hash']
        and value['render_sha256'] == resolved['sha256'] and value['size_bytes'] == resolved['size_bytes']
        and digest(value['original_packet_receipt']) == digest(resolved['packet_receipt']))
    ref = c._parse(c.EvidenceRef, value['evidence_ref'])
    file = _fresh(db, p, Evidence, ref['evidence_id'])
    actual, raw = evidence.evidence_bytes(db, p, file.id)
    need(actual.id == file.id and actual.workspace_id == p.workspace_id
        and digest(sources._text(actual, raw)) == digest(ref) and raw == resolved['content']
        and ref['sha256'] == value['render_sha256'] and ref['size_bytes'] == value['size_bytes'])
    if selection['kind'] == 'approved_source_set':
        need(len(files) == 1 and files[0].id == actual.id
            and actual.data['uploaded_by'] == prep['data_actor_id']
            and digest(actual.data.get(KEY)) == digest(_file_marker(plan, job, event, value)))
        uploader = prep['data_actor_id']; document_receipt = {'id': event.id, 'record_hash': value['record_hash']}
    else:
        need(not files and digest(ref) == digest(resolved['evidence_ref']))
        packet = _fresh(db, p, sources.packets.Packet, selection['packet']['id'])
        uploader = packet.actor_id
        document_receipt = {'id': packet.id, 'record_hash': resolved['packet_receipt']['producer_hash']}
    upload = _upload(db, p, actual, uploader)
    need(value['upload_audit_ref'] == {'id': upload.id, 'record_hash': _fingerprint(upload)})
    return {'marker': copy.deepcopy(marker), 'receipt': copy.deepcopy(value),
        'receipt_ref': copy.deepcopy(marker['receipt_ref']), 'evidence_ref': ref,
        'prepare_job_id': job.id, 'preparation_snapshot_hash': prep['record_hash'],
        'document': {'selection': copy.deepcopy(selection), 'approved_selection': copy.deepcopy(value['approved_selection']),
            'evidence_ref': copy.deepcopy(ref), 'receipt_ref': document_receipt}, 'content': raw}


@checked
def cold_read(db, p, task, plan):
    """Read a real completed artifact, including cancelled/historical Plans.

    No current head selection, freshness refresh, lease bypass, cloud consent
    or repair happens here. Underlying original sources remain authenticated.
    The returned local bytes are server-only; public views use marker only.
    """
    with db.no_autoflush:
        store._reader(db, p)
        task = _fresh(db, p, Task, task.id); plan = _fresh(db, p, Plan, plan.id)
        prep, job = store._saved(db, p, task, plan)
        return _read(db, p, task, plan, prep, job)


@checked
def run(db, p, job):
    """Normal worker handler. The caller retains the only commit authority."""
    need(job.kind == c.PREPARE_KIND and job.data.get('schema') == store.JOB_SCHEMA)
    with db.no_autoflush:
        store._reader(db, p, write=True)
        task = _fresh(db, p, Task, job.data['task_id'], lock=True)
        plan = _fresh(db, p, Plan, job.data['plan_id'], lock=True)
        job = _fresh(db, p, Job, job.id, lock=True)
        owner = _lease(db, p, job); lock_current_actor(db, p)
        prep, original_job = store._saved(db, p, task, plan)
        need(original_job.id == job.id and p.user_id == prep['data_actor_id'])
        if plan.status == 'cancelled': return 'cancelled'
        need(plan.status in {'queued', 'running', 'awaiting_consent'} and len(plan.data['operations']) == 1)
        # The handler may have committed its complete artifact before the
        # worker finalized this original Job. Reclaiming it only closes that
        # historical work; it neither creates new bytes nor renews a sending
        # grant. Current reader/actor/fence/custody and original sources above
        # and in _read still apply. Any partial inventory fails closed here.
        if _read(db, p, task, plan, prep, job, running_job=job) is not None:
            return 'succeeded'
        context = store._scope(db, p, task, prep['request'], plan.id)
        expected_context = set(c.PreparationSnapshot.model_fields) - {
            'schema', 'request', 'request_hash', 'requested_by', 'record_hash'}
        need(type(context) is dict and set(context) == expected_context | {'manifest'}
            and all(key == 'manifest' or digest(prep[key]) == digest(value) for key, value in context.items()))
        selection = prep['request']['document_source']; need(selection is not None)
        resolved = sources.resolve_documents(db, p, task, selection)
        receipt_id = uid()
        if selection['kind'] == 'approved_source_set':
            file = evidence.save_evidence(db, p, resolved['content'], 'text/plain')
            need(file.data.get('cloud_model_ids') == [] and file.data.get('uploaded_by') == p.user_id
                and file.version == 1)
            uploader = p.user_id
        else:
            file = _fresh(db, p, Evidence, resolved['evidence_ref']['evidence_id'])
            packet = _fresh(db, p, sources.packets.Packet, selection['packet']['id'])
            uploader = packet.actor_id
        actual, raw = evidence.evidence_bytes(db, p, file.id)
        ref = sources._text(actual, raw)
        need(actual.workspace_id == p.workspace_id and actual.id == file.id
            and raw == resolved['content'] and ref['sha256'] == resolved['sha256'])
        db.flush()  # Persist upload's independent Audit before a no-autoflush cold read.
        upload = _upload(db, p, actual, uploader)
        value = {'schema': SCHEMA, 'receipt_id': receipt_id, 'workspace_id': p.workspace_id,
            'task_id': task.id, 'plan_id': plan.id, 'preparation_snapshot_hash': prep['record_hash'],
            'prepare_job_id': job.id, 'prepare_job_key': job.idempotency_key, 'actor_id': p.user_id,
            'creator_lease_hash': digest(owner['lease_token']), 'selection': copy.deepcopy(selection),
            'approved_selection': copy.deepcopy(resolved['approved_selection']),
            'source_snapshot_hash': resolved['source_set']['source_snapshot_hash'],
            'render_sha256': resolved['sha256'], 'size_bytes': len(raw), 'evidence_ref': ref,
            'original_packet_receipt': copy.deepcopy(resolved['packet_receipt']),
            'upload_audit_ref': {'id': upload.id, 'record_hash': _fingerprint(upload)}}
        value['record_hash'] = c.record_hash(value)
        event = Audit(id=receipt_id, workspace_id=p.workspace_id, actor_id=p.user_id,
            action=PREPARED, object_id=plan.id, data=value)
        if selection['kind'] == 'approved_source_set':
            actual.data = {**actual.data, KEY: _file_marker(plan, job, event, value)}
        db.add(event)
        plan.data = {**plan.data, 'document_preparation': _marker(value)}
        plan.status = 'awaiting_consent'; plan.version += 1
        db.flush()
        need(_read(db, p, task, plan, prep, job, running_job=job) is not None)
        return 'succeeded'


@checked
def fail_job(db, p, job, reason):
    """Record failure before the worker stages its original Job's terminal state.

    The normal recovery Principal is a read-only projection of the original
    actor. This grants no execution and intentionally does not require a live
    writer role that may itself have been revoked. Job.error owns the bounded
    failure explanation; this hook preserves all sealed and artifact data.
    """
    need(job.kind == c.PREPARE_KIND and job.data.get('schema') == store.JOB_SCHEMA)
    with db.no_autoflush:
        task = _fresh(db, p, Task, job.data['task_id'], lock=True)
        plan = _fresh(db, p, Plan, job.data['plan_id'], lock=True)
        job = _fresh(db, p, Job, job.id, lock=True)
        _lease(db, p, job)
        prep, original_job = store._saved(db, p, task, plan)
        need(original_job.id == job.id and p.user_id == prep['data_actor_id'])
        if plan.status == 'cancelled': return 'cancelled'
        if plan.status != 'failed':
            plan.status = 'failed'; plan.version += 1
        return 'failed'
