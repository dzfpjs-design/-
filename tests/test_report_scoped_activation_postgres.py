"""Synthetic isolated-PG checks; no report, user database or provider calls."""
from datetime import timedelta
from uuid import uuid4
import copy

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import report_evidence_scoped_activation as activation
from generic_agents import report_evidence_delegated_dispatch_gate as gate
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job
from tests.test_report_evidence_delegated_budget_start_postgres import scene_and_preview
from tests.test_cloud_report_plan_allowance_postgres import own_database, prepared_request
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene
from tests.test_material_ocr import local_runtime
from tests.test_report_evidence_prebound_transport import prepared


def test_scoped_activation_issue_replay_revoke_never_sends(reading_scene, monkeypatch):
    monkeypatch.setattr(gate, 'ready', lambda: False)
    p, request, _, preview, _ = scene_and_preview(reading_scene, monkeypatch)
    with Session(engine(), expire_on_commit=False) as db:
        before = db.scalar(select(func.count()).select_from(CloudCall))
        record, replay = activation.issue_tx(db, p, request, preview['preview_id'])
        identity = record.id
        assert not replay and record.status == 'internal'
        assert record.data['manifest']['map_job_id'] == preview['allowance']['job_id']
        db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        record, replay = activation.issue_tx(db, p, request, preview['preview_id'])
        assert replay and record.id == identity
        db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        record, replay = activation.revoke_tx(db, p, request, identity)
        assert not replay and record.status == 'revoked'
        db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        record, replay = activation.revoke_tx(db, p, request, identity)
        assert replay and record.status == 'revoked'
        db.commit()
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before
        assert len(db.scalars(select(Audit).where(Audit.object_id == identity,
                         Audit.action == activation.REVOKE_ACTION)).all()) == 1
        with pytest.raises(HTTPException):
            activation.issue_tx(db, p, request, preview['preview_id'])


def test_wrong_scope_or_tampered_audit_denied(reading_scene, monkeypatch):
    monkeypatch.setattr(gate, 'ready', lambda: False)
    p, request, _, preview, _ = scene_and_preview(reading_scene, monkeypatch)
    with Session(engine(), expire_on_commit=False) as db:
        record, _ = activation.issue_tx(db, p, request, preview['preview_id']); identity = record.id
        db.commit()
    with Session(engine()) as db:
        record = db.get(Job, identity)
        with pytest.raises(HTTPException):
            activation._checked(db, p, record, map_job_id=str(uuid4()))
        audit = db.get(Audit, record.data['issue_audit_id'])
        audit.data = {'synthetic': 'changed'}; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            activation._checked(db, p, db.get(Job, identity))


def test_expired_scope_fails_closed(reading_scene, monkeypatch):
    monkeypatch.setattr(gate, 'ready', lambda: False)
    p, request, _, preview, _ = scene_and_preview(reading_scene, monkeypatch)
    with Session(engine(), expire_on_commit=False) as db:
        record, _ = activation.issue_tx(db, p, request, preview['preview_id']); identity = record.id
        db.commit()
    with Session(engine()) as db:
        record = db.get(Job, identity); data = copy.deepcopy(record.data)
        now = db.scalar(select(func.clock_timestamp()))
        data['manifest']['issued_at'] = (now - timedelta(hours=2)).isoformat()
        data['manifest']['expires_at'] = (now - timedelta(hours=1)).isoformat()
        data['manifest_hash'] = digest(data['manifest']); record.data = data
        audit = db.get(Audit, data['issue_audit_id'])
        audit.data = {**audit.data, 'manifest_hash': data['manifest_hash']}
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            activation._checked(db, p, db.get(Job, identity))


def test_exact_activated_plan_creates_one_bounded_job_without_network(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_budget_start as budget
    from generic_agents.models import SetupReportDraft
    monkeypatch.setattr(gate, 'ready', lambda: False)
    monkeypatch.setattr(budget, '_enabled', lambda: False)
    p, request, command, preview, _ = scene_and_preview(reading_scene, monkeypatch)
    with Session(engine(), expire_on_commit=False) as db:
        record, _ = activation.issue_tx(db, p, request, preview['preview_id'])
        activation_id = record.id; db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        saved, saved_command = budget._saved_preview(db, p, preview['preview_id'])
        summary = budget._summary(db, p, saved, saved_command)
        assert summary['can_start'] is True and summary['can_send'] is True
        before = db.scalar(select(func.count()).select_from(CloudCall))
        result = budget.start_tx(db, p, request, command, preview_id=preview['preview_id'],
                                 allowance_preview=preview['allowance'])
        assert result['replayed'] is False
        db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        row = db.get(SetupReportDraft, preview['allowance']['draft_id'])
        activation.for_job(db, p, row, map_job_id=preview['allowance']['job_id'], lock=False)
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before
        db.rollback()
    with Session(engine(), expire_on_commit=False) as db:
        activation.revoke_tx(db, p, request, activation_id); db.commit()
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, preview['allowance']['draft_id'])
        with pytest.raises(HTTPException):
            activation.for_job(db, p, row, map_job_id=preview['allowance']['job_id'], lock=False)


def test_revocation_after_reservation_prevents_transport_intent(reading_scene, monkeypatch, tmp_path):
    from generic_agents import report_evidence_delegated_budget_start as budget
    from generic_agents import report_evidence_pipeline as evidence
    from generic_agents import report_evidence_prebound_transport as transport
    from generic_agents import report_evidence_delegated_wire_v1 as wire
    from generic_agents import cloud_scoped_extra_reservation as scoped
    from generic_agents.leases import attach
    from generic_agents.models import SetupReportDraft, now
    from generic_agents.security import Principal
    monkeypatch.setattr(gate, 'ready', lambda: False)
    monkeypatch.setattr(budget, '_enabled', lambda: False)
    monkeypatch.setattr(wire, 'root', lambda: tmp_path / 'wire')
    p, request, command, preview, _ = scene_and_preview(reading_scene, monkeypatch)
    with Session(engine(), expire_on_commit=False) as db:
        record, _ = activation.issue_tx(db, p, request, preview['preview_id'])
        aid = record.id; db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        budget.start_tx(db, p, request, command, preview_id=preview['preview_id'],
                        allowance_preview=preview['allowance'])
        db.commit()
    jid = preview['allowance']['job_id']; rid = preview['allowance']['draft_id']
    first = preview['allowance']['stages'][0]; lease = str(uuid4())
    with Session(engine(), expire_on_commit=False) as db:
        job = db.get(Job, jid); job.status = 'running'; job.lease_token = lease
        job.lease_until = now() + timedelta(minutes=5)
        row = db.get(SetupReportDraft, rid); row.status = 'running'
        stage = evidence._stage(row, first['stage_id'])
        evidence._replace_stage(row, first['stage_id'], {**stage, 'status':'running','inflight':True})
        db.commit()
    worker = Principal(p.user_id, p.workspace_id, p.role, 'worker')
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, jid, lease); db.info['worker_actor'] = worker
        permit = db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                           Job.idempotency_key==scoped.permit_key(jid, first['stage_id'])))
        call_id=scoped.reserve_scoped_tx(db, worker, permit.id, preview['allowance']['model_id'],
                    first['token_bound'], first['request_hash'], first['options_hash'])
        db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, jid, lease); db.info['worker_actor'] = worker
        row = db.get(SetupReportDraft, rid)
        messages = evidence._request(row, first['stage_id'])['messages']
        options = copy.deepcopy(evidence._spec(row, first['stage_id'])['dispatch_options'])
        sink=transport.prepare_prebound_capture(db, worker, first['stage_id'], call_id, messages, options)
        try:
            with Session(engine(), expire_on_commit=False) as revoke_db:
                activation.revoke_tx(revoke_db, p, request, aid); revoke_db.commit()
            with pytest.raises(transport.PreboundTransportDenied):
                transport.commit_transport_intent(db, worker, first['stage_id'], call_id, messages, options, sink)
        finally:
            sink.close()
    with Session(engine()) as db:
        assert db.get(CloudCall, call_id).status == 'reserved'
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.action=='cloud.delegated_evidence.transport_intent_v1')) == 0


def test_real_http_activation_revoke_and_role_enforcement(reading_scene, monkeypatch):
    from generic_agents.models import Membership
    monkeypatch.setattr(gate, 'ready', lambda: False)
    p, _, _, preview, _ = scene_and_preview(reading_scene, monkeypatch)
    client=reading_scene['client']; path='/api/setup/report-evidence-delegated/activation'
    response=client.post(path,json={'preview_id':preview['preview_id'],'consent':True})
    assert response.status_code==201, response.text
    issued=response.json(); assert issued['this_action_calls_model'] is False and issued['user_confirmed_business'] is False
    again=client.post(path,json={'preview_id':preview['preview_id'],'consent':True})
    assert again.status_code==201 and again.json()['replayed'] is True
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        member.role='operator'; db.commit()
    forbidden=client.post(path+'/'+issued['activation_id']+'/revoke')
    assert forbidden.status_code==403
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        member.role='admin'; db.commit()
    revoked=client.post(path+'/'+issued['activation_id']+'/revoke')
    assert revoked.status_code==200 and revoked.json()['status']=='revoked'


def test_activation_lock_serializes_revocation_and_allows_retry(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_budget_start as budget
    monkeypatch.setattr(gate, 'ready', lambda: False)
    p, request, _, preview, _ = scene_and_preview(reading_scene, monkeypatch)
    with Session(engine(), expire_on_commit=False) as db:
        record, _ = activation.issue_tx(db, p, request, preview['preview_id'])
        identity=record.id; db.commit()
    with Session(engine(), expire_on_commit=False) as held:
        saved, _ = budget._saved_preview(held,p,preview['preview_id'])
        activation.for_preview(held,p,saved,lock=True)
        with Session(engine(), expire_on_commit=False) as other:
            with pytest.raises(HTTPException) as exc:
                activation.revoke_tx(other,p,request,identity)
            assert exc.value.status_code==409 and exc.value.detail['code']=='report_scope_busy'
            other.rollback()
        held.commit()
    with Session(engine(), expire_on_commit=False) as db:
        record, replay=activation.revoke_tx(db,p,request,identity)
        assert not replay and record.status=='revoked'; db.commit()


def test_unactivated_legacy_reservation_cannot_reach_intent(prepared, monkeypatch):
    from generic_agents import report_evidence_prebound_transport as transport
    from tests.test_report_evidence_prebound_transport import _session
    fixture, call_id, messages, options = prepared
    p, _, _, _, _, stage, _ = fixture
    monkeypatch.setattr(gate,'ready',lambda:False)
    with _session(fixture) as db:
        with pytest.raises(transport.PreboundTransportDenied):
            transport.prepare_prebound_capture(db,p,stage['stage_id'],call_id,messages,options)


def test_activation_commit_rejects_late_authority_change(reading_scene, monkeypatch):
    from generic_agents.models import User
    monkeypatch.setattr(gate,'ready',lambda:False)
    p,request,_,preview,_=scene_and_preview(reading_scene,monkeypatch)
    with Session(engine(),expire_on_commit=False) as db:
        record,_=activation.issue_tx(db,p,request,preview['preview_id']); identity=record.id
        db.get(User,p.user_id).active=False
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Job,identity) is None and db.get(User,p.user_id).active is True


def test_activation_is_actor_and_workspace_bound(reading_scene,monkeypatch):
    from generic_agents.security import Principal
    monkeypatch.setattr(gate,'ready',lambda:False)
    p,request,_,preview,_=scene_and_preview(reading_scene,monkeypatch)
    with Session(engine(),expire_on_commit=False) as db:
        record,_=activation.issue_tx(db,p,request,preview['preview_id']);identity=record.id;db.commit()
    with Session(engine()) as db:
        record=db.get(Job,identity)
        for other in (Principal(str(uuid4()),p.workspace_id,'admin',p.session_id),
                      Principal(p.user_id,str(uuid4()),'admin',p.session_id)):
            with pytest.raises(HTTPException):activation._checked(db,other,record)


def test_revoked_activation_with_missing_audit_is_not_replayed(reading_scene,monkeypatch):
    monkeypatch.setattr(gate,'ready',lambda:False)
    p,request,_,preview,_=scene_and_preview(reading_scene,monkeypatch)
    with Session(engine(),expire_on_commit=False) as db:
        record,_=activation.issue_tx(db,p,request,preview['preview_id']);identity=record.id;db.commit()
    with Session(engine(),expire_on_commit=False) as db:
        activation.revoke_tx(db,p,request,identity);db.commit()
    with Session(engine()) as db:
        audit=db.scalar(select(Audit).where(Audit.object_id==identity,Audit.action==activation.REVOKE_ACTION))
        db.delete(audit);db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):activation.revoke_tx(db,p,request,identity)
