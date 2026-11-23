"""Disposable-PG helper for two independent Goal Tasks in one workspace.

The observations come from the existing loopback-only simulated supplier,
not a field device, real MQTT broker, or cloud model. No Goal, approval,
evaluation, receipt, or policy record is synthesized directly in the DB.
"""
import copy
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import goal_evaluation_append_store_v297 as append_store
from generic_agents import models as m
from generic_agents import native_goal_scoped_worker_v355 as goal_worker
from generic_agents.config import settings
from generic_agents.contracts import GoalSpec, digest
from generic_agents.db import engine
from generic_agents.gateway import validate_endpoint
from generic_agents.goal_evaluation_provider_v288 import KEY
from generic_agents.security import Principal
from generic_agents.workflow_goal_evaluation_models_v288 import GoalEvaluationV288
from tests.auto_evaluation_journey_fixture_helpers_v357 import setup_simulated_scope
from tests.test_authored_service_workflow_pg_v270 import good
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import command, configure_two_stage_policy, operator
from tests.test_device_gateway_source_pg_v270 import OBJECTS
from tests.test_runtime_documents_pg_v205 import uploaded, run, version


def setup_two_scopes(publication, stack, monkeypatch):
    """One real release/supplier; two entities, approved policies and reviewers."""
    first = setup_simulated_scope(publication)
    s = first.scene; s.workspace = s.workspace_id; client = s.client; release = first.release
    instance_id = release['instance_id']
    object_type = next(x for x in release['snapshot']['object_types'] if x['name'] == OBJECTS[0]['name'])
    second_entity = good(client.post('/api/instances/' + instance_id + '/objects', json={
        'request_id': str(uuid4()), 'release_id': release['id'], 'object_type': object_type['key'],
        'attributes': {first.fields['名称']: '第二个隔离模拟观察点'}, 'reference': 'goal-fixture-' + uuid4().hex}), 201)
    assert second_entity['id'] != first.entity['id']
    # The source/asset identity must remain the authentic granted simulated
    # device. Expand its published scope before any Task or acquisition.
    # Binding the device increments the asset version; fetch the real current
    # revision rather than reusing the pre-binding create response.
    assets = good(client.get('/api/sensing'))['assets']
    asset = next(item for item in assets if item['id'] == first.asset['id'])
    spec = copy.deepcopy(asset['data'])
    assert spec['coverage_entity_ids'] == [first.entity['id']]
    spec['coverage_entity_ids'].append(second_entity['id'])
    updated = good(client.patch('/api/sensing/assets/' + asset['id'], json={
        'expected_version': asset['version'], 'title': asset['title'], 'status': 'enabled',
        'specification': spec, 'reason': '在任务开始前登记两个隔离模拟对象的覆盖范围'}))
    assert set(updated['data']['coverage_entity_ids']) == {first.entity['id'], second_entity['id']}
    end = datetime.now(timezone.utc) + timedelta(hours=24)
    policy = good(client.post('/api/monitor-policies', json={
        'title': '第二隔离对象观测策略', 'entity_ids': [second_entity['id']],
        'source_ids': [first.source['capability']['id']], 'fields': first.required,
        'sensor_asset_ids': [asset['id']],
        'selection_requirement': {'allowed_modalities': ['numeric'], 'allowed_actions': ['observe'],
            'max_attempts': 1, 'max_total_cost_minor': 0},
        'timezone': 'Asia/Shanghai', 'max_age_seconds': publication.preview['binding']['preparation_policy']['max_age_seconds'],
        'max_acquisitions_per_day': 1, 'max_duration_seconds': 2, 'valid_until': end.isoformat()}), 201)
    good(client.post('/api/monitor-policies/' + policy['id'] + '/approve', json={
        'expected_version': policy['version'], 'reason': '仅允许第二隔离对象一次软件观测'}))
    path = '/api/sensing/monitor-policies/' + policy['id'] + '/scheduling-control'
    control = good(client.get(path, params={'instance_id': instance_id}))
    second_pin = {'policy_id': policy['id'], 'approved_hash': control['approved_hash']}
    good(client.post(path + '?instance_id=' + instance_id, json={
        'schema': 'monitor-scheduling-command-v253', 'request_id': str(uuid4()), 'instance_id': instance_id,
        'expected_version': control['control']['version'] if control['control'] else 0,
        'expected_policy_version': control['policy_version'], 'approved_hash': control['approved_hash'],
        'mode': 'goal_only', 'reason': '仅响应原任务，不启用周期采样'}), 201)
    reviewer1, reviewer1_client = operator(s, stack)
    reviewer2, reviewer2_client = operator(s, stack)
    acceptor, _ = operator(s, stack, login=False)
    configure_two_stage_policy(s, release, reviewer1, reviewer2, acceptor)
    origin = urlparse(s.origin)
    assert origin.scheme == 'http' and origin.hostname == '127.0.0.1' and origin.port
    endpoints = [s.origin + '/acquisitions', s.origin + '/acquisitions/receipts']
    monkeypatch.setattr(settings(), 'development_connector_urls', ','.join(endpoints))
    assert all(validate_endpoint({'endpoint': endpoint}) == endpoint for endpoint in endpoints)
    return SimpleNamespace(publication=publication, scoped=first, scene=s, client=client, release=release,
        reviewers=(reviewer1_client, reviewer2_client),
        targets=((first.entity, first.policy_pin), (second_entity, second_pin)))


def create_approved_task(context, target, serial):
    """Create one real task with its own document, source set and approvals."""
    from generic_agents import runtime_document_receipts as delivery
    entity, policy_pin = target; s = context.scene; client = context.client; release = context.release
    business_id = release['available_business_ids'][0]
    task = good(client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
        'request_id': str(uuid4()), 'release_id': release['id'], 'business_id': business_id,
        'entity_ids': [entity['id']], 'title': '独立隔离模拟任务 ' + str(serial),
        'graph_inputs': {'analysis_question': '取得软件模拟观测与独立数据回执，比较资料并保留不确定性。',
            'policy_pin': policy_pin}, 'goal_spec': None}), 201)
    task_id = task['id']
    raw = ('仅使用隔离软件模拟来源，为当前对象取得观测和独立接收回执，'
           '比较候选结论并保留资料不足，不认定现场事实。').encode()
    document = good(uploaded(s, release, raw, 'isolated-two-goal-' + str(serial) + '.txt')[0], 201)
    version_id = document['latest_version_id']
    assert run(s, version_id) == 'succeeded'
    delivery.consume_once(s.workspace_id)
    parsed = version(s, version_id)
    assert parsed['status'] == 'parsed' and parsed['delivery']['verified']
    blocks = good(client.get('/api/runtime-documents/versions/' + version_id + '/blocks', params={
        'attempt_id': parsed['latest_attempt_id'], 'expected_parse_hash': parsed['parse_hash']}))
    assert blocks['records'] and not blocks['has_more']
    selectors = [{'kind': 'native', 'version_id': version_id, 'attempt_id': parsed['latest_attempt_id'],
        'parse_hash': parsed['parse_hash'], 'block_id': block['id'],
        'char_span': {'start': 0, 'end': len(block['text'])}}
        for block in blocks['records'] if block['text']]
    base = '/api/runtime-task-documents/tasks/' + task_id
    current = good(client.get(base + '/context'))
    source = good(client.post(base + '/source-sets', json={'request_id': str(uuid4()),
        'expected_task_version': current['task_version'], 'expected_approval_version': current['approval_version'],
        'prior_set_id': None, 'prior_set_hash': None, 'input_key': 'runtime_document_sources',
        'selectors': selectors}), 201)
    goal = GoalSpec.model_validate({'purpose': '取得隔离模拟观测和独立收件并形成评价，不证明现场状态。',
        'entity_ids': [entity['id']], 'required_fields': context.scoped.required, 'subgoals': [{
            'id': 'received_evidence', 'description': '原观测与独立收件和不可改写评价',
            'criterion_ids': ['observations_ready', 'evaluation_created']}],
        'criteria': [{'id': 'observations_ready', 'kind': 'evidence_sufficient', 'description': '资料满足来源和时效要求'},
            {'id': 'evaluation_created', 'kind': 'capability_success',
             'capability_id': context.publication.pins['evaluation_packet']['id'], 'description': '形成独立评价资料包'}],
        'evidence': {'source': 'observations', 'time': {'mode': 'latest', 'timezone': 'Asia/Shanghai',
            'max_age_seconds': context.publication.preview['binding']['preparation_policy']['max_age_seconds']},
            'minimum_verification': 'unverified_extraction', 'minimum_records_per_entity': 1},
        'observation_mode': 'allow_new', 'deadline': None, 'max_steps': 12, 'max_tool_calls': 12,
        'stop_on_conflict': True}).model_dump(mode='json')
    approval = '/api/workspace/tasks/' + task_id + '/approval-v175'
    current = good(client.get(approval)); value = copy.deepcopy(current['value'])
    value.update(title=task['title'], note='仅隔离软件模拟，不认定现场。', goal_spec=goal)
    value['graph_inputs']['runtime_document_sources'] = source['ref']
    command(client, approval, 'revise', value=value); command(client, approval, 'submit')
    submitted = good(client.get(approval)); submission_hash = submitted['submission_hash']
    for stage, reviewer in enumerate(context.reviewers):
        state = good(reviewer.get(approval))
        assert state['current_stage'] == stage and state['submission_hash'] == submission_hash
        command(reviewer, approval, 'decide', submission_hash=submission_hash, decision='approve')
    approved = good(client.get(approval))
    assert approved['status'] == 'approved' and len(approved['decisions']) == 2
    assert len({row['actor_id'] for row in approved['decisions']}) == 2 and approved['permissions']['start']
    return SimpleNamespace(id=task_id, entity=entity, source=source, approval=approval, goal=goal)


def mature_initial_task(context, task):
    """Start one approved task and require its own completed initial head."""
    from generic_agents.http_observation_task_consumer_v270 import FAMILY, receive, tick
    from generic_agents.instance_data_receipts import observation_receipt
    from generic_agents.workflow_task_consumer_v265 import step
    client = context.client; s = context.scene; workspace = s.workspace_id; release = context.release
    with Session(engine()) as db:
        session = db.scalar(select(m.AuthSession).where(m.AuthSession.user_id == s.actor_id,
            m.AuthSession.revoked.is_(False), m.AuthSession.expires_at > func.clock_timestamp())
            .order_by(m.AuthSession.created_at.desc()))
        assert session is not None
        principal = Principal(s.actor_id, workspace, 'admin', session.id)
    command(client, task.approval, 'start')
    assert good(client.get(task.approval))['status'] == 'executing'
    after = delivery_after = None; query_pin = None
    for _ in range(40):
        with Session(engine()) as db:
            live = db.get(m.Task, task.id); assert live.workspace_id == workspace
            jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == workspace,
                m.Job.data['task_id'].as_string() == task.id)))
            activations = live.data.get('graph_runtime', {}).get('activations', {})
            reasoning = [job for job in jobs if activations.get(job.data.get('activation_id'), {}).get('node_id') == 'reasoning']
            assert all(job.status == 'queued' and job.attempts == 0 for job in reasoning)
            assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id == workspace)) == 0
            if live.data.get('native_goal_query_id'):
                query = db.get(m.DataRun, live.data['native_goal_query_id'])
                current = (query.id, query.version, digest(query.data))
                if query_pin is None: query_pin = current
                assert current == query_pin
            head = copy.deepcopy(live.data.get(KEY))
        if reasoning and head: break
        graph = None
        if not reasoning:
            graph = step(workspace, release['instance_id'], family='graph', task_id=task.id,
                after=after, delivery_after=delivery_after, limit=1)
            after, delivery_after = graph['after'], graph['delivery_after']
        observed = tick(workspace, release['instance_id'], task.id, family=FAMILY, limit=20)
        received = receive(workspace, release['instance_id'], task.id, family=FAMILY, limit=20)
        assert not (graph and graph['errors']) and not observed['blocked'] and not received.get('blocked')
        if not (graph and graph['processed']) and not observed['processed'] and not received.get('received_count'):
            time.sleep(.25)
    else:
        raise AssertionError('独立任务未形成首代评价，不能发放政策')
    for _ in range(40):
        try:
            with Session(engine()) as db:
                live = db.get(m.Task, task.id)
                selected = append_store.provider.binding(db, principal, live)
                mature = append_store._mature_head(db, principal, live, selected)
                assert mature['head']['head_hash'] == head['head_hash'] and head['generation'] == 1
                record = db.get(GoalEvaluationV288, head['evaluation_ref']['id'])
                assert record and record.task_id == task.id and record.record_hash == head['evaluation_ref']['record_hash']
                observations = list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == workspace,
                    m.Observation.entity_id == task.entity['id'])))
                received_ids = [item.id for item in observations if observation_receipt(db, principal, item)['data_reception']]
                cited = {item.get('observation_id') for item in
                    record.data['evaluation_result']['result']['observation_evidence']}
                used_received_ids = sorted(set(received_ids) & cited)
                assert used_received_ids and query_pin and live.data['native_goal_query_id'] == query_pin[0]
                assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id == workspace)) == 0
                return SimpleNamespace(task_id=task.id, entity_id=task.entity['id'], head=copy.deepcopy(head),
                    goal_hash=live.data['goal_hash'], q0=query_pin, observation_ids=used_received_ids)
        except (HTTPException, ValueError):
            goal_worker.tick(workspace, release['instance_id'], task.id)
    raise AssertionError('初代评价没有通过原成熟门禁，未发放政策')
