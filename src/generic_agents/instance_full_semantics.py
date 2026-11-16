"""Pinned whole-design snapshots; unsupported semantic execution never degrades."""
import copy
from fastapi import HTTPException
from .contracts import digest
from .models import Capability
from .services import scoped
from . import report_full_semantic_configuration_v1 as semantic

SCHEMA = 'instance-full-semantic-plan-v1'
MARKERS = ('semantic_plan', 'full_semantic_plan_schema', 'full_semantic_plan_hash')


def need(value):
    if not value:
        raise HTTPException(409, '完整业务设计或执行边界无法核对，原记录保留')


def plan_value(designs, businesses):
    need(type(designs) is list and type(businesses) is list)
    need(all(type(b) is dict and type(b.get('name')) is str and type(b.get('id')) is str
        and type(b.get('flow')) is dict for b in businesses))
    need(len({b['name'] for b in businesses}) == len(businesses)
        and len({b['id'] for b in businesses}) == len(businesses))
    checked = [semantic.check_design(design) for design in designs]
    config = {'fullSemanticDesigns': checked, 'flows': {b['name']: b['flow'] for b in businesses}}
    semantic.signature_context(config, {})
    bindings = []
    for business in businesses:
        context = semantic.signature_context(config, business['flow'])
        if context is not None:
            bindings.append({'business_id': business['id'], 'name': business['name'], 'source': context})
    value = {'schema': SCHEMA, 'designs': copy.deepcopy(checked), 'business_bindings': bindings,
        'execution_policy': 'blocked_pending_full_semantic_runtime_v1'}
    return {**value, 'plan_hash': digest(value)}


def compile_plan(db, p, config, businesses):
    marked = 'fullSemanticDesigns' in config or any('fullSemanticSource' in b['flow'] for b in businesses)
    if not marked:
        return None
    from .report_full_semantic_handoff import shared_records, validate_configuration
    validate_configuration(config)
    designs = config.get('fullSemanticDesigns', [])
    issued = {record['id']: record['design'] for record in shared_records(db, p)}
    need({d['handoff_id']: d for d in designs} == issued)
    if not designs:
        return None
    plan = plan_value(designs, businesses)
    for business in businesses:
        context = semantic.signature_context(config, business['flow'])
        if context is not None:
            design = next(d for d in designs if d['handoff_id'] == context['handoff_id'])
            business['issues'] = list(dict.fromkeys(business['issues'] + semantic.execution_issues(design, context['candidate_index'])))
            business['available'] = False
            business['execution_mode'] = 'blocked'
    return plan


def verify_plan(snapshot):
    businesses = snapshot.get('businesses', [])
    need(type(businesses) is list and all(type(b) is dict and type(b.get('flow', {})) is dict for b in businesses))
    marked_flows = any('fullSemanticSource' in b.get('flow', {}) for b in businesses)
    if 'semantic_plan' not in snapshot:
        need(not marked_flows)
        return None
    plan = snapshot['semantic_plan']
    try:
        need(type(plan) is dict and set(plan) == {'schema', 'designs', 'business_bindings', 'execution_policy', 'plan_hash'})
        need(plan == plan_value(plan['designs'], businesses) and bool(plan['designs']))
        for binding in plan['business_bindings']:
            business = next(b for b in businesses if b['id'] == binding['business_id'])
            need(business['available'] is False and business['execution_mode'] == 'blocked')
        return copy.deepcopy(plan)
    except (ValueError, TypeError, KeyError, StopIteration, RecursionError):
        need(False)


def pins(snapshot):
    plan = verify_plan(snapshot)
    return {} if plan is None else {'full_semantic_plan_schema': SCHEMA, 'full_semantic_plan_hash': plan['plan_hash']}


def verify_release(db, p, release):
    snapshot = release.data.get('snapshot', {})
    package = scoped(db, Capability, release.package_id, p) if release.package_id else None
    spec = package.data.get('specification', {}) if package else {}
    marked = any(key in container for container in (snapshot, release.data, spec) for key in MARKERS)
    marked = marked or any('fullSemanticSource' in b.get('flow', {}) for b in snapshot.get('businesses', []))
    marked = marked or any('fullSemanticSource' in b.get('flow', {}) for b in spec.get('business_definitions', []))
    if not marked:
        return None
    need(release.workspace_id == p.workspace_id and digest(snapshot) == release.configuration_hash)
    plan = verify_plan(snapshot)
    need(plan is not None and all(release.data.get(k) == v for k, v in pins(snapshot).items()))
    if package:
        need(all(spec.get(k) == v for k, v in pins(snapshot).items()))
    # This version preserves the whole design but has no formal-source or
    # independent-condition/parallel execution adapter. There is no fallback.
    raise HTTPException(409, '完整业务设计尚未完成正式来源和协同执行核对，不能启用或执行任务')
