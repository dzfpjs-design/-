"""Minimal isolated check of the exact synthetic checkpoint save guard.

The resulting checkpoint is marked preflight-only and must not be used as a
mature business checkpoint or evidence of the real workflow.
"""
import json
import os
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Capability, Task, User
from tests.synthetic_fresh_mature_checkpoint_candidate import save


def test_fresh_checkpoint_exact_guard(account, tmp_path):
    assert not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('DASHSCOPE_API_KEY')
    assert not settings().cloud_allowed_providers
    root = Path(os.environ['GA_SYNTHETIC_FRESH_GOAL_CHECKPOINT'])
    key = tmp_path / 'gateway-keys' / 'master.key'
    key.parent.mkdir(mode=0o700)
    key.write_text('synthetic-preflight-key')
    os.chmod(key, 0o600)
    (settings().storage_dir / 'preflight.txt').write_text('synthetic preflight evidence')
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        cap = Capability(workspace_id=account[2], namespace='synthetic', name='preflight',
            release_version='1', kind='tool', status='draft', content_hash='0' * 64, data={})
        db.add(cap); db.flush()
        task = Task(workspace_id=account[2], owner_id=user.id, package_id=cap.id,
            title='synthetic preflight', status='draft', data={})
        db.add(task); db.commit(); task_id = task.id
    save(task_id, key)
    marker = root / 'PREFLIGHT_ONLY'
    marker.write_text('not a mature business checkpoint')
    os.chmod(marker, 0o600)
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['task_id'] == task_id
    assert (root / 'synthetic-mature.pgcustom').stat().st_size > 0
    assert (root / 'evidence' / 'preflight.txt').read_text() == 'synthetic preflight evidence'
    if os.environ.get('GA_TEST_SYNTHETIC_PREFLIGHT_FAIL') == '1':
        pytest.fail('deliberate synthetic failure after checkpoint save')
