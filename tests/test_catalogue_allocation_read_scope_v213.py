"""Pure allocation-check reuse; never actual source/IAM/paid acceptance.

The real native validator and public synthetic evidence exercise normal and
tampered inputs. A mocked inventory boundary below tests scope lifetime only,
not database rows, financial records, files, current permissions or dispatch.
"""
import asyncio
import copy
import hashlib
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from generic_agents import report_catalogue_allocation_read_scope_v213 as reuse
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents.report_evidence_strict_map import StrictMapError
from tests.test_report_business_catalogue_allocation_native_v213 import scene


@pytest.fixture
def evidence():
    return scene(True)[3]


@pytest.fixture
def principal(evidence):
    snapshot = evidence['source_context']['snapshot']
    return NS(user_id=snapshot['actor_id'], workspace_id=snapshot['workspace_id'],
              session_id='public-scope-session', role='operator')


@pytest.fixture
def checks(monkeypatch):
    original, calls = native._check_allocation_evidence_uncached, []

    def counted(value):
        calls.append(None)
        return original(value)

    monkeypatch.setattr(native, '_check_allocation_evidence_uncached', counted)
    return calls


def test_default_and_incomplete_identity_keep_the_original_full_path(evidence, principal, checks):
    assert native.check_allocation_evidence(evidence) == evidence
    assert native.check_allocation_evidence(evidence) == evidence
    del principal.session_id
    with reuse.allocation_read_scope(principal):
        assert native.check_allocation_evidence(evidence) == evidence
        assert native.check_allocation_evidence(evidence) == evidence
    assert len(checks) == 4


def test_equal_full_checks_detach_results_and_next_scope_revalidates(evidence, principal, checks):
    original = copy.deepcopy(evidence)
    with reuse.allocation_read_scope(principal):
        first = native.check_allocation_evidence(evidence)
        first['document']['businesses'][0]['purpose'] = 'caller-local mutation'
        for _ in range(4):
            result = native.check_allocation_evidence(copy.deepcopy(evidence))
            assert result == original and result is not evidence
        assert len(checks) == 1
        state = reuse._CURRENT.get()
        assert len(state.entries) == 1
        assert list(result) == list(original)
    assert state.closed and state.entries == [] and state.retained_bytes == 0
    with reuse.allocation_read_scope(principal):
        assert native.check_allocation_evidence(evidence) == original
    assert len(checks) == 2 and evidence == original


def test_different_actual_value_rechecks_even_with_unchanged_embedded_hashes(evidence, principal, checks):
    changed = copy.deepcopy(evidence)
    changed['note'] += ' another complete user requirement'
    with reuse.allocation_read_scope(principal):
        assert native.check_allocation_evidence(evidence) == evidence
        assert native.check_allocation_evidence(changed) == changed
        assert native.check_allocation_evidence(evidence) == evidence
    assert len(checks) == 2


def test_original_catalog_tamper_is_not_hidden_by_a_previous_success(evidence, principal, checks):
    changed = copy.deepcopy(evidence)
    directory = changed['source_context']['catalog']['index']
    first = next(iter(directory))
    directory[first]['text'] += ' changed original text'
    with reuse.allocation_read_scope(principal):
        native.check_allocation_evidence(evidence)
        for _ in range(2):
            with pytest.raises(StrictMapError):
                native.check_allocation_evidence(changed)
    assert len(checks) == 3, 'failed original checks cannot become cached results'


def test_replaced_uncached_validator_cannot_borrow_another_validators_success(evidence, principal, checks, monkeypatch):
    with reuse.allocation_read_scope(principal):
        native.check_allocation_evidence(evidence)
        calls = []

        def replacement(value):
            calls.append(None)
            raise ValueError('public replacement rejects the same bytes')

        monkeypatch.setattr(native, '_check_allocation_evidence_uncached', replacement)
        for _ in range(2):
            with pytest.raises(StrictMapError):
                native.check_allocation_evidence(evidence)
    assert len(checks) == 1 and len(calls) == 2


@pytest.mark.parametrize('limit', ['MAX_RETAINED_BYTES', 'MAX_ENTRIES'])
def test_capacity_disables_reuse_without_a_new_rejection(evidence, principal, checks, monkeypatch, limit):
    monkeypatch.setattr(reuse, limit, 1 if limit == 'MAX_RETAINED_BYTES' else 0)
    with reuse.allocation_read_scope(principal):
        assert native.check_allocation_evidence(evidence) == evidence
        assert native.check_allocation_evidence(evidence) == evidence
        assert reuse._CURRENT.get().entries == []
    assert len(checks) == 2


def test_fingerprint_failure_uses_the_original_validator(evidence, principal, checks, monkeypatch):
    def unavailable(value):
        raise ValueError('private cache serialization unavailable')

    monkeypatch.setattr(reuse, 'canonical_json', unavailable)
    with reuse.allocation_read_scope(principal):
        assert native.check_allocation_evidence(evidence) == evidence
        assert native.check_allocation_evidence(evidence) == evidence
    assert len(checks) == 2


def test_compression_failure_uses_the_successful_original_result(evidence, principal, checks, monkeypatch):
    def unavailable(value):
        raise zlib.error('private cache compression unavailable')

    monkeypatch.setattr(reuse.zlib, 'compress', unavailable)
    with reuse.allocation_read_scope(principal):
        assert native.check_allocation_evidence(evidence) == evidence
        assert native.check_allocation_evidence(evidence) == evidence
    assert len(checks) == 2


@pytest.mark.parametrize('damage', ['broken', 'trailing', 'short', 'oversized_length', 'collision'])
def test_damaged_private_key_is_a_miss_with_a_complete_recheck(evidence, principal, checks, damage):
    with reuse.allocation_read_scope(principal):
        native.check_allocation_evidence(evidence)
        state = reuse._CURRENT.get()
        entry = state.entries[0]
        incoming = evidence
        if damage == 'broken':
            entry = replace(entry, content=b'not a zlib stream')
        elif damage == 'trailing':
            entry = replace(entry, content=entry.content + b'extra compressed tail')
        elif damage == 'short':
            entry = replace(entry, content=entry.content[:-1])
        elif damage == 'oversized_length':
            entry = replace(entry, original_length=10 ** 12)
        else:
            incoming = copy.deepcopy(evidence)
            incoming['note'] = '另' + incoming['note'][1:]
            # Even a forged matching SHA cannot make old full bytes a hit.
            assert len(reuse._content(incoming)) == entry.original_length
            entry = replace(entry, fingerprint=hashlib.sha256(reuse._content(incoming)).digest(),
                            original_length=len(reuse._content(incoming)))
        state.entries[0] = entry
        assert native.check_allocation_evidence(incoming) == incoming
    assert len(checks) == 2


def test_lossless_key_fits_with_the_unchanged_result_order_when_two_raw_copies_would_not(principal, monkeypatch):
    # A private adapter-size test, not a valid source/paid evidence fixture.
    value = {'z_last': 'x' * 1000000, 'a_first': True}
    calls = []

    def original(item):
        calls.append(None)
        return copy.deepcopy(item)

    monkeypatch.setattr(reuse, 'MAX_RETAINED_BYTES', 1100000)
    actual = reuse._content(value)
    assert len(actual) < reuse.MAX_RETAINED_BYTES < 2 * len(actual)
    with reuse.allocation_read_scope(principal):
        first = reuse._check(value, original)
        second = reuse._check(copy.deepcopy(value), original)
        assert first == second == value and list(first) == list(second) == list(value)
        state = reuse._CURRENT.get()
        assert len(state.entries) == 1 and state.retained_bytes <= reuse.MAX_RETAINED_BYTES
        assert reuse._key_matches(state.entries[0], actual, hashlib.sha256(actual).digest())
    assert len(calls) == 1


def test_mutating_validator_is_not_cached_and_original_fallback_identity_survives(principal):
    value, calls = {'counter': 0}, []

    def original(item):
        calls.append(None)
        item['counter'] += 1
        return item

    assert reuse._check(value, original) is value
    with reuse.allocation_read_scope(principal):
        assert reuse._check(value, original) is value
        assert reuse._check(value, original) is value
        assert reuse._CURRENT.get().entries == []
    assert len(calls) == 3 and value['counter'] == 3


def test_copied_context_cannot_reuse_in_another_thread_or_after_scope(evidence, principal, checks):
    with reuse.allocation_read_scope(principal):
        native.check_allocation_evidence(evidence)
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(copied.run, native.check_allocation_evidence, evidence).result(timeout=10) == evidence
        assert len(checks) == 2
        assert native.check_allocation_evidence(evidence) == evidence
        assert len(checks) == 2
    assert copied.run(native.check_allocation_evidence, evidence) == evidence
    assert len(checks) == 3


def test_copied_async_task_cannot_reuse_its_parent_scope(evidence, principal, checks):
    async def child():
        return native.check_allocation_evidence(evidence)

    async def scenario():
        with reuse.allocation_read_scope(principal):
            native.check_allocation_evidence(evidence)
            assert await asyncio.create_task(child()) == evidence
            assert len(checks) == 2
            assert native.check_allocation_evidence(evidence) == evidence
            assert len(checks) == 2

    asyncio.run(scenario())


def test_binding_change_nested_scope_and_exception_do_not_share_lifetimes(evidence, principal, checks):
    with pytest.raises(RuntimeError):
        with reuse.allocation_read_scope(principal):
            native.check_allocation_evidence(evidence)
            with reuse.allocation_read_scope(principal):
                native.check_allocation_evidence(evidence)
            principal.role = 'viewer'
            native.check_allocation_evidence(evidence)
            assert len(checks) == 3
            state = reuse._CURRENT.get()
            raise RuntimeError('leave the private scope')
    assert reuse._CURRENT.get() is None and state.closed and state.entries == []
    principal.role = 'operator'
    native.check_allocation_evidence(evidence)
    assert len(checks) == 4


def test_inventory_boundary_runs_every_time_and_only_shares_its_internal_pure_checks(evidence, principal, checks, monkeypatch):
    boundaries = []

    def actual_boundary_spy(db, actor, *, exclusive=False):
        assert actor is principal
        boundaries.append(exclusive)
        native.check_allocation_evidence(evidence)
        native.check_allocation_evidence(evidence)
        return {'public_boundary_only': True}

    monkeypatch.setattr(consumer, '_inventory_value', actual_boundary_spy)
    assert consumer._inventory(None, principal) == {'public_boundary_only': True}
    assert consumer._inventory(None, principal, exclusive=True) == {'public_boundary_only': True}
    assert boundaries == [False, True] and len(checks) == 2
    assert reuse._CURRENT.get() is None
