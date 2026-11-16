"""Read-only typed machine results; ACK is distinct from human/field truth."""
import copy
from datetime import datetime

from sqlalchemy import select

from . import models as m
from .contracts import digest
from .services import audit
from . import native_pixel_provenance_v250 as origin
from .native_pixel_receipts_v250 import receipt_projection

HUMAN_ACTION = 'native_pixel.human_receipt_saved_v250'
ASSOCIATION = 'task_entity_association_not_verified_coverage'


def _human_anchor(task, receipt):
    return {'schema': 'native-pixel-human-receipt-v250', 'task_id': task.id,
        'request_id': receipt['request_id'], 'receipt_hash': digest(receipt),
        'actor_id': receipt['actor'], 'step_id': receipt['step_id'],
        'activation_id': receipt.get('activation_id')}


def record_human_receipt(db, p, task, receipt):
    from .workflow_pixel_runs_v250 import runs_for_task
    runs = runs_for_task(db, p, task)
    if not runs and 'pixel_pair_snapshot' not in task.data:
        return None
    origin.need(receipt['actor'] == p.user_id and receipt in task.data['workflow_runtime']['receipts'])
    origin.need(not any(a.data.get('request_id') == receipt['request_id']
        for a in origin.audits(db, p, HUMAN_ACTION, task.id)))
    audit(db, p, HUMAN_ACTION, task.id, _human_anchor(task, receipt))
    return None


def human_receipts(db, p, context):
    task, business = context['task'], context['business']
    records = task.data['workflow_runtime']['receipts']
    issued = origin.audits(db, p, HUMAN_ACTION, task.id)
    steps = {step['id']: step for step in business['flow']['steps']}
    graph = None
    if 'graph_runtime' in task.data:
        from .workflow_graph import graph_for
        graph = graph_for(context['release'], business)
    values, used = [], set()
    for receipt in records:
        if 'invocation_id' in receipt:
            continue
        if receipt.get('execution_mode') not in {None, 'human_graph_result'}:
            continue
        anchors = [a for a in issued if a.data.get('request_id') == receipt['request_id']]
        origin.need(len(anchors) == 1 and anchors[0].actor_id == receipt['actor']
                    and anchors[0].data == _human_anchor(task, receipt))
        used.add(anchors[0].id)
        at = datetime.fromisoformat(receipt['at'].replace('Z', '+00:00'))
        origin.need(at.utcoffset() is not None)
        activation_id = receipt.get('activation_id')
        if activation_id is not None:
            origin.need(graph is not None)
            from .workflow_graph import node_for
            activation = task.data['graph_runtime']['activations'][activation_id]
            node = node_for(graph, activation)
            origin.need(node['kind'] == 'human' and node['actor_id'] == receipt['actor'])
            original = [a for a in origin.audits(db, p, 'workflow.graph.human_completed', task.id)
                        if a.data == {'activation_id': activation_id}]
            origin.need(len(original) == 1 and original[0].actor_id == receipt['actor'])
            role = node['role']; step = steps.get(receipt['step_id'])
            name = step['name'] if step is not None else role
            note = None; output = copy.deepcopy(receipt['output'])
        else:
            step = steps.get(receipt['step_id'])
            origin.need(step is not None and step['agent'] == '人工确认'
                        and receipt.get('role') == step['role'])
            role = step['role']; name = step['name']
            note = receipt['note']; output = None
            original = [a for a in origin.audits(db, p, 'workflow.step_completed', task.id)
                        if a.data.get('step') == receipt['step_id'] and a.actor_id == receipt['actor']]
            origin.need(bool(original))
        values.append({'step_id': receipt['step_id'], 'step_name': name,
            'actor_id': receipt['actor'], 'role': role, 'note': note,
            'completed_at': receipt['at'], 'receipt_hash': digest(receipt),
            'activation_id': activation_id, 'output': output})
    origin.need(used == {a.id for a in issued})
    return values


def verify_task_humans(db, p, task):
    from .workflow_pixel_runs_v250 import consumed_runs_for_task, checked_saved_run
    runs = consumed_runs_for_task(db, p, task)
    if not runs and 'pixel_pair_snapshot' not in task.data:
        return None
    if runs:
        context = checked_saved_run(db, p, runs[0])
    else:
        from .workspace_api import saved_execution
        current, release, business = saved_execution(db, p, task.id)
        context = {'task': current, 'release': release, 'business': business}
    human_receipts(db, p, context)
    return None


def result(db, p, run, *, data_only=False):
    from .workflow_pixel_runs_v250 import checked_saved_run, run_view, settlement_action, consumed_runs_for_task
    context = checked_saved_run(db, p, run)
    task = context['task']
    origin.need(len(task.data['entity_ids']) == 1)
    reception = receipt_projection(db, p, run)
    received = reception['state'] == 'received'
    public = copy.deepcopy(run_view(run))
    public['settlement_action'] = settlement_action(db, p, run)
    if data_only and not received:
        public['result'] = None
    observation = None
    event = None
    if reception['observation_id'] is not None:
        observation = origin.row(db, p, m.Observation, reception['observation_id'])
    if received:
        event = origin.row(db, p, m.Event, reception['event_id'])
    timing = {'run_started_at': origin.iso(context['input_audit'].created_at),
        'model_completed_at': origin.iso(context['result_audit'].created_at) if context.get('result_audit') else None,
        'registered_at': origin.iso(observation.created_at) if observation else None,
        'received_at': origin.iso(event.received_at) if event and event.received_at else None}
    humans = human_receipts(db, p, context)
    if run.id not in {item.id for item in consumed_runs_for_task(db, p, task)}:
        humans = []  # A retained failed attempt does not acquire a later run's review.
    return {'run': public, 'entity_id': task.data['entity_ids'][0], 'reception': reception,
        'human_receipts': humans, 'candidate_only': True,
        'not_authoritative': True, 'field_truth_verified': False,
        'data_values_available': received, 'association_basis': ASSOCIATION, 'timing': timing}


def assessment_records(db, p, request, tasks, *, limit=200):
    if 'imagery' not in request['required_modalities'] or not tasks:
        return [], [], False
    from .evidence_assessment import ref
    rows = list(db.scalars(select(m.WorkflowPixelRun).where(m.WorkflowPixelRun.workspace_id == p.workspace_id,
        m.WorkflowPixelRun.task_id.in_([task.id for task in tasks])).order_by(m.WorkflowPixelRun.id).limit(limit + 1)))
    records, references = [], []
    for run in rows[:limit]:
        entry = result(db, p, run, data_only=True)
        if entry['entity_id'] not in request['entity_ids']:
            continue
        received = entry['data_values_available']
        output = entry['run']['result']
        records.append({'key': 'WorkflowPixelRun:' + run.id, 'modality': 'imagery',
            'kind': 'pixel_comparison', 'name': next(t.title for t in tasks if t.id == run.task_id),
            'source_ref': ref(run), 'task_id': run.task_id,
            'task_title': next(t.title for t in tasks if t.id == run.task_id),
            'entity_ids': [entry['entity_id']], 'association_basis': ASSOCIATION,
            'native_observation_id': entry['reception']['observation_id'],
            'status': run.status, 'integrity_verified': received,
            'integrity_issue': entry['reception']['integrity_issue'] if not received else None,
            'source_times': [{'value': None, 'basis': 'acquisition_time_unknown'}],
            'source_time_verified': False, 'source_sha256': [run.data['sources'][s]['sha256'] for s in ('before', 'after')],
            'quality_gaps': ['像素坐标，无地理参考；采集时刻与领域质量未核实'],
            'review_state': 'confirmed' if entry['human_receipts'] else 'unreviewed',
            'metrics': {key: output[key] for key in ('candidate_pixels', 'common_valid_pixels', 'unknown_pixels')}
                       if received else {}, 'domain_quality_verified': False})
        references.append(ref(run))
    return records, references, len(rows) > limit


def legacy_receipt_view(db, p, observation):
    context = origin.checked_observation(db, p, observation)
    entry = result(db, p, context['run'], data_only=True)
    packet = context['packet']
    return {'id': observation.id, 'created_at': origin.iso(observation.created_at),
        'received_day': packet['registration_day'], 'entity_id': observation.entity_id,
        'integrity_error': entry['reception']['integrity_issue'],
        'data_received': entry['data_values_available'], 'native_pixel_result_v250': entry,
        'record': {'schema': 'native-pixel-receipt-record-v250',
            'source_key': 'WorkflowPixelRun:' + context['run'].id, 'name': '本地像素变化候选成果',
            'review_state': 'confirmed' if entry['human_receipts'] else 'unreviewed',
            'date_basis': 'machine_result_registered', 'candidate_only': True,
            'entity_state_updated': False, 'coordinate_space': 'pixel'}}
