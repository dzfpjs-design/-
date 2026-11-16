"""Explicit, durable workspace schema adoption from an authenticated full design.

Position proofs retain pending source semantics. These receipts adopt a data
structure selected by a workspace user, never approve original-source meaning
or unlock workflow execution. Shared receipts contain no private review notes.
"""
import copy
from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from . import report_full_semantic_source_review as review
from . import report_full_semantic_region_mapping as positions
from . import report_full_semantic_schema_v1 as pure
from .contracts import digest
from .models import Audit, SetupRevision, uid
from .report_reading_drafts import SafeContract
from .report_full_semantic_schema_models import FullSemanticSchemaAdoption as Adoption
from .report_full_semantic_region_models import FullSemanticRegionProof as Proof
from .services import audit, require


router = APIRouter()
PREFIX = '/full-semantic-schemas/{identity}'
ORIGIN = 'report-full-semantic-schema-origin-v1'
OBJECT_ORIGIN = 'report-object-schema-origin-v170'
LEDGER = 'report-full-semantic-schema-adoption-ledger-v1'
OBJECT_LEDGER = 'report-object-schema-adoption-ledger-v170'
HEADER_KEYS = {'id', 'plan_hash', 'material_ids'}
ORIGIN_KEYS = {'schema', 'adoption_id', 'plan_hash', 'kind', 'name', 'owner_path', 'owner_value_hash'}
HASH = r'^[a-f0-9]{64}$'


def fail(code, message, status=409):
    review.fail('full_schema_' + code, message, status)


class Selection(SafeContract):
    object_names: list[str] = Field(min_length=1, max_length=40)
    relationship_names: list[str] = Field(max_length=30)

    @field_validator('object_names', 'relationship_names', mode='before')
    @classmethod
    def strict_names(cls, value):
        if type(value) is not list or any(type(name) is not str for name in value):
            raise ValueError('请选择真实的对象与关系名称')
        return value


class RelationshipPolicy(SafeContract):
    cardinality: Literal['many', 'one_per_source', 'one_per_target']
    allow_self: bool = Field(strict=True)


class Preview(SafeContract):
    schema: Literal['report-full-semantic-schema-preview-command-v1', 'report-object-schema-preview-command-v170']
    proof_id: UUID
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=HASH)
    selection: Selection
    relationship_policies: dict[str, RelationshipPolicy]


class Adopt(Preview):
    schema: Literal['report-full-semantic-schema-command-v1', 'report-object-schema-command-v170']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=HASH)
    semantic_mode: Literal['workspace_schema_design']
    consent: bool = Field(strict=True)

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError('请明确同意采用本次数据结构设计；这不代表原文语义确认或执行授权')
        return value


def _seal(value, key):
    pure._json(value)
    return {**value, key: digest(value)}


def _same(left, right):
    return pure._json(left) == pure._json(right)


def command_hash(p, identity, body):
    schema = ('report-object-schema-command-hash-v170' if _object_command(body)
        else 'report-full-semantic-schema-command-hash-v1')
    return digest({'schema': schema,
        'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'handoff_id': str(identity), 'body': body.model_dump(mode='json')})


def _preview_body(body):
    value = body.model_dump(mode='json')
    return Preview.model_validate({**{key: value[key] for key in Preview.model_fields},
        'schema': ('report-object-schema-preview-command-v170' if _object_command(body)
            else 'report-full-semantic-schema-preview-command-v1')})


def _object_command(body):
    return body.schema in {'report-object-schema-preview-command-v170', 'report-object-schema-command-v170'}


def _identity(p, identity, nonce, *, object_family=False):
    prefix = 'object-schema-v170:' if object_family else 'full-semantic-schema-v1:'
    return str(uuid5(UUID(str(identity)), prefix + p.workspace_id + ':' + str(nonce)))


def _header(identity, plan, material_ids):
    return {'id': identity, 'plan_hash': plan['plan_hash'], 'material_ids': sorted(set(material_ids))}


def _origin(identity, plan, name, kind):
    source = plan['object_sources' if kind == 'object' else 'relationship_sources'][name]
    return {'schema': OBJECT_ORIGIN if plan['schema'] == pure.OBJECT_SCHEMA else ORIGIN,
        'adoption_id': identity, 'plan_hash': plan['plan_hash'],
        'kind': kind, 'name': name, 'owner_path': source['owner_path'], 'owner_value_hash': source['owner_value_hash']}


def _objects(config):
    from .setup_object_proposals import existing_objects
    return existing_objects(config)


def _conflicts(config, plan):
    existing_objects = _objects(config); existing_relations = config.get('instanceRelationTypes', [])
    if (type(existing_objects) is not list or type(existing_relations) is not list
            or any(type(item) is not dict or type(item.get('name')) is not str
                for item in [*existing_objects, *existing_relations])):
        fail('configuration_shape', '已有对象或关系配置无法核对，原配置保留', 422)
    if len(existing_objects) + len(plan['objects']) > 40 or len(existing_relations) + len(plan['relationships']) > 30:
        fail('capacity', '加入后超过40种对象或30种关系；请先核对实例范围，原配置保留', 422)
    result = []
    for kind, current, selected in (('object', existing_objects, plan['objects']),
            ('relationship', existing_relations, plan['relationships'])):
        origins = config.get('instanceObjectOrigins' if kind == 'object' else 'instanceRelationOrigins', {})
        if type(origins) is not dict:
            fail('configuration_shape', '已有来源记录无法核对，原配置保留', 422)
        names = {item['name'] for item in current} | set(origins)
        result.extend({'kind': kind, 'name': item['name']} for item in selected if item['name'] in names)
    return result


def _make_preview(p, identity, body, config, plan):
    schema = 'report-object-schema-preview-v170' if plan['schema'] == pure.OBJECT_SCHEMA else 'report-full-semantic-schema-preview-v1'
    return _seal({'schema': schema, 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'handoff_id': str(identity), 'proof_id': str(body.proof_id),
        'plan': copy.deepcopy(plan), 'configuration': {'revision': body.configuration_revision,
            'hash': body.configuration_hash}, 'conflicts': _conflicts(config, plan)}, 'preview_hash')


def _source_context(db, p, identity, proof_id, *, require_current, lock=False):
    ctx = review.context(db, p, identity, lock=lock)
    public = positions.get_proof(UUID(str(identity)), UUID(str(proof_id)), p, db)
    if require_current and (str(identity) not in ctx[2] or public.get('current_source_verified') is not True):
        fail('source_stale', '完整设计或正式来源版本已有变化；原输入、采用记录与回执保留')
    proof = public['proof']
    if require_current and lock:
        _, material, _, _, confirmation = positions.current_source(
            db, p, ctx[5], proof['selection'], formal=True, lock=True)
        if confirmation.id != proof['confirmation_id'] or material.id != proof['selection']['copy_ref']['id']:
            fail('source_stale', '正式来源或整篇确认版本已变化，原输入保留')
    material_ids = sorted({ctx[5]['original']['ref']['id'], proof['selection']['copy_ref']['id']})
    return ctx, proof, material_ids


def _plan(body, ctx, proof):
    if _object_command(body) != (ctx[1]['design']['schema'] == 'report-object-semantic-design-v169'):
        fail('family', '采用命令与完整设计合同不同；不能用旧结构省略业务标识或技术身份确认', 422)
    try:
        return pure.build_schema_adoption(ctx[1]['design'], ctx[3], proof,
            body.selection.model_dump(mode='json'),
            relationship_policies={name: value.model_dump(mode='json') for name, value in body.relationship_policies.items()})
    except (ValueError, TypeError, KeyError):
        fail('invalid_plan', '对象、完整字段、关系政策或位置证明无法核对；原输入保留', 422)


def prepare(db, p, identity, body, *, lock=False):
    ctx, proof, materials = _source_context(db, p, identity, body.proof_id, require_current=True, lock=lock)
    config = ctx[0]
    if config.version != body.configuration_revision or digest(config.data) != body.configuration_hash:
        fail('stale', '工作区配置已有新版本；原输入保留，请重新核对预览')
    plan = _plan(body, ctx, proof)
    return config, _make_preview(p, identity, body, config.data, plan), materials


def _append(config, identity, plan, material_ids):
    out = copy.deepcopy(config)
    if _conflicts(out, plan):
        fail('name_conflict', '已有同名对象或关系；本次采用不会覆盖原配置')
    out['instanceObjects'] = _objects(out) + copy.deepcopy(plan['objects'])
    out.setdefault('instanceRelationTypes', []).extend(copy.deepcopy(plan['relationships']))
    for key, kind, items in (('instanceObjectOrigins', 'object', plan['objects']),
            ('instanceRelationOrigins', 'relationship', plan['relationships'])):
        origins = out.setdefault(key, {})
        for item in items: origins[item['name']] = _origin(identity, plan, item['name'], kind)
    headers = out.setdefault('fullSemanticSchemaAdoptions', [])
    if type(headers) is not list or any(type(header) is not dict or header.get('id') == identity for header in headers):
        fail('configuration_shape', '采用来源清单无法核对，原配置保留')
    headers.append(_header(identity, plan, material_ids))
    return out


def _receipt(p, identity, resource_id, configuration_id, body, plan, accepted_revision, configuration_hash):
    schema = 'report-object-schema-receipt-v170' if plan['schema'] == pure.OBJECT_SCHEMA else 'report-full-semantic-schema-receipt-v1'
    return _seal({'schema': schema, 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'handoff_id': str(identity), 'proof_id': str(body.proof_id),
        'id': resource_id, 'configuration_id': configuration_id, 'request_id': str(body.request_id),
        'command_hash': command_hash(p, identity, body), 'preview_hash': body.preview_hash,
        'plan_hash': plan['plan_hash'], 'accepted_revision': accepted_revision,
        'configuration_hash': configuration_hash}, 'receipt_hash')


def _revision(db, p, configuration_id, version):
    return db.scalar(select(SetupRevision).where(SetupRevision.workspace_id == p.workspace_id,
        SetupRevision.draft_id == configuration_id, SetupRevision.version == version))


def checked_adoption(db, p, row, *, require_current=False):
    """Cold-check real consent, original preview/plan and both accepted revisions.

    Historical receipt verification does not demand a current source. Compilation
    opts into the separate current-source check, so ordinary edits keep working
    after a source change without laundering a stale schema into a new release.
    """
    if row is None or row.workspace_id != p.workspace_id:
        fail('not_found', '未找到本工作区的数据结构采用记录', 404)
    try:
        data = row.data
        pure._keys(data, {'schema', 'command', 'plan', 'preview', 'receipt', 'ledger_hash'}, 'ledger_shape')
        pure._sealed(data, 'ledger_hash')
        if data['schema'] not in {LEDGER, OBJECT_LEDGER} or row.version != 1:
            raise ValueError('ledger')
        body = Adopt.model_validate(data['command'])
        if (data['schema'] == OBJECT_LEDGER) != _object_command(body):
            raise ValueError('ledger family')
        owner = type('AdoptingActor', (), {'user_id': row.actor_id, 'workspace_id': p.workspace_id})()
        if not _same(data['command'], body.model_dump(mode='json')) or str(body.request_id) != row.request_id:
            raise ValueError('command')
        if row.id != _identity(owner, row.handoff_id, body.request_id, object_family=_object_command(body)):
            raise ValueError('identity')
        before = _revision(db, p, row.configuration_id, body.configuration_revision)
        accepted = _revision(db, p, row.configuration_id, body.configuration_revision + 1)
        if before is None or accepted is None or digest(before.data) != body.configuration_hash:
            raise ValueError('revision')
        ctx, proof, materials = _source_context(db, p, row.handoff_id, body.proof_id,
            require_current=require_current)
        if ctx[0].id != row.configuration_id:
            raise ValueError('configuration scope')
        plan = _plan(body, ctx, proof)
        pure.check_schema_adoption(data['plan'], ctx[1]['design'], ctx[3], proof,
            body.selection.model_dump(mode='json'),
            relationship_policies={name: value.model_dump(mode='json') for name, value in body.relationship_policies.items()})
        preview = _make_preview(owner, row.handoff_id, _preview_body(body), before.data, plan)
        if not _same(data['preview'], preview) or preview['preview_hash'] != body.preview_hash or preview['conflicts']:
            raise ValueError('preview')
        expected_config = _append(before.data, row.id, plan, materials)
        if not _same(accepted.data, expected_config):
            raise ValueError('accepted revision')
        expected = _receipt(owner, row.handoff_id, row.id, row.configuration_id, body, plan,
            accepted.version, digest(accepted.data))
        if not _same(data['receipt'], expected):
            raise ValueError('receipt')
        if _object_command(body):
            audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
                Audit.action == 'setup.full_semantic_schema.adopted', Audit.object_id == row.id)))
            expected_audit = {'handoff_id': row.handoff_id, 'request_id': row.request_id,
                'plan_hash': plan['plan_hash'], 'receipt_hash': expected['receipt_hash'],
                'accepted_revision': accepted.version}
            if len(audits) != 1 or audits[0].actor_id != row.actor_id or not _same(audits[0].data, expected_audit):
                raise ValueError('independent adoption audit')
        return copy.deepcopy(data)
    except HTTPException as error:
        if error.status_code == 422:
            fail('integrity', '已保存的采用命令或完整结构无法核对；原配置与回执保留')
        raise
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration, RecursionError):
        fail('integrity', '采用记录、原预览、来源或接受版本无法核对；原配置与回执保留')


def _checked_protection_authority(db, p, row, data, header):
    """Project only data just returned by this module's real cold verifier."""
    try:
        pure._keys(header, HEADER_KEYS, 'header_shape')
        receipt = data['receipt']; accepted = _revision(db, p, row.configuration_id, receipt['accepted_revision'])
        stored = next(item for item in accepted.data['fullSemanticSchemaAdoptions'] if item['id'] == row.id)
        if not _same(header, stored): raise ValueError('header')
        plan = data['plan']
        return {'header': copy.deepcopy(stored), 'objects': copy.deepcopy(plan['objects']),
            'relationships': copy.deepcopy(plan['relationships']),
            'object_origins': {item['name']: _origin(row.id, plan, item['name'], 'object') for item in plan['objects']},
            'relationship_origins': {item['name']: _origin(row.id, plan, item['name'], 'relationship') for item in plan['relationships']}}
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
        fail('integrity', '数据结构采用来源无法核对，原配置保留')


def protection_authority(db, p, header):
    """Authoritative historical projection for save protection; no current grant."""
    try:
        pure._keys(header, HEADER_KEYS, 'header_shape')
        row = db.get(Adoption, header['id']); data = checked_adoption(db, p, row)
        return _checked_protection_authority(db, p, row, data, header)
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
        fail('integrity', '数据结构采用来源无法核对，原配置保留')


def verify_origin(db, p, config, name, kind):
    """Verify current issued design/source plus the exact adopted configuration."""
    try:
        if kind not in {'object', 'relationship'}: raise ValueError('kind')
        origins_key = 'instanceObjectOrigins' if kind == 'object' else 'instanceRelationOrigins'
        definitions_key = 'instanceObjects' if kind == 'object' else 'instanceRelationTypes'
        origin = config[origins_key][name]; pure._keys(origin, ORIGIN_KEYS, 'origin_shape')
        if origin['schema'] not in {ORIGIN, OBJECT_ORIGIN} or origin['kind'] != kind or origin['name'] != name:
            raise ValueError('origin')
        row = db.get(Adoption, origin['adoption_id']); data = checked_adoption(db, p, row, require_current=True)
        plan = data['plan']; expected = _origin(row.id, plan, name, kind)
        if not _same(origin, expected): raise ValueError('origin')
        headers = config['fullSemanticSchemaAdoptions']
        if type(headers) is not list: raise ValueError('headers')
        selected = [item for item in headers if type(item) is dict and item.get('id') == row.id]
        if len(selected) != 1: raise ValueError('header')
        authority = _checked_protection_authority(db, p, row, data, selected[0])
        expected_definition = next(item for item in plan['objects' if kind == 'object' else 'relationships'] if item['name'] == name)
        actual = [item for item in config[definitions_key] if item.get('name') == name]
        if len(actual) != 1 or not _same(actual[0], expected_definition): raise ValueError('definition')
        return set(authority['header']['material_ids'])
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
        fail('origin_invalid', '对象或关系的采用记录、当前完整设计或正式来源已变化；原配置保留')


def origin_material_ids(config, names, kind):
    """Read only validated headers; verify_origin establishes their authority first."""
    key = 'instanceObjectOrigins' if kind == 'object' else 'instanceRelationOrigins'
    result = set()
    try:
        headers = config.get('fullSemanticSchemaAdoptions', [])
        for name in names:
            origin = config.get(key, {}).get(name, {})
            if origin.get('schema') not in {ORIGIN, OBJECT_ORIGIN}: continue
            pure._keys(origin, ORIGIN_KEYS, 'origin_shape')
            if origin['kind'] != kind or origin['name'] != name:
                raise ValueError('origin scope')
            pure._uuid(origin['adoption_id']); pure._hash(origin['plan_hash']); pure._hash(origin['owner_value_hash'])
            matches = [header for header in headers if header.get('id') == origin['adoption_id']]
            if len(matches) != 1: raise ValueError('header')
            header = matches[0]; pure._keys(header, HEADER_KEYS, 'header_shape')
            if header['plan_hash'] != origin['plan_hash'] or type(header['material_ids']) is not list:
                raise ValueError('header')
            if header['material_ids'] != sorted(set(header['material_ids'])): raise ValueError('materials')
            for identity in header['material_ids']: pure._uuid(identity)
            result.update(header['material_ids'])
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        fail('origin_invalid', '采用来源清单无法核对；原配置保留')


def _prior(db, p, nonce):
    return db.scalar(select(Adoption).where(Adoption.workspace_id == p.workspace_id, Adoption.request_id == str(nonce)))


def _reply(db, p, row):
    data = checked_adoption(db, p, row)
    config = review.full.old().configuration_row(db, p)
    return {'schema': 'report-full-semantic-schema-reply-v1', 'id': row.id, 'handoff_id': row.handoff_id,
        'plan': data['plan'], 'receipt': data['receipt'],
        'configuration': {'revision': config.version, 'hash': digest(config.data)},
        'current_matches': config.version == data['receipt']['accepted_revision']
            and digest(config.data) == data['receipt']['configuration_hash']}


@router.get(PREFIX)
def state(identity: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    ctx = review.context(db, p, identity)
    if str(identity) not in ctx[2]: fail('source_stale', '此完整设计已被替换；原采用历史保留')
    proofs = list(db.scalars(select(Proof).where(Proof.workspace_id == p.workspace_id,
        Proof.handoff_id == str(identity)).order_by(Proof.created_at, Proof.id).limit(101)))
    rows = list(db.scalars(select(Adoption).where(Adoption.workspace_id == p.workspace_id,
        Adoption.handoff_id == str(identity)).order_by(Adoption.created_at, Adoption.id).limit(41)))
    if len(proofs) > 100 or len(rows) > 40: fail('capacity', '采用历史或位置证明超过完整读取上限', 413)
    public = [positions.get_proof(identity, UUID(row.id), p, db) for row in proofs]
    schema = ('report-object-schema-state-v170' if ctx[1]['design']['schema'] == 'report-object-semantic-design-v169'
        else 'report-full-semantic-schema-state-v1')
    return review.bounded({'schema': schema, 'handoff_id': str(identity),
        'active': True, 'design_hash': ctx[1]['design']['design_hash'], 'manifest_hash': ctx[3]['manifest_hash'],
        'configuration': {'revision': ctx[0].version, 'hash': digest(ctx[0].data)},
        'objects': copy.deepcopy(ctx[1]['design']['result']['objects']),
        'relationships': copy.deepcopy(ctx[1]['design']['result']['relationships']),
        'proofs': [{'id': item['id'], 'proof_hash': item['proof']['proof_hash'],
            'current_source_verified': item['current_source_verified']} for item in public],
        'adoptions': [{'id': row.id, 'receipt': checked_adoption(db, p, row)['receipt']} for row in rows],
        'semantic_mode': 'workspace_schema_design', 'execution_supported': False})


@router.post(PREFIX + '/preview')
def preview(identity: UUID, body: Preview, p: review.pipeline.P, db: review.pipeline.DB):
    require(p, 'admin', 'operator')
    return prepare(db, p, identity, body)[1]


@router.post(PREFIX + '/adoptions')
def adopt(identity: UUID, body: Adopt, p: review.pipeline.P, db: review.pipeline.DB):
    require(p, 'admin', 'operator')
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
        {'key': 'full-schema-adoption-v1:' + p.workspace_id + ':' + str(body.request_id)})
    prior = _prior(db, p, body.request_id)
    if prior is not None:
        if prior.actor_id != p.user_id or prior.handoff_id != str(identity):
            fail('nonce_conflict', '此请求编号已用于其他采用操作；原回执保留')
        data = checked_adoption(db, p, prior)
        if data['receipt']['command_hash'] != command_hash(p, identity, body):
            fail('nonce_conflict', '原采用编号不能替换正文；原回执保留')
        return _reply(db, p, prior)
    config, prepared, materials = prepare(db, p, identity, body, lock=True)
    if prepared['preview_hash'] != body.preview_hash:
        fail('preview_changed', '采用预览已有变化，原输入保留，请重新核对')
    if prepared['conflicts']: fail('name_conflict', '已有同名对象或关系，本次采用不会覆盖原配置')
    identity_id = _identity(p, identity, body.request_id, object_family=_object_command(body)); plan = prepared['plan']
    proposed = _append(config.data, identity_id, plan, materials)
    setup = review.full.old().setup()
    setup.validate_configuration(proposed)
    setup.automation_review_actor(db, p)
    receipt = _receipt(p, identity, identity_id, config.id, body, plan, config.version + 1, digest(proposed))
    data = _seal({'schema': OBJECT_LEDGER if _object_command(body) else LEDGER,
        'command': body.model_dump(mode='json'), 'plan': copy.deepcopy(plan),
        'preview': copy.deepcopy(prepared), 'receipt': receipt}, 'ledger_hash')
    row = Adoption(id=identity_id, workspace_id=p.workspace_id, actor_id=p.user_id,
        handoff_id=str(identity), configuration_id=config.id, request_id=str(body.request_id), data=data)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id,
        draft_id=config.id, version=config.version, data=config.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(row); config.data = proposed; config.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=config.id, version=config.version, data=proposed))
    audit(db, p, 'setup.full_semantic_schema.adopted', row.id, {'handoff_id': str(identity),
        'request_id': str(body.request_id), 'plan_hash': plan['plan_hash'], 'receipt_hash': receipt['receipt_hash'],
        'accepted_revision': config.version})
    db.flush()
    checked_adoption(db, p, row)
    db.commit()
    return _reply(db, p, row)


@router.get(PREFIX + '/requests/{nonce}')
def recover(identity: UUID, nonce: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
        {'key': 'full-schema-adoption-v1:' + p.workspace_id + ':' + str(nonce)})
    row = _prior(db, p, nonce)
    if row is None:
        audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'setup.full_semantic_schema.adopted', Audit.data['request_id'].as_string() == str(nonce))))
        configuration = review.full.old().configuration_row(db, p)
        known_ids = {_identity(p, identity, nonce), _identity(p, identity, nonce, object_family=True)}
        headers = configuration.data.get('fullSemanticSchemaAdoptions', [])
        if audits or any(header.get('id') in known_ids for header in headers):
            fail('integrity', '原结构采用记录与独立审计或配置不完整；不能当作未提交，原输入保留')
    found = row is not None and row.actor_id == p.user_id and row.handoff_id == str(identity)
    return {**(_reply(db, p, row) if found else {}),
        'schema': 'report-full-semantic-schema-recovery-v1', 'found': found}


@router.get(PREFIX + '/adoptions/{adoption_id}')
def get_adoption(identity: UUID, adoption_id: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    row = db.get(Adoption, str(adoption_id))
    if row is None or row.workspace_id != p.workspace_id or row.handoff_id != str(identity):
        fail('not_found', '未找到本工作区的采用记录', 404)
    return _reply(db, p, row)
