"""Synthetic adapter tests; Root runs them, without database/provider/file IO.

Only storage lookup, evidence-byte access and existing lineage adapters are
fake. The real requirements builder, receipt rebuild, JSON-schema output gate
and observation/Invocation integrity validator all run unchanged.
"""
import copy
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace as N
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList, Grouping, Null
from sqlalchemy.sql.schema import Column

from generic_agents import instance_data_receipts as subject
from generic_agents import observation_integrity
from generic_agents.contracts import digest
from generic_agents.instance_data_requirements import build_requirements
from generic_agents.models import Audit, Capability, DomainInstance, Entity, Event, InstanceRelease, Invocation, Job, Memory, Observation
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177
from generic_agents.simulation_models_v176 import SimulationEnvironmentV176, SimulationSampleV176


class Storage:
    def __init__(self):
        self.rows = {}; self.event = None; self.memories = []; self.source_release = None
        self.no_autoflush = nullcontext()

    def put(self, model, row):
        # The real common source dispatcher cold-loads Observation/Source by
        # UUID before selecting a family. Assign IDs and empty manifest parts
        # at creation, before any original request, output or hash is built.
        if model in {Capability, Observation}:
            row.id = str(uuid5(NAMESPACE_URL, 'state-receipt-v245/' + model.__tablename__ + '/' + row.id))
        if model is Capability:
            row.data.setdefault('specification', {})
            row.data.setdefault('data_policy', {})
        self.rows[model, row.id] = row
        return row

    def _value(self, expression, row, model):
        if isinstance(expression, BindParameter):
            return expression.value
        if isinstance(expression, Null):
            return None
        if isinstance(expression, Grouping):
            return self._value(expression.element, row, model)
        if isinstance(expression, Column):
            assert expression.table is model.__table__, 'foreign synthetic query column'
            return getattr(row, expression.key) if row is not None else None
        if isinstance(expression, BooleanClauseList):
            values = [self._value(clause, row, model) for clause in expression.clauses]
            if expression.operator is operators.and_: return all(values)
            if expression.operator is operators.or_: return any(values)
            raise AssertionError('unsupported synthetic boolean operator')
        if isinstance(expression, BinaryExpression):
            left = self._value(expression.left, row, model)
            right = self._value(expression.right, row, model)
            op = expression.operator
            if op is operators.json_getitem_op:
                assert type(right) is str, 'unsupported synthetic JSON path'
                if left is None: return None  # PostgreSQL missing nested JSON paths yield NULL.
                assert isinstance(left, dict), 'unsupported synthetic JSON container'
                return left.get(right)
            if op is operators.eq: return left is not None and right is not None and left == right
            if op is operators.is_: return left is right
            if op is operators.is_not: return left is not right
            if op is operators.in_op:
                assert isinstance(right, (list, tuple)), 'unsupported synthetic IN operand'
                return left is not None and left in right
            raise AssertionError('unsupported synthetic binary operator')
        raise AssertionError('unsupported synthetic query expression')

    def _selected(self, statement):
        descriptions = statement.column_descriptions
        assert len(descriptions) == 1, 'unsupported synthetic multi-column query'
        model, projection = descriptions[0]['entity'], descriptions[0]['expr']
        assert model in {Observation, Audit, Event, Memory, Capability, Entity, Job,
            HTTPObservationExchangeV177, SimulationEnvironmentV176, SimulationSampleV176}, \
            'unsupported synthetic query model'
        assert not statement._order_by_clauses, 'unsupported synthetic ordering'
        assert projection is model or (getattr(projection, 'class_', None) is model
            and getattr(projection, 'key', None) == 'id'), 'unsupported synthetic scalar projection'
        for clause in statement._where_criteria:
            self._value(clause, None, model)
        if model is Event:
            rows = [self.event] if self.event is not None else []
        elif model is Memory:
            rows = self.memories
        else:
            rows = [row for (kind, _), row in self.rows.items() if kind is model]
        rows = [row for row in rows if all(self._value(clause, row, model) for clause in statement._where_criteria)]
        if statement._limit_clause is not None:
            limit = statement._limit_clause.value
            assert type(limit) is int and limit >= 0, 'unsupported synthetic limit'
            rows = rows[:limit]
        if projection is model:
            return rows
        return [row.id for row in rows]

    def scalar(self, statement):
        rows = self._selected(statement)
        return rows[0] if rows else None

    def scalars(self, statement):
        return self._selected(statement)


def scoped(db, model, identity, p, lock=False):
    row = db.rows.get((model, identity))
    if row is None or row.workspace_id != p.workspace_id:
        raise HTTPException(404, 'synthetic scope rejection')
    return row


def lineage(db, p, instance, release, entity, *, lock_sources=False):
    assert lock_sources is False
    assert instance.workspace_id == release.workspace_id == entity.workspace_id == p.workspace_id
    assert instance.id == release.instance_id and entity.namespace == instance.namespace
    return {'entity_id': entity.id, 'source_package_id': entity.schema_capability_id,
            'target_release_id': release.id, 'target_configuration_hash': release.configuration_hash}


def seal(scene):
    snapshot = scene.release.data['snapshot']
    snapshot['data_requirements'] = build_requirements(snapshot)
    scene.release.configuration_hash = digest(snapshot)
    scene.package.data = {'specification': {'configuration_hash': scene.release.configuration_hash,
        'data_requirements_schema': subject.REQUIREMENTS,
        'data_requirements_hash': snapshot['data_requirements']['requirements_hash']}}
    scene.package.content_hash = scene.package.validated_hash = digest(scene.package.data)


@pytest.fixture
def scene():
    db = Storage(); p = N(workspace_id='workspace', user_id='actor')
    instance = db.put(DomainInstance, N(id='instance', workspace_id=p.workspace_id,
        namespace='synthetic-instance', active_release_id='a-later-unrelated-release'))
    schema = {'type': 'object', 'properties': {'attributes': {'type': 'object',
        'properties': {'temperature': {'type': 'integer'}}, 'required': ['temperature'],
        'additionalProperties': False}, 'anomalies': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['attributes', 'anomalies'], 'additionalProperties': False}
    source = db.put(Capability, N(id='source', workspace_id=p.workspace_id, kind='source',namespace='synthetic_legacy',
        data={'description': 'synthetic source'}, content_hash=''))
    source.content_hash = digest(source.data)
    extractor = db.put(Capability, N(id='extractor', workspace_id=p.workspace_id,namespace='synthetic_legacy',
        data={'output_schema': schema}, content_hash='', release_version='1.0.0'))
    extractor.content_hash = digest(extractor.data)
    pin = {'capability_id': extractor.id, 'capability_hash': extractor.content_hash,
        'release_version': extractor.release_version, 'agent_role': 'perception',
        'output_schema': copy.deepcopy(schema), 'bindings': {'attributes': {'source': 'object_attributes'}}}
    snapshot = {'businesses': [{'id': 'business', 'name': '合成业务', 'available': True, 'issues': [],
        'flow': {'steps': [{'id': 'step', 'name': '合成提取', 'agent': '多模感知智能体', 'execution': pin}]}}],
        'entity_schemas': {}, 'object_types': [], 'resources': [], 'sensor_sources': [], 'materials': [],
        'automation': {}, 'equipment': {}, 'general_issues': []}
    package = db.put(Capability, N(id='package', workspace_id=p.workspace_id, kind='domain_package',
        namespace=instance.namespace, data={}, content_hash='', validated_hash=''))
    release = db.put(InstanceRelease, N(id='release', workspace_id=p.workspace_id, instance_id=instance.id,
        package_id=package.id, status='prepared', release_number=1,
        configuration_hash='', data={'snapshot': snapshot, 'data_requirements_schema': subject.REQUIREMENTS}))
    entity = db.put(Entity, N(id='entity', workspace_id=p.workspace_id, namespace=instance.namespace,
        schema_capability_id=package.id, entity_type='synthetic-type', data={'name': 'synthetic'}))
    extracted = {'attributes': {'temperature': 7}, 'anomalies': []}
    inputs = {'attributes': {'temperature': 7}, 'evidence_ids': ['evidence']}
    call = db.put(Invocation, N(id='invocation', workspace_id=p.workspace_id, capability_id=extractor.id,
        status='succeeded', data={'capability_hash': extractor.content_hash,
            'input_hash': digest(inputs), 'output': copy.deepcopy(extracted)}))
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    original = {'source_id': source.id, 'source_record_id': 'record', 'processing_version': 'v1',
        'entity_id': entity.id, 'business_day': None, 'observed_at': when.isoformat(),
        'extractor_id': extractor.id, **copy.deepcopy(inputs)}
    observation = db.put(Observation, N(id='observation', workspace_id=p.workspace_id, source_id=source.id,
        source_record_id='record', processing_version='v1', entity_id=entity.id, business_day=None,
        observed_at=when, status='processed', version=2, data={**copy.deepcopy(inputs),
            'original_request': original, 'input_hash': digest(original), 'extractor_id': extractor.id,
            'invocation_id': call.id, 'extracted': copy.deepcopy(extracted), 'model_receipt': {}}))
    event = N(id='event', workspace_id=p.workspace_id, kind='observation.data',
        idempotency_key='data:' + observation.id, status='pending_delivery', data={
            'observation_id': observation.id, 'entity_id': entity.id, 'source_id': source.id,
            'invocation_id': call.id, 'extracted': copy.deepcopy(extracted), 'evidence_ids': ['evidence']})
    value = N(db=db, p=p, instance=instance, source=source, extractor=extractor, package=package,
        release=release, entity=entity, call=call, observation=observation, event=event, file_hash='a' * 64)
    seal(value); db.source_release = release; db.event = event
    with patch.object(subject, 'scoped', scoped), patch.object(observation_integrity, 'scoped', scoped), \
         patch.object(subject, 'generated_release', lambda db, p, entity: db.source_release), \
         patch.object(subject, 'require_entity', lineage), \
         patch.object(observation_integrity, 'evidence_bytes', lambda db, p, identity: (N(sha256=value.file_hash), b'synthetic')):
        yield value


def receive(scene):
    return subject.receive_observation(scene.db, scene.p, scene.event, scene.observation)


def acknowledge(scene, receipt):
    scene.event.data = {**scene.event.data, 'data_reception': copy.deepcopy(receipt)}
    scene.event.status = 'received'
    scene.db.memories = [N(workspace_id=scene.p.workspace_id, status='candidate', data={
        **copy.deepcopy(scene.event.data), 'event_id': scene.event.id, 'type': 'observation_evidence',
        'not_authoritative': True})]


def test_receive_is_idempotent_compact_and_cold_query_is_readonly_after_human_review(scene):
    assert scene.db.scalar(select(Observation).where(Observation.id == scene.observation.id,
        Observation.workspace_id == 'foreign')) is None
    with pytest.raises(AssertionError, match='unsupported synthetic query model'):
        scene.db.scalar(select(DomainInstance))
    with pytest.raises(AssertionError, match='unsupported synthetic binary operator'):
        scene.db.scalars(select(Audit).where(Audit.object_id.like('foreign%')))
    receipt = receive(scene); before = copy.deepcopy(scene.observation.data)
    assert receive(scene) == receipt and scene.observation.data == before and scene.observation.version == 2
    assert receipt['requirements_hash'] == scene.release.data['snapshot']['data_requirements']['requirements_hash']
    assert receipt['release_id'] == scene.release.id and receipt['declaration_status'] == 'declared'
    assert receipt['declared_outputs'][0]['step_id'] == 'step'
    assert 'output_schema' not in receipt['declared_outputs'][0] and 'data_requirements' not in receipt
    assert receipt['not_authoritative'] is True and receipt['can_execute'] is receipt['can_delete'] is False
    acknowledge(scene, receipt)
    scene.observation.version += 1
    scene.observation.data['human_verification'] = {'decision': 'confirmed', 'input_hash': digest({
        'extracted': scene.observation.data['extracted'], 'evidence_ids': ['evidence'], 'invocation_id': scene.call.id})}
    frozen = copy.deepcopy((scene.observation.data, scene.event.data, scene.db.memories[0].data))
    assert subject.observation_receipt(scene.db, scene.p, scene.observation) == {'data_reception': receipt}
    assert subject.query_context(scene.db, scene.p, [scene.entity.id]) is None
    assert (scene.observation.data, scene.event.data, scene.db.memories[0].data) == frozen


@pytest.mark.parametrize('part', ['snapshot', 'package_schema', 'package_hash', 'release_marker', 'config', 'package_content'])
def test_new_release_cannot_lose_one_marker_or_self_resign_one_side(scene, part):
    if part == 'snapshot': del scene.release.data['snapshot']['data_requirements']
    elif part == 'package_schema': del scene.package.data['specification']['data_requirements_schema']
    elif part == 'package_hash': scene.package.data['specification']['data_requirements_hash'] = 'f' * 64
    elif part == 'release_marker': scene.release.data['data_requirements_schema'] = None
    elif part == 'config': scene.release.configuration_hash = 'f' * 64
    else: scene.package.content_hash = 'f' * 64
    if part.startswith('package_') and part != 'package_content':
        scene.package.content_hash = scene.package.validated_hash = digest(scene.package.data)
    with pytest.raises((HTTPException, ValueError)): subject.verify_release(scene.db, scene.p, scene.release)


def test_truly_legacy_release_stays_unmodified_but_leftover_receipt_cannot_downgrade(scene):
    del scene.release.data['snapshot']['data_requirements']; del scene.release.data['data_requirements_schema']
    scene.release.configuration_hash = digest(scene.release.data['snapshot'])
    scene.package.data = {'specification': {'configuration_hash': scene.release.configuration_hash}}
    scene.package.content_hash = scene.package.validated_hash = digest(scene.package.data)
    before = copy.deepcopy(scene.observation.data)
    assert subject.verify_release(scene.db, scene.p, scene.release) is None
    assert receive(scene) is None and scene.observation.data == before
    assert subject.observation_receipt(scene.db, scene.p, scene.observation) == {}
    scene.event.data['data_reception'] = {'schema': subject.SCHEMA}
    with pytest.raises(HTTPException): receive(scene)


@pytest.mark.parametrize('field', ['observation_id', 'entity_id', 'source_id', 'invocation_id', 'extracted', 'evidence_ids'])
def test_exact_event_payload_must_match_original_observation_before_receipt(scene, field):
    scene.event.data[field] = [] if field == 'evidence_ids' else {'changed': True} if field == 'extracted' else 'foreign'
    with pytest.raises(HTTPException): receive(scene)
    assert 'data_reception' not in scene.observation.data


def test_invocation_and_actual_evidence_are_cold_checked_not_merely_receipt_hashes(scene):
    receipt = receive(scene); acknowledge(scene, receipt)
    scene.observation.data['extracted']['attributes']['temperature'] = 8
    scene.event.data['extracted']['attributes']['temperature'] = 8
    with pytest.raises(HTTPException): subject.observation_receipt(scene.db, scene.p, scene.observation)
    scene.observation.data['extracted']['attributes']['temperature'] = 7
    scene.event.data['extracted']['attributes']['temperature'] = 7
    scene.file_hash = 'b' * 64
    with pytest.raises(HTTPException): subject.observation_receipt(scene.db, scene.p, scene.observation)


@pytest.mark.parametrize('target', ['observation', 'event', 'memory', 'memory_payload', 'duplicate_memory', 'missing_memory', 'pending_event'])
def test_each_persisted_link_is_required_even_after_self_rehash(scene, target):
    receipt = receive(scene); acknowledge(scene, receipt)
    if target in ('observation', 'event', 'memory'):
        container = {'observation': scene.observation.data, 'event': scene.event.data,
                     'memory': scene.db.memories[0].data}[target]
        changed = container['data_reception']; changed['requirements_hash'] = 'f' * 64
        changed['receipt_hash'] = digest({key: value for key, value in changed.items() if key != 'receipt_hash'})
    elif target == 'memory_payload': scene.db.memories[0].data['extracted'] = {'attributes': {}, 'anomalies': []}
    elif target == 'duplicate_memory': scene.db.memories.append(copy.deepcopy(scene.db.memories[0]))
    elif target == 'missing_memory': scene.db.memories.clear()
    else: scene.event.status = 'pending_delivery'
    with pytest.raises(HTTPException): subject.observation_receipt(scene.db, scene.p, scene.observation)


def test_unplanned_capability_is_honestly_not_declared_and_never_filled(scene):
    scene.release.data['snapshot']['businesses'][0]['flow']['steps'][0]['execution']['capability_id'] = 'other-capability'
    seal(scene)
    receipt = receive(scene)
    assert receipt['declaration_status'] == 'not_declared' and receipt['declared_outputs'] == []
    assert receipt['can_execute'] is False and receipt['physical_state_verified'] is False


@pytest.mark.parametrize('change', ['version', 'output_schema'])
def test_declared_capability_cannot_drift_or_admit_schema_invalid_actual_output(scene, change):
    if change == 'version':
        scene.extractor.release_version = '1.0.1'
    else:
        bad = {'attributes': {'temperature': 'not-an-integer'}, 'anomalies': []}
        scene.call.data['output'] = copy.deepcopy(bad)
        scene.observation.data['extracted'] = copy.deepcopy(bad)
        scene.event.data['extracted'] = copy.deepcopy(bad)
    with pytest.raises(HTTPException): receive(scene)
    assert 'data_reception' not in scene.observation.data


def test_frozen_destination_uses_existing_lineage_and_not_active_release(scene):
    scene.entity.schema_capability_id = 'old-package'
    scene.observation.data['local_instance'] = {'id': scene.instance.id, 'release_id': scene.release.id,
        'entity_source_package_id': 'old-package', 'entity_definition_hash': digest(lineage(
            scene.db, scene.p, scene.instance, scene.release, scene.entity))}
    receipt = receive(scene)
    assert receipt['release_id'] == 'release' and receipt['release_id'] != scene.instance.active_release_id
    scene.observation.data['local_instance']['entity_definition_hash'] = 'f' * 64
    with pytest.raises(HTTPException): receive(scene)


def test_conflicting_destinations_foreign_scope_and_orphan_new_package_fail_closed(scene):
    scene.observation.data['local_instance'] = {'id': scene.instance.id, 'release_id': scene.release.id}
    scene.observation.data['reported_rule_contract'] = {'instance_id': scene.instance.id, 'release_id': 'other'}
    with pytest.raises(HTTPException): receive(scene)
    del scene.observation.data['reported_rule_contract']; scene.observation.workspace_id = 'foreign'
    with pytest.raises(HTTPException): receive(scene)
    with pytest.raises(HTTPException): subject.observation_receipt(scene.db, scene.p, scene.observation)
    scene.observation.workspace_id = scene.p.workspace_id; scene.db.source_release = None
    with pytest.raises(HTTPException): subject.query_context(scene.db, scene.p, [scene.entity.id])
