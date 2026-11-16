"""Normal waiting reception is insufficient, not a failed existing-data check.

All success uses ordinary local extraction and the independent receiver. Fault
cases damage only the named saved proof; no receiver/status checker is mocked.
The global source case verifies its genuinely unversioned original contract.
"""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from generic_agents import models as m,worker,workspace_observation_intake as intake
from tests.test_query_field_intent_pg_v246 import (ORIGINAL,create,extract,legacy_body,
    modern_base,modern_body,only_local)
from tests.test_scoped_data_gap_intake_pg_v245 import scene,event,good
from tests.test_vertical_slice import manifest,publish


def receipt_memory(db,s,observation):
    delivered=event(db,s,observation['id'])
    return list(db.scalars(select(m.Memory).where(m.Memory.workspace_id==s.workspace_id,
        m.Memory.data['event_id'].as_string()==delivered.id,
        m.Memory.data['type'].as_string()=='observation_evidence')))


def need_job(s,identity,request_id='initial'):
    with Session(s.bound) as db:
        rows=list(db.scalars(select(m.Job).where(m.Job.workspace_id==s.workspace_id,
            m.Job.kind=='check_workspace_data_need',m.Job.status=='queued',
            m.Job.idempotency_key=='workspace-need:'+identity+':'+request_id,
            m.Job.data['query_id'].as_string()==identity)))
        assert len(rows)==1
        return rows[0].id


def test_original_need_job_succeeds_while_waiting_and_explicit_recheck_closes_only_after_real_ack(scene):
    s=scene;initial=create(s,modern_body(s));observation=extract(s)
    original_job=need_job(s,initial['id'])
    assert worker.process_once(original_job,kinds=['check_workspace_data_need'],workspace_id=s.workspace_id,deliver=False)
    waiting=good(s.client.get(modern_base(s)+'/'+initial['id']))
    assert waiting['status']=='needs_existing_evidence' and waiting['query_status']=='insufficient'
    assert waiting['result_status']=='insufficient' and waiting['records']==[]
    query=good(s.client.post('/api/data/queries',json=legacy_body(s)))
    result=query['data']['result']
    assert query['status']=='insufficient' and result['observation_evidence']==[]
    excluded=next(row for row in result['excluded_evidence'] if row.get('observation_id')==observation['id'])
    assert excluded['status']=='pending_data_reception' and excluded['delivery_status']=='pending_delivery'
    assert '独立接收' in excluded['reason'] and 'attributes' not in excluded
    with Session(s.bound) as db:
        saved=db.get(m.Job,original_job)
        assert saved.status=='succeeded' and saved.attempts==1
        assert event(db,s,observation['id']).status=='pending_delivery' and not receipt_memory(db,s,observation)
        assert db.get(m.DataRun,initial['id']).status=='insufficient'
        only_local(db,s)
    intake.deliver_pending(s.workspace_id)
    request_id=str(uuid4())
    good(s.client.post(modern_base(s)+'/'+initial['id']+'/check',json={
        'request_id':request_id,'expected_version':waiting['version'],'reason':'独立收件后按原条件核对已有登记'}))
    recheck_job=need_job(s,initial['id'],request_id)
    assert worker.process_once(recheck_job,kinds=['check_workspace_data_need'],workspace_id=s.workspace_id,deliver=False)
    complete=good(s.client.get(modern_base(s)+'/'+initial['id']))
    assert complete['status']=='resolved' and complete['query_status']=='insufficient' and complete['result_status']=='sufficient'
    assert complete['purpose']==ORIGINAL and complete['records'][0]['extracted_values'][s.field]==27
    with Session(s.bound) as db:
        assert db.get(m.DataRun,initial['id']).status=='insufficient'
        assert db.get(m.Job,original_job).status==db.get(m.Job,recheck_job).status=='succeeded'
        assert event(db,s,observation['id']).status=='received' and len(receipt_memory(db,s,observation))==1
        assert db.scalar(select(func.count()).select_from(m.Invocation).where(m.Invocation.workspace_id==s.workspace_id))==1
        only_local(db,s)


@pytest.mark.parametrize('damage',['bad_pending_payload','missing_received_memory','withdrawn_received_event'])
def test_damaged_pending_or_claimed_receipt_fails_closed_without_receiving_or_repairing(scene,damage):
    s=scene;initial=create(s,modern_body(s));observation=extract(s)
    if damage!='bad_pending_payload':intake.deliver_pending(s.workspace_id)
    with Session(s.bound) as db:
        delivered=event(db,s,observation['id'])
        if damage=='bad_pending_payload':
            value=copy.deepcopy(delivered.data);value['extracted']['attributes'][s.field]=999;delivered.data=value
        elif damage=='missing_received_memory':
            memories=receipt_memory(db,s,observation);assert len(memories)==1;db.delete(memories[0])
        else:delivered.status='pending_delivery'
        db.commit()
        saved_status=delivered.status
        memories_before=[row.id for row in receipt_memory(db,s,observation)]
    assert s.client.post('/api/data/queries',json=legacy_body(s)).status_code==409
    assert s.client.get(modern_base(s)+'/'+initial['id']).status_code==409
    job=need_job(s,initial['id'])
    assert worker.process_once(job,kinds=['check_workspace_data_need'],workspace_id=s.workspace_id,deliver=False)
    with Session(s.bound) as db:
        assert db.get(m.Job,job).status=='failed' and db.get(m.Job,job).attempts==1
        assert db.get(m.DataRun,initial['id']).status=='insufficient'
        assert event(db,s,observation['id']).status==saved_status
        assert [row.id for row in receipt_memory(db,s,observation)]==memories_before
        assert db.scalar(select(func.count()).select_from(m.Invocation).where(m.Invocation.workspace_id==s.workspace_id))==1
        only_local(db,s)


def test_genuine_unversioned_global_source_keeps_original_no_managed_ack_contract(scene):
    s=scene
    package=publish(s.client,manifest(namespace='quantity_global_v246',name='global_'+uuid4().hex[:8],
        specification={'entity_schemas':{'entry':{'type':'object','additionalProperties':False,
            'properties':{s.field:{'type':'integer','title':'数量'}}}}}))
    entity=good(s.client.post('/api/entities',json={'package_id':package['id'],'entity_type':'entry',
        'external_id':str(uuid4()),'attributes':{s.field:11}}),201)
    global_source=copy.copy(s);global_source.entity=entity
    observation=extract(global_source)
    result=good(s.client.post('/api/data/queries',json=legacy_body(global_source)))
    assert result['status']=='sufficient'
    evidence=result['data']['result']['observation_evidence']
    assert len(evidence)==1 and evidence[0]['observation_id']==observation['id']
    assert evidence[0]['attributes'][s.field]==27 and 'data_reception' not in evidence[0]
    with Session(s.bound) as db:
        assert event(db,s,observation['id']).status=='pending_delivery'
        assert not receipt_memory(db,s,observation)
        only_local(db,s)
