"""Pure data requirements derived from an already compiled instance snapshot.

This additive system-design contract is not a document fact, a published
capability check, permission, an observation or a verified result. The caller
owns compilation/authentication. Existing object definitions are copied apart
from operational output contracts; no object relevance, identity, units,
required fields, retention policy or deletion authority is guessed here.
"""
import copy
import hashlib
import json
import math
import re


SCHEMA = 'instance-data-requirements-v1'
DEPENDENCIES = ('businesses', 'entity_schemas', 'object_types', 'resources',
                'sensor_sources', 'materials', 'automation', 'equipment', 'general_issues')
ROLES = {'业务智能体': 'business', '多模感知智能体': 'perception', '数据管理智能体': 'data'}
_HASH = re.compile(r'^[a-f0-9]{64}$')
_VERSION = re.compile(r'^\d+\.\d+\.\d+$')


def _need(value, code):
    if not value:
        raise ValueError(code)


def _safe(value, ancestors=None):
    if ancestors is None:
        ancestors = set()
    if type(value) in (dict, list):
        identity = id(value)
        _need(identity not in ancestors, 'data_requirements_json_cycle')
        ancestors.add(identity)
        try:
            if type(value) is dict:
                for key, item in value.items():
                    _need(type(key) is str, 'data_requirements_json_key')
                    _safe(key, ancestors); _safe(item, ancestors)
            else:
                for item in value:
                    _safe(item, ancestors)
        finally:
            ancestors.remove(identity)
    elif type(value) is str:
        try:
            value.encode('utf-8', errors='strict')
        except UnicodeError:
            raise ValueError('data_requirements_unicode') from None
        _need('\x00' not in value, 'data_requirements_nul')
    elif type(value) is float:
        _need(math.isfinite(value), 'data_requirements_nonfinite')
    else:
        _need(value is None or type(value) in (bool, int), 'data_requirements_json_value')


def _bytes(value):
    try:
        _safe(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8', errors='strict')
    except (UnicodeError, RecursionError, OverflowError, TypeError):
        raise ValueError('data_requirements_unsafe_json') from None


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _text(value):
    return type(value) is str and bool(value.strip())


def _issues(value):
    _need(type(value) is list and all(_text(item) for item in value), 'data_requirements_issues')
    return copy.deepcopy(value)


def _output(step):
    _need(type(step) is dict and _text(step.get('id')) and _text(step.get('name'))
          and type(step.get('agent')) is str and step['agent'] in ROLES,
          'data_requirements_automatic_step')
    pin = step.get('execution')
    _need(type(pin) is dict and _text(pin.get('capability_id'))
          and type(pin.get('capability_hash')) is str and _HASH.fullmatch(pin['capability_hash'])
          and type(pin.get('release_version')) is str and _VERSION.fullmatch(pin['release_version'])
          and pin.get('agent_role') == ROLES[step['agent']]
          and type(pin.get('output_schema')) is dict and type(pin.get('bindings')) is dict,
          'data_requirements_frozen_output_contract')
    schema = pin['output_schema']
    properties = schema.get('properties', {})
    _need(type(properties) is dict and all(type(name) is str and type(value) in (dict, bool)
          for name, value in properties.items()), 'data_requirements_output_properties')
    # Do not resolve/flatten refs, infer semantic names or fabricate requiredness.
    # The entire original schema still constrains every actual runtime value.
    return {'step_id': step['id'], 'step_name': step['name'], 'agent_role': pin['agent_role'],
        'capability_id': pin['capability_id'], 'capability_hash': pin['capability_hash'],
        'release_version': pin['release_version'], 'output_schema': copy.deepcopy(schema),
        'output_schema_hash': _digest(schema), 'input_bindings': copy.deepcopy(pin['bindings']),
        # Stable display order survives JSONB's reordering of object keys. The
        # original schema values, refs and required lists are kept unchanged.
        'fields': [{'name': name, 'schema': copy.deepcopy(properties[name])} for name in sorted(properties)],
        'storage': 'existing_evidence_and_receipts', 'status': 'candidate'}


def _business(value, general_issues):
    _need(type(value) is dict and _text(value.get('id')) and _text(value.get('name')),
          'data_requirements_business')
    issues = [*copy.deepcopy(general_issues), *_issues(value.get('issues'))]
    usable = value.get('available') is True and not issues
    outputs = []
    if usable:
        flow = value.get('flow')
        _need(type(flow) is dict and type(flow.get('steps')) is list, 'data_requirements_flow')
        steps = flow['steps']; seen = set(); manual_count = 0
        for step in steps:
            _need(type(step) is dict and _text(step.get('id')) and step['id'] not in seen,
                  'data_requirements_step_identity')
            seen.add(step['id'])
            if step.get('agent') == '人工确认':
                manual_count += 1
            else:
                outputs.append(_output(step))
        summary = (f'从冻结能力契约派生{len(outputs)}项候选输出；'
            f'{manual_count}项人工步骤仍保存非结构化结果回执，不为其编造输出字段。')
    else:
        if not issues:
            issues.append('业务尚未具备执行条件')
        summary = '业务存在阻塞，未从其中的能力声明生成可用输出字段；原配置与问题保留。'
    return {'id': value['id'], 'name': value['name'], 'status': 'derived' if usable else 'blocked',
            'issues': issues, 'outputs': outputs, 'summary': summary}


def build_requirements(snapshot: dict) -> dict:
    """Derive a deterministic design; never mutate or authenticate the input."""
    _need(type(snapshot) is dict and all(key in snapshot for key in DEPENDENCIES),
          'data_requirements_compiled_dependencies')
    try:
        inputs = {key: copy.deepcopy(snapshot[key]) for key in DEPENDENCIES}
    except RecursionError:
        raise ValueError('data_requirements_dependency_depth') from None
    _bytes(inputs)
    _need(type(inputs['entity_schemas']) is dict and all(type(inputs[key]) is list for key in
          ('businesses', 'object_types', 'resources', 'sensor_sources', 'materials')),
          'data_requirements_dependency_shape')
    general = _issues(inputs['general_issues'])
    businesses = [_business(value, general) for value in inputs['businesses']]
    _need(len({item['id'] for item in businesses}) == len(businesses), 'data_requirements_business_identity')
    result = {'schema': SCHEMA, 'origin': 'system_design', 'dependency_hash': _digest(inputs),
        'dependency_hashes': {key: _digest(value) for key, value in inputs.items()},
        'confirmed_object_catalog': copy.deepcopy(inputs['object_types']), 'businesses': businesses,
        'evidence_policy': {'status': 'candidate', 'source_timestamp': 'unknown_until_actual_output',
            'unit': 'unknown_until_actual_output', 'retention_authorized': False, 'deletion_authorized': False},
        'summary': '根据编译快照自动派生数据保存需求，无需单独配置数据管理；'
            '这属于系统设计，不是原文明示或结果核实，也不授予执行、外发或删除权限。'}
    return {**result, 'requirements_hash': _digest(result)}


def verify_requirements(snapshot: dict) -> None:
    """Legacy snapshots may omit the field; a present value must cold-rebuild."""
    _need(type(snapshot) is dict, 'data_requirements_snapshot')
    if 'data_requirements' not in snapshot:
        return None
    expected = build_requirements(snapshot)
    _need(_bytes(snapshot['data_requirements']) == _bytes(expected), 'data_requirements_changed')
    return None
