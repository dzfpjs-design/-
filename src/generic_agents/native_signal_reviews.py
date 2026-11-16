"""Explicit supplementary business review, original native workflow remains untouched."""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from . import models as m
from .contracts import digest
from .services import scoped,check_version,require,audit,published
from .perception_signals import checked,view


def origin(db,p,signal,*,lock=False):
    basis=checked(db,p,signal,allow_rejected=False)
    native=basis.get('original_model_result') or {}
    identity=native.get('task_id')
    if not basis.get('candidate_only') or not native.get('source_key') or not identity:
        raise HTTPException(409,'此线索没有可核对的原生分析任务，请使用已启用的人工业务')
    from .workspace_api import execution
    task,release,business=execution(db,p,identity,lock)
    if task.data['instance_id']!=signal.instance_id or release.id!=basis['release_id'] or signal.entity_id not in task.data['entity_ids']:
        raise HTTPException(409,'原分析任务与线索的实例、版本或对象不一致')
    signature=digest({'task_id':task.id,'package_id':task.package_id,'owner_id':task.owner_id,
        'instance_id':task.data['instance_id'],'release_id':task.data['instance_release_id'],
        'business_id':task.data['business_id'],'entity_ids':task.data['entity_ids'],'creation_hash':task.data['creation_hash']})
    return task,release,business,basis,signature


def assignee(task,release,business):
    if task.status not in {'workflow_waiting','workflow_completed'}:
        raise HTTPException(409,'原任务正在自动处理、已取消或有执行阻塞；本次不插入审核，请核对原任务')
    steps=business['flow']['steps'];runtime=task.data['workflow_runtime']
    current=next((s for s in steps if s['id']==runtime['current_step_id']),None)
    if task.status=='workflow_waiting' and (not current or current['agent']!='人工确认'):
        raise HTTPException(409,'请等待原分析结果保存并进入人工核查')
    selected=current
    if task.status=='workflow_completed':
        for receipt in reversed(runtime['receipts']):
            selected=next((s for s in steps if s['id']==receipt['step_id'] and s['agent']=='人工确认'),None)
            if selected:break
    return (release.data['snapshot']['roles'][selected['role']]['user_id'],selected['id']) if selected else (task.owner_id,None)


def validate_link(db,p,signal,*,lock=False):
    task,release,business,basis,signature=origin(db,p,signal,lock=lock)
    link=signal.data.get('source_task_link')
    if not link or digest(link)!=signal.data.get('source_task_link_hash') or link['task_id']!=task.id or link['source_task_hash']!=signature or link['basis_hash']!=signal.data['basis_hash'] or task.id not in signal.data.get('task_ids',[]):
        raise HTTPException(409,'原分析任务的线索关联依据不一致；人工说明保留')
    if task.status not in {'workflow_waiting','workflow_completed'}:
        raise HTTPException(409,'原任务已取消或尚未完成自动处理，补充审核暂不能提交')
    return task,link


def link(db,p,signal,body):
    require(p,'admin','operator')
    request_hash=digest({**body.model_dump(exclude={'version'}),'actor':p.user_id})
    old=signal.data.get('source_task_link')
    if old:
        if old['request_id']==body.request_id and old['request_hash']==request_hash:return view(db,p,signal)
        raise HTTPException(409,'此线索已保存原任务关联，请打开原任务核查，不能改绑')
    check_version(signal,body.version)
    # Read-only lock: no version, draft, progress or receipt change to the original Task.
    task,release,business,basis,signature=origin(db,p,signal,lock=True)
    if body.task_id!=task.id or body.task_version!=task.version or body.basis_hash!=signal.data['basis_hash']:
        raise HTTPException(409,'原任务或线索版本已变化，请核对后明确关联')
    if any(scoped(db,m.Task,t,p).status!='workflow_cancelled' for t in signal.data.get('task_ids',[])):
        raise HTTPException(409,'此线索已交给其它业务，不应重复关联')
    avoid_duplicate_handoff(db,p,signal)
    actor,step=assignee(task,release,business)
    value={'schema':'native-task-supplementary-review-v1','task_id':task.id,'task_title':task.title,
        'source_task_hash':signature,'basis_hash':signal.data['basis_hash'],'note':signal.data.get('note',''),
        'review_user_id':actor,'review_step_id':step,'actor':p.user_id,'at':m.now().isoformat(),
        'request_id':body.request_id,'request_hash':request_hash,'original_workflow_changed':False}
    signal.version+=1;signal.status='handed_off';signal.data={**signal.data,'source_task_link':value,
        'source_task_link_hash':digest(value),'task_ids':[*signal.data.get('task_ids',[]),task.id]}
    audit(db,p,'signal.original_task_linked',signal.id,{'task_id':task.id});return view(db,p,signal)


def authorize(db,p,link,override):
    require(p,'admin','operator')
    if p.user_id!=link['review_user_id'] and (p.role!='admin' or not override.strip()):
        raise HTTPException(403,'请由原流程负责人核查；管理员代为核查需要说明原因')


def save(db,p,signal,body,*,final=False):
    require(p,'admin','operator')
    operation='decision' if final else 'draft'
    signature=digest({'operation':operation,'actor':p.user_id,'input':body.model_dump(exclude={'version'})})
    requests=signal.data.get('linked_review_requests',[])
    prior=next((r for r in requests if r['request_id']==body.request_id),None)
    if prior:
        if prior['hash']!=signature:raise HTTPException(409,'原保存请求不能更换内容或审核账号')
        return view(db,p,signal)
    check_version(signal,body.version);task,link=validate_link(db,p,signal,lock=True);authorize(db,p,link,body.override_reason)
    if len(requests)>=100:raise HTTPException(409,'本线索审核保存已达100次，原人工记录保留')
    value={'decision':body.decision,'note':body.note,'override_reason':body.override_reason,
        'revision_reason':body.revision_reason,'actor':p.user_id,'at':m.now().isoformat()}
    update={'linked_review_draft':value,'linked_review_requests':[*requests,{'request_id':body.request_id,'hash':signature,'operation':operation,'actor':p.user_id}]}
    if final:
        if not body.decision or not body.note.strip():raise HTTPException(422,'请填写核查结论并选择异常、无异常或证据不足')
        old=signal.data.get('business_review')
        if old and not body.revision_reason.strip():raise HTTPException(422,'修改已保存判断需要填写改判原因，原判断会保留')
        value={**value,'task_id':task.id,'signal_id':signal.id,'basis_hash':signal.data['basis_hash'],
            'source_time_verified':False,'entity_state_updated':False,'device_action_authorized':False,
            'supplementary_review':True,'original_workflow_changed':False}
        memory=m.Memory(workspace_id=p.workspace_id,status='candidate',text='原分析任务补充审核：'+task.title,
            data={'type':'native_signal_review','task_id':task.id,'signal_id':signal.id,'source_task_link':copy.deepcopy(link),
                'perception_signal':copy.deepcopy(signal.data['basis']),'business_review':value,
                'source_clock':'not_inferred','entity_state_updated':False,'auto_publish':False})
        db.add(memory);db.flush();value={**value,'data_receipt_id':memory.id}
        update={**update,'business_review':value,'business_review_history':[*signal.data.get('business_review_history',[]),*([old] if old else [])],'linked_review_draft':None}
        signal.status='reviewed'
    signal.version+=1;signal.data={**signal.data,**update}
    audit(db,p,'signal.native_review_'+operation,signal.id,{'task_id':task.id});return view(db,p,signal)


def listing(db,p,task):
    rows=list(db.scalars(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id==p.workspace_id,
        m.PerceptionSignal.data['source_task_link']['task_id'].as_string()==task.id).order_by(m.PerceptionSignal.created_at.desc()).limit(101)))
    return {'records':[view(db,p,s) for s in rows[:100]],'has_more':len(rows)>100,'original_workflow_changed':False}


def related(db,p,signal):
    """Expose historical duplicate receipts, keeping each note and original evidence."""
    basis=signal.data.get('basis') or {};native=basis.get('original_model_result') or {};watch=basis.get('watch_definition') or {}
    if not native.get('source_key') or not native.get('source_machine_hash'):return []
    event=scoped(db,m.Event,signal.event_id,p)
    rows=db.scalars(select(m.PerceptionSignal).join(m.Event,m.Event.id==m.PerceptionSignal.event_id).join(m.Observation,m.Observation.id==m.Event.data['observation_id'].as_string()).where(
        m.PerceptionSignal.workspace_id==p.workspace_id,m.PerceptionSignal.instance_id==signal.instance_id,m.PerceptionSignal.entity_id==signal.entity_id,m.PerceptionSignal.id!=signal.id,
        m.Event.workspace_id==p.workspace_id,m.Event.kind=='perception.candidate',m.Event.data['watch_id'].as_string()==event.data.get('watch_id'),
        m.Observation.workspace_id==p.workspace_id,m.Observation.data['native_packet']['source_key'].as_string()==native['source_key'],
        m.Observation.data['native_packet']['source_machine_hash'].as_string()==native['source_machine_hash']).order_by(m.PerceptionSignal.created_at).limit(100))
    result=[]
    for row in rows:
        if (row.data.get('basis') or {}).get('watch_definition')!=watch:continue
        tasks=[]
        for identity in row.data.get('task_ids',[]):
            task=scoped(db,m.Task,identity,p)
            if task.status!='workflow_cancelled':tasks.append({'id':task.id,'title':task.title,'status':task.status})
        result.append({'id':row.id,'status':row.status,'tasks':tasks,'note':row.data.get('note','')})
    return result


def handoff_source(db,p,signal,basis,native):
    """Lock original evidence for a new current handoff; grant no linked-review rights."""
    require(p,'admin','operator')
    from .instance_api import active
    from .workspace_api import execution
    from .entity_release_compat import require_entity
    from .native_receipts import checked as checked_native
    instance,target=active(db,p,signal.instance_id)
    if (basis['instance_id']!=instance.id or basis['release_id']!=target.id
        or basis['package_id']!=target.package_id or not basis.get('candidate_only')):
        raise HTTPException(409,'原线索不是当前明确确认的接收版本，不能迁移旧通知')
    entity=scoped(db,m.Entity,signal.entity_id,p)
    require_entity(db,p,instance,target,entity)
    event=scoped(db,m.Event,signal.event_id,p)
    observation=scoped(db,m.Observation,event.data['observation_id'],p)
    record=checked_native(db,p,observation)
    if (observation.data.get('native_instance_id')!=instance.id or observation.entity_id!=entity.id
        or observation.data.get('native_packet')!=native or record['task_id']!=native['task_id']
        or record['key']!=native['source_key'] or not record['integrity_verified']):
        raise HTTPException(409,'原分析结果、归集回执或线索出处不一致')
    task,release,_=execution(db,p,native['task_id'],True)
    published(db,p,task.package_id,'domain_package')
    if (task.data['instance_id']!=instance.id or release.instance_id!=instance.id
        or task.data['instance_release_id']!=release.id or task.package_id!=release.package_id
        or entity.id not in task.data['entity_ids']):
        raise HTTPException(409,'原分析任务的实例、对象或来源版本不一致')
    return task


def avoid_duplicate_handoff(db,p,signal):
    native=((signal.data.get('basis') or {}).get('original_model_result') or {})
    if native.get('task_id'):
        basis=checked(db,p,signal,allow_rejected=False)
        task=scoped(db,m.Task,native['task_id'],p,True)
        if task.data['instance_release_id']==basis['release_id']:
            origin(db,p,signal,lock=True)
        else:
            handoff_source(db,p,signal,basis,native)
    if any(row['tasks'] for row in related(db,p,signal)):
        raise HTTPException(409,'同一原处理的历史通知已交给业务，请打开已关联任务，不重复安排审核')
