"""Reexport an authenticated synthetic Start with the hardened DB fence."""
import os
from pathlib import Path
import shutil

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Plan
from tests.synthetic_advice_checkpoint import save_if_requested
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE') or not os.environ.get('GA_SYNTHETIC_ADVICE_CHECKPOINT'),
    reason='requires isolated input and output synthetic checkpoints')
def test_reexport_authenticated_synthetic_start_without_model_call(monkeypatch, tmp_path, reject_non_loopback_network):
    case = restore_case(monkeypatch)
    source_key = Path(settings().device_gateway_key_file)
    target_dir = tmp_path / 'gateway-keys'; target_dir.mkdir()
    key = target_dir / 'master.key'; shutil.copyfile(source_key, key)
    with Session(engine()) as db:
        plan = db.scalar(select(Plan).where(Plan.workspace_id == case.workspace_id,
            Plan.task_id == case.task_id, Plan.data['schema'].as_string() == contracts.PLAN))
        assert plan is not None
        plan_id = plan.id
    save_if_requested(case.task_id, plan_id, key)
    assert (Path(os.environ['GA_SYNTHETIC_ADVICE_CHECKPOINT']) / 'manifest.json').is_file()
    print('V1_REAL_SYNTHETIC_START_REEXPORTED', flush=True)
