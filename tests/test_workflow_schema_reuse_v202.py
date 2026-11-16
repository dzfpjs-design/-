"""Only JSON Schema validity is reused; current graph/projection checks still run."""
import copy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from generic_agents import contracts
from generic_agents import full_semantic_graph_v1 as graph
from generic_agents import guarded_port_projection_v198 as projection


def obj(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
        'additionalProperties': False}


def schema():
    return obj(packet=obj(name={'type': 'string', 'minLength': 2},
        temperature={'type': 'number', 'x-unit': '°C'}))


def reference(pointer='/name', *, kind='string', unit=None):
    return {'schema': projection.SCHEMA, 'source': projection.SOURCE,
        'kind': kind, 'nullable': False, 'unit': unit, 'consumer_node_id': 'consume',
        'origin': {'node_id': 'produce', 'port_ordinal': 0, 'output_pointer': '/packet'},
        'projection': {'op': 'copy', 'pointer': pointer}}


@pytest.fixture(autouse=True)
def empty_schema_cache():
    contracts._check_json_schema.cache_clear()
    yield
    contracts._check_json_schema.cache_clear()


@pytest.fixture
def schema_checks(monkeypatch):
    original = contracts.Draft202012Validator.check_schema
    calls = []

    def counted(value):
        calls.append(copy.deepcopy(value))
        return original(value)

    monkeypatch.setattr(contracts.Draft202012Validator, 'check_schema', counted)
    return calls


def test_graph_and_projection_reuse_only_complete_equal_schema_content(schema_checks):
    source = schema()
    graph._schema(source)
    graph._schema(copy.deepcopy(source))
    assert projection.derive_schema(source, reference()) == {'type': 'string', 'minLength': 2}
    reordered = {key: copy.deepcopy(value) for key, value in reversed(list(source.items()))}
    assert projection.derive_schema(reordered, reference()) == {'type': 'string', 'minLength': 2}
    assert len(schema_checks) == 1
    assert contracts._check_json_schema.cache_info().hits == 3


def test_nested_mutation_rechecks_new_content_and_invalid_schemas_are_never_reused(schema_checks):
    source = schema()
    graph._schema(source)
    source['properties']['packet']['properties']['name']['minLength'] = 4
    assert projection.derive_schema(source, reference())['minLength'] == 4
    assert len(schema_checks) == 2
    source['properties']['packet']['properties']['name']['minLength'] = -1
    for _ in range(2):
        with pytest.raises(ValueError, match='graph_json_schema'):
            graph._schema(source)
        with pytest.raises(projection.ProjectionContractError) as failure:
            projection.derive_schema(source, reference())
        assert failure.value.code == 'origin_schema'
    assert len(schema_checks) == 6
    assert contracts._check_json_schema.cache_info().currsize == 2


@pytest.mark.parametrize('keyword', ['$ref', '$id', '$dynamicRef', '$recursiveRef'])
def test_warmed_schema_validity_cannot_bypass_current_reference_restrictions(keyword, schema_checks):
    source = schema()
    source['properties']['packet']['properties']['name'][keyword] = 'https://untrusted.invalid/schema'
    # The generic metaschema permits this content. Graph/projection restrictions
    # are intentionally stricter and must run even when validity is cached.
    contracts.check_schema(source)
    for _ in range(2):
        with pytest.raises(ValueError, match='graph_schema_reference'):
            graph._schema(source)
        with pytest.raises(projection.ProjectionContractError):
            projection.derive_schema(source, reference())
    assert len(schema_checks) == 1


def test_warm_schema_does_not_cache_projection_reference_or_actual_value_semantics(schema_checks):
    source = schema()
    first = projection.derive_schema(source, reference())
    assert first == {'type': 'string', 'minLength': 2}
    first['minLength'] = 1
    second = projection.derive_schema(source, reference())
    assert second == {'type': 'string', 'minLength': 2}
    Draft202012Validator(second).validate('ab')
    with pytest.raises(ValidationError):
        Draft202012Validator(second).validate('a')
    temperature_ref = reference('/temperature', kind='number', unit='°C')
    assert projection.derive_schema(source, temperature_ref) == {'type': 'number', 'x-unit': '°C'}
    actual = {'packet': {'name': '树木', 'temperature': -0.0}}
    assert projection.project(actual, reference()) == '树木'
    actual['packet']['name'] = '林木'
    assert projection.project(actual, reference()) == '林木'
    with pytest.raises(projection.ProjectionContractError):
        projection.derive_schema(source, reference('/missing'))
    with pytest.raises(projection.ProjectionContractError):
        projection.derive_schema(source, reference('/temperature', kind='number', unit='K'))
    assert len(schema_checks) == 1
