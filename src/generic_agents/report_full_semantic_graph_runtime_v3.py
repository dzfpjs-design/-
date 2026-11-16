"""Compile and cold-verify graph releases without relaxing older contracts."""
import copy
from fastapi import HTTPException
from .contracts import digest
from . import report_full_semantic_configuration_v1 as semantic
from . import report_full_semantic_runtime_v2 as v2

SCHEMA = 'instance-full-semantic-runtime-plan-v3'
BUSINESS_MARKER = 'full_semantic_graph_origin'
MARKERS = v2.MARKERS
BLOCKED = '此业务的完整条件及协同执行设计尚未独立确认；原设计保留。'


def need(ok):
    if not ok: raise ValueError('full_graph_release_integrity')


def plan_value(designs, businesses, graph_adoptions, legacy_plan, roles):
    from .report_full_semantic_graph_binding_v3 import check_binding
    from .report_full_semantic_graph_adoption import origin_value
    need(type(graph_adoptions) is list and bool(graph_adoptions))
    need(type(businesses) is list and len({b['id'] for b in businesses})==len(businesses)
        and len({b['name'] for b in businesses})==len(businesses))
    designs=[semantic.check_design(d) for d in designs]
    legacy_by_name={}
    if legacy_plan is not None:
        need(type(legacy_plan) is dict and set(legacy_plan)=={'plan','businesses','roles'})
        legacy_by_name={a['origin']['business_name']:a for a in legacy_plan['plan']['adoptions']}
    seen = set()
    for item in graph_adoptions:
        need(type(item) is dict and set(item) == {'origin', 'binding'})
        binding = check_binding(item['binding']); origin = item['origin']
        name = binding['business_name']; need(name not in seen); seen.add(name)
        need(origin == origin_value(origin['adoption_id'], binding,command_hash=origin['command_hash']) and binding['source_design'] in designs)
        business = next((b for b in businesses if b['name'] == name), None)
        need(business is not None and business.get(BUSINESS_MARKER) == origin)
        need(business['flow'] == semantic.derive_flows(binding['source_design'])[name])
        need(business['available'] is (not business['issues']))
        need(business['execution_mode'] == ('confirmed_graph_workflow' if business['available'] else 'blocked'))
        need(name not in binding['blocked_businesses'])
        for role, actor in binding['role_bindings'].items():
            need(roles.get(role) == {key: actor[key] for key in ('user_id', 'username')})
    for business in businesses:
        if business['name'] not in seen:
            need(BUSINESS_MARKER not in business)
            item=legacy_by_name.get(business['name'])
            need((v2.BUSINESS_MARKER in business) is (item is not None))
            if item:
                origin=item['origin']
                need(business[v2.BUSINESS_MARKER]==origin
                    and roles.get(origin['role'])=={'user_id':origin['actor_id'],'username':origin['actor_username']})
            if semantic.signature_context({'fullSemanticDesigns': designs,
                    'flows': {b['name']: b['flow'] for b in businesses}}, business['flow']) is not None:
                need(not business['available'] or item is not None)
    if legacy_plan is not None:
        # The independent v2 contract is checked using its frozen view. The v3
        # projection may only add its own graph availability and origin.
        need(type(legacy_plan) is dict and set(legacy_plan) == {'plan', 'businesses', 'roles'})
        expected = v2.plan_value(designs, legacy_plan['businesses'], legacy_plan['plan']['adoptions'], legacy_plan['roles'])
        need(expected == legacy_plan['plan'])
        need({b['id'] for b in legacy_plan['businesses']}=={b['id'] for b in businesses})
        for old in legacy_plan['businesses']:
            current = next((b for b in businesses if b['id'] == old['id']), None); need(current is not None)
            if current['name'] not in seen: need(current == old)
            else:
                need({k: v for k, v in current.items() if k not in ('issues', 'available', 'execution_mode', BUSINESS_MARKER)}
                    == {k: v for k, v in old.items() if k not in ('issues', 'available', 'execution_mode')})
    value = {'schema': SCHEMA, 'designs': copy.deepcopy(designs),
        'graph_adoptions': copy.deepcopy(graph_adoptions), 'legacy_plan': copy.deepcopy(legacy_plan),
        'businesses_hash': digest(businesses), 'roles_hash': digest(roles)}
    return {**value, 'plan_hash': digest(value)}


def validated_bindings(db, p, config):
    from .report_full_semantic_graph_adoption import CONFIG_KEY, verify_origin
    values = config.get(CONFIG_KEY, {}); need(type(values) is dict and 1 <= len(values) <= 20)
    result = []
    for name, origin in sorted(values.items()):
        binding = verify_origin(db, p, origin)
        need(binding['business_name'] == name and binding['source_design'] in config.get('fullSemanticDesigns', []))
        result.append({'origin': copy.deepcopy(origin), 'binding': binding})
    return result


def compile_plan(db, p, config, businesses, roles):
    adoptions = validated_bindings(db, p, config)
    legacy = None
    if config.get(v2.CONFIG_KEY):
        old_businesses = copy.deepcopy(businesses)
        old_plan = v2.compile_plan(db, p, config, old_businesses)
        old_roles = {}
        for item in old_plan['adoptions']:
            origin = item['origin']; old_roles[origin['role']] = {'user_id': origin['actor_id'], 'username': origin['actor_username']}
        legacy = {'plan': old_plan, 'businesses': old_businesses, 'roles': old_roles}
        businesses[:] = copy.deepcopy(old_businesses)
    by_name = {a['binding']['business_name']: a for a in adoptions}
    for business in businesses:
        item = by_name.get(business['name'])
        if item:
            if legacy:
                business['issues'] = [issue for issue in business['issues'] if issue != v2.BLOCKED_ISSUE]
            business[BUSINESS_MARKER] = copy.deepcopy(item['origin'])
            business['available'] = not business['issues']
            business['execution_mode'] = 'confirmed_graph_workflow' if business['available'] else 'blocked'
        elif legacy is None and semantic.signature_context(config, business['flow']) is not None:
            business['issues'] = list(dict.fromkeys([*business['issues'], BLOCKED]))
            business['available'] = False; business['execution_mode'] = 'blocked'
    from .report_full_semantic_handoff import shared_records
    need({d['handoff_id']: d for d in config['fullSemanticDesigns']}
        == {r['id']: r['design'] for r in shared_records(db, p)})
    return plan_value(config['fullSemanticDesigns'], businesses, adoptions, legacy, roles)


def verify_plan(snapshot):
    plan = snapshot.get('semantic_runtime_plan')
    need(type(plan) is dict and plan.get('schema') == SCHEMA and 'semantic_plan' not in snapshot)
    need(plan == plan_value(plan['designs'], snapshot['businesses'], plan['graph_adoptions'],
        plan['legacy_plan'], snapshot['roles']))
    return copy.deepcopy(plan)


def pins(snapshot):
    plan = verify_plan(snapshot)
    return {'full_semantic_runtime_schema': SCHEMA, 'full_semantic_runtime_hash': plan['plan_hash']}


def verify_release(db, p, release, *, live=True,business_name=None):
    from .models import Capability, DomainInstance
    from .services import scoped
    from .report_full_semantic_graph_adoption import verify_origin
    try:
        snapshot = release.data['snapshot']; plan = verify_plan(snapshot)
        package = scoped(db, Capability, release.package_id, p)
        instance = scoped(db, DomainInstance, release.instance_id, p)
        spec = package.data['specification']
        need(release.status == 'prepared' and (not live or package.status == 'published') and package.kind == 'domain_package'
            and package.namespace == instance.namespace and digest(package.data) == package.content_hash
            and package.validated_hash == package.content_hash and digest(snapshot) == release.configuration_hash
            and spec['configuration_hash'] == release.configuration_hash
            and spec['business_definitions'] == snapshot['businesses'])
        for key, value in pins(snapshot).items(): need(release.data.get(key) == value and spec.get(key) == value)
        for value in (snapshot, release.data, spec):
            need(not any(key in value for key in ('semantic_plan', 'full_semantic_plan_schema', 'full_semantic_plan_hash')))
        for adoption in plan['graph_adoptions']:
            if live and (business_name is None or adoption['binding']['business_name']==business_name):
                need(verify_origin(db, p, adoption['origin'], require_current=False) == adoption['binding'])
            else:
                from .report_full_semantic_graph_adoption import checked, lookup_adoption
                data=checked(db,p,lookup_adoption(db,adoption['origin']['adoption_id']))
                need(data['record']['origin']==adoption['origin'] and data['record']['binding']==adoption['binding'])
        if plan['legacy_plan']:
            from .report_full_semantic_runtime_adoption import verify_origin as verify_old
            for adoption in plan['legacy_plan']['plan']['adoptions']:
                if live and (business_name is None or adoption['binding']['business_name']==business_name):need(verify_old(db, p, adoption['origin'], require_current=False) == adoption['binding'])
                else:
                    from .report_full_semantic_runtime_adoption import checked_ledger,accepted_history,lookup_adoption
                    row=lookup_adoption(db,adoption['origin']['adoption_id']);data=checked_ledger(row,p)
                    accepted_history(db,p,row,data)
                    need(data['record']['origin']==adoption['origin'] and data['record']['binding']==adoption['binding'])
        return plan
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise HTTPException(409, '完整协同执行设计、真实采用回执或冻结版本无法核对，原记录保留') from None


def business_binding(release, business):
    plan = verify_plan(release.data['snapshot'])
    found = [a['binding'] for a in plan['graph_adoptions'] if a['binding']['business_name'] == business['name']]
    need(len(found) == 1 and business['execution_mode'] == 'confirmed_graph_workflow')
    return copy.deepcopy(found[0])


def dependencies(snapshot):
    if snapshot.get('semantic_runtime_plan', {}).get('schema') != SCHEMA: return []
    return list(dict.fromkeys(node['execution']['capability_id']
        for item in verify_plan(snapshot)['graph_adoptions'] for node in item['binding']['graph']['nodes']
        if node['kind'] == 'ability'))
