"""Actual issued graph inputs, explicit entity scope and original Task recovery.

Uses the existing synthetic document-provider fixture and real PG/API/worker
gates. No release, source, Task, graph, authorization or transport is patched.
Metadata/lookup alone must create no Task, Job, Invocation or admission.
"""
import copy
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventIntent
from generic_agents.models import Capability, CloudCall, Invocation, Job, Membership, Task, User
from generic_agents.security import password_hash
from generic_agents.task_creation_recovery_v120 import TaskCreationRetirement
from generic_agents.worker import process_once
from generic_agents.workflow_graph import SYSTEM_INPUTS
from generic_agents.workspace_api import NewTask, creation_body_hash
from tests.test_graph_wait_journey_v118 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    start, admit_observation, poll, reply, human,
)


def _paths(value):
    base = '/api/workspace/instances/' + value['release']['instance_id']
    return base + '/graph-task-inputs', base + '/tasks', base + '/task-requests/lookup'


def _body(value, *, task=None, entity_ids=None, correlated=None):
    release = value['release']
    return {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': entity_ids if entity_ids is not None else [value['object']['id']],
        'title': task['title'] if task is not None else '明确选择本次消息关联对象',
        'request_id': task['creation_request_id'] if task is not None else str(uuid4()),
        'graph_inputs': {'watched_entity': correlated if correlated is not None else value['object']['id']}}


def _counts():
    with Session(engine()) as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Task, Job, Invocation, CloudCall, GraphEventIntent, GraphEventAdmission,
                TaskCreationRetirement)}


def _job_ids():
    with Session(engine()) as db:
        return set(db.scalars(select(Job.id)))


def _assert_login_only(before, prior_jobs):
    after = _counts()
    assert after == {**before, Job.__tablename__: before[Job.__tablename__] + 1}
    with Session(engine()) as db:
        added = db.execute(select(Job.kind, Job.status).where(Job.id.not_in(prior_jobs))).all()
        assert [tuple(row) for row in added] == [('login_limit', 'internal')]
    return after


def _other_object(value):
    release = value['release']; kind = release['snapshot']['object_types'][0]
    attributes = {field['key']: {'文本': kind['name'], '整数': 2, '数值': 2.5,
        '是否': True, '日期': '2026-01-01'}[field['kind']] for field in kind['fields'] if field['required']}
    return reply(value['client'].post('/api/instances/' + release['instance_id'] + '/objects', json={
        'release_id': release['id'], 'object_type': kind['key'], 'attributes': attributes,
        'reference': '另一个明确关联对象_' + uuid4().hex, 'request_id': 'v120_object_' + uuid4().hex}), status=201)


def test_actual_input_metadata_is_exact_read_only_and_excludes_server_fields_from_user_form(waiting_release):
    value = waiting_release; client = value['client']; metadata_path, _, _ = _paths(value)
    release = value['release']; graph = value['adopt_body']['graph']
    params = {'release_id': release['id'], 'business_id': release['available_business_ids'][0]}
    before = _counts()
    metadata = reply(client.get(metadata_path, params=params))
    assert metadata['schema'] == 'workflow-graph-task-inputs-v1'
    assert metadata['instance_id'] == release['instance_id'] and metadata['release_id'] == release['id']
    assert metadata['configuration_hash'] == release['configuration_hash']
    assert metadata['mode'] == 'graph' and metadata['execution_granted'] is False
    assert metadata['input_schema'] == graph['input_schema'] and metadata['graph_hash'] == digest(graph)
    assert metadata['hash'] == digest({key: val for key, val in metadata.items() if key != 'hash'})
    properties = graph['input_schema']['properties']
    assert set(metadata['system_fields']) == set(properties) & SYSTEM_INPUTS
    assert set(metadata['user_input_schema']['properties']) == set(properties) - SYSTEM_INPUTS
    assert not set(metadata['user_input_schema']['required']) & SYSTEM_INPUTS
    association = next(row for row in metadata['entity_fields'] if row['field'] == 'watched_entity')
    assert set(association) == {'field', 'label', 'required', 'waits'}
    assert association['required'] is True and association['label'] and association['waits']
    assert 'value' not in association and 'default' not in association
    assert client.get(metadata_path, params={**params, 'release_id': str(uuid4())}).status_code == 409
    assert client.get(metadata_path, params={**params, 'business_id': str(uuid4())}).status_code == 409
    assert _counts() == before


def test_actual_association_rejects_unselected_object_before_dispatch_and_selected_second_object_completes_journey(waiting_release):
    value = waiting_release; client = value['client']; _, create_path, lookup_path = _paths(value)
    other = _other_object(value); selected = value['object']['id']; other_id = other['id']
    before = _counts()
    invalid = _body(value, correlated=other_id)
    response = client.post(create_path, json=invalid)
    assert response.status_code in {409, 422}, response.text
    assert _counts() == before, 'a rejected association must not dispatch a capability before its later wait'
    absent = reply(client.post(lookup_path, json=invalid))
    assert absent['found'] is False and absent['request'] is None
    override = _body(value)
    override['graph_inputs']['entity_ids'] = [other_id]
    assert client.post(create_path, json=override).status_code == 422
    assert _counts() == before

    # Both objects are genuinely selected, but the correlation deliberately
    # names the second object. A default to entity_ids[0] would fail this flow.
    body = _body(value, entity_ids=[selected, other_id], correlated=other_id)
    created = reply(client.post(create_path, json=body), status=201)
    task = reply(client.get('/api/workspace/tasks/' + created['id']))
    assert task['entity_ids'] == [selected, other_id]
    assert task['graph_runtime']['inputs']['watched_entity'] == other_id
    assert set(task['graph_wait_authority']['entity_ids']) == {selected, other_id}
    assert {row['id'] for row in task['object_snapshots']} == {selected, other_id}
    assert task['creation_hash'] == creation_body_hash(NewTask.model_validate(body))
    assert process_once(next(iter(task['graph_jobs'].values()))['job_id'], deliver=False)
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    task, _ = human(client, task, 'a')
    assert next(iter(task['graph_wait_authority']['windows'].values()))['status'] == 'open'
    initial_cut = copy.deepcopy(task['graph_wait_authority']['initial_cut'])
    receipt_id, sequence = admit_observation({**value, 'object': other})
    task = poll(client, task['id'])
    window = next(iter(task['graph_wait_authority']['windows'].values()))
    assert window['status'] == 'consumed' and window['event_id'] == receipt_id
    assert window['open_sequence'] < sequence <= window['closed_sequence']
    assert task['graph_runtime']['events'][0]['correlation'] == other_id
    task, complete_body = human(client, task, 'c')
    assert task['completed'] is True
    assert task['graph_wait_authority']['initial_cut'] == initial_cut
    saved = reply(client.post(lookup_path, json=body))
    assert saved['found'] and saved['request']['task_id'] == task['id']
    assert saved['request']['creation_hash'] == task['creation_hash']
    replay = reply(client.post('/api/workspace/tasks/' + task['id'] + '/graph/complete', json=complete_body))
    assert replay['graph_runtime'] == task['graph_runtime']
    with Session(engine()) as db:
        persisted = db.get(Task, task['id'])
        assert persisted.data['graph_runtime'] == task['graph_runtime']
        assert persisted.data['graph_wait_authority'] == task['graph_wait_authority']
        assert db.scalar(select(func.count()).select_from(CloudCall)) == value['cloud_calls_before']


def test_actual_creation_lookup_checks_original_actor_and_body_and_survives_source_revocation(waiting_release):
    value = waiting_release; client = value['client']; metadata_path, create_path, lookup_path = _paths(value)
    task = start(value); body = _body(value, task=task)
    with Session(engine()) as db:
        owner = db.get(Task, task['id']); owner_id = owner.owner_id; workspace_id = owner.workspace_id
    assert creation_body_hash(NewTask.model_validate(body)) == task['creation_hash']
    before = _counts()
    missing = reply(client.post(lookup_path, json={**body, 'request_id': str(uuid4())}))
    assert missing == {'schema': 'workflow-task-request-recovery-v1', 'found': False,
        'request': None, 'retirement': None}
    found = reply(client.post(lookup_path, json=body))
    assert found['found'] is True
    assert found['request'] == {'request_id': body['request_id'], 'actor_id': owner_id,
        'instance_id': value['release']['instance_id'], 'release_id': body['release_id'],
        'business_id': body['business_id'], 'task_id': task['id'], 'creation_hash': task['creation_hash']}
    assert client.post(lookup_path, json={**body, 'title': '原nonce不能换完整输入'}).status_code == 409
    assert client.post(lookup_path, json={**body, 'graph_inputs': {'watched_entity': str(uuid4())}}).status_code == 409
    retire_path = lookup_path.rsplit('/', 1)[0] + '/retire'
    assert client.post(retire_path, json={'original': body, 'reason': '已保存的任务不能当作未提交撤回'}).status_code == 409
    assert _counts() == before

    username, password = 'v120_other_actor_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        actor = User(username=username, password_hash=password_hash(password))
        db.add(actor); db.flush()
        db.add(Membership(user_id=actor.id, workspace_id=workspace_id, role='operator')); db.commit()
    prior_jobs = _job_ids()
    with TestClient(app) as other:
        login = reply(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': workspace_id})
        before = _assert_login_only(before, prior_jobs)
        assert other.post(lookup_path, json=body).status_code == 403
        assert other.post(create_path, json=body).status_code == 403
    assert _counts() == before

    producer = value['producer']
    reply(client.post('/api/setup/graph-event-producers/' + producer['id'] + '/revoke', json={
        'version': producer['version'], 'reason': '暂停新任务，原发起请求只读核对仍可恢复'}))
    after_revocation = _counts()
    params = {'release_id': body['release_id'], 'business_id': body['business_id']}
    assert client.get(metadata_path, params=params).status_code == 409
    restored = reply(client.post(lookup_path, json=body))
    assert restored == found
    assert reply(client.post(create_path, json=body), status=201)['id'] == task['id']
    assert _counts() == after_revocation
    # A current viewer retains original read recovery; this grants no new Task.
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id,
            Membership.user_id == owner_id))
        membership.role = 'viewer'; db.commit()
    assert reply(client.post(lookup_path, json=body)) == found
    assert client.post(create_path, json=body).status_code == 403
    assert _counts() == after_revocation


def test_actual_retirement_seals_original_nonce_under_revoked_source_and_blocks_late_creation(waiting_release):
    value = waiting_release; client = value['client']; _, create_path, lookup_path = _paths(value)
    body = _body(value); retire_path = lookup_path.rsplit('/', 1)[0] + '/retire'
    assert reply(client.post(lookup_path, json=body))['found'] is False
    producer = value['producer']
    reply(client.post('/api/setup/graph-event-producers/' + producer['id'] + '/revoke', json={
        'version': producer['version'], 'reason': '暂停新执行，未提交输入仍可持久保留和撤回'}))
    before = _counts()
    reason = '已保留原输入，撤回原编号后另行明确填写'
    retired = reply(client.post(retire_path, json={'original': body, 'reason': reason}))
    assert retired['schema'] == 'workflow-task-request-retirement-v1'
    assert retired['request_id'] == body['request_id'] and retired['instance_id'] == value['release']['instance_id']
    assert retired['original'] == NewTask.model_validate(body).model_dump(mode='json', exclude_unset=True)
    assert retired['creation_hash'] == creation_body_hash(NewTask.model_validate(body))
    assert retired['reason'] == reason
    assert retired['command_hash'] == digest({'actor_id': retired['actor_id'],
        'instance_id': retired['instance_id'], 'original': retired['original'], 'reason': reason})
    after = _counts(); table = TaskCreationRetirement.__tablename__
    assert after[table] == before[table] + 1
    assert {key: count for key, count in after.items() if key != table} == {
        key: count for key, count in before.items() if key != table}
    assert reply(client.post(retire_path, json={'original': body, 'reason': reason})) == retired
    recovered = reply(client.post(lookup_path, json=body))
    assert recovered == {'schema': 'workflow-task-request-recovery-v1', 'found': False,
        'request': None, 'retirement': retired}
    assert client.post(retire_path, json={'original': body, 'reason': '同编号不能换撤回理由'}).status_code == 409
    assert client.post(lookup_path, json={**body, 'title': '同编号不能改原输入'}).status_code == 409
    late = client.post(create_path, json=body)
    assert late.status_code == 409 and '原发起请求已保留' in late.json()['detail']
    assert client.post(create_path, json={**body, 'title': '换内容也不能重用已撤回编号'}).status_code == 409
    assert _counts() == after

    username, password = 'v120_retirement_actor_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        workspace_id = db.get(Capability, value['release']['package_id']).workspace_id
        actor = User(username=username, password_hash=password_hash(password)); db.add(actor); db.flush()
        db.add(Membership(user_id=actor.id, workspace_id=workspace_id, role='operator')); db.commit()
    prior_jobs = _job_ids()
    with TestClient(app) as other:
        login = reply(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': workspace_id})
        after = _assert_login_only(after, prior_jobs)
        assert other.post(lookup_path, json=body).status_code == 403
        assert other.post(retire_path, json={'original': body, 'reason': reason}).status_code == 403
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id,
            Membership.user_id == retired['actor_id']))
        membership.role = 'viewer'; db.commit()
    assert reply(client.post(lookup_path, json=body)) == recovered
    assert _counts() == after
