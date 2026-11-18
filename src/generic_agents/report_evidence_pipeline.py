"""Versioned private full-report Map/reduce workflow with immutable paid journal.

Only exact original stages may execute. Saved results are reconstructed from
their same-scope journal, never normalized or copied into the mutable DB state.
No V1/V2 record, formal configuration or execution grant is migrated here.
"""
import copy
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field, field_validator
from sqlalchemy import select

from . import report_reading_drafts as legacy
from . import report_full_reading_pipeline as full
from . import report_evidence_journal as journal
from . import report_evidence_journal_v2 as journal_v2
from .contracts import digest
from .gateway import cloud_json, prepare_cloud_json
from .leases import checkpoint, LeaseLost, ExecutionUnauthorized
from .models import SetupReportDraft, SetupAnalysis, SetupMaterial, CloudCall, Job, uid, now
from .services import scoped, require, audit
from .report_full_reading_source import freeze_full_source, check_full_source, project_full_source
from .report_evidence_plan import build_evidence_source, validate_map_result, EvidenceCapacityExceeded
from .report_evidence_preparation import (GENERATION_V3 as CURRENT_MAP_GENERATION,
    GENERATION_V4, GENERATION_V5, GENERATION_V6, GENERATION_V7, GENERATION_V168, GENERATION_V172, build_map_preparation, check_map_preparation, rebuild_map_request)
from .report_evidence_reduction import build_reduction_preparation, check_reduction_preparation, rebuild_reduction_request
from .report_evidence_semantics import validate_evidence_result, validate_evidence_merge
from . import report_evidence_pipeline_v3 as v3
from . import report_evidence_pipeline_v4 as v4
from . import report_evidence_pipeline_v5 as v5
from . import report_evidence_reduction_v3 as reduction_v3
from .cloud_evidence_profiles import declared_map_transport, STRUCTURED_REPORT_TRANSPORT, REF_ARM_REPORT_TRANSPORT, NATIVE_REPORT_TRANSPORT, CLAIM_NATIVE_TRANSPORT, CLAIM_ENUM_NATIVE_TRANSPORT
from .report_evidence_manual_v5 import ManualReviewValueV5


router = APIRouter()
STATE = 'report-evidence-draft-state-v1'
PREVIEW = 'report-evidence-reading-preview-v1'
PRIVACY_VERSION = 'evidence-privacy-v1'
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_PREVIEW_BYTES = 5 * 1024 * 1024
DB, P, ReadP = legacy.DB, legacy.P, full.ReadP
FE = r'^(?:fe_|fl_)[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
FER = r'^fer_[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
FES = r'^fes_[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
PREVIEW_KEYS = {'schema', 'actor_id', 'workspace_id', 'body', 'configuration_hash', 'published_model_hash',
    'preparation_cap', 'provider', 'model', 'source_snapshot', 'source_hash', 'projection_hash', 'map_preparation',
    'input_summary', 'can_start', 'start_blocker', 'can_apply', 'can_publish', 'preview_hash'}
STAGE_KEYS = {'status', 'reserved_call_id', 'journal', 'result_hash', 'result_count', 'inflight', 'error', 'error_code'}
STATE_KEYS = {'schema', 'phase', 'preview', 'start_command_hash', 'start_receipt', 'maps', 'reduction',
    'model_result_hash', 'review_value', 'review_history', 'recovery_history', 'resume_history', 'error', 'error_code', 'job_id'}
STATES = {'queued', 'running', 'awaiting_merge', 'response_unknown', 'failed', 'review', 'needs_clarification'}
PHASES = {'map', 'awaiting_reduce', 'reduce', 'review', 'failed', 'response_unknown', 'needs_clarification'}


class PreviewCommand(full.PreviewCommand):
    request_id: str = Field(pattern=FE, strict=True)


class StartCommand(PreviewCommand):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须明确同意当前发送范围')
        return value


class ReducePreviewCommand(legacy.SafeContract):
    revision: int = Field(ge=1, strict=True)
    request_id: str = Field(pattern=FER, strict=True)


class ReduceCommand(ReducePreviewCommand):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    consent: Literal[True]
    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须明确同意当前汇总范围')
        return value


class ResumePreviewCommand(legacy.SafeContract):
    revision: int = Field(ge=1, strict=True)
    request_id: str = Field(pattern=FES, strict=True)


class ResumeCommand(ResumePreviewCommand):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    consent: Literal[True]
    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须明确同意剩余资料发送范围')
        return value


class ReviewValue(legacy.SafeContract):
    result: dict
    note: str = Field(default='', max_length=12000, strict=True)


class ReviewCommand(legacy.SafeContract):
    revision: int = Field(ge=1, strict=True)
    request_id: UUID
    value: ReviewValue | ManualReviewValueV5


class RecoveryCommand(legacy.SafeContract):
    revision: int = Field(ge=1, strict=True)
    request_id: UUID



def _family(preview):
    # Exact version dispatch: old V5/V6/V7 continue to their unchanged adapter.
    from . import report_evidence_enum_pipeline_v172 as enum_family
    if enum_family.applies(preview): return enum_family
    from . import report_evidence_pipeline_v168 as selected
    return selected if selected.applies(preview) else v5


def _reducer(preview):
    from . import report_evidence_enum_pipeline_v172 as enum_family
    if enum_family.applies(preview):
        from . import report_evidence_enum_reduction_v172 as selected
        return selected
    if _family(preview) is not v5:
        from . import report_evidence_reduction_v168 as selected
        return selected
    return reduction_v3


def fail(code, message, status=409, **extra):
    legacy.fail(code, message, status, **extra)


def seal(value, key):
    return {**value, key: digest(value)}


def checked_hash(value, key):
    return type(value) is dict and value.get(key) == digest({k: v for k, v in value.items() if k != key})


def is_evidence(row):
    return bool(row and type(row.data) is dict and row.data.get('schema') == STATE)


def scrub(text):
    """Immutable evidence-privacy-v1, copied from the original preview filter."""
    text = re.sub(r'(?i)\b(?:sk-[a-z0-9_-]{10,}|bearer\s+[a-z0-9._-]{10,})', '[凭证已移除]', text)
    text = re.sub(r'(?i)(?:api[_ -]?key|password|secret|token|密码|密钥)\s*[:=：]\s*[^\s,，;；]+', '[凭证已移除]', text)
    if '@' in text:
        text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱已移除]', text)
    return re.sub(r'(?<!\d)(?:1[3-9]\d{9}|\d{17}[0-9Xx])(?!\d)', '[个人号码已移除]', text)


def scrub_tree(value):
    if isinstance(value, dict):
        return {scrub(str(k)): scrub_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_tree(v) for v in value]
    return scrub(value) if isinstance(value, str) else value


def _context(configuration):
    profile = configuration.get('instanceProfile', {})
    return scrub_tree({'profile': {key: str(profile.get(key, '')) for key in ('name', 'domain', 'goal')},
        'businesses': [item if type(item) is str else item['name'] for item in configuration.get('businesses', [])],
        'object_names': [item.get('name', '') for item in configuration.get('instanceObjects', [])]})


def preparation_cap(cap, *, generation_version=None):
    execution = cap.data['execution']
    keys = ('provider', 'model', 'max_tokens', 'thinking_mode')
    if generation_version in {v3.GENERATION_V3, GENERATION_V4, GENERATION_V5, GENERATION_V6, GENERATION_V7, GENERATION_V168, GENERATION_V172}:
        keys += ('report_evidence_output',)
    return {'execution': {key: copy.deepcopy(execution[key]) for key in keys if key in execution},
        'data_policy': {'redact_fields': copy.deepcopy(cap.data.get('data_policy', {}).get('redact_fields', []))}}


def _cap(preview):
    data = preview['preparation_cap']
    if (type(data) is not dict or set(data) != {'execution', 'data_policy'}
            or type(data['execution']) is not dict or set(data['execution']) - ({'provider', 'model', 'max_tokens', 'thinking_mode'}
                | ({'report_evidence_output'} if v3.applies(preview) or v4.applies(preview) or _family(preview).applies(preview) else set()))
            or not {'provider', 'model'} <= set(data['execution']) or type(data['data_policy']) is not dict
            or set(data['data_policy']) != {'redact_fields'} or type(data['data_policy']['redact_fields']) is not list
            or any(type(item) is not str for item in data['data_policy']['redact_fields'])):
        raise ValueError('Preparation capability differs')
    return SimpleNamespace(data=copy.deepcopy(data))


def _catalog(preview):
    projection, _ = project_full_source(check_full_source(preview['source_snapshot']))
    projection = scrub_tree(projection)
    if digest(projection) != preview['projection_hash']:
        raise ValueError('Frozen privacy projection differs')
    return build_evidence_source(preview['source_hash'], [[block[0], block[3]] for block in projection['blocks']])


def build_preview(p, body, configuration, cap, source, *, can_start=True,
                  generation_version=CURRENT_MAP_GENERATION):
    source = check_full_source(copy.deepcopy(source)); source_hash = digest(source)
    projection = scrub_tree(project_full_source(source)[0]); minimal = preparation_cap(cap, generation_version=generation_version)
    prepared = build_map_preparation(SimpleNamespace(data=minimal),
        build_evidence_source(source_hash, [[block[0], block[3]] for block in projection['blocks']]),
        _context(configuration), scrub(body.note), generation_version=generation_version)
    value = {'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': body.model_dump(mode='json'), 'configuration_hash': digest(configuration),
        'published_model_hash': cap.content_hash, 'preparation_cap': minimal,
        'provider': cap.data['execution']['provider'], 'model': cap.data['execution']['model'],
        'source_snapshot': source, 'source_hash': source_hash, 'projection_hash': digest(projection),
        'map_preparation': prepared, 'input_summary': copy.deepcopy(prepared['input_summary']),
        'can_start': can_start, 'start_blocker': None if can_start else '工作区模型预算不足；本次仅预览，没有预留或调用。',
        'can_apply': False, 'can_publish': False}
    value = seal(value, 'preview_hash')
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_PREVIEW_BYTES:
        fail('evidence_preview_capacity', '完整预览超过保存容量，原件保留；未截断或发送', 413)
    return value


def check_preview(value, p):
    try:
        if (type(value) is not dict or set(value) != PREVIEW_KEYS or value['schema'] != PREVIEW
                or value['actor_id'] != p.user_id or value['workspace_id'] != p.workspace_id
                or not checked_hash(value, 'preview_hash') or digest(value['source_snapshot']) != value['source_hash']
                or not re.fullmatch(r'[a-f0-9]{64}', value['published_model_hash'])
                or type(value['can_start']) is not bool or value['can_apply'] is not False or value['can_publish'] is not False
                or value['start_blocker'] != (None if value['can_start'] else '工作区模型预算不足；本次仅预览，没有预留或调用。')):
            raise ValueError('Preview scope/hash')
        body = PreviewCommand.model_validate(value['body']); source = check_full_source(value['source_snapshot'])
        if body.selection.model_dump(mode='json') != source['selection']:
            raise ValueError('Selection')
        cap = _cap(value)
        if value['provider'] != cap.data['execution']['provider'] or value['model'] != cap.data['execution']['model']:
            raise ValueError('Model')
        check_map_preparation(value['map_preparation'], _catalog(value), cap)
        if value['input_summary'] != value['map_preparation']['input_summary']:
            raise ValueError('Summary')
        if value['map_preparation']['user_instruction'] != scrub(body.note):
            raise ValueError('User instruction')
        return value
    except (KeyError, TypeError, ValueError, HTTPException):
        fail('evidence_preview_integrity', '冻结全文、来源范围或消息无法核对；原稿保留，不会重发')


def budget_available(db, p, tokens, calls):
    return full.budget_available(db, p, tokens, calls)


def make_preview(db, p, body):
    config = legacy.workspace_draft(db, p)
    if config.version != body.revision:
        fail('configuration_changed', '工作区已变化，请重新核对预览')
    cap = legacy.old().model(db, p, str(body.model_id))
    generation = _current_map_generation(cap.data['execution'])
    value = build_preview(p, body, config.data, cap, freeze_full_source(db, p, body.selection),
        generation_version=generation)
    summary = value['input_summary']
    if not budget_available(db, p, summary['map_token_bound'], summary['map_call_count']):
        value = build_preview(p, body, config.data, cap, value['source_snapshot'], can_start=False,
            generation_version=generation)
    return value


def command_hash(p, body, *, kind='report_evidence_reading', draft_id=None):
    value = {'kind': kind, 'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'body': body.model_dump(mode='json')}
    if draft_id is not None:
        value['draft_id'] = draft_id
    return digest(value)


def start_receipt(p, identity, body, preview):
    return seal({'schema': 'report-evidence-reading-start-receipt-v1', 'kind': 'report_evidence_reading',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'request_id': body.request_id, 'draft_id': identity,
        'preview_hash': preview['preview_hash'], 'source_hash': preview['source_hash'],
        'command_hash': command_hash(p, body)}, 'receipt_hash')


def _empty_stage(status='pending', **extra):
    return {'status': status, 'reserved_call_id': None, 'journal': None, 'result_hash': None,
        'result_count': None, 'inflight': False, 'error': None, 'error_code': None, **extra}


def _new_data(p, identity, body, preview, job_id):
    return {'schema': STATE, 'phase': 'map', 'preview': preview,
        'start_command_hash': command_hash(p, body), 'start_receipt': start_receipt(p, identity, body, preview),
        'maps': [_empty_stage(chunk_id=chunk['chunk_id']) for chunk in preview['map_preparation']['plan']['chunks']],
        'reduction': _empty_stage('not_started', preview=None, start_receipt=None),
        'model_result_hash': None, 'review_value': None, 'review_history': [], 'recovery_history': [], 'resume_history': [],
        'error': None, 'error_code': None, 'job_id': job_id}


def _state_size(value):
    if not legacy.jsonb_compatible(value):
        fail('evidence_state_invalid', '草案包含不能持久保存的值，原记录保留', 422)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_STATE_BYTES:
        fail('evidence_state_capacity', '草案与人工历史已达保存容量，原稿保留；未截断或覆盖', 413)


def _set(row, value):
    _state_size(value); row.data = value; row.version += 1


def _stage(row, key):
    if key == 'reduce':
        return row.data['reduction']
    return next(stage for stage in row.data['maps'] if stage['chunk_id'] == key)


def _replace_stage(row, key, value, **fields):
    data = copy.deepcopy(row.data)
    if key == 'reduce':
        data['reduction'] = value
    else:
        data['maps'] = [value if stage['chunk_id'] == key else stage for stage in data['maps']]
    data.update(fields); _set(row, data)


def _journal_for(row, key):
    return journal_v2 if key == 'reduce' and _family(row.data['preview']).applies(row.data['preview']) else journal


def _journal_root(row=None, key=None):
    from .config import settings
    namespace = ('report-evidence-reduction-journal-v2'
        if row is not None and key == 'reduce' and _family(row.data['preview']).applies(row.data['preview']) else 'report-evidence-journal')
    return Path(settings().storage_dir).absolute() / namespace


def _stage_journal_root(row, key):
    # Preserve the old zero-argument locator for every old protocol and Map.
    return _journal_root(row, key) if _journal_for(row, key) is journal_v2 else _journal_root()


def _authorized(row, key):
    if key == 'reduce':
        return row.data['reduction']['preview']['reduction_preparation']
    return next(chunk for chunk in row.data['preview']['map_preparation']['plan']['chunks'] if chunk['chunk_id'] == key)


def _spec(row, key):
    if key != 'reduce':
        from . import report_evidence_enum_pipeline_v172 as enum_family
        if enum_family.applies(row.data['preview']):
            from .report_evidence_enum_map_v172 import stage_spec
            return stage_spec(row.data['preview']['map_preparation'], key)
    return (_authorized(row, key) if key == 'reduce' else row.data['preview']['map_preparation'])['generation_contract']


def journal_binding(row, key, call_id):
    preview = row.data['preview']
    authorized = _authorized(row, key)
    dispatch = (_family(preview).dispatch(preview, key, authorized) if _family(preview).applies(preview) else
        v4.dispatch(preview, key, authorized) if v4.applies(preview) else
        v3.dispatch(preview, key, authorized) if v3.applies(preview)
        else {'model_id': preview['body']['model_id'], 'authorized': authorized})
    schema = 'report-evidence-journal-binding-v2' if _journal_for(row, key) is journal_v2 else 'report-evidence-journal-binding-v1'
    return {'schema': schema, 'actor_id': row.actor_id, 'workspace_id': row.workspace_id,
        'draft_id': row.id, 'stage': key, 'reserved_call_id': call_id, 'source_hash': preview['source_hash'],
        'catalog_hash': preview['map_preparation']['catalog_descriptor']['catalog_hash'],
        'generation_hash': digest({'published_model_hash': preview['published_model_hash'], 'contract': _spec(row, key)}),
        'dispatch_hash': digest(dispatch),
        'messages_hash': _authorized(row, key)['messages_hash']}


def _descriptor(row, key):
    stage = _stage(row, key)
    if not stage['reserved_call_id']:
        if stage['journal'] is not None:
            raise ValueError('Journal without reservation')
        return None
    adapter = _journal_for(row, key)
    try:
        descriptor = adapter.read_descriptor(_stage_journal_root(row, key), journal_binding(row, key, stage['reserved_call_id']))
    except adapter.JournalError as error:
        if stage['journal'] is None and error.code in {'journal_missing_or_unsafe', 'journal_partial_or_corrupt'}:
            return None
        raise
    if stage['journal'] is not None and descriptor != stage['journal']:
        raise ValueError('Original journal descriptor changed')
    return descriptor


def _parsed(row, key):
    stage = _stage(row, key); descriptor = _descriptor(row, key)
    if descriptor is None:
        fail('evidence_saved_response_incomplete', '尚无完整原回答，原编号保留；不会补发或重复付费')
    preview = row.data['preview']
    if _family(preview).applies(preview):
        loaded = _journal_for(row, key).read_response(_stage_journal_root(row, key),
            journal_binding(row, key, stage['reserved_call_id']), descriptor)
        _check_receipt_identity(row, key, loaded['receipt'])
        return _family(row.data['preview']).parsed_response(sys.modules[__name__], row, key, loaded, descriptor)
    if v4.applies(preview):
        loaded = journal.read_response(_journal_root(), journal_binding(row, key, stage['reserved_call_id']), descriptor)
        _check_receipt_identity(row, key, loaded['receipt'])
        return v4.parsed_response(sys.modules[__name__], row, key, loaded, descriptor)
    response = journal.parse_response(_journal_root(), journal_binding(row, key, stage['reserved_call_id']), descriptor)
    _check_receipt_identity(row, key, response['receipt'])
    if v3.applies(preview):
        v3.check_response(sys.modules[__name__], row, key, response, descriptor)
    return response


def _check_receipt_identity(row, key, receipt):
    stage = _stage(row, key); preview = row.data['preview']
    if (receipt.get('call_id') != stage['reserved_call_id'] or receipt.get('provider') != preview['provider']
            or receipt.get('model') != preview['model']):
        raise ValueError('Original receipt/model differs')


def _map_results(row, catalog=None):
    catalog = catalog or _catalog(row.data['preview']); results = {}
    for stage in row.data['maps']:
        if stage['status'] != 'succeeded':
            continue
        key = stage['chunk_id']; response = _parsed(row, key)
        bounded = _family(row.data['preview']).applies(row.data['preview']); strict = v4.applies(row.data['preview']); modern = bounded or strict or v3.applies(row.data['preview'])
        value = (_family(row.data['preview']).stage_projection(sys.modules[__name__], row, key, catalog) if bounded else
            v4.stage_projection(sys.modules[__name__], row, key, catalog) if strict else
            v3.stage_projection(sys.modules[__name__], row, key, catalog) if modern else
            validate_map_result(response['value'], catalog, _authorized(row, key)['span_refs']))
        if ((not modern and value != response['value']) or digest(value) != stage['result_hash']
                or len(value['claims']) != stage['result_count']):
            raise ValueError('Map result differs from original')
        results[key] = value
    return results


def _reduction_result(row, catalog=None, maps=None):
    if v4.applies(row.data['preview']) and row.data['reduction']['status'] != 'not_started':
        _block_v4_reduce(row.data['preview'])
    if row.data['reduction']['status'] != 'succeeded':
        return None
    preview = row.data['preview']; catalog = catalog or _catalog(preview); maps = maps if maps is not None else _map_results(row, catalog)
    prepared = row.data['reduction']['preview']['reduction_preparation']
    response = _parsed(row, 'reduce')
    modern = v3.applies(preview) or _family(preview).applies(preview)
    value = (_family(row.data['preview']).stage_projection(sys.modules[__name__], row, 'reduce', catalog) if _family(preview).applies(preview) else
        v3.stage_projection(sys.modules[__name__], row, 'reduce', catalog) if modern else
        validate_evidence_merge(response['value'], catalog, [item[0] for item in prepared['payload']['spans']],
            preview['map_preparation']['context'], prepared['payload']['input_items']))
    count = sum(len(value['result'][kind]) for kind in ('global_claims', 'objects', 'relationships', 'businesses', 'questions'))
    if ((not modern and value != response['value']) or digest(value) != row.data['reduction']['result_hash']
            or digest(value['result']) != row.data['model_result_hash'] or count != row.data['reduction']['result_count']):
        raise ValueError('Reduction result differs from original')
    return value


def _reduce_receipt(row, p, body, preview):
    if v3.applies(row.data['preview']) or _family(row.data['preview']).applies(row.data['preview']):
        prepared = preview['reduction_preparation']
        schema = 'report-evidence-reduce-receipt-v3' if _family(row.data['preview']).applies(row.data['preview']) else 'report-evidence-reduce-receipt-v2'
        return seal({'schema': schema, 'kind': 'report_evidence_reduce',
            'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'request_id': body.request_id,
            'draft_id': row.id, 'accepted_revision': body.revision + 1, 'preview_hash': body.preview_hash,
            'map_inputs_hash': prepared['map_inputs_hash'], 'map_plan_hash': prepared['map_plan_hash'],
            'command_hash': command_hash(p, body, kind='report_evidence_reduce', draft_id=row.id)}, 'receipt_hash')
    return seal({'schema': 'report-evidence-reduce-receipt-v1', 'kind': 'report_evidence_reduce',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'request_id': body.request_id, 'draft_id': row.id,
        'accepted_revision': body.revision + 1, 'preview_hash': body.preview_hash,
        'map_results_hash': preview['reduction_preparation']['map_results_hash'],
        'command_hash': command_hash(p, body, kind='report_evidence_reduce', draft_id=row.id)}, 'receipt_hash')


def _check_reduce_preview(value, row, p, catalog, maps):
    if (type(value) is not dict or set(value) != {'schema', 'actor_id', 'workspace_id', 'draft_id', 'body',
            'published_model_hash', 'reduction_preparation', 'can_start', 'start_blocker', 'can_apply', 'can_publish', 'preview_hash'}
            or value['schema'] != 'report-evidence-reduce-preview-v1' or not checked_hash(value, 'preview_hash')
            or value['actor_id'] != p.user_id or value['workspace_id'] != p.workspace_id or value['draft_id'] != row.id
            or value['published_model_hash'] != row.data['preview']['published_model_hash']
            or type(value['can_start']) is not bool or value['can_apply'] is not False or value['can_publish'] is not False
            or value['start_blocker'] not in ([None] if value['can_start'] else
                ['归并预算不足；原声明已保存，本次只预览。'] + (['汇总尚未提供独立额度授权；原声明已保存，本次只预览。']
                    if row.request_id.startswith('fl_') else []))):
        raise ValueError('Reduction preview differs')
    ReducePreviewCommand.model_validate(value['body'])
    if _family(row.data['preview']).applies(row.data['preview']):
        _reducer(row.data['preview']).check_reduction_preparation(value['reduction_preparation'], catalog, _cap(row.data['preview']),
            row.data['preview']['map_preparation'], _family(row.data['preview']).map_inputs(sys.modules[__name__], row, catalog))
    elif v3.applies(row.data['preview']):
        check_reduction_preparation(value['reduction_preparation'], catalog, _cap(row.data['preview']),
            row.data['preview']['map_preparation'],
            map_inputs=v3.complete_map_inputs(sys.modules[__name__], row, catalog))
    else:
        check_reduction_preparation(value['reduction_preparation'], catalog, _cap(row.data['preview']),
            row.data['preview']['map_preparation'], maps)


def _compact_chunk(chunk):
    keys = ('chunk_id', 'messages_hash', 'input_summary', 'chunk_hash')
    if 'request_hash' in chunk: keys += ('request_hash',)
    return {key: copy.deepcopy(chunk[key]) for key in keys}


def _check_resume_preview(value, row, p):
    keys = {'schema', 'actor_id', 'workspace_id', 'draft_id', 'body', 'original_preview_hash', 'published_model_hash',
        'source_hash', 'completed_chunks', 'saved_chunks', 'remaining_chunks', 'input_summary',
        'can_start', 'start_blocker', 'can_apply', 'can_publish', 'preview_hash'}
    original = row.data['preview']
    if (type(value) is not dict or set(value) != keys or value['schema'] != 'report-evidence-resume-preview-v1'
            or not checked_hash(value, 'preview_hash') or value['actor_id'] != p.user_id
            or value['workspace_id'] != p.workspace_id or value['draft_id'] != row.id
            or value['original_preview_hash'] != original['preview_hash'] or value['source_hash'] != original['source_hash']
            or value['published_model_hash'] != original['published_model_hash'] or type(value['can_start']) is not bool
            or value['can_apply'] is not False or value['can_publish'] is not False
            or value['start_blocker'] not in ([None] if value['can_start'] else
                ['剩余资料读取预算不足；已保存回答不变，本次只预览。'] + (['原定向额度尚不能安全交接给新任务；已保存回答不变，本次只预览。']
                    if row.request_id.startswith('fl_') else []))):
        raise ValueError('Resume preview differs')
    ResumePreviewCommand.model_validate(value['body'])
    chunks = original['map_preparation']['plan']['chunks']; ids = [chunk['chunk_id'] for chunk in chunks]
    if any(type(value[key]) is not list for key in ('completed_chunks', 'saved_chunks', 'remaining_chunks')):
        raise ValueError('Resume groups differ')
    remaining = [item['chunk_id'] for item in value['remaining_chunks']]
    groups = [value['completed_chunks'], value['saved_chunks'], remaining]
    if (not remaining or any(any(type(item) is not str for item in group) or group != [key for key in ids if key in group] for group in groups)
            or len([item for group in groups for item in group]) != len(ids)
            or set().union(*(set(group) for group in groups)) != set(ids)):
        raise ValueError('Resume does not cover each original batch exactly once')
    if value['remaining_chunks'] != [_compact_chunk(chunk) for chunk in chunks if chunk['chunk_id'] in remaining]:
        raise ValueError('Resume changes original messages')
    byte_field = 'prepared_request_bytes' if v4.applies(original) or _family(original).applies(original) else 'prepared_messages_bytes'
    expected = {'call_count': len(remaining),
        'token_bound': sum(chunk['input_summary']['token_bound'] for chunk in value['remaining_chunks']),
        byte_field: sum(chunk['input_summary'][byte_field] for chunk in value['remaining_chunks'])}
    if value['input_summary'] != expected:
        raise ValueError('Resume budget differs')


def _resume_receipt(row, p, body, preview):
    return seal({'schema': 'report-evidence-resume-receipt-v1', 'kind': 'report_evidence_resume',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'draft_id': row.id, 'request_id': body.request_id,
        'accepted_revision': body.revision + 1, 'preview_hash': body.preview_hash,
        'remaining_chunks': [chunk['chunk_id'] for chunk in preview['remaining_chunks']],
        'command_hash': command_hash(p, body, kind='report_evidence_resume', draft_id=row.id)}, 'receipt_hash')


def check_row(row, p):
    if not row or row.workspace_id != p.workspace_id or row.actor_id != p.user_id:
        fail('not_found', '未找到本人分段读取草案', 404)
    try:
        if (not is_evidence(row) or set(row.data) != STATE_KEYS or row.status not in STATES
                or row.data['phase'] not in PHASES or type(row.version) is not int or row.version < 1):
            raise ValueError('State')
        _state_size(row.data); preview = check_preview(row.data['preview'], p)
        body = StartCommand(**preview['body'], preview_hash=preview['preview_hash'], consent=True)
        if (row.request_id != body.request_id or row.data['start_command_hash'] != command_hash(p, body)
                or row.data['start_receipt'] != start_receipt(p, row.id, body, preview)):
            raise ValueError('Start receipt')
        if [stage['chunk_id'] for stage in row.data['maps']] != [chunk['chunk_id'] for chunk in preview['map_preparation']['plan']['chunks']]:
            raise ValueError('Map stage coverage')
        for stage in [*row.data['maps'], row.data['reduction']]:
            extra = {'chunk_id'} if 'chunk_id' in stage else {'preview', 'start_receipt'}
            if (set(stage) != STAGE_KEYS | extra or stage['status'] not in {'pending', 'queued', 'running', 'succeeded', 'failed', 'response_unknown', 'not_started'}
                    or type(stage['inflight']) is not bool):
                raise ValueError('Stage shape')
            if stage['reserved_call_id'] is not None and (type(stage['reserved_call_id']) is not str
                    or str(UUID(stage['reserved_call_id'])) != stage['reserved_call_id']):
                raise ValueError('Reserved call')
            if stage['result_hash'] is not None and (stage['status'] != 'succeeded' or not re.fullmatch(r'[a-f0-9]{64}', stage['result_hash'])):
                raise ValueError('Result status/hash')
            if stage['result_hash'] is None and stage['result_count'] is not None:
                raise ValueError('Count without result')
            if stage['status'] == 'succeeded' and (stage['journal'] is None or stage['result_hash'] is None
                    or type(stage['result_count']) is not int or stage['result_count'] < 0 or stage['inflight'] is not False):
                raise ValueError('Succeeded original')
            key = stage.get('chunk_id', 'reduce')
            if stage['journal'] is not None:
                _descriptor(row, key)
        catalog = _catalog(preview); maps = _map_results(row, catalog)
        reduction = row.data['reduction']
        if v4.applies(preview) and (reduction['preview'] is not None or row.data['review_history']):
            raise ValueError('Strict Map V4 cannot contain Reduce or manual adoption')
        if reduction['preview'] is not None:
            _check_reduce_preview(reduction['preview'], row, p, catalog, maps)
            command = ReduceCommand(**reduction['preview']['body'], preview_hash=reduction['preview']['preview_hash'], consent=True)
            if reduction['start_receipt'] != _reduce_receipt(row, p, command, reduction['preview']):
                raise ValueError('Reduce receipt')
        elif reduction['start_receipt'] is not None or reduction['reserved_call_id'] is not None:
            raise ValueError('Reduction without authority')
        original = _reduction_result(row, catalog, maps)
        if original is None and row.data['model_result_hash'] is not None:
            raise ValueError('Final result without original')
        for entry in row.data['review_history']:
            if set(entry) != {'value', 'receipt'}:
                raise ValueError('Review entry')
            command = ReviewCommand(revision=entry['receipt']['accepted_revision'] - 1,
                request_id=entry['receipt']['request_id'], value=entry['value'])
            receipt = _review_receipt(row, p, command, entry['value'])
            if receipt != entry['receipt'] or receipt['accepted_revision'] > row.version:
                raise ValueError('Review receipt')
            if v3.applies(preview) or _family(preview).applies(preview):
                v3.manual_projection(sys.modules[__name__], row, p, command, entry['value'], catalog)
            else:
                result = validate_evidence_result(entry['value']['result'], catalog, list(catalog['index']), preview['map_preparation']['context'])
                if result != entry['value']['result']:
                    raise ValueError('Review changed during validation')
        if row.data['review_value'] != (row.data['review_history'][-1]['value'] if row.data['review_history'] else None):
            raise ValueError('Latest review')
        for receipt in row.data['recovery_history']:
            command = RecoveryCommand(revision=receipt['accepted_revision'] - 1, request_id=receipt['request_id'])
            expected = (_native_recovery_receipt(row, p, command, receipt['recovered_stages'],
                receipt['remaining_chunks'], receipt['completion_scope'])
                if _family(preview).native(preview) else _recovery_receipt(row, p, command))
            if receipt != expected or receipt['accepted_revision'] > row.version:
                raise ValueError('Recovery receipt')
        if len(row.data['resume_history']) > 24:
            raise ValueError('Resume history capacity')
        for entry in row.data['resume_history']:
            if set(entry) != {'preview', 'receipt'}:
                raise ValueError('Resume entry')
            _check_resume_preview(entry['preview'], row, p)
            command = ResumeCommand(**entry['preview']['body'], preview_hash=entry['preview']['preview_hash'], consent=True)
            if entry['receipt'] != _resume_receipt(row, p, command, entry['preview']) or entry['receipt']['accepted_revision'] > row.version:
                raise ValueError('Resume receipt')
        return row
    except (KeyError, TypeError, ValueError, HTTPException):
        fail('evidence_state_integrity', '原调用、来源、原回答或人工稿无法核对；记录保留，不会重发')


def owned(db, p, identity, lock=False):
    query = select(SetupReportDraft).where(SetupReportDraft.id == str(identity),
        SetupReportDraft.workspace_id == p.workspace_id, SetupReportDraft.actor_id == p.user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = check_row(db.scalar(query), p)
    if row.request_id.startswith('fl_'):
        from .report_evidence_delegated_origin import verify_link
        verify_link(db, p, row, lock=lock)
    return row


def _stage_view(row, key, result=None):
    stage = _stage(row, key); descriptor = _descriptor(row, key); receipt = None
    if descriptor and descriptor['consumable']:
        receipt = _journal_for(row, key).read_response(_stage_journal_root(row, key),
            journal_binding(row, key, stage['reserved_call_id']), descriptor)['receipt']
        if row.request_id.startswith('fl_'):
            # Provider headers and arbitrary usage fields are untrusted response
            # bytes.  They can echo credentials, so the private UI receives only
            # a small numeric usage projection.  The ledger and immutable journal
            # retain the exact receipt for all server-side integrity checks.
            usage = receipt.get('usage')
            allowed = ('prompt_tokens', 'completion_tokens', 'total_tokens',
                       'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
            safe_usage = ({name: usage[name] for name in allowed
                           if type(usage.get(name)) is int and 0 <= usage[name] <= 2**53 - 1}
                          if isinstance(usage, dict) else {})
            finish = receipt.get('finish_reason')
            count = receipt.get('response_choice_count')
            receipt = {'provider': receipt.get('provider'), 'model': receipt.get('model'),
                       'usage': safe_usage, 'request_id': None, 'call_id': receipt.get('call_id'),
                       'evidence_profile': copy.deepcopy(receipt.get('evidence_profile')),
                       'finish_reason': finish if type(finish) is str and finish in
                       {'stop', 'length', 'content_filter', 'tool_calls', 'function_call'} else None,
                       'response_choice_count': count if type(count) is int and 0 <= count <= 2**53 - 1 else None}
    value = {field: copy.deepcopy(stage[field]) for field in
        ('status', 'reserved_call_id', 'result_hash', 'result_count', 'error', 'error_code')}
    value.update(journal=descriptor, raw_response_saved=descriptor is not None, receipt=receipt)
    if key != 'reduce':
        value.update(chunk_id=key, result=copy.deepcopy(result))
    return value


def draft_view(row, p):
    check_row(row, p); data = row.data; catalog = _catalog(data['preview']); maps = _map_results(row, catalog)
    reduced = _reduction_result(row, catalog, maps)
    value = {'schema': 'report-evidence-reading-draft-v1', 'id': row.id, 'actor_id': row.actor_id,
        'workspace_id': row.workspace_id, 'request_id': row.request_id, 'status': row.status, 'revision': row.version,
        'phase': data['phase'], 'preview': copy.deepcopy(data['preview']),
        'maps': [_stage_view(row, stage['chunk_id'], maps.get(stage['chunk_id'])) for stage in data['maps']],
        'reduction': {**_stage_view(row, 'reduce'), 'preview': copy.deepcopy(data['reduction']['preview']),
            'start_receipt': copy.deepcopy(data['reduction']['start_receipt']),
            'reconciliation': None if reduced is None else copy.deepcopy(reduced['reconciliation'])},
        'model_result': None if reduced is None else copy.deepcopy(reduced['result']), 'model_result_hash': data['model_result_hash'],
        'review_value': copy.deepcopy(data['review_value']), 'review_history': copy.deepcopy(data['review_history']),
        'recovery_history': copy.deepcopy(data['recovery_history']), 'resume_history': copy.deepcopy(data['resume_history']),
        'start_receipt': copy.deepcopy(data['start_receipt']), 'error': data['error'], 'error_code': data['error_code'],
        'job_id': data['job_id'], 'can_apply': False, 'can_publish': False}
    if v3.applies(data['preview']) or v4.applies(data['preview']) or _family(data['preview']).applies(data['preview']):
        value['schema'] = 'report-evidence-reading-draft-v3' if _family(data['preview']).applies(data['preview']) else 'report-evidence-reading-draft-v2'
        value['reduction']['projection_metadata'] = v3.reduction_metadata(reduced)
    if _family(data['preview']) is not v5:
        value['schema'] = ('report-claim-enum-reading-draft-v172'
            if data['preview']['map_preparation']['generation_contract']['schema'] == GENERATION_V172
            else 'report-claim-array-reading-draft-v168')
        value['object_review_family'] = 'report-object-semantic-review-v166'
    return seal(value, 'view_hash')


def replay_start(row, p, body):
    check_row(row, p)
    if command_hash(p, body) != row.data['start_command_hash']:
        fail('nonce_conflict', '原读取编号已用于另一请求；不会新增调用')
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(row.data['start_receipt'])}


def _review_receipt(row, p, body, value):
    return seal({'schema': 'report-evidence-review-receipt-v1', 'kind': 'report_evidence_review',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'draft_id': row.id, 'request_id': str(body.request_id),
        'accepted_revision': body.revision + 1, 'value_hash': digest(value),
        'command_hash': command_hash(p, body, kind='report_evidence_review', draft_id=row.id)}, 'receipt_hash')


def _recovery_receipt(row, p, body):
    return seal({'schema': 'report-evidence-recovery-receipt-v1', 'kind': 'report_evidence_recovery',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'draft_id': row.id, 'request_id': str(body.request_id),
        'accepted_revision': body.revision + 1,
        'command_hash': command_hash(p, body, kind='report_evidence_recovery', draft_id=row.id)}, 'receipt_hash')


def _native_recovery_receipt(row, p, body, recovered, remaining, completion):
    ids = [item['chunk_id'] for item in row.data['maps']]
    if (type(recovered) is not list or not recovered or type(remaining) is not list
            or remaining != [key for key in ids if key in remaining]
            or len(remaining) != len(set(remaining))
            or completion not in {'partial_maps', 'all_maps', 'reduction'}):
        raise ValueError('Native recovery receipt scope differs')
    names = []
    from . import report_evidence_native_bridge_v1 as bridge
    for item in recovered:
        if type(item) is not dict or set(item) != {'stage', 'call_id', 'capture_hash', 'journal_descriptor_hash', 'result_hash'}:
            raise ValueError('Native recovery stage receipt differs')
        if item['stage'] not in ids + ['reduce'] or item['stage'] == 'reduce' and row.data['reduction']['preview'] is None:
            raise ValueError('Native recovery receipt has an unknown stage')
        key = item['stage']; stage = _stage(row, key); descriptor = _descriptor(row, key)
        state = bridge.load_verified(sys.modules[__name__], row, key, item['capture_hash'])
        if (stage['status'] != 'succeeded' or item['call_id'] != stage['reserved_call_id']
                or descriptor is None or descriptor['descriptor_hash'] != item['journal_descriptor_hash']
                or item['result_hash'] != stage['result_hash'] or state['ready'] is not True or key in names):
            raise ValueError('Native recovery receipt original differs')
        names.append(key)
    if ((completion == 'reduction') != (names == ['reduce']) or set(names) & set(remaining)
            or completion != 'reduction' and names != [key for key in ids if key in names]
            or (completion == 'partial_maps') != bool(remaining)):
        raise ValueError('Native recovery receipt order differs')
    value = _recovery_receipt(row, p, body)
    value.pop('receipt_hash'); value.update(schema='report-evidence-recovery-receipt-v2',
        recovered_stages=copy.deepcopy(recovered), remaining_chunks=copy.deepcopy(remaining),
        completion_scope=completion, new_cloud_calls=0)
    return seal(value, 'receipt_hash')


def _native_preflight(row, key):
    """No writes or settlement: check raw, all semantics and journal conflicts."""
    from . import report_evidence_native_bridge_v1 as bridge
    original, receipt, state = bridge.response(sys.modules[__name__], row, key)
    adapter = _journal_for(row, key); binding = journal_binding(row, key, _stage(row, key)['reserved_call_id'])
    descriptor = bridge.check_missing_journal(adapter, _stage_journal_root(row, key), binding, original, receipt)
    loaded = {'content': original, 'receipt': receipt, 'descriptor': descriptor}
    body, inspected = _family(row.data['preview']).inspect_response(sys.modules[__name__], row, key, loaded, descriptor)
    wire = _family(row.data['preview'])._parse(row.data['preview'], key, body, inspected)
    catalog = _catalog(row.data['preview'])
    if _family(row.data['preview']) is not v5:
        _family(row.data['preview']).preflight_projection(sys.modules[__name__], row, key, wire, descriptor,
            body, inspected, binding, receipt, catalog)
        return original, receipt, state, descriptor
    if key == 'reduce':
        from .report_evidence_reduction_wire_v6 import build_reduction_projection
        maps = _family(row.data['preview']).map_inputs(sys.modules[__name__], row, catalog)
        prep = row.data['preview']['map_preparation']; cap = _cap(row.data['preview'])
        _reducer(row.data['preview']).check_reduction_preparation(_authorized(row, key), catalog, cap, prep, maps)
        inputs = _reducer(row.data['preview']).reduction_inputs_v3(prep, catalog, cap, maps)
        build_reduction_projection(inspected['arguments'].encode('utf-8'), catalog=catalog,
            allowed_span_refs=inputs['allowed_span_refs'], context=inputs['context'],
            input_order=inputs['input_order'], input_items=inputs['input_items'])
    else:
        from .report_evidence_projection_v5 import build_map_projection
        build_map_projection(wire, binding=binding, expected_binding=binding, descriptor=descriptor,
            metadata=inspected['metadata'], evidence_profile=receipt['evidence_profile'], catalog=catalog,
            allowed_span_refs=_authorized(row, key)['span_refs'], protocol_version=7)
    return original, receipt, state, descriptor


def _recover_native(db, p, row, command):
    from . import report_evidence_native_bridge_v1 as bridge
    stages = ([('reduce', row.data['reduction'])] if row.data['reduction']['preview'] else
        [(item['chunk_id'], item) for item in row.data['maps'] if item['status'] != 'succeeded'])
    targets, remaining = [], []
    for key, stage in stages:
        if stage['reserved_call_id'] is None:
            if stage['inflight'] or key == 'reduce':
                fail('evidence_saved_response_incomplete', '原调用状态未知，不能免费恢复或再次付费')
            remaining.append(key)
        else:
            targets.append(key)
    if not targets:
        fail('evidence_saved_response_incomplete', '尚无可恢复的原付费回答，没有新增调用')
    checked = []
    try:
        for key in targets:
            call = _call_guard(db, p, row, key, _stage(row, key)['reserved_call_id'])
            try:
                state = bridge.load_verified(sys.modules[__name__], row, key)
            except FileNotFoundError:
                fail('evidence_saved_response_incomplete', '原HTTP捕获尚未完整保存，原调用保留')
            if state['ready'] is not True or state['content_encoding_safe'] is not True:
                fail('evidence_saved_response_incomplete', '原HTTP捕获未完整结束或已隔离，未修复或重发')
            original = _native_preflight(row, key)
            if call.status in {'failed', 'succeeded'}:
                saved, receipt = call.data.get('receipt'), original[1]
                critical = ('call_id', 'provider', 'model', 'usage', 'request_id')
                if (type(saved) is not dict or digest({k: saved[k] for k in critical if k in saved})
                        != digest({k: receipt[k] for k in critical if k in receipt})):
                    raise ValueError('Native finalized ledger receipt differs')
            elif call.status != 'reserved':
                raise ValueError('Native original reservation state differs')
            checked.append((key, original))
    except HTTPException:
        raise
    except (ValueError, OSError):
        fail('evidence_saved_response_invalid', '原回答、引用或磁盘片段不匹配；没有改写、结算或新调用', 422)
    accepted = row.version + 1
    for key, (original, receipt, _, _) in checked:
        binding = journal_binding(row, key, _stage(row, key)['reserved_call_id'])
        bridge.write_missing_journal(_journal_for(row, key), _stage_journal_root(row, key), binding, original, receipt)
    recovered = []
    for key, (_, _, state, _) in checked:
        _consume(db, p, row, key)
        stage = _stage(row, key)
        recovered.append({'stage': key, 'call_id': stage['reserved_call_id'], 'capture_hash': state['capture_hash'],
            'journal_descriptor_hash': stage['journal']['descriptor_hash'], 'result_hash': stage['result_hash']})
    if row.data['reduction']['preview'] is not None:
        completion = 'reduction'
    elif remaining:
        completion = 'partial_maps'; row.status = 'failed'
        _set(row, {**row.data, 'phase': 'failed', 'error_code': 'evidence_remaining_unstarted',
            'error': '已免费恢复原付费结果；剩余资料尚未发送，须另行预览并同意继续。'})
    else:
        completion = 'all_maps'; _finish_maps(db, p, row)
    receipt = _native_recovery_receipt(row, p, command, recovered, remaining, completion)
    row.version = accepted; row.data = {**row.data, 'recovery_history': [*row.data['recovery_history'], receipt]}
    _state_size(row.data); db.commit()
    return {'draft': draft_view(row, p), 'receipt': receipt}


def _fresh_cap(db, p, row, *, map_source=False):
    require(p, 'admin', 'operator')
    linked_original_id = None
    if row.request_id.startswith('fl_'):
        from .report_evidence_delegated_origin import verify_link
        origin = verify_link(db, p, row, require_current=True)
        linked_original_id = origin['payload']['basis']['original_draft_id']
    if map_source:
        from .report_unresolved_overlap import guard_new_paid_sources
        selection = row.data['preview']['body']['selection']
        guard_new_paid_sources(db, p, [selection['original_ref'], selection['copy_ref']],
            linked_original_id=linked_original_id, current_draft_id=row.id)
    _require_current_map(row.data['preview'])
    preview = row.data['preview']; cap = legacy.old().model(db, p, preview['body']['model_id'])
    if (cap.content_hash != preview['published_model_hash'] or preparation_cap(cap,
            generation_version=preview['map_preparation']['generation_contract']['schema']) != preview['preparation_cap']):
        fail('evidence_model_changed', '原已发布模型或准备配置已变化，已保存回答保留；未发送')
    if map_source:
        config = legacy.workspace_draft(db, p)
        command = PreviewCommand.model_validate(preview['body'])
        if config.version != command.revision:
            fail('configuration_changed', '原业务配置已变化，已保存回答保留；请重新核对')
        actual = build_preview(p, command, config.data, cap, freeze_full_source(db, p, command.selection),
            can_start=preview['can_start'], generation_version=preview['map_preparation']['generation_contract']['schema'])
        if actual != preview:
            fail('evidence_source_changed', '原件、精确页版本、配置或消息已变化；未发送')
    else:
        for ref in (preview['body']['selection']['original_ref'], preview['body']['selection']['copy_ref']):
            material = scoped(db, SetupMaterial, ref['id'], p)
            if material.archived or material.version != ref['version'] or material.sha256 != ref['hash']:
                fail('evidence_source_changed', '原资料已变化或归档，原声明保留；未发送归并')
    return cap


def _current_map_generation(execution):
    transport = declared_map_transport(execution)
    return (GENERATION_V172 if transport == CLAIM_ENUM_NATIVE_TRANSPORT else GENERATION_V168 if transport == CLAIM_NATIVE_TRANSPORT else GENERATION_V7 if transport == NATIVE_REPORT_TRANSPORT else GENERATION_V6 if transport == REF_ARM_REPORT_TRANSPORT else
        GENERATION_V5 if transport == STRUCTURED_REPORT_TRANSPORT else GENERATION_V4 if transport else CURRENT_MAP_GENERATION)


def _require_current_map(preview):
    expected = _current_map_generation(preview['preparation_cap']['execution'])
    if preview['map_preparation']['generation_contract']['schema'] != expected:
        fail('evidence_task_version_changed', '读取任务版本已更新；旧记录与原回答仍可读取核对，新调用须使用新编号重新预览并明确同意')


def _block_v4_reduce(preview):
    if v4.applies(preview):
        fail('evidence_reduce_unsupported', '此严格读取版本目前仅支持分组声明；汇总尚未接通，原答保留，不会降级或新增调用')


def _other_paid(db, p, identity):
    other = db.scalar(select(SetupReportDraft.id).where(SetupReportDraft.workspace_id == p.workspace_id,
        SetupReportDraft.id != identity, SetupReportDraft.status.in_(legacy.ACTIVE)).limit(1))
    old = db.scalars(select(SetupAnalysis).where(SetupAnalysis.workspace_id == p.workspace_id,
        SetupAnalysis.status.in_(['queued', 'running', 'failed']))).all()
    if other or any(legacy.legacy_pending(row, db) for row in old):
        fail('understanding_pending', '工作区还有原调用待处理或结果未知；未新增付费任务')


def reduce_preview(row, p, body, *, can_start=True):
    _block_v4_reduce(row.data['preview'])
    if row.version != body.revision or row.status != 'awaiting_merge' or any(stage['status'] != 'succeeded' for stage in row.data['maps']):
        fail('evidence_reduce_not_ready', '原声明尚未全部保存，或版本已变化，请读回原稿')
    preview = row.data['preview']; catalog = _catalog(preview); maps = _map_results(row, catalog)
    try:
        prepared = (_reducer(preview).build_reduction_preparation(_cap(preview), preview['map_preparation'], catalog,
            _family(row.data['preview']).map_inputs(sys.modules[__name__], row, catalog)) if _family(preview).applies(preview) else
            build_reduction_preparation(_cap(preview), preview['map_preparation'], catalog,
            map_inputs=v3.complete_map_inputs(sys.modules[__name__], row, catalog)) if v3.applies(preview)
            else build_reduction_preparation(_cap(preview), preview['map_preparation'], catalog, maps))
    except (ValueError, EvidenceCapacityExceeded):
        fail('evidence_reduce_capacity', '完整汇总输入无法按本次单调用契约发送；原声明保留，未截断或增加调用', 413)
    return seal({'schema': 'report-evidence-reduce-preview-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'draft_id': row.id, 'body': body.model_dump(mode='json'), 'published_model_hash': preview['published_model_hash'],
        'reduction_preparation': prepared, 'can_start': can_start,
        'start_blocker': None if can_start else ('汇总尚未提供独立额度授权；原声明已保存，本次只预览。'
            if row.request_id.startswith('fl_') else '归并预算不足；原声明已保存，本次只预览。'),
        'can_apply': False, 'can_publish': False}, 'preview_hash')


def resume_preview(row, p, body, *, can_start=True):
    if row.version != body.revision or row.status not in {'failed', 'response_unknown'} or row.data['reduction']['preview'] is not None:
        fail('evidence_resume_conflict', '仅可核对中断的原分段读取，版本或阶段已变化')
    if len(row.data['resume_history']) >= 24:
        fail('evidence_resume_capacity', '原恢复历史已达上限，已保存回答保留；不会隐藏删除或排队', 413)
    completed, saved, remaining = [], [], []
    catalog = _catalog(row.data['preview'])
    for stage in row.data['maps']:
        key = stage['chunk_id']
        if stage['status'] == 'succeeded':
            completed.append(key)
        elif stage['reserved_call_id']:
            try:
                response = _parsed(row, key)
                bounded = _family(row.data['preview']).applies(row.data['preview']); strict = v4.applies(row.data['preview']); modern = bounded or strict or v3.applies(row.data['preview'])
                result = (_family(row.data['preview']).stage_projection(sys.modules[__name__], row, key, catalog) if bounded else
                    v4.stage_projection(sys.modules[__name__], row, key, catalog) if strict else
                    v3.stage_projection(sys.modules[__name__], row, key, catalog) if modern else
                    validate_map_result(response['value'], catalog, _authorized(row, key)['span_refs']))
                if not modern and result != response['value']:
                    raise ValueError('Original changed')
            except (ValueError, HTTPException):
                fail('evidence_resume_blocked', '原调用结果未知、隔离或无效，必须先处理原号；不会重付或继续新批次')
            saved.append(key)
        else:
            if stage['inflight'] or stage['journal'] is not None:
                fail('evidence_resume_blocked', '原发送状态无法核对，原编号保留；不会继续付费')
            remaining.append(_compact_chunk(_authorized(row, key)))
    if not remaining:
        fail('evidence_resume_no_remaining', '没有未发送批次；如有已保存回答，请使用免费恢复')
    byte_field = 'prepared_request_bytes' if v4.applies(row.data['preview']) or _family(row.data['preview']).applies(row.data['preview']) else 'prepared_messages_bytes'
    value = seal({'schema': 'report-evidence-resume-preview-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'draft_id': row.id, 'body': body.model_dump(mode='json'), 'original_preview_hash': row.data['preview']['preview_hash'],
        'published_model_hash': row.data['preview']['published_model_hash'], 'source_hash': row.data['preview']['source_hash'],
        'completed_chunks': completed, 'saved_chunks': saved, 'remaining_chunks': remaining,
        'input_summary': {'call_count': len(remaining),
            'token_bound': sum(chunk['input_summary']['token_bound'] for chunk in remaining),
            byte_field: sum(chunk['input_summary'][byte_field] for chunk in remaining)},
        'can_start': can_start, 'start_blocker': None if can_start else ('原定向额度尚不能安全交接给新任务；已保存回答不变，本次只预览。'
            if row.request_id.startswith('fl_') else '剩余资料读取预算不足；已保存回答不变，本次只预览。'),
        'can_apply': False, 'can_publish': False}, 'preview_hash')
    _check_resume_preview(value, row, p)
    return value


@router.post('/report-evidence-readings/preview')
def preview_route(body: PreviewCommand, p: P, db: DB):
    require(p, 'admin', 'operator')
    if body.request_id.startswith('fl_'):
        fail('delegated_start_required', '受托读取须使用带来源关联的专用入口')
    if legacy.by_nonce(db, p, body.request_id):
        fail('nonce_exists', '此编号已有原记录，请先按原编号恢复')
    value = make_preview(db, p, body)
    from .report_unresolved_overlap import guard_new_paid_sources
    selection = value['body']['selection']
    guard_new_paid_sources(db, p, [selection['original_ref'], selection['copy_ref']])
    db.rollback(); return value


@router.post('/report-evidence-readings', status_code=202)
def start_route(body: StartCommand, p: P, db: DB):
    if body.request_id.startswith('fl_'):
        fail('delegated_start_required', '受托读取须使用带来源关联的专用入口')
    prior = legacy.by_nonce(db, p, body.request_id)
    if prior is not None:
        return replay_start(prior, p, body)
    require(p, 'admin', 'operator'); legacy.workspace_draft(db, p)
    prior = legacy.by_nonce(db, p, body.request_id)
    if prior is not None:
        return replay_start(prior, p, body)
    legacy.guard_existing_paid(db, p)
    command = PreviewCommand.model_validate({key: value for key, value in body.model_dump(mode='json').items()
        if key not in {'preview_hash', 'consent'}})
    value = make_preview(db, p, command)
    from .report_unresolved_overlap import guard_new_paid_sources
    selection = value['body']['selection']
    guard_new_paid_sources(db, p, [selection['original_ref'], selection['copy_ref']])
    if value['preview_hash'] != body.preview_hash:
        fail('preview_changed', '读取任务、完整范围、模型、配置或预算已变化，请重新核对预览；旧记录仍可读取')
    _require_current_map(value)
    if not value['can_start']:
        fail('evidence_budget_blocked', value['start_blocker'])
    row = SetupReportDraft(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, request_id=body.request_id, status='queued')
    job = Job(workspace_id=p.workspace_id, kind=legacy.KIND, idempotency_key='report-evidence:' + body.request_id,
        data={'actor_id': p.user_id, 'report_draft_id': row.id})
    db.add(job); db.flush(); row.data = _new_data(p, row.id, body, value, job.id); _state_size(row.data)
    db.add(row); audit(db, p, 'setup.report_evidence.authorize', row.id,
        {'request_id': body.request_id, 'preview_hash': body.preview_hash, 'map_calls': value['input_summary']['map_call_count']})
    db.commit(); return replay_start(row, p, body)


# Only known, record-local integrity failures are quarantined. Authentication,
# permission, database, and unexpected failures continue to fail the request.
_LIST_INTEGRITY_ERRORS = frozenset({
    'evidence_state_integrity', 'delegated_origin_missing', 'delegated_origin_invalid',
    'delegated_audit_invalid', 'delegated_job_invalid', 'delegated_original_changed',
    'delegated_original_job_changed', 'delegated_original_ledger_changed',
    'delegated_candidate_changed', 'full_state_integrity', 'full_provisional_integrity',
    'not_found',
})


def _list_checked(db, p):
    rows = db.scalars(select(SetupReportDraft).where(SetupReportDraft.workspace_id == p.workspace_id,
        SetupReportDraft.actor_id == p.user_id, SetupReportDraft.data['schema'].as_string() == STATE)
        .order_by(SetupReportDraft.created_at.desc(), SetupReportDraft.id.desc()).limit(101)).all()
    if len(rows) > 100:
        fail('evidence_list_capacity', '草案超过列表容量，记录保留；请按原编号恢复', 413)
    good, quarantined = [], 0
    for row in rows:
        try:
            check_row(row, p)
            if row.request_id.startswith('fl_'):
                from .report_evidence_delegated_origin import verify_link
                verify_link(db, p, row, lock=False)
        except HTTPException as error:
            detail = error.detail
            if (error.status_code not in (404, 409) or not isinstance(detail, dict)
                    or detail.get('code') not in _LIST_INTEGRITY_ERRORS):
                raise
            quarantined += 1
            continue
        good.append(row)
    result = seal({'schema': 'report-evidence-reading-list-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'items': [{'id': row.id, 'request_id': row.request_id, 'status': row.status, 'revision': row.version,
            'material_refs': row.data['preview']['body']['material_refs'], 'name': row.data['preview']['source_snapshot']['original']['name'],
            'created_at': row.created_at.isoformat(), 'can_apply': False, 'can_publish': False} for row in good]}, 'list_hash')
    return result, quarantined


@router.get('/report-evidence-readings')
def list_route(p: ReadP, db: DB):
    result, _ = _list_checked(db, p)
    return result


@router.get('/report-evidence-readings/list-v2')
def list_v2_route(p: ReadP, db: DB):
    # One scoped snapshot gives the UI the original exact v1 DTO and a count;
    # no quarantined identifiers, source, request, or action links are exposed.
    result, quarantined = _list_checked(db, p)
    return seal({'schema': 'report-evidence-reading-list-v2', 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'list': result, 'quarantined_count': quarantined}, 'list_v2_hash')


@router.get('/report-evidence-readings/by-request/{request_id}')
def by_request_route(request_id: str, p: ReadP, db: DB):
    row = check_row(legacy.by_nonce(db, p, request_id), p)
    if row.request_id.startswith('fl_'):
        from .report_evidence_delegated_origin import verify_link
        verify_link(db, p, row, lock=False)
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(row.data['start_receipt'])}


@router.get('/report-evidence-readings/{identity}')
def get_route(identity: UUID, p: ReadP, db: DB):
    return draft_view(owned(db, p, identity), p)


@router.get('/report-evidence-readings/{identity}/status')
def status_route(identity: UUID, p: ReadP, db: DB):
    row = owned(db, p, identity)
    return seal({'schema': 'report-evidence-reading-status-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'draft_id': row.id, 'revision': row.version, 'status': row.status, 'phase': row.data['phase'],
        'maps': [{key: value for key, value in _stage_view(row, stage['chunk_id']).items() if key != 'result'} for stage in row.data['maps']],
        'reduction': _stage_view(row, 'reduce'), 'error': row.data['error'], 'error_code': row.data['error_code'],
        'can_apply': False, 'can_publish': False}, 'status_hash')


@router.post('/report-evidence-readings/{identity}/reduce-preview')
def reduce_preview_route(identity: UUID, body: ReducePreviewCommand, p: P, db: DB):
    require(p, 'admin', 'operator'); row = owned(db, p, identity)
    _fresh_cap(db, p, row)
    value = reduce_preview(row, p, body, can_start=not row.request_id.startswith('fl_')); summary = value['reduction_preparation']['input_summary']
    if value['can_start'] and not budget_available(db, p, summary['token_bound'], 1):
        value = reduce_preview(row, p, body, can_start=False)
    db.rollback(); return value


@router.post('/report-evidence-readings/{identity}/reduce', status_code=202)
def reduce_start_route(identity: UUID, body: ReduceCommand, p: P, db: DB):
    row = owned(db, p, identity); prior = row.data['reduction']['start_receipt']
    if prior:
        if prior['request_id'] != body.request_id or prior['command_hash'] != command_hash(p, body, kind='report_evidence_reduce', draft_id=row.id):
            fail('nonce_conflict', '本稿已有汇总请求，请恢复原号；不会重发')
        return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(prior)}
    if row.request_id.startswith('fl_'):
        fail('delegated_reduce_authorization_unavailable', '本次汇总尚无独立额度授权；原分段保留，未创建任务或发送。')
    require(p, 'admin', 'operator'); legacy.workspace_draft(db, p); row = owned(db, p, identity, True)
    if row.data['reduction']['start_receipt']:
        return reduce_start_route(identity, body, p, db)
    _other_paid(db, p, row.id); _fresh_cap(db, p, row)
    value = reduce_preview(row, p, ReducePreviewCommand(revision=body.revision, request_id=body.request_id))
    if not budget_available(db, p, value['reduction_preparation']['input_summary']['token_bound'], 1):
        fail('evidence_budget_blocked', '汇总预算不足，全部原声明保留；未发送')
    if body.preview_hash != value['preview_hash']:
        fail('preview_changed', '汇总输入或消息已变化，请重新预览')
    receipt = _reduce_receipt(row, p, body, value)
    job = Job(workspace_id=p.workspace_id, kind=legacy.KIND, idempotency_key='evidence-reduce:' + body.request_id,
        data={'actor_id': p.user_id, 'report_draft_id': row.id})
    db.add(job); db.flush(); row.status = 'queued'
    _set(row, {**row.data, 'phase': 'reduce', 'job_id': job.id,
        'reduction': _empty_stage('queued', preview=value, start_receipt=receipt)})
    audit(db, p, 'setup.report_evidence.reduce_authorize', row.id, {'request_id': body.request_id, 'preview_hash': body.preview_hash})
    db.commit(); return {'draft': draft_view(row, p), 'receipt': receipt}


@router.get('/report-evidence-readings/{identity}/reduce-receipts/{request_id}')
def reduce_receipt_route(identity: UUID, request_id: str, p: ReadP, db: DB):
    row = owned(db, p, identity); receipt = row.data['reduction']['start_receipt']
    if not receipt or receipt['request_id'] != request_id:
        fail('receipt_not_found', '暂未找到本人原汇总回执', 404)
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(receipt)}


def record_review(row, p, body):
    _block_v4_reduce(row.data['preview'])
    check_row(row, p); signature = command_hash(p, body, kind='report_evidence_review', draft_id=row.id)
    prior = next((entry for entry in row.data['review_history'] if entry['receipt']['request_id'] == str(body.request_id)), None)
    if prior:
        if prior['receipt']['command_hash'] != signature:
            fail('nonce_conflict', '原人工稿编号已用于另一保存，原稿保留')
        return prior['receipt']
    require(p, 'admin', 'operator')
    if _family(row.data['preview']) is not v5:
        fail('evidence_review_family', '此报告使用新的对象语义，请在独立结构核对中保存；原机器回答保持不变')
    if row.status not in {'review', 'needs_clarification'} or body.revision != row.version:
        fail('evidence_review_conflict', '草案尚未形成或另一页面已有新稿，当前输入与服务器原稿均保留')
    catalog = _catalog(row.data['preview'])
    try:
        if v3.applies(row.data['preview']) or _family(row.data['preview']).applies(row.data['preview']):
            value = body.value.model_dump(mode='json')
            v3.manual_projection(sys.modules[__name__], row, p, body, value, catalog)
        else:
            result = validate_evidence_result(body.value.result, catalog, list(catalog['index']), row.data['preview']['map_preparation']['context'])
            if result != body.value.result:
                raise ValueError('Original changed')
            value = {'result': result, 'note': body.value.note}
    except ValueError:
        fail('evidence_review_invalid', '人工稿结构或所选来源引用无法核对，原稿保留；未保存此次修改', 422)
    receipt = _review_receipt(row, p, body, value)
    _set(row, {**row.data, 'review_value': value,
        'review_history': [*row.data['review_history'], {'value': copy.deepcopy(value), 'receipt': receipt}]})
    return receipt


def review_reply(row, p, receipt):
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(receipt),
        'current_matches': row.version == receipt['accepted_revision'] and digest(row.data['review_value']) == receipt['value_hash']}


@router.put('/report-evidence-readings/{identity}/review')
def review_route(identity: UUID, body: ReviewCommand, p: P, db: DB):
    row = owned(db, p, identity, True); before = row.version; receipt = record_review(row, p, body)
    if before != row.version:
        audit(db, p, 'setup.report_evidence.review_save', row.id, {'request_id': str(body.request_id), 'accepted_revision': row.version})
    db.commit(); return review_reply(row, p, receipt)


@router.get('/report-evidence-readings/{identity}/review-receipts/{request_id}')
def review_receipt_route(identity: UUID, request_id: UUID, p: ReadP, db: DB):
    row = owned(db, p, identity)
    receipt = next((entry['receipt'] for entry in row.data['review_history'] if entry['receipt']['request_id'] == str(request_id)), None)
    if receipt is None:
        fail('receipt_not_found', '暂未找到本人原人工稿回执', 404)
    return review_reply(row, p, receipt)


@router.post('/report-evidence-readings/{identity}/recover-saved')
def recover_saved_route(identity: UUID, body: RecoveryCommand, p: P, db: DB):
    row = owned(db, p, identity, True); signature = command_hash(p, body, kind='report_evidence_recovery', draft_id=row.id)
    prior = next((item for item in row.data['recovery_history'] if item['request_id'] == str(body.request_id)), None)
    if prior:
        if prior['command_hash'] != signature:
            fail('nonce_conflict', '原恢复编号已用于另一请求；原答与人工稿保留')
        return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(prior)}
    require(p, 'admin', 'operator')
    if row.status not in {'failed', 'response_unknown'} or row.version != body.revision:
        fail('evidence_recovery_conflict', '仅可免费恢复当前中断稿的原回答，状态或版本已变化')
    if row.request_id.startswith('fl_'):
        from .report_evidence_delegated_recovery_v1 import recover
        return recover(sys.modules[__name__], db, p, row, body)
    if _family(row.data['preview']).native(row.data['preview']):
        return _recover_native(db, p, row, body)
    keys = (['reduce'] if row.data['reduction']['preview'] else
        [stage['chunk_id'] for stage in row.data['maps'] if stage['status'] != 'succeeded'])
    if any(_descriptor(row, key) is None for key in keys):
        fail('evidence_saved_response_incomplete', '未处理批次并非都有完整原回答，原记录保留；不会补发或重复付费')
    accepted = row.version + 1
    try:
        for key in keys:
            _consume(db, p, row, key)
        if row.data['reduction']['preview'] is None:
            _finish_maps(db, p, row)
    except (ValueError, HTTPException):
        db.rollback()
        fail('evidence_saved_response_invalid', '已保存原答仍未通过结构、引用或归并核对；原内容与费用保留，没有调用模型', 422)
    receipt = _recovery_receipt(row, p, body)
    row.version = accepted; row.data = {**row.data, 'recovery_history': [*row.data['recovery_history'], receipt]}; _state_size(row.data)
    audit(db, p, 'setup.report_evidence.saved_response_recovered', row.id,
        {'request_id': str(body.request_id), 'stages': keys, 'new_call': False})
    db.commit(); return {'draft': draft_view(row, p), 'receipt': receipt}


@router.get('/report-evidence-readings/{identity}/recovery-receipts/{request_id}')
def recovery_receipt_route(identity: UUID, request_id: UUID, p: ReadP, db: DB):
    row = owned(db, p, identity)
    receipt = next((item for item in row.data['recovery_history'] if item['request_id'] == str(request_id)), None)
    if receipt is None:
        fail('receipt_not_found', '暂未找到本人原恢复回执', 404)
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(receipt)}


@router.get('/report-evidence-readings/{identity}/captures/{stage}')
def capture_route(identity: UUID, stage: str, p: ReadP, db: DB):
    from . import report_evidence_native_bridge_v1 as bridge
    row = owned(db, p, identity); preview = row.data['preview']
    ids = [item['chunk_id'] for item in row.data['maps']]
    if not _family(preview).native(preview) or stage not in ids + (['reduce'] if row.data['reduction']['preview'] else []):
        fail('not_found', '未找到本人此版本的原HTTP捕获', 404)
    current = _stage(row, stage); spec, auth = _spec(row, stage), _authorized(row, stage)
    policy = copy.deepcopy(spec['input_policy']); policy.pop('single_batch')
    state, eligible, blocker = None, False, 'capture_missing'
    if current['reserved_call_id']:
        try:
            state = bridge.load_verified(sys.modules[__name__], row, stage)
            if state['state'] == 'missing':
                state = None
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            fail('evidence_capture_integrity', '原HTTP捕获完整性不能核对；没有改写、恢复或调用', 409)
        if state is not None:
            blocker = 'capture_incomplete' if not state['ready'] or not state['content_encoding_safe'] else 'response_invalid'
            if state['ready'] and state['content_encoding_safe']:
                try:
                    _native_preflight(row, stage)
                    eligible = current['status'] != 'succeeded' and row.status in {'failed', 'response_unknown'}
                    blocker = None if eligible else 'stage_not_recoverable'
                except (ValueError, OSError, HTTPException):
                    pass
    value = {'schema': 'report-evidence-native-capture-view-v1',
        'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'draft_id': row.id,
        'draft_revision': row.version, 'generation_schema': preview['map_preparation']['generation_contract']['schema'],
        'stage': stage, 'stage_status': current['status'], 'reserved_call_id': current['reserved_call_id'],
        'delivery': spec['delivery'], 'request_hash': auth['request_hash'], 'messages_hash': auth['messages_hash'],
        'source_hash': preview['source_hash'], 'catalog_hash': preview['map_preparation']['catalog_descriptor']['catalog_hash'],
        'profile_hash': digest(policy), 'schema_proof_hash': digest(spec['schema_proof']),
        'token_bound': auth['input_summary']['token_bound'], 'capture_state': 'missing' if state is None else state['state'],
        'response_complete': False if state is None else state['response_complete'],
        'http_status': None if state is None else state['http_status'],
        'http_content_encoding': None if state is None else state['http_content_encoding'],
        'content_encoding_safe': state is not None and state['content_encoding_safe'] is True,
        'headers_hash': None if state is None else state['headers_hash'],
        'body_bytes': 0 if state is None else state['body_bytes'],
        'body_sha256': None if state is None else state['body_sha256'],
        'capture_hash': None if state is None else state['capture_hash'],
        'recovery_eligible': eligible, 'blocker': blocker, 'can_apply': False, 'can_publish': False}
    return seal(value, 'view_hash')


@router.get('/report-evidence-readings/{identity}/capture-body')
def capture_body_route(identity: UUID, stage: str, capture_hash: str, p: ReadP, db: DB):
    from . import report_evidence_native_bridge_v1 as bridge
    from . import report_evidence_http_capture_v1 as capture
    row = owned(db, p, identity)
    ids = [item['chunk_id'] for item in row.data['maps']]
    if (not _family(row.data['preview']).native(row.data['preview']) or stage not in ids + (['reduce'] if row.data['reduction']['preview'] else [])
            or not _stage(row, stage)['reserved_call_id']):
        fail('not_found', '未找到本人原HTTP捕获', 404)
    try:
        raw = capture.read_body(bridge.root(), bridge.context(sys.modules[__name__], row, stage), capture_hash)
    except (ValueError, OSError):
        fail('evidence_capture_integrity', '原HTTP捕获或当前哈希不能核对，未返回其他内容', 409)
    return Response(raw, media_type='application/octet-stream', headers={
        'Content-Disposition': 'attachment; filename="report-evidence-' + stage + '-capture.bin"',
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


@router.post('/report-evidence-readings/{identity}/resume-preview')
def resume_preview_route(identity: UUID, body: ResumePreviewCommand, p: P, db: DB):
    require(p, 'admin', 'operator'); legacy.workspace_draft(db, p); row = owned(db, p, identity, True)
    _other_paid(db, p, row.id); _fresh_cap(db, p, row, map_source=True)
    value = resume_preview(row, p, body, can_start=not row.request_id.startswith('fl_')); summary = value['input_summary']
    if value['can_start'] and not budget_available(db, p, summary['token_bound'], summary['call_count']):
        value = resume_preview(row, p, body, can_start=False)
    db.rollback(); return value


@router.post('/report-evidence-readings/{identity}/resume', status_code=202)
def resume_route(identity: UUID, body: ResumeCommand, p: P, db: DB):
    row = owned(db, p, identity)
    prior = next((entry for entry in row.data['resume_history'] if entry['receipt']['request_id'] == body.request_id), None)
    if prior:
        if prior['receipt']['command_hash'] != command_hash(p, body, kind='report_evidence_resume', draft_id=row.id):
            fail('nonce_conflict', '原继续编号已用于另一请求；不会排新付费任务')
        return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(prior['receipt'])}
    if row.request_id.startswith('fl_'):
        fail('delegated_resume_handoff_unavailable', '原定向额度尚不能安全交接给新任务；已保存结果保留，未创建任务或发送。')
    require(p, 'admin', 'operator'); legacy.workspace_draft(db, p); row = owned(db, p, identity, True)
    if any(entry['receipt']['request_id'] == body.request_id for entry in row.data['resume_history']):
        return resume_route(identity, body, p, db)
    _other_paid(db, p, row.id); _fresh_cap(db, p, row, map_source=True)
    value = resume_preview(row, p, ResumePreviewCommand(revision=body.revision, request_id=body.request_id))
    summary = value['input_summary']
    if not budget_available(db, p, summary['token_bound'], summary['call_count']):
        fail('evidence_budget_blocked', '剩余资料读取预算不足，已保存回答保留；未新增调用')
    if value['preview_hash'] != body.preview_hash:
        fail('preview_changed', '剩余批次、来源或消息已变化，请重新预览')
    for key in value['saved_chunks']:
        _consume(db, p, row, key)
    data = copy.deepcopy(row.data)
    for stage in data['maps']:
        if stage['chunk_id'] in [chunk['chunk_id'] for chunk in value['remaining_chunks']]:
            stage.update(status='pending', error=None, error_code=None)
    receipt = _resume_receipt(row, p, body, value)
    job = Job(workspace_id=p.workspace_id, kind=legacy.KIND, idempotency_key='evidence-resume:' + body.request_id,
        data={'actor_id': p.user_id, 'report_draft_id': row.id})
    db.add(job); db.flush(); data.update(phase='map', error=None, error_code=None, job_id=job.id,
        resume_history=[*data['resume_history'], {'preview': value, 'receipt': receipt}])
    _state_size(data); row.data = data; row.version = body.revision + 1; row.status = 'queued'
    audit(db, p, 'setup.report_evidence.resume_authorize', row.id,
        {'request_id': body.request_id, 'preview_hash': body.preview_hash, 'remaining_calls': summary['call_count']})
    db.commit(); return {'draft': draft_view(row, p), 'receipt': receipt}


@router.get('/report-evidence-readings/{identity}/resume-receipts/{request_id}')
def resume_receipt_route(identity: UUID, request_id: str, p: ReadP, db: DB):
    row = owned(db, p, identity)
    receipt = next((entry['receipt'] for entry in row.data['resume_history'] if entry['receipt']['request_id'] == request_id), None)
    if receipt is None:
        fail('receipt_not_found', '暂未找到本人原继续回执', 404)
    return {'draft': draft_view(row, p), 'receipt': copy.deepcopy(receipt)}


def _request(row, key):
    preview = row.data['preview']; cap = _cap(preview); catalog = _catalog(preview)
    if key == 'reduce':
        _block_v4_reduce(preview)
        if _family(preview).applies(preview):
            return _reducer(preview).rebuild_reduction_request(_authorized(row, key), catalog, cap,
                preview['map_preparation'], _family(row.data['preview']).map_inputs(sys.modules[__name__], row, catalog))
        if v3.applies(preview):
            return rebuild_reduction_request(_authorized(row, key), catalog, cap, preview['map_preparation'],
                map_inputs=v3.complete_map_inputs(sys.modules[__name__], row, catalog))
        return rebuild_reduction_request(_authorized(row, key), catalog, cap, preview['map_preparation'], _map_results(row, catalog))
    return rebuild_map_request(preview['map_preparation'], catalog, cap, key)


def _expected(row, key):
    preview = row.data['preview']
    authorized = _authorized(row, key)
    return {'workspace_id': row.workspace_id, 'capability_id': preview['body']['model_id'], 'actor_id': row.actor_id,
        'request_hash': authorized['request_hash'] if v4.applies(preview) or _family(preview).applies(preview) else authorized['messages_hash'],
        'dispatch_options_hash': digest(_spec(row, key)['dispatch_options']),
        'provider': preview['provider'], 'model': preview['model']}


def _call_guard(db, p, row, key, call_id, *, lock=True):
    call = scoped(db, CloudCall, call_id, p, lock); expected = _expected(row, key)
    if (call.capability_id != expected['capability_id'] or call.reserved_tokens != _authorized(row, key)['input_summary']['token_bound']
            or any(call.data.get(field) != expected[field] for field in ('actor_id', 'request_hash', 'dispatch_options_hash'))
            or call.data.get('input_policy_hash') != digest(_spec(row, key)['input_policy'])):
        fail('evidence_reservation_changed', '真实调用预留与冻结阶段不一致；原记录保留，不会重发')
    if v4.applies(row.data['preview']) or _family(row.data['preview']).applies(row.data['preview']):
        spec = _spec(row, key)
        strict = {'delivery': spec['delivery'], 'request_endpoint': spec['endpoint'],
            'request_body_bytes': _authorized(row, key)['input_summary']['prepared_request_bytes'],
            'schema_proof_hash': digest(spec['schema_proof'])}
        if any(call.data.get(field) != value for field, value in strict.items()):
            fail('evidence_reservation_changed', '严格Map原请求或交付证明与真实预留不一致；未发送或重发')
    return call


def _settle(db, p, row, key, receipt, valid):
    from .cloud_budget import settle_paid_response
    call_id = _stage(row, key)['reserved_call_id']; _call_guard(db, p, row, key, call_id)
    settle_paid_response(db, call_id, legacy.safe_paid_receipt(receipt), _expected(row, key), 'succeeded' if valid else 'failed')


def _delegated_reduce_usage_valid(row, key, receipt):
    if not row.request_id.startswith('fl_') or key != 'reduce':
        return True
    usage = receipt.get('usage') if isinstance(receipt, dict) else None
    total = usage.get('total_tokens') if isinstance(usage, dict) else None
    bound = _authorized(row, key)['input_summary']['token_bound']
    return type(total) is int and 0 <= total <= bound


def _schema_valid(row, key, descriptor):
    from .services import validate
    try:
        if _family(row.data['preview']).applies(row.data['preview']):
            loaded = _journal_for(row, key).read_response(_stage_journal_root(row, key),
                journal_binding(row, key, _stage(row, key)['reserved_call_id']), descriptor)
            _check_receipt_identity(row, key, loaded['receipt'])
            response = _family(row.data['preview']).parsed_response(sys.modules[__name__], row, key, loaded, descriptor)
        elif v4.applies(row.data['preview']):
            loaded = journal.read_response(_journal_root(), journal_binding(row, key, _stage(row, key)['reserved_call_id']), descriptor)
            _check_receipt_identity(row, key, loaded['receipt'])
            response = v4.parsed_response(sys.modules[__name__], row, key, loaded, descriptor)
        else:
            response = journal.parse_response(_journal_root(), journal_binding(row, key, _stage(row, key)['reserved_call_id']), descriptor)
        if not _delegated_reduce_usage_valid(row,key,response['receipt']):
            return False
        if v3.applies(row.data['preview']):
            v3.check_response(sys.modules[__name__], row, key, response, descriptor)
        validate(_spec(row, key)['output_schema'], response['value'])
        return True
    except (ValueError, HTTPException):
        return False


def _consume(db, p, row, key):
    stage = _stage(row, key); descriptor = _descriptor(row, key)
    if descriptor is None:
        fail('evidence_saved_response_incomplete', '原调用尚无完整已保存回答，原编号保留；不会自动重付')
    response = _parsed(row, key); _settle(db, p, row, key, response['receipt'], _schema_valid(row, key, descriptor))
    if not _delegated_reduce_usage_valid(row,key,response['receipt']):
        fail('delegated_reduce_usage_unverified', '汇总原答与费用回执已保留，但用量缺失或超出授权上限；未采用，不会自动重发。')
    preview = row.data['preview']; catalog = _catalog(preview)
    bounded = _family(preview).applies(preview); strict = v4.applies(preview); modern = bounded or strict or v3.applies(preview)
    if key == 'reduce':
        _block_v4_reduce(preview)
        prepared = _authorized(row, key)
        value = (_family(row.data['preview']).stage_projection(sys.modules[__name__], row, key, catalog) if bounded else
            v3.stage_projection(sys.modules[__name__], row, key, catalog) if modern else
            validate_evidence_merge(response['value'], catalog, [item[0] for item in prepared['payload']['spans']],
                preview['map_preparation']['context'], prepared['payload']['input_items']))
        count = sum(len(value['result'][kind]) for kind in ('global_claims', 'objects', 'relationships', 'businesses', 'questions'))
        complete = any(business['steps'] for business in value['result']['businesses'])
        row.status = 'review' if complete else 'needs_clarification'
        fields = {'phase': 'review', 'model_result_hash': digest(value['result']),
            'error': None if complete else '汇总已保存，但业务流程尚待明确；全局声明及问题均保留。',
            'error_code': None if complete else 'needs_clarification'}
    else:
        value = (_family(row.data['preview']).stage_projection(sys.modules[__name__], row, key, catalog) if bounded else
            v4.stage_projection(sys.modules[__name__], row, key, catalog) if strict else
            v3.stage_projection(sys.modules[__name__], row, key, catalog) if modern else
            validate_map_result(response['value'], catalog, _authorized(row, key)['span_refs']))
        count = len(value['claims']); fields = {}
    if not modern and value != response['value']:
        fail('evidence_original_changed', '原模型声明在校验时发生改变，原答保留；未采用', 422)
    _replace_stage(row, key, {**stage, 'status': 'succeeded', 'journal': descriptor, 'result_hash': digest(value),
        'result_count': count, 'inflight': False, 'error': None, 'error_code': None}, **fields)
    audit(db, p, 'setup.report_evidence.stage_saved', row.id, {'stage': key, 'result_hash': digest(value), 'result_count': count})
    checkpoint(db)


def _finish_maps(db, p, row):
    results = _map_results(row)
    if set(results) != {stage['chunk_id'] for stage in row.data['maps']}:
        fail('evidence_maps_incomplete', '原分段尚未全部保存，未进入汇总')
    has_claims = any(value['claims'] for value in results.values())
    row.status = 'awaiting_merge' if has_claims else 'needs_clarification'
    _set(row, {**row.data, 'phase': 'awaiting_reduce' if has_claims else 'needs_clarification',
        'error': None if has_claims else '全部原分段未形成待核声明；请核对来源或业务要求，没有新增汇总调用。',
        'error_code': None if has_claims else 'needs_clarification'})
    audit(db, p, 'setup.report_evidence.maps_finished', row.id, {'claim_count': sum(len(value['claims']) for value in results.values()),
        'new_reduce_call': False})
    checkpoint(db)


def _run_stage(db, p, row, key):
    stage = _stage(row, key)
    if stage['status'] == 'succeeded':
        return
    if _descriptor(row, key) is not None:
        _consume(db, p, row, key); return
    if stage['reserved_call_id'] or stage['inflight']:
        fail('evidence_response_unknown', '原阶段可能已发送但尚无完整回答；原编号保留，禁止自动重付')
    if row.request_id.startswith('fl_'):
        from .report_evidence_delegated_dispatch_gate import ready
        if not ready():
            from . import report_evidence_scoped_activation as scope
            from .leases import fenced,ownership
            from sqlalchemy.orm import Session
            if not isinstance(db,Session) or not fenced(db):
                fail('delegated_dispatch_not_ready', '分段读取的定向派发尚未接通；没有预留或发送。')
            job=db.get(Job,ownership(db).get('job_id'))
            scope.for_worker(db,p,job,lock=False)
    cap = _fresh_cap(db, p, row, map_source=key != 'reduce'); request = _request(row, key); spec = _spec(row, key)
    schema = legacy.canonical_schema(spec['output_schema'])
    if _family(row.data['preview']).applies(row.data['preview']):
        from .cloud_evidence_profiles import evidence_options
        from .gateway import cloud_json_options
        prepare_request = _family(row.data['preview']).tool(row.data['preview']).prepare_request
        actual, _, images = prepare_cloud_json(cap, request['messages'][1:], schema)
        strict_request = prepare_request(actual, schema,
            evidence_options(cap.data['execution'], cloud_json_options(cap.data['execution']), spec['evidence_stage']),
            spec['evidence_stage'])
        if (images or strict_request['request'] != request['request'] or strict_request['body'] != request['request_body']
                or strict_request['body_sha256'] != _authorized(row, key)['request_hash']
                or digest(strict_request['request']['messages']) != _authorized(row, key)['messages_hash']):
            fail('evidence_dispatch_changed', '严格报告实际完整请求与冻结消息、来源或工具不同，未发送')
    elif v4.applies(row.data['preview']):
        from .cloud_evidence_profiles import evidence_options
        from .gateway import cloud_json_options
        from .report_evidence_strict_map import prepare_strict_map_request
        actual, _, images = prepare_cloud_json(cap, request['messages'][1:], schema)
        strict_request = prepare_strict_map_request(actual, schema,
            evidence_options(cap.data['execution'], cloud_json_options(cap.data['execution']), 'map-v4'))
        if (images or strict_request['request'] != request['request'] or strict_request['body'] != request['request_body']
                or strict_request['body_sha256'] != _authorized(row, key)['request_hash']
                or digest(strict_request['request']['messages']) != _authorized(row, key)['messages_hash']):
            fail('evidence_dispatch_changed', '严格Map实际完整请求与冻结消息、来源或工具不同，未发送')
    else:
        actual, _, images = prepare_cloud_json(cap, request['messages'][1:], schema)
        if images or actual != request['messages'] or digest(actual) != _authorized(row, key)['messages_hash']:
            fail('evidence_dispatch_changed', '实际已发布模型改变了冻结消息或来源，未发送')
    native = _family(row.data['preview']).native(row.data['preview'])
    delegated_wire = row.request_id.startswith('fl_')
    if delegated_wire and (native or not v3.applies(row.data['preview'])):
        fail('evidence_delegated_wire_unsupported', '受托汇总或读取尚无匹配的定向额度与原包协议；未发送')
    row.status = 'running'; _replace_stage(row, key, {**stage, 'status': 'running', 'inflight': True})
    identity = row.id; known_binding = None; native_context = None; delegated_context = None
    native_provider, native_model = row.data['preview']['provider'], row.data['preview']['model']
    native_policy = copy.deepcopy(spec['input_policy']); native_policy.pop('single_batch', None)
    response_journal, response_root = _journal_for(row, key), _stage_journal_root(row, key)
    checkpoint(db)

    prebound = None
    if delegated_wire:
        from .cloud_scoped_extra_reservation import ScopedReservationDenied, permit_key, reserve_scoped_tx
        from .leases import ownership
        from .report_evidence_prebound_gateway import PreboundGatewayCall
        worker_job = ownership(db).get('job_id')
        try:
            permit = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
                Job.idempotency_key == permit_key(worker_job, key)))
            if permit is None:
                raise ScopedReservationDenied('受托阶段缺少定向额度；没有新增调用')
            call_id = reserve_scoped_tx(db, p, permit.id, cap.id,
                _authorized(row, key)['input_summary']['token_bound'],
                digest(actual), digest(spec['dispatch_options']))
            checkpoint(db)
        except ScopedReservationDenied:
            # The reservation transaction did not commit. Restore only this
            # artificial inflight marker; no CloudCall or permit is fabricated.
            db.rollback()
            current = owned(db, p, identity, True); pending = _stage(current, key)
            if pending['inflight'] is True and pending['reserved_call_id'] is None and pending['journal'] is None:
                _replace_stage(current, key, {**pending, 'inflight': False})
                checkpoint(db)
            fail('evidence_scoped_budget_denied', '该阶段定向额度或任务状态无法核对；没有新增调用')
        prebound = PreboundGatewayCall(db, p, cap.id, key, call_id)

    def reserved(call_id):
        nonlocal known_binding, native_context, delegated_context
        try:
            db.expire_all(); legacy.workspace_draft(db, p); current = owned(db, p, identity, True)
            call = _call_guard(db, p, current, key, call_id)
            if call.status != 'reserved':
                fail('evidence_reservation_changed', '真实调用不是新预留；未发送')
            if delegated_wire:
                if (_stage(current, key)['reserved_call_id'] != call_id
                        or _stage(current, key)['inflight'] is not True):
                    fail('evidence_reservation_changed', '原阶段与定向调用不一致；未发送')
            else:
                _replace_stage(current, key, {**_stage(current, key), 'reserved_call_id': call_id})
            known_binding = journal_binding(current, key, call_id)
            checkpoint(db)
            # A real reservation is no longer an unstarted stage even if this
            # second fresh permission/source check refuses the HTTP dispatch.
            db.expire_all(); legacy.workspace_draft(db, p); current = owned(db, p, identity, True)
            _fresh_cap(db, p, current, map_source=key != 'reduce'); checkpoint(db)
            if native:
                from . import report_evidence_native_bridge_v1 as bridge
                from . import report_evidence_http_capture_v1 as capture
                native_context = bridge.context(sys.modules[__name__], current, key)
                return capture.prepare(bridge.root(), native_context)
            if delegated_wire:
                from . import report_evidence_delegated_wire_v1 as delegated_bridge
                delegated_context = delegated_bridge.context(sys.modules[__name__], current, key)
                # The dedicated prebound gateway creates the sink only after
                # rechecking the committed reservation and actual messages.
                return None
        except BaseException:
            # The gateway independently finishes a refused reservation before
            # returning to run(). Release our CloudCall lock first; the earlier
            # inflight checkpoint remains durable and cannot become unstarted.
            db.rollback(); raise

    def received(content, receipt):
        # Never touch an ORM attribute or begin a DB/lease transaction before
        # the original callback response has been fully fsynced on disk.
        if known_binding is None:
            fail('evidence_reservation_missing', '缺少已知原预留编号，未采用回答；不会重付')
        if native:
            from . import report_evidence_native_bridge_v1 as bridge
            original, original_receipt, _ = bridge.response_context(native_context,
                native_provider, native_model, native_policy, spec['evidence_stage'])
            if content != original or receipt != original_receipt:
                raise ValueError('Native callback differs from durable capture')
            descriptor = bridge.write_missing_journal(response_journal, response_root, known_binding, content, receipt)
        else:
            if delegated_wire:
                from . import report_evidence_delegated_wire_v1 as delegated_bridge
                original, original_receipt, _ = delegated_bridge.response_context(
                    delegated_context, native_provider, native_model, native_policy)
                if content != original or receipt != original_receipt:
                    raise ValueError('受托回调与耐久原 HTTP 回答不一致')
            descriptor = response_journal.write_response(response_root, known_binding, content, receipt)
        actor = db.info.pop('worker_actor', None)
        try:
            current = owned(db, p, identity, True)
            if _stage(current, key)['reserved_call_id'] != known_binding['reserved_call_id']:
                fail('evidence_reservation_changed', '原预留编号已变化，磁盘原答保留；未覆盖')
            _replace_stage(current, key, {**_stage(current, key), 'journal': descriptor})
            _settle(db, p, current, key, receipt, _schema_valid(current, key, descriptor))
            checkpoint(db)
        except BaseException:
            db.rollback(); raise
        finally:
            if actor is not None:
                db.info['worker_actor'] = actor

    profile_options = {'evidence_stage': spec['evidence_stage']} if v3.applies(row.data['preview']) or v4.applies(row.data['preview']) or _family(row.data['preview']).applies(row.data['preview']) else {}
    if delegated_wire:
        profile_options['delegated_evidence_wire_guard'] = True
        profile_options['delegated_prebound'] = prebound
    returned, _ = cloud_json(cap, request['messages'][1:], schema, p, full_report=True,
        on_reserved=reserved, on_response=received, **profile_options)
    row = owned(db, p, identity, True); original = _parsed(row, key)
    if digest(returned) != digest(original['value']):
        fail('evidence_provider_projection_changed', '网关返回值与原已保存回答不同，原答保留；未采用', 422)
    _consume(db, p, row, key)


def _stop(db, p, row, key, code, message):
    stage = _stage(row, key); descriptor = _descriptor(row, key)
    unknown = descriptor is None and bool(stage['reserved_call_id'] or stage['inflight'])
    row.status = 'response_unknown' if unknown else 'failed'
    if unknown:
        code, message = 'evidence_response_unknown', '原阶段可能已发送，尚无完整回答；原编号保留，禁止自动重付。'
    _replace_stage(row, key, {**stage, 'status': row.status, 'error': message, 'error_code': code},
        phase=row.status, error=message, error_code=code)
    audit(db, p, 'setup.report_evidence.stopped', row.id, {'stage': key, 'response_unknown': unknown,
        'raw_response_saved': descriptor is not None})


def record_job_failure(db, p, job, reason):
    # Legacy failure dispatch already holds this candidate. This path only
    # records saved outcomes; it must not acquire a workspace lock afterwards.
    row = owned(db, p, job.data['report_draft_id'], True)
    if (job.kind != legacy.KIND or job.workspace_id != row.workspace_id or job.id != row.data['job_id']
            or job.data.get('actor_id') != row.actor_id):
        return  # A superseded/foreign Job cannot stop a later explicit resume.
    if row.status not in {'queued', 'running', 'response_unknown'}:
        return
    if row.data['reduction']['preview'] is None and all(stage['status'] == 'succeeded' for stage in row.data['maps']):
        _finish_maps(db, p, row); return
    key = 'reduce' if row.data['reduction']['preview'] is not None else next(
        stage['chunk_id'] for stage in row.data['maps'] if stage['status'] != 'succeeded')
    _stop(db, p, row, key, 'evidence_worker_stopped', '后台任务已停止，所有原答与人工稿保留；未自动继续付费。')


def _reclaimed_job(db, p, row):
    lease = db.info.get('worker_lease')
    if not lease:
        return None
    # attach() checks and locks this exact token using DB time before every
    # transaction/commit. Refresh its metadata, never infer recovery from a
    # previous executor merely raising LeaseLost.
    job = db.scalar(select(Job).where(Job.id == lease['job_id'], Job.workspace_id == p.workspace_id)
        .execution_options(populate_existing=True))
    if (job is None or job.kind != legacy.KIND or job.id != row.data['job_id']
            or job.status != 'running' or job.lease_token != lease['lease_token']
            or type(job.data) is not dict or job.data.get('actor_id') != row.actor_id
            or job.data.get('report_draft_id') != row.id
            or type(job.data.get('lease_recoveries')) is not int or job.data['lease_recoveries'] < 0):
        fail('evidence_job_scope_changed', '当前后台租约与原草案授权不一致，未继续调用或改写原阶段')
    return job if job.data['lease_recoveries'] > 0 else None


def run(db, p, identity):
    legacy.workspace_draft(db, p); row = owned(db, p, identity, True)
    if row.status not in {'queued', 'running'}:
        return row.status
    reclaimed = _reclaimed_job(db, p, row)
    if reclaimed is not None:
        # The new owned lease only records the interruption. Even a valid raw
        # cannot silently authorize paying for later unstarted Map stages.
        record_job_failure(db, p, reclaimed, 'expired original evidence worker lease')
        checkpoint(db)
        return row.status
    key = 'reduce'
    try:
        if row.data['phase'] == 'map':
            for chunk in row.data['preview']['map_preparation']['plan']['chunks']:
                key = chunk['chunk_id']; legacy.workspace_draft(db, p); row = owned(db, p, identity, True)
                _run_stage(db, p, row, key)
            row = owned(db, p, identity, True); _finish_maps(db, p, row)
        elif row.data['phase'] == 'reduce':
            _run_stage(db, p, row, key)
        else:
            fail('evidence_phase_invalid', '原阶段无法继续，所有原记录保留')
    except (LeaseLost, ExecutionUnauthorized):
        raise
    except Exception as error:
        db.rollback(); legacy.workspace_draft(db, p); row = owned(db, p, identity, True)
        if row.data['reduction']['status'] == 'succeeded':
            return row.status
        if row.data['reduction']['preview'] is None and all(stage['status'] == 'succeeded' for stage in row.data['maps']):
            _finish_maps(db, p, row); return row.status
        detail = legacy.local_failure_detail(error)
        code, message = detail if detail else ('evidence_candidate_invalid', '原阶段的结构、引用或汇总核对未通过；原答与费用保留，不会自动重付。')
        _stop(db, p, row, key, code, message[:1000]); checkpoint(db)
    return owned(db, p, identity).status


# A separate read-only projection; original draft DTOs and write paths stay V1.
from .report_evidence_saved_facts import router as saved_facts_router
router.include_router(saved_facts_router)
