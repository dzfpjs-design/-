"""Preserve issued schema adoptions without renewing source or runtime authority.

Ordinary save/restore may retain stale evidence while changing unrelated setup.
The database helper verifies the original adoption ledger and accepted revision;
only the separate compilation verifier requires the source/design to be current.
Future runtime bindings need their own typed authority verifier at the public
configuration-protection hook, rather than being inferred from schema adoption.
"""
import json

from fastapi import HTTPException


HEADER_KEY = 'fullSemanticSchemaAdoptions'
ORIGIN_SCHEMA = 'report-full-semantic-schema-origin-v1'
OBJECT_ORIGIN_SCHEMA = 'report-object-schema-origin-v170'


def _need(condition):
    if not condition:
        raise HTTPException(409, '已采用的数据结构、来源或采用回执不能删除或改写；原配置保留')


def _same(left, right):
    """JSON equality preserves strict booleans/numbers and ordered field lists."""
    try:
        return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False) == json.dumps(right, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _need(False)


def _origins(config, key):
    value = config.get(key, {})
    _need(type(value) is dict and all(type(name) is str and type(origin) is dict
        for name, origin in value.items()))
    return value


def _rows(value):
    _need(type(value) is list and all(type(row) is dict and type(row.get('name')) is str
        and bool(row['name']) for row in value))
    result = {row['name']: row for row in value}
    _need(len(result) == len(value))
    return result


def _marked(origin):
    schema = origin.get('schema')
    if type(schema) is str and schema.startswith(('report-full-semantic-schema-', 'report-object-schema-')):
        _need(schema in {ORIGIN_SCHEMA, OBJECT_ORIGIN_SCHEMA})
    return schema in {ORIGIN_SCHEMA, OBJECT_ORIGIN_SCHEMA}


def _headers(config):
    values = config.get(HEADER_KEY, [])
    _need(type(values) is list)
    identities = []
    for header in values:
        _need(type(header) is dict and set(header) == {'id', 'plan_hash', 'material_ids'}
            and type(header['id']) is str and type(header['plan_hash']) is str
            and type(header['material_ids']) is list)
        identities.append(header['id'])
    _need(len(set(identities)) == len(identities))
    return values


def _authorized_state(db, p, config):
    """Validate every header/origin against real accepted rows, including extras."""
    _need(type(config) is dict)
    headers = _headers(config)
    object_origins = _origins(config, 'instanceObjectOrigins')
    relation_origins = _origins(config, 'instanceRelationOrigins')
    marked_objects = {name for name, origin in object_origins.items() if _marked(origin)}
    marked_relations = {name for name, origin in relation_origins.items() if _marked(origin)}
    if not headers:
        _need(not marked_objects and not marked_relations)
        return headers
    from .report_full_semantic_schema_adoption import protection_authority
    objects = _rows(config.get('instanceObjects', []))
    relationships = _rows(config.get('instanceRelationTypes', []))
    issued_objects = set(); issued_relations = set()
    for header in headers:
        authority = protection_authority(db, p, header)
        _need(type(authority) is dict and set(authority) == {
            'header', 'objects', 'relationships', 'object_origins', 'relationship_origins'}
            and _same(authority['header'], header))
        for rows, origins, actual, actual_origins, seen in (
                (authority['objects'], authority['object_origins'], objects, object_origins, issued_objects),
                (authority['relationships'], authority['relationship_origins'], relationships,
                    relation_origins, issued_relations)):
            expected = _rows(rows)
            _need(type(origins) is dict and set(expected) == set(origins)
                and not seen.intersection(expected))
            for name, row in expected.items():
                _need(name in actual and _same(actual[name], row)
                    and name in actual_origins and _same(actual_origins[name], origins[name])
                    and type(origins[name]) is dict and _marked(origins[name]))
            seen.update(expected)
    _need(marked_objects == issued_objects and marked_relations == issued_relations)
    return headers


def protect_configuration(db, p, previous, proposed):
    """Ordinary saves retain all accepted schemas; new markers need real ledgers."""
    before = _authorized_state(db, p, previous)
    after = _authorized_state(db, p, proposed)
    _need(len(after) >= len(before) and _same(after[:len(before)], before))
    return proposed


def require_compiled_objects(config, objects):
    """Pure gate: compile the adopted fields, never a separate caller schema.

This is not a provenance verifier. Call the database origin verifier separately;
manual objects remain subject to the existing object-contract validation.
"""
    _need(type(config) is dict)
    origins = _origins(config, 'instanceObjectOrigins')
    names = {name for name, origin in origins.items() if _marked(origin)}
    if not names:
        return
    expected = _rows(config.get('instanceObjects', []))
    actual = _rows(objects)
    for name in names:
        _need(name in expected and name in actual and _same(expected[name], actual[name]))
