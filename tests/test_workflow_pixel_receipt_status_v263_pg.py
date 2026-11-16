"""Real Task/native CPU/SDK/independent ACK; synthetic RGB is engineering only.

No native inference, source authorization, SDK result or ACK is mocked. The
explicit four-step configuration tests execution plumbing, not machine-authored
business quality or physical change. A genuine producer-finalize barrier proves
the current safe 409 window; it does not claim seamless automatic continuation.
Recovery is the existing Task run-ability API with retry:true after completion.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from threading import Event as ThreadEvent
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import business_tools
from generic_agents import workflow_pixel_receipt_status_v263 as subject
from generic_agents.config import settings
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.instance_compiler import signature_value
from generic_agents.security import Principal
from generic_agents.worker import process_once, deliver_events
from tests.test_instance_workspace import configuration, prepared, object_row, complete
from tests.test_vertical_slice import publish
from tests.test_workflow_abilities import enable
from tests.test_workflow_pixel_native_pg_v250 import flow_scene, native_scene, pytestmark, good, ack


def inventory(s):
    with Session(engine()) as db:
        return {cls.__name__: sorted((row.id, row.version, getattr(row, 'status', None), digest(row.data))
            for row in db.scalars(select(cls).where(cls.workspace_id == s.workspace_id)))
            for cls in (m.WorkflowPixelRun, m.Task, m.Job, m.Invocation, m.Observation,
                m.Event, m.Memory, m.Audit, m.Entity, m.CloudCall)}


def read(s, *, p=None, task_id=None):
    before = inventory(s)
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        stamp = db.scalar(text('SELECT transaction_timestamp()')); transaction = db.get_transaction()
        value = subject.execute(db, p or s.p, task_id or s.task_id)
        assert db.get_transaction() is transaction and not db.new and not db.dirty and not db.deleted
        assert value['as_of'] == stamp.astimezone(timezone.utc).isoformat()
    assert inventory(s) == before
    Draft202012Validator(subject.output_schema()).validate(value)
    assert value['snapshot_hash'] == digest({k: v for k, v in value.items() if k != 'snapshot_hash'})
    assert value['registered_count'] == sum(value[k] for k in ('received_count', 'waiting_count', 'blocked_count'))
    assert value['all_registered_received'] is (value['registered_count'] > 0
        and value['received_count'] == value['registered_count'])
    assert value['expected_sources_verified'] is False
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            assert not set(item) & {'candidate_pixels', 'common_valid_pixels', 'unknown_pixels',
                'probability', 'scores', 'attributes', 'anomalies', 'raw', 'content', 'note'}
            pending.extend(item.values())
        elif isinstance(item, list): pending.extend(item)
    return value


def sdk(s, cap, key, writer=None):
    if writer is not None:
        call = invoke(writer, s.p, cap['id'], 'data', {'task_id': s.task_id}, key)
        return call.id, call.status, copy.deepcopy(call.data)
    with Session(engine()) as db: return sdk(s, cap, key, db)


def create_task(s, release=None):
    release = release or s.release; entity = object_row(s.client, release)
    task = good(s.client.post('/api/workspace/instances/' + s.instance_id + '/tasks', json={
        'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [entity['id']], 'title': '当次像素收件行政核查', 'request_id': str(uuid4()),
        'pixel_pair': {'before_image_id': s.source_before['id'], 'after_image_id': s.source_after['id'],
            'pixel_correspondence_confirmed': True, 'full_image_present_confirmed': True}}), 201)
    return task, entity


def numeric_manifest():
    status = subject.output_schema()
    return Manifest(kind='tool', namespace='pixel_receipt_administration_v263', name='receipt_administration',
        version='1.0.0', owner='工程核对岗位', description='只判断已登记像素成果的行政收件缺口，不判断现场变化',
        allowed_agent_roles=['business'], execution={'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        input_schema={'type': 'object', 'required': ['attributes'], 'additionalProperties': False,
            'properties': {'attributes': copy.deepcopy(status)}},
        output_schema={'type': 'object', 'required': ['attributes', 'anomalies', 'method'], 'additionalProperties': False,
            'properties': {'attributes': copy.deepcopy(status), 'method': {'const': 'registered_numeric_rules'},
                'anomalies': {'type': 'array', 'items': {'type': 'object',
                    'required': ['field', 'value', 'rule_id', 'severity'], 'additionalProperties': False,
                    'properties': {'field': {'type': 'string'}, 'value': {'type': 'number'},
                        'rule_id': {'type': 'string'}, 'severity': {'type': 'string'}}}}}},
        specification={'rules': [
            {'id': 'none_registered', 'field': 'registered_count', 'operator': 'lt', 'threshold': 1, 'severity': 'review'},
            {'id': 'waiting', 'field': 'waiting_count', 'operator': 'gt', 'threshold': 0, 'severity': 'review'},
            {'id': 'blocked', 'field': 'blocked_count', 'operator': 'gt', 'threshold': 0, 'severity': 'review'}]},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False})


def configured_flow(s):
    receipt_cap = publish(s.client, subject.manifest().model_dump(mode='json'))
    rules_cap = publish(s.client, numeric_manifest().model_dump(mode='json'))
    c = configuration(); flow = c['flows']['资料核验']
    first = copy.deepcopy(flow['steps'][0]); human = copy.deepcopy(flow['steps'][-1])
    first.update(name='计算当次两图候选', agent='多模感知智能体', role='', nextId='pixel_receipts',
        execution={'capability_id': s.registered['capability']['id'], 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    data = copy.deepcopy(first)
    data.update(id='pixel_receipts', name='像素Run独立收件核查', agent='数据管理智能体', nextId='receipt_business',
        execution={'capability_id': receipt_cap['id'], 'automation_action': 'data', 'arguments': {}, 'bindings': {}})
    business = copy.deepcopy(first)
    business.update(id='receipt_business', name='像素成果行政状态核对', agent='业务智能体', nextId=human['id'],
        execution={'capability_id': rules_cap['id'], 'automation_action': 'state', 'arguments': {},
            'bindings': {'attributes': {'source': 'previous_output_object', 'step_id': data['id']}}})
    flow['steps'] = [first, data, business, human]
    for action in ('observe', 'data', 'state'):
        c['automation']['policies']['资料核验']['rules'][action] = {'mode': '自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    release, _, check = prepared(s.client, c, instance_id=s.instance_id)
    assert release['available_business_ids'], check
    enable(s.client, release)
    return SimpleNamespace(release=release, data=data, business=business, human=human)


def test_fixed_manifest_data_only_and_server_injected_task_binding():
    value = subject.manifest().model_dump(mode='json')
    assert subject.validate_manifest(value) == value
    business_tools.validate_bindings(value)
    assert value['allowed_agent_roles'] == ['data']
    assert value['execution'] == {'adapter': 'builtin', 'handler': subject.HANDLER,
        'side_effect': False, 'input_bindings': {'task_id': 'task_identifier'}}
    mutations = [lambda v: v['allowed_agent_roles'].append('perception'),
        lambda v: v['execution']['input_bindings'].update(task_id='task_id'),
        lambda v: v['execution'].update(side_effect=True),
        lambda v: v['execution'].update(query_tool=True),
        lambda v: v['data_policy'].update(cloud_allowed=True)]
    for mutate in mutations:
        bad = copy.deepcopy(value); mutate(bad)
        with pytest.raises(HTTPException) as failure: business_tools.validate_bindings(bad)
        assert failure.value.status_code == 422
    assert subject.manifest().model_dump(mode='json') == value


def test_real_gateway_enforces_distinct_rr_ro_and_late_ack_does_not_rewrite_sdk(flow_scene, monkeypatch):
    s = flow_scene; cap = publish(s.client, subject.manifest().model_dump(mode='json'))
    original = subject.execute; readers = []; writer_session = None

    def observed(db, actor, task_id):
        assert db is not writer_session and db.bind is not writer_session.bind
        assert db.scalar(text('SELECT pg_backend_pid()')) != writer_session.scalar(text('SELECT pg_backend_pid()'))
        assert db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        assert db.scalar(text('SHOW transaction_read_only')) == 'on'
        with pytest.raises(DBAPIError) as failure:
            with db.begin_nested(): db.execute(text('UPDATE tasks SET version=version WHERE id=:id'), {'id': task_id})
        assert getattr(failure.value.orig, 'sqlstate', getattr(failure.value.orig, 'pgcode', None)) == '25006'
        result = original(db, actor, task_id); readers.append(copy.deepcopy(result)); return result

    monkeypatch.setattr(subject, 'execute', observed)
    key = 'pixel-receipt:' + str(uuid4())
    with Session(engine()) as db:
        writer_session = db; identity, status, stored = sdk(s, cap, key, db)
        assert status == 'succeeded' and stored['output']['waiting_count'] == 1
        assert db.scalar(text('SHOW transaction_read_only')) == 'off'
    frozen = copy.deepcopy(stored['output']); assert readers == [frozen]
    ack(s)
    with Session(engine()) as db:
        writer_session = db; replay = sdk(s, cap, key, db)
        assert replay[0] == identity and replay[2]['output'] == frozen and len(readers) == 1
        received = sdk(s, cap, 'pixel-receipt:' + str(uuid4()), db)[2]['output']
    assert received['registered_count'] == received['received_count'] == 1
    assert received['all_registered_received'] is True and received['expected_sources_verified'] is False
    assert frozen['received_count'] == 0 and frozen['source_snapshot_hash'] != received['source_snapshot_hash']
    with Session(engine()) as db: assert db.get(m.Invocation, identity).data['output'] == frozen
    # The observer proves the SDK's independent connection only. This final
    # ordinary reader may legitimately bind the same Engine as another Session.
    monkeypatch.setattr(subject, 'execute', original)
    read(s)


def test_queued_real_task_empty_scope_and_actual_actor_transaction_guards(flow_scene):
    s = flow_scene; task, _ = create_task(s)
    empty = read(s, task_id=task['id'])
    assert empty['registered_count'] == 0 and empty['runs'] == [] and empty['all_registered_received'] is False
    for mode in ('READ COMMITTED, READ ONLY', 'REPEATABLE READ, READ WRITE'):
        with Session(engine()) as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + mode))
            with pytest.raises(HTTPException) as failure: subject.execute(db, s.p, s.task_id)
            assert failure.value.status_code == 409 and db.in_transaction()
    for stale in (Principal(str(uuid4()), s.workspace_id, s.p.role, s.p.session_id),
            Principal(s.p.user_id, str(uuid4()), s.p.role, s.p.session_id)):
        with pytest.raises(HTTPException) as failure: read(s, p=stale)
        assert failure.value.status_code == 403
    with pytest.raises(HTTPException) as failure: read(s, task_id=str(uuid4()))
    assert failure.value.status_code == 404


@pytest.mark.parametrize('damage', ['independent_memory', 'input_audit', 'native_artifact'])
def test_bad_ack_is_counted_blocked_but_broken_provenance_is_not_false_empty(flow_scene, damage):
    s = flow_scene; ack(s); frozen = read(s); path = raw = None
    assert frozen['received_count'] == 1
    with Session(engine()) as db:
        run = db.get(m.WorkflowPixelRun, s.run_id)
        if damage == 'independent_memory': db.delete(db.get(m.Memory, frozen['runs'][0]['memory']['id']))
        elif damage == 'input_audit': db.delete(db.get(m.Audit, run.data['input_audit_id']))
        else:
            ref = next(v for v in run.data['result']['artifacts'] if v['kind'] == 'manifest')
            path = settings().storage_dir / db.get(m.Evidence, ref['evidence_id']).storage_key
            raw = path.read_bytes(); path.write_bytes(raw + b'corrupt')
        db.commit()  # Deliberate corruption only inside the disposable PG fixture.
    try:
        if damage == 'independent_memory':
            blocked = read(s)
            assert (blocked['registered_count'], blocked['received_count'], blocked['waiting_count'], blocked['blocked_count']) == (1, 0, 0, 1)
            ref = blocked['runs'][0]
            assert ref['reception_state'] == 'blocked_integrity' and ref['memory'] is None and ref['receipt_hash'] is None
            assert ref['integrity_issue'] == subject.ERROR and blocked['all_registered_received'] is False
        else:
            with pytest.raises(HTTPException) as failure: read(s)
            assert failure.value.status_code == 409 and failure.value.detail == subject.ERROR
        assert frozen['received_count'] == 1 and frozen['all_registered_received'] is True
    finally:
        if path is not None: path.write_bytes(raw)


def test_native_model_ack_data_sdk_whole_business_and_human_are_real(flow_scene):
    s = flow_scene; meta = configured_flow(s); task, entity = create_task(s, meta.release)
    assert process_once(task['workflow_runtime']['automatic']['job_id'], deliver=False)
    task = good(s.client.get('/api/workspace/tasks/' + task['id']))
    entry = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/pixel-results'))['records'][0]
    assert entry['reception']['state'] == 'waiting_data'
    deliver_events(workspace_id=s.workspace_id, observation_id=entry['reception']['observation_id'], event_kind='observation.data')
    assert process_once(task['workflow_runtime']['automatic']['job_id'], deliver=False)
    task = good(s.client.get('/api/workspace/tasks/' + task['id']))
    data = next(v for v in task['workflow_runtime']['receipts'] if v['step_id'] == meta.data['id'])
    frozen = copy.deepcopy(data['output'])
    assert frozen['registered_count'] == frozen['received_count'] == 1 and frozen['expected_sources_verified'] is False
    assert process_once(task['workflow_runtime']['automatic']['job_id'], deliver=False)
    task = good(s.client.get('/api/workspace/tasks/' + task['id']))
    business = next(v for v in task['workflow_runtime']['receipts'] if v['step_id'] == meta.business['id'])
    assert business['input_snapshot'] == {'attributes': frozen}
    assert business['output'] == {'attributes': frozen, 'anomalies': [], 'method': 'registered_numeric_rules'}
    assert task['status'] == 'workflow_waiting' and task['workflow_runtime']['current_step_id'] == meta.human['id']
    finished = good(complete(s.client, task, note='已核对原图与独立收件；零候选不证明现场无变化'))
    assert finished['status'] == 'workflow_completed'
    with Session(engine()) as db:
        assert db.get(m.Invocation, data['invocation_id']).data['output'] == frozen
        assert db.get(m.Invocation, business['invocation_id']).data['role'] == 'business'
        assert db.get(m.Invocation, data['invocation_id']).data['role'] == 'data'
        assert db.get(m.Entity, entity['id']).data == entity['attributes']
        assert list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id))) == []


def test_actual_producer_finalize_window_is_explicit_retryable_409_not_zero_or_received(flow_scene):
    s = flow_scene; task, _ = create_task(s)
    job_id = task['workflow_runtime']['automatic']['job_id']
    reached = ThreadEvent(); release = ThreadEvent()

    def before_commit(db):
        candidates = [r for r in db.identity_map.values() if isinstance(r, m.Job) and r.id == job_id
            and r.status == 'succeeded' and r.data.get('finished_at')]
        if candidates and not reached.is_set():
            reached.set(); assert release.wait(45), 'test did not release actual producer final commit'

    # No Job/status/lease is manufactured. Hold the real worker's final commit
    # after its model/SDK/Task handler has durably saved the original result.
    event.listen(Session, 'before_commit', before_commit)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process_once, job_id=job_id, deliver=False)
            try:
                assert reached.wait(90), 'actual native producer did not reach final commit'
                with Session(engine()) as db:
                    assert db.get(m.Job, job_id).status == 'running'
                    run = db.scalar(select(m.WorkflowPixelRun).where(m.WorkflowPixelRun.job_id == job_id))
                    assert run is not None and run.status == 'succeeded'
                    run_id = run.id
                with pytest.raises(HTTPException) as failure: read(s, task_id=task['id'])
                assert failure.value.status_code == 409 and failure.value.detail == subject.PRODUCER_PENDING
            finally: release.set()
            assert future.result(timeout=90)
    finally:
        release.set(); event.remove(Session, 'before_commit', before_commit)
    with Session(engine()) as db: assert db.get(m.Job, job_id).status == 'succeeded'
    pending = read(s, task_id=task['id'])
    assert pending['registered_count'] == 1 and pending['waiting_count'] == 1 and not pending['all_registered_received']
    ack(SimpleNamespace(workspace_id=s.workspace_id, run_id=run_id))
    received = read(s, task_id=task['id'])
    assert received['received_count'] == 1 and received['all_registered_received'] is True
    # This deliberately does not assert automatic continuation during the
    # normal window. A failed data ability uses the unchanged explicit
    # POST /api/workspace/tasks/{id}/run-ability {version,step_id,request_id,retry:true}.
