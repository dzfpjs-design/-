"""Actual native supplier/Invocation/Event/Memory late reception on owned PG.

No transport, current-published check, original context or receipt is patched.
Package withdrawal uses the existing legacy fixture's explicit status condition;
pause/disable/replacement/restoration use the real instance/capability APIs.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (Acquisition, Audit, Capability, CloudCall, DataGap, DataRun,
    DomainInstance, Entity, Event, InstanceRelease, Invocation, Job, Memory, Membership,
    Observation, ObservationPlan, SensorReservation, Task)
from generic_agents.original_acquisition_reception_v127 import context, hold, trigger_hold, MARKER
from generic_agents.security import Principal
from generic_agents.worker import process_once, deliver_events
from tests.test_native_goal_acquisitions import prepared, stopped, rows, receipt
from tests.test_sensing_selection import supplier, dispatch
from tests.test_vertical_slice import publish, manifest


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _principal(client):
    me = _ok(client.get('/api/me'))
    return Principal(me['user_id'], me['workspace_id'], me['role'], 'actual-original-reception')


def _instance(client, p):
    return next(row for row in _ok(client.get('/api/instances')) if row['id'] == p['r']['instance_id'])


def _two_business_prepared(client, supplier):
    """Two real human businesses in the *original* issued release, not a rewrite."""
    from generic_agents.instance_compiler import signature_value
    from tests.test_instance_workspace import configuration, prepared as compile_release, enable, object_row, task_row
    from tests.test_native_goals import define, drain
    from tests.test_sensing_selection import asset, monitor
    config = configuration(); second = '独立人工资料归档'
    config['businesses'].append(second)
    config['flows'][second] = copy.deepcopy(config['flows']['资料核验'])
    config['flows'][second]['output'] = '独立资料归档结论'
    config['automation']['policies'][second] = {'rules': {'write': {'mode': '仅给出建议'}}}
    for flow in config['flows'].values():
        flow['confirmedSignature'] = json.dumps(signature_value(config, flow), ensure_ascii=False)
    release, _, _ = compile_release(client, config)
    assert len(release['available_business_ids']) == 2
    assert all(row['execution_mode'] == 'confirmed_human_workflow' for row in release['snapshot']['businesses'])
    enable(client, release); obj = object_row(client, release); task, creation = task_row(client, release, obj)
    field = next(iter(obj['attributes']))
    source = publish(client, manifest('source', name='dual_business_original_supplier_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'http', 'endpoint': supplier['url'] + '/observe',
            'receipt_endpoint': supplier['url'] + '/receipt', 'side_effect': True},
        specification={'timezone': 'UTC', 'observation_capability': {
            'fields': [field], 'modalities': ['numeric'], 'actions': ['observe']}}))
    selected_asset = asset(client, obj, source, fields=[field])
    policy = monitor(client, obj, source, [selected_asset], fields=[field])
    spec = {'purpose': '只核对原资料业务；保留另一独立人工业务的启用状态', 'entity_ids': [obj['id']],
        'required_fields': [field], 'criteria': [
            {'id': 'enough', 'kind': 'evidence_sufficient', 'description': '原资料满足要求'},
            {'id': 'human', 'kind': 'human_confirmation', 'description': '原业务明确人工验收'}],
        'subgoals': [{'id': 'check', 'description': '核对本任务原资料', 'criterion_ids': ['enough', 'human']}],
        'evidence': {'source': 'observations', 'minimum_verification': 'confirmed',
            'time': {'mode': 'latest', 'max_age_seconds': 86400, 'timezone': 'Asia/Shanghai'}},
        'observation_mode': 'allow_new'}
    base = '/api/workspace/tasks/' + task['id'] + '/goal'
    define(client, base, {'request_id': str(uuid4()), 'expected_version': task['version'],
        'reason': '固定原业务目标；第二业务与本补采不共享执行授权', 'goal_spec': spec})
    drain(task)
    with Session(engine()) as db:
        acq = db.scalars(select(Acquisition)).one()
        plan = db.get(ObservationPlan, acq.data['observation_plan_id'])
        assert plan.data['observation_request']['goal_delegation']['task_id'] == task['id']
        identifier = acq.id
        assert db.get(Task, task['id']).data['business_id'] == creation['business_id']
    return {'r': release, 'obj': obj, 'task': task, 'base': base, 'field': field, 'source': source,
        'asset': selected_asset, 'monitor': policy, 'acquisition_id': identifier,
        'original_business_id': creation['business_id']}


def _block(client, p, kind='package_withdrawn'):
    if kind == 'package_withdrawn':
        with Session(engine()) as db:
            package = db.get(Capability, db.get(Task, p['task']['id']).package_id)
            assert package.status == 'published'; package.status = 'revoked'; db.commit()
    elif kind == 'instance_not_active':
        current = _instance(client, p)
        _ok(client.post('/api/instances/' + current['id'] + '/pause', json={'version': current['version']}))
    elif kind == 'business_disabled':
        current = _instance(client, p)
        original = p['original_business_id']
        other = next(row for row in p['r']['available_business_ids'] if row != original)
        enabled = _ok(client.post('/api/instances/' + current['id'] + '/activate', json={'version': current['version'],
            'release_id': p['r']['id'], 'business_ids': [other], 'confirm_human_workflow_scope': True}))
        assert enabled['active_release_id'] == p['r']['id']
        assert original not in enabled['enabled_business_ids'] and other in enabled['enabled_business_ids']
    else:
        from tests.test_instance_workspace import configuration, prepared as compile_release, enable
        from generic_agents.instance_compiler import signature_value
        config = configuration(); flow = config['flows']['资料核验']; flow['output'] = '新版本核验结论'
        flow['confirmedSignature'] = json.dumps(signature_value(config, flow), ensure_ascii=False)
        release, _, _ = compile_release(client, config, instance_id=p['r']['instance_id'])
        assert release['id'] != p['r']['id']; enable(client, release)


def _restore(client, p):
    with Session(engine()) as db:
        package = db.get(Capability, p['r']['package_id'])
        assert package.status == 'published'  # Revoked packages have no republish API.
    current = _instance(client, p)
    _ok(client.post('/api/instances/' + current['id'] + '/activate', json={'version': current['version'],
        'release_id': p['r']['id'], 'business_ids': p['r']['available_business_ids'], 'confirm_human_workflow_scope': True}))


def _pending(client, supplier, *, blocked='package_withdrawn'):
    p = (_two_business_prepared(client, supplier) if blocked == 'business_disabled'
        else prepared(client, supplier, verification='confirmed'))
    supplier['modes'][p['asset']['external_id']] = 'unknown'; dispatch(p['monitor'], p['acquisition_id'])
    assert len(supplier['calls']) == 1
    key = supplier['calls'][0]['request_key']
    supplier['receipts'][key].update(status='accepted', terminal=False, no_future_device_activity=False)
    if blocked: _block(client, p, blocked)
    current = _ok(client.get(p['base']))
    stop_body = {'request_id': str(uuid4()), 'expected_version': current['version'],
        'reason': '停止原目标；只保留已派发原请求的迟到资料和真实回执'}
    stopped_value = _ok(client.post(p['base'] + '/stop', json=stop_body))
    p['stop_command'] = {'path': p['base'] + '/stop', 'body': stop_body, 'response': stopped_value}
    assert receipt(client, p).status_code == 200
    assert rows(client, p)['items'][0]['reservation_held']
    return p


def _body(client, p):
    from generic_agents.models import now
    extractor = publish(client, manifest('tool', name='late_original_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': [{'id': 'reported_high', 'field': 'temperature', 'operator': 'gt', 'threshold': 90}]}))
    at = now()
    return {'source_id': p['source']['id'], 'source_record_id': uuid4().hex, 'entity_id': p['obj']['id'],
        'observed_at': at.isoformat(), 'business_day': str(at.date()),
        'attributes': {p['field']: p['obj']['attributes'][p['field']], 'temperature': 95},
        'extractor_id': extractor['id'], 'startup_mode': 'automatic', 'acquisition_id': p['acquisition_id']}


def _ingest(client, p):
    body = _body(client, p); row = _ok(client.post('/api/observations', json=body), 201)
    assert _ok(client.post('/api/observations', json=body), 201)['id'] == row['id']
    with Session(engine()) as db:
        identity = db.scalar(select(Job.id).where(Job.idempotency_key == 'extract:' + row['id']))
    assert process_once(identity, deliver=False)
    with Session(engine()) as db:
        assert db.get(Job, identity).status == 'succeeded', db.get(Job, identity).data.get('error')
    deliver_events(observation_id=row['id'], event_kind='observation.data')
    return row['id'], body, identity


def _review(client, p, identity):
    item = next(row for row in rows(client, p)['items'][0]['observations']['items'] if row['id'] == identity)
    assert item['can_verify'] and item['review'] is None
    body = {'request_id': str(uuid4()), 'expected_version': item['version'], 'expected_input_hash': item['input_hash'],
        'decision': 'confirmed', 'reason': '核对原输入与真实提取回执；只保留资料，不恢复原目标或认定现场状态'}
    path = '/api/observations/' + identity + '/verify'
    value = _ok(client.post(path, json=body))
    assert _ok(client.post(path, json=body)) == value
    return {'path': path, 'body': body, 'response': value}


def _jobs():
    with Session(engine()) as db: return set(db.scalars(select(Job.id)))


def run_reception_journey(client, supplier, tmp_path):
    """Real nonterminal input/review -> terminal receipt -> retained original proof."""
    p = _pending(client, supplier); principal = _principal(client)
    before = _jobs()
    with Session(engine()) as db:
        entity_before = digest(db.get(Entity, p['obj']['id']).data)
        clouds_before = set(db.scalars(select(CloudCall.id)))
    identity, body, processing = _ingest(client, p)
    review = _review(client, p, identity)
    item = rows(client, p)['items'][0]
    assert item['status'] == 'awaiting_observation' and item['reservation_held'] and not item['device_action_terminal']
    with Session(engine()) as db:
        original = db.get(Observation, identity); limit = copy.deepcopy(original.data[MARKER])
        assert limit['code'] == 'package_withdrawn'
    with Session(engine()) as db:
        original = db.get(Observation, identity)
        assert hold(db, principal, original) == limit
        from generic_agents.state_refresh import source_changed
        assert source_changed(db, principal, original, '原撤回业务只保存迟到资料') is None
        db.commit()
    key = supplier['calls'][0]['request_key']
    supplier['receipts'][key].update(status='completed', terminal=True, no_future_device_activity=True)
    reads_before = len(supplier['read_calls'])
    assert reads_before == 1
    final = _ok(receipt(client, p))
    assert len(supplier['read_calls']) == reads_before + 1
    assert final['status'] == 'succeeded' and not final['reservation_held'] and final['closing_observation_id'] == identity
    assert final['reason'] == '原采集已核查结案，设备占用已释放。'
    assert _ok(receipt(client, p))['status'] == 'succeeded' and len(supplier['read_calls']) == 2
    goal = _ok(client.get(p['base'])); export = _ok(client.get(p['base'] + '/export'))
    task_path = '/api/workspace/tasks/' + p['task']['id']; task = _ok(client.get(task_path))
    assert goal['cancelled'] and not goal['acceptance'] and export['goal']['cancelled']
    assert _jobs() - before == {processing}
    with Session(engine()) as db:
        assert not db.scalar(select(Job.id).where(Job.kind == 'settle_received_acquisition_v245',
            Job.data['settlement']['acquisition_id'].as_string() == p['acquisition_id']))
        original = db.get(Observation, identity); ctx = context(db, principal, original)
        call = db.get(Invocation, original.data['invocation_id'])
        event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + identity))
        evidence = db.scalars(select(Memory).where(Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence')).one()
        restrictions = list(db.scalars(select(Audit).where(Audit.object_id == identity,
            Audit.action == 'observation.original_reception_only')))
        assert len(restrictions) == 1 and call.status == 'succeeded' and event.status == 'received'
        assert evidence.status == 'candidate' and evidence.data['not_authoritative'] is True
        assert not db.scalar(select(Event.id).where(Event.kind == 'perception.anomaly', Event.data['observation_id'].as_string() == identity))
        assert digest(db.get(Entity, p['obj']['id']).data) == entity_before
        assert set(db.scalars(select(CloudCall.id))) == clouds_before
        assert len(list(db.scalars(select(Task)))) == 1
        proof = {'schema': 'original-acquisition-reception-journey-proof-v127', 'task_id': p['task']['id'],
            'task_path': task_path, 'goal_path': p['base'], 'export_path': p['base'] + '/export',
            'instance_id': p['r']['instance_id'], 'release_id': p['r']['id'], 'package_id': p['r']['package_id'],
            'acquisition_id': p['acquisition_id'], 'observation_id': identity,
            'goal_stop': p['stop_command'],
            'ingest': {'path': '/api/observations', 'body': body, 'input_hash': original.data['input_hash']},
            'verify': review, 'receipt_read': {'path': p['base'] + '/acquisitions/' + p['acquisition_id'] + '/receipt',
                'body': {'expected_version': final['version'], 'reason': '只查询已结案原请求，不重发设备动作'}},
            'processing_job_id': processing, 'invocation': {'id': call.id, 'idempotency_key': call.idempotency_key, 'data': copy.deepcopy(call.data)},
            'event': {'id': event.id, 'kind': event.kind, 'data': copy.deepcopy(event.data)},
            'memory': {'id': evidence.id, 'status': evidence.status, 'data': copy.deepcopy(evidence.data)},
            'restriction': copy.deepcopy(original.data[MARKER]), 'restriction_audit_id': restrictions[0].id,
            'original_proof': ctx.proof, 'task': task, 'goal': goal, 'export': export,
            'original_supplier_request': copy.deepcopy(supplier['calls'][0]),
            'original_supplier_request_hash': ctx.acquisition.data['request_hash'],
            'physical_state_verified': False, 'new_cloud_calls': 0, 'new_device_posts': 0}
    folder = tmp_path / 'original-reception-proof'; folder.mkdir(mode=0o700)
    path = folder / 'proof.json'; path.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True), encoding='utf-8'); path.chmod(0o600)
    return {**proof, 'proof_path': str(path)}


def test_real_withdrawn_nonterminal_data_review_and_terminal_receipt_journey(client, supplier, tmp_path):
    proof = run_reception_journey(client, supplier, tmp_path)
    assert proof['restriction']['proof'] == proof['original_proof']


@pytest.mark.parametrize('blocked', ['instance_not_active', 'business_disabled', 'release_replaced'])
def test_other_explicit_scope_losses_preserve_only_original_data(client, supplier, blocked):
    p = _pending(client, supplier, blocked=blocked); before = _jobs(); identity, _, processing = _ingest(client, p)
    _review(client, p, identity)
    with Session(engine()) as db:
        original = db.get(Observation, identity)
        assert hold(db, _principal(client), original)['code'] == blocked
        assert db.get(Acquisition, p['acquisition_id']).status == 'awaiting_observation'
    assert _jobs() - before == {processing}


def test_stopping_only_one_goal_keeps_independent_monitoring_path_live(client, supplier):
    p = _pending(client, supplier, blocked=None); identity, _, _ = _ingest(client, p)
    with Session(engine()) as db:
        original = db.get(Observation, identity)
        assert context(db, _principal(client), original) is not None
        assert hold(db, _principal(client), original) is None and MARKER not in original.data
        assert db.scalar(select(Job.id).where(Job.kind == 'refresh_object_state', Job.data['state_request']['trigger_observation_id'].as_string() == identity))
        assert db.get(Task, p['task']['id']).data['native_goal_cancelled']


@pytest.mark.parametrize('blocked', [None, 'package_withdrawn'])
def test_only_held_delivery_skips_the_real_downstream_graph_fanout(client, supplier, monkeypatch, blocked):
    from generic_agents import worker
    from generic_agents.graph_event_models import GraphEventIntent
    p = _pending(client, supplier, blocked=blocked); actual = worker.enqueue_graph_event; calls = []
    with Session(engine()) as db:
        intents = set(db.scalars(select(GraphEventIntent.id)))
        graph_jobs = set(db.scalars(select(Job.id).where(Job.kind.in_(['admit_graph_source_event', 'receive_full_semantic_graph_event']))))

    def observed(db, event):
        if event.kind == 'observation.data':
            assert blocked is None, '原接收限制必须在派生接口调用之前生效'
            calls.append(event.id)
        return actual(db, event)  # Active control still executes the real code.

    monkeypatch.setattr(worker, 'enqueue_graph_event', observed)
    identity, _, _ = _ingest(client, p)
    assert bool(calls) is (blocked is None)
    with Session(engine()) as db:
        assert set(db.scalars(select(GraphEventIntent.id))) == intents
        assert set(db.scalars(select(Job.id).where(Job.kind.in_(['admit_graph_source_event', 'receive_full_semantic_graph_event'])))) == graph_jobs
        event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + identity))
        assert event.status == 'received'


def test_active_processing_anomaly_already_queued_before_real_pause_is_received_without_new_work(client, supplier, monkeypatch):
    from generic_agents import worker
    from generic_agents.graph_event_models import GraphEventIntent
    p = _pending(client, supplier, blocked=None); body = _body(client, p)
    created = _ok(client.post('/api/observations', json=body), 201)
    with Session(engine()) as db:
        processing = db.scalar(select(Job.id).where(Job.idempotency_key == 'extract:' + created['id']))
    assert process_once(processing, deliver=False)
    with Session(engine()) as db:
        events = list(db.scalars(select(Event).where(Event.data['observation_id'].as_string() == created['id'])))
        assert {row.kind for row in events} == {'observation.data', 'perception.anomaly'}
        assert all(row.status == 'pending_delivery' for row in events)
        assert db.get(Observation, created['id']).data['extracted']['anomalies']
        jobs, tasks = set(db.scalars(select(Job.id))), set(db.scalars(select(Task.id)))
        intents, clouds = set(db.scalars(select(GraphEventIntent.id))), set(db.scalars(select(CloudCall.id)))
        entity_hash = digest(db.get(Entity, p['obj']['id']).data)
    # This public action occurs after actual route commit, before either event.
    _block(client, p, 'instance_not_active')

    def forbidden(db, event):
        raise AssertionError('已排队旧异常不能越过原接收限制继续图分发')

    monkeypatch.setattr(worker, 'enqueue_graph_event', forbidden)
    counts = (len(supplier['calls']), len(supplier['read_calls']))
    deliver_events(observation_id=created['id'])
    _review(client, p, created['id'])
    with Session(engine()) as db:
        original = db.get(Observation, created['id']); assert original.status == 'processed'
        assert original.data['human_verification']['decision'] == 'confirmed'
        events = list(db.scalars(select(Event).where(Event.data['observation_id'].as_string() == original.id)))
        assert all(row.status == 'received' and row.data['derivation_receipt']['original_reception_restriction_hash']
            == original.data[MARKER]['restriction_hash'] for row in events)
        data_event = next(row for row in events if row.kind == 'observation.data')
        memory = db.scalars(select(Memory).where(Memory.data['event_id'].as_string() == data_event.id)).one()
        assert memory.status == 'candidate' and memory.data['data_reception'] == original.data['data_reception']
        assert set(db.scalars(select(Job.id))) == jobs and set(db.scalars(select(Task.id))) == tasks
        assert set(db.scalars(select(GraphEventIntent.id))) == intents and set(db.scalars(select(CloudCall.id))) == clouds
        assert digest(db.get(Entity, p['obj']['id']).data) == entity_hash
        assert db.get(Acquisition, p['acquisition_id']).status == 'awaiting_observation'
    assert (len(supplier['calls']), len(supplier['read_calls'])) == counts


@pytest.mark.parametrize('target', ['source', 'extractor'])
def test_no_new_ingest_authority_when_original_source_or_extractor_is_withdrawn(client, supplier, target):
    p = _pending(client, supplier); body = _body(client, p)
    with Session(engine()) as db:
        cap = db.get(Capability, body['source_id' if target == 'source' else 'extractor_id']); cap.status = 'revoked'; db.commit()
    assert client.post('/api/observations', json=body).status_code == 409
    with Session(engine()) as db:
        assert not db.scalar(select(Observation.id).where(Observation.source_record_id == body['source_record_id']))


@pytest.mark.parametrize('target', ['source', 'extractor'])
def test_withdrawal_between_registration_and_processing_grants_no_new_model_call(client, supplier, target):
    p = _pending(client, supplier); body = _body(client, p)
    original = _ok(client.post('/api/observations', json=body), 201)
    with Session(engine()) as db:
        identity = db.scalar(select(Job.id).where(Job.idempotency_key == 'extract:' + original['id']))
        entity_before = digest(db.get(Entity, p['obj']['id']).data)
        db.get(Capability, body['source_id' if target == 'source' else 'extractor_id']).status = 'revoked'; db.commit()
    assert process_once(identity, deliver=False)
    with Session(engine()) as db:
        assert db.get(Job, identity).status != 'succeeded'
        row = db.get(Observation, original['id'])
        assert row.data['input_hash'] == original['data']['input_hash'] and not row.data.get('extracted')
        assert not db.scalar(select(Invocation.id).where(Invocation.idempotency_key.like('observation:' + row.id + ':%'), Invocation.status == 'succeeded'))
        assert digest(db.get(Entity, p['obj']['id']).data) == entity_before
        assert db.get(Acquisition, p['acquisition_id']).status == 'awaiting_observation'
        assert db.scalar(select(SensorReservation.id).where(SensorReservation.acquisition_id == p['acquisition_id']))


def test_saved_history_survives_later_source_and_extractor_withdrawal(client, supplier):
    p = _pending(client, supplier); identity, _, _ = _ingest(client, p); principal = _principal(client)
    with Session(engine()) as db:
        original = db.get(Observation, identity)
        from generic_agents.instance_data_receipts import observation_receipt
        before = observation_receipt(db, principal, original)
        db.get(Capability, original.source_id).status = 'revoked'
        db.get(Capability, original.data['extractor_id']).status = 'revoked'; db.commit()
    with Session(engine()) as db:
        assert observation_receipt(db, principal, db.get(Observation, identity)) == before


@pytest.mark.parametrize('damage', ['none', 'marker_missing', 'audit_missing', 'duplicate_audit', 'marker_and_audit_missing',
    'event_restriction_missing', 'memory_restriction_missing', 'derivation_audit_missing'])
def test_reenabled_scope_cannot_erase_or_reissue_an_original_restriction(client, supplier, damage):
    p = _pending(client, supplier, blocked='instance_not_active'); identity, _, _ = _ingest(client, p); _restore(client, p); principal = _principal(client)
    with Session(engine()) as db:
        original = db.get(Observation, identity)
        audit = db.scalar(select(Audit).where(Audit.object_id == identity, Audit.action == 'observation.original_reception_only'))
        if damage in {'marker_missing', 'marker_and_audit_missing'}:
            data = copy.deepcopy(original.data); data.pop(MARKER); original.data = data
        if damage in {'audit_missing', 'marker_and_audit_missing'}: db.delete(audit)
        if damage == 'duplicate_audit':
            db.add(Audit(workspace_id=audit.workspace_id, actor_id=audit.actor_id, object_id=audit.object_id,
                action=audit.action, data=copy.deepcopy(audit.data)))
        if damage.endswith('restriction_missing') or damage == 'derivation_audit_missing':
            event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + identity))
            if damage == 'derivation_audit_missing':
                row = db.scalar(select(Audit).where(Audit.object_id == event.id, Audit.action == 'observation.derivation_skipped')); db.delete(row)
            else:
                row = event if damage == 'event_restriction_missing' else db.scalar(select(Memory).where(Memory.data['event_id'].as_string() == event.id))
                data = copy.deepcopy(row.data); data.pop('derivation_receipt'); row.data = data
        db.commit()
    with Session(engine()) as db:
        if damage == 'none':
            original = db.get(Observation, identity)
            assert hold(db, principal, original, remember=True) == original.data[MARKER]
        else:
            with pytest.raises(HTTPException): hold(db, principal, db.get(Observation, identity), remember=True)


def test_actual_receiver_lock_and_sticky_issue_serialize_with_review_and_preserve_cold_receipt(client, supplier):
    p = _pending(client, supplier, blocked=None); body = _body(client, p)
    created = _ok(client.post('/api/observations', json=body), 201); principal = _principal(client)
    with Session(engine()) as db:
        processing = db.scalar(select(Job.id).where(Job.idempotency_key == 'extract:' + created['id']))
    assert process_once(processing, deliver=False)
    _block(client, p)
    entered, review_entered = ThreadEvent(), ThreadEvent()
    item = rows(client, p)['items'][0]['observations']['items'][0]
    review = {'path': '/api/observations/' + created['id'] + '/verify', 'body': {
        'request_id': str(uuid4()), 'expected_version': item['version'], 'expected_input_hash': item['input_hash'],
        'decision': 'confirmed', 'reason': '并发接收期间保存原人工核查，不覆盖人稿或升级业务权限'}}

    def actual_review():
        review_entered.set()
        return client.post(review['path'], json=review['body'])

    def second_session():
        with Session(engine()) as other:
            other.execute(text("SET LOCAL lock_timeout='150ms'"))
            original = other.get(Observation, created['id']); entered.set()
            try:
                hold(other, principal, original, remember=True)
            except DBAPIError as error:
                assert (getattr(error.orig, 'sqlstate', None) or getattr(error.orig, 'pgcode', None)) == '55P03'
                other.rollback(); return 'same_observation_locked'
            return 'unexpected_second_writer'

    with Session(engine()) as receiver:
        original = receiver.get(Observation, created['id']); before_version = original.version
        assert MARKER not in original.data and 'data_reception' not in original.data
        event = receiver.scalar(select(Event).where(Event.idempotency_key == 'data:' + original.id))
        from generic_agents.instance_data_receipts import receive_observation, observation_receipt
        # The first production receiver JSON write must already own the row.
        reception = receive_observation(receiver, principal, event, original)
        pool = ThreadPoolExecutor(max_workers=2)
        human_future = pool.submit(actual_review); future = pool.submit(second_session)
        try:
            assert entered.wait(5) and review_entered.wait(5)
            assert future.result(timeout=5) == 'same_observation_locked'
            assert not human_future.done(), '真人核查须等待原接收写入释放同一资料锁'
            first = hold(receiver, principal, original, remember=True)
            receiver.commit()
            review['response'] = _ok(human_future.result(timeout=10))
        finally:
            receiver.rollback()  # Also release the row on an assertion failure.
            pool.shutdown(wait=True)
    assert review['response']['version'] == before_version + 1
    deliver_events(observation_id=created['id'], event_kind='observation.data')
    with Session(engine()) as db:
        original = db.get(Observation, created['id'])
        assert original.version == before_version + 1
        assert original.data['human_verification']['request_id'] == review['body']['request_id']
        assert original.data[MARKER] == first and original.data['data_reception'] == reception
        assert len(list(db.scalars(select(Audit).where(Audit.object_id == original.id,
            Audit.action == 'observation.original_reception_only')))) == 1
        event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + original.id))
        memory = db.scalars(select(Memory).where(Memory.data['event_id'].as_string() == event.id)).one()
        assert event.status == 'received' and memory.status == 'candidate'
        assert event.data['data_reception'] == memory.data['data_reception'] == reception
        assert event.data['derivation_receipt'] == memory.data['derivation_receipt']
        assert observation_receipt(db, principal, original)['data_reception'] == reception
    assert _ok(client.post(review['path'], json=review['body'])) == review['response']


@pytest.mark.parametrize('damage', ['origin_missing', 'delegation_missing', 'query_native_marker_missing'])
def test_active_original_native_chain_cannot_be_downgraded_before_any_restriction(client, supplier, damage):
    p = _pending(client, supplier, blocked=None); identity, _, _ = _ingest(client, p); principal = _principal(client)
    with Session(engine()) as db:
        original = db.get(Observation, identity); assert MARKER not in original.data
        acq = db.get(Acquisition, p['acquisition_id']); plan = db.get(ObservationPlan, acq.data['observation_plan_id'])
        if damage == 'query_native_marker_missing':
            task = db.get(Task, p['task']['id']); run = db.get(DataRun, task.data['native_goal_query_id'])
            data = copy.deepcopy(run.data); data.pop('native_goal_task_id'); run.data = data
        else:
            data = copy.deepcopy(plan.data)
            if damage == 'origin_missing': data.pop('observation_request')
            else: data['observation_request'].pop('goal_delegation')
            plan.data = data
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): hold(db, principal, db.get(Observation, identity))


@pytest.mark.parametrize('damage', ['goal_context', 'query', 'gap', 'plan', 'dispatch_quality', 'dispatch_body',
    'dispatch_audit', 'definition', 'package', 'invocation', 'event', 'memory', 'restriction', 'restriction_audit',
    'origin_missing', 'origin_null', 'delegation_missing', 'delegation_null', 'query_native_marker_missing', 'restriction_missing'])
def test_cold_original_reception_refuses_damaged_real_chain(client, supplier, damage):
    p = _pending(client, supplier); identity, _, _ = _ingest(client, p); principal = _principal(client)
    with Session(engine()) as db:
        original = db.get(Observation, identity); acq = db.get(Acquisition, p['acquisition_id'])
        task = db.get(Task, p['task']['id']); plan = db.get(ObservationPlan, acq.data['observation_plan_id'])
        if damage == 'goal_context':
            data = copy.deepcopy(task.data); data['native_goal_context']['business_id'] = str(uuid4()); task.data = data
        elif damage == 'query':
            run = db.get(DataRun, task.data['native_goal_query_id']); run.data = {**run.data, 'native_goal_request_hash': '0' * 64}
        elif damage == 'gap':
            gap = db.get(DataGap, plan.data['observation_request']['gap_id']); gap.data = {**gap.data, 'request': {}}
        elif damage in {'plan', 'origin_missing', 'origin_null', 'delegation_missing', 'delegation_null'}:
            data = copy.deepcopy(plan.data)
            if damage == 'plan': data['observation_request'] = {}
            elif damage == 'origin_missing': data.pop('observation_request')
            elif damage == 'origin_null': data['observation_request'] = None
            elif damage == 'delegation_missing': data['observation_request'].pop('goal_delegation')
            else: data['observation_request']['goal_delegation'] = None
            plan.data = data
        elif damage == 'query_native_marker_missing':
            run = db.get(DataRun, task.data['native_goal_query_id']); data = copy.deepcopy(run.data)
            data.pop('native_goal_task_id'); run.data = data
        elif damage.startswith('dispatch_'):
            if damage == 'dispatch_audit':
                audit = db.scalar(select(Audit).where(Audit.object_id == acq.id, Audit.action == 'acquisition.dispatch')); audit.action = 'changed'
            else:
                data = copy.deepcopy(acq.data)
                if damage == 'dispatch_quality': data['dispatch_observation_constraints']['selection_requirement']['minimum_verification'] = 'unverified_extraction'
                else: data['request_hash'] = '0' * 64
                acq.data = data
        elif damage == 'definition':
            entity = db.get(Entity, p['obj']['id']); entity.entity_type = 'another_type'
        elif damage == 'package':
            package = db.get(Capability, task.package_id); package.data = {**package.data, 'description': 'changed frozen package'}
        elif damage == 'invocation':
            call = db.get(Invocation, original.data['invocation_id']); call.data = {**call.data, 'input_hash': '0' * 64}
        elif damage in {'event', 'memory'}:
            event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + identity))
            row = event if damage == 'event' else db.scalar(select(Memory).where(Memory.data['event_id'].as_string() == event.id))
            row.data = {**row.data, 'invocation_id': str(uuid4())}
        elif damage == 'restriction_missing':
            data = copy.deepcopy(original.data); data.pop(MARKER); original.data = data
        elif damage == 'restriction':
            data = copy.deepcopy(original.data); data[MARKER]['proof']['release_id'] = str(uuid4())
            data[MARKER]['restriction_hash'] = digest({key: value for key, value in data[MARKER].items() if key != 'restriction_hash'}); original.data = data
        else:
            audit = db.scalar(select(Audit).where(Audit.object_id == identity, Audit.action == 'observation.original_reception_only')); audit.data = {}
        db.commit()
    with Session(engine()) as db:
        original = db.get(Observation, identity)
        from generic_agents.instance_data_receipts import observation_receipt
        with pytest.raises(HTTPException):
            if damage in {'event', 'memory'}: observation_receipt(db, principal, original)
            else: hold(db, principal, original)


@pytest.mark.parametrize('damage', ['version', 'entity', 'package', 'history', 'fingerprint'])
def test_review_trigger_is_not_itself_permission_to_suppress_work(client, supplier, damage):
    p = _pending(client, supplier); identity, _, _ = _ingest(client, p); _review(client, p, identity); principal = _principal(client)
    with Session(engine()) as db:
        original = db.get(Observation, identity)
        trigger = 'observation-review:' + identity + ':' + str(original.version)
        options = {'entity_id': original.entity_id, 'package_id': p['r']['package_id']}
        if damage == 'version': trigger = 'observation-review:' + identity + ':' + str(original.version + 1)
        elif damage == 'entity': options['entity_id'] = str(uuid4())
        elif damage == 'package': options['package_id'] = str(uuid4())
        else:
            data = copy.deepcopy(original.data)
            if damage == 'history': data['verification_history_hash'] = '0' * 64
            else: data['human_verification']['input_hash'] = '0' * 64
            original.data = data; db.flush()
        with pytest.raises(HTTPException): trigger_hold(db, principal, trigger, **options)


def test_viewer_cannot_register_or_verify_late_input(client, supplier):
    p = _pending(client, supplier); identity, _, _ = _ingest(client, p); body = _body(client, p)
    with Session(engine()) as db:
        member = db.scalars(select(Membership)).one(); member.role = 'viewer'; db.commit()
    assert client.post('/api/observations', json=body).status_code == 403
    assert client.post('/api/observations/' + identity + '/verify', json={
        'expected_version': 1, 'reason': '不能用历史接收证明增加操作权限', 'decision': 'confirmed'}).status_code == 403
