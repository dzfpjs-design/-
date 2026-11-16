"""Actual PostgreSQL elapsed-time leases across repeated local clock hours."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, DateTime, func, literal, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents import leases, worker


@pytest.mark.parametrize('db_zone', ['America/Los_Angeles', 'UTC', 'Asia/Shanghai'])
def test_original_claim_and_renew_keep_elapsed_seconds_in_dst_fold(account, monkeypatch, db_zone):
    # 01:15 PST is the *second* 01:15, after 01:59 PDT rolled back. Use a fixed
    # instant so this regression remains meaningful on every test execution day.
    anchor = datetime(2026, 11, 1, 9, 15, tzinfo=timezone.utc)
    configured = engine()
    assert configured.url.database.startswith('ga_test_')
    isolated = create_engine(configured.url, pool_size=1, max_overflow=0,
        connect_args={'options': '-c timezone=' + db_zone})

    class DatabaseClock:
        def clock_timestamp(self):
            return literal(anchor, type_=DateTime(timezone=True))

        def __getattr__(self, name):
            return getattr(func, name)

    monkeypatch.setattr(worker, 'engine', lambda: isolated)
    monkeypatch.setattr(worker, 'func', DatabaseClock())
    monkeypatch.setattr(leases, 'func', DatabaseClock())
    try:
        with Session(isolated) as db:
            row = Job(workspace_id=account[2], kind='dst-lease-regression-v268',
                idempotency_key='dst-lease:' + str(uuid4()), status='queued',
                available_at=anchor - timedelta(seconds=1), data={})
            db.add(row); db.commit(); identity = row.id
        claimed = worker.claim(identity, workspace_id=account[2])
        assert claimed is not None
        with Session(isolated) as db:
            until = db.get(Job, identity).lease_until
            assert until.astimezone(timezone.utc) == anchor + timedelta(seconds=leases.LEASE_SECONDS)
        assert leases.renew(isolated, *claimed)
        with Session(isolated) as db:
            assert db.get(Job, identity).lease_until.astimezone(timezone.utc) == anchor + timedelta(seconds=leases.LEASE_SECONDS)
    finally:
        isolated.dispose()


@pytest.mark.parametrize('db_zone', ['America/Los_Angeles', 'UTC', 'Asia/Shanghai'])
@pytest.mark.parametrize('clock_hour,clock_minute,until_hour,until_minute,valid', [
    (8, 59, 9, 2, True),   # Valid lease spans 01:59 PDT -> repeated 01:02 PST.
    (9, 15, 8, 59, False), # Expired 01:59 PDT must fail at repeated 01:15 PST.
])
def test_lease_fence_compares_instants_across_dst_fold(account, monkeypatch, db_zone,
        clock_hour, clock_minute, until_hour, until_minute, valid):
    clock = datetime(2026, 11, 1, clock_hour, clock_minute, tzinfo=timezone.utc)
    until = datetime(2026, 11, 1, until_hour, until_minute, tzinfo=timezone.utc)
    configured = engine()
    assert configured.url.database.startswith('ga_test_')
    isolated = create_engine(configured.url, pool_size=1, max_overflow=0,
        connect_args={'options': '-c timezone=' + db_zone})

    class DatabaseClock:
        def clock_timestamp(self):
            return literal(clock, type_=DateTime(timezone=True))

    monkeypatch.setattr(leases, 'func', DatabaseClock())
    token = str(uuid4())
    try:
        with Session(isolated) as db:
            row = Job(workspace_id=account[2], kind='dst-fence-regression-v268',
                idempotency_key='dst-fence:' + str(uuid4()), status='running',
                lease_token=token, lease_until=until, data={})
            db.add(row); db.commit(); identity = row.id
        with isolated.begin() as conn:
            if valid:
                leases._check(conn, identity, token)
            else:
                with pytest.raises(leases.LeaseLost):
                    leases._check(conn, identity, token)
    finally:
        isolated.dispose()
