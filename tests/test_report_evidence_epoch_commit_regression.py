import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from generic_agents import report_evidence_epoch_handoff as epoch
from generic_agents.db import engine
from generic_agents.models import Job
from tests.test_report_evidence_epoch_handoff_postgres import recovered_fixture
from tests.test_report_evidence_prebound_transport import prepared
from tests.test_cloud_report_plan_allowance_postgres import own_database
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene


def test_late_before_commit_epoch_mutation_is_rejected(prepared,reading_scene,monkeypatch,tmp_path):
    principal,request,command,fixture,_=recovered_fixture(prepared,reading_scene,monkeypatch,tmp_path)
    _,rid,jid,_,_,_,_=fixture
    monkeypatch.setattr(epoch,'_enabled',lambda:True)
    with Session(engine(),expire_on_commit=False) as db:
        epoch.handoff_tx(db,principal,request,rid,command)
        def late_mutation(session):
            job=session.get(Job,jid)
            job.data={**job.data,'delegated_epoch_hash':'0'*64}
        event.listen(db,'before_commit',late_mutation)
        with pytest.raises(ValueError):
            db.commit()


def test_late_before_commit_revocation_cannot_mint_send_witness(prepared):
    from generic_agents import cloud_scoped_extra_reservation as scoped
    from generic_agents import report_evidence_prebound_transport as transport
    from generic_agents.models import User
    from tests.test_report_evidence_prebound_transport import _session
    fixture,call_id,messages,options=prepared
    worker,_,_,_,cap,stage,_=fixture
    with _session(fixture) as db:
        sink=transport.prepare_prebound_capture(db,worker,stage['stage_id'],call_id,messages,options)
        try:
            scoped._session_checks(db)
            def late_revoke(session):
                session.get(User,worker.user_id).active=False
            event.listen(db,'before_commit',late_revoke)
            with pytest.raises(ValueError):
                transport.commit_transport_intent(db,worker,stage['stage_id'],call_id,messages,options,sink)
        finally:
            sink.close()
