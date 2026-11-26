"""Count synthetic test work without recording inputs, identifiers or SQL.

Only install this probe inside an isolated synthetic test process. Durations
are inclusive; nested functions must not be added together. No ORM state,
authorization decision, provider request or source text is cached here.
"""
from collections import defaultdict
from functools import wraps
from threading import Lock
from time import perf_counter_ns


class SyntheticAdviceProbe:
    def __init__(self):
        self._lock = Lock()
        self._stats = defaultdict(lambda: {'calls': 0, 'inclusive_ns': 0})

    def _record(self, label, elapsed, input_chars=0):
        with self._lock:
            entry = self._stats[label]
            entry['calls'] += 1
            entry['inclusive_ns'] += elapsed
            if input_chars:
                entry['input_chars'] = entry.get('input_chars', 0) + input_chars
                entry['max_input_chars'] = max(entry.get('max_input_chars', 0), input_chars)

    def wrap(self, monkeypatch, module, name, label, *, lengths=False):
        original = getattr(module, name)

        @wraps(original)
        def wrapped(*args, **kwargs):
            # Count only character totals, not contents, row identity or hashes.
            sizes = sum(len(value) for value in (args[0], args[3]) if isinstance(value, str)) if lengths and len(args) >= 4 else 0
            started = perf_counter_ns()
            try:
                return original(*args, **kwargs)
            finally:
                self._record(label, perf_counter_ns() - started, sizes)

        monkeypatch.setattr(module, name, wrapped)

    def snapshot(self):
        with self._lock:
            result = {name: dict(value) for name, value in self._stats.items()}
            self._stats.clear()
            return result


def install(monkeypatch):
    # This helper is deliberately not imported by production code.
    from generic_agents import typed_payload_codec_v131 as codec
    from generic_agents import typed_payload_orm_v131 as orm
    from generic_agents import same_goal_advice_store_v302 as old
    from generic_agents import same_goal_advice_unlimited_store_v2 as store
    from generic_agents import same_goal_advice_common_inventory_v2 as inventory
    from generic_agents import same_goal_advice_read_session_v2 as read_session

    probe = SyntheticAdviceProbe()
    probe.wrap(monkeypatch, orm, 'decode_payload', 'orm_decode', lengths=True)
    probe.wrap(monkeypatch, codec, '_decode_payload_uncached', 'full_verification', lengths=True)
    probe.wrap(monkeypatch, old, '_rows', 'read_rows')
    probe.wrap(monkeypatch, store, '_read_queued', 'read_preparation')
    probe.wrap(monkeypatch, inventory, '_read', 'read_inventory')
    probe.wrap(monkeypatch, read_session, '_check_loaded_state', 'check_loaded_state')
    return probe
