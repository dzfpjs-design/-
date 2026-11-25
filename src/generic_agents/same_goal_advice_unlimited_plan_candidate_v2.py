"""Read-only preparation proposal for a real v2 policy and source selection.

This module never creates a Plan, Job, audit, receipt, preview or route. Its
result must not be used as authority: mixed-version nonce and workers must be
isolated and all facts rechecked by the future actual writer/lease holder.
"""
import copy
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import goal_budget_snapshot_candidate_v2 as pending
from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_quota_v304 as quota
from . import same_goal_advice_read_session_v2 as isolation
from . import same_goal_advice_sources_v302 as sources
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_authority_v2 as authority
from . import same_goal_advice_unlimited_contracts_v2 as contracts
from . import same_goal_advice_unlimited_policy_v2 as policy_schema
from . import same_goal_advice_unlimited_store_v2 as v2store
from .contracts import digest
from .models import Audit, AuthSession, uid
from .security import Principal
from .temporal import instant


def _derive_core(db, p, task_id, body, policy_ref, requested_by):
    # Result is never authority. Callers must separately prove a current HTTP
    # session or a fenced durable grant tied to the original saved Plan.
    request = c.Prepare.model_validate(body).model_dump(mode='json')
    reference = c._parse(c.Ref, policy_ref)
    task_id = store.existing._id(task_id)
    with isolation.isolated(db, task_id) as reader:
        pending._extra_state(db, reader, p.workspace_id)
        policy = authority.current_policy(reader, p, task_id, reference)
        task = store.existing._task(reader, p, task_id, lock=True)
        context = quota._configuration_base(reader, p, task, request)
        store.need(context['scope'] == policy['scope']
            and store._pin(context['model']) == policy['model_pin']
            and context['model'].data['execution']['provider'] == 'deepseek'
            and request['expected_mature_head_hash'] == policy['mature_head_hash'])
        selected = store.provider.binding(reader, p, task)
        store.need(selected is not None)
        nodes = {node['id']: node for node in selected['binding']['graph']['nodes']}
        business_actor, data_actor = nodes['reasoning']['actor_id'], nodes['packet']['actor_id']
        store.need(store._prepare_actor_allowed(p, business_actor, data_actor))
        mature = store.existing._mature_head(reader, p, task, selected)
        store.need(mature['head']['head_hash'] == request['expected_mature_head_hash'])
        store.need(request['document_source'] is not None)
        sources.resolve_documents(reader, p, task, request['document_source'])
        plan_id = uid(); scope = {**context['scope'], 'analysis_plan_id': plan_id}
        record = mature['record']; manifest = context['advice'].data
        prep = contracts.seal_preparation({'schema': contracts.PREPARATION, 'scope': scope,
            'source_business_capability_ref': store._pin(context['source']),
            'advice_capability_ref': store._pin(context['advice']), 'model_pin': store._pin(context['model']),
            'data_actor_id': data_actor, 'business_actor_id': business_actor,
            'original_query_ref': record['original_query_ref'],
            'mature_head': {'head_hash': mature['head']['head_hash'],
                'evaluation_ref': mature['head']['evaluation_ref'], 'goal_check_job_id': mature['goal_check_job'].id,
                'memory_id': mature['memory'].id, 'report_hash': mature['report_hash']},
            'evaluation_query_ref': record['evaluation_query_ref'], 'evaluation_cut': record['cut_at'],
            'instruction': context['source'].data['specification']['instruction'], 'budget': policy['limits'],
            'request': request, 'request_hash': digest(request), 'requested_by': requested_by}, manifest=manifest)
        pending._extra_state(db, reader, p.workspace_id)
        result = {'preparation_candidate': copy.deepcopy(prep), 'policy_ref': copy.deepcopy(reference),
            'plan_write_authorized': False, 'prepare_job_authorized': False,
            'preview_authorized': False, 'cloud_send_authorized': False}
    return result


@store.checked
def derive_delegated(db, p, task_id, job_id, body, policy_ref, grant_id):
    from . import same_goal_advice_offline_grant_candidate_v2 as delegation
    proof = delegation.check_worker(db, p, task_id, job_id, grant_id)
    result = _derive_core(db, p, task_id, body, policy_ref, proof['requested_by'])
    # Recheck authorization and source after all current-configuration reads.
    delegation.check_worker(db, p, task_id, job_id, grant_id)
    return result


@store.checked
def derive_candidate(db, p, task_id, body, policy_ref):
    """Derive a read-only proposal from the real current HTTP session."""
    store.need(type(p) is Principal)
    try: UUID(p.session_id)
    except (ValueError, TypeError, AttributeError): store.need(False, '必须使用当前真实用户会话')
    result = _derive_core(db, p, task_id, body, policy_ref, p.user_id)
    reference = c._parse(c.Ref, policy_ref)
    task_id = store.existing._id(task_id)
    # Both the policy and real session can naturally expire during source
    # checking. Revalidate after the caller-state postcheck, in the same
    # locked transaction; this still grants no write or dispatch authority.
    with db.no_autoflush:
        with Session(bind=db.connection(), autoflush=False, expire_on_commit=False,
                     join_transaction_mode='rollback_only') as reader:
            current = authority.current_policy(reader, p, task_id, reference)
            store.need(digest(current['limits']) == digest(result['preparation_candidate']['budget']))
            v2store._lock_current_reader(reader, p)
            row = reader.execute(select(func.clock_timestamp(), AuthSession.expires_at, Audit)
                .select_from(AuthSession).join(Audit, Audit.id == reference['id']).where(
                    AuthSession.id == p.session_id, AuthSession.user_id == p.user_id,
                    AuthSession.revoked.is_(False), Audit.workspace_id == p.workspace_id,
                    Audit.object_id == task_id, Audit.action == policy_schema.POLICY)
                .with_for_update(read=True, of=[AuthSession, Audit])
                .execution_options(populate_existing=True)).one_or_none()
            store.need(row is not None, '原政策或当前会话无法核对')
            checked_at, session_expiry, event = row
            store.need(checked_at.tzinfo is not None and session_expiry > checked_at
                and digest(event.data) == reference['record_hash'], '原政策或当前会话已变化')
            saved, _ = policy_schema.policy_shape(event, checked_at)
            store.need(instant(saved['expires_at']) > checked_at, '原 DeepSeek 政策已到期')
            result['policy_and_session_checked_at'] = checked_at.isoformat()
    return result
