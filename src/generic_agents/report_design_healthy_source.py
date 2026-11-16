"""Read original V172 Map claims from a normally successful report producer."""
import copy
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from . import report_design_saved_source_v174 as original
from . import report_evidence_enum_pipeline_v172 as enum_maps
from . import report_evidence_enum_reduction_v172 as enum_inputs
from . import report_evidence_pipeline as pipeline
from . import report_evidence_reduction_wire_v168 as input_validator
from .contracts import digest
from .evidence_request_scope_v191 import catalog_read_scope
from .models import Audit, Job, SetupReportDraft
from .report_evidence_plan import resolve_evidence
from .report_full_semantic_handoff import material_refs
from .services import scoped
from .typed_payload_codec_v131 import canonical_json

SCHEMA = 'report-design-healthy-source-v1'
SCOPE = 'verified-v172-map-claims-and-successful-reduce'
ERROR = '正常报告的原分段声明、成功汇总或独立调用记录无法核对'
MAX_BYTES = original.MAX_BYTES
_HASH = re.compile(r'^[a-f0-9]{64}$')
_FIELDS = frozenset(('schema scope actor_id workspace_id producer source_hash catalog_hash catalog_descriptor '
    'source_snapshot_hash materials context context_hash user_instruction user_instruction_hash producer_model '
    'map_preparation_hash map_plan_hash map_generation_hash map_proofs map_inputs_hash map_calls map_job '
    'input_order input_order_hash input_items input_items_hash input_claims_hash claim_count allowed_span_refs '
    'allowed_span_refs_hash spans healthy_reduction original_reduction_adopted semantic_quality_verified '
    'consumer_authorized can_apply can_publish snapshot_hash').split())
_REDUCTION_FIELDS = frozenset(('binding journal_descriptor_hash capture_hash http_body_sha256 http_body_bytes '
    'receipt_hash http_status finish_reason response_complete arguments_sha256 arguments_bytes '
    'reduction_preparation_hash result_hash model_result_hash result_count cloud_call job stage_audit_ref').split())


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def candidate(row):
    """Cheap exact-family hint. Only cold_source_context authenticates it."""
    if row is None or row.status not in {'review', 'needs_clarification'} or type(row.data) is not dict:
        return False
    stage = row.data.get('reduction')
    return (row.data.get('phase') == 'review' and enum_maps.applies(row.data.get('preview'))
        and type(stage) is dict and stage.get('status') == 'succeeded' and stage.get('inflight') is False)


def _reduction(db, p, row):
    stage = row.data['reduction']
    _need(stage['preview'] is not None and stage['start_receipt'] is not None
        and stage['result_hash'] is not None and row.data['model_result_hash'] is not None)
    # check_row has already rebuilt and validated the successful reduction,
    # reconciled original input positions, and checked the saved result hashes.
    descriptor, loaded, _, inspected, binding = enum_maps._read(pipeline, row, 'reduce')
    metadata, receipt = inspected['metadata'], loaded['receipt']
    _need(metadata['http_status'] == 200 and metadata['finish_reason'] == 'stop'
        and metadata['envelope_valid'] is True and metadata['envelope_errors'] == []
        and inspected['response_complete'] is True and receipt['response_complete'] is True)
    ledger = original._ledger_pin(db, p, row, 'reduce', receipt, status='succeeded')
    job = scoped(db, Job, row.data['job_id'], p)
    job_pin = original._job_pin(job, row, status='succeeded',
        key='evidence-reduce:' + stage['start_receipt']['request_id'])
    audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.action == 'setup.report_evidence.stage_saved',
        Audit.data['stage'].as_string() == 'reduce').limit(2)))
    _need(len(audits) == 1 and audits[0].actor_id == row.actor_id
        and audits[0].data == {'stage': 'reduce', 'result_hash': stage['result_hash'],
            'result_count': stage['result_count']})
    return {'binding': copy.deepcopy(binding), 'journal_descriptor_hash': descriptor['descriptor_hash'],
        'capture_hash': receipt['capture_hash'], 'http_body_sha256': metadata['response_body_sha256'],
        'http_body_bytes': metadata['response_body_bytes'], 'receipt_hash': digest(receipt),
        'http_status': 200, 'finish_reason': 'stop', 'response_complete': True,
        'arguments_sha256': metadata['arguments_sha256'], 'arguments_bytes': metadata['arguments_bytes'],
        'reduction_preparation_hash': pipeline._authorized(row, 'reduce')['preparation_hash'],
        'result_hash': stage['result_hash'], 'model_result_hash': row.data['model_result_hash'],
        'result_count': stage['result_count'], 'cloud_call': ledger, 'job': job_pin,
        'stage_audit_ref': {'id': audits[0].id, 'record_hash': digest(audits[0].data)}}


def _load(db, p, producer_id, *, lock):
    query = select(SetupReportDraft).where(SetupReportDraft.id == producer_id,
        SetupReportDraft.workspace_id == p.workspace_id, SetupReportDraft.actor_id == p.user_id)
    if lock:
        query = query.with_for_update()
    row = pipeline.check_row(db.scalar(query.execution_options(populate_existing=True)), p)
    _need(candidate(row))
    preview = row.data['preview']
    materials = material_refs(db, p, row, lock=lock)
    catalog, cap = pipeline._catalog(preview), pipeline._cap(preview)
    maps = enum_maps.map_inputs(pipeline, row, catalog)
    inputs = enum_inputs.reduction_inputs_v3(preview['map_preparation'], catalog, cap, maps)
    _need(inputs['scope'] == {'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'draft_id': row.id}
        and 1 <= len(inputs['input_order']) <= 1920)
    reduction = _reduction(db, p, row)
    map_key = 'report-evidence:' + row.request_id
    map_job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == map_key))
    map_job_pin = original._job_pin(map_job, row, status='succeeded', key=map_key)
    calls = []
    for item in maps:
        binding = item['binding']
        key = binding['stage']
        captured = pipeline._journal_for(row, key).read_response(
            pipeline._stage_journal_root(row, key), binding, item['descriptor'])
        calls.append({'chunk_id': key, 'binding': copy.deepcopy(binding),
            'cloud_call': original._ledger_pin(db, p, row, key, captured['receipt'], status='succeeded')})
    value = {'schema': SCHEMA, 'scope': SCOPE, 'actor_id': row.actor_id, 'workspace_id': row.workspace_id,
        'producer': {'id': row.id, 'revision': row.version, 'status': row.status, 'phase': row.data['phase'],
            'request_id': row.request_id, 'state_hash': digest(row.data),
            'start_command_hash': row.data['start_command_hash'], 'start_receipt_hash': digest(row.data['start_receipt'])},
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
        'healthy_reduction': reduction, 'original_reduction_adopted': False,
        'semantic_quality_verified': False, 'consumer_authorized': False, 'can_apply': False, 'can_publish': False}
    result = {'snapshot': {**value, 'snapshot_hash': digest(value)}, 'catalog': copy.deepcopy(catalog),
        'context': copy.deepcopy(inputs['context']), 'allowed_span_refs': copy.deepcopy(inputs['allowed_span_refs'])}
    check_context(result)
    return result


def cold_source_context(db, p, producer_id, *, lock=False):
    """Authenticate and rebuild positive history, without admitting a consumer."""
    from .report_saved_map_design_consumer_v176 import _current_reader

    try:
        with db.no_autoflush, catalog_read_scope(p, producer_id, map_preparation=True):
            _need(type(lock) is bool)
            original._identity(producer_id)
            _current_reader(db, p)
            return _load(db, p, producer_id, lock=lock)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, ERROR) from None


def _hashes(values):
    _need(all(type(value) is str and _HASH.fullmatch(value) for value in values))


def _id(value):
    _need(type(value) is str and str(UUID(value)) == value)


def _binding(value, snapshot, stage):
    producer = snapshot['producer']
    _need(value['actor_id'] == snapshot['actor_id'] and value['workspace_id'] == snapshot['workspace_id']
        and value['draft_id'] == producer['id'] and value['stage'] == stage)


def _check_reduction(reduction, value):
    _need(type(reduction) is dict and set(reduction) == _REDUCTION_FIELDS
        and type(reduction['http_status']) is int and reduction['http_status'] == 200
        and reduction['finish_reason'] == 'stop' and reduction['response_complete'] is True
        and all(type(reduction[key]) is int and reduction[key] > 0
            for key in ('http_body_bytes', 'arguments_bytes', 'result_count'))
        and reduction['arguments_bytes'] <= reduction['http_body_bytes'])
    _hashes(reduction[key] for key in ('journal_descriptor_hash', 'capture_hash', 'http_body_sha256',
        'receipt_hash', 'arguments_sha256', 'reduction_preparation_hash', 'result_hash', 'model_result_hash'))
    binding, call, job = (reduction[key] for key in ('binding', 'cloud_call', 'job'))
    _binding(binding, value, 'reduce')
    _id(reduction['stage_audit_ref']['id'])
    _id(job['id'])
    _need(call['id'] == binding['reserved_call_id'] and call['status'] == 'succeeded'
        and call['workspace_id'] == value['workspace_id'] and call['receipt_hash'] == reduction['receipt_hash']
        and call['capability_id'] == value['producer_model']['capability_id']
        and job['kind'] == pipeline.legacy.KIND and job['status'] == 'succeeded'
        and job['idempotency_key'].startswith('evidence-reduce:')
        and job['id'] != value['map_job']['id']
        and set(reduction['stage_audit_ref']) == {'id', 'record_hash'})
    expected_audit = {'stage': 'reduce', 'result_hash': reduction['result_hash'],
        'result_count': reduction['result_count']}
    _need(reduction['stage_audit_ref']['record_hash'] == digest(expected_audit))


def check_context(source_context):
    """Check the new source shape and ordered real claim array, without IO."""
    try:
        return _check_context(source_context)
    except (KeyError, TypeError, AttributeError, OSError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _check_context(source_context):
    _need(type(source_context) is dict and set(source_context) == {'snapshot', 'catalog', 'context', 'allowed_span_refs'})
    value = source_context['snapshot']
    _need(type(value) is dict and set(value) == _FIELDS and value['schema'] == SCHEMA and value['scope'] == SCOPE
        and len(canonical_json(value).encode('utf-8', errors='strict')) <= MAX_BYTES
        and value['snapshot_hash'] == digest({key: item for key, item in value.items() if key != 'snapshot_hash'}))
    producer = value['producer']
    _need(type(producer) is dict and set(producer) == {'id', 'revision', 'status', 'phase', 'request_id',
        'state_hash', 'start_command_hash', 'start_receipt_hash'} and producer['status'] in {'review', 'needs_clarification'}
        and producer['phase'] == 'review' and type(producer['revision']) is int and producer['revision'] >= 1)
    for identity in (value['actor_id'], value['workspace_id'], producer['id'], value['map_job']['id']):
        _id(identity)
    _hashes(producer[key] for key in ('state_hash', 'start_command_hash', 'start_receipt_hash'))
    _hashes(value[key] for key in ('source_hash', 'catalog_hash', 'source_snapshot_hash', 'map_preparation_hash',
        'map_plan_hash', 'map_generation_hash', 'map_inputs_hash', 'input_order_hash', 'input_items_hash',
        'input_claims_hash', 'allowed_span_refs_hash', 'snapshot_hash'))
    _need(all(value[key] is False for key in ('original_reduction_adopted', 'semantic_quality_verified',
        'consumer_authorized', 'can_apply', 'can_publish')))
    catalog, context, refs = (source_context[key] for key in ('catalog', 'context', 'allowed_span_refs'))
    _need(_same(value['catalog_descriptor'], catalog['descriptor'])
        and value['source_hash'] == catalog['descriptor']['source_hash']
        and value['catalog_hash'] == catalog['descriptor']['catalog_hash']
        and _same(value['context'], context) and value['context_hash'] == digest(context)
        and _same(value['allowed_span_refs'], refs) and value['allowed_span_refs_hash'] == digest(refs)
        and type(value['user_instruction']) is str and value['user_instruction_hash'] == digest(value['user_instruction']))
    claims = input_validator._inputs(catalog, refs, context, value['input_order'], value['input_items'])
    ordered = [copy.deepcopy(claims[key]) for key in value['input_order']]
    spans = [[item['span_ref'], item['text']] for item in resolve_evidence(catalog, refs, refs)]
    _need(type(value['claim_count']) is int and 1 <= value['claim_count'] == len(ordered) <= 1920
        and value['input_order_hash'] == digest(value['input_order']) and value['input_items_hash'] == digest(value['input_items'])
        and value['input_claims_hash'] == digest(ordered) and _same(value['spans'], spans)
        and type(value['map_proofs']) is list and 1 <= len(value['map_proofs']) <= 24
        and value['map_inputs_hash'] == digest(value['map_proofs'])
        and type(value['map_calls']) is list and len(value['map_calls']) == len(value['map_proofs']))
    chunks = []
    calls = set()
    for index, (proof, call) in enumerate(zip(value['map_proofs'], value['map_calls']), 1):
        key = 'map-' + str(index)
        _need(proof['chunk_id'] == call['chunk_id'] == key)
        _binding(call['binding'], value, key)
        ledger = call['cloud_call']
        _need(ledger['status'] == 'succeeded' and ledger['id'] == call['binding']['reserved_call_id']
            and ledger['workspace_id'] == value['workspace_id'] and ledger['id'] not in calls
            and ledger['capability_id'] == value['producer_model']['capability_id'])
        chunks.append(key)
        calls.add(ledger['id'])
    _need(all(identity.split('/claims/')[0] in chunks for identity in value['input_order'])
        and value['map_job']['status'] == 'succeeded'
        and value['map_job']['kind'] == pipeline.legacy.KIND
        and value['map_job']['idempotency_key'] == 'report-evidence:' + producer['request_id'])
    _check_reduction(value['healthy_reduction'], value)
    _need(value['healthy_reduction']['cloud_call']['id'] not in calls)
    return value, ordered, spans
