"""Explicit append-only revisions of a separately adopted manual executor.

Changing a draft's current binding never edits the preceding adoption, a release
snapshot or a task. Source proofs and current member permissions remain separate
from the independent consent to the new workspace execution design.
"""
import copy
from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from .contracts import digest
from .models import SetupRevision, uid
from .report_reading_drafts import SafeContract
from . import report_full_semantic_runtime_adoption as adoption
from . import report_full_semantic_runtime_v2 as runtime
from .report_full_semantic_runtime_revision_models import FullSemanticRuntimeRevision as Revision


router = APIRouter()
PREFIX = adoption.PREFIX + '/revisions'
LEDGER = 'report-full-semantic-runtime-revision-ledger-v1'
RECORD = 'report-full-semantic-runtime-revision-record-v1'
RECEIPT = 'report-full-semantic-runtime-revision-receipt-v1'
_RECORD_KEYS = {'schema', 'id', 'workspace_id', 'actor_id', 'target_actor_id', 'handoff_id',
    'configuration_id', 'request_id', 'business_name', 'proof_id', 'binding', 'origin',
    'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash', 'record_hash',
    'parent_id', 'root_id', 'parent_receipt_hash', 'previous_executor'}


class Parent(SafeContract):
    id: UUID
    receipt_hash: str = Field(strict=True, pattern=adoption.HASH)


class Preview(adoption.Preview):
    schema: Literal['report-full-semantic-runtime-revision-preview-command-v1']
    parent: Parent


class Adopt(Preview):
    schema: Literal['report-full-semantic-runtime-revision-command-v1']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=adoption.HASH)
    consent: Literal[True]
    semantic_mode: Literal['workspace_workflow_design']

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError('请独立确认新的业务负责人；原回执和既有任务不会被改写')
        return value


def preview_command(body):
    values = body.model_dump(mode='json')
    return Preview.model_validate({key: ('report-full-semantic-runtime-revision-preview-command-v1'
        if key == 'schema' else values[key]) for key in Preview.model_fields})


def command_hash(p, identity, body):
    return digest({'schema': 'report-full-semantic-runtime-revision-command-hash-v1',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'handoff_id': str(identity),
        'command': body.model_dump(mode='json')})


def revision_id(p, identity, request_id):
    return str(uuid5(UUID(str(identity)), 'full-semantic-runtime-revision-v1:' + p.workspace_id
        + ':' + p.user_id + ':' + str(request_id)))


def make_preview(p, identity, body, binding, previous_executor):
    legacy = adoption.Preview.model_validate({key: ('report-full-semantic-runtime-preview-command-v2'
        if key == 'schema' else body.model_dump(mode='json')[key]) for key in adoption.Preview.model_fields})
    value = adoption.make_preview(p, identity, legacy, binding)
    value.pop('preview_hash')
    value.update(schema='report-full-semantic-runtime-revision-preview-v1',
        command=preview_command(body).model_dump(mode='json'),
        previous_executor=copy.deepcopy(previous_executor),
        effect_scope='future_releases_only',
        consent_scope='确认本预览的新负责人及人工执行范围；原回执、已生成版本和既有任务保持原记录')
    return adoption._seal(value, 'preview_hash')


def make_receipt(record):
    return adoption._seal({'schema': RECEIPT, **{key: record[key] for key in (
        'id', 'actor_id', 'workspace_id', 'handoff_id', 'business_name', 'request_id',
        'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash', 'record_hash',
        'parent_id', 'root_id', 'parent_receipt_hash')},
        'binding_hash': record['binding']['binding_hash'], 'origin_hash': record['origin']['origin_hash']}, 'receipt_hash')


def checked_ledger(row, p, *, private=False):
    """Pure closed-shape check; actual parent and accepted revisions are separate."""
    try:
        if row is None or row.workspace_id != p.workspace_id or private and row.actor_id != p.user_id:
            adoption.fail('not_found', '未找到此工作区的负责人修订记录', 404)
        data = row.data
        runtime._keys(data, {'schema', 'record', 'command', 'receipt', 'ledger_hash'}, 'revision_ledger')
        runtime._need(data['schema'] == LEDGER and row.version == 1, 'revision_version')
        runtime._safe(data); runtime._sealed(data, 'ledger_hash')
        record = data['record']; runtime._keys(record, _RECORD_KEYS, 'revision_record')
        runtime._sealed(record, 'record_hash')
        runtime._need(record['schema'] == RECORD and all(record[key] == getattr(row, key) for key in (
            'id', 'workspace_id', 'actor_id', 'target_actor_id', 'handoff_id', 'configuration_id',
            'request_id', 'business_name', 'proof_id', 'parent_id', 'root_id')), 'revision_identity')
        for key in ('id', 'workspace_id', 'actor_id', 'target_actor_id', 'handoff_id', 'configuration_id',
                'request_id', 'proof_id', 'parent_id', 'root_id'):
            runtime._uuid(record[key])
        body = Adopt.model_validate(data['command'])
        runtime._need(data['command'] == body.model_dump(mode='json'), 'revision_command_shape')
        owner = type('Owner', (), {'workspace_id': row.workspace_id, 'user_id': row.actor_id})()
        binding = runtime.check_runtime_binding(record['binding'])
        runtime.check_origin(record['origin'], binding)
        runtime._need(row.id == revision_id(owner, row.handoff_id, body.request_id)
            and row.parent_id != row.id and str(body.parent.id) == row.parent_id
            and body.parent.receipt_hash == record['parent_receipt_hash']
            and str(body.request_id) == row.request_id and str(body.actor_id) == row.target_actor_id
            and str(body.proof_id) == row.proof_id and body.business_name == row.business_name
            and record['origin']['adoption_id'] == row.id
            and binding['handoff_id'] == row.handoff_id and binding['business_name'] == row.business_name
            and binding['proof_id'] == row.proof_id and binding['executor']['user_id'] == row.target_actor_id
            and record['accepted_revision'] == body.configuration_revision + 1
            and record['command_hash'] == command_hash(owner, row.handoff_id, body)
            and record['preview_hash'] == body.preview_hash
            and make_preview(owner, row.handoff_id, body, binding, record['previous_executor'])['preview_hash'] == body.preview_hash
            and data['receipt'] == make_receipt(record), 'revision_command_or_receipt')
        return data
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        adoption.fail('integrity', '负责人修订命令、明确同意或回执无法冷核；原记录保留')


def _descends(records, children, previous, current):
    visited = set(); identity = previous
    while identity in records and identity not in visited:
        if identity == current:
            return True
        visited.add(identity); identity = children.get(identity)
    return False


def _lineage(db, p, previous, current):
    if previous == current:
        return
    from .report_full_semantic_replacement import record_set
    records, _, children = record_set(db, p)
    if _descends(records, children, previous, current):
        return
    adoption.fail('parent', '新的完整设计不属于原采用记录的后续版本，不能按同名业务改绑')


def _parent(db, p, row, data, *, lock=False):
    parent = adoption.lookup_adoption(db, row.parent_id, lock=lock)
    prior = adoption.checked_ledger(parent, p)
    record, previous = data['record'], prior['record']
    root_id = parent.root_id if prior['schema'] == LEDGER else parent.id
    if (root_id != row.root_id or parent.configuration_id != row.configuration_id
            or parent.business_name != row.business_name
            or prior['receipt']['receipt_hash'] != record['parent_receipt_hash']
            or previous['binding']['executor'] != record['previous_executor']
            or previous['binding'] == record['binding']
            or previous['accepted_revision'] >= record['accepted_revision']):
        adoption.fail('history', '负责人修订的前版本、回执或工作区范围不一致，原记录保留')
    _lineage(db, p, parent.handoff_id, row.handoff_id)
    return parent, prior


def _history_step(db, p, row, data, prior):
    record = data['record']; body = Adopt.model_validate(data['command']); versions = {}
    for version in (body.configuration_revision, record['accepted_revision']):
        item = db.scalar(select(SetupRevision).where(SetupRevision.workspace_id == p.workspace_id,
            SetupRevision.draft_id == row.configuration_id, SetupRevision.version == version))
        if item is None:
            adoption.fail('history', '负责人修订前后的真实配置历史缺失，原回执保留')
        versions[version] = item.data
    before, after = versions[body.configuration_revision], versions[record['accepted_revision']]
    expected = copy.deepcopy(before)
    if (type(expected.get(runtime.CONFIG_KEY)) is not dict
            or expected[runtime.CONFIG_KEY].get(row.business_name) != prior['record']['origin']):
        adoption.fail('history', '负责人修订未准确接续原配置中的采用记录')
    expected[runtime.CONFIG_KEY][row.business_name] = copy.deepcopy(record['origin'])
    if (digest(before) != body.configuration_hash or after != expected
            or digest(after) != record['configuration_hash']
            or record['binding']['source_design'] not in before.get('fullSemanticDesigns', [])
            or before.get('flows', {}).get(row.business_name) != runtime.semantic.derive_flows(
                record['binding']['source_design'])[row.business_name]):
        adoption.fail('history', '负责人修订与接受版本、业务步骤或来源设计不一致')
    return after


def accepted_history(db, p, row, data):
    """Iteratively cold-check the whole real chain, including its legacy root."""
    current, value = row, data; visited = set(); result = None
    while value['schema'] == LEDGER:
        if current.id in visited:
            adoption.fail('history', '负责人修订历史存在循环，原回执保留')
        visited.add(current.id)
        parent, prior = _parent(db, p, current, value)
        after = _history_step(db, p, current, value, prior)
        if result is None:
            result = after
        current, value = parent, prior
    adoption.accepted_history(db, p, current, value)
    return result


def require_head(db, p, row):
    child = db.scalar(select(Revision).where(Revision.parent_id == row.id))
    if child is not None:
        adoption.fail('parent_stale', '此采用记录已有后续修订，请保留原输入并读取当前版本')


def prepare(db, p, identity, body, *, lock=False):
    ctx = adoption.review.context(db, p, identity, lock=lock)
    configuration, record, heads, manifest, _, _ = ctx
    if (str(identity) not in heads or body.configuration_revision != configuration.version
            or body.configuration_hash != digest(configuration.data)):
        adoption.fail('stale', '当前完整设计或配置已变化，请保留输入并重新预览')
    parent = adoption.lookup_adoption(db, str(body.parent.id), lock=lock)
    prior = adoption.checked_ledger(parent, p)
    adoption.accepted_history(db, p, parent, prior)
    bindings = configuration.data.get(runtime.CONFIG_KEY, {})
    if (parent.configuration_id != configuration.id or parent.business_name != body.business_name
            or prior['receipt']['receipt_hash'] != body.parent.receipt_hash
            or type(bindings) is not dict or len(bindings) > 20
            or bindings.get(body.business_name) != prior['record']['origin']):
        adoption.fail('parent', '原负责人回执与当前业务配置不一致，请读取当前采用版本')
    require_head(db, p, parent)
    _lineage(db, p, parent.handoff_id, str(identity))
    proof = adoption._formal(db, p, identity, body.proof_id, ctx, lock=lock)
    actor = adoption.executor(db, p, body.actor_id, lock=lock)
    try:
        binding = runtime.build_runtime_binding(record['design'], manifest, proof,
            body.business_name, actor, proof_id=str(body.proof_id))
        if configuration.data.get('flows', {}).get(body.business_name) != runtime.semantic.derive_flows(
                record['design'])[body.business_name]:
            adoption.fail('flow_changed', '当前业务步骤已有变化，不能采用另一份执行范围')
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        adoption.fail('unsupported', '当前仅支持单个人工步骤且无分支或协同；复杂业务原记录保留')
    if binding == prior['record']['binding']:
        adoption.fail('unchanged', '负责人和执行设计均未变化，无需再次确认')
    role = binding['source_business']['steps'][0]['role']
    for name, other in bindings.items():
        if name == body.business_name:
            continue
        if type(other) is not dict:
            adoption.fail('configuration', '其他业务的负责人记录无法核对，原配置保留')
        if other.get('role') == role and (other.get('actor_id') != actor['user_id']
                or other.get('actor_username') != actor['username']):
            adoption.fail('shared_role', '此职责同时用于其他业务；需共同核对负责人，不能只改其中一项造成执行范围冲突')
    return configuration, parent, prior, make_preview(p, identity, body, binding, prior['record']['binding']['executor'])


def reply(db, p, row):
    data = checked_ledger(row, p, private=True); accepted_history(db, p, row, data)
    configuration = adoption.review.full.old().configuration_row(db, p)
    return {'schema': 'report-full-semantic-runtime-revision-result-v1',
        'receipt': copy.deepcopy(data['receipt']), 'origin': copy.deepcopy(data['record']['origin']),
        'configuration': adoption.review.full.old().setup().draft_view(configuration),
        'current_matches': configuration.version == data['record']['accepted_revision']
            and digest(configuration.data) == data['record']['configuration_hash'],
        'source_semantics_status': 'not_certified', 'effect_scope': 'future_releases_only'}


@router.get(PREFIX)
def state(identity: UUID, p: adoption.review.pipeline.ReadP, db: adoption.review.pipeline.DB):
    """Expose authoritative current parent receipts without private command data."""
    ctx = adoption.review.context(db, p, identity)
    configuration = ctx[0]
    if str(identity) not in ctx[2]:
        adoption.fail('stale', '完整设计已有后续版本，请读取当前版本后修订')
    from .report_full_semantic_replacement import record_set
    records, _, children = record_set(db, p)
    values = configuration.data.get(runtime.CONFIG_KEY, {})
    if type(values) is not dict or len(values) > 20:
        adoption.fail('configuration', '工作区负责人采用清单无法核对')
    bindings = []
    for name, origin in values.items():
        try:
            runtime._keys(origin, runtime._ORIGIN_KEYS, 'origin_shape')
            row = adoption.lookup_adoption(db, origin['adoption_id'])
            data = adoption.checked_ledger(row, p)
            adoption.accepted_history(db, p, row, data)
            if (row.configuration_id != configuration.id or row.business_name != name
                    or data['record']['origin'] != origin):
                adoption.fail('integrity', '当前业务负责人来源与真实采用记录不一致')
            require_head(db, p, row)
            if not _descends(records, children, row.handoff_id, str(identity)):
                continue
            bindings.append({'business_name': name, 'origin': copy.deepcopy(origin),
                'receipt': copy.deepcopy(data['receipt']),
                'executor': copy.deepcopy(data['record']['binding']['executor']),
                'root_id': row.root_id if data['schema'] == LEDGER else row.id,
                'parent_id': row.parent_id if data['schema'] == LEDGER else None})
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            adoption.fail('integrity', '当前业务负责人采用清单无法冷核，原记录保留')
    return {'schema': 'report-full-semantic-runtime-revision-state-v1', 'handoff_id': str(identity),
        'configuration': {'revision': configuration.version, 'hash': digest(configuration.data)},
        'bindings': bindings}


@router.post(PREFIX + '/preview')
def preview(identity: UUID, body: Preview, p: adoption.review.pipeline.ReadP, db: adoption.review.pipeline.DB):
    adoption.require(p, 'admin', 'operator')
    return prepare(db, p, identity, body)[3]


@router.post(PREFIX + '/adopt')
def adopt(identity: UUID, body: Adopt, p: adoption.review.pipeline.P, db: adoption.review.pipeline.DB):
    adoption.require(p, 'admin', 'operator')
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key': 'full-semantic-runtime-revision-v1:' + str(body.request_id)})
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    existing = db.scalar(select(Revision).where(Revision.request_id == str(body.request_id)))
    if existing is not None:
        if (existing.workspace_id != p.workspace_id or existing.actor_id != p.user_id
                or existing.handoff_id != str(identity)):
            adoption.fail('nonce_conflict', '修订请求已绑定其他身份或工作区，原记录保留')
        data = checked_ledger(existing, p, private=True)
        if data['record']['command_hash'] != command_hash(p, identity, body):
            adoption.fail('nonce_conflict', '原修订请求不能更换前版本、负责人或执行范围')
        return reply(db, p, existing)
    configuration, parent, prior, prepared = prepare(db, p, identity, body, lock=True)
    if prepared['preview_hash'] != body.preview_hash:
        adoption.fail('stale', '修订预览、来源、负责人或配置已变化，请保留输入重新预览')
    identity_value = revision_id(p, identity, body.request_id)
    binding = prepared['binding']; origin = runtime.build_origin(identity_value, binding)
    proposed = copy.deepcopy(configuration.data)
    proposed[runtime.CONFIG_KEY][body.business_name] = copy.deepcopy(origin)
    adoption.review.full.old().setup().validate_configuration(proposed)
    lock_current_actor(db, p)
    if adoption.executor(db, p, body.actor_id, lock=True) != binding['executor']:
        adoption.fail('actor', '负责人权限在修订期间变化，原输入保留')
    root_id = parent.root_id if prior['schema'] == LEDGER else parent.id
    record = adoption._seal({'schema': RECORD, 'id': identity_value, 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'target_actor_id': str(body.actor_id), 'handoff_id': str(identity),
        'configuration_id': configuration.id, 'request_id': str(body.request_id),
        'business_name': body.business_name, 'proof_id': str(body.proof_id),
        'binding': binding, 'origin': origin, 'parent_id': parent.id, 'root_id': root_id,
        'parent_receipt_hash': prior['receipt']['receipt_hash'],
        'previous_executor': prior['record']['binding']['executor'],
        'command_hash': command_hash(p, identity, body), 'preview_hash': body.preview_hash,
        'accepted_revision': configuration.version + 1, 'configuration_hash': digest(proposed)}, 'record_hash')
    data = adoption._seal({'schema': LEDGER, 'record': record, 'command': body.model_dump(mode='json'),
        'receipt': make_receipt(record)}, 'ledger_hash')
    row = Revision(id=identity_value, workspace_id=p.workspace_id, actor_id=p.user_id,
        target_actor_id=str(body.actor_id), handoff_id=str(identity), proof_id=str(body.proof_id),
        configuration_id=configuration.id, request_id=str(body.request_id), business_name=body.business_name,
        parent_id=parent.id, root_id=root_id, version=1, data=data)
    checked_ledger(row, p, private=True)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=configuration.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(row); configuration.data = proposed; configuration.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=configuration.id, version=configuration.version, data=proposed))
    adoption.audit(db, p, 'setup.full_semantic_runtime.executor_revised', row.id,
        {'request_id': row.request_id, 'parent_id': parent.id, 'root_id': root_id,
            'record_hash': record['record_hash'], 'accepted_revision': configuration.version,
            'effect_scope': 'future_releases_only', 'semantic_mode': runtime.SEMANTIC_MODE})
    db.commit()
    return reply(db, p, row)


@router.get(PREFIX + '/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: adoption.review.pipeline.ReadP, db: adoption.review.pipeline.DB):
    row = db.scalar(select(Revision).where(Revision.workspace_id == p.workspace_id,
        Revision.actor_id == p.user_id, Revision.handoff_id == str(identity), Revision.request_id == str(request_id)))
    if row is None:
        return {'schema': 'report-full-semantic-runtime-revision-recovery-v1', 'found': False, 'request_id': str(request_id)}
    return {'schema': 'report-full-semantic-runtime-revision-recovery-v1', 'found': True, **reply(db, p, row)}


@router.get(PREFIX + '/{revision_identity}')
def get_revision(identity: UUID, revision_identity: UUID, p: adoption.review.pipeline.ReadP, db: adoption.review.pipeline.DB):
    row = db.get(Revision, str(revision_identity))
    if row is None or row.handoff_id != str(identity):
        adoption.fail('not_found', '未找到此完整设计的负责人修订记录', 404)
    return reply(db, p, row)
