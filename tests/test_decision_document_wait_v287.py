"""Real preflight/snapshot/ledger bodies with explicit synthetic IO boundaries.

The locked graph/lease/IAM lookup, original packet source inventory, consent
files and audit insertion are spies. This is not real SQL, IAM, producer,
provider or runtime verification. Root runs the tests; no model call is needed.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import models as m
from generic_agents import business_decision_sources_v276 as sources
from generic_agents import business_reasoning_protocols_v276 as protocols
from generic_agents import workflow_document_packet_wait_v207 as subject
from generic_agents.contracts import digest


def ident(value):
    return str(UUID(int=value))


class AuditOnlyDB:
    """Only the real document-wait inventory SELECT is available."""
    def __init__(self):
        self.events = []
        self.scene = None

    def scalars(self, statement):
        s = self.scene
        expected = select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.object_id == s.job.id, m.Audit.action.in_([subject.WAIT, subject.WAKE]))
        assert str(statement) == str(expected)
        assert statement.compile().params == expected.compile().params
        return list(self.events)

    def __getattr__(self, name):
        raise AssertionError('Unexpected DB operation: ' + name)


@pytest.fixture
def scene(monkeypatch):
    p = NS(workspace_id=ident(1), user_id=ident(2), role='operator')
    data_ref = {'id': ident(3), 'evidence_id': ident(4), 'sha256': 'a' * 64,
        'version': 1, 'simulation_only': True, 'proposal_only': True, 'cloud_authorized': False}
    doc_ref = {'id': ident(5), 'evidence_id': ident(6), 'sha256': 'b' * 64,
        'version': 1, 'source_set_id': ident(7), 'source_set_hash': 'c' * 64}
    inputs = {'question': '核对原两份资料是否需要补充观测', 'data_packet': data_ref,
        'document_packet': doc_ref, 'decision_intent': 'information_acquisition',
        'candidate_specs': [{'candidate_ref': 'candidate_1', 'proposal': '比较已登记的输出',
            'claims': [], 'capability_pins': [{'capability_ref': 'cap_1', 'capability_id': ident(8),
                'capability_hash': 'd' * 64, 'release_version': '1.0.0',
                'delivery': 'output', 'ports': []}]}]}
    cap = NS(id=ident(9), data={'execution': {'adapter': protocols.REVIEW}}, content_hash='e' * 64)
    model = NS(id=ident(10), content_hash='f' * 64)
    job = NS(id=ident(11), attempts=1, status='running', data={
        'task_id': ident(12), 'actor_id': p.user_id, 'activation_id': 'reasoning_1',
        'graph_hash': '1' * 64, 'cancel_epoch': 0, 'input_hash': digest(inputs),
        'invocation_key': 'original-decision-key', 'inputs': copy.deepcopy(inputs)})
    task = NS(id=ident(12), version=1, data={'graph_jobs': {
        'reasoning_1': {'job_id': job.id, 'phase': 'running'}}})
    node = {'id': 'reasoning', 'actor_id': p.user_id, 'execution': copy.deepcopy(cap.data['execution'])}
    db = AuditOnlyDB()
    evidence_ids = [data_ref['evidence_id'], doc_ref['evidence_id']]
    grants = {identity: [{'evidence_id': identity, 'audit_id': ident(20 + index),
        'audit_hash': str(index + 2) * 64}] for index, identity in enumerate(evidence_ids)}
    s = NS(db=db, p=p, task=task, job=job, node=node, cap=cap, model=model, inputs=inputs,
        evidence_ids=evidence_ids, grants=grants, pending=[], source_failure=False,
        current_failure=False, existing_intent=False, calls=[], file_ids=[])
    db.scene = s

    def locked(actual_db, principal, actual_task, actual_job, actual_cap, value):
        # Explicit lease/graph boundary; keep raw inputs/hash comparison real.
        s.calls.append('locked')
        assert (actual_db, principal, actual_task, actual_job, actual_cap) == (db, p, task, job, cap)
        subject.need(value == job.data['inputs'] and digest(value) == job.data['input_hash'])
        return task, job, node

    def no_intent(actual_db, actual_task, actual_job):
        s.calls.append('no_intent')
        assert (actual_db, actual_task, actual_job) == (db, task, job)
        subject.need(not s.existing_intent)

    def current(actual_db, principal, actual_task, actual_job):
        s.calls.append('current')
        assert (actual_db, principal, actual_task, actual_job) == (db, p, task, job)
        subject.need(not s.current_failure)
        return task, node, cap, model

    def source_snapshot(actual_db, principal, actual_task, value):
        s.calls.append('sources')
        assert (actual_db, principal, actual_task) == (db, p, task)
        selected = sources._inputs(value)  # Real packet schema and exact EID derivation.
        subject.need(not s.source_failure)
        frozen = {'schema': sources.SCHEMA, 'task_id': task.id,
            'data': {'packet': selected['data_packet'], 'producer_job_id': ident(30)},
            'documents': {'packet': selected['document_packet'], 'producer_job_id': ident(31)},
            'evidence_ids': selected['evidence_ids']}
        frozen['sources_hash'] = digest(frozen)
        return frozen, copy.deepcopy(s.pending)

    def files(actual_db, principal, ids, actual_model):
        s.calls.append('files')
        assert (actual_db, principal, actual_model) == (db, p, model)
        assert ids == evidence_ids
        s.file_ids.append(copy.deepcopy(ids))
        pins = [{'evidence_id': identity, 'sha256': ('a' if index == 0 else 'b') * 64,
            'media_type': 'text/plain', 'size_bytes': 12, 'version_at_wait': 1,
            'authorized_at_wait': bool(s.grants[identity])} for index, identity in enumerate(ids)]
        return pins, copy.deepcopy(s.grants)

    def save_audit(actual_db, principal, action, identity, data):
        assert (actual_db, principal, identity) == (db, p, job.id)
        db.events.append(NS(id=ident(100 + len(db.events)), actor_id=p.user_id,
            action=action, object_id=identity, data=copy.deepcopy(data)))

    def forbidden_legacy(*_args, **_kwargs):
        raise AssertionError('Decision protocols must not use the legacy document-only selector')

    monkeypatch.setattr(subject.original, '_locked_preflight', locked)
    monkeypatch.setattr(subject.consent, '_no_intent', no_intent)
    monkeypatch.setattr(subject.consent, '_current', current)
    monkeypatch.setattr(subject.consent, '_files', files)
    monkeypatch.setattr(sources, 'wait_snapshot', source_snapshot)
    monkeypatch.setattr(subject.packet, 'uses_family', forbidden_legacy)
    monkeypatch.setattr(subject.packet, 'selected_packets', forbidden_legacy)
    monkeypatch.setattr(subject, 'audit', save_audit)
    return s


def use_adapter(s, adapter):
    s.cap.data['execution']['adapter'] = adapter
    s.node['execution']['adapter'] = adapter


def preflight(s, *, node=None):
    return subject.preflight(s.db, s.p, s.task, s.job,
        s.node if node is None else node, s.cap, s.inputs)


@pytest.mark.parametrize('adapter', [protocols.DECISION, protocols.REVIEW])
def test_both_fixed_decision_protocols_derive_eids_without_changing_raw_job_inputs(scene, adapter):
    s = scene; use_adapter(s, adapter)
    before = copy.deepcopy((s.inputs, s.job.data, s.task.data))
    assert preflight(s) is False
    assert s.calls == ['locked', 'no_intent', 'current', 'sources', 'files']
    assert s.file_ids == [s.evidence_ids]
    assert (s.inputs, s.job.data, s.task.data) == before
    assert 'evidence_ids' not in s.inputs and not s.db.events and s.job.status == 'running'


@pytest.mark.parametrize('adapter', [protocols.DECISION, protocols.REVIEW])
@pytest.mark.parametrize('pending_packet', ['data_packet', 'document_packet'])
def test_either_original_producer_tail_waits_with_both_packets_on_the_same_job(scene, adapter, pending_packet):
    s = scene; use_adapter(s, adapter)
    s.pending = [ident(30 if pending_packet == 'data_packet' else 31)]
    # The source boundary supplies the pending producer list; either committed
    # original producer may still be reaped. Both references must stay frozen.
    before_identity = subject.identity(s.job); before_inputs = copy.deepcopy(s.inputs)
    assert preflight(s) is True
    record = subject.inventory(s.db, s.p, s.task, s.job)['history'][0]
    assert record['reason'] == 'producer' and record['identity'] == before_identity
    assert record['packets'][0][pending_packet] == s.inputs[pending_packet]
    assert record['packets'][0]['data_packet'] == s.inputs['data_packet']
    assert record['packets'][0]['document_packet'] == s.inputs['document_packet']
    assert s.inputs == s.job.data['inputs'] == before_inputs and 'evidence_ids' not in s.inputs
    assert s.job.status == subject.PHASE and s.task.version == 2
    assert [event.action for event in s.db.events] == [subject.WAIT]
    assert not set(s.task.data['graph_jobs']['reasoning_1']) & {
        'dispatch_committed', 'dispatch_ownership', 'dispatch_authorization', 'invocation_id'}


@pytest.mark.parametrize('unconsented_index', [0, 1])
def test_missing_consent_for_either_original_eid_waits_before_any_intent(scene, unconsented_index):
    s = scene; s.grants[s.evidence_ids[unconsented_index]] = []
    assert preflight(s) is True
    record = subject.inventory(s.db, s.p, s.task, s.job)['history'][0]
    assert record['reason'] == 'consent'
    assert [pin['evidence_id'] for pin in record['evidence_pins']] == s.evidence_ids
    assert s.file_ids == [s.evidence_ids] and s.job.status == subject.PHASE


@pytest.mark.parametrize('damage', ['wrong_eid', 'same_eid', 'unknown_input'])
def test_real_protocol_input_validation_rejects_tampering_before_scope_or_snapshot(scene, damage):
    s = scene
    if damage == 'wrong_eid': s.inputs['evidence_ids'] = [s.evidence_ids[0], ident(99)]
    elif damage == 'same_eid': s.inputs['document_packet']['evidence_id'] = s.evidence_ids[0]
    else: s.inputs['permission'] = True
    with pytest.raises(HTTPException): preflight(s)
    assert not s.calls and not s.db.events and subject.KEY not in s.job.data


def test_snapshot_body_propagates_original_source_proof_failure_without_wait_or_consent(scene):
    s = scene; s.source_failure = True
    with pytest.raises(HTTPException) as error:
        subject.snapshot_inputs(s.db, s.p, s.task, s.cap, s.inputs, s.model)
    assert error.value.status_code == 409 and s.calls == ['sources']
    assert not s.file_ids and not s.db.events and subject.KEY not in s.job.data


@pytest.mark.parametrize('damage', ['input_hash', 'wrong_node', 'current_authority', 'existing_intent', 'source'])
def test_preflight_preserves_original_gates_and_never_persists_after_failure(scene, damage):
    s = scene; node = s.node
    if damage == 'input_hash': s.job.data['input_hash'] = '0' * 64
    elif damage == 'wrong_node': node = {**s.node, 'id': 'another-node'}
    elif damage == 'current_authority': s.current_failure = True
    elif damage == 'existing_intent': s.existing_intent = True
    else: s.source_failure = True
    with pytest.raises(HTTPException): preflight(s, node=node)
    assert not s.db.events and subject.KEY not in s.job.data and s.job.status == 'running'
    assert s.task.version == 1 and not s.file_ids


@pytest.mark.parametrize('declared', [False, True])
def test_legacy_keeps_its_family_gate_and_document_only_snapshot(scene, monkeypatch, declared):
    s = scene; use_adapter(s, protocols.LEGACY)
    s.inputs = {'question': '比较原资料', 'evidence_ids': s.evidence_ids,
        'candidates': [{'candidate_ref': 'proposal_1', 'summary': '原候选', 'context': None}]}
    s.job.data['inputs'] = copy.deepcopy(s.inputs); s.job.data['input_hash'] = digest(s.inputs)
    family_calls = []
    def uses_family(db, p, task, ids):
        assert (db, p, task, ids) == (s.db, s.p, s.task, s.evidence_ids)
        family_calls.append(copy.deepcopy(ids)); return declared
    row = NS(id=ident(40), evidence_id=s.evidence_ids[1], sha256='b' * 64,
        invocation_id=ident(41), source_set_id=ident(42), data={'selected': {'source_set_hash': 'c' * 64}})
    producer = NS(id=ident(43), status='succeeded')
    monkeypatch.setattr(subject.packet, 'uses_family', uses_family)
    monkeypatch.setattr(subject.packet, 'selected_packets', lambda *_args: [row])
    monkeypatch.setattr(subject.packet, '_inventory', lambda *_args: (
        row, None, None, producer, None, None, {'status': 'completed'}, {'phase': 'succeeded'}))
    monkeypatch.setattr(subject.packet, 'packet_proof', lambda actual: {'packet_id': actual.id})
    assert preflight(s) is (False if declared else None)
    assert family_calls == [s.evidence_ids] and 'sources' not in s.calls
    assert s.file_ids == ([s.evidence_ids] if declared else [])
    assert not s.db.events and subject.KEY not in s.job.data


def test_unknown_adapter_is_not_a_server_decision_protocol(scene):
    s = scene; use_adapter(s, 'workspace_supplied_review')
    s.inputs = {'not_a_protocol': True}
    assert preflight(s) is None
    assert not s.calls and not s.db.events and subject.KEY not in s.job.data
