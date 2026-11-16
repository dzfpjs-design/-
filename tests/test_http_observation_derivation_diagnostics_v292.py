"""Bounded receipt-derivation diagnostic payload over explicit Audit IO spies.

Job/Exchange/Acq objects are supplied fixture boundaries, not actual lease, PG,
receiver, saved-receipt-resume or ingest evidence. No retry grant is asserted.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import http_observation_worker_v177 as subject
from generic_agents.contracts import digest
from generic_agents.worker_failure_diagnostics_v289 import SCHEMA, MAX_FRAMES


def uid(number): return str(UUID(int=number))


@pytest.mark.parametrize('http', [False, True])
def test_derivation_audit_keeps_actual_branch_ids_and_only_bounded_exception_locations(monkeypatch, http):
    secret = 'private-wire-credential-and-validation-detail-do-not-save'
    db = object(); p = NS(workspace_id=uid(1), user_id=uid(2))
    job = NS(id=uid(3), lease_token=secret, data={'request': secret}, status='running')
    exchange = NS(id=uid(4), dispatch_job_id=uid(5), data={'wire': secret}, status='received')
    acquisition = NS(id=uid(6), data={'receipt': secret}, status='awaiting_observation')
    calls = []

    def audit(actual, actor, action, object_id, value):
        assert actual is db and actor is p
        calls.append((action, object_id, value))

    monkeypatch.setattr(subject, 'audit', audit)
    before = copy.deepcopy((job.__dict__, exchange.__dict__, acquisition.__dict__))
    try:
        if http:
            raise HTTPException(422, {'detail': secret, 'fields': ['private-field']})
        digest({'credential': secret, 'unsupported': object()})
    except Exception as failure:
        subject._derivation_failure_diagnostic(db, p, job, exchange, acquisition, failure)

    assert len(calls) == 1
    action, object_id, value = calls[0]
    assert action == 'http.observation.derivation_failed_v292' and object_id == exchange.id
    assert set(value) == {'schema', 'workspace_id', 'job_id', 'exchange_id', 'acquisition_id',
        'original_dispatch_job_id', 'diagnostic', 'diagnostic_hash'}
    assert value['schema'] == 'http-observation-derivation-failure-v292'
    assert value['workspace_id'] == p.workspace_id and value['acquisition_id'] == acquisition.id
    assert value['job_id'] == job.id and value['exchange_id'] == exchange.id
    assert value['original_dispatch_job_id'] == exchange.dispatch_job_id != job.id
    diagnostic = value['diagnostic']
    assert diagnostic['schema'] == SCHEMA and diagnostic['http_status'] == (422 if http else None)
    assert len(diagnostic['frames']) <= MAX_FRAMES
    assert value['diagnostic_hash'] == digest(diagnostic)
    assert secret not in str(value) and 'private-field' not in str(value) and 'lease_token' not in str(value)
    assert not {'post_sent', 'resume_allowed', 'retry_allowed', 'observation_id',
        'received', 'goal_completed'} & set(value)
    assert (job.__dict__, exchange.__dict__, acquisition.__dict__) == before
