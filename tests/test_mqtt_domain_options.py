"""Real, scoped SQL pagination: saved user objects must not disappear after 100 rows."""
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy import select

from generic_agents.db import engine
from generic_agents.models import Entity, Capability, Workspace, Membership, User
from generic_agents.contracts import digest
from tests.test_vertical_slice import manifest, publish, entity


def test_numeric_objects_page_search_scope_and_no_raw_attributes(client, account):
    definition = manifest()
    definition['specification']['entity_schemas']['site']['properties']['count'] = {'type': 'integer'}
    package = publish(client, definition)
    with Session(engine()) as db:
        for i in range(123):
            db.add(Entity(workspace_id=account[2], namespace=package['namespace'], entity_type='site',
                external_id=f'station-{i:03}', schema_capability_id=package['id'],
                data={'name': f'观测站{i:03}', 'temperature': i}))
        other = Workspace(name='另一工作区'); db.add(other); db.flush()
        db.add(Entity(workspace_id=other.id, namespace=package['namespace'], entity_type='site',
            external_id='secret-station', schema_capability_id=package['id'],
            data={'name': '不可显示的对象', 'temperature': 42}))
        db.commit()
    found, cursor = [], ''
    while True:
        response = client.get('/api/mqtt/domain-options', params={'limit': 25, **({'after': cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        assert response.headers['X-Total-Count'] == '123'
        assert 'no-store' in response.headers['Cache-Control']
        found += response.json(); cursor = response.headers['X-Next-Cursor']
        if not cursor: break
    assert len(found) == len({x['id'] for x in found}) == 123
    assert all({f['key']: f['label'] for f in x['fields']} ==
               {'temperature': 'temperature', 'count': 'count'} for x in found)
    assert all('data' not in x and 'temperature' not in x for x in found)
    response = client.get('/api/mqtt/domain-options', params={'q': '观测站007'})
    assert response.status_code == 200 and len(response.json()) == 1
    assert response.json()[0]['label'] == '观测站007'
    single = client.get('/api/mqtt/domain-options/' + found[-1]['id'])
    assert single.status_code == 200 and single.json() == found[-1]
    assert client.get('/api/mqtt/domain-options', params={'q': '007', 'after': found[0]['id']}).status_code == 404


def test_only_numeric_published_schemas_and_read_only_options(client, account):
    package = publish(client, manifest())
    obj = entity(client, package)
    text_package = publish(client, manifest(name='text_only', specification={'entity_schemas': {
        'site': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}}}))
    with Session(engine()) as db:
        db.add(Entity(workspace_id=account[2], namespace=text_package['namespace'], entity_type='site',
            external_id=uuid4().hex, schema_capability_id=text_package['id'], data={'name': '纯文本对象'}))
        db.commit()
        before = [(x.id, x.version, digest(x.data)) for x in db.scalars(select(Entity)).all()]
    response = client.get('/api/mqtt/domain-options')
    assert response.status_code == 200 and [x['id'] for x in response.json()] == [obj['id']]
    with Session(engine()) as db:
        assert [(x.id, x.version, digest(x.data)) for x in db.scalars(select(Entity)).all()] == before
    assert client.get('/api/mqtt/domain-options', params={'instance_id': str(uuid4())}).status_code == 404


def test_rule_options_filter_local_scope_and_stable_paging(client, account):
    package = publish(client, manifest()); obj = entity(client, package)
    local = {'adapter': 'builtin', 'handler': 'numeric_rules'}
    # Register through the actual API; no model run is substituted by a fixed result.
    rule = publish(client, manifest('tool', name='local_a', allowed_agent_roles=['perception'],
        execution=local, data_policy={'cloud_allowed': False},
        specification={'rules': [{'id': 'hot', 'field': 'temperature', 'operator': 'gt', 'threshold': 30}]}))
    publish(client, manifest('tool', name='local_b', allowed_agent_roles=['perception'],
        execution=local, data_policy={'cloud_allowed': False},
        specification={'rules': [{'id': 'cold', 'field': 'temperature', 'operator': 'lt', 'threshold': 0}]}))
    publish(client, manifest('tool', name='not_perception', allowed_agent_roles=['data'],
        execution=local, data_policy={'cloud_allowed': False}, specification={'rules': []}))
    response = client.get('/api/mqtt/rule-options', params={'entity_id': obj['id'], 'limit': 1})
    assert response.status_code == 200, response.text
    assert response.headers['X-Total-Count'] == '2'
    cursor = response.headers['X-Next-Cursor']; assert cursor
    other = client.get('/api/mqtt/rule-options', params={'entity_id': obj['id'], 'limit': 1, 'after': cursor})
    assert other.status_code == 200 and other.json()[0]['id'] != response.json()[0]['id']
    assert other.headers['X-Next-Cursor'] == ''
    search = client.get('/api/mqtt/rule-options', params={'entity_id': obj['id'], 'q': 'local_a'})
    assert search.status_code == 200 and [x['id'] for x in search.json()] == [rule['id']]
    selected = client.get('/api/mqtt/rule-options/' + rule['id'], params={'entity_id': obj['id']})
    assert selected.status_code == 200 and selected.json()['id'] == rule['id']
    assert client.get('/api/mqtt/rule-options', params={'entity_id': str(uuid4())}).status_code == 404


def test_generated_instances_have_human_names_current_state_and_their_own_rules(client):
    from tests.mqtt_reuse_fixture import generated_sensor_instance, local_rule
    first = generated_sensor_instance(client, '实例甲')
    second = generated_sensor_instance(client, '实例乙')
    rule_a = local_rule(client, first, '甲本地规则', 38)
    rule_b = local_rule(client, second, '乙本地规则', 35)
    options = client.get('/api/mqtt/domain-options').json()
    assert len(options) == 2
    by_id = {x['id']: x for x in options}
    for item, label in [(first, '实例甲'), (second, '实例乙')]:
        option = by_id[item['object']['id']]
        assert option['label'] == label + '的监测对象' and option['type'] == '监测对象'
        assert option['instance']['instance_name'] == label and option['instance']['current'] is True
        assert option['fields'] == [{'key': item['field'], 'label': '温度'}]
    response = client.get('/api/mqtt/rule-options', params={'entity_id': first['object']['id']})
    assert response.status_code == 200 and [x['id'] for x in response.json()] == [rule_a['id']]
    search = client.get('/api/mqtt/rule-options', params={'entity_id': first['object']['id'], 'q': '甲本地'})
    assert search.status_code == 200 and [x['id'] for x in search.json()] == [rule_a['id']]
    assert client.get('/api/mqtt/rule-options/' + rule_b['id'], params={
        'entity_id': first['object']['id']}).status_code == 404
    instance = next(x for x in client.get('/api/instances').json() if x['id'] == first['release']['instance_id'])
    pause = client.post('/api/instances/' + instance['id'] + '/pause', json={'version': instance['version']})
    assert pause.status_code == 200, pause.text
    selected = client.get('/api/mqtt/domain-options/' + first['object']['id'])
    assert selected.status_code == 200 and selected.json()['instance']['current'] is False
    other = client.get('/api/mqtt/domain-options', params={'instance_id': second['release']['instance_id']})
    assert other.status_code == 200 and [x['id'] for x in other.json()] == [second['object']['id']]
