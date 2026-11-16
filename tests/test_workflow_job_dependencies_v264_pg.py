"""Real PG/Task/Job/SDK barriers, with a held real producer final commit.

The compact flows are explicit engineering configurations. Native positives use
the installed CPU model and independent receiver. The graph fixture substitutes
only synthetic document conversion/OCR/provider transport before real review
and adoption; no SDK, ACK, saved workflow authority or Job is fabricated. Direct
DB changes below are disposable negative fault injections after normal creation.
Only Root executes this file against its fenced PostgreSQL/native runtime.
"""
import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event as ThreadEvent
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import worker
from generic_agents import leases
from generic_agents import workflow_job_dependencies_v264 as dependencies
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from tests.test_instance_workspace import prepared, object_row, complete
from tests.test_workflow_abilities import tool, conf, enable
from tests.test_workflow_pixel_receipt_status_v263_pg import (
    flow_scene, native_scene, configured_flow, create_task, good,
    pytestmark as native_pytestmark,
)
from tests import test_full_semantic_graph_journey_v114 as graph_journey
from tests import test_report_full_semantic_region_source_postgres as region_source
from tests.test_full_semantic_graph_journey_v114 import (
    v7_scene, reading_scene, local_runtime,
)
from tests.test_instance_workspace import save


KIND = 'execute_workflow_step'


@pytest.fixture
def full_source_scene(request, client, account, monkeypatch, local_runtime):
    """Adapt only this synthetic fixture to conftest's current owned UUID DB."""
    from tests import conftest as test_context

    def current_owned_database():
        expected = test_context._original.set(database=test_context._test_name)
        url = engine().url
        assert re.fullmatch(r'ga_test_[0-9a-f]{12}', test_context._test_name)
        assert test_context._original.database != test_context._test_name
        assert expected.host in {'127.0.0.1', 'localhost', '::1'}
        assert type(expected.port) is int and 1 <= expected.port <= 65535
        assert url == expected and url != test_context._original
        assert url.database == test_context._test_name and url.port == expected.port
        me = good(client.get('/api/me'))
        assert me['workspace_id'] == account[2]
        with Session(engine()) as db:
            actual = db.execute(text('SELECT current_database(),inet_server_port(),host(inet_server_addr())')).one()
            assert actual[0] == test_context._test_name and actual[1] == expected.port
            assert actual[2] in {'127.0.0.1', '::1'}
            assert db.get(m.Workspace, account[2]) is not None
            assert db.scalar(select(m.Membership.id).where(m.Membership.workspace_id == account[2],
                m.Membership.user_id == me['user_id'])) is not None

    current_owned_database()
    # No fixed-name database opt-in or environment variable is supplied. The
    # original fixture still performs every synthetic upload/review API call.
    from tests import test_report_full_reading_source as reading_source
    import sys
    legacy_guard = reading_source.fence
    aliases = [module for name, module in list(sys.modules.items())
               if name.startswith('tests.') and getattr(module, 'fence', None) is legacy_guard]
    assert region_source in aliases and reading_source in aliases and 2 <= len(aliases) <= 128
    for module in aliases:
        monkeypatch.setattr(module, 'fence', current_owned_database)
    return region_source.full_source_scene.__wrapped__(request, client, monkeypatch, local_runtime)


@pytest.fixture
def v7_scene(reading_scene, monkeypatch):
    """Keep the existing synthetic provider setup, with the new exact DB guard."""
    from tests import test_report_evidence_pipeline_v7_postgres as native
    region_source.fence()
    scene = reading_scene
    assert native.reply(scene['client'].get('/api/me'))['role'] == 'admin'
    manifest = json.loads((native.Path(native.__file__).parents[1] / 'examples/qwen.json').read_text())
    manifest.update(name='synthetic_native_v7_' + uuid4().hex[:10],
        description='PUBLIC synthetic native HTTP only; not actual model/report quality')
    declaration = {'schema': 'report-evidence-output-v5', 'max_tokens': 32768,
        'map_transport': native.DELIVERY, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    manifest['execution'].update(provider='qwen', model='qwen3.8-flash',
        secret_ref='QWEN_TOKENPLAN_API_KEY', max_tokens=8192, report_evidence_output=declaration)
    assert 'thinking_mode' not in manifest['execution']
    cap = native.publish(scene['client'], manifest)
    assert cap['status'] == 'published' and cap['validated_hash'] == cap['content_hash']
    assert cap['data']['execution']['report_evidence_output'] == declaration
    monkeypatch.setattr(native.settings(), 'cloud_daily_call_limit', 25)
    body = {**copy.deepcopy(scene['body']), 'request_id': 'fe_' + str(uuid4()), 'model_id': cap['id']}
    return {**scene, 'body': body, 'cap': cap}


def _task(client, task_id):
    return good(client.get('/api/workspace/tasks/' + task_id))


def _new_task(client, release, *, entity=None):
    if entity is None:
        kind = release['snapshot']['object_types'][1]
        attributes = {field['key']: 12 if field['label'] == '数量' else '公开登记数量'
            for field in kind['fields']}
        entity = good(client.post('/api/instances/' + release['instance_id'] + '/objects', json={
            'release_id': release['id'], 'object_type': kind['key'], 'attributes': attributes,
        }), 201)
    task = good(client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
        'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [entity['id']], 'title': '真实后台完成依赖工程核验', 'request_id': str(uuid4()),
    }), 201)
    return task, entity


def _release(client, capability, *, one_step=False):
    configuration = conf(capability, ending=one_step)
    flow = configuration['flows']['资料核验']
    if not one_step:
        first, human = flow['steps']
        second = copy.deepcopy(first)
        first['nextId'] = 'second_calculation'
        second.update(id='second_calculation', name='复核登记数量', output='数量复核结果', nextId=human['id'])
        second['execution']['arguments']['operation'] = 'count'
        flow['steps'] = [first, second, human]
        flow['confirmedSignature'] = json.dumps(signature_value(configuration, flow), ensure_ascii=False)
    release, _, checked = prepared(client, configuration)
    assert release['available_business_ids'], checked
    enable(client, release)
    return release


@pytest.fixture
def compact_scene(client, account):
    capability = tool(client)
    release = _release(client, capability)
    task, entity = _new_task(client, release)
    return SimpleNamespace(client=client, workspace_id=account[2], capability=capability,
        release=release, task_id=task['id'], parent_id=task['workflow_runtime']['automatic']['job_id'],
        entity=entity)


@contextmanager
def _producer_final_commit(job_id):
    """Hold only the real worker's final succeeded commit, never its handler."""
    reached = ThreadEvent()
    released = ThreadEvent()

    def before_commit(db):
        finishing = any(isinstance(row, m.Job) and row.id == job_id
            and row.status == 'succeeded' and row.data.get('finished_at')
            for row in db.identity_map.values())
        if finishing and not reached.is_set():
            reached.set()
            assert released.wait(60), 'test did not release the real producer final commit'

    event.listen(Session, 'before_commit', before_commit)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.process_once, job_id=job_id, deliver=False)
            try:
                assert reached.wait(90), 'real producer did not reach its final succeeded commit'
                yield
            finally:
                released.set()
                assert future.result(timeout=90)
    finally:
        released.set()
        event.remove(Session, 'before_commit', before_commit)


@contextmanager
def _lost_final_commit(job_id):
    """Real handler/SDK saves survive one actual finalizer LeaseLost fault."""
    lost = []

    def before_commit(db):
        finishing = next((row for row in db.identity_map.values() if isinstance(row, m.Job)
            and row.id == job_id and row.status == 'succeeded' and row.data.get('finished_at')), None)
        if finishing is not None and not lost:
            lost.append(finishing.lease_token)
            raise leases.LeaseLost('disposable final-commit interruption after the real handler')

    event.listen(Session, 'before_commit', before_commit)
    try:
        yield lost
    finally:
        event.remove(Session, 'before_commit', before_commit)


def _expire_and_recover(job_id, old_token, monkeypatch):
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        assert job.status == 'running' and job.lease_token == old_token and job.attempts == 1
        # Disposable crash simulation changes only the lease deadline, using
        # the server clock. Job, SDK, receipt and source states remain real.
        job.lease_until = db.scalar(text("SELECT clock_timestamp() - INTERVAL '1 second'"))
        workspace_id = job.workspace_id
        db.commit()
    original_claim = worker.claim
    observed = []

    def observed_claim(identity=None, kinds=None, **options):
        claimed = original_claim(identity, kinds, **options)
        if identity == job_id and claimed is not None:
            observed.append(claimed)
        return claimed

    with monkeypatch.context() as patch:
        patch.setattr(worker, 'claim', observed_claim)
        assert worker.process_once(job_id, deliver=False, workspace_id=workspace_id)
    assert len(observed) == 1 and observed[0][0] == job_id and observed[0][1] != old_token
    with Session(engine()) as db:
        recovered = db.get(m.Job, job_id)
        assert recovered.status == 'succeeded' and recovered.attempts == 2
        assert recovered.lease_token == observed[0][1]


def _call_inventory(workspace_id):
    with Session(engine()) as db:
        return {
            'sdk': sorted((row.id, row.status, row.capability_id, row.idempotency_key, digest(row.data))
                for row in db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == workspace_id))),
            'cloud': sorted((row.id, getattr(row, 'status', None), digest(row.data))
                for row in db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == workspace_id))),
            'sources': sorted((row.id, row.status, row.content_hash)
                for row in db.scalars(select(m.Capability).where(m.Capability.workspace_id == workspace_id,
                    m.Capability.kind == 'source'))),
        }


def _delete_child_binding(db, task, child):
    assert dependencies.KEY in child.data and child.id in task.data[dependencies.REFS]
    child.data = {key: value for key, value in child.data.items() if key != dependencies.KEY}
    task.data = {key: value for key, value in task.data.items() if key != dependencies.REFS}
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == task.workspace_id,
        m.Audit.action == dependencies.BOUND, m.Audit.object_id == child.id)))
    assert len(proofs) == 1
    db.delete(proofs[0])


def _bound_child(s, *, producer_status):
    task = _task(s.client, s.task_id)
    assert task['status'] == 'workflow_queued', task['workflow_runtime']
    child_id = task['workflow_runtime']['automatic']['job_id']
    assert child_id != s.parent_id
    with Session(engine()) as db:
        parent = db.get(m.Job, s.parent_id)
        child = db.get(m.Job, child_id)
        saved = db.get(m.Task, s.task_id)
        assert parent.status == producer_status and child.status == 'queued'
        marker = copy.deepcopy(child.data[dependencies.KEY])
        assert [pin['job_id'] for pin in marker['parents']] == [s.parent_id]
        assert saved.data[dependencies.REFS][child.id] == marker['record_hash']
        proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == dependencies.BOUND, m.Audit.object_id == child.id)))
        assert len(proofs) == 1 and proofs[0].data == marker
        assert proofs[0].actor_id == marker['bound_by'] == child.data['actor_id']
        assert marker['record_hash'] == digest({k: v for k, v in marker.items() if k != 'record_hash'})
        receipts = saved.data['workflow_runtime']['receipts']
        assert len(receipts) == 1 and receipts[0]['request_id'] == parent.id
        parent_call = db.get(m.Invocation, receipts[0]['invocation_id'])
        assert parent_call.status == 'succeeded' and parent_call.data['job_id'] == parent.id
        completed = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == 'workflow.ability_completed', m.Audit.object_id == s.task_id,
            m.Audit.data['invocation'].as_string() == parent_call.id)))
        assert len(completed) == 1
    return child_id


def _unissued(job_id):
    with Session(engine()) as db:
        child = db.get(m.Job, job_id)
        assert child.status == 'queued' and child.attempts == 0 and child.lease_token is None
        assert db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == child.workspace_id,
            m.Invocation.idempotency_key == child.idempotency_key)) is None


def _one_sdk(job_id, *, attempts=1):
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == job.workspace_id,
            m.Invocation.idempotency_key == job.idempotency_key)))
        assert job.status == 'succeeded' and job.attempts == attempts
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        call = calls[0]
        proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == job.workspace_id,
            m.Audit.action == 'capability.invoke', m.Audit.object_id == call.id)))
        assert len(proofs) == 1 and proofs[0].data == {'status': 'succeeded'}
        return call.id, copy.deepcopy(call.data['output'])


def test_real_final_commit_barrier_concurrent_claimers_and_ready_job_fairness(compact_scene):
    s = compact_scene
    ready_release = _release(s.client, s.capability, one_step=True)
    ready_entity = object_row(s.client, ready_release, 1)
    with _producer_final_commit(s.parent_id):
        child_id = _bound_child(s, producer_status='running')
        for _ in range(3):
            assert worker.claim(child_id, [KIND], workspace_id=s.workspace_id) is None
            assert worker.process_once(child_id, [KIND], deliver=False, workspace_id=s.workspace_id) is False
            _unissued(child_id)

        # A newer real Job must pass the older pending child before LIMIT 1.
        ready_task, _ = _new_task(s.client, ready_release, entity=ready_entity)
        ready_id = ready_task['workflow_runtime']['automatic']['job_id']
        with Session(engine()) as db:
            assert db.get(m.Job, child_id).available_at <= db.get(m.Job, ready_id).available_at
        start = Barrier(2)

        def contender():
            assert worker.claim(child_id, [KIND], workspace_id=s.workspace_id) is None
            start.wait(timeout=20)
            return worker.process_once(kinds=[KIND], deliver=False, workspace_id=s.workspace_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contender) for _ in range(2)]
            assert sorted(f.result(timeout=30) for f in futures) == [False, True]
        _unissued(child_id)
        _one_sdk(ready_id)
        assert _task(s.client, ready_task['id'])['status'] == 'workflow_completed'

    assert worker.process_once(child_id, deliver=False, workspace_id=s.workspace_id)
    child_call, _ = _one_sdk(child_id)
    assert worker.process_once(child_id, deliver=False, workspace_id=s.workspace_id) is False
    final = _task(s.client, s.task_id)
    assert final['status'] == 'workflow_waiting' and final['workflow_runtime']['current_step_id'] == 'record'
    assert len(final['workflow_runtime']['receipts']) == 2
    assert final['workflow_runtime']['receipts'][0]['output']['value'] == 12
    assert final['workflow_runtime']['receipts'][1]['output']['value'] == 1
    assert final['workflow_runtime']['receipts'][1]['invocation_id'] == child_call
    _one_sdk(s.parent_id)
    finished = good(complete(s.client, final, note='已核对真实后台结果与原完成记录'))
    assert finished['status'] == 'workflow_completed'
    with Session(engine()) as db:
        assert db.get(m.Entity, s.entity['id']).data == s.entity['attributes']
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id)))


def _blocked_without_child_sdk(s, child_id, expected):
    assert worker.process_once(child_id, deliver=False, workspace_id=s.workspace_id)
    saved = _task(s.client, s.task_id)
    assert saved['status'] == 'workflow_blocked'
    automatic = saved['workflow_runtime']['automatic']
    assert automatic['phase'] == 'blocked' and expected in automatic['error']
    assert len(saved['workflow_runtime']['receipts']) == 1
    with Session(engine()) as db:
        job = db.get(m.Job, child_id)
        assert job.status == 'failed' and job.attempts == 1
        assert db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == s.workspace_id,
            m.Invocation.idempotency_key == job.idempotency_key)) is None


@pytest.mark.parametrize('terminal', ['failed', 'cancelled'])
def test_unsuccessful_parent_blocks_real_queued_child_without_sdk(compact_scene, terminal):
    s = compact_scene
    assert worker.process_once(s.parent_id, deliver=False)
    child_id = _bound_child(s, producer_status='succeeded')
    # Negative corruption only: retain the real successful SDK and handler receipt.
    with Session(engine()) as db:
        parent = db.get(m.Job, s.parent_id)
        parent.status = terminal
        parent.data = {**parent.data, 'error': 'disposable negative terminal fault injection'}
        db.commit()
    _blocked_without_child_sdk(s, child_id, dependencies.FAILED)
    with Session(engine()) as db:
        assert db.get(m.Job, s.parent_id).status == terminal


@pytest.mark.parametrize('damage', [
    'marker_missing', 'marker_tampered', 'audit_missing', 'audit_tampered',
    'task_ref_missing', 'task_ref_tampered', 'cross_task_parent',
])
def test_dependency_marker_independent_audit_task_ref_and_task_scope_fail_closed(compact_scene, damage):
    s = compact_scene
    assert worker.process_once(s.parent_id, deliver=False)
    child_id = _bound_child(s, producer_status='succeeded')
    foreign_marker = None
    if damage == 'cross_task_parent':
        other, _ = _new_task(s.client, s.release)
        other_parent = other['workflow_runtime']['automatic']['job_id']
        assert worker.process_once(other_parent, deliver=False)
        other = _task(s.client, other['id'])
        with Session(engine()) as db:
            foreign_marker = copy.deepcopy(db.get(m.Job,
                other['workflow_runtime']['automatic']['job_id']).data[dependencies.KEY])
    with Session(engine()) as db:
        job = db.get(m.Job, child_id)
        task = db.get(m.Task, s.task_id)
        proof = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == dependencies.BOUND, m.Audit.object_id == child_id))
        marker = copy.deepcopy(job.data[dependencies.KEY])
        refs = copy.deepcopy(task.data[dependencies.REFS])
        if damage == 'marker_missing':
            job.data = {k: v for k, v in job.data.items() if k != dependencies.KEY}
        elif damage == 'marker_tampered':
            marker['parents'][0]['output_hash'] = '0' * 64
            job.data = {**job.data, dependencies.KEY: marker}
        elif damage == 'audit_missing':
            db.delete(proof)
        elif damage == 'audit_tampered':
            proof.data = {**proof.data, 'bound_by': str(uuid4())}
        elif damage == 'task_ref_missing':
            refs.pop(child_id)
            task.data = {**task.data, dependencies.REFS: refs}
        elif damage == 'task_ref_tampered':
            refs[child_id] = '0' * 64
            task.data = {**task.data, dependencies.REFS: refs}
        else:
            # Re-seal all three forged negative claims together, so the actual
            # same-Task ancestry check, not just a mismatched hash, must reject it.
            marker['parents'] = foreign_marker['parents']
            marker['record_hash'] = digest({k: v for k, v in marker.items() if k != 'record_hash'})
            job.data = {**job.data, dependencies.KEY: marker}
            proof.data = copy.deepcopy(marker)
            refs[child_id] = marker['record_hash']
            task.data = {**task.data, dependencies.REFS: refs}
        db.commit()
    _blocked_without_child_sdk(s, child_id, dependencies.ERROR)
    _one_sdk(s.parent_id)


def test_enrolled_compact_child_cannot_become_legacy_by_deleting_all_three_binding_sides(compact_scene):
    s = compact_scene
    assert worker.process_once(s.parent_id, deliver=False)
    child_id = _bound_child(s, producer_status='succeeded')
    with Session(engine()) as db:
        task = db.get(m.Task, s.task_id)
        child = db.get(m.Job, child_id)
        enrollment = copy.deepcopy(task.data[dependencies.ENROLL])
        assert enrollment['first_job_id'] == s.parent_id and enrollment['first_scope']['visit'] == 0
        enrolled = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == dependencies.ENROLLED, m.Audit.object_id == task.id)))
        assert len(enrolled) == 1 and enrolled[0].data == enrollment
        _delete_child_binding(db, task, child)
        db.commit()
    before = _call_inventory(s.workspace_id)
    _blocked_without_child_sdk(s, child_id, dependencies.ERROR)
    assert _call_inventory(s.workspace_id) == before
    with Session(engine()) as db:
        assert db.get(m.Task, s.task_id).data[dependencies.ENROLL] == enrollment
        assert db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == dependencies.ENROLLED, m.Audit.object_id == s.task_id)).data == enrollment


def test_old_compact_producer_recovers_after_first_child_enrollment_without_new_sdk(client, account, monkeypatch):
    capability = tool(client)
    release = _release(client, capability)
    # This simulates only the old version's absence of dependency metadata at
    # actual Task creation. Source, scheduling, SDK and handler still execute.
    with monkeypatch.context() as old_version:
        old_version.setattr(dependencies, 'bind_compact', lambda *args, **kwargs: None)
        task, _ = _new_task(client, release)
    parent_id = task['workflow_runtime']['automatic']['job_id']
    s = SimpleNamespace(client=client, workspace_id=account[2], task_id=task['id'], parent_id=parent_id)
    with Session(engine()) as db:
        parent = db.get(m.Job, parent_id)
        saved = db.get(m.Task, s.task_id)
        assert parent.data['scope']['visit'] == 0 and parent.data['scope']['attempt'] == 1
        assert dependencies.KEY not in parent.data and dependencies.ENROLL not in saved.data
        assert dependencies.REFS not in saved.data
        assert db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == dependencies.ENROLLED, m.Audit.object_id == saved.id)) is None

    with _lost_final_commit(parent_id) as lost:
        assert worker.process_once(parent_id, deliver=False, workspace_id=s.workspace_id)
    assert len(lost) == 1
    child_id = _bound_child(s, producer_status='running')
    _unissued(child_id)
    with Session(engine()) as db:
        saved = db.get(m.Task, s.task_id)
        child = db.get(m.Job, child_id)
        enrollment = copy.deepcopy(saved.data[dependencies.ENROLL])
        assert enrollment['first_job_id'] == child_id
        assert enrollment['first_scope'] == {
            'step_id': child.data['scope']['step_id'], 'visit': 1, 'attempt': 1,
            'input_hash': child.data['scope']['input_hash'], 'invocation_key': child.idempotency_key,
        }
        assert child.data[dependencies.KEY]['compact_enrollment_hash'] == enrollment['record_hash']
        assert dependencies.KEY not in db.get(m.Job, parent_id).data
        parent_receipt = copy.deepcopy(saved.data['workflow_runtime']['receipts'][0])
        child_binding = copy.deepcopy(child.data[dependencies.KEY])
        proof = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == dependencies.ENROLLED, m.Audit.object_id == saved.id))
        assert proof.data == enrollment
    before = _call_inventory(s.workspace_id)
    _expire_and_recover(parent_id, lost[0], monkeypatch)
    assert _call_inventory(s.workspace_id) == before
    call_id, output = _one_sdk(parent_id, attempts=2)
    assert call_id == parent_receipt['invocation_id'] and output == parent_receipt['output']
    with Session(engine()) as db:
        saved = db.get(m.Task, s.task_id)
        assert saved.data[dependencies.ENROLL] == enrollment
        assert db.get(m.Job, child_id).data[dependencies.KEY] == child_binding
        assert saved.data[dependencies.REFS][child_id] == child_binding['record_hash']
    assert worker.process_once(child_id, deliver=False, workspace_id=s.workspace_id)
    _one_sdk(child_id)
    final = good(complete(client, _task(client, s.task_id), note='已核对升级前原结果和升级后真实子步骤'))
    assert final['status'] == 'workflow_completed'


@pytest.fixture
def parallel_graph_seed(v7_scene, monkeypatch):
    original = graph_journey.three_step_fixture

    def engineering_parallel_design():
        template = original()
        steps = template['design']['result']['businesses'][0]['steps']
        for index in (0, 2):
            steps[index].update(agent='业务智能体', role='')
        prototype = next(node for node in template['graph']['nodes'] if node['id'] == 'b')
        for node in template['graph']['nodes']:
            if node['id'] not in {'a', 'c'}:
                continue
            node.update(kind='ability', execution=copy.deepcopy(prototype['execution']))
            node.pop('role', None)
            node.pop('output_schema', None)
        return template

    # Change the synthetic upstream design before source review, proof,
    # semantic preview/adoption, compilation or any saved Task exists.
    monkeypatch.setattr(graph_journey, 'three_step_fixture', engineering_parallel_design)
    return graph_journey.graph_seed.__wrapped__(v7_scene, monkeypatch)


def _parallel_graph_task(seed):
    client = seed['client']
    view, member, compilation = graph_journey.configured(seed)
    configuration = copy.deepcopy(view['configuration'])
    name = seed['record']['design']['result']['businesses'][0]['name']
    configuration['automation']['policies'][name]['rules']['data'] = {'mode': '自动运行'}
    save(client, configuration, review_automation=True)
    view = good(client.get('/api/setup/draft'))
    cap = tool(client, agent='business')
    command = graph_journey.graph_command(seed, member, view['configuration'], cap)
    execution = next(node['execution'] for node in command['graph']['nodes'] if node['kind'] == 'ability')
    for node in command['graph']['nodes']:
        if node['kind'] == 'ability':
            node['execution'] = copy.deepcopy(execution)
    command['node_actions'] = {name: 'data' for name in ('a', 'b', 'c')}
    path = '/api/setup/full-semantic-graphs/' + seed['record']['id']
    preview = good(client.post(path + '/preview', json=command))
    adoption = {**command, 'schema': 'report-full-semantic-graph-command-v3',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'],
        'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    adopted = good(client.post(path + '/adopt', json=adoption))
    assert good(client.post(path + '/adopt', json=adoption))['receipt'] == adopted['receipt']
    current = good(client.get('/api/setup/draft'))
    compilation.update(revision=current['revision'], role_bindings={'职责甲': member['id'], '职责乙': member['id']})
    checked = good(client.post('/api/instances/check', json=compilation))
    assert checked['general_issues'] == [], checked['general_issues']
    release = graph_journey.generate(client, compilation, checked)
    assert len(release['available_business_ids']) == 1
    instance = next(row for row in good(client.get('/api/instances')) if row['id'] == release['instance_id'])
    good(client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    entity, _ = graph_journey.create_object(client, release, release['snapshot']['object_types'][0])
    task = good(client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
        'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [entity['id']], 'title': '真实并行分支与汇合后台依赖工程核验', 'request_id': str(uuid4()),
    }), 201)
    return SimpleNamespace(client=client, workspace_id=seed['scene']['p'].workspace_id,
        task_id=task['id'], cap=cap, entity=entity)


def _graph_job(task, node_id):
    activations = [(key, value) for key, value in task['graph_runtime']['activations'].items()
        if value['node_id'] == node_id]
    assert len(activations) == 1
    return task['graph_jobs'][activations[0][0]]['job_id']


def test_real_adopted_graph_fork_join_waits_for_both_jobs_without_blocking_parallel_branch(parallel_graph_seed):
    s = _parallel_graph_task(parallel_graph_seed)
    initial = _task(s.client, s.task_id)
    assert not initial['graph_pending_humans']
    a_id, b_id = (_graph_job(initial, node) for node in ('a', 'b'))
    with _producer_final_commit(a_id):
        after_a = _task(s.client, s.task_id)
        assert len(after_a['workflow_runtime']['receipts']) == 1
        with Session(engine()) as db:
            assert db.get(m.Job, a_id).status == 'running'
            b_job = db.get(m.Job, b_id)
            assert b_job.status == 'queued' and b_job.data[dependencies.KEY]['parents'] == []
        # b shares a fork with a but has no control dependency on a's completion.
        assert worker.process_once(b_id, deliver=False, workspace_id=s.workspace_id)
        _one_sdk(b_id)
        joined = _task(s.client, s.task_id)
        c_id = _graph_job(joined, 'c')
        with Session(engine()) as db:
            saved = db.get(m.Task, s.task_id)
            child = db.get(m.Job, c_id)
            marker = copy.deepcopy(child.data[dependencies.KEY])
            assert sorted(pin['job_id'] for pin in marker['parents']) == sorted([a_id, b_id])
            assert saved.data[dependencies.REFS][c_id] == marker['record_hash']
            assert dependencies.CONTROL in saved.data
            proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
                m.Audit.action == dependencies.BOUND, m.Audit.object_id == c_id)))
            assert len(proofs) == 1 and proofs[0].data == marker
            controls = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
                m.Audit.action == dependencies.CONTROL_AUDIT, m.Audit.object_id == s.task_id)))
            assert marker['control_record_hash'] in {row.data['record_hash'] for row in controls}
            assert len(saved.data['workflow_runtime']['receipts']) == 2
        for _ in range(3):
            assert worker.claim(c_id, ['execute_full_semantic_graph_node'], workspace_id=s.workspace_id) is None
            assert worker.process_once(c_id, deliver=False, workspace_id=s.workspace_id) is False
        _unissued(c_id)
        assert not joined['completed']

    assert worker.process_once(c_id, deliver=False, workspace_id=s.workspace_id)
    _one_sdk(a_id)
    _one_sdk(c_id)
    assert worker.process_once(c_id, deliver=False, workspace_id=s.workspace_id) is False
    completed = _task(s.client, s.task_id)
    assert completed['completed'] and completed['status'] == 'workflow_completed'
    with Session(engine()) as db:
        saved = db.get(m.Task, s.task_id)
        assert saved.data['graph_runtime'] == completed['graph_runtime']
        assert len(saved.data['workflow_runtime']['receipts']) == 3
        calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id,
            m.Invocation.capability_id == s.cap['id'])))
        assert len(calls) == 3 and all(call.status == 'succeeded' for call in calls)
        assert db.get(m.Entity, s.entity['id']).data == s.entity['attributes']


def test_future_graph_child_cannot_become_legacy_by_deleting_binding_but_retaining_control(parallel_graph_seed):
    s = _parallel_graph_task(parallel_graph_seed)
    initial = _task(s.client, s.task_id)
    a_id, b_id = (_graph_job(initial, node) for node in ('a', 'b'))
    assert worker.process_once(a_id, deliver=False, workspace_id=s.workspace_id)
    assert worker.process_once(b_id, deliver=False, workspace_id=s.workspace_id)
    joined = _task(s.client, s.task_id)
    c_id = _graph_job(joined, 'c')
    _unissued(c_id)
    with Session(engine()) as db:
        task = db.get(m.Task, s.task_id)
        child = db.get(m.Job, c_id)
        control = copy.deepcopy(task.data[dependencies.CONTROL])
        controls = sorted((row.id, digest(row.data)) for row in db.scalars(select(m.Audit).where(
            m.Audit.workspace_id == s.workspace_id, m.Audit.action == dependencies.CONTROL_AUDIT,
            m.Audit.object_id == task.id)))
        assert controls
        _delete_child_binding(db, task, child)
        db.commit()
    before = _call_inventory(s.workspace_id)
    assert worker.process_once(c_id, deliver=False, workspace_id=s.workspace_id)
    blocked = _task(s.client, s.task_id)
    transport = next(value for value in blocked['graph_jobs'].values() if value['job_id'] == c_id)
    assert transport['phase'] == 'blocked' and transport['reason']
    assert not blocked['completed']
    assert _call_inventory(s.workspace_id) == before
    with Session(engine()) as db:
        task = db.get(m.Task, s.task_id)
        child = db.get(m.Job, c_id)
        assert child.status == 'failed' and child.attempts == 1
        assert task.data[dependencies.CONTROL] == control
        assert sorted((row.id, digest(row.data)) for row in db.scalars(select(m.Audit).where(
            m.Audit.workspace_id == s.workspace_id, m.Audit.action == dependencies.CONTROL_AUDIT,
            m.Audit.object_id == task.id))) == controls
        assert db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == s.workspace_id,
            m.Invocation.idempotency_key == child.idempotency_key)) is None
    _one_sdk(a_id)
    _one_sdk(b_id)


def test_graph_completed_handler_recovers_original_job_against_bound_historical_control(parallel_graph_seed, monkeypatch):
    s = _parallel_graph_task(parallel_graph_seed)
    initial = _task(s.client, s.task_id)
    a_id, b_id = (_graph_job(initial, node) for node in ('a', 'b'))
    with Session(engine()) as db:
        original_a_binding = copy.deepcopy(db.get(m.Job, a_id).data[dependencies.KEY])
    with _lost_final_commit(a_id) as lost:
        assert worker.process_once(a_id, deliver=False, workspace_id=s.workspace_id)
    assert len(lost) == 1
    after_a = _task(s.client, s.task_id)
    a_receipt = next(row for row in after_a['workflow_runtime']['receipts'] if row['request_id'] == a_id)
    assert worker.process_once(b_id, deliver=False, workspace_id=s.workspace_id)
    _one_sdk(b_id)
    joined = _task(s.client, s.task_id)
    c_id = _graph_job(joined, 'c')
    _unissued(c_id)
    with Session(engine()) as db:
        task = db.get(m.Task, s.task_id)
        original_c_binding = copy.deepcopy(db.get(m.Job, c_id).data[dependencies.KEY])
        assert sorted(pin['job_id'] for pin in original_c_binding['parents']) == sorted([a_id, b_id])
        assert task.data[dependencies.CONTROL]['record_hash'] != original_a_binding['control_record_hash']
        assert db.get(m.Job, a_id).status == 'running'
        assert db.get(m.Invocation, a_receipt['invocation_id']).status == 'succeeded'
    before = _call_inventory(s.workspace_id)
    _expire_and_recover(a_id, lost[0], monkeypatch)
    assert _call_inventory(s.workspace_id) == before
    call_id, output = _one_sdk(a_id, attempts=2)
    assert call_id == a_receipt['invocation_id'] and output == a_receipt['output']
    with Session(engine()) as db:
        task = db.get(m.Task, s.task_id)
        assert db.get(m.Job, a_id).data[dependencies.KEY] == original_a_binding
        assert db.get(m.Job, c_id).data[dependencies.KEY] == original_c_binding
        proof = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == dependencies.BOUND, m.Audit.object_id == c_id))
        assert proof.data == original_c_binding
        assert task.data[dependencies.REFS][c_id] == original_c_binding['record_hash']
    assert worker.process_once(c_id, deliver=False, workspace_id=s.workspace_id)
    _one_sdk(c_id)
    final = _task(s.client, s.task_id)
    assert final['completed'] and final['status'] == 'workflow_completed'
    assert len(final['workflow_runtime']['receipts']) == 3


@native_pytestmark
def test_real_native_finalize_barrier_then_independent_late_ack_preserves_waiting_sdk(flow_scene):
    s = flow_scene
    meta = configured_flow(s)
    task, entity = create_task(s, meta.release)
    parent_id = task['workflow_runtime']['automatic']['job_id']
    probe = SimpleNamespace(client=s.client, workspace_id=s.workspace_id, task_id=task['id'], parent_id=parent_id)
    with _producer_final_commit(parent_id):
        child_id = _bound_child(probe, producer_status='running')
        for _ in range(3):
            assert worker.claim(child_id, [KIND], workspace_id=s.workspace_id) is None
            assert worker.process_once(child_id, deliver=False, workspace_id=s.workspace_id) is False
        _unissued(child_id)
        with Session(engine()) as db:
            run = db.scalar(select(m.WorkflowPixelRun).where(m.WorkflowPixelRun.workspace_id == s.workspace_id,
                m.WorkflowPixelRun.job_id == parent_id))
            assert run is not None and run.status == 'succeeded'
            assert run.invocation_id and db.get(m.Invocation, run.invocation_id).status == 'succeeded'

    # Producer completion is not an independent data ACK. The real read-only
    # SDK may truthfully save waiting; no automatic rewrite is permitted later.
    entry = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/pixel-results'))['records'][0]
    assert entry['reception']['state'] == 'waiting_data'
    assert worker.process_once(child_id, deliver=False, workspace_id=s.workspace_id)
    data_call, waiting = _one_sdk(child_id)
    assert waiting['registered_count'] == waiting['waiting_count'] == 1
    assert waiting['received_count'] == 0 and waiting['all_registered_received'] is False
    saved = _task(s.client, task['id'])
    data_receipt = next(r for r in saved['workflow_runtime']['receipts'] if r['step_id'] == meta.data['id'])
    assert data_receipt['invocation_id'] == data_call and data_receipt['output'] == waiting

    worker.deliver_events(workspace_id=s.workspace_id,
        observation_id=entry['reception']['observation_id'], event_kind='observation.data')
    received = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/pixel-results'))['records'][0]
    assert received['reception']['state'] == 'received' and received['reception']['memory_id']
    with Session(engine()) as db:
        assert db.get(m.Invocation, data_call).data['output'] == waiting
        observation = db.get(m.Observation, entry['reception']['observation_id'])
        delivered = db.get(m.Event, received['reception']['event_id'])
        assert delivered.status == 'received' and delivered.received_at is not None
        assert 'data_reception' in observation.data

    saved = _task(s.client, task['id'])
    business_id = saved['workflow_runtime']['automatic']['job_id']
    assert worker.process_once(business_id, deliver=False, workspace_id=s.workspace_id)
    _one_sdk(business_id)
    saved = _task(s.client, task['id'])
    business = next(r for r in saved['workflow_runtime']['receipts'] if r['step_id'] == meta.business['id'])
    assert business['input_snapshot'] == {'attributes': waiting}
    assert business['output']['attributes'] == waiting
    assert saved['status'] == 'workflow_waiting' and saved['workflow_runtime']['current_step_id'] == meta.human['id']
    final = good(complete(s.client, saved, note='已核对真实收件；行政等待快照不是现场判断'))
    assert final['status'] == 'workflow_completed'
    with Session(engine()) as db:
        assert db.get(m.Invocation, data_call).data['output'] == waiting
        assert db.get(m.Entity, entity['id']).data == entity['attributes']
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id)))
