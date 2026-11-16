"""Incremental MQTT daily ledgers; frozen revisions contain references, never whole-day lists."""
import base64
import json
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select, func, exists, and_, or_, case, cast, String
from sqlalchemy.orm import aliased
from .models import (MqttDailyLedger, MqttDailyEntry, MqttDailyChange, MqttDailyCheckpoint,
    MqttGateway, MqttReceipt, MqttReceiptDelivery, MqttBinding, MqttBindingRevision,
    Observation, Job, Event, Memory, DomainInstance, now)
from .contracts import digest
from .services import scoped, require, audit, enqueue_event, timestamp
from .security import Principal

COUNTERS = ('raw_total','data_received','data_pending','data_blocked','raw_needs_review',
            'processing_total','processing_succeeded','processing_pending','processing_failed','processing_blocked')
CHECK_ERRORS = (HTTPException, KeyError, ValueError, TypeError, OSError)
PAGE_LIMIT = 100
UNKNOWN_DAY = '__undated__'


def internal(workspace_id):
    return Principal('mqtt-daily-index',workspace_id,'viewer','mqtt-daily-index')


def _scope(ledger):
    return {'ledger_id':ledger.id,'workspace_id':ledger.workspace_id,
            'gateway_id':ledger.gateway_id,'day_key':ledger.day_key}


def _vector(item):
    result = {key:0 for key in COUNTERS}
    if item['kind']=='original':
        result['raw_total']=1
        result['data_received']=int(item['data_received'])
        result['data_pending']=int(item['status']=='pending')
        result['data_blocked']=int(item['status']=='blocked')
        result['raw_needs_review']=int(item['needs_review'])
    else:
        result['processing_total']=1
        result['processing_'+item['status']]=1
    return result


def _change_hash(row):
    return digest({'id':row.id,'workspace_id':row.workspace_id,'ledger_id':row.ledger_id,
        'entry_id':row.entry_id,'revision':row.revision,'item_key':row.item_key,
        'item_created_at':row.item_created_at.astimezone(timezone.utc).isoformat(),'kind':row.kind,'status':row.status,
        'instance_id':row.instance_id,'receipt_id':row.receipt_id,'data':row.data})


def checked_change(row, ledger):
    item=row.data.get('item',{})
    counts=row.data.get('counts',{})
    if (row.workspace_id!=ledger.workspace_id or row.ledger_id!=ledger.id
            or row.data.get('schema')!='mqtt-daily-change-v2' or row.data.get('scope')!=_scope(ledger)
            or row.content_hash!=_change_hash(row) or item.get('item_id')!=row.item_key
            or item.get('kind')!=row.kind or item.get('status')!=row.status
            or item.get('instance_id')!=row.instance_id or item.get('receipt_id')!=row.receipt_id
            or set(counts)!=set(COUNTERS) or any(type(value) is not int or value<0 for value in counts.values())):
        raise HTTPException(409,'每日整理的冻结修订依据不一致，请保留原件并核对')
    return deepcopy(item)


def checked_head(db,ledger):
    latest=db.scalar(select(MqttDailyChange).where(MqttDailyChange.workspace_id==ledger.workspace_id,
        MqttDailyChange.ledger_id==ledger.id).order_by(MqttDailyChange.revision.desc()).limit(1))
    return _verify_head(ledger,latest)


def _verify_head(ledger,latest):
    if not latest:
        if ledger.revision!=0:raise HTTPException(409,'每日整理的当前修订缺少原依据')
        return None
    checked_change(latest,ledger)
    if (ledger.revision!=latest.revision or ledger.data.get('change_id')!=latest.id
            or ledger.data.get('change_hash')!=latest.content_hash
            or ledger.data.get('counts')!=latest.data['counts'] or ledger.published_revision>ledger.revision):
        raise HTTPException(409,'每日整理的统计头与冻结修订不一致，请核对账本')
    return latest


def _ledger(db,p,gateway_id,day):
    # Gateway is first, including internal delivery, so capture and scope updates cannot deadlock.
    scoped(db,MqttGateway,gateway_id,p,lock=True)
    day_key=day or UNKNOWN_DAY
    ledger=db.scalar(select(MqttDailyLedger).where(MqttDailyLedger.workspace_id==p.workspace_id,
        MqttDailyLedger.gateway_id==gateway_id,MqttDailyLedger.day_key==day_key).with_for_update()
        .execution_options(populate_existing=True))
    if ledger is None:
        ledger=MqttDailyLedger(workspace_id=p.workspace_id,gateway_id=gateway_id,day_key=day_key,
            data={'schema':'mqtt-daily-ledger-v2','counts':{key:0 for key in COUNTERS}},revision=0,published_revision=0)
        db.add(ledger);db.flush()
    checked_head(db,ledger)
    return ledger


def _write(db,p,gateway_id,day,item,created_at):
    ledger=_ledger(db,p,gateway_id,day)
    latest=db.scalar(select(MqttDailyChange).where(MqttDailyChange.workspace_id==p.workspace_id,
        MqttDailyChange.ledger_id==ledger.id,MqttDailyChange.item_key==item['item_id'])
        .order_by(MqttDailyChange.revision.desc()).limit(1))
    old=None
    if latest:
        old=checked_change(latest,ledger)
        entry=scoped(db,MqttDailyEntry,latest.entry_id,p,lock=True)
        if (entry.ledger_id!=ledger.id or entry.item_key!=item['item_id']
                or entry.current_revision!=latest.revision or entry.current_hash!=latest.content_hash
                or entry.data.get('item')!=old):
            raise HTTPException(409,'每日整理的当前项目与冻结版本不一致')
        if old==item:return ledger
    else:
        entry=MqttDailyEntry(workspace_id=p.workspace_id,ledger_id=ledger.id,item_key=item['item_id'],
            current_revision=0,current_hash='0'*64,data={},created_at=created_at)
        db.add(entry);db.flush()
    counts=dict(ledger.data['counts'])
    before=_vector(old) if old else {key:0 for key in COUNTERS}
    after=_vector(item)
    for key in COUNTERS:counts[key]+=after[key]-before[key]
    if any(value<0 for value in counts.values()):raise HTTPException(409,'每日整理的增量统计不一致')
    from .models import uid
    change=MqttDailyChange(id=uid(),workspace_id=p.workspace_id,ledger_id=ledger.id,entry_id=entry.id,
        revision=ledger.revision+1,item_key=item['item_id'],item_created_at=entry.created_at,
        kind=item['kind'],status=item['status'],instance_id=item.get('instance_id'),receipt_id=item['receipt_id'],
        data={'schema':'mqtt-daily-change-v2','scope':_scope(ledger),'item':deepcopy(item),
              'before_hash':latest.content_hash if latest else None,'previous_hash':ledger.data.get('change_hash'),
              'counts':counts,'changed_at':now().isoformat()})
    change.content_hash=_change_hash(change);db.add(change);db.flush()
    entry.current_revision=change.revision;entry.current_hash=change.content_hash
    entry.data={'schema':'mqtt-daily-entry-v2','item':deepcopy(item)};entry.version+=1
    ledger.revision=change.revision;ledger.version+=1
    ledger.data={**ledger.data,'counts':counts,'change_id':change.id,'change_hash':change.content_hash}
    return ledger


def _last_item(db,p,receipt,kind,key):
    return db.scalar(select(MqttDailyChange).where(MqttDailyChange.workspace_id==p.workspace_id,
        MqttDailyChange.receipt_id==receipt.id,MqttDailyChange.kind==kind,MqttDailyChange.item_key==key)
        .order_by(MqttDailyChange.created_at.desc()).limit(1))


def original_item(db,p,receipt,*,report=None,blocked=False):
    from .mqtt_data import raw_report
    previous=_last_item(db,p,receipt,'original','original:'+receipt.id)
    try:
        report=report or raw_report(db,p,receipt)
        error=None
    except CHECK_ERRORS:
        if previous:
            ledger=scoped(db,MqttDailyLedger,previous.ledger_id,p)
            item=checked_change(previous,ledger)
        else:
            # The original intake event freezes its claimed day even if current interpretation is damaged.
            event=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,
                Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
            report=(event.data.get('report') if event else None)
            if not isinstance(report,dict) or digest(report)!=event.data.get('report_hash'):
                report={'business_day':None,'observed_at':None,'received_at':receipt.created_at.isoformat()}
            item={'item_id':'original:'+receipt.id,'kind':'original','receipt_id':receipt.id,
                  'evidence_id':receipt.evidence_id,'input_sha256':receipt.input_sha256,
                  'topic':receipt.data.get('topic',''),'received_at':report['received_at'],
                  'observed_at':report.get('observed_at'),'business_day':report.get('business_day'),
                  'record_label':'未识别数据编号','instance_id':None,'report_hash':None}
        item={**item,'status':'blocked','needs_review':True,'data_received':False,
              'error':'原件或原收件依据未通过核对，请恢复原件后重新检查','last_checked_at':now().isoformat()}
        return item
    event=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,
        Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
    received=bool(event and event.status=='received')
    status='blocked' if blocked or (event and event.status=='blocked_delivery') else 'received' if received else 'pending'
    decoded=receipt.data.get('decoded') or {}
    return {'item_id':'original:'+receipt.id,'kind':'original','instance_id':None,'receipt_id':receipt.id,
        'evidence_id':receipt.evidence_id,'input_sha256':receipt.input_sha256,'topic':report['topic'],
        'received_at':report['received_at'],'observed_at':report['observed_at'],'business_day':report['business_day'],
        'record_label':decoded.get('reported_record_id') or '未识别数据编号',
        'status':status,'data_received':received,'needs_review':bool(receipt.data['issues']) or report['business_day'] is None or blocked,
        'error':'原数据送达待恢复核对' if status=='blocked' else None,'report_hash':digest(report),
        'source_proof':digest({'packet_key':receipt.packet_key,'report':report}),
        'last_checked_at':previous.data['item']['last_checked_at'] if previous and not blocked else now().isoformat()}


def index_original(db,p,receipt,*,report=None,received=False,blocked=False,inspection=False):
    item=original_item(db,p,receipt,report=report,blocked=blocked)
    if received and not item['error']:item={**item,'status':'received','data_received':True}
    if inspection:item={**item,'last_checked_at':now().isoformat()}
    return _write(db,p,receipt.gateway_id,item['business_day'],item,receipt.created_at)


def processing_item(db,p,receipt,binding,delivery=None,observation=None,job=None,*,inspection=False):
    from .mqtt_binding_routes import checked_delivery,checked_revision,_labels
    from .mqtt_observations import binding_hash
    key='processing:'+(delivery.id if delivery else 'legacy:'+receipt.id+':'+binding.id)
    previous=_last_item(db,p,receipt,'processing',key)
    prior=checked_change(previous,scoped(db,MqttDailyLedger,previous.ledger_id,p)) if previous else None
    item={'item_id':key,'kind':'processing','receipt_id':receipt.id,'delivery_id':delivery.id if delivery else None,
        'binding_id':binding.id,'revision':getattr(binding,'revision',1),'source_id':binding.source_id,
        'entity_id':binding.entity_id,'entity_label':'待核对处理对象','entity_type_label':'待核对',
        'instance_name':'工作区待核对','rule_name':'待核对规则','instance_id':None,
        'instance_release_id':None,'observation_id':observation.id if observation else None,
        'observed_at':(receipt.data.get('decoded') or {}).get('observed_at'),
        'business_day':(receipt.data.get('decoded') or {}).get('business_day'),
        'received_at':receipt.data.get('received_at',receipt.created_at.isoformat()),'status':'pending','needs_review':False,'error':None,
        'record_label':(receipt.data.get('decoded') or {}).get('reported_record_id') or '未识别数据编号',
        'input_sha256':receipt.input_sha256,'evidence_id':receipt.evidence_id,
        'extractor_id':binding.extractor_id,'capability_hash':None,
        'deployment':'local','cloud_allowed':False,'invocation_id':observation.data.get('invocation_id') if observation else None,
        'last_checked_at':prior['last_checked_at'] if prior and not inspection else now().isoformat()}
    try:
        from .mqtt_data import raw_report
        report=raw_report(db,p,receipt)
        item['business_day']=report['business_day'];item['observed_at']=report['observed_at']
        checked_revision(db,p,binding)
        if delivery:checked_delivery(db,p,delivery,binding,receipt,execution=False)
        context=binding.data.get('instance') or {}
        item.update(_labels(db,p,binding));item['instance_id']=context.get('id')
        item['instance_release_id']=context.get('release_id');item['capability_hash']=binding.data['extractor_hash']
        if delivery and job and (job.data.get('receipt_id')!=receipt.id or job.data.get('binding_id')!=binding.id
                or job.data.get('delivery_id')!=delivery.id or job.data.get('target_hash')!=delivery.data.get('target_hash')):
            raise HTTPException(409,'固定分发任务与原目标不一致')
        if receipt.status=='quarantined' or (delivery and delivery.status=='invalidated'):
            raise HTTPException(409,'原数据存在冲突或已失效')
        if observation:
            if (observation.workspace_id!=p.workspace_id or observation.source_id!=binding.source_id
                    or observation.entity_id!=binding.entity_id or observation.data.get('mqtt_receipt_id')!=receipt.id
                    or observation.data.get('mqtt_binding_id')!=binding.id
                    or (delivery and (observation.data.get('mqtt_delivery_id')!=delivery.id
                        or observation.data.get('mqtt_delivery_target_hash')!=delivery.data.get('target_hash')))):
                raise HTTPException(409,'独立处理回执与原分发目标不一致')
            from .mqtt_observations import guard
            guard(db,p,observation,execution=False)
            if observation.status=='processed':
                from .observation_integrity import checked_report
                item['result_hash']=digest(checked_report(db,p,observation));item['status']='succeeded'
            elif observation.status=='failed':
                item['status']='failed';item['needs_review']=True
                item['error']=observation.data.get('processing_error') or '原件处理失败，需核对后恢复'
        elif job and job.status in {'failed','cancelled'}:
            item['status']='blocked';item['needs_review']=True;item['error']=job.data.get('error') or '原关联任务停止，需核对授权和版本'
        item['source_proof']=digest({'binding_hash':binding_hash(binding),'input_sha256':receipt.input_sha256,
            'delivery_target':delivery.data.get('target_hash') if delivery else None,
            'observation_hash':observation.data.get('input_hash') if observation else None})
    except CHECK_ERRORS:
        if prior:
            # Keep original history ownership; a broken current binding cannot move records into another instance.
            item=deepcopy(prior)
            item['last_checked_at']=now().isoformat() if inspection else prior['last_checked_at']
        else:
            # Invalid first targets do not grant visibility to an instance. Intake is an
            # independent frozen source for dates, never the unchecked binding/receipt.
            original=original_item(db,p,receipt)
            for field in ('business_day','observed_at','received_at','record_label'):
                item[field]=original.get(field)
            item['instance_id']=None;item['instance_release_id']=None
            item['observation_id']=None;item['invocation_id']=None;item['capability_hash']=None
            item['entity_label']='待核对处理对象';item['entity_type_label']='待核对'
            item['instance_name']='工作区待核对';item['rule_name']='待核对规则'
        item['status']='blocked';item['needs_review']=True;item['error']='原件、冻结关联或原处理回执未通过核对'
    return item


def index_delivery(db,p,delivery,*,inspection=False):
    from .mqtt_binding_routes import find_binding
    receipt=scoped(db,MqttReceipt,delivery.receipt_id,p)
    binding=find_binding(db,p,delivery.binding_id)
    observation=scoped(db,Observation,delivery.observation_id,p) if delivery.observation_id else None
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='submit_mqtt_receipt',
        Job.idempotency_key=='mqtt-submit:'+receipt.id+':'+binding.id))
    item=processing_item(db,p,receipt,binding,delivery,observation,job,inspection=inspection)
    return _write(db,p,receipt.gateway_id,item['business_day'],item,delivery.created_at)


def index_legacy(db,p,receipt,binding,*,inspection=False):
    observation=scoped(db,Observation,receipt.data['observation_id'],p) if receipt.data.get('observation_id') else None
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='submit_mqtt_receipt',
        Job.data['receipt_id'].as_string()==receipt.id,Job.data['delivery_id'].as_string().is_(None)))
    item=processing_item(db,p,receipt,binding,None,observation,job,inspection=inspection)
    return _write(db,p,receipt.gateway_id,item['business_day'],item,job.created_at if job else receipt.created_at)


def index_observation(db,p,observation):
    delivery=db.scalar(select(MqttReceiptDelivery).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
        MqttReceiptDelivery.observation_id==observation.id))
    if delivery:return index_delivery(db,p,delivery)
    from .mqtt_binding_routes import find_binding
    receipt=scoped(db,MqttReceipt,observation.data['mqtt_receipt_id'],p)
    binding=find_binding(db,p,observation.data['mqtt_binding_id'])
    return index_legacy(db,p,receipt,binding)


def publish_ledger(db,ledger):
    frozen=checked_head(db,ledger)
    if not frozen or ledger.published_revision==ledger.revision:return
    value={'schema':'mqtt-inbox-daily-v2','ledger_id':ledger.id,'gateway_id':ledger.gateway_id,
        'business_day':None if ledger.day_key==UNKNOWN_DAY else ledger.day_key,'revision':frozen.revision,
        'date_basis':'capture_time_unknown_no_day_assigned' if ledger.day_key==UNKNOWN_DAY else 'reported_capture_time',
        'revision_hash':frozen.content_hash,'change_id':frozen.id,'counts':frozen.data['counts'],
        'not_authoritative':True,'source_time_verified':False,'physical_state_verified':False}
    value['summary_hash']=digest(value)
    enqueue_event(db,internal(ledger.workspace_id),'perception.daily','mqtt-daily-v2:'+ledger.id+':'+str(frozen.revision),value)
    ledger.published_revision=ledger.revision


def publish_pending(workspace_id=None):
    from sqlalchemy.orm import Session
    from .db import engine
    with Session(engine()) as db:
        query=select(MqttDailyLedger).where(MqttDailyLedger.revision>MqttDailyLedger.published_revision)
        if workspace_id is not None:query=query.where(MqttDailyLedger.workspace_id==workspace_id)
        rows=db.scalars(query.order_by(MqttDailyLedger.created_at).with_for_update(skip_locked=True).limit(100)).all()
        for ledger in rows:publish_ledger(db,ledger)
        db.commit()


def validate_v2(db,event):
    p=internal(event.workspace_id);ledger=scoped(db,MqttDailyLedger,event.data.get('ledger_id'),p)
    frozen=scoped(db,MqttDailyChange,event.data.get('change_id'),p)
    checked_change(frozen,ledger)
    value={key:val for key,val in event.data.items() if key not in {'summary_hash','delivery_error'}}
    if (value.get('schema') not in {'mqtt-inbox-daily-v2','mqtt-daily-v2'}
            or value.get('gateway_id')!=ledger.gateway_id or value.get('revision')!=frozen.revision
            or value.get('revision_hash')!=frozen.content_hash or value.get('counts')!=frozen.data['counts']
            or value.get('business_day')!=(None if ledger.day_key==UNKNOWN_DAY else ledger.day_key)
            or digest(value)!=event.data.get('summary_hash')):
        raise HTTPException(409,'每日整理通知与原冻结修订不一致')


def _latest_query(ledger_id,workspace_id,revision):
    later=aliased(MqttDailyChange)
    return select(MqttDailyChange).where(MqttDailyChange.workspace_id==workspace_id,
        MqttDailyChange.ledger_id==ledger_id,MqttDailyChange.revision<=revision,
        ~exists(select(later.id).where(later.ledger_id==MqttDailyChange.ledger_id,
            later.item_key==MqttDailyChange.item_key,later.revision<=revision,later.revision>MqttDailyChange.revision)))


def _snapshot(db,p,identity,revision=None):
    # One SQL statement gives a coherent READ COMMITTED head + latest change;
    # frozen revision members can then be read without locking multiple days.
    latest_id=select(MqttDailyChange.id).where(MqttDailyChange.workspace_id==p.workspace_id,
        MqttDailyChange.ledger_id==MqttDailyLedger.id).order_by(MqttDailyChange.revision.desc())
    latest_id=latest_id.limit(1).correlate(MqttDailyLedger).scalar_subquery()
    pair=db.execute(select(MqttDailyLedger,MqttDailyChange).outerjoin(MqttDailyChange,MqttDailyChange.id==latest_id)
        .where(MqttDailyLedger.workspace_id==p.workspace_id,MqttDailyLedger.id==identity)
        .execution_options(populate_existing=True)).one_or_none()
    if pair is None:raise HTTPException(404,'记录不存在或不在当前工作区')
    ledger,latest=pair;_verify_head(ledger,latest)
    if not latest:raise HTTPException(404,'此日期尚无已登记的原件或独立处理')
    wanted=revision or ledger.revision
    frozen=db.scalar(select(MqttDailyChange).where(MqttDailyChange.workspace_id==p.workspace_id,
        MqttDailyChange.ledger_id==ledger.id,MqttDailyChange.revision==wanted))
    if not frozen:raise HTTPException(404,'每日整理的指定修订不存在')
    checked_change(frozen,ledger)
    return ledger,frozen


def _scoped_query(db,p,ledger,revision,instance_id=None,kind=None,status='all'):
    if instance_id:scoped(db,DomainInstance,instance_id,p)
    query=_latest_query(ledger.id,p.workspace_id,revision)
    if instance_id:
        linked=aliased(MqttDailyChange)
        own_original=exists(select(linked.id).where(linked.workspace_id==p.workspace_id,linked.ledger_id==ledger.id,
            linked.revision<=revision,linked.kind=='processing',linked.instance_id==instance_id,
            linked.receipt_id==MqttDailyChange.receipt_id))
        query=query.where(or_(and_(MqttDailyChange.kind=='processing',MqttDailyChange.instance_id==instance_id),
                              and_(MqttDailyChange.kind=='original',own_original)))
    if kind:query=query.where(MqttDailyChange.kind==kind)
    if status=='needs_review':query=query.where(MqttDailyChange.data['item']['needs_review'].as_boolean().is_(True))
    elif status!='all':query=query.where(MqttDailyChange.status==status)
    return query


def _count_scope(db,p,ledger,frozen,instance_id):
    if instance_id:_count_scope(db,p,ledger,frozen,None)
    query=_scoped_query(db,p,ledger,frozen.revision,instance_id).subquery()
    original=query.c.kind=='original';processing=query.c.kind=='processing'
    expressions={'raw_total':original,'data_received':and_(original,query.c.data['item']['data_received'].as_boolean().is_(True)),
        'data_pending':and_(original,query.c.status=='pending'),'data_blocked':and_(original,query.c.status=='blocked'),
        'raw_needs_review':and_(original,query.c.data['item']['needs_review'].as_boolean().is_(True)),
        'processing_total':processing,**{'processing_'+state:and_(processing,query.c.status==state)
            for state in ('succeeded','pending','failed','blocked')}}
    values=db.execute(select(*(func.count().filter(condition).label(key) for key,condition in expressions.items()))).one()
    counts=dict(values._mapping)
    if not instance_id:
        if counts!=frozen.data['counts']:
            raise HTTPException(409,'每日整理的完整清单与冻结统计不一致，请核对账本，不可导出为完整清单')
    else:
        # Instance membership is itself a disclosure permission. Verify all of its
        # frozen target declarations, including older versions giving raw visibility.
        members=db.scalars(select(MqttDailyChange).where(MqttDailyChange.workspace_id==p.workspace_id,
            MqttDailyChange.ledger_id==ledger.id,MqttDailyChange.revision<=frozen.revision,
            MqttDailyChange.kind=='processing',MqttDailyChange.instance_id==instance_id)
            .execution_options(yield_per=500))
        for member in members:checked_change(member,ledger)
    return counts


def checkpoint_view(row):
    if not row:return None
    return {'id':row.id,'version':row.version,'status':row.status,'purpose':row.purpose,
        'phase':row.data.get('phase'),'processed':row.data.get('processed',0),'error':row.data.get('error'),
        'revision':row.data.get('revision'),'revision_hash':row.data.get('revision_hash'),
        'updated_at':row.data.get('updated_at') or row.created_at.isoformat(),
        'checked_through':deepcopy(row.data.get('cursor')),'last_checked_at':row.data.get('completed_at')}


def checked_checkpoint_view(db,p,row):
    if not row:return None
    pass_version=case((MqttDailyCheckpoint.status.in_(['failed','completed']),MqttDailyCheckpoint.version-1),
                      else_=MqttDailyCheckpoint.version)
    key='mqtt-daily-checkpoint:'+MqttDailyCheckpoint.id+':'+cast(pass_version,String)
    pair=db.execute(select(MqttDailyCheckpoint,Job).outerjoin(Job,and_(
        Job.workspace_id==MqttDailyCheckpoint.workspace_id,Job.idempotency_key==key,Job.kind=='index_mqtt_daily'))
        .where(MqttDailyCheckpoint.workspace_id==p.workspace_id,MqttDailyCheckpoint.id==row.id)
        .execution_options(populate_existing=True)).one_or_none()
    if not pair or pair[1] is None:raise HTTPException(409,'检查进度缺少原冻结任务依据，请保留原件并核对')
    current,job=pair
    if current.status in {'queued','running'}:_check_checkpoint_job(current,job)
    elif current.status=='failed':_check_checkpoint_job(current,job,failed=True)
    elif current.status=='completed':
        proof=_checkpoint_identity(current,job);committed=job.data.get('checkpoint_committed') or {}
        if (job.data.get('checkpoint_committed_hash')!=digest(committed)
                or committed.get('checkpoint_hash')!=job.data.get('checkpoint_hash')
                or committed.get('checkpoint_id')!=current.id or committed.get('pass')!=proof['version']
                or committed.get('next_version')!=current.version or proof['version']+1!=current.version
                or committed.get('status')!='completed' or committed.get('state_hash')!=digest(current.data)):
            raise HTTPException(409,'检查完成状态与原提交回执不一致，请保留原件并核对')
    else:raise HTTPException(409,'检查进度状态不受支持，请保留原检查位置')
    return checkpoint_view(current)


def summary(db,p,identity,revision=None,instance_id=None):
    ledger,frozen=_snapshot(db,p,identity,revision);counts=_count_scope(db,p,ledger,frozen,instance_id)
    if instance_id and counts['processing_total']==0:raise HTTPException(404,'此业务实例在该修订中没有关联资料')
    gateway=scoped(db,MqttGateway,ledger.gateway_id,p)
    inspection=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
        MqttDailyCheckpoint.ledger_id==ledger.id,MqttDailyCheckpoint.purpose=='inspection')
        .order_by(MqttDailyCheckpoint.created_at.desc()).limit(1))
    backfill=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
        MqttDailyCheckpoint.gateway_id==gateway.id,MqttDailyCheckpoint.purpose=='backfill')
        .order_by(MqttDailyCheckpoint.created_at.desc()).limit(1))
    status='needs_review' if (counts['raw_needs_review'] or counts['data_blocked'] or counts['processing_failed'] or counts['processing_blocked']) else 'pending' if counts['data_pending'] or counts['processing_pending'] else 'recorded_with_limits'
    inspection_view=checked_checkpoint_view(db,p,inspection);backfill_view=checked_checkpoint_view(db,p,backfill)
    if instance_id:
        def own_progress(view):
            if not view:return None
            return {**{key:value for key,value in view.items() if key not in {'processed','checked_through'}},
                    'scope':'whole_gateway_history' if view['purpose']=='backfill' else 'whole_gateway_day',
                    'scope_description':'来源历史数据回填；不作为此实例处理数量' if view['purpose']=='backfill'
                        else '来源整日检查；不作为此实例处理数量'}
        inspection_view=own_progress(inspection_view);backfill_view=own_progress(backfill_view)
    return {'id':ledger.id,'ledger_id':ledger.id,'gateway_id':gateway.id,'gateway_name':gateway.name,
        'business_day':None if ledger.day_key==UNKNOWN_DAY else ledger.day_key,
        'date_basis':'capture_time_unknown_no_day_assigned' if ledger.day_key==UNKNOWN_DAY else 'reported_capture_time',
        'revision':frozen.revision,'current_revision':ledger.revision,'revision_hash':frozen.content_hash,
        'latest_changed':ledger.revision!=frozen.revision,'instance_id':instance_id,'counts':counts,'status':status,
        'updated_at':frozen.data['changed_at'],'not_authoritative':True,'physical_state_verified':False,
        'integrity':{'last_checked_at':inspection.data.get('completed_at') if inspection else None,
            'checked_items':None if instance_id else inspection.data.get('processed',0) if inspection else 0,
            'scope':'indexed_items_checked_at_recording','inspection':inspection_view},
        'indexing':backfill_view or {'status':'awaiting_backfill','processed':None if instance_id else 0,'error':None},
        'cost':{'status':'unknown','reason':'未接入冻结调用、实际耗时与已结算费用口径；本地计算也有运行成本'}}


def _cursor(scope,position):
    value={'scope':digest(scope),'position':position}
    return base64.urlsafe_b64encode(json.dumps({'value':value,'hash':digest(value)},separators=(',',':')).encode()).decode().rstrip('=')


def _read_cursor(token,scope):
    try:
        if len(token)>2000:raise ValueError()
        decoded=json.loads(base64.urlsafe_b64decode(token+'='*((-len(token))%4)))
        value=decoded['value']
        if decoded['hash']!=digest(value) or value['scope']!=digest(scope):raise ValueError()
        position=value['position']
        if not isinstance(position,list) or len(position)!=2 or not all(isinstance(part,str) for part in position):raise ValueError()
        timestamp(position[0])
        return position
    except (ValueError,KeyError,TypeError,UnicodeError,HTTPException):
        raise HTTPException(409,'翻页位置不属于当前日期、业务实例、修订或筛选范围，请重新读取清单')


def records(db,p,identity,revision,instance_id=None,kind='original',status='all',limit=100,after=None):
    if not 1<=limit<=PAGE_LIMIT:raise HTTPException(422,'每页最多读取100条整理记录')
    ledger,frozen=_snapshot(db,p,identity,revision)
    if kind not in {'original','processing'} or status not in {'all','pending','received','succeeded','failed','blocked','needs_review'}:
        raise HTTPException(422,'整理清单类型或状态筛选不受支持')
    scoped_counts=_count_scope(db,p,ledger,frozen,instance_id)
    if instance_id and not scoped_counts['processing_total']:raise HTTPException(404,'此业务实例在该修订中没有关联资料')
    scope={'workspace_id':p.workspace_id,'ledger_id':identity,'revision':revision,'revision_hash':frozen.content_hash,
           'instance_id':instance_id,'kind':kind,'status':status}
    query=_scoped_query(db,p,ledger,revision,instance_id,kind,status)
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    if after:
        created,key=_read_cursor(after,scope)
        query=query.where(or_(MqttDailyChange.item_created_at>timestamp(created),
            and_(MqttDailyChange.item_created_at==timestamp(created),MqttDailyChange.item_key>key)))
    found=db.scalars(query.order_by(MqttDailyChange.item_created_at,MqttDailyChange.item_key).limit(limit+1)).all()
    page=found[:limit];items=[]
    for row in page:
        item=checked_change(row,ledger)
        # Only the selected frozen history; internal target/proof digests remain implementation detail.
        items.append({'id':row.entry_id,**{key:value for key,value in item.items() if key not in {'source_proof','report_hash','result_hash'}}})
    next_cursor=_cursor(scope,[page[-1].item_created_at.isoformat(),page[-1].item_key]) if len(found)>limit else None
    return {'schema':'mqtt-daily-records-v2','ledger_id':ledger.id,'revision':revision,
        'revision_hash':frozen.content_hash,'instance_id':instance_id,'kind':kind,'status':status,
        'total':total,'records':items,'next_cursor':next_cursor}


CSV_COLUMNS=('清单类型','数据编号','数据主题','声明采集时间（中国时间）','接收时间（中国时间）',
             '声明采集日期','数据管理收讫','处理状态','业务实例','业务对象','本地规则','关联版本','需要核对','处理说明')


def _csv_text(value):
    raw='' if value is None else str(value)
    # 前导空白不能绕过表格公式防护；只修改导出显示值，原审计字段保留。
    stripped=raw.lstrip(' \t\r\n\v\f')
    return "'"+raw if (raw[:1] in {'\t','\r','\n'} or stripped[:1] in {'=','+','-','@'}) else raw


def _csv_time(value):
    if not value:return '未知'
    from zoneinfo import ZoneInfo
    try:return timestamp(value).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    except (HTTPException,ValueError,TypeError,AttributeError):return '时间格式待核'


def _csv_row(item):
    original=item['kind']=='original'
    state=('原件已送达数据管理' if item.get('data_received') else '原件送达受阻' if item['status']=='blocked'
           else '原件尚未送达数据管理') if original else {
        'succeeded':'已提取候选信息','failed':'处理失败，原件保留',
        'blocked':'当前处理受阻，历史依据保留','pending':'等待后台处理'}.get(item['status'],'处理状态待核')
    return ['原件清单' if original else '对象处理记录',item.get('record_label') or '数据编号未识别',
        item.get('topic',''),_csv_time(item.get('observed_at')),_csv_time(item.get('received_at')),
        item.get('business_day') or '采集时间待核',
        ('已收讫' if item.get('data_received') else '尚未收讫') if original else '见原件收讫记录',state,
        item.get('instance_name',''),item.get('entity_label',''),item.get('rule_name',''),
        '' if original else str(item.get('revision') or '待核'),'是' if item.get('needs_review') else '否',
        item.get('processing_error') or item.get('error') or '']


def _csv_lineage(db,p,ledger,item,raw_cache):
    from .mqtt_data import raw_report
    receipt=scoped(db,MqttReceipt,item['receipt_id'],p)
    report=raw_cache.get(receipt.id)
    if report is None:
        report=raw_report(db,p,receipt)
        if len(raw_cache)>=256:raw_cache.clear()
        raw_cache[receipt.id]=report
    if (receipt.gateway_id!=ledger.gateway_id or any(item.get(key)!=report.get(key) for key in
            ('receipt_id','evidence_id','input_sha256','observed_at','business_day','received_at'))
            or (item['kind']=='original' and item.get('topic')!=report['topic'])):
        raise HTTPException(409,'下载清单项与冻结原件血缘不一致，请保留原进度并核对')
    if item['kind']=='processing' and (item.get('source_proof') or item.get('instance_id')):
        from .mqtt_binding_routes import find_binding,checked_revision,checked_delivery
        binding=find_binding(db,p,item['binding_id']);checked_revision(db,p,binding)
        context=binding.data.get('instance') or {}
        if (binding.gateway_id!=ledger.gateway_id or binding.entity_id!=item.get('entity_id')
                or binding.source_id!=item.get('source_id') or binding.extractor_id!=item.get('extractor_id')
                or context.get('id')!=item.get('instance_id') or context.get('release_id')!=item.get('instance_release_id')
                or binding.data.get('extractor_hash')!=item.get('capability_hash')):
            raise HTTPException(409,'下载处理记录与原冻结关联目标不一致')
        if item.get('delivery_id'):
            delivery=scoped(db,MqttReceiptDelivery,item['delivery_id'],p)
            checked_delivery(db,p,delivery,binding,receipt,execution=False)
            if item.get('observation_id') and delivery.observation_id!=item['observation_id']:
                raise HTTPException(409,'下载处理记录与原独立处理回执不一致')
        if item.get('observation_id'):
            observation=scoped(db,Observation,item['observation_id'],p)
            from .mqtt_observations import guard
            guard(db,p,observation,execution=False)
            if (observation.entity_id!=binding.entity_id or observation.source_id!=binding.source_id
                    or observation.data.get('mqtt_receipt_id')!=receipt.id
                    or observation.data.get('mqtt_binding_id')!=binding.id):
                raise HTTPException(409,'下载处理记录的观测来源不一致')


def prepare_csv(db,p,identity,revision,expected_revision_hash,expected_total,instance_id=None,kind='original',status='all'):
    """完整核验后才交付文件；有界临时缓存允许大清单流式发送，禁止半份成功。"""
    import csv
    import io
    from hashlib import sha256
    from tempfile import SpooledTemporaryFile
    ledger,frozen=_snapshot(db,p,identity,revision)
    if expected_revision_hash!=frozen.content_hash:raise HTTPException(409,'下载请求不属于原固定修订，请保留原进度重新核对')
    if kind not in {'original','processing'} or status not in {'all','pending','received','succeeded','failed','blocked','needs_review'}:
        raise HTTPException(422,'下载清单类型或状态不受支持')
    counts=_count_scope(db,p,ledger,frozen,instance_id)
    if instance_id and not counts['processing_total']:raise HTTPException(404,'此业务实例在该修订中没有关联资料')
    query=_scoped_query(db,p,ledger,revision,instance_id,kind,status)
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    if total!=expected_total:raise HTTPException(409,'下载总数与原固定清单不一致，请保留原进度重新核对')
    raw_cache={}
    # 过滤外的同一授权范围成员也要核验，不能借筛选隐藏损坏的完整性依据。
    all_members=db.scalars(_scoped_query(db,p,ledger,revision,instance_id).execution_options(yield_per=500))
    for member in all_members:_csv_lineage(db,p,ledger,checked_change(member,ledger),raw_cache)
    stream=SpooledTemporaryFile(max_size=1024*1024,mode='w+b')
    digest_file=sha256();buffer=io.StringIO(newline='');writer=csv.writer(buffer,quoting=csv.QUOTE_ALL,lineterminator='\r\n')
    def emit(row):
        buffer.seek(0);buffer.truncate(0);writer.writerow([_csv_text(value) for value in row])
        encoded=buffer.getvalue().encode('utf-8');stream.write(encoded);digest_file.update(encoded)
    try:
        stream.write(b'\xef\xbb\xbf');digest_file.update(b'\xef\xbb\xbf');emit(CSV_COLUMNS)
        emitted=0
        rows=db.scalars(query.order_by(MqttDailyChange.item_created_at,MqttDailyChange.item_key).execution_options(yield_per=500))
        for row in rows:
            emit(_csv_row(checked_change(row,ledger)));emitted+=1
        if emitted!=total:raise HTTPException(409,'下载清单成员不完整，未交付部分CSV')
        _,again=_snapshot(db,p,identity,revision)
        if again.content_hash!=expected_revision_hash:raise HTTPException(409,'下载期间冻结修订依据改变，未交付CSV')
        _count_scope(db,p,ledger,again,instance_id)
        size=stream.tell();stream.seek(0)
        day='unknown' if ledger.day_key==UNKNOWN_DAY else ledger.day_key
        from datetime import date
        if day!='unknown' and date.fromisoformat(day).isoformat()!=day:raise HTTPException(409,'冻结日期格式不一致')
        scope={'workspace_id':p.workspace_id,'ledger_id':identity,'revision':revision,'revision_hash':expected_revision_hash,
               'instance_id':instance_id,'kind':kind,'status':status,'total':total}
        return {'stream':stream,'size':size,'sha256':digest_file.hexdigest(),'revision':revision,
                'revision_hash':expected_revision_hash,'total':total,'scope_hash':digest(scope),
                'filename':f'mqtt-{kind}-{day}-r{revision}.csv'}
    except BaseException:
        stream.close();raise


def original_bytes(db,p,identity,receipt_id,revision,instance_id=None):
    ledger,frozen=_snapshot(db,p,identity,revision)
    _count_scope(db,p,ledger,frozen,None)
    if instance_id:_count_scope(db,p,ledger,frozen,instance_id)
    query=_scoped_query(db,p,ledger,frozen.revision,instance_id,'original').where(MqttDailyChange.receipt_id==receipt_id)
    row=db.scalar(query)
    if not row:raise HTTPException(404,'此固定日期、修订或业务实例没有该原件')
    item=checked_change(row,ledger)
    from .evidence import evidence_bytes
    evidence,content=evidence_bytes(db,p,item['evidence_id'])
    if evidence.sha256!=item['input_sha256']:
        raise HTTPException(409,'原件字节与固定整理修订摘要不一致')
    return content,item['input_sha256']


def days(db,p,gateway_id=None,instance_id=None,limit=20,after=None,business_day=None):
    if not 1<=limit<=PAGE_LIMIT:raise HTTPException(422,'每页最多读取100个日期')
    if gateway_id:scoped(db,MqttGateway,gateway_id,p)
    if instance_id:scoped(db,DomainInstance,instance_id,p)
    scope={'workspace_id':p.workspace_id,'gateway_id':gateway_id,'instance_id':instance_id,'type':'days','business_day':business_day}
    query=select(MqttDailyLedger).where(MqttDailyLedger.workspace_id==p.workspace_id,MqttDailyLedger.revision>0)
    if gateway_id:query=query.where(MqttDailyLedger.gateway_id==gateway_id)
    if business_day:
        if business_day!='unknown':
            from datetime import date
            try:date.fromisoformat(business_day)
            except ValueError:raise HTTPException(422,'请使用实际采集日期或unknown待核日期')
        query=query.where(MqttDailyLedger.day_key== (UNKNOWN_DAY if business_day=='unknown' else business_day))
    if instance_id:query=query.where(exists(select(MqttDailyChange.id).where(MqttDailyChange.ledger_id==MqttDailyLedger.id,
        MqttDailyChange.instance_id==instance_id,MqttDailyChange.kind=='processing')))
    if after:
        created,key=_read_cursor(after,scope)
        query=query.where(or_(MqttDailyLedger.created_at<timestamp(created),
            and_(MqttDailyLedger.created_at==timestamp(created),MqttDailyLedger.id<key)))
    found=db.scalars(query.order_by(MqttDailyLedger.created_at.desc(),MqttDailyLedger.id.desc()).limit(limit+1)).all()
    page=found[:limit]
    return {'schema':'mqtt-days-v2','records':[summary(db,p,row.id,instance_id=instance_id) for row in page],
        'next_cursor':_cursor(scope,[page[-1].created_at.isoformat(),page[-1].id]) if len(found)>limit else None}


def inspection_state(db,p,identity,instance_id=None):
    ledger,frozen=_snapshot(db,p,identity)
    if instance_id:
        if not _count_scope(db,p,ledger,frozen,instance_id)['processing_total']:
            raise HTTPException(404,'此实例没有该日期的冻结关联处理')
    row=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
        MqttDailyCheckpoint.ledger_id==identity,MqttDailyCheckpoint.purpose=='inspection')
        .order_by(MqttDailyCheckpoint.created_at.desc()).limit(1))
    view=checked_checkpoint_view(db,p,row)
    if view and instance_id:
        view={key:value for key,value in view.items() if key not in {'processed','checked_through'}}
        view['scope']='whole_gateway_day';view['scope_description']='来源整日检查；不作为此实例的处理计数'
    return view


def _enqueue_checkpoint(db,row,actor_id):
    key='mqtt-daily-checkpoint:'+row.id+':'+str(row.version)
    if db.scalar(select(Job.id).where(Job.workspace_id==row.workspace_id,Job.idempotency_key==key)):return
    snapshot={'checkpoint_id':row.id,'workspace_id':row.workspace_id,'gateway_id':row.gateway_id,
        'ledger_id':row.ledger_id,'purpose':row.purpose,'version':row.version,'status':row.status,'state':deepcopy(row.data)}
    db.add(Job(workspace_id=row.workspace_id,kind='index_mqtt_daily',idempotency_key=key,
        data={'checkpoint_id':row.id,'actor_id':actor_id,'pass':row.version,'internal_scope':'mqtt-daily-index',
              'checkpoint_snapshot':snapshot,'checkpoint_hash':digest(snapshot)}))


def _checkpoint_identity(row,job):
    proof=job.data.get('checkpoint_snapshot') or {}
    if (job.kind!='index_mqtt_daily' or job.workspace_id!=row.workspace_id
            or job.data.get('checkpoint_id')!=row.id or job.data.get('pass')!=proof.get('version')
            or job.data.get('checkpoint_hash')!=digest(proof)
            or proof.get('checkpoint_id')!=row.id or proof.get('workspace_id')!=row.workspace_id
            or proof.get('gateway_id')!=row.gateway_id or proof.get('ledger_id')!=row.ledger_id
            or proof.get('purpose')!=row.purpose or job.data.get('actor_id')!=proof.get('state',{}).get('actor_id')):
        raise HTTPException(409,'检查任务与冻结来源范围不一致，请保留原检查位置')
    return proof


def _check_checkpoint_job(row,job,*,failed=False):
    proof=_checkpoint_identity(row,job)
    state=deepcopy(row.data);saved=deepcopy(proof['state'])
    if failed:
        for field in ('error','updated_at'):state.pop(field,None);saved.pop(field,None)
        valid=row.status=='failed' and row.version==proof['version']+1 and state==saved
    else:valid=row.version==proof['version'] and row.status==proof['status'] and state==saved
    if not valid:raise HTTPException(409,'检查位置与原任务冻结位置不一致，请保留原检查范围')


def _checked_failed(db,row):
    job=db.scalar(select(Job).where(Job.workspace_id==row.workspace_id,
        Job.idempotency_key=='mqtt-daily-checkpoint:'+row.id+':'+str(row.version-1)))
    if not job:raise HTTPException(409,'原失败检查缺少冻结任务位置，不能自动恢复')
    _check_checkpoint_job(row,job,failed=True)


def _watermark(db,model,filters):
    row=db.scalar(select(model).where(*filters).order_by(model.created_at.desc(),model.id.desc()).limit(1))
    return [row.created_at.isoformat(),row.id] if row else None


def _new_checkpoint(db,p,gateway,*,ledger=None,request_id=None,system_key=None,request_hash=None):
    row=MqttDailyCheckpoint(workspace_id=p.workspace_id,gateway_id=gateway.id,
        ledger_id=ledger.id if ledger else None,purpose='inspection' if ledger else 'backfill',
        request_id=request_id,system_key=system_key,status='queued',data={})
    if ledger:
        frozen=checked_head(db,ledger)
        data={'phase':'entries','revision':frozen.revision,'revision_hash':frozen.content_hash,'cursor':None}
    else:
        data={'phase':'originals','cursor':None,'watermarks':{
            'originals':_watermark(db,MqttReceipt,[MqttReceipt.workspace_id==p.workspace_id,MqttReceipt.gateway_id==gateway.id]),
            'deliveries':_watermark(db,MqttReceiptDelivery,[MqttReceiptDelivery.workspace_id==p.workspace_id,
                MqttReceiptDelivery.receipt_id.in_(select(MqttReceipt.id).where(
                    MqttReceipt.workspace_id==p.workspace_id,MqttReceipt.gateway_id==gateway.id))]),
            'legacy':_watermark(db,Job,[Job.workspace_id==p.workspace_id,Job.kind=='submit_mqtt_receipt',
                Job.data['delivery_id'].as_string().is_(None),Job.data['receipt_id'].as_string().in_(
                    select(MqttReceipt.id).where(MqttReceipt.workspace_id==p.workspace_id,
                        MqttReceipt.gateway_id==gateway.id))])}}
    row.data={**data,'processed':0,'error':None,'updated_at':now().isoformat(),'actor_id':p.user_id,
              **({'request_hash':request_hash} if request_hash else {})}
    db.add(row);db.flush();_enqueue_checkpoint(db,row,p.user_id)
    return row


def request_inspection(db,p,identity,body,instance_id=None):
    require(p,'admin')
    if instance_id:raise HTTPException(422,'业务实例页面仅查看来源检查进度；启动整日检查请从资源接入页操作')
    ledger=scoped(db,MqttDailyLedger,identity,p)
    gateway=scoped(db,MqttGateway,ledger.gateway_id,p,lock=True)
    ledger,frozen=_snapshot(db,p,identity)
    old=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
        MqttDailyCheckpoint.request_id==body['request_id']))
    request_hash=digest({'ledger_id':identity,'body':body})
    if old:
        if old.data.get('request_hash')!=request_hash or old.data.get('actor_id')!=p.user_id:
            raise HTTPException(409,'此检查请求已用于其他范围，请读取原检查进度')
        return old
    if body['action']=='retry':
        latest=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
            MqttDailyCheckpoint.ledger_id==ledger.id).order_by(MqttDailyCheckpoint.created_at.desc()).with_for_update().limit(1))
        if not latest or latest.status!='failed' or latest.version!=body.get('expected_version'):
            raise HTTPException(409,'原检查不满足恢复条件，请读取最新进度')
        _checked_failed(db,latest)
        # A new request retains the old failed checkpoint and resumes its exact frozen range.
        row=MqttDailyCheckpoint(workspace_id=p.workspace_id,gateway_id=gateway.id,ledger_id=ledger.id,
            purpose='inspection',request_id=body['request_id'],status='queued',
            data={**deepcopy(latest.data),'error':None,'actor_id':p.user_id,'request_hash':request_hash,
                  'resumes_checkpoint':latest.id,'updated_at':now().isoformat()})
        db.add(row);db.flush();_enqueue_checkpoint(db,row,p.user_id)
    else:
        running=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
            MqttDailyCheckpoint.ledger_id==ledger.id,MqttDailyCheckpoint.status.in_(['queued','running'])).limit(1))
        if running:raise HTTPException(409,'该日期已有检查正在排队或执行，请查看已有进度')
        row=_new_checkpoint(db,p,gateway,ledger=ledger,request_id=body['request_id'],request_hash=request_hash)
    audit(db,p,'mqtt.daily.inspection_requested',row.id,{'ledger_id':ledger.id,'action':body['action']})
    return row


def backfill_state(db,p,gateway_id):
    scoped(db,MqttGateway,gateway_id,p)
    row=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
        MqttDailyCheckpoint.gateway_id==gateway_id,MqttDailyCheckpoint.purpose=='backfill')
        .order_by(MqttDailyCheckpoint.created_at.desc()).limit(1))
    return checked_checkpoint_view(db,p,row)


def request_backfill(db,p,gateway_id,body):
    require(p,'admin');gateway=scoped(db,MqttGateway,gateway_id,p,lock=True)
    request_hash=digest({'gateway_id':gateway_id,'body':body})
    old=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
        MqttDailyCheckpoint.request_id==body['request_id']))
    if old:
        if old.data.get('request_hash')!=request_hash or old.data.get('actor_id')!=p.user_id:
            raise HTTPException(409,'此回填请求已用于其他范围，请读取原进度')
        return old
    latest=db.scalar(select(MqttDailyCheckpoint).where(MqttDailyCheckpoint.workspace_id==p.workspace_id,
        MqttDailyCheckpoint.gateway_id==gateway_id,MqttDailyCheckpoint.purpose=='backfill')
        .order_by(MqttDailyCheckpoint.created_at.desc()).with_for_update().limit(1))
    if body['action']=='retry':
        if not latest or latest.status!='failed' or latest.version!=body.get('expected_version'):
            raise HTTPException(409,'原回填不满足恢复条件，请读取最新进度')
        _checked_failed(db,latest)
        row=MqttDailyCheckpoint(workspace_id=p.workspace_id,gateway_id=gateway.id,purpose='backfill',
            request_id=body['request_id'],status='queued',
            data={**deepcopy(latest.data),'error':None,'actor_id':p.user_id,'request_hash':request_hash,
                  'resumes_checkpoint':latest.id,'updated_at':now().isoformat()})
        db.add(row);db.flush();_enqueue_checkpoint(db,row,p.user_id)
    else:
        if latest and latest.status in {'queued','running','failed'}:
            raise HTTPException(409,'已有回填未结束；请查看进度或恢复原检查范围')
        row=_new_checkpoint(db,p,gateway,request_id=body['request_id'],request_hash=request_hash)
    audit(db,p,'mqtt.daily.backfill_requested',row.id,{'gateway_id':gateway.id,'action':body['action']})
    return row


def _page_source(db,model,filters,cursor,watermark,batch):
    if not watermark:return []
    query=select(model).where(*filters)
    upper_date,upper_id=watermark
    query=query.where(or_(model.created_at<timestamp(upper_date),
        and_(model.created_at==timestamp(upper_date),model.id<=upper_id)))
    if cursor:
        created,key=cursor
        query=query.where(or_(model.created_at>timestamp(created),and_(model.created_at==timestamp(created),model.id>key)))
    return db.scalars(query.order_by(model.created_at,model.id).limit(batch)).all()


def _block_frozen(db,p,ledger,change):
    item=checked_change(change,ledger)
    item={**item,'status':'blocked','needs_review':True,'error':'原件、原关联或原处理回执未通过此次检查',
          'last_checked_at':now().isoformat()}
    if item['kind']=='original':item['data_received']=False
    return _write(db,p,ledger.gateway_id,None if ledger.day_key==UNKNOWN_DAY else ledger.day_key,item,change.item_created_at)


def process_checkpoint(db,p,job,*,batch=100):
    checkpoint=scoped(db,MqttDailyCheckpoint,job.data['checkpoint_id'],p)
    scoped(db,MqttGateway,checkpoint.gateway_id,p,lock=True)
    checkpoint=scoped(db,MqttDailyCheckpoint,checkpoint.id,p,lock=True)
    proof=_checkpoint_identity(checkpoint,job)
    committed=job.data.get('checkpoint_committed')
    if committed:
        if (job.data.get('checkpoint_committed_hash')!=digest(committed)
                or committed.get('checkpoint_hash')!=job.data['checkpoint_hash']
                or committed.get('checkpoint_id')!=checkpoint.id or committed.get('pass')!=proof['version']
                or committed.get('next_version')!=proof['version']+1 or checkpoint.version<committed['next_version']):
            raise HTTPException(409,'已提交批次与原任务的冻结位置不一致')
        # Batch progress and this marker committed atomically. A worker death
        # before its separate final Job status transaction must not repeat it.
        return
    _check_checkpoint_job(checkpoint,job)
    state=deepcopy(checkpoint.data);phase=state['phase'];cursor=state.get('cursor');processed=0;touched={}
    if phase=='entries':
        ledger,frozen=_snapshot(db,p,checkpoint.ledger_id,state['revision'])
        if frozen.content_hash!=state['revision_hash']:raise HTTPException(409,'原检查的冻结修订已失效')
        query=_latest_query(ledger.id,p.workspace_id,frozen.revision)
        if cursor:
            created,key=cursor
            query=query.where(or_(MqttDailyChange.item_created_at>timestamp(created),
                and_(MqttDailyChange.item_created_at==timestamp(created),MqttDailyChange.item_key>key)))
        items=db.scalars(query.order_by(MqttDailyChange.item_created_at,MqttDailyChange.item_key).limit(batch)).all()
        for change in items:
            item=checked_change(change,ledger)
            try:
                if item['kind']=='original':changed=index_original(db,p,scoped(db,MqttReceipt,item['receipt_id'],p),inspection=True)
                elif item.get('delivery_id'):changed=index_delivery(db,p,scoped(db,MqttReceiptDelivery,item['delivery_id'],p),inspection=True)
                else:
                    from .mqtt_binding_routes import find_binding
                    changed=index_legacy(db,p,scoped(db,MqttReceipt,item['receipt_id'],p),find_binding(db,p,item['binding_id']),inspection=True)
            except CHECK_ERRORS:changed=_block_frozen(db,p,ledger,change)
            touched[changed.id]=changed
            state['cursor']=[change.item_created_at.isoformat(),change.item_key];processed+=1
        if len(items)<batch:state['phase']='done'
    else:
        receipt_scope=select(MqttReceipt.id).where(MqttReceipt.workspace_id==p.workspace_id,MqttReceipt.gateway_id==checkpoint.gateway_id)
        if phase=='originals':
            model=MqttReceipt;filters=[model.workspace_id==p.workspace_id,model.gateway_id==checkpoint.gateway_id]
        elif phase=='deliveries':
            model=MqttReceiptDelivery;filters=[model.workspace_id==p.workspace_id,model.receipt_id.in_(receipt_scope)]
        elif phase=='legacy':
            model=Job;filters=[model.workspace_id==p.workspace_id,model.kind=='submit_mqtt_receipt',
                model.data['delivery_id'].as_string().is_(None),model.data['receipt_id'].as_string().in_(receipt_scope)]
        else:raise HTTPException(409,'检查阶段不受支持')
        rows=_page_source(db,model,filters,cursor,state['watermarks'][phase],batch)
        for row in rows:
            if phase=='originals':changed=index_original(db,p,row,inspection=True)
            elif phase=='deliveries':changed=index_delivery(db,p,row,inspection=True)
            else:
                from .mqtt_binding_routes import find_binding
                changed=index_legacy(db,p,scoped(db,MqttReceipt,row.data['receipt_id'],p),find_binding(db,p,row.data['binding_id']),inspection=True)
            touched[changed.id]=changed
            state['cursor']=[row.created_at.isoformat(),row.id];processed+=1
        if len(rows)<batch:
            state['phase']={'originals':'deliveries','deliveries':'legacy','legacy':'done'}[phase];state['cursor']=None
    state['processed']+=processed;state['updated_at']=now().isoformat();checkpoint.version+=1
    checkpoint.status='completed' if state['phase']=='done' else 'running'
    if checkpoint.status=='completed':state['completed_at']=now().isoformat()
    checkpoint.data=state
    if checkpoint.status!='completed':_enqueue_checkpoint(db,checkpoint,state['actor_id'])
    for ledger in touched.values():publish_ledger(db,ledger)
    committed={'checkpoint_id':checkpoint.id,'checkpoint_hash':job.data['checkpoint_hash'],
        'pass':proof['version'],'next_version':checkpoint.version,'status':checkpoint.status,
        'state_hash':digest(state)}
    job.data={**job.data,'checkpoint_committed':committed,'checkpoint_committed_hash':digest(committed)}


def fail_checkpoint(db,job):
    if job.kind!='index_mqtt_daily':return
    row=db.get(MqttDailyCheckpoint,job.data.get('checkpoint_id'))
    if row and row.workspace_id==job.workspace_id and row.status!='completed':
        try:_check_checkpoint_job(row,job)
        except CHECK_ERRORS:return
        row.status='failed';row.version+=1
        row.data={**row.data,'error':'检查中断；已保存位置和原件保留，可核对后恢复',
                  'updated_at':now().isoformat()}


def schedule_once(workspace_id=None):
    from sqlalchemy.orm import Session
    from .db import engine
    with Session(engine()) as db:
        query=select(MqttGateway).where(~exists(select(MqttDailyCheckpoint.id).where(
            MqttDailyCheckpoint.workspace_id==MqttGateway.workspace_id,
            MqttDailyCheckpoint.gateway_id==MqttGateway.id,MqttDailyCheckpoint.purpose=='backfill')))
        if workspace_id is not None:query=query.where(MqttGateway.workspace_id==workspace_id)
        gateways=db.scalars(query.order_by(MqttGateway.created_at).limit(50)).all()
        for gateway in gateways:
            p=Principal(gateway.owner_id,gateway.workspace_id,'viewer','mqtt-daily-backfill')
            scoped(db,MqttGateway,gateway.id,p,lock=True)
            if db.scalar(select(MqttDailyCheckpoint.id).where(MqttDailyCheckpoint.workspace_id==gateway.workspace_id,
                    MqttDailyCheckpoint.gateway_id==gateway.id,MqttDailyCheckpoint.purpose=='backfill').limit(1)):continue
            # This is a read/index job, not authority to receive another device packet or invoke models.
            _new_checkpoint(db,p,gateway,system_key='backfill:'+gateway.id)
        db.commit()
    if workspace_id is None:publish_pending()
    else:publish_pending(workspace_id=workspace_id)
