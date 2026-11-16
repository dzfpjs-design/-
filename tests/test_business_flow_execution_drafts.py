"""Actual unfinished-draft parser/store with explicit synthetic SQL boundaries.

No parser, inventory, history, IAM or save/recovery implementation is replaced.
Only SQL and qualifications.get are boundary adapters; the qualification below
is invented inline and establishes no report/source/adoption/execution proof.
These tests do not execute PostgreSQL, transactions, concurrency, HTTP, a browser
or a model. Root separately verifies real persistence, rollback and user flows.
"""
import copy
import hashlib
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError

from generic_agents import business_flow_execution_drafts as drafts
from generic_agents import models as m
from generic_agents.business_flow_execution_draft_models import BusinessFlowExecutionDraft as Draft
from generic_agents.business_flow_execution_draft_models import BusinessFlowExecutionDraftCommand as Command
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests import test_business_flow_review_store as base
from tests.test_report_saved_map_design_consumer_v176 import equal_statement, matches_statement


identity = base.identity
wire = base.wire
NONCES = ('first', 'second', 'third', 'unknown', 'other', 'new', 'missing', 'orphan', 'failed')


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure execution drafts cannot connect or launch processes')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


class IO(base.IO):
    """Exact queries, including reverse nonce traces; unknown SQL is an error."""
    def __init__(self, context):
        super().__init__(context)
        self.additions = []; self.order = []

    def scalars(self, statement):
        if self.failure is not None: raise self.failure
        p = self.c.p
        choices = [
            (select(Draft).where(Draft.workspace_id == p.workspace_id,
                Draft.actor_id == p.user_id).execution_options(populate_existing=True),
                [row for row in self.drafts if row.workspace_id == p.workspace_id and row.actor_id == p.user_id]),
            (select(Command).where(Command.workspace_id == p.workspace_id,
                Command.actor_id == p.user_id).execution_options(populate_existing=True),
                [row for row in self.commands if row.workspace_id == p.workspace_id and row.actor_id == p.user_id]),
            (select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action.in_([drafts.COMMAND_AUDIT, drafts.STATE_AUDIT]),
                or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id)),
                [row for row in self.events if row.workspace_id == p.workspace_id
                    and row.action in {drafts.COMMAND_AUDIT, drafts.STATE_AUDIT}
                    and (row.actor_id == p.user_id or row.data.get('actor_id') == p.user_id)])]
        for label in NONCES:
            nonce = identity(label)
            choices.extend([
                (select(Command).where(Command.workspace_id == p.workspace_id,
                    Command.request_id == nonce).execution_options(populate_existing=True),
                    [row for row in self.commands if row.workspace_id == p.workspace_id and row.request_id == nonce]),
                (select(Draft).where(Draft.workspace_id == p.workspace_id,
                    or_(Draft.request_id == nonce, Draft.data['command_refs'].contains([{'request_id': nonce}]))),
                    [row for row in self.drafts if row.workspace_id == p.workspace_id and
                        (row.request_id == nonce or any(ref['request_id'] == nonce for ref in row.data['command_refs']))]),
                (select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                    m.Audit.action.in_([drafts.COMMAND_AUDIT, drafts.STATE_AUDIT]),
                    m.Audit.data['request_id'].as_string() == nonce),
                    [row for row in self.events if row.workspace_id == p.workspace_id
                        and row.action in {drafts.COMMAND_AUDIT, drafts.STATE_AUDIT}
                        and row.data.get('request_id') == nonce])])
        for expected, result in choices:
            if matches_statement(statement, expected):
                equal_statement(statement, expected); return list(result)
        raise AssertionError('Unexpected execution-draft SQL')

    def scalar(self, statement):
        raise AssertionError('No source, provider, Attempt or current configuration query is allowed')

    def add(self, row):
        self.additions.append(type(row).__name__); self.order.append(('add', type(row).__name__))
        if isinstance(row, Draft):
            if all(saved is not row for saved in self.drafts): self.drafts.append(row)
        elif isinstance(row, Command): self.commands.append(row)
        elif isinstance(row, m.Audit):
            row.id = identity('execution-draft-audit-' + str(len(self.events))); self.events.append(row)
        else: raise AssertionError('Draft saving cannot create any other domain record')

    def flush(self):
        self.order.append(('flush', None)); super().flush()


@pytest.fixture
def scene(monkeypatch):
    c = base.scene(); c.qualification_id = identity('qualification'); c.flow_id = 'native_flow'
    c.qualification = {'id': c.qualification_id, 'reviewed_by': c.p.user_id, 'status': 'qualified',
        'human_review': 'author_self_reviewed', 'qualification_hash': 'a' * 64,
        'value': {'business': {'flows': [{'flow_id': c.flow_id}]}}, **drafts.FLAGS}
    c.qualification_reads = []; c.qualification_available = True; db = IO(c)
    def qualification_get(actual_db, actor, qualification_id):
        assert actual_db is db
        c.qualification_reads.append((actor.user_id, actor.workspace_id, qualification_id))
        if not c.qualification_available:
            raise HTTPException(404, 'synthetic missing qualification')
        if actor.user_id != c.qualification['reviewed_by'] or actor.workspace_id != c.attempt.workspace_id:
            raise HTTPException(404, 'synthetic non-owned qualification')
        if qualification_id != c.qualification_id:
            raise HTTPException(404, 'synthetic other qualification')
        return copy.deepcopy(c.qualification)
    monkeypatch.setattr(drafts.qualifications, 'get', qualification_get)
    return c, db


def value():
    return {'business_name': '', 'execution_bindings': {},
        'buffers': {'未完成节点': {'raw_input': '{尚未完成', 'integer': 1, 'decimal': 1.0, 'flag': True}},
        'role_bindings': {}, 'node_actions': {}, 'external_node_ids': []}


def body(c, label='first', *, version=0, supplied=None):
    return {'schema': drafts.COMMAND_SCHEMA, 'request_id': identity(label),
        'qualification_id': c.qualification_id, 'qualification_hash': c.qualification['qualification_hash'],
        'flow_id': c.flow_id, 'expected_version': version, 'value': value() if supplied is None else supplied}


def save(c, db, request):
    return drafts.save(db, c.p, wire(request))


def recover(c, db, request):
    return drafts.recover(db, c.p, request['request_id'], hashlib.sha256(wire(request)).hexdigest())


def test_first_get_is_a_version_zero_view_and_does_not_create_or_default_a_draft(scene):
    c, db = scene
    result = drafts.get(db, c.p, c.qualification_id, c.flow_id)
    assert result['schema'] == drafts.VIEW and result['version'] == 0 and result['saved'] is False
    assert result['id'] is None and result['value'] is None and result['value_hash'] is None and result['history'] == []
    assert all(result[key] is False for key in drafts.FLAGS)
    assert db.additions == [] and db.flushes == db.commits == 0


def test_incomplete_input_preserves_float_integer_bool_and_raw_editor_buffers(scene):
    c, db = scene; request = body(c); before = canonical_json(request)
    parsed = drafts.parse(wire(request)); result = save(c, db, request)
    stored = result['draft']
    assert canonical_json(parsed) == before
    assert canonical_json(stored['value']) == canonical_json(request['value'])
    numbers = stored['value']['buffers']['未完成节点']
    assert type(numbers['integer']) is int and type(numbers['decimal']) is float and type(numbers['flag']) is bool
    assert numbers['raw_input'] == '{尚未完成' and stored['value']['execution_bindings'] == {}
    assert stored['version'] == 1 and stored['value_hash'] == digest(request['value'])
    assert drafts.get(db, c.p, c.qualification_id, c.flow_id) == stored
    assert len(db.drafts) == len(db.commands) == 1 and len(db.events) == 2
    assert db.order[:4] == [('add', 'BusinessFlowExecutionDraft'), ('flush', None),
        ('add', 'BusinessFlowExecutionDraftCommand'), ('flush', None)]


@pytest.mark.parametrize('bad_version', [True, False, 0.0, 1.0, '0', -1, None])
def test_version_is_a_strict_nonnegative_integer(scene, bad_version):
    c, db = scene; request = body(c); request['expected_version'] = bad_version
    with pytest.raises(ValueError): drafts.parse(wire(request))
    assert not db.additions


def test_parse_preserves_version_at_history_capacity_for_original_request_checks(scene):
    c, db = scene
    request = body(c, version=1000)
    assert drafts.parse(wire(request)) == request
    assert not db.additions and db.commits == 0
    # Capacity is a fresh-save gate, not permission for the parser to discard
    # an otherwise valid command before exact original-nonce reconciliation.


@pytest.mark.parametrize('fault', ['extra-body', 'missing-body', 'extra-value', 'missing-value', 'wrong-value-type',
    'wrong-schema', 'upper-uuid', 'wrong-hash', 'wrong-flow', 'nonfinite', 'nested-duplicate', 'outer-duplicate', 'utf8', 'oversized'])
def test_exact_parser_rejects_invalid_shapes_without_coercion_or_truncation(scene, fault):
    c, db = scene; request = body(c)
    if fault == 'extra-body': request['can_execute'] = False
    elif fault == 'missing-body': request.pop('qualification_hash')
    elif fault == 'extra-value': request['value']['can_apply'] = True
    elif fault == 'missing-value': request['value'].pop('buffers')
    elif fault == 'wrong-value-type': request['value']['role_bindings'] = []
    elif fault == 'wrong-schema': request['schema'] = drafts.STATE
    elif fault == 'upper-uuid': request['request_id'] = request['request_id'].upper()
    elif fault == 'wrong-hash': request['qualification_hash'] = 'G' * 64
    elif fault == 'wrong-flow': request['flow_id'] = 'not/a/flow'
    elif fault == 'nonfinite':
        raw = wire(request).replace(b'"integer": 1', b'"integer": 1e400')
    elif fault == 'nested-duplicate':
        raw = wire(request).replace(b'"integer": 1', b'"integer": 1, "integer": 2')
    elif fault == 'outer-duplicate': raw = wire(request)[:-1] + b',"expected_version":0}'
    elif fault == 'utf8': raw = b'\xff'
    elif fault == 'oversized': raw = b' ' * (drafts.MAX_BYTES + 1)
    if fault not in {'nonfinite', 'nested-duplicate', 'outer-duplicate', 'utf8', 'oversized'}: raw = wire(request)
    with pytest.raises(ValueError): drafts.parse(raw)
    assert db.additions == []


def test_original_request_returns_exact_old_result_before_any_current_qualification(scene):
    c, db = scene; first = body(c); original = save(c, db, first)
    second_value = value(); second_value['buffers']['下一项'] = '第二次保存'
    second = body(c, 'second', version=1, supplied=second_value); latest = save(c, db, second)
    assert latest['draft']['version'] == 2
    c.qualification_reads.clear(); c.qualification_available = False
    before = (db.commits, len(db.additions), len(db.commands), len(db.events))
    assert save(c, db, first) == original
    response = recover(c, db, first)
    assert response['found'] is True and response['result'] == original and response['result'] != latest
    assert response['body_sha256'] == hashlib.sha256(wire(first)).hexdigest()
    assert not c.qualification_reads and before == (db.commits, len(db.additions), len(db.commands), len(db.events))


@pytest.mark.parametrize('fault', ['float-to-int', 'whitespace', 'qualification', 'flow', 'value', 'version'])
def test_same_nonce_cannot_be_rebound_even_when_python_values_compare_equal(scene, fault):
    c, db = scene; request = body(c); original = save(c, db, request); changed = copy.deepcopy(request)
    if fault == 'float-to-int': changed['value']['buffers']['未完成节点']['decimal'] = 1
    elif fault == 'qualification': changed['qualification_id'] = identity('another-qualification')
    elif fault == 'flow': changed['flow_id'] = 'other_flow'
    elif fault == 'value': changed['value']['business_name'] = 'changed'
    elif fault == 'version': changed['expected_version'] = 1
    raw = wire(changed) + (b'\n' if fault == 'whitespace' else b'')
    if fault == 'float-to-int': assert request == changed and wire(request) != raw
    c.qualification_reads.clear()
    with pytest.raises(HTTPException) as caught: drafts.save(db, c.p, raw)
    assert caught.value.status_code == 409 and not c.qualification_reads
    assert recover(c, db, request)['result'] == original and len(db.commands) == 1


def test_same_workspace_foreign_actor_nonce_is_conflict_and_other_workspace_cannot_read_it(scene):
    c, db = scene; request = body(c); original = save(c, db, request); first_actor = c.p
    c.p = NS(**{**vars(first_actor), 'user_id': identity('foreign-actor')})
    with pytest.raises(HTTPException) as caught: recover(c, db, request)
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException): save(c, db, request)
    c.p = NS(**{**vars(first_actor), 'workspace_id': identity('other-workspace')})
    response = recover(c, db, request)
    assert response['found'] is False and response['result'] is None
    c.p = first_actor
    assert recover(c, db, request)['result'] == original


def test_proven_cas_conflict_is_only_before_any_add_and_is_tied_to_original_nonce_sha(scene):
    c, db = scene; first = body(c); save(c, db, first)
    request = body(c, 'second', version=0); before = (len(db.additions), len(db.commands), len(db.events), db.commits)
    with pytest.raises(HTTPException) as caught: save(c, db, request)
    assert caught.value.detail == {'code': 'business_flow_execution_draft_version_conflict', 'not_applied': True,
        'request_id': request['request_id'], 'body_sha256': hashlib.sha256(wire(request)).hexdigest(), 'current_version': 1}
    assert before == (len(db.additions), len(db.commands), len(db.events), db.commits)
    request['expected_version'] = 1
    assert save(c, db, request)['draft']['version'] == 2


def test_qualification_failure_is_not_an_applied_or_not_applied_receipt(scene):
    c, db = scene; c.qualification['qualification_hash'] = 'b' * 64
    request = body(c); request['qualification_hash'] = 'a' * 64
    with pytest.raises(HTTPException) as caught: save(c, db, request)
    assert caught.value.status_code == 409 and not isinstance(caught.value.detail, dict)
    assert not db.additions and db.commits == 0


@pytest.mark.parametrize('loss', ['state', 'command', 'command-audit', 'state-audit', 'both-audits',
    'state+command', 'state+audits', 'command+audits', 'double-command-audit', 'double-state-audit', 'orphan-audit'])
def test_missing_or_duplicate_independent_traces_never_become_missing_or_new(scene, loss):
    c, db = scene; request = body(c); save(c, db, request)
    if loss in {'state', 'state+command', 'state+audits'}: db.drafts.clear()
    if loss in {'command', 'state+command', 'command+audits'}: db.commands.clear()
    if loss in {'both-audits', 'state+audits', 'command+audits'}: db.events.clear()
    elif loss == 'command-audit': db.events[:] = [e for e in db.events if e.action != drafts.COMMAND_AUDIT]
    elif loss == 'state-audit': db.events[:] = [e for e in db.events if e.action != drafts.STATE_AUDIT]
    elif loss.startswith('double-'):
        action = drafts.COMMAND_AUDIT if loss == 'double-command-audit' else drafts.STATE_AUDIT
        original = next(e for e in db.events if e.action == action)
        db.events.append(m.Audit(id=identity('duplicate-audit'), workspace_id=original.workspace_id,
            actor_id=original.actor_id, object_id=original.object_id, action=original.action, data=copy.deepcopy(original.data)))
    elif loss == 'orphan-audit':
        event = db.events[0]; data = copy.deepcopy(event.data); data['draft_id'] = identity('orphan-draft')
        db.events.append(m.Audit(id=identity('orphan-audit'), workspace_id=event.workspace_id,
            actor_id=event.actor_id, object_id=event.object_id, action=event.action, data=data))
    before = len(db.additions)
    for operation in (lambda: recover(c, db, request),
            lambda: drafts.get(db, c.p, c.qualification_id, c.flow_id),
            lambda: save(c, db, body(c, 'new', version=0))):
        with pytest.raises(HTTPException) as caught: operation()
        assert caught.value.status_code == 409 and not isinstance(caught.value.detail, dict)
    assert len(db.additions) == before


def test_resealed_forged_original_result_cannot_replace_exact_raw_body_or_history(scene):
    c, db = scene; request = body(c); save(c, db, request); command = db.commands[0]
    command.data = copy.deepcopy(command.data)
    command.data['result']['draft']['value']['buffers']['伪造'] = 'changed'
    command.command_hash = digest(drafts._command_pin(command))
    event = next(e for e in db.events if e.action == drafts.COMMAND_AUDIT)
    event.data = drafts._command_audit(command)
    with pytest.raises(HTTPException): recover(c, db, request)
    assert len(db.commands) == 1


def test_read_role_can_recover_own_saved_work_but_cannot_save_a_new_version(scene):
    c, db = scene; request = body(c); result = save(c, db, request)
    c.p = NS(**{**vars(c.p), 'role': 'viewer'}); db.membership_role = 'viewer'
    assert recover(c, db, request)['result'] == result
    assert save(c, db, request) == result
    with pytest.raises(HTTPException) as caught: save(c, db, body(c, 'second', version=1))
    assert caught.value.status_code == 403 and len(db.commands) == 1


def test_sql_exception_propagates_and_is_never_a_conflict_or_false_missing(scene):
    c, db = scene; request = body(c)
    db.failure = OperationalError('synthetic sql', {}, RuntimeError('synthetic DB failure'))
    with pytest.raises(OperationalError): save(c, db, request)
    with pytest.raises(OperationalError): recover(c, db, request)
    assert not db.additions and db.commits == 0


def test_failure_after_row_add_is_not_misreported_as_not_applied(scene, monkeypatch):
    c, db = scene; request = body(c)
    def failed_flush(): raise OperationalError('synthetic flush', {}, RuntimeError('synthetic DB failure'))
    monkeypatch.setattr(db, 'flush', failed_flush)
    with pytest.raises(OperationalError): save(c, db, request)
    assert db.additions == ['BusinessFlowExecutionDraft'] and db.commits == 0
    # This fake has no transaction/rollback. It must not pretend to prove
    # atomicity, successful persistence, or permission to issue a new nonce.
