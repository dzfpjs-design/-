"""Closed actual policy intake: new INSERT only, no ordinary-winner adoption.

The caller holds the Grant/source-key locks and proves policy authority.
This module registers neither an approval nor a model call and never commits.
China is the default for this new flow; explicit source timezone has priority.
"""
from copy import deepcopy
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import Observation,Job,Entity
from .security import require
from .services import audit,published,scoped,validate,timestamp


def registration_clock(observed_at,business_day,specification):
    name=specification.get('timezone','Asia/Shanghai')
    try:
        if type(name) is not str or not name:raise ValueError()
        zone=ZoneInfo(name)
    except (ValueError,TypeError,ZoneInfoNotFoundError):
        raise HTTPException(409,'来源的登记时区无效，请先核对来源配置') from None
    actual=timestamp(observed_at)
    if actual is not None and str(actual.astimezone(zone).date())!=business_day:
        raise HTTPException(422,'资料日期与已填写的采集时间不一致；原人稿保留')
    return actual


def register(db,p,body,reported_contract):
    require(p,'admin','operator')
    if body.startup_mode!='manual' or body.evidence_ids or body.acquisition_id or body.sensor_asset_id or body.sensor_modality:
        raise HTTPException(422,'有限数值策略只接新人工登记，不上传文件、控制设备或关闭补采')
    prior=db.scalar(select(Observation.id).where(Observation.workspace_id==p.workspace_id,
        Observation.source_id==body.source_id,Observation.source_record_id==body.source_record_id).limit(1))
    if prior is not None:raise HTTPException(409,'原来源键已经登记，不能换处理版本取得新的策略资格')
    source=published(db,p,body.source_id,'source');extractor=published(db,p,body.extractor_id)
    scoped(db,Entity,body.entity_id,p)
    for cap in (source,extractor):
        if cap.data['execution'].get('side_effect') or cap.data.get('data_policy',{}).get('cloud_allowed') is not False:
            raise HTTPException(409,'本次登记只允许已批准的本地只读数值能力')
    execution=extractor.data['execution'];source_execution=source.data['execution']
    if execution.get('adapter')!='builtin' or execution.get('handler')!='numeric_rules' or 'perception' not in extractor.data.get('allowed_agent_roles',[]):
        raise HTTPException(409,'此登记只支持已批准的本地数值规则')
    if (source.namespace=='platform_mqtt' or source.data.get('specification',{}).get('mqtt_binding_id')
        or source_execution.get('adapter') not in {'upload','builtin'}
        or source_execution.get('adapter')=='builtin' and source_execution.get('handler')!='numeric_rules'):
        raise HTTPException(409,'请选择原批准的人工登记来源，不能借此读取外部设备')
    validate(extractor.data['input_schema'],{'attributes':body.attributes})
    actual=registration_clock(body.observed_at,body.business_day,source.data['specification'])
    original=body.model_dump(exclude={'evidence_ids'})
    value=body.model_dump(exclude={'source_id','source_record_id','processing_version','entity_id','observed_at','business_day'})
    row=Observation(workspace_id=p.workspace_id,source_id=body.source_id,
        source_record_id=body.source_record_id,processing_version=body.processing_version,
        entity_id=body.entity_id,observed_at=actual,business_day=body.business_day,
        data={**value,'original_request':original,'input_hash':digest(original),'submitted_by':p.user_id,
            'reported_rule_contract':deepcopy(reported_contract),'source_time_verified':False,
            'date_basis':'operator_declared_capture_time' if actual is not None else 'operator_declared_capture_time_unknown'})
    db.add(row);db.flush()
    job=Job(workspace_id=p.workspace_id,kind='process_reported_observation',idempotency_key='extract:'+row.id,
        data={'observation_id':row.id,'actor_id':p.user_id,'extractor_id':body.extractor_id})
    db.add(job);db.flush()
    from .state_refresh import source_changed
    source_changed(db,p,row,'已保存新策略输入；采集时间由人工填写，未知时保持为空')
    audit(db,p,'observation.ingest',row.id)
    return row,job
