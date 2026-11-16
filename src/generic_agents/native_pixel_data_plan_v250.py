"""Deterministic, additive storage contract for new pixel workflow releases.

This is a design, never a field fact, permission or a reception receipt. Old
snapshots without a pixel adapter keep their original bytes and requirements.
"""
import copy

from .contracts import digest

KEY = 'native_pixel_results_v250'
SCHEMA = 'instance-native-pixel-data-plan-v250'
ADAPTER = 'local_pixel_change'
FAMILY = 'workflow_pixel_change_candidate_v250'
ARTIFACTS = ('binary_mask', 'candidate_overlay', 'manifest', 'probability_data',
             'probability_preview', 'valid_mask')


def _need(value):
    if not value:
        raise ValueError('native_pixel_data_plan_invalid')


def _steps(snapshot):
    _need(type(snapshot) is dict and type(snapshot.get('businesses', [])) is list)
    runtime = snapshot.get('semantic_runtime_plan') or {}
    _need(type(runtime) is dict and type(runtime.get('graph_adoptions', [])) is list)
    graphs = {item['binding']['business_name']: item['binding']['graph']
              for item in runtime.get('graph_adoptions', [])
              if type(item) is dict and type(item.get('binding')) is dict
              and type(item['binding'].get('graph')) is dict}
    for business in snapshot.get('businesses', []):
        _need(type(business) is dict)
        graph = graphs.get(business['name'])
        if graph is not None:
            for node in graph['nodes']:
                if node.get('kind') == 'ability':
                    yield business, node.get('source_step_id') or node['id'], node['id'], node.get('execution', {})
        else:
            flow = business.get('flow')
            if type(flow) is not dict:
                continue  # Unavailable legacy businesses may lack a flow.
            for step in flow.get('steps', []):
                _need(type(step) is dict)
                yield business, step.get('id'), None, step.get('execution') or {}


def build(snapshot):
    """Return None for genuinely unrelated releases; do not add legacy tags."""
    bindings = []
    for business, step_id, node_id, pin in _steps(snapshot):
        if type(pin) is not dict or pin.get('adapter') != ADAPTER:
            continue
        _need(type(step_id) is str and bool(step_id)
              and type(pin.get('capability_id')) is str
              and type(pin.get('capability_hash')) is str and len(pin['capability_hash']) == 64
              and not set(pin['capability_hash']) - set('0123456789abcdef')
              and type(pin.get('release_version')) is str
              and pin.get('agent_role') in {'perception', 'data'}
              and type(pin.get('output_schema')) is dict)
        schema = copy.deepcopy(pin['output_schema'])
        bindings.append({'business_id': business['id'], 'business_name': business['name'],
            'step_id': step_id, 'node_id': node_id, 'capability_id': pin['capability_id'],
            'capability_hash': pin['capability_hash'], 'release_version': pin['release_version'],
            'agent_role': pin['agent_role'], 'output_schema': schema,
            'output_schema_hash': digest(schema), 'source_family': FAMILY,
            'storage': {'machine_result': 'workflow_pixel_runs_v250',
                'originals_and_artifacts': 'immutable_evidence',
                'handoff': 'observation_and_outbox', 'reception': 'independent_candidate_memory',
                'human_work': 'original_task_receipts_and_independent_human_audit'},
            'artifact_kinds': list(ARTIFACTS), 'candidate_only': True,
            'coordinate_space': 'pixel', 'georeference_available': False,
            'registration_day_basis': 'issued_at_asia_shanghai',
            'source_time_verified': False, 'object_state_updated': False})
    if not bindings:
        return None
    bindings.sort(key=lambda value: (value['business_id'], value['step_id'], value['node_id'] or ''))
    _need(len({(v['business_id'], v['step_id'], v['node_id']) for v in bindings}) == len(bindings))
    value = {'schema': SCHEMA, 'source_family': FAMILY, 'bindings': bindings,
        'time_zone': 'Asia/Shanghai', 'date_basis': 'machine_result_registered',
        'requires_independent_ack': True, 'field_truth_verified': False,
        'automatic_domain_use': False, 'deletion_authorized': False,
        'local_handoff_recovery': 'explicit_original_result_settlement_v250'}
    return {**value, 'plan_hash': digest(value)}


def verify(snapshot):
    expected = build(snapshot)
    if expected is None:
        _need(KEY not in snapshot)
        return None
    _need(type(snapshot.get(KEY)) is dict and digest(snapshot[KEY]) == digest(expected))
    return copy.deepcopy(expected)


def declared(snapshot, business_id, capability_id, capability_hash):
    plan = verify(snapshot)
    _need(plan is not None)
    values = [item for item in plan['bindings'] if item['business_id'] == business_id
              and item['capability_id'] == capability_id and item['capability_hash'] == capability_hash]
    _need(bool(values))
    return plan, copy.deepcopy(values)
