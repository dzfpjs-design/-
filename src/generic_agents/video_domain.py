"""Explicit scene association, candidate observations and business review drafts."""
from sqlalchemy import select
from fastapi import HTTPException
from .models import VideoDomainBinding, Entity, Membership, User, Observation, VideoAlert, Task
from .services import scoped, published
from .contracts import digest


def snapshot(db, p, channel):
    row = db.scalar(select(VideoDomainBinding).where(VideoDomainBinding.workspace_id == p.workspace_id,
        VideoDomainBinding.channel_id == channel.id, VideoDomainBinding.status == 'active'))
    if not row: return None
    return {'binding_id': row.id, 'binding_generation': row.data['generation'], 'entity_id': row.entity_id,
        'entity_version': row.data['entity_version'], 'source_id': row.data['source_id'],
        'package_id': row.data['package_id'], 'package_hash': row.data['package_hash'],
        'entity_label': row.data['entity_label'], 'association': 'observed_scene',
        'not_physical_identity':True,'channel_generation':row.data['channel_generation'],
        **{k:row.data[k] for k in ['instance_id','instance_release_id','instance_release_number','business_id','business_name'] if k in row.data}}


def admitted(db, p, frame):
    context = frame.data.get('source_context') or {}; association = context.get('domain_binding')
    if not association: return None, 'not_configured'
    row = scoped(db, VideoDomainBinding, association['binding_id'], p, True)
    entity = scoped(db, Entity, association['entity_id'], p)
    allowed = db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == row.actor_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator'])))
    if (row.status != 'active' or row.data['generation'] != association['binding_generation']
        or row.channel_id != context['channel_id'] or row.entity_id != entity.id
        or entity.version != association['entity_version'] or row.data['channel_generation'] != context['channel_generation']
        or not allowed): return None, 'blocked_binding_changed'
    try:
        source = published(db, p, association['source_id'], 'source')
        package = published(db, p, association['package_id'], 'domain_package')
        from .media import validated_channel, channel_manifest
        channel, gateway = validated_channel(db, p, context['channel_id'])
        if source.data != channel_manifest(channel, gateway).model_dump(mode='json') or package.content_hash != association['package_hash']:
            return None, 'blocked_source_or_package_changed'
    except HTTPException: return None, 'blocked_source_or_package_changed'
    if row.data.get('instance_id'):
        from .instance_domain import resolve_target
        try:
            target=resolve_target(db,p,entity,association.get('instance_release_id'),association.get('business_id'))
            if any(association.get(k)!=v or row.data.get(k)!=v for k,v in target.items()):return None,'blocked_instance_destination_changed'
        except HTTPException:return None,'blocked_instance_destination_changed'
    elif association.get('instance_id'):return None,'blocked_instance_destination_changed'
    return association,'candidate_unclocked'


def observation(db, p, run, frame):
    association, status = admitted(db, p, frame)
    if not association: return {'domain_delivery': status}
    key = 'video-frame:' + frame.id
    release = 'vid-' + run.data['pipeline_hash'][:24]
    row = db.scalar(select(Observation).where(Observation.workspace_id == p.workspace_id,
        Observation.source_id == association['source_id'], Observation.source_record_id == key, Observation.processing_version == release))
    if not row:
        context = frame.data['source_context']
        counts = {}
        for item in frame.data['tracks']:
            label = str(item['label_id']); counts[label] = counts.get(label, 0) + 1
        row = Observation(workspace_id=p.workspace_id, source_id=association['source_id'], source_record_id=key,
            processing_version=release, entity_id=association['entity_id'], observed_at=None, business_day=None,
            status='extracted_unclocked', data={'submitted_by': p.user_id, 'run_id': run.id, 'frame_id': frame.id,
                'domain_binding': association, 'source_context': context, 'evidence_ids': [frame.evidence_id, run.evidence_id],
                'source_clock_utc': 'unknown', 'received_interval_unix': [context['received_start_unix'], context['received_end_unix']],
                'video_seconds': frame.data['seconds'], 'not_authoritative': True, 'deployment': 'local',
                'extracted': {'attributes': {}, 'anomalies': [], 'method': 'local_rtdetr_bytetrack',
                    'candidate_state': {'tracks': frame.data['tracks'], 'label_counts': counts},
                    'uncertainty': {'source_clock': 'unknown', 'identity': 'scene_association_only', 'domain_quality': 'not_evaluated'}},
                'input_hash': digest({'frame': frame.data['receipt_hash'], 'binding': association})})
        db.add(row); db.flush()
    return {'domain_delivery': status, 'observation_id': row.id, 'entity_id': association['entity_id']}


def business_draft(db, p, run, frame, alert):
    delivery = observation(db, p, run, frame)
    if not delivery.get('observation_id'): return delivery
    if alert.data.get('task_id'): return {**delivery, 'task_id': alert.data['task_id']}
    association = frame.data['source_context']['domain_binding']
    if association.get('instance_id'):
        from .instance_domain import pending_review
        task=pending_review(db,p,association,run,frame,alert)
        task.data={**task.data,'observation_id':delivery['observation_id']}
        return {**delivery,'task_id':task.id}
    task = Task(workspace_id=p.workspace_id, title='核查视频线索：' + association['entity_label'],
        package_id=association['package_id'], owner_id=p.user_id, status='draft', data={
            'trigger': 'video.presence', 'event_id': alert.data['event_id'], 'video_alert_id': alert.id,
            'run_id': run.id, 'entity_ids': [association['entity_id']], 'fields': [], 'domain_binding': association,
            'observation_id': delivery['observation_id'], 'evidence_ids': [frame.evidence_id, run.evidence_id],
            'evidence': {**delivery, 'candidate': {key: alert.data.get(key) for key in ['from_seconds', 'to_seconds', 'track_ref']},
                'not_authoritative': True, 'source_clock_utc': 'unknown', 'device_action_authorized': False},
            'reason': '感知已报告待核线索，业务智能体收到任务草稿；未认定业务异常，未自动规划或执行设备动作'})
    db.add(task); db.flush(); return {**delivery, 'task_id': task.id}
