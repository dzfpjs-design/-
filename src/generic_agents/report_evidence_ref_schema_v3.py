"""Versioned provider projection: inline only immediate anyOf reference arms.

V2 first audits the complete local schema. The only provider-schema change is
substitution of an immediate anyOf pure reference by its already audited,
explicitly typed definition. Other references and shared definitions remain.
This preserves V2's provider predicate, not the local constraints which V2
already reports as unsupported. It does not establish provider acceptance,
generation, source authenticity, permissions, budget or a usable product flow.
"""
import copy
import hashlib
import json

from . import report_evidence_ref_schema_v2 as v2


PROOF_SCHEMA = 'report-evidence-strict-ref-arm-proof-v3'
AUDIT_SCHEMA = 'report-evidence-strict-ref-arm-audit-v3'
REFERENCE_POLICY = 'immediate-anyof-ref-arm-typed-inline-v1'
MAX_SCHEMA_BYTES = v2.MAX_SCHEMA_BYTES
MAX_SCHEMA_NODES = v2.MAX_SCHEMA_NODES
MAX_SCHEMA_DEPTH = v2.MAX_SCHEMA_DEPTH
MAX_AUDIT_BYTES = v2.MAX_AUDIT_BYTES
MAX_PROOF_BYTES = v2.MAX_PROOF_BYTES
TYPES = {'object', 'array', 'string', 'integer', 'number', 'boolean'}


class RefArmSchemaError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise RefArmSchemaError(code)


def _bytes(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise RefArmSchemaError('schema_json_value') from None


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _pointer(value):
    return value.replace('~', '~0').replace('/', '~1')


def _target(ref, definitions):
    prefix = '#/$def/'
    _need(type(ref) is str and ref.startswith(prefix), 'arm_reference')
    name = ref[len(prefix):].replace('~1', '/').replace('~0', '~')
    _need(name in definitions and prefix + _pointer(name) == ref, 'arm_reference')
    return name


def _nodes(node):
    count = 1
    for child in node.get('properties', {}).values():
        count += _nodes(child)
    if 'items' in node:
        count += _nodes(node['items'])
    for child in node.get('anyOf', []):
        count += _nodes(child)
    return count


def _local(node, root=False):
    """Translate only schema positions for the second complete V2 audit."""
    result = {key: copy.deepcopy(value) for key, value in node.items()
        if key not in {'properties', 'items', 'anyOf', '$def', '$ref'}}
    if '$ref' in node:
        result['$ref'] = '#/$defs/' + node['$ref'][len('#/$def/'):]
    if 'properties' in node:
        result['properties'] = {key: _local(value) for key, value in node['properties'].items()}
    if 'items' in node:
        result['items'] = _local(node['items'])
    if 'anyOf' in node:
        result['anyOf'] = [_local(value) for value in node['anyOf']]
    if root and '$def' in node:
        result['$defs'] = {key: _local(value) for key, value in node['$def'].items()}
    return result


def _compile_v2(value):
    try:
        return v2.compile_ref_schema(value)
    except v2.RefSchemaError as error:
        raise RefArmSchemaError('v2_' + error.code) from None


def compile_ref_schema(local_schema):
    original_bytes = _bytes(local_schema)
    base = _compile_v2(local_schema)
    source = base['provider_schema']; definitions = source.get('$def', {})
    compiled, active, changes = {}, set(), []
    projected_bytes = [len(_bytes(source))]
    projected_nodes = [base['audit']['counts']['schema_nodes']]

    def definition(name):
        if name in compiled:
            return compiled[name]
        _need(name not in active, 'arm_reference_cycle')
        active.add(name)
        compiled[name] = transform(definitions[name], '/$def/' + _pointer(name))
        active.remove(name)
        return compiled[name]

    def transform(node, path):
        result = {key: copy.deepcopy(value) for key, value in node.items()
            if key not in {'properties', 'items', 'anyOf', '$def'}}
        if 'properties' in node:
            result['properties'] = {key: transform(value, path + '/properties/' + _pointer(key))
                for key, value in sorted(node['properties'].items())}
        if 'items' in node:
            result['items'] = transform(node['items'], path + '/items')
        if 'anyOf' in node:
            arms = []
            for i, arm in enumerate(node['anyOf']):
                position = path + '/anyOf/' + str(i)
                if set(arm) != {'$ref'}:
                    arms.append(transform(arm, position)); continue
                name = _target(arm['$ref'], definitions)
                _need(definitions[name].get('type') in TYPES
                    and 'anyOf' not in definitions[name], 'arm_target_not_explicitly_typed')
                target = definition(name)
                # Charge exact canonical replacement bytes and stored schema
                # nodes BEFORE copying. Shared definitions remain in the total.
                projected_bytes[0] += len(_bytes(target)) - len(_bytes(arm))
                projected_nodes[0] += _nodes(target) - 1
                _need(projected_bytes[0] <= MAX_SCHEMA_BYTES, 'post_schema_bytes')
                _need(projected_nodes[0] <= MAX_SCHEMA_NODES, 'post_schema_nodes')
                arms.append(copy.deepcopy(target))
                changes.append({'path': position, 'operation': 'inline_immediate_anyof_ref_arm',
                    'original': copy.deepcopy(arm), 'target_hash': _hash(target)})
            result['anyOf'] = arms
        return result

    provider = transform(source, '')
    for name in sorted(definitions):
        definition(name)
    if '$def' in source:
        provider['$def'] = {name: compiled[name] for name in sorted(compiled)}
    actual_bytes = len(_bytes(provider))
    _need(actual_bytes == projected_bytes[0] and actual_bytes <= MAX_SCHEMA_BYTES, 'post_schema_bytes')
    # This re-audits every stored expanded node, all remaining references and
    # logical dependency depth. No old self-hash or compact graph is trusted.
    post = _compile_v2(_local(provider, root=True))
    counts = post['audit']['counts']
    _need(_bytes(post['provider_schema']) == _bytes(provider), 'post_schema_changed')
    _need(counts['schema_nodes'] == projected_nodes[0]
        and counts['schema_nodes'] <= MAX_SCHEMA_NODES
        and counts['max_logical_depth'] <= MAX_SCHEMA_DEPTH, 'post_schema_nodes_or_depth')
    audit = {'schema': AUDIT_SCHEMA, 'reference_policy': REFERENCE_POLICY,
        'base_proof_hash': base['proof_hash'], 'base_provider_schema_hash': base['provider_schema_hash'],
        'base_audit': copy.deepcopy(base['audit']), 'transformations': changes,
        'counts': {**counts, 'inlined_arms': len(changes), 'schema_bytes': actual_bytes},
        'limits': {'schema_nodes': MAX_SCHEMA_NODES, 'logical_depth': MAX_SCHEMA_DEPTH,
            'schema_bytes': MAX_SCHEMA_BYTES, 'audit_bytes': MAX_AUDIT_BYTES, 'proof_bytes': MAX_PROOF_BYTES}}
    _need(len(_bytes(audit)) <= MAX_AUDIT_BYTES, 'schema_audit_capacity')
    proof = {'schema': PROOF_SCHEMA, 'local_schema_hash': base['local_schema_hash'],
        'provider_schema': provider, 'provider_schema_hash': _hash(provider),
        'audit': audit, 'audit_hash': _hash(audit)}
    proof['proof_hash'] = _hash(proof)
    _need(len(_bytes(proof)) <= MAX_PROOF_BYTES, 'schema_proof_capacity')
    _need(original_bytes == _bytes(local_schema), 'local_schema_changed')
    return proof


def check_ref_schema_proof(proof, local_schema):
    expected = compile_ref_schema(local_schema)
    _need(type(proof) is dict and _bytes(proof) == _bytes(expected), 'schema_proof_mismatch')
    return expected
