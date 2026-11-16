"""Narrow admission-gate tests with real PG original observation/independent ACK.

The task's saved evaluation, producer, frozen query, admitted item and ACKRef
are synthetic projections of those real inputs. These tests do not prove an
actual published append, candidate, promotion, broker, or user journey.
"""
import copy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Event, Memory, Observation, Task
from generic_agents.instance_data_receipts import observation_receipt
from generic_agents.worker import process_once
from generic_agents import auto_evaluation_evidence_gate_v357 as gate
from tests.test_auto_evaluation_ack_receiver_v356 import prepare


@pytest.fixture
def admitted(client,account,monkeypatch):
    scene=prepare(client,account,monkeypatch,False)
    assert process_once(scene.job,deliver=False)
    with Session(engine()) as db:
        obs=db.get(Observation,scene.identity); task=db.get(Task,scene.task)
        event=db.scalar(select(Event).where(Event.workspace_id==scene.p.workspace_id,
            Event.kind=='observation.data',Event.idempotency_key=='data:'+obs.id))
        assert event and event.status=='received'
        receipt=observation_receipt(db,scene.p,obs)['data_reception']
        memories=list(db.scalars(select(Memory).where(Memory.workspace_id==scene.p.workspace_id,
            Memory.data['event_id'].as_string()==event.id,Memory.data['type'].as_string()=='observation_evidence')))
        assert len(memories)==1 and memories[0].data['data_reception']==receipt
        required=task.data['goal_spec']['required_fields']
        attrs={k:v for k,v in obs.data['extracted']['attributes'].items() if k in required}
        assert attrs and obs.entity_id in task.data['entity_ids']
        cut=datetime.now(timezone.utc).isoformat()
        item={'observation_id':obs.id,'entity_id':obs.entity_id,'version':obs.version,'source_id':obs.source_id,
            'observed_at':obs.observed_at.isoformat(),'attributes':attrs,'source':'processed_observation','verification':'unverified_extraction',
            'data_reception':copy.deepcopy(receipt),'goal_evaluation_knowledge_cut_v288':{
                'cut_at':cut,'event_id':event.id,'received_at':event.received_at.isoformat(),
                'memory_id':memories[0].id,'memory_created_at':memories[0].created_at.isoformat(),
                'observation_created_at':obs.created_at.isoformat(),'data_reception_hash':receipt['receipt_hash']}}
        job=NS(id=str(uuid4()),workspace_id=scene.p.workspace_id)
        origin_data={'task_id':task.id,'job_id':job.id,'observation_id':obs.id,'event_id':event.id,
            'witness_id':str(uuid4()),'receipt_hash':digest(receipt),'actor_id':scene.p.user_id}
        origin_data['binding_hash']=digest(origin_data)
        origin=NS(id=str(uuid4()),workspace_id=scene.p.workspace_id,task_id=task.id,job_id=job.id,
            observation_id=obs.id,event_id=event.id,policy_id=str(uuid4()),policy_version=1,data=origin_data)
        request={'fields':required,'entity_ids':task.data['entity_ids'],'evidence_requirement':{'minimum_verification':'unverified_extraction'}}
        state={'status':'sufficient','result':{'status':'sufficient','observation_evidence':[item],'excluded_evidence':[]}}
        query_ref={'query_id':str(uuid4()),'version':1,'data_hash':'a'*64}
        ack={'workspace_id':scene.p.workspace_id,'task_id':task.id,'query_admitted':True,
            'entity_id':obs.entity_id,'event_id':event.id,'memory_id':memories[0].id,
            'data_reception_hash':receipt['receipt_hash'],'source_id':obs.source_id,
            'observed_at':obs.observed_at.isoformat(),'received_at':event.received_at.isoformat(),
            'observation_id':obs.id,'fields':list(attrs)}
        record={'evaluation_query_ref':query_ref,'evaluation_result':copy.deepcopy(state),
            'evaluation_request':copy.deepcopy(request),'record_hash':'b'*64,'producer':{'job_id':job.id},
            'cut_at':cut,'ack_refs':[ack]}
        row=NS(id=str(uuid4()),workspace_id=scene.p.workspace_id,task_id=task.id,job_id=job.id,
            evaluation_query_id=query_ref['query_id'],record_hash=record['record_hash'],data=record)
        frozen={'query_ref':query_ref,'request':request,'result':state,'provenance':{'cut_at':cut,
            'derived_window':{'mode':'latest','start':(obs.observed_at-timedelta(days=1)).isoformat(),'end':cut}}}
        # Expire session-independent SQLAlchemy attributes before session exits.
        p=scene.p; db.expunge(task)
        return NS(p=p,task=task,job=job,origin=origin,row=row,frozen=frozen)


def sync_state(s):
    """Model a changed frozen result also reaching the candidate record."""
    s.row.data['evaluation_result']=copy.deepcopy(s.frozen['result'])


def check(s):return gate.build_binding(s.p,s.task,s.job,s.origin,s.row,s.frozen)


def test_admitted_real_original_and_ack_bind_deterministically(admitted):
    s=admitted; value=check(s)
    assert value==check(s)
    assert value['observation_id']==s.origin.observation_id
    assert value['event_id']==s.origin.event_id
    assert value['relevant_fields'] and value['binding_hash']


@pytest.mark.parametrize('case',[
    'only_excluded','only_metadata','duplicate_admission','unrelated_field','wrong_entity','wrong_event',
    'altered_receipt','bad_cut','ack_not_admitted','ack_wrong_memory','query_mismatch',
    'candidate_result_mismatch','insufficient','outside_window','future_reception','verification_below_minimum'])
def test_unusable_or_inconsistent_evidence_never_binds(admitted,case):
    s=admitted;state=s.frozen['result'];report=state['result'];item=report['observation_evidence'][0]
    if case=='only_excluded':
        report['excluded_evidence']=[{'observation_id':s.origin.observation_id,'reason':'outside window'}]
        report['observation_evidence']=[];sync_state(s)
    elif case=='only_metadata':
        report['observation_evidence']=[];report['metadata']={'observation_id':s.origin.observation_id};sync_state(s)
    elif case=='duplicate_admission':report['observation_evidence'].append(copy.deepcopy(item));sync_state(s)
    elif case=='unrelated_field':item['attributes']={'unrelated':1};sync_state(s)
    elif case=='wrong_entity':item['entity_id']=str(uuid4());sync_state(s)
    elif case=='wrong_event':item['goal_evaluation_knowledge_cut_v288']['event_id']=str(uuid4());sync_state(s)
    elif case=='altered_receipt':item['data_reception']['receipt_hash']='0'*64;sync_state(s)
    elif case=='bad_cut':item['goal_evaluation_knowledge_cut_v288']['cut_at']='2000-01-01T00:00:00+00:00';sync_state(s)
    elif case=='ack_not_admitted':s.row.data['ack_refs'][0]['query_admitted']=False
    elif case=='ack_wrong_memory':s.row.data['ack_refs'][0]['memory_id']=str(uuid4())
    elif case=='query_mismatch':s.row.evaluation_query_id=str(uuid4())
    elif case=='candidate_result_mismatch':item['attributes'][next(iter(item['attributes']))]='changed'
    elif case=='insufficient':state['status']=report['status']='insufficient';sync_state(s)
    elif case=='outside_window':s.frozen['provenance']['derived_window']['start']='2999-01-01T00:00:00+00:00'
    elif case=='verification_below_minimum':
        s.frozen['request']['evidence_requirement']['minimum_verification']='confirmed'
        s.row.data['evaluation_request']=copy.deepcopy(s.frozen['request'])
    elif case=='future_reception':
        item['goal_evaluation_knowledge_cut_v288']['received_at']='2999-01-01T00:00:00+00:00';sync_state(s)
    with pytest.raises(HTTPException) as error:check(s)
    assert error.value.status_code==409


def test_binding_persisted_once_and_wrong_actor_or_duplicate_is_rejected(admitted):
    s=admitted
    with Session(engine()) as db:
        first=gate.bind(db,s.p,s.task,s.job,s.origin,s.row,s.frozen);db.commit()
        assert gate.verify(db,s.p,s.task,s.job,s.origin,s.row,s.frozen)==first
        with pytest.raises(HTTPException):gate.bind(db,s.p,s.task,s.job,s.origin,s.row,s.frozen)
        other=NS(**vars(s.origin));other.data={**s.origin.data,'actor_id':str(uuid4())}
        other.data['binding_hash']=digest({k:v for k,v in other.data.items() if k!='binding_hash'})
        with pytest.raises(HTTPException):gate.verify(db,s.p,s.task,s.job,other,s.row,s.frozen)


def assessment_fixture(s,time_basis='rolling_latest',max_age=60):
    """Build only the provider projection from a synthetic saved evaluation.

    The recorded observation and ACK originate from the real disposable PG
    fixture; the evaluation and provider inputs here remain synthetic.
    """
    row=s.row
    row.generation=1
    row.source_binding_hash='c'*64
    row.goal_snapshot_hash='d'*64
    row.data['evaluation_result']['result']['evidence']=[]
    row.data['preparation_policy']={'time_basis':time_basis,'max_age_seconds':max_age}
    return row,row.data


def test_current_provider_evidence_at_rolling_boundary_but_not_after(admitted):
    from generic_agents.goal_evaluation_provider_v288 import _evidence,VIEW
    s=admitted;row,value=assessment_fixture(s)
    item=value['evaluation_result']['result']['observation_evidence'][0]
    observed=datetime.fromisoformat(item['observed_at'])
    boundary=observed+timedelta(seconds=60)
    at_boundary=_evidence(row,value,boundary)
    assert at_boundary[VIEW]['state']=='current'
    assert gate.verify_assessment(s.origin,row,at_boundary) is True
    after_boundary=_evidence(row,value,boundary+timedelta(microseconds=1))
    assert after_boundary[VIEW]['state']=='stale' and after_boundary['status']=='insufficient'
    with pytest.raises(HTTPException) as error:gate.verify_assessment(s.origin,row,after_boundary)
    assert error.value.status_code==409


def test_current_assessment_rejects_metadata_only_or_mutated_admitted_item(admitted):
    from generic_agents.goal_evaluation_provider_v288 import _evidence,VIEW
    s=admitted;row,value=assessment_fixture(s)
    observed=datetime.fromisoformat(value['evaluation_result']['result']['observation_evidence'][0]['observed_at'])
    evidence=_evidence(row,value,observed+timedelta(seconds=30))
    assert gate.verify_assessment(s.origin,row,evidence)
    missing=copy.deepcopy(evidence);missing['observation_evidence']=[]
    missing['excluded_evidence']=[{'observation_id':s.origin.observation_id,'reason':'不在有效窗口'}]
    with pytest.raises(HTTPException):gate.verify_assessment(s.origin,row,missing)
    changed=copy.deepcopy(evidence)
    changed['observation_evidence'][0]['attributes'][next(iter(changed['observation_evidence'][0]['attributes']))]='changed'
    with pytest.raises(HTTPException):gate.verify_assessment(s.origin,row,changed)
    wrong_ref=copy.deepcopy(evidence);wrong_ref[VIEW]['evaluation_ref']['record_hash']='e'*64
    with pytest.raises(HTTPException):gate.verify_assessment(s.origin,row,wrong_ref)


def test_fixed_window_evaluation_remains_current_and_does_not_fabricate_new_data(admitted):
    from generic_agents.goal_evaluation_provider_v288 import _evidence,VIEW
    s=admitted;row,value=assessment_fixture(s,time_basis='fixed_window')
    observed=datetime.fromisoformat(value['evaluation_result']['result']['observation_evidence'][0]['observed_at'])
    evidence=_evidence(row,value,observed+timedelta(days=30))
    assert evidence[VIEW]['state']=='current' and gate.verify_assessment(s.origin,row,evidence)
    # A fixed historical window can remain internally valid but is not proof
    # that a current physical state is still true.
    assert evidence[VIEW]['physical_state_verified'] is False
