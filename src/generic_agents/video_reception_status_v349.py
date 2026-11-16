"""Read-only, cold-verified data reception status for a saved video candidate."""
from fastapi import HTTPException
from sqlalchemy import select
from . import models as m
from . import native_video_provenance_v164 as video

SCHEMA = 'video-candidate-data-reception-v349'


def _result(state, observation_id=None):
    return {'schema': SCHEMA, 'state': state,
        'data_reception_verified': state == 'received', 'observation_id': observation_id}


def status(db, p, basis):
    """Never alter or sign an original, nor fold the changing ACK into basis_hash.

    Caller must have checked the task's original video association. Use one
    repeatable-read snapshot for all rows, including unique event and ACK.
    """
    try:
        run_id = basis['perception_event']['run_id']
        entity_id = basis['entity_id']
        record_id = 'VideoRun:' + run_id + ':' + entity_id
        rows = list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
            m.Observation.source_record_id == record_id).limit(2)))
        if len(rows) > 1:
            return _result('integrity_problem')
        if not rows:
            if not video.has_run_trace(db, p, run_id):
                return _result('not_available')
            run = video.row(db, m.VideoRun, run_id, p)
            parent = video.row(db, m.Job, run.data['job_id'], p)
            jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
                m.Job.idempotency_key == 'native-sync:' + parent.id).limit(2)))
            if len(jobs) > 1:
                return _result('integrity_problem')
            if not jobs:
                return _result('pending' if parent.status in {'queued', 'running'} else 'integrity_problem')
            job = jobs[0]
            if (job.kind != 'sync_native_receipts' or job.data.get('original_video_run_id') != run_id
                    or job.data.get('instance_id') != basis['instance_id']
                    or job.data.get('entity_ids') != [entity_id]):
                return _result('integrity_problem')
            if job.status in {'queued', 'running'}:
                return _result('pending')
            return _result('blocked' if job.status in {'failed', 'cancelled'} else 'integrity_problem')
        observation = rows[0]
        context = video.checked_observation(db, p, observation, live=False)
        if (context['instance'].id != basis['instance_id'] or context['release'].id != basis['release_id']
                or context['entity'].id != entity_id or context['run'].id != run_id):
            return _result('integrity_problem')
        event = context['event']
        if event.status in {'pending_delivery', 'blocked_delivery'}:
            memories = list(db.scalars(select(m.Memory.id).where(m.Memory.workspace_id == p.workspace_id,
                m.Memory.data['event_id'].as_string() == event.id,
                m.Memory.data['type'].as_string() == 'observation_evidence').limit(1)))
            if (event.received_at is not None or memories or 'data_reception' in observation.data
                    or 'data_reception' in event.data):
                return _result('integrity_problem')
            return _result('pending' if event.status == 'pending_delivery' else 'blocked')
        if event.status != 'received':
            return _result('integrity_problem')
        from .instance_data_receipts import observation_receipt
        receipt = observation_receipt(db, p, observation).get('data_reception')
        if (type(receipt) is not dict or receipt.get('schema') != video.RECEPTION_SCHEMA
                or receipt.get('run_id') != run_id or receipt.get('entity_id') != entity_id
                or receipt.get('instance_id') != basis['instance_id']
                or receipt.get('release_id') != basis['release_id']):
            return _result('integrity_problem')
        return _result('received', observation.id)
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError, OSError, OverflowError, RecursionError):
        return _result('integrity_problem')
