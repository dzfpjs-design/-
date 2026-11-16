"""Explicit successors of a complete graph decision, effective in future releases.

The original command, source proof, graph, releases and tasks are never rewritten.
Cold history checks grant no permission to execute; the current compilation and
dispatch paths separately recheck source, accounts, capabilities and automation.
"""
import copy
import re
from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from .contracts import digest
from .models import SetupRevision, uid
from .report_reading_drafts import SafeContract
from .report_full_semantic_graph_binding_v3 import check_binding, build_binding
from .report_full_semantic_graph_revision_models import FullSemanticGraphRevision as Revision
from . import report_full_semantic_graph_adoption as base


router = APIRouter()
PREFIX = base.PREFIX + '/revisions'
LEDGER = 'report-full-semantic-graph-revision-ledger-v1'
RECORD = 'report-full-semantic-graph-revision-record-v1'
RECEIPT = 'report-full-semantic-graph-revision-receipt-v1'
# The immutable root is additional to this limit on revision rows. Existing
# chains of 256 revisions remain readable; a successor needs one vacant slot.
MAX_CHAIN = 256
_ROW_FIELDS = ('id', 'workspace_id', 'actor_id', 'handoff_id', 'configuration_id',
    'request_id', 'business_name', 'proof_id', 'parent_id', 'root_id')
_RECORD_KEYS = set(_ROW_FIELDS) | {'schema', 'binding', 'origin', 'command_hash',
    'preview_hash', 'accepted_revision', 'configuration_hash', 'parent_receipt_hash',
    'previous_binding_hash', 'record_hash'}
_RECEIPT_FIELDS = ('id', 'workspace_id', 'actor_id', 'handoff_id', 'request_id',
    'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash',
    'record_hash', 'parent_id', 'root_id', 'parent_receipt_hash', 'previous_binding_hash')


class Parent(SafeContract):
    id: UUID
    receipt_hash: str = Field(strict=True, pattern=base.HASH)


class Preview(base.Preview):
    schema: Literal['report-full-semantic-graph-revision-preview-command-v1']
    parent: Parent


class Adopt(Preview):
    schema: Literal['report-full-semantic-graph-revision-command-v1']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=base.HASH)
    consent: Literal[True]
    semantic_mode: Literal['workspace_workflow_design']

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError('请独立确认完整的新执行设计；原版本和已有任务不会被改写')
        return value


def need(ok, message='完整执行修订的原命令、回执或历史无法核对；原记录保留'):
    if not ok:
        base.fail(message)


def _uuid(value):
    return type(value) is str and str(UUID(value)) == value


def preview_command(body, *, family=None):
    if family: return family.revision_preview_body(body)
    values = body.model_dump(mode='json')
    return Preview.model_validate({key: ('report-full-semantic-graph-revision-preview-command-v1'
        if key == 'schema' else values[key]) for key in Preview.model_fields})


def command_hash(p, identity, body, *, family=None):
    return digest({'schema': family.REVISION_COMMAND_HASH if family else 'report-full-semantic-graph-revision-command-hash-v1',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'handoff_id': str(identity),
        'command': body.model_dump(mode='json')})


def revision_id(p, identity, request_id, *, family=None):
    return str(uuid5(UUID(str(identity)), (family.REVISION_ID_NAMESPACE if family else 'graph-revision-v1:') + p.workspace_id + ':'
        + p.user_id + ':' + str(request_id)))


def make_preview(p, identity, body, binding, *, root_id, previous_binding_hash, family=None):
    value = base.make_preview(p, identity, body, binding, family=family)
    value.pop('preview_hash')
    value.update(schema=family.REVISION_PREVIEW_SCHEMA if family else 'report-full-semantic-graph-revision-preview-v1',
        command=preview_command(body, family=family).model_dump(mode='json'),
        parent=body.parent.model_dump(mode='json'), root_id=root_id,
        previous_binding_hash=previous_binding_hash, effect_scope='future_releases_only',
        consent_scope='确认本预览完整流程、负责人、条件、协同、能力与输入范围；仅用于后续生成版本，原版本和已有任务保留；不认定原文事实，不授权设备或外部写入')
    return base.seal(value, 'preview_hash')


def make_receipt(record, *, family=None):
    return base.seal({'schema': family.REVISION_RECEIPT if family else RECEIPT,
        **{key: record[key] for key in _RECEIPT_FIELDS}}, 'receipt_hash')


def execution_value(data):
    """Action consent is part of the revision even when the graph is unchanged."""
    return {'binding': data['record']['binding'],
        'external_node_ids': data['command']['external_node_ids'],
        'node_actions': data['command']['node_actions']}


def _family(row):
    if row is not None and row.data.get('schema') == 'report-full-semantic-graph-revision-ledger-v4':
        from . import report_full_semantic_graph_adoption_v4
        return report_full_semantic_graph_adoption_v4
    if row is not None and row.data.get('schema') == 'report-object-graph-revision-ledger-v171':
        from . import report_object_graph_adoption_v171
        return report_object_graph_adoption_v171
    return None


def checked_ledger(row, p):
    """Closed immutable shape only; ``checked`` also requires real history."""
    try:
        need(row is not None and row.workspace_id == p.workspace_id, '未找到此工作区的完整执行修订')
        data = row.data; family = _family(row)
        need(type(data) is dict and set(data) == {'schema', 'record', 'command', 'receipt', 'ledger_hash'}
            and data['schema'] == (family.REVISION_LEDGER if family else LEDGER) and row.version == 1)
        record = data['record']
        need(type(record) is dict and set(record) == _RECORD_KEYS and record['schema'] == (family.REVISION_RECORD if family else RECORD))
        need(all(record[key] == getattr(row, key) for key in _ROW_FIELDS))
        need(all(_uuid(record[key]) for key in _ROW_FIELDS if key != 'business_name'))
        need(all(type(record[key]) is str and re.fullmatch(base.HASH, record[key]) is not None
            for key in ('command_hash', 'preview_hash', 'configuration_hash',
                'parent_receipt_hash', 'previous_binding_hash', 'record_hash')))
        body = (family.RevisionAdopt if family else Adopt).model_validate(data['command'])
        need(data['command'] == body.model_dump(mode='json'))
        owner = type('Owner', (), {'workspace_id': row.workspace_id, 'user_id': row.actor_id})()
        binding = family.check_binding(record['binding']) if family else check_binding(record['binding'])
        need(family is not None or binding['schema'] == 'report-full-semantic-graph-binding-v3')
        if family: family.check_command_binding(body, binding)
        need(binding['graph'] == body.graph and binding['owner_bindings'] == body.owner_bindings
            and binding['business_name'] == body.business_name
            and {name: actor['user_id'] for name, actor in binding['role_bindings'].items()}
                == {name: str(actor) for name, actor in body.role_bindings.items()})
        need(row.id == revision_id(owner, row.handoff_id, body.request_id, family=family)
            and row.parent_id != row.id and str(body.parent.id) == row.parent_id
            and body.parent.receipt_hash == record['parent_receipt_hash']
            and str(body.request_id) == row.request_id and str(body.proof_id) == row.proof_id
            and body.business_name == row.business_name and binding['proof_id'] == row.proof_id
            and binding['source_design']['handoff_id'] == row.handoff_id
            and record['accepted_revision'] == body.configuration_revision + 1
            and record['command_hash'] == command_hash(owner, row.handoff_id, body, family=family)
            and record['preview_hash'] == body.preview_hash
            and make_preview(owner, row.handoff_id, body, binding, root_id=row.root_id,
                previous_binding_hash=record['previous_binding_hash'], family=family)['preview_hash'] == body.preview_hash
            and record['origin'] == (family.origin_value if family else base.origin_value)(row.id, binding, command_hash=record['command_hash'])
            and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'})
            and data['receipt'] == make_receipt(record, family=family)
            and data['ledger_hash'] == digest({key: value for key, value in data.items() if key != 'ledger_hash'}))
        return data
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        base.fail('完整执行修订命令、明确同意或回执无法冷核；原记录保留')


def _lineage(db, p, previous, current):
    if previous == current:
        return
    from .report_full_semantic_replacement import record_set
    records, _, children = record_set(db, p)
    identity = previous; seen = set()
    while identity in records and identity not in seen:
        if identity == current:
            return
        seen.add(identity); identity = children.get(identity)
    base.fail('新完整设计不属于原执行采用记录的真实后续版本，不能按同名业务改绑')


def _root_id(row, data):
    return row.root_id if data['schema'] in {LEDGER, 'report-full-semantic-graph-revision-ledger-v4',
        'report-object-graph-revision-ledger-v171'} else row.id


def checked_any(db, p, row):
    return base.checked(db, p, row)


def _history_step(db, p, row, data, parent, prior):
    record = data['record']; family = _family(row)
    body = (family.RevisionAdopt if family else Adopt).model_validate(data['command'])
    need(family is not None or prior['record']['binding']['schema'] == 'report-full-semantic-graph-binding-v3',
        '新版执行设计不能通过旧版修订降级')
    need(parent is not None and parent.workspace_id == row.workspace_id
        and parent.configuration_id == row.configuration_id and parent.business_name == row.business_name
        and _root_id(parent, prior) == row.root_id
        and prior['receipt']['receipt_hash'] == record['parent_receipt_hash']
        and prior['record']['binding']['binding_hash'] == record['previous_binding_hash']
        and prior['record']['accepted_revision'] < record['accepted_revision']
        and execution_value(data) != execution_value(prior))
    _lineage(db, p, parent.handoff_id, row.handoff_id)
    before = db.scalar(select(SetupRevision.data).where(SetupRevision.workspace_id == p.workspace_id,
        SetupRevision.draft_id == row.configuration_id, SetupRevision.version == body.configuration_revision))
    after = db.scalar(select(SetupRevision.data).where(SetupRevision.workspace_id == p.workspace_id,
        SetupRevision.draft_id == row.configuration_id, SetupRevision.version == record['accepted_revision']))
    need(type(before) is dict and type(after) is dict, '完整执行修订前后的真实配置历史缺失')
    expected = copy.deepcopy(before)
    need(type(expected.get(base.CONFIG_KEY)) is dict
        and expected[base.CONFIG_KEY].get(row.business_name) == prior['record']['origin'])
    expected[base.CONFIG_KEY][row.business_name] = copy.deepcopy(record['origin'])
    from .report_full_semantic_configuration_v1 import derive_flows
    need(digest(before) == body.configuration_hash and after == expected
        and digest(after) == record['configuration_hash']
        and record['binding']['source_design'] in before.get('fullSemanticDesigns', [])
        and before.get('flows', {}).get(row.business_name)
            == derive_flows(record['binding']['source_design'])[row.business_name])


def _checked_chain(db, p, row):
    """Return the checked ledger and revision count, with the root additional."""
    result = checked_ledger(row, p); current, value = row, result; seen = set()
    while True:
        need(current.id not in seen and len(seen) < MAX_CHAIN, '完整执行修订历史存在循环或超过完整核对上限')
        seen.add(current.id)
        if value['schema'] == 'report-object-graph-revision-ledger-v171':
            _family(current).check_audit(db, p, current, value)
        parent = base.lookup_adoption(db, current.parent_id)
        need(parent is not None and parent.id not in seen, '完整执行修订的真实前版本缺失或存在循环')
        is_revision = parent.data.get('schema') in {LEDGER, 'report-full-semantic-graph-revision-ledger-v4',
            'report-object-graph-revision-ledger-v171'}
        prior = checked_ledger(parent, p) if is_revision else base.checked_base(db, p, parent)
        _history_step(db, p, current, value, parent, prior)
        if not is_revision:
            need(parent.id == row.root_id)
            break
        current, value = parent, prior
    return result, len(seen)


def checked(db, p, row):
    """Iteratively cold-check each actual successor and the immutable root."""
    return _checked_chain(db, p, row)[0]


def _checked_parent(db, p, row):
    """Cold-read the parent and reject overflow before offering or writing it."""
    if row is not None and row.data.get('schema') in {LEDGER, 'report-full-semantic-graph-revision-ledger-v4',
            'report-object-graph-revision-ledger-v171'}:
        data, count = _checked_chain(db, p, row)
        need(count < MAX_CHAIN, '完整执行修订已达到完整历史核对上限，不能追加新修订；原记录仍可读取')
        return data
    return base.checked_base(db, p, row)


def require_head(db, p, row):
    need(db.scalar(select(Revision.id).where(Revision.parent_id == row.id)) is None,
        '此执行采用记录已有后续修订，请保留原输入并读取当前版本')


def prepare(db, p, identity, body, *, lock=False, family=None):
    ctx = base.review.context(db, p, identity, lock=lock)
    configuration, source, heads, manifest, _, _ = ctx
    need(str(identity) in heads and configuration.version == body.configuration_revision
        and digest(configuration.data) == body.configuration_hash,
        '当前完整设计或配置已变化，请保留输入并重新预览')
    parent = base.lookup_adoption(db, str(body.parent.id), lock=lock)
    prior = _checked_parent(db, p, parent)
    need(family is not None or prior['record']['binding']['schema'] == 'report-full-semantic-graph-binding-v3',
        '新版执行设计不能通过旧版修订降级，请使用对应的完整后继修订入口')
    values = configuration.data.get(base.CONFIG_KEY)
    need(parent.configuration_id == configuration.id and parent.business_name == body.business_name
        and type(values) is dict and len(values) <= 20
        and values.get(body.business_name) == prior['record']['origin']
        and prior['receipt']['receipt_hash'] == body.parent.receipt_hash,
        '前版本回执与当前工作区执行设计不一致，请读取当前版本')
    require_head(db, p, parent)
    _lineage(db, p, parent.handoff_id, str(identity))
    proof = base.old._formal(db, p, identity, body.proof_id, ctx, lock=lock)
    roles = base.members(db, p, body.role_bindings, lock)
    if lock and family is None:
        from .full_semantic_graph_v1 import validate_graph
        from .models import Capability
        from .services import scoped
        try:
            graph = validate_graph(body.graph)
        except ValueError as error:
            base.fail('完整执行修订尚不能核对：' + str(error), 422)
        for capability_id in sorted({node['execution']['capability_id'] for node in graph['nodes']
                if node['kind'] == 'ability'}):
            scoped(db, Capability, capability_id, p, True)
    pins = (family.capabilities(db, p, body, lock=lock) if family
        else base.capabilities(db, p, body.graph, body.external_node_ids))
    base.automation(configuration.data, body.business_name, body.graph, body.node_actions)
    base.schema_references(db, p, configuration.data, body.owner_bindings)
    try:
        builder = family.build_binding if family else build_binding
        extras = family.binding_arguments(body) if family else {}
        binding = builder(source['design'], manifest, proof, body.business_name,
            body.graph, body.owner_bindings, proof_id=str(body.proof_id), capability_pins=pins, role_bindings=roles, **extras)
        from .report_full_semantic_configuration_v1 import derive_flows
        need(configuration.data.get('flows', {}).get(body.business_name) == derive_flows(source['design'])[body.business_name])
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
        base.fail('完整执行修订尚不能核对：' + str(error), 422)
    need(body.business_name not in binding['blocked_businesses'], '此业务仍有未解决的执行约束，不能修订为可运行')
    need({'binding': binding, 'external_node_ids': body.external_node_ids, 'node_actions': body.node_actions}
        != execution_value(prior), '完整执行设计及许可均未变化，无需再次确认')
    # A role name is workspace-wide at compilation. Changing it for this graph
    # must not silently contradict another independently adopted business.
    for name, origin in values.items():
        if name == body.business_name:
            continue
        need(type(origin) is dict, '其他业务的执行采用记录无法核对')
        other_row = base.lookup_adoption(db, origin.get('adoption_id'))
        other = checked_any(db, p, other_row)
        need(other_row.configuration_id == configuration.id and other_row.business_name == name
            and other['record']['origin'] == origin, '其他业务的执行采用记录无法核对')
        for role, actor in binding['role_bindings'].items():
            previous = other['record']['binding']['role_bindings'].get(role)
            need(previous is None or previous == actor, '此职责用于其他业务；请共同核对负责人，不能生成相互冲突的角色绑定')
    legacy_values = configuration.data.get(base.old.runtime.CONFIG_KEY, {})
    need(type(legacy_values) is dict, '原人工执行采用清单无法核对')
    for name, origin in legacy_values.items():
        need(type(origin) is dict, '原人工执行采用清单无法核对')
        other_row = base.old.lookup_adoption(db, origin.get('adoption_id'))
        other = base.old.checked_ledger(other_row, p)
        base.old.accepted_history(db, p, other_row, other)
        need(other_row.configuration_id == configuration.id and other_row.business_name == name
            and other['record']['origin'] == origin, '原人工执行出处与真实采用记录不一致')
        old_binding = other['record']['binding']
        role = old_binding['source_business']['steps'][0]['role']
        need(role not in binding['role_bindings']
            or binding['role_bindings'][role] == old_binding['executor'],
            '此职责用于原人工业务；请共同核对负责人，不能生成相互冲突的角色绑定')
    root_id = _root_id(parent, prior)
    return configuration, make_preview(p, identity, body, binding, root_id=root_id,
        previous_binding_hash=prior['record']['binding']['binding_hash'], family=family)


def reply(db, p, row):
    need(row is not None and row.actor_id == p.user_id, '请由原提交账号读取完整执行修订回执')
    data = checked(db, p, row)
    configuration = base.review.full.old().configuration_row(db, p)
    family = _family(row)
    return {'schema': ('report-object-graph-revision-result-v171'
        if data['schema'] == 'report-object-graph-revision-ledger-v171'
        else 'report-full-semantic-graph-revision-result-v4' if family
        else 'report-full-semantic-graph-revision-result-v1'),
        'receipt': copy.deepcopy(data['receipt']), 'origin': copy.deepcopy(data['record']['origin']),
        'configuration': base.review.full.old().setup().draft_view(configuration),
        'current_matches': configuration.version == data['record']['accepted_revision']
            and digest(configuration.data) == data['record']['configuration_hash'],
        'source_semantics_status': 'not_certified', 'effect_scope': 'future_releases_only'}


@router.post(PREFIX + '/preview')
def preview(identity: UUID, body: Preview, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    base.require(p, 'admin', 'operator')
    return prepare(db, p, identity, body)[1]


@router.post(PREFIX + '/adopt')
def adopt(identity: UUID, body: Adopt, p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return execute_adopt(identity, body, p, db)


def execute_adopt(identity, body, p, db, *, family=None):
    base.require(p, 'admin', 'operator')
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key': 'full-graph-v3:' + str(body.request_id)})
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    existing = base.lookup_request(db, str(body.request_id))
    if existing is not None:
        need(isinstance(existing, Revision), '原请求编号已用于完整执行采用，不能重新用于修订')
        need(existing.workspace_id == p.workspace_id and existing.actor_id == p.user_id
            and existing.handoff_id == str(identity), '原修订请求已绑定其他身份或工作区')
        data = checked(db, p, existing)
        need(data['record']['command_hash'] == command_hash(p, identity, body, family=family),
            '原请求不能更换前版本、条件、能力、负责人或执行范围')
        return reply(db, p, existing)
    if family is not None and family.REVISION_LEDGER == 'report-object-graph-revision-ledger-v171':
        family.require_absent_request(db, p, identity, body.request_id)
    configuration, prepared = prepare(db, p, identity, body, lock=True, family=family)
    need(prepared['preview_hash'] == body.preview_hash, '修订预览、来源、能力、负责人或配置已变化，请保留输入重新预览')
    identity_value = revision_id(p, identity, body.request_id, family=family)
    binding = prepared['binding']; signature = command_hash(p, identity, body, family=family)
    origin = (family.origin_value if family else base.origin_value)(identity_value, binding, command_hash=signature)
    proposed = copy.deepcopy(configuration.data)
    proposed[base.CONFIG_KEY][body.business_name] = copy.deepcopy(origin)
    base.review.full.old().setup().validate_configuration(proposed)
    lock_current_actor(db, p)
    need(base.members(db, p, body.role_bindings, lock=True) == binding['role_bindings'],
        '负责人权限在修订期间变化，原输入保留')
    record = base.seal({'schema': family.REVISION_RECORD if family else RECORD, 'id': identity_value, 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'handoff_id': str(identity), 'configuration_id': configuration.id,
        'request_id': str(body.request_id), 'business_name': body.business_name, 'proof_id': str(body.proof_id),
        'binding': binding, 'origin': origin, 'command_hash': signature, 'preview_hash': body.preview_hash,
        'accepted_revision': configuration.version + 1, 'configuration_hash': digest(proposed),
        'parent_id': str(body.parent.id), 'root_id': prepared['root_id'],
        'parent_receipt_hash': body.parent.receipt_hash,
        'previous_binding_hash': prepared['previous_binding_hash']}, 'record_hash')
    data = base.seal({'schema': family.REVISION_LEDGER if family else LEDGER, 'record': record, 'command': body.model_dump(mode='json'),
        'receipt': make_receipt(record, family=family)}, 'ledger_hash')
    row = Revision(**{key: record[key] for key in _ROW_FIELDS}, version=1, data=data)
    checked_ledger(row, p)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=configuration.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(row); configuration.data = proposed; configuration.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=proposed))
    base.audit(db, p, 'setup.full_semantic_graph.revised', row.id,
        {'request_id': row.request_id, 'parent_id': row.parent_id, 'root_id': row.root_id,
            'record_hash': record['record_hash'], 'accepted_revision': configuration.version,
            'effect_scope': 'future_releases_only'})
    db.flush(); checked(db, p, row); db.commit()
    return reply(db, p, row)


@router.get(PREFIX + '/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    row = db.scalar(select(Revision).where(Revision.workspace_id == p.workspace_id,
        Revision.actor_id == p.user_id, Revision.handoff_id == str(identity), Revision.request_id == str(request_id)))
    if row is None:
        return {'schema': 'report-full-semantic-graph-revision-recovery-v1', 'found': False}
    need(row.data.get('schema') in {LEDGER, 'report-full-semantic-graph-revision-ledger-v4'},
        '原请求属于另一执行合同，请使用原入口恢复')
    return {'found': True, **reply(db, p, row)}


@router.get(PREFIX + '/{revision_identity}')
def get_revision(identity: UUID, revision_identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    row = db.get(Revision, str(revision_identity))
    need(row is not None and row.handoff_id == str(identity)
        and row.data.get('schema') in {LEDGER, 'report-full-semantic-graph-revision-ledger-v4'},
        '未找到此完整设计的旧版执行修订记录')
    return reply(db, p, row)
