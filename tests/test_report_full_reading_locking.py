"""Real three-connection lock-order regression; no timing-based sleep oracle.

Root alone executes in its empty fenced PG. The normal dispatcher, source
validators, row locks and synthetic reserve/settle provider all remain real.
The negative control temporarily restores the former candidate-first entry to
prove the NOWAIT assertion is sensitive; it is not product acceptance evidence.
"""
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from psycopg import Error as DriverError
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import report_full_reading_pipeline as pipeline
from generic_agents.db import engine
from generic_agents.models import SetupDraft, SetupReportDraft
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene, SyntheticProvider, start, get


def sqlstate(error):
    original = error.orig
    return getattr(original, 'sqlstate', None) or getattr(original, 'pgcode', None)


def assert_dispatch_lock_order(scene, monkeypatch, *, negative_control):
    fence(); provider = SyntheticProvider(scene, monkeypatch)
    preview, _, accepted = start(scene)
    identity, job_id = accepted['draft']['id'], accepted['draft']['job_id']
    reached_workspace, worker_finished, stop = threading.Event(), threading.Event(), threading.Event()
    worker_identity = {'id': None, 'workspace_pid': None}; drivers = []; driver_lock = threading.Lock()
    actual_workspace = pipeline.legacy.workspace_draft; actual_run = pipeline.run

    def observed_workspace(*args, **kwargs):
        # Observation only: preserve original SQL/function, no replacement lock,
        # waiting gate, return value or branch inside the product function.
        if threading.get_ident() == worker_identity['id']:
            worker_identity['workspace_pid'] = args[0].connection().connection.driver_connection.info.backend_pid
            reached_workspace.set()
        return actual_workspace(*args, **kwargs)

    def finite_worker_transactions(session, transaction, connection):
        if threading.get_ident() != worker_identity['id']:
            return
        # Test-owned query deadlines only. Real FOR UPDATE, lease checks and
        # commits still execute. Capture owned DBAPI connections for cancellation
        # solely if a failed test cannot finish after all its locks are released.
        driver = connection.connection.driver_connection
        with driver_lock:
            if all(existing is not driver for existing in drivers): drivers.append(driver)
        connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '20s'")

    def old_candidate_first(db, p, draft_id):
        # Explicit negative control: precisely reinstate the earlier run-entry
        # inversion, then call today's real dispatcher. No fake lock outcome.
        pipeline.owned(db, p, draft_id, True)
        return actual_run(db, p, draft_id)

    def execute_worker():
        worker_identity['id'] = threading.get_ident()
        try:
            return process_once(job_id, deliver=False, workspace_id=scene['p'].workspace_id, _stop_event=stop)
        finally:
            worker_finished.set()

    monkeypatch.setattr(pipeline.legacy, 'workspace_draft', observed_workspace)
    if negative_control: monkeypatch.setattr(pipeline, 'run', old_candidate_first)
    event.listen(Session, 'after_begin', finite_worker_transactions)
    workspace, probe = Session(engine()), Session(engine())
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='owned-full-reading-lock')
    future = None; cleanup_errors = []
    try:
        workspace.execute(text("SET LOCAL lock_timeout = '5s'"))
        workspace.execute(text("SET LOCAL statement_timeout = '10s'"))
        workspace_pid = workspace.scalar(text('SELECT pg_backend_pid()'))
        held = workspace.scalar(select(SetupDraft).where(SetupDraft.workspace_id == scene['p'].workspace_id)
            .with_for_update().execution_options(populate_existing=True))
        assert held is not None
        # The workspace row is locked before the actual dispatcher is launched.
        future = executor.submit(execute_worker)
        assert reached_workspace.wait(10), 'worker never reached the real workspace lock function'
        assert not worker_finished.is_set(), 'worker cannot complete while the workspace transaction holds its row'
        probe.execute(text("SET LOCAL lock_timeout = '2s'"))
        probe.execute(text("SET LOCAL statement_timeout = '5s'"))
        probe_pid = probe.scalar(text('SELECT pg_backend_pid()'))
        assert len({workspace_pid, probe_pid, worker_identity['workspace_pid']}) == 3
        try:
            candidate = probe.scalar(select(SetupReportDraft).where(SetupReportDraft.id == identity,
                SetupReportDraft.workspace_id == scene['p'].workspace_id,
                SetupReportDraft.actor_id == scene['p'].user_id).with_for_update(nowait=True))
        except DBAPIError as error:
            if not negative_control: raise
            assert sqlstate(error) == '55P03', 'negative control must fail on the real candidate lock, not another error'
        else:
            assert not negative_control, 'candidate-first negative control did not hold the candidate row'
            assert candidate is not None and candidate.id == identity
        finally:
            # Release the probe before the workspace gate, so the worker cannot
            # be delayed by the successful NOWAIT read in the positive case.
            probe.rollback()
        workspace.rollback()
        assert future.result(timeout=35) is True
        assert worker_finished.is_set()
    finally:
        probe.rollback(); probe.close(); workspace.rollback(); workspace.close()
        # Every failure path releases test locks before waiting/cancelling. No
        # background interpreter, service, scheduler or model was launched.
        if future is not None and not future.done():
            stop.set()
            try:
                future.result(timeout=25)
            except FutureTimeout:
                with driver_lock: owned_drivers = list(drivers)
                for driver in owned_drivers:
                    if driver.closed: continue
                    try: driver.cancel()
                    except DriverError as error: cleanup_errors.append(type(error).__name__)
                if not worker_finished.wait(10): cleanup_errors.append('worker_thread_not_stopped')
                else:
                    try: future.result(timeout=2)
                    except FutureTimeout: cleanup_errors.append('worker_future_not_finished')
                    except Exception as error: cleanup_errors.append('worker_failure_' + type(error).__name__)
            except Exception as error:
                cleanup_errors.append('worker_failure_' + type(error).__name__)
        stopped = future is None or future.done()
        executor.shutdown(wait=stopped, cancel_futures=True)
        event.remove(Session, 'after_begin', finite_worker_transactions)
        assert stopped and not cleanup_errors, 'owned lock-test worker/connection cleanup was incomplete'
    view = get(scene, identity)
    assert view['status'] == view['phase'] == 'awaiting_merge', view.get('error')
    assert all(stage['status'] == 'succeeded' and stage['raw_response_saved'] for stage in view['extractions'])
    assert len(provider.calls) == len(preview['batches'])


def test_real_dispatcher_waits_for_workspace_without_holding_candidate(reading_scene, monkeypatch):
    assert_dispatch_lock_order(reading_scene, monkeypatch, negative_control=False)


def test_negative_control_old_candidate_first_entry_reproduces_nowait_55p03(reading_scene, monkeypatch):
    """Sensitivity control only; the temporarily regressed path is NOT a pass."""
    assert_dispatch_lock_order(reading_scene, monkeypatch, negative_control=True)
