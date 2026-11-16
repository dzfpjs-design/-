"""Manual capture anchors use a real disposable PG; capture/model fixtures are simulated."""
import copy
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit, Job, Membership, StreamSession
from generic_agents.security import Principal
from generic_agents.worker import process_once
from generic_agents import manual_video_provenance_v348 as manual
from generic_agents import native_video_provenance_v164 as origin
from tests.test_media import media_runtime, capture
from tests.test_local_specialists import mocked_runtime
from tests.test_instance_perception import linked


def principal(db, account):
    membership = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
    return Principal(membership.user_id, account[2], membership.role, 'test')


def setup_capture(client):
    installation, channel, release, obj, _, _ = linked(client)
    response = capture(client, channel, installation)
    assert response.status_code == 201, response.text
    return response.json(), release, obj


def test_manual_queue_records_declaration_and_unbound_validation_is_not_upgraded(client, media_runtime, account):
    queued, _, _ = setup_capture(client)
    with Session(engine()) as db:
        p = principal(db, account); session = db.get(StreamSession, queued['id']); job = db.get(Job, queued['job_id'])
        assert manual.has_declaration(db, p, session)
        ctx = manual.checked_declaration(db, p, session, job, live=True)
        assert ctx['frozen_scope']['capture_mode'] == 'manual'
        assert ctx['frozen_scope']['watch_id'] is None and ctx['frozen_scope']['plan_id'] is None
        assert ctx['authority_pin']['reservation_receipt']['authority']['raw_media_cloud_allowed'] is False
        previous = next(s for s in db.scalars(select(StreamSession).where(StreamSession.workspace_id == p.workspace_id)) if s.id != session.id)
        assert not manual.has_declaration(db, p, previous)
        assert not origin.has_input(db, p, previous.id)


@pytest.mark.parametrize('damage', ['session_marker', 'job_marker', 'declaration', 'request', 'wrong_actor', 'old_source_version'])
def test_independent_declaration_and_both_markers_prevent_silent_downgrade(client, media_runtime, account, damage):
    queued, _, _ = setup_capture(client)
    with Session(engine()) as db:
        p = principal(db, account); session = db.get(StreamSession, queued['id']); job = db.get(Job, queued['job_id'])
        anchor = db.scalar(select(Audit).where(Audit.object_id == session.id, Audit.action == manual.DECL_ACTION))
        if damage == 'session_marker': session.data = {k:v for k,v in session.data.items() if k != manual.MARKER}
        elif damage == 'job_marker': job.data = {k:v for k,v in job.data.items() if k != manual.MARKER}
        elif damage == 'declaration': db.delete(anchor)
        elif damage == 'request': session.data = {**session.data, 'seconds': session.data['seconds'] + 1}
        elif damage == 'old_source_version':
            from generic_agents.models import Capability
            source = db.get(Capability, session.data['domain_binding']['source_id'])
            from generic_agents.contracts import digest
            source.data = {**source.data, 'execution': {**source.data['execution'], 'capture_hash': '0' * 64}}
            source.content_hash = source.validated_hash = digest(source.data)
        else: job.data = {**job.data, 'actor_id': '00000000-0000-4000-8000-000000000999'}
        db.flush()
        assert manual.has_declaration(db, p, session)
        with pytest.raises(HTTPException): manual.checked_declaration(db, p, session, job, live=True)
        db.rollback()


def test_frozen_input_is_durable_before_capture_and_cannot_be_reused_by_new_lease(client, media_runtime, account, monkeypatch):
    queued, _, _ = setup_capture(client)
    from generic_agents import media
    original = media.source_state
    def check_at_first_external_read(gateway, channel):
        with Session(engine()) as other:
            p = principal(other, account)
            anchors = origin.input_audits(other, p, queued['id'])
            assert len(anchors) == 1 and anchors[0].data['schema'] == manual.INPUT_SCHEMA
        return original(gateway, channel)
    monkeypatch.setattr(media, 'source_state', check_at_first_external_read)
    assert process_once(queued['job_id'], deliver=False)
    with Session(engine()) as db:
        p = principal(db, account); session = db.get(StreamSession, queued['id']); job = db.get(Job, queued['job_id'])
        assert session.status == 'captured' and job.status == 'succeeded'
        assert origin._input_context(db, p, session)['frozen_scope']['capture_mode'] == 'manual'
        with pytest.raises(HTTPException): manual.freeze(db, p, session, job)


def test_new_manual_video_issues_and_receives_its_own_data_packet(client, media_runtime, account, monkeypatch):
    from generic_agents.models import Observation, VideoRun
    from generic_agents.worker import deliver_events
    from generic_agents.instance_data_receipts import observation_receipt
    from tests.test_media import finish
    from generic_agents import local_video
    original_execute = local_video.execute_video
    def complete_fixture(content, backend, configuration, checkpoints, guard, receive, lock_fd=None):
        def enriched(value, temp):
            if value.get('kind') == 'complete':
                value = {**value, 'tracking_identity': 'session_and_segment_only', 'sample_hz': configuration['sample_hz']}
            return receive(value, temp)
        return original_execute(content, backend, configuration, checkpoints, guard, enriched, lock_fd)
    monkeypatch.setattr(local_video, 'execute_video', complete_fixture)
    queued, release, obj = setup_capture(client)
    session, video = finish(client, queued)
    assert video['status'] == 'succeeded', video.get('error')
    candidate_id = video['alerts'][0]['task_id']
    path = '/api/workspace/tasks/' + candidate_id + '/video-data-reception'
    pending = client.get(path)
    assert pending.status_code == 200, pending.text
    assert pending.json()['state'] == 'pending' and not pending.json()['data_reception_verified']
    from generic_agents.models import Task
    from generic_agents.video_workflow_handoff_v347 import _origin
    with Session(engine()) as db:
        original_basis = _origin(db, principal(db, account), db.get(Task, candidate_id))['basis_hash']
    with Session(engine()) as db:
        p = principal(db, account)
        run = db.get(VideoRun, video['id'])
        process_job = db.get(Job, run.data['job_id'])
        if process_job.status != 'succeeded': origin._run_context(db, p, run)
        assert process_job.status == 'succeeded', str(process_job.data.get('failure_diagnostic_v289', {}).get('diagnostic', {}).get('frames'))
        assert len(origin._audits(db, p, origin.RESULT_ACTION, run.id)) == 1
        child = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.kind == 'sync_native_receipts', Job.data['original_video_run_id'].as_string() == run.id))
        assert child is not None and child.status == 'queued'
        child_id = child.id
    assert process_once(child_id, deliver=False)
    with Session(engine()) as db:
        p = principal(db, account); child = db.get(Job, child_id)
        assert child.status == 'succeeded', child.data.get('error')
        rows = list(db.scalars(select(Observation).where(Observation.workspace_id == p.workspace_id,
            Observation.source_record_id == 'VideoRun:' + video['id'] + ':' + obj['id'])))
        assert len(rows) == 1
        obs_id = rows[0].id
    assert client.get(path).json()['state'] == 'pending'
    deliver_events(workspace_id=account[2], observation_id=obs_id)
    with Session(engine()) as db:
        p = principal(db, account); observation = db.get(Observation, obs_id)
        reception = observation_receipt(db, p, observation)['data_reception']
        assert reception['capture_mode'] == 'manual' and reception['source_time_verified'] is False
        assert reception['supplement_plan_id'] is None and reception['automatic_watch_id'] is None
        assert reception['entity_id'] == obj['id'] and reception['release_id'] == release['id']
    received = client.get(path)
    assert received.status_code == 200, received.text
    assert received.json()['state'] == 'received' and received.json()['data_reception_verified']
    assert received.json()['observation_id'] == obs_id
    review_path = '/api/workspace/instances/' + release['instance_id'] + '/native-video-reviews/observations/' + obs_id
    review = client.get(review_path)
    assert review.status_code == 200, review.text
    assert review.json()['source_available'] and review.json()['can_save_draft']
    frame_id = review.json()['frames'][0]['id']
    saved = client.post(review_path + '/draft', json={'request_id':'manual_review_original_349',
        'expected_version':0, 'note':'对照画面记录测试意见，尚未确认现场状态', 'frame_ids':[frame_id], 'decision':'uncertain'})
    assert saved.status_code == 200, saved.text
    cold = client.get(review_path)
    assert cold.status_code == 200 and cold.json()['draft']['note'] == '对照画面记录测试意见，尚未确认现场状态'
    assert cold.json()['version'] == 1 and client.get(path).json()['state'] == 'received'
    from generic_agents.models import Memory, Task
    from generic_agents.video_workflow_handoff_v347 import task_reception
    with Session(engine()) as db:
        p = principal(db, account); task = db.get(Task, candidate_id)
        memory = db.scalar(select(Memory).where(Memory.workspace_id == p.workspace_id,
            Memory.data['type'].as_string() == 'observation_evidence',
            Memory.data['observation_id'].as_string() == obs_id))
        assert memory is not None
        original = copy.deepcopy(memory.data)
        memory.data = {**memory.data, 'data_reception': {'tampered': True}}; db.flush()
        value = task_reception(db, p, task, {'execution_mode':'confirmed_graph_workflow'})
        assert value['state'] == 'integrity_problem' and not value['data_reception_verified']
        db.rollback()
        assert db.get(Memory, memory.id).data == original
    from generic_agents.models import Event
    from generic_agents.video_workflow_handoff_v347 import _origin
    from generic_agents.video_reception_status_v349 import status as reception_status
    with Session(engine()) as db:
        p = principal(db, account); task = db.get(Task, candidate_id)
        basis = _origin(db, p, task); before = copy.deepcopy(task.data)
        assert basis['basis_hash'] == original_basis
        assert reception_status(db, p, basis)['state'] == 'received'
        obs = db.get(Observation, obs_id)
        event = db.scalar(select(Event).where(Event.workspace_id == p.workspace_id,
            Event.kind == 'observation.data', Event.idempotency_key == 'data:' + obs.id))
        memory = db.scalar(select(Memory).where(Memory.workspace_id == p.workspace_id,
            Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence'))
        db.delete(memory); obs.data = {k:v for k,v in obs.data.items() if k != 'data_reception'}
        event.data = {k:v for k,v in event.data.items() if k != 'data_reception'}
        event.status = 'blocked_delivery'; event.received_at = None; db.flush()
        assert reception_status(db, p, basis)['state'] == 'blocked'
        assert task.data == before and _origin(db, p, task)['basis_hash'] == basis['basis_hash']
        db.rollback()
    with Session(engine()) as db:
        p = principal(db, account); task = db.get(Task, candidate_id); basis = _origin(db, p, task)
        obs = db.get(Observation, obs_id)
        duplicate = Observation(workspace_id=p.workspace_id, source_id=obs.source_id,
            source_record_id=obs.source_record_id, processing_version='duplicate_349',
            entity_id=obs.entity_id, observed_at=obs.observed_at, business_day=obs.business_day,
            status=obs.status, data=copy.deepcopy(obs.data))
        db.add(duplicate); db.flush()
        assert reception_status(db, p, basis)['state'] == 'integrity_problem'
        db.rollback()
    from types import SimpleNamespace
    from generic_agents import video_workflow_handoff_v347 as handoff
    with Session(engine()) as db:
        p = principal(db, account); source = db.get(Task, candidate_id)
        ctx = handoff.context(db, p, source, {'execution_mode':'confirmed_graph_workflow'})
        selected = handoff.Selection(source_task_id=source.id, source_task_version=source.version, basis_hash=ctx['basis_hash'])
        source, fixed = handoff.prepare(db, p, selected, release['instance_id'],
            SimpleNamespace(release_id=release['id'], business_id=release['available_business_ids'][0], entity_ids=[obj['id']]),
            {'execution_mode':'confirmed_graph_workflow'})
        child = Task(workspace_id=p.workspace_id, owner_id=p.user_id, package_id=source.package_id,
            title='测试审批关联', status='approval_draft', data={'creation_hash':'f'*64,
            'instance_id':fixed['instance_id'],'instance_release_id':fixed['release_id'],
            'business_id':fixed['business_id'],'entity_ids':[fixed['entity_id']]})
        db.add(child); db.flush(); handoff.attach(db,p,child,source,fixed,'f'*64)
        assert handoff.task_reception(db,p,source,{'execution_mode':'confirmed_graph_workflow'})['state'] == 'received'
        assert handoff.task_reception(db,p,child,{'execution_mode':'confirmed_graph_workflow'})['state'] == 'received'
        assert fixed['basis_hash'] == original_basis
        db.rollback()


def test_verified_native_video_receipt_is_dispatched_before_unrelated_simulation(monkeypatch):
    from types import SimpleNamespace
    from generic_agents import instance_data_receipts as receiver, native_sound_receipts, native_pixel_receipts_v250
    from generic_agents import native_video_reception_v164, simulation_observation_origin_v177
    p = SimpleNamespace(workspace_id='w'); observation = SimpleNamespace(workspace_id='w'); event = object()
    monkeypatch.setattr(native_sound_receipts, 'handles', lambda *_: False)
    monkeypatch.setattr(native_pixel_receipts_v250, 'handles', lambda *_: False)
    monkeypatch.setattr(native_video_reception_v164, 'handles', lambda *_: True)
    monkeypatch.setattr(native_video_reception_v164, 'receipt', lambda *_: {'verified': True})
    def unrelated_guard(*_args, **_kwargs): raise AssertionError('unrelated sample guard must not claim an independently issued video')
    monkeypatch.setattr(simulation_observation_origin_v177, 'guard', unrelated_guard)
    assert receiver._receipt(None, p, event, observation) == {'verified': True}


def test_video_destination_requires_independent_checked_origin(monkeypatch):
    from types import SimpleNamespace
    from generic_agents import instance_data_receipts as receiver
    entity = SimpleNamespace(id='entity'); release = object()
    observation = SimpleNamespace(entity_id='entity'); p = object()
    monkeypatch.setattr(receiver, 'scoped', lambda *_args, **_kwargs: entity)
    monkeypatch.setattr(origin, 'has_issued', lambda *_: True)
    monkeypatch.setattr(origin, 'checked_observation', lambda *_: {'entity': entity, 'release': release})
    assert receiver._destination(None, p, observation) == (release, entity, [])
    def reject(*_args, **_kwargs): raise HTTPException(409, 'invalid proof')
    monkeypatch.setattr(origin, 'checked_observation', reject)
    with pytest.raises(HTTPException): receiver._destination(None, p, observation)
