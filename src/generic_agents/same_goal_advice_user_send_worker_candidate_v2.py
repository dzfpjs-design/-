"""Dedicated, one-attempt worker for disposable synthetic integration only.

No credential or provider route is used. The selected control is never
reclaimed automatically: an interrupted or uncertain attempt stays for review.
"""
import argparse
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .models import Job
from . import same_goal_advice_cloud_reservation_candidate_v2 as reservation
from . import same_goal_advice_deepseek_adapter_candidate_v2 as transport
from . import same_goal_advice_dispatch_control_candidate_v2 as control
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_response_spool_test_v2 as spool
from . import same_goal_advice_unlimited_lease_candidate_v2 as lease
from . import worker as runtime_worker


def _check(db):
    if not (settings().v2_user_send_test_enabled and settings().v2_user_send_worker_enabled):
        raise RuntimeError('后台分析服务尚未启用')
    guard.require_disposable_database(db)
    if not settings().v2_response_spool_enabled or settings().v2_response_spool_root is None:
        raise RuntimeError('后台分析服务暂不可用')
    spool.validate_root(settings().v2_response_spool_root)
    transport._test_endpoint()  # Loopback endpoint only; never a provider key.


def tick():
    """One bounded sweep. A claimed control is never automatically retried."""
    with Session(engine()) as db:
        _check(db)
        ids=list(db.scalars(select(Job.id).where(Job.kind==control.KIND,Job.status=='queued',Job.attempts==0)
            .order_by(Job.id).limit(20)))
    claimed=failed=0
    for control_id in ids:
        try:
            with Session(engine()) as db:
                _check(db);proof=control.claim(db,control_id);db.commit()
            claimed+=1
            with Session(engine()) as db:
                scope=control.verify_worker(db,proof)
                actor=runtime_worker.actor(db,scope['workspace_id'],scope['actor_id'])
                db.commit()
            with Session(engine()) as db:
                leased=lease.acquire(db,actor,scope['task_id'],scope['plan_id'],worker_proof=proof);db.commit()
            with Session(engine()) as db:
                allocated=reservation.reserve(db,actor,scope['task_id'],scope['plan_id'],leased['job_id'],
                    leased['lease_token'],worker_proof=proof);db.commit()
            outcome=transport.execute_synthetic_test_only(actor,scope['task_id'],scope['plan_id'],leased['job_id'],
                leased['lease_token'],allocated['cloud_call_id'],worker_proof=proof)
            if outcome.get('state')!='settled':failed+=1
        except Exception:
            # Leave the original control/permit and all records untouched. No
            # automatic resubmission follows an uncertain or failed attempt.
            failed+=1
    return {'claimed':claimed,'failed':failed}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    while True:
        tick()
        if args.once:return
        time.sleep(5)


if __name__=='__main__':
    main()
