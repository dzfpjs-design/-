"""Fast real-publication preflight before the mature-Goal long tests."""
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.test_same_goal_advice_manual_append_pg import managed, scene, reject_non_loopback_network


def test_published_candidate_ports_match_real_object_fields(deepseek_3600_publication):
    p = deepseek_3600_publication
    obj = next(item for item in p.release['snapshot']['object_types'] if item['name'] == '天气观测站')
    fields = {item['label']: item['key'] for item in obj['fields']}
    nodes = p.preview['binding']['graph']['nodes']
    reasoning = next(item for item in nodes if item['id'] == 'reasoning')
    specs = reasoning['execution']['arguments']['candidate_specs']
    ports = [port for spec in specs for pin in spec['capability_pins']
        if pin['delivery'] == 'requested_observation' for port in pin['ports']]
    assert {port['field'] for port in ports} == {fields['气温'], fields['相对湿度']}
    assert all(port['pointer'] == '/requested_observation/' + port['field'] for port in ports)
