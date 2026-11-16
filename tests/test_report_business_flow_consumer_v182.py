"""Actual V182 decoder/inventory/review functions; explicit synthetic I/O only.

Reuses the public protocol answer and strict SQL inventory boundary. These do
not establish real PostgreSQL, provider quality, source IAM, financial posting,
browser persistence or a whole-report V166 source. Root runs those separately.
"""
import asyncio
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import report_scoped_design_consumer_v181 as app
from generic_agents import report_business_flow_native_v182 as native
from generic_agents import report_business_flow_preparation_v182 as preparation
from generic_agents import report_business_flow_wire_v182 as wire
from generic_agents.contracts import digest
from generic_agents.models import Audit, CloudCall, Job
from generic_agents.report_scoped_design_models_v181 import ReportScopedDesignAttemptV181 as Attempt
from generic_agents.report_scoped_design_models_v181 import ReportScopedDesignAttemptCommandV181 as Command
from tests import test_report_scoped_design_consumer_v181 as legacy
from tests import test_report_business_flow_protocol_v182 as protocol
from tests.test_report_saved_map_design_consumer_v176 import equal_statement, matches_statement

identity = legacy.identity


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Synthetic consumer boundary cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    cap, source, plan = protocol.scene()
    p = NS(user_id=source['snapshot']['actor_id'], workspace_id=source['snapshot']['workspace_id'], role='operator')
    cap.id = identity(18211); cap.workspace_id = p.workspace_id
    cap.status = 'published'; cap.content_hash = digest(cap.data)
    body = app.PreviewFlowBody(plan_id=identity(18212), expected_plan_version=2,
        plan_hash=plan['plan_hash'], scope_id='custom:alpha', revision=7, model_id=cap.id,
        note=' 独立流程说明＋+🙂保留。 ', activity_id=identity(18213), generation_profile='flow-v182')
    prep = preparation.build_preparation(cap, source, plan, body.scope_id, note=body.note)
    budget = {'schema': 'report-scoped-design-budget-v181', 'call_limit': 1,
        'token_bound': prep['input_summary']['token_bound'], 'maximum_cost_usd': 3,
        'conservative_cost_usd': 0.2, 'pricing_basis_hash': 'b' * 64, 'grant_hash': 'd' * 64}
    preview = {'schema': app.PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': body.model_dump(), 'source_context': source, 'scope_plan': plan,
        'capability': app._cap_value(cap), 'preparation': prep, 'budget': budget,
        **app.FALSE_FLAGS, 'generation_profile': 'flow-v182'}
    preview['preview_hash'] = app.h(preview)
    start = {**body.model_dump(), 'request_id': body.activity_id, 'consent': True,
        'source_snapshot_hash': source['snapshot']['snapshot_hash'], 'preview_hash': preview['preview_hash']}
    raw = protocol.raw(start); parsed, exact, sha = app.read_wire(raw, app.StartFlowBody)
    row = Attempt(id=identity(18214), workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=body.activity_id, plan_id=body.plan_id, plan_version=2, plan_hash=body.plan_hash,
        scope_id=body.scope_id, producer_id=source['snapshot']['producer']['id'], model_id=cap.id,
        grant_hash=budget['grant_hash'], revision=7, version=1, status='queued', job_id=identity(18215),
        reserved_call_id=None, data={'schema': app.STATE, 'preview': preview, 'dispatch_started': False,
            'dispatch_identity': None, 'call_pin': None, 'response': None, 'projection': None,
            'manual_value': None, 'error': None, 'command_refs': [{'id': identity(18216),
                'request_id': body.activity_id, 'operation': 'start', 'wire_sha256': sha}]})
    command = Command(id=identity(18216), workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=body.activity_id, plan_id=row.plan_id, attempt_id=row.id, attempt_version=1,
        body_hash=app.h(parsed.model_dump()), operation='start', method='POST',
        path=app.PREFIX + '/start-flow', exact_body=exact, wire_sha256=sha,
        data={'schema': app.COMMAND, 'result': app.result(row, 'start')})
    command.command_hash = app.h(app._command_pin(command))
    anchors = [Audit(id=identity(18217), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=app.COMMAND_AUDIT, object_id=command.id, data=app._command_anchor(command)),
        Audit(id=identity(18218), workspace_id=p.workspace_id, actor_id=p.user_id,
            action=app.STATE_AUDIT, object_id=row.id, data=app._state_anchor(row, None))]
    job = Job(id=row.job_id, workspace_id=p.workspace_id, kind=app.JOB, status='queued', version=1,
        idempotency_key='scoped-design-v181:' + row.request_id,
        data={'actor_id': p.user_id, 'attempt_id': row.id}, lease_token=identity(18219))
    return NS(p=p, body=body, cap=cap, source=source, plan=plan, prep=prep, row=row,
        command=command, anchors=anchors, job=job, raw=raw)


class InventoryIO(legacy.IO):
    """Only exact existing SQL gets a response; no validator/whole DB stand-in."""
    def scalars(self, statement):
        for row in self.rows:
            expected = select(CloudCall).where(CloudCall.workspace_id == self.c.p.workspace_id,
                CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string() == row.id)
            if matches_statement(statement, expected):
                equal_statement(statement, expected)
                return [call for call in self.calls
                    if call.data['gateway_dispatch']['identity']['suggestion_id'] == row.id]
        return super().scalars(statement)

    def scalar(self, statement):
        for nonce in {identity(899), identity(898), identity(897)}:
            expected = select(Command).where(Command.workspace_id == self.c.p.workspace_id,
                Command.request_id == nonce)
            if matches_statement(statement, expected):
                equal_statement(statement, expected)
                return next((row for row in self.commands if row.request_id == nonce), None)
        return super().scalar(statement)


def reserved(c, db):
    selected = app._family(c.prep).native
    row = c.row; spec = c.prep['generation_contract']
    app._transition(db, c.p, row, {**row.data, 'dispatch_started': True,
        'dispatch_identity': app.dispatch_identity(row)}, 'running')
    call = CloudCall(id=identity(18220), workspace_id=row.workspace_id, capability_id=row.model_id,
        status='reserved', reserved_tokens=c.prep['input_summary']['token_bound'],
        charged_tokens=c.prep['input_summary']['token_bound'], data={'actor_id': row.actor_id,
            'request_hash': c.prep['request_hash'], 'dispatch_options_hash': digest(spec['dispatch_options']),
            'delivery': selected.DELIVERY, 'request_endpoint': selected.ENDPOINT,
            'request_body_bytes': c.prep['input_summary']['prepared_request_bytes'],
            'input_policy_hash': digest(spec['input_policy']), 'schema_proof_hash': digest(spec['schema_proof']),
            'gateway_dispatch': {'schema': 'cloud-dispatch-v130', 'identity': app.dispatch_identity(row),
                'attempt_id': identity(18221), 'reservation_request_hash': c.prep['request_hash']}})
    db.calls = [call]; app._associate(db, c.p, row, call)
    c.job.status = 'running'
    db.info['worker_lease'] = {'job_id': c.job.id, 'lease_token': c.job.lease_token}
    return call


def paid(c, arguments=None):
    selected = app._family(c.prep).native
    answer = protocol.answer(c.prep) if arguments is None else arguments
    raw = protocol.response(json.dumps(answer, ensure_ascii=False))
    inspected = selected.inspect_response(raw, 200, selected.STAGE,
        response_complete=True, http_content_encoding=None)
    receipt = {**inspected['metadata'], 'call_id': c.row.reserved_call_id, 'provider': 'qwen',
        'model': selected.MODEL, 'capture_hash': 'a' * 64, 'usage': inspected['usage']}
    return {'body': raw, 'receipt': receipt, 'capture': {'response_complete': True, 'ready': True,
        'content_encoding_safe': True, 'body_sha256': hashlib.sha256(raw).hexdigest(),
        'capture_hash': 'a' * 64, 'http_status': 200, 'http_content_encoding': None},
        'descriptor': {'synthetic': True}, 'binding': app.journal_binding(c.row)}


def reviewed_scene(monkeypatch):
    c = scene(); db = InventoryIO(c); call = reserved(c, db); saved = paid(c)
    reads, settlements = [], []
    def read(actual_db, actual_p, actual_row, *, recover_saved):
        assert actual_db is db and actual_p is c.p and actual_row is c.row
        assert actual_row.reserved_call_id == call.id
        assert app.journal_binding(actual_row) == saved['binding']
        reads.append(recover_saved); return saved
    def settle(actual_db, call_id, receipt, expected, status):
        assert actual_db is db and call_id == call.id and receipt['call_id'] == call.id
        assert expected['request_hash'] == c.prep['request_hash']
        settlements.append(status); call.status = status
    monkeypatch.setattr(app, 'runtime', lambda: NS(read_response=read))
    from generic_agents import cloud_budget
    monkeypatch.setattr(cloud_budget, 'settle_paid_response', settle)
    data, status = app._read_paid(db, c.p, c.row, recover_saved=True)
    assert status == 'review'
    app._transition(db, c.p, c.row, data, status)
    return c, db, saved, reads, settlements


def request(c, operation, payload):
    raw = protocol.raw(payload)
    async def body():
        return raw
    return NS(method='POST', url=NS(path=app.PREFIX + '/' + c.row.id + '/' + operation), body=body), raw


def test_old_and_new_preparation_body_paths_and_cold_inventories_stay_separate():
    new = scene(); old = legacy.scene()
    assert app.check_preview(new.row.data['preview'], new.p) is new.row.data['preview']
    assert app.check_preview(old.row.data['preview'], old.p) is old.row.data['preview']
    assert app._body_family(new.body).id == 'flow-v182'
    assert app._body_family(old.body).id == 'scope-v181'
    assert app._start_path(app._family(new.prep)).endswith('/start-flow')
    assert app._start_path(app._family(old.prep)).endswith('/start')
    db = InventoryIO(new)
    db.rows.append(old.row); db.commands.append(old.command); db.audits.extend(old.anchors); db.jobs.append(old.job)
    records, commands, calls = app._inventory(db, new.p)
    assert set(records) == {new.row.id, old.row.id} and len(commands) == 2 and calls == {}
    assert app.view(new.row)['generation_profile'] == 'flow-v182'
    assert 'generation_profile' not in app.view(old.row)
    for current in (new, old):
        restored = app.original_request(current.row.request_id, current.p, db,
            hashlib.sha256(current.raw).hexdigest())
        assert restored['found'] is True and restored['result'] == current.command.data['result']
    with pytest.raises(HTTPException):
        app.read_wire(new.raw, app.StartBody)
    with pytest.raises(HTTPException):
        app.read_wire(old.raw, app.StartFlowBody)


@pytest.mark.parametrize('fault', ['missing_profile', 'old_profile', 'unknown_profile', 'bool_profile',
    'different_nonce', 'extra_field', 'duplicate_key'])
def test_flow_command_rejects_wrong_profile_and_never_relabels_original_raw(fault):
    c = scene(); value = json.loads(c.raw)
    if fault == 'missing_profile': del value['generation_profile']
    elif fault == 'old_profile': value['generation_profile'] = 'scope-v181'
    elif fault == 'unknown_profile': value['generation_profile'] = 'indexed-v179'
    elif fault == 'bool_profile': value['generation_profile'] = True
    elif fault == 'different_nonce': value['request_id'] = identity(899)
    elif fault == 'extra_field': value['can_apply'] = True
    raw = protocol.raw(value)
    if fault == 'duplicate_key': raw = raw[:-1] + b',"generation_profile":"flow-v182"}'
    original = bytes(c.raw)
    with pytest.raises(HTTPException): app.read_wire(raw, app.StartFlowBody)
    assert c.raw == original


def test_single_scoped_reservation_uses_new_native_pins_and_rejects_an_extra_call():
    c = scene(); db = InventoryIO(c); call = reserved(c, db)
    assert app._inventory(db, c.p)[2][c.row.id] is call
    assert call.data['delivery'] == native.DELIVERY
    assert call.data['gateway_dispatch']['identity']['task_id'] == 'business-flow-v182'
    assert c.row.data['call_pin'] == app._call_pin(call)
    db.calls.append(call)
    with pytest.raises(app.InventoryError): app._inventory(db, c.p)


def test_actual_new_projection_creates_false_ack_draft_and_terminal_reads_do_not_settle_again(monkeypatch):
    c, db, saved, reads, settlements = reviewed_scene(monkeypatch)
    projection = c.row.data['projection']; value = c.row.data['manual_value']
    assert projection['schema'] == wire.PROJECTION and projection['wire']['schema'] == wire.WIRE
    assert projection['wire_bytes_sha256'] == hashlib.sha256(json.loads(saved['body'])['choices'][0]['message']['content'].encode()).hexdigest()
    assert value['schema'] == wire.MANUAL and value['result'] == projection['wire']
    assert value['note'] == '' and value['acknowledge_partial_scope'] is False and value['acknowledge_inferences'] is False
    assert 'objects' not in value['result'] and 'reconciliation_groups' not in value['result']
    before = app.h(c.row.data)
    data, status = app._read_paid(db, c.p, c.row, recover_saved=True)
    assert status == 'review' and app.h(data) == before
    assert reads == [True] and settlements == ['succeeded'] and len(db.calls) == 1
    view = app.view(c.row)
    assert view['result'] == projection['wire'] and view['partial_scope'] is True
    assert view['aggregate_verified'] is view['can_apply'] is view['can_publish'] is False


def test_false_ack_human_note_saves_replays_original_get_and_recovers_without_overwriting_machine(monkeypatch):
    c, db, _, reads, settlements = reviewed_scene(monkeypatch)
    original_projection = app.h(c.row.data['projection']); original_response = app.h(c.row.data['response'])
    original_source = app.h(c.row.data['preview']['source_context'])
    value = copy.deepcopy(c.row.data['manual_value']); value['note'] = ' 私人核对说明＋+🙂需保留；尚未确认。 '
    req, raw = request(c, 'save-review', {'request_id': identity(899), 'expected_version': c.row.version,
        'value': value, 'confirmed': False})
    # Explicit IAM I/O boundary only. The actual require() role check, source,
    # graph, decoder, command/audit inventory, CAS and replay checks still run.
    monkeypatch.setattr(app, 'lock_current_actor', lambda *args: None)
    result = asyncio.run(app.save_review_route(c.row.id, req, c.p, db))
    assert c.row.data['manual_value'] == value
    assert value['acknowledge_partial_scope'] is value['acknowledge_inferences'] is False
    assert app.h(c.row.data['projection']) == original_projection
    assert app.h(c.row.data['response']) == original_response
    assert app.h(c.row.data['preview']['source_context']) == original_source
    assert len(db.commands) == 2 and db.commits == 1
    assert asyncio.run(app.save_review_route(c.row.id, req, c.p, db)) == result
    probe = app.original_request(identity(899), c.p, db, hashlib.sha256(raw).hexdigest())
    assert probe['found'] is True and probe['result'] == result and db.commits == 1
    with pytest.raises(HTTPException): app.original_request(identity(899), c.p, db, '0' * 64)
    changed = json.loads(raw); changed['value']['note'] += '同号异文不覆盖'
    changed_request, _ = request(c, 'save-review', changed)
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(app.save_review_route(c.row.id, changed_request, c.p, db))
    assert conflict.value.status_code == 409 and c.row.data['manual_value'] == value
    assert len(db.commands) == 2 and db.commits == 1
    recovery, recovery_raw = request(c, 'recover-saved', {'request_id': identity(898), 'expected_version': c.row.version})
    recovered = asyncio.run(app.recover_route(c.row.id, recovery, c.p, db))
    assert recovered['operation'] == 'recover-saved' and c.row.data['manual_value'] == value
    assert app.h(c.row.data['projection']) == original_projection
    assert app.h(c.row.data['response']) == original_response
    assert len(db.commands) == 3 and db.commits == 2
    assert reads == [True] and settlements == ['succeeded'] and len(db.calls) == 1
    assert app.original_request(identity(898), c.p, db, hashlib.sha256(recovery_raw).hexdigest())['result'] == recovered
    assert app._inventory(db, c.p)[0][c.row.id] is c.row


def test_confirming_requires_explicit_acks_but_never_grants_whole_report_adoption(monkeypatch):
    c, db, _, _, _ = reviewed_scene(monkeypatch); before = app.h(c.row.data)
    monkeypatch.setattr(app, 'lock_current_actor', lambda *args: None)
    value = copy.deepcopy(c.row.data['manual_value'])
    for partial, inferred in [(False, False), (True, False)]:
        value['acknowledge_partial_scope'] = partial; value['acknowledge_inferences'] = inferred
        req, _ = request(c, 'save-review', {'request_id': identity(899), 'expected_version': c.row.version,
            'value': value, 'confirmed': True})
        with pytest.raises(HTTPException) as caught: asyncio.run(app.save_review_route(c.row.id, req, c.p, db))
        assert caught.value.status_code == 422 and app.h(c.row.data) == before
        assert len(db.commands) == 1 and db.commits == 0
    value['acknowledge_partial_scope'] = value['acknowledge_inferences'] = True
    req, _ = request(c, 'save-review', {'request_id': identity(899), 'expected_version': c.row.version,
        'value': value, 'confirmed': True})
    result = asyncio.run(app.save_review_route(c.row.id, req, c.p, db))
    assert result['status'] == 'reviewed'
    assert app.view(c.row)['partial_scope'] is True
    assert app.view(c.row)['aggregate_verified'] is app.view(c.row)['can_apply'] is False


@pytest.mark.parametrize('fault', ['missing_node', 'unreachable', 'producer', 'quote', 'binding', 'empty_citations'])
def test_invalid_graph_or_source_cannot_replace_an_already_saved_flow_draft(monkeypatch, fault):
    c, db, _, _, _ = reviewed_scene(monkeypatch); before = app.h(c.row.data)
    value = copy.deepcopy(c.row.data['manual_value']); result = value['result']; flow = result['business']['flows'][0]
    if fault == 'missing_node': flow['edges'][0]['to_node'] = 'absent'
    elif fault == 'unreachable': flow['edges'] = []
    elif fault == 'producer': flow['deliverables'][0]['produced_by'] = ['absent']
    elif fault == 'quote': result['evidence'][0]['quote'] += '不得修写原文'
    elif fault == 'binding': result['binding_hash'] = '0' * 64
    else: flow['nodes'][0]['attribution']['citations'] = []
    req, _ = request(c, 'save-review', {'request_id': identity(899), 'expected_version': c.row.version,
        'value': value, 'confirmed': False})
    monkeypatch.setattr(app, 'lock_current_actor', lambda *args: None)
    with pytest.raises(HTTPException) as caught: asyncio.run(app.save_review_route(c.row.id, req, c.p, db))
    assert caught.value.status_code == 422
    assert app.h(c.row.data) == before and len(db.commands) == 1 and db.commits == 0


def test_old_candidate_and_new_flow_manual_families_are_never_mutually_accepted(monkeypatch):
    c, _, _, _, _ = reviewed_scene(monkeypatch); old = legacy.scene()
    old_answer = legacy.protocol.response(old.prep, old.source)
    old_projection = legacy.protocol.project(old.prep, old.source, old_answer)
    from generic_agents.report_evidence_pipeline_v168 import manual_value_from_candidate
    old_manual = manual_value_from_candidate(old_projection['result'], old.source['catalog'],
        old.prep['allowed_span_refs'], old.source['context'])
    with pytest.raises(HTTPException) as caught: app._manual(old_manual, c.row.data['preview'])
    assert caught.value.status_code == 422
    with pytest.raises(HTTPException) as caught: app._manual(c.row.data['manual_value'], old.row.data['preview'])
    assert caught.value.status_code == 422


def test_unknown_flow_worker_only_reads_same_reserved_call_and_never_dispatches_again(monkeypatch):
    c = scene(); db = InventoryIO(c); call = reserved(c, db); reads = []
    frozen_binding = copy.deepcopy(app.journal_binding(c.row))
    def read(actual_db, actual_p, actual_row, *, recover_saved):
        assert actual_db is db and actual_p is c.p and actual_row is c.row
        assert actual_row.reserved_call_id == call.id and recover_saved is True
        assert app.journal_binding(actual_row) == frozen_binding
        reads.append(actual_row.reserved_call_id); return None
    def forbidden(*args, **kwargs): raise AssertionError('Unknown original flow may not POST again')
    monkeypatch.setattr(app, 'runtime', lambda: NS(read_response=read, dispatch=forbidden))
    first = app.run(db, c.p, c.job); second = app.run(db, c.p, c.job)
    assert first['status'] == second['status'] == 'response_unknown'
    assert reads == [identity(18220), identity(18220)]
    assert len(db.calls) == 1 and c.row.data['dispatch_started'] is True


@pytest.mark.parametrize('family', ['scope-v181', 'flow-v182'])
def test_foreign_answer_is_retained_as_original_failure_not_redecoded_or_adopted(monkeypatch, family):
    c = scene() if family == 'flow-v182' else legacy.scene()
    db = InventoryIO(c); reserved(c, db)
    if family == 'flow-v182':
        old = legacy.scene()
        foreign = legacy.protocol.response(old.prep, old.source)
    else:
        foreign = protocol.answer(scene().prep)
    saved = paid(c, foreign); settlements = []
    monkeypatch.setattr(app, 'runtime', lambda: NS(read_response=lambda *args, **kwargs: saved))
    from generic_agents import cloud_budget
    monkeypatch.setattr(cloud_budget, 'settle_paid_response', lambda *args: settlements.append(args[-1]))
    data, status = app._read_paid(db, c.p, c.row, recover_saved=True)
    assert status == 'failed' and data['projection'] is data['manual_value'] is None
    assert data['response']['body_sha256'] == hashlib.sha256(saved['body']).hexdigest()
    assert settlements == ['failed']
    app._transition(db, c.p, c.row, data, status)
    original = app.h(c.row.data)
    def forbidden(*args, **kwargs): raise AssertionError('A saved failed family cannot borrow a newer decoder')
    monkeypatch.setattr(app, 'runtime', lambda: NS(read_response=forbidden, dispatch=forbidden))
    again, state = app._read_paid(db, c.p, c.row, recover_saved=True)
    assert state == 'failed' and app.h(again) == original and settlements == ['failed']
