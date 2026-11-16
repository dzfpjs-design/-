"""Authorized sensor inventory, durable source selection and bounded alternatives."""
import copy
from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from pydantic import Field,FiniteFloat,StrictInt,model_validator,ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .contracts import Contract,digest
from .models import (SensorAsset,SensorReservation,SensorAssetEvent,ObservationPlan,Acquisition,
    MonitorPolicy,Entity,Job,Event,DataGap,Task,now)
from .services import scoped,published,audit,check_version
from .security import require,Principal
from .temporal import instant

ACTIONS=Literal['read_existing','observe','capture_image','capture_video','pan_tilt','fly_route']
MODALITIES=Literal['numeric','image','thermal','video','multispectral','point_cloud','text']

class SourceObservation(Contract):
    fields:list[str]=Field(min_length=1,max_length=30)
    modalities:list[MODALITIES]=Field(min_length=1,max_length=7)
    actions:list[ACTIONS]=Field(min_length=1,max_length=6)
    receipt_contract:Literal['request_asset_terminal_v1']='request_asset_terminal_v1'

class AssetSpec(Contract):
    coverage_entity_ids:list[str]=Field(min_length=1,max_length=100)
    fields:list[str]=Field(min_length=1,max_length=30)
    modalities:list[MODALITIES]=Field(min_length=1,max_length=7)
    actions:list[ACTIONS]=Field(min_length=1,max_length=6)
    resolution_m:FiniteFloat|None=Field(default=None,gt=0)
    latency_seconds:StrictInt=Field(ge=1,le=86400)
    quote_ceiling_minor:StrictInt=Field(ge=0,le=100000000)
    currency:Literal['CNY','USD']='CNY'
    health:Literal['healthy','degraded','offline','unknown']='unknown'
    health_observed_at:str|None=None
    health_valid_until:str|None=None
    available_from:str|None=None
    available_until:str|None=None
    @model_validator(mode='after')
    def dates(self):
        for field in ['coverage_entity_ids','fields','modalities','actions']:
            if len(getattr(self,field))!=len(set(getattr(self,field))):raise ValueError('范围、字段、模态或动作不能重复')
        if self.health!='unknown' and not (self.health_observed_at and self.health_valid_until):raise ValueError('健康声明需要核查时间及有效期')
        if self.health_observed_at and instant(self.health_observed_at)>now():raise ValueError('健康核查时间不能在未来')
        if self.health_valid_until and (not self.health_observed_at or instant(self.health_valid_until)<=instant(self.health_observed_at)):
            raise ValueError('健康有效期须晚于核查时间')
        if self.available_from:instant(self.available_from)
        if self.available_until:instant(self.available_until)
        if self.available_until and (self.available_from and instant(self.available_until)<=instant(self.available_from)):
            raise ValueError('可用窗口结束须晚于开始')
        return self

class SelectionRequirement(Contract):
    allowed_modalities:list[MODALITIES]=Field(min_length=1,max_length=7)
    allowed_actions:list[ACTIONS]=Field(default_factory=lambda:['read_existing','observe'],min_length=1,max_length=6)
    max_resolution_m:FiniteFloat|None=Field(default=None,gt=0)
    max_latency_seconds:StrictInt=Field(default=300,ge=1,le=86400)
    max_attempts:StrictInt=Field(default=3,ge=1,le=5)
    max_total_cost_minor:StrictInt=Field(default=0,ge=0,le=100000000)
    currency:Literal['CNY','USD']='CNY'
    observation_wait_seconds:StrictInt=Field(default=300,ge=1,le=86400)
    plan_timeout_seconds:StrictInt=Field(default=3600,ge=1,le=2592000)
    minimum_verification:Literal['unverified_extraction','confirmed']='unverified_extraction'
    @model_validator(mode='after')
    def unique(self):
        for field in ['allowed_modalities','allowed_actions']:
            if len(getattr(self,field))!=len(set(getattr(self,field))):raise ValueError('允许模态与动作不能重复')
        return self

def validate_asset(db,p,source_id,value):
    source=published(db,p,source_id,'source')
    declared=source.data['specification'].get('observation_capability')
    if not declared:raise HTTPException(422,'来源尚未登记观测字段、模态、动作及绑定回执契约')
    declared=SourceObservation.model_validate(declared)
    for name in ['fields','modalities','actions']:
        if not set(value[name]).issubset(getattr(declared,name)):raise HTTPException(422,'资产能力不能超出已发布来源声明')
    for identifier in value['coverage_entity_ids']:
        entity=scoped(db,Entity,identifier,p)
        package=published(db,p,entity.schema_capability_id,'domain_package')
        fields=package.data['specification']['entity_schemas'][entity.entity_type].get('properties',{})
        if not set(value['fields']).issubset(fields):raise HTTPException(422,'观测字段必须属于覆盖对象的已发布业务类型')
    return source

def reservation(db,p,acq,*,create=False):
    asset_id=acq.data.get('sensor_asset_id')
    if not asset_id:return None
    if create:db.execute(insert(SensorReservation).values(workspace_id=p.workspace_id,asset_id=asset_id)
        .on_conflict_do_nothing(index_elements=['workspace_id','asset_id']))
    return db.scalar(select(SensorReservation).where(SensorReservation.workspace_id==p.workspace_id,
        SensorReservation.asset_id==asset_id).with_for_update())

def release(db,p,acq):
    row=reservation(db,p,acq)
    if row and row.acquisition_id==acq.id:row.acquisition_id=None
    from .device_identity import release as release_device
    release_device(db,p,acq)

def evidence(db,p,policy,entity_id,*,fields=None,single_record=False):
    from .services import query_evidence
    requirement=copy.deepcopy(policy.data.get('observation_evidence_requirement') or {'source':'observations',
        'minimum_verification':policy.data['selection_requirement']['minimum_verification'],
        'minimum_records_per_entity':1,'time':{'mode':'latest','timezone':policy.data.get('timezone','UTC'),'max_age_seconds':policy.data['max_age_seconds']}})
    if single_record:requirement['minimum_records_per_entity']=1
    result=query_evidence(db,p,[entity_id],fields or policy.data['fields'],learning=False,requirement=requirement)

    req=policy.data['selection_requirement'];accepted=[];ids=set()
    for item in result['observation_evidence']:
        from .models import Observation
        obs=scoped(db,Observation,item['observation_id'],p)
        meta=obs.data.get('sensor_evidence')
        # Legacy observations without asset metadata cannot prove modality/resolution.
        try:source=published(db,p,obs.source_id,'source')
        except HTTPException:
            result['excluded_evidence'].append({'observation_id':obs.id,'entity_id':entity_id,'reason':'原观测来源已撤回，未据此结案'});continue
        declaration=source.data['specification'].get('observation_capability',{})
        modes=[meta['modality']] if meta else declaration.get('modalities',[])
        reason=None
        if not set(modes)&set(req['allowed_modalities']):reason='缺少符合要求的观测模态登记'
        if req['max_resolution_m'] is not None and (not meta or meta.get('resolution_m') is None or meta['resolution_m']>req['max_resolution_m']):
            reason='缺少满足原要求的观测分辨率登记'
        if reason:result['excluded_evidence'].append({'observation_id':obs.id,'entity_id':entity_id,'reason':reason})
        else:accepted.append({**item,'sensor_evidence':meta});ids.add(obs.id)
    result['observation_evidence']=accepted
    result['conflicts']=[c for c in result['conflicts'] if len({digest(r['attributes'].get(c['field'])) for r in accepted if r['observation_id'] in c.get('observation_ids',[])})>1]
    selected=fields or policy.data['fields'];missing=[f for f in selected if sum(r['attributes'].get(f) is not None for r in accepted)<requirement['minimum_records_per_entity']]
    result['gaps']=[g for g in result['gaps'] if g.get('route')!='perception']
    if missing:result['gaps'].append({'entity_id':entity_id,'missing_fields':missing,'route':'perception','reason':'符合原模态、精度、时间和确认要求的观测不足'})
    result['status']='insufficient' if result['gaps'] or not accepted else 'conflict' if result['conflicts'] else 'sufficient'
    return result

def history(plan,kind,reason,**extra):
    plan.data={**plan.data,'history':[*plan.data.get('history',[]),{'kind':kind,'reason':reason,'at':now().isoformat(),**extra}]}
    plan.version+=1

def escalate(db,p,plan,code,reason):
    if plan.status=='escalated' and plan.data.get('escalation_code')==code:return
    plan.status='escalated';plan.data={**plan.data,'escalation_code':code,'reason':reason}
    history(plan,'escalated',reason,code=code)
    key='sensing-escalation:'+plan.id+':'+code+':'+str(plan.version)
    event=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.idempotency_key==key))
    if not event:
        event=Event(workspace_id=p.workspace_id,kind='perception.acquisition_escalation',idempotency_key=key,
            data={'plan_id':plan.id,'entity_id':plan.entity_id,'policy_id':plan.policy_id,'actor_id':p.user_id,'code':code,'reason':reason})
        db.add(event);db.flush()
    plan.data={**plan.data,'escalation_event_id':event.id,'business_delivery':'pending'}
    audit(db,p,'perception.plan_escalated',plan.id,{'code':code})

def used_budget(db,p,policy):
    rows=db.scalars(select(Acquisition).where(Acquisition.workspace_id==p.workspace_id,
        Acquisition.data['policy_id'].as_string()==policy.id)).all()
    day=str(now().astimezone(ZoneInfo(policy.data.get('timezone','UTC'))).date())
    return rows,sum(a.data.get('reserved_cost_minor',0) for a in rows),sum(a.data.get('budget_day')==day for a in rows),day

def candidates(db,p,policy,entity_id,missing,attempted):
    from . import device_identity as identity
    req=policy.data['selection_requirement'];rows,cost,count,day=used_budget(db,p,policy);result=[]
    legacy_unknown=identity.unidentified_inflight(db,p)
    for identifier in policy.data['sensor_asset_ids']:
        asset=scoped(db,SensorAsset,identifier,p);d=asset.data;reasons=[];source=None;link=None;device=None
        try:link,device=identity.checked(db,p,asset.id)
        except HTTPException as error:reasons.append(error.detail)
        if device and link:
            expected=policy.data.get('device_scope',{}).get(asset.id)
            current_scope={'binding_id':link.id,'device_id':device.id,'identity_hash':identity.identity_hash(device)}
            if expected!=current_scope:reasons.append('接口设备归属已改变，超出原监测批准范围')
        if legacy_unknown:reasons.append('工作区存在未归属统一设备的旧动作，先核对原回执')
        if device:
            hold=identity.occupied(db,p,device.id)
            if hold and hold.acquisition_id:reasons.append('统一设备被其他接口的未完成或未知动作占用')
        if asset.source_id not in policy.data['source_ids']:reasons.append('来源不在管理员授权目录')
        try:source=published(db,p,asset.source_id,'source');validate_asset(db,p,asset.source_id,d)
        except (HTTPException,ValidationError):reasons.append('来源声明未发布或已经失效')
        if asset.status!='enabled':reasons.append('资产已停用')
        if entity_id not in d['coverage_entity_ids']:reasons.append('登记覆盖不包含对象')
        gain=sorted(set(missing)&set(d['fields']))
        if not gain:reasons.append('无法补足当前缺失字段')
        if not set(req['allowed_modalities'])&set(d['modalities']):reasons.append('模态不符合授权要求')
        actions=[a for a in req['allowed_actions'] if a in d['actions']]
        if not actions:reasons.append('不支持获准动作；固定设备不能假称转向或飞行')
        if d['health']!='healthy' or not d.get('health_valid_until') or instant(d['health_valid_until'])<=now():reasons.append('健康声明未知、离线、降级或已过期')
        if d.get('available_from') and instant(d['available_from'])>now() or d.get('available_until') and instant(d['available_until'])<=now():reasons.append('当前不在登记可用窗口')
        if req['max_resolution_m'] is not None and (d['resolution_m'] is None or d['resolution_m']>req['max_resolution_m']):reasons.append('登记分辨率未知或不满足要求')
        if d['latency_seconds']>req['max_latency_seconds']:reasons.append('登记交付时间超限')
        if d['currency']!=req['currency']:reasons.append('币种不同，未进行隐含换算')
        if d['quote_ceiling_minor']+cost>req['max_total_cost_minor']:reasons.append('累计费用预留不足')
        if count>=policy.data['max_acquisitions_per_day']:reasons.append('策略当日次数已耗尽')
        if identifier in attempted:reasons.append('此计划已经尝试过该资产')
        occupied=db.scalar(select(SensorReservation).where(SensorReservation.workspace_id==p.workspace_id,SensorReservation.asset_id==identifier))
        if occupied and occupied.acquisition_id:reasons.append('资产被未完成或未知动作占用')
        recent=[a for a in rows if a.data.get('sensor_asset_id')==identifier and a.entity_id==entity_id]
        if recent and max(a.created_at for a in recent)>now()-timedelta(seconds=policy.data['cooldown_seconds']):reasons.append('对象与资产仍在冷却期')
        ex=source.data['execution'] if source else {}
        matching=[a for a in actions if ex.get('side_effect') is (a!='read_existing')]
        action='read_existing' if 'read_existing' in matching else matching[0] if matching else None
        if source and (not action or source.data['execution'].get('adapter')!='http' or not source.data['execution'].get('receipt_endpoint') or
            (action!='read_existing' and source.data['execution'].get('side_effect') is not True) or
            (action=='read_existing' and source.data['execution'].get('side_effect') is not False)):
            reasons.append('没有与所选动作匹配的已接入控制或读取接口')
        result.append({'asset_id':asset.id,'asset_version':asset.version,'asset_title':asset.title,'source_id':asset.source_id,
            'device_id':device.id if device else None,'device_version':device.version if device else None,
            'binding_id':link.id if link else None,'binding_version':link.version if link else None,
            'device_title':device.title if device else None,'source_hash':source.content_hash if source else None,'action':action,'fields':gain,'eligible':not reasons,'reasons':reasons,
            'cost_minor':d['quote_ceiling_minor'],'currency':d['currency'],'latency_seconds':d['latency_seconds'],
            'resolution_m':d['resolution_m'],'health_basis':'操作者登记且仍在有效期的健康声明，尚非真实设备探测证明',
            'rank':[-len(gain),0 if action=='read_existing' else 1,d['quote_ceiling_minor'],d['latency_seconds'],d['resolution_m'] or 1e12,asset.id]})
    return sorted(result,key=lambda c:(not c['eligible'],c['rank']))

def refresh(db,p,policy,plan,*,retry=False):
    from .goal_conflict_observation_service_v259 import check_plan,require_new_action,KEY as conflict_key
    once=check_plan(db,p,plan)
    from .perception import authorized_monitor
    from .data_gaps import receipts_ready
    authorized_monitor(db,p,policy)
    from .observation_requests import effective
    policy=effective(db,p,policy,plan.data.get('observation_request'))
    if plan.data['policy_hash']!=policy.data['approved_hash']:raise HTTPException(409,'原计划监测授权版本失效')
    if plan.status in {'completed','cancelled'}:return []
    budget_rows,cost,count,day=used_budget(db,p,policy)
    plan.data={**plan.data,'budget':{'reserved_cost_minor':cost,'currency':policy.data['selection_requirement']['currency'],
        'daily_count':count,'budget_day':day,'timezone':policy.data.get('timezone','UTC')}}
    state=evidence(db,p,policy,plan.entity_id)
    active=db.scalars(select(Acquisition).where(Acquisition.workspace_id==p.workspace_id,
        Acquisition.data['observation_plan_id'].as_string()==plan.id).order_by(Acquisition.created_at,Acquisition.id)).all()
    busy=[a for a in active if a.status in {'dispatching','unknown','awaiting_observation'}]
    if state['status']=='sufficient' and receipts_ready(db,p,{'result':state}) and not busy and (once is None or any(a.status=='succeeded' for a in active)):
        plan.status='completed';plan.data={**plan.data,'result':state,'reason':'现有观测满足原字段、时间、确认要求且已归集'}
        history(plan,'completed',plan.data['reason']);return []
    if busy:
        acq=busy[0]
        if acq.status in {'unknown','dispatching'}:escalate(db,p,plan,'unknown_action','动作结果未知；保留资产占用，核对回执前禁止替代或重放')
        elif now()>=instant(plan.data['deadline']):
            escalate(db,p,plan,'deadline_inflight','原计划期限已到；已接受动作仍待实际观测，保留占用并请求业务协调')
        elif now()>instant(acq.data['dispatch_started_at'])+timedelta(seconds=policy.data['selection_requirement']['observation_wait_seconds']):
            escalate(db,p,plan,'observation_timeout','已接受动作尚无合格且已归集观测；已超到数期限，保留占用并请求业务协调')
        else:
            reason=('原设备动作已永久结束，等待符合原质量要求且已归集的观测' if acq.data.get('device_action_terminal') else '原请求已接受，设备动作尚未确认结束；等待结束回执及合格观测')+'；未替代或重发'
            if plan.status!='awaiting_observation':history(plan,'receipt_reconciled',reason,acquisition_id=acq.id)
            plan.status='awaiting_observation';plan.data={**plan.data,'reason':reason}
        return []
    if now()>=instant(plan.data['deadline']):escalate(db,p,plan,'deadline','原计划期限已到，未扩大授权或继续补采');return []
    if plan.status=='escalated' and not retry:return []
    if state['status']=='conflict' and once is None:escalate(db,p,plan,'conflict','已有观测证据冲突，先核查现有来源，未自动增加设备动作');return []
    pending=next((a for a in active if a.status=='blocked_resource' or
        retry and a.status=='blocked_connector' and not a.data.get('dispatch_started_at')),None)
    if pending:
        if pending.status=='blocked_connector':
            options=candidates(db,p,policy,pending.entity_id,pending.data['selected_fields'],set())
            choice=next(c for c in options if c['asset_id']==pending.data['sensor_asset_id'])
            if choice['device_id']!=pending.data.get('sensor_device_id') or choice['source_hash']!=pending.data['source_hash'] or choice['fields']!=sorted(pending.data['selected_fields']) or choice['cost_minor']>pending.data['reserved_cost_minor']:
                escalate(db,p,plan,'changed_dispatch_scope','尚未下发请求的设备、来源、字段或已预留报价范围改变，未扩大原请求');return []
            pending.data={**pending.data,'sensor_asset_version':choice['asset_version'],'sensor_device_version':choice['device_version'],
                'device_binding_version':choice['binding_version'],'device_binding_id':choice['binding_id']}
            plan.status='selecting'
            history(plan,'undispatched_reviewed','人工重新核查同一设备及原字段，更新尚未下发请求的身份版本；未扩大原范围',acquisition_id=pending.id)
        try:pre_dispatch(db,p,policy,pending)
        except HTTPException as error:
            if error.status_code!=423:
                pending.status='blocked_connector';escalate(db,p,plan,'asset_changed',error.detail)
            else:plan.status='waiting_resource';plan.data={**plan.data,'reason':error.detail}
            return []
        pending.status='authorized';pending.version+=1;plan.status='awaiting_dispatch'
        history(plan,'resource_recovered','原资产条件恢复，重新排队尚未下发的原请求；未增加费用或尝试',acquisition_id=pending.id)
        db.add(Job(workspace_id=p.workspace_id,kind='dispatch_acquisition',idempotency_key='dispatch:'+pending.id+':resume:'+str(pending.version),
            data={'acquisition_id':pending.id,'actor_id':policy.approved_by}));return [pending.id]
    if any(a.status=='authorized' for a in active):return []
    if len(active)>=(1 if once is not None else policy.data['selection_requirement']['max_attempts']):escalate(db,p,plan,'attempts','原计划最大尝试数已耗尽，未继续替代');return []
    missing=copy.deepcopy(once['context']['fields']) if once is not None else sorted({field for g in state.get('gaps',[]) for field in g.get('missing_fields',[])})
    if not missing:missing=policy.data['fields'] # Collection receipt can be pending even when values are present.
    if state['status']=='sufficient' and once is None:
        plan.status='awaiting_collection';plan.data={**plan.data,'reason':'字段已有记录，等待数据管理归集回执；未重复补采'};return []
    options=candidates(db,p,policy,plan.entity_id,missing,{a.data.get('sensor_asset_id') for a in active})
    plan.data={**plan.data,'candidates':options,'remaining_fields':missing}
    eligible=[c for c in options if c['eligible']]
    if not eligible:
        waiting=any(c['reasons'] and all(r in {'资产被未完成或未知动作占用','统一设备被其他接口的未完成或未知动作占用','对象与资产仍在冷却期','当前不在登记可用窗口'} for r in c['reasons']) for c in options)
        if waiting:plan.status='waiting_resource';plan.data={**plan.data,'reason':'可满足要求的资产正在占用、冷却或等候窗口；保持原期限'}
        else:escalate(db,p,plan,'no_source','没有满足能力、覆盖、健康、精度、时效或预算的已授权资产；请核对选择依据')
        return []
    from .monitor_scheduling_service_v253 import require_proposal
    require_proposal(db,p,policy,plan.data.get('observation_request'))
    if once is not None:require_new_action(db,p,plan)
    choice=eligible[0];_,_,_,day=used_budget(db,p,policy)
    acq=Acquisition(workspace_id=p.workspace_id,entity_id=plan.entity_id,capability_id=choice['source_id'],
        idempotency_key='sensing:'+plan.id+':'+str(len(active)+1),status='authorized',data={'policy_id':policy.id,
            'policy_hash':policy.data['approved_hash'],'actor_id':policy.approved_by,'observation_plan_id':plan.id,
            **({conflict_key:copy.deepcopy(once['link'])} if once is not None else {}),
            'sensor_device_id':choice['device_id'],'sensor_device_version':choice['device_version'],
            'device_binding_id':choice['binding_id'],'device_binding_version':choice['binding_version'],
            **({'data_gap_id':plan.data['observation_request']['gap_id']} if plan.data.get('observation_request') else {}),
            'sensor_asset_id':choice['asset_id'],'sensor_asset_version':choice['asset_version'],'source_hash':choice['source_hash'],
            'selected_action':choice['action'],'selected_fields':choice['fields'],'reserved_cost_minor':choice['cost_minor'],
            'currency':choice['currency'],'budget_day':day,'gaps':[{'field':f,'reason':'原要求观测不足'} for f in choice['fields']],
            'reason':'在有效授权内按补足收益、已有读取优先、费用、时间及精度选择资产','selection':choice})
    db.add(acq);db.flush()
    from .monitor_scheduling_service_v253 import freeze_acquisition
    freeze_acquisition(db,p,policy,acq,plan.data.get('observation_request'))
    _,total,count,day=used_budget(db,p,policy)
    plan.data={**plan.data,'budget':{'reserved_cost_minor':total,'currency':choice['currency'],'daily_count':count,'budget_day':day,'timezone':policy.data.get('timezone','UTC')}}
    plan.status='awaiting_dispatch';plan.data={**plan.data,'current_acquisition_id':acq.id,'reason':acq.data['reason']}
    history(plan,'selected',acq.data['reason'],acquisition_id=acq.id,asset_id=choice['asset_id'])
    job=Job(workspace_id=p.workspace_id,kind='dispatch_acquisition',idempotency_key='dispatch:'+acq.id,
        data={'acquisition_id':acq.id,'actor_id':policy.approved_by})
    db.add(job)
    if once is not None:
        from .goal_conflict_observation_service_v259 import freeze_action
        freeze_action(db,p,plan,acq,job)
    return [acq.id]

def propose(db,p,policy,entity_ids,*,observation_request=None):
    from .observation_requests import effective,deadline as request_deadline
    effective_policy=effective(db,p,policy,observation_request,dispatch=True)
    from .monitor_scheduling_service_v253 import action_pin,KEY as schedule_key
    schedule_pin=action_pin(db,p,policy,observation_request)
    periodic_pin=schedule_pin if schedule_pin and schedule_pin['trigger']=='periodic' else None
    created=[]
    for identifier in entity_ids:
        plan=db.scalar(select(ObservationPlan).where(ObservationPlan.workspace_id==p.workspace_id,
            ObservationPlan.policy_id==policy.id,ObservationPlan.entity_id==identifier,
            ObservationPlan.status.not_in(['completed','cancelled']),
            *([ObservationPlan.data[schedule_key]['control']['record_hash'].as_string()==periodic_pin['control']['record_hash']] if periodic_pin else []),
            ObservationPlan.data['observation_request']['gap_id'].as_string()==(observation_request['gap_id'] if observation_request else None)).order_by(ObservationPlan.created_at.desc()).with_for_update())
        if not plan:
            state=evidence(db,p,effective_policy,identifier)
            from .data_gaps import receipts_ready
            if state['status']=='sufficient' and receipts_ready(db,p,{'result':state}):continue
            deadline=min(now()+timedelta(seconds=policy.data['selection_requirement']['plan_timeout_seconds']),instant(policy.data['valid_until']))
            if request_deadline(observation_request):deadline=min(deadline,request_deadline(observation_request))
            plan=ObservationPlan(workspace_id=p.workspace_id,policy_id=policy.id,entity_id=identifier,
                data={'policy_hash':policy.data['approved_hash'],'deadline':deadline.isoformat(),'actor_id':policy.approved_by,'initial_evidence':state,
                    **({schedule_key:copy.deepcopy(schedule_pin)} if schedule_pin else {}),
                    **({'observation_request':copy.deepcopy(observation_request)} if observation_request else {})})
            db.add(plan);db.flush()
        created.extend(refresh(db,p,policy,plan))
    return created

def pre_dispatch(db,p,policy,acq):
    from .device_identity import lock
    lock(db,p)
    plan=scoped(db,ObservationPlan,acq.data['observation_plan_id'],p,True)
    if plan.status in {'completed','cancelled','escalated'} or now()>=instant(plan.data['deadline']):raise HTTPException(409,'原观测计划已停止或超期')
    from .observation_requests import effective
    policy=effective(db,p,policy,plan.data.get('observation_request'),dispatch=True)
    from .monitor_scheduling_service_v253 import require_dispatch
    require_dispatch(db,p,policy,acq,plan.data.get('observation_request'))
    from .goal_conflict_observation_service_v259 import require_new_action
    require_new_action(db,p,plan,acquisition=acq)
    asset=scoped(db,SensorAsset,acq.data['sensor_asset_id'],p,True)
    options=candidates(db,p,policy,acq.entity_id,acq.data['selected_fields'],set())
    choice=next(c for c in options if c['asset_id']==asset.id)
    # This request already owns its budget allocation and cooldown record.
    ignored={'累计费用预留不足','策略当日次数已耗尽','对象与资产仍在冷却期'}
    asset_busy='资产被未完成或未知动作占用'
    device_busy='统一设备被其他接口的未完成或未知动作占用'
    if (acq.status in {'authorized','dispatching'} and 'dispatch_started_at' not in acq.data
            and acq.workspace_id==p.workspace_id and acq.data.get('actor_id')==p.user_id==policy.approved_by
            and (asset_busy in choice['reasons'] or device_busy in choice['reasons'])):
        # candidates intentionally reports every occupied resource. During the
        # second pre-send check this exact request already owns both holds.
        # The identity lock above and these original row locks retain the same
        # exclusion fence; no resource is created, cleared or reassigned here.
        if asset_busy in choice['reasons']:
            hold=reservation(db,p,acq)
            if (hold is not None and hold.workspace_id==p.workspace_id
                    and hold.asset_id==asset.id==acq.data['sensor_asset_id']==choice['asset_id']
                    and hold.acquisition_id==acq.id):
                ignored.add(asset_busy)
        if device_busy in choice['reasons']:
            from .device_identity import reservation as device_reservation
            hold=device_reservation(db,p,acq)
            if (hold is not None and hold.workspace_id==p.workspace_id
                    and hold.device_id==acq.data.get('sensor_device_id')==choice['device_id']
                    and hold.acquisition_id==acq.id):
                ignored.add(device_busy)
    reasons=[r for r in choice['reasons'] if r not in ignored]
    _,cost,count,day=used_budget(db,p,policy)
    if cost>policy.data['selection_requirement']['max_total_cost_minor'] or count>policy.data['max_acquisitions_per_day'] or acq.data['budget_day']!=day:
        reasons.append('派发时累计预算或原日次数授权已失效')
    identity_changed=any(choice[key]!=acq.data.get(saved) for key,saved in [('device_id','sensor_device_id'),('device_version','sensor_device_version'),('binding_id','device_binding_id'),('binding_version','device_binding_version')])
    if identity_changed:reasons.append('统一设备或接口绑定版本改变，原选择需要重新核查')
    if asset.version!=acq.data['sensor_asset_version'] or choice['source_hash']!=acq.data['source_hash'] or reasons:
        if asset.version==acq.data['sensor_asset_version'] and choice['source_hash']==acq.data['source_hash'] and not identity_changed and reasons and set(reasons)<={'资产被未完成或未知动作占用','统一设备被其他接口的未完成或未知动作占用'}:raise HTTPException(423,'资产被未完成或未知动作占用；原请求尚未下发')
        raise HTTPException(409,'资产/来源版本或可执行条件改变，未下发；'+('、'.join(reasons) or '请重新核查选择'))
    if plan.data.get('observation_request'):
        acq.data={**acq.data,'dispatch_observation_constraints':copy.deepcopy({key:policy.data[key] for key in
            ['fields','max_age_seconds','selection_requirement','observation_evidence_requirement']})}
    return asset

def after_receipt(db,p,acq,receipt):
    """Only permanently closed, bound requests can release a failed action."""
    asset=scoped(db,SensorAsset,acq.data['sensor_asset_id'],p)
    if receipt.get('request_key')!=acq.idempotency_key or receipt.get('asset_external_id')!=asset.external_id or not isinstance(receipt.get('receipt_id'),str) or not receipt.get('receipt_id') or len(receipt['receipt_id'])>200:
        raise HTTPException(409,'供应商回执未绑定原请求与资产')
    status=receipt.get('status')
    if status not in {'accepted','completed','rejected','completed_no_data','cancelled'}:raise HTTPException(409,'供应商回执状态不受支持')
    if status=='completed' and (receipt.get('terminal') is not True or receipt.get('no_future_device_activity') is not True):
        raise HTTPException(409,'成功回执未保证原设备动作永久结束，继续保留占用')
    if acq.data.get('device_action_terminal') and status=='accepted':raise HTTPException(409,'原请求已永久结束，回执不能降级为执行中')
    if status not in {'accepted','completed'} and (receipt.get('terminal') is not True or receipt.get('no_future_observation') is not True):
        raise HTTPException(409,'失败回执未保证原请求永久结束；继续保留未知动作和占用')
    acq.status='awaiting_observation' if status in {'accepted','completed'} else 'rejected'
    acq.data={**acq.data,'device_action_terminal':status!='accepted'}
    if status not in {'accepted','completed'}:release(db,p,acq)
    return status

def close_observation(db,p,observation,acq,*,refresh_plan=True):
    from .data_gaps import receipts_ready
    if acq.data.get('sensor_device_id') and acq.data.get('selected_action')!='read_existing' and acq.data.get('device_action_terminal') is not True:return False
    policy=scoped(db,MonitorPolicy,acq.data['policy_id'],p)
    plan=scoped(db,ObservationPlan,acq.data['observation_plan_id'],p,True)
    from .observation_requests import frozen
    # Parent cancellation stops future work but never drops original dispatched quality.
    original_policy=policy
    if plan.data.get('observation_request'):policy=frozen(policy,acq)
    state=evidence(db,p,policy,acq.entity_id,fields=acq.data['selected_fields'],single_record=True)
    from .goal_conflict_observation_service_v259 import check_plan, _ack
    once=check_plan(db,p,plan)
    if once is None and (state['status']!='sufficient' or not receipts_ready(db,p,{'result':state})):return False
    matched=[r for r in state.get('observation_evidence',[]) if r['observation_id']==observation.id]
    if len(matched)!=1 or not all(matched[0]['attributes'].get(f) is not None for f in acq.data['selected_fields']):return False
    if once is not None:
        # The new reading must pass the original selection admission. Historic
        # conflicting readings remain in the unmodified Goal/window evidence.
        if not receipts_ready(db,p,{'result':{'observation_evidence':matched}}):return False
        from .observation_integrity import checked_report
        _ack(db,p,observation,checked_report(db,p,observation))
    acq.status='succeeded';acq.version+=1;release(db,p,acq)
    audit(db,p,'acquisition.close',acq.id,{'observation_id':observation.id,'verified_collection':True})
    acq.data={**acq.data,'closing_observation_id':observation.id,'closing_evidence':state,'finished_at':now().isoformat()}
    plan=scoped(db,ObservationPlan,acq.data['observation_plan_id'],p,True)
    history(plan,'observation_received','所选字段已满足原确认、时间与归集要求',acquisition_id=acq.id,observation_id=observation.id)
    if once is not None:
        if plan.status!='cancelled':
            plan.status='completed';plan.data={**plan.data,'result':state,
                'reason':'本次明确申请的新观测已按原要求独立接收；原同刻冲突与Goal仍按原规则待人工核查，未自动充分或验收'}
            history(plan,'completed',plan.data['reason'])
        return True
    if not refresh_plan:
        # This post-ACK path settles only saved work. Do not call refresh(),
        # which may choose another asset or queue another device request.
        if plan.status not in {'completed','cancelled'}:
            complete=evidence(db,p,policy,acq.entity_id)
            busy=db.scalar(select(Acquisition.id).where(Acquisition.workspace_id==p.workspace_id,
                Acquisition.data['observation_plan_id'].as_string()==plan.id,
                Acquisition.status.not_in(['succeeded','rejected','cancelled'])).limit(1))
            if complete['status']=='sufficient' and receipts_ready(db,p,{'result':complete}) and busy is None:
                plan.status='completed';plan.data={**plan.data,'result':complete,
                    'reason':'原计划全部字段与独立收件均已满足，既有设备请求已结束'}
                history(plan,'completed',plan.data['reason'])
            else:
                plan.status='awaiting_observation' if busy is not None else 'awaiting_collection'
                plan.data={**plan.data,'reason':'原补采已核验；整份原计划仍有缺口或既有请求待结束，未安排新设备动作'}
                history(plan,'settlement_waiting',plan.data['reason'])
        return True
    policy=original_policy
    try:
        from .perception import authorized_monitor
        authorized_monitor(db,p,policy)
        if plan.status=='escalated':plan.status='selecting'
        refresh(db,p,policy,plan)
    except HTTPException:
        plan.status='cancelled';history(plan,'cancelled','新观测已保存，但监测授权失效，未继续其他动作')
    return True

def receive_escalation(db,event):
    plan=db.scalar(select(ObservationPlan).where(ObservationPlan.id==event.data['plan_id'],
        ObservationPlan.workspace_id==event.workspace_id))
    if not plan:return False
    from .original_acquisition_coordination_v127 import prepare
    original=prepare(db,event)
    if original.receipt is not None:return original.receipt
    plan,gap=original.plan,original.gap
    task=original.task or db.scalar(select(Task).where(Task.workspace_id==event.workspace_id,Task.id==plan.data.get('business_task_id')))
    if not task and gap and not gap.data.get('business_task_id'):
        from .data_gaps import _coordinate
        _coordinate(db,event,gap)
    if not task and gap and gap.data.get('business_task_id'):
        task=db.scalar(select(Task).where(Task.workspace_id==event.workspace_id,Task.id==gap.data['business_task_id']))
    if not task:
        entity=db.get(Entity,plan.entity_id)
        task=Task(workspace_id=event.workspace_id,owner_id=event.data['actor_id'],package_id=entity.schema_capability_id,
            title='协调观测：'+str(entity.data.get('name') or entity.external_id),status='draft',data={'trigger':event.kind,
                'entity_ids':[entity.id],'fields':plan.data.get('remaining_fields',[]),'observation_plan_id':plan.id,
                'coordination_reason':event.data['reason']})
        db.add(task);db.flush()
    task=db.scalar(select(Task).where(Task.id==task.id,Task.workspace_id==event.workspace_id).with_for_update().execution_options(populate_existing=True))
    plan=db.scalar(select(ObservationPlan).where(ObservationPlan.id==plan.id,ObservationPlan.workspace_id==event.workspace_id).with_for_update().execution_options(populate_existing=True))
    if event.id in task.data.get('perception_escalations',[]) and plan.data.get('business_task_id')==task.id and plan.data.get('business_delivery')=='received':return True
    task.data={**task.data,'perception_escalations':list(dict.fromkeys([*task.data.get('perception_escalations',[]),event.id]))}
    if not task.data.get('native_goal_context'):task.version+=1  # Native system receipts must preserve human draft CAS.
    plan.data={**plan.data,'business_delivery':'received','business_task_id':task.id,'business_received_at':now().isoformat()};plan.version+=1
    return True

def validate_policy(db,p,value):
    from zoneinfo import ZoneInfoNotFoundError
    try:ZoneInfo(value.get('timezone','UTC'))
    except (ZoneInfoNotFoundError,ValueError):raise HTTPException(422,'监测时区必须为IANA时区')
    for field in ['entity_ids','source_ids','fields','sensor_asset_ids']:
        items=value.get(field,[])
        if len(items)!=len(set(items)):raise HTTPException(422,'策略范围、字段和资产不能重复')
    for identifier in value['entity_ids']:
        entity=scoped(db,Entity,identifier,p);package=published(db,p,entity.schema_capability_id,'domain_package')
        if not set(value['fields']).issubset(package.data['specification']['entity_schemas'][entity.entity_type].get('properties',{})):
            raise HTTPException(422,'监测字段不属于范围对象类型')
    req=value.get('selection_requirement');ids=value.get('sensor_asset_ids',[])
    if bool(req)!=bool(ids):raise HTTPException(422,'资产白名单和选择要求必须一起指定')
    if req:
        SelectionRequirement.model_validate(req)
        for identifier in ids:
            asset=scoped(db,SensorAsset,identifier,p)
            from .device_identity import checked
            checked(db,p,asset.id)
            validate_asset(db,p,asset.source_id,asset.data)
            if asset.source_id not in value['source_ids']:raise HTTPException(422,'资产来源必须在授权来源范围内')
            if not set(asset.data['coverage_entity_ids'])&set(value['entity_ids']):raise HTTPException(422,'资产登记覆盖与策略没有交集')
        if value.get('device_scope') is not None:
            from .device_identity import scope_snapshot
            if value['device_scope']!=scope_snapshot(db,p,ids):raise HTTPException(409,'保存策略后的设备归属改变，请重新核查设备范围并保存策略')
    elif len(value['source_ids'])>1:raise HTTPException(422,'多来源监测须明确登记资产与选择要求，不能无差别下发')

def refresh_after_action(db,p,acq):
    if not acq.data.get('observation_plan_id'):return
    policy=scoped(db,MonitorPolicy,acq.data['policy_id'],p,True)
    plan=scoped(db,ObservationPlan,acq.data['observation_plan_id'],p,True)
    if plan.status=='cancelled':return
    if acq.status=='rejected' and plan.status=='escalated':plan.status='selecting'
    try:refresh(db,p,policy,plan)
    except HTTPException:
        plan.status='cancelled';history(plan,'cancelled','监测授权失效，保留外部动作回执，未派发新动作')

def revoke(db,p,policy,reason):
    for plan in db.scalars(select(ObservationPlan).where(ObservationPlan.workspace_id==p.workspace_id,
        ObservationPlan.policy_id==policy.id,ObservationPlan.status.not_in(['completed','cancelled'])).with_for_update()):
        plan.status='cancelled';history(plan,'cancelled','管理员撤销监测授权：'+reason)
    for acq in db.scalars(select(Acquisition).where(Acquisition.workspace_id==p.workspace_id,
        Acquisition.data['policy_id'].as_string()==policy.id,Acquisition.status=='authorized').with_for_update()):
        acq.status='cancelled';acq.version+=1;acq.data={**acq.data,'reason':'监测授权撤销，未派发'}


def close_available(db,p,acq):
    if not acq.data.get('observation_plan_id') or acq.status!='awaiting_observation':return
    from .models import Observation
    from .perception import receive_acquisition_observation
    for obs in db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,
        Observation.data['acquisition_id'].as_string()==acq.id,Observation.status=='processed')
        .order_by(Observation.observed_at.desc()).limit(20)):
        receive_acquisition_observation(db,p,obs)
        if acq.status=='succeeded':break
