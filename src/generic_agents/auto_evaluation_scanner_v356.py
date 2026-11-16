"""Task-scoped ACK-witness scanner. Never dispatches tools, devices or models.

It is disabled by the same product gate as policy enable. The status table
records queued, blocked and unknown outcomes; it is not an execution receipt.
"""
from uuid import UUID
from datetime import datetime,timedelta,timezone
from fastapi import HTTPException
from sqlalchemy import DateTime,ForeignKey,String,UniqueConstraint,select,and_,or_,func,text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy.orm import Session
from .db import engine
from .models import Base,Row,Scoped,Payload,Task,Job,Membership,User,Observation,Entity,Capability
from .security import Principal
from .auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356 as Witness
from .auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356 as Trigger,enqueue,historical_origin


class AutoEvaluationScanStateV356(Row,Scoped,Payload,Base):
    """Recoverable operator status, never an authorization or evaluation."""
    __tablename__='auto_evaluation_scan_states_v356'
    __table_args__=(UniqueConstraint('workspace_id','task_id','witness_id'),)
    task_id:Mapped[str]=mapped_column(ForeignKey('tasks.id'),index=True)
    witness_id:Mapped[str]=mapped_column(ForeignKey('auto_evaluation_ack_witnesses_v356.id'))
    status:Mapped[str]=mapped_column(String(20))
    next_attempt_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)


def _record(workspace,task_id,witness_id,status):
    # Serialize even first insertion; ORM assignment is required to keep the
    # canonical typed-payload columns paired with data during updates.
    from .contracts import digest
    if status not in {'queued','blocked','unknown','superseded'}:raise ValueError('invalid scanner state')
    lock_key=int(digest({'scope':'auto-evaluation-scan-state-v356','workspace':workspace,'task':task_id,'witness':witness_id})[:16],16)-(1<<63)
    with Session(engine()) as db:
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock_key})
        state=db.scalar(select(AutoEvaluationScanStateV356).where(AutoEvaluationScanStateV356.workspace_id==workspace,
            AutoEvaluationScanStateV356.task_id==task_id,AutoEvaluationScanStateV356.witness_id==witness_id).with_for_update())
        if state is not None and (state.status=='queued' or (state.status=='superseded' and status!='queued')):
            return
        clock=db.scalar(select(func.clock_timestamp()))
        next_at=None if status in {'queued','superseded'} else clock+timedelta(seconds=15 if status=='unknown' else 60)
        payload={'schema':'auto-evaluation-scan-state-v356','status':status}
        if state is None:
            db.add(AutoEvaluationScanStateV356(workspace_id=workspace,task_id=task_id,witness_id=witness_id,
                status=status,version=1,next_attempt_at=next_at,data=payload))
        else:
            state.status=status;state.next_attempt_at=next_at;state.data=payload;state.version+=1
        db.commit()


def task_status(db,p,task_id,limit=100):
    from .goal_evaluation_append_store_v297 import _reader,_task
    _reader(db,p);task=_task(db,p,task_id)
    if type(limit) is not int or not 1<=limit<=100:raise HTTPException(422,'数量范围无效')
    rows=db.scalars(select(AutoEvaluationScanStateV356).where(AutoEvaluationScanStateV356.workspace_id==p.workspace_id,
        AutoEvaluationScanStateV356.task_id==task.id).order_by(AutoEvaluationScanStateV356.created_at.desc()).limit(limit+1)).all()
    # Even an apparent queued state is not a verified operation receipt.
    labels={'queued':'queued_status_needs_verification','blocked':'requires_review','unknown':'outcome_unknown','superseded':'authorization_changed'}
    from .auto_evaluation_policy_v356 import current,_checked_current
    policy=current(db,p,task.id);_checked_current(db,p,task,policy)
    from .native_goals import manage
    try:
        _reader(db,p,write=True);manage(p,task);can_inspect=True
    except HTTPException:
        can_inspect=False
    visible=rows[:limit]
    witness_rows=db.scalars(select(Witness).where(Witness.workspace_id==p.workspace_id,Witness.task_id==task.id,
        Witness.id.in_([row.witness_id for row in visible]))).all()
    witnesses={item.id:item for item in witness_rows}
    observation_rows=db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,
        Observation.id.in_([item.observation_id for item in witness_rows]))).all()
    observations={item.id:item for item in observation_rows}
    subject_ids=task.data.get('entity_ids',[])
    scoped_observations=[item for item in observation_rows if item.entity_id and item.entity_id in subject_ids]
    entity_rows=db.scalars(select(Entity).where(Entity.workspace_id==p.workspace_id,
        Entity.id.in_([item.entity_id for item in scoped_observations]))).all()
    source_rows=db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,
        Capability.id.in_([item.source_id for item in scoped_observations]))).all()
    entities={item.id:item for item in entity_rows};sources={item.id:item for item in source_rows}
    records=[]
    for row in visible:
        witness=witnesses.get(row.witness_id)
        if witness is None:raise HTTPException(409,'原接收依据无法核对')
        changed=policy is None or policy.status!='enabled' or policy.id!=witness.policy_id or policy.version!=witness.policy_version
        obs=observations.get(witness.observation_id)
        summary=None
        if obs is not None and obs.entity_id in subject_ids:
            entity=entities.get(obs.entity_id);source=sources.get(obs.source_id)
            if entity is not None and source is not None:
                name=entity.data.get('name') if isinstance(entity.data,dict) else None
                name=name.strip()[:200] if isinstance(name,str) and name.strip() else entity.external_id
                summary={'object_name':name,'source_name':source.name,'reported_observed_at':obs.observed_at.isoformat() if obs.observed_at else None}
        # These are lookup/display details, not proof that the underlying input
        # was received or used in any evaluation. The investigation endpoint
        # must independently authenticate the original chain.
        records.append({'time':row.created_at.isoformat(),'status':labels.get(row.status,'requires_review'),
            'authorization_changed':changed,'observation':summary,'review_reference':witness.id if can_inspect else None})
    return {'records':records,'has_more':len(rows)>limit}


def _scope(workspace,instance,task):
    try:
        if not all(type(x) is str and str(UUID(x))==x for x in (workspace,instance,task)):raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ValueError('必须指定原工作区、实例及任务') from None


class AuthorizationChanged(HTTPException):
    def __init__(self):super().__init__(409,'原接收时的自动评价授权已更改')


def _resume_or_enqueue(db,p,instance,task_id,witness):
    # If the command committed but the status write failed, inspect the one
    # original trigger and its independent proof; never issue another command.
    task=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==task_id).with_for_update())
    if task is None or task.data.get('instance_id')!=instance:raise HTTPException(404,'原任务或实例不匹配')
    old=db.scalar(select(Trigger).where(Trigger.workspace_id==p.workspace_id,Trigger.task_id==task_id,
        Trigger.event_id==witness.event_id,Trigger.policy_id==witness.policy_id,
        Trigger.policy_version==witness.policy_version))
    if old is not None:
        return historical_origin(db,p,task,old,witness)
    from .auto_evaluation_policy_v356 import current,_checked_current
    policy=current(db,p,task_id,lock=True);_checked_current(db,p,task,policy)
    if policy is None or policy.status!='enabled' or policy.id!=witness.policy_id or policy.version!=witness.policy_version:
        raise AuthorizationChanged()
    if p.role not in {'admin','operator'}:raise HTTPException(403,'原授权账号不可用')
    return enqueue(db,p,task_id,witness.observation_id)


def tick(workspace,instance,task_id,*,after=None):
    """Process at most 50 original committed witnesses, one transaction each.

    `after` permits fair pagination even if earlier witnesses are blocked;
    callers must restart from None after the page has drained. Outcomes are
    persisted separately where possible; this does not start the producer Job.
    """
    _scope(workspace,instance,task_id)
    from .auto_evaluation_policy_api_v356 import automatic_runner_available
    if not automatic_runner_available():raise HTTPException(409,'自动评价处理链尚未接通')
    with Session(engine()) as db:
        task=db.scalar(select(Task).where(Task.workspace_id==workspace,Task.id==task_id))
        if task is None or task.data.get('instance_id')!=instance:raise HTTPException(404,'原任务或实例不匹配')
        issued=select(AutoEvaluationScanStateV356.id).where(AutoEvaluationScanStateV356.workspace_id==workspace,
            AutoEvaluationScanStateV356.task_id==task_id,AutoEvaluationScanStateV356.witness_id==Witness.id,
            or_(AutoEvaluationScanStateV356.status.in_(['queued','superseded']),
                AutoEvaluationScanStateV356.next_attempt_at>func.clock_timestamp())).exists()
        query=select(Witness.id,Witness.created_at).where(Witness.workspace_id==workspace,Witness.task_id==task_id,~issued)
        if after:query=query.where(or_(Witness.created_at>after[0],and_(Witness.created_at==after[0],Witness.id>after[1])))
        rows=list(db.execute(query.order_by(Witness.created_at,Witness.id).limit(50)))
    result={'queued':0,'blocked':0,'unknown':0,'superseded':0,'unrecorded':0,'after':None}
    for identity,created in rows:
        result['after']=(created,identity)
        try:
            with Session(engine()) as db:
                w=db.scalar(select(Witness).where(Witness.workspace_id==workspace,Witness.task_id==task_id,Witness.id==identity))
                if w is None:raise HTTPException(409,'原接收凭证不存在')
                actor=w.data.get('actor_id');member=db.scalar(select(Membership).where(Membership.workspace_id==workspace,Membership.user_id==actor))
                user=db.get(User,actor) if actor else None
                role=member.role if member and user and user.active else 'viewer'
                p=Principal(actor,workspace,role,'auto-evaluation-scanner')
                _resume_or_enqueue(db,p,instance,task_id,w);db.commit()
            outcome='queued';result['queued']+=1
        except AuthorizationChanged:
            outcome='superseded';result['superseded']+=1
        except (HTTPException,ValueError,KeyError,TypeError,AttributeError):
            outcome='blocked';result['blocked']+=1
        except SQLAlchemyError:
            # Commit outcome may be unknown. Future attempts use this same
            # witness and deterministic original request, never a new nonce.
            outcome='unknown';result['unknown']+=1
        try:_record(workspace,task_id,identity,outcome)
        except SQLAlchemyError:result['unrecorded']+=1
    return result
