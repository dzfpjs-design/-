"""Actual worker publication and precise native ASR handoff; Root alone runs.

The commit observer creates a second real DB/worker session. It never changes
authority, returns a model result, or bypasses a lease. Provider fixtures used by
the graph source-design seed end before actual local ASR execution.
"""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.security import Principal
from generic_agents.worker import process_once
from generic_agents.worker_native_audio_v136 import KIND, enqueue_completed
from tests.test_workflow_audio import configured
from tests.test_workflow_abilities import finish_job, conf, enable
from tests.test_instance_workspace import prepared, object_row, save
from tests.test_vertical_slice import manifest, publish
from tests.test_full_semantic_graph_audio_journey import (
    audio_graph_seed, audio_command, v7_scene, reading_scene, local_runtime, full_source_scene,
)


def good(response, status=200):
    assert response.status_code == status, response.text[:400]
    return response.json()


def prepare_audio_task(client):
    source, cap, release, attached, body = configured(client)
    base = '/api/workspace/instances/' + release['instance_id']
    task = good(client.post(base + '/tasks', json=body), 201)
    return {'client': client, 'source': source, 'cap': cap, 'release': release,
        'attached': attached, 'body': body, 'base': base, 'task': task,
        'job_id': task['workflow_runtime']['automatic']['job_id']}


def exact_child(db, processing_id):
    rows = list(db.scalars(select(m.Job).where(m.Job.idempotency_key == 'native-audio-sync-v136:' + processing_id)))
    assert len(rows) == 1
    assert rows[0].kind == KIND
    return rows[0]


def inventory(db):
    return {model.__tablename__: set(db.scalars(select(model.id)))
        for model in (m.AudioRun, m.Invocation, m.CloudCall, m.Observation)}


def test_actual_success_and_exact_child_become_visible_atomically(client):
    value = prepare_audio_task(client); processing_id = value['job_id']
    seen = {}

    def after_commit(_session):
        if seen:
            return
        with Session(engine()) as other:
            child = other.scalar(select(m.Job).where(m.Job.idempotency_key ==
                'native-audio-sync-v136:' + processing_id))
            if child is None:
                return
            parent = other.get(m.Job, processing_id)
            # Record before calling the real child worker to avoid recursive
            # observation of its commits. This is not a timing sleep oracle.
            audits = list(other.scalars(select(m.Audit).where(m.Audit.object_id == child.id,
                m.Audit.action == 'native_audio.sync_queued_v136')))
            seen.update(child_id=child.id, original_status=parent.status,
                audit_data=[copy.deepcopy(row.data) for row in audits],
                command={key: child.data[key] for key in
                    ('actor_id', 'run_id', 'task_id', 'activation_id', 'processing_job_id')})
        seen['processed'] = process_once(seen['child_id'], deliver=False)

    event.listen(Session, 'after_commit', after_commit)
    try:
        task = finish_job(client, value['task'])
    finally:
        event.remove(Session, 'after_commit', after_commit)
    assert seen and seen['original_status'] == 'succeeded' and seen['processed'] is True
    assert seen['audit_data'] == [{'schema': 'native-audio-sync-command-v136',
        'key': 'native-audio-sync-v136:' + processing_id, 'command': seen['command']}]
    receipt = task['workflow_runtime']['receipts'][0]
    with Session(engine()) as db:
        child = exact_child(db, processing_id)
        assert child.id == seen['child_id'] and child.status == 'succeeded'
        expected = {'actor_id': receipt['actor'], 'run_id': receipt['output']['audio_run_id'],
            'task_id': task['id'], 'activation_id': None, 'processing_job_id': processing_id}
        assert all(key in child.data and child.data[key] == val for key, val in expected.items())
        assert child.data['result']['created'] == 1
        assert db.get(m.Job, processing_id).status == 'succeeded'
        assert not list(db.scalars(select(m.Job.id).where(m.Job.kind == 'sync_native_receipts',
            m.Job.data['original_task_id'].as_string() == task['id'])))
        before = inventory(db)
    assert process_once(seen['child_id'], deliver=False) is False
    me = good(client.get('/api/me'))
    p = Principal(me['user_id'], me['workspace_id'], me['role'], 'worker-native-audio-test')
    with Session(engine()) as db:
        assert enqueue_completed(db, p, db.get(m.Job, processing_id)) is True
        assert enqueue_completed(db, p, db.get(m.Job, processing_id)) is True
        db.commit()
    with Session(engine()) as db:
        assert inventory(db) == before
        assert exact_child(db, processing_id).id == seen['child_id']


def test_actual_child_rejects_unsettled_original_without_losing_saved_asr(client):
    value = prepare_audio_task(client); task = finish_job(client, value['task'])
    with Session(engine()) as db:
        child = exact_child(db, value['job_id']); child_id = child.id
        run_id = task['workflow_runtime']['receipts'][0]['output']['audio_run_id']
        run = db.get(m.AudioRun, run_id)
        original = copy.deepcopy(run.data); before = inventory(db)
        db.get(m.Job, value['job_id']).status = 'running'
        db.commit()
    assert process_once(child_id, deliver=False)
    with Session(engine()) as db:
        assert db.get(m.Job, child_id).status == 'failed'
        assert db.get(m.AudioRun, run_id).status == 'succeeded'
        assert db.get(m.AudioRun, run_id).data == original
        assert inventory(db) == before


def test_actual_child_cannot_substitute_another_successful_run(client):
    value = prepare_audio_task(client); first = finish_job(client, value['task'])
    second_body = {**value['body'], 'request_id': str(uuid4()), 'title': '另一实际语音任务'}
    second = finish_job(client, good(client.post(value['base'] + '/tasks', json=second_body), 201))
    first_run = first['workflow_runtime']['receipts'][0]['output']['audio_run_id']
    second_run = second['workflow_runtime']['receipts'][0]['output']['audio_run_id']
    assert first_run != second_run
    with Session(engine()) as db:
        child = exact_child(db, value['job_id']); child_id = child.id
        other_child = exact_child(db, second['workflow_runtime']['receipts'][0]['request_id'])
        queue_audits = {row.id: copy.deepcopy(row.data) for row in db.scalars(select(m.Audit).where(
            m.Audit.action == 'native_audio.sync_queued_v136', m.Audit.object_id.in_([child.id, other_child.id])))}
        assert len(queue_audits) == 2
        original = {identity: copy.deepcopy(db.get(m.AudioRun, identity).data) for identity in (first_run, second_run)}
        before = inventory(db)
        # All target values are individually real and mutually consistent.
        # Remove the other child to permit a fully self-consistent key rewrite;
        # both actual queue Audits remain unchanged and must reject borrowing.
        foreign_key = other_child.idempotency_key
        db.delete(other_child); db.flush()
        child.idempotency_key = foreign_key
        child.data = {**child.data, 'run_id': second_run, 'task_id': second['id'],
            'activation_id': None,
            'processing_job_id': second['workflow_runtime']['receipts'][0]['request_id']}
        db.commit()
    assert process_once(child_id, deliver=False)
    with Session(engine()) as db:
        assert db.get(m.Job, child_id).status == 'failed'
        assert inventory(db) == before
        assert {identity: db.get(m.AudioRun, identity).data for identity in original} == original
        assert {identity: db.get(m.Audit, identity).data for identity in queue_audits} == queue_audits


def test_final_queue_conflict_preserves_actual_result_and_never_fakes_success(client):
    value = prepare_audio_task(client)
    with Session(engine()) as db:
        original = db.get(m.Job, value['job_id'])
        collision = m.Job(workspace_id=original.workspace_id, kind='check_workspace_data_need',
            idempotency_key='native-audio-sync-v136:' + original.id, data={'corrupt_queue_fixture': True})
        db.add(collision); db.commit(); collision_id = collision.id
    with pytest.raises(HTTPException):
        process_once(value['job_id'], deliver=False)
    with Session(engine()) as db:
        original = db.get(m.Job, value['job_id'])
        assert original.status == 'running'
        task = db.get(m.Task, value['task']['id'])
        receipt = task.data['workflow_runtime']['receipts'][0]
        run = db.get(m.AudioRun, receipt['output']['audio_run_id'])
        call = db.get(m.Invocation, receipt['invocation_id'])
        assert run.status == call.status == 'succeeded'
        assert run.data['result']['transcription']['inference_performed'] is True
        assert call.data['output'] == receipt['output']
        assert run.data['result'] == receipt['output']['audio_transcription']
        assert not list(db.scalars(select(m.CloudCall.id)))
        assert not list(db.scalars(select(m.Observation.id)))
        assert db.get(m.Job, collision_id).data == {'corrupt_queue_fixture': True}


@pytest.fixture
def same_named_result_service(monkeypatch):
    """An actual owned HTTP service, not a patched gateway response."""
    state = {'calls': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'] += 1
            value = json.dumps({'audio_run_id': 'application-reference-not-a-native-run'}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(value))); self.end_headers(); self.wfile.write(value)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    state['endpoint'] = 'http://127.0.0.1:' + str(server.server_address[1]) + '/result'
    monkeypatch.setattr(settings(), 'development_connector_urls', state['endpoint'])
    try:
        yield state
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_non_asr_actual_http_result_with_same_named_field_keeps_original_path(client, same_named_result_service):
    service = same_named_result_service
    cap = publish(client, manifest('tool', name='unrelated_audio_reference', allowed_agent_roles=['data'],
        specification={}, input_schema={'type': 'object', 'required': ['records', 'field', 'operation'],
            'properties': {'records': {'type': 'array'}, 'field': {'type': 'string'}, 'operation': {'type': 'string'}}},
        output_schema={'type': 'object', 'required': ['audio_run_id'], 'additionalProperties': False,
            'properties': {'audio_run_id': {'type': 'string'}}},
        execution={'adapter': 'http', 'endpoint': service['endpoint'], 'side_effect': False,
            'input_bindings': {'records': 'archive_records'}}, data_policy={'external_allowed': True}))
    configuration = conf(cap); flow = configuration['flows']['资料核验']
    flow['steps'][0]['execution']['allow_external'] = True
    flow['confirmedSignature'] = json.dumps(signature_value(configuration, flow), ensure_ascii=False)
    release, _, check = prepared(client, configuration)
    assert release['available_business_ids'], check
    enable(client, release); obj = object_row(client, release, index=1)
    body = {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [obj['id']], 'title': '其它能力的同名业务字段', 'request_id': str(uuid4())}
    task = finish_job(client, good(client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json=body), 201))
    receipt = task['workflow_runtime']['receipts'][0]
    assert receipt['output'] == {'audio_run_id': 'application-reference-not-a-native-run'}
    assert service['calls'] == 1 and task['status'] == 'workflow_waiting'
    with Session(engine()) as db:
        assert db.get(m.Job, receipt['request_id']).status == 'succeeded'
        assert not list(db.scalars(select(m.AudioRun.id)))
        assert not list(db.scalars(select(m.Job.id).where(m.Job.kind == KIND)))
        assert not list(db.scalars(select(m.CloudCall.id)))


def test_real_graph_audio_completion_has_one_exact_activation_child(audio_graph_seed):
    from tests.test_full_semantic_instance_journey_v113 import configured as graph_configuration, generate, create_object
    from tests.test_audio import upload
    seed = audio_graph_seed; client = seed['client']
    source = good(upload(client), 201); assert process_once(source['job_id'], deliver=False)
    source = good(client.get('/api/audio/' + source['id']))
    capability = good(client.post('/api/audio/' + source['id'] + '/register-business', json={'version': source['version']}))
    view, member, compilation = graph_configuration(seed)
    name = seed['record']['design']['result']['businesses'][0]['name']
    configuration = copy.deepcopy(view['configuration'])
    configuration['automation']['policies'][name]['rules']['observe'] = {'mode': '自动运行'}
    save(client, configuration, review_automation=True)
    view = good(client.get('/api/setup/draft'))
    command = audio_command(seed, member, view['configuration'], capability['capability_id'])
    path = '/api/setup/full-semantic-graphs/' + seed['record']['id']
    preview = good(client.post(path + '/preview', json=command))
    good(client.post(path + '/adopt', json={**command, 'schema': 'report-full-semantic-graph-command-v3',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True,
        'semantic_mode': 'workspace_workflow_design'}))
    current = good(client.get('/api/setup/draft'))
    compilation.update(revision=current['revision'], role_bindings={'职责甲': member['id'], '职责乙': member['id']})
    checked = good(client.post('/api/instances/check', json=compilation))
    assert checked['general_issues'] == []
    release = generate(client, compilation, checked); enable(client, release)
    base = '/api/workspace/instances/' + release['instance_id']
    candidate = next(item for item in good(client.get(base + '/audio-candidates')) if item['source_run_id'] == source['id'])
    attached = good(client.post(base + '/input-audio', json={'source_run_id': source['id'],
        'source_snapshot_hash': candidate['snapshot_hash'], 'request_id': str(uuid4())}), 201)
    obj, _ = create_object(client, release, release['snapshot']['object_types'][0])
    task = good(client.post(base + '/tasks', json={'release_id': release['id'],
        'business_id': release['available_business_ids'][0], 'entity_ids': [obj['id']],
        'audio_ids': [attached['id']], 'title': '实际完整图原生语音精确归集', 'request_id': str(uuid4())}), 201)
    activation_id, transport = next(iter(task['graph_jobs'].items()))
    assert process_once(transport['job_id'], deliver=False)
    task = good(client.get('/api/workspace/tasks/' + task['id']))
    receipt = next(item for item in task['workflow_runtime']['receipts'] if item.get('activation_id') == activation_id)
    with Session(engine()) as db:
        child = exact_child(db, transport['job_id']); identity = child.id
        assert child.data['activation_id'] == activation_id
        assert child.data['run_id'] == receipt['output']['audio_run_id']
        assert db.get(m.Job, transport['job_id']).status == 'succeeded'
        assert task['graph_jobs'][activation_id]['phase'] == 'succeeded'
        assert child.data['task_id'] == task['id']
    assert process_once(identity, deliver=False)
    with Session(engine()) as db:
        assert db.get(m.Job, identity).status == 'succeeded'
        assert db.get(m.Job, identity).data['result']['created'] == 1
    assert process_once(identity, deliver=False) is False
