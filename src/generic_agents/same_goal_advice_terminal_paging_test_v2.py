"""Bounded, fenced terminal scan experiment; never grants execution.

This is not wired to versioned inventory, policy, API or a worker. It does not
verify nonterminal records and cannot establish that a new request is safe.
"""
from uuid import UUID

from sqlalchemy import func, select, text

from . import same_goal_advice_cloud_claim_test_v2 as claims
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_terminal_candidate_v2 as terminal
from . import same_goal_advice_unlimited_store_v2 as custody
from .config import settings
from .models import AuthSession, Task
from .security import Principal


@store.checked
def scan(db,p,task_id,*,page_size=64):
    """Recheck all terminal rows in stable keyset pages with no full row list.

    The caller must use a fresh READ COMMITTED transaction. After the identity
    check, table fences are acquired before reading history; each subsequent
    statement therefore sees commits preceding the fences, while writers stay
    blocked until the transaction ends. This is not a scalable lock design.
    """
    store.need(not db.new and not db.dirty and not db.deleted,'需要没有待提交修改的新事务')
    claims.require_test_database(db)
    store.need(settings().v2_terminal_history_test_enabled is True and type(p) is Principal)
    store.need(type(page_size) is int and 1<=page_size<=128 and db.get_nested_transaction() is None)
    try: UUID(p.session_id)
    except (ValueError,TypeError,AttributeError):store.need(False,'需要当前有效登录会话')
    isolation=db.scalar(text("SELECT current_setting('transaction_isolation')"))
    store.need(isolation=='read committed','终态扫描需要 READ COMMITTED 和后续写入门禁')
    # Locks prevent uncooperative database writers from inserting a late
    # orphan while page boundaries are being traversed. NOWAIT avoids waiting
    # while holding a task lock. The global availability impact is unresolved.
    db.execute(text('LOCK TABLE public.plan_versions, public.jobs, public.audit_events, '
        'public.cloud_model_calls, public.invocations, public.evidence_objects, '
        'public.ga_test_advice_v2_terminals, public.ga_test_advice_v2_first_response, '
        'public.ga_test_advice_v2_dispatch_claims '
        'IN SHARE MODE NOWAIT'))
    store._reader(db,p)
    task=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==task_id)
        .with_for_update(read=True).execution_options(populate_existing=True))
    store.need(task is not None)
    custody._lock_current_reader(db,p)
    terminal._schema(db)
    orphan=db.scalar(text('''SELECT EXISTS(
        SELECT 1 FROM public.audit_events a
        WHERE a.workspace_id=:workspace AND a.action=:action
          AND (a.data->>'task_id'=:task OR a.object_id IN
              (SELECT id FROM public.plan_versions WHERE workspace_id=:workspace AND task_id=:task))
          AND NOT EXISTS(SELECT 1 FROM public.ga_test_advice_v2_terminals t
              JOIN public.plan_versions p ON p.id=t.plan_id AND p.workspace_id=t.workspace_id
              WHERE t.workspace_id=a.workspace_id AND t.audit_id=a.id AND t.plan_id=a.object_id
                AND p.task_id=:task))'''),{'workspace':p.workspace_id,'task':task_id,'action':terminal.ACTION})
    store.need(not orphan,'存在无法关联的终态回执；保留原记录等待核对')
    after='';count=pages=peak=0
    while True:
        page=db.execute(text('''SELECT t.plan_id,t.call_id,t.intent_id,t.audit_id
            FROM public.ga_test_advice_v2_terminals t
            JOIN public.plan_versions p ON p.id=t.plan_id AND p.workspace_id=t.workspace_id
            WHERE t.workspace_id=:workspace AND p.task_id=:task AND t.plan_id>:after
            ORDER BY t.plan_id LIMIT :limit'''),
            {'workspace':p.workspace_id,'task':task_id,'after':after,'limit':page_size}).all()
        if not page:break
        pages+=1;peak=max(peak,len(page))
        for row in page:
            proof=terminal.verify(db,p.workspace_id,row.call_id,row.intent_id)
            store.need(proof['plan_id']==row.plan_id and proof['terminal_audit_id']==row.audit_id
                and not proof['retry_authorized'] and not proof['new_request_authorized'])
            count+=1;after=row.plan_id
    valid=db.scalar(select(AuthSession.id).where(AuthSession.id==p.session_id,AuthSession.user_id==p.user_id,
        AuthSession.revoked.is_(False),AuthSession.expires_at>func.clock_timestamp()))
    store.need(valid==p.session_id,'当前会话已过期；终态扫描无效')
    return {'verified_terminals':count,'pages':pages,'max_page_rows':peak,
        'new_request_authorized':False,'retry_authorized':False}
