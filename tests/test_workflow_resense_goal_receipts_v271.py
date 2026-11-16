"""Pure proof/negative boundaries for a read-only goal receipt checker.

These tests do not manufacture real SQL/IAM/lease or sampling evidence. Full
producer, V177 and DataReception checkers remain real dependencies in production;
boundary replacements below exercise omission rejection and actor separation.
"""
import ast
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import workflow_resense_goal_receipts_v271 as receipts
from generic_agents.contracts import digest


def proof_scene():
    owner, reviewer, workspace, task_id, job_id, call_id, cap_id = [str(uuid4()) for _ in range(7)]
    context = {'task': SimpleNamespace(id=task_id), 'job': SimpleNamespace(id=job_id),
        'node': {'actor_id': owner}, 'capability': SimpleNamespace(id=cap_id, content_hash='a' * 64),
        'binding': {'binding_hash': 'b' * 64}}
    inputs = {'context': {'context_hash': 'c' * 64,
        'producer': {'invocation_id': str(uuid4()), 'job_id': str(uuid4())}}}
    approval = {'activation_id': 'resense-activation'}
    key = 'graph:' + task_id + ':resense-activation:1'
    call = SimpleNamespace(id=call_id, data={})
    state = {'schema': receipts.service.STATE, 'operation': receipts.variant.OP,
        'task_id': task_id, 'job_id': job_id, 'activation_id': approval['activation_id'],
        'capability_id': cap_id, 'capability_hash': 'a' * 64, 'input_hash': digest(inputs),
        'approval_hash': digest(approval), 'source_binding_hash': 'b' * 64,
        'invocation_id': call_id, 'request_key': key, 'actor_id': owner,
        'created_at': '2026-11-01T12:00:00+00:00', 'simulation_only': True,
        'operation_data': {'original': 'not authority'}}
    state['state_hash'] = digest(state)
    started = [SimpleNamespace(actor_id=owner, data=receipts.service._audit_data(state))]
    return SimpleNamespace(user_id=reviewer, workspace_id=workspace), context, call, inputs, key, approval, state, started


def test_original_started_actor_is_not_replaced_by_independent_reviewer():
    p, context, call, inputs, key, approval, state, started = proof_scene()
    assert p.user_id != context['node']['actor_id']
    checked = receipts._state_value(state, context, call, inputs, key, approval, started)
    assert checked == state and checked is not state
    assert checked['actor_id'] == context['node']['actor_id'] and checked['actor_id'] != p.user_id


@pytest.mark.parametrize('damage', [None, 'old_reader_label', 'release', 'package', 'job', 'call', 'producer', 'scope', 'context'])
def test_new_family_acceptance_reads_the_original_data_packet_family(monkeypatch, damage):
    # Original V199 inventory and graph reads are explicit read-only boundaries.
    # Family routing, exact original context membership and scope checks run.
    from generic_agents import business_decision_sources_v276 as sources
    p, _, _, _, _, _, _, _ = proof_scene()
    task = SimpleNamespace(id=str(uuid4()), package_id=str(uuid4()),
        data={'instance_id': str(uuid4()), 'business_id': 'public_observation'})
    release = SimpleNamespace(id=str(uuid4())); binding = {'schema': 'authored-decision-workflow-binding-v278',
        'binding_hash': 'b' * 64, 'graph': {'original_graph': True}}
    scope = {'workspace_id': p.workspace_id, 'task_id': task.id, 'instance_id': task.data['instance_id'],
        'release_id': release.id, 'package_id': task.package_id, 'business_id': task.data['business_id'],
        'source_binding_hash': binding['binding_hash'], 'graph_hash': digest(binding['graph'])}
    producer = {'node_id': 'data_packet', 'invocation_id': str(uuid4()), 'job_id': str(uuid4()), 'capability_id': str(uuid4())}
    value = {'producer': producer, 'scope': scope, 'context_hash': 'c' * 64}
    context = {'task': task, 'release': release, 'binding': binding}
    reference = {'id': str(uuid4()), 'evidence_id': str(uuid4()), 'sha256': 'd' * 64}
    call = SimpleNamespace(id=producer['invocation_id'], data={'output': {'packet': reference}})
    data = {'producer_job_id': producer['job_id'], 'invocation_id': call.id, 'context_result': {
        'evidence_package': {'scope': copy.deepcopy(scope), 'producer': copy.deepcopy(producer)},
        'resense_context': copy.deepcopy(value), 'candidates': []}}
    package_hash = 'a' * 64; source_release = release
    if damage == 'old_reader_label': producer['node_id'] = 'context_reader'
    elif damage == 'release': source_release = SimpleNamespace(id=str(uuid4()))
    elif damage == 'package': package_hash = 'f' * 64
    elif damage == 'job': data['producer_job_id'] = str(uuid4())
    elif damage == 'call': data['invocation_id'] = str(uuid4())
    elif damage == 'producer': data['context_result']['evidence_package']['producer']['capability_id'] = str(uuid4())
    elif damage == 'scope': data['context_result']['evidence_package']['scope']['task_id'] = str(uuid4())
    elif damage == 'context': data['context_result']['resense_context']['context_hash'] = 'f' * 64
    monkeypatch.setattr(receipts.reader, '_check_context', lambda *_args: None)
    def fresh(_db, actual, model, identity):
        assert actual is p
        if model is receipts.Invocation:
            assert identity == call.id
            return call
        assert model is receipts.Capability and identity == task.package_id
        return SimpleNamespace(content_hash='a' * 64)
    def original_packet(_db, actual, actual_task, selected):
        assert actual is p and actual_task is task and selected == reference
        return source_release, package_hash, copy.deepcopy(data)
    monkeypatch.setattr(receipts, 'fresh', fresh)
    monkeypatch.setattr(sources, '_data', original_packet)
    monkeypatch.setattr(receipts.reader, 'validate_manifest', lambda *_args: pytest.fail('V199 was misread as the old builtin Reader'))
    if damage is None:
        assert receipts._producer(None, p, context, value) == value
    else:
        with pytest.raises(HTTPException): receipts._producer(None, p, context, value)


@pytest.mark.parametrize('damage', ['missing_audit', 'duplicate_audit', 'reader_actor', 'input',
    'approval', 'binding', 'request_key', 'claimed_production', 'audit_hash', 'bad_time'])
def test_rehashing_state_cannot_replace_the_independent_started_proof(damage):
    p, context, call, inputs, key, approval, state, started = proof_scene()
    if damage == 'missing_audit':
        started.clear()
    elif damage == 'duplicate_audit':
        started.append(copy.deepcopy(started[0]))
    elif damage == 'reader_actor':
        state['actor_id'] = p.user_id
    elif damage == 'input':
        state['input_hash'] = 'f' * 64
    elif damage == 'approval':
        state['approval_hash'] = 'f' * 64
    elif damage == 'binding':
        state['source_binding_hash'] = 'f' * 64
    elif damage == 'request_key':
        state['request_key'] = 'another-request'
    elif damage == 'claimed_production':
        state['simulation_only'] = False
    elif damage == 'audit_hash':
        started[0].data = {**started[0].data, 'state_hash': 'f' * 64}
    else:
        state['created_at'] = 'not a source timestamp'
    state['state_hash'] = digest({field: value for field, value in state.items() if field != 'state_hash'})
    with pytest.raises((HTTPException, ValueError)):
        receipts._state_value(state, context, call, inputs, key, approval, started)


def test_context_source_proof_uses_original_audit_actor_and_current_reader_workspace(monkeypatch):
    p, context, call, inputs, key, approval, _, _ = proof_scene()
    proof = receipts.variant._proof_value(p, context, call, inputs, key, approval)
    call.data[receipts.variant.PROOF_KEY] = proof
    anchored = SimpleNamespace(actor_id=context['node']['actor_id'],
        data=receipts.variant._proof_anchor(proof))
    monkeypatch.setattr(receipts, 'audits', lambda *args: [anchored])
    assert receipts._source_proof(None, p, context, call, inputs, key, approval) == proof
    anchored.actor_id = p.user_id
    with pytest.raises(HTTPException):
        receipts._source_proof(None, p, context, call, inputs, key, approval)


@pytest.mark.parametrize('admitted', [True, False])
def test_current_reader_iam_is_queried_without_impersonating_original_actor(admitted):
    p, context, *_ = proof_scene()
    class Query:
        def scalar(self, statement):
            values = list(statement.compile().params.values())
            assert p.user_id in values and p.workspace_id in values
            assert context['node']['actor_id'] not in values
            return p.user_id if admitted else None
    if admitted:
        receipts._reader_iam(Query(), p)
    else:
        with pytest.raises(HTTPException):
            receipts._reader_iam(Query(), p)


@pytest.mark.parametrize('damage', ['pending', 'missing_event', 'missing_received_at',
    'missing_data_receipt', 'wrong_closing_observation', 'wrong_acquisition'])
def test_success_marker_cannot_replace_actual_observation_event_and_data_receipt(monkeypatch, damage):
    p = SimpleNamespace(workspace_id=str(uuid4()))
    instance, release, entity, source, observation_id, acquisition_id, exchange_id = [str(uuid4()) for _ in range(7)]
    context = {'task': SimpleNamespace(data={'instance_id': instance}), 'release': SimpleNamespace(id=release)}
    observation = SimpleNamespace(id=observation_id, status='processed', entity_id=entity, source_id=source,
        data={'acquisition_id': acquisition_id, 'extracted': {'attributes': {'temperature': 25}}})
    exchange = SimpleNamespace(id=exchange_id, observation_id=observation_id, acquisition_id=acquisition_id)
    acquisition = SimpleNamespace(id=acquisition_id, status='succeeded', entity_id=entity, capability_id=source,
        data={'closing_observation_id': observation_id})
    events = [SimpleNamespace(status='received', received_at=datetime.now(timezone.utc))]
    if damage == 'pending':
        events[0].status = 'pending_delivery'
    elif damage == 'missing_event':
        events.clear()
    elif damage == 'missing_received_at':
        events[0].received_at = None
    elif damage == 'wrong_closing_observation':
        acquisition.data['closing_observation_id'] = str(uuid4())
    elif damage == 'wrong_acquisition':
        exchange.acquisition_id = str(uuid4())
    monkeypatch.setattr(receipts, 'fresh', lambda db, principal, model, identity:
        observation if model is receipts.Observation else exchange)
    monkeypatch.setattr(receipts.mapped, 'guard', lambda *args, **kwargs:
        {'instance_id': instance, 'release_id': release, 'entity_id': entity, 'exchange_id': exchange_id})
    def reception(*args):
        raise HTTPException(409, 'independent data receipt is absent')
    monkeypatch.setattr(receipts.service, '_observation_receipt_hash', reception)
    db = SimpleNamespace(scalars=lambda *args: events)
    with pytest.raises(HTTPException):
        receipts._observation(db, p, context, observation_id, {}, {source: {}}, acquisition=acquisition)


def test_checker_contains_no_write_dispatch_reconcile_or_receipt_creation_calls():
    path = Path(__file__).resolve().parents[1] / 'src/generic_agents/workflow_resense_goal_receipts_v271.py'
    tree = ast.parse(path.read_text())
    forbidden = {'flush', 'commit', 'rollback', 'renew', 'checkpoint', 'reconcile',
        'validate_call_approval', 'checked_context', 'resense_receipt', '_complete_plans',
        'propose_acquisitions', 'dispatch_acquisition', 'receive_observation'}
    names = {node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))}
    assert not names & forbidden

    # Local set inventory additions are not SQLAlchemy Session.add.
    additions = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == 'add']
    assert all(isinstance(node.func.value, ast.Name)
        and node.func.value.id in {'original_ids', 'plan_ids'} for node in additions)
