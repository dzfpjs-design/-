"""Real scoped run/history/callback plus disposable PostgreSQL FK lock IO.

The confirmed Plan and source admission are explicit synthetic boundaries; this
does not prove source IAM, real paid reservation, provider output or lease
renewal. Actual original Attempt/Command/Audit/association code runs unchanged.
Two real owned ga_test_ sessions prove FK lock release before separate finish.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from generic_agents import models as m
from generic_agents import report_scoped_design_consumer_v181 as app
from generic_agents import report_scoped_design_store_v181 as plans
from generic_agents.contracts import digest
from generic_agents.leases import LeaseLost
from tests.test_report_scoped_design_consumer_v181 import scene, IO, identity
from tests.test_report_saved_map_reserved_callback_pg_v213 import (
    fk_sessions, no_provider_http, StopAtTransportBoundary)


def execution_case(monkeypatch, sessions, failure):
    case = scene()
    case.job.status = 'running'
    trace, bindings = [], []

    class TransactionIO(IO):
        def __init__(self):
            super().__init__(case)
            self.info['worker_lease'] = {'job_id': case.job.id, 'lease_token': case.job.lease_token}
            self.saved = None

        def commit(self):
            sessions.owner.commit()
            trace.append('checkpoint-commit')
            row = case.row
            self.saved = (copy.deepcopy(row.data), row.status, row.version,
                row.reserved_call_id, list(self.audits))
            super().commit()

        def rollback(self):
            sessions.owner.rollback()
            trace.append('callback-rollback')
            assert self.saved is not None
            saved_data, case.row.status, case.row.version, case.row.reserved_call_id, audits = self.saved
            case.row.data = copy.deepcopy(saved_data)
            self.audits = list(audits)

    db = TransactionIO()
    saved_plan = NS(id=case.body.plan_id, workspace_id=case.p.workspace_id, actor_id=case.p.user_id,
        producer_id=case.row.producer_id, version=case.body.expected_plan_version,
        revision=case.body.revision, status='confirmed', data={'plan': case.plan})

    def checked_plan(actual_db, principal, plan_id, *, lock=False, confirmed=False):
        assert actual_db is db and principal is case.p and plan_id == saved_plan.id
        assert lock is True and confirmed is True
        return saved_plan

    def admit(actual_db, principal, plan, body, *, lock=False, attempt=None):
        assert actual_db is db and principal is case.p and plan is saved_plan
        assert body.model_dump() == case.body.model_dump() and lock is True and attempt is case.row
        if attempt.reserved_call_id is not None:
            assert attempt.reserved_call_id == identity(820)
            trace.append('fresh-gate-after-real-association')
            sessions.attach()
            with pytest.raises(DBAPIError) as blocked:
                sessions.independent_finish_probe()
            code = getattr(blocked.value.orig, 'sqlstate', getattr(blocked.value.orig, 'pgcode', None))
            assert code == '55P03', 'negative control must observe a real PostgreSQL FK lock conflict'
            trace.append('independent-finish-proven-blocked')
            if failure is not None:
                raise failure
        return {'source_context': case.source, 'capability': case.cap,
            'preparation': copy.deepcopy(case.prep), 'budget': copy.deepcopy(case.row.data['preview']['budget'])}

    def dispatch(actual_db, principal, job, detached, *, request, spec, on_reserved):
        assert actual_db is db and principal is case.p and job is case.job
        assert case.row.data['dispatch_started'] is True and db.saved[0]['dispatch_started'] is True
        prep = case.row.data['preview']['preparation']
        call = m.CloudCall(id=identity(820), workspace_id=case.p.workspace_id, capability_id=case.row.model_id,
            status='reserved', reserved_tokens=prep['input_summary']['token_bound'],
            charged_tokens=prep['input_summary']['token_bound'], data={
                'actor_id': case.p.user_id, 'request_hash': prep['request_hash'],
                'dispatch_options_hash': digest(spec['dispatch_options']), 'delivery': app.native.DELIVERY,
                'request_endpoint': app.native.ENDPOINT,
                'request_body_bytes': prep['input_summary']['prepared_request_bytes'],
                'input_policy_hash': digest(spec['input_policy']), 'schema_proof_hash': digest(spec['schema_proof']),
                'gateway_dispatch': {'schema': 'cloud-dispatch-v130', 'identity': app.dispatch_identity(case.row),
                    'attempt_id': identity(821), 'reservation_request_hash': prep['request_hash']}})
        db.calls.append(call)  # Independent reservation boundary, not a DB paid-success fixture.
        trace.append('independent-reservation-committed')
        try:
            binding = on_reserved(call.id)
        except BaseException as error:
            assert error is failure, 'the original callback failure must escape without replacement'
            trace.append('independent-finish-entered')
            sessions.independent_finish_probe()
            trace.append('independent-finish-lock-acquired')
            raise StopAtTransportBoundary() from error
        bindings.append(binding)
        assert binding == app.journal_binding(case.row)
        trace.append('independent-finish-entered')
        sessions.independent_finish_probe()
        trace.append('independent-finish-lock-acquired')
        raise StopAtTransportBoundary()

    monkeypatch.setattr(plans, 'checked_plan', checked_plan)
    monkeypatch.setattr(app, 'runtime', lambda: NS(admit=admit, dispatch=dispatch))
    return NS(case=case, db=db, trace=trace, bindings=bindings)


@pytest.mark.parametrize('failure', [HTTPException(409, 'public current source recheck failed'),
    LeaseLost('public scoped lease changed'), KeyboardInterrupt('public scoped interruption')])
def test_scoped_callback_rolls_back_fk_transaction_before_independent_finish(monkeypatch, fk_sessions, failure):
    flow = execution_case(monkeypatch, fk_sessions, failure)
    with pytest.raises(StopAtTransportBoundary) as stopped:
        app.run(flow.db, flow.case.p, flow.case.job)
    assert stopped.value.__cause__ is failure
    assert flow.trace.index('fresh-gate-after-real-association') < flow.trace.index('callback-rollback')
    assert flow.trace.index('callback-rollback') < flow.trace.index('independent-finish-entered')
    assert flow.trace[-1] == 'independent-finish-lock-acquired'
    assert flow.case.row.data['dispatch_started'] is True and flow.case.row.reserved_call_id is None
    assert fk_sessions.child_count() == 0 and flow.bindings == []


def test_scoped_success_callback_preserves_binding_and_checkpoint_before_external_wait(monkeypatch, fk_sessions):
    flow = execution_case(monkeypatch, fk_sessions, None)
    with pytest.raises(StopAtTransportBoundary):
        app.run(flow.db, flow.case.p, flow.case.job)
    assert 'callback-rollback' not in flow.trace and len(flow.bindings) == 1
    assert flow.trace.count('checkpoint-commit') == 2
    assert flow.trace[-1] == 'independent-finish-lock-acquired'
    assert flow.case.row.reserved_call_id == identity(820) and fk_sessions.child_count() == 1
