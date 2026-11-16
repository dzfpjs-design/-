"""Pure start/command/original-body boundaries, no capture/DB/SDK proof."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import audio_streams as service


def start():
    return {'schema': 'audio-stream-start-v255', 'request_id': '音轨/原申请?保留-' + uuid4().hex,
        'channel_version': 3, 'instance_id': str(uuid4()), 'release_id': str(uuid4()),
        'business_id': 'business_audio', 'entity_id': str(uuid4()), 'model_capability_id': str(uuid4()),
        'track_index': 0, 'configuration': {'channel': 'mono', 'threshold': .5},
        'segment_seconds': 10, 'max_segments': 5, 'max_runtime_seconds': 300, 'max_connections': 2,
        'confirm_audio_capture': True}


def test_original_unicode_request_and_numeric_token_bytes_are_preserved_separately():
    body = start(); original = ('  ' + json.dumps(body, ensure_ascii=False, indent=2) + '\n').replace('0.5', '5e-1')
    raw, value, raw_hash, body_hash = service.parse(original.encode(), 'start')
    assert raw == original and value == body and raw_hash == hashlib.sha256(original.encode()).hexdigest()
    assert raw_hash != body_hash and '5e-1' in raw


@pytest.mark.parametrize('field,value', [('request_id', 'short'), ('request_id', 'null-byte-\0-request'),
    ('channel_version', True), ('channel_version', 0), ('instance_id', 'not-a-resource'),
    ('track_index', True), ('track_index', -1), ('track_index', 32), ('segment_seconds', 0),
    ('segment_seconds', 61), ('segment_seconds', 1.5), ('max_segments', 101),
    ('max_runtime_seconds', 3601), ('max_connections', 0), ('max_connections', 5),
    ('confirm_audio_capture', False), ('confirm_audio_capture', 1)])
def test_new_audio_capture_requires_explicit_bounded_typed_body(field, value):
    body = start(); body[field] = value
    with pytest.raises(HTTPException) as error: service.parse(json.dumps(body).encode(), 'start')
    assert error.value.status_code == 422


@pytest.mark.parametrize('change', [{'read_authorized': True}, {'url': 'rtsp://127.0.0.1:8554/arbitrary'},
    {'configuration': {'channel': 'auto', 'threshold': .5}}, {'configuration': {'channel': 'mono', 'threshold': True}},
    {'configuration': {'channel': 'mono', 'threshold': 1}}, {'configuration': {'channel': 'mono', 'threshold': 0}}])
def test_client_cannot_replace_audio_consent_with_video_consent_or_free_url(change):
    body = {**start(), **change}
    with pytest.raises(HTTPException): service.parse(json.dumps(body).encode(), 'start')


@pytest.mark.parametrize('raw', [b'{}', b'[]', b'{"schema":"a","schema":"b"}', b'{"x":NaN}', b'\xff', b' ' * 32769])
def test_malformed_and_ambiguous_original_json_has_no_valid_new_request(raw):
    with pytest.raises(HTTPException) as error: service.parse(raw, 'start')
    assert error.value.status_code == 422


def test_pause_stop_resume_share_closed_original_command_contract():
    for action in ['pause', 'stop', 'resume']:
        value = {'schema': 'audio-stream-command-v255', 'request_id': 'command-original-' + uuid4().hex,
            'version': 7, 'action': action}
        assert service.parse(json.dumps(value).encode(), 'command')[1] == value
    with pytest.raises(HTTPException): service.parse(json.dumps({**value, 'reset_budget': True}).encode(), 'command')


def test_resume_computes_original_remaining_limits_without_reset_or_fractional_extra_second():
    now = datetime(2026, 11, 1, tzinfo=timezone.utc); body = start()
    limits = service.remaining_limits(body, segments_used=3, connections_used=1,
        deadline=now + timedelta(seconds=7.9), current=now)
    assert limits == {'segments': 2, 'connections': 1, 'runtime_seconds': 7}
    finished = service.remaining_limits(body, segments_used=5, connections_used=2, deadline=now, current=now)
    assert finished == {'segments': 0, 'connections': 0, 'runtime_seconds': 0}
    with pytest.raises(HTTPException): service.remaining_limits(body, segments_used=True, connections_used=0, deadline=now, current=now)


def test_only_server_selected_loopback_gateway_can_build_runner_request():
    body = start(); remaining = {'segments': 2, 'connections': 1, 'runtime_seconds': 20}
    request = service.runner_request(body, url='rtsp://127.0.0.1:8554/camera/audio', directory='/private/output', remaining=remaining)
    assert request == {'schema': 'audio-stream-capture-v1', 'url': 'rtsp://127.0.0.1:8554/camera/audio',
        'directory': '/private/output', 'audio_stream_index': 0, 'segment_seconds': 10,
        'max_segments': 2, 'max_runtime_seconds': 20, 'max_segment_bytes': 20 * 1024 * 1024}
    for url in ['rtsp://example.org:8554/camera', 'http://127.0.0.1:8554/camera', 'rtsp://169.254.169.254:8554/camera']:
        with pytest.raises(HTTPException): service.runner_request(body, url=url, directory='/private/output', remaining=remaining)
    with pytest.raises(HTTPException): service.runner_request(body, url='rtsp://127.0.0.1:8554/camera',
        directory='/private/output', remaining={**remaining, 'connections': 0})
