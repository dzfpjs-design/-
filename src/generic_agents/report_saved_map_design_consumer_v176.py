"""One independent, consented design call over cold-checked saved Maps.

The fixed runtime module is an explicit central integration seam. Its absence
blocks the route. No legacy decoder, fake Map, alternative provider, reservation
retry, old consent or old settlement is used as a fallback.
"""
import base64
import copy
import hashlib
import importlib
import json
import math
import re
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from .contracts import Contract, digest
from .db import session
from .models import Audit, CloudCall, Job, SetupReportDraft, User, Membership, AuthSession, now, uid
from .security import Principal, authenticated, require
from .relations import lock_current_actor
from .services import audit
from .typed_payload_codec_v131 import canonical_json
from .leases import ownership, checkpoint
from .gateway import ModelBlocked
from . import report_saved_map_design_preparation_v176 as preparation
from . import report_design_grouped_native_v176 as native
from . import report_design_grouped_wire_v174 as grouped
from .report_saved_map_design_models_v176 import (ReportSavedMapDesignV176 as Consumer,
    ReportSavedMapDesignCommandV176 as Command)

PREFIX = '/api/setup/report-saved-map-designs'
JOB = 'read_saved_map_design_v176'
STATE = 'report-saved-map-design-state-v176'
COMMAND = 'report-saved-map-design-command-v176'
PREVIEW = 'report-saved-map-design-preview-v176'
RESULT = 'report-saved-map-design-result-v176'
COMMAND_AUDIT = 'report.saved_map_design.command_v176'
STATE_AUDIT = 'report.saved_map_design.state_v176'
ALLOCATION = 'catalogue-allocation-v213'
CURRENT_ALLOCATION = 'catalogue-current-allocation-v260'
ALLOCATION_PROFILES = {ALLOCATION, CURRENT_ALLOCATION}
ALLOCATION_PREVIEW = 'report-business-catalogue-allocation-preview-v213'
ALLOCATION_LINK_AUDIT = 'report.catalogue_allocation_apply_link_v213'
ALLOCATION_CHILD_LINK_AUDIT = 'report.catalogue_allocation_child_link_v213'
ALLOCATION_END_AUDIT = 'report.catalogue_allocation_end_v213'
CURRENT_ALLOCATION_LINK_AUDIT = 'report.catalogue_current_allocation_apply_link_v260'
CURRENT_ALLOCATION_CHILD_LINK_AUDIT = 'report.catalogue_current_allocation_child_link_v260'
CURRENT_ALLOCATION_END_AUDIT = 'report.catalogue_current_allocation_end_v260'
MAX_WIRE_BYTES = 65536
MAX_INVENTORY = 1000
SHA = re.compile(r'^[a-f0-9]{64}$')
FALSE_FLAGS = {'semantic_quality_verified': False, 'can_apply': False, 'can_publish': False}
router = APIRouter(prefix=PREFIX)
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


class InventoryError(HTTPException):
    def __init__(self, detail='新方案、原命令或独立回执库存不完整；原记录保留，不能另建请求或调用'):
        super().__init__(409, detail)


def need(ok, message='新方案的来源、原版本或请求无法核对', code=409):
    if not ok:
        raise HTTPException(code, message)


def h(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def same(left, right):
    return canonical_json(left) == canonical_json(right)


def identity(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError('请保留原小写UUID编号')
    return value


def route_identity(value):
    try:
        return identity(value)
    except (ValueError,TypeError,AttributeError):
        raise HTTPException(422,'请保留原小写UUID编号') from None


class Closed(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)


class PreviewBody(Closed):
    producer_id: StrictStr
    revision: int = Field(strict=True, ge=0)
    model_id: StrictStr
    note: StrictStr = Field(max_length=2000)

    @field_validator('producer_id', 'model_id')
    @classmethod
    def ids(cls, value):
        return identity(value)

    @field_validator('note')
    @classmethod
    def unicode(cls, value):
        canonical_json(value)
        return value


class StartBody(PreviewBody):
    request_id: StrictStr
    source_snapshot_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    preview_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    consent: Literal[True]

    @field_validator('request_id')
    @classmethod
    def nonce(cls, value):
        return identity(value)

    @field_validator('consent', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError('请明确同意这一次新调用')
        return value


class PreviewIndexedBody(PreviewBody):
    generation_profile: Literal['indexed-v178', 'indexed-v179', 'catalogue-v211']
    activity_id: StrictStr

    @field_validator('activity_id')
    @classmethod
    def activity(cls, value):
        return identity(value)


class StartIndexedBody(StartBody):
    generation_profile: Literal['indexed-v178', 'indexed-v179', 'catalogue-v211']
    activity_id: StrictStr

    @field_validator('activity_id')
    @classmethod
    def activity(cls, value):
        return identity(value)

    @model_validator(mode='after')
    def original_activity_nonce(self):
        if self.request_id != self.activity_id:
            raise ValueError('原活动编号必须保持为本次原请求编号')
        return self


class AllocationSelection(Closed):
    mode: Literal['unassigned_and_pending', 'explicit']
    positions: list[Annotated[int, Field(strict=True, ge=0, lt=1920)]]

    @model_validator(mode='after')
    def exact_positions(self):
        if self.positions != sorted(set(self.positions)):
            raise ValueError('本批原位置必须严格升序且不重复')
        if self.mode == 'unassigned_and_pending' and self.positions:
            raise ValueError('默认未定项由服务器从当前稿计算')
        if self.mode == 'explicit' and not self.positions:
            raise ValueError('请明确选择本次要核对的原位置')
        return self


class AllocationReview(Closed):
    accepted: Literal[True]
    expected_catalogue_review_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')

    @field_validator('accepted', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError('请明确核对本次已保存业务目录')
        return value


class PreviewAllocationBody(Closed):
    generation_profile: Literal['catalogue-allocation-v213']
    activity_id: StrictStr
    draft_id: StrictStr
    expected_draft_version: int = Field(strict=True, ge=1)
    expected_document_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    model_id: StrictStr
    selection: AllocationSelection
    batch_ordinal: int = Field(strict=True, ge=0, lt=1920)
    max_batch_items: int = Field(strict=True, ge=1, le=80)
    catalogue_review: AllocationReview
    note: StrictStr = Field(max_length=2000)

    @field_validator('activity_id', 'draft_id', 'model_id')
    @classmethod
    def identities(cls, value):
        return identity(value)

    @field_validator('note')
    @classmethod
    def unicode(cls, value):
        canonical_json(value)
        if '\x00' in value:
            raise ValueError('本次完整要求不能包含空字符')
        return value


class StartAllocationBody(PreviewAllocationBody):
    request_id: StrictStr
    preview_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    plan_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    batch_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    consent: Literal[True]

    @field_validator('request_id')
    @classmethod
    def nonce(cls, value):
        return identity(value)

    @field_validator('consent', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError('请明确同意这一个新批次调用')
        return value

    @model_validator(mode='after')
    def original_activity_nonce(self):
        if self.request_id != self.activity_id:
            raise ValueError('本批原活动编号必须保持为原请求编号')
        return self


class RecoverBody(Closed):
    request_id: StrictStr
    expected_version: int = Field(strict=True, ge=1)

    @field_validator('request_id')
    @classmethod
    def nonce(cls, value):
        return identity(value)


def read_wire(raw, model):
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES, '请求须为64KB以内的完整UTF-8 JSON', 422)
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('重复字段')
                value[key] = item
            return value
        exact = raw.decode('utf-8', 'strict')
        value = json.loads(exact, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限数')))
        canonical_json(value)
        return model.model_validate(value), exact, hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '请求的字段、数值、版本或原始JSON不符合新方案合同') from None


def runtime():
    try:
        module = importlib.import_module('.report_saved_map_design_runtime_v176', __package__)
    except ModuleNotFoundError as error:
        if error.name != __package__ + '.report_saved_map_design_runtime_v176':
            raise
        raise HTTPException(503, '新方案的真实来源、当前授权或原生调用接线尚未启用；未创建调用') from None
    need(all(callable(getattr(module, name, None)) for name in ('admit','dispatch','read_response')),
        '新方案中央接线不完整，未采用旧管线或其他模型', 503)
    return module


def check_budget(value, token_bound):
    need(type(value) is dict and set(value) == {'schema','call_limit','token_bound',
        'maximum_cost_usd','pricing_basis_hash','grant_hash'}
        and value['schema'] == 'report-saved-map-design-budget-v176'
        and type(value['call_limit']) is int and value['call_limit'] == 1
        and type(value['token_bound']) is int and value['token_bound'] == token_bound
        and type(value['maximum_cost_usd']) in {int, float}
        and value['maximum_cost_usd'] > 0, '本次独立调用缺少真实的新费用上限或活动许可')
    try:
        need(math.isfinite(value['maximum_cost_usd']))
    except (OverflowError, TypeError):
        need(False)
    for key in ('pricing_basis_hash','grant_hash'):
        need(type(value[key]) is str and bool(SHA.fullmatch(value[key])))
    return copy.deepcopy(value)


def _cap_value(cap):
    return {'id': cap.id, 'workspace_id': cap.workspace_id,
        'content_hash': cap.content_hash, 'data': copy.deepcopy(cap.data)}


def _cap(value):
    need(type(value) is dict and set(value) == {'id','workspace_id','content_hash','data'}
        and value['content_hash'] == digest(value['data']))
    return SimpleNamespace(**copy.deepcopy(value))


def _family(prep):
    # Preserve the original module globals and call signatures for every V176
    # row. Only an exact NEW preparation marker enters the explicit registry.
    if type(prep) is dict and prep.get('schema') == preparation.PREPARATION:
        return SimpleNamespace(id='saved-v176', preparation=preparation, native=native, wire=grouped)
    from .report_design_profiles_v178 import profile_id, modules
    profile = profile_id(prep)
    need(profile in {'indexed-v178', 'indexed-v179', 'catalogue-v211', *ALLOCATION_PROFILES}, '原生成版本无法核对，不能使用旧族解码器')
    return modules(profile)


def _native_preparation(prep):
    if type(prep) is dict and prep.get('schema') == 'report-business-catalogue-current-allocation-preparation-v260':
        from .catalogue_current_allocation_v260 import native_preparation
        return native_preparation(prep)
    return prep


def _body_family(body):
    if getattr(body, 'generation_profile', None) == CURRENT_ALLOCATION:
        from .catalogue_current_allocation_v260 import PreviewBody as CurrentPreviewBody
        from .report_design_profiles_v178 import modules
        need(isinstance(body, CurrentPreviewBody))
        return modules(CURRENT_ALLOCATION)
    if isinstance(body, (PreviewAllocationBody, StartAllocationBody)):
        from .report_design_profiles_v178 import modules
        return modules(ALLOCATION)
    if isinstance(body, (PreviewIndexedBody, StartIndexedBody)):
        from .report_design_profiles_v178 import modules
        return modules(body.generation_profile)
    need(type(body) in {PreviewBody, StartBody})
    return SimpleNamespace(id='saved-v176', preparation=preparation, native=native, wire=grouped)


def _preview_model(family):
    if family.id == CURRENT_ALLOCATION:
        from .catalogue_current_allocation_v260 import PreviewBody as CurrentPreviewBody
        return CurrentPreviewBody
    if family.id == ALLOCATION:
        return PreviewAllocationBody
    return PreviewIndexedBody if family.id in {'indexed-v178', 'indexed-v179', 'catalogue-v211'} else PreviewBody


def _start_path(family):
    if family.id in ALLOCATION_PROFILES:
        return PREFIX + '/start-allocation'
    return PREFIX + ('/start-indexed' if family.id in {'indexed-v178', 'indexed-v179', 'catalogue-v211'} else '/start')


def _profile_fields(family, body):
    if family.id == CURRENT_ALLOCATION:
        return {'generation_profile': CURRENT_ALLOCATION, 'activity_id': body.activity_id}
    if family.id == ALLOCATION:
        return {'generation_profile': ALLOCATION, 'activity_id': body.activity_id}
    return ({'generation_profile': family.id, 'activity_id': body.activity_id}
        if family.id in {'indexed-v178', 'indexed-v179', 'catalogue-v211'} else {})


def prepare(db, p, body, *, lock=False, consumer=None):
    if getattr(body, 'generation_profile', None) == CURRENT_ALLOCATION:
        from .catalogue_current_allocation_v260 import prepare as current_prepare
        return current_prepare(db, p, body, lock=lock, consumer=consumer)
    if isinstance(body, PreviewAllocationBody):
        if consumer is None:
            return prepare_allocation(db, p, body, lock=lock)
        return prepare_allocation(db, p, body, lock=lock, consumer=consumer)
    require(p, 'admin', 'operator')
    family = _body_family(body)
    arguments = _profile_fields(family, body)
    admitted = runtime().admit(db, p, body.producer_id, body.revision, body.model_id,
        lock=lock, consumer=consumer, note=body.note, **arguments)
    need(type(admitted) is dict and set(admitted) == {'source_context','capability','revision','budget'})
    source, cap = admitted['source_context'], admitted['capability']
    need(type(admitted['revision']) is int and admitted['revision'] == body.revision
        and cap.id == body.model_id and cap.workspace_id == p.workspace_id
        and cap.status == 'published' and cap.content_hash == digest(cap.data))
    snapshot = source['snapshot']
    need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and snapshot['producer']['id'] == body.producer_id, '已存Map的真实作者、工作区或来源不匹配')
    prep = family.preparation.build_preparation(cap, source, note=body.note)
    budget = check_budget(admitted['budget'], prep['input_summary']['token_bound'])
    value = {'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': {key: copy.deepcopy(getattr(body, key)) for key in _preview_model(family).model_fields},
        'source_context': copy.deepcopy(source), 'capability': _cap_value(cap),
        'preparation': prep, 'budget': budget, **FALSE_FLAGS, **arguments}
    return {**value, 'preview_hash': h(value)}


def check_preview(value, p):
    need(type(value) is dict and 'preparation' in value)
    family = _family(value['preparation'])
    if family.id == CURRENT_ALLOCATION:
        from .catalogue_current_allocation_v260 import check_preview as current_check
        return current_check(value, p)
    if family.id == ALLOCATION:
        return check_allocation_preview(value, p)
    added = {'generation_profile', 'activity_id'} if family.id in {'indexed-v178', 'indexed-v179', 'catalogue-v211'} else set()
    need(type(value) is dict and set(value) == {'schema','actor_id','workspace_id','body',
        'source_context','capability','preparation','budget',*FALSE_FLAGS,'preview_hash',*added}
        and value['schema'] == PREVIEW and value['actor_id'] == p.user_id
        and value['workspace_id'] == p.workspace_id
        and value['preview_hash'] == h({key: item for key, item in value.items() if key != 'preview_hash'}))
    need(all(value[key] is False for key in FALSE_FLAGS))
    body = _preview_model(family).model_validate(value['body']); cap = _cap(value['capability'])
    need(all(same(value[key], item) for key, item in _profile_fields(family, body).items()))
    need(cap.id == body.model_id and cap.workspace_id == p.workspace_id)
    snapshot = value['source_context']['snapshot']
    need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and snapshot['producer']['id'] == body.producer_id)
    family.preparation.check_preparation(value['preparation'], cap, value['source_context'], note=body.note)
    check_budget(value['budget'], value['preparation']['input_summary']['token_bound'])
    return value


def preview_view(value):
    source, prep = value['source_context']['snapshot'], value['preparation']
    family = _family(prep); body = _preview_model(family).model_validate(value['body'])
    if family.id == CURRENT_ALLOCATION:
        from .catalogue_current_allocation_v260 import preview_view as current_view
        return current_view(value)
    if family.id == ALLOCATION:
        evidence = value['allocation_evidence']; plan = evidence['plan']
        batch = plan['batches'][body.batch_ordinal]
        return {'schema': ALLOCATION_PREVIEW, 'producer_id': source['producer']['id'],
            'revision': value['revision'], 'model_id': value['capability']['id'],
            'model': family.native.MODEL, 'provider': 'qwen', 'note': body.note,
            'draft_ref': copy.deepcopy(evidence['draft_ref']),
            'catalogue_review_hash': prep['catalogue_review_hash'],
            'plan_hash': prep['plan_hash'], 'batch_hash': prep['batch_hash'],
            'batch_ordinal': body.batch_ordinal, 'batch_count': len(plan['batches']),
            'decision_count': len(batch['decision_keys']), 'decision_keys': list(batch['decision_keys']),
            'input_count': plan['source_pins']['input_count'],
            'source_snapshot_hash': source['snapshot_hash'], 'preview_hash': value['preview_hash'],
            'sent_payload': copy.deepcopy(prep['payload']), 'input_summary': copy.deepcopy(prep['input_summary']),
            'budget': copy.deepcopy(value['budget']), 'call_count': 1,
            'requires_separate_authorization': True, **FALSE_FLAGS, **_profile_fields(family, body)}
    return {'schema': PREVIEW, 'producer_id': source['producer']['id'],
        'revision': value['body']['revision'], 'model_id': value['capability']['id'],
        'model': family.native.MODEL, 'provider': 'qwen', 'note': value['body']['note'],
        'source_snapshot_hash': source['snapshot_hash'], 'preview_hash': value['preview_hash'],
        'materials': copy.deepcopy(source['materials']), 'map_count': len(source['map_proofs']),
        'claim_count': source['claim_count'], 'sent_payload': copy.deepcopy(prep['payload']),
        'input_summary': copy.deepcopy(prep['input_summary']), 'budget': copy.deepcopy(value['budget']),
        'call_count': 1, 'requires_separate_authorization': True, **FALSE_FLAGS, **_profile_fields(family, body)}


def _allocation_arguments(evidence):
    return {key: copy.deepcopy(evidence[key]) for key in
        ('plan', 'batch_ordinal', 'document', 'draft_ref', 'draft_origin', 'name_origin', 'note')}


def prepare_allocation(db, p, body, *, lock=False, consumer=None):
    require(p, 'admin', 'operator')
    need(isinstance(body, PreviewAllocationBody))
    family = _body_family(body)
    adapter = runtime()
    need(callable(getattr(adapter, 'admit_allocation', None)), '本批独立来源与费用接线尚未启用；未创建调用', 503)
    admitted = adapter.admit_allocation(db, p, body, lock=lock, consumer=consumer)
    need(type(admitted) is dict and set(admitted) == {'allocation_source', 'capability', 'revision', 'budget'})
    actual = admitted['allocation_source']; cap = admitted['capability']
    need(cap.id == body.model_id and cap.workspace_id == p.workspace_id and cap.status == 'published'
        and cap.content_hash == digest(cap.data) and type(admitted['revision']) is int
        and admitted['revision'] == actual['revision']
        and actual['current_source_verified'] is True and actual['actual_paid_verified'] is False)
    evidence = family.native.build_allocation_evidence(actual['source_context'], plan=actual['plan'],
        batch_ordinal=body.batch_ordinal, document=actual['document'], draft_ref=actual['draft_ref'],
        draft_origin=actual['draft_origin'], name_origin=actual['name_origin'], note=body.note)
    prep = family.preparation.build_preparation(cap, actual['source_context'], **_allocation_arguments(evidence))
    value = {'schema': ALLOCATION_PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': {key: copy.deepcopy(body.model_dump()[key]) for key in PreviewAllocationBody.model_fields},
        'source_context': copy.deepcopy(actual['source_context']), 'capability': _cap_value(cap),
        'revision': admitted['revision'], 'allocation_evidence': evidence, 'preparation': prep,
        'budget': check_budget(admitted['budget'], prep['input_summary']['token_bound']),
        **FALSE_FLAGS, **_profile_fields(family, body)}
    return check_allocation_preview({**value, 'preview_hash': h(value)}, p)


def check_allocation_preview(value, p):
    need(type(value) is dict and set(value) == {'schema', 'actor_id', 'workspace_id', 'body',
        'source_context', 'capability', 'preparation', 'budget', 'revision', 'allocation_evidence',
        'preview_hash', 'generation_profile', 'activity_id', *FALSE_FLAGS}
        and value['schema'] == ALLOCATION_PREVIEW and value['actor_id'] == p.user_id
        and value['workspace_id'] == p.workspace_id
        and value['preview_hash'] == h({key: item for key, item in value.items() if key != 'preview_hash'})
        and all(value[key] is False for key in FALSE_FLAGS))
    body = PreviewAllocationBody.model_validate(value['body']); family = _body_family(body)
    cap = _cap(value['capability']); evidence = value['allocation_evidence']
    family.native.check_allocation_evidence(evidence)
    snapshot = evidence['source_context']['snapshot']; reference = evidence['draft_ref']
    metadata = evidence['draft_origin']['metadata']
    need(cap.id == body.model_id and cap.workspace_id == p.workspace_id
        and snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and reference['actor_id'] == p.user_id and reference['workspace_id'] == p.workspace_id
        and reference['id'] == body.draft_id and reference['version'] == body.expected_draft_version
        and reference['document_hash'] == body.expected_document_hash
        and type(value['revision']) is int and value['revision'] == metadata['revision']
        and same(evidence['source_context'], value['source_context'])
        and same(evidence, value['preparation']['allocation_evidence'])
        and evidence['note'] == body.note and evidence['batch_ordinal'] == body.batch_ordinal
        and same(evidence['plan']['selection'], body.selection.model_dump())
        and evidence['plan']['max_batch_items'] == body.max_batch_items
        and evidence['plan']['catalogue_review_hash'] == body.catalogue_review.expected_catalogue_review_hash
        and all(same(value[key], item) for key, item in _profile_fields(family, body).items()))
    family.preparation.check_preparation(value['preparation'], cap, value['source_context'],
        **_allocation_arguments(evidence))
    check_budget(value['budget'], value['preparation']['input_summary']['token_bound'])
    return value


def _lock(db, key, *, shared=False):
    name = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
    db.execute(text('SELECT ' + name + '(:k)'), {'k': int(h(key)[:15],16)})


def _nonce(db, p, nonce, *, shared=False):
    _lock(db, {'v176_saved_map_design_nonce': nonce, 'workspace_id': p.workspace_id}, shared=shared)


def _row_pin(row):
    return {key: copy.deepcopy(getattr(row,key)) for key in ('id','workspace_id','actor_id',
        'request_id','producer_id','model_id','grant_hash','revision','version','status','job_id','reserved_call_id','data')}


def _command_pin(row):
    return {key: copy.deepcopy(getattr(row,key)) for key in ('id','workspace_id','actor_id',
        'request_id','consumer_id','operation','method','path','exact_body','body_sha256','data')}


def _command_anchor(row):
    return {'schema': COMMAND, 'consumer_id': row.consumer_id, 'request_id': row.request_id,
        'command_hash': row.command_hash, 'body_sha256': row.body_sha256,
        'operation': row.operation, 'method': row.method, 'path': row.path,
        'result_hash': h(row.data['result'])}


def _state_anchor(row, previous):
    return {'schema': STATE, 'consumer_id': row.id, 'version': row.version,
        'previous_state_hash': previous, 'state_hash': h(_row_pin(row)),
        'command_refs': copy.deepcopy(row.data['command_refs'])}


def _audit_state(db, p, row, previous):
    audit(db,p,STATE_AUDIT,row.id,_state_anchor(row,previous))


def _transition(db, p, row, data, status=None, call_id=None):
    previous = h(_row_pin(row)); row.data = copy.deepcopy(data)
    if status is not None: row.status = status
    if call_id is not None: row.reserved_call_id = call_id
    row.version += 1; _audit_state(db,p,row,previous)


def dispatch_identity(row):
    family = _family(row.data['preview']['preparation'])
    task_id = {'indexed-v178':'saved-map-design-v178','indexed-v179':'saved-map-design-v179',
        'catalogue-v211':'report-business-catalogue-v211',
        ALLOCATION:'report-business-catalogue-allocation-v213'}.get(family.id, 'saved-map-design-v176')
    if family.id == CURRENT_ALLOCATION:
        from .catalogue_current_allocation_v260 import TASK_PREFIX
        _native_preparation(row.data['preview']['preparation'])
        task_id = TASK_PREFIX + row.data['preview']['current_scope']['current_scope_hash']
    return {'suggestion_id': row.id, 'job_id': row.job_id,
        'task_id': task_id,
        'request_hash': _native_preparation(row.data['preview']['preparation'])['request_hash'],
        'workspace_id': row.workspace_id, 'actor_id': row.actor_id, 'capability_id': row.model_id}


def gateway_dispatch_identity(row):
    """Exactly the four explicit inputs accepted by cloud_json's real wrapper."""
    value=dispatch_identity(row)
    return {key:value[key] for key in ('suggestion_id','job_id','task_id','request_hash')}


def _call_pin(call):
    return {'id': call.id, 'workspace_id': call.workspace_id, 'capability_id': call.capability_id,
        'reserved_tokens': call.reserved_tokens, **{key: copy.deepcopy(call.data.get(key)) for key in
            ('actor_id','request_hash','dispatch_options_hash','gateway_dispatch','delivery',
             'request_endpoint','request_body_bytes','input_policy_hash','schema_proof_hash')}}


def _check_call(row, call):
    raw_prep = row.data['preview']['preparation']; dispatch = call.data.get('gateway_dispatch', {})
    selected = _family(raw_prep).native; prep = _native_preparation(raw_prep)
    spec=prep['generation_contract']
    if not (call.workspace_id == row.workspace_id and call.capability_id == row.model_id
        and type(call.reserved_tokens) is int and call.reserved_tokens == prep['input_summary']['token_bound']
        and call.data.get('actor_id') == row.actor_id and call.data.get('request_hash') == prep['request_hash']
        and call.data.get('dispatch_options_hash') == digest(prep['generation_contract']['dispatch_options'])
        and call.data.get('delivery')==selected.DELIVERY and call.data.get('request_endpoint')==selected.ENDPOINT
        and type(call.data.get('request_body_bytes')) is int
        and call.data['request_body_bytes']==prep['input_summary']['prepared_request_bytes']
        and call.data.get('input_policy_hash')==digest(spec['input_policy'])
        and call.data.get('schema_proof_hash')==digest(spec['schema_proof'])
        and dispatch.get('schema') == 'cloud-dispatch-v130'
        and same(dispatch.get('identity'), dispatch_identity(row))
        and dispatch.get('reservation_request_hash') == prep['request_hash']
        and type(dispatch.get('attempt_id')) is str):
        raise InventoryError('新调用的实际预算预留、原请求或调度身份无法核对')
    identity(dispatch['attempt_id'])
    if row.reserved_call_id is not None:
        if row.reserved_call_id != call.id or not same(row.data['call_pin'],_call_pin(call)):
            raise InventoryError('原调用的独立预留锚点已变化，未创建新调用')
    return call


def _query(db, statement):
    return list(db.scalars(statement.execution_options(populate_existing=True)))


def _check_allocation_answer(row):
    """Frozen answer proof only; never reads a current Draft, source or HTTP file."""
    data = row.data
    apply_key = ('current_allocation_apply' if _family(data['preview']['preparation']).id == CURRENT_ALLOCATION
        else 'allocation_apply')
    if apply_key not in data or 'allocation_raw_arguments_b64' not in data or data['manual_value'] is not None:
        raise InventoryError('本批原回答或人工采用记录的独立字段缺失')
    encoded = data['allocation_raw_arguments_b64']; response = data.get('response')
    if encoded is None:
        if data.get('projection') is not None or response is not None and response.get('arguments_sha256') is not None:
            raise InventoryError('本批固定回答缺少原始参数字节')
        return
    try:
        if type(encoded) is not str or len(encoded) > 2 * 1024 * 1024:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        raw.decode('utf-8', errors='strict')
        if len(raw) > 1024 * 1024 or base64.b64encode(raw).decode('ascii') != encoded:
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise InventoryError('本批原参数的UTF8或base64字节无法核对') from None
    sha = hashlib.sha256(raw).hexdigest()
    if (row.reserved_call_id is None or type(response) is not dict
        or response.get('arguments_sha256') != sha or type(response.get('receipt')) is not dict
        or response['receipt'].get('arguments_sha256') != sha
        or response['receipt'].get('call_id') != row.reserved_call_id):
        raise InventoryError('本批原参数与独立财务回执无法核对')
    if data.get('projection') is not None:
        evidence = data['preview']['allocation_evidence']
        _family(data['preview']['preparation']).wire.check_projection(data['projection'], raw,
            plan=evidence['plan'], batch_ordinal=evidence['batch_ordinal'])


def _inventory_value(db, p, *, exclusive=False):
    # All writes, including worker transitions, take this actor mutex before
    # sorted consumer locks. Readers cannot observe half of a commit. Discovery
    # is three-sided: losing a command and its Audit still leaves row refs.
    _lock(db, {'v176_saved_map_design_actor':p.user_id,'workspace_id':p.workspace_id}, shared=not exclusive)
    records = _query(db,select(Consumer).where(Consumer.workspace_id==p.workspace_id,Consumer.actor_id==p.user_id))
    commands = _query(db,select(Command).where(Command.workspace_id==p.workspace_id,Command.actor_id==p.user_id))
    audits = _query(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.actor_id==p.user_id,
        Audit.action.in_([STATE_AUDIT,COMMAND_AUDIT])))
    allocation_lifecycle = _query(db,select(Audit).where(Audit.workspace_id==p.workspace_id,
        or_(Audit.actor_id==p.user_id,Audit.data['actor_id'].as_string()==p.user_id),
        Audit.action.in_([ALLOCATION_LINK_AUDIT, ALLOCATION_CHILD_LINK_AUDIT, ALLOCATION_END_AUDIT,
            CURRENT_ALLOCATION_LINK_AUDIT, CURRENT_ALLOCATION_CHILD_LINK_AUDIT, CURRENT_ALLOCATION_END_AUDIT])))
    allocation_links = [event for event in allocation_lifecycle
        if event.action in {ALLOCATION_LINK_AUDIT, ALLOCATION_CHILD_LINK_AUDIT}]
    allocation_ends = [event for event in allocation_lifecycle if event.action == ALLOCATION_END_AUDIT]
    current_links = [event for event in allocation_lifecycle
        if event.action in {CURRENT_ALLOCATION_LINK_AUDIT, CURRENT_ALLOCATION_CHILD_LINK_AUDIT}]
    current_ends = [event for event in allocation_lifecycle if event.action == CURRENT_ALLOCATION_END_AUDIT]
    if len(records)>MAX_INVENTORY or len(commands)>MAX_INVENTORY*4 or len(audits)>MAX_INVENTORY*20 or len(allocation_lifecycle)>MAX_INVENTORY*10:
        raise InventoryError('新方案库存超过当前完整核对容量，未按截断清单判断不存在')
    ids={row.id for row in records}|{row.consumer_id for row in commands}
    for event in audits:
        if type(event.data) is not dict or type(event.data.get('consumer_id')) is not str:
            raise InventoryError()
        ids.add(event.data['consumer_id'])
    for value in sorted(ids):
        identity(value); _lock(db,{'v176_saved_map_design':value,'workspace_id':p.workspace_id},shared=not exclusive)
    by_id={row.id:row for row in records}; by_command={row.id:row for row in commands}
    if len(by_id)!=len(records) or len(by_command)!=len(commands) or set(by_id)!=ids:
        raise InventoryError()
    checked_commands=set(); checked_audits=set(); calls={}
    from .report_catalogue_allocation_read_scope_v213 import allocation_read_scope
    for row in records:
        with allocation_read_scope(p):
            if type(row.version) is not int or row.version<1 or row.data.get('schema')!=STATE or type(row.data.get('dispatch_started')) is not bool:
                raise InventoryError()
            check_preview(row.data['preview'],p)
            family=_family(row.data['preview']['preparation'])
            if family.id != ALLOCATION and 'interrupted_end' in row.data:
                raise InventoryError()
            if family.id != CURRENT_ALLOCATION and ('current_interrupted_end' in row.data
                    or 'current_allocation_apply' in row.data):
                raise InventoryError()
            body=row.data['preview']['body']
            producer_id = (row.data['preview']['source_context']['snapshot']['producer']['id']
                if family.id in ALLOCATION_PROFILES else body['producer_id'])
            revision = row.data['preview']['revision'] if family.id in ALLOCATION_PROFILES else body['revision']
            if row.producer_id!=producer_id or row.model_id!=body['model_id'] or row.revision!=revision or row.grant_hash!=row.data['preview']['budget']['grant_hash']:
                raise InventoryError()
            refs=row.data.get('command_refs'); refs_by_id={ref['id']:ref for ref in refs} if type(refs) is list else {}
            if not refs or len(refs_by_id)!=len(refs): raise InventoryError()
            row_commands=[item for item in commands if item.consumer_id==row.id]
            if {item.id for item in row_commands}!=set(refs_by_id): raise InventoryError()
            for command in row_commands:
                ref=refs_by_id[command.id]
                if command.command_hash!=h(_command_pin(command)) or command.method!='POST': raise InventoryError()
                if family.id == CURRENT_ALLOCATION:
                    if command.operation == 'start':
                        from .catalogue_current_allocation_v260 import StartBody as CurrentStartBody
                        model = CurrentStartBody
                    elif command.operation == 'recover-saved': model = RecoverBody
                    elif command.operation == 'apply-current-allocation':
                        from .catalogue_current_allocation_apply_v260 import ApplyBody as CurrentApplyBody
                        model = CurrentApplyBody
                    elif command.operation == 'end-current-interrupted':
                        from .catalogue_current_allocation_end_v260 import EndInterruptedBody as CurrentEndInterruptedBody
                        model = CurrentEndInterruptedBody
                    else: raise InventoryError()
                elif family.id == ALLOCATION:
                    if command.operation == 'start': model = StartAllocationBody
                    elif command.operation == 'recover-saved': model = RecoverBody
                    elif command.operation == 'apply-allocation':
                        from .report_business_catalogue_allocation_apply_v213 import ApplyAllocationBody
                        model = ApplyAllocationBody
                    elif command.operation == 'end-interrupted':
                        from .report_business_catalogue_allocation_end_v213 import EndInterruptedBody
                        model = EndInterruptedBody
                    else: raise InventoryError()
                else:
                    model=(StartIndexedBody if family.id in {'indexed-v178','indexed-v179','catalogue-v211'} else StartBody) if command.operation=='start' else RecoverBody
                value,exact,sha=read_wire(command.exact_body.encode('utf-8'),model)
                expected_path=(_start_path(family) if command.operation=='start' else
                    PREFIX+'/'+row.id+('/apply-allocation' if command.operation=='apply-allocation'
                        else '/apply-current-allocation' if command.operation=='apply-current-allocation'
                        else '/end-current-interrupted' if command.operation=='end-current-interrupted'
                        else '/end-interrupted' if command.operation=='end-interrupted' else '/recover-saved'))
                allowed = ({'start','recover-saved','apply-current-allocation','end-current-interrupted'}
                    if family.id == CURRENT_ALLOCATION else {'start','recover-saved','apply-allocation','end-interrupted'}
                    if family.id == ALLOCATION else {'start','recover-saved'})
                if command.operation not in allowed or command.path!=expected_path or value.request_id!=command.request_id or sha!=command.body_sha256:
                    raise InventoryError()
                if not same(ref,{'id':command.id,'request_id':command.request_id,'operation':command.operation,'wire_sha256':sha}): raise InventoryError()
                anchors=[event for event in audits if event.action==COMMAND_AUDIT and event.object_id==command.id]
                if len(anchors)!=1 or not same(anchors[0].data,_command_anchor(command)): raise InventoryError()
                result=command.data.get('result')
                if type(result) is not dict or result.get('id')!=row.id or result.get('job_id')!=row.job_id or type(result.get('version')) is not int or not 1<=result['version']<=row.version:
                    raise InventoryError()
                if command.operation=='start':
                    if family.id in ALLOCATION_PROFILES:
                        native_prep = _native_preparation(row.data['preview']['preparation'])
                        if (command.request_id!=row.request_id or value.preview_hash!=row.data['preview']['preview_hash']
                            or value.plan_hash!=native_prep['plan_hash']
                            or value.batch_hash!=native_prep['batch_hash']
                            or not same({key:value.model_dump()[key] for key in _preview_model(family).model_fields},body)):
                            raise InventoryError()
                        if family.id == CURRENT_ALLOCATION and (value.request_hash != native_prep['request_hash']
                                or value.current_scope_hash != row.data['preview']['current_scope']['current_scope_hash']):
                            raise InventoryError()
                    elif command.request_id!=row.request_id or value.preview_hash!=row.data['preview']['preview_hash'] or value.source_snapshot_hash!=row.data['preview']['source_context']['snapshot']['snapshot_hash'] or not same({key:getattr(value,key) for key in _preview_model(family).model_fields},body): raise InventoryError()
                elif command.operation == 'apply-allocation':
                    from .report_business_catalogue_allocation_apply_v213 import check_parent_command
                    check_parent_command(db, p, row, command, ref=ref)
                elif command.operation == 'end-interrupted':
                    from .report_business_catalogue_allocation_end_v213 import check_parent_command
                    check_parent_command(db, p, row, command, ref=ref)
                elif command.operation == 'apply-current-allocation':
                    from .catalogue_current_allocation_apply_v260 import check_parent_command
                    check_parent_command(db, p, row, command, ref=ref)
                elif command.operation == 'end-current-interrupted':
                    from .catalogue_current_allocation_end_v260 import check_parent_command
                    check_parent_command(db, p, row, command, ref=ref)
                checked_commands.add(command.id);checked_audits.add(anchors[0].id)
            starts=[item for item in row_commands if item.operation=='start']
            if len(starts)!=1:raise InventoryError()
            history=sorted((event for event in audits if event.action==STATE_AUDIT and event.object_id==row.id),key=lambda event:event.data.get('version',0))
            if len(history)!=row.version:raise InventoryError()
            previous=None
            previous_refs=[]
            for version,event in enumerate(history,1):
                data=event.data
                if set(data)!={'schema','consumer_id','version','previous_state_hash','state_hash','command_refs'} or type(data.get('version')) is not int or data['version']!=version or data.get('previous_state_hash')!=previous or data.get('schema')!=STATE or data.get('consumer_id')!=row.id or type(data.get('state_hash')) is not str or SHA.fullmatch(data['state_hash']) is None:
                    raise InventoryError()
                saved_refs=data['command_refs']
                if type(saved_refs) is not list or not saved_refs or len(saved_refs)<len(previous_refs) or not same(saved_refs,refs[:len(saved_refs)]) or not same(saved_refs[:len(previous_refs)],previous_refs):raise InventoryError()
                for ref in saved_refs[len(previous_refs):]:
                    if by_command[ref['id']].data['result']['version']!=version:raise InventoryError()
                previous_refs=saved_refs;previous=data['state_hash'];checked_audits.add(event.id)
            if not same(history[-1].data,_state_anchor(row,history[-1].data['previous_state_hash'])):raise InventoryError()
            job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==row.job_id).execution_options(populate_existing=True))
            if job is None or job.kind!=JOB or job.idempotency_key!='saved-map-design:'+row.request_id or job.data.get('actor_id')!=p.user_id or job.data.get('consumer_id')!=row.id:
                raise InventoryError('新方案缺少真实的原后台任务，未重建或重跑')
            if family.id in ALLOCATION_PROFILES:
                if job.data.get('generation_profile') != family.id:
                    raise InventoryError('本批原后台任务的生成族身份无法核对')
                _check_allocation_answer(row)
            actual_calls=_query(db,select(CloudCall).where(CloudCall.workspace_id==p.workspace_id,
                CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string()==row.id))
            if len(actual_calls)>1 or row.reserved_call_id is not None and (len(actual_calls)!=1 or actual_calls[0].id!=row.reserved_call_id):raise InventoryError('新方案存在多次调用或独立调用库存损坏')
            if actual_calls:
                if row.data.get('dispatch_started') is not True:raise InventoryError()
                calls[row.id]=_check_call(row,actual_calls[0])
            elif row.data.get('call_pin') is not None:raise InventoryError()
    if checked_commands!=set(by_command) or checked_audits!={event.id for event in audits}:raise InventoryError()
    if allocation_links or any(command.operation == 'apply-allocation' for command in commands) or any(
        row.data.get('allocation_apply') is not None for row in records):
        from .report_business_catalogue_allocation_apply_v213 import check_inventory_links
        check_inventory_links(db, p, by_id, by_command, allocation_links)
    if allocation_ends or any(command.operation == 'end-interrupted' for command in commands) or any(
        row.data.get('interrupted_end') is not None for row in records):
        from .report_business_catalogue_allocation_end_v213 import check_inventory
        check_inventory(db, p, by_id, by_command, allocation_ends)
    if current_links or any(command.operation == 'apply-current-allocation' for command in commands) or any(
        row.data.get('current_allocation_apply') is not None for row in records):
        from .catalogue_current_allocation_apply_v260 import check_inventory_links
        check_inventory_links(db, p, by_id, by_command, current_links)
    if current_ends or any(command.operation == 'end-current-interrupted' for command in commands) or any(
        row.data.get('current_interrupted_end') is not None for row in records):
        from .catalogue_current_allocation_end_v260 import check_inventory
        check_inventory(db, p, by_id, by_command, current_ends)
    return by_id,by_command,calls


def _inventory(db, p, *, exclusive=False):
    try:
        from .report_catalogue_allocation_read_scope_v213 import allocation_read_scope
        with allocation_read_scope(p):
            return _inventory_value(db,p,exclusive=exclusive)
    except InventoryError:
        raise
    except HTTPException:
        raise InventoryError() from None
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError,OverflowError):
        raise InventoryError() from None


def _owned(records,p,consumer_id):
    row=records.get(consumer_id)
    need(row is not None and row.workspace_id==p.workspace_id and row.actor_id==p.user_id,
        '新方案不存在或不属于当前账号',404)
    return row


def checked_consumer(db,p,consumer_id,*,lock=False):
    """Internal owner ledger, NOT a current source, grant or dispatch permit."""
    need(type(lock) is bool)
    records,_,_=_inventory(db,p,exclusive=lock)
    return _owned(records,p,consumer_id)


def result(row,operation):
    return {'schema':RESULT,'id':row.id,'version':row.version,'operation':operation,
        'status':row.status,'job_id':row.job_id}


def _add_command(db,p,row,request_id,operation,path,exact,sha):
    command=Command(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,request_id=request_id,
        consumer_id=row.id,operation=operation,method='POST',path=path,exact_body=exact,
        body_sha256=sha,data={'schema':COMMAND,'result':result(row,operation)})
    command.command_hash=h(_command_pin(command));db.add(command)
    audit(db,p,COMMAND_AUDIT,command.id,_command_anchor(command));return command


def _replay(commands,p,nonce,operation,path,exact,sha):
    matches=[row for row in commands.values() if row.request_id==nonce]
    if not matches:return None
    need(len(matches)==1);row=matches[0]
    need(row.actor_id==p.user_id and row.operation==operation and row.path==path
        and row.exact_body==exact and row.body_sha256==sha,'原编号已有不同正文或操作，未重复执行')
    return copy.deepcopy(row.data['result'])


def _any_nonce(db,p,nonce):
    from .catalogue_current_allocation_closure_v260 import guard_nonce
    guard_nonce(db,p,nonce)
    other=db.scalar(select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==nonce))
    need(other is None or other.actor_id==p.user_id,'该编号属于另一个账号，不能读取或借用',403)
    # A workspace nonce cannot be borrowed merely by deleting its command.
    # Only matching nonce traces participate; unrelated actors' damaged rows do
    # not expose their contents or become this actor's source authority.
    records=_query(db,select(Consumer).where(Consumer.workspace_id==p.workspace_id))
    anchors=_query(db,select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action.in_([STATE_AUDIT,COMMAND_AUDIT])))
    if len(records)>MAX_INVENTORY or len(anchors)>MAX_INVENTORY*20:raise InventoryError()
    for row in records+anchors:
        data=row.data if type(row.data) is dict else {}
        refs=data.get('command_refs',[])
        matches=(getattr(row,'request_id',None)==nonce or data.get('request_id')==nonce
            or type(refs) is list and any(type(ref) is dict and ref.get('request_id')==nonce for ref in refs))
        need(not matches or row.actor_id==p.user_id,'该编号属于另一个账号，不能读取或借用',403)


def journal_binding(row):
    need(row.reserved_call_id is not None)
    preview=row.data['preview'];prep=_native_preparation(preview['preparation'])
    return {'schema':'report-evidence-journal-binding-v2','actor_id':row.actor_id,
        'workspace_id':row.workspace_id,'draft_id':row.id,'stage':'reduce',
        'reserved_call_id':row.reserved_call_id,'source_hash':prep['source_hash'],
        'catalog_hash':prep['catalog_hash'],'generation_hash':digest({'published_model_hash':preview['capability']['content_hash'],
            'contract':prep['generation_contract']}),'dispatch_hash':digest(dispatch_identity(row)),
        'messages_hash':prep['messages_hash']}


def _associate(db,p,row,call):
    _check_call(row,call)
    if row.reserved_call_id is None:
        _transition(db,p,row,{**row.data,'call_pin':_call_pin(call)},call_id=call.id)
    return journal_binding(row)


def _read_paid(db,p,row,*,recover_saved):
    if row.status in {'review','needs_clarification','failed'}:
        # This already stored terminal answer is not overwritten by a missing
        # file, a new decoder or a second settlement. Fresh adoption has its own
        # current source/HTTP/ledger gate; this only preserves recorded history.
        return copy.deepcopy(row.data),row.status
    family=_family(row.data['preview']['preparation']);selected=family.native
    value=runtime().read_response(db,p,row,recover_saved=recover_saved)
    if value is None:
        return {**row.data,'error':'原调用尚无可核对的完整原生回答；只保留原调用，不再发送'},'response_unknown'
    need(type(value) is dict and set(value)=={'body','receipt','capture','descriptor','binding'})
    need(type(value['body']) is bytes and row.reserved_call_id is not None
        and same(value['binding'],journal_binding(row)))
    capture=value['capture'];receipt=value['receipt']
    need(type(capture) is dict and capture.get('response_complete') is True
        and capture.get('ready') is True and capture.get('content_encoding_safe') is True
        and capture.get('body_sha256')==hashlib.sha256(value['body']).hexdigest()
        and receipt.get('call_id')==row.reserved_call_id and receipt.get('provider')=='qwen'
        and receipt.get('model')==selected.MODEL and receipt.get('capture_hash')==capture.get('capture_hash'))
    inspected=selected.inspect_response(value['body'],capture['http_status'],selected.STAGE,
        response_complete=True,http_content_encoding=capture['http_content_encoding'])
    need(all(same(receipt.get(key),expected) for key,expected in inspected['metadata'].items()),
        '已付回答的原生HTTP元数据和财务回执不一致')
    # A complete but invalid envelope (including finish=length) is retained as
    # failed, never repaired or adopted. Settlement still refers to this call.
    from .cloud_budget import settle_paid_response
    prep=_native_preparation(row.data['preview']['preparation'])
    expected={'workspace_id':p.workspace_id,'capability_id':row.model_id,'actor_id':p.user_id,
        'request_hash':prep['request_hash'],'dispatch_options_hash':digest(prep['generation_contract']['dispatch_options']),
        'provider':'qwen','model':selected.MODEL}
    response={key:copy.deepcopy(value[key]) for key in ('receipt','capture','descriptor','binding')}
    response['body_sha256']=hashlib.sha256(value['body']).hexdigest()
    envelope_ok=inspected['metadata']['envelope_valid'] is True
    status='failed';projection=None;manual=None
    allocation_raw = {}
    if envelope_ok:
        arguments=inspected['arguments'].encode('utf-8',errors='strict')
        if family.id in ALLOCATION_PROFILES:
            allocation_raw = {'allocation_raw_arguments_b64': base64.b64encode(arguments).decode('ascii')}
        source=row.data['preview']['source_context'];snapshot=source['snapshot']
        try:
            if family.id in ALLOCATION_PROFILES:
                evidence = row.data['preview']['allocation_evidence']
                projection = selected.parse_arguments(value['body'], capture['http_status'], selected.STAGE,
                    response_complete=True, http_content_encoding=capture['http_content_encoding'],
                    plan=evidence['plan'], batch_ordinal=evidence['batch_ordinal'], allocation_evidence=evidence)
                status = ('needs_clarification' if any(decision['disposition'] == 'pending' or decision['questions']
                    for decision in projection['raw_wire']['decisions'].values()) else 'review')
            elif family.id == 'catalogue-v211':
                # A directory is a separate machine proposal, never a V166
                # complete design or a manually confirmed configuration.
                projection=family.wire.build_catalogue_projection(arguments,
                    indexed=prep['indexed_inputs'],source_snapshot_hash=prep['source_snapshot_hash'],
                    input_claims=[snapshot['input_items'][key] for key in snapshot['input_order']],
                    catalog=source['catalog'])
                status='needs_clarification' if projection['questions'] else 'review'
            else:
                extra=({'indexed':prep['indexed_inputs'],'source_snapshot_hash':prep['source_snapshot_hash']}
                    if family.id in {'indexed-v178','indexed-v179'} else {})
                projection=family.wire.build_reduction_projection(arguments,catalog=source['catalog'],
                    allowed_span_refs=source['allowed_span_refs'],context=source['context'],
                    input_order=snapshot['input_order'],input_items=snapshot['input_items'],**extra)
                from .report_evidence_pipeline_v168 import manual_value_from_candidate
                manual=manual_value_from_candidate(projection['result'],source['catalog'],source['allowed_span_refs'],source['context'])
                status='needs_clarification' if projection['result']['questions'] else 'review'
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OverflowError):
            projection=None;manual=None
        response['arguments_sha256']=hashlib.sha256(arguments).hexdigest()
    else:response['arguments_sha256']=None
    settle_paid_response(db,row.reserved_call_id,receipt,expected,'succeeded' if projection is not None else 'failed')
    return {**row.data,**allocation_raw,'response':response,'projection':projection,'manual_value':manual,
        'error':None if projection is not None else '本次新回答未完整通过原生终止、结构或全部声明对照核验；原回答和费用保留，未重发'},status


def view(row):
    raw_prep=row.data['preview']['preparation']
    family=_family(raw_prep);prep=_native_preparation(raw_prep)
    body=_preview_model(family).model_validate(row.data['preview']['body'])
    return {'schema':'report-saved-map-design-view-v176','id':row.id,'version':row.version,
        'status':row.status,'producer_id':row.producer_id,'model_id':row.model_id,'job_id':row.job_id,
        'reserved_call_id':row.reserved_call_id,'source_snapshot_hash':prep['source_snapshot_hash'],
        'preview_hash':row.data['preview']['preview_hash'],'budget':copy.deepcopy(row.data['preview']['budget']),
        'note':row.data['preview']['body']['note'],'error':row.data['error'],
        'result':copy.deepcopy(row.data['projection']['result']) if row.data['projection'] and family.id not in {'catalogue-v211',*ALLOCATION_PROFILES} else None,
        'manual_value':copy.deepcopy(row.data['manual_value']),
        'projection':copy.deepcopy(row.data['projection']), 'call_count_limit':1,**FALSE_FLAGS,
        **_profile_fields(family,body),
        **({'catalogue':copy.deepcopy(row.data['projection']), 'catalogue_partial':None,
            'revision':row.revision}
            if family.id=='catalogue-v211' else {}),
        **({'allocation':copy.deepcopy(row.data['projection']), 'catalogue':None, 'catalogue_partial':None,
            'revision':row.revision, 'draft_ref':copy.deepcopy(row.data['preview']['allocation_evidence']['draft_ref']),
            'base_businesses':copy.deepcopy(row.data['preview']['allocation_evidence']['plan']['businesses']),
            'catalogue_review_hash':prep['catalogue_review_hash'], 'plan_hash':prep['plan_hash'],
            'batch_hash':prep['batch_hash'], 'batch_ordinal':prep['batch_ordinal'],
            'allocation_apply':copy.deepcopy(row.data['current_allocation_apply'] if family.id == CURRENT_ALLOCATION
                else row.data['allocation_apply'])}
            if family.id in ALLOCATION_PROFILES else {}),
        **({'admission_basis':copy.deepcopy(row.data['preview']['admission_basis']),
            'current_scope':copy.deepcopy(row.data['preview']['current_scope'])}
            if family.id == CURRENT_ALLOCATION else {})}


@router.post('/preview')
async def preview_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview','预览路径或方法不符合合同',422)
    body,_,_=read_wire(await request.body(),PreviewBody)
    return preview_view(prepare(db,p,body,lock=False))


@router.post('/start',status_code=202)
async def start_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start','启动路径或方法不符合合同',422)
    body,exact,sha=read_wire(await request.body(),StartBody)
    return _start(db,p,body,exact,sha)


@router.post('/preview-indexed')
async def preview_indexed_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-indexed','预览路径或方法不符合合同',422)
    body,_,_=read_wire(await request.body(),PreviewIndexedBody)
    return preview_view(prepare(db,p,body,lock=False))


@router.post('/start-indexed',status_code=202)
async def start_indexed_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-indexed','启动路径或方法不符合合同',422)
    body,exact,sha=read_wire(await request.body(),StartIndexedBody)
    return _start(db,p,body,exact,sha)


@router.post('/preview-allocation')
async def preview_allocation_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-allocation','本批预览路径或方法不符合合同',422)
    body,_,_=await _allocation_route_wire(request,PreviewAllocationBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body,lock=False)))


@router.post('/start-allocation',status_code=202)
async def start_allocation_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-allocation','本批启动路径或方法不符合合同',422)
    body,exact,sha=await _allocation_route_wire(request,StartAllocationBody)
    return await run_in_threadpool(_start_allocation,db,p,body,exact,sha)


async def _allocation_route_wire(request, model):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        need(size <= MAX_WIRE_BYTES, '本批完整申请超过当前容量，未裁剪或发送', 413)
        chunks.append(chunk)
    raw = b''.join(chunks)
    # This hint only selects the strict decoder. Duplicate keys, unsafe strings
    # and every field are still checked by read_wire against that exact model.
    try: hint = json.loads(raw).get('generation_profile')
    except (ValueError, TypeError, AttributeError, UnicodeError): hint = None
    if hint == CURRENT_ALLOCATION and model in {PreviewAllocationBody, StartAllocationBody}:
        from .catalogue_current_allocation_v260 import PreviewBody as CurrentPreviewBody, StartBody as CurrentStartBody
        model = CurrentStartBody if model is StartAllocationBody else CurrentPreviewBody
    return read_wire(raw, model)


def _allocation_prelock(db,p):
    from .report_business_catalogue_allocation_source_v213 import lock_before_consumer
    require(p,'admin','operator');lock_current_actor(db,p)
    lock_before_consumer(db,p,write=True)


def _allocation_fresh_hint(db,p,job):
    # Only a lock-order hint, never a current grant or row/lease verification.
    # Started and terminal original calls must not acquire new source gates.
    with db.no_autoflush:
        row=db.scalar(select(Consumer).where(Consumer.id==job.data.get('consumer_id'),
            Consumer.workspace_id==p.workspace_id,Consumer.actor_id==p.user_id)
            .execution_options(populate_existing=True))
    return (row is not None and type(row.data) is dict and row.data.get('dispatch_started') is False
        and row.status not in {'review','needs_clarification','failed'})


def _start_allocation(db,p,body,exact,sha):
    # Source and Catalogue order precedes this family's Consumer actor mutex.
    # A replay does not need the old Draft to remain current or editable.
    if body.generation_profile == CURRENT_ALLOCATION:
        # Historical success/closed replay precedes NEW execution authority.
        # Take Catalogue EX initially; two first Starts must not both retain SH
        # and later attempt to upgrade it after finding no original command.
        from .catalogue_current_allocation_closure_v260 import _reader as closure_reader
        from .report_business_catalogue_store_v211 import _lock as catalogue_lock
        closure_reader(db,p,lock=True)
        catalogue_lock(db,p,write=True)
    else:
        _allocation_prelock(db,p)
    path=PREFIX+'/start-allocation'
    _nonce(db,p,body.request_id)
    if body.generation_profile == CURRENT_ALLOCATION:
        from .catalogue_current_allocation_closure_v260 import reject_closed_start
        reject_closed_start(db,p,body,exact,sha)
    _any_nonce(db,p,body.request_id)
    _,commands,_=_inventory(db,p,exclusive=True)
    prior=_replay(commands,p,body.request_id,'start',path,exact,sha)
    if prior is not None:return prior
    if body.generation_profile == CURRENT_ALLOCATION:
        _allocation_prelock(db,p)
    frozen=prepare(db,p,body,lock=True)
    prep=_native_preparation(frozen['preparation'])
    need(frozen['preview_hash']==body.preview_hash and prep['plan_hash']==body.plan_hash
        and prep['batch_hash']==body.batch_hash,
        '本批原预览、已保存稿或费用许可已变化，请保留原工作并重新核对')
    if body.generation_profile == CURRENT_ALLOCATION:
        need(body.request_hash == prep['request_hash']
            and body.current_scope_hash == frozen['current_scope']['current_scope_hash'],
            '本批原生请求或当前准入范围已变化；原人稿保留')
    need(db.scalar(select(Consumer.id).where(Consumer.workspace_id==p.workspace_id,
        Consumer.grant_hash==frozen['budget']['grant_hash'])) is None,
        '本批活动许可已被占用，未新增调用或重用额度')
    consumer_id,job_id,command_id=uid(),uid(),uid()
    row=Consumer(id=consumer_id,workspace_id=p.workspace_id,actor_id=p.user_id,
        request_id=body.request_id,producer_id=frozen['source_context']['snapshot']['producer']['id'],
        model_id=body.model_id,grant_hash=frozen['budget']['grant_hash'],revision=frozen['revision'],
        version=1,status='queued',job_id=job_id,reserved_call_id=None,data={
            'schema':STATE,'preview':frozen,'dispatch_started':False,'dispatch_identity':None,
            'call_pin':None,'response':None,'projection':None,'manual_value':None,'error':None,
            'allocation_raw_arguments_b64':None,
            **({'current_allocation_apply':None} if body.generation_profile == CURRENT_ALLOCATION else {'allocation_apply':None}),
            'command_refs':[{'id':command_id,'request_id':body.request_id,'operation':'start','wire_sha256':sha}]})
    try:
        db.add(Job(id=job_id,workspace_id=p.workspace_id,kind=JOB,status='queued',
            idempotency_key='saved-map-design:'+body.request_id,
            data={'actor_id':p.user_id,'consumer_id':consumer_id,'generation_profile':body.generation_profile}))
        db.flush();db.add(row);db.flush()
        command=Command(id=command_id,workspace_id=p.workspace_id,actor_id=p.user_id,request_id=body.request_id,
            consumer_id=row.id,operation='start',method='POST',path=path,exact_body=exact,
            body_sha256=sha,data={'schema':COMMAND,'result':result(row,'start')})
        command.command_hash=h(_command_pin(command));db.add(command);db.flush()
        audit(db,p,COMMAND_AUDIT,command.id,_command_anchor(command));_audit_state(db,p,row,None)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if getattr(getattr(error.orig,'diag',None),'constraint_name',None)=='uq_report_saved_map_design_grant_v176':
            raise HTTPException(409,'本批活动许可已被另一提交占用；未新增调用或重用额度') from None
        raise
    except BaseException:
        db.rollback();raise
    return result(row,'start')


def _start(db,p,body,exact,sha):
    if isinstance(body,StartAllocationBody):
        return _start_allocation(db,p,body,exact,sha)
    family=_body_family(body);path=_start_path(family)
    _nonce(db,p,body.request_id)
    _any_nonce(db,p,body.request_id);records,commands,_=_inventory(db,p,exclusive=True)
    prior=_replay(commands,p,body.request_id,'start',path,exact,sha)
    if prior is not None:return prior
    require(p,'admin','operator');lock_current_actor(db,p)
    frozen=prepare(db,p,body,lock=True)
    need(frozen['preview_hash']==body.preview_hash and frozen['source_context']['snapshot']['snapshot_hash']==body.source_snapshot_hash,
        '原预览、费用许可或发送范围已变化，请保留人稿并明确重新核对')
    need(db.scalar(select(Consumer.id).where(Consumer.workspace_id==p.workspace_id,
        Consumer.grant_hash==frozen['budget']['grant_hash'])) is None,
        '本次独立活动许可已经占用，原方案与费用保留；没有新建方案或退款重用')
    consumer_id,job_id,command_id=uid(),uid(),uid()
    row=Consumer(id=consumer_id,workspace_id=p.workspace_id,actor_id=p.user_id,
        request_id=body.request_id,producer_id=body.producer_id,model_id=body.model_id,
        grant_hash=frozen['budget']['grant_hash'],revision=body.revision,
        version=1,status='queued',job_id=job_id,reserved_call_id=None,data={
            'schema':STATE,'preview':frozen,'dispatch_started':False,'dispatch_identity':None,
            'call_pin':None,'response':None,'projection':None,'manual_value':None,'error':None,
            'command_refs':[{'id':command_id,'request_id':body.request_id,'operation':'start','wire_sha256':sha}]})
    try:
        # These mappers have scalar foreign keys, not ORM relationships. Flush
        # each parent before adding its child; add() order alone does not order
        # independent mapper INSERTs. All rows still commit or roll back once.
        db.add(Job(id=job_id,workspace_id=p.workspace_id,kind=JOB,status='queued',
            idempotency_key='saved-map-design:'+body.request_id,data={'actor_id':p.user_id,'consumer_id':consumer_id}))
        db.flush()
        db.add(row);db.flush()
        command=Command(id=command_id,workspace_id=p.workspace_id,actor_id=p.user_id,request_id=body.request_id,
            consumer_id=row.id,operation='start',method='POST',path=path,exact_body=exact,
            body_sha256=sha,data={'schema':COMMAND,'result':result(row,'start')})
        command.command_hash=h(_command_pin(command));db.add(command);db.flush()
        audit(db,p,COMMAND_AUDIT,command.id,_command_anchor(command));_audit_state(db,p,row,None)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if getattr(getattr(error.orig,'diag',None),'constraint_name',None)=='uq_report_saved_map_design_grant_v176':
            raise HTTPException(409,'本次独立活动许可已被另一提交占用；未新增调用或重用额度') from None
        raise
    except BaseException:
        db.rollback()
        raise
    return result(row,'start')


@router.get('/requests/{request_id}')
def original_request(request_id:str,p:P,db:DB,expected_sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
    route_identity(request_id);need(type(expected_sha256) is str and SHA.fullmatch(expected_sha256),'原请求SHA格式不正确',422)
    _nonce(db,p,request_id,shared=True)
    from .catalogue_current_allocation_closure_v260 import lookup as closed_lookup
    closed=closed_lookup(db,p,request_id,expected_sha256)
    if closed is not None:return closed
    _any_nonce(db,p,request_id)
    _,commands,_=_inventory(db,p)
    matches=[row for row in commands.values() if row.request_id==request_id]
    if not matches:return {'schema':'report-saved-map-design-request-v176','found':False,
        'request_id':request_id,'wire_sha256':expected_sha256,'result':None}
    need(len(matches)==1 and matches[0].body_sha256==expected_sha256,'原请求摘要不一致')
    return {'schema':'report-saved-map-design-request-v176','found':True,'request_id':request_id,
        'wire_sha256':matches[0].body_sha256,'result':copy.deepcopy(matches[0].data['result'])}


def _current_reader(db,p):
    """Recheck real HTTP identity without requesting a lock in display GETs."""
    require(p,'admin','operator','viewer')
    query=select(User,Membership).join(Membership,Membership.user_id==User.id).where(
        User.id==p.user_id,User.active.is_(True),Membership.workspace_id==p.workspace_id,
        Membership.role==p.role,Membership.role.in_(['admin','operator','viewer']))
    try:
        UUID(getattr(p,'session_id',None));http_session=True
    except (ValueError,TypeError,AttributeError):
        http_session=False
    if http_session:
        query=query.add_columns(AuthSession).join(AuthSession,AuthSession.user_id==User.id).where(
            AuthSession.id==p.session_id,AuthSession.revoked.is_(False),AuthSession.expires_at>now())
    need(db.execute(query.execution_options(populate_existing=True)).first() is not None,
        '当前账号、工作区权限或登录会话已变化，请重新登录后核对原方案',403)


def _current_allocation_history_prelock(db,p,*,consumer_id=None,producer_id=None):
    # A discovery hint only. Full inventory still authenticates every frozen
    # family below. Never take Catalogue after already holding Consumer.
    query=select(Consumer).where(Consumer.workspace_id==p.workspace_id,Consumer.actor_id==p.user_id)
    if consumer_id is not None:query=query.where(Consumer.id==consumer_id)
    if producer_id is not None:query=query.where(Consumer.producer_id==producer_id)
    with db.no_autoflush:
        rows=list(db.scalars(query.execution_options(populate_existing=True)))
    hinted=any(type(row.data) is dict and type(row.data.get('preview')) is dict
        and type(row.data['preview'].get('preparation')) is dict
        and row.data['preview']['preparation'].get('schema')=='report-business-catalogue-current-allocation-preparation-v260'
        for row in rows)
    if hinted:
        from .report_business_catalogue_allocation_source_v213 import lock_before_consumer
        lock_before_consumer(db,p)
    return hinted


@router.get('')
def list_route(producer_id:str,p:P,db:DB):
    route_identity(producer_id);_current_reader(db,p)
    producer=db.scalar(select(SetupReportDraft).where(SetupReportDraft.id==producer_id,
        SetupReportDraft.workspace_id==p.workspace_id,SetupReportDraft.actor_id==p.user_id)
        .execution_options(populate_existing=True))
    need(producer is not None,'原报告不存在或不属于当前账号',404)
    history_prelocked=_current_allocation_history_prelock(db,p,producer_id=producer_id)
    records,_,_=_inventory(db,p)
    selected=[row for row in sorted(records.values(),key=lambda row:row.id) if row.producer_id==producer_id]
    for row in selected:
        if _family(row.data['preview']['preparation']).id == CURRENT_ALLOCATION:
            need(history_prelocked,'原归类族的历史预锁依据已变化，请重新读取')
            from .catalogue_current_allocation_v260 import verify_history
            verify_history(db,p,row)
    items=[view(row) for row in selected]
    return {'schema':'report-saved-map-design-list-v178','producer_id':producer_id,'items':items}


def _capture_summary(row):
    """Read original bounded local capture only; never repair, settle or send."""
    answer={'schema':'report-saved-map-design-capture-summary-v178','available':False,
        'status':'missing','finish_reason':None,'http_status':None,'response_complete':False,
        'response_bytes':None,'arguments_bytes':None,'usage':None,'problem':None}
    if row.reserved_call_id is None:
        return answer
    from . import report_evidence_http_capture_v1 as capture
    from . import report_evidence_native_bridge_v1 as bridge
    raw_prep=row.data['preview']['preparation'];selected=_family(raw_prep).native
    prep=_native_preparation(raw_prep)
    spec=prep['generation_contract'];policy=copy.deepcopy(spec['input_policy'])
    need(policy.pop('single_batch') is True)
    ctx={'binding':journal_binding(row),'request_hash':prep['request_hash'],
        'endpoint':spec['endpoint'],'delivery':spec['delivery'],
        'schema_proof_hash':digest(spec['schema_proof']),'profile_hash':digest(policy),
        'token_bound':prep['input_summary']['token_bound'],'max_bytes':spec['max_response_bytes']}
    try:
        root=bridge.root();state=capture.load(root,ctx)
        answer.update(available=state['state']!='missing',status=state['state'],
            http_status=state['http_status'],response_complete=state['response_complete'],
            response_bytes=state['body_bytes'])
        if state['state']=='missing':return answer
        if state['ready'] is not True or state['content_encoding_safe'] is not True:
            return {**answer,'problem':'原采集仍不完整或被隔离；未采用、修写或重新发送'}
        body=capture.read_body(root,ctx,state['capture_hash'])
        inspected=selected.inspect_response(body,state['http_status'],selected.STAGE,
            response_complete=state['response_complete'],http_content_encoding=state['http_content_encoding'])
        metadata=inspected['metadata'];finish=metadata['finish_reason']
        answer['finish_reason']=finish if finish in {'stop','length','content_filter','tool_calls','function_call'} else None
        answer['arguments_bytes']=metadata['arguments_bytes']
        usage=inspected['usage']
        # The original body retains all usage fields. A public summary exposes
        # only validated token totals, never provider extension strings/objects.
        if (type(usage) is dict and all(type(usage.get(key)) is int and usage[key]>=0
                for key in ('prompt_tokens','completion_tokens','total_tokens'))
                and usage['prompt_tokens']+usage['completion_tokens']==usage['total_tokens']):
            answer['usage']={key:usage[key] for key in ('prompt_tokens','completion_tokens','total_tokens')}
        if metadata['envelope_valid'] is not True:
            answer['problem']='原采集未完整通过原生终止与封套核验；原记录保留，未采用或重发'
        elif _family(prep).id in {'indexed-v178','indexed-v179'}:
            from .report_design_response_diagnostics_v180 import diagnose_response
            answer['content_check']=diagnose_response(
                inspected['arguments'].encode('utf-8',errors='strict'),
                expected_count=prep['indexed_inputs']['input_count'])
        return answer
    except (OSError,ValueError,KeyError,TypeError,UnicodeError,RecursionError,OverflowError):
        return {**answer,'status':'unavailable','problem':'原采集暂不可安全核对；未显示原答或发起新调用'}


@router.get('/{consumer_id}/capture-summary')
def capture_summary_route(consumer_id:str,p:P,db:DB):
    route_identity(consumer_id);_current_reader(db,p)
    records,_,_=_inventory(db,p)
    return _capture_summary(_owned(records,p,consumer_id))


@router.get('/{consumer_id}/catalogue-partial')
def catalogue_partial_route(consumer_id:str,p:P,db:DB):
    route_identity(consumer_id)
    from .report_business_catalogue_source_v212 import partial_view
    try:
        return partial_view(db,p,consumer_id)
    except HTTPException:
        raise
    except (OSError,ValueError,KeyError,TypeError,AttributeError,UnicodeError,RecursionError,OverflowError):
        raise HTTPException(409,'原失败回答尚不能安全形成部分建议；原回答、费用和失败状态保留，未重新调用') from None


@router.get('/{consumer_id}')
def get_route(consumer_id:str,p:P,db:DB):
    route_identity(consumer_id)
    history_prelocked=_current_allocation_history_prelock(db,p,consumer_id=consumer_id)
    records,_,_=_inventory(db,p);row=_owned(records,p,consumer_id)
    if _family(row.data['preview']['preparation']).id == CURRENT_ALLOCATION:
        need(history_prelocked,'原归类族的历史预锁依据已变化，请重新读取')
        from .catalogue_current_allocation_v260 import verify_history
        verify_history(db,p,row)
    return view(row)


@router.post('/{consumer_id}/recover-saved')
async def recover_route(consumer_id:str,request:Request,p:P,db:DB):
    route_identity(consumer_id);need(request.method=='POST' and request.url.path==PREFIX+'/'+consumer_id+'/recover-saved','恢复路径或方法不符合合同',422)
    body,exact,sha=read_wire(await request.body(),RecoverBody)
    _nonce(db,p,body.request_id);_any_nonce(db,p,body.request_id)
    records,commands,calls=_inventory(db,p,exclusive=True);row=_owned(records,p,consumer_id)
    path=PREFIX+'/'+consumer_id+'/recover-saved'
    prior=_replay(commands,p,body.request_id,'recover-saved',path,exact,sha)
    if prior is not None:return prior
    require(p,'admin','operator');lock_current_actor(db,p)
    need(row.version==body.expected_version,'新方案版本已变化，请保留原请求并核对最新保存版本')
    need(row.data['dispatch_started'] is True,'本方案尚未开始原调用，没有可恢复的已付回答')
    if row.id in calls:_associate(db,p,row,calls[row.id])
    data,status=_read_paid(db,p,row,recover_saved=True)
    command_id=uid();data={**data,'command_refs':data['command_refs']+[
        {'id':command_id,'request_id':body.request_id,'operation':'recover-saved','wire_sha256':sha}]}
    _transition(db,p,row,data,status)
    command=Command(id=command_id,workspace_id=p.workspace_id,actor_id=p.user_id,request_id=body.request_id,
        consumer_id=row.id,operation='recover-saved',method='POST',path=path,exact_body=exact,
        body_sha256=sha,data={'schema':COMMAND,'result':result(row,'recover-saved')})
    command.command_hash=h(_command_pin(command));db.add(command);audit(db,p,COMMAND_AUDIT,command.id,_command_anchor(command))
    db.commit();return result(row,'recover-saved')


def _lease(db,p,job,row):
    lease=ownership(db)
    need(job.workspace_id==p.workspace_id and job.id==row.job_id and job.kind==JOB
        and job.status=='running' and job.data.get('actor_id')==p.user_id
        and job.data.get('consumer_id')==row.id and type(lease) is dict
        and lease.get('job_id')==job.id and type(job.lease_token) is str
        and lease.get('lease_token')==job.lease_token,
        '原新方案Job的实际租约或执行主体无法核对')


def run(db,p,job):
    """Normal Worker seam. A second ownership never initiates another POST.

    Central dispatch must wrap cloud_dispatch_v130 with dispatch_identity(row).
    on_reserved returns the exact v2 binding after committing the single real
    CloudCall association; it is called before transport and is not a sink.
    """
    allocation_hint = job.data.get('generation_profile') in ALLOCATION_PROFILES
    source_prelocked = allocation_hint and _allocation_fresh_hint(db,p,job)
    if source_prelocked:
        _allocation_prelock(db,p)
    records,_,calls=_inventory(db,p,exclusive=True)
    row=_owned(records,p,job.data.get('consumer_id'));_lease(db,p,job,row)
    family=_family(row.data['preview']['preparation'])
    need((family.id in ALLOCATION_PROFILES) == allocation_hint
        and (not allocation_hint or family.id == job.data.get('generation_profile')),
        '原后台任务的生成族预锁提示和固定实际申请不一致，未继续调用')
    if row.status in {'review','needs_clarification','failed'}:return view(row)
    if row.data['dispatch_started'] is True:
        if row.id in calls:_associate(db,p,row,calls[row.id])
        data,status=_read_paid(db,p,row,recover_saved=True)
        _transition(db,p,row,data,status);db.commit();return view(row)
    if family.id in ALLOCATION_PROFILES:
        need(source_prelocked, '本批新派发的原预锁提示已变化，未反向补锁或发送')
    body=_preview_model(family).model_validate(row.data['preview']['body'])
    require(p,'admin','operator')
    fresh=prepare(db,p,body,lock=True,consumer=row)
    need(same(fresh,row.data['preview']),'本次来源、当前模型或独立费用许可已变化；未发送')
    request=family.preparation.rebuild_request(fresh['preparation'],_cap(fresh['capability']),fresh['source_context'],
        **(_allocation_arguments(fresh['allocation_evidence']) if family.id in ALLOCATION_PROFILES else {'note':body.note}))
    spec=family.preparation.stage_spec(fresh['preparation'])
    _transition(db,p,row,{**row.data,'dispatch_started':True,'dispatch_identity':dispatch_identity(row)},'running')
    # Intent is durable before entering even reservation. A kill in this gap is
    # deliberately unknown, not permission to reserve/send on a later attempt.
    checkpoint(db)
    def on_reserved(call_id):
        try:
            if family.id in ALLOCATION_PROFILES:
                _allocation_prelock(db,p)
            identity(call_id);records,_,actual_calls=_inventory(db,p,exclusive=True)
            actual=_owned(records,p,row.id);_lease(db,p,job,actual)
            need(actual.status in {'running','response_unknown'} and actual.data['dispatch_started'] is True
                and actual.reserved_call_id in {None,call_id})
            call=actual_calls.get(actual.id);need(call is not None and call.id==call_id,'真实预留没有关联原调度身份')
            binding=_associate(db,p,actual,call)
            # A real owned-call exception may now be used by the central source
            # reader, but every other unresolved source/call still blocks dispatch.
            fresh=prepare(db,p,body,lock=True,consumer=actual)
            need(same(fresh,actual.data['preview']),'预留后当前资格已变化，未继续发送')
            checkpoint(db);return binding
        except BaseException:
            # A flushed association holds an FK KEY SHARE on CloudCall. The
            # gateway settles in ANOTHER Session: release this failed callback
            # transaction before that independent finish can take FOR UPDATE.
            # Earlier durable dispatch intent/reservation remain untouched.
            db.rollback()
            raise
    detached=SimpleNamespace(**_row_pin(row))
    try:
        runtime().dispatch(db,p,job,detached,request=request,spec=spec,on_reserved=on_reserved)
    except (ModelBlocked,ValueError,HTTPException) as failure:
        if isinstance(failure,InventoryError) or isinstance(failure,HTTPException) and failure.status_code in {401,403}:
            raise
        # A complete paid response may precede a schema/finish error in the
        # gateway. Read that SAME call, settle it if proven, and retain failure
        # without another POST. Unproven transport/storage stays unresolved.
        records,_,calls=_inventory(db,p,exclusive=True);row=_owned(records,p,detached.id);_lease(db,p,job,row)
        if row.id in calls:_associate(db,p,row,calls[row.id])
        try:
            data,status=_read_paid(db,p,row,recover_saved=True)
        except (OSError,ValueError):
            data,status={**row.data,'error':'原调用仍无完整可核对回答；原号和费用保留，不重新发送'},'response_unknown'
        if status not in {'review','needs_clarification','failed'}:
            _transition(db,p,row,data,'response_unknown');db.commit();raise
        _transition(db,p,row,data,status);db.commit();return view(row)
    records,_,calls=_inventory(db,p,exclusive=True);row=_owned(records,p,detached.id);_lease(db,p,job,row)
    if row.id in calls:_associate(db,p,row,calls[row.id])
    data,status=_read_paid(db,p,row,recover_saved=False)
    _transition(db,p,row,data,status);db.commit();return view(row)


def record_job_failure(db,p,job,reason=None):
    """Worker cleanup only; never swallow lease, IAM, database or inventory errors."""
    records,_,calls=_inventory(db,p,exclusive=True);row=_owned(records,p,job.data.get('consumer_id'))
    _lease(db,p,job,row)
    if row.status in {'review','needs_clarification','failed'}:return
    started=row.data['dispatch_started'] is True
    if started:
        if row.id in calls:_associate(db,p,row,calls[row.id])
        try:
            data,status=_read_paid(db,p,row,recover_saved=True)
        except (OSError,ValueError):
            data,status=row.data,'response_unknown'
        if status in {'review','needs_clarification','failed'}:
            _transition(db,p,row,data,status)
            return
    _transition(db,p,row,{**row.data,'error':('原调用结果尚待核对；原号与费用保留，不重新发送' if started
        else '新方案未通过调用前核对；没有使用旧许可或其他模型')},'response_unknown' if started else 'failed')


# Explicit relative routes share this original Consumer router/prefix.
from .catalogue_current_allocation_closure_v260 import register_routes as register_current_allocation_closure
register_current_allocation_closure(router)

# A separate historical display contract; full reads and every write retain
# their original source, native-response and complete-inventory gates.
from .catalogue_current_saved_record_v260 import register_routes as register_current_saved_record
register_current_saved_record(router)
from .catalogue_current_saved_index_v260 import register_routes as register_current_saved_index
register_current_saved_index(router)
