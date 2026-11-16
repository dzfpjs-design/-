"""Pure mixed-version integrity checks; no DB/source/automation authority claim.

The dispatch spy below checks only version routing and frozen envelopes. Root's
separate real PG journey exercises the actual adoption and execution ledgers.
"""
import copy
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest

from generic_agents.contracts import digest
from generic_agents import instance_semantic_runtime_dispatch as dispatch
from generic_agents import report_full_semantic_graph_runtime_v3 as graph_runtime
from generic_agents import report_full_semantic_runtime_v2 as v2
from generic_agents.report_full_semantic_graph_adoption import origin_value
from tests.test_report_full_semantic_graph_binding_v3 import fixture, build
from tests.test_report_full_semantic_schema_v1_pure import seal, uid


def snapshot(*, mixed=True):
    def source(result):
        result['global_claims'] = []; result['questions'] = []
        # This mixed synthetic source has no unresolved global interpreter work.
        # Resolve its invented unknown evidence explicitly as workspace inference
        # before the real source validator runs; never weaken that validator.
        def inferred(value):
            if isinstance(value, list):
                for item in value: inferred(item)
            elif isinstance(value, dict):
                if value.get('status') == 'unknown' and 'explanation' in value:
                    value.update(status='inferred', explanation='合成测试明确采用的工作区设计；不认定原文事实')
                for item in value.values(): inferred(item)
        inferred(result)
        for coordination in result['businesses'][0]['coordination']:
            if coordination['kind'] == 'unspecified':
                coordination.update(kind='parallel', start_rule='明确同时处理两项合成职责',
                    wait_rule='等待两项各自处理完成', join_rule='两项全部完成后汇合')
        business = result['businesses'][1]
        business['steps'] = business['steps'][:1]
        business['steps'][0].update(name='独立旧人工步骤', agent='人工确认', role='独立旧职责',
            next_index=None, else_index=None, branch_condition='')
        for key in ('participants', 'interactions', 'conditions', 'coordination'):
            business[key] = []

    values = fixture(extra_business=True, change=source)
    graph_binding = build(values); design = values['design']
    old_name = design['result']['businesses'][1]['name']
    old_actor = {'user_id': uid(9480), 'username': '原单步负责人🙂', 'membership_role': 'operator'}
    old_binding = v2.build_runtime_binding(design, values['manifest'], values['proof'], old_name,
        old_actor, proof_id=values['proof_id'])
    old_origin = v2.build_origin(uid(9481), old_binding)
    graph_origin = origin_value(uid(9482), graph_binding, command_hash='8' * 64)
    old_roles = {'独立旧职责': {'user_id': old_actor['user_id'], 'username': old_actor['username']}}
    businesses = [{'id': uid(9490 + index), 'name': business['name'],
        'flow': v2.semantic.derive_flows(design)[business['name']],
        'issues': [] if index == 1 and mixed else [v2.BLOCKED_ISSUE],
        'available': index == 1 and mixed,
        'execution_mode': 'confirmed_human_workflow' if index == 1 and mixed else 'blocked',
        **({v2.BUSINESS_MARKER: copy.deepcopy(old_origin)} if index == 1 and mixed else {})}
        for index, business in enumerate(design['result']['businesses'])]
    legacy = None
    if mixed:
        old_businesses = copy.deepcopy(businesses)
        legacy = {'plan': v2.plan_value([design], old_businesses,
            [{'origin': old_origin, 'binding': old_binding}], old_roles),
            'businesses': old_businesses, 'roles': old_roles}
    businesses[0].update(issues=[], available=True, execution_mode='confirmed_graph_workflow',
        **{graph_runtime.BUSINESS_MARKER: graph_origin})
    roles = {name: {key: actor[key] for key in ('user_id', 'username')}
        for name, actor in graph_binding['role_bindings'].items()}
    if mixed:
        roles.update(copy.deepcopy(old_roles))
    plan = graph_runtime.plan_value([design], businesses,
        [{'origin': graph_origin, 'binding': graph_binding}], legacy, roles)
    return {'businesses': businesses, 'roles': roles, 'semantic_runtime_plan': plan}


def reseal_projection(value):
    """Model an attacker resealing only self-reported outer projection hashes."""
    plan = value['semantic_runtime_plan']
    plan['businesses_hash'] = digest(value['businesses']); plan['roles_hash'] = digest(value['roles'])
    seal(plan, 'plan_hash')


def test_normal_mixed_v2_v3_plan_keeps_both_exact_role_contracts_and_source_flows():
    value = snapshot(); frozen = copy.deepcopy(value)
    plan = graph_runtime.verify_plan(value)
    assert plan == value['semantic_runtime_plan']
    assert graph_runtime.pins(value) == {'full_semantic_runtime_schema': graph_runtime.SCHEMA,
        'full_semantic_runtime_hash': plan['plan_hash']}
    assert value['businesses'][0]['execution_mode'] == 'confirmed_graph_workflow'
    assert value['businesses'][1]['execution_mode'] == 'confirmed_human_workflow'
    assert v2.verify_plan({'businesses': plan['legacy_plan']['businesses'],
        'roles': plan['legacy_plan']['roles'], 'semantic_runtime_plan': plan['legacy_plan']['plan']})
    assert value == frozen


@pytest.mark.parametrize('change', ['user', 'username', 'remove', 'extra'])
def test_nested_v2_role_cannot_be_rebound_only_in_the_actual_v3_snapshot(change):
    value = snapshot(); role = '独立旧职责'
    if change == 'user': value['roles'][role]['user_id'] = uid(9499)
    elif change == 'username': value['roles'][role]['username'] = '另一真实成员'
    elif change == 'remove': value['roles'].pop(role)
    else: value['roles'][role]['unapproved'] = True
    # The nested v2 plan and its original binding stay internally valid.
    legacy = value['semantic_runtime_plan']['legacy_plan']
    assert v2.verify_plan({'businesses': legacy['businesses'], 'roles': legacy['roles'],
        'semantic_runtime_plan': legacy['plan']}) == legacy['plan']
    reseal_projection(value)
    with pytest.raises(ValueError): graph_runtime.verify_plan(value)


def test_v2_marker_cannot_authorize_an_unadopted_sibling_without_a_nested_v2_ledger_contract():
    value = snapshot(mixed=False); business = value['businesses'][1]
    business.update(issues=[], available=True, execution_mode='confirmed_human_workflow',
        **{v2.BUSINESS_MARKER: {'adoption_id': uid(9499)}})
    reseal_projection(value)
    with pytest.raises(ValueError): graph_runtime.verify_plan(value)


@pytest.mark.parametrize('change', ['missing-marker', 'wrong-origin', 'extra-business', 'missing-business', 'duplicate-name', 'duplicate-id'])
def test_nested_business_inventory_and_origins_are_exact_not_just_parallel_self_hashes(change):
    value = snapshot()
    if change == 'missing-marker': value['businesses'][1].pop(v2.BUSINESS_MARKER)
    elif change == 'wrong-origin': value['businesses'][1][v2.BUSINESS_MARKER]['adoption_id'] = uid(9499)
    elif change == 'extra-business':
        extra = copy.deepcopy(value['businesses'][1]); extra.update(id=uid(9499), name='未采用的额外业务')
        value['businesses'].append(extra)
    elif change == 'missing-business': value['businesses'].pop()
    elif change == 'duplicate-name': value['businesses'][1]['name'] = value['businesses'][0]['name']
    else: value['businesses'][1]['id'] = value['businesses'][0]['id']
    reseal_projection(value)
    with pytest.raises(ValueError): graph_runtime.verify_plan(value)


def bundle(value):
    value = copy.deepcopy(value); pins = graph_runtime.pins(value)
    release = NS(id=uid(9460), workspace_id=uid(9461), instance_id=uid(9462), package_id=uid(9463),
        configuration_hash=digest(value), status='prepared',
        data={'snapshot': value, 'configuration_revision': 9, **pins})
    package = NS(id=release.package_id, data={'specification': {'configuration_hash': release.configuration_hash,
        'business_definitions': copy.deepcopy(value['businesses']), **pins}})
    config = {'fullSemanticGraphBindings': {value['businesses'][0]['name']:
        value['semantic_runtime_plan']['graph_adoptions'][0]['origin']},
        'fullSemanticRuntimeBindings': {value['businesses'][1]['name']:
            value['semantic_runtime_plan']['legacy_plan']['plan']['adoptions'][0]['origin']}}
    return release, package, NS(id=uid(9464), version=9, data=config)


def reseal_bundle(release, package):
    release.configuration_hash = digest(release.data['snapshot'])
    spec = package.data['specification']; spec['configuration_hash'] = release.configuration_hash
    spec['business_definitions'] = copy.deepcopy(release.data['snapshot']['businesses'])


@pytest.mark.parametrize('downcast', ['all-v2', 'all-unmarked', 'snapshot-v2', 'release-v2',
    'package-v2', 'remove-plan', 'legacy-marker'])
def test_actual_issued_graph_configuration_cannot_be_downcast_by_resealing_all_runtime_markers(monkeypatch, downcast):
    value = snapshot(); release, package, draft = bundle(value)
    spec = package.data['specification']; current = release.data['snapshot']
    old = copy.deepcopy(current['semantic_runtime_plan']['legacy_plan'])
    v2_pins = {'full_semantic_runtime_schema': v2.SCHEMA,
        'full_semantic_runtime_hash': old['plan']['plan_hash']}
    if downcast == 'all-v2':
        current.update(businesses=old['businesses'], roles=old['roles'], semantic_runtime_plan=old['plan'])
        release.data.update(v2_pins); spec.update(v2_pins)
    elif downcast == 'all-unmarked':
        current.pop('semantic_runtime_plan')
        for business in current['businesses']:
            business.pop(v2.BUSINESS_MARKER, None); business.pop(graph_runtime.BUSINESS_MARKER, None)
        for target in (release.data, spec):
            target.pop('full_semantic_runtime_schema'); target.pop('full_semantic_runtime_hash')
    elif downcast == 'snapshot-v2': current['semantic_runtime_plan'] = old['plan']
    elif downcast == 'release-v2': release.data.update(v2_pins)
    elif downcast == 'package-v2': spec.update(v2_pins)
    elif downcast == 'remove-plan': current.pop('semantic_runtime_plan')
    else: current['semantic_plan'] = {'schema': 'instance-full-semantic-plan-v1'}
    reseal_bundle(release, package)
    class DB:
        def scalar(self, query): return draft
    monkeypatch.setattr(dispatch, 'scoped', lambda *args: package)
    called_v2 = []
    def old_verifier(*args):
        called_v2.append(True)
        return copy.deepcopy(old['plan'])
    def graph_verifier(db, p, frozen):
        try:
            plan = graph_runtime.verify_plan(frozen.data['snapshot'])
            for key, expected in graph_runtime.pins(frozen.data['snapshot']).items():
                if frozen.data.get(key) != expected or spec.get(key) != expected: raise ValueError('pins')
            return plan
        except (ValueError, KeyError, TypeError):
            raise HTTPException(409, '冻结图合同不一致') from None
    monkeypatch.setattr(v2, 'verify_release', old_verifier)
    monkeypatch.setattr(graph_runtime, 'verify_release', graph_verifier)
    with pytest.raises(HTTPException) as error:
        dispatch.verify_release(DB(), NS(workspace_id=release.workspace_id), release)
    assert error.value.status_code == 409
    assert not called_v2
