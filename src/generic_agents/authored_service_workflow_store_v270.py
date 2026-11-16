"""Authored-service eligibility over the unchanged V208 durable ledger.

Design: rebuild the exact server recipe from published capabilities, current
members, confirmed flow, source materials and automation. Service delegations
belong to this explicit family, never a relabelled V193/V195 source.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import select

from . import authored_workflow_store_v208 as common
from . import authored_service_workflow_graph_v270 as template
from . import models as m
from .authored_service_workflow_contracts_v270 import (
    BINDING, COMMAND, EXTERNAL_NODE_IDS, NODE_ACTIONS, OPERATIONS, PATH, PREFIX,
    PREVIEW, RESULT, TEMPLATE, Adopt, Preview, parse, preview_body,
)
from .contracts import digest
from .services import published, require, scoped, validate


need, same = common.need, common.same


def _lock_people(db, p, body):
    from .relations import lock_current_actor
    pins = list(body.tool_bindings.model_dump(mode='json').values())
    pins += [pin.model_dump(mode='json') for pin in body.observation_extractors]
    identities = {p.user_id, *body.role_bindings.values(), *(pin['actor_id'] for pin in pins)}
    for identity in sorted(identities):
        common._actor(db, p, identity, lock=True)
    lock_current_actor(db, p)


def _roles(db, p, body, flow):
    expected = {step['role'] for step in flow['steps']}
    need(set(body.role_bindings) == expected, '请选择全部原人工岗位；固定服务岗位由服务器明确生成')
    posts = {key: template.tool_role(body.business_name, key) for key in OPERATIONS}
    need(not expected.intersection(posts.values()), '原人工岗位与固定服务岗位冲突')
    result = {key: common._actor(db, p, value) for key, value in sorted(body.role_bindings.items())}
    for key, pin in body.tool_bindings.model_dump(mode='json').items():
        result[posts[key]] = common._actor(db, p, pin['actor_id'])
    return result


def _tool_capability(db, p, cap, operation, *, lock=False):
    from . import business_reasoning_v197 as reasoning
    from . import workflow_document_packet_v207 as packet
    from . import workflow_registered_tool_plan_v201 as planner
    from . import workflow_service_context_v196 as resense
    from . import workflow_task_context_v196 as reader
    from .workflow_abilities import eligible, eligible_service
    published(db, p, cap.id, 'tool')
    need(cap.content_hash == cap.validated_hash == digest(cap.data), '能力发布版本或验证摘要已变化')
    role = template.ROLES[operation]
    if operation in {'planner', 'resense'}:
        module = planner if operation == 'planner' else resense
        module.validate_manifest(cap.data)
        eligible_service(cap, role, service_actor='execution_service', operation=module.OP)
    elif operation == 'packet':
        eligible(cap, role, allow_document_packet=True)
        packet.validate_manifest(cap.data)
    elif operation == 'reasoning':
        eligible(cap, role)
        model = reasoning.validate_manifest(db, p, cap.data)
        if lock:
            actual = scoped(db, m.Capability, model.id, p, True)
            need(actual.status == 'published'
                and actual.content_hash == actual.validated_hash == digest(actual.data)
                == cap.data['execution']['model_profile_hash'])
    else:
        need(operation == 'context_reader')
        eligible(cap, role)
        reader.validate_manifest(cap.data)
    return common.template.capability_pin(cap, role)


def _extractor_capability(db, p, cap):
    from .workflow_abilities import eligible
    published(db, p, cap.id)
    need(cap.kind in {'tool', 'specialist_model'}
        and cap.content_hash == cap.validated_hash == digest(cap.data), '异步感知能力必须是实际验证和发布的工具或专业模型')
    eligible(cap, 'perception')
    return common.template.capability_pin(cap, 'perception')


def _pins(db, p, body, *, lock=False):
    result = {}
    values = body.tool_bindings.model_dump(mode='json')
    for operation, pin in sorted(values.items(), key=lambda pair: pair[1]['capability_id']):
        cap = scoped(db, m.Capability, pin['capability_id'], p, lock)
        need(cap.content_hash == pin['capability_hash'], '选择的固定服务能力版本已变化')
        result[operation] = _tool_capability(db, p, cap, operation, lock=lock)
    extractors = {}
    for pin in sorted(body.observation_extractors, key=lambda value: value.capability_id):
        common._actor(db, p, pin.actor_id, lock=lock)
        cap = scoped(db, m.Capability, pin.capability_id, p, lock)
        need(cap.content_hash == pin.capability_hash, '选择的异步感知能力版本已变化')
        extractors[cap.id] = _extractor_capability(db, p, cap)
    return result, extractors


def _candidate_targets(db, p, body, *, lock=False):
    """Validate actual V201 targets; never reserve, bind Task IDs or dispatch."""
    from . import workflow_registered_tool_plan_v201 as planner
    parent = scoped(db, m.Capability, body.tool_bindings.planner.capability_id, p, lock)
    result = {}
    plans = [value.model_dump(mode='json') for value in body.candidate_plans]
    for candidate in plans:
        for step in candidate['steps']:
            if lock:
                scoped(db, m.Capability, step['capability_id'], p, True)
            target = planner._target(db, p, parent, step, simulation_only=True)
            forced = target.data['execution'].get('input_bindings', {})
            need(not set(step['arguments']).intersection(forced), '候选动作不能覆盖登记能力锁定的服务器输入')
            schema = copy.deepcopy(target.data['input_schema'])
            schema['required'] = [key for key in schema.get('required', []) if key not in forced]
            validate(schema, step['arguments'])
            result[target.id] = {'capability_id': target.id, 'capability_hash': target.content_hash,
                'release_version': target.release_version}
    return {key: result[key] for key in sorted(result)}


def automation(db, p, row, name):
    proof = common.automation(db, p, row, name)
    rules = proof['policy'].get('rules', {})
    need(all(type(rules.get(action)) is dict and rules[action].get('mode')
        in {'自动运行', '执行前确认'} for action in ('write', 'supplement')),
        '请在原自动化设置中明确核对候选动作与补采范围；数据处理许可不授予服务动作')
    return proof


def prepare(db, p, body, *, lock=False):
    require(p, 'admin', 'operator')
    if lock:
        _lock_people(db, p, body)
    else:
        common._actor(db, p, p.user_id)
    row = common.configuration(db, p, lock=lock)
    common.no_other_graphs(row.data)
    need(row.version == body.configuration_revision and digest(row.data) == body.configuration_hash,
        '当前配置已变化；请保留原稿并重新核对服务配方')
    from .instance_compiler import flow_for, flow_issues, names, signature_value
    need(body.business_name in names(row.data) and body.business_name in row.data.get('flows', {})
        and body.business_name not in row.data.get('businessAliases', {}), '请选择当前已保存的原人工业务')
    prior = body.prior_origin.model_dump(mode='json') if body.prior_origin else None
    need(same(row.data.get(common.CONFIG_KEY, {}).get(body.business_name), prior), '此人工流程已有后继采用，未覆盖原出处')
    flow = flow_for(row.data, body.business_name)
    try:
        template.linear_flow(flow)
    except (ValueError, TypeError, AttributeError):
        need(False, '本配方只接收已确认的全人工线性流程，不改写原分支、能力或来源')
    issues = flow_issues(db, p, row.data, flow, name=body.business_name, require_signature=True)
    need(not issues, {'message': '请先补齐并确认当前原人工流程', 'issues': issues})
    roles = _roles(db, p, body, flow)
    others = [common.checked_origin(db, p, issued)['role_bindings']
        for name, issued in row.data.get(common.CONFIG_KEY, {}).items() if name != body.business_name]
    try:
        common.template.compatible_roles(roles, others)
    except ValueError as error:
        need(False, str(error))
    pins, extractors = _pins(db, p, body, lock=lock)
    targets = _candidate_targets(db, p, body, lock=lock)
    actors = {key: value['actor_id'] for key, value in body.tool_bindings.model_dump(mode='json').items()}
    proposals = copy.deepcopy(body.candidate_proposals)
    plans = [value.model_dump(mode='json') for value in body.candidate_plans]
    try:
        translated = template.build_graph(flow, roles, pins, actors, business_name=body.business_name,
            candidate_proposals=proposals, candidate_plans=plans)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        need(False, str(error))
    from .report_full_semantic_graph_adoption import ability_capabilities
    delegated = {key: {**value, 'side_effect': True} for key, value in translated['service_delegations'].items()}
    actual_pins = ability_capabilities(db, p, translated['graph'], body.external_node_ids,
        service_delegations=delegated)
    from .runtime_task_documents import verify_graph_sources
    verify_graph_sources(db, p, translated['graph'], current=True)
    binding = common._seal({'schema': BINDING, 'authorship': 'human_authored', 'model_generated': False,
        'semantic_quality_verified': False, 'simulation_only': True, 'template': TEMPLATE,
        'configuration_id': row.id, 'configuration_revision': row.version, 'configuration_hash': digest(row.data),
        'business_name': body.business_name, 'prior_origin': prior, 'flow': copy.deepcopy(flow),
        'flow_signature': signature_value(row.data, flow), 'flow_hash': digest(flow), 'role_bindings': roles,
        'tool_bindings': body.tool_bindings.model_dump(mode='json'), 'candidate_proposals': proposals,
        'candidate_plans': plans, 'candidate_tool_pins': targets,
        'observation_extractors': [pin.model_dump(mode='json') for pin in body.observation_extractors],
        'observation_extractor_pins': extractors, 'graph': translated['graph'],
        'step_mapping': translated['step_mapping'], 'capability_pins': actual_pins,
        'service_delegations': translated['service_delegations'], 'node_actions': copy.deepcopy(body.node_actions),
        'external_node_ids': copy.deepcopy(body.external_node_ids), 'event_source_bindings': {},
        'automation': automation(db, p, row, body.business_name),
        'source_materials': common.materials(db, p, flow, lock=lock)})
    return row, make_preview(p, body, check_binding(binding))


def binding_body(binding):
    reserved = {template.tool_role(binding['business_name'], operation) for operation in OPERATIONS}
    return {'schema': 'authored-service-workflow-preview-command-v270',
        **{key: copy.deepcopy(binding[key]) for key in ('configuration_revision', 'configuration_hash',
            'business_name', 'tool_bindings', 'candidate_proposals', 'candidate_plans', 'observation_extractors',
            'node_actions', 'external_node_ids', 'prior_origin', 'simulation_only')},
        'role_bindings': {key: actor['user_id'] for key, actor in binding['role_bindings'].items() if key not in reserved}}


def check_binding(value):
    keys = {'schema', 'authorship', 'model_generated', 'semantic_quality_verified', 'simulation_only', 'template',
        'configuration_id', 'configuration_revision', 'configuration_hash', 'business_name', 'prior_origin',
        'flow', 'flow_signature', 'flow_hash', 'role_bindings', 'tool_bindings', 'candidate_proposals',
        'candidate_plans', 'candidate_tool_pins', 'observation_extractors', 'observation_extractor_pins',
        'graph', 'step_mapping', 'capability_pins', 'service_delegations', 'node_actions', 'external_node_ids',
        'event_source_bindings', 'automation', 'source_materials', 'binding_hash'}
    try:
        need(type(value) is dict and set(value) == keys and value['schema'] == BINDING
            and value['template'] == TEMPLATE and value['authorship'] == 'human_authored'
            and value['model_generated'] is False and value['semantic_quality_verified'] is False
            and value['simulation_only'] is True and value['event_source_bindings'] == {}
            and value['binding_hash'] == digest({key: item for key, item in value.items() if key != 'binding_hash'})
            and value['flow_hash'] == digest(value['flow']))
        body = Preview.model_validate(binding_body(value))
        pins = value['capability_pins']
        need(type(pins) is dict and set(pins) == set(OPERATIONS))
        selected = body.tool_bindings.model_dump(mode='json')
        for operation, pin in selected.items():
            need(pins[operation]['capability_id'] == pin['capability_id']
                and pins[operation]['capability_hash'] == pin['capability_hash'])
        translated = template.build_graph(value['flow'], value['role_bindings'], pins,
            {key: pin['actor_id'] for key, pin in selected.items()}, business_name=body.business_name,
            candidate_proposals=body.candidate_proposals,
            candidate_plans=[plan.model_dump(mode='json') for plan in body.candidate_plans])
        need(same(translated['graph'], value['graph'])
            and same(translated['step_mapping'], value['step_mapping'])
            and same(translated['service_delegations'], value['service_delegations']))
        template.service_delegations(value)
        extractors = value['observation_extractor_pins']
        need(type(extractors) is dict and set(extractors) == {pin.capability_id for pin in body.observation_extractors})
        for pin in body.observation_extractors:
            frozen = extractors[pin.capability_id]
            need(type(frozen) is dict and set(frozen) == template.PIN_KEYS
                and frozen['capability_id'] == pin.capability_id and frozen['capability_hash'] == pin.capability_hash
                and frozen['agent_role'] == 'perception' and frozen['input_bindings'] == {} and frozen['arguments'] == {})
            # A declaration uses the same complete port shape without inventing
            # an execution node, activation, dependency edge or result.
            from .contracts import check_schema
            check_schema(frozen['input_schema']); check_schema(frozen['output_schema'])
            need(type(frozen['release_version']) is str and bool(frozen['release_version'])
                and type(frozen['adapter']) is str and bool(frozen['adapter'])
                and type(frozen['max_attempts']) is int and 1 <= frozen['max_attempts'] <= 5)
        targets = value['candidate_tool_pins']
        steps = [step for plan in body.candidate_plans for step in plan.steps]
        need(type(targets) is dict and set(targets) == {step.capability_id for step in steps})
        for step in steps:
            pin = targets[step.capability_id]
            need(type(pin) is dict and set(pin) == {'capability_id', 'capability_hash', 'release_version'}
                and pin['capability_id'] == step.capability_id and pin['capability_hash'] == step.capability_hash
                and type(pin['release_version']) is str and bool(pin['release_version']))
        return copy.deepcopy(value)
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '原服务配方、实际端口或独立来源绑定无法完整重建；未接受伪装的递归来源') from None


def service_delegations(binding):
    return template.service_delegations(check_binding(binding))


def make_preview(p, body, binding):
    value = {'schema': PREVIEW, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'command': preview_body(body), 'binding': binding, 'projection': template.projection(binding),
        'can_execute': False, 'cloud_authorized': False}
    return {**value, 'preview_hash': digest(value)}


def verify_origin(db, p, issued, *, require_current=True):
    binding = common.checked_origin(db, p, issued)
    need(binding['schema'] == BINDING)
    current = common.configuration(db, p)
    if require_current:
        need(same(current.data.get(common.CONFIG_KEY, {}).get(binding['business_name']), issued))
    common.no_other_graphs(current.data)
    body = Preview.model_validate(binding_body(binding))
    need(same(_roles(db, p, body, binding['flow']), binding['role_bindings']))
    pins, extractors = _pins(db, p, body)
    targets = _candidate_targets(db, p, body)
    rebuilt = template.build_graph(binding['flow'], binding['role_bindings'], pins,
        {key: pin['actor_id'] for key, pin in body.tool_bindings.model_dump(mode='json').items()},
        business_name=body.business_name, candidate_proposals=body.candidate_proposals,
        candidate_plans=[plan.model_dump(mode='json') for plan in body.candidate_plans])
    need(same(rebuilt['graph'], binding['graph']) and same(rebuilt['step_mapping'], binding['step_mapping'])
        and same(rebuilt['service_delegations'], binding['service_delegations'])
        and same(extractors, binding['observation_extractor_pins']) and same(targets, binding['candidate_tool_pins'])
        and same(automation(db, p, current, body.business_name), binding['automation'])
        and same(common.verify_materials(db, p, binding, current=True), binding['source_materials']))
    from .report_full_semantic_graph_adoption import ability_capabilities
    delegated = {key: {**value, 'side_effect': True} for key, value in rebuilt['service_delegations'].items()}
    actual = ability_capabilities(db, p, rebuilt['graph'], body.external_node_ids, service_delegations=delegated)
    need(same(actual, binding['capability_pins']))
    from .runtime_task_documents import verify_graph_sources
    verify_graph_sources(db, p, binding['graph'], current=True)
    return binding


def preview(db, p, body):
    common.inventory(db, p)
    return prepare(db, p, body)[1]


def adopt(db, p, raw):
    return common.adopt(db, p, raw, _family_path=PATH)


def recover(db, p, identity, expected_hash):
    return common.recover(db, p, identity, expected_hash, _family_path=PATH)


def get_adoption(db, p, identity):
    result = common.get_adoption(db, p, identity, _family_path=PATH)
    return {**result, 'schema': 'authored-service-workflow-adoption-view-v270'}


def context(db, p, name):
    require(p, 'admin', 'operator')
    common._actor(db, p, p.user_id)
    row = common.configuration(db, p)
    rows, _ = common.inventory(db, p)
    from .instance_compiler import flow_issues, names
    need(name in names(row.data), '当前工作区没有此原业务', 404)
    people = list(db.execute(select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
        m.Membership.role.in_(['admin', 'operator'])).limit(257)))
    need(len(people) <= 256, '当前成员超过容量；未静默截断')
    members = [{'user_id': user.id, 'username': user.username, 'membership_role': member.role} for user, member in people]
    caps = list(db.scalars(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
        m.Capability.kind.in_(['tool', 'specialist_model']), m.Capability.status == 'published').limit(257)))
    need(len(caps) <= 256, '当前已发布能力超过容量；未静默截断')
    capabilities, extractor_options = [], []
    for cap in caps:
        for operation in OPERATIONS:
            try:
                pin = _tool_capability(db, p, cap, operation)
            except HTTPException:
                continue
            item = {'operation': operation, 'id': cap.id, 'name': cap.name, 'hash': cap.content_hash,
                'release_version': cap.release_version, 'adapter': pin['adapter'],
                'input_schema': copy.deepcopy(pin['input_schema']), 'output_schema': copy.deepcopy(pin['output_schema'])}
            if operation == 'planner':
                item['candidate_tool_pins'] = copy.deepcopy(cap.data['specification']['tool_pins'])
            capabilities.append(item)
        try:
            pin = _extractor_capability(db, p, cap)
        except HTTPException:
            continue
        extractor_options.append({'id': cap.id, 'name': cap.name, 'hash': cap.content_hash,
            'release_version': cap.release_version, 'adapter': pin['adapter'],
            'input_schema': copy.deepcopy(pin['input_schema']), 'output_schema': copy.deepcopy(pin['output_schema'])})
    flow = copy.deepcopy(row.data.get('flows', {}).get(name))
    reason = None
    try:
        common.no_other_graphs(row.data)
        template.linear_flow(flow)
        need(not flow_issues(db, p, row.data, flow, name=name, require_signature=True), '请先确认原人工流程')
        automation(db, p, row, name)
        need({item['operation'] for item in capabilities} == set(OPERATIONS), '尚缺本配方必需的真实已发布能力')
    except (HTTPException, ValueError, TypeError, AttributeError) as error:
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        reason = detail.get('message', '当前前置条件尚未核对') if type(detail) is dict else str(detail)
    return {'schema': 'authored-service-workflow-context-v270', 'configuration_revision': row.version,
        'configuration_hash': digest(row.data), 'business_name': name, 'flow': flow,
        'human_roles': sorted({step.get('role', '') for step in (flow or {}).get('steps', [])}),
        'tool_roles': {key: template.tool_role(name, key) for key in OPERATIONS},
        'prior_origin': copy.deepcopy(row.data.get(common.CONFIG_KEY, {}).get(name)),
        'members': sorted(members, key=lambda value: value['user_id']),
        'capabilities': sorted(capabilities, key=lambda value: (value['operation'], value['id'])),
        'observation_extractors': sorted(extractor_options, key=lambda value: value['id']),
        'adoptions': [get_adoption(db, p, item.id) for item in rows if item.business_name == name
            and item.data['binding']['schema'] == BINDING],
        'node_actions': copy.deepcopy(NODE_ACTIONS), 'external_node_ids': list(EXTERNAL_NODE_IDS),
        'simulation_only': True, 'can_preview': reason is None, 'reason': reason,
        'can_execute': False, 'cloud_authorized': False}
