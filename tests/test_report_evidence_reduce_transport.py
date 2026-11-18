"""Synthetic local DB and HTTP only; never touches a real provider or workspace."""
import copy
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import report_evidence_reduce_transport as subject
from generic_agents import report_evidence_pipeline as evidence
from generic_agents.db import engine
from generic_agents.leases import attach
from generic_agents.models import CloudCall, Job
from generic_agents.security import authenticated
from tests.test_cloud_report_plan_allowance_postgres import own_database, prepared_request
from tests.test_report_evidence_prebound_worker_postgres import long_reading_scene
from tests.test_material_ocr import local_runtime
from tests.test_report_evidence_delegated_wire_postgres import owned_capture_roots


def test_reduce_transport_commits_one_scoped_intent_and_witness(long_reading_scene, monkeypatch):
    from tests.test_report_evidence_prebound_worker_postgres import test_fourteen_frozen_maps_consume_one_scoped_call_each
    test_fourteen_frozen_maps_consume_one_scoped_call_each(long_reading_scene, monkeypatch)
    request = prepared_request(long_reading_scene)
    with Session(engine()) as db:
        p = authenticated(request, db)
        grant = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == 'cloud_report_reduce_allowance_v1'))
        assert grant
        job = db.get(Job, grant.data['manifest']['job_id'])
        row = evidence.owned(db, p, grant.data['manifest']['draft_id'])
        messages = copy.deepcopy(evidence._request(row, 'reduce')['messages'])
        options = copy.deepcopy(evidence._spec(row, 'reduce')['dispatch_options'])
        call_id = grant.data['used_call_id']
        cap_id, job_id, token = grant.data['manifest']['model_id'], job.id, job.lease_token
    # A mismatched frozen request must leave the already reserved call untouched.
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, job_id, token); db.info['worker_actor'] = p
        altered = copy.deepcopy(messages); altered[-1]['content'] += ' changed'
        with pytest.raises(subject.ReduceTransportDenied):
            subject.prepare_prebound_capture(db, p, 'reduce', call_id, altered, options)
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, job_id, token); db.info['worker_actor'] = p
        sink = subject.prepare_prebound_capture(db, p, 'reduce', call_id, messages, options)
        witness = subject.commit_transport_intent(db, p, 'reduce', call_id, messages, options, sink)
        from generic_agents import report_evidence_prebound_transport as common
        assert 0 < witness._deadline - common._monotonic() <= common._MAX_INTENT_DELAY_SECONDS
        acquired = witness.consume(p, cap_id, 'reduce', call_id, messages, options, sink)
        assert acquired['call_id'] == call_id and acquired['sink'] is sink
        with pytest.raises(ValueError):
            witness.consume(p, cap_id, 'reduce', call_id, messages, options, sink)
        sink.close()
    with Session(engine()) as db:
        dispatch = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == 'cloud_extra_dispatch_v1',
            Job.data['stage_id'].as_string() == 'reduce'))
        assert dispatch.data['state'] == 'transport_intent'
        assert db.get(CloudCall, call_id).status == 'reserved'
    # Once intent exists it is unknown if sending happened and never reusable.
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, job_id, token); db.info['worker_actor'] = p
        with pytest.raises(subject.ReduceTransportDenied):
            subject.prepare_prebound_capture(db, p, 'reduce', call_id, messages, options)


def test_reduce_transport_rolls_back_commit_drift(long_reading_scene, monkeypatch):
    from sqlalchemy import event, update
    from generic_agents.models import Audit, User
    from generic_agents import cloud_scoped_extra_reservation as scoped
    from tests.test_report_evidence_prebound_worker_postgres import test_fourteen_frozen_maps_consume_one_scoped_call_each
    test_fourteen_frozen_maps_consume_one_scoped_call_each(long_reading_scene, monkeypatch)
    request = prepared_request(long_reading_scene)
    with Session(engine()) as db:
        p = authenticated(request, db)
        grant = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == 'cloud_report_reduce_allowance_v1'))
        job = db.get(Job, grant.data['manifest']['job_id'])
        row = evidence.owned(db, p, grant.data['manifest']['draft_id'])
        messages = copy.deepcopy(evidence._request(row, 'reduce')['messages'])
        options = copy.deepcopy(evidence._spec(row, 'reduce')['dispatch_options'])
        call_id, cap_id, job_id, token = grant.data['used_call_id'], grant.data['manifest']['model_id'], job.id, job.lease_token
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, job_id, token); db.info['worker_actor'] = p
        sink = subject.prepare_prebound_capture(db, p, 'reduce', call_id, messages, options)
        # An extension registered after the shared guard can mutate ORM state,
        # delete/expire a protected identity, or issue direct SQL with no ORM
        # flush. None may commit an intent or produce a usable witness.
        try:
            for mode in ('orm_revoke', 'sql_revoke', 'orm_delete', 'orm_expire', 'job_change'):
                scoped._session_checks(db)
                def drift(session):
                    if mode == 'sql_revoke':
                        session.execute(update(User).where(User.id == p.user_id).values(active=False),
                                        execution_options={'synchronize_session': False})
                    elif mode == 'orm_delete':
                        session.delete(session.get(User, p.user_id))
                    elif mode == 'orm_expire':
                        session.expire(session.get(User, p.user_id))
                    elif mode == 'job_change':
                        session.get(Job, job_id).status = 'failed'
                    else:
                        session.get(User, p.user_id).active = False
                event.listen(db, 'before_commit', drift)
                try:
                    with pytest.raises(subject.ReduceTransportDenied):
                        subject.commit_transport_intent(db, p, 'reduce', call_id, messages, options, sink)
                finally:
                    event.remove(db, 'before_commit', drift)
        finally:
            sink.close()
    with Session(engine()) as db:
        dispatch = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == 'cloud_extra_dispatch_v1',
            Job.data['stage_id'].as_string() == 'reduce'))
        assert dispatch.data['state'] == 'reserved'
        assert db.get(CloudCall, call_id).capability_id == cap_id
        assert db.get(User, p.user_id).active is True
        assert not db.scalar(select(Audit).where(Audit.object_id == dispatch.id,
            Audit.action == 'cloud.delegated_evidence.reduce_transport_intent_v1'))


def test_expired_reduce_witness_cannot_be_consumed(monkeypatch):
    from generic_agents import report_evidence_prebound_transport as common
    witness = common.CommittedTransportIntent(common._WITNESS, None, 'c', 'reduce', 'call', [], {}, None, {}, 'h', 10.0)
    monkeypatch.setattr(common, '_monotonic', lambda: 10.0)
    with pytest.raises(common.PreboundTransportDenied):
        witness.consume(None, 'c', 'reduce', 'call', [], {}, None)
    # Expiration consumes the witness even if time moves backwards.
    monkeypatch.setattr(common, '_monotonic', lambda: 0.0)
    with pytest.raises(common.PreboundTransportDenied):
        witness.consume(None, 'c', 'reduce', 'call', [], {}, None)
