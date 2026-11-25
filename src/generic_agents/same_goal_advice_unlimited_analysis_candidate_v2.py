"""Local v2 source/frame candidate; no route, preview, Start or cloud call.

Every result is derived from persisted current task, approved documents and the
existing mature-goal readers.  It is not authority to send or to bypass any
future preview, per-model consent, budget, idempotency or worker checks.
"""
from uuid import UUID
from sqlalchemy import func, select
from . import same_goal_advice_analysis_v305 as analysis
from . import same_goal_advice_frame_v305 as frames
from . import same_goal_advice_prepare_document_candidate_v2 as documents
from . import same_goal_advice_quota_v304 as quota
from . import same_goal_advice_read_session_v2 as isolation
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_authority_v2 as authority
from . import same_goal_advice_unlimited_contracts_v2 as contracts
from . import same_goal_advice_unlimited_policy_v2 as policy
from . import same_goal_advice_unlimited_store_v2 as custody
from .contracts import digest
from .models import Audit, AuthSession, Plan, Task
from .security import Principal
from .temporal import instant


@store.checked
def build_current(db, p, task, plan, *, started=False, reserved_test_only=False, intent_test_only=False):
    """Read current originals for the authenticated business actor only."""
    store.need(type(started) is bool and type(reserved_test_only) is bool and type(intent_test_only) is bool
        and (not reserved_test_only or started) and (not intent_test_only or reserved_test_only)
        and type(p) is Principal and p.role in {'admin', 'operator'}
        and type(task) is Task and type(plan) is Plan)
    try: UUID(p.session_id)
    except (ValueError, TypeError, AttributeError): store.need(False, '需要当前业务岗位登录会话')
    with isolation.isolated(db, task.id) as reader:
        current = store.existing._task(reader, p, task.id, lock=True)
        current_plan = store._fresh(reader, p, Plan, plan.id)
        store.need(current_plan.task_id == current.id and current_plan.status ==
            ('awaiting_dispatch' if started else 'awaiting_consent'))
        result = custody._read_queued(reader, p, current.id, current_plan.id, prepared=True, started=started,
            reserved_test_only=reserved_test_only,intent_test_only=intent_test_only)
        prep,job=result[:2]
        store.need(p.role == 'admin' or p.user_id == prep['business_actor_id'])
        checked = authority.current_policy(reader, p, current.id, prep['budget']['policy_ref'],
            reserved_plan_test_only=current_plan.id if reserved_test_only else None,
            intent_test_only=intent_test_only)
        store.need(checked['scope'] == {key: value for key, value in prep['scope'].items()
                if key != 'analysis_plan_id'}
            and digest(checked['limits']) == digest(prep['budget'])
            and checked['model_pin'] == prep['model_pin']
            and checked['mature_head_hash'] == prep['mature_head']['head_hash'])
        context = quota._configuration_base(reader, p, current, prep['request'])
        selected = analysis._match_scope_facts(reader, p, current, current_plan, prep, context)
        document = documents.cold_read(reader, p, current, current_plan, started=started,
            reserved_test_only=reserved_test_only,intent_test_only=intent_test_only)
        resolved = analysis._verified_sources(reader, p, current, current_plan, prep, job,
            selected, document, contracts.seal_source)
        frame = frames.from_resolved(reader, p, current, current_plan, resolved)
        # Source resolution may take time; an originally valid session/policy
        # can expire naturally while the Task/actor rows remain locked.
        custody._lock_current_reader(reader, p)
        row = reader.execute(select(func.clock_timestamp(), AuthSession.expires_at, Audit)
            .select_from(AuthSession).join(Audit, Audit.id == prep['budget']['policy_ref']['id'])
            .where(AuthSession.id == p.session_id, AuthSession.user_id == p.user_id,
                AuthSession.revoked.is_(False), Audit.workspace_id == p.workspace_id,
                Audit.object_id == current.id, Audit.action == policy.POLICY)
            .with_for_update(read=True, of=[AuthSession, Audit])
            .execution_options(populate_existing=True)).one_or_none()
        store.need(row is not None)
        now, session_expires, event = row
        store.need(now.tzinfo is not None and session_expires > now
            and digest(event.data) == prep['budget']['policy_ref']['record_hash'])
        saved, limits = policy.policy_shape(event, now)
        store.need(instant(saved['expires_at']) > now and digest(limits) == digest(prep['budget']))
        return frame
