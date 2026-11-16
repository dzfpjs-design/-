"""Explicit-policy automatic evaluation origin; never manufacture an ACK.

Call `enqueue` after observation reception commits; caller commits its transaction.
A failure leaves the original observation intact and can be retried with the same
stable event/strategy version.  This module does not start a worker or make calls.
"""
from datetime import timedelta
from uuid import UUID, uuid5
from fastapi import HTTPException
from sqlalchemy import CheckConstraint, DDL, ForeignKey, Integer, UniqueConstraint, event, func, select
from sqlalchemy.orm import Mapped, mapped_column
from .contracts import digest
from .models import Base, Row, Scoped, Payload, Event, Observation, Task, Audit
from .goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
from .instance_data_receipts import observation_receipt
from .temporal import instant
from .goal_evaluation_append_store_v297 import accept, _context

NAMESPACE=UUID('0a665a14-de72-4372-a065-cd26d0541fa7')
ACTION='goal_evaluation.auto_origin_v356'

class AutoEvaluationTriggerV356(Row,Scoped,Payload,Base):
    __tablename__='auto_evaluation_triggers_v356'
    __table_args__=(UniqueConstraint('workspace_id','task_id','event_id','policy_version'),
                    UniqueConstraint('workspace_id','job_id'),CheckConstraint('policy_version >= 1'))
    task_id:Mapped[str]=mapped_column(ForeignKey('tasks.id'),index=True)
    event_id:Mapped[str]=mapped_column(ForeignKey('outbox_events.id'))
    observation_id:Mapped[str]=mapped_column(ForeignKey('observations.id'))
    policy_id:Mapped[str]=mapped_column(ForeignKey('auto_evaluation_policies_v356.id'))
    policy_version:Mapped[int]=mapped_column(Integer)
    command_id:Mapped[str]=mapped_column(ForeignKey('goal_evaluation_append_commands_v297.id'))
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'),index=True)

_guard='''CREATE OR REPLACE FUNCTION ga_auto_eval_trigger_v356_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'automatic evaluation origin is immutable' USING ERRCODE='55000'; END; $$;'''
event.listen(AutoEvaluationTriggerV356.__table__,'after_create',DDL(_guard).execute_if(dialect='postgresql'))
event.listen(AutoEvaluationTriggerV356.__table__,'after_create',DDL('CREATE TRIGGER ga_auto_eval_trigger_v356_guard BEFORE UPDATE OR DELETE ON %(fullname)s FOR EACH ROW EXECUTE FUNCTION ga_auto_eval_trigger_v356_immutable()').execute_if(dialect='postgresql'))

def need(condition,message='自动评价依据或权限无法核对，原数据保留'):
    if not condition: raise HTTPException(409,message)

def _event(db,p,observation_id):
    obs=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.id==observation_id))
    need(obs is not None and obs.entity_id is not None)
    evt=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind=='observation.data',
        Event.idempotency_key=='data:'+obs.id))
    need(evt is not None and evt.status=='received')
    from .auto_evaluation_ack_witness_v356 import require_unrestricted
    require_unrestricted(db,p,evt)
    receipt=observation_receipt(db,p,obs)
    need(type(receipt) is dict and type(receipt.get('data_reception')) is dict,
         '数据管理尚未形成可验证的独立接收回执')
    return obs,evt,receipt['data_reception']

def enqueue(db,p,task_id,observation_id):
    from . import auto_evaluation_policy_v356 as policy
    task=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==task_id).with_for_update())
    need(task is not None)
    row=policy.current(db,p,task_id,lock=True);need(row is not None)
    policy.verify(db,p,task,row)
    obs,evt,receipt=_event(db,p,observation_id)
    need(obs.entity_id in task.data.get('entity_ids',[]),'观测对象不在原任务范围内')
    need(evt.received_at is not None and evt.received_at.tzinfo is not None
        and evt.received_at >= instant(row.data['enabled_at']),'策略启用之前的数据不会自动触发评价')
    prior=db.scalar(select(AutoEvaluationTriggerV356).where(AutoEvaluationTriggerV356.workspace_id==p.workspace_id,
        AutoEvaluationTriggerV356.task_id==task.id,AutoEvaluationTriggerV356.event_id==evt.id,
        AutoEvaluationTriggerV356.policy_version==row.version))
    if prior is not None:
        from .models import Job
        old_job=db.get(Job,prior.job_id);need(old_job is not None)
        authority(db,p,task,old_job)
        need(prior.policy_id==row.id and prior.observation_id==obs.id)
        return prior
    clock=db.scalar(select(func.clock_timestamp()))
    # Serialised by the policy row lock; both counts are workspace/task specific.
    history=db.scalars(select(AutoEvaluationTriggerV356).where(AutoEvaluationTriggerV356.workspace_id==p.workspace_id,
        AutoEvaluationTriggerV356.task_id==task.id,AutoEvaluationTriggerV356.created_at>=clock-timedelta(days=1))
        .order_by(AutoEvaluationTriggerV356.created_at.desc())).all()
    need(len(history)<row.data['max_evaluations_per_day'],'自动评价次数已达上限，等待人工处理')
    if history:
        need(history[0].created_at<=clock-timedelta(minutes=row.data['min_interval_minutes']),
             '自动评价间隔尚未到期，原观测保留')
    # Only the original ACK transaction may create this immutable witness.
    # A timestamp, a current policy, or an ACK alone cannot authorize a new run.
    from .auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356,check
    witnesses=list(db.scalars(select(AutoEvaluationAckWitnessV356).where(
        AutoEvaluationAckWitnessV356.workspace_id==p.workspace_id,AutoEvaluationAckWitnessV356.task_id==task.id,
        AutoEvaluationAckWitnessV356.event_id==evt.id,AutoEvaluationAckWitnessV356.policy_id==row.id,
        AutoEvaluationAckWitnessV356.policy_version==row.version).limit(2)))
    need(len(witnesses)==1,'原接收时未捕获有效授权凭证；原观测保留')
    witness=witnesses[0];check(db,p,task,row,witness,obs=obs,evt=evt,receipt=receipt)
    context=_context(db,p,task)
    request_id=str(uuid5(NAMESPACE,':'.join([p.workspace_id,task.id,evt.id,row.id,str(row.version)])))
    body={'request_id':request_id,'expected_goal_hash':context['goal']['hash'],
          'expected_mature_head_hash':context['mature']['head']['head_hash'],
          'reason':'已授权的自动评价：数据管理已独立接收新的观测'}
    result=accept(db,p,task.id,body)
    command=db.get(Command,result['operation_id']);need(command is not None and command.task_id==task.id)
    binding={'schema':'auto-evaluation-trigger-v356','workspace_id':p.workspace_id,'task_id':task.id,
        'event_id':evt.id,'observation_id':obs.id,'receipt_hash':digest(receipt),'policy_id':row.id,
        'policy_version':row.version,'policy_hash':row.data['policy_hash'],'command_id':command.id,'job_id':command.job_id,
        'request_id':request_id,'actor_id':p.user_id,'witness_id':witness.id}
    binding['binding_hash']=digest(binding)
    trigger=AutoEvaluationTriggerV356(workspace_id=p.workspace_id,task_id=task.id,event_id=evt.id,
        observation_id=obs.id,policy_id=row.id,policy_version=row.version,command_id=command.id,job_id=command.job_id,data=binding)
    db.add(trigger);db.flush()
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,object_id=trigger.id,data=binding));db.flush()
    return trigger

def historical_origin(db,p,task,origin,witness):
    """Authenticate a saved original; do not authorize or dispatch work."""
    from .auto_evaluation_ack_witness_v356 import _anchor,ACTION as WITNESS_ACTION
    from .auto_evaluation_policy_v356 import AutoEvaluationPolicyCommandV356,_checked_command
    from .goal_evaluation_append_store_v297 import _inventory
    value=origin.data
    need(type(value) is dict and value.get('schema')=='auto-evaluation-trigger-v356' and value.get('binding_hash')==digest({k:v for k,v in value.items() if k!='binding_hash'}))
    need(origin.workspace_id==p.workspace_id==task.workspace_id and origin.task_id==task.id
        and origin.event_id==witness.event_id and origin.observation_id==witness.observation_id
        and origin.policy_id==witness.policy_id and origin.policy_version==witness.policy_version
        and value.get('witness_id')==witness.id and value.get('workspace_id')==p.workspace_id
        and value.get('task_id')==task.id and value.get('event_id')==witness.event_id
        and value.get('observation_id')==witness.observation_id and value.get('policy_id')==witness.policy_id
        and value.get('policy_version')==witness.policy_version and value.get('actor_id')==p.user_id
        and value.get('command_id')==origin.command_id and value.get('job_id')==origin.job_id)
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==ACTION,Audit.object_id==origin.id)).all())
    need(len(audits)==1 and audits[0].actor_id==p.user_id and digest(audits[0].data)==digest(value))
    wdata=_anchor(witness)
    need(wdata.get('actor_id')==p.user_id and wdata.get('policy_hash')==value.get('policy_hash')
        and wdata.get('receipt_hash')==value.get('receipt_hash'))
    wa=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==WITNESS_ACTION,Audit.object_id==witness.id)).all())
    need(len(wa)==1 and wa[0].actor_id==p.user_id and digest(wa[0].data)==digest(wdata))
    obs=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.id==witness.observation_id))
    evt=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.id==witness.event_id,
        Event.kind=='observation.data',Event.idempotency_key=='data:'+witness.observation_id))
    need(obs is not None and evt is not None and evt.status=='received')
    received=observation_receipt(db,p,obs)
    need(type(received) is dict and type(received.get('data_reception')) is dict
        and digest(received['data_reception'])==wdata.get('receipt_hash'))
    commands=list(db.scalars(select(AutoEvaluationPolicyCommandV356).where(
        AutoEvaluationPolicyCommandV356.workspace_id==p.workspace_id,AutoEvaluationPolicyCommandV356.task_id==task.id,
        AutoEvaluationPolicyCommandV356.data['result']['policy_snapshot']['version'].as_integer()==witness.policy_version).limit(2)).all())
    need(len(commands)==1 and commands[0].actor_id==p.user_id and commands[0].data.get('action')=='enable')
    saved=_checked_command(db,p,task,commands[0]);snapshot=saved['policy_snapshot']
    need(snapshot['enabled'] is True and snapshot['version']==witness.policy_version
        and snapshot['policy']['policy_hash']==value['policy_hash'])
    found=[entry for entry in _inventory(db,p,task) if entry[0].id==origin.command_id]
    need(len(found)==1)
    command,record,job=found[0]
    expected_request=str(uuid5(NAMESPACE,':'.join([p.workspace_id,task.id,witness.event_id,witness.policy_id,str(witness.policy_version)])))
    need(job.id==origin.job_id and record['actor_id']==p.user_id and record['request_id']==value.get('request_id')==expected_request
        and record['scope']['instance_id']==task.data['instance_id'])
    return origin


def authority(db,p,task,job):
    """Check an auto origin under Task→Policy locks; return None for manual jobs."""
    from . import auto_evaluation_policy_v356 as policy
    rows=db.scalars(select(AutoEvaluationTriggerV356).where(AutoEvaluationTriggerV356.workspace_id==p.workspace_id,
        AutoEvaluationTriggerV356.job_id==job.id)).all()
    need(len(rows)<=1)
    if not rows:
        # A missing origin must not turn an automatic command into a manual one.
        orphan=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,Audit.action==ACTION,
            Audit.data['job_id'].as_string()==job.id).limit(1))
        need(orphan is None,'自动评价来源缺失；原操作保留')
        return None
    origin=rows[0];value=origin.data
    need(type(value) is dict and value.get('binding_hash')==digest({k:v for k,v in value.items() if k!='binding_hash'}))
    command=db.get(Command,origin.command_id)
    need(command is not None and origin.workspace_id==task.workspace_id==p.workspace_id and origin.task_id==task.id
        and origin.job_id==job.id==command.job_id and command.task_id==task.id
        and value.get('command_id')==command.id and value.get('job_id')==job.id and value.get('actor_id')==p.user_id
        and value.get('event_id')==origin.event_id and value.get('observation_id')==origin.observation_id
        and value.get('policy_id')==origin.policy_id and value.get('policy_version')==origin.policy_version
        and value.get('request_id')==command.request_id)
    proofs=db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==ACTION,Audit.object_id==origin.id)).all()
    need(len(proofs)==1 and proofs[0].actor_id==p.user_id and digest(proofs[0].data)==digest(value))
    row=policy.current(db,p,task.id,lock=True);need(row is not None and row.id==origin.policy_id
        and row.version==origin.policy_version and row.data.get('policy_hash')==value.get('policy_hash'))
    policy.verify(db,p,task,row)
    obs,evt,receipt=_event(db,p,origin.observation_id)
    need(evt.id==origin.event_id and obs.entity_id in task.data.get('entity_ids',[]) and digest(receipt)==value.get('receipt_hash'))
    from .auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356,check
    witness=db.get(AutoEvaluationAckWitnessV356,value.get('witness_id'))
    need(witness is not None and witness.event_id==evt.id and witness.observation_id==obs.id)
    check(db,p,task,row,witness,obs=obs,evt=evt,receipt=receipt)
    return origin
