"""Automatic origin checks; database/integration coverage is a separate gate."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import auto_evaluation_trigger_v356 as trigger
from generic_agents import auto_evaluation_policy_v356 as policy
from generic_agents.contracts import digest


def test_live_origin_revocation_and_tampering_fail_closed(monkeypatch):
    ids=[str(uuid4()) for _ in range(10)]
    ws,task_id,job_id,cmd_id,event_id,obs_id,policy_id,actor,request_id,entity_id=ids
    p=NS(workspace_id=ws,user_id=actor);task=NS(workspace_id=ws,id=task_id,data={'entity_ids':[entity_id]});job=NS(id=job_id)
    receipt={'schema':'test-independent-ack','receipt_hash':'abc'}
    value={'schema':'auto-evaluation-trigger-v356','workspace_id':ws,'task_id':task_id,'event_id':event_id,
        'observation_id':obs_id,'receipt_hash':digest(receipt),'policy_id':policy_id,'policy_version':1,
        'policy_hash':'saved','command_id':cmd_id,'job_id':job_id,'request_id':request_id,'actor_id':actor,'witness_id':str(uuid4())}
    value['binding_hash']=digest(value)
    origin=NS(id=str(uuid4()),workspace_id=ws,task_id=task_id,event_id=event_id,observation_id=obs_id,
        policy_id=policy_id,policy_version=1,command_id=cmd_id,job_id=job_id,data=value)
    command=NS(id=cmd_id,job_id=job_id,task_id=task_id,request_id=request_id)
    audit=NS(actor_id=actor,data=value.copy());state=NS(status='enabled',id=policy_id,version=1,data={'policy_hash':'saved'})
    class Results:
        def __init__(self,rows):self.rows=rows
        def all(self):return self.rows
    class DB:
        def scalars(self,q):
            return Results([origin] if 'auto_evaluation_triggers_v356' in str(q) else [audit])
        def get(self,cls,key):return command if cls is trigger.Command else NS(event_id=event_id,observation_id=obs_id)
    monkeypatch.setattr(policy,'current',lambda *a,**k:state)
    def verify(*args):
        if state.status!='enabled':raise HTTPException(409,'已撤销')
    monkeypatch.setattr(policy,'verify',verify)
    monkeypatch.setattr(trigger,'_event',lambda *a:(NS(id=obs_id,entity_id=entity_id),NS(id=event_id),receipt))
    from generic_agents import auto_evaluation_ack_witness_v356 as witnesses
    monkeypatch.setattr(witnesses,'check',lambda *a,**k:None)
    assert trigger.authority(DB(),p,task,job) is origin
    state.status='revoked'
    with pytest.raises(HTTPException):trigger.authority(DB(),p,task,job)
    state.status='enabled';origin.data={**value,'receipt_hash':'wrong'}
    with pytest.raises(HTTPException):trigger.authority(DB(),p,task,job)


def test_manual_append_passes_but_orphaned_auto_audit_cannot_be_manual():
    p=NS(workspace_id=str(uuid4()));task=NS(id=str(uuid4()));job=NS(id=str(uuid4()))
    db=Mock();db.scalars.return_value.all.return_value=[];db.scalar.return_value=None
    assert trigger.authority(db,p,task,job) is None
    db.scalar.return_value=str(uuid4())
    with pytest.raises(HTTPException):trigger.authority(db,p,task,job)


def test_historical_origin_recomputes_original_deterministic_nonce(monkeypatch):
    """Proof boundary test; independent DB chain is mocked, not end-to-end."""
    from uuid import uuid5
    from generic_agents import auto_evaluation_ack_witness_v356 as witness_mod
    from generic_agents import goal_evaluation_append_store_v297 as append
    ws,task_id,actor,event,obs,policy_id,witness_id,command_id,job_id,instance=[str(uuid4()) for _ in range(10)]
    p=NS(workspace_id=ws,user_id=actor);task=NS(workspace_id=ws,id=task_id,data={'instance_id':instance})
    request=str(uuid5(trigger.NAMESPACE,':'.join([ws,task_id,event,policy_id,'1'])))
    receipt={'schema':'fixture'};wdata={'actor_id':actor,'policy_hash':'a'*64,'receipt_hash':digest(receipt)}
    witness=NS(id=witness_id,event_id=event,observation_id=obs,policy_id=policy_id,policy_version=1)
    value={'schema':'auto-evaluation-trigger-v356','workspace_id':ws,'task_id':task_id,'event_id':event,
        'observation_id':obs,'policy_id':policy_id,'policy_version':1,'policy_hash':'a'*64,
        'receipt_hash':digest(receipt),'command_id':command_id,'job_id':job_id,'request_id':request,
        'actor_id':actor,'witness_id':witness_id}
    value['binding_hash']=digest(value)
    origin=NS(id=str(uuid4()),workspace_id=ws,task_id=task_id,event_id=event,observation_id=obs,
        policy_id=policy_id,policy_version=1,command_id=command_id,job_id=job_id,data=value)
    wa=NS(actor_id=actor,data=wdata);oa=NS(actor_id=actor,data=value)
    policy_command=NS(actor_id=actor,data={'action':'enable'})
    command=NS(id=command_id);job=NS(id=job_id)
    record={'actor_id':actor,'request_id':request,'scope':{'instance_id':instance}}
    class Results:
        def __init__(self,rows):self.rows=rows
        def all(self):return self.rows
    class DB:
        def __init__(self):self.index=0;self.scalar_index=0
        def scalars(self,q):
            rows=[[oa],[wa],[policy_command]][self.index];self.index+=1;return Results(rows)
        def scalar(self,q):
            self.scalar_index+=1;return NS(id=obs) if self.scalar_index==1 else NS(id=event,status='received')
    monkeypatch.setattr(witness_mod,'_anchor',lambda w:wdata)
    monkeypatch.setattr(trigger,'observation_receipt',lambda *a:{'data_reception':receipt})
    monkeypatch.setattr(policy,'_checked_command',lambda *a:{'policy_snapshot':{'enabled':True,'version':1,'policy':{'policy_hash':'a'*64}}})
    monkeypatch.setattr(append,'_inventory',lambda *a:[(command,record,job)])
    assert trigger.historical_origin(DB(),p,task,origin,witness) is origin
    # Make a mutually consistent forged origin+audit+command with a new nonce.
    value['request_id']=str(uuid4());value['binding_hash']=digest({k:v for k,v in value.items() if k!='binding_hash'})
    record['request_id']=value['request_id']
    with pytest.raises(HTTPException):trigger.historical_origin(DB(),p,task,origin,witness)
