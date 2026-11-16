"""Reader-boundary unit spies, not real SQL/IAM/model/runtime verification.

The original packet inventories and frozen graph read are explicit boundaries.
The new producer comparisons, legacy Job/Invocation comparisons, source input
schemas and frozen-source equality checks remain real. No current DataRun exists.
"""
import copy
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import business_decision_sources_v276 as subject
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, Invocation, Job, Task


def ident(value):
    return str(UUID(int=value))


class ReadDB:
    def __init__(self, reader):
        self.reader = reader
        self.inside = 0
        self.reads = 0

    @property
    @contextmanager
    def no_autoflush(self):
        self.inside += 1
        try:
            yield
        finally:
            self.inside -= 1

    def scalar(self, _query):
        assert self.inside > 0
        self.reads += 1
        return self.reader

    def __getattr__(self, name):
        raise AssertionError('Unexpected DB operation: ' + name)


@pytest.fixture
def scene(monkeypatch):
    p = NS(user_id=ident(1), workspace_id=ident(2), role='viewer')
    actor = ident(3)
    task = Task(id=ident(4), workspace_id=p.workspace_id, package_id=ident(5), owner_id=actor,
        data={'instance_release_id': ident(6), 'instance_id': ident(7)})
    release = NS(id=ident(6), instance_id=ident(7), package_id=task.package_id)
    package = NS(id=task.package_id, workspace_id=p.workspace_id, kind='domain_package', data={'frozen': 'package'})
    package.content_hash = package.validated_hash = digest(package.data)
    db = ReadDB(p.user_id)
    rows = {(Task, task.id): task, (Capability, package.id): package}
    audit_rows = {}
    producers = {}

    def producer(index, family):
        activation_id = 'producer_' + str(index)
        cap = NS(id=ident(index), data={'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'},
            'execution': {'adapter': family.ADAPTER}}, release_version='1.0.0')
        cap.content_hash = cap.validated_hash = digest(cap.data)
        node = {'id': activation_id, 'actor_id': actor, 'execution': {'adapter': family.ADAPTER,
            'agent_role': 'data', 'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'release_version': cap.release_version, 'input_schema': cap.data['input_schema'],
            'output_schema': cap.data['output_schema'], 'max_attempts': 1}}
        inputs = {'original': index}
        job = NS(id=ident(index + 1), workspace_id=p.workspace_id, kind=subject.scope.KIND, status='succeeded',
            data={'task_id': task.id, 'actor_id': actor, 'activation_id': activation_id, 'activation_version': 0,
                'cancel_epoch': 0, 'inputs': inputs, 'input_hash': digest(inputs), 'invocation_key': 'original:' + str(index)})
        call = NS(id=ident(index + 2), workspace_id=p.workspace_id, capability_id=cap.id, status='succeeded',
            idempotency_key=job.data['invocation_key'], data={'job_id': job.id, 'inputs': copy.deepcopy(inputs),
                'input_hash': digest(inputs), 'role': 'data', 'capability_hash': cap.content_hash,
                'lease_token': 'original-lease:' + str(index), 'side_effect': True})
        packet = NS(id=ident(index + 3), workspace_id=p.workspace_id, task_id=task.id,
            actor_id=actor, invocation_id=call.id, job_id=job.id, evidence_id=ident(index + 4),
            sha256=str(index)[0] * 64, data={})
        activation = {'id': activation_id, 'epoch': 0, 'status': 'completed',
            'inputs_snapshot': {'arguments': copy.deepcopy(inputs)}}
        transport = {'job_id': job.id, 'invocation_key': call.idempotency_key, 'cancel_epoch': 0,
            'input_hash': digest(inputs), 'phase': 'succeeded', 'dispatch_committed': True,
            'dispatch_ownership': {'job_id': job.id, 'lease_token': call.data['lease_token']},
            'dispatch_input_hash': digest(inputs), 'invocation_id': call.id,
            'dispatch_authorization': {'epoch': 0, 'job_id': job.id, 'lease_token': call.data['lease_token'],
                'input_hash': digest(inputs), 'capability_hash': cap.content_hash, 'actor_id': actor,
                'authorized_at': '2026-11-01T00:00:00+00:00'}}
        task.data = {**task.data, 'graph_jobs': {**task.data.get('graph_jobs', {}), activation_id: transport}}
        audit_rows.setdefault(('workflow.graph.node_completed', task.id), []).append(NS(actor_id=actor,
            data={'activation_id': activation_id, 'invocation_id': call.id}))
        audit_rows.setdefault(('workflow.graph.dispatch_authorized', task.id), []).append(NS(actor_id=actor,
            data={'activation_id': activation_id, 'job_id': job.id, 'epoch': 0, 'input_hash': digest(inputs)}))
        for model, row in ((Capability, cap), (Invocation, call), (Job, job), (family.Packet, packet)):
            rows[(model, row.id)] = row
        value = NS(packet=packet, job=job, call=call, cap=cap, node=node, activation=activation, transport=transport)
        producers[activation_id] = value
        monkeypatch.setattr(family, 'validate_manifest', lambda _value: None)
        return value

    data = producer(10, subject.data_family)
    docs = producer(20, subject.document_family)
    graph = {'nodes': [data.node, docs.node]}
    binding = {'graph': graph, 'binding_hash': digest(graph)}
    state = {'graph_hash': digest(graph)}
    task.data = {**task.data, 'graph_runtime': state}
    for value in producers.values():
        value.job.data['graph_hash'] = state['graph_hash']
        value.call.data['approval'] = (subject.data_family if value is data else subject.document_family).make_approval(
            task, value.job, value.node, value.job.data['inputs'])

    data_ref = {'id': data.packet.id, 'evidence_id': data.packet.evidence_id, 'sha256': data.packet.sha256,
        'version': 1, 'simulation_only': True, 'proposal_only': True, 'cloud_authorized': False}
    goal = {'original_goal': True}
    saved = {'request': {'fields': ['temperature']}, 'goal_delegation': {'task_id': task.id},
        'native_goal_task_id': task.id, 'native_goal_context_hash': digest(goal),
        'result': {'status': 'sufficient', 'result': {'observation_evidence': [{'original_value': 24}]}}}
    saved['native_goal_request_hash'] = digest(saved['request'])
    saved['native_goal_result_hash'] = digest(saved['result'])
    query = {'query_id': ident(50), 'version_at_read': 3, 'request_hash': digest(saved['request']),
        'goal_delegation_hash': digest(saved['goal_delegation']), 'result_hash_at_read': digest(saved['result'])}
    context = {'evidence_package': {'query': query, 'scope': {'task_id': task.id, 'release_id': release.id,
        'package_id': package.id, 'source_binding_hash': binding['binding_hash'], 'native_goal_context_hash': digest(goal)}}}
    data.output = {'packet': data_ref, 'evidence_ids': [data.packet.evidence_id], 'context_result': context}
    data.packet.data = {'context_result': copy.deepcopy(context), 'provenance': {
        'schema': 'workflow-task-evidence-packet-provenance-v199', 'scope': subject.scope._scope(task, data.job, data.node, data.activation),
        'binding_hash': binding['binding_hash'], 'release_id': release.id, 'package_hash': package.content_hash,
        'goal': goal, 'query': {'id': ident(50), 'version': 3, 'data': saved}, 'sources': []}}
    evidence = NS(id=data.packet.evidence_id, sha256=data.packet.sha256)
    monkeypatch.setattr(subject.data_family, '_checked', lambda *_args: (NS(), data.call, data.job, evidence, b'original data', data.output))

    entries = [{'source_name': 'original.txt', 'version_sequence': 1, 'location_label': '原文块1',
        'partial': False, 'warnings': [], 'text': 'Only the originally selected text'}]
    source_set = NS(id=ident(60), source_set_hash=digest(entries), task_id=task.id,
        release_id=release.id, instance_id=release.instance_id, data={'entries': entries})
    docs.packet.source_set_id = source_set.id
    doc_ref = {'id': docs.packet.id, 'evidence_id': docs.packet.evidence_id, 'sha256': docs.packet.sha256,
        'version': 1, 'source_set_id': source_set.id, 'source_set_hash': source_set.source_set_hash}
    docs.output = {'packet': doc_ref, 'evidence_ids': [docs.packet.evidence_id]}
    monkeypatch.setattr(subject.document_family, 'checked_packet', lambda *_args, **_kwargs: {
        'packet_id': docs.packet.id, 'producer_job_id': docs.job.id, 'invocation_id': docs.call.id,
        'evidence_id': docs.packet.evidence_id, 'sha256': docs.packet.sha256})
    monkeypatch.setattr(subject.document_family, '_inventory', lambda *_args: (
        docs.packet, NS(data={'output': docs.output}), docs.call, docs.job, task, source_set, docs.activation, docs.transport))
    for value in producers.values():
        value.call.data['output'] = copy.deepcopy(value.output)
        value.activation['output'] = copy.deepcopy(value.output)
        value.transport['output_hash'] = digest(value.output)

    def fresh(db, principal, model, identity):
        assert db.inside > 0 and principal is p
        assert model is not Audit
        return rows[(model, identity)]
    def frozen(_db, principal, task_id, activation_id):
        assert principal is p and task_id == task.id
        value = producers[activation_id]
        return task, release, {}, graph, state, value.activation, value.node
    monkeypatch.setattr(subject, 'fresh', fresh)
    monkeypatch.setattr(subject, '_audits', lambda _db, _p, action, identity: audit_rows[(action, identity)])
    monkeypatch.setattr(subject.scope, '_frozen', frozen)
    monkeypatch.setattr(subject.workflow, 'business_binding', lambda *_args: binding)
    inputs = {'data_packet': copy.deepcopy(data_ref), 'document_packet': copy.deepcopy(doc_ref),
        'evidence_ids': [data.packet.evidence_id, docs.packet.evidence_id], 'question': 'another contract may add inputs'}
    return NS(db=db, p=p, task=task, data=data, docs=docs, inputs=inputs, saved=saved, source_set=source_set,
        audits=audit_rows, package=package, release=release, actor=actor)


def test_original_packets_freeze_without_a_current_query_or_execution_actor(scene):
    s = scene
    before = copy.deepcopy((s.task.data, s.data.packet.data, s.docs.call.data))
    value = subject.resolve(s.db, s.p, s.task, s.inputs)
    assert s.p.user_id != s.actor
    assert value['data']['provenance']['query']['data'] == s.saved
    assert value['documents']['entries'] == s.source_set.data['entries']
    assert 'Only the originally selected text' in value['documents']['text']
    assert value['sources_hash'] == digest({key: item for key, item in value.items() if key != 'sources_hash'})
    assert subject.checked_frozen(s.db, s.p, s.task, value) == value
    assert before == (s.task.data, s.data.packet.data, s.docs.call.data)
    assert s.db.inside == 0


@pytest.mark.parametrize('damage', ['duplicate_evidence', 'substitute_evidence', 'packet_hash', 'packet_id',
    'producer_running', 'producer_actor', 'original_lease', 'completion_audit', 'query_hash', 'package_hash', 'source_set'])
def test_missing_or_relabelled_original_sources_are_rejected(scene, damage):
    s = scene
    if damage == 'duplicate_evidence': s.inputs['evidence_ids'] = [s.data.packet.evidence_id] * 2
    elif damage == 'substitute_evidence': s.inputs['evidence_ids'][0] = ident(99)
    elif damage == 'packet_hash': s.inputs['data_packet']['sha256'] = '0' * 64
    elif damage == 'packet_id': s.inputs['document_packet']['id'] = s.data.packet.id
    elif damage == 'producer_running': s.data.job.status = 'running'
    elif damage == 'producer_actor': s.docs.job.data['actor_id'] = s.p.user_id
    elif damage == 'original_lease': s.data.call.data['lease_token'] = 'replacement-lease'
    elif damage == 'completion_audit': s.audits[('workflow.graph.node_completed', s.task.id)][0].actor_id = s.p.user_id
    elif damage == 'query_hash': s.saved['result']['result']['observation_evidence'][0]['original_value'] = 99
    elif damage == 'package_hash': s.package.validated_hash = '0' * 64
    else: s.source_set.release_id = ident(99)
    with pytest.raises(HTTPException):
        subject.resolve(s.db, s.p, s.task, s.inputs)
    assert s.db.inside == 0


def test_current_reader_membership_is_required_independently_of_producer(scene):
    scene.db.reader = None
    with pytest.raises(HTTPException):
        subject.resolve(scene.db, scene.p, scene.task, scene.inputs)


def test_rehashed_modified_frozen_data_cannot_replace_the_original_packet(scene):
    value = subject.resolve(scene.db, scene.p, scene.task, scene.inputs)
    value['data']['provenance']['query']['data']['result']['result']['observation_evidence'][0]['original_value'] = 88
    value['sources_hash'] = digest({key: item for key, item in value.items() if key != 'sources_hash'})
    with pytest.raises(HTTPException):
        subject.checked_frozen(scene.db, scene.p, scene.task, value)


def test_packet_ports_preserve_the_original_structures_and_are_independent_copies():
    assert subject.data_packet_schema() == subject.data_family.output_schema()['properties']['packet']
    assert subject.document_packet_schema() == subject.document_family.output_schema()['properties']['packet']
    schema = subject.input_schema(); schema['properties']['data_packet']['properties'].clear()
    assert subject.data_packet_schema()['properties']


def test_derived_original_evidence_ids_do_not_mutate_graph_inputs(scene):
    value = copy.deepcopy(scene.inputs); value.pop('evidence_ids')
    original = copy.deepcopy(value)
    frozen = subject.resolve(scene.db, scene.p, scene.task, value)
    assert value == original and 'evidence_ids' not in value
    assert frozen['evidence_ids'] == [value['data_packet']['evidence_id'], value['document_packet']['evidence_id']]


def test_original_producer_tail_can_only_be_a_wait_condition(scene):
    scene.data.job.status = 'running'
    frozen, pending = subject.wait_snapshot(scene.db, scene.p, scene.task, scene.inputs)
    assert pending == [scene.data.job.id]
    assert frozen['data']['producer_job_id'] == scene.data.job.id
    with pytest.raises(HTTPException):
        subject.resolve(scene.db, scene.p, scene.task, scene.inputs)
    scene.data.job.status = 'succeeded'
    terminal = subject.resolve(scene.db, scene.p, scene.task, scene.inputs)
    assert terminal == frozen


def test_pending_producer_does_not_waive_completed_transport_and_audit(scene):
    scene.data.job.status = 'running'
    scene.audits[('workflow.graph.node_completed', scene.task.id)].clear()
    with pytest.raises(HTTPException):
        subject.wait_snapshot(scene.db, scene.p, scene.task, scene.inputs)
