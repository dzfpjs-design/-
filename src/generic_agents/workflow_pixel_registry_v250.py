"""Register a verified pixel adapter through the ordinary capability registry."""
import copy
from sqlalchemy import select
from fastapi import HTTPException
from .contracts import digest
from .models import Audit, Capability, DomainInstance
from .services import register, validate_capability, publish, require, check_version, scoped
from .relations import lock_current_actor
from . import pixel_change_runs_v249 as reference
from .workflow_pixel_manifest_v250 import manifest, validate_manifest

ACTION = 'workflow_pixel.ability_registered_v250'


def _sample(db, p, instance_id, identity):
    row = reference.get(db, p, instance_id, identity)
    reference.need(row.status == 'succeeded', '请先完成真实本地像素分析再登记业务能力')
    return row


def options(db, p, instance_id, identity):
    row = _sample(db, p, instance_id, identity); reasons = []
    if p.role != 'admin': reasons.append('请由管理员登记专业能力')
    value = manifest(row.data['model_trace'], row.data['request']['threshold_text'], row.data['runtime_hash'])
    try: validate_manifest(db, p, value)
    except HTTPException as error: reasons.append(str(error.detail))
    return {'schema': 'workflow-pixel-registration-options-v250', 'instance_id': instance_id,
        'reference_run_id': row.id, 'reference_version': row.version, 'reference_result_hash': row.data['result_hash'],
        'can_register': not reasons, 'reasons': reasons, 'model_trace': copy.deepcopy(row.data['model_trace']),
        'threshold_text': row.data['request']['threshold_text'], 'runtime_hash': row.data['runtime_hash']}


def _records(db, p, request_id):
    values = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == ACTION,
        Audit.data['body']['request_id'].as_string() == request_id).limit(2)))
    reference.need(len(values) <= 1)
    return values


def _saved(proof, p, instance_id, expected_hash, reference_id=None):
    d = proof.data
    if proof.actor_id != p.user_id or d.get('instance_id') != instance_id:
        raise HTTPException(404, '原登记申请不属于当前账号或实例')
    reference.need(digest(d['body']) == d['body_hash'] == expected_hash and d['actor_id'] == proof.actor_id
        and d['workspace_id'] == p.workspace_id and (reference_id is None or d['reference_run_id'] == reference_id)
        and d['receipt_hash'] == digest({k: v for k, v in d.items() if k != 'receipt_hash'})
        and d['result']['body_hash'] == expected_hash and proof.object_id == d['result']['capability']['id'])
    return copy.deepcopy(d['result'])


def create(db, p, instance_id, identity, body):
    require(p, 'admin'); reference._lock_nonce(db, p, 'workflow-register:' + body.request_id)
    prior = _records(db, p, body.request_id)
    if prior: return _saved(prior[0], p, instance_id, digest(body.model_dump()), identity)
    lock_current_actor(db, p); scoped(db, DomainInstance, instance_id, p, True)
    row = _sample(db, p, instance_id, identity); check_version(row, body.version)
    value = manifest(row.data['model_trace'], row.data['request']['threshold_text'], row.data['runtime_hash'])
    validate_manifest(db, p, value)
    reference._lock_nonce(db, p, 'workflow-capability:' + digest(value.model_dump()))
    cap = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.namespace == value.namespace, Capability.name == value.name, Capability.release_version == value.version))
    if cap is None:
        cap = register(db, p, value); validate_capability(db, p, cap.id, cap.version)
        publish(db, p, cap.id, cap.version, '仅任务当次原图的本地像素候选，不授予业务事实、设备或外发权限')
    else:
        from .services import published
        published(db, p, cap.id)
        reference.need(digest(cap.data) == digest(value.model_dump()))
    result = {'schema': 'workflow-pixel-registration-v250', 'request_id': body.request_id,
        'body_hash': digest(body.model_dump()), 'reference_run_id': row.id, 'reference_result_hash': row.data['result_hash'],
        'capability': {'id': cap.id, 'hash': cap.content_hash, 'version': cap.release_version,
            'namespace': cap.namespace, 'name': cap.name, 'kind': cap.kind},
        'model_trace': copy.deepcopy(row.data['model_trace']), 'threshold_text': value.execution['threshold_text'],
        'runtime_hash': value.execution['runtime_hash']}
    payload = {'schema': 'workflow-pixel-registration-proof-v250', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'instance_id': instance_id, 'reference_run_id': row.id, 'reference_version': row.version,
        'body': body.model_dump(), 'body_hash': digest(body.model_dump()), 'result': result}
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=ACTION, object_id=cap.id,
        data={**payload, 'receipt_hash': digest(payload)}))
    db.flush(); return result


def recover(db, p, instance_id, request_id, expected_hash):
    scoped(db, DomainInstance, instance_id, p)
    prior = _records(db, p, request_id)
    result = _saved(prior[0], p, instance_id, expected_hash) if prior else None
    return {'schema': 'workflow-pixel-registration-recovery-v250', 'found': result is not None,
        'state': 'found' if result is not None else 'unknown', 'request_id': request_id,
        'expected_body_hash': expected_hash, 'result': result}
