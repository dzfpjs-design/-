"""Bounded presend diagnostic payload; original audit/lease IO are explicit spies.

Does not authorize retry, prove an unsent request or demonstrate worker custody.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import perception, leases
from generic_agents.contracts import digest
from generic_agents.worker_failure_diagnostics_v289 import SCHEMA, MAX_FRAMES


def uid(number): return str(UUID(int=number))


@pytest.mark.parametrize('http', [False, True])
def test_original_current_and_exchange_job_are_distinct_and_diagnostic_contains_no_secrets(monkeypatch, http):
    secret = 'private-credential-and-error-detail-do-not-save'
    db = object(); p = NS(workspace_id=uid(1), user_id=uid(2))
    acquisition = NS(id=uid(3), data={'original': 'unchanged'}, status='dispatching')
    exchange = NS(id=uid(4), dispatch_job_id=uid(5), data={'frozen': 'unchanged'})
    owner = {'job_id': uid(6), 'lease_token': secret}
    audits = []

    def ownership(actual):
        assert actual is db
        return owner

    def audit(actual, actor, action, object_id, value):
        assert actual is db and actor is p
        audits.append((action, object_id, value))

    monkeypatch.setattr(leases, 'ownership', ownership)
    monkeypatch.setattr(perception, 'audit', audit)
    before = copy.deepcopy((acquisition.data, exchange.data, owner))
    try:
        if http:
            raise HTTPException(403, {'credential': secret})
        # The real generic_agents code location survives while traceback
        # arguments and locals (including the secret) remain excluded.
        digest({'credential': secret, 'unsupported': object()})
    except Exception as failure:
        perception._managed_source_failure_diagnostic(db, p, acquisition, exchange, failure)
    assert len(audits) == 1
    action, object_id, value = audits[0]
    assert action == 'acquisition.managed_source_failure_v291' and object_id == acquisition.id
    assert set(value) == {'schema', 'workspace_id', 'acquisition_id', 'exchange_id',
        'job_id', 'original_dispatch_job_id', 'diagnostic', 'diagnostic_hash'}
    assert value['workspace_id'] == p.workspace_id and value['acquisition_id'] == acquisition.id
    assert value['exchange_id'] == exchange.id and value['job_id'] == owner['job_id']
    assert value['original_dispatch_job_id'] == exchange.dispatch_job_id != owner['job_id']
    diagnostic = value['diagnostic']
    assert diagnostic['schema'] == SCHEMA and diagnostic['http_status'] == (403 if http else None)
    assert len(diagnostic['frames']) <= MAX_FRAMES
    assert value['diagnostic_hash'] == digest(diagnostic)
    assert secret not in str(value) and 'lease_token' not in str(value)
    assert not any(key in value for key in ('post_sent', 'resume_allowed', 'retry_allowed'))
    assert (acquisition.data, exchange.data, owner) == before and acquisition.status == 'dispatching'
