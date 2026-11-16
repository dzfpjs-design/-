"""Normal authenticated authored-origin API on Root's disposable PostgreSQL.

No paid attempt/provider stub/direct graph issuance is used. These tests stop
at real publication; V207 source/175/worker and owned G are separate gates.
"""
import copy
import hashlib
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, authored_workflow_store_v208 as store
from generic_agents.authored_workflow_models_v208 import AuthoredWorkflowAdoption as Adoption, AuthoredWorkflowCommand as Command
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents import workflow_document_packet_v207 as packet
from tests.test_instance_workspace import configuration, save, OBJECTS

BASE = store.PREFIX


def good(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def published_packet(client):
    value = Manifest(kind='tool', namespace='authored_v208', name='document_packet_' + uuid4().hex[:12],
        version='1.0.0', owner='公开测试', description='只从批准原来源生产本地资料包', allowed_agent_roles=['data'],
        input_schema=packet.input_schema(), output_schema=packet.output_schema(),
        execution={'adapter': packet.ADAPTER, 'side_effect': True}, dependencies=[],
        data_policy={'cloud_allowed': False}, budget={'max_attempts': 1, 'max_concurrency': 1},
        specification={'schema': 'workflow-document-packet-contract-v207', 'operation': 'build_approved_document_packet'}).model_dump(mode='json')
    draft = good(client.post('/api/capabilities', json=value), 201)
    checked = good(client.post('/api/capabilities/' + draft['id'] + '/validate', json={'expected_version': draft['version'], 'reason': '核对本地资料包合同'}))
    return good(client.post('/api/capabilities/' + draft['id'] + '/publish', json={'expected_version': checked['version'], 'reason': '供明确人工编制图采用'}))


def configured(client):
    value = configuration(); policy = value['automation']['policies']['资料核验']
    policy['triggers'] = ['人工发起']; policy['rules']['data'] = {'mode': '自动运行'}
    save(client, value, review_automation=True)
    return good(client.get('/api/setup/draft'))


def preview_body(client, cap, name='资料核验'):
    current = good(client.get(BASE + '/context', params={'business_name': name}))
    actor = good(client.get('/api/me'))['user_id']
    return {'configuration_revision': current['configuration_revision'], 'configuration_hash': current['configuration_hash'],
        'business_name': name, 'packet_capability_id': cap['id'], 'packet_capability_hash': cap['content_hash'],
        'packet_actor_id': actor, 'role_bindings': {role: actor for role in current['human_roles']},
        'reasoning': None, 'candidate_proposals': [], 'node_actions': {'packet': 'data'},
        'external_node_ids': [], 'prior_origin': current['prior_origin']}


def adopted(client, *, cap=None):
    configured(client); cap = cap or published_packet(client)
    body = preview_body(client, cap); preview = good(client.post(BASE + '/preview', json=body))
    command = {**body, 'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True}
    submitted = client.post(BASE + '/adopt', json=command)
    result = good(submitted, 201)
    return cap, preview, command, result


def counts(workspace):
    with Session(engine()) as db:
        return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace))
            for model in (Adoption, Command, m.SetupRevision, m.Job, m.Invocation, m.CloudCall))


def test_normal_adoption_is_original_command_and_flow_without_jobs_or_paid_authority(client):
    initial = configured(client); cap = published_packet(client); body = preview_body(client, cap)
    workspace = good(client.get('/api/me'))['workspace_id']; before = counts(workspace)
    preview = good(client.post(BASE + '/preview', json=body))
    assert counts(workspace) == before
    command = {**body, 'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True}
    submitted = client.post(BASE + '/adopt', json=command)
    result = good(submitted, 201)
    current = good(client.get('/api/setup/draft'))
    assert current['configuration']['flows'] == initial['configuration']['flows']
    assert current['configuration'][store.CONFIG_KEY]['资料核验'] == result['origin']
    assert counts(workspace)[-3:] == before[-3:]
    again = good(client.post(BASE + '/adopt', json=command), 201)
    assert again == result
    recovered = good(client.get(BASE + '/requests/' + command['request_id'],
        params={'expected_hash': hashlib.sha256(submitted.request.content).hexdigest()}))
    assert recovered['found']


def test_revision_keeps_old_original_and_rejects_stale_head_without_new_writes(client):
    cap, first_preview, original, first = adopted(client)
    old = good(client.get(BASE + '/adoptions/' + first['id']))
    replacement = published_packet(client)
    next_body = preview_body(client, replacement); assert next_body['prior_origin'] == first['origin']
    preview = good(client.post(BASE + '/preview', json=next_body))
    command = {**next_body, 'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True}
    second = good(client.post(BASE + '/adopt', json=command), 201)
    assert second['id'] != first['id'] and good(client.get(BASE + '/adoptions/' + first['id'])) == old
    assert good(client.post(BASE + '/adopt', json=original), 201) == first
    current = good(client.get('/api/setup/draft')); workspace = good(client.get('/api/me'))['workspace_id']; before = counts(workspace)
    stale = {**command, 'request_id': str(uuid4()), 'configuration_revision': current['revision'],
        'configuration_hash': digest(current['configuration'])}
    assert client.post(BASE + '/adopt', json=stale).status_code == 409
    assert counts(workspace) == before


def test_independent_audit_blocks_deleted_command_row_and_marker_without_reconstruction(client):
    _, _, _, result = adopted(client)
    account = good(client.get('/api/me')); workspace = account['workspace_id']
    with Session(engine()) as db:
        row = db.get(Adoption, result['id'])
        command = db.scalar(select(Command).where(Command.adoption_id == row.id))
        request_id, original_hash = command.request_id, command.body_sha256
        config = db.get(m.SetupDraft, row.configuration_id)
        value = copy.deepcopy(config.data); value.pop(store.CONFIG_KEY); config.data = value
        db.delete(command); db.flush(); db.delete(row); db.commit()
    before = counts(workspace)
    assert client.get(BASE + '/requests/' + request_id, params={'expected_hash': original_hash}).status_code == 409
    assert client.get(BASE + '/context', params={'business_name': '资料核验'}).status_code == 409
    assert counts(workspace) == before and before[:2] == (0, 0)


def test_plain_config_cannot_strip_authored_origin_or_mutate_adopted_flow(client):
    _, _, _, result = adopted(client); draft = good(client.get('/api/setup/draft'))
    for edit in ('marker', 'flow'):
        value = copy.deepcopy(draft['configuration'])
        if edit == 'marker': value.pop(store.CONFIG_KEY)
        else: value['flows']['资料核验']['steps'][0]['name'] = '擅自换义'
        assert client.put('/api/setup/draft', json={'revision': draft['revision'], 'configuration': value}).status_code == 409
    assert good(client.get('/api/setup/draft')) == draft


def test_actual_check_generation_uses_authored_origin_and_typed_port(client):
    _, preview, _, result = adopted(client)
    draft = good(client.get('/api/setup/draft'))
    body = {'revision': draft['revision'], 'objects': OBJECTS,
        'role_bindings': {role: actor['user_id'] for role, actor in preview['binding']['role_bindings'].items()}}
    checked = good(client.post('/api/instances/check', json=body))
    assert not checked['general_issues']
    release = good(client.post('/api/instances', json={**body, 'confirmed_configuration_hash': checked['configuration_hash'],
        'confirm_configuration': True}), 201)
    business = next(item for item in release['snapshot']['businesses'] if item['name'] == '资料核验')
    assert business['execution_mode'] == 'confirmed_graph_workflow' and business['authored_workflow_origin'] == result['origin']
    assert release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['binding']['graph'] == preview['binding']['graph']
