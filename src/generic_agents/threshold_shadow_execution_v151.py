"""Future-input local shadow outbox. Never changes the formal observation path."""
from copy import deepcopy
from datetime import datetime
import math

from fastapi import HTTPException
from sqlalchemy import func, select

from .models import Audit, Invocation, Job, Membership, Observation, User, uid
from .services import audit
from .typed_payload_codec_v131 import canonical_json

KIND = 'process_threshold_shadow_v151'
STATE = 'threshold_shadow.consumption_state_v151'
ENQUEUE = 'threshold_shadow.enqueued_v151'
ERROR = '原影子观察的接纳、后台任务或真实调用库存无法核对；原记录保留，请核查'
_FIELDS = ('id','workspace_id','grant_id','observation_id','source_record_id','job_id',
           'actor_id','version','status','data')
_DATA = {'schema','epoch','grant_version','scope_hash','original_input_hash',
         'registration_audit_id','parent_job_id','parent_extractor_id','admission_lease_hash',
         'basis','reservation','receipt','error','late'}


def _store():
    from . import threshold_shadow_store_v151
    return threshold_shadow_store_v151


def _need(condition, message=ERROR):
    if not condition:
        if message==ERROR:raise _store().ShadowInventoryError(ERROR)
        raise HTTPException(409,message)


def _hash(value): return _store().hash_value(value)
def _same(left, right): return canonical_json(left) == canonical_json(right)


def _snapshot(row):
    return {key:deepcopy(getattr(row,key)) for key in _FIELDS}


def _seal(db,p,row,operation,*,initial=False):
    if not initial: row.version += 1
    row.data=deepcopy(row.data);row.record_hash=_hash(_snapshot(row))
    audit(db,p,STATE,row.id,{'schema':'threshold-shadow-consumption-state-v151',
        'grant_id':row.grant_id,'version':row.version,'record_hash':row.record_hash,
        'operation':operation,'snapshot':_snapshot(row)})


def _rows(db,statement):
    return list(db.scalars(statement.execution_options(populate_existing=True)))


def _one(db,p,model,identity):
    row=db.scalar(select(model).where(model.workspace_id==p.workspace_id,
        model.id==identity).execution_options(populate_existing=True))
    _need(row is not None);return row


def _live_submitter(db,p,observation):
    _need(type(observation.data) is dict)
    actor=observation.data.get('submitted_by')
    _need(db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(
        User.id==actor,User.active.is_(True),Membership.workspace_id==p.workspace_id,
        Membership.role.in_(['admin','operator'])).with_for_update(read=True,of=[User,Membership])) is not None,
        '原登记人员当前无执行权限，停止新的影子调用；原正式记录保留')


def _scope_observation(db,p,grant,observation,*,future=True):
    scope=grant.data['scope'];data=observation.data
    _need(type(data) is dict)
    original=data.get('original_request')
    _need(type(original) is dict and data.get('input_hash')==_hash(original))
    contract=data.get('reported_rule_contract')
    _need(type(contract) is dict and contract.get('instance_id')==scope['instance_id']
        and contract.get('release_id')==scope['release_id']
        and contract.get('entity_id')==scope['entity_id']
        and observation.entity_id==scope['entity_id'] and observation.source_id==scope['source_id']
        and data.get('extractor_id')==scope['baseline_id']
        and contract.get('source_hash')==scope['source_hash']
        and contract.get('extractor_hash')==scope['baseline_hash'])
    _need(all(_same(original.get(k),getattr(observation,k)) for k in
        ('source_id','source_record_id','processing_version','entity_id','business_day')))
    _need(original.get('extractor_id')==scope['baseline_id']
        and original.get('startup_mode') in {'manual','automatic'}
        and original.get('evidence_ids',[])==[] and original.get('sensor_asset_id') is None
        and original.get('acquisition_id') is None and original.get('sensor_modality') is None)
    values=original.get('attributes')
    _need(type(values) is dict and set(values)=={scope['field']}
        and _same(values,data.get('attributes'))
        and type(values[scope['field']]) in {int,float} and math.isfinite(values[scope['field']]))
    ingests=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.object_id==observation.id,Audit.action=='observation.ingest'))
    _need(len(ingests)==1 and ingests[0].actor_id==data.get('submitted_by'))
    if future:
        # Check the entire original source identity inventory, not only the
        # successful consumers. A queued/failed first processing version is
        # still an old registration and cannot be replaced with another UUID.
        _store().frontier(db,p,scope)
        identities=_rows(db,select(Observation).where(Observation.workspace_id==p.workspace_id,
            Observation.source_id==scope['source_id'],
            Observation.source_record_id==observation.source_record_id).limit(2))
        _need(len(identities)==1 and identities[0].id==observation.id,
            '同一原来源键存在多个登记或处理版本，不能挑选其中一版进入影子观察；正式记录保留')
        frontier=grant.data['frontier']
        _need(observation.id not in frontier['observation_ids']
            and observation.source_record_id not in frontier['source_record_ids'],
            '此原来源记录在批准或恢复前已经登记，不能换编号、处理版本或日期进入影子观察')
    return values,ingests[0]


def _active(db,grant):
    _need(grant.status=='active','影子观察已暂停或结束，不会安排新的候选调用')
    expires=datetime.fromisoformat(grant.data['expires_at'])
    _need(expires.tzinfo is not None and expires>db.scalar(select(func.clock_timestamp())),
        '影子观察已到期，原输入与回执保留')


def _job_context(p,grant,row,job):
    data=job.data
    _need(type(data) is dict)
    _need(job.workspace_id==p.workspace_id==grant.workspace_id==row.workspace_id
        and job.kind==KIND and job.id==row.job_id
        and job.idempotency_key==f'threshold-shadow:{grant.id}:{row.observation_id}'
        and data.get('schema')=='threshold-shadow-job-v151'
        and data.get('shadow_grant_id')==grant.id and data.get('consumption_id')==row.id
        and data.get('observation_id')==row.observation_id
        and data.get('parent_job_id')==row.data['parent_job_id']
        and data.get('actor_id')==row.actor_id
        and type(data.get('shadow_epoch')) is int and data['shadow_epoch']==row.data['epoch']
        and type(data.get('shadow_grant_version')) is int
        and data['shadow_grant_version']==row.data['grant_version'])


def _checked_receipt(db,p,grant,row):
    reservation=row.data['reservation'];receipt=row.data['receipt']
    key=f'threshold-shadow-call:{grant.id}:{row.id}'
    calls=_rows(db,select(Invocation).where(Invocation.workspace_id==p.workspace_id,
        Invocation.idempotency_key==key))
    if reservation is None:
        _need(not calls and receipt is None and row.status!='reserved');return None
    scope=grant.data['scope'];basis=row.data['basis']
    _need(type(basis) is dict and type(reservation) is dict and set(reservation)=={
        'key','capability_id','capability_hash','input_hash','job_id','lease_token_hash'})
    inputs={'attributes':{scope['field']:basis['actual_value']}}
    _need(len(calls)==1 and reservation['key']==key and reservation['job_id']==row.job_id
        and reservation['capability_id']==scope['candidate_capability_id']
        and reservation['capability_hash']==scope['candidate_hash']
        and reservation['input_hash']==_hash(inputs))
    call=calls[0]
    _need(call.capability_id==reservation['capability_id'] and type(call.data) is dict
        and call.status in {'running','unknown','failed','blocked_evidence','blocked_model','succeeded','not_executed'}
        and call.data.get('role')=='perception' and call.data.get('side_effect') is False
        and call.data.get('approval') is None and call.data.get('job_id')==row.job_id
        and call.data.get('input_hash')==reservation['input_hash']
        and call.data.get('capability_hash')==reservation['capability_hash']
        and type(call.data.get('lease_token')) is str
        and _hash(call.data['lease_token'])==reservation['lease_token_hash'])
    if receipt is not None:
        _need(type(receipt) is dict and set(receipt)=={'invocation_id','output','output_hash','lease_token_hash'}
            and call.status=='succeeded' and receipt['invocation_id']==call.id
            and receipt['lease_token_hash']==reservation['lease_token_hash']
            and receipt['output_hash']==_hash(receipt['output'])
            and _same(receipt['output'],call.data.get('output')))
        _checked_output(grant,basis,call)
    _need((row.status=='completed')==(receipt is not None and row.data['late'] is False))
    return call


def _checked_output(grant,basis,call):
    scope=grant.data['scope'];manifest=grant.data['evaluation']['freeze']['candidate']['candidate_manifest']
    rule=manifest['specification']['rules'][0];value=basis['actual_value']
    matched=value>rule['threshold'] if rule['operator']=='gt' else value<rule['threshold']
    values={scope['field']:value}
    expected={'attributes':values,'anomalies':([{'field':rule['field'],'value':value,
        'rule_id':rule['id'],'severity':rule.get('severity','warning')}] if matched else []),
        'method':'registered_numeric_rules'}
    _need(call.status=='succeeded' and _same(call.data.get('output'),expected));return matched


def _stored_basis(grant,row):
    """Saved proof content, independent of today's source availability/version."""
    basis=row.data['basis']
    if basis is None:
        _need(row.data['reservation'] is None);return None
    fields={'schema','observation_id','baseline_capability_id','baseline_capability_hash','rule_id',
        'field','field_label','unit','operator','threshold','actual_value','actual_hit','instance_id',
        'release_id','entity_id','source_id','proof','basis_hash','observation_version'}
    _need(type(basis) is dict and set(basis)==fields
        and basis['schema']=='threshold-evolution-feedback-basis-v147'
        and type(basis['observation_version']) is int and basis['observation_version']>=1)
    stable={k:v for k,v in basis.items() if k not in {'basis_hash','observation_version'}}
    _need(basis['basis_hash']==_hash(stable));scope=grant.data['scope'];proof=basis['proof']
    _need(type(proof) is dict and proof.get('schema')=='threshold-evolution-machine-proof-v147'
        and proof.get('workspace_id')==row.workspace_id
        and basis['observation_id']==row.observation_id
        and all(_same(basis[k],scope[k]) for k in ('instance_id','release_id','entity_id','source_id','field','unit','operator','rule_id'))
        and basis['baseline_capability_id']==scope['baseline_id']
        and basis['baseline_capability_hash']==scope['baseline_hash']
        and _same(basis['threshold'],scope['baseline_threshold'])
        and type(basis['actual_value']) in {int,float} and math.isfinite(basis['actual_value'])
        and type(basis['actual_hit']) is bool)
    original=proof.get('original_request');source=proof.get('source');extractor=proof.get('extractor')
    frozen=grant.data['evaluation']['freeze']['candidate']
    _need(type(original) is dict and _hash(original)==row.data['original_input_hash']
        and proof.get('original_input_hash')==row.data['original_input_hash']
        and original.get('source_id')==scope['source_id']
        and original.get('source_record_id')==row.source_record_id
        and original.get('entity_id')==scope['entity_id'] and original.get('extractor_id')==scope['baseline_id']
        and _same(original.get('attributes'),{scope['field']:basis['actual_value']})
        and type(source) is dict and source.get('id')==scope['source_id']
        and source.get('hash')==scope['source_hash']==_hash(source.get('manifest'))
        and type(extractor) is dict and extractor.get('id')==scope['baseline_id']
        and extractor.get('hash')==scope['baseline_hash']
        and _same(extractor.get('manifest'),frozen['baseline_manifest']))
    value=basis['actual_value'];threshold=scope['baseline_threshold']
    _need(basis['actual_hit']==(value>threshold if scope['operator']=='gt' else value<threshold))
    return stable


def _current_machine(db,p,grant,row,observation):
    """Current formal-source eligibility is a display/dispatch gate, not an Audit."""
    _scope_observation(db,p,grant,observation,future=False)
    from .threshold_evolution_basis_v147 import feedback_basis
    current=feedback_basis(db,p,row.observation_id,grant.data['scope']['rule_id'])
    # observation_version is explicitly informational in V147 and excluded
    # from its original basis_hash. Never re-sign or update the saved proof.
    stable={k:v for k,v in current.items() if k not in {'basis_hash','observation_version'}}
    _need(current['basis_hash']==row.data['basis']['basis_hash']
        and _same(stable,_stored_basis(grant,row)))
    return current


def consumption_inventory(db,p,grant):
    """Verify both directions of actual rows, state Audits, Jobs and calls."""
    from .threshold_shadow_models_v151 import ThresholdShadowGrant as Grant, ThresholdShadowConsumption as Consumption
    # Full workspace inventory exposes orphan Audits even when their target and
    # Job disappear together. A JSON grant_id alone is never an inventory root.
    grants=_rows(db,select(Grant).where(Grant.workspace_id==p.workspace_id));grant_ids={g.id for g in grants}
    all_rows=_rows(db,select(Consumption).where(Consumption.workspace_id==p.workspace_id)
        .order_by(Consumption.created_at,Consumption.id));by_id={r.id:r for r in all_rows}
    _need(all(type(r.data) is dict for r in all_rows))
    all_states=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE))
    all_queued=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==ENQUEUE))
    all_jobs=_rows(db,select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND))
    all_calls=_rows(db,select(Invocation).where(Invocation.workspace_id==p.workspace_id,
        Invocation.idempotency_key.startswith('threshold-shadow-call:')))
    _need(all(r.grant_id in grant_ids for r in all_rows))
    for issued in all_states+all_queued:
        _need(issued.object_id in by_id and type(issued.data) is dict
            and issued.data.get('grant_id')==by_id[issued.object_id].grant_id)
    by_job_id={r.job_id:r for r in all_rows}
    _need({j.id for j in all_jobs}==set(by_job_id))
    for actual in all_jobs:
        _need(type(actual.data) is dict and actual.data.get('shadow_grant_id')==by_job_id[actual.id].grant_id
            and actual.data.get('consumption_id')==by_job_id[actual.id].id)
    for actual in all_calls:
        pieces=actual.idempotency_key.split(':')
        _need(len(pieces)==3 and pieces[1] in grant_ids and pieces[2] in by_id
            and by_id[pieces[2]].grant_id==pieces[1]
            and type(by_id[pieces[2]].data.get('reservation')) is dict
            and by_id[pieces[2]].data['reservation'].get('key')==actual.idempotency_key)
    rows=[r for r in all_rows if r.grant_id==grant.id]
    states=[a for a in all_states if a.data['grant_id']==grant.id]
    queued=[a for a in all_queued if a.data['grant_id']==grant.id]
    jobs=[j for j in all_jobs if j.data['shadow_grant_id']==grant.id]
    calls=[c for c in all_calls if c.idempotency_key.startswith('threshold-shadow-call:'+grant.id+':')]
    _need(len(rows)<=grant.data['max_inputs'] and len({r.source_record_id for r in rows})==len(rows)
        and len({r.job_id for r in rows})==len(rows) and {j.id for j in jobs}=={r.job_id for r in rows}
        and {a.object_id for a in states}=={r.id for r in rows}
        and {a.object_id for a in queued}=={r.id for r in rows} and len(queued)==len(rows))
    by_job={j.id:j for j in jobs};keys=set();result=[]
    for row in rows:
        _need(type(row.version) is int and row.version>=1 and type(row.data) is dict
            and set(row.data)==_DATA and row.data['schema']=='threshold-shadow-consumption-record-v151'
            and row.record_hash==_hash(_snapshot(row)) and row.workspace_id==grant.workspace_id
            and type(row.data['epoch']) is int and row.data['epoch']>=1
            and type(row.data['grant_version']) is int and row.data['grant_version']>=1
            and type(row.data['late']) is bool and row.data['scope_hash']==_hash(grant.data['scope']))
        history=[a for a in states if a.object_id==row.id]
        _need(len(history)==row.version)
        versions=[]
        for issued in history:
            d=issued.data
            _need(type(d) is dict and set(d)=={'schema','grant_id','version','record_hash','operation','snapshot'}
                and d['schema']=='threshold-shadow-consumption-state-v151' and d['grant_id']==grant.id
                and type(d['version']) is int and d['version']>=1
                and issued.actor_id==row.actor_id and type(d['snapshot']) is dict
                and set(d['snapshot'])==set(_FIELDS) and type(d['snapshot']['version']) is int
                and d['snapshot']['version']==d['version'] and d['snapshot']['id']==row.id
                and d['snapshot']['grant_id']==grant.id and d['snapshot']['actor_id']==row.actor_id
                and d['snapshot']['workspace_id']==p.workspace_id
                and d['record_hash']==_hash(d['snapshot']))
            versions.append(d['version'])
            if d['version']==row.version:_need(_same(d['snapshot'],_snapshot(row)))
        _need(sorted(versions)==list(range(1,row.version+1)))
        job=by_job[row.job_id];_job_context(p,grant,row,job)
        observation=_one(db,p,Observation,row.observation_id)
        parent=_one(db,p,Job,row.data['parent_job_id'])
        _need(parent.kind=='process_reported_observation' and parent.status=='succeeded'
            and type(parent.data) is dict
            and parent.data.get('observation_id')==observation.id
            and parent.data.get('extractor_id')==row.data['parent_extractor_id']==grant.data['scope']['baseline_id']
            and parent.data.get('actor_id')==row.actor_id
            and _hash(parent.lease_token)==row.data['admission_lease_hash'])
        issued=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,
            Audit.action==ENQUEUE,Audit.object_id==row.id))
        _need(len(issued)==1 and issued[0].actor_id==row.actor_id and _same(issued[0].data,{
            'schema':'threshold-shadow-enqueue-v151','grant_id':grant.id,'consumption_id':row.id,
            'observation_id':row.observation_id,'job_id':row.job_id,'parent_job_id':parent.id,
            'epoch':row.data['epoch'],'grant_version':row.data['grant_version'],
            'source_record_id':row.source_record_id,'original_input_hash':row.data['original_input_hash']}))
        _stored_basis(grant,row)
        call=_checked_receipt(db,p,grant,row)
        if row.data['reservation'] is not None:keys.add(row.data['reservation']['key'])
        basis=row.data['basis'];receipt=row.data['receipt'];values=None;baseline=None;candidate=None
        current_issue=None
        if basis is not None:
            try:
                _current_machine(db,p,grant,row,observation)
                values={basis['field']:deepcopy(basis['actual_value'])};baseline=basis['actual_hit']
                if receipt is not None:candidate=_checked_output(grant,basis,call)
            except HTTPException as error:
                if error.status_code not in {403,404,409,422}:raise
                current_issue='原正式登记或来源当前不可用于这些机器数值；实际调用与人工作业保留，可暂停或结束'
        result.append({'schema':'threshold-shadow-consumption-view-v151','id':row.id,
            'observation_id':row.observation_id,'source_record_id':row.source_record_id,'status':row.status,
            'job_id':job.id,'job_status':job.status,'error':row.data['error'] or current_issue,'values':values,
            'baseline_matched':baseline,'candidate_matched':candidate,
            'invocation_id':call.id if call is not None else None,'late':row.data['late'],
            'created_at':row.created_at.isoformat()})
    _need({c.idempotency_key for c in calls}==keys)
    return result


def _matching_grants(candidates,observation):
    """Keep independently approved instance/release scopes independent."""
    _need(type(observation.data) is dict)
    contract=observation.data.get('reported_rule_contract')
    _need(type(contract) is dict and all(type(contract.get(k)) is str
        and contract[k] for k in ('instance_id','release_id')))
    _need(all(type(g.data) is dict and type(g.data.get('scope')) is dict
        and all(type(g.data['scope'].get(k)) is str and g.data['scope'][k]
            for k in ('instance_id','release_id','entity_id','source_id','baseline_id'))
        for g in candidates))
    return [g for g in candidates if
        g.data['scope'].get('instance_id')==contract['instance_id']
        and g.data['scope'].get('release_id')==contract['release_id']
        and g.data['scope'].get('entity_id')==observation.entity_id
        and g.data['scope'].get('source_id')==observation.source_id
        and g.data['scope'].get('baseline_id')==observation.data.get('extractor_id')]


def enqueue_completed(db,p,parent_job):
    """Called in the original success transaction, before its final status flush."""
    from .threshold_shadow_models_v151 import ThresholdShadowGrant as Grant, ThresholdShadowConsumption as Consumption
    if parent_job.kind!='process_reported_observation':return False
    observation=_one(db,p,Observation,parent_job.data['observation_id'])
    if observation.status!='processed':return False
    _need(type(observation.data) is dict)
    candidates=_rows(db,select(Grant).where(Grant.workspace_id==p.workspace_id,Grant.status=='active'))
    _need(len(candidates)<=100,'影子授权范围过多，正式输入已保留，请先整理授权')
    matches=_matching_grants(candidates,observation)
    _need(len(matches)<=1,'同一原输入存在冲突影子授权，停止候选计算，正式处理保留')
    if not matches:return False
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    store=_store();grant=store.get_grant(db,p,matches[0].id,lock=True)
    store.fresh_grant(db,p,grant);_active(db,grant);_live_submitter(db,p,observation)
    _,issued=_scope_observation(db,p,grant,observation)
    previous=consumption_inventory(db,p,grant)
    _need(not any(r['source_record_id']==observation.source_record_id for r in previous),
        '此原来源记录已占用过影子额度，不能通过新处理版本再次进入')
    _need(len(previous)<grant.data['max_inputs'],'影子观察额度已用完；正式处理保留，不再安排候选')
    _need(parent_job.data.get('actor_id')==p.user_id and type(parent_job.lease_token) is str
        and parent_job.status=='running' and parent_job.data.get('extractor_id')==grant.data['scope']['baseline_id'])
    identity=uid();child=Job(id=uid(),workspace_id=p.workspace_id,kind=KIND,status='queued',attempts=0,
        idempotency_key=f'threshold-shadow:{grant.id}:{observation.id}',data={
            'schema':'threshold-shadow-job-v151','actor_id':p.user_id,'shadow_grant_id':grant.id,
            'shadow_epoch':grant.data['epoch'],'shadow_grant_version':grant.version,
            'consumption_id':identity,'observation_id':observation.id,'parent_job_id':parent_job.id})
    db.add(child)
    row=Consumption(id=identity,workspace_id=p.workspace_id,grant_id=grant.id,
        observation_id=observation.id,source_record_id=observation.source_record_id,job_id=child.id,
        actor_id=p.user_id,version=1,status='queued',record_hash='',data={
            'schema':'threshold-shadow-consumption-record-v151','epoch':grant.data['epoch'],
            'grant_version':grant.version,'scope_hash':_hash(grant.data['scope']),
            'original_input_hash':observation.data['input_hash'],'registration_audit_id':issued.id,
            'parent_job_id':parent_job.id,'parent_extractor_id':parent_job.data['extractor_id'],
            'admission_lease_hash':_hash(parent_job.lease_token),'basis':None,'reservation':None,
            'receipt':None,'error':None,'late':False})
    db.add(row);_seal(db,p,row,'admit',initial=True)
    audit(db,p,ENQUEUE,row.id,{'schema':'threshold-shadow-enqueue-v151','grant_id':grant.id,
        'consumption_id':row.id,'observation_id':row.observation_id,'job_id':row.job_id,
        'parent_job_id':parent_job.id,'epoch':row.data['epoch'],'grant_version':row.data['grant_version'],
        'source_record_id':row.source_record_id,'original_input_hash':row.data['original_input_hash']})
    return True


def _owned(db,p,job):
    from .threshold_shadow_models_v151 import ThresholdShadowConsumption as Consumption
    grant=_store().get_grant(db,p,job.data['shadow_grant_id'],lock=True,check_consumptions=False)
    consumption_inventory(db,p,grant)
    row=_one(db,p,Consumption,job.data['consumption_id']);_job_context(p,grant,row,job)
    _need(p.user_id==row.actor_id);return grant,row


def _fresh_consumption(db,p,grant,row):
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    _store().fresh_grant(db,p,grant);_active(db,grant)
    _need(grant.version==row.data['grant_version'] and grant.data['epoch']==row.data['epoch'],
        '原接纳后授权已经暂停、恢复或结束；此输入不补追新的候选调用')
    observation=_one(db,p,Observation,row.observation_id)
    _live_submitter(db,p,observation)
    _,issued=_scope_observation(db,p,grant,observation)
    _need(observation.source_record_id==row.source_record_id
        and observation.data.get('input_hash')==row.data['original_input_hash']
        and issued.id==row.data['registration_audit_id'],
        '原接纳输入或登记凭据已经变化，不能替换原预约数据；原记录保留')
    from .threshold_evolution_basis_v147 import feedback_basis
    basis=feedback_basis(db,p,row.observation_id,grant.data['scope']['rule_id'])
    _need(basis['baseline_capability_id']==grant.data['scope']['baseline_id']
        and basis['field']==grant.data['scope']['field'] and basis['unit']==grant.data['scope']['unit'])
    return basis


def _stop(db,p,row,message,*,call=None,grant=None):
    data={**row.data,'error':str(message)[:1800],'late':row.data['reservation'] is not None}
    if call is not None and call.status=='succeeded':
        _checked_output(grant,row.data['basis'],call)
        data['receipt']={'invocation_id':call.id,'output':deepcopy(call.data['output']),
            'output_hash':_hash(call.data['output']),
            'lease_token_hash':row.data['reservation']['lease_token_hash']}
    row.status='stopped';row.data=data;_seal(db,p,row,'stop');db.commit();return 'stopped'


def authorize_gateway(db,p,capability_id,agent_role,inputs,key):
    """A reserved prefix is internal authority, never a user-selected call key."""
    from .leases import ownership
    from .threshold_shadow_models_v151 import ThresholdShadowConsumption as Consumption
    parts=key.split(':');lease=ownership(db)
    _need(len(parts)==3 and parts[0]=='threshold-shadow-call' and lease.get('job_id') is not None,
        '影子调用必须来自原授权的后台预约，不能通过普通工具入口伪造')
    job=_one(db,p,Job,lease['job_id'])
    _need(job.kind==KIND and type(job.data) is dict and job.data.get('actor_id')==p.user_id)
    grant=_store().get_grant(db,p,parts[1],lock=True,check_consumptions=False)
    row=_one(db,p,Consumption,parts[2])
    _job_context(p,grant,row,job)
    _need(row.status=='reserved' and row.data['receipt'] is None and row.actor_id==p.user_id
        and lease.get('lease_token')==job.lease_token and agent_role=='perception'
        and capability_id==grant.data['scope']['candidate_capability_id'])
    reservation=row.data['reservation']
    _need(type(reservation) is dict and reservation['key']==key
        and reservation['input_hash']==_hash(inputs)
        and reservation['lease_token_hash']==_hash(job.lease_token))
    states=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE,
        Audit.object_id==row.id))
    current=[a for a in states if type(a.data.get('version')) is int and a.data['version']==row.version]
    _need(row.record_hash==_hash(_snapshot(row)) and len(current)==1
        and current[0].actor_id==row.actor_id and current[0].data.get('record_hash')==row.record_hash
        and _same(current[0].data.get('snapshot'),_snapshot(row)))
    _fresh_consumption(db,p,grant,row)
    _need(_same(inputs,{'attributes':{row.data['basis']['field']:row.data['basis']['actual_value']}}))


def process(db,p,job):
    from .gateway import invoke
    from .leases import ownership
    grant,row=_owned(db,p,job)
    if row.status in {'completed','failed','stopped'}:return row.status
    call=_checked_receipt(db,p,grant,row)
    try:basis=_fresh_consumption(db,p,grant,row)
    except HTTPException as error:return _stop(db,p,row,error.detail,call=call,grant=grant)
    if row.data['reservation'] is None:
        lease=ownership(db)
        _need(lease.get('job_id')==job.id and lease.get('lease_token')==job.lease_token
            and type(job.lease_token) is str and job.status=='running')
        _need(not job.data.get('recovery_only'),'原任务已达到恢复上限，不安排新的候选调用')
        inputs={'attributes':{basis['field']:deepcopy(basis['actual_value'])}}
        row.status='reserved';row.data={**row.data,'basis':deepcopy(basis),'reservation':{
            'key':f'threshold-shadow-call:{grant.id}:{row.id}',
            'capability_id':grant.data['scope']['candidate_capability_id'],
            'capability_hash':grant.data['scope']['candidate_hash'],'input_hash':_hash(inputs),
            'job_id':job.id,'lease_token_hash':_hash(job.lease_token)}}
        _seal(db,p,row,'reserve')
        # No commit separates reservation from Gateway's original running-call commit.
        _fresh_consumption(db,p,grant,row)
        call=invoke(db,p,row.data['reservation']['capability_id'],'perception',inputs,
            row.data['reservation']['key'])
    _need(call is not None)
    if call.status!='succeeded':
        row.status='failed';row.data={**row.data,'error':'候选调用失败或送达未知，保留已占额度和原回执，不自动重发'}
        _seal(db,p,row,'fail');db.commit();return 'failed'
    grant,row=_owned(db,p,job)
    try:_fresh_consumption(db,p,grant,row)
    except HTTPException as error:return _stop(db,p,row,error.detail,call=call,grant=grant)
    _checked_output(grant,row.data['basis'],call)
    row.status='completed';row.data={**row.data,'receipt':{'invocation_id':call.id,
        'output':deepcopy(call.data['output']),'output_hash':_hash(call.data['output']),
        'lease_token_hash':row.data['reservation']['lease_token_hash']},'error':None,'late':False}
    _seal(db,p,row,'complete');db.commit();return 'completed'


def fail(db,p,job,reason):
    grant,row=_owned(db,p,job)
    if row.status in {'completed','failed','stopped'}:return row.status
    call=_checked_receipt(db,p,grant,row)
    if call is not None and call.status=='succeeded':
        return _stop(db,p,row,'后台处理已停止；实际候选回执仅留历史，不推进正式处理',call=call,grant=grant)
    row.status='failed';row.data={**row.data,'error':str(reason)[:1800]}
    _seal(db,p,row,'fail');db.commit();return 'failed'
