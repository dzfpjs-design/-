"""Actual PG admission with a narrowly injected database-clock query result.

This exercises the rollback/recovery contract; it does not claim that the real
server clock moved backwards. Sources, actors, Invocations and admission gates
remain real, using only the existing synthetic numeric observation fixture.
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.functions import FunctionElement

from generic_agents.db import engine
from generic_agents.graph_event_admission import admit, read_admission, read_intent
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventIntent, GraphEventStream
from generic_agents.models import CloudCall, Invocation, Job
from generic_agents.worker import process_once
from tests.test_graph_event_pipeline import delivery_job, observe, pending_intents, scene


def test_backward_admission_clock_rolls_back_and_the_exact_pending_intent_recovers(client, monkeypatch):
    value = scene(client); p = value['principal']
    first, _, _ = observe(client, value, 28)
    first_intent = pending_intents(first['id'])[0][0]
    first_job, _ = delivery_job(first_intent)
    assert process_once(first_job, deliver=False)
    with Session(engine()) as db:
        assert db.get(Job, first_job).status == 'succeeded', db.get(Job, first_job).data
        original = db.scalar(select(GraphEventAdmission).where(GraphEventAdmission.intent_id == first_intent))
        assert original.sequence == 1
        first_id, first_stamp = original.id, original.admitted_at
        first_receipt = read_admission(db, p, first_id)

    second, _, _ = observe(client, value, 29)
    second_intent = pending_intents(second['id'])[0][0]
    with Session(engine(), expire_on_commit=False) as db:
        pending = db.get(GraphEventIntent, second_intent)
        assert pending.status == 'pending' and pending.receipt_id is None
        frozen_intent = read_intent(db, p, second_intent)
        invocation_ids = set(db.scalars(select(Invocation.id).where(Invocation.workspace_id == p.workspace_id)))
        assert len(invocation_ids) == 2
        real_scalar = db.scalar; injected = []

        def backward_clock(statement, *args, **kwargs):
            columns = list(getattr(statement, 'selected_columns', ()))
            if (len(columns) == 1 and isinstance(columns[0], FunctionElement)
                    and columns[0].name == 'clock_timestamp' and not list(columns[0].clauses)
                    and not statement.get_final_froms()):
                injected.append(statement)
                return first_stamp - timedelta(seconds=1)
            return real_scalar(statement, *args, **kwargs)

        # Patch this Session only, and only the standalone DB-clock expression.
        # All source/projection/actor checks and the locked admission cursor run.
        with monkeypatch.context() as clock_fault:
            clock_fault.setattr(db, 'scalar', backward_clock)
            with pytest.raises(HTTPException) as caught:
                admit(db, p, second_intent)
            assert caught.value.status_code == 409 and '时钟' in str(caught.value.detail)
            assert len(injected) == 1
            db.rollback()

        pending = db.get(GraphEventIntent, second_intent)
        assert pending.status == 'pending' and pending.receipt_id is None
        assert read_intent(db, p, second_intent) == frozen_intent
        assert db.scalar(select(GraphEventAdmission.id).where(GraphEventAdmission.intent_id == second_intent)) is None
        stream = db.scalar(select(GraphEventStream).where(GraphEventStream.workspace_id == p.workspace_id))
        assert stream.last_sequence == 1
        assert read_admission(db, p, first_id) == first_receipt
        assert set(db.scalars(select(Invocation.id).where(Invocation.workspace_id == p.workspace_id))) == invocation_ids

        recovered, created = admit(db, p, second_intent)
        assert created and recovered.sequence == 2 and recovered.admitted_at >= first_stamp
        recovered_id = recovered.id
        db.commit()
        recovered_receipt = read_admission(db, p, recovered_id)
        repeated, created_again = admit(db, p, second_intent)
        assert not created_again and repeated.id == recovered_id and repeated.sequence == 2
        assert read_admission(db, p, repeated.id) == recovered_receipt
        db.commit()

    with Session(engine()) as db:
        assert db.scalar(select(GraphEventStream.last_sequence).where(GraphEventStream.workspace_id == p.workspace_id)) == 2
        assert list(db.scalars(select(GraphEventAdmission.sequence).where(
            GraphEventAdmission.workspace_id == p.workspace_id).order_by(GraphEventAdmission.sequence))) == [1, 2]
        assert set(db.scalars(select(Invocation.id).where(Invocation.workspace_id == p.workspace_id))) == invocation_ids
        assert db.scalar(select(CloudCall.id).where(CloudCall.workspace_id == p.workspace_id)) is None
        assert read_admission(db, p, first_id) == first_receipt
