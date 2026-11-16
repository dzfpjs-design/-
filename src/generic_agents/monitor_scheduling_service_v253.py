"""Scheduling mode ledger and final action gate, without new device authority.

Policy EX serializes the first Head as well as changes with action admission.
Writers never acquire a Goal/V200 Task after taking that Policy lock.
"""
import copy
import hashlib
import json
from functools import wraps
from fastapi import HTTPException
from sqlalchemy import Text,and_,or_,cast,func,select,text
from .contracts import digest
from .models import Acquisition,Audit,Capability,DataRun,DomainInstance,Entity,InstanceRelease,Job,MonitorPolicy,ObservationPlan,Task,now,uid
from .services import require,scoped
from .monitor_scheduling_contracts_v253 import ModeCommand,RetireCommand
from .monitor_scheduling_models_v253 import (MonitorSchedulingHeadV253 as Head,MonitorSchedulingCommandV253 as Command,
    MonitorSchedulingRetiredRequestV253 as RetiredRequest)

ACTION='monitor.scheduling_mode_saved_v253'
ACTION_PIN='monitor.scheduling_acquisition_v253'
RETIRED_ACTION='monitor.scheduling_request_retired_v253'
KEY='monitor_scheduling_v253'
MAX_COMMANDS=512
ERROR='无法核对监测方式及原操作记录；未安排新的采集，请保留原申请并检查相关资料'
GOAL_ONLY='此监测策略仅在已确认业务目标需要补充资料时采集；未安排周期采集'
STALE='监测方式已变化，原排队记录保留；未按旧设置安排采集'


def need(ok,message=ERROR,code=409):
    if not ok:raise HTTPException(code,message)


def integrity(fn):
    @wraps(fn)
    def checked_shape(*args,**kwargs):
        try:return fn(*args,**kwargs)
        except (KeyError,TypeError,ValueError,AttributeError,IndexError):
            raise HTTPException(409,ERROR) from None
    return checked_shape


def same(a,b):return digest(a)==digest(b)
def seal(value):return {**copy.deepcopy(value),'record_hash':digest(value)}
def plain(value):return {k:copy.deepcopy(v) for k,v in value.items() if k!='record_hash'}


def internal(db,model,identifier,p):
    try:return scoped(db,model,identifier,p)
    except HTTPException as e:
        if e.status_code==404:raise HTTPException(409,ERROR) from None
        raise


def command_anchor(row):
    return {'schema':'monitor-scheduling-command-audit-v253','command_id':row.id,'policy_id':row.policy_id,
        'actor_id':row.actor_id,'request_id':row.request_id,'command_hash':row.command_hash}


@integrity
def checked_command(db,p,row):
    d=row.data
    keys={'schema','id','workspace_id','policy_id','instance_id','actor_id','request_id','body','body_hash',
        'raw','raw_sha256','before','after','result'}
    need(type(d) is dict and set(d)==keys and d['schema']=='monitor-scheduling-saved-command-v253'
        and row.workspace_id==p.workspace_id==d['workspace_id'] and row.id==d['id']
        and row.policy_id==d['policy_id'] and row.actor_id==d['actor_id'] and row.request_id==d['request_id']
        and row.version==1 and digest(d)==row.command_hash)
    try:
        body=ModeCommand.model_validate(d['body'])
        decoded=json.loads(d['raw'])
    except (ValueError,TypeError,KeyError):need(False)
    need(same(body.model_dump(mode='json'),d['body']) and same(decoded,d['body'])
        and body.request_id==row.request_id and body.instance_id==d['instance_id']
        and digest(d['body'])==d['body_hash'] and type(d['raw']) is str
        and hashlib.sha256(d['raw'].encode('utf8')).hexdigest()==d['raw_sha256'])
    after=d['after'];before=d['before'];result=d['result']
    need(type(after) is dict and after['record_hash']==digest(plain(after))
        and after['workspace_id']==row.workspace_id and after['policy_id']==row.policy_id
        and after['actor_id']==row.actor_id and after['mode']==body.mode
        and after['version']==body.expected_version+1 and after['epoch']==after['version']
        and after['last_command_id']==row.id and after['command_ids'][-1:]==[row.id])
    need((before is None and body.expected_version==0 and after['command_ids']==[row.id]) or
        (type(before) is dict and before['record_hash']==digest(plain(before))
         and before['version']==body.expected_version and before['id']==after['id']
         and before['policy_id']==row.policy_id and before['workspace_id']==p.workspace_id
         and after['command_ids']==[*before['command_ids'],row.id]))
    expected={'schema':'monitor-scheduling-result-v253','command_id':row.id,'request_id':row.request_id,
        'workspace_id':p.workspace_id,'instance_id':body.instance_id,'policy_id':row.policy_id,'actor_id':row.actor_id,
        'operation':'set_mode','mode':body.mode,'version':after['version'],'epoch':after['epoch'],
        'control_id':after['id'],'control_hash':after['record_hash'],'policy_version':body.expected_policy_version,
        'approved_hash':body.approved_hash,'body_hash':d['body_hash'],'raw_sha256':d['raw_sha256'],
        'committed_at':after['updated_at']}
    expected={**expected,'receipt_hash':digest(expected)}
    need(same(result,expected))
    anchors=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==ACTION,Audit.object_id==row.id)))
    need(len(anchors)==1 and anchors[0].actor_id==row.actor_id and same(anchors[0].data,command_anchor(row)))
    return copy.deepcopy(d)


def has_control_trace(db,p,policy):
    """A lost ledger cannot erase independent known-control execution traces.

    Bounded ID-only probes, without reading source values or adopting pins as
    authority. Genuine legacy Plans/Acquisitions do not carry this reserved key.
    """
    checks=[(ObservationPlan,and_(ObservationPlan.data.has_key(KEY),or_(
        ObservationPlan.policy_id==policy.id,ObservationPlan.data[KEY]['policy_id'].as_string()==policy.id))),
        (Acquisition,and_(Acquisition.data.has_key(KEY),or_(Acquisition.data['policy_id'].as_string()==policy.id,
            Acquisition.data[KEY]['policy_id'].as_string()==policy.id))),
        (Audit,or_(and_(Audit.action==ACTION_PIN,Audit.data['policy_id'].as_string()==policy.id),
            and_(Audit.action=='workspace.monitor.tick_queued_v203',
                Audit.data['pin']['schedule_hint'].has_key('control'),or_(
                    Audit.data['pin']['policy_id'].as_string()==policy.id,
                    Audit.data['pin']['schedule_hint']['control']['policy_id'].as_string()==policy.id)))),
        (Job,and_(Job.data['workspace_monitor_tick']['schedule_hint'].has_key('control'),or_(
            and_(Job.kind=='consume_workspace_monitor_tick',Job.data['policy_id'].as_string()==policy.id),
            Job.data['workspace_monitor_tick']['schedule_hint']['control']['policy_id'].as_string()==policy.id)))]
    for model,where in checks:
        if db.scalar(select(model.id).where(model.workspace_id==p.workspace_id,where).limit(1)) is not None:return True
    return False


@integrity
def checked(db,p,policy):
    heads=list(db.scalars(select(Head).where(Head.workspace_id==p.workspace_id,Head.policy_id==policy.id)
        .execution_options(populate_existing=True)))
    commands=list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,Command.policy_id==policy.id)
        .order_by(Command.created_at,Command.id).limit(MAX_COMMANDS+1)))
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==ACTION,
        Audit.data['policy_id'].as_string()==policy.id)))
    need(len(heads)<=1 and len(commands)<=MAX_COMMANDS)
    if not heads:
        need(not commands and not audits and not has_control_trace(db,p,policy))
        return None
    head=heads[0];d=head.data
    need(type(d) is dict and set(d)=={'schema','id','workspace_id','policy_id','version','epoch','mode',
        'actor_id','updated_at','command_ids','last_command_id','record_hash'}
        and d['schema']=='monitor-scheduling-head-v253' and d['id']==head.id
        and d['workspace_id']==p.workspace_id and d['policy_id']==policy.id and d['version']==head.version
        and d['epoch']==head.epoch==head.version and d['mode']==head.mode in {'periodic','goal_only'}
        and d['record_hash']==head.record_hash==digest(plain(d)))
    identities=d['command_ids']
    need(type(identities) is list and len(identities)==len(set(identities))==head.version
        and set(identities)=={c.id for c in commands} and {a.object_id for a in audits}==set(identities)
        and len(audits)==len(identities) and identities[-1:]==[d['last_command_id']])
    byid={c.id:c for c in commands};previous=None
    for identity in identities:
        item=checked_command(db,p,byid[identity]);need(same(item['before'],previous));previous=item['after']
    need(same(previous,d))
    return head


def runtime_pin(db,p,policy):
    head=checked(db,p,policy)
    return None if head is None else {'schema':'monitor-scheduling-pin-v253','control_id':head.id,
        'policy_id':policy.id,'epoch':head.epoch,'mode':head.mode,'record_hash':head.record_hash}


def scope(db,p,policy,instance_id):
    instance=scoped(db,DomainInstance,instance_id,p)
    objects=[internal(db,Entity,i,p) for i in policy.data['entity_ids']]
    need(any(o.namespace==instance.namespace for o in objects),'此监测策略不属于所选业务实例',404)
    return instance,objects,all(o.namespace==instance.namespace for o in objects)


def view(db,p,policy,instance_id):
    instance,objects,all_inside=scope(db,p,policy,instance_id)
    head=checked(db,p,policy);reason=None
    if not all_inside:reason='此策略同时覆盖其他业务实例，请在完整策略管理中核对；此页面不能修改整组设置'
    elif p.role!='admin':reason='请由管理员修改监测方式'
    else:
        from .perception import authorized_monitor
        try:authorized_monitor(db,p,policy)
        except HTTPException as error:reason=str(error.detail)
    control=None if head is None else {'id':head.id,'version':head.version,'epoch':head.epoch,'mode':head.mode,
        'actor_id':head.data['actor_id'],'updated_at':head.data['updated_at'],'record_hash':head.record_hash}
    return {'schema':'monitor-scheduling-view-v253','workspace_id':p.workspace_id,'instance_id':instance.id,
        'policy_id':policy.id,'policy_version':policy.version,'approved_hash':policy.data.get('approved_hash'),
        'control':control,'effective_mode':'legacy' if head is None else head.mode,
        'can_change':reason is None,'reason':reason,'changes_authorization':False,
        'action_notice':'切换方式不取消已开始或结果未知的采集，请继续核对原回执；旧版本未下发的请求不会恢复下发，恢复周期监测将使用新版本'}


def find_command(db,p,request_id):
    rows=list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==request_id).limit(2)))
    need(len(rows)<=1)
    return rows[0] if rows else None


def recovery(db,p,policy_id,request_id,raw_sha256):
    scoped(db,MonitorPolicy,policy_id,p)
    result={'schema':'monitor-scheduling-recovery-v253','workspace_id':p.workspace_id,'policy_id':policy_id,
        'request_id':request_id,'raw_sha256':raw_sha256,'found':False,'outcome':None,
        'raw':None,'body_hash':None,'result':None,'retirement':None}
    committed,retired=nonce_records(db,p,policy_id,request_id,read=True)
    d=committed if committed is not None else retired
    if d is None:return result
    need(d['raw_sha256']==raw_sha256,'原申请字节摘要不一致')
    return {**result,'found':True,'outcome':'committed' if committed is not None else 'retired',
        'raw':d['raw'],'body_hash':d['body_hash'],'result':committed['result'] if committed is not None else None,
        'retirement':retired['receipt'] if retired is not None else None}


def set_mode(db,p,policy_id,body,raw):
    # Same nonce is recovered before current authorization/CAS. It remains a
    # historical request, never an implicit second write after role/mode change.
    prior=original_mode_result(db,p,policy_id,body,raw)
    if prior is not None:return prior,False
    require(p,'admin')
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    lock_nonce(db,p,body.request_id)
    prior=original_mode_result(db,p,policy_id,body,raw)
    if prior is not None:return prior,False
    policy=scoped(db,MonitorPolicy,policy_id,p,True)
    from .perception import authorized_monitor
    authorized_monitor(db,p,policy)
    _,_,all_inside=scope(db,p,policy,body.instance_id)
    need(all_inside,'此策略跨业务实例，此页面不能修改整组设置',403)
    need(policy.version==body.expected_policy_version and policy.data['approved_hash']==body.approved_hash,'监测授权版本已变化，请重新核对')
    head=checked(db,p,policy)
    need((head.version if head else 0)==body.expected_version,'监测方式已被修改，请重新核对')
    previous=copy.deepcopy(head.data) if head else None
    need(head is None or len(head.data['command_ids'])<MAX_COMMANDS,'原操作历史已达容量，请保留记录')
    command_id=uid();head_id=head.id if head else uid();version=body.expected_version+1
    after=seal({'schema':'monitor-scheduling-head-v253','id':head_id,'workspace_id':p.workspace_id,'policy_id':policy.id,
        'version':version,'epoch':version,'mode':body.mode,'actor_id':p.user_id,'updated_at':now().isoformat(),
        'command_ids':[*((previous or {}).get('command_ids',[])),command_id],'last_command_id':command_id})
    typed=body.model_dump(mode='json');raw_hash=hashlib.sha256(raw.encode('utf8')).hexdigest()
    receipt={'schema':'monitor-scheduling-result-v253','command_id':command_id,'request_id':body.request_id,
        'workspace_id':p.workspace_id,'instance_id':body.instance_id,'policy_id':policy.id,'actor_id':p.user_id,
        'operation':'set_mode','mode':body.mode,'version':version,'epoch':version,'control_id':head_id,
        'control_hash':after['record_hash'],'policy_version':policy.version,'approved_hash':policy.data['approved_hash'],
        'body_hash':digest(typed),'raw_sha256':raw_hash,'committed_at':after['updated_at']}
    receipt={**receipt,'receipt_hash':digest(receipt)}
    data={'schema':'monitor-scheduling-saved-command-v253','id':command_id,'workspace_id':p.workspace_id,
        'policy_id':policy.id,'instance_id':body.instance_id,'actor_id':p.user_id,'request_id':body.request_id,
        'body':typed,'body_hash':digest(typed),'raw':raw,'raw_sha256':raw_hash,'before':previous,'after':after,'result':receipt}
    command=Command(id=command_id,workspace_id=p.workspace_id,policy_id=policy.id,actor_id=p.user_id,
        request_id=body.request_id,version=1,command_hash=digest(data),data=data)
    db.add(command)
    if head is None:
        head=Head(id=head_id,workspace_id=p.workspace_id,policy_id=policy.id,version=version,
            epoch=version,mode=body.mode,record_hash=after['record_hash'],data=after);db.add(head)
    else:head.version=version;head.epoch=version;head.mode=body.mode;head.record_hash=after['record_hash'];head.data=after
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,object_id=command.id,data=command_anchor(command)))
    db.flush();checked(db,p,policy)
    return copy.deepcopy(receipt),True


def object_title(db,p,entity):
    package=internal(db,Capability,entity.schema_capability_id,p)
    need(package.kind=='domain_package' and digest(package.data)==package.content_hash)
    from .instance_relation_types import relation_metadata
    return relation_metadata(package,[entity])['entities'][0]['title']


def catalog(db,p,instance_id,offset=0,limit=20):
    instance=scoped(db,DomainInstance,instance_id,p)
    related=select(Entity.id).where(Entity.workspace_id==p.workspace_id,Entity.namespace==instance.namespace,
        func.jsonb_exists(MonitorPolicy.data['entity_ids'],cast(Entity.id,Text))).exists()
    predicate=(MonitorPolicy.workspace_id==p.workspace_id,related)
    total=db.scalar(select(func.count()).select_from(MonitorPolicy).where(*predicate))
    policies=list(db.scalars(select(MonitorPolicy).where(*predicate).order_by(MonitorPolicy.created_at.desc(),MonitorPolicy.id).offset(offset).limit(limit)))
    labels={};units={}
    if instance.active_release_id:
        release=internal(db,InstanceRelease,instance.active_release_id,p)
        need(digest(release.data['snapshot'])==release.configuration_hash)
        for kind in release.data['snapshot'].get('object_types',[]):
            labels.update({f['key']:f['label'] for f in kind['fields']});units.update({f['key']:f.get('unit') for f in kind['fields']})
    records=[]
    for policy in policies:
        _,objects,all_inside=scope(db,p,policy,instance.id)
        sources=[internal(db,Capability,i,p) for i in policy.data['source_ids']]
        records.append({'id':policy.id,'title':policy.data['title'],'status':policy.status,
            'fields':[{'key':f,'label':labels.get(f,f),'unit':units.get(f)} for f in policy.data['fields']],
            'objects':[{'id':o.id,'title':object_title(db,p,o),
                'entity_type':o.entity_type,'in_instance':o.namespace==instance.namespace} for o in objects],
            'sources':[{'id':c.id,'title':c.data.get('display_name') or c.data['name']} for c in sources],
            'valid_until':policy.data.get('valid_until'),'max_acquisitions_per_day':policy.data['max_acquisitions_per_day'],
            'cooldown_seconds':policy.data['cooldown_seconds'],'all_entities_in_instance':all_inside,
            'view':view(db,p,policy,instance.id)})
    return {'schema':'monitor-scheduling-catalog-v253','workspace_id':p.workspace_id,'instance_id':instance.id,
        'offset':offset,'limit':limit,'total':total,'has_more':offset+len(records)<total,'records':records}



def periodic_gate(db,p,policy,*,expected=None,check_expected=False):
    current=runtime_pin(db,p,policy)
    if check_expected and not same(expected,current):return {'allowed':False,'reason':STALE,'pin':current}
    allowed=current is None or current['mode']=='periodic'
    return {'allowed':allowed,'reason':None if allowed else GOAL_ONLY,'pin':current}


@integrity
def formal_goal(db,p,request):
    """Read an already guarded request; never acquire a new Task after Policy.

    Original effective()/ObservationRequest.guard owns its Task/Gap locks and
    source authorization. This extra discriminator refuses ordinary DataNeeds.
    """
    if not request:return None
    run=internal(db,DataRun,request['query_id'],p)
    delegation=run.data.get('goal_delegation')
    if not delegation and not run.data.get('native_goal_task_id'):return None
    need(type(delegation) is dict and run.data.get('native_goal_task_id')==delegation.get('task_id')
        and same(delegation,request.get('goal_delegation')))
    task=internal(db,Task,delegation['task_id'],p)
    from .goals import snapshot
    context=task.data.get('native_goal_context')
    need(type(context) is dict and digest(context)==task.data.get('native_goal_context_hash')
        and run.data.get('native_goal_context_hash')==digest(context)
        and run.data.get('native_goal_request_hash')==digest(run.data['request'])
        and same(delegation['goal_snapshot'],snapshot(task))
        and run.data['native_goal_task_id']==task.id and context['entity_ids']==task.data['entity_ids']
        and task.data['goal_spec']['observation_mode']=='allow_new'
        and task.data.get('workflow_runtime') is not None)
    from .native_goals import guard
    guard(db,p,task,lock_sources=False)
    need(task.data.get('native_goal_query_id')==run.id and request['entity_id'] in context['entity_ids']
        and request['gap_id'] in run.data.get('data_gap_ids',[]) and request['fields']
        and set(request['fields']).issubset(task.data['goal_spec']['required_fields'])
        and request['query_request_hash']==digest(run.data['request']))
    return {'task_id':task.id,'query_id':run.id,'gap_id':request['gap_id'],
        'request_hash':digest(request),'goal_context_hash':digest(context)}


def action_pin(db,p,policy,request):
    current=runtime_pin(db,p,policy)
    if current is None:return None
    goal=formal_goal(db,p,request)
    need(current['mode']=='periodic' or goal is not None,GOAL_ONLY)
    return {'schema':'monitor-scheduling-action-v253','policy_id':policy.id,
        'trigger':'formal_goal' if goal else 'periodic','control':current,'goal':goal}


def require_proposal(db,p,policy,request):
    # No Head preserves legacy behavior. A generic request is not a formal Goal.
    return action_pin(db,p,policy,request)


def freeze_acquisition(db,p,policy,acquisition,request):
    pin=action_pin(db,p,policy,request)
    if pin is None:return
    need(KEY not in acquisition.data and not db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
        Audit.action==ACTION_PIN,Audit.object_id==acquisition.id)))
    acquisition.data={**acquisition.data,KEY:pin}
    data={'schema':'monitor-scheduling-acquisition-audit-v253','acquisition_id':acquisition.id,
        'policy_id':policy.id,'actor_id':p.user_id,'pin_hash':digest(pin),'pin':copy.deepcopy(pin)}
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION_PIN,object_id=acquisition.id,data=data))


@integrity
def require_dispatch(db,p,policy,acquisition,request):
    current=runtime_pin(db,p,policy)
    saved=acquisition.data.get(KEY)
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==ACTION_PIN,Audit.object_id==acquisition.id)))
    if saved is None:
        need(not audits)
        if current is None:return
        # Pre-control genuine Goal work keeps its original source permission;
        # an old periodic acquisition never gains a new control epoch by retry.
        if formal_goal(db,p,request) is not None:return
        need(False,GOAL_ONLY if current['mode']=='goal_only' else STALE)
    need(type(saved) is dict and set(saved)=={'schema','policy_id','trigger','control','goal'}
        and saved['schema']=='monitor-scheduling-action-v253' and saved['policy_id']==policy.id
        and len(audits)==1 and audits[0].actor_id==audits[0].data.get('actor_id')
        and same(audits[0].data,{'schema':'monitor-scheduling-acquisition-audit-v253',
            'acquisition_id':acquisition.id,'policy_id':policy.id,'actor_id':audits[0].actor_id,
            'pin_hash':digest(saved),'pin':saved}))
    if saved['trigger']=='formal_goal':
        need(saved['goal'] is not None and same(saved['goal'],formal_goal(db,p,request)))
        return
    need(saved['trigger']=='periodic' and saved['goal'] is None and current is not None
        and current['mode']=='periodic' and same(current,saved['control']),STALE)



def lock_nonce(db,p,request_id):
    key=int(digest({'monitor_scheduling_nonce':p.workspace_id,'request_id':request_id})[:15],16)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})


def retirement_anchor(row):
    return {'schema':'monitor-scheduling-retirement-audit-v253','retirement_id':row.id,
        'workspace_id':row.workspace_id,'policy_id':row.policy_id,'instance_id':row.instance_id,
        'actor_id':row.actor_id,'request_id':row.request_id,'record_hash':row.record_hash}


def original_for_retirement(body,*,code=422):
    try:
        need(len(body.original_raw.encode('utf8'))<=32768,'原申请正文过大',code)
        original=ModeCommand.model_validate(json.loads(body.original_raw))
    except (ValueError,TypeError,UnicodeError):
        raise HTTPException(code,'原申请正文或字段不能核对，请保留原文') from None
    need(original.request_id==body.request_id and original.instance_id==body.instance_id
        and hashlib.sha256(body.original_raw.encode('utf8')).hexdigest()==body.raw_sha256
        and digest(original.model_dump(mode='json'))==body.body_hash,'原申请的范围、正文或摘要不一致',code)
    return original


@integrity
def checked_retirement(db,p,row):
    d=row.data
    need(row.workspace_id==p.workspace_id and row.version==1 and type(d) is dict and set(d)=={
        'schema','id','workspace_id','policy_id','instance_id','actor_id','request_id','original_body','body_hash',
        'raw','raw_sha256','retirement_body','retirement_body_hash','retirement_raw','retirement_raw_sha256','retired_at','receipt'}
        and d['schema']=='monitor-scheduling-saved-retirement-v253' and digest(d)==row.record_hash
        and d['id']==row.id and d['workspace_id']==row.workspace_id and d['policy_id']==row.policy_id
        and d['instance_id']==row.instance_id and d['actor_id']==row.actor_id and d['request_id']==row.request_id)
    command=RetireCommand.model_validate(d['retirement_body']);original=original_for_retirement(command,code=409)
    need(same(command.model_dump(mode='json'),json.loads(d['retirement_raw']))
        and d['retirement_body_hash']==digest(command.model_dump(mode='json'))
        and d['retirement_raw_sha256']==hashlib.sha256(d['retirement_raw'].encode('utf8')).hexdigest()
        and same(d['original_body'],original.model_dump(mode='json')) and d['raw']==command.original_raw
        and d['raw_sha256']==command.raw_sha256 and d['body_hash']==command.body_hash
        and command.request_id==row.request_id and command.instance_id==row.instance_id)
    expected={'schema':'monitor-scheduling-retirement-v253','id':row.id,'workspace_id':p.workspace_id,
        'instance_id':row.instance_id,'policy_id':row.policy_id,'actor_id':row.actor_id,'request_id':row.request_id,
        'body_hash':d['body_hash'],'raw_sha256':d['raw_sha256'],'retirement_body_hash':d['retirement_body_hash'],
        'retirement_raw_sha256':d['retirement_raw_sha256'],'reason':command.reason,'retired_at':d['retired_at'],
        'mode_changed':False,'epoch_changed':False}
    need(same(d['receipt'],{**expected,'receipt_hash':digest(expected)}))
    internal(db,MonitorPolicy,row.policy_id,p);internal(db,DomainInstance,row.instance_id,p)
    anchors=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==RETIRED_ACTION,Audit.object_id==row.id).limit(2)))
    need(len(anchors)==1 and anchors[0].actor_id==row.actor_id and same(anchors[0].data,retirement_anchor(row)))
    return copy.deepcopy(d)


@integrity
def nonce_records(db,p,policy_id,request_id,*,read=False):
    commands=list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==request_id).limit(2)))
    retired=list(db.scalars(select(RetiredRequest).where(RetiredRequest.workspace_id==p.workspace_id,RetiredRequest.request_id==request_id).limit(2)))
    claims=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action.in_([ACTION,RETIRED_ACTION]),Audit.data['request_id'].as_string()==request_id).limit(3)))
    need(len(commands)<=1 and len(retired)<=1 and not(commands and retired))
    owners={r.actor_id for r in [*commands,*retired]}|{a.actor_id for a in claims}
    policies={r.policy_id for r in [*commands,*retired]}|{a.data.get('policy_id') for a in claims}
    if owners:need(p.user_id in owners and policy_id in policies,'没有此原申请',404 if read else 409)
    if not commands and not retired:
        need(not claims)
        return None,None
    if commands:
        row=commands[0];d=checked_command(db,p,row)
        need(row.actor_id==p.user_id and row.policy_id==policy_id and len(claims)==1
            and claims[0].action==ACTION and claims[0].object_id==row.id)
        return d,None
    row=retired[0];d=checked_retirement(db,p,row)
    need(row.actor_id==p.user_id and row.policy_id==policy_id and len(claims)==1
        and claims[0].action==RETIRED_ACTION and claims[0].object_id==row.id)
    return None,d


def original_mode_result(db,p,policy_id,body,raw):
    committed,retired=nonce_records(db,p,policy_id,body.request_id)
    if committed is not None:
        need(committed['raw']==raw and same(committed['body'],body.model_dump(mode='json')),'原请求不能更换正文')
        return committed['result']
    if retired is not None:
        need(retired['raw']==raw and same(retired['original_body'],body.model_dump(mode='json')),'已结束原申请不能更换正文')
        raise HTTPException(409,'这份申请已保留并结束，不能再次执行；请查看原记录后另拟申请')
    return None


def retirement_result(p,policy_id,request_id,committed,retired):
    return {'schema':'monitor-scheduling-retire-result-v253','workspace_id':p.workspace_id,'policy_id':policy_id,
        'request_id':request_id,'outcome':'committed' if committed is not None else 'retired',
        'result':committed['result'] if committed is not None else None,
        'retirement':retired['receipt'] if retired is not None else None}


def _ending_replay(db,p,policy_id,body,raw):
    committed,retired=nonce_records(db,p,policy_id,body.request_id)
    if committed is not None:
        need(committed['raw']==body.original_raw and committed['body_hash']==body.body_hash
            and committed['raw_sha256']==body.raw_sha256 and committed['instance_id']==body.instance_id,
            '原申请不能更换范围或正文')
        return retirement_result(p,policy_id,body.request_id,committed,None)
    if retired is not None:
        need(retired['retirement_raw']==raw and same(retired['retirement_body'],body.model_dump(mode='json')),
            '已结束操作的原文不能更换')
        return retirement_result(p,policy_id,body.request_id,None,retired)
    return None


def retire_request(db,p,policy_id,body,raw):
    original=original_for_retirement(body)
    saved=_ending_replay(db,p,policy_id,body,raw)
    if saved is not None:return saved,False
    # With no trusted stored intent there is no historical owner proof. Only a
    # current real administrator can first fence this nonce, never client actor
    # claims or found:false. The fence grants no mode/monitoring permission.
    require(p,'admin')
    from .relations import lock_current_actor
    lock_current_actor(db,p);lock_nonce(db,p,body.request_id)
    saved=_ending_replay(db,p,policy_id,body,raw)
    if saved is not None:return saved,False
    policy=scoped(db,MonitorPolicy,policy_id,p,True)
    scope(db,p,policy,body.instance_id) # Real current workspace/namespace, no stale approval/CAS gate.
    identity=uid();stamp=now().isoformat();typed=body.model_dump(mode='json')
    receipt={'schema':'monitor-scheduling-retirement-v253','id':identity,'workspace_id':p.workspace_id,
        'instance_id':body.instance_id,'policy_id':policy.id,'actor_id':p.user_id,'request_id':body.request_id,
        'body_hash':body.body_hash,'raw_sha256':body.raw_sha256,'retirement_body_hash':digest(typed),
        'retirement_raw_sha256':hashlib.sha256(raw.encode('utf8')).hexdigest(),'reason':body.reason,'retired_at':stamp,
        'mode_changed':False,'epoch_changed':False}
    receipt={**receipt,'receipt_hash':digest(receipt)}
    data={'schema':'monitor-scheduling-saved-retirement-v253','id':identity,'workspace_id':p.workspace_id,
        'policy_id':policy.id,'instance_id':body.instance_id,'actor_id':p.user_id,'request_id':body.request_id,
        'original_body':original.model_dump(mode='json'),'body_hash':body.body_hash,'raw':body.original_raw,
        'raw_sha256':body.raw_sha256,'retirement_body':typed,'retirement_body_hash':digest(typed),
        'retirement_raw':raw,'retirement_raw_sha256':receipt['retirement_raw_sha256'],'retired_at':stamp,'receipt':receipt}
    row=RetiredRequest(id=identity,workspace_id=p.workspace_id,policy_id=policy.id,instance_id=body.instance_id,
        actor_id=p.user_id,request_id=body.request_id,version=1,record_hash=digest(data),data=data)
    db.add(row);db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=RETIRED_ACTION,
        object_id=row.id,data=retirement_anchor(row)))
    db.flush();_,proof=nonce_records(db,p,policy.id,body.request_id)
    return retirement_result(p,policy.id,body.request_id,None,proof),True
