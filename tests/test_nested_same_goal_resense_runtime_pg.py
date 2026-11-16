"""Exercise recursive same-Task resense with the real weather simulator and ACK."""
from contextlib import ExitStack
from datetime import datetime, timedelta
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import acquisition_ack_settlement_v245 as settlement
from generic_agents import device_gateway_sources_v270 as sources
from generic_agents import instance_data_receipts, models as m, worker
from generic_agents import workflow_service_poll_v195 as poll
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.instance_compiler import identifier
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import (
    command, configure_two_stage_policy, operator,
)
from tests.test_instance_workspace import object_row
from tests.test_nested_async_managed_source_pg import (
    BASE, OBJECTS, WEATHER_FIELDS, confirmed_catalogue, enum_scene, fence,
    full_source_scene, local_runtime, managed, nonce, read, reading_scene, reply, scene,
)
from tests.test_nested_same_goal_resense_pg import (
    test_recursive_publication_binds_same_task_reader_output_instead_of_gap_entry as _publish_recursive,
)
from tests.test_sensing_selection import monitor
from tests.test_vertical_slice import explicit_goal

GRAPH = 'execute_full_semantic_graph_node'
PROCESS = 'process_http_mapped_observation_v177'


def _job(w, kind, **fields):
    with Session(engine()) as db:
        rows = list(db.scalars(select(m.Job.id).where(m.Job.workspace_id == w, m.Job.kind == kind,
            *[m.Job.data[key].as_string() == value for key, value in fields.items()])))
        assert len(rows) == 1, (kind, fields, rows)
        return rows[0]


def _run(w, identity, kind):
    assert worker.process_once(identity, kinds=[kind], workspace_id=w, deliver=False)
    with Session(engine()) as db:
        return db.get(m.Job, identity).status


def _plan_status(plan):
    last = (plan.data.get('history') or [{}])[-1]
    return {'status': plan.status, 'escalation': plan.data.get('escalation_code'),
        'seconds_left': int((datetime.fromisoformat(plan.data['deadline']) - m.now()).total_seconds()),
        'last_kind': last.get('kind'), 'last_reason': str(last.get('reason', ''))[:180]}


def _issue(s, release, business):
    client = s.client
    i = release['instance_id']
    current = next(row for row in reply(client.get('/api/instances')) if row['id'] == i)
    reply(client.post('/api/instances/' + i + '/activate', json={'version': current['version'],
        'release_id': release['id'], 'business_ids': [business], 'confirm_workflow_scope': True}))
    environment = reply(client.post('/api/workspace/simulation-v176/environments', json={
        'request_id': nonce(), 'instance_id': i, 'release_id': release['id'],
        'note': '独立模拟环境的递归补采，不用于现场结论', 'confirm_simulation': True}), 201)
    device = reply(client.get('/api/sensing/devices/' + s.grant['sensor_device_id']))
    device = reply(client.post('/api/sensing/devices/' + device['id'] + '/approve', json={
        'expected_version': device['version'], 'reason': '核对模拟天气设备的原身份'}))
    connection = read(s)
    grant = next(row for row in connection['grants'] if row['id'] == s.grant['id'])
    declared = next(item for item in release['snapshot']['data_requirements']['businesses'] if item['id'] == business)
    extractor, = [item for item in declared['outputs'] if item['kind'] == 'asynchronous_perception']
    links = {key: {'field_key': identifier('field', label), 'unit': unit}
        for key, (label, unit) in WEATHER_FIELDS.items()}
    issued = reply(client.post(BASE + '/' + s.connection_id + '/sources', json={
        'request_id': nonce(), 'expected_version': connection['version'], 'grant_id': grant['id'],
        'grant_expected_version': grant['version'], 'environment_id': environment['id'],
        'entity_type': identifier('object', OBJECTS[0]['name']), 'links': links,
        'extractor_id': extractor['capability_id'], 'extractor_hash': extractor['capability_hash'],
        'confirm_simulation': True, 'reason': '登记当前递归实例的模拟天气来源'}))
    assert issued['simulation'] is True and issued['observation_created'] is False
    return issued['capability'], device, sorted(row['field_key'] for row in links.values())


def test_recursive_same_goal_resense_settles_original_http_and_data_ack(
        managed, enum_scene, monkeypatch, account):
    s = managed
    client, w = s.client, s.workspace_id
    prior = {row['id'] for row in reply(client.get('/api/instances'))}
    _publish_recursive(managed, enum_scene, monkeypatch, account)
    instance, = [row for row in reply(client.get('/api/instances')) if row['id'] not in prior]
    i = instance['id']
    release, = reply(client.get('/api/instances/' + i + '/releases'))
    business, = release['available_business_ids']
    source, device, fields = _issue(s, release, business)
    # V195 independently requires its explicit local connector destinations.
    configured = settings().development_connector_urls
    monkeypatch.setattr(settings(), 'development_connector_urls', ','.join(filter(None, (
        configured, s.origin + '/acquisitions', s.origin + '/acquisitions/receipts'))))
    entity = object_row(client, release)
    now = m.now()
    asset = reply(client.post('/api/sensing/assets', json={
        'source_id': source['id'], 'external_id': sources.DEVICE, 'title': '原模拟天气来源',
        'reason': '保存真实发现后的模拟设备能力范围',
        'specification': {'coverage_entity_ids': [entity['id']], 'fields': fields,
            'modalities': ['numeric'], 'actions': ['observe'], 'resolution_m': 1,
            'latency_seconds': 10, 'quote_ceiling_minor': 0, 'currency': 'CNY', 'health': 'healthy',
            'health_observed_at': now.isoformat(), 'health_valid_until': (now + timedelta(hours=1)).isoformat()}}), 201)
    reply(client.post('/api/sensing/assets/' + asset['id'] + '/device', json={
        'expected_version': asset['version'], 'device_id': device['id'],
        'device_expected_version': device['version'], 'reason': '绑定原模拟天气身份'}))
    policy = monitor(client, entity, source, [asset], fields=fields, max_attempts=1)
    with Session(engine()) as db:
        approved_hash = db.get(m.MonitorPolicy, policy['id']).data['approved_hash']
    goal = explicit_goal(entity, fields, mode='allow_new', source='observations')
    goal['purpose'] = '核对模拟气象补充资料及独立接收'
    actor_scope = SimpleNamespace(client=client, workspace=w)
    with ExitStack() as stack:
        first, first_client = operator(actor_scope, stack)
        second, second_client = operator(actor_scope, stack)
        acceptor, _ = operator(actor_scope, stack, login=False)
        configure_two_stage_policy(actor_scope, release, first, second, acceptor)
        task = reply(client.post('/api/workspace/instances/' + i + '/tasks', json={
            'request_id': str(uuid4()), 'release_id': release['id'], 'business_id': business,
            'entity_ids': [entity['id']], 'title': '递归图同任务气象补采',
            'goal_spec': goal, 'graph_inputs': {'original_material': '已登记的工程模拟资料范围',
                'policy_pin': {'policy_id': policy['id'], 'approved_hash': approved_hash}}}), 201)
        t = task['id']
        path = '/api/workspace/tasks/' + t + '/approval-v175'
        submitted = command(client, path, 'submit')
        for reviewer in (first_client, second_client):
            command(reviewer, path, 'decide', submission_hash=submitted['submission_hash'], decision='approve')
        command(client, path, 'start')
    # Deterministic exact-job integration, not a claim about scheduler ordering.
    assert _run(w, _job(w, 'check_native_business_goal', task_id=t), 'check_native_business_goal') == 'succeeded'
    reader_job = _job(w, GRAPH, task_id=t)
    assert _run(w, reader_job, GRAPH) == 'succeeded'
    with Session(engine()) as db:
        reader_call = db.scalar(select(m.Invocation).where(m.Invocation.workspace_id == w,
            m.Invocation.data['job_id'].as_string() == reader_job))
        assert reader_call is not None and reader_call.data['output']['status'] == 'ready'
        context = reader_call.data['output']['resense_context']
        assert context['scope']['task_id'] == t and context['policy']['policy_id'] == policy['id']
        assert context['gap']['observation_request']['goal_delegation']['task_id'] == t
        resense_job, = db.scalars(select(m.Job.id).where(m.Job.workspace_id == w, m.Job.kind == GRAPH,
            m.Job.data['task_id'].as_string() == t, m.Job.id != reader_job))
        before_resense = [_plan_status(plan) for plan in db.scalars(select(m.ObservationPlan).where(
            m.ObservationPlan.workspace_id == w, m.ObservationPlan.policy_id == policy['id'],
            m.ObservationPlan.data['observation_request']['gap_id'].as_string() == context['gap']['gap_id']).limit(3))]
    status = _run(w, resense_job, GRAPH)
    with Session(engine()) as db:
        call = db.scalar(select(m.Invocation).where(m.Invocation.workspace_id == w,
            m.Invocation.data['job_id'].as_string() == resense_job))
        job = db.get(m.Job, resense_job)
        transport = db.get(m.Task, t).data['graph_jobs'][job.data['activation_id']]
        assert status == 'waiting_service', {
            'status': status, 'phase': transport.get('phase'),
            'reason': str(transport.get('reason', ''))[:240],
            'invocation_status': call.status if call else None,
            'invocation_error': str(call.data.get('error', ''))[:240] if call else None,
        }
        assert call.data['inputs'] == {'context': context}
        call_id = call.id
        pin, = call.data['service_state']['operation_data']['acquisition_pins']
        a = pin['acquisition_id']
        plan_id = pin['observation_plan_id']
        before_dispatch = _plan_status(db.get(m.ObservationPlan, plan_id))
    dispatch = _job(w, 'dispatch_acquisition', acquisition_id=a)
    dispatch_status = _run(w, dispatch, 'dispatch_acquisition')
    with Session(engine()) as db:
        job = db.get(m.Job, dispatch)
        acquisition = db.get(m.Acquisition, a)
        diagnostic = job.data.get('failure_diagnostic_v289', {}).get('diagnostic', {})
        assert dispatch_status == 'succeeded', {
            'job_status': dispatch_status, 'attempts': job.attempts, 'before_resense': before_resense,
            'before_dispatch': before_dispatch, 'after_dispatch': _plan_status(db.get(m.ObservationPlan, plan_id)),
            'acquisition_status': acquisition.status,
            'acquisition_reason': str(acquisition.data.get('reason', ''))[:180],
            'error_type': diagnostic.get('error_type'), 'http_status': diagnostic.get('http_status'),
            'frames': diagnostic.get('frames', [])[-4:],
        }
        exchange = db.scalar(select(Exchange).where(Exchange.workspace_id == w, Exchange.acquisition_id == a))
        assert exchange is not None and exchange.status == 'received' and exchange.observation_id
        obs = exchange.observation_id
    assert _run(w, _job(w, PROCESS, observation_id=obs), PROCESS) == 'succeeded'
    worker.deliver_events(workspace_id=w, observation_id=obs, event_kind='observation.data')
    with Session(engine()) as db:
        observation = db.get(m.Observation, obs)
        received = instance_data_receipts.observation_receipt(db, s.p, observation)['data_reception']
        assert received['observation_id'] == obs and received['instance_id'] == i
        assert received['simulation_origin']['simulation'] is True
        assert received['simulation_origin']['field_verified'] is False
        settle, = db.scalars(select(m.Job.id).where(m.Job.workspace_id == w, m.Job.kind == settlement.KIND,
            m.Job.data['settlement']['observation_id'].as_string() == obs))
    assert _run(w, settle, settlement.KIND) == 'succeeded'
    deadline = monotonic() + 15
    while not poll.schedule_once(w, job_id=resense_job):
        assert monotonic() < deadline, 'original service receipt poll did not become due'
        sleep(.1)
    resumed_status = _run(w, resense_job, GRAPH)
    with Session(engine()) as db:
        original = db.get(m.Invocation, call_id)
        job = db.get(m.Job, resense_job)
        diagnostic = job.data.get('failure_diagnostic_v289', {}).get('diagnostic', {})
        if resumed_status != 'succeeded':
            print('resense resume job', resumed_status, 'attempts', job.attempts)
            print('resense resume error', diagnostic.get('error_type'), diagnostic.get('http_status'))
            print('resense resume invocation', original.status, 'saved output', 'output' in original.data,
                'previous status', original.data.get('service_previous_status'))
            for frame in diagnostic.get('frames', []):
                print('resense resume frame', frame['module'], frame['function'], frame['line'])
        assert resumed_status == 'succeeded'
        assert original.status == 'succeeded'
        receipt = original.data['output']['service_receipt']
        assert receipt['context_hash'] == context['context_hash']
        assert receipt['producer_invocation_id'] == context['producer']['invocation_id']
        assert receipt['task_id'] == t and receipt['gap_id'] == context['gap']['gap_id']
        assert db.get(m.Acquisition, a).data['closing_observation_id'] == obs
        assert list(db.scalars(select(m.Invocation.id).where(m.Invocation.workspace_id == w,
            m.Invocation.data['job_id'].as_string() == resense_job))) == [call_id]
