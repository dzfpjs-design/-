"""Static object-field compatibility is narrower than device or runtime proof."""
from generic_agents.authored_goal_data_workflow_contracts_v288 import BINDING
from generic_agents.instance_candidate_fields_v1 import issue, observation_scope


def binding(*pins):
    return {'schema': BINDING, 'candidate_specs': [{'capability_pins': list(pins)}]}


def requested(*names):
    return {'delivery': 'requested_observation', 'ports': [{'field': name} for name in names]}


def test_scope_names_only_types_with_all_required_fields():
    catalog = [{'key': 'a', 'name': '混合对象'}, {'key': 'b', 'name': '温度对象'}]
    schemas = {'a': {'properties': {'t': {'type': 'number'}, 'h': {'type': 'number'}}},
               'b': {'properties': {'t': {'type': 'number'}}}}
    result = observation_scope(binding(requested('t', 'h')), catalog, schemas)
    assert result == {'status': 'matched', 'object_names': ['混合对象'],
                      'check': 'requested_observation_field_presence_only'}
    assert issue(result) is None


def test_fields_split_between_types_and_unselected_objects_are_pending():
    catalog = [{'key': 'a', 'name': '温度'}, {'key': 'b', 'name': '湿度'}]
    schemas = {'a': {'properties': {'t': {}}}, 'b': {'properties': {'h': {}}}}
    for objects, definitions in [(catalog, schemas), ([], {})]:
        result = observation_scope(binding(requested('t', 'h')), objects, definitions)
        assert result['status'] == 'pending' and result['object_names'] == []
        assert '当前对象定义尚未匹配' in issue(result)


def test_empty_required_ports_cannot_be_claimed_compatible():
    result = observation_scope(binding(requested()), [{'key': 'a', 'name': '对象'}],
                               {'a': {'properties': {'t': {}}}})
    assert result['status'] == 'pending' and issue(result)


def test_other_graphs_and_pure_output_candidates_do_not_change():
    assert observation_scope({'schema': 'old-version'}, [], {}) is None
    assert observation_scope(binding({'delivery': 'output', 'ports': [{'field': 't'}]}), [], {}) is None
