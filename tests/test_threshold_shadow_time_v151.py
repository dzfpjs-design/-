"""The shadow writer must use the reader's closed aware-time vocabulary.

These are pure lexical boundaries, not database-clock or future-expiry tests.
Original accepted text must remain unchanged; rejected text is not normalized.
"""
from copy import deepcopy
from datetime import datetime

from fastapi import HTTPException
import pytest

from generic_agents.threshold_shadow_protocol_v151 import CreateShadow, parse_command


def body(expiry):
    return {'schema': 'threshold-shadow-create-v151',
        'request_id': '15120000-0000-4000-8000-000000000001',
        'evaluation_id': '15120000-0000-4000-8000-000000000002',
        'expected_evaluation_version': 7, 'title': '原时间词法的影子观察',
        'reason': '明确时区，禁止保存后在停止界面无法读取',
        'max_inputs': 2, 'expires_at': expiry, 'confirm_shadow_only': True}


@pytest.mark.parametrize('expiry', [
    '2026-10-22T09:30:00+0800',
    '2026-10-22T09:30:00+08',
    '2026-10-22T09:30:00+08:00:30',
    '2026-10-22T09:30:00+08:00:30.5',
    '2026-10-22 09:30:00+08:00',
    '2026-10-22_09:30:00+08:00',
    '20261022T093000+0800',
    '2026-10-22T09:30:00,5+08:00',
])
def test_shadow_writer_rejects_python_only_aware_spellings_before_saving(expiry):
    # Distinguish a real writer/reader disagreement from an already invalid date.
    parsed = datetime.fromisoformat(expiry)
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None
    original = body(expiry); before = deepcopy(original)
    with pytest.raises(HTTPException) as error:
        parse_command(CreateShadow, original)
    assert error.value.status_code == 422
    assert type(error.value.detail) is str and error.value.detail
    assert original == before and original['expires_at'] == expiry


@pytest.mark.parametrize('expiry', [
    '2026-10-22T09:30:00+08:00',
    '2026-10-22T01:30:00+00:00',
    '2026-10-22T01:30:00Z',
    '2026-10-22T01:30:00.123456+00:00',
    '2026-10-22T09:30+08:00',
    '2026-10-22T01:30Z',
])
def test_shadow_common_reader_vocabulary_preserves_original_aware_text(expiry):
    original = body(expiry); before = deepcopy(original)
    parsed = parse_command(CreateShadow, original)
    assert parsed.expires_at == expiry and type(parsed.expires_at) is str
    assert parsed.model_dump(by_alias=True) == before
    assert original == before
