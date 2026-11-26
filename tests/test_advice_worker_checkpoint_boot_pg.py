"""Test subprocess environment against an empty disposable database."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import CloudCall
from tests.test_advice_user_real_http_checkpoint_pg import run_worker_process
from tests.test_advice_v2_recovery_console_candidate_pg import spool_root


def test_worker_child_starts_with_verified_disposable_db(spool_root):
    result=run_worker_process()
    assert result.returncode==0, result.stderr[-1200:]
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
