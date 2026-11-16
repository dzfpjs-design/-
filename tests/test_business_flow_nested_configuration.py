"""Actual private nested-draft store with explicit SQL/qualification IO adapters.

No parser, inventory, history, IAM or store implementation is mocked. Source
qualification is a public synthetic boundary, not report/approval authority.
Root separately runs real PostgreSQL/ASGI, typed persistence and cold restart.
"""
import copy
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError

from generic_agents import business_flow_nested_configuration_store as store
from generic_agents import business_flow_execution_drafts as previous
from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests import test_business_flow_review_store as base
from tests.test_business_flow_nested_execution_v195 import scene as plan_scene
from tests.test_report_saved_map_design_consumer_v176 import equal_statement, matches_statement
from tests.test_business_flow_execution_drafts import no_external_io


Draft, Command = store.Draft, store.Command
identity, wire = base.identity, base.wire
NONCES = ('first', 'second', 'third', 'unknown', 'other', 'new', 'missing', 'orphan', 'failed')


class IO(base.IO):
    """Exact production-query shapes, reverse nonce traces and current IAM."""
    def __init__(self, context):
        super().__init__(context); self.additions = []; self.order = []

    def scalars(self, statement):
        if self.failure is not None: raise self.failure
        p = self.c.p
        choices = [
            (select(Draft).where(Draft.workspace_id == p.workspace_id, Draft.actor_id == p.user_id)
                .execution_options(populate_existing=True),
                [row for row in self.drafts if row.workspace_id == p.workspace_id and row.actor_id == p.user_id]),
            (select(Command).where(Command.workspace_id == p.workspace_id, Command.actor_id == p.user_id)
                .execution_options(populate_existing=True),
                [row for row in self.commands if row.workspace_id == p.workspace_id and row.actor_id == p.user_id]),
            (select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action.in_([store.COMMAND_AUDIT, store.STATE_AUDIT]),
                or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id)),
                [row for row in self.events if row.workspace_id == p.workspace_id
                    and row.action in {store.COMMAND_AUDIT, store.STATE_AUDIT}
                    and (row.actor_id == p.user_id or row.data.get('actor_id') == p.user_id)])]
        for label in NONCES:
            nonce = identity(label)
            choices.extend([
                (select(Command).where(Command.workspace_id == p.workspace_id, Command.request_id == nonce)
                    .execution_options(populate_existing=True),
                    [row for row in self.commands if row.workspace_id == p.workspace_id and row.request_id == nonce]),
                (select(Draft).where(Draft.workspace_id == p.workspace_id,
                    or_(Draft.request_id == nonce, Draft.data['command_refs'].contains([{'request_id': nonce}]))),
                    [row for row in self.drafts if row.workspace_id == p.workspace_id and
                        (row.request_id == nonce or any(ref['request_id'] == nonce for ref in row.data['command_refs']))]),
                (select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                    m.Audit.action.in_([store.COMMAND_AUDIT, store.STATE_AUDIT]), m.Audit.data['request_id'].as_string() == nonce),
                    [row for row in self.events if row.workspace_id == p.workspace_id
                        and row.action in {store.COMMAND_AUDIT, store.STATE_AUDIT} and row.data.get('request_id') == nonce])])
        for expected, result in choices:
            if matches_statement(statement, expected):
                equal_statement(statement, expected); return list(result)
        raise AssertionError('Unexpected nested configuration query')

    def scalar(self, statement):
        raise AssertionError('Draft storage must not read an original file, provider, current configuration or execution source')

    def add(self, row):
        self.additions.append(type(row).__name__); self.order.append(('add', type(row).__name__))
        if isinstance(row, Draft):
            if all(item is not row for item in self.drafts): self.drafts.append(row)
        elif isinstance(row, Command): self.commands.append(row)
        elif isinstance(row, m.Audit):
            row.id = identity('nested-draft-audit-' + str(len(self.events))); self.events.append(row)
        else: raise AssertionError('Only draft, command and independent Audits may be added')

    def flush(self):
        self.order.append(('flush', None)); super().flush()


@pytest.fixture
def scene(monkeypatch):
    c = base.scene(); c.qualification_id = identity('nested-qualification')
    plan, _, _, _, _ = plan_scene()
    other = copy.deepcopy(plan['business']['flows'][0]); other['name'] += '另一条流程'
    plan['business']['flows'].append(other)
    c.qualification = {'schema': store.nested.RESULT, 'id': c.qualification_id,
        'qualification_hash': 'a' * 64, 'reviewed_by': c.p.user_id, 'status': 'engineering_reviewed',
        **store.nested.mode_fields('engineering_simulation'), 'value': plan,
        'source_binding': {'schema': 'business-flow-nested-review-source-v193',
            'value_schema': store.nested.plans.PLAN, 'generation_profile': 'flow-v193'}, **store.FLAGS}
    c.qualification_reads = []; c.qualification_available = True; db = IO(c)
    def qualification_boundary(actual_db, actor, qualification_id):
        assert actual_db is db
        c.qualification_reads.append((actor.user_id, actor.workspace_id, qualification_id))
        if not c.qualification_available: raise HTTPException(404, 'synthetic qualification temporarily unavailable')
        if qualification_id != c.qualification_id: raise HTTPException(404, 'synthetic other qualification')
        return copy.deepcopy(c.qualification)
    monkeypatch.setattr(store.qualifications, 'get', qualification_boundary)
    return c, db


def value():
    flow = {'flow_index': 0, 'business_name': '', 'execution_bindings': None, 'role_bindings': {},
        'node_actions': {}, 'external_node_ids': [], 'entry_mode': None,
        'buffers': {'原数字': '1.', '未完成输入': '{还没写完', '多行': '第一行\n第二行',
            'integer': 1, 'decimal': 1.0, 'negative_zero': -0.0, 'flag': False, 'unset': None,
            'ports': ['相同名称', '相同名称', '项内\n换行']}}
    other = copy.deepcopy(flow); other['flow_index'] = 1; other['business_name'] = '另一流程尚未配置'
    return {'selected_flow_index': None, 'causal_bindings': None, 'flows': [flow, other, copy.deepcopy(flow)],
        'buffers': {'全局入口': '', '数字中间文字': '-'}, 'recovery': [{'删除的端口': '恢复\n原文'}, {'删除的端口': '恢复\n原文'}]}


def body(c, label='first', *, version=0):
    return {'schema': store.COMMAND_SCHEMA, 'request_id': identity(label), 'qualification_id': c.qualification_id,
        'qualification_hash': c.qualification['qualification_hash'], 'expected_version': version,
        'source_plan_hash': digest(c.qualification['value']), 'scope_binding_hash': 'b' * 64, 'value': value()}


def save(c, db, request): return store.save(db, c.p, wire(request))
def recover(c, db, request): return store.recover(db, c.p, request['request_id'], hashlib.sha256(wire(request)).hexdigest())


def test_initial_get_is_real_qualification_bound_but_creates_no_draft_or_fictional_value(scene):
    c, db = scene; result = store.get(db, c.p, c.qualification_id)
    assert result['schema'] == store.VIEW and result['version'] == 0 and result['saved'] is False
    assert result['id'] is result['value'] is result['value_hash'] is result['saved_at'] is result['scope_binding_hash'] is None
    assert result['history'] == [] and result['source_plan_hash'] == digest(c.qualification['value'])
    assert result['review_mode'] == 'engineering_simulation' and result['simulation_only'] is True
    assert result['current_source_checked'] is result['scope_binding_checked'] is False
    assert all(result[key] is False for key in store.FLAGS)
    assert len(result) == 23 and db.additions == [] and db.commits == db.flushes == 0


def test_incomplete_whole_plan_preserves_ordinals_duplicates_newlines_numeric_text_and_typed_numbers(scene):
    c, db = scene; request = body(c); original = canonical_json(request)
    assert canonical_json(store.parse(wire(request))) == original
    result = save(c, db, request); stored = result['draft']
    assert canonical_json(stored['value']) == canonical_json(request['value'])
    buffers = stored['value']['flows'][0]['buffers']
    assert type(buffers['integer']) is int and type(buffers['decimal']) is float and buffers['negative_zero'] == -0.0
    assert canonical_json(buffers['negative_zero']) == '-0.0'
    assert type(buffers['flag']) is bool and buffers['unset'] is None and buffers['原数字'] == '1.'
    assert buffers['ports'] == ['相同名称', '相同名称', '项内\n换行']
    assert [flow['flow_index'] for flow in stored['value']['flows']] == [0, 1, 0]
    assert store.get(db, c.p, c.qualification_id) == stored
    assert len(db.drafts) == len(db.commands) == 1 and len(db.events) == 2
    assert db.order[:4] == [('add', 'BusinessFlowNestedConfigurationDraft'), ('flush', None),
        ('add', 'BusinessFlowNestedConfigurationCommand'), ('flush', None)]


@pytest.mark.parametrize('actor_id', ['', '00000000-0000-4000-8000-000000000002'])
def test_optional_extractor_selection_survives_exact_save_and_readback(scene, actor_id):
    c, db = scene
    request = body(c)
    selection = [{'capability_id': '00000000-0000-4000-8000-000000000001',
        'capability_hash': 'a' * 64, 'actor_id': actor_id}]
    request['value']['flows'][0]['observation_extractors'] = selection
    stored = save(c, db, request)['draft']
    assert canonical_json(stored['value']) == canonical_json(request['value'])
    assert store.get(db, c.p, c.qualification_id) == stored
    assert 'observation_extractors' not in stored['value']['flows'][1]


@pytest.mark.parametrize('selection', [[], None, [{'capability_id': 'invalid'}]])
def test_optional_extractor_selection_must_be_complete_when_present(scene, selection):
    c, db = scene
    request = body(c)
    request['value']['flows'][0]['observation_extractors'] = selection
    with pytest.raises(ValueError):
        store.parse(wire(request))
    assert db.additions == []


@pytest.mark.parametrize('bad', [True, False, '0', 0.0, 1.0, -1, None])
def test_expected_version_is_strict_nonnegative_and_does_not_consume_a_nonce(scene, bad):
    c, db = scene; request = body(c); request['expected_version'] = bad
    with pytest.raises(ValueError): store.parse(wire(request))
    assert db.additions == []


def test_parse_preserves_history_capacity_version_for_exact_original_request_checks(scene):
    c, _ = scene; request = body(c, version=1000)
    assert store.parse(wire(request)) == request


@pytest.mark.parametrize('fault', ['extra_body', 'missing_body', 'old_marker', 'flow_id', 'extra_value',
    'missing_value', 'flow_extra', 'bool_flow_index', 'bad_entry_type', 'selected_bool', 'upper_uuid',
    'hash', 'nested_duplicate', 'outer_duplicate', 'nonfinite', 'depth', 'oversize'])
def test_closed_raw_json_has_no_coercion_silent_defaults_or_truncation(scene, fault):
    c, db = scene; request = body(c)
    if fault == 'extra_body': request['can_apply'] = True
    elif fault == 'missing_body': request.pop('scope_binding_hash')
    elif fault == 'old_marker': request['schema'] = previous.COMMAND_SCHEMA
    elif fault == 'flow_id': request['flow_id'] = 'not_an_index'
    elif fault == 'extra_value': request['value']['can_execute'] = True
    elif fault == 'missing_value': request['value'].pop('recovery')
    elif fault == 'flow_extra': request['value']['flows'][0]['approved'] = True
    elif fault == 'bool_flow_index': request['value']['flows'][0]['flow_index'] = True
    elif fault == 'bad_entry_type': request['value']['flows'][0]['entry_mode'] = False
    elif fault == 'selected_bool': request['value']['selected_flow_index'] = False
    elif fault == 'upper_uuid': request['request_id'] = request['request_id'].upper()
    elif fault == 'hash': request['source_plan_hash'] = 'G' * 64
    elif fault == 'depth':
        nested = request['value']['buffers']
        for _ in range(42): nested['deep'] = {}; nested = nested['deep']
    raw = wire(request)
    if fault == 'nested_duplicate': raw = raw.replace(b'"integer": 1', b'"integer": 1, "integer": 2')
    elif fault == 'outer_duplicate': raw = raw[:-1] + b',"expected_version":0}'
    elif fault == 'nonfinite': raw = raw.replace(b'"integer": 1', b'"integer": 1e400')
    elif fault == 'oversize': raw = b' ' * (store.MAX_BYTES + 1)
    with pytest.raises(ValueError): store.parse(raw)
    assert not db.additions and db.commits == 0


def test_scope_hash_is_a_correctable_declaration_and_original_reply_remains_immutable(scene):
    c, db = scene; first = body(c); original = save(c, db, first)
    second = body(c, 'second', version=1); second['scope_binding_hash'] = 'c' * 64
    second['value']['buffers']['后续修改'] = '没有覆盖第一版'
    latest = save(c, db, second)
    assert latest['draft']['scope_binding_hash'] == 'c' * 64 and latest['draft']['version'] == 2
    assert latest['draft']['scope_binding_checked'] is False
    assert [command.scope_binding_hash for command in db.commands] == ['b' * 64, 'c' * 64]
    assert store.get(db, c.p, c.qualification_id) == latest['draft']
    c.qualification_available = False; c.qualification_reads.clear()
    before = (db.commits, len(db.additions), len(db.events))
    assert save(c, db, first) == original and recover(c, db, first)['result'] == original
    assert recover(c, db, second)['result'] == latest
    assert not c.qualification_reads and before == (db.commits, len(db.additions), len(db.events))


@pytest.mark.parametrize('fault', ['bytes', 'number_type', 'qualification', 'scope', 'source_plan', 'value'])
def test_same_nonce_cannot_change_body_or_typed_value_before_original_result_lookup(scene, fault):
    c, db = scene; first = body(c); original = save(c, db, first); altered = copy.deepcopy(first)
    if fault == 'number_type': altered['value']['flows'][0]['buffers']['decimal'] = 1
    elif fault == 'qualification': altered['qualification_id'] = identity('other-qualification')
    elif fault == 'scope': altered['scope_binding_hash'] = 'c' * 64
    elif fault == 'source_plan': altered['source_plan_hash'] = 'd' * 64
    elif fault == 'value': altered['value']['buffers']['changed'] = True
    raw = wire(altered) + (b'\n' if fault == 'bytes' else b'')
    c.qualification_reads.clear()
    with pytest.raises(HTTPException) as error: store.save(db, c.p, raw)
    assert error.value.status_code == 409 and not c.qualification_reads
    assert recover(c, db, first)['result'] == original and len(db.commands) == 1


def test_only_proven_preinsert_cas_conflict_has_explicit_nonce_sha_and_not_applied(scene):
    c, db = scene; save(c, db, body(c)); second = body(c, 'second'); before = len(db.additions)
    with pytest.raises(HTTPException) as error: save(c, db, second)
    assert error.value.detail == {'code': 'business_flow_nested_configuration_version_conflict', 'not_applied': True,
        'request_id': second['request_id'], 'body_sha256': hashlib.sha256(wire(second)).hexdigest(), 'current_version': 1}
    assert len(db.additions) == before and len(db.commands) == 1
    second['expected_version'] = 1
    assert save(c, db, second)['draft']['version'] == 2


@pytest.mark.parametrize('fault', ['owner', 'old_schema', 'false_human', 'simulation', 'wrong_source', 'wrong_hash', 'foreign_index'])
def test_real_qualification_boundary_does_not_admit_an_old_or_unowned_source(scene, fault):
    c, db = scene; request = body(c)
    if fault == 'owner': c.qualification['reviewed_by'] = identity('foreign-actor')
    elif fault == 'old_schema': c.qualification['schema'] = 'author-review-result-v1'
    elif fault == 'false_human': c.qualification['human_review'] = 'author_self_reviewed'
    elif fault == 'simulation': c.qualification['simulation_only'] = False
    elif fault == 'wrong_source': request['source_plan_hash'] = 'f' * 64
    elif fault == 'wrong_hash': request['qualification_hash'] = 'f' * 64
    else: request['value']['flows'][0]['flow_index'] = 2
    with pytest.raises((HTTPException, ValueError)): save(c, db, request)
    assert not db.additions and db.commits == 0


def test_cross_actor_nonce_is_conflict_but_another_workspace_does_not_receive_the_original(scene):
    c, db = scene; request = body(c); original = save(c, db, request); actor = c.p
    c.p = type(actor)(**{**vars(actor), 'user_id': identity('foreign-actor')})
    with pytest.raises(HTTPException): recover(c, db, request)
    with pytest.raises(HTTPException): save(c, db, request)
    c.p = type(actor)(**{**vars(actor), 'workspace_id': identity('foreign-workspace')})
    assert recover(c, db, request)['found'] is False
    c.p = actor
    assert recover(c, db, request)['result'] == original


@pytest.mark.parametrize('loss', ['state', 'command', 'command_audit', 'state_audit', 'both_audits',
    'state_command', 'state_audits', 'command_audits', 'duplicate_audit'])
def test_missing_independent_traces_never_turn_into_unknown_or_a_new_slot(scene, loss):
    c, db = scene; request = body(c); save(c, db, request)
    if loss in {'state', 'state_command', 'state_audits'}: db.drafts.clear()
    if loss in {'command', 'state_command', 'command_audits'}: db.commands.clear()
    if loss in {'both_audits', 'state_audits', 'command_audits'}: db.events.clear()
    elif loss == 'command_audit': db.events[:] = [event for event in db.events if event.action != store.COMMAND_AUDIT]
    elif loss == 'state_audit': db.events[:] = [event for event in db.events if event.action != store.STATE_AUDIT]
    elif loss == 'duplicate_audit':
        event = copy.deepcopy(db.events[0]); event.id = identity('duplicate-audit'); db.events.append(event)
    for operation in (lambda: recover(c, db, request), lambda: save(c, db, body(c, 'second')),
        lambda: store.get(db, c.p, c.qualification_id)):
        with pytest.raises(HTTPException) as error: operation()
        assert error.value.status_code == 409


@pytest.mark.parametrize('fault', ['current_value', 'old_result', 'claimed_source_checked', 'command_scope', 'previous_hash', 'unknown_family'])
def test_resealed_record_or_command_cannot_replace_independent_audit_and_history(scene, fault):
    c, db = scene; request = body(c); save(c, db, request); row, command = db.drafts[0], db.commands[0]
    if fault == 'current_value': row.data['value']['buffers']['invented'] = True
    elif fault == 'old_result': command.data['result']['draft']['value']['buffers']['invented'] = True
    elif fault == 'claimed_source_checked': command.data['result']['draft']['current_source_checked'] = True
    elif fault == 'command_scope': command.scope_binding_hash = 'f' * 64
    elif fault == 'previous_hash': command.data['previous_hash'] = 'f' * 64
    else: row.data['schema'] = previous.STATE
    command.command_hash = digest(store._command_pin(command))
    with pytest.raises(HTTPException): recover(c, db, request)


def test_current_iam_and_sql_failures_do_not_become_missing_or_not_applied(scene):
    c, db = scene; request = body(c); save(c, db, request)
    db.active = False
    with pytest.raises(HTTPException) as error: recover(c, db, request)
    assert error.value.status_code == 403
    db.active = True; failure = OperationalError('synthetic SQL unavailable', {}, RuntimeError('IO boundary'))
    db.failure = failure
    with pytest.raises(OperationalError) as error: recover(c, db, request)
    assert error.value is failure
