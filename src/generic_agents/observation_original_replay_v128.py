"""Read-only recovery of one exact original observation submission.

The caller retains every current IAM/source/extractor/date/file/device gate.
None means that no saved row was supplied, never permission to create one.
Success returns the actual cold-checked Observation, without issuing work,
repairing a receipt, or changing any business/device state.
"""
from fastapi import HTTPException
from sqlalchemy import select

from .contracts import ObservationCreate, digest
from .models import Acquisition, Event, Memory, Observation, SensorAsset, SensorAssetEvent
from .services import scoped, timestamp

ERROR = '原观测提交、原补采或数据接收依据无法核对；原记录保留，未新建或重新处理'
COLUMNS = {'source_id', 'source_record_id', 'processing_version', 'entity_id', 'observed_at', 'business_day'}
STATES = {'received', 'queued', 'running', 'failed', 'cancelled', 'processed'}


def _need(value):
    if not value: raise HTTPException(409, ERROR)


def _same(left, right):
    # Python equality alone equates bool/int and int/float.  Preserve the
    # original JSON/hash distinction used by the existing registration DTO.
    return digest(left) == digest(right)


def _sensor(db, p, row, body, acq):
    sensor_id = body.sensor_asset_id
    if acq is not None and acq.data.get('sensor_asset_id'):
        _need(sensor_id in (None, acq.data['sensor_asset_id']))
        sensor_id = acq.data['sensor_asset_id']
    if not sensor_id:
        _need(body.sensor_modality is None and 'sensor_evidence' not in row.data)
        return
    asset = scoped(db, SensorAsset, sensor_id, p)
    _need(asset.source_id == body.source_id and body.entity_id in asset.data['coverage_entity_ids'])
    recorded = row.data.get('sensor_evidence')
    _need(isinstance(recorded, dict))
    if acq is None or not acq.data.get('sensor_asset_id'):
        version = recorded.get('asset_version')
        _need(type(version) is int and 1 <= version <= asset.version)
        rows = list(db.scalars(select(SensorAssetEvent).where(SensorAssetEvent.workspace_id == p.workspace_id,
            SensorAssetEvent.asset_id == asset.id,
            SensorAssetEvent.data['after']['version'].as_integer() == version)
            .execution_options(populate_existing=True)))
        _need(len(rows) == 1)
        after = rows[0].data['after']
        _need(after['id'] == asset.id and after['workspace_id'] == p.workspace_id and after['source_id'] == body.source_id
            and after['external_id'] == asset.external_id and after['version'] == version)
        snap = after['data']
        _need(body.entity_id in snap['coverage_entity_ids'])
        bindings = list(db.scalars(select(SensorAssetEvent).where(SensorAssetEvent.workspace_id == p.workspace_id,
            SensorAssetEvent.asset_id == asset.id, SensorAssetEvent.data['operation'].as_string() == 'device_bound')
            .execution_options(populate_existing=True)))
        candidates = []
        for issued in bindings:
            value = issued.data.get('after')
            _need(isinstance(value, dict) and type(value.get('version')) is int and value['id'] == asset.id
                and value['workspace_id'] == p.workspace_id and value['source_id'] == body.source_id)
            if value['version'] <= version: candidates.append(issued)
        candidates.sort(key=lambda item: item.data['after']['version'], reverse=True)
        _need(not candidates or sum(item.data['after']['version'] == candidates[0].data['after']['version'] for item in candidates) == 1)
        fixed = candidates[0].data['binding_after'] if candidates else None
        if fixed is not None:
            _need(isinstance(fixed, dict) and fixed['asset_id'] == asset.id and fixed['workspace_id'] == p.workspace_id
                and isinstance(fixed.get('device_id'), str) and type(fixed.get('version')) is int and fixed['version'] >= 1)
        identity = {'device_id': fixed['device_id'] if fixed else None, 'binding_version': fixed['version'] if fixed else None}
        if acq is not None:
            # The old generic acquisition path may accept an explicitly named
            # asset without a dispatched asset snapshot; retain its original
            # acquisition-derived identity shape, never a current binding.
            identity = {'device_id': acq.data.get('sensor_device_id'), 'device_version': acq.data.get('sensor_device_version'),
                'binding_version': acq.data.get('device_binding_version')}
    else:
        version = acq.data['sensor_asset_version'] if acq.data.get('sensor_asset_id') else asset.version
        snap = acq.data.get('dispatch_asset') or asset.data
        identity = {'device_id': acq.data.get('sensor_device_id'), 'device_version': acq.data.get('sensor_device_version'),
            'binding_version': acq.data.get('device_binding_version')}
    modality = body.sensor_modality or (snap['modalities'][0] if len(snap['modalities']) == 1 else None)
    _need(modality in snap['modalities'])
    expected = {'asset_id': asset.id,
        'asset_version': version,
        'modality': modality, 'resolution_m': snap['resolution_m'],
        'basis': 'registered_asset_declaration_not_independently_measured', **identity}
    _need('sensor_evidence' in row.data and _same(row.data['sensor_evidence'], expected))


def _validate(db, p, row, body):
    if row is None: return None
    _need(isinstance(body, ObservationCreate) and row.workspace_id == p.workspace_id)
    actual = db.scalar(select(Observation).where(Observation.workspace_id == p.workspace_id,
        Observation.id == row.id).execution_options(populate_existing=True))
    _need(actual is not None and actual.status in STATES and isinstance(actual.data, dict))
    row = actual
    expected = body.model_dump(exclude={'evidence_ids'} if not body.evidence_ids else set())
    original = row.data.get('original_request')
    _need(isinstance(original, dict) and _same(original, expected)
        and row.data.get('input_hash') == digest(original) == digest(expected))
    for key in COLUMNS - {'observed_at'}:
        _need(getattr(row, key) == getattr(body, key))
    _need(row.observed_at == timestamp(body.observed_at))
    supplied = body.model_dump(exclude=COLUMNS)
    _need(all(key in row.data and _same(row.data[key], value) for key, value in supplied.items()))
    # IDs alone do not establish that the originally referenced bytes survive.
    from .evidence import evidence_bytes
    for identifier in body.evidence_ids: evidence_bytes(db, p, identifier)
    acq = scoped(db, Acquisition, body.acquisition_id, p) if body.acquisition_id else None
    if acq is not None:
        _need(acq.entity_id == row.entity_id and acq.capability_id == row.source_id)
    _sensor(db, p, row, body, acq)
    from .original_acquisition_reception_v127 import context
    original_context = context(db, p, row)
    if acq is not None:
        if original_context is None:
            _need(acq.status == 'awaiting_observation')
        else:
            _need(original_context.acquisition.id == acq.id)
            _need(acq.status == 'awaiting_observation' or acq.status == 'succeeded'
                and acq.data.get('closing_observation_id') == row.id and row.status == 'processed')
    else: _need(original_context is None)
    if row.status == 'processed':
        from .observation_integrity import checked_report
        checked_report(db, p, row, allow_candidates=True)
    event = db.scalar(select(Event).where(Event.workspace_id == p.workspace_id,
        Event.kind == 'observation.data', Event.idempotency_key == 'data:' + row.id)
        .execution_options(populate_existing=True))
    memories = list(db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
        Memory.data['observation_id'].as_string() == row.id,
        Memory.data['type'].as_string() == 'observation_evidence').execution_options(populate_existing=True)))
    claimed = ('data_reception' in row.data or event is not None and 'data_reception' in event.data
        or any('data_reception' in item.data for item in memories))
    closed = acq is not None and acq.status == 'succeeded'
    if claimed or closed:
        from .instance_data_receipts import observation_receipt
        _need(row.status == 'processed' and bool(observation_receipt(db, p, row).get('data_reception')))
    return row


def validate(db, p, row, body):
    """Return None or the actual verified saved row; perform no writes/ingest.

    A processed-but-undelivered original can be read as processed.  It is not
    asserted received.  Closed acquisition recovery is limited to an actual
    native successful close with its own Observation and full Event/Memory
    receipt; generic closed acquisitions do not acquire historical permission.
    """
    try:
        return _validate(db, p, row, body)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, OSError, RecursionError):
        raise HTTPException(409, ERROR) from None
