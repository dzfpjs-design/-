"""A reconstruction is accepted only when the original independent hash fits."""
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import goal_evaluation_query_v288 as q
from generic_agents import workflow_goal_evaluation_query_repair_v296 as repair
from generic_agents.contracts import DataRequest


def original():
    requirement = {'quality': 'confirmed'}
    request = {'entity_ids': ['object'], 'fields': ['humidity'], 'mode': 'structured'}
    window = {'start': '2026-11-02T06:45:59+00:00', 'end': '2026-11-02T07:45:59+00:00'}
    marker = {'cut_at': '2026-11-02T07:45:59+00:00', 'derived_window': window,
        'goal_snapshot': {'spec': {'evidence': requirement}}, 'creator': {'invocation_id': 'original'}}
    row = SimpleNamespace(id='original-query', version=1, status='queued',
        data={'request': request, q.KEY: marker})
    state = {'request': copy.deepcopy(request), 'status': 'insufficient',
        'need': {'fields': ['humidity']}, 'result': {'status': 'insufficient',
            'resolved_time': copy.deepcopy(window), 'evaluated_at': marker['cut_at'],
            'evidence_requirement': requirement, 'gaps': [{'field': 'humidity', 'reason': 'stale'}],
            'observation_evidence': [], 'excluded_evidence': [{'id': 'retained-observation'}]}}
    saved = SimpleNamespace(data={'query_ref': repair._reference(row, state)[0]})
    return row, state, saved


def test_restores_exact_original_insufficient_result_without_mutating_originals():
    row, state, saved = original(); before = copy.deepcopy(row.data)
    data = repair._candidate(row, state, saved)
    assert data['result']['status'] == 'insufficient'
    assert row.data == before and 'result' not in row.data and row.status == 'queued'
    state['result']['gaps'].clear()
    assert data['result']['result']['gaps']


@pytest.mark.parametrize('part', ['status', 'cut', 'window', 'request', 'value', 'exclusion', 'creator', 'version'])
def test_plausible_but_changed_result_or_provenance_cannot_replace_saved_digest(part):
    row, state, saved = original()
    if part == 'status': state['status'] = state['result']['status'] = 'sufficient'
    if part == 'cut': state['result']['evaluated_at'] = '2026-11-02T07:46:00+00:00'
    if part == 'window': state['result']['resolved_time']['end'] = '2026-11-02T08:45:59+00:00'
    if part == 'request': state['request']['fields'] = ['temperature']
    if part == 'value': state['result']['observation_evidence'] = [{'humidity': 70}]
    if part == 'exclusion': state['result']['excluded_evidence'] = []
    if part == 'creator': row.data[q.KEY]['creator']['invocation_id'] = 'replacement'
    if part == 'version': row.version = 2
    with pytest.raises(HTTPException): repair._candidate(row, state, saved)
    assert 'result' not in row.data


def test_restore_refuses_without_real_worker_fence_before_any_query(monkeypatch):
    from generic_agents import leases
    monkeypatch.setattr(leases, 'ownership', lambda db: {})
    monkeypatch.setattr(leases, 'fenced', lambda db: False)
    monkeypatch.setattr(repair, 'inspect_retained', lambda *a: pytest.fail('authority reached without fence'))
    with pytest.raises(HTTPException):
        repair.restore_result(object(), None, None, SimpleNamespace(id='original-job'), None,
            None, None, None, None)


def prepared_request():
    marker = {'cut_at': '2026-11-02T07:45:59+00:00', 'derived_window': {'mode': 'latest'}}
    request = DataRequest(question='检查湿度', entity_ids=['object'], fields=['humidity'],
        mode='structured').model_dump(mode='json')
    request.update(resolved_time=copy.deepcopy(marker['derived_window']), clarifications=[],
        reference_at=marker['cut_at'])
    return request, marker


def test_original_prepared_request_preserves_normal_server_derived_fields():
    request, marker = prepared_request()
    repair._request_shape(request, marker)


@pytest.mark.parametrize('part', ['extra', 'missing', 'cut', 'clarification', 'window', 'coercion'])
def test_changed_prepared_request_shape_or_cut_cannot_be_reconstructed(part):
    request, marker = prepared_request()
    if part == 'extra': request['retry_with_new_cut'] = True
    if part == 'missing': del request['reference_at']
    if part == 'cut': request['reference_at'] = '2026-11-02T07:46:00+00:00'
    if part == 'clarification': request['clarifications'] = ['different']
    if part == 'window': request['resolved_time'] = {'mode': 'archive'}
    if part == 'coercion': request['allow_external_tools'] = 0
    with pytest.raises(HTTPException): repair._request_shape(request, marker)
