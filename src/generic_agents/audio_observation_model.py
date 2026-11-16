"""Registered local classification of one trusted captured audio original.

This adapter owns no Task, observation writer, receiver or ACK.  Its stream
origin bridge supplies the actual bytes and configuration and retains all
source/controller/lease authorization.  Classification remains a candidate;
the empty attributes/anomalies cannot be used as measured object facts.
"""
import copy
import hashlib
from uuid import UUID

from fastapi import HTTPException

from .contracts import Manifest, digest


ADAPTER = 'local_sound_observation'
UUID_PATTERN = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
ERROR = '原音轨、采集授权或固定声音模型无法核对，未接受本次候选结果'


class SoundObservationOutputHeld(RuntimeError):
    """A validated calculation is quarantined, never a successful invocation.

    The caller may preserve it under its actual lease and failure audit.  This
    exception writes nothing and grants neither replay nor reception rights.
    Its public message deliberately contains no source, URL or model output.
    """
    def __init__(self, output, source_proof_hash):
        self.output = copy.deepcopy(output)
        self.source_proof_hash = source_proof_hash
        super().__init__('声音模型已计算，原采集授权未通过最终核对；结果仅可留存为受阻候选')


def _need(value, message=ERROR, code=409):
    if not value:
        raise HTTPException(code, message)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _trace(value):
    from .local_sound import PACK, RECIPE, RECIPE_HASH
    _need(type(value) is dict and set(value) == {'installation_id', 'pack_id', 'generation',
        'recipe_hash', 'backend', 'model_sha256', 'labels_sha256'}, code=422)
    _need(_uuid(value['installation_id']) and type(value['generation']) is int
        and value['generation'] >= 1 and value['pack_id'] == PACK and value['backend'] == 'cpu'
        and value['recipe_hash'] == RECIPE_HASH and value['model_sha256'] == RECIPE['files'][0]['sha256']
        and value['labels_sha256'] == RECIPE['files'][1]['sha256'], code=422)
    return copy.deepcopy(value)


def manifest(model_trace):
    """Build the fixed observation capability; this alone grants no execution."""
    from .local_sound import PACK, RECIPE, RECIPE_HASH
    trace = _trace(model_trace)
    suffix = int(digest({'adapter': ADAPTER, 'model_trace': trace})[:12], 16)
    return Manifest(kind='specialist_model', namespace='platform_local', name='yamnet_observation_sound',
        version='1.0.' + str(suffix), owner='本地环境声音服务',
        description='已授权采集音轨的完整环境声音候选窗口和原分数；不转写、不跳过静音，不确认现场异常、物种或独立声源数量。',
        allowed_agent_roles=['perception', 'data'],
        input_schema={'type': 'object', 'additionalProperties': False,
            'required': ['attributes', 'evidence_ids'], 'properties': {
                'attributes': {'type': 'object', 'maxProperties': 0},
                'evidence_ids': {'type': 'array', 'minItems': 1, 'maxItems': 1,
                    'items': {'type': 'string', 'pattern': UUID_PATTERN}}}},
        output_schema={'type': 'object', 'additionalProperties': False,
            'required': ['attributes', 'anomalies', 'environment_sound'], 'properties': {
                'attributes': {'type': 'object', 'maxProperties': 0},
                'anomalies': {'type': 'array', 'maxItems': 0},
                'environment_sound': {'type': 'object', 'additionalProperties': False,
                    'required': ['schema', 'deployment', 'backend', 'cloud_calls', 'input_sha256',
                        'recipe_hash', 'model', 'audio', 'classification', 'elapsed_seconds'],
                    'properties': {'schema': {'const': 'local-environment-sound-v1'},
                        'deployment': {'const': 'local'}, 'backend': {'const': 'cpu'},
                        'cloud_calls': {'type': 'integer', 'const': 0},
                        'input_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                        'recipe_hash': {'const': RECIPE_HASH}, 'model': {'type': 'object'},
                        'audio': {'type': 'object'}, 'classification': {'type': 'object'},
                        'elapsed_seconds': {'type': 'number', 'minimum': 0}}}}},
        execution={'adapter': ADAPTER, 'pack_id': PACK, 'recipe_hash': RECIPE_HASH, 'backend': 'cpu',
            'side_effect': False, 'model_trace': trace},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        specification={'candidate_only': True, 'automatic_domain_use': False, 'domain_evaluation': 'pending',
            'physical_state_verified': False, 'score_calibration': 'not_verified', 'asr_used': False,
            'vad_used': False, 'license': RECIPE['license'], 'license_evidence': RECIPE['license_evidence']})


def validate_manifest(db, p, value, *, live=True):
    """Rebuild every manifest field and check the independent install proof."""
    from .local_sound import checked_trace
    try:
        typed = value if isinstance(value, Manifest) else Manifest.model_validate(value)
        trace = _trace(typed.execution.get('model_trace'))
        _need(digest(typed.model_dump(mode='json')) == digest(manifest(trace).model_dump(mode='json')),
            '音轨模型必须使用固定本地能力，不得扩大输入、事实字段或外发权限', 422)
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(422, '音轨模型登记不符合固定本地契约') from None
    checked_trace(db, p, trace, live=live)
    return typed


def _inputs(value):
    _need(type(value) is dict and set(value) == {'attributes', 'evidence_ids'}
        and type(value['attributes']) is dict and not value['attributes']
        and type(value['evidence_ids']) is list and len(value['evidence_ids']) == 1
        and _uuid(value['evidence_ids'][0]),
        '音轨分析只接受一个已保存的原件，不接收人工字段、声道或模型分数', 422)
    return value['evidence_ids'][0]


def _capability(db, p, cap):
    _need(cap.workspace_id == p.workspace_id and cap.status == 'published'
        and cap.kind == 'specialist_model' and cap.content_hash == digest(cap.data)
        and cap.validated_hash == cap.content_hash)
    typed = validate_manifest(db, p, cap.data, live=True)
    _need(cap.namespace == typed.namespace and cap.name == typed.name and cap.release_version == typed.version)
    return typed


def _capture(db, p, evidence_id, cap_id, cap_hash, key):
    from .audio_streams import check_capture_input
    from .local_sound import configuration
    value = check_capture_input(db, p, evidence_id, cap_id, cap_hash, key=key, execution=True)
    _need(type(value) is dict and set(value) == {'evidence', 'content', 'configuration', 'source_proof'})
    evidence, content = value['evidence'], value['content']
    _need(evidence.id == evidence_id and evidence.workspace_id == p.workspace_id and type(content) is bytes
        and 0 < len(content) <= 20 * 1024 * 1024 and evidence.size_bytes == len(content)
        and evidence.sha256 == hashlib.sha256(content).hexdigest()
        and type(value['source_proof']) is dict and bool(value['source_proof']))
    try:
        selected = copy.deepcopy(configuration(value['configuration']))
    except (TypeError, ValueError):
        raise HTTPException(409, ERROR) from None
    fingerprint = digest({'evidence_id': evidence.id, 'workspace_id': evidence.workspace_id,
        'sha256': evidence.sha256, 'size_bytes': evidence.size_bytes, 'media_type': evidence.media_type,
        'configuration': selected, 'source_proof': value['source_proof']})
    return content, selected, fingerprint, digest(value['source_proof'])


def execute(db, p, cap, inputs, key):
    """Invoke the real isolated model only for the bridge's frozen original."""
    from .services import require
    from .local_sound import call, validate
    require(p, 'admin', 'operator')
    evidence_id = _inputs(inputs)
    _need(type(key) is str and bool(key), code=422)
    _capability(db, p, cap)
    cap_id, cap_hash = cap.id, cap.content_hash
    content, selected, fingerprint, source_proof_hash = _capture(db, p, evidence_id, cap_id, cap_hash, key)

    def guard():
        _need(cap.id == cap_id and cap.content_hash == cap_hash)
        _capability(db, p, cap)
        _, _, current, _ = _capture(db, p, evidence_id, cap_id, cap_hash, key)
        _need(current == fingerprint, '原音轨或处理条件已变化，本次候选未接受')

    guard()
    result = call(content, selected, guard=guard)
    validate(result, content, selected)
    output = {'attributes': {}, 'anomalies': [], 'environment_sound': result}
    try:
        guard()
    except (HTTPException, ValueError, OSError):
        raise SoundObservationOutputHeld(output, source_proof_hash) from None
    return output


def observation_marker(db, p, row):
    """Delegate family recognition, including reverse origin inventory."""
    from .audio_streams import observation_marker as marker
    return marker(db, p, row)


def handles_observation(db, p, row):
    return observation_marker(db, p, row)


def verify_observation(db, p, row, extractor_id, *, execution=True):
    """The bridge owns source integrity and authority; preserve its Evidence."""
    from .audio_streams import check_observation
    return check_observation(db, p, row, extractor_id, execution=execution)
