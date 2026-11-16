"""Cold historical provenance for a genuine Daily Task's one copied image."""
import copy
from datetime import timezone
from urllib.parse import quote
from sqlalchemy import select
from fastapi import HTTPException
from .contracts import digest
from .models import Audit, DomainInstance, Entity, Evidence, InstanceRelease, Job, Task, VideoFrame
from .services import scoped
from . import media_daily_task_adapter as daily

MARKER = 'daily_task_frame_v233'
DEPENDENCY = 'DailyTaskFrameV233'
RESULT_AUDIT = 'data.daily_frame_assessed_v233'
ERROR = '原任务固定输入、画面原件或独立接收证明无法核对；没有重新采集或改写原记录'


def need(value):
    if not value: raise HTTPException(409, ERROR)


def _one(db, p, action, identity):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).limit(2)))
    need(len(rows) == 1)
    return rows[0]


def read_source(db, p, task_id, *, expected_pin=None):
    try: return _read_source(db, p, task_id, expected_pin=expected_pin)
    except HTTPException: raise
    except (ValueError, TypeError, KeyError, AttributeError, OSError, IndexError):
        raise HTTPException(409, ERROR) from None


def _read_source(db, p, task_id, *, expected_pin=None):
    daily.current_actor(db, p)
    task = scoped(db, Task, task_id, p)
    identity = task.data['instance_id']
    intents, slots = daily.inventory(db, p, identity)
    matched = [slot for slot in slots if slot.task_id == task.id]
    need(len(matched) == 1)
    slot = matched[0]; intent = next(row for row in intents if row.id == slot.intent_id)
    from .workspace_api import saved_execution
    from .report_text_parse_scope import report_text_parse_scope
    with db.no_autoflush, report_text_parse_scope():
        checked_task, release, _ = saved_execution(db, p, task_id)
    need(checked_task is task and release.id == slot.data['body']['release_id']
        and release.configuration_hash == intent.data['configuration']['configuration_hash'] == intent.data['pins']['configuration_hash'])
    image = scoped(db, Evidence, slot.data['image_id'], p)
    need(image.id != slot.data['source']['frame']['evidence_id'] and image.data['uploaded_by'] == intent.actor_id)
    upload = _one(db, p, 'evidence.upload', image.id)
    need(upload.actor_id == intent.actor_id and upload.data == {'media_type': image.media_type,
        'sha256': image.sha256, 'cloud_model_id': None, 'consent': False})
    enable = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == daily.COMMAND, Audit.object_id == intent.id,
        Audit.data['operation'].as_string() == 'enable').limit(2)))
    born = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == daily.STATE, Audit.object_id == slot.id,
        Audit.data['snapshot']['status'].as_string() == 'created').limit(2)))
    need(len(enable) == len(born) == 1)
    need(born[0].data['snapshot']['task_id'] == task.id and born[0].data['snapshot']['data'] == slot.data)
    snapshots = task.data['image_snapshots']; objects = task.data['object_snapshots']
    need(len(snapshots) == len(objects) == 1 and objects[0]['id'] == slot.entity_id)
    from .entity_release_compat import verify_task_binding_snapshot, BINDINGS_HASH
    object_bindings = verify_task_binding_snapshot(task, release)
    need(object_bindings is not None)
    need(type(intent.data['pins']['entity_version']) is int
        and objects[0]['version'] == intent.data['pins']['entity_version']
        and digest({'data': objects[0]['attributes'], 'definition': object_bindings['objects'][0]['definition']})
            == intent.data['pins']['entity_hash'])
    pin = {'schema': 'daily-frame-fixed-input-v233', 'workspace_id': p.workspace_id, 'task_id': task.id,
        'instance_id': identity, 'release_id': release.id, 'configuration_hash': release.configuration_hash, 'package_id': task.package_id,
        'business_id': slot.business_id, 'entity_ids': list(task.data['entity_ids']), 'creator_id': task.owner_id,
        'creation_request_id': task.data['creation_request_id'], 'creation_hash': task.data['creation_hash'],
        'image_snapshot': copy.deepcopy(snapshots[0]), 'intent_id': intent.id,
        'object_snapshots': copy.deepcopy(objects), 'object_definition_bindings': copy.deepcopy(object_bindings),
        'object_definition_bindings_hash': task.data[BINDINGS_HASH],
        'enable_command_hash': enable[0].data['record_hash'], 'slot_id': slot.id,
        'slot_created_hash': born[0].data['record_hash'], 'origin': copy.deepcopy(slot.data['source'])}
    from .evidence_assessment import ref
    pin['image_extended_ref'] = ref(image, extended=True)
    need(expected_pin is None or pin == expected_pin)
    source_hash = digest(pin)
    content = daily.originals.read_original(daily.originals.settings().storage_dir, p.workspace_id, image)
    from .evidence import inspect_content
    actual_image = inspect_content(content, image.media_type)
    frame = scoped(db, VideoFrame, slot.data['source']['frame']['id'], p)
    need(actual_image['modality'] == image.data.get('modality') == 'image'
        and all(type(image.data.get(key)) is int and image.data[key] > 0
            and image.data[key] == actual_image[key] == frame.data[key] for key in ('width', 'height')))
    entity = scoped(db, Entity, slot.entity_id, p); instance = scoped(db, DomainInstance, identity, p)
    attrs = objects[0].get('attributes', {})
    label = next((str(attrs[field['key']]) for field in objects[0].get('fields', [])
        if field.get('kind') == '文本' and attrs.get(field.get('key'))), entity.external_id)
    event = scoped(db, daily.Event, slot.data['source']['event_id'], p)
    proof = daily.delivery.checked(db, p, event)
    records = [item for item in proof['body']['summary']['records'] if digest(item) == slot.data['source']['record_hash']]
    need(len(records) == 1)
    limitations = ['只核查一张已保存画面，不能代表全部视频与对象覆盖',
        '对象关联来自已确认的任务范围，不是物理身份或当前状态证明',
        '拍摄时间未知；批次、接收和图片登记时间不得替代拍摄时间', *slot.data['source']['limitations']]
    view = {'schema': 'daily-frame-source-v233', 'task_id': task.id, 'task_title': task.title,
        'instance_id': identity, 'release_id': release.id, 'business_id': slot.business_id,
        'entity': {'id': slot.entity_id, 'label': label}, 'source_hash': source_hash,
        'image': {'id': image.id, 'sha256': image.sha256, 'media_type': image.media_type,
            'size_bytes': image.size_bytes, 'width': image.data['width'], 'height': image.data['height'],
            'content_path': '/api/workspace/tasks/'+quote(task.id, safe='')+'/daily-frame/content'},
        'time': {'attempt_day': slot.day, 'received_day': records[0].get('received_day'),
            'scene_observed_at': None, 'scene_time_trust': 'unknown',
            'image_registered_at': image.created_at.astimezone(timezone.utc).isoformat()},
        'limitations': limitations, 'permissions': {'can_read': True,
            'can_assess': p.role in {'admin', 'operator'} and instance.status == 'partially_active'},
        'scope': 'saved_single_frame_not_verified_coverage', 'physical_state_verified': False, 'cloud_calls': 0}
    return {'view': view, 'pin': pin, 'content': content, 'task': task, 'image': image, 'slot': slot,
        'intent': intent, 'source_hash': source_hash, 'frame_sha256': image.sha256}


def dependency(bundle):
    return {'type': DEPENDENCY, 'id': bundle['task'].id, 'signature': bundle['source_hash'],
        'frame_sha256': bundle['frame_sha256'], 'pin': copy.deepcopy(bundle['pin'])}


def recheck_dependency(db, p, value):
    need(type(value) is dict and set(value) == {'type', 'id', 'signature', 'frame_sha256', 'pin'}
        and value['type'] == DEPENDENCY)
    result = read_source(db, p, value['id'], expected_pin=value['pin'])
    need(result['source_hash'] == value['signature'] and result['frame_sha256'] == value['frame_sha256'])
    return result


def marker(bundle):
    return {'task_id': bundle['task'].id, 'source_hash': bundle['source_hash'],
        'frame_sha256': bundle['frame_sha256'], 'pin': copy.deepcopy(bundle['pin'])}


def collect(db, p, request):
    value = request[MARKER]
    need(type(value) is dict and set(value) == {'task_id', 'source_hash', 'frame_sha256', 'pin'})
    bundle = recheck_dependency(db, p, {'type': DEPENDENCY, 'id': value['task_id'],
        'signature': value['source_hash'], 'frame_sha256': value['frame_sha256'], 'pin': value['pin']})
    need(request['instance_id'] == bundle['pin']['instance_id']
        and request['entity_ids'] == bundle['pin']['entity_ids'] and request['required_modalities'] == ['video']
        and request['goal'] == 'current_state' and request['lookback_hours'] == 24)
    from .evidence_assessment import ref
    objects = [scoped(db, Entity, identity, p) for identity in request['entity_ids']]
    need([ref(row) for row in objects] == request['object_refs'])
    from .daily_frame_quality_v233 import assessment_record
    from .models import now
    return [assessment_record(bundle, request, assessed_at=now().isoformat())], [*[ref(row) for row in objects], dependency(bundle)], False


def result_proof(row):
    value = row.data['request'][MARKER]
    return {'schema': 'daily-frame-assessed-proof-v233', 'assessment_id': row.id,
        'task_id': value['task_id'],
        'job_id': row.data['job_id'], 'request_hash': row.data['request_hash'],
        'result_hash': row.data['result_hash'], 'source_hash': value['source_hash'],
        'frame_sha256': value['frame_sha256']}


def checked_result(db, p, row):
    from .evidence_assessment import checked
    checked(row)
    if 'result' not in row.data:
        witnessed = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == RESULT_AUDIT, Audit.object_id == row.id).limit(1))
        job = scoped(db, Job, row.data['job_id'], p)
        need(row.status != 'completed' and 'result_hash' not in row.data and witnessed is None
            and not (job.status == 'succeeded' and row.status != 'cancelled'))
        return row
    event = _one(db, p, RESULT_AUDIT, row.id)
    need(event.actor_id == row.owner_id and event.data == result_proof(row))
    result = row.data['result']; records = result['records']
    need(len(records) == 1 and records[0]['kind'] == 'daily_task_frame'
        and result['cloud_calls'] == 0 and result['entity_state_updated'] is False)
    projection = records[0]['daily_frame_result_v233']; value = row.data['request'][MARKER]
    need(projection['source_hash'] == value['source_hash'] and projection['frame_sha256'] == value['frame_sha256']
        and projection['source_time_verified'] is False and projection['domain_quality_verified'] is False
        and projection['current_state_sufficient'] is False and projection['state_conclusion'] is None
        and projection['cloud_calls'] == 0 and projection['entity_state_updated'] is False)
    return row
