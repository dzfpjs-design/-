"""New provenance is a data-contract dependency, without changing legacy wires."""
import copy
import pytest

from generic_agents.instance_data_contract_dispatch import build_requirements, verify_requirements
from generic_agents.instance_data_requirements import build_requirements as old_build
from tests.test_instance_data_requirements_pure import snapshot


def modern():
    value = snapshot()
    value.update(object_origins={'合成对象': {'adoption_id': 'synthetic-adoption'}},
        full_semantic_schema_adoptions=[{'id': 'synthetic-adoption', 'plan_hash': 'a' * 64}],
        relation_types={'synthetic-relation': {'title': '关联'}},
        relation_origins={'关联': {'adoption_id': 'synthetic-adoption'}})
    return value


def test_legacy_contract_is_byte_equal_and_new_plan_is_not_inferred():
    value = snapshot()
    assert build_requirements(value) == old_build(value)
    new = modern(); before = copy.deepcopy(new)
    plan = build_requirements(new)
    assert plan['schema'] == 'instance-data-requirements-v2'
    assert plan['origin'] == 'system_design'
    assert set(plan['dependency_hashes']) >= {'semantic_runtime_plan', 'object_origins',
        'full_semantic_schema_adoptions', 'relation_types', 'relation_origins'}
    assert new == before and plan['confirmed_object_catalog'] == value['object_types']
    new['data_requirements'] = plan
    verify_requirements(new)


@pytest.mark.parametrize('key', ['object_origins', 'full_semantic_schema_adoptions',
    'relation_types', 'relation_origins', 'semantic_runtime_plan'])
def test_each_runtime_or_schema_provenance_change_invalidates_data_contract(key):
    value = modern(); value['semantic_runtime_plan'] = {'plan_hash': 'b' * 64}
    value['data_requirements'] = build_requirements(value)
    value[key] = [] if key == 'full_semantic_schema_adoptions' else {}
    with pytest.raises(ValueError): verify_requirements(value)


def test_missing_new_contract_and_missing_origins_cannot_select_legacy():
    value = modern()
    with pytest.raises(ValueError): verify_requirements(value)
    value.pop('relation_origins')
    with pytest.raises(ValueError): build_requirements(value)
