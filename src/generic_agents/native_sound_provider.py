"""Read-only sound sibling for the legacy native receipts surface.

Machine scores are redacted here until independent ACK. Task candidate review
continues to use its original Task result view, not this data-agent projection.
"""
import copy
from fastapi import HTTPException
from . import native_sound_provenance as origin
from .native_sound_receipts import receipt_projection


def legacy_receipt_view(db, p, observation):
    base = {'id': observation.id, 'created_at': origin.iso(observation.created_at),
        'received_day': observation.business_day, 'entity_id': observation.entity_id,
        'integrity_error': None, 'data_received': False, 'record': None, 'native_sound_result_v1': None}
    try:
        context = origin.checked_observation(db, p, observation)
        run = context['run']; reception = receipt_projection(db, p, run)
        available = reception['state'] == 'received'
        from .workflow_sound import run_view
        result = run_view(run)
        if not available:
            result = {**result, 'result': None}
        entry = {'run': result, 'entity_id': observation.entity_id, 'reception': reception,
            'data_values_available': available, 'candidate_only': True, 'not_authoritative': True,
            'physical_state_verified': False}
        return {**base, 'received_day': context['packet']['registration_day'], 'data_received': available,
            'integrity_error': reception['integrity_issue'], 'native_sound_result_v1': entry,
            'record': {'schema': 'native-sound-receipt-record-v1', 'source_key': 'EnvironmentSoundRun:' + run.id,
                'name': '本地环境声音候选成果', 'review_state': 'reviewed' if run.data['reviews'] else 'unreviewed',
                'date_basis': 'machine_result_registered', 'recording_time_verified': False,
                'candidate_only': True, 'entity_state_updated': False}}
    except HTTPException as error:
        if error.status_code == 403:
            raise
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        pass
    return {**base, 'integrity_error': origin.ERROR}
