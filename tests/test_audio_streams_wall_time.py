"""Capture wall-time and media-count separation; no native/DB success claim."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from generic_agents import audio_streams as streams
from generic_agents.security import Principal


class StopBeforeExternalRead(RuntimeError): pass


def test_record_reserves_remaining_authorized_wall_time_not_exact_media_duration(monkeypatch, tmp_path):
    from generic_agents import audio_stream_budget, models
    stamp = datetime(2026, 11, 1, tzinfo=timezone.utc)
    body = {'schema': 'audio-stream-start-v255', 'request_id': 'wall-clock-original-' + uuid4().hex,
        'channel_version': 1, 'instance_id': str(uuid4()), 'release_id': str(uuid4()),
        'business_id': 'business_audio', 'entity_id': str(uuid4()), 'model_capability_id': str(uuid4()),
        'track_index': 0, 'configuration': {'channel': 'mono', 'threshold': .5},
        'segment_seconds': 2, 'max_segments': 2, 'max_runtime_seconds': 40, 'max_connections': 1,
        'confirm_audio_capture': True}
    p = Principal(str(uuid4()), str(uuid4()), 'admin', 'wall-time-interface-test')
    stream = SimpleNamespace(id=str(uuid4()), status='queued', data={'generation': 1,
        'capture_inventory': [], 'connections_used': 0, 'deadline': (stamp + timedelta(seconds=40)).isoformat()})
    job = SimpleNamespace(id=str(uuid4()), data={'stream_id': stream.id, 'generation': 1})
    channel, gateway = object(), object()
    # These are explicit controller-entry interface spies. The tested boundary
    # stops at reservation; it fabricates no capture, model result or ACK.
    monkeypatch.setattr(streams, '_row', lambda *args, **kwargs: stream)
    monkeypatch.setattr(streams, '_checked', lambda *args: (body, {}))
    monkeypatch.setattr(streams, '_record_job', lambda *args: None)
    monkeypatch.setattr(streams, '_guard_record', lambda *args: (stream, channel, gateway, {}))
    monkeypatch.setattr(models, 'now', lambda: stamp)
    seen = []
    def reserve(db, actor, controller, selected_channel, selected_gateway, seconds, connection, generation):
        seen.append(seconds)
        assert actor is p and controller is stream and selected_channel is channel and selected_gateway is gateway
        assert seconds == 40 and generation == 1
        raise StopBeforeExternalRead()
    monkeypatch.setattr(audio_stream_budget, 'reserve', reserve)
    with pytest.raises(StopBeforeExternalRead): streams.process(object(), p, job)
    assert seen == [40]
    request = streams.runner_request(body, url='rtsp://127.0.0.1:8554/audio-only', directory=str(tmp_path),
        remaining={'segments': 2, 'connections': 1, 'runtime_seconds': seen[0]})
    assert request['max_runtime_seconds'] == 40 and request['max_segments'] == 2 and request['segment_seconds'] == 2


def test_remaining_wall_clock_is_never_extended_to_original_or_extra_grace(tmp_path):
    body = {'track_index': 0, 'segment_seconds': 2, 'max_runtime_seconds': 40}
    request = streams.runner_request(body, url='rtsp://127.0.0.1:8554/audio-only', directory=str(tmp_path),
        remaining={'segments': 1, 'connections': 1, 'runtime_seconds': 7})
    assert request['max_runtime_seconds'] == 7 and request['max_segments'] == 1
