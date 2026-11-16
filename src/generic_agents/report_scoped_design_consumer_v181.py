"""Independent partial business design; no whole-report adoption authority."""
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
from sqlalchemy import select, text
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
from . import report_scoped_design_preparation_v181 as preparation
from . import report_scoped_design_native_v181 as native
from . import report_scoped_design_wire_v181 as grouped
from . import report_scoped_design_profiles_v182 as profiles
from .report_scoped_design_models_v181 import (ReportScopedDesignAttemptV181 as Consumer,
    ReportScopedDesignAttemptCommandV181 as Command)
PREFIX='/api/setup/report-scoped-design-attempts/v181'
JOB='read_scoped_design_v181'
STATE='report-scoped-design-state-v181'
COMMAND='report-scoped-design-command-v181'
PREVIEW='report-scoped-design-preview-v181'
RESULT='report-scoped-design-result-v181'
COMMAND_AUDIT='report.scoped_design.command_v181'
STATE_AUDIT='report.scoped_design.state_v181'
MAX_WIRE_BYTES=1024*1024
MAX_INVENTORY=1000
SHA=re.compile(r'^[a-f0-9]{64}$')
FALSE_FLAGS={'semantic_quality_verified':False,'aggregate_verified':False,'can_apply':False,'can_publish':False}
router=APIRouter(prefix=PREFIX)
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]

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
    plan_id: StrictStr
    expected_plan_version: int = Field(strict=True,ge=1)
    plan_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    scope_id: StrictStr = Field(min_length=1,max_length=128)
    revision: int = Field(strict=True,ge=1)
    model_id: StrictStr
    note: StrictStr = Field(max_length=2000)
    activity_id: StrictStr
    @field_validator('plan_id','model_id','activity_id')
    @classmethod
    def ids(cls,value): return identity(value)
    @field_validator('scope_id','note')
    @classmethod
    def unicode(cls,value): canonical_json(value); return value

class StartBody(PreviewBody):
    request_id: StrictStr
    source_snapshot_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    preview_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    consent: Literal[True]
    @field_validator('request_id')
    @classmethod
    def nonce(cls,value): return identity(value)
    @field_validator('consent',mode='before')
    @classmethod
    def explicit(cls,value):
        if value is not True: raise ValueError('请明确同意这一次单业务调用')
        return value
    @model_validator(mode='after')
    def original_activity_nonce(self):
        if self.request_id!=self.activity_id: raise ValueError('原活动必须使用原请求编号')
        return self

class RecoverBody(Closed):
    request_id: StrictStr
    expected_version: int = Field(strict=True, ge=1)

    @field_validator('request_id')
    @classmethod
    def nonce(cls, value):
        return identity(value)


class PreviewFlowBody(PreviewBody):
    generation_profile: Literal['flow-v182']


class StartFlowBody(StartBody):
    generation_profile: Literal['flow-v182']


class PreviewFlowCompatBody(PreviewBody):
    generation_profile: Literal['flow-v183']


class StartFlowCompatBody(StartBody):
    generation_profile: Literal['flow-v183']


class PreviewFlowPlanBody(PreviewBody):
    generation_profile: Literal['flow-v184']


class StartFlowPlanBody(StartBody):
    generation_profile: Literal['flow-v184']


class PreviewFlowReasonedBody(PreviewBody):
    generation_profile: Literal['flow-v187']


class StartFlowReasonedBody(StartBody):
    generation_profile: Literal['flow-v187']


class PreviewFlowCausalBody(PreviewBody):
    generation_profile: Literal['flow-v190']


class StartFlowCausalBody(StartBody):
    generation_profile: Literal['flow-v190']


class PreviewFlowNestedBody(PreviewBody):
    generation_profile: Literal['flow-v193']


class StartFlowNestedBody(StartBody):
    generation_profile: Literal['flow-v193']


class PreviewFlowRevisionBody(PreviewBody):
    generation_profile: Literal['flow-v185']
    parent_attempt_id: StrictStr
    @field_validator('parent_attempt_id')
    @classmethod
    def parent_id(cls, value): return identity(value)


class StartFlowRevisionBody(StartBody):
    generation_profile: Literal['flow-v185']
    parent_attempt_id: StrictStr
    @field_validator('parent_attempt_id')
    @classmethod
    def parent_id(cls, value): return identity(value)


class SaveReviewBody(RecoverBody):
    value: dict
    confirmed: bool = Field(strict=True)


def _manual(value, preview):
    family=_family(preview['preparation'])
    if profiles.is_flow(family):
        prepared=preview['preparation'];source=preview['source_context']
        try:
            return family.wire.validate_value(value,scope_binding=prepared['scope_binding'],
                catalog=source['catalog'],allowed_span_refs=prepared['allowed_span_refs'],
                context=source['context'],input_claims=prepared['input_claims'])
        except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError,OverflowError):
            raise HTTPException(422,'业务流程人工稿或原文依据不完整，原稿保留') from None
    from .report_object_semantics_v166 import validate_value
    refs = [item[0] for item in preview['preparation']['payload']['spans']]
    source = preview['source_context']
    try:
        checked = validate_value(value, source['catalog'], refs, source['context'])
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError,OverflowError):
        raise HTTPException(422,'人工稿的业务结构、字段或引用不完整，原稿保留') from None
    need(same(checked, value), '人工稿字段或来源不完整，未覆盖原稿', 422)
    need(len(checked['result']['businesses']) == 1
        and checked['result']['businesses'][0]['name'] == preview['preparation']['scope_binding']['scope_name'],
        '人工稿须属于当前单业务范围；其他业务请分别保存', 422)
    return checked


def read_wire(raw, model):
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES, '请求须为1MiB以内的完整UTF-8 JSON', 422)
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
        module = importlib.import_module('.report_scoped_design_runtime_v181', __package__)
    except ModuleNotFoundError as error:
        if error.name != __package__ + '.report_scoped_design_runtime_v181':
            raise
        raise HTTPException(503, '新方案的真实来源、当前授权或原生调用接线尚未启用；未创建调用') from None
    need(all(callable(getattr(module, name, None)) for name in ('admit','dispatch','read_response')),
        '新方案中央接线不完整，未采用旧管线或其他模型', 503)
    return module


def check_budget(value, token_bound):
    need(type(value) is dict and set(value) == {'schema','call_limit','token_bound',
        'maximum_cost_usd','conservative_cost_usd','pricing_basis_hash','grant_hash'}
        and value['schema'] == 'report-scoped-design-budget-v181'
        and type(value['call_limit']) is int and value['call_limit'] == 1
        and type(value['token_bound']) is int and value['token_bound'] == token_bound
        and type(value['maximum_cost_usd']) in {int, float}
        and 0 < value['maximum_cost_usd'] <= 3
        and type(value['conservative_cost_usd']) in {int,float}
        and 0 < value['conservative_cost_usd'] <= value['maximum_cost_usd'], '本次独立调用缺少真实的新费用上限或活动许可')
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
    try:return profiles.selected(prep)
    except ValueError:raise HTTPException(409,'原生成协议无法核对；没有改用其他解码器') from None
def _body_family(body):
    types={PreviewBody:profiles.SCOPED,StartBody:profiles.SCOPED,
        PreviewFlowBody:profiles.FLOW,StartFlowBody:profiles.FLOW,
        PreviewFlowCompatBody:profiles.FLOW_COMPAT,StartFlowCompatBody:profiles.FLOW_COMPAT,
        PreviewFlowPlanBody:profiles.FLOW_PLAN,StartFlowPlanBody:profiles.FLOW_PLAN,
        PreviewFlowRevisionBody:profiles.FLOW_REVISION,StartFlowRevisionBody:profiles.FLOW_REVISION,
        PreviewFlowReasonedBody:profiles.FLOW_REASONED,StartFlowReasonedBody:profiles.FLOW_REASONED,
        PreviewFlowCausalBody:profiles.FLOW_CAUSAL,StartFlowCausalBody:profiles.FLOW_CAUSAL,
        PreviewFlowNestedBody:profiles.FLOW_NESTED,StartFlowNestedBody:profiles.FLOW_NESTED}
    need(type(body) in types)
    return profiles.modules(types[type(body)])
def _preview_model(family): return {profiles.SCOPED:PreviewBody,profiles.FLOW:PreviewFlowBody,
    profiles.FLOW_COMPAT:PreviewFlowCompatBody,profiles.FLOW_PLAN:PreviewFlowPlanBody,profiles.FLOW_REVISION:PreviewFlowRevisionBody,
    profiles.FLOW_REASONED:PreviewFlowReasonedBody,profiles.FLOW_CAUSAL:PreviewFlowCausalBody,
    profiles.FLOW_NESTED:PreviewFlowNestedBody}[family.id]
def _start_model(family): return {profiles.SCOPED:StartBody,profiles.FLOW:StartFlowBody,
    profiles.FLOW_COMPAT:StartFlowCompatBody,profiles.FLOW_PLAN:StartFlowPlanBody,profiles.FLOW_REVISION:StartFlowRevisionBody,
    profiles.FLOW_REASONED:StartFlowReasonedBody,profiles.FLOW_CAUSAL:StartFlowCausalBody,
    profiles.FLOW_NESTED:StartFlowNestedBody}[family.id]
def _start_path(family): return PREFIX+{profiles.SCOPED:'/start',profiles.FLOW:'/start-flow',
    profiles.FLOW_COMPAT:'/start-flow-compatible',profiles.FLOW_PLAN:'/start-flow-plan',profiles.FLOW_REVISION:'/start-flow-revision',
    profiles.FLOW_REASONED:'/start-flow-reasoned',profiles.FLOW_CAUSAL:'/start-flow-causal',
    profiles.FLOW_NESTED:'/start-flow-nested'}[family.id]
def _profile_fields(family,body):
    if family.id == profiles.FLOW_REVISION:
        return {'generation_profile': family.id, 'parent_attempt_id': body.parent_attempt_id}
    return {'generation_profile':family.id} if profiles.is_flow(family) else {}

def prepare(db, p, body, *, lock=False, consumer=None):
    require(p, 'admin', 'operator')
    family=_body_family(body)
    from .report_scoped_design_store_v181 import checked_plan
    plan = checked_plan(db,p,body.plan_id,lock=lock,confirmed=True)
    need(plan.version == body.expected_plan_version and plan.revision == body.revision
        and plan.data['plan']['plan_hash'] == body.plan_hash, '原分派版本已变化；未发送')
    admitted = runtime().admit(db, p, plan, body, lock=lock, attempt=consumer)
    need(type(admitted) is dict and set(admitted) == {'source_context','capability','preparation','budget'})
    source, cap = admitted['source_context'], admitted['capability']
    need(cap.id == body.model_id and cap.workspace_id == p.workspace_id
        and cap.status == 'published' and cap.content_hash == digest(cap.data))
    snapshot = source['snapshot']
    need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and snapshot['producer']['id'] == plan.producer_id, '已存Map的真实作者、工作区或来源不匹配')
    prep = admitted['preparation']
    budget = check_budget(admitted['budget'], prep['input_summary']['token_bound'])
    value = {'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': {key: copy.deepcopy(getattr(body, key)) for key in _preview_model(family).model_fields},
        'source_context': copy.deepcopy(source), 'capability': _cap_value(cap),
        'scope_plan': copy.deepcopy(plan.data['plan']),
        'preparation': prep, 'budget': budget, **FALSE_FLAGS,**_profile_fields(family,body)}
    return {**value, 'preview_hash': h(value)}


def check_preview(value, p):
    need(type(value) is dict and 'preparation' in value)
    family = _family(value['preparation'])
    added={'generation_profile','parent_attempt_id'} if family.id == profiles.FLOW_REVISION else ({'generation_profile'} if profiles.is_flow(family) else set())
    need(type(value) is dict and set(value) == {'schema','actor_id','workspace_id','body',
        'source_context','scope_plan','capability','preparation','budget',*FALSE_FLAGS,'preview_hash',*added}
        and value['schema'] == PREVIEW and value['actor_id'] == p.user_id
        and value['workspace_id'] == p.workspace_id
        and value['preview_hash'] == h({key: item for key, item in value.items() if key != 'preview_hash'}))
    need(all(value[key] is False for key in FALSE_FLAGS))
    body = _preview_model(family).model_validate(value['body']); cap = _cap(value['capability'])
    need(all(same(value.get(k),v) for k,v in _profile_fields(family,body).items()))
    need(cap.id == body.model_id and cap.workspace_id == p.workspace_id)
    snapshot = value['source_context']['snapshot']
    need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and value['scope_plan']['plan_hash'] == body.plan_hash)
    family.preparation.check_preparation(value['preparation'], cap, value['source_context'],
        value['scope_plan'], body.scope_id, note=body.note)
    if family.id == profiles.FLOW_REVISION:
        parent = value['preparation']['parent_source']['binding']
        need(parent['attempt_id'] == body.parent_attempt_id and parent['plan_id'] == body.plan_id
            and parent['plan_hash'] == body.plan_hash and parent['scope_id'] == body.scope_id
            and parent['revision'] == body.revision and parent['producer_id'] == snapshot['producer']['id'])
    check_budget(value['budget'], value['preparation']['input_summary']['token_bound'])
    return value


def preview_view(value):
    source, prep = value['source_context']['snapshot'], value['preparation']
    family = _family(prep); body = _preview_model(family).model_validate(value['body'])
    extra = {}
    if family.id == profiles.FLOW_REVISION:
        extra = {'parent_attempt_id': body.parent_attempt_id, 'parent_source_hash': prep['parent_source_hash'],
            'base_plan_hash': digest(prep['parent_source']['raw_plan']), 'platform_policy_hash': prep['platform_policy_hash']}
    return {'schema': PREVIEW, 'producer_id': source['producer']['id'],
        'plan_id':body.plan_id,'plan_hash':body.plan_hash,'plan_version':body.expected_plan_version,
        'scope_id':body.scope_id,'partial_scope':True,
        'revision': value['body']['revision'], 'model_id': value['capability']['id'],
        'model': family.native.MODEL, 'provider': family.native.PROVIDER if family.id in {profiles.FLOW_REASONED,profiles.FLOW_CAUSAL,profiles.FLOW_NESTED} else 'qwen', 'note': value['body']['note'],
        'source_snapshot_hash': source['snapshot_hash'], 'preview_hash': value['preview_hash'],
        'materials': copy.deepcopy(source['materials']), 'map_count': len(source['map_proofs']),
        'claim_count': source['claim_count'], 'sent_payload': copy.deepcopy(prep['payload']),
        'input_summary': copy.deepcopy(prep['input_summary']), 'budget': copy.deepcopy(value['budget']),
        'call_count': 1, 'requires_separate_authorization': True, **FALSE_FLAGS, **_profile_fields(family, body), **extra}


def _lock(db, key, *, shared=False):
    name = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
    db.execute(text('SELECT ' + name + '(:k)'), {'k': int(h(key)[:15],16)})


def _nonce(db, p, nonce, *, shared=False):
    _lock(db, {'v181_saved_map_design_nonce': nonce, 'workspace_id': p.workspace_id}, shared=shared)


def _row_pin(row):
    return {key: copy.deepcopy(getattr(row,key)) for key in ('id','workspace_id','actor_id',
        'plan_id','plan_version','plan_hash','scope_id',
        'request_id','producer_id','model_id','grant_hash','revision','version','status','job_id','reserved_call_id','data')}


def _command_pin(row):
    return {key: copy.deepcopy(getattr(row,key)) for key in ('id','workspace_id','actor_id',
        'plan_id','attempt_version','body_hash',
        'request_id','attempt_id','operation','method','path','exact_body','wire_sha256','data')}


def _command_anchor(row):
    return {'schema': COMMAND, 'attempt_id': row.attempt_id, 'request_id': row.request_id,
        'command_hash': row.command_hash, 'wire_sha256': row.wire_sha256,
        'operation': row.operation, 'method': row.method, 'path': row.path,
        'result_hash': h(row.data['result'])}


def _state_anchor(row, previous):
    return {'schema': STATE, 'attempt_id': row.id, 'version': row.version,
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
    return {'suggestion_id': row.id, 'job_id': row.job_id,
        'task_id': family.native.STAGE if profiles.is_flow(family) else 'scoped-design-v181',
        'request_hash': row.data['preview']['preparation']['request_hash'],
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
    prep = row.data['preview']['preparation']; dispatch = call.data.get('gateway_dispatch', {})
    selected = _family(prep).native
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


def _inventory_value(db, p, *, exclusive=False):
    # All writes, including worker transitions, take this actor mutex before
    # sorted consumer locks. Readers cannot observe half of a commit. Discovery
    # is three-sided: losing a command and its Audit still leaves row refs.
    _lock(db, {'v181_saved_map_design_actor':p.user_id,'workspace_id':p.workspace_id}, shared=not exclusive)
    records = _query(db,select(Consumer).where(Consumer.workspace_id==p.workspace_id,Consumer.actor_id==p.user_id))
    commands = _query(db,select(Command).where(Command.workspace_id==p.workspace_id,Command.actor_id==p.user_id))
    audits = _query(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.actor_id==p.user_id,
        Audit.action.in_([STATE_AUDIT,COMMAND_AUDIT])))
    if len(records)>MAX_INVENTORY or len(commands)>MAX_INVENTORY*4 or len(audits)>MAX_INVENTORY*20:
        raise InventoryError('新方案库存超过当前完整核对容量，未按截断清单判断不存在')
    ids={row.id for row in records}|{row.attempt_id for row in commands}
    for event in audits:
        if type(event.data) is not dict or type(event.data.get('attempt_id')) is not str:
            raise InventoryError()
        ids.add(event.data['attempt_id'])
    for value in sorted(ids):
        identity(value); _lock(db,{'v181_saved_map_design':value,'workspace_id':p.workspace_id},shared=not exclusive)
    by_id={row.id:row for row in records}; by_command={row.id:row for row in commands}
    if len(by_id)!=len(records) or len(by_command)!=len(commands) or set(by_id)!=ids:
        raise InventoryError()
    checked_commands=set(); checked_audits=set(); calls={}
    for row in records:
        if type(row.version) is not int or row.version<1 or row.data.get('schema')!=STATE or type(row.data.get('dispatch_started')) is not bool:
            raise InventoryError()
        check_preview(row.data['preview'],p)
        family=_family(row.data['preview']['preparation'])
        body=row.data['preview']['body']
        if (row.producer_id!=row.data['preview']['source_context']['snapshot']['producer']['id']
            or row.plan_id!=body['plan_id'] or row.plan_version!=body['expected_plan_version']
            or row.plan_hash!=body['plan_hash'] or row.scope_id!=body['scope_id']
            or row.model_id!=body['model_id'] or row.revision!=body['revision']
            or row.grant_hash!=row.data['preview']['budget']['grant_hash']):
            raise InventoryError()
        refs=row.data.get('command_refs'); refs_by_id={ref['id']:ref for ref in refs} if type(refs) is list else {}
        if not refs or len(refs_by_id)!=len(refs): raise InventoryError()
        row_commands=[item for item in commands if item.attempt_id==row.id]
        if {item.id for item in row_commands}!=set(refs_by_id): raise InventoryError()
        for command in row_commands:
            ref=refs_by_id[command.id]
            if command.command_hash!=h(_command_pin(command)) or command.method!='POST': raise InventoryError()
            model={'start':_start_model(family),'recover-saved':RecoverBody,'save-review':SaveReviewBody}.get(command.operation)
            if model is None: raise InventoryError()
            value,exact,sha=read_wire(command.exact_body.encode('utf-8'),model)
            expected_path=_start_path(family) if command.operation=='start' else PREFIX+'/'+row.id+'/'+command.operation
            if (command.operation not in {'start','recover-saved','save-review'} or command.path!=expected_path
                or value.request_id!=command.request_id or sha!=command.wire_sha256
                or command.plan_id!=row.plan_id or command.attempt_version!=command.data['result']['version']
                or command.body_hash!=h(value.model_dump())):
                raise InventoryError()
            if not same(ref,{'id':command.id,'request_id':command.request_id,'operation':command.operation,'wire_sha256':sha}): raise InventoryError()
            anchors=[event for event in audits if event.action==COMMAND_AUDIT and event.object_id==command.id]
            if len(anchors)!=1 or not same(anchors[0].data,_command_anchor(command)): raise InventoryError()
            result=command.data.get('result')
            if type(result) is not dict or result.get('id')!=row.id or result.get('job_id')!=row.job_id or type(result.get('version')) is not int or not 1<=result['version']<=row.version:
                raise InventoryError()
            if command.operation=='start':
                if command.request_id!=row.request_id or value.preview_hash!=row.data['preview']['preview_hash'] or value.source_snapshot_hash!=row.data['preview']['source_context']['snapshot']['snapshot_hash'] or not same({key:getattr(value,key) for key in _preview_model(family).model_fields},body): raise InventoryError()
            checked_commands.add(command.id);checked_audits.add(anchors[0].id)
        starts=[item for item in row_commands if item.operation=='start']
        if len(starts)!=1:raise InventoryError()
        history=sorted((event for event in audits if event.action==STATE_AUDIT and event.object_id==row.id),key=lambda event:event.data.get('version',0))
        if len(history)!=row.version:raise InventoryError()
        previous=None
        previous_refs=[]
        for version,event in enumerate(history,1):
            data=event.data
            if set(data)!={'schema','attempt_id','version','previous_state_hash','state_hash','command_refs'} or type(data.get('version')) is not int or data['version']!=version or data.get('previous_state_hash')!=previous or data.get('schema')!=STATE or data.get('attempt_id')!=row.id or type(data.get('state_hash')) is not str or SHA.fullmatch(data['state_hash']) is None:
                raise InventoryError()
            saved_refs=data['command_refs']
            if type(saved_refs) is not list or not saved_refs or len(saved_refs)<len(previous_refs) or not same(saved_refs,refs[:len(saved_refs)]) or not same(saved_refs[:len(previous_refs)],previous_refs):raise InventoryError()
            for ref in saved_refs[len(previous_refs):]:
                if by_command[ref['id']].data['result']['version']!=version:raise InventoryError()
            previous_refs=saved_refs;previous=data['state_hash'];checked_audits.add(event.id)
        if not same(history[-1].data,_state_anchor(row,history[-1].data['previous_state_hash'])):raise InventoryError()
        if row.data['manual_value'] is not None:
            _manual(row.data['manual_value'],row.data['preview'])
        if row.status=='reviewed':
            need(row_commands and any(item.operation=='save-review'
                and read_wire(item.exact_body.encode('utf-8'),SaveReviewBody)[0].confirmed is True for item in row_commands))
        job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==row.job_id).execution_options(populate_existing=True))
        if job is None or job.kind!=JOB or job.idempotency_key!='scoped-design-v181:'+row.request_id or job.data.get('actor_id')!=p.user_id or job.data.get('attempt_id')!=row.id:
            raise InventoryError('新方案缺少真实的原后台任务，未重建或重跑')
        actual_calls=_query(db,select(CloudCall).where(CloudCall.workspace_id==p.workspace_id,
            CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string()==row.id))
        if len(actual_calls)>1 or row.reserved_call_id is not None and (len(actual_calls)!=1 or actual_calls[0].id!=row.reserved_call_id):raise InventoryError('新方案存在多次调用或独立调用库存损坏')
        if actual_calls:
            if row.data.get('dispatch_started') is not True:raise InventoryError()
            calls[row.id]=_check_call(row,actual_calls[0])
        elif row.data.get('call_pin') is not None:raise InventoryError()
    if checked_commands!=set(by_command) or checked_audits!={event.id for event in audits}:raise InventoryError()
    return by_id,by_command,calls


def _inventory(db, p, *, exclusive=False):
    try:
        return _inventory_value(db,p,exclusive=exclusive)
    except InventoryError:
        raise
    except HTTPException:
        raise InventoryError() from None
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError,OverflowError):
        raise InventoryError() from None


def _owned(records,p,attempt_id):
    row=records.get(attempt_id)
    need(row is not None and row.workspace_id==p.workspace_id and row.actor_id==p.user_id,
        '新方案不存在或不属于当前账号',404)
    return row


def checked_attempt(db,p,attempt_id,*,lock=False):
    """Internal owner ledger, NOT a current source, grant or dispatch permit."""
    need(type(lock) is bool)
    records,_,_=_inventory(db,p,exclusive=lock)
    return _owned(records,p,attempt_id)


def result(row,operation):
    return {'schema':RESULT,'id':row.id,'version':row.version,'operation':operation,
        'status':row.status,'job_id':row.job_id}


def _replay(commands,p,nonce,operation,path,exact,sha):
    matches=[row for row in commands.values() if row.request_id==nonce]
    if not matches:return None
    need(len(matches)==1);row=matches[0]
    need(row.actor_id==p.user_id and row.operation==operation and row.path==path
        and row.exact_body==exact and row.wire_sha256==sha,'原编号已有不同正文或操作，未重复执行')
    return copy.deepcopy(row.data['result'])


def _any_nonce(db,p,nonce):
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
    preview=row.data['preview'];prep=preview['preparation']
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
    if row.status in {'review','needs_clarification','reviewed','failed'}:
        # This already stored terminal answer is not overwritten by a missing
        # file, a new decoder or a second settlement. Fresh adoption has its own
        # current source/HTTP/ledger gate; this only preserves recorded history.
        return copy.deepcopy(row.data),row.status
    family=_family(row.data['preview']['preparation']);selected=family.native
    provider = selected.PROVIDER if family.id in {profiles.FLOW_REASONED,profiles.FLOW_CAUSAL,profiles.FLOW_NESTED} else 'qwen'
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
        and receipt.get('call_id')==row.reserved_call_id and receipt.get('provider')==provider
        and receipt.get('model')==selected.MODEL and receipt.get('capture_hash')==capture.get('capture_hash'))
    inspected=selected.inspect_response(value['body'],capture['http_status'],selected.STAGE,
        response_complete=True,http_content_encoding=capture['http_content_encoding'])
    need(all(same(receipt.get(key),expected) for key,expected in inspected['metadata'].items()),
        '已付回答的原生HTTP元数据和财务回执不一致')
    # A complete but invalid envelope (including finish=length) is retained as
    # failed, never repaired or adopted. Settlement still refers to this call.
    from .cloud_budget import settle_paid_response
    prep=row.data['preview']['preparation']
    expected={'workspace_id':p.workspace_id,'capability_id':row.model_id,'actor_id':p.user_id,
        'request_hash':prep['request_hash'],'dispatch_options_hash':digest(prep['generation_contract']['dispatch_options']),
        'provider':provider,'model':selected.MODEL}
    response={key:copy.deepcopy(value[key]) for key in ('receipt','capture','descriptor','binding')}
    response['body_sha256']=hashlib.sha256(value['body']).hexdigest()
    envelope_ok=inspected['metadata']['envelope_valid'] is True
    status='failed';projection=None;manual=None
    if envelope_ok:
        arguments=inspected['arguments'].encode('utf-8',errors='strict')
        source=row.data['preview']['source_context'];snapshot=source['snapshot']
        try:
            refs=[item[0] for item in prep['payload']['spans']]
            if profiles.is_flow(family):
                extra = {'parent_source': prep['parent_source']} if family.id == profiles.FLOW_REVISION else {}
                projection=family.wire.build_projection(arguments,catalog=source['catalog'],
                    allowed_span_refs=refs,context=source['context'],input_claims=prep['input_claims'],
                    scope_binding=prep['scope_binding'], **extra)
                manual=family.wire.new_manual(projection)
                _manual(manual,row.data['preview'])
                questions=projection['wire']['business']['questions']
            else:
                extra={'indexed':prep['indexed_inputs'],'input_claims':prep['input_claims'],
                    'source_snapshot_hash':prep['source_snapshot_hash'],'scope_binding':prep['scope_binding']}
                projection=family.wire.build_reduction_projection(arguments,catalog=source['catalog'],
                    allowed_span_refs=refs,context=source['context'],**extra)
                from .report_evidence_pipeline_v168 import manual_value_from_candidate
                manual=manual_value_from_candidate(projection['result'],source['catalog'],refs,source['context'])
                questions=projection['result']['questions']
            unresolved = (family.id == profiles.FLOW_REVISION and any(
                item['resolution'] == 'unresolved' for item in projection['raw_review']['findings']))
            status='needs_clarification' if questions or unresolved else 'review'
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OverflowError):
            projection=None;manual=None
        response['arguments_sha256']=hashlib.sha256(arguments).hexdigest()
    else:response['arguments_sha256']=None
    settle_paid_response(db,row.reserved_call_id,receipt,expected,'succeeded' if projection is not None else 'failed')
    return {**row.data,'response':response,'projection':projection,'manual_value':manual,
        'error':None if projection is not None else '本次新回答未完整通过原生终止、结构或全部声明对照核验；原回答和费用保留，未重发'},status


def view(row):
    prep=row.data['preview']['preparation']
    family=_family(prep);body=_preview_model(family).model_validate(row.data['preview']['body'])
    return {'schema':'report-scoped-design-view-v181','id':row.id,'version':row.version,
        'plan_id':row.plan_id,'plan_version':row.plan_version,'plan_hash':row.plan_hash,
        'scope_id':row.scope_id,'partial_scope':True,
        'status':row.status,'producer_id':row.producer_id,'model_id':row.model_id,'job_id':row.job_id,
        'reserved_call_id':row.reserved_call_id,'source_snapshot_hash':prep['source_snapshot_hash'],
        'preview_hash':row.data['preview']['preview_hash'],'budget':copy.deepcopy(row.data['preview']['budget']),
        'note':row.data['preview']['body']['note'],'error':row.data['error'],
        'result':copy.deepcopy(row.data['projection']['wire'] if profiles.is_flow(family)
            else row.data['projection']['result']) if row.data['projection'] else None,
        'manual_value':copy.deepcopy(row.data['manual_value']),
        'projection':copy.deepcopy(row.data['projection']), 'call_count_limit':1,**FALSE_FLAGS,
        **_profile_fields(family,body)}


@router.post('/preview')
async def preview_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview','预览路径或方法不符合合同',422)
    body,_,_=read_wire(await request.body(),PreviewBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body,lock=False)))


@router.post('/start',status_code=202)
async def start_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start','启动路径或方法不符合合同',422)
    body,exact,sha=read_wire(await request.body(),StartBody)
    return await run_in_threadpool(_start,db,p,body,exact,sha)


@router.post('/preview-flow')
async def preview_flow_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-flow','流程预览路径或方法不符',422)
    body,_,_=read_wire(await request.body(),PreviewFlowBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body,lock=False)))


@router.post('/preview-flow-compatible')
async def preview_flow_compat_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-flow-compatible','流程预览路径或方法不符',422)
    body,_,_=read_wire(await request.body(),PreviewFlowCompatBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body,lock=False)))


@router.post('/start-flow-compatible',status_code=202)
async def start_flow_compat_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-flow-compatible','流程启动路径或方法不符',422)
    body,exact,sha=read_wire(await request.body(),StartFlowCompatBody)
    return await run_in_threadpool(_start,db,p,body,exact,sha)


@router.post('/preview-flow-plan')
async def preview_flow_plan_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-flow-plan','流程计划预览路径或方法不符',422)
    body,_,_=read_wire(await request.body(),PreviewFlowPlanBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body,lock=False)))


@router.post('/start-flow-plan',status_code=202)
async def start_flow_plan_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-flow-plan','流程计划启动路径或方法不符',422)
    body,exact,sha=read_wire(await request.body(),StartFlowPlanBody)
    return await run_in_threadpool(_start,db,p,body,exact,sha)


@router.post('/preview-flow-revision')
async def preview_flow_revision_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-flow-revision','流程复核预览路径或方法不符',422)
    body,_,_=read_wire(await request.body(),PreviewFlowRevisionBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body)))


@router.post('/start-flow-revision',status_code=202)
async def start_flow_revision_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-flow-revision','流程复核启动路径或方法不符',422)
    body,exact,sha=read_wire(await request.body(),StartFlowRevisionBody)
    return await run_in_threadpool(_start,db,p,body,exact,sha)


@router.post('/preview-flow-reasoned')
async def preview_flow_reasoned_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-flow-reasoned','推理规划预览路径或方法不符',422)
    body,_,_=read_wire(await request.body(),PreviewFlowReasonedBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body)))


@router.post('/start-flow-reasoned',status_code=202)
async def start_flow_reasoned_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-flow-reasoned','推理规划启动路径或方法不符',422)
    body,exact,sha=read_wire(await request.body(),StartFlowReasonedBody)
    return await run_in_threadpool(_start,db,p,body,exact,sha)


@router.post('/preview-flow-causal')
async def preview_flow_causal_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-flow-causal','流程与资料来源预览路径或方法不符',422)
    body,_,_=read_wire(await request.body(),PreviewFlowCausalBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body)))


@router.post('/start-flow-causal',status_code=202)
async def start_flow_causal_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-flow-causal','流程与资料来源启动路径或方法不符',422)
    body,exact,sha=read_wire(await request.body(),StartFlowCausalBody)
    return await run_in_threadpool(_start,db,p,body,exact,sha)


@router.post('/preview-flow-nested')
async def preview_flow_nested_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/preview-flow-nested','递归流程预览路径或方法不符',422)
    body,_,_=read_wire(await request.body(),PreviewFlowNestedBody)
    return await run_in_threadpool(lambda:preview_view(prepare(db,p,body)))


@router.post('/start-flow-nested',status_code=202)
async def start_flow_nested_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-flow-nested','递归流程启动路径或方法不符',422)
    body,exact,sha=read_wire(await request.body(),StartFlowNestedBody)
    return await run_in_threadpool(_start,db,p,body,exact,sha)


@router.post('/start-flow',status_code=202)
async def start_flow_route(request:Request,p:P,db:DB):
    need(request.method=='POST' and request.url.path==PREFIX+'/start-flow','流程启动路径或方法不符',422)
    body,exact,sha=read_wire(await request.body(),StartFlowBody)
    return await run_in_threadpool(_start,db,p,body,exact,sha)


def _start(db,p,body,exact,sha):
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
    attempt_id,job_id,command_id=uid(),uid(),uid()
    row=Consumer(id=attempt_id,workspace_id=p.workspace_id,actor_id=p.user_id,
        request_id=body.request_id,producer_id=frozen['source_context']['snapshot']['producer']['id'],model_id=body.model_id,
        plan_id=body.plan_id,plan_version=body.expected_plan_version,plan_hash=body.plan_hash,scope_id=body.scope_id,
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
            idempotency_key='scoped-design-v181:'+body.request_id,data={'actor_id':p.user_id,'attempt_id':attempt_id}))
        db.flush()
        db.add(row);db.flush()
        command=Command(id=command_id,workspace_id=p.workspace_id,actor_id=p.user_id,request_id=body.request_id,
            plan_id=row.plan_id,attempt_version=row.version,body_hash=h(body.model_dump()),
            attempt_id=row.id,operation='start',method='POST',path=path,exact_body=exact,
            wire_sha256=sha,data={'schema':COMMAND,'result':result(row,'start')})
        command.command_hash=h(_command_pin(command));db.add(command);db.flush()
        audit(db,p,COMMAND_AUDIT,command.id,_command_anchor(command));_audit_state(db,p,row,None)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if getattr(getattr(error.orig,'diag',None),'constraint_name',None)=='uq_report_scoped_design_grant_v181':
            raise HTTPException(409,'本次独立活动许可已被另一提交占用；未新增调用或重用额度') from None
        raise
    except BaseException:
        db.rollback()
        raise
    return result(row,'start')


@router.get('/requests/{request_id}')
def original_request(request_id:str,p:P,db:DB,expected_sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
    route_identity(request_id);need(type(expected_sha256) is str and SHA.fullmatch(expected_sha256),'原请求SHA格式不正确',422)
    _nonce(db,p,request_id,shared=True);_any_nonce(db,p,request_id)
    _,commands,_=_inventory(db,p)
    matches=[row for row in commands.values() if row.request_id==request_id]
    if not matches:return {'schema':'report-scoped-design-request-v181','found':False,
        'request_id':request_id,'wire_sha256':expected_sha256,'result':None}
    need(len(matches)==1 and matches[0].wire_sha256==expected_sha256,'原请求摘要不一致')
    return {'schema':'report-scoped-design-request-v181','found':True,'request_id':request_id,
        'wire_sha256':matches[0].wire_sha256,'result':copy.deepcopy(matches[0].data['result'])}


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


@router.get('')
def list_route(producer_id:str,p:P,db:DB):
    route_identity(producer_id);_current_reader(db,p)
    producer=db.scalar(select(SetupReportDraft).where(SetupReportDraft.id==producer_id,
        SetupReportDraft.workspace_id==p.workspace_id,SetupReportDraft.actor_id==p.user_id)
        .execution_options(populate_existing=True))
    need(producer is not None,'原报告不存在或不属于当前账号',404)
    records,_,_=_inventory(db,p)
    items=[view(row) for row in sorted(records.values(),key=lambda row:row.id)
        if row.producer_id==producer_id]
    return {'schema':'report-scoped-design-list-v181','producer_id':producer_id,'items':items}


@router.get('/{attempt_id}')
def get_route(attempt_id:str,p:P,db:DB):
    route_identity(attempt_id);records,_,_=_inventory(db,p);return view(_owned(records,p,attempt_id))


@router.post('/{attempt_id}/recover-saved')
async def recover_route(attempt_id:str,request:Request,p:P,db:DB):
    route_identity(attempt_id);need(request.method=='POST' and request.url.path==PREFIX+'/'+attempt_id+'/recover-saved','恢复路径或方法不符合合同',422)
    body,exact,sha=read_wire(await request.body(),RecoverBody)
    return await run_in_threadpool(_recover_saved,db,p,attempt_id,body,exact,sha)


def _recover_saved(db,p,attempt_id,body,exact,sha):
    """Original recovery transaction runs wholly off the HTTP event loop."""
    _nonce(db,p,body.request_id);_any_nonce(db,p,body.request_id)
    records,commands,calls=_inventory(db,p,exclusive=True);row=_owned(records,p,attempt_id)
    path=PREFIX+'/'+attempt_id+'/recover-saved'
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
        plan_id=row.plan_id,attempt_version=row.version,body_hash=h(body.model_dump()),
        attempt_id=row.id,operation='recover-saved',method='POST',path=path,exact_body=exact,
        wire_sha256=sha,data={'schema':COMMAND,'result':result(row,'recover-saved')})
    command.command_hash=h(_command_pin(command));db.add(command);audit(db,p,COMMAND_AUDIT,command.id,_command_anchor(command))
    db.commit();return result(row,'recover-saved')


@router.post('/{attempt_id}/save-review')
async def save_review_route(attempt_id:str,request:Request,p:P,db:DB):
    route_identity(attempt_id)
    path=PREFIX+'/'+attempt_id+'/save-review'
    need(request.method=='POST' and request.url.path==path,'人工稿保存路径或方法不符',422)
    body,exact,sha=read_wire(await request.body(),SaveReviewBody)
    return await run_in_threadpool(_save_review,db,p,attempt_id,body,exact,sha,path)


def _save_review(db,p,attempt_id,body,exact,sha,path):
    """Original save and its commit share a single synchronous worker call."""
    _nonce(db,p,body.request_id);_any_nonce(db,p,body.request_id)
    records,commands,_=_inventory(db,p,exclusive=True);row=_owned(records,p,attempt_id)
    prior=_replay(commands,p,body.request_id,'save-review',path,exact,sha)
    if prior is not None:return prior
    require(p,'admin','operator');lock_current_actor(db,p)
    need(row.version==body.expected_version,'人工稿已有新版本，请保留本次修改并核对最新稿')
    need(row.status in {'review','needs_clarification','reviewed'} and row.data['projection'] is not None,
        '当前原回答尚未通过单业务核对，不能保存成已完成方案')
    value=_manual(body.value,row.data['preview'])
    family=_family(row.data['preview']['preparation'])
    if body.confirmed:
        if profiles.is_flow(family):
            confirm_value=family.wire.confirm_value
            need(not value['result']['business']['questions'],'请先处理业务流程中的待确认问题，再标记已核对',422)
        else:
            from .report_object_semantics_v166 import confirm_value
        try:
            need(same(confirm_value(value),value),'请先处理待确认问题和对象标识，再标记本业务已核对',422)
        except ValueError:
            raise HTTPException(422,'请先处理待确认问题和对象标识，再标记本业务已核对') from None
    command_id=uid()
    data={**row.data,'manual_value':copy.deepcopy(value),'command_refs':row.data['command_refs']+[
        {'id':command_id,'request_id':body.request_id,'operation':'save-review','wire_sha256':sha}]}
    questions=value['result']['business']['questions'] if profiles.is_flow(family) else value['result']['questions']
    _transition(db,p,row,data,'reviewed' if body.confirmed else ('needs_clarification' if questions else 'review'))
    command=Command(id=command_id,workspace_id=p.workspace_id,actor_id=p.user_id,request_id=body.request_id,
        plan_id=row.plan_id,attempt_id=row.id,attempt_version=row.version,body_hash=h(body.model_dump()),
        operation='save-review',method='POST',path=path,exact_body=exact,wire_sha256=sha,
        data={'schema':COMMAND,'result':result(row,'save-review')})
    command.command_hash=h(_command_pin(command));db.add(command)
    audit(db,p,COMMAND_AUDIT,command.id,_command_anchor(command));db.commit()
    return result(row,'save-review')


def _lease(db,p,job,row):
    lease=ownership(db)
    need(job.workspace_id==p.workspace_id and job.id==row.job_id and job.kind==JOB
        and job.status=='running' and job.data.get('actor_id')==p.user_id
        and job.data.get('attempt_id')==row.id and type(lease) is dict
        and lease.get('job_id')==job.id and type(job.lease_token) is str
        and lease.get('lease_token')==job.lease_token,
        '原新方案Job的实际租约或执行主体无法核对')


def run(db,p,job):
    """Normal Worker seam. A second ownership never initiates another POST.

    Central dispatch must wrap cloud_dispatch_v130 with dispatch_identity(row).
    on_reserved returns the exact v2 binding after committing the single real
    CloudCall association; it is called before transport and is not a sink.
    """
    records,_,calls=_inventory(db,p,exclusive=True)
    row=_owned(records,p,job.data.get('attempt_id'));_lease(db,p,job,row)
    if row.status in {'review','needs_clarification','reviewed','failed'}:return view(row)
    if row.data['dispatch_started'] is True:
        if row.id in calls:_associate(db,p,row,calls[row.id])
        data,status=_read_paid(db,p,row,recover_saved=True)
        _transition(db,p,row,data,status);db.commit();return view(row)
    family=_family(row.data['preview']['preparation'])
    body=_preview_model(family).model_validate(row.data['preview']['body'])
    require(p,'admin','operator')
    fresh=prepare(db,p,body,lock=True,consumer=row)
    need(same(fresh,row.data['preview']),'本次来源、当前模型或独立费用许可已变化；未发送')
    request=family.preparation.rebuild_request(fresh['preparation'],_cap(fresh['capability']),fresh['source_context'],
        fresh['scope_plan'],body.scope_id,note=body.note)
    spec=family.preparation.stage_spec(fresh['preparation'])
    _transition(db,p,row,{**row.data,'dispatch_started':True,'dispatch_identity':dispatch_identity(row)},'running')
    # Intent is durable before entering even reservation. A kill in this gap is
    # deliberately unknown, not permission to reserve/send on a later attempt.
    checkpoint(db)
    def on_reserved(call_id):
        try:
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
    records,_,calls=_inventory(db,p,exclusive=True);row=_owned(records,p,job.data.get('attempt_id'))
    _lease(db,p,job,row)
    if row.status in {'review','needs_clarification','reviewed','failed'}:return
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


