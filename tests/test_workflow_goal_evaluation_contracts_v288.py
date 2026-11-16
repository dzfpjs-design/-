"""Independent synthetic pure contracts: no IO, PG, provider or model proof.

Pinned UUIDs/hashes below are fictional references, not authenticated ACKs or
dispatch grants. Root's separate integration tests must authenticate actual
rows/audits, query cuts and producer completion in the target runtime.
"""
import copy
import hashlib

import pytest

from generic_agents.contracts import GoalSpec
from generic_agents.typed_payload_codec_v131 import canonical_json
from generic_agents import workflow_goal_evaluation_contracts_v288 as subject


INITIAL = '2026-11-01T12:00:00+00:00'
CUT = '2026-11-01T12:05:00+00:00'
CHECKED = '2026-11-01T12:05:01+00:00'


def uid(number):
    return f'00000000-0000-4000-8000-{number:012d}'


def hashed(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def goal_spec(*, fixed=False, observation_mode='allow_new'):
    time = ({'mode': 'range', 'timezone': 'Asia/Shanghai',
        'start': '2026-10-31T11:00:00+00:00', 'end': '2026-10-31T12:00:00+00:00'}
        if fixed else {'mode': 'latest', 'timezone': 'Asia/Shanghai', 'max_age_seconds': 600})
    return GoalSpec.model_validate({'purpose': '核对同一对象的实际温度后形成业务建议',
        'entity_ids': [uid(6)], 'required_fields': ['temperature'],
        'subgoals': [{'id': 'read', 'description': '读取实际资料', 'criterion_ids': ['data']}],
        'criteria': [{'id': 'data', 'description': '资料充分', 'kind': 'evidence_sufficient'}],
        'evidence': {'source': 'observations', 'time': time},
        'observation_mode': observation_mode}).model_dump(mode='json')


def scene(*, fixed=False, acquisition=True):
    """Build fictional bytes/refs independently; never call a DB or receiver."""
    goal = goal_spec(fixed=fixed)
    policy = subject.make_preparation_policy(goal, allow_acquisition=acquisition and not fixed)
    if fixed:
        from generic_agents.temporal import resolve, instant
        initial_window = resolve(goal['evidence']['time'], reference=instant(INITIAL))
    else:
        initial_window = subject.derive_evaluation_window(goal, policy, cut_at=INITIAL, trusted_now=INITIAL)
    snapshot = {'version': 1, 'hash': hashed(goal), 'spec': goal, 'time': initial_window}
    scope = {'workspace_id': uid(1), 'task_id': uid(2), 'instance_id': uid(3),
        'release_id': uid(4), 'package_id': uid(5), 'business_id': 'read_actual_data',
        'graph_hash': hashed('frozen graph'), 'source_binding_hash': hashed('new frozen V288 binding'),
        'goal_snapshot_hash': hashed(snapshot), 'native_goal_context_hash': hashed('original Goal context')}
    window = subject.derive_evaluation_window(goal, policy, cut_at=CUT, trusted_now=CHECKED,
        **({'initial_window': initial_window} if fixed else {}))
    request = {'question': goal['purpose'], 'entity_ids': goal['entity_ids'],
        'fields': goal['required_fields'], 'mode': 'structured', 'model_profile_id': None,
        'business_timezone': 'Asia/Shanghai', 'evidence_requirement': goal['evidence'],
        'relation_query': None, 'tool_steps': [], 'allow_external_tools': False,
        'clarifications': [], 'resolved_time': window, 'reference_at': CUT}
    observed = '2026-10-31T11:30:00+00:00' if fixed else '2026-11-01T12:01:00+00:00'
    received = '2026-10-31T11:31:00+00:00' if fixed else '2026-11-01T12:02:00+00:00'
    report = {'status': 'sufficient', 'evidence': [], 'observation_evidence': [
        {'observation_id': uid(40), 'entity_id': uid(6), 'observed_at': observed,
            'attributes': {'temperature': 21.0}, 'data_receipt_hash': hashed('independent receipt')}],
        'gaps': [], 'conflicts': [], 'resolved_time': window,
        'evidence_requirement': goal['evidence'], 'evaluated_at': CUT}
    result = {'request': request, 'need': {'entity_ids': goal['entity_ids'], 'fields': goal['required_fields']},
        'status': 'sufficient', 'result': report}
    original_request = {**copy.deepcopy(request), 'resolved_time': initial_window, 'reference_at': INITIAL}
    original_result = {'status': 'insufficient', 'result': {'status': 'insufficient', 'evaluated_at': INITIAL}}
    q0 = {'request': original_request, 'result': original_result, 'native_goal_task_id': uid(2)}
    qe = {'request': request, 'result': result, 'goal_evaluation_v288': {'evaluation_id': uid(26)}}
    ack = {'workspace_id': uid(1), 'task_id': uid(2), 'origin_task_id': uid(2), 'query_admitted': True, 'entity_id': uid(6), 'fields': ['temperature'],
        'acquisition_id': uid(39), 'acquisition_hash': hashed('acquisition'),
        'observation_id': uid(40), 'observation_hash': hashed('observation'),
        'source_id': uid(41), 'source_hash': hashed('source'),
        'extractor_id': uid(42), 'extractor_hash': hashed('extractor'),
        'event_id': uid(43), 'event_hash': hashed('received event'),
        'memory_id': uid(44), 'memory_hash': hashed('receiver memory'),
        'data_reception_hash': hashed('independent receipt'), 'observed_at': observed,
        'received_at': received, 'simulation_only': True, 'test_only': True}
    request_key = 'graph:synthetic-task:evaluation:1'
    unsigned = {'schema': subject.RECORD, 'evaluation_id': uid(26), 'scope': scope, 'goal_snapshot': snapshot,
        'preparation_policy': policy, 'generation': 1, 'request_id': uid(27), 'request_key': request_key,
        'original_query_ref': {'query_id': uid(10), 'version': 2,
            'request_hash': hashed(original_request), 'result_hash': hashed(original_result), 'data_hash': hashed(q0)},
        'evaluation_query_ref': {'query_id': uid(11), 'version': 1,
            'request_hash': hashed(request), 'result_hash': hashed(result), 'data_hash': hashed(qe)},
        'evaluation_request': request, 'evaluation_result': result,
        'query_controls': {'cached_only': True, 'learning': False, 'emit_gaps': False, 'include_trusted_cases': False},
        'cut_at': CUT, 'cut_checked_at': CHECKED, 'derived_window': window,
        'trigger': {'kind': 'acquisition_completed' if acquisition and not fixed else 'data_ready',
            'local_plan_hash': hashed('local plan'), 'reader_invocation_id': uid(20),
            'reader_result_hash': hashed('reader output'),
            'reader_context_hash': hashed('original resense context') if acquisition and not fixed else None,
            'resense_invocation_id': uid(21) if acquisition and not fixed else None,
            'resense_receipt_hash': hashed('complete original resense receipt') if acquisition and not fixed else None},
        'producer': {'workspace_id': uid(1), 'task_id': uid(2), 'scope_hash': hashed(scope),
            'capability_id': uid(25), 'capability_hash': hashed('registered evaluation packet capability'),
            'invocation_id': uid(26), 'job_id': uid(27), 'actor_id': uid(28), 'lease_token': uid(29),
            'activation_id': 'evaluation_activation', 'node_id': 'evaluation_packet', 'cancel_epoch': 0,
            'input_hash': hashed('actual arguments'), 'request_key_hash': hashed(request_key)},
        'ack_refs': [ack], 'evidence_ref': {'evidence_id': uid(50), 'sha256': hashed('actual bytes'),
            'size_bytes': 64, 'media_type': 'text/plain'},
        'simulation_only': True, 'test_only': True, **subject.FLAGS}
    kwargs = {'expected_scope': copy.deepcopy(scope), 'goal_snapshot': copy.deepcopy(snapshot),
        'preparation_policy': copy.deepcopy(policy), 'trusted_now': CHECKED}
    sealed = subject.seal_record(unsigned, **kwargs)
    return unsigned, sealed, kwargs, q0


def rehash(record):
    value = copy.deepcopy(record)
    value['record_hash'] = subject.record_hash(value)
    return value


def check_bad(record, kwargs):
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.check_record(rehash(record), **kwargs)


def test_rolling_new_generation_retains_original_goal_and_query_anchor():
    unsigned, sealed, kwargs, original = scene()
    before = copy.deepcopy(original)
    checked = subject.check_record(sealed, **kwargs)
    assert checked == sealed and checked is not sealed
    assert original == before and checked['original_query_ref']['query_id'] != checked['evaluation_query_ref']['query_id']
    assert checked['goal_snapshot']['time']['end'] == INITIAL
    assert checked['derived_window']['end'] == CUT
    assert checked['ack_refs'][0]['observed_at'] > INITIAL
    assert all(checked[key] is False for key in subject.FLAGS)
    assert 'output_hash' not in checked['producer'] and 'completion_audit_id' not in checked['producer']
    assert 'record_hash' not in unsigned


@pytest.mark.parametrize('change', [
    lambda p: p.update(time_basis='fixed_window'),
    lambda p: p.update(timezone='UTC'),
    lambda p: p.update(max_age_seconds=601),
    lambda p: p.update(max_age_seconds=True),
    lambda p: p.update(allow_acquisition=1),
    lambda p: p.update(fields=['temperature']),
])
def test_policy_parameters_are_closed_and_exact_not_hash_authority(change):
    goal = goal_spec(); policy = subject.make_preparation_policy(goal, allow_acquisition=True)
    change(policy); policy['policy_hash'] = subject.policy_hash(policy)
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.check_preparation_policy(goal, policy)


@pytest.mark.parametrize('change', [
    lambda g: g['evidence']['time'].update(timezone=None),
    lambda g: g['evidence']['time'].update(timezone='not/a/timezone'),
    lambda g: g['evidence']['time'].update(max_age_seconds=None),
    lambda g: g['evidence']['time'].update(start='2020-01-01'),
    lambda g: g.update(observation_mode='existing_only'),
    lambda g: g['evidence'].update(source='archive'),
])
def test_real_goal_must_allow_explicit_latest_observations(change):
    goal = goal_spec(); change(goal)
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.make_preparation_policy(goal, allow_acquisition=True)


@pytest.mark.parametrize('cut,now', [
    ('2026-11-01T12:05:02+00:00', CHECKED),
    ('2026-11-01T12:05:00', CHECKED),
    (CUT, '2026-11-01'),
])
def test_derive_rejects_future_or_unzoned_cut(cut, now):
    goal = goal_spec(); policy = subject.make_preparation_policy(goal)
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.derive_evaluation_window(goal, policy, cut_at=cut, trusted_now=now)


def test_rolling_cut_uses_actual_age_and_timezone_without_mutation():
    goal = goal_spec(); original = copy.deepcopy(goal)
    policy = subject.make_preparation_policy(goal)
    window = subject.derive_evaluation_window(goal, policy,
        cut_at='2026-11-01T20:05:00+08:00', trusted_now=CHECKED)
    assert window['start'] == '2026-11-01T11:55:00+00:00'
    assert window['end'] == CUT and window['timezone'] == 'Asia/Shanghai'
    assert goal == original
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.derive_evaluation_window(goal, policy, cut_at=CUT, trusted_now=CHECKED, initial_window=window)


def test_fixed_existing_data_ready_seals_original_window_without_moving_it():
    _, sealed, kwargs, _ = scene(fixed=True)
    value = subject.check_record(sealed, **kwargs)
    assert value['trigger']['kind'] == 'data_ready'
    assert value['preparation_policy']['allow_acquisition'] is False
    assert value['derived_window'] == value['goal_snapshot']['time']
    assert value['derived_window']['end'] == '2026-10-31T12:00:00+00:00'
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.make_preparation_policy(value['goal_snapshot']['spec'], allow_acquisition=True)
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.derive_evaluation_window(value['goal_snapshot']['spec'], value['preparation_policy'],
            cut_at=CUT, trusted_now=CHECKED)
    wrong = copy.deepcopy(value['derived_window']); wrong['end'] = CUT
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.derive_evaluation_window(value['goal_snapshot']['spec'], value['preparation_policy'],
            cut_at=CUT, trusted_now=CHECKED, initial_window=wrong)


def test_fixed_history_cannot_be_satisfied_by_future_new_sensing():
    _, record, kwargs, _ = scene(fixed=True)
    record['ack_refs'][0].update(observed_at='2026-11-01T12:01:00+00:00', received_at='2026-11-01T12:02:00+00:00')
    check_bad(record, kwargs)
    _, record, kwargs, _ = scene(fixed=True)
    record['trigger'].update(kind='acquisition_completed', reader_context_hash=hashed('context'),
        resense_invocation_id=uid(21), resense_receipt_hash=hashed('receipt'))
    check_bad(record, kwargs)


@pytest.mark.parametrize('change', [
    lambda r: r['scope'].update(task_id=uid(101)),
    lambda r: r['scope'].update(workspace_id=uid(101)),
    lambda r: r['scope'].update(source_binding_hash=hashed('another binding')),
    lambda r: r['goal_snapshot'].update(version=2),
    lambda r: r['goal_snapshot']['spec'].update(purpose='another Goal'),
    lambda r: r['producer'].update(task_id=uid(102)),
    lambda r: r['producer'].update(workspace_id=uid(102)),
    lambda r: r['producer'].update(scope_hash=hashed('other scope')),
    lambda r: r['producer'].update(request_key_hash=hashed('other nonce')),
    lambda r: r.update(evaluation_id=uid(101)),
    lambda r: r.update(request_id=uid(101)),
    lambda r: r['original_query_ref'].update(query_id=r['evaluation_query_ref']['query_id']),
    lambda r: r.update(generation=True),
    lambda r: r['producer'].update(output_hash=hashed('not yet produced')),
])
def test_scope_goal_query_and_current_producer_pins_cannot_be_rebound(change):
    _, record, kwargs, _ = scene(); change(record); check_bad(record, kwargs)


@pytest.mark.parametrize('change', [
    lambda a: a.update(task_id=uid(103)),
    lambda a: a.update(workspace_id=uid(103)),
    lambda a: a.update(entity_id=uid(103)),
    lambda a: a.update(fields=['humidity']),
    lambda a: a.update(fields=['temperature', 'temperature']),
    lambda a: a.update(observed_at='2026-11-01T12:06:00+00:00'),
    lambda a: a.update(observed_at='2026-11-01T11:54:59+00:00'),
    lambda a: a.update(received_at='2026-11-01T12:05:02+00:00'),
    lambda a: a.update(received_at='2026-11-01T12:00:00+00:00'),
])
def test_receipt_scope_order_and_actual_time_boundaries(change):
    _, record, kwargs, _ = scene(); change(record['ack_refs'][0]); check_bad(record, kwargs)


def test_duplicate_missing_receipts_and_simulation_downgrade_are_rejected():
    for change in (lambda r: r['ack_refs'].append(copy.deepcopy(r['ack_refs'][0])),
            lambda r: r.update(ack_refs=[]), lambda r: r.update(simulation_only=False),
            lambda r: r.update(test_only=False)):
        _, record, kwargs, _ = scene(); change(record); check_bad(record, kwargs)


def test_existing_source_origin_is_separate_from_current_consumer_scope():
    _, record, kwargs, _ = scene(acquisition=False)
    record['ack_refs'][0]['origin_task_id'] = uid(105)
    # Structural admissibility only: production still checks real source IAM,
    # original acquisition ownership and its independent reception chain.
    record = rehash(record)
    assert subject.check_record(record, **kwargs)['ack_refs'][0]['task_id'] == kwargs['expected_scope']['task_id']
    _, record, kwargs, _ = scene(acquisition=True)
    record['ack_refs'][0]['origin_task_id'] = uid(105)
    check_bad(record, kwargs)


def test_received_collection_outside_window_can_be_saved_only_as_excluded():
    _, record, kwargs, _ = scene(acquisition=True)
    state = record['evaluation_result']; state['status'] = state['result']['status'] = 'insufficient'
    state['result']['observation_evidence'] = []
    record['evaluation_query_ref']['result_hash'] = hashed(state)
    record['ack_refs'][0].update(query_admitted=False, fields=[], observed_at='2026-11-01T11:00:00+00:00')
    record = rehash(record)
    checked = subject.check_record(record, **kwargs)
    assert checked['evaluation_result']['status'] == 'insufficient'
    assert checked['ack_refs'][0]['query_admitted'] is False
    checked['ack_refs'][0]['query_admitted'] = True
    check_bad(checked, kwargs)


def test_admission_cannot_be_forged_by_rehashing_a_receipt_marker():
    _, record, kwargs, _ = scene(acquisition=True)
    record['ack_refs'][0]['query_admitted'] = False
    check_bad(record, kwargs)


@pytest.mark.parametrize('change', [
    lambda r: r['evaluation_request'].update(mode='cloud'),
    lambda r: r['evaluation_request'].update(fields=['humidity']),
    lambda r: r['evaluation_request'].update(entity_ids=[uid(105)]),
    lambda r: r['evaluation_request'].update(model_profile_id=uid(105)),
    lambda r: r['evaluation_request'].update(tool_steps=[{'id': 'provider'}]),
    lambda r: r['evaluation_request'].update(allow_external_tools=True),
    lambda r: r['evaluation_request'].update(clarifications=['unresolved question']),
    lambda r: r['query_controls'].update(emit_gaps=True),
    lambda r: r['query_controls'].update(learning=0),
    lambda r: r['evaluation_result']['result']['resolved_time'].update(end=CHECKED),
])
def test_rehashed_local_request_or_real_report_cannot_change_goal_or_cut(change):
    _, record, kwargs, _ = scene(); change(record)
    record['evaluation_query_ref']['request_hash'] = hashed(record['evaluation_request'])
    record['evaluation_query_ref']['result_hash'] = hashed(record['evaluation_result'])
    check_bad(record, kwargs)


def test_legal_insufficient_or_conflict_result_is_retained_for_review_not_accepted():
    for status in ('insufficient', 'conflict'):
        _, record, kwargs, _ = scene(acquisition=False)
        record['evaluation_result']['status'] = status
        record['evaluation_result']['result']['status'] = status
        record['evaluation_query_ref']['result_hash'] = hashed(record['evaluation_result'])
        record = rehash(record)
        assert subject.check_record(record, **kwargs)['evaluation_result']['status'] == status
        assert record['can_accept'] is False and record['can_dispatch'] is False


@pytest.mark.parametrize('flag', tuple(subject.FLAGS))
@pytest.mark.parametrize('value', [True, 0])
def test_authority_flags_must_be_literal_false(flag, value):
    _, record, kwargs, _ = scene(); record[flag] = value; check_bad(record, kwargs)


def test_seals_schemas_and_results_are_detached_and_exact_storage_types_survive():
    unsigned, record, kwargs, _ = scene()
    checked = subject.check_record(record, **kwargs)
    checked['ack_refs'][0]['fields'].append('local unsaved mutation')
    unsigned['goal_snapshot']['spec']['purpose'] = 'local draft'
    assert subject.check_record(record, **kwargs) == record
    assert subject.preparation_policy_schema() is not subject.preparation_policy_schema()
    schema = subject.record_schema(); schema['properties'].clear()
    assert subject.record_schema()['properties']
    changed = copy.deepcopy(record)
    changed['evaluation_result']['result']['observation_evidence'][0]['attributes']['temperature'] = 21
    assert subject.record_hash(changed) != record['record_hash']
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.seal_record(record, **kwargs)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), ('not', 'json')])
def test_unsafe_json_never_becomes_a_storage_hash(value):
    with pytest.raises(subject.GoalEvaluationContractError):
        subject.record_hash({'unexpected': value})

@pytest.mark.parametrize('value', [True, None, 0])
def test_trusted_cases_cannot_be_enabled_or_coerced(value):
    _raw, record, kwargs, _original = scene()
    record['query_controls']['include_trusted_cases'] = value
    check_bad(record, kwargs)

def test_report_must_use_the_exact_frozen_evaluation_clock():
    _raw, record, kwargs, _original = scene()
    record['evaluation_result']['result']['evaluated_at'] = CHECKED
    record['evaluation_query_ref']['result_hash'] = hashed(record['evaluation_result'])
    check_bad(record, kwargs)
