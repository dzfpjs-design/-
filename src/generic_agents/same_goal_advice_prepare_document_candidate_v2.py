"""Unrouted v2 local-document handler for an isolated dedicated worker.

One database transaction owns the locked original queued job through receipt
and terminal state. No network call, cloud consent, preview or send is made.
Rollback may leave a content-addressed blob cache, never a database receipt.
The generic worker must remain stopped/isolated; this is not deployable yet.
"""
import copy

from sqlalchemy import select

from . import evidence
from . import same_goal_advice_preparation_v304 as legacy
from . import same_goal_advice_sources_v302 as sources
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_contracts_v2 as c
from . import same_goal_advice_unlimited_plan_candidate_v2 as candidate
from . import same_goal_advice_unlimited_store_v2 as v2
from . import same_goal_advice_prepare_writer_candidate_v2 as writer
from .contracts import digest
from .models import Audit, Evidence, Job, Plan, Task, uid
from .relations import lock_current_actor

SCHEMA = 'same-goal-advice-unlimited-document-preparation-v2'
FIELDS = {'schema', 'receipt_id', 'workspace_id', 'task_id', 'plan_id',
    'preparation_snapshot_hash', 'prepare_job_id', 'prepare_job_key', 'actor_id',
    'selection', 'approved_selection', 'source_snapshot_hash', 'render_sha256',
    'size_bytes', 'evidence_ref', 'original_packet_receipt', 'upload_audit_ref',
    'processing_mode', 'record_hash'}


def _marker(record):
    return {'schema': SCHEMA, 'receipt_ref': {'id': record['receipt_id'], 'record_hash': record['record_hash']},
        'evidence_ref': copy.deepcopy(record['evidence_ref']), 'prepare_job_id': record['prepare_job_id'],
        'preparation_snapshot_hash': record['preparation_snapshot_hash']}


def _file_marker(record):
    return {'schema': SCHEMA + '/evidence', 'task_id': record['task_id'], 'plan_id': record['plan_id'],
        'prepare_job_id': record['prepare_job_id'], 'preparation_snapshot_hash': record['preparation_snapshot_hash'],
        'receipt_id': record['receipt_id'], 'receipt_hash': record['record_hash']}


@old.checked
def verify_original(db, p, task, plan, prep, job, events, files):
    """Cold-check original source, durable bytes, upload and receipt; no grant."""
    old.need(len(events) == 1 and events[0].action == v2.DOCUMENT_PREPARED)
    event = events[0]; value = event.data
    old.need(type(value) is dict and set(value) == FIELDS and value['schema'] == SCHEMA
        and value['record_hash'] == c.old.record_hash(value) and value['receipt_id'] == event.id
        and event.object_id == plan.id and event.actor_id == prep['data_actor_id'] == value['actor_id']
        and value['workspace_id'] == p.workspace_id and value['task_id'] == task.id and value['plan_id'] == plan.id
        and value['prepare_job_id'] == job.id and value['prepare_job_key'] == job.idempotency_key
        and value['preparation_snapshot_hash'] == prep['record_hash']
        and value['processing_mode'] == 'one_locked_transaction'
        and digest(plan.data['document_preparation']) == digest(_marker(value)))
    selection = prep['request']['document_source']
    old.need(selection is not None and digest(value['selection']) == digest(selection))
    resolved = sources.resolve_documents(db, p, task, selection)
    old.need(digest(value['approved_selection']) == digest(resolved['approved_selection'])
        and value['source_snapshot_hash'] == resolved['source_set']['source_snapshot_hash']
        and value['render_sha256'] == resolved['sha256'] and value['size_bytes'] == resolved['size_bytes']
        and digest(value['original_packet_receipt']) == digest(resolved['packet_receipt']))
    ref = c.old._parse(c.old.EvidenceRef, value['evidence_ref'])
    actual, raw = evidence.evidence_bytes(db, p, ref['evidence_id'])
    old.need(actual.workspace_id == p.workspace_id and digest(sources._text(actual, raw)) == digest(ref)
        and raw == resolved['content'] and ref['sha256'] == value['render_sha256']
        and ref['size_bytes'] == value['size_bytes'])
    if selection['kind'] == 'approved_source_set':
        old.need(len(files) == 1 and files[0].id == actual.id
            and actual.data['uploaded_by'] == prep['data_actor_id']
            and digest(actual.data.get(v2.DOCUMENT_KEY)) == digest(_file_marker(value)))
        uploader = prep['data_actor_id']
    else:
        old.need(not files and digest(ref) == digest(resolved['evidence_ref']))
        packet = legacy._fresh(db, p, sources.packets.Packet, selection['packet']['id'])
        uploader = packet.actor_id
    upload = legacy._upload(db, p, actual, uploader)
    old.need(value['upload_audit_ref'] == {'id': upload.id, 'record_hash': legacy._fingerprint(upload)})
    return copy.deepcopy(value)


@old.checked
def process(db, p, task_id, job_id, *, delegation_id=None):
    """Run only the fixed v2 kind, with a caller-owned atomic transaction."""
    writer.require_disposable_database(db)
    old._reader(db, p, write=True)
    task = old.existing._task(db, p, task_id, lock=True)
    lock_current_actor(db, p)
    job = db.scalar(select(Job).where(Job.id == job_id, Job.workspace_id == p.workspace_id)
        .with_for_update().execution_options(populate_existing=True))
    old.need(job is not None and job.kind == v2.PREPARE_KIND and job.available_at == writer.UNAVAILABLE_TO_GENERAL_WORKERS
        and type(job.data) is dict and job.data.get('task_id') == task.id)
    plan = db.scalar(select(Plan).where(Plan.id == job.data['plan_id'], Plan.task_id == task.id,
        Plan.workspace_id == p.workspace_id).with_for_update().execution_options(populate_existing=True))
    old.need(plan is not None)
    if job.status == 'succeeded':
        prep, original = v2.read_prepared(db, p, task, plan)
        old.need(original.id == job.id and p.user_id == prep['data_actor_id'])
        return {'plan_id': plan.id, 'job_id': job.id, 'replayed': True}
    prep, original = v2.read_queued(db, p, task, plan)
    old.need(original.id == job.id and p.user_id == prep['data_actor_id'])
    # Re-derive the actual current policy, Goal, source and actor just before
    # local processing. Candidate-generated Plan IDs have no authority.
    if delegation_id is None:
        proposal = candidate.derive_candidate(db, p, task.id, prep['request'], prep['budget']['policy_ref'])
    else:
        # First usable slice: adopt an existing, approved V207 packet without
        # creating a shared file. Source-set file writes remain disabled.
        old.need(prep['request']['document_source']['kind'] == 'document_packet_v207',
            '离线处理需先准备并批准原资料包；未写入新文件')
        proposal = candidate.derive_delegated(db, p, task.id, job.id, prep['request'],
            prep['budget']['policy_ref'], delegation_id)
    fresh = copy.deepcopy(proposal['preparation_candidate']); fresh.pop('record_hash')
    fresh['scope']['analysis_plan_id'] = plan.id
    cap = old._fresh(db, p, old.Capability, prep['advice_capability_ref']['id'])
    old.need(digest(c.seal_preparation(fresh, manifest=cap.data)) == digest(prep))
    selection = prep['request']['document_source']; resolved = sources.resolve_documents(db, p, task, selection)
    receipt_id = uid()
    if selection['kind'] == 'approved_source_set':
        # The committed intent survives this transaction rolling back.
        from .same_goal_advice_file_intent_candidate_v2 import require_committed, check_private_directory, _check_current, _bind_commit_guard
        require_committed(db, p, task, plan, job, prep, resolved)
        check_private_directory(p, resolved['sha256'])
        _check_current(db, p, task.id, prep['budget']['policy_ref'])
        _bind_commit_guard(db, p, task.id, prep['budget']['policy_ref'])
        file = evidence.save_evidence(db, p, resolved['content'], 'text/plain')
        _check_current(db, p, task.id, prep['budget']['policy_ref'])
        old.need(file.data.get('cloud_model_ids') == [] and file.data.get('uploaded_by') == p.user_id
            and file.version == 1)
        uploader = p.user_id
    else:
        file = legacy._fresh(db, p, Evidence, resolved['evidence_ref']['evidence_id'])
        packet = legacy._fresh(db, p, sources.packets.Packet, selection['packet']['id'])
        uploader = packet.actor_id
    actual, raw = evidence.evidence_bytes(db, p, file.id); ref = sources._text(actual, raw)
    old.need(actual.workspace_id == p.workspace_id and actual.id == file.id
        and raw == resolved['content'] and ref['sha256'] == resolved['sha256'])
    db.flush()
    upload = legacy._upload(db, p, actual, uploader)
    value = {'schema': SCHEMA, 'receipt_id': receipt_id, 'workspace_id': p.workspace_id,
        'task_id': task.id, 'plan_id': plan.id, 'preparation_snapshot_hash': prep['record_hash'],
        'prepare_job_id': job.id, 'prepare_job_key': job.idempotency_key, 'actor_id': p.user_id,
        'selection': copy.deepcopy(selection), 'approved_selection': copy.deepcopy(resolved['approved_selection']),
        'source_snapshot_hash': resolved['source_set']['source_snapshot_hash'], 'render_sha256': resolved['sha256'],
        'size_bytes': len(raw), 'evidence_ref': ref, 'original_packet_receipt': copy.deepcopy(resolved['packet_receipt']),
        'upload_audit_ref': {'id': upload.id, 'record_hash': legacy._fingerprint(upload)},
        'processing_mode': 'one_locked_transaction'}
    value['record_hash'] = c.old.record_hash(value)
    event = Audit(id=receipt_id, workspace_id=p.workspace_id, actor_id=p.user_id,
        action=v2.DOCUMENT_PREPARED, object_id=plan.id, data=value)
    if selection['kind'] == 'approved_source_set': actual.data = {**actual.data, v2.DOCUMENT_KEY: _file_marker(value)}
    db.add(event); plan.data = {**plan.data, 'document_preparation': _marker(value)}
    plan.status = 'awaiting_consent'; plan.version += 1
    job.status = 'succeeded'; job.attempts = 1; job.version += 1
    db.flush()
    saved, original = v2.read_prepared(db, p, task, plan)
    old.need(saved == prep and original.id == job.id)
    return {'plan_id': plan.id, 'job_id': job.id, 'replayed': False}
