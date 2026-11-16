"""Finite request/eligibility and real ASGI route mechanics, not source approval."""
import asyncio
import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from generic_agents import media_daily_task_adapter as a, media_daily_task_api as api
from generic_agents.contracts import digest
from generic_agents.security import Principal


def value():
    return {'schema': 'media-daily-task-preview-v226', 'instance_version': 1,
        'release_id': str(uuid4()), 'configuration_hash': 'a'*64, 'business_id': 'daily-business',
        'entity_id': str(uuid4()), 'camera_id': str(uuid4()), 'camera_version': 1, 'camera_hash': 'b'*64,
        'start_day': '2026-10-27', 'end_day': '2026-10-28', 'expires_at': '2026-10-29T12:00:00+08:00',
        'max_tasks': 2, 'frame_rule': 'earliest_verified_frame', 'allow_incomplete': False}


def encode(body): return json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()


def test_complete_finite_scope_is_explicit_and_keeps_the_original_china_dates():
    body = value(); parsed = a.parse(encode(body), a.Preview)
    assert a.days(a.configuration(parsed)) == ['2026-10-27', '2026-10-28']
    assert a.configuration(parsed) == body and parsed.allow_incomplete is False
    body.update(schema='media-daily-task-enable-v226', request_id=str(uuid4()), preview_hash='c'*64, confirm_scope=True)
    assert a.configuration(a.parse(encode(body), a.Enable))['schema'] == 'media-daily-task-preview-v226'
    assert a.instant(body['expires_at']) == datetime(2026, 10, 29, 4, tzinfo=timezone.utc)


@pytest.mark.parametrize('change', [
    {'max_tasks': True}, {'max_tasks': 1.0}, {'max_tasks': 3}, {'max_tasks': 0},
    {'instance_version': False}, {'camera_version': 0}, {'allow_incomplete': 1},
    {'end_day': '2026-11-03'}, {'end_day': '2026-10-26'}, {'start_day': '2026-02-30'},
    {'expires_at': '2026-10-29T12:00:00'}, {'camera_hash': 'not-a-hash'}, {'unknown': 'ignored?'},
])
def test_bad_scope_is_rejected_without_defaulting_or_coercion(change):
    body = value(); body.update(change)
    with pytest.raises(HTTPException): a.parse(encode(body), a.Preview)


@pytest.mark.parametrize('raw', [b'{"schema":"x","schema":"y"}', b'[]', b'{"x":NaN}', b'\xff'])
def test_ambiguous_or_non_json_wire_is_rejected(raw):
    with pytest.raises(HTTPException): a.parse(raw, a.Preview)


@pytest.mark.parametrize('consent', [False, 1, 'true', None])
def test_enable_needs_an_actual_human_boolean_consent(consent):
    body = value(); body.update(schema='media-daily-task-enable-v226', request_id=str(uuid4()),
        preview_hash='c'*64, confirm_scope=consent)
    with pytest.raises(HTTPException): a.parse(encode(body), a.Enable)


@pytest.fixture
def eligibility(monkeypatch):
    """Only eligibility predicates: source/ORM authority is explicitly substituted."""
    from generic_agents import instance_api, entity_release_compat, instance_semantic_runtime_dispatch
    from generic_agents import graph_task_inputs_v120, runtime_task_documents, business_flow_execution_runtime
    body = value(); identity = str(uuid4()); camera = NS(id=body['camera_id'], version=1,
        gateway_id=str(uuid4()), path='camera/front', data={'name': '摄像头'}, status='validated')
    body['camera_hash'] = a.camera_hash(camera)
    instance = NS(id=identity, version=1, data={'enabled_business_ids': ['daily-business']})
    business = {'id': 'daily-business', 'name': '资料技术整理', 'available': True,
        'execution_mode': 'confirmed_graph_workflow'}
    release = NS(id=body['release_id'], configuration_hash=body['configuration_hash'],
        data={'snapshot': {'profile': {'simulation_v176': True}, 'businesses': [business]}})
    cap = NS(id=str(uuid4()), content_hash='d'*64, release_version='1',
        data={'execution': {'side_effect': False}, 'data_policy': {'cloud_allowed': False}})
    graph = {'input_schema': {'type': 'object', 'properties': {'task_image_ids': {
        'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {'type': 'string'}}}},
        'nodes': [{'kind': 'ability', 'execution': {'capability_id': cap.id, 'capability_hash': cap.content_hash}}]}
    binding = {'schema': 'business-flow-execution-binding-v1', 'graph': graph, 'binding_hash': 'e'*64,
        'role_bindings': {'data': str(uuid4())}}
    inputs = {'user_input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
    extra_ports = []; observed = []
    runtime = NS(verify_release=lambda *args, **kw: observed.append('release'),
        business_binding=lambda *args: binding)
    entity = NS(id=body['entity_id'], version=1, data={})
    gateway = NS(id=camera.gateway_id, version=1, data={'name': '网关'})
    monkeypatch.setattr(instance_api, 'active', lambda *args: (instance, release))
    monkeypatch.setattr(instance_semantic_runtime_dispatch, 'modern', lambda _: runtime)
    monkeypatch.setattr(entity_release_compat, 'require_entity', lambda *args, **kw: {'scope': 'fixture'})
    monkeypatch.setattr(graph_task_inputs_v120, 'describe', lambda *args: inputs)
    monkeypatch.setattr(runtime_task_documents, 'ports', lambda _: extra_ports)
    monkeypatch.setattr(business_flow_execution_runtime, 'require_task_start',
        lambda _, **kw: observed.append(kw))
    monkeypatch.setattr(a, 'published', lambda *args: cap)
    monkeypatch.setattr(a, 'scoped', lambda db, model, key, p: {entity.id: entity, camera.id: camera, gateway.id: gateway}[key])
    return NS(**locals())


def test_technical_scope_keeps_real_entry_gate_and_requires_no_external_control(eligibility):
    s = eligibility
    pin = a.target(None, None, s.identity, s.body)
    assert pin['instance_id'] == s.identity and all(value is not None for value in pin.values())
    assert s.observed == ['release', {'verified_anomaly_handoff': False}]


@pytest.mark.parametrize('status', ['confirmed', 'draft', 'disabled', 'failed'])
def test_target_needs_the_actual_validated_channel_state(eligibility, status):
    s = eligibility; s.camera.status = status; s.body['camera_hash'] = a.camera_hash(s.camera)
    with pytest.raises(HTTPException) as error: a.target(None, None, s.identity, s.body)
    assert error.value.status_code == 409


def test_failed_gateway_does_not_make_a_validated_historical_camera_ineligible(eligibility):
    s = eligibility; s.gateway.status = 'failed'; s.gateway.data = {'error': 'offline test fixture'}
    pin = a.target(None, None, s.identity, s.body)
    assert pin['camera_id'] == s.camera.id and pin['gateway_hash'] == digest(s.gateway.data)


@pytest.mark.parametrize('case', ['not_trial', 'disabled', 'side_effect', 'cloud', 'extra_input', 'source_set', 'not_single_image'])
def test_trial_flag_never_substitutes_for_actual_inputs_or_capability_boundaries(eligibility, case):
    s = eligibility
    if case == 'not_trial': s.release.data['snapshot']['profile']['simulation_v176'] = False
    elif case == 'disabled': s.instance.data['enabled_business_ids'] = []
    elif case == 'side_effect': s.cap.data['execution']['side_effect'] = True
    elif case == 'cloud': s.cap.data['data_policy']['cloud_allowed'] = True
    elif case == 'extra_input': s.inputs['user_input_schema'].update(properties={'required_data': {'type': 'string'}}, required=['required_data'])
    elif case == 'source_set': s.extra_ports.append({'port': 'real-document-set'})
    else: s.graph['input_schema']['properties']['task_image_ids']['maxItems'] = 0
    with pytest.raises(HTTPException): a.target(None, None, s.identity, s.body)


@pytest.mark.parametrize('route', ['preview', 'enable', 'pause'])
def test_routes_yield_the_async_loop_while_the_sync_transaction_runs(monkeypatch, route):
    started, release = threading.Event(), threading.Event(); calls = []
    body = value()
    if route == 'enable': body.update(schema='media-daily-task-enable-v226', request_id=str(uuid4()), preview_hash='c'*64, confirm_scope=True)
    if route == 'pause': body = {'schema': 'media-daily-task-pause-v226', 'request_id': str(uuid4()), 'expected_version': 1, 'reason': '先暂停核对'}
    raw = encode(body); done = False
    async def receive():
        nonlocal done
        if done: return {'type': 'http.disconnect'}
        done = True; return {'type': 'http.request', 'body': raw, 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}, receive)
    db = NS(rollback=lambda: calls.append('rollback'))
    def service(*args, **kw):
        if route != 'preview': a.parse(args[3], a.Pause if route == 'pause' else a.Enable)
        started.set(); assert release.wait(3); calls.append(route); return {'route': route}
    monkeypatch.setattr(a, 'preview' if route == 'preview' else 'command', service)
    async def run():
        args = [uuid4()]
        if route == 'pause': args.append(uuid4())
        task = asyncio.create_task(getattr(api, route)(*args, request, NS(), db))
        try:
            for _ in range(200):
                if started.is_set(): break
                await asyncio.sleep(.005)
            assert started.is_set() and not task.done()
            release.set(); assert await task == {'route': route}
        finally:
            release.set(); await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run()); assert calls == [route]


@pytest.mark.parametrize('route', ['preview', 'enable', 'pause'])
def test_stream_capacity_rejects_before_any_service_or_transaction(monkeypatch, route):
    called = []; monkeypatch.setattr(a, 'command', lambda *args, **kw: called.append(True))
    monkeypatch.setattr(a, 'preview', lambda *args, **kw: called.append(True))
    async def receive(): return {'type': 'http.request', 'body': b'x'*(a.MAX_BODY+1), 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}, receive)
    async def run():
        args = [uuid4()]
        if route == 'pause': args.append(uuid4())
        with pytest.raises(HTTPException) as error: await getattr(api, route)(*args, request, NS(), NS())
        assert error.value.status_code == 413
    asyncio.run(run()); assert not called
