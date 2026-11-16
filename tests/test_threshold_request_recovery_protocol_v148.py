"""Pure V148 seams; Root separately owns the real HTTP/PG product negatives.

Frozen proposal accepted by Root:
* read_retirement_wire(bytes) -> plain root dict, <=512 KiB strict JSON.
* RetirementCommand -> exact eight-field DTO, no defaults or normalization.
* retirement_identity(original_request_id, command) -> exact six fields:
  request_id, method, path, wire_sha256, operation, owner_refs. The request_id
  is the OLD nonce; create owners are observation_id/rule_id, all other routes
  use feedback_id. This pure result grants no workspace or mutation authority.
* retirement_receipt_hash(unsigned) validates exactly these eleven keys:
  schema,id,request_id,original_request_id,actor_id,workspace_id,original_method,
  original_path,original_sha256,reason,outcome. schema is
  threshold-request-retirement-receipt-v148 and outcome is retired. The hash is
  SHA256(canonical_json(unsigned).encode('utf-8')). Never include receipt_hash,
  SQL times, versions, current basis, raw wire, or invented dispatch authority.

These imports intentionally fail before implementation; this is an engineering
red only. Root's real persisted Command and three new-route 404 checks are the
separate product evidence. No database, model, network, or fallback is used here.
"""
import copy
import hashlib
import json
import math

import pytest

from generic_agents.threshold_request_recovery_protocol_v148 import (
    RetirementCommand, read_retirement_wire, retirement_identity, retirement_receipt_hash,
)
from generic_agents.typed_payload_codec_v131 import canonical_json


OLD = '11111111-1111-4111-8111-111111111111'
NEW = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OBS = '22222222-2222-4222-8222-222222222222'
FEEDBACK = '33333333-3333-4333-8333-333333333333'
ACTOR = '44444444-4444-4444-8444-444444444444'
WORKSPACE = '55555555-5555-4555-8555-555555555555'
RECEIPT = '66666666-6666-4666-8666-666666666666'
ROOT = '/api/local-rules/evolution'
ERRORS = (ValueError, TypeError)


def original_raw(**changes):
    # Deliberately invalid BUSINESS DTO: identity remains unambiguous.
    body = {'request_id': OLD, 'observation_id': OBS, 'rule_id': 'count_high',
            'expected_observation_version': 0, 'label': 'not-a-label', 'reason': None}
    body.update(changes)
    return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def command_body(original=None, **changes):
    original = original_raw() if original is None else original
    value = {'schema': 'threshold-request-retirement-command-v148', 'request_id': NEW,
        'original_method': 'POST', 'original_path': ROOT + '/feedback',
        'original_wire_text': original,
        'original_sha256': hashlib.sha256(original.encode('utf-8')).hexdigest(),
        'reason': '  保留这份原请求，请停止接收；原人稿不删除。  ', 'confirm': True}
    value.update(changes)
    return value


def identity(value=None, old=OLD):
    return retirement_identity(old, RetirementCommand.model_validate(value or command_body()))


def unsigned_receipt(**changes):
    value = {'schema': 'threshold-request-retirement-receipt-v148', 'id': RECEIPT,
        'request_id': NEW, 'original_request_id': OLD, 'actor_id': ACTOR,
        'workspace_id': WORKSPACE, 'original_method': 'POST', 'original_path': ROOT + '/feedback',
        'original_sha256': command_body()['original_sha256'], 'reason': '保留原稿并结束原请求',
        'outcome': 'retired'}
    value.update(changes)
    return value


def test_strict_reader_preserves_numeric_types_sign_and_original_wire_string():
    raw = ('{"zero":-0.0,"float":1.0,"integer":1,"large":9007199254740993,'
           '"exponent":1e21,"text":"1.0",').encode('utf-8')
    # Build the nested string with the standard encoder; no original wire is rebuilt by the seam.
    raw += b'"wire":' + json.dumps('{ "v": -0.0 }').encode() + b'}'
    value = read_retirement_wire(raw)
    assert type(value) is dict and type(value['zero']) is float
    assert math.copysign(1, value['zero']) == -1
    assert type(value['float']) is float and type(value['integer']) is int
    assert type(value['large']) is int and value['large'] == 9007199254740993
    assert type(value['exponent']) is float and value['exponent'] == 1e21
    assert value['text'] == '1.0' and value['wire'] == '{ "v": -0.0 }'


@pytest.mark.parametrize('raw', [
    b'{"a":1,"a":2}', b'{"nested":{"a":1,"\\u0061":2}}',
    b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e309}', b'{"a":01}',
    b'{}{}', b'{"a":"\xff"}', b'{"a":"\\ud800"}', b'{"a":"\\u0000"}',
    b'[]', b'null',
])
def test_reader_rejects_nonobject_ambiguous_nonfinite_or_invalid_text(raw):
    with pytest.raises(ERRORS): read_retirement_wire(raw)


@pytest.mark.parametrize('raw', ['{}', bytearray(b'{}'), {}, None])
def test_reader_requires_actual_bytes(raw):
    with pytest.raises(ERRORS): read_retirement_wire(raw)


def test_reader_512_kib_bound_is_inclusive_utf8_bytes_not_character_count():
    start, end = b'{"text":"', b'"}'
    raw = start + b'x' * (512 * 1024 - len(start) - len(end)) + end
    assert len(raw) == 512 * 1024 and isinstance(read_retirement_wire(raw), dict)
    with pytest.raises(ERRORS): read_retirement_wire(raw + b' ')
    multibyte = ('{"text":"' + '林' * 175000 + '"}').encode('utf-8')
    assert len(multibyte) > 512 * 1024
    with pytest.raises(ERRORS): read_retirement_wire(multibyte)


def test_dto_is_exact_and_does_not_trim_reason_or_rebuild_original_wire():
    value = command_body()
    before = copy.deepcopy(value)
    model = RetirementCommand.model_validate(value)
    assert model.model_dump(mode='json', by_alias=True) == before
    assert value == before and model.original_wire_text == before['original_wire_text']
    assert model.reason.startswith('  ') and model.reason.endswith('  ')
    for key in before:
        missing = dict(before); missing.pop(key)
        with pytest.raises(ERRORS): RetirementCommand.model_validate(missing)
    for key in ('actor_id', 'workspace_id', 'owner_refs'):
        with pytest.raises(ERRORS): RetirementCommand.model_validate({**before, key: ACTOR})


@pytest.mark.parametrize(('key', 'value'), [
    ('schema', 'threshold-request-retirement-command-v147'),
    ('request_id', NEW.upper()), ('request_id', None),
    ('original_method', 'post'), ('original_method', 'GET'),
    ('original_sha256', 'A' * 64), ('original_sha256', 'a' * 63),
    ('reason', ''), ('reason', ' \n\t'), ('reason', 'x' * 2001),
    ('confirm', False), ('confirm', 1), ('confirm', 'true'),
])
def test_dto_rejects_wrong_literal_types_and_bounds(key, value):
    with pytest.raises(ERRORS): RetirementCommand.model_validate(command_body(**{key: value}))


@pytest.mark.parametrize(('method', 'path', 'operation', 'owners'), [
    ('POST', ROOT + '/feedback', 'feedback_create', {'observation_id': OBS, 'rule_id': 'count_high'}),
    ('PUT', ROOT + '/feedback/' + FEEDBACK, 'feedback_save', {'feedback_id': FEEDBACK}),
    ('POST', ROOT + '/feedback/' + FEEDBACK + '/submit', 'feedback_submit', {'feedback_id': FEEDBACK}),
    ('POST', ROOT + '/feedback/' + FEEDBACK + '/cancel', 'feedback_cancel', {'feedback_id': FEEDBACK}),
    ('POST', ROOT + '/feedback/' + FEEDBACK + '/revisions', 'feedback_revisions', {'feedback_id': FEEDBACK}),
])
def test_identity_accepts_only_original_route_identity_without_business_validation(method, path, operation, owners):
    value = command_body(original_method=method, original_path=path)
    result = identity(value)
    assert result == {'request_id': OLD, 'method': method, 'path': path,
                      'wire_sha256': value['original_sha256'], 'operation': operation, 'owner_refs': owners}


@pytest.mark.parametrize('changes', [
    {'expected_observation_version': 'bad-CAS'}, {'label': {'not': 'a-label'}},
    {'confirm': False}, {'reason': ['not', 'text']},
])
def test_invalid_business_fields_do_not_need_to_be_fixed_before_identity_is_retired(changes):
    raw = original_raw(**changes)
    value = command_body(raw)
    assert identity(value)['owner_refs'] == {'observation_id': OBS, 'rule_id': 'count_high'}
    assert RetirementCommand.model_validate(value).original_wire_text == raw


def test_identity_does_not_rewrite_typed_raw_or_merge_whitespace_variants():
    raw = ('{ "request_id":"' + OLD + '","observation_id":"' + OBS + '",'
           '"rule_id":"count_high","invalid_values":[-0.0,1.0,1,1e21,9007199254740993,"1.0"] }')
    value = command_body(raw)
    model = RetirementCommand.model_validate(value)
    before = model.original_wire_text
    result = retirement_identity(OLD, model)
    assert model.original_wire_text == before == raw
    assert result['wire_sha256'] == hashlib.sha256(raw.encode('utf-8')).hexdigest()
    compact = json.dumps(json.loads(raw), ensure_ascii=False, separators=(',', ':'))
    assert compact != raw and identity(command_body(compact))['wire_sha256'] != result['wire_sha256']
    wrong = {**value, 'original_wire_text': compact}
    with pytest.raises(ERRORS): identity(wrong)


@pytest.mark.parametrize('raw', [
    '{}', '[]', 'null', original_raw()[:-1] + ',"request_id":"' + OLD + '"}',
    '{"request_id":"' + OLD + '","observation_id":"' + OBS + '","rule_id":"count_high","n":NaN}',
    '{"request_id":"' + OLD + '","observation_id":"' + OBS + '","rule_id":"count_high","n":1e309}',
    '{"request_id":"' + OLD + '",}',
])
def test_identity_rejects_unverifiable_original_json(raw):
    with pytest.raises(ERRORS): identity(command_body(raw))


@pytest.mark.parametrize(('method', 'path'), [
    ('POST', ROOT + '/feedback/'), ('PUT', ROOT + '/feedback'),
    ('POST', ROOT + '/feedback/' + FEEDBACK), ('PUT', ROOT + '/feedback/' + FEEDBACK + '/submit'),
    ('POST', ROOT + '/feedback/' + FEEDBACK + '/submit?x=1'),
    ('POST', ROOT + '/feedback/' + FEEDBACK + '/submit#x'),
    ('POST', 'https://example.invalid' + ROOT + '/feedback'),
    ('POST', ROOT + '/feedback/%33' + FEEDBACK[1:] + '/submit'),
    ('POST', ROOT + '/feedback/../feedback'), ('POST', ROOT + '//feedback'),
    ('POST', ROOT + '/candidates'), ('POST', ROOT + '/requests/' + OLD + '/retire'),
])
def test_identity_rejects_route_aliases_method_mismatch_and_cross_family(method, path):
    with pytest.raises(ERRORS): identity(command_body(original_method=method, original_path=path))


@pytest.mark.parametrize('changes', [
    {'request_id': OLD}, {'original_sha256': '0' * 64},
    {'original_wire_text': original_raw(request_id=NEW)},
    {'original_wire_text': original_raw(request_id=NEW.upper())},
    {'original_wire_text': original_raw(request_id=1)},
])
def test_identity_rejects_shared_or_changed_nonce_and_unmatched_sha(changes):
    value = command_body(**changes)
    if 'original_wire_text' in changes:
        # A correct SHA must not hide the independent original-nonce check.
        value['original_sha256'] = hashlib.sha256(value['original_wire_text'].encode('utf-8')).hexdigest()
    with pytest.raises(ERRORS): identity(value)


@pytest.mark.parametrize('changes', [
    {'observation_id': None}, {'observation_id': NEW.upper()},
    {'observation_id': 'not-a-uuid'}, {'rule_id': ''}, {'rule_id': 7}, {'rule_id': 'x' * 81},
])
def test_create_identity_needs_unambiguous_owner_fields(changes):
    with pytest.raises(ERRORS): identity(command_body(original_raw(**changes)))


def test_original_limit_is_64_kib_even_inside_a_larger_valid_retirement_envelope():
    raw = original_raw()
    raw += ' ' * (64 * 1024 - len(raw.encode('utf-8')))
    value = command_body(raw)
    envelope = json.dumps(value, ensure_ascii=False).encode('utf-8')
    assert 64 * 1024 < len(envelope) < 512 * 1024
    parsed = read_retirement_wire(envelope)
    assert identity(parsed)['wire_sha256'] == value['original_sha256']
    with pytest.raises(ERRORS): identity(command_body(raw + ' '))


def test_receipt_hash_has_the_frozen_closed_canonical_oracle_and_no_mutation():
    value = unsigned_receipt(); before = copy.deepcopy(value)
    expected = hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()
    assert retirement_receipt_hash(value) == expected
    assert retirement_receipt_hash(dict(reversed(list(value.items())))) == expected
    assert value == before and len(expected) == 64
    assert retirement_receipt_hash(unsigned_receipt(reason=' 保留原稿并结束原请求 ')) != expected
    for key in value:
        missing = dict(value); missing.pop(key)
        with pytest.raises(ERRORS): retirement_receipt_hash(missing)
    for key in ('receipt_hash', 'created_at', 'version', 'basis', 'original_wire_text'):
        with pytest.raises(ERRORS): retirement_receipt_hash({**value, key: expected})


@pytest.mark.parametrize('changes', [
    {'id': OBS}, {'request_id': OBS}, {'original_request_id': OBS},
    {'actor_id': OBS}, {'workspace_id': OBS}, {'original_sha256': '0' * 64},
    {'original_path': ROOT + '/feedback/' + FEEDBACK + '/submit'},
    {'original_method': 'PUT', 'original_path': ROOT + '/feedback/' + FEEDBACK},
])
def test_receipt_hash_binds_each_identity_dimension(changes):
    assert retirement_receipt_hash(unsigned_receipt(**changes)) != retirement_receipt_hash(unsigned_receipt())


@pytest.mark.parametrize('changes', [
    {'schema': 'threshold-request-retirement-receipt-v147'}, {'outcome': 'saved'},
    {'outcome': True}, {'id': 1}, {'request_id': NEW.upper()},
    {'original_request_id': NEW}, {'actor_id': ''}, {'workspace_id': None},
    {'original_method': 'GET'}, {'original_path': ROOT + '/grants/' + FEEDBACK},
    {'original_sha256': 'A' * 64}, {'reason': ' \n\t'}, {'reason': 1},
])
def test_receipt_hash_rejects_wrong_shape_types_or_retired_identity(changes):
    with pytest.raises(ERRORS): retirement_receipt_hash(unsigned_receipt(**changes))
