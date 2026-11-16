"""Real option, price and quota writers over declared IO boundaries.

The reused price fixture supplies synthetic HTML via an HTTP spy, storage and
database adapters, and explicit IAM/publication IO. No PG, official fetch,
actual account, model call, payment or durable runtime acceptance is claimed.
Actual price parsing, independent underlying Audit records, wrappers and cold
receipt readers execute; no mutator is substituted for a GET.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_options_v310 as s
from generic_agents.contracts import digest
from generic_agents.models import Task
from generic_agents.security import Principal
from tests.test_same_goal_advice_price_v307 import scene as price_scene


def uid(): return str(uuid4())


@pytest.fixture
def scene(monkeypatch):
    t = price_scene.__wrapped__(monkeypatch)
    task = Task(id=uid(), workspace_id=t.p.workspace_id, owner_id=t.p.user_id,
        package_id=uid(), title='实际任务引用 IO', version=1, status='workflow_active', data={})
    source_data = {'execution': {'model_profile_id': t.model.id, 'model_profile_hash': t.model.content_hash}}
    source = s.Capability(id=uid(), workspace_id=t.p.workspace_id, kind='tool', status='published',
        data=source_data, content_hash=digest(source_data), validated_hash=digest(source_data), release_version='1.0.0')
    t.rows[s.Capability].append(source)
    pin = {'capability_id': source.id, 'capability_hash': source.content_hash,
        'release_version': source.release_version, 'agent_role': 'business'}
    selected = {'binding': {'graph': {'nodes': [{'id': 'reasoning', 'kind': 'ability', 'execution': pin}]}}}
    scope = {'workspace_id': t.p.workspace_id, 'task_id': task.id, 'instance_id': uid(), 'release_id': uid(),
        'package_id': task.package_id, 'business_id': 'original-business', 'goal_version': 1,
        'goal_hash': 'a' * 64, 'goal_snapshot_hash': 'b' * 64, 'native_goal_context_hash': 'c' * 64,
        'source_binding_hash': 'd' * 64}
    goal = {'spec': {'max_tool_calls': 12, 'max_steps': 12, 'deadline': None}}
    context = {'scope': scope, 'model': t.model, 'source': source,
        'advice': NS(id=uid(), content_hash='e' * 64, release_version='1.0.0'),
        'goal': goal, 'document_required': True}
    quota_body = {'request_id': uid(), 'expected_goal_hash': scope['goal_hash'],
        'expected_mature_head_hash': 'f' * 64, 'max_cost_minor_units': 100,
        'currency': 'CNY', 'validity_seconds': 7200, 'reason': '明确配置一次费用上限'}
    t.task = task; t.context = context; t.quota_body = quota_body
    t.config_reads = 0; t.occupied = []; t.live_config = True; t.nonce_calls = []
    def nonce_io(db, p, *, action, task_id, body, subject_id=None, advice_id=None):
        # Dedicated transaction lock and cross-family ownership inventory IO;
        # the actual task wrapper/price/quota receipts below remain unmocked.
        assert db is t.db and task_id == task.id and subject_id == task.id and advice_id is None
        assert action in {s.PRICE_ACTION, s.QUOTA_ACTION} and type(body) is dict and 'request_id' in body
        t.nonce_calls.append({'action': action, 'task_id': task_id, 'actor_id': p.user_id,
            'subject_id': subject_id, 'advice_id': advice_id, 'body': copy.deepcopy(body),
            'config_reads_before': t.config_reads})
    monkeypatch.setattr(s.store.command_nonce, 'acquire', nonce_io)
    def task_read(db, p, identity, *, lock=False):
        assert db is t.db and p.workspace_id == task.workspace_id
        if identity != task.id: raise HTTPException(404, 'scoped task IO missing')
        return task
    monkeypatch.setattr(s.store.existing, '_task', task_read)
    monkeypatch.setattr(s, 'lock_current_actor', lambda db, p: None)  # Current User/Member lock IO.
    monkeypatch.setattr(s.quota, 'lock_current_actor', lambda db, p: None)
    monkeypatch.setattr(s.store.provider, 'binding', lambda db, p, actual: selected)
    def configuration(db, p, actual, body):
        t.config_reads += 1
        if not t.live_config: raise HTTPException(409, 'actual Goal/release/counter/head IO refused')
        assert actual is task and body['expected_goal_hash'] == quota_body['expected_goal_hash']
        assert body['expected_mature_head_hash'] == quota_body['expected_mature_head_hash']
        return context
    monkeypatch.setattr(s.quota, '_configuration_base', configuration)
    monkeypatch.setattr(s.quota, '_configuration', configuration)
    monkeypatch.setattr(s.store, '_policy_starts', lambda *args: list(t.occupied))
    t.price = lambda body=None, p=None: s.price(t.db, p or t.p, task.id, body or t.body)
    t.quota = lambda body=None, p=None: s.configure_quota(t.db, p or t.p, task.id, body or quota_body)
    t.receipts = lambda request_id, p=None: s.read_receipts(t.db, p or t.p, task, request_id)
    return t


def test_price_command_binds_actual_fixed_model_and_underlying_registration_in_one_uncommitted_transaction(scene):
    t = scene; result = t.price()
    assert result['basis_ref']['id'] in {row.id for row in t.rows[s.Audit] if row.action == s.prices.PRICE_BASIS}
    candidates = t.receipts(t.body['request_id']); assert len(candidates) == 1
    value = candidates[0]
    assert value['plan'] is None and value['operation'] == 'price' and value['body'] == t.body
    assert value['actor'] == t.p.user_id and value['read_result']()['basis_ref'] == result['basis_ref']
    assert t.fetches == 1
    assert t.nonce_calls == [{'action': s.PRICE_ACTION, 'task_id': t.task.id, 'actor_id': t.p.user_id,
        'subject_id': t.task.id, 'advice_id': None, 'body': t.body, 'config_reads_before': 0}]


def test_price_nonce_retry_and_read_never_fetch_or_renew_after_expiry_or_retirement(scene, monkeypatch):
    t = scene; result = t.price(); originals = copy.deepcopy([row.data for row in t.rows[s.Audit]])
    t.time += timedelta(hours=2); t.model.status = 'retired'
    def no_write(*args, **kwargs): raise AssertionError('original receipt cannot call authorizer/fetch')
    monkeypatch.setattr(s.prices, 'authorize', no_write)
    monkeypatch.setattr(s.prices, '_fetch', no_write)
    retried = t.price(); read = t.receipts(t.body['request_id'])[0]['read_result']()
    assert retried['basis_ref'] == read['basis_ref'] == result['basis_ref']
    assert t.fetches == 1 and [row.data for row in t.rows[s.Audit]] == originals


def test_quota_command_derives_exact_model_limits_and_saves_original_resolved_expiry(scene):
    t = scene; t.price(); result = t.quota()
    assert result['limits']['max_tokens'] == 4096 and result['limits']['max_request_bytes'] == 50000
    assert result['limits']['max_evidence_items'] == 2 and result['configuration_only'] is True
    assert result['pricing_verified'] is result['cloud_send_authorized'] is False
    wrapper = next(row for row in t.rows[s.Audit] if row.action == s.QUOTA_ACTION)
    assert wrapper.data['authorization_body']['expires_at'] == (t.time + timedelta(seconds=7200)).isoformat()
    assert wrapper.data['policy_ref'] == result['quota_policy_ref']
    candidates = t.receipts(t.quota_body['request_id'])
    assert candidates[0]['operation'] == 'quota' and candidates[0]['read_result']() == result
    assert t.fetches == 1


def test_quota_nonce_retry_and_other_current_reader_do_not_need_current_head_price_or_old_admin_role(scene, monkeypatch):
    t = scene; t.price(); result = t.quota(); original_rows = list(t.rows[s.Audit])
    t.time += timedelta(days=1); t.live_config = False
    other = Principal(uid(), t.p.workspace_id, 'viewer', 'actual-current-reader-IO')
    def no_current(*args, **kwargs): raise AssertionError('historical quota cannot authorize or read current price/head')
    monkeypatch.setattr(s.quota, 'authorize', no_current)
    monkeypatch.setattr(s.prices, 'current', no_current)
    monkeypatch.setattr(s.quota, '_configuration_base', no_current)
    cold = t.receipts(t.quota_body['request_id'], p=other)[0]['read_result']()
    assert cold == result and t.quota() == result and t.rows[s.Audit] == original_rows
    t.issuer_active = False
    assert t.receipts(t.quota_body['request_id'], p=other)[0]['read_result']() == result


@pytest.mark.parametrize('kind', ['price', 'quota'])
def test_new_commands_require_actual_admin_before_fetch_or_any_write(scene, kind):
    t = scene; user = Principal(uid(), t.p.workspace_id, 'operator', 'current-reader')
    before = list(t.rows[s.Audit])
    with pytest.raises(HTTPException) as error:
        if kind == 'price': t.price(p=user)
        else: t.quota(p=user)
    assert error.value.status_code == 403 and t.fetches == 0 and t.rows[s.Audit] == before


@pytest.mark.parametrize('kind,field,value', [('price', 'validity_seconds', True), ('price', 'model_pin', {'id': 'other'}),
    ('price', 'url', 'https://caller-controlled.invalid'), ('quota', 'python', 'caller.module'),
    ('quota', 'validity_seconds', 299), ('quota', 'max_cost_minor_units', True), ('quota', 'max_cost_minor_units', 1.5),
    ('quota', 'currency', 'EUR')])
def test_closed_invalid_configuration_fails_before_fetch_or_writes(scene, kind, field, value):
    t = scene; body = copy.deepcopy(t.body if kind == 'price' else t.quota_body); body[field] = value
    with pytest.raises(HTTPException):
        if kind == 'price': t.price(body)
        else: t.quota(body)
    assert t.fetches == 0 and t.rows[s.Audit] == []


def test_price_body_cannot_select_a_different_real_model(scene):
    t = scene; body = copy.deepcopy(t.body); body['model_pin']['id'] = uid()
    with pytest.raises(HTTPException): t.price(body)
    assert t.fetches == 0


def test_model_maximum_and_goal_deadline_bound_the_actual_quota_authorizer_body(scene):
    t = scene; t.model.data['execution']['max_tokens'] = 1024
    t.model.content_hash = t.model.validated_hash = digest(t.model.data)
    # Actual frozen source-publication resolution is IO in this single quota
    # case. The configuration context supplies the current fixed model pin.
    t.context['goal']['spec']['deadline'] = (t.time + timedelta(minutes=10)).isoformat()
    t.body['model_pin'] = s.store._pin(t.model)
    s.prices.authorize(t.db, t.p, t.body)
    result = t.quota()
    assert result['limits']['max_tokens'] == 1024
    assert result['expires_at'] == t.context['goal']['spec']['deadline']


def test_currency_mismatch_does_not_invent_fx_or_authorize_policy(scene):
    t = scene; t.price(); body = {**t.quota_body, 'currency': 'USD'}
    before = list(t.rows[s.Audit])
    with pytest.raises(HTTPException): t.quota(body)
    assert t.rows[s.Audit] == before


@pytest.mark.parametrize('changed', ['body', 'issuer', 'duplicate'])
def test_saved_wrapper_identity_cannot_be_rebound_or_duplicated(scene, changed):
    t = scene; t.price(); before = t.fetches
    wrapper = next(row for row in t.rows[s.Audit] if row.action == s.PRICE_ACTION)
    if changed == 'body':
        with pytest.raises(HTTPException): t.price({**t.body, 'reason': 'different body'})
    elif changed == 'issuer':
        other = Principal(uid(), t.p.workspace_id, 'admin', 'actual-other-admin-IO')
        with pytest.raises(HTTPException): t.price(p=other)
    else:
        t.rows[s.Audit].append(s.Audit(id=uid(), workspace_id=t.p.workspace_id, actor_id=t.p.user_id,
            action=wrapper.action, object_id=t.task.id, created_at=wrapper.created_at, data=copy.deepcopy(wrapper.data)))
        with pytest.raises(HTTPException): t.receipts(t.body['request_id'])
    assert t.fetches == before


@pytest.mark.parametrize('kind', ['price', 'quota'])
def test_underlying_receipt_without_task_wrapper_is_not_rebuilt(scene, kind):
    t = scene; t.price()
    if kind == 'quota': t.quota()
    action = s.PRICE_ACTION if kind == 'price' else s.QUOTA_ACTION
    t.rows[s.Audit] = [row for row in t.rows[s.Audit] if row.action != action]
    before = list(t.rows[s.Audit]); fetches = t.fetches
    with pytest.raises(HTTPException):
        if kind == 'price': t.price()
        else: t.quota()
    assert t.fetches == fetches and t.rows[s.Audit] == before


def test_existing_unexpired_policy_is_not_extended_or_reallocated(scene):
    t = scene; t.price(); t.quota(); body = {**t.quota_body, 'request_id': uid()}
    before = list(t.rows[s.Audit])
    with pytest.raises(HTTPException): t.quota(body)
    assert t.rows[s.Audit] == before


def test_explicit_new_policy_after_expiry_preserves_old_spent_allocation_and_all_original_rows(scene):
    t=scene;t.price();original=t.quota()
    old_rows={row.id:copy.deepcopy(row.data) for row in t.rows[s.Audit]}
    old_allocation=[(NS(status='reviewed'),{'calls':[NS(status='succeeded')]})]
    t.occupied=old_allocation
    t.time+=timedelta(hours=3);t.price({**t.body,'request_id':uid()})
    result=t.quota({**t.quota_body,'request_id':uid(),'reason':'原授权自然到期后明确配置下一次费用，保留原调用与费用'})
    assert result['quota_policy_ref']!=original['quota_policy_ref']
    assert result['limits']['max_actual_calls']==1 and result['cloud_send_authorized'] is False
    assert t.occupied is old_allocation and old_allocation[0][1]['calls'][0].status=='succeeded'
    assert all(next(row.data for row in t.rows[s.Audit] if row.id==identity)==value for identity,value in old_rows.items())


def test_missing_official_bytes_returns_saved_command_candidate_but_read_result_needs_check(scene):
    t = scene; result = t.price(); original = t.receipts(t.body['request_id'])[0]
    t.files.clear()
    candidate = t.receipts(t.body['request_id'])[0]
    assert candidate['body'] == original['body'] and candidate['audit_id'] == original['audit_id']
    with pytest.raises(HTTPException): candidate['read_result']()
    assert result['basis_ref']['id'] in {row.id for row in t.rows[s.Audit]}


@pytest.mark.parametrize('fault', ['body_hash', 'object_scope', 'deleted_nonce', 'price_ref', 'policy_ref'])
def test_damaged_or_reverse_scoped_wrapper_fails_closed(scene, fault):
    t = scene; t.price(); t.quota()
    action = s.QUOTA_ACTION if fault == 'policy_ref' else s.PRICE_ACTION
    wrapper = next(row for row in t.rows[s.Audit] if row.action == action)
    if fault == 'body_hash': wrapper.data = {**wrapper.data, 'body_hash': '9' * 64}
    elif fault == 'object_scope': wrapper.object_id = uid()
    elif fault == 'deleted_nonce': wrapper.data = {key: value for key, value in wrapper.data.items() if key != 'request_id'}
    elif fault == 'price_ref': wrapper.data['price_basis_ref']['record_hash'] = '9' * 64
    else: wrapper.data['policy_ref']['record_hash'] = '9' * 64
    request_id = t.quota_body['request_id'] if action == s.QUOTA_ACTION else t.body['request_id']
    with pytest.raises(HTTPException): t.receipts(request_id)


def test_empty_lookup_is_readonly_and_does_not_invoke_any_authorizer(scene, monkeypatch):
    t = scene
    def forbidden(*args, **kwargs): raise AssertionError('GET must never invoke a configuration writer')
    monkeypatch.setattr(s.prices, 'authorize', forbidden)
    monkeypatch.setattr(s.quota, 'authorize', forbidden)
    assert t.receipts(uid()) == [] and t.fetches == 0 and t.rows[s.Audit] == []
