"""Fail-closed read seams; no successful paid record is fabricated here.

The real failed capture, source, financial ledger and readonly API journey are
verified separately on Root's owned runtime. These synthetic negatives must
stop before transport/source access or any spending/settlement operation.
"""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import report_business_catalogue_source_v212 as source
from generic_agents import report_saved_map_design_consumer_v176 as consumer


def boundary(monkeypatch):
    p = NS(user_id=str(uuid4()), workspace_id=str(uuid4()), role='operator')
    row = NS(id=str(uuid4()), workspace_id=p.workspace_id, actor_id=p.user_id,
        status='failed', reserved_call_id=str(uuid4()), job_id=str(uuid4()),
        request_id=str(uuid4()), data={'preview': {'preparation': {
            'schema': 'report-business-catalogue-preparation-v211'}},
            'projection': None, 'manual_value': None, 'dispatch_started': True})
    call = NS(id=row.reserved_call_id, status='failed')
    job = NS(id=row.job_id, workspace_id=p.workspace_id, kind=consumer.JOB,
        idempotency_key='saved-map-design:' + row.request_id,
        data={'actor_id': p.user_id, 'consumer_id': row.id})
    events = []
    monkeypatch.setattr(consumer, '_current_reader', lambda db, actor: events.append('identity'))
    monkeypatch.setattr(consumer, '_inventory', lambda db, actor: ({row.id: row}, {}, {row.id: call}))
    def unavailable(*args, **kwargs):
        events.append('read_original')
        return None
    monkeypatch.setattr(consumer, 'runtime', lambda: NS(read_response=unavailable))
    return NS(p=p, row=row, call=call, job=job, events=events,
        db=NS(scalar=lambda query: job))


def test_current_identity_denial_stops_before_original_record_access(monkeypatch):
    b = boundary(monkeypatch)
    def denied(db, p):
        raise HTTPException(403, 'current identity denied')
    monkeypatch.setattr(consumer, '_current_reader', denied)
    monkeypatch.setattr(consumer, '_inventory', lambda *args: pytest.fail('denied identity read inventory'))
    with pytest.raises(HTTPException) as failure:
        source.load_failed_partial_source(b.db, b.p, b.row.id)
    assert failure.value.status_code == 403 and b.events == []


@pytest.mark.parametrize('fault', ['queued', 'response_unknown', 'review',
    'successful_projection', 'manual_value', 'not_dispatched', 'legacy_family',
    'unsettled_call', 'succeeded_call', 'other_call', 'other_job_actor', 'other_job_consumer'])
def test_ineligible_original_cannot_become_partial_or_read_a_capture(monkeypatch, fault):
    b = boundary(monkeypatch)
    if fault in {'queued', 'response_unknown', 'review'}: b.row.status = fault
    elif fault == 'successful_projection': b.row.data['projection'] = {'already': 'complete'}
    elif fault == 'manual_value': b.row.data['manual_value'] = {'already': 'manual'}
    elif fault == 'not_dispatched': b.row.data['dispatch_started'] = False
    elif fault == 'legacy_family': b.row.data['preview']['preparation']['schema'] = 'report-saved-map-design-preparation-v179'
    elif fault == 'unsettled_call': b.call.status = 'pending'
    elif fault == 'succeeded_call': b.call.status = 'succeeded'
    elif fault == 'other_call': b.call.id = str(uuid4())
    elif fault == 'other_job_actor': b.job.data['actor_id'] = str(uuid4())
    elif fault == 'other_job_consumer': b.job.data['consumer_id'] = str(uuid4())
    with pytest.raises(HTTPException) as failure:
        source.load_failed_partial_source(b.db, b.p, b.row.id)
    assert failure.value.status_code == 409
    assert b.events == ['identity']


def test_missing_original_capture_remains_failed_without_recovery_or_new_call(monkeypatch):
    b = boundary(monkeypatch)
    with pytest.raises(HTTPException) as failure:
        source.load_failed_partial_source(b.db, b.p, b.row.id)
    assert failure.value.status_code == 409
    assert b.events == ['identity', 'read_original']
    assert b.row.status == 'failed' and b.call.status == 'failed'
