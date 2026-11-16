"""Ordinary authenticated PG adoption/recovery/publication without model IO.

The existing managed fixture supplies its real loopback discovery and approved
test device. Only normal registry and V278/V208 APIs issue the new adoption.
Discarding a client response exercises cold recovery, not a real network fault.
No Task, paid call, decision answer, DataRun, Gap, Observation or ACK is created.
Root executes this suite on its disposable PostgreSQL; authoring was static.
"""
import copy
import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import authored_decision_workflow_contracts_v278 as commands
from generic_agents import authored_workflow_store_v208 as common
from generic_agents import business_decision_runtime_v276 as reasoning
from generic_agents import workflow_decision_tool_planner_v277 as planner
from generic_agents.authored_workflow_models_v208 import AuthoredWorkflowAdoption as Adoption, AuthoredWorkflowCommand as Command
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from tests.test_authored_service_workflow_pg_v270 import prepared, good, publish
from tests.test_authored_decision_workflow_v278 import command as command_shape
from tests.test_device_gateway_v268 import scene
from tests.test_device_gateway_tools_v269 import managed
from tests.test_instance_workspace import OBJECTS
from tests.test_workflow_task_packet_registration_v199 import manifest as data_packet_manifest

BASE = commands.PREFIX


def counts(workspace):
    with Session(engine()) as db:
        return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace))
            for model in (m.Task, m.Invocation, m.CloudCall))


def ledger_snapshot(workspace):
    """Cold snapshot of existing common-ledger rows; never manufacture an origin."""
    with Session(engine()) as db:
        adoptions = list(db.scalars(select(Adoption).where(Adoption.workspace_id == workspace)))
        requests = list(db.scalars(select(Command).where(Command.workspace_id == workspace)))
        return {'adoptions': {row.id: {'version': row.version, 'binding_hash': row.binding_hash,
                    'accepted_revision': row.accepted_revision, 'data': copy.deepcopy(row.data)} for row in adoptions},
            'commands': {row.id: {'version': row.version, 'path': row.path, 'exact_body': row.exact_body,
                    'body_sha256': row.body_sha256, 'command_hash': row.command_hash,
                    'data': copy.deepcopy(row.data)} for row in requests}}


def prepared_decision(s):
    # Reuse only public material/configuration/actor setup and actual managed
    # discovery. Do not execute a V270 adoption or either family test function.
    original, extractor, material = prepared(s)
    listed = {row['id']: row for row in good(s.client.get('/api/capabilities'))}
    legacy_reasoner = listed[original['tool_bindings']['reasoning']['capability_id']]
    model = listed[legacy_reasoner['data']['execution']['model_profile_id']]
    assert model['kind'] == 'foundation_model' and model['status'] == 'published'
    assert model['content_hash'] == legacy_reasoner['data']['execution']['model_profile_hash']
    assert digest(model['data']) == model['content_hash']
    data_packet = publish(s.client, data_packet_manifest())
    reason = publish(s.client, Manifest(kind='tool', namespace='decision_pg_v278',
        name='decision_' + uuid4().hex[:12], version='1.0.0', owner='独立工程核验账号',
        description='只比较两份真实选择的原包；采用不调用模型或证明当前数值', allowed_agent_roles=['business'],
        input_schema=reasoning.input_schema(), output_schema=reasoning.output_schema(),
        execution={'adapter': reasoning.ADAPTER, 'model_profile_id': model['id'],
            'model_profile_hash': model['content_hash'], 'side_effect': False},
        dependencies=[model['id']], data_policy={'cloud_allowed': True},
        budget={'max_attempts': 1, 'max_concurrency': 1}, specification={'schema': reasoning.CONTRACT,
            'instruction': '区分候选是否适用与事实是否冲突；缺失观测只可提出申请，不宣称已有数值。'}).model_dump(mode='json'))
    resense = listed[original['tool_bindings']['resense']['capability_id']]
    target_pin = {key: resense[key] for key in ('release_version',)}
    target_pin.update(capability_id=resense['id'], capability_hash=resense['content_hash'])
    plan = publish(s.client, Manifest(kind='tool', namespace='decision_pg_v278',
        name='local_decision_plan_' + uuid4().hex[:12], version='1.0.0', owner='独立工程核验账号',
        description='本地计划仅选择补采或继续；不发HTTP、不批准、不生成数据收件', allowed_agent_roles=['business'],
        input_schema=planner.input_schema(), output_schema=planner.output_schema(),
        execution={'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': planner.OP, 'side_effect': True}, dependencies=[resense['id']],
        data_policy={'cloud_allowed': False}, budget={'max_attempts': 1, 'max_concurrency': 1},
        specification={'schema': planner.CONTRACT, 'resense_tool_pin': target_pin}).model_dump(mode='json'))
    pins = {'data_packet': data_packet, 'packet': listed[original['tool_bindings']['packet']['capability_id']],
        'reasoning': reason, 'planner': plan, 'resense': resense}
    current = good(s.client.get('/api/setup/draft'))
    body = command_shape()
    body.update(configuration_revision=current['revision'], configuration_hash=digest(current['configuration']),
        business_name=original['business_name'], tool_bindings={key: {
            'capability_id': row['id'], 'capability_hash': row['content_hash'], 'actor_id': s.actor_id}
            for key, row in pins.items()}, role_bindings=copy.deepcopy(original['role_bindings']),
        observation_extractors=copy.deepcopy(original['observation_extractors']),
        node_actions=copy.deepcopy(commands.NODE_ACTIONS), external_node_ids=list(commands.EXTERNAL_NODE_IDS),
        prior_origin=None, simulation_only=True)
    # The managed device remains an explicit unselected proposal with no numeric
    # port claim. It must still enter dependency closure, never a direct step.
    managed_cap = listed[s.capability_id]
    body['candidate_specs'] = [{'candidate_ref': 'candidate_status',
        'proposal': '仅核对已登记的模拟设备收据合同；不声称已有温湿度数值。', 'claims': [], 'capability_pins': [{
            'capability_ref': 'device_receipt', 'capability_id': managed_cap['id'],
            'capability_hash': managed_cap['content_hash'], 'release_version': managed_cap['release_version'],
            'delivery': 'output', 'ports': []}]}, {'candidate_ref': 'candidate_acquire',
        'proposal': '原数据不足时按原context申请观测，再核独立接收；已有数值时可继续人工核查。',
        'claims': [], 'capability_pins': [{'capability_ref': 'resense', **target_pin,
            'delivery': 'requested_observation', 'ports': [
                {'field': 'temperature_c', 'pointer': '/requested_observation/temperature_c'},
                {'field': 'relative_humidity_pct', 'pointer': '/requested_observation/relative_humidity_pct'}]}]}]
    return body, pins, model, managed_cap, extractor, material


def adopt_after_discarding_response(s, body):
    preview = good(s.client.post(BASE + '/preview', json=body))
    request = {**body, 'schema': 'authored-decision-workflow-adopt-command-v278',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True}
    raw = (' \t' + json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8')
    response = s.client.post(BASE + '/adopt', content=raw)
    assert response.status_code == 201, response.text[:500]
    del response  # The caller retains only its exact sent body/request ID.
    recovered = good(s.client.get(BASE + '/requests/' + request['request_id'],
        params={'expected_hash': hashlib.sha256(raw).hexdigest()}))
    assert recovered['found'] and recovered['body'] == request
    assert recovered['schema'] == 'authored-decision-workflow-recovery-v278'
    return preview, request, raw, recovered['result']


def test_normal_decision_adoption_cold_recovers_exact_common_command_and_publishes(managed):
    s = managed
    body, pins, model, candidate, extractor, material = prepared_decision(s)
    before = counts(s.workspace_id); prior = ledger_snapshot(s.workspace_id)
    preview, request, raw, adopted = adopt_after_discarding_response(s, body)
    assert preview['binding']['schema'] == commands.BINDING
    assert preview['binding']['simulation_only'] is True
    assert preview['binding']['source_materials'][0]['id'] == material['id']
    assert counts(s.workspace_id) == before
    same = good(s.client.post(BASE + '/adopt', content=raw), 201)
    assert same == adopted and counts(s.workspace_id) == before
    wrong_hash = s.client.get(BASE + '/requests/' + request['request_id'], params={'expected_hash': '0' * 64})
    assert wrong_hash.status_code == 409
    changed_raw = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    assert changed_raw != raw
    assert s.client.post(BASE + '/adopt', content=changed_raw).status_code == 409
    view = good(s.client.get(BASE + '/adoptions/' + adopted['id']))
    assert view['binding_hash'] == preview['binding']['binding_hash']
    assert view['graph'] == preview['binding']['graph'] and view['origin'] == adopted['origin']
    assert view['can_execute'] is False and view['cloud_authorized'] is False
    after = ledger_snapshot(s.workspace_id)
    for kind, rows in prior.items():
        assert all(after[kind][identity] == value for identity, value in rows.items())
    with Session(engine()) as db:
        original = db.scalar(select(Command).where(Command.workspace_id == s.workspace_id,
            Command.request_id == request['request_id']))
        adoption = db.get(Adoption, adopted['id'])
        assert original.path == commands.PATH and original.adoption_id == adoption.id
        assert original.exact_body.encode('utf-8') == raw
        assert original.body_sha256 == hashlib.sha256(raw).hexdigest()
        assert original.data['schema'] == commands.COMMAND and original.data['result'] == adopted
        assert adoption.data['binding'] == preview['binding'] and adoption.binding_hash == preview['binding']['binding_hash']
        assert adoption.data['command_id'] == original.id and adoption.version == original.version == 1
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action.in_([common.COMMAND_AUDIT, common.ADOPTED_AUDIT]),
            m.Audit.object_id.in_([original.id, adoption.id]))))
        assert len(events) == 2 and {row.action for row in events} == {common.COMMAND_AUDIT, common.ADOPTED_AUDIT}
        assert all(row.actor_id == original.actor_id == adoption.actor_id for row in events)
    current = good(s.client.get('/api/setup/draft'))
    publication = {'revision': current['revision'], 'objects': OBJECTS,
        'role_bindings': {key: actor['user_id'] for key, actor in preview['binding']['role_bindings'].items()}}
    checked = good(s.client.post('/api/instances/check', json=publication))
    assert not checked['general_issues'] and all(row['available'] for row in checked['businesses']), [
        (row['name'], row['issues']) for row in checked['businesses']]
    release = good(s.client.post('/api/instances', json={**publication,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    original_binding = release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['binding']
    assert original_binding == preview['binding']
    graph = original_binding['graph']; nodes = {row['id']: row for row in graph['nodes']}
    assert nodes['data_packet']['execution']['capability_id'] == pins['data_packet']['id']
    assert nodes['reasoning']['execution']['adapter'] == reasoning.ADAPTER
    assert nodes['planner']['execution']['capability_id'] == pins['planner']['id']
    assert 'resense' not in graph['goals'][0]['required_nodes']
    assert {edge['on']: edge['to'] for edge in graph['edges'] if edge['from'] == 'route_acquisition'}['true'] == 'resense'
    declarations = [row for business in release['snapshot']['data_requirements']['businesses']
        for row in business['outputs'] if row.get('kind') == 'asynchronous_perception']
    assert len(declarations) == 1 and declarations[0]['capability_id'] == extractor['id']
    assert declarations[0]['node_id'] is None and declarations[0]['source_binding_hash'] == original_binding['binding_hash']
    assert declarations[0]['status'] == 'candidate'
    with Session(engine()) as db:
        stored_release = db.get(m.InstanceRelease, release['id'])
        package = db.get(m.Capability, stored_release.package_id)
        dependencies = set(package.data['dependencies'])
        assert {row['id'] for row in pins.values()} | {candidate['id'], extractor['id']} <= dependencies
        assert pins['reasoning']['data']['dependencies'] == [model['id']]
        ability_caps = {node['execution']['capability_id'] for node in graph['nodes'] if node['kind'] == 'ability'}
        assert candidate['id'] not in ability_caps and extractor['id'] not in ability_caps
        assert package.content_hash == package.validated_hash == digest(package.data)
    assert counts(s.workspace_id) == before
    # A second ordinary adoption revision makes common-ledger preservation
    # non-vacuous. It changes no existing command, adoption or published release.
    first_ledger = ledger_snapshot(s.workspace_id)
    next_draft = good(s.client.get('/api/setup/draft')); revised = copy.deepcopy(body)
    revised.update(configuration_revision=next_draft['revision'],
        configuration_hash=digest(next_draft['configuration']), prior_origin=adopted['origin'])
    revised['candidate_specs'][1]['proposal'] += ' 采用新修订仍须另核原数据与独立收件。'
    _next_preview, _next_request, _next_raw, next_adoption = adopt_after_discarding_response(s, revised)
    assert next_adoption['id'] != adopted['id']
    retained = ledger_snapshot(s.workspace_id)
    for kind, rows in first_ledger.items():
        assert all(retained[kind][identity] == value for identity, value in rows.items())
    assert len(retained['adoptions']) == len(first_ledger['adoptions']) + 1
    assert len(retained['commands']) == len(first_ledger['commands']) + 1
    assert good(s.client.get(BASE + '/adoptions/' + adopted['id'])) == view
    old_recovery = good(s.client.get(BASE + '/requests/' + request['request_id'],
        params={'expected_hash': hashlib.sha256(raw).hexdigest()}))
    assert old_recovery['result'] == adopted and old_recovery['body'] == request
    with Session(engine()) as db:
        assert db.get(m.InstanceRelease, release['id']).data['snapshot'] == release['snapshot']
    assert counts(s.workspace_id) == before


def test_changed_original_material_blocks_new_adoption_without_runtime_side_effects(managed):
    s = managed
    body, _pins, _model, _candidate, _extractor, material = prepared_decision(s)
    preview = good(s.client.post(BASE + '/preview', json=body)); before = counts(s.workspace_id)
    ledger_before = ledger_snapshot(s.workspace_id)
    # Adversarial durable source mutation; parsed materials have no ordinary
    # edit endpoint. No authority, answer, Goal, Gap or result row is forged.
    with Session(engine()) as db:
        source = db.get(m.SetupMaterial, material['id']); source.archived = True; source.version += 1
        db.commit()
    response = s.client.post(BASE + '/adopt', json={**body,
        'schema': 'authored-decision-workflow-adopt-command-v278', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'consent': True})
    assert response.status_code == 409
    assert counts(s.workspace_id) == before and ledger_snapshot(s.workspace_id) == ledger_before
