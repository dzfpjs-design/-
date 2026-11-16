"""Generated domain objects and explicitly selected business destinations."""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from .models import DomainInstance,InstanceRelease,User,Membership,Task
from .services import scoped
from .instance_compiler import material_ids_for_flow,verify_material_snapshots


def object_display(entity,package):
    catalog=package.data.get('specification',{}).get('object_catalog',[])
    kind=next((x for x in catalog if x['key']==entity.entity_type),None)
    if kind:
        label=next((entity.data.get(f['key']) for f in kind['fields'] if f['kind']=='文本' and f['required'] and entity.data.get(f['key'])),None)
        return {'label':str(label or '未命名对象')[:200],'type':kind['name']}
    return {'label':str(entity.data.get('name') or entity.data.get('title') or entity.external_id)[:200],'type':entity.entity_type}


def generated_release(db,p,entity):
    rows=list(db.scalars(select(InstanceRelease).where(InstanceRelease.workspace_id==p.workspace_id,
        InstanceRelease.package_id==entity.schema_capability_id).limit(2)))
    if len(rows)>1:raise HTTPException(409,'对象来源版本存在歧义，不能猜测接收实例')
    return rows[0] if rows else None


def current_context(db,p,entity,release_id=None):
    """An object definition match, never permission to reuse a device grant."""
    if entity.workspace_id!=p.workspace_id:raise HTTPException(404,'对象不属于当前工作空间')
    source=generated_release(db,p,entity)
    if source is None:
        if release_id:raise HTTPException(422,'此对象没有对应的初始化实例版本')
        return None
    instance=scoped(db,DomainInstance,source.instance_id,p)
    if instance.status!='partially_active' or not instance.active_release_id:
        raise HTTPException(409,'实例尚未启用或已暂停，原记录保留')
    if release_id is not None and release_id!=instance.active_release_id:
        raise HTTPException(409,'原业务目的地版本已改变，请明确核对新的关联；旧授权不迁移')
    release=scoped(db,InstanceRelease,instance.active_release_id,p)
    from .entity_release_compat import require_entity
    proof=require_entity(db,p,instance,release,entity)
    return instance,release,proof


def require_destination_version(release,entities,expected_release_id):
    # An untagged old request must not acquire a new release after a lost reply.
    if expected_release_id is None:
        if any(row.schema_capability_id!=release.package_id for row in entities):
            raise HTTPException(409,'请先核对当前实例版本，再以新请求明确提交；原请求和记录保留')
    elif expected_release_id!=release.id:
        raise HTTPException(409,'请求固定的实例版本已改变，原请求保留，请重新核对')


def local_observation_context(db,p,instance_id,entity,expected=None):
    # Hold the actual destination row until the immutable request is saved.
    instance=scoped(db,DomainInstance,instance_id,p,True)
    if expected is not None and (not isinstance(expected,dict) or set(expected)!={'id','release_id'}
            or expected['id']!=instance.id):
        raise HTTPException(409,'原上传目的地无法核对，请保留原请求')
    context=current_context(db,p,entity,release_id=expected['release_id'] if expected else None)
    if context is None or context[0].id!=instance.id:raise HTTPException(409,'对象不属于上传来源的实例')
    _,release,proof=context
    require_destination_version(release,[entity],expected['release_id'] if expected else None)
    result={'id':instance.id,'release_id':release.id}
    if entity.schema_capability_id!=release.package_id:
        from .contracts import digest
        result.update(entity_source_package_id=entity.schema_capability_id,entity_definition_hash=digest(proof))
    return result


def target_options(db,p,entity):
    source=generated_release(db,p,entity)
    if source is None:return None
    instance=scoped(db,DomainInstance,source.instance_id,p);release=source;current=False;issue=None
    try:
        instance,release,_=current_context(db,p,entity);current=True
    except HTTPException as error:issue=str(error.detail)
    return {'instance_id':instance.id,'instance_name':instance.data['name'],'instance_release_id':release.id,
        'instance_release_number':release.release_number,'current':current,'reason':issue,
        'businesses':[{'id':b['id'],'name':b['name'],'enabled':current and b['available'] and b['id'] in instance.data.get('enabled_business_ids',[])}
            for b in release.data['snapshot']['businesses']]}


def resolve_target(db,p,entity,release_id,business_id):
    context=current_context(db,p,entity,release_id)
    if context is None:
        if release_id or business_id:raise HTTPException(422,'此对象没有对应的初始化实例版本')
        return None
    instance,release,_=context
    if not release_id or not business_id:raise HTTPException(422,'请明确选择当前实例版本中接收线索的业务')
    business=next((x for x in release.data['snapshot']['businesses'] if x['id']==business_id),None)
    if not business or not business['available'] or business_id not in instance.data.get('enabled_business_ids',[]):
        raise HTTPException(409,'接收线索的业务尚未满足执行条件或未启用')
    for step in business['flow']['steps']:
        if step['agent']!='人工确认':
            from .workflow_abilities import pin
            if pin(db,p,step,release.data['snapshot']['automation']['policies'][business['name']])!=step['execution']:raise HTTPException(409,'业务的执行能力版本已变化，请核对配置')
            continue
        user_id=release.data['snapshot']['roles'][step['role']]['user_id']
        if not db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(User.id==user_id,User.active.is_(True),
            Membership.workspace_id==p.workspace_id,Membership.role.in_(['admin','operator']))):
            raise HTTPException(409,'业务负责人已停用或失去权限，请重新核对配置')
    ids=material_ids_for_flow(business['flow'])|set(release.data['snapshot'].get('relation_material_ids',[]))
    sources=[s for s in release.data['snapshot']['materials'] if s['id'] in ids]
    verify_material_snapshots(db,p,sources)
    return {'instance_id':instance.id,'instance_release_id':release.id,'instance_release_number':release.release_number,
        'business_id':business_id,'business_name':business['name']}


def object_snapshot(entity,release):
    kind=next(x for x in release.data['snapshot']['object_types'] if x['key']==entity.entity_type)
    return {'id':entity.id,'version':entity.version,'type_name':kind['name'],'attributes':copy.deepcopy(entity.data),'fields':copy.deepcopy(kind['fields'])}


def pending_review(db,p,association,run,frame,alert):
    from .models import Entity
    entity=scoped(db,Entity,association['entity_id'],p)
    target=resolve_target(db,p,entity,association['instance_release_id'],association['business_id'])
    release=scoped(db,InstanceRelease,target['instance_release_id'],p)
    business=next(b for b in release.data['snapshot']['businesses'] if b['id']==target['business_id'])
    ids=material_ids_for_flow(business['flow'])
    first=business['flow']['steps'][0]
    track=next((t for t in frame.data['tracks'] if t['track_ref']==alert.data['track_ref'] and t['label']==alert.data['label']),None)
    if not track:raise ValueError('视频线索与保存画面的轨迹不一致')
    media={'run_id':run.id,'frame_id':frame.id,'video_alert_id':alert.id,'original_evidence_id':run.evidence_id,
        'frame_evidence_id':frame.evidence_id,'original_sha256':run.data['input_sha256'],'frame_sha256':frame.data['frame_sha256'],
        'label':alert.data['label'],'label_id':track['label_id'],'track_ref':alert.data['track_ref'],
        'from_seconds':alert.data['from_seconds'],'to_seconds':alert.data['to_seconds'],'source_clock_utc':'unknown',
        'association':'observed_scene','not_physical_identity':True,'device_action_authorized':False}
    object_binding={}
    if entity.schema_capability_id!=release.package_id:
        from .entity_release_compat import freeze_task_objects
        from .contracts import digest
        proof=freeze_task_objects(db,p,scoped(db,DomainInstance,target['instance_id'],p),release,[entity])
        object_binding={'object_definition_bindings':proof,'object_definition_bindings_hash':digest(proof)}
    task=Task(workspace_id=p.workspace_id,package_id=release.package_id,
        owner_id=release.data['snapshot']['roles'][first['role']]['user_id'] if first['agent']=='人工确认' else p.user_id,
        title=target['business_name']+'：核查'+association['entity_label']+'的视频线索',status='workflow_pending',data={**target,**object_binding,
            'trigger':'video.presence','event_id':alert.data['event_id'],'video_alert_id':alert.id,'run_id':run.id,
            'entity_ids':[entity.id],'object_snapshots':[object_snapshot(entity,release)],'domain_binding':association,
            'material_snapshots':copy.deepcopy([s for s in release.data['snapshot']['materials'] if s['id'] in ids]),
            'evidence_ids':[frame.evidence_id,run.evidence_id],'perception_event':media,
            'workflow_runtime':{'current_step_id':first['id'],'drafts':{},'receipts':[],'max_steps':100},
            'execution_mode':business['execution_mode'],'goal_spec':None,'not_current_state_update':True,
            'reason':'感知按明确关联送达候选线索；等待业务核查，不自动认定异常或执行设备动作'})
    db.add(task);db.flush();return task
