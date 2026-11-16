"""Explicit authored graph family; old graph families are never reinterpreted."""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from . import models as m, authored_workflow_store_v208 as store
from .contracts import digest
from .services import scoped

SCHEMA = 'instance-authored-workflow-runtime-v208'
CONFIG_KEY = store.CONFIG_KEY
BINDING = store.BINDING
BUSINESS_MARKER = 'authored_workflow_origin'
MARKERS = ('semantic_runtime_plan', 'full_semantic_runtime_schema', 'full_semantic_runtime_hash')
PLAN_KEYS = {'schema', 'graph_adoptions', 'businesses_hash', 'roles_hash', 'plan_hash'}
same, need = store.same, store.need


def validated_bindings(db, p, config):
    return verify_configuration(db, p, config)


def verify_configuration(db, p, config):
    """Unconditional current-publication gate, even after every marker is lost.

    Historical SetupRevision reading uses checked_origin, never this gate.
    The compiler passes its original, un-enriched current configuration here.
    """
    need(type(config) is dict)
    present = bool(config.get(CONFIG_KEY))
    if not present:
        present = bool(db.scalar(select(store.Adoption.id).where(store.Adoption.workspace_id == p.workspace_id).limit(1))
            or db.scalar(select(store.Command.id).where(store.Command.workspace_id == p.workspace_id).limit(1))
            or db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action.in_([store.COMMAND_AUDIT, store.ADOPTED_AUDIT])).limit(1)))
    if not present: return []
    store.no_other_graphs(config)
    store.inventory(db, p)
    current = store.configuration(db, p)
    need(same(config, current.data), '生成版本须使用已保存的当前配置；历史配置不授新的发布资格')
    values = config.get(CONFIG_KEY)
    need(type(values) is dict and 1 <= len(values) <= 20)
    result = []
    for name, issued in sorted(values.items()):
        binding = store.verify_origin(db, p, issued)
        need(name == binding['business_name'] and same(config.get('flows', {}).get(name), binding['flow']))
        result.append({'origin': copy.deepcopy(issued), 'binding': binding})
    return result


def plan_value(businesses, adoptions, roles):
    need(type(businesses) is list and 1 <= len(businesses) <= 20 and type(roles) is dict
        and type(adoptions) is list and 1 <= len(adoptions) <= 20)
    names = []; by_name = {}
    for business in businesses:
        need(type(business) is dict and type(business.get('name')) is str and business['name'] not in by_name)
        by_name[business['name']] = business
    for item in adoptions:
        need(type(item) is dict and set(item) == {'origin', 'binding'})
        binding = store.check_binding(item['binding']); name = binding['business_name']
        issued = item['origin']
        need(type(issued) is dict and set(issued) == {'schema', 'adoption_id', 'business_name', 'binding_hash', 'command_hash'}
            and issued['schema'] == store.ORIGIN and issued['business_name'] == name
            and issued['binding_hash'] == binding['binding_hash'] and name in by_name and name not in names)
        business = by_name[name]; issues = business.get('issues')
        need(type(issues) is list and all(type(value) is str and value for value in issues)
            and business.get('available') is (not issues)
            and business.get('execution_mode') == ('confirmed_graph_workflow' if not issues else 'blocked')
            and same(business.get(BUSINESS_MARKER), issued) and same(business.get('flow'), binding['flow'])
            and 'full_semantic_graph_origin' not in business and 'full_semantic_runtime_origin' not in business)
        for role, actor in binding['role_bindings'].items():
            need(same(roles.get(role), {key: actor[key] for key in ('user_id', 'username')}))
        names.append(name)
    need(names == sorted(names))
    for business in businesses:
        if business['name'] in names: continue
        need(BUSINESS_MARKER not in business and 'full_semantic_graph_origin' not in business
            and 'full_semantic_runtime_origin' not in business
            and business.get('execution_mode') in {'confirmed_human_workflow', 'blocked'}
            and all(step.get('agent') == '人工确认' for step in business.get('flow', {}).get('steps', [])),
            '本片只能与普通人工业务并存，不能静默组成其他执行计划')
    value = {'schema': SCHEMA, 'graph_adoptions': copy.deepcopy(adoptions),
        'businesses_hash': digest(businesses), 'roles_hash': digest(roles)}
    return {**value, 'plan_hash': digest(value)}


def compile_plan(db, p, config, businesses, roles):
    adoptions = validated_bindings(db, p, config)
    by_name = {item['binding']['business_name']: item for item in adoptions}
    for business in businesses:
        item = by_name.get(business['name'])
        if item:
            need(BUSINESS_MARKER not in business or same(business[BUSINESS_MARKER], item['origin']))
            business[BUSINESS_MARKER] = copy.deepcopy(item['origin'])
    return plan_value(businesses, adoptions, roles)


def verify_plan(snapshot):
    need(type(snapshot) is dict and 'semantic_plan' not in snapshot)
    plan = snapshot.get('semantic_runtime_plan')
    need(type(plan) is dict and set(plan) == PLAN_KEYS and plan['schema'] == SCHEMA)
    expected = plan_value(snapshot['businesses'], plan['graph_adoptions'], snapshot['roles'])
    need(same(plan, expected))
    for key, value in (('full_semantic_runtime_schema', SCHEMA), ('full_semantic_runtime_hash', plan['plan_hash'])):
        if key in snapshot: need(snapshot[key] == value)
    return copy.deepcopy(expected)


def _plan_pins(plan):
    return {'full_semantic_runtime_schema': SCHEMA, 'full_semantic_runtime_hash': plan['plan_hash']}


def pins(snapshot):
    return _plan_pins(verify_plan(snapshot))


def _plan_dependencies(plan):
    result = [node['execution']['capability_id'] for item in plan['graph_adoptions']
        for node in item['binding']['graph']['nodes'] if node['kind'] == 'ability']
    for item in plan['graph_adoptions']:
        binding = item['binding']
        if binding['schema'] in {'authored-service-workflow-binding-v270', 'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'}:
            result.extend(binding['observation_extractor_pins'])
        if binding['schema'] in {'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'}:
            result.extend(binding['candidate_tool_pins'])
    return list(dict.fromkeys(result))


def dependencies(snapshot):
    if type(snapshot) is not dict or snapshot.get('semantic_runtime_plan', {}).get('schema') != SCHEMA: return []
    return _plan_dependencies(verify_plan(snapshot))


def verify_release(db, p, release, *, live=True, business_name=None):
    try:
        need(type(live) is bool and release.workspace_id == p.workspace_id)
        snapshot = release.data['snapshot']; plan = verify_plan(snapshot)
        if business_name is not None: need(any(value['name'] == business_name for value in snapshot['businesses']))
        package = scoped(db, m.Capability, release.package_id, p)
        instance = scoped(db, m.DomainInstance, release.instance_id, p); spec = package.data['specification']
        need(release.status == 'prepared' and (not live or package.status == 'published')
            and package.kind == 'domain_package' and package.namespace == instance.namespace
            and package.content_hash == package.validated_hash == digest(package.data)
            and release.configuration_hash == digest(snapshot) == spec['configuration_hash']
            and same(spec['business_definitions'], snapshot['businesses']))
        for key, value in _plan_pins(plan).items(): need(release.data.get(key) == value and spec.get(key) == value)
        need(set(_plan_dependencies(plan)) <= set(package.data.get('dependencies', [])))
        current = store.configuration(db, p)
        version = release.data.get('configuration_revision')
        config = (current.data if version == current.version else db.scalar(select(m.SetupRevision.data).where(
            m.SetupRevision.workspace_id == p.workspace_id, m.SetupRevision.draft_id == current.id, m.SetupRevision.version == version)))
        need(type(config) is dict); store.no_other_graphs(config)
        expected = {item['binding']['business_name']: item['origin'] for item in plan['graph_adoptions']}
        need(same(config.get(CONFIG_KEY), expected))
        for item in plan['graph_adoptions']:
            binding = item['binding']; name = binding['business_name']
            need(binding['configuration_id'] == current.id and same(config.get('flows', {}).get(name), binding['flow']))
            actual = (store.verify_origin(db, p, item['origin'], require_current=False)
                if live and (business_name is None or business_name == name) else store.checked_origin(db, p, item['origin']))
            need(same(actual, binding))
        return plan
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(409, '人工编制执行图、采用回执或实例冻结版本无法核对；原记录保留') from None


def business_binding(release, business):
    plan = verify_plan(release.data['snapshot'])
    actual = next((item for item in release.data['snapshot']['businesses'] if item['id'] == business.get('id')), None)
    need(actual is not None and same(actual, business) and business.get('execution_mode') == 'confirmed_graph_workflow')
    matches = [item['binding'] for item in plan['graph_adoptions'] if item['binding']['business_name'] == business['name']]
    need(len(matches) == 1)
    return copy.deepcopy(matches[0])


def require_task_start(binding, *, verified_anomaly_handoff, automation_handoff=None):
    if automation_handoff is not None:
        from .business_automation_admission import entry_matches
        need(type(verified_anomaly_handoff) is bool and verified_anomaly_handoff is False)
        need(entry_matches(binding,automation_handoff))
        return
    if binding.get('schema') in {'authored-service-workflow-binding-v270', 'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'}:
        from .authored_workflow_store_v208 import check_binding
        checked = check_binding(binding)
        need(type(verified_anomaly_handoff) is bool and verified_anomaly_handoff is False
            and checked['automation']['policy']['triggers'] == ['人工发起'],
            '此服务流程只接受已确认的人工发起；其他入口需另行核对原授权')
        return
    from .authored_image_workflow_graph_v243 import BINDING as IMAGE_BINDING, TEMPLATE as IMAGE_TEMPLATE
    if binding.get('schema') == IMAGE_BINDING:
        automation = binding.get('automation')
        policy = automation.get('policy') if type(automation) is dict else None
        need(type(verified_anomaly_handoff) is bool and verified_anomaly_handoff is False
            and binding.get('template') == IMAGE_TEMPLATE and type(policy) is dict
            and type(policy.get('triggers')) is list and '人工发起' in policy['triggers'],
            '此图像复核流程未明确允许人工发起；自动入口须使用已授权的原持续规则交接')
        return
    need(type(verified_anomaly_handoff) is bool and binding.get('schema') == BINDING
        and verified_anomaly_handoff is False,
        '人工编制模板只接受正常人工发起；不从异常、定时或设备事件借用启动授权')
