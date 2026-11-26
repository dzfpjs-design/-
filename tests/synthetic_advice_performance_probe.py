"""Count synthetic test work without recording inputs, identifiers or SQL.

Only install this probe inside an isolated synthetic test process. Durations
are inclusive; nested functions must not be added together. SQL execute timings
end when a result object is returned, not when its rows are consumed. No ORM
state, authorization decision, provider request or source text is cached here.
"""
from collections import defaultdict
from functools import wraps
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from threading import Lock
from time import perf_counter_ns

_SQL_CALLERS = {
    'generic_agents.same_goal_advice_read_session_v2': 'connection_read_session',
    'generic_agents.same_goal_advice_store_v302': 'connection_advice_store',
    'generic_agents.same_goal_advice_unlimited_store_v2': 'connection_unlimited_store',
    'generic_agents.same_goal_advice_common_inventory_v2': 'connection_inventory',
    'generic_agents.same_goal_advice_context_v310': 'connection_advice_context',
    'generic_agents.goal_evaluation_provider_v288': 'connection_goal_binding',
    'generic_agents.typed_payload_orm_v131': 'connection_payload_orm',
    'generic_agents.gateway_credentials_v268': 'connection_gateway_auth',
}
_SQL_FUNCTIONS = {
    ('generic_agents.services', 'scoped'): 'connection_services_scoped',
    ('generic_agents.services', 'published'): 'connection_services_published',
    ('generic_agents.services', 'validate'): 'connection_services_validate',
}
# The remaining admissible labels are frozen from checked-in application code,
# never from SQL, arguments or runtime data. Unknown module names collapse.
_APP_CODE_MODULES = frozenset(
    path.stem for path in (Path(__file__).resolve().parents[1] / 'src' / 'generic_agents').glob('*.py')
    if path.is_file() and not path.is_symlink() and path.stem.isidentifier()
)


def _sql_caller_labels():
    """Return whitelisted labels only; never retain frames, arguments or SQL."""
    frame = sys._getframe(1)
    while frame:
        module = frame.f_globals.get('__name__', '')
        if module.startswith('generic_agents.'):
            stem = module.removeprefix('generic_agents.')
            function_label = _SQL_FUNCTIONS.get((module, frame.f_code.co_name))
            if function_label:
                parent = frame.f_back
                while parent:
                    parent_module = parent.f_globals.get('__name__', '')
                    parent_stem = parent_module.removeprefix('generic_agents.')
                    if parent_module.startswith('generic_agents.') and parent_module != module:
                        parent_label = parent_stem if parent_stem in _APP_CODE_MODULES else 'other'
                        return (function_label, function_label + '_via_' + parent_label)
                    parent = parent.f_back
                return (function_label, function_label + '_via_other')
            if module in _SQL_CALLERS:
                return (_SQL_CALLERS[module],)
            return (('connection_module_' + stem) if stem in _APP_CODE_MODULES else 'connection_other_app',)
        frame = frame.f_back
    return ('connection_other',)


class SyntheticAdviceProbe:
    def __init__(self):
        self._lock = Lock()
        self._stats = defaultdict(lambda: {'calls': 0, 'inclusive_ns': 0})
        self._scoped_salt = os.urandom(32)
        self._scoped_seen = set()
        self._scoped_sessions = set()

    def _record(self, label, elapsed, input_chars=0):
        with self._lock:
            entry = self._stats[label]
            entry['calls'] += 1
            entry['inclusive_ns'] += elapsed
            if input_chars:
                entry['input_chars'] = entry.get('input_chars', 0) + input_chars
                entry['max_input_chars'] = max(entry.get('max_input_chars', 0), input_chars)

    def wrap(self, monkeypatch, module, name, label, *, length_indices=(), classify_sql=False):
        original = getattr(module, name)

        @wraps(original)
        def wrapped(*args, **kwargs):
            # Count only character totals, not contents, row identity or hashes.
            sizes = sum(len(args[index]) for index in length_indices
                        if len(args) > index and isinstance(args[index], str))
            caller_labels = _sql_caller_labels() if classify_sql else ()
            started = perf_counter_ns()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = perf_counter_ns() - started
                self._record(label, elapsed, sizes)
                for caller_label in caller_labels:
                    self._record(caller_label, elapsed)

        monkeypatch.setattr(module, name, wrapped)

    def snapshot(self):
        with self._lock:
            result = {name: dict(value) for name, value in self._stats.items()}
            self._stats.clear()
            self._scoped_seen.clear()
            self._scoped_sessions.clear()
            self._scoped_salt = os.urandom(32)
            return result

    def record_scoped(self, db, model, object_id, principal, lock):
        """Count repeats with per-snapshot ephemeral tokens, never exported."""
        try:
            workspace, user = principal.workspace_id, principal.user_id
            if (type(object_id) is not str or type(workspace) is not str or type(user) is not str
                    or type(lock) is not bool or any(len(item) > 256 for item in (object_id, workspace, user))
                    or not isinstance(model, type)):
                raise ValueError('unsupported synthetic scoped input')
            material = json.dumps([model.__module__, model.__qualname__, workspace, user, object_id, lock],
                                  ensure_ascii=True, separators=(',', ':')).encode()
            with self._lock:
                token = hmac.new(self._scoped_salt, material, hashlib.sha256).digest()
                session_token = hmac.new(self._scoped_salt, token + str(id(db)).encode(), hashlib.sha256).digest()
                entry = self._stats['scoped_repeats']
                entry['calls'] += 1
                if lock:
                    entry['locked_calls'] = entry.get('locked_calls', 0) + 1
                if token in self._scoped_seen:
                    entry['repeated_keys'] = entry.get('repeated_keys', 0) + 1
                    if lock:
                        entry['locked_repeated_keys'] = entry.get('locked_repeated_keys', 0) + 1
                if session_token in self._scoped_sessions:
                    entry['repeated_in_session'] = entry.get('repeated_in_session', 0) + 1
                if len(self._scoped_seen) < 10000 and len(self._scoped_sessions) < 10000:
                    self._scoped_seen.add(token); self._scoped_sessions.add(session_token)
                else:
                    entry['not_retained'] = entry.get('not_retained', 0) + 1
        except Exception:
            # A diagnostic must never alter the real authorization/read result.
            with self._lock:
                entry = self._stats['scoped_repeats']
                entry['calls'] += 1
                entry['unclassified'] = entry.get('unclassified', 0) + 1

    def wrap_scoped_aliases(self, monkeypatch):
        from generic_agents import services
        original = services.scoped

        @wraps(original)
        def wrapped(db, model, object_id, principal, lock=False):
            try:
                return original(db, model, object_id, principal, lock)
            finally:
                self.record_scoped(db, model, object_id, principal, lock)

        # Already imported aliases and later "from services import scoped"
        # must both use this test-only wrapper, without any source changes.
        for module in list(sys.modules.values()):
            if not getattr(module, '__name__', '').startswith('generic_agents.'):
                continue
            for name, value in list(vars(module).items()):
                if value is original:
                    monkeypatch.setattr(module, name, wrapped)


def install(monkeypatch):
    # This helper is deliberately not imported by production code.
    from generic_agents import typed_payload_codec_v131 as codec
    from generic_agents import typed_payload_orm_v131 as orm
    from generic_agents import same_goal_advice_store_v302 as old
    from generic_agents import same_goal_advice_unlimited_store_v2 as store
    from generic_agents import same_goal_advice_common_inventory_v2 as inventory
    from generic_agents import same_goal_advice_read_session_v2 as read_session
    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session

    probe = SyntheticAdviceProbe()
    probe.wrap_scoped_aliases(monkeypatch)
    probe.wrap(monkeypatch, orm._DecodedPayloadText, 'process_result_value',
               'result_processor', length_indices=(1,))
    probe.wrap(monkeypatch, orm, 'decode_payload', 'orm_decode', length_indices=(0, 3))
    probe.wrap(monkeypatch, codec, '_decode_payload_uncached', 'full_verification', length_indices=(0, 3))
    probe.wrap(monkeypatch, Session, 'execute', 'session_execute')
    probe.wrap(monkeypatch, Connection, 'execute', 'connection_execute', classify_sql=True)
    probe.wrap(monkeypatch, Connection, 'exec_driver_sql', 'driver_execute')
    probe.wrap(monkeypatch, old, '_rows', 'read_rows')
    probe.wrap(monkeypatch, store, '_read_queued', 'read_preparation')
    probe.wrap(monkeypatch, inventory, '_read', 'read_inventory')
    probe.wrap(monkeypatch, read_session, '_check_loaded_state', 'check_loaded_state')
    return probe
