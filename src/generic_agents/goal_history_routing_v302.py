"""Finite, command-bound history custody; never new evidence or a live grant.

This authenticates only the base named by one actual immutable append Command.
It deliberately does not walk that base's ancestors or re-read its sensor facts.
Current Qe/ACK/originals, execution IAM/lease, head/CAS and acceptance retain
their full normal checks. No result here is suitable as provider evidence.
"""
import copy
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import or_

from . import goal_evaluation_append_contracts_v297 as commands_contract
from . import goal_evaluation_append_store_v297 as commands
from . import goal_evaluation_provider_v288 as provider
from . import goal_evaluation_query_v288 as query
from . import workflow_goal_evaluation_contracts_v288 as contract
from .contracts import digest
from .goals import snapshot
from .models import Audit, Capability, DataRun, Invocation, Job, Memory, Task
from .goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation, row_values


SCHEMA = 'goal-history-routing-proof-v302'
PURPOSE = 'routing-not-facts'
APPEND_RECORD = 'workflow-goal-evaluation-record-v299'
LEGACY_RECORD = 'workflow-goal-evaluation-record-v288'
ERROR = '追加命令所指定的历史完成沿革无法核对；旧事实未作为新证据，未取得执行或验收权限'
MAX_ROWS = 1000
_HEAD_FIELDS = frozenset({'schema', 'task_id', 'evaluation_ref', 'generation', 'query_id',
    'goal_snapshot_hash', 'source_binding_hash', 'goal_check_job_id', 'actor_id', 'previous_hash', 'head_hash'})


def need(value):
    if not value: raise HTTPException(409, ERROR)


def _same(left, right):
    if isinstance(left, datetime) or isinstance(right, datetime):
        return (type(left) is datetime and type(right) is datetime
            and left.tzinfo is not None and right.tzinfo is not None
            and left.utcoffset() is not None and right.utcoffset() is not None
            and left.astimezone(timezone.utc) == right.astimezone(timezone.utc))
    return digest(left) == digest(right)


def _rows(db, p, model, *where):
    return commands._rows(db, p, model, *where)


def _fresh(db, p, model, identity): return provider._fresh(db, p, model, identity)


def _audit(db, p, action, identity, actor, expected=None):
    rows = _rows(db, p, Audit, Audit.action == action, Audit.object_id == identity)
    need(len(rows) == 1 and rows[0].actor_id == actor
        and (expected is None or _same(rows[0].data, expected)))
    return rows[0]


def _scope(task, selected, record):
    binding, release = selected['binding'], selected['release']
    base = {'workspace_id': task.workspace_id, 'task_id': task.id,
        'instance_id': task.data['instance_id'], 'release_id': release.id,
        'package_id': task.package_id, 'business_id': task.data['business_id'],
        'graph_hash': digest(binding['graph'])}
    need(binding['schema'] == 'authored-goal-data-workflow-binding-v288'
        and binding['workspace_id'] == task.workspace_id
        and release.id == task.data['instance_release_id']
        and release.instance_id == task.data['instance_id'] and release.package_id == task.package_id
        and _same(record['scope'], base) and record['scope_hash'] == digest(base)
        and record['source_binding_hash'] == binding['binding_hash']
        and record['goal_snapshot_hash'] == digest(snapshot(task))
        and record['native_goal_context_hash'] == task.data['native_goal_context_hash']
        and record['goal_version'] == task.data['goal_version'] and record['goal_hash'] == task.data['goal_hash'])
    return {**base, 'source_binding_hash': binding['binding_hash'],
        'goal_snapshot_hash': record['goal_snapshot_hash'],
        'native_goal_context_hash': record['native_goal_context_hash']}


def _selected(db, p, task, record):
    events = _rows(db, p, Audit, Audit.action == provider.SELECTED, Audit.object_id == task.id,
        Audit.data['head_hash'].as_string() == record['base_mature_head_hash'])
    need(len(events) == 1)
    head = events[0].data
    need(type(head) is dict and set(head) == _HEAD_FIELDS and head['schema'] == provider.HEAD
        and type(head['generation']) is int and head['generation'] > 0
        and head['task_id'] == task.id and head['head_hash'] == record['base_mature_head_hash']
        == digest({key: value for key, value in head.items() if key != 'head_hash'})
        and head['evaluation_ref'] == record['base_evaluation_ref']
        and head['goal_check_job_id'] == record['base_goal_check_job_id']
        and head['goal_snapshot_hash'] == record['goal_snapshot_hash']
        and head['source_binding_hash'] == record['source_binding_hash']
        and events[0].actor_id == head['actor_id'])
    return head


def _memory(db, p, task, command, head, row):
    job = _fresh(db, p, Job, command['base_goal_check_job_id'])
    provider._goal_job(task, job)
    need(job.status == 'succeeded' and job.lease_until is None and job.id == head['goal_check_job_id']
        and job.data['actor_id'] == head['actor_id']
        and job.data[provider.JOB_REF] == command['base_evaluation_ref']
        and job.data[provider.JOB_PRODUCER] == row.job_id
        and job.data['goal_check_id'] == command['base_goal_check_memory_id'])
    memory = _fresh(db, p, Memory, command['base_goal_check_memory_id']); report = memory.data['report']
    need(memory.status == 'candidate' and memory.data['type'] == 'native_goal_check'
        and memory.data['task_id'] == task.id
        and memory.data['report_hash'] == digest(report) == command['base_goal_check_report_hash']
        and _same(report['goal_snapshot'], snapshot(task))
        and report[provider.JOB_REF] == command['base_evaluation_ref']
        and report['evidence'][provider.VIEW]['evaluation_ref'] == command['base_evaluation_ref']
        and report['goal_evaluation_head_hash_v288'] == head['head_hash'])
    return job, memory


def _query_snapshot(db, p, task, row, value, call):
    """Original snapshot/SAVED equality, without historical creator recursion."""
    from .goal_evaluation_append_query_v299 import SAVED as APPEND_SAVED, SCHEMA as APPEND_QUERY
    qrow = _fresh(db, p, DataRun, row.evaluation_query_id)
    marker = qrow.data[query.KEY]
    append = value['schema'] == APPEND_RECORD
    schema, action = (APPEND_QUERY, APPEND_SAVED) if append else (query.SCHEMA, query.SAVED)
    creator = {**value['producer'], 'request_key': call.idempotency_key}
    if append: creator['command_ref'] = value['command_ref']
    need(type(marker) is dict and marker['schema'] == schema
        and marker['evaluation_id'] == row.id and marker['request_id'] == row.job_id
        and marker['request_key'] == call.idempotency_key and _same(marker['creator'], creator)
        and marker['marker_hash'] == digest({key: item for key, item in marker.items() if key != 'marker_hash'})
        and _same(marker['scope'], value['scope']) and _same(marker['goal_snapshot'], value['goal_snapshot'])
        and _same(marker['preparation_policy'], value['preparation_policy'])
        and _same(marker['derived_window'], value['derived_window'])
        and marker['cut_at'] == value['cut_at'] and marker['cut_checked_at'] == value['cut_checked_at']
        and _same(marker['controls'], query.CONTROLS)
        and _same(marker['original_query_ref'], value['original_query_ref'])
        and qrow.owner_id == value['producer']['actor_id'] and qrow.version == 1
        and _same(query.query_ref(qrow), value['evaluation_query_ref'])
        and _same(qrow.data['request'], value['evaluation_request'])
        and _same(qrow.data['result'], value['evaluation_result'])
        and qrow.status == value['evaluation_result']['status'])
    if append:
        need(marker['producer_family'] == 'append_command_v297'
            and _same(marker['command_ref'], value['command_ref']))
    else:
        need('producer_family' not in marker and 'command_ref' not in marker)
    original = provider._q0(db, p, task)
    need(_same(query.query_ref(original), value['original_query_ref'])
        and _same(marker['original_query_snapshot'], original.data))
    events = _rows(db, p, Audit, Audit.action.in_([query.SAVED, APPEND_SAVED]),
        or_(Audit.object_id == qrow.id, Audit.data['evaluation_id'].as_string() == row.id))
    expected = {'schema': schema, 'evaluation_id': row.id, 'job_id': row.job_id,
        'marker_hash': marker['marker_hash'], 'query_ref': query.query_ref(qrow)}
    if append: expected.update(producer_family='append_command_v297', command_ref=value['command_ref'])
    need(len(events) == 1 and events[0].action == action and events[0].object_id == qrow.id
        and events[0].actor_id == value['producer']['actor_id'] and _same(events[0].data, expected))
    return copy.deepcopy(expected['query_ref'])


def _legacy_terminal(db, p, task, selected, row, value, source, journal):
    """Keep the original unknown Audit plus the exact fenced V294 hash event."""
    from . import workflow_goal_evaluation_recovery_v294 as recovery
    call, job, cap, activation = (source[key] for key in ('call', 'job', 'cap', 'activation'))
    actor = value['producer']['actor_id']
    initial = _audit(db, p, 'capability.invoke', call.id, actor)
    terminal = _rows(db, p, Audit, Audit.action == recovery.TERMINAL, Audit.object_id == call.id)
    if initial.data == {'status': 'succeeded'}:
        need(not terminal and call.data.get('service_previous_status') is None)
        return
    need(initial.data == {'status': 'unknown'} and recovery.applies(call)
        and call.data.get('service_previous_status') == 'unknown'
        and type(call.data.get('service_receipt_verified_at')) is str
        and bool(call.data['service_receipt_verified_at']))
    expected = {'schema': recovery.SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'job_id': job.id, 'invocation_id': call.id, 'actor_id': actor,
        'activation_id': activation['id'], 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'input_hash': digest(job.data['inputs']), 'approval_hash': digest(source['approval']),
        'source_binding_hash': selected['binding']['binding_hash'], 'initial_unknown_audit_id': initial.id,
        'query_ref': value['evaluation_query_ref'], 'evaluation_record_hash': row.record_hash,
        'journal_hash': journal['journal_hash'], 'output_hash': digest(journal['output'])}
    expected['record_hash'] = digest(expected)
    need(len(terminal) == 1 and terminal[0].actor_id == actor and _same(terminal[0].data, expected))
    _audit(db, p, 'workflow.service.receipt_verified', call.id, actor,
        {'job_id': job.id, 'activation_id': activation['id']})


def _legacy(db, p, task, selected, row, value):
    from . import workflow_goal_evaluation_store_v288 as store
    from . import workflow_goal_evaluation_append_v299 as append
    from . import goal_evaluation_maturity_v299 as maturity
    # Fixed original family, not absence of new markers, chooses this branch.
    need(value['schema'] == LEGACY_RECORD and 'producer_family' not in value and 'command_ref' not in value)
    need(not _rows(db, p, Command, or_(Command.job_id == row.job_id,
        Command.invocation_key == row.request_key)))
    need(not _rows(db, p, Audit, Audit.action == append.CREATED, Audit.object_id == row.invocation_id)
        and not _rows(db, p, Audit, Audit.action == maturity.MATURED,
            Audit.data['evaluation_ref']['id'].as_string() == row.id))
    source = store._cold_node(db, p, task, value['producer']['activation_id'], 'evaluation_packet')
    job, call, cap = (source[key] for key in ('job', 'call', 'cap'))
    need(job.id == row.job_id and call.id == row.invocation_id and job.status == call.status == 'succeeded'
        and job.lease_until is None and source['activation']['status'] == 'completed'
        and _same(source['graph'], selected['binding']['graph'])
        and not any(key in call.data for key in store.FOREIGN) and append.KEY not in call.data)
    journal = call.data[store.KEY]; output = store._output(row)
    need(journal['schema'] == store.STATE and journal['journal_hash'] == digest({
        key: item for key, item in journal.items() if key != 'journal_hash'})
        and journal['task_id'] == task.id and journal['job_id'] == job.id and journal['invocation_id'] == call.id
        and journal['capability_id'] == cap.id and journal['capability_hash'] == cap.content_hash
        and journal['input_hash'] == digest(job.data['inputs']) == call.data['input_hash']
        and journal['approval_hash'] == digest(source['approval'])
        and journal['evaluation_record_hash'] == row.record_hash
        and _same(journal['output'], output) and _same(call.data['output'], output))
    _audit(db, p, store.CREATED, call.id, value['producer']['actor_id'], store._anchor(journal))
    _legacy_terminal(db, p, task, selected, row, value, source, journal)
    return job, call


def _append(db, p, task, row, value, head):
    need(value['schema'] == APPEND_RECORD and value['producer_family'] == 'append_command_v297')
    command = _fresh(db, p, Command, value['command_ref']['id'])
    record, job = commands._saved(db, p, task, command)
    need(value['command_ref'] == {'id': command.id, 'record_hash': record['record_hash']}
        and job.id == row.job_id)
    phase, call = commands._operation_progress(db, p, task, record, job)
    need(phase == 'completed' and call is not None and call.id == row.invocation_id
        and job.status == call.status == 'succeeded' and job.lease_until is None
        and head['previous_hash'] == record['base_mature_head_hash'])
    return job, call


@commands.checked
def checked_base(db, p, task, selected, record):
    """Detached custody for exactly an ACCEPTED Command's admitted base.

    No full-reader fallback is automatic. A legacy record without the sealed
    Memory/report admission anchor needs the old full proof at its caller;
    missing, mixed or inconsistent new-family evidence always fails closed.
    """
    with db.no_autoflush:
        commands._reader(db, p)
        task = _fresh(db, p, Task, task.id)
        need(task.workspace_id == p.workspace_id)
        if not {'base_goal_check_memory_id', 'base_goal_check_report_hash'} <= record.keys():
            raise HTTPException(409, {'schema': SCHEMA, 'reason_code': 'legacy_full_needed',
                'reason': '原命令缺少受独立接纳审计绑定的历史成果锚点，不能使用有限沿革证明'})
        original = _fresh(db, p, Command, record['command_id'])
        admitted, _request_job = commands._saved(db, p, task, original)
        need(_same(admitted, record))
        scope = _scope(task, selected, admitted)
        head = _selected(db, p, task, admitted)
        rows = _rows(db, p, Evaluation, or_(Evaluation.id == admitted['base_evaluation_ref']['id'],
            Evaluation.invocation_id == admitted['base_evaluation_ref']['id']))
        need(len(rows) == 1)
        row = rows[0]; value = row.data
        need(type(value) is dict and value.get('schema') in {LEGACY_RECORD, APPEND_RECORD}
            and row.record_hash == admitted['base_evaluation_ref']['record_hash']
            and _same(value['scope'], scope) and _same(value['goal_snapshot'], snapshot(task))
            and value['original_query_ref'] == admitted['original_query_ref'])
        expected = row_values(value)
        need(all(name == 'data' or _same(getattr(row, name), actual) for name, actual in expected.items())
            and row.id == row.invocation_id == admitted['base_evaluation_ref']['id']
            and row.task_id == task.id and row.workspace_id == p.workspace_id
            and row.actor_id == head['actor_id'] and head['generation'] == row.generation
            and head['query_id'] == row.evaluation_query_id)
        if value['schema'] == APPEND_RECORD:
            job, call = _append(db, p, task, row, value, head)
        else:
            job, call = _legacy(db, p, task, selected, row, value)
        producer = value['producer']
        need(producer['job_id'] == job.id and producer['invocation_id'] == call.id
            and producer['actor_id'] == job.data['actor_id'] and producer['capability_id'] == call.capability_id
            == admitted['capability_id']
            and producer['workspace_id'] == p.workspace_id and producer['task_id'] == task.id
            and producer['scope_hash'] == digest(scope)
            and producer['capability_hash'] == call.data['capability_hash']
            == digest(call.data['capability_snapshot']) == admitted['capability_hash']
            and producer['input_hash'] == call.data['input_hash'] == digest(call.data['inputs'])
            == admitted['input_hash'] and _same(call.data['inputs'], admitted['inputs'])
            and producer['lease_token'] == call.data['lease_token']
            and producer['request_key_hash'] == digest(call.idempotency_key)
            and call.idempotency_key == row.request_key == job.idempotency_key)
        frozen_ref = _query_snapshot(db, p, task, row, value, call)
        goal_job, memory = _memory(db, p, task, admitted, head, row)
        return copy.deepcopy({'schema': SCHEMA, 'purpose': PURPOSE,
            'command_ref': {'id': original.id, 'record_hash': admitted['record_hash']},
            'head': head, 'base_evaluation_ref': admitted['base_evaluation_ref'],
            'producer': {key: producer[key] for key in ('job_id', 'invocation_id', 'actor_id',
                'capability_id', 'capability_hash', 'input_hash', 'request_key_hash')},
            'goal_check_job_id': goal_job.id, 'memory_id': memory.id,
            'report_hash': admitted['base_goal_check_report_hash'],
            'sdk_input_hash': producer['input_hash'], 'query_ref': frozen_ref,
            'historical_publication_verified': True, **contract.FLAGS})
