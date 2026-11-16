"""Pure schema cache checks; no DB, service, model or device is exercised."""
import copy
import json

import pytest
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from generic_agents import contracts as subject


@pytest.fixture(autouse=True)
def empty_schema_cache():
    subject._check_json_schema.cache_clear()
    yield
    subject._check_json_schema.cache_clear()


@pytest.fixture
def checked_schemas(monkeypatch):
    original = subject.Draft202012Validator.check_schema
    calls = []

    def counted(schema, *args, **kwargs):
        calls.append(copy.deepcopy(schema))
        return original(schema, *args, **kwargs)

    monkeypatch.setattr(subject.Draft202012Validator, 'check_schema', counted)
    return calls


def schema():
    return {'type': 'object', 'properties': {'value': {'type': 'string'}},
        'required': ['value'], 'additionalProperties': False}


def manifest():
    return {'kind': 'tool', 'namespace': 'schema_cache_test', 'name': 'cached_schema',
        'version': '1.0.0', 'owner': 'pure test', 'description': 'Schema content cache test',
        'input_schema': schema(), 'output_schema': schema()}


def test_equal_schema_content_and_key_order_check_once(checked_schemas):
    value = manifest()
    subject.Manifest.model_validate(value)
    value['input_schema'] = dict(reversed(list(value['input_schema'].items())))
    subject.Manifest.model_validate(value)
    assert len(checked_schemas) == 1
    assert subject._check_json_schema.cache_info().hits == 3


def test_nested_schema_mutation_is_rechecked_and_rejected(checked_schemas):
    value = schema()
    subject.check_schema(value)
    value['properties']['value']['type'] = 'integer'
    subject.check_schema(value)
    value['properties']['value']['type'] = 'not-a-json-schema-type'
    with pytest.raises(SchemaError):
        subject.check_schema(value)
    assert len(checked_schemas) == 3
    assert subject._check_json_schema.cache_info().currsize == 2


def test_invalid_schema_is_never_cached(checked_schemas):
    value = {'type': 'not-a-json-schema-type'}
    for _ in range(2):
        with pytest.raises(SchemaError):
            subject.check_schema(value)
    assert len(checked_schemas) == 2
    assert subject._check_json_schema.cache_info().currsize == 0


def test_output_schema_is_checked_after_warm_input(checked_schemas):
    value = manifest()
    subject.Manifest.model_validate(value)
    value['output_schema']['properties']['value']['type'] = 'not-a-json-schema-type'
    with pytest.raises(SchemaError):
        subject.Manifest.model_validate(value)
    assert len(checked_schemas) == 2


@pytest.mark.parametrize('change', ['field', 'secret', 'external_reference'])
def test_current_manifest_checks_are_not_cached(change, checked_schemas):
    value = manifest()
    subject.Manifest.model_validate(value)
    if change == 'field':
        value['name'] = 'Invalid Name'
    elif change == 'secret':
        value['specification'] = {'password': 'synthetic-test-literal'}
    else:
        value['specification'] = {'nested': [{'$ref': 'https://invalid.example/schema'}]}
    with pytest.raises(ValidationError):
        subject.Manifest.model_validate(value)
    assert len(checked_schemas) == 1


def test_success_cache_is_bounded_and_eviction_rechecks(checked_schemas):
    for index in range(129):
        subject.check_schema({'type': 'object', 'title': str(index)})
    info = subject._check_json_schema.cache_info()
    assert info.maxsize == 128 and info.currsize == 128
    subject.check_schema({'title': '128', 'type': 'object'})
    assert len(checked_schemas) == 129
    subject.check_schema({'type': 'object', 'title': '0'})
    assert len(checked_schemas) == 130
    assert subject._check_json_schema.cache_info().currsize == 128


def test_boolean_schemas_keep_their_distinct_meaning(checked_schemas):
    for value in (True, False, True, False):
        subject.check_schema(value)
    assert checked_schemas == [True, False]
    assert subject._check_json_schema.cache_info().currsize == 2


def test_python_tuple_is_not_silently_changed_into_json_array(checked_schemas):
    value = {'type': ('integer',)}
    for _ in range(2):
        with pytest.raises(SchemaError):
            subject.check_schema(value)
    assert len(checked_schemas) == 2
    assert subject._check_json_schema.cache_info().currsize == 0


def test_non_string_keyword_keeps_original_python_validation(checked_schemas):
    value = {'type': 'object', 1: 'unknown-numeric-keyword'}
    subject.check_schema(value)
    subject.check_schema(value)
    assert len(checked_schemas) == 2
    assert 1 in checked_schemas[0] and '1' not in checked_schemas[0]
    assert subject._check_json_schema.cache_info().currsize == 0


def test_large_valid_schema_is_checked_without_entering_cache(checked_schemas):
    value = {'type': 'object', 'title': 'x' * subject._SCHEMA_CACHE_MAX_BYTES}
    subject.check_schema(value)
    subject.check_schema(value)
    assert len(checked_schemas) == 2
    assert subject._check_json_schema.cache_info().currsize == 0


def test_entry_size_limit_counts_utf8_bytes(monkeypatch, checked_schemas):
    value = {'type': 'object', 'title': '森林' * 12}
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    encoded_size = len(serialized.encode('utf-8'))
    assert len(serialized) + 1 < encoded_size
    monkeypatch.setattr(subject, '_SCHEMA_CACHE_MAX_BYTES', len(serialized) + 1)
    subject.check_schema(value)
    subject.check_schema(value)
    assert len(checked_schemas) == 2
    assert subject._check_json_schema.cache_info().currsize == 0
    monkeypatch.setattr(subject, '_SCHEMA_CACHE_MAX_BYTES', encoded_size)
    subject.check_schema(value)
    subject.check_schema(value)
    assert len(checked_schemas) == 3
    assert subject._check_json_schema.cache_info().currsize == 1
