"""Private generation receipts, atomically accepted with the generated release.

The audit stores fingerprints and result identity, never the submitted configuration.
The transaction-level nonce lock is global: a UUID cannot be rebound by a different
actor or workspace, even when the two submissions arrive concurrently.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from .contracts import digest
from .models import Audit, DomainInstance, InstanceRelease


ACTION = 'instance.generation.accepted'
SCHEMA = 'instance-generation-receipt-v1'
PUBLIC_KEYS = ('request_id', 'request_hash', 'generation_intent', 'instance_id',
               'release_id', 'release_number', 'configuration_hash')
CONFLICT = '生成请求编号已使用或收据无法核对，请保留原请求记录'


def lock_request(db, request_id):
    key = int(digest({'instance_generation_request': str(request_id)})[:16], 16) - (1 << 63)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def request_record(db, request_id, principal=None):
    query = select(Audit).where(Audit.action == ACTION,
        Audit.data['request_id'].as_string() == str(request_id))
    if principal is not None:
        query = query.where(Audit.workspace_id == principal.workspace_id,
                            Audit.actor_id == principal.user_id)
    rows = list(db.scalars(query.limit(2)))
    if len(rows) > 1:
        raise HTTPException(409, CONFLICT)
    return rows[0] if rows else None


def record_hash(record, data):
    return digest({'workspace_id': record.workspace_id, 'actor_id': record.actor_id,
        'action': record.action, 'object_id': record.object_id,
        'data': {key: value for key, value in data.items() if key != 'record_hash'}})


def receipt_view(record):
    return {'saved': True, **{key: record.data[key] for key in PUBLIC_KEYS},
            'actor_id': record.actor_id, 'workspace_id': record.workspace_id}


def accept_request(db, principal, body, result, *, release_actor_id):
    data = {'schema': SCHEMA, 'request_id': str(body.request_id),
        'request_hash': digest(body.model_dump(mode='json')),
        'generation_intent': body.generation_intent, 'instance_id': result['instance_id'],
        'release_id': result['id'], 'release_actor_id': release_actor_id,
        'release_number': result['release_number'],
        'configuration_hash': result['configuration_hash'], 'result_hash': digest(result)}
    record = Audit(workspace_id=principal.workspace_id, actor_id=principal.user_id,
                   action=ACTION, object_id=result['id'], data=data)
    data['record_hash'] = record_hash(record, data)
    db.add(record)
    return record


def recover_request(db, principal, request_id, release_view, *, body=None, record=None):
    """Read a bound result without draft access, writes, compilation or grants."""
    record = record if record is not None else request_record(db, request_id, principal)
    if record is None:
        return {'saved': False, 'request_id': str(request_id)}
    if record.workspace_id != principal.workspace_id or record.actor_id != principal.user_id:
        raise HTTPException(409, CONFLICT)
    data = record.data
    if not isinstance(data, dict):
        raise HTTPException(409, CONFLICT)
    try:
        valid = (data.get('schema') == SCHEMA and record.action == ACTION
            and data['request_id'] == str(request_id)
            and str(UUID(data['request_id'])) == data['request_id']
            and data['generation_intent'] in {'new_instance', 'new_release'}
            and record.object_id == data['release_id']
            and record_hash(record, data) == data.get('record_hash'))
        if body is not None:
            valid = (valid and data['request_hash'] == digest(body.model_dump(mode='json'))
                     and data['generation_intent'] == body.generation_intent)
        if not valid:
            raise ValueError()
        release = db.scalar(select(InstanceRelease).where(InstanceRelease.id == data['release_id'],
            InstanceRelease.workspace_id == principal.workspace_id))
        instance = db.scalar(select(DomainInstance).where(DomainInstance.id == data['instance_id'],
            DomainInstance.workspace_id == principal.workspace_id))
        if (release is None or instance is None or release.instance_id != instance.id
            or release.actor_id != data['release_actor_id']
            or release.release_number != data['release_number']
            or release.configuration_hash != data['configuration_hash']):
            raise ValueError()
        result = release_view(release)
        if digest(result) != data['result_hash']:
            raise ValueError()
        return {**receipt_view(record), 'release': result}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, CONFLICT) from None
