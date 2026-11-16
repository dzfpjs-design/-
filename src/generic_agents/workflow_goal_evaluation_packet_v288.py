"""Closed ports for an independently saved Goal evaluation.

This is a new source family. Its packet identity is an evaluation record, never
a V199 packet. A result proposes a route; no port grants receipt, execution or
acceptance. Runtime entry points are supplied below only after actual records,
creator authority and upstream producer completion have been checked.
"""
import copy

from fastapi import HTTPException

from .contracts import Manifest
from .services import validate

OP = 'create_goal_evaluation_packet_v288'
CONTRACT = 'workflow-goal-evaluation-packet-contract-v288'
RESULT = 'workflow-goal-evaluation-packet-result-v288'
FLAGS = {key: False for key in ('can_dispatch', 'can_accept', 'permissions_verified',
    'source_authenticity_verified', 'current_outcome_verified', 'goal_completion_verified',
    'data_reception_verified', 'semantic_quality_verified')}
ID = {'type': 'string', 'pattern': r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'}
HASH = {'type': 'string', 'pattern': r'^[a-f0-9]{64}$'}


def need(value, detail='独立目标评估或实际来源无法核实；未补采、调用模型或完成目标', code=409):
    if not value:
        raise HTTPException(code, detail)


def _object(properties):
    return {'type': 'object', 'properties': copy.deepcopy(properties),
        'required': list(properties), 'additionalProperties': False}


def input_schema():
    from .workflow_goal_data_route_runtime_v288 import output_schema as plan_schema
    return _object({'local_plan': plan_schema()})


def packet_schema():
    return _object({'id': ID, 'evidence_id': ID, 'sha256': HASH,
        'version': {'type': 'integer', 'const': 1}, 'simulation_only': {'type': 'boolean'},
        'proposal_only': {'type': 'boolean', 'const': True},
        'cloud_authorized': {'type': 'boolean', 'const': False}})


def output_schema():
    return _object({'schema': {'const': RESULT},
        'route': {'enum': ['data_ready', 'review_required']},
        'query_status': {'type': 'string', 'minLength': 1, 'maxLength': 100},
        'reason_code': {'enum': ['evaluation_reports_sufficient', 'evaluation_requires_review']},
        'evaluation_ref': _object({'id': ID, 'record_hash': HASH}),
        'evidence_ids': {'type': 'array', 'items': ID, 'minItems': 1, 'maxItems': 1},
        'packet': packet_schema(), **{key: {'const': False} for key in FLAGS}})


def checked_result(value):
    validate(output_schema(), value)
    need(value['packet']['id'] == value['evaluation_ref']['id']
        and value['evidence_ids'] == [value['packet']['evidence_id']]
        and (value['route'] == 'data_ready') == (value['query_status'] == 'sufficient')
        and value['reason_code'] == ('evaluation_reports_sufficient'
            if value['route'] == 'data_ready' else 'evaluation_requires_review')
        and all(value[key] is False for key in FLAGS)
        and value['packet']['proposal_only'] is True
        and value['packet']['cloud_authorized'] is False
        and type(value['packet']['version']) is int)
    return copy.deepcopy(value)


def validate_manifest(manifest):
    value = manifest.model_dump(mode='json') if isinstance(manifest, Manifest) else copy.deepcopy(manifest)
    Manifest.model_validate(value)
    need(value['kind'] == 'tool' and value['allowed_agent_roles'] == ['data']
        and value['execution'] == {'adapter': 'workflow_service', 'service_actor': 'reader_service',
            'operation': OP, 'side_effect': True}
        and value['specification'] == {'schema': CONTRACT}
        and value['input_schema'] == input_schema() and value['output_schema'] == output_schema()
        and value['dependencies'] == [] and value['data_policy']['cloud_allowed'] is False
        and type(value['budget']['max_attempts']) is int and value['budget']['max_attempts'] == 1,
        '评估须登记为明确的本地数据写入能力；不接受云模型或设备权限', 422)
    return value


def _store():
    # A fixed internal module, never a request-selectable implementation.
    from . import workflow_goal_evaluation_store_v288
    return workflow_goal_evaluation_store_v288


def _inputs(value):
    return _store()._inputs(value)


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    return _store().validate_call_approval(db, p, cap, role, inputs, key, approval)


def execute(db, p, cap, inputs, key, approval):
    return _store().execute(db, p, cap, inputs, key, approval)


def reconcile(db, p, cap, inputs, key, approval, call):
    return _store().reconcile(db, p, cap, inputs, key, approval, call)


def saved_result(db, p, task, job, call, cap, inputs):
    return _store().saved_result(db, p, task, job, call, cap, inputs)


def saved_receipt(db, p, cap, inputs, key, approval, call):
    return _store().saved_receipt(db, p, cap, inputs, key, approval, call)


def retained_marker_shape(call):
    return _store().retained_marker_shape(call)


def producer_integrity_pin(db, p, task, job, inputs):
    return _store().producer_integrity_pin(db, p, task, job, inputs)


def producer_wait_pin(db, p, task, job, inputs):
    return _store().producer_wait_pin(db, p, task, job, inputs)
