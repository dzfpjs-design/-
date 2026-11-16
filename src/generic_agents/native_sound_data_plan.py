"""Declare environmental sound outputs only on newly compiled business releases.

This additive plan describes storage and independent reception. It grants no
sensor action, automatic anomaly finding, deletion or field-truth authority.
"""
import copy
from .contracts import digest
from .native_pixel_data_plan_v250 import _steps

KEY = 'native_sound_results'
SCHEMA = 'instance-native-sound-data-plan-v1'
FAMILY = 'workflow_environment_sound_candidate_v1'
ADAPTER = 'local_environment_sound'


def build(snapshot):
    bindings = []
    for business, step_id, node_id, pin in _steps(snapshot):
        if type(pin) is not dict or pin.get('adapter') != ADAPTER:
            continue
        if not (type(step_id) is str and step_id and type(pin.get('capability_id')) is str
                and type(pin.get('capability_hash')) is str and len(pin['capability_hash']) == 64
                and not set(pin['capability_hash']) - set('0123456789abcdef')
                and type(pin.get('release_version')) is str
                and pin.get('agent_role') in {'perception', 'data'}
                and type(pin.get('output_schema')) is dict):
            raise ValueError('native_sound_data_plan_invalid')
        schema = copy.deepcopy(pin['output_schema'])
        bindings.append({'business_id': business['id'], 'business_name': business['name'],
            'step_id': step_id, 'node_id': node_id, 'capability_id': pin['capability_id'],
            'capability_hash': pin['capability_hash'], 'release_version': pin['release_version'],
            'agent_role': pin['agent_role'], 'output_schema': schema,
            'output_schema_hash': digest(schema), 'source_family': FAMILY,
            'storage': {'machine_result': 'environment_sound_runs',
                'original': 'immutable_evidence', 'handoff': 'observation_and_outbox',
                'reception': 'independent_candidate_memory',
                'human_work': 'versioned_sound_review_and_original_task_receipts'},
            'candidate_only': True, 'score_semantics': 'uncalibrated_multilabel',
            'temporal_bounds': 'model_windows_not_exact_event_boundaries',
            'source_time_verified': False, 'object_state_updated': False})
    if not bindings:
        return None
    bindings.sort(key=lambda v: (v['business_id'], v['step_id'], v['node_id'] or ''))
    if len({(v['business_id'], v['step_id'], v['node_id']) for v in bindings}) != len(bindings):
        raise ValueError('native_sound_data_plan_duplicate_binding')
    value = {'schema': SCHEMA, 'source_family': FAMILY, 'bindings': bindings,
        'time_zone': 'Asia/Shanghai', 'date_basis': 'machine_result_registered',
        'requires_independent_ack': True, 'recording_time_unknown_is_preserved': True,
        'field_truth_verified': False, 'automatic_domain_use': False,
        'deletion_authorized': False}
    return {**value, 'plan_hash': digest(value)}


def verify(snapshot):
    expected = build(snapshot)
    if expected is None:
        if KEY in snapshot:
            raise ValueError('native_sound_data_plan_unexpected')
    elif type(snapshot.get(KEY)) is not dict or digest(snapshot[KEY]) != digest(expected):
        raise ValueError('native_sound_data_plan_changed')
    return copy.deepcopy(expected)


def declared(snapshot, business_id, capability_id, capability_hash):
    plan = verify(snapshot)
    if plan is None:
        raise ValueError('native_sound_output_not_declared')
    rows = [v for v in plan['bindings'] if v['business_id'] == business_id
            and v['capability_id'] == capability_id and v['capability_hash'] == capability_hash]
    if not rows:
        raise ValueError('native_sound_output_not_declared')
    return plan, copy.deepcopy(rows)
