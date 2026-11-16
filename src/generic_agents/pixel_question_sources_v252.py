"""Bounded as-of pixel sources; no inference, ACK, mutation or ordinary frame.

The caller supplies a fresh REPEATABLE READ READ ONLY Session for capture.
Historical checks deliberately do not recapture current inventory/heads.
"""
import copy
import hashlib

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, text, func, cast
from sqlalchemy.dialects.postgresql import JSONPATH

from . import models as m
from .contracts import digest
from .services import scoped
from . import native_pixel_provenance_v250 as origin
from . import native_pixel_provider_v250 as provider
from .native_pixel_receipts_v250 import receipt_projection
from .pixel_question_source_contract_v252 import (SCHEMA, FAMILY, ASSOCIATION, HUMAN_BASIS,
    MAX_RECORDS, METRICS, ERROR, LIMIT_ERROR, need, integer, uid, seal_record, seal_snapshot,
    validate_snapshot, compute)

SOURCE_ACTION = 'pixel_question.source_frozen_v252'
SOURCE_AUDIT_KEYS = {'schema', 'query_id', 'workspace_id', 'instance_id', 'actor_id',
    'authorization_release_id', 'authorization_configuration_hash', 'tool_id', 'tool_hash',
    'plan_hash', 'creation_hash', 'context_hash', 'execution_job_id', 'source_snapshot_hash',
    'source_revision_hash', 'inventory_hash', 'captured_at', 'complete', 'run_count'}


def _ref(audit):
    return {'id': audit.id, 'action': audit.action, 'object_id': audit.object_id,
        'actor_id': audit.actor_id, 'data_hash': digest(audit.data), 'created_at': origin.iso(audit.created_at)}


def _anchor(db, p, action, object_id):
    values = origin.audits(db, p, action, object_id)
    need(len(values) == 1)
    return values[0]


def _check_ref(db, p, value):
    row = origin.row(db, p, m.Audit, value['id'])
    need(_ref(row) == value)
    rows = origin.audits(db, p, value['action'], value['object_id'])
    need(any(r.id == row.id for r in rows))
    return row


def _human(db, p, context, public):
    """Only hashes/meta of a completed receipt enter the source snapshot."""
    task = context['task']
    raw = [r for r in task.data['workflow_runtime']['receipts'] if digest(r) == public['receipt_hash']]
    need(len(raw) == 1)
    receipt = raw[0]
    saved = [a for a in origin.audits(db, p, provider.HUMAN_ACTION, task.id)
             if a.data.get('request_id') == receipt['request_id']]
    need(len(saved) == 1 and saved[0].actor_id == receipt['actor']
         and saved[0].data == provider._human_anchor(task, receipt))
    if public['activation_id'] is not None:
        completed = [a for a in origin.audits(db, p, 'workflow.graph.human_completed', task.id)
            if a.actor_id == public['actor_id'] and a.data == {'activation_id': public['activation_id']}]
        need(len(completed) == 1)
        original = completed[0]
    else:
        # Compact completion has no request_id in its legacy Audit. Match its
        # exact real step/actor/branch target and consume one audit per visit;
        # never pretend receipt.at equals the separate audit database clock.
        steps = context['business']['flow']['steps']
        index = next(i for i, s in enumerate(steps) if s['id'] == receipt['step_id'])
        step = steps[index]
        following = step.get('elseId', 'end') if receipt.get('branch') is False else step.get('nextId', 'auto')
        if following == 'auto': following = steps[index + 1]['id'] if index + 1 < len(steps) else 'end'
        expected = {'step': receipt['step_id'], 'next': following}
        completed = sorted([a for a in origin.audits(db, p, 'workflow.step_completed', task.id)
            if a.actor_id == receipt['actor'] and a.data == expected], key=lambda a: (a.created_at, a.id))
        peers = []
        for item in task.data['workflow_runtime']['receipts']:
            if item.get('invocation_id') or item.get('execution_mode') is not None: continue
            if item.get('step_id') == receipt['step_id'] and item.get('actor') == receipt['actor']:
                peer_next = step.get('elseId', 'end') if item.get('branch') is False else step.get('nextId', 'auto')
                if peer_next == 'auto': peer_next = steps[index + 1]['id'] if index + 1 < len(steps) else 'end'
                if peer_next == following: peers.append(item)
        need(len(completed) == len(peers) and receipt in peers)
        original = completed[peers.index(receipt)]
    note, output = public['note'], public['output']
    return {k: copy.deepcopy(public[k]) for k in ('receipt_hash', 'step_id', 'step_name', 'actor_id',
        'role', 'completed_at', 'activation_id')} | {
        'note_sha256': hashlib.sha256(note.encode('utf-8')).hexdigest() if note is not None else None,
        'output_sha256': digest(output) if output is not None else None,
        'saved_audit': _ref(saved[0]), 'completion_audit': _ref(original)}


def _filter_task(statement, task_id, entity_id):
    if task_id is not None:
        statement = statement.where(m.Task.id == task_id)
    if entity_id is not None:
        statement = statement.where(or_(m.Task.data['entity_ids'].contains([entity_id]),
            m.Task.data['object_snapshots'].contains([{'id': entity_id}])))
    return statement


def _inventory(db, p, instance_id, task_id, entity_id, limit):
    """Independent queries retain orphan producer proofs instead of inner joins."""
    from .workflow_pixel_runs_v250 import INPUT_ACTION, RESULT_ACTION
    tasks = _filter_task(select(m.Task.id).where(m.Task.workspace_id == p.workspace_id,
        m.Task.data['instance_id'].as_string() == instance_id), task_id, entity_id).correlate(None)
    # A broken linked Task cannot make a physically scoped producer disappear.
    invalid_task = or_(m.Task.id.is_(None), m.Task.workspace_id != p.workspace_id,
                       m.Task.data['instance_id'].as_string().is_(None),
                       m.Task.data['instance_id'].as_string() != instance_id)
    physical = select(m.WorkflowPixelRun).outerjoin(m.Task, m.Task.id == m.WorkflowPixelRun.task_id).where(
        m.WorkflowPixelRun.workspace_id == p.workspace_id,
        or_(m.WorkflowPixelRun.data['instance_id'].as_string() == instance_id, m.Task.id.in_(tasks)))
    if task_id is not None: physical = physical.where(m.WorkflowPixelRun.task_id == task_id)
    if entity_id is not None:
        physical = physical.where(or_(invalid_task, m.Task.data['entity_ids'].contains([entity_id]),
            m.Task.data['object_snapshots'].contains([{'id': entity_id}])))
    rows = list(db.scalars(physical.order_by(m.WorkflowPixelRun.id).limit(limit + 1)
                           .execution_options(populate_existing=True)))
    need(len(rows) <= limit, LIMIT_ERROR)
    run_ids = [r.id for r in rows]
    inputs = select(m.Audit).outerjoin(m.Task, m.Task.id == m.Audit.data['task_id'].as_string()).where(
        m.Audit.workspace_id == p.workspace_id, m.Audit.action == INPUT_ACTION,
        or_(m.Audit.data['instance_id'].as_string() == instance_id, m.Task.id.in_(tasks), m.Audit.object_id.in_(run_ids)))
    if task_id is not None:
        inputs = inputs.where(or_(m.Audit.data['task_id'].as_string() == task_id, m.Audit.object_id.in_(run_ids)))
    if entity_id is not None:
        inputs = inputs.where(or_(invalid_task, m.Task.data['entity_ids'].contains([entity_id]),
            m.Task.data['object_snapshots'].contains([{'id': entity_id}]), m.Audit.object_id.in_(run_ids)))
    proofs = list(db.scalars(inputs.order_by(m.Audit.id).limit(limit + 1)))
    need(len(proofs) <= limit, LIMIT_ERROR)
    need(len(proofs) == len(rows) and {a.object_id for a in proofs} == set(run_ids))
    for run in rows:
        selected = [a for a in proofs if a.object_id == run.id]
        need(len(selected) == 1 and selected[0].data.get('instance_id') == instance_id
             and selected[0].data.get('task_id') == run.task_id)
    # Result/issued audits with a missing Run or missing input proof must not
    # vanish. Result proof has task_id; input and issued additionally pin the
    # original instance. Do not use a successful join as existence evidence.
    for action, identity in ((RESULT_ACTION, m.Audit.object_id),
                              (origin.ISSUED_ACTION, m.Audit.data['run_id'].as_string())):
        statement = select(m.Audit).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == action,
            or_(m.Audit.data['task_id'].as_string().in_(tasks), identity.in_(run_ids),
                and_(m.Audit.data['instance_id'].as_string() == instance_id,
                     m.Audit.data['task_id'].as_string() == task_id) if task_id is not None else
                and_(m.Audit.data['instance_id'].as_string() == instance_id,
                     m.Audit.data['entity_id'].as_string() == entity_id) if entity_id is not None else
                m.Audit.data['instance_id'].as_string() == instance_id))
        values = list(db.scalars(statement.order_by(m.Audit.id).limit(limit + 1)))
        need(len(values) <= limit, LIMIT_ERROR)
        identities = [a.object_id if action == RESULT_ACTION else a.data.get('run_id') for a in values]
        need(len(set(identities)) == len(identities) and set(identities) <= set(run_ids))
    # Native observations/fixed source are a fourth reverse inventory, even
    # when both a mutable marker and the issued Audit have been removed.
    observation_query = select(m.Observation).outerjoin(m.Capability, m.Capability.id == m.Observation.source_id).where(
        m.Observation.workspace_id == p.workspace_id,
        or_(m.Observation.data[origin.PACKET]['instance_id'].as_string() == instance_id,
            and_(m.Capability.workspace_id == p.workspace_id, m.Capability.namespace == 'platform_native_pixel_results',
                 m.Capability.data['specification']['managed_instance'].as_string() == instance_id)))
    if task_id is not None:
        observation_query = observation_query.where(m.Observation.data[origin.PACKET]['task_id'].as_string() == task_id)
    if entity_id is not None: observation_query = observation_query.where(m.Observation.entity_id == entity_id)
    observations = list(db.scalars(observation_query.order_by(m.Observation.id).limit(limit + 1)))
    need(len(observations) <= limit, LIMIT_ERROR)
    for observation in observations:
        context = origin.checked_observation(db, p, observation)
        need(context['run'].id in run_ids)
    from .workflow_pixel_runs_v250 import consumed_runs_for_task
    referenced = select(m.Task).where(m.Task.id.in_(tasks),
        func.jsonb_path_exists(m.Task.data, cast('$.**.pixel_run_id', JSONPATH))).order_by(m.Task.id).limit(limit + 1)
    references = list(db.scalars(referenced))
    need(len(references) <= limit, LIMIT_ERROR)
    for task in references:
        need({r.id for r in consumed_runs_for_task(db, p, task)} <= set(run_ids))
    return rows


def _record(db, p, run, consumed, humans):
    from .workflow_pixel_runs_v250 import checked_saved_run, checked_run, _completed
    context = checked_saved_run(db, p, run)
    if run.id in consumed: context = _completed(db, p, context)
    task, release = context['task'], context['release']
    need(len(task.data['entity_ids']) == 1)
    reception = receipt_projection(db, p, run)
    need(reception['state'] != 'blocked_integrity')
    native = None
    if reception['observation_id'] is not None:
        native = origin.checked_observation(db, p, origin.row(db, p, m.Observation, reception['observation_id']))
    if native is not None: context = checked_run(db, p, run)
    package = origin.row(db, p, m.Capability, release.package_id)
    memory = origin.row(db, p, m.Memory, reception['memory_id']) if reception['memory_id'] else None
    result = run.data['result'] if reception['state'] == 'received' else None
    issued = native['issued_audit'] if native else None
    source_audit = _anchor(db, p, origin.SOURCE_ACTION, native['source'].id) if native else None
    anchors = {'input': _ref(context['input_audit']),
        'result': _ref(context['result_audit']) if context['result_audit'] else None,
        'completion': _ref(context['completion_audit']) if context.get('completion_audit') else None,
        'settlement': _ref(context['settlement_audit']) if context.get('settlement_audit') else None,
        'issued': _ref(issued) if issued else None,
        'derived_source': _ref(source_audit) if source_audit else None}
    return seal_record({'run_id': run.id, 'task_id': run.task_id, 'entity_id': task.data['entity_ids'][0],
        'producer_release_id': release.id, 'producer_configuration_hash': release.configuration_hash,
        'producer_package_id': release.package_id, 'producer_package_hash': package.content_hash,
        'capability_id': run.capability_id, 'capability_hash': run.data['capability_hash'], 'source_family': FAMILY,
        'invocation_id': run.invocation_id, 'job_id': run.job_id, 'run_status': run.status,
        'producer_job_status': context['job'].status, 'consumed_by_task': run.id in consumed,
        'request_hash': run.data['request_hash'], 'sources_hash': run.data['sources_hash'],
        'model_trace_hash': run.data['model_trace_hash'], 'result_hash': run.data['result_hash'],
        'sources': copy.deepcopy(run.data['sources']), 'source_anchors': anchors,
        'reception': {'state': reception['state'], 'observation_id': reception['observation_id'], 'event_id': reception['event_id'],
            'memory': {'id': memory.id, 'version': memory.version, 'data_hash': digest(memory.data)} if memory else None,
            'packet_hash': digest(native['packet']) if native else None,
            'event_base_hash': digest(native['base']) if native else None,
            'receipt_hash': reception['receipt']['receipt_hash'] if reception['receipt'] else None},
        'timing': {'run_started_at': origin.iso(context['input_audit'].created_at),
            'model_completed_at': origin.iso(context['result_audit'].created_at) if context['result_audit'] else None,
            'registered_at': origin.iso(native['observation'].created_at) if native else None,
            'registration_day': native['packet']['registration_day'] if native else None,
            'received_at': origin.iso(native['event'].received_at) if memory else None, 'observed_at': None},
        'metrics': {k: result[k] for k in METRICS} if result else None,
        'artifacts': copy.deepcopy(result['artifacts']) if result else None,
        'human_receipts': humans if run.id in consumed else [], 'association_basis': ASSOCIATION})


def capture(db, p, instance_id: str, *, task_id: str | None = None,
            entity_id: str | None = None, limit: int = 1000) -> dict:
    """SELECT-only complete inventory, bounded before any content filtering."""
    resolved_scope = False
    try:
        need(integer(limit, 1, MAX_RECORDS), LIMIT_ERROR)
        need(not db.new and not db.dirty and not db.deleted)
        need(db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
             and db.scalar(text('SHOW transaction_read_only')) == 'on',
             '来源捕获需要独立的一致只读事务，未以分页替代完整库存')
        instance = scoped(db, m.DomainInstance, instance_id, p)
        if task_id is not None:
            task = scoped(db, m.Task, task_id, p)
            need(task.data.get('instance_id') == instance_id)
        if entity_id is not None:
            entity = scoped(db, m.Entity, entity_id, p)
            need(entity.namespace == instance.namespace)
            if task_id is not None: need(entity_id in task.data.get('entity_ids', []))
        resolved_scope = True
        rows = _inventory(db, p, instance_id, task_id, entity_id, limit)
        from .workflow_pixel_runs_v250 import checked_saved_run, consumed_runs_for_task
        by_task = {}
        records = []
        for run in rows:
            context = checked_saved_run(db, p, run)
            task = context['task']
            need(run.data['instance_id'] == instance_id and task.data['instance_id'] == instance_id
                 and (task_id is None or task.id == task_id)
                 and (entity_id is None or task.data['entity_ids'] == [entity_id]))
            if task.id not in by_task:
                consumed = {r.id for r in consumed_runs_for_task(db, p, task)}
                public = provider.human_receipts(db, p, context)
                humans = [_human(db, p, context, h) for h in public]
                by_task[task.id] = consumed, humans
            records.append(_record(db, p, run, *by_task[task.id]))
        body = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'instance_id': instance_id,
            'captured_by': p.user_id, 'scope': {'task_id': task_id, 'entity_id': entity_id},
            'captured_at': origin.iso(db.scalar(text('SELECT CURRENT_TIMESTAMP'))),
            'consistency': 'single_repeatable_read_view',
            'inventory': {'complete': True, 'limit': limit, 'run_ids': [r.id for r in rows]},
            'records': records, 'time_basis': 'machine_result_registered', 'time_zone': 'Asia/Shanghai',
            'human_basis': HUMAN_BASIS, 'candidate_only': True, 'not_authoritative': True,
            'field_truth_verified': False, 'source_time_verified': False}
        return seal_snapshot(body)
    except HTTPException as error:
        if resolved_scope and error.status_code in {404, 422}: need(False)
        raise
    except (KeyError, ValueError, TypeError, AttributeError, StopIteration, OSError):
        need(False)


def _verify_human(db, p, context, frozen):
    task = context['task']
    records = [r for r in task.data['workflow_runtime']['receipts'] if digest(r) == frozen['receipt_hash']]
    need(len(records) == 1)
    raw = records[0]
    saved = _check_ref(db, p, frozen['saved_audit'])
    completed = _check_ref(db, p, frozen['completion_audit'])
    saved_peers = [a for a in origin.audits(db, p, provider.HUMAN_ACTION, task.id)
                   if a.data.get('request_id') == raw['request_id']]
    need(len(saved_peers) == 1 and saved_peers[0].id == saved.id)
    need(saved.object_id == completed.object_id == task.id and saved.actor_id == completed.actor_id == raw['actor']
         and saved.data == provider._human_anchor(task, raw)
         and frozen['actor_id'] == raw['actor'] and frozen['step_id'] == raw['step_id']
         and frozen['completed_at'] == raw['at'] and frozen['activation_id'] == raw.get('activation_id'))
    if frozen['activation_id'] is not None:
        from .workflow_graph import graph_for, node_for
        graph = graph_for(context['release'], context['business'])
        activation = task.data['graph_runtime']['activations'][frozen['activation_id']]
        node = node_for(graph, activation)
        need(node['kind'] == 'human' and node['actor_id'] == raw['actor'] and frozen['role'] == node['role']
             and completed.data == {'activation_id': frozen['activation_id']})
        need(len([a for a in origin.audits(db, p, 'workflow.graph.human_completed', task.id)
                  if a.data == completed.data]) == 1)
        note, output = None, raw['output']
        step = next((s for s in context['business']['flow']['steps'] if s['id'] == raw['step_id']), None)
        name = step['name'] if step else node['role']
    else:
        steps = context['business']['flow']['steps']
        index = next(i for i, s in enumerate(steps) if s['id'] == raw['step_id'])
        step = steps[index]
        following = step.get('elseId', 'end') if raw.get('branch') is False else step.get('nextId', 'auto')
        if following == 'auto': following = steps[index + 1]['id'] if index + 1 < len(steps) else 'end'
        need(step['agent'] == '人工确认' and frozen['role'] == raw['role'] == step['role']
             and completed.data == {'step': raw['step_id'], 'next': following})
        note, output, name = raw['note'], None, step['name']
    need(frozen['step_name'] == name and frozen['note_sha256'] == (
        hashlib.sha256(note.encode('utf-8')).hexdigest() if note is not None else None)
        and frozen['output_sha256'] == (digest(output) if output is not None else None))


def _verify_record(db, p, record, instance_id):
    from .workflow_pixel_runs_v250 import checked_saved_run, _completed
    run = origin.row(db, p, m.WorkflowPixelRun, record['run_id'])
    context = checked_saved_run(db, p, run)
    if record['consumed_by_task']:
        context = _completed(db, p, context)
        need(record['source_anchors']['completion'] == _ref(context['completion_audit']))
    task, release = context['task'], context['release']
    package = origin.row(db, p, m.Capability, release.package_id)
    need(run.task_id == record['task_id'] and task.data['instance_id'] == run.data['instance_id'] == instance_id
         and task.data['entity_ids'] == [record['entity_id']] and run.job_id == record['job_id']
         and run.invocation_id == record['invocation_id'] and run.capability_id == record['capability_id']
         and release.id == record['producer_release_id'] and release.configuration_hash == record['producer_configuration_hash']
         and package.id == record['producer_package_id'] and package.content_hash == record['producer_package_hash']
         and run.data['capability_hash'] == record['capability_hash']
         and run.data['request_hash'] == record['request_hash'] and run.data['sources_hash'] == record['sources_hash']
         and run.data['model_trace_hash'] == record['model_trace_hash'] and run.data['sources'] == record['sources'])
    for ref in record['source_anchors'].values():
        if ref is not None: _check_ref(db, p, ref)
    need(_ref(context['input_audit']) == record['source_anchors']['input'])
    if record['result_hash'] is not None:
        need(run.data['result_hash'] == record['result_hash'] and context['result_audit'] is not None
             and _ref(context['result_audit']) == record['source_anchors']['result'])
    reception = record['reception']
    if reception['observation_id'] is not None:
        observation = origin.row(db, p, m.Observation, reception['observation_id'])
        current = origin.checked_observation(db, p, observation)
        need(current['run'].id == run.id and current['event'].id == reception['event_id']
             and digest(current['packet']) == reception['packet_hash'] and digest(current['base']) == reception['event_base_hash']
             and _ref(current['issued_audit']) == record['source_anchors']['issued']
             and origin.iso(observation.created_at) == record['timing']['registered_at']
             and current['packet']['registration_day'] == record['timing']['registration_day'])
    if reception['state'] == 'received':
        actual = receipt_projection(db, p, run)
        need(actual['state'] == 'received' and actual['receipt']['receipt_hash'] == reception['receipt_hash']
             and actual['memory_id'] == reception['memory']['id'])
        memory = origin.row(db, p, m.Memory, actual['memory_id'])
        need(memory.version == reception['memory']['version'] and digest(memory.data) == reception['memory']['data_hash']
             and {k: run.data['result'][k] for k in METRICS} == record['metrics']
             and run.data['result']['artifacts'] == record['artifacts'])
        event = origin.row(db, p, m.Event, actual['event_id'])
        need(origin.iso(event.received_at) == record['timing']['received_at'])
    for human in record['human_receipts']:
        _verify_human(db, p, context, human)
    # Null then-absent anchors and lifecycle state are intentionally not
    # compared to current values: normal ACK/retry/human progress is allowed.


def verify_frozen_records(db, p, snapshot):
    """Content/record cold checks only; a caller must verify its own saved-query Audit.

    This additive helper grants no V252 or sibling query authority. The old
    saved-query verifier, wire, hash and computation remain unchanged.
    """
    with db.no_autoflush:
        validate_snapshot(snapshot)
        need(snapshot['workspace_id'] == p.workspace_id and snapshot['captured_by'] == p.user_id)
        for record in snapshot['records']:
            _verify_record(db, p, record, snapshot['instance_id'])


def _verify_saved_snapshot(db, p, question_run, snapshot):
    try:
        validate_snapshot(snapshot)
        need(isinstance(question_run, m.DataRun) and question_run.workspace_id == p.workspace_id
             and question_run.owner_id == p.user_id and snapshot['workspace_id'] == p.workspace_id
             and snapshot['captured_by'] == p.user_id)
        row = origin.row(db, p, m.DataRun, question_run.id)
        need(row.owner_id == p.user_id)
        state = row.data['pixel_question_v252']
        need(digest(state['source_snapshot']) == digest(snapshot))
        from .pixel_question_service_v252 import source_audit_expected
        anchors = origin.audits(db, p, SOURCE_ACTION, row.id)
        need(len(anchors) == 1 and anchors[0].id == state['source_audit_id']
             and anchors[0].actor_id == p.user_id and set(anchors[0].data) == SOURCE_AUDIT_KEYS
             and digest(anchors[0].data) == digest(source_audit_expected(row, state))
             and anchors[0].data['source_snapshot_hash'] == snapshot['snapshot_hash']
             and anchors[0].data['source_revision_hash'] == snapshot['revision_hash']
             and anchors[0].data['inventory_hash'] == snapshot['inventory']['inventory_hash']
             and anchors[0].data['instance_id'] == snapshot['instance_id'])
        for record in snapshot['records']:
            _verify_record(db, p, record, snapshot['instance_id'])
    except HTTPException as error:
        if error.status_code == 403: raise
        need(False)
    except (KeyError, ValueError, TypeError, AttributeError, StopIteration, OSError):
        need(False)


def verify_saved_snapshot(db, p, question_run, snapshot: dict) -> None:
    # Source verification must not flush an unrelated caller's pending writes.
    with db.no_autoflush:
        return _verify_saved_snapshot(db, p, question_run, snapshot)


def verify_current_snapshot(db, p, snapshot: dict) -> None:
    """Explicit diagnostic only. Normal question execution never calls this."""
    validate_snapshot(snapshot)
    need(snapshot['workspace_id'] == p.workspace_id and snapshot['captured_by'] == p.user_id)
    fresh = capture(db, p, snapshot['instance_id'], task_id=snapshot['scope']['task_id'],
                    entity_id=snapshot['scope']['entity_id'], limit=snapshot['inventory']['limit'])
    need(fresh['revision_hash'] == snapshot['revision_hash'],
         'source_changed：当前已有新的合法处理或人工记录；原问题的查询时快照未改写')
