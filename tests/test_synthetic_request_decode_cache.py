"""The experimental cache must preserve decoder behavior and scope boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import random
import threading

import pytest

from generic_agents import typed_payload_codec_v131 as codec
from tests.synthetic_request_decode_cache import SyntheticBudget, install


def _record():
    owner = {'table': 'tasks', 'field': 'data', 'owner_id': 'synthetic-object', 'workspace_id': 'synthetic-workspace'}
    marker = codec.MARKER_PREFIX + '00000000-0000-4000-8000-000000000011'
    value = {'items': [{'x': -0.0, 'y': True}, 1, 'synthetic-text']}
    return owner, marker, value, codec.encode_payload(value, owner, marker), codec.canonical_json(value)


def _spy_uncached(monkeypatch):
    original = codec._decode_payload_uncached
    calls = []
    def checked(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(codec, '_decode_payload_uncached', checked)
    return calls


def test_exact_authenticated_hit_returns_new_tree_and_scope_clears(monkeypatch):
    owner, marker, value, envelope, projection = _record()
    calls = _spy_uncached(monkeypatch)
    budget = SyntheticBudget()
    decode = install(monkeypatch, budget)
    with budget.bind('synthetic-actor', owner['workspace_id']) as scope:
        first = decode(envelope, marker, owner, projection)
        first['items'].append('mutated')
        second = decode(envelope, marker, owner, projection)
        assert second == value and len(calls) == 1
        assert second is not first and scope.hits == 1 and scope.misses == 1
        assert 0 < scope.used == budget.used <= budget.limit
    assert scope.closed and not scope.entries and budget.used == scope.used == 0
    with budget.bind('other-actor', owner['workspace_id']) as next_scope:
        assert decode(envelope, marker, owner, projection) == value
        assert next_scope.hits == 0 and len(calls) == 2


def test_tampering_owner_marker_projection_and_legacy_do_not_hit(monkeypatch):
    owner, marker, value, envelope, projection = _record()
    budget = SyntheticBudget()
    decode = install(monkeypatch, budget)
    with budget.bind('synthetic-actor', owner['workspace_id']) as scope:
        assert decode(envelope, marker, owner, projection) == value
        altered = json.loads(envelope); altered['payload_hash'] = '0' * 64
        cases = [
            (codec.canonical_json(altered), marker, owner, projection),
            (envelope, marker, {**owner, 'owner_id': 'other-object'}, projection),
            (envelope, marker, {**owner, 'workspace_id': 'other-workspace'}, projection),
            (envelope, marker, owner, codec.canonical_json({'items': []})),
            (envelope, codec.MARKER_PREFIX + '00000000-0000-4000-8000-000000000012', owner, projection),
        ]
        for arguments in cases:
            with pytest.raises(codec.TypedPayloadError):
                decode(*arguments)
        assert decode(None, codec.LEGACY_MARKER, owner, projection) == value
        assert scope.hits == 0


def test_budget_exhaustion_and_oversized_record_fall_back(monkeypatch):
    owner, marker, value, envelope, projection = _record()
    calls = _spy_uncached(monkeypatch)
    budget = SyntheticBudget(limit=1, per_scope=1)
    decode = install(monkeypatch, budget)
    with budget.bind('synthetic-actor', owner['workspace_id']) as scope:
        assert decode(envelope, marker, owner, projection) == value
        assert decode(envelope, marker, owner, projection) == value
        assert len(calls) == 2 and scope.hits == 0 and budget.used == 0
    budget.limit = budget.per_scope = 4 * 1024 * 1024
    with budget.bind('actor-a', owner['workspace_id']) as first:
        assert decode(envelope, marker, owner, projection) == value
        budget.limit = budget.used
        with budget.bind('actor-b', owner['workspace_id']) as second:
            assert decode(envelope, marker, owner, projection) == value
            assert second.hits == 0 and second.used == 0 and budget.used == first.used
        assert budget.used == first.used
    assert budget.used == 0


def test_background_thread_task_and_closed_context_cannot_reuse(monkeypatch):
    owner, marker, value, envelope, projection = _record()
    calls = _spy_uncached(monkeypatch)
    budget = SyntheticBudget()
    decode = install(monkeypatch, budget)

    async def exercise():
        with budget.bind('synthetic-actor', owner['workspace_id']) as scope:
            assert decode(envelope, marker, owner, projection) == value
            inherited = copy_context()
            async def child():
                return decode(envelope, marker, owner, projection)
            assert await asyncio.create_task(child()) == value
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(inherited.run, decode, envelope, marker, owner, projection).result() == value
            assert scope.hits == 0 and len(calls) == 3
            after = copy_context()
        assert after.run(decode, envelope, marker, owner, projection) == value
        assert len(calls) == 4 and budget.used == 0

    asyncio.run(exercise())


def test_cached_results_match_original_types_and_invalid_inputs(monkeypatch):
    owner, marker, _value, envelope, projection = _record()
    original = codec.decode_payload
    budget = SyntheticBudget()
    decode = install(monkeypatch, budget)
    rng = random.Random(94721)
    scalars = [None, False, True, 0, 1, -1, -0.0, 1.0, 1e-100, 1e100,
               'synthetic-中文', '', 2**63 - 1, -(2**63)]
    values = list(scalars)
    for _ in range(500):
        values.append({'a': rng.choice(scalars), 'b': [rng.choice(scalars), {'c': rng.choice(scalars)}]})
    with budget.bind('synthetic-actor', owner['workspace_id']):
        for value in values:
            text = codec.encode_payload(value, owner, marker)
            projected = codec.canonical_json(value)
            expected = original(text, marker, owner, projected)
            assert codec.canonical_json(decode(text, marker, owner, projected)) == codec.canonical_json(expected)
            assert codec.canonical_json(decode(text, marker, owner, projected)) == codec.canonical_json(expected)
        invalid = [
            (None, marker, owner, projection),
            (envelope, 'invalid-marker', owner, projection),
            (envelope, marker, {**owner, 'workspace_id': None}, projection),
            (envelope, marker, owner, '{"duplicate":1,"duplicate":2}'),
            (envelope, marker, owner, 'NaN'),
            (envelope, marker, owner, '\ud800'),
            ('{"broken":', marker, owner, projection),
        ]
        for args in invalid:
            with pytest.raises(Exception) as before:
                original(*args)
            with pytest.raises(type(before.value)):
                decode(*args)


def test_global_budget_remains_bounded_across_concurrent_scopes(monkeypatch):
    owner, marker, value, envelope, projection = _record()
    budget = SyntheticBudget(limit=3500, per_scope=3500)
    decode = install(monkeypatch, budget)
    ready = threading.Barrier(8)
    def task(index):
        with budget.bind(f'synthetic-actor-{index}', owner['workspace_id']) as scope:
            assert decode(envelope, marker, owner, projection) == value
            ready.wait(timeout=10)
            with budget.lock:
                assert budget.used <= budget.limit
            return scope.used
    with ThreadPoolExecutor(max_workers=8) as pool:
        retained = list(pool.map(task, range(8)))
    assert any(retained) and budget.used == 0


def test_sixteen_mib_scopes_compete_under_one_thirty_two_mib_pool(monkeypatch):
    owner, marker, _value, _envelope, _projection = _record()
    budget = SyntheticBudget(limit=32 * 1024 * 1024, per_scope=16 * 1024 * 1024)
    decode = install(monkeypatch, budget)
    records = []
    for index in range(24):
        current_owner = {**owner, 'owner_id': f'synthetic-{index}'}
        value = {'payload': 'x' * 200_000, 'index': index}
        records.append((current_owner, value, codec.encode_payload(value, current_owner, marker), codec.canonical_json(value)))
    barrier = threading.Barrier(4)
    def task(index):
        with budget.bind(f'synthetic-actor-{index}', owner['workspace_id']) as scope:
            for current_owner, value, envelope, projection in records:
                assert decode(envelope, marker, current_owner, projection) == value
            barrier.wait(timeout=60)
            with budget.lock:
                assert budget.used <= budget.limit and scope.used <= budget.per_scope
                retained = scope.used
                bypassed = scope.bypassed
            return retained, bypassed
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(task, range(4)))
    assert sum(item[0] for item in results) <= budget.limit
    assert sum(item[1] for item in results) > 0
    assert budget.used == 0


def test_existing_process_lru_remains_unchanged_and_scope_cache_is_separate(monkeypatch):
    owner, marker, value, envelope, projection = _record()
    codec._verified_decode_json.cache_clear()
    calls = _spy_uncached(monkeypatch)
    budget = SyntheticBudget()
    decode = install(monkeypatch, budget, disable_process_cache=False)
    try:
        with budget.bind('actor-a', owner['workspace_id']) as first:
            assert decode(envelope, marker, owner, projection) == value
            assert decode(envelope, marker, owner, projection) == value
            assert first.hits == 1 and len(calls) == 1
        with budget.bind('actor-b', owner['workspace_id']) as second:
            assert decode(envelope, marker, owner, projection) == value
            # The new scope did not hit; the pre-existing process LRU can hit.
            assert second.hits == 0 and len(calls) == 1
            assert codec._verified_decode_json.cache_info().hits >= 1
            with pytest.raises(codec.TypedPayloadError):
                decode(envelope, marker, {**owner, 'owner_id': 'other'}, projection)
        assert budget.used == 0
    finally:
        codec._verified_decode_json.cache_clear()


def test_thread_reuse_and_nested_scopes_do_not_reuse_old_cache(monkeypatch):
    owner, marker, value, envelope, projection = _record()
    calls = _spy_uncached(monkeypatch)
    budget = SyntheticBudget()
    decode = install(monkeypatch, budget)
    def request(actor):
        with budget.bind(actor, owner['workspace_id']) as outer:
            assert decode(envelope, marker, owner, projection) == value
            with budget.bind(actor + '-nested', owner['workspace_id']) as inner:
                assert decode(envelope, marker, owner, projection) == value
                assert inner.hits == 0
            assert decode(envelope, marker, owner, projection) == value
            return outer.hits, threading.get_ident()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(request, 'actor-a').result()
        second = pool.submit(request, 'actor-b').result()
    assert first[0] == second[0] == 1 and first[1] == second[1]
    assert len(calls) == 4 and budget.used == 0


def test_exception_and_cancel_close_scope_and_clear_retained_entries(monkeypatch):
    owner, marker, value, envelope, projection = _record()
    budget = SyntheticBudget()
    decode = install(monkeypatch, budget)
    with pytest.raises(RuntimeError):
        with budget.bind('actor-a', owner['workspace_id']) as scope:
            assert decode(envelope, marker, owner, projection) == value
            assert scope.used > 0
            raise RuntimeError('synthetic failure')
    assert scope.closed and not scope.entries and budget.used == 0
    async def exercise():
        ready = asyncio.Event()
        scope_ref = []
        async def request():
            with budget.bind('actor-b', owner['workspace_id']) as active:
                scope_ref.append(active)
                assert decode(envelope, marker, owner, projection) == value
                ready.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(request())
        await ready.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert scope_ref[0].closed and not scope_ref[0].entries and budget.used == 0
    asyncio.run(exercise())
