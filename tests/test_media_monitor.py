"""Scheduling evidence uses real PG; codec/model mocks are not runtime/model proof."""
from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import media_monitor
from generic_agents.db import engine
from generic_agents.models import MediaMonitor, StreamSession, VideoRun, Membership, CloudCall, Job
from generic_agents.worker import process_once
from tests.test_media import media_runtime, channel, capture, finish, session
from tests.test_local_specialists import mocked_runtime, ready
from tests.test_local_video import CFG


def prepared(client):
    install = ready(client); ch = channel(client)
    finish(client, capture(client, ch, install).json())
    return client.get('/api/media/channels').json()[0], install


def start(client, ch, install, **kwargs):
    return client.post('/api/media/channels/' + ch['id'] + '/monitors', json={
        'channel_version': ch['version'], 'installation_id': install['id'], 'seconds': 4,
        'interval_seconds': 15, 'max_segments': 2, 'runtime_minutes': 5,
        'allow_automatic_reads': True, 'configuration': CFG, **kwargs})


def current(client, identity):
    return next(x for x in client.get('/api/media/monitors').json() if x['id'] == identity)


def due(identity):
    with Session(engine()) as db:
        row = db.get(MediaMonitor, identity)
        row.data = {**row.data, 'next_due_at': (media_monitor.clock(db) - timedelta(seconds=1)).isoformat()}; db.commit()


def next_session(client, monitor):
    media_monitor.schedule_once(); m = current(client, monitor['id'])
    return session(client, m['current_session_id'])


def test_two_saved_segments_budget_and_no_duplicate_schedule(client, media_runtime):
    ch, install = prepared(client); r = start(client, ch, install); assert r.status_code == 201, r.text
    m = r.json(); first = next_session(client, m)
    for _ in range(3): media_monitor.schedule_once()
    assert current(client, m['id'])['attempts'] == 1
    finish(client, first); due(m['id']); second = next_session(client, m)
    assert second['id'] != first['id'] and second['monitor_sequence'] == 2
    finish(client, second); media_monitor.schedule_once(); final = current(client, m['id'])
    assert final['status'] == 'exhausted' and final['attempts'] == 2 and final['reserved_seconds'] == 8
    assert final['successful_segments'] == 2 and len(final['outcomes']) == 2, final['outcomes']
    assert final['outcomes'][1]['unobserved_receive_interval']['source_utc'] == 'unknown'
    assert final['continuous_recording'] is False
    with Session(engine()) as db:
        assert not db.scalars(select(CloudCall)).all()
        assert len(db.scalars(select(StreamSession).where(StreamSession.data['monitor_id'].as_string() == m['id'])).all()) == 2


def test_pause_stale_progress_resume_retains_budget_and_stop_is_final(client, media_runtime):
    ch, install = prepared(client); m = start(client, ch, install).json(); s = next_session(client, m)
    r = client.post('/api/media/monitors/' + m['id'] + '/pause', json={'version': 1}); assert r.status_code == 200, r.text
    assert session(client, s['id'])['status'] == 'cancelled'
    process_once(s['job_id'], deliver=False); media_monitor.schedule_once()
    paused = current(client, m['id']); assert paused['attempts'] == 1 and paused['outcomes'][0]['capture_status'] == 'cancelled'
    assert client.post('/api/media/monitors/' + m['id'] + '/resume', json={'version': 1}).status_code == 409
    r = client.post('/api/media/monitors/' + m['id'] + '/resume', json={'version': paused['version']}); assert r.status_code == 200
    assert r.json()['attempts'] == 1 and r.json()['deadline'] == m['deadline']
    s2 = next_session(client, m); assert s2['id'] != s['id']
    assert client.post('/api/media/monitors/' + m['id'] + '/stop', json={'version': 1}).status_code == 200
    assert client.post('/api/media/monitors/' + m['id'] + '/stop', json={'version': 1}).status_code == 200
    assert client.post('/api/media/monitors/' + m['id'] + '/resume', json={'version': current(client, m['id'])['version']}).status_code == 409


def test_failure_backoff_and_three_failure_stop_no_fake_results(client, media_runtime, monkeypatch):
    from generic_agents import media
    from fastapi import HTTPException
    ch, install = prepared(client); m = start(client, ch, install, max_segments=8).json()
    monkeypatch.setattr(media, 'source_state', lambda *args: (_ for _ in ()).throw(HTTPException(409, '通道离线')))
    for attempt in range(3):
        due(m['id']); s = next_session(client, m); process_once(s['job_id'], deliver=False)
        assert session(client, s['id'])['status'] == 'failed' and session(client, s['id'])['evidence_id'] is None
        media_monitor.schedule_once()
        if attempt < 2:
            assert current(client, m['id'])['current_session_id'] is None
    result = current(client, m['id']); assert result['status'] == 'blocked' and result['attempts'] == 3
    assert result['successful_segments'] == 0 and result['consecutive_failures'] == 3


@pytest.mark.parametrize('change', ['channel', 'model', 'role', 'deadline', 'pipeline'])
def test_dependency_or_deadline_change_stops_queued_read(client, media_runtime, monkeypatch, change):
    from generic_agents.models import MediaChannel, SpecialistInstallation
    from generic_agents import media
    ch, install = prepared(client); m = start(client, ch, install).json(); s = next_session(client, m)
    with Session(engine()) as db:
        if change == 'channel':
            row = db.get(MediaChannel, ch['id']); row.data = {**row.data, 'read_authorized': False}
        elif change == 'model':
            row = db.get(SpecialistInstallation, install['id']); row.data = {**row.data, 'generation': row.data['generation'] + 1}
        elif change == 'role':
            db.scalar(select(Membership).where(Membership.workspace_id == m.get('workspace_id', db.get(MediaMonitor,m['id']).workspace_id))).role = 'viewer'
        elif change == 'deadline':
            row = db.get(MediaMonitor, m['id']); row.data = {**row.data, 'deadline': (media_monitor.clock(db) - timedelta(seconds=1)).isoformat()}
        db.commit()
    if change == 'pipeline': monkeypatch.setattr(media, 'CAPTURE_HASH', 'changed')
    media_monitor.schedule_once(); row = current(client, m['id'])
    assert row['status'] == ('exhausted' if change == 'deadline' else 'blocked')
    assert session(client, s['id'])['status'] == 'cancelled'
    media_monitor.schedule_once(); finalized = current(client, m['id']); assert len(finalized['outcomes']) == 1
    media_monitor.schedule_once(); assert current(client, m['id'])['version'] == finalized['version']


def test_activation_requires_proof_explicit_grant_bounds_and_no_duplicate_source(client, media_runtime):
    install = ready(client); ch = channel(client)
    assert start(client, ch, install).status_code == 409
    finish(client, capture(client, ch, install).json()); ch = client.get('/api/media/channels').json()[0]
    assert start(client, ch, install, allow_automatic_reads=False).status_code == 422
    assert start(client, ch, install, max_segments=101).status_code == 422
    assert start(client, ch, install, configuration={'recorded_at': '2026-10-08T00:00:00Z'}).status_code == 422
    assert start(client, ch, install).status_code == 201
    assert start(client, ch, install).status_code == 409
    assert capture(client, ch, install).status_code == 409
    duplicate = channel(client); finish(client, capture(client, duplicate, install).json())
    duplicate = next(x for x in client.get('/api/media/channels').json() if x['id'] == duplicate['id'])
    assert start(client, duplicate, install).status_code == 409


def test_processing_must_finish_before_another_read_even_after_schedule_due(client, media_runtime):
    ch, install = prepared(client); m = start(client, ch, install).json(); s = next_session(client, m)
    process_once(s['job_id'], deliver=False); due(m['id']); media_monitor.schedule_once()
    assert current(client, m['id'])['attempts'] == 1
    saved = session(client, s['id']); run = client.get('/api/videos/' + saved['video_run_id']).json()
    process_once(run['job_id'], deliver=False); media_monitor.schedule_once()
    assert current(client, m['id'])['attempts'] == 2


def test_interrupted_live_segment_does_not_reread_without_another_budget_reservation(client, media_runtime):
    ch, install = prepared(client); m = start(client, ch, install).json(); s = next_session(client, m)
    with Session(engine()) as db:
        row = db.get(StreamSession, s['id']); row.status = 'capturing'; db.commit()
    process_once(s['job_id'], deliver=False)
    failed = session(client, s['id']); assert failed['status'] == 'failed' and failed['evidence_id'] is None
    assert failed['attempt_history'][0]['state'] == 'interrupted_unsaved_window'
    due(m['id']); fresh = next_session(client, m)
    assert fresh['id'] != s['id'] and current(client, m['id'])['attempts'] == 2
