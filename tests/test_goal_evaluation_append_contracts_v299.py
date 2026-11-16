"""Pure consistency tests, not command/SDK/source authority or PG proof."""
import copy

import pytest

from generic_agents import workflow_goal_evaluation_contracts_v288 as previous
from generic_agents import workflow_goal_evaluation_contracts_v299 as subject
from tests.test_workflow_goal_evaluation_contracts_v288 import scene, uid


def record():
    _policy, value, kwargs, _original = scene(acquisition=False)
    value.update(schema=subject.RECORD, generation=2, producer_family=subject.PRODUCER_FAMILY,
        command_ref={'id': uid(90), 'record_hash': 'a' * 64})
    value['producer'] = {key: value['producer'][key] for key in subject.ProducerPin.model_fields}
    value['request_key'] = 'goal-evaluation-append:' + value['scope']['task_id'] + ':' + uid(91)
    value['producer']['request_key_hash'] = previous._hash(value['request_key'])
    value['trigger'] = {'kind': 'append_requested', 'command_ref': copy.deepcopy(value['command_ref']),
        'base_evaluation_ref': {'id': uid(92), 'record_hash': 'b' * 64},
        'base_mature_head_hash': 'c' * 64, 'request_hash': 'd' * 64}
    value['record_hash'] = subject.record_hash(value)
    return value, kwargs


def test_append_record_has_its_own_producer_trigger_and_exact_fixed_old_dispatch():
    value, kwargs = record()
    assert subject.check_record(value, **kwargs) == value
    assert previous.check_record(value, **kwargs) == value
    assert not {'activation_id', 'node_id', 'cancel_epoch', 'command_id'} & set(value['producer'])
    assert value['producer']['scope_hash'] == previous._hash(value['scope'])
    _policy, old, old_kwargs, _original = scene()
    before = copy.deepcopy(old)
    assert previous.check_record(old, **old_kwargs) == before


@pytest.mark.parametrize('fault', ['graph_producer', 'old_trigger', 'command_mismatch', 'scope_confusion',
    'same_base', 'generation_one', 'coerced_controls', 'excluded_ack', 'unknown_schema'])
def test_closed_append_record_cannot_borrow_old_graph_identity_or_weaken_goal(fault):
    value, kwargs = record()
    if fault == 'graph_producer': value['producer']['activation_id'] = 'invented_graph_activation'
    if fault == 'old_trigger': value['trigger']['reader_invocation_id'] = uid(20)
    if fault == 'command_mismatch': value['trigger']['command_ref']['id'] = uid(999)
    if fault == 'scope_confusion': value['producer']['scope_hash'] = '0' * 64
    if fault == 'same_base': value['trigger']['base_evaluation_ref']['id'] = value['evaluation_id']
    if fault == 'generation_one': value['generation'] = 1
    if fault == 'coerced_controls': value['query_controls']['learning'] = 0
    if fault == 'excluded_ack': value['ack_refs'][0]['query_admitted'] = False
    if fault == 'unknown_schema': value['schema'] = 'workflow-goal-evaluation-record-v300'
    value['record_hash'] = subject.record_hash(value)
    with pytest.raises(previous.GoalEvaluationContractError): previous.check_record(value, **kwargs)
