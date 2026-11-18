"""Late ORM listeners must not bypass handoff/intent commit guards."""
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from generic_agents import report_evidence_epoch_handoff as epoch
from generic_agents import report_evidence_prebound_transport as transport
from generic_agents.db import engine
from generic_agents.models import Audit, Job, Membership, SetupReportDraft, User
from tests.test_report_evidence_epoch_handoff_postgres import recovered_fixture
from tests.test_report_evidence_prebound_transport import prepared, _session
from tests.test_cloud_report_plan_allowance_postgres import own_database
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene


@pytest.mark.parametrize('target',['job','actor','membership','delete_audit','delete_epoch'])
def test_late_listener_cannot_commit_handoff(prepared,reading_scene,monkeypatch,tmp_path,target):
    principal,request,command,fixture,_=recovered_fixture(prepared,reading_scene,monkeypatch,tmp_path)
    _,rid,jid,gid,_,_,_=fixture
    monkeypatch.setattr(epoch,'_enabled',lambda:True)
    with Session(engine(),expire_on_commit=False) as db:
        result=epoch.handoff_tx(db,principal,request,rid,command)
        def late(session):
            if target=='job':
                obj=session.get(Job,jid);obj.data={**obj.data,'delegated_epoch_hash':'0'*64}
            elif target=='actor':session.get(User,principal.user_id).active=False
            elif target=='membership':
                session.scalar(select(Membership).where(Membership.user_id==principal.user_id,
                    Membership.workspace_id==principal.workspace_id)).role='operator'
            elif target=='delete_epoch':session.delete(session.get(Job,result['epoch_id']))
            else:
                obj=session.scalar(select(Audit).where(Audit.object_id==result['epoch_id'],
                    Audit.action=='cloud.report_epoch.handoff_v1'));session.delete(obj)
        event.listen(db,'before_commit',late)
        with pytest.raises(ValueError):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Job,jid).status=='failed'
        assert db.get(SetupReportDraft,rid).status=='failed'
        assert db.get(Job,result['epoch_id']) is None


@pytest.mark.parametrize('target',['actor','membership','job','delete_audit'])
def test_late_listener_cannot_commit_map_transport_intent(prepared,target):
    from generic_agents import cloud_scoped_extra_reservation as scoped
    fixture,call_id,messages,options=prepared
    worker,rid,jid,_,_,stage,_=fixture
    with _session(fixture) as db:
        sink=transport.prepare_prebound_capture(db,worker,stage['stage_id'],call_id,messages,options)
        try:
            scoped._session_checks(db)
            def late(session):
                if target=='actor':session.get(User,worker.user_id).active=False
                elif target=='membership':
                    session.scalar(select(Membership).where(Membership.user_id==worker.user_id,
                        Membership.workspace_id==worker.workspace_id)).role='operator'
                elif target=='job':
                    obj=session.get(Job,jid);obj.data={**obj.data,'actor_id':'changed'}
                else:
                    obj=session.scalar(select(Audit).where(Audit.action=='cloud.delegated_evidence.transport_intent_v1'))
                    session.delete(obj)
            event.listen(db,'before_commit',late)
            with pytest.raises(ValueError):
                transport.commit_transport_intent(db,worker,stage['stage_id'],call_id,messages,options,sink)
        finally:sink.close()
    with Session(engine()) as db:
        assert not db.scalars(select(Audit).where(Audit.action=='cloud.delegated_evidence.transport_intent_v1')).all()


@pytest.mark.parametrize('short_lease',[False,True])
def test_map_witness_expires_within_all_persisted_bounds(prepared,monkeypatch,short_lease):
    from datetime import datetime, timedelta
    from generic_agents.models import now
    from generic_agents.cloud_scoped_extra_reservation import dispatch_key
    fixture,call_id,messages,options=prepared
    worker,_,jid,gid,cap,stage,_=fixture
    if short_lease:
        with Session(engine()) as db:
            db.get(Job,jid).lease_until=now()+timedelta(seconds=5);db.commit()
    tick={'value':100.0}
    monkeypatch.setattr(transport,'_monotonic',lambda:tick['value'])
    with _session(fixture) as db:
        sink=transport.prepare_prebound_capture(db,worker,stage['stage_id'],call_id,messages,options)
        try:
            witness=transport.commit_transport_intent(db,worker,stage['stage_id'],call_id,messages,options,sink)
            with Session(engine()) as check:
                dispatch=check.scalar(select(Job).where(Job.idempotency_key==dispatch_key(jid,stage['stage_id'])))
                intent=dispatch.data['intent'];grant=check.get(Job,gid)
                issued=datetime.fromisoformat(intent['issued_at']);end=datetime.fromisoformat(intent['not_after'])
                assert end<=issued+timedelta(seconds=30)
                assert end<=datetime.fromisoformat(intent['lease_until'])
                assert end<=datetime.fromisoformat(grant.data['manifest']['expires_at'])
            tick['value']+=31
            with pytest.raises(transport.PreboundTransportDenied):
                witness.consume(worker,cap,stage['stage_id'],call_id,messages,options,sink)
        finally:sink.close()


def test_witness_expiring_during_commit_leaves_terminal_intent(prepared,monkeypatch):
    from generic_agents import cloud_scoped_extra_reservation as scoped
    fixture,call_id,messages,options=prepared
    worker,_,_,_,_,stage,_=fixture
    tick={'value':100.0};monkeypatch.setattr(transport,'_monotonic',lambda:tick['value'])
    with _session(fixture) as db:
        sink=transport.prepare_prebound_capture(db,worker,stage['stage_id'],call_id,messages,options)
        try:
            scoped._session_checks(db)
            event.listen(db,'before_commit',lambda session:tick.update(value=131.0))
            with pytest.raises(transport.PreboundTransportDenied):
                transport.commit_transport_intent(db,worker,stage['stage_id'],call_id,messages,options,sink)
        finally:sink.close()
    with Session(engine()) as db:
        # The commit may already be durable when expiration is observed;
        # preserve it as potentially sent and never mint a witness or retry.
        assert len(db.scalars(select(Audit).where(Audit.action=='cloud.delegated_evidence.transport_intent_v1')).all())==1


def test_revocation_after_committed_intent_does_not_cancel_inflight_witness(prepared):
    """The intent is the audited admission point; this test performs no HTTP."""
    fixture,call_id,messages,options=prepared
    worker,_,_,_,cap,stage,_=fixture
    with _session(fixture) as db:
        sink=transport.prepare_prebound_capture(db,worker,stage['stage_id'],call_id,messages,options)
        try:
            witness=transport.commit_transport_intent(db,worker,stage['stage_id'],call_id,messages,options,sink)
            with Session(engine()) as revoke:
                revoke.get(User,worker.user_id).active=False;revoke.commit()
            bound=witness.consume(worker,cap,stage['stage_id'],call_id,messages,options,sink)
            assert bound['messages']==messages and bound['options']==options
            with pytest.raises(transport.PreboundTransportDenied):
                witness.consume(worker,cap,stage['stage_id'],call_id,messages,options,sink)
        finally:sink.close()
