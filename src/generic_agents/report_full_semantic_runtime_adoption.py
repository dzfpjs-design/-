"""Explicit, durable adoption of a supported workspace workflow design.

Position proof remains position proof. A separate strict consent command adopts
the displayed single manual workflow; it never certifies source facts or grants
an account/device new permissions. No model, worker or external action is called.
"""
import copy
from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from .contracts import digest
from .models import Membership, SetupRevision, User, uid
from .report_reading_drafts import SafeContract
from . import report_full_semantic_source_review as review
from . import report_full_semantic_region_mapping as region
from . import report_full_semantic_runtime_v2 as runtime
from .report_full_semantic_runtime_models import FullSemanticRuntimeAdoption as Adoption
from .services import audit, require


router = APIRouter()
PREFIX = '/full-semantic-runtimes/{identity}'
LEDGER = 'report-full-semantic-runtime-ledger-v2'
RECORD = 'report-full-semantic-runtime-record-v2'
RECEIPT = 'report-full-semantic-runtime-receipt-v2'
HASH = r'^[a-f0-9]{64}$'


def fail(code, message, status=409):
    raise HTTPException(status, {'code': 'full_runtime_' + code, 'message': message})


class Preview(SafeContract):
    schema: Literal['report-full-semantic-runtime-preview-command-v2']
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=HASH)
    proof_id: UUID
    business_name: str = Field(strict=True, min_length=1, max_length=100)
    actor_id: UUID


class Adopt(Preview):
    schema: Literal['report-full-semantic-runtime-command-v2']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=HASH)
    consent: Literal[True]
    semantic_mode: Literal['workspace_workflow_design']

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError('请独立明确采用所显示的工作区执行设计；引用定位不代表语义同意')
        return value


def _seal(value, key):
    runtime._safe(value)
    return {**value, key: digest(value)}


def preview_command(body):
    values = body.model_dump(mode='json')
    return Preview.model_validate({key: ('report-full-semantic-runtime-preview-command-v2'
        if key == 'schema' else values[key]) for key in Preview.model_fields})


def command_hash(p, identity, body):
    return digest({'schema': 'report-full-semantic-runtime-command-hash-v2',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'handoff_id': str(identity),
        'command': body.model_dump(mode='json')})


def adoption_id(p, identity, request_id):
    return str(uuid5(UUID(str(identity)), 'full-semantic-runtime-v2:' + p.workspace_id
        + ':' + p.user_id + ':' + str(request_id)))


def executor(db, p, actor_id, *, lock=False):
    query = select(User, Membership).join(Membership, Membership.user_id == User.id).where(
        User.id == str(actor_id), User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator']))
    if lock:
        query = query.with_for_update(read=True, of=[User, Membership]).execution_options(populate_existing=True)
    row = db.execute(query).first()
    if row is None:
        fail('actor', '执行人必须是当前工作区仍有权限的真实账号；此操作不会授予权限')
    user, membership = row
    return {'user_id': user.id, 'username': user.username, 'membership_role': membership.role}


def make_preview(p, identity, body, binding):
    return _seal({'schema': 'report-full-semantic-runtime-preview-v2', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'handoff_id': str(identity),
        'command': preview_command(body).model_dump(mode='json'),
        'source_business': copy.deepcopy(binding['source_business']), 'binding': copy.deepcopy(binding),
        'execution_boundary': {'trigger_mode': 'manual', 'manual_result_required': True,
            'steps': 1, 'automatic_tools': False, 'device_actions': False,
            'independent_conditions': False, 'interactions': False, 'parallel_execution': False},
        'semantic_mode': runtime.SEMANTIC_MODE, 'source_semantics_status': 'not_certified',
        'consent_scope': '采用本预览的工作区人工执行设计；不确认原文事实，也不代替独立的数据结构采用'}, 'preview_hash')


def _formal(db, p, identity, proof_id, ctx, *, lock=False):
    public = region.get_proof(UUID(str(identity)), UUID(str(proof_id)), p, db)
    if public.get('current_source_verified') is not True:
        fail('source', '正式副本、原件或引用位置已变化，请先核对当前来源')
    proof = public['proof']
    if lock:
        _, _, _, _, confirmation = region.current_source(db, p, ctx[5], proof['selection'], formal=True, lock=True)
        if confirmation.id != proof['confirmation_id']:
            fail('source', '正式副本确认记录已变化，原输入保留')
    return proof


def prepare(db, p, identity, body, *, lock=False):
    ctx = review.context(db, p, identity, lock=lock)
    configuration, record, heads, manifest, _, _ = ctx
    if (str(identity) not in heads or body.configuration_revision != configuration.version
            or body.configuration_hash != digest(configuration.data)):
        fail('stale', '当前设计或工作区配置已变化，请保留输入并重新预览')
    bindings = configuration.data.get(runtime.CONFIG_KEY, {})
    if type(bindings) is not dict or len(bindings) >= 20:
        fail('capacity', '执行设计采用清单无法核对或已达上限，原配置保留')
    if body.business_name in bindings:
        fail('already_adopted', '此业务已有明确采用记录；请保留原回执，不会覆盖负责人')
    existing = db.scalar(select(Adoption).where(Adoption.workspace_id == p.workspace_id,
        Adoption.handoff_id == str(identity), Adoption.business_name == body.business_name))
    if existing is not None:
        fail('already_adopted', '此业务的原采用记录仍在；不能删除配置标记后重新绑定')
    proof = _formal(db, p, identity, body.proof_id, ctx, lock=lock)
    actor = executor(db, p, body.actor_id, lock=lock)
    try:
        binding = runtime.build_runtime_binding(record['design'], manifest, proof,
            body.business_name, actor, proof_id=str(body.proof_id))
        expected_flow = review.full.pure().derive_flows(record['design'])[body.business_name]
        if configuration.data.get('flows', {}).get(body.business_name) != expected_flow:
            fail('flow_changed', '原业务步骤、来源标记或确认字段已有变化，不能按另一流程采用')
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        fail('unsupported', '当前仅支持原设计确实只有一个人工步骤、无条件分支或协同的业务；原完整设计保持不可执行')
    return configuration, make_preview(p, identity, body, binding)


def make_receipt(record):
    return _seal({'schema': RECEIPT, **{key: record[key] for key in (
        'id', 'actor_id', 'workspace_id', 'handoff_id', 'business_name', 'request_id',
        'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash', 'record_hash')},
        'binding_hash': record['binding']['binding_hash'], 'origin_hash': record['origin']['origin_hash']}, 'receipt_hash')


def lookup_adoption(db, identity, *, lock=False):
    """Resolve an immutable root or appended revision by its exact identity."""
    if lock:
        row = db.scalar(select(Adoption).where(Adoption.id == str(identity)).with_for_update())
    else:
        row = db.get(Adoption, str(identity))
    if row is not None:
        return row
    from .report_full_semantic_runtime_revision_models import FullSemanticRuntimeRevision
    if lock:
        return db.scalar(select(FullSemanticRuntimeRevision).where(
            FullSemanticRuntimeRevision.id == str(identity)).with_for_update())
    return db.get(FullSemanticRuntimeRevision, str(identity))


def checked_ledger(row, p, *, private=False):
    if type(getattr(row, 'data', None)) is dict and row.data.get('schema') == 'report-full-semantic-runtime-revision-ledger-v1':
        from .report_full_semantic_runtime_revision import checked_ledger as checked_revision
        return checked_revision(row, p, private=private)
    try:
        if row is None or row.workspace_id != p.workspace_id or private and row.actor_id != p.user_id:
            fail('not_found', '未找到此工作区的执行设计采用记录', 404)
        data = row.data
        runtime._keys(data, {'schema', 'record', 'command', 'receipt', 'ledger_hash'}, 'ledger_shape')
        runtime._need(data['schema'] == LEDGER and row.version == 1, 'ledger_version')
        runtime._safe(data); runtime._sealed(data, 'ledger_hash')
        record = data['record']
        runtime._keys(record, {'schema', 'id', 'workspace_id', 'actor_id', 'target_actor_id',
            'handoff_id', 'configuration_id', 'request_id', 'business_name', 'proof_id', 'binding',
            'origin', 'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash', 'record_hash'}, 'record_shape')
        runtime._sealed(record, 'record_hash')
        runtime._need(record['schema'] == RECORD and all(record[key] == getattr(row, key) for key in (
            'id', 'workspace_id', 'actor_id', 'target_actor_id', 'handoff_id', 'configuration_id',
            'request_id', 'business_name', 'proof_id')), 'record_identity')
        for key in ('id', 'workspace_id', 'actor_id', 'target_actor_id', 'handoff_id', 'configuration_id', 'request_id', 'proof_id'):
            runtime._uuid(record[key])
        body = Adopt.model_validate(data['command'])
        owner = type('Owner', (), {'workspace_id': row.workspace_id, 'user_id': row.actor_id})()
        binding = runtime.check_runtime_binding(record['binding'])
        runtime.check_origin(record['origin'], binding)
        runtime._need(row.id == adoption_id(owner, row.handoff_id, body.request_id)
            and str(body.request_id) == row.request_id and str(body.actor_id) == row.target_actor_id
            and str(body.proof_id) == row.proof_id and body.business_name == row.business_name
            and record['origin']['adoption_id'] == row.id
            and binding['handoff_id'] == row.handoff_id and binding['business_name'] == row.business_name
            and binding['proof_id'] == row.proof_id and binding['executor']['user_id'] == row.target_actor_id
            and record['accepted_revision'] == body.configuration_revision + 1
            and record['command_hash'] == command_hash(owner, row.handoff_id, body)
            and record['preview_hash'] == body.preview_hash
            and make_preview(owner, row.handoff_id, body, binding)['preview_hash'] == body.preview_hash
            and data['receipt'] == make_receipt(record), 'command_or_receipt')
        return data
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        fail('integrity', '采用命令、明确同意或保存回执无法冷核；原记录保留')


def accepted_history(db, p, row, data):
    if data['schema'] == 'report-full-semantic-runtime-revision-ledger-v1':
        from .report_full_semantic_runtime_revision import accepted_history as revision_history
        return revision_history(db, p, row, data)
    record = data['record']; body = Adopt.model_validate(data['command'])
    versions = {}
    for version in (body.configuration_revision, record['accepted_revision']):
        item = db.scalar(select(SetupRevision).where(SetupRevision.workspace_id == p.workspace_id,
            SetupRevision.draft_id == row.configuration_id, SetupRevision.version == version))
        if item is None:
            fail('history', '采用前后的工作区配置历史缺失，原回执保留')
        versions[version] = item.data
    before, after = versions[body.configuration_revision], versions[record['accepted_revision']]
    expected = copy.deepcopy(before)
    if (type(expected.get(runtime.CONFIG_KEY, {})) is not dict
            or row.business_name in expected.get(runtime.CONFIG_KEY, {})):
        fail('history', '原采用动作不能覆盖已有执行设计')
    expected.setdefault(runtime.CONFIG_KEY, {})[row.business_name] = copy.deepcopy(record['origin'])
    if (digest(before) != body.configuration_hash or after != expected
            or digest(after) != record['configuration_hash']
            or record['binding']['source_design'] not in before.get('fullSemanticDesigns', [])
            or before.get('flows', {}).get(row.business_name) != runtime.semantic.derive_flows(
                record['binding']['source_design'])[row.business_name]):
        fail('history', '原采用动作与保存前后配置不一致，不能由自洽摘要替代真实历史')
    return after


def verify_origin(db, p, origin, *, require_current=True):
    """Verify a current draft head or a frozen historical release binding.

    Frozen releases retain their exact adopted executor after draft revisions.
    They still require an authentic accepted ledger, current formal source and
    current membership; this does not keep revoked accounts executable.
    """
    try:
        runtime._keys(origin, runtime._ORIGIN_KEYS, 'origin_shape')
        row = lookup_adoption(db, origin['adoption_id'])
        data = checked_ledger(row, p)
        if origin != data['record']['origin']:
            fail('integrity', '配置中的执行设计来源与实际采用记录不一致')
        accepted_history(db, p, row, data)
        ctx = review.context(db, p, row.handoff_id)
        configuration, record, heads, manifest, _, _ = ctx
        binding = data['record']['binding']
        if (configuration.id != row.configuration_id
                or record['design'] != binding['source_design'] or manifest != binding['source_manifest']):
            fail('stale', '执行设计、采用来源或完整设计已有变化；不会继续旧授权')
        if require_current:
            from .report_full_semantic_runtime_revision import require_head
            require_head(db, p, row)
            if (row.handoff_id not in heads
                    or configuration.data.get(runtime.CONFIG_KEY, {}).get(row.business_name) != origin
                    or configuration.data.get('flows', {}).get(row.business_name) != runtime.semantic.derive_flows(
                        binding['source_design'])[row.business_name]):
                fail('stale', '当前执行设计采用版本已变化；不会恢复旧负责人')
        proof = _formal(db, p, row.handoff_id, row.proof_id, ctx)
        actor = executor(db, p, row.target_actor_id)
        expected = runtime.build_runtime_binding(record['design'], manifest, proof,
            row.business_name, actor, proof_id=row.proof_id)
        if expected != binding:
            fail('stale', '正式来源、负责人账号或冻结执行范围已有变化，原结果保留')
        return copy.deepcopy(binding)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        fail('integrity', '执行设计来源无法冷核，原配置和记录保留')


def validated_bindings(db, p, config):
    values = config.get(runtime.CONFIG_KEY, {})
    if type(values) is not dict or len(values) > 20:
        fail('configuration', '工作区执行设计采用清单无法核对')
    result = []
    for name in sorted(values):
        origin = values[name]
        if type(name) is not str or type(origin) is not dict or origin.get('business_name') != name:
            fail('configuration', '执行设计采用清单的业务与来源不一致')
        binding = verify_origin(db, p, origin)
        if binding['source_design'] not in config.get('fullSemanticDesigns', []):
            fail('configuration', '配置删除或替换了采用动作对应的完整设计')
        result.append({'origin': copy.deepcopy(origin), 'binding': binding})
    return result


def protect_configuration(db, p, previous, proposed):
    """Ordinary saves cannot add, remove or rebind an independently adopted grant."""
    if previous.get(runtime.CONFIG_KEY, {}) != proposed.get(runtime.CONFIG_KEY, {}):
        fail('adoption_command_required', '执行设计采用记录不能由普通配置保存添加、删除或改写')
    # Retaining a historical adoption must remain possible when an actor/source
    # becomes unavailable. Compilation/execution, rather than unrelated saves,
    # own the separate live-source and current-permission gate.
    values = proposed.get(runtime.CONFIG_KEY, {})
    if type(values) is not dict or len(values) > 20:
        fail('configuration', '工作区执行设计采用清单无法核对')
    for name, origin in values.items():
        try:
            runtime._keys(origin, runtime._ORIGIN_KEYS, 'origin_shape')
            row = lookup_adoption(db, origin['adoption_id'])
            data = checked_ledger(row, p)
            accepted_history(db, p, row, data)
            if type(name) is not str or name != row.business_name or origin != data['record']['origin']:
                fail('integrity', '配置中的执行设计采用标记与真实历史不一致')
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            fail('integrity', '执行设计采用标记或历史无法核对，原配置保留')
    return copy.deepcopy(proposed)


def reply(db, p, row):
    if type(getattr(row, 'data', None)) is dict and row.data.get('schema') == 'report-full-semantic-runtime-revision-ledger-v1':
        from .report_full_semantic_runtime_revision import reply as revision_reply
        return revision_reply(db, p, row)
    data = checked_ledger(row, p, private=True); accepted_history(db, p, row, data)
    configuration = review.full.old().configuration_row(db, p)
    return {'schema': 'report-full-semantic-runtime-adoption-result-v2',
        'receipt': copy.deepcopy(data['receipt']), 'origin': copy.deepcopy(data['record']['origin']),
        'configuration': review.full.old().setup().draft_view(configuration),
        'current_matches': configuration.version == data['record']['accepted_revision']
            and digest(configuration.data) == data['record']['configuration_hash'],
        'source_semantics_status': 'not_certified'}


@router.post(PREFIX + '/preview')
def preview(identity: UUID, body: Preview, p: review.pipeline.ReadP, db: review.pipeline.DB):
    require(p, 'admin', 'operator')
    _, result = prepare(db, p, identity, body)
    return result


@router.post(PREFIX + '/adopt')
def adopt(identity: UUID, body: Adopt, p: review.pipeline.P, db: review.pipeline.DB):
    require(p, 'admin', 'operator')
    # A UUID is globally bound within this independent command namespace.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key': 'full-semantic-runtime-adoption-v2:' + str(body.request_id)})
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    prior = db.scalar(select(Adoption).where(Adoption.request_id == str(body.request_id)))
    if prior is not None:
        if (prior.workspace_id != p.workspace_id or prior.actor_id != p.user_id
                or prior.handoff_id != str(identity)):
            fail('nonce_conflict', '此采用请求编号已绑定其他身份或工作区，原记录保留')
        data = checked_ledger(prior, p, private=True)
        if command_hash(p, identity, body) != data['record']['command_hash']:
            fail('nonce_conflict', '原采用请求不能更换业务、负责人或执行范围')
        return reply(db, p, prior)
    configuration, prepared = prepare(db, p, identity, body, lock=True)
    if prepared['preview_hash'] != body.preview_hash:
        fail('stale', '预览、来源、执行人或配置已有变化，请重新核对')
    identity_value = adoption_id(p, identity, body.request_id)
    binding = prepared['binding']; origin = runtime.build_origin(identity_value, binding)
    proposed = copy.deepcopy(configuration.data)
    proposed.setdefault(runtime.CONFIG_KEY, {})[body.business_name] = copy.deepcopy(origin)
    review.full.old().setup().validate_configuration(proposed)
    # Final reviewer and executor locks are held through the atomic commit.
    lock_current_actor(db, p)
    if executor(db, p, body.actor_id, lock=True) != binding['executor']:
        fail('actor', '执行人状态在采用期间变化，原输入保留')
    record = _seal({'schema': RECORD, 'id': identity_value, 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'target_actor_id': str(body.actor_id), 'handoff_id': str(identity),
        'configuration_id': configuration.id, 'request_id': str(body.request_id),
        'business_name': body.business_name, 'proof_id': str(body.proof_id),
        'binding': binding, 'origin': origin, 'command_hash': command_hash(p, identity, body),
        'preview_hash': body.preview_hash, 'accepted_revision': configuration.version + 1,
        'configuration_hash': digest(proposed)}, 'record_hash')
    data = _seal({'schema': LEDGER, 'record': record, 'command': body.model_dump(mode='json'),
        'receipt': make_receipt(record)}, 'ledger_hash')
    row = Adoption(id=identity_value, workspace_id=p.workspace_id, actor_id=p.user_id,
        target_actor_id=str(body.actor_id), handoff_id=str(identity), proof_id=str(body.proof_id),
        configuration_id=configuration.id, request_id=str(body.request_id), business_name=body.business_name,
        version=1, data=data)
    checked_ledger(row, p, private=True)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=configuration.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(row); configuration.data = proposed; configuration.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=configuration.id, version=configuration.version, data=proposed))
    audit(db, p, 'setup.full_semantic_runtime.adopted', row.id, {'request_id': str(body.request_id),
        'record_hash': record['record_hash'], 'origin_hash': origin['origin_hash'],
        'accepted_revision': configuration.version, 'semantic_mode': runtime.SEMANTIC_MODE})
    db.commit()
    return reply(db, p, row)


@router.get(PREFIX + '/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    row = db.scalar(select(Adoption).where(Adoption.workspace_id == p.workspace_id,
        Adoption.actor_id == p.user_id, Adoption.handoff_id == str(identity), Adoption.request_id == str(request_id)))
    if row is None:
        return {'schema': 'report-full-semantic-runtime-recovery-v2', 'found': False, 'request_id': str(request_id)}
    return {'schema': 'report-full-semantic-runtime-recovery-v2', 'found': True, **reply(db, p, row)}
