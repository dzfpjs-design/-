"""Read-only diagnostics on the explicitly synthetic restored checkpoint."""
import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_store_v302 as store
from generic_agents.db import engine
from generic_agents.models import Audit
from generic_agents.temporal import instant
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'), reason='dedicated checkpoint only')
def test_readonly_policy_expiry_diagnostic(monkeypatch, reject_non_loopback_network):
    case = restore_case(monkeypatch)
    with Session(engine()) as db:
        now = db.scalar(select(func.clock_timestamp()))
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == case.workspace_id,
            Audit.object_id == case.task_id, Audit.action == store.QUOTA_POLICY)))
        active = sum(instant(row.data['issued_at']) <= now < instant(row.data['expires_at']) for row in rows)
        expired = sum(instant(row.data['expires_at']) <= now for row in rows)
        print('ISOLATED_SYNTHETIC_V1_POLICY_COUNTS', {'policies': len(rows), 'active': active, 'expired': expired}, flush=True)
        assert rows and active + expired == len(rows)
