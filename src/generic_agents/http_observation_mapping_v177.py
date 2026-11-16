"""Pure, explicit HTTP measurement/domain mapping. Hashes are not permissions.

Callers must cold-check actual Source/Asset/release/Goal/approval authority and
persist the exchange before I/O. This module does no I/O and creates no domain
record. Existing MQTT and V176 Sample authority are never borrowed or changed.
"""
import copy
import hashlib
import json
import math
import re
from datetime import datetime
from functools import wraps
from uuid import UUID

from jsonschema import Draft202012Validator

from .device_acquisition_adapter_v176 import UNITS, normalize_request

CONFIG_SCHEMA = 'http-observation-mapping-v177'
FREEZE_SCHEMA = 'http-observation-mapping-freeze-v177'
SUPPLIER = 'sim-acquisition-receipt-v176'
TIME = 'simulator_sample_time'
MARK = {'simulation': True, 'origin_kind': 'simulated_equipment', 'field_verified': False}
_SCOPE = {'workspace_id', 'environment_id', 'instance_id', 'release_id', 'package_id', 'package_hash', 'entity_type'}
_REQUEST = {'request_key', 'target_external_id', 'fields', 'max_duration_seconds',
    'asset_external_id', 'action', 'max_cost_minor', 'currency'}
_CONFIG = {'schema', 'scope', 'links', 'supplier_contract', 'time_semantics'}
_FREEZE = {'schema', 'mapping', 'mapping_hash', 'source_id', 'source_manifest_hash',
    'entity_schemas_hash', 'field_schemas', 'allowed_actions', 'freeze_hash'}
_HASH = re.compile(r'^[a-f0-9]{64}$')


class HTTPObservationMappingError(ValueError):
    pass


def _validated(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPObservationMappingError:
            raise
        except (TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise HTTPObservationMappingError('原字段映射或回执结构不符合冻结合同') from None
    return checked


def need(value, message='原来源、字段映射或补采包不符合冻结合同；不建立观测或关闭补采'):
    if not value:
        raise HTTPObservationMappingError(message)


def _json(value, ancestors=()):
    kind = type(value)
    if value is None or kind in {bool, int}:
        return
    if kind is float:
        need(math.isfinite(value)); return
    if kind is str:
        need('\x00' not in value); value.encode('utf-8', 'strict'); return
    need(kind in {dict, list} and not any(value is parent for parent in ancestors))
    parents = (*ancestors, value)
    if kind is dict:
        for key, item in value.items():
            need(type(key) is str); _json(key, parents); _json(item, parents)
    else:
        for item in value: _json(item, parents)


def canonical(value):
    try:
        _json(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise HTTPObservationMappingError('原内容不是有限、无隐式转换的JSON') from None


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _text(value, limit=200):
    need(type(value) is str and 0 < len(value) <= limit and value.strip() == value and bool(value))
    canonical(value)


def _uuid(value):
    try:
        need(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise HTTPObservationMappingError('必须使用已确认的真实小写UUID身份') from None


def _hash(value):
    need(type(value) is str and _HASH.fullmatch(value) is not None)


def _closed(value, keys):
    need(type(value) is dict and set(value) == keys)


def _marks(value):
    need(type(value) is dict and all(type(value.get(k)) is type(v) and value[k] == v for k, v in MARK.items()))


def _scope(value):
    _closed(value, _SCOPE)
    for key in _SCOPE - {'package_hash', 'entity_type'}:
        _uuid(value[key])
    _hash(value['package_hash']); _text(value['entity_type'], 100)


def _links(value):
    need(type(value) is dict and 1 <= len(value) <= 8)
    targets = []
    for raw, spec in value.items():
        need(type(raw) is str and raw in UNITS)
        _closed(spec, {'field_key', 'unit'}); _text(spec['field_key'], 100); _text(spec['unit'], 40)
        need(spec['unit'] == UNITS[raw], '单位须与原供应商声明精确相同，不换算或改写单位')
        targets.append(spec['field_key'])
    need(len(set(targets)) == len(targets), '多个测量字段不能覆盖同一领域字段')


def _config(value):
    _closed(value, _CONFIG)
    need(value['schema'] == CONFIG_SCHEMA and value['supplier_contract'] == SUPPLIER
        and value['time_semantics'] == TIME)
    _scope(value['scope']); _links(value['links'])


def _no_refs(value):
    if type(value) is dict:
        need(not {'$ref', '$dynamicRef', '$recursiveRef'} & set(value),
            '此纯数值映射不解析Schema引用，不读取外部定义')
        for item in value.values(): _no_refs(item)
    elif type(value) is list:
        for item in value: _no_refs(item)


def _field_schema(schema, unit):
    need(type(schema) is dict and schema.get('type') in {'number', 'integer'}
        and type(schema.get('unit')) is str and schema['unit'] == unit,
        '领域字段必须为原已声明数值字段，单位未知或不同均不能自动映射')
    _no_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise HTTPObservationMappingError('领域数值Schema无法核对') from None


@_validated
def freeze_mapping(source_manifest, scope, schemas, links):
    """Validate a saved Source against actual caller-supplied release schemas.

    Source configuration contains no Source ID/hash. The returned external freeze
    adds the registered source_id and whole manifest hash without a hash cycle.
    A caller must still prove those IDs/hashes are actual current records.
    """
    canonical(source_manifest); canonical(scope); canonical(schemas); canonical(links)
    _closed(scope, _SCOPE | {'source_id'}); _uuid(scope['source_id'])
    configured_scope = {key: copy.deepcopy(scope[key]) for key in _SCOPE}
    _scope(configured_scope); _links(links)
    need(type(source_manifest) is dict and source_manifest.get('kind') == 'source')
    spec, execution, policy = (source_manifest.get(key) for key in ('specification', 'execution', 'data_policy'))
    need(type(spec) is type(execution) is type(policy) is dict)
    configured = spec.get('http_observation_mapping_v177'); _config(configured)
    expected = {'schema': CONFIG_SCHEMA, 'scope': configured_scope, 'links': copy.deepcopy(links),
        'supplier_contract': SUPPLIER, 'time_semantics': TIME}
    need(canonical(configured) == canonical(expected), 'Source中的映射与本次明确确认范围不同')
    need(spec.get('simulation_environment_id') == scope['environment_id']
        and policy.get('simulation') is True and policy.get('test_only') is True
        and policy.get('cloud_allowed') is False and policy.get('deployment') == 'local'
        and 'perception' in source_manifest.get('allowed_agent_roles', []),
        '此供应商合同仅用于明确隔离的模拟来源，不授予现场状态权威')
    need(execution.get('adapter') == 'http' and type(execution.get('side_effect')) is bool)
    for key in ('endpoint', 'receipt_endpoint'): _text(execution.get(key), 2000)
    declaration = spec.get('observation_capability'); need(type(declaration) is dict)
    fields, actions = declaration.get('fields'), declaration.get('actions')
    targets = {item['field_key'] for item in links.values()}
    need(type(fields) is list and len(fields) == len(set(fields)) and set(fields) == targets)
    need(declaration.get('modalities') == ['numeric']
        and declaration.get('receipt_contract', 'request_asset_terminal_v1') == 'request_asset_terminal_v1')
    need(type(actions) is list and bool(actions) and len(actions) == len(set(actions))
        and set(actions) <= {'read_existing', 'observe'}
        and all(execution['side_effect'] is (action != 'read_existing') for action in actions))
    _no_refs(schemas)
    need(type(schemas) is dict and scope['entity_type'] in schemas)
    selected = schemas[scope['entity_type']]
    need(type(selected) is dict and selected.get('type') == 'object' and type(selected.get('properties')) is dict)
    field_schemas = {}
    for raw, link in links.items():
        schema = selected['properties'].get(link['field_key']); _field_schema(schema, link['unit'])
        field_schemas[link['field_key']] = copy.deepcopy(schema)
    frozen = {'schema': FREEZE_SCHEMA, 'mapping': copy.deepcopy(configured), 'mapping_hash': digest(configured),
        'source_id': scope['source_id'], 'source_manifest_hash': digest(source_manifest),
        'entity_schemas_hash': digest(schemas), 'field_schemas': field_schemas,
        'allowed_actions': copy.deepcopy(actions)}
    return {**frozen, 'freeze_hash': digest(frozen)}


def _frozen(value):
    canonical(value); _closed(value, _FREEZE)
    need(value['schema'] == FREEZE_SCHEMA); _config(value['mapping']); _uuid(value['source_id'])
    for key in ('mapping_hash', 'source_manifest_hash', 'entity_schemas_hash', 'freeze_hash'): _hash(value[key])
    need(value['mapping_hash'] == digest(value['mapping'])
        and value['freeze_hash'] == digest({key: item for key, item in value.items() if key != 'freeze_hash'}))
    actions = value['allowed_actions']
    need(type(actions) is list and bool(actions) and len(set(actions)) == len(actions)
        and set(actions) <= {'read_existing', 'observe'})
    need(type(value['field_schemas']) is dict
        and set(value['field_schemas']) == {item['field_key'] for item in value['mapping']['links'].values()})
    for item in value['mapping']['links'].values():
        _field_schema(value['field_schemas'][item['field_key']], item['unit'])
    return value


def _domain_request(request, frozen):
    """Strict independent domain DTO: its fields are EntitySchema keys, not raw enums."""
    canonical(request); _closed(request, _REQUEST)
    key = request['request_key']
    need(type(key) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', key) is not None)
    _text(request['target_external_id'], 200); _text(request['asset_external_id'], 64)
    fields = request['fields']
    need(type(fields) is list and 1 <= len(fields) <= 8
        and all(type(field) is str for field in fields) and len(set(fields)) == len(fields)
        and set(fields) <= set(frozen['field_schemas']), '请求字段须为原领域字段，不接收raw字段或未授权字段')
    need(type(request['max_duration_seconds']) is int and 1 <= request['max_duration_seconds'] <= 86400)
    need(type(request['max_cost_minor']) is int and 0 <= request['max_cost_minor'] <= 10**12)
    need(type(request['action']) is str and request['action'] in frozen['allowed_actions'])
    need(type(request['currency']) is str and request['currency'] in {'CNY', 'USD'})


@_validated
def to_supplier_request(domainRequest8keys, mapping):
    """Only rename fields. Send wire_frozen.body verbatim, not another JSON encoding."""
    frozen = _frozen(mapping); _domain_request(domainRequest8keys, frozen)
    inverse = {item['field_key']: raw for raw, item in frozen['mapping']['links'].items()}
    wire = copy.deepcopy(domainRequest8keys)
    wire['fields'] = [inverse[field] for field in domainRequest8keys['fields']]
    body = canonical(wire)
    # The legacy raw-field DTO applies here only, after explicit inverse mapping.
    # Its validator is pure; no ledger, device, source or network is constructed.
    try:
        checked, exact, signature = normalize_request(body.encode('utf-8'), wire['request_key'])
    except Exception:
        raise HTTPObservationMappingError('映射后的供应商8字段请求不符合原设备协议；不派发') from None
    need(canonical(checked.model_dump()) == body and exact == body)
    base = {'mapping_freeze_hash': frozen['freeze_hash']}
    domain = {'schema': 'http-domain-request-freeze-v177', **base,
        'request': copy.deepcopy(domainRequest8keys), 'request_hash': digest(domainRequest8keys)}
    supplier = {'schema': 'http-supplier-request-freeze-v177', **base,
        'request': wire, 'request_hash': digest(wire), 'body': body, 'body_sha256': signature}
    return {'domain_frozen': domain, 'wire_frozen': supplier}


def _exchange(mapping, domain, wire):
    _closed(domain, {'schema', 'mapping_freeze_hash', 'request', 'request_hash'})
    _closed(wire, {'schema', 'mapping_freeze_hash', 'request', 'request_hash', 'body', 'body_sha256'})
    rebuilt = to_supplier_request(domain['request'], mapping)
    need(canonical(domain) == canonical(rebuilt['domain_frozen'])
        and canonical(wire) == canonical(rebuilt['wire_frozen']),
        '原领域请求、实际供应商正文或映射冻结值不同')
    return wire['request']


def _clock(value):
    need(value is None or type(value) is str, '原时间须为原文本或null，不能隐式转换')
    if value is None or value == '': return False
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (ValueError, TypeError, OverflowError):
        return False


@_validated
def from_supplier_observation(rawPackage, mapping, domainFrozen, wireFrozen):
    """Check only protocol consistency and project values; never issue domain authority."""
    frozen = _frozen(mapping); request = _exchange(frozen, domainFrozen, wireFrozen)
    need(len(canonical(rawPackage).encode('utf-8')) <= 1024 * 1024)
    _marks(rawPackage)
    need(rawPackage.get('schema') == SUPPLIER
        and rawPackage.get('request_key') == request['request_key']
        and rawPackage.get('asset_external_id') == request['asset_external_id']
        and rawPackage.get('target_external_id') == request['target_external_id']
        and rawPackage.get('request_hash') == wireFrozen['request_hash']
        and rawPackage.get('request_body_sha256') == wireFrozen['body_sha256'])
    key_hash = hashlib.sha256(request['request_key'].encode('utf-8')).hexdigest()
    device_key, record_id = 'acq-v176:' + key_hash, 'sim-acq-v176:' + key_hash
    need(rawPackage.get('receipt_id') == record_id and rawPackage.get('device_command_key') == device_key
        and type(rawPackage.get('cost_minor')) is int and rawPackage['cost_minor'] == 0
        and rawPackage.get('currency') == request['currency']
        and rawPackage.get('artifacts') == [] and rawPackage.get('source_time_verified') is False
        and rawPackage.get('field_observation_created') is False)
    status, observation = rawPackage.get('status'), rawPackage.get('observation')
    need(status in {'accepted', 'completed', 'completed_no_data'})
    terminal = status != 'accepted'
    need(rawPackage.get('terminal') is terminal and rawPackage.get('no_future_device_activity') is terminal
        and rawPackage.get('no_future_observation') is terminal
        and (rawPackage.get('raw_device_status') == 'succeeded' if terminal
             else rawPackage.get('raw_device_status') in {'accepted', 'running'}))
    result = {'schema': 'http-observation-projection-v177', **MARK, 'test_only': True,
        'mapping_freeze_hash': frozen['freeze_hash'], 'mapping_hash': frozen['mapping_hash'],
        'source_id': frozen['source_id'], 'source_manifest_hash': frozen['source_manifest_hash'],
        'scope': copy.deepcopy(frozen['mapping']['scope']),
        'domain_request_hash': domainFrozen['request_hash'], 'wire_request_hash': wireFrozen['request_hash'],
        'wire_body_sha256': wireFrozen['body_sha256'], 'raw_package': copy.deepcopy(rawPackage),
        'raw_package_hash': digest(rawPackage), 'status': 'waiting' if status == 'accepted' else 'no_data',
        'mapped_attributes': {}, 'field_units': {}, 'observed_at': None, 'temporal_usable': False,
        'time_semantics': TIME, 'source_time_verified': False,
        'observation_created': False, 'acquisition_closed': False, 'production_activation': False}
    if status != 'completed':
        need(observation is None and rawPackage.get('observation_available') is False)
        return result
    need(rawPackage.get('observation_available') is True)
    _marks(observation)
    supplier = rawPackage.get('supplier_receipt'); _marks(supplier)
    original = supplier.get('result'); _marks(original)
    command = {'device_id': request['asset_external_id'], 'action': 'read_telemetry',
        'expected_revision': None, 'arguments': {}}
    need(rawPackage.get('device_command_sha256') == digest(command)
        and supplier.get('request_id') == device_key and supplier.get('device_id') == request['asset_external_id']
        and supplier.get('action') == 'read_telemetry' and supplier.get('status') == 'succeeded'
        and type(supplier.get('device_revision')) is int and supplier['device_revision'] >= 1
        and original.get('id') == request['asset_external_id']
        and original.get('revision') == supplier['device_revision'] and type(original.get('revision')) is int
        and original.get('time_semantics') == TIME and original.get('physical_device_connected') is False)
    need(observation.get('schema') == 'sim-telemetry-observation-v176'
        and observation.get('source_record_id') == record_id and observation.get('request_key') == request['request_key']
        and observation.get('device_command_key') == device_key
        and observation.get('asset_external_id') == request['asset_external_id']
        and observation.get('target_external_id') == request['target_external_id']
        and observation.get('time_semantics') == TIME and observation.get('source_time_verified') is False
        and observation.get('physical_device_connected') is False and observation.get('image_evidence_ids') == []
        and observation.get('supplier_receipt_hash') == digest(supplier))
    attributes, units = observation.get('attributes'), observation.get('field_units')
    need(type(attributes) is dict and type(units) is dict
        and set(attributes) == set(units) == set(request['fields']) and type(original.get('state')) is dict)
    mapped, mapped_units = {}, {}
    for raw in request['fields']:
        link = frozen['mapping']['links'][raw]; field, unit = link['field_key'], link['unit']
        value = attributes[raw]
        need(type(value) in {int, float} and math.isfinite(value) and abs(value) <= 1e15
            and canonical(value) == canonical(original['state'].get(raw))
            and type(units[raw]) is str and units[raw] == unit)
        schema = frozen['field_schemas'][field]
        need(schema['type'] != 'integer' or type(value) is int, '整数字段不能通过浮点或布尔值隐式转换')
        need(not list(Draft202012Validator(schema).iter_errors(value)), '原数值不符合已冻结领域字段约束')
        mapped[field] = copy.deepcopy(value); mapped_units[field] = unit
    observed = observation.get('observed_at')
    need(canonical(observed) == canonical(original.get('sampled_at')), '采样时间不能换成接收时间或另一次读数')
    usable = _clock(observed)
    return {**result, 'status': 'mapped' if usable else 'unclocked',
        'mapped_attributes': mapped, 'field_units': mapped_units,
        'observed_at': copy.deepcopy(observed), 'temporal_usable': usable,
        'source_record_id': record_id}
