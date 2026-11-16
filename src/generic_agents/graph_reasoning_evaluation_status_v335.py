"""Read-only explanation of the evaluation frozen by an authored graph node.

This view never authorizes a send or changes a graph, job, source or head.
Its caller must use an authorized, repeatable-read task snapshot.
"""
from fastapi import HTTPException

from . import business_goal_ready_sources_v288 as sources
from . import goal_evaluation_provider_v288 as provider
from . import workflow_graph as workflow
from . import workflow_graph_scope as scope
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope
from .authored_goal_data_workflow_store_v288 import BINDING
from .contracts import digest
from .models import Job, Memory
from .services import scoped

SCHEMA = 'reasoning-evaluation-status-v335'
MESSAGES = {
    'current': '原步骤引用的评价仍在有效期内；这不表示步骤已经执行或已获外发授权。',
    'superseded': '任务已有更新的评价，原步骤仍使用之前的资料；原流程尚未因此继续。',
    'expired': '原步骤引用的评价已过期，需重新核对资料；原流程状态保持不变。',
    'unknown': '评价资料状态暂时无法核对；原流程状态保持不变。',
    'not_applicable': '此任务没有可核对的此类推理步骤。',
}


def _result(status, *, activation_id=None, evaluated_at=None, current_evaluated_at=None,
            current_freshness=None, checked_at=None):
    return {'schema': SCHEMA, 'status': status, 'message': MESSAGES[status],
            'activation_id': activation_id, 'evaluated_at': evaluated_at,
            'current_evaluated_at': current_evaluated_at, 'current_freshness': current_freshness,
            'checked_at': checked_at}


def _checked_mature_report(db, p, task, head):
    """Check the Goal decision separately from freshness, including older heads."""
    job = provider._fresh(db, p, Job, head['goal_check_job_id'])
    memory = provider._fresh(db, p, Memory, job.data['goal_check_id'])
    report = memory.data['report']
    if not (job.status == 'succeeded' and memory.status == 'candidate'
            and memory.data['type'] == 'native_goal_check' and memory.data['task_id'] == task.id
            and memory.data['report_hash'] == digest(report)
            and report['goal_snapshot'] == provider.snapshot(task)
            and report[provider.JOB_REF] == head['evaluation_ref']
            and report['evidence'][provider.VIEW]['evaluation_ref'] == head['evaluation_ref']
            and report['goal_evaluation_head_hash_v288'] == head['head_hash']):
        raise ValueError('Current evaluation is not backed by its completed Goal check')


@authored_goal_binding_parse_scope()
def project(db, p, task, release, business, graph):
    """Compare independently verified frozen and mature records in one snapshot."""
    # Preserve actual caller authorization errors, rather than recasting them as
    # an unavailable source. Require current workspace membership before
    # deriving status from protected historical source records.
    try:
        sources._reader_iam(db, p)
    except HTTPException as error:
        raise HTTPException(403, '当前账号无法读取此任务的评价资料状态') from error
    try:
        binding = workflow.business_binding(release, business)
        if binding.get('schema') != BINDING:
            return _result('not_applicable')
        state = workflow.invoke_pure(workflow.graph_api.check_run, graph, task.data['graph_runtime'])
        identities = [key for key, activation in state['activations'].items()
                      if activation.get('node_id') == 'reasoning']
        if not identities:
            return _result('not_applicable')
        if len(identities) != 1:
            return _result('unknown')
        identity = identities[0]
        actual, selected_release, _, definition, frozen_state, activation, node = scope._frozen(
            db, p, task.id, identity)
        if selected_release.id != release.id or node['execution']['adapter'] != 'business_goal_ready_v288':
            return _result('unknown', activation_id=identity)
        transport = actual.data.get('graph_jobs', {}).get(identity)
        if not isinstance(transport, dict) or not transport.get('job_id'):
            return _result('unknown', activation_id=identity)
        job = scoped(db, Job, transport['job_id'], p)
        scope._job_matches(actual, job, frozen_state, activation, node)
        expected = workflow.invoke_pure(workflow.graph_api.ability_inputs, definition, frozen_state, identity)
        if expected != job.data['inputs']:
            return _result('unknown', activation_id=identity)
        selected_inputs = sources._inputs(job.data['inputs'])
        original = sources._data(db, p, actual, selected_inputs['data_packet'], live=False)[2]
        record = original['evaluation_record']
        original_ref = {'id': record['evaluation_id'], 'record_hash': record['record_hash']}
        head = provider._head(db, p, actual)
        if head is None:
            return _result('unknown', activation_id=identity)
        _checked_mature_report(db, p, actual, head)
        selected = provider.binding(db, p, actual)
        if selected is None:
            return _result('unknown', activation_id=identity)
        row, value, trusted = provider._record(db, p, actual, selected, head['evaluation_ref'], lock_sources=False)
        current = provider._evidence(row, value, trusted)
        fresh = current[provider.VIEW]['state']
        if fresh not in {'current', 'stale'}:
            return _result('unknown', activation_id=identity)
        status = 'superseded' if original_ref != head['evaluation_ref'] else ('current' if fresh == 'current' else 'expired')
        return _result(status, activation_id=identity, evaluated_at=record['cut_at'],
                       current_evaluated_at=value['cut_at'], current_freshness=fresh,
                       checked_at=trusted.isoformat())
    except HTTPException as error:
        if error.status_code in (401, 403):
            raise
        return _result('unknown')
    except (KeyError, TypeError, ValueError, StopIteration, AttributeError):
        return _result('unknown')
