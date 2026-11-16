"""Explicit source-bound graph configuration; adopting never dispatches work.

This family has its own qualified source. It does not fabricate an old complete
report design/manifest/position proof. Ordinary configuration writes cannot
create/remove its grant. Immutable replies recover without current source IO.
"""
import copy
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from . import business_flow_execution_source as sources
from . import business_flow_review_store as reviews
from . import business_flow_nested_adoption_v195 as nested
from .business_flow_execution_models import BusinessFlowExecutionAdoption as Adoption
from .contracts import digest
from .models import Audit, SetupDraft, SetupRevision, uid, now
from .report_reading_drafts import SafeContract
from .services import audit, require

CONFIG_KEY = 'businessFlowExecutions'
BINDING = 'business-flow-execution-binding-v1'
ORIGIN = 'business-flow-execution-origin-v1'
LEDGER = 'business-flow-execution-ledger-v1'
AUDIT = 'business_flow.execution_adopted_v1'
PREFIX = '/api/setup/business-flow-executions'
MAX_BODY = 2 * 1024 * 1024
AGENTS = {'human': '人工确认', 'business_agent': '业务智能体',
    'perception_agent': '多模感知智能体', 'data_agent': '数据管理智能体',
    'task_center': '任务中心', 'execution_service': '执行服务'}


def need(ok, message='流程执行设计、原请求或采用历史无法核对；已有记录保留', code=409):
    if not ok: raise HTTPException(code, message)


class Preview(SafeContract):
    schema: Literal['business-flow-execution-preview-command-v1']
    qualification_id: UUID
    qualification_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    flow_id: str = Field(strict=True, pattern=r'^[a-z][a-z0-9_-]{0,47}$')
    business_name: str = Field(strict=True, min_length=1, max_length=200)
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    execution_bindings: dict
    role_bindings: dict[str, UUID]
    node_actions: dict[str, Literal['observe', 'state', 'data', 'learn']]
    external_node_ids: list[str] = Field(max_length=128)


class Adopt(Preview):
    schema: Literal['business-flow-execution-adopt-command-v1']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    consent: Literal[True]
    share_source: Literal[True]

    @field_validator('consent', 'share_source', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True: raise ValueError('请明确采用并共享所显示的流程及依据')
        return value


def parse(raw, model):
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_BODY, code=422)
        def pairs(items):
            result = {}
            for k, v in items:
                if k in result: raise ValueError('duplicate')
                result[k] = v
            return result
        def constant(_): raise ValueError('nonfinite')
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=constant)
        reviews._bytes(value, MAX_BODY)
        body = model.model_validate(value)
        fields = set(model.model_fields)
        if issubclass(model, nested.Preview) and 'observation_extractors' not in value:
            fields.remove('observation_extractors')
        need(set(value) == fields, code=422)
        return body
    except HTTPException: raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '执行设计字段、明确选择或原请求格式不完整；未截断或发送') from None


def preview_body(body):
    if isinstance(body, nested.Preview):
        return nested.preview_body(body)
    data = body.model_dump(mode='json')
    return Preview.model_validate({k: 'business-flow-execution-preview-command-v1' if k == 'schema' else data[k]
        for k in Preview.model_fields})


def seal(value, key): return {**value, key: digest(value)}


def derive_flow(package, flow_id):
    q = package['qualification']
    selected = [f for f in q['value']['business']['flows'] if f['flow_id'] == flow_id]
    need(len(selected) == 1, '请选择这一核对版本中存在的完整流程', 422)
    f = selected[0]
    return {'trigger': f['trigger']['description'], 'output': '\n'.join(d['name'] for d in f['deliverables']),
        'steps': [{'id': n['node_id'], 'name': n['name'], 'purpose': n['responsibility'],
            'agent': AGENTS[n['actor']], 'role': n['role'], 'output': '\n'.join(n['outputs'])} for n in f['nodes']],
        'confirmedAt': None, 'confirmedSignature': None,
        'businessFlowSource': {'schema': 'business-flow-execution-flow-v1', 'qualification_id': q['id'],
            'qualification_hash': q['qualification_hash'], 'flow_id': flow_id, 'source_flow_hash': digest(f)},
        'source_flow': copy.deepcopy(f), 'execution_display': 'typed_graph'}


def check_binding(value):
    if type(value) is dict and value.get('schema') == nested.BINDING:
        return nested.check_binding(value)
    from .business_flow_execution_graph import build_execution_graph
    keys = {'schema', 'business_name', 'qualification_id', 'qualification_hash', 'flow_id', 'flow', 'graph',
        'role_bindings', 'capability_pins', 'event_source_bindings', 'event_admission_policy', 'source_materials',
        'source', 'execution_bindings', 'translation', 'node_actions', 'external_node_ids', 'binding_hash'}
    need(type(value) is dict and set(value) == keys and value['schema'] == BINDING)
    reviews._bytes(value, 16 * 1024 * 1024)
    package = value['source']; q = package['qualification']
    need(type(package) is dict and set(package) == {'schema', 'qualification', 'scope_binding', 'material_snapshot'}
        and package['schema'] == 'business-flow-execution-source-v1'
        and value['qualification_id'] == q['id'] and value['qualification_hash'] == q['qualification_hash']
        and value['flow'] == derive_flow(package, value['flow_id'])
        and type(value['business_name']) is str and bool(value['business_name'].strip())
        and value['business_name'] == value['business_name'].strip()
        and value['event_source_bindings'] == {} and value['event_admission_policy'] is None)
    translation = build_execution_graph(q['value'], value['flow_id'], value['execution_bindings'],
        scope_binding=package['scope_binding'])
    need(not translation['issues'] and translation['graph'] is not None
        and reviews._same(translation, value['translation']) and value['graph'] == translation['graph'])
    graph = value['graph']; roles = value['role_bindings']
    source_nodes = {n['node_id']: n for n in value['flow']['source_flow']['nodes']}
    need(type(roles) is dict and set(roles) == {n['role'] for n in source_nodes.values()})
    for node in graph['nodes']:
        if node['kind'] not in {'human', 'ability'}: continue
        source_node = source_nodes[node['source_step_id']]
        need(source_node['role'] in roles and roles[source_node['role']]['user_id'] == node['actor_id'])
    pins = {n['id']: n['execution'] for n in graph['nodes'] if n['kind'] == 'ability'}
    need(value['capability_pins'] == pins and set(value['node_actions']) == set(pins)
        and all(v in {'observe', 'state', 'data', 'learn'} for v in value['node_actions'].values())
        and value['binding_hash'] == digest({k: v for k, v in value.items() if k != 'binding_hash'}))
    return copy.deepcopy(value)


def origin(identity, binding, command_hash):
    if binding.get('schema') == nested.BINDING:
        return nested.origin(identity, binding, command_hash)
    need(binding.get('schema') == BINDING)
    return {'schema': ORIGIN, 'adoption_id': identity, 'business_name': binding['business_name'],
        'binding_hash': binding['binding_hash'], 'command_hash': command_hash}


def proposed(before, name, binding, issued_origin):
    from .instance_compiler import names
    need(name not in names(before) and name not in before.get('flows', {})
        and name not in before.get('businessAliases', {}) and name not in before.get(CONFIG_KEY, {}),
        '同名业务已经存在；请保留现有业务，通过后继修订处理，不能覆盖原流程')
    need(len(names(before)) < 20, '业务清单已满，原配置保留', 422)
    result = copy.deepcopy(before)
    result.setdefault('businesses', []).append(name)
    result.setdefault('flows', {})[name] = copy.deepcopy(binding['flow'])
    result.setdefault(CONFIG_KEY, {})[name] = copy.deepcopy(issued_origin)
    return result


def current_configuration(db, p, *, lock=False):
    from .report_configuration_handoff import configuration_row
    return configuration_row(db, p, lock=lock)


def make_preview(p, body, binding):
    if isinstance(body, nested.Preview):
        return nested.make_preview(p, body, binding)
    return seal({'schema': 'business-flow-execution-preview-v1', 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'command': preview_body(body).model_dump(mode='json'), 'binding': binding,
        'scope': '采用选定流程的执行设计并与本工作区共享流程和依据；不启动任务、设备或外部写入',
        'unselected_flow_ids': binding['translation']['unselected_flow_ids'],
        'semantic_quality_verified': False, 'can_execute': False}, 'preview_hash')


def prepare(db, p, body):
    if isinstance(body, nested.Preview):
        return nested.prepare(db, p, body)
    from .report_full_semantic_graph_adoption import members, ability_capabilities
    from .business_flow_execution_graph import build_execution_graph
    require(p, 'admin', 'operator')
    package = sources.fresh(db, p, str(body.qualification_id))
    need(package['qualification']['qualification_hash'] == body.qualification_hash)
    translated = build_execution_graph(package['qualification']['value'], body.flow_id,
        body.execution_bindings, scope_binding=package['scope_binding'])
    need(not translated['issues'] and translated['graph'] is not None,
        {'code': 'business_flow_execution_unresolved', 'issues': translated['issues']}, 422)
    graph = translated['graph']; roles = members(db, p, body.role_bindings, lock=True)
    pins = ability_capabilities(db, p, graph, body.external_node_ids)
    need(set(body.node_actions) == set(pins), '请逐个明确真实能力节点的自动化动作', 422)
    configuration = current_configuration(db, p, lock=True)
    need(configuration.version == body.configuration_revision and digest(configuration.data) == body.configuration_hash,
        '配置版本已变化，请保留输入并重新预览')
    binding = seal({'schema': BINDING, 'business_name': body.business_name,
        'qualification_id': str(body.qualification_id), 'qualification_hash': body.qualification_hash,
        'flow_id': body.flow_id, 'flow': derive_flow(package, body.flow_id), 'graph': graph,
        'role_bindings': roles, 'capability_pins': pins, 'event_source_bindings': {}, 'event_admission_policy': None,
        'source_materials': sources.current_materials(db, p, package), 'source': package,
        'execution_bindings': copy.deepcopy(body.execution_bindings), 'translation': translated,
        'node_actions': copy.deepcopy(body.node_actions), 'external_node_ids': copy.deepcopy(body.external_node_ids)}, 'binding_hash')
    binding = check_binding(binding)
    # This is configuration adoption. Automation is configured/reviewed in its
    # own ordinary step and is still checked by compilation and live execution.
    test_origin = origin('00000000-0000-4000-8000-000000000000', binding, '0' * 64)
    from .setup_api import validate_configuration
    validate_configuration(proposed(configuration.data, body.business_name, binding, test_origin))
    return configuration, make_preview(p, body, binding)


def _audit(row):
    need(type(row.data) is dict and row.data.get('schema') in {LEDGER, nested.LEDGER})
    is_nested = row.data['schema'] == nested.LEDGER
    value = {'schema': nested.LEDGER if is_nested else LEDGER, 'adoption_id': row.id, 'actor_id': row.actor_id,
        'request_id': row.request_id, 'qualification_id': row.qualification_id,
        'configuration_id': row.configuration_id, 'accepted_revision': row.accepted_revision,
        'record_hash': row.record_hash, 'command_hash': row.command_hash, 'body_sha256': row.body_sha256}
    if is_nested:
        binding = row.data['binding']
        value.update(review_mode=binding['review_mode'], simulation_only=binding['simulation_only'])
    return value


def _result(identity, request_id, qualification_id, issued_origin, accepted_revision, configuration_hash, binding):
    value = {'schema': 'business-flow-execution-adoption-result-v1', 'id': identity,
        'request_id': request_id, 'qualification_id': qualification_id, 'origin': issued_origin,
        'accepted_revision': accepted_revision, 'configuration_hash': configuration_hash,
        'binding_hash': binding['binding_hash'], 'can_execute': False}
    if binding['schema'] == nested.BINDING:
        value.update(schema=nested.RESULT, review_mode=binding['review_mode'],
            simulation_only=binding['simulation_only'], value_schema=nested.plans.PLAN)
    return value


def _row_pin(row):
    return {**{k: getattr(row, k) for k in ('id', 'workspace_id', 'actor_id', 'qualification_id',
        'configuration_id', 'request_id', 'business_name', 'accepted_revision', 'exact_body', 'body_sha256', 'command_hash')},
        'version': row.version, 'saved_at': reviews._utc(row.created_at), 'data': row.data}


def checked_row(db, p, row):
    try:
        need(row is not None and row.workspace_id == p.workspace_id and row.version == 1)
        data = row.data
        need(type(data) is dict and set(data) == {'schema', 'binding', 'origin', 'preview', 'result'}
            and data['schema'] in {LEDGER, nested.LEDGER})
        is_nested = data['schema'] == nested.LEDGER
        body = parse(row.exact_body.encode('utf-8'), nested.Adopt if is_nested else Adopt)
        binding = check_binding(data['binding'])
        need((binding['schema'] == nested.BINDING) is is_nested)
        need(row.request_id == str(body.request_id) and row.qualification_id == str(body.qualification_id)
            and row.business_name == body.business_name == binding['business_name']
            and row.body_sha256 == hashlib.sha256(row.exact_body.encode('utf-8')).hexdigest()
            and row.command_hash == digest(body.model_dump(mode='json'))
            and row.record_hash == digest(_row_pin(row)) and row.accepted_revision == body.configuration_revision + 1
            and data['origin'] == origin(row.id, binding, row.command_hash)
            and data['preview'] == make_preview(type('RecordAuthor', (), {'user_id': row.actor_id,
                'workspace_id': row.workspace_id})(), body, binding)
            and body.preview_hash == data['preview']['preview_hash'])
        # Command data and adopted binding must match in both directions.
        need(body.qualification_hash == binding['qualification_hash']
            and (body.flow_index == binding['flow_index'] if is_nested else body.flow_id == binding['flow_id'])
            and reviews._same(body.execution_bindings, binding['execution_bindings'])
            and body.node_actions == binding['node_actions'] and body.external_node_ids == binding['external_node_ids']
            and {k: str(v) for k, v in body.role_bindings.items()} == {k: v['user_id'] for k, v in binding['role_bindings'].items()})
        if is_nested:
            need(reviews._same(body.causal_bindings, binding['causal_bindings'])
                and body.entry_mode == binding['entry_policy']['mode'])
            nested.check_command_extractors(body, binding)
        before = db.scalar(select(SetupRevision.data).where(SetupRevision.workspace_id == p.workspace_id,
            SetupRevision.draft_id == row.configuration_id, SetupRevision.version == body.configuration_revision))
        after = db.scalar(select(SetupRevision.data).where(SetupRevision.workspace_id == p.workspace_id,
            SetupRevision.draft_id == row.configuration_id, SetupRevision.version == row.accepted_revision))
        need(before is not None and after is not None and digest(before) == body.configuration_hash
            and reviews._same(after, proposed(before, row.business_name, binding, data['origin'])))
        expected = _result(row.id, row.request_id, row.qualification_id, data['origin'],
            row.accepted_revision, digest(after), binding)
        need(reviews._same(data['result'], expected))
        events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == AUDIT, Audit.object_id == row.id)))
        need(len(events) == 1 and events[0].actor_id == row.actor_id and reviews._same(events[0].data, _audit(row)))
        sources.check_shared_qualification(db, p, binding['source']['qualification'])
        return copy.deepcopy(data)
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '流程采用原命令、独立审计或真实配置历史无法核对；原记录保留') from None


def checked_origin(db, p, issued_origin):
    need(type(issued_origin) is dict and issued_origin.get('schema') in {ORIGIN, nested.ORIGIN})
    row = db.scalar(select(Adoption).where(Adoption.id == issued_origin.get('adoption_id'), Adoption.workspace_id == p.workspace_id))
    data = checked_row(db, p, row)
    need(data['origin'] == issued_origin)
    return copy.deepcopy(data['binding'])


def verify_origin(db, p, issued_origin, require_current=True):
    from .report_full_semantic_graph_adoption import members, ability_capabilities, automation
    binding = checked_origin(db, p, issued_origin)
    configuration = current_configuration(db, p)
    if require_current:
        need(configuration.data.get(CONFIG_KEY, {}).get(binding['business_name']) == issued_origin)
    sources.current_materials(db, p, binding['source'])
    capability_options = ({'service_delegations': nested.service_delegations(binding['translation'])}
        if binding['schema'] == nested.BINDING else {})
    need(members(db, p, {k: v['user_id'] for k, v in binding['role_bindings'].items()}) == binding['role_bindings']
        and ability_capabilities(db, p, binding['graph'], binding['external_node_ids'],
            **capability_options) == binding['capability_pins'])
    if binding['schema'] == nested.BINDING:
        nested.verify_extractors(db, p, binding)
    automation(configuration.data, binding['business_name'], binding['graph'], binding['node_actions'])
    return binding


def protect_configuration(db, p, previous, proposed_value):
    need(previous.get(CONFIG_KEY, {}) == proposed_value.get(CONFIG_KEY, {}),
        '流程执行出处必须通过专用采用命令保存，不能由普通配置增加或删除')
    _inventory(db, p)
    from .instance_compiler import names
    for name, issued_origin in proposed_value.get(CONFIG_KEY, {}).items():
        binding = checked_origin(db, p, issued_origin)
        need(name == binding['business_name'] and name in names(proposed_value)
            and proposed_value.get('flows', {}).get(name) == binding['flow'],
            '已采用流程的步骤、分支或来源不能由旧线性编辑器改写；原配置保留')
    # A fabricated flow marker without an origin must never fall back to legacy.
    need(all(not f.get('businessFlowSource') or name in proposed_value.get(CONFIG_KEY, {})
        for name, f in proposed_value.get('flows', {}).items()))


def _inventory(db, p):
    rows = list(db.scalars(select(Adoption).where(Adoption.workspace_id == p.workspace_id)))
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == AUDIT)))
    need(len(rows) <= 2000 and len(events) == len(rows) and {r.id for r in rows} == {e.object_id for e in events})
    need(len({r.request_id for r in rows}) == len(rows))
    for row in rows:
        found = [e for e in events if e.object_id == row.id]
        need(len(found) == 1 and found[0].actor_id == row.actor_id and reviews._same(found[0].data, _audit(row)))
    # An orphan grant in configuration history is an independent trace even
    # if both its primary adoption row and its Audit have been removed.
    snapshots = list(db.scalars(select(SetupRevision.data).where(SetupRevision.workspace_id == p.workspace_id)))
    snapshots += list(db.scalars(select(SetupDraft.data).where(SetupDraft.workspace_id == p.workspace_id)))
    by_id = {r.id: r for r in rows}; referenced = set()
    for snapshot in snapshots:
        values = snapshot.get(CONFIG_KEY, {})
        need(type(values) is dict)
        for name, issued in values.items():
            need(type(issued) is dict and issued.get('schema') in {ORIGIN, nested.ORIGIN}
                and issued.get('business_name') == name)
            identity = issued.get('adoption_id'); referenced.add(identity)
            need(identity in by_id and reviews._same(issued, by_id[identity].data['origin']))
    need(referenced == set(by_id))
    return rows


def _recover(db, p, request_id, expected_sha256, *, nested_family):
    require(p, 'admin', 'operator'); reviews.identity(request_id)
    need(type(expected_sha256) is str and reviews.SHA.fullmatch(expected_sha256), code=422)
    reviews._lock(db, {'business_flow_execution_nonce': request_id, 'workspace_id': p.workspace_id})
    matches = [r for r in _inventory(db, p) if r.request_id == request_id]
    need(len(matches) <= 1)
    schema = nested.REQUEST if nested_family else 'business-flow-execution-original-result-v1'
    if not matches:
        return {'schema': schema, 'request_id': request_id,
            'found': False, 'body_sha256': expected_sha256, 'result': None}
    row = matches[0]; need(row.actor_id == p.user_id and row.body_sha256 == expected_sha256)
    need((row.data['schema'] == nested.LEDGER) is nested_family,
        '原采用编号属于另一明确合同，未重新采用或更换原号')
    return {'schema': schema, 'request_id': request_id,
        'found': True, 'body_sha256': expected_sha256, 'result': checked_row(db, p, row)['result']}


def recover(db, p, request_id, expected_sha256):
    return _recover(db, p, request_id, expected_sha256, nested_family=False)


def recover_nested(db, p, request_id, expected_sha256):
    return _recover(db, p, request_id, expected_sha256, nested_family=True)


def _adopt(db, p, raw, *, nested_family):
    require(p, 'admin', 'operator'); body = parse(raw, nested.Adopt if nested_family else Adopt)
    original = (recover_nested if nested_family else recover)(db, p, str(body.request_id), hashlib.sha256(raw).hexdigest())
    if original['found']: return original['result']
    # recover() holds the original nonce and validates the full inventory. Only
    # read-only preparation can prove this absent command has not been applied.
    # Never catch creation/flush/commit failures as a rejection receipt.
    try:
        configuration, preview = prepare(db, p, body)
        need(preview['preview_hash'] == body.preview_hash, '执行设计或来源已变化，请保留输入并重新预览')
    except HTTPException as error:
        if error.status_code not in (409, 422): raise
        raise HTTPException(error.status_code, {'code': ('business_flow_nested_execution_adoption_not_applied' if nested_family
            else 'business_flow_execution_adoption_not_applied'),
            'not_applied': True, 'request_id': str(body.request_id),
            'body_sha256': hashlib.sha256(raw).hexdigest(), 'reason': error.detail}) from None
    identity = uid(); command_hash = digest(body.model_dump(mode='json')); binding = preview['binding']
    issued_origin = origin(identity, binding, command_hash)
    proposal = proposed(configuration.data, body.business_name, binding, issued_origin)
    from .setup_api import validate_configuration
    from .relations import lock_current_actor
    validate_configuration(proposal); lock_current_actor(db, p)
    accepted_revision = configuration.version + 1
    result = _result(identity, str(body.request_id), str(body.qualification_id), issued_origin,
        accepted_revision, digest(proposal), binding)
    row = Adoption(id=identity, workspace_id=p.workspace_id, actor_id=p.user_id,
        qualification_id=str(body.qualification_id), configuration_id=configuration.id,
        request_id=str(body.request_id), business_name=body.business_name, accepted_revision=accepted_revision,
        version=1, created_at=now(), exact_body=raw.decode('utf-8'), body_sha256=hashlib.sha256(raw).hexdigest(),
        command_hash=command_hash, data={'schema': nested.LEDGER if nested_family else LEDGER,
            'binding': binding, 'origin': issued_origin,
            'preview': preview, 'result': result})
    row.record_hash = digest(_row_pin(row))
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=configuration.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(row); configuration.data = proposal; configuration.version = accepted_revision
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=configuration.id, version=accepted_revision, data=proposal))
    db.flush(); audit(db, p, AUDIT, row.id, _audit(row)); db.flush()
    checked_row(db, p, row); db.commit(); return copy.deepcopy(result)


def adopt(db, p, raw):
    return _adopt(db, p, raw, nested_family=False)


def adopt_nested(db, p, raw):
    return _adopt(db, p, raw, nested_family=True)
