"""Local decision routing with explicit synthetic IO seams, not E2E evidence.

Real V274 cross-validation and the new planner run here. Runtime provenance,
reader IAM, PG transaction/lease behavior and V196 dispatch are separate gates;
spies are labelled and never represent a real model, Observation, ACK or Goal.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select

from generic_agents import business_decision_contracts_v274 as decision
from generic_agents import workflow_decision_tool_planner_v277 as subject
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, DataGap, Invocation, Job, Task


def ident(n): return '27700000-0000-4000-8000-' + format(n, '012x')


def context():
    delegation = {'task_id': ident(10)}
    request = {'gap_id': ident(34), 'query_id': ident(33), 'entity_id': ident(20), 'fields': ['temperature_c'],
        'query_request_hash': 'a' * 64, 'goal_delegation': delegation}
    value = {'schema': subject.reader.CONTEXT, 'scope': {'workspace_id': ident(1), 'task_id': ident(10),
        'instance_id': ident(30), 'release_id': ident(31), 'package_id': ident(32), 'business_id': 'public_simulation',
        'graph_hash': 'a' * 64, 'source_binding_hash': 'b' * 64, 'goal_snapshot_hash': 'c' * 64,
        'native_goal_context_hash': 'd' * 64}, 'query': {'query_id': ident(33), 'request_hash': 'a' * 64,
        'goal_delegation_hash': digest(delegation), 'version_at_read': 1, 'result_hash_at_read': 'c' * 64},
        'gap': {'gap_id': ident(34), 'entity_id': ident(20), 'version_at_read': 1, 'fields': ['temperature_c'],
            'observation_request': request, 'observation_request_hash': digest(request)},
        'policy': {'policy_id': ident(35), 'approved_hash': 'a' * 64, 'approved_by': ident(2),
            'version_at_read': 1, 'source_pins': [{'source_id': ident(36), 'source_hash': 'b' * 64}]},
        'producer': {'capability_id': ident(37), 'invocation_id': ident(38), 'job_id': ident(39), 'actor_id': ident(2),
            'capability_hash': 'a' * 64, 'activation_id': 'data_packet', 'node_id': 'data_packet',
            'cancel_epoch': 0, 'input_hash': 'b' * 64, 'request_key_hash': 'c' * 64},
        'permissions_verified': False, 'can_dispatch': False}
    return {**value, 'context_hash': digest(value)}


def base_manifest():
    return {'contract_version': '1.0', 'kind': 'tool', 'namespace': 'public_test',
        'name': 'public_decision_plan', 'version': '1.0.0', 'owner': 'public synthetic fixture',
        'description': 'Synthetic local planner test; no dispatch, observation or source authority.',
        'input_schema': {}, 'output_schema': {}, 'allowed_agent_roles': ['business'],
        'execution': {}, 'specification': {}, 'data_policy': {'cloud_allowed': False},
        'budget': {'max_attempts': 1, 'max_concurrency': 1}, 'dependencies': []}


def case(observed=False):
    resense_data = {**base_manifest(), 'version': subject.resense.RELEASE_VERSION,
        'input_schema': subject.resense.input_schema(), 'output_schema': subject.resense.output_schema(),
        'execution': {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': subject.resense.OP, 'side_effect': True}}
    target = {'capability_id': ident(21), 'capability_hash': digest(resense_data), 'release_version': '2.0.0'}
    manifest = {**base_manifest(), 'input_schema': subject.input_schema(), 'output_schema': subject.output_schema(),
        'execution': {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': subject.OP, 'side_effect': True},
        'specification': {'schema': subject.CONTRACT, 'resense_tool_pin': target}, 'dependencies': [target['capability_id']]}
    cap = NS(id=ident(4), data=manifest, content_hash=digest(manifest))
    source = {'source_ref': 'source_resense', 'kind': 'capability', 'source_id': ident(21),
        'content_hash': target['capability_hash'], 'text': 'The registered context service can request temperature.',
        'readability': 'readable'}
    requirement = {'requirement_ref': 'need_temperature', 'entity_id': ident(20), 'field': 'temperature_c',
        'value_type': 'number', 'unit': 'Cel', 'semantics': 'instantaneous', 'time_mode': 'bounded',
        'time_scope': {'start': '2026-11-01T11:59:00+00:00', 'end': '2026-11-01T12:01:00+00:00'},
        'required_kind': 'observation'}
    fact = {'fact_ref': 'fact_resense', 'kind': 'capability_declaration', 'entity_id': ident(20),
        'field': 'resense_contract', 'unit': None, 'semantics': 'statement',
        'time_scope': {'start': None, 'end': None}, 'value': 'can request temperature', 'source_refs': [{
            'source_ref': source['source_ref'], 'quote_start': 0, 'quote_end': len(source['text']), 'quote': source['text']}]}
    inputs = {'schema': decision.INPUT, 'decision_intent': 'information_acquisition',
        'scope': {'task_id': ident(10), 'activation_id': 'reasoner', 'goal_snapshot_hash': 'c' * 64, 'entity_ids': [ident(20)]},
        'requirements': [requirement], 'sources': [source], 'capabilities': [{'capability_ref': 'cap_resense',
            **target, 'input_schema_hash': digest(resense_data['input_schema']),
            'output_schema_hash': digest(resense_data['output_schema']), 'source_ref': 'source_resense',
            'action_kind': 'information_acquisition', 'output_ports': [], 'information_targets': [{
                'port_ref': 'port_temperature', 'pointer': '/requested_observation/temperature_c',
                'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel'}]}],
        'candidates': [{'candidate_ref': 'candidate_2', 'proposal': 'Request a simulated observation for review.',
            'capability_refs': ['cap_resense'], 'requirement_mappings': [{'requirement_ref': 'need_temperature',
                'capability_ref': 'cap_resense', 'delivery': 'requested_observation', 'port_ref': 'port_temperature'}],
            'claims': []}], 'facts': [fact], 'known_conflicts': []}
    model = {'schema': decision.MODEL, 'input_hash': decision.input_hash(inputs),
        'summary': 'A request proposal; no current measured value is asserted.', 'candidate_assessments': [{
            'candidate_ref': 'candidate_2', 'candidate_applicability': 'applicable',
            'applicability_reason': 'The requested field is declared.', 'evidence_integrity': 'supported',
            'evidence_reason': 'The capability registration supports a request.', 'capability_refs': ['cap_resense'],
            'requirement_refs': ['need_temperature'], 'unmet_requirement_refs': [], 'fact_refs': ['fact_resense'],
            'source_refs': ['source_resense'], 'refutations': [], 'missing_information': []}],
        'source_coverage': [{'source_ref': 'source_resense', 'status': 'reviewed', 'fact_refs': ['fact_resense'],
            'reason': 'The original declaration was reviewed.'}], 'conflicts': [],
        'knowledge_gaps': [{'requirement_ref': 'need_temperature', 'reason': 'No observation is present.'}],
        'recommendation': {'candidate_ref': 'candidate_2', 'reason': 'Request missing information for review.',
            'fact_refs': ['fact_resense']}}
    if observed:
        observation = {'source_ref': 'source_observation', 'kind': 'observation', 'source_id': ident(40),
            'content_hash': 'e' * 64, 'text': 'temperature_c=25', 'readability': 'readable'}
        inputs['sources'].append(observation)
        inputs['facts'].append({'fact_ref': 'fact_temperature', 'kind': 'observation', 'entity_id': ident(20),
            'field': 'temperature_c', 'unit': 'Cel', 'semantics': 'instantaneous',
            'time_scope': {'start': '2026-11-01T12:00:00+00:00', 'end': '2026-11-01T12:00:00+00:00'},
            'value': 25, 'source_refs': [{'source_ref': 'source_observation', 'quote_start': 0,
                'quote_end': len(observation['text']), 'quote': observation['text']}]})
        model['source_coverage'].append({'source_ref': 'source_observation', 'status': 'reviewed',
            'fact_refs': ['fact_temperature'], 'reason': 'The declared original observation was reviewed.'})
        model['candidate_assessments'][0]['fact_refs'].append('fact_temperature')
        model['candidate_assessments'][0]['source_refs'].append('source_observation')
        model['recommendation']['fact_refs'].append('fact_temperature'); model['knowledge_gaps'] = []
    model['input_hash'] = decision.input_hash(inputs)
    result = decision.derive_result(inputs, model)
    ctx = context()
    frame = {'decision_input': inputs, 'sources': {'data': {'packet': {'simulation_only': True},
        'context_result': {'candidates': [ctx]}}}, 'capability_pins': {'capabilities': [{
            'capability_ref': 'cap_resense', **target, 'data': resense_data,
            'delivery': 'requested_observation', 'action_kind': 'information_acquisition'}],
            'policies': [{'context_hash': ctx['context_hash'], 'context': ctx}]}}
    producer = {'schema': subject.PRODUCER, 'workspace_id': ident(1), 'task_id': ident(10), 'job_id': ident(11),
        'invocation_id': ident(12), 'actor_id': ident(3), 'capability_id': ident(13),
        'activation_id': 'reasoner', 'node_id': 'reasoner', **{key: 'a' * 64 for key in (
            'capability_hash', 'input_hash', 'output_hash', 'request_key_hash', 'capture_hash',
            'decision_input_hash', 'frame_hash', 'sources_hash')}}
    return NS(cap=cap, frame=frame, result=result, model=model, producer=producer, target=target, context=ctx)


def test_closed_schema_definitions_resolve_at_input_root_and_manifest_locks_service():
    s = case()
    Draft202012Validator.check_schema(subject.input_schema())
    Draft202012Validator(subject.input_schema()).validate({'decision_result': s.result})
    assert subject.validate_manifest(s.cap.data) == s.cap.data
    assert subject.input_schema()['additionalProperties'] is False
    assert 'reasoning_result' not in subject.input_schema()['properties']


def test_missing_observation_routes_original_context_to_original_resense_without_dispatch():
    s = case(); before = copy.deepcopy((s.result, s.frame))
    assert subject.reader._check_context(s.context) == s.context
    value = subject._route(s.cap, s.result, s.frame, s.producer)
    assert value['route'] == 'acquire' and value['acquisition_required'] and not value['no_gap']
    assert value['resense_context'] == s.context and value['resense_capability'] == s.target
    assert value['missing_requirement_refs'] == ['need_temperature'] and value['simulation_only'] is True
    assert not any(value[key] for key in ('can_dispatch', 'permissions_verified', 'physical_state_verified', 'independent_acceptance'))
    assert (s.result, s.frame) == before


def test_current_facts_sufficient_routes_continue_without_context_or_resense():
    s = case(observed=True)
    s.frame['sources']['data']['context_result']['candidates'] = []
    s.frame['capability_pins']['policies'] = []
    value = subject._route(s.cap, s.result, s.frame, s.producer)
    assert value['route'] == 'continue' and value['no_gap'] and not value['acquisition_required']
    assert value['resense_context'] is None and value['resense_capability'] is None
    assert value['missing_requirement_refs'] == []


def test_candidate_identity_is_not_a_whitelist_or_forced_candidate_two():
    s = case(); candidate_ref = 'candidate_alpha'
    s.frame['decision_input']['candidates'][0]['candidate_ref'] = candidate_ref
    s.model['candidate_assessments'][0]['candidate_ref'] = candidate_ref
    s.model['recommendation']['candidate_ref'] = candidate_ref
    s.model['input_hash'] = decision.input_hash(s.frame['decision_input'])
    result = decision.derive_result(s.frame['decision_input'], s.model)
    assert subject._route(s.cap, result, s.frame, s.producer)['selected_candidate_ref'] == candidate_ref


def test_acquire_does_not_silently_omit_another_selected_capability():
    s = case(); inputs = s.frame['decision_input']
    source = copy.deepcopy(inputs['sources'][0]); source.update(source_ref='source_health', source_id=ident(91), text='Health declaration.')
    inputs['sources'].append(source)
    cap = copy.deepcopy(inputs['capabilities'][0]); cap.update(capability_ref='cap_health', capability_id=ident(91),
        source_ref='source_health', information_targets=[])
    inputs['capabilities'].append(cap); inputs['candidates'][0]['capability_refs'].append('cap_health')
    fact = copy.deepcopy(inputs['facts'][0]); fact.update(fact_ref='fact_health', field='health_contract', value='health only',
        source_refs=[{'source_ref': 'source_health', 'quote_start': 0, 'quote_end': len(source['text']), 'quote': source['text']}])
    inputs['facts'].append(fact)
    row = s.model['candidate_assessments'][0]; row['capability_refs'].append('cap_health')
    row['fact_refs'].append('fact_health'); row['source_refs'].append('source_health')
    s.model['source_coverage'].append({'source_ref': 'source_health', 'status': 'reviewed',
        'fact_refs': ['fact_health'], 'reason': 'The other selected capability was also reviewed.'})
    s.model['input_hash'] = decision.input_hash(inputs); result = decision.derive_result(inputs, s.model)
    with pytest.raises(HTTPException):
        subject._route(s.cap, result, s.frame, s.producer)


def test_no_missing_fields_but_unknown_global_facts_do_not_create_a_resense_gap():
    s = case(observed=True); inputs = s.frame['decision_input']
    source = {'source_ref': 'source_uncertain', 'kind': 'packet', 'source_id': ident(90),
        'content_hash': 'f' * 64, 'text': 'unknown contextual status', 'readability': 'readable'}
    inputs['sources'].append(source)
    inputs['facts'].append({'fact_ref': 'fact_uncertain', 'kind': 'document_statement', 'entity_id': ident(20),
        'field': 'other_status', 'unit': None, 'semantics': 'unknown', 'time_scope': {'start': None, 'end': None},
        'value': 'unknown contextual status', 'source_refs': [{'source_ref': source['source_ref'],
            'quote_start': 0, 'quote_end': len(source['text']), 'quote': source['text']}]})
    s.model['source_coverage'].append({'source_ref': source['source_ref'], 'status': 'reviewed',
        'fact_refs': ['fact_uncertain'], 'reason': 'The uncertainty is retained.'})
    s.model['input_hash'] = decision.input_hash(inputs); result = decision.derive_result(inputs, s.model)
    assert result['missing_requirement_refs'] == [] and result['fact_consistency'] == 'unknown'
    with pytest.raises(HTTPException):
        subject._route(s.cap, result, s.frame, s.producer)


@pytest.mark.parametrize('damage', ['no_context', 'ambiguous_context', 'wrong_entity', 'missing_field',
    'no_policy_proof', 'other_resense_pin', 'telemetry', 'extra_property', 'fake_ready', 'legacy_result'])
def test_acquisition_requires_real_selected_resense_and_exact_original_context(damage):
    s = case()
    if damage == 'no_context': s.frame['sources']['data']['context_result']['candidates'] = []
    elif damage == 'ambiguous_context': s.frame['sources']['data']['context_result']['candidates'].append(copy.deepcopy(s.context))
    elif damage == 'wrong_entity': s.frame['sources']['data']['context_result']['candidates'][0]['gap']['entity_id'] = ident(99)
    elif damage == 'missing_field': s.frame['sources']['data']['context_result']['candidates'][0]['gap']['fields'] = []
    elif damage == 'no_policy_proof': s.frame['capability_pins']['policies'] = []
    elif damage == 'other_resense_pin': s.cap.data['specification']['resense_tool_pin']['capability_id'] = ident(99)
    elif damage == 'telemetry': s.frame['capability_pins']['capabilities'][0]['data']['execution']['adapter'] = 'http'
    elif damage == 'extra_property': s.result['can_dispatch'] = True
    elif damage == 'fake_ready': s.result['current_facts_sufficient'] = True
    else: s.result = {'schema': 'business-reasoning-result-v197'}
    with pytest.raises((HTTPException, ValueError, KeyError)):
        subject._route(s.cap, s.result, s.frame, s.producer)


@pytest.mark.parametrize('intent', ['operational_action', 'business_conclusion'])
def test_unsupported_intent_is_blocked_even_when_pure_result_is_ready(intent):
    s = case(observed=True); inputs = s.frame['decision_input']; inputs['decision_intent'] = intent
    inputs['capabilities'][0]['action_kind'] = 'operational_action' if intent == 'operational_action' else 'analysis'
    inputs['capabilities'][0]['output_ports'] = inputs['capabilities'][0]['information_targets']
    inputs['capabilities'][0]['information_targets'] = []
    inputs['candidates'][0]['requirement_mappings'][0]['delivery'] = 'output'
    s.model['input_hash'] = decision.input_hash(inputs); result = decision.derive_result(inputs, s.model)
    assert result['review_state'] == 'ready_for_review'
    with pytest.raises(HTTPException, match='尚未实现'):
        subject._route(s.cap, result, s.frame, s.producer)


@pytest.mark.parametrize('status', ['resolved', 'cancelled', 'needs_review', 'pending_delivery'])
def test_current_gap_cannot_trigger_duplicate_or_pending_acquisition(monkeypatch, status):
    s = case(); current = {'task': NS(id=ident(10)), 'job': NS(), 'capability': s.cap}
    monkeypatch.setattr(subject, '_producer', lambda *a: (s.producer, s.frame))
    monkeypatch.setattr(subject.reader, 'checked_context', lambda *a, **k: copy.deepcopy(s.context))
    monkeypatch.setattr(subject.sources, 'fresh', lambda db, p, model, identity: NS(status=status)
        if model is DataGap else pytest.fail('blocked Gap reached a target'))
    with pytest.raises(HTTPException):
        subject._plan(None, NS(), current, {'decision_result': s.result})


def test_no_gap_local_plan_never_reads_gap_policy_or_target(monkeypatch):
    s = case(observed=True); current = {'task': NS(id=ident(10)), 'job': NS(), 'capability': s.cap}
    monkeypatch.setattr(subject, '_producer', lambda *a: (s.producer, s.frame))
    monkeypatch.setattr(subject.sources, 'fresh', lambda *a: pytest.fail('continue read a current Gap or target'))
    monkeypatch.setattr(subject.reader, 'checked_context', lambda *a, **k: pytest.fail('continue checked a resense context'))
    assert subject._plan(None, NS(), current, {'decision_result': s.result})['route'] == 'continue'


@pytest.mark.parametrize('damage', [None, 'wrong_status', 'missing_record', 'record_hash', 'input', 'capability', 'job'])
def test_retained_local_plan_is_only_a_same_record_reconciliation_hint(monkeypatch, damage):
    s = case(); local = local_call(monkeypatch, s)
    p, db, job, call, inputs, approval, events = (local.p, local.db, local.job, local.call,
        local.inputs, local.approval, local.events)
    subject.execute(db, p, s.cap, inputs, 'original', approval)
    call.status = 'unknown'
    call.data = {**call.data, 'capability_snapshot': copy.deepcopy(s.cap.data), 'job_id': job.id}
    if damage == 'wrong_status': call.status = 'running'
    elif damage == 'missing_record': call.data.pop(subject.KEY)
    elif damage == 'record_hash': call.data[subject.KEY]['record_hash'] = '0' * 64
    elif damage == 'input': call.data['inputs'] = {'forged': True}
    elif damage == 'capability': call.capability_id = ident(99)
    elif damage == 'job': call.data['job_id'] = ident(99)
    before = copy.deepcopy(call.data)
    assert subject.retained_marker_shape(call) is (damage is None)
    from generic_agents.workflow_service_catalog_v197 import monitoring_receipt_retained
    assert monitoring_receipt_retained(call) is (damage is None)
    assert call.data == before and len(events) == 1


def local_call(monkeypatch, s):
    p = NS(workspace_id=ident(1), user_id=ident(2)); inputs = {'decision_result': s.result}; approval = {'original': 'fixture'}
    task = NS(id=ident(10)); job = NS(id=ident(50), data={'actor_id': p.user_id})
    call = NS(id=ident(51), workspace_id=p.workspace_id, capability_id=s.cap.id, data={
        'capability_hash': s.cap.content_hash, 'input_hash': digest(inputs), 'inputs': inputs, 'approval': approval})
    current = {'task': task, 'job': job, 'capability': s.cap}
    monkeypatch.setattr(subject, 'validate_call_approval', lambda *a: current)
    monkeypatch.setattr(subject.service, '_call', lambda *a, **k: call)
    monkeypatch.setattr(subject, '_plan', lambda *a: subject._route(s.cap, s.result, s.frame, s.producer))
    events = []
    def same_sql(query): return str(query.compile(compile_kwargs={'literal_binds': True}))
    expected_sql = same_sql(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == subject.CREATED, Audit.object_id == call.id).limit(2))
    class IO:
        def scalars(self, query):
            assert same_sql(query) == expected_sql, 'unknown SQL is rejected'
            return events
        def flush(self): pass
        def commit(self): pytest.fail('local planner changed transaction ownership')
    def save(db, actual_p, action, object_id, data):
        assert actual_p is p and action == subject.CREATED and object_id == call.id
        events.append(NS(actor_id=p.user_id, data=copy.deepcopy(data)))
    monkeypatch.setattr(subject, 'audit', save)
    return NS(p=p, inputs=inputs, approval=approval, task=task, job=job, call=call, db=IO(), events=events)


def test_execute_records_one_local_plan_and_reconcile_never_builds_another(monkeypatch):
    s = case(); local = local_call(monkeypatch, s)
    output = subject.execute(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval)
    assert output['route'] == 'acquire' and len(local.events) == 1
    monkeypatch.setattr(subject, '_plan', lambda *a: pytest.fail('existing plan recomputed current routing'))
    assert subject.execute(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval) == output
    assert subject.reconcile(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval, local.call) == output
    assert len(local.events) == 1


@pytest.mark.parametrize('damage', ['missing_record', 'missing_audit', 'actor', 'record', 'output'])
def test_local_plan_and_independent_audit_cannot_be_recreated_or_forged(monkeypatch, damage):
    s = case(); local = local_call(monkeypatch, s)
    subject.execute(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval)
    if damage == 'missing_record': local.call.data.pop(subject.KEY)
    elif damage == 'missing_audit': local.events.clear()
    elif damage == 'actor': local.events[0].actor_id = ident(99)
    elif damage == 'record': local.call.data[subject.KEY]['job_id'] = ident(99)
    else: local.call.data[subject.KEY]['output']['can_dispatch'] = True
    with pytest.raises(HTTPException):
        subject.reconcile(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval, local.call)


def producer_fixture(monkeypatch):
    """Explicit native-scope/runtime proof spies, retaining the real consumer checks."""
    s = case(); p = NS(workspace_id=ident(1), user_id=ident(2)); original_actor = ident(3)
    original_inputs = {'server_frozen': 'synthetic boundary'}
    reasoner_data = {'execution': {'adapter': subject.reasoning.ADAPTER}}
    cap = NS(id=ident(13), data=reasoner_data, content_hash=digest(reasoner_data))
    job = NS(id=ident(11), status='succeeded', data={'task_id': ident(10), 'activation_id': 'reasoner',
        'actor_id': original_actor, 'inputs': original_inputs, 'invocation_key': 'original-paid-key'})
    call = NS(id=ident(12), workspace_id=p.workspace_id, status='succeeded', capability_id=cap.id,
        idempotency_key=job.data['invocation_key'], data={'capability_hash': cap.content_hash,
            'input_hash': digest(original_inputs), 'job_id': job.id, 'lease_token': 'original-lease',
            'role': 'business', 'approval': None, 'side_effect': False, 'output': copy.deepcopy(s.result)})
    node = {'id': 'reasoner', 'execution': {'adapter': subject.reasoning.ADAPTER, 'agent_role': 'business',
        'capability_id': cap.id, 'capability_hash': cap.content_hash}}
    activation = {'id': 'reasoner', 'status': 'completed', 'epoch': 0, 'output': copy.deepcopy(s.result)}
    current = {'id': 'planner', 'epoch': 0, 'inputs_snapshot': {'bindings': {'decision_result': {
        'missing': False, 'producer_activation_id': 'reasoner', 'value_hash': digest(s.result)}}}}
    state = {'activations': {'reasoner': activation, 'planner': current}}
    owner = {'job_id': job.id, 'lease_token': 'original-lease'}
    transport = {'job_id': job.id, 'invocation_id': call.id, 'dispatch_ownership': owner,
        'dispatch_authorization': {'job_id': job.id, 'lease_token': owner['lease_token'], 'actor_id': original_actor,
            'epoch': 0, 'input_hash': digest(original_inputs), 'capability_hash': cap.content_hash},
        'phase': 'succeeded', 'dispatch_committed': True, 'output_hash': digest(s.result)}
    task = NS(id=ident(10), data={'graph_runtime': state, 'graph_jobs': {'reasoner': transport}})
    consumer = NS(data={'activation_id': 'planner'})
    def frozen(db, actual_p, task_id, activation_id):
        assert actual_p is p and task_id == task.id
        return task, None, None, None, state, state['activations'][activation_id], node
    monkeypatch.setattr(subject.scope, '_frozen', frozen)
    matched = []
    monkeypatch.setattr(subject.scope, '_job_matches', lambda *a: matched.append(a) or transport)
    rows = {(Job, job.id): job, (Invocation, call.id): call, (Capability, cap.id): cap}
    monkeypatch.setattr(subject.sources, 'fresh', lambda db, p, model, identity: rows[(model, identity)])
    seen = []
    def saved(db, actual_p, actual_task, actual_job, actual_call, actual_cap, inputs):
        assert actual_p is p and p.user_id != job.data['actor_id']
        assert (actual_task, actual_job, actual_call, actual_cap, inputs) == (task, job, call, cap, original_inputs)
        seen.append(actual_p.user_id)
        return copy.deepcopy(s.result)
    monkeypatch.setattr(subject.reasoning, 'saved_result', saved)
    s.frame['sources']['sources_hash'] = 'a' * 64
    frozen_state = {'request': {'frame': s.frame, 'frame_hash': digest(s.frame)},
        'capture': {'raw_sha256': 'b' * 64}}
    def checked_state(db, actual_p, actual_cap, inputs, key, actual_call, *, actor_id):
        assert actual_p is p and actor_id == original_actor and key == job.data['invocation_key']
        return copy.deepcopy(frozen_state)
    monkeypatch.setattr(subject.reasoning, '_checked_state_for_actor', checked_state)
    from generic_agents.business_reasoning_protocols_v276 import recovery_record
    marker, recovery_action = recovery_record(subject.reasoning.ADAPTER)
    events = {'capability.invoke': [NS(actor_id=original_actor, data={'status': 'succeeded'})], recovery_action: []}
    def same_sql(query): return str(query.compile(compile_kwargs={'literal_binds': True}))
    queries = {same_sql(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == call.id).limit(2)): action for action in events}
    for action, field, selected, data in [
        ('workflow.graph.node_completed', 'activation_id', activation['id'],
            {'activation_id': activation['id'], 'invocation_id': call.id}),
        ('workflow.graph.dispatch_authorized', 'job_id', job.id, {'activation_id': activation['id'],
            'job_id': job.id, 'epoch': activation['epoch'], 'input_hash': digest(original_inputs)})]:
        events[action] = [NS(actor_id=original_actor, data=data)]
        queries[same_sql(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == action,
            Audit.object_id == task.id, Audit.data[field].as_string() == selected).limit(2))] = action
    class IO:
        def scalars(self, query):
            assert same_sql(query) in queries, 'unknown SQL is rejected'
            return events[queries[same_sql(query)]]
        def commit(self): pytest.fail('producer proof wrote storage')
    return NS(case=s, p=p, task=task, consumer=consumer, job=job, call=call, cap=cap, node=node,
        activation=activation, current=current, transport=transport, events=events,
        marker=marker, recovery_action=recovery_action, seen=seen, db=IO())


def test_producer_resolves_exact_native_edge_and_keeps_current_reader(monkeypatch):
    s = producer_fixture(monkeypatch)
    pin, frame = subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)
    assert s.seen == [s.p.user_id] and pin['actor_id'] == s.job.data['actor_id'] != s.p.user_id
    assert pin['job_id'] == s.job.id and pin['invocation_id'] == s.call.id and frame == s.case.frame


@pytest.mark.parametrize('damage', ['edge', 'value_hash', 'output', 'adapter', 'call_role',
    'approval', 'input_hash', 'lease', 'dispatch_actor', 'invoke_audit', 'completion_audit', 'saved_result'])
def test_producer_never_searches_equal_results_or_accepts_forged_origin(monkeypatch, damage):
    s = producer_fixture(monkeypatch)
    if damage == 'edge': s.current['inputs_snapshot']['bindings'].clear()
    elif damage == 'value_hash': s.current['inputs_snapshot']['bindings']['decision_result']['value_hash'] = '0' * 64
    elif damage == 'output': s.activation['output'] = {}
    elif damage == 'adapter': s.cap.data['execution']['adapter'] = 'business_reasoning'
    elif damage == 'call_role': s.call.data['role'] = 'perception'
    elif damage == 'approval': s.call.data['approval'] = {'forged': True}
    elif damage == 'input_hash': s.call.data['input_hash'] = '0' * 64
    elif damage == 'lease': s.call.data['lease_token'] = 'other-lease'
    elif damage == 'dispatch_actor': s.transport['dispatch_authorization']['actor_id'] = ident(99)
    elif damage == 'invoke_audit': s.events['capability.invoke'] = []
    elif damage == 'completion_audit': s.events['workflow.graph.node_completed'] = []
    else: monkeypatch.setattr(subject.reasoning, 'saved_result', lambda *a: {'forged': True})
    with pytest.raises(HTTPException):
        subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)


def test_unfinished_original_job_waits_by_original_numbers_before_consumer_invocation(monkeypatch):
    s = producer_fixture(monkeypatch); s.job.status = 'running'
    monkeypatch.setattr(subject.reasoning, 'saved_result', lambda *a: pytest.fail('unfinished Job was adopted'))
    with pytest.raises(subject.reader.ProducerPending) as raised:
        subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)
    assert raised.value.job_id == s.job.id and raised.value.invocation_id == s.call.id


@pytest.mark.parametrize('original_status', [None, 'failed'])
def test_only_real_same_answer_recovery_audit_can_replace_missing_original_success(monkeypatch, original_status):
    s = producer_fixture(monkeypatch)
    s.events['capability.invoke'] = [] if original_status is None else [NS(actor_id=s.job.data['actor_id'], data={'status': original_status})]
    previous = original_status or 'running'
    s.call.data[s.marker] = {'previous_status': previous, 'output_hash': digest(s.case.result), 'recovered_at': 'original-server-time'}
    proof = {'job_id': s.job.id, 'activation_id': s.activation['id'], 'previous_status': previous,
        'output_hash': digest(s.case.result)}
    s.events[s.recovery_action] = [NS(actor_id=s.job.data['actor_id'], data=proof)]
    assert subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)[0]['job_id'] == s.job.id
    s.events[s.recovery_action] = []
    with pytest.raises(HTTPException):
        subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)
