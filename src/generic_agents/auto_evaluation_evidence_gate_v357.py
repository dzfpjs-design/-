"""Bind automatic evaluation to an actually admitted observation.

The sealed evaluation record already contains the complete frozen query result
and admitted ACK inventory. The additional audit is a consistent cross-link,
not a tamper-proof ledger or a new execution grant. Manual appends are unchanged.
"""
from fastapi import HTTPException
import copy
from types import SimpleNamespace
from sqlalchemy import event, func, select
from .contracts import digest
from .models import Audit
from .temporal import instant, matches
from .data_requirements import RANK

ACTION='goal_evaluation.auto_admitted_evidence_v357'
SCHEMA='auto-evaluation-admitted-evidence-v357'

def need(condition):
    if not condition:raise HTTPException(409,'本次新观测未作为有效业务证据纳入评价；原评价保留')

def check_admitted(p,task,origin,frozen,acks):
    """Require one real admitted item and its independently recorded ACK."""
    try:
        source=origin.data;state=frozen['result'];report=state['result'];req=frozen['request']
        need(state['status']==report['status']=='sufficient')
        observations=report['observation_evidence'];need(type(observations) is list)
        selected=[x for x in observations if type(x) is dict and x.get('observation_id')==origin.observation_id]
        need(len(selected)==1);item=selected[0];receipt=item['data_reception'];cut=item['goal_evaluation_knowledge_cut_v288']
        fields=req['fields'];attrs=item['attributes'];need(type(fields) is list and type(attrs) is dict)
        relevant=sorted(k for k in fields if k in attrs and attrs[k] is not None)
        minimum=req['evidence_requirement']['minimum_verification'];actual=item['verification']
        need(minimum in RANK and actual in RANK and RANK[actual]>=RANK[minimum])
        need(bool(relevant) and item['source']=='processed_observation'
             and item['entity_id'] in task.data['entity_ids'] and item['entity_id'] in req['entity_ids']
             and receipt['event_id']==origin.event_id==cut['event_id']
             and digest(receipt)==source['receipt_hash']
             and receipt['receipt_hash']==cut['data_reception_hash']
             and cut['cut_at']==frozen['provenance']['cut_at'])
        observed=instant(item['observed_at']);received=instant(cut['received_at']);at=instant(cut['cut_at'])
        need(observed<=received<=at and matches(observed,frozen['provenance']['derived_window'],evaluated_at=at))
        ack_matches=[x for x in acks if type(x) is dict and x.get('observation_id')==origin.observation_id]
        need(len(ack_matches)==1);ack=ack_matches[0]
        need(ack['workspace_id']==p.workspace_id and ack['task_id']==task.id
             and ack['query_admitted'] is True and ack['entity_id']==item['entity_id']
             and ack['event_id']==origin.event_id and ack['memory_id']==cut['memory_id']
             and ack['data_reception_hash']==receipt['receipt_hash']
             and ack['source_id']==item['source_id'] and ack['observed_at']==item['observed_at']
             and ack['received_at']==cut['received_at'] and set(relevant).issubset(set(ack['fields'])))
        return item,ack,relevant
    except HTTPException:raise
    except (AttributeError,KeyError,TypeError,ValueError):
        raise HTTPException(409,'本次新观测证据或接收依据无法核对；原评价保留') from None

def build_binding(p,task,job,origin,row,frozen):
    try:
        record=row.data;source=origin.data;state=frozen['result'];req=frozen['request']
        need(origin.workspace_id==p.workspace_id==task.workspace_id==job.workspace_id==row.workspace_id
             and origin.task_id==task.id==row.task_id and origin.job_id==job.id==row.job_id
             and source['binding_hash']==digest({k:v for k,v in source.items() if k!='binding_hash'})
             and source['task_id']==task.id and source['job_id']==job.id
             and source['observation_id']==origin.observation_id and source['event_id']==origin.event_id
             and source['witness_id'] and row.evaluation_query_id==frozen['query_ref']['query_id']
             and record['evaluation_query_ref']==frozen['query_ref']
             and digest(record['evaluation_result'])==digest(state)
             and digest(record['evaluation_request'])==digest(req)
             and row.record_hash==record['record_hash'] and record['producer']['job_id']==job.id
             and record['cut_at']==frozen['provenance']['cut_at'])
        item,ack,relevant=check_admitted(p,task,origin,frozen,record['ack_refs'])
        value={'schema':SCHEMA,'workspace_id':p.workspace_id,'task_id':task.id,'producer_job_id':job.id,
            'origin_id':origin.id,'witness_id':source['witness_id'],'observation_id':origin.observation_id,
            'event_id':origin.event_id,'policy_id':origin.policy_id,'policy_version':origin.policy_version,
            'evaluation_id':row.id,'evaluation_record_hash':row.record_hash,'query_ref':frozen['query_ref'],
            'cut_at':record['cut_at'],'admitted_item_hash':digest(item),'ack_ref_hash':digest(ack),
            'relevant_fields':relevant,'evaluation_result_hash':digest(state)}
        return {**value,'binding_hash':digest(value)}
    except HTTPException:raise
    except (AttributeError,KeyError,TypeError,ValueError):
        raise HTTPException(409,'本次新观测证据或接收依据无法核对；原评价保留') from None

def bind(db,p,task,job,origin,row,frozen):
    value=build_binding(p,task,job,origin,row,frozen)
    found=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==ACTION,Audit.object_id==row.id).limit(2)))
    need(not found)
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,object_id=row.id,data=value))
    db.flush()
    return value

def verify(db,p,task,job,origin,row,frozen):
    value=build_binding(p,task,job,origin,row,frozen)
    found=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==ACTION,Audit.object_id==row.id).limit(2).execution_options(populate_existing=True)))
    need(len(found)==1 and found[0].actor_id==origin.data['actor_id'] and digest(found[0].data)==digest(value))
    return value

def verify_if_auto(db,p,task,job,row,frozen):
    """Check saved binding without granting or restarting producer execution."""
    from .auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356, ACTION as ORIGIN_ACTION
    origins=list(db.scalars(select(AutoEvaluationTriggerV356).where(
        AutoEvaluationTriggerV356.workspace_id==p.workspace_id,
        AutoEvaluationTriggerV356.job_id==job.id).limit(2)))
    need(len(origins)<=1)
    if not origins:
        orphan=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,Audit.action==ORIGIN_ACTION,
            Audit.data['job_id'].as_string()==job.id).limit(1))
        need(orphan is None)
        return None
    return verify(db,p,task,job,origins[0],row,frozen)

def verify_assessment(origin,row,evidence):
    """Check the actual current assessment, never alter historical reports."""
    try:
        from .goal_evaluation_provider_v288 import VIEW
        need(type(evidence) is dict and evidence['status']=='sufficient'
            and evidence[VIEW]['state']=='current'
            and evidence[VIEW]['evaluation_ref']=={'id':row.id,'record_hash':row.record_hash})
        expected=row.data['evaluation_result']['result']['observation_evidence']
        actual=evidence['observation_evidence']
        need(type(expected) is list and type(actual) is list)
        before=[item for item in expected if type(item) is dict and item.get('observation_id')==origin.observation_id]
        after=[item for item in actual if type(item) is dict and item.get('observation_id')==origin.observation_id]
        need(len(before)==len(after)==1 and digest(before[0])==digest(after[0]))
        return True
    except HTTPException:raise
    except (AttributeError,KeyError,TypeError,ValueError):
        raise HTTPException(409,'本次评价依据已过期或发生变化；原评价保留') from None

def install_commit_freshness(db,origin,row,record):
    """Recheck the detached assessment at the root transaction's commit hook.

    This bounds the validation point to before_commit, not to the physical
    PostgreSQL COMMIT instant; subsequent flush/commit latency is not bounded.
    No task, old report or persistent fact is modified by this check.
    """
    need(not db.in_nested_transaction())
    transaction=db.get_transaction();need(transaction is not None)
    active_key='auto_evaluation_commit_freshness_v357'
    cleanup_key='auto_evaluation_commit_cleanup_v357'
    need(active_key not in db.info)
    origin_pin=SimpleNamespace(observation_id=origin.observation_id)
    saved=copy.deepcopy(record)
    row_pin=SimpleNamespace(id=row.id,record_hash=row.record_hash,generation=row.generation,
        evaluation_query_id=row.evaluation_query_id,source_binding_hash=row.source_binding_hash,
        goal_snapshot_hash=row.goal_snapshot_hash,data=saved)
    def at_commit(session):
        state=session.info.get(active_key)
        need(type(state) is dict and state['transaction'] is transaction
            and session.get_transaction() is transaction and not session.in_nested_transaction())
        from .goal_evaluation_provider_v288 import _evidence, _stale
        now=session.connection().execute(select(func.clock_timestamp())).scalar_one()
        verify_assessment(origin_pin,row_pin,_evidence(row_pin,saved,now))
        # Re-read after projection/copy/hash work; use the exact same freshness
        # rule as the provider. ORM flush and physical COMMIT still follow.
        final_now=session.connection().execute(select(func.clock_timestamp())).scalar_one()
        need(not _stale(saved,final_now))
    if cleanup_key not in db.info:
        def at_end(session,ended):
            state=session.info.get(active_key)
            if type(state) is dict and ended is state['transaction']:
                callback=state['callback']
                if event.contains(session,'before_commit',callback):event.remove(session,'before_commit',callback)
                session.info.pop(active_key,None)
        event.listen(db,'after_transaction_end',at_end)
        db.info[cleanup_key]=at_end
    event.listen(db,'before_commit',at_commit)
    db.info[active_key]={'transaction':transaction,'callback':at_commit}
