"""Actual public query visibility, with no new model inference.

The standalone test uses the real manual configuration/Goal/check worker and
an ordinary structured query. Root can call the exported candidate assertion
against its already restored V139 receipt; this module never fabricates a
candidate DataRun or reruns the learned model to obtain one.
"""
from contextlib import contextmanager
import copy
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents.security import password_hash
from generic_agents.services import as_dict
from tests.test_native_goals import setup, define, drain


REFERENCE_KEYS = {'schema', 'id', 'version', 'status', 'question', 'created_at',
    'reference_only', 'family', 'context', 'notice'}
CONTEXT_KEYS = {'instance_id', 'task_id', 'detail_path'}


def good(response, status=200):
    assert response.status_code == status, response.text[:800]
    return response.json()


def reference(value, identity, family, *, instance_id, task_id):
    assert type(value) is dict and set(value) == REFERENCE_KEYS
    assert value['schema'] == 'data-query-reference-v144'
    assert value['id'] == identity and value['family'] == family
    assert value['reference_only'] is True
    assert type(value['version']) is int and value['version'] >= 1
    assert all(type(value[key]) is str for key in ('status', 'question', 'created_at', 'notice'))
    assert value['notice'].strip()
    assert type(value['context']) is dict and set(value['context']) == CONTEXT_KEYS
    assert value['context'] == {'instance_id': instance_id, 'task_id': task_id,
        'detail_path': '/api/data/queries/' + identity}
    # Exact closed keys forbid raw result/snapshot data and fabricated data={}.
    assert 'data' not in value
    return value


def inventory(workspace_id):
    with Session(engine()) as db:
        return {model.__name__: {row.id: digest(as_dict(row))
            for row in db.scalars(select(model).where(model.workspace_id == workspace_id))}
            for model in (m.DataRun, m.Job, m.Invocation, m.CloudCall,
                m.Task, m.Entity, m.Memory, m.Audit)}


@contextmanager
def another_operator(workspace_id):
    """A real additional member and cookie login, as in the account fixture."""
    username, password = 'query_visibility_' + uuid4().hex[:16], uuid4().hex
    with Session(engine()) as db:
        user = m.User(username=username, password_hash=password_hash(password))
        db.add(user); db.flush()
        db.add(m.Membership(user_id=user.id, workspace_id=workspace_id, role='operator'))
        db.commit(); user_id = user.id
    with TestClient(app) as client:
        login = good(client.post('/api/login', json={'username': username, 'password': password}))
        client.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': workspace_id})
        me = good(client.get('/api/me'))
        assert me['user_id'] == user_id and me['workspace_id'] == workspace_id and me['role'] == 'operator'
        yield client


def assert_native_candidate_visibility(owner_client, other_client, instance_id, query_id):
    """Read-only assertions over Root's actual already received V139 query.

    The returned proof contains real public responses and stable inventories.
    Both clients must already hold their genuine same-workspace sessions.
    """
    owner, other = good(owner_client.get('/api/me')), good(other_client.get('/api/me'))
    assert owner['workspace_id'] == other['workspace_id'] and owner['user_id'] != other['user_id']
    assert other['role'] == 'operator'
    dedicated = '/api/workspace/instances/' + instance_id + '/native-candidate-queries/' + query_id
    public = '/api/data/queries/' + query_id
    before = inventory(owner['workspace_id'])
    actual = good(owner_client.get(dedicated))
    assert actual['id'] == query_id and actual['schema'] == 'native-candidate-query-view-v139'
    assert other_client.get(dedicated).status_code == 404
    assert other_client.get(public).status_code == 404
    owner_rows = good(owner_client.get('/api/data/queries'))
    other_rows = good(other_client.get('/api/data/queries'))
    assert type(owner_rows) is list and type(other_rows) is list
    assert not any(row.get('id') == query_id for row in other_rows), 'another member must not discover a private native candidate through the generic list'
    own = [row for row in owner_rows if row.get('id') == query_id]
    assert len(own) == 1, 'the original actor must retain a usable safe reference'
    reference(own[0], query_id, 'native_candidate', instance_id=instance_id, task_id=None)
    recovered = good(owner_client.get(public))
    assert recovered == actual, 'the reference resolves through the original cold-checked candidate projection'
    assert inventory(owner['workspace_id']) == before, 'all reads must leave Jobs, calls, source records and audits unchanged'
    return {'workspace_id': owner['workspace_id'], 'owner_id': owner['user_id'],
        'other_id': other['user_id'], 'query_id': query_id,
        'readbacks': [{'path': dedicated, 'response': actual},
            {'path': public, 'response': recovered},
            {'path': '/api/data/queries', 'response': owner_rows}],
        'other_query_ids': [row['id'] for row in other_rows], 'inventory': before}


def test_generic_shared_queries_remain_readable_and_native_goal_runs_are_safe_task_references(client, account):
    release, obj, task, base, command = setup(client)
    define(client, base, command); drain(task)
    task_path = '/api/workspace/tasks/' + task['id']
    original_task = good(client.get(task_path))
    with Session(engine()) as db:
        saved_task = db.get(m.Task, task['id'])
        query_id = saved_task.data['native_goal_query_id']
        native = db.get(m.DataRun, query_id)
        assert native.data['native_goal_task_id'] == saved_task.id
        assert native.data['goal_delegation']['task_id'] == saved_task.id
        assert native.data['native_goal_request_hash'] == digest(native.data['request'])
        assert native.data.get('result'), 'the original worker produced actual stored evidence'
        assert not list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == account[2])))
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == account[2])))
    ordinary = good(client.post('/api/data/queries', json={
        'question': '核对指定原业务档案的已登记字段', 'entity_ids': [obj['id']],
        'fields': command['goal_spec']['required_fields'], 'mode': 'structured',
        'evidence_requirement': {'source': 'archive'}}))
    assert ordinary['id'] != query_id and ordinary['data'].get('result')
    with another_operator(account[2]) as other:
        before = inventory(account[2])
        owner_rows = good(client.get('/api/data/queries'))
        shared_rows = good(other.get('/api/data/queries'))
        assert next(row for row in owner_rows if row['id'] == ordinary['id']) == ordinary
        assert next(row for row in shared_rows if row['id'] == ordinary['id']) == ordinary
        assert good(other.get('/api/data/queries/' + ordinary['id'])) == ordinary
        owned = next(row for row in owner_rows if row['id'] == query_id)
        reference(owned, query_id, 'native_task', instance_id=release['instance_id'], task_id=task['id'])
        assert good(client.get('/api/data/queries/' + query_id)) == owned
        task_read = other.get(task_path)
        if task_read.status_code == 200:
            visible = next(row for row in shared_rows if row['id'] == query_id)
            reference(visible, query_id, 'native_task', instance_id=release['instance_id'], task_id=task['id'])
            assert good(other.get('/api/data/queries/' + query_id)) == visible
        else:
            assert task_read.status_code in (403, 404)
            assert not any(row['id'] == query_id for row in shared_rows)
            assert other.get('/api/data/queries/' + query_id).status_code in (403, 404)
        assert good(client.get(task_path)) == original_task
        assert inventory(account[2]) == before


def _generic_writes_are_blocked(client, identity, version):
    base = '/api/data/queries/' + identity
    command = {'expected_version': version, 'reason': '核对原记录，不能绕开原任务的权限与固定依据'}
    for method, path, body in (
        ('put', base + '/draft', {**command, 'draft': {'question': '仅保存原核对说明'}}),
        ('post', base + '/confirm', command),
        ('post', base + '/review-derived', {**command, 'decision': 'confirmed'}),
        ('post', base + '/cancel', command),
    ):
        response = getattr(client, method)(path, json=body)
        assert response.status_code == 409, (path, response.status_code, response.text[:500])


def test_native_goal_reverse_link_prevents_deleted_and_malformed_marker_generic_bypasses(client, account):
    release, _, task, base, command = setup(client)
    define(client, base, command); drain(task)
    task_path = '/api/workspace/tasks/' + task['id']
    original_task = good(client.get(task_path))
    with Session(engine()) as db:
        saved = db.get(m.Task, task['id'])
        identity = saved.data['native_goal_query_id']
        run = db.get(m.DataRun, identity)
        original = copy.deepcopy(run.data)
        version = run.version
    # The actual original Task keeps its independent query pointer throughout.
    # No Task/Job/Invocation, request, result or claimed success is fabricated.
    removed = {key: value for key, value in original.items() if key not in {
        'native_goal_task_id', 'native_goal_context_hash', 'native_goal_request_hash'}}
    variants = [
        {key: value for key, value in original.items() if key != 'native_goal_task_id'},
        removed,
        {**original, 'native_query_contract': None},
        {**original, 'native_task_query_contract': {'task_id': None}},
        {**original, 'native_goal_task_id': None},
    ]
    try:
        for damaged in variants:
            with Session(engine()) as db:
                db.get(m.DataRun, identity).data = copy.deepcopy(damaged); db.commit()
            before = inventory(account[2])
            response = client.get('/api/data/queries/' + identity)
            if response.status_code == 200:
                reference(response.json(), identity, 'native_task',
                    instance_id=release['instance_id'], task_id=task['id'])
            else:
                assert response.status_code == 409, response.text[:500]
            rows = good(client.get('/api/data/queries'))
            for value in rows:
                if value['id'] == identity:
                    reference(value, identity, 'native_task',
                        instance_id=release['instance_id'], task_id=task['id'])
            _generic_writes_are_blocked(client, identity, version)
            assert good(client.get(task_path)) == original_task
            assert inventory(account[2]) == before, 'failed generic writes must not repair, overwrite or append history'
    finally:
        with Session(engine()) as db:
            db.get(m.DataRun, identity).data = original; db.commit()
    recovered = good(client.get('/api/data/queries/' + identity))
    reference(recovered, identity, 'native_task', instance_id=release['instance_id'], task_id=task['id'])


def assert_candidate_marker_guard_on_disposable_clone(owner_client, other_client, instance_id, query_id):
    """Optional fault probe ONLY on Root's independent disposable V139 clone.

    Unlike assert_native_candidate_visibility, this helper deliberately deletes
    one DataRun marker and restores it in finally. It must not be called against
    the already sealed acceptance database, and never runs a model or a query.
    """
    owner = good(owner_client.get('/api/me'))
    with Session(engine()) as db:
        row = db.get(m.DataRun, query_id)
        assert row.workspace_id == owner['workspace_id'] and row.owner_id == owner['user_id']
        original, version = copy.deepcopy(row.data), row.version
        assert 'native_candidate_query_v139' in original
        assert list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == owner['workspace_id'],
            m.Audit.object_id == query_id, m.Audit.action == 'native_candidate_query.intent_v139')))
    try:
        with Session(engine()) as db:
            db.get(m.DataRun, query_id).data = {key: value for key, value in original.items()
                if key not in {'native_candidate_query_v139', 'native_candidate_query_v139_hash'}}
            db.commit()
        before = inventory(owner['workspace_id'])
        public = '/api/data/queries/' + query_id
        assert other_client.get(public).status_code == 404
        assert not any(value['id'] == query_id for value in good(other_client.get('/api/data/queries')))
        own = next(value for value in good(owner_client.get('/api/data/queries')) if value['id'] == query_id)
        reference(own, query_id, 'native_candidate', instance_id=None, task_id=None)
        assert good(owner_client.get(public)) == own
        _generic_writes_are_blocked(owner_client, query_id, version)
        assert inventory(owner['workspace_id']) == before
        proof = {'query_id': query_id, 'damaged_reference': own, 'writes_blocked': 4}
    finally:
        with Session(engine()) as db:
            db.get(m.DataRun, query_id).data = original; db.commit()
    original_view = good(owner_client.get('/api/workspace/instances/' + instance_id
        + '/native-candidate-queries/' + query_id))
    assert original_view['schema'] == 'native-candidate-query-view-v139'
    return {**proof, 'restored': original_view}


def test_native_goal_public_gap_recheck_child_remains_task_reference_without_new_model(client, account):
    from generic_agents.worker import process_once

    release, _, task, base, command = setup(client, source='observations', observation_mode='existing_only')
    define(client, base, command); drain(task)
    with Session(engine()) as db:
        original_task = db.get(m.Task, task['id'])
        parent_id = original_task.data['native_goal_query_id']
        assert not original_task.data.get('current_plan_id')
        gap = db.scalars(select(m.DataGap).where(m.DataGap.workspace_id == account[2],
            m.DataGap.query_id == parent_id)).one()
        gap_id = gap.id
        assert gap.data['business_task_id'] == task['id']
    gap_path = '/api/data/gaps/' + gap_id
    gap = good(client.get(gap_path))
    assert gap['status'] not in ('resolved', 'cancelled')
    requested = good(client.post(gap_path + '/recheck', json={
        'expected_version': gap['version'], 'reason': '只核对原目标已有资料，不派发新观测或模型'}))
    assert requested['status'] == 'verifying'
    with Session(engine()) as db:
        job = db.scalars(select(m.Job).where(m.Job.workspace_id == account[2],
            m.Job.kind == 'recheck_data_gap', m.Job.data['gap_id'].as_string() == gap_id,
            m.Job.status == 'queued')).one()
        job_id = job.id
    assert process_once(job_id, deliver=False, workspace_id=account[2])
    gap = good(client.get(gap_path))
    child_id = gap['data']['latest_recheck_query_id']
    assert child_id != parent_id
    with Session(engine()) as db:
        child = db.get(m.DataRun, child_id)
        saved_task = db.get(m.Task, task['id'])
        assert child.data['parent_query_id'] == parent_id
        assert child.data['gap_recheck']['gap_id'] == gap_id
        assert child.data['goal_delegation']['task_id'] == task['id']
        assert not any(key in child.data for key in (
            'native_goal_task_id', 'native_query_contract', 'native_task_query_contract'))
        assert saved_task.data['native_goal_query_id'] == parent_id
        assert saved_task.data.get('data_query_run_id') != child_id
        assert not list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == account[2])))
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == account[2])))
        assert not list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id == account[2])))
        version, original = child.version, copy.deepcopy(child.data)
    before = inventory(account[2])
    current = good(client.get('/api/data/queries/' + child_id))
    reference(current, child_id, 'native_task', instance_id=release['instance_id'], task_id=task['id'])
    assert next(value for value in good(client.get('/api/data/queries')) if value['id'] == child_id) == current
    _generic_writes_are_blocked(client, child_id, version)
    assert inventory(account[2]) == before
    # The actual DataGap still records this real child after both child-side
    # pointers are removed. It must independently keep the query restricted.
    try:
        with Session(engine()) as db:
            db.get(m.DataRun, child_id).data = {key: value for key, value in original.items()
                if key not in {'parent_query_id', 'gap_recheck', 'goal_delegation'}}
            db.commit()
        damaged_before = inventory(account[2])
        response = client.get('/api/data/queries/' + child_id)
        if response.status_code == 200:
            reference(response.json(), child_id, 'native_task',
                instance_id=release['instance_id'], task_id=task['id'])
        else:
            assert response.status_code == 409, response.text[:500]
        for value in good(client.get('/api/data/queries')):
            if value['id'] == child_id:
                reference(value, child_id, 'native_task',
                    instance_id=release['instance_id'], task_id=task['id'])
        _generic_writes_are_blocked(client, child_id, version)
        assert inventory(account[2]) == damaged_before
    finally:
        with Session(engine()) as db:
            db.get(m.DataRun, child_id).data = original; db.commit()
    assert good(client.get('/api/data/queries/' + child_id)) == current


def test_ordinary_business_delegation_keeps_shared_raw_query_compatibility(client, account):
    from tests.test_data_tools import setup as tools_setup
    from tests.test_goal_queries import goal
    from tests.test_goal_time import model
    from generic_agents.worker import process_once

    package, objects, _, steps = tools_setup(client)
    task = good(client.post('/api/tasks', json={'title': '旧业务已有证据的共享委托查询',
        'package_id': package['id'], 'entity_ids': [value['id'] for value in objects],
        'goal_spec': goal(objects, steps)}), 201)
    profile = model(client)
    plan = good(client.post('/api/tasks/' + task['id'] + '/plan', json={
        'expected_version': task['version'], 'reason': '先保存实际委托查询，不生成模型计划',
        'model_profile_id': profile['id']}))
    assert process_once(plan['data']['planning_job_id'], deliver=False, workspace_id=account[2])
    with Session(engine()) as db:
        saved_task = db.get(m.Task, task['id'])
        assert not saved_task.data.get('workflow_runtime') and not saved_task.data.get('native_goal_context')
        identity = saved_task.data['data_query_run_id']
        row = db.get(m.DataRun, identity)
        assert row.data['goal_delegation']['task_id'] == task['id']
        expected = as_dict(row)
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == account[2])))
        assert not list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == account[2])))
    with another_operator(account[2]) as other:
        before = inventory(account[2])
        for reader in (client, other):
            assert good(reader.get('/api/data/queries/' + identity)) == expected
            assert next(value for value in good(reader.get('/api/data/queries')) if value['id'] == identity) == expected
        assert inventory(account[2]) == before
