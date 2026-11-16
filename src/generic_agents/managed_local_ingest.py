"""An explicit instance upload path; legacy HTTP ingestion remains frozen."""
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,text
from .contracts import digest
from .models import Observation,Entity,Job
from .services import require,published,scoped,timestamp,audit


def ingest(body,p,db,*,expected_local_context):
    require(p,'admin','operator')
    if (body.startup_mode!='manual' or body.acquisition_id or body.sensor_asset_id or body.sensor_modality
            or not body.evidence_ids):
        raise HTTPException(422,'此入口只保存已选实例的本地上传候选，不控制设备或关闭补采')
    source=published(db,p,body.source_id,'source')
    managed=source.data['specification'].get('managed_instance')
    if (not managed or source.data['execution'].get('adapter')!='upload'
            or source.data['data_policy'].get('cloud_allowed') is not False
            or source.data['data_policy'].get('external_allowed') is not False):
        raise HTTPException(409,'请核对实例本地上传来源，不读取外部设备或发送云端')
    entity=scoped(db,Entity,body.entity_id,p)
    from .instance_domain import local_observation_context
    context=local_observation_context(db,p,managed,entity,expected_local_context)
    from .local_observations import checked_cap,file_inputs
    extractor=checked_cap(db,p,body.extractor_id)
    if 'perception' not in extractor.data['allowed_agent_roles']:
        raise HTTPException(403,'提取器未授权感知智能体')
    original=file_inputs(db,p,extractor,body.evidence_ids,body.attributes)
    observed=timestamp(body.observed_at)
    day=str((observed or original.created_at).astimezone(ZoneInfo(source.data['specification'].get('timezone','UTC'))).date())
    if body.business_day!=day:raise HTTPException(422,'业务日期与来源时间或原件上传日期不一致')
    content=body.model_dump();signature=digest(content)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({
        'managed_local':p.workspace_id,'source':source.id,'record':body.source_record_id,
        'processing':body.processing_version})[:16],16)-(1<<63)})
    existing=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,
        Observation.source_id==source.id,Observation.source_record_id==body.source_record_id,
        Observation.processing_version==body.processing_version))
    from .api import as_dict
    if existing:
        if existing.data.get('input_hash')!=signature or existing.data.get('local_instance')!=context:
            raise HTTPException(409,'原图片请求已有不同内容或目的地，原记录保留，请先核对')
        return as_dict(existing)
    value=body.model_dump(exclude={'source_id','source_record_id','processing_version','entity_id','observed_at','business_day'})
    value.update(candidate_only=True,local_capability_hash=digest(extractor.data),local_source_hash=digest(source.data),
        local_original_sha256=original.sha256,date_basis='reported_capture_time_unverified' if observed else 'file_upload_request',
        local_instance=context,input_hash=signature,submitted_by=p.user_id,original_request=content)
    row=Observation(workspace_id=p.workspace_id,source_id=source.id,source_record_id=body.source_record_id,
        processing_version=body.processing_version,entity_id=entity.id,observed_at=observed,business_day=day,data=value)
    db.add(row);db.flush()
    db.add(Job(workspace_id=p.workspace_id,kind='process_local_observation',idempotency_key='extract:'+row.id,
        data={'observation_id':row.id,'actor_id':p.user_id,'extractor_id':extractor.id}))
    from .state_refresh import source_changed
    source_changed(db,p,row,'新登记已保存，等待处理')
    audit(db,p,'observation.ingest',row.id)
    db.commit();return as_dict(row)
