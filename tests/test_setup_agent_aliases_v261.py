"""Pure exact-path alias checks, not V5 source or business-quality acceptance."""
import copy

import pytest

from generic_agents import setup_agent_aliases_v261 as aliases

ALIAS = '业务驱动智能体'
CANONICAL = '业务智能体'


def test_only_exact_v5_step_agent_paths_change_and_each_change_is_auditable():
    original = {'agent': ALIAS, 'businesses': [
        {'name': ALIAS, 'inference_reason': ALIAS, 'steps': [
            {'agent': ALIAS, 'name': ALIAS, 'role': ALIAS,
                'citations': [{'quote': ALIAS, 'agent': ALIAS}], 'inference_reason': ALIAS},
            {'agent': '数据管理智能体'}]},
        {'steps': [{'agent': '人工确认'}, {'agent': ALIAS}]}],
        'objects': [{'agent': ALIAS}], 'questions': [ALIAS]}
    before = copy.deepcopy(original)
    result, traces = aliases.normalize_v5_agents(original, contract_version=5)
    expected = copy.deepcopy(original)
    expected['businesses'][0]['steps'][0]['agent'] = CANONICAL
    expected['businesses'][1]['steps'][1]['agent'] = CANONICAL
    assert result == expected and original == before
    assert traces == [{'policy': 'setup-v5-agent-aliases-v261',
        'path': '/businesses/0/steps/0/agent', 'original': ALIAS, 'canonical': CANONICAL},
        {'policy': 'setup-v5-agent-aliases-v261',
        'path': '/businesses/1/steps/1/agent', 'original': ALIAS, 'canonical': CANONICAL}]
    assert all(set(trace) == {'policy', 'path', 'original', 'canonical'} for trace in traces)


@pytest.mark.parametrize('agent', [ALIAS + ' ', ' ' + ALIAS, '业务驱动智能体。',
    '业务驱动Agent', CANONICAL, '多模感知智能体', '未知智能体', None, True, [ALIAS]])
def test_no_strip_fuzzy_conversion_or_unknown_agent_repair(agent):
    value = {'businesses': [{'steps': [{'agent': agent}]}]}
    result, traces = aliases.normalize_v5_agents(value, contract_version=5)
    assert result == value and traces == []


@pytest.mark.parametrize('version', [1, 2, 3, 4, 6, 5.0, '5', True, None])
def test_other_versions_and_coerced_versions_never_receive_the_v5_policy(version):
    value = {'businesses': [{'steps': [{'agent': ALIAS}]}]}
    result, traces = aliases.normalize_v5_agents(value, contract_version=version)
    assert result == value and result is not value and traces == []


@pytest.mark.parametrize('value', [None, [], {}, {'businesses': []},
    {'businesses': {'steps': [{'agent': ALIAS}]}}, {'businesses': [None]},
    {'businesses': [{'steps': {'agent': ALIAS}}]},
    {'businesses': [{'steps': [None, ALIAS, {'agent': {'label': ALIAS}}]}]}])
def test_malformed_and_empty_containers_are_not_rebuilt_into_valid_v5(value):
    before = copy.deepcopy(value)
    result, traces = aliases.normalize_v5_agents(value, contract_version=5)
    assert result == before and value == before and traces == []


def test_valid_alias_beside_an_invalid_step_does_not_erase_the_schema_error():
    value = {'businesses': [{'steps': [None, {'agent': ALIAS, 'unexpected': '保留未知字段'}]}]}
    result, traces = aliases.normalize_v5_agents(value, contract_version=5)
    # A null step and the extra field remain for the real downstream schema to
    # reject. Normalization itself never claims the result has passed V5.
    assert result['businesses'][0]['steps'][0] is None
    assert result['businesses'][0]['steps'][1] == {'agent': CANONICAL, 'unexpected': '保留未知字段'}
    assert traces[0]['path'] == '/businesses/0/steps/1/agent'
    assert value['businesses'][0]['steps'][1]['agent'] == ALIAS


def test_output_is_an_independent_copy_and_cannot_mutate_original_wire_projection():
    value = {'businesses': [{'steps': [{'agent': ALIAS, 'citations': [{'quote': ALIAS}]}]}]}
    before = copy.deepcopy(value)
    result, traces = aliases.normalize_v5_agents(value, contract_version=5)
    result['businesses'][0]['steps'][0]['citations'][0]['quote'] = '后续另存的人稿'
    result['businesses'].append({'steps': []})
    traces[0]['original'] = '不得回写原件'
    assert value == before


def test_second_pass_is_idempotent_and_does_not_duplicate_normalization_trace():
    value = {'businesses': [{'steps': [{'agent': ALIAS}]}]}
    first, first_traces = aliases.normalize_v5_agents(value, contract_version=5)
    second, second_traces = aliases.normalize_v5_agents(first, contract_version=5)
    assert second == first and second is not first
    assert len(first_traces) == 1 and second_traces == []
    assert first_traces[0]['original'] == ALIAS and value['businesses'][0]['steps'][0]['agent'] == ALIAS


def test_each_path_is_recorded_even_if_a_python_caller_shares_one_step_object():
    # Parsed JSON has separate objects; a direct caller must not lose a path's
    # audit entry merely because deepcopy preserves shared Python references.
    step = {'agent': ALIAS}
    value = {'businesses': [{'steps': [step]}, {'steps': [step]}]}
    result, traces = aliases.normalize_v5_agents(value, contract_version=5)
    assert [trace['path'] for trace in traces] == [
        '/businesses/0/steps/0/agent', '/businesses/1/steps/0/agent']
    assert all(b['steps'][0]['agent'] == CANONICAL for b in result['businesses'])
    assert step['agent'] == ALIAS
