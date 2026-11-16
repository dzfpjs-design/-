"""Actual PG scheduler boundary; synthetic approval rows do not grant execution."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Capability,DomainInstance,Job,Membership,Task
from generic_agents.layered_approval_models_v175 import ApprovalPolicyV175,ApprovalPolicyVersionV175,ApprovalManagedTaskV175
from generic_agents.worker import claim
from generic_agents.goal_approval_claim_barrier_v354 import project


@pytest.fixture
def family(account):
    workspace=account[2]
    with Session(engine()) as db:
        user=db.scalar(select(Membership.user_id).where(Membership.workspace_id==workspace))
        package=Capability(workspace_id=workspace,namespace='scheduler-test',name='test',release_version='1',kind='domain_package',content_hash='0'*64,data={})
        instance=DomainInstance(workspace_id=workspace,namespace='scheduler-test',data={})
        db.add_all([package,instance]);db.flush()
        policy=ApprovalPolicyV175(workspace_id=workspace,instance_id=instance.id,business_id='test',data={});db.add(policy);db.flush()
        version=ApprovalPolicyVersionV175(workspace_id=workspace,policy_id=policy.id,revision=1,actor_id=user,data={});db.add(version);db.flush()
        def new(status,kind='check_native_business_goal'):
            task=Task(workspace_id=workspace,title='simulation test',owner_id=user,package_id=package.id,data={});db.add(task);db.flush()
            managed=None
            if status is not None:
                managed=ApprovalManagedTaskV175(workspace_id=workspace,task_id=task.id,policy_id=policy.id,policy_version_id=version.id,owner_id=user,status=status,data={});db.add(managed);db.flush()
            job=Job(workspace_id=workspace,kind=kind,idempotency_key='claim-'+uuid4().hex,data={'task_id':task.id,'actor_id':user});db.add(job);db.flush()
            return job.id,managed.id if managed else None
        rows={x:new(x) for x in ('draft','in_review','returned','withdrawn','approved','accepted','acceptance_returned','executing')}
        rows['unmanaged']=new(None);rows['other_kind']=new('accepted','other_local_test');db.commit()
    return workspace,rows


def test_queued_goal_claims_only_while_managed_approval_is_executing(family):
    workspace,rows=family
    for status in ('draft','in_review','returned','withdrawn','approved','accepted','acceptance_returned'):
        job_id,_=rows[status];assert claim(job_id=job_id,workspace_id=workspace) is None
        with Session(engine()) as db:
            job=db.get(Job,job_id);assert job.status=='queued' and job.attempts==0 and job.lease_token is None
    for name in ('executing','unmanaged','other_kind'):
        job_id,_=rows[name];result=claim(job_id=job_id,workspace_id=workspace)
        assert result and result[0]==job_id
    accepted,managed_id=rows['accepted']
    # A state change only affects scheduler eligibility; it is not a validated
    # approval transition or authority to perform the actual Goal handler.
    with Session(engine()) as db:
        db.get(ApprovalManagedTaskV175,managed_id).status='executing';db.commit()
    result=claim(job_id=accepted,workspace_id=workspace);assert result and result[0]==accepted


def test_interrupted_running_goal_can_be_recovered_after_approval_closes(family):
    workspace,rows=family;job_id,_=rows['accepted']
    with Session(engine()) as db:
        job=db.get(Job,job_id);job.status='running';job.attempts=1;job.lease_token='old-token';job.lease_until=datetime.now(timezone.utc)-timedelta(seconds=5);db.commit()
    result=claim(job_id=job_id,workspace_id=workspace);assert result and result[0]==job_id and result[1]!='old-token'
    with Session(engine()) as db:
        job=db.get(Job,job_id);assert job.attempts==2 and job.data['lease_recoveries']==1


def test_status_projection_explains_hold_without_rewriting_job():
    worker={'state':'queued','job_status':'queued','historical':False,'attempts':0}
    assert project(worker,{'status':'accepted'})['state']=='approval_closed'
    assert project(worker,{'status':'returned'})['state']=='waiting_approval'
    assert project(worker,{'status':'executing'})==worker
    assert project(worker,None)==worker
    assert project({'state':'running'}, {'status':'accepted'})['state']=='running'
    assert worker=={'state':'queued','job_status':'queued','historical':False,'attempts':0}
