"""Bind an independently received observation to a then-authorized policy.

`issue` is only called INSIDE the observation.data ACK transaction after the
Memory and Event status have been flushed. Caller must use a savepoint so a
failure to issue authority never rolls back receipt.  Task then Policy NOWAIT locks serialize issuance with enable/revoke; contention
never waits in the ACK transaction. No timestamp proves commit order.
"""
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import DDL, ForeignKey, Integer, String, UniqueConstraint, event, select, text
from sqlalchemy.orm import Mapped, mapped_column, object_session
from .models import Base, Row, Scoped, Payload, Task, Observation, Event, Audit, Memory
from .contracts import digest
from .instance_data_receipts import observation_receipt

ACTION='goal_evaluation.auto_ack_witness_v356'

class AutoEvaluationAckWitnessV356(Row,Scoped,Payload,Base):
    __tablename__='auto_evaluation_ack_witnesses_v356'
    __table_args__=(UniqueConstraint('workspace_id','task_id','event_id','policy_version'),)
    task_id:Mapped[str]=mapped_column(ForeignKey('tasks.id'),index=True)
    observation_id:Mapped[str]=mapped_column(ForeignKey('observations.id'))
    event_id:Mapped[str]=mapped_column(ForeignKey('outbox_events.id'))
    policy_id:Mapped[str]=mapped_column(ForeignKey('auto_evaluation_policies_v356.id'))
    policy_version:Mapped[int]=mapped_column(Integer)

class AutoEvaluationAckFailureV356(Row,Scoped,Payload,Base):
    """Operator-visible non-authoritative failure: never grants or retries work."""
    __tablename__='auto_evaluation_ack_failures_v356'
    __table_args__=(UniqueConstraint('workspace_id','task_id','event_id'),)
    task_id:Mapped[str]=mapped_column(String(36),index=True)
    event_id:Mapped[str]=mapped_column(ForeignKey('outbox_events.id'))
    observation_id:Mapped[str]=mapped_column(ForeignKey('observations.id'))

class AutoEvaluationAckOverflowV356(Row,Scoped,Payload,Base):
    """Receipt-level uncertainty; never proves which policies were enabled."""
    __tablename__='auto_evaluation_ack_overflows_v356'
    __table_args__=(UniqueConstraint('workspace_id','event_id'),)
    event_id:Mapped[str]=mapped_column(ForeignKey('outbox_events.id'))
    observation_id:Mapped[str]=mapped_column(ForeignKey('observations.id'))
    entity_id:Mapped[str]=mapped_column(String(36),index=True)

_guard='''CREATE OR REPLACE FUNCTION ga_auto_eval_ack_v356_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'automatic evaluation ACK witness is immutable' USING ERRCODE='55000'; END; $$;'''
event.listen(AutoEvaluationAckWitnessV356.__table__,'after_create',DDL(_guard).execute_if(dialect='postgresql'))
event.listen(AutoEvaluationAckWitnessV356.__table__,'after_create',DDL('CREATE TRIGGER ga_auto_eval_ack_v356_guard BEFORE UPDATE OR DELETE ON %(fullname)s FOR EACH ROW EXECUTE FUNCTION ga_auto_eval_ack_v356_immutable()').execute_if(dialect='postgresql'))

def need(condition,message='自动评价接收授权凭证无法核对；原观测保留'):
    if not condition:raise HTTPException(409,message)

def _ack(db,p,observation_id):
    obs=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.id==observation_id))
    need(obs is not None and obs.entity_id is not None)
    event=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind=='observation.data',
        Event.idempotency_key=='data:'+obs.id))
    need(event is not None and event.status=='received')
    value=observation_receipt(db,p,obs)
    need(type(value) is dict and type(value.get('data_reception')) is dict)
    return obs,event,value['data_reception']

def require_unrestricted(db,p,evt):
    """Reject any persisted derivation restriction, even if one projection is removed."""
    need(type(evt.data) is dict and evt.data.get('derivation_receipt') is None,
        '原观测受限，未授权自动评价')
    audit=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
        Audit.object_id==evt.id,Audit.action=='observation.derivation_skipped').limit(1))
    projected=db.scalar(select(Memory.id).where(Memory.workspace_id==p.workspace_id,
        Memory.data['event_id'].as_string()==evt.id,
        Memory.data['derivation_receipt'].as_string().is_not(None)).limit(1))
    need(audit is None and projected is None,'原观测受限，未授权自动评价')

def _anchor(row):
    value=row.data
    need(type(value) is dict and value.get('binding_hash')==digest({k:v for k,v in value.items() if k!='binding_hash'}))
    need(value.get('schema')=='auto-evaluation-ack-witness-v356' and value.get('workspace_id')==row.workspace_id
        and value.get('task_id')==row.task_id and value.get('event_id')==row.event_id
        and value.get('observation_id')==row.observation_id and value.get('policy_id')==row.policy_id
        and value.get('policy_version')==row.policy_version)
    return value

@contextmanager
def bounded_savepoint(db,timeout_ms=250):
    """Limit hidden FK/index lock waits as well as the explicit NOWAIT locks.

    Call only after ACK changes are flushed: begin_nested() flushes first.
    PostgreSQL restores SET LOCAL on savepoint rollback; successful paths reset
    the previous setting before releasing the savepoint.
    """
    need(type(timeout_ms) is int and 1<=timeout_ms<=1000)
    need(not db.new and not db.dirty and not db.deleted,
        '接收数据尚未刷新，不能安全建立授权凭证保存点')
    with db.begin_nested():
        previous=db.scalar(text("SELECT current_setting('lock_timeout')"))
        prior_statement=db.scalar(text("SELECT current_setting('statement_timeout')"))
        db.execute(text("SELECT set_config('lock_timeout', :value, true)"),{'value':str(timeout_ms)+'ms'})
        db.execute(text("SELECT set_config('statement_timeout', :value, true)"),{'value':str(timeout_ms)+'ms'})
        yield
        db.execute(text("SELECT set_config('statement_timeout', :value, true)"),{'value':prior_statement})
        db.execute(text("SELECT set_config('lock_timeout', :value, true)"),{'value':previous})

@contextmanager
def reception(db,evt):
    """Mark only the original pending→received transition in this transaction."""
    need(object_session(evt) is db and evt.kind=='observation.data' and evt.status=='pending_delivery')
    tx=db.get_transaction();need(tx is not None and 'auto_eval_ack_scope_v356' not in db.info)
    db.info['auto_eval_ack_scope_v356']=(evt.id,tx)
    try:yield
    finally:db.info.pop('auto_eval_ack_scope_v356',None)

def issue(db,p,task_id,observation_id):
    scope=db.info.get('auto_eval_ack_scope_v356')
    need(type(scope) is tuple and len(scope)==2 and scope[1] is db.get_transaction(),
        '只能在原数据接收事务中签发自动评价凭证')
    from . import auto_evaluation_policy_v356 as policy
    task=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==task_id).with_for_update(nowait=True))
    need(task is not None)
    row=db.scalar(select(policy.AutoEvaluationPolicyV356).where(
        policy.AutoEvaluationPolicyV356.workspace_id==p.workspace_id,
        policy.AutoEvaluationPolicyV356.task_id==task_id).with_for_update(nowait=True).execution_options(populate_existing=True))
    if row is None or row.status!='enabled':return None
    policy.verify(db,p,task,row)
    obs,evt,receipt=_ack(db,p,observation_id)
    need(evt.id==scope[0])
    require_unrestricted(db,p,evt)
    need(obs.entity_id in task.data.get('entity_ids',[]))
    old=db.scalar(select(AutoEvaluationAckWitnessV356).where(AutoEvaluationAckWitnessV356.workspace_id==p.workspace_id,
        AutoEvaluationAckWitnessV356.task_id==task.id,AutoEvaluationAckWitnessV356.event_id==evt.id,
        AutoEvaluationAckWitnessV356.policy_version==row.version))
    if old is not None:
        check(db,p,task,row,old,obs=obs,evt=evt,receipt=receipt)
        return old
    value={'schema':'auto-evaluation-ack-witness-v356','workspace_id':p.workspace_id,'task_id':task.id,
        'event_id':evt.id,'observation_id':obs.id,'receipt_hash':digest(receipt),'policy_id':row.id,
        'policy_version':row.version,'policy_hash':row.data['policy_hash'],'actor_id':p.user_id}
    value['binding_hash']=digest(value)
    witness=AutoEvaluationAckWitnessV356(workspace_id=p.workspace_id,task_id=task.id,event_id=evt.id,
        observation_id=obs.id,policy_id=row.id,policy_version=row.version,data=value)
    db.add(witness);db.flush()
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,object_id=witness.id,data=value));db.flush()
    return witness

def check(db,p,task,policy,witness,*,obs=None,evt=None,receipt=None):
    value=_anchor(witness)
    need(witness.workspace_id==p.workspace_id==task.workspace_id and witness.task_id==task.id
        and witness.policy_id==policy.id and witness.policy_version==policy.version
        and value.get('policy_hash')==policy.data.get('policy_hash') and value.get('actor_id')==p.user_id)
    proofs=db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==ACTION,Audit.object_id==witness.id)).all()
    need(len(proofs)==1 and proofs[0].actor_id==p.user_id and digest(proofs[0].data)==digest(value))
    if obs is None:obs,evt,receipt=_ack(db,p,witness.observation_id)
    require_unrestricted(db,p,evt)
    need(obs.id==witness.observation_id and obs.entity_id in task.data.get('entity_ids',[])
        and evt.id==witness.event_id and digest(receipt)==value.get('receipt_hash'))
    return value


def record_failure(db,p,task_id,observation_id,code):
    """Record after failed issue savepoint in a second savepoint before ACK commit.

    If even this write fails, caller rolls back only that savepoint, commits ACK,
    and exposes the operational failure via its existing telemetry. It never
    confers authority and cannot be converted into a retrospective witness.
    """
    scope=db.info.get('auto_eval_ack_scope_v356')
    need(type(scope) is tuple and len(scope)==2 and scope[1] is db.get_transaction())
    need(code in {'lock_contended','authority_unavailable','proof_failed','unexpected_error','derivation_restricted'})
    # This records a failure and grants no authority. In particular it must
    # still work when the independent receipt itself is what failed checking.
    obs=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.id==observation_id))
    need(obs is not None)
    evt=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.id==scope[0],
        Event.kind=='observation.data',Event.idempotency_key=='data:'+obs.id,Event.status=='received'))
    need(evt is not None)
    task=db.scalar(select(Task.id).where(Task.workspace_id==p.workspace_id,Task.id==task_id))
    need(task==task_id)
    old=db.scalar(select(AutoEvaluationAckFailureV356).where(AutoEvaluationAckFailureV356.workspace_id==p.workspace_id,
        AutoEvaluationAckFailureV356.task_id==task_id,AutoEvaluationAckFailureV356.event_id==evt.id))
    if old is not None:
        need(old.observation_id==obs.id)
        return old
    value={'schema':'auto-evaluation-ack-failure-v356','code':code,'event_id':evt.id,
        'observation_id':obs.id,'task_id':task_id,'automatic_evaluation_started':False}
    row=AutoEvaluationAckFailureV356(workspace_id=p.workspace_id,task_id=task_id,event_id=evt.id,
        observation_id=obs.id,data=value)
    db.add(row);db.flush();return row


def record_overflow(db,event,observation,reason):
    """Record event-level uncertainty; this never grants execution authority."""
    scope=db.info.get('auto_eval_ack_scope_v356')
    need(type(scope) is tuple and len(scope)==2 and scope[0]==event.id and scope[1] is db.get_transaction())
    need(event.status=='received' and event.workspace_id==observation.workspace_id and observation.entity_id is not None)
    need(reason in {'too_many_policies','budget_exhausted','scan_unavailable'})
    old=db.scalar(select(AutoEvaluationAckOverflowV356).where(
        AutoEvaluationAckOverflowV356.workspace_id==event.workspace_id,AutoEvaluationAckOverflowV356.event_id==event.id))
    if old is not None:return old
    row=AutoEvaluationAckOverflowV356(workspace_id=event.workspace_id,event_id=event.id,
        observation_id=observation.id,entity_id=observation.entity_id,
        data={'code':reason,'witness_capture_skipped':True,'scope':'observation_only'})
    db.add(row);db.flush();return row


def capture_enabled_for_ack(db,event,observation,*,restricted=False,budget=None):
    """Bounded policy signing in the original ACK; never enqueues evaluation."""
    from .auto_evaluation_policy_api_v356 import automatic_runner_available
    if not automatic_runner_available():return
    import logging,time
    from sqlalchemy.exc import SQLAlchemyError
    from . import auto_evaluation_policy_v356 as policy
    from .models import Membership,User
    from .security import Principal
    log=logging.getLogger(__name__);started=time.monotonic();maximum=8
    if budget is None:budget={'remaining':2.0,'deferred_overflows':[]}
    budget.setdefault('deferred_overflows',[])
    def remaining():return max(0,budget['remaining']-(time.monotonic()-started))
    def overflow(reason):
        # Capture only an uncertainty notice in the original ACK scope. This
        # does not imply an enabled policy or confer any authority.
        scope=db.info.get('auto_eval_ack_scope_v356')
        need(type(scope) is tuple and len(scope)==2 and scope[0]==event.id and scope[1] is db.get_transaction())
        budget['deferred_overflows'].append({'workspace_id':event.workspace_id,'event_id':event.id,
            'observation_id':observation.id,'entity_id':observation.entity_id,
            'data':{'code':reason,'witness_capture_skipped':True,'scope':'observation_only'}})
    try:
        if remaining()<=0:
            overflow('budget_exhausted');return
        try:
            with bounded_savepoint(db,timeout_ms=200):
                choices=list(db.execute(select(Task.id,policy.AutoEvaluationPolicyV356.data).join(
                    policy.AutoEvaluationPolicyV356,policy.AutoEvaluationPolicyV356.task_id==Task.id).where(
                    Task.workspace_id==event.workspace_id,policy.AutoEvaluationPolicyV356.workspace_id==event.workspace_id,
                    policy.AutoEvaluationPolicyV356.status=='enabled',Task.data['entity_ids'].contains([observation.entity_id]))
                    .order_by(Task.id).limit(maximum+1)))
        except (SQLAlchemyError,HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
            overflow('scan_unavailable');return
        if len(choices)>maximum:
            overflow('too_many_policies');return
        if choices and remaining()<=0:
            overflow('budget_exhausted');return
        for task_id,values in choices:
            if remaining()<=0:
                overflow('budget_exhausted');return
            actor=values.get('actor_id') if type(values) is dict else None
            if type(actor) is not str:actor=observation.data.get('submitted_by')
            # Principal is only an identity to verify, never an authorization.
            fallback=Principal(actor or '',event.workspace_id,'viewer','auto-evaluation-ack')
            code='authority_unavailable'
            try:
                with bounded_savepoint(db,timeout_ms=200):
                    member=db.scalar(select(Membership).where(Membership.workspace_id==event.workspace_id,Membership.user_id==actor))
                    user=db.get(User,actor) if actor else None
                    p=Principal(actor or '',event.workspace_id,member.role if member and user and user.active else 'viewer','auto-evaluation-ack')
                    if restricted:
                        record_failure(db,p,task_id,observation.id,'derivation_restricted')
                    else:
                        issue(db,p,task_id,observation.id)
                continue
            except SQLAlchemyError as exc:
                code='lock_contended' if getattr(getattr(exc,'orig',None),'sqlstate',None) in {'55P03','57014'} else 'unexpected_error'
            except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                code='proof_failed'
            try:
                with bounded_savepoint(db,timeout_ms=200):record_failure(db,fallback,task_id,observation.id,code)
            except (SQLAlchemyError,HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                overflow('scan_unavailable')
                log.warning('自动评价收件失败状态无法保存；原数据接收继续')
    finally:
        budget['remaining']=remaining()


def flush_deferred_overflows(db,budget):
    """One bounded batch per receiver transaction; failures never undo ACK."""
    import logging
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.dialects.postgresql import insert
    rows=budget.get('deferred_overflows',[])
    if not rows:return
    try:
        # The caller flushes its real ACKs before establishing this savepoint.
        with bounded_savepoint(db,timeout_ms=200):
            stmt=insert(AutoEvaluationAckOverflowV356).values(rows).on_conflict_do_nothing(
                index_elements=['workspace_id','event_id'])
            db.execute(stmt)
    except (SQLAlchemyError,HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
        logging.getLogger(__name__).warning('自动评价收件范围状态无法批量保存；原数据接收继续')


def task_summary(db,p,task_id,limit=100):
    """Read only; no new evaluation, receipts, authority or device actions."""
    from .goal_evaluation_append_store_v297 import _reader,_task
    _reader(db,p);task=_task(db,p,task_id)
    if type(limit) is not int or not 1<=limit<=100:raise HTTPException(422,'数量范围无效')
    witnesses=db.scalars(select(AutoEvaluationAckWitnessV356).where(
        AutoEvaluationAckWitnessV356.workspace_id==p.workspace_id,AutoEvaluationAckWitnessV356.task_id==task.id)
        .order_by(AutoEvaluationAckWitnessV356.created_at.desc()).limit(limit)).all()
    failures=db.scalars(select(AutoEvaluationAckFailureV356).where(
        AutoEvaluationAckFailureV356.workspace_id==p.workspace_id,AutoEvaluationAckFailureV356.task_id==task.id)
        .order_by(AutoEvaluationAckFailureV356.created_at.desc()).limit(limit)).all()
    rows=[{'observation_id':r.observation_id,'event_id':r.event_id,'time':r.created_at.isoformat(),
        'status':'witness_recorded','reason':None} for r in witnesses]
    rows += [{'observation_id':r.observation_id,'event_id':r.event_id,'time':r.created_at.isoformat(),
        'status':'not_captured','reason':r.data.get('code','unexpected_error')} for r in failures]
    overflows=db.scalars(select(AutoEvaluationAckOverflowV356).where(
        AutoEvaluationAckOverflowV356.workspace_id==p.workspace_id,
        AutoEvaluationAckOverflowV356.entity_id.in_(task.data.get('entity_ids',[])))
        .order_by(AutoEvaluationAckOverflowV356.created_at.desc()).limit(limit)).all()
    rows += [{'observation_id':r.observation_id,'event_id':r.event_id,'time':r.created_at.isoformat(),
        'status':'possibly_not_captured','reason':r.data.get('code','scan_unavailable')} for r in overflows]
    rows.sort(key=lambda x:x['time'],reverse=True)
    return {'records':rows[:limit],'has_more':len(rows)>limit or len(witnesses)==limit or len(failures)==limit or len(overflows)==limit}
