"""Actual new journal/closed-wire checks with explicit read-only SQL IO.

The synthetic value proves inventory shape only. It is not a real report,
source permission, successful AI run, adoption, generated instance or PG test.
No current-source validator or actor validator is replaced by a success stub.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents import report_object_revision_store_v166 as store
from generic_agents.report_object_revision_api_v166 import parse_wire, wire_sha256, command_path
from generic_agents.report_object_revision_models_v166 import ReportObjectRevision as Revision
from generic_agents.report_object_revision_models_v166 import ReportObjectRevisionCommand as Command


def uid(number):
    return '166abc00-0000-4000-8000-' + f'{number:012x}'


WS, ACTOR, REPORT, ROW = [uid(n) for n in range(1, 5)]
P = NS(user_id=ACTOR, workspace_id=WS, role='operator', session_id=uid(8))
WHEN = datetime(2026, 10, 21, 12, 30, tzinfo=timezone.utc)
BASIS = {'report_id': REPORT, 'report_revision': 4, 'model_result_hash': 'a' * 64,
    'source_hash': 'b' * 64, 'catalog_hash': 'c' * 64, 'context_hash': 'd' * 64,
    'base_value_hash': 'e' * 64}


def value(confirmed=False):
    return {'schema': 'report-object-semantic-review-v166', 'note': '人稿原理由，不是原文或领域核实。',
        'result': {'schema': 'report-object-semantic-candidate-wire-v166',
            'source_hash': BASIS['source_hash'], 'catalog_hash': BASIS['catalog_hash'],
            'domain': '合成结构库存', 'global_claims': [], 'relationships': [],
            'businesses': [], 'questions': ['业务标识仍待核对。'],
            'objects': [{'name': '记录单', 'fields': [{'label': '展示说明', 'kind': '文本',
                'required': False, 'unit': None, 'citations': [], 'inference_reason': '仅为库存形状样本。'}],
                'citations': [], 'inference_reason': '仅为库存形状样本。',
                'business_identity': {'status': 'not_established', 'field_labels': [],
                    'citations': [], 'explanation': '尚未明确业务标识，不证明原文没有标识。'}}]},
        'object_identity_confirmations': [{'object_name': '记录单',
            'acknowledge_platform_identity': confirmed}]}


def raw(body):
    return json.dumps(body, ensure_ascii=False, indent=1).encode('utf-8')


def journal(operations=('save',)):
    commands, audits, previous = [], [], None
    row = Revision(id=ROW, workspace_id=WS, report_id=REPORT, actor_id=ACTOR,
        status='draft', version=1, basis_hash=digest(BASIS), record_hash='', data={})
    for version, operation in enumerate(operations, 1):
        body = {'request_id': 'object-revision-' + str(version).zfill(4), 'expected_version': version - 1,
            'basis_hash': digest(BASIS), 'value': value(operation == 'confirm')}
        body['value']['note'] += ' 第' + str(version) + '版。'
        row.version = version
        row.status = 'confirmed' if operation == 'confirm' else 'draft'
        row.data = {'schema': store.STATE_SCHEMA, 'basis': copy.deepcopy(BASIS), 'value': body['value']}
        snapshot = store._snapshot(row)
        row.record_hash = digest(snapshot)
        command = Command(id=uid(100 + version), workspace_id=WS, actor_id=ACTOR,
            report_id=REPORT, revision_id=ROW, record_version=version,
            request_id=body['request_id'], operation=operation, method='POST',
            path=command_path(REPORT, operation), wire_sha256=wire_sha256(raw(body)),
            body_hash=digest(body), created_at=WHEN + timedelta(seconds=version), command_hash='',
            data={'schema': store.COMMAND_SCHEMA, 'wire': raw(body).decode(), 'body': copy.deepcopy(body),
                'record_snapshot': snapshot, 'previous_hash': previous})
        command.data['result'] = store._result(command)
        command.command_hash = digest(store._command_pin(command))
        audits.append(m.Audit(id=uid(200 + version), workspace_id=WS, actor_id=ACTOR,
            action=store.ACTION, object_id=command.id, data=store._audit_pin(command)))
        commands.append(command)
        previous = row.record_hash
    return row, commands, audits


class JournalIO:
    """Only the exact journal's SELECT/lock families; never an ORM simulator."""
    def __init__(self, rows=(), commands=(), audits=(), *, target_report=REPORT):
        self.rows = {Revision: list(rows), Command: list(commands), m.Audit: list(audits)}
        self.calls = []
        self.target_report = target_report
        self.report = m.SetupReportDraft(id=target_report, workspace_id=WS, actor_id=ACTOR,
            request_id='rd_' + uid(9), status='reviewed', data={}, version=4)

    def execute(self, statement, params):
        assert str(statement) in {'SELECT pg_advisory_xact_lock(:key)',
            'SELECT pg_advisory_xact_lock_shared(:key)'}
        keys = [{'report_object_revision_report_v166': report_id, 'workspace_id': WS}
            for report_id in {REPORT, self.target_report}]
        keys.append({'report_object_revision_inventory_v166': ACTOR, 'workspace_id': WS})
        keys += [{'report_object_revision_nonce_v166': item.request_id,
            'workspace_id': WS, 'actor_id': ACTOR} for item in self.rows[Command]]
        keys.append({'report_object_revision_nonce_v166': 'object-revision-0001',
            'workspace_id': WS, 'actor_id': ACTOR})
        assert params == {'key': params['key']} and params['key'] in {
            int(digest(key)[:16], 16) - (1 << 63) for key in keys}
        self.calls.append(('lock', str(statement), params['key']))

    def scalar(self, statement):
        assert statement.column_descriptions[0]['entity'] is m.SetupReportDraft
        assert statement.column_descriptions[0]['name'] == 'actor_id'
        flattened = list(statement.compile().params.values())
        assert WS in flattened and self.target_report in flattened and ACTOR in flattened
        self.calls.append(('owner',))
        return self.report.actor_id

    def scalars(self, statement):
        model = statement.column_descriptions[0]['entity']
        assert model in self.rows, 'unexpected SQL must fail this explicit IO boundary'
        flattened = []
        for item in statement.compile().params.values():
            flattened.extend(item if isinstance(item, list) else [item])
        assert WS in flattened
        if statement._limit_clause is not None:
            assert model in {Command, m.Audit} and ACTOR in flattened
            request = next(item for item in flattened if type(item) is str and item.startswith('object-revision-'))
            result = [item for item in self.rows[model] if
                (item.request_id if model is Command else item.data['request_id']) == request]
        else:
            scoped_reports = {REPORT, self.target_report} & set(
                item for item in flattened if type(item) is str)
            assert scoped_reports or ACTOR in flattened, 'only report inventory or actual actor discovery is provided'
            result = self.rows[model]
        if model is m.Audit:
            assert store.ACTION in flattened
        self.calls.append(('read' if statement._limit_clause is not None or scoped_reports else 'discover', model))
        return list(result)

    def add(self, _):
        raise AssertionError('Historical/replay paths must not write')

    def flush(self):
        raise AssertionError('Historical/replay paths must not flush')


def test_actual_inventory_preserves_optional_identity_and_every_original_wire():
    row, commands, audits = journal(('save', 'confirm'))
    before = copy.deepcopy((row.data, commands[0].data, commands[1].data, audits[0].data))
    actual, history = store._inventory(JournalIO([row], commands, audits), P, REPORT)
    assert actual is row and history == commands
    assert row.data['value']['result']['objects'][0]['fields'][0]['required'] is False
    assert commands[0].data['wire'].startswith('{\n ')
    assert (row.data, commands[0].data, commands[1].data, audits[0].data) == before


@pytest.mark.parametrize('missing', ['record', 'command', 'audit', 'command-and-audit', 'record-and-command'])
def test_compound_inventory_loss_is_never_empty_or_unknown(missing):
    row, commands, audits = journal()
    db = JournalIO([] if missing in {'record', 'record-and-command'} else [row],
        [] if missing in {'command', 'command-and-audit', 'record-and-command'} else commands,
        [] if missing in {'audit', 'command-and-audit'} else audits)
    with pytest.raises(HTTPException) as error:
        store.request_result(db, P, REPORT, commands[0].request_id, commands[0].wire_sha256)
    assert error.value.status_code == 409


@pytest.mark.parametrize('mode', ['new-write', 'unknown-lookup'])
def test_cross_report_compound_loss_cannot_reuse_nonce_before_source_or_iam(mode, monkeypatch):
    row, commands, _ = journal()
    target = uid(20)
    db = JournalIO([row], [], [], target_report=target)
    original = commands[0]
    before = copy.deepcopy(row.data)
    def forbidden(*args, **kwargs):
        raise AssertionError('Broken actor inventory must stop before current source')
    monkeypatch.setattr(store, '_source', forbidden)
    with pytest.raises(HTTPException) as error:
        if mode == 'new-write':
            target_basis = {**BASIS, 'report_id': target}
            body = {'request_id': original.request_id, 'expected_version': 0,
                'basis_hash': digest(target_basis), 'value': value()}
            store.mutate(db, P, target, 'save', body, raw(body), 'POST', command_path(target, 'save'))
        else:
            store.request_result(db, P, target, original.request_id, original.wire_sha256)
    assert error.value.status_code == 409 and row.data == before
    actor_key = int(digest({'report_object_revision_inventory_v166': ACTOR,
        'workspace_id': WS})[:16], 16) - (1 << 63)
    report_keys = [int(digest({'report_object_revision_report_v166': report_id,
        'workspace_id': WS})[:16], 16) - (1 << 63) for report_id in sorted([REPORT, target])]
    actor_lock = next(i for i, call in enumerate(db.calls) if call[0] == 'lock' and call[2] == actor_key)
    discovery = next(i for i, call in enumerate(db.calls) if call[0] == 'discover')
    locked_reports = [call[2] for call in db.calls if call[0] == 'lock' and call[2] in report_keys]
    assert actor_lock < discovery and locked_reports[:2] == report_keys
    expected_lock = 'SELECT pg_advisory_xact_lock(:key)' if mode == 'new-write' else 'SELECT pg_advisory_xact_lock_shared(:key)'
    assert db.calls[actor_lock][1] == expected_lock
    # scalar() permits only the ownership-column SELECT; real IAM/clock or any
    # add/flush would fail the explicit IO seam rather than fabricate success.
    assert [call for call in db.calls if call[0] == 'owner'] == [('owner',)]


@pytest.mark.parametrize('fault', ['actor', 'version-bool', 'status', 'basis', 'value', 'wire', 'audit', 'broken-data'])
def test_independent_edges_and_original_types_reject_single_side_corruption(fault):
    row, commands, audits = journal()
    if fault == 'actor': row.actor_id = uid(999)
    elif fault == 'version-bool': row.version = True
    elif fault == 'status': row.status = 'confirmed'
    elif fault == 'basis': row.data['basis']['report_revision'] = True
    elif fault == 'value': row.data['value']['note'] = '不是原稿'
    elif fault == 'wire': commands[0].data['wire'] += ' '
    elif fault == 'audit': audits[0].data['record_hash'] = 'f' * 64
    else: commands[0].data = {}
    with pytest.raises(HTTPException) as error:
        store._inventory(JournalIO([row], commands, audits), P, REPORT)
    assert error.value.status_code == 409


def test_saved_request_returns_original_version_not_current_confirmed_view():
    row, commands, audits = journal(('save', 'confirm'))
    reply = store.request_result(JournalIO([row], commands, audits), P, REPORT,
        commands[0].request_id, commands[0].wire_sha256)
    assert reply['outcome'] == 'saved' and reply['result'] == commands[0].data['result']
    assert reply['result']['version'] == 1 and reply['result']['status'] == 'draft'
    assert row.version == 2 and row.status == 'confirmed'


def test_genuinely_empty_inventory_returns_unknown_without_current_source_or_writes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unknown lookup is not a source or execution permission')
    monkeypatch.setattr(store, '_source', forbidden)
    reply = store.request_result(JournalIO(), P, REPORT, 'object-revision-0001', 'f' * 64)
    assert reply == {'schema': store.REQUEST_SCHEMA, 'request_id': 'object-revision-0001',
        'wire_sha256': 'f' * 64, 'outcome': 'unknown', 'result': None}


@pytest.mark.parametrize('fault', ['wrong-sha', 'changed-wire'])
def test_same_nonce_cannot_be_rebound_to_other_exact_bytes(fault):
    row, commands, audits = journal(); original = commands[0]
    with pytest.raises(HTTPException) as error:
        if fault == 'wrong-sha':
            store.request_result(JournalIO([row], commands, audits), P, REPORT,
                original.request_id, 'f' * 64)
        else:
            store.mutate(JournalIO([row], commands, audits), P, REPORT, 'save',
                copy.deepcopy(original.data['body']), original.data['wire'].encode() + b' ',
                'POST', original.path)
    assert error.value.status_code == 409


def test_actual_saved_replay_never_reopens_current_source_or_writes(monkeypatch):
    row, commands, audits = journal(('save', 'confirm')); original = commands[0]
    def forbidden(*args, **kwargs):
        raise AssertionError('Known exact reply cannot renew source/authority')
    monkeypatch.setattr(store, '_source', forbidden)
    reply = store.mutate(JournalIO([row], commands, audits), P, REPORT, 'save',
        copy.deepcopy(original.data['body']), original.data['wire'].encode(), 'POST', original.path)
    assert reply['result'] == original.data['result'] and row.version == 2


def test_source_unavailable_keeps_saved_human_history_without_a_current_fallback(monkeypatch):
    from generic_agents import report_object_revision_source_v166 as source
    row, commands, audits = journal(('save', 'confirm'))
    def unavailable(*args, **kwargs):
        raise HTTPException(409, 'synthetic current source unavailable')
    monkeypatch.setattr(source, 'load_source', unavailable)
    result = store.detail(JournalIO([row], commands, audits), P, REPORT)
    assert result['source_available'] is False and result['can_save'] is False and result['can_confirm'] is False
    assert result['value'] == row.data['value'] and result['basis_hash'] == row.basis_hash
    assert [item['version'] for item in result['draft_history']] == [1, 2]
    assert result['source_issue'] and result['status'] == 'confirmed'


def test_other_report_owner_is_not_a_private_revision_reader():
    db = JournalIO(); db.report.actor_id = uid(999)
    with pytest.raises(HTTPException) as error:
        store.detail(db, P, REPORT)
    assert error.value.status_code == 404
    assert db.calls == [('owner',)]


@pytest.mark.parametrize('operations,blocked', [((), False), (('save',), False),
    (('save', 'confirm'), True), (('save', 'confirm', 'save'), True)],
    ids=['no-new-record', 'draft-only', 'confirmed', 'confirmed-then-new-draft'])
def test_legacy_new_attempt_checks_confirm_history_without_renewing_source(operations, blocked, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Legacy gate is human inventory only')
    monkeypatch.setattr(store, '_source', forbidden)
    if operations:
        row, commands, audits = journal(operations)
        db = JournalIO([row], commands, audits)
    else:
        db = JournalIO()
    if blocked:
        with pytest.raises(HTTPException) as error:
            store.reject_legacy_adoption(db, P, REPORT)
        assert error.value.status_code == 409
        assert error.value.detail == '此报告已确认独立新结构稿，新版采用尚未接入，原稿与配置保留，请勿用旧采用覆盖'
    else:
        assert store.reject_legacy_adoption(db, P, REPORT) is None


@pytest.mark.parametrize('fault', ['bool-version', 'float-version', 'string-version', 'extra',
    'duplicate', 'nonfinite', 'nul', 'surrogate', 'invalid-utf8'])
def test_actual_closed_wire_does_not_coerce_or_rewrite_human_requests(fault):
    body = {'request_id': 'object-revision-0001', 'expected_version': 0,
        'basis_hash': digest(BASIS), 'value': value()}
    if fault == 'bool-version': body['expected_version'] = True
    elif fault == 'float-version': body['expected_version'] = 0.0
    elif fault == 'string-version': body['expected_version'] = '0'
    elif fault == 'extra': body['adopt_configuration'] = True
    data = raw(body)
    if fault == 'duplicate': data = data[:-1] + b',"\\u0076alue":{}}'
    elif fault == 'nonfinite': data = data.replace(b'"expected_version": 0', b'"expected_version": NaN')
    elif fault == 'nul': data = data.replace(b'"note":', b'"bad\\u0000key":null,"note":', 1)
    elif fault == 'surrogate': data = data[:-1] + b',"bad":"\\ud800"}'
    elif fault == 'invalid-utf8': data = b'\xff' + data
    before = data
    with pytest.raises(ValueError):
        parse_wire(data)
    assert data == before
