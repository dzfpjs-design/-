"""Pure V288 proposals over synthetic, exact V196 DTOs; not source/IAM/E2E proof.

Real reader schema/hash checks, GoalSpec and the shared frozen policy checker
remain active. No DB row, original producer, observed value, type/unit, policy
approval, ACK, permission, service registration or actual sensing is asserted.
"""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from generic_agents import workflow_goal_data_policy_v288 as subject
from generic_agents import workflow_goal_evaluation_contracts_v288 as evaluation
from generic_agents import workflow_task_context_v196 as reader
from generic_agents.contracts import DataRequest, GoalSpec, digest
from generic_agents.temporal import resolve


def ident(index): return str(UUID(int=2880000 + index))


def seal(value, key):
    return {**copy.deepcopy(value), key: digest({name: item for name, item in value.items() if name != key})}


def scene(*, allow_acquisition=True, observation_mode='allow_new', time_mode='latest', evidence_source='observations'):
    time = {'mode': time_mode, 'timezone': 'Asia/Shanghai'}
    if time_mode == 'latest': time['max_age_seconds'] = 600
    elif time_mode == 'range': time.update(start='2026-10-01', end='2026-10-02')
    goal = GoalSpec(purpose='核对本次技术模拟的温度与湿度，保留真实来源及独立收件要求。',
        entity_ids=[ident(1)], required_fields=['temperature_c', 'humidity_pct'],
        criteria=[{'id': 'enough', 'description': '原范围的真实观测资料充分', 'kind': 'evidence_sufficient'}],
        subgoals=[{'id': 'inspect', 'description': '核对真实资料', 'criterion_ids': ['enough']}],
        evidence={'source': evidence_source, 'time': time}, observation_mode=observation_mode).model_dump(mode='json')
    policy = evaluation.make_preparation_policy(goal, allow_acquisition=allow_acquisition)
    reference = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    window = resolve(goal['evidence']['time'], reference=reference)
    snapshot = {'version': 1, 'hash': digest(goal), 'spec': copy.deepcopy(goal), 'time': window}
    scope = {'workspace_id': ident(2), 'task_id': ident(3), 'instance_id': ident(4), 'release_id': ident(5),
        'package_id': ident(6), 'business_id': 'public_weather_simulation', 'graph_hash': 'a' * 64,
        'source_binding_hash': 'b' * 64, 'goal_snapshot_hash': digest(snapshot), 'native_goal_context_hash': 'c' * 64}
    request = DataRequest(question=goal['purpose'], entity_ids=goal['entity_ids'], fields=goal['required_fields'],
        mode='structured', evidence_requirement=goal['evidence'], business_timezone='Asia/Shanghai').model_dump(mode='json')
    request.update(resolved_time=copy.deepcopy(window), reference_at=reference.isoformat(), clarifications=[])
    delegation = {'task_id': scope['task_id'], 'goal_snapshot': snapshot}
    query = {'query_id': ident(7), 'request_hash': digest(request), 'goal_delegation_hash': digest(delegation),
        'version_at_read': 1, 'result_hash_at_read': 'd' * 64}
    producer = {**{key: ident(index) for index, key in enumerate(
        ('capability_id', 'invocation_id', 'job_id', 'actor_id'), 20)}, 'capability_hash': 'e' * 64,
        'activation_id': 'context_reader_activation', 'node_id': 'context_reader', 'cancel_epoch': 0,
        'input_hash': 'f' * 64, 'request_key_hash': '1' * 64}
    monitor = {'policy_id': ident(8), 'approved_hash': '2' * 64, 'approved_by': ident(9),
        'version_at_read': 1, 'source_pins': [{'source_id': ident(10), 'source_hash': '3' * 64}]}
    fields = sorted(goal['required_fields'])
    observation_request = {'gap_id': ident(11), 'query_id': query['query_id'], 'entity_id': ident(1),
        'fields': fields, 'request': request, 'query_request_hash': query['request_hash'], 'goal_delegation': delegation}
    context = seal({'schema': reader.CONTEXT, 'scope': scope, 'query': query,
        'gap': {'gap_id': ident(11), 'entity_id': ident(1), 'version_at_read': 1, 'fields': fields,
            'observation_request': observation_request, 'observation_request_hash': digest(observation_request)},
        'policy': monitor, 'producer': producer, **reader.FLAGS}, 'context_hash')
    package = seal({'schema': reader.EVIDENCE, 'scope': scope, 'query': query, 'evidence_status': 'insufficient',
        'entity_ids': goal['entity_ids'], 'fields': goal['required_fields'], 'evidence_refs': [],
        'gap_refs': [{'gap_id': ident(11), 'entity_id': ident(1), 'status': 'received', 'version_at_read': 1,
            'fields': goal['required_fields'], 'request_hash': digest(request)}], 'producer': producer,
        **reader.FLAGS}, 'package_hash')
    result = {'schema': reader.RESULT, 'status': 'ready', 'evidence_package': package,
        'resense_context': copy.deepcopy(context), 'candidates': [context], 'issues': [], **reader.FLAGS}
    if time_mode != 'latest':
        # The real V196 monitor selector cannot issue a historical context.
        result.update(status='needs_selection', resense_context=None, candidates=[])
    assert reader.checked_result(result) == result
    return NS(goal=goal, policy=policy, result=result)


def reseal(s):
    value = s.result
    value['candidates'] = [seal({**context, 'gap': {**context['gap'],
        'observation_request_hash': digest(context['gap']['observation_request'])}}, 'context_hash')
        for context in value['candidates']]
    if value['status'] == 'ready' and value['resense_context'] is not None and value['candidates']:
        value['resense_context'] = copy.deepcopy(value['candidates'][0])
    value['evidence_package'] = seal(value['evidence_package'], 'package_hash')
    return s


def sufficient(s):
    value = s.result
    value.update(status='no_gap', resense_context=None, candidates=[])
    package = value['evidence_package']
    package.update(evidence_status='sufficient', gap_refs=[], evidence_refs=[{'kind': 'observation',
        'entity_id': s.goal['entity_ids'][0], 'observation_id': ident(30), 'version': 1,
        'observed_at': ('2026-10-01T12:00:00+00:00' if s.goal['evidence']['time']['mode'] == 'range'
            else '2026-11-01T11:59:00+00:00'), 'evidence_hash': '4' * 64}])
    return reseal(s)


def routed(s): return subject.route(s.result, s.goal, s.policy)


def checked(value):
    Draft202012Validator(subject.output_schema()).validate(value)
    assert all(value[key] is False for key in subject.FLAGS)
    assert (value['context_ref'] is not None) is (value['route'] == 'acquire')
    return value


def test_unique_full_original_latest_context_proposes_acquisition_without_future_values_or_rights(monkeypatch):
    s = scene(); before = copy.deepcopy((s.result, s.goal, s.policy))
    monkeypatch.setattr(reader, 'execute', lambda *args, **kwargs: pytest.fail('pure route executed a reader'))
    monkeypatch.setattr(reader, 'checked_context', lambda *args, **kwargs: pytest.fail('pure route tried database authorization'))
    value = checked(routed(s)); context = s.result['resense_context']
    assert value['route'] == 'acquire' and value['reason_code'] == 'approved_latest_context'
    assert value['context_ref'] == {'context_hash': context['context_hash'], 'query_id': context['query']['query_id'],
        'query_request_hash': context['query']['request_hash'], 'gap_id': context['gap']['gap_id'],
        'entity_id': context['gap']['entity_id'], 'policy_id': context['policy']['policy_id'],
        'approved_hash': context['policy']['approved_hash']}
    assert not s.result['evidence_package']['evidence_refs']
    assert value['reader_result_hash'] == digest(s.result) and value['goal_spec_hash'] == digest(s.goal)
    assert value['preparation_policy_hash'] == s.policy['policy_hash']
    assert (s.result, s.goal, s.policy) == before and routed(s) == value


def test_real_reader_sufficiency_report_can_propose_ready_without_permission_to_acquire():
    s = sufficient(scene(allow_acquisition=False, observation_mode='existing_only'))
    before = copy.deepcopy((s.result, s.goal, s.policy))
    value = checked(routed(s))
    assert value['route'] == 'data_ready' and value['reason_code'] == 'reader_reports_sufficient'
    assert 'values' not in value and 'observations' not in value
    assert (s.result, s.goal, s.policy) == before


@pytest.mark.parametrize('reported', ['insufficient', 'conflict', 'unknown', 'needs_review'])
def test_no_gap_is_never_a_substitute_for_an_explicit_sufficient_reader_report(reported):
    s = sufficient(scene()); s.result['evidence_package']['evidence_status'] = reported; reseal(s)
    assert checked(routed(s))['route'] == 'review_required'


@pytest.mark.parametrize('ready', [False, True])
def test_fixed_historical_sufficient_is_ready_but_missing_past_evidence_never_acquires(ready):
    s = scene(time_mode='range', allow_acquisition=False)
    if ready: sufficient(s)
    value = checked(routed(s))
    assert value['route'] == ('data_ready' if ready else 'review_required')
    assert value['reason_code'] == ('reader_reports_sufficient' if ready else 'fixed_window_requires_review')
    assert value['context_ref'] is None and s.policy['allow_acquisition'] is False


def test_fixed_policy_cannot_be_resealed_to_authorize_new_sampling():
    s = scene(time_mode='range', allow_acquisition=False)
    s.policy['allow_acquisition'] = True
    s.policy['policy_hash'] = evaluation.policy_hash(s.policy)
    value = checked(routed(s))
    assert value['route'] == 'review_required' and value['reason_code'] == 'invalid_preparation_policy'


def test_fixed_archive_report_can_be_ready_without_fabricating_observation_timestamps():
    s = sufficient(scene(time_mode='archive', evidence_source='archive', allow_acquisition=False))
    ref = s.result['evidence_package']['evidence_refs'][0]
    ref.update(kind='archive', observation_id=None, observed_at=None); reseal(s)
    assert checked(routed(s))['route'] == 'data_ready'


def test_legacy_goal_without_timezone_is_not_silently_given_china_time():
    s = scene(); s.goal['evidence']['time']['timezone'] = None
    before = copy.deepcopy(s.goal)
    assert checked(routed(s))['reason_code'] == 'invalid_preparation_policy'
    assert s.goal == before and s.goal['evidence']['time']['timezone'] is None


@pytest.mark.parametrize('kind', ['relation_query', 'data_query'])
def test_unsupported_advanced_goal_queries_are_refused_before_proposing_any_sampling(kind):
    s = scene()
    if kind == 'relation_query':
        s.goal[kind] = {'package_id': ident(6), 'relation_types': ['related_to'], 'root_ids': [ident(1)],
            'direction': 'outgoing', 'max_hops': 1, 'include_roots': False, 'at': None}
    else:
        s.goal[kind] = {'tool_steps': [{'id': 'read', 'capability_id': ident(50),
            'input_from': 'evidence', 'parameters': {}}], 'max_tool_seconds': 60, 'allow_external_tools': False}
    assert GoalSpec.model_validate(s.goal, strict=True).model_dump(mode='json') == s.goal
    value = checked(routed(s))
    assert value['route'] == 'review_required' and value['reason_code'] == 'unsupported_goal_query'


@pytest.mark.parametrize('damage', ['none', 'bad_hash', 'wrong_age', 'wrong_timezone', 'extra_authority', 'bad_bool'])
def test_frozen_policy_is_checked_by_shared_contract_and_never_fabricated(damage):
    s = scene()
    if damage == 'none': s.policy = None
    elif damage == 'bad_hash': s.policy['policy_hash'] = '0' * 64
    elif damage == 'wrong_age': s.policy['max_age_seconds'] = 601
    elif damage == 'wrong_timezone': s.policy['timezone'] = 'UTC'
    elif damage == 'extra_authority': s.policy['permissions_verified'] = True
    else: s.policy['allow_acquisition'] = 1
    assert checked(routed(s))['route'] == 'review_required'


def test_explicit_policy_refusal_does_not_borrow_monitor_context_as_permission():
    s = scene(allow_acquisition=False)
    value = checked(routed(s))
    assert value['reason_code'] == 'acquisition_not_permitted' and value['route'] == 'review_required'


def test_two_approved_contexts_are_not_reduced_to_the_explicit_first_selection():
    s = scene(); extra = copy.deepcopy(s.result['candidates'][0]); extra['policy']['policy_id'] = ident(40)
    s.result['candidates'].append(extra); reseal(s)
    assert reader.checked_result(s.result) == s.result
    value = checked(routed(s))
    assert value['reason_code'] == 'context_not_unique' and value['context_ref'] is None


def test_partial_gap_is_not_silently_completed_by_dropping_an_original_goal_field():
    s = scene(); context = s.result['candidates'][0]
    context['gap']['fields'] = ['temperature_c']; context['gap']['observation_request']['fields'] = ['temperature_c']
    s.result['evidence_package']['gap_refs'][0]['fields'] = ['temperature_c']; reseal(s)
    assert reader.checked_result(s.result) == s.result
    assert checked(routed(s))['reason_code'] == 'field_coverage_mismatch'


def test_full_gap_field_reordering_keeps_separate_original_query_and_gap_request_hashes():
    s = scene(); original_query_hash = s.result['evidence_package']['query']['request_hash']
    request = s.result['candidates'][0]['gap']['observation_request']['request']
    request['fields'] = list(reversed(request['fields']))
    s.result['evidence_package']['gap_refs'][0]['request_hash'] = digest(request); reseal(s)
    assert digest(request) != original_query_hash
    assert reader.checked_result(s.result) == s.result
    value = checked(routed(s))
    assert value['route'] == 'acquire' and value['context_ref']['query_request_hash'] == original_query_hash


@pytest.mark.parametrize('damage', ['other_entity', 'partial_package', 'multiple_gaps', 'gap_version',
    'gap_request_hash', 'awaiting_observation', 'unknown_gap_state', 'scope_mismatch', 'query_mismatch'])
def test_original_scope_fields_gap_and_query_references_must_remain_coherent(damage):
    s = scene(); package = s.result['evidence_package']; context = s.result['candidates'][0]
    if damage == 'other_entity': package['entity_ids'] = [ident(41)]
    elif damage == 'partial_package': package['fields'] = ['temperature_c']
    elif damage == 'multiple_gaps': package['gap_refs'].append({**package['gap_refs'][0], 'gap_id': ident(42)})
    elif damage == 'gap_version': package['gap_refs'][0]['version_at_read'] = 2
    elif damage == 'gap_request_hash': package['gap_refs'][0]['request_hash'] = '0' * 64
    elif damage in {'awaiting_observation', 'unknown_gap_state'}: package['gap_refs'][0]['status'] = damage
    elif damage == 'scope_mismatch': context['scope']['task_id'] = ident(43)
    else: context['query']['query_id'] = ident(44)
    reseal(s)
    assert checked(routed(s))['route'] == 'review_required'


def test_resealed_request_cannot_weaken_original_goal_quality_or_be_a_different_query():
    s = scene(); request = s.result['candidates'][0]['gap']['observation_request']['request']
    request['evidence_requirement']['minimum_verification'] = 'unverified_extraction'
    s.result['evidence_package']['gap_refs'][0]['request_hash'] = digest(request); reseal(s)
    assert reader.checked_result(s.result) == s.result
    assert checked(routed(s))['reason_code'] == 'query_request_mismatch'


def test_context_tool_steps_are_refused_before_proposing_acquisition():
    s = scene(); request = s.result['candidates'][0]['gap']['observation_request']['request']
    request['tool_steps'] = [{'id': 'read', 'capability_id': ident(51), 'input_from': 'evidence', 'parameters': {}}]
    s.result['evidence_package']['gap_refs'][0]['request_hash'] = digest(request); reseal(s)
    assert checked(routed(s))['reason_code'] == 'unsupported_goal_query'


@pytest.mark.parametrize('damage', ['mode', 'timezone', 'age', 'unknown_start'])
def test_original_time_requirement_and_snapshot_window_are_not_reinterpreted(damage):
    s = scene(); context = s.result['candidates'][0]
    request = context['gap']['observation_request']['request']; window = request['resolved_time']
    if damage == 'mode': window['mode'] = 'range'
    elif damage == 'timezone': window['timezone'] = 'UTC'
    elif damage == 'age': window['max_age_seconds'] = 601
    else: window['start'] = None
    # Keep ordinary hashes coherent so the explicit original window comparison
    # must reject the changed time semantics rather than silently sliding it.
    s.result['evidence_package']['gap_refs'][0]['request_hash'] = digest(request); reseal(s)
    assert checked(routed(s))['reason_code'] == 'time_requirement_mismatch'


def test_original_goal_snapshot_is_required_not_only_a_matching_context_self_hash():
    s = scene(); context = s.result['candidates'][0]
    snapshot = context['gap']['observation_request']['goal_delegation']['goal_snapshot']
    snapshot['spec']['purpose'] = 'Another saved purpose'
    snapshot['hash'] = digest(snapshot['spec'])
    context['scope']['goal_snapshot_hash'] = digest(snapshot)
    context['query']['goal_delegation_hash'] = digest(context['gap']['observation_request']['goal_delegation'])
    s.result['evidence_package']['scope'] = copy.deepcopy(context['scope'])
    s.result['evidence_package']['query'] = copy.deepcopy(context['query']); reseal(s)
    assert reader.checked_result(s.result) == s.result
    assert checked(routed(s))['reason_code'] == 'goal_snapshot_mismatch'


@pytest.mark.parametrize('damage', ['empty_refs', 'unknown_time', 'wrong_ref_entity', 'missing_observation_id',
    'unresolved_gap', 'archive_as_observation'])
def test_ready_report_still_requires_complete_consistent_reference_metadata(damage):
    s = sufficient(scene()); package = s.result['evidence_package']
    if damage == 'empty_refs': package['evidence_refs'] = []
    elif damage == 'unknown_time': package['evidence_refs'][0]['observed_at'] = None
    elif damage == 'wrong_ref_entity': package['evidence_refs'][0]['entity_id'] = ident(45)
    elif damage == 'missing_observation_id': package['evidence_refs'][0]['observation_id'] = None
    elif damage == 'unresolved_gap':
        package['gap_refs'] = [{'gap_id': ident(11), 'entity_id': ident(1), 'status': 'needs_review',
            'version_at_read': 1, 'fields': s.goal['required_fields'], 'request_hash': '0' * 64}]
    else: package['evidence_refs'][0]['kind'] = 'archive'
    reseal(s)
    assert checked(routed(s))['route'] == 'review_required'


@pytest.mark.parametrize('damage', ['multiple_entities', 'duplicate_fields', 'numeric_field', 'extra_goal_key', 'bad_reader_hash'])
def test_malformed_or_out_of_p0_inputs_propose_review_without_mutation(damage):
    s = scene()
    if damage == 'multiple_entities': s.goal['entity_ids'].append(ident(46))
    elif damage == 'duplicate_fields': s.goal['required_fields'].append(s.goal['required_fields'][0])
    elif damage == 'numeric_field': s.goal['required_fields'][0] = 3
    elif damage == 'extra_goal_key': s.goal['can_dispatch'] = True
    else: s.result['resense_context']['context_hash'] = '0' * 64
    before = copy.deepcopy((s.result, s.goal, s.policy))
    assert checked(routed(s))['route'] == 'review_required'
    assert (s.result, s.goal, s.policy) == before
