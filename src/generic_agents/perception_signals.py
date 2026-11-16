"""Durable business receipt for registered observation anomalies, never physical truth."""
import copy
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException
from .models import PerceptionSignal,DomainInstance,InstanceRelease,Entity,Observation,Event,Task,Capability,now
from .security import Principal
from .services import scoped,check_version,published
from .contracts import digest
from .observation_integrity import checked_report,CHECK_ERRORS


def _machine_payload_hash(db,p,row,observed_at):
    source=scoped(db,Capability,row.source_id,p)
    extractor=scoped(db,Capability,row.data['extractor_id'],p)
    return digest({'source_id':row.source_id,'record':row.source_record_id,'processing_version':row.processing_version,
        'entity_id':row.entity_id,'observed_at':observed_at,
        'business_day':row.business_day,'original_request':row.data.get('original_request'),
        'input_hash':row.data.get('input_hash'),'attributes':row.data.get('attributes'),
        'extracted':row.data.get('extracted'),'evidence_ids':row.data.get('evidence_ids',[]),
        'invocation_id':row.data.get('invocation_id'),'extractor_id':row.data.get('extractor_id'),
        'source_hash':digest(source.data),'extractor_hash':digest(extractor.data)})


def _machine_basis_for_anchor(db,p,row,anchor):
    # Internal writer/replay seam: even this closed marker is reloaded against
    # the actual original Event before any raw timestamp reaches the hash.
    from .observation_time_anchor_v135 import check_anchor
    value=check_anchor(db,p,row,anchor)
    return {'machine_hash':_machine_payload_hash(db,p,row,value['observed_at']),
        'observed_at':value['observed_at'],'time_anchor':value,'protocol':'processing_event'}


def machine_basis(db,p,row,*,event_id=None,job_id=None):
    from .native_sound_receipts import handles as sound_source
    if sound_source(db,p,row):
        from .sound_candidate_watches import machine_basis as sound_basis
        return sound_basis(db,p,row,event_id=event_id,job_id=job_id)
    if row.data.get('native_instance_id'):
        from .candidate_watches import source
        return {'machine_hash':source(db,p,row)['machine_hash'],'observed_at':None,
            'time_anchor':None,'protocol':'native_packet'}
    from .observation_time_anchor_v135 import processing_anchor,candidate_time
    if row.data.get('candidate_only'):
        anchor=candidate_time(db,p,row,event_id=event_id,job_id=job_id)
    else:
        if job_id is not None:raise ValueError('登记异常不能借候选检查任务改换原时间')
        value=processing_anchor(db,p,row,event_id=event_id)
        anchor={'observed_at':value['observed_at'],'time_anchor':value,'protocol':'processing_event','expected_hash':None}
    signature=_machine_payload_hash(db,p,row,anchor['observed_at'])
    if anchor['expected_hash'] is not None and signature!=anchor['expected_hash']:
        raise ValueError('原候选缺少可核对的原时间字节或原机器依据已变化；不能猜时区或重签历史')
    return {'machine_hash':signature,'observed_at':anchor['observed_at'],
        'time_anchor':anchor['time_anchor'],'protocol':anchor['protocol']}


def machine_hash(db,p,row,*,event_id=None,job_id=None):
    return machine_basis(db,p,row,event_id=event_id,job_id=job_id)['machine_hash']


def _simulation_projection(db,p,row,value):
    """Keep the saved SIM source on every projection; never infer a manual source."""
    from .simulation_observation_origin_v177 import guard
    label=guard(db,p,row)
    keys={'simulation','origin_kind','field_verified','simulation_origin'}
    if label is None:
        if keys & set(value):raise ValueError('非模拟原观测不能附加模拟来源标记')
    elif (value.get('simulation') is not True or value.get('origin_kind')!='simulated_equipment'
          or value.get('field_verified') is not False
          or digest(value.get('simulation_origin'))!=digest(label)):
        raise ValueError('模拟通知的原来源标记缺失或不一致')
    return label


def event_report(db,p,event,*,allow_candidates=False):
    if event.kind=='perception.candidate':
        from .candidate_watches import event_report as candidate_report
        return candidate_report(db,p,event)
    if event.kind!='perception.anomaly':raise ValueError('不是观测异常通知')
    row=scoped(db,Observation,event.data['observation_id'],p)
    if event.data.get('entity_id')!=row.entity_id or event.data.get('source_id')!=row.source_id:
        raise ValueError('通知来源或对象不一致')
    report=checked_report(db,p,row,allow_candidates=allow_candidates)
    _simulation_projection(db,p,row,event.data)
    if event.data.get('invocation_id')!=row.data['invocation_id'] or event.data.get('extracted')!=report['extracted']:
        raise ValueError('异常通知与原提取回执不一致')
    from .observation_time_anchor_v135 import processing_anchor
    processing_anchor(db,p,row,event_id=event.id)
    anomalies=report['extracted'].get('anomalies')
    if not isinstance(anomalies,list) or not anomalies:raise ValueError('通知没有可核对的异常候选')
    return row,report


def freeze(db,p,event,instance,entity):
    if entity.namespace!=instance.namespace:raise ValueError('对象不属于接收实例')
    row,report=event_report(db,p,event)
    simulation_origin=_simulation_projection(db,p,row,event.data)
    release=scoped(db,InstanceRelease,instance.active_release_id,p)
    from .entity_release_compat import require_entity
    proof=require_entity(db,p,instance,release,entity)
    candidate=event.kind=='perception.candidate'
    if candidate and event.data['watch_definition']['release_id']!=release.id:raise ValueError('原线索规则不属于当前接收版本')
    if not candidate:
        contract=row.data.get('reported_rule_contract')
        if contract:
            if contract.get('instance_id')!=instance.id or contract.get('release_id')!=release.id:
                raise ValueError('原登记规则不属于当前接收版本，旧通知不能改签')
        elif entity.schema_capability_id!=release.package_id:
            raise ValueError('旧通知缺少当前接收版本的明确规则授权')
    original=machine_basis(db,p,row,event_id=event.id)
    basis={'schema':'local-model-candidate-signal-v1' if candidate else 'reported-anomaly-v1','candidate_only':candidate,'instance_id':instance.id,'release_id':release.id,
        'package_id':release.package_id,'entity_id':entity.id,
        'object_name':next((v for v in entity.data.values() if isinstance(v,str) and v.strip()),entity.external_id),
        'entity_version':entity.version,'observation_id':row.id,'source_name':report['source_name'],
        'original_attributes':copy.deepcopy(report['attributes'] if candidate else row.data['attributes']),'extracted':copy.deepcopy(report['extracted']),
        'trigger_rules':copy.deepcopy([event.data['selection']] if candidate else scoped(db,Capability,row.data['extractor_id'],p).data['specification'].get('rules',[])),
        'original_model_result':copy.deepcopy(report['original']) if candidate else None,
        'evidence_ids':copy.deepcopy(row.data.get('evidence_ids',[])),
        'watch_definition':copy.deepcopy(event.data['watch_definition']) if candidate else None,
        'reported_at':original['observed_at'],'business_day':row.business_day,
        'event_hash':digest(event.data),'observation_hash':original['machine_hash'],
        'not_authoritative':True,'source_time_verified':False,'entity_state_updated':False}
    if entity.schema_capability_id!=release.package_id:
        basis.update(source_entity_package_id=entity.schema_capability_id,entity_definition_hash=digest(proof))
    if simulation_origin is not None:
        if (simulation_origin['instance_id']!=instance.id or simulation_origin['release_id']!=release.id
            or simulation_origin['entity_id']!=entity.id):raise ValueError('模拟来源不能改变接收实例或版本')
        basis.update(simulation=True,origin_kind='simulated_equipment',field_verified=False,
                     simulation_origin=copy.deepcopy(simulation_origin))
    return basis


def receive(db,event):
    from .audio_stream_delivery import handles_event as handles_audio_event,receive as receive_audio
    audio_actor=Principal(event.data.get('producer_actor_id','audio-receiver'),event.workspace_id,'viewer','audio-signal-receiver')
    if handles_audio_event(db,audio_actor,event):return receive_audio(db,event)
    entity=db.get(Entity,event.data.get('entity_id'))
    if not entity or entity.workspace_id!=event.workspace_id:return False
    instance=db.scalar(select(DomainInstance).where(DomainInstance.workspace_id==event.workspace_id,
        DomainInstance.namespace==entity.namespace).with_for_update().execution_options(populate_existing=True))
    if not instance:return False  # Keep the explicitly separate legacy workflow.
    observation=db.get(Observation,event.data.get('observation_id'))
    if not observation or observation.workspace_id!=event.workspace_id or observation.entity_id!=entity.id:
        actor='signal-receiver'
    else:actor=observation.data.get('submitted_by','signal-receiver')
    p=Principal(actor,event.workspace_id,'viewer','signal-receiver')
    from .sound_candidate_watches import handles_event
    sound_notification=handles_event(db,p,event)
    issue=None;basis=None
    try:
        basis=freeze(db,p,event,instance,entity)
    except (*CHECK_ERRORS,AttributeError):
        issue='原登记、来源、提取回执或实例版本未通过核对；异常已保留，请先恢复依据'
    from .signal_routing import freeze_intent,failure_state
    intent=None;routing_failure=None
    try:
        intent=freeze_intent(db,instance,event,observation,basis) if basis and observation and not sound_notification else None
    except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
        routing_failure=failure_state(instance.data.get('signal_routes'))
    # Routing corruption cannot obstruct the original anomaly inbox receipt.

    db.execute(insert(PerceptionSignal).values(workspace_id=event.workspace_id,event_id=event.id,
        instance_id=instance.id,entity_id=entity.id,status='blocked_evidence' if issue else 'unassigned',
        data={'basis':basis,'basis_hash':digest(basis) if basis else None,'receipt_issue':issue,
              'note':'','note_requests':[],'task_ids':[],'received_at':now().isoformat(),
              'raw_event_hash':digest(event.data),'receiving_release_id':instance.active_release_id,
              **({'route_intent':intent} if intent else {}),
              **({'route_failure':routing_failure} if routing_failure else {})})
        .on_conflict_do_nothing(index_elements=['workspace_id','event_id']))
    # The parent and its quota reservation belong to this receipt transaction.
    # Isolate bad routing data; never roll back the original anomaly inbox ACK.
    if basis and observation and not sound_notification:
        signal=db.scalar(select(PerceptionSignal).where(PerceptionSignal.workspace_id==event.workspace_id,PerceptionSignal.event_id==event.id))
        try:
            with db.begin_nested():
                from .signal_approval_routing_v247 import freeze as freeze_approval
                freeze_approval(db,instance,event,observation,signal,basis)
        except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
            signal.data={**signal.data,'signal_approval_failure_v247':{
                'message':'异常审批草稿的原授权或来源未通过核对；原异常保留，未发起新任务'}}
    if basis and observation and not sound_notification and instance.data.get('signal_dual_routes') is not None:
        signal=db.scalar(select(PerceptionSignal).where(PerceptionSignal.workspace_id==event.workspace_id,PerceptionSignal.event_id==event.id))
        try:
            with db.begin_nested():
                from .signal_dual_handoffs import freeze_parent
                parent=freeze_parent(db,instance,event,observation,basis,signal)
                if parent:signal.data={**signal.data,'dual_handoff_id':parent.id}
        except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
            signal.data={**signal.data,'dual_handoff_failure':{'code':'dual_freeze_blocked','message':'双路授权或交接依据未通过核对，原异常已保留'}}
    if basis and observation and not sound_notification and instance.data.get('signal_graph_routes_v134') is not None:
        signal=db.scalar(select(PerceptionSignal).where(PerceptionSignal.workspace_id==event.workspace_id,PerceptionSignal.event_id==event.id))
        try:
            with db.begin_nested():
                from .signal_graph_routing_v134 import freeze as freeze_graph
                freeze_graph(db,instance,event,observation,signal,basis)
        except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
            signal.data={**signal.data,'graph_route_failure_v134':{'code':'graph_route_freeze_blocked',
                'message':'完整流程授权或原交接依据未通过核对；异常通知保留，本次不自动重试'}}
    return True


def checked(db,p,signal,*,allow_rejected=True,lock_sources=True):
    from .audio_stream_delivery import is_signal as is_audio_signal,checked_signal
    if is_audio_signal(db,p,signal):return checked_signal(db,p,signal,allow_rejected=allow_rejected,lock_sources=lock_sources)
    basis=signal.data.get('basis')
    if not basis or digest(basis)!=signal.data.get('basis_hash'):raise HTTPException(409,'异常依据尚未核对或原快照发生变化')
    if basis['instance_id']!=signal.instance_id or basis['entity_id']!=signal.entity_id:
        raise HTTPException(409,'异常的接收实例或对象发生变化')
    event=scoped(db,Event,signal.event_id,p)
    if event.status!='received' or digest(event.data)!=basis['event_hash']:
        raise HTTPException(409,'原通知或业务送达回执发生变化')
    try:
        row,report=event_report(db,p,event)
        _simulation_projection(db,p,row,basis)
        original=machine_basis(db,p,row,event_id=event.id)
        if (row.id!=basis['observation_id'] or original['machine_hash']!=basis['observation_hash']
            or 'reported_at' not in basis or original['observed_at']!=basis['reported_at']):
            raise ValueError('原登记版本发生变化')
        entity=scoped(db,Entity,signal.entity_id,p);instance=scoped(db,DomainInstance,signal.instance_id,p)
        release=scoped(db,InstanceRelease,basis['release_id'],p)
        from .entity_release_compat import require_entity
        proof=require_entity(db,p,instance,release,entity,lock_sources=lock_sources)
        if release.package_id!=basis['package_id']:
            raise ValueError('原对象版本或实例关联改变')
        has_proof=any(key in basis for key in ('source_entity_package_id','entity_definition_hash'))
        if ((entity.schema_capability_id!=basis['package_id'] or has_proof)
            and (basis.get('source_entity_package_id')!=entity.schema_capability_id
                or basis.get('entity_definition_hash')!=digest(proof))):
            raise ValueError('原对象来源或完整定义证明改变')
        if not allow_rejected and report['review']=='rejected':raise ValueError('原结果已被人工拒绝')
    except CHECK_ERRORS:
        raise HTTPException(409,'原异常依据已变化、被拒绝或未通过核对，请核对原登记；已保存说明保留')
    return basis


def view(db,p,signal):
    from .audio_stream_delivery import is_signal as is_audio_signal,signal_view as audio_signal_view
    if is_audio_signal(db,p,signal):return audio_signal_view(db,p,signal)
    from .sound_candidate_watches import is_sound_signal
    sound_notification=is_sound_signal(db,p,signal)
    issue=signal.data.get('receipt_issue');basis=None
    if not issue:
        try:basis=checked(db,p,signal)
        except HTTPException as error:issue=error.detail
    if basis and not sound_notification and signal.data.get('source_task_link'):
        try:
            from .native_signal_reviews import validate_link
            validate_link(db,p,signal)
        except HTTPException as error:issue=error.detail;basis=None
    source_review=None
    if basis:
        try:source_review=event_report(db,p,scoped(db,Event,signal.event_id,p))[1]['review']
        except CHECK_ERRORS:pass
    tasks=[]
    for identity in signal.data.get('task_ids',[]):
        row=scoped(db,Task,identity,p)
        tasks.append({'id':row.id,'title':row.title,'status':row.status,'business_name':row.data.get('business_name'),'request_id':row.data.get('creation_request_id')})
    related=[]
    if basis and not sound_notification and basis.get('original_model_result',{}):
        from .native_signal_reviews import related as related_native
        related=related_native(db,p,signal)
    from .signal_routing import public_state
    dual_view={}
    if signal.data.get('dual_handoff_id'):
        from .signal_dual_models import SignalDualHandoff
        from .signal_dual_handoffs import parent_view
        dual_view={'dual_handoff':parent_view(db,p,scoped(db,SignalDualHandoff,signal.data['dual_handoff_id'],p))}
    elif signal.data.get('dual_handoff_failure'):
        dual_view={'dual_handoff_failure':signal.data['dual_handoff_failure']}
    from .signal_graph_routing_v134 import public_state as graph_route_state
    return {**dual_view,**public_state(signal),**graph_route_state(signal),'id':signal.id,'version':signal.version,'status':signal.status,'instance_id':signal.instance_id,
        'entity_id':signal.entity_id,'received_at':signal.data['received_at'],'basis_hash':signal.data.get('basis_hash'),
        'basis':basis,'source_review':source_review,'integrity_issue':issue,'note':signal.data.get('note',''),'tasks':tasks,
        'business_review':signal.data.get('business_review'),'entity_state_updated':False,
        'source_task_link':signal.data.get('source_task_link'),'linked_review_draft':signal.data.get('linked_review_draft'),
        'linked_review_requests':signal.data.get('linked_review_requests',[]),'business_review_history':signal.data.get('business_review_history',[]),'related_native_receipts':related}


def snapshot(db,p,identity,instance,release,entities,version,basis_hash):
    signal=scoped(db,PerceptionSignal,identity,p,True);check_version(signal,version)
    from .sound_candidate_watches import require_notification_only
    require_notification_only(db,p,signal)
    basis=checked(db,p,signal,allow_rejected=False)
    from .signal_dual_models import SignalDualHandoff
    from .signal_routing import source_identity
    original=scoped(db,Observation,basis['observation_id'],p)
    from .signal_graph_routing_v134 import has_source_family
    if has_source_family(db,p.workspace_id,signal.instance_id,source_identity(original)):
        raise HTTPException(409,'此原登记已有完整流程交接记录，请打开原异常及任务；不会重复建立任务')
    # A new processing version produces another Signal, not another original
    # source. The SQL ledger owns that source family even after quota is spent
    # or either original Task is cancelled; a stripped UI marker grants nothing.
    if db.scalar(select(SignalDualHandoff.id).where(SignalDualHandoff.workspace_id==p.workspace_id,
        SignalDualHandoff.instance_id==signal.instance_id,
        SignalDualHandoff.source_identity_hash==source_identity(original)).limit(1)):
        raise HTTPException(409,'此异常已安排独立响应与核验，请打开已保存的两项任务')
    if signal.instance_id!=instance.id or [signal.entity_id]!=entities or basis['release_id']!=release.id:
        raise HTTPException(409,'异常不属于本次实例、对象或当前业务版本')
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,scoped(db,Entity,signal.entity_id,p))
    if basis_hash!=signal.data['basis_hash']:raise HTTPException(409,'异常依据版本已变化')
    from .native_signal_reviews import avoid_duplicate_handoff
    avoid_duplicate_handoff(db,p,signal)
    for tid in signal.data.get('task_ids',[]):
        task=scoped(db,Task,tid,p)
        if task.status!='workflow_cancelled':raise HTTPException(409,'该异常已交给业务处理，请打开已保存任务')
    return {'signal_id':signal.id,'basis_hash':basis_hash,'basis':copy.deepcopy(basis),'note':signal.data.get('note','')}


def attach(db,p,task,snapshot):
    signal=scoped(db,PerceptionSignal,snapshot['signal_id'],p,True)
    if signal.data['basis_hash']!=snapshot['basis_hash']:raise HTTPException(409,'交接过程中异常依据发生变化')
    signal.version+=1;signal.status='handed_off'
    signal.data={**signal.data,'task_ids':[*signal.data.get('task_ids',[]),task.id]}


def finish(db,p,task,decision,note):
    from .signal_dual_handoffs import finish_task
    if finish_task(db,p,task,decision,note):return
    pinned=task.data['perception_signal'];signal=scoped(db,PerceptionSignal,pinned['signal_id'],p,True)
    checked(db,p,signal,allow_rejected=False)
    if pinned['basis_hash']!=signal.data['basis_hash'] or task.id not in signal.data.get('task_ids',[]):
        raise HTTPException(409,'任务和原异常交接不一致')
    review={'task_id':task.id,'decision':decision,'note':note,'actor':p.user_id,'at':now().isoformat(),
        'source_time_verified':False,'entity_state_updated':False,'device_action_authorized':False}
    signal.version+=1;signal.status='reviewed';signal.data={**signal.data,'business_review':review}
    task.data={**task.data,'perception_review':review}


def rule_contract(db,p,instance,release,entity,source,extractor):
    from .entity_release_compat import require_entity
    proof=require_entity(db,p,instance,release,entity)
    for cap in (source,extractor):
        if cap.data['execution'].get('side_effect') or cap.data['data_policy'].get('cloud_allowed') is not False:
            raise HTTPException(409,'此入口仅允许已登记的本地只读规则，不允许设备控制或云发送')
    if source.data['execution'].get('adapter') not in {'upload','builtin'}:
        raise HTTPException(409,'请选择人工登记来源；此入口不读取外部设备')
    if source.data['execution'].get('adapter')=='builtin' and source.data['execution'].get('handler')!='numeric_rules':
        raise HTTPException(409,'来源不是已实现的人工规则来源')
    if extractor.data['execution'].get('adapter')!='builtin' or extractor.data['execution'].get('handler')!='numeric_rules':
        raise HTTPException(409,'请选择已登记的本地数值规则')
    contract={'schema':'local-reported-rule-v1','instance_id':instance.id,'release_id':release.id,
        'entity_id':entity.id,'entity_version':entity.version,'source_hash':digest(source.data),
        'extractor_hash':digest(extractor.data)}
    if entity.schema_capability_id!=release.package_id:
        contract.update(entity_source_package_id=entity.schema_capability_id,entity_definition_hash=digest(proof))
    return contract


def guard_rule(db,p,row,*,result=None):
    from .models import Membership,User
    role=db.scalar(select(Membership.role).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id,User.active.is_(True)))
    if role not in {'admin','operator'}:raise HTTPException(403,'原登记处理账号已停用或失去写入权限')
    db.refresh(row)
    contract=row.data['reported_rule_contract']
    from .instance_api import active
    instance=scoped(db,DomainInstance,contract['instance_id'],p);db.refresh(instance)
    instance,release=active(db,p,contract['instance_id']);entity=scoped(db,Entity,row.entity_id,p);db.refresh(entity)
    if release.id!=contract['release_id']:
        raise HTTPException(409,'实例版本已变化，旧登记规则不迁移；请核对已保存登记')
    for identity in [row.source_id,row.data['extractor_id']]:db.refresh(scoped(db,Capability,identity,p))
    source=published(db,p,row.source_id,'source');extractor=published(db,p,row.data['extractor_id'])
    current=rule_contract(db,p,instance,release,entity,source,extractor)
    if current!=contract:
        raise HTTPException(409,'本地规则、对象或实例版本发生变化，请核对已保存登记')
    original=row.data['original_request']
    if digest(original)!=row.data['input_hash'] or original['attributes']!=row.data['attributes']:
        raise HTTPException(409,'原人工登记内容发生变化')
    if row.data.get('evidence_ids') or row.data.get('acquisition_id') or row.data.get('sensor_asset_id'):
        raise HTTPException(409,'人工规则作业不能处理文件、控制设备或关闭补采')
    for key in ['source_id','source_record_id','processing_version','entity_id','business_day']:
        if original[key]!=getattr(row,key):raise HTTPException(409,'原人工登记标识、对象或日期发生变化')
    from .observation_time_anchor_v135 import same_observation_instant
    try:same_observation_instant(original['observed_at'],row.observed_at)
    except (ValueError,TypeError,KeyError):raise HTTPException(409,'原人工登记时间发生变化') from None
    if result is not None:
        matches=[]
        for rule in extractor.data['specification'].get('rules',[]):
            value=row.data['attributes'].get(rule['field'])
            if isinstance(value,(float,int)) and not isinstance(value,bool) and (
                rule['operator']=='gt' and value>rule['threshold'] or rule['operator']=='lt' and value<rule['threshold']):
                matches.append({'field':rule['field'],'value':value,'rule_id':rule['id'],'severity':rule.get('severity','warning')})
        expected={'attributes':row.data['attributes'],'anomalies':matches,'method':'registered_numeric_rules'}
        if result!=expected:raise HTTPException(409,'本地规则回执与原登记或已批准阈值不一致')


def deliver_pending(workspace_id=None):
    """Recover only durable notifications belonging to the new read-only rule path."""
    from sqlalchemy.orm import Session
    from .db import engine
    from .worker import deliver_events, _workspace_uuid
    if workspace_id is not None: workspace_id = _workspace_uuid(workspace_id)
    with Session(engine()) as db:
        query=select(Observation.workspace_id,Observation.id).join(Event,
            Event.data['observation_id'].as_string()==Observation.id).where(
                Event.workspace_id==Observation.workspace_id,Event.status=='pending_delivery',
                Event.kind.in_(['observation.data','observation.failed','perception.anomaly']),
                Observation.data['reported_rule_contract'].is_not(None),
                Observation.data['reported_rule_contract']['instance_id'].as_string().is_not(None))
        if workspace_id is not None: query=query.where(Observation.workspace_id==workspace_id,Event.workspace_id==workspace_id)
        rows=list(db.execute(query.distinct().limit(100)))
    for workspace,identity in rows:deliver_events(workspace_id=workspace,observation_id=identity)

    from .signal_routing import deliver_pending as deliver_routing
    deliver_routing(workspace_id)
    from .signal_graph_routing_v134 import deliver_pending as deliver_graph_routing
    deliver_graph_routing(workspace_id)
