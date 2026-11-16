"""Cold managed MQTT custody for Goal ACKs; never an acquisition or a grant.

The original packet and managed delivery are independently authenticated.
PUBACK and sensor_original Memory cannot replace observation.data reception.
Mutable QoS delivery counters and current route heads are not origin pins.
"""
import copy
from datetime import datetime, timezone
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from . import mqtt_observations as mqtt, mqtt_binding_routes as routes
from .contracts import digest
from .evidence import evidence_bytes
from .models import (Acquisition, Audit, Capability, Entity, Event, Evidence, InstanceRelease, Invocation,
    Memory, MqttBindingRevision, MqttGateway, MqttReceipt, MqttReceiptDelivery, Observation)
from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from .temporal import instant
from .workflow_goal_evaluation_contracts_v288 import MqttOriginPinsV297, MqttTestOriginV297

KIND = 'mqtt_passive_v297'
SCHEMA = 'mqtt-passive-origin-pins-v297'
ERROR = 'MQTT原报文、固定关联、独立观测收件或目标归属无法核对；未采集、重试或调用模型'
FORWARD = frozenset({'mqtt_receipt_id', 'mqtt_binding_id', 'mqtt_binding_hash',
    'mqtt_delivery_id', 'mqtt_delivery_target_hash'})
FOREIGN = frozenset({'http_mapped_origin_v177', 'http_mapped_receipt_evidence_id_v177',
    'simulation_origin', 'native_packet', 'native_instance_id'})


def need(value):
    if not value: raise HTTPException(409, ERROR)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException as error:
            if error.status_code in {401, 403}: raise
            raise HTTPException(409, ERROR) from None
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError,
                RecursionError, OverflowError, UnicodeError):
            need(False)
    return wrapped


def _row(db, p, model, identity):
    row = db.scalar(select(model).where(model.workspace_id == p.workspace_id, model.id == identity)
        .execution_options(populate_existing=True))
    need(row is not None)
    return row


def _rows(db, statement):
    return list(db.scalars(statement.limit(2).execution_options(populate_existing=True)))


def _time(value):
    need(isinstance(value, datetime) or type(value) is str)
    value = value if isinstance(value, datetime) else instant(value)
    need(value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _audit_hash(row):
    return digest({'id': row.id, 'actor_id': row.actor_id, 'action': row.action,
        'object_id': row.object_id, 'data': row.data, 'created_at': _time(row.created_at).isoformat()})


def _audit(db, p, action, object_id, actor, data):
    rows = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == object_id))
    need(len(rows) == 1 and rows[0].actor_id == actor and digest(rows[0].data) == digest(data))
    return rows[0]


def _deliveries(db, p, observation):
    return _rows(db, select(MqttReceiptDelivery).where(MqttReceiptDelivery.workspace_id == p.workspace_id,
        MqttReceiptDelivery.observation_id == observation.id))


def _legacy_receipts(db, p, observation):
    return _rows(db, select(MqttReceipt).where(MqttReceipt.workspace_id == p.workspace_id,
        MqttReceipt.data['observation_id'].as_string() == observation.id))


@checked
def handles(db, p, observation):
    """Witness detection only; removing a JSON marker cannot select legacy."""
    need(observation.workspace_id == p.workspace_id)
    source = _row(db, p, Capability, observation.source_id)
    execution = source.data.get('execution', {}); specification = source.data.get('specification', {})
    return bool(FORWARD & set(observation.data) or execution.get('adapter') == 'mqtt_source'
        or 'mqtt_binding_id' in specification or _deliveries(db, p, observation)
        or _legacy_receipts(db, p, observation))


def _test_declaration(db, p, receipt, cut):
    # Fixed server module; a missing installed verifier must not erase a real
    # test-origin declaration or be replaced by a client-provided boolean.
    from .mqtt_test_origin_v297 import checked_declaration
    return checked_declaration(db, p, receipt, cut=cut)


@checked
def checked_origin(db, p, observation, *, task, cut):
    from .goal_evaluation_query_v288 import _reader
    from .instance_data_receipts import observation_receipt
    _reader(db, p)
    cut = _time(cut)
    with db.no_autoflush:
        obs = _row(db, p, Observation, observation.id)
        need(obs.workspace_id == task.workspace_id == p.workspace_id and obs.status == 'processed'
            and obs.entity_id in task.data['entity_ids'] and not (FOREIGN & set(obs.data))
            and not obs.data.get('acquisition_id')
            and not obs.data.get('original_request', {}).get('acquisition_id'))
        # Real reverse witnesses also forbid a mixed/acquisition origin when
        # somebody removes its forward marker. No substitute Acq is created.
        need(not _rows(db, select(Acquisition).where(Acquisition.workspace_id == p.workspace_id,
            Acquisition.data['closing_observation_id'].as_string() == obs.id)))
        need(not _rows(db, select(Exchange).where(Exchange.workspace_id == p.workspace_id,
            Exchange.observation_id == obs.id)))
        receipt = _row(db, p, MqttReceipt, obs.data['mqtt_receipt_id'])
        binding = _row(db, p, MqttBindingRevision, obs.data['mqtt_binding_id'])
        delivery = _row(db, p, MqttReceiptDelivery, obs.data['mqtt_delivery_id'])
        _row(db, p, MqttGateway, receipt.gateway_id)
        reverse = _deliveries(db, p, obs)
        need(len(reverse) == 1 and reverse[0].id == delivery.id and not _legacy_receipts(db, p, obs)
            and receipt.data['schema'] == 'mqtt-receipt-v1' and receipt.status == delivery.status == 'submitted'
            and delivery.receipt_id == receipt.id and delivery.binding_id == binding.id
            and delivery.observation_id == obs.id and binding.gateway_id == receipt.gateway_id
            and binding.entity_id == obs.entity_id and binding.source_id == obs.source_id
            and binding.extractor_id == obs.data['extractor_id']
            and binding.content_hash == mqtt.binding_hash(binding))
        entity = _row(db, p, Entity, obs.entity_id)
        release = _row(db, p, InstanceRelease, task.data['instance_release_id'])
        _row(db, p, Capability, entity.schema_capability_id)
        _row(db, p, Capability, release.package_id)
        source = _row(db, p, Capability, obs.source_id)
        extractor = _row(db, p, Capability, obs.data['extractor_id'])
        _row(db, p, Invocation, obs.data['invocation_id'])
        _row(db, p, Evidence, receipt.evidence_id)
        authorization = _row(db, p, Audit, delivery.data['authorization_id'])
        need(binding.data['instance'] == {'id': task.data['instance_id'], 'release_id': release.id}
            and release.instance_id == task.data['instance_id'] and release.package_id == task.package_id
            and source.kind == 'source' and extractor.kind == 'tool'
            and source.content_hash == source.validated_hash == digest(source.data)
            and extractor.content_hash == extractor.validated_hash == digest(extractor.data))
        # Both readers use the immutable original revision/authorization. A
        # later pause or revision grants no new execution and erases no proof.
        actual = mqtt.guard(db, p, obs, execution=False, result=obs.data['extracted'])
        target = routes.checked_delivery(db, p, delivery, binding, receipt, execution=False)
        need(actual.id == receipt.id and obs.data['mqtt_delivery_target_hash'] == digest(target))
        packet, raw = evidence_bytes(db, p, receipt.evidence_id)
        need(packet.sha256 == receipt.input_sha256 and packet.size_bytes == len(raw))
        saved = _audit(db, p, 'mqtt.packet.saved', receipt.id, receipt.data['received_by'],
            {'input_sha256': receipt.input_sha256, 'status': 'awaiting_object_binding'})
        submitted = _audit(db, p, 'mqtt.delivery.submitted', delivery.id, obs.data['submitted_by'],
            {'observation_id': obs.id, 'revision': delivery.revision})
        received_at = _time(receipt.data['received_at'])
        need(receipt.data['received_by'] == obs.data['submitted_by']
            and _time(obs.observed_at) <= received_at <= _time(receipt.created_at)
            <= _time(saved.created_at) <= _time(obs.created_at)
            <= _time(submitted.created_at) <= cut
            and _time(packet.created_at) <= _time(receipt.created_at)
            and _time(binding.created_at) <= _time(authorization.created_at)
            <= _time(delivery.created_at) <= _time(obs.created_at))
        # This is the independent business observation ACK, not PUBACK or the
        # separate sensor_original Memory. Its reader rechecks extraction SDK.
        claimed = obs.data['data_reception']
        event = _row(db, p, Event, claimed['event_id'])
        memories = _rows(db, select(Memory).where(Memory.workspace_id == p.workspace_id,
            Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence'))
        received = observation_receipt(db, p, obs)['data_reception']
        need(len(memories) == 1 and received['instance_id'] == task.data['instance_id']
            and received['release_id'] == release.id and received['package_id'] == task.package_id
            and received['observation_id'] == obs.id and received['entity_id'] == obs.entity_id
            and digest(claimed) == digest(received) == digest(event.data['data_reception'])
            == digest(memories[0].data['data_reception'])
            and event.kind == 'observation.data' and event.idempotency_key == 'data:' + obs.id
            and event.status == 'received' and _time(event.created_at) <= _time(event.received_at) <= cut
            and _time(memories[0].created_at) <= cut)
        declaration = _test_declaration(db, p, receipt, cut)
        test_origin = None
        if declaration is not None:
            need(type(declaration) is dict and set(declaration) == {'simulation_only', 'test_only', 'test_origin'}
                and declaration['simulation_only'] is True and declaration['test_only'] is True)
            test_origin = MqttTestOriginV297.model_validate(declaration['test_origin']).model_dump(mode='json')
        pins = {'schema': SCHEMA, 'receipt_id': receipt.id, 'gateway_id': receipt.gateway_id,
            'packet_key': receipt.packet_key, 'source_record_id': receipt.source_record_id,
            'input_sha256': receipt.input_sha256, 'packet_evidence_id': packet.id,
            'packet_evidence_sha256': packet.sha256, 'packet_size_bytes': packet.size_bytes,
            'config_hash': receipt.data['config_hash'], 'gateway_version': receipt.data['gateway_version'],
            'topic': receipt.data['topic'], 'received_at': received_at.isoformat(),
            'receipt_created_at': _time(receipt.created_at).isoformat(), 'decoded_hash': digest(receipt.data['decoded']),
            'binding_id': binding.id, 'binding_revision': binding.revision, 'binding_hash': binding.content_hash,
            'instance_id': task.data['instance_id'], 'release_id': release.id, 'package_id': task.package_id,
            'entity_id': obs.entity_id, 'field_links_hash': digest(binding.data['field_links']),
            'field_units_hash': digest(binding.data['field_units']), 'delivery_id': delivery.id,
            'delivery_target_hash': digest(target), 'route_id': delivery.route_id,
            'route_version': delivery.data['route_version'], 'authorization_id': authorization.id,
            'authorization_hash': delivery.data['authorization_hash'], 'saved_audit_id': saved.id,
            'saved_audit_hash': _audit_hash(saved), 'submitted_audit_id': submitted.id,
            'submitted_audit_hash': _audit_hash(submitted), 'test_origin': test_origin}
        pins = MqttOriginPinsV297.model_validate(pins).model_dump(mode='json')
        return {'origin_kind': KIND, 'origin_task_id': None, 'mqtt_origin': pins,
            'simulation_only': test_origin is not None, 'test_only': test_origin is not None}


@checked
def immutable_pins(origin):
    need(origin['origin_kind'] == KIND and origin['origin_task_id'] is None)
    return copy.deepcopy(MqttOriginPinsV297.model_validate(origin['mqtt_origin']).model_dump(mode='json'))


@checked
def lock_rows(origin):
    """Additional actual originals; caller owns common Obs/ACK/Capability locks."""
    pins = immutable_pins(origin)
    rows = [(MqttReceipt, pins['receipt_id']), (MqttGateway, pins['gateway_id']),
        (MqttBindingRevision, pins['binding_id']), (MqttReceiptDelivery, pins['delivery_id']),
        (Evidence, pins['packet_evidence_id']), (Audit, pins['authorization_id']),
        (Audit, pins['saved_audit_id']), (Audit, pins['submitted_audit_id'])]
    if pins['test_origin'] is not None: rows.append((Audit, pins['test_origin']['audit_id']))
    return sorted(set(rows), key=lambda item: (item[0].__tablename__, item[1]))
