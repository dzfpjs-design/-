"""Real PG foundation for an advice journey, not a complete advice send.

Only loopback synthetic device data and fixed in-memory HTML are used. The
task has not yet been given the matured goal or confirmed advice binding.
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from uuid import uuid4

from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import sensor_native_packet as packets
from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, Membership, Task, User
from generic_agents.security import Principal
from tests.test_sensor_native_analysis_pg import setup_analysis
from tests.test_sensor_automation_pg import device
from tests.test_device_gateway_v268 import scene
from tests.test_device_gateway_tools_v269 import managed
from tests.test_authored_goal_data_workflow_pg_v288 import (
    test_real_six_port_adoption_recovers_original_request_and_publishes_fixed_preparation_policy as publish_real_graph,
)
from tests.test_instance_workspace import object_row


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


def test_real_authored_goal_graph_release_and_task_are_saved_in_pg(managed):
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
    with Session(engine()) as db:
        stored = db.get(Task, task_id)
        assert stored is not None and stored.workspace_id == managed.workspace_id
        assert stored.data['instance_release_id'] == release['id']
        assert not list(db.scalars(select(CloudCall)))
    engine().dispose()
    with Session(engine()) as db:
        assert db.get(Task, task_id).data['instance_release_id'] == release['id']
        assert not list(db.scalars(select(CloudCall)))
