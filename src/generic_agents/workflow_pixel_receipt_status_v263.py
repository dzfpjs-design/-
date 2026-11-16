"""Current Task's registered pixel Runs and independent reception, read only.

The caller owns a fresh REPEATABLE READ / READ ONLY transaction. This module
does not infer, ACK, repair, acquire, commit, or declare physical change. The
short output is frozen by its real SDK Invocation. source_snapshot_hash is an
opaque digest of the internal authorized status source, not client evidence or
a claim that the complete source payload can be reconstructed from this view.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import select, text

from .contracts import Manifest, digest
from .media_daily_task_adapter import current_actor
from .models import DomainInstance, Event, Memory, WorkflowPixelRun, Audit, Job
from .services import scoped, validate
from . import native_pixel_receipts_v250 as reception
from . import native_pixel_provenance_v250 as origin
from . import pixel_question_sources_v252 as sources
from .workflow_pixel_runs_v250 import checked_saved_run, INPUT_ACTION

HANDLER = 'task_pixel_receipt_status_v263'
SCHEMA = 'workflow-pixel-receipt-status-v263'
ERROR = '原任务像素原件、运行或独立收件依据无法核对；原记录保留，未执行接收、修复或推理'
PRODUCER_PENDING = '上一步仍在保存完成记录，原成果保留，待完成后可重试'
MAX_RECORDS = 1000
WAITING = {'waiting_data'}
BLOCKED = {'producer_failed', 'blocked_integrity', 'blocked_delivery'}
UUID = {'type': 'string', 'pattern': r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'}
SHA = {'type': 'string', 'pattern': r'^[0-9a-f]{64}$'}


def need(value, message=ERROR, code=409):
    if not value: raise HTTPException(code, message)


def obj(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def input_schema():
    return obj({'task_id': copy.deepcopy(UUID)})


def output_schema():
    optional_id = {**UUID, 'type': ['string', 'null']}; optional_hash = {**SHA, 'type': ['string', 'null']}
    memory = obj({'id': UUID, 'version': {'type': 'integer', 'minimum': 1}, 'data_hash': SHA})
    run = obj({'run_id': UUID, 'run_version': {'type': 'integer', 'minimum': 1}, 'run_status': {'type': 'string'},
        'producer_status': {'type': 'string'}, 'reception_state': {'enum': ['received', *sorted(WAITING | BLOCKED)]},
        'source_record_hash': SHA, 'observation_id': optional_id, 'event_id': optional_id,
        'memory': {'oneOf': [{'type': 'null'}, memory]}, 'receipt_hash': optional_hash,
        'result_hash': optional_hash, 'request_hash': SHA, 'sources_hash': SHA,
        'detail_path': {'type': 'string'}, 'task_path': {'type': 'string'}, 'integrity_issue': {'type': ['string', 'null']}})
    return obj({'schema': {'const': SCHEMA}, 'workspace_id': UUID, 'instance_id': UUID, 'task_id': UUID,
        'as_of': {'type': 'string', 'format': 'date-time'},
        **{key: {'type': 'integer', 'minimum': 0, 'maximum': MAX_RECORDS}
            for key in ('registered_count', 'received_count', 'waiting_count', 'blocked_count')},
        'all_registered_received': {'type': 'boolean'}, 'expected_sources_verified': {'const': False},
        'snapshot_hash': SHA, 'source_snapshot_hash': SHA, 'runs': {'type': 'array', 'maxItems': MAX_RECORDS, 'items': run}})


def manifest():
    return Manifest(kind='tool', namespace=HANDLER, name=HANDLER, version='1.0.0', owner='平台任务像素收件核查',
        description='核查当前任务已登记像素Run的独立接收、等待与受阻；不读取像素数值、不代办接收，不证明预期影像全部齐全。',
        allowed_agent_roles=['data'], input_schema=input_schema(), output_schema=output_schema(),
        execution={'adapter': 'builtin', 'handler': HANDLER, 'side_effect': False,
            'input_bindings': {'task_id': 'task_identifier'}},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        budget={'max_attempts': 2, 'max_concurrency': 1}, dependencies=[],
        specification={'schema': SCHEMA, 'basis': 'registered_run_scan', 'expected_sources_verified': False,
            'consistency': 'caller_current_repeatable_read_read_only', 'ack_performed': False,
            'inference_performed': False, 'repair_performed': False})


def validate_manifest(value):
    data = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
    need(type(data) is dict and digest(data) == digest(manifest().model_dump(mode='json')),
        '像素收件核查只能使用固定数据岗位、真实任务绑定及本地只读合同', 422)
    return copy.deepcopy(data)


def _capture(db, p, task_id):
    from .workspace_api import saved_execution
    task, release, _ = saved_execution(db, p, task_id)
    instance = scoped(db, DomainInstance, task.data['instance_id'], p)
    need(release.instance_id == instance.id)
    stamp = db.scalar(text('SELECT transaction_timestamp()'))
    # A normal producer may commit its Task/SDK result before the worker's
    # final Job commit. Never mistake that window for independently received
    # data or hide it as an empty scan. This first bounded inventory proves
    # actual registration; the existing full reverse inventory follows below.
    pending_rows = list(db.scalars(select(WorkflowPixelRun).where(WorkflowPixelRun.workspace_id == p.workspace_id,
        WorkflowPixelRun.task_id == task.id).order_by(WorkflowPixelRun.id).limit(MAX_RECORDS + 1)))
    inputs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == INPUT_ACTION,
        Audit.data['task_id'].as_string() == task.id).limit(MAX_RECORDS + 1)))
    need(len(pending_rows) <= MAX_RECORDS and len(inputs) == len(pending_rows)
        and {proof.object_id for proof in inputs} == {run.id for run in pending_rows})
    for run in pending_rows:
        job = scoped(db, Job, run.job_id, p)
        if job.status in {'queued', 'running'}:
            checked_saved_run(db, p, run)
            need(False, PRODUCER_PENDING)
    rows = sources._inventory(db, p, instance.id, task.id, None, MAX_RECORDS)
    records = []; refs = []; received = waiting = blocked = 0
    for run in rows:
        context = checked_saved_run(db, p, run)
        need(run.task_id == task.id and context['task'].id == task.id and context['release'].id == release.id)
        actual = reception.receipt_projection(db, p, run); state = actual['state']
        event = scoped(db, Event, actual['event_id'], p) if actual['event_id'] else None
        if state == 'waiting_data' and event is not None and event.status == 'blocked_delivery': state = 'blocked_delivery'
        need(state == 'received' or state in WAITING | BLOCKED)
        memory = scoped(db, Memory, actual['memory_id'], p) if state == 'received' else None
        need(state != 'received' or memory is not None and actual['receipt'] is not None)
        full = {'run_id': run.id, 'run_version': run.version, 'run_data_hash': digest(run.data),
            'task_id': task.id, 'instance_id': instance.id, 'release_id': release.id,
            'producer': {'id': context['job'].id, 'status': context['job'].status},
            'invocation': {'id': context['invocation'].id, 'status': context['invocation'].status,
                'data_hash': digest(context['invocation'].data)},
            'input_audit': sources._ref(context['input_audit']),
            'result_audit': sources._ref(context['result_audit']) if context['result_audit'] else None,
            'reception': copy.deepcopy(actual), 'event_status': event.status if event else None,
            'reception_state': state}
        records.append(full)
        refs.append({'run_id': run.id, 'run_version': run.version, 'run_status': run.status,
            'producer_status': context['job'].status, 'reception_state': state, 'source_record_hash': digest(full),
            'observation_id': actual['observation_id'], 'event_id': actual['event_id'],
            'memory': {'id': memory.id, 'version': memory.version, 'data_hash': digest(memory.data)} if memory else None,
            'receipt_hash': actual['receipt']['receipt_hash'] if memory else None,
            'result_hash': run.data['result_hash'], 'request_hash': run.data['request_hash'], 'sources_hash': run.data['sources_hash'],
            'detail_path': '/api/workspace/tasks/' + task.id + '/pixel-results', 'task_path': '/api/workspace/tasks/' + task.id,
            'integrity_issue': ERROR if state in BLOCKED else None})
        if state == 'received': received += 1
        elif state in WAITING: waiting += 1
        else: blocked += 1
    source = {'schema': 'task-pixel-receipt-status-source-v263', 'workspace_id': p.workspace_id,
        'instance_id': instance.id, 'task_id': task.id, 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'as_of': origin.iso(stamp),
        'registered_run_ids': [run.id for run in rows], 'records': records, 'expected_sources_verified': False}
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'instance_id': instance.id, 'task_id': task.id,
        'as_of': source['as_of'], 'registered_count': len(rows), 'received_count': received,
        'waiting_count': waiting, 'blocked_count': blocked, 'all_registered_received': bool(rows) and received == len(rows),
        'expected_sources_verified': False, 'source_snapshot_hash': digest(source), 'runs': refs}
    need(received + waiting + blocked == len(rows))
    value['snapshot_hash'] = digest(value); validate(output_schema(), value)
    return copy.deepcopy(value)


def execute(db, p, task_id):
    try:
        from .multimodal_query_contracts_v262 import identity
        need(identity(task_id), '任务标识必须是标准UUID', 422)
        need(not db.new and not db.dirty and not db.deleted)
        need(db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
            and db.scalar(text('SHOW transaction_read_only')) == 'on', '像素收件核查须使用独立一致只读事务')
        with db.no_autoflush:
            current_actor(db, p)
            result = _capture(db, p, task_id)
            need(not db.new and not db.dirty and not db.deleted)
            return result
    except HTTPException as error:
        if error.status_code == 409 and error.detail == PRODUCER_PENDING:
            raise HTTPException(409, PRODUCER_PENDING) from None
        if error.status_code in {401, 403, 404}: raise HTTPException(error.status_code, ERROR) from None
        raise HTTPException(409, ERROR) from None
    except (KeyError, ValueError, TypeError, AttributeError, OSError, OverflowError, StopIteration):
        raise HTTPException(409, ERROR) from None
