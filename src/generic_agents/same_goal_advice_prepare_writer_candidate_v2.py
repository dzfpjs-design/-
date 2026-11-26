"""Unrouted transactional v2 preparation writer for isolated testing only.

It cannot send, start an analysis or create model usage. Its sentinel means
the existing ordinary worker cannot claim the new Job, including by job ID.
Do not deploy: old API/nonce writers are not globally isolated yet, and no
production worker, receipt reader or migration has been installed.
"""
from datetime import datetime
import os
from pathlib import Path
import re

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from . import same_goal_advice_common_inventory_v2 as inventory
from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_plan_candidate_v2 as candidate
from . import same_goal_advice_unlimited_store_v2 as v2
from . import workspace_advice_nonce_candidate_v2 as nonces
from .contracts import digest
from .config import settings
from .models import Audit, Job, Plan, uid
from .relations import lock_current_actor

UNAVAILABLE_TO_GENERAL_WORKERS = v2.UNAVAILABLE_TO_GENERAL_WORKERS


def require_disposable_database(db):
    """Hard-stop this incomplete candidate outside a dedicated test cluster."""
    try: expected_url = make_url(os.environ.get('GA_TEST_DATABASE_URL', ''))
    except ArgumentError: old.need(False, '缺少有效测试数据库配置，未创建任务')
    expected_directory = Path(os.environ.get('GA_TEST_PG_DATA_DIR', '')).resolve()
    expected_identifier = os.environ.get('GA_TEST_PG_SYSTEM_IDENTIFIER', '')
    url = db.get_bind().url
    old.need(url.drivername.startswith('postgresql') and url.host == '127.0.0.1'
        and url.port and url.username in {'advice_test', 'counter_test'} and url.password is None
        and type(url.database) is str and re.fullmatch(r'ga_test_[0-9a-f]{12}', url.database) is not None
        and expected_url.host == url.host and expected_url.port == url.port
        and expected_url.username == url.username and expected_url.password is None
        and expected_directory.name == 'data'
        and (expected_directory.parent.name.startswith('advice-race-pg-')
            or expected_directory.parent.name.startswith('v2-counter-isolated-'))
        and expected_identifier.isdecimal(), '此候选只允许专用隔离测试数据库，未创建任务')
    actual_directory, actual_identifier, actual_database, actual_user = db.execute(text(
        "SELECT current_setting('data_directory'), (SELECT system_identifier::text FROM pg_control_system()), "
        "current_database(), current_user")).one()
    old.need(Path(actual_directory).resolve() == expected_directory and actual_identifier == expected_identifier
        and actual_database == url.database and actual_user == url.username,
        '测试数据库身份不匹配，未创建任务')


@old.checked
def prepare(db, p, task_id, body, policy_ref):
    """Create exactly one original Plan/Job/Audit in the caller's transaction.

    Replay returns only the previously validated queued or locally prepared
    original, including its exact original policy and body. This is not a
    worker/preview/send grant.
    """
    require_disposable_database(db)
    request = c.Prepare.model_validate(body).model_dump(mode='json')
    reference = c._parse(c.Ref, policy_ref)
    old._reader(db, p, write=True)
    nonces.acquire(db, p, action=v2.ACCEPTED, task_id=task_id, body=request)
    task = old.existing._task(db, p, task_id, lock=True)
    lock_current_actor(db, p)
    basis = inventory.read(db, p, task)
    originals = old._rows(db, p, Audit, Audit.action == v2.ACCEPTED,
        Audit.data['request_id'].as_string() == request['request_id'])
    old.need(len(originals) <= 1)
    if originals:
        event = originals[0]
        plan = db.scalar(select(Plan).where(Plan.id == event.object_id,
            Plan.workspace_id == p.workspace_id, Plan.task_id == task.id).with_for_update())
        old.need(plan is not None)
        cancellations = old._rows(db, p, Audit, Audit.action == v2.CANCELLED_EXPIRED, Audit.object_id == plan.id)
        prep, job = (v2.read_expired_cancelled(db, p, task, plan) if cancellations else
            v2.read_prepared(db, p, task, plan) if plan.status == 'awaiting_consent'
            else v2.read_queued(db, p, task, plan))
        old.need(prep['request'] == request and prep['requested_by'] == p.user_id
            and prep['budget']['policy_ref'] == reference
            and job.available_at == UNAVAILABLE_TO_GENERAL_WORKERS)
        result = {'plan_id': plan.id, 'job_id': job.id, 'replayed': True}
        if cancellations: result['state'] = 'cancelled_expired'
        return result
    old.need(basis['queued_v2'] == 0)
    old.need(not any(old._preparation_open(row[0]) for row in old._inventory(db, p, task,
        history_compatible=settings().v2_terminal_history_test_enabled)),
        '原业务建议未结清，未创建第二个准备任务')
    proposal = candidate.derive_candidate(db, p, task.id, request, reference)
    prep = proposal['preparation_candidate']; plan_id = prep['scope']['analysis_plan_id']
    key = v2.job_key(task.id, plan_id); job_id = uid(); audit_id = uid()
    clock = db.scalar(select(func.clock_timestamp()))
    old.need(type(clock) is datetime and clock.tzinfo is not None)
    plan = Plan(id=plan_id, workspace_id=p.workspace_id, task_id=task.id, status='queued', version=1,
        created_at=clock, data={'schema': v2.PLAN, 'preparation_snapshot': prep,
            'jobs': {'prepare': {'id': job_id, 'kind': v2.PREPARE_KIND, 'schema': v2.JOB_SCHEMA, 'key': key}},
            'operations': [{'operation': 'prepare', 'request_id': request['request_id'], 'body': request,
                'body_hash': digest(request), 'actor_id': p.user_id, 'audit_id': audit_id}],
            'document_preparation': None, 'reviews': []})
    job = Job(id=job_id, workspace_id=p.workspace_id, kind=v2.PREPARE_KIND, status='queued', version=1,
        attempts=0, created_at=clock, available_at=UNAVAILABLE_TO_GENERAL_WORKERS, idempotency_key=key,
        data=v2.job_data(plan_id, prep, key))
    event = Audit(id=audit_id, workspace_id=p.workspace_id, actor_id=p.user_id, action=v2.ACCEPTED,
        object_id=plan_id, created_at=clock, data=v2.accepted_data(plan_id, prep, job_id, key))
    with db.begin_nested():
        db.add(plan); db.flush(); db.add_all([job, event]); db.flush()
        saved, original = v2.read_queued(db, p, task, plan)
        old.need(saved == prep and original.id == job_id
            and original.available_at == UNAVAILABLE_TO_GENERAL_WORKERS)
    return {'plan_id': plan_id, 'job_id': job_id, 'replayed': False}
