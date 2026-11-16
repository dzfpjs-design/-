"""Pure boundaries plus actual v117 source/admission IO; no fabricated DB intent.

Task-release authority has a separately named unit boundary below. The receipt
integration test substitutes that boundary explicitly; it does not certify a
real v118 release/adoption journey, which belongs to the workflow integration.
"""
import copy
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import graph_wait_sources_v118 as waits
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventProducer, GraphEventStream
from generic_agents.models import Capability, Entity, InstanceRelease, Membership, Task, User
from generic_agents.security import Principal, password_hash
from generic_agents.worker import process_once
from tests.test_full_semantic_graph_v1 import graph as no_wait_graph, wait_graph
from tests.test_graph_event_observation_sources import contract_fixture, identity
from tests.test_graph_event_pipeline import BASE, delivery_job, observe, ok, pending_intents, scene
from tests.test_vertical_slice import entity


def wire(contract=None, producer_id=None):
    contract = contract_fixture() if contract is None else copy.deepcopy(contract)
    graph = wait_graph(); subscription = graph['subscriptions'][0]
    subscription['event_type'] = contract['event_kind']
    subscription['output_schema'] = copy.deepcopy(contract['output_schema'])
    bindings = {subscription['id']: {'producer_id': producer_id or identity(20),
        'contract_hash': digest(contract), 'contract': contract,
        'scope': {'entity_policy': 'frozen_task_entities', 'correlation_ref': subscription['correlation_ref'],
            'source_policy': 'exact_registered_source'}}}
    return graph, bindings


def producer_wire(value):
    producer = value['producers'][0]
    return wire(producer['contract'], producer['id'])


def test_policy_and_bindings_are_exact_and_detached_without_flattening():
    policy = waits.check_policy(waits.POLICY)
    policy['sequence']['open'] = 'inclusive'
    assert waits.POLICY['sequence']['open'] == 'exclusive'
    graph, bindings = wire(); before = copy.deepcopy((graph, bindings))
    checked = waits.check_bindings(graph, bindings)
    checked['response']['contract']['output_schema']['properties']['result'] = {'type': 'string'}
    assert (graph, bindings) == before
    assert waits.check_bindings(no_wait_graph(), {}) == {}
    assert bindings['response']['contract_hash'] == digest(bindings['response']['contract'])
    assert bindings['response']['contract_hash'] != bindings['response']['contract']['contract_hash']


@pytest.mark.parametrize('change', [
    lambda value: value.update(extra=True),
    lambda value: value.update(arrival_basis='source_observed_at'),
    lambda value: value['sequence'].update(open='inclusive'),
    lambda value: value['deadline'].update(basis='postgres_commit_timestamp'),
    lambda value: value.update(clock_regression='clamp'),
    lambda value: value.update(physical_state_verified=0),
    lambda value: value['revocation'].update(history='block'),
])
def test_policy_rejects_changes_including_boolean_numeric_aliases(change):
    value = copy.deepcopy(waits.POLICY); change(value)
    with pytest.raises(ValueError): waits.check_policy(value)


@pytest.mark.parametrize('change', [
    lambda values: values.clear(),
    lambda values: values.update(extra=copy.deepcopy(values['response'])),
    lambda values: values['response'].update(extra=True),
    lambda values: values['response'].update(producer_id='not-a-producer'),
    lambda values: values['response'].update(contract_hash=values['response']['contract']['contract_hash']),
    lambda values: values['response']['scope'].update(extra=True),
    lambda values: values['response']['scope'].update(entity_policy='all_workspace_entities'),
    lambda values: values['response']['scope'].update(correlation_ref='another_reference'),
    lambda values: values['response']['scope'].update(source_policy='latest_registered_source'),
])
def test_binding_inventory_scope_and_outer_contract_pin_are_closed(change):
    graph, bindings = wire(); change(bindings)
    with pytest.raises(ValueError): waits.check_bindings(graph, bindings)


@pytest.mark.parametrize('change', [
    lambda graph: graph['subscriptions'][0].update(event_type='same_name_is_not_a_producer'),
    lambda graph: graph['subscriptions'][0].update(output_schema={'type': 'object'}),
    lambda graph: graph['subscriptions'][0].update(output_schema=copy.deepcopy(
        graph['subscriptions'][0]['output_schema']['$defs']['extractor_result'])),
])
def test_subscription_cannot_change_kind_or_flatten_registered_output(change):
    graph, bindings = wire(); change(graph)
    with pytest.raises(ValueError): waits.check_bindings(graph, bindings)


def actor(db, workspace_id):
    user = User(username='wait_consumer_' + uuid4().hex, password_hash=password_hash(uuid4().hex))
    db.add(user); db.flush()
    db.add(Membership(user_id=user.id, workspace_id=workspace_id, role='operator'))
    db.commit()
    return Principal(user.id, workspace_id, 'operator', 'synthetic-task-consumer')


def test_verify_uses_actual_current_actor_and_original_registered_pins(client):
    value = scene(client); graph, bindings = producer_wire(value)
    with Session(engine()) as db:
        consumer = actor(db, value['principal'].workspace_id)
        producer = db.get(GraphEventProducer, bindings['response']['producer_id'])
        assert producer.registered_by != consumer.user_id
        assert waits.verify_bindings(db, consumer, graph, bindings, lock=True) == bindings
        db.commit()
        membership = db.scalar(select(Membership).where(Membership.user_id == consumer.user_id,
            Membership.workspace_id == consumer.workspace_id))
        membership.role = 'viewer'; db.commit()
        with pytest.raises(HTTPException) as caught:
            waits.verify_bindings(db, consumer, graph, bindings)
        assert caught.value.status_code == 403


@pytest.mark.parametrize('change', ['source_retired', 'extractor_retired', 'producer_revoked'])
def test_current_revocation_blocks_live_binding_without_rewriting_original_registration(client, change):
    value = scene(client); graph, bindings = producer_wire(value)
    with Session(engine()) as db:
        if change == 'producer_revoked':
            row = db.get(GraphEventProducer, bindings['response']['producer_id']); row.status = 'revoked'
        else:
            key = 'source_id' if change == 'source_retired' else 'extractor_id'
            row = db.get(Capability, bindings['response']['contract'][key]); row.status = 'retired'
        db.commit()
        with pytest.raises(HTTPException) as caught:
            waits.verify_bindings(db, value['principal'], graph, bindings)
        assert caught.value.status_code == 409
        assert db.get(GraphEventProducer, bindings['response']['producer_id']).data['contract'] == bindings['response']['contract']


def test_cut_uses_a_different_read_committed_connection_and_persists_after_outer_rollback(client, monkeypatch):
    value = scene(client); seen = {}; original = waits.admission._stream
    def capture(reader, principal):
        seen['pid'] = reader.scalar(select(func.pg_backend_pid()))
        seen['isolation'] = reader.connection().get_isolation_level()
        return original(reader, principal)
    monkeypatch.setattr(waits.admission, '_stream', capture)
    with Session(engine()) as db:
        outer = db.scalar(select(func.pg_backend_pid()))
        # Use an actual Connection-bound Session to catch accidental nested use.
        with Session(bind=db.connection()) as connection_bound:
            cut = waits.take_cut(connection_bound, value['principal'])
        assert seen['pid'] != outer and seen['isolation'] == 'READ COMMITTED'
        assert cut['sequence'] == 0 and type(cut['at']) is float
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(GraphEventStream.last_sequence)) == 0


def test_cut_blocks_future_admission_clock_instead_of_clamping_it(client):
    value = scene(client); observation, _, _ = observe(client, value)
    intent = pending_intents(observation['id'])[0][0]; job, _ = delivery_job(intent)
    assert process_once(job)
    with Session(engine()) as db:
        row = db.scalar(select(GraphEventAdmission)); row.admitted_at = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
        with pytest.raises(HTTPException) as caught: waits.take_cut(db, value['principal'])
        assert caught.value.status_code == 409


class PrefixDB:
    def __init__(self, rows): self.rows = rows
    def execute(self, query):
        assert query._for_update_arg is None
        return iter(self.rows)


def test_prefix_proves_unbound_rows_and_preserves_global_sequence_order():
    at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    cut = {'sequence': 3, 'at': at.timestamp() + 3}
    rows = [(1, at, identity(20)), (2, at + timedelta(seconds=1), identity(21)),
        (3, at + timedelta(seconds=2), identity(20))]
    assert waits._prefix(PrefixDB(rows), SimpleNamespace(workspace_id=identity(3)), cut, 1, {identity(20)}) == [3]
    with pytest.raises(ValueError):
        waits._prefix(PrefixDB(rows), SimpleNamespace(workspace_id=identity(3)), cut, 1,
            {identity(20)}, floor_at=at.timestamp() + 2)


@pytest.mark.parametrize('rows,sequence', [
    ([(1, 0), (3, 1)], 3), ([(1, 0)], 2), ([(1, 0), (1, 1)], 2),
    ([(1, 1), (2, 0)], 2), ([(1, 0), (2, 4)], 2), ([(0, 0)], 0),
])
def test_missing_or_backward_unrelated_prefix_blocks_before_filtering(rows, sequence):
    at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    rows = [(seq, at + timedelta(seconds=seconds), identity(21)) for seq, seconds in rows]
    with pytest.raises(ValueError):
        waits._prefix(PrefixDB(rows), SimpleNamespace(workspace_id=identity(3)),
            {'sequence': sequence, 'at': at.timestamp() + 3}, 0, {identity(20)})


def task_scope_fixture(monkeypatch):
    graph, bindings = wire(); workspace = identity(3); owner = identity(8)
    snapshots = [{'id': identity(5), 'version': 1, 'attributes': {'name': '合成对象'}}]
    task = SimpleNamespace(id=identity(30), workspace_id=workspace, owner_id=owner, package_id=identity(31),
        data={'instance_release_id': identity(32), 'instance_id': identity(33), 'business_id': 'business',
            'entity_ids': [identity(5)], 'object_snapshots': snapshots,
            'graph_wait_authority': {'scope_hash': digest(snapshots), 'cursor': {'sequence': 0, 'at': 0}}})
    release = SimpleNamespace(id=identity(32), workspace_id=workspace, instance_id=identity(33),
        package_id=identity(31), data={'snapshot': {'businesses': [{'id': 'business'}],
            'semantic_runtime_plan': {'schema': 'instance-full-semantic-runtime-plan-v4'},
            'full_semantic_runtime_schema': 'instance-full-semantic-runtime-plan-v4'}})
    frozen = {'schema': 'report-full-semantic-graph-binding-v4', 'graph': graph,
        'event_source_bindings': bindings, 'event_admission_policy': copy.deepcopy(waits.POLICY)}
    def row(db, model, identity_value, principal, **kwargs):
        value = task if model is Task else release
        assert model in {Task, InstanceRelease} and identity_value == value.id
        assert principal.workspace_id == workspace and kwargs.get('lock', False) is False
        return value
    monkeypatch.setattr(waits, '_row', row)
    # This is explicitly a unit stand-in for the independently tested cold
    # release authority. It is not a synthetic source/adoption DB record.
    monkeypatch.setitem(sys.modules, 'generic_agents.report_full_semantic_graph_runtime_v4',
        SimpleNamespace(business_binding=lambda actual, business: frozen))
    p = Principal(owner, workspace, 'operator', 'synthetic-task-authority-unit')
    return task, frozen, p, graph, bindings


def test_task_reader_requires_actual_frozen_v4_graph_source_bindings_and_snapshot_hash(monkeypatch):
    task, frozen, p, graph, bindings = task_scope_fixture(monkeypatch)
    assert waits._task_scope(None, p, task, graph, bindings) == {identity(5)}
    wrong = copy.deepcopy(bindings); wrong['response']['producer_id'] = identity(40)
    with pytest.raises(ValueError): waits._task_scope(None, p, task, graph, wrong)
    frozen['schema'] = 'report-full-semantic-graph-binding-v3'
    with pytest.raises(ValueError): waits._task_scope(None, p, task, graph, bindings)
    frozen['schema'] = 'report-full-semantic-graph-binding-v4'
    task.data['graph_wait_authority']['scope_hash'] = 'f' * 64
    with pytest.raises(ValueError): waits._task_scope(None, p, task, graph, bindings)


@pytest.mark.parametrize('change', ['extra_entity', 'duplicate_entity', 'duplicate_snapshot', 'foreign_snapshot'])
def test_task_object_scope_cannot_expand_or_alias_even_with_a_recomputed_scope_hash(monkeypatch, change):
    task, _, p, graph, bindings = task_scope_fixture(monkeypatch)
    if change == 'extra_entity': task.data['entity_ids'].append(identity(50))
    elif change == 'duplicate_entity': task.data['entity_ids'].append(identity(5))
    elif change == 'duplicate_snapshot': task.data['object_snapshots'].append(copy.deepcopy(task.data['object_snapshots'][0]))
    else: task.data['object_snapshots'][0]['id'] = identity(50)
    task.data['graph_wait_authority']['scope_hash'] = digest(task.data['object_snapshots'])
    with pytest.raises(ValueError): waits._task_scope(None, p, task, graph, bindings)


def test_task_reader_does_not_grant_an_unassigned_workspace_operator_access(monkeypatch):
    task, _, p, graph, bindings = task_scope_fixture(monkeypatch)
    p = Principal(identity(60), p.workspace_id, 'operator', 'synthetic-unassigned-unit')
    with pytest.raises(HTTPException) as caught: waits._task_scope(None, p, task, graph, bindings)
    assert caught.value.status_code == 403


def test_real_admission_prefix_task_projection_and_cross_actor_original_history_remain_separate(client, monkeypatch):
    value = scene(client, ('processed-observation-v1', 'anomaly-candidate-v1'))
    graph, bindings = producer_wire(value)
    first, _, _ = observe(client, value, 105)
    with Session(engine()) as db:
        package_id = db.get(Entity, value['object']['id']).schema_capability_id
        consumer = actor(db, value['principal'].workspace_id)
    other_object = entity(client, {'id': package_id})
    second_value = {**value, 'object': other_object}; second, _, _ = observe(client, second_value, 28)
    for observation in (first, second):
        for intent_id, _ in pending_intents(observation['id']):
            job, _ = delivery_job(intent_id); assert process_once(job)
    # Isolate the task-grant boundary: actual producer, intent, admission, actor
    # membership, stream and clock checks below still run against PostgreSQL.
    calls = []
    def scope(db, principal, actual_task, actual_graph, actual_bindings):
        calls.append(principal.user_id)
        assert principal.user_id == consumer.user_id and actual_bindings == bindings
        return {value['object']['id']}
    monkeypatch.setattr(waits, '_task_scope', scope)
    with Session(engine()) as db:
        assert waits.verify_bindings(db, consumer, graph, bindings, lock=True) == bindings
        cut = waits.take_cut(db, consumer); assert cut['sequence'] == 3
        task = SimpleNamespace(id=identity(90), data={'graph_wait_authority': {'cursor': {'sequence': 0, 'at': 0}}})
        rows = waits.read_receipts(db, consumer, task, graph, bindings, after=0, cut=cut)
        assert calls == [consumer.user_id] and len(rows) == 1
        row = rows[0]
        assert set(row) == {'event_id', 'event_type', 'correlation', 'output', 'sequence',
            'received_at', 'producer_id', 'contract_hash'}
        assert row['producer_id'] == bindings['response']['producer_id']
        assert row['correlation'] == value['object']['id']
        assert row['output']['observation_id'] == first['id']
        assert row['output']['result']['attributes']['temperature'] == 105
        assert 'proof' not in row and row['output']['physical_state_verified'] is False
        persisted = db.get(GraphEventAdmission, row['event_id'])
        assert row['received_at'] == persisted.admitted_at.timestamp()
        task.data['graph_wait_authority']['cursor'] = copy.deepcopy(cut)
        assert waits.read_receipts(db, consumer, task, graph, bindings, after=cut['sequence'], cut=cut) == []
        with pytest.raises(HTTPException) as caught:
            waits.admission.read_admission(db, consumer, row['event_id'])
        assert caught.value.status_code == 403
        # A locally resealed receipt still cannot contradict its actual intent.
        tampered = copy.deepcopy(persisted.data)
        tampered['projection']['output']['result']['attributes']['temperature'] = 999
        tampered['admission_hash'] = digest({key: child for key, child in tampered.items() if key != 'admission_hash'})
        persisted.data = tampered; db.commit()
        task.data['graph_wait_authority']['cursor'] = {'sequence': 0, 'at': 0}
        with pytest.raises(HTTPException) as caught:
            waits.read_receipts(db, consumer, task, graph, bindings, after=0, cut=cut)
        assert caught.value.status_code == 409
