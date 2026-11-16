"""Real managed packet/processing/ACK with a seeded same-namespace Env witness.

The Env and its independent creation Audit are explicit PG fixture records,
not proof of the V176 publication/API or a valid simulator Sample. MQTT route,
prospective declaration, raw capture, actual worker lease/SDK and data receiver
are not replaced. Capture is direct, not a real broker/PUBACK or V288 Goal.
"""
import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import simulation_origin_v176 as simulation
from generic_agents import goal_ack_origin_mqtt_v297 as mqtt
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, DomainInstance, InstanceRelease, Invocation, Job, MqttReceiptDelivery, Observation
from generic_agents.simulation_models_v176 import SimulationEnvironmentV176 as Environment
from generic_agents.simulation_models_v176 import SimulationDefinitionV176 as Definition, SimulationSampleV176 as Sample
from generic_agents.worker import process_once, deliver_events
from tests.test_mqtt_binding_routes import managed_pair, pending_jobs
from tests.test_mqtt_observations import save


def original(client, account, monkeypatch, *, declaration=True):
    fixture, destinations, _routes = managed_pair(client, account, monkeypatch)
    gateway = fixture[0]; p = fixture[3]; release_view, _instance, obj, _field, _rule = destinations[0]
    if declaration:
        response = client.post('/api/mqtt/gateways/' + gateway['id'] + '/test-origin', json={
            'request_id': str(uuid4()), 'expected_gateway_version': gateway['version'],
            'expected_config_hash': gateway['config_hash'], 'source_label': '同空间MQTT独立协议测试',
            'basis_type': 'manufacturer_parameters', 'basis_notes': '软件报文；不是V176样本或物理现场观测。'})
        assert response.status_code == 201, response.text
    with Session(engine()) as db:
        release = db.get(InstanceRelease, release_view['id']); instance = db.get(DomainInstance, release.instance_id)
        value = {'schema': 'simulation-environment-v176', **simulation._MARK,
            'instance_id': instance.id, 'release_id': release.id, 'namespace': instance.namespace,
            'package_id': release.package_id, 'configuration_hash': release.configuration_hash,
            'created_by': p.user_id, 'note': '独立PG分类fixture；不声明V176发布或样本成功。'}
        environment = Environment(id=str(uuid4()), workspace_id=p.workspace_id, instance_id=instance.id,
            release_id=release.id, actor_id=p.user_id, data=value)
        db.add(environment); db.flush()
        db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id,
            action='simulation.environment.created_v176', object_id=environment.id,
            data={'record_hash': digest(value)}))
        env_id = environment.id; db.commit()
    receipt, _raw = save(fixture, record='independent_mqtt_namespace_v298')
    for identity in pending_jobs(receipt.id): assert process_once(identity, deliver=False)
    with Session(engine()) as db:
        deliveries = list(db.scalars(select(MqttReceiptDelivery).where(
            MqttReceiptDelivery.receipt_id == receipt.id)))
        obs = next(db.get(Observation, row.observation_id) for row in deliveries
            if db.get(Observation, row.observation_id).entity_id == obj['id'])
        extraction = db.scalar(select(Job).where(Job.kind == 'process_mqtt_observation',
            Job.data['observation_id'].as_string() == obs.id))
        assert extraction.status == 'queued' and not obs.data.get('invocation_id')
        target = SimpleNamespace(workspace_id=p.workspace_id, package_id=release_view['package_id'],
            data={'entity_ids': [obj['id']], 'instance_id': release_view['instance_id'],
                'instance_release_id': release_view['id']})
        return SimpleNamespace(p=p, observation_id=obs.id, job_id=extraction.id, env_id=env_id, target=target)


def test_real_declared_managed_mqtt_is_not_v176_and_completes_original_extraction_and_ack(client, account, monkeypatch):
    s = original(client, account, monkeypatch)
    with Session(engine()) as db:
        obs = db.get(Observation, s.observation_id); before = copy.deepcopy(obs.data)
        count = db.scalar(select(func.count()).select_from(Invocation))
        assert simulation.guard(db, s.p, obs, execution=False) is None
        assert obs.data == before and not db.new and not db.dirty and not db.deleted
        assert db.scalar(select(func.count()).select_from(Invocation)) == count
    assert process_once(s.job_id, deliver=False)
    with Session(engine()) as db:
        obs = db.get(Observation, s.observation_id)
        assert db.get(Job, s.job_id).status == 'succeeded' and obs.status == 'processed'
        assert 'simulation_origin' not in obs.data and obs.data.get('invocation_id')
    deliver_events(workspace_id=s.p.workspace_id, observation_id=s.observation_id, event_kind='observation.data')
    with Session(engine()) as db:
        obs = db.get(Observation, s.observation_id)
        origin = mqtt.checked_origin(db, s.p, obs, task=s.target, cut=db.scalar(select(func.clock_timestamp())))
        assert origin['simulation_only'] is True and origin['test_only'] is True
        assert origin['origin_task_id'] is None and db.get(Environment, s.env_id) is not None
        assert not db.new and not db.dirty and not db.deleted


def test_mqtt_without_prospective_declaration_cannot_bypass_namespace_environment(client, account, monkeypatch):
    s = original(client, account, monkeypatch, declaration=False)
    with Session(engine()) as db:
        obs = db.get(Observation, s.observation_id)
        with pytest.raises(HTTPException): simulation.guard(db, s.p, obs)
        assert not obs.data.get('invocation_id') and not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('field', ['mqtt_receipt_id', 'mqtt_binding_hash', 'mqtt_delivery_id'])
def test_deleted_forward_mqtt_fields_are_not_a_namespace_legacy_escape(client, account, monkeypatch, field):
    s = original(client, account, monkeypatch)
    with Session(engine()) as db:
        obs = db.get(Observation, s.observation_id)
        obs.data = {key: value for key, value in obs.data.items() if key != field}; db.flush()
        with pytest.raises(HTTPException): simulation.guard(db, s.p, obs)
        assert not obs.data.get('invocation_id')


def test_independent_linked_sample_cannot_be_reclassified_as_passive_mqtt(client, account, monkeypatch):
    s = original(client, account, monkeypatch)
    with Session(engine()) as db:
        definition = Definition(id=str(uuid4()), workspace_id=s.p.workspace_id, environment_id=s.env_id,
            actor_id=s.p.user_id, status='paused', data={'fixture': 'negative witness only, not a valid definition'})
        db.add(definition); db.flush()
        db.add(Sample(workspace_id=s.p.workspace_id, definition_id=definition.id, actor_id=s.p.user_id,
            request_id=str(uuid4()), status='failed', observation_id=s.observation_id,
            data={'fixture': 'negative independent Sample link only'}))
        db.flush(); obs = db.get(Observation, s.observation_id)
        with pytest.raises(HTTPException): simulation.guard(db, s.p, obs)
        assert not obs.data.get('invocation_id')


def test_mixed_acquisition_marker_cannot_use_declared_mqtt_namespace_path(client, account, monkeypatch):
    s = original(client, account, monkeypatch)
    with Session(engine()) as db:
        obs = db.get(Observation, s.observation_id)
        obs.data = {**obs.data, 'acquisition_id': str(uuid4())}; db.flush()
        with pytest.raises(HTTPException): simulation.guard(db, s.p, obs)
        assert not obs.data.get('invocation_id')
