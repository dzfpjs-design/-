"""Read-only acceptance custody for the explicit authored image recipe.

Successful status alone is insufficient: every producer remains bound to its
activation/transport/Invocation/audit and the original input/artifact bytes.
This never invokes a capability, rebuilds a file or changes human receipts.
"""
import hashlib
from fastapi import HTTPException
from sqlalchemy import select
from . import models as m, workflow_graph_scope as scope
from . import full_semantic_graph_v1 as kernel
from . import workflow_data_quality_v219 as quality, workflow_quality_archive_v220 as archive
from .authored_image_workflow_graph_v243 import BINDING, OPERATIONS
from .contracts import digest
from .services import scoped
from .workflow_task_context_v196 import ProducerPending

ERROR = '图像复核的原件、真实工具回执或内部技术报告无法完整核对；未重跑或补造验收依据'


def need(value):
    if not value: raise HTTPException(409,ERROR)


def _completion_audit(db,p,task,activation,node,*,call=None):
    action = 'workflow.graph.node_completed' if call is not None else 'workflow.graph.human_completed'
    expected = {'activation_id':activation['id']}
    if call is not None: expected['invocation_id'] = call.id
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == task.id,m.Audit.action == action,
        m.Audit.data['activation_id'].as_string() == activation['id']).limit(2)))
    need(len(rows) == 1 and rows[0].actor_id == node['actor_id'] and quality.same(rows[0].data,expected))


def verify_task_results(db,p,task,graph,binding):
    if type(binding) is not dict or binding.get('schema') != BINDING: return
    try: _verify(db,p,task,graph,binding)
    except HTTPException as error:
        if error.status_code == 403: raise
        raise HTTPException(409,ERROR) from None
    except (KeyError,TypeError,ValueError,AttributeError,IndexError,OSError,ProducerPending):
        raise HTTPException(409,ERROR) from None


def _verify(db,p,task,graph,binding):
    need(task.workspace_id == p.workspace_id and quality.same(binding['graph'],graph))
    state = kernel.check_run(graph,task.data['graph_runtime'])
    need(state['status'] == 'succeeded' and task.status == 'workflow_completed')
    ability_nodes = {node['id']:node for node in graph['nodes'] if node['kind'] == 'ability'}
    need(set(ability_nodes) == set(OPERATIONS))
    receipts = task.data['workflow_runtime']['receipts']
    need(type(receipts) is list)
    producers = {}
    for operation in OPERATIONS:
        node = ability_nodes[operation]
        activations = [row for row in state['activations'].values() if row['node_id'] == operation]
        need(len(activations) == 1 and activations[0]['status'] == 'completed')
        activation = activations[0]
        actual,_,_,frozen,current,frozen_activation,frozen_node = scope._frozen(db,p,task.id,activation['id'])
        need(actual.id == task.id and quality.same(frozen,graph) and quality.same(current,state)
            and quality.same(frozen_activation,activation) and quality.same(frozen_node,node))
        transport = task.data['graph_jobs'][activation['id']]
        need(transport['phase'] == 'succeeded')
        job = scoped(db,m.Job,transport['job_id'],p)
        call = scoped(db,m.Invocation,transport['invocation_id'],p)
        scope._job_matches(task,job,state,activation,node)
        output = activation['output']
        need(job.status == call.status == 'succeeded' and job.kind == scope.KIND
            and job.data['actor_id'] == node['actor_id']
            and call.capability_id == node['execution']['capability_id']
            and call.data['capability_hash'] == node['execution']['capability_hash']
            and call.data['job_id'] == job.id and call.data['role'] == 'data'
            and transport['invocation_key'] == job.data['invocation_key'] == call.idempotency_key
            and transport['input_hash'] == job.data['input_hash'] == call.data['input_hash'] == digest(job.data['inputs'])
            and transport['output_hash'] == digest(output) and quality.same(call.data['output'],output))
        _completion_audit(db,p,task,activation,node,call=call)
        saved = [row for row in receipts if row.get('activation_id') == activation['id']]
        need(len(saved) == 1 and saved[0]['execution_mode'] == 'registered_graph_ability'
            and saved[0]['request_id'] == job.id and saved[0]['actor'] == node['actor_id']
            and saved[0]['invocation_id'] == call.id and saved[0]['capability_id'] == call.capability_id
            and saved[0]['capability_hash'] == node['execution']['capability_hash']
            and saved[0]['release_version'] == node['execution']['release_version']
            and saved[0]['input_hash'] == job.data['input_hash'] and quality.same(saved[0]['input_snapshot'],job.data['inputs'])
            and quality.same(saved[0]['output'],output) and saved[0]['entity_state_updated'] is False)
        producers[operation] = (job,call,output)
    first = producers['quality'][2]
    second = producers['dedup'][2]
    quality.checked_quality_report(db,p,task,first['report'])
    quality.checked_dedup_report(db,p,task,second['report'])
    need(quality.same(producers['dedup'][0].data['inputs']['quality_report'],first['report']))
    report_job, report_call, report_output = producers['report']
    need(quality.same(report_job.data['inputs'],{'quality_report':first['report'],'dedup_report':second['report']}))
    need(quality.same(archive.checked_saved_result(db,p,report_call),report_output))
    # Retained original bytes are cold-read under their original IDs/hashes.
    # Keep the historical technical findings; do not rerun format/model checks.
    remaining = quality.MAX_TOTAL_BYTES
    seen = {}
    for result in (first,second):
        for file in result['report']['files']:
            identity = file['evidence_id']
            evidence = scoped(db,m.Evidence,identity,p)
            need(evidence.sha256 == file['expected_sha256'] and evidence.media_type == file['media_type'])
            if identity not in seen:
                raw = quality.read_original(quality.settings().storage_dir,p.workspace_id,evidence,remaining=remaining)
                remaining -= len(raw)
                seen[identity] = (hashlib.sha256(raw).hexdigest(),len(raw))
            actual_hash, size = seen[identity]
            need(actual_hash == file['expected_sha256'] and
                (file['actual_sha256'] is None or file['actual_sha256'] == actual_hash)
                and (file['size_bytes'] is None or file['size_bytes'] == size))
    # Preserve and verify independent human records; they are not appended to
    # the V220 technical report, and they do not replace formal acceptance.
    for node in graph['nodes']:
        if node['kind'] != 'human': continue
        activations = [row for row in state['activations'].values() if row['node_id'] == node['id']]
        need(len(activations) == 1 and activations[0]['status'] == 'completed')
        activation = activations[0]
        _completion_audit(db,p,task,activation,node)
        saved = [row for row in receipts if row.get('activation_id') == activation['id']]
        need(len(saved) == 1 and saved[0]['execution_mode'] == 'human_graph_result'
            and saved[0]['step_id'] == node['source_step_id'] and saved[0]['actor'] == node['actor_id']
            and saved[0]['entity_state_updated'] is False and quality.same(saved[0]['output'],activation['output']))
        commands = [command for command in state['commands'] if command['request_id'] == saved[0]['request_id']]
        need(len(commands) == 1 and commands[0]['op'] == 'complete' and commands[0]['actor_id'] == node['actor_id'])
