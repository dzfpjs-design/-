"""A real, narrow runtime contract for an unchanged single manual source step.

No graph is flattened. Independent conditions, interactions, coordination,
branches, multiple steps or participants beyond this one human remain blocked.
Adoption is a workspace execution-design decision, never source-fact approval.
The pure builders cannot establish authority; DB entry points cold-check the
independent adoption command, its accepted configuration history and live source.
"""
import copy
import json
import math
import re
from uuid import UUID

from fastapi import HTTPException

from .contracts import digest
from . import report_full_semantic_configuration_v1 as semantic


SCHEMA = 'instance-full-semantic-runtime-plan-v2'
BINDING = 'report-full-semantic-runtime-binding-v2'
ORIGIN = 'report-full-semantic-runtime-origin-v2'
POLICY = 'source_single_manual_step_only_v2'
SEMANTIC_MODE = 'workspace_workflow_design'
CONFIG_KEY = 'fullSemanticRuntimeBindings'
BUSINESS_MARKER = 'full_semantic_runtime_origin'
MARKERS = ('semantic_runtime_plan', 'full_semantic_runtime_schema', 'full_semantic_runtime_hash')
MAX_BYTES = 24 * 1024 * 1024
BLOCKED_ISSUE = '此完整业务未采用受支持的单个人工步骤执行设计；原条件和协同保留，不会按顺序替代执行。'
_HASH = re.compile(r'^[a-f0-9]{64}$')
_BINDING_KEYS = {'schema', 'handoff_id', 'design_hash', 'business_name', 'candidate_index',
    'business_hash', 'source_design', 'source_business', 'source_manifest', 'proof_id', 'source_proof',
    'manifest_hash', 'proof_hash', 'source_anchor', 'executor', 'trigger_mode', 'runtime_policy',
    'semantic_mode', 'source_semantics_status', 'binding_hash'}
_ORIGIN_KEYS = {'schema', 'adoption_id', 'handoff_id', 'design_hash', 'business_name', 'business_hash',
    'manifest_hash', 'proof_id', 'proof_hash', 'binding_hash', 'actor_id', 'actor_username',
    'role', 'trigger_mode', 'semantic_mode', 'origin_hash'}


def _need(ok, code):
    if not ok:
        raise ValueError('full_semantic_runtime_' + code)


def _keys(value, expected, code):
    _need(type(value) is dict and set(value) == set(expected), code)


def _safe(value):
    def visit(item, depth=0):
        _need(depth <= 48, 'json_depth')
        if type(item) is dict:
            for key, child in item.items():
                _need(type(key) is str, 'json_key'); visit(key, depth + 1); visit(child, depth + 1)
        elif type(item) is list:
            for child in item: visit(child, depth + 1)
        elif type(item) is str:
            _need('\x00' not in item, 'json_nul'); item.encode('utf-8', 'strict')
        else:
            _need(item is None or type(item) in (bool, int)
                or type(item) is float and math.isfinite(item), 'json_value')
    try:
        visit(value)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
        _need(len(encoded) <= MAX_BYTES, 'capacity')
        return encoded
    except (UnicodeError, TypeError, OverflowError, RecursionError):
        raise ValueError('full_semantic_runtime_json') from None


def _uuid(value):
    try:
        _need(type(value) is str and str(UUID(value)) == value, 'uuid')
    except (ValueError, AttributeError):
        raise ValueError('full_semantic_runtime_uuid') from None


def _sealed(value, key):
    _need(type(value.get(key)) is str and bool(_HASH.fullmatch(value[key]))
        and value[key] == digest({k: v for k, v in value.items() if k != key}), key)


def source_business(design, business_name):
    design = semantic.check_design(design)
    _need(type(business_name) is str and business_name.strip() and business_name == business_name.strip(), 'business_name')
    found = [(index, business) for index, business in enumerate(design['result']['businesses'])
        if business['name'] == business_name]
    _need(len(found) == 1, 'business_not_found')
    index, business = found[0]
    # This adapter has no global-claim interpreter or question-resolution path.
    _need(not design['result']['global_claims'] and not design['result']['questions'], 'global_semantics_unsupported')
    _need(len(business['steps']) == 1 and not any(business[key]
        for key in ('interactions', 'conditions', 'coordination')), 'control_structure_unsupported')
    step = business['steps'][0]
    _need(step['agent'] == '人工确认' and step['role'].strip()
        and step['next_index'] is None and step['else_index'] is None
        and not step['branch_condition'].strip(), 'single_manual_step_required')
    people = business['participants']
    _need(len(people) <= 1 and all(person['agent'] in {'人工确认', step['role']} for person in people),
        'participants_unsupported')
    return design, index, copy.deepcopy(business)


def _source_contracts(design, manifest, proof):
    # These already-defined pure validators check the complete closed source and
    # mapping contracts. Neither one authenticates a persisted proof or consent.
    from .report_full_semantic_schema_v1 import _manifest, _proof
    _manifest(design, manifest); _proof(design, manifest, proof)


def build_runtime_binding(design, manifest, proof, business_name, executor, *, proof_id):
    """Closed pure builder; caller owns all source, membership and consent checks."""
    try:
        for value in (design, manifest, proof, executor): _safe(value)
        design, index, business = source_business(design, business_name)
        _source_contracts(design, manifest, proof); _uuid(proof_id)
        _keys(executor, {'user_id', 'username', 'membership_role'}, 'executor_shape')
        _uuid(executor['user_id'])
        _need(type(executor['username']) is str and executor['username'].strip()
            and len(executor['username']) <= 100
            and executor['membership_role'] in {'admin', 'operator'}, 'executor')
        value = {'schema': BINDING, 'handoff_id': design['handoff_id'], 'design_hash': design['design_hash'],
            'business_name': business_name, 'candidate_index': index, 'business_hash': digest(business),
            'source_design': design, 'source_business': business, 'source_manifest': copy.deepcopy(manifest),
            'proof_id': proof_id, 'source_proof': copy.deepcopy(proof), 'manifest_hash': manifest['manifest_hash'],
            'proof_hash': proof['proof_hash'], 'source_anchor': copy.deepcopy(proof['selection']['anchor']),
            'executor': copy.deepcopy(executor), 'trigger_mode': 'manual', 'runtime_policy': POLICY,
            'semantic_mode': SEMANTIC_MODE, 'source_semantics_status': 'not_certified'}
        _safe(value)
        return {**value, 'binding_hash': digest(value)}
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        raise ValueError('full_semantic_runtime_binding_input') from None


def check_runtime_binding(value, design=None):
    _keys(value, _BINDING_KEYS, 'binding_shape'); _safe(value)
    expected = build_runtime_binding(value['source_design'] if design is None else design,
        value['source_manifest'], value['source_proof'], value['business_name'], value['executor'],
        proof_id=value['proof_id'])
    _need(value == expected, 'binding_changed')
    return expected


def build_origin(adoption_id, binding):
    _uuid(adoption_id); binding = check_runtime_binding(binding)
    value = {'schema': ORIGIN, 'adoption_id': adoption_id, **{key: binding[key] for key in (
        'handoff_id', 'design_hash', 'business_name', 'business_hash', 'manifest_hash', 'proof_id',
        'proof_hash', 'binding_hash', 'trigger_mode', 'semantic_mode')},
        'actor_id': binding['executor']['user_id'], 'actor_username': binding['executor']['username'],
        'role': binding['source_business']['steps'][0]['role']}
    return {**value, 'origin_hash': digest(value)}


def check_origin(origin, binding):
    _keys(origin, _ORIGIN_KEYS, 'origin_shape')
    _need(origin == build_origin(origin['adoption_id'], binding), 'origin_changed')
    return copy.deepcopy(origin)


def plan_value(designs, businesses, adoptions, roles):
    """Cold-check every business and exact approved actor; never clear issues."""
    _safe({'designs': designs, 'businesses': businesses, 'adoptions': adoptions, 'roles': roles})
    _need(type(designs) is list and bool(designs) and type(businesses) is list
        and type(adoptions) is list and bool(adoptions) and len(adoptions) <= 20
        and type(roles) is dict, 'plan_inputs')
    designs = [semantic.check_design(design) for design in designs]
    _need(all(type(b) is dict and type(b.get('name')) is str and type(b.get('id')) is str
        and type(b.get('flow')) is dict for b in businesses), 'business_shape')
    _need(len({b['name'] for b in businesses}) == len(businesses)
        and len({b['id'] for b in businesses}) == len(businesses), 'business_duplicates')
    config = {'fullSemanticDesigns': designs, 'flows': {b['name']: b['flow'] for b in businesses}}
    semantic.signature_context(config, {})
    by_name = {}
    for adoption in adoptions:
        _keys(adoption, {'origin', 'binding'}, 'adoption_shape')
        binding = check_runtime_binding(adoption['binding'])
        origin = check_origin(adoption['origin'], binding)
        _need(binding['business_name'] not in by_name
            and any(design == binding['source_design'] for design in designs), 'adoption_design')
        by_name[binding['business_name']] = (origin, binding)
    bindings = []
    for business in businesses:
        context = semantic.signature_context(config, business['flow'])
        if context is None:
            _need(BUSINESS_MARKER not in business and business['name'] not in by_name, 'legacy_adoption')
            continue
        name = business['name']; adopted = by_name.get(name)
        item = {'business_id': business['id'], 'name': name, 'source': context, 'adoption_id': None}
        if adopted:
            origin, binding = adopted
            _need(business.get(BUSINESS_MARKER) == origin, 'business_origin')
            _need(business['flow'] == semantic.derive_flows(binding['source_design'])[name], 'source_flow_changed')
            _need(roles.get(origin['role']) == {'user_id': origin['actor_id'], 'username': origin['actor_username']}, 'executor_binding')
            _need(type(business.get('issues')) is list and all(type(issue) is str and issue for issue in business['issues']), 'business_issues')
            available = not business['issues']
            _need(business.get('available') is available and business.get('execution_mode') == (
                'confirmed_human_workflow' if available else 'blocked'), 'business_availability')
            item['adoption_id'] = origin['adoption_id']
        else:
            _need(BUSINESS_MARKER not in business and business.get('available') is False
                and business.get('execution_mode') == 'blocked', 'unsupported_business_enabled')
        bindings.append(item)
    _need(set(by_name) <= {b['name'] for b in businesses}, 'missing_adopted_business')
    value = {'schema': SCHEMA, 'designs': copy.deepcopy(designs), 'adoptions': copy.deepcopy(adoptions),
        'business_bindings': bindings, 'execution_policy': POLICY}
    _safe(value)
    return {**value, 'plan_hash': digest(value)}


def compile_plan(db, p, config, businesses, *, roles=None):
    """Return v2 plan or None, preserving every existing business issue.

    Pass actual compiled roles to check the caller's role selection immediately.
    When omitted, adoption actors only rebuild the plan; verify_plan/pins still
    require the final snapshot's actual roles before either check or generation.
    Existing business issues are preserved; only a clean approved business runs.
    """
    if CONFIG_KEY not in config:
        return None
    from .report_full_semantic_runtime_adoption import validated_bindings
    adoptions = validated_bindings(db, p, config)
    if not adoptions:
        return None
    by_name = {item['origin']['business_name']: item for item in adoptions}; adopted_roles = {}
    for business in businesses:
        context = semantic.signature_context(config, business['flow'])
        if context is None:
            _need(business['name'] not in by_name, 'legacy_adoption'); continue
        item = by_name.get(business['name'])
        if item is None:
            business['issues'] = list(dict.fromkeys([*business['issues'], BLOCKED_ISSUE]))
            business['available'] = False; business['execution_mode'] = 'blocked'
        else:
            origin = item['origin']; business[BUSINESS_MARKER] = copy.deepcopy(origin)
            actor = {'user_id': origin['actor_id'], 'username': origin['actor_username']}
            _need(origin['role'] not in adopted_roles or adopted_roles[origin['role']] == actor, 'conflicting_role')
            adopted_roles[origin['role']] = actor
            business['available'] = not business['issues']
            business['execution_mode'] = 'confirmed_human_workflow' if business['available'] else 'blocked'
    designs = config.get('fullSemanticDesigns', [])
    from .report_full_semantic_handoff import shared_records
    _need({d['handoff_id']: d for d in designs} == {r['id']: r['design'] for r in shared_records(db, p)}, 'active_designs')
    return plan_value(designs, businesses, adoptions, adopted_roles if roles is None else roles)


def verify_plan(snapshot):
    _need(type(snapshot) is dict, 'snapshot')
    businesses = snapshot.get('businesses', [])
    _need(type(businesses) is list and all(type(b) is dict for b in businesses), 'businesses')
    marked = any(key in snapshot for key in MARKERS)
    marked = marked or any(BUSINESS_MARKER in b for b in businesses)
    if not marked:
        return None
    _need('semantic_plan' not in snapshot, 'mixed_runtime_versions')
    plan = snapshot.get('semantic_runtime_plan')
    _keys(plan, {'schema', 'designs', 'adoptions', 'business_bindings', 'execution_policy', 'plan_hash'}, 'plan_shape')
    expected = plan_value(plan['designs'], snapshot.get('businesses'), plan['adoptions'], snapshot.get('roles'))
    _need(plan == expected, 'plan_changed')
    return expected


def pins(snapshot):
    plan = verify_plan(snapshot)
    return {} if plan is None else {'full_semantic_runtime_schema': SCHEMA,
        'full_semantic_runtime_hash': plan['plan_hash']}


def verify_release(db, p, release):
    """Cold authority gate; never invoke the v1 gate or infer a semantic grant."""
    from .models import Capability, DomainInstance
    from .services import scoped
    from .report_full_semantic_runtime_adoption import verify_origin
    try:
        snapshot = release.data.get('snapshot', {})
        package = scoped(db, Capability, release.package_id, p) if release.package_id else None
        spec = package.data.get('specification', {}) if package else {}
        marked = any(key in value for value in (snapshot, release.data, spec) for key in MARKERS)
        marked = marked or any(BUSINESS_MARKER in b for b in snapshot.get('businesses', []) if type(b) is dict)
        marked = marked or any(BUSINESS_MARKER in b for b in spec.get('business_definitions', []) if type(b) is dict)
        if not marked:
            return None
        _need(release.workspace_id == p.workspace_id and release.status == 'prepared'
            and package is not None and package.kind == 'domain_package' and package.status == 'published', 'release')
        instance = scoped(db, DomainInstance, release.instance_id, p)
        _need(package.namespace == instance.namespace and digest(package.data) == package.content_hash
            and package.validated_hash == package.content_hash and digest(snapshot) == release.configuration_hash
            and spec.get('configuration_hash') == release.configuration_hash
            and spec.get('business_definitions') == snapshot.get('businesses'), 'package')
        plan = verify_plan(snapshot); _need(plan is not None, 'missing_plan')
        for key, value in pins(snapshot).items():
            _need(release.data.get(key) == value and spec.get(key) == value, 'release_pins')
        _need(not any(key in value for value in (release.data, spec) for key in (
            'full_semantic_plan_schema', 'full_semantic_plan_hash', 'semantic_plan')), 'mixed_runtime_versions')
        for adoption in plan['adoptions']:
            _need(verify_origin(db, p, adoption['origin'], require_current=False) == adoption['binding'], 'live_adoption')
        return copy.deepcopy(plan)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise HTTPException(409, '单个人工步骤执行设计、正式来源、采用命令或实例快照无法核对，原记录保留') from None
