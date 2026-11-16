"""Every original reaches data management, including unbound and undated inputs."""
from sqlalchemy import select
from fastapi import HTTPException
from .contracts import digest
from .models import MqttReceipt, Memory, now
from .evidence import evidence_bytes
from .mqtt_messages import SensorFields, decode_sensor_message
from .services import scoped, enqueue_event
from .security import Principal


def raw_report(db,p,row):
    snapshot=row.data['config_snapshot']
    if digest(snapshot)!=row.data['config_hash']:
        raise HTTPException(409,'原接入配置摘要不一致')
    expected_key=digest({'config_hash':row.data['config_hash'],'version':row.data['gateway_version'],
                         'topic':row.data['topic'],'sha256':row.input_sha256})
    evidence,content=evidence_bytes(db,p,row.evidence_id)
    if (row.packet_key!=expected_key or evidence.sha256!=row.input_sha256
        or evidence.data.get('gateway_id')!=row.gateway_id or evidence.data.get('topic')!=row.data['topic']
        or evidence.data.get('modality')!='sensor_packet'):
        raise HTTPException(409,'原收件与原件归属不一致')
    try:decoded=decode_sensor_message(content,snapshot['mapping'],SensorFields(**snapshot['fields']))
    except ValueError:decoded=None
    if decoded!=row.data['decoded']:
        raise HTTPException(409,'原报文与保存的解释不一致')
    if row.source_record_id!=(decoded['source_record_id'] if decoded else None):
        raise HTTPException(409,'原报文数据编号不一致')
    return {'schema':'mqtt-raw-packet-v1','receipt_id':row.id,'gateway_id':row.gateway_id,
            'evidence_id':row.evidence_id,'input_sha256':row.input_sha256,'topic':row.data['topic'],
            'received_at':row.data['received_at'],'observed_at':decoded['observed_at'] if decoded else None,
            'business_day':decoded['business_day'] if decoded else None,
            'capture_time_unknown':not decoded or not decoded['observed_at'],
            'intake_issues':row.data.get('intake_issues',row.data['issues']),
            'config_hash':row.data['config_hash'],'source_time_verified':False,'physical_state_verified':False,
            'not_authoritative':True,'date_basis':'reported_capture_time_or_unknown'}


def enqueue_packet(db,p,receipt):
    report=raw_report(db,p,receipt)
    event=enqueue_event(db,p,'perception.sensor_packet','mqtt-packet:'+receipt.id,
        {'schema':'mqtt-packet-delivery-v1','receipt_id':receipt.id,'gateway_id':receipt.gateway_id,
         'report':report,'report_hash':digest(report)})
    from .mqtt_daily import index_original
    index_original(db,p,receipt,report=report)
    return event


def summary_report(db,p,gateway_id,day,*,ids=None):
    query=select(MqttReceipt).where(MqttReceipt.workspace_id==p.workspace_id,MqttReceipt.gateway_id==gateway_id)
    if day is None:query=query.where(MqttReceipt.data['decoded']['business_day'].as_string().is_(None))
    else:query=query.where(MqttReceipt.data['decoded']['business_day'].as_string()==day)
    if ids is not None:query=query.where(MqttReceipt.id.in_(ids))
    rows=db.scalars(query.order_by(MqttReceipt.id).limit(1001)).all()
    truncated=len(rows)>1000;rows=rows[:1000]
    reports=[];originals=[];unverifiable=[]
    for row in rows:
        try:report=raw_report(db,p,row)
        except (HTTPException,KeyError,ValueError,TypeError,OSError):
            unverifiable.append(row.id)
            originals.append({'receipt_id':row.id,'input_sha256':row.input_sha256,
                              'evidence_id':row.evidence_id,'report_hash':None,'verified':False})
        else:
            reports.append(report)
            originals.append({'receipt_id':row.id,'input_sha256':row.input_sha256,
                              'evidence_id':row.evidence_id,'report_hash':digest(report),'verified':True})
    return {'schema':'mqtt-inbox-daily-v1','gateway_id':gateway_id,'business_day':day,
            'date_basis':'reported_capture_time' if day else 'capture_time_unknown_no_day_assigned',
            'classification_basis':'original_intake','unique_original_ids':[row.id for row in rows],
            'originals':originals,'intake_review_ids':[r['receipt_id'] for r in reports if r['intake_issues']],
            'unverifiable_receipt_ids':unverifiable,
            'truncated':truncated,'not_authoritative':True,'source_time_verified':False,
            'status':'needs_review' if truncated or unverifiable or day is None or any(r['intake_issues'] for r in reports) else 'originals_received'}


def receive_packet(db,event):
    p=Principal('sensor-data-receiver',event.workspace_id,'viewer','sensor-data-receiver')
    receipt=scoped(db,MqttReceipt,event.data['receipt_id'],p)
    report=raw_report(db,p,receipt)
    if event.data['report']!=report or event.data['report_hash']!=digest(report):
        raise HTTPException(409,'数据管理接收回执与原收件不一致')
    db.add(Memory(workspace_id=event.workspace_id,status='candidate',
        text='传感原件已保存并接收，采集时间待核对' if report['capture_time_unknown'] else '传感原件已接收，仍须核对设备与业务字段',
        data={**report,'event_id':event.id,'type':'sensor_original','current_issues':receipt.data['issues']}))
    from .mqtt_daily import index_original
    index_original(db,p,receipt,report=report,received=True)
    # Reception stays authoritative even if registering this independent
    # quality diagnostic fails. Its worker can claim only after ACK commit.
    conflicts=[x for x in (receipt.data.get('decoded') or {}).get('issues',[])
        if isinstance(x,str) and x.startswith('unit_conflict:')]
    if conflicts:
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError
        from .mqtt_unit_quality_dispatch_v357 import enqueue
        db.flush()
        try:
            with db.begin_nested():
                previous_lock=db.scalar(text("SELECT current_setting('lock_timeout')"))
                previous_statement=db.scalar(text("SELECT current_setting('statement_timeout')"))
                db.execute(text("SELECT set_config('lock_timeout', '250ms', true)"))
                db.execute(text("SELECT set_config('statement_timeout', '1000ms', true)"))
                job=enqueue(db,event,receipt)
                if job is None:raise ValueError('原单位异常未形成诊断请求')
                db.execute(text("SELECT set_config('lock_timeout', :value, true)"),{'value':previous_lock})
                db.execute(text("SELECT set_config('statement_timeout', :value, true)"),{'value':previous_statement})
        except (HTTPException,SQLAlchemyError,ValueError,KeyError,TypeError,AttributeError,OSError):
            event.data={**event.data,'unit_quality_enqueue':{'state':'blocked','reason':'diagnostic_enqueue_unavailable'}}
        else:
            event.data={**event.data,'unit_quality_enqueue':{'state':'queued'}}


def validate_summary(db,event):
    p=Principal('sensor-data-receiver',event.workspace_id,'viewer','sensor-data-receiver')
    report={k:v for k,v in event.data.items() if k not in {'summary_hash','delivery_error'}}
    if digest(report)!=event.data.get('summary_hash'):
        raise HTTPException(409,'传感原件整理摘要不一致')
    original_ids=report['unique_original_ids']
    if len(original_ids)>1000 or len(set(original_ids))!=len(original_ids):
        raise HTTPException(409,'传感原件整理清单无效')
    verified=summary_report(db,p,report['gateway_id'],report['business_day'],ids=original_ids)
    # The original snapshot may have been capped. Verify its entries, retaining its cap warning.
    verified['truncated']=report['truncated']
    if report['truncated']:verified['status']='needs_review'
    if verified!=report:raise HTTPException(409,'整理清单与原件不一致')


def deliver_pending(workspace_id=None):
    from .worker import deliver_events
    from .mqtt_daily import publish_pending
    scope={} if workspace_id is None else {'workspace_id':workspace_id}
    deliver_events(event_kind='perception.sensor_packet',**scope)
    publish_pending(**scope)
    deliver_events(event_kind='perception.daily',event_schema='mqtt-inbox-daily-v1',**scope)
    deliver_events(event_kind='perception.daily',event_schema='mqtt-inbox-daily-v2',**scope)
