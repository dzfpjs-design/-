"""Isolated concurrent-index build vs a pre-existing duplicate writer."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents.db import engine
from generic_agents.models import Audit
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_nonce_price_candidate_v2_pg import real_price_context

INDEX = 'ga_test_upload_index_writer_race'


@pytest.mark.parametrize('commit_duplicate', [False, True])
def test_concurrent_unique_build_waits_for_uncommitted_duplicate_and_fails_closed(real_price_context, commit_duplicate):
    s = real_price_context; assert engine().url.database.startswith('ga_test_')
    with Session(engine()) as db:
        basis = prices.authorize(db, s['principal'], s['body'])['basis_ref']
        price = db.get(Audit, basis['id']); upload = db.get(Audit, price.data['upload_audit_ref']['id'])
        fields = {'workspace_id': upload.workspace_id, 'actor_id': upload.actor_id,
            'object_id': upload.object_id, 'action': upload.action, 'data': copy.deepcopy(upload.data)}
        original_id = upload.id; db.commit()
    entered = Event(); builder_pids = []; duplicate_id = str(uuid4())
    with Session(engine()) as writer:
        writer_pid = writer.scalar(text('SELECT pg_backend_pid()'))
        writer.add(Audit(id=duplicate_id, **fields)); writer.flush()
        def build():
            with engine().connect().execution_options(isolation_level='AUTOCOMMIT') as db:
                builder_pids.append(db.scalar(text('SELECT pg_backend_pid()'))); entered.set()
                try:
                    db.execute(text(f"CREATE UNIQUE INDEX CONCURRENTLY {INDEX} ON audit_events "
                        "(workspace_id, object_id) WHERE action = 'evidence.upload'"))
                    return 'unexpectedly-built'
                except IntegrityError:
                    return 'duplicate-rejected'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(build)
            try:
                assert entered.wait(5)
                deadline = monotonic() + 10
                while True:
                    with Session(engine()) as observer:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': builder_pids[0]})
                    if writer_pid in blockers: break
                    assert not future.done(), 'index build finished before the competing writer was released'
                    assert monotonic() < deadline, 'index build did not wait for the uncommitted writer'
                    sleep(.02)
                if commit_duplicate: writer.commit()
                else: writer.rollback()
                assert future.result(timeout=15) == ('duplicate-rejected' if commit_duplicate else 'unexpectedly-built')
            finally:
                writer.rollback()
    with engine().connect().execution_options(isolation_level='AUTOCOMMIT') as db:
        valid = db.scalar(text('SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid '
            'WHERE c.relname=:name'), {'name': INDEX})
        assert valid is (not commit_duplicate)
        with Session(engine()) as check:
            assert check.get(Audit, original_id) is not None
            assert (check.get(Audit, duplicate_id) is not None) is commit_duplicate
        db.execute(text(f'DROP INDEX CONCURRENTLY {INDEX}'))
    if commit_duplicate:
        # Delete only the deliberate test-created duplicate after proving it
        # survived failure, so parametrized cases are order-independent.
        with Session(engine()) as cleanup:
            cleanup.delete(cleanup.get(Audit, duplicate_id)); cleanup.commit()
