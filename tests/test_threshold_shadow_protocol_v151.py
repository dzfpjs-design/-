"""Pure closed-wire shadow commands; no DB, authorization or clock claims.

The aware expiry remains original text. Future/seven-day bounds belong to the
API's actual database clock and are deliberately not guessed by these tests.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import UUID

from fastapi import HTTPException
import pytest

from generic_agents.threshold_shadow_protocol_v151 import (
    ROOT, CreateShadow, ControlShadow, canonical_uuid, identify, parse_command,
    read_command,
)


REQUEST = '15100000-0000-4000-8000-00000000000a'
EVALUATION = '15100000-0000-4000-8000-00000000000b'
GRANT = '15100000-0000-4000-8000-00000000000c'


def create_body():
    return {'schema': 'threshold-shadow-create-v151', 'request_id': REQUEST,
        'evaluation_id': EVALUATION, 'expected_evaluation_version': 7,
        'title': ' 原对象的影子观察 ', 'reason': ' 保留原批准理由与中文原文 ',
        'max_inputs': 2, 'expires_at': '2026-10-22T09:30:00+08:00',
        'confirm_shadow_only': True}


def control_body():
    return {'schema': 'threshold-shadow-control-v151', 'request_id': REQUEST,
        'expected_version': 4, 'reason': '明确停止后续预约，原结果保留',
        'confirm': True}


def rejected(model, body):
    original = deepcopy(body)
    with pytest.raises(HTTPException) as error:
        parse_command(model, body)
    assert error.value.status_code == 422
    assert type(error.value.detail) is str and error.value.detail
    assert body == original


@pytest.mark.parametrize('expiry', [
    '2026-10-22T09:30:00+08:00', '2026-10-22T01:30:00Z',
    '2026-10-21T18:30:00-07:00',
])
def test_create_preserves_closed_original_text_and_aware_offset(expiry):
    body = create_body(); body['expires_at'] = expiry
    original = deepcopy(body)
    result = parse_command(CreateShadow, body)
    assert result.model_dump(by_alias=True) == original
    assert result.expires_at == expiry and type(result.expires_at) is str
    assert type(result.max_inputs) is int and type(result.expected_evaluation_version) is int
    assert result.confirm_shadow_only is True and body == original
    assert identify('POST', ROOT) == (None, 'create', CreateShadow)


@pytest.mark.parametrize('action', ['pause', 'resume', 'end'])
def test_control_is_closed_and_bound_to_exact_original_route(action):
    body = control_body(); original = deepcopy(body)
    assert parse_command(ControlShadow, body).model_dump(by_alias=True) == original
    assert body == original
    assert identify('POST', ROOT + '/' + GRANT + '/' + action) == (GRANT, action, ControlShadow)


@pytest.mark.parametrize('model,field,value', [
    (CreateShadow, 'expected_evaluation_version', True),
    (CreateShadow, 'expected_evaluation_version', 1.0),
    (CreateShadow, 'expected_evaluation_version', '1'),
    (CreateShadow, 'expected_evaluation_version', 0),
    (CreateShadow, 'max_inputs', True), (CreateShadow, 'max_inputs', 1.0),
    (CreateShadow, 'max_inputs', '1'), (CreateShadow, 'max_inputs', 0),
    (CreateShadow, 'max_inputs', 31), (CreateShadow, 'max_inputs', -1),
    (ControlShadow, 'expected_version', True),
    (ControlShadow, 'expected_version', 1.0),
    (ControlShadow, 'expected_version', '1'),
    (ControlShadow, 'expected_version', 0),
])
def test_versions_and_input_quota_never_coerce_or_clamp(model, field, value):
    body = create_body() if model is CreateShadow else control_body()
    body[field] = value
    rejected(model, body)


@pytest.mark.parametrize('model,field', [
    (CreateShadow, 'confirm_shadow_only'), (ControlShadow, 'confirm'),
])
@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_confirmation_requires_actual_true(model, field, value):
    body = create_body() if model is CreateShadow else control_body()
    body[field] = value
    rejected(model, body)


def test_all_fields_are_required_and_neither_family_accepts_extra_fields():
    for model, factory in [(CreateShadow, create_body), (ControlShadow, control_body)]:
        for field in factory():
            body = factory(); del body[field]
            rejected(model, body)
        for field, value in [('scope', {}), ('approved', True), ('candidate_id', GRANT)]:
            body = factory(); body[field] = value
            rejected(model, body)
    rejected(CreateShadow, control_body())
    rejected(ControlShadow, create_body())


@pytest.mark.parametrize('model,field,value', [
    (CreateShadow, 'title', ''), (CreateShadow, 'title', ' \t'),
    (CreateShadow, 'title', '名' * 201), (CreateShadow, 'title', None),
    (CreateShadow, 'reason', ''), (CreateShadow, 'reason', ' \n'),
    (CreateShadow, 'reason', '理' * 2001), (CreateShadow, 'reason', None),
    (ControlShadow, 'reason', ''), (ControlShadow, 'reason', '\t'),
    (ControlShadow, 'reason', '理' * 2001), (ControlShadow, 'reason', None),
])
def test_human_text_is_meaningful_bounded_and_never_defaulted(model, field, value):
    body = create_body() if model is CreateShadow else control_body()
    body[field] = value
    rejected(model, body)


@pytest.mark.parametrize('expiry', [
    '2026-10-22T09:30:00', '2026-10-22', '2026-02-30T01:00:00+00:00',
    '', ' 2026-10-22T01:30:00Z', '2026-10-22T01:30:00Z ',
    True, 1792632600, None, datetime(2026, 10, 22, tzinfo=timezone.utc),
])
def test_expiry_is_an_explicit_aware_string_without_conversion(expiry):
    body = create_body(); body['expires_at'] = expiry
    rejected(CreateShadow, body)


def test_pure_parser_does_not_claim_to_enforce_database_now_or_seven_day_window():
    for expiry in ['2000-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00']:
        body = create_body(); body['expires_at'] = expiry
        assert parse_command(CreateShadow, body).expires_at == expiry


def test_utf8_wire_roundtrip_preserves_original_human_text_and_numeric_kinds():
    body = create_body()
    raw = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    value = read_command(raw)
    assert value == body and type(value['max_inputs']) is int
    assert parse_command(CreateShadow, value).model_dump(by_alias=True) == body
    value['title'] = '独立修改不改变原wire'
    assert read_command(raw)['title'] == body['title']
    raw_float = raw.replace(b'"max_inputs":2', b'"max_inputs":2.0')
    parsed = read_command(raw_float)
    assert type(parsed['max_inputs']) is float
    rejected(CreateShadow, parsed)


@pytest.mark.parametrize('raw', [
    ('{"request_id":"' + REQUEST + '","request_id":"' + REQUEST + '"}').encode(),
    ('{"request_id":"' + REQUEST + '","nested":{"x":1,"\\u0078":2}}').encode(),
    ('{"request_id":"' + REQUEST + '","max_inputs":NaN}').encode(),
    ('{"request_id":"' + REQUEST + '","max_inputs":Infinity}').encode(),
    ('{"request_id":"' + REQUEST + '","max_inputs":1e309}').encode(),
    b'{"request_id":"\xff"}', b'{"request_id":"\\ud800"}',
    b'{"request_id":"\x00"}', b'\xef\xbb\xbf{}', b'{}{}', b'',
    b' ' * (64 * 1024 + 1), 'not bytes', bytearray(b'{}'),
])
def test_wire_rejects_ambiguity_bad_utf8_and_oversize_without_parsing_fallback(raw):
    with pytest.raises((ValueError, TypeError)):
        read_command(raw)


@pytest.mark.parametrize('raw', [b'[]', b'null', b'true', b'1', b'"text"', b'{}'])
def test_command_requires_an_object_with_original_request_id(raw):
    with pytest.raises((ValueError, TypeError)):
        read_command(raw)


@pytest.mark.parametrize('value', [REQUEST.upper(), REQUEST.replace('-', ''),
    UUID(REQUEST), 'not-a-uuid', True, None])
def test_canonical_uuid_never_invents_or_normalizes_an_identity(value):
    with pytest.raises((ValueError, TypeError)):
        canonical_uuid(value)
    body = create_body(); body['request_id'] = value
    rejected(CreateShadow, body)


@pytest.mark.parametrize('method,path', [
    ('GET', ROOT), ('PUT', ROOT), ('post', ROOT), ('POST', ROOT + '/'),
    ('POST', ROOT + '/' + GRANT + '/run'),
    ('POST', ROOT + '/' + GRANT.upper() + '/pause'),
    ('POST', ROOT + '/' + GRANT + '/pause/'),
    ('POST', ROOT + '/' + GRANT + '/%70ause'),
    ('POST', ROOT + '/' + GRANT + '/pause?x=1'),
    ('POST', '/api/local-rules/evolution/threshold-evaluations/' + GRANT + '/cancel'),
])
def test_route_identification_rejects_aliases_and_other_command_families(method, path):
    with pytest.raises((ValueError, TypeError)):
        identify(method, path)
