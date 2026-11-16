"""Lease recovery of a saved success checkpoint; no inference/model proof.

The explicit synthetic saved Run tests only real worker finalization. Every
model, install and evidence-read entry is forbidden, so recovery cannot quietly
replace an already saved result with a fresh video execution.
"""
import copy
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import local_video, models as m, worker
from generic_agents.db import engine


def test_plain_uploaded_video_saved_success_finalizes_original_expired_job_without_reinference(account, monkeypatch):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_'), \
        'Only Root-owned disposable ga_test_ PostgreSQL may run this test.'
    def denied(*args, **kwargs):
        raise AssertionError('Saved-success finalization must not install, read or infer again')
    monkeypatch.setattr(local_video, 'execute_video', denied)
    monkeypatch.setattr(local_video, 'ready_install', denied)
    monkeypatch.setattr(local_video, 'evidence_bytes', denied)
    with Session(bound, expire_on_commit=False) as db:
        actor = db.scalar(select(m.User.id).where(m.User.username == account[0]))
        installation = m.SpecialistInstallation(workspace_id=account[2], pack_id='synthetic-saved-checkpoint-' + uuid4().hex,
            status='ready', data={'synthetic_finalization_fixture': True})
        evidence = m.Evidence(workspace_id=account[2], sha256='a'*64, media_type='video/mp4', size_bytes=1,
            storage_key='not-read-synthetic-finalization-fixture', data={'modality': 'video'})
        db.add_all([installation, evidence]); db.flush()
        run = m.VideoRun(workspace_id=account[2], installation_id=installation.id, evidence_id=evidence.id,
            request_key=uuid4().hex, status='succeeded', data={'source_context': None,
                'generation': 1, 'saved_samples': 0, 'synthetic_saved_checkpoint': True})
        db.add(run); db.flush()
        job = m.Job(workspace_id=account[2], kind='process_video', idempotency_key='video:' + run.id,
            status='running', attempts=1, lease_token=str(uuid4()), lease_until=m.now() - timedelta(minutes=1),
            data={'run_id': run.id, 'generation': 1, 'actor_id': actor})
        db.add(job); db.flush()
        run.data = {**run.data, 'job_id': job.id}
        db.commit()
        run_id, job_id = run.id, job.id
        before = {column.key: copy.deepcopy(getattr(run, column.key)) for column in m.VideoRun.__table__.columns
            if not column.info.get('typed_payload_internal')}
    assert worker.process_once(job_id, kinds=worker.SETUP_KINDS, deliver=False, workspace_id=account[2])
    bound.dispose()
    with Session(bound) as db:
        job = db.get(m.Job, job_id); run = db.get(m.VideoRun, run_id)
        assert job.status == 'succeeded' and job.attempts == 2 and job.data.get('finished_at')
        assert job.lease_until is None and job.data['lease_recoveries'] == 1
        assert {name: copy.deepcopy(getattr(run, name)) for name in before} == before
        assert db.scalar(select(m.Job.id).where(m.Job.workspace_id == account[2],
            m.Job.idempotency_key == 'native-sync:' + job_id)) is None
        assert not list(db.scalars(select(m.Invocation.id).where(m.Invocation.workspace_id == account[2])))
        assert not list(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id == account[2])))
