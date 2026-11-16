"""Real issued source/consent/package/PG/Job/Invocation graph journey.

Only synthetic document conversion/OCR/provider transport seams are substituted.
No design, ledger, compiler, package, capability, worker or task gate is patched.
Root alone executes this test against its disposable fenced PostgreSQL service.
"""
import copy
import json,os
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Capability, Task, Invocation, Job, Memory
from generic_agents.worker import process_once
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_source_review_postgres import v7_scene, reading_scene, local_runtime
from tests.test_report_full_semantic_region_source_postgres import full_source_scene
from tests.test_report_full_semantic_schema_adoption import schema_seed, adopted
from tests.test_full_semantic_instance_journey_v113 import configured, generate, create_object
from tests.test_instance_workspace import enable
from tests.test_workflow_abilities import tool
from tests.test_report_full_semantic_graph_binding_v3 import three_step_fixture, owner_rows, ref
from generic_agents.report_full_semantic_graph_binding_v3 import required_owner_fields


@pytest.fixture
def graph_seed(v7_scene, monkeypatch):
    from tests import test_report_full_semantic_source_review_postgres as source_review
    template = three_step_fixture(); original = source_review.human_value

    def clean(value):
        if isinstance(value, list): return [clean(x) for x in value]
        if not isinstance(value, dict): return value
        output = {key: clean(child) for key, child in value.items()}
        if 'citations' in output:
            output['citations'] = []
            if 'inference_reason' in output: output['inference_reason'] = '合成工作区设计，执行映射需独立采用'
        if 'status' in output and 'explanation' in output:
            output.update(status='inferred', explanation='合成工作区设计，非原文事实')
        return output

    def human_design(result):
        value = original(result)
        value['result']['businesses'] = [clean(template['design']['result']['businesses'][0]),
            {'operation': 'upsert', 'original_name': '', 'name': '另一个未确认业务',
                'trigger': '人工发起', 'output': '核对资料', 'steps': [{
                    'name': '核对资料', 'purpose': '核对资料', 'agent': '人工确认', 'role': '资料核对人',
                    'output': '核对结果', 'next_index': None, 'else_index': None, 'branch_condition': '',
                    'citations': [], 'inference_reason': '合成设计'}], 'citations': [], 'inference_reason': '合成设计',
                'participants': [], 'interactions': [], 'conditions': [], 'coordination': []}]
        value['result']['global_claims'] = clean(template['design']['result']['global_claims'])
        value['result']['questions'] = copy.deepcopy(template['design']['result']['questions'])
        return value

    monkeypatch.setattr(source_review, 'human_value', human_design)
    seed = schema_seed.__wrapped__(source_review.complete_source_review_seed(v7_scene, monkeypatch))
    _, _, acceptance = adopted(seed)
    return {**seed, 'schema_adoption': acceptance, 'template': template}


def graph_command(seed, member, configuration, cap):
    from generic_agents.report_full_semantic_runtime_adoption import executor
    client = seed['client']; state = reply(client.get(seed['path']))
    design, manifest = seed['record']['design'], state['manifest']
    graph = copy.deepcopy(seed['template']['graph'])
    for node in graph['nodes']:
        if node['source_step_id']:
            index = {'a': 0, 'b': 1, 'c': 2}[node['id']]
            node['source_step_id'] = design['business_bindings'][0]['step_ids'][index]
        if node['kind'] in {'human', 'ability'}: node['actor_id'] = member['id']
    ability = next(n for n in graph['nodes'] if n['kind'] == 'ability')
    with Session(engine()) as db:
        row = db.get(Capability, cap['id'])
        ability['execution'] = {'capability_id': row.id, 'capability_hash': row.content_hash,
            'agent_role': 'business', 'input_bindings': {'records': 'records'},
            'arguments': {'field': '数量', 'operation': 'count'}, 'input_schema': copy.deepcopy(row.data['input_schema']),
            'output_schema': copy.deepcopy(row.data['output_schema']), 'adapter': 'builtin',
            'release_version': row.release_version, 'max_attempts': row.data['budget']['max_attempts']}
    graph['input_schema'] = {'type': 'object', 'properties': {'object_records': {'type': 'array', 'items': {'type': 'object'}}},
        'required': ['object_records'], 'additionalProperties': False}
    graph['references'] = {'records': {'kind': 'array', 'nullable': False, 'source': 'input', 'field': 'object_records', 'node_id': None},
        'count': {'kind': 'number', 'nullable': True, 'source': 'output', 'field': 'value', 'node_id': 'b'}}
    graph['predicates']['p_after'] = {'op': 'gte', 'left': {'op': 'ref', 'key': 'count'},
        'right': {'op': 'value', 'kind': 'number', 'value': 0}}
    targets = {row['owner_path']: {c['source_field']: copy.deepcopy(c['targets']) for c in row['clauses']}
        for row in seed['template']['owner_bindings']}
    resolutions = {}; scopes = {}

    def origin(name, kind='object', field=None):
        source = configuration['instanceObjectOrigins' if kind == 'object' else 'instanceRelationOrigins'][name]
        field_type = None
        if field:
            obj = next(o for o in design['result']['objects'] if o['name'] == name)
            field_type = next(f['kind'] for f in obj['fields'] if f['label'] == field)
        return {'kind': 'schema_origin', 'id': name, 'field': field, 'field_type': field_type, 'origin': copy.deepcopy(source)}

    actual = {}
    for spec in required_owner_fields(design):
        path = spec['owner_path']; fields = spec['source_fields']; kind = spec['kind']
        actual[path] = copy.deepcopy(targets.get(path, {f: [] for f in fields}))
        if path.startswith('/businesses/1'):
            actual[path] = {f: [] for f in fields}; resolutions[path] = 'unresolved'
            scopes[path] = [design['result']['businesses'][1]['name']]
        elif kind in {'domain', 'question'}:
            actual[path] = {f: [] for f in fields}; resolutions[path] = 'informational'
        elif kind in {'object', 'field'}:
            oi = int(path.split('/')[2]); obj = design['result']['objects'][oi]
            field = obj['fields'][int(path.split('/')[4])]['label'] if kind == 'field' else None
            actual[path] = {f: [origin(obj['name'], field=field)] for f in fields}
        elif kind == 'relationship':
            relation = design['result']['relationships'][int(path.split('/')[2])]
            actual[path] = {f: [origin(relation['name'], 'relationship')] for f in fields}
            actual[path]['source_type'] = [origin(relation['source_type'])]
            actual[path]['target_type'] = [origin(relation['target_type'])]
    actual['/global_claims/0']['statement'].extend([ref('value_ref', 'records'), ref('value_ref', 'count')])
    owners = owner_rows(design, manifest, design['result']['businesses'][0]['name'], actual,
        resolutions=resolutions, scopes=scopes)
    draft = reply(client.get('/api/setup/draft'))
    return {'schema': 'report-full-semantic-graph-preview-command-v3', 'configuration_revision': draft['revision'],
        'configuration_hash': digest(draft['configuration']), 'proof_id': seed['proof_id'],
        'business_name': design['result']['businesses'][0]['name'], 'graph': graph, 'owner_bindings': owners,
        'role_bindings': {'职责甲': member['id'], '职责乙': member['id']}, 'external_node_ids': [],'node_actions':{'b':'data'}}


def human(client, task, node_id, *, draft=False, request_id=None):
    pending = next(x for x in task['graph_pending_humans'] if x['node_id'] == node_id)
    body = {'request_id': request_id or str(uuid4()), 'activation_id': pending['activation_id'],
        'version': pending['version'], 'cancel_epoch': task['graph_runtime']['cancel_epoch'],
        **({'draft': {'ok': True}} if draft else {'output': {'ok': True}})}
    response = client.put('/api/workspace/tasks/' + task['id'] + '/graph/draft', json=body) if draft else client.post(
        '/api/workspace/tasks/' + task['id'] + '/graph/complete', json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def test_real_full_graph_parallel_human_builtin_join_branch_and_durable_result(graph_seed,account,monkeypatch):
    seed = graph_seed; client = seed['client']; view, member, compilation = configured(seed)
    from tests.test_instance_workspace import save
    configuration=copy.deepcopy(view['configuration'])
    name=seed['record']['design']['result']['businesses'][0]['name']
    configuration['automation']['policies'][name]['rules']['data']={'mode':'自动运行'}
    save(client,configuration,review_automation=True)
    view=reply(client.get('/api/setup/draft'))
    cap = tool(client, agent='business')
    command = graph_command(seed, member, view['configuration'], cap)
    path = '/api/setup/full-semantic-graphs/' + seed['record']['id']
    preview = reply(client.post(path + '/preview', json=command))
    body = {**command, 'schema': 'report-full-semantic-graph-command-v3', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(path + '/adopt', json=body))
    assert reply(client.post(path + '/adopt', json=body))['receipt'] == accepted['receipt']
    assert reply(client.get(path + '/requests/' + body['request_id']))['found']
    current = reply(client.get('/api/setup/draft'))
    compilation.update(revision=current['revision'], role_bindings={'职责甲': member['id'], '职责乙': member['id']})
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert checked['general_issues'] == [], checked['general_issues']
    release = generate(client, compilation, checked)
    assert len(release['available_business_ids']) == 1
    instance=next(i for i in reply(client.get('/api/instances')) if i['id']==release['instance_id'])
    reply(client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
        'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True}))
    obj, _ = create_object(client, release, release['snapshot']['object_types'][0])
    payload = {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [obj['id']], 'title': '并行核对真实登记及注册统计', 'request_id': str(uuid4())}
    task = reply(client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json=payload), status=201)
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert [x['node_id'] for x in task['graph_pending_humans']] == ['a']
    assert len(task['graph_jobs']) == 1
    task, _ = human(client, task, 'a', draft=True)
    job = next(iter(task['graph_jobs'].values()))['job_id']
    assert process_once(job, deliver=False)
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert task['graph_pending_humans'][0]['draft'] == {'ok': True}
    assert task['graph_pending_humans'][0]['node_id'] == 'a'  # human did not block the capability
    assert next(iter(task['graph_jobs'].values()))['phase'] == 'succeeded'
    assert not task['completed']
    task, saved = human(client, task, 'a')
    assert [x['node_id'] for x in task['graph_pending_humans']] == ['c']
    assert reply(client.post('/api/workspace/tasks/' + task['id'] + '/graph/complete', json=saved))['id'] == task['id']
    task, final_body = human(client, task, 'c')
    assert task['completed'] and task['status'] == 'workflow_completed'
    reopened = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert reopened['graph_runtime'] == task['graph_runtime']
    with Session(engine()) as db:
        persisted = db.get(Task, task['id']); assert persisted.data['graph_runtime'] == reopened['graph_runtime']
        calls = db.scalars(select(Invocation).where(Invocation.capability_id == cap['id'])).all()
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        assert calls[0].data['output']['provenance'][0]['entity_id'] == obj['id']
        assert db.get(Memory, persisted.data['workflow_runtime']['data_receipt_id']).status == 'candidate'
    # A real successful tool result may arrive after cancellation. Preserve the
    # Invocation and input draft while preventing every successor activation.
    payload['request_id']=str(uuid4())
    second=reply(client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json=payload),status=201)
    second=reply(client.get('/api/workspace/tasks/'+second['id']))
    second,_=human(client,second,'a',draft=True)
    from generic_agents import gateway
    original=gateway.invoke
    def cancel_after_result(*args,**kwargs):
        call=original(*args,**kwargs)
        current=reply(client.get('/api/workspace/tasks/'+second['id']))
        cancel={'request_id':str(uuid4()),'version':current['graph_runtime']['version'],
            'cancel_epoch':current['graph_runtime']['cancel_epoch'],'reason':'保留真实返回，停止后续处理'}
        reply(client.post('/api/workspace/tasks/'+second['id']+'/graph/cancel',json=cancel))
        return call
    monkeypatch.setattr(gateway,'invoke',cancel_after_result)
    job=next(iter(second['graph_jobs'].values()))['job_id'];assert process_once(job,deliver=False)
    cancelled=reply(client.get('/api/workspace/tasks/'+second['id']))
    assert cancelled['status']=='workflow_cancelled' and not cancelled['completed']
    assert next(iter(cancelled['graph_jobs'].values()))['phase']=='late_result_preserved'
    assert all(a['node_id']!='c' for a in cancelled['graph_runtime']['activations'].values())
    assert any(a['draft']=={'ok':True} for a in cancelled['graph_runtime']['activations'].values())
    checkpoint_path=os.environ.get('GA_V114_CHECKPOINT')
    if checkpoint_path:
        username,password,workspace=account
        checkpoint=Path(checkpoint_path);checkpoint.touch(mode=0o600,exist_ok=True);checkpoint.chmod(0o600)
        checkpoint.write_text(json.dumps({'account':{'username':username,'password':password,'workspace_id':workspace},
            'release_id':release['id'],'instance_id':release['instance_id'],'task_id':task['id'],
            'cancelled_task_id':cancelled['id'],'object_ids':[obj['id']],'graph_request_id':body['request_id'],
            'handoff_id':seed['record']['id'],'proof_id':seed['proof_id']},ensure_ascii=False))
        checkpoint.chmod(0o600)
