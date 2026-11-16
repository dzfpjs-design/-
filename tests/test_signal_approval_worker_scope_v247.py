"""Current-source CLI and full SQL scope contracts, with all real IO denied.

No private candidate path/hash or historical M61 fixture is changed. Interface
spies and empty SQL readers prove scope only, not durable worker acceptance.
Root runs this file with --noconftest; real scoped HTTP/PG proofs are separate.
"""
import socket
import subprocess
import sys
from threading import Event as StopEvent
from types import SimpleNamespace as NS

import httpx
import pytest
from sqlalchemy import select,func,or_,and_
from sqlalchemy.dialects import postgresql

from generic_agents import cli,db as database,worker,signal_approval_routing_v247 as routing
from generic_agents.models import Job,Event
from generic_agents.signal_approval_models_v247 import SignalApprovalOccurrenceV247 as Occurrence

WORKSPACE='b2c83f19-27bd-473c-ae91-4792e4b9fd31'
JOB='e2d911e1-241c-47a7-b28a-31ac8e1f7648'
SENTINEL=object()


@pytest.fixture(autouse=True)
def no_real_runtime(monkeypatch):
    def denied(*args,**kwargs):pytest.fail('Scope tests must not connect, launch workers or execute real IO')
    for module in (cli,worker,database,routing):
        if hasattr(module,'engine'):monkeypatch.setattr(module,'engine',denied)
        if hasattr(module,'Session'):monkeypatch.setattr(module,'Session',denied)
    monkeypatch.setattr(database,'create_engine',denied)
    monkeypatch.setattr(cli,'migrate',denied)
    monkeypatch.setattr(worker,'deliver_events',denied)
    monkeypatch.setattr(worker,'run',denied)
    monkeypatch.setattr(httpx,'Client',denied)
    monkeypatch.setattr(socket,'create_connection',denied)
    monkeypatch.setattr(socket.socket,'connect',denied)
    monkeypatch.setattr(socket.socket,'connect_ex',denied)
    monkeypatch.setattr(subprocess,'Popen',denied)
    monkeypatch.setattr(subprocess,'run',denied)


def same_sql(actual,expected):
    left=actual.compile(dialect=postgresql.dialect())
    right=expected.compile(dialect=postgresql.dialect())
    assert str(left)==str(right)
    assert left.params==right.params


@pytest.mark.parametrize('mode,identity,code',[('workspace',WORKSPACE,1),('job',JOB,3)])
def test_cli_selects_only_explicit_workspace_or_original_job_and_preserves_exit_code(monkeypatch,mode,identity,code):
    calls=[]
    def workspace(value):
        assert mode=='workspace';calls.append(('workspace',value));return code
    def job(value):
        assert mode=='job';calls.append(('job',value));return code
    monkeypatch.setattr(worker,'run_setup',workspace)
    monkeypatch.setattr(worker,'run_setup_job',job)
    monkeypatch.setattr(sys,'argv',['ga','setup-worker','--'+mode+'-id',identity])
    with pytest.raises(SystemExit) as result:cli.main()
    assert result.value.code==code and calls==[(mode,identity)]


@pytest.mark.parametrize('arguments',[
    ['setup-worker'],['setup-worker','--workspace-id',WORKSPACE,'--job-id',JOB],
    ['setup-worker','--workspace-id','invalid'],['worker','--workspace-id',WORKSPACE]])
def test_cli_rejects_unscoped_or_ambiguous_setup_before_any_worker(monkeypatch,arguments):
    monkeypatch.setattr(worker,'run_setup',lambda *_args:pytest.fail('Rejected scope reached scheduler'))
    monkeypatch.setattr(worker,'run_setup_job',lambda *_args:pytest.fail('Rejected scope reached job'))
    monkeypatch.setattr(sys,'argv',['ga',*arguments])
    with pytest.raises(SystemExit) as result:cli.main()
    assert result.value.code==2


@pytest.mark.parametrize('saved_status,expected',[('succeeded',0),('failed',1)])
def test_actual_bounded_job_entry_keeps_identity_kind_and_no_delivery(monkeypatch,saved_status,expected):
    calls=[]
    class ReadSession:
        def __enter__(self):return self
        def __exit__(self,*_args):return False
        def get(self,model,identity):
            assert model is Job and identity==JOB
            return NS(status=saved_status)
    monkeypatch.setattr(worker,'process_once',lambda **kwargs:calls.append(kwargs) or True)
    monkeypatch.setattr(worker,'engine',lambda:SENTINEL)
    def session(bind):
        assert bind is SENTINEL;return ReadSession()
    monkeypatch.setattr(worker,'Session',session)
    assert worker.run_setup_job(JOB)==expected
    assert calls==[{'job_id':JOB,'kinds':worker.SETUP_KINDS,'deliver':False}]


@pytest.mark.parametrize('job_id,kinds,workspace',[
    (None,None,None),(None,[routing.KIND],None),(None,[routing.KIND],WORKSPACE),
    (JOB,[routing.KIND],None),(JOB,[routing.KIND],WORKSPACE)])
def test_actual_claim_has_complete_scope_lease_and_global_new_kind_exclusion(monkeypatch,job_id,kinds,workspace):
    queries=[]
    class EmptySession:
        def __enter__(self):return self
        def __exit__(self,*_args):return False
        def scalar(self,query):queries.append(query);return None
    monkeypatch.setattr(worker,'engine',lambda:SENTINEL)
    def session(bind,**kwargs):
        assert bind is SENTINEL and kwargs=={'expire_on_commit':False}
        return EmptySession()
    monkeypatch.setattr(worker,'Session',session)
    assert worker.claim(job_id=job_id,kinds=kinds,workspace_id=workspace) is None
    assert len(queries)==1
    compiled=queries[0].compile(dialect=postgresql.dialect());sql=str(compiled);values=compiled.params.values()
    # Preserve the actual scope/lease assertions without assuming that no
    # newer dependency or safety predicate can be added to the claim query.
    assert 'jobs.available_at <=' in sql and 'clock_timestamp()' in sql
    assert 'jobs.status =' in sql and 'jobs.lease_until IS NULL' in sql
    assert 'FOR UPDATE SKIP LOCKED' in sql and 'ORDER BY jobs.available_at' in sql
    assert {'queued','running','login_limit'} <= {v for v in values if isinstance(v,str)}
    if job_id is not None:assert 'jobs.id =' in sql and job_id in values
    if workspace is not None:assert 'jobs.workspace_id =' in sql and workspace in values
    if kinds is not None:assert any(isinstance(v,list) and v==kinds for v in values)
    if workspace is None and job_id is None:
        assert any(isinstance(v,list) and {routing.KIND,'compare_pixel_change_v249'} <= set(v) for v in values)


def test_poll_uses_complete_workspace_received_or_unqueued_frozen_query_and_bounded_page(monkeypatch):
    queries=[];commits=[]
    class EmptySession:
        def __enter__(self):return self
        def __exit__(self,*_args):return False
        def scalars(self,query):queries.append(query);return []
        def commit(self):commits.append(True)
    monkeypatch.setattr(database,'engine',lambda:SENTINEL)
    def session(bind):
        assert bind is SENTINEL;return EmptySession()
    monkeypatch.setattr(routing,'Session',session)
    assert routing.poll_once(WORKSPACE)==0
    initial_job=select(Job.id).where(Job.workspace_id==WORKSPACE,Job.kind==routing.KIND,
        Job.idempotency_key=='signal-approval:'+Occurrence.id+':initial').exists()
    received=select(Event.id).where(Event.workspace_id==WORKSPACE,Event.kind=='observation.data',
        Event.data['observation_id'].as_string()==Occurrence.observation_id,Event.status=='received').exists()
    expected=select(Occurrence).where(Occurrence.workspace_id==WORKSPACE,Occurrence.status.in_(['frozen','waiting_data']),
        or_(and_(Occurrence.status=='frozen',~initial_job),received)).order_by(Occurrence.created_at,Occurrence.id).limit(20)
    assert len(queries)==1 and commits==[True]
    same_sql(queries[0],expected)


def test_explicit_stop_prevents_claim_and_poll_before_database():
    stop=StopEvent();stop.set()
    assert worker.claim(kinds=[routing.KIND],workspace_id=WORKSPACE,_stop_event=stop) is None
    assert routing.poll_once(WORKSPACE,_stop_event=stop)==0


@pytest.mark.parametrize('identity',[None,'invalid',WORKSPACE.upper(),7])
def test_poll_never_defaults_missing_or_noncanonical_workspace_to_global(identity):
    with pytest.raises(ValueError,match='UUID'):routing.poll_once(identity)


def test_stop_between_poll_page_and_iteration_does_not_check_or_queue_any_input(monkeypatch):
    stop=StopEvent()
    class StopOnRead:
        def __enter__(self):return self
        def __exit__(self,*_args):return False
        def scalars(self,query):stop.set();return [NS(actor_id=JOB)]
        def commit(self):pass
    monkeypatch.setattr(database,'engine',lambda:SENTINEL)
    monkeypatch.setattr(routing,'Session',lambda bind:StopOnRead())
    monkeypatch.setattr(routing,'checked_occurrence',lambda *_args:pytest.fail('Stop reached saved input'))
    monkeypatch.setattr(routing,'queue',lambda *_args:pytest.fail('Stop queued another local job'))
    assert routing.poll_once(WORKSPACE,_stop_event=stop)==0


def test_new_kind_has_one_scoped_setup_root_and_no_added_daily_or_global_entry():
    assert worker.SETUP_KINDS.count(routing.KIND)==1
    assert worker.SETUP_POLL_ROOTS.count(('signal_approval_routing_v247','poll_once'))==1
    assert not any(name=='signal_approval_routing_v247' for name,_ in worker.SETUP_DAILY_ROOTS)
