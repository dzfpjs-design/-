"""Actual routing _prepare source dispatch with explicit synthetic IO spies.

No generic_agents.api or PostgreSQL fixture is imported. _prepare, its family
selection, material union/order, exact snapshot projection and summary are real.
Active release, binding authority, actors, capabilities, entities, source reads
and snapshot verification are deliberate IO boundaries. This proves dispatch
only, never a real source, IAM, database transaction, model, Task or field result.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException

from generic_agents import signal_graph_routing_v134 as routing
from generic_agents import instance_api as instances
from generic_agents import workflow_graph as workflows
from generic_agents import instance_compiler as compiler
from generic_agents import perception_signals as perception
from generic_agents import graph_task_inputs_v120 as task_inputs
from generic_agents import business_flow_execution_source as saved_source
from generic_agents import report_configuration_handoff as handoffs
from generic_agents import models as m
from generic_agents.contracts import digest


def uid(label): return str(uuid5(NAMESPACE_URL, 'public-routing-business-flow-source:' + label))


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure source dispatch cannot perform external IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def scene(monkeypatch):
    p = NS(user_id=uid('actor'), workspace_id=uid('workspace'), role='operator')
    actor_id = uid('node-actor'); instance = NS(id=uid('instance'), workspace_id=p.workspace_id,
        data={'enabled_business_ids': ['business_' + 'a' * 16]})
    ids = {key: uid('material-' + key) for key in ('word', 'copy', 'flow', 'object', 'relation', 'proof', 'unrelated')}
    def material(key):
        identity = ids[key]
        return {'id': identity, 'version': 7, 'hash': digest(['public', key]), 'evidence_id': uid('evidence-' + key),
            'status': 'needs_input' if key in {'word', 'copy'} else 'parsed', 'archived': False,
            'blocks_hash': digest(['saved-blocks', key]), 'fixture_annotation': '原冻结快照，非来源授权'}
    materials = [material(key) for key in ('relation', 'copy', 'unrelated', 'object', 'word', 'flow')]
    flow = {'steps': [{'id': 'original_node', 'name': '旧展示名称', 'sourceId': ids['flow']}]}
    business = {'id': instance.data['enabled_business_ids'][0], 'name': '用户实际业务', 'available': True,
        'execution_mode': 'confirmed_graph_workflow', 'flow': flow}
    graph = {'nodes': [{'id': 'n_' + 'a' * 24, 'kind': 'human', 'actor_id': actor_id,
        'source_step_id': 'original_node'}, {'id': 'end', 'kind': 'end', 'source_step_id': None}]}
    binding = {'schema': 'business-flow-execution-binding-v1', 'binding_hash': 'b' * 64,
        'graph': graph, 'source': {'public_frozen_source_boundary': True},
        'flow': {'source_flow': {'nodes': [{'node_id': 'original_node', 'name': '原流程完整步骤名称'}]}}}
    snapshot = {'businesses': [business], 'materials': materials,
        'automation': {'policies': {business['name']: {'triggers': ['异常事件']}}},
        'object_material_ids': [ids['object']], 'relation_material_ids': [ids['relation']]}
    release = NS(id=uid('release'), configuration_hash='c' * 64, data={'snapshot': snapshot})
    body = {'release_id': release.id, 'business_id': business['id'], 'source_id': uid('source'),
        'extractor_id': uid('extractor'), 'entity_ids': [uid('entity')]}
    caps = {}
    for identity in (body['source_id'], body['extractor_id']):
        caps[identity] = NS(id=identity, name='public_local', version=3, content_hash=digest(identity),
            data={'description': '公开合成只读来源', 'allowed_agent_roles': ['perception'],
                'execution': {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
                'data_policy': {'cloud_allowed': False},
                'specification': {'rules': [{'id': 'temperature', 'field': 'temperature_c', 'operator': 'gt', 'threshold': 38.0}]}})
    refs = [{key: row[key] for key in ('id', 'version', 'hash', 'evidence_id')}
        for row in materials if row['id'] in {ids['word'], ids['copy']}]
    c = NS(p=p, db=object(), instance=instance, release=release, business=business, binding=binding,
        body=body, ids=ids, refs=refs, calls=[], material_calls=[], verified=[], described=[],
        source_error=None, snapshot_error=None)
    def active(db, actor, identity):
        assert db is c.db and actor is p and identity == instance.id
        return instance, release
    def business_binding(actual_release, actual_business):
        assert actual_release is release and actual_business is business
        return binding
    def actor(db, workspace_id, identity, *, lock=True):
        assert db is c.db and workspace_id == p.workspace_id and identity == actor_id
        c.calls.append(('actor', identity, lock)); return NS(role='operator'), '实际成员边界'
    def fresh(db, model, identity, principal, *, lock=False):
        assert db is c.db and principal is p
        c.calls.append(('fresh', model, identity, lock))
        if model is m.SetupMaterial:
            c.material_calls.append((identity, lock)); return NS(id=identity)
        assert model is m.Capability and identity in caps
        return caps[identity]
    def published(db, principal, identity, kind=None):
        assert db is c.db and principal is p and identity in caps
        if kind is not None: assert kind == 'source' and identity == body['source_id']
        return caps[identity]
    def entities(db, principal, actual_instance, actual_release, entity_ids, *, lock=True):
        assert db is c.db and principal is p and actual_instance is instance and actual_release is release
        assert entity_ids == body['entity_ids']; c.calls.append(('entities', lock))
        return [{'id': body['entity_ids'][0], 'name': '合成对象', 'version': 1}]
    def scoped(db, model, identity, principal):
        assert db is c.db and principal is p and model is m.Entity and identity == body['entity_ids'][0]
        return NS(id=identity)
    def rule(*args): c.calls.append(('rule_contract',))
    def current_materials(db, principal, source, *, lock=False):
        assert db is c.db and principal is p and source is binding['source'] and lock is False
        c.calls.append(('current_materials', lock))
        if c.source_error is not None: raise c.source_error
        return copy.deepcopy(c.refs)
    def verify(db, principal, selected):
        assert db is c.db and principal is p
        c.verified.append(copy.deepcopy(selected)); c.calls.append(('verify_materials',))
        if c.snapshot_error is not None: raise c.snapshot_error
    def describe(db, principal, instance_id, release_id, business_id):
        assert db is c.db and principal is p and (instance_id, release_id, business_id) == (instance.id, release.id, business['id'])
        c.described.append(True); return {'schema': 'synthetic-input-metadata-only'}
    monkeypatch.setattr(instances, 'active', active)
    monkeypatch.setattr(workflows, 'business_binding', business_binding)
    monkeypatch.setattr(routing, '_actor', actor)
    monkeypatch.setattr(routing, 'fresh', fresh)
    monkeypatch.setattr(routing, 'published', published)
    monkeypatch.setattr(routing, '_entities', entities)
    monkeypatch.setattr(routing, 'scoped', scoped)
    monkeypatch.setattr(perception, 'rule_contract', rule)
    monkeypatch.setattr(saved_source, 'current_materials', current_materials)
    monkeypatch.setattr(compiler, 'verify_material_snapshots', verify)
    monkeypatch.setattr(task_inputs, 'describe', describe)
    return c


def prepare(c, *, lock=True):
    return routing._prepare(c.db, c.p, c.instance, c.body, lock_selection=lock)


def expected_materials(c, *, legacy=False):
    expected = {c.ids[key] for key in ('word', 'copy', 'flow', 'object', 'relation')}
    return [row for row in c.release.data['snapshot']['materials'] if row['id'] in expected]


@pytest.mark.parametrize('lock', [True, False])
def test_new_family_uses_fresh_source_and_preserves_exact_saved_material_snapshots(scene, lock):
    c = scene; before = copy.deepcopy(c.release.data); output = prepare(c, lock=lock)
    assert output['materials'] == expected_materials(c) == c.verified[0]
    assert output['materials_hash'] == digest(output['materials']) and c.release.data == before
    assert c.material_calls == [(identity, lock) for identity in sorted(row['id'] for row in expected_materials(c))]
    assert [call for call in c.calls if call[0] == 'current_materials'] == [('current_materials', False)]
    assert next(i for i, call in enumerate(c.calls) if call[0] == 'current_materials') < next(
        i for i, call in enumerate(c.calls) if call[0] == 'fresh' and call[1] is m.SetupMaterial)
    assert next(i for i, call in enumerate(c.calls) if call[0] == 'verify_materials') > max(
        i for i, call in enumerate(c.calls) if call[0] == 'fresh' and call[1] is m.SetupMaterial)
    assert any(row['status'] == 'needs_input' and row['blocks_hash'] for row in output['materials'])
    assert c.described == [True]
    output['materials'][0]['hash'] = 'changed-return-copy'
    assert c.release.data == before


def test_new_family_does_not_consume_a_legacy_proof_even_when_one_is_present(scene):
    c = scene
    c.binding['source_proof'] = {'selection': {'anchor': {'source_ref': {'id': c.ids['unrelated']},
        'copy_material_id': c.ids['unrelated']}}}
    output = prepare(c)
    assert c.ids['unrelated'] not in {row['id'] for row in output['materials']}
    assert any(call[0] == 'current_materials' for call in c.calls)


def test_actual_source_failure_propagates_before_material_locks_or_new_authority(scene):
    c = scene; error = HTTPException(409, 'synthetic full-source generation changed'); c.source_error = error
    with pytest.raises(HTTPException) as caught: prepare(c)
    assert caught.value is error and not c.material_calls and not c.verified and not c.described


def test_snapshot_verification_failure_is_not_converted_to_success(scene):
    c = scene; error = HTTPException(409, 'synthetic raw evidence mismatch'); c.snapshot_error = error
    with pytest.raises(HTTPException) as caught: prepare(c)
    assert caught.value is error and len(c.verified) == 1 and c.material_calls and not c.described


def test_missing_frozen_material_cannot_be_replaced_by_the_current_source_reference(scene):
    c = scene
    c.release.data['snapshot']['materials'][:] = [row for row in c.release.data['snapshot']['materials'] if row['id'] != c.ids['copy']]
    with pytest.raises(HTTPException) as caught: prepare(c)
    assert caught.value.status_code == 409
    assert any(call[0] == 'current_materials' for call in c.calls)
    assert not c.material_calls and not c.verified


def test_report_source_extra_materials_join_the_same_sorted_lock_inventory(scene, monkeypatch):
    c = scene; first = c.release.data['snapshot']['materials'][0]
    first['report_sources'] = [{'source_review_id': uid('source-review')}]
    def owned_record(db, principal, identity, *, kind):
        assert db is c.db and principal is c.p and identity == uid('source-review') and kind == 'source_review'
        return NS(data={'record': {'materials': [{'id': c.ids['proof']}, {'id': c.ids['word']}]}})
    monkeypatch.setattr(handoffs, 'owned_record', owned_record)
    output = prepare(c)
    expected = {row['id'] for row in expected_materials(c)} | {c.ids['proof']}
    assert c.material_calls == [(identity, True) for identity in sorted(expected)]
    assert c.ids['proof'] not in {row['id'] for row in output['materials']}
    assert output['materials'] == expected_materials(c)


@pytest.mark.parametrize('schema', ['report-full-semantic-graph-binding-v3',
    'report-full-semantic-graph-binding-v4', 'report-object-graph-binding-v171'])
def test_each_known_legacy_family_keeps_its_actual_proof_and_never_calls_new_source(scene, schema):
    c = scene; c.binding['schema'] = schema
    c.binding['source_proof'] = {'selection': {'anchor': {'source_ref': {'id': c.ids['word']},
        'copy_material_id': c.ids['copy']}}}
    c.source_error = AssertionError('Legacy must not inspect new source')
    output = prepare(c)
    assert output['materials'] == expected_materials(c)
    assert not any(call[0] == 'current_materials' for call in c.calls)
    assert output['graph_summary']['nodes'][0]['name'] == '旧展示名称'


@pytest.mark.parametrize('schema', [None, 'unknown-binding-v999', 'business-flow-execution-binding-v2', []])
def test_unknown_family_cannot_downgrade_through_a_plausible_legacy_proof(scene, schema):
    c = scene; c.binding['schema'] = schema
    c.binding['source_proof'] = {'selection': {'anchor': {'source_ref': {'id': c.ids['word']},
        'copy_material_id': c.ids['copy']}}}
    with pytest.raises(HTTPException) as caught: prepare(c)
    assert caught.value.status_code == 409 and not c.material_calls and not c.verified
    assert not any(call[0] == 'current_materials' for call in c.calls)


def test_new_graph_summary_uses_original_source_names_instead_of_runtime_hash_ids(scene):
    c = scene; summary = prepare(c)['graph_summary']
    assert summary['nodes'][0]['name'] == '原流程完整步骤名称'
    assert summary['nodes'][0]['name'] not in {c.binding['graph']['nodes'][0]['id'], '旧展示名称'}
    assert summary['nodes'][1]['name'] == '本次流程结束'


def test_new_summary_rejects_missing_original_node_mapping_instead_of_guessing(scene):
    c = scene; c.binding['flow']['source_flow']['nodes'][0]['node_id'] = 'different_original'
    with pytest.raises(HTTPException) as caught: prepare(c)
    assert caught.value.status_code == 409
