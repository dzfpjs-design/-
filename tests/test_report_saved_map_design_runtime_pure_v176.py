"""Trust seams with explicit synthetic SQL, allowance and HTTP boundaries.

These tests retain actual published/AuthSession predicates, pure preparation,
consumer inventory/call/lease checks and original tmp-path capture/journal.
Source/material admission callbacks are labelled spies, not real ownership or
PG evidence; no provider is contacted. Root alone executes this file.
"""
import copy
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import gateway, relations
from generic_agents import report_saved_map_design_runtime_v176 as subject
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_design_saved_source_v174 as source
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_journal_v2 as journal
from generic_agents.contracts import digest
from generic_agents.models import Capability, User, Membership, AuthSession, CloudCall, Job
from generic_agents.report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_saved_map_design_consumer_v176 import (
    scene, InventoryIO, equal_statement, matches_statement, no_network_or_process,
)
from tests.test_report_saved_map_design_preparation_v176 import identity
from tests.test_report_design_grouped_wire_v174 import scene as answer_scene


CLOCK = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def allowance(p, body):
    return {'schema': subject.ALLOWANCE, 'activity_id': identity(400),
        'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'producer_id': body.producer_id, 'model_id': body.model_id,
        'revision': body.revision, 'valid_until': '2099-01-01T00:00:00+00:00',
        'maximum_cost_usd': 3, 'input_usd_per_million': 2,
        'output_usd_per_million': 6, 'pricing_basis_hash': 'b' * 64}


class RuntimeIO:
    """Only exact real capability/grant/actor queries, not a DB implementation."""
    def __init__(self, p, cap, grant_hash):
        self.p = p; self.cap = cap; self.grant_hash = grant_hash
        self.consumed = None; self.authorized = True; self.log = []
        self.poison = False

    def scalar(self, statement):
        assert not self.poison, 'Original HTTP callback must not revisit ORM state'
        expected = select(Capability).where(Capability.id == self.cap.id,
            Capability.workspace_id == self.p.workspace_id)
        if matches_statement(statement, expected):
            equal_statement(statement, expected); self.log.append('published'); return self.cap
        expected = select(Consumer).where(Consumer.workspace_id == self.p.workspace_id,
            Consumer.grant_hash == self.grant_hash)
        equal_statement(statement, expected); self.log.append('grant'); return self.consumed

    def execute(self, statement):
        assert not self.poison, 'Original HTTP callback must not revisit ORM state'
        p = self.p
        expected = select(User, Membership).join(Membership, Membership.user_id == User.id).where(
            User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
            Membership.role.in_(['admin', 'operator']), Membership.role == p.role)
        expected = expected.add_columns(AuthSession).join(AuthSession, AuthSession.user_id == User.id).where(
            AuthSession.id == p.session_id, AuthSession.revoked.is_(False), AuthSession.expires_at > CLOCK)
        expected = expected.with_for_update(read=True, of=[User, Membership, AuthSession]).execution_options(populate_existing=True)
        # The actual AuthSession predicate carries a datetime SQL bind, which
        # is deliberately outside the JSON codec. Compare that bind with its
        # exact Python type/offset; retain typed canonical checks for all others.
        left, right = statement.compile(), expected.compile()
        assert str(left) == str(right) and set(left.params) == set(right.params)
        for key, value in right.params.items():
            actual = left.params[key]
            if type(value) is datetime:
                assert type(actual) is datetime and actual == value and actual.tzinfo == value.tzinfo
            else:
                assert canonical_json(actual) == canonical_json(value)
        self.log.append('actor-session')
        return NS(first=lambda: (object(), object(), object()) if self.authorized else None)

    def flush(self):
        assert not self.poison; self.log.append('flush')

    def add(self, *_): raise AssertionError('Admission must not write a consumer/call/job')
    def commit(self): raise AssertionError('Admission must not commit')


@pytest.fixture
def admission(monkeypatch):
    p, body, cap, context, row, *_ = scene()
    p.session_id = identity(401)
    cap.kind = 'foundation_model'; cap.status = 'published'
    cap.data['execution']['secret_ref'] = 'DASHSCOPE_API_KEY'
    cap.data['data_policy']['cloud_allowed'] = True
    cap.content_hash = digest(cap.data); cap.validated_hash = cap.content_hash
    permit = allowance(p, body); db = RuntimeIO(p, cap, digest(permit))
    state = NS(p=p, body=body, cap=cap, context=context, row=row, permit=permit,
        db=db, revision=body.revision, sources=[], owned_exception=None)
    monkeypatch.setattr(relations, 'now', lambda: CLOCK)
    def current_allowance(principal, producer, revision, model):
        assert principal is p and (producer, revision, model) == (body.producer_id, body.revision, body.model_id)
        return copy.deepcopy(state.permit)
    monkeypatch.setattr(subject, '_allowance', current_allowance)
    def workspace(actual_db, actual_p, *, lock):
        assert actual_db is db and actual_p is p and type(lock) is bool
        db.log.append(('configuration', lock)); return NS(version=state.revision)
    monkeypatch.setattr(source.pipeline.legacy, 'workspace_draft', workspace)
    def source_admission(actual_db, actual_p, producer, *, lock, current_consumer_id):
        assert actual_db is db and actual_p is p and producer == body.producer_id
        assert current_consumer_id == state.owned_exception
        state.sources.append((lock, current_consumer_id)); db.log.append('source-spy')
        return copy.deepcopy(context)
    monkeypatch.setattr(source, 'load_source_context', source_admission)
    return state


def admit(state, **options):
    b = state.body
    return subject.admit(state.db, state.p, b.producer_id, b.revision, b.model_id,
        lock=options.pop('lock', False), note=options.pop('note', b.note), **options)


def test_admission_uses_actual_foundation_kind_current_session_configuration_and_note_bound(admission):
    a = admission; note = ' 新独立说明，不沿用旧模型许可。 ' * 20
    actual = admit(a, note=note)
    prep = subject.preparation.build_preparation(a.cap, a.context, note=note)
    assert set(actual) == {'source_context', 'capability', 'revision', 'budget'}
    assert actual['capability'] is a.cap and actual['source_context'] == a.context
    assert actual['budget']['token_bound'] == prep['input_summary']['token_bound']
    assert actual['budget']['grant_hash'] == digest(a.permit)
    assert actual['budget']['call_limit'] == 1 and actual['budget']['maximum_cost_usd'] == 3
    assert a.db.log[:3] == ['actor-session', ('configuration', False), 'published']
    assert a.db.log[-2:] == ['flush', 'source-spy'] and a.sources == [(False, None)]
    assert prep['input_summary']['token_bound'] != subject.preparation.build_preparation(
        a.cap, a.context, note='')['input_summary']['token_bound']


@pytest.mark.parametrize('fault', ['revoked_session', 'configuration', 'cap_kind', 'cap_hash',
    'unpublished', 'cloud_policy', 'secret', 'grant_consumed'])
def test_current_admission_never_borrows_stale_role_model_config_or_activity(admission, fault):
    a = admission
    if fault == 'revoked_session': a.db.authorized = False
    elif fault == 'configuration': a.revision += 1
    elif fault == 'cap_kind': a.cap.kind = 'tool'
    elif fault == 'cap_hash': a.cap.validated_hash = 'a' * 64
    elif fault == 'unpublished': a.cap.status = 'draft'
    elif fault == 'cloud_policy':
        a.cap.data['data_policy']['cloud_allowed'] = False
        a.cap.content_hash = a.cap.validated_hash = digest(a.cap.data)
    elif fault == 'secret':
        a.cap.data['execution']['secret_ref'] = 'QWEN_TOKENPLAN_API_KEY'
        a.cap.content_hash = a.cap.validated_hash = digest(a.cap.data)
    else: a.db.consumed = NS(id=identity(499))
    with pytest.raises(HTTPException) as error: admit(a)
    assert error.value.status_code == (403 if fault == 'revoked_session' else 409)
    assert a.sources == []


def test_budget_uses_actual_complete_body_and_ceiling_instead_of_old_reduce_or_fixed_estimate(admission):
    a = admission; note = '明确保留新增说明。' * 300
    prep = subject.preparation.build_preparation(a.cap, a.context, note=note)
    output = prep['generation_contract']['dispatch_options']['max_tokens']
    cost = (Decimal(prep['input_summary']['prepared_request_bytes'] + 1024) * 2 + Decimal(output) * 6) / Decimal(1000000)
    exact_ceiling = float(cost.quantize(Decimal('0.000001'), rounding=ROUND_CEILING))
    a.permit['maximum_cost_usd'] = exact_ceiling
    a.db.grant_hash = digest(a.permit)
    assert admit(a, note=note)['budget']['maximum_cost_usd'] == exact_ceiling
    a.permit['maximum_cost_usd'] = exact_ceiling - .000001
    a.db.grant_hash = digest(a.permit)
    with pytest.raises(HTTPException) as error: admit(a, note=note)
    assert error.value.status_code == 409 and '费用上限' in error.value.detail


@pytest.mark.parametrize('fault', ['other_actor', 'expired', 'naive_expiry', 'nonfinite', 'price_floor', 'duplicate'])
def test_actual_allowance_reader_requires_independent_owner_prices_and_strict_original_file(tmp_path, monkeypatch, fault):
    p, body, *_ = scene(); value = allowance(p, body)
    if fault == 'other_actor': value['actor_id'] = identity(499)
    elif fault == 'expired': value['valid_until'] = '2000-01-01T00:00:00+00:00'
    elif fault == 'naive_expiry': value['valid_until'] = '2099-01-01T00:00:00'
    elif fault == 'nonfinite': value['maximum_cost_usd'] = float('nan')
    elif fault == 'price_floor': value['input_usd_per_million'] = 1.65
    content = json.dumps(value)
    if fault == 'duplicate': content = content[:-1] + ',"schema":' + json.dumps(subject.ALLOWANCE) + '}'
    path = tmp_path / 'saved-map-design-allowance-v176.json'; path.write_text(content, encoding='utf-8')
    monkeypatch.setattr(subject, 'settings', lambda: NS(storage_dir=tmp_path))
    with pytest.raises(HTTPException) as error:
        subject._allowance(p, body.producer_id, body.revision, body.model_id)
    assert error.value.status_code == 409


def test_actual_allowance_has_no_default_on_missing_file_and_good_file_is_detached(tmp_path, monkeypatch):
    p, body, *_ = scene(); value = allowance(p, body)
    monkeypatch.setattr(subject, 'settings', lambda: NS(storage_dir=tmp_path))
    with pytest.raises(HTTPException) as error:
        subject._allowance(p, body.producer_id, body.revision, body.model_id)
    assert error.value.status_code == 503
    (tmp_path / 'saved-map-design-allowance-v176.json').write_text(json.dumps(value), encoding='utf-8')
    assert subject._allowance(p, body.producer_id, body.revision, body.model_id) == value


def actual_call(row):
    prep = row.data['preview']['preparation']; spec = prep['generation_contract']
    return CloudCall(id=identity(488), workspace_id=row.workspace_id, capability_id=row.model_id,
        status='reserved', reserved_tokens=prep['input_summary']['token_bound'], charged_tokens=0,
        data={'actor_id': row.actor_id, 'request_hash': prep['request_hash'],
            'dispatch_options_hash': digest(spec['dispatch_options']), 'delivery': subject.native.DELIVERY,
            'request_endpoint': subject.native.ENDPOINT, 'request_body_bytes': prep['input_summary']['prepared_request_bytes'],
            'input_policy_hash': digest(spec['input_policy']), 'schema_proof_hash': digest(spec['schema_proof']),
            'gateway_dispatch': {'schema': 'cloud-dispatch-v130', 'identity': consumer.dispatch_identity(row),
                'attempt_id': identity(489), 'reservation_request_hash': prep['request_hash']}})


class SourceIO(InventoryIO):
    def __init__(self, values):
        p, _, _, _, row, command, audits, job, _ = values
        super().__init__(p, [row], [command], audits, [job]); self.no_autoflush = nullcontext()
        self.info = {'worker_lease': {'job_id': job.id, 'lease_token': job.lease_token}}
        self.excluded = None; self.source_queries = []
    def scalar(self, statement):
        expected = select(CloudCall.id).where(CloudCall.workspace_id == self.p.workspace_id,
            CloudCall.status.not_in(('succeeded', 'failed')))
        if self.excluded is not None: expected = expected.where(CloudCall.id != self.excluded)
        expected = expected.limit(1)
        if matches_statement(statement, expected):
            equal_statement(statement, expected); self.source_queries.append(statement)
            return next((call.id for call in self.calls if call.status not in {'succeeded', 'failed'}
                and call.id != self.excluded), None)
        return super().scalar(statement)
    def get(self, model, identity_value):
        assert model in {Job, CloudCall}
        values = self.jobs if model is Job else self.calls
        return next((row for row in values if row.id == identity_value), None)


class SourceBoundaryReached(Exception): pass


@pytest.fixture
def owned_call_source(monkeypatch):
    values = scene(); p, _, _, _, row, _, _, job, _ = values
    db = SourceIO(values); job.status = 'running'
    consumer._transition(db, p, row, {**row.data, 'dispatch_started': True,
        'dispatch_identity': consumer.dispatch_identity(row)}, 'running')
    call = actual_call(row); db.calls = [call]; consumer._associate(db, p, row, call)
    db.excluded = call.id
    # These are explicit pre-existing role/legacy-source boundaries. The new
    # consumer inventory, its independent audits, lease and call pins stay real.
    monkeypatch.setattr(source, '_current_role', lambda actual_db, actual_p: actual_db is db and actual_p is p)
    monkeypatch.setattr(source.pipeline.legacy, 'guard_existing_paid', lambda actual_db, actual_p: None)
    def original_source(*args, **kwargs): raise SourceBoundaryReached()
    monkeypatch.setattr(source.pipeline, 'owned', original_source)
    return NS(p=p, db=db, row=row, call=call, job=job)


def test_current_consumer_exception_is_only_exact_checked_reserved_owned_lease(owned_call_source):
    a = owned_call_source
    with pytest.raises(SourceBoundaryReached):
        source.load_source_context(a.db, a.p, a.row.producer_id, lock=True, current_consumer_id=a.row.id)
    assert len(a.db.source_queries) == 1
    assert consumer.checked_consumer(a.db, a.p, a.row.id).reserved_call_id == a.call.id
    assert a.db.commits == 0


@pytest.mark.parametrize('fault', ['lease', 'other_call', 'call_hash', 'missing_audit', 'no_exception'])
def test_source_exception_never_exempts_unrelated_unknown_forged_call_or_lost_inventory(owned_call_source, fault):
    a = owned_call_source; consumer_id = a.row.id
    if fault == 'lease': a.db.info['worker_lease']['lease_token'] = identity(499)
    elif fault == 'other_call':
        a.db.calls.append(CloudCall(id=identity(498), workspace_id=a.p.workspace_id,
            capability_id=a.row.model_id, status='reserved', data={}))
    elif fault == 'call_hash': a.call.data['request_hash'] = 'c' * 64
    elif fault == 'missing_audit': a.db.audits = [event for event in a.db.audits if event.action != consumer.COMMAND_AUDIT]
    else: consumer_id = None; a.db.excluded = None
    with pytest.raises(HTTPException) as error:
        source.load_source_context(a.db, a.p, a.row.producer_id, lock=True, current_consumer_id=consumer_id)
    assert error.value.status_code == 409 and a.db.commits == 0


def test_dispatch_saves_original_http_journal_before_orm_and_recovery_never_calls_provider(tmp_path, monkeypatch):
    p, body, cap, context, row, _, _, job, _ = scene()
    cap.kind = 'foundation_model'; cap.validated_hash = cap.content_hash
    db = RuntimeIO(p, cap, row.grant_hash)
    prep = row.data['preview']['preparation']; spec = subject.preparation.stage_spec(prep)
    request = subject.preparation.rebuild_request(prep, cap, context, note=body.note)
    storage = tmp_path / 'capture'; saved = tmp_path / 'journal'
    monkeypatch.setattr(bridge, 'root', lambda: storage)
    monkeypatch.setattr(subject, '_journal_root', lambda: saved)
    call_id = identity(488); events = []; scopes = []
    def durable_association(actual_id):
        assert actual_id == call_id
        events.append('reserved-callback'); row.reserved_call_id = actual_id
        db.poison = True
        return consumer.journal_binding(row)
    def synthetic_http(actual_cap, messages, schema, principal, **options):
        assert actual_cap is cap and principal is p and options['evidence_stage'] == subject.native.STAGE
        assert options['full_report'] is True
        assert options['dispatch_identity'] == consumer.gateway_dispatch_identity(row)
        prepared, _, images = gateway.prepare_cloud_json(cap, messages, schema)
        assert images == 0
        actual = subject.native.prepare_request(prepared, schema,
            subject.native.dispatch_options(cap.data['execution'], gateway.cloud_json_options(cap.data['execution'])), subject.native.STAGE)
        assert actual['body'] == request['request_body'] and actual['body_sha256'] == prep['request_hash']
        with options['on_reserved'](call_id) as sink:
            scopes.append(sink.context); sink.mark_request_started(); sink.begin_response(200, None, 'SYNTHETIC-NOT-PROVIDER')
            response = json.dumps({'usage': {'prompt_tokens': 8, 'completion_tokens': 9, 'total_tokens': 17},
                'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                    'content': json.dumps(answer_scene()[0], ensure_ascii=False)}}]}, ensure_ascii=False).encode('utf-8')
            sink.append(response); sink.finish(eof=True)
            wire, receipt, _ = bridge.response_context(sink.context, 'qwen', subject.native.MODEL,
                subject.native.input_policy(cap.data['execution']), subject.native.STAGE)
            options['on_response'](wire, receipt)
            descriptor = journal.read_descriptor(saved, sink.context['binding'])
            assert journal.read_response(saved, sink.context['binding'], descriptor)['content'] == wire
            events.append('journal-durable-before-dispatch-return')
        return answer_scene()[0], receipt
    monkeypatch.setattr(gateway, 'cloud_json', synthetic_http)
    detached = NS(**consumer._row_pin(row))
    subject.dispatch(db, p, job, detached, request=request, spec=spec, on_reserved=durable_association)
    value = subject.read_response(db, p, row, recover_saved=False)
    assert value['binding'] == scopes[0]['binding'] and value['receipt']['call_id'] == call_id
    assert events == ['reserved-callback', 'journal-durable-before-dispatch-return']
    value2 = subject.read_response(db, p, row, recover_saved=True)
    assert value2 == value and len(scopes) == 1


def test_partial_original_capture_is_unknown_without_journal_or_new_dispatch(tmp_path, monkeypatch):
    p, _, cap, _, row, *_ = scene(); row.reserved_call_id = identity(488)
    monkeypatch.setattr(bridge, 'root', lambda: tmp_path / 'capture')
    monkeypatch.setattr(subject, '_journal_root', lambda: tmp_path / 'journal')
    def forbidden(*args, **kwargs): raise AssertionError('Original response read cannot dispatch')
    monkeypatch.setattr(gateway, 'cloud_json', forbidden)
    ctx = subject._context(row)
    with capture.prepare(bridge.root(), ctx) as sink:
        sink.mark_request_started(); sink.begin_response(200, None, None)
        sink.append(b'{"choices":['); sink.abort('transport_error')
    assert subject.read_response(None, p, row, recover_saved=True) is None
    assert not (tmp_path / 'journal').exists()


def test_complete_capture_without_journal_recovers_only_same_call_original_bytes(tmp_path, monkeypatch):
    p, _, _, _, row, *_ = scene(); row.reserved_call_id = identity(488)
    monkeypatch.setattr(bridge, 'root', lambda: tmp_path / 'capture')
    monkeypatch.setattr(subject, '_journal_root', lambda: tmp_path / 'journal')
    def forbidden(*args, **kwargs): raise AssertionError('Missing journal recovery cannot reserve or send')
    monkeypatch.setattr(gateway, 'cloud_json', forbidden)
    ctx = subject._context(row)
    original = json.dumps({'usage': {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7},
        'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
            'content': json.dumps(answer_scene()[0], ensure_ascii=False)}}]}, ensure_ascii=False).encode('utf-8')
    with capture.prepare(bridge.root(), ctx) as sink:
        sink.mark_request_started(); sink.begin_response(200, None, 'SYNTHETIC-COMPLETE-ORIGINAL')
        sink.append(original); before = sink.finish(eof=True)
    with pytest.raises((OSError, ValueError)):
        subject.read_response(None, p, row, recover_saved=False)
    assert not (tmp_path / 'journal').exists()
    restored = subject.read_response(None, p, row, recover_saved=True)
    assert restored['body'] == original and restored['binding'] == ctx['binding']
    assert restored['receipt']['call_id'] == row.reserved_call_id
    assert restored['capture']['capture_hash'] == before['capture_hash']
    assert capture.load(bridge.root(), ctx) == before
    assert subject.read_response(None, p, row, recover_saved=False) == restored
