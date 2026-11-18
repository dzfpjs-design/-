"""The internal reduce contract never opens from environment configuration."""
import pytest
from types import SimpleNamespace
from fastapi import HTTPException
from generic_agents import report_evidence_delegated_reduce_start as subject
from generic_agents import report_evidence_delegated_dispatch_gate as dispatch
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_delegated_reduce_preflight as preflight


def test_reduce_default_closed_before_database_or_transport(monkeypatch):
    monkeypatch.setenv('GA_ENABLE_DELEGATED_REPORT', 'true')
    assert subject._enabled() is False and dispatch.ready() is False
    with pytest.raises(subject.ReduceAuthorizationDenied):
        subject.preview_tx(None,None,None,None,None)
    with pytest.raises(subject.ReduceAuthorizationDenied):
        subject.start_tx(None,None,None,None,None,None)


@pytest.mark.parametrize(('usage','accepted'), [({'total_tokens':20},True),
    ({'total_tokens':101},False), ({'total_tokens':True},False), ({},False), (None,False)])
def test_unknown_or_over_bound_usage_cannot_become_a_reduce_result(monkeypatch,usage,accepted):
    monkeypatch.setattr(pipeline,'_authorized',lambda row,key:{'input_summary':{'token_bound':100}})
    row=SimpleNamespace(request_id='fl_10000000-0000-4000-8000-000000000001')
    assert pipeline._delegated_reduce_usage_valid(row,'reduce',{'usage':usage}) is accepted


@pytest.mark.parametrize('usage',[{}, {'total_tokens':101}])
def test_reduce_never_adopts_a_result_with_unverified_usage(monkeypatch,usage):
    row=SimpleNamespace(request_id='fl_10000000-0000-4000-8000-000000000001')
    monkeypatch.setattr(pipeline,'_authorized',lambda row,key:{'input_summary':{'token_bound':100}})
    monkeypatch.setattr(pipeline,'_stage',lambda *_:{'reserved_call_id':'original-call'})
    monkeypatch.setattr(pipeline,'_descriptor',lambda *_:{'capture':'original'})
    monkeypatch.setattr(pipeline,'_parsed',lambda *_:{'receipt':{'usage':usage}})
    monkeypatch.setattr(pipeline,'_schema_valid',lambda *_:False)
    settlements=[]
    monkeypatch.setattr(pipeline,'_settle',lambda *args:settlements.append(args[-1]))
    monkeypatch.setattr(pipeline,'_catalog',lambda *_:pytest.fail('unsafe adoption'))
    with pytest.raises(HTTPException) as error:
        pipeline._consume(None,None,row,'reduce')
    assert error.value.detail['code']=='delegated_reduce_usage_unverified'
    assert settlements==[False]


def test_map_intent_versions_are_exact_and_deadline_is_bounded():
    expected={'schema':'intent','token_bound':1}
    period={'not_before':'2026-01-01T00:00:00+00:00','expires_at':'2026-01-01T01:00:00+00:00'}
    timed={**expected,'issued_at':'2026-01-01T00:01:00+00:00',
        'not_after':'2026-01-01T00:01:30+00:00','lease_until':'2026-01-01T00:02:00+00:00'}
    assert preflight._valid_map_intent(expected,expected,period,period)
    assert preflight._valid_map_intent(timed,expected,period,period)
    assert not preflight._valid_map_intent({**timed,'not_after':'2026-01-01T00:01:31+00:00'},expected,period,period)
    assert not preflight._valid_map_intent({**timed,'unexpected':True},expected,period,period)
    assert not preflight._valid_map_intent({**expected,'token_bound':True},expected,period,period)
