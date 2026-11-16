"""Actual PG/API, local numeric rules and a declared localhost supplier.

Conflicts come from two ordinary saved observations with the same declared
clock, real extraction and independent data ACKs. No Observation/result/ACK is
fabricated in the DB. The supplier is explicitly synthetic, not field hardware.
All workers below select an exact Job and workspace; no global queue is run.
"""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m, worker
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import password_hash
from tests.test_anomaly_goal_handoff_v253 import goal_spec
from tests.test_instance_workspace import prepared, enable, task_row
from tests.test_native_goals import define, drain
from tests.test_sensing_selection import supplier, asset, monitor
from tests.test_vertical_slice import manifest, publish


def good(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _job(s, kind, **pins):
    with Session(s.bound) as db:
        query = select(m.Job).where(m.Job.workspace_id == s.workspace_id, m.Job.kind == kind,
                                   m.Job.status == 'queued')
        for key, value in pins.items(): query = query.where(m.Job.data[key].as_string() == value)
        rows = list(db.scalars(query)); assert len(rows) == 1, [(row.id, row.status) for row in rows]
        return rows[0].id


def _run(s, identity):
    assert worker.process_once(identity, deliver=False)
    with Session(s.bound) as db:
        job = db.get(m.Job, identity)
        assert job.status == 'succeeded', {'kind': job.kind, 'status': job.status, 'error': job.data.get('error')}


def _observe(s, value, at, *, source=None, acquisition_id=None, receive=True):
    body = {'source_id': (source or s.input_source)['id'], 'source_record_id': str(uuid4()),
            'entity_id': s.entity['id'], 'observed_at': at.isoformat(), 'business_day': at.date().isoformat(),
            'attributes': {s.field: value}, 'extractor_id': s.extractor['id'],
            'startup_mode': 'automatic' if acquisition_id else 'manual'}
    if acquisition_id: body['acquisition_id'] = acquisition_id
    observation = good(s.client.post('/api/observations', json=body), 201)
    _run(s, _job(s, 'process_observation', observation_id=observation['id']))
    if receive:
        worker.deliver_events(workspace_id=s.workspace_id, observation_id=observation['id'], event_kind='observation.data')
        _assert_received(s, observation['id'])
    return observation


def _assert_received(s, observation_id):
    with Session(s.bound) as db:
        row = db.get(m.Observation, observation_id)
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + row.id))
        assert row.status == 'processed' and event.status == 'received' and event.received_at is not None
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == s.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence')))
        assert len(memories) == 1
        assert row.data['data_reception'] == event.data['data_reception'] == memories[0].data['data_reception']


def _old(s):
    with Session(s.bound) as db:
        query = db.get(m.DataRun, s.query_id); gap = db.get(m.DataGap, s.gap_id)
        plans = list(db.scalars(select(m.ObservationPlan).where(m.ObservationPlan.workspace_id == s.workspace_id,
            m.ObservationPlan.data['observation_request']['gap_id'].as_string() == gap.id)))
        return {'query': (query.version, query.status, copy.deepcopy(query.data)),
                'gap': (gap.version, gap.status, copy.deepcopy(gap.data)),
                'plans': {row.id: (row.version, row.status, copy.deepcopy(row.data)) for row in plans}}


def _inventory(s):
    with Session(s.bound) as db:
        return {cls.__name__: sorted(row.id for row in db.scalars(select(cls).where(cls.workspace_id == s.workspace_id)))
                for cls in (m.DataRun, m.DataGap, m.ObservationPlan, m.Acquisition, m.Job, m.Observation,
                            m.Invocation, m.Event, m.Memory, m.Audit, m.CloudCall)}


def build_scene(client, supplier, *, two_assets=False):
    release, _, _ = prepared(client); enable(client, release)
    kind = release['snapshot']['object_types'][1]
    numeric = next(field for field in kind['fields'] if field['label'] == '数量')
    title = next(field for field in kind['fields'] if field['label'] == '标题')
    entity = good(client.post('/api/instances/' + release['instance_id'] + '/objects', json={
        'release_id': release['id'], 'object_type': kind['key'],
        'attributes': {title['key']: 'V259声明数值冲突场景', numeric['key']: 1}}), 201)
    task, _ = task_row(client, release, entity)
    with Session(engine()) as db:
        row = db.get(m.Task, task['id']); workspace = row.workspace_id; actor = row.owner_id
        original_entity = copy.deepcopy(db.get(m.Entity, entity['id']).data)
    s = SimpleNamespace(client=client, supplier=supplier, bound=engine(), workspace_id=workspace,
        actor_id=actor, release=release, entity=entity, task=task, field=numeric['key'],
        original_entity=original_entity, goal_base='/api/workspace/tasks/' + task['id'] + '/goal')
    s.base = s.goal_base + '/conflict-observations'
    s.input_source = publish(client, manifest('source', name='declared_numeric_' + uuid4().hex[:10],
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': [], 'timezone': 'UTC',
            'observation_capability': {'fields': [s.field], 'modalities': ['numeric'], 'actions': ['observe']}},
        input_schema={'type': 'object'}, output_schema={'type': 'object'}, data_policy={'cloud_allowed': False}))
    s.extractor = publish(client, manifest('tool', name='conflict_numeric_' + uuid4().hex[:10],
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': []}, input_schema={'type': 'object'}, output_schema={'type': 'object'},
        data_policy={'cloud_allowed': False}))
    clock = m.now() - timedelta(minutes=1)
    s.original_observations = [_observe(s, value, clock) for value in (10, 20)]
    s.source = publish(client, manifest('source', name='conflict_supplier_' + uuid4().hex[:10],
        allowed_agent_roles=['perception'], execution={'adapter': 'http', 'endpoint': supplier['url'] + '/observe',
            'receipt_endpoint': supplier['url'] + '/receipt', 'side_effect': True},
        specification={'timezone': 'UTC',
            'observation_capability': {'fields': [s.field], 'modalities': ['numeric'], 'actions': ['observe']}}))
    s.assets = [asset(client, entity, s.source, fields=[s.field], latency_seconds=10)]
    if two_assets: s.assets.append(asset(client, entity, s.source, fields=[s.field], latency_seconds=20))
    s.policy = monitor(client, entity, s.source, s.assets, fields=[s.field], max_attempts=2)
    spec = goal_spec(s, [s.field], purpose='仅对已独立接收的同刻数值冲突申请明确一次复测，不裁决旧记录')
    s.defined = define(client, s.goal_base, {'request_id': str(uuid4()), 'expected_version': task['version'],
        'reason': '明确原任务数值证据范围与最终人工验收', 'goal_spec': spec})
    drain(task)
    with Session(s.bound) as db:
        current = db.get(m.Task, task['id']); s.query_id = current.data['native_goal_query_id']
        gaps = list(db.scalars(select(m.DataGap).where(m.DataGap.workspace_id == workspace,
            m.DataGap.query_id == s.query_id, m.DataGap.entity_id == entity['id'])))
        assert len(gaps) == 1 and gaps[0].status == 'needs_review'
        assert gaps[0].data['kind'] == 'conflict' and gaps[0].data['missing_fields'] == []
        assert gaps[0].data['conflict_fields'] == [s.field]
        s.gap_id = gaps[0].id
        assert not list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id == workspace)))
        assert db.get(m.DataRun, s.query_id).status == 'conflict'
    s.original = _old(s)
    return s


@pytest.fixture
def scene(client, supplier):
    return build_scene(client, supplier)


def context(s, *, client=None, gap_id=None):
    return good((client or s.client).get(s.base + '/context', params={'gap_id': gap_id or s.gap_id}))


def body_for(s, ctx=None, **changes):
    ctx = ctx or context(s)
    return {'schema': 'goal-conflict-observe-once-v259', 'request_id': str(uuid4()), 'gap_id': ctx['gap_id'],
            'context_hash': ctx['context_hash'], 'monitor_policy_id': s.policy['id'],
            'monitor_policy_version': s.policy['version'], 'confirm_new_observation': True,
            'reason': '管理员明确仅申请一次新观测，保留原冲突及独立人工验收', **changes}


def raw_for(body): return json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def post(s, body, *, client=None, raw=None):
    return (client or s.client).post(s.base, content=raw if raw is not None else raw_for(body),
                                    headers={'Content-Type': 'application/json'})


def detail(s, receipt): return good(s.client.get(s.base + '/' + receipt['id']))


def lookup(s, body, raw):
    return s.client.get(s.base + '/requests', params={'request_id': body['request_id'],
                                                    'raw_sha256': hashlib.sha256(raw.encode()).hexdigest()})


def test_context_is_complete_numeric_independently_received_and_readonly(scene):
    s = scene; before = _inventory(s); ctx = context(s)
    assert ctx['schema'] == 'goal-conflict-context-v259' and ctx['can_request']
    assert ctx['fields'] == [s.field] and ctx['old_conflict_status'] == 'needs_review'
    assert ctx['field_truth_verified'] is False and len(ctx['conflicts']) == 1
    candidates = ctx['conflicts'][0]['candidates']
    assert all(set(row) == {'observation_id', 'source_id', 'source_hash', 'source_name',
                            'invocation_id', 'value', 'receipt_hash'} for row in candidates)
    assert all(type(row['source_name']) is str and row['source_name'].strip() for row in candidates)
    assert {row['observation_id'] for row in candidates} == {row['id'] for row in s.original_observations}
    assert sorted(row['value'] for row in candidates) == [10, 20]
    assert all(type(row['value']) is int and len(row['receipt_hash']) == 64 for row in candidates)
    assert ctx['conflict_hash'] == digest({key: ctx[key] for key in ('entity_id', 'fields', 'conflicts')})
    assert ctx['context_hash'] == digest({key: value for key, value in ctx.items()
        if key not in {'monitor_policies', 'can_request', 'reason', 'context_hash'}})
    assert [row['id'] for row in ctx['monitor_policies']] == [s.policy['id']]
    assert context(s) == ctx and _inventory(s) == before and _old(s) == s.original and not s.supplier['calls']


def pending_once(s):
    body = body_for(s); raw = raw_for(body); receipt = good(post(s, body, raw=raw), 201)
    assert receipt['outcome'] == 'accepted' and receipt['original_query_id'] == s.query_id
    assert receipt['original_gap_id'] == s.gap_id and receipt['branch_query_id'] != s.query_id
    assert receipt['branch_gap_id'] != s.gap_id and receipt['observation_plan_id']
    assert _old(s) == s.original and not s.supplier['calls']
    with Session(s.bound) as db:
        parent = db.get(m.Task, s.task['id']); original = db.get(m.DataRun, s.query_id)
        branch = db.get(m.DataRun, receipt['branch_query_id']); gap = db.get(m.DataGap, receipt['branch_gap_id'])
        plan = db.get(m.ObservationPlan, receipt['observation_plan_id'])
        assert parent.data['native_goal_query_id'] == branch.id and gap.query_id == branch.id
        assert branch.data['request'] == original.data['request']
        assert branch.data['goal_delegation'] == original.data['goal_delegation']
        assert plan.data['observation_request']['gap_id'] == gap.id
    waiting = detail(s, receipt)['execution']
    assert waiting['state'] == 'awaiting_dispatch' and waiting['device_requests_limit'] == 1
    assert waiting['received_observation_ids'] == [] and waiting['old_conflict_resolved'] is False
    acquisition_id = waiting['acquisition_id']; assert acquisition_id
    _run(s, _job(s, 'dispatch_acquisition', acquisition_id=acquisition_id))
    assert len(s.supplier['calls']) == 1
    observation = _observe(s, 30, m.now(), source=s.source, acquisition_id=acquisition_id, receive=False)
    pending = detail(s, receipt)['execution']
    assert pending['observation_ids'] == [observation['id']] and pending['received_observation_ids'] == []
    assert pending['state'] != 'received' and not good(s.client.get(s.goal_base))['acceptance']
    return receipt, observation


def acknowledge_and_settle(s, observation):
    worker.deliver_events(workspace_id=s.workspace_id, observation_id=observation['id'], event_kind='observation.data')
    _assert_received(s, observation['id'])
    with Session(s.bound) as db:
        settling = list(db.scalars(select(m.Job.id).where(m.Job.workspace_id == s.workspace_id,
            m.Job.kind == 'settle_received_acquisition_v245', m.Job.status == 'queued',
            m.Job.data['settlement']['observation_id'].as_string() == observation['id'])))
    for identity in settling: _run(s, identity)


def received_once(s):
    receipt, observation = pending_once(s)
    acknowledge_and_settle(s, observation)
    drain(s.task)
    received = detail(s, receipt)['execution']
    assert received['state'] == 'received' and received['received_observation_ids'] == [observation['id']]
    assert received['plan_status'] == 'completed' and received['acquisition_status'] == 'succeeded'
    assert received['old_conflict_resolved'] is received['goal_acceptance_automatic'] is False
    assert not good(s.client.get(s.goal_base))['acceptance']
    assert _old(s) == s.original and len(s.supplier['calls']) == 1
    with Session(s.bound) as db:
        assert db.get(m.Entity, s.entity['id']).data == s.original_entity
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id)))
        assert not list(db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace_id,
            m.Job.kind == 'recheck_data_gap', m.Job.data['gap_id'].as_string() == s.gap_id)))
    return receipt, observation


def test_ack_after_explicit_goal_stop_only_closes_original_action_without_reviving_goal(scene):
    s = scene; receipt, observation = pending_once(s)
    current = good(s.client.get(s.goal_base))
    stopped = good(s.client.post(s.goal_base + '/stop', json={
        'request_id': str(uuid4()), 'expected_version': current['version'],
        'reason': '停止原Goal协调，保留已发设备请求并只接收其迟到原结果'}))
    assert stopped['cancelled'] and not stopped['acceptance']
    with Session(s.bound) as db:
        task = db.get(m.Task, s.task['id'])
        stopped_task = (task.version, task.status, copy.deepcopy(task.data))
        row = db.get(m.Observation, observation['id']); invocation_id = row.data['invocation_id']
        call = db.get(m.Invocation, invocation_id)
        original_call = (call.data['input_hash'], call.status, copy.deepcopy(call.data))
        invocation_ids = list(db.scalars(select(m.Invocation.id).where(m.Invocation.workspace_id == s.workspace_id)))
        assert db.get(m.DataGap, receipt['branch_gap_id']).status == 'cancelled'
    acknowledge_and_settle(s, observation)
    drain(s.task)  # Any already queued Goal check must skip an ended parent.
    progress = detail(s, receipt)['execution']
    assert progress['state'] == 'received' and progress['acquisition_status'] == 'succeeded'
    assert progress['plan_status'] in {'completed', 'cancelled'}
    assert progress['received_observation_ids'] == [observation['id']]
    assert progress['old_conflict_resolved'] is progress['goal_acceptance_automatic'] is False
    after = good(s.client.get(s.goal_base))
    assert after['cancelled'] == stopped['cancelled'] and not after['acceptance']
    with Session(s.bound) as db:
        task = db.get(m.Task, s.task['id']); call = db.get(m.Invocation, invocation_id)
        assert (task.version, task.status, task.data) == stopped_task
        assert (call.data['input_hash'], call.status, call.data) == original_call
        assert set(db.scalars(select(m.Invocation.id).where(m.Invocation.workspace_id == s.workspace_id))) == set(invocation_ids)
        assert db.get(m.DataGap, receipt['branch_gap_id']).status == 'cancelled'
    assert _old(s) == s.original and len(s.supplier['calls']) == 1


def test_explicit_once_forks_real_query_and_gap_then_waits_for_independent_ack(scene):
    s = scene; receipt, observation = received_once(s)
    # latest retains the original time-window semantics. A new reading is not
    # a verdict about the earlier same-clock conflict or automatic acceptance.
    goal = good(s.client.get(s.goal_base))
    assert goal['check']['evidence']['status'] == 'conflict'
    assert goal['check']['evidence']['conflicts']
    assert not goal['check']['evaluation']['ready_for_acceptance'] and not goal['acceptance']
    assert observation['id'] in {row['observation_id'] for row in goal['check']['evidence']['observation_evidence']}
    with Session(s.bound) as db:
        assert db.get(m.DataRun, receipt['branch_query_id']).status == 'conflict'
        assert db.get(m.DataGap, receipt['branch_gap_id']).status == 'needs_review'
    assert _old(s) == s.original


def test_finished_acquisition_with_missing_new_ack_is_blocked_integrity_not_awaiting_dispatch(scene):
    s = scene; receipt, observation = received_once(s)
    with Session(s.bound) as db:
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.idempotency_key == 'data:' + observation['id']))
        memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == s.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence'))
        assert memory is not None; identifier = memory.id; db.delete(memory); db.commit()
    before = _inventory(s)
    result = detail(s, receipt)
    assert result['receipt'] == receipt
    assert result['execution']['state'] == 'blocked_integrity' and result['execution']['reason']
    assert result['execution']['received_observation_ids'] == []
    assert result['execution']['acquisition_status'] == 'succeeded'
    assert result['execution']['goal_acceptance_automatic'] is False
    assert _inventory(s) == before and len(s.supplier['calls']) == 1
    with Session(s.bound) as db:
        assert db.get(m.Memory, identifier) is None


def test_original_raw_recovery_does_not_repeat_effect_or_allow_another_task(scene):
    s = scene; body = body_for(s, request_id='原同刻冲突/只复测一次?259'); raw = raw_for(body)
    receipt = good(post(s, body, raw=raw), 201); before = _inventory(s)
    assert good(post(s, body, raw=raw), 201) == receipt
    recovered = good(lookup(s, body, raw))
    assert recovered['found'] and recovered['outcome'] == 'accepted' and recovered['original_raw'] == raw
    assert recovered['receipt'] == receipt and _inventory(s) == before
    assert post(s, {**body, 'reason': body['reason'] + '另一正文'}).status_code == 409
    other, _ = task_row(s.client, s.release, s.entity)
    wrong = s.client.post('/api/workspace/tasks/' + other['id'] + '/goal/conflict-observations',
                          content=raw, headers={'Content-Type': 'application/json'})
    assert wrong.status_code == 409 and not s.supplier['calls']
    unknown_body = {**body, 'request_id': '未曾发送的新原号/259'}
    unknown = good(lookup(s, unknown_body, raw_for(unknown_body)))
    assert unknown['found'] is False and unknown['receipt'] is None


@contextmanager
def another_admin(s):
    name = 'conflict_admin_' + uuid4().hex[:12]; password = uuid4().hex
    with Session(s.bound) as db:
        actor = m.User(username=name, password_hash=password_hash(password)); db.add(actor); db.flush()
        db.add(m.Membership(workspace_id=s.workspace_id, user_id=actor.id, role='admin')); db.commit()
    with TestClient(s.client.app) as client:
        session = good(client.post('/api/login', json={'username': name, 'password': password}))
        client.headers.update({'X-CSRF-Token': session['csrf_token'], 'X-Workspace-ID': s.workspace_id})
        yield client


def test_two_real_admins_and_distinct_nonces_accept_only_one_original_conflict(scene):
    s = scene
    with another_admin(s) as other:
        first = body_for(s); second = body_for(s, context(s, client=other)); barrier = Barrier(3)
        def submit(client, body):
            barrier.wait(timeout=10); return post(s, body, client=client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(submit, s.client, first); b = pool.submit(submit, other, second)
            barrier.wait(timeout=10); results = [a.result(timeout=20), b.result(timeout=20)]
        assert sorted(response.status_code for response in results) == [201, 409], [response.text for response in results]
    with Session(s.bound) as db:
        plans = list(db.scalars(select(m.ObservationPlan).where(m.ObservationPlan.workspace_id == s.workspace_id)))
        acquisitions = list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id == s.workspace_id)))
        assert len(plans) == len(acquisitions) == 1
    assert _old(s) == s.original and not s.supplier['calls']


def test_history_recovers_original_receipts_by_actor_without_raw_or_legacy_side_effects(scene):
    s = scene; body = body_for(s); accepted = good(post(s, body), 201)
    another_body = {**body, 'request_id': str(uuid4()), 'reason': '另号只核对同一冲突，不产生第二次采集'}
    rejected = good(post(s, another_body), 409)['detail']['receipt']
    before = _inventory(s)
    page = good(s.client.get(s.base + '/history', params={'limit': 1}))
    assert set(page) == {'schema', 'workspace_id', 'actor_id', 'task_id', 'before_id',
                         'items', 'has_more', 'next_cursor'}
    assert page['schema'] == 'goal-conflict-observation-history-v259'
    assert page['workspace_id'] == s.workspace_id and page['actor_id'] == s.actor_id
    assert page['task_id'] == s.task['id'] and page['before_id'] is None
    assert page['has_more'] and len(page['items']) == 1 and page['next_cursor'] == page['items'][-1]['id']
    next_page = good(s.client.get(s.base + '/history', params={'limit': 1, 'before_id': page['next_cursor']}))
    assert next_page['before_id'] == page['next_cursor'] and not next_page['has_more']
    assert next_page['next_cursor'] is None and len(next_page['items']) == 1
    rows = page['items'] + next_page['items']
    assert {row['id'] for row in rows} == {accepted['id'], rejected['id']}
    assert all(row == (accepted if row['id'] == accepted['id'] else rejected) for row in rows)
    assert all('raw' not in row and 'original_raw' not in row for row in rows)
    assert _inventory(s) == before
    with another_admin(s) as other:
        private_before = _inventory(s)
        private = good(other.get(s.base + '/history'))
        assert private['items'] == [] and not private['has_more'] and private['next_cursor'] is None
        assert _inventory(s) == private_before
    legacy, _ = task_row(s.client, s.release, s.entity)
    legacy_before = _inventory(s)
    no_records = good(s.client.get('/api/workspace/tasks/' + legacy['id'] + '/goal/conflict-observations/history'))
    assert no_records['items'] == [] and no_records['next_cursor'] is None
    assert _inventory(s) == legacy_before
    assert _old(s) == s.original and not s.supplier['calls']


def test_malformed_or_unconfirmed_original_never_creates_a_branch(scene):
    s = scene; body = body_for(s); before = _inventory(s)
    malformed = [raw_for({**body, 'extra': True}), raw_for({**body, 'confirm_new_observation': False}),
                 raw_for({**body, 'monitor_policy_version': True}),
                 raw_for({**body, 'reason': '\0'}), raw_for({**body, 'gap_id': uuid4().hex})]
    malformed.append(raw_for(body)[:-1] + ',"reason":"重复键"}')
    malformed.append(raw_for(body).replace('"monitor_policy_version":' + str(body['monitor_policy_version']),
                                         '"monitor_policy_version":NaN'))
    for raw in malformed:
        assert post(s, body, raw=raw).status_code == 422
    assert _inventory(s) == before and _old(s) == s.original and not s.supplier['calls']


@pytest.mark.parametrize('changed,code', [('context_hash', 'context_changed'), ('monitor_policy_version', 'policy_changed')])
def test_stale_scope_rejection_is_durable_and_does_not_turn_into_later_admission(scene, changed, code):
    s = scene; body = body_for(s)
    body[changed] = '0' * 64 if changed == 'context_hash' else body[changed] + 1
    raw = raw_for(body); response = post(s, body, raw=raw)
    rejected = good(response, 409)['detail']
    assert rejected['found'] and rejected['outcome'] == 'rejected' and rejected['original_raw'] == raw
    assert rejected['receipt']['rejection']['code'] == code
    before = _inventory(s)
    assert good(lookup(s, body, raw)) == rejected
    assert good(post(s, body, raw=raw), 409)['detail'] == rejected
    assert _inventory(s) == before and _old(s) == s.original and not s.supplier['calls']


def test_missing_original_independent_ack_is_not_recreated_by_an_explicit_request(scene):
    s = scene; body = body_for(s)
    with Session(s.bound) as db:
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.idempotency_key == 'data:' + s.original_observations[0]['id']))
        memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == s.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence'))
        assert memory is not None; identifier = memory.id; db.delete(memory); db.commit()
    assert post(s, body).status_code == 409
    with Session(s.bound) as db:
        assert db.get(m.Memory, identifier) is None
        assert not list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id == s.workspace_id)))
    assert not s.supplier['calls']


def test_terminal_supplier_rejection_cannot_automatically_select_a_second_acquisition(client, supplier):
    s = build_scene(client, supplier, two_assets=True)
    supplier['modes'][s.assets[0]['external_id']] = 'rejected'
    receipt = good(post(s, body_for(s)), 201); view = detail(s, receipt)['execution']
    job_id = _job(s, 'dispatch_acquisition', acquisition_id=view['acquisition_id'])
    assert worker.process_once(job_id, deliver=False)
    with Session(s.bound) as db:
        rows = list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id == s.workspace_id,
            m.Acquisition.data['observation_plan_id'].as_string() == receipt['observation_plan_id'])))
        assert len(rows) == 1 and rows[0].id == view['acquisition_id']
        action = rows[0]; job = db.get(m.Job, job_id)
        assert job.status == 'failed' and action.status == 'rejected'
        proof = action.data['receipt']; chosen = db.get(m.SensorAsset, action.data['sensor_asset_id'])
        assert proof['status'] == 'rejected' and proof['terminal'] is True
        assert proof['no_future_observation'] is True and proof['request_key'] == action.idempotency_key
        assert proof['asset_external_id'] == chosen.external_id and proof['receipt_id']
        assert db.get(m.ObservationPlan, receipt['observation_plan_id']).status == 'escalated'
        dispatch_ids = list(db.scalars(select(m.Job.id).where(m.Job.workspace_id == s.workspace_id,
            m.Job.kind == 'dispatch_acquisition')))
        assert dispatch_ids == [job_id]
    assert len(supplier['calls']) == 1 and _old(s) == s.original
    failed = detail(s, receipt)['execution']
    assert failed['state'] == 'failed' and failed['goal_acceptance_automatic'] is False


def test_missing_command_audit_blocks_recovery_and_never_recreates_acceptance(scene):
    s = scene; body = body_for(s); raw = raw_for(body); receipt = good(post(s, body, raw=raw), 201)
    with Session(s.bound) as db:
        audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
                                                      m.Audit.object_id == receipt['id'])))
        assert audits
        for audit in audits: db.delete(audit)
        db.commit()
    before = _inventory(s)
    assert lookup(s, body, raw).status_code == 409 and post(s, body, raw=raw).status_code == 409
    assert _inventory(s) == before and not s.supplier['calls']
