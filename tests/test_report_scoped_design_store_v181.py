"""Actual planner/codec/ledger over explicit synthetic SQL/source dependencies.

This is not a real report, source authorization, PG transaction or provider
test. The source callback is the documented Root runtime seam; business draft,
confirmation, history, actor SQL and independent Audit validators are real.
"""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from generic_agents import models as m
from generic_agents import relations
from generic_agents import report_scoped_design_store_v181 as subject
from generic_agents import report_scoped_design_plan_v180 as planner
from generic_agents.contracts import digest
from generic_agents.report_scoped_design_models_v181 import ReportScopedDesignPlanV181 as Plan
from generic_agents.report_scoped_design_models_v181 import ReportScopedDesignPlanCommandV181 as Command
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_saved_map_design_preparation_v176 import source_scene


NOW = datetime(2026, 10, 23, 20, 0, 0, tzinfo=timezone.utc)
def uid(n): return '181abc00-0000-4000-8000-' + f'{n:012x}'
def raw(value): return json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False).encode('utf-8')


def base(source, *, complete=False):
    snapshot, _, indexed = subject._inputs(source)
    return {'schema': planner.LEDGER, 'source_snapshot_hash': snapshot['snapshot_hash'],
        'indexed_inputs_hash': indexed['inputs_hash'], 'input_claims_hash': indexed['input_claims_hash'],
        'input_count': indexed['input_count'], 'scopes': [{'scope_id': 'public-record-review',
            'name': '公共合成记录核对', 'claim_positions': list(range(indexed['input_count'])),
            'context_positions': []}] if complete else [], 'shared': [], 'background': []}


def same_statement(actual, expected):
    left, right = actual.compile(), expected.compile()
    assert str(left) == str(right)
    assert set(left.params) == set(right.params)
    for key in left.params:
        a, b = left.params[key], right.params[key]
        assert type(a) is type(b)
        if isinstance(a, datetime):
            assert a.tzinfo is not None and a.utcoffset() == b.utcoffset() and a == b
        else:
            assert canonical_json(a) == canonical_json(b)


class ReadWriteIO:
    """Only exact SQL families used by this inventory, IAM and owner lookup.

    add/flush retain generated rows for pure protocol tests; they deliberately
    do not claim SQLAlchemy INSERT ordering, isolation or real persistence.
    """
    def __init__(self, source):
        self.source = copy.deepcopy(source)
        snapshot = source['snapshot']
        self.p = NS(user_id=snapshot['actor_id'], workspace_id=snapshot['workspace_id'],
            role='operator', session_id=uid(100))
        self.producer = snapshot['producer']['id']
        self.rows, self.commands, self.events, self.calls, self.pending = [], [], [], [], []
        self.active = self.session_valid = self.owner_valid = True
        self.membership_role = 'operator'

    def execute(self, statement, params=None):
        if params is not None:
            assert str(statement) in {'SELECT pg_advisory_xact_lock(:key)',
                'SELECT pg_advisory_xact_lock_shared(:key)'}
            assert set(params) == {'key'} and type(params['key']) is int
            self.calls.append(('lock', str(statement), params['key']))
            return NS()
        p = self.p
        expected = select(m.User, m.Membership).join(m.Membership,
            m.Membership.user_id == m.User.id).where(m.User.id == p.user_id, m.User.active.is_(True),
                m.Membership.workspace_id == p.workspace_id)
        if statement._for_update_arg is not None:
            expected = expected.where(m.Membership.role.in_(['admin', 'operator']), m.Membership.role == p.role)
        else:
            expected = expected.where(m.Membership.role == p.role)
        expected = expected.add_columns(m.AuthSession).join(m.AuthSession,
            m.AuthSession.user_id == m.User.id).where(m.AuthSession.id == p.session_id,
                m.AuthSession.revoked.is_(False), m.AuthSession.expires_at > NOW)
        if statement._for_update_arg is not None:
            expected = expected.with_for_update(read=True, of=[m.User, m.Membership, m.AuthSession])
        same_statement(statement, expected)
        self.calls.append(('iam', statement._for_update_arg is not None))
        good = self.active and self.session_valid and self.membership_role == p.role
        return NS(first=lambda: (True,) if good else None)

    def scalar(self, statement):
        same_statement(statement, select(m.SetupReportDraft.actor_id).where(
            m.SetupReportDraft.id == self.producer, m.SetupReportDraft.workspace_id == self.p.workspace_id,
            m.SetupReportDraft.actor_id == self.p.user_id))
        self.calls.append(('owner',))
        return self.p.user_id if self.owner_valid else None

    def scalars(self, statement):
        p = self.p; model = statement.column_descriptions[0]['entity']
        if model in {Plan, Command}:
            expected = select(model).where(model.workspace_id == p.workspace_id,
                model.actor_id == p.user_id)
            result = self.rows if model is Plan else self.commands
        else:
            assert model is m.Audit
            expected = select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action.in_([subject.COMMAND_AUDIT, subject.STATE_AUDIT]),
                or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))
            result = self.events
        same_statement(statement, expected)
        assert statement._for_update_arg is None
        self.calls.append(('select', model))
        return list(result)

    def add(self, value):
        assert type(value) in {Plan, Command, m.Audit}, 'No Job, CloudCall or original producer writes'
        target = self.rows if isinstance(value, Plan) else self.commands if isinstance(value, Command) else self.events
        if value not in target: target.append(value)
        self.pending.append(value); self.calls.append(('add', type(value)))

    def flush(self):
        for value in self.pending:
            if value.id is None: value.id = uid(1000 + len(self.events) + self.pending.index(value))
            if value.created_at is None: value.created_at = NOW
        self.calls.append(('flush', tuple(type(value) for value in self.pending)))
        self.pending = []


@pytest.fixture
def setup(monkeypatch):
    _, source = source_scene(); io = ReadWriteIO(source); loads = []
    monkeypatch.setattr(m, 'now', lambda: NOW)
    monkeypatch.setattr(relations, 'now', lambda: NOW)
    def load(db, p, producer_id, revision, *, lock=False):
        assert db is io and p is io.p and producer_id == io.producer and revision == 3 and type(lock) is bool
        if lock:
            assert any(call[0] == 'lock' and 'pg_advisory_xact_lock(' in call[1] for call in io.calls)
        loads.append((producer_id, revision, lock)); return copy.deepcopy(io.source)
    monkeypatch.setattr(subject, 'runtime', lambda: NS(load_source=load))
    return io, source, loads


def write(io, operation, *, ledger=None, request_id=None, plan_id=None, expected_version=None):
    body = {'request_id': request_id or uid(200 + len(io.commands)), 'revision': 3,
        'source_snapshot_hash': io.source['snapshot']['snapshot_hash']}
    if operation == 'create': body.update(producer_id=io.producer, ledger=ledger if ledger is not None else base(io.source))
    else:
        body['expected_version'] = expected_version if expected_version is not None else io.rows[0].version
        if operation == 'confirm': body['confirm'] = True
        else: body['ledger'] = ledger
    result = subject.mutate(io, io.p, plan_id, operation, raw(body), actual_method='POST',
        actual_path=subject.command_path(plan_id, operation))
    return result, body


def test_empty_and_partial_drafts_save_without_fake_confirmation_or_losing_source_positions(setup):
    io, source, loads = setup; before = canonical_json(source)
    result, _ = write(io, 'create'); row = io.rows[0]
    assert result['status'] == 'draft' and result['version'] == 1 and result['plan_hash'] is None
    assert set(row.data) == {'source_context', 'ledger', 'plan'} and row.data['plan'] is None
    assert row.data['ledger']['scopes'] == [] and 'confirmation' not in row.data['ledger']
    assert canonical_json(row.data['source_context']) == before
    part = base(source, complete=True); part['scopes'][0]['claim_positions'] = [0]
    write(io, 'save', ledger=part, plan_id=row.id)
    detail = subject.detail(io, io.p, row.id)
    assert detail['ledger'] == part and detail['source_summary']['input_count'] == source['snapshot']['claim_count']
    assert detail['confirmation'] is None and detail['plan'] is None and len(detail['history']) == 2
    assert detail['history'][0]['ledger']['scopes'] == []
    assert detail['requires_current_source_check'] is True and detail['can_apply'] is detail['can_publish'] is False
    assert len(loads) == 2 and canonical_json(source) == before


def test_confirm_uses_saved_ledger_server_actor_clock_and_real_complete_planner(setup):
    io, source, _ = setup
    write(io, 'create', ledger=base(source, complete=True)); row = io.rows[0]
    result, body = write(io, 'confirm', plan_id=row.id, request_id=uid(300))
    actual = subject.checked_plan(io, io.p, row.id, confirmed=True)
    plan = actual.data['plan']; confirmation = plan['scope_ledger']['confirmation']
    assert result['status'] == actual.status == 'confirmed' and result['plan_hash'] == plan['plan_hash']
    assert confirmation == {'schema': planner.CONFIRMATION, 'status': 'confirmed',
        'actor_id': io.p.user_id, 'request_id': body['request_id'],
        'confirmed_at': NOW.isoformat(), 'ledger_hash': digest(actual.data['ledger'])}
    snapshot, claims, indexed = subject._inputs(source)
    assert planner.check_plan(plan, indexed, snapshot['snapshot_hash'], plan['scope_ledger'], input_claims=claims) == plan
    assert plan['dispatch_allowed'] is False and plan['semantic_quality_verified'] is False
    assert len(plan['source_map']['entries']) == source['snapshot']['claim_count']
    assert 'source_context' not in io.commands[-1].data and 'confirmation' not in body
    assert subject.detail(io, io.p, row.id)['can_save'] is False
    with pytest.raises(HTTPException) as caught: write(io, 'save', ledger=base(source), plan_id=row.id)
    assert caught.value.status_code == 409 and len(io.commands) == 2


def test_incomplete_confirmation_returns_clear_conflict_and_leaves_draft_and_ledger_untouched(setup):
    io, source, _ = setup; write(io, 'create'); row = io.rows[0]
    before = canonical_json(row.data); counts = (len(io.commands), len(io.events))
    with pytest.raises(HTTPException) as caught: write(io, 'confirm', plan_id=row.id)
    assert caught.value.status_code == 409 and '尚未完整分派' in caught.value.detail
    assert row.version == 1 and row.status == 'draft' and canonical_json(row.data) == before
    assert (len(io.commands), len(io.events)) == counts


@pytest.mark.parametrize('fault', ['bool_position', 'float_position', 'outside', 'duplicate', 'two_scopes',
    'unknown_context', 'unknown_shared_scope', 'missing_reason', 'confirmation', 'wrong_hash', 'extra', 'count_bool'])
def test_partial_save_preserves_work_but_never_accepts_invalid_or_self_confirmed_ownership(setup, fault):
    _, source, _ = setup; ledger = base(source, complete=True); scope = ledger['scopes'][0]
    if fault == 'bool_position': scope['claim_positions'] = [True]
    elif fault == 'float_position': scope['claim_positions'] = [0.0]
    elif fault == 'outside': scope['claim_positions'] = [ledger['input_count']]
    elif fault == 'duplicate': scope['claim_positions'] = [0, 0]
    elif fault == 'two_scopes': ledger['scopes'].append({**copy.deepcopy(scope), 'scope_id': 'another', 'name': '另一范围'})
    elif fault == 'unknown_context': scope['context_positions'] = [0]
    elif fault == 'unknown_shared_scope': ledger['shared'] = [{'input_position': 0, 'scope_ids': ['missing', 'another'], 'reason': '公共理由'}]
    elif fault == 'missing_reason': ledger['background'] = [{'input_position': 0}]
    elif fault == 'confirmation': ledger['confirmation'] = {'status': 'confirmed'}
    elif fault == 'wrong_hash': ledger['indexed_inputs_hash'] = '0' * 64
    elif fault == 'extra': scope['original_verified'] = True
    else: ledger['input_count'] = True
    before = canonical_json(ledger)
    with pytest.raises(HTTPException): subject.validate_draft(ledger, source)
    assert canonical_json(ledger) == before


def test_shared_and_explicit_background_context_are_distinct_from_ownership(setup):
    _, source, _ = setup; ledger = base(source)
    ledger['scopes'] = [{'scope_id': 'a', 'name': '范围甲', 'claim_positions': [0], 'context_positions': [2]},
        {'scope_id': 'b', 'name': '范围乙', 'claim_positions': [], 'context_positions': []}]
    ledger['shared'] = [{'input_position': 1, 'scope_ids': ['a', 'b'], 'reason': '两范围明确共享此原声明'}]
    ledger['background'] = [{'input_position': 2, 'reason': '人工明确保留为背景，不是删除原声明'}]
    assert subject.validate_draft(ledger, source) == ledger
    assert ledger['input_count'] > 3, 'remaining source positions deliberately stay unassigned'


@pytest.mark.parametrize('fault', ['revision_bool', 'version_float', 'nonce_upper', 'source_context',
    'indexed', 'client_confirmation', 'consent_integer', 'duplicate_json'])
def test_closed_exact_original_wire_never_accepts_client_source_or_confirmation(setup, fault):
    io, _, _ = setup
    body = {'request_id': uid(401), 'revision': 3, 'source_snapshot_hash': io.source['snapshot']['snapshot_hash'],
        'expected_version': 1, 'confirm': True}; operation = 'confirm'
    if fault == 'revision_bool': body['revision'] = True
    elif fault == 'version_float': body['expected_version'] = 1.0
    elif fault == 'nonce_upper': body['request_id'] = uid(401).upper()
    elif fault in {'source_context', 'indexed', 'client_confirmation'}: body[fault] = {}
    elif fault == 'consent_integer': body['confirm'] = 1
    content = raw(body)
    if fault == 'duplicate_json': content = content[:-1] + b',"revision":3}'
    with pytest.raises(ValueError): subject.parse_wire(content, operation)


def test_exact_nonce_get_and_replay_restore_original_version_without_current_source_or_new_write(setup, monkeypatch):
    io, _, loads = setup; saved, body = write(io, 'create'); command = io.commands[0]
    def forbidden(*args, **kwargs): raise AssertionError('Historical receipt must not reload current source')
    monkeypatch.setattr(subject, 'runtime', forbidden)
    count = len(io.calls); reply = subject.request_result(io, io.p, body['request_id'], command.wire_sha256)
    assert reply['outcome'] == 'saved' and reply['result'] == saved
    assert not any(call[0] in {'add', 'flush'} for call in io.calls[count:])
    repeated = subject.mutate(io, io.p, None, 'create', raw(body), actual_method='POST', actual_path=subject.command_path(None, 'create'))
    assert repeated == saved and len(io.commands) == 1 and len(loads) == 1
    changed = raw(body) + b' '
    with pytest.raises(HTTPException): subject.mutate(io, io.p, None, 'create', changed,
        actual_method='POST', actual_path=subject.command_path(None, 'create'))
    assert len(io.commands) == 1


@pytest.mark.parametrize('fault', ['row_missing', 'command_and_audits_missing', 'command_audit_missing', 'audit_only'])
def test_original_unknown_requires_complete_actor_inventory_not_a_missing_nonce_shortcut(setup, fault):
    io, _, _ = setup; _, body = write(io, 'create'); command = io.commands[0]
    if fault == 'row_missing': io.rows = []
    elif fault == 'command_and_audits_missing': io.commands = []; io.events = []
    elif fault == 'command_audit_missing': io.events = [event for event in io.events if event.action != subject.COMMAND_AUDIT]
    else: io.rows = []; io.commands = []
    before = (len(io.rows), len(io.commands), len(io.events))
    with pytest.raises(subject.InventoryError): subject.request_result(io, io.p, body['request_id'], command.wire_sha256)
    assert (len(io.rows), len(io.commands), len(io.events)) == before


def test_storage_history_cannot_be_self_resealed_against_independent_audit(setup):
    io, _, _ = setup; write(io, 'create'); command = io.commands[0]
    command.exact_body += ' '; command.wire_sha256 = hashlib.sha256(command.exact_body.encode()).hexdigest()
    command.command_hash = digest(subject._command_pin(command))
    with pytest.raises(subject.InventoryError): subject.detail(io, io.p, io.rows[0].id)


def test_current_readonly_membership_session_and_owner_are_actual_queries_and_viewer_has_no_fresh_write(setup):
    io, source, _ = setup; write(io, 'create'); row = io.rows[0]
    io.p.role = io.membership_role = 'viewer'
    start = len(io.calls); detail = subject.detail(io, io.p, row.id)
    assert detail['can_save'] is detail['can_confirm'] is False
    assert not any(call == ('iam', True) for call in io.calls[start:])
    with pytest.raises(HTTPException) as caught: write(io, 'save', plan_id=row.id, ledger=base(source))
    assert caught.value.status_code == 403 and len(io.commands) == 1
    io.session_valid = False
    with pytest.raises(HTTPException) as caught: subject.detail(io, io.p, row.id)
    assert caught.value.status_code == 403


def test_cas_fixed_source_and_source_loader_failure_preserve_all_saved_human_work(setup, monkeypatch):
    io, source, _ = setup; write(io, 'create'); row = io.rows[0]; before = canonical_json(row.data)
    with pytest.raises(HTTPException) as caught: write(io, 'save', plan_id=row.id, expected_version=99, ledger=base(source))
    assert caught.value.status_code == 409
    def revoked(*args, **kwargs): raise HTTPException(409, '当前真实来源版本已变化')
    monkeypatch.setattr(subject, 'runtime', lambda: NS(load_source=revoked))
    with pytest.raises(HTTPException) as caught: write(io, 'save', plan_id=row.id, ledger=base(source, complete=True))
    assert caught.value.status_code == 409 and canonical_json(row.data) == before
    assert subject.detail(io, io.p, row.id)['ledger'] == base(source)
    assert len(io.commands) == 1


def test_actor_then_sorted_report_then_plan_locks_have_no_shared_upgrade_and_no_execution_rows(setup):
    io, source, _ = setup; write(io, 'create'); row = io.rows[0]
    io.calls.clear(); subject.checked_plan(io, io.p, row.id)
    locks = [call for call in io.calls if call[0] == 'lock']
    expected = [{'scoped_design_actor_v181': io.p.user_id, 'workspace_id': io.p.workspace_id},
        {'scoped_design_report_v181': io.producer, 'workspace_id': io.p.workspace_id},
        {'scoped_design_plan_v181': row.id, 'workspace_id': io.p.workspace_id}]
    assert [call[2] for call in locks] == [int(digest(key)[:16], 16) - (1 << 63) for key in expected]
    assert all('pg_advisory_xact_lock_shared(' in call[1] for call in locks)
    io.calls.clear(); write(io, 'save', plan_id=row.id, ledger=base(source, complete=True))
    assert all('pg_advisory_xact_lock_shared(' not in call[1] for call in io.calls if call[0] == 'lock')
    assert all(call[1] in {Plan, Command, m.Audit} for call in io.calls if call[0] == 'add')


def test_genuinely_empty_inventory_can_report_unknown_and_database_errors_propagate(setup, monkeypatch):
    io, _, _ = setup
    assert subject.request_result(io, io.p, uid(900), 'a' * 64)['outcome'] == 'unknown'
    def fail(*args, **kwargs): raise SQLAlchemyError('synthetic database failure')
    monkeypatch.setattr(io, 'scalars', fail)
    with pytest.raises(SQLAlchemyError): subject.request_result(io, io.p, uid(901), 'b' * 64)


def test_normal_proposal_uses_cold_source_and_real_pure_proposal_without_saving_or_making_confirmation(setup):
    io, source, loads = setup
    reply = subject.proposal(io, io.p, io.producer, 3)
    assert reply['schema'] == 'report-scoped-design-proposal-view-v181'
    assert reply['source_snapshot_hash'] == source['snapshot']['snapshot_hash']
    assert reply['source_summary']['input_count'] == source['snapshot']['claim_count']
    suggested = reply['proposal']
    assert subject.validate_draft(suggested['ledger'], source) == suggested['ledger']
    assert 'confirmation' not in suggested['ledger'] and suggested['ledger']['background'] == []
    assert reply['can_apply'] is reply['can_publish'] is False
    assert loads == [(io.producer, 3, False)] and io.rows == io.commands == io.events == []
    assert not any(call[0] in {'add', 'flush'} for call in io.calls)


def test_saved_inputs_page_original_positions_and_text_without_current_source_or_false_completion(setup, monkeypatch):
    io, source, loads = setup; write(io, 'create'); row = io.rows[0]
    before = canonical_json(row.data); snapshot, claims, indexed = subject._inputs(source)
    def forbidden(*args, **kwargs): raise AssertionError('Saved evidence is not a new source grant')
    monkeypatch.setattr(subject, 'runtime', forbidden)
    start = len(io.calls); pages = []; position = 0
    while True:
        page = subject.plan_inputs(io, io.p, row.id, offset=position, limit=1)
        assert set(page) == {'schema', 'plan_id', 'source_snapshot_hash', 'indexed_inputs_hash',
            'input_claims_hash', 'input_count', 'offset', 'limit', 'next_offset', 'items',
            'historical_source', 'requires_current_source_check', 'can_apply', 'can_publish'}
        assert page['schema'] == subject.INPUTS and page['plan_id'] == row.id
        assert page['source_snapshot_hash'] == snapshot['snapshot_hash']
        assert page['indexed_inputs_hash'] == indexed['inputs_hash']
        assert page['input_claims_hash'] == indexed['input_claims_hash']
        assert page['input_count'] == len(claims) and page['offset'] == position and page['limit'] == 1
        assert page['historical_source'] is page['requires_current_source_check'] is True
        assert page['can_apply'] is page['can_publish'] is False
        pages.extend(page['items'])
        if position + 1 == len(claims):
            assert page['next_offset'] is None; break
        assert page['next_offset'] == position + 1
        position = page['next_offset']
    assert [item['global_position'] for item in pages] == list(range(len(claims)))
    assert [item['input_id'] for item in pages] == snapshot['input_order']
    assert [item['claim'] for item in pages] == claims
    assert pages[0]['claim'] == pages[1]['claim'] and pages[0]['input_id'] != pages[1]['input_id']
    assert pages[0]['claim'] is not pages[1]['claim']
    for item in pages:
        assert set(item) == {'global_position', 'input_id', 'claim', 'spans', 'anchor_status'}
        assert item['anchor_status'] == 'resolved_saved_text'
        assert item['spans'] == [{'span_ref': ref, 'text': source['catalog']['index'][ref]['text']}
            for ref in item['claim']['span_refs']]
    # The saved resolver preserves whitespace/Unicode and never returns an
    # uncited neighbouring span just because it is in the full catalog.
    assert '＋+🙂' in pages[0]['spans'][0]['text']
    assert '未被原声明引用的旁支' not in canonical_json(pages)
    empty = subject.plan_inputs(io, io.p, row.id, offset=len(claims) + 5, limit=100)
    assert empty['items'] == [] and empty['next_offset'] is None and empty['input_count'] == len(claims)
    pages[0]['claim']['statement'] = 'local display edit'
    pages[0]['spans'][0]['text'] = 'local display edit'
    assert canonical_json(row.data) == before and len(loads) == 1
    assert not any(call[0] in {'add', 'flush', 'owner'} or call == ('iam', True) for call in io.calls[start:])
    assert all('pg_advisory_xact_lock_shared(' in call[1] for call in io.calls[start:] if call[0] == 'lock')


def test_saved_input_with_no_references_remains_unanchored_and_is_not_guessed_from_duplicate_text(setup):
    io, _, _ = setup
    # Author another explicitly synthetic source before creating a Plan. Its
    # first declaration has no citation; a duplicate statement still has one.
    snapshot = io.source['snapshot']; first = snapshot['input_items'][snapshot['input_order'][0]]
    first.update(span_refs=[], status='unknown', explanation='原声明未提供可定位依据，仍待人工核对')
    snapshot['input_items_hash'] = digest(snapshot['input_items'])
    snapshot['input_claims_hash'] = digest([snapshot['input_items'][key] for key in snapshot['input_order']])
    snapshot['snapshot_hash'] = digest({key: value for key, value in snapshot.items() if key != 'snapshot_hash'})
    write(io, 'create'); page = subject.plan_inputs(io, io.p, io.rows[0].id, limit=2)
    first, second = page['items']
    assert first['claim']['statement'] == second['claim']['statement']
    assert first['claim']['span_refs'] == first['spans'] == [] and first['anchor_status'] == 'unanchored'
    assert second['claim']['span_refs'] and second['spans'] and second['anchor_status'] == 'resolved_saved_text'


@pytest.mark.parametrize('offset,limit', [(True, 50), (0.0, 50), ('0', 50), (-1, 50),
    (0, True), (0, 1.0), (0, '1'), (0, 0), (0, 101), (0, None)])
def test_input_paging_requires_strict_integers_and_never_clamps_or_silently_truncates(setup, offset, limit):
    io, _, _ = setup; write(io, 'create'); before = (len(io.rows), len(io.commands), len(io.events))
    with pytest.raises(HTTPException) as caught:
        subject.plan_inputs(io, io.p, io.rows[0].id, offset=offset, limit=limit)
    assert caught.value.status_code == 422
    assert (len(io.rows), len(io.commands), len(io.events)) == before


@pytest.mark.parametrize('fault,code', [('revoked_session', 403), ('missing_command_audit', 409),
    ('missing_plan', 409), ('unknown_plan', 404)])
def test_input_page_requires_current_identity_and_complete_original_inventory_before_disclosure(setup, fault, code):
    io, _, _ = setup; write(io, 'create'); plan_id = io.rows[0].id
    if fault == 'revoked_session': io.session_valid = False
    elif fault == 'missing_command_audit': io.events = [item for item in io.events if item.action != subject.COMMAND_AUDIT]
    elif fault == 'missing_plan': io.rows = []
    else: plan_id = uid(997)
    start = len(io.calls)
    with pytest.raises(HTTPException) as caught: subject.plan_inputs(io, io.p, plan_id)
    assert caught.value.status_code == code
    assert not any(call[0] in {'add', 'flush'} for call in io.calls[start:])


def test_viewer_can_read_original_inputs_but_corrupt_saved_text_is_not_substituted_with_current_source(setup):
    io, _, _ = setup; write(io, 'create'); row = io.rows[0]
    io.p.role = io.membership_role = 'viewer'
    page = subject.plan_inputs(io, io.p, row.id)
    assert page['items'] and page['can_apply'] is page['can_publish'] is False
    source = row.data['source_context']; ref = source['snapshot']['allowed_span_refs'][0]
    source['catalog']['index'][ref]['text'] += ' altered after save'
    with pytest.raises(subject.InventoryError): subject.plan_inputs(io, io.p, row.id)
