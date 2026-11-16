"""Typed-edit contract and command behavior; no SQL/model/browser claims."""
import ast
import copy
import json
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import setup_understanding as u, setup_edits as api
from generic_agents.setup_edit_operations import EditResult, project, review_edits
from generic_agents.contracts import digest

ROOT = Path(__file__).resolve().parents[1]
NONCE = UUID('e0000000-0000-4000-8000-000000000001')
OP_NONCE = UUID('e0000000-0000-4000-8000-000000000002')


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure edit lifecycle tests forbid databases, models and child services')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(u, 'cloud_json', forbidden)


def configuration():
    return {'version': 1, 'resources': [], 'connections': [], 'materials': [],
        'instanceProfile': {'name': '合成核对', 'domain': '数量核对', 'goal': '只改指定角色'},
        'businesses': ['数量复核'], 'flows': {'数量复核': {'trigger': '人工发起', 'output': '已核对结果',
            'steps': [
                {'id': 'private-auto-uuid', 'name': '汇总', 'purpose': '计算数量', 'agent': '数据管理智能体',
                 'role': '', 'output': '均值', 'nextId': 'private-grant-uuid', 'elseId': 'end', 'condition': '',
                 'execution': {'capability_id': 'private-cap-uuid', 'automation_action': 'data',
                               'arguments': {'field': '数量', 'operation': 'mean'}, 'bindings': {}}},
                {'id': 'private-grant-uuid', 'name': '复核', 'purpose': '人工核对', 'agent': '人工确认',
                 'role': '复核员', 'output': '复核结果', 'nextId': 'private-role-uuid', 'elseId': 'end', 'condition': '',
                 'planning_tools': [{'capability_id': 'private-cap-uuid', 'allow_external': False}]},
                {'id': 'private-role-uuid', 'name': '确认', 'purpose': '确认结果', 'agent': '人工确认',
                 'role': '原负责人', 'output': '确认结果', 'nextId': 'end', 'elseId': 'end', 'condition': ''}]}}}


def proposal():
    return {'operations': [{'id': 'edit_1', 'type': 'set_step', 'target': 's_3',
        'field': 'role', 'value': '新负责人', 'dependencies': [], 'citations': [],
        'inference_reason': '用户明确要求只修改第三步负责人'}], 'questions': []}


def prepared(monkeypatch, config=None):
    config = config or configuration()
    draft = SimpleNamespace(id='draft', version=7, data=config)
    cap = SimpleNamespace(id='model', content_hash='model-hash', data={
        'execution': {'provider': 'qwen', 'model': 'qwen-plus', 'max_tokens': 2048},
        'data_policy': {'redact_fields': []}})
    monkeypatch.setattr(u, 'draft', lambda *a: draft)
    monkeypatch.setattr(u, 'model', lambda *a: cap)
    monkeypatch.setattr(u, 'preview_budget', lambda *a, **k: None)
    body = u.PreviewRequest(revision=7, model_id='model', material_ids=[], instruction='只改第三步负责人',
                            contract_version=6, request_id=NONCE)
    return u.prepare(object(), SimpleNamespace(workspace_id='workspace'), body), draft, cap, body


def test_version6_requires_nonce_and_unknown_version_is_blocked():
    for version in (6, 7):
        with pytest.raises(ValidationError):
            u.PreviewRequest(revision=1, model_id='m', material_ids=[], contract_version=version)
    with pytest.raises(HTTPException):
        u.request_contract({'contract_version': 7, 'contract_hash': digest(u.Result.model_json_schema())})


def test_edit_preview_has_frozen_local_handles_but_cloud_gets_no_uuid_or_grant(monkeypatch):
    req, _, cap, _ = prepared(monkeypatch)
    assert u.request_contract(req) is EditResult
    assert req['generation_contract']['version'] == 'setup-edits-v6'
    assert req['edit_scope_hash'] == digest(req['edit_handles'])
    assert req['preview_hash'] == digest({k: v for k, v in req.items() if k != 'preview_hash'})
    text = json.dumps(json.loads(u.messages_for(req, None, 0)[0]['content'])['context'], ensure_ascii=False)
    for private in ('private-auto-uuid', 'private-cap-uuid', 'planning_tools', 'capability_id', 'edit_handles'):
        assert private not in text
    assert 's_3' in text
    u.checked_v3_request(req, cap, req['source_snapshot'])
    changed = copy.deepcopy(req)
    changed['edit_scope_hash'] = '0' * 64
    changed['preview_hash'] = digest({k: v for k, v in changed.items() if k != 'preview_hash'})
    with pytest.raises(HTTPException):
        u.checked_v3_request(changed, cap)


def test_public_preview_hides_server_uuid_mapping(monkeypatch):
    req, _, _, body = prepared(monkeypatch)
    db = SimpleNamespace(commit=lambda: None)
    monkeypatch.setattr(u, 'prepare', lambda *a: req)
    public = u.preview_request(body, SimpleNamespace(role='admin'), db)
    assert 'edit_handles' not in public and public['edit_scope_hash'] == req['edit_scope_hash']


def test_legacy_schema_rules_and_fixed_generation_functions_are_unchanged():
    original = ROOT / '.agentic-engineering/history/2026-10-16-before-scoped-edits/backend/src/generic_agents/setup_understanding.py'
    before = ast.parse(original.read_text())
    current = ast.parse((ROOT / 'src/generic_agents/setup_understanding.py').read_text())
    protected = {'Citation', 'Step', 'Business', 'Result', 'ResultV2', 'ResultV5',
                 'grounded_contract', 'full_report_contract', 'relation_report_contract'}
    old = {node.name: ast.dump(node, include_attributes=False) for node in before.body if getattr(node, 'name', None) in protected}
    new = {node.name: ast.dump(node, include_attributes=False) for node in current.body if getattr(node, 'name', None) in protected}
    assert old == new


class FakeDB:
    def __init__(self):
        self.rows, self.commits = [], 0
    def add(self, value):
        self.rows.append(value)
    def execute(self, value):
        self.rows.append(value)
    def commit(self):
        self.commits += 1


@pytest.fixture
def command_case(monkeypatch):
    config = configuration()
    _, handles = project(config, [], scrub_text=lambda value: value)
    result = proposal()
    row = SimpleNamespace(id='analysis', version=4, status='review', created_at=datetime.now(timezone.utc),
        data={'request': {'contract_version': 6, 'provider': 'qwen', 'model': 'qwen-plus', 'revision': 7,
            'materials': [], 'chunks': [[]], 'context': {}, 'request_id': str(NONCE), 'edit_handles': handles},
            'result': result, 'result_hash': digest(result),
            'edit_review': review_edits(config, result, handles, str(NONCE), [])})
    draft = SimpleNamespace(id='draft', version=7, data=copy.deepcopy(config))
    db = FakeDB()
    p = SimpleNamespace(user_id='operator', role='admin', workspace_id='workspace')
    monkeypatch.setattr(api, 'scoped', lambda *a: row)
    monkeypatch.setattr(api, 'draft', lambda *a: draft)
    monkeypatch.setattr(api, 'audit', lambda *a, **kw: None)
    monkeypatch.setattr(u, 'current', lambda *a: (row, draft, None))
    return row, draft, db, p, config


def review(case):
    row, _, db, p, _ = case
    body = api.EditReview(version=row.version, request_id=OP_NONCE,
                          result_hash=row.data['result_hash'], operation_ids=['edit_1'])
    return api.review_edits(row.id, body, p, db), body


def apply(case):
    row, draft, db, p, _ = case
    body = api.EditApply(version=row.version, revision=draft.version,
        request_id=UUID('e0000000-0000-4000-8000-000000000003'),
        result_hash=row.data['result_hash'], operation_ids=['edit_1'])
    return api.apply_edits(row.id, body, p, db), body


def test_review_is_durable_command_and_replay_does_not_repeat(command_case):
    row, draft, db, p, _ = command_case
    saved, body = review(command_case)
    assert saved['review_selection']['operation_ids'] == ['edit_1']
    assert saved['edit_receipts'][-1]['action'] == 'review'
    version = row.version
    assert api.review_edits(row.id, body, p, db)['version'] == version
    assert len(row.data['edit_receipts']) == 1 and draft.version == 7


def test_apply_requires_saved_selection_and_preserves_unmodified_bindings(command_case):
    row, draft, _, _, before = command_case
    with pytest.raises(HTTPException) as failed:
        apply(command_case)
    assert failed.value.status_code == 409 and draft.data == before
    review(command_case)
    saved, _ = apply(command_case)
    steps = saved['configuration']['flows']['数量复核']['steps']
    old = before['flows']['数量复核']['steps']
    assert steps[:2] == old[:2]
    assert [s['id'] for s in steps] == [s['id'] for s in old]
    assert steps[2] == {**old[2], 'role': '新负责人'}
    assert draft.version == 8 and saved['receipt']['status'] == 'applied'


def test_lost_apply_response_replay_returns_latest_draft_without_mutation(command_case, monkeypatch):
    row, draft, db, p, _ = command_case
    review(command_case)
    _, body = apply(command_case)
    draft.data = {**draft.data, 'later_human_note': '人工后续编辑'}
    draft.version += 1
    later = copy.deepcopy(draft.data)
    monkeypatch.setattr(u, 'current', lambda *a: pytest.fail('replay must not reapply old edit'))
    recovered = api.apply_edits(row.id, body, p, db)
    assert recovered['configuration'] == later and draft.version == 9
    assert len(row.data['edit_receipts']) == 2
    changed = body.model_copy(update={'operation_ids': []})
    with pytest.raises(HTTPException) as failure:
        api.apply_edits(row.id, changed, p, db)
    assert failure.value.status_code == 409


def test_undo_does_not_overwrite_later_human_edit(command_case):
    row, draft, db, p, _ = command_case
    review(command_case)
    apply(command_case)
    draft.data = {**draft.data, 'later': '保留'}
    draft.version += 1
    body = api.EditUndo(version=row.version, revision=draft.version, result_hash=row.data['result_hash'],
        request_id=UUID('e0000000-0000-4000-8000-000000000004'))
    with pytest.raises(HTTPException) as failure:
        api.undo_edits(row.id, body, p, db)
    assert failure.value.status_code == 409 and draft.data['later'] == '保留'


def test_undo_restores_original_ids_and_bindings_and_replay_is_safe(command_case):
    row, draft, db, p, before = command_case
    review(command_case)
    apply(command_case)
    body = api.EditUndo(version=row.version, revision=draft.version, result_hash=row.data['result_hash'],
        request_id=UUID('e0000000-0000-4000-8000-000000000004'))
    restored = api.undo_edits(row.id, body, p, db)
    assert restored['configuration']['flows'] == before['flows']
    assert row.status == 'undone' and draft.version == 9
    assert api.undo_edits(row.id, body, p, db)['revision'] == 9
    assert len(row.data['edit_receipts']) == 3


def test_unknown_edit_fields_and_duplicate_operations_are_rejected():
    with pytest.raises(ValidationError):
        api.EditReview(version=1, result_hash='0' * 64, request_id=OP_NONCE,
                       operation_ids=['edit_1', 'edit_1'])
    with pytest.raises(ValidationError):
        api.EditApply(version=1, revision=1, result_hash='0' * 64, request_id=OP_NONCE,
                      operation_ids=[], configuration={'execution': 'forged'})


def test_version6_failed_without_raw_cannot_use_legacy_paid_retry(command_case, monkeypatch):
    row, _, db, p, _ = command_case
    row.status = 'failed'
    monkeypatch.setattr(u, 'scoped', lambda *a: row)
    monkeypatch.setattr(u, 'enqueue', lambda *a: pytest.fail('missing raw cannot enqueue a second paid call'))
    with pytest.raises(HTTPException) as failed:
        u.retry_analysis(row.id, u.AnalysisAction(version=row.version), p, db)
    assert failed.value.status_code == 409 and row.status == 'failed'


@pytest.mark.parametrize('invalid', [False, True])
def test_version6_saved_raw_runs_only_local_validation_and_retains_answer(command_case, monkeypatch, invalid):
    row, draft, db, p, _ = command_case
    row.status = 'queued'
    row.data['request']['contract_hash'] = digest(EditResult.model_json_schema())
    candidate = proposal()
    if invalid:
        candidate['operations'][0]['field'] = 'execution'
    raw = {'batch': 0, 'value': candidate, 'receipt': {'call_id': 'old-paid-call'}}
    raw['hash'] = digest(raw)
    row.data.update(raw_response=copy.deepcopy(raw), completed=[], local_only_recovery=True)
    monkeypatch.setattr(u, 'scoped', lambda *a: row)
    monkeypatch.setattr(u, 'settle_paid_raw', lambda *a: None)
    monkeypatch.setattr(u, 'checkpoint', lambda *a: None)
    monkeypatch.setattr(u, 'audit', lambda *a, **k: None)
    before = copy.deepcopy(draft.data)
    u.run(db, p, row.id)
    assert row.data['raw_response'] == raw and draft.data == before
    assert row.status == ('failed' if invalid else 'review')
    if not invalid:
        assert row.data['result_hash'] == digest(row.data['result'])
        assert row.data['edit_review'][0]['id'] == 'edit_1'


def test_registered_edit_routes_and_request_schema_are_explicit():
    from generic_agents.api import app
    paths = app.openapi()['paths']
    for action in ('review', 'apply', 'undo'):
        assert 'post' in paths['/api/setup/understanding/{object_id}/edits/' + action]
    schema = api.EditApply.model_json_schema()
    assert schema['additionalProperties'] is False
    assert {'request_id', 'result_hash', 'operation_ids', 'revision', 'version'} <= set(schema['required'])


@pytest.mark.parametrize('original', ['数量复核', ''])
def test_legacy_whole_business_update_cannot_erase_existing_manual_ids_or_bindings(original):
    config = configuration()
    before = copy.deepcopy(config)
    result = {'businesses': [{'operation': 'upsert', 'original_name': original, 'name': '数量复核'}]}
    with pytest.raises(HTTPException) as failed:
        u.require_scoped_updates(config, result, ['数量复核'])
    assert failed.value.status_code == 409 and config == before


@pytest.mark.parametrize('operation,selected', [('remove', ['数量复核']), ('upsert', [])])
def test_legacy_explicit_removal_or_unselected_old_suggestion_remains_available(operation, selected):
    u.require_scoped_updates(configuration(), {'businesses': [
        {'operation': operation, 'original_name': '数量复核', 'name': '数量复核'}]}, selected)


def test_legacy_new_business_suggestion_is_still_available():
    u.require_scoped_updates(configuration(), {'businesses': [
        {'operation': 'upsert', 'original_name': '', 'name': '新业务'}]}, ['新业务'])
