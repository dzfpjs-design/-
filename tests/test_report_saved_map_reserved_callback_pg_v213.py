"""Actual run/callback/history with unit source IO plus real two-session FK IO.

No real paid success/HTTP is manufactured. The established public Consumer
fixture retains its original command/Audit checks; only admission/transport are
explicit synthetic boundaries. Two owned ga_test_ connections demonstrate the
FK lock held at that boundary and its release BEFORE independent settlement.
"""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from generic_agents import report_saved_map_design_consumer_v176 as app
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.leases import LeaseLost
from tests.test_report_saved_map_design_consumer_v176 import scene, InventoryIO, identity


class StopAtTransportBoundary(BaseException):
    """An explicit test stop, never a successful model or completed Job."""


@pytest.fixture(autouse=True)
def no_provider_http(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Only owned test PostgreSQL IO is allowed; never provider HTTP')
    monkeypatch.setattr(httpx.Client, 'stream', forbidden)
    monkeypatch.setattr(httpx.Client, 'request', forbidden)
    monkeypatch.setattr(httpx, 'request', forbidden)


@pytest.fixture
def fk_sessions():
    configured = engine()
    assert configured.url.get_backend_name() == 'postgresql'
    assert configured.url.database and configured.url.database.startswith('ga_test_')
    schema = 'callback_v213_' + uuid4().hex
    parent, child = f'"{schema}"."reserved_parent"', f'"{schema}"."pending_child"'
    with configured.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'CREATE TABLE {parent} (id text PRIMARY KEY)'))
        connection.execute(text(f'CREATE TABLE {child} (id text PRIMARY KEY, parent_id text NOT NULL REFERENCES {parent}(id))'))
        connection.execute(text(f'INSERT INTO {parent} (id) VALUES (:id)'), {'id': 'only-owned-test-parent'})
    try:
        with configured.connect() as owner, configured.connect() as independent:
            def attach():
                owner.execute(text("SET LOCAL lock_timeout = '250ms'"))
                owner.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                owner.execute(text(f'INSERT INTO {child} (id,parent_id) VALUES (:id,:parent)'),
                    {'id': 'only-owned-test-child', 'parent': 'only-owned-test-parent'})
            def independent_finish_probe():
                with independent.begin():
                    independent.execute(text("SET LOCAL lock_timeout = '250ms'"))
                    independent.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                    assert independent.execute(text(f'SELECT id FROM {parent} WHERE id=:id FOR UPDATE NOWAIT'),
                        {'id': 'only-owned-test-parent'}).scalar_one() == 'only-owned-test-parent'
            def child_count():
                with independent.begin():
                    return independent.execute(text(f'SELECT count(*) FROM {child}')).scalar_one()
            try:
                yield NS(owner=owner, attach=attach, independent_finish_probe=independent_finish_probe,
                    child_count=child_count)
            finally:
                owner.rollback()
                independent.rollback()
    finally:
        # Generated name, disposable DB, and our connections only. No backend
        # termination, pool clearing, production table or shared session action.
        with configured.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def execution_case(monkeypatch, sessions, failure):
    p, body, cap, source, row, command, audits, job, _raw = scene()
    job.status = 'running'
    trace, bindings = [], []
    class TransactionIO(InventoryIO):
        def __init__(self):
            super().__init__(p, rows=[row], commands=[command], audits=audits, jobs=[job])
            self.info['worker_lease'] = {'job_id': job.id, 'lease_token': job.lease_token}
            self.saved = None
        def commit(self):
            sessions.owner.commit()
            trace.append('checkpoint-commit')
            self.saved = (copy.deepcopy(row.data), row.status, row.version, row.reserved_call_id,
                list(self.audits))
            super().commit()
        def rollback(self):
            sessions.owner.rollback()
            trace.append('callback-rollback')
            assert self.saved is not None
            saved_data, row.status, row.version, row.reserved_call_id, restored = self.saved
            row.data = copy.deepcopy(saved_data)
            self.audits = list(restored)
    db = TransactionIO()
    frozen_budget = copy.deepcopy(row.data['preview']['budget'])
    def admitted(actual_db, principal, producer, revision, model, *, lock, consumer=None, note=''):
        assert actual_db is db and principal is p and lock is True and note == body.note
        assert (producer, revision, model) == (body.producer_id, body.revision, body.model_id)
        if consumer.reserved_call_id is not None:
            trace.append('fresh-gate-after-real-association')
            assert consumer is row and consumer.reserved_call_id == identity(88)
            sessions.attach()  # The DB effect of an autoflushed FK association.
            with pytest.raises(DBAPIError) as blocked:
                sessions.independent_finish_probe()
            code = getattr(blocked.value.orig, 'sqlstate', getattr(blocked.value.orig, 'pgcode', None))
            assert code == '55P03', 'the negative control must be a real FK row-lock conflict'
            trace.append('independent-finish-proven-blocked')
            if failure is not None:
                raise failure
        return {'source_context': source, 'capability': cap, 'revision': revision, 'budget': frozen_budget}
    def dispatch(actual_db, principal, actual_job, detached, *, request, spec, on_reserved):
        assert actual_db is db and principal is p and actual_job is job
        assert row.data['dispatch_started'] is True and db.saved[0]['dispatch_started'] is True
        prep = row.data['preview']['preparation']
        call = m.CloudCall(id=identity(88), workspace_id=p.workspace_id, capability_id=row.model_id,
            status='reserved', reserved_tokens=prep['input_summary']['token_bound'],
            charged_tokens=prep['input_summary']['token_bound'], data={
                'actor_id': p.user_id, 'request_hash': prep['request_hash'],
                'dispatch_options_hash': digest(spec['dispatch_options']), 'delivery': app.native.DELIVERY,
                'request_endpoint': app.native.ENDPOINT, 'request_body_bytes': prep['input_summary']['prepared_request_bytes'],
                'input_policy_hash': digest(spec['input_policy']), 'schema_proof_hash': digest(spec['schema_proof']),
                'gateway_dispatch': {'schema': 'cloud-dispatch-v130', 'identity': app.dispatch_identity(row),
                    'attempt_id': identity(89), 'reservation_request_hash': prep['request_hash']}})
        db.calls.append(call)
        trace.append('independent-reservation-committed')
        try:
            binding = on_reserved(call.id)
        except BaseException as error:
            assert error is failure, 'callback must rethrow the original failure unchanged'
            trace.append('independent-finish-entered')
            sessions.independent_finish_probe()  # Must no longer be blocked.
            trace.append('independent-finish-lock-acquired')
            raise StopAtTransportBoundary() from error
        bindings.append(binding)
        assert binding == app.journal_binding(row)
        trace.append('independent-finish-entered')
        sessions.independent_finish_probe()
        trace.append('independent-finish-lock-acquired')
        raise StopAtTransportBoundary()
    monkeypatch.setattr(app, 'runtime', lambda: NS(admit=admitted, dispatch=dispatch))
    return NS(p=p, row=row, job=job, db=db, trace=trace, bindings=bindings)


@pytest.mark.parametrize('failure', [ValueError('public source recheck failed'),
    LeaseLost('public lease changed'), KeyboardInterrupt('public interruption')])
def test_real_callback_rolls_back_fk_transaction_before_independent_finish(monkeypatch, fk_sessions, failure):
    case = execution_case(monkeypatch, fk_sessions, failure)
    with pytest.raises(StopAtTransportBoundary) as stopped:
        app.run(case.db, case.p, case.job)
    assert stopped.value.__cause__ is failure
    assert case.trace.index('fresh-gate-after-real-association') < case.trace.index('callback-rollback')
    assert case.trace.index('callback-rollback') < case.trace.index('independent-finish-entered')
    assert case.trace[-1] == 'independent-finish-lock-acquired'
    assert case.row.data['dispatch_started'] is True and case.row.reserved_call_id is None
    assert fk_sessions.child_count() == 0 and not case.bindings


def test_old_success_callback_keeps_same_binding_and_commits_before_external_wait(monkeypatch, fk_sessions):
    case = execution_case(monkeypatch, fk_sessions, None)
    with pytest.raises(StopAtTransportBoundary):
        app.run(case.db, case.p, case.job)
    assert 'callback-rollback' not in case.trace and len(case.bindings) == 1
    assert case.trace.count('checkpoint-commit') == 2
    assert case.trace.index('fresh-gate-after-real-association') < len(case.trace) - 1
    assert case.trace[-1] == 'independent-finish-lock-acquired'
    assert case.row.reserved_call_id == identity(88) and fk_sessions.child_count() == 1
