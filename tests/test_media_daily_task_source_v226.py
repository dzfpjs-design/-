"""Actual generic capture/Job/Frame/Daily/ACK path in disposable PostgreSQL.

Only public test_media transport and local-model execution are mocked. They
produce inert synthetic video bytes and real JPEG frames through the normal
capture and processing handlers. No successful Job, Frame, publication, data
Memory or ACK is inserted by this fixture. This is not a physical-camera,
model-quality, instance-authorization or Task-creation acceptance test.
"""
import copy
import hashlib
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import media_daily_delivery as delivery, models as m
from generic_agents import media_daily_task_adapter as adapter
from generic_agents import video_daily
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.security import Principal
from tests import test_media as media_tests
from tests.test_local_specialists import mocked_runtime, ready
from tests.test_media import capture, channel, finish, media_runtime


@pytest.fixture
def daily_camera_source(client, account, media_runtime, monkeypatch):
    bound = engine()
    assert bound.dialect.name == 'postgresql'
    assert bound.url.database.startswith('ga_test_'), 'Never run against application data'
    # Resolve only the disposable directory created by public conftest. macOS
    # can place that directory below /var -> /private/var; production SafeFS
    # keeps its NOFOLLOW checks and never resolves an arbitrary source root.
    storage_root = settings().storage_dir
    assert storage_root.name.startswith('ga-test-evidence-')
    assert storage_root.is_dir() and not storage_root.is_symlink()
    monkeypatch.setattr(settings(), 'storage_dir', storage_root.resolve(strict=True))
    original_mock_video = media_tests.local_video.execute_video

    def video_with_complete_contract(content, backend, configuration, checkpoints, guard, receive, lock_fd=None):
        def complete(value, directory):
            if value.get('kind') == 'complete':
                # The old public transport stub predates these actual runner
                # fields. Supply them before the normal handler validates and
                # persists the event; never edit a saved Run or its success.
                # Existing values win, so an invalid emitted value is not fixed.
                value = {'tracking_identity': 'session_and_segment_only',
                    'sample_hz': configuration['sample_hz'], **value}
            return receive(value, directory)
        return original_mock_video(content, backend, configuration, checkpoints, guard,
            complete, lock_fd=lock_fd)

    monkeypatch.setattr(media_tests.local_video, 'execute_video', video_with_complete_contract)
    installation = ready(client)
    camera = channel(client)
    queued = capture(client, camera, installation)
    assert queued.status_code == 201, queued.text[:200]
    captured, processed = finish(client, queued.json())
    assert captured['status'] == 'captured'
    assert processed['status'] == 'succeeded', processed.get('error')
    assert processed['delivery']['data_received'] == len(processed['frames']) > 0
    with Session(bound) as db:
        user = db.scalar(select(m.User).where(m.User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'v226-generic-source-test')
        session = db.get(m.StreamSession, captured['id'])
        run = db.get(m.VideoRun, processed['id'])
        assert session.data.get('supplement_plan_id') is None
        assert session.data.get('domain_binding') is None
        assert not list(db.scalars(select(m.Audit.id).where(
            m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == 'native_video.capture_frozen_v164',
            m.Audit.object_id == session.id)))
        capture_job_id, process_job_id = session.data['job_id'], run.data['job_id']
        assert db.get(m.Job, capture_job_id).status == 'succeeded'
        assert db.get(m.Job, process_job_id).status == 'succeeded'
        video_daily.reconcile(db, p.workspace_id)
        db.commit()
    consumed = delivery.consume_once(p.workspace_id)
    assert consumed['received'] == 1 and consumed['blocked'] == 0
    with Session(bound) as db:
        daily = db.scalar(select(m.VideoDaily).where(
            m.VideoDaily.workspace_id == p.workspace_id,
            m.VideoDaily.source_key == 'channel:' + camera['id']))
        events = list(db.scalars(select(m.Event).where(
            m.Event.workspace_id == p.workspace_id,
            m.Event.kind == delivery.KIND,
            m.Event.data['daily_id'].as_string() == daily.id,
            m.Event.data['daily_revision'].as_integer() == daily.version)))
        assert len(events) == 1
        event = events[0]
        proof = delivery.checked(db, p, event)
        delivery.validate_sources(db, p, proof['body'])
        assert proof['state']['status'] == 'received'
        records = proof['body']['summary']['records']
        assert len(records) == 1
        assert records[0]['id'] == captured['id'] and records[0]['run_id'] == processed['id']
        assert proof['state']['memory_id'] != proof['body']['producer_memory_id']
        # Every fault starts with the SAME fully accepted source. Otherwise an
        # unrelated early failure could make all adversarial assertions green.
        proof['event_id'] = event.id
        baseline, original, raw = adapter.checked_camera_frame(db, p, proof,
            copy.deepcopy(records[0]), camera['id'])
        assert baseline['frame']['evidence_id'] == original.id
        assert hashlib.sha256(raw).hexdigest() == original.sha256 == baseline['frame']['sha256']
        return SimpleNamespace(bound=bound, p=p, camera_id=camera['id'],
            day=daily.business_day, daily_id=daily.id, event_id=event.id,
            record=copy.deepcopy(records[0]), session_id=captured['id'],
            run_id=processed['id'], capture_job_id=capture_job_id,
            process_job_id=process_job_id)


def source_proof(db, source):
    event = db.get(m.Event, source.event_id)
    proof = delivery.checked(db, source.p, event)
    delivery.validate_sources(db, source.p, proof['body'])
    assert proof['state']['status'] == 'received'
    proof['event_id'] = event.id
    return proof


def checked(db, source, *, camera_id=None):
    return adapter.checked_camera_frame(db, source.p, source_proof(db, source),
        copy.deepcopy(source.record), source.camera_id if camera_id is None else camera_id)


def inventory(db, source):
    return {row.__tablename__: db.scalar(select(func.count()).select_from(row).where(
        row.workspace_id == source.p.workspace_id))
        for row in (m.Task, m.Job, m.Invocation, m.Event, m.Memory, m.Evidence, m.Audit, m.CloudCall)}


def test_generic_capture_uses_actual_frame_and_distinct_daily_receiver_without_native_watch(daily_camera_source):
    source = daily_camera_source
    with Session(source.bound) as db:
        before = inventory(db, source)
        pin, evidence, content = checked(db, source)
        assert pin['daily_id'] == source.daily_id and pin['event_id'] == source.event_id
        assert pin['day'] == source.day and pin['session_id'] == source.session_id
        assert pin['run_id'] == source.run_id and pin['job_id'] == source.process_job_id
        assert pin['capture_job_id'] == source.capture_job_id
        assert pin['frame']['sample_index'] == 0
        assert pin['frame']['evidence_id'] == evidence.id
        assert hashlib.sha256(content).hexdigest() == evidence.sha256 == pin['frame']['sha256']
        assert evidence.media_type in {'image/png', 'image/jpeg'}
        proof = source_proof(db, source)
        assert pin['ack_memory_id'] == proof['state']['memory_id']
        assert pin['ack_memory_hash'] == proof['state']['memory_hash']
        assert pin['ack_memory_id'] != proof['body']['producer_memory_id']
        assert db.get(m.Event, pin['frame']['event_id']).status == 'received'
        assert db.get(m.Memory, pin['frame']['memory_id']).data['type'] == 'video_observation'
        assert inventory(db, source) == before


def test_same_workspace_does_not_authorize_another_camera(daily_camera_source):
    source = daily_camera_source
    with Session(source.bound) as db:
        before = inventory(db, source)
        with pytest.raises(HTTPException) as error:
            checked(db, source, camera_id=str(uuid4()))
        assert error.value.status_code == 409
        assert inventory(db, source) == before


def test_daily_ack_cannot_replace_a_missing_frame_receiver_memory(daily_camera_source):
    source = daily_camera_source
    with Session(source.bound) as db:
        frame = db.scalar(select(m.VideoFrame).where(m.VideoFrame.run_id == source.run_id)
            .order_by(m.VideoFrame.sample_index))
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == source.p.workspace_id,
            m.Event.kind == 'video.data',
            m.Event.idempotency_key == 'video-data:' + source.run_id + ':' + str(frame.sample_index)))
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == source.p.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'video_observation')))
        assert len(memories) == 1
        db.delete(memories[0]); db.commit()
    with Session(source.bound) as db:
        assert source_proof(db, source)['state']['status'] == 'received'
        before = inventory(db, source)
        with pytest.raises(HTTPException) as error: checked(db, source)
        assert error.value.status_code == 409
        assert inventory(db, source) == before


@pytest.mark.parametrize('kind', ['frame', 'video'])
def test_actual_original_bytes_tamper_blocks_without_repair(daily_camera_source, kind):
    source = daily_camera_source
    with Session(source.bound) as db:
        if kind == 'frame':
            identity = db.scalar(select(m.VideoFrame.evidence_id).where(m.VideoFrame.run_id == source.run_id)
                .order_by(m.VideoFrame.sample_index))
        else:
            identity = db.get(m.VideoRun, source.run_id).evidence_id
        evidence = db.get(m.Evidence, identity)
        path = settings().storage_dir / evidence.storage_key
        original = path.read_bytes()
        assert hashlib.sha256(original).hexdigest() == evidence.sha256
    try:
        path.write_bytes(b'isolated-test-corruption-not-a-camera-observation')
        with Session(source.bound) as db:
            before = inventory(db, source)
            with pytest.raises(HTTPException) as error: checked(db, source)
            assert error.value.status_code == 409
            assert path.read_bytes() == b'isolated-test-corruption-not-a-camera-observation'
            assert inventory(db, source) == before
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize('job_field', ['capture_job_id', 'process_job_id'])
@pytest.mark.parametrize('status', ['running', 'failed'])
def test_saved_frames_never_replace_successful_actual_jobs(daily_camera_source, job_field, status):
    source = daily_camera_source
    with Session(source.bound) as db:
        db.get(m.Job, getattr(source, job_field)).status = status
        db.commit()
    with Session(source.bound) as db:
        before = inventory(db, source)
        with pytest.raises(HTTPException) as error: checked(db, source)
        assert error.value.status_code == 409
        assert inventory(db, source) == before


def test_missing_independent_video_completion_audit_cannot_be_recreated(daily_camera_source):
    source = daily_camera_source
    with Session(source.bound) as db:
        rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == source.p.workspace_id,
            m.Audit.action == 'video.completed', m.Audit.object_id == source.run_id)))
        assert len(rows) == 1
        db.delete(rows[0]); db.commit()
    with Session(source.bound) as db:
        before = inventory(db, source)
        with pytest.raises(HTTPException) as error: checked(db, source)
        assert error.value.status_code == 409
        assert inventory(db, source) == before


@pytest.mark.parametrize('field,value', [('channel_id', None), ('session_id', None), ('capture_hash', 'f' * 64)])
def test_generic_source_context_drift_is_not_new_native_authority(daily_camera_source, field, value):
    source = daily_camera_source
    with Session(source.bound) as db:
        run = db.get(m.VideoRun, source.run_id)
        context = copy.deepcopy(run.data['source_context'])
        context[field] = value
        run.data = {**run.data, 'source_context': context}
        db.commit()
    with Session(source.bound) as db:
        before = inventory(db, source)
        with pytest.raises(HTTPException) as error: checked(db, source)
        assert error.value.status_code == 409
        assert inventory(db, source) == before
