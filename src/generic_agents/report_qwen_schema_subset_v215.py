"""Explicit Qwen provider-schema subset; never a local validation downgrade.

Only the documented unsupported assertion keywords are removed. Property names,
literal values and the complete local schema remain unchanged. This pure
compiler does not prove provider acceptance or authorize a model call.
"""
import copy

from .contracts import digest
from .typed_payload_codec_v131 import canonical_json


NATIVE_SCHEMA_VARIANT = 'qwen-json-schema-subset-20261027'
POLICY_SCHEMA = 'report-qwen-schema-subset-policy-v215'
UNSUPPORTED_KEYWORDS = frozenset({'uniqueItems', 'contains', 'minContains', 'maxContains',
    'additionalItems', 'patternProperties', 'propertyNames', 'multipleOf'})
_SCHEMA_MAPS = frozenset({'properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'})
_SCHEMA_LISTS = frozenset({'allOf', 'anyOf', 'oneOf', 'prefixItems'})
_SCHEMA_VALUES = frozenset({'items', 'additionalItems', 'contains', 'propertyNames',
    'additionalProperties', 'unevaluatedProperties', 'unevaluatedItems',
    'not', 'if', 'then', 'else', 'contentSchema'})
LOCAL_CONSTRAINTS = ['exact_decision_keys', 'known_business_keys', 'unique_recipients',
    'disposition_arity', 'ownership_questions_for_pending', 'no_conflicting_question_kind']
ERROR = 'Qwen提供商Schema方言或完整本地Schema无法核对，未降级或发送'


def _need(value):
    if not value:
        raise ValueError(ERROR)


def _path(pointer, key):
    return pointer + '/' + str(key).replace('~', '~0').replace('/', '~1')


def _value(key, value, pointer, removed):
    if key in _SCHEMA_MAPS:
        _need(type(value) is dict)
        return {name: _walk(child, _path(pointer, name), removed) for name, child in value.items()}
    if key in _SCHEMA_LISTS:
        _need(type(value) is list)
        return [_walk(child, _path(pointer, index), removed) for index, child in enumerate(value)]
    if key in _SCHEMA_VALUES:
        # Older tuple-items schemas still have schema entries, not literals.
        if key == 'items' and type(value) is list:
            return [_walk(child, _path(pointer, index), removed) for index, child in enumerate(value)]
        return _walk(value, pointer, removed)
    if key == 'dependencies':
        _need(type(value) is dict)
        return {name: (_walk(child, _path(pointer, name), removed)
                       if type(child) in (dict, bool) else copy.deepcopy(child))
                for name, child in value.items()}
    # enum/const/default/examples and extension data are data, not schemas.
    return copy.deepcopy(value)


def _walk(node, pointer, removed):
    _need(type(node) in (dict, bool))
    if type(node) is bool:
        return node
    output = {}
    for key, value in node.items():
        position = _path(pointer, key)
        if key in UNSUPPORTED_KEYWORDS:
            _need(removed is not None)
            removed.append({'path': position, 'value': copy.deepcopy(value)})
            # Record nested assertions too, while preserving the full original
            # value of the removed outer assertion in the transformation audit.
            _value(key, value, position, removed)
        else:
            output[key] = _value(key, value, position, removed)
    return output


def preflight_provider_schema(provider_schema):
    """Reject unsupported keywords at schema nodes, not same-named properties."""
    canonical_json(provider_schema)
    _need(type(provider_schema) is dict)
    _walk(provider_schema, '', None)
    return provider_schema


def compile_provider_schema(local_schema, *, native_schema_variant):
    _need(type(native_schema_variant) is str and native_schema_variant == NATIVE_SCHEMA_VARIANT)
    canonical_json(local_schema)
    _need(type(local_schema) is dict)
    removed = []
    provider = _walk(local_schema, '', removed)
    preflight_provider_schema(provider)
    removed.sort(key=lambda item: item['path'])
    policy = {'schema': POLICY_SCHEMA, 'native_schema_variant': NATIVE_SCHEMA_VARIANT,
        'transformation': 'strip_documented_qwen_unsupported_assertions',
        'removed_assertions': removed, 'local_validation_required': True,
        'local_constraints': list(LOCAL_CONSTRAINTS), 'provider_support_verified': False}
    return {'local_schema_hash': digest(local_schema), 'provider_schema': provider,
        'provider_schema_hash': digest(provider), 'schema_policy': policy,
        'schema_policy_hash': digest(policy)}
