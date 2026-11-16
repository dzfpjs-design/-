"""Real graph options projection with explicit synthetic IO boundary spies.

The actual options function, source/extractor merge, graph summary, legacy empty
shape and sealed hash run unchanged. Release/current-source/actor/description
and independent state inventory are deliberately supplied adapters. This is
neither a source-authority proof nor PG, browser, device or Task acceptance.
No generic_agents.api or PostgreSQL fixture is imported.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException

from generic_agents import signal_graph_routing_v134 as app
from generic_agents import instance_api as instances
from generic_agents import graph_task_inputs_v120 as task_inputs
from generic_agents import workflow_graph as workflows
from generic_agents.contracts import digest


def uid(label):
    return str(uuid5(NAMESPACE_URL, 'public-http-graph-options:' + label))


def reference(label):
    return {'id': uid(label), 'name': '公开合成来源 ' + label,
        'version': 3, 'content_hash': digest({'public': label})}


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure options projection cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def scene(monkeypatch):
    p = NS(user_id=uid('actor'), workspace_id=uid('workspace'), role='operator')
    business_id = 'business_' + 'a' * 16
    instance = NS(id=uid('instance'), workspace_id=p.workspace_id, version=7,
        data={'enabled_business_ids': [business_id]})
    business = {'id': business_id, 'name': '森林防火疑似异常复核（HTTP模拟验证）',
        'available': True, 'execution_mode': 'confirmed_graph_workflow',
        'flow': {'steps': [{'id': 'source_review', 'name': '核对异常依据'}]}}
    ordinary = {'id': 'business_' + 'b' * 16, 'name': '原人工业务',
        'available': True, 'execution_mode': 'confirmed_human_workflow'}
    release = NS(id=uid('release'), data={'snapshot': {
        'businesses': [business, ordinary], 'automation': {'policies': {
            business['name']: {'triggers': ['异常事件']}}}}})
    actor_id = uid('reviewer')
    graph = {'nodes': [
        {'id': 'runtime_source_review', 'kind': 'human', 'actor_id': actor_id,
            'source_step_id': 'source_review'},
        {'id': 'end', 'kind': 'end', 'source_step_id': None}]}
    binding = {'schema': 'business-flow-execution-binding-v1', 'binding_hash': 'c' * 64,
        'graph': graph, 'flow': {'source_flow': {'nodes': [
            {'node_id': 'source_review', 'name': '核对异常依据'}]}}}
    field = {'key': 'field_' + 'd' * 16, 'label': '气温', 'kind': '数值', 'unit': '°C'}
    entity = {'id': uid('entity'), 'name': '合成监测对象', 'fields': [field]}
    source = reference('existing-source')
    extractor = {**reference('existing-rule'), 'rules': [
        {'id': 'high', 'field': field['key'], 'operator': 'gt', 'threshold': '35.0'}]}
    original = {'sources': [source], 'extractors': [extractor], 'entities': [{'id': entity['id']}]}
    metadata = {'user_input_schema': {'type': 'object', 'properties': {
        'target_entity': {'type': 'string'}, 'note': {'type': ['null', 'string']},
        'threshold': {'type': 'number'}}}, 'entity_fields': []}
    c = NS(p=p, db=object(), instance=instance, release=release, business=business,
        binding=binding, graph=graph, entity=entity, field=field, metadata=metadata,
        original=original, http_options=[], calls=[], http_error=None,
        description_error=None, inventory_error=None)

    def active(db, principal, identity):
        assert db is c.db and principal is p and identity == instance.id
        c.calls.append(('active',))
        return instance, release

    def original_options(db, principal, current):
        assert db is c.db and principal is p and current is instance
        c.calls.append(('original_options',))
        return original

    def http_options(db, principal, current, selected):
        assert db is c.db and principal is p and current is instance and selected is release
        c.calls.append(('http_options',))
        if c.http_error is not None:
            raise c.http_error
        # A second lookup is a contract violation: it could observe another
        # generation, yielding IDs not represented by the returned source list.
        assert c.calls.count(('http_options',)) == 1
        return c.http_options

    def inventory(db, principal, current):
        assert db is c.db and principal is p and current is instance
        c.calls.append(('inventory',))
        if c.inventory_error is not None:
            raise c.inventory_error
        return {'schema': app.STATE, 'routes': [], 'requests': []}

    def entities(db, principal, current, selected, identities, *, lock=True):
        assert db is c.db and principal is p and current is instance and selected is release
        assert identities == [entity['id']] and lock is False
        c.calls.append(('entities', lock))
        return [copy.deepcopy(entity)]

    def business_binding(selected, current):
        assert selected is release and current is business
        c.calls.append(('binding',))
        return binding

    def actor(db, workspace_id, identity, *, lock=True):
        assert db is c.db and workspace_id == p.workspace_id and identity == actor_id and lock is False
        c.calls.append(('actor', lock))
        return NS(user_id=identity, role='operator'), '真实成员适配边界'

    def describe(db, principal, instance_id, release_id, selected_business_id):
        assert db is c.db and principal is p
        assert (instance_id, release_id, selected_business_id) == (instance.id, release.id, business_id)
        c.calls.append(('describe',))
        if c.description_error is not None:
            raise c.description_error
        return copy.deepcopy(metadata)

    monkeypatch.setattr(instances, 'active', active)
    monkeypatch.setattr(app.single, 'options', original_options)
    monkeypatch.setattr(app.http_source, 'options', http_options)
    monkeypatch.setattr(app, '_state_authority', inventory)
    monkeypatch.setattr(app, '_entities', entities)
    monkeypatch.setattr(workflows, 'business_binding', business_binding)
    monkeypatch.setattr(app, '_actor', actor)
    monkeypatch.setattr(task_inputs, 'describe', describe)
    return c


def read(c):
    return app.options(c.db, c.p, c.instance)


def assert_sealed(value):
    assert value['options_hash'] == digest({key: item for key, item in value.items()
        if key != 'options_hash'})


def test_empty_http_options_preserve_exact_existing_response_shape(scene):
    c = scene
    result = read(c)
    assert set(result) == {'schema', 'instance_id', 'instance_version', 'release_id',
        'ttl_seconds', 'max_tasks_limit', 'max_entities', 'sources', 'extractors',
        'entities', 'businesses', 'can_enable', 'blocking_reason', 'options_hash'}
    assert 'http_mapped_source_ids_v177' not in result
    assert result['schema'] == 'signal-graph-routing-options-v134'
    assert result['can_enable'] is True and result['blocking_reason'] is None
    assert result['sources'] == c.original['sources']
    assert result['extractors'] == c.original['extractors']
    assert len(result['businesses']) == 1  # The ordinary human-only filter stays unchanged.
    assert result['businesses'][0]['graph_summary']['nodes'][0]['name'] == '核对异常依据'
    assert c.calls.count(('http_options',)) == 1
    assert_sealed(result)


def test_nonempty_http_ids_are_sorted_unique_members_of_the_actual_source_list(scene):
    c = scene
    first, second = reference('http-first'), reference('http-second')
    first_rule, second_rule = reference('http-first-rule'), reference('http-second-rule')
    c.http_options = [
        {'source': second, 'extractor': second_rule},
        {'source': first, 'extractor': first_rule},
        {'source': second, 'extractor': first_rule}]
    before = copy.deepcopy((c.original, c.http_options, c.instance.data))
    result = read(c)
    assert result['http_mapped_source_ids_v177'] == sorted([first['id'], second['id']])
    source_ids = [row['id'] for row in result['sources']]
    extractor_ids = [row['id'] for row in result['extractors']]
    assert source_ids == sorted({c.original['sources'][0]['id'], first['id'], second['id']})
    assert extractor_ids == sorted({c.original['extractors'][0]['id'], first_rule['id'], second_rule['id']})
    assert set(result['http_mapped_source_ids_v177']) <= set(source_ids)
    assert c.calls.count(('http_options',)) == 1
    assert (c.original, c.http_options, c.instance.data) == before
    assert_sealed(result)
    without_classification = {key: item for key, item in result.items()
        if key not in {'options_hash', 'http_mapped_source_ids_v177'}}
    assert digest(without_classification) != result['options_hash']
    result['sources'][-1]['name'] = 'changed output only'
    result['http_mapped_source_ids_v177'].clear()
    assert (c.original, c.http_options, c.instance.data) == before


def test_http_source_already_in_legacy_options_is_classified_without_duplicate_or_replacement(scene):
    c = scene
    existing_source = c.original['sources'][0]
    existing_extractor = c.original['extractors'][0]
    c.http_options = [{'source': {**existing_source, 'name': '不可替换原已显示来源'},
        'extractor': {**existing_extractor, 'name': '不可替换原已显示规则'}},
        {'source': copy.deepcopy(existing_source), 'extractor': copy.deepcopy(existing_extractor)}]
    result = read(c)
    assert result['sources'] == [existing_source] and result['extractors'] == [existing_extractor]
    assert result['http_mapped_source_ids_v177'] == [existing_source['id']]
    assert c.calls.count(('http_options',)) == 1
    assert_sealed(result)


@pytest.mark.parametrize('boundary', ['http_error', 'description_error', 'inventory_error'])
def test_system_failure_propagates_instead_of_returning_a_partial_or_empty_choice_list(scene, boundary):
    c = scene
    error = RuntimeError('synthetic read failure at ' + boundary)
    setattr(c, boundary, error)
    with pytest.raises(RuntimeError) as caught:
        read(c)
    assert caught.value is error
    assert c.calls.count(('http_options',)) == 1


def test_http_lookup_http_failure_is_not_relabelled_as_no_http_sources(scene):
    c = scene
    error = HTTPException(503, '当前来源库存不可用')
    c.http_error = error
    with pytest.raises(HTTPException) as caught:
        read(c)
    assert caught.value is error
    assert c.calls.count(('http_options',)) == 1
