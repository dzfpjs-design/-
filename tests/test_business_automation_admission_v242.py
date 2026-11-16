"""Pure entry and frozen-policy checks, not live publication or DB authority.

Plan verification is an explicit boundary spy in policy tests. Root separately
proves real published configuration, leased occurrence and ordinary Task gates.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import business_automation_admission as subject
from generic_agents import business_flow_execution_runtime as flow_runtime
from generic_agents import authored_workflow_runtime_v208 as authored_runtime
from generic_agents import instance_semantic_runtime_dispatch as dispatch
from generic_agents import setup_automation
from generic_agents.contracts import digest
from tests.test_instance_workspace import configuration


@pytest.fixture
def policy_boundary(monkeypatch):
    config = configuration()
    name = '资料核验'
    policy = setup_automation.safe_policy()
    policy['triggers'] = ['人工发起', '新观测到达', '定时运行']
    policy['rules']['data']['mode'] = '自动运行'
    config['automation']['policies'][name] = policy
    policy['flow_review'] = setup_automation.metadata(config, name, policy, 'confirmed',
        actor_id='synthetic-pure-author', confirmed_at='2026-10-28T00:00:00Z')
    business = {'id': 'opaque-business-id-含中文', 'name': name, 'available': True}
    binding = {'schema': flow_runtime.NEW_BINDING, 'business_name': name,
        'flow': {'source_flow': {'trigger': {'kind': 'manual'}}},
        'graph': {'input_schema': {'type': 'object', 'properties': {'task_image_ids': {
            'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {'type': 'string'}}}},
            'nodes': [{'id': 'data', 'kind': 'ability', 'execution': {
                'capability_id': 'synthetic-cap', 'capability_hash': '1'*64}},
                {'id': 'human', 'kind': 'human'}]},
        'node_actions': {'data': 'data'}, 'role_bindings': {'核验负责人': {'user_id': 'synthetic'}},
        'binding_hash': '2'*64}
    plan = {'schema': flow_runtime.SCHEMA, 'graph_adoptions': [{'binding': binding}]}
    snapshot = {'businesses': [business], 'semantic_runtime_plan': plan,
        'automation': copy.deepcopy(config['automation'])}
    seen = []
    def verify(value):
        seen.append(value)
        return value['semantic_runtime_plan']
    monkeypatch.setattr(dispatch, 'modern', lambda _: NS(verify_plan=verify))
    return NS(config=config, name=name, policy=snapshot['automation']['policies'][name],
        business=business, binding=binding, snapshot=snapshot, seen=seen)


def test_confirmed_dynamic_business_gets_frozen_sealed_entry_without_changing_source(policy_boundary):
    s = policy_boundary
    before = copy.deepcopy(s.snapshot)
    admission = subject.compile_admission(s.snapshot, s.config)
    assert s.snapshot == before and s.seen == [s.snapshot]
    assert admission['schema'] == subject.SCHEMA
    assert admission['admission_hash'] == digest({k:v for k,v in admission.items() if k != 'admission_hash'})
    s.snapshot[subject.KEY] = admission
    found = subject.policy(s.snapshot, s.business)
    assert found['business_id'] == s.business['id']
    assert found['allowed_triggers'] == ['new_data', 'daily_time'] and found['time'] == '08:00'
    assert found['provider_ids'] == [subject.PROVIDER]
    assert found['roles_hash'] == digest(s.binding['role_bindings'])
    assert found['graph_hash'] == digest(s.binding['graph'])
    assert found['policy_hash'] == setup_automation.policy_hash(s.policy)
    assert [node['kind'] for node in s.binding['graph']['nodes']] == ['ability', 'human']


def test_old_release_is_not_upgraded_by_runtime_reconstruction(policy_boundary):
    s = policy_boundary
    assert subject.policy(s.snapshot, s.business) is None
    assert subject.KEY not in s.snapshot


@pytest.mark.parametrize('fault', ['unconfirmed', 'policy_changed', 'no_trigger', 'bad_trigger',
    'bad_time', 'event_entry', 'unavailable', 'human_only_input', 'zero_images',
    'audio_input', 'missing_action', 'confirm_action', 'unknown_family'])
def test_missing_or_conflicting_policy_cannot_grant_an_entry(policy_boundary, fault):
    s = policy_boundary
    if fault == 'unconfirmed': s.policy['flow_review']['status'] = 'needs_review'
    elif fault == 'policy_changed': s.policy['budget'] += 1
    elif fault == 'no_trigger': s.policy['triggers'] = ['人工发起']
    elif fault == 'bad_trigger': s.policy['triggers'].append('直接调用工具')
    elif fault == 'bad_time': s.policy['time'] = '24:00'
    elif fault == 'event_entry': s.binding['flow']['source_flow']['trigger']['kind'] = 'event'
    elif fault == 'unavailable': s.business['available'] = False
    elif fault == 'human_only_input': s.binding['graph']['input_schema']['properties'] = {}
    elif fault == 'zero_images': s.binding['graph']['input_schema']['properties']['task_image_ids']['maxItems'] = 0
    elif fault == 'audio_input': s.binding['graph']['input_schema']['properties']['task_audio_ref'] = {'type':'object'}
    elif fault == 'missing_action': s.binding['node_actions'] = {}
    elif fault == 'confirm_action': s.policy['rules']['data']['mode'] = '执行前确认'
    elif fault == 'unknown_family': s.snapshot['semantic_runtime_plan']['schema'] = 'unknown'
    # Keep the policy self-hash valid where needed so its downstream predicate
    # is exercised rather than all negative cases stopping at one early hash.
    if fault not in {'policy_changed', 'unconfirmed'}:
        s.policy['flow_review']['policy_hash'] = setup_automation.policy_hash(s.policy)
    before = copy.deepcopy(s.snapshot)
    assert subject._build(s.snapshot) is None and s.snapshot == before


def test_compile_requires_actual_current_flow_review_not_only_snapshot_self_hash(policy_boundary):
    s = policy_boundary
    s.config['flows'][s.name]['steps'][0]['purpose'] = '改变后的人工要求'
    with pytest.raises(HTTPException) as error:
        subject.compile_admission(s.snapshot, s.config)
    assert error.value.status_code == 409


@pytest.mark.parametrize('kind', ['required', 'allOf', 'oneOf', 'ref'])
def test_required_human_input_in_root_constraints_blocks_provider_instead_of_failing_later(policy_boundary, kind):
    s = policy_boundary
    schema = s.binding['graph']['input_schema']
    schema['properties']['inspection_question'] = {'type':'string','minLength':1}
    if kind == 'required': schema['required'] = ['task_image_ids','inspection_question']
    elif kind == 'allOf': schema['allOf'] = [{'required':['inspection_question']}]
    elif kind == 'oneOf': schema['oneOf'] = [{'required':['inspection_question']}]
    else:
        schema['$defs'] = {'question':{'required':['inspection_question']}}
        schema['allOf'] = [{'$ref':'#/$defs/question'}]
    before = copy.deepcopy(s.snapshot)
    assert subject._build(s.snapshot) is None and s.snapshot == before


def test_optional_human_input_and_normal_system_requirements_do_not_become_new_provider_requirements(policy_boundary):
    s = policy_boundary
    schema = s.binding['graph']['input_schema']
    schema['properties']['inspection_question'] = {'type':'string'}
    schema['properties']['entity_ids'] = {'type':'array','minItems':1}
    schema['required'] = ['task_image_ids','entity_ids']
    assert subject._build(s.snapshot) is not None


@pytest.mark.parametrize('fault', ['declaration', 'policy', 'business'])
def test_frozen_admission_cannot_be_retained_after_policy_or_business_mutation(policy_boundary, fault):
    s = policy_boundary
    s.snapshot[subject.KEY] = subject.compile_admission(s.snapshot, s.config)
    business = copy.deepcopy(s.business)
    if fault == 'declaration': s.snapshot[subject.KEY]['businesses'][0]['roles_hash'] = 'a'*64
    elif fault == 'policy': s.policy['time'] = '09:00'
    elif fault == 'business': business['name'] = '另一个业务'
    with pytest.raises(HTTPException) as error: subject.policy(s.snapshot, business)
    assert error.value.status_code == 409


def synthetic_token():
    transaction = object()
    db = NS(get_transaction=lambda: transaction)
    fields = {key: 'synthetic-'+key for key in subject._TOKEN_FIELDS}
    fields.update(unit={'source':'explicit-unit-fixture'}, source_target={'source':'explicit-target-fixture'},
        binding_hash='b'*64)
    return db, subject.AutomationHandoff(subject._ISSUER, db, **fields)


@pytest.mark.parametrize('runtime', [flow_runtime, authored_runtime])
@pytest.mark.parametrize('token', [True, False, {}, {'approved': True}, 'authorized'])
def test_json_or_boolean_is_never_a_task_entry_handoff(runtime, token):
    with pytest.raises(HTTPException) as error:
        runtime.require_task_start({'schema': 'irrelevant'}, verified_anomaly_handoff=False,
            automation_handoff=token)
    assert error.value.status_code == 409


@pytest.mark.parametrize('fault', ['new_transaction', 'mutated_field', 'wrong_binding', 'event_binding', 'anomaly_bool'])
def test_private_token_still_cannot_cross_transaction_binding_or_anomaly_authority(fault):
    db, token = synthetic_token()
    binding = {'schema': flow_runtime.NEW_BINDING, 'binding_hash': token.binding_hash,
        'flow': {'source_flow': {'trigger': {'kind':'manual'}}}}
    anomaly = False
    if fault == 'new_transaction': db.get_transaction = lambda: object()
    elif fault == 'mutated_field': token.unit['source'] = 'changed'
    elif fault == 'wrong_binding': binding['binding_hash'] = 'c'*64
    elif fault == 'event_binding': binding['flow']['source_flow']['trigger']['kind'] = 'event'
    elif fault == 'anomaly_bool': anomaly = True
    with pytest.raises(HTTPException) as error:
        flow_runtime.require_task_start(binding, verified_anomaly_handoff=anomaly, automation_handoff=token)
    assert error.value.status_code == 409


def test_current_private_token_dispatches_only_a_matching_manual_entry():
    _, token = synthetic_token()
    binding = {'schema': flow_runtime.NEW_BINDING, 'binding_hash': token.binding_hash,
        'flow': {'source_flow': {'trigger': {'kind':'manual'}}}}
    assert subject.entry_matches(binding, token) is True
    flow_runtime.require_task_start(binding, verified_anomaly_handoff=False, automation_handoff=token)
    # The ordinary event gate remains required when no automation token exists.
    from tests.test_business_flow_execution_task_trigger import envelope
    with pytest.raises(HTTPException): flow_runtime.require_task_start(envelope(), verified_anomaly_handoff=False)
