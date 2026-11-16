"""Genuine saved QA -> one normal capture -> independent ACK -> new frame QA.

The inherited V233 fixture has explicit transport/local-model and graph-trial
eligibility seams. Capture/processing Jobs, Frames, per-frame receivers, Daily
publication/ACK, QA workers and command transactions are real production paths.
No successful Job, Frame, receipt, object binding or Task is fabricated here.
These tests do not establish camera, scene-time or domain-model accuracy.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, media_daily_delivery as delivery, video_daily
from generic_agents import frame_supplement_v236 as service
from generic_agents import frame_supplement_source_v236 as source
from generic_agents import daily_frame_assessment_source_v233 as original_source
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.security import Principal, password_hash
from generic_agents.worker import process_once, run_setup_job
from tests.test_daily_frame_assessment_api_v233 import create as create_qa, finish as finish_qa
from tests.test_daily_frame_assessment_source_v233 import (
    daily_frame_source, daily_scene, daily_camera_source, media_runtime, mocked_runtime,
)
from tests.test_media import finish as finish_capture
from tests.test_media_daily_task_pg_v226 import reconfirm_camera
from tests.test_supplement import explicit_no_alert_fixture


def raw(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def counters(db, case):
    return {cls.__tablename__: db.scalar(select(func.count()).select_from(cls).where(
        cls.workspace_id == case.p.workspace_id)) for cls in
        (m.Task, m.StreamSession, m.VideoReadReservation, m.VideoRun, m.EvidenceAssessment, m.Job, m.CloudCall)}


def original_rows(db, case):
    parent = case.case
    identities = (
        (m.Task, case.task_id), (m.EvidenceAssessment, case.assessment_id),
        (m.Evidence, parent.image_id), (m.Evidence, parent.origin['frame']['evidence_id']),
        (m.VideoFrame, parent.origin['frame']['id']),
        (m.StreamSession, parent.scene.source.session_id), (m.VideoRun, parent.scene.source.run_id),
        (m.Event, parent.origin['event_id']), (m.Memory, parent.origin['ack_memory_id']),
    )
    result = {}
    for cls, identity in identities:
        row = db.get(cls, identity)
        assert row is not None
        result[(cls.__tablename__, identity)] = {column.name: copy.deepcopy(getattr(row, column.name))
            for column in row.__table__.columns if not column.info.get('typed_payload_internal')}
    return result


def propose_body(case):
    return {'schema': 'frame-supplement-propose-v236', 'request_id': str(uuid4()),
        'assessment_id': case.assessment_id, 'expected_result_hash': case.parent_result['result_hash'],
        'expected_source_hash': case.source_hash, 'expected_frame_sha256': case.frame_sha256}


def propose(case, body=None):
    value = body or propose_body(case); encoded = b' \n'+raw(value)+b'\n'
    with Session(case.bound) as db:
        result, created = service.propose(db, case.p, case.task_id, encoded)
        db.commit()
    assert created is True and result['operation'] == 'propose'
    assert result['request_id'] == value['request_id']
    assert result['body_sha256'] == hashlib.sha256(encoded).hexdigest()
    return NS(body=value, raw=encoded, command=result, plan_id=result['plan_id'], plan=result['plan'])


def approve(case, proposal):
    value = {'schema': 'frame-supplement-approve-v236', 'request_id': str(uuid4()),
        'version': proposal.plan['version'], 'expected_decision_hash': proposal.plan['decision_hash'],
        # The public local-model transport emits a genuine four-second result.
        # This is a requested supported duration, not a changed product default.
        'seconds': 4, 'confirm_one_time_read': True}
    encoded = b'\n'+raw(value)+b'  '
    with Session(case.bound) as db:
        result = service.approve(db, case.p, case.task_id, proposal.plan_id, encoded)
        db.commit()
    assert result['operation'] == 'approve' and result['plan_id'] == proposal.plan_id
    assert result['body_sha256'] == hashlib.sha256(encoded).hexdigest()
    return NS(body=value, raw=encoded, command=result, plan=result['plan'])


def advance(case, plan_id):
    with Session(case.bound) as db:
        # Match the scheduler's request -> Instance -> Plan order. The test
        # does not turn a lock-free helper call into transaction evidence.
        before = db.get(m.SupplementPlan, plan_id)
        if before.status == 'reading':
            from generic_agents.evidence_assessment_api import lock_request
            lock_request(db, case.p, service.assessment_nonce(before))
        service.scoped(db, m.DomainInstance, before.instance_id, case.p, True)
        service.scoped(db, m.SupplementPlan, plan_id, case.p, True)
        row = service.checked_plan(db, case.p, case.task_id, plan_id)
        service.advance(db, case.p, row)
        db.commit()
    with Session(case.bound) as db:
        row = service.checked_plan(db, case.p, case.task_id, plan_id)
        return service.plan_view(db, case.p, row)


@pytest.fixture
def frame_supplement_case(daily_frame_source, explicit_no_alert_fixture):
    parent = daily_frame_source
    assert parent.bound.dialect.name == 'postgresql' and parent.bound.url.database.startswith('ga_test_')
    assert settings().storage_dir == settings().storage_dir.resolve(strict=True)
    _, _, saved = create_qa(parent)
    completed = finish_qa(parent, saved)
    camera_id = parent.scene.source.camera_id
    cameras = parent.scene.client.get('/api/media/channels')
    assert cameras.status_code == 200
    camera = next(item for item in cameras.json() if item['id'] == camera_id)
    if not camera.get('capability_id'):
        registered = parent.scene.client.post('/api/media/channels/'+camera_id+'/register',
            json={'version': camera['version']})
        assert registered.status_code == 200, registered.text[:300]
        camera = registered.json()
    assert camera['capability_id']
    case = NS(case=parent, bound=parent.bound, p=parent.p, client=parent.scene.client,
        task_id=parent.task_id, assessment_id=completed['id'], parent_result=completed,
        source_hash=parent.source_hash, frame_sha256=parent.frame_sha256, camera_id=camera_id,
        path='/api/workspace/tasks/'+parent.task_id+'/frame-supplements')
    with Session(case.bound) as db:
        # Registration is an ordinary NEW capability, not permission to rewrite
        # the original source or to insert a synthetic domain_binding.
        bundle = original_source.read_source(db, case.p, case.task_id, expected_pin=parent.pin)
        assert bundle['source_hash'] == case.source_hash
        original_source.checked_result(db, case.p, db.get(m.EvidenceAssessment, case.assessment_id))
        assert db.get(m.VideoRun, parent.scene.source.run_id).data['source_context'].get('domain_binding') is None
        case.old_baseline = original_rows(db, case)
        case.start_counts = counters(db, case)
    return case


def receive_and_save_followup(case, monkeypatch):
    from tests import test_media as media_tests
    proposal = propose(case)
    assert proposal.plan['decision']['source']['can_execute'] is True
    with Session(case.bound) as db: before_approval = counters(db, case)
    approved = approve(case, proposal)
    new_session_id = approved.plan['session']['id']
    assert new_session_id != case.case.scene.source.session_id
    with Session(case.bound) as db:
        after_approval = counters(db, case)
        assert after_approval[m.StreamSession.__tablename__] == before_approval[m.StreamSession.__tablename__] + 1
        assert after_approval[m.VideoReadReservation.__tablename__] == before_approval[m.VideoReadReservation.__tablename__] + 1
        assert after_approval[m.Job.__tablename__] == before_approval[m.Job.__tablename__] + 1
        for cls in (m.Task, m.VideoRun, m.EvidenceAssessment, m.CloudCall):
            assert after_approval[cls.__tablename__] == before_approval[cls.__tablename__]
        session = db.get(m.StreamSession, new_session_id)
        initial = {'id': session.id, 'job_id': session.data['job_id']}
        assert session.data.get('domain_binding') is None
    original_transport = media_tests.media.child

    def new_transport(script, request, directory, guard, receive):
        def received(value, temp):
            if value.get('kind') == 'capture.complete':
                # Give only this synthetic external capture different VIDEO
                # bytes before the real handler receives it. The independent
                # JPEG frame outputs deliberately remain identical. Thus a
                # new-video fault below cannot merely corrupt the old blob.
                path = temp/value['file']
                path.write_bytes(path.read_bytes()+b'\0v236-new-transport-receipt')
            return receive(value, temp)
        return original_transport(script, request, directory, guard, received)

    monkeypatch.setattr(media_tests.media, 'child', new_transport)
    captured, processed = finish_capture(case.client, initial)
    assert captured['status'] == 'captured' and processed['status'] == 'succeeded'
    assert processed['delivery']['data_received'] == len(processed['frames']) > 0
    assert processed['id'] != case.case.scene.source.run_id
    # Successful processing and an OLD received publication are not a new ACK.
    waiting = advance(case, proposal.plan_id)
    assert waiting['status'] == 'reading' and waiting['followup'] is None
    assert waiting['session']['receipt_status'] == 'waiting'
    with Session(case.bound) as db:
        video_daily.reconcile(db, case.p.workspace_id)
        db.commit()
    waiting = advance(case, proposal.plan_id)
    assert waiting['status'] == 'reading' and waiting['followup'] is None
    assert waiting['session']['receipt_status'] == 'waiting'
    delivered = delivery.consume_once(case.p.workspace_id)
    assert delivered['received'] >= 1 and delivered['blocked'] == 0
    following = advance(case, proposal.plan_id)
    assert following['status'] == 'rechecking' and following['followup'] is not None
    assert following['session']['receipt_status'] == 'received'
    assert run_setup_job(following['followup']['job_id']) == 0
    return NS(**vars(case), proposal=proposal, approval=approved, plan_id=proposal.plan_id,
        new_session_id=new_session_id, new_run_id=processed['id'], plan=following)


@pytest.fixture
def frame_supplement_rechecking(frame_supplement_case, monkeypatch):
    return receive_and_save_followup(frame_supplement_case, monkeypatch)


@pytest.fixture
def frame_supplement_received(frame_supplement_case, monkeypatch):
    case = receive_and_save_followup(frame_supplement_case, monkeypatch)
    completed = advance(case, case.plan_id)
    assert completed['status'] == 'completed' and completed['followup']['status'] == 'completed'
    with Session(case.bound) as db:
        row = service.checked_plan(db, case.p, case.task_id, case.plan_id)
        bundle = source.read_followup(db, case.p, row)
        assert bundle['frame_sha256'] == hashlib.sha256(bundle['content']).hexdigest()
        assert original_rows(db, case) == case.old_baseline
        # Every adversarial follow-up test starts from a genuinely complete
        # source, not an unrelated common early failure.
        dependency = source.dependency(bundle)
        assert source.recheck_dependency(db, case.p, dependency)['source_hash'] == bundle['source_hash']
    return NS(**{**vars(case), 'plan': completed,
        'pin': copy.deepcopy(bundle['pin']), 'dependency': copy.deepcopy(dependency)})


def test_proposal_reads_saved_stock_and_sealed_qa_without_reserving_a_new_read(frame_supplement_case):
    case = frame_supplement_case
    with Session(case.bound) as db: before = counters(db, case)
    proposal = propose(case)
    decision = proposal.plan['decision']
    assert decision['assessment_id'] == case.assessment_id
    assert decision['result_hash'] == case.parent_result['result_hash']
    assert decision['source_hash'] == case.source_hash and decision['frame_sha256'] == case.frame_sha256
    assert decision['rule_version'] == 'frame-supplement-business-rules-v236'
    stock = decision['stock']
    assert stock['run_id'] == case.case.scene.source.run_id
    assert stock['total_frames'] == 9 and stock['checked_frames'] == len(stock['frames']) == 8
    assert stock['limit'] == 8 and stock['truncated'] is True
    assert decision['recommendation'] == 'inspect_saved_first_then_optional_read'
    assert {'source_time', 'coverage', 'domain_quality'} <= set(decision['target_gaps'])
    assert len(decision['cannot_certify']) >= 3 and all(
        isinstance(item, str) and item.strip() for item in decision['cannot_certify'])
    assert decision['read']['max_reads'] == 1 and decision['read']['cloud_calls'] == 0
    assert decision['read']['physical_control_authorized'] is False
    exposed = json.dumps(proposal.plan, ensure_ascii=False)
    assert str(settings().storage_dir) not in exposed
    assert not any(key in exposed for key in ('"storage_key"', '"rtsp_origin"', '"api_origin"', '"secret_ref"'))
    with Session(case.bound) as db:
        assert counters(db, case) == before
        assert original_rows(db, case) == case.old_baseline
        assert source.read_original(db, case.p, service.checked_plan(db, case.p,
            case.task_id, proposal.plan_id))['source_hash'] == case.source_hash


def test_one_approved_session_and_same_bytes_never_become_new_scene_truth(frame_supplement_received):
    case = frame_supplement_received
    comparison = case.plan['comparison']
    assert comparison['before_assessment_id'] == case.assessment_id
    assert comparison['after_assessment_id'] == case.plan['followup']['id']
    assert comparison['before_frame_sha256'] == comparison['after_frame_sha256'] == case.frame_sha256
    assert comparison['same_original'] is True and comparison['new_scene_information_verified'] is False
    assert comparison['current_state_sufficient'] is False and comparison['state_conclusion'] is None
    assert {'source_time', 'coverage', 'domain_quality'} <= set(comparison['remaining_gaps'])
    for side in (comparison['before'], comparison['after']):
        assert side['source_time_verified'] is side['domain_quality_verified'] is side['current_state_sufficient'] is False
        assert side['state_conclusion'] is None and side['cloud_calls'] == 0 and side['entity_state_updated'] is False
    with Session(case.bound) as db:
        child = db.get(m.EvidenceAssessment, case.plan['followup']['id'])
        assert child.data['request']['parent_id'] == case.assessment_id
        assert 'supplement_frame_v236' in child.data['request']
        assert 'daily_task_frame_v233' not in child.data['request']
        source.checked_result(db, case.p, child)
        assert child.data['request_hash'] == digest(child.data['request'])
        assert len(list(db.scalars(select(m.StreamSession).where(
            m.StreamSession.workspace_id == case.p.workspace_id,
            m.StreamSession.data[service.FAMILY]['plan_id'].as_string() == case.plan_id)))) == 1
        actual_counts = counters(db, case)
        for cls in (m.Task, m.CloudCall):
            assert actual_counts[cls.__tablename__] == case.start_counts[cls.__tablename__]
        for cls in (m.StreamSession, m.VideoReadReservation, m.VideoRun, m.EvidenceAssessment):
            assert actual_counts[cls.__tablename__] == case.start_counts[cls.__tablename__] + 1
        assert actual_counts[m.Job.__tablename__] == case.start_counts[m.Job.__tablename__] + 3
        assert original_rows(db, case) == case.old_baseline


@pytest.mark.parametrize('fault', ['original_result_audit', 'original_ack', 'original_copy_bytes'])
def test_new_proposal_cannot_use_a_broken_original_basis(frame_supplement_case, fault):
    case = frame_supplement_case
    baseline = propose(case)
    assert baseline.plan['decision']['source']['can_execute'] is True
    path = None; original = None
    with Session(case.bound) as db:
        if fault == 'original_result_audit':
            event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == case.p.workspace_id,
                m.Audit.action == original_source.RESULT_AUDIT, m.Audit.object_id == case.assessment_id))
            assert event is not None; db.delete(event); db.commit()
        elif fault == 'original_ack':
            memory = db.get(m.Memory, case.case.origin['ack_memory_id'])
            assert memory is not None; db.delete(memory); db.commit()
        else:
            image = db.get(m.Evidence, case.case.image_id)
            path = settings().storage_dir/image.storage_key
            original = path.read_bytes(); path.write_bytes(b'corrupt original saved frame')
    try:
        with Session(case.bound) as db:
            before = counters(db, case)
            with pytest.raises(HTTPException) as error:
                service.propose(db, case.p, case.task_id, raw(propose_body(case)))
            assert error.value.status_code in {404, 409}
            db.rollback()
            assert counters(db, case) == before
    finally:
        if path is not None: path.write_bytes(original)


@pytest.mark.parametrize('fault', ['new_ack', 'new_video_bytes', 'new_frame_receiver'])
def test_followup_source_is_physically_cold_and_requires_its_independent_receipt(frame_supplement_received, fault):
    case = frame_supplement_received
    path = None; original = None
    with Session(case.bound) as db:
        row = service.checked_plan(db, case.p, case.task_id, case.plan_id)
        assert source.read_followup(db, case.p, row, expected_pin=case.pin)['source_hash'] == case.plan['supplement']['source_hash']
        session = db.get(m.StreamSession, case.new_session_id)
        if fault == 'new_ack':
            events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == case.p.workspace_id,
                m.Event.kind == delivery.KIND)))
            proofs = [delivery.checked(db, case.p, event) for event in events]
            matching = [item for item in proofs if any(record['id'] == case.new_session_id
                for record in item['body']['summary']['records'])]
            assert matching
            for proof in matching:
                memory = db.get(m.Memory, proof['state']['memory_id'])
                assert memory is not None; db.delete(memory)
            db.commit()
        elif fault == 'new_frame_receiver':
            frame = db.scalar(select(m.VideoFrame).where(m.VideoFrame.run_id == case.new_run_id,
                m.VideoFrame.sample_index == 0))
            memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == case.p.workspace_id,
                m.Memory.data['type'].as_string() == 'video_observation',
                m.Memory.data['frame_id'].as_string() == frame.id))
            assert memory is not None; db.delete(memory); db.commit()
        else:
            image = db.get(m.Evidence, session.evidence_id)
            old_video = db.get(m.Evidence, db.get(m.StreamSession,
                case.case.scene.source.session_id).evidence_id)
            assert image.storage_key != old_video.storage_key and image.sha256 != old_video.sha256
            path = settings().storage_dir/image.storage_key
            original = path.read_bytes(); path.write_bytes(b'truncated new video')
    try:
        with Session(case.bound) as db:
            row = service.checked_plan(db, case.p, case.task_id, case.plan_id)
            for read in (lambda: source.read_followup(db, case.p, row),
                    lambda: source.read_followup(db, case.p, row, expected_pin=case.pin),
                    lambda: source.recheck_dependency(db, case.p, case.dependency)):
                with pytest.raises(HTTPException) as error: read()
                assert error.value.status_code in {404, 409}
    finally:
        if path is not None: path.write_bytes(original)


def test_current_reconfiguration_blocks_old_approval_but_not_original_command_recovery(frame_supplement_case):
    case = frame_supplement_case
    proposal = propose(case)
    reconfirm_camera(case.case.scene)
    with Session(case.bound) as db:
        before = counters(db, case)
        row = service.checked_plan(db, case.p, case.task_id, proposal.plan_id)
        assert service.plan_view(db, case.p, row)['id'] == proposal.plan_id
        recovered = service.recover(db, case.p, case.task_id, proposal.body['request_id'],
            hashlib.sha256(proposal.raw).hexdigest())
        assert recovered['found'] is True and recovered['result']['plan_id'] == proposal.plan_id
        assert source.read_original(db, case.p, row)['source_hash'] == case.source_hash
    with pytest.raises(HTTPException) as error: approve(case, proposal)
    assert error.value.status_code == 409
    with Session(case.bound) as db:
        assert counters(db, case) == before
        assert original_rows(db, case) == case.old_baseline
    # A changed current registration is an explainable non-executable NEW
    # suggestion, never silent reuse of the old approval or another camera.
    changed = propose(case)
    assert changed.plan['decision']['source']['configuration_changed'] is True
    assert changed.plan['decision']['source']['can_execute'] is False
    assert changed.plan['decision']['source']['blocked_reasons']
    with Session(case.bound) as db:
        assert counters(db, case) == before


def test_original_raw_approval_recovery_does_not_create_a_second_session(frame_supplement_case):
    case = frame_supplement_case
    proposal = propose(case); approved = approve(case, proposal)
    with Session(case.bound) as db:
        before = counters(db, case)
        recovered = service.recover(db, case.p, case.task_id, approved.body['request_id'],
            hashlib.sha256(approved.raw).hexdigest())
        assert recovered['found'] is True
        assert recovered['result']['plan']['session']['id'] == approved.plan['session']['id']
        repeated = service.approve(db, case.p, case.task_id, proposal.plan_id, approved.raw)
        db.commit()
        assert repeated['plan']['session']['id'] == approved.plan['session']['id']
        assert counters(db, case) == before
        # Identical parsed JSON with different original UTF8 bytes is not
        # the saved approval receipt and cannot be silently re-signed.
        with pytest.raises(HTTPException) as error:
            service.approve(db, case.p, case.task_id, proposal.plan_id, raw(approved.body))
        assert error.value.status_code == 409
        db.rollback()
        assert counters(db, case) == before


def test_saved_approval_is_recoverable_even_when_the_original_file_is_lost(frame_supplement_case):
    case = frame_supplement_case
    proposal = propose(case); approved = approve(case, proposal)
    with Session(case.bound) as db:
        image = db.get(m.Evidence, case.case.image_id)
        path = settings().storage_dir/image.storage_key
        original = path.read_bytes(); path.unlink()
        before = counters(db, case)
    try:
        with Session(case.bound) as db:
            recovered = service.recover(db, case.p, case.task_id, approved.body['request_id'],
                hashlib.sha256(approved.raw).hexdigest())
            assert recovered['found'] is True
            assert recovered['result']['plan']['session']['id'] == approved.plan['session']['id']
            replay = service.approve(db, case.p, case.task_id, proposal.plan_id, approved.raw)
            db.commit()
            assert replay['plan']['session']['id'] == approved.plan['session']['id']
            assert counters(db, case) == before
    finally: path.write_bytes(original)


@pytest.mark.parametrize('state', ['cancelled', 'failed'])
def test_a_real_unfinished_or_failed_parent_cannot_authorize_a_supplement(frame_supplement_case, state):
    case = frame_supplement_case
    _, _, saved = create_qa(case.case)
    child = saved['assessment']
    if state == 'cancelled':
        stopped = case.client.post('/api/evidence-assessments/'+child['id']+'/cancel',
            json={'version': child['version']})
        assert stopped.status_code == 200
    else:
        with Session(case.bound) as db:
            image = db.get(m.Evidence, case.case.image_id)
            path = settings().storage_dir/image.storage_key
            original = path.read_bytes(); path.write_bytes(b'explicit source fault before QA worker')
        try: assert run_setup_job(child['job_id']) == 1
        finally: path.write_bytes(original)
        with Session(case.bound) as db:
            assert db.get(m.Job, child['job_id']).status.startswith(('failed', 'blocked'))
    # Source restoration is verified first; rejection must concern the actual
    # parent state, rather than a source fault accidentally masking this case.
    with Session(case.bound) as db:
        assert original_source.read_source(db, case.p, case.task_id,
            expected_pin=case.case.pin)['source_hash'] == case.source_hash
        before = counters(db, case)
        body = {**propose_body(case), 'assessment_id': child['id']}
        with pytest.raises(HTTPException) as error:
            service.propose(db, case.p, case.task_id, raw(body))
        assert error.value.status_code == 409
        db.rollback()
        assert counters(db, case) == before


def test_another_real_member_cannot_recover_or_cancel_the_original_operators_request(frame_supplement_case):
    case = frame_supplement_case
    proposal = propose(case)
    with Session(case.bound) as db:
        user = m.User(username='v236_other_'+uuid4().hex[:12], password_hash=password_hash(uuid4().hex))
        db.add(user); db.flush()
        db.add(m.Membership(user_id=user.id, workspace_id=case.p.workspace_id, role='operator'))
        user_id = user.id; db.commit()
    other = Principal(user_id, case.p.workspace_id, 'operator', 'v236-real-other-member')
    with Session(case.bound) as db:
        before = counters(db, case)
        with pytest.raises(HTTPException) as error:
            service.recover(db, other, case.task_id, proposal.body['request_id'],
                hashlib.sha256(proposal.raw).hexdigest())
        assert error.value.status_code == 403
        db.rollback()
        cancelled = {'schema': 'frame-supplement-cancel-v236', 'request_id': str(uuid4()),
            'version': proposal.plan['version'], 'reason': '不得停止另一人的建议'}
        with pytest.raises(HTTPException) as error:
            service.cancel(db, other, case.task_id, proposal.plan_id, raw(cancelled))
        assert error.value.status_code == 403
        db.rollback()
        assert counters(db, case) == before


def test_completed_generic_qa_is_not_a_fixed_task_frame_authority(frame_supplement_case):
    case = frame_supplement_case
    response = case.client.post('/api/evidence-assessments', json={
        'instance_id': case.case.scene.release['instance_id'], 'release_id': case.case.scene.release['id'],
        'entity_ids': [case.case.scene.entity['id']], 'purpose': '普通对象资料核查，不是固定任务画面来源',
        'goal': 'current_state', 'required_modalities': ['video'], 'lookback_hours': 24,
        'request_id': str(uuid4()), 'parent_id': None})
    assert response.status_code == 201, response.text[:300]
    generic = response.json()
    assert run_setup_job(generic['job_id']) == 0
    result = case.client.get('/api/evidence-assessments/'+generic['id'])
    assert result.status_code == 200 and result.json()['status'] == 'completed'
    assert 'daily_task_frame_v233' not in result.json()['request']
    with Session(case.bound) as db:
        before = counters(db, case)
        body = {**propose_body(case), 'assessment_id': generic['id'],
            'expected_result_hash': result.json()['result_hash']}
        with pytest.raises(HTTPException) as error:
            service.propose(db, case.p, case.task_id, raw(body))
        assert error.value.status_code in {404, 409}
        db.rollback()
        assert counters(db, case) == before


@pytest.mark.parametrize('fault', ['cancel', 'admin_revoked', 'session_marker_removed'])
def test_generic_read_reservation_rechecks_the_real_plan_authority_before_execution(frame_supplement_case, fault):
    from generic_agents import video_read_boundary as boundary
    case = frame_supplement_case
    proposal = propose(case); approved = approve(case, proposal)
    with Session(case.bound) as db:
        session = db.get(m.StreamSession, approved.plan['session']['id'])
        assert session.data.get('domain_binding') is None
        reservation_id = session.data['read_reservation_id']
        # The SAME actual, normally reserved request must pass before the
        # isolated fault, including the new generic-session V236 live hook.
        assert boundary.guard(db, case.p, reservation_id).id == reservation_id
    reader = case.p
    if fault == 'cancel':
        value = {'schema': 'frame-supplement-cancel-v236', 'request_id': str(uuid4()),
            'version': approved.plan['version'], 'reason': '停止本次尚未执行的有限读取'}
        with Session(case.bound) as db:
            stopped = service.cancel(db, case.p, case.task_id, proposal.plan_id, raw(value))
            db.commit()
            assert stopped['plan']['status'] == 'cancelled'
            assert stopped['body_sha256'] == hashlib.sha256(raw(value)).hexdigest()
    elif fault == 'admin_revoked':
        with Session(case.bound) as db:
            member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == case.p.workspace_id,
                m.Membership.user_id == case.p.user_id))
            assert member.role == 'admin'
            member.role = 'viewer'; db.commit()
        reader = Principal(case.p.user_id, case.p.workspace_id, 'viewer', 'v236-current-demoted-member')
    else:
        with Session(case.bound) as db:
            session = db.get(m.StreamSession, approved.plan['session']['id'])
            assert service.FAMILY in session.data
            session.data = {key: item for key, item in session.data.items() if key != service.FAMILY}
            db.commit()
    with Session(case.bound) as db:
        before = counters(db, case)
        # Use the stale original Principal on purpose: the hook must check
        # actual membership/Plan and cannot trust the old admin role claim.
        with pytest.raises(HTTPException) as error: boundary.guard(db, case.p, reservation_id)
        assert error.value.status_code in {403, 409}
        db.rollback()
        assert counters(db, case) == before
        if fault != 'session_marker_removed':
            recovered = service.recover(db, reader, case.task_id, approved.body['request_id'],
                hashlib.sha256(approved.raw).hexdigest())
            assert recovered['found'] is True
            assert recovered['result']['plan']['session']['id'] == approved.plan['session']['id']


@pytest.mark.parametrize('job_status', ['failed', 'blocked_model'])
def test_completed_parent_result_with_failed_actual_job_is_not_a_new_read_authority(frame_supplement_case, job_status):
    case = frame_supplement_case
    proposal = propose(case)
    assert proposal.plan['decision']['source']['can_execute'] is True
    with Session(case.bound) as db:
        assessment = db.get(m.EvidenceAssessment, case.assessment_id)
        job = db.get(m.Job, assessment.data['job_id'])
        assert assessment.status == 'completed' and job.status == 'succeeded'
        assert assessment.data['result_hash'] == case.parent_result['result_hash']
        original_source.checked_result(db, case.p, assessment)
        # This controlled ledger-outcome fault follows a REAL full QA worker.
        # It is not evidence that a worker failed naturally, and never inserts
        # a success, changes the result, or fabricates a source/receipt.
        job.status = job_status; db.commit()
    with Session(case.bound) as db:
        before = counters(db, case)
        with pytest.raises(HTTPException) as error:
            service.propose(db, case.p, case.task_id, raw(propose_body(case)))
        assert error.value.status_code == 409
        db.rollback()
    with pytest.raises(HTTPException) as error: approve(case, proposal)
    assert error.value.status_code == 409
    with Session(case.bound) as db:
        assert counters(db, case) == before
        assert db.get(m.EvidenceAssessment, case.assessment_id).data['result_hash'] == case.parent_result['result_hash']
        assert db.get(m.Job, case.parent_result['job_id']).status == job_status
        recovered = service.recover(db, case.p, case.task_id, proposal.body['request_id'],
            hashlib.sha256(proposal.raw).hexdigest())
        assert recovered['found'] is True and recovered['result']['plan']['status'] == 'blocked'


def test_saved_followup_result_waits_for_actual_job_and_a_failure_never_completes_the_plan(frame_supplement_rechecking):
    case = frame_supplement_rechecking
    with Session(case.bound) as db:
        row = service.checked_plan(db, case.p, case.task_id, case.plan_id)
        assert row.status == 'rechecking' and row.data.get('comparison') is None
        child = db.get(m.EvidenceAssessment, case.plan['followup']['id'])
        job = source.checked_association(db, case.p, child)
        assert child.status == 'completed' and job.status == 'succeeded'
        source.checked_result(db, case.p, child)
        source.read_followup(db, case.p, row)  # Genuine originals and ACK pass first.
        result_hash = child.data['result_hash']; job_id = job.id
        # Controlled status projection of the worker's post-result/pre-settle
        # window. No lease or natural crash is claimed and no Job is executed
        # again; only the new wait/failure branches are being exercised.
        job.status = 'running'; db.commit()
        before = counters(db, case)
    waiting = advance(case, case.plan_id)
    assert waiting['status'] == 'rechecking' and waiting['comparison'] is None
    assert waiting['followup']['status'] == 'running'
    with Session(case.bound) as db:
        assert counters(db, case) == before
        assert service.checked_plan(db, case.p, case.task_id, case.plan_id).status == 'rechecking'
        job = db.get(m.Job, job_id); assert job.status == 'running'
        job.status = 'failed'; db.commit()
    failed = advance(case, case.plan_id)
    assert failed['status'] == 'blocked' and failed['comparison'] is None
    assert failed['followup']['status'] == 'failed'
    assert advance(case, case.plan_id)['status'] == 'blocked'
    with Session(case.bound) as db:
        assert counters(db, case) == before
        assert db.get(m.Job, job_id).status == 'failed'
        assert db.get(m.EvidenceAssessment, case.plan['followup']['id']).data['result_hash'] == result_hash
        assert original_rows(db, case) == case.old_baseline


def test_processing_guard_uses_the_original_context_even_when_the_session_run_fk_is_corrupt(frame_supplement_case):
    from generic_agents.local_specialists import guard_record
    case = frame_supplement_case
    proposal = propose(case); approved = approve(case, proposal)
    session_id = approved.plan['session']['id']
    assert process_once(approved.plan['session']['job_id'], deliver=False)
    with Session(case.bound) as db:
        session = db.get(m.StreamSession, session_id)
        assert session.status == 'captured'
        run = db.get(m.VideoRun, session.video_run_id)
        run_id, generation = run.id, run.data['generation']
        assert run_id != case.case.scene.source.run_id and run.status == 'queued'
        assert run.data['source_context']['session_id'] == session_id
        # REAL reading/queued-processing baseline, not a completed Plan which
        # would already reject for an unrelated terminal-state reason.
        assert service.checked_plan(db, case.p, case.task_id, proposal.plan_id).status == 'reading'
        assert guard_record(db, case.p, m.VideoRun, run_id, generation).id == run_id
    for wrong in (None, case.case.scene.source.run_id):
        with Session(case.bound) as db:
            session = db.get(m.StreamSession, session_id)
            assert session.video_run_id == run_id
            # Isolated FK-corruption injection; no success or receipt is
            # fabricated. Rollback restores the actual existing relationship.
            session.video_run_id = wrong; db.flush()
            with pytest.raises(HTTPException) as error:
                guard_record(db, case.p, m.VideoRun, run_id, generation)
            assert error.value.status_code == 409
            db.rollback()
    cancel = {'schema': 'frame-supplement-cancel-v236', 'request_id': str(uuid4()),
        'version': approved.plan['version'], 'reason': '停止后即使反向关联损坏也必须拦住处理'}
    with Session(case.bound) as db:
        stopped = service.cancel(db, case.p, case.task_id, proposal.plan_id, raw(cancel))
        db.commit(); assert stopped['plan']['status'] == 'cancelled'
    with Session(case.bound) as db:
        session = db.get(m.StreamSession, session_id)
        session.video_run_id = None; db.flush()
        with pytest.raises(HTTPException) as error:
            guard_record(db, case.p, m.VideoRun, run_id, generation)
        assert error.value.status_code == 409
        db.rollback()
        assert db.get(m.VideoRun, run_id).data['source_context']['session_id'] == session_id
        assert original_rows(db, case) == case.old_baseline


def test_missing_current_source_capability_after_normal_reconfirmation_saves_only_a_blocked_suggestion(frame_supplement_case):
    case = frame_supplement_case
    original = propose(case)
    reconfirm_camera(case.case.scene)
    with Session(case.bound) as db:
        camera = db.get(m.MediaChannel, case.camera_id)
        assert camera.data.get('capability_id') is None
        # Only current execution registration is unavailable. The actual
        # original historical Task/frame/ACK remains fully cold-verifiable.
        bundle = original_source.read_source(db, case.p, case.task_id, expected_pin=case.case.pin)
        assert bundle['source_hash'] == case.source_hash
        before = counters(db, case)
    changed = propose(case)
    assert changed.plan['status'] == 'proposed'
    assert changed.plan['decision']['source']['can_execute'] is False
    assert changed.plan['decision']['source']['blocked_reasons']
    assert changed.plan['permissions']['can_approve'] is False
    with Session(case.bound) as db:
        assert counters(db, case) == before
        recovered = service.recover(db, case.p, case.task_id, original.body['request_id'],
            hashlib.sha256(original.raw).hexdigest())
        assert recovered['found'] is True and recovered['result']['plan_id'] == original.plan_id
        assert original_rows(db, case) == case.old_baseline


def test_failed_real_capture_stays_failed_without_a_second_read_or_followup_qa(frame_supplement_case, monkeypatch):
    from tests import test_media as media_tests
    case = frame_supplement_case
    proposal = propose(case); approved = approve(case, proposal)
    session_id = approved.plan['session']['id']
    def failed_transport(*args, **kwargs): raise RuntimeError('explicit synthetic video transport failure')
    monkeypatch.setattr(media_tests.media, 'child', failed_transport)
    assert process_once(approved.plan['session']['job_id'], deliver=False)
    first = advance(case, proposal.plan_id)
    assert first['status'] == 'blocked' and first['followup'] is None
    assert first['session']['id'] == session_id and first['session']['capture_status'] == 'failed'
    with Session(case.bound) as db: before = counters(db, case)
    second = advance(case, proposal.plan_id)
    assert second['status'] == 'blocked' and second['followup'] is None
    with Session(case.bound) as db:
        assert counters(db, case) == before
        assert original_rows(db, case) == case.old_baseline


@pytest.mark.parametrize('requested_seconds', [4, 8])
def test_real_partial_capture_finishes_local_processing_then_blocks_without_followup_or_another_read(
        frame_supplement_case, monkeypatch, requested_seconds):
    from tests import test_media as media_tests
    from generic_agents.evidence_assessment_api import lock_request
    case = frame_supplement_case
    proposal = propose(case)
    body = {'schema': 'frame-supplement-approve-v236', 'request_id': str(uuid4()),
        'version': proposal.plan['version'], 'expected_decision_hash': proposal.plan['decision_hash'],
        'seconds': requested_seconds, 'confirm_one_time_read': True}
    with Session(case.bound) as db:
        approved = service.approve(db, case.p, case.task_id, proposal.plan_id, raw(body))
        db.commit()
    session_id = approved['plan']['session']['id']; actual_seconds = 2.0
    original_transport = media_tests.media.child

    def partial_transport(script, request, directory, guard, receive):
        def received(value, temp):
            if value.get('kind') == 'capture.complete':
                assert value['requested_seconds'] == requested_seconds
                value = {**value, 'partial': True, 'duration_seconds': actual_seconds,
                    'received_start_unix': value['received_end_unix']-actual_seconds,
                    'error': 'explicit synthetic upstream ended before the requested duration'}
            return receive(value, temp)
        return original_transport(script, request, directory, guard, received)

    monkeypatch.setattr(media_tests.media, 'child', partial_transport)
    assert process_once(approved['plan']['session']['job_id'], deliver=False)
    with Session(case.bound) as db:
        session = db.get(m.StreamSession, session_id)
        assert session.status == 'partial' and session.evidence_id and session.video_run_id
        assert session.data['receipt']['partial'] is True
        assert session.data['receipt']['requested_seconds'] == requested_seconds
        assert session.data['receipt']['duration_seconds'] == actual_seconds
        assert db.get(m.Job, session.data['job_id']).status == 'succeeded'
        run = db.get(m.VideoRun, session.video_run_id)
        run_id, processing_job_id = run.id, run.data['job_id']
        assert run.status == 'queued' and db.get(m.Job, processing_job_id).status == 'queued'
        assert run.data['source_context']['partial'] is True
        video_evidence_id = session.evidence_id
    queued = advance(case, proposal.plan_id)
    assert queued['status'] == 'reading' and queued['followup'] is None
    with Session(case.bound) as db:
        run = db.get(m.VideoRun, run_id)
        assert run.status == 'queued' and run.data.get('cancel_requested') is not True
        assert db.get(m.Job, processing_job_id).status == 'queued'
    original_video = media_tests.local_video.execute_video
    running_seen = []

    def partial_video(content, backend, configuration, checkpoints, guard, receive, lock_fd=None):
        # Observe the actual normally claimed processing Job/Run, not a
        # manufactured running or successful row. The poll must not stop it.
        with Session(case.bound) as db:
            assert db.get(m.VideoRun, run_id).status == 'running'
            assert db.get(m.Job, processing_job_id).status == 'running'
        view = advance(case, proposal.plan_id)
        assert view['status'] == 'reading' and view['followup'] is None
        with Session(case.bound) as db:
            assert db.get(m.VideoRun, run_id).data.get('cancel_requested') is not True
        running_seen.append(True)
        frames = 0

        def received(value, directory):
            nonlocal frames
            if value.get('kind') == 'frame':
                if value['seconds'] > actual_seconds: return
                frames += 1
            if value.get('kind') == 'complete':
                value = {**value, 'duration_seconds': actual_seconds, 'sample_count': frames}
            return receive(value, directory)
        return original_video(content, backend, configuration, checkpoints, guard, received, lock_fd=lock_fd)

    monkeypatch.setattr(media_tests.local_video, 'execute_video', partial_video)
    assert process_once(processing_job_id, deliver=False)
    assert running_seen == [True]
    processed = case.client.get('/api/videos/'+run_id)
    assert processed.status_code == 200 and processed.json()['status'] == 'succeeded'
    assert processed.json()['delivery']['data_received'] == len(processed.json()['frames']) > 0
    with Session(case.bound) as db:
        row = service.checked_plan(db, case.p, case.task_id, proposal.plan_id)
        assert row.status == 'reading'
        lock_request(db, case.p, service.assessment_nonce(row))
        service.scoped(db, m.DomainInstance, row.instance_id, case.p, True)
        service.scoped(db, m.SupplementPlan, row.id, case.p, True)
        job = db.get(m.Job, processing_job_id)
        assert job.status == 'succeeded' and db.get(m.VideoRun, run_id).status == 'succeeded'
        # Controlled pre-settle status projection AFTER a genuine successful
        # worker; rollback restores the original ledger, not a fabricated
        # success. No Job/Run is re-executed or forced captured/full.
        job.status = 'running'; db.flush()
        service.advance(db, case.p, row)
        assert row.status == 'reading' and row.data.get('followup_assessment_id') is None
        assert db.get(m.VideoRun, run_id).data.get('cancel_requested') is not True
        db.rollback()
    blocked = advance(case, proposal.plan_id)
    assert blocked['status'] == 'blocked' and blocked['followup'] is None and blocked['comparison'] is None
    # Complete-supplement receipt qualification is blocked; the genuine
    # partial-data Daily ACK below remains independently preserved/verified.
    assert blocked['session']['receipt_status'] == 'blocked'
    message = blocked['progress']+' '+(blocked['error'] or '')
    assert '实际' in message and '请求' in message
    assert str(requested_seconds) in message and '2' in message
    with Session(case.bound) as db:
        assert db.get(m.StreamSession, session_id).status == 'partial'
        assert db.get(m.StreamSession, session_id).evidence_id == video_evidence_id
        assert db.get(m.VideoRun, run_id).status == 'succeeded'
        assert db.get(m.Job, processing_job_id).status == 'succeeded'
        video_daily.reconcile(db, case.p.workspace_id); db.commit()
    received = delivery.consume_once(case.p.workspace_id)
    assert received['received'] >= 1 and received['blocked'] == 0
    with Session(case.bound) as db:
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == case.p.workspace_id,
            m.Event.kind == delivery.KIND)))
        matching = [event for event in events if any(record['id'] == session_id
            for record in event.data['summary']['records'])]
        assert matching
        for event in matching:
            proof = delivery.checked(db, case.p, event)
            assert proof['state']['status'] == 'received'
            delivery.validate_sources(db, case.p, proof['body'])
        row = service.checked_plan(db, case.p, case.task_id, proposal.plan_id)
        with pytest.raises(HTTPException) as error: source.read_followup(db, case.p, row)
        assert error.value.status_code == 409  # ACK is not full-capture qualification.
        before = counters(db, case)
        assert before[m.StreamSession.__tablename__] == case.start_counts[m.StreamSession.__tablename__] + 1
        assert before[m.VideoReadReservation.__tablename__] == case.start_counts[m.VideoReadReservation.__tablename__] + 1
        assert before[m.EvidenceAssessment.__tablename__] == case.start_counts[m.EvidenceAssessment.__tablename__]
        assert original_rows(db, case) == case.old_baseline
    assert advance(case, proposal.plan_id)['status'] == 'blocked'
    with Session(case.bound) as db: assert counters(db, case) == before
