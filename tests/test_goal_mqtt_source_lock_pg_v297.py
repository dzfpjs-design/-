"""Real PG source-lock contention/rollback; not a full Goal acceptance proof."""
from types import SimpleNamespace
from time import monotonic

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import goal_evaluation_provider_v288 as provider
from generic_agents import services, workflow_goal_evaluation_store_v288 as store
from generic_agents.workflow_goal_evaluation_contracts_v288 import MqttAckRefV297
from generic_agents.contracts import EvidenceRequirement
from generic_agents.models import Observation, Acquisition
from generic_agents.temporal import instant
from uuid import uuid4
from generic_agents.db import engine
from generic_agents.models import Audit, MqttGateway, MqttReceipt
from tests.test_goal_ack_origin_mqtt_pg_v297 import original


def test_busy_mqtt_original_returns_without_wait_and_rolls_back_all_prior_source_locks(client,account,monkeypatch):
    s=original(client,account,monkeypatch,declare_test=True)
    with Session(engine()) as db:
        receipt=db.get(MqttReceipt,s.receipt_id)
        gateway_id=receipt.gateway_id
        audit_id=s.declaration['audit_id']
    # The receiver holds its Gateway before it can notify a Task. Goal-side
    # locking must not wait on it while retaining any earlier source locks.
    with Session(engine()) as receiver, Session(engine()) as goal:
        receiver.scalar(select(MqttGateway).where(MqttGateway.id==gateway_id).with_for_update())
        started=monotonic()
        with pytest.raises(HTTPException) as failure:
            provider._lock_passive_originals(goal,s.p,[(Audit,audit_id),(MqttGateway,gateway_id)])
        assert failure.value.status_code==409 and monotonic()-started<3
        assert goal.scalar(select(func.count()).select_from(MqttReceipt))>=1  # Savepoint recovered transaction.
        with Session(engine()) as independent:
            assert independent.scalar(select(Audit.id).where(Audit.id==audit_id).with_for_update(nowait=True))==audit_id
        receiver.rollback()
        provider._lock_passive_originals(goal,s.p,[(Audit,audit_id),(MqttGateway,gateway_id)])
        assert not goal.new and not goal.dirty and not goal.deleted


def test_source_lock_scope_failure_rolls_back_without_reading_another_workspace(client,account,monkeypatch):
    s=original(client,account,monkeypatch,declare_test=True)
    alien=SimpleNamespace(workspace_id='00000000-0000-0000-0000-000000000000')
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            provider._lock_passive_originals(db,alien,[(Audit,s.declaration['audit_id'])])
        assert db.scalar(select(func.count()).select_from(Audit))>0


def test_actual_query_and_store_ack_keep_passive_test_origin_without_acquisition(client,account,monkeypatch):
    s=original(client,account,monkeypatch,declare_test=True)
    # This valid-shaped ID remains an explicit consumer-scope stub, not proof
    # of a native Goal, publication, creator lease or persisted evaluation.
    s.target.id=str(uuid4())
    with Session(engine()) as db:
        obs=db.get(Observation,s.identity); fields=list(obs.data['extracted']['attributes'])
        s.target.data['goal_spec']={'required_fields':fields}
        requirement=EvidenceRequirement.model_validate({'source':'observations',
            'time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':86400},
            'minimum_verification':'unverified_extraction'}).model_dump(mode='json')
        report=services.query_evidence(db,s.p,[obs.entity_id],fields,learning=False,
            include_trusted_cases=False,requirement=requirement)
        assert report['status']=='sufficient' and len(report['observation_evidence'])==1
        refs=store._acks(db,s.p,s.target,{'result':report},instant(report['evaluated_at']),report['resolved_time'])
        assert len(refs)==1
        ack=MqttAckRefV297.model_validate(refs[0]).model_dump(mode='json')
        assert ack['query_admitted'] is ack['simulation_only'] is ack['test_only'] is True
        assert ack['origin_task_id'] is None and 'acquisition_id' not in ack
        assert db.scalar(select(func.count()).select_from(Acquisition))==0
        assert not db.new and not db.dirty and not db.deleted
