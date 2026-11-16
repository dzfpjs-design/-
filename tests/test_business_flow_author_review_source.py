"""Fresh-source review boundaries; public synthetic capture/context only."""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_qualification_api as api
from generic_agents import business_flow_review_source as source
from generic_agents import report_design_saved_source_v174 as fresh
from generic_agents.contracts import digest
from tests.test_business_flow_review_api_pure import source_case
from tests.test_report_business_flow_protocol_v182 import answer


def case(monkeypatch):
    c = source_case(monkeypatch)
    loaded = source.load(c.db, c.p, c.row.id)
    original = copy.deepcopy(c.row.data['preview']['source_context'])
    # The public source fixture's original context is re-read by the real
    # diagnostics. Only physical source authority is replaced here.
    calls = []
    def current(db, p, producer_id, *, lock):
        assert db is c.db and p is c.p and producer_id == c.row.producer_id and lock is True
        calls.append('current_material_and_acl'); return copy.deepcopy(original)
    monkeypatch.setattr(fresh, 'load_source_context', current)
    value = answer(c.row.data['preview']['preparation'])
    value['business']['questions'] = []
    # Drop only the question's no-longer-used source, as an explicit fixture
    # author action; the source code never repairs a candidate.
    used = set()
    def scan(v):
        if type(v) is dict:
            for k, item in v.items():
                if k == 'citations': used.update(item)
                else: scan(item)
        elif type(v) is list:
            for item in v: scan(item)
    scan(value['business'])
    value['evidence'] = [q for q in value['evidence'] if q['span_ref'] in used]
    saved = {'attempt_id': c.row.id, 'source_binding': loaded['source_binding'],
        'baseline_value': loaded['baseline_value'], 'value': value}
    return c, saved, original, calls


def test_fresh_review_uses_exact_original_and_current_material_context_without_configuration_revision_gate(monkeypatch):
    c, saved, original, calls = case(monkeypatch)
    before = copy.deepcopy(c.row.data)
    result = source.current_review_source(c.db, c.p, c.row.id, saved)
    assert calls == ['current_material_and_acl']
    assert result == {'schema': 'business-flow-author-review-source-v1', 'attempt_id': c.row.id,
        'source_binding_hash': digest(saved['source_binding']), 'value_hash': digest(saved['value']),
        'diagnostic_context_hash': digest(source.load(c.db, c.p, c.row.id)['diagnostic_context']),
        'original_source_hash': digest(original), 'current_source_hash': digest(original),
        'current_materials_hash': digest(original['snapshot']['materials'])}
    assert c.row.data == before and c.row.status == 'failed'


@pytest.mark.parametrize('fault', ['context', 'materials', 'catalog', 'snapshot_binding', 'different_original',
    'unresolved_question', 'short_quote', 'invalid_graph'])
def test_source_changes_or_unresolved_draft_block_new_review(monkeypatch, fault):
    c, saved, original, _ = case(monkeypatch)
    if fault in {'context', 'materials', 'catalog'}:
        current = copy.deepcopy(original)
        if fault == 'context': current['context']['note'] = 'changed'
        elif fault == 'materials': current['snapshot']['materials'].append({'changed': True})
        else: current['catalog']['changed'] = True
        monkeypatch.setattr(fresh, 'load_source_context', lambda *a, **k: current)
    elif fault == 'snapshot_binding':
        saved['source_binding']['source_snapshot_hash'] = 'a' * 64
    elif fault == 'different_original': saved['baseline_value'] = {}
    elif fault == 'unresolved_question':
        saved['value']['business']['questions'] = [{'question': '仍有不明确的业务职责', 'reason': '需核实', 'citations': []}]
    elif fault == 'short_quote': saved['value']['evidence'][0]['quote'] = '不能当作完整原文'
    else: saved['value']['business']['flows'][0]['end_nodes'] = ['missing']
    with pytest.raises(HTTPException) as error:
        source.current_review_source(c.db, c.p, c.row.id, saved)
    assert error.value.status_code in {409, 422}


def test_database_failure_is_not_converted_to_source_success_or_review(monkeypatch):
    c, saved, _, _ = case(monkeypatch)
    class DatabaseFailure(Exception): pass
    def failed(*a, **k): raise DatabaseFailure('synthetic database failure')
    monkeypatch.setattr(fresh, 'load_source_context', failed)
    with pytest.raises(DatabaseFailure): source.current_review_source(c.db, c.p, c.row.id, saved)


def test_qualification_replays_original_before_any_source_or_current_draft_access(monkeypatch):
    events = []
    module = NS(parse_wire=lambda raw: {}, replay=lambda *a, **k: {'original': True})
    monkeypatch.setattr(api, 'storage', lambda: module)
    def forbidden(*a, **k): raise AssertionError('original recovery must not renew source authority')
    monkeypatch.setattr(api, 'saved_for_review', forbidden)
    result = api.qualify(object(), NS(role='operator'), '10000000-0000-4000-8000-000000000001', b'{}', 'POST', '/same')
    assert result == {'original': True} and not events


def test_new_qualification_rechecks_current_source_before_store_create(monkeypatch):
    c, saved, _, _ = case(monkeypatch); events = []
    module = NS(parse_wire=lambda raw: {}, replay=lambda *a, **k: None,
        create=lambda *a, **k: (events.append(('create', k['source_snapshot'])), {'created': True})[1])
    monkeypatch.setattr(api, 'storage', lambda: module)
    monkeypatch.setattr(api, 'saved_for_review', lambda *a: (events.append('saved'), saved)[1])
    result = api.qualify(c.db, c.p, c.row.id, b'{}', 'POST', '/same')
    assert result == {'created': True} and events[0] == 'saved'
    assert events[1][0] == 'create' and events[1][1]['value_hash'] == digest(saved['value'])


@pytest.mark.parametrize('stage,code,proven', [('source', 409, True), ('source', 422, True),
    ('source', 403, False), ('create', 409, False)])
def test_only_read_only_source_failures_after_absent_nonce_are_proven_not_applied(monkeypatch, stage, code, proven):
    c, saved, _, _ = case(monkeypatch)
    def failed(*a, **k): raise HTTPException(code, 'synthetic exact stage failure')
    module = NS(parse_wire=lambda raw: {}, replay=lambda *a, **k: None, create=failed)
    monkeypatch.setattr(api, 'storage', lambda: module)
    monkeypatch.setattr(api, 'saved_for_review', lambda *a: saved)
    if stage == 'source': monkeypatch.setattr(source, 'current_review_source', failed)
    with pytest.raises(HTTPException) as error:
        api.qualify(c.db, c.p, c.row.id, b'{}', 'POST', '/same')
    assert error.value.status_code == code
    if proven:
        assert error.value.detail['code'] == 'business_flow_author_review_source_not_applied'
        assert error.value.detail['not_applied'] is True
    else: assert error.value.detail == 'synthetic exact stage failure'
