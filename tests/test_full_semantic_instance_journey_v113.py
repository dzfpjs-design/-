"""Root-run real API/PG vertical slice; only existing synthetic IO/provider seams.

The human draft is constructed before its first persisted manual-review receipt.
No saved design, authority, package, runtime or execution gate is patched. This
proves the supported single-person workflow, not general semantic execution or
real-document/model quality. Unsupported full designs keep their old boundary.
"""
import copy
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Capability, Entity, InstanceRelease, Memory, Relation, Task, now
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_source_review_postgres import v7_scene, reading_scene, local_runtime
from tests.test_report_full_semantic_region_source_postgres import full_source_scene
from tests.test_report_full_semantic_schema_adoption import schema_seed, adopted
from tests.test_instance_workspace import save, enable, task_row, complete


SINGLE = '单步资料核对'
COMPLEX = '多步资料核对'
ROLE = '资料核对人'


@pytest.fixture
def journey_seed(v7_scene, monkeypatch):
    from tests import test_report_full_semantic_source_review_postgres as source_review
    original = source_review.human_value

    def human_design(model_result):
        value = original(model_result)

        def step(name, target):
            return {'name': name, 'purpose': '对照正式资料核对登记内容', 'agent': '人工确认',
                'role': ROLE, 'output': '人工核对结论', 'next_index': target, 'else_index': None,
                'branch_condition': '', 'citations': [], 'inference_reason': '合成人工设计，执行采用另行确认'}

        def business(name, steps):
            return {'operation': 'upsert', 'original_name': '', 'name': name, 'trigger': '人工发起资料核对',
                'output': '保存人工核对意见', 'steps': steps, 'citations': [],
                'inference_reason': '合成人工设计，不把工作区采用当作原文事实',
                'participants': [], 'interactions': [], 'conditions': [], 'coordination': []}

        # Keep the real source/catalog hashes, domain and complete object fields.
        # The existing helper then attaches its explicit synthetic reference to
        # business index 1 and adds the two document-grounded object definitions.
        value['result']['businesses'] = [business(COMPLEX, [step('先核对', 1), step('再登记', None)]),
            business(SINGLE, [step(SINGLE, None)])]
        value['result']['global_claims'] = []
        value['result']['questions'] = []
        return value

    monkeypatch.setattr(source_review, 'human_value', human_design)
    saved_seed = source_review.complete_source_review_seed(v7_scene, monkeypatch)
    seed = schema_seed.__wrapped__(saved_seed)
    command, preview, acceptance = adopted(seed)
    assert preview['plan']['semantic_mode'] == 'workspace_schema_design'
    return {**seed, 'schema_command': command, 'schema_adoption': acceptance}


def configured(seed):
    client = seed['client']; current = reply(client.get('/api/setup/draft'))
    config = copy.deepcopy(current['configuration'])
    config['instanceProfile'].update(name='完整设计到真实登记的合成实例', domain='资料核对', goal='登记对象与关系并保存人工核对结论')
    config['automation'] = {'version': 1, 'policies': {name: {
        'triggers': ['人工发起'], 'rules': {'write': {'mode': '仅给出建议'}}}
        for name in config['businesses']}}
    for flow in config['flows'].values():
        assert flow['confirmedAt'] is None and flow['confirmedSignature'] is None
    save(client, config, review_automation=True)
    view = reply(client.get('/api/setup/draft'))
    for flow in view['configuration']['flows'].values():
        assert flow['confirmedAt'] is None and flow['confirmedSignature'] is None
    member = next(item for item in reply(client.get('/api/instances/members')) if item['id'] == seed['scene']['p'].user_id)
    body = {'revision': view['revision'], 'objects': copy.deepcopy(view['configuration']['instanceObjects']),
        'role_bindings': {ROLE: member['id']}}
    return view, member, body


def runtime_command(seed, member, business=SINGLE):
    view = reply(seed['client'].get('/api/setup/draft'))
    return {'schema': 'report-full-semantic-runtime-preview-command-v2',
        'configuration_revision': view['revision'], 'configuration_hash': digest(view['configuration']),
        'proof_id': seed['proof_id'], 'business_name': business, 'actor_id': member['id']}


def generate(client, body, checked, *, instance_id=None):
    response = client.post('/api/instances', json={**body,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        **({'instance_id': instance_id} if instance_id else {})})
    assert response.status_code == 201, response.text
    return response.json()


def create_object(client, release, kind):
    values = {}
    for field in kind['fields']:
        if not field['required']: continue
        values[field['key']] = {'文本': kind['name'], '整数': 1, '数值': 1.25,
            '是否': True, '日期': '2026-01-01'}[field['kind']]
    command = {'release_id': release['id'], 'object_type': kind['key'], 'reference': kind['name'],
        'attributes': values, 'request_id': 'v113_' + uuid4().hex}
    response = client.post('/api/instances/' + release['instance_id'] + '/objects', json=command)
    assert response.status_code == 201, response.text
    result = response.json()
    assert reply(client.post('/api/instances/' + release['instance_id'] + '/objects', json=command), status=201)['id'] == result['id']
    return result, values


def relation_query(client, release, relation_type, source, target, field):
    response = client.post('/api/data/queries', json={'question': '核对已确认资料关联的任务登记名称',
        'entity_ids': [source['id'], target['id']], 'fields': [field], 'mode': 'structured',
        'relation_query': {'package_id': release['package_id'], 'relation_types': [relation_type],
            'root_ids': [source['id']], 'direction': 'outgoing', 'max_hops': 1}})
    assert response.status_code == 200, response.text
    return response.json()


def test_full_design_schema_runtime_package_objects_relations_query_and_manual_task_persist(journey_seed, account):
    seed = journey_seed; client = seed['client']; view, member, compilation = configured(seed)
    frozen_flows = copy.deepcopy(view['configuration']['flows'])
    runtime_path = '/api/setup/full-semantic-runtimes/' + seed['record']['id']

    # A real v1 release with intact provenance remains a blocked draft. The v2
    # adapter is adopted separately; no old source-flow signature is fabricated.
    legacy_check = reply(client.post('/api/instances/check', json=compilation))
    assert legacy_check['semantic_plan']['execution_policy'] == 'blocked_pending_full_semantic_runtime_v1'
    assert all(not item['available'] for item in legacy_check['businesses'])
    legacy = generate(client, compilation, legacy_check)
    instance = next(item for item in reply(client.get('/api/instances')) if item['id'] == legacy['instance_id'])
    assert client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': legacy['id'],
        'business_ids': [legacy_check['businesses'][1]['id']], 'confirm_human_workflow_scope': True}).status_code == 409

    rejected = client.post(runtime_path + '/preview', json=runtime_command(seed, member, COMPLEX))
    assert rejected.status_code == 409 and rejected.json()['detail']['code'] == 'full_runtime_unsupported'
    command = runtime_command(seed, member)
    preview = reply(client.post(runtime_path + '/preview', json=command))
    adoption = {**command, 'schema': 'report-full-semantic-runtime-command-v2', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(runtime_path + '/adopt', json=adoption))
    assert reply(client.post(runtime_path + '/adopt', json=adoption))['receipt'] == accepted['receipt']
    assert reply(client.get(runtime_path + '/requests/' + adoption['request_id']))['receipt'] == accepted['receipt']
    current = reply(client.get('/api/setup/draft'))
    assert current['configuration']['flows'] == frozen_flows
    for flow in current['configuration']['flows'].values():
        assert flow['confirmedAt'] is None and flow['confirmedSignature'] is None
    compilation['revision'] = current['revision']

    # Separate caller-provided schemas cannot erase adopted objects or weaken a
    # required field. Both are rejected by the actual compilation endpoint.
    missing = {**compilation, 'objects': compilation['objects'][:-1]}
    assert client.post('/api/instances/check', json=missing).status_code in (409, 422)
    weakened = copy.deepcopy(compilation); weakened['objects'][0]['fields'][0]['required'] = False
    assert client.post('/api/instances/check', json=weakened).status_code in (409, 422)
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert checked['general_issues'] == []
    usable = [item for item in checked['businesses'] if item['available']]
    blocked = [item for item in checked['businesses'] if not item['available']]
    assert [item['name'] for item in usable] == [SINGLE] and [item['name'] for item in blocked] == [COMPLEX]
    release = generate(client, compilation, checked, instance_id=legacy['instance_id'])
    assert release['package_id'] and release['available_business_ids'] == [usable[0]['id']]
    enabled = enable(client, release)
    assert enabled['status'] == 'partially_active'

    created = {}; values = {}
    for kind in release['snapshot']['object_types']:
        created[kind['name']], values[kind['name']] = create_object(client, release, kind)
    assert {'核对资料', '核对任务'} <= set(created)
    source, target = created['核对资料'], created['核对任务']
    object_path = '/api/instances/' + release['instance_id'] + '/objects'
    listed = reply(client.get(object_path)); by_id = {item['id']: item for item in listed}
    for name, obj in created.items(): assert by_id[obj['id']]['attributes'] == values[name]

    # The type's provenance cannot prove a particular edge. This is a new claim
    # against the actual synthetic source block, then an explicit human review.
    path = '/api/instances/' + release['instance_id'] + '/relations'
    catalog = reply(client.get(path + '/catalog', params={'release_id': release['id']}))
    relation_type = next(item for item in catalog['relation_types'] if item['title'] == '资料关联任务')['id']
    material = reply(client.get('/api/setup/materials/' + seed['scene']['original']['id']))
    block = next(item for item in material['blocks'] if '核对资料关联核对任务' in item['text'])
    claim = {'request_id': str(uuid4()), 'expected_version': catalog['instance_version'],
        'release_id': release['id'], 'reason': '逐字核对合成原文及两个具体登记编号',
        'relation_type': relation_type, 'source_entity_id': source['id'], 'target_entity_id': target['id'],
        'attributes': {}, 'statement': '核对资料关联核对任务',
        'document_refs': [{'material_id': material['id'], 'block_id': block['id'], 'quote': block['text']}],
        'evidence_refs': [], 'valid_from': (now() - timedelta(days=1)).isoformat(), 'valid_until': None}
    saved = reply(client.post(path, json=claim), status=201); relation = saved['record']
    assert saved['saved'] and relation['status'] == 'proposed'
    assert reply(client.post(path, json=claim), status=201)['record']['id'] == relation['id']
    target_kind = next(item for item in release['snapshot']['object_types'] if item['name'] == '核对任务')
    field = next(item['key'] for item in target_kind['fields'] if item['required'] and item['kind'] == '文本')
    assert relation_query(client, release, relation_type, source, target, field)['status'] == 'insufficient'
    reviewed = reply(client.post(path + '/' + relation['id'] + '/review', json={
        'request_id': str(uuid4()), 'expected_version': relation['version'], 'release_id': release['id'],
        'reason': '原文同时列明两个具体登记编号，人工确认本次声明', 'decision': 'confirmed'}))
    assert reviewed['record']['status'] == 'confirmed' and reviewed['record']['usable']
    query = relation_query(client, release, relation_type, source, target, field)
    assert query['status'] == 'sufficient'
    assert query['data']['result']['selected_entity_ids'] == [target['id']]
    assert query['data']['result']['relations']['relation_evidence'][0]['relation_id'] == relation['id']
    listing = reply(client.get(path, params={'release_id': release['id']}))
    assert [item['id'] for item in listing['items']] == [relation['id']]
    history = reply(client.get(path + '/' + relation['id'] + '/history', params={'release_id': release['id']}))
    assert len(history['items']) == 2

    task, creation = task_row(client, release, source)
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert task['business']['name'] == SINGLE and len(task['business']['flow']['steps']) == 1
    step_id = task['workflow_runtime']['current_step_id']
    note = '已对照正式原页核对资料与登记关系；保存人工结论🙂'
    draft = reply(client.put('/api/workspace/tasks/' + task['id'] + '/note', json={
        'version': task['version'], 'step_id': step_id, 'note': note}))
    fetched = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert fetched['workflow_runtime']['drafts'][step_id]['note'] == note
    response = complete(client, draft, note=note)
    assert response.status_code == 200, response.text
    finished = response.json()
    assert finished['status'] == 'workflow_completed' and len(finished['workflow_runtime']['receipts']) == 1
    assert reply(client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json=creation), status=201)['id'] == task['id']

    # A new authenticated browser session reopens the actual persisted records.
    token = reply(client.post('/api/tokens'))['token']
    with TestClient(app) as reopened:
        reopened.headers['Authorization'] = 'Bearer ' + token
        reopened.headers['X-Workspace-ID'] = account[2]
        assert {item['id'] for item in reply(reopened.get(object_path))} == {item['id'] for item in created.values()}
        assert reply(reopened.get(path + '/' + relation['id'], params={'release_id': release['id']}))['status'] == 'confirmed'
        assert reply(reopened.get(path + '/' + relation['id'] + '/history', params={'release_id': release['id']})) == history
        assert reply(reopened.get('/api/workspace/tasks/' + task['id']))['status'] == 'workflow_completed'
        queries = reply(reopened.get('/api/data/queries'))
        assert next(item for item in queries if item['id'] == query['id'])['status'] == 'sufficient'
        assert reply(reopened.get(seed['schema_path'] + '/adoptions/' + seed['schema_adoption']['id']))['receipt'] == seed['schema_adoption']['receipt']
        assert reply(reopened.get(runtime_path + '/requests/' + adoption['request_id']))['receipt'] == accepted['receipt']
    with Session(engine()) as db:
        stored = db.get(InstanceRelease, release['id']); package = db.get(Capability, stored.package_id)
        assert package.status == 'published' and stored.status == 'prepared'
        assert db.get(Relation, relation['id']).status == 'confirmed'
        assert all(db.get(Entity, obj['id']).version == 1 for obj in created.values())
        persisted = db.get(Task, task['id'])
        assert persisted.status == 'workflow_completed'
        result = db.get(Memory, finished['workflow_runtime']['data_receipt_id'])
        assert result.status == 'candidate' and result.data['entity_state_updated'] is False
    checkpoint_path = os.environ.get('GA_V113_CHECKPOINT')
    if checkpoint_path:
        username, password, workspace_id = account
        checkpoint = Path(checkpoint_path)
        checkpoint.touch(mode=0o600, exist_ok=True)
        checkpoint.chmod(0o600)
        checkpoint.write_text(json.dumps({'account': {'username': username, 'password': password,
            'workspace_id': workspace_id}, 'release_id': release['id'], 'instance_id': release['instance_id'],
            'task_id': task['id'], 'object_ids': [obj['id'] for obj in created.values()],
            'relation_id': relation['id'], 'runtime_request_id': adoption['request_id'],
            'schema_request_id': seed['schema_command']['request_id'], 'handoff_id': seed['record']['id'],
            'proof_id': seed['proof_id']}, ensure_ascii=False), encoding='utf-8')
        checkpoint.chmod(0o600)
