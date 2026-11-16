"""Closed human case commands; Root runs these tests, no provider access."""
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import layered_cases_v209 as cases


def value():
    return {'title': '工程资料核对经验', 'outcome': '已按选取原文完成工程核对',
        'experience': '先保留原件及识别边界，再逐级确认', 'applicable_scope': '本工程资料核对',
        'caveats': '并未证明现场对象状态，不能自动发布或操作设备'}


def create_body():
    return {'request_id': str(uuid4()), 'expected_source_task_version': 7,
        'source_fingerprint': 'a' * 64, 'value': value()}


def encoded(body): return json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()


def test_create_keeps_original_utf8_and_semantic_values():
    original = create_body(); original['value']['title'] += '😀'
    raw = json.dumps(original, ensure_ascii=False, indent=2).encode()
    body, exact, hashed = cases.parse(raw, 'create')
    import hashlib
    assert exact.encode() == raw and hashed == hashlib.sha256(raw).hexdigest()
    assert body.model_dump() == original


@pytest.mark.parametrize('mutation', ['extra', 'bool_version', 'float_version', 'numeric_title',
    'empty_experience', 'nul_caveats', 'long_outcome', 'bad_sha', 'bad_uuid'])
def test_invalid_create_is_rejected_without_coercion_or_truncation(mutation):
    body = create_body()
    if mutation == 'extra': body['trusted'] = True
    if mutation == 'bool_version': body['expected_source_task_version'] = True
    if mutation == 'float_version': body['expected_source_task_version'] = 7.0
    if mutation == 'numeric_title': body['value']['title'] = 123
    if mutation == 'empty_experience': body['value']['experience'] = '   '
    if mutation == 'nul_caveats': body['value']['caveats'] = '边界\x00'
    if mutation == 'long_outcome': body['value']['outcome'] = '原' * 6001
    if mutation == 'bad_sha': body['source_fingerprint'] = 'A' * 64
    if mutation == 'bad_uuid': body['request_id'] = 'new_request'
    with pytest.raises(HTTPException) as caught: cases.parse(encoded(body), 'create')
    assert caught.value.status_code == 422


@pytest.mark.parametrize('raw', [b'{"request_id":"x","request_id":"y"}', b'{"value":NaN}', b'\xff'])
def test_duplicate_nonfinite_or_non_utf8_original_is_rejected(raw):
    with pytest.raises(HTTPException) as caught: cases.parse(raw, 'create')
    assert caught.value.status_code == 422


def test_case_request_capacity_is_explicit():
    with pytest.raises(HTTPException) as caught: cases.parse(b' ' * 65537, 'create')
    assert caught.value.status_code == 413


@pytest.mark.parametrize('operation', ['submit', 'withdraw', 'review', 'revise'])
def test_change_contract_has_fixed_revision_and_explicit_reason(operation):
    body = {'request_id': str(uuid4()), 'expected_case_version': 2, 'revision_id': str(uuid4()),
        'revision_hash': 'b' * 64, 'reason': '按原固定版本核对'}
    if operation == 'review': body['decision'] = 'approve'
    if operation == 'revise': body['value'] = value()
    assert cases.parse(encoded(body), operation)[0].model_dump() == body
    body['reason'] = '\n'
    with pytest.raises(HTTPException): cases.parse(encoded(body), operation)
