"""Business-data decisions run on typed values, never code or model output."""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import business_data as b
from generic_agents.business_data_api import BindingCreate, Version


@pytest.mark.parametrize('baseline,current,incoming', [
    ({'field': 0}, {'field': False}, {'field': 1}),
    ({'field': 1}, {'field': 1.0}, {'field': 2}),
    ({'field': {'x': 0}}, {'field': {'x': False}}, {'field': {'x': 1}}),
    ({'field': [0]}, {'field': [False]}, {'field': [1]}),
])
def test_human_type_changes_are_real_conflicts_not_python_equality(baseline, current, incoming):
    after, conflicts = b.merge_three_way(baseline, current, incoming)
    assert conflicts == ['field'] and b.same(after, current)


def test_source_unchanged_preserves_human_mapped_and_unmapped_fields():
    after, conflicts = b.merge_three_way({'name': 'old', 'amount': 1},
        {'name': 'human', 'amount': 1, 'note': 'keep manual note'}, {'name': 'old', 'amount': 2})
    assert not conflicts and after == {'name': 'human', 'amount': 2, 'note': 'keep manual note'}


def test_matching_explicit_human_and_provider_change_is_not_conflict():
    after, conflicts = b.merge_three_way({'name': 'old'}, {'name': 'new'}, {'name': 'new'})
    assert after == {'name': 'new'} and conflicts == []


@pytest.mark.parametrize('value', [False, True, 1.0, None, '', ' x ', {'id': 1}, [1]])
def test_unstable_or_ambiguous_external_keys_are_rejected(value):
    with pytest.raises(HTTPException): b.external_key({'id': value}, '/id')


@pytest.mark.parametrize('value,expected', [(0, '0'), (9007199254740993, '9007199254740993'), ('001', '001')])
def test_external_key_preserves_exact_integer_or_lexical_identity(value, expected):
    assert b.external_key({'id': value}, '/id') == expected


def test_pointer_selector_escapes_headers_and_never_evaluates_source():
    assert b.pointer({'a/b': {'~id': 0}}, '/a~1b/~0id') == 0
    assert b.pointer({'name': False}, '/name') is False
    assert b.pointer({}, '/missing') is b.MISSING


@pytest.mark.parametrize('value,kind', [('=SUM(A2:A3)', 'number'), ('1.5', 'integer'),
    (False, 'integer'), ('NaN', 'number'), ('Infinity', 'number'), (1700000000000, 'date')])
def test_formula_nonfinite_lossy_number_and_invented_date_are_not_converted(value, kind):
    with pytest.raises((HTTPException, ValueError)): b.convert(value, kind)


def test_zero_false_null_and_iso_date_are_not_replaced_with_defaults():
    assert b.convert(0, 'identity') == 0
    assert b.convert(False, 'identity') is False
    assert b.convert(None, 'identity') is None
    assert b.convert('2026-10-25', 'date') == '2026-10-25'


@pytest.mark.parametrize('value', [True, 1.0, '1'])
def test_action_cas_is_strict_integer(value):
    with pytest.raises(ValidationError): Version(request_id=uuid4(), expected_version=value)


def test_root_json_dataset_key_is_valid_explicit_binding_not_an_unselected_value():
    value = BindingCreate(request_id=uuid4(), source_id='source', source_expected_version=1,
        read_id='read', instance_id='instance', release_id='release', object_type='dynamic', schema_hash='a'*64,
        record_set='', external_id_path='/id', fields=[{'target': 'field', 'source': '/name', 'conversion': 'identity'}])
    assert value.record_set == ''
    with pytest.raises(ValidationError): BindingCreate.model_validate({**value.model_dump(), 'record_set': None})


@pytest.mark.parametrize('value,expected', [(None, False), ({}, False),
    ({'execution': None, 'specification': None}, False),
    ({'namespace': b.NS, 'execution': None}, True),
    ({'execution': {'adapter': b.ADAPTER}, 'specification': None}, True),
    ({'specification': {'schema': 'business-data-source-v204'}}, True)])
def test_managed_source_detection_is_total_and_cannot_be_hidden_by_bad_siblings(value, expected):
    assert b.is_managed_manifest(value) is expected


def test_mapped_required_schema_checks_whole_object_and_preserves_missing():
    binding = NS(data={'fields': [{'target': 'amount', 'source': '/quantity', 'conversion': 'integer'}]})
    assert b.mapped({'quantity': '0'}, binding) == {'amount': 0}
    with pytest.raises(HTTPException): b.mapped({}, binding)
    with pytest.raises(HTTPException): b.validate_target({'type': 'object', 'required': ['name']}, {'amount': 0})


def test_daily_uses_original_reception_china_day_not_provider_timestamp():
    from generic_agents.business_data_receipts import _day
    read = NS(data={'received_at': '2026-10-25T16:01:00+00:00', 'provider_time': '1999-01-01'})
    assert _day(read) == '2026-10-26'
    read.data['received_at'] = '2026-10-15T15:59:00+00:00'
    assert _day(read) == '2026-10-15'  # Old capture days are retained, not rebased to today's bucket.
