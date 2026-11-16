"""Normal PG weather Source issuance over an actual authored release.

The fixture uses real loopback discovery and ordinary registry, confirmation,
adoption, compilation, activation, simulation-environment and device-approval
APIs. Source publication is metadata preparation, never an Observation or a
paid model run. No final release or requirements are inserted or mocked.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import device_gateway_sources_v270 as sources
from generic_agents import device_gateway_tools_v269 as tools
from generic_agents import models as m
from generic_agents.authored_service_workflow_contracts_v270 import PREFIX as AUTHORING
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.device_gateway_models_v268 import DeviceGatewayConnectionV268 as Connection
from generic_agents.device_gateway_tool_models_v269 import DeviceGatewayCredentialSnapshotV269 as Snapshot
from generic_agents.instance_compiler import signature_value
from tests.test_authored_service_workflow_pg_v270 import good, prepared
from tests.test_device_gateway_tools_v269 import managed, scene
from tests.test_device_gateway_v268 import BASE, TOKEN, nonce, read
from tests.test_instance_workspace import save


WEATHER_FIELDS = {
    'temperature_c': ('气温', '°C'),
    'humidity_percent': ('相对湿度', '%'),
    'wind_m_s': ('风速', 'm/s'),
    'pressure_hpa': ('气压', 'hPa'),
}
OBJECTS = [{'name': '天气观测站', 'fields': [
    {'label': '名称', 'kind': '文本', 'required': True},
    *[{'label': label, 'kind': '数值', 'required': False, 'unit': unit}
        for label, unit in WEATHER_FIELDS.values()],
]}]


def _runtime_counts(workspace_id):
    with Session(engine()) as db:
        return {model.__name__: db.scalar(select(func.count()).select_from(model).where(
            model.workspace_id == workspace_id)) for model in
            (m.Task, m.Invocation, m.CloudCall, m.Observation, m.Acquisition)}


def _source_count(db, workspace_id):
    return db.scalar(select(func.count()).select_from(m.Capability).where(
        m.Capability.workspace_id == workspace_id, m.Capability.namespace == sources.NAMESPACE))


def _published_weather_release(s):
    """Use only confirmed configuration and normal authoring/publication APIs."""
    body, extractor, material = prepared(s)
    draft = good(s.client.get('/api/setup/draft'))
    cfg = copy.deepcopy(draft['configuration'])
    cfg['instanceProfile'].update(name='公开天气设备模拟接入', domain='天气设备模拟联调',
        goal='在独立模拟范围核对天气采样原号回执，并保留感知与数据依据。', simulation_v176=True)
    flow = cfg['flows'][body['business_name']]
    flow['confirmedSignature'] = json.dumps(signature_value(cfg, flow), ensure_ascii=False)
    save(s.client, cfg, review_automation=True)
    current = good(s.client.get('/api/setup/draft'))
    body = {**body, 'configuration_revision': current['revision'],
        'configuration_hash': digest(current['configuration'])}
    preview = good(s.client.post(AUTHORING + '/preview', json=body))
    assert preview['binding']['source_materials'][0]['id'] == material['id']
    assert preview['binding']['observation_extractor_pins'][extractor['id']]['capability_hash'] == extractor['content_hash']
    command = {**body, 'schema': 'authored-service-workflow-adopt-command-v270',
        'request_id': nonce(), 'preview_hash': preview['preview_hash'], 'consent': True}
    raw = ('  ' + json.dumps(command, ensure_ascii=False) + '\n').encode('utf-8')
    adopted = good(s.client.post(AUTHORING + '/adopt', content=raw), 201)
    recovery = good(s.client.get(AUTHORING + '/requests/' + command['request_id'],
        params={'expected_hash': hashlib.sha256(raw).hexdigest()}))
    assert recovery['found'] and recovery['result'] == adopted
    current = good(s.client.get('/api/setup/draft'))
    publication = {'revision': current['revision'], 'objects': copy.deepcopy(OBJECTS),
        'role_bindings': {key: actor['user_id'] for key, actor in preview['binding']['role_bindings'].items()}}
    checked = good(s.client.post('/api/instances/check', json=publication))
    assert not checked['general_issues'] and all(row['available'] for row in checked['businesses']), [
        (row['name'], row['issues']) for row in checked['businesses']]
    release = good(s.client.post('/api/instances', json={**publication,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': nonce()}), 201)
    assert release['status'] == 'prepared' and release['snapshot']['profile']['simulation_v176'] is True
    instance = next(row for row in good(s.client.get('/api/instances')) if row['id'] == release['instance_id'])
    activated = good(s.client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'],
        'confirm_human_workflow_scope': True, 'confirm_workflow_scope': True}))
    assert activated['active_release_id'] == release['id'] and activated['status'] == 'partially_active'
    environment = good(s.client.post('/api/workspace/simulation-v176/environments', json={
        'request_id': nonce(), 'instance_id': instance['id'], 'release_id': release['id'],
        'note': '独立天气设备软件模拟核验；不表示现场天气或物理设备连接。', 'confirm_simulation': True}), 201)
    weather = next(row for row in release['snapshot']['object_types'] if row['name'] == OBJECTS[0]['name'])
    fields = {field['label']: field['key'] for field in weather['fields']}
    schema = release['snapshot']['entity_schemas'][weather['key']]
    links = {}
    for supplier_field, (label, unit) in WEATHER_FIELDS.items():
        field_key = fields[label]
        assert schema['properties'][field_key]['type'] == 'number'
        assert schema['properties'][field_key]['unit'] == unit
        links[supplier_field] = {'field_key': field_key, 'unit': unit}
    declarations = [output for business in release['snapshot']['data_requirements']['businesses']
        for output in business['outputs'] if output.get('kind') == 'asynchronous_perception']
    assert len(declarations) == 1
    declared = declarations[0]
    assert declared['capability_id'] == extractor['id'] and declared['capability_hash'] == extractor['content_hash']
    assert declared['release_version'] == extractor['release_version']
    assert declared['output_schema'] == extractor['data']['output_schema']
    assert declared['output_schema_hash'] == digest(extractor['data']['output_schema'])
    assert declared['node_id'] is None and declared['step_id'] == 'perception:' + extractor['id']
    assert declared['declaration_kind'] == 'registered_observation_extractor'
    assert declared['source_binding_hash'] == preview['binding']['binding_hash']
    graph = release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['binding']['graph']
    assert extractor['id'] not in [node['execution']['capability_id'] for node in graph['nodes'] if node['kind'] == 'ability']
    return NS(release=release, environment=environment, extractor=extractor,
        entity_type=weather['key'], links=links, preview=preview)


def _ready_source(s):
    publication = _published_weather_release(s)
    device = good(s.client.get('/api/sensing/devices/' + s.grant['sensor_device_id']))
    assert device['status'] == 'review'
    approved = good(s.client.post('/api/sensing/devices/' + device['id'] + '/approve', json={
        'expected_version': device['version'], 'reason': '核对独立模拟天气设备的统一身份；不声明现场设备已连接。'}))
    assert approved['status'] == 'enabled' and approved['approved_by'] == s.actor_id
    connection = read(s)
    grant = next(row for row in connection['grants'] if row['id'] == s.grant['id'])
    assert grant['effective']
    body = {'request_id': nonce(), 'expected_version': connection['version'],
        'grant_id': grant['id'], 'grant_expected_version': grant['version'],
        'environment_id': publication.environment['id'], 'entity_type': publication.entity_type,
        'links': publication.links, 'extractor_id': publication.extractor['id'],
        'extractor_hash': publication.extractor['content_hash'], 'confirm_simulation': True,
        'reason': '明确为已启用模拟版本登记天气字段来源；不启动补采或创建观测。'}
    return publication, body


def test_normal_source_issuance_get_recovery_and_tool_share_actual_revision_snapshot(managed):
    s = managed
    publication, body = _ready_source(s)
    before = _runtime_counts(s.workspace_id)
    assert all(value == 0 for value in before.values())
    url = BASE + '/' + s.connection_id + '/sources'
    created = good(s.client.post(url, json=body))
    source_id = created['capability']['id']
    assert created['operation'] == sources.OPERATION and created['capability']['status'] == 'published'
    assert created['simulation'] is True and created['field_verified'] is False
    assert created['observation_created'] is False
    assert good(s.client.post(url, json=body)) == created
    recovered = good(s.client.get(BASE + '/requests/' + body['request_id']))
    assert recovered == {'found': True, 'request_id': body['request_id'], 'result': created}
    inventory = good(s.client.get(url))
    item = next(row for row in inventory['sources'] if row['id'] == source_id)
    assert item['integrity_valid'] and item['configuration_current'] and item['availability_reason'] == 'current'
    assert item['environment_id'] == publication.environment['id']
    assert item['device_external_id'] == sources.DEVICE and item['observation_created'] is False
    another = good(s.client.post(url, json={**body, 'request_id': nonce()}))
    assert another['capability']['id'] == source_id
    with Session(engine()) as db:
        cap = db.get(m.Capability, source_id)
        assert cap.kind == 'source' and cap.status == 'published'
        assert cap.content_hash == cap.validated_hash == digest(cap.data)
        assert cap.data['execution'][sources.EXECUTION_KEY] == sources.SCOPE
        assert 'secret_ref' not in cap.data['execution'] and 'headers' not in cap.data['execution']
        assert cap.data['execution']['endpoint'] == s.origin + '/acquisitions'
        assert cap.data['execution']['receipt_endpoint'] == s.origin + '/acquisitions/receipts'
        context = sources.validate_source(db, s.p, cap)
        tool = db.get(m.Capability, s.capability_id)
        tool_context = tools.validate_tool(db, s.p, tool)
        assert context['snapshot'].id == tool_context['snapshot'].id
        assert context['snapshot'].connection_id == s.connection_id
        assert context['snapshot'].credential_revision == 1
        assert context['snapshot'].token_cipher == db.get(Connection, s.connection_id).token_cipher
        assert TOKEN not in context['snapshot'].token_cipher
        assert db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.workspace_id == s.workspace_id)) == 1
        assert _source_count(db, s.workspace_id) == 1
        source_proof = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.object_id == source_id, m.Audit.action == sources.ISSUED))
        tool_proof = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.object_id == s.capability_id, m.Audit.action == tools.ISSUED))
        assert source_proof.data['snapshot_id'] == tool_proof.data['snapshot_id'] == context['snapshot'].id
        assert source_proof.data['binding'] == context['binding']
        assert context['binding']['mapping']['links'] == publication.links
        assert context['binding']['mapping']['scope']['release_id'] == publication.release['id']
        for model in (m.Capability, m.Audit, m.Job):
            assert all(TOKEN not in json.dumps(row.data, ensure_ascii=False)
                for row in db.scalars(select(model).where(model.workspace_id == s.workspace_id)))
    for response in (created, recovered, inventory, another):
        public = json.dumps(response, ensure_ascii=False)
        assert TOKEN not in public and 'token_cipher' not in public and 'snapshot_id' not in public
    assert _runtime_counts(s.workspace_id) == before


def test_deleted_issued_snapshot_with_source_and_tool_remnants_is_rejected_without_repair(managed):
    s = managed
    _, body = _ready_source(s)
    url = BASE + '/' + s.connection_id + '/sources'
    created = good(s.client.post(url, json=body))
    source_id = created['capability']['id']
    before = _runtime_counts(s.workspace_id)
    with Session(engine()) as db:
        snapshot = db.scalar(select(Snapshot).where(Snapshot.workspace_id == s.workspace_id))
        assert snapshot is not None
        snapshot_id = snapshot.id
        assert db.scalar(select(func.count()).select_from(m.Audit).where(
            m.Audit.workspace_id == s.workspace_id, m.Audit.data['snapshot_id'].as_string() == snapshot_id,
            m.Audit.action.in_([tools.ISSUED, sources.ISSUED]))) == 2
        # This is deliberate damage in a disposable negative-test database.
        # All positive authority above came through ordinary saved APIs.
        db.delete(snapshot)
        db.commit()
    blocked = s.client.post(url, json={**body, 'request_id': nonce()})
    assert blocked.status_code == 409, blocked.text[:500]
    inventory = good(s.client.get(url))
    item = next(row for row in inventory['sources'] if row['id'] == source_id)
    assert item['integrity_valid'] is False and item['configuration_current'] is False
    assert item['availability_reason'] == 'integrity_invalid'
    with Session(engine()) as db:
        assert db.get(Snapshot, snapshot_id) is None
        assert db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.workspace_id == s.workspace_id)) == 0
        assert _source_count(db, s.workspace_id) == 1
        assert db.scalar(select(func.count()).select_from(m.Audit).where(
            m.Audit.workspace_id == s.workspace_id, m.Audit.data['snapshot_id'].as_string() == snapshot_id,
            m.Audit.action.in_([tools.ISSUED, sources.ISSUED]))) == 2
    assert TOKEN not in blocked.text and TOKEN not in json.dumps(inventory)
    assert _runtime_counts(s.workspace_id) == before
