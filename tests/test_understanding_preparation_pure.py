"""Synthetic preparation/CAS checks; run outside repo with --noconftest."""
import copy
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import understanding_preparation as api
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, SetupMaterial, SetupUnderstandingPreparation

ACTOR = '10000000-0000-4000-8000-000000000001'
WORKSPACE = '20000000-0000-4000-8000-000000000001'
OTHER = '30000000-0000-4000-8000-000000000001'
MATERIAL = '40000000-0000-4000-8000-000000000001'
MODEL = '50000000-0000-4000-8000-000000000001'
EMPTY = {'note': '', 'mode': 'understand', 'model_id': '', 'material_refs': []}


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure preparation checks prohibit external effects')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)


def principal(**changes):
    return SimpleNamespace(**{'user_id': ACTOR, 'workspace_id': WORKSPACE,
                             'role': 'operator', **changes})


def request(workspace=WORKSPACE):
    return SimpleNamespace(headers={'x-workspace-id': workspace} if workspace else {})


def body(revision=0, number=1, action='save', **value):
    return api.SavePreparation(revision=revision,
        request_id=UUID(int=number), action=action, value={**EMPTY, **value})


class DB:
    def __init__(self, row=None, materials=(), models=()):
        self.row, self.materials, self.models = row, list(materials), list(models)
        self.queries, self.executed, self.added, self.commits = [], [], [], []

    def scalar(self, query):
        self.queries.append(query)
        return self.row

    def scalars(self, query):
        self.queries.append(query)
        entity = query.column_descriptions[0]['entity']
        rows = self.materials if entity is SetupMaterial else self.models
        return SimpleNamespace(all=lambda: rows)

    def execute(self, query):
        self.executed.append(query)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits.append(copy.deepcopy(self.row.data))


def setup():
    row = SetupUnderstandingPreparation(id=OTHER, workspace_id=WORKSPACE,
        actor_id=ACTOR, version=0, data={})
    return principal(), row, DB(row)


@pytest.mark.parametrize('change', [
    {'note': 'x' * 4001}, {'note': 'bad\x00text'}, {'note': '\ud800'},
    {'note': 123}, {'mode': 'other'}, {'model_id': 'not-a-uuid'},
    {'material_refs': [{'id': MATERIAL, 'version': True, 'hash': 'a' * 64}]},
    {'material_refs': [{'id': MATERIAL, 'version': 0, 'hash': 'a' * 64}]},
    {'material_refs': [{'id': MATERIAL, 'version': 1, 'hash': 'z' * 64}]},
    {'material_refs': [{'id': MATERIAL, 'version': 1, 'hash': 'a' * 64}] * 2},
    {'actor_id': ACTOR},
])
def test_strict_value_rejects_unsafe_or_ambiguous_input(change):
    with pytest.raises(ValidationError):
        api.PreparationValue(**{**EMPTY, **change})


def test_normalization_preserves_human_text_and_clear_is_explicit():
    note = '  原文\n\t不裁剪  '
    assert body(note=note).value.note == note
    assert body(model_id=MODEL).model_dump(mode='json')['value']['model_id'] == MODEL
    with pytest.raises(ValidationError):
        body(action='clear', note=note)
    with pytest.raises(ValidationError):
        api.SavePreparation(revision=True, request_id=UUID(int=1), action='save', value=EMPTY)


@pytest.mark.parametrize('changes', [
    {'value': {**EMPTY, 'note': '\ud800'}}, {'action': '\ud800'},
    {'request_id': '\ud800'}, {'value': {**EMPTY, 'model_id': 'bad\x00uuid'}},
])
def test_http_body_invalid_unicode_and_nul_return_safe_422(changes):
    raw = {'revision': 0, 'request_id': str(UUID(int=1)), 'action': 'save', 'value': EMPTY, **changes}
    with pytest.raises(HTTPException) as caught:
        api.SavePreparation.model_validate(raw)
    assert caught.value.status_code == 422
    caught.value.detail.encode('utf-8')


@pytest.mark.parametrize('actor,workspace,p', [
    (OTHER, WORKSPACE, principal()), (ACTOR, None, principal()),
    (ACTOR, OTHER, principal()), (ACTOR, WORKSPACE, principal(user_id=OTHER)),
])
def test_scope_rejected_before_any_read_or_write(actor, workspace, p):
    db = DB()
    for action in [lambda: api.get_preparation(actor, request(workspace), p, db),
                   lambda: api.put_preparation(actor, body(), request(workspace), p, db),
                   lambda: api.get_preparation_receipt(actor, UUID(int=1), request(workspace), p, db)]:
        with pytest.raises(HTTPException) as caught:
            action()
        assert caught.value.status_code == 403
    assert not (db.queries or db.executed or db.added or db.commits)


def test_empty_get_and_missing_receipt_never_write_even_for_viewer():
    db, p = DB(), principal(role='viewer')
    answer = api.get_preparation(ACTOR, request(), p, db)
    assert answer == {'actor_id': ACTOR, 'workspace_id': WORKSPACE, 'revision': 0,
        'value': None, 'value_hash': None, 'updated_at': None, 'backup': None, 'problems': []}
    with pytest.raises(HTTPException) as caught:
        api.get_preparation_receipt(ACTOR, UUID(int=1), request(), p, db)
    assert caught.value.status_code == 404
    assert not (db.executed or db.added or db.commits)
    for query in db.queries:
        rendered = str(query)
        assert 'actor_id =' in rendered and 'workspace_id =' in rendered


def test_first_save_receipt_audit_and_snapshot_are_one_commit():
    p, row, db = setup()
    command = body(note='人类输入')
    answer = api.put_preparation(ACTOR, command, request(), p, db)
    receipt = answer['receipt']
    assert answer['state'] == 'saved' and answer['current_matches'] is True
    assert row.version == receipt['accepted_revision'] == 1
    assert receipt['request_hash'] == digest({'scope': {'actor_id': ACTOR,
        'workspace_id': WORKSPACE}, 'body': command.model_dump(mode='json')})
    assert receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
    assert len(db.commits) == len(db.added) == 1 and isinstance(db.added[0], Audit)
    assert '人类输入' not in str(db.added[0].data)
    assert db.commits[0]['value']['note'] == '人类输入'
    assert '人类输入' not in str(db.commits[0]['receipts'])


def test_same_nonce_recovery_precedes_stale_cas_and_never_reaudits():
    p, row, db = setup()
    original = body(note='原稿')
    saved = api.put_preparation(ACTOR, original, request(), p, db)
    api.put_preparation(ACTOR, body(1, 2, note='后稿'), request(), p, db)
    repeated = api.put_preparation(ACTOR, original, request(), p, db)
    assert repeated['receipt'] == saved['receipt']
    assert repeated['current']['value']['note'] == '后稿'
    assert repeated['current_matches'] is False and row.version == 2
    assert len(db.added) == 2
    recovered = api.get_preparation_receipt(ACTOR, original.request_id, request(), principal(role='viewer'), db)
    assert recovered == repeated


def test_nonce_changed_body_and_stale_new_nonce_keep_server_value():
    p, row, db = setup()
    api.put_preparation(ACTOR, body(note='已保存'), request(), p, db)
    for command, code in [(body(note='重签'), 'request_conflict'),
                          (body(number=2, note='另页原稿'), 'revision_conflict'),
                          (body(number=3, note='已保存'), 'revision_conflict')]:
        before = copy.deepcopy(row.data)
        with pytest.raises(HTTPException) as caught:
            api.put_preparation(ACTOR, command, request(), p, db)
        assert caught.value.status_code == 409 and caught.value.detail['code'] == code
        assert caught.value.detail['current']['value']['note'] == '已保存'
        assert row.data == before and row.version == 1


def test_clear_backup_autosave_restore_and_repeat_empty_clear():
    p, row, db = setup()
    api.put_preparation(ACTOR, body(note='旧稿'), request(), p, db)
    cleared = api.put_preparation(ACTOR, body(1, 2, 'clear'), request(), p, db)
    backup = cleared['current']['backup']
    assert backup['value']['note'] == '旧稿' and backup['revision'] == 1
    api.put_preparation(ACTOR, body(2, 3, 'clear'), request(), p, db)
    assert row.data['backup'] == backup
    api.put_preparation(ACTOR, body(3, 4, note='新页'), request(), p, db)
    assert row.data['backup'] == backup
    restored = api.put_preparation(ACTOR, body(4, 5, 'restore', **backup['value']), request(), p, db)
    assert restored['current']['value']['note'] == '旧稿'
    assert restored['current']['backup']['value']['note'] == '新页'
    with pytest.raises(HTTPException):
        api.put_preparation(ACTOR, body(5, 6, 'restore', note='非备份'), request(), p, db)


def test_lost_restore_response_recovers_before_changed_backup_or_newer_save():
    p, row, db = setup()
    api.put_preparation(ACTOR, body(note='原稿'), request(), p, db)
    api.put_preparation(ACTOR, body(1, 2, 'replace', note='替换'), request(), p, db)
    command = body(2, 3, 'restore', note='原稿')
    restored = api.put_preparation(ACTOR, command, request(), p, db)
    before = len(db.commits), len(db.added), copy.deepcopy(row.data)
    assert api.put_preparation(ACTOR, command, request(), p, db) == restored
    assert (len(db.commits), len(db.added), row.data) == before
    api.put_preparation(ACTOR, body(3, 4, note='后来'), request(), p, db)
    replay = api.put_preparation(ACTOR, command, request(), p, db)
    assert replay['receipt'] == restored['receipt'] and replay['current_matches'] is False


def test_restoring_previously_saved_missing_material_does_not_lose_human_work():
    p, row, db = setup()
    ref = {'id': MATERIAL, 'version': 1, 'hash': 'a' * 64}
    db.materials = [SimpleNamespace(id=MATERIAL, workspace_id=WORKSPACE, version=1,
        sha256='a' * 64, archived=False, status='parsed')]
    api.put_preparation(ACTOR, body(note='原稿', material_refs=[ref]), request(), p, db)
    api.put_preparation(ACTOR, body(1, 2, 'clear'), request(), p, db)
    db.materials = []
    restored = api.put_preparation(ACTOR, body(2, 3, 'restore', note='原稿', material_refs=[ref]), request(), p, db)
    assert restored['current']['value']['material_refs'] == [ref]
    assert restored['current']['problems'][0]['code'] == 'missing'


def test_material_changes_preserve_original_ref_and_notes_not_silently_upgrade():
    p, row, db = setup()
    ref = {'id': MATERIAL, 'version': 1, 'hash': 'a' * 64}
    db.materials = [SimpleNamespace(id=MATERIAL, workspace_id=WORKSPACE, version=2,
        sha256='b' * 64, archived=True, status='failed')]
    answer = api.put_preparation(ACTOR, body(note='留住输入', material_refs=[ref], model_id=MODEL), request(), p, db)
    assert answer['current']['value']['material_refs'] == [ref]
    assert {p['code'] for p in answer['current']['problems']} == {'changed', 'archived', 'not_ready', 'unavailable'}
    db.materials = []
    answer = api.put_preparation(ACTOR, body(1, 2, note='继续写', material_refs=[ref]), request(), p, db)
    assert answer['current']['value']['material_refs'] == [ref]
    assert answer['current']['problems'][0]['code'] == 'missing'
    with pytest.raises(HTTPException) as caught:
        api.put_preparation(ACTOR, body(2, 3, material_refs=[{**ref, 'id': OTHER}]), request(), p, db)
    assert caught.value.status_code == 404


def test_viewer_cannot_save_and_query_has_scope_and_row_lock():
    p, row, db = setup()
    with pytest.raises(HTTPException) as caught:
        api.put_preparation(ACTOR, body(), request(), principal(role='viewer'), db)
    assert caught.value.status_code == 403 and not db.queries and not db.executed
    api.put_preparation(ACTOR, body(), request(), p, db)
    locked = [q for q in db.queries if 'FOR UPDATE' in str(q)]
    assert locked and all('actor_id =' in str(q) and 'workspace_id =' in str(q) for q in locked)


def test_receipt_retention_uses_order_not_json_key_sort():
    p, row, db = setup()
    for n in range(1, 67):
        api.put_preparation(ACTOR, body(n - 1, 1000 - n, note=str(n)), request(), p, db)
    assert len(row.data['receipt_order']) == len(row.data['receipts']) == 64
    assert row.data['receipt_order'][0] == str(UUID(int=997))
    assert row.data['receipt_order'][-1] == str(UUID(int=934))
    with pytest.raises(HTTPException) as caught:
        api.get_preparation_receipt(ACTOR, UUID(int=999), request(), p, db)
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        api.put_preparation(ACTOR, body(0, 999, note='1'), request(), p, db)
    assert caught.value.detail['code'] == 'revision_conflict' and row.version == 66


@pytest.mark.parametrize('field', ['value_hash', 'receipt_hash', 'owner'])
def test_corrupt_state_or_receipt_never_mutates(field):
    p, row, db = setup()
    api.put_preparation(ACTOR, body(note='原稿'), request(), p, db)
    if field == 'value_hash': row.data['value_hash'] = 'f' * 64
    elif field == 'owner': row.actor_id = OTHER
    else: row.data['receipts'][str(UUID(int=1))]['receipt_hash'] = 'f' * 64
    before = copy.deepcopy(row.data)
    with pytest.raises(HTTPException):
        api.put_preparation(ACTOR, body(1, 2, note='新稿'), request(), p, db)
    assert row.data == before and row.version == 1
