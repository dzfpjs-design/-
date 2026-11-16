"""Human-confirmed scene association. No automatic entity creation or fact mutation."""
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select
from .contracts import Contract
from .setup_api import P, DB
from .models import Entity, Capability, VideoDomainBinding, Observation, Task, VideoDaily
from .services import scoped, require, published, audit, check_version
from .specialist_api import Version
from .media_api import view
from .media import validated_channel, channel_manifest
from .instance_domain import object_display,target_options,resolve_target

router = APIRouter(prefix='/api/media')


@router.get('/daily')
def daily(p: P, db: DB):
    return [{'id':x.id,'version':x.version,'created_at':x.created_at.isoformat(),**x.data, 'source_key': x.source_key, 'received_day': x.business_day} for x in db.scalars(
        select(VideoDaily).where(VideoDaily.workspace_id == p.workspace_id).order_by(VideoDaily.business_day.desc(), VideoDaily.created_at.desc()).limit(100))]


@router.post('/daily/reconcile')
def refresh_daily(p: P, db: DB):
    require(p, 'admin', 'operator')
    from .video_daily import reconcile
    reconcile(db, p.workspace_id); db.commit(); return daily(p, db)


@router.get('/domain-options')
def options(p: P, db: DB):
    result = []
    for entity in db.scalars(select(Entity).where(Entity.workspace_id == p.workspace_id).order_by(Entity.created_at.desc()).limit(100)):
        package = db.get(Capability, entity.schema_capability_id)
        if not package or package.status != 'published': continue
        result.append({'id':entity.id,'version':entity.version,**object_display(entity,package),'instance':target_options(db,p,entity)})
    return result


@router.get('/domain-bindings')
def bindings(p: P, db: DB):
    return [{**view(x), 'entity_id': x.entity_id, 'channel_id': x.channel_id} for x in db.scalars(
        select(VideoDomainBinding).where(VideoDomainBinding.workspace_id == p.workspace_id))]


class Binding(Contract):
    version: int | None = Field(default=None, ge=1)
    channel_version: int = Field(ge=1)
    entity_id: str
    entity_version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=500)
    confirm_scene_association: Literal[True]
    instance_release_id:str|None=None
    business_id:str|None=None


@router.put('/channels/{identity}/domain-binding')
def associate(identity: str, body: Binding, p: P, db: DB):
    require(p, 'admin', 'operator')
    from .media_monitor import node_lock
    node_lock(db)
    channel, gateway = validated_channel(db, p, identity); check_version(channel, body.channel_version)
    entity = scoped(db, Entity, body.entity_id, p); check_version(entity, body.entity_version)
    package = published(db, p, entity.schema_capability_id, 'domain_package')
    if not channel.data.get('capability_id'): raise HTTPException(409, '请先登记当前版本的视频来源能力')
    source = published(db, p, channel.data['capability_id'], 'source')
    if source.data != channel_manifest(channel, gateway).model_dump(mode='json'):
        raise HTTPException(409, '视频来源能力版本已变化，请先保存通道资源关联')
    target=resolve_target(db,p,entity,body.instance_release_id,body.business_id)
    row = db.scalar(select(VideoDomainBinding).where(VideoDomainBinding.workspace_id == p.workspace_id,
        VideoDomainBinding.channel_id == identity).with_for_update())
    if row:
        if body.version is None: raise HTTPException(409, '关联已由另一操作保存，请重新核对')
        check_version(row, body.version)
        history = [*row.data.get('history', []), {'entity_id': row.entity_id, 'data': {k: v for k, v in row.data.items() if k != 'history'}}]
        generation = row.data['generation'] + 1; row.entity_id = entity.id; row.actor_id = p.user_id; row.status = 'active'; row.version += 1
    else:
        if body.version is not None: raise HTTPException(409, '原关联不存在，请重新核对')
        row = VideoDomainBinding(workspace_id=p.workspace_id, channel_id=identity, entity_id=entity.id, actor_id=p.user_id)
        db.add(row); history = []; generation = 1
    row.data = {'generation': generation, 'entity_version': entity.version, 'package_id': package.id,
        'package_hash': package.content_hash, 'entity_label':object_display(entity,package)['label'],
        'source_id': source.id, 'channel_generation': channel.data['generation'], 'reason': body.reason,
        'association': 'observed_scene', 'not_physical_identity': True, 'history': history,**(target or {})}
    db.flush(); audit(db, p, 'video.domain.associated', row.id, {'entity_id': entity.id, 'generation': generation})
    db.commit(); return {**view(row), 'entity_id': entity.id, 'channel_id': identity}


@router.post('/domain-bindings/{identity}/disable')
def disable(identity: str, body: Version, p: P, db: DB):
    require(p, 'admin', 'operator'); row = scoped(db, VideoDomainBinding, identity, p, True); check_version(row, body.version)
    row.status = 'disabled'; row.version += 1; row.data = {**row.data, 'generation': row.data['generation'] + 1}
    audit(db, p, 'video.domain.disabled', row.id); db.commit(); return view(row)


@router.get('/domain-state/{identity}')
def state(identity: str, p: P, db: DB):
    entity = scoped(db, Entity, identity, p)
    observations = db.scalars(select(Observation).where(Observation.workspace_id == p.workspace_id,
        Observation.entity_id == entity.id, Observation.status == 'extracted_unclocked').order_by(Observation.created_at.desc()).limit(20)).all()
    tasks = db.scalars(select(Task).where(Task.workspace_id == p.workspace_id, Task.data['trigger'].as_string() == 'video.presence',
        Task.data['domain_binding']['entity_id'].as_string() == entity.id).order_by(Task.created_at.desc()).limit(20)).all()
    return {'entity_id':entity.id,'label':object_display(entity,scoped(db,Capability,entity.schema_capability_id,p))['label'],
        'source_clock_utc': 'unknown', 'not_authoritative': True,
        'observations': [{'id': o.id, 'status': o.status, 'run_id': o.data['run_id'], 'frame_id': o.data['frame_id'],
            'video_seconds': o.data['video_seconds'], 'counts': o.data['extracted']['candidate_state']['label_counts'],
            'evidence_ids': o.data['evidence_ids']} for o in observations],
        'tasks': [{'id': t.id, 'title': t.title, 'status': t.status, 'run_id': t.data['run_id'],
            'video_alert_id': t.data['video_alert_id'],'reason':t.data['reason'],'instance_id':t.data.get('instance_id'),'business_name':t.data.get('business_name')} for t in tasks]}
