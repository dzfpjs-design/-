"""Local single-image receipts enter the common observation/data pipeline."""
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,text
from .contracts import Manifest,digest
from .models import Capability,Observation,Event,Memory,Job,Entity,DomainInstance,InstanceRelease
from .services import published,scoped,audit
from .evidence import evidence_bytes

def checked_cap(db,p,identity):
    from .local_specialists import ready_install
    from .specialist_api import capability_manifest
    cap=published(db,p,identity,'specialist_model')
    if cap.data['execution'].get('adapter')!='local_specialist':raise HTTPException(422,'此入口仅接已登记的本地单图检测或分割模型')
    installation=ready_install(db,p,cap.data['execution']['installation_id'])
    if capability_manifest(installation).model_dump()!=cap.data:raise HTTPException(409,'本地模型登记或安装版本已变化，请核对后重新提交')
    return cap

def source_manifest(instance):
    return Manifest(kind='source',namespace='platform_local_files',name='instance_'+instance.id.replace('-',''),
        version='1.0.0',owner='平台数据管理',description='业务工作区手动上传图片（采集时间未核实）',
        allowed_agent_roles=['perception','data'],execution={'adapter':'upload'},
        input_schema={'type':'object'},output_schema={'type':'object'},
        specification={'timezone':'Asia/Shanghai','managed_instance':instance.id,'date_basis':'file_upload_request'},
        data_policy={'cloud_allowed':False,'external_allowed':False,'deployment':'local'})

def source(db,p,instance,manifest=None):
    value=(manifest or source_manifest(instance)).model_dump();fingerprint=digest(value)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'local_upload_source':instance.id})[:16],16)-(1<<63)})
    row=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.namespace==value['namespace'],
        Capability.name==value['name'],Capability.release_version==value['version']))
    if row:
        if row.status!='published' or row.data!=value or row.validated_hash!=fingerprint:raise HTTPException(409,'自动形成的上传来源记录不一致，原记录保留')
    else:
        # A fixed read-only data identity; no endpoint, secret, device or mutable action grant.
        row=Capability(workspace_id=p.workspace_id,namespace=value['namespace'],name=value['name'],release_version=value['version'],
            kind='source',status='published',content_hash=fingerprint,validated_hash=fingerprint,data=value)
        db.add(row);db.flush();audit(db,p,'source.local_upload_derived',row.id,{'instance_id':instance.id})
    return row

def file_inputs(db,p,extractor,ids,attributes):
    checked_cap(db,p,extractor.id)
    if len(ids)!=1 or attributes:raise HTTPException(422,'本地单图观测须明确选择一幅原图，不接收人工填写的提取字段')
    ev,_=evidence_bytes(db,p,ids[0])
    if ev.data.get('modality')!='image':raise HTTPException(422,'本地单图模型只接受PNG或JPEG原图')
    return ev

def verify(db,p,row,extractor_id,*,execution=True,lock=False):
    checked_cap(db,p,extractor_id)
    original=row.data.get('original_request',{})
    if digest(original)!=row.data.get('input_hash'):raise HTTPException(409,'原本地观测请求不一致，请保留原件并核对')
    for key in ['source_id','source_record_id','processing_version','entity_id','business_day']:
        if original.get(key)!=getattr(row,key):raise HTTPException(409,'本地观测来源或对象已变化')
    from .services import timestamp
    if timestamp(original.get('observed_at'))!=row.observed_at or original.get('extractor_id')!=extractor_id:
        raise HTTPException(409,'原采集时间或模型选择已变化')
    if original.get('attributes')!=row.data.get('attributes') or original.get('evidence_ids')!=row.data.get('evidence_ids'):
        raise HTTPException(409,'原字段或文件选择已变化')
    cap=checked_cap(db,p,extractor_id)
    ev=file_inputs(db,p,cap,row.data['evidence_ids'],row.data['attributes'])
    if digest(cap.data)!=row.data.get('local_capability_hash') or ev.sha256!=row.data.get('local_original_sha256'):
        raise HTTPException(409,'原模型登记或图片摘要已变化')
    src=published(db,p,row.source_id,'source')
    if digest(src.data)!=row.data.get('local_source_hash'):raise HTTPException(409,'原上传来源已变化')
    context=row.data.get('local_instance')
    managed=src.data.get('specification',{}).get('managed_instance')
    if managed and (not isinstance(context,dict) or context.get('id')!=managed or not context.get('release_id')):
        raise HTTPException(409,'原实例处理范围缺失或不一致，不能按通用输入重新执行；原件保留')
    if context is not None and not isinstance(context,dict):
        raise HTTPException(409,'原实例处理范围无法核对，原件保留')
    if context:
        if execution:
            from .instance_api import active
            instance,release=active(db,p,context['id'],True) if lock else active(db,p,context['id'])
            if release.id!=context['release_id']:
                raise HTTPException(409,'实例版本已变化；旧处理授权不迁移，原处理记录保留')
        else:
            instance=scoped(db,DomainInstance,context['id'],p)
            release=scoped(db,InstanceRelease,context['release_id'],p)
        entity=scoped(db,Entity,row.entity_id,p)
        from .entity_release_compat import require_entity
        proof=require_entity(db,p,instance,release,entity)
        has_proof=any(key in context for key in ('entity_source_package_id','entity_definition_hash'))
        if ((entity.schema_capability_id!=release.package_id or has_proof)
            and (context.get('entity_source_package_id')!=entity.schema_capability_id
                or context.get('entity_definition_hash')!=digest(proof))):
            raise HTTPException(409,'实例或对象版本已变化；原处理记录保留')
    return ev

def reconcile(db,p,source_id,day):
    source=scoped(db,Capability,source_id,p)
    rows=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.source_id==source_id,
        Observation.business_day==day,Observation.data['candidate_only'].as_boolean()==True).order_by(Observation.id)))
    if not rows:return
    value={'schema':'local-file-daily-v1','source_id':source.id,'instance_id':source.data['specification'].get('managed_instance'),
        'business_day':day,'date_basis':'file_upload_request','source_clock_verified':False,'not_authoritative':True,
        'processed_ids':[r.id for r in rows if r.status=='processed'],'failed_ids':[r.id for r in rows if r.status=='failed'],
        'pending_ids':[r.id for r in rows if r.status=='received'],'reviewed_ids':[r.id for r in rows if
            r.data.get('human_verification',{}).get('decision')=='confirmed' and
            r.data['human_verification'].get('input_hash')==digest({'extracted':r.data.get('extracted',{}),
                'evidence_ids':r.data.get('evidence_ids',[]),'invocation_id':r.data.get('invocation_id')})],
        'status':'incomplete' if any(r.status!='processed' for r in rows) else 'collected_with_limits'}
    from .services import enqueue_event
    enqueue_event(db,p,'perception.daily','local-daily:'+source.id+':'+day+':'+digest(value),value)

def options(db,p):
    result=[]
    for cap in db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.status=='published',Capability.kind=='specialist_model')):
        if cap.data.get('execution',{}).get('adapter')!='local_specialist':continue
        try:checked_cap(db,p,cap.id)
        except (HTTPException,KeyError):continue
        result.append({'id':cap.id,'name':cap.data['description'].split('。',1)[0],'version':cap.release_version,'deployment':'local','candidate_only':True})
    return result

def view(db,p,row):
    from .models import Invocation
    result={'id':row.id,'version':row.version,'status':row.status,'entity_id':row.entity_id,'received_day':row.business_day,
        'created_at':row.created_at.isoformat(),'source_clock':row.observed_at.isoformat() if row.observed_at else None,
        'candidate_only':True,'request_id':row.source_record_id,'extractor_id':row.data['extractor_id'],'evidence_id':row.data['evidence_ids'][0],
        'source_sha256':row.data['local_original_sha256'],'date_basis':row.data['date_basis'],
        'review':row.data.get('human_verification'),'error':row.data.get('processing_error'),
        'output':None,'integrity_error':None,'data_received':False,
        'retry_available':False,'retry_reason':'当前处理不需要重试','retry_attempts':0,'retry_limit':3,
        'retry_target_release_id':row.data.get('local_instance',{}).get('release_id') if isinstance(row.data.get('local_instance'),dict) else None}
    retries=row.data.get('retry_requests',[])
    journal_ok=not retries or digest(retries)==row.data.get('retry_requests_hash')
    result.update(retry_requests=retries if journal_ok else [],retry_receipts_verified=journal_ok)
    if row.status=='failed':
        from .observation_inputs import view as input_view
        from .observation_integrity import CHECK_ERRORS
        try:
            qualification=input_view(db,p,row)
            result.update(retry_available=qualification['retry_available'],retry_reason=qualification['retry_reason'],
                retry_attempts=qualification['attempts'])
        except CHECK_ERRORS:
            result['retry_reason']='原输入或处理授权需核对，暂不能重新排队；原件保留'
    try:
        original=row.data['original_request']
        from .services import timestamp
        if any(original[k]!=getattr(row,k) for k in ['source_id','source_record_id','processing_version','entity_id','business_day']) or timestamp(original.get('observed_at'))!=row.observed_at:
            raise HTTPException(409,'原对象、来源或时间已变化')
        if any(original.get(k)!=row.data.get(k) for k in ['attributes','evidence_ids','extractor_id']):
            raise HTTPException(409,'原字段、文件或模型选择已变化')
        src=scoped(db,Capability,row.source_id,p)
        if digest(src.data)!=row.data['local_source_hash'] or src.data['specification'].get('managed_instance')!=row.data.get('local_instance',{}).get('id'):
            raise HTTPException(409,'原上传来源归属已变化')
        ev,_=evidence_bytes(db,p,result['evidence_id'])
        if ev.sha256!=result['source_sha256'] or digest(row.data['original_request'])!=row.data['input_hash']:
            raise HTTPException(409,'原件或原提交条件不一致')
        if row.status=='processed':
            call=scoped(db,Invocation,row.data['invocation_id'],p)
            inputs={'attributes':row.data['attributes'],'evidence_ids':row.data['evidence_ids']}
            if call.status!='succeeded' or call.capability_id!=row.data['extractor_id'] or call.data.get('input_hash')!=digest(inputs) or call.data.get('output')!=row.data['extracted']:
                raise HTTPException(409,'原本地处理回执不一致')
            output=row.data['extracted'];native=output.get('segmentation') or output.get('detection')
            if not native or native['image_sha256']!=ev.sha256 or native.get('cloud_calls')!=0:raise HTTPException(409,'本地模型原件或外发回执不一致')
            if output.get('segmentation'):
                from .segmentation import mask_bytes
                for mask in native['masks']:mask_bytes(db,p,native,mask['mask_evidence_id'],ev.id)
            result['output']=output
        event=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.idempotency_key=='data:'+row.id))
        result['data_received']=bool(event and event.status=='received')
    except (HTTPException,KeyError,ValueError,OSError):result['integrity_error']='原件或处理回执需恢复，暂不显示候选值；核查说明仍保留。'
    return result

def schedule(workspace_id=None):
    """Recover only local file receipts; never drain unrelated workspaces."""
    from .db import engine
    from .security import Principal
    from .worker import deliver_events
    from sqlalchemy.orm import Session
    with Session(engine()) as db:
        query=select(Observation.workspace_id,Observation.source_id,Observation.business_day).where(
            Observation.data['candidate_only'].as_boolean()==True,
            Observation.data['native_instance_id'].as_string().is_(None))
        if workspace_id is not None:query=query.where(Observation.workspace_id==workspace_id)
        groups=db.execute(query.distinct().limit(100)).all()
    workspaces=set()
    for workspace,source_id,day in groups:
        with Session(engine()) as db:
            p=Principal('local-data-receiver',workspace,'viewer','local-data-receiver')
            reconcile(db,p,source_id,day);db.commit();workspaces.add(workspace)
    for workspace in workspaces:deliver_events(workspace_id=workspace)
