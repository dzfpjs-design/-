"""History display/cursor contracts only; no service/database/transport."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import business_data_history as h


def test_old_aba_failure_explains_version_change_and_controls_are_removed():
    assert '读取期间连接版本' in h.failure_detail('本次读取期间连接或执行批准已变化；原读取保留，须明确恢复', 'fallback')
    assert h.failure_detail('CSV表头\x00含重复字段\n未截断', 'fallback') == 'CSV表头含重复字段未截断'
    assert h.failure_detail({'error': 'not a safe string'}, 'fallback') == 'fallback'


@pytest.mark.parametrize('value', ['https://public.example/private?token=original', '/Users/owner/private/file',
    '读取 /srv/private/input.bin 失败', 'password=do-not-echo', 'Authorization: Bearer original', 'C:\\Users\\owner\\secret',
    '0' * 64, str(uuid4()), '{"body":"private"}'])
def test_error_display_never_echoes_urls_private_paths_credentials_or_internal_ids(value):
    rendered = h.failure_detail(value, 'fallback')
    assert value not in rendered and '已在此隐藏' in rendered


def test_cursor_is_subject_scoped_canonical_bounded_and_timezone_aware():
    workspace, subject, audit_id = map(str, (uuid4(), uuid4(), uuid4()))
    audit = NS(created_at=datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc), id=audit_id)
    cursor = h._encode(workspace, 'read', subject, audit)
    assert h._decode(cursor, workspace, 'read', subject) == (audit.created_at, audit_id)
    for value in ('', '!' * 10, 'a' * 769, cursor + '=', cursor[:-1]):
        with pytest.raises(HTTPException) as error: h._decode(value, workspace, 'read', subject)
        assert error.value.status_code == 422
    with pytest.raises(HTTPException): h._decode(cursor, workspace, 'import', subject)
    with pytest.raises(HTTPException): h._decode(cursor, str(uuid4()), 'read', subject)
