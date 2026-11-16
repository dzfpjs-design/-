"""Root-executed synthetic request-local reuse; no original files/DB/provider.

The real complete directory validator and canonical content encoder run here.
The API orchestration test replaces named I/O boundaries explicitly; it does
not prove actual HTTP IAM, capture files, persistence, latency or model value.
"""
import asyncio
import copy
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import evidence_request_scope_v191 as request_scope
from generic_agents import report_evidence_plan as evidence
from generic_agents.security import Principal


P = Principal('10000000-0000-4000-8000-000000000001',
    '10000000-0000-4000-8000-000000000002', 'operator',
    '10000000-0000-4000-8000-000000000003')
ATTEMPT = '10000000-0000-4000-8000-000000000004'
OTHER = '10000000-0000-4000-8000-000000000005'


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure request reuse must not connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def complete_checks(monkeypatch):
    original = evidence._check_evidence_source_uncached
    calls = []
    def counted(catalog):
        calls.append(None)
        return original(catalog)
    monkeypatch.setattr(evidence, '_check_evidence_source_uncached', counted)
    return calls


def catalog(text='公开原文🙂；逐字保留。'):
    return evidence.build_evidence_source('a' * 64,
        [('w:p1', text), ('w:p2', ''), ('o:p1', '另一页公开文字。')], max_span_chars=8)


def test_outside_scope_preserves_complete_validation_and_original_return_identity(complete_checks):
    value = catalog()
    assert evidence.check_evidence_source(value) is value
    assert evidence.check_evidence_source(value) is value
    assert len(complete_checks) == 2


def test_same_request_checks_complete_actual_content_once_even_for_detached_equal_directories(complete_checks):
    value = catalog(); before = copy.deepcopy(value)
    with request_scope.catalog_read_scope(P, ATTEMPT):
        first = evidence.check_evidence_source(value)
        second = evidence.check_evidence_source(copy.deepcopy(value))
        assert first == second == before
        assert first is not value and first is not second
        refs = list(value['index'])
        assert evidence.resolve_evidence(value, refs, refs)[0]['text'] == before['index'][refs[0]]['text']
        assert len(complete_checks) == 1
    assert value == before


def test_mutable_returns_never_change_input_or_a_later_cached_result(complete_checks):
    value = catalog(); before = copy.deepcopy(value)
    with request_scope.catalog_read_scope(P, ATTEMPT):
        first = evidence.check_evidence_source(value)
        first['index']['w:p1/s1']['text'] = '本地乱改'
        first['descriptor']['ordered_source_refs'].reverse()
        first['descriptor']['counts']['spans'] = 999
        second = evidence.check_evidence_source(value)
        assert second == before
        second['index'].clear()
        refs = list(value['index']); resolved = evidence.resolve_evidence(value, refs, refs)
        resolved[0]['text'] = '调用者改动'
        assert evidence.check_evidence_source(value) == before
        assert len(complete_checks) == 1
    assert value == before


def test_reused_catalog_does_not_reuse_or_widen_allowed_reference_scope(complete_checks):
    value = catalog(); word, page = 'w:p1/s1', 'o:p1/s1'
    with request_scope.catalog_read_scope(P, ATTEMPT):
        assert evidence.resolve_span(value, word, [word])['span_ref'] == word
        for ref, allowed in [(page, [word]), (word, []), (word, [word, word]),
                (word, ['w:p999/s1'])]:
            with pytest.raises(ValueError):
                evidence.resolve_span(value, ref, allowed)
        assert evidence.resolve_span(value, page, [page])['span_ref'] == page
        assert len(complete_checks) == 1, 'only directory integrity is shared, never the caller scope'


@pytest.mark.parametrize('mutate', [
    lambda c: c['index']['w:p1/s1'].update(text='篡改但不改描述'),
    lambda c: c['index']['w:p1/s1'].update(text_hash='b' * 64),
    lambda c: c['index']['w:p1/s1'].update(start=1),
    lambda c: c['descriptor']['counts'].update(spans=True),
    lambda c: c['descriptor']['ordered_source_refs'].reverse(),
    lambda c: c['index'].update({'w:p9/s1': copy.deepcopy(c['index']['w:p1/s1'])}),
])
def test_actual_index_types_offsets_order_and_extra_values_cannot_hide_behind_old_descriptor(complete_checks, mutate):
    value = catalog(); original = copy.deepcopy(value)
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(value)
        mutate(value)
        with pytest.raises(ValueError):
            evidence.check_evidence_source(value)
        with pytest.raises(ValueError):
            evidence.check_evidence_source(value)
        assert len(complete_checks) == 3, 'invalid content and failures are never memoized'
        assert evidence.check_evidence_source(original) == original
        assert len(complete_checks) == 3, 'the old valid content may still reuse only its own check'


def test_valid_new_complete_content_under_same_source_id_is_checked_independently(complete_checks):
    first, changed = catalog(), catalog('完全不同但重新正确建目录的正文。')
    assert first['descriptor']['source_hash'] == changed['descriptor']['source_hash']
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(first)
        assert evidence.check_evidence_source(changed) == changed
        assert evidence.check_evidence_source(copy.deepcopy(changed)) == changed
        assert len(complete_checks) == 2


def test_non_json_content_falls_back_to_the_original_rejection_not_an_old_cached_success(complete_checks):
    value = catalog()
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(value)
        value['index']['w:p1/s1']['text'] = {'not', 'json'}
        with pytest.raises(ValueError):
            evidence.check_evidence_source(value)
        assert len(complete_checks) == 2


@pytest.mark.parametrize('limit,value', [('MAX_ENTRIES', 0), ('MAX_RETAINED_BYTES', 1)])
def test_capacity_only_disables_reuse_without_new_business_rejection(monkeypatch, complete_checks, limit, value):
    monkeypatch.setattr(request_scope, limit, value); source = catalog()
    with request_scope.catalog_read_scope(P, ATTEMPT):
        assert evidence.check_evidence_source(source) == source
        assert evidence.check_evidence_source(source) == source
        assert len(complete_checks) == 2


def test_full_entry_budget_keeps_old_valid_result_and_fully_checks_uncached_new_content(monkeypatch, complete_checks):
    monkeypatch.setattr(request_scope, 'MAX_ENTRIES', 1)
    first, second = catalog(), catalog('第二份完整资料。')
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(first)
        evidence.check_evidence_source(second)
        evidence.check_evidence_source(second)
        evidence.check_evidence_source(first)
        assert len(complete_checks) == 3


def test_new_request_and_captured_context_after_exit_cannot_reuse_previous_results(complete_checks):
    value = catalog()
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(value)
        retained = copy_context()
    assert retained.run(evidence.check_evidence_source, value) is value
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(value)
        evidence.check_evidence_source(value)
    assert len(complete_checks) == 3


def test_exception_destroys_entries_even_in_a_previously_copied_context(complete_checks):
    value = catalog(); retained = None
    with pytest.raises(RuntimeError, match='synthetic source failure'):
        with request_scope.catalog_read_scope(P, ATTEMPT):
            evidence.check_evidence_source(value); retained = copy_context()
            raise RuntimeError('synthetic source failure')
    assert retained.run(evidence.check_evidence_source, value) is value
    assert evidence.check_evidence_source(value) is value
    assert len(complete_checks) == 3


@pytest.mark.parametrize('principal,attempt', [
    (P, ATTEMPT), (replace(P, user_id=OTHER), ATTEMPT),
    (replace(P, workspace_id=OTHER), ATTEMPT), (replace(P, session_id=OTHER), ATTEMPT),
    (replace(P, role='admin'), ATTEMPT), (P, OTHER),
])
def test_nested_identity_or_attempt_scopes_are_independent_and_restore_only_the_live_outer_scope(complete_checks, principal, attempt):
    value = catalog()
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(value)
        with request_scope.catalog_read_scope(principal, attempt):
            evidence.check_evidence_source(value)
            evidence.check_evidence_source(value)
        evidence.check_evidence_source(value)
        assert len(complete_checks) == 2


def test_nested_exception_destroys_child_without_exposing_or_erasing_the_live_parent(complete_checks):
    value = catalog()
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(value)
        with pytest.raises(RuntimeError):
            with request_scope.catalog_read_scope(P, OTHER):
                evidence.check_evidence_source(value); child = copy_context()
                raise RuntimeError('nested failure')
        assert child.run(evidence.check_evidence_source, value) is value
        evidence.check_evidence_source(value)
        assert len(complete_checks) == 3


def test_incomplete_identity_scope_never_inherits_outer_result_or_adds_a_business_error(complete_checks):
    value = catalog(); incomplete = SimpleNamespace(user_id=P.user_id, workspace_id=P.workspace_id, role=P.role)
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(value)
        with request_scope.catalog_read_scope(incomplete, ATTEMPT):
            assert evidence.check_evidence_source(value) is value
            assert evidence.check_evidence_source(value) is value
        evidence.check_evidence_source(value)
    assert len(complete_checks) == 3


def test_changed_mutable_principal_disables_reuse_without_treating_cache_as_iam(complete_checks):
    value = catalog(); mutable = SimpleNamespace(**vars(P))
    with request_scope.catalog_read_scope(mutable, ATTEMPT):
        evidence.check_evidence_source(value)
        mutable.session_id = OTHER
        assert evidence.check_evidence_source(value) is value
        assert evidence.check_evidence_source(value) is value
    assert len(complete_checks) == 3


def test_copied_context_in_another_actual_thread_cannot_read_or_populate_parent_entries(complete_checks):
    value = catalog()
    with request_scope.catalog_read_scope(P, ATTEMPT):
        evidence.check_evidence_source(value); inherited = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(inherited.run, evidence.check_evidence_source, value).result(timeout=5)
        assert result is value
        evidence.check_evidence_source(value)
        assert len(complete_checks) == 2


def test_async_children_and_to_thread_cannot_reuse_parent_even_with_inherited_context(complete_checks):
    value = catalog()
    async def scenario():
        with request_scope.catalog_read_scope(P, ATTEMPT):
            evidence.check_evidence_source(value)
            await asyncio.sleep(0)
            evidence.check_evidence_source(value)
            async def child():
                assert evidence.check_evidence_source(value) is value
                assert evidence.check_evidence_source(value) is value
            await asyncio.create_task(child())
            result = await asyncio.to_thread(evidence.check_evidence_source, value)
            assert result is value
            evidence.check_evidence_source(value)
            assert len(complete_checks) == 4
    asyncio.run(scenario())


def test_each_child_task_can_create_its_own_scope_but_not_share_with_sibling_or_parent(complete_checks):
    value = catalog()
    async def child(attempt):
        with request_scope.catalog_read_scope(P, attempt):
            evidence.check_evidence_source(value)
            await asyncio.sleep(0)
            evidence.check_evidence_source(value)
    async def scenario():
        with request_scope.catalog_read_scope(P, ATTEMPT):
            evidence.check_evidence_source(value)
            await asyncio.gather(child(ATTEMPT), child(ATTEMPT))
            evidence.check_evidence_source(value)
    asyncio.run(scenario())
    assert len(complete_checks) == 3


def test_unrelated_callback_cannot_prime_the_complete_public_validator(complete_checks):
    value = catalog(); value['index']['w:p1/s1']['text'] = '未重算hash'
    with request_scope.catalog_read_scope(P, ATTEMPT):
        assert request_scope._check_catalog(value, lambda item: item) == value
        with pytest.raises(ValueError):
            evidence.check_evidence_source(value)
    assert len(complete_checks) == 1


def test_actual_detail_orchestration_only_reuses_directory_and_repeats_all_io_boundaries_each_get(monkeypatch, complete_checks):
    from generic_agents import business_flow_review_api as api
    value = catalog(); calls = []
    def read_saved(db, principal, attempt):
        assert principal is P and attempt == ATTEMPT
        calls.append('saved_inventory_and_iam_boundary'); evidence.check_evidence_source(value)
        return None
    def read_source(db, principal, attempt):
        assert principal is P and attempt == ATTEMPT
        calls.append('original_inventory_capture_journal_boundary'); evidence.check_evidence_source(copy.deepcopy(value))
        return {'name': '公开测试业务', 'source_binding': {}, 'baseline_value': {'business': {'name': '公开测试业务'}}}
    def diagnose(loaded, current):
        calls.append('current_draft_diagnosis'); evidence.check_evidence_source(value)
        return {'available': True, 'semantic_quality_verified': False, 'can_execute': False}
    monkeypatch.setattr(api.store, 'get', read_saved)
    monkeypatch.setattr(api.source, 'load', read_source)
    monkeypatch.setattr(api.source, 'diagnose', diagnose)
    first = api.detail(object(), P, ATTEMPT); second = api.detail(object(), P, ATTEMPT)
    assert first == second and first['source_available'] is True
    assert first['current_source_checked'] is first['can_apply'] is first['can_publish'] is first['can_execute'] is False
    assert calls == ['saved_inventory_and_iam_boundary', 'original_inventory_capture_journal_boundary', 'current_draft_diagnosis'] * 2
    assert len(complete_checks) == 2, 'a second GET always starts an empty directory scope'
    assert evidence.check_evidence_source(value) is value
    assert len(complete_checks) == 3


def test_detail_source_exception_or_denied_role_cannot_leave_a_live_directory_result(monkeypatch, complete_checks):
    from generic_agents import business_flow_review_api as api
    value = catalog(); calls = []
    monkeypatch.setattr(api.store, 'get', lambda *args: None)
    def unavailable(*args):
        calls.append('source'); evidence.check_evidence_source(value)
        raise HTTPException(409, 'synthetic missing original capture')
    monkeypatch.setattr(api.source, 'load', unavailable)
    with pytest.raises(HTTPException) as error:
        api.detail(object(), P, ATTEMPT)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as denied:
        api.detail(object(), replace(P, role='viewer'), ATTEMPT)
    assert denied.value.status_code == 403 and calls == ['source']
    assert evidence.check_evidence_source(value) is value
    assert len(complete_checks) == 2
