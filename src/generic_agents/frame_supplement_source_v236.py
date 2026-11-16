"""Cold generic-camera receipts for an explicit one-read supplement.

An object's association comes from the approved plan, never a fabricated native
Run.domain_binding. Acquisition clocks and domain quality remain unverified.
"""
import copy
from datetime import timezone
from urllib.parse import quote
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import Audit, Entity, Event, Evidence, EvidenceAssessment, Job, StreamSession, VideoRun
from .services import audit, scoped
from . import daily_frame_assessment_source_v233 as original
from . import media_daily_task_adapter as daily

MARKER = 'supplement_frame_v236'
DEPENDENCY = 'SupplementFrameV236'
CREATE = 'data.supplement_frame_requested_v236'
RESULT = 'data.supplement_frame_assessed_v236'
ERROR = '本方案的原核查、新画面或独立收件证明无法核对；未替换旧画面'


def need(value):
    if not value: raise HTTPException(409, ERROR)


class ReceiptPending(HTTPException):
    def __init__(self): super().__init__(409, '本次读取尚无真实独立接收回执，继续保留原读取')


def parent(db, p, row, *, require_succeeded=True):
    from .daily_frame_assessment_api_v233 import _associated
    from .frame_supplement_v236 import FAMILY
    marker = row.data[FAMILY]
    assessment, job = _associated(db, p, marker['task_id'], row.assessment_id)
    original.checked_result(db, p, assessment)
    need(assessment.status == 'completed' and assessment.data['result_hash'] == marker['result_hash']
        and assessment.data['request'][original.MARKER]['source_hash'] == marker['source_hash']
        and assessment.data['request'][original.MARKER]['frame_sha256'] == marker['frame_sha256'])
    if require_succeeded: need(job.status == 'succeeded')
    return assessment


def read_original(db, p, row):
    try: return _read_original(db, p, row)
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError, OSError, IndexError):
        raise HTTPException(409, ERROR) from None


def _read_original(db, p, row):
    assessment = parent(db, p, row)
    return original.read_source(db, p, assessment.data['request'][original.MARKER]['task_id'],
        expected_pin=assessment.data['request'][original.MARKER]['pin'])


def _fixed_session(db, p, row):
    from .frame_supplement_v236 import FAMILY
    session = scoped(db, StreamSession, row.data['session_id'], p)
    approved = row.data['approved']
    need(all(session.data.get(key) == value for key, value in row.data['session_pin'].items()))
    need(session.channel_id == row.data['target']['camera_id']
        and session.data.get('frame_supplement_v236') == {'plan_id': row.id,
            'task_id': row.data[FAMILY]['task_id'], 'decision_hash': row.data['decision_hash']}
        and session.data['seconds'] == approved['seconds']
        and session.data['configuration'] == approved['read_configuration']
        and session.data['installation_id'] == row.data['target']['installation_id']
        and session.data['installation_generation'] == row.data['target']['installation_generation']
        and session.data['channel_generation'] == row.data['target']['channel_generation']
        and session.data['gateway_generation'] == row.data['target']['gateway_generation']
        and session.data['capture_hash'] == row.data['target']['capture_hash']
        and session.data['backend'] == row.data['target']['backend'])
    captures = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.kind == 'capture_media', Job.data['session_id'].as_string() == session.id).limit(2)))
    need(len(captures) == 1 and captures[0].id == session.data['job_id']
        and captures[0].data['actor_id'] == row.data['approved_by'])
    matches = list(db.scalars(select(StreamSession.id).where(StreamSession.workspace_id == p.workspace_id,
        StreamSession.data['frame_supplement_v236']['plan_id'].as_string() == row.id).limit(2)))
    need(matches == [session.id])
    return session


def _find_receipt(db, p, row, session):
    from . import media_daily_delivery as delivery
    # Once fixed, only its immutable publication is valid; don't switch to a
    # later revision or new frame when an existing proof becomes damaged.
    fixed = row.data.get('received_source')
    query = select(Event).where(Event.workspace_id == p.workspace_id,
        Event.kind == delivery.KIND, Event.data['modality'].as_string() == 'video',
        Event.data['source_key'].as_string() == 'channel:'+session.channel_id)
    if fixed:
        events = list(db.scalars(query.where(Event.id == fixed['event_id']).limit(2)))
        need(len(events) == 1)
    else:
        events = list(db.scalars(query.order_by(Event.created_at, Event.id).limit(1001)))
        need(len(events) <= 1000)
    for event in events:
        records = event.data.get('summary', {}).get('records', [])
        need(type(records) is list and len(records) <= 100)
        exact = [record for record in records if record.get('id') == session.id]
        if not exact: continue
        need(len(exact) == 1)
        proof = delivery.checked(db, p, event)
        if proof['state']['status'] != 'received':
            if fixed: need(False)
            continue
        delivery.validate_sources(db, p, proof['body']); proof['event_id'] = event.id
        return proof, exact[0]
    raise ReceiptPending()


def read_followup(db, p, row, *, expected_pin=None):
    try: return _read_followup(db, p, row, expected_pin=expected_pin)
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError, OSError, IndexError):
        raise HTTPException(409, ERROR) from None


def _read_followup(db, p, row, *, expected_pin=None):
    from .frame_supplement_v236 import FAMILY, check_row
    check_row(db, p, row)
    old = read_original(db, p, row); session = _fixed_session(db, p, row)
    run = scoped(db, VideoRun, session.video_run_id, p) if session.video_run_id else None
    need(session.status == 'captured' and run is not None and run.status == 'succeeded')
    proof, record = _find_receipt(db, p, row, session)
    frozen, evidence, content = daily.checked_camera_frame(db, p, proof, record, session.channel_id)
    frozen['limitations'] = ['当日资料未齐，仅采用本次已接收片段，不代表全部尝试。'] if any(
        proof['body']['summary'].get(key) for key in ('provisional', 'pending_ids', 'failed_or_partial_ids')) else []
    need(frozen['session_id'] == session.id and frozen['run_id'] == run.id
        and run.data['pipeline_hash'] == row.data['target']['pipeline_hash'])
    if row.data.get('received_source'): need(row.data['received_source'] == frozen)
    from .evidence_assessment import ref
    pin = {'schema': 'supplement-frame-source-v236', 'workspace_id': p.workspace_id,
        'plan_id': row.id, 'task_id': row.data[FAMILY]['task_id'],
        'instance_id': row.instance_id, 'entity_ids': old['pin']['entity_ids'],
        'original_source_hash': old['source_hash'], 'original_result_hash': row.data[FAMILY]['result_hash'],
        'decision_hash': row.data['decision_hash'], 'approved': copy.deepcopy(row.data['approved']),
        'approved_by': row.data['approved_by'], 'target': copy.deepcopy(row.data['target']),
        'receipt': copy.deepcopy(frozen), 'frame_extended_ref': ref(evidence, extended=True),
        'image_snapshot': {'id': evidence.id, 'sha256': evidence.sha256, 'media_type': evidence.media_type,
            'size_bytes': evidence.size_bytes, 'width': evidence.data.get('width'), 'height': evidence.data.get('height')}}
    need(expected_pin is None or pin == expected_pin)
    from .evidence import inspect_content
    actual = inspect_content(content, evidence.media_type)
    need(actual['modality'] == evidence.data.get('modality') == 'image'
        and all(type(evidence.data.get(key)) is int and evidence.data[key] == actual[key]
            for key in ('width', 'height')))
    view = {'task_id': pin['task_id'], 'task_title': old['view']['task_title'],
        'instance_id': row.instance_id, 'entity': copy.deepcopy(old['view']['entity']),
        'image': {'id': evidence.id, 'sha256': evidence.sha256, 'media_type': evidence.media_type,
            'size_bytes': evidence.size_bytes, 'width': actual['width'], 'height': actual['height']},
        'time': {'attempt_day': proof['body']['day'], 'received_day': record.get('received_day'),
            'capture_requested_at': session.created_at.astimezone(timezone.utc).isoformat(),
            'image_registered_at': evidence.created_at.astimezone(timezone.utc).isoformat(),
            'scene_observed_at': None, 'scene_time_trust': 'unknown'},
        'limitations': ['画面关联来自本方案明确授权，不是物理对象或现场时效证明',
            '一次只读新接收不保证新现场信息或画质改善', *frozen['limitations']]}
    source_hash = digest(pin); view['source_hash'] = source_hash
    return {'pin': pin, 'content': content, 'source_hash': source_hash,
        'frame_sha256': evidence.sha256, 'view': view}


def dependency(bundle):
    return {'type': DEPENDENCY, 'id': bundle['pin']['plan_id'], 'signature': bundle['source_hash'],
        'frame_sha256': bundle['frame_sha256'], 'pin': copy.deepcopy(bundle['pin'])}


def recheck_dependency(db, p, value):
    from .frame_supplement_v236 import checked_plan
    need(type(value) is dict and set(value) == {'type', 'id', 'signature', 'frame_sha256', 'pin'}
        and value['type'] == DEPENDENCY and value['id'] == value['pin']['plan_id'])
    row = checked_plan(db, p, value['pin']['task_id'], value['id'])
    bundle = read_followup(db, p, row, expected_pin=value['pin'])
    need(bundle['source_hash'] == value['signature'] and bundle['frame_sha256'] == value['frame_sha256'])
    return bundle


def collect(db, p, request):
    from .daily_frame_quality_v233 import assessment_record
    from .evidence_assessment import ref
    from .models import now
    value = request[MARKER]
    need(type(value) is dict and set(value) == {'plan_id', 'task_id', 'parent_assessment_id', 'source_hash', 'frame_sha256', 'pin'})
    bundle = recheck_dependency(db, p, {'type': DEPENDENCY, 'id': value['plan_id'],
        'signature': value['source_hash'], 'frame_sha256': value['frame_sha256'], 'pin': value['pin']})
    need(request['instance_id'] == bundle['pin']['instance_id']
        and request['entity_ids'] == bundle['pin']['entity_ids'] and request['required_modalities'] == ['video']
        and request['goal'] == 'current_state' and request['parent_id'] == value['parent_assessment_id'])
    objects = [scoped(db, Entity, identity, p) for identity in request['entity_ids']]
    need([ref(row) for row in objects] == request['object_refs'])
    record = assessment_record(bundle, request, assessed_at=now().isoformat())
    record['key'] = DEPENDENCY+':'+value['plan_id']; record['kind'] = 'supplement_frame'
    record['name'] = '本次补充读取独立接收的一张画面'
    record['association_basis'] = 'explicit_supplement_scope_not_verified_coverage'
    record['supplement_frame_result_v236'] = record.pop('daily_frame_result_v233')
    return [record], [*[ref(row) for row in objects], dependency(bundle)], False


def is_record(db, row):
    if MARKER in row.data.get('request', {}): return True
    if db.scalar(select(Audit.id).where(Audit.workspace_id == row.workspace_id,
        Audit.object_id == row.id, Audit.action.in_([CREATE, RESULT])).limit(1)): return True
    if db.scalar(select(Job.id).where(Job.workspace_id == row.workspace_id,
        Job.data['assessment_id'].as_string() == row.id, Job.data[MARKER].is_not(None)).limit(1)): return True
    from .models import SupplementPlan
    from .frame_supplement_v236 import STATE, FAMILY
    if db.scalar(select(SupplementPlan.id).where(SupplementPlan.workspace_id == row.workspace_id,
        SupplementPlan.data[FAMILY].is_not(None),
        SupplementPlan.data['followup_assessment_id'].as_string() == row.id).limit(1)): return True
    return db.scalar(select(Audit.id).where(Audit.workspace_id == row.workspace_id, Audit.action == STATE,
        Audit.data['snapshot']['data']['followup_assessment_id'].as_string() == row.id).limit(1)) is not None


def _audit(db, p, action, identity):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).limit(2)))
    need(len(rows) == 1)
    return rows[0]


def checked_association(db, p, row):
    from .evidence_assessment import checked_request
    from .frame_supplement_v236 import checked_plan
    checked_request(row); value = row.data['request'].get(MARKER)
    need(type(value) is dict and set(value) == {'plan_id', 'task_id', 'parent_assessment_id', 'source_hash', 'frame_sha256', 'pin'})
    plan = checked_plan(db, p, value['task_id'], value['plan_id'])
    event = _audit(db, p, CREATE, row.id); job = scoped(db, Job, row.data['job_id'], p)
    expected = {'plan_id': plan.id, 'task_id': value['task_id'], 'assessment_id': row.id,
        'parent_assessment_id': plan.assessment_id, 'request_hash': row.data['request_hash'],
        'creation_hash': row.data['creation_hash'], 'job_id': job.id, 'marker_hash': digest(value)}
    need(event.actor_id == row.owner_id == plan.data['approved_by'] and event.data == expected
        and row.data['request']['parent_id'] == value['parent_assessment_id'] == plan.assessment_id
        and row.request_id == 'frame-supplement-assess-'+plan.id
        and row.instance_id == plan.instance_id and plan.data.get('followup_assessment_id') == row.id
        and job.kind == 'assess_evidence' and job.idempotency_key == 'assessment:'+row.id
        and job.data['assessment_id'] == row.id and job.data['actor_id'] == row.owner_id
        and job.data.get(MARKER) == {'plan_id': plan.id, 'task_id': value['task_id'], 'marker_hash': digest(value)})
    normal = _audit(db, p, 'data.evidence_assessment_requested', row.id)
    need(normal.actor_id == row.owner_id and normal.data == {'objects': 1, 'modalities': ['video']})
    return job


def result_proof(row):
    marker = row.data['request'][MARKER]
    return {'schema': 'supplement-frame-assessed-v236', 'assessment_id': row.id,
        'job_id': row.data['job_id'], 'request_hash': row.data['request_hash'],
        'result_hash': row.data['result_hash'], 'marker_hash': digest(marker)}


def checked_result(db, p, row):
    try: return _checked_result(db, p, row)
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError, OSError, IndexError):
        raise HTTPException(409, ERROR) from None


def _checked_result(db, p, row):
    if not is_record(db, row): return row
    from .evidence_assessment import checked
    checked(row); job = checked_association(db, p, row)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == RESULT, Audit.object_id == row.id).limit(2)))
    if 'result' not in row.data:
        need(row.status != 'completed' and 'result_hash' not in row.data and not events
            and not (job.status == 'succeeded' and row.status != 'cancelled'))
        return row
    need(len(events) == 1 and events[0].actor_id == row.owner_id and events[0].data == result_proof(row))
    records = row.data['result']['records']; value = row.data['request'][MARKER]
    need(len(records) == 1 and records[0]['kind'] == 'supplement_frame')
    result = records[0]['supplement_frame_result_v236']
    need(result['source_hash'] == value['source_hash'] and result['frame_sha256'] == value['frame_sha256']
        and all(result[key] is False for key in ('source_time_verified', 'domain_quality_verified',
            'current_state_sufficient', 'entity_state_updated')) and result['state_conclusion'] is None
        and type(result['cloud_calls']) is int and result['cloud_calls'] == 0)
    return row
