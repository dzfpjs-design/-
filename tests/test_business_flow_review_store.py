"""Actual store transitions with strict, explicitly synthetic SQL/IAM I/O.

No validator, store function or inventory guard is replaced. Fake rows are
created by save(), not SQL success fixtures. This proves pure contracts only,
not real PostgreSQL JSONB/transactions, concurrent locking, HTTP IAM, browser
durability, report authority or the companion diagnostics' semantic quality.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError

from generic_agents import business_flow_review_store as store
from generic_agents import models as m
from generic_agents.business_flow_review_models import BusinessFlowReviewCommand as Command
from generic_agents.business_flow_review_models import BusinessFlowReviewDraft as Draft
from generic_agents.report_scoped_design_models_v181 import ReportScopedDesignAttemptV181 as Attempt
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_saved_map_design_consumer_v176 import equal_statement, matches_statement


def identity(name): return str(uuid5(NAMESPACE_URL, 'public-business-review:' + name))
def wire(value): return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Synthetic store boundary cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def diagnostic(value):
    # The API-owned diagnostic boundary is deliberately NOT a source validator.
    return {'schema': 'business-flow-review-diagnostics-v1', 'available': True,
        'value': copy.deepcopy(value), 'issues': [{'code': 'synthetic_storage_only', 'path': '',
            'message': '仅合成存储边界，不证明业务或原文有效', 'span_ref': None}],
        'source_evidence': [], 'structural_valid': False, 'source_valid': False, **store.FLAGS}


def scene():
    p = NS(user_id=identity('actor'), workspace_id=identity('workspace'), role='operator',
        session_id='public-synthetic-iam-boundary')
    attempt = Attempt(id=identity('attempt'), workspace_id=p.workspace_id, actor_id=p.user_id,
        producer_id=identity('producer'), model_id=identity('model'), revision=7,
        reserved_call_id=identity('call'), status='failed', version=9, data={'untouched_original': True})
    source = {'schema': 'business-flow-review-source-v1', 'attempt_id': attempt.id,
        'producer_id': attempt.producer_id, 'revision': attempt.revision, 'model_id': attempt.model_id,
        'request_hash': 'a' * 64, 'reserved_call_id': attempt.reserved_call_id,
        'capture_hash': 'b' * 64, 'response_body_sha256': 'c' * 64, 'arguments_sha256': 'd' * 64,
        'source_snapshot_hash': 'e' * 64, 'generation_profile': 'flow-v183'}
    baseline = {'schema': 'report-business-flow-wire-v182', 'business': {'flows': [{
        'nodes': [], 'edges': [{'to_node': 'not_defined'}]}]},
        'evidence': [{'span_ref': 'w:p1/s1', 'quote': '截取'}]}
    return NS(p=p, attempt=attempt, source=source, baseline=baseline)


class IO:
    """Exact PostgreSQL statement boundary; unknown SQL always fails loudly."""
    def __init__(self, c):
        self.c = c; self.drafts = []; self.commands = []; self.events = []
        self.commits = 0; self.flushes = 0; self.locks = []
        self.active = True; self.membership_role = c.p.role; self.failure = None

    def execute(self, statement, params=None):
        if hasattr(statement, 'text'):
            assert statement.text in {'SELECT pg_advisory_xact_lock(:k)', 'SELECT pg_advisory_xact_lock_shared(:k)'}
            assert set(params) == {'k'} and type(params['k']) is int
            self.locks.append(statement.text); return NS(first=lambda: None)
        p = self.c.p
        base = select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
            m.User.id == p.user_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
            m.Membership.role == p.role)
        # relations.lock_current_actor spells the same AND predicates in its
        # own order; compile/compare against that exact real query as well.
        locked = select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
            m.User.id == p.user_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
            m.Membership.role.in_(['admin', 'operator']), m.Membership.role == p.role).with_for_update(
                read=True, of=[m.User, m.Membership]).execution_options(populate_existing=True)
        for expected in (base, locked):
            if matches_statement(statement, expected):
                equal_statement(statement, expected)
                valid = self.active and self.membership_role == p.role
                if matches_statement(statement, locked): valid = valid and self.membership_role in {'admin', 'operator'}
                return NS(first=lambda: (object(), object()) if valid else None)
        raise AssertionError('Unexpected SQL execute')

    def scalars(self, statement):
        if self.failure is not None: raise self.failure
        p = self.c.p
        choices = [
            (select(Draft).where(Draft.workspace_id == p.workspace_id, Draft.actor_id == p.user_id),
                [row for row in self.drafts if row.workspace_id == p.workspace_id and row.actor_id == p.user_id]),
            (select(Command).where(Command.workspace_id == p.workspace_id, Command.actor_id == p.user_id),
                [row for row in self.commands if row.workspace_id == p.workspace_id and row.actor_id == p.user_id]),
            (select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action.in_([store.COMMAND_AUDIT, store.STATE_AUDIT]),
                or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id)),
                [row for row in self.events if row.workspace_id == p.workspace_id and
                    (row.actor_id == p.user_id or row.data.get('actor_id') == p.user_id)])]
        for label in ('first', 'second', 'third', 'unknown', 'other', 'new'):
            nonce = identity(label)
            choices.extend([
                (select(Command).where(Command.workspace_id == p.workspace_id, Command.request_id == nonce),
                    [row for row in self.commands if row.workspace_id == p.workspace_id and row.request_id == nonce]),
                (select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                    m.Audit.action.in_([store.COMMAND_AUDIT, store.STATE_AUDIT]),
                    m.Audit.data['request_id'].as_string() == nonce),
                    [row for row in self.events if row.workspace_id == p.workspace_id and row.data.get('request_id') == nonce]),
                (select(Draft).where(Draft.workspace_id == p.workspace_id,
                    or_(Draft.request_id == nonce, Draft.data['command_refs'].contains([{'request_id': nonce}]))),
                    [row for row in self.drafts if row.workspace_id == p.workspace_id and
                        (row.request_id == nonce or any(ref['request_id'] == nonce for ref in row.data['command_refs']))])])
        for expected, result in choices:
            if matches_statement(statement, expected):
                equal_statement(statement, expected); return list(result)
        raise AssertionError('Unexpected SQL scalars')

    def scalar(self, statement):
        p = self.c.p; parent = self.c.attempt
        expected = select(Attempt).where(Attempt.id == parent.id, Attempt.workspace_id == p.workspace_id,
            Attempt.actor_id == p.user_id)
        equal_statement(statement, expected)
        return parent if parent.workspace_id == p.workspace_id and parent.actor_id == p.user_id else None

    def add(self, row):
        if isinstance(row, Draft):
            if all(saved is not row for saved in self.drafts): self.drafts.append(row)
        elif isinstance(row, Command): self.commands.append(row)
        elif isinstance(row, m.Audit):
            row.id = identity('audit-' + str(len(self.events))); self.events.append(row)
        else: raise AssertionError('No provider call, Job or original Attempt write is allowed')

    def flush(self): self.flushes += 1
    def commit(self): self.commits += 1


def body(label='first', version=0, value=None, note=' 私人待办＋+🙂保留 '):
    return {'request_id': identity(label), 'expected_version': version, 'value': value, 'note': note}


def save(c, db, request):
    return store.save(db, c.p, c.attempt.id, wire(request), method='POST', path=store.save_path(c.attempt.id),
        source_binding=c.source, baseline_value=c.baseline, diagnostics=diagnostic(request['value']))


@pytest.mark.parametrize('value', [{}, None, [], {'business': {'flows': []}}, '尚未整理'])
def test_safe_incomplete_values_are_saved_without_semantic_repair(value):
    c = scene(); db = IO(c); request = body(value=value)
    original = canonical_json([c.source, c.baseline, c.attempt.data]); result = save(c, db, request)
    assert result['value'] == value and result['note'] == request['note'] and result['status'] == 'incomplete'
    assert result['baseline_value'] == c.baseline and result['source_binding'] == c.source
    assert result['diagnostics']['value'] == value
    assert all(result[key] is False for key in store.FLAGS)
    assert store.get(db, c.p, c.attempt.id)['value'] == value
    assert canonical_json([c.source, c.baseline, c.attempt.data]) == original
    assert c.attempt.version == 9 and c.attempt.status == 'failed'
    assert len(db.drafts) == len(db.commands) == db.commits == 1 and len(db.events) == 2


@pytest.mark.parametrize('fault', ['duplicate', 'extra_source', 'extra_diagnostic', 'extra_baseline',
    'bool_version', 'negative_version', 'float_version', 'uppercase_uuid', 'bad_uuid', 'bad_note',
    'long_note', 'nul', 'deep', 'nan', 'utf8', 'oversize', 'not_bytes'])
def test_wire_boundary_rejects_bad_shape_and_unsafe_json_without_side_effects(fault):
    value = body(value={})
    if fault == 'extra_source': value['source_binding'] = {}
    elif fault == 'extra_diagnostic': value['diagnostics'] = {}
    elif fault == 'extra_baseline': value['baseline_value'] = {}
    elif fault == 'bool_version': value['expected_version'] = False
    elif fault == 'negative_version': value['expected_version'] = -1
    elif fault == 'float_version': value['expected_version'] = 0.0
    elif fault == 'uppercase_uuid': value['request_id'] = value['request_id'].upper()
    elif fault == 'bad_uuid': value['request_id'] = 'new'
    elif fault == 'bad_note': value['note'] = None
    elif fault == 'long_note': value['note'] = 'x' * 12001
    elif fault == 'nul': value['value'] = '\x00'
    elif fault == 'deep':
        nested = None
        for _ in range(50): nested = [nested]
        value['value'] = nested
    raw = wire(value)
    if fault == 'duplicate': raw = raw[:-1] + b',"note":"duplicate"}'
    elif fault == 'nan': raw = raw[:-1] + b',"hidden":NaN}'
    elif fault == 'utf8': raw = b'\xff'
    elif fault == 'oversize': raw = b' ' * (store.MAX_WIRE_BYTES + 1)
    elif fault == 'not_bytes': raw = raw.decode('utf-8')
    with pytest.raises(ValueError): store.parse_wire(raw)


def test_saved_history_and_original_dto_survive_new_edits_and_cold_store_reads():
    c = scene(); db = IO(c); first = body(value={}); saved_first = save(c, db, first)
    second = body('second', 1, {'business': {'flows': [{'name': '待核', 'edges': []}]}}, ' 第二版待改 ')
    saved_second = save(c, db, second)
    current = store.get(db, c.p, c.attempt.id)
    assert current['version'] == 2 and current['value'] == second['value']
    assert [item['value'] for item in current['history']] == [first['value'], second['value']]
    sha = hashlib.sha256(wire(first)).hexdigest()
    original = store.original_request(db, c.p, first['request_id'], sha)
    assert original == {'schema': store.REQUEST, 'request_id': first['request_id'], 'found': True,
        'body_sha256': sha, 'result': saved_first}
    assert original['result'] != saved_second
    assert store.replay(db, c.p, c.attempt.id, wire(first), method='POST', path=store.save_path(c.attempt.id)) == saved_first
    assert save(c, db, first) == saved_first and db.commits == 2
    assert store.list_saved(db, c.p) == [current]
    # No runtime/provider/source loader exists on this boundary. A fresh store
    # read uses only already-generated saved rows/commands/audits and real code.
    cold = IO(c); cold.drafts = copy.deepcopy(db.drafts); cold.commands = copy.deepcopy(db.commands)
    cold.events = copy.deepcopy(db.events)
    assert store.get(cold, c.p, c.attempt.id) == current
    assert store.original_request(cold, c.p, first['request_id'], sha)['result'] == saved_first
    assert cold.commits == 0 and all('shared' in item for item in cold.locks)


@pytest.mark.parametrize('fault', ['baseline', 'source', 'diagnostic_value', 'apply', 'publish', 'execute', 'semantic'])
def test_frozen_baseline_source_and_diagnostics_cannot_replace_human_value(fault):
    c = scene(); db = IO(c); save(c, db, body(value={}))
    request = body('second', 1, {'unfinished': True}); diag = diagnostic(request['value'])
    source, baseline = copy.deepcopy(c.source), copy.deepcopy(c.baseline)
    if fault == 'baseline': baseline['evidence'][0]['quote'] += 'changed'
    elif fault == 'source': source['capture_hash'] = 'f' * 64
    elif fault == 'diagnostic_value': diag['value'] = {'silently_repaired': True}
    else: diag[{'apply': 'can_apply', 'publish': 'can_publish', 'execute': 'can_execute', 'semantic': 'semantic_quality_verified'}[fault]] = True
    before = canonical_json(db.drafts[0].data)
    with pytest.raises(HTTPException): store.save(db, c.p, c.attempt.id, wire(request), method='POST',
        path=store.save_path(c.attempt.id), source_binding=source, baseline_value=baseline, diagnostics=diag)
    assert canonical_json(db.drafts[0].data) == before and db.commits == 1 and len(db.commands) == 1


def test_only_proven_cas_conflict_is_not_applied_and_original_nonce_always_wins():
    c = scene(); db = IO(c); first = body(value={}); original = save(c, db, first)
    with pytest.raises(HTTPException) as conflict: save(c, db, body('second', 0, {'local_work': True}))
    assert conflict.value.status_code == 409 and conflict.value.detail == {
        'code': 'business_flow_review_version_conflict', 'not_applied': True, 'current_version': 1}
    assert store.original_request(db, c.p, identity('second'), hashlib.sha256(wire(body('second', 0, {'local_work': True}))).hexdigest())['found'] is False
    assert save(c, db, first) == original and len(db.commands) == db.commits == 1
    changed = {**first, 'note': '同号异文'}
    with pytest.raises(HTTPException) as different: save(c, db, changed)
    assert different.value.status_code == 409 and type(different.value.detail) is str
    assert db.commits == 1


@pytest.mark.parametrize('fault', ['raw', 'path', 'method', 'target', 'sha'])
def test_original_request_cannot_be_borrowed_or_return_latest_by_guess(fault):
    c = scene(); db = IO(c); request = body(value={}); save(c, db, request)
    raw = wire(request); path = store.save_path(c.attempt.id); method = 'POST'; target = c.attempt.id
    if fault == 'raw': raw = raw.replace(b'"note": ', b'"note":  ')
    elif fault == 'path': path += '/other'
    elif fault == 'method': method = 'PUT'
    elif fault == 'target': target = identity('other-attempt'); path = store.save_path(target)
    if fault == 'sha':
        with pytest.raises(HTTPException): store.original_request(db, c.p, request['request_id'], '0' * 64)
    else:
        with pytest.raises(HTTPException): store.replay(db, c.p, target, raw, method=method, path=path)
    assert db.commits == 1


@pytest.mark.parametrize('missing', ['draft', 'command', 'command_audit', 'state_audit', 'both_command_and_audit'])
def test_three_way_residuals_never_become_missing_or_fresh_authority(missing):
    c = scene(); db = IO(c); request = body(value={}); save(c, db, request)
    if missing == 'draft': db.drafts.clear()
    elif missing == 'command': db.commands.clear()
    elif missing == 'command_audit': db.events[:] = [row for row in db.events if row.action != store.COMMAND_AUDIT]
    elif missing == 'state_audit': db.events[:] = [row for row in db.events if row.action != store.STATE_AUDIT]
    else: db.commands.clear(); db.events.clear()
    with pytest.raises(HTTPException) as caught:
        store.original_request(db, c.p, request['request_id'], hashlib.sha256(wire(request)).hexdigest())
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException): store.get(db, c.p, c.attempt.id)
    assert db.commits == 1


@pytest.mark.parametrize('which_nonce', ['first', 'second'])
def test_foreign_actor_cannot_reuse_nonce_when_only_other_actors_draft_survives(which_nonce):
    c = scene(); db = IO(c); save(c, db, body(value={}))
    save(c, db, body('second', 1, {'still': 'incomplete'}))
    db.commands.clear(); db.events.clear()
    c.p.user_id = identity('different-actor')
    before = canonical_json(db.drafts[0].data)
    with pytest.raises(HTTPException) as caught:
        store.replay(db, c.p, c.attempt.id, wire(body(which_nonce, 0, {})), method='POST', path=store.save_path(c.attempt.id))
    assert caught.value.status_code == 409
    assert canonical_json(db.drafts[0].data) == before and db.commits == 2


def test_current_membership_and_active_account_are_real_query_boundaries():
    c = scene(); db = IO(c); first = body(value={}); saved = save(c, db, first)
    db.membership_role = 'viewer'
    with pytest.raises(HTTPException) as stale: store.get(db, c.p, c.attempt.id)
    assert stale.value.status_code == 403
    c.p.role = 'viewer'
    assert store.get(db, c.p, c.attempt.id)['value'] == {}
    assert store.original_request(db, c.p, first['request_id'], hashlib.sha256(wire(first)).hexdigest())['result'] == saved
    with pytest.raises(HTTPException) as denied: save(c, db, body('second', 1, {'x': 1}))
    assert denied.value.status_code == 403
    db.active = False
    with pytest.raises(HTTPException) as inactive: store.get(db, c.p, c.attempt.id)
    assert inactive.value.status_code == 403 and db.commits == 1


def test_absence_and_post_probe_use_no_shared_to_exclusive_upgrade():
    c = scene(); db = IO(c); request = body(value={})
    assert store.get(db, c.p, c.attempt.id) is None and store.list_saved(db, c.p) == []
    db.locks.clear()
    assert store.replay(db, c.p, c.attempt.id, wire(request), method='POST', path=store.save_path(c.attempt.id)) is None
    save(c, db, request)
    assert db.locks and all(item == 'SELECT pg_advisory_xact_lock(:k)' for item in db.locks)
    assert db.commits == 1


def test_sql_failures_are_not_swallowed_or_translated_into_missing_success():
    c = scene(); db = IO(c)
    failure = OperationalError('public synthetic SQL boundary', {}, RuntimeError('synthetic failure'))
    db.failure = failure
    with pytest.raises(OperationalError) as caught: store.get(db, c.p, c.attempt.id)
    assert caught.value is failure and not db.drafts and not db.commands and db.commits == 0


def test_complete_one_mib_value_plus_note_and_command_shell_can_be_saved():
    c = scene(); db = IO(c)
    value = 'x' * (store.MAX_VALUE_BYTES - 2)  # Quotes are part of the value boundary.
    assert len(canonical_json(value).encode('utf-8')) == store.MAX_VALUE_BYTES
    request = body(value=value, note='注' * 12000); raw = wire(request)
    assert store.MAX_VALUE_BYTES < len(raw) < store.MAX_WIRE_BYTES == 2 * 1024 * 1024
    result = save(c, db, request)
    assert result['value'] == value and result['note'] == request['note']
    assert store.get(db, c.p, c.attempt.id)['value'] == value and db.commits == 1


def test_all_source_refs_and_more_than_two_mib_diagnostics_are_preserved_without_truncation():
    c = scene(); db = IO(c); request = body(value={}); diag = diagnostic(request['value'])
    diag['source_evidence'] = [{'span_ref': 'w:p' + str(i + 1) + '/s1', 'source_text': '原文',
        'text_origin': 'platform_resolved_source'} for i in range(257)]
    diag['source_evidence'][0]['source_text'] = 'a' * (3 * 1024 * 1024)
    encoded = canonical_json(diag).encode('utf-8')
    assert 2 * 1024 * 1024 < len(encoded) < store.MAX_DIAGNOSTICS_BYTES == 8 * 1024 * 1024
    result = store.save(db, c.p, c.attempt.id, wire(request), method='POST', path=store.save_path(c.attempt.id),
        source_binding=c.source, baseline_value=c.baseline, diagnostics=diag)
    assert result['diagnostics'] == diag
    assert len(store.get(db, c.p, c.attempt.id)['diagnostics']['source_evidence']) == 257
    assert store.original_request(db, c.p, request['request_id'], hashlib.sha256(wire(request)).hexdigest())['result'] == result
    assert 'diagnostics' not in db.commands[0].data  # The original result holds one copy.
    assert len(canonical_json(db.commands[0].data).encode('utf-8')) < store.MAX_RECORD_BYTES


@pytest.mark.parametrize('fault', ['value_bytes', 'diagnostic_bytes'])
def test_capacity_overflow_is_rejected_without_clipping_or_new_saved_rows(fault):
    c = scene(); db = IO(c); request = body(value={}); diag = diagnostic(request['value'])
    if fault == 'value_bytes': request['value'] = 'x' * store.MAX_VALUE_BYTES
    else: diag['source_evidence'] = [{'span_ref': 'w:p1/s1',
        'source_text': 'x' * store.MAX_DIAGNOSTICS_BYTES, 'text_origin': 'platform_resolved_source'}]
    with pytest.raises((ValueError, HTTPException)):
        store.save(db, c.p, c.attempt.id, wire(request), method='POST', path=store.save_path(c.attempt.id),
            source_binding=c.source, baseline_value=c.baseline, diagnostics=diag)
    assert not db.drafts and not db.commands and not db.events and db.commits == 0
