"""Closed append-command evaluation records; never synthetic graph producers."""
import copy
from typing import Literal

from . import workflow_goal_evaluation_contracts_v288 as original

RECORD = 'workflow-goal-evaluation-record-v299'
PRODUCER_FAMILY = 'append_command_v297'
FLAGS = original.FLAGS
Closed, ID, Hash = original.Closed, original.ID, original.Hash
record_hash = original.record_hash
check_preparation_policy = original.check_preparation_policy
derive_evaluation_window = original.derive_evaluation_window


class CommandRef(Closed):
    id: ID
    record_hash: Hash


class EvaluationRef(Closed):
    id: ID
    record_hash: Hash


class ProducerPin(Closed):
    workspace_id: ID
    task_id: ID
    scope_hash: Hash
    invocation_id: ID
    job_id: ID
    actor_id: ID
    capability_id: ID
    capability_hash: Hash
    input_hash: Hash
    request_key_hash: Hash
    lease_token: ID


class EvaluationTrigger(Closed):
    kind: Literal['append_requested']
    command_ref: CommandRef
    base_evaluation_ref: EvaluationRef
    base_mature_head_hash: Hash
    request_hash: Hash


class GoalEvaluationRecord(original.GoalEvaluationRecord):
    schema: Literal['workflow-goal-evaluation-record-v299']
    producer_family: Literal['append_command_v297']
    command_ref: CommandRef
    producer: ProducerPin
    trigger: EvaluationTrigger


def check_record(raw, *, expected_scope, goal_snapshot, preparation_policy, trusted_now):
    """Consistency only; command/source/SDK/Audit proof belongs to cold readers."""
    o = original; need = o._need
    need(type(raw) is dict and all(raw.get(key) is False for key in FLAGS)
        and type(raw.get('query_controls')) is dict
        and raw['query_controls'].get('cached_only') is True
        and all(raw['query_controls'].get(key) is False
            for key in ('emit_gaps', 'learning', 'include_trusted_cases')))
    value = o._parse(GoalEvaluationRecord, raw)
    expected = o._parse(o.EvaluationScope, expected_scope); snapshot = o._parse(o.GoalSnapshot, goal_snapshot)
    need(o._json(value['scope']) == o._json(expected) and o._json(value['goal_snapshot']) == o._json(snapshot)
        and o._hash(snapshot) == expected['goal_snapshot_hash'] and o._hash(snapshot['spec']) == snapshot['hash'])
    goal = o._goal(snapshot['spec']); policy = check_preparation_policy(snapshot['spec'], preparation_policy)
    need(o._json(value['preparation_policy']) == o._json(policy))
    cut, checked = (o._instant(value[key]) for key in ('cut_at', 'cut_checked_at'))
    now = o._instant(trusted_now)
    need(cut <= checked <= now and value['generation'] >= 2)
    if goal['deadline'] is not None: need(cut < o._instant(goal['deadline']))
    initial = snapshot['time']; need(type(initial.get('reference_at')) is str)
    initial_cut = o._instant(initial['reference_at'])
    need(initial_cut <= cut and o._json(initial) == o._json(o._resolved(goal['evidence']['time'], initial_cut)))
    window = derive_evaluation_window(snapshot['spec'], policy, cut_at=value['cut_at'],
        trusted_now=value['cut_checked_at'], **({'initial_window': initial} if policy['time_basis'] == 'fixed_window' else {}))
    need(o._json(value['derived_window']) == o._json(window)
        and value['original_query_ref']['query_id'] != value['evaluation_query_ref']['query_id']
        and value['record_hash'] == record_hash(value))
    producer, trigger = value['producer'], value['trigger']
    need(value['producer_family'] == PRODUCER_FAMILY and value['evaluation_id'] == producer['invocation_id']
        and value['request_id'] == producer['job_id'] and producer['workspace_id'] == expected['workspace_id']
        and producer['task_id'] == expected['task_id'] and producer['scope_hash'] == o._hash(expected)
        and producer['request_key_hash'] == o._hash(value['request_key'])
        and o._json(trigger['command_ref']) == o._json(value['command_ref'])
        and value['command_ref']['id'] != value['evaluation_id']
        and trigger['base_evaluation_ref']['id'] != value['evaluation_id'])
    o._check_request(value, goal, window, policy, cut)
    for key in ('observation_id', 'event_id', 'memory_id'):
        o._unique([item[key] for item in value['ack_refs']])
    o._unique([item['acquisition_id'] for item in value['ack_refs'] if 'origin_kind' not in item])
    admitted = value['evaluation_result']['result'].get('observation_evidence')
    need(type(admitted) is list and len(admitted) <= 1000
        and all(type(item) is dict and type(item.get('observation_id')) is str for item in admitted))
    admitted_ids = [item['observation_id'] for item in admitted]; o._unique(admitted_ids)
    need(set(admitted_ids) == {ack['observation_id'] for ack in value['ack_refs']})
    from .temporal import matches
    for ack in value['ack_refs']:
        o._unique(ack['fields'])
        observed, received = o._instant(ack['observed_at']), o._instant(ack['received_at'])
        need(ack['query_admitted'] is True and ack['workspace_id'] == expected['workspace_id']
            and ack['task_id'] == expected['task_id'] and ack['entity_id'] in goal['entity_ids']
            and bool(ack['fields']) and set(ack['fields']) <= set(goal['required_fields'])
            and observed <= received <= cut and matches(observed, window, evaluated_at=cut)
            and (not ack['simulation_only'] or value['simulation_only'])
            and (not ack['test_only'] or value['test_only']))
        if ack.get('origin_kind') == 'mqtt_passive_v297':
            pins = ack['mqtt_origin']; declared_test = pins['test_origin'] is not None
            need(ack['origin_task_id'] is None and pins['instance_id'] == expected['instance_id']
                and pins['release_id'] == expected['release_id'] and pins['package_id'] == expected['package_id']
                and pins['entity_id'] == ack['entity_id'] and pins['input_sha256'] == pins['packet_evidence_sha256']
                and ack['simulation_only'] is declared_test and ack['test_only'] is declared_test
                and o._instant(pins['received_at']) <= o._instant(pins['receipt_created_at']) <= cut)
    return copy.deepcopy(value)


def seal_record(unsigned, *, expected_scope, goal_snapshot, preparation_policy, trusted_now):
    original._need(type(unsigned) is dict and 'record_hash' not in unsigned)
    value = copy.deepcopy(unsigned); value['record_hash'] = record_hash(value)
    return check_record(value, expected_scope=expected_scope, goal_snapshot=goal_snapshot,
        preparation_policy=preparation_policy, trusted_now=trusted_now)


def record_schema():
    return copy.deepcopy(GoalEvaluationRecord.model_json_schema())
