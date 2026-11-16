"""Pure recomputation dedup with actual typed source/binding/plan validators.

Only DB/adoption/source/member inventories use explicit IO spies.  These tests
prove repeated computation was removed, not PostgreSQL authority or source
quality.  Current checks still execute anew on every operation.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_execution_runtime as runtime
from generic_agents import business_flow_execution_adoption as adoption
from generic_agents import business_flow_execution_source as source
from generic_agents import report_full_semantic_graph_adoption as shared
from generic_agents import workflow_graph, workflow_graph_goal_tools_v126 as tools
from generic_agents import services, models
from generic_agents.contracts import digest
from tests.test_workflow_graph_goal_business_flow_sources import source_binding
from tests.test_graph_event_observation_sources import contract_fixture


def uid(label):
    return str(uuid5(NAMESPACE_URL, 'public-pure-plan-dedup:' + label))


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('pure runtime dedup test cannot perform external IO')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(subprocess, 'Popen', deny)


def scene(monkeypatch, *, with_ability=True):
    binding, cap = source_binding(with_ability)
    origin = adoption.origin(uid('adoption'), binding, 'b' * 64)
    business = {'id': 'business_' + 'a' * 16, 'name': binding['business_name'],
        'flow': copy.deepcopy(binding['flow']), 'available': True, 'issues': [],
        'execution_mode': 'confirmed_graph_workflow', runtime.BUSINESS_MARKER: copy.deepcopy(origin)}
    roles = {name: {key: row[key] for key in ('user_id', 'username')}
        for name, row in binding['role_bindings'].items()}
    plan = runtime.plan_value([business], [{'origin': origin, 'binding': binding}], None, roles)
    snapshot = {'businesses': [business], 'roles': roles, 'semantic_runtime_plan': plan}
    configuration_hash = digest(snapshot); pins = runtime.pins(snapshot)
    data = {'specification': {'configuration_hash': configuration_hash,
        'business_definitions': copy.deepcopy(snapshot['businesses']), **pins},
        'dependencies': runtime.dependencies(snapshot)}
    package = NS(id=uid('package'), status='published', kind='domain_package', namespace='public-dedup',
        data=data, content_hash=digest(data), validated_hash=digest(data))
    instance = NS(id=uid('instance'), namespace=package.namespace)
    p = NS(workspace_id=uid('workspace'), user_id=uid('operator'))
    release = NS(id=uid('release'), workspace_id=p.workspace_id, package_id=package.id, instance_id=instance.id,
        status='prepared', configuration_hash=configuration_hash, data={'snapshot': snapshot, **pins})
    seen = []; flags = {'source': True, 'roles': True, 'origin': True}
    db = object()

    def scoped(database, model, identity, principal):
        assert database is db and principal is p
        seen.append(('scoped', model, identity))
        if model is models.Capability and identity == package.id:
            return package
        assert model is models.DomainInstance and identity == instance.id
        return instance

    def checked_origin(database, principal, issued):
        assert database is db and principal is p
        seen.append(('origin', copy.deepcopy(issued)))
        if not flags['origin'] or digest(issued) != digest(origin):
            raise HTTPException(409, 'actual immutable adoption inventory unavailable')
        return copy.deepcopy(binding)

    def current_materials(database, principal, actual_source, *, lock=False):
        assert database is db and principal is p and lock is False
        assert digest(actual_source) == digest(binding['source'])
        seen.append(('source',))
        if not flags['source']:
            raise HTTPException(409, 'current source/material inventory changed')
        return copy.deepcopy(binding['source_materials'])

    def members(database, principal, requested):
        assert database is db and principal is p
        assert requested == {name: row['user_id'] for name, row in binding['role_bindings'].items()}
        seen.append(('members',))
        result = copy.deepcopy(binding['role_bindings'])
        if not flags['roles']:
            next(iter(result.values()))['user_id'] = uid('revoked-role')
        return result

    def abilities(database, principal, graph, external_ids):
        assert database is db and principal is p
        assert digest(graph) == digest(binding['graph']) and external_ids == binding['external_node_ids']
        seen.append(('abilities',))
        return copy.deepcopy(binding['capability_pins'])

    def automation(configuration, name, graph, actions):
        assert configuration == {'current_configuration_boundary': True}
        assert name == binding['business_name'] and actions == binding['node_actions']
        assert digest(graph) == digest(binding['graph'])
        seen.append(('automation',))

    monkeypatch.setattr(services, 'scoped', scoped)
    monkeypatch.setattr(adoption, 'checked_origin', checked_origin)
    monkeypatch.setattr(adoption, 'current_configuration',
        lambda database, principal: NS(data={'current_configuration_boundary': True}))
    monkeypatch.setattr(source, 'current_materials', current_materials)
    monkeypatch.setattr(shared, 'members', members)
    monkeypatch.setattr(shared, 'ability_capabilities', abilities)
    monkeypatch.setattr(shared, 'automation', automation)
    return NS(db=db, p=p, release=release, package=package, binding=binding, business=business,
        snapshot=snapshot, plan=plan, cap=cap, seen=seen, flags=flags)


def count_real_verify_plan(monkeypatch):
    actual = runtime.verify_plan; calls = []

    def counted(snapshot):
        calls.append(snapshot)
        return actual(snapshot)

    monkeypatch.setattr(runtime, 'verify_plan', counted)
    return calls


@pytest.mark.parametrize('live', [True, False])
@pytest.mark.parametrize('with_ability', [True, False])
def test_one_release_operation_validates_real_typed_plan_once(monkeypatch, live, with_ability):
    c = scene(monkeypatch, with_ability=with_ability)
    calls = count_real_verify_plan(monkeypatch)
    before = copy.deepcopy((c.release.data, c.package.data))
    assert runtime.verify_release(c.db, c.p, c.release, live=live) == c.plan
    assert calls == [c.snapshot]
    assert sum(item[0] == 'origin' for item in c.seen) == 1
    if live:
        assert all((kind,) in c.seen for kind in ('source', 'members', 'abilities', 'automation'))
    else:
        assert not any(item[0] in {'source', 'members', 'abilities', 'automation'} for item in c.seen)
    assert (c.release.data, c.package.data) == before


def test_public_pins_and_dependencies_each_keep_full_real_plan_validation(monkeypatch):
    c = scene(monkeypatch)
    calls = count_real_verify_plan(monkeypatch)
    pins = runtime.pins(c.snapshot)
    assert calls == [c.snapshot]
    assert pins == {'full_semantic_runtime_schema': runtime.SCHEMA, 'full_semantic_runtime_hash': c.plan['plan_hash']}
    assert runtime.dependencies(c.snapshot) == [c.cap.id]
    assert calls == [c.snapshot, c.snapshot]
    pins['full_semantic_runtime_hash'] = 'f' * 64
    assert runtime.pins(c.snapshot)['full_semantic_runtime_hash'] == c.plan['plan_hash']
    assert len(calls) == 3


@pytest.mark.parametrize('value', [None, {}, {'semantic_runtime_plan': {'schema': 'unknown-runtime'}}])
def test_unrecognized_dependencies_keep_original_empty_result_without_relabel(monkeypatch, value):
    def forbidden(_):
        raise AssertionError('unknown family cannot borrow this runtime validator')
    monkeypatch.setattr(runtime, 'verify_plan', forbidden)
    assert runtime.dependencies(value) == []


@pytest.mark.parametrize('entrypoint', ['pins', 'dependencies'])
def test_public_derivers_do_not_accept_typed_or_resealed_plan_tampering(monkeypatch, entrypoint):
    c = scene(monkeypatch)
    c.snapshot['businesses'][0]['available'] = 1  # JSON number is not the frozen true.
    with pytest.raises((ValueError, HTTPException)):
        getattr(runtime, entrypoint)(c.snapshot)


@pytest.mark.parametrize('fault', ['plan', 'release_pin', 'spec_pin', 'dependencies', 'origin'])
def test_release_rejects_plan_pins_dependency_and_original_adoption_changes(monkeypatch, fault):
    c = scene(monkeypatch)
    assert runtime.verify_release(c.db, c.p, c.release) == c.plan
    if fault == 'plan': c.snapshot['semantic_runtime_plan']['plan_hash'] = 'f' * 64
    elif fault == 'release_pin': c.release.data['full_semantic_runtime_hash'] = 'f' * 64
    elif fault == 'spec_pin':
        c.package.data['specification']['full_semantic_runtime_hash'] = 'f' * 64
        c.package.content_hash = c.package.validated_hash = digest(c.package.data)
    elif fault == 'dependencies':
        c.package.data['dependencies'] = []
        c.package.content_hash = c.package.validated_hash = digest(c.package.data)
    else: c.flags['origin'] = False
    with pytest.raises(HTTPException):
        runtime.verify_release(c.db, c.p, c.release)


@pytest.mark.parametrize('changed', ['source', 'roles'])
def test_current_source_and_roles_are_checked_again_between_operations(monkeypatch, changed):
    c = scene(monkeypatch)
    assert runtime.verify_release(c.db, c.p, c.release) == c.plan
    original_count = sum(item[0] == ('source' if changed == 'source' else 'members') for item in c.seen)
    c.flags[changed] = False
    with pytest.raises(HTTPException):
        runtime.verify_release(c.db, c.p, c.release)
    assert sum(item[0] == ('source' if changed == 'source' else 'members') for item in c.seen) == original_count + 1


def test_dependency_derivation_keeps_actual_closed_event_contract_validation():
    contract = contract_fixture()
    plan = {'graph_adoptions': [{'binding': {'graph': {'nodes': []},
        'event_source_bindings': {'original': {'contract': contract}}}}]}
    assert runtime._dependencies_from_plan(plan) == [contract['source_id'], contract['extractor_id']]
    broken = copy.deepcopy(plan)
    broken['graph_adoptions'][0]['binding']['event_source_bindings']['original']['contract']['source_hash'] = 'f' * 64
    with pytest.raises(ValueError):
        runtime._dependencies_from_plan(broken)


def test_goal_catalog_context_selects_the_real_binding_graph_only_once(monkeypatch):
    c = scene(monkeypatch)
    calls = []; actual = workflow_graph.business_binding

    def counted(release, business):
        calls.append((release, business))
        return actual(release, business)

    def forbidden(*args):
        raise AssertionError('graph_for would repeat the identical binding selection')

    monkeypatch.setattr(workflow_graph, 'business_binding', counted)
    monkeypatch.setattr(workflow_graph, 'graph_for', forbidden)
    binding, graph, dependencies = tools._context(c.db, c.p, c.release, c.business)
    assert calls == [(c.release, c.business)]
    assert binding == c.binding and graph == c.binding['graph'] and dependencies == {c.cap.id}
    assert graph is binding['graph']
