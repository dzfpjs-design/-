"""Local decision routing with explicit synthetic IO seams, not E2E evidence.

V284 cross-validation and V285 routing are exercised against synthetic boundaries. Runtime provenance,
reader IAM, PG transaction/lease behavior and V196 dispatch are separate gates;
spies are labelled and never represent a real model, Observation, ACK or Goal.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select

from generic_agents import business_decision_contracts_v284 as decision
from generic_agents import workflow_decision_tool_planner_v285 as subject
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, DataGap, Invocation, Job, Task


def ident(n): return '28500000-0000-4000-8000-' + format(n, '012x')


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
    model = {'schema': decision.MODEL, 'candidate_reviews': [{
        'candidate_ref': 'candidate_2', 'suitability': 'suitable',
        'reasoning': 'The original registered capability can request the declared field.',
        'supporting_fact_refs': ['fact_resense'], 'blocking_conditions': []}],
        'source_reviews': [{'source_ref': 'source_resense', 'assessment': 'reviewed',
            'reason': 'The declaration was reviewed.'}], 'conflicts': [],
        'recommendation': {'candidate_ref': 'candidate_2', 'reason': 'Obtain missing information for review.',
            'supporting_fact_refs': ['fact_resense']}}
    if observed:
        observation = {'source_ref': 'source_observation', 'kind': 'observation', 'source_id': ident(40),
            'content_hash': 'e' * 64, 'text': 'temperature_c=25', 'readability': 'readable'}
        inputs['sources'].append(observation)
        inputs['facts'].append({'fact_ref': 'fact_temperature', 'kind': 'observation', 'entity_id': ident(20),
            'field': 'temperature_c', 'unit': 'Cel', 'semantics': 'instantaneous',
            'time_scope': {'start': '2026-11-01T12:00:00+00:00', 'end': '2026-11-01T12:00:00+00:00'},
            'value': 25, 'source_refs': [{'source_ref': 'source_observation', 'quote_start': 0,
                'quote_end': len(observation['text']), 'quote': observation['text']}]})
        model['source_reviews'].append({'source_ref': 'source_observation', 'assessment': 'reviewed',
            'reason': 'The original received observation was reviewed at this pure boundary.'})
        model['candidate_reviews'][0]['supporting_fact_refs'].append('fact_temperature')
        model['recommendation']['supporting_fact_refs'].append('fact_temperature')
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




def producer_fixture(monkeypatch):
    """Explicit native-scope/runtime proof spies, retaining the real consumer checks."""
    s = case(); p = NS(workspace_id=ident(1), user_id=ident(2)); original_actor = ident(3)
    original_inputs = {'server_frozen': 'synthetic boundary'}
    reasoner_data = {'kind': 'tool', 'version': '1.0.0', 'execution': {'adapter': subject.reasoning.ADAPTER},
        'input_schema': {'type': 'object'}, 'output_schema': decision.output_schema()}
    cap = NS(id=ident(13), kind='tool', data=reasoner_data, content_hash=digest(reasoner_data),
        validated_hash=digest(reasoner_data), release_version='1.0.0')
    monkeypatch.setattr(subject.reasoning, '_manifest_shape', lambda value: value)  # explicit manifest boundary spy
    job = NS(id=ident(11), kind=subject.scope.KIND, status='succeeded', data={'task_id': ident(10), 'activation_id': 'reasoner',
        'actor_id': original_actor, 'inputs': original_inputs, 'invocation_key': 'original-paid-key'})
    call = NS(id=ident(12), workspace_id=p.workspace_id, status='succeeded', capability_id=cap.id,
        idempotency_key=job.data['invocation_key'], data={'capability_hash': cap.content_hash,
            'input_hash': digest(original_inputs), 'job_id': job.id, 'lease_token': 'original-lease',
            'role': 'business', 'approval': None, 'side_effect': False, 'inputs': copy.deepcopy(original_inputs), 'output': copy.deepcopy(s.result)})
    node = {'id': 'reasoner', 'actor_id': original_actor, 'execution': {'adapter': subject.reasoning.ADAPTER, 'agent_role': 'business',
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'release_version': cap.release_version,
        'input_schema': copy.deepcopy(cap.data['input_schema']), 'output_schema': copy.deepcopy(cap.data['output_schema'])}}
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


def derived(s):
    s.result = decision.derive_result(s.frame['decision_input'], s.model)
    return s.result


def routed(s): return subject._route(s.cap, s.result, s.frame, s.producer)


def no_rights(value):
    assert not any(value[key] for key in ('can_dispatch', 'permissions_verified',
        'physical_state_verified', 'independent_acceptance'))
    assert value['no_gap'] is (not bool(value['missing_requirement_refs']))
    if value['route'] != 'acquire':
        assert value['resense_context'] is value['resense_capability'] is None
        assert value['acquisition_required'] is False


def conflicted(s):
    assert any(row['fact_ref'] == 'fact_temperature' for row in s.frame['decision_input']['facts'])
    inputs = s.frame['decision_input']
    source = {'source_ref': 'source_other', 'kind': 'observation', 'source_id': ident(41),
        'content_hash': 'f' * 64, 'text': 'temperature_c=29', 'readability': 'readable'}
    inputs['sources'].append(source)
    other = copy.deepcopy(inputs['facts'][-1])
    other.update(fact_ref='fact_other', value=29, source_refs=[{'source_ref': source['source_ref'],
        'quote_start': 0, 'quote_end': len(source['text']), 'quote': source['text']}])
    inputs['facts'].append(other)
    s.model['source_reviews'].append({'source_ref': source['source_ref'], 'assessment': 'reviewed',
        'reason': 'The contrary original observation remains visible.'})
    conflict = {'conflict_ref': 'conflict_temperature', 'fact_refs': ['fact_temperature', 'fact_other'],
        'reason': 'Different original values at the same declared time.'}
    inputs['known_conflicts'] = [copy.deepcopy(conflict)]; s.model['conflicts'] = [conflict]
    derived(s)
    return s


def test_manifest_and_closed_route_schema_use_only_v284_input_and_v285_service():
    s = case()
    for schema in (subject.input_schema(), subject.output_schema()): Draft202012Validator.check_schema(schema)
    Draft202012Validator(subject.input_schema()).validate({'decision_result': s.result})
    assert subject.validate_manifest(s.cap.data) == s.cap.data
    assert subject.OP == 'plan_business_decision_review_v285'
    assert subject.input_schema()['additionalProperties'] is False
    old = copy.deepcopy(s.cap.data); old['execution']['operation'] = 'plan_business_decision_v277'
    with pytest.raises(HTTPException): subject.validate_manifest(old)


@pytest.mark.parametrize('observed,route,reason', [(False, 'acquire', 'acquisition_ready'),
    (True, 'continue', 'current_facts_ready')])
def test_acquire_and_continue_keep_original_context_and_have_no_issued_rights(observed, route, reason):
    s = case(observed); before = copy.deepcopy((s.result, s.frame))
    value = routed(s)
    assert value['route'] == route and value['reason_code'] == reason
    assert value['selected_candidate_ref'] == 'candidate_2' and value['conflict_refs'] == []
    if not observed:
        assert value['resense_context'] == s.context and value['resense_capability'] == s.target
        assert value['acquisition_required'] is True
        assert not any(row['kind'] == 'observation' for row in s.frame['decision_input']['facts'])
    no_rights(value)
    assert (s.result, s.frame) == before


@pytest.mark.parametrize('observed,route,reason', [(False, 'review_required', 'facts_missing'),
    (True, 'no_action', 'no_recommendation')])
def test_null_recommendation_is_not_forced_into_a_candidate_or_automatically_sufficient(observed, route, reason):
    s = case(observed); s.model['recommendation'].update(candidate_ref=None, supporting_fact_refs=[]); derived(s)
    value = routed(s)
    assert value['route'] == route and value['reason_code'] == reason
    assert value['selected_candidate_ref'] is None
    no_rights(value)


def test_conflict_is_retained_before_other_routes_even_if_a_candidate_remains_selected():
    s = conflicted(case(True)); before = copy.deepcopy(s.result)
    value = routed(s)
    assert value['route'] == 'review_required' and value['reason_code'] == 'conflicting_evidence'
    assert value['conflict_refs'] == ['conflict_temperature'] and value['no_gap'] is True
    assert s.result == before and s.result['model_value']['recommendation']['candidate_ref'] == 'candidate_2'
    no_rights(value)


@pytest.mark.parametrize('damage', ['uncertain', 'unsuitable', 'blocked', 'unsupported', 'recommendation_mismatch'])
def test_valid_unexecutable_model_selection_is_saved_as_review_without_changing_its_opinion(damage):
    s = case(); row = s.model['candidate_reviews'][0]
    if damage in {'uncertain', 'unsuitable'}: row['suitability'] = damage
    elif damage == 'blocked': row['blocking_conditions'] = ['An original prerequisite remains unresolved.']
    elif damage == 'unsupported': row['supporting_fact_refs'] = []
    else: s.model['recommendation']['supporting_fact_refs'] = []
    derived(s); before = copy.deepcopy(s.result)
    value = routed(s)
    assert value['route'] == 'review_required' and value['reason_code'] == 'selection_needs_review'
    assert s.result == before and value['selected_candidate_ref'] == 'candidate_2'
    no_rights(value)


@pytest.mark.parametrize('selected', [False, True])
def test_incomplete_source_review_cannot_turn_into_no_action_or_current_facts(selected):
    s = case(True); s.model['source_reviews'][0]['assessment'] = 'incomparable'
    if not selected: s.model['recommendation'].update(candidate_ref=None, supporting_fact_refs=[])
    derived(s); value = routed(s)
    assert value['route'] == 'review_required' and value['reason_code'] == 'source_review_incomplete'
    no_rights(value)


@pytest.mark.parametrize('intent', ['operational_action', 'business_conclusion'])
def test_other_intents_remain_explicit_backlog_even_with_complete_current_facts(intent):
    s = case(True); inputs = s.frame['decision_input']; inputs['decision_intent'] = intent
    cap = inputs['capabilities'][0]
    cap['action_kind'] = 'operational_action' if intent == 'operational_action' else 'analysis'
    cap['output_ports'] = cap['information_targets']; cap['information_targets'] = []
    inputs['candidates'][0]['requirement_mappings'][0]['delivery'] = 'output'
    derived(s); value = routed(s)
    assert value['route'] == 'review_required' and value['reason_code'] == 'unsupported_decision_intent'
    no_rights(value)


@pytest.mark.parametrize('damage', ['no_context', 'ambiguous_context', 'wrong_entity', 'missing_field',
    'no_policy_proof', 'other_target'])
def test_supported_but_unavailable_acquisition_scope_has_a_nonexecuting_route(damage):
    s = case()
    if damage == 'no_context': s.frame['sources']['data']['context_result']['candidates'] = []
    elif damage == 'ambiguous_context': s.frame['sources']['data']['context_result']['candidates'].append(copy.deepcopy(s.context))
    elif damage == 'wrong_entity': s.frame['sources']['data']['context_result']['candidates'][0]['gap']['entity_id'] = ident(99)
    elif damage == 'missing_field': s.frame['sources']['data']['context_result']['candidates'][0]['gap']['fields'] = []
    elif damage == 'no_policy_proof': s.frame['capability_pins']['policies'] = []
    else: s.cap.data['specification']['resense_tool_pin']['capability_id'] = ident(99)
    value = routed(s)
    assert value['route'] == 'review_required' and value['reason_code'] == 'unsupported_acquisition_scope'
    no_rights(value)


def test_mixed_candidate_capabilities_cannot_be_silently_partially_executed():
    s = case(); inputs = s.frame['decision_input']
    source = copy.deepcopy(inputs['sources'][0]); source.update(source_ref='source_health', source_id=ident(91), text='Health only.')
    inputs['sources'].append(source)
    cap = copy.deepcopy(inputs['capabilities'][0]); cap.update(capability_ref='cap_health', capability_id=ident(91),
        source_ref='source_health', information_targets=[])
    inputs['capabilities'].append(cap); inputs['candidates'][0]['capability_refs'].append('cap_health')
    fact = copy.deepcopy(inputs['facts'][0]); fact.update(fact_ref='fact_health', field='health_contract', value='health only',
        source_refs=[{'source_ref': source['source_ref'], 'quote_start': 0, 'quote_end': len(source['text']), 'quote': source['text']}])
    inputs['facts'].append(fact)
    s.model['candidate_reviews'][0]['supporting_fact_refs'].append('fact_health')
    s.model['recommendation']['supporting_fact_refs'].append('fact_health')
    s.model['source_reviews'].append({'source_ref': source['source_ref'], 'assessment': 'reviewed', 'reason': 'Read the other original capability.'})
    derived(s); value = routed(s)
    assert value['route'] == 'review_required' and value['reason_code'] == 'unsupported_acquisition_scope'
    no_rights(value)


@pytest.mark.parametrize('damage', ['legacy_result', 'result_hash', 'scope', 'authority', 'unknown_fact', 'mechanical_coverage'])
def test_tampered_result_refs_or_legacy_schema_are_errors_not_successful_review_routes(damage):
    s = case()
    if damage == 'legacy_result': s.result['schema'] = 'business-decision-result-v274'
    elif damage == 'result_hash': s.result['input_hash'] = '0' * 64
    elif damage == 'scope': s.result['scope']['task_id'] = ident(99)
    elif damage == 'authority': s.result['can_dispatch'] = True
    elif damage == 'unknown_fact': s.result['model_value']['recommendation']['supporting_fact_refs'] = ['invented_fact']
    else: s.result['mechanical_review']['candidates'][0]['covered_requirement_refs'] = []
    with pytest.raises(HTTPException): routed(s)


@pytest.mark.parametrize('status', ['resolved', 'cancelled', 'needs_review', 'pending_delivery'])
def test_only_acquire_rechecks_the_actual_current_gap_and_rejects_changed_status(monkeypatch, status):
    s = case(); current = {'task': NS(id=ident(10)), 'job': NS(), 'capability': s.cap}
    monkeypatch.setattr(subject, '_producer', lambda *a: (s.producer, s.frame))
    monkeypatch.setattr(subject.reader, 'checked_context', lambda *a, **k: copy.deepcopy(s.context))
    monkeypatch.setattr(subject.sources, 'fresh', lambda db, p, model, identity: NS(status=status)
        if model is DataGap else pytest.fail('blocked Gap reached a current target'))
    with pytest.raises(HTTPException): subject._plan(None, NS(), current, {'decision_result': s.result})


def test_current_policy_context_change_cannot_reuse_a_cached_frame_permission(monkeypatch):
    s = case(); current = {'task': NS(id=ident(10)), 'job': NS(), 'capability': s.cap}
    monkeypatch.setattr(subject, '_producer', lambda *a: (s.producer, s.frame))
    monkeypatch.setattr(subject.reader, 'checked_context', lambda *a, **k: subject.need(False, 'current policy changed'))
    with pytest.raises(HTTPException, match='current policy changed'):
        subject._plan(None, NS(), current, {'decision_result': s.result})


@pytest.mark.parametrize('damage', [None, 'status', 'hash', 'version'])
def test_acquire_rechecks_the_same_published_resense_version_after_live_context(monkeypatch, damage):
    s = case(); current = {'task': NS(id=ident(10)), 'job': NS(), 'capability': s.cap}
    target = NS(status='published', data=copy.deepcopy(s.frame['capability_pins']['capabilities'][0]['data']),
        content_hash=s.target['capability_hash'], validated_hash=s.target['capability_hash'],
        release_version=s.target['release_version'])
    if damage == 'status': target.status = 'draft'
    elif damage == 'hash': target.content_hash = '0' * 64
    elif damage == 'version': target.release_version = '9.0.0'
    monkeypatch.setattr(subject, '_producer', lambda *a: (s.producer, s.frame))
    monkeypatch.setattr(subject.reader, 'checked_context', lambda *a, **k: copy.deepcopy(s.context))
    monkeypatch.setattr(subject.sources, 'fresh', lambda db, p, model, identity:
        NS(status='received') if model is DataGap else target)
    if damage:
        with pytest.raises(HTTPException): subject._plan(None, NS(), current, {'decision_result': s.result})
    else:
        value = subject._plan(None, NS(), current, {'decision_result': s.result})
        assert value['route'] == 'acquire' and value['resense_capability'] == s.target
        no_rights(value)


@pytest.mark.parametrize('kind', ['continue', 'no_action', 'review_required'])
def test_nonacquire_never_reads_or_rents_current_gap_policy_or_source_authority(monkeypatch, kind):
    s = case(kind != 'review_required')
    if kind == 'no_action': s.model['recommendation'].update(candidate_ref=None, supporting_fact_refs=[]); derived(s)
    if kind == 'review_required': s.model['candidate_reviews'][0]['suitability'] = 'uncertain'; derived(s)
    current = {'task': NS(id=ident(10)), 'job': NS(), 'capability': s.cap}
    monkeypatch.setattr(subject, '_producer', lambda *a: (s.producer, s.frame))
    monkeypatch.setattr(subject.sources, 'fresh', lambda *a: pytest.fail('nonacquire read current authority'))
    monkeypatch.setattr(subject.reader, 'checked_context', lambda *a, **k: pytest.fail('nonacquire checked current resense context'))
    assert subject._plan(None, NS(), current, {'decision_result': s.result})['route'] == kind


@pytest.mark.parametrize('route', ['acquire', 'continue', 'no_action', 'review_required'])
def test_each_route_is_one_audited_local_plan_and_same_record_reconcile_never_rebuilds(monkeypatch, route):
    s = case(route in {'continue', 'no_action'})
    if route == 'no_action': s.model['recommendation'].update(candidate_ref=None, supporting_fact_refs=[]); derived(s)
    if route == 'review_required': s.model['candidate_reviews'][0]['suitability'] = 'uncertain'; derived(s)
    local = local_call(monkeypatch, s)
    output = subject.execute(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval)
    assert output['route'] == route and len(local.events) == 1
    monkeypatch.setattr(subject, '_plan', lambda *a: pytest.fail('original saved plan rebuilt'))
    assert subject.execute(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval) == output
    assert subject.reconcile(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval, local.call) == output
    assert len(local.events) == 1
    no_rights(output)


@pytest.mark.parametrize('damage', ['missing_record', 'missing_audit', 'actor', 'record', 'output', 'old_state'])
def test_missing_changed_or_legacy_local_state_never_becomes_a_replacement_plan(monkeypatch, damage):
    s = case(); local = local_call(monkeypatch, s)
    subject.execute(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval)
    if damage == 'missing_record': local.call.data.pop(subject.KEY)
    elif damage == 'missing_audit': local.events.clear()
    elif damage == 'actor': local.events[0].actor_id = ident(99)
    elif damage == 'record': local.call.data[subject.KEY]['job_id'] = ident(99)
    elif damage == 'output': local.call.data[subject.KEY]['output']['can_dispatch'] = True
    else: local.call.data['workflow_decision_plan_v277'] = {'saved': 'old family'}
    with pytest.raises(HTTPException):
        subject.reconcile(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval, local.call)


def test_reconcile_without_original_record_cannot_call_plan_or_create_audit(monkeypatch):
    s = case(); local = local_call(monkeypatch, s)
    monkeypatch.setattr(subject, '_plan', lambda *a: pytest.fail('reconcile invented a local plan'))
    with pytest.raises(HTTPException):
        subject.reconcile(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval, local.call)
    assert not local.events and subject.KEY not in local.call.data


def test_retained_marker_is_only_an_original_local_plan_shape_hint(monkeypatch):
    s = case(); local = local_call(monkeypatch, s)
    subject.execute(local.db, local.p, s.cap, local.inputs, 'original-key', local.approval)
    local.call.status = 'unknown'; local.call.data.update(capability_snapshot=copy.deepcopy(s.cap.data), job_id=local.job.id)
    assert subject.retained_marker_shape(local.call) is True
    local.call.data['workflow_decision_plan_v277'] = {'saved': 'old'}
    assert subject.retained_marker_shape(local.call) is False


def test_producer_resolves_exact_v284_native_edge_under_actual_reader_not_original_actor(monkeypatch):
    s = producer_fixture(monkeypatch)
    pin, frame = subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)
    assert s.seen == [s.p.user_id] and pin['actor_id'] == s.job.data['actor_id'] != s.p.user_id
    assert pin['job_id'] == s.job.id and pin['invocation_id'] == s.call.id and frame == s.case.frame


@pytest.mark.parametrize('damage', ['edge', 'value_hash', 'output', 'old_adapter', 'node_actor', 'call_role',
    'approval', 'version', 'input_schema', 'output_schema', 'input_hash', 'lease',
    'dispatch_actor', 'invoke_audit', 'completion_audit', 'saved_result'])
def test_wrong_original_producer_or_old_version_cannot_be_adopted(monkeypatch, damage):
    s = producer_fixture(monkeypatch)
    if damage == 'edge': s.current['inputs_snapshot']['bindings'].clear()
    elif damage == 'value_hash': s.current['inputs_snapshot']['bindings']['decision_result']['value_hash'] = '0' * 64
    elif damage == 'output': s.activation['output'] = {}
    elif damage == 'old_adapter': s.cap.data['execution']['adapter'] = 'business_decision_v276'
    elif damage == 'node_actor': s.node['actor_id'] = s.p.user_id
    elif damage == 'call_role': s.call.data['role'] = 'perception'
    elif damage == 'approval': s.call.data['approval'] = {'forged': True}
    elif damage == 'version': s.node['execution']['release_version'] = '9.0.0'
    elif damage == 'input_schema': s.node['execution']['input_schema'] = {'type': 'string'}
    elif damage == 'output_schema': s.node['execution']['output_schema'] = {'type': 'string'}
    elif damage == 'input_hash': s.call.data['input_hash'] = '0' * 64
    elif damage == 'lease': s.call.data['lease_token'] = 'other-lease'
    elif damage == 'dispatch_actor': s.transport['dispatch_authorization']['actor_id'] = ident(99)
    elif damage == 'invoke_audit': s.events['capability.invoke'] = []
    elif damage == 'completion_audit': s.events['workflow.graph.node_completed'] = []
    else: monkeypatch.setattr(subject.reasoning, 'saved_result', lambda *a: {'forged': True})
    with pytest.raises(HTTPException): subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)


def test_unfinished_original_job_waits_by_original_ids_before_consumer_invocation(monkeypatch):
    s = producer_fixture(monkeypatch); s.job.status = 'running'
    monkeypatch.setattr(subject.reasoning, 'saved_result', lambda *a: pytest.fail('unfinished Job adopted'))
    with pytest.raises(subject.reader.ProducerPending) as raised:
        subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)
    assert raised.value.job_id == s.job.id and raised.value.invocation_id == s.call.id


def test_original_answer_recovery_requires_the_v284_marker_and_independent_exact_audit(monkeypatch):
    s = producer_fixture(monkeypatch); previous = 'blocked_model'
    s.call.data[s.marker] = {'previous_status': previous, 'output_hash': digest(s.case.result), 'recovered_at': 'saved-original-time'}
    s.events['capability.invoke'][0].data = {'status': previous}
    proof = {'job_id': s.job.id, 'activation_id': s.activation['id'], 'previous_status': previous,
        'output_hash': digest(s.case.result)}
    s.events[s.recovery_action] = [NS(actor_id=s.job.data['actor_id'], data=proof)]
    assert subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)[0]['job_id'] == s.job.id
    s.events[s.recovery_action] = []
    with pytest.raises(HTTPException): subject._producer(s.db, s.p, s.task, s.consumer, s.case.result)


