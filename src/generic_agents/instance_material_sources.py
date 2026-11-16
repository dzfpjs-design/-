"""Read-only, typed material sources for DataQuery V2.

Records describe received/processed material, never current field conditions.
Snapshots retain exact selected publications; recheck never searches a newer
revision, repairs a receipt, starts a Job, or grants source execution authority.
"""
import copy
import math
from dataclasses import dataclass
from datetime import timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from . import models as m
from .contracts import digest
from .services import scoped

BUNDLE = 'material-source-bundle-v2'
SNAPSHOT = 'material-source-snapshot-v2'
LIMIT = 200
SCAN_LIMIT = 1000
SCOPE_KEYS = {'instance_id', 'release_id', 'business_ids', 'entity_ids', 'batch_day',
              'source_kinds', 'document_search_ids'}
KINDS = ('observation_day', 'supplement_captures', 'document_searches')
ERROR = '原资料范围、固定来源或独立收件依据无法核对；未以当前资料替换'
TIME_NOTE = '采集批次、接收和处理时间不证明现场拍摄时间；现场时间未核验。'
PHYSICAL_NOTE = '已核对来源与接收元数据，未对本次资料的全部原件字节重新检查。'
EXPECTED_NOTE = '没有完整预期来源清单，不能证明本批次全部应交资料已收齐。'
DOCUMENT_NOTE = '仅采用所选正式正文搜索；初始化报告/SetupMaterial及未进入该索引的资料未覆盖。'
DATE_BASIS = {
    'observation_day': '原日整理归集日；也可按原记录的接收/处理日命中，不是现场日期。',
    'supplement_captures': '原采集任务发起批次日期（中国时区），迟到仍属原批次。',
    'document_searches': '固定文档版本的登记日期，不表示文档形成、现场拍摄或接收日期。',
}


def need(value, message=ERROR, status=409):
    if not value:
        raise HTTPException(status, message)


def _uuid(value):
    return type(value) is str and str(UUID(value)) == value


def _ids(value, *, uuids=False, nonempty=False, maximum=30):
    need(type(value) is list and len(value) <= maximum and (bool(value) or not nonempty)
         and all(type(x) is str and 0 < len(x) <= 100 for x in value)
         and len(value) == len(set(value)), '资料范围列表格式不正确', 422)
    if uuids:
        need(all(_uuid(x) for x in value), '资料范围编号不正确', 422)


def _scope_value(scope):
    from .instance_observation_day_v216 import requested_day
    try:
        need(type(scope) is dict and set(scope) == SCOPE_KEYS, '资料范围字段不完整', 422)
        need(_uuid(scope['instance_id']) and _uuid(scope['release_id']), '实例版本编号不正确', 422)
        _ids(scope['business_ids'], nonempty=True)
        _ids(scope['entity_ids'], uuids=True)
        _ids(scope['source_kinds'], nonempty=True, maximum=3)
        _ids(scope['document_search_ids'], uuids=True, maximum=20)
        need(set(scope['source_kinds']) <= set(KINDS), '资料来源类型不受支持', 422)
        need(scope['entity_ids'] or scope['source_kinds'] == ['document_searches'],
             '传感资料必须明确选择对象，空列表不表示所有对象', 422)
        need(not scope['document_search_ids'] or 'document_searches' in scope['source_kinds'],
             '正文搜索未包含在所选资料类型中', 422)
        need(type(scope['batch_day']) is str, '资料范围必须保留明确采集批次日期', 422)
        requested_day(scope['batch_day'])
        return copy.deepcopy(scope)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '资料范围格式不正确') from None


def _authority(db, p, scope, *, live):
    from .media_daily_task_adapter import current_actor
    current_actor(db, p)
    instance = scoped(db, m.DomainInstance, scope['instance_id'], p)
    release = scoped(db, m.InstanceRelease, scope['release_id'], p)
    cap = scoped(db, m.Capability, release.package_id, p)
    need(release.instance_id == instance.id and release.status == 'prepared'
         and cap.kind == 'domain_package' and cap.namespace == instance.namespace
         and cap.content_hash == cap.validated_hash == digest(cap.data)
         and digest(release.data['snapshot']) == release.configuration_hash
         and cap.data['specification']['configuration_hash'] == release.configuration_hash)
    from .instance_data_receipts import verify_release
    verify_release(db, p, release)
    if live:
        from .instance_api import active
        actual, current = active(db, p, instance.id)
        need(actual.id == instance.id and current.id == release.id,
             '实例启用版本已改变，请重新核对资料范围')
    else:
        from .instance_semantic_runtime_dispatch import modern, verify_release as verify_runtime
        if release.data['snapshot'].get('semantic_runtime_plan', {}).get('schema') in {
                'instance-full-semantic-runtime-plan-v3', 'instance-full-semantic-runtime-plan-v4',
                'instance-object-graph-runtime-plan-v171', 'instance-business-flow-graph-runtime-v1',
                'instance-authored-workflow-runtime-v208'}:
            modern(release.data['snapshot']).verify_release(db, p, release, live=False)
        else:
            verify_runtime(db, p, release)
    businesses = release.data['snapshot']['businesses']
    need(type(businesses) is list and all(type(b) is dict for b in businesses))
    enabled = {b['id']: b for b in businesses if b.get('available') is True}
    if live:
        enabled = {key: value for key, value in enabled.items()
                   if key in instance.data.get('enabled_business_ids', [])}
    need(set(scope['business_ids']) <= set(enabled), '业务不属于该实例已启用范围', 403)
    for identity in scope['entity_ids']:
        entity = scoped(db, m.Entity, identity, p)
        need(entity.namespace == instance.namespace, '对象不属于所选实例', 403)
        if live:
            from .entity_release_compat import require_entity
            require_entity(db, p, instance, release, entity, lock_sources=False)
    return instance, release, enabled


def _scope_pin(instance, release):
    return {'workspace_id': instance.workspace_id, 'instance_id': instance.id,
            'release_id': release.id, 'package_id': release.package_id,
            'configuration_hash': release.configuration_hash}


def validate_scope(db, p, scope):
    """Creation-time real membership/scope check, without collecting files."""
    scope = _scope_value(scope)
    try:
        with db.no_autoflush:
            _authority(db, p, scope, live=True)
            from .runtime_document_models import RuntimeDocumentSearch as Search
            for identity in scope['document_search_ids']:
                search = scoped(db, Search, identity, p)
                need(search.instance_id == scope['instance_id'] and search.release_id == scope['release_id'],
                     '正文搜索不属于所选实例版本', 403)
        return scope
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, ERROR) from None


def _reference(key, kind, label, *, version='', task_id=None, plan_id=None,
               run_id=None, path=None, excerpt=None, verified=True, limitations=()):
    return {'key': key, 'kind': kind, 'label': label, 'version_label': version,
            'task_id': task_id, 'plan_id': plan_id, 'run_id': run_id,
            'source_path': path, 'excerpt': excerpt, 'metadata_verified': bool(verified),
            'physical_recheck': 'not_checked', 'limitations': list(limitations)}


def _record(key, kind, label, scope, *, business_ids=None, entity_ids=None,
            capture=None, processing=None, delivery='unknown', received_at=None,
            references=(), text=None, limitations=()):
    return {'key': key, 'kind': kind, 'label': label,
            'business_ids': list(scope['business_ids'] if business_ids is None else business_ids),
            'entity_ids': list(scope['entity_ids'] if entity_ids is None else entity_ids),
            'batch_day': scope['batch_day'], 'received_at': received_at,
            'scene_observed_at': None, 'scene_time_trust': 'unknown',
            'capture': capture, 'processing': processing, 'delivery': {'status': delivery},
            'references': list(references), 'text': text, 'limitations': list(limitations)}


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _task(db, p, scope, identity):
    from .workspace_api import saved_execution
    task, release, business = saved_execution(db, p, identity)
    need(task.data['instance_id'] == scope['instance_id'] and release.id == scope['release_id']
         and business['id'] in scope['business_ids'])
    ids = task.data['entity_ids']
    need(type(ids) is list and ids and set(ids) <= set(scope['entity_ids']))
    from .entity_release_compat import verify_task_binding_snapshot
    verify_task_binding_snapshot(task, release)
    return task, {'task_id': task.id, 'package_id': task.package_id,
        'instance_id': task.data['instance_id'], 'release_id': release.id,
        'business_id': business['id'], 'entity_ids': copy.deepcopy(ids),
        'object_snapshots': copy.deepcopy(task.data.get('object_snapshots')),
        'object_definition_bindings': copy.deepcopy(task.data.get('object_definition_bindings')),
        'object_definition_bindings_hash': task.data.get('object_definition_bindings_hash')}


def _delivery_status(value):
    return {'received': 'received', 'pending_delivery': 'pending',
            'not_published': 'pending', 'blocked_delivery': 'blocked'}.get(value, 'unknown')


def _entry(provider, ref, record):
    return {'provider_id': provider, 'source_ref': copy.deepcopy(ref),
            'record': copy.deepcopy(record), 'record_hash': digest(record)}


def _day_basis(modality, record, day, selected):
    fields = ('received_day',) if modality == 'video' else (
        'origin_upload_day', 'processing_request_day', 'processing_completed_day')
    axes = ['collection_day'] if day == selected else []
    axes.extend(key for key in fields if record.get(key) == selected)
    declared = record.get('source_clock_utc') == 'user_supplied_unverified' or any(
        isinstance(source, dict) and source.get('time_basis') == 'user_declared_unverified'
        for source in record.get('sources', []))
    return {'definition': DATE_BASIS['observation_day'], 'collection_day': day,
        'selected_day': selected, 'matched_axes': axes,
        'reader_definition': record.get('date_basis'),
        'reader_clock_label': record.get('source_clock_utc', 'unknown'),
        'scene_time_trust': 'unverified' if declared else 'unknown'}


def _day_capture(db, p, modality, record):
    if modality != 'video' or record.get('mode') == 'upload':
        return None
    session = scoped(db, m.StreamSession, record['id'], p)
    status = record['capture_status']
    actual = None
    if status in {'captured', 'partial'}:
        from .media import validate_completion
        from .media_daily_task_adapter import one
        need(session.status == status and session.evidence_id == record.get('evidence_id'))
        validate_completion(session.data['receipt'], session.data['seconds'])
        job = scoped(db, m.Job, session.data['job_id'], p)
        saved = one(db, p, 'media.capture.saved', session.id)
        need(saved.actor_id == job.data['actor_id'] and saved.data == {
            'partial': status == 'partial', 'cloud_calls': 0})
        actual = _number(session.data['receipt']['duration_seconds'])
    reason = '仅部分读取，未表示所要求片段完整。' if status == 'partial' else (
        '原读取未保存有效视频，实际时长未知。' if status in {'failed', 'blocked', 'cancelled'} else None)
    return {'status': status, 'requested_seconds': _number(session.data['seconds']),
            'actual_seconds': actual, 'reason': reason}


def _day_read(db, p, scope, ref):
    from . import media_daily_delivery as delivery
    from . import instance_observation_day_v216 as day
    event = scoped(db, m.Event, ref['event_id'], p)
    proof = delivery.checked(db, p, event)
    delivery.validate_sources(db, p, proof['body'])
    body = proof['body']
    need(body['payload_hash'] == ref['payload_hash'] and body['modality'] == ref['modality']
         and body['daily_id'] == ref['daily_id'] and body['daily_revision'] == ref['revision'])
    matches = [x for x in body['summary']['records'] if x['id'] == ref['source_id']
               and (x.get('key') or x['id']) == ref['record_key']]
    need(len(matches) == 1 and digest(matches[0]) == ref['source_record_hash'])
    record = matches[0]
    need(ref['date_basis'] == _day_basis(body['modality'], record, body['day'], scope['batch_day']))
    need(ref['capture'] == _day_capture(db, p, body['modality'], record))
    if ref['task_binding'] is not None:
        task, binding = _task(db, p, scope, ref['task_binding']['task_id'])
        need(binding == ref['task_binding'] and record.get('task_id') == task.id
             and record.get('instance_id') == scope['instance_id'])
    else:
        target = record.get('business_target')
        need(type(target) is dict and target['instance_id'] == scope['instance_id']
             and target['instance_release_id'] == scope['release_id']
             and target['business_id'] in scope['business_ids']
             and target['entity_id'] in scope['entity_ids'])
        run = scoped(db, m.VideoRun, record['run_id'], p)
        actual = (run.data.get('source_context') or {}).get('domain_binding')
        need(type(actual) is dict and all(actual.get(k) == target[k] for k in
             ('instance_id', 'instance_release_id', 'business_id', 'entity_id')))
    need(day._matches_day(body['modality'], record, body['day'], scope['batch_day']))
    return proof


def _collect_day(db, p, scope):
    from . import instance_observation_day_v216 as day
    view = day.read_day(db, p, scope['instance_id'], scope['batch_day'])
    need(view['release_id'] == scope['release_id'])
    entries, limitations = [], []
    complete, truncated = True, False
    for section in view['sections']:
        need(not any(issue['code'] in {'unverified_assignment', 'delivery_unverified',
             'source_integrity', 'day_read_failed'} for issue in section['issues']),
             '所选日整理有来源或独立接收依据无法核对，不能显示为空资料')
        complete = complete and section['count_complete']
        truncated = truncated or section['truncated']
        limitations.extend(x['message'] for x in section['issues'])
        for item in section['records']:
            binding = item['binding']; ids = [e['id'] for e in binding['entities']]
            if binding['business_id'] not in scope['business_ids'] or not set(ids) <= set(scope['entity_ids']):
                continue
            ack = item['collection']['ack']
            # A damaged/unpublished assignment cannot become an authorized
            # record by copying the view's cached label.
            need(ack['integrity_verified'] is True and ack['event_id'])
            from . import media_daily_delivery as delivery
            proof = delivery.checked(db, p, scoped(db, m.Event, ack['event_id'], p))
            records = [r for r in proof['body']['summary']['records'] if r['id'] == binding['source_id']]
            need(len(records) == 1)
            original = records[0]
            _, task_binding = _task(db, p, scope, binding['task_id']) if binding['task_id'] else (None, None)
            ref = {'event_id': ack['event_id'], 'payload_hash': proof['body']['payload_hash'],
                'modality': item['modality'], 'daily_id': item['collection']['id'],
                'revision': item['collection']['revision'], 'source_id': binding['source_id'],
                'record_key': original.get('key') or original['id'],
                'source_record_hash': digest(original), 'task_binding': task_binding,
                'date_basis': _day_basis(item['modality'], original, proof['body']['day'], scope['batch_day']),
                'capture': _day_capture(db, p, item['modality'], original)}
            _day_read(db, p, scope, ref)
            key = 'day:'+item['id']
            refs = [_reference(key+':receipt', 'daily_receipt', item['title'],
                version='日修订 '+str(item['collection']['revision']), task_id=binding['task_id'],
                run_id=original.get('run_id'), path=item['detail_url'], limitations=(PHYSICAL_NOTE,))]
            processing = {'status': item['status'], 'frames_saved': original.get('saved_frames')}
            record = _record(key, 'day_record', item['title'], scope,
                business_ids=[binding['business_id']], entity_ids=ids, capture=copy.deepcopy(ref['capture']),
                processing=processing,
                delivery=_delivery_status(ack['status']), received_at=ack['received_at'], references=refs,
                limitations=(TIME_NOTE, PHYSICAL_NOTE, '仅关联原任务对象集合，不分摊为每个对象的观测。'))
            record['batch_day'] = proof['body']['day']
            record['limitations'].append(DATE_BASIS['observation_day'])
            record['scene_time_trust'] = ref['date_basis']['scene_time_trust']
            if record['scene_time_trust'] == 'unverified':
                record['limitations'].append('原来源有填报/声明时间，未独立核验；未转换成已确认现场时间。')
            entries.append(_entry('observation_day', ref, record))
    return entries, limitations, complete, truncated


def _supplement_receipt(db, p, session, fixed=None):
    from . import media_daily_delivery as delivery
    if fixed is not None:
        events = [scoped(db, m.Event, fixed['event_id'], p)]
    else:
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
            m.Event.kind == delivery.KIND, m.Event.data['modality'].as_string() == 'video',
            m.Event.data['summary']['records'].contains([{'id': session.id}]))
            .order_by(m.Event.created_at.desc(), m.Event.id.desc()).limit(SCAN_LIMIT+1)))
        need(len(events) <= SCAN_LIMIT, '本次读取的日修订超过核对上限，请在原资料入口核对')
        witnesses = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == delivery.PUBLICATION,
            m.Audit.data['payload']['summary']['records'].contains([{'id': session.id}]))
            .limit(SCAN_LIMIT+1)))
        need(len(witnesses) <= SCAN_LIMIT and {a.object_id for a in witnesses} == {e.id for e in events})
    if not events:
        return None, None
    event = events[0]; proof = delivery.checked(db, p, event)
    body = proof['body']
    from .instance_observation_day_v216 import TZ
    need(body['modality'] == 'video' and body['source_key'] == 'channel:'+session.channel_id
         and body['day'] == str(session.created_at.astimezone(TZ).date()))
    delivery.validate_sources(db, p, body)
    records = [r for r in body['summary']['records'] if r['id'] == session.id]
    need(len(records) == 1 and records[0].get('run_id') == session.video_run_id)
    ref = {'event_id': event.id, 'payload_hash': body['payload_hash'],
           'record_hash': digest(records[0])}
    need(fixed is None or ref == fixed)
    return proof, ref


def _supplement_read(db, p, scope, identity, fixed=None):
    from . import frame_supplement_v236 as plans
    from . import frame_supplement_source_v236 as source
    row = scoped(db, m.SupplementPlan, identity, p)
    plans.check_row(db, p, row)
    task, task_binding = _task(db, p, scope, row.data[plans.FAMILY]['task_id'])
    need(row.instance_id == scope['instance_id'])
    # Original fixed source and sealed QA prove association; today's camera
    # capability, online flag, plan pause or newer Daily head grant nothing.
    original = source.read_original(db, p, row)
    need(original['pin']['instance_id'] == scope['instance_id'])
    session = source._fixed_session(db, p, row)
    from .instance_observation_day_v216 import TZ
    need(str(session.created_at.astimezone(TZ).date()) == scope['batch_day'])
    capture_job = scoped(db, m.Job, session.data['job_id'], p)
    need(capture_job.kind == 'capture_media' and capture_job.data.get('session_id') == session.id)
    from .video_read_boundary import checked
    reservation = checked(scoped(db, m.VideoReadReservation, session.data['read_reservation_id'], p))
    need(reservation.data['receipt_hash'] == session.data['read_reservation_hash']
         and reservation.request_key == 'clip:'+session.id and reservation.channel_id == session.channel_id
         and reservation.actor_id == capture_job.data['actor_id'])
    run = scoped(db, m.VideoRun, session.video_run_id, p) if session.video_run_id else None
    process = scoped(db, m.Job, run.data['job_id'], p) if run else None
    from .media_daily_task_adapter import one
    from .media import validate_completion
    capture_audit = None
    if session.status in {'captured', 'partial'}:
        validate_completion(session.data['receipt'], session.data['seconds'])
        capture_audit = one(db, p, 'media.capture.saved', session.id)
        need(capture_audit.actor_id == capture_job.data['actor_id']
             and capture_audit.data == {'partial': session.status == 'partial', 'cloud_calls': 0}
             and session.data['receipt']['partial'] is (session.status == 'partial'))
    if run:
        context = run.data.get('source_context')
        need(type(context) is dict and context.get('session_id') == session.id
             and context.get('channel_id') == session.channel_id
             and run.evidence_id == session.evidence_id and context.get('evidence_id') == run.evidence_id
             and process.kind == 'process_video' and process.data.get('run_id') == run.id
             and process.data.get('actor_id') == capture_job.data['actor_id']
             and run.installation_id == session.data['installation_id']
             and run.data['configuration'] == session.data['configuration']
             and run.data['backend'] == session.data['backend']
             and run.data['installation_generation'] == session.data['installation_generation']
             and context.get('domain_binding') == session.data.get('domain_binding'))
        completion = session.data.get('receipt')
        need(isinstance(completion, dict)
             and context.get('partial') is completion['partial']
             and context.get('captured_duration_seconds') == completion['duration_seconds']
             and context.get('received_start_unix') == completion['received_start_unix']
             and context.get('received_end_unix') == completion['received_end_unix'])
        need(run.data['retry_of'] is None and run.request_key == digest({
            'input': run.data['input_sha256'], 'configuration': run.data['configuration'],
            'pipeline': run.data['pipeline_hash'], 'installation': run.installation_id,
            'generation': run.data['installation_generation'], 'backend': run.data['backend'],
            'retry_of': run.data['retry_of'], 'actor': process.data['actor_id'], 'source_context': context}))
    physical = False
    if run and run.status == 'succeeded':
        from .native_video_provenance_v164 import _frames
        from . import workflow_data_quality_v219 as originals
        validate_completion(session.data['receipt'], session.data['seconds'])
        completion = session.data['receipt']
        need(capture_job.status == 'succeeded' and completion['partial'] == (session.status == 'partial')
             and session.status in {'captured', 'partial'}
             and context.get('partial') is completion['partial']
             and context.get('captured_duration_seconds') == completion['duration_seconds']
             and context.get('received_start_unix') == completion['received_start_unix']
             and context.get('received_end_unix') == completion['received_end_unix']
             and run.data['result_hash'] == digest(run.data['result'])
             and abs(run.data['result']['duration_seconds']-completion['duration_seconds']) <= .5)
        evidence = scoped(db, m.Evidence, run.evidence_id, p)
        originals.read_original(originals.settings().storage_dir, p.workspace_id, evidence)
        need(evidence.sha256 == run.data['input_sha256'] == session.data['input_sha256'])
        selected_frames = list(db.scalars(select(m.VideoFrame).where(m.VideoFrame.workspace_id == p.workspace_id,
            m.VideoFrame.run_id == run.id).order_by(m.VideoFrame.sample_index).limit(121)))
        need(0 < len(selected_frames) <= 120)
        for frame in selected_frames:
            original_frame = scoped(db, m.Evidence, frame.evidence_id, p)
            originals.read_original(originals.settings().storage_dir, p.workspace_id, original_frame)
        frames, _ = _frames(db, p, run)
        need([frame.id for frame in frames] == [frame.id for frame in selected_frames])
        completed = one(db, p, 'video.completed', run.id)
        need(completed.actor_id == process.data['actor_id']
             and completed.data == {'samples': len(frames), 'cloud_calls': 0})
        physical = process.status == 'succeeded'
    proof, receipt_ref = _supplement_receipt(db, p, session,
        fixed['receipt'] if fixed is not None else None)
    completion = session.data.get('receipt')
    authority = {k: copy.deepcopy(row.data[k]) for k in
        (plans.FAMILY, 'approved', 'approved_by', 'target', 'session_pin', 'session_id')}
    pin = {'plan_id': row.id, 'task_binding': task_binding, 'authority_hash': digest(authority),
        'session_id': session.id, 'reservation_hash': reservation.data['receipt_hash'],
        'capture_audit': {'id': capture_audit.id, 'hash': digest(capture_audit.data)} if capture_audit else None,
        'completion': copy.deepcopy(completion), 'run_id': run.id if run else None,
        'run_input': ({k: copy.deepcopy(run.data.get(k)) for k in
            ('source_context', 'input_sha256', 'pipeline_hash', 'configuration', 'backend')} if run else None),
        'run_result_hash': run.data.get('result_hash') if run else None, 'receipt': receipt_ref}
    pin['date_basis'] = {'definition': DATE_BASIS['supplement_captures'],
        'batch_day': str(session.created_at.astimezone(TZ).date()),
        'capture_requested_at': session.created_at.astimezone(timezone.utc).isoformat(),
        'data_received_at': proof['state']['received_at'] if proof else None,
        'scene_time_trust': 'unknown'}
    pin['settled_jobs'] = {'capture': capture_job.status if capture_job.status == 'succeeded' else None,
                          'process': process.status if process and process.status == 'succeeded' else None}
    if fixed is not None:
        # Work that was unfinished may legitimately settle later. No fixed
        # receipt/result can be replaced, and old answers remain old answers.
        for key in ('plan_id', 'task_binding', 'authority_hash', 'session_id', 'reservation_hash'):
            need(pin[key] == fixed[key])
        for key in ('capture_audit', 'completion', 'run_id', 'run_input', 'run_result_hash', 'receipt'):
            if fixed[key] is not None:
                need(pin[key] == fixed[key])
        for key, status in fixed['settled_jobs'].items():
            if status is not None:
                need(pin['settled_jobs'][key] == status)
        need(all(pin['date_basis'][key] == fixed['date_basis'][key] for key in
                 ('definition', 'batch_day', 'capture_requested_at', 'scene_time_trust')))
        if fixed['date_basis']['data_received_at'] is not None:
            need(pin['date_basis']['data_received_at'] == fixed['date_basis']['data_received_at'])
        return fixed, None
    requested = _number(row.data['approved']['seconds'])
    actual = _number(completion.get('duration_seconds')) if isinstance(completion, dict) and session.evidence_id else None
    if session.status not in {'captured', 'partial'}:
        actual = None
    processing_status = run.status if run else 'not_started'
    if process and process.status in {'queued', 'running'}:
        processing_status = process.status
    elif process and process.status != 'succeeded':
        processing_status = process.status
    frames = run.data.get('saved_samples') if run and processing_status == 'succeeded' else None
    need(frames is None or type(frames) is int and frames >= 0)
    delivered = _delivery_status(proof['state']['status']) if proof else 'pending'
    key = 'supplement:'+row.id
    refs = [_reference(key+':capture', 'capture', '本方案明确授权的一次读取',
        task_id=task.id, plan_id=row.id, run_id=run.id if run else None,
        path='/api/videos/'+run.id if run else None, limitations=(TIME_NOTE, PHYSICAL_NOTE))]
    if proof:
        refs.append(_reference(key+':receipt', 'daily_receipt', '部分数据独立接收' if session.status == 'partial' else '片段独立接收',
            version='日修订 '+str(proof['body']['daily_revision']), task_id=task.id,
            plan_id=row.id, run_id=run.id if run else None, limitations=(PHYSICAL_NOTE,)))
    if physical:
        for reference in refs:
            reference['physical_recheck'] = 'checked'
            reference['limitations'] = [note for note in reference['limitations'] if note != PHYSICAL_NOTE]
    reason = ('实际读取未达到请求时长，完整补充资料资格未成立；已有部分数据独立接收状态另列。'
              if session.status == 'partial' else '读取失败或停止，实际有效时长未知。'
              if session.status in {'failed', 'cancelled', 'blocked'} else None)
    record = _record(key, 'capture', '补充观察片段', scope,
        business_ids=[task_binding['business_id']], entity_ids=task_binding['entity_ids'],
        capture={'status': session.status, 'requested_seconds': requested, 'actual_seconds': actual, 'reason': reason},
        processing={'status': processing_status, 'frames_saved': frames}, delivery=delivered,
        received_at=proof['state']['received_at'] if proof else None, references=refs,
        limitations=(TIME_NOTE, '原件字节核对不表示画质可用、物理身份或现场正常。'
                     if physical else PHYSICAL_NOTE,
                     '原方案的对象关联不代表物理身份或现场正常。'))
    return pin, record


def _collect_supplements(db, p, scope):
    from . import frame_supplement_v236 as plans
    rows = list(db.scalars(select(m.SupplementPlan).where(m.SupplementPlan.workspace_id == p.workspace_id,
        m.SupplementPlan.instance_id == scope['instance_id'])
        .order_by(m.SupplementPlan.created_at, m.SupplementPlan.id).limit(SCAN_LIMIT+1)))
    witnesses = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == plans.STATE,
        m.Audit.data['snapshot']['instance_id'].as_string() == scope['instance_id'])
        .order_by(m.Audit.id).limit(SCAN_LIMIT*40+1)))
    limited = len(rows) > SCAN_LIMIT or len(witnesses) > SCAN_LIMIT*40
    own = {r.id: r for r in rows[:SCAN_LIMIT] if plans.FAMILY in r.data}
    if not limited:
        need(set(own) == {a.object_id for a in witnesses})
    entries = []
    from .instance_observation_day_v216 import TZ
    for row in own.values():
        plans.check_row(db, p, row)
        hint = scoped(db, m.Task, row.data[plans.FAMILY]['task_id'], p)
        if (hint.data.get('instance_release_id') != scope['release_id']
                or hint.data.get('business_id') not in scope['business_ids']
                or not set(hint.data.get('entity_ids', [])) <= set(scope['entity_ids'])):
            continue
        if not row.data.get('session_id'):
            continue
        session = scoped(db, m.StreamSession, row.data['session_id'], p)
        if str(session.created_at.astimezone(TZ).date()) != scope['batch_day']:
            continue
        pin, record = _supplement_read(db, p, scope, row.id)
        entries.append(_entry('supplement_captures', pin, record))
        if len(entries) >= LIMIT:
            limited = True
            break
    limitations = ['补充读取清单达到扫描/展示上限，当前范围不完整。'] if limited else []
    return entries, limitations, not limited, limited


_DOCUMENT_PIN_KEYS = ('search_id', 'origin_hash', 'keyword', 'result_hash', 'version', 'status')


def _document_header(fixed):
    if fixed is None:
        return None
    need(type(fixed) is dict and set(_DOCUMENT_PIN_KEYS) <= set(fixed)
         and set(fixed) <= set(_DOCUMENT_PIN_KEYS) | {'hit_index', 'hit_hash', 'date_basis'})
    return {key: copy.deepcopy(fixed[key]) for key in _DOCUMENT_PIN_KEYS}


def _document_proof(db, p, scope, identity, *, fixed=None, memo=None):
    """One cold proof per fixed Search in this public collect/recheck call.

    The dictionary is owned by the caller's stack, never a Session/global
    cache. It retains only the checked Search result, not permission grants,
    a new source inventory, a parser result or a different request's bytes.
    """
    from . import runtime_documents as documents
    from .runtime_document_models import RuntimeDocumentSearch as Search, RuntimeDocumentVersion as Version
    header = _document_header(fixed)
    binding = (id(db), p.workspace_id, p.user_id, p.role, digest(scope))
    if memo is not None and identity in memo:
        verified = memo[identity]
        need(verified['binding'] == binding and (header is None or header == verified['pin']),
             '同一正文搜索的原固定依据存在冲突，未替换或合并引用')
        return verified
    search = scoped(db, Search, identity, p)
    need(search.instance_id == scope['instance_id'] and search.release_id == scope['release_id'],
         '正文搜索不属于所选实例版本', 403)
    origin = documents.search_origin(search)
    documents.anchored(db, p, documents.SEARCH_ORIGIN, search.id, origin)
    job = scoped(db, m.Job, search.data['job_id'], p)
    need(job.kind == 'search_runtime_documents' and job.data.get('runtime_document_id') == search.id
         and job.data.get('actor_id') == search.actor_id)
    need(header is None or header['search_id'] == identity and header['keyword'] == search.keyword)
    if header is not None and header['result_hash'] is None:
        # Only the selected pending/unavailable search metadata was adopted.
        # A later result is not silently added to an old query's evidence.
        need(digest(origin) == header['origin_hash'])
        verified = {'binding': binding, 'pin': header, 'result': None, 'reason': None}
        if memo is not None:
            memo[identity] = verified
        return verified
    # The existing document scope still performs its original cold checks.
    # Extending its lifetime through Parse-Job verification avoids rereading
    # the same full original immediately after checked_search exits.
    with documents.verification_scope(db):
        result = documents.checked_search(db, p, search) if search.status == 'completed' else None
        unsettled_parse = []
        if result is not None:
            for item in search.data['inventory']:
                if not item['eligible']:
                    continue
                version = scoped(db, Version, item['version_id'], p)
                attempt, _ = documents.selected_attempt(db, p, version, item['attempt_id'], item['parse_hash'])
                actual_job = scoped(db, m.Job, attempt.job_id, p)
                need(actual_job.kind == 'parse_runtime_document'
                     and actual_job.data.get('runtime_document_id') == version.id
                     and actual_job.data.get('actor_id') == attempt.actor_id)
                if actual_job.status != 'succeeded':
                    unsettled_parse.append(actual_job.status)
    eligible = result is not None and job.status == 'succeeded' and not unsettled_parse
    pin = {'search_id': search.id, 'origin_hash': digest(origin), 'keyword': search.keyword,
           'result_hash': digest(result) if eligible else None,
           'version': search.version, 'status': search.status}
    if header is not None:
        need(pin['origin_hash'] == header['origin_hash'])
        if header['result_hash'] is not None:
            need(result is not None and pin['result_hash'] == header['result_hash']
                 and job.status == 'succeeded' and not unsettled_parse)
        pin = header
    reason = None
    if search.status != 'completed' or job.status != 'succeeded':
        status = 'running' if job.status in {'queued', 'running'} else 'failed'
        reason = '所选正式正文搜索尚未成功结算（'+status+'），未采用其片段。'
    elif unsettled_parse:
        reason = '原正文解析的实际后台任务尚未成功结算，未采用搜索片段。'
    verified = {'binding': binding, 'pin': pin, 'result': result, 'reason': reason}
    if memo is not None:
        memo[identity] = verified
    return verified


def _document(db, p, scope, identity, fixed=None, *, memo=None):
    from .runtime_document_models import RuntimeDocumentVersion as Version
    verified = _document_proof(db, p, scope, identity, fixed=fixed, memo=memo)
    if fixed is not None:
        return fixed, [], [], True, False
    pin, result = verified['pin'], verified['result']
    if verified['reason'] is not None:
        return pin, [], [verified['reason']], False, False
    coverage = result['coverage']
    entries = []
    for index, hit in enumerate(result['hits'][:LIMIT]):
        version = scoped(db, Version, hit['version_id'], p)
        from .instance_observation_day_v216 import TZ
        date_basis = {'definition': DATE_BASIS['document_searches'],
            'version_registered_at': version.created_at.astimezone(timezone.utc).isoformat(),
            'batch_day': str(version.created_at.astimezone(TZ).date()), 'scene_time_trust': 'unknown'}
        if date_basis['batch_day'] != scope['batch_day']:
            continue
        key = 'document:'+identity+':'+str(index)
        reference = _reference(key+':excerpt', 'document_excerpt', hit['title'],
            version='文档版本 '+str(hit['sequence']), path=hit['citation_url'], excerpt=hit['text'],
            limitations=('仅所选搜索匹配原段，不代表整份文档或全部资料。',))
        # checked_search -> citation -> checked_attempt/checked_version reads
        # the fixed original bytes. Media metadata checks above do not.
        reference['physical_recheck'] = 'checked'
        record = _record(key, 'document_excerpt', hit['title'], scope, entity_ids=[],
            processing={'status': 'parsed', 'frames_saved': None}, delivery='received',
            references=[reference], text=hit['text'], limitations=(DOCUMENT_NOTE,))
        record['batch_day'] = date_basis['batch_day']
        record['limitations'].append(DATE_BASIS['document_searches'])
        entries.append(_entry('document_searches', {**pin, 'hit_index': index,
            'hit_hash': digest(hit), 'date_basis': date_basis}, record))
    limited = coverage.get('limited') is True or len(result['hits']) > LIMIT
    complete = not limited and not coverage.get('unavailable')
    notes = []
    if not complete:
        notes.append('所选正文搜索有未覆盖/不可用资料或达到上限，未证明全文检索完整。')
    if not entries:
        notes.append('所选搜索无本次版本登记日范围内的匹配原段，不表示报告没有相关内容。')
    if len(entries) != len(result['hits']):
        notes.append('文档按原版本登记日期筛选；其他登记日期的搜索原段未纳入。')
    return pin, entries, notes, complete, limited


def _collect_documents(db, p, scope):
    entries, controls, notes = [], [], [DOCUMENT_NOTE]
    memo = {}
    complete, limited = True, False
    if not scope['document_search_ids']:
        return [], ['尚未选择已完成的正式正文搜索，本次未覆盖文档正文。', DOCUMENT_NOTE], False, False
    for identity in scope['document_search_ids']:
        pin, rows, errors, ok, truncated = _document(db, p, scope, identity, memo=memo)
        entries.extend(rows)
        # Empty/pending searches also remain fixed dependencies, not an
        # invented zero-record proof or a request to launch a child search.
        controls.append(pin)
        notes.extend(errors); complete = complete and ok; limited = limited or truncated
    return entries, notes, complete, limited, controls


@dataclass(frozen=True)
class Provider:
    id: str
    version: str
    collector: object
    recheck: object
    citation: object


def _citations(record):
    return copy.deepcopy(record['references'])


REGISTRY = {
    'observation_day': Provider('observation_day', '1', _collect_day, _day_read, _citations),
    'supplement_captures': Provider('supplement_captures', '1', _collect_supplements, _supplement_read, _citations),
    'document_searches': Provider('document_searches', '1', _collect_documents, _document, _citations),
}


def _bundle(scope, providers, entries, coverage, notes, snapshot):
    return {'schema': BUNDLE, 'scope': copy.deepcopy(scope), 'providers': providers,
        'records': [copy.deepcopy(e['record']) for e in entries], 'snapshot': snapshot,
        'coverage': copy.deepcopy(coverage), 'limitations': list(dict.fromkeys(notes))}


def collect(db, p, scope):
    scope = _scope_value(scope)
    try:
        with db.no_autoflush:
            instance, release, _ = _authority(db, p, scope, live=True)
            entries, providers, controls = [], [], []
            notes = [EXPECTED_NOTE]; complete, limited = True, False
            for identity in scope['source_kinds']:
                provider = REGISTRY[identity]
                value = provider.collector(db, p, scope)
                rows, messages, ok, truncated = value[:4]
                if len(value) == 5:
                    controls.extend(value[4])
                entries.extend(rows); notes.extend(messages)
                complete = complete and ok; limited = limited or truncated
                providers.append({'id': provider.id, 'version': provider.version})
            if len(entries) > LIMIT:
                entries = entries[:LIMIT]; limited = True; complete = False
                notes.append('资料记录达到统一200条上限，当前回答范围不完整。')
            need(len({e['record']['key'] for e in entries}) == len(entries))
            coverage = {'scan_complete': complete and not limited, 'records_checked': len(entries),
                'limit': LIMIT, 'truncated': limited, 'expected_sources_verified': False}
            notes = list(dict.fromkeys(notes))
            snapshot = {'schema': SNAPSHOT, 'scope': copy.deepcopy(scope), 'scope_pin': _scope_pin(instance, release),
                'providers': copy.deepcopy(providers), 'entries': copy.deepcopy(entries),
                'document_controls': copy.deepcopy(controls), 'coverage': coverage, 'limitations': notes,
                'date_basis': {key: DATE_BASIS[key] for key in scope['source_kinds']}}
            return _bundle(scope, providers, entries, coverage, notes, snapshot)
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OSError, IndexError):
        raise HTTPException(409, ERROR) from None


def recheck(db, p, snapshot):
    """Recheck server-owned, independently sealed query evidence, not client JSON."""
    try:
        need(type(snapshot) is dict and set(snapshot) == {'schema', 'scope', 'scope_pin',
            'providers', 'entries', 'document_controls', 'coverage', 'limitations', 'date_basis'}
            and snapshot['schema'] == SNAPSHOT)
        scope = _scope_value(snapshot['scope'])
        with db.no_autoflush:
            instance, release, _ = _authority(db, p, scope, live=False)
            need(snapshot['scope_pin'] == _scope_pin(instance, release))
            expected = [{'id': key, 'version': REGISTRY[key].version} for key in scope['source_kinds']]
            need(snapshot['providers'] == expected and type(snapshot['entries']) is list
                 and len(snapshot['entries']) <= LIMIT
                 and snapshot['date_basis'] == {key: DATE_BASIS[key] for key in scope['source_kinds']})
            need(type(snapshot['coverage']) is dict and set(snapshot['coverage']) == {
                'scan_complete', 'records_checked', 'limit', 'truncated', 'expected_sources_verified'}
                and type(snapshot['coverage']['records_checked']) is int
                and snapshot['coverage']['records_checked'] == len(snapshot['entries'])
                and snapshot['coverage']['limit'] == LIMIT
                and snapshot['coverage']['expected_sources_verified'] is False
                and type(snapshot['coverage']['scan_complete']) is bool
                and type(snapshot['coverage']['truncated']) is bool
                and len({e['record']['key'] for e in snapshot['entries']}) == len(snapshot['entries']))
            document_memo = {}
            for entry in snapshot['entries']:
                need(set(entry) == {'provider_id', 'source_ref', 'record', 'record_hash'}
                     and entry['provider_id'] in scope['source_kinds']
                     and digest(entry['record']) == entry['record_hash'])
                kind, ref = entry['provider_id'], entry['source_ref']
                if kind == 'observation_day':
                    REGISTRY[kind].recheck(db, p, scope, ref)
                elif kind == 'supplement_captures':
                    REGISTRY[kind].recheck(db, p, scope, ref['plan_id'], fixed=ref)
                else:
                    proof = _document_proof(db, p, scope, ref['search_id'], fixed=ref, memo=document_memo)
                    actual = proof['result']
                    need(actual is not None and type(ref['hit_index']) is int
                         and 0 <= ref['hit_index'] < len(actual['hits']))
                    need(digest(actual['hits'][ref['hit_index']]) == ref['hit_hash'])
                    from .runtime_document_models import RuntimeDocumentVersion as Version
                    from .instance_observation_day_v216 import TZ
                    version = scoped(db, Version, actual['hits'][ref['hit_index']]['version_id'], p)
                    need(ref['date_basis'] == {'definition': DATE_BASIS['document_searches'],
                        'version_registered_at': version.created_at.astimezone(timezone.utc).isoformat(),
                        'batch_day': str(version.created_at.astimezone(TZ).date()), 'scene_time_trust': 'unknown'})
            need({x['search_id'] for x in snapshot['document_controls']} == set(scope['document_search_ids'])
                 and len(snapshot['document_controls']) == len(scope['document_search_ids']))
            for fixed in snapshot['document_controls']:
                _document_proof(db, p, scope, fixed['search_id'], fixed=fixed, memo=document_memo)
            return _bundle(scope, copy.deepcopy(snapshot['providers']), snapshot['entries'],
                snapshot['coverage'], snapshot['limitations'], copy.deepcopy(snapshot))
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OSError, IndexError):
        raise HTTPException(409, ERROR) from None


def catalog(db, p, instance_id, batch_day):
    """Bounded metadata only; no source collection, parsing, ACK or Job work."""
    from .instance_observation_day_v216 import requested_day
    from .media_daily_task_adapter import current_actor
    from .instance_api import active
    from .entity_release_compat import require_entity
    from .runtime_document_models import RuntimeDocumentSearch as Search
    try:
        day = requested_day(batch_day)
        current_actor(db, p)
        with db.no_autoflush:
            instance, release = active(db, p, instance_id)
            businesses = [{'id': b['id'], 'label': b['name']} for b in release.data['snapshot']['businesses']
                          if b.get('available') is True and b['id'] in instance.data.get('enabled_business_ids', [])]
            rows = list(db.scalars(select(m.Entity).where(m.Entity.workspace_id == p.workspace_id,
                m.Entity.namespace == instance.namespace).order_by(m.Entity.id).limit(LIMIT+1)))
            notes = [EXPECTED_NOTE, TIME_NOTE, DOCUMENT_NOTE]
            if len(rows) > LIMIT:
                notes.append('对象目录仅展示前200项，请缩小范围；不是完整对象清单。')
            entities = []
            for row in rows[:LIMIT]:
                try:
                    require_entity(db, p, instance, release, row, lock_sources=False)
                except HTTPException as error:
                    if error.status_code not in {404, 409}:
                        raise
                    notes.append('有对象的版本兼容关系无法核对，未开放该对象查询。')
                    continue
                entities.append({'id': row.id, 'label': row.external_id,
                                 'business_ids': [b['id'] for b in businesses]})
            searches = list(db.scalars(select(Search).where(Search.workspace_id == p.workspace_id,
                Search.instance_id == instance.id, Search.release_id == release.id)
                .order_by(Search.created_at.desc(), Search.id.desc()).limit(LIMIT+1)))
            if len(searches) > LIMIT:
                notes.append('正文搜索目录仅展示最近200项，不代表全部文档。')
            search_views = []
            for search in searches[:LIMIT]:
                job = scoped(db, m.Job, search.data['job_id'], p)
                need(job.kind == 'search_runtime_documents'
                     and job.data.get('runtime_document_id') == search.id
                     and job.data.get('actor_id') == search.actor_id)
                status = search.status
                if job.status in {'queued', 'running'}:
                    status = job.status
                elif job.status != 'succeeded':
                    status = 'cancelled' if job.status == 'cancelled' else 'failed'
                search_views.append({'id': search.id, 'label': search.keyword,
                                     'status': status, 'version': search.version})
            result = {'schema': 'data-query-catalog-v2', 'instance_id': instance.id,
                'release_id': release.id, 'configuration_hash': release.configuration_hash,
                'batch_day': day, 'businesses': businesses, 'entities': entities,
                'source_kinds': [
                    {'id': 'observation_day', 'label': '实例日整理资料', 'available': True,
                     'limitation': '按原日整理归集/接收/处理日期筛选，仅确证绑定的视频、音频和影像。'},
                    {'id': 'supplement_captures', 'label': '补充读取资料（含部分片段）', 'available': True,
                     'limitation': '按原Session发起批次日筛选，迟到仍原批次；仅原方案/任务明确关联的读取。'},
                    {'id': 'document_searches', 'label': '正式文档正文搜索', 'available': bool(searches),
                     'limitation': '按固定文档版本登记日期筛选。'+DOCUMENT_NOTE}],
                'document_searches': search_views,
                'permissions': {'can_create': p.role in {'admin', 'operator'}},
                'limitations': list(dict.fromkeys(notes))}
            return {**result, 'catalog_hash': digest(result)}
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, ERROR) from None
