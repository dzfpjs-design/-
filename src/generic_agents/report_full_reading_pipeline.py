"""Private full-source extraction and separately authorized reconciliation.

No v1/v2 record is upgraded. Every paid stage has its own immutable messages,
reservation and raw checkpoint. Reading a preview never calls a provider.
"""
import copy
import base64
import json
import time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field, model_validator, field_validator
from sqlalchemy import select, text

from . import report_reading_drafts as legacy
from .contracts import digest
from .models import SetupReportDraft, SetupMaterial, SetupAnalysis, Job, CloudCall, uid, now
from .services import scoped, require, audit
from .security import Principal
from .gateway import prepare_cloud_json, cloud_json, cloud_json_options, CLOUD_JSON_SYSTEM_PREFIX
from .cloud_input_profiles import input_policy, request_options, measure_input
from .leases import checkpoint, LeaseLost, ExecutionUnauthorized
from .report_full_reading_source import (FullSourceSelection, MaterialRef, freeze_full_source,
    check_full_source, project_full_source, full_source_options)
from .report_full_reading_semantics import (FullReportResult, FullMergeResult, validate_full_result,
    validate_full_merge, FULL_READING_TASK_V1, FULL_MERGE_TASK_V1, FULL_READING_TASK_V2, FULL_MERGE_TASK_V2)

router = APIRouter()
STATE = 'report-full-reading-draft-state-v1'
PREVIEW = 'report-full-reading-preview-v1'
GENERATION = 'report-full-reading-generation-v1'
MESSAGES_V1 = 'report-full-reading-messages-v1'
MESSAGES_V2 = 'report-full-reading-messages-v2'
DB = legacy.DB
P = legacy.P
MAX_STATE_BYTES = legacy.MAX_STATE_BYTES
MAX_PREVIEW_BYTES = 5 * 1024 * 1024
FR = r'^fr_[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
FM = r'^fm_[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
PREVIEW_KEYS = {'schema', 'actor_id', 'workspace_id', 'body', 'configuration_hash', 'model_hash', 'provider', 'model',
    'source_snapshot', 'source_hash', 'projection_hash', 'generation_contract', 'generation_contract_hash',
    'batches', 'input_summary', 'can_start', 'start_blocker', 'can_apply', 'can_publish', 'preview_hash'}
MERGE_PREVIEW_KEYS = {'schema', 'actor_id', 'workspace_id', 'draft_id', 'body', 'extract_results_hash', 'payload',
    'messages_hash', 'input_summary', 'generation_contract_hash', 'can_start', 'start_blocker', 'can_apply', 'can_publish', 'preview_hash'}


class PreviewCommand(legacy.SafeContract):
    request_id: str = Field(pattern=FR, strict=True)
    revision: int = Field(ge=1, strict=True)
    model_id: UUID
    material_refs: list[MaterialRef] = Field(min_length=1, max_length=1)
    selection: FullSourceSelection
    note: str = Field(default='', max_length=4000, strict=True)

    @model_validator(mode='after')
    def original_matches(self):
        if self.material_refs[0] != self.selection.original_ref:
            raise ValueError('全文选择与Word原件不一致')
        return self


class StartCommand(PreviewCommand):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须明确同意当前发送预览')
        return value


class MergePreviewCommand(legacy.SafeContract):
    revision: int = Field(ge=1, strict=True)
    request_id: str = Field(pattern=FM, strict=True)


class RecoveryCommand(legacy.SafeContract):
    revision: int = Field(ge=1, strict=True)
    request_id: UUID


class MergeCommand(MergePreviewCommand):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须明确同意当前合并预览')
        return value


def fail(code, message, status=409, **extra):
    legacy.fail(code, message, status, **extra)


def is_full(row):
    return bool(row and isinstance(row.data, dict) and row.data.get('schema') == STATE)


def seal(value, key):
    return {**value, key: digest(value)}


def checked_hash(value, key):
    return isinstance(value, dict) and value.get(key) == digest({k: v for k, v in value.items() if k != key})


def read_principal(request: Request, response: Response, db: DB):
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    return legacy.private_principal(request, response, db)


ReadP = Annotated[Principal, Depends(read_principal)]


def _context(configuration):
    profile = configuration.get('instanceProfile', {})
    return legacy.old().scrub_tree({'profile': {key: str(profile.get(key, '')) for key in ('name', 'domain', 'goal')},
        'businesses': legacy.old().names(configuration),
        'object_names': [item.get('name', '') for item in configuration.get('instanceObjects', [])]})


def _task_pair(messages_version):
    if messages_version == MESSAGES_V1:
        return FULL_READING_TASK_V1, FULL_MERGE_TASK_V1
    if messages_version == MESSAGES_V2:
        return FULL_READING_TASK_V2, FULL_MERGE_TASK_V2
    raise ValueError('Unknown full-reading messages version')


def generation(cap, *, messages_version=MESSAGES_V2):
    reading_task, merge_task = _task_pair(messages_version)
    options = request_options(cap.data['execution'], cloud_json_options(cap.data['execution']), full_report=True)
    return {'version': GENERATION, 'messages_version': messages_version,
        'trusted_task': reading_task, 'merge_task': merge_task,
        'output_schema': legacy.canonical_schema(FullReportResult.model_json_schema()),
        'merge_schema': legacy.canonical_schema(FullMergeResult.model_json_schema()),
        'gateway_prefix_hash': digest(CLOUD_JSON_SYSTEM_PREFIX), 'dispatch_options': options,
        'input_policy': {'single_batch': True, **input_policy(cap.data['execution'], full_report=True)}}


def check_generation(value):
    keys = {'version', 'messages_version', 'trusted_task', 'merge_task', 'output_schema', 'merge_schema',
            'gateway_prefix_hash', 'dispatch_options', 'input_policy'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('Generation differs')
    reading_task, merge_task = _task_pair(value['messages_version'])
    if (value['version'] != GENERATION
            or value['trusted_task'] != reading_task or value['merge_task'] != merge_task
            or value['output_schema'] != legacy.canonical_schema(FullReportResult.model_json_schema())
            or value['merge_schema'] != legacy.canonical_schema(FullMergeResult.model_json_schema())
            or value['gateway_prefix_hash'] != digest(CLOUD_JSON_SYSTEM_PREFIX)):
        raise ValueError('Generation differs')
    return value


def messages(spec, payload, *, merge=False):
    trusted = {'schema': 'report-full-reading-task-v1', 'task': spec['merge_task'] if merge else spec['trusted_task'],
        'purpose': 'candidate_only', 'source_policy': 'untrusted_evidence', 'full_report_confirmed': False}
    return [{'role': 'system', 'content': json.dumps(trusted, ensure_ascii=False, sort_keys=True)},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, sort_keys=True)}]


def prepared_stage(cap, spec, payload, *, merge=False):
    requested = messages(spec, payload, merge=merge)
    schema = legacy.canonical_schema(spec['merge_schema'] if merge else spec['output_schema'])
    prepared, _, images = prepare_cloud_json(cap, requested, schema)
    if images or len(prepared) != len(requested) + 1 or any(
            actual['role'] != expected['role'] or json.loads(actual['content']) != json.loads(expected['content'])
            for actual, expected in zip(prepared[1:], requested)):
        fail('full_gateway_projection_changed', '网关改变了已选择全文或添加了图像；未发送')
    summary = measure_input(prepared, spec['input_policy'], spec['dispatch_options']['max_tokens'])
    return prepared, summary


def budget_available(db, p, tokens, calls):
    from .config import settings
    row = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == 'cloud-budget:' + str(now().date())))
    used = row.data if row else {'tokens': 0, 'calls': 0}
    return used['tokens'] + tokens <= settings().cloud_daily_token_limit and used['calls'] + calls <= settings().cloud_daily_call_limit


def _payload(projection, context, note, blocks):
    return {'schema': 'report-full-reading-extract-evidence-v1', 'purpose': 'candidate_only',
        'full_report_confirmed': False, 'context': context, 'user_instruction': note,
        'source_metadata': {k: copy.deepcopy(v) for k, v in projection.items() if k != 'blocks'},
        'blocks': copy.deepcopy(blocks)}


def extraction_batches(cap, spec, projection, context, note):
    blocks = projection['blocks']
    def make(group, ordinal):
        payload = _payload(projection, context, note, group)
        prepared, summary = prepared_stage(cap, spec, payload)
        return seal({'batch_id': 'extract-' + str(ordinal), 'source_refs': [row[0] for row in group],
            'payload': payload, 'messages_hash': digest(prepared), 'input_summary': summary}, 'batch_hash')
    try:
        return [make(blocks, 1)]
    except ValueError:
        pass
    # Prefer one complete source view per extraction. Both views remain evidence
    # of the same report; the reconciliation task must not count them twice.
    word = [row for row in blocks if row[0].startswith('w:')]
    ocr = [row for row in blocks if row[0].startswith('o:')]
    try:
        return [make(word, 1), make(ocr, 2)]
    except ValueError:
        pass
    low, high, best = 1, len(blocks) - 1, 0
    while low <= high:
        middle = (low + high) // 2
        try:
            make(blocks[:middle], 1); best = middle; low = middle + 1
        except ValueError:
            high = middle - 1
    if not best:
        fail('full_input_capacity', '全文中的单块超过模型消息容量，未发送或截断', 413)
    try:
        return [make(blocks[:best], 1), make(blocks[best:], 2)]
    except ValueError:
        fail('full_input_capacity', '完整来源无法在明确的两次抽取范围内发送；全部资料保留，未截断或增加隐藏调用', 413)


def build_preview(p, body, configuration, cap, source, *, can_start=True, messages_version=MESSAGES_V2):
    source = check_full_source(copy.deepcopy(source))
    projection, _ = project_full_source(source)
    # Use the existing deterministic privacy filter, then freeze its exact view.
    projection = legacy.old().scrub_tree(projection)
    spec = generation(cap, messages_version=messages_version)
    context = _context(configuration)
    batches = extraction_batches(cap, spec, projection, context, legacy.old().scrub(body.note))
    refs = [ref for batch in batches for ref in batch['source_refs']]
    if refs != [row[0] for row in projection['blocks']] or len(set(refs)) != len(refs):
        fail('full_coverage_invalid', '全文分批存在遗漏、重排或重复；未发送')
    value = {'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': body.model_dump(mode='json'), 'configuration_hash': digest(configuration),
        'model_hash': cap.content_hash, 'provider': cap.data['execution']['provider'],
        'model': cap.data['execution']['model'], 'source_snapshot': source, 'source_hash': digest(source),
        'projection_hash': digest(projection), 'generation_contract': spec,
        'generation_contract_hash': digest(spec), 'batches': batches,
        'input_summary': {'batch_count': len(batches), 'extract_token_bound': sum(b['input_summary']['token_bound'] for b in batches),
            'total_source_refs': len(refs), 'prepared_messages_bytes': sum(b['input_summary']['prepared_messages_bytes'] for b in batches),
            'full_report_confirmed': False, 'maximum_total_calls': len(batches) + 1},
        'can_start': can_start, 'start_blocker': None if can_start else '工作区今日模型预算不足；本次仅预览，没有预留或调用。',
        'can_apply': False, 'can_publish': False}
    value = seal(value, 'preview_hash')
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_PREVIEW_BYTES:
        fail('full_snapshot_capacity', '完整来源与预览超过保存容量，原件保留；未截断或发送', 413)
    return value


def make_preview(db, p, body):
    config = legacy.workspace_draft(db, p)
    if config.version != body.revision:
        fail('configuration_changed', '工作区已变化，请刷新全文读取预览')
    cap = legacy.old().model(db, p, str(body.model_id))
    value = build_preview(p, body, config.data, cap, freeze_full_source(db, p, body.selection))
    available = budget_available(db, p, value['input_summary']['extract_token_bound'], len(value['batches']))
    if not available:
        value = seal({**{k: v for k, v in value.items() if k != 'preview_hash'}, 'can_start': False,
            'start_blocker': '工作区今日模型预算不足；本次仅预览，没有预留或调用。'}, 'preview_hash')
    return value


def check_preview(value, p):
    try:
        if set(value) != PREVIEW_KEYS or value['schema'] != PREVIEW or value['actor_id'] != p.user_id or value['workspace_id'] != p.workspace_id:
            raise ValueError('Scope')
        if not checked_hash(value, 'preview_hash') or digest(value['source_snapshot']) != value['source_hash']:
            raise ValueError('Hash')
        body = PreviewCommand.model_validate(value['body'])
        source = check_full_source(value['source_snapshot'])
        if source['selection'] != body.selection.model_dump(mode='json'):
            raise ValueError('Selection')
        projection, _ = project_full_source(source)
        projection = legacy.old().scrub_tree(projection)
        if digest(projection) != value['projection_hash']:
            raise ValueError('Projection')
        spec = check_generation(value['generation_contract'])
        if digest(spec) != value['generation_contract_hash'] or value['can_apply'] is not False or value['can_publish'] is not False:
            raise ValueError('Generation')
        fake = type('FrozenCapability', (), {'data': {'execution': {}, 'data_policy': {'redact_fields': []}}})()
        actual = []
        for index, batch in enumerate(value['batches'], 1):
            if set(batch) != {'batch_id', 'source_refs', 'payload', 'messages_hash', 'input_summary', 'batch_hash'} or batch['batch_id'] != 'extract-' + str(index) or not checked_hash(batch, 'batch_hash'):
                raise ValueError('Batch')
            prepared, summary = prepared_stage(fake, spec, batch['payload'])
            if digest(prepared) != batch['messages_hash'] or summary != batch['input_summary']:
                raise ValueError('Messages')
            if batch['source_refs'] != [row[0] for row in batch['payload']['blocks']]:
                raise ValueError('Refs')
            if batch['payload'] != _payload(projection, value['batches'][0]['payload']['context'],
                    legacy.old().scrub(body.note), batch['payload']['blocks']):
                raise ValueError('Payload')
            actual.extend(batch['payload']['blocks'])
        if not 1 <= len(value['batches']) <= 2 or actual != projection['blocks']:
            raise ValueError('Coverage')
        expected_summary = {'batch_count': len(value['batches']),
            'extract_token_bound': sum(b['input_summary']['token_bound'] for b in value['batches']),
            'total_source_refs': len(actual),
            'prepared_messages_bytes': sum(b['input_summary']['prepared_messages_bytes'] for b in value['batches']),
            'full_report_confirmed': False, 'maximum_total_calls': len(value['batches']) + 1}
        if value['input_summary'] != expected_summary or type(value['can_start']) is not bool:
            raise ValueError('Summary')
        return value
    except (KeyError, TypeError, ValueError, HTTPException):
        fail('full_preview_integrity', '完整读取的冻结范围或消息无法核对；原稿保留，不会重发')


def command_hash(p, body, *, kind='report_full_reading', draft_id=None):
    value = {'kind': kind, 'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'body': body.model_dump(mode='json')}
    if draft_id is not None:
        value['draft_id'] = draft_id
    return digest(value)


def start_receipt(p, identity, body, preview):
    return seal({'schema': 'report-full-reading-start-receipt-v1', 'kind': 'report_full_reading',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'request_id': body.request_id,
        'draft_id': identity, 'preview_hash': preview['preview_hash'], 'source_hash': preview['source_hash'],
        'command_hash': command_hash(p, body)}, 'receipt_hash')


def _index(preview):
    _, sources = project_full_source(preview['source_snapshot'])
    return {key: {**value, 'text': legacy.old().scrub(value['text'])} for key, value in sources.items()}


def _stage_sources(preview, key):
    sources = _index(preview)
    if key == 'merge':
        return sources
    sent = next(batch['source_refs'] for batch in preview['batches'] if batch['batch_id'] == key)
    return {ref: sources[ref] for ref in sent}


def _input_items(row):
    return {stage['batch_id'] + '/' + kind + '/' + str(index): item
        for stage in row.data['extractions'] for kind in ('businesses', 'objects', 'relationships', 'questions')
        for index, item in enumerate(stage['result'][kind])}


def check_row(row, p):
    if not row or row.workspace_id != p.workspace_id or row.actor_id != p.user_id:
        fail('not_found', '未找到本人全文读取草案', 404)
    try:
        if not is_full(row):
            raise ValueError('State')
        if set(row.data) != {'schema', 'phase', 'preview', 'start_command_hash', 'start_receipt', 'extractions', 'merge',
                'model_result', 'model_result_hash', 'review_value', 'review_history', 'recovery_history', 'error', 'error_code', 'job_id'}:
            raise ValueError('State keys')
        preview = check_preview(row.data['preview'], p)
        body = StartCommand(**preview['body'], preview_hash=preview['preview_hash'], consent=True)
        if (row.request_id != body.request_id or row.data['start_command_hash'] != command_hash(p, body)
                or row.data['start_receipt'] != start_receipt(p, row.id, body, preview)):
            raise ValueError('Start')
        stages = row.data['extractions']
        if [s['batch_id'] for s in stages] != [b['batch_id'] for b in preview['batches']]:
            raise ValueError('Stages')
        for stage in [*stages, row.data['merge']]:
            if set(stage) - {'batch_id', 'status', 'raw_response', 'reserved_call_id', 'inflight', 'result', 'result_hash',
                    'error', 'error_code', 'preview', 'start_receipt', 'reconciliation'}:
                raise ValueError('Stage keys')
            raw = stage.get('raw_response')
            if raw and (not checked_hash(raw, 'hash') or raw['receipt']['call_id'] != stage['reserved_call_id']):
                raise ValueError('Raw')
            if stage.get('result') is not None and digest(stage['result']) != stage['result_hash']:
                raise ValueError('Result')
            if stage.get('result') is not None and stage['status'] != 'succeeded':
                raise ValueError('Result status')
            if stage['status'] == 'succeeded':
                if not raw or 'value' not in raw:
                    raise ValueError('Result original')
                context = preview['batches'][0]['payload']['context']
                if 'batch_id' in stage:
                    expected = validate_full_result(raw['value'], _stage_sources(preview, stage['batch_id']), context)
                else:
                    merged = validate_full_merge(raw['value'], _index(preview), context, _input_items(row))
                    expected = merged['result']
                    if stage['reconciliation'] != merged['reconciliation']:
                        raise ValueError('Reconciliation original')
                if stage['result'] != expected:
                    raise ValueError('Model original changed')
        merge = row.data['merge']
        if merge.get('preview'):
            _check_merge_preview(merge['preview'], row, p)
            command = MergeCommand(**merge['preview']['body'], preview_hash=merge['preview']['preview_hash'], consent=True)
            receipt = merge['start_receipt']
            if (receipt['command_hash'] != command_hash(p, command, kind='report_full_merge', draft_id=row.id)
                    or not checked_hash(receipt, 'receipt_hash') or receipt['actor_id'] != p.user_id
                    or receipt['workspace_id'] != p.workspace_id or receipt['draft_id'] != row.id
                    or receipt['request_id'] != command.request_id or receipt['accepted_revision'] != command.revision + 1
                    or receipt['preview_hash'] != command.preview_hash
                    or receipt['extract_results_hash'] != merge['preview']['extract_results_hash']):
                raise ValueError('Merge command')
        if row.data.get('model_result') is not None and digest(row.data['model_result']) != row.data['model_result_hash']:
            raise ValueError('Final result')
        if row.data.get('model_result') is not None and row.data['model_result'] != merge.get('result'):
            raise ValueError('Original final result')
        for entry in row.data['review_history']:
            receipt = entry['receipt']
            if (receipt['actor_id'] != p.user_id or receipt['workspace_id'] != p.workspace_id or receipt['draft_id'] != row.id
                    or receipt['value_hash'] != digest(entry['value']) or not checked_hash(receipt, 'receipt_hash')):
                raise ValueError('Review')
            command = legacy.ReviewCommand(revision=receipt['accepted_revision'] - 1,
                request_id=receipt['request_id'], value=entry['value'])
            if receipt['command_hash'] != command_hash(p, command, kind='report_full_review', draft_id=row.id):
                raise ValueError('Review command')
        if row.data['review_history'] and row.data['review_value'] != row.data['review_history'][-1]['value']:
            raise ValueError('Latest review')
        for receipt in row.data['recovery_history']:
            command = RecoveryCommand(revision=receipt['accepted_revision'] - 1, request_id=receipt['request_id'])
            if (set(receipt) != {'schema', 'kind', 'actor_id', 'workspace_id', 'draft_id', 'request_id',
                    'accepted_revision', 'command_hash', 'receipt_hash'}
                    or receipt['schema'] != 'report-full-reading-recovery-receipt-v1' or receipt['kind'] != 'report_full_recovery'
                    or receipt['actor_id'] != p.user_id or receipt['workspace_id'] != p.workspace_id
                    or receipt['draft_id'] != row.id or not checked_hash(receipt, 'receipt_hash')
                    or receipt['command_hash'] != command_hash(p, command, kind='report_full_recovery', draft_id=row.id)):
                raise ValueError('Recovery receipt')
        return row
    except (KeyError, TypeError, ValueError):
        fail('full_state_integrity', '全文读取的原调用、结果或人工稿无法核对；原记录保留')


def owned(db, p, identity, lock=False):
    query = select(SetupReportDraft).where(SetupReportDraft.id == str(identity),
        SetupReportDraft.workspace_id == p.workspace_id, SetupReportDraft.actor_id == p.user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return check_row(db.scalar(query), p)


def _response_saved(stage):
    raw=stage.get('raw_response') or {}
    receipt=raw.get('receipt') or {}
    # Legacy answers lack this field; retain their historical read semantics.
    return bool(raw) and receipt.get('response_body_status','complete') == 'complete'


def _stage_view(stage):
    return {'status': stage['status'], 'result_hash': stage.get('result_hash'),
        'receipt': copy.deepcopy((stage.get('raw_response') or {}).get('receipt')),
        'raw_response_saved': _response_saved(stage), 'error_code': stage.get('error_code'), 'error': stage.get('error')}


def draft_view(row, p):
    check_row(row, p)
    data = row.data
    return seal({'schema': 'report-full-reading-draft-v1', 'id': row.id, 'actor_id': row.actor_id,
        'workspace_id': row.workspace_id, 'request_id': row.request_id, 'status': row.status, 'revision': row.version,
        'phase': data['phase'], 'preview': copy.deepcopy(data['preview']),
        'extractions': [{'batch_id': s['batch_id'], **_stage_view(s)} for s in data['extractions']],
        'merge': {key: copy.deepcopy(data['merge'].get(key)) for key in ('status', 'preview', 'start_receipt', 'reconciliation')}
            | {'receipt': copy.deepcopy((data['merge'].get('raw_response') or {}).get('receipt')),
               'raw_response_saved': _response_saved(data['merge'])},
        'model_result': copy.deepcopy(data.get('model_result')), 'model_result_hash': data.get('model_result_hash'),
        'review_value': copy.deepcopy(data['review_value']), 'review_history': copy.deepcopy(data['review_history']),
        'recovery_history': copy.deepcopy(data['recovery_history']),
        'start_receipt': copy.deepcopy(data['start_receipt']), 'error': data.get('error'), 'error_code': data.get('error_code'),
        'job_id': data['job_id'], 'can_apply': False, 'can_publish': False}, 'view_hash')


def replay_start(row, p, body):
    check_row(row, p)
    if command_hash(p, body) != row.data['start_command_hash']:
        fail('nonce_conflict', '原全文启动编号已用于另一份请求；不会新增调用')
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(row.data['start_receipt'])}


def _state_size(value):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_STATE_BYTES:
        fail('full_state_capacity', '完整草案保存已达容量上限，原稿保留；未截断或覆盖', 413)


def _set(row, data):
    legacy.safe_strings(data); _state_size(data); row.data = data; row.version += 1


def _merge_payload(row):
    preview = row.data['preview']
    return {'schema': 'report-full-reading-merge-evidence-v1', 'purpose': 'candidate_only', 'full_report_confirmed': False,
        'context': preview['batches'][0]['payload']['context'], 'user_instruction': preview['batches'][0]['payload']['user_instruction'],
        'source_coverage': preview['source_snapshot']['coverage'],
        'extraction_results': [{'batch_id': s['batch_id'], 'result_hash': s['result_hash'], 'result': s['result']} for s in row.data['extractions']],
        'input_items': list(_input_items(row))}


def merge_preview(row, p, body, cap, *, can_start=True):
    if row.version != body.revision or row.status != 'awaiting_merge' or any(s['status'] != 'succeeded' for s in row.data['extractions']):
        fail('full_merge_not_ready', '抽取尚未全部保存，或草案版本已变化；请读回原记录')
    if any(not _complete_response(s.get('raw_response')) for s in row.data['extractions']):
        fail('full_completion_unverified', '历史抽取缺少完整结束证明；原结果保留，不能据此发起新的合并调用')
    preview = row.data['preview']; spec = preview['generation_contract']
    if cap.content_hash != preview['model_hash'] or generation(cap, messages_version=spec['messages_version']) != spec:
        fail('full_model_changed', '原模型配置已变化，已保存抽取保留；未发送合并')
    payload = _merge_payload(row)
    try:
        prepared, summary = prepared_stage(cap, spec, payload, merge=True)
    except ValueError as error:
        fail('full_merge_capacity', str(error) + ' 已保存抽取不变，不会截断或增加隐藏调用', 413)
    return seal({'schema': 'report-full-reading-merge-preview-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'draft_id': row.id, 'body': body.model_dump(mode='json'), 'extract_results_hash': digest([
            {'batch_id': s['batch_id'], 'result_hash': s['result_hash']} for s in row.data['extractions']]),
        'payload': payload, 'messages_hash': digest(prepared), 'input_summary': summary,
        'generation_contract_hash': preview['generation_contract_hash'], 'can_start': can_start,
        'start_blocker': None if can_start else '合并预算不足；抽取已保存，本次只预览，没有调用。',
        'can_apply': False, 'can_publish': False}, 'preview_hash')


def _check_merge_preview(value, row, p):
    if (set(value) != MERGE_PREVIEW_KEYS or not checked_hash(value, 'preview_hash') or value['schema'] != 'report-full-reading-merge-preview-v1'
            or value['actor_id'] != p.user_id or value['workspace_id'] != p.workspace_id or value['draft_id'] != row.id
            or value['generation_contract_hash'] != row.data['preview']['generation_contract_hash']
            or value['payload'] != _merge_payload(row)
            or value['extract_results_hash'] != digest([{'batch_id': s['batch_id'], 'result_hash': s['result_hash']} for s in row.data['extractions']])):
        raise ValueError('Merge preview')
    MergePreviewCommand.model_validate(value['body'])
    if type(value['can_start']) is not bool or value['can_apply'] is not False or value['can_publish'] is not False:
        raise ValueError('Merge authority')
    fake = type('FrozenCapability', (), {'data': {'execution': {}, 'data_policy': {'redact_fields': []}}})()
    prepared, summary = prepared_stage(fake, row.data['preview']['generation_contract'], value['payload'], merge=True)
    if digest(prepared) != value['messages_hash'] or summary != value['input_summary']:
        raise ValueError('Merge messages')


@router.get('/report-drafts/full-source-options/{word_id}')
def source_options(word_id: UUID, p: ReadP, db: DB, version: int = Query(..., ge=1),
                   source_hash: str = Query(..., alias='hash', pattern=r'^[a-f0-9]{64}$')):
    return full_source_options(db, p, {'id': str(word_id), 'version': version, 'hash': source_hash})


@router.post('/report-full-readings/preview')
def preview_route(body: PreviewCommand, p: P, db: DB):
    require(p, 'admin', 'operator')
    if legacy.by_nonce(db, p, body.request_id):
        fail('nonce_exists', '此全文读取编号已有记录，请先从原编号恢复')
    result = make_preview(db, p, body); db.rollback(); return result


@router.post('/report-full-readings', status_code=202)
def start_route(body: StartCommand, p: P, db: DB):
    prior = legacy.by_nonce(db, p, body.request_id)
    if prior is not None:
        return replay_start(prior, p, body)
    require(p, 'admin', 'operator'); legacy.workspace_draft(db, p)
    prior = legacy.by_nonce(db, p, body.request_id)
    if prior is not None:
        return replay_start(prior, p, body)
    legacy.guard_existing_paid(db, p)
    command = PreviewCommand.model_validate({k: v for k, v in body.model_dump(mode='json').items() if k not in {'preview_hash', 'consent'}})
    value = make_preview(db, p, command)
    if value['preview_hash'] != body.preview_hash:
        fail('preview_changed', '完整来源、配置、模型或预算已变化，请重新预览')
    if not value['can_start']:
        fail('full_budget_blocked', value['start_blocker'])
    row = SetupReportDraft(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, request_id=body.request_id, status='queued')
    row.data = {'schema': STATE, 'phase': 'extract', 'preview': value, 'start_command_hash': command_hash(p, body),
        'start_receipt': start_receipt(p, row.id, body, value),
        'extractions': [{'batch_id': b['batch_id'], 'status': 'pending'} for b in value['batches']],
        'merge': {'status': 'not_started'}, 'model_result': None, 'model_result_hash': None,
        'review_value': None, 'review_history': [], 'recovery_history': [], 'error': None, 'error_code': None}
    db.add(row); db.flush()
    job = Job(workspace_id=p.workspace_id, kind=legacy.KIND, idempotency_key='report-full-reading:' + body.request_id,
        data={'actor_id': p.user_id, 'report_draft_id': row.id})
    db.add(job); db.flush(); row.data = {**row.data, 'job_id': job.id}; _state_size(row.data)
    audit(db, p, 'setup.report_full_reading.authorize', row.id, {'request_id': body.request_id, 'preview_hash': body.preview_hash})
    db.commit(); return replay_start(row, p, body)


@router.get('/report-full-readings')
def list_route(p: ReadP, db: DB):
    rows = db.scalars(select(SetupReportDraft).where(SetupReportDraft.workspace_id == p.workspace_id,
        SetupReportDraft.actor_id == p.user_id, SetupReportDraft.data['schema'].as_string() == STATE)
        .order_by(SetupReportDraft.created_at.desc(), SetupReportDraft.id.desc()).limit(101)).all()
    if len(rows) > 100:
        fail('full_list_capacity', '全文草案超过当前列表容量，原稿保留；请按原编号恢复', 413)
    return seal({'schema': 'report-full-reading-list-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'items': [{'id': r.id, 'request_id': r.request_id, 'status': r.status, 'revision': r.version,
            'material_refs': r.data['preview']['body']['material_refs'],
            'name': r.data['preview']['source_snapshot']['original']['name'], 'created_at': r.created_at.isoformat(),
            'can_apply': False, 'can_publish': False} for r in rows]}, 'list_hash')


@router.get('/report-full-readings/by-request/{request_id}')
def by_request_route(request_id: str, p: ReadP, db: DB):
    row = check_row(legacy.by_nonce(db, p, request_id), p)
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(row.data['start_receipt'])}


@router.get('/report-full-readings/{identity}')
def get_route(identity: UUID, p: ReadP, db: DB):
    return draft_view(owned(db, p, identity), p)


@router.get('/report-full-readings/{identity}/stages/{stage}/raw')
def raw_stage_route(identity: UUID, stage: str, response: Response, p: ReadP, db: DB, revision: int = Query(..., ge=1)):
    from .report_full_raw_view import raw_view
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return raw_view(db, p, str(identity), stage, revision)


@router.get('/report-full-readings/{identity}/stages/{stage}/failed-candidate')
def failed_stage_candidate_route(identity: UUID, stage: str, response: Response, p: ReadP, db: DB, revision: int = Query(..., ge=1)):
    from .report_full_failed_candidate_view import failed_candidate_view
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return failed_candidate_view(db, p, str(identity), stage, revision)


@router.get('/report-full-readings/{identity}/status')
def status_route(identity: UUID, p: ReadP, db: DB):
    row = owned(db, p, identity)
    return seal({'schema': 'report-full-reading-status-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'id': row.id, 'revision': row.version, 'status': row.status, 'phase': row.data['phase'],
        'extractions': [{'batch_id': s['batch_id'], **_stage_view(s)} for s in row.data['extractions']],
        'merge_status': row.data['merge']['status'], 'error': row.data.get('error'), 'error_code': row.data.get('error_code'),
        'can_apply': False, 'can_publish': False}, 'status_hash')


@router.post('/report-full-readings/{identity}/merge-preview')
def merge_preview_route(identity: UUID, body: MergePreviewCommand, p: P, db: DB):
    require(p, 'admin', 'operator'); row = owned(db, p, identity)
    cap = legacy.old().model(db, p, row.data['preview']['body']['model_id'])
    value = merge_preview(row, p, body, cap)
    if not budget_available(db, p, value['input_summary']['token_bound'], 1):
        value = seal({**{k: v for k, v in value.items() if k != 'preview_hash'}, 'can_start': False,
            'start_blocker': '合并预算不足；抽取已保存，本次只预览，没有调用。'}, 'preview_hash')
    db.rollback(); return value


@router.post('/report-full-readings/{identity}/merge', status_code=202)
def merge_start_route(identity: UUID, body: MergeCommand, p: P, db: DB):
    row = owned(db, p, identity); prior = row.data['merge'].get('start_receipt')
    if prior:
        if prior['request_id'] != body.request_id or prior['command_hash'] != command_hash(p, body, kind='report_full_merge', draft_id=row.id):
            fail('nonce_conflict', '本稿已有合并请求，须核对原号；不会再调用')
        return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(prior)}
    require(p, 'admin', 'operator')
    # Same lock order as all other setup paid starts: workspace before candidate.
    # This draft itself is awaiting_merge and must not block its own merge.
    legacy.workspace_draft(db, p)
    row = owned(db, p, identity, True)
    prior = row.data['merge'].get('start_receipt')
    if prior:
        if prior['request_id'] != body.request_id or prior['command_hash'] != command_hash(p, body, kind='report_full_merge', draft_id=row.id):
            fail('nonce_conflict', '本稿已有合并请求，须核对原号；不会再调用')
        return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(prior)}
    other = db.scalar(select(SetupReportDraft.id).where(SetupReportDraft.workspace_id == p.workspace_id,
        SetupReportDraft.id != row.id, SetupReportDraft.status.in_(legacy.ACTIVE)).limit(1))
    old_rows = db.scalars(select(SetupAnalysis).where(SetupAnalysis.workspace_id == p.workspace_id,
        SetupAnalysis.status.in_(['queued', 'running', 'failed']))).all()
    if other or any(legacy.legacy_pending(item) for item in old_rows):
        fail('understanding_pending', '工作区还有另一原调用待处理或结果未知；本稿抽取保留，未新增合并调用')
    cap = legacy.old().model(db, p, row.data['preview']['body']['model_id'])
    preview = merge_preview(row, p, MergePreviewCommand(revision=body.revision, request_id=body.request_id), cap)
    if not budget_available(db, p, preview['input_summary']['token_bound'], 1):
        fail('full_budget_blocked', '合并预算不足，已保存抽取保留；未发送')
    if preview['preview_hash'] != body.preview_hash:
        fail('preview_changed', '合并范围或预算已变化，请重新核对')
    receipt = seal({'schema': 'report-full-reading-merge-receipt-v1', 'kind': 'report_full_merge',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'request_id': body.request_id, 'draft_id': row.id,
        'accepted_revision': row.version + 1, 'preview_hash': body.preview_hash,
        'extract_results_hash': preview['extract_results_hash'],
        'command_hash': command_hash(p, body, kind='report_full_merge', draft_id=row.id)}, 'receipt_hash')
    job = Job(workspace_id=p.workspace_id, kind=legacy.KIND, idempotency_key='report-full-merge:' + body.request_id,
        data={'actor_id': p.user_id, 'report_draft_id': row.id})
    db.add(job); db.flush(); row.status = 'queued'
    _set(row, {**row.data, 'phase': 'merge', 'job_id': job.id,
        'merge': {'status': 'queued', 'preview': preview, 'start_receipt': receipt}})
    audit(db, p, 'setup.report_full_reading.merge_authorize', row.id, {'request_id': body.request_id, 'preview_hash': body.preview_hash})
    db.commit(); return {'draft': draft_view(row, p), 'receipt': receipt}


@router.get('/report-full-readings/{identity}/merge-receipts/{request_id}')
def merge_receipt_route(identity: UUID, request_id: str, p: ReadP, db: DB):
    row = owned(db, p, identity); receipt = row.data['merge'].get('start_receipt')
    if not receipt or receipt['request_id'] != request_id:
        fail('receipt_not_found', '暂未找到本人原合并回执', 404)
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(receipt)}


def record_review(row, p, body):
    check_row(row, p)
    signature = command_hash(p, body, kind='report_full_review', draft_id=row.id)
    old = next((e for e in row.data['review_history'] if e['receipt']['request_id'] == str(body.request_id)), None)
    if old:
        if old['receipt']['command_hash'] != signature:
            fail('nonce_conflict', '原人工稿编号已用于另一份保存，原稿保留')
        return old['receipt']
    require(p, 'admin', 'operator')
    if row.status not in {'review', 'needs_clarification'} or body.revision != row.version:
        fail('full_review_conflict', '草案尚未形成，或另一页已有新稿；当前输入与服务器原稿均保留', current=draft_view(row, p))
    preview = row.data['preview']
    try:
        result = validate_full_result(body.value.result, _index(preview), preview['batches'][0]['payload']['context'])
    except ValueError as error:
        fail('full_review_invalid', str(error)[:1000] + ' 原人工稿保留，未保存此修改。', 422)
    value = {'result': result, 'note': body.value.note}
    receipt = seal({'schema': 'report-full-reading-review-receipt-v1', 'kind': 'report_full_review',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'request_id': str(body.request_id), 'draft_id': row.id,
        'accepted_revision': row.version + 1, 'value_hash': digest(value), 'command_hash': signature}, 'receipt_hash')
    entry = {'value': value, 'receipt': receipt}
    _set(row, {**row.data, 'review_value': value, 'review_history': [*row.data['review_history'], entry]})
    return receipt


def review_reply(row, p, receipt):
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(receipt),
        'current_matches': row.version == receipt['accepted_revision'] and digest(row.data['review_value']) == receipt['value_hash']}


@router.put('/report-full-readings/{identity}/review')
def review_route(identity: UUID, body: legacy.ReviewCommand, p: P, db: DB):
    row = owned(db, p, identity, True); before = row.version; receipt = record_review(row, p, body)
    if before != row.version:
        audit(db, p, 'setup.report_full_reading.review_save', row.id, {'request_id': str(body.request_id), 'accepted_revision': row.version})
    db.commit(); return review_reply(row, p, receipt)


@router.get('/report-full-readings/{identity}/review-receipts/{request_id}')
def review_receipt_route(identity: UUID, request_id: UUID, p: ReadP, db: DB):
    row = owned(db, p, identity)
    receipt = next((e['receipt'] for e in row.data['review_history'] if e['receipt']['request_id'] == str(request_id)), None)
    if receipt is None:
        fail('receipt_not_found', '暂未找到本人原人工稿回执', 404)
    return review_reply(row, p, receipt)


@router.post('/report-full-readings/{identity}/recover-saved')
def recover_saved_route(identity: UUID, body: RecoveryCommand, p: P, db: DB):
    row = owned(db, p, identity, True)
    signature = command_hash(p, body, kind='report_full_recovery', draft_id=row.id)
    prior = next((r for r in row.data['recovery_history'] if r['request_id'] == str(body.request_id)), None)
    if prior:
        if prior['command_hash'] != signature:
            fail('nonce_conflict', '原恢复编号已有另一命令；保存的回答与人工稿均保留')
        return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(prior)}
    require(p, 'admin', 'operator')
    if row.status != 'failed' or row.version != body.revision:
        fail('full_recovery_conflict', '仅可恢复当前失败稿的已保存回答；版本或状态已变化，请读回原稿')
    merge = row.data['merge']
    keys = (['merge'] if merge.get('preview') else
            [s['batch_id'] for s in row.data['extractions'] if s['status'] != 'succeeded'])
    if not keys or any(not _stage(row, key).get('raw_response') for key in keys):
        fail('full_saved_response_incomplete', '尚无可完整继续的已保存回答；原记录保留，不会补发缺失批次或重复付费')
    accepted_revision = row.version + 1
    try:
        for key in keys:
            _consume(db, p, row, key)
    except HTTPException as error:
        db.rollback()
        if isinstance(error.detail,dict) and error.detail.get('code')=='full_completion_unverified':
            fail('full_completion_unverified', '原回答被截断或缺少完整结束证明；已保存记录保留，请人工核对，未采用或重发', 422)
        fail('full_saved_response_invalid', '已保存原答仍未通过结构、引用或合并核对；原内容和费用回执保留，没有调用模型', 422)
    except ValueError:
        db.rollback()
        fail('full_saved_response_invalid', '已保存原答仍未通过结构、引用或合并核对；原内容和费用回执保留，没有调用模型', 422)
    if keys != ['merge']:
        row.status = 'awaiting_merge'
        row.data = {**row.data, 'phase': 'awaiting_merge', 'error': None, 'error_code': None}
    receipt = seal({'schema': 'report-full-reading-recovery-receipt-v1', 'kind': 'report_full_recovery',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'draft_id': row.id,
        'request_id': str(body.request_id), 'accepted_revision': accepted_revision, 'command_hash': signature}, 'receipt_hash')
    # _consume does not commit without a worker lease. Publish one CAS version,
    # the recovered result and its nonce receipt atomically in this API transaction.
    row.version = accepted_revision
    row.data = {**row.data, 'recovery_history': [*row.data['recovery_history'], receipt]}
    _state_size(row.data)
    audit(db, p, 'setup.report_full_reading.saved_response_recovered', row.id,
          {'request_id': str(body.request_id), 'new_call': False, 'stages': keys})
    db.commit()
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(receipt)}


@router.get('/report-full-readings/{identity}/recovery-receipts/{request_id}')
def recovery_receipt_route(identity: UUID, request_id: UUID, p: ReadP, db: DB):
    row = owned(db, p, identity)
    receipt = next((r for r in row.data['recovery_history'] if r['request_id'] == str(request_id)), None)
    if not receipt:
        fail('receipt_not_found', '暂未找到本人原回答恢复回执', 404)
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(receipt)}


def _stage(row, key):
    if key == 'merge':
        return row.data['merge']
    return next(s for s in row.data['extractions'] if s['batch_id'] == key)


def _replace_stage(row, key, value, **fields):
    data = copy.deepcopy(row.data)
    if key == 'merge':
        data['merge'] = value
    else:
        data['extractions'] = [value if s['batch_id'] == key else s for s in data['extractions']]
    data.update(fields); _set(row, data)


def _dispatch(row, key):
    return row.data['merge']['preview'] if key == 'merge' else next(b for b in row.data['preview']['batches'] if b['batch_id'] == key)


def _expected(row, key):
    preview = row.data['preview']; stage = _dispatch(row, key)
    return {'workspace_id': row.workspace_id, 'capability_id': preview['body']['model_id'], 'actor_id': row.actor_id,
        'request_hash': stage['messages_hash'], 'dispatch_options_hash': digest(preview['generation_contract']['dispatch_options']),
        'provider': preview['provider'], 'model': preview['model']}


def _fresh_cap(db, p, row, key):
    check_row(row, p); require(p, 'admin', 'operator')
    preview = row.data['preview']; cap = legacy.old().model(db, p, preview['body']['model_id'])
    if (cap.content_hash != preview['model_hash']
            or generation(cap, messages_version=preview['generation_contract']['messages_version']) != preview['generation_contract']):
        fail('full_model_changed', '原模型配置已变化，原回答保留；未发送')
    # A merge sends only the already saved extraction results. It does not
    # silently read newer page text or change the authorized original view.
    if key == 'merge':
        if any(not _complete_response(s.get('raw_response')) for s in row.data['extractions']):
            fail('full_completion_unverified', '历史抽取缺少完整结束证明；原结果保留，未发送新的合并请求')
        for ref in (preview['body']['selection']['original_ref'], preview['body']['selection']['copy_ref']):
            material = scoped(db, SetupMaterial, ref['id'], p)
            if material.archived or material.version != ref['version'] or material.sha256 != ref['hash']:
                fail('full_source_changed', '原资料已变化或归档，抽取仍保留；未发送合并')
        _check_merge_preview(row.data['merge']['preview'], row, p)
    else:
        body = PreviewCommand.model_validate(preview['body']); config = legacy.workspace_draft(db, p)
        if config.version != body.revision:
            fail('configuration_changed', '工作区配置已变化，已保存结果保留；未发送')
        actual = build_preview(p, body, config.data, cap, freeze_full_source(db, p, body.selection),
                               can_start=preview['can_start'], messages_version=preview['generation_contract']['messages_version'])
        if actual != preview:
            fail('full_dispatch_changed', '原件、全部页面版本或模型消息已变化，未发送')
    return cap


def _complete_response(raw):
    if not isinstance(raw,dict) or not isinstance(raw.get('receipt'),dict):
        return False
    receipt = raw['receipt']
    if not (receipt.get('completion_guard') == 'full-report-completion-v1'
            and type(receipt.get('response_choice_count')) is int and receipt['response_choice_count'] == 1
            and receipt.get('finish_reason') == 'stop' and receipt.get('response_body_status') == 'complete'
            and type(raw.get('content')) is str and type(raw.get('provider_wire_base64')) is str):
        return False
    try:
        from .gateway import MAX_FULL_READING_WIRE_BYTES
        if len(raw['provider_wire_base64']) > ((MAX_FULL_READING_WIRE_BYTES+2)//3)*4: return False
        wire=base64.b64decode(raw['provider_wire_base64'],validate=True)
        if len(wire)>MAX_FULL_READING_WIRE_BYTES: return False
        response=json.loads(wire)
        choices=response.get('choices') if type(response) is dict else None
        if (type(choices) is not list or len(choices)!=1 or type(choices[0]) is not dict
                or choices[0].get('finish_reason')!='stop'
                or type(choices[0].get('message')) is not dict
                or choices[0]['message'].get('content')!=raw['content']
                or response.get('usage',{})!=receipt.get('usage',{})):
            return False
        value=json.loads(raw['content'],parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return value==raw.get('value')
    except (ValueError,TypeError,RecursionError,UnicodeError):
        return False


def _settle(db, row, key, raw):
    from .cloud_budget import settle_paid_response
    from .services import validate
    stage = _stage(row, key); spec = row.data['preview']['generation_contract']
    if not checked_hash(raw, 'hash') or raw['receipt']['call_id'] != stage.get('reserved_call_id'):
        fail('full_paid_checkpoint_integrity', '新全文阶段的原回答与调用回执不一致，未重发')
    valid = False
    if 'value' in raw:
        try:
            validate(spec['merge_schema'] if key == 'merge' else spec['output_schema'], raw['value']); valid = _complete_response(raw)
        except HTTPException:
            pass
    settle_paid_response(db, raw['receipt']['call_id'], raw['receipt'], _expected(row, key), 'succeeded' if valid else 'failed')


def _consume(db, p, row, key):
    stage = _stage(row, key); raw = stage.get('raw_response')
    if not raw:
        fail('full_paid_response_missing', '原阶段没有已保存回答，不会自动重复调用')
    _settle(db, row, key, raw)
    if not _complete_response(raw):
        fail('full_completion_unverified', '原付费回答未证明完整结束；已保存记录保留，不能自动采用或重发', 422)
    if 'value' not in raw:
        fail('full_response_invalid', '原付费回答不是有效候选JSON；原内容与回执已保存，不会自动重付', 422)
    preview = row.data['preview']; context = preview['batches'][0]['payload']['context']
    if key == 'merge':
        merged = validate_full_merge(raw['value'], _index(preview), context, _input_items(row))
        result = merged['result']
        stage = {**stage, 'status': 'succeeded', 'result': result, 'result_hash': digest(result),
                 'reconciliation': merged['reconciliation'], 'inflight': False}
        row.status = 'review' if result['businesses'] else 'needs_clarification'
        _replace_stage(row, key, stage, phase='review', model_result=result, model_result_hash=digest(result),
                       error=None if result['businesses'] else '已保存结果尚未形成业务，请核对待明确事项。', error_code=None)
    else:
        result = validate_full_result(raw['value'], _stage_sources(preview, key), context)
        _replace_stage(row, key, {**stage, 'status': 'succeeded', 'result': result,
                                  'result_hash': digest(result), 'inflight': False})
    audit(db, p, 'setup.report_full_reading.stage_saved', row.id, {'stage': key, 'result_hash': digest(result)})
    checkpoint(db)


def _run_stage(db, p, row, key):
    stage = _stage(row, key)
    if stage['status'] == 'succeeded':
        return
    if stage.get('raw_response'):
        _consume(db, p, row, key); return
    if stage.get('inflight') or stage.get('reserved_call_id'):
        fail('full_response_unknown', '上次阶段可能已发送但尚无保存回答；原编号保留，禁止自动重付')
    cap = _fresh_cap(db, p, row, key)
    preview = row.data['preview']; spec = preview['generation_contract']; authorized = _dispatch(row, key)
    prepared, summary = prepared_stage(cap, spec, authorized['payload'], merge=key == 'merge')
    if digest(prepared) != authorized['messages_hash'] or summary != authorized['input_summary']:
        fail('full_dispatch_changed', '阶段实际消息或预留与授权不一致；未发送')
    row.status = 'running'
    _replace_stage(row, key, {**stage, 'status': 'running', 'inflight': True})
    checkpoint(db); started = time.monotonic(); identity = row.id

    def reserved(call_id):
        db.expire_all(); legacy.workspace_draft(db, p)
        current = owned(db, p, identity, True); _fresh_cap(db, p, current, key)
        call = scoped(db, CloudCall, call_id, p); expected = _expected(current, key)
        if (call.status != 'reserved' or call.capability_id != expected['capability_id']
                or call.reserved_tokens != authorized['input_summary']['token_bound']
                or any(call.data.get(k) != expected[k] for k in ('actor_id', 'request_hash', 'dispatch_options_hash'))
                or call.data.get('input_policy_hash') != digest(spec['input_policy'])):
            fail('full_reservation_changed', '阶段真实预算预留与冻结授权不一致，未发送')
        _replace_stage(current, key, {**_stage(current, key), 'reserved_call_id': call_id}); checkpoint(db)

    def received(content, receipt):
        actor = db.info.pop('worker_actor', None)
        try:
            current = owned(db, p, identity, True)
            if _stage(current, key).get('raw_response'):
                fail('full_paid_response_conflict', '此阶段原回答已保存，不能替换')
            saved_receipt = copy.deepcopy(receipt)
            wire = saved_receipt.pop('_full_report_wire_base64', None)
            raw = legacy.paid_raw(content if type(content) is str else None,
                                  {**saved_receipt, 'duration_ms': round((time.monotonic() - started) * 1000)})
            if wire is not None:
                if type(wire) is not str: fail('full_paid_response_invalid', '原响应无法安全保存，未重发')
                raw = {**raw, 'provider_wire_base64':wire}
                raw['hash'] = digest({k:v for k,v in raw.items() if k!='hash'})
            _replace_stage(current, key, {**_stage(current, key), 'raw_response': raw})
            _settle(db, current, key, raw); checkpoint(db)
        except BaseException:
            db.rollback(); raise
        finally:
            if actor is not None:
                db.info['worker_actor'] = actor

    result, _ = cloud_json(cap, messages(spec, authorized['payload'], merge=key == 'merge'),
        legacy.canonical_schema(spec['merge_schema'] if key == 'merge' else spec['output_schema']), p, full_report=True,
        on_reserved=reserved, on_response=received, full_report_completion_guard=True)
    row = owned(db, p, identity, True)
    if (_stage(row, key).get('raw_response') or {}).get('value') != result:
        fail('full_paid_response_missing', '阶段原回答尚未持久保存，可能已计费；不会自动重付')
    _consume(db, p, row, key)


def record_job_failure(db, p, job, reason):
    row = owned(db, p, job.data['report_draft_id'], True)
    if row.status not in {'queued', 'running', 'response_unknown'}:
        return
    if (row.data['phase'] == 'extract'
            and all(s['status'] == 'succeeded' for s in row.data['extractions'])):
        # The last paid result was committed before the worker lost its lease.
        # Preserve its original/raw pair and finish only the local phase change.
        row.status = 'awaiting_merge'
        _set(row, {**row.data, 'phase': 'awaiting_merge', 'error': None, 'error_code': None})
        audit(db, p, 'setup.report_full_reading.extract_recovered', row.id,
              {'all_results_saved': True, 'new_call': False})
        return
    key = 'merge' if row.data['phase'] == 'merge' else next(
        (s['batch_id'] for s in row.data['extractions'] if s['status'] != 'succeeded'), row.data['extractions'][-1]['batch_id'])
    stage = _stage(row, key); unknown = bool(stage.get('reserved_call_id')) and not stage.get('raw_response')
    row.status = 'response_unknown' if unknown else 'failed'
    body_status = ((stage.get('raw_response') or {}).get('receipt') or {}).get('response_body_status')
    body_unavailable = body_status in ('oversize','unsupported_encoding')
    _replace_stage(row, key, {**stage, 'status': row.status}, phase=row.status,
        error_code='full_response_unknown' if unknown else 'full_response_body_unavailable' if body_unavailable else 'full_worker_stopped',
        error='原阶段可能已发送，原编号保留，禁止自动重付。' if unknown else
              '云响应超出安全接收上限或采用不支持的编码；完整原答未保存，真实用量无法核对。已保留原调用和预算，不会自动重发。' if body_unavailable else
              '后台任务已停止；全部已保存抽取、原回答与人工稿均保留。')
    audit(db, p, 'setup.report_full_reading.stopped', row.id, {'stage': key, 'response_unknown': unknown})


def run(db, p, identity):
    legacy.workspace_draft(db, p)
    row = owned(db, p, identity, True)
    if row.status not in {'queued', 'running'}:
        return row.status
    key = 'merge'
    try:
        if row.data['phase'] == 'extract':
            for batch in row.data['preview']['batches']:
                key = batch['batch_id']; legacy.workspace_draft(db, p)
                row = owned(db, p, identity, True)
                _run_stage(db, p, row, key)
            row = owned(db, p, identity, True); row.status = 'awaiting_merge'
            _set(row, {**row.data, 'phase': 'awaiting_merge', 'error': None, 'error_code': None}); checkpoint(db)
        elif row.data['phase'] == 'merge':
            _run_stage(db, p, row, key)
        else:
            fail('full_phase_invalid', '原阶段无法继续，已保存回答保留')
    except (LeaseLost, ExecutionUnauthorized):
        raise
    except Exception as error:
        db.rollback(); row = owned(db, p, identity, True); stage = _stage(row, key)
        unknown = bool(stage.get('reserved_call_id')) and not stage.get('raw_response')
        row.status = 'response_unknown' if unknown else 'failed'
        detail = legacy.local_failure_detail(error)
        code, reason = detail if detail else ('full_candidate_invalid', '阶段结构、引用或合并去向未通过核对；原回答与费用回执保留，不会自动重付。')
        body_status = ((stage.get('raw_response') or {}).get('receipt') or {}).get('response_body_status')
        if body_status in ('oversize','unsupported_encoding'):
            code, reason = ('full_response_body_unavailable', '云响应超出安全接收上限或采用不支持的编码；完整原答未保存，真实用量无法核对。已保留原调用和预算，不会自动重发。')
        if unknown:
            code, reason = 'full_response_unknown', '原阶段可能已发送，尚无已保存回答；原编号保留，禁止自动重付。'
        _replace_stage(row, key, {**stage, 'status': row.status, 'error_code': code, 'error': reason[:1000]},
            phase=row.status, error_code=code, error=reason[:1000])
        audit(db, p, 'setup.report_full_reading.failed', row.id, {'stage': key, 'response_unknown': unknown,
            'raw_response_saved': bool(stage.get('raw_response'))}); checkpoint(db)
    return owned(db, p, identity).status
