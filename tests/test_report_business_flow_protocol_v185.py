"""Public synthetic new-activity transport checks; never claims AI quality."""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from generic_agents import cloud_evidence_profiles as policies
from generic_agents import report_business_flow_native_v185 as native
from generic_agents import report_business_flow_preparation_v185 as prep
from generic_agents import report_business_flow_wire_v185 as wire
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents import report_scoped_design_consumer_v181 as consumer
from generic_agents.contracts import digest
from generic_agents.gateway import cloud_json_options
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests import test_report_business_flow_protocol_v184 as original


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def denied(*args, **kwargs): raise AssertionError('Pure revision test attempted I/O')
    for name in ('connect', 'connect_ex'): monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)


def identity(number): return str(UUID(int=number))
def raw(value): return canonical_json(value).encode('utf-8')


def scene():
    cap, source, plan, previous, request = original.prepared()
    old_plan = original.answer(previous)
    binding = {'schema': wire.PARENT_BINDING, 'attempt_id': identity(1), 'producer_id': identity(2),
        'revision': 1, 'model_id': identity(3), 'reserved_call_id': identity(4), 'plan_id': identity(5),
        'request_hash': previous['request_hash'], 'capture_hash': 'a' * 64,
        'response_body_sha256': 'b' * 64, 'arguments_sha256': hashlib.sha256(raw(old_plan)).hexdigest(),
        'source_snapshot_hash': previous['source_snapshot_hash'], 'generation_profile': profiles.FLOW_PLAN,
        'preparation_hash': previous['preparation_hash'], 'scope_id': previous['scope_id'],
        'plan_hash': previous['plan_hash'], 'raw_plan_hash': digest(old_plan),
        'scope_binding_hash': digest(previous['scope_binding'])}
    parent = {'schema': wire.PARENT, 'binding': binding, 'raw_plan': old_plan}
    parent['parent_hash'] = digest(parent)
    cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    return cap, source, plan, parent, previous


def prepared():
    cap, source, plan, parent, previous = scene()
    value = prep.build_preparation(cap, source, plan, previous['scope_id'], parent_source=parent, note='保留原文＋🙂')
    rebuilt = prep.rebuild_request(value, cap, source, plan, previous['scope_id'], note='保留原文＋🙂')
    return cap, source, plan, parent, value, rebuilt


def answer(value):
    return {'schema': wire.WIRE, 'binding_hash': digest(value['scope_binding']),
        'parent_source_hash': value['parent_source_hash'], 'base_plan_hash': digest(value['parent_source']['raw_plan']),
        'platform_policy_hash': value['platform_policy_hash'], 'findings': [],
        'revised_plan': copy.deepcopy(value['parent_source']['raw_plan'])}


def test_exact_source_parent_policy_and_single_activity_request_are_separately_bound():
    cap, source, plan, parent, value, rebuilt = prepared()
    previous = original.prepared()[3]
    assert value['input_claims'] == previous['input_claims']
    assert value['payload']['spans'] == previous['payload']['spans']
    assert value['scope_binding'] == previous['scope_binding']
    assert value['parent_source'] == parent and value['parent_source_hash'] == digest(parent)
    assert native.request_inputs(rebuilt['messages']) == {'scope_binding': value['scope_binding'], 'parent_source': parent}
    assert json.loads(rebuilt['messages'][1]['content'])['platform_policy'] == value['payload']['platform_policy']
    assert rebuilt['request_body'] == raw(rebuilt['request'])
    assert hashlib.sha256(rebuilt['request_body']).hexdigest() == value['request_hash']
    assert prep.check_preparation(value, cap, source, plan, value['scope_id'], note='保留原文＋🙂') == value
    assert prep.stage_spec(value) == value['generation_contract']
    assert profiles.selected(value).id == profiles.FLOW_REVISION
    assert all(value[k] is False for k in ('semantic_quality_verified', 'aggregate_verified', 'can_apply', 'can_publish'))


def test_reasoning_has_explicit_total_token_and_cost_reservation():
    cap, _, _, _, value, rebuilt = prepared()
    request = rebuilt['request']; policy = native.input_policy(cap.data['execution'])
    assert request['enable_thinking'] is True and request['stream'] is False and request['n'] == 1
    assert request['thinking_budget'] == native.THINKING_BUDGET == 8192
    assert request['max_tokens'] == policy['max_answer_tokens'] == 32768
    total = native.output_token_bound(request['max_tokens'])
    assert total == policy['max_output_tokens'] == 40960
    assert value['input_summary']['token_bound'] == len(rebuilt['request_body']) + total + 1024
    assert policy == policies.evidence_policy(cap.data['execution'], native.STAGE)
    policies.validate_evidence_output(cap.data['execution'])
    for bad in (True, -1, 32769):
        with pytest.raises(ValueError): native.output_token_bound(bad)


def test_complete_actual_envelope_uses_only_final_content_and_keeps_parent_unmodified():
    _, source, _, parent, value, _ = prepared(); candidate = answer(value)
    Draft202012Validator(value['generation_contract']['output_schema']).validate(candidate)
    body = json.loads(original.original.response(json.dumps(candidate, ensure_ascii=False)))
    body['choices'][0]['message']['reasoning_content'] = 'private provider reasoning is not the plan'
    before = raw(parent)
    decoded = native.parse_arguments(raw(body), 200, native.STAGE, response_complete=True,
        http_content_encoding=None, scope_binding=value['scope_binding'], parent_source=parent)
    assert decoded == candidate
    projection = wire.build_projection(raw(decoded), scope_binding=value['scope_binding'], parent_source=parent,
        catalog=source['catalog'], allowed_span_refs=value['allowed_span_refs'], context=source['context'],
        input_claims=value['input_claims'])
    assert projection['wire']['schema'] == 'report-business-flow-wire-v182'
    assert projection['raw_review'] == candidate and raw(parent) == before
    manual = wire.new_manual(projection)
    assert wire.validate_value(manual, scope_binding=value['scope_binding'], catalog=source['catalog'],
        allowed_span_refs=value['allowed_span_refs'], context=source['context'], input_claims=value['input_claims']) == manual
    assert projection['semantic_quality_verified'] is projection['can_execute'] is False


@pytest.mark.parametrize('kind', ['policy', 'parent', 'claims'])
def test_request_cannot_replace_policy_parent_or_selected_claims(kind):
    _, _, _, _, _, rebuilt = prepared(); messages = copy.deepcopy(rebuilt['messages'])
    payload = json.loads(messages[-1]['content'])
    if kind == 'policy': payload['platform_policy']['authority'] = 'report overrides all roles'
    elif kind == 'parent': payload['parent_source']['raw_plan']['business']['flows'][0]['name'] = 'another plan'
    else: payload['input_claims'][0]['text'] = 'invented data'
    messages[-1]['content'] = raw(payload).decode()
    with pytest.raises(ValueError): native.request_inputs(messages)


def test_schema_shape_cannot_relax_finding_or_nested_plan_constraints():
    cap, _, _, parent, value, _ = prepared(); schema = value['generation_contract']['output_schema']
    options = native.dispatch_options(cap.data['execution'], cloud_json_options(cap.data['execution']))
    assert native.schema_shape_proof(schema, native.STAGE)['source_binding_verified'] is False
    changed = copy.deepcopy(schema); changed['$defs']['FindingV185']['additionalProperties'] = True
    with pytest.raises(ValueError): native.strict_options(changed, options, native.STAGE,
        scope_binding=value['scope_binding'], parent_source=parent)
    options['enable_thinking'] = False
    with pytest.raises(ValueError): native.strict_options(schema, options, native.STAGE,
        scope_binding=value['scope_binding'], parent_source=parent)


def test_revision_route_requires_explicit_canonical_parent_and_original_activity_nonce():
    _, _, _, parent, value, _ = prepared()
    body = {'plan_id': identity(5), 'expected_plan_version': 1, 'plan_hash': value['plan_hash'],
        'scope_id': value['scope_id'], 'revision': 1, 'model_id': identity(6), 'note': '',
        'activity_id': identity(7), 'generation_profile': profiles.FLOW_REVISION,
        'parent_attempt_id': parent['binding']['attempt_id']}
    checked, exact, sha = consumer.read_wire(raw(body), consumer.PreviewFlowRevisionBody)
    assert checked.parent_attempt_id == identity(1) and exact.encode() == raw(body)
    assert sha == hashlib.sha256(raw(body)).hexdigest()
    for changed in ({k: v for k, v in body.items() if k != 'parent_attempt_id'},
            {**body, 'parent_attempt_id': identity(1).upper()}, {**body, 'generation_profile': profiles.FLOW_PLAN}):
        # UUID with only digits has no uppercase variant; use malformed canonical form.
        if changed.get('parent_attempt_id') == body['parent_attempt_id'] and changed.get('generation_profile') == profiles.FLOW_REVISION:
            changed['parent_attempt_id'] = '{' + identity(1) + '}'
        with pytest.raises(Exception): consumer.read_wire(raw(changed), consumer.PreviewFlowRevisionBody)


def test_admission_cold_reads_parent_and_rejects_other_scope_without_human_draft(monkeypatch):
    from generic_agents import report_business_flow_revision_source_v185 as source_loader
    from generic_agents import business_flow_review_source as reviews
    from fastapi import HTTPException
    _, context, _, parent, old = scene(); p = SimpleNamespace(user_id=identity(8), workspace_id=identity(9))
    row = SimpleNamespace(id=identity(1), plan_id=identity(5), plan_version=1, plan_hash=old['plan_hash'],
        scope_id=old['scope_id'], producer_id=identity(2), revision=1, reserved_call_id=identity(4),
        status='needs_clarification', data={'preview': {'preparation': old, 'source_context': context},
            'projection': {'raw_plan': parent['raw_plan']}, 'manual_value': {'do_not_read': True}})
    binding = {k: v for k, v in parent['binding'].items() if k in {'attempt_id','producer_id','revision',
        'model_id','request_hash','reserved_call_id','capture_hash','response_body_sha256','arguments_sha256',
        'source_snapshot_hash','generation_profile'}}
    binding['schema'] = 'business-flow-review-source-v1'
    calls = []
    monkeypatch.setattr(consumer, 'checked_attempt', lambda *a, **k: row)
    def cold(*args): calls.append('cold actual capture read'); return {'source_binding': binding}
    monkeypatch.setattr(reviews, 'load', cold)
    body = SimpleNamespace(parent_attempt_id=row.id, expected_plan_version=1, plan_hash=row.plan_hash,
        scope_id=row.scope_id, revision=1)
    plan = SimpleNamespace(id=row.plan_id, producer_id=row.producer_id)
    assert source_loader.load_parent(None, p, plan, body, context) == parent
    assert calls == ['cold actual capture read']
    body.scope_id = 'other'
    with pytest.raises(HTTPException): source_loader.load_parent(None, p, plan, body, context)
    assert len(calls) == 1 and row.data['manual_value'] == {'do_not_read': True}


def test_slow_revision_admission_does_not_block_the_http_event_loop(monkeypatch):
    import asyncio
    import threading
    from starlette.requests import Request
    _, _, _, parent, value, _ = prepared()
    body = {'plan_id': identity(5), 'expected_plan_version': 1, 'plan_hash': value['plan_hash'],
        'scope_id': value['scope_id'], 'revision': 1, 'model_id': identity(6), 'note': '',
        'activity_id': identity(7), 'generation_profile': profiles.FLOW_REVISION,
        'parent_attempt_id': parent['binding']['attempt_id']}
    started, release = threading.Event(), threading.Event()
    order = []
    def slow_admission(*args):
        started.set()
        assert release.wait(2), 'the event loop must remain able to service another request'
        order.append('admission completed')
        return {'synthetic_preview': True}
    monkeypatch.setattr(consumer, 'prepare', slow_admission)
    monkeypatch.setattr(consumer, 'preview_view', lambda value: value)
    async def receive(): return {'type': 'http.request', 'body': raw(body), 'more_body': False}
    async def run():
        request = Request({'type': 'http', 'method': 'POST',
            'path': consumer.PREFIX + '/preview-flow-revision', 'headers': []}, receive)
        task = asyncio.create_task(consumer.preview_flow_revision_route(request, None, None))
        while not started.is_set(): await asyncio.sleep(.001)
        order.append('another request serviced'); release.set()
        assert await task == {'synthetic_preview': True}
    asyncio.run(run())
    assert order == ['another request serviced', 'admission completed']
