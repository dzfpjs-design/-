"""Root-run owned PG/API journey, using only inherited public synthetic IO.

The original V170 source, human confirmation, full position proof and schema
adoption remain real stores. This test does not rewrite their values, origins,
validators or model answers. Its explicit graph decisions do not certify source
meaning, real-report quality, browser behavior or a physical service restart.
All successful writes below use ordinary authenticated product APIs.
"""
import copy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, DomainInstance, Entity, Event, InstanceRelease, Invocation, Job, Memory, SetupDraft, SetupRevision, Task
from generic_agents.report_full_semantic_graph_models import FullSemanticGraphAdoption
from generic_agents.report_full_semantic_graph_revision_models import FullSemanticGraphRevision
from generic_agents.report_object_graph_binding_v171 import required_owner_fields
from generic_agents.graph_wait_sources_v118 import POLICY
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline_v7_postgres import v7_scene
from tests.test_report_full_semantic_region_source_postgres import full_source_scene
from tests.test_report_object_adoption_postgres_v169 import confirmed_scene, assert_paid_unchanged
from tests.test_report_object_source_postgres_v170 import object_source_scene, assert_no_public_private_values
from tests.test_report_object_schema_postgres_v170 import object_schema_scene, adopted_schema
from tests.test_instance_workspace import save
from tests.test_report_evidence_pipeline import reply


ROOT = '/api/setup/full-semantic-object-graphs-v171/'


def guards(**values):
    return {**dict.fromkeys(('start', 'precondition', 'stop', 'exception')), **values}


def ref(kind, identity):
    return {'kind': kind, 'id': identity}


def edge(identity, start, end, *, branch=None):
    return {'id': identity, 'from': start, 'to': end, 'on': 'branch' if branch else 'success',
        'branch_id': branch, 'loop_id': None}


def schema(properties=None):
    return {'type': 'object', 'properties': properties or {}, 'required': list(properties or {}),
        'additionalProperties': False}


def prepare(seed):
    client = seed['client']; schema_command, schema_preview, schema_saved = adopted_schema(seed)
    saved = reply(client.get('/api/setup/draft')); config = copy.deepcopy(saved['configuration'])
    config['instanceProfile'].update(name='新对象完整图合成验收', domain='资料核查',
        goal='保留原完整设计与身份说明，登记独立技术记录并保存人工结果')
    config['automation'] = {'version': 1, 'policies': {name: {
        'triggers': ['人工发起'], 'rules': {'write': {'mode': '仅给出建议'}}}
        for name in config['businesses']}}
    save(client, config, review_automation=True)
    current = reply(client.get('/api/setup/draft'))
    me = reply(client.get('/api/me'))
    member = next(item for item in reply(client.get('/api/instances/members')) if item['id'] == me['user_id'])
    return current, member, schema_saved


def command(seed, current, member):
    design = seed['record']['design']; result = design['result']; business = result['businesses'][0]
    assert len(result['businesses']) == 1, 'This public fixture must keep its complete original business inventory'
    assert len(business['steps']) == 2 and all(step['agent'] == '人工确认' for step in business['steps'])
    assert [item['kind'] for item in business['conditions']] == ['precondition']
    assert business['conditions'][0]['step_indices'] == [0]
    assert len(business['coordination']) == 1 and business['coordination'][0]['kind'] == 'parallel'
    assert set(business['coordination'][0]['step_indices']) == {0, 1}
    assert all(step['next_index'] is None and step['else_index'] is None
        and not step['branch_condition'] for step in business['steps'])
    roles = list(dict.fromkeys([step['role'] for step in business['steps']]
        + [person['agent'] for person in business['participants']]))
    assert len(roles) == 4, 'Keep both original human and participant role names; one real member may hold all four'
    step_ids = design['business_bindings'][0]['step_ids']
    inputs = {'precondition_confirmed': {'type': 'boolean'}, 'parallel_start_confirmed': {'type': 'boolean'}}
    graph = {'schema': 'full-semantic-graph-v1', 'id': 'object_graph_public_v171', 'entry': 'f',
        'input_schema': schema(inputs),
        'references': {key: {'kind': 'boolean', 'nullable': False, 'source': 'input', 'field': key, 'node_id': None}
            for key in inputs},
        'predicates': {'p_pre': {'op': 'ref', 'key': 'precondition_confirmed'},
            'p_start': {'op': 'ref', 'key': 'parallel_start_confirmed'}}, 'guards': guards(),
        'nodes': [
            {'id': 'f', 'kind': 'fork', 'source_step_id': None, 'guards': guards(start='p_start'),
                'join_id': 'j', 'branch_ids': ['A', 'B']},
            {'id': 'a', 'kind': 'human', 'source_step_id': step_ids[0], 'guards': guards(precondition='p_pre'),
                'actor_id': member['id'], 'role': business['steps'][0]['role'], 'output_schema': schema({'ok': {'type': 'boolean'}})},
            {'id': 'b', 'kind': 'human', 'source_step_id': step_ids[1], 'guards': guards(),
                'actor_id': member['id'], 'role': business['steps'][1]['role'], 'output_schema': schema({'ok': {'type': 'boolean'}})},
            {'id': 'j', 'kind': 'join', 'source_step_id': None, 'guards': guards(), 'fork_id': 'f', 'policy': 'all'},
            {'id': 'e', 'kind': 'end', 'source_step_id': None, 'guards': guards()}],
        'edges': [edge('f_a', 'f', 'a', branch='A'), edge('f_b', 'f', 'b', branch='B'),
            edge('a_j', 'a', 'j'), edge('b_j', 'b', 'j'), edge('j_e', 'j', 'e')],
        'loops': [], 'subscriptions': [], 'goals': [{'id': 'done', 'required_nodes': ['a', 'b']}]}
    manifest = seed['proof_result']['manifest']; configured = current['configuration']

    def origin(name, field=None, *, relationship=False):
        key = 'instanceRelationOrigins' if relationship else 'instanceObjectOrigins'
        actual = copy.deepcopy(configured[key][name])
        assert actual['schema'] == 'report-object-schema-origin-v170'
        original = next(item for item in result['relationships' if relationship else 'objects'] if item['name'] == name)
        assert actual['owner_value_hash'] == digest(original)
        return {'kind': 'schema_origin', 'id': name, 'field': field,
            'field_type': None if field is None else next(item['kind'] for item in original['fields'] if item['label'] == field),
            'origin': actual}

    specs = required_owner_fields(design); owners = []
    assert len(specs) == len(manifest['owners'])
    for spec, evidence in zip(specs, manifest['owners']):
        path, kind = spec['owner_path'], spec['kind']; assert path == evidence['path']
        targets = {field: [] for field in spec['source_fields']}; resolution = 'typed'
        reason = '明确的工作区图设计选择，不把原声明、位置或身份说明认定为原文事实或业务唯一性。'
        if kind in {'domain', 'question'}:
            resolution = 'informational'; reason = '原上下文或待核问题完整保留，不冒充已解决执行约束。'
        elif kind == 'global_claim':
            targets = {field: [ref('goal', 'done'), *[ref('role_binding', role) for role in roles]] for field in targets}
            # The two explicit human input values are part of this workspace
            # interpretation. Referencing their predicates alone does not
            # declare their meaning in the complete owner coverage contract.
            targets['statement'].extend(ref('value_ref', key) for key in inputs)
        elif kind in {'object', 'field', 'business_identity'}:
            obj = result['objects'][int(path.split('/')[2])]
            field = obj['fields'][int(path.split('/')[4])]['label'] if kind == 'field' else None
            targets = {name: [origin(obj['name'], field)] for name in targets}
        elif kind == 'platform_identity_confirmation':
            name = design['object_identity_confirmations'][int(path.split('/')[2])]['object_name']
            targets = {field: [origin(name)] for field in targets}
        elif kind == 'relationship':
            relation = result['relationships'][int(path.split('/')[2])]
            targets = {field: [origin(relation['name'], relationship=True)] for field in targets}
            targets.update(source_type=[origin(relation['source_type'])], target_type=[origin(relation['target_type'])])
        elif kind == 'business': targets.update(trigger=[ref('node', 'f')], output=[ref('goal', 'done')])
        elif kind == 'step':
            node = ('a', 'b')[int(path.split('/')[-1])]
            for field in ('name', 'agent', 'role', 'purpose', 'output'): targets[field] = [ref('node', node)]
            targets['next_index'] = [ref('edge', node + '_j')]
        elif kind == 'participant':
            index = int(path.split('/')[-1]); person = business['participants'][index]
            targets.update(agent=[ref('role_binding', person['agent'])], responsibility=[ref('node', ('a', 'b')[index])])
        elif kind == 'interaction':
            item = business['interactions'][int(path.split('/')[-1])]
            targets.update(from_participant_index=[ref('role_binding', business['participants'][item['from_participant_index']]['agent'])],
                to_participant_index=[ref('role_binding', business['participants'][item['to_participant_index']]['agent'])],
                purpose=[ref('edge', 'a_j')], condition=[ref('predicate', 'p_pre')] if item['condition'].strip() else [],
                step_indices=[ref('node', ('a', 'b')[index]) for index in item['step_indices']])
        elif kind == 'condition':
            targets.update(kind=[ref('predicate', 'p_pre')], statement=[ref('predicate', 'p_pre')], step_indices=[ref('node', 'a')])
        elif kind == 'coordination':
            item = business['coordination'][int(path.split('/')[-1])]
            targets.update(kind=[ref('fork', 'f')], start_rule=[ref('predicate', 'p_start')],
                step_indices=[ref('node', ('a', 'b')[index]) for index in item['step_indices']],
                wait_rule=[ref('join', 'j')], join_rule=[ref('join', 'j')])
            reason += ' 明确选择等待两项完成后all汇合；不认定原文已规定此汇合政策。'
        else: raise AssertionError('New source owner has no explicit test mapping: ' + kind)
        owners.append({'owner_path': path, 'value_hash': evidence['value_hash'], 'ref_hashes': copy.deepcopy(evidence['ref_hashes']),
            'resolution': resolution, 'reason': reason, 'scope_businesses': [business['name']],
            'clauses': [{'source_field': field, 'targets': targets[field]} for field in spec['source_fields']]})
    return {'schema': 'report-object-graph-preview-command-v171', 'configuration_revision': current['revision'],
        'configuration_hash': digest(configured), 'proof_id': seed['proof_id'], 'business_name': business['name'],
        'graph': graph, 'owner_bindings': owners, 'role_bindings': {role: member['id'] for role in roles},
        'external_node_ids': [], 'node_actions': {}, 'event_source_bindings': {}, 'event_admission_policy': copy.deepcopy(POLICY)}


def store_inventory():
    """Read-only relevant write inventory; no SQL fault injection or successes."""
    models = (Audit, CloudCall, DomainInstance, Entity, Event, InstanceRelease, Invocation, Job,
        Memory, SetupDraft, SetupRevision, Task, FullSemanticGraphAdoption, FullSemanticGraphRevision)
    with Session(engine()) as db:
        return {model.__tablename__: {row.id: digest({'version': getattr(row, 'version', None), 'data': row.data,
            'status': getattr(row, 'status', None), 'actor_id': getattr(row, 'actor_id', None),
            'object_id': getattr(row, 'object_id', None), 'request_id': getattr(row, 'request_id', None)})
            for row in db.scalars(select(model))} for model in models}


def human(client, task, node_id, *, draft=False):
    pending = next(item for item in task['graph_pending_humans'] if item['node_id'] == node_id)
    body = {'request_id': str(uuid4()), 'activation_id': pending['activation_id'], 'version': pending['version'],
        'cancel_epoch': task['graph_runtime']['cancel_epoch'],
        **({'draft': {'ok': True}} if draft else {'output': {'ok': True}})}
    path = '/api/workspace/tasks/' + task['id'] + '/graph/' + ('draft' if draft else 'complete')
    response = client.put(path, json=body) if draft else client.post(path, json=body)
    return reply(response), body


def test_new_object_complete_graph_real_generation_empty_records_human_work_and_cold_read(object_schema_scene, account):
    fence(); seed = object_schema_scene; client = seed['client']; original = copy.deepcopy(seed['record']['design'])
    current, member, schema_saved = prepare(seed); body = command(seed, current, member)
    path = ROOT + seed['record']['id']
    editor = reply(client.get(path + '/editor-context'))
    assert editor['schema'] == 'report-object-graph-editor-context-v171' and editor['design'] == original
    preview = reply(client.post(path + '/preview', json=body))
    assert preview['binding']['schema'] == 'report-object-graph-binding-v171'
    assert preview['binding']['source_design'] == original and preview['binding']['owner_bindings'] == body['owner_bindings']
    assert preview['source_semantics_status'] == 'not_certified'
    accepted_body = {**body, 'schema': 'report-object-graph-command-v171', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(path + '/adopt', json=accepted_body)); receipt = accepted['receipt']
    assert accepted['origin']['schema'] == 'report-object-graph-origin-v171'
    assert reply(client.post(path + '/adopt', json=accepted_body))['receipt'] == receipt
    assert reply(client.get(path + '/requests/' + accepted_body['request_id']))['receipt'] == receipt
    configured = reply(client.get('/api/setup/draft'))
    assert configured['configuration']['fullSemanticDesigns'] == current['configuration']['fullSemanticDesigns']
    compilation = {'revision': configured['revision'], 'objects': configured['configuration']['instanceObjects'],
        'role_bindings': body['role_bindings']}
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert checked['semantic_runtime_plan']['schema'] == 'instance-object-graph-runtime-plan-v171'
    business = next(item for item in checked['businesses'] if item['name'] == body['business_name'])
    assert business['available'] is True and business['execution_mode'] == 'confirmed_graph_workflow'
    assert checked['general_issues'] == []
    release = reply(client.post('/api/instances', json={**compilation,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': str(uuid4())}), 201)
    instance = next(item for item in reply(client.get('/api/instances')) if item['id'] == release['instance_id'])
    reply(client.post('/api/instances/' + instance['id'] + '/activate', json={'version': instance['version'],
        'release_id': release['id'], 'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    objects = []; by_type = {}
    for kind in release['snapshot']['object_types']:
        assert kind['contract_schema'] == 'report-object-type-v170' and all(not field['required'] for field in kind['fields'])
        original_type = next(item for item in schema_saved['plan']['objects'] if item['name'] == kind['name'])
        assert [{key: field[key] for key in ('label', 'kind', 'required', 'unit')}
            for field in kind['fields']] == original_type['fields']
        assert kind['business_identity'] == original_type['business_identity']
        assert kind['platform_identity_confirmation'] == original_type['platform_identity_confirmation']
        created = reply(client.post('/api/instances/' + instance['id'] + '/objects', json={
            'release_id': release['id'], 'object_type': kind['key'], 'attributes': {}}), 201)
        objects.append(created)
        by_type[kind['key']] = created
    assert len(objects) == 2
    numeric = next(kind for kind in release['snapshot']['object_types'] if all(field['kind'] in {'整数', '数值'} for field in kind['fields']))
    second = by_type[numeric['key']]
    field = numeric['fields'][0]
    assert client.post('/api/instances/' + instance['id'] + '/objects', json={'release_id': release['id'],
        'object_type': numeric['key'], 'attributes': {field['key']: None}}).status_code == 422
    with Session(engine()) as db:
        for obj in objects:
            stored = db.get(Entity, obj['id'])
            assert stored.data == {} and stored.external_id.startswith('auto_')
    patched = reply(client.patch('/api/entities/' + second['id'], json={'expected_version': second['version'],
        'reason': '人工明确补录公开合成数量，原空记录和版本保留', 'attributes': {field['key']: 7}}))
    assert patched['version'] == second['version'] + 1
    base = '/api/workspace/instances/' + instance['id']
    task_body = {'release_id': release['id'], 'business_id': business['id'], 'entity_ids': [obj['id'] for obj in objects],
        'title': '新对象全可选字段与完整并行人工核查', 'request_id': str(uuid4()),
        'graph_inputs': {'precondition_confirmed': True, 'parallel_start_confirmed': True}}
    task = reply(client.post(base + '/tasks', json=task_body), 201)
    # Creation returns the durable task row; the workspace read enriches it
    # with current per-node work and permissions, as in the existing UI path.
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert {item['node_id'] for item in task['graph_pending_humans']} == {'a', 'b'}
    assert reply(client.post(base + '/tasks', json=task_body), 201)['id'] == task['id']
    task, draft = human(client, task, 'a', draft=True)
    assert next(item for item in reply(client.get('/api/workspace/tasks/' + task['id']))['graph_pending_humans']
        if item['node_id'] == 'a')['draft'] == {'ok': True}
    task, first = human(client, task, 'a'); assert task['completed'] is False
    assert [item['node_id'] for item in task['graph_pending_humans']] == ['b']
    task, final = human(client, task, 'b')
    assert task['status'] == 'workflow_completed' and task['completed'] is True
    assert len(task['workflow_runtime']['receipts']) == 2
    assert reply(client.post('/api/workspace/tasks/' + task['id'] + '/graph/complete', json=final))['graph_runtime'] == task['graph_runtime']
    blocked = reply(client.post(base + '/tasks', json={**task_body, 'request_id': str(uuid4()),
        'title': '前置条件明确不成立，不得完成原完整图',
        'graph_inputs': {'precondition_confirmed': False, 'parallel_start_confirmed': True}}), 201)
    blocked = reply(client.get('/api/workspace/tasks/' + blocked['id']))
    assert not blocked['completed'] and all(item['node_id'] != 'a' for item in blocked['graph_pending_humans'])
    if any(item['node_id'] == 'b' for item in blocked['graph_pending_humans']):
        blocked, _ = human(client, blocked, 'b')
    assert not blocked['completed'] and blocked['status'] != 'workflow_completed'
    with Session(engine()) as db:
        frozen_task = copy.deepcopy(db.get(Task, task['id']).data)
        frozen_entities = {obj['id']: copy.deepcopy(db.get(Entity, obj['id']).data) for obj in objects}
        assert frozen_entities[second['id']] == {field['key']: 7}
        definitions = frozen_task['object_definition_bindings']['objects']
        assert len(definitions) == 2
        assert all(item['definition']['schema'] == 'instance-entity-definition-v170' for item in definitions)
        for item in definitions:
            metadata = item['definition']['object_type_contract']
            stored_entity = db.get(Entity, item['entity_id'])
            catalog = next(kind for kind in release['snapshot']['object_types'] if kind['key'] == stored_entity.entity_type)
            assert metadata == {key: catalog[key] for key in
                ('contract_schema', 'business_identity', 'platform_identity_confirmation')}
        assert db.get(Memory, task['workflow_runtime']['data_receipt_id']).status == 'candidate'
    cold_objects = reply(client.get('/api/instances/' + instance['id'] + '/objects'))
    with TestClient(app) as cold:
        auth = reply(cold.post('/api/login', json={'username': account[0], 'password': account[1]}))
        cold.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        assert reply(cold.get('/api/workspace/tasks/' + task['id']))['graph_runtime'] == task['graph_runtime']
        assert reply(cold.get('/api/instances/' + instance['id'] + '/objects')) == cold_objects
        assert reply(cold.get(path + '/requests/' + accepted_body['request_id']))['receipt'] == receipt
        assert reply(cold.get(seed['schema_path'] + '/requests/' + schema_saved['receipt']['request_id']))['receipt'] == schema_saved['receipt']
    with Session(engine()) as db:
        assert db.get(Task, task['id']).data == frozen_task
        assert {obj['id']: db.get(Entity, obj['id']).data for obj in objects} == frozen_entities
    assert seed['record']['design'] == original
    assert_no_public_private_values(preview, accepted, checked, release, task)
    assert_paid_unchanged(seed['scene'])


def test_old_graph_routes_and_missing_new_identity_owner_refuse_without_writes(object_schema_scene):
    fence(); seed = object_schema_scene; client = seed['client']
    current, member, _ = prepare(seed); body = command(seed, current, member)
    before = store_inventory(); original = copy.deepcopy(body); path = ROOT + seed['record']['id']
    for old_path in ('/api/setup/full-semantic-graphs/', '/api/setup/full-semantic-graphs-v4/'):
        response = client.post(old_path + seed['record']['id'] + '/preview', json=body)
        assert response.status_code == 422 and store_inventory() == before
    for fault in ('missing-identity', 'missing-confirmation', 'old-origin'):
        bad = copy.deepcopy(body)
        if fault == 'missing-identity':
            bad['owner_bindings'] = [row for row in bad['owner_bindings'] if row['owner_path'] != '/objects/0/business_identity']
        elif fault == 'missing-confirmation':
            bad['owner_bindings'] = [row for row in bad['owner_bindings'] if row['owner_path'] != '/object_identity_confirmations/0']
        else:
            owner = next(row for row in bad['owner_bindings'] if row['owner_path'] == '/objects/0/business_identity')
            owner['clauses'][0]['targets'][0]['origin']['schema'] = 'report-full-semantic-schema-origin-v1'
        response = client.post(path + '/preview', json=bad)
        assert response.status_code in (409, 422), (fault, response.text)
        assert store_inventory() == before
    valid = reply(client.post(path + '/preview', json=body))
    assert valid['binding']['schema'] == 'report-object-graph-binding-v171'
    assert body == original and store_inventory() == before
    assert_paid_unchanged(seed['scene'])
