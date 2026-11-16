"""Pure consent/original-body boundaries; never a recovery authorization."""
import hashlib
import json
import pytest
from fastapi import HTTPException
from generic_agents.contracts import digest
from generic_agents.sound_result_settlement import parse


def body(**changes):
    return {'schema': 'sound-result-settlement-v1', 'request_id': 'sound-settle-original',
        'task_version': 3, 'run_version': 2, 'reason': '继续交接原成果，不重新分析。',
        'confirm_original_result_handoff': True, **changes}


def test_raw_original_and_normalized_body_have_distinct_preserved_hashes():
    raw = json.dumps(body(), ensure_ascii=False, indent=2).encode()
    typed, exact, hashed = parse(raw)
    assert exact.encode() == raw and hashed == hashlib.sha256(raw).hexdigest()
    assert typed.model_dump(mode='json') == body() and hashed != digest(body())


@pytest.mark.parametrize('patch', [{'confirm_original_result_handoff': 1}, {'confirm_original_result_handoff': False},
    {'task_version': True}, {'run_version': 0}, {'reason': '   '}, {'reason': '\x00'},
    {'request_id': 'bad'}, {'model_rerun': True}, {'schema': 'other'}])
def test_cannot_turn_recovery_into_an_implicit_or_broader_execution(patch):
    with pytest.raises(HTTPException) as caught:
        parse(json.dumps(body(**patch)).encode())
    assert caught.value.status_code == 422


def test_duplicate_fields_are_not_normalized_into_a_different_original():
    raw = json.dumps(body()).replace('"run_version": 2', '"run_version": 2, "run_version": 99').encode()
    with pytest.raises(HTTPException) as caught: parse(raw)
    assert caught.value.status_code == 422
