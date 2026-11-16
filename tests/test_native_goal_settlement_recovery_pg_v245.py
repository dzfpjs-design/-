"""Recover an actually ACKed original without replaying old data or HTTP.

The normal new receiver/extraction creates every success record. Deleting only
its new local queue and queue audit below is explicit old-version/fault setup;
it does not fabricate an ACK, Observation, Memory, supplier or Invocation.
"""
import copy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from generic_agents import models as m,worker
from generic_agents import acquisition_ack_settlement_v245 as settlement
from generic_agents.api import app
from generic_agents.security import password_hash
from tests.test_acquisition_ack_settlement_pg_v245 import (
    simulated_supplier,mapped_scene,extract,ack,event,settle_job,counts,good)


def old_received_without_queue(client,simulated_supplier):
    s=mapped_scene(client,simulated_supplier);extract(s);identity=ack(s)
    with Session(s.bound) as db:
        job=db.get(m.Job,identity)
        assert job.status=='queued' and job.attempts==0
        for recorded in db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace_id,
            m.Audit.action==settlement.ACTION,m.Audit.object_id==identity)):
            db.delete(recorded)
        db.delete(job);db.commit()
        row=event(db,s)
        assert row.status=='received' and settle_job(db,s) is None
        s.before_event=copy.deepcopy(row.data);s.before_observation=copy.deepcopy(db.get(m.Observation,s.observation_id).data)
        s.before_memory=[(record.id,copy.deepcopy(record.data)) for record in db.scalars(select(m.Memory).where(
            m.Memory.workspace_id==s.workspace_id,m.Memory.data['event_id'].as_string()==row.id,
            m.Memory.data['type'].as_string()=='observation_evidence'))]
        assert len(s.before_memory)==1
        s.version=db.get(m.Acquisition,s.acquisition_id).version
    s.path='/api/workspace/tasks/'+s.task['id']+'/goal/acquisitions/'+s.acquisition_id+'/receipt'
    s.body={'expected_version':s.version,'reason':'仅恢复原已收件补采的本地核验，不重新采集'}
    return s


def test_normal_receipt_api_recovers_old_actual_ack_into_one_local_job_and_closes_same_request(client,simulated_supplier):
    s=old_received_without_queue(client,simulated_supplier)
    with Session(s.bound) as db:before=counts(db,s)
    first=good(client.post(s.path,json=s.body));assert first['id']==s.acquisition_id and first['status']=='awaiting_observation'
    second=good(client.post(s.path,json=s.body));assert second['status']=='awaiting_observation'
    with Session(s.bound) as db:
        job=settle_job(db,s);assert job is not None and job.status=='queued' and job.attempts==0;identity=job.id
        assert db.scalar(select(func.count()).select_from(m.Job).where(m.Job.workspace_id==s.workspace_id,m.Job.kind==settlement.KIND))==1
        requests=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace_id,
            m.Audit.action=='native_goal.data_ack_settlement_requested_v245',m.Audit.object_id==s.acquisition_id)))
        assert len(requests)==2 and all(row.actor_id==s.p.user_id and row.data['request']==s.body
            and row.data['job_id']==identity and row.data['replayed_data_event'] is False for row in requests)
        assert counts(db,s)==before and event(db,s).data==s.before_event
        assert db.get(m.Observation,s.observation_id).data==s.before_observation
        assert [(row.id,row.data) for row in db.scalars(select(m.Memory).where(m.Memory.workspace_id==s.workspace_id,
            m.Memory.data['event_id'].as_string()==event(db,s).id,m.Memory.data['type'].as_string()=='observation_evidence'))]==s.before_memory
    assert simulated_supplier.calls==[('POST','/acquisitions')]
    assert worker.process_once(identity,kinds=[settlement.KIND],workspace_id=s.workspace_id,deliver=False)
    result=good(client.post(s.path,json=s.body))  # completed history needs no fresh HTTP or new CAS grant
    assert result['status']=='succeeded' and result['closing_observation_id']==s.observation_id and not result['reservation_held']
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,s.acquisition_id)
        assert acquisition.status=='succeeded' and db.get(m.ObservationPlan,acquisition.data['observation_plan_id']).status=='completed'
        assert counts(db,s)==before and event(db,s).data==s.before_event
    assert simulated_supplier.calls==[('POST','/acquisitions')]


@pytest.mark.parametrize('fault',['memory','stale_version','wrong_parent','viewer'])
def test_old_received_status_never_repairs_bad_ack_or_bypasses_current_request_authority(client,simulated_supplier,fault):
    s=old_received_without_queue(client,simulated_supplier);path=s.path;body=copy.deepcopy(s.body)
    with Session(s.bound) as db:
        if fault=='memory':db.delete(db.get(m.Memory,s.before_memory[0][0]))
        if fault=='viewer':
            member=db.scalar(select(m.Membership).where(m.Membership.workspace_id==s.workspace_id,m.Membership.user_id==s.p.user_id))
            member.role='viewer'
        db.commit();before=counts(db,s)
    if fault=='stale_version':body['expected_version']=s.version+1
    if fault=='wrong_parent':path=path.replace(s.task['id'],str(uuid4()))
    response=client.post(path,json=body)
    assert response.status_code==({'viewer':403,'wrong_parent':404}.get(fault,409)),response.text[:700]
    with Session(s.bound) as db:
        assert settle_job(db,s) is None and counts(db,s)==before
        assert db.get(m.Acquisition,s.acquisition_id).status=='awaiting_observation' and event(db,s).status=='received'
    assert simulated_supplier.calls==[('POST','/acquisitions')]


def test_other_current_admin_requests_recovery_under_their_own_real_login_identity(client,simulated_supplier):
    s=old_received_without_queue(client,simulated_supplier);username='ack-recovery-admin-'+uuid4().hex[:8]
    password=uuid4().hex+uuid4().hex
    with Session(s.bound) as db:
        user=m.User(username=username,password_hash=password_hash(password));db.add(user);db.flush();identity=user.id
        db.add(m.Membership(workspace_id=s.workspace_id,user_id=identity,role='admin'));db.commit()
    with TestClient(app) as other:
        login=good(other.post('/api/login',json={'username':username,'password':password}))
        other.headers.update({'X-Workspace-ID':s.workspace_id,'X-CSRF-Token':login['csrf_token']})
        result=good(other.post(s.path,json=s.body));assert result['status']=='awaiting_observation'
    with Session(s.bound) as db:
        request=db.scalar(select(m.Audit).where(m.Audit.action=='native_goal.data_ack_settlement_requested_v245',m.Audit.object_id==s.acquisition_id))
        assert request.actor_id==identity and request.data['request']==s.body
        job=settle_job(db,s);assert job is not None and job.data['actor_id']==s.p.user_id
        job_id=job.id
    assert worker.process_once(job_id,kinds=[settlement.KIND],workspace_id=s.workspace_id,deliver=False)
    with Session(s.bound) as db:assert db.get(m.Acquisition,s.acquisition_id).status=='succeeded'
    assert simulated_supplier.calls==[('POST','/acquisitions')]
