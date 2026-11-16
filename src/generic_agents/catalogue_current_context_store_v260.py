"""M1: explicitly preserve a saved catalogue under a current configuration.

Historical authoring proofs and fresh source qualification are separate. This
module never creates a model Job/grant or confirms a current Plan.
"""
import asyncio
import copy
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC
from threading import current_thread

from fastapi import HTTPException
from sqlalchemy import select,text
from . import models as m
from .contracts import digest
from .relations import lock_current_actor
from .security import require
from . import report_business_catalogue_store_v211 as cat
from . import report_design_saved_source_v174 as source
from .catalogue_current_context_contracts_v260 import Fork,DraftRef,ConfigurationBasis,parse,identity,sha,managed,working_identity,child_request
from .catalogue_current_context_models_v260 import CatalogueCurrentContextBindingV260 as Binding,CatalogueCurrentContextCommandV260 as Command

PREFIX='/api/setup/report-business-catalogues/v260/current-contexts'
COMMAND_ACTION='report.catalogue_current_command_v260'
BOUND_ACTION='report.catalogue_current_bound_v260'
LINK_ACTION='report.catalogue_current_children_v260'
CONFIRM_ACTION='report.catalogue_current_confirmed_v260'
LIMIT=200
ERROR='当前配置复用目录、原人稿或独立保存依据无法核对；原记录保留'
REASONS={'draft_changed':'原目录已保存新版本，请核对最新人稿后另拟申请',
    'configuration_changed':'工作区配置已变化，原人稿保留，请核对当前版本',
    'source_unavailable':'原报告、完整来源或当前读取资格暂不能核对；未重新分析',
    'already_forked':'同一目录及当前配置已有工作副本，请从历史打开原申请',
    'binding_changed':'原绑定或原保存依据不能完整核对',
    'already_confirmed':'该工作副本已确认，不能覆盖原办理范围',
    'not_eligible':'当前账号或原目录不允许本次复用',
    'preparation_failed':'工作副本未完整保存，原申请和人稿保留'}
_PENDING=ContextVar('catalogue_current_pending_fork_v260',default=None)
_TOKEN=object()


def need(value,message=ERROR,status=409):
    if not value:raise HTTPException(status,message)


def _owner():
    try:task=asyncio.current_task()
    except RuntimeError:task=None
    return current_thread(),task


def _principal(p):return (p.user_id,p.workspace_id,p.session_id,p.role)


def _pending(db,p,draft_id):
    value=_PENDING.get()
    if value is None:return None
    need(value['token'] is _TOKEN and value['db'] is db and value['principal']==_principal(p)
        and value['owner']==_owner() and value['working_id']==draft_id)
    return value


@contextmanager
def _pending_fork(db,p,value):
    need(_PENDING.get() is None,'目录复用事务不能重入')
    state={**value,'token':_TOKEN,'db':db,'principal':_principal(p),'owner':_owner()}
    token=_PENDING.set(state)
    try:yield
    finally:_PENDING.reset(token)


def trusted_create_id(db,p,server_id,raw):
    identity(server_id);need(managed(server_id))
    value=_pending(db,p,server_id)
    need(value is not None and value['stage']=='create' and hashlib.sha256(raw).hexdigest()==value['create_sha'])
    body=cat.parse_wire(raw,'create')
    need(body['request_id']==value['create_nonce'] and body['consumer_id']==value['consumer_id'])
    return server_id


def _audit(db,p,action,object_id,data):
    row=m.Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=action,object_id=object_id,data=copy.deepcopy(data))
    db.add(row);db.flush();return row


def _row(db,p,cls,identity_value,*,lock=False,internal=False):
    statement=select(cls).where(cls.workspace_id==p.workspace_id,cls.id==identity_value)
    if lock:statement=statement.with_for_update()
    row=db.scalar(statement.execution_options(populate_existing=True))
    need(row is not None,ERROR if internal else '没有当前账号范围内的目录资料',409 if internal else 404)
    return row


def _ref(row):
    return {'id':row.id,'workspace_id':row.workspace_id,'actor_id':row.actor_id,'version':row.version,
        'record_hash':row.record_hash,'origin_hash':row.origin_hash,'document_hash':row.document_hash}


def _at(reference,by_draft,by_command,p):
    ref=DraftRef.model_validate(reference).model_dump();row=by_draft.get(ref['id'])
    need(row is not None and row.workspace_id==p.workspace_id==ref['workspace_id']
        and row.actor_id==p.user_id==ref['actor_id'])
    commands=[c for c in by_command.values() if c.draft_id==row.id and c.draft_version==ref['version']]
    need(len(commands)==1);command=commands[0];state=command.data['state']
    actual={'id':row.id,'workspace_id':row.workspace_id,'actor_id':row.actor_id,'version':command.draft_version,
        'record_hash':digest(state),'origin_hash':state['origin_hash'],'document_hash':state['document_hash']}
    need(cat.same(actual,ref) and digest(command.data['document'])==ref['document_hash'])
    return row,copy.deepcopy(command.data['document'])


def _pins(row,p):
    origin=row.data['origin'];metadata,_=cat._check_origin(origin,p)
    snapshot=origin['source_context']['snapshot']
    return {'producer_id':metadata['producer_id'],'consumer_id':metadata['consumer_id'],'source_revision':metadata['revision'],
        'origin_hash':row.origin_hash,'source_context_hash':digest(origin['source_context']),
        'source_snapshot_hash':metadata['source_snapshot_hash'],'indexed_inputs_hash':metadata['indexed_inputs_hash'],
        'input_claims_hash':metadata['input_claims_hash'],'input_count':metadata['input_count'],
        'materials_hash':digest(snapshot['materials']),
        'map_proofs_hash':digest({key:snapshot[key] for key in ('map_proofs','map_calls','map_job')}),
        'capture_proof_hash':digest(origin['capture_proof'])}


def _child_ref(command):
    return {'command_id':command.id,'request_id':command.request_id,'raw_sha256':command.wire_sha256,
        'state_hash':digest(command.data['state'])}


def _command_origin(row):
    return {'schema':'catalogue-current-context-command-record-v260','id':row.id,'version':row.version,
        'workspace_id':row.workspace_id,'actor_id':row.actor_id,'request_id':row.request_id,
        'source_draft_id':row.source_draft_id,'binding_id':row.binding_id,'operation':row.operation,'outcome':row.outcome,
        'path':row.path,'raw_sha256':row.raw_sha256,'body_hash':row.body_hash,'created_at':row.created_at.astimezone(UTC).isoformat(),
        'exact_body':row.exact_body,'receipt':copy.deepcopy(row.data['receipt'])}


def _command_meta(db,p,row,by_draft,by_command):
    need(row.workspace_id==p.workspace_id and row.actor_id==p.user_id and row.version==1
        and type(row.data) is dict and set(row.data)=={'receipt'})
    # M1 never writes a confirm. A later confirmed command is an explicit,
    # read-only boundary until its real current-plan verifier is installed.
    need(row.operation=='fork','后续目录确认记录须由对应版本核验，本版本未放行写入')
    body,original=parse(row.exact_body.encode('utf-8'))
    need(row.path==PREFIX+'/forks' and row.request_id==body.request_id and row.source_draft_id==body.source_draft_ref.id
        and row.raw_sha256==hashlib.sha256(original.encode()).hexdigest() and row.body_hash==digest(body.model_dump()))
    if row.outcome=='accepted':_at(body.source_draft_ref.model_dump(),by_draft,by_command,p)
    else:
        original_parent=by_draft.get(row.source_draft_id)
        need(original_parent is not None and original_parent.actor_id==p.user_id
            and body.source_draft_ref.workspace_id==p.workspace_id and body.source_draft_ref.actor_id==p.user_id)
    receipt=row.data['receipt'];core={k:v for k,v in receipt.items() if k!='record_hash'}
    need(set(receipt)=={'schema','id','version','workspace_id','actor_id','request_id','operation','raw_sha256','body_hash','created_at',
        'outcome','binding_id','source_draft_ref','working_draft_ref','target_configuration','plan_ref','rejection','record_hash'}
        and receipt['schema']=='catalogue-current-context-receipt-v260' and receipt['record_hash']==digest(core)
        and all(receipt[key]==getattr(row,key) for key in ('id','version','workspace_id','actor_id','request_id','operation','raw_sha256','body_hash','outcome','binding_id'))
        and receipt['created_at']==row.created_at.astimezone(UTC).isoformat()
        and cat.same(receipt['source_draft_ref'],body.source_draft_ref.model_dump())
        and cat.same(receipt['target_configuration'],body.target_configuration.model_dump()) and receipt['plan_ref'] is None)
    need(row.record_hash==digest(_command_origin(row)))
    anchors=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==COMMAND_ACTION,m.Audit.object_id==row.id).limit(2)))
    need(len(anchors)==1 and anchors[0].actor_id==p.user_id and cat.same(anchors[0].data,_command_origin(row)))
    if row.outcome=='accepted':need(row.binding_id is not None and receipt['working_draft_ref'] is not None and receipt['rejection'] is None)
    else:need(row.outcome=='rejected' and row.binding_id is None and receipt['working_draft_ref'] is None
        and type(receipt['rejection']) is dict and set(receipt['rejection'])=={'code','reason'}
        and receipt['rejection']['code'] in REASONS and receipt['rejection']['reason']==REASONS[receipt['rejection']['code']])
    return row


def _binding_meta(db,p,row,by_draft,by_command,commands,bindings,seen=None):
    seen=set() if seen is None else set(seen)
    need(row.id not in seen and len(seen)<32);seen.add(row.id)
    record=row.data
    fields={'schema','id','version','workspace_id','actor_id','created_at','source_draft_ref','source_pins','target_configuration',
        'initial_working_draft_ref','fork_command_ref','child_create_ref','child_save_ref'}
    need(set(record)==fields and record['schema']=='catalogue-current-context-binding-record-v260'
        and record['id']==row.id and record['version']==row.version==1 and record['workspace_id']==row.workspace_id==p.workspace_id
        and record['actor_id']==row.actor_id==p.user_id and row.record_hash==digest(record)
        and record['created_at']==row.created_at.astimezone(UTC).isoformat())
    parent,parent_document=_at(record['source_draft_ref'],by_draft,by_command,p)
    need(row.source_draft_id==parent.id and cat.same(_pins(parent,p),record['source_pins']))
    if managed(parent.id):
        parents=[b for b in bindings.values() if b.working_draft_id==parent.id]
        need(len(parents)==1);_binding_meta(db,p,parents[0],by_draft,by_command,commands,bindings,seen)
    basis=ConfigurationBasis.model_validate(record['target_configuration']).model_dump()
    context_key=digest({'workspace_id':p.workspace_id,'actor_id':p.user_id,'source_draft_ref':record['source_draft_ref'],'target_configuration':basis})
    need(row.context_key==context_key and managed(row.working_draft_id) and row.working_draft_id!=parent.id)
    working=by_draft.get(row.working_draft_id)
    need(working is not None and working.actor_id==p.user_id and working.data['plan_ref'] is None
        and cat.same(working.data['origin'],parent.data['origin']))
    root=commands.get(row.fork_command_id);need(root is not None)
    need(row.working_draft_id==working_identity(p.workspace_id,root.request_id))
    _command_meta(db,p,root,by_draft,by_command)
    body,_=parse(root.exact_body.encode())
    need(root.outcome=='accepted' and root.binding_id==row.id and root.source_draft_id==parent.id
        and cat.same(record['fork_command_ref'],{'command_id':root.id,'request_id':root.request_id,'raw_sha256':root.raw_sha256,'body_hash':root.body_hash})
        and cat.same(body.source_draft_ref.model_dump(),record['source_draft_ref']) and cat.same(body.target_configuration.model_dump(),basis))
    create=by_command.get(record['child_create_ref']['command_id']);saved=by_command.get(record['child_save_ref']['command_id'])
    need(create is not None and saved is not None and create.draft_id==saved.draft_id==working.id
        and create.operation=='create' and saved.operation=='save' and create.draft_version==1 and saved.draft_version==2
        and create.request_id==child_request(p.workspace_id,root.request_id,'create') and saved.request_id==child_request(p.workspace_id,root.request_id,'save')
        and cat.same(record['child_create_ref'],_child_ref(create)) and cat.same(record['child_save_ref'],_child_ref(saved)))
    create_body=cat.parse_wire(create.exact_body.encode(),'create');save_body=cat.parse_wire(saved.exact_body.encode(),'save')
    need(create_body['consumer_id']==parent.consumer_id and create_body['expected_projection_hash']==parent.data['origin']['metadata']['projection_hash']
        and save_body['expected_version']==1 and save_body['expected_record_hash']==digest(create.data['state'])
        and cat.same(save_body['document'],parent_document) and cat.same(saved.data['document'],parent_document)
        and cat.same(save_body['actions'],_copy_actions(create.data['document'],parent_document)))
    initial,_=_at(record['initial_working_draft_ref'],by_draft,by_command,p)
    need(initial.id==working.id and record['initial_working_draft_ref']['version']==2
        and record['initial_working_draft_ref']['document_hash']==record['source_draft_ref']['document_hash']
        and cat.same(root.data['receipt']['working_draft_ref'],record['initial_working_draft_ref']))
    bound=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==BOUND_ACTION,m.Audit.object_id==row.id).limit(2)))
    link=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==LINK_ACTION,m.Audit.object_id==row.id).limit(2)))
    expected_link={'schema':'catalogue-current-context-children-v260','binding_id':row.id,'binding_record_hash':row.record_hash,
        'actor_id':p.user_id,'source_draft_id':parent.id,'working_draft_id':working.id,
        'command_id':root.id,'command_record_hash':root.record_hash,'child_create_ref':record['child_create_ref'],'child_save_ref':record['child_save_ref']}
    need(len(bound)==len(link)==1 and bound[0].actor_id==link[0].actor_id==p.user_id
        and cat.same(bound[0].data,record) and cat.same(link[0].data,expected_link))
    return row


def verify_catalogue_inventory(db,p,by_draft,by_command):
    """No catalogue callback: caller already checked ordinary continuous history."""
    rows=list(db.scalars(select(Binding).where(Binding.workspace_id==p.workspace_id,Binding.actor_id==p.user_id).limit(LIMIT+1)))
    commands=list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,Command.actor_id==p.user_id).limit(LIMIT+1)))
    need(len(rows)<=LIMIT and len(commands)<=LIMIT)
    bindings={row.id:row for row in rows};by_root={row.id:row for row in commands}
    need(len(bindings)==len(rows) and len(by_root)==len(commands))
    events=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action.in_([COMMAND_ACTION,BOUND_ACTION,LINK_ACTION,CONFIRM_ACTION]),m.Audit.actor_id==p.user_id).limit(LIMIT*4+1)))
    need(len(events)<=LIMIT*4 and not any(e.action==CONFIRM_ACTION for e in events),
        '后续目录确认记录尚未由本版本核验，未降为普通可写目录')
    for action,expected in ((COMMAND_ACTION,set(by_root)),(BOUND_ACTION,set(bindings)),(LINK_ACTION,set(bindings))):
        actual=[e.object_id for e in events if e.action==action]
        need(len(actual)==len(set(actual)) and set(actual)==expected)
    pending=_PENDING.get()
    allowed=None
    if pending is not None:
        allowed=pending['working_id'];_pending(db,p,allowed)
    managed_ids={row.id for row in by_draft.values() if managed(row.id)}
    expected_ids={row.working_draft_id for row in rows}
    need(managed_ids-({allowed} if allowed else set())==expected_ids-({allowed} if allowed else set()))
    for row in commands:_command_meta(db,p,row,by_draft,by_command)
    need({row.binding_id for row in commands if row.outcome=='accepted'}==set(bindings)
        and len([row for row in commands if row.outcome=='accepted'])==len(bindings))
    for row in rows:_binding_meta(db,p,row,by_draft,by_command,by_root,bindings)
    return bindings,by_root


def guard_mutation(db,p,row,operation,raw):
    if not managed(row.id):return
    pending=_pending(db,p,row.id)
    if pending is not None:
        need(pending['stage']=='save' and operation=='save' and hashlib.sha256(raw).hexdigest()==pending['save_sha']
            and cat.parse_wire(raw,'save')['request_id']==pending['save_nonce'])
        return
    # The normal inventory has already cold-checked all independent links.
    rows=list(db.scalars(select(Binding).where(Binding.workspace_id==p.workspace_id,
        Binding.actor_id==p.user_id,Binding.working_draft_id==row.id).limit(2)))
    need(len(rows)==1)
    need(operation=='save','该工作副本的当前范围确认须使用专属入口，不能借旧目录确认')
    confirmed=db.scalar(select(Command.id).where(Command.workspace_id==p.workspace_id,
        Command.binding_id==rows[0].id,Command.operation=='confirm').limit(1))
    need(confirmed is None,'已确认工作副本只读，原人稿保留')


def can_edit(db,p,row):
    if not managed(row.id):return True
    if _PENDING.get() is not None and _pending(db,p,row.id) is not None:return True
    values=list(db.scalars(select(Binding).where(Binding.workspace_id==p.workspace_id,
        Binding.actor_id==p.user_id,Binding.working_draft_id==row.id).limit(2)))
    need(len(values)==1)
    return db.scalar(select(Command.id).where(Command.workspace_id==p.workspace_id,
        Command.binding_id==values[0].id,Command.operation=='confirm').limit(1)) is None


def _inventory(db,p):
    cat._lock(db,p)
    by_draft,by_child,_=cat._inventory(db,p)
    bindings,commands=verify_catalogue_inventory(db,p,by_draft,by_child)
    return by_draft,by_child,bindings,commands


def _basis(db,p,*,lock=False):
    row=source.pipeline.legacy.workspace_draft(db,p,lock=lock)
    return row,{'revision':row.version,'configuration_hash':digest(row.data)}


def _context_value(p,parent,target,validation,verified,reason):
    value={'schema':'catalogue-current-context-source-v260','workspace_id':p.workspace_id,'actor_id':p.user_id,
        'source_draft_ref':_ref(parent),'source_pins':_pins(parent,p),'target_configuration':target,'validation':validation,
        'document_preserved':True,'current_source_verified':verified,'machine_analysis_performed':False,
        'can_dispatch':False,'can_confirm':False}
    return {**value,'can_fork':reason is None,'reason':reason,'context_hash':digest(value)}


def _fresh(db,p,parent):
    cat.plans._iam(db,p,write=True)
    fresh=cat._load_origin(db,p,parent.consumer_id,origin_mode=cat._mode(parent.data['origin']))
    need(cat.same(cat._origin_content(fresh),cat._origin_content(parent.data['origin'])),REASONS['source_unavailable'])
    actual=source.load_source_context(db,p,parent.data['origin']['metadata']['producer_id'],lock=True)
    need(cat.same(actual,parent.data['origin']['source_context']),REASONS['source_unavailable'])


def source_context(db,p,draft_id):
    identity(draft_id);cat.plans._iam(db,p,write=p.role in {'admin','operator'});cat._lock(db,p)
    drafts,_,_,_=_inventory(db,p);parent=cat._owned(drafts,draft_id)
    _,target=_basis(db,p)
    validation=cat._validation(parent.data['document'],parent.data['origin'])
    verified=False;reason=REASONS['not_eligible'] if p.role not in {'admin','operator'} else None
    if reason is None:
        try:_fresh(db,p,parent);verified=True
        except HTTPException as error:
            if error.status_code in {401,403}:raise
            reason=REASONS['source_unavailable']
        except (OSError,ValueError,KeyError,TypeError,AttributeError,UnicodeError,RecursionError):
            reason=REASONS['source_unavailable']
    return _context_value(p,parent,target,validation,verified,reason)


def _nonce(db,p,request_id):
    key='catalogue-current-v260:'+p.workspace_id+':'+request_id
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big',signed=True)})


def _existing(db,p,request_id):
    rows=list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==request_id).limit(2)))
    traces=list(db.scalars(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==COMMAND_ACTION,
        m.Audit.data['request_id'].as_string()==request_id).limit(2)))
    binding_traces=list(db.scalars(select(Binding.id).where(Binding.workspace_id==p.workspace_id,
        Binding.actor_id==p.user_id,Binding.data['fork_command_ref']['request_id'].as_string()==request_id).limit(2)))
    bound_traces=list(db.scalars(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==BOUND_ACTION,m.Audit.actor_id==p.user_id,
        m.Audit.data['fork_command_ref']['request_id'].as_string()==request_id).limit(2)))
    child_nonces=[child_request(p.workspace_id,request_id,operation) for operation in ('create','save')]
    child_traces=db.scalar(select(cat.Command.id).where(cat.Command.workspace_id==p.workspace_id,
        cat.Command.request_id.in_(child_nonces)).limit(1))
    child_audits=db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==cat.COMMAND_AUDIT,m.Audit.data['request_id'].as_string().in_(child_nonces)).limit(1))
    deterministic_id=working_identity(p.workspace_id,request_id)
    copy_trace=db.scalar(select(cat.Draft.id).where(cat.Draft.workspace_id==p.workspace_id,cat.Draft.id==deterministic_id).limit(1))
    foreign_binding=db.scalar(select(Binding.id).where(Binding.workspace_id==p.workspace_id,Binding.working_draft_id==deterministic_id).limit(1))
    foreign_bound=db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==BOUND_ACTION,
        m.Audit.data['initial_working_draft_ref']['id'].as_string()==deterministic_id).limit(1))
    state_trace=db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==cat.STATE_AUDIT,m.Audit.object_id==deterministic_id).limit(1))
    if not rows:
        need(not traces and not binding_traces and not bound_traces and child_traces is None and child_audits is None
            and copy_trace is None and foreign_binding is None and foreign_bound is None and state_trace is None)
        return None
    need(len(rows)==len(traces)==1);need(rows[0].actor_id==p.user_id,'原申请属于另一账号',403)
    _,_,_,commands=_inventory(db,p)
    need(rows[0].id in commands);return rows[0]


def _save_command(db,p,identity_value,body,raw_text,binding=None,working_ref=None,code=None,created_at=None):
    stamp=created_at or m.now();raw_sha=hashlib.sha256(raw_text.encode()).hexdigest();body_hash=digest(body.model_dump())
    receipt={'schema':'catalogue-current-context-receipt-v260','id':identity_value,'version':1,'workspace_id':p.workspace_id,
        'actor_id':p.user_id,'request_id':body.request_id,'operation':'fork','raw_sha256':raw_sha,'body_hash':body_hash,
        'created_at':stamp.astimezone(UTC).isoformat(),'outcome':'rejected' if code else 'accepted',
        'binding_id':binding.id if binding else None,'source_draft_ref':body.source_draft_ref.model_dump(),
        'working_draft_ref':copy.deepcopy(working_ref),'target_configuration':body.target_configuration.model_dump(),
        'plan_ref':None,'rejection':{'code':code,'reason':REASONS[code]} if code else None}
    receipt['record_hash']=digest(receipt)
    row=Command(id=identity_value,version=1,created_at=stamp,workspace_id=p.workspace_id,actor_id=p.user_id,
        request_id=body.request_id,source_draft_id=body.source_draft_ref.id,binding_id=binding.id if binding else None,
        operation='fork',outcome=receipt['outcome'],path=PREFIX+'/forks',exact_body=raw_text,raw_sha256=raw_sha,
        body_hash=body_hash,record_hash='',data={'receipt':receipt})
    row.record_hash=digest(_command_origin(row));db.add(row);db.flush();_audit(db,p,COMMAND_ACTION,row.id,_command_origin(row))
    return row


def _copy_actions(seed,document):
    before={row['key'] for row in seed['businesses']};after={row['key'] for row in document['businesses']};actions=[]
    reason='明确复制直接来源中已保存的目录结构；不推断旧拆分或合并决定'
    if before-after:actions.append({'kind':'delete','source_business_keys':sorted(before-after),'target_business_keys':[],'reason':reason})
    if after-before:actions.append({'kind':'add','source_business_keys':[],'target_business_keys':sorted(after-before),'reason':reason})
    cat.edit.validate_actions(actions,seed,document);return actions


def fork(db,p,raw):
    try:body,raw_text=parse(raw)
    except (ValueError,TypeError,UnicodeError,RecursionError):raise HTTPException(422,'目录复用原文或严格字段格式无效') from None
    prior=_existing(db,p,body.request_id)
    if prior:
        need(prior.exact_body==raw_text,'原号已绑定另一份正文，未重建工作副本');return copy.deepcopy(prior.data['receipt'])
    require(p,'admin','operator');lock_current_actor(db,p);_nonce(db,p,body.request_id)
    prior=_existing(db,p,body.request_id)
    if prior:
        need(prior.exact_body==raw_text,'原号已绑定另一份正文，未重建工作副本');return copy.deepcopy(prior.data['receipt'])
    # Child nonces are acquired before the Catalogue actor domain, including
    # when normal mutate() later re-enters these same locks.
    cat.plans._lock(db,{'scoped_design_actor_v181':p.user_id,'workspace_id':p.workspace_id},shared=True)
    children={op:child_request(p.workspace_id,body.request_id,op) for op in ('create','save')}
    for nonce in sorted(children.values()):cat.plans._lock(db,{'catalogue_nonce_v211':nonce,'workspace_id':p.workspace_id},shared=False)
    cat._lock(db,p,write=True)
    drafts,by_child,bindings,_=_inventory(db,p)
    parent=cat._owned(drafts,body.source_draft_ref.id)
    need(parent.actor_id==p.user_id and body.source_draft_ref.actor_id==p.user_id and body.source_draft_ref.workspace_id==p.workspace_id,
        '原目录不属于当前账号',403)
    _,target=_basis(db,p,lock=True);code=None
    if not cat.same(_ref(parent),body.source_draft_ref.model_dump()):code='draft_changed'
    elif not cat.same(target,body.target_configuration.model_dump()):code='configuration_changed'
    validation=cat._validation(parent.data['document'],parent.data['origin'])
    if code is None:
        try:
            _fresh(db,p,parent)
            value=_context_value(p,parent,target,validation,True,None)
            if value['context_hash']!=body.expected_context_hash:code='draft_changed'
        except HTTPException as error:
            if error.status_code in {401,403,404}:raise
            code='source_unavailable'
        except (OSError,ValueError,KeyError,TypeError,AttributeError,UnicodeError,RecursionError):
            code='source_unavailable'
    context_key=digest({'workspace_id':p.workspace_id,'actor_id':p.user_id,'source_draft_ref':body.source_draft_ref.model_dump(),
        'target_configuration':body.target_configuration.model_dump()})
    working_id=working_identity(p.workspace_id,body.request_id)
    if code is None and any(b.context_key==context_key for b in bindings.values()):code='already_forked'
    root_id=m.uid();stamp=m.now()
    if code:return copy.deepcopy(_save_command(db,p,root_id,body,raw_text,code=code,created_at=stamp).data['receipt'])
    need(working_id not in drafts and not any(c.request_id in children.values() for c in by_child.values()))
    create_body={'request_id':children['create'],'consumer_id':parent.consumer_id,
        'expected_projection_hash':parent.data['origin']['metadata']['projection_hash']}
    if cat._mode(parent.data['origin'])==cat.PARTIAL:create_body['origin_mode']=cat.PARTIAL
    create_raw=cat.bytes_value(create_body)
    document=copy.deepcopy(parent.data['document']);seed=cat._seed(parent.data['origin']);actions=_copy_actions(seed,document)
    try:
        with db.begin_nested():
            state={'working_id':working_id,'create_nonce':children['create'],'save_nonce':children['save'],
                'consumer_id':parent.consumer_id,'create_sha':hashlib.sha256(create_raw).hexdigest(),'save_sha':None,'stage':'create',
                'root_command_id':root_id,'context_key':context_key,'source_draft_ref':body.source_draft_ref.model_dump()}
            with _pending_fork(db,p,state):
                created=cat.mutate(db,p,None,'create',create_raw,actual_method='POST',actual_path=cat.command_path(None,'create'),
                    _trusted_create_id=working_id)
                save_body={'request_id':children['save'],'expected_version':created['version'],'expected_record_hash':created['record_hash'],
                    'document':document,'actions':actions}
                save_raw=cat.bytes_value(save_body);_PENDING.get()['save_sha']=hashlib.sha256(save_raw).hexdigest();_PENDING.get()['stage']='save'
                saved=cat.mutate(db,p,working_id,'save',save_raw,actual_method='POST',actual_path=cat.command_path(working_id,'save'))
                working=_row(db,p,cat.Draft,working_id,internal=True)
                need(working.document_hash==parent.document_hash and cat.same(working.data['origin'],parent.data['origin']))
                child_rows=list(db.scalars(select(cat.Command).where(cat.Command.workspace_id==p.workspace_id,cat.Command.request_id.in_(list(children.values())))))
                by_op={c.operation:c for c in child_rows};need(len(child_rows)==2 and set(by_op)=={'create','save'})
                binding_id=m.uid();working_ref=_ref(working)
                record={'schema':'catalogue-current-context-binding-record-v260','id':binding_id,'version':1,
                    'workspace_id':p.workspace_id,'actor_id':p.user_id,'created_at':stamp.astimezone(UTC).isoformat(),
                    'source_draft_ref':body.source_draft_ref.model_dump(),'source_pins':_pins(parent,p),
                    'target_configuration':target,'initial_working_draft_ref':working_ref,
                    'fork_command_ref':{'command_id':root_id,'request_id':body.request_id,'raw_sha256':hashlib.sha256(raw).hexdigest(),'body_hash':digest(body.model_dump())},
                    'child_create_ref':_child_ref(by_op['create']),'child_save_ref':_child_ref(by_op['save'])}
                binding=Binding(id=binding_id,version=1,created_at=stamp,workspace_id=p.workspace_id,actor_id=p.user_id,
                    source_draft_id=parent.id,working_draft_id=working.id,context_key=context_key,fork_command_id=root_id,
                    record_hash=digest(record),data=record)
                db.add(binding);db.flush()
                command=_save_command(db,p,root_id,body,raw_text,binding,working_ref,created_at=stamp)
                _audit(db,p,BOUND_ACTION,binding.id,record)
                _audit(db,p,LINK_ACTION,binding.id,{'schema':'catalogue-current-context-children-v260','binding_id':binding.id,
                    'binding_record_hash':binding.record_hash,'actor_id':p.user_id,'source_draft_id':parent.id,'working_draft_id':working.id,
                    'command_id':command.id,'command_record_hash':command.record_hash,
                    'child_create_ref':record['child_create_ref'],'child_save_ref':record['child_save_ref']})
            # No pending exemption may survive the final independent check.
            need(_PENDING.get() is None);db.flush();_inventory(db,p)
        return copy.deepcopy(command.data['receipt'])
    except HTTPException as error:
        if error.status_code in {401,403,404}:raise
        db.expire_all();return copy.deepcopy(_save_command(db,p,root_id,body,raw_text,code='preparation_failed',created_at=stamp).data['receipt'])
    except (ValueError,KeyError,TypeError,AttributeError,UnicodeError):
        db.expire_all();return copy.deepcopy(_save_command(db,p,root_id,body,raw_text,code='preparation_failed',created_at=stamp).data['receipt'])


def request_result(db,p,request_id,raw_sha256):
    identity(request_id);need(sha(raw_sha256),'原文摘要格式无效',422)
    row=_existing(db,p,request_id)
    if row:need(row.raw_sha256==raw_sha256,'原号已绑定不同原文')
    return {'schema':'catalogue-current-context-request-v260','workspace_id':p.workspace_id,'actor_id':p.user_id,
        'request_id':request_id,'raw_sha256':raw_sha256,'found':row is not None,'operation':row.operation if row else None,
        'outcome':row.outcome if row else None,'binding_id':row.binding_id if row else None,
        'receipt':copy.deepcopy(row.data['receipt']) if row else None,'original_raw':row.exact_body if row else None}


def _binding_view(p,row,drafts,commands):
    root=commands[row.fork_command_id];working=drafts[row.working_draft_id]
    return {'schema':'catalogue-current-context-binding-v260','id':row.id,'version':1,'workspace_id':p.workspace_id,'actor_id':p.user_id,
        'created_at':row.created_at.astimezone(UTC).isoformat(),'source_draft_ref':copy.deepcopy(row.data['source_draft_ref']),
        'source_pins':copy.deepcopy(row.data['source_pins']),'target_configuration':copy.deepcopy(row.data['target_configuration']),
        'initial_working_draft_ref':copy.deepcopy(row.data['initial_working_draft_ref']),'current_working_draft_ref':_ref(working),
        'status':'working','plan_ref':None,'fork_receipt':copy.deepcopy(root.data['receipt']),'confirmation_receipt':None,
        'can_edit':p.role in {'admin','operator'},'can_confirm':False,
        'reason':'目录和原人稿已保留；当前配置确认尚未接通，模型分派请从当前范围入口核对','record_hash':row.record_hash}


def binding_view(db,p,binding_id):
    identity(binding_id);drafts,_,bindings,commands=_inventory(db,p)
    row=bindings.get(binding_id);need(row is not None,'没有当前账号的复用目录',404)
    return _binding_view(p,row,drafts,commands)


def binding_for_working(db,p,working_draft_id):
    identity(working_draft_id);drafts,_,bindings,commands=_inventory(db,p)
    rows=[row for row in bindings.values() if row.working_draft_id==working_draft_id]
    need(len(rows)==1,'没有当前账号的工作副本绑定',404)
    return _binding_view(p,rows[0],drafts,commands)


def history(db,p,*,source_draft_id=None,before_id=None,limit=20):
    need(type(limit) is int and 1<=limit<=50,'历史页大小无效',422)
    if source_draft_id is not None:identity(source_draft_id)
    _,_,_,commands=_inventory(db,p)
    values=[r for r in commands.values() if source_draft_id is None or r.source_draft_id==source_draft_id]
    values.sort(key=lambda r:(r.created_at,r.id),reverse=True)
    if before_id is not None:
        identity(before_id);cursor=next((r for r in values if r.id==before_id),None)
        need(cursor is not None,'历史游标不属于当前账号与来源',404)
        values=[r for r in values if (r.created_at,r.id)<(cursor.created_at,cursor.id)]
    more=len(values)>limit;page=values[:limit]
    return {'schema':'catalogue-current-context-history-v260','workspace_id':p.workspace_id,'actor_id':p.user_id,
        'source_draft_id':source_draft_id,'before_id':before_id,'items':[copy.deepcopy(r.data['receipt']) for r in page],
        'has_more':more,'next_cursor':page[-1].id if more else None}
