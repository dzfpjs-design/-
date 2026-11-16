"""Published extension contract for task-bound local pixel change candidates."""
import copy
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from .contracts import Manifest, digest
from .local_changer_install import PACK, RECIPE_HASH, PIPELINE_HASH, RECIPE
from .pixel_change_codec_v249 import RUNTIME_HASH

ADAPTER = 'local_pixel_change'
FAMILY = 'workflow_pixel_change_candidate_v250'
SHA = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
UUID = {'type': 'string', 'format': 'uuid'}


def closed(properties, required=None):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
            'required': list(properties) if required is None else required}


def image_schema():
    return closed({'id': UUID, 'name': {'type': 'string', 'maxLength': 250}, 'sha256': SHA,
        'media_type': {'const': 'image/png'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 20 * 1024 * 1024},
        'width': {'type': 'integer', 'minimum': 32}, 'height': {'type': 'integer', 'minimum': 32},
        'created_at': {'type': 'string', 'format': 'date-time'}})


def pair_schema():
    return closed({'schema': {'const': 'workflow-pixel-pair-v250'}, 'before': image_schema(), 'after': image_schema(),
        'input_order': {'const': 'before_after'}, 'pixel_correspondence_confirmed': {'const': True},
        'full_image_present_confirmed': {'const': True}, 'coordinate_space': {'const': 'pixel'},
        'georeference_available': {'const': False}, 'pair_hash': SHA})


def artifact_schema():
    return closed({'kind': {'enum': ['valid_mask', 'probability_data', 'probability_preview', 'candidate_overlay', 'binary_mask', 'manifest']},
        'evidence_id': UUID, 'sha256': SHA, 'size_bytes': {'type': 'integer', 'minimum': 1}, 'media_type': {'type': 'string'}})


def result_schema():
    count = {'type': 'integer', 'minimum': 0, 'maximum': 16000000}
    return closed({'schema': {'const': 'workflow-pixel-result-v250'}, 'coordinate_space': {'const': 'pixel'},
        'georeference_available': {'const': False}, 'candidate_only': {'const': True}, 'cloud_calls': {'const': 0},
        'object_state_updated': {'const': False}, 'cloud_shadow_screening': {'const': 'not_applied'},
        'width': {'type': 'integer', 'minimum': 32}, 'height': {'type': 'integer', 'minimum': 32},
        **{k: copy.deepcopy(count) for k in ('total_pixels', 'common_valid_pixels', 'unknown_pixels', 'candidate_pixels')},
        'threshold_text': {'type': 'string', 'pattern': '^0\\.[0-9]{1,6}$'},
        'inference_seconds_text': {'type': 'string', 'maxLength': 100}, 'result_hash': SHA,
        'artifacts': {'type': 'array', 'minItems': 6, 'maxItems': 6, 'items': artifact_schema()}})


def manifest(model_trace, threshold_text, runtime_hash=RUNTIME_HASH):
    expected = {'installation_id', 'pack_id', 'generation', 'recipe_hash', 'pipeline_hash', 'weights_sha256', 'backend'}
    try:
        from uuid import UUID as UUIDType
        import re
        valid = (type(model_trace) is dict and set(model_trace) == expected
            and str(UUIDType(model_trace['installation_id'])) == model_trace['installation_id']
            and model_trace['pack_id'] == PACK and type(model_trace['generation']) is int and model_trace['generation'] >= 1
            and model_trace['recipe_hash'] == RECIPE_HASH and model_trace['pipeline_hash'] == PIPELINE_HASH
            and model_trace['weights_sha256'] == RECIPE['files'][0]['sha256'] and model_trace['backend'] == 'cpu'
            and type(threshold_text) is str and re.fullmatch(r'0\.[0-9]{1,6}', threshold_text)
            and Decimal(0) < Decimal(threshold_text) < Decimal(1) and runtime_hash == RUNTIME_HASH)
    except (ValueError, TypeError, KeyError, InvalidOperation):
        valid = False
    if not valid:
        raise HTTPException(422, '像素专业能力需要固定本地模型、阈值和像素处理版本')
    label = digest({'model_trace': model_trace, 'threshold_text': threshold_text, 'runtime_hash': runtime_hash})[:24]
    return Manifest(kind='specialist_model', namespace=ADAPTER, name='candidates_' + label, version='1.0.0',
        owner='本地专业模型网关', description='使用任务当次两张原始图片计算像素变化候选；不认定实际对象变化或现场异常。',
        allowed_agent_roles=['perception', 'data'], input_schema=closed({'task_id': UUID, 'pair_ref': pair_schema()}),
        output_schema=closed({'pixel_run_id': UUID, 'attributes': closed({
            'candidate_pixels': {'type': 'integer', 'minimum': 0}, 'common_valid_pixels': {'type': 'integer', 'minimum': 0},
            'unknown_pixels': {'type': 'integer', 'minimum': 0}, 'not_authoritative': {'const': True}}),
            'anomalies': {'type': 'array', 'maxItems': 0}, 'pixel_comparison': result_schema()}),
        execution={'adapter': ADAPTER, 'model_trace': copy.deepcopy(model_trace), 'runtime_hash': runtime_hash,
            'threshold_text': threshold_text, 'side_effect': False,
            'input_bindings': {'task_id': 'task_identifier', 'pair_ref': 'task_pixel_pair_refs'}},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        budget={'max_attempts': 2, 'max_concurrency': 1},
        specification={'candidate_only': True, 'coordinate_space': 'pixel', 'source_family': FAMILY,
            'automatic_domain_use': False, 'production_license_verified': False, 'domain_evaluation': 'pending'})


def validate_manifest(db, p, value, *, live=True):
    execution = value.execution
    expected = manifest(execution.get('model_trace'), execution.get('threshold_text'), execution.get('runtime_hash'))
    if digest(value.model_dump()) != digest(expected.model_dump()):
        raise HTTPException(422, '像素能力不能更换来源、扩大外发范围或改成业务事实输出')
    if live:
        from .pixel_change_runs_v249 import _model
        from .local_changer_install import trace
        current = _model(db, p, execution['model_trace']['installation_id'], physical=True)
        if digest(trace(current)) != digest(execution['model_trace']):
            raise HTTPException(409, '本地模型代次已变化，请明确登记新版本；原结果保留')
