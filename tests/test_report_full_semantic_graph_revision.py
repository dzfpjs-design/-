"""Closed synthetic contracts plus one Root-run API/PG full-graph revision.

Pure history doubles are not provenance authority. The integration case uses
real source, graph, account, publication, compilation, Job and Invocation gates;
only graph_seed's existing synthetic document/provider seams are substituted.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID, uuid4, uuid5

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Capability, Membership, SetupMaterial, SetupRevision, Task, User
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from generic_agents import report_full_semantic_graph_adoption as base
from generic_agents import report_full_semantic_graph_revision as revision
from generic_agents.report_full_semantic_graph_revision_models import FullSemanticGraphRevision
from generic_agents.report_full_semantic_graph_binding_v3 import build_binding
from generic_agents.report_full_semantic_configuration_v1 import derive_flows
from tests.test_report_full_semantic_graph_binding_v3 import three_step_fixture, build
from tests.test_report_full_semantic_schema_v1_pure import uid
from tests.test_report_full_semantic_graph_ledger import ledger_seed
from tests.test_full_semantic_graph_journey_v114 import (
    graph_seed, human, v7_scene, reading_scene, local_runtime, full_source_scene,
)
from tests.test_full_semantic_instance_journey_v113 import generate, create_object
from tests.test_instance_workspace import save
from tests.test_report_evidence_pipeline import reply
from tests.test_vertical_slice import publish


def root_contract():
    values = three_step_fixture(); binding = build(values)
    p = NS(user_id=uid(8001), workspace_id=uid(8002))
    before = {'fullSemanticDesigns': [binding['source_design']],
        'flows': derive_flows(binding['source_design'])}
    preview_body = base.Preview(schema='report-full-semantic-graph-preview-command-v3',
        configuration_revision=3, configuration_hash=digest(before), proof_id=values['proof_id'],
        business_name=values['business_name'], graph=values['graph'], owner_bindings=values['owner_bindings'],
        role_bindings={key: value['user_id'] for key, value in values['role_bindings'].items()},
        external_node_ids=[], node_actions={'b': 'data'})
    preview = base.make_preview(p, binding['handoff_id'], preview_body, binding)
    body = base.Adopt.model_validate({**preview_body.model_dump(mode='json'),
        'schema': 'report-full-semantic-graph-command-v3', 'request_id': uid(8003),
        'preview_hash': preview['preview_hash'], 'consent': True, 'semantic_mode': 'workspace_workflow_design'})
    identity = str(uuid5(UUID(binding['handoff_id']), 'graph-v3:' + p.workspace_id + ':'
        + p.user_id + ':' + str(body.request_id)))
    origin = base.origin_value(identity, binding, command_hash=base.command_hash(p, binding['handoff_id'], body))
    after = copy.deepcopy(before); after[base.CONFIG_KEY] = {binding['business_name']: origin}
    record = base.seal({'id': identity, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'handoff_id': binding['handoff_id'], 'configuration_id': uid(8004), 'request_id': str(body.request_id),
        'binding': binding, 'origin': origin, 'command_hash': base.command_hash(p, binding['handoff_id'], body),
        'preview_hash': preview['preview_hash'], 'accepted_revision': 4, 'configuration_hash': digest(after)}, 'record_hash')
    receipt = base.seal({key: record[key] for key in ('id', 'workspace_id', 'actor_id', 'handoff_id',
        'request_id', 'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash', 'record_hash')}, 'receipt_hash')
    data = base.seal({'schema': base.SCHEMA, 'record': record, 'command': body.model_dump(mode='json'),
        'receipt': receipt}, 'ledger_hash')
    row = NS(**{key: record[key] for key in ('id', 'workspace_id', 'actor_id', 'handoff_id',
        'configuration_id', 'request_id')}, proof_id=values['proof_id'], business_name=values['business_name'], version=1, data=data)
    return p, row, {3: before, 4: after}


def successor(p, parent, before, version, *, action='data', change_graph=True):
    binding = copy.deepcopy(parent.data['record']['binding'])
    if change_graph:
        binding['graph']['id'] += '_next'
        binding = build_binding(binding['source_design'], binding['source_manifest'], binding['source_proof'],
            binding['business_name'], binding['graph'], binding['owner_bindings'], proof_id=binding['proof_id'],
            capability_pins=binding['capability_pins'], role_bindings=binding['role_bindings'])
    preview_body = revision.Preview(schema='report-full-semantic-graph-revision-preview-command-v1',
        configuration_revision=version, configuration_hash=digest(before), proof_id=binding['proof_id'],
        business_name=binding['business_name'], graph=binding['graph'], owner_bindings=binding['owner_bindings'],
        role_bindings={key: value['user_id'] for key, value in binding['role_bindings'].items()},
        external_node_ids=[], node_actions={'b': action},
        parent={'id': parent.id, 'receipt_hash': parent.data['receipt']['receipt_hash']})
    root_id = parent.data['record'].get('root_id', parent.id)
    preview = revision.make_preview(p, parent.handoff_id, preview_body, binding, root_id=root_id,
        previous_binding_hash=parent.data['record']['binding']['binding_hash'])
    body = revision.Adopt.model_validate({**preview_body.model_dump(mode='json'),
        'schema': 'report-full-semantic-graph-revision-command-v1', 'request_id': uid(8100 + version),
        'preview_hash': preview['preview_hash'], 'consent': True, 'semantic_mode': 'workspace_workflow_design'})
    identity = revision.revision_id(p, parent.handoff_id, body.request_id)
    signature = revision.command_hash(p, parent.handoff_id, body)
    origin = base.origin_value(identity, binding, command_hash=signature)
    after = copy.deepcopy(before); after[base.CONFIG_KEY][binding['business_name']] = origin
    record = base.seal({'schema': revision.RECORD, 'id': identity, 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'handoff_id': parent.handoff_id, 'configuration_id': parent.configuration_id,
        'request_id': str(body.request_id), 'business_name': binding['business_name'], 'proof_id': binding['proof_id'],
        'binding': binding, 'origin': origin, 'command_hash': signature, 'preview_hash': body.preview_hash,
        'accepted_revision': version + 1, 'configuration_hash': digest(after), 'parent_id': parent.id,
        'root_id': root_id, 'parent_receipt_hash': parent.data['receipt']['receipt_hash'],
        'previous_binding_hash': parent.data['record']['binding']['binding_hash']}, 'record_hash')
    data = base.seal({'schema': revision.LEDGER, 'record': record, 'command': body.model_dump(mode='json'),
        'receipt': revision.make_receipt(record)}, 'ledger_hash')
    row = NS(**{key: record[key] for key in revision._ROW_FIELDS}, version=1, data=data)
    return row, after


class HistoryDB:
    def __init__(self, rows, history):
        self.rows = {row.id: row for row in rows}; self.history = history

    def scalar(self, statement):
        values = statement.compile().params
        assert 'version_1' in values, 'unexpected query in synthetic history check'
        return copy.deepcopy(self.history.get(values['version_1']))


@pytest.fixture
def contracts(monkeypatch):
    p, root, history = root_contract()
    first, after = successor(p, root, history[4], 4); history[5] = after
    # A separately consented action change must not be reduced to executor-only
    # or graph-only equality: this binding intentionally stays byte-identical.
    second, latest = successor(p, first, after, 5, action='observe', change_graph=False); history[6] = latest
    db = HistoryDB([root, first, second], history)
    monkeypatch.setattr(base, 'lookup_adoption', lambda store, identity, lock=False: store.rows.get(identity))
    return p, root, first, second, db


def reseal(row):
    record = row.data['record']; body = revision.Adopt.model_validate(row.data['command'])
    preview = revision.make_preview(NS(user_id=row.actor_id, workspace_id=row.workspace_id), row.handoff_id,
        body, record['binding'], root_id=row.root_id, previous_binding_hash=record['previous_binding_hash'])
    row.data['command']['preview_hash'] = preview['preview_hash']
    body = revision.Adopt.model_validate(row.data['command'])
    signature = revision.command_hash(NS(user_id=row.actor_id, workspace_id=row.workspace_id), row.handoff_id, body)
    record.update(command_hash=signature, preview_hash=body.preview_hash,
        origin=base.origin_value(row.id, record['binding'], command_hash=signature))
    row.data['record'] = base.seal({key: value for key, value in record.items() if key != 'record_hash'}, 'record_hash')
    row.data['receipt'] = revision.make_receipt(row.data['record'])
    row.data = base.seal({key: value for key, value in row.data.items() if key != 'ledger_hash'}, 'ledger_hash')


def test_closed_chain_cold_rebuild_and_action_only_revision_preserve_all_inputs(contracts):
    p, root, first, second, db = contracts; frozen = copy.deepcopy((root.data, first.data, second.data, db.history))
    assert revision.checked(db, p, first) == first.data
    assert revision.checked(db, p, second) == second.data
    assert second.data['record']['binding'] == first.data['record']['binding']
    assert second.data['command']['node_actions'] != first.data['command']['node_actions']
    assert frozen == (root.data, first.data, second.data, db.history)


def test_chain_limit_allows_last_revision_and_reads_it_but_refuses_another_parent(contracts, monkeypatch):
    p, root, first, second, db = contracts
    assert revision.MAX_CHAIN == 256
    # Exercise the exact boundary with two complete, cold-checked revisions,
    # avoiding hundreds of duplicate semantic rebuilds in a contract test.
    monkeypatch.setattr(revision, 'MAX_CHAIN', 2)
    assert revision._checked_parent(db, p, root) == root.data
    assert revision._checked_parent(db, p, first) == first.data
    assert revision.checked(db, p, second) == second.data
    with pytest.raises(HTTPException) as refused:
        revision._checked_parent(db, p, second)
    assert refused.value.status_code == 409
    third, after = successor(p, second, db.history[6], 6, action='data', change_graph=False)
    db.rows[third.id] = third; db.history[7] = after
    with pytest.raises(HTTPException): revision.checked(db, p, third)


@pytest.mark.parametrize('lock', [False, True])
def test_prepare_refuses_overlong_append_before_live_authority_or_any_write(contracts, monkeypatch, lock):
    p, root, first, second, db = contracts
    monkeypatch.setattr(revision, 'MAX_CHAIN', 2)
    binding = second.data['record']['binding']
    configuration = NS(id=root.configuration_id, version=6, data=copy.deepcopy(db.history[6]))
    monkeypatch.setattr(base.review, 'context', lambda *args, **kwargs: (
        configuration, {'design': binding['source_design']}, {root.handoff_id: {}},
        binding['source_manifest'], None, None))
    def unexpected(*args, **kwargs):
        pytest.fail('overlong append must stop before new proof/member/capability checks or writes')
    monkeypatch.setattr(base.old, '_formal', unexpected)
    monkeypatch.setattr(base, 'members', unexpected)
    monkeypatch.setattr(base, 'capabilities', unexpected)
    third, _ = successor(p, second, configuration.data, 6, action='data', change_graph=False)
    command = revision.preview_command(revision.Adopt.model_validate(third.data['command']))
    frozen = copy.deepcopy((root.data, first.data, second.data, db.history, configuration.data))
    with pytest.raises(HTTPException) as refused:
        revision.prepare(db, p, root.handoff_id, command, lock=lock)
    assert refused.value.status_code == 409
    assert frozen == (root.data, first.data, second.data, db.history, configuration.data)


@pytest.mark.parametrize('consent', [False, 1, 'true', None])
def test_strict_independent_consent(contracts, consent):
    body = copy.deepcopy(contracts[2].data['command']); body['consent'] = consent
    with pytest.raises(ValueError): revision.Adopt.model_validate(body)


@pytest.mark.parametrize('change', [
    lambda body: body.update(extra=True), lambda body: body['parent'].update(extra=True),
    lambda body: body['parent'].pop('receipt_hash'), lambda body: body.update(configuration_revision=True),
    lambda body: body.update(semantic_mode='source_fact_approval'),
])
def test_closed_command_parent_and_cas(contracts, change):
    body = copy.deepcopy(contracts[2].data['command']); change(body)
    with pytest.raises(ValueError): revision.Adopt.model_validate(body)


@pytest.mark.parametrize('version', [3, 4, 5, 6])
def test_every_real_accepted_history_step_is_required(contracts, version):
    p, _, _, second, db = contracts; db.history[version]['forged'] = 'not accepted'
    with pytest.raises(HTTPException): revision.checked(db, p, second)


def test_missing_parent_cycle_and_closed_ledger_fail_closed(contracts):
    p, root, first, second, db = contracts
    del db.rows[first.id]
    with pytest.raises(HTTPException): revision.checked(db, p, second)
    db.rows[first.id] = second
    with pytest.raises(HTTPException): revision.checked(db, p, second)
    db.rows[first.id] = first
    second.data['unexpected'] = True
    with pytest.raises(HTTPException): revision.checked(db, p, second)
    assert root.id in db.rows


def test_source_change_requires_the_actual_successor_relation(monkeypatch):
    from generic_agents import report_full_semantic_replacement as replacement
    old, current, unrelated = uid(8300), uid(8301), uid(8302)
    # This only tests the traversal contract. API adoption independently reads
    # record_set's real, cold-checked replacement ledgers and current proof.
    monkeypatch.setattr(replacement, 'record_set', lambda *args: (
        {old: {}, current: {}, unrelated: {}}, {current: {}, unrelated: {}}, {old: current}))
    revision._lineage(None, None, old, old)
    revision._lineage(None, None, old, current)
    with pytest.raises(HTTPException): revision._lineage(None, None, old, unrelated)
    with pytest.raises(HTTPException): revision._lineage(None, None, current, old)


@pytest.mark.parametrize('part', ['graph', 'owners', 'roles', 'external', 'actions', 'parent', 'root'])
def test_resealed_command_cannot_replace_actual_consent_or_history(contracts, part):
    p, _, first, _, db = contracts
    if part == 'graph': first.data['command']['graph']['id'] += '_forged'
    elif part == 'owners': first.data['command']['owner_bindings'][0]['reason'] += ' forged'
    elif part == 'roles': first.data['command']['role_bindings']['职责甲'] = uid(8991)
    elif part == 'external': first.data['command']['external_node_ids'] = ['b']
    elif part == 'actions': first.data['command']['node_actions']['b'] = 'observe'
    elif part == 'parent':
        first.data['command']['parent']['receipt_hash'] = '0' * 64
        first.data['record']['parent_receipt_hash'] = '0' * 64
    else: first.root_id = uid(8992); first.data['record']['root_id'] = first.root_id
    reseal(first)
    with pytest.raises(HTTPException): revision.checked(db, p, first)


def revision_command(client, seed, receipt):
    current = reply(client.get('/api/setup/draft'))
    body = {key: copy.deepcopy(seed['graph_body'][key]) for key in base.Preview.model_fields}
    body.update(schema='report-full-semantic-graph-revision-preview-command-v1',
        configuration_revision=current['revision'], configuration_hash=digest(current['configuration']),
        parent={'id': receipt['id'], 'receipt_hash': receipt['receipt_hash']})
    return body


def accept(client, path, command, *, request_id=None):
    preview = reply(client.post(path + '/preview', json=command))
    body = {**command, 'schema': 'report-full-semantic-graph-revision-command-v1',
        'request_id': request_id or str(uuid4()), 'preview_hash': preview['preview_hash'],
        'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    result = reply(client.post(path + '/adopt', json=body))
    assert reply(client.post(path + '/adopt', json=body))['receipt'] == result['receipt']
    assert reply(client.get(path + '/requests/' + body['request_id']))['receipt'] == result['receipt']
    return body, result


def activate(client, release):
    instance = next(item for item in reply(client.get('/api/instances')) if item['id'] == release['instance_id'])
    return reply(client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))


def task(client, release, obj):
    result = reply(client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
        'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [obj['id']], 'title': '完整执行修订后按固定版本处理', 'request_id': str(uuid4())}), status=201)
    return reply(client.get('/api/workspace/tasks/' + result['id']))


def test_real_graph_revision_keeps_old_tasks_and_receipts_and_uses_new_full_design(ledger_seed):
    seed = ledger_seed; client = seed['client']; root = seed['graph_acceptance']; path = seed['graph_path'] + '/revisions'
    current = reply(client.get('/api/setup/draft')); root_config = copy.deepcopy(current['configuration'])
    roles = {name: actor for name, actor in seed['graph_body']['role_bindings'].items()}
    compilation = {'revision': current['revision'], 'objects': root_config['instanceObjects'], 'role_bindings': roles}
    checked = reply(client.post('/api/instances/check', json=compilation))
    old_release = generate(client, compilation, checked); activate(client, old_release)
    obj, _ = create_object(client, old_release, old_release['snapshot']['object_types'][0])
    old_task = task(client, old_release, obj); frozen = copy.deepcopy(old_task['graph_runtime'])
    password = uuid4().hex; username = 'graph_revision_' + uuid4().hex[:12]
    with Session(engine()) as db:
        member = User(username=username, password_hash=password_hash(password)); db.add(member); db.flush()
        db.add(Membership(user_id=member.id, workspace_id=seed['scene']['p'].workspace_id, role='operator'))
        new_actor = member.id
        old_cap = db.get(Capability, next(node for node in seed['graph_body']['graph']['nodes']
            if node['kind'] == 'ability')['execution']['capability_id'])
        manifest = copy.deepcopy(old_cap.data); manifest.update(name='graph_revision_tool_' + uuid4().hex[:12])
        db.commit()
    new_cap = publish(client, manifest)
    current = reply(client.get('/api/setup/draft')); config = copy.deepcopy(current['configuration'])
    business_name = seed['graph_body']['business_name']
    config['automation']['policies'][business_name]['rules']['observe'] = {'mode': '自动运行'}
    save(client, config, review_automation=True)
    command = revision_command(client, seed, root['receipt'])
    command['role_bindings']['职责甲'] = new_actor
    for node in command['graph']['nodes']:
        if node['kind'] == 'human': node['actor_id'] = new_actor
        if node['kind'] == 'ability':
            with Session(engine()) as db:
                cap = db.get(Capability, new_cap['id'])
                node['execution'].update(capability_id=cap.id, capability_hash=cap.content_hash,
                    release_version=cap.release_version, input_schema=copy.deepcopy(cap.data['input_schema']),
                    output_schema=copy.deepcopy(cap.data['output_schema']), max_attempts=cap.data['budget']['max_attempts'])
    command['graph']['predicates']['p_after']['right']['value'] = 1
    command['owner_bindings'][0]['reason'] += ' 本次独立确认新能力、负责人和判断门槛。'
    first_body, first = accept(client, path, command)
    assert first['effect_scope'] == 'future_releases_only' and first['source_semantics_status'] == 'not_certified'
    assert first['receipt']['parent_id'] == root['receipt']['id'] and first['receipt']['root_id'] == root['receipt']['id']
    assert reply(client.get('/api/workspace/tasks/' + old_task['id']))['graph_runtime'] == frozen
    assert reply(client.get(seed['graph_path'] + '/requests/' + seed['graph_body']['request_id']))['receipt'] == root['receipt']
    stale_parent = revision_command(client, seed, root['receipt'])
    assert client.post(path + '/preview', json=stale_parent).status_code == 409
    conflict_nonce = copy.deepcopy(first_body); conflict_nonce['request_id'] = seed['graph_body']['request_id']
    assert client.post(path + '/adopt', json=conflict_nonce).status_code == 409
    changed_nonce = copy.deepcopy(first_body); changed_nonce['node_actions']['b'] = 'observe'
    assert client.post(path + '/adopt', json=changed_nonce).status_code == 409
    # A second revision changes only the separately confirmed automation action.
    second_command = {**copy.deepcopy(command), 'parent': {'id': first['receipt']['id'],
        'receipt_hash': first['receipt']['receipt_hash']}}
    current = reply(client.get('/api/setup/draft'))
    second_command.update(configuration_revision=current['revision'], configuration_hash=digest(current['configuration']))
    second_command['node_actions'] = {'b': 'observe'}
    second_body, second = accept(client, path, second_command)
    assert second['receipt']['parent_id'] == first['receipt']['id']
    current = reply(client.get('/api/setup/draft'))
    downgraded = copy.deepcopy(current['configuration'])
    downgraded[base.CONFIG_KEY][business_name] = root['origin']
    assert client.put('/api/setup/draft', json={'revision': current['revision'], 'configuration': downgraded}).status_code == 409
    compilation.update(revision=current['revision'], role_bindings=command['role_bindings'])
    checked = reply(client.post('/api/instances/check', json=compilation))
    new_release = generate(client, compilation, checked, instance_id=old_release['instance_id']); activate(client, new_release)
    assert new_release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['origin'] == second['origin']
    new_task = task(client, new_release, obj)
    assert client.post('/api/workspace/tasks/' + new_task['id'] + '/graph/complete', json={
        'request_id': str(uuid4()), 'activation_id': new_task['graph_pending_humans'][0]['activation_id'],
        'version': 0, 'cancel_epoch': 0, 'output': {'ok': True}}).status_code == 409
    old_job = next(iter(old_task['graph_jobs'].values()))['job_id']; assert process_once(old_job, deliver=False)
    old_task = reply(client.get('/api/workspace/tasks/' + old_task['id']))
    old_task, _ = human(client, old_task, 'a'); old_task, _ = human(client, old_task, 'c')
    assert old_task['completed']
    assert any(item['predicate_id'] == 'p_after' and item['result']['truth'] == 'true'
        for item in old_task['graph_runtime']['predicate_receipts'])
    with TestClient(app) as assigned:
        login = reply(assigned.post('/api/login', json={'username': username, 'password': password}))
        assigned.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': seed['scene']['p'].workspace_id})
        new_task, _ = human(assigned, new_task, 'a', draft=True)
        new_job = next(iter(new_task['graph_jobs'].values()))['job_id']; assert process_once(new_job, deliver=False)
        new_task = reply(assigned.get('/api/workspace/tasks/' + new_task['id']))
        new_task, _ = human(assigned, new_task, 'a')
        assert new_task['completed']
        assert new_task['graph_pending_humans'] == []
        assert any(item['predicate_id'] == 'p_after' and item['result']['truth'] == 'false'
            for item in new_task['graph_runtime']['predicate_receipts'])
        assert all(item['node_id'] != 'c' for item in new_task['graph_runtime']['activations'].values())
    with Session(engine()) as db:
        row = db.get(FullSemanticGraphRevision, second['receipt']['id'])
        assert revision.checked(db, seed['scene']['p'], row)['receipt'] == second['receipt']
        assert db.get(Task, old_task['id']).data['instance_release_id'] == old_release['id']
        assert db.get(Task, new_task['id']).data['instance_release_id'] == new_release['id']
        history = list(db.scalars(select(SetupRevision).where(SetupRevision.draft_id == row.configuration_id)))
        assert {first['receipt']['accepted_revision'], second['receipt']['accepted_revision']} <= {item.version for item in history}
        db.get(SetupMaterial, seed['scene']['copy']['id']).archived = True; db.commit()
    assert reply(client.get(path + '/requests/' + first_body['request_id']))['receipt'] == first['receipt']
    assert reply(client.get(path + '/requests/' + second_body['request_id']))['receipt'] == second['receipt']
    assert reply(client.post(path + '/adopt', json=second_body))['receipt'] == second['receipt']
    stale = reply(client.get('/api/setup/draft')); compilation['revision'] = stale['revision']
    assert client.post('/api/instances/check', json=compilation).status_code == 409
