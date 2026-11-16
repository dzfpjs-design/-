"""Durable stop-only monitoring. It never approves or dispatches a candidate."""
from copy import deepcopy
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from .models import Audit,Job,now,uid
from .services import audit,scoped
from .threshold_policy_models_v152 import ThresholdPolicyGrant as Grant,ThresholdPolicySelection as Selection
from .threshold_policy_rules_v153 import monitor_statistics
from . import threshold_policy_execution_v153 as execution

KIND='check_threshold_policy_v153'
SCOPE='threshold-policy-stop-v153'
QUEUED='threshold_policy.monitor_queued_v153'
CHECKED='threshold_policy.monitor_checked_v153'
BASIS_KEYS={'schema','kind','policy_id','selection_id','invocation','feedbacks','selections','statistics','checked_at','grant_pin'}


def _store():return execution._store()
def _need(value,message=execution.ERROR):return execution._need(value,message)
def _hash(value):return execution._hash(value)
def _same(a,b):return execution._same(a,b)


def queue_monitor(db,p,g,trigger):
    _need(type(trigger) is dict and trigger.get('kind') in {'expiry','selection','feedback'})
    identity='policy-watch:'+g.id+':'+_hash(trigger)
    previous=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==identity))
    if previous is not None:return previous
    available=datetime.fromisoformat(g.data['expires_at']) if trigger['kind']=='expiry' else now()
    job=Job(id=uid(),workspace_id=p.workspace_id,kind=KIND,idempotency_key=identity,available_at=available,
        data={'actor_id':g.actor_id,'policy_id':g.id,'internal_scope':SCOPE,'trigger':deepcopy(trigger)})
    db.add(job);db.flush()
    audit(db,p,QUEUED,job.id,{'schema':'threshold-policy-monitor-queue-v153','job_id':job.id,
        'policy_id':g.id,'actor_id':g.actor_id,'trigger':deepcopy(trigger),'available_at':available.isoformat(),
        'policy_pin':execution.grant_pin(g)})
    return job


def ensure_monitor(db,p,g):
    return queue_monitor(db,p,g,{'kind':'expiry','expires_at':g.data['expires_at']})


def enqueue_feedback(db,p,feedback):
    if feedback.status!='submitted':return
    selections=list(db.scalars(select(Selection).where(Selection.workspace_id==p.workspace_id,
        Selection.observation_id==feedback.observation_id)))
    _need(len(selections)<=1)
    for row in selections:
        if row.choice!='candidate':continue
        g=scoped(db,Grant,row.policy_id,p)
        queue_monitor(db,p,g,{'kind':'feedback','feedback_id':feedback.id,'version':feedback.version,'record_hash':feedback.data['record_hash']})


def _feedback(db,p,row,g):
    from . import threshold_evolution_api_v147 as feedback
    from .threshold_evolution_models_v147 import FeedbackRevision,ThresholdCommand
    from .threshold_feedback_history_v148 import inventory_chain
    scope=g.data['scope']
    records=list(db.scalars(select(FeedbackRevision).where(FeedbackRevision.workspace_id==p.workspace_id,
        FeedbackRevision.observation_id==row.observation_id,
        FeedbackRevision.baseline_capability_id==scope['candidate_capability_id'],
        FeedbackRevision.rule_id==scope['rule_id']).order_by(FeedbackRevision.revision)))
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==feedback.COMMAND_AUDIT,
        Audit.data['observation_id'].as_string()==row.observation_id,
        Audit.data['baseline_capability_id'].as_string()==scope['candidate_capability_id'],
        Audit.data['rule_id'].as_string()==scope['rule_id'])))
    commands=list(db.scalars(select(ThresholdCommand).where(ThresholdCommand.workspace_id==p.workspace_id,
        ThresholdCommand.data['result_snapshot']['observation_id'].as_string()==row.observation_id,
        ThresholdCommand.data['result_snapshot']['baseline_capability_id'].as_string()==scope['candidate_capability_id'],
        ThresholdCommand.data['result_snapshot']['rule_id'].as_string()==scope['rule_id'])))
    roots={r.root_id for r in records}
    audit_roots={feedback._checked_audit(p,item)['root_id'] for item in audits}
    command_roots={feedback._checked_command(db,p,item)['root_id'] for item in commands}
    _need(len(roots)<=1 and roots==audit_roots==command_roots,
        '原人工判断与命令审计库存不完整；不会将丢失记录当作未标注')
    if not roots:return None
    chain=inventory_chain(db,p,next(iter(roots)),False)
    submitted=[r for r in chain if r.status=='submitted']
    if not submitted:return None
    result=submitted[-1];basis=result.data['basis'];scope=g.data['scope'];projection=execution.project(scope,row.data['original_request']['attributes'])
    _need(basis['observation_id']==row.observation_id and basis['baseline_capability_id']==scope['candidate_capability_id']
        and basis['rule_id']==scope['rule_id'] and basis['field']==scope['field']
        and basis['instance_id']==scope['instance_id'] and basis['release_id']==scope['release_id']
        and basis['entity_id']==scope['entity_id'] and basis['source_id']==scope['source_id']
        and _same(basis['actual_value'],projection['value']) and basis['actual_hit'] is row.data['result']['actual_matched'])
    return {'id':result.id,'version':result.version,'record_hash':result.data['record_hash'],'observation_id':row.observation_id,
        'label':result.data['label'],'selection':execution.selection_pin(row)}


def _samples(db,p,g,*,_verified_rows=None):
    rows=execution._selection_records(db,p)[0] if _verified_rows is None else _verified_rows
    selected=[r for r in rows if r.policy_id==g.id and r.choice=='candidate']
    samples=[];feedbacks=[]
    for row in selected:
        result=row.data['result'];feedback=_feedback(db,p,row,g) if row.status=='completed' else None
        if feedback is not None:feedbacks.append(feedback)
        samples.append({'status':row.status,'label':feedback['label'] if feedback else None,
            'actual_matched':result['actual_matched'] if result else None,
            'baseline_projection_matched':result['baseline_projection_matched'] if result else None})
    return selected,feedbacks,monitor_statistics(samples,g.data['criteria'])


def _historical_selection(db,p,pin,chains):
    _need(type(pin) is dict and set(pin)=={'id','version','record_hash'})
    matches=[a.data['snapshot'] for a in chains.get(pin['id'],[]) if a.data['version']==pin['version'] and a.data['record_hash']==pin['record_hash']]
    _need(len(matches)==1);return matches[0]


def validate_monitor_basis(db,p,g,basis,*,_read_inventory=None):
    """Validate saved stop evidence, not today's eligibility or today's labels."""
    _need(type(basis) is dict and set(basis)==BASIS_KEYS and basis['schema']=='threshold-policy-monitor-basis-v153'
        and basis['policy_id']==g.id and basis['kind'] in {'technical_failure','feedback_regression','expired','quota_exhausted'})
    instant=datetime.fromisoformat(basis['checked_at']);_need(instant.tzinfo is not None)
    pin=basis['grant_pin'];_need(type(pin) is dict and set(pin)=={'id','version','record_hash','epoch'} and pin['id']==g.id)
    states=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==_store().STATE_ACTION,Audit.object_id==g.id)))
    previous=[a.data['snapshot'] for a in states if a.data.get('version')==pin['version'] and a.data.get('record_hash')==pin['record_hash']]
    _need(len(previous)==1 and previous[0]['status'] in {'active','paused'} and previous[0]['data']['epoch']==pin['epoch'])
    if _read_inventory is None:rows,chains=execution._selection_records(db,p)
    else:
        from .threshold_policy_read_inventory_v155 import scoped_read_inventory
        read=scoped_read_inventory(_read_inventory,db,p)
        read.pin_rows(db,p,states)
        rows,chains=read.selection_data(db,p)
    saved=[_historical_selection(db,p,x,chains) for x in basis['selections']]
    _need(len({r['id'] for r in saved})==len(saved) and all(r['policy_id']==g.id and r['choice']=='candidate' for r in saved))
    if basis['kind']=='expired':
        _need(instant>=datetime.fromisoformat(previous[0]['data']['expires_at']) and not saved and basis['selection_id'] is None
            and basis['invocation'] is None and basis['feedbacks']==[] and basis['statistics'] is None);return
    if basis['kind']=='quota_exhausted':
        _need(len(saved)>=previous[0]['data']['max_candidate_inputs'] and all(r['status'] in {'completed','failed','stopped'} for r in saved)
            and basis['selection_id'] is None and basis['invocation'] is None and basis['feedbacks']==[] and basis['statistics'] is None);return
    if basis['kind']=='technical_failure':
        _need(len(saved)==1 and saved[0]['id']==basis['selection_id'] and saved[0]['status']=='failed'
            and saved[0]['data']['grant_pin']['epoch']==pin['epoch'] and basis['feedbacks']==[] and basis['statistics'] is None)
        call=scoped(db,execution.Invocation,basis['invocation']['id'],p)
        _need(_same(basis['invocation'],execution._call_pin(call)) and call.status in {'succeeded','failed','unknown','not_executed','blocked_evidence','blocked_model'}
            and _same(saved[0]['data']['invocation'],basis['invocation']));return
    _need(saved and basis['selection_id'] is None and basis['invocation'] is None)
    from .threshold_evolution_models_v147 import FeedbackRevision
    from .threshold_feedback_history_v148 import inventory_chain
    labelled={};by_id={r['id']:r for r in saved}
    for record in basis['feedbacks']:
        _need(set(record)=={'id','version','record_hash','observation_id','label','selection'})
        row=scoped(db,FeedbackRevision,record['id'],p);chain=inventory_chain(db,p,row.root_id,False)
        _need(any(r.id==row.id for r in chain) and row.status=='submitted' and row.version==record['version']
            and row.data['record_hash']==record['record_hash'] and row.observation_id==record['observation_id'] and row.data['label']==record['label'])
        sample=_historical_selection(db,p,record['selection'],chains)
        _need(sample['id'] in by_id and sample['status']=='completed' and _same(sample,by_id[sample['id']])
            and sample['observation_id']==row.observation_id and row.observation_id not in labelled)
        scope=previous[0]['data']['scope'];original=sample['data']['original_request'];basis_value=row.data['basis']
        _need(basis_value['baseline_capability_id']==scope['candidate_capability_id']
            and basis_value['rule_id']==scope['rule_id'] and basis_value['field']==scope['field']
            and all(basis_value[key]==scope[key] for key in ('instance_id','release_id','entity_id','source_id'))
            and _same(basis_value['actual_value'],original['attributes'][scope['field']])
            and basis_value['actual_hit'] is sample['data']['result']['actual_matched'])
        labelled[row.observation_id]=record['label']
    samples=[{'status':r['status'],'label':labelled.get(r['observation_id']),
        'actual_matched':r['data']['result']['actual_matched'] if r['data']['result'] else None,
        'baseline_projection_matched':r['data']['result']['baseline_projection_matched'] if r['data']['result'] else None} for r in saved]
    actual=monitor_statistics(samples,previous[0]['data']['criteria'])
    _need(actual['regressed'] and _same(actual,basis['statistics']))


def _basis(db,p,g,rows,feedbacks,statistics):
    moment=now();common={'schema':'threshold-policy-monitor-basis-v153','policy_id':g.id,'selection_id':None,
        'invocation':None,'feedbacks':[],'selections':[],'statistics':None,'checked_at':moment.isoformat(),'grant_pin':execution.grant_pin(g)}
    if moment>=datetime.fromisoformat(g.data['expires_at']):return {**common,'kind':'expired'}
    failed=[r for r in rows if r.status=='failed' and r.data['grant_pin']['epoch']==g.data['epoch'] and r.data['invocation'] is not None]
    if g.status=='active' and failed:
        first=sorted(failed,key=lambda r:r.id)[0]
        return {**common,'kind':'technical_failure','selection_id':first.id,'selections':[execution.selection_pin(first)],'invocation':deepcopy(first.data['invocation'])}
    if statistics['regressed']:
        return {**common,'kind':'feedback_regression','selections':[execution.selection_pin(r) for r in rows],
            'feedbacks':deepcopy(feedbacks),'statistics':statistics}
    if len(rows)>=g.data['max_candidate_inputs'] and all(r.status in {'completed','failed','stopped'} for r in rows):
        return {**common,'kind':'quota_exhausted','selections':[execution.selection_pin(r) for r in rows]}
    return None


def process(db,p,job):
    _need(job.kind==KIND and job.data.get('internal_scope')==SCOPE)
    g=_store().get_grant(db,p,job.data['policy_id'],True,check_selections=False)
    queues=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==QUEUED,Audit.object_id==job.id)))
    _need(len(queues)==1);entry=queues[0].data
    _need(entry['policy_id']==g.id and entry['job_id']==job.id and entry['actor_id']==job.data['actor_id']==g.actor_id
        and _same(entry['trigger'],job.data['trigger']) and job.idempotency_key=='policy-watch:'+g.id+':'+_hash(job.data['trigger']))
    rows,feedbacks,statistics=_samples(db,p,g);basis=None
    if g.status!='rolled_back':basis=_basis(db,p,g,rows,feedbacks,statistics)
    if basis is not None:
        reasons={'technical_failure':'实际候选调用、结果保存失败或结果未知，已自动停止并回滚后续输入',
            'feedback_regression':'明确人工判断显示候选误漏报退化，已自动停止并回滚后续输入',
            'expired':'有限候选授权已到期，后续输入重新核对原基线',
            'quota_exhausted':'候选额度已用完且全部占用已结束，后续输入重新核对原基线'}
        reason=reasons[basis['kind']];g.status='rolled_back';g.data={**g.data,'reason':reason,'rollback':{
            'reason':reason,'origin':'monitor','selection_id':basis['selection_id'],'basis':basis}}
        _store().seal_grant(db,p,g,'auto_rollback',monitor_basis=basis)
    checked=now().isoformat();result={'policy_id':g.id,'checked_at':checked,'statistics':statistics,'stopped':basis is not None}
    audit(db,p,CHECKED,g.id,{'schema':'threshold-policy-monitor-check-v153','job_id':job.id,**deepcopy(result)})
    job.data={**job.data,'result':result};return result


def monitor_view(db,p,g,*,_readiness=None):
    issue=None;statistics=None
    try:
        if _readiness is None:
            _store().fresh_candidate(db,p,g,lock=False)
            _,_,statistics=_samples(db,p,g)
        else:
            _need(_readiness.readable(db,p,g,'candidate'))
            _,_,statistics=_samples(db,p,g,_verified_rows=_readiness.rows)
            _readiness.check(db,p,g)
    except (HTTPException,ValueError,TypeError,KeyError):issue='当前候选或人工判断依据不可核对；历史和停止记录保留'
    checks=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==CHECKED,Audit.object_id==g.id).order_by(Audit.created_at.desc()).limit(1)))
    rollback=g.data['rollback'];stop=None if rollback is None else {'reason':rollback['reason'],'origin':rollback['origin']}
    return {'statistics':statistics,'issue':issue,'last_check_at':checks[0].data['checked_at'] if checks else None,'last_stop':stop}
