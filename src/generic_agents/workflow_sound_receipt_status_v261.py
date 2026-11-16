"""Read-only receipt status of a Task's registered environmental-sound Runs.

The caller supplies an existing REPEATABLE READ, READ ONLY transaction and
persists the returned output in its real Invocation trace. This module neither
changes transaction mode nor commits. A later ACK requires a new invocation;
it cannot change this returned snapshot. Registered Runs are not an inventory
of all expected originals. No audio or class scores leave this adapter. The
source reader retains its existing original-integrity checks; this adapter
performs no ACK, repair, or acquisition.
"""
import copy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from .contracts import Manifest, digest
from .media_daily_task_adapter import current_actor
from .models import DomainInstance, Task
from .services import scoped
from . import task_sound_query_evidence as evidence

HANDLER = 'task_sound_receipt_status_v261'
SCHEMA = 'workflow-sound-receipt-status-v261'
ERROR = '任务声音收件依据无法核对；原记录保留，未执行接收、修复或采集'
WAITING = frozenset({'waiting_data', 'local_handoff_pending'})
BLOCKED = frozenset({'blocked_integrity', 'producer_failed'})
UUID_PATTERN = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
SHA_PATTERN = r'^[0-9a-f]{64}$'


def need(value, message=ERROR, code=409):
    if not value:
        raise HTTPException(code, message)


def obj(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields),
        'additionalProperties': False}


def input_schema():
    return obj({'task_id': {'type': 'string', 'pattern': UUID_PATTERN}})


def output_schema():
    identity = {'type': 'string', 'pattern': UUID_PATTERN}
    optional_id = {'type': ['string', 'null'], 'pattern': UUID_PATTERN}
    hashed = {'type': 'string', 'pattern': SHA_PATTERN}
    optional_hash = {'type': ['string', 'null'], 'pattern': SHA_PATTERN}
    memory = obj({'id': identity, 'version': {'type': 'integer', 'minimum': 1},
        'data_hash': hashed})
    run = obj({'run_id': identity, 'run_version': {'type': 'integer', 'minimum': 1},
        'run_status': {'type': ['string', 'null']},
        'reception_state': {'enum': ['received', *sorted(WAITING), *sorted(BLOCKED)]},
        'source_record_hash': hashed, 'observation_id': optional_id,
        'event_id': optional_id, 'memory': {'anyOf': [memory, {'type': 'null'}]},
        'receipt_hash': optional_hash, 'result_hash': optional_hash,
        'request_hash': optional_hash, 'sound_snapshot_hash': optional_hash,
        'detail_path': {'type': 'string'}, 'task_path': {'type': 'string'},
        'integrity_issue': {'type': ['string', 'null']}})
    fields = {'schema': {'const': SCHEMA}, 'workspace_id': identity,
        'instance_id': identity, 'task_id': identity,
        'as_of': {'type': 'string', 'format': 'date-time'}}
    fields.update({key: {'type': 'integer', 'minimum': 0, 'maximum': evidence.MAX_RECORDS}
        for key in ('registered_count', 'received_count', 'waiting_count', 'blocked_count')})
    fields.update({'all_registered_received': {'type': 'boolean'},
        'expected_sources_verified': {'const': False}, 'snapshot_hash': hashed,
        'source_snapshot_hash': hashed,
        'runs': {'type': 'array', 'maxItems': evidence.MAX_RECORDS, 'items': run}})
    return obj(fields)


def manifest():
    """Register, validate and publish through the ordinary capability API."""
    return Manifest(kind='tool', namespace='task_sound_receipt_status_v261', name=HANDLER,
        version='1.0.0', owner='平台任务声音收件核查',
        description='核查当前任务已登记声音Run的独立接收、等待与受阻状态；'
            '不输出原音或分数，不证明预期原音全部齐全，不代办接收或修复。',
        allowed_agent_roles=['data'], input_schema=input_schema(), output_schema=output_schema(),
        execution={'adapter': 'builtin', 'handler': HANDLER, 'side_effect': False,
            'input_bindings': {'task_id': 'task_identifier'}},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        budget={'max_attempts': 1, 'max_concurrency': 1}, dependencies=[],
        specification={'schema': SCHEMA, 'basis': 'registered_run_scan',
            'consistency': 'caller_current_repeatable_read_read_only',
            'expected_sources_verified': False, 'ack_performed': False,
            'repair_performed': False, 'acquisition_performed': False})


def validate_manifest(value):
    data = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
    need(type(data) is dict and digest(data) == digest(manifest().model_dump(mode='json')),
        '声音收件核查须固定数据岗位、真实任务绑定、本地只读及完整输出合同', 422)
    return copy.deepcopy(data)


def execute(db, p, task_id):
    """Return a detached, count-only view of the actual current Task snapshot.

    ``snapshot_hash`` hashes this short output without its own hash field.
    ``source_snapshot_hash`` is an opaque anchor to the complete source capture,
    whose payload is not included here and cannot be reconstructed from this
    output. ``source_record_hash`` and the Run/receipt pins retain the basis for
    each status without returning candidate values. Missing or unauthorized
    scopes raise; damaged owned ACKs remain blocked.
    """
    need(type(task_id) is str, '任务标识必须是标准UUID', 422)
    try:
        need(str(UUID(task_id)) == task_id, '任务标识必须是标准UUID', 422)
    except ValueError:
        raise HTTPException(422, '任务标识必须是标准UUID') from None
    need(not db.new and not db.dirty and not db.deleted,
        '声音收件核查不能包含待保存修改')
    need(db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        and db.scalar(text('SHOW transaction_read_only')) == 'on',
        '声音收件核查需要调用方现有的一致只读事务')
    stamp = db.scalar(text('SELECT transaction_timestamp()'))
    with db.no_autoflush:
        current_actor(db, p)
        task = scoped(db, Task, task_id, p)
        instance_id = task.data.get('instance_id')
        need(type(instance_id) is str)
        instance = scoped(db, DomainInstance, instance_id, p)
        source = evidence.capture(db, p, instance.id, as_of=stamp, task_id=task.id)
        runs = []
        received = waiting = blocked = 0
        for record in source['records']:
            reception = record['reception']; reference = record['reference']
            state = reception['state']
            if state == 'received':
                received += 1
            elif state in WAITING:
                waiting += 1
            else:
                need(state in BLOCKED)
                blocked += 1
            runs.append({'run_id': record['run_id'], 'run_version': record['run_version'],
                'run_status': record['run_status'], 'reception_state': state,
                'source_record_hash': record['record_hash'],
                'observation_id': reception['observation_id'], 'event_id': reception['event_id'],
                'memory': copy.deepcopy(reception['memory']), 'receipt_hash': reception['receipt_hash'],
                **{key: reference[key] for key in ('result_hash', 'request_hash',
                    'sound_snapshot_hash', 'detail_path', 'task_path')},
                'integrity_issue': record['integrity_issue']})
        need(not db.new and not db.dirty and not db.deleted)
        body = {'schema': SCHEMA, 'workspace_id': p.workspace_id,
            'instance_id': instance.id, 'task_id': task.id, 'as_of': source['as_of'],
            'registered_count': len(runs), 'received_count': received,
            'waiting_count': waiting, 'blocked_count': blocked,
            'all_registered_received': bool(runs) and received == len(runs),
            'expected_sources_verified': False, 'source_snapshot_hash': source['snapshot_hash'],
            'runs': runs}
        return copy.deepcopy({**body, 'snapshot_hash': digest(body)})
