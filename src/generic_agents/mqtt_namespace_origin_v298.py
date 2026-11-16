"""Classify an independently declared MQTT packet beside a V176 environment.

No V176 origin, Sample, SDK or ACK is manufactured. This pre-extraction proof
uses the original raw packet/delivery and prospective test declaration; normal
MQTT execution/reception guards still run after classification returns True.
"""
from fastapi import HTTPException
from sqlalchemy import func, select

from . import goal_ack_origin_mqtt_v297 as cold
from . import mqtt_binding_routes as routes, mqtt_observations as mqtt
from .contracts import digest
from .evidence import evidence_bytes
from .models import (Acquisition, Audit, Capability, DomainInstance, Entity, Evidence, InstanceRelease,
    Job, MqttBindingRevision, MqttGateway, MqttReceipt, MqttReceiptDelivery, Observation)
from .simulation_models_v176 import SimulationEnvironmentV176 as Environment, SimulationSampleV176 as Sample
from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange

ERROR = '此命名空间中的MQTT来源、原件分发或预先测试声明无法核对；未冒充V176样本或授予执行'
FOREIGN = frozenset({'simulation_origin', 'http_mapped_origin_v177', 'http_mapped_receipt_evidence_id_v177',
    'reported_rule_contract', 'reported_contract', 'native_instance_id', 'native_packet'})


def need(value):
    if not value: raise HTTPException(409, ERROR)


def _execution(db, p, obs):
    from .leases import fenced, ownership
    lease = ownership(db)
    need(fenced(db) and type(lease) is dict and bool(lease.get('job_id')))
    job = cold._row(db, p, Job, lease['job_id'])
    need(job.kind == 'process_mqtt_observation' and job.status == 'running'
        and job.idempotency_key == 'extract:' + obs.id and job.data['observation_id'] == obs.id
        and job.data['actor_id'] == p.user_id == obs.data['submitted_by']
        and job.lease_token == lease.get('lease_token') and bool(job.lease_token)
        and cold._time(job.lease_until) > db.scalar(select(func.clock_timestamp())))


def _checked(db, p, observation, *, source, entity, environments, execution):
    from . import simulation_origin_v176 as original
    from .mqtt_test_origin_v297 import checked_declaration
    need(type(execution) is bool and len(environments) == 1 and entity is not None)
    obs = cold._row(db, p, Observation, observation.id)
    actual_source = cold._row(db, p, Capability, obs.source_id)
    obj = cold._row(db, p, Entity, obs.entity_id)
    need(source.id == actual_source.id and entity.id == obj.id and actual_source.kind == 'source'
        and actual_source.content_hash == actual_source.validated_hash == digest(actual_source.data)
        and not (FOREIGN & set(obs.data)) and not obs.data.get('acquisition_id')
        and not obs.data.get('original_request', {}).get('acquisition_id'))
    spec = actual_source.data.get('specification', {}); policy = actual_source.data.get('data_policy', {})
    need(type(spec) is dict and type(policy) is dict and 'simulation_environment_id' not in spec
        and not ('simulation' in policy and policy['simulation'] is not False)
        and policy.get('origin_kind') != 'simulated_equipment'
        and actual_source.data['execution']['adapter'] == 'mqtt_source')
    for model, predicate in ((Sample, Sample.observation_id == obs.id),
            (Acquisition, Acquisition.data['closing_observation_id'].as_string() == obs.id),
            (Exchange, Exchange.observation_id == obs.id)):
        need(not cold._rows(db, select(model).where(model.workspace_id == p.workspace_id, predicate)))
    environment = cold._row(db, p, Environment, environments[0].id)
    value = environment.data
    need(type(value) is dict and set(value) == original._ENVIRONMENT
        and value['schema'] == 'simulation-environment-v176')
    original._marks(value)
    original._audit(db, p, 'simulation.environment.created_v176', environment,
        {'record_hash': original._hash(value)})
    instance = cold._row(db, p, DomainInstance, environment.instance_id)
    env_release = cold._row(db, p, InstanceRelease, environment.release_id)
    need(value['created_by'] == environment.actor_id and value['instance_id'] == instance.id
        and value['release_id'] == env_release.id and env_release.instance_id == instance.id
        and value['package_id'] == env_release.package_id and value['configuration_hash'] == env_release.configuration_hash
        and value['namespace'] == instance.namespace == obj.namespace)
    receipt = cold._row(db, p, MqttReceipt, obs.data['mqtt_receipt_id'])
    binding = cold._row(db, p, MqttBindingRevision, obs.data['mqtt_binding_id'])
    delivery = cold._row(db, p, MqttReceiptDelivery, obs.data['mqtt_delivery_id'])
    cold._row(db, p, MqttGateway, receipt.gateway_id)
    cold._row(db, p, Capability, obj.schema_capability_id)
    extractor = cold._row(db, p, Capability, obs.data['extractor_id'])
    cold._row(db, p, Evidence, receipt.evidence_id)
    cold._row(db, p, Audit, delivery.data['authorization_id'])
    destination = binding.data['instance']
    release = cold._row(db, p, InstanceRelease, destination['release_id'])
    reverse = cold._deliveries(db, p, obs)
    need(len(reverse) == 1 and reverse[0].id == delivery.id and not cold._legacy_receipts(db, p, obs)
        and destination == {'id': instance.id, 'release_id': release.id}
        and release.instance_id == instance.id and binding.entity_id == obj.id
        and binding.gateway_id == receipt.gateway_id and binding.source_id == obs.source_id
        and binding.extractor_id == extractor.id and binding.content_hash == mqtt.binding_hash(binding)
        and extractor.kind == 'tool' and extractor.content_hash == extractor.validated_hash == digest(extractor.data)
        and receipt.status == delivery.status == 'submitted' and receipt.data['schema'] == 'mqtt-receipt-v1'
        and delivery.receipt_id == receipt.id and delivery.binding_id == binding.id
        and delivery.observation_id == obs.id and obs.data['mqtt_binding_hash'] == binding.content_hash)
    if 'local_instance' in obs.data:
        local = obs.data['local_instance']
        need(local['id'] == instance.id and local['release_id'] == release.id)
    packet, raw = evidence_bytes(db, p, receipt.evidence_id)
    need(packet.sha256 == receipt.input_sha256 and packet.size_bytes == len(raw))
    saved = cold._audit(db, p, 'mqtt.packet.saved', receipt.id, receipt.data['received_by'],
        {'input_sha256': receipt.input_sha256, 'status': 'awaiting_object_binding'})
    submitted = cold._audit(db, p, 'mqtt.delivery.submitted', delivery.id, obs.data['submitted_by'],
        {'observation_id': obs.id, 'revision': delivery.revision})
    need(receipt.data['received_by'] == obs.data['submitted_by']
        and cold._time(obs.observed_at) <= cold._time(receipt.data['received_at']) <= cold._time(receipt.created_at)
        <= cold._time(saved.created_at) <= cold._time(obs.created_at) <= cold._time(submitted.created_at))
    declaration = checked_declaration(db, p, receipt)
    need(type(declaration) is dict and set(declaration) == {'simulation_only', 'test_only', 'test_origin'}
        and declaration['simulation_only'] is True and declaration['test_only'] is True)
    # Authenticate the exact raw packet, revision, target and authorization.
    # Passing the actual execution flag preserves current owner/IAM/revoke
    # guards instead of treating historical custody as execution permission.
    if execution: _execution(db, p, obs)
    actual = mqtt.guard(db, p, obs, execution=execution)
    target = routes.checked_delivery(db, p, delivery, binding, receipt, execution=execution)
    need(actual.id == receipt.id and obs.data['mqtt_delivery_target_hash'] == digest(target))
    return True


def is_independent_mqtt(db, p, observation, *, source, entity, environments, execution):
    try:
        return _checked(db, p, observation, source=source, entity=entity,
            environments=environments, execution=execution)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError,
            RecursionError, OverflowError, UnicodeError):
        need(False)
