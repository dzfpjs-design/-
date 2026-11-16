"""Current CLI/SQL scope only; no workers, network or database are started."""
import sys
from threading import Event
import pytest
from sqlalchemy import select, func, or_
from sqlalchemy.dialects import postgresql
from generic_agents import worker, cli
from generic_agents.models import Job

WORKSPACE = 'b2c83f19-27bd-473c-ae91-4792e4b9fd31'
JOB = 'e2d911e1-241c-47a7-b28a-31ac8e1f7648'
KIND = 'compare_pixel_change_v249'


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('scope tests cannot launch runtime or open database')
    monkeypatch.setattr(worker, 'engine', denied)
    monkeypatch.setattr(worker, 'Session', denied)
    monkeypatch.setattr(worker, 'run', denied)
    monkeypatch.setattr(worker, 'deliver_events', denied)
    monkeypatch.setattr(cli, 'migrate', denied)


@pytest.mark.parametrize('workspace,job_id', [(None, None), (WORKSPACE, None), (None, JOB), (WORKSPACE, JOB)])
def test_complete_claim_predicate_never_globally_adopts_pixel_jobs(monkeypatch, workspace, job_id):
    queries = []
    class Empty:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def scalar(self, query): queries.append(query); return None
    monkeypatch.setattr(worker, 'engine', lambda: None)
    monkeypatch.setattr(worker, 'Session', lambda *args, **kwargs: Empty())
    assert KIND in worker.SETUP_KINDS
    assert worker.claim(job_id=job_id, kinds=(KIND,), workspace_id=workspace) is None
    query = (select(Job).where(Job.available_at <= func.clock_timestamp(), Job.kind != 'login_limit',
        or_(Job.status == 'queued', (Job.status == 'running') & or_(Job.lease_until.is_(None), Job.lease_until < func.clock_timestamp())))
        .order_by(Job.available_at).with_for_update(skip_locked=True).limit(1))
    if job_id: query = query.where(Job.id == job_id)
    query = query.where(Job.kind.in_((KIND,)))
    if workspace is None and job_id is None:
        query = query.where(Job.kind.not_in(['create_signal_approval_draft_v247', KIND]))
    if workspace is not None: query = query.where(Job.workspace_id == workspace)
    actual = queries[0].compile(dialect=postgresql.dialect()); expected = query.compile(dialect=postgresql.dialect())
    assert str(actual) == str(expected) and actual.params == expected.params


@pytest.mark.parametrize('mode,identity', [('job', JOB), ('workspace', WORKSPACE)])
def test_real_cli_routes_only_explicit_scope(monkeypatch, mode, identity):
    calls = []
    monkeypatch.setattr(worker, 'run_setup_job', lambda value: calls.append(('job', value)) or 0)
    monkeypatch.setattr(worker, 'run_setup', lambda value: calls.append(('workspace', value)) or 0)
    monkeypatch.setattr(sys, 'argv', ['ga', 'setup-worker', '--' + mode + '-id', identity])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0 and calls == [(mode, identity)]


def test_stop_prevents_any_claim_before_database():
    stop = Event(); stop.set()
    assert worker.claim(kinds=(KIND,), workspace_id=WORKSPACE, _stop_event=stop) is None
