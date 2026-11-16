"""Workspace-scoped, user-approved periodic sensor reads and candidate inbox.

Only read-only protocol adapters run. A threshold match is an unverified clue,
not a physical incident or permission to act. Network calls hold the policy and
binding transaction locks so disabling or changing a grant cannot race a commit.
"""
from __future__ import annotations
import copy
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from .contracts import digest
from .db import engine
from .models import Job, Observation, Memory, Membership, User, Entity, now
from .security import Principal
from .services import audit, published
from .ftps_gateway_api import _lock

POLICY='sensor_auto_policy_v1'; READING='sensor_auto_reading_v1'; ALERT='sensor_auto_alert_v1'; DATA='sensor_auto_data_v1'


def policy_key(protocol, identity):return f'sensor-auto-policy:{protocol}:{identity}'

def _iso(value):return value.astimezone(timezone.utc).isoformat()

def _parse(value):
    if not isinstance(value,str):raise ValueError('invalid time')
    x=datetime.fromisoformat(value.replace('Z','+00:00'))
    if x.tzinfo is None or x.utcoffset() is None:raise ValueError('timezone missing')
    return x.astimezone(timezone.utc)

def _membership(db,workspace,actor):
    return db.scalar(select(Membership.id).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==workspace,Membership.user_id==actor,Membership.role=='admin',User.active.is_(True)).with_for_update(read=True,of=[Membership,User])) is not None

def _source(db,p,protocol,identity,expected_version=None):
    if protocol=='modbus':
        from . import modbus_gateway_api as api,modbus_binding as binding
        conn=api._find(db,p,identity,api._CONNECTION); link=binding._binding(db,p,identity)
        if expected_version is not None and conn.version!=expected_version:raise HTTPException(409,'连接版本已变化')
        grant,_=api._authorized(p.workspace_id,conn.data,db)
    elif protocol=='opcua':
        from . import opcua_gateway_api as api
        conn=api._connection(db,p,identity,expected_version);link=api._binding(db,p,identity)
        grant=api._grant(p.workspace_id,conn.data['grant_id'],db)
    else:raise HTTPException(422,'设备协议不受支持')
    if not link:raise HTTPException(409,'请先确认业务对象和数值字段关联')
    b=link.data;source=published(db,p,b['source_id'],'source')
    if protocol=='modbus':entity,package,spec=binding._validate(db,p,conn,b['entity_id'],b['field'])
    else:entity,package,spec=api._field(db,p,conn,b['entity_id'],b['field'])
    if (b.get('connection_version')!=conn.version or b.get('source_hash')!=digest(source.data)
        or b.get('package_id')!=package.id or b.get('package_hash')!=digest(package.data) or b.get('field_schema')!=spec):
        raise HTTPException(409,'连接、来源或业务字段已变化，请重新关联')
    return conn,link,grant

def connection_options(db,p,page=1,page_size=50):
    rows=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind.in_(['modbus_connection_v1','opcua_connection_v1']),Job.status=='internal').order_by(Job.created_at.desc(),Job.id.desc()).offset((page-1)*page_size).limit(page_size+1)).all()
    has_more=len(rows)>page_size;rows=rows[:page_size]
    result=[]
    for row in rows:
        protocol='modbus' if row.kind=='modbus_connection_v1' else 'opcua'
        try:
            conn,link,_=_source(db,p,protocol,row.id); available=True;reason=None
        except HTTPException as e:
            link=None;available=False;reason=str(e.detail)
        pol=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==policy_key(protocol,row.id),Job.kind==POLICY,Job.status=='internal'))
        result.append({'protocol':protocol,'connection_id':row.id,'connection_version':row.version,'name':row.data.get('name','传感器'),
                       'available':available,'reason':reason,'field':link.data.get('field') if link else None,
                       'unit':link.data.get('unit') if link else row.data.get('unit_label',''),
                       'policy':policy_view(pol) if pol else None})
    return result,has_more

def policy_view(row):
    return {'id':row.id,'version':row.version,**copy.deepcopy(row.data)}

def _reading(db,p,policy,conn,link,grant,when):
    protocol=policy.data['protocol']; key='sensor-auto-reading:'+policy.id+':'+str(policy.data['generation'])+':'+policy.data['next_due']
    old=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key))
    if old:raise HTTPException(409,'本次采集已有记录，请核对原记录')
    if protocol=='modbus':
        from .modbus_readonly_transport import read_registers,decode_registers
        config=conn.data; count=1 if config['value_type'].endswith('16') else 2
        values=read_registers(grant['host'],grant['port'],grant['unit'],config['function'],config['address'],count)
        value=decode_registers(values,config['value_type'],config['word_order'],config['scale'],config['offset'])
        data={'connection_id':conn.id,'version':conn.version,'actor_id':p.user_id,'state':'succeeded','value':value,
              'register_values':values,'unit_label':config['unit_label'],'read_at':_iso(now()),'quality':'unverified',
              'time_note':'采集时间为服务器读取时间，设备未提供观测时间','interpretation':copy.deepcopy(config),'automatic':True}
        row=Job(workspace_id=p.workspace_id,kind='modbus_reading_v1',status='internal',idempotency_key=key,data=data);db.add(row);db.flush()
        from .modbus_binding import save_observation
        observation=save_observation(db,p,conn,row)
    else:
        from .opcua_readonly_transport import read_value
        from . import opcua_gateway_api as api
        value=read_value(grant)
        data={'connection_id':conn.id,'version':conn.version,'actor_id':p.user_id,'state':'succeeded',**value,'unit':conn.data['unit_label'],'automatic':True}
        row=Job(workspace_id=p.workspace_id,kind='opcua_reading_v1',status='internal',idempotency_key=key,data=data);db.add(row);db.flush()
        b=link.data;entity,package,spec=api._field(db,p,conn,b['entity_id'],b['field']);source=published(db,p,b['source_id'],'source')
        if b['package_hash']!=digest(package.data) or b['source_hash']!=digest(source.data) or b['field_schema']!=spec:raise HTTPException(409,'对象字段或来源已变化')
        val=value['value'];eligible=value['quality']=='good' and val is not None and (spec.get('type')!='integer' or float(val).is_integer())
        converted=int(val) if eligible and spec.get('type')=='integer' else val
        eligible=eligible and Draft202012Validator(spec).is_valid(converted)
        attrs={b['field']:converted} if eligible else {}
        observation=Observation(workspace_id=p.workspace_id,source_id=source.id,source_record_id='opcua:'+row.id,processing_version='1.0.0',entity_id=entity.id,observed_at=None,business_day=None,status='extracted_unclocked',data={'schema':'opcua-unverified-observation-v1','submitted_by':p.user_id,'candidate_only':True,'source_reported_timestamp':value['source_timestamp'],'server_reported_timestamp':value['server_timestamp'],'received_at':value['received_at'],'reading_id':row.id,'quality':value['quality'],'status_code':value['status_code'],'unit':b['unit'],'extracted':{'attributes':attrs,'anomalies':[]},'evidence_ids':[]})
        db.add(observation);db.flush();audit(db,p,'opcua.observation.saved',observation.id)
    if observation is None:raise HTTPException(409,'业务对象关联已不可用')
    row.data={**row.data,'observation_id':observation.id};db.flush()
    return row,observation

def _freshness(protocol,reading,now_at,max_age):
    if protocol=='modbus':return 'unknown'
    stamp=reading.data.get('source_timestamp')
    if not stamp:return 'unknown'
    try:delta=(now_at-_parse(stamp)).total_seconds()
    except (ValueError,TypeError):return 'unknown'
    return 'future' if delta < -300 else 'stale' if delta>max_age else 'reported_recent'

def _publish(db,p,policy,conn,link,reading,observation,when):
    protocol=policy.data['protocol'];quality=reading.data.get('quality','unverified');value=reading.data.get('value');freshness=_freshness(protocol,reading,when,policy.data['max_age_seconds'])
    entity=db.scalar(select(Entity).where(Entity.workspace_id==p.workspace_id,Entity.id==observation.entity_id))
    if not entity:raise HTTPException(409,'业务对象已不可用')
    payload={'schema':'sensor-auto-data-v1','policy_id':policy.id,'policy_generation':policy.data['generation'],'configuration_request_id':policy.data['configuration_request_id'],'connection_id':conn.id,
             'protocol':protocol,'entity_id':observation.entity_id,'source_id':observation.source_id,'observation_id':observation.id,
             'reading_id':reading.id,'value':value,'unit':link.data['unit'],'field':link.data['field'],'quality':quality,'freshness':freshness,
             'device_name':conn.data.get('name') or '传感器','object_name':entity.data.get('name') or entity.external_id,
             'field_name':link.data.get('field_schema',{}).get('title') or link.data['field'],
             'source_timestamp':reading.data.get('source_timestamp'),'received_at':reading.data.get('read_at') or reading.data.get('received_at')}
    payload['evidence_hash']=digest(payload)
    memory=Memory(workspace_id=p.workspace_id,status='candidate',text='自动采集读数已归集；设备时间和质量需核查',data={**payload,'type':DATA});db.add(memory);db.flush()
    threshold=policy.data.get('threshold');match=False
    valid_attributes=observation.data.get('extracted',{}).get('attributes',{})
    eligible=bool(threshold and value is not None and valid_attributes.get(link.data['field'])==value
                  and quality in {'good','unverified'} and freshness not in {'stale','future'})
    if eligible:match=value>threshold['value'] if threshold['operator']=='above' else value<threshold['value']
    current=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==ALERT,Job.status=='internal',
        Job.data['policy_id'].as_string()==policy.id,Job.data['policy_generation'].as_integer()==policy.data['generation'],
        Job.data['status'].as_string().in_(['unconfirmed','acknowledged'])).order_by(Job.created_at.desc()).limit(1).with_for_update())
    if match:
        if current:
            # Do not flood the inbox while the same configured condition stays true.
            current.data={**current.data,'last_seen_at':payload['received_at'],'repeat_count':current.data.get('repeat_count',1)+1};current.version+=1
        else:
            data={**payload,'threshold':copy.deepcopy(threshold),'status':'unconfirmed','message':'读数触及配置阈值，为待核实线索，不代表现场结论','acknowledged_by':None,'acknowledged_at':None,'last_seen_at':payload['received_at'],'repeat_count':1}
            alert=Job(workspace_id=p.workspace_id,kind=ALERT,status='internal',idempotency_key='sensor-auto-alert:'+observation.id,data=data);db.add(alert);db.flush()
            db.add(Memory(workspace_id=p.workspace_id,status='candidate',text='传感器阈值线索待核查',data={'schema':'sensor-auto-alert-v1','type':ALERT,'alert_id':alert.id,'observation_id':observation.id,'evidence_hash':payload['evidence_hash']}))
            audit(db,p,'sensor.auto.clue',alert.id,{'observation_id':observation.id,'quality':quality,'freshness':freshness})
    elif eligible and current:
        current.data={**current.data,'status':'no_longer_matching','last_seen_at':payload['received_at'],
                      'message':'后续读数未再达到阈值；不代表现场风险已解除'};current.version+=1
    return payload

def _one(db,workspace,identity,at):
    initial=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind==POLICY,Job.status=='internal'))
    if not initial:return False
    lock_key='sensor-auto:'+str(initial.data.get('protocol'))+':'+str(initial.data.get('connection_id'))
    _lock(db,workspace,lock_key)
    policy=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind==POLICY,Job.status=='internal').with_for_update().execution_options(populate_existing=True))
    if not policy or policy.data.get('enabled') is not True or _parse(policy.data['next_due'])>at:return False
    d=copy.deepcopy(policy.data);actor=d['approved_by'];p=Principal(actor,workspace,'admin','sensor-auto-worker')
    if not _membership(db,workspace,actor):
        policy.data={**d,'enabled':False,'state':'paused','last_error':'授权人已失去管理员权限，自动采集已暂停'};policy.version+=1;db.commit();return True
    try:
        if d['protocol']=='modbus':
            from .managed_modbus_grants import lock_workspace
            lock_workspace(db,workspace,shared=True)
        elif d['protocol']=='opcua':
            from .managed_opcua_grants import lock_workspace
            lock_workspace(db,workspace,shared=True)
        conn,link,grant=_source(db,p,d['protocol'],d['connection_id'],d['connection_version'])
        _lock(db,workspace,d['protocol']+'-binding:'+conn.id)
        if link.id!=d['binding_id'] or digest(link.data)!=d['binding_hash']:raise HTTPException(409,'对象关联已变化')
        # Same transaction lock serializes configuration and pause through the read.
        row,obs=_reading(db,p,policy,conn,link,grant,at)
        if obs is not None:
            _source(db,p,d['protocol'],d['connection_id'],d['connection_version'])
            _publish(db,p,policy,conn,link,row,obs,at)
        policy.data={**d,'next_due':_iso(at+timedelta(seconds=d['interval_seconds'])),'state':'active','last_error':None,'last_read_at':_iso(at),'last_reading_id':row.id}
        policy.version+=1;audit(db,p,'sensor.auto.read',policy.id,{'reading_id':row.id,'observation_id':obs.id if obs else None});db.commit()
    except Exception as exc:
        db.rollback()
        # Reacquire the policy after rollback; do not retry a paid/action request.
        _lock(db,workspace,lock_key)
        policy=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind==POLICY,Job.status=='internal').with_for_update())
        if policy and policy.data.get('enabled') is True and policy.data.get('generation')==d['generation']:
            policy.data={**policy.data,'next_due':_iso(at+timedelta(seconds=d['interval_seconds'])),'state':'error',
                         'last_error':'自动读取未完成，请检查设备连接、授权、关联和证书；下次按计划再检查','last_error_at':_iso(at),'last_error_type':type(exc).__name__}
            policy.version+=1;audit(db,p,'sensor.auto.read_failed',policy.id,{'error_type':type(exc).__name__});db.commit()
    return True

def tick(workspace_id,*,limit=4,at=None,_stop_event=None):
    if str(UUID(workspace_id))!=workspace_id or type(limit) is not int or not 1<=limit<=16:raise ValueError('invalid workspace or limit')
    at=at or now()
    if at.tzinfo is None:raise ValueError('timezone required')
    with Session(engine()) as db:
        # Values are canonical UTC ISO-8601 strings written by this module.
        # Filter and order by due time before applying the scan limit: otherwise
        # many future policies can permanently starve a later due connection.
        due=Job.data['next_due'].as_string()
        rows=list(db.scalars(select(Job.id).where(Job.workspace_id==workspace_id,Job.kind==POLICY,Job.status=='internal',Job.data['enabled'].as_boolean()==True,due<=_iso(at)).order_by(due,Job.created_at,Job.id).limit(100)))
    processed=0
    for identity in rows:
        if processed>=limit or (_stop_event is not None and _stop_event.is_set()):break
        with Session(engine(),expire_on_commit=False) as db:
            if _one(db,workspace_id,identity,at):processed+=1
    return processed

def start_workspace_collector(workspace_id,stop_event):
    import threading
    if str(UUID(workspace_id))!=workspace_id:raise ValueError('invalid workspace')
    def run():
        while not stop_event.is_set():
            try:tick(workspace_id,_stop_event=stop_event)
            except Exception as e:print('传感器自动采集暂不可用：'+type(e).__name__,flush=True)
            if stop_event.wait(5):break
    thread=threading.Thread(target=run,name='sensor-auto-'+workspace_id[:8],daemon=True);thread.start();return thread
