"""Fenced, synthetic PG tests; no actual gateway or external request."""
import copy
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from generic_agents import report_evidence_delegated_wire_v1 as wire
from generic_agents import report_evidence_pipeline as evidence
from generic_agents import report_evidence_prebound_transport as transport
from generic_agents.cloud_scoped_extra_reservation import dispatch_key
from generic_agents.db import engine
from generic_agents.leases import ExecutionUnauthorized, attach
from generic_agents.models import Audit, Capability, CloudCall, Job, Membership, SetupReportDraft, User
from tests.test_cloud_report_plan_allowance_postgres import own_database, reserve_first, stage_fixture
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene


@pytest.fixture
def prepared(reading_scene, monkeypatch, tmp_path):
    # These transport-contract fixtures predate scoped activation and isolate
    # transport behavior; separate scoped tests exercise the production gate.
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    monkeypatch.setattr(gate, 'ready', lambda: True)
    base = tmp_path.resolve()
    monkeypatch.setattr(wire, 'root', lambda: base / 'capture')
    fixture = stage_fixture(reading_scene, monkeypatch)
    p, rid, jid, gid, cap_id, first, lease = fixture
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, jid, lease)
        db.info['worker_actor'] = p
        call_id = reserve_first(db, fixture)
        db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        row = db.get(SetupReportDraft, rid)
        request = evidence._request(row, first['stage_id'])
        options = copy.deepcopy(evidence._spec(row, first['stage_id'])['dispatch_options'])
    return fixture, call_id, request['messages'], options


def _session(fixture):
    p, _, jid, _, _, _, lease = fixture
    db = Session(engine(), expire_on_commit=False)
    attach(db, jid, lease)
    db.info['worker_actor'] = p
    return db


def test_prebound_callback_and_intent_are_one_shot_and_do_not_reserve_again(prepared):
    fixture, call_id, messages, options = prepared
    p, rid, jid, gid, cap_id, first, _ = fixture
    with _session(fixture) as db:
        with Session(engine()) as check:
            before = check.scalar(select(func.count()).select_from(CloudCall))
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        try:
            witness = transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
            frozen = witness.consume(p, cap_id, first['stage_id'], call_id, messages, options, sink)
            assert frozen['call_id'] == call_id and frozen['messages'] == messages and frozen['options'] == options
            with pytest.raises(transport.PreboundTransportDenied):
                witness.consume(p, cap_id, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before
        assert db.get(CloudCall, call_id).status == 'reserved'
        dispatch = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == dispatch_key(jid, first['stage_id'])))
        assert dispatch.data['state'] == 'transport_intent'
        assert dispatch.data['call_id'] == call_id
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.action == 'cloud.delegated_evidence.transport_intent_v1')) == 1


def test_restart_after_intent_does_not_create_a_second_handoff(prepared):
    fixture, call_id, messages, options = prepared
    p, _, _, _, _, first, _ = fixture
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        try:
            transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()
    with _session(fixture) as db:
        with pytest.raises(transport.PreboundTransportDenied):
            transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
    with Session(engine()) as db:
        assert db.get(CloudCall, call_id).status == 'reserved'


def test_handoff_mismatch_is_terminal_and_witness_cannot_be_copied(prepared):
    fixture, call_id, messages, options = prepared
    p, _, _, _, cap_id, first, _ = fixture
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        try:
            witness = transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
            with pytest.raises(transport.PreboundTransportDenied):
                copy.copy(witness)
            with pytest.raises(transport.PreboundTransportDenied):
                copy.deepcopy(witness)
            altered = [*messages, {'role': 'user', 'content': 'altered'}]
            with pytest.raises(transport.PreboundTransportDenied):
                witness.consume(p, cap_id, first['stage_id'], call_id, altered, options, sink)
            with pytest.raises(transport.PreboundTransportDenied):
                witness.consume(p, cap_id, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()


def test_synthetic_http_uses_the_one_prebound_handoff(prepared):
    fixture, call_id, messages, options = prepared
    p, _, _, _, cap_id, first, _ = fixture
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={'x-request-id': 'synthetic'}, stream=httpx.ByteStream(b'{"ok":true}'))
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        try:
            witness = transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
            bound = witness.consume(p, cap_id, first['stage_id'], call_id, messages, options, sink)
            with httpx.Client(transport=httpx.MockTransport(handle)) as client:
                sink.mark_request_started()
                with client.stream('POST', 'https://synthetic.invalid/', json={**bound['options'], 'messages': bound['messages']}) as response:
                    sink.begin_response(response.status_code, response.headers.get('content-encoding'), response.headers.get('x-request-id'))
                    for chunk in response.iter_raw():
                        sink.append(chunk)
                    sink.finish(eof=True)
            with pytest.raises(transport.PreboundTransportDenied):
                witness.consume(p, cap_id, first['stage_id'], call_id, messages, options, sink)
            assert len(requests) == 1 and requests[0]['messages'] == messages
        finally:
            sink.close()


@pytest.mark.parametrize('mode', ['capability', 'version', 'created_at', 'delete', 'expire', 'expunge'])
def test_same_session_call_mutation_aborts_intent(prepared, mode):
    fixture, call_id, messages, options = prepared
    p, _, jid, _, cap_id, first, _ = fixture
    with Session(engine(), expire_on_commit=False) as db:
        original = db.get(CloudCall, call_id)
        columns = {x.key: copy.deepcopy(getattr(original, x.key)) for x in inspect(original).mapper.column_attrs}
        if mode == 'capability':
            cap = db.get(Capability, cap_id)
            other = Capability(workspace_id=p.workspace_id, namespace=cap.namespace,
                name='synthetic-other', release_version=cap.release_version, kind=cap.kind,
                status=cap.status, content_hash=cap.content_hash, validated_hash=cap.validated_hash,
                data=copy.deepcopy(cap.data))
            db.add(other); db.flush(); other_id = other.id; db.commit()
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        try:
            def mutate(session):
                call = session.get(CloudCall, call_id)
                if mode == 'capability': call.capability_id = other_id
                elif mode == 'version': call.version += 1
                elif mode == 'created_at': call.created_at += timedelta(seconds=1)
                elif mode == 'delete': session.delete(call)
                elif mode == 'expire': session.expire(call)
                else: session.expunge(call)
            event.listen(db, 'before_commit', mutate, insert=True)
            with pytest.raises(transport.PreboundTransportDenied):
                transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()
    with Session(engine()) as db:
        call = db.get(CloudCall, call_id)
        assert {key: getattr(call, key) for key in columns} == columns
        dispatch = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == dispatch_key(jid, first['stage_id'])))
        assert dispatch.data['state'] == 'reserved'
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.action == 'cloud.delegated_evidence.transport_intent_v1')) == 0


@pytest.mark.parametrize('which', ['messages', 'options'])
def test_post_commit_caller_mutation_never_mints_witness(prepared, monkeypatch, which):
    fixture, call_id, messages, options = prepared
    p, _, jid, _, _, first, _ = fixture
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        checkpoint = transport.checkpoint
        def commit_then_mutate(session):
            checkpoint(session)
            if which == 'messages': messages.append({'role': 'user', 'content': 'mutated'})
            else: options['temperature'] = 0.99
        monkeypatch.setattr(transport, 'checkpoint', commit_then_mutate)
        try:
            with pytest.raises(transport.PreboundTransportDenied):
                transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()
    with Session(engine()) as db:
        dispatch = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == dispatch_key(jid, first['stage_id'])))
        assert dispatch.data['state'] == 'transport_intent'
        assert db.get(CloudCall, call_id).status == 'reserved'


def test_later_before_commit_mutation_is_caught_at_flush(prepared, monkeypatch):
    fixture, call_id, messages, options = prepared
    p, _, jid, _, _, first, _ = fixture
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        checkpoint = transport.checkpoint
        def add_late_listener_then_commit(session):
            def mutate_late(inner):
                inner.get(CloudCall, call_id).version += 1
            event.listen(session, 'before_commit', mutate_late)
            checkpoint(session)
        monkeypatch.setattr(transport, 'checkpoint', add_late_listener_then_commit)
        try:
            with pytest.raises(transport.PreboundTransportDenied):
                transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()
    with Session(engine()) as db:
        dispatch = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == dispatch_key(jid, first['stage_id'])))
        assert dispatch.data['state'] == 'reserved'


@pytest.mark.parametrize('what', ['messages', 'options', 'numeric_options', 'call'])
def test_changed_transport_binding_is_rejected_before_capture(prepared, what):
    fixture, call_id, messages, options = prepared
    p, _, _, _, _, first, _ = fixture
    if what == 'messages':
        messages = [*messages, {'role': 'user', 'content': 'different synthetic input'}]
    elif what == 'options':
        options = {**options, 'temperature': 0.987}
    elif what == 'numeric_options':
        altered = {**options, 'max_tokens': float(options['max_tokens'])}
        assert altered == options
        options = altered
    else:
        call_id = str(uuid4())
    with _session(fixture) as db:
        with pytest.raises(transport.PreboundTransportDenied):
            transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)


def test_witness_rejects_python_equal_but_differently_encoded_options(prepared):
    fixture, call_id, messages, options = prepared
    p, _, _, _, cap_id, first, _ = fixture
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        try:
            witness = transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
            altered = {**options, 'max_tokens': float(options['max_tokens'])}
            assert altered == options
            with pytest.raises(transport.PreboundTransportDenied):
                witness.consume(p, cap_id, first['stage_id'], call_id, messages, altered, sink)
            with pytest.raises(transport.PreboundTransportDenied):
                witness.consume(p, cap_id, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()


@pytest.mark.parametrize('field,original,altered', [
    ('option', False, 0), ('option', True, 1), ('option', 1, 1.0),
    ('message', False, 0), ('message', True, 1), ('message', 1, 1.0),
])
def test_handoff_uses_strict_json_digest_for_nested_values(prepared, field, original, altered):
    fixture, call_id, messages, options = prepared
    p, _, _, _, cap_id, first, _ = fixture
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        try:
            # Isolated handoff contract test; no intent or HTTP is executed.
            frozen_messages = copy.deepcopy(messages)
            frozen_options = copy.deepcopy(options)
            if field == 'message':
                frozen_messages.append({'role': 'user', 'synthetic': {'value': original}})
            else:
                frozen_options['synthetic'] = {'value': original}
            changed_messages = copy.deepcopy(frozen_messages)
            changed_options = copy.deepcopy(frozen_options)
            if field == 'message': changed_messages[-1]['synthetic']['value'] = altered
            else: changed_options['synthetic']['value'] = altered
            assert changed_messages == frozen_messages and changed_options == frozen_options
            def witness():
                return transport.CommittedTransportIntent(transport._WITNESS, p, cap_id,
                    first['stage_id'], call_id, frozen_messages, frozen_options, sink, sink.context, 'synthetic',
                    transport._monotonic()+30)
            with pytest.raises(transport.PreboundTransportDenied):
                witness().consume(p, cap_id, first['stage_id'], call_id, changed_messages, changed_options, sink)
            assert witness().consume(p, cap_id, first['stage_id'], call_id, frozen_messages, frozen_options, sink)['call_id'] == call_id
        finally:
            sink.close()


@pytest.mark.parametrize('what', ['role', 'active'])
def test_revocation_after_capture_before_intent_is_denied(prepared, what):
    fixture, call_id, messages, options = prepared
    p, _, jid, _, _, first, _ = fixture
    with _session(fixture) as db:
        sink = transport.prepare_prebound_capture(db, p, first['stage_id'], call_id, messages, options)
        try:
            with Session(engine()) as other:
                if what == 'role':
                    other.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
                        Membership.user_id == p.user_id)).role = 'viewer'
                else:
                    other.get(User, p.user_id).active = False
                other.commit()
            with pytest.raises((ExecutionUnauthorized, transport.PreboundTransportDenied)):
                transport.commit_transport_intent(db, p, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()
    with Session(engine()) as db:
        dispatch = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == dispatch_key(jid, first['stage_id'])))
        assert dispatch.data['state'] == 'reserved'
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.action == 'cloud.delegated_evidence.transport_intent_v1')) == 0
