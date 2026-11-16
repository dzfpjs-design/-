"""Bridge an owned mapped HTTP exchange into the normal observation pipeline.

This module sends no HTTP request. Live Source/Asset/Goal/approval authorization
and the single actual transport remain in the central acquisition dispatcher.
The parent worker must atomically succeed and release the waiting child; a
supplier projection is neither extraction, an ACK nor completion of a data gap.
"""
import copy
import math
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import http_observation_exchange_v177 as ledger
from . import http_observation_mapping_v177 as protocol
from .contracts import ObservationCreate
from .models import Entity, Job, Observation, SensorAsset
from .services import published, scoped

PROCESS = 'process_http_mapped_observation_v177'
PROCESSING_VERSION = 'http-mapped-v177'
ORIGIN_SCHEMA = 'http-mapped-origin-v177'
MAPPING_KEY = 'http_observation_mapping_v177'
EXTRACTOR_ID = 'http_observation_extractor_id_v177'
EXTRACTOR_HASH = 'http_observation_extractor_hash_v177'


def _closed(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            ledger.need(False, 'HTTP映射、原回执或正式观测无法核对；保留原号，不重新派发')
    return checked


def _registered_rules(db, p, identity, fields, *, package_id, entity_type, field_units):
    # Keep the old MQTT contract untouched. Existing SIM tools explicitly carry
    # typed side_effect=False; it is equally local/read-only, but 0 is not False.
    cap = published(db, p, identity, 'tool')
    execution, policy = cap.data['execution'], cap.data['data_policy']
    ledger.need(type(execution) is dict
        and set(execution) in ({'adapter', 'handler'}, {'adapter', 'handler', 'side_effect'})
        and execution['adapter'] == 'builtin' and execution['handler'] == 'numeric_rules'
        and ('side_effect' not in execution or execution['side_effect'] is False)
        and 'perception' in cap.data['allowed_agent_roles']
        and not policy.get('cloud_allowed') and not policy.get('external_allowed'),
        '此映射入口只允许已发布的本地只读数值规则')
    rules = cap.data['specification'].get('rules', [])
    managed = cap.data['specification'].get('managed_numeric_rule')
    if managed is not None or cap.namespace == 'platform_numeric_rules':
        from .local_rules_api import require_current_check
        require_current_check(db, p, cap)
        ledger.need(type(managed) is dict and managed.get('package_id') == package_id
            and managed.get('entity_type') == entity_type and type(rules) is list
            and all(type(rule) is dict and rule.get('unit') == field_units.get(rule.get('field'))
                for rule in rules), '本地规则的业务对象类型或单位与原映射不符')
    ledger.need(type(rules) is list and len(rules) <= 64)
    for rule in rules:
        ledger.need(type(rule) is dict and rule.get('field') in fields
            and rule.get('operator') in {'gt', 'lt'} and type(rule.get('threshold')) in {int, float}
            and math.isfinite(rule['threshold']) and abs(rule['threshold']) <= 1e15
            and type(rule.get('id')) is str and bool(rule['id'])
            and ('unit' not in rule or rule['unit'] == field_units[rule['field']]),
            '规则字段、原单位和有限数值条件须与已确认映射一致')
    return cap


def _extractor(db, p, source, mapping):
    spec = source.data['specification']
    identity, expected = spec[EXTRACTOR_ID], spec[EXTRACTOR_HASH]
    protocol._uuid(identity); protocol._hash(expected)
    fields = mapping['field_schemas']
    units = {link['field_key']: link['unit'] for link in mapping['mapping']['links'].values()}
    scope = mapping['mapping']['scope']
    extractor = _registered_rules(db, p, identity, set(fields), package_id=scope['package_id'],
        entity_type=scope['entity_type'], field_units=units)
    execution, policy = extractor.data['execution'], extractor.data['data_policy']
    ledger.need(extractor.id == identity and extractor.workspace_id == p.workspace_id
        and extractor.kind == 'tool' and extractor.status == 'published'
        and extractor.content_hash == extractor.validated_hash == expected == protocol.digest(extractor.data)
        and 'perception' in extractor.data['allowed_agent_roles']
        and execution.get('adapter') == 'builtin' and execution.get('handler') == 'numeric_rules'
        and execution.get('side_effect', False) is False
        and policy.get('deployment') == 'local' and policy.get('cloud_allowed') is False
        and not policy.get('external_allowed'),
        '原Source必须固定已发布本地数值规则及完整内容摘要，不能换模型或外发')
    return extractor


@_closed
def prepare_dispatch(db, p, acq, source, asset, payload):
    """Freeze before the caller commits intent and sends exact wire.body once.

    None means this Source has no new mapping configuration at all. An invalid
    configured mapping raises instead of falling back to an older family.
    created=False is an original exchange lookup, never permission to POST.
    """
    source = published(db, p, source.id, 'source')
    spec = source.data['specification']
    if MAPPING_KEY not in spec:
        return None
    configured = spec[MAPPING_KEY]
    ledger.need(type(configured) is dict and source.id == acq.capability_id)
    entity = scoped(db, Entity, acq.entity_id, p)
    actual_asset = scoped(db, SensorAsset, asset.id, p)
    ledger.need(actual_asset.id == acq.data['sensor_asset_id']
        and actual_asset.source_id == source.id and actual_asset.version == acq.data['sensor_asset_version'])
    package = published(db, p, entity.schema_capability_id, 'domain_package')
    scope = {**configured['scope'], 'workspace_id': p.workspace_id, 'source_id': source.id,
        'package_id': package.id, 'package_hash': package.content_hash, 'entity_type': entity.entity_type}
    mapping = protocol.freeze_mapping(source.data, scope,
        package.data['specification']['entity_schemas'], configured['links'])
    _extractor(db, p, source, mapping)
    pair = protocol.to_supplier_request(payload, mapping)
    job, _ = ledger._leased(db, p)
    row, created = ledger.create(db, p, acq.id, job.id, mapping,
        pair['domain_frozen'], pair['wire_frozen'])
    if created:
        row = ledger.mark_awaiting_receipt(db, p, row.id, row.version)
    return row, created


def _body(row, extractor_id):
    projection = row.data['projection']
    ledger.need(row.status == 'received' and projection['status'] == 'mapped'
        and projection['temporal_usable'] is True)
    clock = datetime.fromisoformat(projection['observed_at'])
    ledger.need(clock.tzinfo is not None and clock.utcoffset() is not None)
    pins = row.data['freeze']['parent_pins']
    return ObservationCreate(source_id=row.source_id, source_record_id='http-acquisition:' + row.id,
        processing_version=PROCESSING_VERSION, entity_id=pins['entity_id'],
        observed_at=projection['observed_at'],
        business_day=str(clock.astimezone(ZoneInfo('Asia/Shanghai')).date()),
        attributes=copy.deepcopy(projection['mapped_attributes']),
        evidence_ids=[],
        extractor_id=extractor_id, startup_mode='automatic', acquisition_id=row.acquisition_id,
        sensor_asset_id=pins['sensor_asset_id'], sensor_modality='numeric')


def _ingest(body, p, db):
    # The existing authenticated ingest function is owned by the central API.
    # Keep this lazy: pure protocol tests neither import nor substitute that API.
    from .api import ingest_observation_record
    return ingest_observation_record(body, p, db, commit=False)


def _request(body):
    # Match the established numeric ingestion hash; parsed receipt Evidence is
    # traceable provenance, never a file input to builtin numeric_rules.
    return body.model_dump(mode='json', exclude={'evidence_ids'} if not body.evidence_ids else set())


def _observation(db, p, row, identity, body):
    observation = scoped(db, Observation, identity, p, True)
    expected = _request(body)
    ledger.need(observation.workspace_id == p.workspace_id and row.actor_id == p.user_id
        and observation.source_id == row.source_id and observation.source_record_id == body.source_record_id
        and observation.processing_version == PROCESSING_VERSION and observation.entity_id == body.entity_id
        and observation.business_day == body.business_day
        and ledger.same(observation.data['original_request'], expected)
        and observation.data.get('input_hash') == protocol.digest(expected)
        and observation.data.get('submitted_by') == p.user_id
        and ledger.same(observation.data.get('evidence_ids'), [])
        and observation.data.get('startup_mode') == 'automatic'
        and ledger.same(observation.data['attributes'], body.attributes)
        and observation.data.get('acquisition_id') == row.acquisition_id
        and observation.data.get('sensor_asset_id') == body.sensor_asset_id
        and observation.data.get('sensor_modality') == 'numeric')
    return observation


def _process(db, p, observation):
    process = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == 'extract:' + observation.id).with_for_update()
        .execution_options(populate_existing=True))
    ledger.need(process is not None and process.data.get('observation_id') == observation.id
        and process.data.get('actor_id') == p.user_id)
    return process


def _labels(row):
    projection = row.data['projection']
    return {**ledger.MARK,
        'http_mapped_origin_v177': {'schema': ORIGIN_SCHEMA, 'exchange_id': row.id},
        'http_mapped_receipt_evidence_id_v177': row.data['receipt_history'][-1]['evidence_id'],
        'source_time_verified': False, 'physical_device_connected': False,
        'date_basis': projection['time_semantics'], 'time_semantics': projection['time_semantics'],
        'field_units': copy.deepcopy(projection['field_units'])}


@_closed
def receive(db, p, exchange_id, raw_receipt, *, saved_receipt=False):
    """Consume only an actual owned POST/GET receipt, never query or redispatch.

    The caller must perform current live parent guards before entry. No commit
    follows creation/attachment here: parent success and child activation belong
    to the same final worker transaction.
    """
    ledger.need(type(saved_receipt) is bool)
    origin = ledger.receipt_origin(db, p, exchange_id)
    row = ledger.checked_store(db, p, exchange_id, lock=True)
    if saved_receipt:
        # This is not a caller authorization flag. Verify the actual owned
        # parent/token against the immutable original proof, then do no commit
        # so the caller can roll back all derived rows in one savepoint.
        ledger.need(bool(row.data['receipt_history'])
            and row.data['latest_receipt_hash'] == protocol.digest(raw_receipt)
            and ledger.same(row.data['receipt_history'][-1]['raw'], raw_receipt)
            and ledger.same(row.data['receipt_history'][-1]['receipt_origin'], origin))
    else:
        row = ledger.record_receipt(db, p, exchange_id, row.version, raw_receipt,
            receipt_origin=origin)
    projection = row.data['projection']
    if (row.status != 'received' or projection['status'] != 'mapped'
            or projection['temporal_usable'] is not True):
        return row
    source = published(db, p, row.source_id, 'source')
    mapping = row.data['freeze']['mapping']
    ledger.need(source.content_hash == mapping['source_manifest_hash'])
    extractor = _extractor(db, p, source, mapping)
    body = _body(row, extractor.id)
    labels = _labels(row)
    if row.observation_id:
        observation = _observation(db, p, row, row.observation_id, body)
        ledger.need(all(ledger.same(observation.data.get(key), value) for key, value in labels.items()))
        process = _process(db, p, observation)
        ledger.need(process.kind == PROCESS
            and process.data.get('http_mapped_exchange_id') == row.id
            and process.data.get('http_mapped_parent_job_id') == row.data['receipt_history'][-1]['receipt_origin']['job_id'])
        return row  # No ingestion, new child or status reset after an original receipt.
    created = _ingest(body, p, db)
    ledger.need(type(created) is dict and type(created.get('id')) is str)
    observation = _observation(db, p, row, created['id'], body)
    process = _process(db, p, observation)
    ledger.need(observation.status == 'received' and process.kind == 'process_observation'
        and process.status == 'queued' and type(process.attempts) is int and process.attempts == 0
        and 'http_mapped_origin_v177' not in observation.data,
        '既有观测或处理任务不是本次尚未执行的正常接入，不能覆盖其历史或重新执行')
    process.kind = PROCESS
    process.status = 'waiting_parent'
    process.data = {**process.data, 'http_mapped_exchange_id': row.id,
        'http_mapped_parent_job_id': origin['job_id']}
    observation.data = {**observation.data, **labels}
    return ledger.attach_observation(db, p, row.id, row.version, observation.id)
