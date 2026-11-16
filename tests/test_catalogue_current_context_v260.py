"""M1 real PG/API authoring over an EXPLICIT SYNTHETIC source boundary.

This file does NOT prove original paid/source/provider/capture acceptance.
The existing public V174/V213 pure source fixture supplies the immutable input;
only that external admission loader is replaced. FK-only producer/consumer rows
are labelled failed/cancelled skeletons, never accepted paid evidence. Catalogue
create/save, current identity, config PUT, UUID5 allocation, child commands,
independent Audits, recovery and SAVEPOINT rollback run normally on Root's
disposable PostgreSQL. No accepted Binding or catalogue history is DB-inserted.
Root separately exercises the original customer's real 920-item source.
"""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import catalogue_current_context_store_v260 as current
from generic_agents import report_business_catalogue_store_v211 as cat
from generic_agents import report_business_catalogue_wire_v211 as wire
from generic_agents import report_indexed_inputs_v178 as indexed
from generic_agents.catalogue_current_context_contracts_v260 import child_request, working_identity
from generic_agents.catalogue_current_context_models_v260 import (
    CatalogueCurrentContextBindingV260 as Binding,
    CatalogueCurrentContextCommandV260 as Command)
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
from generic_agents.security import Principal, password_hash
from tests.test_instance_workspace import configuration, save
from tests.test_report_saved_map_design_preparation_v176 import source_scene
from tests.test_vertical_slice import manifest


BASE = '/api/setup/report-business-catalogues/v260/current-contexts'


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def raw(body):
    return json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def h(value):
    return hashlib.sha256(value).hexdigest()


def _replace(value, replacements):
    if type(value) is dict:
        return {key: _replace(item, replacements) for key, item in value.items()}
    if type(value) is list:
        return [_replace(item, replacements) for item in value]
    return replacements.get(value, value) if type(value) is str else value


def _synthetic_origin(p, producer_id, consumer_id, job_id, revision):
    """Reuse the existing pure source builder; never claim a real paid call."""
    _, supplied = source_scene()
    old = supplied['snapshot']
    source = _replace(supplied, {old['actor_id']: p.user_id,
        old['workspace_id']: p.workspace_id, old['producer']['id']: producer_id})
    snapshot = source['snapshot']
    snapshot['map_inputs_hash'] = digest(snapshot['map_proofs'])
    snapshot['snapshot_hash'] = digest({key: value for key, value in snapshot.items()
        if key != 'snapshot_hash'})
    snapshot, claims, _ = cat.checked_source(source)
    index = indexed.build_indexed_inputs(claims, snapshot['snapshot_hash'])
    machine = {'schema': wire.WIRE, 'input_count': len(claims), 'businesses': [{
        'key': 'machine_scope', 'name': '合成机器候选业务', 'purpose': '公开边界夹具',
        'boundary': '不证明原报告语义', 'seed_positions': [0], 'name_basis': 'inferred',
        'reason': '仅为目录历史和事务测试提供明确合成输入'}],
        'allocation_groups': [{'kind': 'business', 'business_keys': ['machine_scope'],
            'input_positions': list(range(len(claims))), 'reason': '合成归属，未经领域验收',
            'question_keys': []}], 'questions': [], 'merge_suggestions': []}
    original = raw(machine)
    projection = wire.build_catalogue_projection(original, indexed=index, input_claims=claims,
        source_snapshot_hash=snapshot['snapshot_hash'], catalog=source['catalog'])
    call_id, request_id = str(uuid4()), str(uuid4())
    metadata = {'schema': cat.ORIGIN, 'consumer_id': consumer_id, 'consumer_version': 1,
        'producer_id': producer_id, 'revision': revision,
        'source_snapshot_hash': snapshot['snapshot_hash'], 'indexed_inputs_hash': index['inputs_hash'],
        'input_claims_hash': index['input_claims_hash'], 'input_count': len(claims),
        'projection_hash': projection['projection_hash'], 'call_id': call_id,
        'capture_hash': digest('synthetic capture boundary, not a real capture'),
        'response_body_sha256': h(b'synthetic source boundary only'),
        'arguments_sha256': h(original)}
    origin = {'metadata': metadata, 'source_context': source, 'indexed_inputs': index,
        'raw_arguments': original.decode(), 'projection': projection,
        'request_hash': digest('synthetic source admission'),
        'job_identity': {'id': job_id, 'workspace_id': p.workspace_id,
            'kind': 'read_saved_map_design_v176', 'actor_id': p.user_id,
            'consumer_id': consumer_id, 'request_id': request_id,
            'idempotency_key': 'saved-map-design:' + request_id},
        'capture_proof': {'receipt': {'call_id': call_id,
            'arguments_sha256': metadata['arguments_sha256']},
            'capture': {'capture_hash': metadata['capture_hash'],
                'body_sha256': metadata['response_body_sha256']},
            'descriptor': {'synthetic_boundary_only': True},
            'binding': {'draft_id': consumer_id, 'reserved_call_id': call_id,
                'actor_id': p.user_id, 'workspace_id': p.workspace_id}}}
    cat._check_origin(origin, p)
    return origin


def catalogue(s, identity):
    return ok(s.client.get(cat.PREFIX + '/drafts/' + identity))


def save_document(s, view, document, actions=None):
    return ok(s.client.post(cat.PREFIX + '/drafts/' + view['id'] + '/save', content=raw({
        'request_id': str(uuid4()), 'expected_version': view['version'],
        'expected_record_hash': view['record_hash'], 'document': document,
        'actions': actions or []}), headers={'Content-Type': 'application/json'}))


def advance(s, suffix='正常配置继续编辑'):
    draft = ok(s.client.get('/api/setup/draft'))
    value = copy.deepcopy(draft['configuration'])
    value['instanceProfile']['goal'] += '；' + suffix
    changed = ok(s.client.put('/api/setup/draft', json={
        'revision': draft['revision'], 'configuration': value}))
    assert changed['revision'] > draft['revision']
    return changed


def ids(s, cls):
    with Session(s.bound) as db:
        return set(db.scalars(select(cls.id).where(cls.workspace_id == s.p.workspace_id)))


def parent_state(s):
    with Session(s.bound) as db:
        row = db.get(cat.Draft, s.parent_id)
        return {column.key: copy.deepcopy(getattr(row, column.key))
            for column in inspect(cat.Draft).columns}


@pytest.fixture
def reuse_scene(client, account, monkeypatch):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_')
    me = ok(client.get('/api/me'))
    p = Principal(me['user_id'], me['workspace_id'], me['role'], 'synthetic-source-boundary')
    revision = save(client, configuration())
    cap = ok(client.post('/api/capabilities', json=manifest('foundation_model',
        namespace='v260_fixture', name='nonexecuted_source_boundary_' + uuid4().hex[:8])), 201)
    producer_id, consumer_id, job_id = (str(uuid4()) for _ in range(3))
    # FK skeletons only: no accepted Consumer, actual CloudCall or paid result.
    with Session(bound) as db:
        db.add(m.SetupReportDraft(id=producer_id, workspace_id=p.workspace_id,
            actor_id=p.user_id, request_id='fixture_' + uuid4().hex[:24],
            status='failed', data={'synthetic_source_boundary_only': True}))
        db.add(m.Job(id=job_id, workspace_id=p.workspace_id,
            kind='read_saved_map_design_v176', status='cancelled',
            idempotency_key='v260-nonexecuted-fk:' + uuid4().hex,
            data={'actor_id': p.user_id, 'synthetic_source_boundary_only': True}))
        db.flush()
        db.add(Consumer(id=consumer_id, workspace_id=p.workspace_id, actor_id=p.user_id,
            request_id=str(uuid4()), producer_id=producer_id, model_id=cap['id'],
            grant_hash=digest('nonexecuted FK ' + consumer_id), revision=revision,
            status='failed', job_id=job_id, reserved_call_id=None,
            data={'synthetic_source_boundary_only': True}))
        db.commit()
    origin = _synthetic_origin(p, producer_id, consumer_id, job_id, revision)
    s = NS(client=client, account=account, bound=bound, p=p, origin=origin,
        consumer_id=consumer_id, source_unavailable=False, source_reads=[])

    def admission(db, actor, identity, *, origin_mode=cat.COMPLETE):
        assert actor.user_id == p.user_id and actor.workspace_id == p.workspace_id
        assert identity == consumer_id and origin_mode == cat.COMPLETE
        s.source_reads.append('origin')
        if s.source_unavailable:
            raise HTTPException(409, 'explicit synthetic immutable-source failure')
        return copy.deepcopy(origin)

    def source_read(db, actor, identity, **kwargs):
        assert actor.user_id == p.user_id and actor.workspace_id == p.workspace_id
        assert identity == producer_id
        s.source_reads.append('source')
        if s.source_unavailable:
            raise HTTPException(409, 'explicit synthetic immutable-source failure')
        return copy.deepcopy(origin['source_context'])

    monkeypatch.setattr(cat, '_load_origin', admission)
    monkeypatch.setattr(current.source, 'load_source_context', source_read)
    created = ok(client.post(cat.PREFIX + '/drafts', json={'request_id': str(uuid4()),
        'consumer_id': consumer_id, 'expected_projection_hash': origin['metadata']['projection_hash']}), 201)
    s.parent_id = created['id']
    view = catalogue(s, s.parent_id)
    document = copy.deepcopy(view['document'])
    document['businesses'] = [{'key': 'human_scope', 'name': '人工保存的完整工作目录😀',
        'purpose': '原作者说明，不代替源语义验收', 'boundary': '只用于公开合成事务测试'}]
    document['allocation_groups'][0]['business_keys'] = ['human_scope']
    actions = [{'kind': 'delete', 'source_business_keys': ['machine_scope'],
        'target_business_keys': [], 'reason': '明确删除此合成候选'},
        {'kind': 'add', 'source_business_keys': [], 'target_business_keys': ['human_scope'],
        'reason': '明确另存合成核对目录'}]
    save_document(s, view, document, actions)
    # Normal saves generate each actual version and both Audits; never set it.
    for number in range(3):
        view = catalogue(s, s.parent_id)
        document = copy.deepcopy(view['document'])
        document['businesses'][0]['purpose'] += '；已保存说明' + str(number)
        save_document(s, view, document)
    s.parent = catalogue(s, s.parent_id)
    assert s.parent['version'] == 5
    s.original_parent = parent_state(s)
    s.jobs, s.calls = ids(s, m.Job), ids(s, m.CloudCall)
    advance(s)
    return s


def context(s, parent_id=None):
    return ok(s.client.get(BASE + '/source-context', params={'draft_id': parent_id or s.parent_id}))


def request_body(s, value=None):
    value = value or context(s)
    assert value['can_fork'] and value['current_source_verified']
    return {'schema': 'catalogue-current-context-fork-v260', 'request_id': str(uuid4()),
        'source_draft_ref': value['source_draft_ref'], 'target_configuration': value['target_configuration'],
        'expected_context_hash': value['context_hash'], 'confirm_preserve_saved_document': True,
        'reason': '明确保留原作者完整目录，在当前配置继续整理；不恢复旧付费许可。😀'}


def post(s, body, original=None):
    return s.client.post(BASE + '/forks', content=original or raw(body),
        headers={'Content-Type': 'application/json'})


def recover(s, body, original):
    return s.client.get(BASE + '/requests/' + body['request_id'], params={'raw_sha256': h(original)})


def fork(s):
    body = request_body(s); original = raw(body)
    receipt = ok(post(s, body, original), 201)
    return body, original, receipt


def binding(s, receipt):
    return ok(s.client.get(BASE + '/bindings/' + receipt['binding_id']))


@contextmanager
def other_client(s, *, same_actor=False):
    from generic_agents.api import app
    if same_actor:
        username, password, workspace = s.account
    else:
        username, password, workspace = 'v260_' + uuid4().hex[:12], uuid4().hex, s.p.workspace_id
        with Session(s.bound) as db:
            user = m.User(username=username, password_hash=password_hash(password))
            db.add(user); db.flush()
            db.add(m.Membership(user_id=user.id, workspace_id=workspace, role='admin')); db.commit()
    with TestClient(app) as client:
        value = ok(client.post('/api/login', json={'username': username, 'password': password}))
        client.headers.update({'X-CSRF-Token': value['csrf_token'], 'X-Workspace-ID': workspace})
        yield client


def test_fork_uses_actual_create_save_structural_copy_and_leaves_original_and_execution_alone(reuse_scene):
    s = reuse_scene; body, original, receipt = fork(s)
    view = binding(s, receipt); working = catalogue(s, receipt['working_draft_ref']['id'])
    assert receipt['outcome'] == 'accepted' and receipt['plan_ref'] is None
    assert UUID(working['id']).version == 5 and working['version'] == 2
    assert working['id'] == working_identity(s.p.workspace_id, body['request_id'])
    assert working['document'] == s.parent['document']
    assert working['document_hash'] == s.parent['document_hash'] and working['plan_ref'] is None
    assert view['target_configuration']['revision'] > s.origin['metadata']['revision']
    assert view['source_pins']['source_revision'] == s.origin['metadata']['revision']
    assert view['can_edit'] and view['can_confirm'] is False
    with Session(s.bound) as db:
        commands = list(db.scalars(select(cat.Command).where(cat.Command.draft_id == working['id'])))
        by_op = {row.operation: row for row in commands}
        assert set(by_op) == {'create', 'save'}
        assert {row['kind'] for row in by_op['save'].data['actions']} == {'delete', 'add'}
        assert by_op['save'].data['document'] == s.parent['document']
        assert db.get(Binding, receipt['binding_id']).record_hash == view['record_hash']
    assert parent_state(s) == s.original_parent
    assert ids(s, m.Job) == s.jobs and ids(s, m.CloudCall) == s.calls
    assert ok(recover(s, body, original))['original_raw'] == original.decode()


def test_same_raw_and_readonly_recovery_keep_initial_receipt_after_head_and_config_advance(reuse_scene):
    s = reuse_scene; body, original, receipt = fork(s); before = binding(s, receipt)
    working = catalogue(s, receipt['working_draft_ref']['id'])
    document = copy.deepcopy(working['document']); document['businesses'][0]['purpose'] += '；后来的人稿'
    save_document(s, working, document); advance(s, '再次正常编辑配置')
    root_ids, audit_ids = ids(s, Command), ids(s, m.Audit)
    assert ok(post(s, body, original), 201) == receipt
    recovered = ok(recover(s, body, original)); now = binding(s, receipt)
    assert recovered['receipt'] == receipt and recovered['original_raw'] == original.decode()
    assert now['fork_receipt'] == receipt and now['initial_working_draft_ref'] == before['initial_working_draft_ref']
    assert now['current_working_draft_ref']['version'] == 3 and now['record_hash'] == before['record_hash']
    history = ok(s.client.get(BASE + '/history', params={'source_draft_id': s.parent_id}))
    assert history['items'] == [receipt]
    assert ids(s, Command) == root_ids and ids(s, m.Audit) == audit_ids


def test_different_nonce_same_context_is_durably_rejected_without_second_copy(reuse_scene):
    s = reuse_scene; body, _, receipt = fork(s)
    second = {**body, 'request_id': str(uuid4())}; original = raw(second)
    rejected = ok(post(s, second, original), 409)['detail']
    assert rejected['receipt']['rejection']['code'] == 'already_forked'
    assert ok(recover(s, second, original)) == rejected
    assert len(ids(s, Binding)) == 1 and len(ids(s, cat.Draft)) == 2
    assert binding(s, receipt)['fork_receipt'] == receipt


def test_raw_nonce_and_foreign_actor_cannot_reuse_a_saved_root_request(reuse_scene):
    s = reuse_scene; body, original, receipt = fork(s)
    before = ids(s, Command), ids(s, cat.Draft), ids(s, m.Audit)
    assert post(s, {**body, 'reason': '另一份正文'}).status_code == 409
    assert s.client.get(BASE + '/requests/' + body['request_id'], params={'raw_sha256': '0' * 64}).status_code == 409
    with other_client(s) as foreign:
        assert foreign.post(BASE + '/forks', content=original,
            headers={'Content-Type': 'application/json'}).status_code == 403
        response = foreign.get(BASE + '/requests/' + body['request_id'], params={'raw_sha256': h(original)})
        assert response.status_code == 403 and body['reason'] not in response.text
        assert foreign.get(BASE + '/bindings/' + receipt['binding_id']).status_code == 404
        assert ok(foreign.get(BASE + '/history', params={'source_draft_id': s.parent_id}))['items'] == []
    # Authentication creates its own session records, never catalogue commands.
    assert (ids(s, Command), ids(s, cat.Draft)) == before[:2]


@pytest.mark.parametrize('failure', ['configuration', 'source'])
def test_known_rejection_is_terminal_and_does_not_create_children_or_authorize_later(reuse_scene, failure):
    s = reuse_scene; body = request_body(s); original = raw(body)
    before = ids(s, cat.Draft), ids(s, cat.Command), ids(s, Binding)
    if failure == 'configuration': advance(s, '申请之后的配置变更')
    else: s.source_unavailable = True
    result = ok(post(s, body, original), 409)['detail']
    assert result['outcome'] == 'rejected'
    assert result['receipt']['rejection']['code'] == ('configuration_changed' if failure == 'configuration' else 'source_unavailable')
    s.source_unavailable = False
    assert ok(recover(s, body, original)) == result
    assert ok(post(s, body, original), 409)['detail'] == result
    assert (ids(s, cat.Draft), ids(s, cat.Command), ids(s, Binding)) == before
    assert parent_state(s) == s.original_parent


def test_failure_after_real_child_save_rolls_back_every_child_before_durable_rejection(reuse_scene, monkeypatch):
    s = reuse_scene; body = request_body(s); original = raw(body)
    before = ids(s, cat.Draft), ids(s, cat.Command), ids(s, Binding)
    mutate, save_root = cat.mutate, current._save_command
    child_ids, cleanup = [], []
    def fail_after_save(db, p, identity, operation, data, **kwargs):
        value = mutate(db, p, identity, operation, data, **kwargs)
        if operation == 'save':
            child_ids.append(value['id'])
            raise HTTPException(409, 'intentional failure AFTER real child save')
        return value
    def record_rejection(*args, **kwargs):
        if kwargs.get('code') == 'preparation_failed': cleanup.append(current._PENDING.get() is None)
        return save_root(*args, **kwargs)
    monkeypatch.setattr(cat, 'mutate', fail_after_save)
    monkeypatch.setattr(current, '_save_command', record_rejection)
    result = ok(post(s, body, original), 409)['detail']
    assert result['receipt']['rejection']['code'] == 'preparation_failed' and child_ids and cleanup == [True]
    assert (ids(s, cat.Draft), ids(s, cat.Command), ids(s, Binding)) == before
    with Session(s.bound) as db:
        assert db.get(cat.Draft, child_ids[0]) is None
        assert not db.scalar(select(cat.Command.id).where(cat.Command.request_id.in_([
            child_request(s.p.workspace_id, body['request_id'], 'create'),
            child_request(s.p.workspace_id, body['request_id'], 'save')])))
    assert ok(recover(s, body, original)) == result and parent_state(s) == s.original_parent


@pytest.mark.parametrize('missing', ['root', 'link', 'binding', 'all_family_anchors'])
def test_missing_independent_anchors_never_downgrade_managed_copy_to_legacy(reuse_scene, missing):
    s = reuse_scene; body, original, receipt = fork(s)
    working = catalogue(s, receipt['working_draft_ref']['id'])
    with Session(s.bound) as db:
        root, bound = db.get(Command, receipt['id']), db.get(Binding, receipt['binding_id'])
        if missing == 'root': db.delete(root)
        elif missing == 'link':
            db.execute(delete(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
                m.Audit.action == current.LINK_ACTION, m.Audit.object_id == bound.id))
        elif missing == 'binding':
            root.binding_id = None; db.flush(); db.delete(bound)
        else:
            db.delete(root); db.flush(); db.delete(bound)
            db.execute(delete(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
                m.Audit.action.in_([current.COMMAND_ACTION, current.BOUND_ACTION, current.LINK_ACTION])))
        db.commit()
    command_ids = ids(s, cat.Command)
    assert s.client.get(cat.PREFIX + '/drafts/' + working['id']).status_code == 409
    response = s.client.post(cat.PREFIX + '/drafts/' + working['id'] + '/save', json={
        'request_id': str(uuid4()), 'expected_version': working['version'],
        'expected_record_hash': working['record_hash'], 'document': working['document'], 'actions': []})
    assert response.status_code == 409
    assert recover(s, body, original).status_code == 409
    if missing == 'all_family_anchors':
        with other_client(s) as foreign:
            response = foreign.get(BASE + '/requests/' + body['request_id'],
                params={'raw_sha256': h(original)})
            assert response.status_code in {403, 409} and body['reason'] not in response.text
    assert ids(s, cat.Command) == command_ids


def test_unconfirmed_author_can_save_during_stale_basis_and_missing_files_but_not_revalidate(reuse_scene):
    s = reuse_scene; _, _, receipt = fork(s); initial = binding(s, receipt)
    working = catalogue(s, receipt['working_draft_ref']['id'])
    advance(s, '保留后来的人工作业'); s.source_unavailable = True
    reads = len(s.source_reads)
    document = copy.deepcopy(working['document']); document['businesses'][0]['purpose'] += '；FS暂缺仍保存的人稿'
    save_document(s, working, document)
    assert len(s.source_reads) == reads
    current_view = binding(s, receipt)
    assert current_view['can_edit'] and not current_view['can_confirm']
    assert current_view['record_hash'] == initial['record_hash'] and current_view['fork_receipt'] == receipt
    assert catalogue(s, working['id'])['document'] == document
    assert context(s, working['id'])['can_fork'] is False
    confirm = s.client.post(cat.PREFIX + '/drafts/' + working['id'] + '/confirm', json={
        'request_id': str(uuid4()), 'expected_version': 3,
        'expected_record_hash': current_view['current_working_draft_ref']['record_hash'],
        'document_hash': current_view['current_working_draft_ref']['document_hash'], 'confirm': True})
    assert confirm.status_code == 409 and ids(s, m.Job) == s.jobs and ids(s, m.CloudCall) == s.calls


def test_new_context_copy_uses_latest_saved_managed_parent_without_changing_its_old_source(reuse_scene):
    s = reuse_scene; _, _, first = fork(s)
    working = catalogue(s, first['working_draft_ref']['id'])
    document = copy.deepcopy(working['document']); document['businesses'][0]['purpose'] += '；必须复制的后续人稿'
    save_document(s, working, document); advance(s, '另建当前版本副本')
    body = request_body(s, context(s, working['id'])); second = ok(post(s, body), 201)
    copied = catalogue(s, second['working_draft_ref']['id']); view = binding(s, second)
    assert copied['document'] == document
    assert second['source_draft_ref']['id'] == working['id'] and second['source_draft_ref']['version'] == 3
    assert view['source_pins']['source_revision'] == s.origin['metadata']['revision']
    assert view['target_configuration']['revision'] > first['target_configuration']['revision']
    assert binding(s, first)['fork_receipt'] == first and parent_state(s) == s.original_parent


def test_own_viewer_recovers_original_receipt_but_cannot_fork_or_save(reuse_scene):
    s = reuse_scene; body, original, receipt = fork(s)
    working = catalogue(s, receipt['working_draft_ref']['id'])
    with Session(s.bound) as db:
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.p.workspace_id,
            m.Membership.user_id == s.p.user_id)); member.role = 'viewer'; db.commit()
    before = ids(s, Command), ids(s, cat.Command), ids(s, Binding)
    assert ok(recover(s, body, original))['receipt'] == receipt
    assert binding(s, receipt)['can_edit'] is False
    assert context(s)['can_fork'] is False
    assert post(s, {**body, 'request_id': str(uuid4())}).status_code == 403
    assert s.client.post(cat.PREFIX + '/drafts/' + working['id'] + '/save', json={
        'request_id': str(uuid4()), 'expected_version': working['version'],
        'expected_record_hash': working['record_hash'], 'document': working['document'], 'actions': []}).status_code == 403
    assert (ids(s, Command), ids(s, cat.Command), ids(s, Binding)) == before


def test_fresh_sessions_in_utc_and_los_angeles_preserve_exact_immutable_hashes(reuse_scene):
    s = reuse_scene; body, original, receipt = fork(s)
    values = []
    for zone in ('UTC', 'America/Los_Angeles'):
        with Session(s.bound) as db:
            db.execute(text("SELECT set_config('TimeZone', :zone, true)"), {'zone': zone})
            recovered = current.request_result(db, s.p, body['request_id'], h(original))
            view = current.binding_view(db, s.p, receipt['binding_id'])
            values.append((recovered, view))
    assert values[0] == values[1]
    assert values[0][0]['receipt'] == receipt
    assert values[0][1]['record_hash'] == binding(s, receipt)['record_hash']


def test_future_confirmation_trace_is_blocked_in_m1_instead_of_presented_as_working_success(reuse_scene):
    s = reuse_scene; _, _, receipt = fork(s)
    with Session(s.bound) as db:
        db.add(m.Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id,
            action=current.CONFIRM_ACTION, object_id=receipt['binding_id'],
            data={'synthetic_negative_only': 'future confirmation verifier is not installed'})); db.commit()
    assert s.client.get(BASE + '/bindings/' + receipt['binding_id']).status_code == 409
    assert s.client.get(cat.PREFIX + '/drafts/' + receipt['working_draft_ref']['id']).status_code == 409
    assert ids(s, m.Job) == s.jobs and ids(s, m.CloudCall) == s.calls


def test_unknown_and_invalid_originals_do_not_allocate_copies_or_new_request_ids(reuse_scene):
    s = reuse_scene; body = request_body(s); original = raw(body)
    before = ids(s, Command), ids(s, cat.Command), ids(s, Binding), ids(s, m.Audit)
    unknown = ok(recover(s, body, original))
    assert not unknown['found'] and unknown['receipt'] is None and unknown['original_raw'] is None
    invalid = [raw({**body, 'confirm_preserve_saved_document': 1}),
        raw({**body, 'extra': 'not allowed'}), raw({**body, 'reason': '\0'}),
        ('{"request_id":"' + body['request_id'] + '","request_id":"' + body['request_id'] + '"}').encode()]
    for value in invalid:
        assert s.client.post(BASE + '/forks', content=value,
            headers={'Content-Type': 'application/json'}).status_code == 422
    assert (ids(s, Command), ids(s, cat.Command), ids(s, Binding), ids(s, m.Audit)) == before


def test_concurrent_same_context_requests_commit_one_real_copy_and_one_terminal_rejection(reuse_scene):
    s = reuse_scene; body = request_body(s)
    bodies = [body, {**body, 'request_id': str(uuid4())}]
    with other_client(s, same_actor=True) as a, other_client(s, same_actor=True) as b:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.post, BASE + '/forks', content=raw(value),
                headers={'Content-Type': 'application/json'}) for client, value in zip((a, b), bodies)]
            responses = [future.result(timeout=30) for future in futures]
    assert sorted(response.status_code for response in responses) == [201, 409], [r.text for r in responses]
    accepted = next(r.json() for r in responses if r.status_code == 201)
    rejected = next(r.json()['detail']['receipt'] for r in responses if r.status_code == 409)
    assert rejected['rejection']['code'] == 'already_forked'
    assert len(ids(s, Binding)) == 1 and len(ids(s, cat.Draft)) == 2
    assert binding(s, accepted)['fork_receipt'] == accepted
    assert parent_state(s) == s.original_parent
