"""Synthetic SIM projections through actual functions, not HTTP/PG acceptance.

The saved-source guard, numeric result check and requirements/receipt rebuild
are real. FakeDB/storage, original-acquisition/lineage and graph/quality outbox
boundaries are explicit spies; they prove no physical observation or dispatch.
"""
import copy
from contextlib import nullcontext
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression

from tests.test_simulation_origin_v176 import scene as base_scene, FakeDB
from tests.test_instance_data_receipts_pure import Storage
from generic_agents import agents, instance_data_receipts as receipts, perception_signals as signals
from generic_agents import observation_integrity, entity_release_compat
from generic_agents import original_acquisition_reception_v127 as original_acquisition
from generic_agents import simulation_origin_v176 as origin
from generic_agents.contracts import digest
from generic_agents.instance_data_requirements import build_requirements
from generic_agents.models import Event, Memory
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177


@pytest.fixture
def scene(monkeypatch):
    # The common dispatcher now cold-loads Observation and source by strict
    # UUID before identifying a legacy Sample. Generate those identities at
    # creation, so all original links, requests and hashes use their real IDs;
    # never relabel/reseal already-built source records to satisfy a guard.
    original_put = FakeDB.put
    def put(db, model, identity, **fields):
        if model in {origin.Capability, origin.Observation}:
            identity = str(uuid5(NAMESPACE_URL,
                'simulation-projections-v176/' + model.__tablename__ + '/' + identity))
        return original_put(db, model, identity, **fields)
    monkeypatch.setattr(FakeDB, 'put', put)
    return base_scene.__wrapped__(monkeypatch)


@pytest.fixture
def projected(scene, monkeypatch):
    s = scene
    # This legacy Sample owns no V177 HTTP Exchange or reverse-ledger Audit.
    # Model the Session context boundary without replacing either real guard;
    # Audit predicates below still evaluate against all original Sample audits.
    s.db.no_autoflush = nullcontext()
    s.db.rows[HTTPObservationExchangeV177] = []
    # Reuse the existing strict public select evaluator, extended solely for
    # the literal prefix predicate used to exclude unrelated policy calls.
    def expression(value,row,model):
        if isinstance(value,BinaryExpression) and value.operator is operators.startswith_op:
            left=expression(value.left,row,model); right=expression(value.right,row,model)
            return type(left) is str and type(right) is str and left.startswith(right)
        return Storage._value(s.db,value,row,model)
    s.db._value=expression
    def scalars(statement):
        descriptions=statement.column_descriptions
        assert len(descriptions)==1, 'unsupported synthetic joined select'
        model,projection=descriptions[0]['entity'],descriptions[0]['expr']
        rows=list(s.db.rows.get(model,[]))
        rows=[row for row in rows if all(expression(clause,row,model) for clause in statement._where_criteria)]
        if projection is model:return rows
        assert getattr(projection,'class_',None) is model
        return [getattr(row,projection.key) for row in rows]
    s.db.scalars=scalars
    s.db.scalar = lambda statement: next(iter(s.db.scalars(statement)), None)
    s.db.get = lambda cls, identity: next((row for row in s.db.rows.get(cls, []) if row.id == identity), None)
    def scoped(db, cls, identity, p, lock=False):
        assert db is s.db and p is s.p
        return origin._get(db,p,cls,identity)
    for module in (agents, signals, receipts, observation_integrity):
        monkeypatch.setattr(module, 'scoped', scoped)
    monkeypatch.setattr(original_acquisition, 'context', lambda db,p,observation: None)
    monkeypatch.setattr(original_acquisition, 'hold', lambda db,p,observation,**kwargs: None)
    def lineage(db,p,instance,release,entity,**kwargs):
        assert db is s.db and p is s.p
        assert instance.id == release.instance_id and entity.namespace == instance.namespace
        assert entity.schema_capability_id == release.package_id
        return {'entity_id':entity.id,'target_release_id':release.id}
    monkeypatch.setattr(entity_release_compat, 'require_entity', lineage)
    monkeypatch.setattr(receipts, 'require_entity', lineage)

    extracted = {'attributes': {'temperature_c':38.0}, 'anomalies': [
        {'field':'temperature_c','value':38.0,'rule_id':'warm','severity':'warning'}],
        'method':'registered_numeric_rules'}
    output_schema = {'type':'object','required':['attributes','anomalies','method'],
        'properties':{'attributes':{'type':'object'},'anomalies':{'type':'array'},
                      'method':{'const':'registered_numeric_rules'}}}
    s.extractor.release_version = '1.0.0'
    s.extractor.data.update(output_schema=output_schema, specification={'rules':[
        {'id':'warm','field':'temperature_c','operator':'gt','threshold':35.0,'severity':'warning'}]})
    s.extractor.content_hash = s.extractor.validated_hash = digest(s.extractor.data)
    pin = {'capability_id':s.extractor.id,'capability_hash':s.extractor.content_hash,
        'release_version':'1.0.0','agent_role':'perception','output_schema':copy.deepcopy(output_schema),
        'bindings':{'attributes':{'source':'object_attributes'}}}
    s.snapshot.update(businesses=[{'id':'business','name':'合成模拟业务','available':True,'issues':[],
        'flow':{'steps':[{'id':'step','name':'核对模拟温度','agent':'多模感知智能体','execution':pin}]}}],
        object_types=[],entity_schemas={},resources=[],sensor_sources=[],materials=[],
        automation={},equipment={},general_issues=[])
    s.snapshot['data_requirements'] = build_requirements(s.snapshot)
    s.release.release_number = 1
    s.release.configuration_hash = digest(s.snapshot)
    s.package.data = {'specification': {'configuration_hash':s.release.configuration_hash,
        'data_requirements_schema':receipts.REQUIREMENTS,
        'data_requirements_hash':s.snapshot['data_requirements']['requirements_hash']}}
    s.package.content_hash = s.package.validated_hash = digest(s.package.data)
    s.env.data['configuration_hash'] = s.release.configuration_hash
    s.definition.data['environment_hash'] = digest(s.env.data)
    s.definition.data['pins'][s.extractor.id] = s.extractor.content_hash
    s.sample.data.update(environment_hash=digest(s.env.data),definition_hash=digest(s.definition.data))
    for record in s.db.rows[origin.Audit]:
        if record.action == 'simulation.environment.created_v176':
            record.data = {'record_hash':digest(s.env.data)}
        elif record.action == 'simulation.definition.created_v176':
            record.data = {'record_hash':digest(s.definition.data)}
        elif record.action == 'simulation.sample.received_v176':
            record.data['record_hash'] = digest(origin.receipt_basis(s.sample.data))
    s.processing_call = s.db.put(origin.Invocation,'processing-call',capability_id=s.extractor.id,
        idempotency_key='observation:'+s.observation.id+':attempt:1',status='succeeded',
        data={'capability_hash':s.extractor.content_hash,'input_hash':digest({'attributes':s.observation.data['attributes']}),
              'output':extracted,'job_id':s.process.id,'role':'perception','side_effect':False,'lease_token':'process-lease'})
    s.observation.version = 2; s.entity.version = 1
    s.entity.data = {}; s.entity.external_id = 'synthetic-weather'
    s.observation.status = 'processed'; s.process.status = 'succeeded'
    s.observation.data.update(invocation_id=s.processing_call.id,extracted=copy.deepcopy(extracted),model_receipt={})
    s.label = origin.guard(s.db,s.p,s.observation)
    payload = {'observation_id':s.observation.id,'entity_id':s.entity.id,'source_id':s.source.id,
        'invocation_id':s.processing_call.id,'evidence_ids':[], 'extracted':copy.deepcopy(extracted),
        'observed_at':s.observation.observed_at.isoformat(),'candidate_only':False,'model_receipt':{},
        'simulation':True,'origin_kind':'simulated_equipment','field_verified':False,
        'simulation_origin':copy.deepcopy(s.label)}
    s.data_event = s.db.put(Event,'data-event',kind='observation.data',idempotency_key='data:'+s.observation.id,
        status='received',data=copy.deepcopy(payload))
    s.anomaly_event = s.db.put(Event,'anomaly-event',kind='perception.anomaly',idempotency_key='anomaly:'+s.observation.id,
        status='received',data=copy.deepcopy(payload))
    return s


def test_receipt_and_memory_cold_read_keep_same_origin_and_original_release(projected):
    s = projected
    s.instance.active_release_id = 'a-new-unrelated-release'
    value = receipts.receive_observation(s.db,s.p,s.data_event,s.observation)
    assert value['simulation_origin'] == s.label and value['simulation'] is True
    assert value['field_verified'] is False and value['release_id'] == s.release.id
    assert value['receipt_hash'] == digest({key:item for key,item in value.items() if key != 'receipt_hash'})
    s.data_event.data['data_reception'] = copy.deepcopy(value)
    memory = s.db.put(Memory,'memory',status='candidate',data={**copy.deepcopy(s.data_event.data),
        'event_id':s.data_event.id,'type':'observation_evidence','not_authoritative':True})
    before = copy.deepcopy((s.observation.data,s.data_event.data,memory.data))
    assert receipts.observation_receipt(s.db,s.p,s.observation) == {'data_reception':value}
    assert (s.observation.data,s.data_event.data,memory.data) == before
    memory.data.pop('simulation_origin')
    with pytest.raises(HTTPException):
        receipts.observation_receipt(s.db,s.p,s.observation)


@pytest.mark.parametrize('key,value', [('simulation',False),('field_verified',True),
    ('origin_kind','field_sensor'),('simulation_origin',None)])
def test_data_event_cannot_lose_or_replace_simulation_markers(projected,key,value):
    s = projected; s.data_event.data[key] = value
    with pytest.raises(HTTPException):
        receipts.receive_observation(s.db,s.p,s.data_event,s.observation)


def test_anomaly_basis_and_checked_reader_preserve_source_not_a_manual_claim(projected):
    s = projected
    s.instance.active_release_id = s.release.id
    basis = signals.freeze(s.db,s.p,s.anomaly_event,s.instance,s.entity)
    assert basis['schema'] == 'reported-anomaly-v1'
    assert basis['simulation_origin'] == s.label and basis['simulation'] is True
    assert basis['origin_kind'] == 'simulated_equipment' and basis['field_verified'] is False
    signal = NS(id='signal',workspace_id=s.p.workspace_id,event_id=s.anomaly_event.id,
        instance_id=s.instance.id,entity_id=s.entity.id,data={'basis':basis,'basis_hash':digest(basis)})
    assert signals.checked(s.db,s.p,signal) == basis
    basis.pop('simulation_origin'); signal.data['basis_hash'] = digest(basis)
    with pytest.raises(HTTPException):
        signals.checked(s.db,s.p,signal)


def test_anomaly_event_with_removed_origin_cannot_freeze_as_legacy(projected):
    s = projected; s.anomaly_event.data.pop('simulation_origin')
    with pytest.raises((HTTPException,ValueError)):
        signals.event_report(s.db,s.p,s.anomaly_event)


def test_actual_agent_graph_emits_marked_data_and_anomaly_events(projected,monkeypatch):
    s = projected
    s.process.status = 'running'; s.observation.status = 'received'
    s.db.info['worker_lease'] = {'job_id':s.process.id,'lease_token':s.process.lease_token}
    from generic_agents import graph_event_dispatch, candidate_watches, quality
    def invoke(db,p,identity,role,inputs,key):
        assert db is s.db and p is s.p and identity==s.extractor.id and role=='perception'
        assert inputs=={'attributes':s.observation.data['attributes']}
        assert key==s.processing_call.idempotency_key
        return s.processing_call
    monkeypatch.setattr(agents,'invoke',invoke)
    monkeypatch.setattr(graph_event_dispatch,'capture_source',lambda *args: None)
    monkeypatch.setattr(candidate_watches,'enqueue_new',lambda *args: None)
    monkeypatch.setattr(quality,'changed',lambda *args: None)
    emitted = []
    def enqueue(db,p,kind,key,payload):
        assert db is s.db and p is s.p
        emitted.append((kind,key,copy.deepcopy(payload)))
        return NS(id='new-'+kind,data=payload)
    monkeypatch.setattr(agents,'enqueue_event',enqueue)
    result = agents.perception_agent(s.db,s.p,s.observation,s.extractor.id)
    assert result['status'] == 'processed'
    assert [item[0] for item in emitted] == ['observation.data','perception.anomaly']
    for _,_,payload in emitted:
        assert payload['simulation_origin'] == s.label
        assert payload['simulation'] is True and payload['field_verified'] is False
        assert payload['origin_kind'] == 'simulated_equipment'


def test_non_simulation_projection_checks_do_not_change_legacy_payload(projected):
    s = projected
    s.observation.data.pop('simulation_origin')
    s.source.data['specification'] = {}; s.db.rows[origin.Environment] = []; s.db.rows[origin.Sample] = []
    legacy = {key:item for key,item in s.data_event.data.items() if key not in {
        'simulation','origin_kind','field_verified','simulation_origin'}}
    before = copy.deepcopy(legacy)
    receipts._event_matches(NS(workspace_id=s.p.workspace_id,kind='observation.data',
        idempotency_key=s.data_event.idempotency_key,data=legacy),s.observation)
    assert signals._simulation_projection(s.db,s.p,s.observation,legacy) is None
    assert legacy == before
