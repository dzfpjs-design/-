"""Normal authenticated registry and monitoring-schedule routes on owned PG.

No registry, source validator, gateway, authority, handler or SQL is mocked.
These tests establish publication/catalog admission and strict rejection/read
boundaries, not a genuinely adopted Task, successful installation/stop, device
operation or business value. Actual stop inventory/cold recovery has its own PG
tests; an unknown original nonce here must stay unknown and create nothing.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import business_flow_execution_api as catalog
from generic_agents import workflow_monitor_schedule_v200 as monitor
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.models import Acquisition, Audit, Capability, CloudCall, Evidence, Invocation
from generic_agents.workflow_abilities import eligible, eligible_service
from tests.test_workflow_service_registration_v195 import (
    authority, good, owned_postgres_only, publish)


BASE = '/api/monitoring-schedules'


def manifest():
    return Manifest(kind='tool', namespace='monitor_schedule_registration_v200',
        name='approved_check_cadence_' + uuid4().hex[:10], version=monitor.RELEASE_VERSION,
        owner='独立工程验收账号', description='调整已批准策略的检查间隔和重点顺序，不控制设备频率',
        allowed_agent_roles=['business'], input_schema=monitor.input_schema(),
        output_schema=monitor.output_schema(),
        execution={'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': monitor.OP, 'side_effect': True}, dependencies=[],
        data_policy={'cloud_allowed': False}, budget={'max_attempts': 1, 'max_concurrency': 1}
        ).model_dump(mode='json')


def effect_counts(db, workspace_id):
    models = (Invocation, CloudCall, Acquisition, Evidence, monitor.Head,
        monitor.Installation, monitor.Cycle, monitor.StopCommand)
    result = {model.__tablename__: db.scalar(select(func.count()).select_from(model).where(
        model.workspace_id == workspace_id)) for model in models}
    result['schedule_audits'] = db.scalar(select(func.count()).select_from(Audit).where(
        Audit.workspace_id == workspace_id,
        Audit.action.in_([monitor.INSTALLED, monitor.CONSUMED, monitor.STATE, monitor.STOPPED])))
    return result


def no_effects(workspace_id):
    with Session(engine()) as db:
        assert all(count == 0 for count in effect_counts(db, workspace_id).values())


def unapproved_inputs():
    # Valid DTO parameters, explicitly not a saved reasoning producer or an
    # approval. A 422 input rejection must not stand in for the actual 409 fence.
    candidate = 'bounded-check-option'
    analysis = {'summary': '仅作未批准调用的合同参数，尚无已保存资料来源',
        'candidate_assessments': [{'candidate_ref': candidate, 'assessment': 'insufficient',
            'reason': '尚未取得真实任务证据', 'evidence_refs': [],
            'missing_information': ['真实任务证据与批准']}],
        'recommendation': {'candidate_ref': None, 'reason': '不作执行推荐', 'evidence_refs': []}}
    return {'reasoning_result': {'schema': monitor.reasoning.RESULT, 'analysis': analysis,
            'review_state': 'needs_information', 'recommended_candidate_ref': None,
            'recommended_context': None, 'permissions_verified': False, 'can_dispatch': False},
        'policy_pin': {'policy_id': str(uuid4()), 'approved_hash': 'a' * 64},
        'expected_schedule_pin': None,
        'profiles': [{'candidate_ref': candidate, 'interval_seconds': 600,
            'priority_entity_ids': [],
            'valid_until': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}]}


def stop_body():
    return {'request_id': str(uuid4()),
        'expected_schedule_pin': {'installation_id': str(uuid4()), 'record_hash': 'b' * 64},
        'reason': '停止后续检查，不取消正在执行的设备动作', 'restore_legacy': False}


def test_real_monitor_publication_persists_exact_pins_and_only_195_service_catalog_admits_it(client, account):
    value = manifest(); row = publish(client, value)
    assert row['status'] == 'published'
    assert any(item['id'] == row['id'] and item['status'] == 'published'
        for item in good(client.get('/api/capabilities')))
    with Session(engine()) as db:
        cap = db.get(Capability, row['id']); p = authority(db, account)
        assert cap.workspace_id == p.workspace_id and cap.kind == 'tool' and cap.status == 'published'
        assert cap.content_hash == cap.validated_hash == digest(cap.data)
        assert cap.data['execution'] == value['execution']
        assert cap.data['input_schema'] == monitor.input_schema()
        assert cap.data['output_schema'] == monitor.output_schema()
        assert cap.data['input_schema']['properties']['expected_schedule_pin']['type'] == ['object', 'null']
        assert 'expected_schedule_pin' in cap.data['input_schema']['required']
        assert cap.data['dependencies'] == [] and cap.data['budget']['max_attempts'] == 1
        assert cap.data['data_policy']['cloud_allowed'] is False
        monitor.validate_manifest(cap.data)
        with pytest.raises(HTTPException) as legacy: eligible(cap, 'business')
        assert legacy.value.status_code == 409
        eligible_service(cap, 'business', service_actor='execution_service', operation=monitor.OP)
        for role, actor, operation in [('data', 'execution_service', monitor.OP),
                ('business', 'task_center', monitor.OP),
                ('business', 'execution_service', 'resense_with_receipt')]:
            with pytest.raises(HTTPException) as wrong:
                eligible_service(cap, role, service_actor=actor, operation=operation)
            assert wrong.value.status_code == 409
        members, choices = catalog._nested_catalog(db, p)
        assert any(member['user_id'] == p.user_id for member in members)
        choice = next(item for item in choices if item['id'] == cap.id)
        assert choice['hash'] == cap.content_hash and choice['release_version'] == cap.release_version
        assert choice['adapter'] == 'workflow_service' and choice['eligible_agent_roles'] == ['business']
        assert choice['service_actor'] == 'execution_service' and choice['operation'] == monitor.OP
        assert choice['requires_approved_plan'] is True and choice['cloud_allowed'] is False
        assert choice['input_schema'] == cap.data['input_schema']
        assert choice['output_schema'] == cap.data['output_schema'] and choice['max_attempts'] == 1
        assert not ({'execution', 'endpoint', 'secret_ref'} & set(choice))
        assert all(count == 0 for count in effect_counts(db, p.workspace_id).values())


@pytest.mark.parametrize('fault', ['missing_configuration_cas', 'side_effect_false', 'cloud_allowed', 'wrong_role'])
def test_real_monitor_registry_rejects_old_or_weakened_contract_before_publication(client, account, fault):
    value = manifest()
    if fault == 'missing_configuration_cas':
        value['input_schema']['properties'].pop('expected_schedule_pin')
        value['input_schema']['required'].remove('expected_schedule_pin')
    elif fault == 'side_effect_false': value['execution']['side_effect'] = False
    elif fault == 'cloud_allowed': value['data_policy']['cloud_allowed'] = True
    else: value['allowed_agent_roles'] = ['data']
    draft = good(client.post('/api/capabilities', json=value), 201)
    rejected = client.post('/api/capabilities/' + draft['id'] + '/validate', json={
        'expected_version': draft['version'], 'reason': '拒绝缺少配置版本或改变权限的监测调度能力'})
    assert rejected.status_code in {409, 422}, rejected.text[:900]
    with Session(engine()) as db:
        cap = db.get(Capability, draft['id'])
        assert cap.status != 'published' and cap.validated_hash != cap.content_hash
    no_effects(account[2])


def test_actual_gateway_rejects_valid_monitor_input_without_195_approval_before_any_new_effect(client, account):
    row = publish(client, manifest()); inputs = unapproved_inputs()
    assert monitor._inputs(inputs) == inputs
    with Session(engine()) as db:
        p = authority(db, account); before = effect_counts(db, p.workspace_id)
        assert all(count == 0 for count in before.values())
        with pytest.raises(HTTPException) as error:
            invoke(db, p, row['id'], 'business', inputs, 'unapproved-monitor-' + uuid4().hex)
        assert error.value.status_code == 409
        assert effect_counts(db, p.workspace_id) == before
    no_effects(account[2])


def test_normal_monitor_router_has_exact_absolute_methods_and_strict_queries(client, account):
    paths = client.app.openapi()['paths']
    expected = {BASE: {'get'}, BASE + '/{policy_id}/stop': {'post'},
        BASE + '/{policy_id}/stop-requests/{request_id}': {'get'}}
    assert {path for path in paths if 'monitoring-schedules' in path} == set(expected)
    for path, methods in expected.items(): assert set(paths[path]) == methods
    policy_id, request_id, task_id = str(uuid4()), str(uuid4()), str(uuid4())
    original = BASE + '/' + policy_id + '/stop-requests/' + request_id
    sha = 'a' * 64
    invalid_queries = [(BASE, None), (BASE, [('task_id', task_id), ('task_id', task_id)]),
        (BASE, {'task_id': task_id, 'extra': 'undeclared'}), (original, None),
        (original, [('expected_sha256', sha), ('expected_sha256', sha)]),
        (original, {'expected_sha256': sha, 'extra': 'undeclared'}),
        (original, {'expected_sha256': 'A' * 64})]
    for path, params in invalid_queries:
        rejected = client.get(path, params=params)
        assert rejected.status_code == 422, (path, rejected.status_code, rejected.text[:400])
    rejected = client.post(BASE + '/' + policy_id + '/stop', params={'extra': 'undeclared'}, json=stop_body())
    assert rejected.status_code == 422, rejected.text[:400]
    # A real scoped Task lookup must happen; no invented empty list is returned.
    assert client.get(BASE, params={'task_id': task_id}).status_code == 404
    no_effects(account[2])


def test_monitor_route_rejects_noncanonical_path_and_body_identifiers_as_422(client, account):
    policy_id, request_id = str(uuid4()), str(uuid4()); sha = 'a' * 64
    rejected = client.get(BASE, params={'task_id': 'not-a-uuid'})
    assert rejected.status_code == 422, rejected.text[:400]
    for path in [BASE + '/not-a-uuid/stop-requests/' + request_id,
            BASE + '/' + policy_id + '/stop-requests/not-a-uuid',
            BASE + '/AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA/stop-requests/' + request_id]:
        rejected = client.get(path, params={'expected_sha256': sha})
        assert rejected.status_code == 422, (path, rejected.status_code, rejected.text[:400])
    rejected = client.post(BASE + '/not-a-uuid/stop', json=stop_body())
    assert rejected.status_code == 422, rejected.text[:400]
    for field in ('request_id', 'installation_id'):
        body = stop_body()
        if field == 'request_id': body[field] = 'not-a-uuid'
        else: body['expected_schedule_pin'][field] = 'not-a-uuid'
        rejected = client.post(BASE + '/' + policy_id + '/stop', json=body)
        assert rejected.status_code == 422, (field, rejected.status_code, rejected.text[:400])
    no_effects(account[2])


def test_monitor_stop_rejects_wrong_duplicate_or_oversized_raw_body_without_creating_inventory(client, account):
    path = BASE + '/' + str(uuid4()) + '/stop'; base = stop_body()
    wrong = [[], {}, {**base, 'extra': 'undeclared'}, {**base, 'restore_legacy': 0},
        {**base, 'reason': '  '}, {**base, 'reason': '长' * 2001},
        {**base, 'expected_schedule_pin': {**base['expected_schedule_pin'], 'record_hash': 'A' * 64}}]
    for body in wrong:
        rejected = client.post(path, json=body)
        assert rejected.status_code == 422, (type(body).__name__, rejected.status_code, rejected.text[:400])
    raw = json.dumps(base, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    duplicate_top = raw[:-1] + b',"restore_legacy":false}'
    nested = copy.deepcopy(base)
    nested['expected_schedule_pin'] = {'installation_id': str(uuid4()), 'record_hash': 'b' * 64}
    nested_raw = json.dumps(nested, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    duplicate_nested = nested_raw.replace(b'"record_hash":', b'"record_hash":"' + b'b' * 64 + b'","record_hash":', 1)
    for invalid in (duplicate_top, duplicate_nested, b'{', b'\xff'):
        rejected = client.post(path, content=invalid, headers={'Content-Type': 'application/json'})
        assert rejected.status_code == 422, rejected.text[:400]
    oversized = {**base, 'reason': 'x' * 17000}
    rejected = client.post(path, content=json.dumps(oversized).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    assert rejected.status_code == 413, rejected.text[:400]
    # Shape-valid POST still cannot create a cancellation for an absent schedule.
    rejected = client.post(path, content=raw, headers={'Content-Type': 'application/json'})
    assert rejected.status_code == 409, rejected.text[:400]
    no_effects(account[2])


def test_unknown_stop_original_nonce_remains_exact_read_only_and_does_not_invent_a_stop(client, account):
    policy_id, request_id = str(uuid4()), str(uuid4()); sha = 'c' * 64
    path = BASE + '/' + policy_id + '/stop-requests/' + request_id
    expected = {'schema': monitor.STOP_RESULT + '/original', 'request_id': request_id,
        'found': False, 'body_sha256': sha, 'result': None}
    assert good(client.get(path, params={'expected_sha256': sha})) == expected
    assert good(client.get(path, params={'expected_sha256': sha})) == expected
    no_effects(account[2])
