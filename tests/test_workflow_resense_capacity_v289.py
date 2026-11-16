"""Ended V196 CPU accounting; source graph/IAM are explicit IO seams.

Actual reader output/schema, frozen input checks, original ORM inventory,
V196 proof/STARTED audit checks and capacity logic run. No real authority,
device result, PG persistence or unknown-result reconciliation is claimed.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import workflow_local_transaction_capacity_v201 as subject
from generic_agents import workflow_service_context_v196 as handler
from generic_agents import workflow_graph_scope, workflow_graph
from generic_agents import workflow_job_dependencies_v264 as dependencies
from generic_agents import authored_goal_data_workflow_store_v288 as family
from generic_agents import models as m
from generic_agents.contracts import digest
from tests.test_workflow_local_transaction_capacity_v201 import ReadIO, scene as base_scene, uid
from tests.test_workflow_task_context_v196 import scene as reader_scene, run, settled
from tests.test_report_business_flow_wire_v184 import no_external


class InventoryIO(ReadIO):
    def clause_matches(self, row, clause):
        left = getattr(clause, 'left', None)
        key = getattr(getattr(left, 'right', None), 'value', None)
        if key == 'job_id':
            assert clause.operator.__name__ == 'eq'
            return row.data.get(key) == clause.right.value
        if getattr(left, 'key', None) == 'object_id':
            return row.object_id == clause.right.value
        return super().clause_matches(row, clause)


@pytest.fixture
def scene(monkeypatch):
    r = reader_scene.__wrapped__(monkeypatch)
    r.cap.workspace_id = r.source.workspace_id = r.p.workspace_id
    r.job.workspace_id = r.call.workspace_id = r.p.workspace_id
    r.cap.kind = 'tool'; r.cap.status = 'published'; r.cap.release_version = '1.0.0'
    r.cap.content_hash = r.cap.validated_hash = digest(r.cap.data)
    r.call.data['capability_hash'] = r.cap.content_hash
    r.source.kind = 'source'; r.source.status = 'published'; r.source.data = {'immutable': 'source'}
    r.source.content_hash = r.source.validated_hash = digest(r.source.data)
    result = run(r, {'policy_id': r.policy.id, 'approved_hash': r.policy.data['approved_hash']})
    settled(r, result)
    r.job.idempotency_key = r.call.idempotency_key
    r.call.data['side_effect'] = False
    context = result['resense_context']
    s = base_scene(); s.p = r.p; s.task = r.task
    s.cap.workspace_id = s.p.workspace_id
    s.cap.release_version = handler.RELEASE_VERSION
    s.cap.data.update(version=handler.RELEASE_VERSION, allowed_agent_roles=['business'],
        input_schema=handler.input_schema(), output_schema=handler.output_schema(),
        execution={'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': handler.OP, 'side_effect': True})
    s.cap.content_hash = s.cap.validated_hash = digest(s.cap.data)
    s.inputs = {'context': copy.deepcopy(context)}
    s.key = 'graph:' + s.task.id + ':a_resense:1'
    s.job.workspace_id = s.p.workspace_id; s.job.idempotency_key = s.key
    s.job.data.update(task_id=s.task.id, actor_id=s.p.user_id, activation_id='a_resense',
        graph_hash=r.state['graph_hash'], inputs=copy.deepcopy(s.inputs),
        input_hash=digest(s.inputs), invocation_key=s.key)
    s.node = {'id': 'resense', 'actor_id': s.p.user_id, 'execution': {'adapter': 'workflow_service',
        'agent_role': 'business', 'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash,
        'release_version': s.cap.release_version, 'input_schema': handler.input_schema(),
        'output_schema': handler.output_schema()}}
    activation = {'id': 'a_resense', 'node_id': 'resense', 'epoch': 0,
        'inputs_snapshot': {'arguments': copy.deepcopy(s.inputs)}, 'status': 'failed'}
    s.task.data['graph_runtime']['activations']['a_resense'] = activation
    s.transport = {'job_id': s.job.id, 'invocation_key': s.key, 'cancel_epoch': 0, 'attempt': 1,
        'input_hash': digest(s.inputs), 'dispatch_input_hash': digest(s.inputs), 'dispatch_committed': True,
        'dispatch_ownership': {'job_id': s.job.id, 'lease_token': s.call.data['lease_token']}, 'phase': 'blocked',
        'invocation_id': s.call.id, 'dispatch_authorization': {'epoch': 0, 'job_id': s.job.id,
            'lease_token': s.call.data['lease_token'], 'actor_id': s.p.user_id,
            'input_hash': digest(s.inputs), 'capability_hash': s.cap.content_hash, 'authorized_at': '2026-10-30T00:00:00+00:00'}}
    s.task.data['graph_jobs']['a_resense'] = s.transport
    s.approval = handler.shared.make_approval(s.task, s.job, s.node, s.inputs)
    s.call.workspace_id = s.p.workspace_id; s.call.idempotency_key = s.key
    s.call.data.update(role='business', capability_hash=s.cap.content_hash,
        capability_snapshot=copy.deepcopy(s.cap.data), inputs=copy.deepcopy(s.inputs),
        input_hash=digest(s.inputs), job_id=s.job.id, approval=copy.deepcopy(s.approval))
    s.binding = {'schema': 'authored-goal-data-workflow-binding-v288', 'workspace_id': s.p.workspace_id,
        'binding_hash': context['scope']['source_binding_hash'], 'graph': r.graph}
    r.business['id'] = s.task.data['business_id']
    current = {'task': s.task, 'job': s.job, 'node': s.node, 'binding': s.binding}
    proof = handler._proof_value(s.p, current, s.call, s.inputs, s.key, s.approval)
    s.call.data[handler.PROOF_KEY] = proof
    audits = [NS(id=uid('source-proof'), workspace_id=s.p.workspace_id, action=handler.PROOF_AUDIT,
        object_id=s.call.id, actor_id=s.p.user_id, data=handler._proof_anchor(proof))]
    for action in ('workflow.graph.dispatch', 'workflow.graph.dispatch_authorized'):
        data = {'activation_id': 'a_resense', 'job_id': s.job.id}
        if action.endswith('authorized'): data.update(epoch=0, input_hash=digest(s.inputs))
        audits.append(NS(id=uid(action), workspace_id=s.p.workspace_id, action=action,
            object_id=s.task.id, actor_id=s.p.user_id, data=data))
    audits.extend(r.db.events)
    s.db = InventoryIO(); s.db.rows = {m.Invocation: [s.call, r.call], m.Job: [s.job, r.job],
        m.Task: [s.task], m.Capability: [s.cap, r.cap, r.source], m.DataRun: [r.run],
        m.DataGap: [r.gap], m.MonitorPolicy: [r.policy], m.Audit: audits}
    monkeypatch.setattr(workflow_graph_scope, '_frozen', lambda *a: (
        s.task, r.release, r.business, r.graph, s.task.data['graph_runtime'], activation, s.node))
    # Only graph ownership/registration is an explicit synthetic IO boundary;
    # all other frozen source bytes, hashes and independent audits remain real.
    monkeypatch.setattr(workflow_graph_scope, '_job_matches', lambda *a: s.transport)
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *a: s.binding)
    monkeypatch.setattr(family, 'check_binding', lambda value: value)
    monkeypatch.setattr(family, 'service_delegations', lambda value: {'resense': {
        'service_actor': 'execution_service', 'operation': handler.OP, 'agent_role': 'business'}})
    pin = {'job_id': r.job.id, 'actor_id': r.job.data['actor_id'],
        'activation_id': r.job.data['activation_id'], 'invocation_id': r.call.id,
        'capability_id': r.cap.id, 'capability_hash': r.cap.content_hash,
        'input_hash': r.job.data['input_hash'], 'output_hash': digest(result)}
    monkeypatch.setattr(dependencies, 'parent_pin', lambda *a: copy.deepcopy(pin))
    s.reader = r; s.context = context; s.proof = proof; s.audits = audits
    return s


@pytest.mark.parametrize('ended', sorted(subject.ENDED))
def test_exact_proven_ended_resense_releases_only_cpu_without_mutating_unknown(scene, ended):
    s = scene; s.job.status = ended
    before = copy.deepcopy((s.call.data, s.job.data, s.task.data, s.audits))
    assert subject.count_active(s.db, s.p, s.cap) == 0
    assert s.call.status == 'unknown'
    assert (s.call.data, s.job.data, s.task.data, s.audits) == before


def test_actual_cold_source_proof_reports_fixture_mismatch_directly(scene):
    s = scene
    assert subject._resense_source_proof(s.db, s.p, s.cap, s.call, s.job, s.task,
        s.transport, handler, s.inputs) == s.inputs['context']


@pytest.mark.parametrize('ref', ['task_id', 'gap_id', 'query_id'])
def test_original_http_exchange_audit_holds_cpu_without_mutable_exchange_or_children(scene, ref):
    from generic_agents.http_observation_exchange_v177 import ACTION
    s = scene
    value = s.task.id if ref == 'task_id' else s.context['gap']['gap_id'] if ref == 'gap_id' else s.context['query']['query_id']
    s.audits.append(NS(id=uid('orphan-http-' + ref), workspace_id=s.p.workspace_id,
        action=ACTION, object_id=uid('missing-exchange'), actor_id=s.p.user_id,
        data={'state': {'data': {'freeze': {'parent_pins': {ref: value}}}}}))
    assert subject._resense_footprint_empty(s.db, s.p, s.task, s.context) is False
    assert subject.count_active(s.db, s.p, s.cap) == 1 and s.call.status == 'unknown'


@pytest.mark.parametrize('fault', ['missing_proof', 'proof_audit', 'duplicate_proof', 'dispatch',
    'authorization_audit', 'authorization', 'source_actor', 'reader_call', 'reader_job',
    'reader_manifest', 'reader_anchor', 'q0', 'gap', 'policy', 'source', 'state', 'orphan_state', 'output'])
def test_missing_or_drifted_actual_originals_hold_cpu_instead_of_inventing_result(scene, fault):
    s = scene
    if fault == 'missing_proof': s.call.data.pop(handler.PROOF_KEY)
    elif fault == 'proof_audit': s.audits[:] = [a for a in s.audits if a.action != handler.PROOF_AUDIT]
    elif fault == 'duplicate_proof': s.audits.append(copy.deepcopy(s.audits[0]))
    elif fault == 'dispatch': s.audits[:] = [a for a in s.audits if a.action != 'workflow.graph.dispatch']
    elif fault == 'authorization_audit': s.audits[:] = [a for a in s.audits if a.action != 'workflow.graph.dispatch_authorized']
    elif fault == 'authorization': s.transport.pop('dispatch_authorization')
    elif fault == 'source_actor': s.audits[0].actor_id = uid('other-actor')
    elif fault == 'reader_call': s.reader.call.status = 'unknown'
    elif fault == 'reader_job': s.reader.job.status = 'running'
    elif fault == 'reader_manifest': s.reader.cap.data['execution']['handler'] = 'not-the-reader'
    elif fault == 'reader_anchor': s.audits[:] = [a for a in s.audits if a.action != handler.reader.PRODUCED]
    elif fault == 'q0': s.reader.run.data['request']['question'] = 'changed'
    elif fault == 'gap': s.reader.gap.data['request'] = {}
    elif fault == 'policy': s.reader.policy.data['approved_hash'] = '0' * 64
    elif fault == 'source': s.reader.source.status = 'draft'
    elif fault == 'state': s.call.data['service_state'] = {'unverified': True}
    elif fault == 'output': s.call.data['output'] = {'unverified': True}
    elif fault == 'orphan_state': s.audits.append(NS(id=uid('orphan-state'), workspace_id=s.p.workspace_id,
        action=handler.shared.STARTED, object_id=s.call.id, actor_id=s.p.user_id, data={}))
    assert subject.count_active(s.db, s.p, s.cap) == 1 and s.call.status == 'unknown'


@pytest.mark.parametrize('model,field', [(m.ObservationPlan, 'task_id'), (m.Acquisition, 'gap_id'),
    (m.Job, 'query_id'), (m.Observation, 'task_id'), (m.Event, 'query_id'), (m.Memory, 'gap_id')])
def test_any_unaccounted_causal_footprint_holds_cpu_even_without_state(scene, model, field):
    s = scene; value = s.task.id if field == 'task_id' else s.context['gap']['gap_id'] if field == 'gap_id' else s.context['query']['query_id']
    row = NS(id=uid('unaccounted'), workspace_id=s.p.workspace_id, data={field: value},
        kind='dispatch_acquisition' if model is m.Job else 'observation.data',
        policy_id=uid('other-policy'), entity_id=s.context['gap']['entity_id'], capability_id=s.reader.source.id)
    if model is m.Memory: row.data['type'] = 'observation_evidence'
    s.db.rows.setdefault(model, []).append(row)
    assert subject.count_active(s.db, s.p, s.cap) == 1


def test_inventory_overflow_is_not_zero_and_source_unknown_keeps_effect_slot(scene):
    s = scene; s.db.rows[m.ObservationPlan] = [NS(workspace_id=s.p.workspace_id)
        for _ in range(subject.MAX_FOOTPRINT + 1)]
    assert subject.count_active(s.db, s.p, s.cap) == 1
    source = s.reader.source
    child = NS(id=uid('child-effect'), workspace_id=s.p.workspace_id, capability_id=source.id,
        status='unknown', data={})
    s.db.rows[m.Invocation].append(child)
    assert subject.count_active(s.db, s.p, source) == 1 and child.status == 'unknown'
