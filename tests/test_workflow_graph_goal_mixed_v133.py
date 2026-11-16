"""Real graph/query workers compete for one original Goal's shared allowance.

Root executes this source against its owned disposable PostgreSQL.  The only
seams below pause an unchanged production gateway/fence; they do not replace a
guard, budget, Invocation, result, Plan or original Task authority.  Both winners
execute real registered local builtins.  The supporting-query case also retains
a real CloudCall for one precise synthetic model HTTP response, with no paid IO
or claim about model understanding quality.
"""
import copy
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.models import Capability, CloudCall, DataRun, Invocation, Job, Plan, Task
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_vertical_slice import manifest, publish
from tests.test_workflow_graph_goal_guard_v133 import (
    _calls, _stop, _task, waiting_release, graph_seed, v7_scene, reading_scene,
    local_runtime, full_source_scene, reply,
)


def _ids(model):
    with Session(engine()) as db:
        return set(db.scalars(select(model.id)))


def _query_task(value):
    # Reuse the actually issued graph/Goal fixture's complete creation body.
    # The helper's original Task remains a distinct stopped control, rather than
    # editing its frozen Goal to add query authority after creation.
    control, body = _task(value, calls=1, steps=12)
    _stop(value['client'], control)
    body = copy.deepcopy(body)
    with Session(engine()) as db:
        package = db.get(Capability, value['release']['package_id'])
        fixed = package.data['specification']['native_query_catalog']['tools']
        aggregate = next(pin for pin in fixed if pin['method'] == 'aggregate')
        cap = db.get(Capability, aggregate['id'])
        assert cap.status == 'published' and cap.content_hash == aggregate['hash']
        assert cap.data['execution']['adapter'] == 'builtin'
    body['request_id'] = str(uuid4())
    body['title'] = 'V133完整图与原目标本机查询共享一次调用'
    body['goal_spec']['data_query'] = {
        'tool_steps': [{'id': 'count_original', 'capability_id': aggregate['id'],
            'input_from': 'evidence', 'parameters': {
                'field': body['goal_spec']['required_fields'][0], 'operation': 'count'}}],
        'max_tool_seconds': 60, 'allow_external_tools': False}
    client = value['client']
    path = '/api/workspace/instances/' + value['release']['instance_id'] + '/tasks'
    task = reply(client.post(path, json=body), status=201)
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert len(task['graph_jobs']) == 1
    with Session(engine()) as db:
        original = db.get(Task, task['id'])
        check_job = original.data['native_goal_check_job_pin']['job_id']
    assert process_once(check_job, deliver=False)
    with Session(engine()) as db:
        original = db.get(Task, task['id'])
        query = db.get(DataRun, original.data['data_query_run_id'])
        assert query.status == 'queued'
        assert query.data['native_query_contract']['task_id'] == original.id
        assert query.data['goal_delegation']['task_id'] == original.id
        query_job = db.get(Job, query.data['native_query_job_id'])
        assert query_job.kind == 'execute_native_goal_query' and query_job.status == 'queued'
        query_id, query_job_id = query.id, query_job.id
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    transport = next(iter(task['graph_jobs'].values()))
    return {'task': task, 'body': body, 'control_id': control['id'],
        'graph_job_id': transport['job_id'], 'graph_key': transport['invocation_key'],
        'query_id': query_id, 'query_job_id': query_job_id,
        'query_key': 'data-tool:' + query_id + ':count_original', 'query_capability_id': aggregate['id']}


def _principal(client):
    me = reply(client.get('/api/me'))
    # Read-only source counters use the actual current identity, with no new
    # authority or synthetic role.  No operation is dispatched by this probe.
    return Principal(me['user_id'], me['workspace_id'], me['role'], 'v133-mixed-counter-probe')


def _assert_graph_aggregate(db, task, job, call):
    """Count the frozen selected numeric values, not the number of objects.

    This graph pins the workflow aggregate_values builtin with field='数量'.
    The real source design can lack that label (or hold a nonnumeric value).
    Unlike the native query counter, this builtin counts only finite numbers.
    """
    cap = db.get(Capability, call.capability_id)
    assert cap.data['execution']['adapter'] == 'builtin'
    assert cap.data['execution']['handler'] == 'aggregate_values'
    assert cap.content_hash == call.data['capability_hash'] == digest(cap.data)
    inputs = job.data['inputs']
    activation = task.data['graph_runtime']['activations'][job.data['activation_id']]
    assert inputs == activation['inputs_snapshot']['arguments']
    assert job.data['input_hash'] == call.data['input_hash'] == digest(inputs)
    expected_records = [{'entity_id': obj['id'], 'type': obj['type_name'], 'version': obj['version'],
        'attributes': {field['label']: copy.deepcopy(obj['attributes'][field['key']])
            for field in obj['fields'] if field['key'] in obj['attributes']}}
        for obj in task.data['object_snapshots']]
    assert inputs['records'] == expected_records
    assert inputs['operation'] == 'count'
    selected = [record['attributes'].get(inputs['field']) for record in expected_records]
    finite = [item for item in selected
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)]
    expected = {'field': inputs['field'], 'operation': 'count', 'value': len(finite),
        'sample_count': len(finite), 'missing_count': len(expected_records) - len(finite),
        'method': 'registered_archive_aggregate',
        'provenance': [{'entity_id': record['entity_id'], 'version': record['version']}
            for record in expected_records]}
    assert call.data['output'] == expected
    receipt = next(row for row in task.data['workflow_runtime']['receipts'] if row['invocation_id'] == call.id)
    assert receipt['input_snapshot'] == inputs and receipt['input_hash'] == digest(inputs)
    assert receipt['output'] == expected


def test_committed_graph_reservation_blocks_original_goal_query_before_any_invocation(waiting_release, monkeypatch):
    from generic_agents import gateway
    from generic_agents.goal_queries import call_count
    from generic_agents.native_step_execution import step_count
    from generic_agents.workflow_graph_goal_guard_v133 import pending_steps
    value = waiting_release; scene = _query_task(value); before = _calls(); cloud = _ids(CloudCall)
    entered, release = threading.Event(), threading.Event(); seen = []
    original = gateway.invoke

    def pause_graph_before_real_invoke(db, p, capability_id, agent_role, inputs, key, **kwargs):
        seen.append(key)
        if key == scene['graph_key']:
            entered.set()
            assert release.wait(30), 'actual graph worker was not released'
        return original(db, p, capability_id, agent_role, inputs, key, **kwargs)

    monkeypatch.setattr(gateway, 'invoke', pause_graph_before_real_invoke)
    with ThreadPoolExecutor(max_workers=2) as pool:
        graph = pool.submit(process_once, scene['graph_job_id'], deliver=False)
        try:
            assert entered.wait(30), 'graph did not reach its committed reservation before real invoke'
            assert _calls() == before
            p = _principal(value['client'])
            with Session(engine()) as db:
                task = db.get(Task, scene['task']['id'])
                reservations = task.data['graph_goal_reservations_v133']['items']
                assert set(reservations) == {scene['graph_key']}
                assert call_count(db, p, task) == 1 and step_count(task) == 1 and pending_steps(task) == 1
            query = pool.submit(process_once, scene['query_job_id'], deliver=False)
            assert query.result(timeout=30)
            assert scene['query_key'] not in seen, 'the losing query must be stopped before gateway.invoke'
            assert _calls() == before
            with Session(engine()) as db:
                row = db.get(DataRun, scene['query_id'])
                assert row.status not in {'queued', 'running', 'sufficient'}
                assert not any(item.get('invocation_id') for item in row.data.get('tool_trace', []))
                assert '预算' in str(row.data.get('error') or row.data.get('result', {}).get('tool_blocker'))
        finally:
            release.set()
        assert graph.result(timeout=30)
    assert seen == [scene['graph_key']]
    with Session(engine()) as db:
        task = db.get(Task, scene['task']['id']); job = db.get(Job, scene['graph_job_id'])
        actual = list(db.scalars(select(Invocation).where(Invocation.id.not_in(before))))
        assert job.status == 'succeeded' and len(actual) == 1
        call = actual[0]
        assert call.status == 'succeeded' and call.idempotency_key == scene['graph_key']
        assert call.data['job_id'] == job.id
        _assert_graph_aggregate(db, task, job, call)
        assert len(task.data['workflow_runtime']['receipts']) == 1
        assert call_count(db, p, task) == 1 and pending_steps(task) == 0
        assert db.get(Task, scene['control_id']).data['native_goal_cancelled']
    assert _ids(CloudCall) == cloud


def test_actual_query_invocation_blocks_graph_reservation_then_real_query_finishes(waiting_release, monkeypatch):
    from generic_agents import native_goal_queries
    from generic_agents.goal_queries import call_count
    value = waiting_release; scene = _query_task(value); before = _calls(); cloud = _ids(CloudCall)
    entered, release = threading.Event(), threading.Event(); reached = []
    original = native_goal_queries.before_dispatch

    def pause_after_actual_query_invocation(db, p, cap, inputs, key):
        if key == scene['query_key']:
            reached.append(key); entered.set()
            assert release.wait(30), 'actual query worker was not released'
        return original(db, p, cap, inputs, key)

    monkeypatch.setattr(native_goal_queries, 'before_dispatch', pause_after_actual_query_invocation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        query = pool.submit(process_once, scene['query_job_id'], deliver=False)
        try:
            assert entered.wait(30), 'query did not reach the real final dispatch fence'
            p = _principal(value['client'])
            with Session(engine()) as db:
                actual = list(db.scalars(select(Invocation).where(Invocation.id.not_in(before))))
                assert len(actual) == 1 and actual[0].status == 'running'
                assert actual[0].idempotency_key == scene['query_key']
                assert 'output' not in actual[0].data
                task = db.get(Task, scene['task']['id'])
                assert call_count(db, p, task) == 1
                assert not task.data.get('graph_goal_reservations_v133', {}).get('items')
                query_call = actual[0].id
            graph = pool.submit(process_once, scene['graph_job_id'], deliver=False)
            assert graph.result(timeout=30)
            assert _calls() == before | {query_call}, 'losing graph must not create another Invocation'
            with Session(engine()) as db:
                task = db.get(Task, scene['task']['id']); job = db.get(Job, scene['graph_job_id'])
                assert job.status in {'failed', 'blocked'}
                assert not task.data.get('graph_goal_reservations_v133', {}).get('items')
                assert not task.data['workflow_runtime']['receipts']
        finally:
            release.set()
        assert query.result(timeout=30)
    assert reached == [scene['query_key']]
    with Session(engine()) as db:
        query = db.get(DataRun, scene['query_id']); call = db.get(Invocation, query_call)
        assert db.get(Job, scene['query_job_id']).status == 'succeeded'
        assert query.status == 'sufficient' and len(query.data['tool_trace']) == 1
        trace = query.data['tool_trace'][0]
        assert trace['invocation_id'] == call.id and trace['status'] == 'succeeded'
        assert call.status == 'succeeded' and call.capability_id == scene['query_capability_id']
        assert call.data['job_id'] == scene['query_job_id'] and call.data['output'] == trace['output']
        assert call.data['output']['records'][0]['values']['value'] == 1
        assert call_count(db, p, db.get(Task, scene['task']['id'])) == 1
    assert _calls() == before | {query_call} and _ids(CloudCall) == cloud


def test_graph_task_does_not_gain_a_legacy_native_plan_authorization_for_zero_invocation_actions(waiting_release):
    value = waiting_release; client = value['client']; task, _ = _task(value, calls=1, steps=12)
    model = publish(client, manifest('foundation_model', name='v133_readonly_plan_boundary_' + uuid4().hex,
        allowed_agent_roles=['business'], execution={'provider': 'deepseek', 'model': 'deepseek-chat',
            'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 2048}, data_policy={'cloud_allowed': True}))
    calls, cloud, plans, jobs = _calls(), _ids(CloudCall), _ids(Plan), _ids(Job)
    with Session(engine()) as db:
        original = copy.deepcopy(db.get(Task, task['id']).data)
    response = client.post('/api/workspace/tasks/' + task['id'] + '/intelligent-plan/preview',
        json={'model_profile_id': model['id']})
    assert response.status_code == 409, response.text
    assert '多个协同节点' in response.json()['detail']
    assert _calls() == calls and _ids(CloudCall) == cloud and _ids(Plan) == plans and _ids(Job) == jobs
    with Session(engine()) as db:
        assert db.get(Task, task['id']).data == original
    # This is an explicit product boundary, not proof that query/inspect/report
    # Plans can already be approved on a graph Task or compete for its steps.


def _supporting_query(value, monkeypatch):
    """Actual public proposal -> meaning review -> adopt -> separate execute.

    Only the precise model transport returns a declared synthetic candidate.
    CloudCall reservation/receipt, v6 grounding, original-session authority,
    human command journals and the eventual data worker are unchanged.
    """
    client = value['client']; task, creation = _task(value, calls=1, steps=12)
    original_goal, original_context_hash = copy.deepcopy(task['goal_spec']), task['native_goal_context_hash']
    root = '/api/workspace/tasks/' + task['id']
    proposals = root + '/query-proposals'
    listing = reply(client.get(proposals))
    assert listing['mode'] == 'reference_only' and listing['can_manage']
    catalog = listing['catalog']; field = creation['goal_spec']['required_fields'][0]
    definition = next(row for row in catalog['fields'] if row['key'] == field)
    tool = next(row for row in catalog['query_tools'] if row['method'] == 'aggregate')
    label = definition['label']; field_quote = '「' + label + '」'; operation_quote = '统计记录数量'
    question = '本任务原范围；' + field_quote + '；' + operation_quote + '。'
    assert question.count(field_quote) == question.count(operation_quote) == 1
    draft = {'request_id': str(uuid4()), 'expected_version': listing['task_version'],
        'reason': '明确保存本任务原范围内的补充查询，不改变正式目标或自动执行',
        'question': question, 'evidence': {
            'source': 'archive', 'minimum_verification': 'recorded',
            'time': {'mode': 'archive', 'timezone': 'Asia/Shanghai'}}}
    reply(client.put(proposals + '/draft', json=draft))
    model = publish(client, manifest('foundation_model', name='v133_support_query_' + uuid4().hex,
        allowed_agent_roles=['business'], execution={'provider': 'deepseek', 'model': 'deepseek-chat',
            'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 2048}, data_policy={'cloud_allowed': True}))
    candidate = {'status': 'candidate', 'summary': '合成候选，仅统计原档案记录条数，必须人工核对含义',
        'fields': [{'key': field, 'quote': field_quote}], 'relation_query': None,
        'operations': [{'capability_id': tool['id'], 'input_from': 0,
            'parameters': {'field': field, 'operation': 'count'}, 'quote': operation_quote}],
        'result_criterion': None, 'limitations': []}
    initial_cloud = _ids(CloudCall); calls = _calls(); sent = []
    with monkeypatch.context() as transport:
        transport.setattr(settings(), 'cloud_daily_call_limit', 10)
        transport.setattr(settings(), 'cloud_daily_token_limit', 1000000)
        transport.setattr(settings(), 'deepseek_api_key', SecretStr('synthetic-v133-no-real-key'))
        original_post = httpx.Client.post

        def synthetic_provider(client, target, **kwargs):
            if str(target) != 'https://api.deepseek.com/chat/completions':
                return original_post(client, target, **kwargs)
            assert kwargs['headers']['Authorization'] == 'Bearer synthetic-v133-no-real-key'
            payload = copy.deepcopy(kwargs['json'])
            request = json.loads(payload['messages'][-1]['content'])
            assert request['question'] == question
            assert any(row['key'] == field for row in request['confirmed_catalog']['fields'])
            assert any(row['id'] == tool['id'] for row in request['confirmed_catalog']['query_tools'])
            sent.append(payload)
            return httpx.Response(200, json={
                'choices': [{'message': {'content': json.dumps(candidate, ensure_ascii=False)}}],
                'usage': {'prompt_tokens': 60, 'completion_tokens': 30, 'total_tokens': 90}},
                headers={'x-request-id': 'synthetic-v133-support-' + str(len(sent))})

        transport.setattr(httpx.Client, 'post', synthetic_provider)
        preview = reply(client.post(proposals + '/preview', json={'model_profile_id': model['id']}))
        command = {'request_id': str(uuid4()), 'expected_version': preview['task_version'],
            'reason': '已核对本次完整文字发送，只生成补充查询候选，不自动执行',
            'model_profile_id': model['id'], 'preview_hash': preview['preview_hash'], 'cloud_authorized': True}
        created = reply(client.post(proposals, json=command)); proposal_id = created['current']['id']
        with Session(engine()) as db:
            proposal = db.get(Plan, proposal_id); proposal_job = proposal.data['job_id']
        assert process_once(proposal_job, deliver=False)
    assert len(sent) == 1 and _calls() == calls
    with Session(engine()) as db:
        cloud = list(db.scalars(select(CloudCall).where(CloudCall.id.not_in(initial_cloud))))
        assert len(cloud) == 1 and cloud[0].status == 'succeeded' and cloud[0].charged_tokens == 90
        assert db.get(Plan, proposal_id).data['cloud_call_id'] == cloud[0].id
    current = reply(client.get(proposals)); row = current['current']
    assert row['id'] == proposal_id and row['status'] == 'needs_review'
    assert row['source_grounding']['semantic_equivalence_verified'] is False
    review = {'request_id': str(uuid4()), 'expected_version': current['task_version'],
        'expected_proposal_version': row['version'], 'decision': 'confirmed',
        'meaning_confirmed': True, 'ranked_subset_confirmed': False,
        'reason': '人工明确核对原字段、原范围、档案记录计数和无比较标准；不认定现场情况'}
    reviewed = reply(client.post(proposals + '/' + proposal_id + '/review', json=review))
    assert reviewed['current']['status'] == 'confirmed'
    adopt = {'request_id': str(uuid4()), 'expected_version': reviewed['task_version'],
        'expected_proposal_version': reviewed['current']['version'],
        'review_hash': reviewed['current']['review_hash'],
        'reason': '明确采用为原目标的参考查询，保持正式完成标准不变；执行另行确认'}
    adopted = reply(client.post(proposals + '/' + proposal_id + '/adopt', json=adopt))
    assert adopted['current']['status'] == 'adopted'
    assert adopted['reference_query']['mode'] == 'reference_only'
    current_task = reply(client.get(root))
    execution = {'request_id': str(uuid4()), 'expected_version': current_task['version'],
        'proposal_id': proposal_id, 'reason': '单独批准执行已人工核对的本机只读补充查询，不推进业务目标'}
    started = reply(client.post(root + '/supporting-queries', json=execution))
    query_id = started['run_id']
    with Session(engine()) as db:
        query = db.get(DataRun, query_id)
        assert query.status == 'queued' and query.data['reference_only'] is True
        assert query.data['native_task_query_contract']['task_id'] == task['id']
        assert not query.data.get('goal_delegation'), 'this is the distinct supporting-query guard family'
        query_job = db.get(Job, query.data['native_query_job_id'])
        assert query_job.kind == 'execute_native_task_query'
        steps = query.data['request']['tool_steps']; assert len(steps) == 1
        query_job_id, query_key = query_job.id, 'data-tool:' + query.id + ':' + steps[0]['id']
    task = reply(client.get(root)); graph = next(iter(task['graph_jobs'].values()))
    assert task['goal_spec'] == original_goal and task['native_goal_context_hash'] == original_context_hash
    return {'task': task, 'graph_job_id': graph['job_id'], 'graph_key': graph['invocation_key'],
        'query_id': query_id, 'query_job_id': query_job_id, 'query_key': query_key,
        'proposal_id': proposal_id, 'execution': execution}


def test_committed_graph_reservation_stops_an_actually_adopted_supporting_query_before_invocation(waiting_release, monkeypatch):
    from generic_agents import gateway
    value = waiting_release; scene = _supporting_query(value, monkeypatch)
    before, cloud = _calls(), _ids(CloudCall); entered, release = threading.Event(), threading.Event()
    reached = []; original = gateway.invoke

    def pause_graph_before_actual_gateway(db, p, capability_id, agent_role, inputs, key, **kwargs):
        reached.append(key)
        if key == scene['graph_key']:
            entered.set()
            assert release.wait(30), 'actual graph worker was not released'
        return original(db, p, capability_id, agent_role, inputs, key, **kwargs)

    monkeypatch.setattr(gateway, 'invoke', pause_graph_before_actual_gateway)
    with ThreadPoolExecutor(max_workers=2) as pool:
        graph = pool.submit(process_once, scene['graph_job_id'], deliver=False)
        try:
            assert entered.wait(30)
            assert _calls() == before
            with Session(engine()) as db:
                task = db.get(Task, scene['task']['id'])
                assert set(task.data['graph_goal_reservations_v133']['items']) == {scene['graph_key']}
            query = pool.submit(process_once, scene['query_job_id'], deliver=False)
            assert query.result(timeout=30)
            assert scene['query_key'] not in reached, 'supporting query cannot enter gateway even without goal_delegation'
            assert _calls() == before
            with Session(engine()) as db:
                query = db.get(DataRun, scene['query_id'])
                assert query.status not in {'queued', 'running', 'sufficient'}
                assert query.data['reference_only'] is True
                assert not any(step.get('invocation_id') for step in query.data.get('tool_trace', []))
                assert '预算' in str(query.data.get('error') or query.data.get('result', {}).get('tool_blocker'))
        finally:
            release.set()
        assert graph.result(timeout=30)
    with Session(engine()) as db:
        calls = list(db.scalars(select(Invocation).where(Invocation.id.not_in(before))))
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        assert calls[0].idempotency_key == scene['graph_key']
        task, job = db.get(Task, scene['task']['id']), db.get(Job, scene['graph_job_id'])
        assert job.status == 'succeeded'
        _assert_graph_aggregate(db, task, job, calls[0])
        assert len(task.data['workflow_runtime']['receipts']) == 1
        assert db.get(Plan, scene['proposal_id']).status == 'adopted'
    assert _ids(CloudCall) == cloud and reached == [scene['graph_key']]


def test_actual_supporting_query_invocation_wins_then_passes_its_real_final_fence(waiting_release, monkeypatch):
    from generic_agents import native_task_queries
    from generic_agents.goal_queries import call_count
    value = waiting_release; scene = _supporting_query(value, monkeypatch)
    before, cloud = _calls(), _ids(CloudCall)
    original_goal = copy.deepcopy(scene['task']['goal_spec'])
    original_context = copy.deepcopy(scene['task']['native_goal_context'])
    original_context_hash = scene['task']['native_goal_context_hash']
    entered, release = threading.Event(), threading.Event(); reached = []
    original = native_task_queries.before_dispatch

    def pause_after_real_supporting_invocation(db, p, cap, inputs, key):
        if key == scene['query_key']:
            reached.append(key); entered.set()
            assert release.wait(30), 'actual supporting query was not released'
        return original(db, p, cap, inputs, key)

    monkeypatch.setattr(native_task_queries, 'before_dispatch', pause_after_real_supporting_invocation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        query = pool.submit(process_once, scene['query_job_id'], deliver=False)
        try:
            assert entered.wait(30), 'supporting query did not reach its actual final fence'
            p = _principal(value['client'])
            with Session(engine()) as db:
                actual = list(db.scalars(select(Invocation).where(Invocation.id.not_in(before))))
                assert len(actual) == 1 and actual[0].status == 'running'
                call = actual[0]; query_call = call.id
                assert call.idempotency_key == scene['query_key'] and 'output' not in call.data
                assert call.data['job_id'] == scene['query_job_id']
                row = db.get(DataRun, scene['query_id']); task = db.get(Task, scene['task']['id'])
                assert row.data['native_task_query_contract']['task_id'] == task.id
                assert not row.data.get('goal_delegation') and row.data['reference_only'] is True
                assert call.workspace_id == row.workspace_id == task.workspace_id
                assert row.owner_id == task.data['native_goal_context']['actor_id']
                assert call_count(db, p, task) == 1
                assert not task.data.get('graph_goal_reservations_v133', {}).get('items')
            graph = pool.submit(process_once, scene['graph_job_id'], deliver=False)
            assert graph.result(timeout=30)
            assert _calls() == before | {query_call}, 'graph cannot reserve beyond the actual query call'
            with Session(engine()) as db:
                task = db.get(Task, scene['task']['id'])
                assert db.get(Job, scene['graph_job_id']).status in {'failed', 'blocked'}
                assert not task.data.get('graph_goal_reservations_v133', {}).get('items')
                assert not task.data['workflow_runtime']['receipts']
        finally:
            release.set()
        assert query.result(timeout=30)
    assert reached == [scene['query_key']]
    with Session(engine()) as db:
        query = db.get(DataRun, scene['query_id']); call = db.get(Invocation, query_call)
        task = db.get(Task, scene['task']['id'])
        assert db.get(Job, scene['query_job_id']).status == 'succeeded'
        assert query.status == 'sufficient' and len(query.data['tool_trace']) == 1
        trace = query.data['tool_trace'][0]
        assert trace['invocation_id'] == call.id and trace['status'] == 'succeeded'
        assert call.status == 'succeeded' and call.idempotency_key == scene['query_key']
        assert call.capability_id == trace['capability_id']
        assert call.data['job_id'] == scene['query_job_id'] and call.data['role'] == 'data'
        assert call.data['inputs'] == trace['inputs']
        assert call.data['input_hash'] == trace['input_hash'] == digest(trace['inputs'])
        assert call.data['output'] == trace['output']
        assert query.data['reference_only'] is True and db.get(Plan, scene['proposal_id']).status == 'adopted'
        assert task.data['goal_spec'] == original_goal
        assert task.data['native_goal_context'] == original_context
        assert task.data['native_goal_context_hash'] == original_context_hash
        assert not task.data['workflow_runtime']['receipts']
        assert call_count(db, p, task) == 1
    assert _calls() == before | {query_call} and _ids(CloudCall) == cloud


def test_public_supporting_cancel_after_running_trace_commit_prevents_a_stale_worker_invocation(waiting_release, monkeypatch):
    from generic_agents import data_tools, gateway
    from generic_agents.goal_queries import call_count
    value = waiting_release; scene = _supporting_query(value, monkeypatch); client = value['client']
    before, cloud = _calls(), _ids(CloudCall)
    entered, release = threading.Event(), threading.Event(); paused, reached = [], []
    original_checkpoint, original_invoke = data_tools.checkpoint, gateway.invoke
    task_path = '/api/workspace/tasks/' + scene['task']['id']
    original_goal = copy.deepcopy(scene['task']['goal_spec'])
    original_context_hash = scene['task']['native_goal_context_hash']

    def pause_after_real_running_trace_checkpoint(db):
        # Inspect only the already loaded row.  In particular do not refresh the
        # worker's cached DataRun after this commit: production must do that at
        # its pre-Invocation gate when the independent cancellation wins.
        cached = next((row for row in list(db.identity_map.values())
            if isinstance(row, DataRun) and row.id == scene['query_id']), None)
        trace = cached.data.get('tool_trace', []) if cached is not None else []
        target = (not paused and cached is not None and cached.status == 'running'
            and len(trace) == 1 and trace[0].get('status') == 'running'
            and 'invocation_id' not in trace[0])
        result = original_checkpoint(db)
        if target:
            paused.append(cached.id); entered.set()
            assert release.wait(30), 'cancel race did not release the original query worker'
        return result

    def observe_actual_gateway(db, p, capability_id, agent_role, inputs, key, **kwargs):
        reached.append(key)
        return original_invoke(db, p, capability_id, agent_role, inputs, key, **kwargs)

    monkeypatch.setattr(data_tools, 'checkpoint', pause_after_real_running_trace_checkpoint)
    monkeypatch.setattr(gateway, 'invoke', observe_actual_gateway)
    with ThreadPoolExecutor(max_workers=1) as pool:
        query = pool.submit(process_once, scene['query_job_id'], deliver=False)
        try:
            assert entered.wait(30), 'query did not commit its actual running tool trace'
            assert _calls() == before and reached == []
            with Session(engine()) as db:
                row = db.get(DataRun, scene['query_id'])
                assert row.status == 'running' and len(row.data['tool_trace']) == 1
                assert row.data['tool_trace'][0]['status'] == 'running'
                assert 'invocation_id' not in row.data['tool_trace'][0]
            current = reply(client.get(task_path))
            cancel = {'request_id': str(uuid4()), 'expected_version': current['version'],
                'reason': '在原工具步骤已保存但尚未调用时明确停止本次补充查询，保留原记录'}
            cancelled = reply(client.post(task_path + '/supporting-queries/' + scene['query_id'] + '/cancel', json=cancel))
            public = next(row for row in cancelled['items'] if row['id'] == scene['query_id'])
            assert public['status'] == 'cancelled' and public['can_cancel'] is False
            with Session(engine()) as db:
                row = db.get(DataRun, scene['query_id'])
                assert row.status == 'cancelled'
                cancelled_at, cancelled_version = row.data['cancelled_at'], row.version
                assert row.data['cancel_reason'] == cancel['reason']
            assert _calls() == before
        finally:
            release.set()
        assert query.result(timeout=30)
    assert paused == [scene['query_id']] and reached == []
    assert _calls() == before, 'the cancelled cached query cannot consume a call before the final fence'
    with Session(engine()) as db:
        row = db.get(DataRun, scene['query_id'])
        assert row.status == 'cancelled' and row.version >= cancelled_version
        assert row.data['cancelled_at'] == cancelled_at and row.data['cancel_reason'] == cancel['reason']
        assert not any(step.get('invocation_id') for step in row.data.get('tool_trace', []))
    recovered = reply(client.post(task_path + '/supporting-queries/requests/recover', json={
        'operation': 'cancel', 'run_id': scene['query_id'], 'original_request': cancel,
        'action': 'probe', 'reason': '只核对刚才原取消请求，不重新执行或另建查询'}))
    assert recovered == {'status': 'saved', 'run_id': scene['query_id'], 'can_resend': False}
    p = _principal(client)
    with Session(engine()) as db:
        assert call_count(db, p, db.get(Task, scene['task']['id'])) == 0
    assert process_once(scene['graph_job_id'], deliver=False)
    with Session(engine()) as db:
        actual = list(db.scalars(select(Invocation).where(Invocation.id.not_in(before))))
        assert len(actual) == 1 and actual[0].status == 'succeeded'
        assert actual[0].idempotency_key == scene['graph_key']
        task, job = db.get(Task, scene['task']['id']), db.get(Job, scene['graph_job_id'])
        assert job.status == 'succeeded'; _assert_graph_aggregate(db, task, job, actual[0])
        assert len(task.data['workflow_runtime']['receipts']) == 1
        assert task.data['goal_spec'] == original_goal and task.data['native_goal_context_hash'] == original_context_hash
        assert call_count(db, p, task) == 1
        assert db.get(DataRun, scene['query_id']).status == 'cancelled'
    assert reached == [scene['graph_key']] and _ids(CloudCall) == cloud
