"""Commit-boundary checks for a test-only v2 Start.

All locks protect current evidence until commit; no network authority is given.
Lock conflicts are rejected with a bounded wait rather than silently reusing
stale data. This candidate does not register a production endpoint or worker.
"""
import copy
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from fastapi import HTTPException
from . import evidence, goals, native_goals
from . import goal_evaluation_provider_v288 as provider
from . import layered_approval_store_v175 as approvals
from . import same_goal_advice_store_v302 as store
from .contracts import digest
from .models import Capability, DomainInstance, Evidence, InstanceRelease, Task
from .runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet, WorkflowDocumentPacketV207
from .temporal import instant
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288
from .layered_approval_models_v175 import ApprovalManagedTaskV175, ApprovalSubmissionV175, ApprovalPolicyVersionV175


def check_goal(db,p,task_id,prep):
    task=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==task_id)
        .execution_options(populate_existing=True))
    store.need(task is not None and task.status!='workflow_cancelled'
        and not task.data.get('native_goal_cancelled') and not task.data.get('native_goal_acceptance')
        and not task.data.get('native_goal_timed_out'))
    goal=goals.snapshot(task)
    store.need(goal['version']==prep['scope']['goal_version'] and goal['hash']==prep['scope']['goal_hash']
        and digest(goal)==prep['scope']['goal_snapshot_hash'])
    deadline=goal['spec'].get('deadline');now=db.scalar(select(func.clock_timestamp()))
    store.need(deadline is None or instant(deadline)>now,'原业务目标已到截止时间；未创建发送申请')


def _rows_locked(db,p,model,identities):
    wanted=sorted(set(identities));store.need(bool(wanted))
    rows=list(db.scalars(select(model).where(model.workspace_id==p.workspace_id,model.id.in_(wanted))
        .order_by(model.id).with_for_update(read=True,nowait=True).execution_options(populate_existing=True)))
    store.need([row.id for row in rows]==wanted)
    return rows


def _lock_approved_sources(db,p,task,prep):
    selection=prep['request']['document_source'];source_ref=selection['source_set_ref'] if selection['kind']=='approved_source_set' else selection['packet']
    _rows_locked(db,p,RuntimeTaskDocumentSourceSet,[source_ref['source_set_id']])
    if selection['kind']=='document_packet_v207':
        _rows_locked(db,p,WorkflowDocumentPacketV207,[selection['packet']['id']])
    _rows_locked(db,p,GoalEvaluationV288,[prep['mature_head']['evaluation_ref']['id']])
    managed=approvals.managed(db,p,task)
    if managed is not None:
        managed=_rows_locked(db,p,ApprovalManagedTaskV175,[managed.id])[0]
        submission,submission_data,_=approvals.current_submission(db,p,managed)
        _rows_locked(db,p,ApprovalSubmissionV175,[submission.id])
        _rows_locked(db,p,ApprovalPolicyVersionV175,[submission_data['policy_version_id']])
    # These existing application guards authenticate approvals and lock the
    # original current Goal sources, instead of treating IDs as approvals.
    native_goals.guard(db,p,task,lock_sources=True)
    provider.require_current(db,p,task,expected_ref=prep['mature_head']['evaluation_ref'],lock_sources=True)


def check_freshness(db,p,prep):
    ref=prep['mature_head']['evaluation_ref']
    evaluation=db.scalar(select(GoalEvaluationV288).where(GoalEvaluationV288.workspace_id==p.workspace_id,
        GoalEvaluationV288.id==ref['id']).execution_options(populate_existing=True))
    now=db.scalar(select(func.clock_timestamp()))
    store.need(evaluation is not None and evaluation.record_hash==ref['record_hash']
        and not provider._stale(evaluation.data,now), '原评价资料已过期；未创建发送申请')


def lock_current(db,p,task,prep,preview_record):
    previous_timeout=db.scalar(text("SELECT current_setting('lock_timeout')"))
    try:
        with db.begin_nested():
            # All cooperative lock waits, including deeper source readers, are
            # bounded. A busy original is a refusal, never a replay substitute.
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            instance=_rows_locked(db,p,DomainInstance,[prep['scope']['instance_id']])[0]
            store.need(instance.status=='partially_active' and instance.active_release_id==prep['scope']['release_id'])
            release=_rows_locked(db,p,InstanceRelease,[prep['scope']['release_id']])[0]
            store.need(release.instance_id==instance.id and release.package_id==prep['scope']['package_id'])
            _rows_locked(db,p,Capability,[prep['source_business_capability_ref']['id'],
                prep['advice_capability_ref']['id'],prep['model_pin']['id'],prep['scope']['package_id']])
            _rows_locked(db,p,Evidence,[pin['evidence_id'] for pin in preview_record['evidence_pins']])
            _lock_approved_sources(db,p,task,prep)
            check_goal(db,p,task.id,prep)
            check_freshness(db,p,prep)
            db.execute(text("SELECT set_config('lock_timeout', :value, true)"),{'value':previous_timeout})
    except DBAPIError as error:
        if getattr(error.orig,'sqlstate',None) != '55P03':raise
        raise HTTPException(409,'原资料或授权正在修改，请保留原申请并稍后重试') from error


def check_evidence_bytes(db,p,preview_record):
    for pin in preview_record['evidence_pins']:
        row,raw=evidence.evidence_bytes(db,p,pin['evidence_id'])
        store.need(row.sha256==pin['sha256'] and row.size_bytes==pin['size_bytes']==len(raw)
            and row.media_type==pin['media_type'])


def unbind_commit_guard(db):
    prior=db.info.pop('v2_start_goal_commit_guard',None)
    if prior is not None and event.contains(db,'before_commit',prior[1]):
        event.remove(db,'before_commit',prior[1])


def _clear_guard_after_root(session, transaction):
    if transaction.parent is None: unbind_commit_guard(session)


def _clear_guard_after_rollback(session, transaction):
    prior=session.info.get('v2_start_goal_commit_guard')
    current=prior[2] if prior is not None else None
    while current is not None:
        if current is transaction:
            unbind_commit_guard(session);return
        current=current.parent


def bind_commit_guard(db,p,task_id,prep,preview_record):
    prep=copy.deepcopy(prep);preview_record=copy.deepcopy(preview_record)
    identity=(p.workspace_id,p.user_id,task_id,prep['record_hash'],preview_record['preview_hash'])
    prior=db.info.get('v2_start_goal_commit_guard')
    if prior is not None:
        store.need(prior[0]==identity and event.contains(db,'before_commit',prior[1]));return
    def check(session):
        check_goal(session,p,task_id,prep)
        check_freshness(session,p,prep)
        check_evidence_bytes(session,p,preview_record)
    if not db.info.get('v2_start_goal_guard_cleanup_installed'):
        event.listen(db,'after_transaction_end',_clear_guard_after_root)
        event.listen(db,'after_soft_rollback',_clear_guard_after_rollback)
        db.info['v2_start_goal_guard_cleanup_installed']=True
    event.listen(db,'before_commit',check)
    db.info['v2_start_goal_commit_guard']=(identity,check,db.get_nested_transaction() or db.get_transaction())
