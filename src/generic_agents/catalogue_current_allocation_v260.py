"""Current admission around the unchanged V213 native allocation protocol.

The wrapper is server-side. It is never added to the native evidence or HTTP
payload. History validates frozen references; only new operations requalify the
current configuration, source, role, budget and saved authoring head.
"""
import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, ROUND_CEILING
import threading

from fastapi import HTTPException
from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator
from sqlalchemy import select

from .contracts import Contract, digest
from .services import published, require
from . import catalogue_current_context_store_v260 as current
from . import report_business_catalogue_allocation_source_v213 as source
from . import report_business_catalogue_allocation_plan_v213 as planning
from . import report_business_catalogue_allocation_preparation_v213 as original
from . import report_business_catalogue_allocation_native_v213 as native
from . import report_business_catalogue_allocation_wire_v213 as wire
from . import report_business_catalogue_store_v211 as catalogue
from .report_catalogue_allocation_read_scope_v213 import allocation_read_scope
from .report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer

PROFILE = 'catalogue-current-allocation-v260'
PREPARATION = 'report-business-catalogue-current-allocation-preparation-v260'
PREVIEW = 'report-business-catalogue-current-allocation-preview-v260'
CONTEXT = 'report-business-catalogue-current-allocation-context-v260'
ADMISSION = 'catalogue-current-admission-v260'
SCOPE = 'catalogue-current-scope-v260'
TASK_PREFIX = 'report-business-catalogue-current-allocation-v260:'
ERROR = '当前配置工作副本、本批原生请求或原准入依据无法核对；原稿和原调用保留'
_ADMISSION_KEYS = set(('schema binding_id binding_record_hash source_draft_ref source_pins '
    'target_configuration working_draft_ref admission_hash').split())
_SCOPE_KEYS = set(('schema admission_hash native_request_hash native_plan_hash native_batch_hash '
    'working_draft_ref current_scope_hash').split())
_FIELDS = {'schema', 'native_preparation', 'admission_basis', 'current_scope', 'preparation_hash'}
_SOURCE_PIN_KEYS = set(('producer_id consumer_id source_revision origin_hash source_context_hash '
    'source_snapshot_hash indexed_inputs_hash input_claims_hash input_count materials_hash '
    'map_proofs_hash capture_proof_hash').split())
_DISPATCH = ContextVar('catalogue_current_allocation_dispatch_v260', default=None)
_TOKEN = object()


def _consumer():
    from . import report_saved_map_design_consumer_v176
    return report_saved_map_design_consumer_v176


def need(value, reason=ERROR, status=409):
    if not value:
        raise HTTPException(status, reason)


def same(left, right):
    return catalogue.same(left, right)


def _hash(value):
    return type(value) is str and catalogue.SHA.fullmatch(value) is not None


class Closed(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)


class Selection(Closed):
    mode: str
    positions: list[int] = Field(max_length=1920)

    @model_validator(mode='after')
    def exact_selection(self):
        if (self.mode not in {'unassigned_and_pending', 'explicit'}
                or any(type(v) is not int or not 0 <= v < 1920 for v in self.positions)
                or self.positions != sorted(set(self.positions))
                or (self.mode == 'unassigned_and_pending' and self.positions)
                or (self.mode == 'explicit' and not self.positions)):
            raise ValueError('请保留本批明确、排序去重的资料范围')
        return self


class Review(Closed):
    accepted: bool = Field(strict=True)
    expected_catalogue_review_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def explicit(self):
        if self.accepted is not True:
            raise ValueError('请先核对业务目录和名称依据')
        return self


class PreviewBody(Closed):
    generation_profile: str
    activity_id: StrictStr
    draft_id: StrictStr
    expected_draft_version: int = Field(strict=True, ge=1)
    expected_document_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    model_id: StrictStr
    selection: Selection
    batch_ordinal: int = Field(strict=True, ge=0, lt=1920)
    max_batch_items: int = Field(strict=True, ge=1, le=80)
    catalogue_review: Review
    note: StrictStr = Field(max_length=2000)
    binding_id: StrictStr
    binding_record_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    expected_configuration_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')

    @field_validator('generation_profile')
    @classmethod
    def profile(cls, value):
        if value != PROFILE:
            raise ValueError('本申请必须使用当前配置工作副本归类版本')
        return value

    @field_validator('activity_id', 'draft_id', 'model_id', 'binding_id')
    @classmethod
    def identities(cls, value):
        return catalogue.identity(value)

    @field_validator('note')
    @classmethod
    def unicode(cls, value):
        wire.codec.canonical(value)
        if '\x00' in value:
            raise ValueError('本次要求不能包含空字符')
        return value


class StartBody(PreviewBody):
    request_id: StrictStr
    preview_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    request_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    plan_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    batch_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    current_scope_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    consent: bool = Field(strict=True)

    @field_validator('request_id')
    @classmethod
    def nonce(cls, value):
        return catalogue.identity(value)

    @model_validator(mode='after')
    def original_activity(self):
        if self.request_id != self.activity_id or self.consent is not True:
            raise ValueError('请保留原活动编号并明确授权这一个新调用')
        return self


def check_admission(value):
    from .catalogue_current_context_contracts_v260 import DraftRef, ConfigurationBasis
    need(type(value) is dict and set(value) == _ADMISSION_KEYS and value['schema'] == ADMISSION)
    catalogue.identity(value['binding_id']); need(_hash(value['binding_record_hash']))
    source_ref = DraftRef.model_validate(value['source_draft_ref']).model_dump()
    working = DraftRef.model_validate(value['working_draft_ref']).model_dump()
    target = ConfigurationBasis.model_validate(value['target_configuration']).model_dump()
    need(same(source_ref, value['source_draft_ref']) and same(working, value['working_draft_ref'])
        and same(target, value['target_configuration'])
        and source_ref['workspace_id'] == working['workspace_id']
        and source_ref['actor_id'] == working['actor_id']
        and source_ref['origin_hash'] == working['origin_hash'])
    pins = value['source_pins']
    need(type(pins) is dict and set(pins) == _SOURCE_PIN_KEYS)
    for key in ('producer_id', 'consumer_id'):
        catalogue.identity(pins[key])
    need(type(pins['source_revision']) is int and pins['source_revision'] >= 1
        and type(pins['input_count']) is int and pins['input_count'] >= 1
        and pins['origin_hash'] == working['origin_hash'])
    need(all(_hash(v) for k, v in pins.items() if k not in {'producer_id', 'consumer_id', 'source_revision', 'input_count'}))
    need(_hash(value['admission_hash'])
        and value['admission_hash'] == digest({k: v for k, v in value.items() if k != 'admission_hash'}))
    return copy.deepcopy(value)


def check_scope(value, admission, prep):
    admission = check_admission(admission)
    need(type(value) is dict and set(value) == _SCOPE_KEYS and value['schema'] == SCOPE
        and value['admission_hash'] == admission['admission_hash']
        and same(value['working_draft_ref'], admission['working_draft_ref'])
        and value['native_request_hash'] == prep['request_hash']
        and value['native_plan_hash'] == prep['plan_hash']
        and value['native_batch_hash'] == prep['batch_hash']
        and value['current_scope_hash'] == digest({k: v for k, v in value.items() if k != 'current_scope_hash'}))
    need(all(_hash(value[k]) for k in ('admission_hash', 'native_request_hash', 'native_plan_hash',
        'native_batch_hash', 'current_scope_hash')))
    return copy.deepcopy(value)


def _wrap(prep, admission):
    admission = check_admission(admission)
    scope = {'schema': SCOPE, 'admission_hash': admission['admission_hash'],
        'native_request_hash': prep['request_hash'], 'native_plan_hash': prep['plan_hash'],
        'native_batch_hash': prep['batch_hash'], 'working_draft_ref': copy.deepcopy(admission['working_draft_ref'])}
    scope['current_scope_hash'] = digest(scope)
    value = {'schema': PREPARATION, 'native_preparation': copy.deepcopy(prep),
        'admission_basis': admission, 'current_scope': scope}
    return {**value, 'preparation_hash': digest(value)}


def native_preparation(value):
    need(type(value) is dict and set(value) == _FIELDS and value['schema'] == PREPARATION
        and value['preparation_hash'] == digest({k: v for k, v in value.items() if k != 'preparation_hash'}))
    prep = value['native_preparation']; original.stage_spec(prep)
    admission = check_admission(value['admission_basis']); check_scope(value['current_scope'], admission, prep)
    evidence = native.check_allocation_evidence(prep['allocation_evidence'])
    need(same(admission['working_draft_ref'], evidence['draft_ref'])
        and admission['source_pins']['origin_hash'] == digest(evidence['draft_origin'])
        and admission['source_pins']['source_revision'] == evidence['draft_origin']['metadata']['revision']
        and admission['source_pins']['producer_id'] == prep['producer_id']
        and admission['source_pins']['source_snapshot_hash'] == prep['source_snapshot_hash'])
    return copy.deepcopy(prep)


def build_preparation(cap, source_context, *, admission_basis, **arguments):
    return _wrap(original.build_preparation(cap, source_context, **arguments), admission_basis)


def check_preparation(value, cap, source_context, **arguments):
    prep = native_preparation(value)
    original.check_preparation(prep, cap, source_context, **arguments)
    return copy.deepcopy(value)


def rebuild_request(value, cap, source_context, **arguments):
    checked = check_preparation(value, cap, source_context, **arguments)
    return original.rebuild_request(checked['native_preparation'], cap, source_context, **arguments)


def stage_spec(value):
    return original.stage_spec(native_preparation(value))


def _binding(db, p, binding_id):
    catalogue.identity(binding_id)
    drafts, children, bindings, commands = current._inventory(db, p)
    binding = bindings.get(binding_id)
    need(binding is not None, '没有当前账号的工作副本绑定', 404)
    row = catalogue._owned(drafts, binding.working_draft_id)
    return binding, row


def context(db, p, binding_id):
    catalogue.plans._iam(db, p); source.lock_before_consumer(db, p)
    binding, row = _binding(db, p, binding_id)
    _, _, _, reference, document, _, validation, review_hash = source._saved(db, p, row.id)
    _, target = current._basis(db, p)
    reason = (None if p.role in {'admin', 'operator'} else '当前账号只读，原人稿和原活动保留')
    if not current.can_edit(db, p, row): reason = '已确认工作副本保持只读'
    if not same(target, binding.data['target_configuration']): reason = '当前配置已变化；请保留人稿并明确另建当前配置副本'
    if not validation['catalogue_structurally_ready']: reason = '请先核对业务名称及名称依据'
    return {'schema': CONTEXT, 'binding_id': binding.id, 'binding_record_hash': binding.record_hash,
        'draft_ref': reference, 'producer_id': row.data['origin']['metadata']['producer_id'],
        'source_revision': row.data['origin']['metadata']['revision'],
        'target_configuration': copy.deepcopy(binding.data['target_configuration']),
        'catalogue_review_hash': review_hash, 'businesses': copy.deepcopy(document['businesses']),
        'validation': validation, 'default_target_positions': sorted(set(validation['unassigned_positions'])
            | set(validation['pending_ownership_positions'])), 'permissions': {'can_request_preview': reason is None},
        'reason': reason, 'current_source_verified': False, 'actual_paid_verified': False,
        'can_dispatch': False, 'can_apply': False, 'can_confirm': False}


def prepare_source(db, p, body, *, lock=False, current_consumer_id=None):
    try:
        return _prepare_source(db, p, body, lock=lock, current_consumer_id=current_consumer_id)
    except HTTPException:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, ERROR) from None


def _prepare_source(db, p, body, *, lock=False, current_consumer_id=None):
    require(p, 'admin', 'operator'); catalogue.plans._iam(db, p, write=True)
    source.lock_before_consumer(db, p, write=lock)
    binding, row = _binding(db, p, body.binding_id)
    need(binding.record_hash == body.binding_record_hash and row.id == body.draft_id)
    need(current.can_edit(db, p, row) and row.data['plan_ref'] is None, '已确认工作副本保持只读')
    _, target = current._basis(db, p, lock=lock)
    need(same(target, binding.data['target_configuration'])
        and target['configuration_hash'] == body.expected_configuration_hash,
        '当前配置已变化；原活动及人稿保留，未继续分派')
    _, origin, claims, reference, document, name_origin, validation, review_hash = source._saved(db, p, row.id)
    need(reference['version'] == body.expected_draft_version
        and reference['document_hash'] == body.expected_document_hash
        and review_hash == body.catalogue_review.expected_catalogue_review_hash,
        '工作副本人稿已变化；原输入保留，请重新核对本批范围')
    need(validation['catalogue_structurally_ready'], '请先核对业务名称及名称依据')
    fresh = catalogue._load_origin(db, p, row.consumer_id, origin_mode=catalogue._mode(origin))
    need(same(catalogue._origin_content(fresh), catalogue._origin_content(origin)))
    actual = current.source.load_source_context(db, p, origin['metadata']['producer_id'],
        lock=lock, current_consumer_id=current_consumer_id)
    need(same(actual, origin['source_context']), '原来源、材料或当前权限已变化；原活动和人稿保留')
    pins = current._pins(row, p)
    need(same(pins, binding.data['source_pins']))
    admission = {'schema': ADMISSION, 'binding_id': binding.id, 'binding_record_hash': binding.record_hash,
        'source_draft_ref': copy.deepcopy(binding.data['source_draft_ref']), 'source_pins': pins,
        'target_configuration': target, 'working_draft_ref': reference}
    admission['admission_hash'] = digest(admission)
    snapshot = origin['source_context']['snapshot']
    plan = planning.build_plan(indexed=origin['indexed_inputs'], input_claims=claims,
        source_snapshot_hash=snapshot['snapshot_hash'], catalog=origin['source_context']['catalog'],
        input_order=snapshot['input_order'], draft_ref=reference, document=document,
        selection=body.selection.model_dump(), max_batch_items=body.max_batch_items, name_origin=name_origin)
    need(plan['catalogue_review_hash'] == review_hash)
    return {'producer_id': origin['metadata']['producer_id'], 'revision': target['revision'],
        'draft_ref': reference, 'document': document, 'draft_origin': copy.deepcopy(origin),
        'source_context': copy.deepcopy(origin['source_context']), 'name_origin': copy.deepcopy(name_origin),
        'plan': plan, 'catalogue_review_hash': review_hash, 'admission_basis': check_admission(admission),
        'current_source_verified': True, 'actual_paid_verified': False}


def admit(db, p, body, *, lock=False, consumer=None):
    from . import report_saved_map_design_runtime_v176 as runtime
    c = _consumer(); need(isinstance(body, PreviewBody))
    current_id = None
    if consumer is not None:
        pinned = PreviewBody.model_validate(consumer.data['preview']['body']).model_dump()
        need(consumer.workspace_id == p.workspace_id and consumer.actor_id == p.user_id
            and consumer.model_id == body.model_id and same(pinned,
                {k: body.model_dump()[k] for k in PreviewBody.model_fields})
            and c._family(consumer.data['preview']['preparation']).id == PROFILE)
        if consumer.reserved_call_id is not None: current_id = consumer.id
    actual = prepare_source(db, p, body, lock=lock, current_consumer_id=current_id)
    plan = actual['plan']; need(plan['status'] == 'ready' and 0 <= body.batch_ordinal < len(plan['batches']),
        '当前工作稿没有这个可发送批次，未创建调用')
    cap = published(db, p, body.model_id, 'foundation_model')
    need(cap.data['execution'].get('secret_ref') == 'DASHSCOPE_API_KEY'
        and cap.data['data_policy'].get('cloud_allowed') is True)
    native.input_policy(cap.data['execution'])
    with allocation_read_scope(p):
        prep = build_preparation(cap, actual['source_context'], admission_basis=actual['admission_basis'],
            plan=plan, batch_ordinal=body.batch_ordinal, document=actual['document'],
            draft_ref=actual['draft_ref'], draft_origin=actual['draft_origin'], name_origin=actual['name_origin'], note=body.note)
        inner = native_preparation(prep)
    need(inner['input_summary']['token_bound'] <= runtime.settings().cloud_daily_token_limit,
        '本批完整请求超过当前云模型日额度，未裁剪或发送')
    allowance = runtime._indexed_allowance(p, actual['producer_id'], actual['revision'], body.model_id, body.activity_id)
    allowance = {**allowance, 'generation_profile': PROFILE,
        'source_snapshot_hash': inner['source_snapshot_hash'], 'request_hash': inner['request_hash'],
        'plan_hash': inner['plan_hash'], 'batch_hash': inner['batch_hash'],
        'draft_ref_hash': digest(actual['draft_ref']), 'note_hash': digest(body.note),
        'current_scope_hash': prep['current_scope']['current_scope_hash']}
    grant = digest(allowance)
    used = db.scalar(select(Consumer).where(Consumer.workspace_id == p.workspace_id, Consumer.grant_hash == grant))
    need(used is None or consumer is not None and used.id == consumer.id, '本批许可已属于原活动，不能重复使用')
    cost = (Decimal(inner['input_summary']['prepared_request_bytes'] + 1024) * Decimal(str(allowance['input_usd_per_million']))
        + Decimal(inner['generation_contract']['dispatch_options']['max_tokens']) * Decimal(str(allowance['output_usd_per_million']))) / Decimal(1000000)
    need(float(cost.quantize(Decimal('0.000001'), rounding=ROUND_CEILING)) <= allowance['maximum_cost_usd'],
        '本批完整原生请求超过单次费用上限，未发送')
    return {'allocation_source': actual, 'capability': cap, 'revision': actual['revision'],
        'budget': {'schema': 'report-saved-map-design-budget-v176', 'call_limit': 1,
            'token_bound': inner['input_summary']['token_bound'], 'maximum_cost_usd': allowance['maximum_cost_usd'],
            'pricing_basis_hash': allowance['pricing_basis_hash'], 'grant_hash': grant}}


def prepare(db, p, body, *, lock=False, consumer=None):
    c = _consumer(); require(p, 'admin', 'operator'); need(isinstance(body, PreviewBody))
    adapter = c.runtime(); method = getattr(adapter, 'admit_current_allocation', None)
    need(callable(method), '当前配置归类尚未接通；未创建调用', 503)
    admitted = method(db, p, body, lock=lock, consumer=consumer)
    need(type(admitted) is dict and set(admitted) == {'allocation_source', 'capability', 'revision', 'budget'})
    actual, cap = admitted['allocation_source'], admitted['capability']
    need(cap.id == body.model_id and cap.workspace_id == p.workspace_id and cap.status == 'published'
        and cap.content_hash == digest(cap.data) and admitted['revision'] == actual['revision']
        and actual['current_source_verified'] is True and actual['actual_paid_verified'] is False)
    with allocation_read_scope(p):
        evidence = native.build_allocation_evidence(actual['source_context'], plan=actual['plan'],
            batch_ordinal=body.batch_ordinal, document=actual['document'], draft_ref=actual['draft_ref'],
            draft_origin=actual['draft_origin'], name_origin=actual['name_origin'], note=body.note)
        prep = build_preparation(cap, actual['source_context'], admission_basis=actual['admission_basis'],
            **c._allocation_arguments(evidence))
        value = {'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
            'body': {k: copy.deepcopy(body.model_dump()[k]) for k in PreviewBody.model_fields},
            'source_context': copy.deepcopy(actual['source_context']), 'capability': c._cap_value(cap),
            'revision': admitted['revision'], 'allocation_evidence': evidence, 'preparation': prep,
            'budget': c.check_budget(admitted['budget'], native_preparation(prep)['input_summary']['token_bound']),
            **c.FALSE_FLAGS, 'generation_profile': PROFILE, 'activity_id': body.activity_id,
            'admission_basis': copy.deepcopy(prep['admission_basis']), 'current_scope': copy.deepcopy(prep['current_scope'])}
        return _check_preview({**value, 'preview_hash': digest(value)}, p)


def check_preview(value, p):
    # Each Current entry owns a fresh bounded pure scope, independently of an
    # enclosing inventory. No source/role/lease/file result is stored here.
    with allocation_read_scope(p):
        return _check_preview(value, p)


def _check_preview(value, p):
    c = _consumer()
    fields = {'schema', 'actor_id', 'workspace_id', 'body', 'source_context', 'capability', 'preparation',
        'budget', 'revision', 'allocation_evidence', 'preview_hash', 'generation_profile', 'activity_id',
        'admission_basis', 'current_scope', *c.FALSE_FLAGS}
    need(type(value) is dict and set(value) == fields and value['schema'] == PREVIEW
        and value['actor_id'] == p.user_id and value['workspace_id'] == p.workspace_id
        and value['preview_hash'] == digest({k: v for k, v in value.items() if k != 'preview_hash'})
        and all(value[k] is False for k in c.FALSE_FLAGS))
    body = PreviewBody.model_validate(value['body']); cap = c._cap(value['capability'])
    inner = native_preparation(value['preparation']); evidence = native.check_allocation_evidence(value['allocation_evidence'])
    admission = check_admission(value['admission_basis']); scope = check_scope(value['current_scope'], admission, inner)
    ref, metadata = evidence['draft_ref'], evidence['draft_origin']['metadata']
    snapshot = evidence['source_context']['snapshot']
    need(cap.id == body.model_id and cap.workspace_id == p.workspace_id
        and snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and ref['actor_id'] == p.user_id and ref['workspace_id'] == p.workspace_id
        and ref['id'] == body.draft_id and ref['version'] == body.expected_draft_version
        and ref['document_hash'] == body.expected_document_hash
        and admission['binding_id'] == body.binding_id and admission['binding_record_hash'] == body.binding_record_hash
        and admission['target_configuration']['configuration_hash'] == body.expected_configuration_hash
        and type(value['revision']) is int and value['revision'] == admission['target_configuration']['revision']
        and admission['source_pins']['source_revision'] == metadata['revision']
        and same(evidence['source_context'], value['source_context'])
        and same(evidence, inner['allocation_evidence'])
        and same(admission, value['preparation']['admission_basis']) and same(scope, value['preparation']['current_scope'])
        and evidence['note'] == body.note and evidence['batch_ordinal'] == body.batch_ordinal
        and same(evidence['plan']['selection'], body.selection.model_dump())
        and evidence['plan']['max_batch_items'] == body.max_batch_items
        and evidence['plan']['catalogue_review_hash'] == body.catalogue_review.expected_catalogue_review_hash
        and value['generation_profile'] == PROFILE and value['activity_id'] == body.activity_id)
    check_preparation(value['preparation'], cap, value['source_context'], **c._allocation_arguments(evidence))
    c.check_budget(value['budget'], inner['input_summary']['token_bound'])
    return value


def preview_view(value):
    c = _consumer(); body = PreviewBody.model_validate(value['body'])
    prep = native_preparation(value['preparation']); evidence = value['allocation_evidence']
    plan = evidence['plan']; batch = plan['batches'][body.batch_ordinal]
    return {'schema': PREVIEW, 'producer_id': prep['producer_id'], 'revision': value['revision'],
        'model_id': value['capability']['id'], 'model': native.MODEL, 'provider': 'qwen', 'note': body.note,
        'draft_ref': copy.deepcopy(evidence['draft_ref']), 'catalogue_review_hash': prep['catalogue_review_hash'],
        'plan_hash': prep['plan_hash'], 'batch_hash': prep['batch_hash'], 'batch_ordinal': body.batch_ordinal,
        'batch_count': len(plan['batches']), 'decision_count': len(batch['decision_keys']),
        'decision_keys': list(batch['decision_keys']), 'input_count': plan['source_pins']['input_count'],
        'source_snapshot_hash': prep['source_snapshot_hash'], 'preview_hash': value['preview_hash'],
        'sent_payload': copy.deepcopy(prep['payload']), 'input_summary': copy.deepcopy(prep['input_summary']),
        'budget': copy.deepcopy(value['budget']), 'call_count': 1, 'requires_separate_authorization': True,
        **c.FALSE_FLAGS, 'generation_profile': PROFILE, 'activity_id': body.activity_id,
        'admission_basis': copy.deepcopy(value['admission_basis']), 'current_scope': copy.deepcopy(value['current_scope'])}


def verify_history(db, p, row, *, physical=True):
    try:
        return _verify_history(db, p, row, physical=physical)
    except HTTPException:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '原机答或引用依据暂不能核对；原字节及人稿保留') from None


def _verify_history(db, p, row, *, physical=True):
    """Deep frozen-source/capture check outside Consumer.inventory (no cycle)."""
    c = _consumer()
    with allocation_read_scope(p):
        _check_preview(row.data['preview'], p)
        admission = check_admission(row.data['preview']['admission_basis'])
    drafts, children, bindings, _ = current._inventory(db, p)
    binding = bindings.get(admission['binding_id'])
    need(binding is not None and binding.record_hash == admission['binding_record_hash']
        and same(binding.data['source_draft_ref'], admission['source_draft_ref'])
        and same(binding.data['source_pins'], admission['source_pins'])
        and same(binding.data['target_configuration'], admission['target_configuration'])
        and binding.working_draft_id == admission['working_draft_ref']['id'])
    working, document = current._at(admission['working_draft_ref'], drafts, children, p)
    evidence = row.data['preview']['allocation_evidence']
    need(same(document, evidence['document']) and same(working.data['origin'], evidence['draft_origin']))
    if physical:
        origin = catalogue._load_origin(db, p, working.consumer_id,
            origin_mode=catalogue._mode(working.data['origin']))
        need(same(catalogue._origin_content(origin), catalogue._origin_content(working.data['origin'])))
    if physical and row.data.get('projection') is not None:
        received = c.runtime().read_response(db, p, row, recover_saved=False)
        with allocation_read_scope(p):
            need(type(received) is dict and set(received) == {'body', 'receipt', 'capture', 'descriptor', 'binding'},
                '原机答捕获或引用依据暂不能核对；原字节及人稿保留')
            response = row.data['response']
            need(type(received['body']) is bytes and same(received['binding'], c.journal_binding(row))
                and same(received['receipt'], response['receipt']) and same(received['capture'], response['capture'])
                and same(received['descriptor'], response['descriptor']))
            inspected = native.inspect_response(received['body'], received['capture']['http_status'], native.STAGE,
                response_complete=received['capture']['response_complete'],
                http_content_encoding=received['capture']['http_content_encoding'])
            raw = inspected['arguments'].encode('utf-8', errors='strict')
            wire.check_projection(row.data['projection'], raw, plan=evidence['plan'], batch_ordinal=evidence['batch_ordinal'])
    return row


def _task():
    try: return asyncio.current_task()
    except RuntimeError: return None


@contextmanager
def dispatch_scope(db, p, job, detached, request):
    """Bind actual stored identity after intent, without authorizing another call."""
    c = _consumer(); need(_DISPATCH.get() is None)
    records, _, _ = c._inventory(db, p, exclusive=True)
    row = c._owned(records, p, detached.id); c._lease(db, p, job, row)
    with allocation_read_scope(p):
        _check_preview(row.data['preview'], p)
        need(c._family(row.data['preview']['preparation']).id == PROFILE
            and row.data['dispatch_started'] is True and row.status == 'running'
            and same(c._row_pin(row), c._row_pin(detached)))
        inner = native_preparation(row.data['preview']['preparation'])
        need(request['request_hash'] == inner['request_hash']
            and same(request['messages'], request['request']['messages']))
        identity = c.dispatch_identity(row)
        proof = {'token': _TOKEN, 'session': db, 'principal': p, 'thread': threading.get_ident(),
            'task': _task(), 'identity': copy.deepcopy(identity), 'job_id': job.id, 'lease_token': job.lease_token,
            'capability_hash': row.data['preview']['capability']['content_hash'],
            'evidence_hash': digest(inner['allocation_evidence'])}
    # Release all writer locks before transport; the ordinary reservation callback
    # independently requalifies the live lease/source and commits the real Call.
    c.checkpoint(db)
    token = _DISPATCH.set(proof)
    try: yield
    finally: _DISPATCH.reset(token)


def require_dispatch(identity, p, capability, evidence):
    proof = _DISPATCH.get()
    need(type(proof) is dict and proof.get('token') is _TOKEN
        and proof['principal'] is p and proof['thread'] == threading.get_ident() and proof['task'] is _task()
        and same(identity, proof['identity']) and identity['job_id'] == proof['job_id']
        and identity['workspace_id'] == p.workspace_id and identity['actor_id'] == p.user_id
        and identity['capability_id'] == capability.id and capability.workspace_id == p.workspace_id
        and capability.content_hash == proof['capability_hash'] and digest(capability.data) == capability.content_hash
        and digest(evidence) == proof['evidence_hash']
        and identity['task_id'].startswith(TASK_PREFIX)
        and _hash(identity['task_id'][len(TASK_PREFIX):]))
    return True
