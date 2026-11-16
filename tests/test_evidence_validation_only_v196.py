"""Real integrity/resolution gates, with no discarded whole-catalog copies."""
import copy

import pytest

from generic_agents import evidence_request_scope_v191 as scope
from generic_agents import report_evidence_plan as evidence
from tests.test_evidence_request_scope_v191 import P, ATTEMPT, catalog, no_external_io


def test_validation_only_reuses_checked_content_without_detaching_a_return(monkeypatch):
    value = catalog(); before = copy.deepcopy(value)
    with scope.catalog_read_scope(P, ATTEMPT):
        assert evidence.validate_evidence_source(value) is None
        original_copy = copy.deepcopy
        def selected_only(item, *args, **kwargs):
            if type(item) is dict and set(item) == {'descriptor', 'index'}:
                raise AssertionError('validation-only hit must not copy an unused full catalog')
            return original_copy(item, *args, **kwargs)
        monkeypatch.setattr(scope.copy, 'deepcopy', selected_only)
        assert evidence.validate_evidence_source(value) is None
        word = 'w:p1/s1'
        assert evidence.resolve_span(value, word, [word])['text'] == before['index'][word]['text']
    assert value == before


def test_validation_only_hits_still_reject_mutated_actual_content_and_forbidden_reference():
    value = catalog()
    with scope.catalog_read_scope(P, ATTEMPT):
        evidence.validate_evidence_source(value)
        with pytest.raises(ValueError):
            evidence.resolve_span(value, 'o:p1/s1', ['w:p1/s1'])
        value['index']['w:p1/s1']['text'] = '修改内容但不修改哈希'
        with pytest.raises(ValueError): evidence.validate_evidence_source(value)
        with pytest.raises(ValueError): evidence.resolve_span(value, 'w:p1/s1', ['w:p1/s1'])


def test_validation_only_outside_scope_does_not_cache_validation(monkeypatch):
    value = catalog(); calls = []
    original = evidence._check_evidence_source_uncached
    def counted(actual):
        calls.append(None); return original(actual)
    monkeypatch.setattr(evidence, '_check_evidence_source_uncached', counted)
    assert evidence.validate_evidence_source(value) is None
    assert evidence.validate_evidence_source(value) is None
    assert len(calls) == 2


def test_validation_only_never_exposes_snapshot_to_later_mutable_returns():
    value = catalog(); before = copy.deepcopy(value)
    with scope.catalog_read_scope(P, ATTEMPT):
        evidence.validate_evidence_source(value)
        detached = evidence.check_evidence_source(value)
        detached['index'].clear()
        evidence.validate_evidence_source(value)
        assert evidence.check_evidence_source(value) == before
        value['descriptor']['counts']['spans'] = True
        with pytest.raises(ValueError): evidence.validate_evidence_source(value)


def test_validation_only_does_not_share_a_different_validators_result():
    value = catalog(); calls = []
    def separate(actual):
        calls.append(None)
        raise ValueError('independent validator refuses this content')
    with scope.catalog_read_scope(P, ATTEMPT):
        evidence.validate_evidence_source(value)
        with pytest.raises(ValueError, match='independent validator'):
            scope._check_catalog(value, separate, detach=False)
    assert len(calls) == 1
