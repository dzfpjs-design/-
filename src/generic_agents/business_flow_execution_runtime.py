"""Runtime for an explicitly adopted, author-reviewed business-flow version.

The independent adoption module owns source, command and IAM authority.  This
module composes its bindings with *real*, independently verified older plans;
it never retags a qualification as a legacy design or invents a child release.
Pure plan checks establish frozen consistency, not execution permission.
"""
import copy
import json
import math
import re
from uuid import UUID

from fastapi import HTTPException

from .contracts import digest
from .report_text_parse_scope import report_text_parse_scope
from . import report_full_semantic_runtime_v2 as v2


CONFIG_KEY = 'businessFlowExecutions'
SCHEMA = 'instance-business-flow-graph-runtime-v1'
NEW_BINDING = 'business-flow-execution-binding-v1'
ORIGIN = 'business-flow-execution-origin-v1'
NESTED_BINDING = 'business-flow-nested-execution-binding-v195'
NESTED_ORIGIN = 'business-flow-nested-execution-origin-v195'
BUSINESS_MARKER = 'full_semantic_graph_origin'
MARKERS = v2.MARKERS
_PLAN_KEYS = {'schema', 'graph_adoptions', 'base_plan', 'businesses_hash', 'roles_hash', 'plan_hash'}
_ORIGIN_KEYS = {'schema', 'adoption_id', 'business_name', 'binding_hash', 'command_hash'}
_HASH = re.compile(r'^[a-f0-9]{64}$')
_OLD_SCHEMAS = {
    'instance-full-semantic-runtime-plan-v2',
    'instance-full-semantic-runtime-plan-v3',
    'instance-full-semantic-runtime-plan-v4',
    'instance-object-graph-runtime-plan-v171',
}


def need(ok):
    if not ok:
        raise ValueError('business_flow_execution_runtime_integrity')


def _json(value):
    """Typed JSON equality: true, 1 and 1.0 must not authenticate each other."""
    def walk(item, depth=0):
        need(depth <= 128)
        if type(item) is dict:
            need(all(type(key) is str for key in item))
            for child in item.values(): walk(child, depth + 1)
        elif type(item) is list:
            for child in item: walk(child, depth + 1)
        elif type(item) is float:
            need(math.isfinite(item))
        else:
            need(item is None or type(item) in (str, int, bool))
    walk(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False)


def _same(left, right):
    return _json(left) == _json(right)


def _uuid(value):
    need(type(value) is str and str(UUID(value)) == value)


def _businesses(values):
    need(type(values) is list and bool(values))
    for item in values:
        need(type(item) is dict and type(item.get('id')) is str and bool(item['id'])
            and type(item.get('name')) is str and bool(item['name'])
            and type(item.get('flow')) is dict)
    need(len({item['id'] for item in values}) == len(values)
        and len({item['name'] for item in values}) == len(values))


def _origin(origin, binding):
    expected = NESTED_ORIGIN if binding.get('schema') == NESTED_BINDING else ORIGIN
    need(type(origin) is dict and set(origin) == _ORIGIN_KEYS and origin['schema'] == expected)
    _uuid(origin['adoption_id'])
    need(type(origin['command_hash']) is str and _HASH.fullmatch(origin['command_hash']) is not None
        and origin['business_name'] == binding['business_name']
        and origin['binding_hash'] == binding['binding_hash'])


def _new_item(item):
    from .business_flow_execution_adoption import check_binding
    need(type(item) is dict and set(item) == {'origin', 'binding'})
    binding = check_binding(item['binding'])
    need(type(binding) is dict and binding.get('schema') in {NEW_BINDING, NESTED_BINDING}
        and _same(binding, item['binding']))
    _origin(item['origin'], binding)
    return binding


def _base_runtime(schema):
    need(schema in _OLD_SCHEMAS)
    if schema == v2.SCHEMA: return v2
    if schema == 'instance-full-semantic-runtime-plan-v3':
        from . import report_full_semantic_graph_runtime_v3
        return report_full_semantic_graph_runtime_v3
    if schema == 'instance-full-semantic-runtime-plan-v4':
        from . import report_full_semantic_graph_runtime_v4
        return report_full_semantic_graph_runtime_v4
    from . import report_object_graph_runtime_v171
    return report_object_graph_runtime_v171


def _check_base(base):
    if base is None: return None
    need(type(base) is dict and set(base) == {'plan', 'businesses', 'roles'}
        and type(base['plan']) is dict and type(base['roles']) is dict)
    _businesses(base['businesses'])
    runtime = _base_runtime(base['plan'].get('schema'))
    checked = runtime.verify_plan({'semantic_runtime_plan': base['plan'],
        'businesses': base['businesses'], 'roles': base['roles']})
    need(checked is not None and _same(checked, base['plan']))
    return runtime


def _old_graphs(base):
    return [] if base is None else base['plan'].get('graph_adoptions', [])


def _old_linear(base):
    if base is None: return []
    plan = base['plan']
    if plan['schema'] == v2.SCHEMA: return plan['adoptions']
    child = plan.get('legacy_plan')
    return [] if child is None else child['plan']['adoptions']


def plan_value(businesses, graph_adoptions, base_plan, roles):
    """Seal exact frozen views; only the new origin marker overlays the base."""
    _json({'businesses': businesses, 'adoptions': graph_adoptions, 'base': base_plan, 'roles': roles})
    _businesses(businesses)
    need(type(roles) is dict and type(graph_adoptions) is list and bool(graph_adoptions))
    _check_base(base_plan)
    old_graphs = _old_graphs(base_plan)
    need(len(graph_adoptions) > len(old_graphs)
        and _same(graph_adoptions[:len(old_graphs)], old_graphs))
    new_items = graph_adoptions[len(old_graphs):]
    old_names = {item['binding']['business_name'] for item in [*old_graphs, *_old_linear(base_plan)]}
    seen = set()
    by_name = {item['name']: item for item in businesses}
    for item in new_items:
        binding = _new_item(item); name = binding['business_name']
        need(name not in seen and name not in old_names and name in by_name)
        seen.add(name); business = by_name[name]
        need(_same(business.get(BUSINESS_MARKER), item['origin'])
            and v2.BUSINESS_MARKER not in business and _same(business['flow'], binding['flow']))
        issues = business.get('issues')
        need(type(issues) is list and all(type(issue) is str and bool(issue) for issue in issues)
            and business.get('available') is (not issues)
            and business.get('execution_mode') == ('confirmed_graph_workflow' if not issues else 'blocked'))
        for role, actor in binding['role_bindings'].items():
            need(_same(roles.get(role), {key: actor[key] for key in ('user_id', 'username')}))
    need([item['binding']['business_name'] for item in new_items] == sorted(seen))
    if base_plan is not None:
        need(_same(roles, base_plan['roles']))
        without_overlay = copy.deepcopy(businesses)
        for business in without_overlay:
            if business['name'] in seen: del business[BUSINESS_MARKER]
        need(_same(without_overlay, base_plan['businesses']))
    else:
        for business in businesses:
            if business['name'] not in seen:
                need(BUSINESS_MARKER not in business and v2.BUSINESS_MARKER not in business)
    value = {'schema': SCHEMA, 'graph_adoptions': copy.deepcopy(graph_adoptions),
        'base_plan': copy.deepcopy(base_plan), 'businesses_hash': digest(businesses), 'roles_hash': digest(roles)}
    return {**value, 'plan_hash': digest(value)}


def validated_bindings(db, p, config):
    """Only new adopted bindings; older keys remain under their own validators."""
    from .business_flow_execution_adoption import verify_origin
    need(type(config) is dict)
    values = config.get(CONFIG_KEY)
    need(type(values) is dict and 1 <= len(values) <= 20
        and all(type(name) is str and bool(name) for name in values))
    result = []
    for name, origin in sorted(values.items()):
        binding = verify_origin(db, p, origin, require_current=True)
        item = {'origin': copy.deepcopy(origin), 'binding': copy.deepcopy(binding)}
        need(_new_item(item)['business_name'] == name)
        result.append(item)
    return result


def compile_plan(db, p, config, businesses, roles):
    adoptions = validated_bindings(db, p, config)
    _businesses(businesses); need(type(roles) is dict)
    names = {item['binding']['business_name'] for item in adoptions}
    old_config = copy.deepcopy(config); old_config.pop(CONFIG_KEY)
    for key in ('fullSemanticGraphBindings', v2.CONFIG_KEY):
        values = old_config.get(key, {})
        need(type(values) is dict and not names.intersection(values))
    old_businesses = copy.deepcopy(businesses); old_roles = copy.deepcopy(roles)
    for business in old_businesses:
        if business['name'] not in names: continue
        if BUSINESS_MARKER in business:
            item = next(item for item in adoptions if item['binding']['business_name'] == business['name'])
            need(_same(business.pop(BUSINESS_MARKER), item['origin']))
        need(v2.BUSINESS_MARKER not in business)
    base = None
    if old_config.get('fullSemanticGraphBindings'):
        from .report_full_semantic_graph_runtime_v4 import for_configuration
        runtime = for_configuration(old_config)
        old_plan = runtime.compile_plan(db, p, old_config, old_businesses, old_roles)
        base = {'plan': old_plan, 'businesses': old_businesses, 'roles': old_roles}
    elif old_config.get(v2.CONFIG_KEY):
        old_plan = v2.compile_plan(db, p, old_config, old_businesses, roles=old_roles)
        need(old_plan is not None)
        base = {'plan': old_plan, 'businesses': old_businesses, 'roles': old_roles}
    if base is not None: _check_base(base)
    by_name = {item['binding']['business_name']: item for item in adoptions}
    for business in old_businesses:
        if business['name'] in names:
            need(BUSINESS_MARKER not in business and v2.BUSINESS_MARKER not in business)
    businesses[:] = copy.deepcopy(old_businesses)
    for business in businesses:
        item = by_name.get(business['name'])
        if item is not None: business[BUSINESS_MARKER] = copy.deepcopy(item['origin'])
    return plan_value(businesses, [*copy.deepcopy(_old_graphs(base)), *adoptions], base, roles)


def verify_plan(snapshot):
    need(type(snapshot) is dict and 'semantic_plan' not in snapshot)
    plan = snapshot.get('semantic_runtime_plan')
    need(type(plan) is dict and set(plan) == _PLAN_KEYS and plan['schema'] == SCHEMA)
    expected = plan_value(snapshot['businesses'], plan['graph_adoptions'], plan['base_plan'], snapshot['roles'])
    need(_same(plan, expected))
    for key, value in (('full_semantic_runtime_schema', SCHEMA), ('full_semantic_runtime_hash', plan['plan_hash'])):
        if key in snapshot: need(snapshot[key] == value)
    return copy.deepcopy(expected)


def pins(snapshot):
    return _pins_from_plan(verify_plan(snapshot))


def _pins_from_plan(plan):
    """Derive pins only from a plan already verified by this operation."""
    return {'full_semantic_runtime_schema': SCHEMA, 'full_semantic_runtime_hash': plan['plan_hash']}


def _verify_old_graph(db, p, item, *, live):
    from .report_full_semantic_graph_adoption import verify_origin, checked, lookup_adoption
    if live:
        actual = verify_origin(db, p, item['origin'], require_current=False)
    else:
        data = checked(db, p, lookup_adoption(db, item['origin']['adoption_id']))
        need(_same(data['record']['origin'], item['origin']))
        actual = data['record']['binding']
    need(_same(actual, item['binding']))


def _verify_old_linear(db, p, item, *, live):
    from .report_full_semantic_runtime_adoption import verify_origin, checked_ledger, accepted_history, lookup_adoption
    if live:
        actual = verify_origin(db, p, item['origin'], require_current=False)
    else:
        row = lookup_adoption(db, item['origin']['adoption_id']); data = checked_ledger(row, p)
        accepted_history(db, p, row, data)
        need(_same(data['record']['origin'], item['origin']))
        actual = data['record']['binding']
    need(_same(actual, item['binding']))


@report_text_parse_scope()
def verify_release(db, p, release, *, live=True, business_name=None):
    from .business_flow_execution_adoption import verify_origin, checked_origin
    from .models import Capability, DomainInstance
    from .services import scoped
    try:
        need(type(live) is bool)
        snapshot = release.data['snapshot']; plan = verify_plan(snapshot)
        need(release.workspace_id == p.workspace_id)
        if business_name is not None:
            need(type(business_name) is str and any(item['name'] == business_name for item in snapshot['businesses']))
        package = scoped(db, Capability, release.package_id, p)
        instance = scoped(db, DomainInstance, release.instance_id, p)
        spec = package.data['specification']
        need(release.status == 'prepared' and (not live or package.status == 'published')
            and package.kind == 'domain_package' and package.namespace == instance.namespace
            and digest(package.data) == package.content_hash and package.validated_hash == package.content_hash
            and digest(snapshot) == release.configuration_hash
            and spec['configuration_hash'] == release.configuration_hash
            and _same(spec['business_definitions'], snapshot['businesses']))
        for key, value in _pins_from_plan(plan).items():
            need(release.data.get(key) == value and spec.get(key) == value)
        actual_dependencies = package.data.get('dependencies')
        need(type(actual_dependencies) is list and all(type(item) is str for item in actual_dependencies)
            and len(set(actual_dependencies)) == len(actual_dependencies)
            and set(_dependencies_from_plan(plan)) <= set(actual_dependencies))
        for value in (snapshot, release.data, spec):
            need(not any(key in value for key in ('semantic_plan', 'full_semantic_plan_schema', 'full_semantic_plan_hash')))
        for item in plan['graph_adoptions']:
            current = live and (business_name is None or item['binding']['business_name'] == business_name)
            if item['binding']['schema'] in {NEW_BINDING, NESTED_BINDING}:
                actual = (verify_origin(db, p, item['origin'], require_current=False) if current
                    else checked_origin(db, p, item['origin']))
                need(_same(actual, item['binding']))
            else:
                _verify_old_graph(db, p, item, live=current)
        for item in _old_linear(plan['base_plan']):
            current = live and (business_name is None or item['binding']['business_name'] == business_name)
            _verify_old_linear(db, p, item, live=current)
        return plan
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(409, '业务流程执行图、真实采用记录或实例冻结版本无法核对，原记录保留') from None


def business_binding(release, business):
    snapshot = release.data['snapshot']; plan = verify_plan(snapshot)
    need(type(business) is dict)
    actual = next((item for item in snapshot['businesses'] if item['id'] == business.get('id')), None)
    need(actual is not None and _same(actual, business))
    found = [item['binding'] for item in plan['graph_adoptions'] if item['binding']['business_name'] == business['name']]
    need(len(found) == 1 and business.get('execution_mode') == 'confirmed_graph_workflow')
    return copy.deepcopy(found[0])


def require_task_start(binding, *, verified_anomaly_handoff, automation_handoff=None):
    """Entry policy after actual binding and, if present, handoff verification.

    This does not verify or manufacture a Delivery. The Task caller must first
    execute signal_graph_routing.prepare_handoff for the real original row.
    """
    def entry_need(value, message='原异常启动安排或当前流程版本无法核对，原记录保留'):
        if not value:
            raise HTTPException(409, message)
    entry_need(type(verified_anomaly_handoff) is bool)
    if automation_handoff is not None:
        from .business_automation_admission import entry_matches
        entry_need(verified_anomaly_handoff is False)
        entry_need(entry_matches(binding,automation_handoff))
        return
    if binding.get('schema') == NESTED_BINDING:
        flow = binding.get('flow'); policy = binding.get('entry_policy')
        entry_need(type(flow) is dict and type(flow.get('source_flow')) is dict
            and type(flow['source_flow'].get('trigger')) is dict
            and type(policy) is dict and set(policy) == {'mode', 'source_trigger_kind'})
        kind = flow['source_flow']['trigger'].get('kind')
        entry_need(policy['source_trigger_kind'] == kind)
        if policy['mode'] == 'engineering_manual':
            entry_need(binding.get('simulation_only') is True
                and binding.get('review_mode') == 'engineering_simulation',
                '工程启动只接受明确标识的模拟配置，不能代替人审核准的业务启动')
            return
        entry_need(binding.get('simulation_only') is False
            and binding.get('review_mode') == 'author_self_review')
        if policy['mode'] == 'declared_manual':
            entry_need(kind == 'manual', '原流程不是人工启动，请先绑定实际启动来源')
            return
        entry_need(policy['mode'] == 'authorized_anomaly_handoff' and kind == 'event'
            and type(binding.get('event_admission_policy')) is dict
            and binding['event_admission_policy'].get('schema') == 'business-flow-nested-event-admission-v195'
            and verified_anomaly_handoff,
            '此递归流程尚未绑定并核实真实异常交接，不能由普通手动任务替代')
        return
    if binding.get('schema') != NEW_BINDING:
        return
    from .business_flow_execution_graph import BINDINGS, BINDINGS_V2, EVENT_TRIGGER_MODE
    execution = binding.get('execution_bindings')
    entry_need(type(execution) is dict)
    flow = binding.get('flow')
    entry_need(type(flow) is dict and type(flow.get('source_flow')) is dict
        and type(flow['source_flow'].get('trigger')) is dict)
    kind = flow['source_flow']['trigger'].get('kind')
    if execution.get('schema') == BINDINGS:
        entry_need(kind == 'manual')
    else:
        entry_need(execution.get('schema') == BINDINGS_V2 and kind == 'event'
            and execution.get('trigger') == {'mode': EVENT_TRIGGER_MODE})
        entry_need(verified_anomaly_handoff,
            '此流程由异常信号启动；请先在业务工作区明确授权来源、规则和对象，不能用普通手动任务代替原异常交接')


def dependencies(snapshot):
    if type(snapshot) is not dict or snapshot.get('semantic_runtime_plan', {}).get('schema') != SCHEMA: return []
    return _dependencies_from_plan(verify_plan(snapshot))


def _dependencies_from_plan(plan):
    """Preserve every event-source contract check without rechecking the plan."""
    values = []
    for item in plan['graph_adoptions']:
        binding = item['binding']
        values.extend(node['execution']['capability_id'] for node in binding['graph']['nodes'] if node['kind'] == 'ability')
        if binding['schema'] == 'business-flow-nested-execution-binding-v195':
            values.extend(sorted(binding.get('observation_extractor_pins', {})))
        if 'event_source_bindings' in binding:
            from .graph_event_source_families_v136 import check_contract, contract_family, PROCESSING, NATIVE_LEARNED_GEO
            for source in binding['event_source_bindings'].values():
                contract = check_contract(source['contract']); values.append(contract['source_id'])
                if contract_family(contract) == PROCESSING: values.append(contract['extractor_id'])
                elif contract_family(contract) == NATIVE_LEARNED_GEO: values.append(contract['capability_id'])
    return list(dict.fromkeys(values))
