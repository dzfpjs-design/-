"""The disabled scoped scanner must never impersonate an active worker."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import auto_evaluation_scanner_v356 as scanner
from generic_agents import auto_evaluation_policy_api_v356 as api


def test_global_gate_blocks_scanning_before_any_db_access(monkeypatch):
    monkeypatch.setattr(api,'automatic_runner_available',lambda:False)
    monkeypatch.setattr(scanner,'Session',lambda *a:pytest.fail('disabled scanner accessed database'))
    with pytest.raises(HTTPException) as error:scanner.tick(*[str(uuid4()) for _ in range(3)])
    assert error.value.status_code==409


def test_scope_never_accepts_malformed_or_ambiguous_identifiers():
    with pytest.raises(ValueError):scanner.tick('bad',str(uuid4()),str(uuid4()))

from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356 as Witness
from tests.test_auto_evaluation_ack_witness_v356 import scene


@pytest.fixture(autouse=True)
def synthetic_policy_proof(monkeypatch):
    # Fixture policy is deliberately synthetic; real proof guards have their
    # own policy PG tests. Exercise scanner behavior without forging an audit.
    from generic_agents import auto_evaluation_policy_v356 as policy
    monkeypatch.setattr(policy,'_checked_current',lambda *a:None)


def test_durable_queue_status_is_not_overwritten_by_a_late_failure(scene):
    with Session(engine()) as db:
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,data={'fixture':'not-authorization'})
        db.add(w);db.commit();identity=w.id
    scanner._record(scene.p.workspace_id,scene.task,identity,'queued')
    scanner._record(scene.p.workspace_id,scene.task,identity,'blocked')
    with Session(engine()) as db:
        row=db.scalar(select(scanner.AutoEvaluationScanStateV356).where(scanner.AutoEvaluationScanStateV356.witness_id==identity))
        assert row.status=='queued' and row.version==1
        view=scanner.task_status(db,scene.p,scene.task)
        assert view['records'][0]['status']=='queued_status_needs_verification'
        assert view['records'][0]['review_reference']==identity
        assert identity not in str({k:v for k,v in view['records'][0].items() if k!='review_reference'})


def test_failure_state_survives_a_fresh_session_and_is_not_called_complete(scene):
    with Session(engine()) as db:
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,data={'fixture':'not-authorization'})
        db.add(w);db.commit();identity=w.id
    scanner._record(scene.p.workspace_id,scene.task,identity,'unknown')
    with Session(engine()) as db:
        assert scanner.task_status(db,scene.p,scene.task)['records'][0]['status']=='outcome_unknown'


def test_blocked_older_witnesses_do_not_starve_later_page(scene,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from generic_agents.models import Task,Event,Observation
    from generic_agents import auto_evaluation_policy_api_v356 as api
    monkeypatch.setattr(api,'automatic_runner_available',lambda:True)
    instance=str(uuid4());last_obs=None
    with Session(engine()) as db:
        task=db.get(Task,scene.task);task.data={**task.data,'instance_id':instance}
        original=db.get(Observation,scene.obs);base=datetime.now(timezone.utc)-timedelta(minutes=2)
        for index in range(56):
            obs=Observation(workspace_id=scene.p.workspace_id,source_id=original.source_id,
                source_record_id='pagination-'+str(index),processing_version='1',entity_id=original.entity_id,
                status='processed',data={});db.add(obs);db.flush()
            evt=Event(workspace_id=scene.p.workspace_id,kind='observation.data',
                idempotency_key='data:'+obs.id,status='received',data={});db.add(evt);db.flush()
            witness=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=evt.id,
                observation_id=obs.id,policy_id=scene.policy,policy_version=1,
                created_at=base+timedelta(seconds=index),data={'actor_id':scene.p.user_id})
            db.add(witness);last_obs=obs.id
        db.commit()
    def fake_enqueue(_db,_p,_task,obs):
        if obs!=last_obs:raise HTTPException(409,'synthetic blocked')
        return object()
    monkeypatch.setattr(scanner,'enqueue',fake_enqueue)
    first=scanner.tick(scene.p.workspace_id,instance,scene.task)
    assert first['blocked']==50 and first['queued']==0 and first['after'] is not None
    second=scanner.tick(scene.p.workspace_id,instance,scene.task,after=first['after'])
    assert second['blocked']==5 and second['queued']==1
    with Session(engine()) as db:
        states=db.scalars(select(scanner.AutoEvaluationScanStateV356).where(
            scanner.AutoEvaluationScanStateV356.task_id==scene.task)).all()
        assert sum(row.status=='queued' for row in states)==1


def test_existing_original_is_verified_and_never_re_enqueued(monkeypatch):
    from types import SimpleNamespace as NS
    from generic_agents.models import Task
    p=NS(workspace_id=str(uuid4()))
    instance=str(uuid4());task=NS(id=str(uuid4()),data={'instance_id':instance});w=NS(id=str(uuid4()),event_id=str(uuid4()),policy_id=str(uuid4()),policy_version=1,observation_id=str(uuid4()))
    original=NS(job_id=str(uuid4()),data={'witness_id':w.id});job=NS(id=original.job_id)
    class DB:
        def scalar(self,query):return task if 'FROM tasks ' in str(query) else original
        def get(self,cls,key):return job
    calls=[]
    from generic_agents import auto_evaluation_policy_v356 as policy
    monkeypatch.setattr(policy,'current',lambda *a,**k:NS(id=w.policy_id,status='enabled',version=1))
    monkeypatch.setattr(scanner,'enqueue',lambda *a:pytest.fail('must never enqueue a replacement'))
    monkeypatch.setattr(scanner,'historical_origin',lambda _db,_p,_task,_origin,_w:(calls.append(_origin) or _origin))
    assert scanner._resume_or_enqueue(DB(),p,instance,task.id,w) is original
    assert calls==[original]


def test_locked_task_instance_change_cannot_enqueue(monkeypatch):
    from types import SimpleNamespace as NS
    p=NS(workspace_id=str(uuid4()));task=NS(id=str(uuid4()),data={'instance_id':str(uuid4())});w=NS()
    class DB:
        def scalar(self,_query):return task
    monkeypatch.setattr(scanner,'enqueue',lambda *a:pytest.fail('changed instance must not enqueue'))
    with pytest.raises(HTTPException):scanner._resume_or_enqueue(DB(),p,str(uuid4()),task.id,w)


def test_blocked_state_uses_persisted_database_backoff_then_retries(scene,monkeypatch):
    from datetime import timedelta
    from sqlalchemy import func
    from generic_agents.models import Task
    from generic_agents import auto_evaluation_policy_api_v356 as api
    monkeypatch.setattr(api,'automatic_runner_available',lambda:True)
    instance=str(uuid4())
    with Session(engine()) as db:
        task=db.get(Task,scene.task);task.data={**task.data,'instance_id':instance}
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,data={'actor_id':scene.p.user_id})
        db.add(w);db.commit();identity=w.id
    scanner._record(scene.p.workspace_id,scene.task,identity,'blocked')
    with Session(engine()) as db:
        state=db.scalar(select(scanner.AutoEvaluationScanStateV356).where(scanner.AutoEvaluationScanStateV356.witness_id==identity))
        assert state.next_attempt_at>db.scalar(select(func.clock_timestamp()))
    calls=[]
    monkeypatch.setattr(scanner,'enqueue',lambda *args:calls.append('issued'))
    skipped=scanner.tick(scene.p.workspace_id,instance,scene.task)
    assert skipped['queued']==skipped['blocked']==0 and not calls
    with Session(engine()) as db:
        state=db.scalar(select(scanner.AutoEvaluationScanStateV356).where(scanner.AutoEvaluationScanStateV356.witness_id==identity))
        state.next_attempt_at=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    resumed=scanner.tick(scene.p.workspace_id,instance,scene.task)
    assert resumed['queued']==1 and calls==['issued']
    again=scanner.tick(scene.p.workspace_id,instance,scene.task)
    assert again['queued']==again['blocked']==0 and calls==['issued']


def test_superseded_authorization_is_terminal_for_original_witness(scene,monkeypatch):
    from generic_agents.models import Task
    from generic_agents import auto_evaluation_policy_api_v356 as api
    from generic_agents import auto_evaluation_policy_v356 as policy
    monkeypatch.setattr(api,'automatic_runner_available',lambda:True)
    instance=str(uuid4())
    with Session(engine()) as db:
        task=db.get(Task,scene.task);task.data={**task.data,'instance_id':instance}
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,data={'actor_id':scene.p.user_id})
        db.add(w);db.flush();identity=w.id
        old=db.get(policy.AutoEvaluationPolicyV356,scene.policy);old.status='revoked';old.version=2;db.commit()
    monkeypatch.setattr(scanner,'enqueue',lambda *a:pytest.fail('superseded authorization cannot enqueue'))
    result=scanner.tick(scene.p.workspace_id,instance,scene.task)
    assert result['superseded']==1 and result['queued']==0
    with Session(engine()) as db:
        state=db.scalar(select(scanner.AutoEvaluationScanStateV356).where(scanner.AutoEvaluationScanStateV356.witness_id==identity))
        assert state.status=='superseded' and state.next_attempt_at is None
        assert scanner.task_status(db,scene.p,scene.task)['records'][0]['status']=='authorization_changed'
    again=scanner.tick(scene.p.workspace_id,instance,scene.task)
    assert again['superseded']==again['queued']==again['blocked']==0


def test_superseded_state_resists_late_failure_but_original_commit_can_win(scene):
    with Session(engine()) as db:
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,data={'fixture':'not-authorization'})
        db.add(w);db.commit();identity=w.id
    scanner._record(scene.p.workspace_id,scene.task,identity,'superseded')
    scanner._record(scene.p.workspace_id,scene.task,identity,'blocked')
    with Session(engine()) as db:
        row=db.scalar(select(scanner.AutoEvaluationScanStateV356).where(scanner.AutoEvaluationScanStateV356.witness_id==identity))
        assert row.status=='superseded' and row.next_attempt_at is None
    # This internal transition represents a separately proven original commit
    # whose status update arrived after authorization changed; no new request.
    scanner._record(scene.p.workspace_id,scene.task,identity,'queued')
    with Session(engine()) as db:
        row=db.scalar(select(scanner.AutoEvaluationScanStateV356).where(scanner.AutoEvaluationScanStateV356.witness_id==identity))
        assert row.status=='queued' and row.next_attempt_at is None


def test_unproven_current_policy_cannot_retire_original_witness(scene,monkeypatch):
    from generic_agents.models import Task
    from generic_agents import auto_evaluation_policy_v356 as policy
    instance=str(uuid4())
    with Session(engine()) as db:
        task=db.get(Task,scene.task);task.data={**task.data,'instance_id':instance};db.flush()
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,data={'actor_id':scene.p.user_id})
        db.add(w);db.flush()
        monkeypatch.setattr(policy,'_checked_current',lambda *a:(_ for _ in ()).throw(HTTPException(409,'invalid audit')))
        with pytest.raises(HTTPException):scanner._resume_or_enqueue(db,scene.p,instance,scene.task,w)
        db.rollback()


def test_saved_queue_and_changed_authorization_are_reported_independently(scene):
    from generic_agents import auto_evaluation_policy_v356 as policy
    with Session(engine()) as db:
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,data={'fixture':'not-authorization'})
        db.add(w);db.commit();identity=w.id
    scanner._record(scene.p.workspace_id,scene.task,identity,'queued')
    with Session(engine()) as db:
        row=db.get(policy.AutoEvaluationPolicyV356,scene.policy);row.status='revoked';row.version=2;db.commit()
    with Session(engine()) as db:
        item=scanner.task_status(db,scene.p,scene.task)['records'][0]
        assert item['status']=='queued_status_needs_verification' and item['authorization_changed'] is True


def test_committed_original_survives_restart_and_revocation_without_reenqueue(scene,monkeypatch):
    """Real PG commit/restart path; historical cryptographic proof is stubbed."""
    from generic_agents.models import Task,Job
    from generic_agents.goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
    from generic_agents import auto_evaluation_policy_v356 as policy
    from generic_agents import auto_evaluation_policy_api_v356 as api
    monkeypatch.setattr(api,'automatic_runner_available',lambda:True)
    instance=str(uuid4());request=str(uuid4())
    with Session(engine()) as db:
        task=db.get(Task,scene.task);task.data={**task.data,'instance_id':instance}
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,observation_id=scene.obs,
            policy_id=scene.policy,policy_version=1,data={'actor_id':scene.p.user_id});db.add(w);db.flush()
        job=Job(workspace_id=scene.p.workspace_id,kind='append_goal_evaluation_v297',status='queued',idempotency_key='recover-'+request,data={});db.add(job);db.flush()
        command=Command(workspace_id=scene.p.workspace_id,task_id=scene.task,actor_id=scene.p.user_id,
            goal_version=1,goal_hash='a'*64,request_id=request,body_hash='b'*64,capability_id=task.package_id,
            capability_hash='c'*64,input_hash='d'*64,job_id=job.id,invocation_key='recover-'+request,
            record_hash='e'*64,version=1,data={'synthetic':'proof boundary'});db.add(command);db.flush()
        origin=scanner.Trigger(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,command_id=command.id,job_id=job.id,
            data={'witness_id':w.id});db.add(origin);db.flush();original_id=origin.id;original_job_id=job.id
        row=db.get(policy.AutoEvaluationPolicyV356,scene.policy);row.status='revoked';row.version=2;db.commit()
    # New Session after the committed original, with no scan state: simulate
    # crash between original commit and a separate status write.
    checked=[]
    monkeypatch.setattr(scanner,'historical_origin',lambda _db,_p,_task,orig,_w:(checked.append(orig.id) or orig))
    monkeypatch.setattr(scanner,'enqueue',lambda *a:pytest.fail('must never submit a replacement'))
    result=scanner.tick(scene.p.workspace_id,instance,scene.task)
    assert result['queued']==1 and checked==[original_id]
    with Session(engine()) as db:
        item=scanner.task_status(db,scene.p,scene.task)['records'][0]
        assert item['status']=='queued_status_needs_verification' and item['authorization_changed'] is True
        assert db.get(Job,original_job_id).status=='queued'


def test_status_identifies_original_observation_only_inside_task_scope(scene):
    from generic_agents.models import Task, Observation, Entity, Capability
    with Session(engine()) as db:
        w=Witness(workspace_id=scene.p.workspace_id,task_id=scene.task,event_id=scene.event,
            observation_id=scene.obs,policy_id=scene.policy,policy_version=1,data={'fixture':'not-authorization'})
        db.add(w);db.commit();identity=w.id
    scanner._record(scene.p.workspace_id,scene.task,identity,'blocked')
    with Session(engine()) as db:
        view=scanner.task_status(db,scene.p,scene.task)['records'][0]
        obs=db.get(Observation,scene.obs);entity=db.get(Entity,obs.entity_id);source=db.get(Capability,obs.source_id)
        assert view['review_reference']==identity
        assert view['observation']['object_name']==entity.external_id
        assert view['observation']['source_name']==source.name
        assert view['observation']['reported_observed_at'] is None
        task=db.get(Task,scene.task);task.data={**task.data,'entity_ids':[]};db.commit()
    with Session(engine()) as db:
        view=scanner.task_status(db,scene.p,scene.task)['records'][0]
        assert view['observation'] is None and view['review_reference']==identity
        from generic_agents.security import Principal
        viewer=Principal(scene.p.user_id,scene.p.workspace_id,'viewer','test')
        assert scanner.task_status(db,viewer,scene.task)['records'][0]['review_reference'] is None
