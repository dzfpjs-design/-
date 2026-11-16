"""ACK-only environmental sound rows in the existing audio/media daily mechanism.

No recorder time is inferred; old ASR cohorts, Memories and delivery revisions
are never re-signed. Unreceived work is a separate read-only status inventory.
"""
import copy
from datetime import date
from types import SimpleNamespace
from sqlalchemy import select, or_, text
from fastapi import HTTPException
from . import models as m
from .contracts import digest
from .sound_models import EnvironmentSoundRun
from . import native_sound_provenance as origin
from .native_sound_receipts import receipt_projection

KIND = 'environment_sound'
MAX_RECORDS = 1000
ERROR = '声音每日记录的原件、来源或独立接收凭据无法核对；原日版本保留'


def need(value):
    if not value: raise HTTPException(409, ERROR)


def checked_day(value):
    try:
        need(type(value) is str and date.fromisoformat(value).isoformat() == value)
    except (TypeError, ValueError):
        need(False)
    return value


def _context(db, p, identity):
    from .workflow_sound import checked_saved_run
    context = checked_saved_run(db, p, identity)
    rows = origin.observations(db, p, identity)
    need(len(rows) == 1)
    context = origin.checked_observation(db, p, rows[0])
    reception = receipt_projection(db, p, context['run'])
    need(reception['state'] == 'received' and reception['receipt'] is not None and reception['memory_id'])
    return {**context, 'reception': reception}


def checked_source(db, p, row):
    context = _context(db, p, row.id)
    from .sound_inputs import input_bytes
    return input_bytes(db, p, context['input'])


def _review_refs(db, p, run):
    result = []
    for value in run.data['reviews']:
        command = run.data['review_commands'][value['request_id']]
        audit = origin.row(db, p, m.Audit, command['audit_id'])
        result.append({'request_id': value['request_id'], 'receipt_hash': value['receipt_hash'],
                       'audit_id': audit.id, 'audit_hash': digest(audit.data)})
    return result


def record(db, p, run):
    context = _context(db, p, run.id)
    run, task, packet = context['run'], context['task'], context['packet']
    snapshot = run.data['sound_snapshot']['snapshot']
    reviews = run.data['reviews']; day = packet['registration_day']
    return {'key': KIND + ':' + run.id, 'kind': KIND, 'id': run.id, 'name': snapshot['name'],
        'source_version': run.version, 'generation': run.data['installation']['generation'], 'status': run.status,
        'mode': 'business_automatic', 'origin_key': 'sound_original:' + run.sound_input_id,
        'origin_upload_day': None, 'date_basis': 'machine_result_registered',
        'registration_day': day, 'registered_at': packet['registered_at'],
        'processing_request_day': str(run.created_at.astimezone(origin.TZ).date()),
        'processing_completed_day': str(context['result_audit'].created_at.astimezone(origin.TZ).date()),
        'late_processing': str(run.created_at.astimezone(origin.TZ).date()) != day,
        'input_sha256': snapshot['input_sha256'], 'evidence_id': snapshot['evidence_id'],
        'source_time': 'unknown', 'integrity_verified': True, 'integrity_issue': None, 'error': None,
        'result_hash': run.data['result_hash'], 'data_received': True,
        'observation_id': context['observation'].id, 'event_id': context['event'].id, 'memory_id': context['reception']['memory_id'],
        'sound_windows': packet['window_count'], 'sound_candidate_classes': packet['candidate_label_count'],
        'review_state': 'confirmed' if reviews else 'unreviewed',
        'review_hash': digest(reviews), 'review_draft_hash': None,
        'last_review_verdict': reviews[-1]['verdict'] if reviews else None,
        'review_receipt_hashes': [value['receipt_hash'] for value in reviews],
        'task_id': task.id, 'task_version': task.version, 'task_status': task.status, 'task_title': task.title,
        'instance_id': run.data['instance_id'], 'not_authoritative': True,
        'recording_time_verified': False, 'entity_state_updated': False}


def groups(db, workspace_id, day=None):
    """Explicit workspace only. Reverse anchors prevent a lost source becoming an empty day."""
    if workspace_id is None: return {}
    p = SimpleNamespace(workspace_id=workspace_id)
    if day is not None: checked_day(day)
    audit_query=select(m.Audit).where(m.Audit.workspace_id == workspace_id, m.Audit.action == origin.ISSUED_ACTION)
    if day is not None:audit_query=audit_query.where(m.Audit.data['registration_day'].as_string()==day)
    issued = list(db.scalars(audit_query.limit(MAX_RECORDS + 1)))
    need(len(issued) <= MAX_RECORDS)
    prior_query=select(m.AudioDaily).where(m.AudioDaily.workspace_id == workspace_id)
    if day is not None:prior_query=prior_query.where(m.AudioDaily.received_day==day)
    prior = list(db.scalars(prior_query))
    old_ids = {item['id'] for daily in prior for item in daily.data.get('records', []) if item.get('kind') == KIND}
    prior_observation_ids={item['observation_id'] for daily in prior for item in daily.data.get('records', []) if item.get('kind')==KIND}
    old_observations = select(m.Observation.id).where(m.Observation.workspace_id == workspace_id,
        m.Observation.data[origin.PACKET]['run_id'].as_string().in_(old_ids))
    sources = select(m.Capability.id).where(m.Capability.workspace_id == workspace_id,
        m.Capability.namespace == 'platform_native_sound_results')
    observation_query=select(m.Observation).where(m.Observation.workspace_id == workspace_id,
        or_(m.Observation.source_record_id.startswith('EnvironmentSoundRun:'), m.Observation.data.has_key(origin.PACKET),
            m.Observation.data.has_key(origin.ISSUED), m.Observation.id.in_([a.object_id for a in issued]),
            m.Observation.source_id.in_(sources), m.Observation.id.in_(old_observations),m.Observation.id.in_(prior_observation_ids)))
    if day is not None:observation_query=observation_query.where(or_(m.Observation.business_day==day,
        m.Observation.id.in_([a.object_id for a in issued]),m.Observation.id.in_(prior_observation_ids)))
    rows=list(db.scalars(observation_query.limit(MAX_RECORDS+1)))
    need(len(rows) <= MAX_RECORDS and {a.object_id for a in issued} <= {row.id for row in rows})
    result = {}; seen = set()
    for observation in rows:
        context = origin.checked_observation(db, p, observation)
        run = context['run']; need(run.id not in seen); seen.add(run.id)
        reception = receipt_projection(db, p, run)
        need(reception['state'] in {'waiting_data', 'received'})
        if reception['state'] != 'received': continue
        day = checked_day(context['packet']['registration_day'])
        result.setdefault((workspace_id, day), []).append(record(db, p, run))
    need(old_ids <= seen)
    return result


def pending(db, p, day=None):
    """No numeric values, no fake registration day, no writes or automatic retry."""
    if day is not None: checked_day(day)
    from .workflow_sound import checked_saved_run
    rows = list(db.scalars(select(EnvironmentSoundRun).where(EnvironmentSoundRun.workspace_id == p.workspace_id)
        .order_by(EnvironmentSoundRun.created_at, EnvironmentSoundRun.id).limit(MAX_RECORDS + 1)))
    need(len(rows) <= MAX_RECORDS)
    result = []
    for run in rows:
        reception = receipt_projection(db, p, run)
        if reception['state'] == 'received': continue
        registered = None
        try:
            context = checked_saved_run(db, p, run.id)
            observations = origin.observations(db, p, run.id)
            if len(observations) == 1:
                registered = origin.checked_observation(db, p, observations[0])['packet']['registration_day']
        except HTTPException as error:
            if error.status_code == 403: raise
        if day is not None and registered is not None and registered != day: continue
        result.append({'run_id': run.id, 'task_id': run.task_id, 'instance_id': run.data.get('instance_id'),
            'state': reception['state'], 'registration_day': registered, 'recording_time_verified': False,
            'window_count': None, 'candidate_class_count': None, 'integrity_issue': reception['integrity_issue'],
            'date_basis': 'machine_result_registered' if registered else 'registration_day_unknown',
            'not_authoritative': True})
    return result


def _verified_record(saved, current, run):
    """Freeze then-existing reviews, while all original machine values stay exact."""
    need(type(saved) is dict and set(saved)==set(current))
    mutable={'source_version','task_version','task_status','review_state','review_hash','last_review_verdict','review_receipt_hashes'}
    need(all(digest(saved[key])==digest(current[key]) for key in set(current)-mutable))
    need(type(saved['source_version']) is int and 2<=saved['source_version']<=current['source_version']
         and type(saved['task_version']) is int and 1<=saved['task_version']<=current['task_version']
         and type(saved['task_status']) is str)
    hashes=saved['review_receipt_hashes'];need(type(hashes) is list and len(set(hashes))==len(hashes))
    reviews=run.data['reviews'][:len(hashes)]
    need(hashes==[value['receipt_hash'] for value in reviews]
         and saved['source_version']==2+len(reviews) and saved['review_hash']==digest(reviews)
         and saved['review_state']==('confirmed' if reviews else 'unreviewed')
         and saved['last_review_verdict']==(reviews[-1]['verdict'] if reviews else None))
    return reviews


def source_pin(db, p, identity, *, saved_record=None):
    context = _context(db, p, identity)
    run, job = context['run'], context['job']; original = run.data['sound_snapshot']['snapshot']
    audit = context['input_audit']; reception = context['reception']
    current_record=record(db,p,run)
    frozen=current_record if saved_record is None else saved_record
    reviews=_verified_record(frozen,current_record,run)
    selected={value['request_id'] for value in reviews}
    return {'kind': KIND, 'id': run.id, 'source_version': frozen['source_version'], 'record_hash': digest(frozen), 'job_id': job.id,
        'job_key': job.idempotency_key, 'actor_id': job.data['actor_id'], 'job_field': 'task_id',
        'job_value': run.task_id, 'source_link': 'native_sound_task',
        'audit': {'id': audit.id, 'action': audit.action, 'object_id': audit.object_id,
                  'actor_id': audit.actor_id, 'data_hash': digest(audit.data)},
        'evidence': [{'id': original['evidence_id'], 'sha256': original['input_sha256']}],
        'sound_reception': {'observation_id': context['observation'].id, 'event_id': context['event'].id,
            'memory_id': reception['memory_id'], 'receipt_hash': reception['receipt']['receipt_hash'],
            'packet_hash': digest(context['packet']), 'result_hash': run.data['result_hash'],
            'registration_day': context['packet']['registration_day']},
        'sound_reviews': [value for value in _review_refs(db,p,run) if value['request_id'] in selected]}


def validate_pin(db, p, pin, saved_record):
    current=source_pin(db,p,pin['id'],saved_record=saved_record)
    need(digest(current)==digest(pin))
    return current


def detail(db, p, saved):
    context = _context(db, p, saved['id'])
    from .native_sound_provider import legacy_receipt_view
    current = record(db, p, context['run'])
    need(current['input_sha256'] == saved['input_sha256'] and current['result_hash'] == saved['result_hash'])
    return {'record': copy.deepcopy(saved), 'integrity_verified': True, 'integrity_issue': None,
        'current_status': context['run'].status, 'current_version': context['run'].version,
        'changed_since_collection': context['run'].version!=saved['source_version'] or context['task'].version!=saved['task_version'],
        'native_sound_result_v1': legacy_receipt_view(db, p, context['observation'])['native_sound_result_v1'],
        'source_time': 'unknown', 'not_authoritative': True, 'entity_state_updated': False, 'cloud_calls': 0}


def reconcile(db, p, day):
    checked_day(day)
    from .audio_daily import save_collection, checked_daily
    db.execute(text('SELECT pg_advisory_xact_lock(734106093)'))
    current=db.scalar(select(m.AudioDaily).where(m.AudioDaily.workspace_id==p.workspace_id,m.AudioDaily.received_day==day).with_for_update())
    legacy=[]
    if current is not None:
        checked_daily(db,p,current.id)
        legacy=[copy.deepcopy(value) for value in current.data['records'] if value.get('kind')!=KIND]
    sounds=groups(db,p.workspace_id,day).get((p.workspace_id,day),[])
    if not sounds:return current
    return save_collection(db,p.workspace_id,day,legacy+sounds)


def refresh_received(db, p, observation):
    """ACK transaction hook. A damaged older day cannot undo this valid source ACK."""
    if not origin.has_issued(db, p, observation): return None
    from .instance_data_receipts import observation_receipt
    context = origin.checked_observation(db, p, observation)
    observation_receipt(db, p, observation)
    day = context['packet']['registration_day']
    try:
        with db.begin_nested():
            return reconcile(db, p, day)
    except HTTPException as error:
        if error.status_code == 403: raise
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        pass
    from .services import audit
    audit(db, p, 'sound.daily_blocked_v1', observation.id,
          {'registration_day': day, 'run_id': context['run'].id, 'reason': ERROR})
    db.flush()
    return None



def refresh_review(db, p, run):
    """New saved human feedback only; pending sound never becomes a daily value."""
    state=receipt_projection(db,p,run)
    if state['state']!='received':return None
    rows=origin.observations(db,p,run.id);need(len(rows)==1)
    return refresh_received(db,p,rows[0])


def collection_status(db, p, day):
    """Read-only operational projection; a fault never changes an old day receipt."""
    checked_day(day)
    daily=db.scalar(select(m.AudioDaily).where(m.AudioDaily.workspace_id==p.workspace_id,m.AudioDaily.received_day==day))
    if daily is not None:
        from .audio_daily import checked_daily
        try:checked_daily(db,p,daily.id)
        except HTTPException as error:
            if error.status_code==403:raise
            return {'state':'collection_blocked','registration_day':day,'reason':ERROR,'affected_run_id':None,
                'daily_id':daily.id,'daily_version':daily.version}
    latest=db.scalar(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action=='sound.daily_blocked_v1',
        m.Audit.data['registration_day'].as_string()==day).order_by(m.Audit.created_at.desc(),m.Audit.id.desc()).limit(1))
    sound_ids={item['id'] for item in daily.data.get('records',[]) if item.get('kind')==KIND} if daily is not None else set()
    unresolved=latest is not None and latest.data.get('run_id') not in sound_ids
    return {'state':'collection_blocked' if unresolved else 'collected' if sound_ids else 'not_collected',
        'registration_day':day,'reason':ERROR if unresolved else None,
        'affected_run_id':latest.data.get('run_id') if unresolved else None,
        'daily_id':daily.id if daily is not None else None,'daily_version':daily.version if daily is not None else None}
