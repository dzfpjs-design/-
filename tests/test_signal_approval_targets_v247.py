"""Pure eligibility/provider contracts; IO spies are not source or ACK evidence."""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator

from generic_agents import signal_approval_targets_v247 as targets
from generic_agents import signal_observation_provider_v247 as provider
from generic_agents.contracts import digest


def test_real_execution_mode_prevents_graph_facade_from_being_manual():
    row = {'execution_mode': 'confirmed_human_workflow',
           'flow': {'steps': [{'id': 'review', 'agent': '人工确认'}]}}
    assert targets.manual(row)
    for mode in ('confirmed_graph_workflow', 'registered_ability_workflow', 'blocked', None):
        assert not targets.manual({**row, 'execution_mode': mode})
    assert not targets.manual({**row, 'flow': {'steps': []}})


@pytest.mark.parametrize('producer,consumer,accepted', [
    ({'type': ['number', 'null']}, {'type': 'number'}, False),
    ({'type': 'integer'}, {'type': 'number'}, True),
    ({'type': 'number'}, {'type': 'integer'}, False),
    ({'type': 'string'}, {'type': 'string', 'pattern': '^approved$'}, False),
    ({'type': 'number', 'minimum': 0}, {'type': 'number', 'minimum': 10}, False),
    ({'type': 'number', 'minimum': 10}, {'type': 'number', 'minimum': 0}, True),
    ({'type': 'object'}, {'type': 'object', 'required': ['value']}, False),
    ({'type': 'object', 'properties': {'value': {'type': 'number'}}, 'required': ['value'], 'additionalProperties': False},
     {'type': 'object', 'properties': {'value': {'type': 'number'}}, 'required': ['value'], 'additionalProperties': False}, True),
    ({'type': 'object', 'properties': {'value': {'type': 'number'}, 'extra': {}}, 'required': ['value'], 'additionalProperties': False},
     {'type': 'object', 'properties': {'value': {'type': 'number'}}, 'additionalProperties': False}, False),
    ({'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {'type': 'object'}},
     {'type': 'array', 'minItems': 2, 'items': {'type': 'object'}}, False),
    ({'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {'type': 'object'}},
     {'type': 'array', 'items': {'type': 'object'}}, True),
    ({'const': 'original'}, {'enum': ['original', 'other']}, True),
    ({'enum': ['original', 'other']}, {'const': 'original'}, False),
])
def test_input_implication_is_conservative_for_all_values_not_one_example(producer, consumer, accepted):
    assert targets.covers(producer, consumer) is accepted


def step(identity, next_id='auto', condition='', else_id='end'):
    return {'id': identity, 'nextId': next_id, 'condition': condition, 'elseId': else_id}


def test_branch_skipped_and_loop_only_producer_is_not_a_mandatory_previous_result():
    straight = targets.dominators([step('first'), step('producer'), step('consumer')])
    assert 'producer' in straight['consumer']
    branch = targets.dominators([step('first', 'producer', 'real bool', 'consumer'),
                                 step('producer'), step('consumer')])
    assert 'producer' not in branch['consumer']
    loop = targets.dominators([step('consumer', 'producer'), step('producer', 'consumer')])
    assert 'producer' not in loop['consumer']
    assert 'consumer' in loop['producer']


@pytest.mark.parametrize('source', ['task_image_ids', 'task_audio_ref', 'perception_frame_ids',
                                  'original_evidence_ids', 'task_scene_refs'])
def test_numeric_input_never_supplies_missing_media_or_materials(source):
    with pytest.raises(HTTPException) as failure:
        targets._source_schema(source, {}, {})
    assert failure.value.status_code == 409


def test_previous_output_must_be_required_and_not_only_an_optional_schema_property():
    node = {'execution': {'bindings': {'value': {'source': 'previous_output', 'step_id': 'prior', 'field': 'value'}}}}
    cap = NS(data={'input_schema': {'type': 'object', 'properties': {'value': {'type': 'number'}}, 'required': ['value']}})
    with pytest.raises(HTTPException):
        targets._step_inputs(node, cap, {}, {'prior': {'type': 'object', 'properties': {'value': {'type': 'number'}}}})
    with pytest.raises(HTTPException):
        targets._step_inputs(node, cap, {}, {'prior': {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': ['number', 'null']}}}})
    targets._step_inputs(node, cap, {}, {'prior': {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'number'}}}})


def test_observation_schema_uses_domain_fields_and_does_not_promise_every_future_sample():
    schema = provider.record_schema({'mapping': {'field_schemas': {'temperature': {'type': 'number', 'unit': '°C'}}}})
    record = schema['items']['properties']
    assert record['version']['type'] == record['processing_version']['type'] == 'string'
    assert record['attributes']['properties'] == {'temperature': {'type': 'number', 'unit': '°C'}}
    assert not record['attributes'].get('required')
    assert targets.covers(schema, {'type': 'array', 'items': {'type': 'object'}})
    assert not targets.covers(schema, {'type': 'array', 'items': {'type': 'object',
        'required': ['attributes'], 'properties': {'attributes': {'type': 'object', 'required': ['temperature']}}}})
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize('field,dynamic,allowed', [
    ('field_temperature', False, True), ('temperature_title', False, False),
    ('field_foreign_depth', False, False), (None, False, False),
    ('field_temperature', True, False)])
def test_aggregate_field_is_fixed_to_this_actual_source_mapping(field, dynamic, allowed):
    bindings = {'records': {'source': 'observation_records'}}
    if dynamic: bindings['field'] = {'source': 'previous_output', 'step_id': 'prior', 'field': 'selected'}
    node = {'execution': {'arguments': {'field': field}, 'bindings': bindings}}
    cap = NS(data={'execution': {'handler': 'aggregate_values'}})
    contract = {'field_units': {'field_temperature': '°C'}}
    if allowed:
        targets.calculation_fields(node, cap, contract)
    else:
        with pytest.raises(HTTPException) as failure:
            targets.calculation_fields(node, cap, contract)
        assert failure.value.status_code == 409


@pytest.fixture
def source_spies(monkeypatch):
    """Explicit IO boundary; independent source/receipt custody is PG's job."""
    from generic_agents import signal_graph_http_source_v177 as http
    from generic_agents import instance_data_receipts, perception_signals
    from generic_agents.models import Observation
    extracted = {'attributes': {'temperature': 27.5}, 'anomalies': [{'field': 'temperature'}]}
    observation = NS(id='observation', entity_id='entity', workspace_id='workspace', source_id='source',
        source_record_id='record', processing_version='http-mapped-v177', attributes={'temperature': 900},
        data={'input_hash': digest('original input')})
    signal = NS(id='signal', instance_id='instance', entity_id='entity', data={'basis_hash': digest('basis')})
    basis = {'observation_id': observation.id, 'extracted': extracted, 'simulation_origin': {'original': True}, 'reported_at': None}
    receipt = {'receipt_hash': digest('ACK'), 'observation_id': observation.id, 'instance_id': 'instance',
        'release_id': 'release', 'extracted_hash': digest(extracted)}
    contract = {'scope': {'instance_id': 'instance', 'release_id': 'release'}, 'field_units': {'temperature': '°C'}}
    origin = {'signal_id': signal.id, 'observation_id': observation.id, 'source_contract': contract,
        'input_hash': observation.data['input_hash'], 'basis_hash': signal.data['basis_hash'], 'receipt_hash': receipt['receipt_hash']}
    task = NS(workspace_id='workspace', data={'instance_id': 'instance', 'instance_release_id': 'release',
        'entity_ids': ['entity'], 'object_snapshots': [{'attributes': {'temperature': 999}}]})
    p = NS(workspace_id='workspace')
    monkeypatch.setattr(provider, 'verify_task', lambda *args: origin)
    monkeypatch.setattr(provider, 'scoped', lambda db, model, identity, p: observation if model is Observation else signal)
    monkeypatch.setattr(http, 'checked_contract', lambda value: copy.deepcopy(value))
    monkeypatch.setattr(http, 'guard', lambda *args, **kwargs: {'simulation': True})
    def historical_signal(*args, **kwargs):
        assert kwargs == {'allow_rejected': False, 'lock_sources': False}
        return copy.deepcopy(basis)
    monkeypatch.setattr(perception_signals, 'checked', historical_signal)
    monkeypatch.setattr(instance_data_receipts, 'observation_receipt', lambda *args: {'data_reception': copy.deepcopy(receipt)})
    return NS(p=p, task=task, receipt=receipt, origin=origin, observation=observation, basis=basis)


def test_provider_does_not_substitute_current_archive_or_raw_cached_attributes(source_spies):
    s = source_spies
    value = provider.records(None, s.p, s.task)[0]
    assert value['attributes'] == {'temperature': 27.5}
    assert value['field_units'] == {'temperature': '°C'} and value['reported_at'] is None
    assert value['version'] == value['processing_version'] == 'http-mapped-v177'
    assert value['physical_state_verified'] is value['source_time_verified'] is False
    assert value['simulation'] is value['not_authoritative'] is True
    assert s.task.data['object_snapshots'][0]['attributes']['temperature'] == 999


@pytest.mark.parametrize('fault', ['missing_ack', 'different_ack', 'different_entity', 'different_input', 'unexpected_field'])
def test_provider_refuses_changed_receipt_scope_input_or_undeclared_field(source_spies, fault):
    s = source_spies
    if fault == 'missing_ack': s.receipt.clear()
    elif fault == 'different_ack': s.receipt['receipt_hash'] = digest('other ACK')
    elif fault == 'different_entity': s.task.data['entity_ids'] = ['other entity']
    elif fault == 'different_input': s.observation.data['input_hash'] = digest('other input')
    elif fault == 'unexpected_field':
        s.basis['extracted']['attributes']['unmapped'] = 1
        s.receipt['extracted_hash'] = digest(s.basis['extracted'])
    with pytest.raises(HTTPException) as failure:
        provider.records(None, s.p, s.task)
    assert failure.value.status_code == 409


@pytest.mark.parametrize('write', [False, True])
def test_provider_forwards_read_or_execution_lock_mode_without_skipping_origin(monkeypatch, write):
    from generic_agents import signal_approval_routing_v247 as routing
    marker = object(); seen = []
    def original(db, p, task, *, lock_sources=False):
        seen.append(lock_sources); return marker
    monkeypatch.setattr(routing, 'checked_task_origin', original)
    assert provider.verify_task(None, None, None, lock_sources=write) is marker
    assert seen == [write]


def test_real_ability_gate_requires_execution_locks_before_other_input_work(monkeypatch):
    from generic_agents import workflow_abilities
    seen = []
    def denied(db, p, task, *, lock_sources=False):
        seen.append(lock_sources)
        raise HTTPException(409, 'original source is damaged')
    monkeypatch.setattr(provider, 'verify_task', denied)
    with pytest.raises(HTTPException) as failure:
        workflow_abilities.checked(None, None, None, None, None, None)
    assert failure.value.detail == 'original source is damaged' and seen == [True]
