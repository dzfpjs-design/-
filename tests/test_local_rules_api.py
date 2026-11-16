"""Disposable PostgreSQL checks for the real local-rule HTTP/storage boundary."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.local_rules_api import (CHECK_ACTION, CREATE_ACTION, CheckRule, CreateRule,
    create_rule, require_current_check)
from generic_agents.models import (Audit, Capability, CloudCall, Entity, EntityEvent, Invocation,
    Job, Membership, Observation, Task, User, Workspace)
from generic_agents.security import Principal
from tests.test_vertical_slice import manifest, publish


@pytest.fixture
def local_client(client):
    # The root integration owns api.py, including router registration.
    return client


@pytest.fixture
def prepared(local_client, account):
    schema = {'type': 'object', 'required': ['name', 'temperature', 'count'],
        'additionalProperties': False, 'properties': {'name': {'type': 'string'},
        'temperature': {'type': 'number', 'unit': 'C', 'minimum': -100, 'maximum': 100},
        'count': {'type': 'integer', 'minimum': 0}}}
    package = publish(local_client, manifest(specification={'entity_schemas': {'site': schema, 'other': schema}}))
    response = local_client.post('/api/entities', json={'package_id': package['id'], 'entity_type': 'site',
        'external_id': uuid4().hex, 'attributes': {'name': '合成测试对象', 'temperature': 20, 'count': 3}})
    assert response.status_code == 201, response.text
    obj = response.json()
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        principal = Principal(user.id, account[2], 'admin', 'local-rule-test')
    body = {'request_id': str(uuid4()), 'title': '温度阈值核对', 'entity_id': obj['id'],
        'rationale': '测试专用人工阈值，仅验证协议，不代表设备观测或业务建议',
        'rules': [{'id': 'warm', 'field': 'temperature', 'unit': 'C', 'operator': 'gt', 'threshold': 30},
                  {'id': 'cold', 'field': 'temperature', 'unit': 'C', 'operator': 'lt', 'threshold': 0},
                  {'id': 'few', 'field': 'count', 'unit': None, 'operator': 'lt', 'threshold': 2}]}
    return package, obj, principal, body


def create(client, body):
    response = client.post('/api/local-rules', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def check(client, cap, values=None):
    response = client.post('/api/local-rules/' + cap['id'] + '/check', json={
        'expected_version': cap['version'], 'values': values or {'temperature': 35, 'count': 3}})
    assert response.status_code == 200, response.text
    return response.json()


def publish_rule(client, cap):
    response = client.post('/api/local-rules/' + cap['id'] + '/publish', json={
        'expected_version': cap['version'], 'reason': '明确确认合成规则范围', 'confirm': True})
    assert response.status_code == 200, response.text
    return response.json()


def update_body(body, version, **changes):
    return {**{key: value for key, value in body.items() if key != 'request_id'},
            'expected_version': version, **changes}


def test_create_check_publish_proof_survives_sessions_without_business_side_effects(local_client, prepared, monkeypatch):
    package, obj, p, body = prepared
    import generic_agents.gateway as gateway
    def unexpected(*args, **kwargs):
        pytest.fail('Local sample checks must never invoke an execution gateway')
    monkeypatch.setattr(gateway, 'invoke', unexpected)
    with Session(engine()) as db:
        before_entity = copy.deepcopy(db.get(Entity, obj['id']).data)
        before_events = db.scalar(select(func.count()).select_from(EntityEvent))
        before_jobs = db.scalar(select(func.count()).select_from(Job))
    cap = create(local_client, body)
    assert cap['status'] == 'draft' and cap['version'] == 1 and cap['last_check'] is None
    assert cap['package_id'] == package['id'] and cap['entity_type'] == 'site'
    checked = check(local_client, cap)
    assert checked['status'] == 'validated' and checked['version'] == 2
    proof = checked['last_check']
    assert proof['definition_hash'] == checked['definition_hash']
    assert proof['check_mode'] == 'local_sample_only' and proof['is_device_observation'] is False
    assert [rule['matched'] for rule in proof['results']] == [True, False, False]
    assert proof['results'][0] == {'id': 'warm', 'field': 'temperature', 'value': 35, 'unit': 'C',
        'operator': 'gt', 'threshold': 30, 'matched': True}
    # A fresh authenticated HTTP client and database session see the same durable check.
    token = local_client.post('/api/tokens').json()['token']
    with TestClient(app) as reopened:
        reopened.headers.update({'Authorization': 'Bearer ' + token, 'X-Workspace-ID': p.workspace_id})
        assert reopened.get('/api/local-rules/' + cap['id']).json() == checked
    published = publish_rule(local_client, checked)
    assert published['status'] == 'published' and published['version'] == 3
    assert published['last_check'] == proof
    assert publish_rule(local_client, published) == published
    assert local_client.get('/api/local-rules/' + cap['id']).json() == published
    with Session(engine()) as db:
        row = db.get(Capability, cap['id'])
        assert row.validated_hash == row.content_hash == digest(row.data)
        assert row.data['execution'] == {'adapter': 'builtin', 'handler': 'numeric_rules'}
        assert row.data['data_policy'] == {'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False}
        assert row.data['allowed_agent_roles'] == ['perception'] and row.data['dependencies'] == [package['id']]
        assert require_current_check(db, p, row).id == proof['id']
        assert db.get(Entity, obj['id']).data == before_entity
        assert db.scalar(select(func.count()).select_from(EntityEvent)) == before_events
        for model in (Observation, Task, Invocation, CloudCall):
            assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__
        assert db.scalar(select(func.count()).select_from(Job)) == before_jobs
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == CHECK_ACTION)) == 1


def test_edit_invalidates_check_preserves_initial_idempotency_and_published_definition(local_client, prepared):
    _, _, _, body = prepared
    checked = check(local_client, create(local_client, body))
    changed_rules = copy.deepcopy(body['rules']); changed_rules[0]['threshold'] = 40
    response = local_client.put('/api/local-rules/' + checked['id'], json=update_body(body, checked['version'],
        title='更正的阈值', rules=changed_rules))
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed['status'] == 'draft' and changed['last_check'] is None
    assert changed['definition_hash'] != checked['definition_hash']
    assert create(local_client, body) == changed
    conflicting = {**body, 'title': '同请求不同内容'}
    assert local_client.post('/api/local-rules', json=conflicting).status_code == 409
    with Session(engine()) as db:
        assert db.get(Capability, changed['id']).validated_hash is None
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == CHECK_ACTION)) == 1
    assert local_client.post('/api/local-rules/' + changed['id'] + '/publish', json={
        'expected_version': changed['version'], 'reason': '旧试算不能发布', 'confirm': True}).status_code == 409
    frozen = publish_rule(local_client, check(local_client, changed))
    assert local_client.put('/api/local-rules/' + frozen['id'], json=update_body(body, frozen['version'])).status_code == 409
    assert local_client.post('/api/local-rules/' + frozen['id'] + '/check', json={
        'expected_version': frozen['version'], 'values': {'temperature': 35, 'count': 3}}).status_code == 409
    assert local_client.get('/api/local-rules/' + frozen['id']).json() == frozen
    cloned = create(local_client, {**body, 'request_id': str(uuid4()), 'title': '复制新草稿'})
    assert cloned['id'] != frozen['id'] and cloned['last_check'] is None and cloned['status'] == 'draft'


def test_noop_edit_still_requires_new_saved_check_and_compare_and_swap(local_client, prepared):
    _, _, _, body = prepared
    cap = check(local_client, create(local_client, body))
    route = '/api/local-rules/' + cap['id']
    response = local_client.put(route, json=update_body(body, cap['version']))
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited['definition_hash'] == cap['definition_hash'] and edited['last_check'] is None
    assert local_client.put(route, json=update_body(body, cap['version'])).status_code == 409
    assert local_client.post(route + '/check', json={'expected_version': cap['version'],
        'values': {'temperature': 35, 'count': 3}}).status_code == 409
    assert local_client.post(route + '/publish', json={'expected_version': edited['version'],
        'reason': '未经重新试算', 'confirm': True}).status_code == 409
    newer = check(local_client, edited)
    final = publish_rule(local_client, newer)
    assert local_client.post(route + '/publish', json={'expected_version': newer['version'],
        'reason': '过期发布请求', 'confirm': True}).status_code == 409
    assert final['last_check']['id'] != cap['last_check']['id']


@pytest.mark.parametrize('changes', [
    {'title': '   '}, {'rationale': '\n\t'}, {'request_id': 'not-a-uuid'}, {'rules': []},
    {'rules': [{'id': 'x', 'field': 'name', 'unit': None, 'operator': 'gt', 'threshold': 1}]},
    {'rules': [{'id': 'x', 'field': 'temperature', 'unit': 'F', 'operator': 'gt', 'threshold': 1}]},
    {'rules': [{'id': 'x', 'field': 'temperature', 'unit': 'C', 'operator': 'gt', 'threshold': True}]},
    {'rules': [{'id': 'x', 'field': 'temperature', 'unit': 'C', 'operator': 'gt', 'threshold': 1e16}]},
    {'rules': [{'id': 'x', 'field': 'temperature', 'unit': 'C', 'operator': 'gte', 'threshold': 1}]},
])
def test_invalid_creation_is_rejected_without_partial_rule(local_client, prepared, changes):
    _, _, _, body = prepared
    response = local_client.post('/api/local-rules', json={**body, **changes})
    assert response.status_code == 422, response.text
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Capability).where(Capability.namespace == 'platform_numeric_rules')) == 0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == CREATE_ACTION)) == 0


def test_rule_limit_and_duplicate_ids_are_rejected(local_client, prepared):
    _, _, _, body = prepared
    duplicate = [body['rules'][0], body['rules'][0]]
    too_many = [{**body['rules'][0], 'id': 'rule_' + str(index)} for index in range(33)]
    for rules in (duplicate, too_many):
        assert local_client.post('/api/local-rules', json={**body, 'rules': rules}).status_code == 422


@pytest.mark.parametrize('threshold', [float('nan'), float('inf'), float('-inf'), '30', True, 10**500])
def test_nonfinite_and_coerced_thresholds_are_contract_errors(threshold):
    with pytest.raises(ValidationError):
        CreateRule.model_validate({'request_id': str(uuid4()), 'title': '测试', 'rationale': '测试',
            'entity_id': str(uuid4()), 'rules': [{'id': 'x', 'field': 'temperature', 'operator': 'gt',
                                               'unit': 'C', 'threshold': threshold}]})


@pytest.mark.parametrize('values', [
    {'temperature': True, 'count': 3}, {'temperature': '35', 'count': 3}, {'temperature': 35},
    {'temperature': 35, 'count': 1.5}, {'temperature': 101, 'count': 3},
    {'temperature': 35, 'count': -1}, {'temperature': 35, 'count': 3, 'extra': 1},
])
def test_invalid_samples_never_validate_or_persist_evidence(local_client, prepared, values):
    _, _, _, body = prepared
    cap = create(local_client, body)
    response = local_client.post('/api/local-rules/' + cap['id'] + '/check',
        json={'expected_version': cap['version'], 'values': values})
    assert response.status_code == 422, response.text
    assert local_client.get('/api/local-rules/' + cap['id']).json() == cap
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == CHECK_ACTION)) == 0


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), 10**500])
def test_nonfinite_samples_rejected_before_storage(value):
    with pytest.raises(ValidationError):
        CheckRule.model_validate({'expected_version': 1, 'values': {'temperature': value}})


def test_exact_thresholds_do_not_match_and_same_field_multiple_rules_work(local_client, prepared):
    _, _, _, body = prepared
    cap = create(local_client, body)
    checked = check(local_client, cap, {'temperature': 30, 'count': 2})
    assert not any(result['matched'] for result in checked['last_check']['results'])
    checked = check(local_client, checked, {'temperature': -1, 'count': 1})
    assert [result['matched'] for result in checked['last_check']['results']] == [False, True, True]
    assert local_client.get('/api/local-rules/' + cap['id']).json()['last_check'] == checked['last_check']


def test_viewer_denied_writes_and_other_workspace_is_hidden(local_client, prepared, account):
    _, _, p, body = prepared
    cap = create(local_client, body)
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.user_id == p.user_id,
            Membership.workspace_id == p.workspace_id)); membership.role = 'viewer'
        another = Workspace(name='另一隔离工作区'); db.add(another); db.flush()
        db.add(Membership(user_id=p.user_id, workspace_id=another.id, role='admin'))
        another_id = another.id; db.commit()
    assert local_client.get('/api/local-rules/' + cap['id']).status_code == 200
    assert local_client.post('/api/local-rules', json=body).status_code == 403
    assert local_client.put('/api/local-rules/' + cap['id'], json=update_body(body, 1)).status_code == 403
    assert local_client.post('/api/local-rules/' + cap['id'] + '/check', json={
        'expected_version': 1, 'values': {'temperature': 35, 'count': 3}}).status_code == 403
    assert local_client.post('/api/local-rules/' + cap['id'] + '/publish', json={
        'expected_version': 1, 'reason': '无权限', 'confirm': True}).status_code == 403
    local_client.headers['X-Workspace-ID'] = another_id
    assert local_client.get('/api/local-rules').json() == []
    assert local_client.get('/api/local-rules/' + cap['id']).status_code == 404
    assert local_client.put('/api/local-rules/' + cap['id'], json=update_body(body, 1)).status_code == 404
    assert local_client.post('/api/local-rules/' + cap['id'] + '/check', json={
        'expected_version': 1, 'values': {'temperature': 35, 'count': 3}}).status_code == 404
    assert local_client.post('/api/local-rules/' + cap['id'] + '/publish', json={
        'expected_version': 1, 'reason': '跨区', 'confirm': True}).status_code == 404
    assert local_client.post('/api/local-rules', json={**body, 'request_id': str(uuid4())}).status_code == 404


def test_operator_can_save_check_but_cannot_publish(local_client, prepared):
    _, _, p, body = prepared
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.user_id == p.user_id,
            Membership.workspace_id == p.workspace_id)); membership.role = 'operator'; db.commit()
    checked = check(local_client, create(local_client, body))
    assert local_client.post('/api/local-rules/' + checked['id'] + '/publish', json={
        'expected_version': checked['version'], 'reason': '需要管理员确认', 'confirm': True}).status_code == 403


def test_stable_pagination_has_total_cursor_and_bounded_limit(local_client, prepared):
    _, _, _, body = prepared
    saved = [create(local_client, {**body, 'request_id': str(uuid4()), 'title': '规则' + str(i)}) for i in range(4)]
    first = local_client.get('/api/local-rules', params={'limit': 2})
    assert first.status_code == 200 and first.headers['X-Total-Count'] == '4'
    assert len(first.json()) == 2 and first.headers['X-Next-Cursor']
    second = local_client.get('/api/local-rules', params={'limit': 2, 'cursor': first.headers['X-Next-Cursor']})
    assert len(second.json()) == 2 and second.headers['X-Next-Cursor'] == ''
    assert {row['id'] for row in first.json() + second.json()} == {row['id'] for row in saved}
    assert local_client.get('/api/local-rules', params={'limit': 101}).status_code == 422
    assert local_client.get('/api/local-rules', params={'cursor': str(uuid4())}).status_code == 404


def test_concurrent_same_request_creates_one_durable_rule(local_client, prepared):
    _, _, p, body = prepared
    # Two authorized authors exercise request locking independently of account locks.
    with Session(engine()) as db:
        second = User(username='parallel_' + uuid4().hex[:12], password_hash='unused-test-only')
        db.add(second); db.flush()
        db.add(Membership(user_id=second.id, workspace_id=p.workspace_id, role='admin'))
        second_p = Principal(second.id, p.workspace_id, 'admin', 'second-local-rule-test'); db.commit()
    barrier = Barrier(2)
    def save(principal):
        with Session(engine(), expire_on_commit=False) as db:
            barrier.wait(timeout=5)
            return create_rule(CreateRule.model_validate(body), principal, db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [p, second_p]))
    assert results[0] == results[1]
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Capability).where(Capability.namespace == 'platform_numeric_rules')) == 1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == CREATE_ACTION)) == 1


@pytest.mark.parametrize('revocation', ['role', 'account'])
def test_stale_principal_cannot_write_after_role_or_account_revocation(local_client, prepared, revocation):
    _, _, p, body = prepared
    with Session(engine()) as db:
        if revocation == 'role':
            db.scalar(select(Membership).where(Membership.user_id == p.user_id,
                Membership.workspace_id == p.workspace_id)).role = 'viewer'
        else:
            db.get(User, p.user_id).active = False
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as rejected:
            create_rule(CreateRule.model_validate(body), p, db)
        assert rejected.value.status_code == 403
        assert not db.scalar(select(Capability).where(Capability.namespace == 'platform_numeric_rules'))


@pytest.mark.parametrize('tamper', ['execution', 'role', 'policy', 'unit', 'metadata', 'hash', 'initial_fingerprint'])
def test_tampered_definition_cannot_be_checked_or_published(local_client, prepared, tamper):
    _, _, p, body = prepared
    cap = check(local_client, create(local_client, body))
    with Session(engine()) as db:
        row = db.get(Capability, cap['id']); value = copy.deepcopy(row.data)
        if tamper == 'execution': value['execution']['side_effect'] = True
        elif tamper == 'role': value['allowed_agent_roles'].append('business')
        elif tamper == 'policy': value['data_policy']['cloud_allowed'] = True
        elif tamper == 'unit': value['specification']['rules'][0]['unit'] = 'F'
        elif tamper == 'metadata': value['specification']['managed_numeric_rule']['package_id'] = str(uuid4())
        elif tamper == 'initial_fingerprint': value['specification']['managed_numeric_rule']['creation_fingerprint'] = 'a' * 64
        else: value['specification']['rules'][0]['threshold'] = 40
        row.data = value
        if tamper != 'hash': row.content_hash = digest(value)
        db.commit()
    for action, payload in [('check', {'expected_version': cap['version'], 'values': {'temperature': 35, 'count': 3}}),
            ('publish', {'expected_version': cap['version'], 'reason': '拒绝变形契约', 'confirm': True})]:
        assert local_client.post('/api/local-rules/' + cap['id'] + '/' + action, json=payload).status_code in {409, 422}
    with Session(engine()) as db:
        assert db.get(Capability, cap['id']).status == 'validated'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == CHECK_ACTION)) == 1


@pytest.mark.parametrize('tamper', ['definition_hash', 'result', 'boolean_result', 'checked_version', 'sample_marker'])
def test_saved_proof_tamper_and_stale_proof_block_publish(local_client, prepared, tamper):
    _, _, _, body = prepared
    cap = check(local_client, create(local_client, body))
    with Session(engine()) as db:
        proof = db.get(Audit, cap['last_check']['id']); data = copy.deepcopy(proof.data)
        if tamper == 'definition_hash': data['definition_hash'] = '0' * 64
        elif tamper == 'result': data['results'][0]['matched'] = False
        elif tamper == 'boolean_result': data['results'][0]['matched'] = 1
        elif tamper == 'checked_version': data['checked_version'] = 'not-a-version'
        else: data['is_device_observation'] = True
        proof.data = data
        # ORM equality also treats True == 1; force the intentional tamper to reach PostgreSQL.
        flag_modified(proof, 'data')
        db.commit()
    assert local_client.post('/api/local-rules/' + cap['id'] + '/publish', json={
        'expected_version': cap['version'], 'reason': '不能复用破损依据', 'confirm': True}).status_code == 409


def test_public_registry_cannot_publish_managed_rule_without_current_saved_check(local_client, prepared):
    _, _, _, body = prepared
    cap = create(local_client, body)
    response = local_client.post('/api/capabilities/' + cap['id'] + '/validate', json={
        'expected_version': cap['version'], 'reason': '公开契约验证不是试算'})
    assert response.status_code == 200, response.text
    validated = response.json()
    # The root's services hook must enforce the same proof for the public publish endpoint.
    response = local_client.post('/api/capabilities/' + cap['id'] + '/publish', json={
        'expected_version': validated['version'], 'reason': '没有试算不能发布'})
    assert response.status_code == 409, response.text
    checked = check(local_client, local_client.get('/api/local-rules/' + cap['id']).json())
    response = local_client.post('/api/capabilities/' + cap['id'] + '/publish', json={
        'expected_version': checked['version'], 'reason': '当前规则已有持久试算'})
    assert response.status_code == 200, response.text
    assert local_client.get('/api/local-rules/' + cap['id']).json()['status'] == 'published'


def test_history_remains_readable_when_domain_dependency_is_unavailable(local_client, prepared):
    package, _, _, body = prepared
    cap = check(local_client, create(local_client, body))
    with Session(engine()) as db:
        db.get(Capability, package['id']).status = 'withdrawn'; db.commit()
    response = local_client.get('/api/local-rules/' + cap['id'])
    assert response.status_code == 200, response.text
    historical = response.json()
    assert historical['last_check'] == cap['last_check'] and historical['rules'] == cap['rules']
    assert historical['context_available'] is False and historical['context_warning']
    assert local_client.get('/api/local-rules').json() == [historical]
    assert local_client.put('/api/local-rules/' + cap['id'], json=update_body(body, cap['version'])).status_code == 409
    assert local_client.post('/api/local-rules/' + cap['id'] + '/check', json={
        'expected_version': cap['version'], 'values': {'temperature': 35, 'count': 3}}).status_code == 409
    assert local_client.post('/api/local-rules/' + cap['id'] + '/publish', json={
        'expected_version': cap['version'], 'reason': '依赖已停用', 'confirm': True}).status_code == 409
    assert create(local_client, body) == historical


def test_mqtt_binding_reuses_rules_within_same_domain_type_but_rejects_other_contexts(local_client, prepared, monkeypatch):
    from generic_agents.config import settings
    from generic_agents.mqtt_observations import checked_extractor
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', 'mqtt://127.0.0.1:1883')
    package, obj, p, body = prepared
    body = {**body, 'rules': [body['rules'][0]]}
    cap = create(local_client, body)
    response = local_client.post('/api/local-rules/' + cap['id'] + '/check', json={
        'expected_version': cap['version'], 'values': {'temperature': 35}})
    assert response.status_code == 200, response.text
    cap = publish_rule(local_client, response.json())
    def add_object(package_id, entity_type='site'):
        response = local_client.post('/api/entities', json={'package_id': package_id, 'entity_type': entity_type,
            'external_id': uuid4().hex, 'attributes': {'name': '合成来源绑定对象', 'temperature': 20, 'count': 3}})
        assert response.status_code == 201, response.text
        return response.json()
    same_type = add_object(package['id'])
    other_type = add_object(package['id'], 'other')
    second_package = publish(local_client, manifest(namespace='other_domain',
        specification=package['data']['specification']))
    other_domain = add_object(second_package['id'])
    configuration = {'broker': 'mqtt://127.0.0.1:1883',
        'topics': {'sensor/original': 'one', 'sensor/same': 'two', 'sensor/type': 'three', 'sensor/domain': 'four'},
        'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}},
        'fields': {}, 'username_ref': '', 'password_ref': ''}
    response = local_client.post('/api/mqtt/gateways', json={'name': '合成绑定协议测试', 'configuration': configuration})
    assert response.status_code == 201, response.text
    gateway = response.json()
    def bind(topic, entity):
        return local_client.post('/api/mqtt/gateways/' + gateway['id'] + '/bindings', json={
            'expected_version': gateway['version'], 'topic': topic, 'entity_id': entity['id'], 'extractor_id': cap['id']})
    assert bind('sensor/original', obj).status_code == 201
    assert bind('sensor/same', same_type).status_code == 201
    assert bind('sensor/type', other_type).status_code == 422
    assert bind('sensor/domain', other_domain).status_code == 422
    # Unit checks are exact; an empty source declaration never means Celsius.
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as rejected:
            checked_extractor(db, p, cap['id'], {'temperature'}, package_id=package['id'],
                entity_type='site', field_units={'temperature': ''})
        assert rejected.value.status_code == 422
        for model in (Observation, Task, Invocation, CloudCall):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_unpublished_package_and_cross_workspace_entities_cannot_seed_rules(local_client, prepared):
    package, _, p, body = prepared
    with Session(engine()) as db:
        db.get(Capability, package['id']).status = 'draft'; db.commit()
    assert local_client.post('/api/local-rules', json=body).status_code == 409
    assert local_client.post('/api/local-rules', json={**body, 'entity_id': str(uuid4())}).status_code == 404


def test_public_registry_cannot_forge_managed_creation_audit_or_pollute_local_list(local_client, prepared):
    _, _, _, body = prepared
    cap = create(local_client, body)
    with Session(engine()) as db:
        forged = copy.deepcopy(db.get(Capability, cap['id']).data)
    request_id = uuid4()
    forged['name'] = 'rule_' + request_id.hex
    forged['specification']['managed_numeric_rule']['creation_request_id'] = str(request_id)
    response = local_client.post('/api/capabilities', json=forged)
    assert response.status_code == 201, response.text
    forged_cap = response.json()
    response = local_client.post('/api/capabilities/' + forged_cap['id'] + '/validate', json={
        'expected_version': forged_cap['version'], 'reason': '没有人用入口创建依据'})
    assert response.status_code == 422, response.text
    assert local_client.get('/api/local-rules').json() == [cap]
