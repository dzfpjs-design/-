"""Database-clock leases fence every worker transaction, including intermediate commits."""
from contextlib import contextmanager
from datetime import timedelta, timezone
import threading
from sqlalchemy import event, select, update, func, text
from sqlalchemy.exc import DBAPIError
from .models import Job, User, Membership

LEASE_SECONDS = 180
HEARTBEAT_SECONDS = 30
_FENCE_WITNESS = object()
_LOCAL_GOAL_PHASE = 'local_goal_handler_lease_phase'
_LOCAL_GOAL_KIND = 'check_native_business_goal'
_GRAPH_HANDLER_PHASE = 'graph_handler_lease_phase'
_GRAPH_HANDLER_KIND = 'execute_full_semantic_graph_node'
_CATALOGUE_ALLOCATION_PHASE = 'catalogue_allocation_handler_lease_phase_v213'
_CATALOGUE_ALLOCATION_KIND = 'read_saved_map_design_v176'
_CATALOGUE_ALLOCATION_PROFILE = 'catalogue-allocation-v213'


class LeaseLost(Exception):
    """A stale executor must roll back and leave recovery to the current owner."""


class ExecutionUnauthorized(Exception):
    pass


def _check_actor(connection, db):
    p = db.info.get('worker_actor')
    if p and not connection.execute(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role == p.role, Membership.role.in_(['admin', 'operator']))).first():
        raise ExecutionUnauthorized('后台执行授权已撤销或变化')


def _renew_advice_checkpoint(connection,db,job_id,token):
    """Renew only this still-live paid sender at its original fenced commit.

    Recovery bookkeeping and independent raw/financial Sessions have no
    worker actor here. No ORM refresh/mutation, token change, new authority or
    expired-lease revival is performed; the original final flush still wins.
    """
    from .security import Principal
    actor=db.info.get('worker_actor')
    if type(actor) is not Principal:return
    if db.info.get('worker_lease')!={'job_id':job_id,'lease_token':token}:
        raise LeaseLost('原提交租约身份已变化，未延长后台执行')
    row=connection.execute(select(Job.kind,Job.data,Job.workspace_id).where(Job.id==job_id,
        Job.kind=='generate_same_goal_advice_v302',
        Job.data['schema'].as_string()=='same-goal-advice-send-job-v308')).first()
    # A different family never loads its potentially large frozen Job payload.
    if row is None:return
    if (row.kind!='generate_same_goal_advice_v302' or type(row.data) is not dict
            or row.data.get('schema')!='same-goal-advice-send-job-v308'
            or 'time_trigger' in row.data or 'time_trigger_hash' in row.data):return
    if (actor.role not in {'admin','operator'} or row.workspace_id!=actor.workspace_id
            or row.data.get('actor_id')!=actor.user_id):
        raise ExecutionUnauthorized('原发送任务与当前后台执行账号不一致，未延长执行')
    # _check already owns this row FOR UPDATE. Recheck clock_timestamp in the
    # UPDATE so expiry during the actual IAM check cannot revive an old owner.
    changed=connection.execute(update(Job).where(Job.id==job_id,Job.workspace_id==actor.workspace_id,
        Job.kind=='generate_same_goal_advice_v302',Job.data['schema'].as_string()=='same-goal-advice-send-job-v308',
        Job.data['actor_id'].as_string()==actor.user_id,Job.lease_token==token,Job.status=='running',
        Job.lease_until>func.clock_timestamp()).values(
            lease_until=func.clock_timestamp()+timedelta(seconds=LEASE_SECONDS)))
    if changed.rowcount!=1:raise LeaseLost('原发送租约已过期或易主，未延长执行')


def _check(connection, job_id, token, *, local_goal_begin=False, graph_handler_begin=False,
        catalogue_allocation_begin=False, allocation_actor_id=None, allocation_workspace_id=None):
    flags = (local_goal_begin, graph_handler_begin, catalogue_allocation_begin)
    if not all(type(flag) is bool for flag in flags) or sum(flags) > 1:
        raise ValueError('租约检查阶段无效')
    if catalogue_allocation_begin:
        if (type(allocation_actor_id) is not str or not allocation_actor_id
                or type(allocation_workspace_id) is not str or not allocation_workspace_id):
            raise ValueError('目录归属阶段必须保留原后台执行身份')
        row = connection.execute(select(Job.lease_token, Job.status, Job.lease_until,
            Job.kind, Job.data, Job.workspace_id).where(Job.id == job_id)).first()
        if (not row or row.kind != _CATALOGUE_ALLOCATION_KIND or type(row.data) is not dict
                or type(row.data.get('generation_profile')) is not str
                or row.data.get('generation_profile') not in {
                    _CATALOGUE_ALLOCATION_PROFILE, 'catalogue-current-allocation-v260'}
                or row.data.get('actor_id') != allocation_actor_id
                or row.workspace_id != allocation_workspace_id
                or ('recovery_only' in row.data and row.data['recovery_only'] is not False)):
            raise LeaseLost('此原任务不属于当前目录归属处理阶段')
        if row.data['generation_profile'] == 'catalogue-current-allocation-v260':
            # Phase activation is restricted to a real fresh Consumer by the
            # worker. Later BEGINs keep that same stored family/Job identity
            # while allowing the handler's legitimate intent/call/answer phases.
            # No row lock here: heartbeat must renew the original Job token.
            if type(row.data.get('consumer_id')) is not str:
                raise LeaseLost('原当前配置归属任务缺少真实目录关联')
            from .report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
            current = connection.execute(select(Consumer.id).where(
                Consumer.id == row.data.get('consumer_id'), Consumer.job_id == job_id,
                Consumer.workspace_id == row.workspace_id,
                Consumer.actor_id == allocation_actor_id,
                Consumer.data['preview']['generation_profile'].as_string() == 'catalogue-current-allocation-v260',
                Consumer.data['preview']['body']['generation_profile'].as_string() == 'catalogue-current-allocation-v260',
                Consumer.data['preview']['preparation']['schema'].as_string()
                    == 'report-business-catalogue-current-allocation-preparation-v260',
                Consumer.data['preview']['preparation']['native_preparation']['schema'].as_string()
                    == 'report-business-catalogue-allocation-preparation-v213')).first()
            if not current:
                raise LeaseLost('原当前配置归属任务的真实目录、生成族或Job关联已变化')
    elif local_goal_begin:
        row = connection.execute(select(Job.lease_token, Job.status, Job.lease_until, Job.kind, Job.data)
            .where(Job.id == job_id)).first()
        local_kind = (row is not None and (row.kind == _LOCAL_GOAL_KIND
            or row.kind == 'append_goal_evaluation_v297' and type(row.data) is dict
            and row.data.get('schema') == 'goal-evaluation-append-job-v297'
            or row.kind == 'promote_goal_evaluation_v299' and type(row.data) is dict
            and row.data.get('schema') == 'goal-evaluation-promotion-job-v299'
            or row.kind == 'prepare_same_goal_advice_v302' and type(row.data) is dict
            and row.data.get('schema') == 'same-goal-advice-prepare-job-v302'
            or row.kind == 'generate_same_goal_advice_v302' and type(row.data) is dict
            and row.data.get('schema') == 'same-goal-advice-send-job-v308'
            or row.kind == 'execute_goal_continuation_read_v314' and type(row.data) is dict
            and row.data.get('schema') == 'same-goal-continuation-execution-job-v314'))
        if (not local_kind or type(row.data) is not dict
                or 'time_trigger' in row.data or 'time_trigger_hash' in row.data):
            raise LeaseLost('此原后台任务不属于可续租的本地手动目标核对')
    elif graph_handler_begin:
        row = connection.execute(select(Job.lease_token, Job.status, Job.lease_until, Job.kind)
            .where(Job.id == job_id)).first()
        if not row or row.kind != _GRAPH_HANDLER_KIND:
            raise LeaseLost('此原后台任务不属于可续租的图节点处理器')
    else:
        row = connection.execute(select(Job.lease_token, Job.status, Job.lease_until)
            .where(Job.id == job_id).with_for_update()).first()
    db_now = connection.scalar(select(func.clock_timestamp()))
    # Same-ZoneInfo comparisons ignore fold during a repeated local hour.
    # Compare absolute instants so an expired old worker cannot pass the fence.
    if (not row or row.lease_token != token or row.status != 'running' or not row.lease_until
            or row.lease_until.astimezone(timezone.utc) <= db_now.astimezone(timezone.utc)):
        raise LeaseLost('后台任务租约已失效，旧执行结果未写入')


class _LocalGoalPhase:
    def __init__(self, db, job_id, token):
        self.db = db
        self.job_id = job_id
        self.token = token
        self.active = False
        self.closed = False

    def activate(self):
        # Initial recovery must already have committed.  No SQL is performed
        # here: the next after_begin checks the ACTUAL DB kind/data and token.
        if (self.closed or self.db.in_transaction()
                or self.db.info.get(_LOCAL_GOAL_PHASE) is not self
                or self.db.info.get('worker_lease') != {'job_id': self.job_id, 'lease_token': self.token}):
            raise ValueError('本地目标核对必须在原恢复事务提交后单独进入')
        self.active = True


@contextmanager
def local_goal_phase(db, job_id, token):
    """A server-only phase; never permission for a tool or external operation.

    It starts inactive so the original recovery transaction retains its lock.
    Only an explicit activation before the actor's first query changes BEGIN;
    COMMIT always keeps the original exclusive fence.  Timer Jobs are excluded
    centrally, and late handler writes may still take their normal row locks.
    """
    if _LOCAL_GOAL_PHASE in db.info:
        raise ValueError('不能叠加本地目标租约阶段')
    phase = _LocalGoalPhase(db, job_id, token)
    db.info[_LOCAL_GOAL_PHASE] = phase
    try:
        yield phase
    finally:
        phase.active = False
        phase.closed = True
        if db.info.get(_LOCAL_GOAL_PHASE) is phase:
            db.info.pop(_LOCAL_GOAL_PHASE)


class _GraphHandlerPhase:
    def __init__(self, db, job_id, token):
        self.db = db
        self.job_id = job_id
        self.token = token
        self.active = False
        self.closed = False

    def activate(self):
        # The worker calls this only after the original recovery commit, before
        # its first actor/source query. Actual kind/token/deadline are checked
        # by the next BEGIN, not trusted from the retained ORM Job or its JSON.
        if (self.closed or self.db.in_transaction()
                or self.db.info.get(_GRAPH_HANDLER_PHASE) is not self
                or self.db.info.get('worker_lease') != {'job_id': self.job_id, 'lease_token': self.token}):
            raise ValueError('图节点处理必须在原恢复事务提交后单独进入')
        self.active = True


@contextmanager
def graph_handler_lease_phase(db, job_id, token):
    """Let long graph validation renew its original lease without a BEGIN lock.

    This server-only lifetime changes no dispatch, cancel, IAM or COMMIT fence.
    Recovery/finalization and every other Job kind keep their exclusive BEGIN.
    A JSON flag is never a phase and no permission or source verdict is cached.
    """
    if _GRAPH_HANDLER_PHASE in db.info:
        raise ValueError('不能叠加图节点租约阶段')
    phase = _GraphHandlerPhase(db, job_id, token)
    db.info[_GRAPH_HANDLER_PHASE] = phase
    try:
        yield phase
    finally:
        phase.active = False
        phase.closed = True
        if db.info.get(_GRAPH_HANDLER_PHASE) is phase:
            db.info.pop(_GRAPH_HANDLER_PHASE)


class _CatalogueAllocationHandlerPhase:
    """A server-owned lifetime, never a catalogue/source/payment permission."""
    def __init__(self, db, job_id, token):
        self.db, self.job_id, self.token = db, job_id, token
        self.actor_id = self.workspace_id = None
        self.active = False
        self.closed = False

    def activate(self, *, workspace_id, actor_id):
        local = self.db.info.get(_LOCAL_GOAL_PHASE)
        graph = self.db.info.get(_GRAPH_HANDLER_PHASE)
        if (self.closed or self.active or self.db.in_transaction()
                or self.db.info.get(_CATALOGUE_ALLOCATION_PHASE) is not self
                or self.db.info.get('worker_lease') != {'job_id': self.job_id, 'lease_token': self.token}
                or type(local) is _LocalGoalPhase and local.active and not local.closed
                or type(graph) is _GraphHandlerPhase and graph.active and not graph.closed
                or type(workspace_id) is not str or not workspace_id
                or type(actor_id) is not str or not actor_id):
            raise ValueError('目录归属必须在原恢复事务提交后以原执行身份进入')
        self.workspace_id, self.actor_id = workspace_id, actor_id
        self.active = True


@contextmanager
def catalogue_allocation_handler_lease_phase(db, job_id, token):
    """Opt in only after fresh allocation recovery commits, before actor IO.

    Only BEGIN's Job lock changes. Every COMMIT retains the original exclusive
    lease/current-actor fence; JSON flags, old families and issued activities
    cannot create this typed lifetime. The handler still checks real authority.
    """
    if _CATALOGUE_ALLOCATION_PHASE in db.info:
        raise ValueError('不能叠加目录归属处理租约阶段')
    phase = _CatalogueAllocationHandlerPhase(db, job_id, token)
    db.info[_CATALOGUE_ALLOCATION_PHASE] = phase
    try:
        yield phase
    finally:
        phase.active = False
        phase.closed = True
        if db.info.get(_CATALOGUE_ALLOCATION_PHASE) is phase:
            db.info.pop(_CATALOGUE_ALLOCATION_PHASE)


def attach(db, job_id, token):
    if db.in_transaction():
        raise ValueError('租约隔离必须在业务事务开始前绑定')
    db.info['worker_lease'] = {'job_id': job_id, 'lease_token': token}
    def begin(session, transaction, connection):
        if not transaction.nested:
            phase = db.info.get(_LOCAL_GOAL_PHASE)
            graph_phase = db.info.get(_GRAPH_HANDLER_PHASE)
            allocation = db.info.get(_CATALOGUE_ALLOCATION_PHASE)
            if type(allocation) is _CatalogueAllocationHandlerPhase and allocation.active and not allocation.closed:
                if (allocation.job_id != job_id or allocation.token != token
                        or type(phase) is _LocalGoalPhase and phase.active and not phase.closed
                        or type(graph_phase) is _GraphHandlerPhase and graph_phase.active and not graph_phase.closed):
                    raise LeaseLost('目录归属处理阶段与原租约不一致')
                _check(connection, job_id, token, catalogue_allocation_begin=True,
                    allocation_actor_id=allocation.actor_id, allocation_workspace_id=allocation.workspace_id)
                actor = db.info.get('worker_actor')
                if actor is not None and (getattr(actor, 'user_id', None) != allocation.actor_id
                        or getattr(actor, 'workspace_id', None) != allocation.workspace_id):
                    raise ExecutionUnauthorized('目录归属处理身份与原后台任务不一致')
            elif type(graph_phase) is _GraphHandlerPhase and graph_phase.active and not graph_phase.closed:
                if (graph_phase.job_id != job_id or graph_phase.token != token
                        or type(phase) is _LocalGoalPhase and phase.active and not phase.closed):
                    raise LeaseLost('图节点处理阶段与原租约不一致')
                _check(connection, job_id, token, graph_handler_begin=True)
            elif type(phase) is _LocalGoalPhase and phase.active and not phase.closed:
                if phase.job_id != job_id or phase.token != token:
                    raise LeaseLost('本地核对阶段与原租约不一致')
                _check(connection, job_id, token, local_goal_begin=True)
            else:
                _check(connection, job_id, token)
            _check_actor(connection, db)
    def commit(session):
        _check(session.connection(), job_id, token)
        _check_actor(session.connection(), db)
        _renew_advice_checkpoint(session.connection(),db,job_id,token)
    event.listen(db, 'after_begin', begin)
    event.listen(db, 'before_commit', commit)
    db.info['worker_lease_fence_v269'] = (_FENCE_WITNESS, job_id, token, begin, commit)


def fenced(db):
    """A dictionary claiming ownership is not an installed transaction fence."""
    witness = db.info.get('worker_lease_fence_v269')
    owned = db.info.get('worker_lease', {})
    return (type(witness) is tuple and len(witness) == 5 and witness[0] is _FENCE_WITNESS
        and witness[1] == owned.get('job_id') and witness[2] == owned.get('lease_token')
        and event.contains(db, 'after_begin', witness[3])
        and event.contains(db, 'before_commit', witness[4]))


def checkpoint(db):
    """Check and release the owned transaction before a potentially long external wait."""
    if db.info.get('worker_lease'):
        db.commit()


def ownership(db):
    return dict(db.info.get('worker_lease', {}))


def renew(bind, job_id, token):
    # clock_timestamp(), unlike transaction now(), advances even after lock waits.
    with bind.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '250ms'"))
        connection.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        result = connection.execute(update(Job).where(Job.id == job_id, Job.lease_token == token,
            Job.status == 'running', Job.lease_until > func.clock_timestamp()).values(
            lease_until=func.clock_timestamp() + timedelta(seconds=LEASE_SECONDS)))
        return result.rowcount == 1


@contextmanager
def heartbeat(bind, job_id, token):
    stop = threading.Event()
    def loop():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                if not renew(bind, job_id, token):
                    return
            except DBAPIError:
                # A short local transaction may hold the Job lock. Deadline checks remain authoritative.
                continue
    thread = threading.Thread(target=loop, name='worker-lease-heartbeat', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)
