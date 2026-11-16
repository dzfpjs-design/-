"""Content checks and cold records for an already frozen Task-sound snapshot.

This helper grants no query authority. Its caller must first verify the real
saved query and independent source Audit. Later legitimate ACK/review progress
does not upgrade frozen pending values. Current original/ACK damage fails
closed; the caller preserves the old payload rather than repairing it.
"""
import copy

from fastapi import HTTPException

from .contracts import digest
from . import task_sound_query_evidence as source
from .sound_models import EnvironmentSoundRun
from .services import scoped
from .multimodal_query_contracts_v262 import need, identity, hashed, integer, instant, wire

SNAPSHOT_KEYS = {'schema', 'source_family', 'workspace_id', 'captured_by', 'instance_id', 'scope', 'as_of',
    'consistency', 'inventory', 'source_integrity_complete', 'records', 'time_basis', 'time_zone',
    'recording_time_verified', 'candidate_only', 'not_authoritative', 'physical_state_verified', 'limitations', 'snapshot_hash'}
RECORD_KEYS = {'source_family', 'run_id', 'task_id', 'run_version', 'run_status', 'entity_id', 'scope_matched',
    'producer_release_id', 'producer_status', 'candidate_available', 'data_values_available', 'values',
    'reception', 'timing', 'reference', 'quality', 'association_basis', 'integrity_issue', 'record_hash'}
RECEPTION = {'state', 'observation_id', 'event_id', 'memory', 'receipt_hash'}
STATES = {'received', 'waiting_data', 'producer_failed', 'local_handoff_pending', 'blocked_integrity'}
TIMING = {'run_started_at', 'model_completed_at', 'registered_at', 'registration_day', 'received_at', 'recorded_at'}
REFERENCE = {'source_family', 'run_id', 'task_id', 'producer_release_id', 'job_id', 'invocation_id',
    'result_hash', 'request_hash', 'capability_id', 'capability_hash', 'sound_snapshot_hash', 'input_audit',
    'result_audit', 'completion_audit', 'issued_audit', 'settlement_audit', 'packet_hash', 'event_base_hash',
    'review_receipt_hashes', 'detail_path', 'task_path'}
QUALITY = {'candidate_only': True, 'not_authoritative': True, 'score_calibration_verified': False,
    'source_time_verified': False, 'physical_state_verified': False, 'entity_state_updated': False}


def validate_snapshot(value):
    need(type(value) is dict and set(value) == SNAPSHOT_KEYS and value['schema'] == source.SCHEMA
        and value['source_family'] == source.FAMILY and all(identity(value[key])
            for key in ('workspace_id', 'captured_by', 'instance_id'))
        and value['consistency'] == 'caller_current_repeatable_read_read_only'
        and value['time_basis'] == 'machine_result_registered' and value['time_zone'] == 'Asia/Shanghai'
        and value['recording_time_verified'] is False and value['candidate_only'] is True
        and value['not_authoritative'] is True and value['physical_state_verified'] is False)
    instant(value['as_of'])
    need(type(value['limitations']) is list and all(type(item) is str for item in value['limitations']))
    scope = value['scope']; inventory = value['inventory']; records = value['records']
    need(type(scope) is dict and set(scope) == {'task_id', 'entity_id'}
        and all(item is None or identity(item) for item in scope.values())
        and type(inventory) is dict and set(inventory) == {'basis', 'complete', 'limit', 'scanned_run_ids',
            'returned_run_ids', 'expected_sources_verified'} and inventory['basis'] == 'registered_run_scan'
        and inventory['complete'] is True and inventory['expected_sources_verified'] is False
        and integer(inventory['limit'], 1, source.MAX_RECORDS) and type(records) is list)
    for name in ('scanned_run_ids', 'returned_run_ids'):
        ids = inventory[name]
        need(type(ids) is list and len(ids) <= inventory['limit'] and len(ids) == len(set(ids))
            and all(identity(item) for item in ids))
    need(set(inventory['returned_run_ids']) <= set(inventory['scanned_run_ids'])
        and [record['run_id'] for record in records] == inventory['returned_run_ids'])
    for record in records:
        need(type(record) is dict and set(record) == RECORD_KEYS and record['source_family'] == source.FAMILY
            and identity(record['run_id']) and identity(record['task_id']) and integer(record['run_version'], 1, 2**63 - 1)
            and type(record['candidate_available']) is bool and type(record['data_values_available']) is bool
            and record['quality'] == QUALITY and all(type(item) is bool for item in record['quality'].values())
            and record['association_basis'] == 'task_entity_association_not_verified_sound_attribution')
        reception = record['reception']; timing = record['timing']; reference = record['reference']
        need(type(reception) is dict and set(reception) == RECEPTION and reception['state'] in STATES
            and type(timing) is dict and set(timing) == TIMING and timing['recorded_at'] is None
            and type(reference) is dict and set(reference) == REFERENCE
            and reference['source_family'] == source.FAMILY and reference['run_id'] == record['run_id']
            and reference['task_id'] == record['task_id']
            and type(reference['review_receipt_hashes']) is list
            and all(hashed(item) for item in reference['review_receipt_hashes'])
            and len(set(reference['review_receipt_hashes'])) == len(reference['review_receipt_hashes']))
        if reception['state'] == 'received':
            need(record['data_values_available'] is True and type(record['values']) is dict
                and type(reception['memory']) is dict and set(reception['memory']) == {'id', 'version', 'data_hash'}
                and identity(reception['memory']['id']) and integer(reception['memory']['version'], 1, 2**63 - 1)
                and hashed(reception['memory']['data_hash']) and hashed(reception['receipt_hash']))
            metrics = record['values']
            need(set(metrics) == {'window_count', 'candidate_class_count', 'candidate_window_count', 'candidates'}
                and all(integer(metrics[key], 0, 1000000) for key in
                    ('window_count', 'candidate_class_count', 'candidate_window_count'))
                and type(metrics['candidates']) is list and metrics['candidate_class_count'] == len(metrics['candidates']))
        else:
            need(record['data_values_available'] is False and record['values'] is None
                and reception['memory'] is None and reception['receipt_hash'] is None)
        need(record['record_hash'] == digest({key: item for key, item in record.items() if key != 'record_hash'}))
    need(type(value['source_integrity_complete']) is bool and value['source_integrity_complete'] == all(
        record['reception']['state'] != 'blocked_integrity' for record in records))
    wire(value)
    need(value['snapshot_hash'] == digest({key: item for key, item in value.items() if key != 'snapshot_hash'}))
    return copy.deepcopy(value)


def verify_frozen_records(db, p, snapshot):
    """Verify old non-null pins, never substitute a fresh record or inventory."""
    try:
        validate_snapshot(snapshot)
        need(snapshot['workspace_id'] == p.workspace_id and snapshot['captured_by'] == p.user_id)
        with db.no_autoflush:
            for frozen in snapshot['records']:
                run = scoped(db, EnvironmentSoundRun, frozen['run_id'], p)
                fresh = source._record(db, p, snapshot['instance_id'], run, snapshot['scope']['entity_id'])
                need(fresh is not None and fresh['reception']['state'] != 'blocked_integrity',
                    '当前声音原件、模型、原任务或独立ACK受损；原冻结内容未改写')
                need(run.task_id == frozen['task_id'] and run.version >= frozen['run_version'])
                for key in ('source_family', 'run_id', 'task_id', 'entity_id', 'producer_release_id'):
                    if frozen[key] is not None: need(fresh[key] == frozen[key])
                for key, old in frozen['reference'].items():
                    if key == 'review_receipt_hashes':
                        need(set(old) <= set(fresh['reference'][key]))
                    elif old is not None:
                        need(old == fresh['reference'][key])
                for key, old in frozen['timing'].items():
                    if old is not None: need(old == fresh['timing'][key])
                for key in ('observation_id', 'event_id', 'memory', 'receipt_hash'):
                    if frozen['reception'][key] is not None:
                        need(frozen['reception'][key] == fresh['reception'][key])
                if frozen['data_values_available']:
                    need(fresh['reception']['state'] == 'received' and digest(fresh['values']) == digest(frozen['values']))
    except HTTPException as error:
        if error.status_code in {401, 403}: raise
        raise HTTPException(409, '冻结声音来源目前无法完整核对，原问题和内容保留') from None
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        raise HTTPException(409, '冻结声音来源目前无法完整核对，原问题和内容保留') from None
