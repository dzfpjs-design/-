"""Explicit human graph adoption with original commands and inverse authority."""
import copy
import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import select, text

from . import models as m, authored_workflow_graph_v208 as template
from .authored_workflow_models_v208 import AuthoredWorkflowAdoption as Adoption, AuthoredWorkflowCommand as Command
from .contracts import Contract, digest
from .services import audit, scoped, published, require
from .typed_payload_codec_v131 import canonical_json
from .layered_approval_contract_v175 import original_json

CONFIG_KEY = 'authoredWorkflowGraphs'
PREFIX = '/api/setup/authored-workflow-graphs'
PATH = PREFIX + '/adopt'
BINDING = 'authored-workflow-binding-v208'
ORIGIN = 'authored-workflow-origin-v208'
PREVIEW = 'authored-workflow-preview-v208'
RESULT = 'authored-workflow-adoption-result-v208'
COMMAND = 'authored-workflow-command-v208'
COMMAND_AUDIT = 'authored_workflow.command_v208'
ADOPTED_AUDIT = 'authored_workflow.adopted_v208'
MAX_ADOPTIONS = 256
ID = Annotated[str, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
OTHER_GRAPH_KEYS = ('fullSemanticGraphBindings', 'fullSemanticRuntimeBindings', 'businessFlowExecutions',
    'semantic_runtime_plan', 'semantic_plan', 'full_semantic_runtime_schema', 'full_semantic_runtime_hash')
same = template.same


def _family(path):
    # Only these server-owned literal paths select a family; never import a
    # module, schema or execution implementation supplied by a client.
    if path == PATH:
        import sys
        return sys.modules[__name__]
    if path == '/api/setup/authored-image-workflow-graphs/adopt':
        from . import authored_image_workflow_store_v243
        return authored_image_workflow_store_v243
    if path == '/api/setup/authored-service-workflow-graphs/adopt':
        from . import authored_service_workflow_store_v270
        return authored_service_workflow_store_v270
    if path == '/api/setup/authored-decision-workflow-graphs/adopt':
        from . import authored_decision_workflow_store_v278
        return authored_decision_workflow_store_v278
    if path == '/api/setup/authored-decision-workflow-graphs-v286/adopt':
        from . import authored_decision_workflow_store_v286
        return authored_decision_workflow_store_v286
    if path == '/api/setup/authored-goal-data-workflow-graphs-v288/adopt':
        from . import authored_goal_data_workflow_store_v288
        return authored_goal_data_workflow_store_v288
    need(False, '人工采用的固定命令路径无法核对')


def _binding_family(binding):
    schema = binding.get('schema') if type(binding) is dict else None
    if schema == BINDING:return _family(PATH)
    if schema == 'authored-image-workflow-binding-v243':return _family('/api/setup/authored-image-workflow-graphs/adopt')
    if schema == 'authored-service-workflow-binding-v270':return _family('/api/setup/authored-service-workflow-graphs/adopt')
    if schema == 'authored-decision-workflow-binding-v278':return _family('/api/setup/authored-decision-workflow-graphs/adopt')
    if schema == 'authored-decision-workflow-binding-v286':return _family('/api/setup/authored-decision-workflow-graphs-v286/adopt')
    if schema == 'authored-goal-data-workflow-binding-v288':return _family('/api/setup/authored-goal-data-workflow-graphs-v288/adopt')
    need(False, '人工采用的固定binding家庭无法核对')


def need(value, message='原人工设计、独立命令、配置历史或执行资格无法核对；原记录保留', code=409):
    if not value: raise HTTPException(code, message)


class Reasoning(Contract):
    capability_id: ID
    capability_hash: Hash
    actor_id: ID


class OriginRef(Contract):
    schema: Literal['authored-workflow-origin-v208']
    adoption_id: ID
    business_name: Annotated[str, Field(strict=True, min_length=1, max_length=100)]
    binding_hash: Hash
    command_hash: Hash


class Preview(Contract):
    configuration_revision: Annotated[StrictInt, Field(ge=1)]
    configuration_hash: Hash
    business_name: Annotated[str, Field(strict=True, min_length=1, max_length=100)]
    packet_capability_id: ID
    packet_capability_hash: Hash
    packet_actor_id: ID
    role_bindings: dict[str, ID]
    reasoning: Reasoning | None
    candidate_proposals: list[Annotated[str, Field(strict=True, min_length=1, max_length=2000)]] = Field(max_length=16)
    node_actions: dict[str, Literal['data']]
    external_node_ids: list[Literal['reasoning']] = Field(max_length=1)
    prior_origin: OriginRef | None = None

    @model_validator(mode='after')
    def selected(self):
        if not self.business_name.strip() or '\x00' in self.business_name: raise ValueError('请明确选择当前业务')
        if any(not value.strip() or '\x00' in value for value in self.candidate_proposals): raise ValueError('候选说明不能为空')
        if (self.reasoning is None and (self.candidate_proposals or self.external_node_ids)) or (
                self.reasoning is not None and (not self.candidate_proposals or self.external_node_ids != ['reasoning'])):
            raise ValueError('分析能力、候选和明确外发节点须同时选择；资料包不授云许可')
        expected = {'packet': 'data', **({'reasoning': 'data'} if self.reasoning else {})}
        if self.node_actions != expected: raise ValueError('须逐项明确此模板真实节点的数据处理动作')
        return self


class Adopt(Preview):
    request_id: ID
    preview_hash: Hash
    consent: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit(cls, value):
        if type(value) is not dict or value.get('consent') is not True: raise ValueError('请明确确认本次人工执行设计')
        return value


def parse(raw, model):
    try:
        value, exact, hashed = original_json(raw, model)
        need(len(raw) <= 65536, '人工执行设计请求超过64KiB；原输入保留', 413)
        return value, exact, hashed
    except HTTPException: raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '人工设计请求不符合固定合同；未接受任意执行图或内部参数') from None


def preview_body(body):
    if getattr(body,'schema',None) in {'authored-goal-data-workflow-preview-command-v288','authored-goal-data-workflow-adopt-command-v288'}:
        return _family('/api/setup/authored-goal-data-workflow-graphs-v288/adopt').preview_body(body)
    if getattr(body,'schema',None) in {'authored-decision-workflow-preview-command-v286','authored-decision-workflow-adopt-command-v286'}:
        return _family('/api/setup/authored-decision-workflow-graphs-v286/adopt').preview_body(body)
    if getattr(body,'schema',None) in {'authored-decision-workflow-preview-command-v278','authored-decision-workflow-adopt-command-v278'}:
        return _family('/api/setup/authored-decision-workflow-graphs/adopt').preview_body(body)
    if getattr(body,'schema',None) in {'authored-service-workflow-preview-command-v270','authored-service-workflow-adopt-command-v270'}:
        return _family('/api/setup/authored-service-workflow-graphs/adopt').preview_body(body)
    if getattr(body,'schema',None) in {'authored-image-workflow-preview-command-v243','authored-image-workflow-adopt-command-v243'}:
        return _family('/api/setup/authored-image-workflow-graphs/adopt').preview_body(body)
    return {key: body.model_dump(mode='json')[key] for key in Preview.model_fields}


def configuration(db, p, *, lock=False):
    from .report_configuration_handoff import configuration_row
    return configuration_row(db, p, lock)


def no_other_graphs(value):
    need(type(value) is dict and not any(value.get(key) for key in OTHER_GRAPH_KEYS),
        '本片不混合其他来源的执行图或运行计划；请保留原出处并另行设计')
    from .instance_compiler import names, flow_for
    for name in names(value):
        flow = flow_for(value, name)
        need(not template.SOURCE_MARKERS.intersection(flow)
            and all(type(step) is dict and step.get('agent') == '人工确认'
                and not template.SOURCE_MARKERS.intersection(step) for step in flow.get('steps', [])),
            '本片只与普通人工业务并存，不能剥离旧来源或混入原自动流程')


def _actor(db, p, identity, *, lock=False):
    from .report_full_semantic_runtime_adoption import executor
    return executor(db, p, identity, lock=lock)


def _fingerprint(row):
    return digest({'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'action': row.action, 'object_id': row.object_id, 'data': row.data})


def automation(db, p, row, name, *, _image_family=False):
    from .setup_automation import policy_for, review_state, policy_hash
    from .instance_compiler import identifier
    state = review_state(row.data, name); policy = policy_for(row.data, name)
    need(state['status'] == 'confirmed' and type(policy) is dict
        and (policy.get('triggers') == ['人工发起'] if not _image_family else
            type(policy.get('triggers')) is list and bool(policy['triggers'])
            and len(policy['triggers'])==len(set(policy['triggers']))
            and set(policy['triggers']) <= {'人工发起','新观测到达','定时运行'})
        and policy.get('rules', {}).get('data', {}).get('mode') == '自动运行',
        '请先在现有自动化设置中明确允许数据处理，并保存和确认人工发起范围；本操作不会打开权限' if not _image_family
        else '请先确认人工发起、收到新资料或每日时间的自动化范围，并允许数据处理自动运行；本片尚未接通异常入口')
    review = policy['flow_review']
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == row.id, m.Audit.action == 'setup.automation.review',
        m.Audit.data['flow_hash'].as_string() == state['flow_hash'],
        m.Audit.data['policy_hash'].as_string() == policy_hash(policy)).order_by(m.Audit.created_at.desc(), m.Audit.id.desc()).limit(257)))
    need(len(events) <= 256)
    matches = [event for event in events if event.actor_id == review['actor_id']
        and event.data.get('body', {}).get('business_id') == identifier('business', name)
        and type(event.data.get('saved_revision')) is int and 1 <= event.data['saved_revision'] <= row.version
        and event.data.get('body', {}).get('revision') == event.data['saved_revision'] - 1
        and event.data.get('body_hash') == digest(event.data['body'])
        and event.data.get('record_hash') == digest({key: value for key, value in event.data.items() if key != 'record_hash'})]
    need(bool(matches), '当前自动化确认缺少真实独立回执；请回原设置核对，不由本操作补签')
    event = matches[0]
    return {'policy': copy.deepcopy(policy), 'flow_hash': state['flow_hash'], 'policy_hash': state['policy_hash'],
        'audit_id': event.id, 'audit_hash': _fingerprint(event)}


def materials(db, p, flow, *, current=True, lock=False):
    from .flow_sources import flow_source_ids
    from .evidence import evidence_bytes
    result = []
    for identity in sorted(flow_source_ids(flow)):
        row = scoped(db, m.SetupMaterial, identity, p, lock)
        need(not current or (row.status == 'parsed' and row.archived is False), '设计原文资料已归档或不可用；未借用其他原件')
        if lock: scoped(db, m.Evidence, row.evidence_id, p, True)
        evidence, raw = evidence_bytes(db, p, row.evidence_id)
        need(evidence.sha256 == row.sha256 and hashlib.sha256(raw).hexdigest() == row.sha256)
        result.append({'id': row.id, 'version': row.version, 'hash': row.sha256, 'evidence_id': row.evidence_id,
            'name': row.name, 'status': row.status, 'archived': row.archived, 'blocks_hash': digest(row.data.get('blocks', []))})
    return result


def verify_materials(db, p, binding, *, current=False, lock=False):
    """Original custody survives metadata/archive changes, never damaged bytes."""
    from .flow_sources import flow_source_ids
    from .evidence import evidence_bytes
    expected = binding['source_materials']
    need(type(expected) is list and [item['id'] for item in expected] == sorted(flow_source_ids(binding['flow'])))
    for item in expected:
        row = scoped(db, m.SetupMaterial, item['id'], p, lock)
        if lock: scoped(db, m.Evidence, item['evidence_id'], p, True)
        evidence, raw = evidence_bytes(db, p, item['evidence_id'])
        need(row.evidence_id == evidence.id == item['evidence_id'] and row.sha256 == evidence.sha256 == item['hash']
            and hashlib.sha256(raw).hexdigest() == item['hash'] and row.name == item['name']
            and digest(row.data.get('blocks', [])) == item['blocks_hash'], '设计原文或固定解析内容损坏；原采用记录保留且未替换')
        if current:
            need(row.status == 'parsed' and row.archived is False and row.version == item['version'],
                '设计依据已归档或版本变化；原记录仅供回查，未取得新执行资格')
    return expected


def _pins(db, p, body, *, lock=False):
    from . import workflow_document_packet_v207 as packet, business_reasoning_v197 as reasoning
    from .workflow_abilities import eligible
    wanted = [(body.packet_capability_id, body.packet_capability_hash, 'data')]
    if body.reasoning: wanted.append((body.reasoning.capability_id, body.reasoning.capability_hash, 'business'))
    caps = {}
    for identity, hashed, role in sorted(wanted):
        cap = scoped(db, m.Capability, identity, p, lock)
        published(db, p, cap.id, 'tool')
        need(cap.content_hash == cap.validated_hash == digest(cap.data) == hashed)
        if role == 'data':
            eligible(cap, role, allow_document_packet=True); packet.validate_manifest(cap.data)
        else:
            eligible(cap, role); model = reasoning.validate_manifest(db, p, cap.data)
            if lock:
                actual_model = scoped(db, m.Capability, model.id, p, True)
                need(actual_model.status == 'published'
                    and actual_model.content_hash == actual_model.validated_hash == digest(actual_model.data)
                    == cap.data['execution']['model_profile_hash'])
        caps[role] = template.capability_pin(cap, role)
    return caps


def _roles(db, p, body, flow, *, lock=False):
    expected = {step['role'] for step in flow['steps']}
    need(set(body.role_bindings) == expected, '请选择全部原人工岗位的真实负责人；保留角色由服务器固定')
    packet_post, reasoning_post = template.packet_role(body.business_name), template.reasoning_role(body.business_name)
    need(not expected.intersection({packet_post, reasoning_post}), '原人工岗位与本业务保留处理岗位冲突；未覆盖')
    identities = {**body.role_bindings, packet_post: body.packet_actor_id,
        **({reasoning_post: body.reasoning.actor_id} if body.reasoning else {})}
    return {key: _actor(db, p, value, lock=lock) for key, value in sorted(identities.items())}


def _seal(value): return {**value, 'binding_hash': digest(value)}


def prepare(db, p, body, *, lock=False):
    require(p, 'admin', 'operator'); row = configuration(db, p, lock=lock)
    _actor(db, p, p.user_id, lock=lock); no_other_graphs(row.data)
    need(row.version == body.configuration_revision and digest(row.data) == body.configuration_hash,
        '配置已变化；请保留本次选项并重新核对预览')
    from .instance_compiler import names, flow_for, flow_issues, signature_value
    need(body.business_name in names(row.data) and body.business_name in row.data.get('flows', {})
        and body.business_name not in row.data.get('businessAliases', {}),
        '请选择原普通业务；本片不能从别名或其他图来源推断设计')
    prior = body.prior_origin.model_dump(mode='json') if body.prior_origin else None
    need(same(row.data.get(CONFIG_KEY, {}).get(body.business_name), prior),
        '人工执行设计已有新采用；请核对当前原出处，未覆盖后继版本')
    flow = flow_for(row.data, body.business_name)
    try: template.linear_flow(flow)
    except (ValueError, TypeError, AttributeError): need(False, '仅支持已确认的全人工线性流程；原分支、能力和出处保持不变')
    issues = flow_issues(db, p, row.data, flow, name=body.business_name, require_signature=True)
    need(not issues, {'message': '请先在原流程设置中补齐并确认当前人工流程', 'issues': issues})
    roles = _roles(db, p, body, flow, lock=lock)
    others = [checked_origin(db, p, issued)['role_bindings'] for name, issued in row.data.get(CONFIG_KEY, {}).items()
        if name != body.business_name]
    try: template.compatible_roles(roles, others)
    except ValueError as error: need(False, str(error))
    pins = _pins(db, p, body, lock=lock)
    translated = template.build_graph(flow, roles, pins['data'], body.packet_actor_id,
        reasoning_pin=pins.get('business'), reasoning_actor=body.reasoning.actor_id if body.reasoning else None,
        candidate_proposals=body.candidate_proposals, business_name=body.business_name)
    graph = translated['graph']
    from .runtime_task_documents import verify_graph_sources
    verify_graph_sources(db, p, graph, current=True)
    binding = _seal({'schema': BINDING, 'authorship': 'human_authored', 'model_generated': False,
        'semantic_quality_verified': False, 'template': template.TEMPLATE, 'configuration_id': row.id,
        'configuration_revision': row.version, 'configuration_hash': digest(row.data),
        'business_name': body.business_name, 'prior_origin': prior, 'flow': copy.deepcopy(flow), 'flow_signature': signature_value(row.data, flow),
        'flow_hash': digest(flow), 'role_bindings': roles, 'packet': {'capability_id': body.packet_capability_id,
            'capability_hash': body.packet_capability_hash, 'actor_id': body.packet_actor_id},
        'reasoning': body.reasoning.model_dump(mode='json') if body.reasoning else None,
        'candidate_proposals': copy.deepcopy(body.candidate_proposals), 'graph': graph,
        'step_mapping': translated['step_mapping'], 'capability_pins': {node['id']: copy.deepcopy(node['execution'])
            for node in graph['nodes'] if node['kind'] == 'ability'},
        'node_actions': copy.deepcopy(body.node_actions), 'external_node_ids': copy.deepcopy(body.external_node_ids),
        'event_source_bindings': {}, 'automation': automation(db, p, row, body.business_name),
        'source_materials': materials(db, p, flow, lock=lock)})
    return row, make_preview(p, body, binding)


def check_binding(value):
    if type(value) is dict and value.get('schema') in {'authored-decision-workflow-binding-v278','authored-decision-workflow-binding-v286','authored-goal-data-workflow-binding-v288'}:
        return _binding_family(value).check_binding(value)
    if type(value) is dict and value.get('schema') == 'authored-service-workflow-binding-v270':
        return _binding_family(value).check_binding(value)
    if type(value) is dict and value.get('schema') == 'authored-image-workflow-binding-v243':
        return _binding_family(value).check_binding(value)
    keys = {'schema', 'authorship', 'model_generated', 'semantic_quality_verified', 'template', 'configuration_id',
        'configuration_revision', 'configuration_hash', 'business_name', 'prior_origin', 'flow', 'flow_signature', 'flow_hash',
        'role_bindings', 'packet', 'reasoning', 'candidate_proposals', 'graph', 'step_mapping', 'capability_pins',
        'node_actions', 'external_node_ids', 'event_source_bindings', 'automation', 'source_materials', 'binding_hash'}
    need(type(value) is dict and set(value) == keys and value.get('schema') == BINDING
        and value.get('authorship') == 'human_authored' and value.get('model_generated') is False
        and value.get('semantic_quality_verified') is False and value.get('template') == template.TEMPLATE
        and value.get('binding_hash') == digest({key: item for key, item in value.items() if key != 'binding_hash'}))
    body = Preview.model_validate({'configuration_revision': value['configuration_revision'],
        'configuration_hash': value['configuration_hash'], 'business_name': value['business_name'],
        'packet_capability_id': value['packet']['capability_id'], 'packet_capability_hash': value['packet']['capability_hash'],
        'packet_actor_id': value['packet']['actor_id'], 'role_bindings': {key: actor['user_id'] for key, actor in value['role_bindings'].items()
            if key not in {template.packet_role(value['business_name']), template.reasoning_role(value['business_name'])}}, 'reasoning': value['reasoning'],
        'candidate_proposals': value['candidate_proposals'], 'node_actions': value['node_actions'],
        'external_node_ids': value['external_node_ids'], 'prior_origin': value['prior_origin']})
    pins = value['capability_pins']; packet = pins['packet']; reason = pins.get('reasoning')
    need(packet['capability_id'] == body.packet_capability_id and packet['capability_hash'] == body.packet_capability_hash
        and (reason is not None) is (body.reasoning is not None) and value['event_source_bindings'] == {}
        and value['flow_hash'] == digest(value['flow']))
    if reason: need(reason['capability_id'] == body.reasoning.capability_id and reason['capability_hash'] == body.reasoning.capability_hash)
    rebuilt = template.build_graph(value['flow'], value['role_bindings'], packet, body.packet_actor_id,
        reasoning_pin=reason, reasoning_actor=body.reasoning.actor_id if body.reasoning else None,
        candidate_proposals=body.candidate_proposals, business_name=body.business_name)
    need(same(value['graph'], rebuilt['graph']) and same(value['step_mapping'], rebuilt['step_mapping'])
        and set(pins) == set(body.node_actions))
    return copy.deepcopy(value)


def make_preview(p, body, binding):
    if binding.get('schema') in {'authored-decision-workflow-binding-v278','authored-decision-workflow-binding-v286','authored-goal-data-workflow-binding-v288'}:return _binding_family(binding).make_preview(p,body,binding)
    if binding.get('schema') == 'authored-service-workflow-binding-v270':return _binding_family(binding).make_preview(p,body,binding)
    if binding.get('schema') == 'authored-image-workflow-binding-v243':return _binding_family(binding).make_preview(p,body,binding)
    value = {'schema': PREVIEW, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'command': preview_body(body), 'binding': binding, 'projection': template.projection(binding),
        'can_execute': False, 'cloud_authorized': False}
    return {**value, 'preview_hash': digest(value)}


def origin(identity, binding, command_hash):
    return {'schema': ORIGIN, 'adoption_id': identity, 'business_name': binding['business_name'],
        'binding_hash': binding['binding_hash'], 'command_hash': command_hash}


def proposed(before, binding, issued):
    value = copy.deepcopy(before); name = binding['business_name']
    need(name in value.get('flows', {}) and same(value['flows'][name], binding['flow'])
        and same(value.get(CONFIG_KEY, {}).get(name), binding['prior_origin']))
    value.setdefault(CONFIG_KEY, {})[name] = copy.deepcopy(issued)
    return value


def _command_hash(p, body):
    path='/api/setup/authored-image-workflow-graphs/adopt' if getattr(body,'schema',None)=='authored-image-workflow-adopt-command-v243' else PATH
    if getattr(body,'schema',None)=='authored-service-workflow-adopt-command-v270':
        path='/api/setup/authored-service-workflow-graphs/adopt'
    if getattr(body,'schema',None)=='authored-decision-workflow-adopt-command-v278':
        path='/api/setup/authored-decision-workflow-graphs/adopt'
    if getattr(body,'schema',None)=='authored-decision-workflow-adopt-command-v286':
        path='/api/setup/authored-decision-workflow-graphs-v286/adopt'
    if getattr(body,'schema',None)=='authored-goal-data-workflow-adopt-command-v288':
        path='/api/setup/authored-goal-data-workflow-graphs-v288/adopt'
    return digest({'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'path': path, 'body': body.model_dump(mode='json')})


def _command_proof(row):
    return {key: copy.deepcopy(getattr(row, key)) for key in ('id', 'workspace_id', 'adoption_id', 'actor_id',
        'request_id', 'path', 'exact_body', 'body_sha256', 'command_hash', 'version', 'data')}


def _adoption_proof(row):
    return {key: copy.deepcopy(getattr(row, key)) for key in ('id', 'workspace_id', 'actor_id', 'configuration_id',
        'business_name', 'ordinal', 'prior_adoption_id', 'accepted_revision', 'binding_hash', 'version', 'data')}


def _anchored(db, p, action, identity, actor, expected):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity)))
    need(len(rows) == 1 and rows[0].actor_id == actor and same(rows[0].data, expected))


def _result(row, command, config_hash):
    return {'schema': _family(command.path).RESULT, 'id': row.id, 'request_id': command.request_id,
        'business_name': row.business_name, 'origin': copy.deepcopy(row.data['origin']),
        'accepted_revision': row.accepted_revision, 'configuration_hash': config_hash,
        'binding_hash': row.binding_hash, 'can_execute': False, 'cloud_authorized': False}


def checked_adoption(db, p, row, *, _seen=None):
    """Historical independent authority, without impersonating the old actor."""
    try:
        need(row is not None and row.workspace_id == p.workspace_id and row.version == 1)
        seen = set() if _seen is None else set(_seen)
        need(row.id not in seen and len(seen) < MAX_ADOPTIONS); seen.add(row.id)
        data = row.data; need(type(data) is dict and set(data) == {'binding', 'origin', 'preview', 'command_id'})
        binding = check_binding(data['binding'])
        commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id, Command.adoption_id == row.id)))
        need(len(commands) == 1); command = commands[0]
        family = _family(command.path)
        need(family is _binding_family(binding))
        body, exact, hashed = family.parse(command.exact_body.encode('utf-8'), family.Adopt)
        owner = type('AuthoredRecordOwner', (), {'user_id': row.actor_id, 'workspace_id': p.workspace_id})()
        need(command.version == 1 and command.actor_id == row.actor_id and command.id == data['command_id']
            and command.path == family.PATH and command.request_id == body.request_id and command.body_sha256 == hashed
            and command.exact_body == exact and command.command_hash == _command_hash(owner, body)
            and binding['configuration_id'] == row.configuration_id and binding['business_name'] == row.business_name == body.business_name
            and row.binding_hash == binding['binding_hash'] and row.accepted_revision == body.configuration_revision + 1
            and same(data['origin'], origin(row.id, binding, command.command_hash))
            and same(data['preview'], make_preview(owner, body, binding)) and body.preview_hash == data['preview']['preview_hash']
            and same(preview_body(body), data['preview']['command'])
            and same(binding_body(binding), preview_body(body)))
        prior = binding['prior_origin']
        need(type(row.ordinal) is int and row.ordinal >= 1)
        if prior is None:
            need(row.ordinal == 1 and row.prior_adoption_id is None)
        else:
            parent = scoped(db, Adoption, row.prior_adoption_id, p)
            old = checked_adoption(db, p, parent, _seen=seen)
            need(parent.configuration_id == row.configuration_id and parent.business_name == row.business_name
                and parent.ordinal + 1 == row.ordinal and same(old['origin'], prior)
                and same(old['binding']['flow'], binding['flow']), '原人工设计修订链断裂；未借其他出处替代')
        before = db.scalar(select(m.SetupRevision.data).where(m.SetupRevision.workspace_id == p.workspace_id,
            m.SetupRevision.draft_id == row.configuration_id, m.SetupRevision.version == body.configuration_revision))
        after = db.scalar(select(m.SetupRevision.data).where(m.SetupRevision.workspace_id == p.workspace_id,
            m.SetupRevision.draft_id == row.configuration_id, m.SetupRevision.version == row.accepted_revision))
        from .instance_compiler import signature_value
        need(type(before) is dict and type(after) is dict and digest(before) == body.configuration_hash
            and same(before['flows'][row.business_name], binding['flow'])
            and same(signature_value(before, binding['flow']), binding['flow_signature'])
            and same(json.loads(binding['flow']['confirmedSignature']), binding['flow_signature'])
            and same(after, proposed(before, binding, data['origin']))
            and same(command.data, {'schema': family.COMMAND, 'result': _result(row, command, digest(after))}))
        _anchored(db, p, COMMAND_AUDIT, command.id, row.actor_id, _command_proof(command))
        _anchored(db, p, ADOPTED_AUDIT, row.id, row.actor_id, _adoption_proof(row))
        proof = binding['automation']; event = scoped(db, m.Audit, proof['audit_id'], p)
        need(event.action == 'setup.automation.review' and event.object_id == row.configuration_id
            and _fingerprint(event) == proof['audit_hash'] and same(before['automation']['policies'][row.business_name], proof['policy']))
        verify_materials(db, p, binding)
        return copy.deepcopy(data)
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '人工设计原命令、独立审计或配置历史缺失；未重建原成功记录') from None


def heads(rows):
    result = {}
    for row in sorted(rows, key=lambda item: (item.business_name, item.ordinal)):
        key = (row.configuration_id, row.business_name); parent = result.get(key)
        need((row.ordinal == 1 and row.prior_adoption_id is None) if parent is None
            else row.ordinal == parent.ordinal + 1 and row.prior_adoption_id == parent.id,
            '人工执行设计存在重复分叉或缺失修订；未猜测当前版本')
        result[key] = row
    return result


def inventory(db, p):
    rows = list(db.scalars(select(Adoption).where(Adoption.workspace_id == p.workspace_id).limit(MAX_ADOPTIONS + 1)))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id).limit(MAX_ADOPTIONS + 1)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, ADOPTED_AUDIT])).limit(2 * MAX_ADOPTIONS + 1)))
    need(len(rows) <= MAX_ADOPTIONS and len(commands) == len(rows) and len(events) == 2 * len(rows))
    need({command.adoption_id for command in commands} == {row.id for row in rows}
        and len({command.request_id for command in commands}) == len(commands)
        and {event.object_id for event in events if event.action == COMMAND_AUDIT} == {command.id for command in commands}
        and {event.object_id for event in events if event.action == ADOPTED_AUDIT} == {row.id for row in rows})
    for row in rows: checked_adoption(db, p, row)
    current = db.scalar(select(m.SetupDraft).where(m.SetupDraft.workspace_id == p.workspace_id))
    if current is None: need(not rows)
    else:
        current_heads = heads(rows)
        expected = {name: row.data['origin'] for (identity, name), row in current_heads.items() if identity == current.id}
        need(all(row.configuration_id == current.id for row in rows) and same(current.data.get(CONFIG_KEY, {}), expected),
            '人工设计标记、原行或独立库存缺失；不能退回旧线性执行')
    return rows, commands


def checked_origin(db, p, issued):
    need(type(issued) is dict and set(issued) == {'schema', 'adoption_id', 'business_name', 'binding_hash', 'command_hash'}
        and issued['schema'] == ORIGIN)
    row = scoped(db, Adoption, issued['adoption_id'], p); value = checked_adoption(db, p, row)
    need(same(value['origin'], issued))
    return value['binding']


def verify_origin(db, p, issued, *, require_current=True):
    binding = checked_origin(db, p, issued); current = configuration(db, p)
    if binding['schema'] in {'authored-decision-workflow-binding-v278','authored-decision-workflow-binding-v286','authored-goal-data-workflow-binding-v288'}:
        return _binding_family(binding).verify_origin(db,p,issued,require_current=require_current)
    if binding['schema'] == 'authored-service-workflow-binding-v270':
        return _binding_family(binding).verify_origin(db,p,issued,require_current=require_current)
    if binding['schema'] == 'authored-image-workflow-binding-v243':
        return _binding_family(binding).verify_origin(db,p,issued,require_current=require_current)
    if require_current:
        need(same(current.data.get(CONFIG_KEY, {}).get(binding['business_name']), issued))
    no_other_graphs(current.data)
    body = Preview.model_validate(binding_body(binding))
    need(same(_roles(db, p, body, binding['flow']), binding['role_bindings']))
    pins = _pins(db, p, body)
    actual = template.build_graph(binding['flow'], binding['role_bindings'], pins['data'], body.packet_actor_id,
        reasoning_pin=pins.get('business'), reasoning_actor=body.reasoning.actor_id if body.reasoning else None,
        candidate_proposals=body.candidate_proposals, business_name=body.business_name)
    need(same(actual['graph'], binding['graph']) and same(automation(db, p, current, binding['business_name']), binding['automation'])
        and same(verify_materials(db, p, binding, current=True), binding['source_materials']))
    from .runtime_task_documents import verify_graph_sources
    verify_graph_sources(db, p, binding['graph'], current=True)
    return binding


def binding_body(binding):
    if binding.get('schema') in {'authored-decision-workflow-binding-v278','authored-decision-workflow-binding-v286','authored-goal-data-workflow-binding-v288'}:return _binding_family(binding).binding_body(binding)
    if binding.get('schema') == 'authored-service-workflow-binding-v270':return _binding_family(binding).binding_body(binding)
    if binding.get('schema') == 'authored-image-workflow-binding-v243':return _binding_family(binding).binding_body(binding)
    return copy.deepcopy({key: binding[key] for key in ('configuration_revision', 'configuration_hash', 'business_name',
        'reasoning', 'candidate_proposals', 'node_actions', 'external_node_ids', 'prior_origin')}) | {
        'packet_capability_id': binding['packet']['capability_id'], 'packet_capability_hash': binding['packet']['capability_hash'],
        'packet_actor_id': binding['packet']['actor_id'], 'role_bindings': {key: actor['user_id'] for key, actor in binding['role_bindings'].items()
            if key not in {template.packet_role(binding['business_name']), template.reasoning_role(binding['business_name'])}}}


def protect_configuration(db, p, previous, proposal, *, restore=False):
    rows, _ = inventory(db, p)
    need(same(previous.get(CONFIG_KEY, {}), proposal.get(CONFIG_KEY, {})),
        '人工执行图出处须沿专用预览和采用命令保存，普通编辑或恢复不能增删改')
    values = proposal.get(CONFIG_KEY, {}); need(type(values) is dict)
    current = configuration(db, p)
    expected = {name: row.data['origin'] for (identity, name), row in heads(rows).items() if identity == current.id}
    need(same(values, expected), '已保存的人工采用出处缺失；不能删除标记后退回旧线性执行')
    for name, issued in values.items():
        binding = checked_origin(db, p, issued)
        need(same(proposal.get('flows', {}).get(name), binding['flow']), '已采用原流程不能被普通编辑或历史恢复覆盖')
    if values: no_other_graphs(proposal)
    return proposal


def _nonce(db, p, identity):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest(['authored-v208', p.workspace_id, identity])[:15], 16)})


def recover(db, p, identity, expected_hash, *, _family_path=PATH):
    family = _family(_family_path)
    recovery_schema='authored-image-workflow-recovery-v243' if family.PATH != PATH else 'authored-workflow-recovery-v208'
    if family.PATH == '/api/setup/authored-service-workflow-graphs/adopt':recovery_schema='authored-service-workflow-recovery-v270'
    if family.PATH == '/api/setup/authored-decision-workflow-graphs/adopt':recovery_schema='authored-decision-workflow-recovery-v278'
    if family.PATH == '/api/setup/authored-decision-workflow-graphs-v286/adopt':recovery_schema='authored-decision-workflow-recovery-v286'
    if family.PATH == '/api/setup/authored-goal-data-workflow-graphs-v288/adopt':recovery_schema='authored-goal-data-workflow-recovery-v288'
    _nonce(db, p, identity); rows, commands = inventory(db, p)
    matches = [row for row in commands if row.request_id == identity]
    if not matches: return {'schema': recovery_schema, 'found': False, 'body': None, 'result': None}
    need(len(matches) == 1); command = matches[0]
    need(command.actor_id == p.user_id and command.body_sha256 == expected_hash and command.path == family.PATH, '原人工采用请求不能更换账号、家庭或正文')
    row = next(item for item in rows if item.id == command.adoption_id)
    checked_adoption(db, p, row)
    return {'schema': recovery_schema, 'found': True,
        'body': json.loads(command.exact_body), 'result': copy.deepcopy(command.data['result'])}


def adopt(db, p, raw, *, _family_path=PATH):
    family=_family(_family_path)
    require(p, 'admin', 'operator'); body, exact, hashed = family.parse(raw, family.Adopt)
    original = recover(db, p, body.request_id, hashed, _family_path=family.PATH)
    if original['found']: return original['result']
    current, preview = family.prepare(db, p, body, lock=True)
    need(preview['preview_hash'] == body.preview_hash, '人工设计或权限已变化；请保留输入并重新核对原预览')
    binding = preview['binding']; identity, command_id = m.uid(), m.uid(); command_hash = _command_hash(p, body)
    issued = origin(identity, binding, command_hash); value = proposed(current.data, binding, issued)
    from .setup_api import validate_configuration
    validate_configuration(value)
    before = db.scalar(select(m.SetupRevision).where(m.SetupRevision.workspace_id == p.workspace_id,
        m.SetupRevision.draft_id == current.id, m.SetupRevision.version == current.version))
    if before is None: db.add(m.SetupRevision(workspace_id=p.workspace_id, draft_id=current.id, version=current.version, data=copy.deepcopy(current.data)))
    else: need(same(before.data, current.data), '原配置历史与当前版本不同；未覆盖历史')
    parent = scoped(db, Adoption, binding['prior_origin']['adoption_id'], p) if binding['prior_origin'] else None
    row = Adoption(id=identity, workspace_id=p.workspace_id, actor_id=p.user_id, configuration_id=current.id,
        business_name=body.business_name, ordinal=parent.ordinal + 1 if parent else 1,
        prior_adoption_id=parent.id if parent else None, accepted_revision=current.version + 1, binding_hash=binding['binding_hash'],
        version=1, data={'binding': binding, 'origin': issued, 'preview': preview, 'command_id': command_id})
    command = Command(id=command_id, workspace_id=p.workspace_id, adoption_id=identity, actor_id=p.user_id,
        request_id=body.request_id, path=family.PATH, exact_body=exact, body_sha256=hashed, command_hash=command_hash,
        version=1, data={})
    command.data = {'schema': family.COMMAND, 'result': _result(row, command, digest(value))}
    db.add(row); db.add(command); current.data = value; current.version = row.accepted_revision
    db.add(m.SetupRevision(workspace_id=p.workspace_id, draft_id=current.id, version=current.version, data=copy.deepcopy(value)))
    db.flush(); audit(db, p, COMMAND_AUDIT, command.id, _command_proof(command))
    audit(db, p, ADOPTED_AUDIT, row.id, _adoption_proof(row)); db.flush()
    checked_adoption(db, p, row); db.commit()
    return copy.deepcopy(command.data['result'])


def preview(db, p, body):
    inventory(db, p)
    return prepare(db, p, body)[1]


def context(db, p, name):
    require(p, 'admin', 'operator'); row = configuration(db, p); _actor(db, p, p.user_id)
    rows, _ = inventory(db, p)
    from .instance_compiler import names
    need(name in names(row.data), '当前工作区没有此业务', 404)
    people = db.execute(select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id, m.Membership.role.in_(['admin', 'operator'])).limit(257)).all()
    need(len(people) <= 256, '当前成员清单超过本片容量；未静默截断')
    members = [{'user_id': user.id, 'username': user.username, 'membership_role': member.role} for user, member in people]
    capabilities = []
    caps = list(db.scalars(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
        m.Capability.kind == 'tool', m.Capability.status == 'published',
        m.Capability.data['execution']['adapter'].as_string().in_(['runtime_document_packet', 'business_reasoning'])).limit(257)))
    need(len(caps) <= 256, '当前能力清单超过本片容量；未静默截断')
    from . import workflow_document_packet_v207 as packet, business_reasoning_v197 as reasoning
    for cap in caps:
        try:
            published(db, p, cap.id, 'tool')
            (packet.validate_manifest(cap.data) if cap.data['execution']['adapter'] == packet.ADAPTER
                else reasoning.validate_manifest(db, p, cap.data))
            need(cap.content_hash == cap.validated_hash == digest(cap.data))
        except HTTPException: continue
        capabilities.append({'id': cap.id, 'name': cap.name, 'hash': cap.content_hash,
            'release_version': cap.release_version, 'adapter': cap.data['execution']['adapter']})
    flow = copy.deepcopy(row.data.get('flows', {}).get(name)); reason = None
    try:
        no_other_graphs(row.data); template.linear_flow(flow)
        from .instance_compiler import flow_issues
        need(not flow_issues(db, p, row.data, flow, name=name)); automation(db, p, row, name)
    except (HTTPException, ValueError, TypeError, AttributeError) as error:
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        reason = detail.get('message', '当前设计前置条件尚未核对') if type(detail) is dict else str(detail)
    return {'schema': 'authored-workflow-context-v208', 'configuration_revision': row.version,
        'configuration_hash': digest(row.data), 'business_name': name, 'flow': flow,
        'human_roles': sorted({step.get('role', '') for step in (flow or {}).get('steps', [])}),
        'packet_role': template.packet_role(name), 'reasoning_role': template.reasoning_role(name),
        'prior_origin': copy.deepcopy(row.data.get(CONFIG_KEY, {}).get(name)),
        'members': sorted(members, key=lambda value: value['user_id']), 'capabilities': sorted(capabilities, key=lambda value: value['id']),
        'adoptions': [get_adoption(db, p, item.id) for item in rows if item.configuration_id == row.id and item.business_name == name
            and item.data['binding']['schema'] == BINDING],
        'can_preview': reason is None, 'reason': reason, 'can_execute': False, 'cloud_authorized': False}


def get_adoption(db, p, identity, *, _family_path=PATH):
    row = scoped(db, Adoption, identity, p); value = checked_adoption(db, p, row)
    family=_family(_family_path);need(family is _binding_family(value['binding']), '请通过原家庭入口回查此采用')
    projection=template.projection if family.PATH == PATH else family.template.projection
    view_schema=('authored-workflow-adoption-view-v208' if family.PATH == PATH else
        'authored-service-workflow-adoption-view-v270' if family.PATH == '/api/setup/authored-service-workflow-graphs/adopt' else
        'authored-decision-workflow-adoption-view-v278' if family.PATH == '/api/setup/authored-decision-workflow-graphs/adopt' else
        'authored-decision-workflow-adoption-view-v286' if family.PATH == '/api/setup/authored-decision-workflow-graphs-v286/adopt' else
        'authored-goal-data-workflow-adoption-view-v288' if family.PATH == '/api/setup/authored-goal-data-workflow-graphs-v288/adopt' else
        'authored-image-workflow-adoption-view-v243')
    return {'schema': view_schema, 'id': row.id,
        'business_name': row.business_name, 'accepted_revision': row.accepted_revision,
        'ordinal': row.ordinal, 'prior_origin': value['binding']['prior_origin'],
        'origin': value['origin'], 'binding_hash': row.binding_hash,
        'projection': projection(value['binding']), 'graph': value['binding']['graph'],
        'role_bindings': value['binding']['role_bindings'], 'can_execute': False, 'cloud_authorized': False}
