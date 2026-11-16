"""Pure schema compiler: retain reviewed references in strict tool schemas.

The cached provider documentation spells its reusable-definition keyword `$def`
(singular). Input remains the original Pydantic `$defs` dialect. This compiler
changes only that dialect, annotations, unsupported length bounds and string
consts, recording every change once at its source path. Definitions are audited
once, including unused ones. No expanded schema or repeated full-text audit is
persisted for repeated slots.

This proof establishes deterministic schema projection, not provider acceptance,
response validity, authorization, complete Map provenance or a usable product
flow. Complete local validators and original response bytes remain mandatory.
No network, file, database, model, journal or permission operations occur here.
Existing compile_schema and all published protocols remain unchanged.
"""
import copy
import hashlib
import json
import math

PROOF_SCHEMA = 'report-evidence-strict-ref-schema-proof-v2'
AUDIT_SCHEMA = 'report-evidence-strict-ref-schema-audit-v2'
REFERENCE_POLICY = 'pydantic-defs-to-deepseek-def-v1'
MAX_SCHEMA_BYTES = 256 * 1024
MAX_AUDIT_BYTES = 256 * 1024
MAX_PROOF_BYTES = 512 * 1024
MAX_DEFINITIONS = 128
MAX_SCHEMA_DEPTH = 32
MAX_SCHEMA_NODES = 10000
_FORMATS = {'email', 'hostname', 'ipv4', 'ipv6', 'uuid'}
_LENGTHS = {'minLength', 'maxLength', 'minItems', 'maxItems'}
_KEYWORDS = {'$defs', '$ref', 'type', 'properties', 'required',
    'additionalProperties', 'items', 'enum', 'const', 'title', 'description',
    'pattern', 'format', 'anyOf', 'default', 'minimum', 'maximum',
    'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf'} | _LENGTHS


class RefSchemaError(ValueError):
    """Safe fixed error code; never includes schema content."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise RefSchemaError(code)


def _bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise RefSchemaError('schema_json_value') from None


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _pointer(value):
    return value.replace('~', '~0').replace('/', '~1')


def compile_ref_schema(local_schema):
    """Return a deterministic, bounded, reference-preserving proof.

    Only root `$defs` and canonical single-definition `#/$defs/X` references are
    accepted. Every reference must be its entire schema node: no siblings,
    external/nested pointers or cycles. Node limits count stored schema nodes
    once, while depth includes every dependency edge's logical expansion depth.
    Neither a compact reference graph nor an unused definition bypasses review.
    """
    _need(type(local_schema) is dict
        and len(_bytes(local_schema)) <= MAX_SCHEMA_BYTES,
        'schema_capacity_or_shape')
    try:
        original = copy.deepcopy(local_schema)
    except (RecursionError, TypeError):
        raise RefSchemaError('schema_json_value') from None
    definitions = original.get('$defs', {})
    _need(type(definitions) is dict and len(definitions) <= MAX_DEFINITIONS
        and all(type(name) is str and name for name in definitions),
        'schema_definitions')
    audit, graph, heights = [], {}, {}
    visits = [0]
    reference_count = [0]
    root_owner = None  # Definition names are strings; this cannot alias one.

    def record(path, operation, value):
        audit.append({'path': path, 'operation': operation,
            'original': copy.deepcopy(value)})

    def convert(node, path, owner, depth=0):
        visits[0] += 1
        _need(depth <= MAX_SCHEMA_DEPTH and visits[0] <= MAX_SCHEMA_NODES,
            'schema_graph_capacity')
        heights[owner] = max(heights.get(owner, 0), depth)
        _need(type(node) is dict and not (set(node) - _KEYWORDS),
            'unsupported_schema_keyword')
        if '$ref' in node:
            _need(set(node) == {'$ref'}, 'unsupported_ref_sibling')
            ref, prefix = node['$ref'], '#/$defs/'
            _need(type(ref) is str and ref.startswith(prefix),
                'external_or_unknown_ref')
            name = ref[len(prefix):].replace('~1', '/').replace('~0', '~')
            _need(name and prefix + _pointer(name) == ref
                and name in definitions, 'external_or_unknown_ref')
            graph[owner].append((name, depth + 1))
            reference_count[0] += 1
            record(path + '/$ref', 'rename_local_ref', ref)
            return {'$ref': '#/$def/' + _pointer(name)}
        result = {}
        if '$defs' in node:
            _need(owner is root_owner and path == ''
                and node['$defs'] == definitions, 'nested_definitions')
        if 'title' in node:
            _need(type(node['title']) is str, 'schema_annotation')
            record(path + '/title', 'remove_annotation', node['title'])
        if 'description' in node:
            _need(type(node['description']) is str, 'schema_annotation')
            result['description'] = node['description']
        if 'anyOf' in node:
            _need(set(node) <= {'anyOf', 'title', 'description'}
                and type(node['anyOf']) is list
                and 1 <= len(node['anyOf']) <= 16, 'unsupported_anyof')
            result['anyOf'] = [convert(child, path + '/anyOf/' + str(i),
                owner, depth + 1) for i, child in enumerate(node['anyOf'])]
            return result
        kind = node.get('type')
        _need(type(kind) is str and kind in
            {'object', 'array', 'string', 'integer', 'number', 'boolean'},
            'unsupported_schema_type')
        result['type'] = kind
        common = {'$defs', 'type', 'title', 'description', 'enum'}
        specific = {
            'object': {'properties', 'required', 'additionalProperties'},
            'array': {'items', 'minItems', 'maxItems'},
            'string': {'const', 'pattern', 'format', 'minLength', 'maxLength'},
            'integer': {'const', 'default', 'minimum', 'maximum',
                'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf'},
            'number': {'const', 'default', 'minimum', 'maximum',
                'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf'},
            'boolean': set(),
        }[kind]
        _need(not (set(node) - common - specific),
            'unsupported_schema_keyword_for_type')
        if 'enum' in node:
            values = node['enum']
            allowed = {'string': (str,), 'integer': (int,),
                'number': (int, float), 'boolean': (bool,)}.get(kind)
            _need(type(values) is list and values and allowed is not None
                and all(type(item) in allowed for item in values)
                and len({_bytes(item) for item in values}) == len(values),
                'schema_enum')
            result['enum'] = copy.deepcopy(values)
        if kind == 'object':
            properties, required = node.get('properties'), node.get('required')
            _need(type(properties) is dict
                and all(type(key) is str for key in properties)
                and type(required) is list
                and all(type(key) is str for key in required)
                and len(set(required)) == len(required)
                and set(required) == set(properties)
                and node.get('additionalProperties') is False,
                'strict_object_required')
            result.update(properties={key: convert(properties[key],
                path + '/properties/' + _pointer(key), owner, depth + 1)
                for key in sorted(properties)}, required=copy.deepcopy(required),
                additionalProperties=False)
        elif kind == 'array':
            _need(type(node.get('items')) is dict, 'schema_array_items')
            result['items'] = convert(node['items'], path + '/items',
                owner, depth + 1)
        if kind in {'string', 'array'}:
            low, high = (('minLength', 'maxLength') if kind == 'string'
                else ('minItems', 'maxItems'))
            for key in (low, high):
                if key in node:
                    _need(type(node[key]) is int and node[key] >= 0,
                        'schema_length_bound')
                    record(path + '/' + key,
                        'remove_provider_unsupported_bound', node[key])
            _need(low not in node or high not in node
                or node[low] <= node[high], 'schema_length_bound')
        if kind == 'string':
            for key in ('pattern', 'format'):
                if key in node:
                    _need(type(node[key]) is str
                        and (key != 'format' or node[key] in _FORMATS),
                        'schema_string_constraint')
                    result[key] = node[key]
            if 'const' in node:
                _need(type(node['const']) is str and ('enum' not in node
                    or node['enum'] == [node['const']]), 'schema_string_const')
                result['enum'] = [node['const']]
                record(path + '/const', 'string_const_to_singleton_enum',
                    node['const'])
        if kind in {'number', 'integer'}:
            for key in sorted(specific):
                if key in node:
                    value = node[key]
                    finite = (type(value) is int or
                        (type(value) is float and math.isfinite(value)))
                    _need(finite and (kind != 'integer'
                        or key not in {'const', 'default'}
                        or type(value) is int)
                        and (key != 'multipleOf' or value > 0),
                        'schema_number_constraint')
                    result[key] = value
        return result

    if '$defs' in original:
        record('/$defs', 'rename_root_definitions', sorted(definitions))
    graph[root_owner], heights[root_owner] = [], 0
    compiled_definitions = {}
    for name in sorted(definitions):
        graph[name], heights[name] = [], 0
        compiled_definitions[name] = convert(definitions[name],
            '/$defs/' + _pointer(name), name)
    provider = convert(original, '', root_owner)

    complete, active = {}, set()
    def logical_depth(owner):
        if owner in complete:
            return complete[owner]
        _need(owner not in active, 'cyclic_schema_ref')
        active.add(owner)
        depth = heights[owner]
        for target, relative_depth in graph[owner]:
            depth = max(depth, relative_depth + logical_depth(target))
        active.remove(owner)
        _need(depth <= MAX_SCHEMA_DEPTH, 'schema_graph_capacity')
        complete[owner] = depth
        return depth

    max_depth = max(logical_depth(owner) for owner in graph)
    if '$defs' in original:
        provider['$def'] = compiled_definitions
    _need(provider.get('type') == 'object'
        and len(_bytes(provider)) <= MAX_SCHEMA_BYTES,
        'schema_capacity_or_shape')
    audit_value = {'schema': AUDIT_SCHEMA, 'reference_policy': REFERENCE_POLICY,
        'transformations': audit,
        'counts': {'definitions': len(definitions), 'references': reference_count[0],
            'schema_nodes': visits[0], 'max_logical_depth': max_depth},
        'limits': {'definitions': MAX_DEFINITIONS, 'schema_nodes': MAX_SCHEMA_NODES,
            'logical_depth': MAX_SCHEMA_DEPTH, 'schema_bytes': MAX_SCHEMA_BYTES,
            'audit_bytes': MAX_AUDIT_BYTES, 'proof_bytes': MAX_PROOF_BYTES}}
    _need(len(_bytes(audit_value)) <= MAX_AUDIT_BYTES, 'schema_audit_capacity')
    proof = {'schema': PROOF_SCHEMA, 'local_schema_hash': _hash(original),
        'provider_schema': provider, 'provider_schema_hash': _hash(provider),
        'audit': audit_value, 'audit_hash': _hash(audit_value)}
    proof['proof_hash'] = _hash(proof)
    _need(len(_bytes(proof)) <= MAX_PROOF_BYTES, 'schema_proof_capacity')
    _need(_bytes(local_schema) == _bytes(original), 'local_schema_changed')
    return proof


def check_ref_schema_proof(proof, local_schema):
    """Cold-rebuild from the expected local schema; public rehashing is no trust."""
    expected = compile_ref_schema(local_schema)
    _need(type(proof) is dict and _bytes(proof) == _bytes(expected),
        'schema_proof_mismatch')
    return expected
