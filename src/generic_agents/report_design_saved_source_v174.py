"""Read-only source for a NEW design consumer of completed V172 Maps.

This narrow profile accepts an original terminal failed-length Reduce only.
The producer is never copied, repaired, re-labelled, settled or executed. Every
Map is read under its original draft/call binding and independently rebuilt.

The caller must supply the normal authenticated principal (or the ordinary
currently-authorized worker actor) and a real transaction. This module rechecks
the existing User/Membership and material authority, but does not validate a
cookie, grant cloud disclosure, select/publish a consumer capability, reserve a
budget or authorize a new Job. Those remain the consumer's boundary duties.
Fresh use must call check_source again; historical request recovery must not
silently reinterpret an old snapshot as a new authorization.
"""
import copy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from . import report_evidence_pipeline as pipeline
from . import report_evidence_enum_pipeline_v172 as original_maps
from . import report_evidence_enum_reduction_v172 as original_inputs
from . import report_claim_enum_native_v172 as native
from .contracts import digest
from .models import CloudCall, Job
from .report_full_semantic_handoff import material_refs
from .report_object_revision_store_v166 import _current_role
from .services import scoped
from .typed_payload_codec_v131 import canonical_json
from .evidence_request_scope_v191 import catalog_read_scope


SCHEMA = 'report-design-saved-source-v174'
SCOPE = 'all-successful-v172-maps-only'
MAX_BYTES = 8 * 1024 * 1024
ERROR = '原分组声明、已终结调用或当前资料权限无法核对；原记录保留，未修复或重发'


def _need(condition):
    if not condition:
        raise HTTPException(409, {'code': 'report_design_saved_source_invalid', 'message': ERROR})


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _identity(value):
    _need(type(value) is str and str(UUID(value)) == value)
    return value


def _job_pin(job, row, *, status, key):
    _need(job is not None and job.workspace_id == row.workspace_id
        and job.kind == pipeline.legacy.KIND and job.status == status
        and job.idempotency_key == key and job.lease_until is None
        and type(job.data) is dict and job.data.get('actor_id') == row.actor_id
        and job.data.get('report_draft_id') == row.id
        and type(job.data.get('finished_at')) is str and bool(job.data['finished_at']))
    return {'id': job.id, 'kind': job.kind, 'status': job.status,
        'idempotency_key': job.idempotency_key, 'data_hash': digest(job.data)}


def _ledger_pin(db, p, row, key, receipt, *, status):
    """Use the real frozen-reservation guard; never call settlement/finish."""
    stage = pipeline._stage(row, key)
    call = pipeline._call_guard(db, p, row, key, stage['reserved_call_id'])
    _need(call.status == status and type(call.data) is dict
        and type(call.reserved_tokens) is int and call.reserved_tokens >= 0
        and type(call.charged_tokens) is int and call.charged_tokens >= 0
        and type(call.data.get('finished_at')) is str and bool(call.data['finished_at'])
        and _same(call.data.get('receipt'), pipeline.legacy.safe_paid_receipt(receipt)))
    return {'id': call.id, 'workspace_id': call.workspace_id,
        'capability_id': call.capability_id, 'status': call.status,
        'reserved_tokens': call.reserved_tokens, 'charged_tokens': call.charged_tokens,
        'data_hash': digest(call.data), 'receipt_hash': digest(receipt)}


def _known_length_failure(db, p, row):
    """Inspect original envelope/EOF only; truncated arguments are NOT parsed."""
    stage = row.data['reduction']
    _need(row.status == 'failed' and row.data['phase'] == 'failed'
        and stage['status'] == 'failed' and type(stage['inflight']) is bool
        and row.data['model_result_hash'] is None and row.data['review_value'] is None
        and stage['result_hash'] is None and stage['result_count'] is None
        and stage['preview'] is not None and stage['start_receipt'] is not None)
    descriptor, loaded, body, inspected, binding = original_maps._read(pipeline, row, 'reduce')
    metadata, receipt = inspected['metadata'], loaded['receipt']
    _need(metadata['http_status'] == 200 and metadata['finish_reason'] == 'length'
        and metadata['envelope_valid'] is False
        and metadata['envelope_errors'] == ['finish_reason']
        and inspected['response_complete'] is True
        and receipt.get('response_complete') is True)
    try:
        native.parse_arguments(body, 200, 'reduce-v172', response_complete=True,
            http_content_encoding=inspected['http_content_encoding'])
    except ValueError:
        pass  # The native envelope gate rejects before parsing truncated JSON.
    else:
        _need(False)
    # _read cold-checks the original capture EOF, body SHA, journal/receipt and
    # native inspection. The provider's finish=length is a known failed result,
    # not an unknown network outcome. It grants no authority over its JSON tail.
    ledger = _ledger_pin(db, p, row, 'reduce', receipt, status='failed')
    job = scoped(db, Job, row.data['job_id'], p)
    job_pin = _job_pin(job, row, status='failed',
        key='evidence-reduce:' + stage['start_receipt']['request_id'])
    return {'binding': copy.deepcopy(binding), 'journal_descriptor_hash': descriptor['descriptor_hash'],
        'capture_hash': receipt['capture_hash'], 'http_body_sha256': metadata['response_body_sha256'],
        'http_body_bytes': metadata['response_body_bytes'], 'receipt_hash': digest(receipt),
        'http_status': 200, 'finish_reason': 'length', 'response_complete': True,
        'original_inflight': stage['inflight'], 'arguments_adopted': False,
        'cloud_call': ledger, 'job': job_pin}


def _load(db, p, producer_id, *, lock, current_consumer_id=None, current_scoped_attempt_id=None):
    _identity(producer_id)
    if not _current_role(db, p):
        raise HTTPException(403, '当前账号没有读取来源以生成新设计的操作权限')
    # Preserve the ordinary workspace-before-producer lock order and all old
    # pending/unknown gates. A new consumer must not clone around old authority.
    pipeline.legacy.guard_existing_paid(db, p)
    excluded = None
    _need(current_consumer_id is None or current_scoped_attempt_id is None)
    if current_consumer_id is not None:
        # This exception is only the already associated call of the ACTUAL
        # leased consumer. A caller-supplied call ID is never exclusion authority.
        from . import report_saved_map_design_consumer_v176 as consumer
        current = consumer.checked_consumer(db, p, current_consumer_id, lock=True)
        _need(current.producer_id == producer_id and current.status == 'running'
            and current.data['dispatch_started'] is True and current.reserved_call_id is not None)
        job = db.get(Job, current.job_id)
        consumer._lease(db, p, job, current)
        call = db.get(CloudCall, current.reserved_call_id)
        _need(call is not None and call.status == 'reserved')
        consumer._check_call(current, call)
        excluded = call.id
    if current_scoped_attempt_id is not None:
        # Independent scoped attempts never borrow a full-report consumer's
        # exception. Verify their own leased job, durable intent and call pin.
        from . import report_scoped_design_consumer_v181 as scoped
        current = scoped.checked_attempt(db, p, current_scoped_attempt_id, lock=True)
        _need(current.producer_id == producer_id and current.status == 'running'
            and current.data['dispatch_started'] is True and current.reserved_call_id is not None)
        job = db.get(Job, current.job_id)
        scoped._lease(db, p, job, current)
        call = db.get(CloudCall, current.reserved_call_id)
        _need(call is not None and call.status == 'reserved')
        scoped._check_call(current, call)
        excluded = call.id
    statement = select(CloudCall.id).where(CloudCall.workspace_id == p.workspace_id,
        CloudCall.status.not_in(('succeeded', 'failed')))
    if excluded is not None:
        statement = statement.where(CloudCall.id != excluded)
    unresolved = db.scalar(statement.limit(1))
    _need(unresolved is None)
    return _cold_source_context(db, p, producer_id, lock=lock)


def _cold_source_context(db, p, producer_id, *, lock=False):
    # Reuse only complete, byte-identical PURE catalog validation during this
    # one computation. All row, file, capture, ledger and capability boundaries
    # below still execute; another invocation starts an independent scope.
    with catalog_read_scope(p, producer_id, map_preparation=True):
        return _cold_source_context_uncached(db, p, producer_id, lock=lock)


def _cold_source_context_uncached(db, p, producer_id, *, lock=False):
    """Original complete immutable-source rebuild, shared without new grants.

    Admission belongs to the caller. Both entries below preserve this exact
    producer/material/Map/native-capture/ledger reconstruction and context.
    """
    row = pipeline.owned(db, p, producer_id, lock=lock)
    # owned/check_row is the existing authority. Refresh guards a caller's ORM
    # identity map too; this changes no persisted producer column or binding.
    db.refresh(row)
    pipeline.check_row(row, p)
    from . import report_design_healthy_source as healthy
    if healthy.candidate(row):
        return healthy.cold_source_context(db, p, producer_id, lock=lock)
    preview = row.data['preview']
    _need(original_maps.applies(preview)
        and preview['map_preparation']['generation_contract']['schema'] == original_maps.GENERATION)
    materials = material_refs(db, p, row, lock=lock)
    catalog, cap = pipeline._catalog(preview), pipeline._cap(preview)
    maps = original_maps.map_inputs(pipeline, row, catalog)
    inputs = original_inputs.reduction_inputs_v3(preview['map_preparation'], catalog, cap, maps)
    _need(inputs['scope'] == {'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'draft_id': row.id}
        and 1 <= len(inputs['input_order']) <= 1920)
    failure = _known_length_failure(db, p, row)
    map_job = db.scalar(select(Job).where(Job.workspace_id == row.workspace_id,
        Job.idempotency_key == 'report-evidence:' + row.request_id))
    map_job_pin = _job_pin(map_job, row, status='succeeded', key='report-evidence:' + row.request_id)
    calls = []
    for chunk, item in zip(preview['map_preparation']['plan']['chunks'], maps):
        loaded = pipeline._journal_for(row, chunk['chunk_id']).read_response(
            pipeline._stage_journal_root(row, chunk['chunk_id']), item['binding'], item['descriptor'])
        calls.append({'chunk_id': chunk['chunk_id'], 'binding': copy.deepcopy(item['binding']),
            'cloud_call': _ledger_pin(db, p, row, chunk['chunk_id'], loaded['receipt'], status='succeeded')})
    value = {'schema': SCHEMA, 'scope': SCOPE, 'actor_id': row.actor_id, 'workspace_id': row.workspace_id,
        'producer': {'id': row.id, 'revision': row.version, 'status': row.status, 'phase': row.data['phase'],
            'request_id': row.request_id, 'state_hash': digest(row.data),
            'start_command_hash': row.data['start_command_hash'],
            'start_receipt_hash': digest(row.data['start_receipt'])},
        'source_hash': inputs['source_hash'], 'catalog_hash': inputs['catalog_hash'],
        'catalog_descriptor': copy.deepcopy(catalog['descriptor']),
        'source_snapshot_hash': digest(preview['source_snapshot']), 'materials': copy.deepcopy(materials),
        'context': copy.deepcopy(inputs['context']), 'context_hash': digest(inputs['context']),
        'user_instruction': inputs['user_instruction'], 'user_instruction_hash': digest(inputs['user_instruction']),
        'producer_model': {'capability_id': preview['body']['model_id'],
            'published_model_hash': preview['published_model_hash'], 'provider': preview['provider'],
            'model': preview['model'], 'preparation_cap': copy.deepcopy(preview['preparation_cap'])},
        'map_preparation_hash': inputs['map_preparation_hash'], 'map_plan_hash': inputs['map_plan_hash'],
        'map_generation_hash': digest(preview['map_preparation']['generation_contract']),
        'map_proofs': copy.deepcopy(inputs['map_proofs']), 'map_inputs_hash': inputs['map_inputs_hash'],
        'map_calls': calls, 'map_job': map_job_pin,
        'input_order': copy.deepcopy(inputs['input_order']), 'input_order_hash': digest(inputs['input_order']),
        'input_items': copy.deepcopy(inputs['input_items']), 'input_items_hash': inputs['input_items_hash'],
        'input_claims_hash': inputs['input_claims_hash'], 'claim_count': len(inputs['input_order']),
        'allowed_span_refs': copy.deepcopy(inputs['allowed_span_refs']),
        'allowed_span_refs_hash': digest(inputs['allowed_span_refs']), 'spans': copy.deepcopy(inputs['spans']),
        'known_producer_failure': failure, 'original_reduction_adopted': False,
        'semantic_quality_verified': False, 'consumer_authorized': False, 'can_apply': False, 'can_publish': False}
    result = {**value, 'snapshot_hash': digest(value)}
    _need(len(canonical_json(result).encode('utf-8', errors='strict')) <= MAX_BYTES)
    return {'snapshot': result, 'catalog': copy.deepcopy(catalog),
        'context': copy.deepcopy(inputs['context']), 'allowed_span_refs': copy.deepcopy(inputs['allowed_span_refs'])}


def load_source_context(db, p, producer_id, *, lock=False, current_consumer_id=None, current_scoped_attempt_id=None):
    """Return the frozen snapshot and same-read validation context, with no IO writes.

    The caller owns transaction lifetime/isolation and must not use this as a
    consumer capability/consent or a live-session substitute. Locks are database
    read authority only, never a change to any original row or file.
    """
    _need(type(lock) is bool)
    try:
        with db.no_autoflush:
            return _load(db, p, producer_id, lock=lock, current_consumer_id=current_consumer_id,
                current_scoped_attempt_id=current_scoped_attempt_id)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, UnicodeError, RecursionError):
        _need(False)


def load_historical_source_context(db, p, producer_id):
    """Cold-read the original context under REAL current reader authority.

    This is no new-design admission: no workspace unknown-call exclusion,
    worker lease, cloud grant, reservation, repair or row lock is obtained.
    Existing source bytes, original Map ledgers/captures and material versions
    are still rebuilt completely; the caller compares with its frozen context.
    """
    from .report_saved_map_design_consumer_v176 import _current_reader
    try:
        with db.no_autoflush:
            _current_reader(db, p)
            _identity(producer_id)
            return _cold_source_context(db, p, producer_id, lock=False)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, UnicodeError, RecursionError):
        _need(False)


def load_source(db, p, producer_id, *, lock=False):
    return load_source_context(db, p, producer_id, lock=lock)['snapshot']


def check_source(value, db, p, *, lock=False):
    """Cold rebuild, not a self-hash check; no old producer can be re-labelled."""
    from .report_design_healthy_source import SCHEMA as HEALTHY_SCHEMA
    _need(type(value) is dict and value.get('schema') in (SCHEMA, HEALTHY_SCHEMA)
        and type(value.get('producer')) is dict and type(value['producer'].get('id')) is str)
    expected = load_source(db, p, value['producer']['id'], lock=lock)
    try:
        _need(_same(value, expected))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _need(False)
    return expected
