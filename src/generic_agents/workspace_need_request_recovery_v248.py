"""Recover one actor's exact saved need, without another data query or action.

New creations have an independent typed-body receipt. Historical creations
retain their actual Job/context/Audit evidence level; no read upgrades history,
invents HTTP bytes, or turns a missing lookup into proof of non-execution.
"""
import copy
import re
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import Audit,DataRun,DomainInstance,Entity,InstanceRelease,Job,uid
from .services import scoped

SCHEMA='workspace-data-need-request-recovery-v248'
PROTOCOL='workspace-data-need-creation-v248'
ACTION='workspace.need_request_saved_v248'
MARKER='workspace_need_receipt_v248'
RECEIPT_SCHEMA='workspace-data-need-original-v248'
MARKER_SCHEMA='workspace-data-need-original-ref-v248'
ERROR='无法核对这份原申请的内容、接收记录或资料范围；原申请已保留，请检查相关资料'


def creation_metadata():
    """Unconditional current producer version, never a public command flag."""
    return {'creation_protocol':PROTOCOL}


def need(value,message=ERROR):
    if not value:raise HTTPException(409,message)


def closed(function):
    @wraps(function)
    def call(*args,**kwargs):
        try:return function(*args,**kwargs)
        except (KeyError,TypeError,ValueError,AttributeError,IndexError,OverflowError):
            raise HTTPException(409,ERROR) from None
    return call


def reference(db,model,identity,p):
    try:return scoped(db,model,identity,p)
    except HTTPException as error:
        if error.status_code==404:raise HTTPException(409,ERROR) from None
        raise


def _base(db,p,instance,row):
    from .workspace_needs import context,KIND
    from .entity_release_compat import require_entity
    c=context(row);request=row.data['request']
    need(row.workspace_id==p.workspace_id==instance.workspace_id and row.owner_id==p.user_id==c['actor_id']
        and type(c['instance_id']) is str and bool(c['instance_id']) and type(c['request_id']) is str
        and type(c['body_hash']) is str and re.fullmatch(r'[a-f0-9]{64}',c['body_hash']) is not None
        and row.question==request['question'] and request['mode']=='structured'
        and request['entity_ids']==[c['entity_id']] and type(request['fields']) is list
        and 1<=len(request['fields'])<=30 and len(set(request['fields']))==len(request['fields'])
        and all(type(field) is str and field for field in request['fields'])
        and set(c['field_labels'])==set(request['fields']) and c['existing_records_only'] is True)
    jobs=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,
        Job.idempotency_key=='workspace-need:'+row.id+':initial').limit(2)))
    need(len(jobs)==1)
    job=jobs[0]
    need(job.kind==KIND and job.data.get('query_id')==row.id and job.data.get('actor_id')==row.owner_id
        and job.data.get('context_hash')==row.data['workspace_need_context_hash']==digest(c)
        and job.data.get('existing_records_only') is True and c['query_request_hash']==digest(request))
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action=='workspace.need_created',Audit.object_id==row.id).limit(2)))
    need(len(audits)==1)
    audit=audits[0]
    need(audit.actor_id==row.owner_id and digest(audit.data)==digest({
        'existing_records_only':True,'field_count':len(request['fields'])}))
    # Only an independently checked original scope can be classified as an
    # external instance mismatch. A damaged context is an integrity failure.
    if c['instance_id']!=instance.id:raise HTTPException(404,'当前账号和实例没有这项原申请')
    release=reference(db,InstanceRelease,c['release_id'],p)
    entity=reference(db,Entity,c['entity_id'],p)
    need(release.instance_id==instance.id and release.status=='prepared'
        and any(b['id']==c['business_id'] for b in release.data['snapshot']['businesses']))
    # This verifies full package/type/lineage without current activation, field
    # inference, an observation scan or a new permission to query that data.
    # Saved dynamic observation fields need not be domain schema properties.
    try:definition=require_entity(db,p,instance,release,entity,lock_sources=False)
    except HTTPException as error:
        if error.status_code==404:raise HTTPException(409,ERROR) from None
        raise
    if 'object_definition_hash' in c:need(c['object_definition_hash']==digest(definition))
    else:need(entity.schema_capability_id==release.package_id)
    return c,job,audit


def _body_scope(body,c,request):
    need(body['request_id']==c['request_id'] and digest(body)==c['body_hash']
        and all(body[key]==c[key] for key in ('release_id','business_id','entity_id'))
        and body['purpose']==request['question'] and digest(body['fields'])==digest(request['fields']))
    requirement=request['evidence_requirement']
    need(requirement['source']==body['source'])
    if body['source']=='archive':
        need(requirement['minimum_verification']=='recorded' and requirement['time']['mode']=='archive')
    else:
        need(requirement['minimum_verification']==body['confirmation'] and requirement['time']['mode']=='latest'
            and type(requirement['time']['max_age_seconds']) is int
            and requirement['time']['max_age_seconds']==body['max_age_hours']*3600)


@closed
def save_original(db,p,row,body):
    """Only the new create branch calls this, before its one original commit."""
    from .workspace_needs import NeedCreate
    instance=reference(db,DomainInstance,row.data['workspace_need_context']['instance_id'],p)
    c,job,audit=_base(db,p,instance,row)
    need(c.get('creation_protocol')==PROTOCOL and MARKER not in row.data)
    old=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==ACTION,Audit.object_id==row.id).limit(2)))
    need(not old)
    original=body.model_dump(mode='json')
    need(digest(NeedCreate.model_validate(original).model_dump(mode='json'))==digest(original))
    _body_scope(original,c,row.data['request'])
    value={'schema':RECEIPT_SCHEMA,'query_id':row.id,'workspace_id':p.workspace_id,'actor_id':p.user_id,
        'instance_id':instance.id,'request_id':c['request_id'],'typed_body':copy.deepcopy(original),
        'body_hash':c['body_hash'],'context_hash':digest(c),'query_request_hash':c['query_request_hash'],
        'creation_audit_id':audit.id,'initial_job_id':job.id}
    value={**value,'receipt_hash':digest(value)};identity=uid()
    db.add(Audit(id=identity,workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,object_id=row.id,data=value))
    row.data={**row.data,MARKER:{'schema':MARKER_SCHEMA,'audit_id':identity,'receipt_hash':value['receipt_hash']}}


@closed
def lookup(db,p,instance_id,request_id,expected_body_hash):
    # No active() or current options lookup: paused/version-changed instances
    # retain their original request and do not gain new submission authority.
    instance=scoped(db,DomainInstance,instance_id,p)
    need(type(request_id) is str and 12<=len(request_id)<=100
        and type(expected_body_hash) is str and re.fullmatch(r'[a-f0-9]{64}',expected_body_hash) is not None)
    result={'schema':SCHEMA,'instance_id':instance_id,'request_id':request_id,'body_hash':expected_body_hash,
        'found':False,'request':None,'query_path':None,'read_only':True,'device_actions':False,'cloud_calls':0}
    rows=list(db.scalars(select(DataRun).where(DataRun.workspace_id==p.workspace_id,
        DataRun.data['workspace_need_context']['request_id'].as_string()==request_id).limit(2)))
    need(len(rows)<=1,'同一原请求存在多条记录，请保留原申请并核对，未选择其中一条')
    if not rows:return result
    row=rows[0];raw_context=row.data.get('workspace_need_context')
    if row.owner_id!=p.user_id:
        need(not (type(raw_context) is dict and raw_context.get('actor_id')==p.user_id))
        raise HTTPException(404,'当前账号和实例没有这项原申请')
    c,job,audit=_base(db,p,instance,row)
    need(c['request_id']==request_id and c['body_hash']==expected_body_hash,
        '原申请内容摘要不一致，请保留原文核对，未采用这项结果')
    records=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==ACTION,Audit.object_id==row.id).limit(2)))
    need(len(records)<=1)
    marker=row.data.get(MARKER)
    modern='creation_protocol' in c or MARKER in row.data or bool(records)
    level='legacy_job_context';typed_body=None;receipt_hash=None;receipt_audit_id=None
    if modern:
        from .workspace_needs import NeedCreate
        need(c.get('creation_protocol')==PROTOCOL and len(records)==1 and type(marker) is dict
            and set(marker)=={'schema','audit_id','receipt_hash'} and marker['schema']==MARKER_SCHEMA)
        record=records[0];value=record.data
        keys={'schema','query_id','workspace_id','actor_id','instance_id','request_id','typed_body','body_hash',
            'context_hash','query_request_hash','creation_audit_id','initial_job_id','receipt_hash'}
        need(type(value) is dict and set(value)==keys and value['schema']==RECEIPT_SCHEMA
            and record.actor_id==row.owner_id and marker['audit_id']==record.id
            and marker['receipt_hash']==value['receipt_hash']==digest({k:v for k,v in value.items() if k!='receipt_hash'})
            and value['query_id']==row.id and value['workspace_id']==p.workspace_id and value['actor_id']==row.owner_id
            and value['instance_id']==instance_id and value['request_id']==request_id
            and value['body_hash']==expected_body_hash and value['context_hash']==digest(c)
            and value['query_request_hash']==c['query_request_hash']
            and value['creation_audit_id']==audit.id and value['initial_job_id']==job.id)
        original=NeedCreate.model_validate(value['typed_body']).model_dump(mode='json')
        need(digest(original)==digest(value['typed_body']))
        _body_scope(original,c,row.data['request'])
        level='typed_body_receipt';typed_body=copy.deepcopy(original)
        receipt_hash=value['receipt_hash'];receipt_audit_id=record.id
    proof={'query_id':row.id,'query_version':row.version,'actor_id':row.owner_id,
        'instance_id':instance_id,'release_id':c['release_id'],'business_id':c['business_id'],'entity_id':c['entity_id'],
        'request_id':request_id,'body_hash':c['body_hash'],'context_hash':digest(c),'query_request_hash':c['query_request_hash'],
        'evidence_level':level,'typed_body':typed_body,'receipt_hash':receipt_hash,
        'creation_audit_id':audit.id,'initial_job_id':job.id,'typed_receipt_audit_id':receipt_audit_id}
    return {**result,'found':True,'request':proof,
        'query_path':'/api/workspace/instances/'+instance_id+'/data-needs/'+row.id}
