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
from generic_agents.models import Job
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
    first=claim(job_id=identity,kinds=[KIND],workspace_id=workspace)
    assert first and first[0]==identity
    with Session(engine()) as db:
        row=db.get(Job,identity);assert isolated(row) and row.status=='running'
        row.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    # Even expired running leases are invisible to both original claim modes.
    assert old.claim(job_id=identity,kinds=old.SETUP_KINDS,workspace_id=workspace) is None
    assert old.claim(job_id=identity,kinds=None) is None
    recovered=claim(job_id=identity,kinds=[KIND],workspace_id=workspace)
    assert recovered and recovered[1]!=first[1]
    with Session(engine()) as db:
        row=db.get(Job,identity);assert isolated(row) and row.data['lease_recoveries']==1
        row.status='failed';row.lease_until=None;row.lease_token=None;db.commit()
    assert old.claim(job_id=identity,kinds=None) is None
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


def test_selected_setup_worker_executes_only_scoped_job(account,monkeypatch):
    from generic_agents import report_reading_drafts, worker
    from generic_agents.models import SetupReportDraft,User
    username, _, workspace=account
    with Session(engine(),expire_on_commit=False) as db:
        actor=db.scalar(select(User).where(User.username==username)); rid=str(uuid4());jid=str(uuid4())
        draft=SetupReportDraft(id=rid,workspace_id=workspace,actor_id=actor.id,
            request_id='fl_'+str(uuid4()),status='queued',
            data={'schema':'report-evidence-draft-state-v1','job_id':jid})
        job=Job(id=jid,workspace_id=workspace,kind=KIND,status='queued',available_at=NOT_AVAILABLE,
            idempotency_key='report-evidence:'+draft.request_id,data={'actor_id':actor.id,'report_draft_id':rid})
        db.add_all([draft,job]);db.commit()
    invoked=[]
    def stub_handler(db, principal, identity):
        invoked.append((principal.workspace_id,identity));return 'review'
    monkeypatch.setattr(report_reading_drafts,'run',stub_handler)
    assert worker.run_setup_job(jid)==0
    assert invoked==[(workspace,rid)]
    with Session(engine()) as db:
        job=db.get(Job,jid);assert job.status=='succeeded' and isolated(job)
