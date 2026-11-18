"""Old real worker claim logic against an isolated, synthetic PostgreSQL DB."""
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import types
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job
from generic_agents import report_evidence_delegated_budget_start as budget, report_evidence_delegated_dispatch_gate as gate, report_evidence_scoped_activation as activation
from tests.test_report_evidence_delegated_budget_start_postgres import scene_and_preview
from tests.test_cloud_report_plan_allowance_postgres import own_database, prepared_request
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene
from tests.test_material_ocr import local_runtime
from generic_agents.worker import claim
from generic_agents.report_evidence_worker_isolation import KIND, NOT_AVAILABLE, isolated


def _legacy_worker():
    root=Path(__file__).resolve().parents[1]
    source=subprocess.run(['git','show','44c07f2:src/generic_agents/worker.py'],cwd=root,
                          check=True,capture_output=True,text=True).stdout
    module=types.ModuleType('generic_agents._original_worker_44c07f2')
    module.__package__='generic_agents';sys.modules[module.__name__]=module
    exec(compile(source,'original_worker_44c07f2.py','exec'),module.__dict__)
    return module


def test_original_unfiltered_and_workspace_workers_never_claim_isolated_job(account):
    _, _, workspace = account
    with Session(engine(),expire_on_commit=False) as db:
        row=Job(workspace_id=workspace,kind=KIND,status='queued',available_at=NOT_AVAILABLE,
                idempotency_key='isolation-test:'+str(uuid4()),data={'actor_id':str(uuid4())})
        db.add(row);db.commit(); identity=row.id
    old=_legacy_worker()
    assert 'read_report_draft' in old.SETUP_KINDS and KIND not in old.SETUP_KINDS
    assert old.claim(job_id=identity,kinds=old.SETUP_KINDS,workspace_id=workspace) is None
    assert old.claim(job_id=identity,kinds=None,workspace_id=workspace) is None
    assert old.claim(job_id=identity,kinds=None) is None
    assert claim(job_id=identity,kinds=None,workspace_id=workspace) is None
    assert claim(job_id=identity,kinds=['read_report_draft'],workspace_id=workspace) is None
    with pytest.raises(ValueError):claim(kinds=[KIND],workspace_id=workspace)
    with pytest.raises(ValueError):claim(job_id=identity,kinds=[KIND,'read_report_draft'],workspace_id=workspace)
    for bad_id in ('','not-a-uuid','ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF',str(uuid4()).replace('-','')):
        with pytest.raises(ValueError):claim(job_id=bad_id,kinds=[KIND],workspace_id=workspace)
    assert claim(job_id=str(uuid4()),kinds=[KIND],workspace_id=workspace) is None
    # A fabricated scoped Job has no frozen grant or first-use proof.
    assert claim(job_id=identity,kinds=[KIND],workspace_id=workspace) is None
    with Session(engine()) as db:
        row=db.get(Job,identity);row.status='running';row.attempts=1
        row.lease_token=str(uuid4());row.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    assert old.claim(job_id=identity,kinds=old.SETUP_KINDS,workspace_id=workspace) is None
    assert old.claim(job_id=identity,kinds=None) is None
    assert claim(job_id=identity,kinds=[KIND],workspace_id=workspace) is None
    with Session(engine()) as db:assert isolated(db.get(Job,identity))


def test_original_worker_still_claims_due_legacy_report(account):
    _, _, workspace=account
    with Session(engine(),expire_on_commit=False) as db:
        row=Job(workspace_id=workspace,kind='read_report_draft',status='queued',
                idempotency_key='legacy-isolation-test:'+str(uuid4()),data={'actor_id':str(uuid4())})
        db.add(row);db.commit();identity=row.id
    old=_legacy_worker()
    result=old.claim(job_id=identity,kinds=old.SETUP_KINDS,workspace_id=workspace)
    assert result and result[0]==identity


def _real_scoped_plan(scene,monkeypatch):
    monkeypatch.setattr(gate,'ready',lambda:False)
    monkeypatch.setattr(budget,'_enabled',lambda:False)
    p,request,command,preview,_=scene_and_preview(scene,monkeypatch)
    with Session(engine(),expire_on_commit=False) as db:
        activation.issue_tx(db,p,request,preview['preview_id']);db.commit()
    with Session(engine(),expire_on_commit=False) as db:
        budget.start_tx(db,p,request,command,preview_id=preview['preview_id'],allowance_preview=preview['allowance']);db.commit()
    return p,preview['allowance']['draft_id'],preview['allowance']['job_id']


def test_selected_setup_worker_executes_only_scoped_job(reading_scene,monkeypatch):
    from generic_agents import report_reading_drafts, worker
    p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
    invoked=[]
    def stub_handler(db, principal, identity):
        invoked.append((principal.workspace_id,identity));return 'review'
    monkeypatch.setattr(report_reading_drafts,'run',stub_handler)
    assert worker.run_setup_job(jid)==0
    assert invoked==[(p.workspace_id,rid)]
    with Session(engine()) as db:
        job=db.get(Job,jid);assert job.status=='succeeded' and isolated(job)
        marker=db.scalar(select(Job).where(Job.idempotency_key=='fl-initial-claim-v1:'+jid))
        assert marker and marker.status=='internal'
        assert db.scalar(select(Audit.id).where(Audit.object_id==marker.id,
            Audit.action=='cloud.report_scope.initial_claim_v1'))


def test_expired_first_claim_and_counter_reset_never_reclaim(reading_scene,monkeypatch):
    p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
    first=claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id)
    assert first and first[0]==jid
    with Session(engine()) as db:
        job=db.get(Job,jid);job.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    assert claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id) is None
    with Session(engine()) as db:
        job=db.get(Job,jid);job.status='queued';job.attempts=0;job.lease_token=None;job.lease_until=None;db.commit()
    assert claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id) is None
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.idempotency_key=='fl-initial-claim-v1:'+jid))==1


def test_concurrent_first_claim_creates_one_durable_witness(reading_scene,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
    barrier=threading.Barrier(2)
    def do_claim():
        barrier.wait(timeout=5)
        return claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:do_claim(),range(2)))
    assert sum(value is not None for value in results)==1
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.idempotency_key=='fl-initial-claim-v1:'+jid))==1
        assert db.scalar(select(func.count()).select_from(CloudCall).where(
            CloudCall.data['scoped_extra']['job_id'].as_string()==jid))==0
