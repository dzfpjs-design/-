"""Pure real-binding reconstruction, detached bounded reuse and owner isolation.

No DB, actor, lease, clock, receiver, historical source or external model proof
is supplied by these tests. Root runs separate actual source/API verification.
"""
import asyncio
import copy
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi import HTTPException

from generic_agents import authored_goal_binding_parse_scope_v300 as scopes
from generic_agents import authored_goal_data_workflow_store_v288 as store
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_authored_goal_data_workflow_v288 import cold_binding, command, reseal


@pytest.fixture
def parsed(monkeypatch):
    value = cold_binding(command()); actual = store._uncached_check_binding; calls = []
    def counted(item):
        calls.append(True)
        return actual(item)
    monkeypatch.setattr(store, '_uncached_check_binding', counted)
    def live(*args, **kwargs): pytest.fail('Pure reconstruction accessed live DB/permission gates')
    for name in ('published', 'scoped', 'require'): monkeypatch.setattr(store, name, live)
    return value, calls


def simple():
    calls = []
    def parse(value):
        calls.append(canonical_json(value))
        return copy.deepcopy(value)
    def read(value, **kwargs): return scopes.read_binding_cached(value, parse=parse, **kwargs)
    return calls, parse, read


def test_real_check_binding_revalidates_within_the_outer_operation_and_detaches_every_return(parsed):
    value, calls = parsed; original = copy.deepcopy(value)
    first = store.check_binding(value); second = store.check_binding(value)
    assert first == second == original and len(calls) == 2
    with scopes.authored_goal_binding_parse_scope():
        first = store.check_binding(value)
        first['graph']['edges'].clear(); first['source_materials'].append({'caller': 'local'})
        assert store.check_binding(copy.deepcopy(value)) == original
        value['source_materials'].append({'new_input': True})
        with pytest.raises(HTTPException): store.check_binding(value)
        assert store.check_binding(original) == original
    assert len(calls) == 6 and original['source_materials'] == []
    assert store.check_binding(original) == original and len(calls) == 7


@pytest.mark.parametrize('damage', ['rehashed_graph', 'rehashed_pin'])
def test_full_canonical_input_and_original_reconstruction_reject_changed_self_hashes(parsed, damage):
    value, calls = parsed
    with scopes.authored_goal_binding_parse_scope():
        assert store.check_binding(value) == value
        bad = copy.deepcopy(value)
        if damage == 'rehashed_graph':
            next(edge for edge in bad['graph']['edges'] if edge['from'] == 'context_reader')['to'] = 'reasoning'
        else: bad['capability_pins']['local_prepare']['arguments'] = {'override': True}
        bad = reseal(bad)
        for _ in range(2):
            with pytest.raises(HTTPException): store.check_binding(bad)
        assert store.check_binding(value) == value
    assert len(calls) == 4


@pytest.mark.parametrize('implementation', ['_build', 'build_graph', 'service_delegations'])
def test_replaced_direct_builder_or_validator_cannot_hit_an_old_structural_success(parsed, monkeypatch, implementation):
    value, calls = parsed
    with scopes.authored_goal_binding_parse_scope():
        store.check_binding(value)
        def replacement(*args, **kwargs): raise HTTPException(409, 'new actual structural implementation rejects')
        target = store if implementation == '_build' else store.template
        monkeypatch.setattr(target, implementation, replacement)
        for _ in range(2):
            with pytest.raises(HTTPException): store.check_binding(value)
    assert len(calls) == 3


def test_parser_identity_and_new_operation_force_actual_reconstruction(parsed, monkeypatch):
    value, calls = parsed; old = store._uncached_check_binding; changed = []
    def new(item): changed.append(True); return old(item)
    with scopes.authored_goal_binding_parse_scope():
        store.check_binding(value); store.check_binding(value)
        monkeypatch.setattr(store, '_uncached_check_binding', new)
        store.check_binding(value); store.check_binding(value)
    with scopes.authored_goal_binding_parse_scope(): store.check_binding(value)
    assert len(calls) == 5 and len(changed) == 3


def test_only_two_detached_results_are_retained_without_eviction():
    calls, _parse, read = simple(); a, b, c = ({'key': label} for label in ('a', 'b', 'c'))
    with scopes.authored_goal_binding_parse_scope():
        for value in (a, b, c, c, a, b): assert read(value) == value
    assert Counter(calls) == Counter({canonical_json(a): 1, canonical_json(b): 1, canonical_json(c): 2})
    assert scopes.MAX_ENTRIES == 2 and scopes.MAX_CANONICAL_BYTES == 8 * 1024 * 1024


@pytest.mark.parametrize('offset,second_count', [(0, 1), (-1, 2)])
def test_total_input_and_output_utf8_budget_is_inclusive_and_never_truncates(monkeypatch, offset, second_count):
    calls, _parse, read = simple(); a, b = {'key': '甲'}, {'key': '乙'}
    budget = 2 * (len(canonical_json(a).encode()) + len(canonical_json(b).encode()))
    monkeypatch.setattr(scopes, 'MAX_CANONICAL_BYTES', budget + offset)
    with scopes.authored_goal_binding_parse_scope():
        for value in (a, b, b, a): assert read(value) == value
    assert Counter(calls) == Counter({canonical_json(a): 1, canonical_json(b): second_count})


@pytest.mark.parametrize('part', ['input', 'output'])
def test_oversize_uses_ordinary_parser_without_storing_partial_results(monkeypatch, part):
    calls, _parse, read = simple(); value = {'text': '完整值🌲'}; size = len(canonical_json(value).encode())
    monkeypatch.setattr(scopes, 'MAX_CANONICAL_BYTES', (size if part == 'input' else 2 * size) - 1)
    with scopes.authored_goal_binding_parse_scope():
        assert read(value) == read(value) == value
    assert len(calls) == 2


def test_failed_parse_does_not_occupy_a_slot_or_become_a_success():
    calls = []
    def parse(value):
        calls.append(value['key'])
        if value['key'] == 'bad': raise ValueError('Original parser refusal')
        return copy.deepcopy(value)
    with scopes.authored_goal_binding_parse_scope():
        for _ in range(2):
            with pytest.raises(ValueError): scopes.read_binding_cached({'key': 'bad'}, parse=parse)
        for key in ('a', 'b', 'a', 'b'): assert scopes.read_binding_cached({'key': key}, parse=parse) == {'key': key}
    assert Counter(calls) == Counter({'bad': 2, 'a': 1, 'b': 1})


@pytest.mark.parametrize('value', [None, {'x': Decimal('1.0')}, {'x': (1, 2)}, {1: 'key'},
    {'x': float('nan')}, {'x': 'nul\x00'}, {'x': '\ud800'}])
def test_unsupported_input_is_passed_unchanged_to_the_original_parser(value):
    calls = []
    def reject(original):
        assert original is value; calls.append(True)
        raise TypeError('original unsupported input')
    with scopes.authored_goal_binding_parse_scope():
        for _ in range(2):
            with pytest.raises(TypeError): scopes.read_binding_cached(value, parse=reject)
    assert len(calls) == 2


def test_input_and_result_never_alias_a_retained_result():
    calls, _parse, read = simple(); value = {'nested': {'items': [1]}}
    with scopes.authored_goal_binding_parse_scope():
        first = read(value); first['nested']['items'].append(2)
        assert read(value) == {'nested': {'items': [1]}}
        value['nested']['items'].append(3)
        assert read(value) == {'nested': {'items': [1, 3]}}
    assert len(calls) == 2


def test_nested_scopes_and_decorator_share_only_the_same_outer_operation():
    calls, _parse, read = simple(); value = {'key': 'one'}
    @scopes.authored_goal_binding_parse_scope()
    def inner(): return read(value)
    @scopes.authored_goal_binding_parse_scope()
    def outer():
        assert inner() == inner()
        with pytest.raises(RuntimeError):
            with scopes.authored_goal_binding_parse_scope(): raise RuntimeError('inner')
        assert read(value) == value
    outer(); outer()
    assert len(calls) == 2


def test_closed_outer_lifetime_expires_copied_context_even_after_an_error():
    calls, _parse, read = simple(); value = {'key': 'one'}
    with pytest.raises(RuntimeError):
        with scopes.authored_goal_binding_parse_scope():
            read(value); copied = copy_context(); raise RuntimeError('outer')
    copied.run(read, value)
    def operation():
        with scopes.authored_goal_binding_parse_scope(): assert read(value) == read(value)
    copied.run(operation); copied.run(operation)
    assert len(calls) == 4


def test_copied_context_inventory_is_detached_and_its_quota_cannot_fork_past_two_entries():
    calls, _parse, read = simple(); a, b, c = ({'key': label} for label in ('a', 'b', 'c'))
    with scopes.authored_goal_binding_parse_scope():
        read(a); copied = copy_context(); copied.run(read, b)
        read(b); copied.run(read, b)
        read(c); read(c); copied.run(read, c); copied.run(read, c)
        assert read(a) == a
        assert scopes._active().lifetime.retained_entries == 2
        assert scopes._active().lifetime.canonical_bytes <= scopes.MAX_CANONICAL_BYTES
    assert Counter(calls) == Counter({canonical_json(a): 1, canonical_json(b): 2, canonical_json(c): 4})


def test_concurrent_threads_cannot_inherit_a_previous_owners_cache():
    calls, _parse, read = simple(); value = {'key': 'one'}; barrier = Barrier(2)
    def worker():
        with scopes.authored_goal_binding_parse_scope():
            barrier.wait(timeout=10)
            return read(value), read(value)
    with scopes.authored_goal_binding_parse_scope():
        expected = read(value); contexts = [copy_context(), copy_context()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(context.run, worker) for context in contexts]
            assert [future.result(timeout=20) for future in futures] == [(expected, expected), (expected, expected)]
        assert read(value) == expected
    assert len(calls) == 3


def test_concurrent_async_tasks_have_new_lifetimes_even_with_an_inherited_context():
    calls, _parse, read = simple(); value = {'key': 'one'}
    async def scenario():
        ready = asyncio.Event()
        async def child():
            with scopes.authored_goal_binding_parse_scope():
                first = read(value); await ready.wait()
                assert read(value) == first
                return first
        with scopes.authored_goal_binding_parse_scope():
            expected = read(value)
            tasks = [asyncio.create_task(child()), asyncio.create_task(child())]
            await asyncio.sleep(0); ready.set()
            assert await asyncio.gather(*tasks) == [expected, expected]
            assert read(value) == expected
    asyncio.run(scenario())
    assert len(calls) == 3
