"""Actual V181 selection over public synthetic source/plan fixtures only.

No original document, ORM, capture, receipt, provider, IAM or consent is cached
or represented as verified. Root executes these tests and target HTTP timing.
"""
import asyncio
import copy
import socket
import subprocess
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from generic_agents import evidence_request_scope_v191 as scope
from generic_agents import report_scoped_design_preparation_v181 as prep
from generic_agents.contracts import digest
from generic_agents.security import Principal
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_scoped_design_v181 import scene, plan_for, identity


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure selection reuse cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def selected(monkeypatch):
    _, source, plan = scene(); original = prep._selected_uncached; calls = []
    def counted(*args):
        calls.append(None)
        return original(*args)
    monkeypatch.setattr(prep, '_selected_uncached', counted)
    principal = Principal(source['snapshot']['actor_id'], source['snapshot']['workspace_id'],
        'operator', identity(991))
    return NS(source=source, plan=plan, original=original, calls=calls,
        principal=principal, attempt=identity(992))


def select(case, scope_id='custom:alpha'):
    return prep._selected(case.source, case.plan, scope_id)


def encoded(value):
    return canonical_json(value).encode('utf-8')


def test_outside_scope_retains_the_exact_original_tuple_and_source_alias_behavior(selected):
    c = selected
    first, second = select(c), select(c)
    assert type(first) is tuple and len(first) == 6
    assert first[0] is second[0] is c.source['snapshot']
    assert first == c.original(c.source, c.plan, 'custom:alpha')
    assert len(c.calls) == 2


def test_actual_full_selection_runs_once_for_equal_complete_inputs_and_keeps_all_original_values(selected):
    c = selected; before = encoded([c.source, c.plan]); expected = c.original(c.source, c.plan, 'custom:alpha')
    with scope.catalog_read_scope(c.principal, c.attempt):
        first = select(c)
        second = prep._selected(copy.deepcopy(c.source), copy.deepcopy(c.plan), 'custom:alpha')
        assert first == second == expected and type(first) is type(second) is tuple
        assert first is not second and first[0] is not c.source['snapshot']
        assert first[5]['scope_binding']['local_to_global'] == [0, 2]
        assert first[5]['input_claims'] == [c.source['snapshot']['input_items'][c.source['snapshot']['input_order'][i]] for i in [0, 2]]
        assert len(c.calls) == 1
    assert encoded([c.source, c.plan]) == before


def test_returned_tuple_dicts_lists_and_nested_claims_are_isolated_from_all_later_hits(selected):
    c = selected; expected = c.original(c.source, c.plan, 'custom:alpha'); before = encoded([c.source, c.plan])
    with scope.catalog_read_scope(c.principal, c.attempt):
        first = select(c)
        first[0]['user_instruction'] = '本地乱改原指示'
        first[1]['tasks'].clear(); first[2]['name'] = '本地改业务名'
        first[3].clear(); first[4].append(['not-an-original-span', '本地补文'])
        first[5]['input_claims'][0]['statement'] = '本地改声明'
        first[5]['indexed']['input_claims'].clear()
        assert select(c) == expected
        assert len(c.calls) == 1
    assert encoded([c.source, c.plan]) == before


@pytest.mark.parametrize('fault', ['source_claim', 'catalog_text', 'source_context', 'plan_payload', 'plan_selfseal'])
def test_changed_actual_source_catalog_or_plan_cannot_reuse_old_self_hashes(selected, fault):
    c = selected; original_source, original_plan = copy.deepcopy(c.source), copy.deepcopy(c.plan)
    with scope.catalog_read_scope(c.principal, c.attempt):
        expected = select(c)
        if fault == 'source_claim':
            key = c.source['snapshot']['input_order'][0]
            c.source['snapshot']['input_items'][key]['statement'] += '被篡改'
        elif fault == 'catalog_text':
            ref = next(iter(c.source['catalog']['index']))
            c.source['catalog']['index'][ref]['text'] += '被篡改'
        elif fault == 'source_context': c.source['context']['global_only_marker'] = '替换背景'
        elif fault == 'plan_payload': c.plan['tasks'][0]['payload']['included_positions'] = [0, 1]
        else:
            c.plan['dispatch_allowed'] = True
            c.plan['plan_hash'] = digest({key: value for key, value in c.plan.items() if key != 'plan_hash'})
        for _ in range(2):
            with pytest.raises(ValueError): select(c)
        assert len(c.calls) == 3, 'both failures use complete real validation, not cached errors'
        assert prep._selected(original_source, original_plan, 'custom:alpha') == expected
        assert len(c.calls) == 3


def test_other_valid_scope_and_unknown_scope_keep_their_own_selection_and_failure(selected):
    c = selected
    with scope.catalog_read_scope(c.principal, c.attempt):
        first = select(c)
        second = select(c, 'custom:beta')
        assert first[5]['scope_binding']['local_to_global'] == [0, 2]
        assert second[5]['scope_binding']['local_to_global'] == [1, 3]
        assert second[2]['name'] == '业务范围乙' and select(c, 'custom:beta') == second
        for _ in range(2):
            with pytest.raises(ValueError): select(c, 'custom:not-present')
        assert len(c.calls) == 4


def test_valid_rebuilt_source_and_plan_are_independent_even_when_only_global_instruction_changes(selected):
    c = selected; first_source, first_plan = copy.deepcopy(c.source), copy.deepcopy(c.plan)
    with scope.catalog_read_scope(c.principal, c.attempt):
        first = select(c)
        c.source['snapshot']['user_instruction'] += '；新增公开全局指示。'
        c.source['snapshot']['user_instruction_hash'] = digest(c.source['snapshot']['user_instruction'])
        c.source['snapshot']['snapshot_hash'] = digest({key: value for key, value in c.source['snapshot'].items() if key != 'snapshot_hash'})
        c.plan = plan_for(c.source)
        second = select(c)
        assert second[0]['snapshot_hash'] != first[0]['snapshot_hash']
        assert second[5]['source_snapshot_hash'] == c.source['snapshot']['snapshot_hash']
        assert select(c) == second and len(c.calls) == 2
        assert prep._selected(first_source, first_plan, 'custom:alpha') == first and len(c.calls) == 2


def test_same_digest_collision_still_requires_exact_complete_parameter_bytes(monkeypatch, selected):
    c = selected
    # Replace this module's digest provider only; real source/plan hashes retain
    # their actual implementation. Exact bytes must still distinguish scopes.
    monkeypatch.setattr(scope, 'hashlib', NS(sha256=lambda value: NS(digest=lambda: b'x' * 32)))
    with scope.catalog_read_scope(c.principal, c.attempt):
        first, second = select(c), select(c, 'custom:beta')
        assert first[2]['name'] != second[2]['name']
        assert select(c) == first and select(c, 'custom:beta') == second
        assert len(c.calls) == 2


def test_parameters_changed_by_the_full_selector_are_not_recorded_as_the_original_key(monkeypatch, selected):
    c = selected
    def changes_parameter(source, plan, scope_id):
        c.calls.append(None); result = c.original(source, plan, scope_id)
        plan['changed_during_selection'] = True
        return result
    monkeypatch.setattr(prep, '_selected_uncached', changes_parameter)
    with scope.catalog_read_scope(c.principal, c.attempt):
        first = select(c)
        c.plan.pop('changed_during_selection')
        second = select(c)
        assert first == second and len(c.calls) == 2


def test_a_different_callback_cannot_prime_the_actual_selector(selected):
    c = selected
    with scope.catalog_read_scope(c.principal, c.attempt):
        fake = ({'not_the_source': True}, {}, {}, [], [], {})
        assert scope._selected_inputs(c.source, c.plan, 'custom:alpha', lambda *_: fake) == fake
        actual = select(c)
        assert actual == c.original(c.source, c.plan, 'custom:alpha')
        assert actual != fake and len(c.calls) == 1


def test_budget_counts_actual_parameter_and_result_bytes_and_digest_without_increasing_limits(monkeypatch, selected):
    c = selected; expected = c.original(c.source, c.plan, 'custom:alpha')
    directory_cost = 2 * len(encoded(c.source['catalog'])) + 32
    selection_cost = len(zlib.compress(encoded([c.source, c.plan, 'custom:alpha']))) + len(encoded(list(expected))) + 32
    # The first actual selection also installs exactly one same-source catalog
    # entry. Leave one byte too little for the COMPLETE selection result.
    monkeypatch.setattr(scope, 'MAX_RETAINED_BYTES', directory_cost + selection_cost - 1)
    with scope.catalog_read_scope(c.principal, c.attempt):
        assert select(c) == expected and select(c) == expected
        assert len(c.calls) == 2, 'counting only the compressed key would incorrectly fit and reuse'
    monkeypatch.setattr(scope, 'MAX_RETAINED_BYTES', directory_cost + selection_cost)
    with scope.catalog_read_scope(c.principal, c.attempt):
        assert select(c) == expected and select(c) == expected
        assert len(c.calls) == 3, 'the exact combined boundary fits without raising the budget'


@pytest.mark.parametrize('entries', [0, 1])
def test_entry_capacity_is_shared_with_catalog_and_only_falls_back_to_full_selection(monkeypatch, selected, entries):
    c = selected; monkeypatch.setattr(scope, 'MAX_ENTRIES', entries)
    with scope.catalog_read_scope(c.principal, c.attempt):
        first, second = select(c), select(c)
        assert first == second and len(c.calls) == 2
        assert first[5]['input_claims'], 'capacity fallback retains full selected claims'


def test_oversized_valid_payload_is_not_truncated_rejected_or_retained(monkeypatch, selected):
    c = selected; text = '完整公开扩展背景，仍待核实。' * 2048
    c.source['snapshot']['user_instruction'] = text
    c.source['snapshot']['user_instruction_hash'] = digest(text)
    c.source['snapshot']['snapshot_hash'] = digest({key: value for key, value in c.source['snapshot'].items() if key != 'snapshot_hash'})
    c.plan = plan_for(c.source)
    monkeypatch.setattr(scope, 'MAX_RETAINED_BYTES', 16 * 1024)
    with scope.catalog_read_scope(c.principal, c.attempt):
        first, second = select(c), select(c)
        assert first == second and first[0]['user_instruction'] == text
        assert len(c.calls) == 2


def test_next_request_exception_and_retained_context_do_not_extend_selection_lifetime(selected):
    c = selected
    with pytest.raises(RuntimeError, match='synthetic source failure'):
        with scope.catalog_read_scope(c.principal, c.attempt):
            select(c); retained = copy_context()
            raise RuntimeError('synthetic source failure')
    assert retained.run(select, c)[0] is c.source['snapshot']
    with scope.catalog_read_scope(c.principal, c.attempt):
        select(c); select(c)
    assert len(c.calls) == 3


def test_nested_scope_is_independent_then_restores_only_the_live_parent_selection(selected):
    c = selected
    with scope.catalog_read_scope(c.principal, c.attempt):
        first = select(c)
        with scope.catalog_read_scope(c.principal, identity(993)):
            assert select(c) == first and select(c) == first
        assert select(c) == first and len(c.calls) == 2


@pytest.mark.parametrize('mode', ['thread', 'child_task'])
def test_selection_cannot_be_reused_by_a_copied_thread_or_child_task_context(selected, mode):
    c = selected
    if mode == 'thread':
        with scope.catalog_read_scope(c.principal, c.attempt):
            first = select(c); inherited = copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(inherited.run, select, c).result(timeout=10)
            assert result[0] is c.source['snapshot']
            assert select(c) == first
    else:
        async def scenario():
            with scope.catalog_read_scope(c.principal, c.attempt):
                first = select(c)
                async def child():
                    return select(c)
                result = await asyncio.create_task(child())
                assert result[0] is c.source['snapshot']
                assert select(c) == first
        asyncio.run(scenario())
    assert len(c.calls) == 2


@pytest.mark.parametrize('fault', ['invalid', 'truncated', 'trailing', 'extra_stream',
    'length_short', 'length_long', 'length_bool', 'overlong_output'])
def test_damaged_compressed_key_is_a_complete_selection_miss_not_a_business_rejection(selected, fault):
    c = selected; key = encoded([c.source, c.plan, 'custom:alpha'])
    with scope.catalog_read_scope(c.principal, c.attempt):
        expected = select(c); state = scope._CURRENT.get()
        index = next(i for i, row in enumerate(state.entries) if row.kind == 'selected_inputs')
        entry = state.entries[index]
        assert entry.original_length == len(key) and zlib.decompress(entry.content) == key
        if fault == 'invalid': changed = replace(entry, content=b'not-a-zlib-stream')
        elif fault == 'truncated': changed = replace(entry, content=entry.content[:-2])
        elif fault == 'trailing': changed = replace(entry, content=entry.content + b'unexpected-tail')
        elif fault == 'extra_stream': changed = replace(entry, content=entry.content + zlib.compress(b'extra-stream'))
        elif fault == 'length_short': changed = replace(entry, original_length=entry.original_length - 1)
        elif fault == 'length_long': changed = replace(entry, original_length=entry.original_length + 1)
        elif fault == 'length_bool': changed = replace(entry, original_length=True)
        else: changed = replace(entry, content=zlib.compress(b'X' * (len(key) + 2)))
        state.entries[index] = changed
        assert select(c) == expected and len(c.calls) == 2
        assert select(c) == expected and len(c.calls) == 2, 'a later intact entry can recover reuse without trusting the broken one'


def test_wrong_complete_decompressed_bytes_cannot_reuse_even_with_the_same_digest(monkeypatch, selected):
    c = selected
    monkeypatch.setattr(scope, 'hashlib', NS(sha256=lambda value: NS(digest=lambda: b'x' * 32)))
    key = encoded([c.source, c.plan, 'custom:alpha'])
    with scope.catalog_read_scope(c.principal, c.attempt):
        expected = select(c); state = scope._CURRENT.get()
        index = next(i for i, row in enumerate(state.entries) if row.kind == 'selected_inputs')
        entry = state.entries[index]; wrong = b'!' + key[1:]
        assert len(wrong) == len(key)
        state.entries[index] = replace(entry, content=zlib.compress(wrong))
        assert select(c) == expected and len(c.calls) == 2


def test_inflate_is_explicitly_bounded_by_actual_recorded_length_plus_one(monkeypatch, selected):
    c = selected; key = encoded([c.source, c.plan, 'custom:alpha']); requested = []
    real_decoder = zlib.decompressobj
    class BoundedDecoder:
        def __init__(self): self.decoder = real_decoder()
        def decompress(self, raw, max_length=0):
            requested.append(max_length)
            assert max_length == len(key) + 1
            return self.decoder.decompress(raw, max_length)
        def __getattr__(self, name): return getattr(self.decoder, name)
    monkeypatch.setattr(scope, 'zlib', NS(compress=zlib.compress, decompressobj=BoundedDecoder, error=zlib.error))
    with scope.catalog_read_scope(c.principal, c.attempt):
        expected = select(c); state = scope._CURRENT.get()
        index = next(i for i, row in enumerate(state.entries) if row.kind == 'selected_inputs')
        state.entries[index] = replace(state.entries[index], content=zlib.compress(b'X' * (len(key) + 4096)))
        assert select(c) == expected and len(c.calls) == 2
        assert requested == [len(key) + 1], 'compressed overrun is bounded, rejected, and revalidated normally'


def test_two_large_complete_repeated_keys_fit_one_shared_budget_without_trimming_any_input(monkeypatch, selected):
    c = selected; text = '完整公开重复背景，不允许剪去未发送的全局声明。' * 4096
    c.source['snapshot']['user_instruction'] = text
    c.source['snapshot']['user_instruction_hash'] = digest(text)
    c.source['snapshot']['snapshot_hash'] = digest({key: value for key, value in c.source['snapshot'].items() if key != 'snapshot_hash'})
    c.plan = plan_for(c.source); before = encoded([c.source, c.plan])
    expected = {name: c.original(c.source, c.plan, name) for name in ('custom:alpha', 'custom:beta')}
    keys = {name: encoded([c.source, c.plan, name]) for name in expected}
    directory_cost = 2 * len(encoded(c.source['catalog'])) + 32
    budget = directory_cost + sum(len(zlib.compress(keys[name])) + len(encoded(list(expected[name]))) + 32 for name in expected)
    raw_budget = directory_cost + sum(len(keys[name]) + len(encoded(list(expected[name]))) + 32 for name in expected)
    assert budget < raw_budget and budget < 16 * 1024 * 1024
    monkeypatch.setattr(scope, 'MAX_RETAINED_BYTES', budget)
    with scope.catalog_read_scope(c.principal, c.attempt):
        for _ in range(3):
            for name in expected:
                actual = select(c, name)
                assert actual == expected[name] and actual[0]['user_instruction'] == text
                positions = [0, 2] if name == 'custom:alpha' else [1, 3]
                assert actual[5]['scope_binding']['local_to_global'] == positions
                assert actual[5]['input_claims'] == [c.source['snapshot']['input_items'][c.source['snapshot']['input_order'][i]] for i in positions]
        assert len(c.calls) == 2
        assert scope._CURRENT.get().retained_bytes == budget
    assert encoded([c.source, c.plan]) == before
