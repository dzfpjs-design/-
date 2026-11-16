"""Queue only the AudioRun completed by this exact original workflow Job."""
from sqlalchemy import select

from . import models as m
from .services import audit, scoped

KIND = 'sync_native_audio_run_v136'
ACTION = 'native_audio.sync_queued_v136'


def enqueue_completed(db, p, job):
    task = scoped(db, m.Task, job.data['task_id'], p)
    receipts = []
    for item in task.data.get('workflow_runtime', {}).get('receipts', []):
        if item.get('request_id') != job.id:
            continue
        cap = db.get(m.Capability, item.get('capability_id'))
        if cap is not None and cap.workspace_id == p.workspace_id and cap.data.get('execution', {}).get('adapter') == 'local_audio':
            receipts.append(item)
    if not receipts:
        return False
    from .native_audio_provenance_v136 import need
    need(len(receipts) == 1)
    receipt = receipts[0]
    activation_id = job.data.get('activation_id')
    from .workspace_api import saved_execution
    _, release, business = saved_execution(db, p, task.id)
    if activation_id:
        from .workflow_graph import graph_for, node_for
        execution = node_for(graph_for(release, business), task.data['graph_runtime']['activations'][activation_id])['execution']
    else:
        execution = next(step['execution'] for step in business['flow']['steps'] if step['id'] == receipt['step_id'])
    need(execution.get('adapter') == 'local_audio' and execution['capability_id'] == receipt['capability_id']
        and execution['capability_hash'] == receipt['capability_hash']
        and type(receipt.get('output')) is dict and bool(receipt['output'].get('audio_run_id')))
    need(receipt.get('execution_mode') == ('registered_graph_ability' if activation_id else 'registered_ability')
        and receipt.get('candidate_only') is True and receipt.get('actor') == p.user_id == job.data['actor_id']
        and receipt.get('activation_id') == activation_id)
    run = scoped(db, m.AudioRun, receipt['output']['audio_run_id'], p)
    need(run.status == 'succeeded' and run.data['workflow_job_id'] == job.id == run.data['job_id'])
    if activation_id:
        # The graph transport key is defined by its runtime, not inferred here.
        from .workflow_graph import TRANSPORT
        transport = task.data.get(TRANSPORT, {}).get(activation_id, {})
        need(transport.get('phase') == 'succeeded' and transport.get('invocation_id') == receipt.get('invocation_id'))
    key = 'native-audio-sync-v136:' + job.id
    original_data = {'actor_id': p.user_id, 'run_id': run.id, 'task_id': task.id,
        'activation_id': activation_id, 'processing_job_id': job.id}
    existing = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.idempotency_key == key))
    if existing is not None:
        need(existing.kind == KIND and all(key in existing.data and existing.data[key] == value for key, value in original_data.items()))
        verify_queue(db,p,existing)
    else:
        child = m.Job(workspace_id=p.workspace_id, kind=KIND, idempotency_key=key,data=original_data)
        db.add(child);db.flush()
        audit(db,p,ACTION,child.id,{'schema':'native-audio-sync-command-v136','key':key,'command':original_data})
    return True


def verify_queue(db,p,job):
    from .native_audio_provenance_v136 import need,same
    keys = {'actor_id','run_id','task_id','activation_id','processing_job_id'}
    need(job.kind == KIND and keys <= set(job.data) and p.user_id == job.data['actor_id']
        and job.idempotency_key == 'native-audio-sync-v136:' + job.data['processing_job_id'])
    command = {key:job.data[key] for key in keys}
    issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION,m.Audit.object_id == job.id).limit(2)))
    need(len(issued) == 1 and issued[0].actor_id == p.user_id
        and same(issued[0].data,{'schema':'native-audio-sync-command-v136','key':job.idempotency_key,'command':command}))
    original = scoped(db,m.Job,command['processing_job_id'],p)
    need(original.kind in {'execute_workflow_step','execute_full_semantic_graph_node'}
        and original.data['actor_id'] == command['actor_id'] and original.data['task_id'] == command['task_id']
        and original.data.get('activation_id') == command['activation_id'])


def process(db, p, job):
    from .native_audio_provenance_v136 import need
    from .native_receipts import sync_audio_run
    verify_queue(db,p,job)
    run = scoped(db, m.AudioRun, job.data['run_id'], p)
    need(run.data['workflow_job_id'] == job.data['processing_job_id'])
    result = sync_audio_run(db, p, run.id, task_id=job.data['task_id'], activation_id=job.data['activation_id'])
    job.data = {**job.data, 'result': result}
