"""Real disposable-PG command/recovery interfaces; media/model seams explicit."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request
from generic_agents import frame_supplement_api_v236 as api, frame_supplement_v236 as service
from generic_agents import models as m
from tests.test_frame_supplement_source_v236 import (frame_supplement_case, frame_supplement_received,
    daily_frame_source, daily_scene, daily_camera_source, media_runtime, mocked_runtime,
    explicit_no_alert_fixture, propose_body, raw, propose as saved_proposal, approve as saved_approval)


def post(case, url, body):
    return case.client.post(url, content=raw(body), headers={'Content-Type': 'application/json'})


def test_normal_proposal_api_recovery_pagination_and_real_read_only_image(frame_supplement_case, monkeypatch):
    case = frame_supplement_case; body = propose_body(case); encoded = raw(body)
    with Session(case.bound) as db:
        initial_sessions = db.scalar(select(func.count()).select_from(m.StreamSession).where(m.StreamSession.workspace_id == case.p.workspace_id))
    response = post(case, case.path, body); assert response.status_code == 201, response.text[:350]
    command = response.json(); plan = command['plan']
    assert plan['status'] == 'proposed' and plan['approved'] is None and plan['session'] is None
    assert plan['decision']['stock']['checked_frames'] > 0
    assert post(case, case.path, body).status_code == 200
    saved = case.client.get(case.path+'/requests/'+body['request_id'], params={'expected_sha256': hashlib.sha256(encoded).hexdigest()})
    assert saved.status_code == 200 and saved.json()['found'] is True
    monkeypatch.setattr(service, 'PAGE_SIZE', 1)
    second = post(case, case.path, propose_body(case)); assert second.status_code == 201
    listing = case.client.get(case.path); assert listing.status_code == 200
    assert len(listing.json()['items']) == 1 and listing.json()['next_cursor']
    rest = case.client.get(case.path, params={'cursor': listing.json()['next_cursor']})
    assert rest.status_code == 200 and len(rest.json()['items']) == 1
    assert case.client.get(case.path, params={'cursor': str(uuid4())}).status_code == 422
    image = case.client.get(plan['original']['content_path'], params={'frame': 'original', 'expected_sha256': case.frame_sha256})
    assert image.status_code == 200 and hashlib.sha256(image.content).hexdigest() == case.frame_sha256
    assert image.headers['x-content-sha256'] == case.frame_sha256
    assert int(image.headers['content-length']) == len(image.content)
    assert image.headers['x-content-type-options'] == 'nosniff'
    assert case.client.get(plan['original']['content_path'], params={'frame': 'original', 'expected_sha256': '0'*64}).status_code == 409
    with Session(case.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.StreamSession).where(
            m.StreamSession.workspace_id == case.p.workspace_id)) == initial_sessions


def test_api_cancel_uses_exact_raw_nonce_and_legacy_approve_cannot_execute_new_family(frame_supplement_case):
    case = frame_supplement_case
    created = post(case, case.path, propose_body(case)); assert created.status_code == 201
    plan = created.json()['plan']
    legacy = case.client.post('/api/supplement-plans/'+plan['id']+'/approve', json={
        'version': plan['version'], 'action_keys':['invented'], 'seconds':4,
        'confirm_one_time_read':True, 'request_id':str(uuid4())})
    assert legacy.status_code == 409
    body = {'schema':'frame-supplement-cancel-v236','request_id':str(uuid4()),
        'version':plan['version'],'reason':'本次暂不补采，保留原核查'}
    stopped = post(case, case.path+'/'+plan['id']+'/cancel', body)
    assert stopped.status_code == 200 and stopped.json()['plan']['status'] == 'cancelled'
    assert post(case, case.path+'/'+plan['id']+'/cancel', body).json() == stopped.json()
    different = post(case, case.path+'/'+plan['id']+'/cancel', {**body, 'reason':'不能改原文'})
    assert different.status_code == 409
    assert case.client.get(case.path+'/requests/'+str(uuid4()), params={'expected_sha256':'a'*64}).json()['found'] is False


@pytest.mark.parametrize('fault', ['result_audit', 'request_marker', 'result_and_hash'])
def test_completed_qa_global_read_cannot_downgrade_missing_proofs(frame_supplement_received, fault):
    case = frame_supplement_received
    from generic_agents import frame_supplement_source_v236 as source
    with Session(case.bound) as db:
        plan = service.checked_plan(db, case.p, case.task_id, case.plan_id)
        identity = plan.data['followup_assessment_id']
        assessment = db.get(m.EvidenceAssessment, identity)
        source.checked_result(db, case.p, assessment)
        if fault == 'result_audit':
            event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == case.p.workspace_id,
                m.Audit.action == source.RESULT, m.Audit.object_id == identity)); db.delete(event)
        elif fault == 'request_marker':
            request = copy.deepcopy(assessment.data['request']); request.pop(source.MARKER)
            from generic_agents.contracts import digest
            assessment.data = {**assessment.data, 'request':request,'request_hash':digest(request)}
        else:
            data = copy.deepcopy(assessment.data); data.pop('result'); data.pop('result_hash'); assessment.data = data
        db.commit()
    response = case.client.get('/api/evidence-assessments/'+identity)
    assert response.status_code == 409
    view = case.client.get(case.path+'/'+case.plan_id)
    assert view.status_code == 200 and view.json()['status'] == 'blocked'
    assert view.json()['followup']['status'] == 'blocked_integrity'


@pytest.mark.parametrize('status', [404, 409])
def test_source_unavailable_proposal_is_saved_without_executing_or_claiming_permission(frame_supplement_case, monkeypatch, status):
    case = frame_supplement_case
    with Session(case.bound) as db:
        initial_sessions = db.scalar(select(func.count()).select_from(m.StreamSession).where(m.StreamSession.workspace_id == case.p.workspace_id))
    def unavailable(*args): raise HTTPException(status, '来源当前不可读取，请保留历史核查')
    monkeypatch.setattr(service, 'current_target', unavailable)
    response = post(case, case.path, propose_body(case)); assert response.status_code == 201
    plan = response.json()['plan']
    assert plan['decision']['source']['can_execute'] is False and plan['permissions']['can_approve'] is False
    approval = {'schema':'frame-supplement-approve-v236','request_id':str(uuid4()),
        'version':plan['version'],'expected_decision_hash':plan['decision_hash'],'seconds':4,'confirm_one_time_read':True}
    assert post(case, case.path+'/'+plan['id']+'/approve', approval).status_code == 409
    with Session(case.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.StreamSession).where(
            m.StreamSession.workspace_id == case.p.workspace_id)) == initial_sessions


def test_actually_completed_parent_then_failed_job_tail_is_not_new_read_authority(frame_supplement_case):
    case = frame_supplement_case
    # First demonstrate a real successful parent/decision, then inject a
    # failure (never fabricate a successful Job to get through the gate).
    response = post(case, case.path, propose_body(case)); assert response.status_code == 201
    plan = response.json()['plan']
    with Session(case.bound) as db:
        assessment = db.get(m.EvidenceAssessment, case.assessment_id)
        job = db.get(m.Job, assessment.data['job_id'])
        assert assessment.status == 'completed' and job.status == 'succeeded'
        job.status = 'failed'; db.commit()
    assert post(case, case.path, propose_body(case)).status_code == 409
    approval = {'schema':'frame-supplement-approve-v236','request_id':str(uuid4()),
        'version':plan['version'],'expected_decision_hash':plan['decision_hash'],'seconds':4,'confirm_one_time_read':True}
    assert post(case, case.path+'/'+plan['id']+'/approve', approval).status_code == 409
    current = case.client.get(case.path+'/'+plan['id'])
    assert current.status_code == 200 and current.json()['status'] == 'blocked'


@pytest.mark.parametrize('remove_current_link', [False, True], ids=['plan-witness','state-only-witness'])
def test_global_assessment_cannot_drop_to_legacy_after_all_direct_markers_are_removed(frame_supplement_received, remove_current_link):
    case = frame_supplement_received
    from generic_agents import frame_supplement_source_v236 as source
    from generic_agents.contracts import digest
    with Session(case.bound) as db:
        plan = service.checked_plan(db, case.p, case.task_id, case.plan_id)
        identity = plan.data['followup_assessment_id']
        assessment = db.get(m.EvidenceAssessment, identity)
        source.checked_result(db, case.p, assessment)
        assert source.is_record(db, assessment) is True
        request = copy.deepcopy(assessment.data['request']); request.pop(source.MARKER)
        assessment.data = {**assessment.data, 'request':request, 'request_hash':digest(request)}
        job = db.get(m.Job, assessment.data['job_id'])
        data = copy.deepcopy(job.data); data.pop(source.MARKER); job.data = data
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == case.p.workspace_id,
            m.Audit.object_id == identity, m.Audit.action.in_([source.CREATE,source.RESULT]))))
        assert len(events) == 2
        for event in events: db.delete(event)
        if remove_current_link:
            data = copy.deepcopy(plan.data); data.pop('followup_assessment_id'); plan.data = data
        db.commit()
    with Session(case.bound) as db:
        assessment = db.get(m.EvidenceAssessment, identity)
        # Plan or its independent original State still proves the family;
        # missing direct evidence is rejected, never reconstructed.
        assert source.is_record(db, assessment) is True
        with pytest.raises(HTTPException) as error: source.checked_result(db, case.p, assessment)
        assert error.value.status_code == 409
    assert case.client.get('/api/evidence-assessments/'+identity).status_code == 409


def test_fixed_receipt_cold_read_does_not_inventory_unrelated_later_camera_history(frame_supplement_received, monkeypatch):
    """Use real saved receipt/bytes; no synthetic successful ACK is inserted.

    Deny a whole-camera Event scan, as if its unrelated later history exceeds
    the first-selection bound. An exact fixed receipt remains independently
    verifiable and must not require the current camera inventory to fit.
    """
    case = frame_supplement_received
    from generic_agents import frame_supplement_source_v236 as source
    with Session(case.bound) as db:
        plan = service.checked_plan(db, case.p, case.task_id, case.plan_id)
        baseline = source.read_followup(db, case.p, plan)
        event_id = plan.data['received_source']['event_id']
        original_scalars = db.scalars
        reached = []
        def bounded_read(statement, *args, **kwargs):
            values = list(statement.compile().params.values())
            if 'channel:'+case.camera_id in values:
                assert event_id in values, 'fixed receipt attempted a whole-camera history scan'
                reached.append('exact_fixed_publication')
            return original_scalars(statement, *args, **kwargs)
        monkeypatch.setattr(db, 'scalars', bounded_read)
        read = source.read_followup(db, case.p, plan, expected_pin=baseline['pin'])
        assert read['source_hash'] == baseline['source_hash'] and read['content'] == baseline['content']
        assert reached


@pytest.mark.parametrize('action', [service.COMMAND, service.STATE])
def test_missing_independent_command_or_state_cannot_be_recovered_as_not_found(frame_supplement_case, action):
    case = frame_supplement_case; body = propose_body(case); encoded = raw(body)
    response = post(case, case.path, body); assert response.status_code == 201
    plan = response.json()['plan']; assert case.client.get(case.path+'/'+plan['id']).status_code == 200
    with Session(case.bound) as db:
        event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == case.p.workspace_id,
            m.Audit.action == action, m.Audit.object_id == plan['id']))
        assert event is not None; db.delete(event); db.commit()
    assert case.client.get(case.path+'/'+plan['id']).status_code == 409
    recovery = case.client.get(case.path+'/requests/'+body['request_id'],
        params={'expected_sha256':hashlib.sha256(encoded).hexdigest()})
    assert recovery.status_code == 409


def test_processing_guard_observes_real_mid_execution_cancellation(frame_supplement_case, monkeypatch):
    """Real queue/Jobs and guards; external decoder/model remain the named seam."""
    case = frame_supplement_case
    proposal = saved_proposal(case); approval = saved_approval(case, proposal)
    from generic_agents import local_video
    from tests.test_media import finish as finish_capture
    original_execute = local_video.execute_video
    reached = []
    def cancelled_during_execute(content, backend, configuration, checkpoints, guard, receive, lock_fd=None):
        guard()  # actual running producer, current member/plan scope really valid
        current = case.client.get(case.path+'/'+proposal.plan_id)
        assert current.status_code == 200
        stopped = post(case, case.path+'/'+proposal.plan_id+'/cancel', {
            'schema':'frame-supplement-cancel-v236','request_id':str(uuid4()),
            'version':current.json()['version'],'reason':'本次处理中明确停止'})
        assert stopped.status_code == 200 and stopped.json()['plan']['status'] == 'cancelled'
        reached.append('real_cancel_committed')
        with pytest.raises(HTTPException): guard()
        reached.append('next_actual_guard_rejected')
        raise ValueError('test cancelled through actual current-scope guard')
    monkeypatch.setattr(local_video, 'execute_video', cancelled_during_execute)
    session = approval.plan['session']
    captured, processed = finish_capture(case.client, {'id':session['id'],'job_id':session['job_id']})
    assert captured['status'] == 'captured'
    assert processed['status'] != 'succeeded'
    assert reached == ['real_cancel_committed','next_actual_guard_rejected']
    current = case.client.get(case.path+'/'+proposal.plan_id)
    assert current.status_code == 200 and current.json()['status'] == 'cancelled'
    assert current.json()['followup'] is None
    # The original task image and saved QA remain normally readable.
    assert case.client.get('/api/workspace/tasks/'+case.task_id).status_code == 200
    assert case.client.get('/api/evidence-assessments/'+case.assessment_id).status_code == 200


@pytest.mark.parametrize('operation', ['propose','approve','cancel'])
def test_async_route_services_do_not_block_event_loop_and_capacity_never_calls_service(monkeypatch, operation):
    entered = threading.Event(); release = threading.Event(); calls = []
    def fake_service(*args):
        calls.append(True); entered.set(); assert release.wait(5)
        return ({'ok':True}, True)
    monkeypatch.setattr(api, 'mutate', fake_service)
    body = propose_body(type('C', (), {'assessment_id':str(uuid4()), 'parent_result':{'result_hash':'a'*64},
        'source_hash':'b'*64,'frame_sha256':'c'*64})())
    if operation == 'approve': body = {'schema':'frame-supplement-approve-v236','request_id':str(uuid4()),
        'version':1,'expected_decision_hash':'a'*64,'seconds':4,'confirm_one_time_read':True}
    if operation == 'cancel': body = {'schema':'frame-supplement-cancel-v236','request_id':str(uuid4()),'version':1,'reason':'停止'}
    def request(content):
        async def receive(): return {'type':'http.request','body':content,'more_body':False}
        return Request({'type':'http','method':'POST','path':'/','headers':[]}, receive)
    async def run():
        function = getattr(api, operation)
        args = ('task', request(raw(body)), Response(), None, None) if operation == 'propose' else (
            'task','plan',request(raw(body)),None,None)
        pending = asyncio.create_task(function(*args))
        try:
            for _ in range(1000):
                if entered.is_set(): break
                await asyncio.sleep(.001)
            assert entered.is_set() and not pending.done()
        finally: release.set()
        await pending
        before = len(calls)
        too_big = request(b'x'*(service.MAX_BODY+1))
        args = ('task',too_big,Response(),None,None) if operation == 'propose' else ('task','plan',too_big,None,None)
        with pytest.raises(HTTPException) as error: await function(*args)
        assert error.value.status_code == 413 and len(calls) == before
    asyncio.run(run())


@pytest.mark.parametrize('first', ['generic_agents.frame_supplement_api_v236','generic_agents.worker'])
def test_new_router_and_worker_import_in_fresh_network_fenced_process(tmp_path, first):
    from tests.test_daily_frame_imports_v235 import SCRIPT
    order = [first, 'generic_agents.evidence_assessment', 'generic_agents.frame_supplement_api_v236',
        'generic_agents.worker']
    env = {key:os.environ[key] for key in ('PATH','SYSTEMROOT','LANG','LC_ALL') if key in os.environ}
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'), PYTHONDONTWRITEBYTECODE='1',
        GA_DATABASE_URL='postgresql+psycopg2://invalid/never_connect', GA_STORAGE_DIR=str(tmp_path/'unused-storage'))
    extended = SCRIPT.replace("print(json.dumps(", "newapi = importlib.import_module('generic_agents.frame_supplement_api_v236')\nassert any(route.path == '/api/workspace/tasks/{task_id}/frame-supplements' for route in newapi.router.routes)\nprint(json.dumps(")
    process = subprocess.run([sys.executable,'-c',extended,json.dumps(order)],cwd=tmp_path,
        env=env,text=True,capture_output=True,timeout=45)
    assert process.returncode == 0, process.stdout[-1000:]+'\n'+process.stderr[-2200:]
    assert json.loads(process.stdout.splitlines()[-1])['attempted_connections'] == 0
