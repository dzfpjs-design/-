"""Hold queued Goal checks while a managed approval is not executing.

This scheduler filter grants no execution permission: the live Goal handler
still verifies the complete approval ledger and actor. Expired running jobs
are deliberately not held here so the existing lease-recovery path can settle
an interrupted original attempt instead of leaving it perpetually running.
"""
from sqlalchemy import and_,select
from .models import Job
from .layered_approval_models_v175 import ApprovalManagedTaskV175 as Managed


def pending_predicate():
    held=(select(Managed.id).where(Managed.workspace_id==Job.workspace_id,
        Managed.task_id==Job.data['task_id'].as_string(),Managed.status!='executing')
        .correlate(Job).exists())
    return and_(Job.kind=='check_native_business_goal',Job.status=='queued',held)


def project(worker,approval):
    """Explain a held, already saved Job without altering its durable status."""
    if not isinstance(worker,dict) or not isinstance(approval,dict):return worker
    if worker.get('state')!='queued' or approval.get('status')=='executing':return worker
    if approval.get('status') in {'accepted','withdrawn'}:
        return {**worker,'state':'approval_closed','historical':True,
            'reason':'原业务已验收或撤回，旧检查请求保留，不再自动执行'}
    return {**worker,'state':'waiting_approval',
        'reason':'原业务尚未处于已批准并启动状态；原检查请求保留'}
