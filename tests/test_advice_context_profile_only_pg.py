"""Profile one GET against a fresh disposable synthetic snapshot; no sender."""
import json
import os
import cProfile
from functools import wraps
from pathlib import Path
import re
import resource
import sys
from time import perf_counter_ns
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String, func, select, text
from sqlalchemy.orm import Session, declarative_base

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, Membership, Plan, Task, User, Workspace
from generic_agents.security import Principal
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db
from tests.synthetic_advice_performance_probe import install

_SyntheticBase = declarative_base()


class _SyntheticScopeRow(_SyntheticBase):
    __tablename__ = 'synthetic_scope_batch_semantics'
    id = Column(String, primary_key=True)
    workspace_id = Column(String, nullable=False)
    value = Column(Integer, nullable=False)


@pytest.mark.skipif(os.environ.get('GA_TEST_CONTEXT_ONLY_PROFILE') != '1', reason='explicit context-only isolated runner required')
def test_read_only_context_profile_on_fresh_synthetic_checkpoint(monkeypatch):
    # This test performs no advice POST or worker invocation. Its runner allows
    # network egress only to its own disposable PostgreSQL port.
    assert not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('DASHSCOPE_API_KEY')
    monkeypatch.setattr(settings(), 'cloud_allowed_providers', '')
    monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', True)
    monkeypatch.setattr(settings(), 'v2_user_send_test_enabled', False)
    monkeypatch.setattr(settings(), 'v2_user_send_worker_enabled', False)
    task_id = restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_CHECKPOINT'])
    login = json.loads((settings().storage_dir / '__synthetic_checkpoint_login__.json').read_text())
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        plans_before = db.scalar(select(func.count()).select_from(Plan))
        capabilities = list(db.scalars(select(Capability).where(
            Capability.workspace_id == login['workspace_id'],
            Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
        origins = {cap.data['specification']['device_gateway_binding']['origin'] for cap in capabilities}
        assert len(origins) == 1
        origin = next(iter(origins))
        assert re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', origin)
        assert int(origin.rsplit(':', 1)[1]) not in {55486, 8766, engine().url.port}
        monkeypatch.setattr(settings(), 'device_gateway_allowed_origins', origin)
    client = TestClient(app)
    auth = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
    assert auth.status_code == 200
    client.headers.update({'X-CSRF-Token': auth.json()['csrf_token'], 'X-Workspace-ID': login['workspace_id']})
    contexts = (context for included in app.routes if hasattr(included, 'effective_route_contexts')
                for context in included.effective_route_contexts())
    route = next(item for item in contexts if item.path ==
                 '/api/workspace/tasks/{task_id}/business-advice-v2/context')
    original_call = route.dependant.call
    if os.environ.get('GA_TEST_CONTEXT_FUNCTION_PROFILE') == '1':
        from tests.synthetic_context_function_profile import summarize
        profiler = cProfile.Profile()
        @wraps(original_call)
        def profiled_context(*args, **kwargs):
            return profiler.runcall(original_call, *args, **kwargs)
        monkeypatch.setattr(route.dependant, 'call', profiled_context)
        started = perf_counter_ns()
        response = client.get(f'/api/workspace/tasks/{task_id}/business-advice-v2/context')
        elapsed = perf_counter_ns() - started
        assert response.status_code == 200
        path = Path(os.environ['GA_TEST_TIMING_LOG'])
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump({'stage': 'context_handler_functions_only', 'status_code': 200,
                       'elapsed_ns': elapsed, 'profile': summarize(profiler)}, out)
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
            assert db.scalar(select(func.count()).select_from(Plan)) == plans_before
        return
    probe = install(monkeypatch)
    from generic_agents import typed_payload_codec_v131 as codec, typed_payload_orm_v131 as orm
    from tests.synthetic_request_decode_cache import SyntheticBudget, install as install_cache
    baseline_codec, baseline_orm, baseline_verified = codec.decode_payload, orm.decode_payload, codec._verified_decode_json
    budget = SyntheticBudget()
    candidate_decode = install_cache(monkeypatch, budget)
    cache_stats = {}
    @wraps(original_call)
    def scoped_context(*args, **kwargs):
        principal = kwargs['p']
        with budget.bind(principal.user_id, principal.workspace_id) as scope:
            result = original_call(*args, **kwargs)
            cache_stats.update(hits=scope.hits, misses=scope.misses, bypassed=scope.bypassed,
                               peak_accounted_bytes=scope.used, entries=len(scope.entries))
            return result
    stable = ('enabled', 'title', 'goal_hash', 'head_hash', 'sources', 'model', 'max_tokens', 'can_prepare', 'can_start')
    baseline_response = None
    runs = []
    for mode in ('baseline', 'candidate', 'baseline', 'candidate'):
        candidate = mode == 'candidate'
        monkeypatch.setattr(route.dependant, 'call', scoped_context if candidate else original_call)
        monkeypatch.setattr(codec, 'decode_payload', candidate_decode if candidate else baseline_codec)
        monkeypatch.setattr(orm, 'decode_payload', candidate_decode if candidate else baseline_orm)
        monkeypatch.setattr(codec, '_verified_decode_json', baseline_verified.__wrapped__ if candidate else baseline_verified)
        cache_stats.clear()
        # On macOS this is a process-lifetime high-water mark in bytes, not a
        # per-request allocation measurement; retain both absolute readings.
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = perf_counter_ns()
        response = client.get(f'/api/workspace/tasks/{task_id}/business-advice-v2/context')
        elapsed = perf_counter_ns() - started
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        metrics = probe.snapshot()
        assert response.status_code == 200 and budget.used == 0
        current = {key: response.json()[key] for key in stable}
        if baseline_response is None: baseline_response = current
        else: assert current == baseline_response
        if candidate: assert cache_stats['hits'] > 0
        runs.append({'mode': mode, 'elapsed_ns': elapsed, 'rss_highwater_before_bytes': rss_before,
                     'rss_highwater_after_bytes': rss_after, 'cache': dict(cache_stats),
                     'inclusive_synthetic_metrics': metrics})
    path = Path(os.environ['GA_TEST_TIMING_LOG'])
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump({'stage': 'context_get_only_ab', 'status_code': 200, 'runs': runs}, out)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(Plan)) == plans_before


@pytest.mark.skipif(os.environ.get('GA_TEST_CONTEXT_ONLY_PROFILE') != '1', reason='explicit context-only isolated runner required')
def test_sequential_scoped_reads_are_not_equivalent_to_batch_during_concurrent_commit(monkeypatch):
    """Prove snapshot timing changes without modifying any business record."""
    from generic_agents import services
    _SyntheticBase.metadata.create_all(engine())
    actor = SimpleNamespace(workspace_id='synthetic-workspace', user_id='synthetic-actor')
    with Session(engine()) as db:
        db.add_all([_SyntheticScopeRow(id='source', workspace_id=actor.workspace_id, value=1),
                    _SyntheticScopeRow(id='extractor', workspace_id=actor.workspace_id, value=1)])
        db.commit()
    probe = install(monkeypatch)
    with Session(engine()) as reader:
        assert reader.connection().get_isolation_level() == 'READ COMMITTED'
        first = services.scoped(reader, _SyntheticScopeRow, 'source', actor)
        assert first.value == 1
        first_metrics = probe.snapshot()
        with Session(engine()) as writer:
            writer.get(_SyntheticScopeRow, 'extractor').value = 2
            writer.commit()
        probe.snapshot()
        second = services.scoped(reader, _SyntheticScopeRow, 'extractor', actor)
        assert second.value == 2
        second_metrics = probe.snapshot()
    with Session(engine()) as writer:
        writer.get(_SyntheticScopeRow, 'extractor').value = 1
        writer.commit()
    probe.snapshot()
    with Session(engine()) as reader:
        rows = list(reader.scalars(select(_SyntheticScopeRow).where(
            _SyntheticScopeRow.workspace_id == actor.workspace_id,
            _SyntheticScopeRow.id.in_(['source', 'extractor']))))
        by_id = {row.id: row for row in rows}
        batch_metrics = probe.snapshot()
        with Session(engine()) as writer:
            writer.get(_SyntheticScopeRow, 'extractor').value = 2
            writer.commit()
        assert by_id['source'].value == by_id['extractor'].value == 1
    assert first_metrics['connection_execute']['calls'] == 1
    assert second_metrics['connection_execute']['calls'] == 1
    assert batch_metrics['connection_execute']['calls'] == 1


@pytest.mark.skipif(os.environ.get('GA_TEST_CONTEXT_ONLY_PROFILE') != '1', reason='explicit context-only isolated runner required')
def test_decode_cache_does_not_hide_committed_data_change_or_permission_revocation(monkeypatch):
    from generic_agents import same_goal_advice_store_v302 as store
    from tests.synthetic_request_decode_cache import SyntheticBudget, install as install_cache
    with Session(engine()) as db:
        workspace = Workspace(name='synthetic-workspace'); user = User(username='synthetic-user', password_hash='synthetic')
        db.add_all([workspace, user]); db.flush()
        membership = Membership(user_id=user.id, workspace_id=workspace.id, role='admin')
        capability = Capability(workspace_id=workspace.id, namespace='synthetic', name='synthetic',
                                release_version='1', kind='domain_package', content_hash='0'*64, data={})
        db.add_all([membership, capability]); db.flush()
        task = Task(workspace_id=workspace.id, owner_id=user.id, package_id=capability.id,
                    title='synthetic-task', data={'synthetic-value': 1})
        db.add(task); db.commit()
        user_id, workspace_id, task_id, membership_id = user.id, workspace.id, task.id, membership.id
    principal = Principal(user_id, workspace_id, 'admin', 'synthetic-session')
    budget = SyntheticBudget()
    install_cache(monkeypatch, budget)
    with budget.bind(user_id, workspace_id) as scope, Session(engine()) as reader:
        store._reader(reader, principal)
        query = select(Task.data).where(Task.id == task_id, Task.workspace_id == workspace_id)
        assert reader.scalar(query)['synthetic-value'] == 1
        assert reader.scalar(query)['synthetic-value'] == 1 and scope.hits >= 1
        # A different workspace must still be filtered by the database.
        assert reader.scalar(select(Task.data).where(Task.id == task_id, Task.workspace_id == 'other-workspace')) is None
        with Session(engine()) as writer:
            changed = writer.get(Task, task_id); changed.data = {'synthetic-value': 2}; writer.commit()
        assert reader.scalar(query)['synthetic-value'] == 2
        with Session(engine()) as writer:
            writer.get(Membership, membership_id).role = 'viewer'; writer.commit()
        with pytest.raises(HTTPException):
            store._reader(reader, principal)
    assert budget.used == 0


@pytest.mark.skipif(os.environ.get('GA_TEST_CONTEXT_TAMPER_PROFILE') != '1', reason='explicit synthetic tamper runner required')
def test_guard_rechecks_tampered_release_in_same_stack(monkeypatch):
    """Synthetic concurrent corruption between two authority checks is rejected."""
    from generic_agents import layered_approval_store_v175 as approval, workspace_api
    from generic_agents import typed_payload_codec_v131 as codec
    assert not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('DASHSCOPE_API_KEY')
    monkeypatch.setattr(settings(), 'cloud_allowed_providers', '')
    monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', True)
    monkeypatch.setattr(settings(), 'v2_user_send_test_enabled', False)
    monkeypatch.setattr(settings(), 'v2_user_send_worker_enabled', False)
    task_id = restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_CHECKPOINT'])
    login = json.loads((settings().storage_dir / '__synthetic_checkpoint_login__.json').read_text())
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        plans_before = db.scalar(select(func.count()).select_from(Plan))
        capabilities = list(db.scalars(select(Capability).where(
            Capability.workspace_id == login['workspace_id'],
            Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
        origins = {cap.data['specification']['device_gateway_binding']['origin'] for cap in capabilities}
        assert len(origins) == 1
        origin = next(iter(origins))
        assert re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', origin)
        assert int(origin.rsplit(':', 1)[1]) not in {55486, 8766, engine().url.port}
        monkeypatch.setattr(settings(), 'device_gateway_allowed_origins', origin)
    state = {'tampered': False, 'second_check_started': False, 'second_check_rejected': False}
    saved_execution = workspace_api.saved_execution
    validate_sources = approval.validate_document_sources

    @wraps(saved_execution)
    def traced_saved_execution(*args, **kwargs):
        in_guard = sys._getframe(1).f_code.co_name == 'guard'
        if state['tampered'] and in_guard:
            state['second_check_started'] = True
        try:
            return saved_execution(*args, **kwargs)
        except HTTPException as error:
            if state['tampered'] and in_guard and error.status_code == 409:
                state['second_check_rejected'] = True
            raise

    @wraps(validate_sources)
    def validate_then_tamper(db, principal, task, value, **kwargs):
        result = validate_sources(db, principal, task, value, **kwargs)
        if not state['tampered']:
            # This deliberately corrupts only the fresh synthetic release;
            # no source records, identities or payloads are written to logs.
            with engine().begin() as writer:
                writer.execute(text("SET LOCAL lock_timeout = '2s'"))
                encoded = writer.scalar(text('SELECT _ga_data_canonical FROM domain_instance_releases WHERE id = :id'),
                                        {'id': task.data['instance_release_id']})
                envelope = json.loads(encoded)
                envelope['payload_hash'] = '0' * 64 if envelope['payload_hash'] != '0' * 64 else '1' * 64
                writer.execute(text('UPDATE domain_instance_releases SET _ga_data_canonical = :value WHERE id = :id'),
                               {'value': codec.canonical_json(envelope), 'id': task.data['instance_release_id']})
            state['tampered'] = True
        return result

    monkeypatch.setattr(workspace_api, 'saved_execution', traced_saved_execution)
    monkeypatch.setattr(approval, 'validate_document_sources', validate_then_tamper)
    client = TestClient(app, raise_server_exceptions=False)
    auth = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
    assert auth.status_code == 200
    client.headers.update({'X-CSRF-Token': auth.json()['csrf_token'], 'X-Workspace-ID': login['workspace_id']})
    reply = client.get(f'/api/workspace/tasks/{task_id}/business-advice-v2/context')
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(Plan)) == plans_before
    assert state['tampered'] is True
    assert state['second_check_started'] is True
    assert state['second_check_rejected'] is True
    assert reply.status_code == 409
