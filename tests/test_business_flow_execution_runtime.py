"""Runtime composition only, using public synthetic fixtures.

Older binding/design/plan validators run unchanged.  The new adoption checker
and DB/source entry points are explicit boundary spies: these tests establish
dispatch and frozen-view consistency, NOT a valid adopted new binding, a real
qualification, IAM, PostgreSQL, source quality or execution permission. Root's
separate actual HTTP/PG journey must establish those authorities.
"""
import copy
import sys
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import business_flow_execution_runtime as runtime
from generic_agents import report_full_semantic_runtime_v2 as v2
from generic_agents import report_full_semantic_graph_runtime_v3 as v3
from generic_agents import report_full_semantic_graph_runtime_v4 as v4
from generic_agents import report_object_graph_runtime_v171 as v171
from generic_agents import report_full_semantic_graph_binding_v4 as event_binding
from generic_agents import report_full_semantic_graph_adoption_v4 as event_adoption
from generic_agents.graph_wait_sources_v118 import POLICY
from tests import test_report_full_semantic_runtime_v2 as linear_scene
from tests import test_report_full_semantic_graph_runtime_v3 as graph_scene
from tests import test_report_object_graph_runtime_v171 as object_scene
from tests.test_report_full_semantic_schema_v1_pure import uid


def seal(value, key='plan_hash'):
    value[key] = digest({name: item for name, item in value.items() if name != key})
    return value


def new_binding(name='独立业务🙂'):
    """A boundary DTO only; it is deliberately not a fabricated authority proof."""
    flow = {'name': name, 'steps': [{'id': 'original_' + str(i), 'name': '原步骤' + str(i),
        'purpose': '完整保留的合成说明', 'unit': '米', 'required': False} for i in range(17)]}
    value = {'schema': runtime.NEW_BINDING, 'business_name': name, 'qualification_id': uid(19101),
        'qualification_hash': '1' * 64, 'flow_id': 'original_flow', 'flow': flow,
        'graph': {'nodes': [{'id': 'native_human', 'kind': 'human'}]},
        'role_bindings': {'独立核对职责': {'user_id': uid(19102), 'username': '合成人员', 'membership_role': 'operator'}},
        'capability_pins': {}, 'event_source_bindings': {}, 'event_admission_policy': copy.deepcopy(POLICY),
        'source_materials': [], 'source': {'boundary_only': True}, 'execution_bindings': {}, 'translation': {}}
    return seal(value, 'binding_hash')


def origin(binding, *, index=0):
    return {'schema': runtime.ORIGIN, 'adoption_id': uid(19200 + index),
        'business_name': binding['business_name'], 'binding_hash': binding['binding_hash'], 'command_hash': '2' * 64}


@pytest.fixture
def boundary(monkeypatch):
    seen = []
    def check_binding(value):
        # The adapter checks exact new marker/self hash, not source authority.
        assert value['schema'] == runtime.NEW_BINDING
        assert digest({k: v for k, v in value.items() if k != 'binding_hash'}) == value['binding_hash']
        return copy.deepcopy(value)
    def verify_origin(db, p, value, *, require_current=True):
        seen.append(('live', require_current, copy.deepcopy(value)))
        return copy.deepcopy(db['new'][value['business_name']])
    def checked_origin(db, p, value):
        seen.append(('history', None, copy.deepcopy(value)))
        return copy.deepcopy(db['new'][value['business_name']])
    module = NS(check_binding=check_binding, verify_origin=verify_origin, checked_origin=checked_origin)
    monkeypatch.setitem(sys.modules, 'generic_agents.business_flow_execution_adoption', module)
    return NS(module=module, seen=seen)


def old_snapshot(kind):
    if kind == 'v2': return linear_scene.snapshot()
    if kind == 'v3':
        value = graph_scene.snapshot(mixed=False)
        # The shared old fixture is a valid pure plan, not a canonical compiler
        # result: its second SOURCE business is unadopted and carries only the
        # older single-step blocker. Real v3.compile_plan ALSO appends v3.BLOCKED
        # to that same source business. Keep both; the unrelated new facade has
        # no fullSemanticSource and must not inherit either source blocker.
        unadopted = value['businesses'][1]
        assert unadopted['issues'] == [v2.BLOCKED_ISSUE]
        assert 'fullSemanticSource' in unadopted['flow'] and runtime.BUSINESS_MARKER not in unadopted
        unadopted['issues'].append(v3.BLOCKED)
        plan = value['semantic_runtime_plan']
        value['semantic_runtime_plan'] = v3.plan_value(plan['designs'], value['businesses'],
            plan['graph_adoptions'], plan['legacy_plan'], value['roles'])
        return value
    if kind == 'v171': return object_scene.snapshot(mixed=True)
    assert kind == 'v4'
    values = object_scene.old_values(2)
    binding = event_binding.build_binding(**values, event_source_bindings={}, event_admission_policy=copy.deepcopy(POLICY))
    source = binding['source_design']; name = binding['business_name']
    item = {'origin': event_adoption.origin_value(uid(19250), binding, command_hash='5' * 64), 'binding': binding}
    businesses = [{'id': uid(19251), 'name': name, 'flow': v2.semantic.derive_flows(source)[name],
        'issues': [], 'available': True, 'execution_mode': 'confirmed_graph_workflow',
        runtime.BUSINESS_MARKER: item['origin']}]
    roles = {role: {key: actor[key] for key in ('user_id', 'username')} for role, actor in binding['role_bindings'].items()}
    plan = v4.plan_value([source], businesses, [item], None, roles)
    return {'businesses': businesses, 'roles': roles, 'semantic_runtime_plan': plan}


def scene(kind=None):
    binding = new_binding(); item = {'origin': origin(binding), 'binding': binding}
    new_business = {'id': uid(19300), 'name': binding['business_name'], 'flow': copy.deepcopy(binding['flow']),
        'issues': [], 'available': True, 'execution_mode': 'confirmed_graph_workflow'}
    roles = {role: {key: actor[key] for key in ('user_id', 'username')} for role, actor in binding['role_bindings'].items()}
    ordinary = {'id': uid(19301), 'name': '普通线性业务', 'flow': {'steps': []},
        'issues': [], 'available': True, 'execution_mode': 'confirmed_human_workflow'}
    base = None; old_graphs = []
    if kind is None:
        businesses = [ordinary, new_business]
    else:
        old = old_snapshot(kind); businesses = copy.deepcopy(old['businesses']) + [ordinary, new_business]
        roles = {**copy.deepcopy(old['roles']), **roles}; plan = old['semantic_runtime_plan']
        module = {'v2': v2, 'v3': v3, 'v4': v4, 'v171': v171}[kind]
        if kind == 'v2':
            plan = module.plan_value(plan['designs'], businesses, plan['adoptions'], roles)
        else:
            assert plan['legacy_plan'] is None
            plan = module.plan_value(plan['designs'], businesses, plan['graph_adoptions'], None, roles)
            old_graphs = copy.deepcopy(plan['graph_adoptions'])
        base = {'plan': plan, 'businesses': copy.deepcopy(businesses), 'roles': copy.deepcopy(roles)}
    new_business = businesses[-1]; new_business[runtime.BUSINESS_MARKER] = copy.deepcopy(item['origin'])
    plan = runtime.plan_value(businesses, [*old_graphs, item], base, roles)
    snapshot = {'businesses': businesses, 'roles': roles, 'semantic_runtime_plan': plan}
    return NS(binding=binding, item=item, base=base, snapshot=snapshot, db={'new': {binding['business_name']: binding}})


def reseal_snapshot(snapshot):
    plan = snapshot['semantic_runtime_plan']
    plan['businesses_hash'] = digest(snapshot['businesses']); plan['roles_hash'] = digest(snapshot['roles'])
    seal(plan)


@pytest.mark.parametrize('kind', [None, 'v2', 'v3', 'v4', 'v171'])
def test_real_older_pure_plan_is_preserved_with_new_marker_only(boundary, kind):
    s = scene(kind); before = copy.deepcopy(s.snapshot)
    assert runtime.verify_plan(s.snapshot) == s.snapshot['semantic_runtime_plan']
    assert s.snapshot == before
    assert len(s.snapshot['businesses'][-1]['flow']['steps']) == 17
    assert s.snapshot['businesses'][-1]['flow']['steps'][0]['required'] is False
    if kind is not None:
        stripped = copy.deepcopy(s.snapshot['businesses']); stripped[-1].pop(runtime.BUSINESS_MARKER)
        assert stripped == s.base['businesses']
    assert runtime.pins(s.snapshot)['full_semantic_runtime_hash'] == s.snapshot['semantic_runtime_plan']['plan_hash']
    released = NS(data={'snapshot': s.snapshot})
    assert runtime.business_binding(released, s.snapshot['businesses'][-1]) == s.binding


@pytest.mark.parametrize('change', ['flow', 'roles', 'base-business', 'base-roles', 'old-binding',
    'extra-plan-key', 'missing-origin', 'origin-family', 'origin-key', 'origin-hash', 'wrong-mode', 'typed-bool'])
def test_self_resealing_cannot_change_baseline_or_new_binding_contract(boundary, change):
    s = scene('v171'); snap = s.snapshot; plan = snap['semantic_runtime_plan']; business = snap['businesses'][-1]
    if change == 'flow': business['flow']['steps'][0]['purpose'] = 'changed'
    elif change == 'roles': snap['roles']['独立核对职责']['user_id'] = uid(19900)
    elif change == 'base-business': snap['businesses'][-2]['name'] = 'changed'
    elif change == 'base-roles': plan['base_plan']['roles']['独立核对职责']['username'] = 'changed'
    elif change == 'old-binding': plan['graph_adoptions'][0]['binding']['business_name'] = 'changed'
    elif change == 'extra-plan-key': plan['unapproved'] = True
    elif change == 'missing-origin': del business[runtime.BUSINESS_MARKER]
    elif change == 'origin-family': business[runtime.BUSINESS_MARKER]['schema'] = 'report-full-semantic-graph-origin-v4'
    elif change == 'origin-key':
        business[runtime.BUSINESS_MARKER]['inferred'] = True
        plan['graph_adoptions'][-1]['origin'] = copy.deepcopy(business[runtime.BUSINESS_MARKER])
    elif change == 'origin-hash':
        business[runtime.BUSINESS_MARKER]['binding_hash'] = 'e' * 64
        plan['graph_adoptions'][-1]['origin'] = copy.deepcopy(business[runtime.BUSINESS_MARKER])
    elif change == 'wrong-mode': business['execution_mode'] = 'confirmed_human_workflow'
    else: business['available'] = 1
    reseal_snapshot(snap)
    with pytest.raises((ValueError, KeyError, AssertionError)): runtime.verify_plan(snap)


@pytest.mark.parametrize('change', ['duplicate', 'unknown-base', 'old-without-base', 'missing-new', 'linear-marker'])
def test_family_and_inventory_cannot_fall_back(boundary, change):
    s = scene('v171'); snap = s.snapshot; plan = snap['semantic_runtime_plan']
    if change == 'duplicate': plan['graph_adoptions'].append(copy.deepcopy(plan['graph_adoptions'][-1]))
    elif change == 'unknown-base': plan['base_plan']['plan']['schema'] = 'unknown'
    elif change == 'old-without-base': plan['base_plan'] = None
    elif change == 'missing-new': plan['graph_adoptions'].pop()
    else: snap['businesses'][-1][v2.BUSINESS_MARKER] = {'unapproved': True}
    reseal_snapshot(snap)
    with pytest.raises((ValueError, KeyError, AssertionError)): runtime.verify_plan(snap)


def test_new_config_lookup_is_explicit_current_and_does_not_mutate(boundary):
    s = scene(); config = {runtime.CONFIG_KEY: {s.binding['business_name']: s.item['origin']}}
    before = copy.deepcopy(config)
    assert runtime.validated_bindings(s.db, object(), config) == [s.item]
    assert boundary.seen == [('live', True, s.item['origin'])] and config == before
    with pytest.raises(ValueError): runtime.validated_bindings(s.db, object(), {'fullSemanticGraphBindings': {}})


@pytest.mark.parametrize('kind', [None, 'v2', 'v3', 'v4', 'v171'])
def test_compile_calls_real_old_builder_and_keeps_exact_base(boundary, monkeypatch, kind):
    from generic_agents import report_full_semantic_graph_adoption as graphs
    from generic_agents import report_full_semantic_runtime_adoption as linear
    from generic_agents import report_full_semantic_handoff as handoff
    s = scene(kind); old = s.base
    businesses = copy.deepcopy(s.snapshot['businesses']); businesses[-1].pop(runtime.BUSINESS_MARKER)
    config = {runtime.CONFIG_KEY: {s.binding['business_name']: s.item['origin']}}
    config['flows'] = {business['name']: business['flow'] for business in businesses}
    if old is not None:
        plan = old['plan']; config['fullSemanticDesigns'] = copy.deepcopy(plan['designs'])
        monkeypatch.setattr(handoff, 'shared_records', lambda db, p: [{'id': d['handoff_id'], 'design': d} for d in plan['designs']])
        if kind == 'v2':
            config[v2.CONFIG_KEY] = {a['binding']['business_name']: a['origin'] for a in plan['adoptions']}
            monkeypatch.setattr(linear, 'validated_bindings', lambda db, p, value: copy.deepcopy(plan['adoptions']))
        else:
            config['fullSemanticGraphBindings'] = {a['binding']['business_name']: a['origin'] for a in plan['graph_adoptions']}
            def old_verify(db, p, value, require_current=True):
                return copy.deepcopy(next(a['binding'] for a in plan['graph_adoptions'] if a['origin'] == value))
            monkeypatch.setattr(graphs, 'verify_origin', old_verify)
    before = copy.deepcopy(config)
    actual = runtime.compile_plan(s.db, object(), config, businesses, copy.deepcopy(s.snapshot['roles']))
    assert actual == s.snapshot['semantic_runtime_plan'] and businesses == s.snapshot['businesses']
    assert config == before
    assert businesses[-1]['issues'] == [] and businesses[-1]['available'] is True
    if kind == 'v3':
        assert businesses[1]['issues'] == [v2.BLOCKED_ISSUE, v3.BLOCKED]
        assert businesses[1]['available'] is False and businesses[1]['execution_mode'] == 'blocked'
        assert runtime.BUSINESS_MARKER not in businesses[1]


def test_binding_selector_rejects_unfrozen_business_even_if_name_matches(boundary):
    s = scene(); business = copy.deepcopy(s.snapshot['businesses'][-1]); business['flow']['steps'].pop()
    with pytest.raises(ValueError): runtime.business_binding(NS(data={'snapshot': s.snapshot}), business)


def release_scene(s, monkeypatch):
    from generic_agents import models, services
    p = NS(workspace_id=uid(19400), user_id=uid(19401))
    snapshot = copy.deepcopy(s.snapshot); configuration_hash = digest(snapshot)
    spec = {'configuration_hash': configuration_hash, 'business_definitions': copy.deepcopy(snapshot['businesses']),
        **runtime.pins(snapshot)}
    data = {'specification': spec, 'dependencies': runtime.dependencies(snapshot)}
    package = NS(id=uid(19402), kind='domain_package', status='published', namespace='synthetic', data=data,
        content_hash=digest(data), validated_hash=digest(data))
    instance = NS(id=uid(19403), namespace='synthetic')
    release = NS(id=uid(19404), workspace_id=p.workspace_id, package_id=package.id, instance_id=instance.id,
        status='prepared', configuration_hash=configuration_hash,
        data={'snapshot': snapshot, **runtime.pins(snapshot)})
    def scoped(db, cls, identity, actor):
        assert actor is p
        if cls is models.Capability and identity == package.id: return package
        if cls is models.DomainInstance and identity == instance.id: return instance
        raise AssertionError('Unexpected DB-scope request')
    monkeypatch.setattr(services, 'scoped', scoped)
    return p, release, package


@pytest.mark.parametrize('live', [True, False])
def test_release_uses_real_new_adoption_entrypoint_for_live_or_history(boundary, monkeypatch, live):
    s = scene(); p, release, package = release_scene(s, monkeypatch)
    assert runtime.verify_release(s.db, p, release, live=live) == s.snapshot['semantic_runtime_plan']
    assert boundary.seen == [('live', False, s.item['origin'])] if live else boundary.seen == [('history', None, s.item['origin'])]
    assert package.data['specification']['business_definitions'] == s.snapshot['businesses']


@pytest.mark.parametrize('change', ['package-hash', 'release-hash', 'release-pin', 'spec-pin', 'workspace', 'status', 'mixed-marker'])
def test_release_rejects_tampering_before_source_is_allowed(boundary, monkeypatch, change):
    s = scene(); p, release, package = release_scene(s, monkeypatch)
    if change == 'package-hash': package.data['unapproved'] = True
    elif change == 'release-hash': release.configuration_hash = 'e' * 64
    elif change == 'release-pin': release.data['full_semantic_runtime_schema'] = v3.SCHEMA
    elif change == 'spec-pin':
        package.data['specification']['full_semantic_runtime_hash'] = 'e' * 64
        package.content_hash = package.validated_hash = digest(package.data)
    elif change == 'workspace': release.workspace_id = uid(19499)
    elif change == 'status': package.status = 'draft'
    else: release.data['semantic_plan'] = {}
    with pytest.raises(HTTPException) as caught: runtime.verify_release(s.db, p, release)
    assert caught.value.status_code == 409 and not boundary.seen


def test_historical_release_cannot_treat_missing_adoption_as_authority(boundary, monkeypatch):
    s = scene(); p, release, _ = release_scene(s, monkeypatch)
    def missing(*args, **kwargs): raise HTTPException(409, 'synthetic missing receipt')
    monkeypatch.setattr(boundary.module, 'checked_origin', missing)
    with pytest.raises(HTTPException): runtime.verify_release(s.db, p, release, live=False)


def test_ability_dependencies_come_from_frozen_graph_and_required_package_inventory(boundary, monkeypatch):
    s = scene(); item = s.snapshot['semantic_runtime_plan']['graph_adoptions'][-1]
    capability = uid(19500)
    item['binding']['graph']['nodes'].append({'id': 'ability', 'kind': 'ability', 'execution': {'capability_id': capability}})
    seal(item['binding'], 'binding_hash'); item['origin']['binding_hash'] = item['binding']['binding_hash']
    s.snapshot['businesses'][-1][runtime.BUSINESS_MARKER] = copy.deepcopy(item['origin'])
    reseal_snapshot(s.snapshot); s.db['new'][s.binding['business_name']] = item['binding']
    assert runtime.dependencies(s.snapshot) == [capability]
    p, release, package = release_scene(s, monkeypatch)
    package.data['dependencies'] = []; package.content_hash = package.validated_hash = digest(package.data)
    with pytest.raises(HTTPException): runtime.verify_release(s.db, p, release)


def test_unrecognized_requested_business_cannot_skip_all_current_gates(boundary, monkeypatch):
    s = scene(); p, release, _ = release_scene(s, monkeypatch)
    with pytest.raises(HTTPException): runtime.verify_release(s.db, p, release, business_name='不存在的业务')
    assert not boundary.seen
