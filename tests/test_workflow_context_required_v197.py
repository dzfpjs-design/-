"""Real reader provenance + explicit read-only narrowing, with scoped IO seams.

The producer uses the actual pure graph completion and reader cold checker.
Only current DB/IAM/source access and the consumer's leased graph_context are
synthetic boundaries; these tests are not registration, PostgreSQL or device
proof. The IO fixture forbids writes and external operations.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import workflow_context_required_v197 as required
from generic_agents import workflow_task_context_v196 as reader
from generic_agents import workflow_service_context_v196 as service
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.business_flow_review_store import _same
from generic_agents.services import validate
from tests.test_workflow_task_context_v196 import scene as producer_scene, run, settled, identity
from tests.test_report_business_flow_wire_v184 import no_external


def consumer(monkeypatch, producer_scene, *, null=False):
    s = producer_scene
    result = settled(s, run(s, {'policy_id': s.policy.id,
        'approved_hash': s.policy.data['approved_hash']}))
    assert result['status'] == 'ready' and result['resense_context'] is not None
    original = copy.deepcopy(result['resense_context'])
    cap = NS(id=identity('required-context-capability'), data={'kind': 'tool',
        'allowed_agent_roles': ['data'],
        'execution': {'adapter': 'builtin', 'handler': required.HANDLER, 'side_effect': False},
        'input_schema': required.input_schema(), 'output_schema': required.output_schema()})
    inputs = {'context': None if null else copy.deepcopy(original)}
    job = NS(id=identity('required-context-job'), data={'inputs': copy.deepcopy(inputs)})
    node = {'execution': {'agent_role': 'data'}}
    checks = []
    def frozen_consumer(db, p, key, capability):
        assert db is s.db and p is s.p and key == 'public-required-context-key' and capability is cap
        checks.append('actual-consumer-scope-boundary')
        return s.task, job, node, {}
    monkeypatch.setattr(required.graph_scope, 'graph_context', frozen_consumer)
    return NS(producer=s, cap=cap, job=job, node=node, inputs=inputs,
        original=original, checks=checks, key='public-required-context-key')


def execute(c):
    s = c.producer
    return required.execute(s.db, s.p, c.cap, c.inputs, c.key)


def test_type_narrowing_uses_the_real_original_producer_and_preserves_detached_full_value(monkeypatch, producer_scene):
    c = consumer(monkeypatch, producer_scene); s = c.producer
    before = copy.deepcopy((c.inputs, s.call.data, s.task.data, s.db.events))
    assert required.validate_manifest(c.cap.data) == c.cap.data
    output = execute(c)
    assert set(output) == {'context'} and _same(output['context'], c.original)
    assert output['context'] is not c.inputs['context']
    assert output['context']['producer']['invocation_id'] == s.call.id
    assert output['context']['permissions_verified'] is False
    assert output['context']['can_dispatch'] is False
    validate(required.output_schema(), output)
    assert (c.inputs, s.call.data, s.task.data, s.db.events) == before
    output['context']['gap']['fields'].clear()
    assert c.inputs['context']['gap']['fields'] and s.call.data['output']['resense_context']['gap']['fields']
    assert c.checks == ['actual-consumer-scope-boundary']


def test_null_is_a_visible_missing_selection_after_actual_task_scope_and_never_a_success(monkeypatch, producer_scene):
    c = consumer(monkeypatch, producer_scene, null=True)
    validate(required.input_schema(), c.inputs)
    def no_producer(*args, **kwargs): raise AssertionError('null must not invent or select a producer')
    monkeypatch.setattr(reader, 'checked_context', no_producer)
    with pytest.raises(HTTPException) as rejected: execute(c)
    assert rejected.value.status_code == 409
    assert rejected.value.detail == '缺少已选观察方案，请补充信息或确认选择'
    assert c.checks == ['actual-consumer-scope-boundary']
    with pytest.raises(HTTPException): validate(required.output_schema(), {'context': None})


@pytest.mark.parametrize('fault', ['missing_produced', 'missing_completed', 'unknown_call',
    'failed_producer_job', 'revoked_actor', 'changed_source'])
def test_hash_or_caller_context_is_not_enough_without_actual_original_provenance(monkeypatch, producer_scene, fault):
    c = consumer(monkeypatch, producer_scene); s = c.producer
    if fault == 'missing_produced': s.db.events.pop(0)
    elif fault == 'missing_completed': s.db.events.pop()
    elif fault == 'unknown_call': s.call.status = 'unknown'
    elif fault == 'failed_producer_job': s.job.status = 'failed'
    elif fault == 'revoked_actor': s.db.current_actor = False
    else: s.source.content_hash = 'f' * 64
    with pytest.raises(HTTPException): execute(c)


def test_completed_activation_with_running_producer_job_propagates_waiting_not_nonnull_success(monkeypatch, producer_scene):
    c = consumer(monkeypatch, producer_scene); s = c.producer
    s.job.status = 'running'
    with pytest.raises(reader.ProducerPending) as pending: execute(c)
    assert pending.value.job_id == s.job.id and pending.value.invocation_id == s.call.id
    assert s.call.status == 'succeeded' and s.activation['status'] == 'completed'


def test_resolved_original_request_is_checked_without_requesting_fresh_dispatch(monkeypatch, producer_scene):
    c = consumer(monkeypatch, producer_scene); s = c.producer
    s.gap.status = 'resolved'
    assert execute(c)['context'] == c.original
    with pytest.raises(HTTPException):
        reader.checked_context(s.db, s.p, c.original, task=s.task, dispatch=True)


@pytest.mark.parametrize('fault', ['job_input', 'consumer_role', 'top_extra', 'authority_flag', 'manifest_side_effect'])
def test_frozen_consumer_input_and_read_only_registration_remain_exact(monkeypatch, producer_scene, fault):
    c = consumer(monkeypatch, producer_scene)
    if fault == 'job_input': c.job.data['inputs'] = {'context': None}
    elif fault == 'consumer_role': c.node['execution']['agent_role'] = 'business'
    elif fault == 'top_extra': c.inputs['verified'] = True
    elif fault == 'authority_flag': c.inputs['context']['can_dispatch'] = True
    else: c.cap.data['execution']['side_effect'] = True
    with pytest.raises(HTTPException): execute(c)


def test_kernel_accepts_explicit_narrowing_but_still_rejects_nullable_direct_service_input():
    # This tests the actual native field/execution contracts, not a UI guess
    # that relabels nullable data as non-null. No Task or permission is created.
    assert kernel._field(required.input_schema(), 'context') == ('object', True)
    assert kernel._field(required.output_schema(), 'context') == ('object', False)
    assert kernel._field(service.input_schema(), 'context') == ('object', False)
    references = {'maybe_context': {'kind': 'object', 'nullable': True,
        'source': 'output', 'field': 'resense_context', 'node_id': 'original-reader'}}
    execution = {'capability_id': identity('narrowing-cap'), 'capability_hash': 'a' * 64,
        'agent_role': 'data', 'input_bindings': {'context': 'maybe_context'}, 'arguments': {},
        'input_schema': required.input_schema(), 'output_schema': required.output_schema(),
        'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 1}
    kernel._execution(execution, references)
    direct = {**copy.deepcopy(execution), 'input_schema': service.input_schema(),
        'output_schema': service.output_schema(), 'adapter': 'workflow_service',
        'agent_role': 'business', 'release_version': '2.0.0'}
    with pytest.raises(ValueError): kernel._execution(direct, references)
    direct['input_bindings'] = {'context': 'checked_context'}
    verified = {'checked_context': {'kind': 'object', 'nullable': False,
        'source': 'output', 'field': 'context', 'node_id': 'explicit-narrowing'}}
    kernel._execution(direct, verified)
