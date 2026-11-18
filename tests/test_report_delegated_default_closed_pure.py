"""The unfinished delegated dispatch cannot use the ordinary cloud quota."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import gateway
from generic_agents import report_evidence_delegated_dispatch_gate as gate
from generic_agents import report_evidence_pipeline as pipeline


def test_production_gate_is_closed_and_has_no_environment_toggle(monkeypatch):
    monkeypatch.setenv('GA_ENABLE_DELEGATED_REPORT', 'true')
    monkeypatch.setenv('GA_CLOUD_DAILY_TOKEN_LIMIT', '999999999')
    assert gate.ready() is False


def test_direct_delegated_gateway_refuses_before_reserve_or_transport(monkeypatch):
    from generic_agents import cloud_budget
    invoked = []
    monkeypatch.setattr(cloud_budget, 'reserve', lambda *a, **k: invoked.append('reserve'))
    with pytest.raises(gateway.ModelBlocked, match='尚未接通'):
        gateway._cloud_json(SimpleNamespace(), [], {}, SimpleNamespace(),
            full_report=True, evidence_stage='map-v3', on_reserved=lambda *_: None,
            on_response=lambda *_: None, delegated_evidence_wire_guard=True)
    assert invoked == []


def test_delegated_worker_refuses_before_stage_change_or_cloud(monkeypatch):
    stage = {'status': 'pending', 'reserved_call_id': None, 'inflight': False}
    row = SimpleNamespace(request_id='fl_10000000-0000-4000-8000-000000000001')
    touched = []
    monkeypatch.setattr(pipeline, '_stage', lambda *_: stage)
    monkeypatch.setattr(pipeline, '_descriptor', lambda *_: None)
    monkeypatch.setattr(pipeline, '_fresh_cap', lambda *_a, **_k: touched.append('model'))
    with pytest.raises(HTTPException) as error:
        pipeline._run_stage(object(), object(), row, 'map-1')
    assert error.value.detail['code'] == 'delegated_dispatch_not_ready'
    assert touched == []
    assert stage == {'status': 'pending', 'reserved_call_id': None, 'inflight': False}
