"""Independent V183 central seams over PUBLIC synthetic transport/inventory.

The real gateway/profile/capture/consumer functions run. Existing narrow fake
HTTP, Call/Session and exact SQL fixture boundaries are reused. No real cloud,
DB, source authorization, human consent, model quality or persistence is proved.
"""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import gateway, cloud_budget
from generic_agents import report_business_flow_native_v183 as native
from generic_agents import report_business_flow_preparation_v183 as preparation
from generic_agents import report_business_flow_native_v182 as previous
from generic_agents import report_business_flow_wire_v182 as wire
from generic_agents import report_scoped_design_consumer_v181 as app
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.cloud_evidence_profiles import validate_evidence_output, evidence_policy
from generic_agents.contracts import Manifest, digest
from generic_agents.models import Audit
from tests import test_report_business_flow_gateway_v182 as old_gateway
from tests import test_report_business_flow_protocol_v182 as old_protocol
from tests import test_report_business_flow_consumer_v182 as old_consumer
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution
from tests.test_saved_map_design_gateway_pure_v176 import native_transport

identity = old_consumer.identity


def scene():
    cap, source, plan = old_protocol.scene()
    cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 16384, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    return cap, source, plan


def invoke(state, monkeypatch, **kwargs):
    # Reuse only the TEST transport harness. Product functions are not replaced.
    # Explicit stage avoids borrowing the V182 helper's old default argument.
    kwargs.setdefault('stage', native.STAGE)
    with monkeypatch.context() as patch:
        patch.setattr(old_gateway, 'native', native)
        patch.setattr(old_gateway, 'preparation', preparation)
        patch.setattr(old_gateway, 'scene', scene)
        return old_gateway.invoke(state, **kwargs)


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 16384,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def consumer_scene():
    """Author a new synthetic record; never relabel a persisted old Attempt."""
    c = old_consumer.scene()
    c.cap.id = identity(18311)
    c.cap.data['execution']['report_evidence_output'] = copy.deepcopy(execution()['report_evidence_output'])
    c.cap.content_hash = digest(c.cap.data)
    body = app.PreviewFlowCompatBody(**{**c.body.model_dump(), 'model_id': c.cap.id,
        'plan_id': identity(18312), 'activity_id': identity(18313), 'generation_profile': 'flow-v183'})
    prep = preparation.build_preparation(c.cap, c.source, c.plan, body.scope_id, note=body.note)
    budget = {**c.row.data['preview']['budget'], 'token_bound': prep['input_summary']['token_bound'],
        'grant_hash': digest({'synthetic_profile': 'flow-v183', 'activity': body.activity_id,
            'request_hash': prep['request_hash']})}
    preview = {'schema': app.PREVIEW, 'actor_id': c.p.user_id, 'workspace_id': c.p.workspace_id,
        'body': body.model_dump(), 'source_context': c.source, 'scope_plan': c.plan,
        'capability': app._cap_value(c.cap), 'preparation': prep, 'budget': budget,
        **app.FALSE_FLAGS, 'generation_profile': 'flow-v183'}
    preview['preview_hash'] = app.h(preview)
    start = {**body.model_dump(), 'request_id': body.activity_id, 'consent': True,
        'source_snapshot_hash': c.source['snapshot']['snapshot_hash'], 'preview_hash': preview['preview_hash']}
    raw = old_protocol.raw(start); parsed, exact, sha = app.read_wire(raw, app.StartFlowCompatBody)
    row = c.row
    row.id = identity(18314); row.request_id = body.activity_id; row.plan_id = body.plan_id
    row.model_id = c.cap.id; row.job_id = identity(18315); row.grant_hash = budget['grant_hash']
    row.data = {**row.data, 'preview': preview, 'command_refs': [{'id': identity(18316),
        'request_id': body.activity_id, 'operation': 'start', 'wire_sha256': sha}]}
    command = c.command
    command.id = identity(18316); command.request_id = body.activity_id; command.plan_id = row.plan_id
    command.attempt_id = row.id; command.body_hash = app.h(parsed.model_dump())
    command.path = app.PREFIX + '/start-flow-compatible'; command.exact_body = exact; command.wire_sha256 = sha
    command.data = {'schema': app.COMMAND, 'result': app.result(row, 'start')}
    command.command_hash = app.h(app._command_pin(command))
    anchors = [Audit(id=identity(18317), workspace_id=c.p.workspace_id, actor_id=c.p.user_id,
        action=app.COMMAND_AUDIT, object_id=command.id, data=app._command_anchor(command)),
        Audit(id=identity(18318), workspace_id=c.p.workspace_id, actor_id=c.p.user_id,
            action=app.STATE_AUDIT, object_id=row.id, data=app._state_anchor(row, None))]
    c.job.id = row.job_id; c.job.idempotency_key = 'scoped-design-v181:' + row.request_id
    c.job.data = {'actor_id': c.p.user_id, 'attempt_id': row.id}; c.job.lease_token = identity(18319)
    c.body, c.prep, c.raw, c.anchors = body, prep, raw, anchors
    return c


def reviewed_scene(monkeypatch):
    c = consumer_scene(); db = old_consumer.InventoryIO(c)
    call = old_consumer.reserved(c, db); saved = old_consumer.paid(c); reads, settlements = [], []
    def read(actual_db, actual_p, row, *, recover_saved):
        assert actual_db is db and actual_p is c.p and row is c.row
        assert row.reserved_call_id == call.id and app.journal_binding(row) == saved['binding']
        reads.append(recover_saved); return saved
    def settle(actual_db, call_id, receipt, expected, status):
        assert actual_db is db and call_id == call.id and receipt['call_id'] == call.id
        assert expected['request_hash'] == c.prep['request_hash']
        settlements.append(status); call.status = status
    monkeypatch.setattr(app, 'runtime', lambda: NS(read_response=read,
        dispatch=lambda *args, **kwargs: pytest.fail('Saved answer must not dispatch')))
    monkeypatch.setattr(cloud_budget, 'settle_paid_response', settle)
    data, status = app._read_paid(db, c.p, c.row, recover_saved=True)
    assert status == 'review'
    app._transition(db, c.p, c.row, data, status)
    return c, db, saved, reads, settlements


def test_new_gateway_uses_exact_native_body_and_bound_scope_without_a_second_call(native_transport, monkeypatch):
    prepares, parses = [], []; original_prepare, original_parse = native.prepare_request, native.parse_arguments
    def prepare(*args, **kwargs):
        prepares.append(copy.deepcopy(kwargs)); return original_prepare(*args, **kwargs)
    def parse(*args, **kwargs):
        parses.append(copy.deepcopy(kwargs)); return original_parse(*args, **kwargs)
    monkeypatch.setattr(native, 'prepare_request', prepare); monkeypatch.setattr(native, 'parse_arguments', parse)
    result, receipt = invoke(native_transport, monkeypatch); state = native_transport
    assert prepares and all(item == state.bound for item in prepares)
    assert parses == [{**state.bound, 'response_complete': True, 'http_content_encoding': None}]
    assert state.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'finish']
    assert len(state.requests) == len(state.reservations) == len(state.callbacks) == 1
    body = state.requests[0]['content']; request = json.loads(body)
    assert body == state.expected['body'] and state.local_preparation['schema'] == preparation.PREPARATION
    assert state.local_preparation['request_hash'] == hashlib.sha256(body).hexdigest()
    assert state.reservations[0] == (len(body) + 16384 + 1024, hashlib.sha256(body).hexdigest())
    assert request['max_tokens'] == 16384 and request['enable_thinking'] is False
    assert request['stream'] is False and request['n'] == 1
    assert native.request_inputs(request['messages']) == state.bound
    assert result['schema'] == wire.WIRE and receipt['delivery'] == native.DELIVERY != previous.DELIVERY
    assert receipt['response_body_sha256'] == hashlib.sha256(state.response).hexdigest()
    assert state.call.data['schema_proof_hash'] == digest(state.expected['schema_proof'])
    assert state.finished[-1][1] == 'succeeded'


def test_actual_provider_subset_only_omits_nonblank_patterns_and_retains_local_lengths(native_transport, monkeypatch):
    invoke(native_transport, monkeypatch); state = native_transport
    request = json.loads(state.requests[0]['content']); provider = request['response_format']['json_schema']['schema']
    local = wire.output_schema(state.bound['scope_binding']); restored = copy.deepcopy(provider)
    fields = [('AttributionV182', ['reason']), ('QuestionV182', ['question']), ('QuestionV182', ['reason']),
        ('TriggerV182', ['description']), ('NodeV182', ['name']), ('NodeV182', ['role']),
        ('NodeV182', ['responsibility']), ('NodeV182', ['inputs', 'items']), ('NodeV182', ['outputs', 'items']),
        ('ConditionEdgeV182', ['condition']), ('DeliverableV182', ['name']), ('DeliverableV182', ['description']),
        ('FlowV182', ['name']), ('BusinessDefinitionV182', ['name'])]
    for definition, path in fields:
        original = local['$defs'][definition]['properties']; target = restored['$defs'][definition]['properties']
        for key in path: original, target = original[key], target[key]
        assert original['pattern'] == r'\S' and 'pattern' not in target
        target['pattern'] = r'\S'
    assert restored == local
    assert provider['$defs']['NodeV182']['properties']['role']['minLength'] == 2
    assert provider['$defs']['NodeV182']['properties']['outputs']['items']['minLength'] == 2
    assert provider['$defs']['QuoteV182']['properties']['quote'] == local['$defs']['QuoteV182']['properties']['quote']
    assert provider['$defs']['QuoteV182']['properties']['quote']['maxLength'] == 1000000
    assert provider['$defs']['QuoteV182']['properties']['span_ref']['pattern'].startswith('^')
    proof = state.expected['schema_proof']
    assert proof['provider_schema'] == provider and proof['provider_schema_hash'] != proof['local_schema_hash']
    assert proof['schema_policy']['local_validation_required'] is True
    assert proof['schema_policy']['provider_support_verified'] is False
    old = previous.identity_schema_proof(local, previous.STAGE, **state.bound)
    assert old['provider_schema'] == local and old['proof_hash'] != proof['proof_hash']
    with pytest.raises(ValueError): native.check_schema_proof(old, local, native.STAGE, **state.bound)
    with pytest.raises(ValueError): previous.check_schema_proof(proof, local, previous.STAGE, **state.bound)


def test_registration_remains_explicit_and_local_wire_is_not_a_new_whole_report_family():
    e = execution(); manifest = Manifest(kind='foundation_model', namespace='public_synthetic', name='flow_compatible',
        version='1.0.0', owner='Synthetic fixture', description='No source, price or execution grant',
        execution=e, allowed_agent_roles=['business'], data_policy={'cloud_allowed': True})
    assert manifest.execution == e and validate_evidence_output(e) is e
    assert native.stage_policy(native.STAGE)['max_response_bytes'] == 1024 * 1024
    assert native.stage_policy(native.STAGE)['max_output_tokens'] == 32768
    assert profiles.modules(profiles.FLOW_COMPAT).wire is profiles.modules(profiles.FLOW).wire is wire
    assert profiles.modules(profiles.FLOW_COMPAT).native is native
    assert profiles.modules(profiles.FLOW).native is previous


@pytest.mark.parametrize('stage', [None, previous.STAGE, 'reduce-scope-v181', 'reduce-v179', 'reduce-v176', 'map-v172'])
def test_new_declaration_cannot_borrow_old_stage(native_transport, monkeypatch, stage):
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, monkeypatch, stage=stage)
    assert native_transport.events == native_transport.requests == []


def test_old_declaration_cannot_borrow_new_stage(native_transport, monkeypatch):
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, monkeypatch, e=old_gateway.execution())
    assert native_transport.events == []


@pytest.mark.parametrize('field', ['delivery', 'schema_proof_hash', 'profile_hash'])
def test_original_capture_cannot_borrow_old_delivery_or_proof(native_transport, monkeypatch, field):
    old_policy = previous.input_policy(old_gateway.execution())
    def mutate(context):
        if field == 'delivery': context[field] = previous.DELIVERY
        elif field == 'profile_hash': context[field] = digest(old_policy)
        else:
            local = wire.output_schema(native_transport.bound['scope_binding'])
            context[field] = digest(previous.identity_schema_proof(local, previous.STAGE, **native_transport.bound))
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, monkeypatch, mutate_context=mutate)
    assert len(native_transport.reservations) == 1 and native_transport.requests == []


@pytest.mark.parametrize('fault', ['blank_name', 'blank_reason', 'short_role', 'short_output', 'bad_target'])
def test_relaxed_provider_schema_never_relaxes_local_graph_or_nonblank_checks(native_transport, monkeypatch, fault):
    cap, source, plan = scene(); prep = preparation.build_preparation(cap, source, plan, 'custom:alpha')
    authored = old_protocol.answer(prep); flow = authored['business']['flows'][0]
    if fault == 'blank_name': flow['name'] = '  '
    elif fault == 'blank_reason': flow['nodes'][0]['attribution']['reason'] = '\t\n '
    elif fault == 'short_role': flow['nodes'][0]['role'] = '岗'
    elif fault == 'short_output': flow['nodes'][0]['outputs'] = ['值']
    else: flow['edges'][0]['to_node'] = 'missing'
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, monkeypatch, value=authored)
    state = native_transport; saved = capture.load(state.root, state.context)
    assert saved['ready'] and capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
    assert len(state.requests) == len(state.reservations) == len(state.callbacks) == 1
    assert state.finished[-1][1] == 'failed'
    assert state.events.index('callback') < state.events.index('finish')


def test_new_capture_cold_reads_only_as_183_and_never_changes_original_bytes(native_transport, monkeypatch):
    _, expected = invoke(native_transport, monkeypatch); state = native_transport
    body, receipt, _ = bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert body.encode('utf-8') == state.response and receipt == expected
    for stage in (previous.STAGE, 'reduce-scope-v181', None):
        with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
            native.input_policy(execution()), stage)
    assert len(state.requests) == 1


def test_old_182_capture_remains_old_and_cannot_be_redecoded_as_183(native_transport):
    _, expected = old_gateway.invoke(native_transport); state = native_transport
    body, receipt, _ = bridge.response_context(state.context, 'qwen', previous.MODEL,
        previous.input_policy(old_gateway.execution()), previous.STAGE)
    assert body.encode('utf-8') == state.response and receipt == expected
    with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(state.requests) == 1 and state.call.data['delivery'] == previous.DELIVERY


def test_partial_new_response_remains_unknown_and_is_not_reposted(native_transport, monkeypatch):
    state = native_transport; state.interrupted = True
    with pytest.raises(gateway.ModelBlocked): invoke(state, monkeypatch)
    saved = capture.load(state.root, state.context)
    assert saved['body_bytes'] == 17 and saved['response_complete'] is False
    with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(state.requests) == len(state.reservations) == 1 and state.callbacks == []


def test_consumer_profile_dto_paths_original_command_and_inventory_are_independent():
    c = consumer_scene(); old = old_consumer.scene(); db = old_consumer.InventoryIO(c)
    assert app.check_preview(c.row.data['preview'], c.p) is c.row.data['preview']
    assert app._family(c.prep).id == profiles.FLOW_COMPAT
    assert app._family(old.prep).id == profiles.FLOW
    assert app._start_path(app._body_family(c.body)) == app.PREFIX + '/start-flow-compatible'
    assert app._start_path(app._body_family(old.body)) == app.PREFIX + '/start-flow'
    paths = {(route.path, method) for route in app.router.routes for method in route.methods}
    assert (app.PREFIX + '/preview-flow-compatible', 'POST') in paths
    assert (app.PREFIX + '/start-flow-compatible', 'POST') in paths
    records, commands, calls = app._inventory(db, c.p)
    assert records[c.row.id] is c.row and commands[c.command.id] is c.command and calls == {}
    assert app.view(c.row)['generation_profile'] == 'flow-v183'
    original = app.original_request(c.row.request_id, c.p, db, hashlib.sha256(c.raw).hexdigest())
    assert original['found'] is True and original['result'] == c.command.data['result']
    for target in (app.StartFlowBody, app.StartBody):
        with pytest.raises(HTTPException): app.read_wire(c.raw, target)
    with pytest.raises(HTTPException): app.read_wire(old.raw, app.StartFlowCompatBody)


def test_new_profile_nonce_cannot_reuse_an_old_start_command(monkeypatch):
    old = old_consumer.scene(); db = old_consumer.InventoryIO(old)
    body = {**json.loads(old.raw), 'generation_profile': 'flow-v183'}
    raw = old_protocol.raw(body); parsed, exact, sha = app.read_wire(raw, app.StartFlowCompatBody)
    def forbidden(*args, **kwargs): raise AssertionError('Nonce conflict cannot admit or dispatch a new call')
    monkeypatch.setattr(app, 'runtime', forbidden)
    with pytest.raises(HTTPException) as conflict: app._start(db, old.p, parsed, exact, sha)
    assert conflict.value.status_code == 409 and db.calls == [] and len(db.rows) == 1
    assert old.command.path.endswith('/start-flow') and old.raw != raw


def test_resealed_new_preparation_cannot_borrow_old_schema_proof():
    c = consumer_scene(); altered = copy.deepcopy(c.prep); spec = altered['generation_contract']
    spec['schema_proof'] = previous.identity_schema_proof(spec['output_schema'], previous.STAGE,
        scope_binding=altered['scope_binding'])
    altered['generation_contract_hash'] = digest(spec)
    altered['preparation_hash'] = digest({key: value for key, value in altered.items() if key != 'preparation_hash'})
    with pytest.raises(ValueError): preparation.check_preparation(altered, c.cap, c.source, c.plan,
        c.body.scope_id, note=c.body.note)


def test_new_consumer_uses_shared_local_manual_but_keeps_183_call_and_original_answer(monkeypatch):
    c, db, saved, reads, settlements = reviewed_scene(monkeypatch)
    assert c.row.data['projection']['schema'] == wire.PROJECTION
    assert c.row.data['manual_value']['schema'] == wire.MANUAL
    assert c.row.data['manual_value']['acknowledge_partial_scope'] is False
    assert c.row.data['manual_value']['acknowledge_inferences'] is False
    assert c.row.data['call_pin']['delivery'] == native.DELIVERY
    assert app.dispatch_identity(c.row)['task_id'] == native.STAGE
    assert app.view(c.row)['result'] == c.row.data['projection']['wire']
    assert app.view(c.row)['generation_profile'] == 'flow-v183'
    assert c.row.data['response']['body_sha256'] == hashlib.sha256(saved['body']).hexdigest()
    before = app.h(c.row.data)
    data, status = app._read_paid(db, c.p, c.row, recover_saved=True)
    assert status == 'review' and app.h(data) == before
    assert reads == [True] and settlements == ['succeeded'] and len(db.calls) == 1


def test_saved_human_note_and_original_nonce_recovery_do_not_overwrite_new_machine_answer(monkeypatch):
    c, db, _, reads, settlements = reviewed_scene(monkeypatch)
    machine, response_hash = app.h(c.row.data['projection']), app.h(c.row.data['response'])
    value = copy.deepcopy(c.row.data['manual_value']); value['note'] = ' 原人工核对文字＋+🙂必须保留。 '
    request, raw = old_consumer.request(c, 'save-review', {'request_id': identity(899),
        'expected_version': c.row.version, 'value': value, 'confirmed': False})
    monkeypatch.setattr(app, 'lock_current_actor', lambda *args: None)
    result = asyncio.run(app.save_review_route(c.row.id, request, c.p, db))
    assert c.row.data['manual_value'] == value and app.h(c.row.data['projection']) == machine
    assert asyncio.run(app.save_review_route(c.row.id, request, c.p, db)) == result
    assert app.original_request(identity(899), c.p, db, hashlib.sha256(raw).hexdigest())['result'] == result
    recovery, recovery_raw = old_consumer.request(c, 'recover-saved', {'request_id': identity(898),
        'expected_version': c.row.version})
    recovered = asyncio.run(app.recover_route(c.row.id, recovery, c.p, db))
    assert app.original_request(identity(898), c.p, db, hashlib.sha256(recovery_raw).hexdigest())['result'] == recovered
    assert c.row.data['manual_value'] == value and app.h(c.row.data['projection']) == machine
    assert app.h(c.row.data['response']) == response_hash
    assert reads == [True] and settlements == ['succeeded'] and len(db.calls) == 1


def test_unknown_new_worker_reads_same_reserved_response_without_dispatch(monkeypatch):
    c = consumer_scene(); db = old_consumer.InventoryIO(c); call = old_consumer.reserved(c, db); reads = []
    frozen = copy.deepcopy(app.journal_binding(c.row))
    def read(actual_db, p, row, *, recover_saved):
        assert actual_db is db and p is c.p and row is c.row and recover_saved is True
        assert row.reserved_call_id == call.id and app.journal_binding(row) == frozen
        reads.append(call.id); return None
    def forbidden(*args, **kwargs): raise AssertionError('Unknown original call cannot issue another POST')
    monkeypatch.setattr(app, 'runtime', lambda: NS(read_response=read, dispatch=forbidden))
    first = app.run(db, c.p, c.job); second = app.run(db, c.p, c.job)
    assert first['status'] == second['status'] == 'response_unknown'
    assert reads == [call.id, call.id] and len(db.calls) == 1


def test_old_failed_182_answer_never_calls_new_decoder_or_replaces_failed_history(monkeypatch):
    c = old_consumer.scene(); db = old_consumer.InventoryIO(c); old_consumer.reserved(c, db)
    arguments = old_protocol.answer(c.prep); arguments['business']['flows'][0]['nodes'][0]['role'] = '岗'
    saved = old_consumer.paid(c, arguments); settlements = []
    def forbidden(*args, **kwargs): raise AssertionError('An old activity cannot borrow native183')
    monkeypatch.setattr(native, 'inspect_response', forbidden); monkeypatch.setattr(native, 'parse_arguments', forbidden)
    monkeypatch.setattr(app, 'runtime', lambda: NS(read_response=lambda *args, **kwargs: saved, dispatch=forbidden))
    monkeypatch.setattr(cloud_budget, 'settle_paid_response', lambda *args: settlements.append(args[-1]))
    data, status = app._read_paid(db, c.p, c.row, recover_saved=True)
    assert status == 'failed' and data['projection'] is data['manual_value'] is None
    assert data['response']['body_sha256'] == hashlib.sha256(saved['body']).hexdigest() and settlements == ['failed']
    app._transition(db, c.p, c.row, data, status); before = app.h(c.row.data)
    monkeypatch.setattr(app, 'runtime', lambda: NS(read_response=forbidden, dispatch=forbidden))
    again, status = app._read_paid(db, c.p, c.row, recover_saved=True)
    assert status == 'failed' and app.h(again) == before and settlements == ['failed']
    assert app._family(c.row.data['preview']['preparation']).native is previous
