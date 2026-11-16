"""Task/projection integration seams only; no SQL, services, models or browser.

The definition prover has its own adversarial pure tests. These tests establish
that workflow entry points call it, retain original pins and never send the
server-only proof through existing ability input projections.
"""
import copy
import socket
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import native_goals as goals
from generic_agents import workflow_abilities as abilities
from generic_agents import workspace_api as workspace
from generic_agents.contracts import digest
from generic_agents.models import DomainInstance, Entity, InstanceRelease, Task


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('continuity integration pure tests prohibit SQL, processes and network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(Session, 'execute', forbidden)
    monkeypatch.setattr(Session, 'commit', forbidden)
    # These synthetic workflow Tasks have no dual-handoff ledger. Keep the
    # real verifier while isolating its SQL lookup; real bindings have PG tests.
    from generic_agents import signal_dual_handoffs as dual
    monkeypatch.setattr(dual, 'parent_for_task', lambda db, actor, task_id: None)


def principal():
    return NS(user_id='actor', workspace_id='workspace', role='admin')


@pytest.fixture
def snapshot_verifier_seam(monkeypatch):
    """The prover's own tests cover proof validation; this tests its consumers."""
    from generic_agents import entity_release_compat as compat
    calls = []
    def verify(task, release=None):
        calls.append((task.id, release.id if release else None))
        return task.data.get('object_definition_bindings')
    monkeypatch.setattr(compat, 'verify_task_binding_snapshot', verify)
    return calls


def fixtures():
    fields = [{'key': 'field_title', 'label': '标题', 'kind': '文本', 'required': True, 'unit': None},
              {'key': 'field_value', 'label': '数量', 'kind': '数值', 'required': True, 'unit': '株'}]
    catalog = {'key': 'type_stable', 'name': '业务对象', 'fields': fields}
    business = {'id': 'business', 'name': '核对业务', 'available': True,
                'execution_mode': 'registered_workflow', 'flow': {'steps': [{'id': 'human', 'agent': '人工确认'}]}}
    release = NS(id='release-new', instance_id='instance', package_id='package-new', release_number=2,
                 data={'snapshot': {'object_types': [catalog], 'businesses': [business], 'materials': []}})
    instance = NS(id='instance', namespace='instance_namespace', status='partially_active',
                  active_release_id=release.id, data={'enabled_business_ids': [business['id']]})
    entities = [NS(id='original-a', workspace_id='workspace', namespace=instance.namespace,
                   entity_type=catalog['key'], schema_capability_id='package-original', version=7,
                   data={'field_title': '合成甲', 'field_value': 20}),
                NS(id='original-b', workspace_id='workspace', namespace=instance.namespace,
                   entity_type=catalog['key'], schema_capability_id='package-original', version=11,
                   data={'field_title': '合成乙', 'field_value': 28})]
    snapshots = [{'id': x.id, 'version': x.version, 'type_name': catalog['name'],
                  'attributes': copy.deepcopy(x.data), 'fields': copy.deepcopy(fields)} for x in entities]
    task = NS(id='task', package_id=release.package_id, owner_id='actor', title='合成核对',
              status='workflow_waiting', version=1, data={
                  'instance_id': instance.id, 'instance_release_id': release.id,
                  'business_id': business['id'], 'entity_ids': [x.id for x in entities],
                  'object_snapshots': snapshots,
                  'workflow_runtime': {'receipts': [], 'current_step_id': 'human'}})
    return instance, release, business, entities, task


def attach_bindings(task):
    # Opaque prover seam fixture, deliberately not presented as a real proof.
    bindings = {'schema': 'instance-entity-bindings-v1', 'instance_id': task.data['instance_id'],
                'target_release_id': task.data['instance_release_id'],
                'objects': [{'entity_id': x['id'], 'entity_version': x['version'],
                             'attributes_hash': digest(x['attributes']),
                             'definition': {'synthetic_prover_seam': True}} for x in task.data['object_snapshots']]}
    task.data.update(object_definition_bindings=bindings, object_definition_bindings_hash=digest(bindings))
    return bindings


class MemoryUnitOfWork:
    def __init__(self, prior=None):
        self.prior = prior
        self.added = []
        self.commits = 0

    def scalar(self, query):
        return self.prior

    def add(self, row):
        self.added.append(row)

    def flush(self):
        for row in self.added:
            if isinstance(row, Task):
                row.id = 'new-task'
                row.version = 1
                row.created_at = datetime(2026, 10, 16, tzinfo=timezone.utc)

    def commit(self):
        self.commits += 1


def create_seams(monkeypatch, *, reject=None):
    from generic_agents import entity_release_compat as compat
    from generic_agents import workflow_images, workflow_scenes, workflow_audio
    instance, release, business, entities, expected = fixtures()
    checked = []
    def scoped(db, model, identity, actor, lock=False):
        if model is DomainInstance:
            assert identity == instance.id
            return instance
        assert model is Entity
        return next(x for x in entities if x.id == identity)
    def require_entity(db, actor, i, r, entity):
        assert i is instance and r is release
        checked.append(entity.id)
        if entity.id == reject:
            raise HTTPException(409, '定义证明不可读')
        return {'synthetic_prover_seam': True}
    def freeze(db, actor, i, r, rows):
        assert list(rows) == entities and checked == [x.id for x in entities]
        return attach_bindings(expected)
    monkeypatch.setattr(workspace, 'scoped', scoped)
    monkeypatch.setattr(workspace, 'active', lambda *a: (instance, release))
    monkeypatch.setattr(workspace, 'material_ids_for_flow', lambda flow: set())
    monkeypatch.setattr(workspace, 'verify_material_snapshots', lambda *a: None)
    monkeypatch.setattr(workspace, 'audit', lambda *a: None)
    monkeypatch.setattr(workspace, 'view', lambda row: row)
    monkeypatch.setattr(compat, 'require_entity', require_entity)
    monkeypatch.setattr(compat, 'freeze_task_objects', freeze)
    for module in (workflow_images, workflow_scenes, workflow_audio):
        monkeypatch.setattr(module, 'snapshots', lambda *a: [])
    monkeypatch.setattr(abilities, 'continue_flow', lambda *a: None)
    body = workspace.NewTask(release_id=release.id, business_id=business['id'],
                             entity_ids=[x.id for x in entities], title='核对原对象', request_id='synthetic-request-001')
    return instance, release, entities, expected, checked, body


def test_task_create_preserves_original_entities_and_freezes_private_proof(monkeypatch):
    instance, release, entities, expected, checked, body = create_seams(monkeypatch)
    before = copy.deepcopy([vars(x) for x in entities])
    db = MemoryUnitOfWork()
    task = workspace.create(instance.id, body, principal(), db)
    assert checked == [x.id for x in entities]
    assert task.package_id == release.package_id and task.data['instance_release_id'] == release.id
    assert task.data['object_snapshots'] == expected.data['object_snapshots']
    assert all(set(x) == {'id', 'version', 'type_name', 'attributes', 'fields'} for x in task.data['object_snapshots'])
    assert task.data['object_definition_bindings'] == expected.data['object_definition_bindings']
    assert task.data['object_definition_bindings_hash'] == digest(task.data['object_definition_bindings'])
    assert [vars(x) for x in entities] == before
    assert len(db.added) == 1 and db.commits == 1


def test_task_create_stops_before_persistence_when_second_original_definition_rejected(monkeypatch):
    instance, _, entities, _, checked, body = create_seams(monkeypatch, reject='original-b')
    before = copy.deepcopy([vars(x) for x in entities]);db = MemoryUnitOfWork()
    with pytest.raises(HTTPException, match='定义证明不可读'):
        workspace.create(instance.id, body, principal(), db)
    assert checked == [x.id for x in entities]
    assert not db.added and db.commits == 0 and [vars(x) for x in entities] == before


def test_idempotent_create_returns_original_task_without_repinning(monkeypatch):
    instance, _, _, old_task, checked, body = create_seams(monkeypatch)
    old_task.data.update(creation_hash=workspace.creation_body_hash(body), creation_request_id=body.request_id)
    before = copy.deepcopy(old_task.data);db = MemoryUnitOfWork(old_task)
    assert workspace.create(instance.id, body, principal(), db) is old_task
    assert old_task.data == before and checked == [] and db.commits == 0 and not db.added


def test_execution_rechecks_definition_permissions_and_preserves_old_task(monkeypatch):
    from generic_agents import entity_release_compat as compat
    _, release, business, _, task = fixtures();attach_bindings(task)
    before = copy.deepcopy(task.data);calls = []
    monkeypatch.setattr(workspace, 'saved_execution', lambda *a: (task, release, business))
    monkeypatch.setattr(workspace, 'published', lambda *a: calls.append('target-published'))
    def verify(db, actor, value, target):
        assert value is task and target is release
        calls.append('fresh-definition-permission')
        raise HTTPException(409, '源业务包已撤销')
    monkeypatch.setattr(compat, 'verify_task_objects', verify)
    with pytest.raises(HTTPException, match='源业务包已撤销'):
        workspace.execution(None, principal(), task.id)
    assert calls == ['target-published', 'fresh-definition-permission'] and task.data == before


def test_saved_execution_remains_history_read_without_new_execution_permission(monkeypatch):
    from generic_agents.models import Capability
    instance, release, business, _, task = fixtures();attach_bindings(task)
    release.configuration_hash = digest(release.data['snapshot'])
    package = NS(kind='domain_package', status='retired', data={'specification': {'configuration_hash': release.configuration_hash}})
    package.content_hash = digest(package.data);package.validated_hash = package.content_hash
    rows = {Task: task, DomainInstance: instance, InstanceRelease: release, Capability: package}
    # The cold data-contract verifier also reads the original scoped release
    # and package; this legacy fixture deliberately carries no new marker.
    from generic_agents import instance_data_receipts as reception
    release.workspace_id=principal().workspace_id
    monkeypatch.setattr(reception,'scoped',lambda db,model,*args:rows[model])
    monkeypatch.setattr(workspace, 'scoped', lambda db, model, *a: rows[model])
    monkeypatch.setattr(workspace, 'published', lambda *a: pytest.fail('history read grants no execution'))
    before = copy.deepcopy(task.data)
    assert workspace.saved_execution(None, principal(), task.id) == (task, release, business)
    assert task.data == before


def test_ability_checked_refuses_before_capability_or_input_projection(monkeypatch):
    from generic_agents import entity_release_compat as compat
    _, release, business, _, task = fixtures();attach_bindings(task)
    before = copy.deepcopy(task.data);calls = []
    def verify(db, actor, value, target):
        calls.append((value.id, target.id))
        raise HTTPException(409, '源定义不再可读')
    monkeypatch.setattr(compat, 'verify_task_objects', verify)
    monkeypatch.setattr(abilities, 'published', lambda *a: pytest.fail('revoked proof must stop before ability lookup'))
    with pytest.raises(HTTPException, match='源定义不再可读'):
        abilities.checked(None, principal(), task, release, business, {})
    assert calls == [(task.id, release.id)] and task.data == before


@pytest.mark.parametrize('with_binding', [False, True])
@pytest.mark.parametrize('can_save_note', [False, True])
def test_task_detail_explains_revoked_new_proof_without_hiding_history_or_writing(monkeypatch, with_binding, can_save_note):
    from generic_agents import entity_release_compat as compat
    from generic_agents import native_step_execution, workflow_cancellation, workflow_ability_requests, workflow_worker_status
    _, release, business, _, task = fixtures()
    release.data['snapshot'].update(roles={}, profile={})
    if with_binding:
        attach_bindings(task)
    before = copy.deepcopy(task.data);checks = []
    monkeypatch.setattr(workspace, 'saved_execution', lambda *a: (task, release, business))
    monkeypatch.setattr(workspace, 'view', lambda row: {'id': row.id, 'object_snapshots': copy.deepcopy(row.data['object_snapshots'])})
    def verify(*args, **kwargs):
        assert kwargs == {'lock_sources': False}
        checks.append(task.id)
        raise HTTPException(409, '原对象来源业务包已撤销，已保存结果保留')
    monkeypatch.setattr(compat, 'verify_task_objects', verify)
    monkeypatch.setattr(native_step_execution, 'unresolved_for_step', lambda *a: False)
    monkeypatch.setattr(workflow_cancellation, 'permissions', lambda *a, **k: {
        'can_save_note': can_save_note, 'can_complete_step': True, 'can_cancel': True,
        'reasons': {} if can_save_note else {'save_note': '当前账号无权保存说明'}, 'reason': None})
    monkeypatch.setattr(workflow_ability_requests, 'permissions', lambda *a, blocked=False: {
        'can_run_ability': not blocked, 'can_retry_ability': not blocked, 'can_probe_ability': True, 'reasons': {}})
    monkeypatch.setattr(workflow_worker_status, 'automatic', lambda *a: {'status': 'not_running'})
    result = workspace.detail(task.id, principal(), None)
    assert result['id'] == task.id and result['object_snapshots'] == before['object_snapshots']
    assert result['native_execution_blocked'] is with_binding
    assert result['permissions']['can_save_note'] is can_save_note
    assert result['permissions']['can_complete_step'] is not with_binding
    assert result['permissions']['can_run_ability'] is not with_binding
    assert result['permissions']['can_retry_ability'] is not with_binding
    assert result['permissions']['can_cancel'] and result['permissions']['can_probe_ability']
    if with_binding:
        assert checks == [task.id]
        assert '原对象来源业务包已撤销' in result['permissions']['blocked_reason']
        assert result['permissions']['reasons']['complete_step'] == result['permissions']['reasons']['run_ability']
        assert ('仍可保存' in result['permissions']['reason']) is can_save_note
    else:
        assert not checks and 'blocked_reason' not in result['permissions']
    if not can_save_note:
        assert result['permissions']['reasons']['save_note'] == '当前账号无权保存说明'
    assert task.data == before


@pytest.mark.parametrize('allowed', [False, True])
def test_revoked_definition_does_not_erase_authorized_human_draft_or_grant_note_permission(monkeypatch, allowed):
    from generic_agents import entity_release_compat as compat
    _, release, business, _, task = fixtures();attach_bindings(task)
    task.data['workflow_runtime']['drafts']={}
    before = copy.deepcopy(task.data);version = task.version;commits=[];authority=[]
    monkeypatch.setattr(workspace, 'saved_execution', lambda *a, **k: (task, release, business))
    monkeypatch.setattr(compat, 'verify_task_objects', lambda *a, **k: pytest.fail('saving a human draft must not dispatch or require live source definition'))
    def authorize(*args):
        authority.append(args)
        if not allowed:
            raise HTTPException(403, '当前账号无权保存说明')
    monkeypatch.setattr(workspace, 'authorize_step', authorize)
    monkeypatch.setattr(workspace, 'audit', lambda *a, **k: None)
    monkeypatch.setattr(workspace, 'view', lambda row: {'id': row.id, 'version': row.version})
    body=workspace.Note(version=version,step_id='human',note='来源需要恢复；先保存已完成的人工核对说明')
    db=NS(commit=lambda:commits.append('saved'))
    if allowed:
        result=workspace.note(task.id,body,principal(),db)
        assert result['version']==version+1 and commits==['saved']
        assert task.data['workflow_runtime']['drafts']['human']['note']==body.note
        assert task.data['workflow_runtime']['receipts']==before['workflow_runtime']['receipts']
        assert task.data['object_definition_bindings']==before['object_definition_bindings']
        assert task.data['object_snapshots']==before['object_snapshots']
    else:
        with pytest.raises(HTTPException,match='当前账号无权保存说明'):
            workspace.note(task.id,body,principal(),db)
        assert task.data==before and task.version==version and not commits
    assert len(authority)==1 and task.status=='workflow_waiting'


def test_bound_inputs_keep_original_versions_and_exclude_private_definition_proof():
    _, _, _, entities, task = fixtures();attach_bindings(task)
    for entity in entities:
        entity.version += 1;entity.data['field_value'] = 999
    step = {'execution': {'bindings': {'records': {'source': 'object_records'},
                                      'context': {'source': 'task_context'}, 'ids': {'source': 'object_ids'}}}}
    result = abilities.bind(task, step, None, principal(), 'original-request')
    assert result['records'] == [
        {'entity_id': 'original-a', 'version': 7, 'attributes': {'标题': '合成甲', '数量': 20}},
        {'entity_id': 'original-b', 'version': 11, 'attributes': {'标题': '合成乙', '数量': 28}}]
    assert sum(row['attributes']['数量'] for row in result['records']) / 2 == 24
    assert result['ids'] == task.data['entity_ids']
    assert set(result['context']) == {'task_title', 'object_snapshots', 'material_snapshots', 'perception_event', 'source_clock'}
    assert 'object_definition_bindings' not in repr(result)
    assert 'synthetic_prover_seam' not in repr(result)


def test_goal_binding_pin_is_absent_for_legacy_and_exact_for_new_tasks(snapshot_verifier_seam):
    _, _, _, _, task = fixtures()
    assert goals.object_binding_pin(task) == {}
    attach_bindings(task)
    expected = {'object_definition_bindings_hash': task.data['object_definition_bindings_hash']}
    assert goals.object_binding_pin(task) == expected
    assert goals.object_binding_pin(task, expected) == expected
    with pytest.raises(HTTPException, match='不一致'):
        goals.object_binding_pin(task, {'object_definition_bindings_hash': 'changed'})


def test_goal_binding_pin_refuses_removal_from_an_existing_goal_context(snapshot_verifier_seam):
    _, _, _, _, task = fixtures();attach_bindings(task)
    context = {'object_definition_bindings_hash': task.data['object_definition_bindings_hash']}
    del task.data['object_definition_bindings'];del task.data['object_definition_bindings_hash']
    with pytest.raises(HTTPException):
        goals.object_binding_pin(task, context)


def test_goal_binding_pin_uses_shared_snapshot_verifier_and_propagates_rejection(monkeypatch):
    from generic_agents import entity_release_compat as compat
    _, _, _, _, task = fixtures();attach_bindings(task);calls = []
    def verify(row, release=None):
        calls.append(row.id)
        raise HTTPException(409, '冻结对象依据损坏')
    monkeypatch.setattr(compat, 'verify_task_binding_snapshot', verify)
    with pytest.raises(HTTPException, match='冻结对象依据损坏'):
        goals.object_binding_pin(task)
    assert calls == [task.id]


def goal_context(task):
    pin = {'spec': {'deadline': None}}
    context = {'schema': 'native-business-goal-v1', 'instance_id': task.data['instance_id'],
               'release_id': task.data['instance_release_id'], 'business_id': task.data['business_id'],
               'entity_ids': task.data['entity_ids'], 'actor_id': 'actor', 'goal_snapshot': pin,
               **goals.object_binding_pin(task)}
    task.data.update(native_goal_context=context, native_goal_context_hash=digest(context))
    return context, pin


def test_goal_runtime_rechecks_new_definition_proof(monkeypatch, snapshot_verifier_seam):
    from generic_agents import entity_release_compat as compat
    from generic_agents import native_goal_time
    instance, release, _, _, task = fixtures();attach_bindings(task)
    context, pin = goal_context(task);before = copy.deepcopy(task.data);calls = []
    monkeypatch.setattr(goals, 'active', lambda *a: (instance, release))
    monkeypatch.setattr(goals, 'snapshot', lambda *a: pin)
    monkeypatch.setattr(native_goal_time, 'timeout', lambda *a: False)
    def verify(db, actor, value, target):
        calls.append((value.id, target.id));return instance
    monkeypatch.setattr(compat, 'verify_task_objects', verify)
    assert goals.guard(None, principal(), task) == context
    assert calls == [(task.id, release.id)] and task.data == before


def test_goal_history_accepts_saved_new_binding_but_legacy_stays_original_package_only(monkeypatch, snapshot_verifier_seam):
    instance, release, _, entities, task = fixtures();attach_bindings(task)
    context, pin = goal_context(task)
    monkeypatch.setattr(workspace, 'saved_execution', lambda *a: (task, release, {}))
    monkeypatch.setattr(goals, 'snapshot', lambda *a: pin)
    def scoped(db, model, identity, actor, *a):
        return instance if model is DomainInstance else next(x for x in entities if x.id == identity)
    monkeypatch.setattr(goals, 'scoped', scoped)
    assert goals.saved_context(None, principal(), task) == context
    assert snapshot_verifier_seam[-1] == (task.id, release.id)
    del task.data['object_definition_bindings'];del task.data['object_definition_bindings_hash']
    context.pop('object_definition_bindings_hash');task.data['native_goal_context_hash'] = digest(context)
    with pytest.raises(HTTPException, match='范围已改变'):
        goals.saved_context(None, principal(), task)


@pytest.mark.parametrize('with_binding', [False, True])
def test_defining_goal_adds_new_binding_pin_without_changing_legacy_context(monkeypatch, snapshot_verifier_seam, with_binding):
    from generic_agents import entity_release_compat as compat
    from generic_agents import native_goal_time, native_query_proposals
    instance, release, _, _, task = fixtures()
    if with_binding:
        attach_bindings(task)
    monkeypatch.setattr(goals, 'journal', lambda *a: True)
    monkeypatch.setattr(goals, 'active', lambda *a: (instance, release))
    monkeypatch.setattr(compat, 'verify_task_objects', lambda *a: instance)
    monkeypatch.setattr(goals, 'initialize', lambda db, actor, row, spec, reason: row.data.update(goal_spec=spec, goal_hash='goal-hash'))
    monkeypatch.setattr(goals, 'snapshot', lambda *a: {'synthetic_goal_pin': True})
    monkeypatch.setattr(goals, 'queue', lambda *a: None)
    monkeypatch.setattr(goals, 'audit', lambda *a: None)
    monkeypatch.setattr(native_goal_time, 'arm', lambda *a: None)
    monkeypatch.setattr(native_query_proposals, 'formal_definition_provenance', lambda *a: None)
    spec = {'criteria': [{'kind': 'evidence_sufficient'}]}
    body = NS(expected_version=1, request_id='synthetic-request', reason='合成纯测', goal_spec=NS(model_dump=lambda: copy.deepcopy(spec)))
    goals.define(None, principal(), task, body)
    context = task.data['native_goal_context']
    assert ('object_definition_bindings_hash' in context) is with_binding
    assert task.data['native_goal_context_hash'] == digest(context)
    assert set(context) == {'schema', 'instance_id', 'release_id', 'business_id', 'entity_ids',
                            'actor_id', 'goal_snapshot'} | ({'object_definition_bindings_hash'} if with_binding else set())
