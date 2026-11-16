"""Cold Goal-check barrier before the V288 reasoning SDK exists.

Only the normal evaluation-specific native Goal Job is eligible. A Task head,
source marker or matching hash is never a replacement for the actual packet
producer, adopted scope, queued Job and completed provider evidence. These
readers create no Job, query, Invocation, reservation, receipt or Goal result.
"""
import copy

from sqlalchemy import select

from . import goal_evaluation_provider_v288 as provider
from . import business_goal_ready_sources_v288 as sources
from . import workflow_graph_scope as scope
from .contracts import digest
from .goals import snapshot
from .models import Capability, Job
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation

ADAPTER = 'business_goal_ready_v288'
FAMILY = 'goal_ready_reasoning_v288'
KIND = 'goal_ready_goal_check_v288'
SCHEMA = 'goal-ready-goal-check-pin-v288'
FIELD = 'data_packet'
PIN_KEYS = {'schema', 'job_id', 'idempotency_key', 'actor_id', 'context_hash',
    'evaluation_ref', 'evaluation_producer_job_id', 'source_binding_hash',
    'graph_hash', 'goal_snapshot_hash', 'data_packet_hash'}
need, checked = provider.need, provider.checked


def _context(db, p, task, job, inputs):
    from . import business_goal_ready_runtime_v288 as runtime
    value = runtime._inputs(inputs)
    actual, release, business, graph, state, activation, node = scope._frozen(
        db, p, task.id, job.data['activation_id'])
    scope._job_matches(actual, job, state, activation, node)
    need(actual.id == task.id and actual.workspace_id == job.workspace_id == p.workspace_id
        and job.kind == scope.KIND and node['kind'] == 'ability' and node['id'] == 'reasoning'
        and node['actor_id'] == job.data['actor_id'] == p.user_id
        and node['execution']['adapter'] == ADAPTER and node['execution']['agent_role'] == 'business'
        and state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids']
        and activation['epoch'] == state['cancel_epoch'] == job.data['cancel_epoch']
        and value == job.data['inputs'] == activation['inputs_snapshot']['arguments']
        and digest(value) == job.data['input_hash'])
    cap = sources.fresh(db, p, Capability, node['execution']['capability_id'])
    runtime._manifest_shape(cap.data)
    need(cap.kind == 'tool' and cap.workspace_id == p.workspace_id and cap.status == 'published'
        and node['execution']['max_attempts'] == 1
        and cap.content_hash == cap.validated_hash == digest(cap.data)
        == node['execution']['capability_hash']
        and cap.release_version == node['execution']['release_version']
        and cap.data['input_schema'] == node['execution']['input_schema']
        and cap.data['output_schema'] == node['execution']['output_schema'])
    selected = provider.binding(db, p, actual)
    need(selected is not None and selected['task'].id == actual.id
        and selected['release'].id == release.id
        and selected['binding']['schema'] == provider.BINDING and selected['binding']['graph'] == graph)
    # Deliberately bypass runtime.qualify_evidence: that requires this Goal
    # check already complete. The data family still verifies its real saved
    # packet/SDK/audits/ACK and live freshness without selecting a Goal head.
    source_release, _package_hash, data = sources._data(db, p, actual, value[FIELD],
        allow_pending=True, live=True)
    row = sources.fresh(db, p, Evaluation, value[FIELD]['id'])
    reference = {'id': row.id, 'record_hash': row.record_hash}
    need(source_release.id == release.id and row.workspace_id == p.workspace_id
        and row.task_id == actual.id and row.instance_id == release.instance_id
        and row.release_id == release.id and row.package_id == actual.package_id
        and row.data == data['evaluation_record'] and data['packet'] == value[FIELD]
        and row.data['scope']['native_goal_context_hash'] == actual.data['native_goal_context_hash']
        == digest(actual.data['native_goal_context'])
        and row.source_binding_hash == selected['binding']['binding_hash']
        and row.graph_hash == digest(graph) and row.goal_snapshot_hash == digest(snapshot(actual)))
    # This is exactly the key emitted by the packet's ordinary
    # native_goals.queue(..., 'goal-evaluation-v288:' + row.id, ...).
    key = 'native-goal:' + actual.id + ':' + actual.data['native_goal_context_hash'] + ':goal-evaluation-v288:' + row.id
    original = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.kind == provider.KIND, Job.idempotency_key == key).execution_options(populate_existing=True))
    need(original is not None, '原评估尚无对应的目标核对任务；未新建请求或调用模型')
    provider._goal_job(actual, original)
    need(original.workspace_id == p.workspace_id and original.kind == provider.KIND
        and original.idempotency_key == key and original.data['task_id'] == actual.id
        and original.data['actor_id'] == row.actor_id == row.data['producer']['actor_id']
        and original.data['context_hash'] == actual.data['native_goal_context_hash']
        and original.data.get(provider.JOB_REF) == reference
        and original.data.get(provider.JOB_PRODUCER) == row.job_id)
    pin = {'schema': SCHEMA, 'job_id': original.id, 'idempotency_key': key,
        'actor_id': original.data['actor_id'], 'context_hash': original.data['context_hash'],
        'evaluation_ref': reference, 'evaluation_producer_job_id': row.job_id,
        'source_binding_hash': row.source_binding_hash, 'graph_hash': row.graph_hash,
        'goal_snapshot_hash': row.goal_snapshot_hash, 'data_packet_hash': digest(value[FIELD])}
    return actual, original, pin


def _completed(db, p, task, original, pin):
    need(original.status == 'succeeded', '原目标核对已失败、停止或状态不明；保留原记录，未调用模型')
    current = provider.require_current(db, p, task, expected_ref=pin['evaluation_ref'])
    need(current is not None and current['evaluation_ref'] == pin['evaluation_ref']
        and current['goal_check_job_id'] == original.id,
        '原目标核对的同一评估、完成记录或独立依据尚不能核实；未调用模型')


@checked
def waiting_pin(db, p, task, job, inputs):
    """Only queued/running original checks wait; all other cases are explicit."""
    actual, original, pin = _context(db, p, task, job, inputs)
    if original.status in {'queued', 'running'}:
        return copy.deepcopy(pin)
    _completed(db, p, actual, original, pin)
    return None


@checked
def ready(db, p, task, job, inputs, pin):
    """Cold same-reference recheck; a changed head never selects another cut."""
    need(type(pin) is dict and set(pin) == PIN_KEYS and pin.get('schema') == SCHEMA)
    actual, original, expected = _context(db, p, task, job, inputs)
    need(pin == expected)
    if original.status in {'queued', 'running'}:
        return False
    _completed(db, p, actual, original, expected)
    return True
