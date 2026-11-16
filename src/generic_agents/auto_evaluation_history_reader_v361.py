"""Verify historic origin as data using the CURRENT authorized reader.

Never construct a Principal for the original actor and never issue authority.
The caller must first validate current session, membership and task management.
"""
from uuid import uuid5
from sqlalchemy import select
from fastapi import HTTPException
from .contracts import digest
from .models import Audit, Observation, Event
from .auto_evaluation_ack_witness_v356 import _anchor,ACTION as WITNESS_ACTION
from .auto_evaluation_trigger_v356 import ACTION, NAMESPACE
from .auto_evaluation_policy_v356 import AutoEvaluationPolicyCommandV356,_checked_command
from .goal_evaluation_append_store_v297 import _inventory
from .instance_data_receipts import observation_receipt

def need(value):
    if not value: raise HTTPException(409,'历史来源或接收记录无法核对，未保存停止请求')

def checked_history(db, current, task, origin, witness):
    """Verify original actors as facts, while all readers use `current`."""
    try:
        value=origin.data
        need(type(value) is dict and value.get('schema')=='auto-evaluation-trigger-v356'
            and value.get('binding_hash')==digest({k:v for k,v in value.items() if k!='binding_hash'}))
        historical_actor=value['actor_id']
        need(type(historical_actor) is str and bool(historical_actor)
            and origin.workspace_id==current.workspace_id==task.workspace_id
            and origin.task_id==task.id and origin.event_id==witness.event_id
            and origin.observation_id==witness.observation_id and origin.policy_id==witness.policy_id
            and origin.policy_version==witness.policy_version and value['witness_id']==witness.id
            and value['workspace_id']==current.workspace_id and value['task_id']==task.id
            and value['event_id']==witness.event_id and value['observation_id']==witness.observation_id
            and value['policy_id']==witness.policy_id and value['policy_version']==witness.policy_version
            and value['command_id']==origin.command_id and value['job_id']==origin.job_id)
        audits=list(db.scalars(select(Audit).where(Audit.workspace_id==current.workspace_id,
            Audit.action==ACTION,Audit.object_id==origin.id).limit(2)))
        need(len(audits)==1 and audits[0].actor_id==historical_actor and digest(audits[0].data)==digest(value))
        wdata=_anchor(witness)
        need(wdata['actor_id']==historical_actor and wdata['policy_hash']==value['policy_hash']
            and wdata['receipt_hash']==value['receipt_hash'])
        witness_audits=list(db.scalars(select(Audit).where(Audit.workspace_id==current.workspace_id,
            Audit.action==WITNESS_ACTION,Audit.object_id==witness.id).limit(2)))
        need(len(witness_audits)==1 and witness_audits[0].actor_id==historical_actor
            and digest(witness_audits[0].data)==digest(wdata))
        obs=db.scalar(select(Observation).where(Observation.workspace_id==current.workspace_id,
                                                Observation.id==witness.observation_id))
        evt=db.scalar(select(Event).where(Event.workspace_id==current.workspace_id,Event.id==witness.event_id,
            Event.kind=='observation.data',Event.idempotency_key=='data:'+witness.observation_id))
        need(obs is not None and evt is not None and evt.status=='received')
        receipt=observation_receipt(db,current,obs)
        need(type(receipt) is dict and type(receipt.get('data_reception')) is dict
            and digest(receipt['data_reception'])==wdata['receipt_hash'])
        commands=list(db.scalars(select(AutoEvaluationPolicyCommandV356).where(
            AutoEvaluationPolicyCommandV356.workspace_id==current.workspace_id,
            AutoEvaluationPolicyCommandV356.task_id==task.id,
            AutoEvaluationPolicyCommandV356.data['result']['policy_snapshot']['version'].as_integer()==witness.policy_version).limit(2)))
        need(len(commands)==1 and commands[0].actor_id==historical_actor and commands[0].data.get('action')=='enable')
        saved=_checked_command(db,current,task,commands[0]);snapshot=saved['policy_snapshot']
        need(snapshot['enabled'] is True and snapshot['version']==witness.policy_version
             and snapshot['policy']['policy_hash']==value['policy_hash'])
        found=[entry for entry in _inventory(db,current,task) if entry[0].id==origin.command_id]
        need(len(found)==1)
        command,record,job=found[0]
        expected=str(uuid5(NAMESPACE,':'.join([current.workspace_id,task.id,witness.event_id,witness.policy_id,str(witness.policy_version)])))
        need(job.id==origin.job_id and record['actor_id']==historical_actor
            and record['request_id']==value['request_id']==expected
            and record['scope']['instance_id']==task.data['instance_id'])
        return command,record,job
    except HTTPException: raise
    except (KeyError,TypeError,ValueError,AttributeError,IndexError):
        raise HTTPException(409,'历史来源或接收记录无法核对，未保存停止请求') from None
