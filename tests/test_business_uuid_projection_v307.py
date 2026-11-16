"""Pure UUID projection only; no IO, identity authority or consent proof."""
import copy
from decimal import Decimal
from uuid import UUID, uuid5

import pytest

from generic_agents import business_uuid_projection_v307 as p
from generic_agents.contracts import digest

NAMESPACE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
IDENTITY = 'abcdef12-abcd-4abc-8abc-abcdef123456'
SALT = 'same-goal-advice:fixed-preparation-hash'


def test_exact_uuid5_formula_and_case_alias_are_stable_in_arbitrary_text():
    text = 'prefix' + IDENTITY + ' suffix ' + IDENTITY.upper()
    expected = str(uuid5(UUID(NAMESPACE), digest(SALT) + ':' + IDENTITY))
    assert p.project_text(text, namespace_id=NAMESPACE, salt=SALT) == 'prefix' + expected + ' suffix ' + expected
    assert len(p.project_text(text, namespace_id=NAMESPACE, salt=SALT)) == len(text)
    assert p.project_text(IDENTITY, namespace_id=NAMESPACE.upper(), salt=SALT) == expected
    assert p.project_text(IDENTITY, namespace_id=NAMESPACE, salt=SALT + ':other') != expected
    assert p.project_text(IDENTITY, namespace_id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', salt=SALT) != expected


def test_inventory_covers_keys_nested_values_and_unknown_text_without_changing_any_scalar_or_logical_ref():
    value = {'key_' + IDENTITY.upper(): [{'source_ref': 'source_original', 'fact_refs': ['fact_1', 'fact_2'],
        'text': 'source ' + IDENTITY, 'number': 1, 'float': 1.0, 'negative_zero': -0.0,
        'truth': True, 'nothing': None}]}
    before = copy.deepcopy(value); aliases = p.derive_aliases(value, namespace_id=NAMESPACE, salt=SALT)
    result = p.project(value, aliases=aliases); child = result['key_' + aliases[IDENTITY]][0]
    assert child['source_ref'] == 'source_original' and child['fact_refs'] == ['fact_1', 'fact_2']
    assert type(child['number']) is int and type(child['float']) is float and type(child['truth']) is bool
    assert digest(child['negative_zero']) == digest(-0.0) and child['nothing'] is None
    assert child['text'] == 'source ' + aliases[IDENTITY] and value == before
    child['fact_refs'].append('changed_return'); assert value == before
    assert aliases == p.derive_aliases(value, namespace_id=NAMESPACE, salt=SALT)


@pytest.mark.parametrize('field', list(sorted(p.LOGICAL_REFS)) + ['fact_refs', 'source_refs', 'capability_refs'])
def test_logical_uuid_collision_is_refused_instead_of_silently_rewriting_protocol_refs(field):
    value = {'actual_entity_id': IDENTITY, field: [IDENTITY] if field.endswith('_refs') else IDENTITY}
    with pytest.raises(p.ProjectionError): p.derive_aliases(value, namespace_id=NAMESPACE, salt=SALT)


def test_late_unknown_uuid_never_falls_back_to_the_original_text():
    aliases = p.derive_aliases({'known': IDENTITY}, namespace_id=NAMESPACE, salt=SALT)
    with pytest.raises(p.ProjectionError): p.project({'late': NAMESPACE}, aliases=aliases)


def test_case_folded_key_collision_cannot_overwrite_an_original_entry():
    value = {IDENTITY: 'first', IDENTITY.upper(): 'second'}
    aliases = p.derive_aliases(value, namespace_id=NAMESPACE, salt=SALT)
    with pytest.raises(p.ProjectionError): p.project(value, aliases=aliases)


@pytest.mark.parametrize('value', [Decimal('1.2'), {'bad': float('nan')}, {'bad': (1, 2)}, {1: 'not string'},
    {'bad': b'bytes'}, {'bad': '\ud800'}, {'bad': '\x00'}])
def test_non_json_values_are_not_coerced_or_given_an_alias_success(value):
    with pytest.raises((p.ProjectionError, ValueError, TypeError)):
        p.derive_aliases(value, namespace_id=NAMESPACE, salt=SALT)
