"""Pure protocol evidence: no DB, model, device, HTTP or source authority."""
import copy
import hashlib
import inspect
from uuid import UUID

import pytest

from generic_agents import http_observation_mapping_v177 as mapping


TEMP = 'field_0b284697a7fecc97'
WIND = 'field_5ed42b2f4b5b54ba'
TYPE = 'object_0cc42679709d4fdc'


def uid(value): return str(UUID(int=value))


def config():
    scope = {'workspace_id': uid(1), 'environment_id': uid(2), 'instance_id': uid(3),
        'release_id': uid(4), 'package_id': uid(5), 'package_hash': 'a' * 64,
        'entity_type': TYPE, 'source_id': uid(6)}
    links = {'temperature_c': {'field_key': TEMP, 'unit': '°C'},
        'wind_m_s': {'field_key': WIND, 'unit': 'm/s'}}
    declared = {'schema': mapping.CONFIG_SCHEMA,
        'scope': {key: value for key, value in scope.items() if key != 'source_id'},
        'links': copy.deepcopy(links), 'supplier_contract': mapping.SUPPLIER,
        'time_semantics': mapping.TIME}
    source = {'kind': 'source', 'namespace': 'simulation', 'name': 'weather',
        'version': '1.0.0', 'allowed_agent_roles': ['perception', 'data'],
        'execution': {'adapter': 'http', 'endpoint': 'http://fixture/acquisitions',
            'receipt_endpoint': 'http://fixture/acquisitions/receipts', 'side_effect': True},
        'data_policy': {'deployment': 'local', 'simulation': True, 'test_only': True, 'cloud_allowed': False},
        'specification': {'simulation_environment_id': scope['environment_id'],
            'http_observation_mapping_v177': declared,
            'observation_capability': {'fields': [TEMP, WIND], 'modalities': ['numeric'],
                'actions': ['observe'], 'receipt_contract': 'request_asset_terminal_v1'}}}
    schemas = {TYPE: {'type': 'object', 'properties': {
        TEMP: {'type': 'number', 'title': '温度', 'unit': '°C', 'minimum': -50, 'maximum': 100},
        WIND: {'type': 'number', 'title': '风速', 'unit': 'm/s', 'minimum': 0}},
        'required': [], 'additionalProperties': False}}
    return source, scope, schemas, links


def frozen(): return mapping.freeze_mapping(*config())


def request(**changes):
    return {'request_key': 'sensing:' + uid(7) + ':1', 'target_external_id': '演练对象一',
        'fields': [TEMP], 'max_duration_seconds': 30, 'asset_external_id': 'sim-weather-01',
        'action': 'observe', 'max_cost_minor': 0, 'currency': 'CNY'} | changes


def exchange(**changes): return mapping.to_supplier_request(request(**changes), frozen())


def packet(wire, *, value=38, observed_at='2026-10-23T12:00:00.123+08:00'):
    body = wire['request']; key_hash = hashlib.sha256(body['request_key'].encode()).hexdigest()
    command_key, record_id = 'acq-v176:' + key_hash, 'sim-acq-v176:' + key_hash
    values = {'temperature_c': value, 'wind_m_s': 2.5}
    selected = {key: values[key] for key in body['fields']}
    supplier = {**mapping.MARK, 'request_id': command_key, 'device_id': body['asset_external_id'],
        'action': 'read_telemetry', 'status': 'succeeded', 'device_revision': 2,
        'result': {**mapping.MARK, 'id': body['asset_external_id'], 'revision': 2,
            'sampled_at': observed_at, 'time_semantics': mapping.TIME,
            'physical_device_connected': False, 'state': values}}
    command = {'device_id': body['asset_external_id'], 'action': 'read_telemetry',
        'expected_revision': None, 'arguments': {}}
    observation = {**mapping.MARK, 'schema': 'sim-telemetry-observation-v176',
        'source_record_id': record_id, 'request_key': body['request_key'],
        'device_command_key': command_key, 'asset_external_id': body['asset_external_id'],
        'target_external_id': body['target_external_id'], 'attributes': selected,
        'field_units': {key: mapping.UNITS[key] for key in selected},
        'observed_at': observed_at, 'time_semantics': mapping.TIME,
        'source_time_verified': False, 'physical_device_connected': False,
        'image_evidence_ids': [], 'supplier_receipt_hash': mapping.digest(supplier)}
    return {**mapping.MARK, 'schema': mapping.SUPPLIER, 'request_key': body['request_key'],
        'asset_external_id': body['asset_external_id'], 'target_external_id': body['target_external_id'],
        'receipt_id': record_id, 'status': 'completed', 'raw_device_status': 'succeeded',
        'terminal': True, 'no_future_device_activity': True, 'no_future_observation': True,
        'request_body_sha256': wire['body_sha256'], 'request_hash': wire['request_hash'],
        'device_command_key': command_key, 'device_command_sha256': mapping.digest(command),
        'cost_minor': 0, 'currency': 'CNY', 'artifacts': [], 'observation': observation,
        'observation_available': True, 'source_time_verified': False,
        'field_observation_created': False, 'supplier_receipt': supplier, 'problem': None}


def project(raw=None, bound=None, freeze=None):
    bound = bound or exchange()
    return mapping.from_supplier_observation(raw or packet(bound['wire_frozen']), freeze or frozen(),
        bound['domain_frozen'], bound['wire_frozen'])


def test_freeze_binds_actual_source_and_schema_without_source_hash_cycle_or_mutation():
    source, scope, schemas, links = config(); originals = copy.deepcopy((source, scope, schemas, links))
    result = mapping.freeze_mapping(source, scope, schemas, links)
    assert (source, scope, schemas, links) == originals
    assert result['source_id'] == scope['source_id']
    assert result['source_manifest_hash'] == mapping.digest(source)
    assert result['entity_schemas_hash'] == mapping.digest(schemas)
    assert result['mapping'] == source['specification']['http_observation_mapping_v177']
    assert 'source_id' not in result['mapping']['scope']
    assert 'source_manifest_hash' not in result['mapping']
    assert result['freeze_hash'] == mapping.digest({k: v for k, v in result.items() if k != 'freeze_hash'})
    result['field_schemas'][TEMP]['title'] = 'changed'
    assert schemas[TYPE]['properties'][TEMP]['title'] == '温度'


@pytest.mark.parametrize('bad', ['unit', 'unknown_unit', 'raw_unit_alias', 'target_missing',
    'duplicate_target', 'scope_mismatch', 'source_fields', 'wrong_type', 'reference', 'field_coercion'])
def test_freeze_rejects_unconfirmed_scope_fields_units_or_schema_references(bad):
    source, scope, schemas, links = config()
    if bad == 'unit': schemas[TYPE]['properties'][TEMP]['unit'] = 'F'
    if bad == 'unknown_unit': del schemas[TYPE]['properties'][TEMP]['unit']
    if bad == 'raw_unit_alias': links['temperature_c']['unit'] = '摄氏度'
    if bad == 'target_missing': del schemas[TYPE]['properties'][TEMP]
    if bad == 'duplicate_target': links['wind_m_s']['field_key'] = TEMP
    if bad == 'scope_mismatch': scope['release_id'] = uid(99)
    if bad == 'source_fields': source['specification']['observation_capability']['fields'] = ['temperature_c']
    if bad == 'wrong_type': schemas[TYPE]['properties'][TEMP]['type'] = 'string'
    if bad == 'reference': schemas[TYPE]['properties'][TEMP]['$ref'] = 'https://never-fetch.invalid/schema'
    if bad == 'field_coercion': links['temperature_c']['field_key'] = 1
    with pytest.raises(mapping.HTTPObservationMappingError):
        mapping.freeze_mapping(source, scope, schemas, links)


@pytest.mark.parametrize('key,value', [('simulation', 1), ('test_only', False), ('cloud_allowed', True)])
def test_sim_mapping_cannot_grant_field_or_cloud_authority(key, value):
    source, scope, schemas, links = config(); source['data_policy'][key] = value
    with pytest.raises(mapping.HTTPObservationMappingError):
        mapping.freeze_mapping(source, scope, schemas, links)


def test_domain_eight_key_dto_is_separate_and_only_fields_change_in_wire():
    domain = request(fields=[WIND, TEMP], max_duration_seconds=15, max_cost_minor=10)
    original = copy.deepcopy(domain); result = mapping.to_supplier_request(domain, frozen())
    assert domain == original
    wire = result['wire_frozen']; saved = result['domain_frozen']
    assert saved['request'] == domain and wire['request']['fields'] == ['wind_m_s', 'temperature_c']
    assert {k: v for k, v in wire['request'].items() if k != 'fields'} == {k: v for k, v in domain.items() if k != 'fields'}
    assert wire['request_hash'] == mapping.digest(wire['request']) != saved['request_hash']
    assert wire['body'] == mapping.canonical(wire['request'])
    assert wire['body_sha256'] == hashlib.sha256(wire['body'].encode('utf-8')).hexdigest()
    assert '演练对象一' in wire['body']


@pytest.mark.parametrize('change', [{'fields': ['temperature_c']}, {'fields': [TEMP, TEMP]},
    {'fields': [uid(99)]}, {'fields': []}, {'fields': [True]}, {'max_duration_seconds': True},
    {'max_cost_minor': 0.0}, {'max_cost_minor': '0'}, {'extra': 'not allowed'},
    {'action': 'fly_route'}, {'currency': 'USD'}, {'asset_external_id': 'invented-device'}])
def test_domain_request_rejects_aliases_coercion_extras_or_unsupported_supplier_contract(change):
    with pytest.raises(mapping.HTTPObservationMappingError):
        mapping.to_supplier_request(request(**change), frozen())


def test_projection_preserves_original_packet_number_unit_and_clock_without_creating_records():
    bound = exchange(); raw = packet(bound['wire_frozen'], value=38.0)
    before = copy.deepcopy(raw)
    result = project(raw, bound)
    assert raw == before and result['raw_package'] == raw
    assert result['raw_package_hash'] == mapping.digest(raw)
    assert result['mapped_attributes'] == {TEMP: 38.0}
    assert type(result['mapped_attributes'][TEMP]) is float
    assert result['field_units'] == {TEMP: '°C'}
    assert result['observed_at'] == '2026-10-23T12:00:00.123+08:00'
    assert result['temporal_usable'] is True and result['status'] == 'mapped'
    assert result['observation_created'] is result['acquisition_closed'] is result['production_activation'] is False
    assert result['source_time_verified'] is result['field_verified'] is False
    result['raw_package']['supplier_receipt']['result']['state']['temperature_c'] = 1
    assert raw == before


@pytest.mark.parametrize('time', [None, '', 'unknown', '2026-10-23T12:00:00'])
def test_unknown_original_time_is_preserved_never_replaced_by_now(time):
    bound = exchange(); raw = packet(bound['wire_frozen'], observed_at=time)
    result = project(raw, bound)
    assert result['status'] == 'unclocked' and result['temporal_usable'] is False
    assert result['observed_at'] == time and result['mapped_attributes'] == {TEMP: 38}
    assert result['raw_package']['supplier_receipt']['result']['sampled_at'] == time


@pytest.mark.parametrize('kind', ['accepted', 'completed_no_data'])
def test_acknowledged_or_completed_without_data_never_projects_measurements(kind):
    bound = exchange(); raw = packet(bound['wire_frozen'])
    raw.update(status=kind, observation=None, observation_available=False)
    if kind == 'accepted':
        raw.update(raw_device_status='running', terminal=False,
            no_future_device_activity=False, no_future_observation=False)
    result = project(raw, bound)
    assert result['status'] == ('waiting' if kind == 'accepted' else 'no_data')
    assert result['mapped_attributes'] == result['field_units'] == {}
    assert result['observed_at'] is None and result['temporal_usable'] is False


@pytest.mark.parametrize('kind', ['key', 'asset', 'target', 'wire_hash', 'body_bytes', 'command_hash',
    'unit', 'extra_attribute', 'missing_attribute', 'raw_value', 'time', 'physical', 'simulation',
    'terminal', 'supplier_identity', 'supplier_hash', 'device_revision'])
def test_mismatched_original_identity_raw_fields_or_temporal_provenance_is_rejected(kind):
    bound = exchange(); raw = packet(bound['wire_frozen']); obs = raw['observation']
    if kind == 'key': raw['request_key'] += ':changed'
    if kind == 'asset': raw['asset_external_id'] = 'sim-dock-01'
    if kind == 'target': raw['target_external_id'] = 'other-object'
    if kind == 'wire_hash': raw['request_hash'] = 'b' * 64
    if kind == 'body_bytes': raw['request_body_sha256'] = 'b' * 64
    if kind == 'command_hash': raw['device_command_sha256'] = 'b' * 64
    if kind == 'unit': obs['field_units']['temperature_c'] = 'F'
    if kind == 'extra_attribute': obs['attributes']['wind_m_s'] = 2.5
    if kind == 'missing_attribute': del obs['attributes']['temperature_c']
    if kind == 'raw_value': obs['attributes']['temperature_c'] = 38.0  # supplier int remains int
    if kind == 'time': obs['observed_at'] = '2026-10-23T04:00:00.123+00:00'  # same instant, different original spelling
    if kind == 'physical': obs['physical_device_connected'] = True
    if kind == 'simulation': raw['simulation'] = 1
    if kind == 'terminal': raw['terminal'] = False
    if kind == 'supplier_identity': raw['supplier_receipt']['device_id'] = 'sim-dock-01'
    if kind == 'supplier_hash': obs['supplier_receipt_hash'] = 'b' * 64
    if kind == 'device_revision': raw['supplier_receipt']['result']['revision'] = True
    with pytest.raises(mapping.HTTPObservationMappingError):
        project(raw, bound)


@pytest.mark.parametrize('value', [True, '38', None, float('inf'), float('nan'), 10**100])
def test_no_default_value_numeric_coercion_or_invalid_measurement(value):
    bound = exchange(); raw = packet(bound['wire_frozen'])
    raw['supplier_receipt']['result']['state']['temperature_c'] = value
    raw['observation']['attributes']['temperature_c'] = value
    try:
        raw['observation']['supplier_receipt_hash'] = mapping.digest(raw['supplier_receipt'])
    except mapping.HTTPObservationMappingError:
        pass  # Non-JSON numeric values must also be rejected by the real entry.
    with pytest.raises(mapping.HTTPObservationMappingError):
        project(raw, bound)


def test_integer_domain_requires_original_integer_and_respects_schema_constraints():
    source, scope, schemas, links = config()
    schemas[TYPE]['properties'][TEMP]['type'] = 'integer'
    saved = mapping.freeze_mapping(source, scope, schemas, links)
    bound = mapping.to_supplier_request(request(), saved)
    assert project(packet(bound['wire_frozen'], value=38), bound, saved)['mapped_attributes'][TEMP] == 38
    with pytest.raises(mapping.HTTPObservationMappingError):
        project(packet(bound['wire_frozen'], value=38.0), bound, saved)
    with pytest.raises(mapping.HTTPObservationMappingError):
        project(packet(bound['wire_frozen'], value=101), bound, saved)


@pytest.mark.parametrize('part', ['domain', 'wire', 'body', 'freeze', 'scope'])
def test_saved_mapping_and_both_original_requests_are_bound(part):
    saved = frozen(); bound = mapping.to_supplier_request(request(), saved)
    raw = packet(bound['wire_frozen'])
    if part == 'domain': bound['domain_frozen']['request']['max_duration_seconds'] += 1
    if part == 'wire': bound['wire_frozen']['request']['fields'] = ['wind_m_s']
    if part == 'body': bound['wire_frozen']['body'] += '\n'
    if part == 'freeze': saved['field_schemas'][TEMP]['unit'] = 'F'
    if part == 'scope': saved['mapping']['scope']['release_id'] = uid(99)
    with pytest.raises(mapping.HTTPObservationMappingError):
        project(raw, bound, saved)


def test_projection_does_not_change_historical_schema_or_silently_translate_units():
    source, scope, schemas, links = config(); before = copy.deepcopy(schemas)
    saved = mapping.freeze_mapping(source, scope, schemas, links)
    bound = mapping.to_supplier_request(request(), saved)
    project(packet(bound['wire_frozen']), bound, saved)
    assert schemas == before and set(schemas[TYPE]['properties']) == {TEMP, WIND}
    assert 'temperature_c' not in schemas[TYPE]['properties']


def test_module_is_pure_and_borrows_no_sample_mqtt_or_old_observation_authority():
    source = inspect.getsource(mapping)
    assert not any(item in source for item in ('from .db', 'from .api', 'sqlalchemy', 'httpx',
        'simulation_origin_v176', 'mqtt_observations', 'ObservationCreate(', 'datetime.now(', 'time.time('))
