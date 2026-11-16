"""Saved-plan statement search, not current-source or paid permission proof.

The established public store IO fixture mocks SQL/source boundaries while real
plan commands, original Audits and saved-source validators run. The separate
route spy covers real Starlette query parsing/forwarding only.
"""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from generic_agents import report_scoped_design_store_v181 as subject
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_scoped_design_store_v181 import setup, write, uid


def test_missing_query_keeps_old_response_and_explicit_empty_only_adds_search_metadata(setup, monkeypatch):
    io, _, loads = setup
    write(io, 'create')
    row = io.rows[0]
    before = canonical_json(row.data)

    def no_current_source(*args, **kwargs):
        raise AssertionError('display search cannot obtain a new source grant')

    monkeypatch.setattr(subject, 'runtime', no_current_source)
    baseline = subject.plan_inputs(io, io.p, row.id, offset=0, limit=2)
    empty = subject.plan_inputs(io, io.p, row.id, offset=0, limit=2, query='')
    assert 'query' not in baseline and 'matching_input_count' not in baseline
    assert empty['query'] == '' and empty['matching_input_count'] == baseline['input_count']
    original_shape = {key: value for key, value in empty.items()
                      if key not in {'query', 'matching_input_count'}}
    assert json.dumps(original_shape, ensure_ascii=False) == json.dumps(baseline, ensure_ascii=False)
    assert canonical_json(row.data) == before and len(loads) == 1


def test_matched_pagination_preserves_duplicate_global_positions_ids_and_exact_saved_evidence(setup, monkeypatch):
    io, source, loads = setup
    write(io, 'create')
    row = io.rows[0]
    snapshot, claims, _ = subject._inputs(source)
    assert claims[0] == claims[1]
    query = claims[0]['statement']
    before = canonical_json(row.data)
    calls_before = len(io.calls)

    def no_current_source(*args, **kwargs):
        raise AssertionError('search reads the original saved plan, not a replacement source')

    monkeypatch.setattr(subject, 'runtime', no_current_source)
    offset, items = 0, []
    while True:
        page = subject.plan_inputs(io, io.p, row.id, offset=offset, limit=1, query=query)
        assert page['query'] == query and page['input_count'] == len(claims)
        assert page['matching_input_count'] >= 2 and page['offset'] == offset
        assert page['historical_source'] is page['requires_current_source_check'] is True
        assert page['can_apply'] is page['can_publish'] is False
        items.extend(page['items'])
        if page['next_offset'] is None:
            break
        assert page['next_offset'] == offset + 1
        offset = page['next_offset']
    assert len(items) == page['matching_input_count']
    assert [item['global_position'] for item in items[:2]] == [0, 1]
    assert items[0]['input_id'] != items[1]['input_id']
    for item in items:
        position = item['global_position']
        assert item['input_id'] == snapshot['input_order'][position]
        assert item['claim'] == claims[position]
        assert item['spans'] == [{'span_ref': ref, 'text': source['catalog']['index'][ref]['text']}
                                for ref in claims[position]['span_refs']]
    beyond = subject.plan_inputs(io, io.p, row.id, offset=len(items) + 1, limit=100, query=query)
    assert beyond['items'] == [] and beyond['next_offset'] is None
    assert beyond['input_count'] == len(claims) and beyond['matching_input_count'] == len(items)
    items[0]['claim']['statement'] = 'local display change'
    assert canonical_json(row.data) == before and len(loads) == 1
    assert not any(call[0] in {'add', 'flush', 'owner'} or call == ('iam', True)
                   for call in io.calls[calls_before:])


def test_no_matches_reports_zero_without_hiding_the_original_inventory(setup):
    io, source, _ = setup
    write(io, 'create')
    page = subject.plan_inputs(io, io.p, io.rows[0].id,
        query='__no_public_statement_literal_216__', offset=0, limit=10)
    assert page['matching_input_count'] == 0 and page['items'] == [] and page['next_offset'] is None
    assert page['input_count'] == len(source['snapshot']['input_order'])


def test_literal_casefold_filter_preserves_positions_without_regex_trimming_or_deduplication():
    # Pure matching semantics only; this is not an authenticated source fixture.
    texts = ['Straße [A-Z] monitoring', 'Straße [A-Z] monitoring', 'STRASSE daily', '林场监测']
    snapshot = {'input_order': ['first', 'duplicate', 'third', 'fourth'],
        'input_items': {key: {'statement': text} for key, text in
                        zip(['first', 'duplicate', 'third', 'fourth'], texts)}}
    assert subject._matching_input_positions(snapshot, 4, 'strasse') == [0, 1, 2]
    assert subject._matching_input_positions(snapshot, 4, '[A-Z]') == [0, 1]
    assert subject._matching_input_positions(snapshot, 4, ' Straße') == []
    assert subject._matching_input_positions(snapshot, 4, '监测') == [3]
    assert subject._matching_input_positions(snapshot, 4, '') == [0, 1, 2, 3]


@pytest.mark.parametrize('query', [True, 7, [], {}, 'x' * 201, 'bad\x00query', '\ud800'])
def test_query_is_bounded_exact_text_and_never_coerced_or_persisted(setup, query):
    io, _, _ = setup
    write(io, 'create')
    row = io.rows[0]
    before = canonical_json(row.data)
    with pytest.raises(HTTPException) as error:
        subject.plan_inputs(io, io.p, row.id, query=query)
    assert error.value.status_code == 422 and canonical_json(row.data) == before


def test_a_previous_search_does_not_let_corrupt_saved_source_borrow_a_result(setup):
    io, _, _ = setup
    write(io, 'create')
    row = io.rows[0]
    assert subject.plan_inputs(io, io.p, row.id, query='')['matching_input_count'] > 0
    directory = row.data['source_context']['catalog']['index']
    directory[next(iter(directory))]['text'] += ' corrupted original'
    with pytest.raises(subject.InventoryError):
        subject.plan_inputs(io, io.p, row.id, query='')


def route_module():
    from generic_agents import api as application
    from generic_agents import report_scoped_design_api_v181 as routes
    return routes


def request_for(query_string):
    return Request({'type': 'http', 'method': 'GET', 'scheme': 'http', 'server': ('testserver', 80),
        'path': '/api/setup/report-scoped-designs/v181/plans/' + uid(500) + '/inputs',
        'query_string': query_string.encode('ascii'), 'headers': []})


def test_route_omits_default_search_keyword_and_forwards_explicit_empty_query(monkeypatch):
    routes = route_module()
    calls = []

    def reader(db, principal, plan_id, **kwargs):
        calls.append((db, principal, plan_id, kwargs))
        return {'explicit_route_boundary_only': True}

    monkeypatch.setattr(routes.store, 'plan_inputs', reader)
    db, principal = object(), NS()
    routes.inputs(uid(500), request_for('offset=0&limit=2'), principal, db, offset=0, limit=2)
    assert calls[-1] == (db, principal, uid(500), {'offset': 0, 'limit': 2})
    routes.inputs(uid(500), request_for('offset=0&limit=2&query='), principal, db,
                  offset=0, limit=2, query='')
    assert calls[-1] == (db, principal, uid(500), {'offset': 0, 'limit': 2, 'query': ''})


def test_route_rejects_duplicate_query_before_reading_a_plan(monkeypatch):
    routes = route_module()

    def forbidden(*args, **kwargs):
        raise AssertionError('duplicate query reached the plan reader')

    monkeypatch.setattr(routes.store, 'plan_inputs', forbidden)
    with pytest.raises(HTTPException) as error:
        routes.inputs(uid(500), request_for('offset=0&limit=2&query=first&query=second'), NS(), object(),
                      offset=0, limit=2, query='second')
    assert error.value.status_code == 422
