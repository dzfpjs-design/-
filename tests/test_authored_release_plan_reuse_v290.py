"""One pure V288 rebuild per release check, with explicit DB/origin IO seams.

Actual V288 reconstruction, plan/pins/dependency derivation and drift checks
run. Synthetic package/config/origin rows do not prove PG custody or live IAM.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import authored_workflow_runtime_v208 as subject
from generic_agents import authored_goal_data_workflow_store_v288 as recipe_store
from generic_agents import models as m
from generic_agents.contracts import digest
from tests.test_authored_goal_data_workflow_v288 import command, cold_binding, ident, reseal


@pytest.fixture
def scene(monkeypatch):
    body = command()
    # A target outside the graph and a perception extractor must remain in the
    # release dependencies; pure reuse cannot drop either specialized family.
    body['candidate_specs'][0]['capability_pins'][0]['capability_id'] = ident(700)
    binding = cold_binding(body)
    binding['observation_extractors'] = [{'capability_id': ident(701),
        'capability_hash': 'c' * 64, 'actor_id': ident(90)}]
    binding['observation_extractor_pins'] = {ident(701): {'capability_id': ident(701),
        'capability_hash': 'c' * 64, 'agent_role': 'perception', 'input_bindings': {},
        'arguments': {}, 'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'},
        'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 1}}
    binding = reseal(binding)
    name = binding['business_name']
    origin = {'schema': subject.store.ORIGIN, 'adoption_id': ident(801), 'business_name': name,
        'binding_hash': binding['binding_hash'], 'command_hash': 'd' * 64}
    business = {'id': 'business', 'name': name, 'available': True, 'issues': [],
        'execution_mode': 'confirmed_graph_workflow', 'flow': copy.deepcopy(binding['flow']),
        subject.BUSINESS_MARKER: copy.deepcopy(origin)}
    roles = {key: {field: value[field] for field in ('user_id', 'username')}
        for key, value in binding['role_bindings'].items()}
    plan = subject.plan_value([business], [{'origin': origin, 'binding': binding}], roles)
    snapshot = {'businesses': [business], 'roles': roles, 'semantic_runtime_plan': plan}
    frozen_pins = {'full_semantic_runtime_schema': subject.SCHEMA,
        'full_semantic_runtime_hash': plan['plan_hash']}
    expected_dependencies = list(dict.fromkeys([node['execution']['capability_id']
        for node in binding['graph']['nodes'] if node['kind'] == 'ability'] + [ident(701), ident(700)]))
    p = NS(workspace_id=binding['workspace_id'], user_id=ident(90), role='operator')
    release = NS(id=ident(802), workspace_id=p.workspace_id, instance_id=ident(803), package_id=ident(804),
        status='prepared', configuration_hash=digest(snapshot),
        data={'snapshot': snapshot, 'configuration_revision': 1, **frozen_pins})
    package = NS(id=release.package_id, kind='domain_package', namespace='synthetic', status='published',
        data={'specification': {'configuration_hash': release.configuration_hash,
            'business_definitions': copy.deepcopy(snapshot['businesses']), **frozen_pins},
            'dependencies': expected_dependencies})
    package.content_hash = package.validated_hash = digest(package.data)
    instance = NS(id=release.instance_id, namespace=package.namespace)
    current = NS(id=binding['configuration_id'], version=1, data={
        subject.CONFIG_KEY: {name: copy.deepcopy(origin)}, 'flows': {name: copy.deepcopy(binding['flow'])}})
    reads, builds = [], []

    class ReadOnlyIO:
        def scalar(self, statement): pytest.fail('current revision needs no historical SQL')
        def add(self, row): pytest.fail('release proof cannot add rows')
        def commit(self): pytest.fail('release proof cannot commit')

    db = ReadOnlyIO()

    def scoped(actual_db, model, identity, actor):
        assert actual_db is db and actor is p
        reads.append(('scoped', model, identity))
        row = package if model is m.Capability else instance
        assert model in {m.Capability, m.DomainInstance} and row.id == identity
        return row

    def configuration(actual_db, actor):
        assert actual_db is db and actor is p
        reads.append(('configuration',)); return current

    def origin_read(actual_db, actor, issued, **kwargs):
        assert actual_db is db and actor is p and issued == origin
        reads.append(('origin', kwargs)); return copy.deepcopy(binding)

    real_build = recipe_store._build

    def counted_build(*args, **kwargs):
        builds.append(True); return real_build(*args, **kwargs)

    monkeypatch.setattr(subject, 'scoped', scoped)
    monkeypatch.setattr(subject.store, 'configuration', configuration)
    monkeypatch.setattr(subject.store, 'checked_origin', origin_read)
    monkeypatch.setattr(subject.store, 'verify_origin', origin_read)
    monkeypatch.setattr(recipe_store, '_build', counted_build)
    return NS(db=db, p=p, release=release, package=package, instance=instance,
        binding=binding, snapshot=snapshot, current=current, pins=frozen_pins,
        dependencies=expected_dependencies, plan=plan, reads=reads, builds=builds)


@pytest.mark.parametrize('live', [False, True])
def test_release_builds_real_graph_once_and_keeps_all_current_io_reads(scene, live):
    s = scene; before = copy.deepcopy((s.release.data, s.package.data, s.current.data))
    result = subject.verify_release(s.db, s.p, s.release, live=live,
        business_name=s.binding['business_name'])
    assert result == s.plan and s.builds == [True]
    assert s.reads == [('scoped', m.Capability, s.package.id),
        ('scoped', m.DomainInstance, s.instance.id), ('configuration',),
        ('origin', {'require_current': False} if live else {})]
    result['graph_adoptions'].clear()
    assert (s.release.data, s.package.data, s.current.data) == before
    # Each next call still validates again; no cross-call or request cache.
    assert subject.verify_release(s.db, s.p, s.release, live=live) == s.plan
    assert s.builds == [True, True]


def test_public_pins_and_dependencies_each_validate_and_keep_extra_targets(scene):
    s = scene
    assert subject.pins(s.snapshot) == s.pins and s.builds == [True]
    assert subject.dependencies(s.snapshot) == s.dependencies and s.builds == [True, True]
    assert ident(700) in s.dependencies and ident(701) in s.dependencies
    assert len(s.dependencies) == len(set(s.dependencies))
    assert subject.dependencies({}) == [] and s.builds == [True, True]


@pytest.mark.parametrize('reader', ['release', 'pins', 'dependencies'])
def test_nested_graph_drift_is_rejected_without_trusting_previous_success(scene, reader):
    s = scene
    subject.verify_release(s.db, s.p, s.release, live=False)
    actual = s.snapshot['semantic_runtime_plan']['graph_adoptions'][0]['binding']
    actual['graph']['nodes'][0]['id'] = 'changed-after-first-read'
    with pytest.raises(HTTPException):
        if reader == 'release': subject.verify_release(s.db, s.p, s.release, live=False)
        elif reader == 'pins': subject.pins(s.snapshot)
        else: subject.dependencies(s.snapshot)


@pytest.mark.parametrize('fault', ['release_pin', 'package_pin', 'extractor_dependency',
    'target_dependency', 'publication', 'origin', 'configuration'])
def test_release_drift_still_fails_after_one_successful_pure_validation(scene, monkeypatch, fault):
    s = scene; subject.verify_release(s.db, s.p, s.release)
    if fault == 'release_pin': s.release.data['full_semantic_runtime_hash'] = '0' * 64
    elif fault == 'package_pin': s.package.data['specification']['full_semantic_runtime_hash'] = '0' * 64
    elif fault == 'extractor_dependency': s.package.data['dependencies'].remove(ident(701))
    elif fault == 'target_dependency': s.package.data['dependencies'].remove(ident(700))
    elif fault == 'publication': s.package.status = 'draft'
    elif fault == 'configuration': s.current.data[subject.CONFIG_KEY].clear()
    elif fault == 'origin':
        def revoked(*args, **kwargs): raise HTTPException(403, 'actual original authority rejected')
        monkeypatch.setattr(subject.store, 'verify_origin', revoked)
    # Re-seal the synthetic package to ensure pins/dependency failures exercise
    # their own guards instead of stopping at an unrelated outer content hash.
    s.package.content_hash = s.package.validated_hash = digest(s.package.data)
    with pytest.raises(HTTPException): subject.verify_release(s.db, s.p, s.release)
    assert s.builds == [True, True]
