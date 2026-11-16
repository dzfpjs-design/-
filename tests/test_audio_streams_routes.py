"""Public router contract checks only; native RTSP/DB journeys are separate."""
import pytest
from fastapi import HTTPException
from generic_agents import audio_stream_api as api


ROOT = '/api/workspace/instances/{instance_id}/audio-streams'


def test_audio_router_uses_exact_full_public_paths_once():
    actual = {(next(iter(route.methods)), route.path) for route in api.router.routes if len(route.methods) == 1}
    expected = {
        ('GET', '/api/audio-streams/catalog'),
        ('GET', '/api/media/channels/{channel_id}/audio-source/options'),
        ('GET', '/api/media/channels/{channel_id}/audio-source/requests'),
        ('POST', '/api/media/channels/{channel_id}/audio-source'),
        ('GET', '/api/audio-streams/models/registrations'),
        ('POST', '/api/audio-streams/models/register'),
        ('GET', ROOT + '/options'), ('GET', ROOT + '/requests'), ('GET', ROOT),
        ('POST', ROOT + '/channels/{channel_id}'),
        ('GET', ROOT + '/{stream_id}/requests'), ('POST', ROOT + '/{stream_id}/commands'),
        ('GET', ROOT + '/{stream_id}/captures'),
        ('GET', ROOT + '/{stream_id}/captures/{observation_id}/content'),
        ('GET', ROOT + '/{stream_id}/captures/{observation_id}'),
        ('GET', ROOT + '/{stream_id}/daily'), ('GET', ROOT + '/{stream_id}')}
    assert actual == expected
    assert api.router.prefix == '' and all('/api/api/' not in path for _, path in actual)


def test_original_lookup_preserves_unicode_and_url_punctuation_but_rejects_nul():
    api._lookup('音轨原号/保留?完整申请', 'a' * 64)
    for identity, fingerprint in [('无效原号-\0-仍然很长', 'a' * 64), ('too-short', 'a' * 64),
        ('音轨原号/保留?完整申请', 'A' * 64), ('音轨原号/保留?完整申请', 'a' * 63)]:
        with pytest.raises(HTTPException) as error: api._lookup(identity, fingerprint)
        assert error.value.status_code == 422
