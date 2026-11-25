"""Disposable-only sequential preflight for fenced document processing.

No route or production worker is registered. A fresh read-only snapshot must
see an already committed file intent, then a separate owned transaction locks
and verifies those exact originals before writing. Never returns the witness to
its caller. Caller-controlled Python code remains inside the trust boundary.
"""
import copy
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session
from . import leases
from . import same_goal_advice_document_lease_candidate_v2 as worker
from . import same_goal_advice_file_intent_candidate_v2 as intent
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from .db import engine
from .models import Job


@old.checked
def process(p, control_id, lease_token):
    """Read committed intent, release connection, then run exact fenced Job.

    This helper owns both Sessions and their transactions. It must not be
    called with a caller's Session or inserted into an ambient transaction.
    Existing downstream rechecks still enforce policy, actor and source.
    """
    with Session(engine(), autoflush=False) as probe:
        probe.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        guard.require_disposable_database(probe)
        old.need(intent.v2._lock_current_reader(probe, p, lock=False), '需要有效登录会话')
        control = worker._control(probe, p, control_id, lock=False)
        target_id = control.data['target']['job_id']
        record = probe.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == intent._key(target_id)))
        old.need(record is not None and type(record.data) is dict
            and type(record.data.get('target')) is dict, '未找到已提交的资料登记')
        target = copy.deepcopy(record.data['target'])
        old.need(target.get('workspace_id') == p.workspace_id and target.get('actor_id') == p.user_id
            and target.get('prepare_job_id') == target_id
            and target.get('task_id') == control.data['target']['task_id']
            and target.get('plan_id') == control.data['target']['plan_id'], '原登记与任务不一致')
        snapshot = intent._snapshot(probe, p, target_id, target, lock=False)
    # probe is closed (rollback of read-only transaction and pool check-in)
    # before a new Session is created, so pool_size=1 is sufficient.
    with Session(engine(), autoflush=False) as db:
        leases.attach(db, control_id, lease_token)
        db.info['worker_actor'] = p
        with db.begin():
            witness = {'session': db, 'transaction': db.get_transaction(), 'principal': p,
                'job_id': target_id, 'target': target, 'snapshot': snapshot, 'used': False, 'poisoned': False}
            def before_flush(session, _context, _instances):
                if not witness['used'] and (session.new or session.dirty or session.deleted):
                    witness['poisoned'] = True
            event.listen(db, 'before_flush', before_flush)
            context = intent._verified_preflight.set(witness)
            try:
                isolation = db.scalar(text("SELECT current_setting('transaction_isolation')"))
                old.need(isolation == 'read committed', '处理需要 READ COMMITTED 隔离级别')
                old.need(not db.new and not db.dirty and not db.deleted)
                result = worker.process(db, p, control_id, lease_token)
                # When recovering an already-saved receipt no new file is
                # written, so not consuming the witness is expected.
                old.need(result['replayed'] or witness['used'], '资料登记核验未执行')
                return result
            finally:
                intent._verified_preflight.reset(context)
