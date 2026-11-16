"""Pure V270 compatibility and unchanged V202 rejection/requeue boundaries.

The authored binding verifier and candidate guards are real. The existing V202
IO/context fixture supplies explicit synthetic authority for audit/queue tests;
these tests do not claim a persisted Task, paid model or device execution.
"""
import copy
import socket
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import authored_service_workflow_store_v270 as authored
from generic_agents import workflow_registered_tool_plan_v201 as plans
from generic_agents import workflow_simulation_review_v202 as subject
from generic_agents.contracts import digest
from tests.test_authored_service_workflow_v270 import cold_binding, command
from tests.test_workflow_simulation_review_v202 import checked_context, result, scene


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail('Compatibility/review boundaries must never perform network IO')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


def authored_parts():
    body = command()
    data = {'execution': {'adapter': 'workflow_service', 'service_actor': 'execution_service',
        'operation': plans.OP, 'side_effect': True}, 'input_schema': plans.input_schema(),
        'output_schema': plans.output_schema(), 'budget': {'max_attempts': 1}}
    pin = body['tool_bindings']['planner']; pin['capability_hash'] = digest(data)
    binding = cold_binding(body)
    graph = binding['graph']; node = next(value for value in graph['nodes'] if value['id'] == 'planner')
    cap = NS(id=pin['capability_id'], content_hash=digest(data), validated_hash=digest(data),
        release_version='1.0.0', data=data)
    business = {'name': binding['business_name'], 'flow': copy.deepcopy(binding['flow'])}
    return binding, business, graph, node, cap


def conflict_result():
    value = result()
    value['review_state'] = 'conflicting_evidence'
    value['analysis']['candidate_assessments'][1]['assessment'] = 'conflicting'
    return value


def test_full_authored_binding_selects_actual_planner_delegation_without_translation():
    binding, business, graph, node, cap = authored_parts()
    before = copy.deepcopy(binding)
    assert 'translation' not in binding
    assert subject._review_delegation(binding, business, graph, node, cap) == {
        'service_actor': 'execution_service', 'operation': plans.OP, 'agent_role': 'business'}
    assert binding == before


@pytest.mark.parametrize('damage', ['simulation', 'service', 'binding_hash', 'graph',
    'actor', 'capability_id', 'capability_hash', 'side_effect', 'business'])
def test_authored_family_does_not_accept_relabelled_source_or_actual_capability(damage):
    binding, business, graph, node, cap = authored_parts()
    if damage == 'simulation':
        binding['simulation_only'] = False
        binding = authored.common._seal({k: v for k, v in binding.items() if k != 'binding_hash'})
    elif damage == 'service':
        binding['service_delegations']['planner']['operation'] = 'resense_from_context'
        binding = authored.common._seal({k: v for k, v in binding.items() if k != 'binding_hash'})
    elif damage == 'binding_hash':
        binding['binding_hash'] = '0' * 64
    elif damage == 'graph':
        graph = copy.deepcopy(graph); graph['entry'] = 'planner'
    elif damage == 'actor':
        node = copy.deepcopy(node); node['actor_id'] = body_actor = 'another_actor'
        assert body_actor != binding['tool_bindings']['planner']['actor_id']
    elif damage == 'capability_id':
        cap.id = binding['tool_bindings']['resense']['capability_id']
    elif damage == 'capability_hash':
        cap.validated_hash = '0' * 64
    elif damage == 'side_effect':
        cap.data['execution']['side_effect'] = False
    else:
        business['name'] = 'another_business'
    with pytest.raises(HTTPException):
        subject._review_delegation(binding, business, graph, node, cap)


def review_context(scene, monkeypatch):
    """Declare the frozen SQL/live context boundary; keep new family and old
    candidate eligibility checks real. No successful producer is manufactured.
    """
    prior = subject._context
    parts = authored_parts()
    def checked(*args, **kwargs):
        subject._review_delegation(*parts)
        selected, assessment = subject.selected_with_gaps(scene.inputs['reasoning_result'])
        values = list(prior(*args, **kwargs))
        values[5], values[6] = selected, assessment
        return tuple(values)
    monkeypatch.setattr(subject, '_context', checked)


def test_current_unselected_conflict_returns_normal_not_applicable_without_mutation(scene, monkeypatch):
    scene.inputs['reasoning_result'] = conflict_result()
    before = copy.deepcopy((scene.inputs, scene.job.__dict__, scene.task.__dict__))
    review_context(scene, monkeypatch)
    shown = subject.describe(scene.db, scene.p, scene.task, 'a')
    assert shown['state'] == 'not_applicable' and shown['can_confirm'] is False
    assert shown['basis_hash'] is None and shown['reason']
    assert '冲突' in shown['reason'] and not scene.db.events
    assert before == (scene.inputs, scene.job.__dict__, scene.task.__dict__)


def test_conflicting_result_still_cannot_be_submitted_or_selected(scene, monkeypatch):
    scene.inputs['reasoning_result'] = conflict_result()
    review_context(scene, monkeypatch)
    with pytest.raises(HTTPException):
        subject.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    with pytest.raises(HTTPException):
        plans.eligible_candidate(scene.db, scene.p, scene.task, scene.job, scene.inputs, scene.producer)
    assert scene.job.status == 'failed' and scene.job.attempts == 1 and not scene.db.events


@pytest.mark.parametrize('failure', [KeyError('translation'), TypeError('invalid binding')])
def test_structural_source_failure_is_not_exposed_as_http500(scene, monkeypatch, failure):
    def broken(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(subject, '_context', broken)
    shown = subject.describe(scene.db, scene.p, scene.task, 'a')
    assert shown['state'] == 'not_applicable' and shown['can_confirm'] is False
    assert shown['reason'] == subject.ERROR and not scene.db.events


def test_original_no_conflict_missing_information_review_retains_audited_same_job_requeue(scene, monkeypatch):
    review_context(scene, monkeypatch)
    before = copy.deepcopy(scene.inputs)
    shown = subject.describe(scene.db, scene.p, scene.task, 'a')
    assert shown['can_confirm'] is True and shown['missing_information']
    receipt = subject.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert receipt['job_id'] == scene.job.id and receipt['queued'] is True
    assert receipt['engineering_only'] is True and receipt['gaps_resolved'] is False
    assert receipt['human_review_verified'] is False and scene.inputs == before
    assert scene.job.attempts == 1 and scene.job.data['attempt'] == 1
    assert scene.job.data['invocation_key'] == 'original-key' and len(scene.db.events) == 1
    assert subject.historical_record(scene.db, scene.task, scene.command.request_id)['prior_job']['status'] == 'failed'


@pytest.mark.parametrize('existing', ['call', 'dispatch_audit', 'dispatch_marker'])
def test_authored_compatibility_cannot_bypass_existing_no_dispatch_proof(scene, monkeypatch, existing):
    review_context(scene, monkeypatch)
    if existing == 'call':
        scene.db.call_id = 'already_existing_invocation'
    elif existing == 'dispatch_audit':
        scene.db.committed_id = 'already_existing_dispatch_audit'
    else:
        scene.task.data['graph_jobs']['a']['dispatch_authorization'] = False
    with pytest.raises(HTTPException):
        subject.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert scene.job.status == 'failed' and not scene.db.events


def test_saved_review_cannot_make_a_later_conflicting_result_eligible(scene, monkeypatch):
    subject.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    checked_context(scene, monkeypatch)
    scene.inputs['reasoning_result'] = conflict_result()
    with pytest.raises(HTTPException):
        subject.checked_selection(scene.db, scene.p, scene.task, scene.job, scene.inputs, scene.producer)
