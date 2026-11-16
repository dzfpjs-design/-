"""Synthetic pure release envelopes, not issued source or execution authority.

All binding, manifest, graph, identity and wait validators are real. These
fixtures contain no database success, provider results or private documents.
Root executes separately; actual HTTP/PG journeys own ledger acceptance claims.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents.contracts import digest
from generic_agents import report_full_semantic_configuration_v1 as semantic
from generic_agents import report_full_semantic_source_manifest_v1 as manifests
from generic_agents import report_full_semantic_graph_binding_v3 as old_binding
from generic_agents import report_full_semantic_graph_binding_v4 as event_binding
from generic_agents import report_full_semantic_graph_adoption as old_adoption
from generic_agents import report_full_semantic_graph_adoption_v4 as event_adoption
from generic_agents import report_full_semantic_graph_revision as revisions
from generic_agents import report_object_graph_adoption_v171 as adoption
from generic_agents import report_object_graph_runtime_v171 as runtime
from generic_agents import report_full_semantic_graph_runtime_v3 as v3
from generic_agents import report_full_semantic_graph_runtime_v4 as v4
from generic_agents import instance_semantic_runtime_dispatch as dispatch
from generic_agents import graph_task_inputs_v120 as task_inputs
from generic_agents import workflow_graph_waits as waits
from generic_agents import graph_wait_kernel_v118 as wait_kernel
from generic_agents.graph_wait_sources_v118 import POLICY
from tests import test_report_object_graph_binding_v171 as object_scene
from tests import test_report_full_semantic_graph_binding_v3 as graph_scene
from tests import test_report_full_semantic_source_manifest_v1_pure as source_scene
from tests.test_report_full_semantic_schema_v1_pure import uid


def reseal(value, key):
    value[key] = digest({name: item for name, item in value.items() if name != key})
    return value


def old_values(index):
    """Build a separate genuine legacy fixture, never retag a new binding."""
    def rename(result):
        for business in result['businesses']: business['name'] += '旧' + str(index)
        for obj in result['objects']: obj['name'] += '旧' + str(index)
        for relation in result['relationships']:
            relation['name'] += '旧' + str(index)
            relation['source_type'] += '旧' + str(index)
            relation['target_type'] += '旧' + str(index)
    values = graph_scene.fixture(change=rename)
    old_design = values['design']; context = {key: old_design[key] for key in semantic.CONTEXT_KEYS}
    context['handoff_id'] = uid(17100 + index)
    design = semantic.build_design(old_design['result'], **context)
    text = '甲对象关联乙对象。长度（米）。🙂 原文。'
    _, catalog, snapshot, projection_hash = source_scene.scene(
        source_scene.source(word=text, pages=[[text]]), change=rename)
    manifest = manifests.build_source_manifest(design, catalog, snapshot,
        privacy_projection_hash=projection_hash)
    proof = reseal({**copy.deepcopy(values['proof']), 'handoff_id': design['handoff_id'],
        'manifest_hash': manifest['manifest_hash']}, 'proof_hash')
    previous = old_design['business_bindings'][0]['step_ids']; actual = design['business_bindings'][0]['step_ids']
    for node in values['graph']['nodes']:
        if node['source_step_id'] is not None: node['source_step_id'] = actual[previous.index(node['source_step_id'])]
    values.update(design=design, manifest=manifest, proof=proof)
    return values


def snapshot(*, mixed=False, wait=False):
    values, sources = object_scene.wait_values() if wait else (object_scene.fixture(), {})
    new = object_scene.build(values, sources=sources)
    bindings = [(new, adoption.origin_value(uid(17200), new, command_hash='8' * 64))]
    if mixed:
        for index, version in [(1, 3), (2, 4)]:
            old = old_values(index)
            binding = (old_binding.build_binding(**old) if version == 3 else event_binding.build_binding(**old,
                event_source_bindings={}, event_admission_policy=copy.deepcopy(POLICY)))
            origin = (old_adoption.origin_value if version == 3 else event_adoption.origin_value)(
                uid(17200 + index), binding, command_hash=str(index) * 64)
            bindings.append((binding, origin))
    designs = []; businesses = []; adoptions = []; roles = {}
    for index, (binding, origin) in enumerate(bindings):
        design = binding['source_design']; name = binding['business_name']; designs.append(copy.deepcopy(design))
        businesses.append({'id': uid(17300 + index), 'name': name, 'flow': semantic.derive_flows(design)[name],
            'issues': [], 'available': True, 'execution_mode': 'confirmed_graph_workflow',
            runtime.BUSINESS_MARKER: copy.deepcopy(origin)})
        adoptions.append({'origin': copy.deepcopy(origin), 'binding': copy.deepcopy(binding)})
        roles.update({name: {key: actor[key] for key in ('user_id', 'username')}
            for name, actor in binding['role_bindings'].items()})
    plan = runtime.plan_value(designs, businesses, adoptions, None, roles)
    return {'businesses': businesses, 'roles': roles, 'semantic_runtime_plan': plan}


def reseal_projection(value):
    plan = value['semantic_runtime_plan']
    plan['businesses_hash'] = digest(value['businesses']); plan['roles_hash'] = digest(value['roles'])
    reseal(plan, 'plan_hash')


@pytest.mark.parametrize('mixed', [False, True])
def test_new_plan_cold_rebuilds_all_identity_owners_and_preserves_actual_legacy_families(mixed):
    value = snapshot(mixed=mixed); before = copy.deepcopy(value)
    assert runtime.verify_plan(value) == value['semantic_runtime_plan']
    assert dispatch.modern(value) is runtime
    assert runtime.pins(value) == {'full_semantic_runtime_schema': runtime.SCHEMA,
        'full_semantic_runtime_hash': value['semantic_runtime_plan']['plan_hash']}
    kinds = [a['binding']['schema'] for a in value['semantic_runtime_plan']['graph_adoptions']]
    assert kinds == [runtime.NEW_BINDING] + ([old_binding.SCHEMA, event_binding.SCHEMA] if mixed else [])
    binding = value['semantic_runtime_plan']['graph_adoptions'][0]['binding']
    assert binding['source_design']['result']['global_claims'] and binding['source_design']['result']['questions']
    assert all(not field['required'] for obj in binding['source_design']['result']['objects'] for field in obj['fields'])
    assert binding['source_design']['result']['objects'][1]['fields'][0]['unit'] == '米'
    assert any(row['owner_path'].startswith('/object_identity_confirmations/') for row in binding['owner_bindings'])
    assert runtime.business_binding(NS(data={'snapshot': value}), value['businesses'][0]) == binding
    assert value == before


@pytest.mark.parametrize('change', ['flow', 'role-user', 'role-name', 'origin', 'binding-marker',
    'source-identity', 'source-unit', 'source-required', 'drop-owner', 'missing-business', 'duplicate-design'])
def test_resealed_projection_cannot_remove_or_reinterpret_frozen_source_and_execution_contracts(change):
    value = snapshot(); plan = value['semantic_runtime_plan']; binding = plan['graph_adoptions'][0]['binding']
    if change == 'flow': value['businesses'][0]['flow']['steps'][0]['purpose'] = 'changed'
    elif change == 'role-user': next(iter(value['roles'].values()))['user_id'] = uid(17999)
    elif change == 'role-name': next(iter(value['roles'].values()))['username'] = 'changed'
    elif change == 'origin': plan['graph_adoptions'][0]['origin']['schema'] = 'report-full-semantic-graph-origin-v4'
    elif change == 'binding-marker': binding['schema'] = event_binding.SCHEMA; reseal(binding, 'binding_hash')
    elif change.startswith('source-'):
        obj = binding['source_design']['result']['objects'][1]
        if change == 'source-identity': obj['business_identity']['explanation'] = 'changed'
        elif change == 'source-unit': obj['fields'][0]['unit'] = '株'
        else: obj['fields'][0]['required'] = True
        reseal(binding['source_design'], 'design_hash'); reseal(binding, 'binding_hash')
    elif change == 'drop-owner': binding['owner_bindings'].pop(); reseal(binding, 'binding_hash')
    elif change == 'missing-business': value['businesses'].clear()
    else: plan['designs'].append(copy.deepcopy(plan['designs'][0]))
    reseal_projection(value)
    with pytest.raises((ValueError, KeyError)): runtime.verify_plan(value)


@pytest.mark.parametrize('legacy', [v3, v4])
def test_new_binding_cannot_be_resealed_under_legacy_runtime_plan(legacy):
    value = snapshot(); value['semantic_runtime_plan']['schema'] = legacy.SCHEMA
    reseal_projection(value)
    with pytest.raises(ValueError): legacy.verify_plan(value)


def test_new_plan_requires_an_actual_new_binding_and_selector_only_accepts_known_origin_versions():
    value = snapshot(mixed=True); plan = value['semantic_runtime_plan']
    with pytest.raises(ValueError): runtime.plan_value(plan['designs'][1:], value['businesses'][1:],
        plan['graph_adoptions'][1:], None, value['roles'])
    config = {'fullSemanticGraphBindings': {item['binding']['business_name']: item['origin']
        for item in plan['graph_adoptions']}}
    assert v4.for_configuration(config) is runtime
    config['fullSemanticGraphBindings']['unknown'] = {'schema': 'report-object-graph-origin-v999'}
    with pytest.raises(HTTPException): v4.for_configuration(config)
    with pytest.raises(HTTPException): dispatch.modern({'semantic_runtime_plan': {'schema': 'unknown'}})
    with pytest.raises(HTTPException): dispatch.modern({'semantic_runtime_plan': {'schema': runtime.SCHEMA},
        'full_semantic_runtime_schema': v4.SCHEMA})


def command():
    values = object_scene.fixture()
    return {'schema': 'report-object-graph-command-v171', 'configuration_revision': 7,
        'configuration_hash': 'a' * 64, 'proof_id': values['proof_id'], 'business_name': values['business_name'],
        'graph': copy.deepcopy(values['graph']), 'owner_bindings': copy.deepcopy(values['owner_bindings']),
        'role_bindings': {role: actor['user_id'] for role, actor in values['role_bindings'].items()},
        'external_node_ids': [], 'node_actions': {}, 'event_source_bindings': {},
        'event_admission_policy': copy.deepcopy(POLICY), 'request_id': uid(17400),
        'preview_hash': 'b' * 64, 'consent': True, 'semantic_mode': 'workspace_workflow_design'}


def test_typed_commands_preview_and_revision_ids_are_explicit_without_legacy_family_conversion():
    raw = command(); body = adoption.Adopt.model_validate(raw)
    assert adoption.preview_body(body).schema == 'report-object-graph-preview-command-v171'
    for legacy in (old_adoption.Adopt, event_adoption.Adopt):
        with pytest.raises(ValidationError): legacy.model_validate(raw)
    bad = {**raw, 'schema': 'report-full-semantic-graph-command-v4'}
    with pytest.raises(ValidationError): adoption.Adopt.model_validate(bad)
    for consent in (False, 1, 'true'):
        with pytest.raises(ValidationError): adoption.Adopt.model_validate({**raw, 'consent': consent})
    revised = adoption.RevisionAdopt.model_validate({**raw,
        'schema': 'report-object-graph-revision-command-v171', 'parent': {'id': uid(17401), 'receipt_hash': 'c' * 64}})
    owner = NS(workspace_id=uid(17402), user_id=uid(17403))
    assert adoption.revision_preview_body(revised).schema == 'report-object-graph-revision-preview-command-v171'
    assert revisions.revision_id(owner, uid(17404), revised.request_id, family=adoption) != revisions.revision_id(
        owner, uid(17404), revised.request_id, family=event_adoption)
    assert revisions.command_hash(owner, uid(17404), revised, family=adoption) != revisions.command_hash(
        owner, uid(17404), revised, family=event_adoption)


def test_new_typed_wait_retains_source_dependencies_and_real_wait_kernel_authority():
    value = snapshot(wait=True); plan = value['semantic_runtime_plan']; binding = plan['graph_adoptions'][0]['binding']
    assert waits.enabled(binding)
    contract = next(iter(binding['event_source_bindings'].values()))['contract']
    assert contract['source_id'] in runtime.dependencies(value)
    entity_id = uid(17500); objects = [{'id': entity_id, 'type_name': '合成对象', 'version': 1, 'attributes': {}}]
    scope_hash = digest(objects)
    state, authority = wait_kernel.new_run(binding['graph'], {'case': entity_id}, {'sequence': 0, 'at': 100},
        binding_hash=binding['binding_hash'], entity_ids=[entity_id], scope_hash=scope_hash)
    task = NS(data={'entity_ids': [entity_id], 'object_snapshots': objects, 'graph_runtime': state,
        waits.AUTHORITY: authority, 'graph_wait_sources': waits.source_index(binding, scope_hash)})
    assert waits.checked(task, binding) == authority
    release = NS(data={'snapshot': value})
    task_inputs.validate_associations(release, value['businesses'][0], {'case': entity_id}, [entity_id])
    with pytest.raises(HTTPException) as foreign:
        task_inputs.validate_associations(release, value['businesses'][0], {'case': uid(17501)}, [entity_id])
    assert foreign.value.status_code == 422
    task.data['graph_wait_sources']['producer_ids'] = []
    with pytest.raises(HTTPException): waits.checked(task, binding)
