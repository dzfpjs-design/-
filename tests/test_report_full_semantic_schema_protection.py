"""Synthetic protection contracts, not database adoption/source/runtime proof.

The fake authority tests that the save hook requires a server ledger decision.
Root runs these tests; fixtures do not read private data or start services.
"""
import copy
import sys
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
import pytest

from generic_agents import report_full_semantic_schema_protection as protection


def scene(number=1):
    identity = str(UUID(int=number))
    name = '对象🙂' + str(number)
    target = '目标' + str(number)
    relation = '关联' + str(number)
    objects = [{'name': item, 'fields': [
        {'label': '名称', 'kind': '文本', 'required': True, 'unit': None},
        {'label': '长度', 'kind': '数值', 'required': False, 'unit': '米'}]}
        for item in (name, target)]
    relationships = [{'name': relation, 'description': '人工采用的结构设计',
        'source_type': name, 'target_type': target, 'cardinality': 'many', 'allow_self': False}]
    def origin(item, kind, path):
        return {'schema': protection.ORIGIN_SCHEMA, 'adoption_id': identity,
            'plan_hash': 'a' * 64, 'kind': kind, 'name': item,
            'owner_path': path, 'owner_value_hash': 'b' * 64}
    authority = {'header': {'id': identity, 'plan_hash': 'a' * 64,
        'material_ids': [str(UUID(int=100)), str(UUID(int=101))]},
        'objects': objects, 'relationships': relationships,
        'object_origins': {item: origin(item, 'object', '/objects/' + str(index))
            for index, item in enumerate((name, target))},
        'relationship_origins': {relation: origin(relation, 'relationship', '/relationships/0')}}
    config = {'fullSemanticSchemaAdoptions': [copy.deepcopy(authority['header'])],
        'instanceObjects': copy.deepcopy(objects), 'instanceRelationTypes': copy.deepcopy(relationships),
        'instanceObjectOrigins': copy.deepcopy(authority['object_origins']),
        'instanceRelationOrigins': copy.deepcopy(authority['relationship_origins'])}
    return config, authority


def fake_ledger(monkeypatch, *authorities):
    values = {item['header']['id']: item for item in authorities}
    calls = []
    def checked(db, p, header):
        calls.append(copy.deepcopy(header))
        found = values.get(header['id'])
        if found is None or not protection._same(found['header'], header):
            raise HTTPException(409, '没有真实受理回执')
        return copy.deepcopy(found)
    monkeypatch.setitem(sys.modules, 'generic_agents.report_full_semantic_schema_adoption',
        SimpleNamespace(protection_authority=checked))
    return calls


def denied(call):
    with pytest.raises(HTTPException) as error:
        call()
    assert error.value.status_code == 409


def test_unchanged_stale_adoption_allows_unrelated_save_and_checks_both_ledgers(monkeypatch):
    previous, authority = scene(); calls = fake_ledger(monkeypatch, authority)
    proposed = copy.deepcopy(previous); proposed['instanceProfile'] = {'goal': '修改无关目标'}
    before = copy.deepcopy((previous, proposed))
    assert protection.protect_configuration(None, None, previous, proposed) is proposed
    assert calls == [authority['header'], authority['header']]
    assert (previous, proposed) == before


@pytest.mark.parametrize('mutation', ['header_delete', 'header_drop_key', 'header_change',
    'header_duplicate', 'object_delete', 'object_change', 'field_delete', 'field_required_number',
    'field_unit', 'object_origin_delete', 'object_origin_change', 'object_origin_legacy',
    'relation_delete', 'relation_change', 'relation_origin_delete', 'all_markers_delete',
    'duplicate_object', 'duplicate_relation'])
def test_save_or_restore_cannot_remove_change_or_launder_accepted_structure(monkeypatch, mutation):
    previous, authority = scene(); fake_ledger(monkeypatch, authority)
    proposed = copy.deepcopy(previous)
    first = proposed['instanceObjects'][0]['name']; relation = proposed['instanceRelationTypes'][0]['name']
    if mutation == 'header_delete': proposed['fullSemanticSchemaAdoptions'] = []
    elif mutation == 'header_drop_key': proposed.pop('fullSemanticSchemaAdoptions')
    elif mutation == 'header_change': proposed['fullSemanticSchemaAdoptions'][0]['plan_hash'] = 'c' * 64
    elif mutation == 'header_duplicate': proposed['fullSemanticSchemaAdoptions'] *= 2
    elif mutation == 'object_delete': proposed['instanceObjects'].pop(0)
    elif mutation == 'object_change': proposed['instanceObjects'][0]['name'] = '手工改名'
    elif mutation == 'field_delete': proposed['instanceObjects'][0]['fields'].pop()
    elif mutation == 'field_required_number': proposed['instanceObjects'][0]['fields'][0]['required'] = 1
    elif mutation == 'field_unit': proposed['instanceObjects'][0]['fields'][1]['unit'] = '厘米'
    elif mutation == 'object_origin_delete': proposed['instanceObjectOrigins'].pop(first)
    elif mutation == 'object_origin_change': proposed['instanceObjectOrigins'][first]['owner_path'] = '/objects/9'
    elif mutation == 'object_origin_legacy': proposed['instanceObjectOrigins'][first] = {'analysis_id': '假冒人工来源'}
    elif mutation == 'relation_delete': proposed['instanceRelationTypes'] = []
    elif mutation == 'relation_change': proposed['instanceRelationTypes'][0]['cardinality'] = 'one_per_source'
    elif mutation == 'relation_origin_delete': proposed['instanceRelationOrigins'].pop(relation)
    elif mutation == 'all_markers_delete':
        proposed.pop('fullSemanticSchemaAdoptions'); proposed['instanceObjectOrigins'] = {}; proposed['instanceRelationOrigins'] = {}
    elif mutation == 'duplicate_object': proposed['instanceObjects'].append(copy.deepcopy(proposed['instanceObjects'][0]))
    else: proposed['instanceRelationTypes'].append(copy.deepcopy(proposed['instanceRelationTypes'][0]))
    before = copy.deepcopy((previous, proposed))
    denied(lambda: protection.protect_configuration(None, None, previous, proposed))
    assert (previous, proposed) == before


def test_equal_forged_configuration_and_valid_previous_cannot_hide_extra_fake_adoption(monkeypatch):
    previous, authority = scene(); forged, other = scene(2)
    fake_ledger(monkeypatch, authority)
    denied(lambda: protection.protect_configuration(None, None, forged, copy.deepcopy(forged)))
    proposed = copy.deepcopy(previous)
    proposed['fullSemanticSchemaAdoptions'].extend(forged['fullSemanticSchemaAdoptions'])
    proposed['instanceObjects'].extend(forged['instanceObjects'])
    proposed['instanceRelationTypes'].extend(forged['instanceRelationTypes'])
    proposed['instanceObjectOrigins'].update(forged['instanceObjectOrigins'])
    proposed['instanceRelationOrigins'].update(forged['instanceRelationOrigins'])
    denied(lambda: protection.protect_configuration(None, None, previous, proposed))
    # A real second accepted row is still checked, rather than trusted by shape.
    fake_ledger(monkeypatch, authority, other)
    assert protection.protect_configuration(None, None, previous, proposed) is proposed


def test_extra_or_unknown_full_origin_without_header_is_not_manual(monkeypatch):
    config, _ = scene()
    config.pop('fullSemanticSchemaAdoptions')
    denied(lambda: protection.protect_configuration(None, None, {}, config))
    name = next(iter(config['instanceObjectOrigins']))
    config['instanceObjectOrigins'][name]['schema'] = 'report-full-semantic-schema-origin-v2'
    denied(lambda: protection.require_compiled_objects(config, config['instanceObjects']))


def test_real_header_does_not_authorize_extra_origins_or_reordered_headers(monkeypatch):
    previous, authority = scene(); second, other = scene(2)
    fake_ledger(monkeypatch, authority, other)
    proposed = copy.deepcopy(previous)
    proposed['instanceObjectOrigins']['伪造额外对象'] = copy.deepcopy(next(iter(authority['object_origins'].values())))
    proposed['instanceObjects'].append({'name': '伪造额外对象', 'fields': []})
    denied(lambda: protection.protect_configuration(None, None, previous, proposed))
    both = copy.deepcopy(previous)
    both['fullSemanticSchemaAdoptions'].extend(second['fullSemanticSchemaAdoptions'])
    both['instanceObjects'].extend(second['instanceObjects'])
    both['instanceRelationTypes'].extend(second['instanceRelationTypes'])
    both['instanceObjectOrigins'].update(second['instanceObjectOrigins'])
    both['instanceRelationOrigins'].update(second['instanceRelationOrigins'])
    reordered = copy.deepcopy(both); reordered['fullSemanticSchemaAdoptions'].reverse()
    denied(lambda: protection.protect_configuration(None, None, both, reordered))


def test_legacy_and_new_manual_objects_keep_existing_validation_boundary(monkeypatch):
    previous, authority = scene(); fake_ledger(monkeypatch, authority)
    proposed = copy.deepcopy(previous)
    manual = {'name': '人工对象', 'fields': [{'label': '名称', 'kind': '文本', 'required': True}]}
    proposed['instanceObjects'].append(manual)
    assert protection.protect_configuration(None, None, previous, proposed) is proposed
    assert protection.require_compiled_objects(proposed, list(reversed(proposed['instanceObjects']))) is None
    assert protection.protect_configuration(None, None, {}, {'instanceObjects': [manual]}) == {'instanceObjects': [manual]}
    assert protection.require_compiled_objects({}, [manual]) is None


@pytest.mark.parametrize('mutation', ['drop_adopted', 'drop_all', 'rename', 'field_delete',
    'type', 'required', 'unit', 'field_order', 'extra_field', 'extra_property', 'duplicate', 'not_list'])
def test_compiler_cannot_verify_one_origin_but_compile_different_or_missing_fields(mutation):
    config, _ = scene(); actual = copy.deepcopy(config['instanceObjects'])
    if mutation == 'drop_adopted': actual.pop(0)
    elif mutation == 'drop_all': actual = []
    elif mutation == 'rename': actual[0]['name'] = '另一对象'
    elif mutation == 'field_delete': actual[0]['fields'].pop()
    elif mutation == 'type': actual[0]['fields'][1]['kind'] = '整数'
    elif mutation == 'required': actual[0]['fields'][0]['required'] = 1
    elif mutation == 'unit': actual[0]['fields'][1]['unit'] = None
    elif mutation == 'field_order': actual[0]['fields'].reverse()
    elif mutation == 'extra_field': actual[0]['fields'].append({'label': '偷偷新增', 'kind': '文本', 'required': False, 'unit': None})
    elif mutation == 'extra_property': actual[0]['allow_unknown'] = True
    elif mutation == 'duplicate': actual.append(copy.deepcopy(actual[0]))
    else: actual = tuple(actual)
    before = copy.deepcopy((config, actual))
    denied(lambda: protection.require_compiled_objects(config, actual))
    assert (config, actual) == before


def test_compile_gate_rejects_config_missing_the_adopted_object():
    config, _ = scene(); actual = copy.deepcopy(config['instanceObjects'])
    config['instanceObjects'].pop(0)
    denied(lambda: protection.require_compiled_objects(config, actual))
