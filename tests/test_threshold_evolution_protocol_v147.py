"""Pure V147 wire/selection boundaries; no DB, worker or model assertions.

The prospective module is deliberately imported normally: its absence is only
an engineering red. Root's separately owned real HTTP case proves the missing
product workflow. Fingerprints bind a command; they do not grant permission.
"""
import hashlib
import math

import pytest

from generic_agents.threshold_evolution_protocol_v147 import (
    ObservationSelectionCommand,
    command_fingerprint,
    read_wire,
    wire_sha256,
)
from generic_agents.typed_payload_codec_v131 import canonical_json


WORKSPACE = '11111111-1111-4111-8111-111111111111'
ACTOR = '22222222-2222-4222-8222-222222222222'
GRANT = '33333333-3333-4333-8333-333333333333'
REQUEST = '44444444-4444-4444-8444-444444444444'
OTHER = '55555555-5555-4555-8555-555555555555'
ROOT = '/api/local-rules/evolution'
PATH = ROOT + '/grants/' + GRANT + '/observations'
ERRORS = (ValueError, TypeError)


def selection_body():
    return {
        'schema': 'threshold-evolution-observation-command-v147',
        'request_id': REQUEST,
        'expected_version': 1,
        'expected_choice': 'candidate',
        'source_record_id': 'operator-reading-1',
        'processing_version': '1',
        'observed_at': '2026-10-21T09:15:00-07:00',
        'business_day': '2026-10-21',
        'attributes': {'温度': -0.0, '计数': 1.0, '说明': '1.0'},
    }


def fingerprint(**changes):
    bound = {
        'method': 'POST', 'path': PATH,
        'workspace_id': WORKSPACE, 'actor_id': ACTOR,
        'raw_sha256': wire_sha256(b'{"request_id":"' + REQUEST.encode() + b'"}'),
    }
    bound.update(changes)
    return command_fingerprint(**bound)


def test_read_wire_returns_plain_json_and_preserves_original_numeric_kinds():
    raw = ('{"zero":-0.0,"float":1.0,"integer":1,"large":9007199254740993,'
        '"exponent":1e21,"text":"1.0","flag":true,"nested":[null,{"温度":2}]}').encode('utf-8')
    value = read_wire(raw)

    assert type(value) is dict and type(value['nested']) is list
    assert type(value['nested'][1]) is dict
    assert type(value['zero']) is float and math.copysign(1, value['zero']) == -1
    assert type(value['float']) is float and type(value['integer']) is int
    assert type(value['large']) is int and value['large'] == 9007199254740993
    assert type(value['exponent']) is float and value['exponent'] == 1e21
    assert type(value['text']) is str and value['text'] == '1.0'
    assert value['flag'] is True and value['nested'][0] is None
    # A plain JSON boolean is valid here; numeric DTO/schema gates are separate.
    value['nested'][1]['温度'] = 99
    assert read_wire(raw)['nested'][1]['温度'] == 2


@pytest.mark.parametrize('raw', [
    b'{"a":1,"a":2}',
    b'{"nested":{"a":1,"\\u0061":2}}',
    b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}',
    b'{"a":1e309}',
    b'{"a":1,}', b'{"a":01}', b'{}{}', b'',
    b'{"a":"\xff"}', b'{"a":"\\ud800"}',
    b'{"a":"\x00"}',
])
def test_read_wire_rejects_ambiguous_nonfinite_or_invalid_json(raw):
    with pytest.raises(ERRORS):
        read_wire(raw)


@pytest.mark.parametrize('raw', ['{}', bytearray(b'{}'), None])
def test_read_wire_requires_original_bytes_not_a_rebuilt_tree_or_text(raw):
    with pytest.raises(ERRORS):
        read_wire(raw)


def test_read_wire_limit_is_64_kib_of_utf8_bytes_inclusive():
    prefix, suffix = b'{"text":"', b'"}'
    at_limit = prefix + b'a' * (65536 - len(prefix) - len(suffix)) + suffix
    assert len(at_limit) == 65536
    assert len(read_wire(at_limit)['text']) == 65536 - len(prefix) - len(suffix)
    with pytest.raises(ERRORS):
        read_wire(at_limit + b' ')

    multibyte = ('{"text":"' + '林' * 22000 + '"}').encode('utf-8')
    assert len(multibyte) > 65536
    with pytest.raises(ERRORS):
        read_wire(multibyte)


def test_wire_sha_is_over_exact_original_utf8_bytes_not_parsed_canonical_json():
    raws = [b'{"value":1}', b'{ "value" : 1 }', '{"值":1}'.encode('utf-8')]
    hashes = [wire_sha256(raw) for raw in raws]
    assert hashes == [hashlib.sha256(raw).hexdigest() for raw in raws]
    assert hashes[0] != hashes[1]
    assert canonical_json(read_wire(raws[0])) == canonical_json(read_wire(raws[1]))


def test_typed_tree_hashes_and_original_wire_hashes_do_not_merge_numeric_identity():
    raws = [b'{"value":-0.0}', b'{"value":0.0}', b'{"value":0}',
        b'{"value":1.0}', b'{"value":1}', b'{"value":"1.0"}']
    typed_hashes = [hashlib.sha256(canonical_json(read_wire(raw)).encode('utf-8')).hexdigest()
        for raw in raws]
    assert len(set(typed_hashes)) == len(raws)
    assert len({wire_sha256(raw) for raw in raws}) == len(raws)
    assert len({fingerprint(raw_sha256=wire_sha256(raw)) for raw in raws}) == len(raws)


def test_command_fingerprint_is_deterministic_and_binds_each_original_owner_dimension():
    original = fingerprint()
    assert type(original) is str and len(original) == 64
    assert set(original) <= set('0123456789abcdef')
    assert original == fingerprint()
    variants = [
        fingerprint(method='PUT'),
        fingerprint(path=ROOT + '/grants/' + OTHER + '/observations'),
        fingerprint(path=ROOT + '/feedback'),
        fingerprint(workspace_id=OTHER), fingerprint(actor_id=OTHER),
        fingerprint(raw_sha256=wire_sha256(b'{ "request_id":"' + REQUEST.encode() + b'"}')),
    ]
    assert len(set([original, *variants])) == 1 + len(variants)


@pytest.mark.parametrize('changes', [
    {'method': 'GET'}, {'method': 'DELETE'}, {'method': 'post'},
    {'method': 'POST '},
    {'path': 'api/local-rules/evolution/feedback'},
    {'path': 'https://example.invalid/api/local-rules/evolution/feedback'},
    {'path': '/api/local-rules/other/feedback'},
    {'path': ROOT + '/grants/../feedback'},
    {'path': ROOT + '//feedback'},
    {'path': ROOT + '/grants/%2e%2e/feedback'},
    {'path': ROOT + '/feedback/%2Fother'},
    {'path': ROOT + '/反馈'},
    {'path': PATH + '?grant=' + OTHER}, {'path': PATH + '#other'},
    {'path': PATH + '\n'},
    {'workspace_id': ''}, {'actor_id': ''},
    {'raw_sha256': 'a' * 63}, {'raw_sha256': 'g' * 64},
])
def test_command_fingerprint_rejects_invalid_bindings_instead_of_normalizing_them(changes):
    with pytest.raises(ERRORS):
        fingerprint(**changes)


@pytest.mark.parametrize('choice', ['candidate', 'baseline'])
def test_selection_dto_is_closed_and_keeps_original_typed_attributes(choice):
    body = selection_body()
    body['expected_choice'] = choice
    before = canonical_json(body)
    model = ObservationSelectionCommand.model_validate(body)
    saved = model.model_dump(mode='json', by_alias=True)

    assert set(saved) == set(body)
    assert saved['schema'] == body['schema'] and saved['request_id'] == REQUEST
    assert type(saved['expected_version']) is int and saved['expected_version'] == 1
    assert saved['expected_choice'] == choice
    assert type(saved['attributes']['温度']) is float
    assert math.copysign(1, saved['attributes']['温度']) == -1
    assert type(saved['attributes']['计数']) is float
    assert saved['attributes']['说明'] == '1.0'
    assert canonical_json(body) == before, 'DTO validation must not edit the caller input'


@pytest.mark.parametrize(('field', 'value'), [
    ('schema', 'threshold-evolution-observation-command-v146'),
    ('request_id', 'not-a-uuid'), ('request_id', 1), ('request_id', None),
    ('expected_version', True), ('expected_version', 1.0),
    ('expected_version', '1'), ('expected_version', 0),
    ('expected_choice', 'automatic'),
    ('source_record_id', ''), ('source_record_id', 'x' * 201),
    ('processing_version', ''), ('processing_version', 'x' * 41),
    ('observed_at', '2026-10-21T09:15:00'),
    ('observed_at', 'not-an-instant'), ('observed_at', 0),
    ('business_day', 'not-a-date'),
    ('attributes', {str(i): i for i in range(31)}), ('attributes', []),
])
def test_selection_dto_rejects_bad_schema_nonce_versions_time_or_bounds(field, value):
    body = selection_body()
    body[field] = value
    with pytest.raises(ERRORS):
        ObservationSelectionCommand.model_validate(body)


@pytest.mark.parametrize('extra', [
    'extractor_id', 'source_id', 'entity_id', 'release_id',
    'evidence_ids', 'acquisition_id', 'sensor_asset_id',
])
def test_selection_dto_cannot_supply_or_override_the_grant_scope(extra):
    body = selection_body()
    body[extra] = OTHER
    with pytest.raises(ERRORS):
        ObservationSelectionCommand.model_validate(body)
