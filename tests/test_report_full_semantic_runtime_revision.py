"""Synthetic contract checks and a Root-run real API/PG executor revision.

Only the existing fixture's original IO/provider seams are synthetic. The PG
journey does not patch ledgers, source proof, membership, compilation or tasks.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Capability, Entity, InstanceRelease, Membership, SetupMaterial, Task, User
from generic_agents.security import password_hash
from generic_agents import report_full_semantic_runtime_adoption as adoption
from generic_agents import report_full_semantic_runtime_revision as revision
from generic_agents import report_full_semantic_runtime_v2 as runtime
from generic_agents.report_full_semantic_runtime_models import FullSemanticRuntimeAdoption
from generic_agents.report_full_semantic_runtime_revision_models import FullSemanticRuntimeRevision
from tests.test_report_full_semantic_runtime_v2 import ledger
from tests.test_report_full_semantic_schema_v1_pure import seal, uid
from tests.test_full_semantic_instance_journey_v113 import (
    journey_seed, configured, runtime_command, generate, create_object, SINGLE, ROLE,
    v7_scene, reading_scene, local_runtime, full_source_scene,
)
from tests.test_instance_workspace import enable, task_row, complete
from tests.test_report_evidence_pipeline import reply


def revision_ledger():
    owner, parent, root_before, root_after = ledger()
    previous = parent.data['record']['binding']
    actor = {'user_id': uid(9201), 'username': '新负责人🙂', 'membership_role': 'operator'}
    binding = runtime.build_runtime_binding(previous['source_design'], previous['source_manifest'],
        previous['source_proof'], previous['business_name'], actor, proof_id=previous['proof_id'])
    before = copy.deepcopy(root_after)
    command = revision.Preview(schema='report-full-semantic-runtime-revision-preview-command-v1',
        configuration_revision=6, configuration_hash=digest(before), proof_id=UUID(binding['proof_id']),
        business_name=parent.business_name, actor_id=UUID(actor['user_id']),
        parent={'id': parent.id, 'receipt_hash': parent.data['receipt']['receipt_hash']})
    preview = revision.make_preview(owner, parent.handoff_id, command, binding, previous['executor'])
    body = revision.Adopt.model_validate({**command.model_dump(mode='json'),
        'schema': 'report-full-semantic-runtime-revision-command-v1', 'request_id': uid(9202),
        'preview_hash': preview['preview_hash'], 'consent': True, 'semantic_mode': runtime.SEMANTIC_MODE})
    identity = revision.revision_id(owner, parent.handoff_id, body.request_id)
    origin = runtime.build_origin(identity, binding)
    after = copy.deepcopy(before); after[runtime.CONFIG_KEY][parent.business_name] = origin
    record = seal({'schema': revision.RECORD, 'id': identity, 'workspace_id': owner.workspace_id,
        'actor_id': owner.user_id, 'target_actor_id': actor['user_id'], 'handoff_id': parent.handoff_id,
        'configuration_id': parent.configuration_id, 'request_id': str(body.request_id),
        'business_name': parent.business_name, 'proof_id': parent.proof_id, 'binding': binding, 'origin': origin,
        'command_hash': revision.command_hash(owner, parent.handoff_id, body), 'preview_hash': body.preview_hash,
        'accepted_revision': 7, 'configuration_hash': digest(after), 'parent_id': parent.id, 'root_id': parent.id,
        'parent_receipt_hash': parent.data['receipt']['receipt_hash'],
        'previous_executor': previous['executor']}, 'record_hash')
    data = seal({'schema': revision.LEDGER, 'record': record, 'command': body.model_dump(mode='json'),
        'receipt': revision.make_receipt(record)}, 'ledger_hash')
    row = NS(**{key: record[key] for key in ('id', 'workspace_id', 'actor_id', 'target_actor_id',
        'handoff_id', 'configuration_id', 'request_id', 'business_name', 'proof_id', 'parent_id', 'root_id')},
        version=1, data=data)
    return owner, row, parent, before, after, root_before, root_after


def reseal(row):
    seal(row.data['record'], 'record_hash')
    row.data['receipt'] = revision.make_receipt(row.data['record'])
    seal(row.data, 'ledger_hash')


class HistoryDB:
    """Contract-only history double; the separate postgres test supplies authority."""
    def __init__(self, parent, values):
        self.parent = parent; self.values = iter(values)

    def get(self, model, identity):
        return self.parent if identity == self.parent.id else None

    def scalar(self, query):
        return NS(data=next(self.values))


def test_revision_contract_is_closed_rebuildable_and_preserves_the_legacy_root():
    owner, row, parent, before, after, root_before, root_after = revision_ledger()
    frozen = copy.deepcopy((row.data, parent.data, before, after))
    assert adoption.checked_ledger(row, owner) == row.data
    assert adoption.checked_ledger(parent, owner) == parent.data
    assert adoption.accepted_history(HistoryDB(parent, [before, after, root_before, root_after]),
        owner, row, row.data) == after
    preview = revision.make_preview(owner, row.handoff_id, revision.Adopt.model_validate(row.data['command']),
        row.data['record']['binding'], parent.data['record']['binding']['executor'])
    assert preview['previous_executor'] != preview['binding']['executor']
    assert preview['effect_scope'] == 'future_releases_only'
    assert preview['source_semantics_status'] == 'not_certified'
    assert (row.data, parent.data, before, after) == frozen


@pytest.mark.parametrize('consent', [False, 1, 'true', None])
def test_executor_revision_requires_independent_strict_true_consent(consent):
    _, row, *_ = revision_ledger(); body = copy.deepcopy(row.data['command']); body['consent'] = consent
    with pytest.raises(ValueError): revision.Adopt.model_validate(body)


@pytest.mark.parametrize('change', [
    lambda body: body.update(extra='not allowed'),
    lambda body: body['parent'].update(extra=True),
    lambda body: body['parent'].pop('receipt_hash'),
    lambda body: body.update(configuration_revision=True),
    lambda body: body.update(semantic_mode='source_fact_approval'),
])
def test_revision_command_does_not_accept_extra_fields_or_weaken_parent_scope(change):
    _, row, *_ = revision_ledger(); body = copy.deepcopy(row.data['command']); change(body)
    with pytest.raises(ValueError): revision.Adopt.model_validate(body)


@pytest.mark.parametrize('change', [
    lambda row: row.data['command'].update(consent=False),
    lambda row: row.data['command']['parent'].update(receipt_hash='0' * 64),
    lambda row: row.data['record'].update(parent_id=uid(9299)),
    lambda row: row.data['record']['origin'].update(actor_id=uid(9298)),
    lambda row: row.data['record']['binding']['executor'].update(membership_role='viewer'),
])
def test_resealed_outer_ledger_cannot_forge_revision_consent_or_scope(change):
    owner, row, *_ = revision_ledger(); change(row); reseal(row)
    with pytest.raises(HTTPException): adoption.checked_ledger(row, owner)


@pytest.mark.parametrize('part', ['before', 'after', 'root-before', 'root-after'])
def test_revision_requires_actual_exact_history_all_the_way_to_its_root(part):
    owner, row, parent, before, after, root_before, root_after = revision_ledger()
    values = [before, after, root_before, root_after]
    values[['before', 'after', 'root-before', 'root-after'].index(part)]['forged'] = 'not in the accepted transaction'
    with pytest.raises(HTTPException):
        adoption.accepted_history(HistoryDB(parent, values), owner, row, row.data)


def test_self_consistent_resealed_parent_receipt_cannot_replace_the_actual_parent():
    owner, row, parent, before, after, root_before, root_after = revision_ledger()
    row.data['command']['parent']['receipt_hash'] = '0' * 64
    row.data['record']['parent_receipt_hash'] = '0' * 64
    body = revision.Adopt.model_validate(row.data['command'])
    preview = revision.make_preview(owner, row.handoff_id, body,
        row.data['record']['binding'], row.data['record']['previous_executor'])
    row.data['command']['preview_hash'] = preview['preview_hash']
    body = revision.Adopt.model_validate(row.data['command'])
    row.data['record'].update(preview_hash=body.preview_hash,
        command_hash=revision.command_hash(owner, row.handoff_id, body))
    reseal(row)
    assert adoption.checked_ledger(row, owner) == row.data
    with pytest.raises(HTTPException):
        adoption.accepted_history(HistoryDB(parent, [before, after, root_before, root_after]), owner, row, row.data)


def test_frozen_release_origin_keeps_its_executor_but_still_checks_source_and_membership(monkeypatch):
    owner, row, parent, _, after, *_ = revision_ledger()
    binding = parent.data['record']['binding']; design = binding['source_design']
    db = HistoryDB(parent, [])
    monkeypatch.setattr(adoption, 'accepted_history', lambda *args: None)
    monkeypatch.setattr(adoption.review, 'context', lambda *args: (
        NS(id=parent.configuration_id, data=after), {'design': design}, {parent.handoff_id: {}},
        binding['source_manifest'], None, None))
    monkeypatch.setattr(adoption, '_formal', lambda *args: binding['source_proof'])
    monkeypatch.setattr(adoption, 'executor', lambda *args: binding['executor'])
    origin = parent.data['record']['origin']
    assert adoption.verify_origin(db, owner, origin, require_current=False) == binding
    monkeypatch.setattr(revision, 'require_head', lambda *args: adoption.fail('parent_stale', '已修订'))
    with pytest.raises(HTTPException): adoption.verify_origin(db, owner, origin)
    def unavailable(*args): adoption.fail('actor', '账号不再有执行权限')
    monkeypatch.setattr(adoption, 'executor', unavailable)
    with pytest.raises(HTTPException): adoption.verify_origin(db, owner, origin, require_current=False)
    monkeypatch.setattr(adoption, 'executor', lambda *args: binding['executor'])
    def stale(*args): adoption.fail('source', '正式来源已变化')
    monkeypatch.setattr(adoption, '_formal', stale)
    with pytest.raises(HTTPException): adoption.verify_origin(db, owner, origin, require_current=False)


def revision_command(seed, parent_receipt, actor_id):
    view = reply(seed['client'].get('/api/setup/draft'))
    return {'schema': 'report-full-semantic-runtime-revision-preview-command-v1',
        'configuration_revision': view['revision'], 'configuration_hash': digest(view['configuration']),
        'proof_id': seed['proof_id'], 'business_name': SINGLE, 'actor_id': actor_id,
        'parent': {'id': parent_receipt['id'], 'receipt_hash': parent_receipt['receipt_hash']}}


def test_postgres_executor_revision_keeps_old_receipts_releases_and_tasks_frozen(journey_seed, account):
    seed = journey_seed; client = seed['client']; _, member, compilation = configured(seed)
    path = '/api/setup/full-semantic-runtimes/' + seed['record']['id']
    command = runtime_command(seed, member)
    preview = reply(client.post(path + '/preview', json=command))
    original_command = {**command, 'schema': 'report-full-semantic-runtime-command-v2',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True,
        'semantic_mode': runtime.SEMANTIC_MODE}
    accepted = reply(client.post(path + '/adopt', json=original_command))
    current = reply(client.get('/api/setup/draft')); frozen_flows = copy.deepcopy(current['configuration']['flows'])
    compilation['revision'] = current['revision']
    checked = reply(client.post('/api/instances/check', json=compilation))
    old_release = generate(client, compilation, checked); enable(client, old_release)
    old_object, _ = create_object(client, old_release, old_release['snapshot']['object_types'][0])
    old_task, _ = task_row(client, old_release, old_object)
    password = uuid4().hex; username = 'revision_' + uuid4().hex[:12]
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); db.add(user); db.flush()
        actor_id = user.id
        db.add(Membership(workspace_id=account[2], user_id=actor_id, role='operator')); db.commit()
        frozen = {name: copy.deepcopy(db.get(model, identity).data) for name, model, identity in (
            ('adoption', FullSemanticRuntimeAdoption, accepted['receipt']['id']),
            ('release', InstanceRelease, old_release['id']), ('package', Capability, old_release['package_id']),
            ('task', Task, old_task['id']), ('object', Entity, old_object['id']))}
    request = revision_command(seed, accepted['receipt'], actor_id)
    assert reply(client.get(path + '/revisions'))['bindings'][0]['receipt'] == accepted['receipt']
    bad = copy.deepcopy(request); bad['parent']['receipt_hash'] = '0' * 64
    assert client.post(path + '/revisions/preview', json=bad).status_code == 409
    prepared = reply(client.post(path + '/revisions/preview', json=request))
    assert prepared['previous_executor']['user_id'] == member['id']
    assert prepared['binding']['executor']['user_id'] == actor_id
    assert prepared['effect_scope'] == 'future_releases_only'
    revised_command = {**request, 'schema': 'report-full-semantic-runtime-revision-command-v1',
        'request_id': str(uuid4()), 'preview_hash': prepared['preview_hash'], 'consent': True,
        'semantic_mode': runtime.SEMANTIC_MODE}
    no_consent = {**revised_command, 'consent': 1}
    assert client.post(path + '/revisions/adopt', json=no_consent).status_code == 422
    revised = reply(client.post(path + '/revisions/adopt', json=revised_command))
    assert reply(client.get(path + '/revisions'))['bindings'][0]['receipt'] == revised['receipt']
    assert revised['receipt']['parent_id'] == accepted['receipt']['id']
    assert revised['receipt']['root_id'] == accepted['receipt']['id']
    assert revised['receipt']['parent_receipt_hash'] == accepted['receipt']['receipt_hash']
    assert reply(client.post(path + '/revisions/adopt', json=revised_command))['receipt'] == revised['receipt']
    assert reply(client.get(path + '/revisions/requests/' + revised_command['request_id']))['receipt'] == revised['receipt']
    assert reply(client.get(path + '/revisions/' + revised['receipt']['id']))['receipt'] == revised['receipt']
    assert reply(client.get(path + '/revisions/requests/' + str(uuid4())))['found'] is False
    changed_nonce = {**revised_command, 'actor_id': member['id']}
    assert client.post(path + '/revisions/adopt', json=changed_nonce).status_code == 409
    assert reply(client.get(path + '/requests/' + original_command['request_id']))['receipt'] == accepted['receipt']
    stale_parent = revision_command(seed, accepted['receipt'], member['id'])
    assert client.post(path + '/revisions/preview', json=stale_parent).status_code == 409
    current = reply(client.get('/api/setup/draft'))
    assert current['configuration']['flows'] == frozen_flows
    assert current['configuration'][runtime.CONFIG_KEY][SINGLE] == revised['origin']
    tampered = copy.deepcopy(current['configuration']); tampered[runtime.CONFIG_KEY][SINGLE] = accepted['origin']
    assert client.put('/api/setup/draft', json={'revision': current['revision'], 'configuration': tampered}).status_code == 409
    with Session(engine()) as db:
        for name, model, identity in (
                ('adoption', FullSemanticRuntimeAdoption, accepted['receipt']['id']),
                ('release', InstanceRelease, old_release['id']), ('package', Capability, old_release['package_id']),
                ('task', Task, old_task['id']), ('object', Entity, old_object['id'])):
            assert db.get(model, identity).data == frozen[name]
        assert db.get(FullSemanticRuntimeRevision, revised['receipt']['id']).parent_id == accepted['receipt']['id']
    assert reply(client.get('/api/workspace/tasks/' + old_task['id']))['roles'][ROLE]['user_id'] == member['id']
    compilation.update(revision=current['revision'], role_bindings={ROLE: actor_id})
    new_check = reply(client.post('/api/instances/check', json=compilation))
    assert new_check['roles'][ROLE]['user_id'] == actor_id
    assert new_check['semantic_runtime_plan']['adoptions'][0]['origin']['adoption_id'] == revised['receipt']['id']
    new_release = generate(client, compilation, new_check, instance_id=old_release['instance_id'])
    assert new_release['snapshot']['roles'][ROLE]['user_id'] == actor_id
    assert {item['id'] for item in reply(client.get('/api/instances/' + old_release['instance_id'] + '/releases'))} == {
        old_release['id'], new_release['id']}
    enable(client, new_release)
    # The schema is unchanged: use the actual prior object through the real
    # release-compatibility gate instead of reusing its unique business number.
    new_task, _ = task_row(client, new_release, old_object)
    assert complete(client, new_task).status_code == 403
    with TestClient(app) as executor:
        login = reply(executor.post('/api/login', json={'username': username, 'password': password}))
        executor.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': account[2]})
        assert reply(executor.get(path + '/revisions'))['bindings'][0]['receipt'] == revised['receipt']
        assert reply(executor.get(path + '/revisions/requests/' + revised_command['request_id']))['found'] is False
        assert executor.post(path + '/revisions/adopt', json=revised_command).status_code == 409
        assert complete(executor, old_task).status_code == 403
        assert complete(executor, new_task).status_code == 200
    assert complete(client, old_task).status_code == 200
    # Recovery remains the original response even after a later unrelated save.
    view = reply(client.get('/api/setup/draft')); retained = copy.deepcopy(view['configuration'])
    retained['instanceProfile']['goal'] = '换人后保留旧回执与任务记录'
    saved = reply(client.put('/api/setup/draft', json={'revision': view['revision'], 'configuration': retained}))
    assert saved['configuration'][runtime.CONFIG_KEY][SINGLE] == revised['origin']
    assert reply(client.get(path + '/revisions/requests/' + revised_command['request_id']))['receipt'] == revised['receipt']
    # A real permission withdrawal blocks that frozen executor, while the admin
    # can explicitly append its successor without needing the old actor's grant.
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == account[2],
            Membership.user_id == actor_id))
        membership.role = 'viewer'; db.commit()
    assert client.get('/api/workspace/tasks/' + new_task['id']).status_code == 409
    assert reply(client.get(path + '/revisions/requests/' + revised_command['request_id']))['receipt'] == revised['receipt']
    second_preview_command = revision_command(seed, revised['receipt'], member['id'])
    second_preview = reply(client.post(path + '/revisions/preview', json=second_preview_command))
    second_command = {**second_preview_command, 'schema': 'report-full-semantic-runtime-revision-command-v1',
        'request_id': str(uuid4()), 'preview_hash': second_preview['preview_hash'], 'consent': True,
        'semantic_mode': runtime.SEMANTIC_MODE}
    second = reply(client.post(path + '/revisions/adopt', json=second_command))
    assert second['receipt']['parent_id'] == revised['receipt']['id']
    assert second['receipt']['root_id'] == accepted['receipt']['id']
    assert reply(client.get(path + '/revisions/' + second['receipt']['id']))['receipt'] == second['receipt']
    assert reply(client.post(path + '/revisions/adopt', json=revised_command))['receipt'] == revised['receipt']
    with Session(engine()) as db:
        assert db.query(FullSemanticRuntimeAdoption).count() == 1
        assert db.query(FullSemanticRuntimeRevision).count() == 2
        assert db.get(InstanceRelease, old_release['id']).data == frozen['release']
        assert db.get(InstanceRelease, new_release['id']).data['snapshot']['roles'][ROLE]['user_id'] == actor_id
        db.get(SetupMaterial, seed['scene']['copy']['id']).archived = True; db.commit()
    # Source staleness cannot erase or make an unknown result unrecoverable.
    assert reply(client.get(path + '/revisions'))['bindings'][0]['receipt'] == second['receipt']
    assert reply(client.get(path + '/revisions/requests/' + second_command['request_id']))['receipt'] == second['receipt']
    assert reply(client.post(path + '/revisions/adopt', json=second_command))['receipt'] == second['receipt']
    view = reply(client.get('/api/setup/draft')); retained = copy.deepcopy(view['configuration'])
    retained['instanceProfile']['goal'] = '来源失效后保留修订历史，等待重新核对'
    saved = reply(client.put('/api/setup/draft', json={'revision': view['revision'], 'configuration': retained}))
    assert saved['configuration'][runtime.CONFIG_KEY][SINGLE] == second['origin']
    assert client.post('/api/instances/check', json={'revision': saved['revision'],
        'objects': retained['instanceObjects'], 'role_bindings': {ROLE: member['id']}}).status_code == 409
