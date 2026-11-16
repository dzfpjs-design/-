"""Explicit synthetic schemas and thresholds, generated through the real APIs."""
import json
from uuid import uuid4

from generic_agents.instance_compiler import signature_value
from tests.test_instance_workspace import configuration, save, enable


OBJECT_TYPES = [{'name': '监测对象', 'fields': [
    {'label': '名称', 'kind': '文本', 'required': True},
    {'label': '温度', 'kind': '数值', 'required': False}]}]


def generated_sensor_instance(client, name):
    config = configuration(); config['instanceProfile']['name'] = name
    for flow in config['flows'].values():
        flow['confirmedSignature'] = json.dumps(signature_value(config, flow), ensure_ascii=False)
    revision = save(client, config, review_automation=True)
    member = client.get('/api/instances/members').json()[0]
    body = {'revision': revision, 'objects': OBJECT_TYPES, 'role_bindings': {'核验负责人': member['id']}}
    preview = client.post('/api/instances/check', json=body)
    assert preview.status_code == 200, preview.text
    result = client.post('/api/instances', json={**body,
        'confirmed_configuration_hash': preview.json()['configuration_hash'], 'confirm_configuration': True})
    assert result.status_code == 201, result.text
    release = result.json(); enable(client, release)
    kind = release['snapshot']['object_types'][0]
    name_field = next(f['key'] for f in kind['fields'] if f['label'] == '名称')
    field = next(f['key'] for f in kind['fields'] if f['label'] == '温度')
    response = client.post('/api/instances/' + release['instance_id'] + '/objects', json={
        'release_id': release['id'], 'object_type': kind['key'],
        'attributes': {name_field: name + '的监测对象', field: 20}})
    assert response.status_code == 201, response.text
    return {'release': release, 'object': response.json(), 'field': field, 'configuration': config}


def local_rule(client, instance, title, threshold):
    response = client.post('/api/local-rules', json={'request_id': str(uuid4()), 'title': title,
        'entity_id': instance['object']['id'], 'rationale': '仅用于来源复用合成验收，不作为现场阈值',
        'rules': [{'id': 'hot', 'field': instance['field'], 'unit': 'C', 'operator': 'gt', 'threshold': threshold}]})
    assert response.status_code == 201, response.text
    row = response.json()
    result = client.post('/api/local-rules/' + row['id'] + '/check', json={
        'expected_version': row['version'], 'values': {instance['field']: 39.5}})
    assert result.status_code == 200, result.text
    row = result.json()
    result = client.post('/api/local-rules/' + row['id'] + '/publish', json={
        'expected_version': row['version'], 'reason': '明确确认合成规则及其未验收的现场边界', 'confirm': True})
    assert result.status_code == 200, result.text
    return result.json()


def route_body(gateway, instance, rule):
    return {'expected_version': gateway['version'], 'request_id': str(uuid4()), 'topic': 'sensor/one',
        'entity_id': instance['object']['id'], 'extractor_id': rule['id'],
        'field_links': {'temperature': instance['field']}, 'include_existing': False}
