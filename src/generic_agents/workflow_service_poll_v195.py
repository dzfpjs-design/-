"""Wake the same saved graph Job to READ its original service receipt only."""
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .db import engine
from .models import Job, Task, Invocation


def schedule_once(workspace_id=None, *, job_id=None):
    if workspace_id is not None and (type(workspace_id) is not str
            or str(UUID(workspace_id)) != workspace_id):
        raise ValueError('工作区编号无效')
    if job_id is not None and (workspace_id is None or type(job_id) is not str or str(UUID(job_id)) != job_id):
        raise ValueError('定向服务回执须明确工作区和标准原后台任务编号')
    count = 0
    with Session(engine()) as db:
        query = select(Job).where(Job.kind == 'execute_full_semantic_graph_node',
            Job.status == 'waiting_service', Job.available_at <= func.clock_timestamp()).order_by(
                Job.available_at, Job.id).limit(50)
        if workspace_id is not None: query = query.where(Job.workspace_id == workspace_id)
        if job_id is not None: query = query.where(Job.id == job_id)
        for candidate in list(db.scalars(query)):
            # Match the normal Task→Job lock order. No call is dispatched here.
            task = db.scalar(select(Task).where(Task.id == candidate.data.get('task_id'),
                Task.workspace_id == candidate.workspace_id).with_for_update())
            job = db.scalar(select(Job).where(Job.id == candidate.id,
                Job.status == 'waiting_service').with_for_update().execution_options(populate_existing=True))
            if job is None: continue
            if task is None or task.data.get('graph_runtime', {}).get('status') in {'cancelled', 'failed', 'stopped'}:
                job.status = 'cancelled'; continue
            transport = task.data.get('graph_jobs', {}).get(job.data.get('activation_id'))
            call = db.scalar(select(Invocation).where(Invocation.workspace_id == job.workspace_id,
                Invocation.idempotency_key == job.data.get('invocation_key')))
            approval = call.data.get('approval') if call is not None else None
            from .workflow_service_catalog_v197 import monitoring_receipt_retained
            receipt_pending = call is not None and (call.status == 'waiting_receipt'
                or call.status == 'unknown' and type(call.data.get('service_state')) is dict
                and call.data['service_state'].get('schema') == 'workflow-service-state-v195'
                or monitoring_receipt_retained(call))
            if (not transport or transport.get('job_id') != job.id or transport.get('phase') != 'waiting_service'
                    or call is None or call.id != transport.get('invocation_id')
                    or not receipt_pending
                    or type(approval) is not dict or approval.get('schema') != 'workflow-service-approval-v195'
                    or call.data.get('job_id') != job.id):
                job.status = 'blocked'; job.data = {**job.data,
                    'error': '原服务请求或等待记录无法核对；未创建新调用或补采'}
                continue
            job.status = 'queued'
            job.data = {**job.data, 'service_receipt_checks': job.data.get('service_receipt_checks', 0) + 1}
            count += 1
        db.commit()
    return count
