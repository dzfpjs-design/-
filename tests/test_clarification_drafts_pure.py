"""Synthetic scoped draft and wrapper tests; --noconftest outside the repo."""
import copy
import json
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import setup_clarifications as api
from generic_agents.contracts import Contract, digest
from generic_agents import models as m

A, W, PARENT, OTHER, MODEL = [str(UUID(int=n)) for n in range(1, 6)]


class NormalStart(Contract):
    revision: int
    model_id: str
    material_ids: list[str]
    instruction: str
    contract_version: int
    request_id: UUID
    preview_hash: str
    consent: bool


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def deny(*args, **kwargs): pytest.fail('pure clarification checks forbid external effects')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(subprocess, 'Popen', deny)
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, 'create_engine', deny)


class DB:
    def __init__(self):
        self.items, self.queries, self.writes, self.commits = {}, [], [], []

    def add(self, row):
        if not row.id: row.id = str(UUID(int=100 + sum(map(len, self.items.values()))))
        if getattr(row, 'created_at', None) is None: row.created_at = m.now()
        if hasattr(row, 'version') and row.version is None: row.version = 1
        self.items.setdefault(type(row), []).append(row)
        self.writes.append(row)

    def flush(self): pass

    def execute(self, query):
        params = query.compile().params
        cls = next(c for c in [m.SetupClarificationDraft, m.SetupClarificationFollowup]
                   if c.__tablename__ == query.table.name)
        if cls is m.SetupClarificationDraft and not self.items.get(cls): self.add(cls(**params))

    def matching(self, query):
        self.queries.append(query)
        cls = query.column_descriptions[0]['entity']
        rows = self.items.get(cls, [])
        params = query.compile().params
        for key, value in params.items():
            column = key.rsplit('_', 1)[0]
            if column in {'id', 'actor_id', 'workspace_id', 'analysis_id', 'result_hash', 'request_id', 'status'}:
                rows = [r for r in rows if getattr(r, column, None) in value] if isinstance(value, list) else [r for r in rows if getattr(r, column, None) == value]
            elif cls is m.SetupAnalysis and isinstance(value, str) and key.startswith('param_'):
                try: UUID(value)
                except ValueError: continue
                rows = [r for r in rows if r.data.get('request', {}).get('request_id') == value]
        return rows

    def scalar(self, query):
        rows = self.matching(query)
        if not rows: return None
        return rows[0].id if query.column_descriptions[0]['name'] == 'id' else rows[0]

    def scalars(self, query): return SimpleNamespace(all=lambda: self.matching(query))

    def commit(self): self.commits.append(copy.deepcopy({c.__name__: [r.data for r in rows if hasattr(r, 'data')] for c, rows in self.items.items()}))


def fixture(monkeypatch, *, questions=None):
    db = DB(); p = SimpleNamespace(user_id=A, workspace_id=W, role='operator')
    request = SimpleNamespace(headers={'x-workspace-id': W})
    result = {'questions': questions or ['同一个问题？', '同一个问题？']}
    parent = m.SetupAnalysis(id=PARENT, workspace_id=W, version=2, status='review',
        data={'request': {'contract_version': 5, 'model_id': MODEL, 'materials': []},
              'result': result, 'result_hash': digest(result)})
    db.add(parent); rh = digest(result)
    config = SimpleNamespace(version=7, data={'instanceProfile': {'name': '原配置'}})
    legacy = SimpleNamespace(calls=[], config=config)
    def prepare(db, p, body):
        legacy.calls.append(body)
        payload = {'revision': body.revision, 'configuration_hash': digest(config.data),
            'model_id': body.model_id, 'materials': [], 'request_id': str(body.request_id),
            'instruction': body.instruction, 'contract_version': 5,
            'context': {'profile': {}, 'businesses': [], 'flows': {}, 'object_names': []},
            'chunks': [[]], 'source_snapshot': {'version': 'setup-source-snapshot-v1', 'materials': []}}
        payload['preview_hash'] = digest(payload); return payload
    legacy.PreviewRequest = lambda **kw: SimpleNamespace(**kw)
    legacy.StartRequest = NormalStart
    legacy.draft = lambda *a, **kw: config
    legacy.prepare = prepare
    legacy.model = lambda *a: SimpleNamespace(id=MODEL)
    legacy.input_summary = lambda req, cap: ({'token_bound': len(json.dumps(req['context']).encode())}, digest(req['context']))
    legacy.preview_budget = lambda *a, **kw: None
    legacy.scrub = lambda value: value.replace('secret-example', '[过滤]')
    legacy.checked_v3_request = lambda req, *a: None
    legacy.request_contract = lambda req: None
    legacy.view = lambda row: {'id': row.id, 'status': row.status, 'version': row.version}
    def enqueue(db, p, row):
        db.add(m.Job(workspace_id=W, kind='understand_setup', idempotency_key=row.id, data={'analysis_id': row.id, 'actor_id': A}))
        row.status = 'queued'
    legacy.enqueue = enqueue
    monkeypatch.setattr(api, 'legacy', lambda: legacy)
    monkeypatch.setattr(api, 'preparation_problems', lambda *a: [])
    return db, p, request, parent, rh, legacy


def initial(monkeypatch, **kwargs):
    db, p, request, parent, rh, old = fixture(monkeypatch, **kwargs)
    view = api.get_draft(A, PARENT, rh, request, p, db)
    value = view['defaults']; value['answers'][0].update(text='  中文回答\n🌲 secret-example  ', include=True)
    return db, p, request, parent, rh, old, value


def save(db, p, request, rh, value, rev=0, number=1, action='save'):
    body = api.DraftCommand(revision=rev, request_id=UUID(int=number), action=action, value=value)
    return api.save_draft(A, PARENT, rh, body, request, p, db)


def preview(db, p, request, rh, value, revision=1, number=50):
    body = api.PreviewCommand(revision=7, draft_revision=revision, value_hash=digest(value), request_id=UUID(int=number))
    return api.preview_followup(A, PARENT, rh, body, request, p, db)


def test_empty_get_no_write_and_duplicate_questions_keep_distinct_occurrences(monkeypatch):
    db, p, request, parent, rh, old = fixture(monkeypatch)
    writes = len(db.writes); view = api.get_draft(A, PARENT, rh, request, p, db)
    assert view['revision'] == 0 and view['value'] is None and len(db.writes) == writes and not db.commits
    source = view['source']; assert source['questions'][0]['id'] != source['questions'][1]['id']
    assert source['hash'] == digest({k: v for k, v in source.items() if k != 'hash'})
    for question in source['questions']:
        assert question['id'] == digest({'analysis_id': PARENT, 'result_hash': rh, 'ordinal': question['ordinal'], 'text': question['text']})
    assert not old.calls


@pytest.mark.parametrize('field', ['note', 'answer'])
@pytest.mark.parametrize('bad', ['bad\x00text', '\ud800'])
def test_bad_unicode_is_safe_422_for_note_and_every_answer(monkeypatch, field, bad):
    *_, value = initial(monkeypatch)
    if field == 'note': value['note'] = bad
    else: value['answers'][1]['text'] = bad
    with pytest.raises(HTTPException) as caught:
        api.DraftCommand(revision=0, request_id=UUID(int=1), action='save', value=value)
    assert caught.value.status_code == 422


def test_30_long_answers_save_without_instruction_or_provider(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch, questions=[str(n) for n in range(30)])
    for answer in value['answers']: answer.update(text='长' * 4000, include=True)
    reply = save(db, p, req, rh, value)
    assert reply['current']['value'] == value and reply['receipt']['accepted_revision'] == 1
    assert not old.calls and sum(len(a['text']) for a in value['answers']) == 120000
    bad = copy.deepcopy(value); bad['answers'].reverse()
    with pytest.raises(HTTPException): save(db, p, req, rh, bad, 1, 2)
    bad = copy.deepcopy(value); bad['answers'][0]['include'] = 'true'
    with pytest.raises(ValidationError): api.ClarificationValue.model_validate(bad)


def test_save_replay_conflict_backup_clear_and_stale_source_keep_human_work(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    first = save(db, p, req, rh, value)
    assert save(db, p, req, rh, value) == first
    with pytest.raises(HTTPException) as error: save(db, p, req, rh, value, 0, 2)
    assert error.value.detail['code'] == 'revision_conflict'
    empty = api.empty_value(first['current']['source'])
    cleared = save(db, p, req, rh, empty, 1, 3, 'clear')
    assert cleared['current']['backup']['value'] == value
    save(db, p, req, rh, empty, 2, 4, 'clear')
    restored = save(db, p, req, rh, value, 3, 5, 'restore')
    assert restored['current']['value'] == value
    parent.data['result'] = {'questions': ['后来修改']}; parent.data['result_hash'] = digest(parent.data['result'])
    later = copy.deepcopy(value); later['note'] = '原题变化仍保全'
    saved = save(db, p, req, rh, later, 4, 6)
    assert saved['current']['value'] == later and saved['current']['problems'][0]['kind'] == 'source'
    with pytest.raises(HTTPException): preview(db, p, req, rh, later, 5)


def test_preview_freezes_complete_projection_hashes_and_replay_before_fresh_state(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    save(db, p, req, rh, value)
    frozen = preview(db, p, req, rh, value)
    assert frozen['status'] == 'prepared' and frozen['child'] is None
    assert frozen['value'] == value and '[过滤]' in frozen['cloud_answers'][0]['answer']
    assert old.calls[0].instruction == '' and len(frozen['cloud_answers']) == 1
    expected = {k: frozen[k] for k in ['request_id','source','draft_revision','value_hash','value','cloud_answers','preview','body']}
    assert frozen['snapshot_hash'] == digest(expected)
    assert frozen['preview']['preview_hash'] == digest({k: v for k, v in frozen['preview'].items() if k != 'preview_hash'})
    assert frozen['preview']['clarification_source']['schema'] == 'clarification-input-v1'
    start = {'preview_hash': frozen['preview']['preview_hash'], 'consent': True}
    assert frozen['start_command_hash'] == digest({'kind':'clarification','actor_id':A,'workspace_id':W,
        'source_hash':frozen['source']['hash'],'request_id':frozen['request_id'],'body':start})
    parent.status = 'running'
    assert preview(db, p, req, rh, value) == frozen and len(old.calls) == 1
    assert not db.items.get(m.Job) and not db.items.get(m.CloudCall)


def test_real_child_atomic_creation_recovery_and_paid_barrier_receipt(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    save(db, p, req, rh, value); frozen = preview(db, p, req, rh, value)
    body = api.StartCommand(preview_hash=frozen['preview']['preview_hash'], consent=True)
    started = api.start_followup(A, PARENT, rh, UUID(int=50), body, req, p, db)
    assert started['status'] == 'started' and started['child']['status'] == 'queued'
    assert len(db.items[m.Job]) == 1 and len(db.items[m.SetupAnalysis]) == 2
    child = db.items[m.SetupAnalysis][-1]
    assert child.data['request'] == frozen['preview'] and child.data['start_command_hash'] == frozen['start_command_hash']
    parent.status = 'running'; old.config.version = 999
    assert api.start_followup(A, PARENT, rh, UUID(int=50), body, req, p, db) == started
    assert len(db.items[m.Job]) == 1
    p.role = 'viewer'
    receipt = api.get_start_receipt(A, UUID(int=50), req, p, db)
    assert receipt['analysis_id'] == child.id and receipt['command_hash'] == frozen['start_command_hash']
    assert receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
    assert 'request' not in receipt and 'value' not in receipt
    assert api.get_followup(A, PARENT, rh, UUID(int=50), req, p, db) == started
    with pytest.raises(HTTPException) as error: api.start_followup(A, PARENT, rh, UUID(int=50), body, req, p, db)
    assert error.value.status_code == 403


def test_scope_guard_precedes_every_query_and_private_lookup_is_double_bound(monkeypatch):
    db, p, req, parent, rh, old = fixture(monkeypatch)
    before = len(db.queries)
    with pytest.raises(HTTPException): api.get_draft(OTHER, PARENT, rh, req, p, db)
    assert len(db.queries) == before
    req.headers = {}
    with pytest.raises(HTTPException): api.get_followups(A, PARENT, rh, req, p, db)
    assert len(db.queries) == before


def test_missing_uniform_receipt_never_creates_and_source_new_constraints(monkeypatch):
    db, p, req, parent, rh, old = fixture(monkeypatch)
    before = len(db.writes)
    with pytest.raises(HTTPException) as error: api.get_start_receipt(A, UUID(int=80), req, p, db)
    assert error.value.status_code == 404 and len(db.writes) == before
    parent.status = 'running'
    with pytest.raises(HTTPException): api.get_draft(A, PARENT, rh, req, p, db)
    parent.status = 'review'; parent.data['request']['contract_version'] = 6
    with pytest.raises(HTTPException): api.get_draft(A, PARENT, rh, req, p, db)


def normal_root(db, nonce=50):
    request = {'revision': 7, 'model_id': MODEL, 'materials': [], 'instruction': '原普通理解要求',
               'contract_version': 5, 'request_id': str(UUID(int=nonce))}
    request['preview_hash'] = digest(request)
    body = {'revision': 7, 'model_id': MODEL, 'material_ids': [], 'instruction': request['instruction'],
            'contract_version': 5, 'request_id': str(UUID(int=nonce)), 'preview_hash': request['preview_hash'], 'consent': True}
    row = m.SetupAnalysis(id=OTHER, workspace_id=W, version=3, status='review', data={
        'request': request, 'consent': {'actor_id': A, 'preview_hash': request['preview_hash']},
        'start_command_hash': digest(body)})
    db.add(row); return row, body


def test_normal_barrier_uses_original_full_command_and_ignores_local_reuse_clones(monkeypatch):
    db, p, req, parent, rh, old = fixture(monkeypatch)
    original, body = normal_root(db)
    clone = m.SetupAnalysis(id=str(UUID(int=777)), workspace_id=W, version=1, status='review',
        data={**copy.deepcopy(original.data), 'reused_from': original.id, 'original_cloud_source': original.id})
    db.add(clone)
    receipt = api.get_start_receipt(A, UUID(int=50), req, p, db)
    assert receipt['kind'] == 'normal' and receipt['analysis_id'] == original.id
    assert receipt['command_hash'] == digest(body)
    bad = m.SetupAnalysis(id=str(UUID(int=778)), workspace_id=W, version=1, status='review', data=copy.deepcopy(original.data))
    db.add(bad)
    with pytest.raises(HTTPException) as error: api.get_start_receipt(A, UUID(int=50), req, p, db)
    assert error.value.detail['code'] == 'nonce_ambiguous'


def test_preview_nonce_is_workspace_global_across_normal_and_followup(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    save(db, p, req, rh, value); normal_root(db)
    with pytest.raises(HTTPException) as error: preview(db, p, req, rh, value)
    assert error.value.detail['code'] == 'request_conflict'
    assert not old.calls and not db.items.get(m.SetupClarificationFollowup)


def test_prepare_returned_new_material_version_cannot_replace_saved_selection(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    mid = str(UUID(int=800)); ref = {'id': mid, 'version': 1, 'hash': 'a' * 64}
    db.add(m.SetupMaterial(id=mid, workspace_id=W, version=1, sha256='a' * 64,
        name='真实字段格式', evidence_id=str(UUID(int=801)), archived=False, status='parsed', data={}))
    value['material_refs'] = [ref]; save(db, p, req, rh, value)
    prior = old.prepare
    def changed(*args):
        payload = prior(*args); payload['materials'] = [{**ref, 'version': 2}]; return payload
    old.prepare = changed
    with pytest.raises(HTTPException) as error: preview(db, p, req, rh, value)
    assert error.value.detail['code'] == 'selection_changed'
    assert not db.items.get(m.SetupClarificationFollowup)
    assert db.items[m.SetupClarificationDraft][0].data['value']['material_refs'] == [ref]


def test_budget_failure_preserves_private_work_and_frozen_replay_does_not_recheck(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    save(db, p, req, rh, value)
    def blocked(*args, **kwargs): raise HTTPException(409, '预算不足，未调用')
    old.preview_budget = blocked
    with pytest.raises(HTTPException): preview(db, p, req, rh, value)
    assert not db.items.get(m.SetupClarificationFollowup) and not db.items.get(m.Job)
    old.preview_budget = lambda *a, **kw: None
    frozen = preview(db, p, req, rh, value)
    old.preview_budget = blocked; parent.data['result'] = None
    assert preview(db, p, req, rh, value) == frozen
    assert api.get_followup(A, PARENT, rh, UUID(int=50), req, p, db) == frozen


def test_draft_receipt_retention_clear_restore_and_audit_never_include_human_text(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    for n in range(1, 67): save(db, p, req, rh, value, n - 1, 1000 - n)
    row = db.items[m.SetupClarificationDraft][0]
    assert len(row.data['receipts']) == 64 and row.data['receipt_order'][0] == str(UUID(int=997))
    assert row.data['receipt_order'][-1] == str(UUID(int=934))
    with pytest.raises(HTTPException) as error:
        api.get_draft_receipt(A, PARENT, rh, UUID(int=999), req, p, db)
    assert error.value.status_code == 404
    for audit in db.items[m.Audit]:
        assert '中文回答' not in str(audit.data) and '同一个问题' not in str(audit.data)


@pytest.mark.parametrize('mutation', ['marker', 'body', 'selected_material', 'start_hash'])
def test_resigned_snapshot_cross_binding_corruption_is_rejected(monkeypatch, mutation):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    save(db, p, req, rh, value); frozen = preview(db, p, req, rh, value)
    f = db.items[m.SetupClarificationFollowup][0]; snap = f.data['snapshot']
    if mutation == 'marker': snap['preview']['clarification_source']['request_id'] = str(UUID(int=90))
    elif mutation == 'body': snap['body']['instruction'] = '被换过的原文'
    elif mutation == 'selected_material': snap['preview']['materials'] = [{'id':OTHER,'version':1,'hash':'a'*64}]
    else:
        f.status = 'started'; f.child_id = OTHER
        f.data['start'] = {'body': {'preview_hash': snap['preview']['preview_hash'], 'consent': True},
                           'command_hash': 'f' * 64, 'analysis_id': OTHER}
    snap['preview']['preview_hash'] = digest({k: v for k, v in snap['preview'].items() if k != 'preview_hash'})
    f.data['snapshot_hash'] = digest(snap)
    with pytest.raises(HTTPException): api.get_followup(A, PARENT, rh, UUID(int=50), req, p, db)


def test_new_flow_locks_parent_before_workspace_and_recovered_child_needs_neither(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    save(db, p, req, rh, value); events = []
    original_lock, original_draft = api.lock_parent, old.draft
    def lock(*args): events.append('parent'); return original_lock(*args)
    monkeypatch.setattr(api, 'lock_parent', lock)
    old.draft = lambda *a, **kw: (events.append('workspace'), original_draft(*a, **kw))[1]
    frozen = preview(db, p, req, rh, value)
    assert events[:2] == ['parent', 'workspace']
    events.clear(); start = api.StartCommand(preview_hash=frozen['preview']['preview_hash'], consent=True)
    api.start_followup(A, PARENT, rh, UUID(int=50), start, req, p, db)
    assert events[:2] == ['parent', 'workspace']
    events.clear(); db.items[m.SetupAnalysis].remove(parent)
    api.start_followup(A, PARENT, rh, UUID(int=50), start, req, p, db)
    assert not events


def test_parent_row_revision_changes_do_not_change_question_binding(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    first = save(db, p, req, rh, value)
    parent.version += 99
    view = api.get_draft(A, PARENT, rh, req, p, db)
    assert view['source'] == first['current']['source'] and not view['problems']
    assert preview(db, p, req, rh, value)['source'] == view['source']


def test_blank_included_answer_saves_but_never_creates_preview(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    value['answers'][0]['text'] = ' \n\t '
    save(db, p, req, rh, value)
    with pytest.raises(HTTPException) as error: preview(db, p, req, rh, value)
    assert error.value.detail['code'] == 'answer_required'
    assert not old.calls and not db.items.get(m.SetupClarificationFollowup)


def test_other_actor_cannot_read_private_snapshot_or_use_workspace_global_nonce(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    save(db, p, req, rh, value); preview(db, p, req, rh, value)
    peer = SimpleNamespace(user_id=OTHER, workspace_id=W, role='operator')
    with pytest.raises(HTTPException) as error:
        api.get_followup(OTHER, PARENT, rh, UUID(int=50), req, peer, db)
    assert error.value.status_code == 404
    command = api.PreviewCommand(revision=7, draft_revision=1, value_hash=digest(value), request_id=UUID(int=50))
    with pytest.raises(HTTPException) as error:
        api.preview_followup(OTHER, PARENT, rh, command, req, peer, db)
    assert error.value.detail['code'] == 'request_conflict'
    assert len(db.items[m.SetupClarificationFollowup]) == 1


def test_invalid_parent_state_is_reported_while_existing_human_input_can_save(monkeypatch):
    db, p, req, parent, rh, old, value = initial(monkeypatch)
    save(db, p, req, rh, value)
    parent.data['request'] = None
    value['note'] = '父记录损坏时仍保全人稿'
    answer = save(db, p, req, rh, value, 1, 2)
    assert answer['current']['value']['note'] == value['note']
    assert answer['current']['problems'][0]['kind'] == 'source'
    with pytest.raises(ValidationError): api.StartCommand(preview_hash='a' * 64, consent=1)
