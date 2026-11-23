"""Real PG foundation for an advice journey, not a complete advice send.

Only loopback synthetic device data and fixed in-memory HTML are used. The
task has not yet been given the matured goal or confirmed advice binding.
"""
import pytest
import copy
from contextlib import ExitStack
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from types import SimpleNamespace
from uuid import uuid4

from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import sensor_native_packet as packets
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, CloudCall, Evidence, Membership, Task, User
from generic_agents.security import Principal
from tests.test_sensor_native_analysis_pg import setup_analysis
from tests.test_sensor_automation_pg import device
from tests.test_device_gateway_v268 import scene
from tests.test_device_gateway_tools_v269 import managed
from tests.test_authored_goal_data_workflow_pg_v288 import (
    test_real_six_port_adoption_recovers_original_request_and_publishes_fixed_preparation_policy as publish_real_graph,
)
from tests.test_instance_workspace import object_row
from tests.test_authored_image_acceptance_pg_v243 import actor_client, approval as approval_view, command as approval_command
from tests.test_runtime_documents_pg_v205 import uploaded, run, version
from generic_agents import runtime_document_receipts
from generic_agents.runtime_document_models import RuntimeDocumentVersion
import tests.test_authored_service_workflow_pg_v270 as authored_service_fixture


@pytest.fixture
def advice_foundation(client, account, device, monkeypatch):
    task, base, packet, model_id = setup_analysis(client, account, device, monkeypatch)
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        principal = Principal(user.id, account[2], 'admin', 'test')
        cap = db.get(Capability, model_id)
        pin = store._pin(cap)
        assert db.get(Task, task['id']) is not None
    return {'task': task['id'], 'base': base, 'packet': packet['id'], 'model': model_id,
            'principal': principal, 'pin': pin}


def test_real_pg_task_model_price_and_membership_survive_new_session(advice_foundation):
    s = advice_foundation
    with Session(engine()) as db:
        p = s['principal']
        store._reader(db, p)
        store._member(db, p, p.user_id, admin=True)
        task, _release, _business = packets.task_access(db, p, s['task'], live=True)
        assert task.owner_id == p.user_id
        assert quota._published(db, p, s['model'], 'foundation_model').content_hash == s['pin']['content_hash']
        first = prices.current(db, p, s['pin'])
        assert first['price']['provider'] == 'deepseek'
        assert not list(db.scalars(select(CloudCall)))
    engine().dispose()
    with Session(engine()) as db:
        assert prices.current(db, s['principal'], s['pin'])['basis_ref'] == first['basis_ref']
        assert db.get(Task, s['task']) is not None
        assert not list(db.scalars(select(CloudCall)))


def test_real_pg_revoke_or_model_tamper_blocks_foundation(advice_foundation):
    s = advice_foundation; p = s['principal']
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
                                                       Membership.user_id == p.user_id))
        membership.role = 'viewer'; db.commit()
    with Session(engine()) as db:
        with pytest.raises((HTTPException, ValueError)):
            store._member(db, p, p.user_id, admin=True)
        assert not list(db.scalars(select(CloudCall)))
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
                                                       Membership.user_id == p.user_id))
        membership.role = 'admin'
        cap = db.get(Capability, s['model'])
        cap.data = {**cap.data, 'owner': 'tampered-after-publication'}; db.commit()
    with Session(engine()) as db:
        with pytest.raises((HTTPException, ValueError)):
            quota._published(db, p, s['model'], 'foundation_model')
        assert not list(db.scalars(select(CloudCall)))


def make_goal_graph_draft(managed):
    """Bootstrap graph publication and task only; this is not a mature goal."""
    publish_real_graph(managed)
    client = managed.client
    candidates = []
    for instance in client.get('/api/instances').json():
        for release in client.get('/api/instances/' + instance['id'] + '/releases').json():
            runtime = release.get('snapshot', {}).get('semantic_runtime_plan', {})
            if runtime.get('graph_adoptions'):
                candidates.append(release)
    assert len(candidates) == 1
    release = candidates[0]
    instance = next(x for x in client.get('/api/instances').json() if x['id'] == release['instance_id'])
    activated = client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_human_workflow_scope': True,
        'confirm_workflow_scope': True,
    })
    assert activated.status_code == 200, activated.text
    unit = str(uuid4())
    created = client.post('/api/workspace/approval-settings-v175/units', json={
        'request_id': str(uuid4()), 'unit_id': unit, 'expected_version': 0,
        'name': '合成业务建议测试单位', 'enabled': True,
    })
    assert created.status_code == 200, created.text
    stage = {'name': '合成审批', 'role_key': 'reviewer', 'scope_id': unit}
    policy = client.post('/api/workspace/approval-settings-v175/policies', json={
        'request_id': str(uuid4()), 'instance_id': release['instance_id'],
        'business_id': release['available_business_ids'][0], 'expected_version': 0,
        'name': '合成业务建议审批策略', 'scope_id': unit, 'stages': [stage],
        'executor': stage, 'acceptance': stage, 'enabled': True,
    })
    assert policy.status_code == 200, policy.text
    target = object_row(client, release)
    response = client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
        'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [target['id']], 'title': '合成业务建议任务', 'request_id': str(uuid4()),
        'graph_inputs': {'analysis_question': '核对合成观测资料', 'policy_pin': None},
    })
    assert response.status_code == 201, response.text
    task_id = response.json()['id']
    return release, target, task_id, unit


def test_real_authored_goal_graph_release_and_task_are_saved_in_pg(managed):
    release, _target, task_id, _unit = make_goal_graph_draft(managed)
    with Session(engine()) as db:
        stored = db.get(Task, task_id)
        assert stored is not None and stored.workspace_id == managed.workspace_id
        assert stored.data['instance_release_id'] == release['id']
        assert not list(db.scalars(select(CloudCall)))
    engine().dispose()
    with Session(engine()) as db:
        assert db.get(Task, task_id).data['instance_release_id'] == release['id']
        assert not list(db.scalars(select(CloudCall)))


@pytest.mark.parametrize('model_provider', ['qwen', 'deepseek'])
def test_graph_task_uses_saved_document_source_and_real_approval_roles(managed, monkeypatch, model_provider):
    """Source authorization, approval and initial goal only; no model call."""
    if model_provider == 'deepseek':
        synthetic_model = copy.deepcopy(authored_service_fixture.model_manifest())
        synthetic_model['name'] = 'synthetic_deepseek'
        synthetic_model['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
            'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
        monkeypatch.setattr(authored_service_fixture, 'model_manifest', lambda: copy.deepcopy(synthetic_model))
    release, target, task_id, unit = make_goal_graph_draft(managed)
    client = managed.client
    holder = SimpleNamespace(client=client, workspace=managed.workspace_id)
    raw = '合成资料用于测试审批与目标，不表示现场事实。'.encode()
    upload, _ = uploaded(holder, release, raw, 'synthetic.txt')
    assert upload.status_code == 201, upload.text
    version_id = upload.json()['latest_version_id']
    assert run(holder, version_id) == 'succeeded'
    runtime_document_receipts.consume_once(managed.workspace_id)
    native = version(holder, version_id)
    assert native['delivery']['verified'] is True
    graph = release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['binding']['graph']
    reasoning = next(node for node in graph['nodes'] if node['id'] == 'reasoning')
    with Session(engine()) as db:
        reasoning_cap = db.get(Capability, reasoning['execution']['capability_id'])
        model_id = reasoning_cap.data['execution']['model_profile_id']
        assert db.get(Capability, model_id).data['execution']['provider'] == model_provider
        evidence_id = db.get(RuntimeDocumentVersion, version_id).evidence_id
        evidence_version = db.get(Evidence, evidence_id).version
    authorized = client.post('/api/evidence/' + evidence_id + '/authorize', json={
        'model_profile_id': model_id, 'expected_version': evidence_version, 'reason': '仅为合成图任务授权资料',
    })
    assert authorized.status_code == 200, authorized.text
    selector = {'kind': 'native', 'version_id': version_id, 'attempt_id': native['latest_attempt_id'],
                'parse_hash': native['parse_hash'], 'block_id': 'b1', 'char_span': {'start': 0, 'end': 4}}
    root = '/api/runtime-task-documents'
    ctx = client.get(root + '/tasks/' + task_id + '/context')
    assert ctx.status_code == 200, ctx.text
    ctx = ctx.json()
    body = {'request_id': str(uuid4()), 'expected_task_version': ctx['task_version'],
            'expected_approval_version': ctx['approval_version'], 'prior_set_id': None, 'prior_set_hash': None,
            'input_key': ctx['input_ports'][0]['key'], 'selectors': [selector]}
    saved = client.post(root + '/tasks/' + task_id + '/source-sets', json=body)
    assert saved.status_code == 201, saved.text
    source = saved.json()
    base = '/api/workspace/tasks/' + task_id + '/approval-v175'
    draft = approval_view(client, task_id)
    value = copy.deepcopy(draft['value']); value['graph_inputs'][body['input_key']] = source['ref']
    field = next(iter(target['attributes']))
    value['goal_spec'] = {'purpose': '核对合成业务资料并保留原流程结果', 'entity_ids': [target['id']],
        'required_fields': [field], 'subgoals': [{'id': 'check', 'description': '核对合成资料',
        'criterion_ids': ['enough', 'human']}], 'criteria': [
            {'id': 'enough', 'kind': 'evidence_sufficient', 'description': '合成资料充分'},
            {'id': 'human', 'kind': 'human_confirmation', 'description': '人工确认'}],
        'evidence': {'source': 'observations', 'minimum_verification': 'unverified_extraction',
                     'time': {'mode': 'latest', 'max_age_seconds': 600, 'timezone': 'Asia/Shanghai'}},
        'observation_mode': 'allow_new'}
    revised = client.post(base + '/revise', json={'request_id': str(uuid4()), 'expected_version': draft['version'],
        'reason': '采用保存的合成资料', 'value': value})
    assert revised.status_code == 200, revised.text
    with ExitStack() as stack:
        reviewer_id, reviewer = actor_client(stack, managed.workspace_id)
        for actor_id in (managed.actor_id, reviewer_id):
            role = client.post('/api/workspace/approval-settings-v175/roles', json={
                'request_id': str(uuid4()), 'grant_id': str(uuid4()), 'expected_version': 0,
                'unit_id': unit, 'user_id': actor_id, 'role_key': 'reviewer', 'enabled': True,
            })
            assert role.status_code == 200, role.text
        approval_command(client, task_id, 'submit')
        pending = approval_view(reviewer, task_id)
        approval_command(reviewer, task_id, 'decide', decision='approve', submission_hash=pending['submission_hash'])
        assert approval_view(client, task_id)['status'] == 'approved'
        approval_command(client, task_id, 'start')
        engine().dispose()
        assert approval_view(client, task_id)['status'] == 'executing'
        assert approval_view(client, task_id)['value']['graph_inputs'][body['input_key']] == source['ref']
    with Session(engine()) as db:
        task = db.get(Task, task_id)
        assert task.data.get('native_goal_context') and task.data.get('goal_hash')
        assert list(db.scalars(select(Audit).where(Audit.workspace_id == managed.workspace_id,
            Audit.object_id == evidence_id, Audit.action == 'evidence.cloud_authorize')))
        assert not list(db.scalars(select(CloudCall)))
