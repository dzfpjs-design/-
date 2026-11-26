"""Profile one GET against a fresh disposable synthetic snapshot; no sender."""
import json
import os
from pathlib import Path
import re
from time import perf_counter_ns
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String, func, select
from sqlalchemy.orm import Session, declarative_base

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, Plan
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
    probe = install(monkeypatch)
    started = perf_counter_ns()
    reply = client.get(f'/api/workspace/tasks/{task_id}/business-advice-v2/context')
    elapsed = perf_counter_ns() - started
    metrics = probe.snapshot()
    path = Path(os.environ['GA_TEST_TIMING_LOG'])
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump({'stage': 'context_get_only', 'status_code': reply.status_code,
                   'elapsed_ns': elapsed, 'inclusive_synthetic_metrics': metrics}, out)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(Plan)) == plans_before
    if reply.status_code != 200:
        detail = reply.json().get('detail')
        # Return only fixed categories; validation values and messages may be private.
        category = ('request_validation' if isinstance(detail, list)
                    else 'service_validation' if isinstance(detail, str) else 'other')
        origins = sorted({item['loc'][0] for item in detail
                          if isinstance(item, dict) and isinstance(item.get('loc'), list)
                          and item['loc'] and item['loc'][0] in {'query', 'path', 'header', 'body', 'cookie'}}) if isinstance(detail, list) else []
        raise AssertionError((reply.status_code, category, origins))


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
