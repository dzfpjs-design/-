"""Read-only, explicitly scoped media-day projection; never an ecological report.

No reconciliation, ACK, model call, state refresh or candidate promotion occurs
here. Audio/imagery membership comes from the original workflow Task and fixed
inputs, not their cached instance label. Video membership is also bound to the
independently published day revision. Standalone workspace uploads are excluded.
"""
from datetime import date
from typing import Annotated
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from .contracts import digest
from .db import session
from .models import Audit, Entity, Event, Task, VideoRun, now
from .services import scoped
from .security import Principal, authenticated
from . import media_daily_delivery as delivery


SCHEMA = 'instance-observation-day-view-v216'
TZ = ZoneInfo('Asia/Shanghai')
MAX_COLLECTIONS = 128
MAX_SCAN_RECORDS = 2000
MAX_VISIBLE_RECORDS = 200
LABELS = {'video': '视频资料', 'audio': '音频资料', 'imagery': '影像资料'}
ACK_LABELS = {'received': '数据管理已独立接收', 'pending_delivery': '等待数据管理独立接收',
    'blocked_delivery': '独立接收受阻', 'not_published': '本修订尚未发布独立接收请求',
    'integrity_error': '原发布或独立接收证据无法核对',
    'verification_limit': '本修订超过本次来源核对容量；接收状态尚未完整核对'}
STATUS_LABELS = {'queued': '等待处理', 'running': '正在处理', 'connecting': '正在连接',
    'capturing': '正在接收', 'succeeded': '处理已保存', 'ready': '原件已准备',
    'parsed': '原件已解析', 'failed': '处理失败', 'cancelled': '已停止',
    'partial': '仅收到部分资料', 'integrity_unknown': '原修订待核'}
REVIEW_LABELS = {'confirmed': '已有人工核对记录', 'unreviewed': '尚未人工核对',
    'waiting': '等待业务核对', 'draft_saved': '核对草稿已保存', 'blocked': '核对受阻',
    'cancelled': '原任务已停止', 'not_requested': '本处理未请求人工核对',
    'not_applicable': '此项不适用'}
READ_ERRORS = (HTTPException, ValueError, KeyError, TypeError, AttributeError, OSError, SQLAlchemyError)
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/api/workspace/instances')


def requested_day(value=None):
    if value is None:
        return str(now().astimezone(TZ).date())
    try:
        if type(value) is not str or len(value) != 10 or str(date.fromisoformat(value)) != value:
            raise ValueError()
        return value
    except ValueError:
        raise HTTPException(422, '请选择YYYY-MM-DD格式的有效日期') from None


def _issue(code, message):
    return {'code': code, 'message': message}


def _need(value):
    if not value:
        raise ValueError('原实例、日修订或来源关联无法核对')


def _scope(db, p, identity):
    from .instance_api import active
    instance, release = active(db, p, identity)
    _need(release.instance_id == instance.id and release.id == instance.active_release_id
        and digest(release.data['snapshot']) == release.configuration_hash)
    businesses = release.data['snapshot']['businesses']
    _need(type(businesses) is list and len({b['id'] for b in businesses}) == len(businesses))
    return instance, release, {b['id']: b['name'] for b in businesses}


def _entities(db, p, instance, release, identifiers):
    from .entity_release_compat import require_entity
    _need(type(identifiers) is list and identifiers and len(identifiers) == len(set(identifiers)))
    result = []
    for identity in identifiers:
        entity = scoped(db, Entity, identity, p)
        require_entity(db, p, instance, release, entity, lock_sources=False)
        result.append({'id': entity.id, 'name': entity.external_id})
    return result


def _binding(db, p, modality, record, instance, release, businesses, published):
    kind = ('video_upload' if record['mode'] == 'upload' else 'video_capture') if modality == 'video' else record['kind']
    cls, _ = delivery.SOURCES[kind]
    source = scoped(db, cls, record['id'], p)
    if modality == 'video':
        # A loose source_context dictionary alone is not an independent record
        # of assignment; the original published day must seal the same target.
        if not published:
            return None
        run = source if kind == 'video_upload' else (
            scoped(db, VideoRun, source.video_run_id, p) if source.video_run_id else None)
        if run is None:
            return None
        target = (run.data.get('source_context') or {}).get('domain_binding')
        frozen = record.get('business_target')
        if not target or target.get('instance_id') != instance.id:
            _need(not frozen or frozen.get('instance_id') != instance.id)
            return None
        if target.get('instance_release_id') != release.id:
            _need(not frozen or frozen.get('instance_release_id') != release.id)
            return None
        _need(type(frozen) is dict and all(frozen.get(k) == target.get(k)
            for k in ('instance_id', 'instance_release_id', 'business_id', 'entity_id'))
            and record.get('run_id') == run.id)
        business_id = target['business_id']; ids = [target['entity_id']]
        task_id = None; relation = 'declared_observed_scene'
    else:
        task_id = source.data.get('workflow_task_id')
        if not task_id:
            _need(not record.get('task_id') and not record.get('instance_id'))
            return None  # An instance-only upload/association is insufficient.
        task_hint = scoped(db, Task, task_id, p)
        if task_hint.data.get('instance_id') != instance.id:
            _need(record.get('instance_id') != instance.id)
            return None
        from .workspace_api import execution
        task, original_release, _ = execution(db, p, task_id, lock_sources=False)
        if task.data.get('instance_id') != instance.id or original_release.id != release.id:
            return None
        _need(record.get('task_id') == task.id and record.get('instance_id') == instance.id)
        if modality == 'audio':
            from .audio_daily import checked_source
            checked_source(db, p, kind, source)
        else:
            from .geo_daily import check_source
            check_source(db, p, kind, source)
        business_id = task.data['business_id']; ids = task.data['entity_ids']
        relation = 'workflow_task_object_set'
    _need(business_id in businesses)
    return {'instance_id': instance.id, 'release_id': release.id,
        'business_id': business_id, 'business_name': businesses[business_id],
        'entities': _entities(db, p, instance, release, ids), 'task_id': task_id,
        'label': '仅关联任务对象集合，未断言每个对象都已观测' if task_id else '人工关联的观测场景，不代表设备物理身份',
        'relation': relation, 'source_id': source.id, 'source_version': source.version}


def _ack(db, p, daily, modality):
    """The producer Memory is never an ACK; only checked independent receipt is."""
    proof = None
    try:
        events = list(db.scalars(select(Event).where(Event.workspace_id == p.workspace_id,
            Event.kind == delivery.KIND, Event.data['daily_id'].as_string() == daily.id,
            Event.data['daily_revision'].as_integer() == daily.version).limit(2)))
        _need(len(events) <= 1)
        if not events:
            witnesses = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
                Audit.action == delivery.PUBLICATION,
                Audit.data['payload']['daily_id'].as_string() == daily.id,
                Audit.data['payload']['daily_revision'].as_integer() == daily.version).limit(1)))
            _need(not witnesses)
            return {'status': 'not_published', 'label': ACK_LABELS['not_published'],
                'verified_received': False, 'integrity_verified': False, 'event_id': None, 'received_at': None}, None
        event = events[0]
        proof = delivery.checked(db, p, event)
        body, state = proof['body'], proof['state']
        _need(body['modality'] == modality and body['daily_id'] == daily.id
            and body['daily_revision'] == daily.version
            and body['summary'] == delivery.summary_value(daily))
        if len(body['source_pins']) > MAX_SCAN_RECORDS:
            return {'status': 'verification_limit', 'label': ACK_LABELS['verification_limit'],
                'verified_received': False, 'integrity_verified': False,
                'event_id': event.id, 'received_at': None}, proof
        delivery.validate_sources(db, p, body)
        return {'status': state['status'], 'label': ACK_LABELS[state['status']],
            'verified_received': state['status'] == 'received', 'integrity_verified': True, 'event_id': event.id,
            'received_at': state['received_at']}, proof
    except READ_ERRORS:
        return {'status': 'integrity_error', 'label': ACK_LABELS['integrity_error'],
            'verified_received': False, 'integrity_verified': False, 'event_id': None, 'received_at': None}, proof


def _matches_day(modality, record, collection_day, selected):
    fields = ('received_day',) if modality == 'video' else ('origin_upload_day',
        'processing_request_day', 'processing_completed_day')
    return selected == collection_day or any(record.get(key) == selected for key in fields)


def _candidate(modality, record, verified):
    if not verified or record.get('integrity_verified') is False:
        return {'summary': '本修订来源尚未完整核对，未采用缓存中的候选数量。',
            'review': '待核对', 'quality': ['来源或独立发布证据待恢复'], 'not_authoritative': True}
    processing = record.get('processing_status') if modality == 'video' else record.get('status')
    if processing != 'succeeded':
        return {'summary': '本修订尚无成功保存的候选结果；未把等待、失败或停止解释为零目标。',
            'review': '待按原处理记录核对', 'quality': ['候选结果尚未形成或原处理未成功'], 'not_authoritative': True}
    if modality == 'video':
        summary = '已保存画面{}帧；规则候选{}条，均不是物种或个体数量。'.format(
            record.get('saved_frames', 0), record.get('business_candidates', 0))
        review = '候选待按原记录核对'
    elif modality == 'audio':
        if record.get('kind') == 'transcription':
            summary = ('已保存实际执行的整段语音转写；本项不表示已执行语音活动区间检测。'
                if record.get('whole_asr_performed') is True else
                '原处理已保存，但本修订未证明执行了整段转写；不能推断无语音。')
        elif record.get('kind') == 'activity':
            summary = '本项仅语音活动检测，保存候选区间{}段；未执行语音转写。'.format(record.get('vad_intervals', 0))
        elif record.get('kind') == 'segments':
            summary = ('本次未选择可转写片段，未将未执行的分段转写解释为无语音。'
                if record.get('no_speech_candidates') is True else
                '已保存分段转写{}段；语音活动检测属于原独立处理记录。'.format(record.get('saved_asr_segments', 0)))
        else:
            summary = '音频结果类型待核对，未推算转写或语音活动数量。'
        summary += ' 以上均不代表鸟鸣或物种识别。'
        review = REVIEW_LABELS.get(record.get('review_state'), '核对状态待确认')
    else:
        summary = ('已保存两期影像变化候选，仍需核对配准、质量掩膜和领域意义。' if record.get('kind') == 'comparison'
            else '候选掩膜{}个；属于无语义分割候选，不代表物种状态。'.format(record.get('candidate_masks', 0)))
        review = REVIEW_LABELS.get(record.get('review_state'), '核对状态待确认')
    quality = []
    if record.get('integrity_verified') is False:
        quality.append('原件或处理结果完整性待核对')
    if record.get('quality_gaps'):
        quality.append('原影像存在范围、地理参考或质量核查缺项')
    if record.get('error') or record.get('status') == 'failed' or record.get('processing_status') == 'failed':
        quality.append('原处理记录包含失败，请打开原记录核查')
    return {'summary': summary, 'review': review, 'quality': quality, 'not_authoritative': True}


def _item(modality, record, binding, daily, ack, proof):
    verified = proof is not None and ack.get('integrity_verified') is True
    pins = proof['body']['source_pins'] if verified else []
    pin = next((value for value in pins if value['id'] == record['id']
        and value['kind'] == (('video_upload' if record['mode'] == 'upload' else 'video_capture')
            if modality == 'video' else record['kind'])), None)
    _need(not verified or pin is not None)
    refs = pin['evidence'] if pin else []
    key = record.get('key') or record['id']
    detail = ('/api/videos/' + record['run_id'] if record.get('run_id') else None) if modality == 'video' else (
        '/api/' + ('audio-daily' if modality == 'audio' else 'imagery-daily') + '/' + daily.id
        + '/records/' + quote(key, safe=''))
    collection_day = daily.business_day if modality == 'video' else daily.received_day
    status = str(record.get('processing_status') or record.get('status') or 'unknown') if verified else 'integrity_unknown'
    source_version = pin['source_version'] if pin else None
    return {'id': daily.id + ':' + str(daily.version) + ':' + key, 'modality': modality,
        'title': str(record.get('name') or LABELS[modality] + '处理记录')[:250] if verified else LABELS[modality] + '资料（原修订待核）',
        'status': status, 'status_label': STATUS_LABELS.get(status, '原处理状态待核'),
        'binding': binding, 'collection': {'id': daily.id, 'revision': daily.version,
            'day': collection_day, 'ack': ack},
        'dates': {'collection_day': collection_day, 'received_day': record.get('received_day') if verified else None,
            'processing_request_day': record.get('processing_request_day') if verified else None,
            'processing_completed_day': record.get('processing_completed_day') if verified else None,
            'original_capture_time': None, 'original_capture_time_state': 'unknown',
            'label': '按接收或处理记录日期归集；原采集时间未核实'},
        'candidate': _candidate(modality, record, verified), 'detail_url': detail,
        'source_version_at_collection': source_version,
        'source_changed_since_collection': binding.get('source_version') != source_version if source_version is not None else None,
        'task_url': ('/setup/?view=workspace&instance=' + binding['instance_id']
            + '&task=' + binding['task_id']) if binding['task_id'] else None,
        'originals': [{'evidence_id': ref['id'], 'sha256': ref['sha256'],
            'url': '/api/evidence/' + ref['id'] + '/content'} for ref in refs],
        'originals_verified': verified, 'candidate_only': True, 'physical_state_verified': False}


def same_evidence_links(records):
    groups = {}
    for record in records:
        for original in record['originals']:
            identity = (original['evidence_id'], original['sha256'])
            group = groups.setdefault(identity, {})
            group[record['id']] = {'record_id': record['id'], 'modality': record['modality']}
    return [{'evidence_id': identity[0], 'records': list(group.values()),
        'relation': 'same_stored_evidence', 'label': '仅同一原文件关联，不代表同物种或同一个体'}
        for identity, group in groups.items() if len({r['modality'] for r in group.values()}) > 1]


def _section(db, p, modality, selected, instance, release, businesses):
    cls = delivery.DAILIES[modality]
    day_column = cls.business_day if modality == 'video' else cls.received_day
    days = ('received_day',) if modality == 'video' else ('processing_request_day', 'processing_completed_day')
    result = {'modality': modality, 'label': LABELS[modality], 'status': 'complete',
        'truncated': False, 'count_complete': True, 'record_count': 0, 'records': [], 'issues': []}
    try:
        conditions = [day_column == selected] + [cls.data['records'].contains([{key: selected}]) for key in days]
        collections = list(db.scalars(select(cls).where(cls.workspace_id == p.workspace_id,
            or_(*conditions)).order_by(day_column.desc(), cls.id).limit(MAX_COLLECTIONS + 1)))
        if len(collections) > MAX_COLLECTIONS:
            result['truncated'] = True
            result['issues'].append(_issue('collection_scan_limit', '日整理清单超过本次扫描上限；当前展示不完整。'))
        scanned = 0; record_limit = False
        for daily in collections[:MAX_COLLECTIONS]:
            ack, proof = _ack(db, p, daily, modality)
            if ack['status'] == 'verification_limit':
                result['truncated'] = True
                result['issues'].append(_issue('ack_scan_limit', ACK_LABELS['verification_limit']))
            records = proof['body']['summary']['records'] if proof is not None else daily.data.get('records')
            _need(type(records) is list and type(daily.version) is int and daily.version >= 1)
            for record in records:
                scanned += 1
                if scanned > MAX_SCAN_RECORDS or len(result['records']) >= MAX_VISIBLE_RECORDS:
                    result['truncated'] = True
                    record_limit = True
                    break
                collection_day = daily.business_day if modality == 'video' else daily.received_day
                if not _matches_day(modality, record, collection_day, selected):
                    continue
                hint = (record.get('business_target') or {}).get('instance_id') if modality == 'video' else record.get('instance_id')
                try:
                    binding = _binding(db, p, modality, record, instance, release, businesses, proof is not None)
                    if binding is None:
                        if hint == instance.id and ack['status'] in {'integrity_error', 'not_published', 'verification_limit'}:
                            result['issues'].append(_issue('unverified_assignment', '有声明归属的资料缺少可核对的原发布或来源关联，未计入。'))
                        continue
                    item = _item(modality, record, binding, daily, ack, proof)
                    result['records'].append(item)
                    if ack['status'] in {'integrity_error', 'not_published', 'verification_limit'}:
                        result['issues'].append(_issue('delivery_unverified', ACK_LABELS[ack['status']]))
                except READ_ERRORS:
                    result['issues'].append(_issue('source_integrity', '部分原处理、任务或对象归属无法核对；未计入，当前展示范围不完整。'))
            if record_limit:
                break
    except READ_ERRORS:
        result['issues'].append(_issue('day_read_failed', '本模态原日修订或来源清单无法完整读取，当前数量未知。'))
    if result['truncated']:
        result['issues'].append(_issue('record_scan_limit', '已到本次扫描或展示上限，请缩小来源范围后在原入口核对。'))
    result['issues'] = list({issue['code']: issue for issue in result['issues']}.values())
    result['count_complete'] = not result['truncated'] and not result['issues']
    result['status'] = 'available' if result['count_complete'] else 'partial' if result['records'] else 'unavailable'
    result['record_count'] = len(result['records']) if result['records'] or result['count_complete'] else None
    return result


def read_day(db, p, identity, day=None):
    selected = requested_day(day)
    # Reader helpers may inspect existing rows; this projection never flushes
    # repairs, consumes an Event, refreshes state, or publishes a new revision.
    with db.no_autoflush:
        try:
            instance, release, businesses = _scope(db, p, identity)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(409, '当前实例启用版本或业务范围无法核对，未展示为已有资料') from None
        sections = [_section(db, p, modality, selected, instance, release, businesses) for modality in LABELS]
    records = [row for section in sections for row in section['records']]
    complete = all(section['count_complete'] for section in sections)
    return {'schema': SCHEMA, 'instance_id': instance.id, 'release_id': release.id,
        'day': selected, 'timezone': 'Asia/Shanghai', 'status': 'available' if complete else 'partial',
        'sections': sections, 'same_evidence_links': same_evidence_links(records),
        'empty_message': '尚无可核实归属到当前启用版本的当日资料；未绑定资料不计入。' if complete and not records else None,
        'scope_note': '仅当前实例版本的明确业务/对象或任务对象集合；独立上传和未绑定资料不计入。',
        'time_note': '日期是资料接收或处理日期，不能作为管理范围内当天现场状态的证明。',
        'coverage_note': '仅核对已存在的日整理记录；未证明全部设备或应交资料均已到齐。',
        'candidate_only': True, 'physical_state_verified': False, 'entity_state_updated': False,
        'species_identification_verified': False, 'all_expected_sources_verified': False,
        'ecological_report': False, 'cloud_calls': 0}


@router.get('/{identity}/observation-day')
def observation_day(identity: str, request: Request, p: P, db: DB, day: str | None = None):
    if set(request.query_params) - {'day'} or len(request.query_params.getlist('day')) > 1:
        raise HTTPException(422, '每日资料只接受一个明确日期参数')
    return read_day(db, p, identity, day)
