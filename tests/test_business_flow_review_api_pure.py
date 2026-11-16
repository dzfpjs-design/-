"""Synthetic source/capture and SQL boundaries around actual review adapters.

Native envelope inspection, diagnostics, exact-wire parsing and draft inventory
are real. Original Attempt admission/read_response are explicit dependency
fixtures. This is not a provider, real file, PostgreSQL or HTTP login test.
"""
import asyncio
import copy
import hashlib
import json
import socket
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from generic_agents import business_flow_review_api as api
from generic_agents import business_flow_review_source as source
from generic_agents import business_flow_review_store as store
from generic_agents import models as m
from generic_agents import report_scoped_design_profiles_v182 as profiles
from tests.test_business_flow_review_store import IO, scene as storage_scene, body, wire, identity
from tests.test_report_business_flow_preparation_v183 import prepared
from tests.test_report_business_flow_protocol_v182 import answer, response
from tests.test_report_business_flow_wire_v182 import scene as flow_scene
from tests.test_report_scoped_design_store_v181 import same_statement


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Adapters must not connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def flags(value):
    assert all(value[key] is False for key in ('semantic_quality_verified', 'can_apply', 'can_publish', 'can_execute'))


def source_case(monkeypatch):
    _, original_source, _, preparation, _ = prepared(); value = answer(preparation)
    value['evidence'][0]['quote'] = value['evidence'][0]['quote'][:-1]
    row = NS(id=identity('original-attempt'), producer_id=original_source['snapshot']['producer']['id'],
        model_id=identity('model'), revision=7, status='failed', reserved_call_id=identity('call'),
        data={'preview': {'preparation': preparation, 'source_context': original_source}})
    p = NS(user_id=original_source['snapshot']['actor_id'], workspace_id=original_source['snapshot']['workspace_id'], role='operator')
    db, calls = object(), []
    envelope = response(json.dumps(value, ensure_ascii=False))
    saved = {'body': envelope, 'receipt': {'call_id': row.reserved_call_id},
        'capture': {'capture_hash': 'b' * 64, 'http_status': 200, 'ready': True,
            'response_complete': True, 'http_content_encoding': None}}
    def checked(actual_db, principal, attempt_id):
        assert actual_db is db and principal is p and attempt_id == row.id
        calls.append('original_inventory'); return row
    def read(actual_db, principal, actual_row, *, recover_saved):
        assert actual_db is db and principal is p and actual_row is row and recover_saved is False
        calls.append('read_original_no_recovery'); return copy.deepcopy(saved)
    def forbidden(*args, **kwargs): raise AssertionError('Human review cannot admit or dispatch another call')
    monkeypatch.setattr(source.attempts, 'checked_attempt', checked)
    monkeypatch.setattr(source.runtime, 'read_response', read)
    monkeypatch.setattr(source.runtime, 'admit', forbidden)
    monkeypatch.setattr(source.runtime, 'dispatch', forbidden)
    return NS(row=row, p=p, db=db, calls=calls, saved=saved, value=value)


def test_source_load_reads_original_complete_failed_answer_and_hashes_without_model_parse_repair_or_new_call(monkeypatch):
    c = source_case(monkeypatch); before = copy.deepcopy(c.row.data)
    family = profiles.selected(c.row.data['preview']['preparation'])
    def forbidden(*args, **kwargs): raise AssertionError('Invalid original flow remains editable, not regenerated or reparsed as valid')
    monkeypatch.setattr(family.native, 'parse_arguments', forbidden)
    loaded = source.load(c.db, c.p, c.row.id)
    assert c.calls == ['original_inventory', 'read_original_no_recovery']
    assert loaded['baseline_value'] == c.value and loaded['source_binding']['generation_profile'] == profiles.FLOW_COMPAT
    assert loaded['source_binding']['reserved_call_id'] == c.row.reserved_call_id
    assert loaded['source_binding']['response_body_sha256'] == hashlib.sha256(c.saved['body']).hexdigest()
    actual_content = json.loads(c.saved['body'])['choices'][0]['message']['content'].encode('utf-8')
    assert loaded['source_binding']['arguments_sha256'] == hashlib.sha256(actual_content).hexdigest()
    diagnostic = source.diagnose(loaded, loaded['baseline_value']); flags(diagnostic)
    assert diagnostic['available'] is diagnostic['structural_valid'] is True and diagnostic['source_valid'] is False
    assert any(item['code'] == 'quote_mismatch' for item in diagnostic['issues'])
    loaded['baseline_value']['evidence'][0]['quote'] = 'local edit'
    assert c.row.data == before and c.row.status == 'failed'


@pytest.mark.parametrize('fault,code', [('unknown_result', 409), ('missing_call', 409),
    ('nonflow_family', 422), ('wrong_receipt_call', 409), ('incomplete', 409), ('length', 409), ('duplicate_content', 409)])
def test_source_adapter_rejects_unknown_mixed_or_incomplete_original_without_fallback(monkeypatch, fault, code):
    c = source_case(monkeypatch)
    if fault == 'unknown_result': c.row.status = 'response_unknown'
    elif fault == 'missing_call': c.row.reserved_call_id = None
    elif fault == 'nonflow_family': c.row.data['preview']['preparation']['schema'] = profiles.MARKERS[profiles.SCOPED]
    elif fault == 'wrong_receipt_call': c.saved['receipt']['call_id'] = identity('wrong-call')
    elif fault == 'incomplete': c.saved['capture']['response_complete'] = False
    elif fault == 'length': c.saved['body'] = response(json.dumps(c.value), finish='length')
    else:
        content = json.dumps(c.value, ensure_ascii=False)
        c.saved['body'] = response(content[:-1] + ',"business":{}}')
    with pytest.raises(HTTPException) as error: source.load(c.db, c.p, c.row.id)
    assert error.value.status_code == code and c.calls.count('read_original_no_recovery') <= 1


def draft_case(monkeypatch):
    c = storage_scene(); io = IO(c); baseline, inputs = flow_scene()
    baseline['evidence'][0]['quote'] = baseline['evidence'][0]['quote'][:-1]
    c.baseline = baseline; c.source['source_snapshot_hash'] = inputs['scope_binding']['source_snapshot_hash']
    loaded = {'source_binding': c.source, 'baseline_value': baseline, 'name': inputs['scope_binding']['name'], 'diagnostic_context': inputs}
    calls = []
    def load(db, principal, attempt_id):
        assert db is io and principal is c.p and attempt_id == c.attempt.id
        # Both GET and POST must have already taken the draft-owner mutex.
        assert io.locks
        calls.append('source'); return copy.deepcopy(loaded)
    monkeypatch.setattr(source, 'load', load)
    return c, io, loaded, calls


def test_saved_incomplete_draft_reloads_with_source_missing_and_exact_replay_needs_no_source_or_new_write(monkeypatch):
    c, io, loaded, calls = draft_case(monkeypatch)
    fresh = api.detail(io, c.p, c.attempt.id); flags(fresh)
    assert fresh['version'] == 0 and fresh['saved'] is False and fresh['historical_source'] is True
    assert fresh['current_source_checked'] is False and fresh['baseline_value'] == c.baseline
    assert io.commits == 0 and io.drafts == io.commands == io.events == []
    content = wire(body(value={}, note='未完成但已经保存的人工作业'))
    saved = api.save(io, c.p, c.attempt.id, content, 'POST', store.save_path(c.attempt.id)); flags(saved)
    assert saved['version'] == 1 and io.commits == 1
    before = (len(io.drafts), len(io.commands), len(io.events), io.commits)
    def unavailable(*args, **kwargs): raise HTTPException(409, 'synthetic missing historical capture')
    monkeypatch.setattr(source, 'load', unavailable)
    read_back = api.detail(io, c.p, c.attempt.id); flags(read_back)
    assert read_back['value'] == {} and read_back['note'] == '未完成但已经保存的人工作业'
    assert read_back['source_available'] is False and read_back['saved'] is True
    assert read_back['baseline_value'] == c.baseline
    assert api.save(io, c.p, c.attempt.id, content, 'POST', store.save_path(c.attempt.id)) == saved
    recovered = store.original_request(io, c.p, identity('first'), hashlib.sha256(content).hexdigest())
    assert recovered['found'] is True and recovered['result'] == saved
    unknown = store.original_request(io, c.p, identity('unknown'), 'a' * 64)
    assert unknown['found'] is False and unknown['result'] is None
    assert (len(io.drafts), len(io.commands), len(io.events), io.commits) == before
    assert c.attempt.data == {'untouched_original': True}


def test_missing_original_with_no_saved_draft_is_blocked_not_a_fabricated_empty_success(monkeypatch):
    c, io, _, _ = draft_case(monkeypatch)
    def unavailable(*args, **kwargs): raise HTTPException(409, 'synthetic unavailable original')
    monkeypatch.setattr(source, 'load', unavailable)
    with pytest.raises(HTTPException) as error: api.detail(io, c.p, c.attempt.id)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        api.save(io, c.p, c.attempt.id, wire(body(value={})), 'POST', store.save_path(c.attempt.id))
    assert error.value.status_code == 409
    assert io.drafts == io.commands == io.events == [] and io.commits == 0


@pytest.mark.parametrize('fault', ['invalid_json', 'duplicate_key', 'bool_version', 'client_diagnostic', 'confirmation'])
def test_bad_save_body_is_422_before_source_or_rows_not_a_program_error_or_execution_request(monkeypatch, fault):
    c, io, _, calls = draft_case(monkeypatch); value = body(value={})
    if fault == 'bool_version': value['expected_version'] = False
    elif fault == 'client_diagnostic': value['diagnostics'] = {'can_execute': True}
    elif fault == 'confirmation': value['confirm'] = True
    content = wire(value)
    if fault == 'invalid_json': content = b'{'
    elif fault == 'duplicate_key': content = content[:-1] + b',"expected_version":0}'
    with pytest.raises(HTTPException) as error: api.save(io, c.p, c.attempt.id, content, 'POST', store.save_path(c.attempt.id))
    assert error.value.status_code == 422
    assert calls == [] and io.drafts == io.commands == io.events == [] and io.commits == 0


@pytest.mark.parametrize('fault', ['inactive_user', 'changed_membership', 'viewer'])
def test_current_identity_failure_precedes_any_original_capture_or_saved_draft_disclosure(monkeypatch, fault):
    c, io, _, calls = draft_case(monkeypatch)
    if fault == 'inactive_user': io.active = False
    elif fault == 'changed_membership': io.membership_role = 'viewer'
    else: c.p.role = io.membership_role = 'viewer'
    with pytest.raises(HTTPException) as error: api.detail(io, c.p, c.attempt.id)
    assert error.value.status_code == 403 and calls == []


def test_http_session_expiry_query_is_readonly_and_fails_before_inventory(monkeypatch):
    c = storage_scene(); c.p.session_id = identity('http-session')
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc); monkeypatch.setattr(m, 'now', lambda: now)
    expected = select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.id == c.p.user_id, m.User.active.is_(True), m.Membership.workspace_id == c.p.workspace_id,
        m.Membership.role == c.p.role).add_columns(m.AuthSession).join(m.AuthSession,
        m.AuthSession.user_id == m.User.id).where(m.AuthSession.id == c.p.session_id,
            m.AuthSession.revoked.is_(False), m.AuthSession.expires_at > now)
    class ExpiredIO:
        def execute(self, statement):
            same_statement(statement, expected); assert statement._for_update_arg is None
            return NS(first=lambda: None)
        def scalars(self, statement): raise AssertionError('Expired session cannot load private inventory')
    with pytest.raises(HTTPException) as error: api.detail(ExpiredIO(), c.p, c.attempt.id)
    assert error.value.status_code == 403


def test_router_has_only_read_and_exact_draft_save_and_rejects_bad_original_nonce(monkeypatch):
    operations = {(next(iter(route.methods)), route.path) for route in api.router.routes}
    assert operations == {('GET', api.PREFIX + '/candidates'), ('GET', api.PREFIX + '/requests/{request_id}'),
        ('GET', api.PREFIX + '/attempts/{attempt_id}'), ('POST', api.PREFIX + '/attempts/{attempt_id}/save')}
    c = storage_scene(); io = IO(c)
    request = Request({'type': 'http', 'method': 'GET', 'path': api.PREFIX + '/requests/bad',
        'query_string': b'expected_sha256=' + b'a' * 64, 'headers': []})
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.original_route('not-a-uuid', request, c.p, io))
    assert error.value.status_code == 422 and io.commits == 0
