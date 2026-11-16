"""Bounded, SELECT-only Task environmental-sound evidence.

The caller owns the current RR READ ONLY transaction and persistence of this
returned snapshot. This module owns no query lifecycle, HTTP route, registry,
model call, acquisition, ACK, daily reconciliation, or execution authorization.
"""
import copy
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, text

from . import models as m
from .contracts import digest
from .media_daily_task_adapter import current_actor
from .native_sound_data_plan import FAMILY
from . import native_sound_provenance as origin
from . import native_sound_receipts as reception
from .services import scoped
from .sound_models import EnvironmentSoundRun, SoundInput
from . import workflow_sound as workflow

SCHEMA = 'task-sound-query-evidence-snapshot-v1'
MAX_RECORDS = 1000
MAX_BYTES = 16 * 1024 * 1024
ERROR = '本次声音成果或独立依据无法核对；所属记录保留，未采用模型数值'
LIMIT_ERROR = '登记Run扫描范围超过本次完整读取上限，请缩小到明确任务；没有返回截断后的总体'
LIMITATIONS = (
    '只覆盖本次登记Run扫描范围，不证明全部预期应交原音或设备资料已收齐',
    '环境声音分类不是ASR转写；原分数未校准，不是风险概率',
    '模型窗口可重叠；窗口数不是独立事件、声源数量或真实持续时长',
    '任务对象关联不证明现场声源归属；原录音时间未知',
    '模型成果登记、独立接收和人工核查均不等于现场事实或正式验收',
)


def _need(value, message=ERROR, code=409):
    if not value:
        raise HTTPException(code, message)


def _uuid(value):
    try:
        _need(type(value) is str and str(UUID(value)) == value, '读取范围标识无效', 422)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '读取范围标识无效') from None
    return value


def _instant(value):
    _need(type(value) is datetime and value.utcoffset() is not None,
          'as_of必须为调用方当前只读事务的真实时间', 422)
    return value.astimezone(timezone.utc)


def _iso(value):
    return _instant(value).isoformat()


def _ref(row):
    return {'id': row.id, 'action': row.action, 'object_id': row.object_id,
            'actor_id': row.actor_id, 'created_at': _iso(row.created_at),
            'data_hash': digest(row.data)}


def _inventory(db, p, instance_id, task_id, limit):
    """Scan registered Runs; independent inputs/proofs must not disappear.

    Entity filtering follows cold source validation, so a corrupted mutable
    entity hint cannot silently hide an unverified Run. A Task filter narrows
    the scan using the actual Run FK, not a response's caller-supplied label.
    """
    task_ids = select(m.Task.id).where(m.Task.workspace_id == p.workspace_id,
        m.Task.data['instance_id'].as_string() == instance_id).correlate(None)
    statement = (select(EnvironmentSoundRun).outerjoin(m.Task, m.Task.id == EnvironmentSoundRun.task_id)
        .outerjoin(SoundInput, SoundInput.id == EnvironmentSoundRun.sound_input_id)
        .where(EnvironmentSoundRun.workspace_id == p.workspace_id,
            or_(EnvironmentSoundRun.data['instance_id'].as_string() == instance_id,
                m.Task.id.in_(task_ids), and_(SoundInput.workspace_id == p.workspace_id,
                    SoundInput.instance_id == instance_id))))
    if task_id is not None:
        statement = statement.where(EnvironmentSoundRun.task_id == task_id)
    rows = list(db.scalars(statement.order_by(EnvironmentSoundRun.id).limit(limit + 1)
        .execution_options(populate_existing=True)))
    _need(len(rows) <= limit, LIMIT_ERROR, 422)
    identities = {row.id for row in rows}
    selected_tasks = task_ids if task_id is None else select(m.Task.id).where(
        m.Task.workspace_id == p.workspace_id, m.Task.id == task_id)
    for action, field in ((workflow.INPUT_ACTION, m.Audit.object_id),
                          (workflow.RESULT_ACTION, m.Audit.object_id),
                          (origin.ISSUED_ACTION, m.Audit.data['run_id'].as_string())):
        query = select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == action, or_(field.in_(identities),
                m.Audit.data['task_id'].as_string().in_(selected_tasks),
                and_(m.Audit.data['instance_id'].as_string() == instance_id,
                     m.Audit.data['task_id'].as_string() == task_id) if task_id is not None
                else m.Audit.data['instance_id'].as_string() == instance_id))
        proofs = list(db.scalars(query.order_by(m.Audit.id).limit(limit + 1)))
        _need(len(proofs) <= limit, LIMIT_ERROR, 422)
        found = [proof.object_id if action != origin.ISSUED_ACTION else proof.data.get('run_id')
                 for proof in proofs]
        _need(set(found) <= identities,
              '登记Run与独立处理凭据库存不一致，没有把缺失记录当成空范围')
        # Existing Runs with missing/duplicate proof become blocked owned
        # records below. An orphan proof with no Run blocks the inventory.
    # A native source/Observation is another immutable reverse link. Missing
    # packet markers do not let a previously issued result vanish from scope.
    observations = select(m.Observation).outerjoin(m.Capability,
        m.Capability.id == m.Observation.source_id).where(m.Observation.workspace_id == p.workspace_id,
        or_(m.Observation.data[origin.PACKET]['instance_id'].as_string() == instance_id,
            and_(m.Capability.workspace_id == p.workspace_id,
                 m.Capability.namespace == 'platform_native_sound_results',
                 m.Capability.data['specification']['managed_instance'].as_string() == instance_id)))
    if task_id is not None:
        observations = observations.where(or_(
            m.Observation.data[origin.PACKET]['task_id'].as_string() == task_id,
            m.Observation.data[origin.ISSUED]['task_id'].as_string() == task_id,
            or_(False, *[m.Observation.source_record_id.startswith('EnvironmentSoundRun:' + row.id + ':')
                         for row in rows])))
    linked = list(db.scalars(observations.order_by(m.Observation.id).limit(limit + 1)))
    _need(len(linked) <= limit, LIMIT_ERROR, 422)
    for observation in linked:
        candidates = {value.get('run_id') for value in
                      (observation.data.get(origin.PACKET), observation.data.get(origin.ISSUED))
                      if type(value) is dict and type(value.get('run_id')) is str}
        record_id = observation.source_record_id or ''
        if record_id.startswith('EnvironmentSoundRun:'):
            parts = record_id.split(':')
            if len(parts) == 3:
                candidates.add(parts[1])
        _need(len(candidates) == 1 and candidates <= identities,
              '原声音观测仍有登记引用，但对应Run库存无法核对')
    return rows


def _empty_record(run, task):
    # Only actual owned database identities are retained on a broken source;
    # mutable source labels, raw audio, result values and notes are not copied.
    return {'source_family': FAMILY, 'run_id': run.id, 'task_id': task.id,
        'run_version': run.version, 'run_status': None, 'entity_id': None, 'scope_matched': None,
        'producer_release_id': None, 'producer_status': None,
        'candidate_available': False, 'data_values_available': False, 'values': None,
        'reception': {'state': 'blocked_integrity', 'observation_id': None,
            'event_id': None, 'memory': None, 'receipt_hash': None},
        'timing': {'run_started_at': None, 'model_completed_at': None,
            'registered_at': None, 'registration_day': None, 'received_at': None,
            'recorded_at': None},
        'reference': {'source_family': FAMILY, 'run_id': run.id, 'task_id': task.id,
            'producer_release_id': None, 'job_id': None, 'invocation_id': None,
            'result_hash': None, 'request_hash': None,
            'capability_id': None, 'capability_hash': None, 'sound_snapshot_hash': None,
            'input_audit': None, 'result_audit': None, 'completion_audit': None,
            'issued_audit': None, 'settlement_audit': None, 'packet_hash': None,
            'event_base_hash': None, 'review_receipt_hashes': [],
            'detail_path': '/api/sounds/runs/' + run.id,
            'task_path': '/api/workspace/tasks/' + task.id},
        'quality': {'candidate_only': True, 'not_authoritative': True,
            'score_calibration_verified': False, 'source_time_verified': False,
            'physical_state_verified': False, 'entity_state_updated': False},
        'association_basis': 'task_entity_association_not_verified_sound_attribution',
        'integrity_issue': ERROR}


def _record(db, p, instance_id, run, entity_id):
    task = scoped(db, m.Task, run.task_id, p)  # Authorization failures are never an empty source.
    value = _empty_record(run, task)
    try:
        # checked_run includes checked_saved_run plus the real saved Invocation
        # output check; receipt_projection independently validates native ACK.
        context = workflow.checked_run(db, p, run.id)
        task, release = context['task'], context['release']
        _need(task.id == run.task_id and task.data['instance_id'] == instance_id
              and run.data['instance_id'] == instance_id and release.instance_id == instance_id)
        ids = task.data['entity_ids']
        _need(type(ids) is list and len(ids) == 1)
        _uuid(ids[0])
        if entity_id is not None and ids != [entity_id]:
            return None
        status = reception.receipt_projection(db, p, run)
        _need(status['state'] in {'waiting_data', 'received', 'producer_failed',
                                 'local_handoff_pending', 'blocked_integrity'})
        input_audit = origin.row(db, p, m.Audit, run.data['input_audit_id'])
        result_audit = origin.row(db, p, m.Audit, run.data['result_audit_id']) if run.data['result_audit_id'] else None
        native = None
        if status['observation_id'] is not None:
            native = origin.checked_observation(db, p,
                origin.row(db, p, m.Observation, status['observation_id']))
        memory = None
        if status['state'] == 'received':
            _need(native is not None and status['receipt'] is not None and status['memory_id'] is not None)
            memory = origin.row(db, p, m.Memory, status['memory_id'])
            reception.verify_memory(native['observation'], memory)
            _need(native['event'].status == 'received' and native['event'].received_at is not None
                  and status['receipt']['run_id'] == run.id
                  and status['receipt']['result_hash'] == run.data['result_hash'])
        value.update(run_status=run.status, entity_id=ids[0], scope_matched=True, producer_release_id=release.id,
            producer_status=context['job'].status, candidate_available=run.status == 'succeeded',
            reception={'state': status['state'], 'observation_id': status['observation_id'],
                'event_id': status['event_id'],
                'memory': {'id': memory.id, 'version': memory.version, 'data_hash': digest(memory.data)} if memory else None,
                'receipt_hash': status['receipt']['receipt_hash'] if memory else None},
            timing={'run_started_at': _iso(input_audit.created_at),
                'model_completed_at': _iso(result_audit.created_at) if result_audit else None,
                'registered_at': native['packet']['registered_at'] if native else None,
                'registration_day': native['packet']['registration_day'] if native else None,
                'received_at': _iso(native['event'].received_at) if memory else None,
                'recorded_at': None}, integrity_issue=status['integrity_issue'])
        value['reference'].update(producer_release_id=release.id, job_id=run.job_id, invocation_id=run.invocation_id,
            result_hash=run.data['result_hash'], request_hash=run.data['request_hash'],
            capability_id=run.capability_id, capability_hash=run.data['capability_hash'],
            sound_snapshot_hash=digest(run.data['sound_snapshot']), input_audit=_ref(input_audit),
            result_audit=_ref(result_audit) if result_audit else None,
            completion_audit=_ref(native['completion_audit']) if native else None,
            issued_audit=_ref(native['issued_audit']) if native else None,
            settlement_audit=_ref(native['settlement_audit']) if native and native.get('settlement_audit') else None,
            packet_hash=digest(native['packet']) if native else None,
            event_base_hash=digest(native['base']) if native else None,
            review_receipt_hashes=[review['receipt_hash'] for review in run.data['reviews']])
        if memory is not None:
            classification = run.data['result']['classification']; candidates = classification['candidates']
            value['data_values_available'] = True
            value['values'] = {'window_count': len(classification['windows']),
                'candidate_class_count': len(candidates),
                'candidate_window_count': len({i for candidate in candidates for i in candidate['window_indices']}),
                'candidates': copy.deepcopy(candidates)}
    except HTTPException as error:
        if error.status_code in {401, 403}:
            raise
        # A scoped Run with bad original bytes/anchors remains a blocked owned
        # record. It is neither a zero result nor a missing/unauthorized Task.
        value = _empty_record(run, task)
    except (KeyError, ValueError, TypeError, AttributeError, OSError, StopIteration):
        value = _empty_record(run, task)
    value['record_hash'] = digest(value)
    return copy.deepcopy(value)


def capture(db, p, instance_id, *, as_of, task_id=None, entity_id=None, limit=1000):
    """Capture only the caller's *current* RR readonly transaction, never time travel."""
    _uuid(instance_id)
    if task_id is not None:
        _uuid(task_id)
    if entity_id is not None:
        _uuid(entity_id)
    _need(type(limit) is int and 1 <= limit <= MAX_RECORDS, '读取上限必须为1至1000的整数', 422)
    stamp = _instant(as_of)
    _need(not db.new and not db.dirty and not db.deleted,
          '只读来源捕获不能包含待保存的人工或后台修改')
    _need(db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
          and db.scalar(text('SHOW transaction_read_only')) == 'on',
          '声音来源捕获需要调用方现有的一致只读事务')
    _need(stamp == _instant(db.scalar(text('SELECT transaction_timestamp()'))),
          'as_of不是当前事务时间；不能把历史时间参数当作历史数据库快照', 422)
    with db.no_autoflush:
        current_actor(db, p)
        instance = scoped(db, m.DomainInstance, instance_id, p)
        if task_id is not None:
            task = scoped(db, m.Task, task_id, p)
            _need(task.data.get('instance_id') == instance.id, '所选任务不属于本实例', 404)
            if entity_id is not None:
                _need(entity_id in task.data['entity_ids'], '所选对象不属于原任务', 422)
        if entity_id is not None:
            entity = scoped(db, m.Entity, entity_id, p)
            _need(entity.namespace == instance.namespace, '所选对象不属于本实例范围', 404)
        rows = _inventory(db, p, instance.id, task_id, limit)
        if task_id is not None and not rows:
            # With Runs, checked_run applies the real saved_execution gate to
            # every source and retains a broken owned source as blocked. An
            # empty Task scope must still pass that gate, not mask its failure.
            from .workspace_api import saved_execution
            _, release, _ = saved_execution(db, p, task_id)
            _need(release.instance_id == instance.id)
        records = []
        for row in rows:
            value = _record(db, p, instance.id, row, entity_id)
            if value is not None:
                records.append(value)
        _need(not db.new and not db.dirty and not db.deleted,
              '来源读取产生了待写修改，未作为只读结果返回')
        body = {'schema': SCHEMA, 'source_family': FAMILY, 'workspace_id': p.workspace_id,
            'captured_by': p.user_id, 'instance_id': instance.id,
            'scope': {'task_id': task_id, 'entity_id': entity_id}, 'as_of': stamp.isoformat(),
            'consistency': 'caller_current_repeatable_read_read_only',
            'inventory': {'basis': 'registered_run_scan', 'complete': True, 'limit': limit,
                'scanned_run_ids': [row.id for row in rows],
                'returned_run_ids': [record['run_id'] for record in records],
                'expected_sources_verified': False},
            'source_integrity_complete': all(record['reception']['state'] != 'blocked_integrity'
                                             for record in records),
            'records': records, 'time_basis': 'machine_result_registered',
            'time_zone': 'Asia/Shanghai', 'recording_time_verified': False,
            'candidate_only': True, 'not_authoritative': True, 'physical_state_verified': False,
            'limitations': list(LIMITATIONS)}
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        _need(len(encoded) <= MAX_BYTES, LIMIT_ERROR, 422)
        return copy.deepcopy({**body, 'snapshot_hash': digest(body)})
