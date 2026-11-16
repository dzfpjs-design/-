"""Explicit, one-read business suggestions from an actually saved frame QA.

No HTTP module is imported at module initialization. Existing SupplementPlan
rows without this family are never rewritten or interpreted here.
"""
import copy
import hashlib
import json
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select, text
from .contracts import digest
from .models import Audit, DomainInstance, EvidenceAssessment, Job, SupplementPlan, Task, now
from .services import audit, require, scoped

FAMILY = 'frame_supplement_v236'
COMMAND = 'business.frame_supplement_command_v236'
STATE = 'business.frame_supplement_state_v236'
MAX_BODY = 16384
MAX_HISTORY = 1000
PAGE_SIZE = 20
STOCK_LIMIT = 8
RULE = 'frame-supplement-business-rules-v236'
ERROR = '补充方案、原请求或独立证明无法核对；原记录保留，未重新读取'
SCHEMAS = {'propose': 'frame-supplement-propose-v236', 'approve': 'frame-supplement-approve-v236',
    'cancel': 'frame-supplement-cancel-v236'}


def need(value, message=ERROR, status=409):
    if not value: raise HTTPException(status, message)


def parse(raw, operation):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BODY, '请求为空或超过16KB', 413)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate')
            result[key] = value
        return result
    fields = {'propose': {'assessment_id', 'expected_result_hash', 'expected_source_hash', 'expected_frame_sha256'},
        'approve': {'version', 'expected_decision_hash', 'seconds', 'confirm_one_time_read'},
        'cancel': {'version', 'reason'}}
    try:
        value = json.loads(raw.decode('utf8'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        need(type(value) is dict and set(value) == fields[operation] | {'schema', 'request_id'}
            and value['schema'] == SCHEMAS[operation], '请求字段不完整或不受支持', 422)
        need(type(value['request_id']) is str and str(UUID(value['request_id'])) == value['request_id'], '原请求编号无效', 422)
        if operation == 'propose':
            need(type(value['assessment_id']) is str and str(UUID(value['assessment_id'])) == value['assessment_id'], '原核查编号无效', 422)
        for key, item in value.items():
            if key.endswith('_hash') or key.endswith('_sha256'):
                need(type(item) is str and len(item) == 64 and all(c in '0123456789abcdef' for c in item), '摘要无效', 422)
        if operation in {'approve', 'cancel'}:
            need(type(value['version']) is int and value['version'] >= 1, '方案版本无效', 422)
        if operation == 'approve':
            need(type(value['seconds']) is int and 4 <= value['seconds'] <= 12
                and value['confirm_one_time_read'] is True, '请明确确认4至12秒的一次读取', 422)
        if operation == 'cancel':
            need(type(value['reason']) is str and 1 <= len(value['reason']) <= 500 and value['reason'].strip(), '请填写停止原因', 422)
        return value
    except HTTPException: raise
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, '补充请求格式无效，未执行') from None


def business_decision(qa, *, assessment_id, result_hash, source_hash, frame_sha256, stock, source):
    """Actually consume the sealed QA projection; never authorize from text."""
    need(type(qa) is dict and qa['source_hash'] == source_hash and qa['frame_sha256'] == frame_sha256
        and qa['current_state_sufficient'] is False and qa['state_conclusion'] is None
        and type(qa['checks']) is list and (qa['visual'] is None or type(qa['visual']) is dict))
    need(type(stock) is dict and type(stock['total_frames']) is int
        and stock['checked_frames'] == len(stock['frames']) <= STOCK_LIMIT
        and stock['limit'] == STOCK_LIMIT and stock['checked_frames'] <= stock['total_frames']
        and stock['truncated'] == (stock['checked_frames'] < stock['total_frames']))
    gaps = sorted({item['kind'] for item in qa['gaps']})
    unresolved_checks = sum(item['status'] != 'passed' for item in qa['checks'])
    original_visual = qa['visual']
    visual_note = ('原图未取得有界像素测量，不能声称原图可用。' if original_visual is None else
        '原图已测亮度/对比分布，有'+str(len(original_visual['candidates']))+'项未校准线索；可辨性仍未验证。')
    return {'rule_version': RULE, 'assessment_id': assessment_id, 'result_hash': result_hash,
        'source_hash': source_hash, 'frame_sha256': frame_sha256, 'stock': copy.deepcopy(stock),
        'target_gaps': gaps, 'cannot_certify': ['拍摄时钟与现场时效', '全部区域、对象或视频覆盖',
            '画面可辨性与物种识别精度', '对象当前状态'],
        'recommendation': 'inspect_saved_first_then_optional_read',
        'explanation': ['业务规则已读取原核查的技术指标与剩余缺口，先检查同一原视频已有画面。',
            '原独立核查有'+str(unresolved_checks)+'项检查未认证或需复核。'+visual_note,
            '已有'+str(stock['total_frames'])+'张保存画面，本次有界检查'+str(stock['checked_frames'])+'张；先检查存量，不把单帧不足等同于没有其他资料。',
            '可以另行确认读取同来源一个片段，但不承诺解决时间、覆盖或领域效果缺口。',
            '相同SHA不算新增信息，不同SHA也不能证明来自新现场或质量提高。'],
        'source': copy.deepcopy(source), 'read': {'default_seconds': 6, 'min_seconds': 4,
            'max_seconds': 12, 'max_reads': 1, 'local_only': True, 'cloud_calls': 0,
            'physical_control_authorized': False}}


def snapshot(row):
    return {'id': row.id, 'workspace_id': row.workspace_id, 'instance_id': row.instance_id,
        'assessment_id': row.assessment_id, 'owner_id': row.owner_id, 'request_id': row.request_id,
        'version': row.version, 'status': row.status, 'data': copy.deepcopy(row.data)}


def save_state(db, p, row, *, initial=False):
    previous = row.data.get('state_head')
    if not initial: row.version += 1
    # The chain head is kept outside the hashed row projection to avoid a
    # circular self-hash; both the complete data and its head are verified.
    data = copy.deepcopy(row.data); data.pop('state_head', None)
    value = snapshot(row); value['data'] = data
    body = {'task_id': data[FAMILY]['task_id'], 'version': row.version,
        'previous': previous, 'snapshot': value}
    body['state_hash'] = digest(body)
    row.data = {**data, 'state_head': body['state_hash']}
    audit(db, p, STATE, row.id, body)
    db.flush()


def inventory(db, p, task_id):
    from .media_daily_task_adapter import current_actor
    current_actor(db, p); scoped(db, Task, task_id, p)
    rows = list(db.scalars(select(SupplementPlan).where(SupplementPlan.workspace_id == p.workspace_id,
        SupplementPlan.data[FAMILY]['task_id'].as_string() == task_id).limit(MAX_HISTORY+1)))
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_([COMMAND, STATE]), Audit.data['task_id'].as_string() == task_id).limit(MAX_HISTORY*40+1)))
    need(len(rows) <= MAX_HISTORY and len(events) <= MAX_HISTORY*40)
    need({row.id for row in rows} == {event.object_id for event in events})
    for row in rows: check_row(db, p, row, events=events)
    return rows


def check_row(db, p, row, *, events=None):
    try:
        need(row.workspace_id == p.workspace_id and type(row.data.get(FAMILY)) is dict)
        if events is None:
            events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
                Audit.action.in_([COMMAND, STATE]), Audit.object_id == row.id).limit(80)))
        states = sorted((event for event in events if event.action == STATE and event.object_id == row.id), key=lambda e: e.data['version'])
        commands = [event for event in events if event.action == COMMAND and event.object_id == row.id]
        need(states and commands and len(states) <= 40)
        previous = None
        for index, event in enumerate(states, 1):
            value = event.data
            need(value['version'] == index and value['previous'] == previous
                and value['task_id'] == row.data[FAMILY]['task_id']
                and value['state_hash'] == digest({k: v for k, v in value.items() if k != 'state_hash'}))
            need(all(value['snapshot'][key] == getattr(row, key) for key in
                ('id', 'workspace_id', 'instance_id', 'assessment_id', 'owner_id', 'request_id'))
                and value['snapshot']['data'][FAMILY] == row.data[FAMILY])
            previous = value['state_hash']
        current = snapshot(row); current['data'].pop('state_head', None)
        need(states[-1].data['snapshot'] == current and row.data['state_head'] == previous)
        seen = set()
        for event in commands:
            value = event.data; original = parse(value['exact_body'].encode('utf8'), value['operation'])
            need(event.actor_id == value['actor_id'] and value['task_id'] == row.data[FAMILY]['task_id']
                and value['plan_id'] == row.id and value['request_id'] == original['request_id']
                and value['request_id'] not in seen and value['body_sha256'] == hashlib.sha256(value['exact_body'].encode('utf8')).hexdigest()
                and value['record_hash'] == digest({k: v for k, v in value.items() if k != 'record_hash'})
                and value['state_hash'] in {state.data['state_hash'] for state in states})
            seen.add(value['request_id'])
            saved = next(state.data['snapshot'] for state in states if state.data['state_hash'] == value['state_hash'])
            if value['operation'] == 'propose':
                need(saved['version'] == 1 and saved['status'] == 'proposed'
                    and original['assessment_id'] == row.assessment_id
                    and all(original['expected_'+field] == row.data[FAMILY][field]
                        for field in ('result_hash', 'source_hash', 'frame_sha256')))
            else:
                need(saved['version'] == original['version']+1)
                if value['operation'] == 'approve':
                    need(saved['status'] == 'reading' and saved['data']['approved_by'] == event.actor_id
                        and saved['data']['approved']['seconds'] == original['seconds']
                        and saved['data']['decision_hash'] == original['expected_decision_hash'])
                else: need(saved['status'] == 'cancelled' and saved['data']['stop_reason'] == original['reason'])
        created = [event for event in commands if event.data['operation'] == 'propose']
        need(len(created) == 1 and created[0].actor_id == row.owner_id
            and created[0].data['request_id'] == row.request_id
            and row.data['decision_hash'] == digest(row.data['decision'])
            and row.data[FAMILY]['assessment_id'] == row.assessment_id)
        approvals = [event for event in commands if event.data['operation'] == 'approve']
        need(len(approvals) == (1 if row.data.get('approved') else 0)
            and len([event for event in commands if event.data['operation'] == 'cancel']) == (1 if row.status == 'cancelled' else 0))
        if row.data.get('approved'):
            approved_state = next(state.data['snapshot'] for state in states
                if state.data['state_hash'] == approvals[0].data['state_hash'])
            need(all(approved_state['data'][key] == row.data[key] for key in
                ('approved', 'approved_by', 'session_id', 'session_pin', 'target', 'decision_hash')))
        return row
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError): raise HTTPException(409, ERROR) from None


def checked_plan(db, p, task_id, plan_id):
    row = next((row for row in inventory(db, p, task_id) if row.id == plan_id), None)
    need(row is not None, '此任务没有该补充方案', 404)
    return row


def is_family(db, row):
    if FAMILY in row.data: return True
    return db.scalar(select(Audit.id).where(Audit.workspace_id == row.workspace_id,
        Audit.object_id == row.id, Audit.action.in_([COMMAND, STATE])).limit(1)) is not None


def lock_nonce(db, p, nonce):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest(
        {'workspace': p.workspace_id, 'family': FAMILY, 'request': nonce})[:16], 16)-(1 << 63)})


def record_command(db, p, row, operation, raw):
    original = parse(raw, operation)
    value = {'task_id': row.data[FAMILY]['task_id'], 'plan_id': row.id, 'actor_id': p.user_id,
        'operation': operation, 'request_id': original['request_id'], 'exact_body': raw.decode('utf8'),
        'body_sha256': hashlib.sha256(raw).hexdigest(), 'state_hash': row.data['state_head']}
    value['record_hash'] = digest(value)
    audit(db, p, COMMAND, row.id, value); db.flush()


def recover(db, p, task_id, nonce, sha):
    from .media_daily_task_adapter import current_actor
    current_actor(db, p); scoped(db, Task, task_id, p)
    need(type(sha) is str and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha), '原请求摘要无效', 422)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == COMMAND, Audit.data['request_id'].as_string() == nonce).limit(2)))
    need(len(events) <= 1)
    base = {'schema': 'frame-supplement-recovery-v236', 'task_id': task_id,
        'request_id': nonce, 'body_sha256': sha, 'found': False, 'result': None}
    rows = inventory(db, p, task_id)
    if not events:
        # A missing command must not convert a retained proposal into unknown.
        retained = db.scalar(select(SupplementPlan.id).where(SupplementPlan.workspace_id == p.workspace_id,
            SupplementPlan.request_id == nonce).limit(1))
        need(retained is None and not any(row.request_id == nonce for row in rows))
        return base
    event = events[0]
    need(event.actor_id == p.user_id, '请由原操作者核对原请求', 403)
    need(event.data['task_id'] == task_id and event.data['body_sha256'] == sha)
    row = next((row for row in rows if row.id == event.object_id), None); need(row is not None)
    result = command_result(db, p, row, event.data['operation'], nonce, sha)
    return {**base, 'found': True, 'result': result}


def command_result(db, p, row, operation, nonce, sha):
    return {'schema': 'frame-supplement-command-v236', 'operation': operation,
        'task_id': row.data[FAMILY]['task_id'], 'plan_id': row.id, 'request_id': nonce,
        'body_sha256': sha, 'plan': plan_view(db, p, row)}


def stock_check(db, p, bundle):
    from .native_video_provenance_v164 import _frames
    from .models import Evidence, VideoRun
    from .daily_frame_quality_v233 import inspect_image
    from . import workflow_data_quality_v219 as originals
    run = scoped(db, VideoRun, bundle['pin']['origin']['run_id'], p)
    frames, pins = _frames(db, p, run)
    checked = []
    for frame, pin in list(zip(frames, pins))[:STOCK_LIMIT]:
        evidence = scoped(db, Evidence, frame.evidence_id, p)
        content = originals.read_original(originals.settings().storage_dir, p.workspace_id, evidence)
        checked.append({'frame_id': frame.id, 'sha256': pin['sha256'],
            'visual': inspect_image(content, evidence.media_type)['visual']})
    return {'run_id': run.id, 'total_frames': len(frames), 'checked_frames': len(checked),
        'limit': STOCK_LIMIT, 'truncated': len(checked) < len(frames), 'frames': checked}


def current_target(db, p, bundle):
    """Fresh actual runtime/source permissions; never a historical proof cache."""
    from .instance_api import active
    from .models import MediaChannel, MediaGateway, VideoRun
    from . import media_daily_task_adapter as daily
    from .media import channel_manifest, CAPTURE_HASH
    from .local_video import PIPELINE_HASH
    from .local_specialists import ready_detection_install
    from .video_read_boundary import authority
    from .services import published
    instance, release = active(db, p, bundle['pin']['instance_id'])
    camera = scoped(db, MediaChannel, bundle['slot'].camera_id, p)
    gateway = scoped(db, MediaGateway, camera.gateway_id, p)
    body = {'instance_version': instance.version, 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'business_id': bundle['pin']['business_id'],
        'entity_id': bundle['pin']['entity_ids'][0], 'camera_id': camera.id,
        'camera_version': camera.version, 'camera_hash': daily.camera_hash(camera)}
    pin = daily.target(db, p, instance.id, body)
    source = published(db, p, camera.data.get('capability_id'), 'source')
    need(source.data == channel_manifest(camera, gateway).model_dump(mode='json'), '当前摄像头来源能力已变化，请正常重新登记核验')
    permission = authority(db, p, camera, gateway, 'manual')
    old_run = scoped(db, VideoRun, bundle['pin']['origin']['run_id'], p)
    installation = ready_detection_install(db, p, old_run.installation_id)
    need(all(type(value) is int and value > 0 for value in
        (camera.data['generation'], gateway.data['generation'], installation.data['generation'])),
        '来源或本地安装配置代次无效，未授予读取')
    return {**pin, 'release_data_hash': digest(release.data), 'package_id': release.package_id,
        'channel_generation': camera.data['generation'],
        'gateway_generation': gateway.data['generation'], 'source_id': source.id,
        'source_hash': source.content_hash, 'read_authority': permission,
        'installation_id': installation.id, 'installation_generation': installation.data['generation'],
        'installation_hash': digest(installation.data), 'backend': installation.data['backend'],
        'capture_hash': CAPTURE_HASH, 'pipeline_hash': PIPELINE_HASH}, camera, gateway, installation


def propose(db, p, task_id, raw):
    from . import daily_frame_assessment_api_v233 as qa
    from . import daily_frame_assessment_source_v233 as original
    from .media_daily_task_adapter import current_actor
    from .models import MediaChannel
    body = parse(raw, 'propose'); nonce = body['request_id']; sha = hashlib.sha256(raw).hexdigest()
    lock_nonce(db, p, nonce); prior = recover(db, p, task_id, nonce, sha)
    if prior['found']: return prior['result'], False
    require(p, 'admin', 'operator')
    task = scoped(db, Task, task_id, p)
    scoped(db, DomainInstance, task.data['instance_id'], p, True)
    from .relations import lock_current_actor
    lock_current_actor(db, p); current_actor(db, p)
    assessment, original_job = qa._associated(db, p, task_id, body['assessment_id'])
    original.checked_result(db, p, assessment)
    need(assessment.status == 'completed' and original_job.status == 'succeeded'
        and assessment.data['result_hash'] == body['expected_result_hash'], '请先完成并核对原独立画面核查及其真实后台任务')
    value = assessment.data['request'][original.MARKER]
    bundle = original.read_source(db, p, task_id, expected_pin=value['pin'])
    need(bundle['source_hash'] == value['source_hash'] == body['expected_source_hash']
        and bundle['frame_sha256'] == value['frame_sha256'] == body['expected_frame_sha256'])
    stock = stock_check(db, p, bundle)
    target = None; problems = []
    try: target, _, _, _ = current_target(db, p, bundle)
    except HTTPException as error:
        if error.status_code not in {404, 409, 422}: raise
        problems = [str(error.detail)]
    camera = scoped(db, MediaChannel, bundle['slot'].camera_id, p)
    source_view = {'camera_id': camera.id, 'label': str(camera.data.get('name') or '原摄像头')[:200],
        'configuration_changed': camera.version != bundle['intent'].data['pins']['camera_version']
            or (target is not None and any(target[key] != bundle['intent'].data['pins'][key]
                for key in ('camera_hash', 'gateway_hash', 'release_id', 'entity_hash'))),
        'can_execute': target is not None, 'blocked_reasons': problems}
    projection = assessment.data['result']['records'][0]['daily_frame_result_v233']
    decision = business_decision(projection, assessment_id=assessment.id, result_hash=body['expected_result_hash'],
        source_hash=bundle['source_hash'], frame_sha256=bundle['frame_sha256'], stock=stock, source=source_view)
    frame = frame_view(bundle, task_id, None, original=True)
    row = SupplementPlan(workspace_id=p.workspace_id, instance_id=bundle['pin']['instance_id'],
        assessment_id=assessment.id, owner_id=p.user_id, request_id=nonce, status='proposed', version=1,
        data={FAMILY: {'task_id': task_id, 'assessment_id': assessment.id, 'result_hash': body['expected_result_hash'],
            'source_hash': bundle['source_hash'], 'frame_sha256': bundle['frame_sha256']},
            'decision': decision, 'decision_hash': digest(decision), 'target': target,
            'original_frame': frame, 'original_result': copy.deepcopy(projection),
            'scope_lines': [bundle['view']['entity']['label']+'；同一原摄像头：'+source_view['label'],
                '本次来源/目标配置与原授权不同，必须重新明确确认。' if source_view['configuration_changed'] else '本次核对的是原摄像头当前配置，不等同于现场对象身份或覆盖证明。',
                '先检查已保存资料；仅建议一次本地只读，不转动云台、不保存音轨、不发送云端。',
                '拍摄时间、现场时效、覆盖和领域精度仍未知，不保证补采能改善。'],
            'progress': '业务已核对原核查与存量，建议已保存；尚未读取摄像头', 'error': None})
    db.add(row); db.flush()
    row.data = {**row.data, 'original_frame': frame_view(bundle, task_id, row.id, original=True)}
    save_state(db, p, row, initial=True); record_command(db, p, row, 'propose', raw)
    return command_result(db, p, row, 'propose', nonce, sha), True


def _write_plan(db, p, task_id, plan_id, raw, operation):
    body = parse(raw, operation); nonce = body['request_id']; sha = hashlib.sha256(raw).hexdigest()
    lock_nonce(db, p, nonce); prior = recover(db, p, task_id, nonce, sha)
    if prior['found']:
        need(prior['result']['plan_id'] == plan_id and prior['result']['operation'] == operation)
        return body, prior['result'], None
    if operation == 'approve':
        from .media_monitor import node_lock
        node_lock(db)
    task = scoped(db, Task, task_id, p)
    scoped(db, DomainInstance, task.data['instance_id'], p, True)
    row = scoped(db, SupplementPlan, plan_id, p, True); checked_plan(db, p, task_id, row.id)
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    need(row.version == body['version'], '方案已推进，请保留原请求并重新读取当前状态')
    return body, None, row


def approve(db, p, task_id, plan_id, raw):
    body, prior, row = _write_plan(db, p, task_id, plan_id, raw, 'approve')
    if prior: return prior
    require(p, 'admin')
    need(row.status == 'proposed' and row.data.get('session_id') is None
        and row.data['decision']['source']['can_execute']
        and row.data['decision_hash'] == body['expected_decision_hash'], '当前方案不可执行，请核对原建议')
    from . import frame_supplement_source_v236 as source
    bundle = source.read_original(db, p, row)
    try: target, camera, gateway, installation = current_target(db, p, bundle)
    except HTTPException as error:
        if error.status_code != 404: raise
        raise HTTPException(409, '当前摄像头来源或本地安装已变化/缺失，请保留原方案并重新核对') from None
    need(target == row.data['target'], '摄像头、对象、来源或本地安装已变化，请另建明确建议')
    from .media_monitor import node_lock, queue_capture
    from .continuous_media import reject_source_conflict
    from .models import MediaMonitor
    node_lock(db); reject_source_conflict(db, camera, gateway)
    need(db.scalar(select(MediaMonitor.id).where(MediaMonitor.channel_id == camera.id,
        MediaMonitor.status.in_(['active', 'paused', 'blocked'])).limit(1)) is None,
        '此摄像头已有读取计划，请处理原计划；本方案未读取')
    from .supplement import READ_CONFIGURATION
    session = queue_capture(db, p, camera, gateway, installation, body['seconds'], READ_CONFIGURATION, read_mode='manual')
    session.data = {**session.data, FAMILY: {'plan_id': row.id, 'task_id': task_id, 'decision_hash': row.data['decision_hash']}}
    row.status = 'reading'; row.data = {**row.data, 'approved': {'seconds': body['seconds'], 'once': True,
        'approved_at': now().isoformat(), 'read_configuration': copy.deepcopy(READ_CONFIGURATION)},
        'approved_by': p.user_id, 'session_id': session.id,
        'session_pin': {key: copy.deepcopy(session.data[key]) for key in ('job_id', 'read_reservation_id',
            'read_reservation_hash', 'installation_id', 'installation_generation', 'gateway_generation',
            'channel_generation', 'capture_hash', 'backend', 'configuration', 'seconds')},
        'progress': '一次读取与预算预约已保存，等待实际采集；尚未独立接收'}
    save_state(db, p, row); record_command(db, p, row, 'approve', raw)
    return command_result(db, p, row, 'approve', body['request_id'], hashlib.sha256(raw).hexdigest())


def stop_owned(db, p, row, reason):
    from .models import StreamSession, VideoRun
    identity = row.data.get('session_id')
    if identity:
        session = scoped(db, StreamSession, identity, p, True)
        if session.status in {'queued', 'connecting', 'capturing'}:
            session.data = {**session.data, 'cancel_requested': True}
        if session.video_run_id:
            run = scoped(db, VideoRun, session.video_run_id, p, True)
            if run.status in {'queued', 'running'}: run.data = {**run.data, 'cancel_requested': True}
    identity = row.data.get('followup_assessment_id')
    if identity:
        assessment = scoped(db, EvidenceAssessment, identity, p, True)
        if assessment.status in {'queued', 'running'}:
            assessment.status = 'cancelled'; assessment.version += 1
            assessment.data = {**assessment.data, 'progress': reason}
            audit(db, p, 'data.evidence_assessment_cancelled', assessment.id, {})


def cancel(db, p, task_id, plan_id, raw):
    body, prior, row = _write_plan(db, p, task_id, plan_id, raw, 'cancel')
    if prior: return prior
    require(p, 'admin', 'operator'); need(p.role == 'admin' or row.owner_id == p.user_id, '请由原作者或管理员停止', 403)
    need(row.status in {'proposed', 'reading', 'rechecking'}, '方案已经结束，原结果保留')
    stop_owned(db, p, row, body['reason'])
    row.status = 'cancelled'; row.data = {**row.data, 'progress': '已停止本方案；原件与核查保留', 'stop_reason': body['reason']}
    save_state(db, p, row); record_command(db, p, row, 'cancel', raw)
    return command_result(db, p, row, 'cancel', body['request_id'], hashlib.sha256(raw).hexdigest())


def frame_view(bundle, task_id, plan_id, *, original=False):
    view = bundle['view']; image = view['image']; timing = view['time']
    return {key: image[key] for key in ('sha256', 'media_type', 'size_bytes', 'width', 'height')} | {
        'source_hash': bundle['source_hash'],
        'content_path': '/api/workspace/tasks/'+task_id+'/frame-supplements/'+str(plan_id)+'/content',
        'time': {'attempt_day': timing.get('attempt_day'), 'received_day': timing.get('received_day'),
            'capture_requested_at': timing.get('capture_requested_at'),
            'image_registered_at': timing['image_registered_at'], 'scene_observed_at': None, 'scene_time_trust': 'unknown'}}


def assessment_view(db, p, row):
    from . import frame_supplement_source_v236 as source
    identity = row.data.get('followup_assessment_id')
    if not identity: return None
    assessment = scoped(db, EvidenceAssessment, identity, p); job = scoped(db, Job, assessment.data['job_id'], p)
    status = assessment.status; error = None; result = result_hash = None
    try:
        source.checked_result(db, p, assessment)
        if 'result' in assessment.data:
            result = copy.deepcopy(assessment.data['result']['records'][0]['supplement_frame_result_v236'])
            result_hash = assessment.data['result_hash']
    except (HTTPException, KeyError, TypeError, ValueError):
        status = 'blocked_integrity'; error = '后继核查的独立证明无法核对，原记录保留'
    if status != 'blocked_integrity' and (job.status.startswith(('failed', 'blocked')) or job.status == 'lease_lost'):
        status = 'failed'; error = '后继本地核查未完成，未自动另建请求'
    if status != 'blocked_integrity' and job.status == 'cancelled': status = 'cancelled'
    if status != 'blocked_integrity' and assessment.status == 'completed' and job.status in {'queued', 'running'}:
        status = 'running'; result = result_hash = None
    return {'id': assessment.id, 'version': assessment.version, 'status': status, 'job_id': job.id,
        'progress': assessment.data.get('progress', ''), 'error': error, 'result_hash': result_hash, 'result': result}


def plan_view(db, p, row):
    check_row(db, p, row)
    from .frame_supplement_source_v236 import parent
    original_assessment = parent(db, p, row, require_succeeded=False)  # metadata only; no old files
    session_view = None; followup = assessment_view(db, p, row); status = row.status
    progress = row.data['progress']; error = row.data.get('error')
    parent_job = scoped(db, Job, original_assessment.data['job_id'], p)
    parent_failed = parent_job.status != 'succeeded'
    if parent_failed and status != 'cancelled':
        status = 'blocked'; error = '原核查的真实后台任务未成功结算，不能继续新读取；原回执保留'
    if row.data.get('session_id'):
        from .models import StreamSession, VideoRun
        session = scoped(db, StreamSession, row.data['session_id'], p)
        run = scoped(db, VideoRun, session.video_run_id, p) if session.video_run_id else None
        capture = scoped(db, Job, session.data['job_id'], p)
        processing = scoped(db, Job, run.data['job_id'], p) if run else None
        failed = session.status in {'failed', 'cancelled', 'blocked'} or capture.status.startswith(('failed', 'blocked')) or capture.status == 'cancelled' or bool(run and run.status in {'failed', 'cancelled', 'blocked'}) or bool(processing and (processing.status.startswith(('failed', 'blocked')) or processing.status == 'cancelled'))
        if failed and status not in {'cancelled', 'completed'}:
            status = 'blocked'; error = '本次实际读取或本地处理失败/停止，未自动重新读取'
        if status == 'reading' and not failed and session.status != 'partial':
            progress = '等待数据管理独立接收本次真实片段' if run and run.status == 'succeeded' else '正在本地处理本次片段' if run else '等待或正在采集本次片段'
        session_view = {'id': session.id, 'job_id': capture.id, 'run_id': session.video_run_id,
            'capture_status': session.status, 'processing_status': run.status if run else None,
            'receipt_status': 'received' if row.data.get('received_source') else 'blocked' if failed or status in {'blocked', 'cancelled'} else 'waiting'}
    if followup and followup['status'] in {'failed', 'blocked_integrity', 'cancelled'} and status != 'cancelled':
        status = 'blocked'; error = followup['error'] or '后继核查已停止，原件保留'
    comparison = copy.deepcopy(row.data.get('comparison'))
    if comparison is not None:
        need(followup is not None)
        if followup['status'] == 'completed':
            need(comparison == make_comparison(row, followup['result'], followup['result_hash']))
        else: comparison = None
    return {'schema': 'frame-supplement-plan-v236', 'id': row.id, 'version': row.version,
        'task_id': row.data[FAMILY]['task_id'], 'instance_id': row.instance_id, 'assessment_id': row.assessment_id,
        'status': status, 'created_at': row.created_at.isoformat(), 'progress': progress, 'error': error,
        'decision_hash': row.data['decision_hash'], 'decision': copy.deepcopy(row.data['decision']),
        'scope_lines': copy.deepcopy(row.data['scope_lines']), 'approved': copy.deepcopy(row.data.get('approved')),
        'original': copy.deepcopy(row.data['original_frame']), 'supplement': copy.deepcopy(row.data.get('supplement_frame')),
        'session': session_view, 'followup': followup, 'comparison': comparison,
        'permissions': {'can_read': True, 'can_approve': p.role == 'admin' and status == 'proposed'
            and row.data['decision']['source']['can_execute'],
            'can_cancel': status in {'proposed', 'reading', 'rechecking'} and p.role in {'admin', 'operator'}
                and (p.role == 'admin' or row.owner_id == p.user_id)}}


def assessment_nonce(row): return 'frame-supplement-assess-'+row.id


def block(db, p, row, reason):
    check_row(db, p, row)
    if row.status in {'completed', 'cancelled', 'blocked'}: return
    stop_owned(db, p, row, '补充方案已阻止，原结果保留')
    row.status = 'blocked'; row.data = {**row.data, 'error': reason,
        'progress': '本方案已阻止，实际读取与原失败保留；没有重新读取'}
    save_state(db, p, row)


def make_comparison(row, after, after_hash):
    before = copy.deepcopy(row.data['original_result'])
    need(before['source_hash'] == row.data[FAMILY]['source_hash']
        and after['current_state_sufficient'] is False and after['state_conclusion'] is None)
    return {'schema': 'frame-supplement-comparison-v236',
        'before_assessment_id': row.assessment_id, 'after_assessment_id': row.data['followup_assessment_id'],
        'before_result_hash': row.data[FAMILY]['result_hash'], 'after_result_hash': after_hash,
        'before_frame_sha256': before['frame_sha256'], 'after_frame_sha256': after['frame_sha256'],
        'same_original': before['frame_sha256'] == after['frame_sha256'],
        'new_scene_information_verified': False, 'current_state_sufficient': False, 'state_conclusion': None,
        'before': before, 'after': copy.deepcopy(after),
        'remaining_gaps': sorted({item['kind'] for item in after['gaps']}),
        'limitations': ['相同原件不算新增信息；不同SHA也不证明新现场或质量提高。',
            '只比较同版本未校准技术指标；不认证时间、覆盖、物种可识别性或对象状态。',
            '本次一次读取结束；剩余缺口保留，不会无限补采。']}


def advance(db, p, row):
    """Caller owns the Instance then Plan lock and commits this transaction."""
    check_row(db, p, row)
    if row.status not in {'reading', 'rechecking'}: return
    from . import frame_supplement_source_v236 as source
    from .media_daily_task_adapter import current_actor
    current_actor(db, p); require(p, 'admin')
    need(p.user_id == row.data['approved_by'])
    if row.status == 'reading':
        session = source._fixed_session(db, p, row)
        from .models import VideoRun
        capture = scoped(db, Job, session.data['job_id'], p)
        run = scoped(db, VideoRun, session.video_run_id, p) if session.video_run_id else None
        processing = scoped(db, Job, run.data['job_id'], p) if run else None
        if session.status in {'failed', 'cancelled', 'blocked'} or capture.status.startswith(('failed', 'blocked')) or capture.status == 'cancelled' or (run and run.status in {'failed', 'cancelled', 'blocked'}):
            block(db, p, row, '本次真实采集或本地处理未成功；没有新核查或第二次读取')
            return
        if processing and (processing.status.startswith(('failed', 'blocked')) or processing.status == 'cancelled'):
            block(db, p, row, '本次实际处理任务未成功结算；没有后继核查或第二次读取')
            return
        if session.status == 'partial':
            import math
            receipt = session.data.get('receipt', {})
            actual = receipt.get('duration_seconds') if isinstance(receipt, dict) else None
            requested = row.data['approved']['seconds']
            actual_text = format(actual, 'g') if type(actual) in {int, float} and math.isfinite(actual) and actual >= 0 else '未知'
            duration = '实际保存'+actual_text+'秒 / 请求'+str(requested)+'秒'
            if capture.status != 'succeeded':
                progress = '部分片段已保存（'+duration+'），等待原采集任务结算；不是完整读取，没有再次采集'
            elif run is None or processing is None:
                block(db, p, row, '部分片段已保存（'+duration+'），但没有可核对的本地处理；未完成完整补采，原件保留且不重新读取')
                return
            else:
                need(processing.kind == 'process_video' and processing.data.get('run_id') == run.id)
                if run.status == processing.status == 'succeeded':
                    block(db, p, row, '本次仅为部分片段（'+duration+'），未完成完整补采；本地处理及已有画面、回执保留，不创建完整补采核查，也不重新读取')
                    return
                progress = ('部分片段已保存（'+duration+'），原处理结果仍在结算；不是完整读取，没有再次采集'
                    if run.status == 'succeeded' else '部分片段已保存（'+duration+'），正在等待或进行本地处理；不是完整读取，没有再次采集')
            if row.data.get('progress') != progress:
                row.data = {**row.data, 'progress': progress}
                save_state(db, p, row)
            return
        if session.status != 'captured' or capture.status != 'succeeded' or not run or run.status != 'succeeded': return
        need(processing.kind == 'process_video' and processing.data.get('run_id') == run.id)
        if processing.status != 'succeeded': return
        try: bundle = source.read_followup(db, p, row)
        except source.ReceiptPending: return
        from . import evidence_assessment_api as normal
        class BoundRequest(normal.Request):
            supplement_frame_v236: dict
        marker = {'plan_id': row.id, 'task_id': row.data[FAMILY]['task_id'],
            'parent_assessment_id': row.assessment_id, 'source_hash': bundle['source_hash'],
            'frame_sha256': bundle['frame_sha256'], 'pin': copy.deepcopy(bundle['pin'])}
        request = BoundRequest(instance_id=row.instance_id, release_id=row.data['target']['release_id'],
            entity_ids=bundle['pin']['entity_ids'], purpose='核查本次补充读取的单张画面，保留所有未认证边界',
            goal='current_state', required_modalities=['video'], lookback_hours=24,
            request_id=assessment_nonce(row), parent_id=row.assessment_id, supplement_frame_v236=marker)
        # An orphan derived request must never be adopted or completed here.
        prior = db.scalar(select(EvidenceAssessment.id).where(EvidenceAssessment.workspace_id == p.workspace_id,
            EvidenceAssessment.request_id == assessment_nonce(row)))
        need(prior is None)
        traces = db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key.like('assessment:%'), Job.data[source.MARKER]['plan_id'].as_string() == row.id).limit(1))
        need(traces is None)
        outcome = {}; assessment = normal.create_saved(request, p, db, outcome=outcome)
        need(outcome.get('created') is True)
        job = scoped(db, Job, assessment.data['job_id'], p)
        job.data = {**job.data, source.MARKER: {'plan_id': row.id, 'task_id': marker['task_id'], 'marker_hash': digest(marker)}}
        event = {'plan_id': row.id, 'task_id': marker['task_id'], 'assessment_id': assessment.id,
            'parent_assessment_id': row.assessment_id, 'request_hash': assessment.data['request_hash'],
            'creation_hash': assessment.data['creation_hash'], 'job_id': job.id, 'marker_hash': digest(marker)}
        audit(db, p, source.CREATE, assessment.id, event)
        row.status = 'rechecking'; row.data = {**row.data, 'received_source': copy.deepcopy(bundle['pin']['receipt']),
            'supplement_frame': frame_view(bundle, marker['task_id'], row.id),
            'followup_assessment_id': assessment.id, 'progress': '本次片段已独立接收，新的固定画面核查已保存排队'}
        save_state(db, p, row)
        source.checked_association(db, p, assessment)
        return
    after = assessment_view(db, p, row)
    need(after is not None)
    if after['status'] in {'queued', 'running'}: return
    if after['status'] != 'completed':
        block(db, p, row, '后继核查未成功，原失败保留；没有再次读取')
        return
    # Re-read both real originals/receipt after the QA result, never turn a
    # mere result hash or processing completion into an independent receipt.
    source.read_followup(db, p, row)
    comparison = make_comparison(row, after['result'], after['result_hash'])
    row.status = 'completed'; row.data = {**row.data, 'comparison': comparison,
        'progress': '本次补充与比较已保存；剩余缺口保留，未认证现场状态'}
    save_state(db, p, row)


def execution_guard(db, p, *, session_id=None, reservation=None):
    """V236-only current scope; no recursive ready_install or source preview.

    Reverse Plan and State witnesses make removing the Session marker fail
    closed. No saved-source reader calls this live execution-only function.
    """
    from .models import StreamSession, InstanceRelease, MediaChannel, MediaGateway, Entity, SpecialistInstallation
    from .media_daily_task_adapter import current_actor, camera_hash
    from .services import published
    from .video_read_boundary import authority
    from .entity_release_compat import require_entity
    if session_id is None:
        key = reservation.data['receipt']['request']['request_key'] if reservation else ''
        if not key.startswith('clip:'): return
        session_id = key[5:]
    session = db.scalar(select(StreamSession).where(StreamSession.workspace_id == p.workspace_id,
        StreamSession.id == session_id))
    plans = list(db.scalars(select(SupplementPlan).where(SupplementPlan.workspace_id == p.workspace_id,
        SupplementPlan.data['session_id'].as_string() == session_id).limit(2)))
    witnesses = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == STATE, Audit.data['snapshot']['data']['session_id'].as_string() == session_id).limit(41)))
    marker = session.data.get(FAMILY) if session else None
    if not marker and not plans and not witnesses: return
    need(session is not None and len(plans) == 1 and 0 < len(witnesses) <= 40)
    row = check_row(db, p, plans[0]); target = row.data['target']
    need(marker == {'plan_id': row.id, 'task_id': row.data[FAMILY]['task_id'], 'decision_hash': row.data['decision_hash']}
        and {event.object_id for event in witnesses} == {row.id}
        and row.status == 'reading' and p.user_id == row.data['approved_by'], '本次补充读取已停止或不再持有授权')
    current_actor(db, p); require(p, 'admin')
    from .frame_supplement_source_v236 import _fixed_session, parent
    parent(db, p, row)
    _fixed_session(db, p, row)
    instance = scoped(db, DomainInstance, row.instance_id, p)
    need(instance.status == 'partially_active' and instance.active_release_id == target['release_id']
        and instance.version == target['instance_version']
        and target['business_id'] in instance.data.get('enabled_business_ids', []), '原实例业务已暂停或授权范围已变化')
    release = scoped(db, InstanceRelease, instance.active_release_id, p)
    need(release.configuration_hash == target['configuration_hash'] and digest(release.data) == target['release_data_hash'])
    published(db, p, release.package_id, 'domain_package')
    entity = scoped(db, Entity, target['entity_id'], p)
    definition = require_entity(db, p, instance, release, entity, lock_sources=False)
    need(entity.version == target['entity_version'] and digest({'data': entity.data, 'definition': definition}) == target['entity_hash'])
    camera = scoped(db, MediaChannel, target['camera_id'], p)
    gateway = scoped(db, MediaGateway, camera.gateway_id, p)
    source = published(db, p, target['source_id'], 'source')
    need(camera.version == target['camera_version'] and camera_hash(camera) == target['camera_hash']
        and gateway.id == target['gateway_id'] and gateway.version == target['gateway_version']
        and digest(gateway.data) == target['gateway_hash'] and source.content_hash == target['source_hash']
        and authority(db, p, camera, gateway, 'manual') == target['read_authority'])
    installation = scoped(db, SpecialistInstallation, target['installation_id'], p)
    need(installation.status == 'ready' and digest(installation.data) == target['installation_hash'])
    return True
