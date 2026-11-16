"""Context-service wire/state/proof seams; no real IAM, PG or device proof.

The live shared approval guard and the independent reader producer checker are
explicit IO boundaries here. The new consumer, strict schemas, original-call
checks, both independent Audit chains and shared resense/ACK implementation are
actual functions. Root separately runs the registered worker/Task path.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import workflow_service_context_v196 as service
from generic_agents import workflow_service_v195 as shared
from generic_agents import workflow_task_context_v196 as reader
from generic_agents.contracts import digest
from generic_agents.services import validate
from tests.test_workflow_service_v195 import scene as original_scene, resense_boundaries
from tests.test_workflow_service_delegation_v195 import reseal
from tests.test_business_flow_nested_adoption_v195 import identity
from tests.test_report_business_flow_wire_v184 import no_external


def scene(monkeypatch, *, reuse=False):
    s = original_scene(monkeypatch, 'execution_service')
    b = resense_boundaries(monkeypatch, s, reuse=reuse)
    # A complete public synthetic reader value. Its producer authority is not
    # inferred from this value or its digest; that IO seam is asserted below.
    b.request.update(query_id=identity('context-query'), query_request_hash='1' * 64)
    b.plan.data['observation_request'] = copy.deepcopy(b.request)
    b.gap = s.db.rows[(shared.DataGap, b.request['gap_id'])]
    b.gap.status = 'unresolved'
    value = {'schema': reader.CONTEXT,
        'scope': {'workspace_id': s.p.workspace_id, 'task_id': s.task.id,
            'instance_id': s.task.data['instance_id'], 'release_id': s.task.data['instance_release_id'],
            'package_id': s.task.package_id, 'business_id': 'public-business',
            'graph_hash': s.state['graph_hash'], 'source_binding_hash': s.binding['binding_hash'],
            'goal_snapshot_hash': '2' * 64, 'native_goal_context_hash': '3' * 64},
        'query': {'query_id': b.request['query_id'], 'request_hash': b.request['query_request_hash'],
            'goal_delegation_hash': digest(b.request['goal_delegation']),
            'version_at_read': 1, 'result_hash_at_read': '4' * 64},
        'gap': {'gap_id': s.inputs['gap_id'], 'entity_id': b.request['entity_id'],
            'version_at_read': 1, 'fields': list(b.request['fields']),
            'observation_request': copy.deepcopy(b.request), 'observation_request_hash': digest(b.request)},
        'policy': {'policy_id': s.inputs['policy_id'], 'approved_hash': 'a' * 64,
            'approved_by': identity('context-policy-approver'), 'version_at_read': 1,
            'source_pins': [{'source_id': b.acq.capability_id, 'source_hash': b.acq.data['source_hash']}]},
        'producer': {'capability_id': identity('reader-capability'), 'capability_hash': '5' * 64,
            'invocation_id': identity('reader-call'), 'job_id': identity('reader-job'),
            'activation_id': 'reader-activation', 'node_id': 'reader-node', 'cancel_epoch': 0,
            'input_hash': '6' * 64, 'request_key_hash': '7' * 64, 'actor_id': identity('reader-actor')},
        'permissions_verified': False, 'can_dispatch': False}
    value['context_hash'] = digest(value)
    assert reader._check_context(value) == value
    s.inputs = {'context': value}
    s.cap.data.update(version=service.RELEASE_VERSION, input_schema=service.input_schema(),
        output_schema=service.output_schema())
    s.cap.data['execution']['operation'] = service.OP
    s.cap.release_version = service.RELEASE_VERSION
    reseal(s.cap)
    s.job.data.update(inputs=copy.deepcopy(s.inputs), input_hash=digest(s.inputs))
    s.node['execution'].update(capability_hash=s.cap.content_hash,
        input_schema=copy.deepcopy(s.cap.data['input_schema']),
        output_schema=copy.deepcopy(s.cap.data['output_schema']), arguments=copy.deepcopy(s.inputs),
        release_version=s.cap.release_version)
    s.approval = shared.make_approval(s.task, s.job, s.node, s.inputs)
    s.call.data.update(inputs=copy.deepcopy(s.inputs), input_hash=digest(s.inputs),
        capability_hash=s.cap.content_hash, approval=copy.deepcopy(s.approval))
    s.transport['input_hash'] = digest(s.inputs)
    s.transport['dispatch_authorization'].update(input_hash=digest(s.inputs), capability_hash=s.cap.content_hash)
    s.current = {'task': s.task, 'job': s.job, 'node': s.node,
        'release': NS(id=s.task.data['instance_release_id']), 'binding': s.binding,
        'transport': s.transport, 'delegation': {'operation': service.OP, 'agent_role': 'business',
            'service_actor': 'execution_service', 'side_effect': True}, 'capability': s.cap}
    s.approval_checks = []; s.reader_checks = []; s.reader_fault = None

    def approval_boundary(db, p, cap, role, inputs, key, approval, *, _variant=None):
        assert (db, p, cap, role, key) == (s.db, s.p, s.cap, 'business', s.key)
        assert _variant is service  # Never a caller-controlled marker/flag.
        assert inputs == s.inputs and approval == s.approval
        service.validate_manifest(cap.data)
        service._inputs(inputs)
        s.approval_checks.append((s.job.id, s.db.lease['lease_token']))
        return s.current
    monkeypatch.setattr(shared, 'validate_call_approval', approval_boundary)

    def reader_boundary(db, p, supplied, *, task, dispatch=True):
        assert db is s.db and p is s.p and task is s.task
        reader._check_context(supplied)
        s.reader_checks.append(dispatch)
        if s.reader_fault is not None: raise s.reader_fault
        return copy.deepcopy(supplied)
    monkeypatch.setattr(reader, 'checked_context', reader_boundary)

    # Actual selects are separated by exact Audit predicates, not one shared
    # list that would accidentally make either independent chain pass/fail.
    old_scalars = s.db.scalars
    def scalars(statement):
        entity = statement.column_descriptions[0]['entity']
        if entity is not shared.Audit: return old_scalars(statement)
        actual = statement.compile()
        for action in (shared.STARTED, service.PROOF_AUDIT):
            expected = select(shared.Audit).where(shared.Audit.workspace_id == s.p.workspace_id,
                shared.Audit.action == action, shared.Audit.object_id == s.call.id).compile()
            if str(actual) == str(expected) and actual.params == expected.params:
                return [event for event in s.db.events if event.workspace_id == s.p.workspace_id
                    and event.action == action and event.object_id == s.call.id]
        raise AssertionError('Unexpected context proof authority SQL')
    s.db.scalars = scalars
    def record_audit(db, p, action, object_id, data):
        assert db is s.db and p is s.p and object_id == s.call.id
        assert action in {shared.STARTED, service.PROOF_AUDIT}
        s.db.events.append(NS(workspace_id=p.workspace_id, actor_id=p.user_id,
            action=action, object_id=object_id, data=copy.deepcopy(data)))
    monkeypatch.setattr(shared, 'audit', record_audit)
    monkeypatch.setattr(service, 'audit', record_audit)
    s.b = b
    return s


def start(s):
    with pytest.raises(shared.PendingService):
        service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    s.call.status = 'waiting_receipt'; s.transport['phase'] = 'waiting_service'


def complete(s):
    s.b.acq.status = 'succeeded'
    s.b.acq.data['closing_observation_id'] = s.b.observation.id
    s.b.plan.status = 'completed'


@pytest.mark.parametrize('fault', ['version', 'operation', 'side_effect', 'input', 'output'])
def test_new_registration_is_closed_and_does_not_mutate_old_two_uuid_contract(monkeypatch, fault):
    s = scene(monkeypatch)
    old_in = shared.input_schema('resense_with_receipt')
    old_out = shared.output_schema('resense_with_receipt')
    assert service.validate_manifest(s.cap.data) == s.cap.data
    broken = copy.deepcopy(s.cap.data)
    if fault == 'version': broken['version'] = '1.0.0'
    elif fault == 'operation': broken['execution']['operation'] = 'resense_with_receipt'
    elif fault == 'side_effect': broken['execution']['side_effect'] = False
    elif fault == 'input': broken['input_schema'] = old_in
    else: broken['output_schema'] = old_out
    with pytest.raises(HTTPException): service.validate_manifest(broken)
    assert shared.input_schema('resense_with_receipt') == old_in
    assert shared.output_schema('resense_with_receipt') == old_out
    assert service.prelock_gap_id(s.inputs) == s.b.request['gap_id']
    assert service._inputs(s.inputs) is not s.inputs


@pytest.mark.parametrize('reuse', [False, True])
def test_full_original_context_is_wire_input_and_only_original_resense_plan_is_polled(monkeypatch, reuse):
    s = scene(monkeypatch, reuse=reuse)
    original = copy.deepcopy((s.inputs, s.call.data, s.transport))
    start(s)
    proof = copy.deepcopy(s.call.data[service.PROOF_KEY])
    state = copy.deepcopy(s.call.data['service_state'])
    assert s.reader_checks == [False, True] and s.db.commits == 1
    assert set(state['operation_data']) == {'observation_request', 'acquisition_pins', 'source_contracts'}
    assert state['input_hash'] == digest(s.inputs) != digest(service._request_inputs(s.inputs['context']))
    assert s.call.data['inputs'] == original[0] and s.inputs == original[0]
    assert len(s.db.events) == 2 and {e.action for e in s.db.events} == {shared.STARTED, service.PROOF_AUDIT}
    assert proof['producer_invocation_id'] == s.inputs['context']['producer']['invocation_id']
    with pytest.raises(shared.PendingService):
        service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    complete(s)
    output = service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    validate(service.output_schema(), output)
    receipt = output['service_receipt']
    assert receipt['schema'] == service.RECEIPT and receipt['operation'] == service.OP
    assert receipt['context_hash'] == proof['context_hash']
    assert receipt['producer_invocation_id'] == proof['producer_invocation_id']
    assert receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
    assert receipt['observations'][0]['data_receipt_hash'] == digest(s.b.receipt)
    assert receipt['plan_receipts'][0]['plan_id'] == s.b.plan.id
    assert s.reader_checks == [False, True, False, False]
    assert s.call.data['service_state'] == state and s.call.data[service.PROOF_KEY] == proof
    assert s.call.data['lease_token'] == original[1]['lease_token'] != s.db.lease['lease_token']
    assert len([row for row in s.b.calls if isinstance(row, tuple)]) == 1
    assert s.db.commits == 1 and len(s.db.events) == 2


def test_original_unknown_invocation_may_only_reconcile_with_new_lease_same_job_and_key(monkeypatch):
    s = scene(monkeypatch); start(s); complete(s)
    original = copy.deepcopy((s.call.data, s.transport))
    s.call.status = 'unknown'; s.db.lease['lease_token'] = identity('new-poll-lease')
    output = service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert output['service_receipt']['invocation_id'] == s.call.id
    assert (s.call.data, s.transport) == original
    assert len([row for row in s.b.calls if isinstance(row, tuple)]) == 1
    s.call.data['lease_token'] = identity('forged-dispatch-owner')
    with pytest.raises(HTTPException):
        service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)


@pytest.mark.parametrize('already_complete', [False, True])
def test_resolved_gap_only_collects_the_exact_existing_plan_and_never_proposes(monkeypatch, already_complete):
    from generic_agents import perception
    s = scene(monkeypatch, reuse=True)
    s.b.gap.status = 'resolved'
    if already_complete: complete(s)
    def no_propose(*args, **kwargs): raise AssertionError('resolved original request cannot dispatch again')
    monkeypatch.setattr(perception, 'propose_acquisitions', no_propose)
    # Preflight permits cold original-receipt checking, not a fresh device grant.
    assert service.validate_call_approval(s.db, s.p, s.cap, 'business', s.inputs, s.key, s.approval) is s.current
    if already_complete:
        output = service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    else:
        with pytest.raises(shared.PendingService):
            service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
        # In production the gateway persists waiting_receipt and this state in
        # one outcome transaction. This seam does not claim a DB commit proof.
        assert s.call.data['service_state']['operation_data']['acquisition_pins'][0]['acquisition_id'] == s.b.acq.id
        s.call.status = 'waiting_receipt'; s.transport['phase'] = 'waiting_service'
        complete(s)
        output = service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert output['service_receipt']['plan_receipts'][0]['plan_id'] == s.b.plan.id
    assert output['service_receipt']['gap_id'] == s.inputs['context']['gap']['gap_id']
    assert s.reader_checks and all(flag is False for flag in s.reader_checks)
    assert s.call.data['inputs'] == s.inputs
    assert s.call.data['service_state']['input_hash'] == digest(s.inputs)
    assert len(s.db.events) == 2 and not [row for row in s.b.calls if isinstance(row, tuple)]


def test_resolved_without_exact_original_inventory_is_not_success_and_does_not_propose(monkeypatch):
    from generic_agents import perception
    s = scene(monkeypatch, reuse=True); s.b.gap.status = 'resolved'
    s.b.plan.data['observation_request'] = {'gap_id': identity('foreign-gap')}
    monkeypatch.setattr(perception, 'propose_acquisitions', lambda *a, **k:
        (_ for _ in ()).throw(AssertionError('no new request for a resolved gap')))
    with pytest.raises(HTTPException): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert 'service_state' not in s.call.data and s.db.commits == 0


@pytest.mark.parametrize('status', ['cancelled', 'needs_review', 'pending_delivery'])
def test_non_open_gap_never_begins_a_fresh_service_state(monkeypatch, status):
    s = scene(monkeypatch); s.b.gap.status = status
    with pytest.raises(HTTPException): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert 'service_state' not in s.call.data and service.PROOF_KEY not in s.call.data
    assert s.db.commits == 0 and not s.db.events


def test_pending_producer_propagates_before_any_proof_proposal_or_commit(monkeypatch):
    s = scene(monkeypatch)
    pending = reader.ProducerPending(identity('reader-job'), identity('reader-call'))
    s.reader_fault = pending
    with pytest.raises(reader.ProducerPending) as rejected:
        service.validate_call_approval(s.db, s.p, s.cap, 'business', s.inputs, s.key, s.approval)
    assert rejected.value is pending
    with pytest.raises(reader.ProducerPending): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert not s.db.events and s.db.commits == 0 and service.PROOF_KEY not in s.call.data
    assert not [row for row in s.b.calls if isinstance(row, tuple)]


@pytest.mark.parametrize('fault', ['missing_audit', 'orphan_audit', 'changed_producer', 'wrong_actor', 'missing_state'])
def test_saved_source_self_hash_never_replaces_independent_original_producer_proof(monkeypatch, fault):
    s = scene(monkeypatch); start(s); complete(s)
    if fault == 'missing_audit': s.db.events[:] = [e for e in s.db.events if e.action != service.PROOF_AUDIT]
    elif fault == 'orphan_audit': del s.call.data[service.PROOF_KEY]
    elif fault == 'changed_producer':
        proof = s.call.data[service.PROOF_KEY]
        proof['producer_invocation_id'] = identity('other-producer')
        proof['proof_hash'] = digest({k: v for k, v in proof.items() if k != 'proof_hash'})
    elif fault == 'wrong_actor':
        next(e for e in s.db.events if e.action == service.PROOF_AUDIT).actor_id = identity('other-actor')
    else:
        del s.call.data['service_state']
        s.db.events[:] = [e for e in s.db.events if e.action != shared.STARTED]
    before = len([row for row in s.b.calls if isinstance(row, tuple)])
    with pytest.raises(HTTPException):
        service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert len([row for row in s.b.calls if isinstance(row, tuple)]) == before == 1
    assert s.db.commits == 1


def test_current_reader_authority_is_rechecked_after_saved_state_and_never_repaired(monkeypatch):
    s = scene(monkeypatch); start(s); complete(s)
    s.reader_fault = HTTPException(409, 'original producer or current policy no longer verifies')
    with pytest.raises(HTTPException):
        service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    s.reader_fault = None
    def repaired(*args, **kwargs):
        altered = copy.deepcopy(s.inputs['context']); altered['policy']['policy_id'] = identity('replacement')
        return altered
    monkeypatch.setattr(reader, 'checked_context', repaired)
    with pytest.raises(HTTPException):
        service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert s.db.commits == 1 and len([row for row in s.b.calls if isinstance(row, tuple)]) == 1


@pytest.mark.parametrize('fault', ['top_key', 'context_flag', 'null', 'old_two_ids'])
def test_caller_json_cannot_sneak_authorization_or_replace_full_context(monkeypatch, fault):
    s = scene(monkeypatch); value = copy.deepcopy(s.inputs)
    if fault == 'top_key': value['verified'] = True
    elif fault == 'context_flag': value['context']['can_dispatch'] = True
    elif fault == 'null': value['context'] = None
    else: value = service._request_inputs(value['context'])
    with pytest.raises(HTTPException): service._inputs(value)
    assert not s.db.events and s.db.commits == 0
