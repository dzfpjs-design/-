"""Current human catalogue and independently rechecked source for one batch.

History/context reads preserve human work. Preparing a new quote additionally
rechecks current origin, original files/captures, directory naming decisions and
the exact saved human document. Nothing here authorizes or dispatches a model.
Callers must acquire these locks BEFORE their Consumer actor lock.
"""
import copy

from . import report_business_catalogue_store_v211 as store
from . import report_business_catalogue_allocation_plan_v213 as planner
from . import report_scoped_design_store_v181 as plans
from . import report_design_saved_source_v174 as saved_source
from .contracts import digest


CONTEXT = 'report-business-catalogue-allocation-context-v213'
SOURCE = 'report-business-catalogue-allocation-source-v213'


def lock_before_consumer(db, p, *, write=False):
    plans._iam(db, p, write=write)
    # Catalogue._lock itself starts with the same V181 actor lock. Keep the
    # original method rather than duplicate or reverse its key/ordering.
    store._lock(db, p, write=write)


def _reference(row):
    return {'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'version': row.version, 'record_hash': row.record_hash,
        'origin_hash': row.origin_hash, 'document_hash': row.document_hash}


def _saved(db, p, draft_id):
    rows, _, _ = store._inventory(db, p)
    row = store._owned(rows, store.identity(draft_id))
    origin = row.data['origin']
    metadata, claims = store._check_origin(origin, p)
    name_origin = origin['projection'] if store._mode(origin) == store.PARTIAL else None
    reference = _reference(row)
    document = copy.deepcopy(row.data['document'])
    validation = planner.catalogue_validation(document, metadata['input_count'], name_origin=name_origin)
    compact_names = planner._name_origin(name_origin, metadata['input_count'])
    review_hash = digest({'base_draft_ref': reference, 'businesses': document['businesses'],
        'name_origin_hash': digest(compact_names)})
    return row, origin, claims, reference, document, name_origin, validation, review_hash


def context(db, p, draft_id):
    """Saved review context, never a claim that current source/grant passed."""
    lock_before_consumer(db, p)
    row, origin, _, reference, document, _, validation, review_hash = _saved(db, p, draft_id)
    writable = p.role in {'admin', 'operator'} and row.data['plan_ref'] is None
    return {'schema': CONTEXT, 'draft_ref': reference,
        'producer_id': origin['metadata']['producer_id'],
        'revision': origin['metadata']['revision'], 'catalogue_review_hash': review_hash,
        'businesses': copy.deepcopy(document['businesses']), 'validation': validation,
        'default_target_positions': sorted(set(validation['unassigned_positions'])
            | set(validation['pending_ownership_positions'])),
        'permissions': {'can_request_preview': writable and validation['catalogue_structurally_ready']},
        'current_source_verified': False, 'actual_paid_verified': False,
        'can_dispatch': False, 'can_apply': False, 'can_confirm': False}


def prepare_source(db, p, draft_id, *, expected_version, expected_document_hash,
        expected_catalogue_review_hash, selection, max_batch_items, write=False,
        current_consumer_id=None):
    """Fresh source for a separately budgeted, separately consented activity."""
    plans._iam(db, p, write=True)
    lock_before_consumer(db, p, write=write)
    row, origin, claims, reference, document, name_origin, validation, review_hash = _saved(db, p, draft_id)
    store.need(row.data['plan_ref'] is None, '已确认的业务目录保持只读，不能重新归类覆盖')
    store.need(type(expected_version) is int and expected_version == row.version
        and expected_document_hash == row.document_hash
        and expected_catalogue_review_hash == review_hash,
        '业务目录或人工稿已变化，原输入保留，请重新核对本批范围')
    store.need(validation['catalogue_structurally_ready'],
        '请先核对业务名称及名称依据，再整理资料归属')
    # This is intentionally the live adoption/source gate, not an origin-hash
    # check, a saved Plan's permission, or a fabricated principal.
    if current_consumer_id is None:
        store._current_source(db, p, origin)
    else:
        # Only the existing source reader may exclude an already associated
        # reserved call: it verifies that actual Consumer, Job lease, actor,
        # dispatch checkpoint and CloudCall. A supplied call ID is never used.
        metadata = origin['metadata']
        fresh = store._load_origin(db, p, metadata['consumer_id'], origin_mode=store._mode(origin))
        store.need(store.same(store._origin_content(fresh), store._origin_content(origin)),
            '原目录回答或接收记录已变化，未继续发送本批资料')
        configuration = saved_source.pipeline.legacy.workspace_draft(db, p, lock=True)
        store.need(configuration.version == metadata['revision'],
            '当前配置版本已变化，原申请保留，未继续发送')
        actual = saved_source.load_source_context(db, p, metadata['producer_id'],
            lock=True, current_consumer_id=current_consumer_id)
        store.need(store.same(actual, origin['source_context']),
            '当前报告资料或权限已变化，原申请保留，未继续发送')
    snapshot = origin['source_context']['snapshot']
    plan = planner.build_plan(indexed=origin['indexed_inputs'], input_claims=claims,
        source_snapshot_hash=snapshot['snapshot_hash'], catalog=origin['source_context']['catalog'],
        input_order=snapshot['input_order'], draft_ref=reference, document=document,
        selection=selection, max_batch_items=max_batch_items, name_origin=name_origin)
    store.need(plan['catalogue_review_hash'] == review_hash)
    return {'schema': SOURCE, 'producer_id': origin['metadata']['producer_id'],
        'revision': origin['metadata']['revision'], 'draft_ref': reference,
        'document': document, 'draft_origin': copy.deepcopy(origin),
        'source_context': copy.deepcopy(origin['source_context']),
        'name_origin': copy.deepcopy(name_origin), 'plan': plan,
        'catalogue_review_hash': review_hash, 'current_source_verified': True,
        'actual_paid_verified': False, 'can_dispatch': False,
        'can_apply': False, 'can_confirm': False}
