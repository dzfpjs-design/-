"""Actual local report -> authorized graph Task -> original formal Goal.

Root alone runs these tests on its owned disposable PostgreSQL.  The inherited
document/provider transport fixture is synthetic; source publication, reviewed
configuration, graph adoption, routing consent, Events, Jobs, Invocations and
formal acceptance are all real production paths.  Fault seams only pause or
abort an unchanged production operation; they never fabricate its authority.
"""
import copy
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

# Initialize the real application before importing router-owned models.  This
# follows the established route-test import order, rather than bypassing setup.
from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (
    Audit, Capability, CloudCall, DomainInstance, Entity, Event, Invocation, Job,
    Memory, Observation, PerceptionSignal, Task, now,
)
from generic_agents.worker import deliver_events, process_once
from tests.test_graph_wait_journey_v118 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime,
    full_source_scene, reply, human, poll,
)
from tests.test_full_semantic_instance_journey_v113 import create_object, generate
from tests.test_graph_event_pipeline import observe, pending_intents, delivery_job
from tests.test_instance_workspace import save
from tests.test_vertical_slice import publish
from tests.test_business_tools import service
from tests.test_workflow_graph_goal_journey_v126 import _check, _acceptance


BASE = '/api/workspace/instances/{identity}/signal-graph-routing-v134'
HUMAN_NAMES = ('V134原异常人工响应', 'V134原异常独立核查')
HUMAN_ROLES = ('V134响应责任人', 'V134核查责任人')


def wire(response, status=200):
    """Read the real canonical envelope, without replacing its signed text."""
    assert response.status_code == status, response.text
    envelope = response.json()
    assert set(envelope) == {'schema', 'value', 'canonical'}
    assert envelope['schema'] == 'report-full-semantic-graph-canonical-wire-v1'
    assert json.loads(envelope['canonical']) == envelope['value']
    return envelope['value']


def path(value):
    return BASE.format(identity=value['release']['instance_id'])


def second_object(value):
    """Create a distinct public business reference without changing its schema."""
    release = value['release']; original = value['object']
    reference = 'v134_second_' + uuid4().hex
    command = {'release_id': release['id'], 'object_type': original['entity_type'],
        'reference': reference, 'attributes': copy.deepcopy(original['attributes']),
        'request_id': 'v134_object_' + uuid4().hex}
    endpoint = '/api/instances/' + release['instance_id'] + '/objects'
    created = reply(value['client'].post(endpoint, json=command), status=201)
    assert created['id'] != original['id']
    assert created['attributes'] == original['attributes']
    assert reply(value['client'].post(endpoint, json=command), status=201)['id'] == created['id']
    with Session(engine()) as db:
        actual = db.get(Entity, created['id'])
        assert actual.external_id == reference and actual.entity_type == original['entity_type']
        assert actual.workspace_id == value['workspace_id'] and actual.data == original['attributes']
    return created


def tasks(value):
    with Session(engine()) as db:
        return list(db.scalars(select(Task.id).where(
            Task.workspace_id == value['workspace_id'],
            Task.data['instance_id'].as_string() == value['release']['instance_id'])))


def effects():
    """Exact before/after inventory; no ignored Job or Invocation families."""
    with Session(engine()) as db:
        return {model.__tablename__: {row.id: digest({'status': getattr(row, 'status', None),
            'version': row.version, 'data': row.data}) for row in db.scalars(select(model))}
            for model in (Task, Job, Invocation, CloudCall, Memory)}


def _graph_revision(value, configuration, local_cap):
    """Re-consent the exact graph after the real automation scope edit."""
    client = value['client']; old = value['adopt_body']; adoption_path = value['adopt_path']
    parent = reply(client.get(adoption_path + '/requests/' + old['request_id']))['receipt']
    current = reply(client.get('/api/setup/draft'))
    command = copy.deepcopy({key: item for key, item in old.items()
        if key not in {'request_id', 'preview_hash', 'consent', 'semantic_mode'}})
    command.update(schema='report-full-semantic-graph-revision-preview-command-v4',
        parent={'id': parent['id'], 'receipt_hash': parent['receipt_hash']},
        configuration_revision=current['revision'], configuration_hash=digest(configuration))
    schema = command['graph']['input_schema']
    schema['properties']['route_numeric'] = {'title': '本次明确保留的核对数值',
        'type': 'number', 'minimum': -0.0}
    schema['required'].append('route_numeric')
    node = next(row for row in command['graph']['nodes'] if row['kind'] == 'ability')
    with Session(engine()) as db:
        cap = db.get(Capability, local_cap['id'])
        node['execution'].update(capability_id=cap.id, capability_hash=cap.content_hash,
            release_version=cap.release_version, input_schema=copy.deepcopy(cap.data['input_schema']),
            output_schema=copy.deepcopy(cap.data['output_schema']), adapter=cap.data['execution']['adapter'],
            max_attempts=cap.data['budget'].get('max_attempts', 2))
    preview = reply(client.post(adoption_path + '/revisions/preview', json=command))
    accepted_body = {**command, 'schema': 'report-full-semantic-graph-revision-command-v4',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True,
        'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(adoption_path + '/revisions/adopt', json=accepted_body))
    assert accepted['receipt']['parent_id'] == parent['id']
    return accepted_body


@pytest.fixture
def routing_scene(waiting_release):
    """Actual mixed release: two usable human businesses plus the source graph.

    The original expensive document/ledger baseline is reused by waiting_release.
    Only a real configuration save/review, successor graph consent and release
    generation are repeated; no issued release or successful receipt is edited.
    """
    value = waiting_release; client = value['client']
    current = reply(client.get('/api/setup/draft'))
    configuration = copy.deepcopy(current['configuration'])
    business_name = value['adopt_body']['business_name']
    policy = configuration['automation']['policies'][business_name]
    policy['triggers'] = ['人工发起', '异常事件']
    policy['rules']['data']['mode'] = '自动运行'
    for index, (name, role) in enumerate(zip(HUMAN_NAMES, HUMAN_ROLES)):
        configuration['businesses'].append(name)
        flow = {'trigger': '人工发起异常核查', 'output': '保存原异常的人工判断',
            'steps': [{'id': 'v134_human_' + str(index), 'name': name,
                'purpose': '对照原登记，由责任人核查，不认定现场事实',
                'agent': '人工确认', 'role': role, 'output': '人工核查意见',
                'sourceId': '', 'citation': '本合成工作区明确采用的人工核查流程',
                'nextId': 'end', 'condition': '', 'elseId': 'end'}],
            'confirmedAt': now().isoformat()}
        # This is the ordinary public flow-confirmation contract, never a
        # replacement signature for a source-derived or already-issued flow.
        from generic_agents.instance_compiler import signature_value
        flow['confirmedSignature'] = json.dumps(signature_value(configuration, flow), ensure_ascii=False)
        configuration['flows'][name] = flow
        configuration['automation']['policies'][name] = {
            'triggers': ['人工发起'], 'rules': {'write': {'mode': '仅给出建议'}}}
    save(client, configuration, review_automation=True)
    current = reply(client.get('/api/setup/draft'))
    # The inherited manual-only fixture deliberately left cloud_allowed absent.
    # Publish a new explicit local contract and really re-adopt that pin. Never
    # patch the old capability or pretend an unchanged graph is a new revision.
    old_node = next(row for row in value['adopt_body']['graph']['nodes'] if row['kind'] == 'ability')
    with Session(engine()) as db:
        manifest = copy.deepcopy(db.get(Capability, old_node['execution']['capability_id']).data)
    manifest.update(name='v134_local_graph_' + uuid4().hex[:12],
        data_policy={'cloud_allowed': False, 'external_allowed': False})
    local_cap = publish(client, manifest)
    adopted = _graph_revision(value, current['configuration'], local_cap)
    current = reply(client.get('/api/setup/draft'))
    me = reply(client.get('/api/me'))
    compilation = copy.deepcopy(value['compilation'])
    compilation.update(revision=current['revision'],
        role_bindings={**adopted['role_bindings'], **dict.fromkeys(HUMAN_ROLES, me['user_id'])})
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert not checked['general_issues'], checked['general_issues']
    release = generate(client, compilation, checked, instance_id=value['release']['instance_id'])
    available = [row for row in release['snapshot']['businesses'] if row['available']]
    assert {row['name'] for row in available} == {business_name, *HUMAN_NAMES}
    assert all(next(row for row in available if row['name'] == name)['execution_mode'] ==
        'confirmed_human_workflow' for name in HUMAN_NAMES)
    instance = next(row for row in reply(client.get('/api/instances')) if row['id'] == release['instance_id'])
    reply(client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    result = {**value, 'release': release, 'adopt_body': adopted,
        'workspace_id': me['workspace_id'], 'actor_id': me['user_id'], 'compilation': compilation}
    result['business_id'] = next(row['id'] for row in available if row['name'] == business_name)
    result['human_business_ids'] = [next(row['id'] for row in available if row['name'] == name)
        for name in HUMAN_NAMES]
    return result


def preview_body(value, *, objects=None):
    selected = objects or [value['object']]
    release = value['release']; obj = selected[0]
    kind = next(row for row in release['snapshot']['object_types'] if row['key'] == obj['entity_type'])
    field = next(row['key'] for row in kind['fields'] if row['key'] in obj['attributes'])
    node = next(row for row in value['adopt_body']['graph']['nodes'] if row['kind'] == 'ability')
    criteria = [{'id': 'enough', 'kind': 'evidence_sufficient', 'description': '原对象资料充分'},
        {'id': 'human', 'kind': 'human_confirmation', 'description': '按原目标由人验收'},
        {'id': 'called', 'kind': 'capability_success', 'capability_id': node['execution']['capability_id'],
            'description': '仅本任务的真实本地统计回执'}]
    goal = {'purpose': '核对原登记异常和已采用完整图，结果仍为待核候选',
        'entity_ids': [row['id'] for row in selected], 'required_fields': [field],
        'criteria': criteria, 'subgoals': [{'id': 'original', 'description': '核对原对象与实际图回执',
            'criterion_ids': [row['id'] for row in criteria]}],
        'evidence': {'source': 'archive', 'minimum_verification': 'recorded',
            'time': {'mode': 'archive', 'timezone': 'Asia/Shanghai'}},
        'observation_mode': 'existing_only', 'max_steps': 12, 'max_tool_calls': 12,
        'stop_on_conflict': True, 'deadline': None}
    instance = next(row for row in reply(value['client'].get('/api/instances'))
        if row['id'] == release['instance_id'])
    return {'schema': 'signal-graph-routing-preview-command-v134',
        'expected_version': instance['version'], 'release_id': release['id'],
        'source_id': value['source']['id'], 'extractor_id': value['extractor']['id'],
        'entity_ids': [row['id'] for row in selected], 'business_id': value['business_id'],
        'entity_binding': 'each_trigger_object', 'entity_input_fields': ['watched_entity'],
        'graph_inputs': {'route_numeric': -0.0}, 'goal_spec': goal, 'max_tasks': 10,
        'reason': '仅授权随后新登记的原对象异常发起已显示本机完整图，不判定现场事实'}


def enable(value, body=None):
    body = preview_body(value) if body is None else copy.deepcopy(body)
    preview = wire(value['client'].post(path(value) + '/preview', json=body))
    command = {**copy.deepcopy(preview['command']), 'schema': 'signal-graph-routing-enable-command-v134',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'],
        'confirm_graph_execution': True}
    accepted = wire(value['client'].post(path(value) + '/enable', json=command), 201)
    return preview, command, accepted


def report(value, *, obj=None, record=None, processing_version='1.0.0', process=True):
    client = value['client']; obj = obj or value['object']; at = now() - timedelta(seconds=1)
    with Session(engine()) as db:
        timezone = db.get(Capability, value['source']['id']).data['specification'].get('timezone', 'UTC')
    body = {'release_id': value['release']['id'], 'source_id': value['source']['id'],
        'source_record_id': record or str(uuid4()), 'processing_version': processing_version,
        'entity_id': obj['id'], 'extractor_id': value['extractor']['id'],
        'attributes': {'temperature': 105}, 'observed_at': at.isoformat(),
        'business_day': at.astimezone(ZoneInfo(timezone)).date().isoformat(), 'startup_mode': 'manual'}
    observation = reply(client.post('/api/workspace/instances/' + value['release']['instance_id'] +
        '/reported-observations', json=body), status=201)
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.workspace_id == value['workspace_id'],
            Job.data['observation_id'].as_string() == observation['id'], Job.kind == 'process_reported_observation'))
        assert job is not None
        identity = job.id
    if process:
        assert process_once(identity, kinds=['process_reported_observation'], deliver=False)
        with Session(engine()) as db:
            assert db.get(Job, identity).status == 'succeeded'
            assert db.get(Observation, observation['id']).status == 'processed'
            event = db.scalar(select(Event).where(Event.workspace_id == value['workspace_id'],
                Event.idempotency_key == 'anomaly:' + observation['id']))
            assert event is not None and event.status == 'received'
            signal = db.scalar(select(PerceptionSignal).where(PerceptionSignal.event_id == event.id))
            assert signal is not None and signal.data['basis']['observation_id'] == observation['id']
            signal_id = signal.id
        return observation, body, identity, signal_id
    return observation, body, identity, None


def deliveries(value):
    from generic_agents.signal_graph_routing_models_v134 import SignalGraphDelivery
    with Session(engine()) as db:
        return [{'id': row.id, 'signal_id': row.signal_id, 'task_id': row.task_id,
            'status': row.status, 'source_identity_hash': row.source_identity_hash,
            'data': copy.deepcopy(row.data)} for row in db.scalars(select(SignalGraphDelivery).where(
                SignalGraphDelivery.workspace_id == value['workspace_id'],
                SignalGraphDelivery.instance_id == value['release']['instance_id']))]


def dispatch(value, identity):
    from generic_agents.signal_graph_routing_v134 import dispatch as actual_dispatch
    with Session(engine()) as db:
        actual_dispatch(db, value['workspace_id'], identity)
        db.commit()


def deliver(value, signal_id):
    row = next(row for row in deliveries(value) if row['signal_id'] == signal_id)
    dispatch(value, row['id'])
    return next(item for item in deliveries(value) if item['id'] == row['id'])


def task_detail(value, identity):
    return reply(value['client'].get('/api/workspace/tasks/' + identity))


def pause(value, enabled):
    listing = wire(value['client'].get(path(value)))
    route_id = enabled['receipt']['route_id']
    body = {'schema': 'signal-graph-routing-pause-command-v134',
        'request_id': str(uuid4()), 'expected_version': listing['instance_version'],
        'reason': '明确停止本授权的新发起与未来自动执行，保留所有原记录'}
    response = wire(value['client'].post(path(value) + '/' + route_id + '/pause', json=body))
    return body, response


def _cancel_task(value, task):
    current = task_detail(value, task['id'])
    body = {'request_id': str(uuid4()), 'version': current['graph_runtime']['version'],
        'cancel_epoch': current['graph_runtime']['cancel_epoch'], 'reason': '停止原图，保留登记与原任务历史'}
    return reply(value['client'].post('/api/workspace/tasks/' + task['id'] + '/graph/cancel', json=body))


def _signals(value):
    return reply(value['client'].get('/api/workspace/instances/' + value['release']['instance_id'] + '/signals'))['records']


def _admit(value, observation_id):
    candidates = pending_intents(observation_id)
    identity = next(identity for identity, producer in candidates if producer == value['producer']['id'])
    job, _ = delivery_job(identity)
    assert process_once(job, deliver=False)
    with Session(engine()) as db:
        assert db.get(Job, job).status == 'succeeded'
    return identity


def negative_zero(value):
    assert type(value) is float and value == 0 and math.copysign(1.0, value) == -1.0


def run_signal_graph_journey(value):
    """Return actual JSON-safe public proof for Root's later restart capture."""
    client = value['client']; obj = value['object']; before_entities = {}
    with Session(engine()) as db:
        cloud_before = set(db.scalars(select(CloudCall.id)))
        for row in db.scalars(select(Entity)):
            before_entities[row.id] = digest({'version': row.version, 'data': row.data})
    options = wire(client.get(path(value) + '/options'))
    assert any(row['id'] == value['business_id'] for row in options['businesses'])
    preview, command, enabled = enable(value)
    negative_zero(command['graph_inputs']['route_numeric'])
    negative_zero(preview['command']['graph_inputs']['route_numeric'])
    for expanded in preview['expanded']:
        negative_zero(expanded['graph_inputs']['route_numeric'])
    original_receipt = copy.deepcopy(enabled['receipt'])
    recovered_http = client.get(path(value) + '/requests/' + command['request_id'])
    assert wire(recovered_http)['result']['receipt'] == original_receipt
    # Receipt hashes remain server-owned. The original full command is bound
    # through the actual domain hash, retaining its signed negative-zero token.
    from generic_agents.signal_graph_routing_v134 import _hash
    from generic_agents.security import Principal
    assert original_receipt['command_hash'] == _hash(Principal(value['actor_id'], value['workspace_id'],
        'viewer', 'read-only-v134-command-check'), value['release']['instance_id'], 'enable', None, command)
    assert wire(client.post(path(value) + '/enable', json=command), 201)['receipt'] == original_receipt
    observation, original_body, processing_job, signal_id = report(value)
    delivery = deliver(value, signal_id)
    assert delivery['status'] == 'created' and delivery['task_id']
    negative_zero(delivery['data']['immutable']['body']['graph_inputs']['route_numeric'])
    assert delivery['data']['immutable_hash'] == digest(delivery['data']['immutable'])
    task = task_detail(value, delivery['task_id'])
    assert task['execution_mode'] == 'confirmed_graph_workflow'
    assert task['entity_ids'] == [obj['id']]
    assert task['graph_runtime']['inputs']['watched_entity'] == obj['id']
    negative_zero(task['graph_runtime']['inputs']['route_numeric'])
    assert task['goal_spec']['entity_ids'] == [obj['id']]
    assert task['goal_spec']['observation_mode'] == 'existing_only'
    assert task['signal_graph_origin_v134']
    route = next(row for row in wire(client.get(path(value)))['routes']
        if row['id'] == original_receipt['route_id'])
    assert task['goal_spec']['deadline'] == route['expires_at']
    with Session(engine()) as db:
        row = db.get(Task, task['id'])
        assert row.owner_id == value['actor_id']
        assert row.data['native_goal_context']['goal_snapshot']['spec'] == row.data['goal_spec']
        definition = row.data['native_goal_check_job_pin']['job_id']
        original_origin = copy.deepcopy(row.data['signal_graph_origin_v134'])
        graph_job = next(iter(row.data['graph_jobs'].values()))['job_id']
        negative_zero(row.data['graph_runtime']['inputs']['route_numeric'])
        instance = db.get(DomainInstance, value['release']['instance_id'])
        grant = next(entry['grant'] for entry in instance.data['signal_graph_routes_v134']['routes']
            if entry['grant']['id'] == original_receipt['route_id'])
        negative_zero(grant['command']['graph_inputs']['route_numeric'])
        assert digest(grant['command']) == digest(command)
        negative_zero(grant['binding']['input_metadata']['input_schema']['properties']['route_numeric']['minimum'])
    # The trigger receipt precedes the future wait. It must not be recycled to
    # finish a window that the original Task has not yet opened.
    _admit(value, observation['id'])
    assert process_once(graph_job, deliver=False)
    with Session(engine()) as db:
        job = db.get(Job, graph_job); assert job.status == 'succeeded'
        call = db.scalar(select(Invocation).where(Invocation.idempotency_key == job.data['invocation_key']))
        assert call.status == 'succeeded' and call.data['job_id'] == job.id
        invocation_id = call.id
        assert call.data['output']['provenance'] == [{'entity_id': obj['id'], 'version': obj['version']}]
    task = task_detail(value, task['id']); assert not task['completed']
    assert not task.get('native_goal_acceptance')
    task, first_human = human(client, task, 'a')
    window = next(iter(task['graph_wait_authority']['windows'].values()))
    assert window['status'] == 'open'
    later, later_body, later_job = observe(client, value, temperature=28)
    later_intent = _admit(value, later['id'])
    task = poll(client, task['id'])
    assert next(iter(task['graph_wait_authority']['windows'].values()))['status'] == 'consumed'
    task, final_human = human(client, task, 'c')
    assert task['completed'] and task['status'] == 'workflow_completed'
    goal, check_command = _check(value, task)
    assert goal['check']['evaluation']['ready_for_acceptance']
    assert goal['acceptance'] is None
    acceptance = _acceptance(value, {'task': task, 'goal': goal})
    accepted = reply(client.post(acceptance['path'], json=acceptance['body']))
    assert accepted['acceptance']['evaluation']['completed']
    assert accepted['acceptance']['physical_state_verified'] is False
    assert accepted['acceptance']['learning_auto_published'] is False
    assert reply(client.post(acceptance['path'], json=acceptance['body']))['acceptance'] == accepted['acceptance']
    exported = reply(client.get('/api/workspace/tasks/' + task['id'] + '/goal/export'))
    assert exported['goal']['acceptance'] == accepted['acceptance']
    assert exported['physical_state_verified'] is False and not exported['learning_auto_published']
    assert len(tasks(value)) == 1
    signal = next(row for row in _signals(value) if row['id'] == signal_id)
    assert not signal.get('business_review'), 'graph completion is not a human anomaly verdict'
    with Session(engine()) as db:
        persisted = db.get(Task, task['id'])
        assert persisted.data['signal_graph_origin_v134'] == original_origin
        assert db.get(Invocation, invocation_id).status == 'succeeded'
        for key in ('native_goal_data_receipt_id',):
            result = db.get(Memory, persisted.data[key])
            assert result.status == 'candidate' and result.data['auto_publish'] is False
            assert result.data['entity_state_updated'] is False
        for identity, signature in before_entities.items():
            entity = db.get(Entity, identity)
            assert digest({'version': entity.version, 'data': entity.data}) == signature
        assert set(db.scalars(select(CloudCall.id))) == cloud_before
    assert wire(client.get(path(value) + '/requests/' + command['request_id']))['result']['receipt'] == original_receipt
    return {'schema': 'actual-signal-graph-routing-journey-v134',
        'instance_id': value['release']['instance_id'], 'release_id': value['release']['id'],
        'task_id': task['id'], 'signal_id': signal_id, 'delivery_id': delivery['id'],
        'grant_command': command, 'grant_receipt': original_receipt,
        'observation': {'id': observation['id'], 'body': original_body, 'job_id': processing_job},
        'later_observation': {'id': later['id'], 'body': later_body, 'job_id': later_job, 'intent_id': later_intent},
        'graph_job_id': graph_job, 'invocation_id': invocation_id, 'goal_definition_job_id': definition,
        'human_commands': [first_human, final_human], 'check': check_command,
        'acceptance': {**acceptance, 'response': accepted}, 'export': exported,
        'source_time_verified': False, 'physical_state_verified': False, 'cloud_calls_added': 0}


def test_real_report_starts_full_graph_and_only_original_formal_goal_can_accept(routing_scene):
    proof = run_signal_graph_journey(routing_scene)
    assert proof['acceptance']['response']['acceptance']['request_id'] == proof['acceptance']['body']['request_id']


def test_each_trigger_object_expands_only_explicit_fields_and_preserves_original_nonce(routing_scene):
    value = routing_scene; client = value['client']
    second = second_object(value)
    body = preview_body(value, objects=[value['object'], second])
    preview, command, enabled = enable(value, body)
    assert preview['preview_hash'] == command['preview_hash']
    _, _, _, signal_id = report(value, obj=second)
    row = deliver(value, signal_id); assert row['status'] == 'created'
    task = task_detail(value, row['task_id'])
    assert task['entity_ids'] == task['goal_spec']['entity_ids'] == [second['id']]
    assert task['graph_runtime']['inputs']['watched_entity'] == second['id']
    negative_zero(task['graph_runtime']['inputs']['route_numeric'])
    assert task['graph_runtime']['inputs']['object_records'][0]['entity_id'] == second['id']
    before = effects()
    wrong = {**command, 'reason': '不能用同一请求改变原授权'}
    assert client.post(path(value) + '/enable', json=wrong).status_code == 409
    assert effects() == before
    assert wire(client.get(path(value) + '/requests/' + command['request_id']))['result']['receipt'] == enabled['receipt']


def test_raw_family_reprocessing_cancellation_and_new_grant_cannot_create_another_task(routing_scene):
    value = routing_scene; client = value['client']; _, _, enabled = enable(value)
    observation, original, _, signal_id = report(value)
    first = deliver(value, signal_id); assert first['status'] == 'created'
    cancelled = _cancel_task(value, task_detail(value, first['task_id']))
    assert cancelled['status'] == 'workflow_cancelled'
    with Session(engine()) as db:
        event = db.scalar(select(Event).where(Event.idempotency_key == 'anomaly:' + observation['id']))
        event.status = 'pending_delivery'; db.commit()
    deliver_events(workspace_id=value['workspace_id'], observation_id=observation['id'])
    dispatch(value, first['id'])
    assert tasks(value) == [first['task_id']]
    _, _, _, again_signal = report(value, record=original['source_record_id'], processing_version='v134-again')
    from generic_agents.signal_graph_routing_v134 import deliver_pending
    deliver_pending(value['workspace_id'])
    assert tasks(value) == [first['task_id']]
    assert len(deliveries(value)) == 1
    # A fresh explicit grant is not a license to treat an old registration as new.
    pause(value, enabled); enable(value)
    _, _, _, third_signal = report(value, record=original['source_record_id'], processing_version='v134-third')
    deliver_pending(value['workspace_id'])
    assert tasks(value) == [first['task_id']] and len(deliveries(value)) == 1
    # The genuine manual handoff endpoint must see the independent SQL history,
    # even if the current Signal is a different processing-version receipt.
    fresh = next(row for row in _signals(value) if row['id'] == third_signal)
    before = effects()
    denied = client.post('/api/workspace/instances/' + value['release']['instance_id'] + '/tasks', json={
        'release_id': value['release']['id'], 'business_id': value['human_business_ids'][0],
        'entity_ids': [value['object']['id']], 'title': '不能把已取消原家族再次交接',
        'request_id': str(uuid4()), 'signal_id': fresh['id'], 'signal_version': fresh['version'],
        'signal_basis_hash': fresh['basis_hash']})
    assert denied.status_code == 409, denied.text
    assert effects() == before and tasks(value) == [first['task_id']]


def _old_path(value):
    return '/api/workspace/instances/' + value['release']['instance_id'] + '/signal-routing'


def _old_enable_body(value, *, dual=False):
    endpoint = _old_path(value); client = value['client']
    options = reply(client.get(endpoint + ('/dual-options' if dual else '/options')))
    assert {row['id'] for row in options['businesses']} >= set(value['human_business_ids'])
    body = {'request_id': str(uuid4()), 'expected_version': options['instance_version'],
        'options_hash': options['options_hash'], 'release_id': value['release']['id'],
        'source_id': value['source']['id'], 'extractor_id': value['extractor']['id'],
        'entity_ids': [value['object']['id']], 'max_tasks': 10,
        'reason': '只创建真实人工异常待办，不启动图能力', 'confirm_human_review_only': True}
    if dual:
        body.update(response_business_id=value['human_business_ids'][0],
            verification_business_id=value['human_business_ids'][1])
    else:
        body['business_id'] = value['human_business_ids'][0]
    return body


def _old_pause(value, enabled, *, dual=False):
    endpoint = _old_path(value); client = value['client']
    listing = reply(client.get(endpoint + ('/dual' if dual else '')))
    body = {'request_id': str(uuid4()), 'expected_version': listing['instance_version'],
        'reason': '明确暂停旧人工路由，保留原人工作业'}
    route_id = enabled['receipt']['route_id']
    return reply(client.post(endpoint + ('/dual' if dual else '') + '/' + route_id + '/pause', json=body))


def _graph_enable_blocked_by_existing_route(value):
    body = preview_body(value); before = effects()
    # Preview itself may remain readable during another grant. The mutating
    # enable must never win; if preview blocks, the later positive uses exactly
    # the same valid template after the real conflicting route is paused.
    response = value['client'].post(path(value) + '/preview', json=body)
    if response.status_code == 200:
        candidate = wire(response)
        command = {**body, 'schema': 'signal-graph-routing-enable-command-v134',
            'request_id': str(uuid4()), 'preview_hash': candidate['preview_hash'],
            'confirm_graph_execution': True}
        response = value['client'].post(path(value) + '/enable', json=command)
    assert response.status_code == 409, response.text
    assert effects() == before
    assert not any(route['status'] == 'active' for route in wire(value['client'].get(path(value)))['routes'])


def test_actual_single_dual_and_graph_grants_are_symmetric_and_old_human_tasks_still_work(routing_scene):
    value = routing_scene; client = value['client']; old_path = _old_path(value)
    from generic_agents import signal_routing, signal_dual_handoffs
    from generic_agents.signal_dual_models import SignalDualHandoff
    # Both old families have genuinely usable confirmed human businesses in
    # this same release. A 409 from a graph-only fixture would not prove this.
    single_body = _old_enable_body(value)
    single = reply(client.post(old_path + '/enable', json=single_body), status=201)
    observation, _, _, signal_id = report(value)
    with Session(engine()) as db:
        signal_routing.dispatch(db, value['workspace_id'], signal_id); db.commit()
    original_signal = next(row for row in _signals(value) if row['id'] == signal_id)
    single_task = original_signal['route_receipt']['task_id']
    assert original_signal['route_receipt']['status'] == 'created'
    assert task_detail(value, single_task)['execution_mode'] == 'confirmed_human_workflow'
    assert len(tasks(value)) == 1
    _graph_enable_blocked_by_existing_route(value)
    _old_pause(value, single)

    _, _, graph_enabled = enable(value)
    before = effects()
    assert client.post(old_path + '/enable', json=_old_enable_body(value)).status_code == 409
    assert client.post(old_path + '/enable-dual', json=_old_enable_body(value, dual=True)).status_code == 409
    assert effects() == before and tasks(value) == [single_task]
    pause(value, graph_enabled)

    dual_body = _old_enable_body(value, dual=True)
    dual = reply(client.post(old_path + '/enable-dual', json=dual_body), status=201)
    dual_observation, _, _, dual_signal_id = report(value)
    with Session(engine()) as db:
        parent = db.scalar(select(SignalDualHandoff).where(
            SignalDualHandoff.workspace_id == value['workspace_id'],
            SignalDualHandoff.observation_id == dual_observation['id']))
        assert parent is not None
        parent_id = parent.id
    for slot in ('response', 'verification'):
        with Session(engine()) as db:
            signal_dual_handoffs.dispatch_slot(db, value['workspace_id'], parent_id, slot); db.commit()
    with Session(engine()) as db:
        parent = db.get(SignalDualHandoff, parent_id)
        assert parent.response_task_id and parent.verification_task_id
        assert parent.response_task_id != parent.verification_task_id
        human_ids = {single_task, parent.response_task_id, parent.verification_task_id}
    assert set(tasks(value)) == human_ids and not deliveries(value)
    _graph_enable_blocked_by_existing_route(value)
    _old_pause(value, dual, dual=True)
    # Genuine human history must also prevent the graph family from reusing
    # either original source after a fresh explicit grant.
    enable(value)
    for old_observation in (observation, dual_observation):
        with Session(engine()) as db:
            old = db.get(Observation, old_observation['id'])
            record = old.source_record_id
        report(value, record=record, processing_version='v134-human-family-reprocess')
    from generic_agents.signal_graph_routing_v134 import deliver_pending
    deliver_pending(value['workspace_id'])
    assert set(tasks(value)) == human_ids
    assert not any(row['status'] == 'created' for row in deliveries(value))


def test_original_registration_before_grant_never_becomes_a_new_trigger_by_reprocessing(routing_scene):
    value = routing_scene; client = value['client']
    observation, original, job, _ = report(value, process=False)
    enable(value)
    assert process_once(job, kinds=['process_reported_observation'], deliver=False)
    from generic_agents.signal_graph_routing_v134 import deliver_pending
    deliver_pending(value['workspace_id'])
    assert not deliveries(value) and not tasks(value)
    # Reprocessing is not a new registration after the grant.
    report(value, record=original['source_record_id'], processing_version='v134-old-again')
    deliver_pending(value['workspace_id'])
    assert not deliveries(value) and not tasks(value)


def test_template_cannot_replace_system_inputs_foreign_entities_or_expand_goal(routing_scene):
    value = routing_scene; client = value['client']; template = preview_body(value)
    second = second_object(value)
    candidates = []
    for update in (
        {'graph_inputs': {**template['graph_inputs'], 'entity_ids': [second['id']]}},
        {'entity_input_fields': ['object_records']},
        {'entity_input_fields': [], 'graph_inputs': {**template['graph_inputs'], 'watched_entity': second['id']}},
        {'graph_inputs': {**template['graph_inputs'], 'watched_entity': second['id']}},
    ):
        candidates.append({**copy.deepcopy(template), **update})
    for goal_update in ({'entity_ids': [value['object']['id'], second['id']]},
                        {'observation_mode': 'allow_new'},
                        {'deadline': (now() + timedelta(hours=1)).isoformat()},
                        {'max_tool_calls': 0, 'criteria': [template['goal_spec']['criteria'][0]]}):
        item = copy.deepcopy(template); item['goal_spec'].update(goal_update)
        candidates.append(item)
    before = effects()
    for candidate in candidates:
        response = client.post(path(value) + '/preview', json=candidate)
        assert response.status_code in {409, 422}, response.text
        assert effects() == before
    assert not deliveries(value) and not tasks(value)


def test_pause_at_real_final_dispatch_blocks_io_but_retains_original_requests_and_draft(routing_scene, monkeypatch):
    from generic_agents import workflow_graph_scope
    value = routing_scene; _, command, enabled = enable(value)
    _, _, _, signal_id = report(value); row = deliver(value, signal_id)
    task = task_detail(value, row['task_id']); task, _ = human(value['client'], task, 'a', draft=True)
    graph_job = next(iter(task['graph_jobs'].values()))['job_id']
    original = workflow_graph_scope.before_dispatch; reached = []; pauses = []
    with Session(engine()) as db: before = set(db.scalars(select(Invocation.id)))

    def pause_then_real_fence(db, p, key, cap):
        # The actual gateway already committed its running Invocation. The
        # real pause API must win before the unchanged final authority fence.
        reached.append(key); pauses.append(pause(value, enabled))
        return original(db, p, key, cap)

    monkeypatch.setattr(workflow_graph_scope, 'before_dispatch', pause_then_real_fence)
    assert process_once(graph_job, deliver=False)
    assert len(reached) == 1
    with Session(engine()) as db:
        calls = list(db.scalars(select(Invocation).where(Invocation.id.not_in(before))))
        assert len(calls) == 1 and calls[0].status != 'succeeded' and 'output' not in calls[0].data
        stored = db.get(Task, task['id'])
        assert not stored.data['workflow_runtime']['receipts']
        assert all('dispatch_authorization' not in item for item in stored.data['graph_jobs'].values())
    restored = task_detail(value, task['id'])
    assert restored['graph_pending_humans'][0]['draft'] == {'ok': True}
    assert wire(value['client'].get(path(value) + '/requests/' + command['request_id']))['result']['receipt'] == enabled['receipt']
    pause_body, paused = pauses[0]
    assert wire(value['client'].get(path(value) + '/requests/' + pause_body['request_id']))['result']['receipt'] == paused['receipt']
    unchanged = effects()
    assert wire(value['client'].post(path(value) + '/enable', json=command), 201)['receipt'] == enabled['receipt']
    assert effects() == unchanged


def test_actual_result_returned_after_pause_is_history_without_graph_or_goal_progress(routing_scene, monkeypatch):
    from generic_agents import gateway
    value = routing_scene; _, _, enabled = enable(value)
    _, _, _, signal_id = report(value); row = deliver(value, signal_id)
    task = task_detail(value, row['task_id']); job = next(iter(task['graph_jobs'].values()))['job_id']
    actual = gateway.invoke; returned = []

    def actual_call_then_pause(*args, **kwargs):
        call = actual(*args, **kwargs)
        assert call.status == 'succeeded'; returned.append(call.id)
        pause(value, enabled)
        return call

    monkeypatch.setattr(gateway, 'invoke', actual_call_then_pause)
    assert process_once(job, deliver=False); assert len(returned) == 1
    current = task_detail(value, task['id'])
    assert not current['completed'] and not current.get('native_goal_acceptance')
    assert not current['workflow_runtime']['receipts']
    assert not any(item['node_id'] == 'c' for item in current['graph_runtime']['activations'].values())
    with Session(engine()) as db:
        call = db.get(Invocation, returned[0]); assert call.status == 'succeeded' and call.data['output']
    # Historical success/UNKNOWN recovery cannot dispatch another call.
    before = effects()
    from generic_agents.signal_graph_routing_v134 import deliver_pending
    deliver_pending(value['workspace_id'])
    assert effects() == before


def test_two_actual_delivery_transactions_and_task_limit_cannot_double_start(routing_scene):
    value = routing_scene; body = preview_body(value); body['max_tasks'] = 1
    enable(value, body)
    _, _, _, signal_id = report(value)
    reservation = next(row for row in deliveries(value) if row['signal_id'] == signal_id)
    before = effects(); ready = threading.Barrier(2)

    def worker():
        ready.wait(timeout=20)
        dispatch(value, reservation['id'])

    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = [pool.submit(worker) for _ in range(2)]
        for run in runs: run.result(timeout=45)
    rows = deliveries(value)
    assert len(rows) == 1 and rows[0]['status'] == 'created'
    identity = rows[0]['task_id']; assert tasks(value) == [identity]
    task = task_detail(value, identity); assert len(task['graph_jobs']) == 1
    with Session(engine()) as db:
        graph_jobs = list(db.scalars(select(Job).where(Job.workspace_id == value['workspace_id'],
            Job.kind == 'execute_full_semantic_graph_node', Job.data['task_id'].as_string() == identity)))
        assert len(graph_jobs) == 1 and graph_jobs[0].status == 'queued'
    # Neither dispatcher runs a capability inline, and another new raw source
    # cannot slip past the one-slot reservation once its Task is created.
    assert effects()['invocations'] == before['invocations']
    report(value)
    from generic_agents.signal_graph_routing_v134 import deliver_pending
    deliver_pending(value['workspace_id'])
    assert tasks(value) == [identity]
    assert len([row for row in deliveries(value) if row['status'] == 'created']) == 1


def test_fault_after_actual_task_goal_and_first_jobs_rolls_back_the_entire_handoff(routing_scene, monkeypatch):
    from fastapi import HTTPException
    from generic_agents import workspace_api
    value = routing_scene; enable(value)
    _, _, _, signal_id = report(value)
    before = effects(); actual_create = workspace_api.create_task; transient = []

    def actual_create_then_abort(*args, **kwargs):
        task = actual_create(*args, **kwargs)
        db = args[3]
        assert task.data['native_goal_context'] and task.data['signal_graph_origin_v134']
        assert task.data['graph_jobs']
        jobs = list(db.scalars(select(Job.id).where(Job.data['task_id'].as_string() == task.id)))
        assert jobs
        transient.append((task.id, jobs))
        raise HTTPException(409, 'V134 explicit fault after real graph creation, before delivery commit')

    monkeypatch.setattr(workspace_api, 'create_task', actual_create_then_abort)
    result = deliver(value, signal_id)
    assert transient and result['status'] == 'blocked' and result['task_id'] is None
    assert effects() == before and not tasks(value)
    with Session(engine()) as db:
        for task_id, job_ids in transient:
            assert db.get(Task, task_id) is None
            assert all(db.get(Job, identity) is None for identity in job_ids)
        signal = db.get(PerceptionSignal, signal_id)
        assert signal.data['task_ids'] == []
        assert db.get(Event, signal.event_id).status == 'received'
    # A recorded failure is terminal; background reconciliation does not create
    # another Task after the injected failure is removed.
    monkeypatch.setattr(workspace_api, 'create_task', actual_create)
    dispatch(value, result['id'])
    assert effects() == before and not tasks(value)


def _hidden_successor(value, cap, *, external):
    """Actually adopt and generate a future-node change, never patch a release."""
    from tests.test_report_full_semantic_graph_binding_v3 import edge, ref
    client = value['client']; endpoint = value['adopt_path']
    previous = reply(client.get(endpoint + '/revisions/requests/' + value['adopt_body']['request_id']))['receipt']
    current = reply(client.get('/api/setup/draft'))
    body = copy.deepcopy({key: item for key, item in value['adopt_body'].items()
        if key not in {'request_id', 'preview_hash', 'consent', 'semantic_mode'}})
    body.update(schema='report-full-semantic-graph-revision-preview-command-v4',
        parent={'id': previous['id'], 'receipt_hash': previous['receipt_hash']},
        configuration_revision=current['revision'], configuration_hash=digest(current['configuration']))
    graph = body['graph']; original = next(row for row in graph['nodes'] if row['id'] == 'b')
    extra = copy.deepcopy(original); extra.update(id='future_hidden', source_step_id=None)
    with Session(engine()) as db:
        actual = db.get(Capability, cap['id'])
        extra['execution'].update(capability_id=actual.id, capability_hash=actual.content_hash,
            release_version=actual.release_version, adapter=actual.data['execution']['adapter'],
            input_schema=copy.deepcopy(actual.data['input_schema']), output_schema=copy.deepcopy(actual.data['output_schema']),
            max_attempts=actual.data['budget'].get('max_attempts', 2))
    graph['nodes'].append(extra)
    # Explicitly preserve the source terminal step and place this extra node on
    # the decision's other branch: it is not present in the initial frontier.
    next(row for row in graph['edges'] if row['id'] == 'd_e')['to'] = extra['id']
    graph['edges'].append(edge('hidden_e', extra['id'], 'e'))
    owner = next(row for row in body['owner_bindings'] if row['owner_path'] == '/global_claims/0')
    owner['reason'] = '本合成负控明确采用判断另一分支的未来能力，以验证自动路由必须核完整图，而不是只核初始节点'
    next(row for row in owner['clauses'] if row['source_field'] == 'statement')['targets'].append(ref('node', extra['id']))
    body['node_actions'][extra['id']] = 'data'
    if external: body['external_node_ids'].append(extra['id'])
    response = client.post(endpoint + '/revisions/preview', json=body)
    if not external:
        assert response.status_code == 409, response.text
        return None
    prepared = reply(response)
    accepted_body = {**body, 'schema': 'report-full-semantic-graph-revision-command-v4',
        'request_id': str(uuid4()), 'preview_hash': prepared['preview_hash'],
        'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    reply(client.post(endpoint + '/revisions/adopt', json=accepted_body))
    current = reply(client.get('/api/setup/draft')); compilation = copy.deepcopy(value['compilation'])
    compilation['revision'] = current['revision']
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert not checked['general_issues'], checked['general_issues']
    release = generate(client, compilation, checked, instance_id=value['release']['instance_id'])
    instance = next(row for row in reply(client.get('/api/instances')) if row['id'] == release['instance_id'])
    reply(client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    return {**value, 'release': release, 'adopt_body': accepted_body}


def test_full_graph_scan_rejects_future_external_or_side_effect_nodes_without_dispatch(routing_scene, service):
    value = routing_scene; client = value['client']
    enable(value)
    _, _, _, signal_id = report(value)
    delivery = deliver(value, signal_id)
    old_task = task_detail(value, delivery['task_id'])
    old_job = next(iter(old_task['graph_jobs'].values()))['job_id']
    ability = next(row for row in value['adopt_body']['graph']['nodes'] if row['kind'] == 'ability')
    with Session(engine()) as db:
        manifest = copy.deepcopy(db.get(Capability, ability['execution']['capability_id']).data)
    # Publishing uses the actual explicitly allowlisted loopback connector
    # fixture. Its endpoint must never receive a call from route preview.
    manifest.update(name='v134_hidden_http_' + uuid4().hex[:12],
        execution={'adapter': 'http', 'endpoint': service['http']},
        data_policy={'cloud_allowed': False, 'external_allowed': True})
    external = publish(client, manifest)
    with Session(engine()) as db:
        unsafe_manifest = copy.deepcopy(db.get(Capability, ability['execution']['capability_id']).data)
    unsafe_manifest.update(name='v134_hidden_write_' + uuid4().hex[:12],
        execution={**unsafe_manifest['execution'], 'side_effect': True})
    side_effect = publish(client, unsafe_manifest)
    before = effects()
    assert _hidden_successor(value, side_effect, external=False) is None
    assert effects() == before
    changed = _hidden_successor(value, external, external=True)
    # The replacement is a real new published release. The existing grant and
    # Task retain their original release; neither silently follows the new pin.
    with Session(engine()) as db:
        before_calls = set(db.scalars(select(Invocation.id).where(
            Invocation.idempotency_key.like('graph:' + old_task['id'] + ':%'))))
    assert process_once(old_job, deliver=False)
    with Session(engine()) as db:
        assert set(db.scalars(select(Invocation.id).where(
            Invocation.idempotency_key.like('graph:' + old_task['id'] + ':%')))) == before_calls
        original = db.get(Task, old_task['id'])
        assert original.data['instance_release_id'] == value['release']['id']
        assert not original.data['workflow_runtime']['receipts']
    options = wire(client.get(path(changed) + '/options'))
    business = next(row for row in options['businesses'] if row['id'] == changed['business_id'])
    assert business['available'] is False and business['reason']
    before = effects()
    assert client.post(path(changed) + '/preview', json=preview_body(changed)).status_code == 409
    assert effects() == before and tasks(value) == [old_task['id']]
    assert len(deliveries(value)) == 1
    assert service['calls'] == 0


@pytest.mark.parametrize('condition', ['source_retired', 'actor_revoked', 'expired_clock'])
def test_frozen_pending_handoff_requires_current_source_actor_and_unexpired_grant(routing_scene, monkeypatch, condition):
    from generic_agents import signal_graph_routing_v134 as routing
    from generic_agents.models import Membership
    value = routing_scene; _, command, enabled = enable(value)
    _, _, _, signal_id = report(value)
    original = next(row for row in deliveries(value) if row['signal_id'] == signal_id)
    assert original['status'] == 'reserved'
    if condition == 'source_retired':
        with Session(engine()) as db:
            db.get(Capability, value['source']['id']).status = 'retired'; db.commit()
    elif condition == 'actor_revoked':
        with Session(engine()) as db:
            member = db.scalar(select(Membership).where(Membership.workspace_id == value['workspace_id'],
                Membership.user_id == value['actor_id']))
            member.role = 'viewer'; db.commit()
    else:
        actual_clock = routing.clock
        # A controlled future-clock seam proves the expiry branch, not an
        # assertion that a real server ran for 24 hours or changed its clock.
        monkeypatch.setattr(routing, 'clock', lambda db: actual_clock(db) + timedelta(hours=25))
    before = effects()
    row = deliver(value, signal_id)
    assert row['status'] == 'blocked' and row['task_id'] is None
    assert effects() == before and not tasks(value)
    assert wire(value['client'].get(path(value) + '/requests/' + command['request_id']))['result']['receipt'] == enabled['receipt']
    routing.deliver_pending(value['workspace_id'])
    assert effects() == before


def test_erasing_json_quota_inventory_does_not_refund_actual_sql_reservation(routing_scene):
    value = routing_scene; body = preview_body(value); body['max_tasks'] = 1
    _, _, enabled = enable(value, body)
    _, _, _, signal_id = report(value)
    created = deliver(value, signal_id); assert created['status'] == 'created'
    task = task_detail(value, created['task_id']); job = next(iter(task['graph_jobs'].values()))['job_id']
    route_id = enabled['receipt']['route_id']
    with Session(engine()) as db:
        instance = db.get(DomainInstance, value['release']['instance_id'])
        original = copy.deepcopy(instance.data)
        changed = copy.deepcopy(original)
        entry = next(row for row in changed['signal_graph_routes_v134']['routes'] if row['grant']['id'] == route_id)
        assert entry['delivery_ids'] == [created['id']]
        entry['delivery_ids'] = []
        instance.data = changed
        audit_ids = set(db.scalars(select(Audit.id)))
        graph_calls = set(db.scalars(select(Invocation.id).where(
            Invocation.idempotency_key.like('graph:' + task['id'] + ':%'))))
        db.commit()
    # Source processing/notification remains real and independently saved, but
    # a corrupt counter may neither reserve a second Task nor dispatch the old.
    report(value)
    from generic_agents.signal_graph_routing_v134 import deliver_pending
    deliver_pending(value['workspace_id'])
    assert process_once(job, deliver=False)
    assert tasks(value) == [created['task_id']] and len(deliveries(value)) == 1
    with Session(engine()) as db:
        assert set(db.scalars(select(Invocation.id).where(
            Invocation.idempotency_key.like('graph:' + task['id'] + ':%')))) == graph_calls
        assert set(db.scalars(select(Audit.id))) >= audit_ids
        # Restore only our deliberately corrupted JSON inventory from its
        # untouched original value; never backfill/re-sign a real lost ledger.
        instance = db.get(DomainInstance, value['release']['instance_id'])
        restored = copy.deepcopy(instance.data)
        entry = next(row for row in restored['signal_graph_routes_v134']['routes'] if row['grant']['id'] == route_id)
        source = next(row for row in original['signal_graph_routes_v134']['routes'] if row['grant']['id'] == route_id)
        assert entry['grant'] == source['grant']
        entry['delivery_ids'] = copy.deepcopy(source['delivery_ids'])
        instance.data = restored; db.commit()
    assert task_detail(value, created['task_id'])['signal_graph_origin_v134'] == task['signal_graph_origin_v134']
    deliver_pending(value['workspace_id'])
    assert tasks(value) == [created['task_id']] and len(deliveries(value)) == 1
