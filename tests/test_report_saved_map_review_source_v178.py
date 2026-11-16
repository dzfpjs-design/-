"""Durable selection protocol with explicit in-memory IAM/source/SQL seams.

Actual head/history/Audit/raw-wire/CAS, V178 native/wire and V166 semantic
validators run. Consumer inventory, original report/material access and original
HTTP read are labeled dependency fixtures, not PG or paid-provider evidence.
Root alone executes these authored tests and the actual API/PG journey.
"""
import copy
import hashlib
import json
import socket
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import UniqueConstraint, select as sql_select
from sqlalchemy.exc import OperationalError

from generic_agents import models as m, relations
from generic_agents import report_saved_map_review_source_v178 as subject
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_design_profiles_v178 as profiles
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_full_semantic_handoff as handoff
from generic_agents.contracts import digest
from generic_agents.report_object_revision_models_v166 import (
    ReportObjectRevision as Revision, ReportObjectRevisionCommand as RevisionCommand)
from generic_agents.report_saved_map_review_models_v178 import ReportSavedMapReviewSourceV178 as Head
from generic_agents.report_evidence_pipeline_v168 import manual_value_from_candidate
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_saved_map_design_consumer_v178 import scene, call_for, paid_response, identity

CLOCK = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure review selection cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def equal_statement(actual, expected):
    left, right = actual.compile(), expected.compile()
    assert str(left) == str(right) and set(left.params) == set(right.params)
    for key, value in right.params.items():
        if type(value) is datetime:
            assert type(left.params[key]) is datetime and left.params[key] == value
        else: assert canonical_json(left.params[key]) == canonical_json(value)


class ReviewIO:
    """Only expected IAM/report/call/new-head/legacy-trace SELECTs and adds."""
    def __init__(self, state):
        self.state = state; self.info = {}; self.log = []; self.heads = []; self.events = []
        self.revision_rows = []; self.revision_commands = []; self.serial = 900
        self.flushed = 0; self.commits = 0

    def execute(self, statement, params=None):
        if params is not None:
            assert str(statement) in {'SELECT pg_advisory_xact_lock(:key)',
                'SELECT pg_advisory_xact_lock_shared(:key)'}
            assert set(params) == {'key'} and type(params['key']) is int
            self.log.append(('lock', str(statement), params['key'])); return NS()
        p = self.state.p
        expected = sql_select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
            m.User.id == p.user_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
            m.Membership.role == p.role)
        expected = expected.add_columns(m.AuthSession).join(m.AuthSession, m.AuthSession.user_id == m.User.id).where(
            m.AuthSession.id == p.session_id, m.AuthSession.revoked.is_(False), m.AuthSession.expires_at > CLOCK)
        if statement._for_update_arg is not None:
            expected = sql_select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
                m.User.id == p.user_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
                m.Membership.role.in_(['admin', 'operator']), m.Membership.role == p.role)
            expected = expected.add_columns(m.AuthSession).join(m.AuthSession, m.AuthSession.user_id == m.User.id).where(
                m.AuthSession.id == p.session_id, m.AuthSession.revoked.is_(False), m.AuthSession.expires_at > CLOCK)
            expected = expected.with_for_update(read=True, of=[m.User, m.Membership, m.AuthSession])
        equal_statement(statement, expected)
        self.log.append(('iam', statement._for_update_arg is not None))
        return NS(first=lambda: (object(), object(), object()) if self.state.authorized else None)

    def scalar(self, statement):
        p = self.state.p; entity = statement.column_descriptions[0]['entity']
        if entity is m.SetupReportDraft:
            expected = sql_select(m.SetupReportDraft).where(m.SetupReportDraft.id == self.state.report.id,
                m.SetupReportDraft.workspace_id == p.workspace_id, m.SetupReportDraft.actor_id == p.user_id)
            if statement._for_update_arg is not None: expected = expected.with_for_update(read=True)
            equal_statement(statement, expected); self.log.append(('report',))
            report = self.state.report
            return report if report.workspace_id == p.workspace_id and report.actor_id == p.user_id else None
        if entity is m.CloudCall:
            params = statement.whereclause.compile().params
            call_id = next(value for value in params.values() if value in self.state.calls)
            expected = sql_select(m.CloudCall).where(m.CloudCall.workspace_id == p.workspace_id, m.CloudCall.id == call_id)
            equal_statement(statement, expected); self.log.append(('call', call_id))
            return self.state.calls[call_id]
        if entity is m.Audit:
            assert statement.column_descriptions[0]['name'] == 'id'
            params = list(statement.whereclause.compile().params.values())
            assert p.workspace_id in params and p.user_id in params and subject.ACTION in params
            return next((event.id for event in self.events if event.workspace_id == p.workspace_id
                and (event.actor_id == p.user_id or event.data.get('actor_id') == p.user_id)), None)
        raise AssertionError('Unexpected scalar query: ' + str(statement))

    def scalars(self, statement):
        p = self.state.p; entity = statement.column_descriptions[0]['entity']
        params = list(statement.whereclause.compile().params.values())
        assert p.workspace_id in params and p.user_id in params
        self.log.append(('inventory', entity.__name__))
        if entity is Head:
            return [head for head in self.heads if head.workspace_id == p.workspace_id and head.actor_id == p.user_id]
        if entity is m.Audit:
            assert subject.ACTION in params
            return [event for event in self.events if event.workspace_id == p.workspace_id
                and (event.actor_id == p.user_id or event.data.get('actor_id') == p.user_id)]
        if entity is Revision:
            assert self.state.report.id in params; return list(self.revision_rows)
        if entity is RevisionCommand:
            assert self.state.report.id in params; return list(self.revision_commands)
        raise AssertionError('Unexpected inventory query: ' + str(statement))

    def add(self, row):
        if isinstance(row, Head): self.heads.append(row)
        elif isinstance(row, m.Audit):
            self.serial += 1; row.id = identity(self.serial); self.events.append(row)
        else: raise AssertionError('Selection cannot create/change a consumer, report, revision, Job or call')

    def flush(self): self.flushed += 1
    def commit(self): self.commits += 1
    def rollback(self): raise AssertionError('No expected positive fixture needs a hidden rollback')
    def connection(self): raise AssertionError('Tests explicitly stub deployment inspection, no DB connection')


def complete_candidate(index=0, profile=profiles.INDEXED):
    selected_scene, selected_response = scene, paid_response
    if profile == profiles.COMPATIBLE:
        from tests.test_report_saved_map_design_consumer_v179 import scene as selected_scene
        from tests.test_report_saved_map_design_consumer_v179 import paid_response as selected_response
    p, body, cap, source, row, *_ = selected_scene()
    if index:
        row.id = identity(181 + index * 1000); row.job_id = identity(182 + index * 1000)
        row.request_id = identity(180 + index * 1000)
        preview = copy.deepcopy(row.data['preview'])
        preview['body']['activity_id'] = row.request_id; preview['activity_id'] = row.request_id
        preview['preview_hash'] = digest({key: value for key, value in preview.items() if key != 'preview_hash'})
        row.data = {**row.data, 'preview': preview}
    call = call_for(p, row); call.id = identity(188 + index * 1000); call.status = 'succeeded'
    row.reserved_call_id = call.id
    saved = selected_response(row); family = profiles.modules(profile)
    inspected = family.native.inspect_response(saved['body'], 200, family.native.STAGE,
        response_complete=True, http_content_encoding=None)
    arguments = inspected['arguments'].encode('utf-8'); prep = row.data['preview']['preparation']
    projection = family.wire.build_reduction_projection(arguments, catalog=source['catalog'],
        allowed_span_refs=source['allowed_span_refs'], context=source['context'],
        input_order=source['snapshot']['input_order'], input_items=source['snapshot']['input_items'],
        indexed=prep['indexed_inputs'], source_snapshot_hash=prep['source_snapshot_hash'])
    manual = manual_value_from_candidate(projection['result'], source['catalog'],
        source['allowed_span_refs'], source['context'])
    response = {key: copy.deepcopy(saved[key]) for key in ('receipt', 'capture', 'descriptor', 'binding')}
    response.update(body_sha256=hashlib.sha256(saved['body']).hexdigest(),
        arguments_sha256=hashlib.sha256(arguments).hexdigest())
    row.status = 'review'
    row.data = {**row.data, 'call_pin': consumer._call_pin(call), 'response': response,
        'projection': projection, 'manual_value': manual}
    return p, source, row, call, saved


@pytest.fixture(params=[profiles.INDEXED, profiles.COMPATIBLE])
def state(monkeypatch, request):
    p, source, row, call, saved = complete_candidate(profile=request.param)
    p.session_id = identity(890)
    report = m.SetupReportDraft(id=row.producer_id, workspace_id=p.workspace_id,
        actor_id=p.user_id, version=source['snapshot']['producer']['revision'],
        status='failed', request_id='fe_' + identity(6), data={'preview': {
            'source_hash': source['snapshot']['source_hash'],
            'map_preparation': {'context': copy.deepcopy(source['context'])}}})
    a = NS(p=p, source=source, row=row, report=report, calls={call.id: call},
        consumers={row.id: row}, responses={row.id: saved}, authorized=True, storage=True,
        human=None, materials=copy.deepcopy(source['snapshot']['materials']),
        catalog=copy.deepcopy(source['catalog']), reads=[], consumer_reads=[])
    a.db = ReviewIO(a)
    monkeypatch.setattr(subject, 'now', lambda: CLOCK)
    monkeypatch.setattr(relations, 'now', lambda: CLOCK)
    monkeypatch.setattr(subject, '_storage_available', lambda db: a.storage)
    def checked(db, actual_p, consumer_id):
        assert db is a.db and actual_p is p
        result = a.consumers.get(consumer_id)
        subject.need(result is not None and result.actor_id == p.user_id
            and result.workspace_id == p.workspace_id, code=404)
        a.consumer_reads.append(consumer_id); return result
    monkeypatch.setattr(subject, '_consumer', checked)
    monkeypatch.setattr(subject, '_revision', lambda db, actual_p, report_id: a.human)
    def report_check(actual, actual_p):
        assert actual is report and actual_p is p; return actual
    monkeypatch.setattr(pipeline, 'check_row', report_check)
    monkeypatch.setattr(pipeline, '_catalog', lambda preview: copy.deepcopy(a.catalog))
    monkeypatch.setattr(handoff, 'material_refs', lambda db, actual_p, actual, lock: copy.deepcopy(a.materials))
    def read(db, actual_p, actual_row, *, recover_saved):
        assert db is a.db and actual_p is p and recover_saved is False
        a.reads.append(actual_row.id); return copy.deepcopy(a.responses[actual_row.id])
    monkeypatch.setattr(consumer, 'runtime', lambda: NS(read_response=read))
    return a


def raw(a, number=800, expected=None, consumer_row=None):
    row = a.row if consumer_row is None else consumer_row
    version = len(a.db.heads[0].data['history']) if a.db.heads else 0
    return json.dumps({'request_id': identity(number), 'expected_version': version if expected is None else expected,
        'consumer_version': row.version}, ensure_ascii=False, indent=1).encode('utf-8')


def select(a, wire=None, consumer_row=None):
    row = a.row if consumer_row is None else consumer_row
    return subject.select_source(a.db, a.p, row.id, raw(a) if wire is None else wire,
        actual_path=subject.path(row.id))


def add_candidate(a):
    _, _, row, call, saved = complete_candidate(1)
    a.consumers[row.id] = row; a.calls[call.id] = call; a.responses[row.id] = saved
    return row


def test_head_points_to_actual_report_not_consumer_and_has_only_one_scope_head():
    table = Head.__table__
    assert any(isinstance(item, UniqueConstraint) and set(item.columns.keys()) == {
        'workspace_id', 'report_id', 'actor_id'} for item in table.constraints)
    assert {key.target_fullname for key in table.c.report_id.foreign_keys} == {'setup_report_drafts.id'}
    assert {key.target_fullname for key in table.c.consumer_id.foreign_keys} == {'report_saved_map_designs_v176.id'}


def test_actual_selection_cold_load_and_original_nonce_reply_preserve_originals(state):
    a = state; original = canonical_json([a.report.data, a.row.data]); wire = raw(a)
    before = subject.detail(a.db, a.p, a.row.id)
    assert before['version'] == 0 and before['selected'] is False and before['can_select'] is True
    result = select(a, wire)
    assert result['report_id'] == a.report.id and result['consumer_id'] == a.row.id
    assert result['version'] == 1 and result['selected'] is True
    assert len(a.db.heads) == len(a.db.events) == 1 and a.db.commits == 0
    head = a.db.heads[0]; subject._check_head(head, a.db.events)
    stored = head.data['history'][0]
    assert stored['exact_body'].encode() == wire and stored['body_sha256'] == hashlib.sha256(wire).hexdigest()
    assert 'consumer_version' not in stored['selected_proof']
    loaded = subject.load_selected_source(a.db, a.p, a.report.id)
    assert set(loaded) == {'basis', 'basis_hash', 'value', 'catalog', 'allowed_span_refs', 'context'}
    assert set(loaded['basis']) == subject.revisions.BASIS_KEYS | {
        'source_kind', 'consumer_id', 'selection_id', 'selection_hash'}
    assert loaded['basis']['source_kind'] == subject.SOURCE_KIND
    assert loaded['basis']['report_revision'] == a.report.version
    assert loaded['basis']['model_result_hash'] == digest(a.row.data['projection']['result'])
    assert loaded['value'] == a.row.data['manual_value'] and loaded['basis']['base_value_hash'] == digest(loaded['value'])
    assert loaded['basis']['selection_hash'] == result['selection_hash']
    assert select(a, wire) == result and len(a.db.heads[0].data['history']) == 1
    recovered = subject.request_result(a.db, a.p, a.row.id, result['request_id'], hashlib.sha256(wire).hexdigest())
    assert recovered['found'] is True and recovered['result'] == result
    missing = subject.request_result(a.db, a.p, a.row.id, identity(899), 'a' * 64)
    assert missing['found'] is False and missing['result'] is None
    assert canonical_json([a.report.data, a.row.data]) == original


def test_explicit_cas_switch_preserves_history_but_saved_v166_draft_blocks_change(state):
    a = state; first_wire = raw(a); first = select(a, first_wire); other = add_candidate(a)
    second_wire = raw(a, 801, consumer_row=other)
    second = select(a, second_wire, other)
    assert second['selection_id'] == first['selection_id'] and second['version'] == 2
    assert second['selection_hash'] != first['selection_hash'] and len(a.db.events) == 2
    assert subject.request_result(a.db, a.p, a.row.id, first['request_id'],
        hashlib.sha256(first_wire).hexdigest())['result'] == first
    subject._check_head(a.db.heads[0], a.db.events)
    a.human = NS(id=identity(950))
    with pytest.raises(HTTPException): select(a, raw(a, 802), a.row)
    assert a.db.heads[0].consumer_id == other.id and len(a.db.events) == 2
    # Same original terminal recovery version is mutable; source proof is not.
    other.version += 1
    loaded = subject.load_selected_source(a.db, a.p, a.report.id)
    assert loaded['basis']['selection_hash'] == second['selection_hash']
    repeated = select(a, raw(a, 803, consumer_row=other), other)
    assert repeated['selection_hash'] == second['selection_hash'] and repeated['version'] == 3


def test_locked_source_loader_never_upgrades_existing_adoption_share_locks(state):
    a = state; select(a); a.db.log.clear()
    loaded = subject.load_selected_source(a.db, a.p, a.report.id, lock=True)
    locks = [entry for entry in a.db.log if entry[0] == 'lock']
    assert loaded['basis']['consumer_id'] == a.row.id and locks
    assert all(entry[1] == 'SELECT pg_advisory_xact_lock_shared(:key)' for entry in locks)
    actor_key = int(digest({'report_object_revision_inventory_v166': a.p.user_id,
        'workspace_id': a.p.workspace_id})[:16], 16) - (1 << 63)
    report_key = int(digest({'report_object_revision_report_v166': a.report.id,
        'workspace_id': a.p.workspace_id})[:16], 16) - (1 << 63)
    assert [entry[2] for entry in locks] == [actor_key, report_key]
    # Explicit selection remains a write/CAS operation and keeps its exclusive
    # inventory lock; this change grants no readonly path mutation authority.
    a.db.log.clear(); select(a, raw(a, 804))
    assert any(entry[0] == 'lock' and entry[1] == 'SELECT pg_advisory_xact_lock(:key)'
        and entry[2] == actor_key for entry in a.db.log)


@pytest.mark.parametrize('fault', ['stale_head', 'stale_consumer', 'same_nonce_other_body', 'wrong_path'])
def test_cas_raw_nonce_and_actual_target_are_not_guessed_from_current_state(state, fault):
    a = state; wire = raw(a); select(a, wire)
    if fault == 'stale_head': damaged = raw(a, 801, expected=0)
    elif fault == 'stale_consumer':
        body = json.loads(raw(a, 801)); body['consumer_version'] += 1; damaged = json.dumps(body).encode()
    elif fault == 'same_nonce_other_body': damaged = wire + b' '
    else:
        with pytest.raises(HTTPException): subject.select_source(a.db, a.p, a.row.id, raw(a, 801), actual_path='/other')
        return
    with pytest.raises(HTTPException): select(a, damaged)
    assert len(a.db.events) == 1 and a.db.heads[0].version == 1


@pytest.mark.parametrize('fault', ['delete_head', 'delete_audit', 'resequence', 'wire', 'proof', 'reply', 'tail'])
def test_resealed_head_or_missing_independent_trace_never_becomes_fresh_or_legacy(state, fault):
    a = state; wire = raw(a); select(a, wire); head = a.db.heads[0]
    if fault == 'delete_head': a.db.heads.clear()
    elif fault == 'delete_audit': a.db.events.clear()
    else:
        command = head.data['history'][0]
        if fault == 'resequence': command['expected_version'] = 1
        elif fault == 'wire': command['exact_body'] += ' '
        elif fault == 'proof': command['selected_proof']['manual_hash'] = 'f' * 64
        elif fault == 'reply': command['result']['consumer_id'] = identity(999)
        else: head.consumer_id = identity(999)
        command['command_hash'] = subject._command_hash(command)
        head.record_hash = digest(subject._snapshot(head))
    with pytest.raises(HTTPException): subject.load_selected_source(a.db, a.p, a.report.id)
    with pytest.raises(HTTPException): subject.request_result(a.db, a.p, a.row.id, identity(800), hashlib.sha256(wire).hexdigest())
    with pytest.raises(HTTPException): select(a, raw(a, 801))


@pytest.mark.parametrize('fault', ['failed', 'queued', 'no_projection', 'projection', 'manual',
    'material_changed', 'catalog_changed', 'context_changed', 'raw_hash', 'call_failed'])
def test_fresh_choice_rechecks_actual_candidate_http_material_and_semantics(state, fault):
    a = state
    if fault in {'failed', 'queued'}: a.row.status = fault
    elif fault == 'no_projection': a.row.data['projection'] = None
    elif fault == 'projection':
        a.row.data['projection']['result']['domain'] += '被篡改'
        a.row.data['projection']['projection_hash'] = digest({k: v for k, v in a.row.data['projection'].items() if k != 'projection_hash'})
    elif fault == 'manual': a.row.data['manual_value']['result']['global_claims'][0]['span_refs'] = ['not-a-source']
    elif fault == 'material_changed': a.materials.append({'changed': True})
    elif fault == 'catalog_changed': a.catalog['descriptor']['source_hash'] = 'f' * 64
    elif fault == 'context_changed': a.report.data['preview']['map_preparation']['context']['changed'] = True
    elif fault == 'raw_hash': a.row.data['response']['body_sha256'] = 'f' * 64
    else: a.calls[a.row.reserved_call_id].status = 'failed'
    with pytest.raises(HTTPException): select(a)
    assert not a.db.heads and not a.db.events


def test_existing_legacy_human_revision_prevents_first_selection_and_viewer_cannot_write(state):
    a = state; a.human = NS(id=identity(951))
    with pytest.raises(HTTPException): select(a)
    assert not a.db.heads
    a.human = None; a.p.role = 'viewer'
    assert subject.detail(a.db, a.p, a.row.id)['can_select'] is False
    with pytest.raises(HTTPException) as error: select(a)
    assert error.value.status_code == 403
    a.authorized = False
    with pytest.raises(HTTPException) as error: subject.detail(a.db, a.p, a.row.id)
    assert error.value.status_code == 403


@pytest.mark.parametrize('trace', ['audit', 'revision', 'command'])
def test_missing_table_with_new_family_trace_fails_closed_instead_of_legacy(state, trace):
    a = state; a.storage = False
    if trace == 'audit':
        a.db.events.append(m.Audit(id=identity(960), workspace_id=a.p.workspace_id, actor_id=a.p.user_id,
            action=subject.ACTION, object_id=identity(961), data={'actor_id': a.p.user_id}))
    elif trace == 'revision':
        a.db.revision_rows.append(NS(data={'basis': {'source_kind': subject.SOURCE_KIND}}))
    else:
        a.db.revision_commands.append(NS(data={'record_snapshot': {'data': {'basis': {'source_kind': subject.SOURCE_KIND}}}}))
    with pytest.raises(HTTPException) as error: subject.load_selected_source(a.db, a.p, a.report.id)
    assert error.value.status_code == 409
    assert not any(item == ('inventory', Head.__name__) for item in a.db.log)


def test_undeployed_storage_allows_only_true_legacy_loader_but_new_apis_require_upgrade(state):
    a = state; a.storage = False
    assert subject.load_selected_source(a.db, a.p, a.report.id) is None
    for operation in (lambda: subject.detail(a.db, a.p, a.row.id), lambda: select(a),
        lambda: subject.request_result(a.db, a.p, a.row.id, identity(800), 'a' * 64)):
        with pytest.raises(HTTPException) as error: operation()
        assert error.value.status_code == 503
    assert not any(item == ('inventory', Head.__name__) for item in a.db.log)


def test_deployed_missing_head_with_new_revision_basis_cannot_fall_back(state):
    a = state
    a.db.revision_rows.append(NS(data={'basis': {'source_kind': subject.SOURCE_KIND}}))
    with pytest.raises(HTTPException): subject.load_selected_source(a.db, a.p, a.report.id)


def test_real_storage_probe_caches_only_inspected_bool_and_does_not_swallow_db_error(monkeypatch):
    connection = object(); calls = []
    db = NS(info={}, connection=lambda: connection)
    def inspect(actual):
        assert actual is connection
        return NS(has_table=lambda name: calls.append(name) or False)
    monkeypatch.setattr(subject, 'inspect', inspect)
    assert subject._storage_available(db) is False and subject._storage_available(db) is False
    assert calls == [Head.__tablename__]
    def fail(_): raise OperationalError('synthetic', {}, RuntimeError('DB unavailable'))
    monkeypatch.setattr(subject, 'inspect', fail)
    with pytest.raises(OperationalError): subject._storage_available(NS(info={}, connection=lambda: connection))


@pytest.mark.parametrize('body', [b'', b'[]', b'{}', b'{', b'\xff',
    b'{"request_id":"x","request_id":"y"}', b'{"x":NaN}',
    json.dumps({'request_id': identity(800), 'expected_version': True, 'consumer_version': 1}).encode(),
    json.dumps({'request_id': identity(800), 'expected_version': 0, 'consumer_version': 1.0}).encode(),
    json.dumps({'request_id': identity(800), 'expected_version': 0, 'consumer_version': 1, 'consent': True}).encode()])
def test_raw_closed_command_does_not_coerce_repair_or_inherit_consumer_consent(body):
    with pytest.raises(ValueError): subject.parse_wire(body)
