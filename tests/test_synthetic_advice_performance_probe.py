"""The isolated profiler counts work without retaining source text or authority."""
import json
import cProfile
import os
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from generic_agents import typed_payload_codec_v131 as codec
from generic_agents import typed_payload_orm_v131 as orm
from tests.synthetic_advice_performance_probe import install
from tests.synthetic_context_function_profile import summarize


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS isolated RSS sampler')
def test_rss_sampler_records_only_self_and_stops_cleanly():
    from tests.synthetic_process_rss import SyntheticRssSampler
    sampler = SyntheticRssSampler(interval_seconds=0.005)
    sampler.start()
    sample_allocation = bytearray(2 * 1024 * 1024)
    time.sleep(0.03)
    result = sampler.finish()
    assert sample_allocation and result['start_bytes'] > 0 and result['end_bytes'] > 0
    assert result['sampled_peak_bytes'] >= result['start_bytes']
    assert all(type(value) is int and value >= 0 for value in result.values())


def test_function_profile_exports_only_whitelisted_names_and_numbers():
    scope = {}
    exec(compile('def scoped():\n    return sum(range(3))',
                 'synthetic-prefix/src/generic_agents/services.py', 'exec'), scope)
    unknown = {}
    exec(compile('def private_synthetic_code():\n    return 1',
                 'private_synthetic_filename.py', 'exec'), unknown)
    profile = cProfile.Profile()
    profile.runcall(lambda: (scope['scoped'](), unknown['private_synthetic_code']()))
    result = summarize(profile)
    assert result['functions']['services.scoped']['calls'] == 1
    assert all(type(value) is int and value >= 0
               for entry in result['functions'].values() for value in entry.values())
    encoded = json.dumps(result)
    assert 'private_synthetic_code' not in encoded and 'private_synthetic_filename' not in encoded


def test_counts_only_numeric_metadata_preserves_fresh_results_and_rejection(monkeypatch):
    codec._verified_decode_json.cache_clear()
    try:
        probe = install(monkeypatch)
        owner = {'table': 'tasks', 'field': 'data', 'owner_id': 'private-id', 'workspace_id': 'private-workspace'}
        marker = codec.MARKER_PREFIX + '00000000-0000-4000-8000-000000000001'
        value = {'details': ['private-document-text']}
        envelope = codec.encode_payload(value, owner, marker)
        raw = lambda actual_owner: json.dumps({'marker': marker, 'canonical': envelope,
            'projection': json.dumps(value), 'owner': actual_owner})
        processor = orm._DecodedPayloadText()
        first = processor.process_result_value(raw(owner), None)
        first['details'].append('changed')
        assert processor.process_result_value(raw(owner), None) == value
        with pytest.raises(HTTPException):
            processor.process_result_value(raw({**owner, 'workspace_id': 'other-workspace'}), None)
        metrics = probe.snapshot()
        assert metrics['result_processor']['calls'] == 3
        assert metrics['result_processor']['input_chars'] >= metrics['result_processor']['max_input_chars'] > 0
        assert metrics['orm_decode']['calls'] == 3
        assert metrics['full_verification']['calls'] == 2
        assert metrics['orm_decode']['input_chars'] >= metrics['orm_decode']['max_input_chars'] > 0
        serialized = json.dumps(metrics)
        for secret in ('private-id', 'private-workspace', 'private-document-text', marker):
            assert secret not in serialized
        assert probe.snapshot() == {}
    finally:
        codec._verified_decode_json.cache_clear()


def test_parallel_synthetic_read_counters_are_not_lost(monkeypatch):
    probe = install(monkeypatch)
    owner = {'table': 'tasks', 'field': 'data', 'owner_id': 's', 'workspace_id': 'w'}
    marker = codec.MARKER_PREFIX + '00000000-0000-4000-8000-000000000002'
    value = {'records': [1, 2]}
    raw = json.dumps({'marker': marker, 'canonical': codec.encode_payload(value, owner, marker),
        'projection': json.dumps(value), 'owner': owner})
    processor = orm._DecodedPayloadText()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: processor.process_result_value(raw, None), range(32)))
    assert results == [value] * 32
    metrics = probe.snapshot()
    assert metrics['orm_decode']['calls'] == metrics['result_processor']['calls'] == 32
    codec._verified_decode_json.cache_clear()


def test_sql_timings_record_only_counts_even_on_error(monkeypatch):
    probe = install(monkeypatch)
    engine = create_engine('sqlite:///:memory:')
    try:
        with Session(engine) as session:
            result = session.execute(text('select :value'), {'value': 'private-query-parameter'})
            assert result.scalar_one() == 'private-query-parameter'
            with pytest.raises(SQLAlchemyError):
                session.execute(text('select * from synthetic_missing_private_table'))
        with engine.connect() as conn:
            assert conn.exec_driver_sql('select 1').scalar_one() == 1
        metrics = probe.snapshot()
        assert metrics['session_execute']['calls'] == 2
        assert metrics['connection_execute']['calls'] == 2
        assert metrics['connection_other']['calls'] == 2
        assert metrics['driver_execute']['calls'] == 1
        assert all(type(value) is int and value >= 0
                   for entry in metrics.values() for value in entry.values())
        encoded = json.dumps(metrics)
        for secret in ('private-query-parameter', 'synthetic_missing_private_table', 'select'):
            assert secret not in encoded
    finally:
        engine.dispose()


def test_sql_unknown_module_is_aggregated_without_its_name(monkeypatch):
    probe = install(monkeypatch)
    namespace = {'__name__': 'generic_agents.private_synthetic_module', 'text': text}
    exec('def query(conn):\n    return conn.execute(text("select 1"))', namespace)
    engine = create_engine('sqlite:///:memory:')
    try:
        with engine.connect() as conn:
            assert namespace['query'](conn).scalar_one() == 1
        metrics = probe.snapshot()
        assert metrics['connection_other_app']['calls'] == 1
        assert 'private_synthetic_module' not in json.dumps(metrics)
    finally:
        engine.dispose()


def test_scoped_repeat_counts_are_ephemeral_and_aliases_are_instrumented(monkeypatch):
    from generic_agents import services
    alias = ModuleType('generic_agents.synthetic_alias_test')
    alias.scoped = services.scoped
    monkeypatch.setitem(sys.modules, alias.__name__, alias)
    probe = install(monkeypatch)
    assert alias.scoped is services.scoped
    original_salt = probe._scoped_salt
    actor = SimpleNamespace(workspace_id='private-workspace', user_id='private-user')
    class SyntheticModel: pass
    session_a, session_b = object(), object()
    probe.record_scoped(session_a, SyntheticModel, 'private-record', actor, False)
    probe.record_scoped(session_a, SyntheticModel, 'private-record', actor, False)
    probe.record_scoped(session_b, SyntheticModel, 'private-record', actor, False)
    probe.record_scoped(session_b, SyntheticModel, 'private-record', actor, True)
    result = probe.snapshot()
    repeats = result['scoped_repeats']
    assert repeats['calls'] == 4
    assert repeats['repeated_keys'] == 2
    assert repeats['repeated_in_session'] == 1
    assert repeats['locked_calls'] == 1
    assert not probe._scoped_seen and not probe._scoped_sessions
    assert probe._scoped_salt != original_salt
    assert all(value not in json.dumps(result) for value in ('private-workspace', 'private-user', 'private-record'))


def test_counts_loaded_state_checks_without_retaining_identifiers(monkeypatch):
    from generic_agents import same_goal_advice_read_session_v2 as isolation
    monkeypatch.setattr(isolation, '_check_loaded_state', lambda *_: None)
    probe = install(monkeypatch)
    assert isolation._check_loaded_state(None, None, 'sensitive-synthetic-id') is None
    metrics = probe.snapshot()
    assert metrics['check_loaded_state']['calls'] == 1
    assert 'sensitive-synthetic-id' not in json.dumps(metrics)


def test_profiled_worker_help_writes_private_numeric_metrics_without_database(tmp_path):
    metrics = tmp_path / 'worker.jsonl'
    root = Path(__file__).resolve().parents[1]
    env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(root / 'src') + ':' + str(root),
        'GA_TEST_WORKER_METRICS_LOG': str(metrics), 'DEEPSEEK_API_KEY': '', 'DASHSCOPE_API_KEY': ''}
    result = subprocess.run([sys.executable, '-m', 'tests.synthetic_advice_profiled_worker', '--help'],
        env=env, cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert metrics.stat().st_mode & 0o077 == 0
    sample = json.loads(metrics.read_text().strip())
    assert type(sample['elapsed_ns']) is int
    assert type(sample['inclusive_synthetic_metrics']) is dict
