"""Actual ledger authority for explicitly approved, lossless typed graph designs.

The public reference-position proof is not semantic consent. Each graph command
records the complete displayed translation, real accounts, capability versions,
and a separate approval of external transmission. No tool is called here.
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
from .report_full_semantic_graph_models import FullSemanticGraphAdoption as Adoption
from . import report_full_semantic_source_review as review
from . import report_full_semantic_runtime_adoption as old
from .services import audit, require, published

router = APIRouter()
PREFIX = '/full-semantic-graphs/{identity}'
CONFIG_KEY = 'fullSemanticGraphBindings'
SCHEMA = 'report-full-semantic-graph-ledger-v3'
HASH = r'^[a-f0-9]{64}$'


def fail(message, status=409):
    raise HTTPException(status, message)


class Preview(SafeContract):
    schema: Literal['report-full-semantic-graph-preview-command-v3']
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=HASH)
    proof_id: UUID
    business_name: str = Field(strict=True, min_length=1, max_length=100)
    graph: dict
    owner_bindings: list[dict] = Field(max_length=2000)
    role_bindings: dict[str, UUID]
    external_node_ids: list[str] = Field(default_factory=list, max_length=100)
    node_actions: dict[str, Literal['observe', 'state', 'data', 'learn']]


class Adopt(Preview):
    schema: Literal['report-full-semantic-graph-command-v3']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=HASH)
    consent: Literal[True]
    semantic_mode: Literal['workspace_workflow_design']

    @field_validator('consent', mode='before')
    @classmethod
    def consent_is_explicit(cls, value):
        if value is not True: raise ValueError('请明确采用所显示的完整执行设计')
        return value


def seal(value, key):
    return {**value, key: digest(value)}


def command_hash(p, identity, body):
    return digest({'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'handoff_id': str(identity), 'command': body.model_dump(mode='json')})


def preview_body(body, *, family=None):
    if family is not None:
        return family.preview_body(body)
    data = body.model_dump(mode='json')
    return Preview.model_validate({key: 'report-full-semantic-graph-preview-command-v3'
        if key == 'schema' else data[key] for key in Preview.model_fields})


def members(db, p, roles, lock=False):
    return {name: old.executor(db, p, actor, lock=lock) for name, actor in sorted(roles.items())}


def capabilities(db, p, graph, external_ids):
    """Check actual published read-only contracts, not a client supplied hash."""
    from .workflow_abilities import eligible, external
    from .full_semantic_graph_v1 import validate_graph
    graph = validate_graph(graph)
    from .runtime_task_documents import verify_graph_sources
    verify_graph_sources(db, p, graph)
    from .workflow_graph_event_sources import require_supported_event_sources
    require_supported_event_sources(graph)
    return ability_capabilities(db, p, graph, external_ids)


def ability_capabilities(db, p, graph, external_ids, *, service_delegations=None):
    """Actual ability authority; event authority is checked by the caller family."""
    from .workflow_abilities import eligible, external
    from .full_semantic_graph_v1 import validate_graph
    graph = validate_graph(graph)
    if service_delegations is not None:
        if type(service_delegations) is not dict or any(
                type(key) is not str or type(value) is not dict
                or set(value) != {'service_actor', 'operation', 'agent_role', 'side_effect'}
                or value['side_effect'] is not True
                for key, value in service_delegations.items()):
            fail('服务委托必须来自明确采用的原节点，不能推断服务权限')
        # Match the complete fixed server declaration. The V288 data writer
        # has a data role; arbitrary roles or service/operation pairs remain
        # forbidden even if a caller supplies a plausible side-effect flag.
        from .business_flow_nested_execution_v195 import service_delegation
        for value in service_delegations.values():
            try:
                expected = service_delegation(value['service_actor'], value['operation'])
            except (ValueError, HTTPException):
                fail('服务委托必须来自明确采用的原节点，不能推断服务权限')
            if value != expected:
                fail('服务角色与固定登记操作不一致，未授予执行权限')
        actual_services = {node['id'] for node in graph['nodes'] if node['kind'] == 'ability'
            and node['execution']['adapter'] == 'workflow_service'}
        if set(service_delegations) != actual_services:
            fail('服务委托与实际图节点不一致')
    result = {}; external_nodes = []
    for node in graph['nodes']:
        if node['kind'] != 'ability': continue
        spec = node['execution']; cap = published(db, p, spec['capability_id'])
        if service_delegations is not None and node['id'] in service_delegations:
            from .workflow_abilities import eligible_service
            delegation = service_delegations[node['id']]
            eligible_service(cap, spec['agent_role'], service_actor=delegation['service_actor'],
                operation=delegation['operation'])
        else:
            if cap.data['execution'].get('adapter') == 'task_archive':
                eligible(cap, spec['agent_role'], allow_task_archive=service_delegations is not None)
            elif cap.data['execution'].get('adapter') == 'task_evidence_packet':
                eligible(cap, spec['agent_role'], allow_task_packet=service_delegations is not None)
            elif cap.data['execution'].get('adapter') == 'runtime_document_packet':
                eligible(cap, spec['agent_role'], allow_document_packet=True)
            else:
                eligible(cap, spec['agent_role'])
        if any(spec[key] != value for key, value in {
            'capability_hash': cap.content_hash, 'release_version': cap.release_version,
            'adapter': cap.data['execution']['adapter'],
            'input_schema': cap.data['input_schema'], 'output_schema': cap.data['output_schema'],
            'max_attempts': cap.data['budget'].get('max_attempts', 2)}.items()):
            fail('注册能力版本或输入输出合同已变化，请重新核对执行设计')
        if external(cap):
            external_nodes.append(node['id'])
            policy = cap.data['data_policy']
            if policy.get('cloud_allowed' if spec['adapter'] in {'cloud_extractor', 'business_reasoning', 'business_decision_v276','business_decision_v284','business_goal_ready_v288'} else 'external_allowed') is not True:
                fail('此能力未允许外发所需输入，请先修改能力的数据政策')
        # Registry-declared server inputs cannot be replaced by literals or
        # arbitrary user fields in an otherwise self-consistent graph.
        for field, source in cap.data['execution'].get('input_bindings', {}).items():
            ref = graph['references'].get(spec['input_bindings'].get(field))
            if not ref or ref['source'] != 'input' or ref['field'] != source:
                fail('能力锁定的服务器输入来源不能由常量或其他节点替代')
        result[node['id']] = copy.deepcopy(spec)
    if (len(external_ids) != len(set(external_ids))
            or set(external_ids) != set(external_nodes)):
        fail('请逐个明确确认需要外发数据的能力；不能隐含外发或增加无关许可')
    return result


def schema_references(db, p, configuration, owners):
    from .report_full_semantic_schema_adoption import verify_origin
    verified = set()
    for owner in owners:
        for clause in owner.get('clauses', []):
            for target in clause.get('targets', []):
                if target.get('kind') != 'schema_origin': continue
                name = target.get('id')
                kind=target.get('origin',{}).get('kind')
                key='instanceObjectOrigins' if kind=='object' else 'instanceRelationOrigins'
                if kind not in {'object','relationship'} or configuration.get(key, {}).get(name) != target.get('origin'):
                    fail('对象字段的出处与当前明确采用的数据结构不一致')
                # verify_origin checks the whole adopted definition, including
                # all fields. Every target still checks its own exact origin.
                # This inventory lives only for this one call, never a request,
                # database transaction or a later authority check.
                identity = (kind, name)
                if identity not in verified:
                    verify_origin(db, p, configuration, name, kind)
                    verified.add(identity)


def automation(configuration, business_name, graph, actions):
    ability_ids={n['id'] for n in graph['nodes'] if n['kind']=='ability'}
    if set(actions)!=ability_ids:fail('请逐个明确能力节点对应的自动化动作')
    from .setup_automation import review_state
    if review_state(configuration,business_name)['status']=='needs_review':
        fail('流程或自动化范围已变化，请先核对并确认')
    rules=configuration.get('automation',{}).get('policies',{}).get(business_name,{}).get('rules',{})
    for action in actions.values():
        if rules.get(action,{}).get('mode')!='自动运行':
            fail('此图节点尚未获自动运行许可；请调整自动化范围，或在图中加入明确人工审批待办')


def make_preview(p, identity, body, binding, *, family=None):
    return seal({'schema': family.PREVIEW_SCHEMA if family else 'report-full-semantic-graph-preview-v3',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'handoff_id': str(identity),
        'command': preview_body(body, family=family).model_dump(mode='json'), 'binding': binding,
        'external_node_ids': copy.deepcopy(body.external_node_ids),
        'semantic_mode': 'workspace_workflow_design', 'source_semantics_status': 'not_certified',
        'consent_scope': '采用所显示的完整流程、角色、条件与协同执行设计；不认定原文事实，不授权设备或外部写入'}, 'preview_hash')


def prepare(db, p, identity, body, lock=False, *, family=None):
    from .report_full_semantic_graph_binding_v3 import build_binding
    ctx = review.context(db, p, identity, lock=lock)
    configuration, record, heads, manifest, _, _ = ctx
    if (str(identity) not in heads or configuration.version != body.configuration_revision
            or digest(configuration.data) != body.configuration_hash):
        fail('来源或配置已变化，原输入保留，请重新预览')
    if body.business_name in configuration.data.get(CONFIG_KEY, {}):
        fail('已有完整执行设计，请保留原回执，通过显式后继修订')
    if db.scalar(select(Adoption.id).where(Adoption.workspace_id == p.workspace_id,
            Adoption.handoff_id == str(identity), Adoption.business_name == body.business_name)):
        fail('原执行采用记录仍在，不能删除标记后重新授权')
    proof = old._formal(db, p, identity, body.proof_id, ctx, lock=lock)
    roles = members(db, p, body.role_bindings, lock)
    pins = (family.capabilities(db, p, body, lock=lock) if family
        else capabilities(db, p, body.graph, body.external_node_ids))
    automation(configuration.data,body.business_name,body.graph,body.node_actions)
    schema_references(db, p, configuration.data, body.owner_bindings)
    try:
        builder = family.build_binding if family else build_binding
        extras = family.binding_arguments(body) if family else {}
        binding = builder(record['design'], manifest, proof, body.business_name,
            body.graph, body.owner_bindings, proof_id=str(body.proof_id),
            capability_pins=pins, role_bindings=roles, **extras)
    except ValueError as error:
        fail('完整执行设计尚不能核对：' + str(error), 422)
    if body.business_name in binding['blocked_businesses']:
        fail('此业务仍有未解决的执行约束，请先明确对应项；其他业务不会据此自动阻断')
    return configuration, make_preview(p, identity, body, binding, family=family)


def origin_value(identity, binding, *, command_hash):
    import re
    if type(command_hash) is not str or not re.fullmatch(HASH,command_hash):raise ValueError('graph_command_hash')
    if binding.get('schema') != 'report-full-semantic-graph-binding-v3':raise ValueError('graph_origin_version')
    return {'schema': 'report-full-semantic-graph-origin-v3', 'adoption_id': identity,
        'handoff_id': binding['source_design']['handoff_id'],
        'business_name': binding['business_name'], 'binding_hash': binding['binding_hash'],'command_hash':command_hash}


def checked_base(db, p, row, *, family=None):
    """Cold rebuild against actual immutable command and before/after revisions."""
    from .report_full_semantic_graph_binding_v3 import check_binding
    if family is None and row is not None and row.data.get('schema') == 'report-full-semantic-graph-ledger-v4':
        from . import report_full_semantic_graph_adoption_v4 as family
    elif family is None and row is not None and row.data.get('schema') == 'report-object-graph-ledger-v171':
        from . import report_object_graph_adoption_v171 as family
    try:
        if row is None or row.workspace_id != p.workspace_id: fail('采用记录不存在', 404)
        data = row.data
        if set(data) != {'schema', 'record', 'command', 'receipt', 'ledger_hash'} or data['schema'] != (family.LEDGER if family else SCHEMA):
            raise ValueError('ledger')
        record = data['record']; command = (family.Adopt if family else Adopt).model_validate(data['command'])
        if data['command'] != command.model_dump(mode='json'): raise ValueError('command_shape')
        if set(record)!={'id','workspace_id','actor_id','handoff_id','configuration_id','request_id','binding',
                'origin','command_hash','preview_hash','accepted_revision','configuration_hash','record_hash'}:
            raise ValueError('record_shape')
        owner = type('Owner', (), {'workspace_id': row.workspace_id, 'user_id': row.actor_id})()
        binding = family.check_binding(record['binding']) if family else check_binding(record['binding'])
        if family is None and binding['schema'] != 'report-full-semantic-graph-binding-v3':raise ValueError('binding_version')
        if family: family.check_command_binding(command, binding)
        if (binding['graph']!=command.graph or binding['owner_bindings']!=command.owner_bindings
                or binding['business_name']!=command.business_name
                or {name:actor['user_id'] for name,actor in binding['role_bindings'].items()}
                    != {name:str(actor) for name,actor in command.role_bindings.items()}):
            raise ValueError('command_binding')
        expected_id = str(uuid5(UUID(row.handoff_id), (family.ID_NAMESPACE if family else 'graph-v3:') + row.workspace_id + ':'
            + row.actor_id + ':' + str(command.request_id)))
        if (row.version != 1 or row.id != expected_id or str(command.request_id) != row.request_id
                or command.business_name != row.business_name or str(command.proof_id) != row.proof_id
                or record['id'] != row.id or record['configuration_id'] != row.configuration_id
                or record['workspace_id'] != row.workspace_id or record['actor_id'] != row.actor_id
                or record['handoff_id'] != row.handoff_id or record['request_id'] != row.request_id
                or binding['business_name'] != row.business_name
                or binding['source_design']['handoff_id'] != row.handoff_id
                or binding['proof_id'] != row.proof_id
                or command_hash(owner, row.handoff_id, command) != record['command_hash']
                or make_preview(owner, row.handoff_id, command, binding, family=family)['preview_hash'] != command.preview_hash
                or record['preview_hash'] != command.preview_hash
                or record['origin'] != (family.origin_value if family else origin_value)(row.id, binding,command_hash=record['command_hash'])
                or record['accepted_revision'] != command.configuration_revision + 1
                or record['record_hash'] != digest({k: v for k, v in record.items() if k != 'record_hash'})
                or data['receipt'] != seal({k: record[k] for k in ('id', 'workspace_id', 'actor_id',
                    'handoff_id', 'request_id', 'command_hash', 'preview_hash', 'accepted_revision',
                    'configuration_hash', 'record_hash')}, 'receipt_hash')
                or data['ledger_hash'] != digest({k: v for k, v in data.items() if k != 'ledger_hash'})):
            raise ValueError('command')
        before = db.scalar(select(SetupRevision.data).where(SetupRevision.workspace_id == p.workspace_id,
            SetupRevision.draft_id == row.configuration_id, SetupRevision.version == command.configuration_revision))
        after = db.scalar(select(SetupRevision.data).where(SetupRevision.workspace_id == p.workspace_id,
            SetupRevision.draft_id == row.configuration_id, SetupRevision.version == record['accepted_revision']))
        if before is None or after is None: raise ValueError('history')
        expected = copy.deepcopy(before)
        if row.business_name in expected.get(CONFIG_KEY, {}): raise ValueError('overwrite')
        expected.setdefault(CONFIG_KEY, {})[row.business_name] = record['origin']
        if (digest(before) != command.configuration_hash or after != expected
                or digest(after) != record['configuration_hash']
                or binding['source_design'] not in before.get('fullSemanticDesigns', [])):
            raise ValueError('history_changed')
        if family is not None and data['schema'] == 'report-object-graph-ledger-v171':
            family.check_audit(db, p, row, data)
        return data
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        fail('完整流程采用命令、来源或配置历史无法冷核，原记录保留')


def lookup_adoption(db, identity, lock=False):
    if identity is None:
        return None
    query = select(Adoption).where(Adoption.id == str(identity))
    row = db.scalar(query.with_for_update() if lock else query)
    if row is not None:
        return row
    from .report_full_semantic_graph_revision_models import FullSemanticGraphRevision
    query = select(FullSemanticGraphRevision).where(FullSemanticGraphRevision.id == str(identity))
    return db.scalar(query.with_for_update() if lock else query)


def lookup_request(db, request_id):
    row = db.scalar(select(Adoption).where(Adoption.request_id == str(request_id)))
    if row is not None:
        return row
    from .report_full_semantic_graph_revision_models import FullSemanticGraphRevision
    return db.scalar(select(FullSemanticGraphRevision).where(FullSemanticGraphRevision.request_id == str(request_id)))


def checked(db, p, row):
    from .report_full_semantic_graph_revision_models import FullSemanticGraphRevision
    if isinstance(row, FullSemanticGraphRevision):
        from .report_full_semantic_graph_revision import checked as checked_revision
        return checked_revision(db, p, row)
    return checked_base(db, p, row)


def verify_origin(db, p, origin, require_current=True):
    row = lookup_adoption(db, origin.get('adoption_id')) if isinstance(origin, dict) else None
    data = checked(db, p, row); binding = data['record']['binding']
    if origin != data['record']['origin']: fail('执行设计出处与真实记录不一致')
    ctx = review.context(db, p, row.handoff_id)
    configuration, record, heads, manifest, _, _ = ctx
    if (row.handoff_id not in heads or record['design'] != binding['source_design']
            or manifest != binding['source_manifest']
            or require_current and configuration.data.get(CONFIG_KEY, {}).get(row.business_name) != origin):
        fail('完整设计或当前授权已变化，不能继续执行旧授权')
    proof = old._formal(db, p, row.handoff_id, row.proof_id, ctx)
    if proof != binding['source_proof']: fail('正式来源位置证明已变化')
    family = None
    if binding['schema'] == 'report-full-semantic-graph-binding-v4':
        from . import report_full_semantic_graph_adoption_v4 as family
    elif binding['schema'] == 'report-object-graph-binding-v171':
        from . import report_object_graph_adoption_v171 as family
    from .report_full_semantic_graph_revision_models import FullSemanticGraphRevision
    if isinstance(row, FullSemanticGraphRevision):
        from .report_full_semantic_graph_revision import Adopt as RevisionCommand
        command = (family.RevisionAdopt if family else RevisionCommand).model_validate(data['command'])
    else:
        command = (family.Adopt if family else Adopt).model_validate(data['command'])
    if members(db, p, command.role_bindings) != binding['role_bindings']:
        fail('负责人权限已变化，原记录保留')
    pins = (family.capabilities(db, p, command) if family
        else capabilities(db, p, binding['graph'], command.external_node_ids))
    if pins != binding['capability_pins']:
        fail('实际能力版本已变化，原记录保留')
    automation(configuration.data,row.business_name,binding['graph'],command.node_actions)
    schema_references(db, p, configuration.data, binding['owner_bindings'])
    return copy.deepcopy(binding)


def protect_configuration(db, p, previous, proposed):
    if previous.get(CONFIG_KEY, {}) != proposed.get(CONFIG_KEY, {}):
        fail('完整流程授权不能通过普通配置保存添加、删除或改写')
    for name, origin in proposed.get(CONFIG_KEY, {}).items():
        data = checked(db, p, lookup_adoption(db, origin.get('adoption_id')))
        if name != data['record']['binding']['business_name'] or origin != data['record']['origin']:
            fail('完整流程出处与实际历史不一致')


def reply(db, p, row):
    from .report_full_semantic_graph_revision_models import FullSemanticGraphRevision
    if isinstance(row, FullSemanticGraphRevision):
        from .report_full_semantic_graph_revision import reply as revision_reply
        return revision_reply(db, p, row)
    if row.actor_id != p.user_id: fail('请由原提交账号恢复完整流程请求', 403)
    data = checked(db, p, row)
    configuration = review.full.old().configuration_row(db, p)
    return {'schema': ({'report-full-semantic-graph-ledger-v3': 'report-full-semantic-graph-adoption-result-v3',
        'report-full-semantic-graph-ledger-v4': 'report-full-semantic-graph-adoption-result-v4',
        'report-object-graph-ledger-v171': 'report-object-graph-adoption-result-v171'}[data['schema']]),
        'receipt': copy.deepcopy(data['receipt']), 'origin': copy.deepcopy(data['record']['origin']),
        'configuration': review.full.old().setup().draft_view(configuration)}


@router.post(PREFIX + '/preview')
def preview(identity: UUID, body: Preview, p: review.pipeline.ReadP, db: review.pipeline.DB):
    require(p, 'admin', 'operator'); return prepare(db, p, identity, body)[1]


@router.post(PREFIX + '/adopt')
def adopt(identity: UUID, body: Adopt, p: review.pipeline.P, db: review.pipeline.DB):
    return execute_adopt(identity, body, p, db)


def execute_adopt(identity, body, p, db, *, family=None):
    require(p, 'admin', 'operator')
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key': 'full-graph-v3:' + str(body.request_id)})
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    prior = lookup_request(db, body.request_id)
    if prior:
        if (prior.actor_id != p.user_id or prior.workspace_id != p.workspace_id
                or prior.handoff_id != str(identity)):
            fail('原请求已绑定其他身份或工作区')
        if checked(db, p, prior)['record']['command_hash'] != command_hash(p, identity, body):
            fail('原请求不能更换条件、能力、负责人或执行范围')
        return reply(db, p, prior)
    if family is not None and family.LEDGER == 'report-object-graph-ledger-v171':
        family.require_absent_request(db, p, identity, body.request_id)
    configuration, prepared = prepare(db, p, identity, body, lock=True, family=family)
    if prepared['preview_hash'] != body.preview_hash: fail('执行预览已变化，请保留原输入并核对')
    identity_value = str(uuid5(identity, (family.ID_NAMESPACE if family else 'graph-v3:') + p.workspace_id + ':' + p.user_id + ':' + str(body.request_id)))
    binding = prepared['binding']; origin = (family.origin_value if family else origin_value)(identity_value, binding,command_hash=command_hash(p,identity,body))
    proposed = copy.deepcopy(configuration.data); proposed.setdefault(CONFIG_KEY, {})[body.business_name] = origin
    review.full.old().setup().validate_configuration(proposed)
    lock_current_actor(db, p); members(db, p, body.role_bindings, lock=True)
    record = seal({'id': identity_value, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'handoff_id': str(identity), 'configuration_id': configuration.id, 'request_id': str(body.request_id),
        'binding': binding, 'origin': origin, 'command_hash': command_hash(p, identity, body),
        'preview_hash': body.preview_hash, 'accepted_revision': configuration.version + 1,
        'configuration_hash': digest(proposed)}, 'record_hash')
    receipt = seal({key: record[key] for key in ('id', 'workspace_id', 'actor_id', 'handoff_id',
        'request_id', 'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash', 'record_hash')}, 'receipt_hash')
    data = seal({'schema': family.LEDGER if family else SCHEMA, 'record': record, 'command': body.model_dump(mode='json'), 'receipt': receipt}, 'ledger_hash')
    row = Adoption(id=identity_value, workspace_id=p.workspace_id, actor_id=p.user_id,
        handoff_id=str(identity), proof_id=str(body.proof_id), configuration_id=configuration.id,
        request_id=str(body.request_id), business_name=body.business_name, version=1, data=data)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=configuration.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(row); configuration.data = proposed; configuration.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=configuration.id, version=configuration.version, data=proposed))
    audit(db, p, 'setup.full_semantic_graph.adopted', row.id, {'record_hash': record['record_hash'], 'request_id': str(body.request_id)})
    db.flush(); checked(db, p, row); db.commit(); return reply(db, p, row)


@router.get(PREFIX + '/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    row = db.scalar(select(Adoption).where(Adoption.workspace_id == p.workspace_id,
        Adoption.actor_id == p.user_id, Adoption.handoff_id == str(identity), Adoption.request_id == str(request_id)))
    if row is not None and row.data.get('schema') not in {SCHEMA, 'report-full-semantic-graph-ledger-v4'}:
        fail('原请求属于另一执行合同，请保留并使用原入口恢复')
    return {'schema': 'report-full-semantic-graph-recovery-v3', 'found': False} if row is None else {'found': True, **reply(db, p, row)}


# This router is already registered by the application. New explicit commands
# are loaded here without changing that registration or any legacy route type.
from . import report_object_graph_adoption_v171 as _object_graph_v171
