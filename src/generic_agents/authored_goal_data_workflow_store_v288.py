"""V288 source-preserving adoption over the original durable V208 ledger.

The new family rebuilds its own fixed graph. Hashes never substitute current
membership, published capabilities, original materials or adopted-command IO.
The common ledger's fixed family allowlist must explicitly install this family.
"""
import copy
from functools import wraps

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy import select

from . import authored_workflow_store_v208 as common
from . import authored_service_workflow_store_v270 as shared
from . import authored_decision_workflow_store_v286 as candidates
from . import authored_goal_data_workflow_graph_v288 as template
from . import models as m
from .authored_goal_data_workflow_contracts_v288 import (
    BINDING, COMMAND, EXTERNAL_NODE_IDS, NODE_ACTIONS, OPERATIONS, PATH, PREFIX,
    PREVIEW, RESULT, TEMPLATE, Adopt, Preview, configured_policy, parse, preview_body,
)
from .contracts import check_schema, digest
from .workflow_goal_evaluation_contracts_v288 import preparation_policy_schema
from .services import published, require, scoped

need, same = common.need, common.same
_extractor_capability, automation = shared._extractor_capability, shared.automation
_candidate_targets = candidates._candidate_targets


def _lock_people(db, p, body):
    from .relations import lock_current_actor
    pins = list(body.tool_bindings.model_dump(mode='json').values())
    pins += [pin.model_dump(mode='json') for pin in body.observation_extractors]
    identities = {p.user_id, body.reviewer_id, *body.role_bindings.values(), *(pin['actor_id'] for pin in pins)}
    for identity in sorted(identities): common._actor(db, p, identity, lock=True)
    lock_current_actor(db, p)


def _safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try: return function(*args, **kwargs)
        except HTTPException: raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError, ImportError):
            raise HTTPException(409, '原资料准备配方、能力端口或人工流程无法核对；未采用或执行') from None
    return checked


def _roles(db, p, body, flow):
    expected = {step['role'] for step in flow['steps']}; need(set(body.role_bindings) == expected)
    posts = {key: template.tool_role(body.business_name, key) for key in OPERATIONS}
    review_post = template.review_role(body.business_name)
    need(not expected.intersection({*posts.values(), review_post}))
    result = {key: common._actor(db, p, value) for key, value in sorted(body.role_bindings.items())}
    for key, pin in body.tool_bindings.model_dump(mode='json').items():
        result[posts[key]] = common._actor(db, p, pin['actor_id'])
    result[review_post] = common._actor(db, p, body.reviewer_id)
    return result


def _tool_capability(db, p, cap, operation, *, lock=False):
    from .workflow_abilities import eligible, eligible_service
    need(operation in OPERATIONS)
    published(db, p, cap.id, 'tool')
    need(cap.content_hash == cap.validated_hash == digest(cap.data)
        and cap.release_version == cap.data['version'])
    role = template.ROLES[operation]; module = template.module_for(operation)
    if operation in template.declarations():
        declaration = template.declarations()[operation]
        need(module.OP == declaration['operation'])
        module.validate_manifest(cap.data)
        eligible_service(cap, role, service_actor=declaration['service_actor'], operation=declaration['operation'])
    elif operation == 'packet':
        eligible(cap, role, allow_document_packet=True); module.validate_manifest(cap.data)
    elif operation == 'context_reader':
        eligible(cap, role); module.validate_manifest(cap.data)
    else:
        need(operation == 'reasoning' and module.ADAPTER == 'business_goal_ready_v288')
        eligible(cap, role); model = module.validate_manifest(db, p, cap.data)
        if lock:
            actual = scoped(db, m.Capability, model.id, p, True)
            need(actual.status == 'published' and actual.content_hash == actual.validated_hash == digest(actual.data)
                == cap.data['execution']['model_profile_hash'])
    return common.template.capability_pin(cap, role)


def _pins(db, p, body, *, lock=False):
    result, extractors = {}, {}
    for operation, pin in sorted(body.tool_bindings.model_dump(mode='json').items(), key=lambda pair: pair[1]['capability_id']):
        cap = scoped(db, m.Capability, pin['capability_id'], p, lock)
        need(cap.content_hash == pin['capability_hash'])
        result[operation] = _tool_capability(db, p, cap, operation, lock=lock)
    for pin in sorted(body.observation_extractors, key=lambda item: item.capability_id):
        common._actor(db, p, pin.actor_id, lock=lock)
        cap = scoped(db, m.Capability, pin.capability_id, p, lock); need(cap.content_hash == pin.capability_hash)
        extractors[cap.id] = _extractor_capability(db, p, cap)
    return result, extractors


def _build(body, flow, roles, pins):
    return template.build_graph(flow, roles, pins,
        {key: pin['actor_id'] for key, pin in body.tool_bindings.model_dump(mode='json').items()},
        business_name=body.business_name, candidate_specs=[item.model_dump(mode='json') for item in body.candidate_specs],
        preparation_policy=body.preparation_policy.model_dump(mode='json'), reviewer_id=body.reviewer_id,
        decision_intent=body.decision_intent)


@_safe
def prepare(db, p, body, *, lock=False):
    require(p, 'admin', 'operator')
    if lock: _lock_people(db, p, body)
    else: common._actor(db, p, p.user_id)
    row = common.configuration(db, p, lock=lock); common.no_other_graphs(row.data)
    need(row.version == body.configuration_revision and digest(row.data) == body.configuration_hash)
    from .instance_compiler import flow_for, flow_issues, names, signature_value
    need(body.business_name in names(row.data) and body.business_name in row.data.get('flows', {})
        and body.business_name not in row.data.get('businessAliases', {}))
    prior = body.prior_origin.model_dump(mode='json') if body.prior_origin else None
    need(same(row.data.get(common.CONFIG_KEY, {}).get(body.business_name), prior))
    flow = flow_for(row.data, body.business_name); template.linear_flow(flow)
    issues = flow_issues(db, p, row.data, flow, name=body.business_name, require_signature=True)
    need(not issues, {'message': '请先补齐并确认原人工流程', 'issues': issues})
    roles = _roles(db, p, body, flow)
    others = [common.checked_origin(db, p, issued)['role_bindings']
        for name, issued in row.data.get(common.CONFIG_KEY, {}).items() if name != body.business_name]
    common.template.compatible_roles(roles, others)
    pins, extractors = _pins(db, p, body, lock=lock); targets = _candidate_targets(db, p, body, lock=lock)
    built = _build(body, flow, roles, pins)
    from .report_full_semantic_graph_adoption import ability_capabilities
    actual = ability_capabilities(db, p, built['graph'], body.external_node_ids,
        service_delegations={key: {**value, 'side_effect': True} for key, value in built['service_delegations'].items()})
    from .runtime_task_documents import verify_graph_sources
    verify_graph_sources(db, p, built['graph'], current=True)
    binding = common._seal({'schema': BINDING, 'authorship': 'human_authored', 'model_generated': False,
        'semantic_quality_verified': False, 'simulation_only': True, 'template': TEMPLATE,
        'workspace_id': p.workspace_id, 'configuration_id': row.id, 'configuration_revision': row.version,
        'configuration_hash': digest(row.data), 'business_name': body.business_name, 'prior_origin': prior,
        'flow': copy.deepcopy(flow), 'flow_signature': signature_value(row.data, flow), 'flow_hash': digest(flow),
        'role_bindings': roles, 'tool_bindings': body.tool_bindings.model_dump(mode='json'),
        'preparation_policy': configured_policy(body.preparation_policy.model_dump(mode='json')),
        'candidate_specs': [item.model_dump(mode='json') for item in body.candidate_specs],
        'decision_intent': body.decision_intent, 'candidate_tool_pins': targets,
        'observation_extractors': [pin.model_dump(mode='json') for pin in body.observation_extractors],
        'observation_extractor_pins': extractors, 'graph': built['graph'], 'step_mapping': built['step_mapping'],
        'capability_pins': actual, 'service_delegations': built['service_delegations'],
        'node_actions': copy.deepcopy(body.node_actions), 'external_node_ids': copy.deepcopy(body.external_node_ids),
        'event_source_bindings': {}, 'automation': automation(db, p, row, body.business_name),
        'source_materials': common.materials(db, p, flow, lock=lock)})
    return row, make_preview(p, body, check_binding(binding))


def binding_body(binding):
    review_post = template.review_role(binding['business_name'])
    reserved = {template.tool_role(binding['business_name'], key) for key in OPERATIONS} | {review_post}
    return {'schema': 'authored-goal-data-workflow-preview-command-v288',
        **{key: copy.deepcopy(binding[key]) for key in ('configuration_revision', 'configuration_hash', 'business_name',
            'tool_bindings', 'preparation_policy', 'candidate_specs', 'decision_intent', 'observation_extractors',
            'node_actions', 'external_node_ids', 'prior_origin', 'simulation_only')},
        'reviewer_id': binding['role_bindings'][review_post]['user_id'],
        'role_bindings': {key: actor['user_id'] for key, actor in binding['role_bindings'].items() if key not in reserved}}


@_safe
def check_binding(value):
    """Rebuild against the actual current server schemas on every check.

    The former scoped reuse did not cover indirect runtime schema functions.
    Avoid returning a structural success after a nested validator changes;
    DB, permissions, sources and files remain checked by their usual callers.
    """
    return _uncached_check_binding(value)


def _uncached_check_binding(value):
    """Exact structural reconstruction, never a replacement for checked_origin."""
    keys = {'schema', 'authorship', 'model_generated', 'semantic_quality_verified', 'simulation_only', 'template',
        'workspace_id', 'configuration_id', 'configuration_revision', 'configuration_hash', 'business_name', 'prior_origin',
        'flow', 'flow_signature', 'flow_hash', 'role_bindings', 'tool_bindings', 'preparation_policy', 'candidate_specs',
        'decision_intent', 'candidate_tool_pins', 'observation_extractors', 'observation_extractor_pins', 'graph',
        'step_mapping', 'capability_pins', 'service_delegations', 'node_actions', 'external_node_ids',
        'event_source_bindings', 'automation', 'source_materials', 'binding_hash'}
    need(type(value) is dict and set(value) == keys and value['schema'] == BINDING
        and value['template'] == TEMPLATE and value['authorship'] == 'human_authored'
        and value['model_generated'] is False and value['semantic_quality_verified'] is False
        and value['simulation_only'] is True and value['event_source_bindings'] == {}
        and value['binding_hash'] == digest({key: item for key, item in value.items() if key != 'binding_hash'})
        and value['flow_hash'] == digest(value['flow']))
    for key in ('workspace_id', 'configuration_id'): TypeAdapter(common.ID).validate_python(value[key], strict=True)
    body = Preview.model_validate(binding_body(value)); pins = value['capability_pins']
    need(type(pins) is dict and set(pins) == set(OPERATIONS))
    selected = body.tool_bindings.model_dump(mode='json')
    for key, pin in selected.items():
        need(pins[key]['capability_id'] == pin['capability_id'] and pins[key]['capability_hash'] == pin['capability_hash'])
    built = _build(body, value['flow'], value['role_bindings'], pins)
    need(same(built['graph'], value['graph']) and same(built['step_mapping'], value['step_mapping'])
        and same(built['service_delegations'], value['service_delegations']))
    template.service_delegations(value)
    extractors = value['observation_extractor_pins']
    need(type(extractors) is dict and set(extractors) == {pin.capability_id for pin in body.observation_extractors})
    for pin in body.observation_extractors:
        frozen = extractors[pin.capability_id]
        need(type(frozen) is dict and set(frozen) == template.PIN_KEYS and frozen['capability_id'] == pin.capability_id
            and frozen['capability_hash'] == pin.capability_hash and frozen['agent_role'] == 'perception'
            and frozen['input_bindings'] == {} and frozen['arguments'] == {}
            and type(frozen['release_version']) is str and bool(frozen['release_version'])
            and type(frozen['adapter']) is str and bool(frozen['adapter'])
            and type(frozen['max_attempts']) is int and 1 <= frozen['max_attempts'] <= 5)
        check_schema(frozen['input_schema']); check_schema(frozen['output_schema'])
    targets = value['candidate_tool_pins']; declarations = [pin for item in body.candidate_specs for pin in item.capability_pins]
    need(type(targets) is dict and set(targets) == {pin.capability_id for pin in declarations})
    for pin in declarations:
        need(targets[pin.capability_id] == {key: getattr(pin, key) for key in ('capability_id', 'capability_hash', 'release_version')})
    return copy.deepcopy(value)


def service_delegations(binding):
    return template.service_delegations(check_binding(binding))


def make_preview(p, body, binding):
    need(binding['workspace_id'] == p.workspace_id)
    value = {'schema': PREVIEW, 'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'command': preview_body(body),
        'binding': binding, 'projection': template.projection(binding), 'can_execute': False, 'cloud_authorized': False}
    return {**value, 'preview_hash': digest(value)}


@_safe
def verify_origin(db, p, issued, *, require_current=True):
    binding = common.checked_origin(db, p, issued)
    need(binding['schema'] == BINDING and binding['workspace_id'] == p.workspace_id)
    current = common.configuration(db, p)
    need(current.id == binding['configuration_id'])
    if require_current: need(same(current.data.get(common.CONFIG_KEY, {}).get(binding['business_name']), issued))
    common.no_other_graphs(current.data)
    body = Preview.model_validate(binding_body(binding)); roles = _roles(db, p, body, binding['flow'])
    pins, extractors = _pins(db, p, body); targets = _candidate_targets(db, p, body)
    built = _build(body, binding['flow'], roles, pins)
    need(same(roles, binding['role_bindings']) and same(built['graph'], binding['graph'])
        and same(built['step_mapping'], binding['step_mapping']) and same(built['service_delegations'], binding['service_delegations'])
        and same(extractors, binding['observation_extractor_pins']) and same(targets, binding['candidate_tool_pins'])
        and same(automation(db, p, current, body.business_name), binding['automation'])
        and same(common.verify_materials(db, p, binding, current=True), binding['source_materials']))
    from .report_full_semantic_graph_adoption import ability_capabilities
    actual = ability_capabilities(db, p, built['graph'], body.external_node_ids,
        service_delegations={key: {**value, 'side_effect': True} for key, value in built['service_delegations'].items()})
    need(same(actual, binding['capability_pins']))
    from .runtime_task_documents import verify_graph_sources
    verify_graph_sources(db, p, built['graph'], current=True)
    return binding


def preview(db, p, body):
    common.inventory(db, p)
    return prepare(db, p, body)[1]


def adopt(db, p, raw):
    return common.adopt(db, p, raw, _family_path=PATH)


def recover(db, p, identity, expected_hash):
    return common.recover(db, p, identity, expected_hash, _family_path=PATH)


def get_adoption(db, p, identity):
    return common.get_adoption(db, p, identity, _family_path=PATH)


@_safe
def context(db, p, name):
    require(p, 'admin', 'operator'); common._actor(db, p, p.user_id)
    row = common.configuration(db, p); rows, _ = common.inventory(db, p)
    from .instance_compiler import flow_issues, names
    need(name in names(row.data), '当前工作区没有此原业务', 404)
    people = list(db.execute(select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id, m.Membership.role.in_(['admin', 'operator'])).limit(257)))
    need(len(people) <= 256)
    members = [{'user_id': user.id, 'username': user.username, 'membership_role': member.role} for user, member in people]
    caps = list(db.scalars(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
        m.Capability.kind.in_(['tool', 'specialist_model']), m.Capability.status == 'published').limit(257)))
    need(len(caps) <= 256); capabilities, extractors, candidate_capabilities = [], [], []
    for cap in caps:
        try: candidate_capabilities.append(candidates._candidate_capability(cap))
        except (HTTPException, ValueError, TypeError, KeyError, AttributeError): pass
        for operation in OPERATIONS:
            try: pin = _tool_capability(db, p, cap, operation)
            except (HTTPException, ValueError, TypeError, KeyError, AttributeError, ImportError): continue
            capabilities.append({'operation': operation, 'id': cap.id, 'name': cap.name, 'hash': cap.content_hash,
                'release_version': cap.release_version, 'adapter': pin['adapter'],
                'input_schema': copy.deepcopy(pin['input_schema']), 'output_schema': copy.deepcopy(pin['output_schema'])})
        try: pin = _extractor_capability(db, p, cap)
        except HTTPException: continue
        extractors.append({'id': cap.id, 'name': cap.name, 'hash': cap.content_hash, 'release_version': cap.release_version,
            'adapter': pin['adapter'], 'input_schema': copy.deepcopy(pin['input_schema']), 'output_schema': copy.deepcopy(pin['output_schema'])})
    flow = copy.deepcopy(row.data.get('flows', {}).get(name)); reason = None
    target_fields, target_fields_issue = [], None
    try: target_fields = candidates._target_fields(row.data)
    except (HTTPException, ValueError, TypeError, KeyError, AttributeError):
        target_fields_issue = '已保存对象字段尚不能按原编译合同核对；请先保存并核对对象结构。'
    try:
        common.no_other_graphs(row.data); template.linear_flow(flow)
        need(not flow_issues(db, p, row.data, flow, name=name, require_signature=True))
        automation(db, p, row, name)
        need({item['operation'] for item in capabilities} == set(OPERATIONS), '尚缺实际已发布的六种固定能力')
    except (HTTPException, ValueError, TypeError, AttributeError) as error:
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        reason = detail.get('message', '当前前置条件尚未核对') if type(detail) is dict else str(detail)
    return {'schema': 'authored-goal-data-workflow-context-v288', 'configuration_revision': row.version,
        'configuration_hash': digest(row.data), 'business_name': name, 'flow': flow,
        'human_roles': sorted({step.get('role', '') for step in (flow or {}).get('steps', [])}),
        'tool_roles': {key: template.tool_role(name, key) for key in OPERATIONS}, 'review_role': template.review_role(name),
        'prior_origin': copy.deepcopy(row.data.get(common.CONFIG_KEY, {}).get(name)),
        'members': sorted(members, key=lambda item: item['user_id']),
        'capabilities': sorted(capabilities, key=lambda item: (item['operation'], item['id'])),
        'observation_extractors': sorted(extractors, key=lambda item: item['id']),
        'target_fields': target_fields, 'target_fields_issue': target_fields_issue, 'target_fields_source': 'saved_configuration',
        'candidate_capabilities': sorted(candidate_capabilities, key=lambda item: item['id']),
        'adoptions': [get_adoption(db, p, item.id) for item in rows if item.business_name == name and item.data['binding']['schema'] == BINDING],
        'preparation_policy_schema': preparation_policy_schema(),
        'node_actions': copy.deepcopy(NODE_ACTIONS), 'external_node_ids': list(EXTERNAL_NODE_IDS),
        'simulation_only': True, 'decision_intent': 'information_acquisition', 'can_preview': reason is None,
        'reason': reason, 'can_execute': False, 'cloud_authorized': False}
