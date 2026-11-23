"""Authenticated V288 instance checks with synthetic objects and no cloud calls."""
from uuid import uuid4

from generic_agents import authored_goal_data_workflow_contracts_v288 as commands
from generic_agents import business_goal_ready_runtime_v288 as reasoning
from generic_agents import workflow_goal_data_route_runtime_v288 as local
from generic_agents import workflow_goal_evaluation_packet_v288 as packet
from generic_agents import workflow_task_context_v196 as reader
from generic_agents.instance_compiler import identifier
from tests.test_authored_decision_workflow_pg_v286 import prepared_decision
from tests.test_authored_goal_data_workflow_pg_v288 import tool
from tests.test_authored_goal_data_workflow_v288 import policy
from tests.test_authored_service_workflow_pg_v270 import good, publish
from tests.test_device_gateway_v268 import scene
from tests.test_device_gateway_tools_v269 import managed
from tests.test_instance_workspace import OBJECTS


def adopt_graph(s, *, fields=None):
    original, old_pins, model, _candidate, _extractor, _material = prepared_decision(s)
    context = [x for x in good(s.client.get('/api/capabilities'))
               if x['data']['execution'].get('handler') == reader.HANDLER]
    assert len(context) == 1
    plan = publish(s.client, tool(local, 'business', {'adapter': 'workflow_service',
        'service_actor': 'execution_service', 'operation': local.OP, 'side_effect': True}))
    evaluation = publish(s.client, tool(packet, 'data', {'adapter': 'workflow_service',
        'service_actor': 'reader_service', 'operation': packet.OP, 'side_effect': True}))
    reason = publish(s.client, tool(reasoning, 'business', {'adapter': reasoning.ADAPTER,
        'model_profile_id': model['id'], 'model_profile_hash': model['content_hash'], 'side_effect': False},
        dependencies=[model['id']], cloud=True))
    pins = {'context_reader': context[0], 'local_prepare': plan, 'resense': old_pins['resense'],
            'evaluation_packet': evaluation, 'packet': old_pins['packet'], 'reasoning': reason}
    if fields is not None:
        pin = next(pin for candidate in original['candidate_specs'] for pin in candidate['capability_pins']
                   if pin['delivery'] == 'requested_observation')
        pin['ports'] = [{'field': field, 'pointer': '/requested_observation/' + field} for field in fields]
    body = {**original, 'schema': 'authored-goal-data-workflow-preview-command-v288',
        'preparation_policy': policy(), 'node_actions': commands.NODE_ACTIONS,
        'external_node_ids': commands.EXTERNAL_NODE_IDS,
        'tool_bindings': {name: {'capability_id': row['id'], 'capability_hash': row['content_hash'],
                                 'actor_id': s.actor_id} for name, row in pins.items()}}
    preview = good(s.client.post(commands.PREFIX + '/preview', json=body))
    good(s.client.post(commands.PATH, json={**body, 'schema': 'authored-goal-data-workflow-adopt-command-v288',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True}), 201)
    current = good(s.client.get('/api/setup/draft'))
    return {'revision': current['revision'], 'objects': OBJECTS,
            'role_bindings': {key: actor['user_id'] for key, actor in preview['binding']['role_bindings'].items()}}


def test_unmatched_candidate_does_not_mark_business_available(managed):
    s = managed
    publication = adopt_graph(s)
    checked = good(s.client.post('/api/instances/check', json=publication))
    business = next(row for row in checked['businesses'] if row.get('candidate_observation_scope'))
    assert business['candidate_observation_scope']['status'] == 'pending'
    assert business['candidate_observation_scope']['object_names'] == []
    assert not business['available'] and business['execution_mode'] == 'blocked'
    assert any('当前对象定义尚未匹配' in text for text in business['issues'])
    release = good(s.client.post('/api/instances', json={**publication,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    assert business['id'] not in release['available_business_ids']
    current = next(item for item in good(s.client.get('/api/instances')) if item['id'] == release['instance_id'])
    denied = s.client.post('/api/instances/' + release['instance_id'] + '/activate', json={
        'version': current['version'], 'release_id': release['id'], 'business_ids': [business['id']],
        'confirm_workflow_scope': True})
    assert denied.status_code == 409
    assert '所选业务仍有执行缺口' in denied.json()['detail']


def test_matching_candidate_reports_only_field_compatible_type(managed):
    s = managed
    fields = [identifier('field', '温度'), identifier('field', '湿度')]
    publication = adopt_graph(s, fields=fields)
    publication['objects'] = [
        {'name': '完整对象', 'fields': [{'label': '名称', 'kind': '文本', 'required': True},
            {'label': '温度', 'kind': '数值', 'required': False, 'unit': '℃'},
            {'label': '湿度', 'kind': '数值', 'required': False, 'unit': '%'}]},
        {'name': '部分对象', 'fields': [{'label': '名称', 'kind': '文本', 'required': True},
            {'label': '温度', 'kind': '数值', 'required': False, 'unit': '℃'}]}]
    checked = good(s.client.post('/api/instances/check', json=publication))
    business = next(row for row in checked['businesses'] if row.get('candidate_observation_scope'))
    assert business['available'] and business['execution_mode'] == 'confirmed_graph_workflow'
    assert business['candidate_observation_scope'] == {'status': 'matched', 'object_names': ['完整对象'],
        'check': 'requested_observation_field_presence_only'}
    release = good(s.client.post('/api/instances', json={**publication,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    assert business['id'] in release['available_business_ids']
    current = next(item for item in good(s.client.get('/api/instances')) if item['id'] == release['instance_id'])
    enabled = good(s.client.post('/api/instances/' + release['instance_id'] + '/activate', json={
        'version': current['version'], 'release_id': release['id'], 'business_ids': [business['id']],
        'confirm_workflow_scope': True}))
    assert enabled['status'] == 'partially_active'


def test_old_release_keeps_its_existing_activation_contract(managed, monkeypatch):
    from generic_agents import instance_candidate_fields_v1
    s = managed
    publication = adopt_graph(s)
    # Recreate the previous compiler's behaviour only inside this synthetic
    # transaction sequence. The persisted old release must not be rewritten.
    with monkeypatch.context() as previous:
        previous.setattr(instance_candidate_fields_v1, 'observation_scope', lambda *_: None)
        checked = good(s.client.post('/api/instances/check', json=publication))
        release = good(s.client.post('/api/instances', json={**publication,
            'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    assert release['available_business_ids']
    current = next(item for item in good(s.client.get('/api/instances')) if item['id'] == release['instance_id'])
    enabled = good(s.client.post('/api/instances/' + release['instance_id'] + '/activate', json={
        'version': current['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    assert enabled['status'] == 'partially_active'
