"""Approved deterministic quality checks; containment is distinct from sufficient evidence."""
from datetime import timedelta
from sqlalchemy import select,text,or_,func
from jsonschema import Draft202012Validator
from .models import QualityPolicy,IssueCheck,Issue,Job,Entity,Relation,Capability,Task,DataRun,Memory,now
from .services import scoped,audit
from .contracts import digest
from .security import Principal,require

LIMIT=100


def enqueue(db,policy,trigger,entity_id=None,*,cursor=None,entity_ids=None,root_trigger=None):
    from sqlalchemy.dialects.postgresql import insert
    key='quality:'+policy.id+':'+str(policy.version)+':'+trigger
    db.execute(insert(Job).values(workspace_id=policy.workspace_id,kind='diagnose_data',idempotency_key=key,
        data={'policy_id':policy.id,'policy_version':policy.version,'actor_id':policy.actor_id,
            'trigger':trigger,'root_trigger':root_trigger or trigger,'entity_ids':entity_ids or ([entity_id] if entity_id else None),'cursor':cursor or {}}).on_conflict_do_nothing(
                index_elements=['workspace_id','idempotency_key']))


def changed(db,p,package_id,trigger,entity_id=None):
    from .original_acquisition_reception_v127 import trigger_hold
    if trigger_hold(db,p,trigger,entity_id=entity_id,package_id=package_id):return
    for policy in db.scalars(select(QualityPolicy).where(QualityPolicy.workspace_id==p.workspace_id,
        QualityPolicy.status=='active',QualityPolicy.data['package_ids'].contains([package_id]))):
        enqueue(db,policy,trigger,entity_id)


def schedule_once():
    from sqlalchemy.orm import Session
    from .db import engine
    with Session(engine()) as db:
        rows=db.scalars(select(QualityPolicy).where(QualityPolicy.status=='active',
            QualityPolicy.next_run_at<=func.clock_timestamp()).with_for_update(skip_locked=True).limit(100)).all()
        for row in rows:
            from .models import User,Membership
            allowed=db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(
                User.id==row.actor_id,User.active.is_(True),Membership.workspace_id==row.workspace_id,Membership.role=='admin'))
            if not allowed:
                row.status='blocked_authorization';row.version+=1
                row.data={**row.data,'authorization_error':'策略批准人的管理员授权已撤销，停止自动检查'}
                continue
            enqueue(db,row,'periodic:'+row.next_run_at.isoformat())
            row.next_run_at=now()+timedelta(seconds=row.data['interval_seconds'])
        db.commit()
    return len(rows)


def relation_problem(db,p,row):
    if row.status in {'withdrawn','rejected'}:return None
    if row.status!='confirmed':return '关系尚未重新确认，不能证明修复完成'
    from .relations import current_problem
    return current_problem(db,p,row,now())


def inspect(db,p,kind,entity=None,relation=None):
    if relation:
        problem=relation_problem(db,p,relation)
        return {'problem':problem,'relation_id':relation.id,'relation_version':relation.version,
            'relation_status':relation.status,'endpoint_versions':relation.data.get('endpoint_versions',{}),
            'current_endpoint_versions':{i:scoped(db,Entity,i,p).version
                for i in {relation.source_entity_id,relation.target_entity_id}},
            'registered_sources':[{'kind':r['kind'],'id':r['id'],'version':r['version']}
                for r in relation.data.get('evidence_snapshot',[])]}
    package=scoped(db,Capability,entity.schema_capability_id,p)
    schema=package.data['specification']['entity_schemas'][entity.entity_type]
    errors=[{'field':'.'.join(map(str,e.path)),'message':e.message}
        for e in Draft202012Validator(schema).iter_errors(entity.data)][:20]
    problem=('档案字段不符合登记格式' if errors else None) if kind=='invalid_schema' else (
        '档案已超过有效期，请补充有效观测，不能修改属性掩盖时效' if entity.valid_until and entity.valid_until<now() else None)
    return {'problem':problem,'entity_version':entity.version,'errors':errors,
        'schema_hash':digest(schema),'valid_until':entity.valid_until.isoformat() if entity.valid_until else None}


def impacts(db,p,entity_id,relation_id=None):
    rows={};truncated=False
    for name,model,condition,label in [
        ('tasks',Task,Task.data['entity_ids'].contains([entity_id]),lambda r:r.title),
        ('queries',DataRun,DataRun.data['request']['entity_ids'].contains([entity_id]),lambda r:r.question),
        ('memories',Memory,Memory.data['evidence']['relations']['relation_evidence'].contains(
            [{'relation_id':relation_id}]) if relation_id else Memory.data['entity_ids'].contains([entity_id]),lambda r:r.text)]:
        values=db.scalars(select(model).where(model.workspace_id==p.workspace_id,condition)
            .order_by(model.created_at.desc()).limit(21)).all()
        rows[name]=[{'id':r.id,'title':label(r),'status':r.status} for r in values[:20]]
        truncated|=len(values)>20
    return {**rows,'truncated':truncated,'note':'历史结果保留；后续执行和可信复用仍须核查当前依据'}


def record(db,p,key,kind,entity,diagnosis,trigger):
    row=db.scalar(select(Issue).where(Issue.workspace_id==p.workspace_id,Issue.data['check_key'].as_string()==key)
        .order_by(Issue.created_at.desc()).with_for_update().limit(1))
    if not row and kind in {'invalid_schema','stale'}:
        row=db.scalar(select(Issue).where(Issue.workspace_id==p.workspace_id,Issue.entity_id==entity.id,
            Issue.kind==kind,Issue.status=='open').with_for_update().limit(1))
    created=False
    if not row:
        row=Issue(workspace_id=p.workspace_id,entity_id=entity.id,kind=kind,data={});db.add(row);db.flush();created=True
    if row.data.get('diagnosis_hash')==digest(diagnosis) and row.status=='open':return row,False
    row.status='open';row.version+=int(not created)
    row.data={**row.data,'last_verification':None,'check_key':key,'entity_version':entity.version,'diagnosis':diagnosis,
        'diagnosis_hash':digest(diagnosis),'errors':[e['message'] for e in diagnosis.get('errors',[])],
        'route':'perception' if kind=='stale' else 'data','severity':'high' if kind=='relation_source' else 'medium',
        'responsible_role':'数据责任人','checker_version':'deterministic_quality_v1','checked_at':now().isoformat(),
        'trigger':trigger,'impact':impacts(db,p,entity.id,diagnosis.get('relation_id')),
        'suggested_action':'核查并纠正关系来源后重新确认，或撤回无法证明的声明' if kind=='relation_source' else
            '提出有依据的字段纠错方案' if kind=='invalid_schema' else '补充有效观测及档案有效期'}
    db.add(IssueCheck(workspace_id=p.workspace_id,issue_id=row.id,actor_id=p.user_id,
        data={'operation':'detected','issue_version':row.version,'diagnosis':diagnosis,'trigger':trigger}))
    return row,created


def diagnose(db,p,*,package_ids=None,entity_ids=None,trigger='manual',cursor=None):
    require(p,'admin','operator')
    # Serializes issue upserts without holding entity locks or racing duplicate open problems.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:workspace,0))'),{'workspace':p.workspace_id})
    q=select(Entity).where(Entity.workspace_id==p.workspace_id)
    if package_ids:q=q.where(Entity.schema_capability_id.in_(package_ids))
    if entity_ids:q=q.where(Entity.id.in_(entity_ids))
    cursor=cursor or {}
    if cursor.get('entity_after'):q=q.where(Entity.id>cursor['entity_after'])
    entities=[] if cursor.get('entities_done') else db.scalars(q.order_by(Entity.id).limit(LIMIT+1)).all();created=[];found=[]
    for e in entities[:LIMIT]:
        for kind in ['invalid_schema','stale']:
            value=inspect(db,p,kind,entity=e)
            if value['problem']:
                issue,new=record(db,p,'entity:'+e.id+':'+kind,kind,e,value,trigger)
                found.append(issue.id)
                if new:created.append(issue.id)
    q=select(Relation).where(Relation.workspace_id==p.workspace_id,Relation.status=='confirmed')
    if package_ids:q=q.where(Relation.package_id.in_(package_ids))
    if entity_ids:
        q=q.where(or_(Relation.source_entity_id.in_(entity_ids),Relation.target_entity_id.in_(entity_ids),
            *[Relation.data['evidence_refs'].contains([{'kind':'entity','id':i}]) for i in entity_ids]))
    if cursor.get('relation_after'):q=q.where(Relation.id>cursor['relation_after'])
    relations=[] if cursor.get('relations_done') else db.scalars(q.order_by(Relation.id).limit(LIMIT+1)).all()
    for r in relations[:LIMIT]:
        value=inspect(db,p,'relation_source',relation=r)
        if value['problem']:
            e=scoped(db,Entity,r.source_entity_id,p)
            issue,new=record(db,p,'relation:'+r.id,'relation_source',e,value,trigger);found.append(issue.id)
            if new:created.append(issue.id)
    result={'scanned':min(len(entities),LIMIT),'relations_scanned':min(len(relations),LIMIT),
        'created_issue_ids':created,'found_issue_ids':found,'truncated':len(entities)>LIMIT or len(relations)>LIMIT,
        'checked_at':now().isoformat(),'trigger':trigger}
    result['continuation']={'entities_done':len(entities)<=LIMIT,'relations_done':len(relations)<=LIMIT,
        'entity_after':entities[LIMIT-1].id if len(entities)>LIMIT else None,
        'relation_after':relations[LIMIT-1].id if len(relations)>LIMIT else None}
    audit(db,p,'data.diagnose',p.workspace_id,result);return result


def run(db,p,job):
    policy=scoped(db,QualityPolicy,job.data['policy_id'],p,True)
    if policy.status!='active' or policy.version!=job.data['policy_version']:return 'cancelled'
    if p.role!='admin':raise ValueError('质量检查策略的管理员授权已撤销')
    result=diagnose(db,p,package_ids=policy.data['package_ids'],entity_ids=job.data.get('entity_ids'),trigger=job.data['trigger'],cursor=job.data.get('cursor'))
    if result['truncated']:
        root=job.data.get('root_trigger',job.data['trigger'])
        enqueue(db,policy,root+':page:'+digest(result['continuation'])[:12],root_trigger=root,
            cursor=result['continuation'],entity_ids=job.data.get('entity_ids'))
    policy.data={**policy.data,'last_result':result,'last_job_id':job.id};return 'succeeded'


def verify(db,p,row,reason):
    require(p,'admin','operator')
    if row.status!='open':raise ValueError('问题已结案，不能覆盖原验证记录')
    relation=scoped(db,Relation,row.data['diagnosis']['relation_id'],p) if row.kind=='relation_source' else None
    entity=scoped(db,Entity,row.entity_id,p)
    value=inspect(db,p,row.kind,entity=entity,relation=relation)
    verified=not value['problem'];row.version+=1
    row.data={**row.data,'last_verification':{'verified':verified,'reason':reason,'diagnosis':value,
        'checked_at':now().isoformat(),'actor_id':p.user_id,
        'disposition':'风险声明已撤回；关联证据仍需补充' if relation and relation.status in {'withdrawn','rejected'} else
            '当前权威记录通过该项检查' if verified else '未达到结案条件，保留问题'}}
    if verified:row.status='resolved'
    db.add(IssueCheck(workspace_id=p.workspace_id,issue_id=row.id,actor_id=p.user_id,
        data={'operation':'verified' if verified else 'verification_failed','reason':reason,
            'issue_version':row.version,'diagnosis':value}))
    audit(db,p,'quality.issue_verified',row.id,{'verified':verified,'reason':reason})
    return verified
