"""Frozen HTTP anomaly intents create approval drafts only after independent ACK.

No HTTP/model call occurs here. The independent source-family occurrence owns
one deterministic ordinary NewTask request, while the existing V175 protocol
continues to own human edits, approval, explicit start and acceptance.
"""
import copy
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
from uuid import UUID,uuid4

from fastapi import HTTPException
from sqlalchemy import select,func,or_,and_,text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from .contracts import digest
from .models import (Audit,Capability,DomainInstance,Entity,Event,InstanceRelease,Job,
    Observation,PerceptionSignal,Task,now)
from .services import audit,check_version,published,require,scoped
from .security import Principal
from .signal_approval_models_v247 import (SignalApprovalRouteV247 as Route,
    SignalApprovalOccurrenceV247 as Occurrence,SignalApprovalCommandV247 as Command)
from . import signal_routing as old
from . import signal_graph_http_source_v177 as http_source

KEY='signal_approval_routes_v247'
ORIGIN='signal_approval_origin_v247'
KIND='create_signal_approval_draft_v247'
TERMINAL={'approval_draft','linked_existing_task'}
ERROR='无法核对原始数据、接收记录或这项交接的设置，请检查相关资料；原记录已保留'


def need(value,message=ERROR):
    if not value:raise HTTPException(409,message)


def guarded(function):
    @wraps(function)
    def call(*args,**kwargs):
        from .report_text_parse_scope import report_text_parse_scope
        try:
            with report_text_parse_scope():return function(*args,**kwargs)
        except (KeyError,TypeError,ValueError,AttributeError,IndexError,OverflowError):
            raise HTTPException(409,ERROR) from None
    return call


def sealed(value,key):return {**copy.deepcopy(value),key:digest(value)}


def seal(row,value):row.data=copy.deepcopy(value);row.payload_hash=digest(value)


def payload(row):
    need(type(row.data) is dict and row.payload_hash==digest(row.data))
    return row.data


def fresh(db,model,identity,p,*,lock=False):
    query=select(model).where(model.id==identity,model.workspace_id==p.workspace_id).execution_options(populate_existing=True)
    if lock:query=query.with_for_update()
    value=db.scalar(query)
    if value is None:raise HTTPException(404,'当前范围没有这项原记录')
    return value


def evidence_row(db,model,identity,p):
    """A saved parent's missing scoped proof is damage, not a missing route.

    Public resource lookup keeps its 404 contract. This helper is only for
    internal Audit/Command IDs already reached through a scoped saved record.
    It never searches another workspace, grants access or reconstructs proof.
    """
    need(model in {Audit,Command})
    try:return fresh(db,model,identity,p)
    except HTTPException as error:
        if error.status_code==404:raise HTTPException(409,ERROR) from None
        raise


def parse_raw(operation,raw):
    from .signal_approval_contracts_v247 import COMMANDS
    need(type(raw) is str and 0<len(raw.encode('utf-8'))<=65536,'请求原文超过范围或不能读取')
    def pairs(items):
        result={}
        for key,value in items:
            need(key not in result,'请求字段不能重复')
            result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
    return COMMANDS[operation].model_validate(value).model_dump(mode='json')


def raw_hash(raw):return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@guarded
def checked_command(db,p,row):
    value=payload(row);body=parse_raw(row.operation,value['raw'])
    need(value['body']==body and row.request_id==body['request_id']
        and value['raw_sha256']==raw_hash(value['raw']) and value['body_hash']==digest(body))
    proof=evidence_row(db,Audit,value['audit_id'],p)
    expected={'command_id':row.id,'instance_id':row.instance_id,'actor_id':row.actor_id,
        'route_id':row.route_id,'operation':row.operation,'request_id':row.request_id,
        'raw_sha256':value['raw_sha256'],'body_hash':value['body_hash'],'result_hash':digest(value['result']),
        'subject_id':value['subject_id']}
    need(proof.actor_id==row.actor_id and proof.action=='signal_approval.command_v247'
        and proof.object_id==row.instance_id and proof.data==expected)
    result=value['result'];receipt=result['receipt']
    need(result['instance_id']==row.instance_id and result['route']['id']==row.route_id
        and receipt['receipt_hash']==digest({k:v for k,v in receipt.items() if k!='receipt_hash'})
        and receipt['request_id']==row.request_id and receipt['actor_id']==row.actor_id
        and receipt['route_id']==row.route_id and receipt['operation']==row.operation
        and receipt['raw_sha256']==value['raw_sha256'] and receipt['body_hash']==value['body_hash'])
    return value


@guarded
def grant(db,p,route):
    value=payload(route);g=value['grant']
    need(g['schema']=='signal-approval-grant-v247' and g['grant_hash']==digest({k:v for k,v in g.items() if k!='grant_hash'})
        and g['id']==route.id and g['workspace_id']==route.workspace_id==p.workspace_id
        and g['instance_id']==route.instance_id and g['release_id']==route.release_id and g['actor_id']==route.actor_id
        and 1<=g['valid_for_hours']<=24 and old.stamp(g['expires_at'])-old.stamp(g['enabled_at'])==timedelta(hours=g['valid_for_hours'])
        and type(value['task_ids']) is list and len(value['task_ids'])==len(set(value['task_ids']))<=g['max_tasks'])
    command=evidence_row(db,Command,value['registration_command_id'],p)
    saved=checked_command(db,p,command)
    need(command.operation=='enable' and command.route_id==route.id and command.actor_id==route.actor_id
        and saved['result']['route']['grant_hash']==g['grant_hash'])
    inventory=list(db.scalars(select(Occurrence).where(Occurrence.workspace_id==p.workspace_id,Occurrence.route_id==route.id)))
    consumed=[row for row in inventory if row.status=='approval_draft' or payload(row).get('receipt') is not None
        or row.task_id is not None and row.status!='linked_existing_task']
    task_inventory=set(db.scalars(select(Task.id).where(Task.workspace_id==p.workspace_id,
        Task.data[ORIGIN]['route_id'].as_string()==route.id)))
    need(set(value['task_ids'])=={row.task_id for row in consumed} and all(row.task_id for row in consumed),
        '已生成草稿的额度记录与真实任务不一致，请核对原任务')
    need(task_inventory==set(value['task_ids']),'草稿来源与已生成任务的记录不一致，请核对原任务')
    for row in consumed:
        item=payload(row);receipt=item['receipt'];origin=item['origin']
        proof=evidence_row(db,Audit,item['delivery_audit_id'],p)
        need(row.status=='approval_draft' and receipt['delivery_hash']==digest({k:v for k,v in receipt.items() if k!='delivery_hash'})
            and receipt['occurrence_id']==row.id and receipt['task_id']==row.task_id
            and receipt['origin_hash']==origin['origin_hash'] and proof.actor_id==route.actor_id
            and proof.action=='signal_approval.draft_created_v247' and proof.object_id==row.id and proof.data==receipt)
    pauses=list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,
        Command.route_id==route.id,Command.operation=='pause')))
    for pause in pauses:checked_command(db,p,pause)
    need((bool(pauses) and route.status=='paused' and value['paused_at'] is not None)
        or (not pauses and route.status=='active' and value['paused_at'] is None))
    return g


def status(route,at=None):
    value=payload(route);g=value['grant']
    if route.status=='paused':return 'paused'
    need(route.status=='active')
    if (at or now())>=old.stamp(g['expires_at']):return 'expired'
    if len(value['task_ids'])>=g['max_tasks']:return 'exhausted'
    return 'active'


def overlaps(left,source_id,extractor_id,entity_ids):
    return (left['source_id']==source_id and left['extractor_id']==extractor_id
        and bool(set(left['entity_ids'])&set(entity_ids)))


def _marker(entries):
    rows=[{'id':row.id,'status':row.status,'expires_at':payload(row)['grant']['expires_at'],
        'used_tasks':len(payload(row)['task_ids']),'max_tasks':payload(row)['grant']['max_tasks'],
        **{key:copy.deepcopy(payload(row)['grant'][key]) for key in ('source_id','extractor_id','entity_ids')}} for row in entries]
    return sealed({'schema':'signal-approval-lock-v247','routes':rows},'hash')


def assert_no_active(db,p,instance,source_id,extractor_id,entity_ids):
    rows=list(db.scalars(select(Route).where(Route.workspace_id==instance.workspace_id,Route.instance_id==instance.id).order_by(Route.id)))
    marker=instance.data.get(KEY)
    if marker is None and not rows:return
    for row in rows:grant(db,p,row)
    need(type(marker) is dict and set(marker)=={'schema','routes','hash'}
        and marker['schema']=='signal-approval-lock-v247'
        and marker==_marker(rows))
    need(not any(row['status']=='active' and now()<old.stamp(row['expires_at'])
        and row['used_tasks']<row['max_tasks'] and overlaps(row,source_id,extractor_id,entity_ids)
        for row in marker['routes']),
        '请先明确暂停异常审批草稿授权，再启用其它自动交接')


def sync_marker(db,instance):
    rows=list(db.scalars(select(Route).where(Route.workspace_id==instance.workspace_id,Route.instance_id==instance.id).order_by(Route.id)))
    marker=_marker(rows)
    instance.data={**instance.data,KEY:marker};instance.version+=1


def route_view(route):
    value=payload(route);g=value['grant'];binding=g['binding']
    return {'id':route.id,'release_id':route.release_id,'status':status(route),
        'source':copy.deepcopy(binding['source']),'extractor':copy.deepcopy(binding['extractor']),
        'entities':copy.deepcopy(binding['entities']),'business':copy.deepcopy(binding['target']['business']),
        'enabled_at':g['enabled_at'],'expires_at':g['expires_at'],'max_tasks':g['max_tasks'],
        'used_tasks':len(value['task_ids']),'reason':g['reason'],'grant_hash':g['grant_hash']}


def prelock_actors(db,p,release):
    identifiers={p.user_id}
    identifiers.update(binding['user_id'] for binding in release.data['snapshot'].get('roles',{}).values())
    for identity in sorted(identifiers):old.actor(db,p.workspace_id,identity)


def no_old_routes(instance,source_id,extractor_id,entity_ids):
    from .signal_dual_handoffs import active_routes as dual
    from .signal_graph_routing_v134 import active_routes as graph
    entries=graph(instance)+dual(instance)+[entry for entry in old.state(instance)['routes'] if old.route_status(entry)=='active']
    need(not any(overlaps(entry['grant'],source_id,extractor_id,entity_ids) for entry in entries),
        '请先明确暂停现有单路、双路或完整流程授权，再启用审批草稿交接')


def prepare(db,p,instance,body,*,lock_sources=True):
    from .instance_api import active
    from .entity_release_compat import require_entity
    from .signal_approval_targets_v247 import prepare as target
    _,release=active(db,p,instance.id)
    need(release.id==body['release_id'],'当前发布版本已变化，请重新核对原条件')
    for identity in sorted({body['source_id'],body['extractor_id']}):
        if lock_sources:
            db.scalar(select(Capability).where(Capability.id==identity,Capability.workspace_id==p.workspace_id)
                .with_for_update(read=True).execution_options(populate_existing=True))
    source=published(db,p,body['source_id'],'source');extractor=published(db,p,body['extractor_id'])
    source_contract=http_source.prepare(db,p,instance,release,source,extractor,body['entity_ids'])
    entities=[]
    for identity in body['entity_ids']:
        obj=fresh(db,Entity,identity,p,lock=lock_sources)
        definition=require_entity(db,p,instance,release,obj,lock_sources=lock_sources)
        entities.append({'id':obj.id,'name':next((v for v in obj.data.values() if type(v) is str and v.strip()),obj.external_id),
            'definition_hash':digest(definition)})
    pinned=target(db,p,instance,release,body['business_id'],source_contract=source_contract,
        entity_ids=body['entity_ids'],lock_sources=lock_sources)
    return {'source':old.cap_ref(source),'extractor':old.cap_ref(extractor),'entities':entities,
        'source_contract':source_contract,'target':pinned}


@guarded
def options(db,p,instance):
    from .instance_api import active
    from .entity_release_compat import require_entity
    from .signal_approval_targets_v247 import prepare as target
    _,release=active(db,p,instance.id)
    sources=[];extractors={};contracts=[]
    for pair in http_source.options(db,p,instance,release):
        source=published(db,p,pair['source']['id'],'source');extractor=published(db,p,pair['extractor']['id'])
        contract=http_source.prepare(db,p,instance,release,source,extractor,[])
        sources.append({**pair['source'],'extractor_id':extractor.id,'simulation':True,'field_verified':False})
        extractors[extractor.id]=old.cap_ref(extractor);contracts.append(contract)
    entities=[]
    for obj in db.scalars(select(Entity).where(Entity.workspace_id==p.workspace_id,Entity.namespace==instance.namespace).order_by(Entity.id).limit(101)):
        if len(entities)>=100:break
        try:definition=require_entity(db,p,instance,release,obj,lock_sources=False)
        except HTTPException:continue
        entities.append({'id':obj.id,'name':next((v for v in obj.data.values() if type(v) is str and v.strip()),obj.external_id),
            'definition_hash':digest(definition)})
    businesses=[]
    for business in release.data['snapshot']['businesses']:
        item={'id':business['id'],'name':business['name'],'execution_mode':business.get('execution_mode','unavailable'),
            'input_provider':'none','required_inputs':[],'can_create_approval_draft':False,
            'blocking_reason':'没有可核对的当前HTTP数值来源','target_hash':None,'approval_policy':None}
        for contract in contracts:
            selected=[row['id'] for row in entities if fresh(db,Entity,row['id'],p).entity_type==contract['scope']['entity_type']]
            if not selected:continue
            try:
                pin=target(db,p,instance,release,business['id'],source_contract=contract,entity_ids=selected[:20],lock_sources=False)
                item.update({key:copy.deepcopy(pin[key]) for key in ('input_provider','required_inputs','target_hash','approval_policy')})
                item.update(can_create_approval_draft=True,blocking_reason=None);break
            except HTTPException as error:
                item['blocking_reason']=str(error.detail)
        businesses.append(item)
    blocked='同一来源、规则与对象已有有效交接授权，请先暂停后重新选择'
    for source in sources:
        for entity in entities:
            try:
                no_old_routes(instance,source['id'],source['extractor_id'],[entity['id']])
                assert_no_active(db,p,instance,source['id'],source['extractor_id'],[entity['id']])
                blocked=None;break
            except HTTPException:continue
        if blocked is None:break
    if p.role not in {'admin','operator'}:blocked='当前账号只读，不能启用审批草稿交接'
    if not sources or not entities or not any(row['can_create_approval_draft'] for row in businesses):
        blocked=blocked or '需要已登记的HTTP数值来源、同范围对象及有效审批业务'
    result={'schema':'signal-approval-options-v247','instance_id':instance.id,'instance_version':instance.version,'release_id':release.id,
        'sources':sorted(sources,key=lambda x:x['id']),'extractors':sorted(extractors.values(),key=lambda x:x['id']),
        'entities':entities,'businesses':businesses,
        'limits':{'valid_for_hours_default':24,'valid_for_hours_max':24,'max_tasks_default':10,'max_tasks_max':100},
        'can_enable':blocked is None,'blocking_reason':blocked}
    return sealed(result,'options_hash')


def listing(db,p,identity):
    instance=fresh(db,DomainInstance,identity,p)
    rows=list(db.scalars(select(Route).where(Route.workspace_id==p.workspace_id,Route.instance_id==identity).order_by(Route.created_at,Route.id).limit(101)))
    need(len(rows)<=100)
    for row in rows:grant(db,p,row)
    return {'schema':'signal-approval-routes-v247','instance_id':identity,'instance_version':instance.version,'routes':[route_view(row) for row in rows]}


def _record_command(db,p,instance,route,operation,body,raw,*,subject_id=None):
    row=Command(id=str(uuid4()),workspace_id=p.workspace_id,instance_id=instance.id,actor_id=p.user_id,
        route_id=route.id,request_id=body['request_id'],operation=operation)
    proof_id=str(uuid4())
    receipt=sealed({'request_id':row.request_id,'operation':operation,'route_id':route.id,'actor_id':p.user_id,
        'body_hash':digest(body),'raw_sha256':raw_hash(raw),'accepted_instance_version':instance.version,
        'accepted_at':now().isoformat()},'receipt_hash')
    result={'schema':'signal-approval-command-result-v247','instance_id':instance.id,'instance_version':instance.version,
        'route':route_view(route),'receipt':receipt,'approval_draft_only':True,'device_actions':False,'cloud_calls':0}
    subject_id=subject_id or route.id
    seal(row,{'raw':raw,'raw_sha256':raw_hash(raw),'body':copy.deepcopy(body),'body_hash':digest(body),'result':result,
        'audit_id':proof_id,'subject_id':subject_id})
    db.add(row);db.flush()
    db.add(Audit(id=proof_id,workspace_id=p.workspace_id,actor_id=p.user_id,action='signal_approval.command_v247',object_id=instance.id,
        data={'command_id':row.id,'instance_id':instance.id,'actor_id':p.user_id,'route_id':route.id,'operation':operation,
            'request_id':row.request_id,'raw_sha256':raw_hash(raw),'body_hash':digest(body),'result_hash':digest(result),
            'subject_id':subject_id}))
    return row,result


@guarded
def command(db,p,identity,operation,body,raw,*,route_id=None,occurrence_id=None):
    require(p,'admin','operator');need(parse_raw(operation,raw)==body)
    # The original command is recovered before any new current-state choice.
    prior=db.scalar(select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==body['request_id']))
    if prior:
        saved=checked_command(db,p,prior)
        need(prior.actor_id==p.user_id and prior.instance_id==identity and prior.operation==operation
            and saved['raw']==raw and saved['body']==body
            and (operation!='pause' or prior.route_id==route_id)
            and (operation!='retry' or saved['result']['receipt'].get('operation')=='retry'
                and saved['subject_id']==occurrence_id),'原请求已保存不同内容，不能改签或换范围')
        return copy.deepcopy(saved['result'])
    if operation=='enable':
        release=fresh(db,InstanceRelease,body['release_id'],p);prelock_actors(db,p,release)
    else:old.actor(db,p.workspace_id,p.user_id)
    nonce_key=int(digest({'workspace_id':p.workspace_id,'signal_approval_request':body['request_id']})[:16],16)-(1<<63)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':nonce_key})
    instance=fresh(db,DomainInstance,identity,p,lock=True)
    # Serialize the same nonce even when two independent HTTP requests race.
    prior=db.scalar(select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==body['request_id']))
    if prior:return command(db,p,identity,operation,body,raw,route_id=route_id,occurrence_id=occurrence_id)
    if operation!='retry':check_version(instance,body['expected_version'])
    if operation=='enable':
        need(db.scalar(select(func.count()).select_from(Route).where(Route.workspace_id==p.workspace_id,Route.instance_id==identity))<100)
        no_old_routes(instance,body['source_id'],body['extractor_id'],body['entity_ids'])
        assert_no_active(db,p,instance,body['source_id'],body['extractor_id'],body['entity_ids'])
        proposed=options(db,p,instance);need(proposed['can_enable'] and body['options_hash']==proposed['options_hash'],
            '当前来源、对象、业务或审批策略已变化，请保留原请求并重新核对')
        binding=prepare(db,p,instance,body)
        clock=db.scalar(select(func.clock_timestamp()))
        route=Route(id=str(uuid4()),workspace_id=p.workspace_id,instance_id=identity,release_id=body['release_id'],actor_id=p.user_id,status='active')
        g=sealed({'schema':'signal-approval-grant-v247','id':route.id,'workspace_id':p.workspace_id,'instance_id':identity,
            'release_id':body['release_id'],'actor_id':p.user_id,'actor_role':p.role,'source_id':body['source_id'],
            'extractor_id':body['extractor_id'],'entity_ids':copy.deepcopy(body['entity_ids']),'business_id':body['business_id'],
            'enabled_at':clock.isoformat(),'expires_at':(clock+timedelta(hours=body['valid_for_hours'])).isoformat(),
            'valid_for_hours':body['valid_for_hours'],'max_tasks':body['max_tasks'],'reason':body['reason'],'binding':binding},'grant_hash')
        seal(route,{'grant':g,'task_ids':[],'paused_at':None,'registration_command_id':None})
        db.add(route);db.flush();sync_marker(db,instance)
    elif operation=='pause':
        route=fresh(db,Route,route_id,p,lock=True);need(route.instance_id==identity);grant(db,p,route)
        need(route.status=='active','此授权已暂停，原请求仍可恢复')
        route.status='paused';route.version+=1;seal(route,{**route.data,'paused_at':now().isoformat()});sync_marker(db,instance)
    elif operation=='retry':
        occurrence=fresh(db,Occurrence,occurrence_id,p,lock=True);need(occurrence.instance_id==identity)
        checked_occurrence(db,p,occurrence);check_version(occurrence,body['expected_version'])
        route=fresh(db,Route,occurrence.route_id,p,lock=True);grant(db,p,route)
        need(occurrence.status not in TERMINAL,'原任务已经保存，请打开原任务')
        queue(db,occurrence,'retry:'+body['request_id']);instance.version+=1
    else:need(False)
    row,result=_record_command(db,p,instance,route,operation,body,raw,subject_id=occurrence_id)
    if operation=='enable':seal(route,{**route.data,'registration_command_id':row.id})
    return result


def recover(db,p,identity,request_id,sha):
    fresh(db,DomainInstance,identity,p)
    row=db.scalar(select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==request_id))
    value={'schema':'signal-approval-recovery-v247','found':False,'request_id':request_id,'raw':None,
        'raw_sha256':None,'result':None,'current':None}
    if row is None:return value
    if row.actor_id!=p.user_id:raise HTTPException(403,'请由原申请账号核对原请求')
    need(row.instance_id==identity)
    saved=checked_command(db,p,row);need(saved['raw_sha256']==sha,'原请求字节摘要不一致，已保存结果保留')
    route=fresh(db,Route,row.route_id,p);grant(db,p,route)
    return {**value,'found':True,'raw':saved['raw'],'raw_sha256':saved['raw_sha256'],
        'result':copy.deepcopy(saved['result']),'current':route_view(route)}


def queue(db,occurrence,key):
    from .data_gaps import queue as add
    frozen=payload(occurrence)['intent']
    body={'occurrence_id':occurrence.id,'actor_id':occurrence.actor_id,'intent_hash':frozen['intent_hash']}
    job_key='signal-approval:'+occurrence.id+':'+key
    add(db,occurrence.workspace_id,KIND,job_key,body)
    job=db.scalar(select(Job).where(Job.workspace_id==occurrence.workspace_id,Job.idempotency_key==job_key))
    need(job is not None and job.kind==KIND and all(job.data.get(k)==v for k,v in body.items()))


def old_family(db,p,instance_id,observation):
    from .signal_graph_routing_models_v134 import SignalGraphDelivery
    from .signal_dual_models import SignalDualHandoff
    identity=old.source_identity(observation)
    for model in (SignalGraphDelivery,SignalDualHandoff):
        if db.scalar(select(model.id).where(model.workspace_id==p.workspace_id,model.instance_id==instance_id,
            model.source_identity_hash==identity).limit(1)):return True
    return False


@guarded
def freeze(db,instance,event,observation,signal,basis):
    """SQL uniqueness owns deduplication; never acquire an Actor or Task lock."""
    if event.kind!='perception.anomaly' or basis.get('schema')!='reported-anomaly-v1':return None
    identity=old.source_identity(observation)
    prior=db.scalar(select(Occurrence).where(Occurrence.workspace_id==instance.workspace_id,
        Occurrence.instance_id==instance.id,Occurrence.source_identity_hash==identity))
    if prior:return prior
    p=Principal(observation.data['submitted_by'],instance.workspace_id,'viewer','signal-approval-freeze')
    if old_family(db,p,instance.id,observation):return None
    rows=list(db.scalars(select(Route).where(Route.workspace_id==instance.workspace_id,Route.instance_id==instance.id)))
    matches=[];at=db.scalar(select(func.clock_timestamp()))
    for route in rows:
        if status(route,at)!='active':continue
        g=grant(db,p,route)
        candidate={**g,'binding':{http_source.CONTRACT_KEY:g['binding']['source_contract']}}
        if http_source.registration_matches(db,p,candidate,event,observation,basis,at):matches.append((route,g))
    if not matches:return None
    need(len(matches)==1)
    route,g=matches[0]
    need(db.scalar(select(func.count()).select_from(Occurrence).where(Occurrence.route_id==route.id))<1000,
        '本授权的异常登记已达保存上限，请暂停并核对原记录')
    row=Occurrence(id=str(uuid4()),workspace_id=instance.workspace_id,instance_id=instance.id,route_id=route.id,
        actor_id=g['actor_id'],signal_id=signal.id,event_id=event.id,observation_id=observation.id,
        source_identity_hash=identity,status='frozen')
    intent=sealed({'schema':'signal-approval-intent-v247','occurrence_id':row.id,'route_id':route.id,
        'grant_hash':g['grant_hash'],'workspace_id':instance.workspace_id,'instance_id':instance.id,
        'release_id':g['release_id'],'actor_id':g['actor_id'],'signal_id':signal.id,'event_id':event.id,
        'observation_id':observation.id,'basis_hash':signal.data['basis_hash'],'input_hash':observation.data['input_hash'],
        'source_identity_hash':identity,'observation_created_at':observation.created_at.isoformat(),
        'frozen_at':at.isoformat()},'intent_hash')
    proof_id=str(uuid4())
    seal(row,{'intent':intent,'producer_audit_id':proof_id,'reason':None,'updated_at':at.isoformat(),
        'creation_body':None,'origin':None,'receipt':None,'delivery_audit_id':None})
    inserted=db.scalar(insert(Occurrence).values(id=row.id,workspace_id=row.workspace_id,instance_id=row.instance_id,
        route_id=row.route_id,actor_id=row.actor_id,signal_id=row.signal_id,event_id=row.event_id,
        observation_id=row.observation_id,source_identity_hash=row.source_identity_hash,status=row.status,
        data=row.data,payload_hash=row.payload_hash)
        .on_conflict_do_nothing(index_elements=['workspace_id','instance_id','source_identity_hash']).returning(Occurrence.id))
    if inserted is None:
        existing=db.scalar(select(Occurrence).where(Occurrence.workspace_id==row.workspace_id,
            Occurrence.instance_id==row.instance_id,Occurrence.source_identity_hash==row.source_identity_hash))
        need(existing is not None);checked_occurrence(db,p,existing)
        return existing
    db.add(Audit(id=proof_id,workspace_id=row.workspace_id,actor_id=observation.data['submitted_by'],
        action='signal_approval.intent_frozen_v247',object_id=row.id,
        data={'intent':copy.deepcopy(intent),'intent_hash':intent['intent_hash'],'source_signal_id':signal.id}))
    queue(db,row,'initial')
    return row


@guarded
def checked_occurrence(db,p,row):
    value=payload(row);intent=value['intent']
    need(intent['schema']=='signal-approval-intent-v247'
        and intent['intent_hash']==digest({k:v for k,v in intent.items() if k!='intent_hash'})
        and all(intent[key]==getattr(row,key) for key in ('route_id','workspace_id','instance_id','actor_id','signal_id','event_id','observation_id','source_identity_hash'))
        and intent['occurrence_id']==row.id)
    proof=evidence_row(db,Audit,value['producer_audit_id'],p)
    original=fresh(db,Observation,row.observation_id,p)
    need(proof.action=='signal_approval.intent_frozen_v247' and proof.object_id==row.id
        and proof.actor_id==original.data['submitted_by']
        and proof.data=={'intent':intent,'intent_hash':intent['intent_hash'],'source_signal_id':row.signal_id})
    route=fresh(db,Route,row.route_id,p);g=grant(db,p,route)
    need(intent['grant_hash']==g['grant_hash'] and intent['release_id']==g['release_id'] and row.actor_id==g['actor_id'])
    return intent,g


def update_occurrence(db,p,row,status_value,reason):
    row.status=status_value;row.version+=1
    seal(row,{**row.data,'reason':reason,'updated_at':now().isoformat()})
    audit(db,p,'signal_approval.state_v247',row.id,{'status':status_value,'version':row.version,
        'intent_hash':row.data['intent']['intent_hash'],'state_hash':row.payload_hash})


def checked_input(db,p,row,intent,g,*,require_ack=True,lock_sources=True):
    from .perception_signals import checked
    from .instance_data_receipts import observation_receipt
    from .state_receipt_guard_v245 import check as reception_status
    signal=fresh(db,PerceptionSignal,row.signal_id,p)
    basis=checked(db,p,signal,allow_rejected=False,lock_sources=lock_sources)
    observation=fresh(db,Observation,row.observation_id,p)
    event=fresh(db,Event,row.event_id,p)
    need(signal.instance_id==row.instance_id and signal.entity_id==observation.entity_id
        and signal.data['basis_hash']==intent['basis_hash'] and intent['basis_hash']==digest(basis)
        and observation.data['input_hash']==intent['input_hash']
        and observation.created_at==old.stamp(intent['observation_created_at'])
        and old.source_identity(observation)==row.source_identity_hash)
    candidate={**g,'binding':{http_source.CONTRACT_KEY:g['binding']['source_contract']}}
    need(http_source.registration_matches(db,p,candidate,event,observation,basis,old.stamp(intent['frozen_at'])))
    origin=http_source.guard(db,p,g['binding']['source_contract'],observation,
        result=basis['extracted'],expected_origin=basis['simulation_origin'])
    pending=reception_status(db,p,observation)
    if pending is not None and pending['status']=='waiting_data':
        if require_ack:return signal,observation,basis,None
        return signal,observation,basis,None
    receipt=observation_receipt(db,p,observation).get('data_reception')
    need(type(receipt) is dict and receipt['observation_id']==observation.id
        and receipt['instance_id']==row.instance_id and receipt['release_id']==intent['release_id']
        and receipt['extracted_hash']==digest(basis['extracted']) and origin['simulation'] is True)
    return signal,observation,basis,receipt


@dataclass(frozen=True)
class _Handoff:
    secret:object
    session:object
    occurrence_id:str
    creation_hash:str


_SECRET=object()


def validate_handoff(db,p,token,body,instance,release):
    need(type(token) is _Handoff and token.secret is _SECRET and token.session is db)
    from .workspace_api import creation_body_hash
    need(token.creation_hash==creation_body_hash(body))
    row=fresh(db,Occurrence,token.occurrence_id,p)
    intent,g=checked_occurrence(db,p,row)
    need(row.task_id is None and row.instance_id==instance.id and p.user_id==row.actor_id
        and release.id==intent['release_id'] and body.release_id==release.id and body.business_id==g['business_id']
        and body.entity_ids==[fresh(db,Observation,row.observation_id,p).entity_id]
        and body.signal_id==row.signal_id and body.signal_basis_hash==intent['basis_hash']
        and body.request_id=='sig247_'+digest(row.id))
    signal,_,basis,receipt=checked_input(db,p,row,intent,g)
    need(receipt is not None and body.signal_version==signal.version)
    return {'signal_id':signal.id,'basis_hash':intent['basis_hash'],'basis':copy.deepcopy(basis),'note':signal.data.get('note','')}


def attach_task(db,p,task,token,body):
    from .workspace_api import creation_body_hash
    from .perception_signals import attach
    row=fresh(db,Occurrence,token.occurrence_id,p)
    intent,g=checked_occurrence(db,p,row)
    _,_,_,receipt=checked_input(db,p,row,intent,g)
    need(receipt is not None and task.status=='approval_draft' and task.data.get('layered_approval_v175'))
    origin=sealed({'schema':'signal-approval-task-origin-v247','occurrence_id':row.id,'route_id':row.route_id,
        'grant_hash':g['grant_hash'],'intent_hash':intent['intent_hash'],'signal_id':row.signal_id,
        'basis_hash':intent['basis_hash'],'observation_id':row.observation_id,'input_hash':intent['input_hash'],
        'receipt_hash':receipt['receipt_hash'],'creation_hash':creation_body_hash(body),
        'source_contract':copy.deepcopy(g['binding']['source_contract'])},'origin_hash')
    task.data={**task.data,ORIGIN:origin}
    attach(db,p,task,task.data['perception_signal'])
    row.task_id=task.id;row.status='approval_draft';row.version+=1
    delivery=sealed({'schema':'signal-approval-delivery-v247','occurrence_id':row.id,'intent_hash':intent['intent_hash'],
        'task_id':task.id,'creation_hash':creation_body_hash(body),'origin_hash':origin['origin_hash'],
        'receipt_hash':receipt['receipt_hash'],'created_at':now().isoformat()},'delivery_hash')
    proof_id=str(uuid4())
    seal(row,{**row.data,'origin':origin,'creation_body':body.model_dump(mode='json'),'receipt':delivery,
        'delivery_audit_id':proof_id,'reason':None,'updated_at':now().isoformat()})
    db.add(Audit(id=proof_id,workspace_id=p.workspace_id,actor_id=p.user_id,action='signal_approval.draft_created_v247',
        object_id=row.id,data=copy.deepcopy(delivery)))
    db.flush()
    # The independent consumption and the draft commit atomically. Preflight
    # and every later reader see the same complete quota/occurrence inventory.
    route=fresh(db,Route,row.route_id,p)
    need(task.id not in route.data['task_ids'] and len(route.data['task_ids'])<g['max_tasks'])
    seal(route,{**route.data,'task_ids':[*route.data['task_ids'],task.id]});route.version+=1
    sync_marker(db,fresh(db,DomainInstance,row.instance_id,p))
    db.flush()


@guarded
def checked_task_origin(db,p,task,*,lock_sources=False):
    candidates=list(db.scalars(select(Occurrence).where(Occurrence.workspace_id==p.workspace_id,Occurrence.task_id==task.id).limit(2)))
    rows=[row for row in candidates if row.status!='linked_existing_task' or payload(row).get('origin') is not None
        or payload(row).get('receipt') is not None]
    producer=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
        Audit.action=='signal_approval.draft_created_v247',Audit.data['task_id'].as_string()==task.id).limit(1))
    marker=task.data.get(ORIGIN)
    if not rows and marker is None and producer is None:return None
    need(task.workspace_id==p.workspace_id and len(rows)==1 and marker is not None)
    row=rows[0];intent,g=checked_occurrence(db,p,row);value=payload(row);origin=value['origin'];delivery=value['receipt']
    need(row.status=='approval_draft' and marker==origin and origin['schema']=='signal-approval-task-origin-v247'
        and origin['origin_hash']==digest({k:v for k,v in origin.items() if k!='origin_hash'})
        and origin['occurrence_id']==row.id and origin['route_id']==row.route_id
        and origin['intent_hash']==intent['intent_hash'] and origin['grant_hash']==g['grant_hash']
        and origin['creation_hash']==task.data['creation_hash'] and task.owner_id==row.actor_id
        and task.data['instance_id']==row.instance_id and task.data['instance_release_id']==intent['release_id']
        and task.data['business_id']==g['business_id'] and task.data['creation_request_id']=='sig247_'+digest(row.id)
        and task.data['perception_signal']['signal_id']==row.signal_id and task.data['perception_signal']['basis_hash']==intent['basis_hash'])
    from .workspace_api import NewTask,creation_body_hash
    original=NewTask.model_validate(value['creation_body'])
    need(creation_body_hash(original)==origin['creation_hash'] and original.signal_id==row.signal_id
        and original.entity_ids==task.data['entity_ids'] and origin['source_contract']==g['binding']['source_contract'])
    proof=evidence_row(db,Audit,value['delivery_audit_id'],p)
    need(delivery['delivery_hash']==digest({k:v for k,v in delivery.items() if k!='delivery_hash'})
        and delivery['task_id']==task.id and delivery['intent_hash']==intent['intent_hash']
        and delivery['origin_hash']==origin['origin_hash'] and delivery['creation_hash']==origin['creation_hash']
        and proof.action=='signal_approval.draft_created_v247' and proof.object_id==row.id
        and proof.actor_id==row.actor_id and proof.data==delivery)
    signal,observation,_,receipt=checked_input(db,p,row,intent,g,lock_sources=lock_sources)
    need(receipt is not None and receipt['receipt_hash']==origin['receipt_hash']==delivery['receipt_hash']
        and origin['observation_id']==observation.id and origin['signal_id']==signal.id
        and origin['input_hash']==intent['input_hash'] and task.id in signal.data.get('task_ids',[]))
    from .layered_approval_store_v175 import managed
    need(managed(db,p,task) is not None)
    return copy.deepcopy(origin)


@guarded
def process(db,p,job):
    initial=fresh(db,Occurrence,job.data['occurrence_id'],p)
    intent,g=checked_occurrence(db,p,initial)
    need(job.kind==KIND and job.workspace_id==initial.workspace_id and job.data['actor_id']==initial.actor_id
        and job.data['intent_hash']==intent['intent_hash'] and p.user_id==initial.actor_id)
    release=fresh(db,InstanceRelease,g['release_id'],p);prelock_actors(db,p,release)
    instance=fresh(db,DomainInstance,initial.instance_id,p,lock=True)
    route=fresh(db,Route,initial.route_id,p,lock=True)
    row=fresh(db,Occurrence,initial.id,p,lock=True)
    intent,g=checked_occurrence(db,p,row)
    if row.status in TERMINAL:
        if row.status=='approval_draft':checked_task_origin(db,p,fresh(db,Task,row.task_id,p),lock_sources=True)
        return
    try:
        with db.begin_nested():
            live=old.actor(db,p.workspace_id,g['actor_id'])
            need(live.role==g['actor_role'] and status(route)=='active','授权已暂停、过期、额度用完或原角色已变化；未新建任务')
            from .layered_approval_store_v175 import current_policy,policy_value
            original_policy=g['binding']['target']['approval_policy']
            policy=current_policy(db,live,original_policy['id'])
            version,_=policy_value(db,live,policy,current=False)
            need(policy.enabled and version.id==original_policy['version_id']
                and digest(version.data)==original_policy['hash']
                and instance.status=='partially_active' and instance.active_release_id==g['release_id']
                and g['business_id'] in instance.data.get('enabled_business_ids',[]),
                '当前业务、人员、审批策略或来源固定版本已变化，未新建任务')
            binding=prepare(db,live,instance,g)
            need(binding==g['binding'],'当前业务、人员、审批策略或来源固定版本已变化，未新建任务')
            fresh(db,PerceptionSignal,row.signal_id,p,lock=True)
            fresh(db,Observation,row.observation_id,p,lock=True)
            signal,observation,basis,receipt=checked_input(db,p,row,intent,g)
            if receipt is None:
                update_occurrence(db,p,row,'waiting_data','设备数据已分析，等待数据管理智能体接收完成；尚未生成审批草稿')
                return
            need(not old_family(db,p,instance.id,observation))
            existing=old.existing_task(db,p,signal,observation)
            if existing:
                row.task_id=existing
                update_occurrence(db,p,row,'linked_existing_task','同一原登记已有正式任务；打开原任务继续，未重复创建或扣额度')
                return
            from .workspace_api import NewTask,create_task,creation_body_hash
            body=NewTask(release_id=g['release_id'],business_id=g['business_id'],entity_ids=[signal.entity_id],
                title='待审批异常核查：'+basis['object_name'][:220],request_id='sig247_'+digest(row.id),
                signal_id=signal.id,signal_version=signal.version,signal_basis_hash=intent['basis_hash'])
            token=_Handoff(_SECRET,db,row.id,creation_body_hash(body))
            task=create_task(instance.id,body,live,db,signal_approval_handoff=token)
            need(task.status=='approval_draft' and task.data.get('layered_approval_v175'))
            need(not db.scalar(select(Job.id).where(Job.workspace_id==p.workspace_id,
                Job.data['task_id'].as_string()==task.id).limit(1)),'审批草稿不应产生业务执行任务')
            checked_task_origin(db,live,task,lock_sources=True)
    except HTTPException as error:
        # Only the attempt is rejected. Its frozen intent/request/source number
        # remain available to an explicit local retry after the cause is fixed.
        row=fresh(db,Occurrence,initial.id,p,lock=True)
        authority=error.status_code==403 or error.detail in {
            '授权已暂停、过期、额度用完或原角色已变化；未新建任务',
            '当前业务、人员、审批策略或来源固定版本已变化，未新建任务'} if type(error.detail) is str else error.status_code==403
        update_occurrence(db,p,row,'blocked_authority' if authority else 'blocked_integrity',
            str(error.detail) if type(error.detail) is str else ERROR)


def wake_for_observation(db,workspace_id,observation_id):
    """ACK callback: exact existing intents only, no Actor/parent-row lock."""
    rows=list(db.scalars(select(Occurrence).where(Occurrence.workspace_id==workspace_id,
        Occurrence.observation_id==observation_id,Occurrence.status.in_(['frozen','waiting_data'])).limit(20)))
    for row in rows:queue(db,row,'data_ack')


def failed_attempt(db,job):
    """Record a failed owned Job honestly; never grant its revoked actor work."""
    need(job.kind==KIND)
    p=Principal(job.data['actor_id'],job.workspace_id,'viewer','signal-approval-failed-attempt')
    initial=fresh(db,Occurrence,job.data['occurrence_id'],p)
    fresh(db,DomainInstance,initial.instance_id,p,lock=True)
    row=fresh(db,Occurrence,initial.id,p,lock=True)
    intent,_=checked_occurrence(db,p,row)
    need(row.actor_id==job.data['actor_id'] and intent['intent_hash']==job.data['intent_hash'])
    if row.status not in TERMINAL:
        update_occurrence(db,p,row,'blocked_authority','本次处理未完成，请检查办理账号、交接设置和相关资料；未新建任务')


def poll_once(workspace_id,*,_stop_event=None):
    """Bounded crash recovery for ACKs whose local wakeup was not consumed."""
    from .db import engine
    from .worker import _workspace_uuid
    workspace_id=_workspace_uuid(workspace_id)
    if _stop_event is not None and _stop_event.is_set():return 0
    count=0
    with Session(engine()) as db:
        initial_job=select(Job.id).where(Job.workspace_id==workspace_id,Job.kind==KIND,
            Job.idempotency_key=='signal-approval:'+Occurrence.id+':initial').exists()
        received=select(Event.id).where(Event.workspace_id==workspace_id,Event.kind=='observation.data',
            Event.data['observation_id'].as_string()==Occurrence.observation_id,Event.status=='received').exists()
        rows=list(db.scalars(select(Occurrence).where(Occurrence.workspace_id==workspace_id,
            Occurrence.status.in_(['frozen','waiting_data']),
            or_(and_(Occurrence.status=='frozen',~initial_job),received))
            .order_by(Occurrence.created_at,Occurrence.id).limit(20)))
        for row in rows:
            if _stop_event is not None and _stop_event.is_set():break
            p=Principal(row.actor_id,workspace_id,'viewer','signal-approval-recovery')
            try:
                intent,g=checked_occurrence(db,p,row)
                _,_,_,receipt=checked_input(db,p,row,intent,g,lock_sources=False)
                if receipt is not None:queue(db,row,'data_ack');count+=1
                elif row.status=='frozen':queue(db,row,'initial')
            except HTTPException:
                # Actual attempts keep the failure. Do not repair a received
                # claim, switch the original source, or acquire an Actor here.
                queue(db,row,'received_recovery')
        db.commit()
    return count


def occurrence_view(row):
    value=payload(row);receipt=value.get('receipt')
    return {'id':row.id,'version':row.version,'route_id':row.route_id,'signal_id':row.signal_id,
        'observation_id':row.observation_id,'source_identity_hash':row.source_identity_hash,
        'status':row.status,'reason':value.get('reason'),'created_at':row.created_at.isoformat(),'updated_at':value['updated_at'],
        'task_id':row.task_id,'task_path':'/api/workspace/tasks/'+row.task_id if row.task_id else None,
        'receipt_hash':receipt.get('delivery_hash') if receipt else None}


def occurrence_detail(db,p,identity,occurrence_id):
    fresh(db,DomainInstance,identity,p)
    row=fresh(db,Occurrence,occurrence_id,p);need(row.instance_id==identity)
    checked_occurrence(db,p,row)
    return occurrence_view(row)


def occurrences(db,p,identity,*,limit=20,after=None):
    fresh(db,DomainInstance,identity,p)
    query=select(Occurrence).where(Occurrence.workspace_id==p.workspace_id,Occurrence.instance_id==identity)
    if after:
        cursor=fresh(db,Occurrence,after,p);need(cursor.instance_id==identity)
        query=query.where(or_(Occurrence.created_at<cursor.created_at,and_(Occurrence.created_at==cursor.created_at,Occurrence.id<cursor.id)))
    rows=list(db.scalars(query.order_by(Occurrence.created_at.desc(),Occurrence.id.desc()).limit(limit+1)))
    for row in rows[:limit]:checked_occurrence(db,p,row)
    return {'schema':'signal-approval-occurrences-v247','instance_id':identity,'items':[occurrence_view(row) for row in rows[:limit]],
        'next_cursor':rows[limit-1].id if len(rows)>limit else None}


def task_source(db,p,task_id):
    task=fresh(db,Task,task_id,p)
    result={'schema':'signal-approval-source-view-v247','task_id':task.id,'found':False,'source':None,'integrity_issue':None}
    try:
        origin=checked_task_origin(db,p,task,lock_sources=False)
        if origin is None:return result
        row=fresh(db,Occurrence,origin['occurrence_id'],p)
        intent,g=checked_occurrence(db,p,row);_,observation,basis,receipt=checked_input(db,p,row,intent,g,lock_sources=False)
        contract=g['binding']['source_contract'];schema=contract['mapping']['field_schemas'];fields=[]
        for key,value in basis['extracted']['attributes'].items():
            need(type(value) in {int,float} and (type(value) is int or math.isfinite(value)))
            fields.append({'key':key,'label':schema[key].get('title',key),'unit':contract['field_units'][key],'value':copy.deepcopy(value)})
        source={'instance_id':row.instance_id,'signal_id':row.signal_id,'observation_id':row.observation_id,
            'source':{k:g['binding']['source'][k] for k in ('id','name')},
            'object':{'id':observation.entity_id,'name':basis['object_name']},'observed_at':basis['reported_at'],
            'source_time_verified':False,'simulation':True,'field_verified':False,'data_received':True,
            'receipt_hash':receipt['receipt_hash'],'fields':fields}
        need(type(source['observed_at']) is str and old.stamp(source['observed_at']).tzinfo is not None)
        return {**result,'found':True,'source':source}
    except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
        return {**result,'found':True,'integrity_issue':ERROR}
