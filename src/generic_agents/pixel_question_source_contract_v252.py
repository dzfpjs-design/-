"""Closed, bounded pixel-question snapshots and deterministic local arithmetic.

This module neither reads sources nor grants invocation/receipt authority. A
matching digest is only a content check; the db-bound caller verifies audits.
"""
import copy
import hashlib
import json
import re
from datetime import date, datetime
from functools import wraps
from uuid import UUID

from fastapi import HTTPException

from .contracts import digest

SCHEMA = 'pixel-question-source-snapshot-v252'
COMPUTATION = 'pixel-question-computation-v252'
FAMILY = 'workflow_pixel_change_candidate_v250'
ASSOCIATION = 'task_entity_association_not_verified_coverage'
HUMAN_BASIS = 'completed_task_receipts_at_query_snapshot'
MAX_RECORDS = 1000
MAX_BYTES = 16 * 1024 * 1024
METRICS = ('candidate_pixels', 'common_valid_pixels', 'unknown_pixels')
KINDS = {'valid_mask', 'probability_data', 'probability_preview', 'candidate_overlay', 'binary_mask', 'manifest'}
ERROR = '本次问题采用的像素成果或独立来源无法核对；原问题与记录保留'
LIMIT_ERROR = '所选范围超过完整查询上限，请缩小任务或对象范围；未输出部分总数'
AUDIT_KEYS = {'id', 'action', 'object_id', 'actor_id', 'data_hash', 'created_at'}
HUMAN_KEYS = {'receipt_hash', 'step_id', 'step_name', 'actor_id', 'role', 'completed_at',
              'activation_id', 'note_sha256', 'output_sha256', 'saved_audit', 'completion_audit'}
RECORD_KEYS = {'run_id', 'task_id', 'entity_id', 'producer_release_id', 'producer_configuration_hash',
    'producer_package_id', 'producer_package_hash', 'capability_id', 'capability_hash', 'source_family',
    'invocation_id', 'job_id', 'run_status', 'producer_job_status', 'consumed_by_task', 'request_hash',
    'sources_hash', 'model_trace_hash', 'result_hash', 'sources', 'source_anchors', 'reception',
    'timing', 'metrics', 'artifacts', 'human_receipts', 'association_basis', 'record_hash'}
SNAPSHOT_KEYS = {'schema', 'workspace_id', 'instance_id', 'captured_by', 'scope', 'captured_at',
    'consistency', 'inventory', 'records', 'time_basis', 'time_zone', 'human_basis', 'candidate_only',
    'not_authoritative', 'field_truth_verified', 'source_time_verified', 'revision_hash', 'snapshot_hash'}


def need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def closed(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError, UnicodeError, RecursionError):
            need(False)
    return call


def keys(value, expected):
    need(type(value) is dict and set(value) == set(expected))


def uid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def sha(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def aware(value):
    try:
        return type(value) is str and datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is not None
    except (ValueError, TypeError):
        return False


def day(value):
    try:
        return type(value) is str and date.fromisoformat(value).isoformat() == value
    except (ValueError, TypeError):
        return False


def integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        need(False)


def audit_ref_valid(value):
    keys(value, AUDIT_KEYS)
    need(uid(value['id']) and uid(value['actor_id']) and type(value['object_id']) is str
         and 0 < len(value['object_id']) <= 100 and type(value['action']) is str
         and 0 < len(value['action']) <= 100 and sha(value['data_hash']) and aware(value['created_at']))


def image_valid(value):
    keys(value, {'id', 'name', 'sha256', 'media_type', 'size_bytes', 'width', 'height', 'created_at'})
    need(uid(value['id']) and type(value['name']) is str and len(value['name']) <= 250
         and sha(value['sha256']) and value['media_type'] == 'image/png'
         and integer(value['size_bytes'], 1, 20 * 1024 * 1024)
         and integer(value['width'], 32) and integer(value['height'], 32) and aware(value['created_at']))


def human_valid(value):
    keys(value, HUMAN_KEYS)
    need(sha(value['receipt_hash']) and uid(value['actor_id']) and aware(value['completed_at'])
         and (value['activation_id'] is None or type(value['activation_id']) is str)
         and all(type(value[k]) is str and len(value[k]) <= 2000 for k in ('step_id', 'step_name', 'role'))
         and all(value[k] is None or sha(value[k]) for k in ('note_sha256', 'output_sha256')))
    for key in ('saved_audit', 'completion_audit'):
        audit_ref_valid(value[key])
        need(value[key]['actor_id'] == value['actor_id'])
    need(value['saved_audit']['action'] == 'native_pixel.human_receipt_saved_v250'
         and value['completion_audit']['action'] == ('workflow.graph.human_completed'
             if value['activation_id'] is not None else 'workflow.step_completed'))


def record_valid(record):
    keys(record, RECORD_KEYS)
    need(all(uid(record[k]) for k in ('run_id', 'task_id', 'entity_id', 'producer_release_id',
        'producer_package_id', 'capability_id', 'invocation_id', 'job_id'))
        and all(sha(record[k]) for k in ('producer_configuration_hash', 'producer_package_hash',
            'capability_hash', 'request_hash', 'sources_hash', 'model_trace_hash', 'record_hash'))
        and (record['result_hash'] is None or sha(record['result_hash']))
        and record['source_family'] == FAMILY and record['association_basis'] == ASSOCIATION
        and type(record['consumed_by_task']) is bool
        and record['run_status'] in {'running', 'succeeded', 'failed', 'cancelled'}
        and record['producer_job_status'] in {'queued', 'running', 'succeeded', 'failed', 'cancelled'})
    keys(record['sources'], {'before', 'after'})
    for image in record['sources'].values(): image_valid(image)
    before, after = record['sources']['before'], record['sources']['after']
    need(before['id'] != after['id'] and before['sha256'] != after['sha256']
         and before['width'] == after['width'] and before['height'] == after['height']
         and digest(record['sources']) == record['sources_hash'])
    keys(record['source_anchors'], {'input', 'result', 'completion', 'settlement', 'issued', 'derived_source'})
    for key, ref in record['source_anchors'].items():
        need(ref is not None or key != 'input')
        if ref is not None: audit_ref_valid(ref)
    actions = {'input': 'workflow_pixel.input_frozen_v250', 'result': 'workflow_pixel.result_frozen_v250',
        'settlement': 'workflow_pixel.result_settled_v250', 'issued': 'native_pixel.receipt_issued_v250',
        'derived_source': 'native_pixel.source_derived_v250'}
    for key, action in actions.items():
        ref = record['source_anchors'][key]
        if ref is not None:
            need(ref['action'] == action)
            if key in {'input', 'result', 'settlement'}: need(ref['object_id'] == record['run_id'])
    completion = record['source_anchors']['completion']
    need(record['consumed_by_task'] == (completion is not None)
         and (record['result_hash'] is not None) == (record['source_anchors']['result'] is not None))
    if completion is not None:
        need(completion['action'] in {'workflow.ability_completed', 'workflow.graph.node_completed'}
             and completion['object_id'] == record['task_id'])
    reception = record['reception']
    keys(reception, {'state', 'observation_id', 'event_id', 'memory', 'packet_hash', 'event_base_hash', 'receipt_hash'})
    need(reception['state'] in {'waiting_data', 'received', 'producer_failed'})
    for key in ('observation_id', 'event_id'):
        need(reception[key] is None or uid(reception[key]))
    for key in ('packet_hash', 'event_base_hash', 'receipt_hash'):
        need(reception[key] is None or sha(reception[key]))
    memory = reception['memory']
    if memory is not None:
        keys(memory, {'id', 'version', 'data_hash'})
        need(uid(memory['id']) and integer(memory['version'], 1) and sha(memory['data_hash']))
    timing = record['timing']
    keys(timing, {'run_started_at', 'model_completed_at', 'registered_at', 'registration_day', 'received_at', 'observed_at'})
    need(aware(timing['run_started_at']) and timing['observed_at'] is None)
    for key in ('model_completed_at', 'registered_at', 'received_at'):
        need(timing[key] is None or aware(timing[key]))
    need(timing['registration_day'] is None or day(timing['registration_day']))
    need(timing['run_started_at'] == record['source_anchors']['input']['created_at']
         and timing['model_completed_at'] == (record['source_anchors']['result']['created_at']
             if record['source_anchors']['result'] is not None else None))
    registered = timing['registered_at'] is not None
    need(registered == (timing['registration_day'] is not None)
         and registered == (reception['observation_id'] is not None)
         and registered == (reception['event_id'] is not None)
         and registered == (record['source_anchors']['issued'] is not None))
    need(registered == (reception['packet_hash'] is not None)
         and registered == (reception['event_base_hash'] is not None)
         and registered == (record['source_anchors']['derived_source'] is not None))
    if registered:
        need(record['source_anchors']['issued']['object_id'] == reception['observation_id'])
    received = reception['state'] == 'received'
    need(received == (record['metrics'] is not None) == (record['artifacts'] is not None)
         and received == (memory is not None) == (reception['receipt_hash'] is not None)
         and received == (timing['received_at'] is not None))
    if received:
        need(registered and record['consumed_by_task'] and record['result_hash'] is not None
             and all(record['source_anchors'][k] is not None for k in ('result', 'completion')))
        keys(record['metrics'], METRICS)
        need(all(integer(record['metrics'][k], 0, 16000000) for k in METRICS)
             and record['metrics']['candidate_pixels'] <= record['metrics']['common_valid_pixels']
             and record['metrics']['common_valid_pixels'] + record['metrics']['unknown_pixels'] == before['width'] * before['height'])
        artifacts = record['artifacts']
        need(type(artifacts) is list and len(artifacts) == 6 and {a.get('kind') for a in artifacts if type(a) is dict} == KINDS)
        for artifact in artifacts:
            keys(artifact, {'kind', 'evidence_id', 'sha256', 'size_bytes', 'media_type'})
            need(uid(artifact['evidence_id']) and sha(artifact['sha256']) and integer(artifact['size_bytes'], 1)
                 and type(artifact['media_type']) is str and len(artifact['media_type']) <= 150)
    if reception['state'] == 'producer_failed':
        need(record['producer_job_status'] == 'failed' and not registered)
    humans = record['human_receipts']
    need(type(humans) is list and len(humans) <= 1000 and (not humans or record['consumed_by_task']))
    for human in humans: human_valid(human)
    need(len({h['receipt_hash'] for h in humans}) == len(humans))
    need(record['record_hash'] == digest({k: v for k, v in record.items() if k != 'record_hash'}))


def inventory_hash(scope, records):
    return digest({'scope': scope, 'records': [{'run_id': r['run_id'], 'task_id': r['task_id'],
        'anchors': r['source_anchors']} for r in records]})


def seal_record(record):
    value = copy.deepcopy(record)
    value.pop('record_hash', None)
    value['record_hash'] = digest(value)
    return value


def seal_snapshot(snapshot):
    value = copy.deepcopy(snapshot)
    value.pop('revision_hash', None)
    value.pop('snapshot_hash', None)
    value['inventory']['inventory_hash'] = inventory_hash(value['scope'], value['records'])
    value['revision_hash'] = digest({k: v for k, v in value.items() if k != 'captured_at'})
    value['snapshot_hash'] = digest(value)
    validate_snapshot(value)
    return value


@closed
def validate_snapshot(snapshot):
    need(len(canonical(snapshot)) <= MAX_BYTES, '本次完整来源快照超过16 MiB，请缩小范围；未截断来源')
    keys(snapshot, SNAPSHOT_KEYS)
    need(snapshot['schema'] == SCHEMA and all(uid(snapshot[k]) for k in ('workspace_id', 'instance_id', 'captured_by'))
         and aware(snapshot['captured_at']) and snapshot['consistency'] == 'single_repeatable_read_view'
         and snapshot['time_basis'] == 'machine_result_registered' and snapshot['time_zone'] == 'Asia/Shanghai'
         and snapshot['human_basis'] == HUMAN_BASIS and snapshot['candidate_only'] is True
         and snapshot['not_authoritative'] is True and snapshot['field_truth_verified'] is False
         and snapshot['source_time_verified'] is False)
    keys(snapshot['scope'], {'task_id', 'entity_id'})
    need(all(v is None or uid(v) for v in snapshot['scope'].values()))
    inventory = snapshot['inventory']
    keys(inventory, {'complete', 'limit', 'run_ids', 'inventory_hash'})
    records = snapshot['records']
    need(inventory['complete'] is True and integer(inventory['limit'], 1, MAX_RECORDS)
         and type(records) is list and len(records) <= inventory['limit'], LIMIT_ERROR)
    for record in records:
        record_valid(record)
        need(all(snapshot['scope'][k] is None or record[k] == snapshot['scope'][k] for k in ('task_id', 'entity_id')))
    ids = [r['run_id'] for r in records]
    need(ids == sorted(set(ids)) and inventory['run_ids'] == ids
         and inventory['inventory_hash'] == inventory_hash(snapshot['scope'], records)
         and snapshot['revision_hash'] == digest({k: v for k, v in snapshot.items()
                if k not in {'captured_at', 'revision_hash', 'snapshot_hash'}})
         and snapshot['snapshot_hash'] == digest({k: v for k, v in snapshot.items() if k != 'snapshot_hash'}))


def _ir(snapshot, ir):
    keys(ir, {'schema', 'source_facet', 'scope', 'operation', 'metric', 'group_by', 'time', 'filters', 'grounding', 'limitations'})
    need(ir['schema'] == 'pixel-question-ir-v252' and ir['source_facet'] == 'received_pixel_machine_results_v250')
    keys(ir['scope'], {'instance_id', 'authorization_release_id', 'task_id', 'entity_id'})
    need(ir['scope']['instance_id'] == snapshot['instance_id'] and uid(ir['scope']['authorization_release_id'])
         and all(ir['scope'][k] == snapshot['scope'][k] for k in ('task_id', 'entity_id')))
    need(ir['operation'] in {'read', 'count', 'sum', 'mean', 'min', 'max'}
         and ir['group_by'] in {'none', 'registration_day', 'task_id', 'entity_id', 'human_completion'})
    keys(ir['time'], {'basis', 'registration_days', 'timezone'})
    days = ir['time']['registration_days']
    need(ir['time']['basis'] == 'machine_result_registered' and ir['time']['timezone'] == 'Asia/Shanghai'
         and (days is None or type(days) is list and 1 <= len(days) <= 31
              and all(day(d) for d in days) and len(set(days)) == len(days)))
    keys(ir['filters'], {'reception', 'human_completion'})
    need(ir['filters']['reception'] in {'any', 'registered', 'received', 'waiting_data'}
         and ir['filters']['human_completion'] in {'any', 'has_completed_receipt', 'no_completed_receipt'})
    keys(ir['grounding'], {'operation_quote', 'metric_quote', 'time_quote', 'filter_quotes'})
    need(type(ir['grounding']['operation_quote']) is str and 0 < len(ir['grounding']['operation_quote']) <= 4000
         and all(ir['grounding'][k] is None or type(ir['grounding'][k]) is str and len(ir['grounding'][k]) <= 4000
                 for k in ('metric_quote', 'time_quote'))
         and type(ir['grounding']['filter_quotes']) is list and len(ir['grounding']['filter_quotes']) <= 4
         and all(type(v) is str and 0 < len(v) <= 4000 for v in ir['grounding']['filter_quotes'])
         and type(ir['limitations']) is list and len(ir['limitations']) <= 8
         and all(type(v) is str and len(v) <= 2000 for v in ir['limitations']))
    if ir['operation'] in {'read', 'count'}:
        need(ir['metric'] is None)
    else:
        need(ir['metric'] in METRICS and ir['filters']['reception'] == 'received')


@closed
def compute(snapshot: dict, ir: dict) -> dict:
    """Pure closed computation; caller independently verifies the saved source."""
    validate_snapshot(snapshot)
    _ir(snapshot, ir)
    days = ir['time']['registration_days']
    unknown = sum(r['timing']['registration_day'] is None for r in snapshot['records']) if days is not None else 0
    dated = [r for r in snapshot['records'] if days is None or r['timing']['registration_day'] in days]
    states = {'processing_records': len(dated), 'registered_results': 0, 'received_results': 0,
              'registered_waiting': 0, 'unregistered_waiting': 0, 'producer_failed': 0}
    for r in dated:
        registered = r['timing']['registered_at'] is not None
        states['registered_results'] += int(registered)
        state = r['reception']['state']
        key = 'received_results' if state == 'received' else 'producer_failed' if state == 'producer_failed' else (
              'registered_waiting' if registered else 'unregistered_waiting')
        states[key] += 1
    selected = []
    for r in dated:
        reception = ir['filters']['reception']; human = ir['filters']['human_completion']
        if reception == 'registered' and r['timing']['registered_at'] is None: continue
        if reception in {'received', 'waiting_data'} and r['reception']['state'] != reception: continue
        if human == 'has_completed_receipt' and not r['human_receipts']: continue
        if human == 'no_completed_receipt' and r['human_receipts']: continue
        selected.append(r)
    buckets = {}
    for record in selected:
        key = None if ir['group_by'] == 'none' else record['timing']['registration_day'] if ir['group_by'] == 'registration_day' else (
              'has_completed_receipt' if record['human_receipts'] else 'no_completed_receipt') if ir['group_by'] == 'human_completion' else record[ir['group_by']]
        buckets.setdefault(key, []).append(record)
    if not buckets and ir['group_by'] == 'none' and ir['operation'] != 'read': buckets[None] = []
    groups = []
    for key in sorted(buckets, key=lambda v: (v is not None, v or '')):
        rows = buckets[key]; op = ir['operation']
        kind, text, numerator, denominator = 'none', None, None, None
        if op == 'count':
            kind = 'integer'; text = numerator = str(len(rows))
        elif op != 'read' and rows:
            values = [r['metrics'][ir['metric']] for r in rows]
            number = sum(values) if op in {'sum', 'mean'} else min(values) if op == 'min' else max(values)
            numerator = str(number)
            if op == 'mean':
                kind = 'rational'; denominator = len(values); text = numerator + '/' + str(denominator)
            else:
                kind = 'integer'; text = numerator
        groups.append({'key': key, 'run_ids': [r['run_id'] for r in rows], 'matched_records': len(rows),
            'value_text': text, 'value_kind': kind, 'numerator': numerator, 'denominator': denominator})
    unit = 'none' if ir['operation'] == 'read' else 'model_result_pixels' if ir['operation'] != 'count' else {
        'any': 'processing_records', 'registered': 'registered_machine_results',
        'received': 'received_machine_results', 'waiting_data': 'waiting_machine_results'}[ir['filters']['reception']]
    limitations = list(ir['limitations'])
    for value in ('像素候选不表示现场变化、对象数量、覆盖面积或正式验收；0候选也不表示现场无变化',
                  '人工状态仅指查询快照中已提交的回执，不表示全部审批完成或现场真值通过'):
        if value not in limitations: limitations.append(value)
    if unknown: limitations.append('unknown_registration_day_excluded')
    if not selected and ir['operation'] not in {'read', 'count'}: limitations.append('no_received_matching_results')
    return {'schema': COMPUTATION, 'source_snapshot_hash': snapshot['snapshot_hash'],
        'operation': ir['operation'], 'metric': ir['metric'], 'group_by': ir['group_by'],
        'time': copy.deepcopy(ir['time']), 'filters': copy.deepcopy(ir['filters']),
        'coverage': {'complete': True, 'scanned_records': len(snapshot['records']), 'matched_records': len(selected),
            'unknown_registration_day_excluded': unknown, 'limit': snapshot['inventory']['limit']},
        'state_counts': states, 'groups': groups,
        'references': [{'run_id': r['run_id'], 'task_id': r['task_id'], 'entity_id': r['entity_id'],
            'producer_release_id': r['producer_release_id'], 'result_hash': r['result_hash'] if r['reception']['state'] == 'received' else None,
            'receipt_hash': r['reception']['receipt_hash'], 'human_receipt_hashes': [h['receipt_hash'] for h in r['human_receipts']],
            'registered_at': r['timing']['registered_at']} for r in selected],
        'unit': unit, 'metrics_basis': 'independently_received_results_only',
        'time_basis': 'machine_result_registered', 'human_basis': HUMAN_BASIS,
        'candidate_only': True, 'not_authoritative': True, 'field_truth_verified': False,
        'source_time_verified': False, 'limitations': limitations}
