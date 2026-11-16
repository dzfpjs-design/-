"""Pure synthetic receipt/route tests. Run with --noconftest outside the repo."""
import copy
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import setup_understanding as api
from generic_agents.contracts import digest
from generic_agents.understanding_apply_receipts import (
    find_apply_receipt, normalized_apply_body, store_apply_receipt)


NONCE = UUID('10000000-0000-4000-8000-000000000001')
RESULT = {'domain': '合成核查', 'businesses': [], 'objects': [], 'questions': []}
BEFORE, AFTER = {'人工目标': '原稿'}, {'人工目标': '原稿', '新增业务': True}


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure receipt tests forbid DB, service, model and subprocess activity')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(api, 'cloud_json', forbidden)
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)


def fixture():
    principal = SimpleNamespace(workspace_id='workspace-a', user_id='actor-a', role='operator')
    row = SimpleNamespace(id='analysis-a', workspace_id=principal.workspace_id, status='review', version=2,
        data={'request': {'contract_version': 5, 'revision': 7, 'configuration_hash': digest(BEFORE)}, 'result': copy.deepcopy(RESULT), 'result_hash': digest(RESULT)})
    body = api.ApplyRequest(version=2, revision=7, request_id=NONCE,
        result_hash=digest(RESULT), configuration_hash=digest(BEFORE))
    return principal, row, body


def stored():
    principal, row, body = fixture()
    receipt = store_apply_receipt(row, principal, body, draft_id='draft-a',
        applied_revision=8, applied_configuration_hash=digest(AFTER))
    row.status, row.version = 'applied', 3
    return principal, row, body, receipt


def test_defaults_and_legacy_contract_are_explicit_and_stable():
    _, _, body = fixture()
    explicit = api.ApplyRequest(**{**body.model_dump(), 'names': [], 'object_names': [], 'accept_domain': False})
    assert normalized_apply_body(body) == normalized_apply_body(explicit)
    assert normalized_apply_body(body)['request_id'] == str(NONCE)
    assert api.ApplyRequest(version=1, revision=1).request_id is None
    assert api.LegacyApplySelection(version=1, revision=1).model_dump() == {
        'version': 1, 'revision': 1, 'names': [], 'object_names': [], 'accept_domain': False}
    with pytest.raises(ValidationError):
        api.LegacyApplySelection(version=1, revision=1, request_id=NONCE)
    for fields in [{'request_id': NONCE}, {'result_hash': 'a' * 64}, {'configuration_hash': 'b' * 64}]:
        with pytest.raises(ValidationError):
            api.ApplyRequest(version=1, revision=1, **fields)
    with pytest.raises(ValidationError):
        api.ApplyRequest(**{**body.model_dump(), 'names': ['重复', '重复']})


def test_receipt_is_actor_workspace_analysis_body_and_result_bound():
    principal, row, body, receipt = stored()
    assert find_apply_receipt(row, principal, body=body) == receipt
    assert find_apply_receipt(row, principal, request_id=NONCE) == receipt
    for change in [{'version': 3}, {'revision': 8}, {'names': ['其他选择']},
                   {'object_names': ['其他对象']}, {'accept_domain': True},
                   {'result_hash': 'e' * 64}, {'configuration_hash': 'f' * 64}]:
        with pytest.raises(HTTPException) as error:
            find_apply_receipt(row, principal, body=api.ApplyRequest(**{**body.model_dump(), **change}))
        assert error.value.status_code == 409
    for change in [{'user_id': 'actor-b'}, {'workspace_id': 'workspace-b'}]:
        with pytest.raises(HTTPException):
            find_apply_receipt(row, SimpleNamespace(**{**vars(principal), **change}), request_id=NONCE)
    changed = copy.deepcopy(row); changed.id = 'analysis-b'
    with pytest.raises(HTTPException):
        find_apply_receipt(changed, principal, request_id=NONCE)
    changed = copy.deepcopy(row); changed.data['result']['domain'] = '损坏建议'
    with pytest.raises(HTTPException):
        find_apply_receipt(changed, principal, request_id=NONCE)
    changed = copy.deepcopy(row); changed.data['apply_commands'][str(NONCE)]['receipt']['applied_revision'] = 9
    with pytest.raises(HTTPException):
        find_apply_receipt(changed, principal, request_id=NONCE)


def test_receipt_contains_only_command_metadata_not_configuration_or_raw_model_content():
    _, row, _, _ = stored()
    saved = row.data['apply_commands'][str(NONCE)]
    assert set(saved) == {'scope', 'body', 'receipt'}
    assert '人工目标' not in str(saved)
    assert 'raw_response' not in str(saved)
    assert 'before_configuration' not in str(saved)


@pytest.mark.parametrize('malformed', ['list-index', 'null-index', 'entry-list', 'entry-null', 'body-list', 'body-missing', 'body-revision', 'body-selection', 'guard-list'])
def test_malformed_command_metadata_returns_conflict_not_crash(malformed):
    principal, row, _, _ = stored()
    if malformed == 'list-index': row.data['apply_commands'] = []
    elif malformed == 'null-index': row.data['apply_commands'] = None
    elif malformed == 'entry-list': row.data['apply_commands'][str(NONCE)] = []
    elif malformed == 'entry-null': row.data['apply_commands'][str(NONCE)] = None
    elif malformed == 'guard-list': row.data['local_reuse_guard'] = []
    else:
        original = row.data['apply_commands'][str(NONCE)]['body']
        if malformed == 'body-list': row.data['apply_commands'][str(NONCE)]['body'] = []
        elif malformed == 'body-missing': del original['version']
        elif malformed == 'body-revision': original['revision'] = 'seven'
        elif malformed == 'body-selection': original['names'] = [{}]
    with pytest.raises(HTTPException) as error:
        find_apply_receipt(row, principal, request_id=NONCE)
    assert error.value.status_code == 409


def test_replay_precedes_stale_version_status_draft_and_paid_validation(monkeypatch):
    principal, row, body, receipt = stored()
    calls = []
    db = SimpleNamespace(commit=lambda: calls.append('commit'))
    monkeypatch.setattr(api, 'scoped', lambda *args: row)
    monkeypatch.setattr(api, 'require', lambda *args: calls.append('role'))
    def forbidden(*args, **kwargs):
        pytest.fail('a saved nonce must not recheck or regenerate its paid suggestion')
    for name in ['check_version', 'current', 'validate_result', 'apply_configuration', 'model', 'generation_contract']:
        monkeypatch.setattr(api, name, forbidden)
    monkeypatch.setattr(api, 'apply_receipt_reply', lambda db, p, found, saved: {'receipt': saved, 'current_matches': False})
    row.status, row.version = 'undone', 19
    assert api.apply_proposal(row.id, body, principal, db)['receipt'] == receipt
    assert calls == ['role', 'commit']


@pytest.mark.parametrize('changed', ['current', 'later-human', 'undone'])
def test_reply_attaches_snapshot_only_when_exact_applied_revision_is_still_current(monkeypatch, changed):
    principal, row, _, receipt = stored()
    config = SimpleNamespace(id='draft-a', version=8, data=copy.deepcopy(AFTER))
    if changed == 'later-human':
        config.version, config.data = 9, {'人工目标': '后来的新稿'}
    if changed == 'undone':
        row.status = 'undone'
    monkeypatch.setattr(api, 'scoped', lambda *args: config)
    monkeypatch.setattr(api, 'view', lambda found: {'id': found.id, 'status': found.status})
    queries = []
    def scalar(query):
        queries.append(query)
        return SimpleNamespace(data=copy.deepcopy(AFTER))
    result = api.apply_receipt_reply(SimpleNamespace(scalar=scalar), principal, row, receipt)
    assert result['current_matches'] == (changed == 'current')
    assert ('configuration_json' in result) == (changed == 'current')
    assert len(queries) == (1 if changed == 'current' else 0)


def test_read_only_actor_can_recover_and_corrupt_snapshot_is_rejected(monkeypatch):
    principal, row, _, receipt = stored(); principal.role = 'viewer'
    reply = api.apply_receipt_reply
    monkeypatch.setattr(api, 'scoped', lambda *args: row)
    monkeypatch.setattr(api, 'require', lambda *args: pytest.fail('receipt GET must allow actor read-only recovery'))
    monkeypatch.setattr(api, 'apply_receipt_reply', lambda db, p, found, saved: {'receipt': saved})
    assert api.get_apply_receipt(row.id, NONCE, principal, object())['receipt'] == receipt
    monkeypatch.setattr(api, 'scoped', lambda *args: SimpleNamespace(version=8, data=AFTER))
    monkeypatch.setattr(api, 'view', lambda found: {})
    with pytest.raises(HTTPException) as error:
        reply(SimpleNamespace(scalar=lambda q: SimpleNamespace(data=BEFORE)), principal, row, receipt)
    assert error.value.status_code == 409


def test_first_apply_stores_receipt_in_the_same_commit_and_keeps_legacy_response(monkeypatch):
    for with_nonce in [True, False]:
        principal, row, body = fixture()
        if not with_nonce:
            body = api.ApplyRequest(version=2, revision=7, accept_domain=True)
        else:
            body = api.ApplyRequest(**{**body.model_dump(), 'accept_domain': True})
        config = SimpleNamespace(id='draft-a', version=7, data=copy.deepcopy(BEFORE))
        commits = []
        def commit():
            commits.append(copy.deepcopy(row.data))
        # This explicit unaliased receipt fixture has no normalization/reuse
        # records. Real proof and corruption behavior is covered by PG tests.
        db = SimpleNamespace(execute=lambda q: None, add=lambda row: None, commit=commit,
                             scalars=lambda q: [], scalar=lambda q: None)
        monkeypatch.setattr(api, 'require', lambda *args: None)
        monkeypatch.setattr(api, 'scoped', lambda *args: row)
        monkeypatch.setattr(api, 'check_version', lambda *args: None)
        monkeypatch.setattr(api, 'current', lambda *args: (row, config, None))
        monkeypatch.setattr(api, 'request_contract', lambda req: api.ResultV5)
        monkeypatch.setattr(api, 'validate_result', lambda *args, **kwargs: None)
        monkeypatch.setattr(api, 'require_scoped_updates', lambda *args: None)
        monkeypatch.setattr(api, 'apply_configuration', lambda *args, **kwargs: copy.deepcopy(AFTER))
        monkeypatch.setattr(api, 'apply_objects', lambda value, *args, **kwargs: value)
        monkeypatch.setattr(api, 'validate_configuration', lambda *args: None)
        monkeypatch.setattr(api, 'audit', lambda *args: None)
        monkeypatch.setattr(api, 'view', lambda found: {'id': found.id, 'status': found.status})
        row.data['request']['chunks'] = [[]]
        answer = api.apply_proposal(row.id, body, principal, db)
        assert len(commits) == 1
        assert ('receipt' in answer) == with_nonce
        assert ('apply_commands' in commits[0]) == with_nonce
        if with_nonce:
            assert answer['receipt']['applied_revision'] == 8
            assert answer['receipt']['configuration_hash'] == digest(BEFORE)
            assert answer['receipt']['applied_configuration_hash'] == digest(AFTER)
            assert answer['receipt'] == commits[0]['apply_commands'][str(NONCE)]['receipt']
