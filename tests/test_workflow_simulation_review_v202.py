"""Pure authority/receipt tests. Real DB/runtime acceptance belongs to Root."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import workflow_simulation_review_v202 as s
from generic_agents import workflow_registered_tool_plan_v201 as tools
from generic_agents.contracts import digest
from generic_agents.models import Audit

AT = datetime(2026, 10, 25, tzinfo=timezone.utc)
REAL_REVIEW_CONTEXT = s._context


def result():
    return {'review_state': 'needs_information', 'recommended_candidate_ref': 'supported',
        'analysis': {'summary': '仅合成资料，实际设备情况未确认',
            'recommendation': {'candidate_ref': 'supported', 'reason': '仅推荐工程验证', 'evidence_refs': ['original']},
            'candidate_assessments': [
                {'candidate_ref': 'supported', 'assessment': 'supported', 'reason': '有原资料，仍有缺项',
                    'evidence_refs': ['original'], 'missing_information': ['缺少实际设备范围', '缺少业务阈值']},
                {'candidate_ref': 'other', 'assessment': 'insufficient', 'reason': '依据不足',
                    'evidence_refs': [], 'missing_information': ['替代方案依据']}]}}


class IO:
    def __init__(self):
        self.events = []; self.call_id = None; self.committed_id = None
    def scalars(self, statement):
        assert statement.column_descriptions[0]['entity'] is Audit
        return self.events
    def scalar(self, statement):
        if 'clock_timestamp' in str(statement): return AT
        return self.committed_id if statement.column_descriptions[0].get('entity') is Audit else self.call_id
    def add(self, event):
        event.id = str(uuid4()); self.events.append(event)
    def flush(self):
        pass


@pytest.fixture
def scene(monkeypatch):
    db = IO(); p = NS(user_id='reviewer', workspace_id='workspace', role='admin')
    inputs = {'reasoning_result': result(), 'candidate_plans': []}
    job = NS(id='job', workspace_id=p.workspace_id, kind='execute_full_semantic_graph_node',
        idempotency_key='original-key', status='failed', version=5, attempts=1,
        lease_token='original-expired-lease', lease_until=AT-timedelta(seconds=1), available_at=AT,
        data={'task_id': 'task', 'activation_id': 'a', 'actor_id': 'executor',
            'graph_hash': 'g', 'cancel_epoch': 0, 'activation_version': 0,
            'input_hash': digest(inputs), 'inputs': inputs, 'invocation_key': 'original-key',
            'attempt': 1, 'recovery_only': False})
    transport = {'job_id': job.id, 'invocation_key': 'original-key', 'phase': 'blocked',
        'attempt': 1, 'invocation_id': None, 'input_hash': digest(inputs), 'cancel_epoch': 0}
    state = {'cancel_epoch': 0, 'graph_hash': 'g'}
    activation = {'id': 'a', 'version': 0, 'epoch': 0}
    task = NS(id='task', workspace_id=p.workspace_id, version=8,
        data={'graph_runtime': state, 'graph_jobs': {'a': transport}})
    producer = {'invocation_id': 'original-model', 'capture_hash': 'raw-answer', 'output_hash': digest(result())}
    pin = {'job': s._stable_job(job), 'graph_hash': 'g', 'cancel_epoch': 0,
        'binding_hash': 'binding', 'capability_id': 'cap', 'capability_hash': 'cap-hash',
        'producer': producer, 'result_hash': digest(result()),
        'targets': [{'capability_id': 'sim', 'capability_hash': 'sim-hash'}]}
    selected, assessment = s.selected_with_gaps(result())
    targets = [{'display_name': '明确模拟工具', 'simulation_only': True}]
    monkeypatch.setattr(s, '_context', lambda *_args, **_kwargs: (task, job, state, activation,
        task.data['graph_jobs']['a'], selected, copy.deepcopy(assessment), targets, copy.deepcopy(pin)))
    from generic_agents import relations, workflow_graph_goal_guard_v133
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *_args: None)
    monkeypatch.setattr(workflow_graph_goal_guard_v133, 'reservations', lambda _task: [])
    basis = s._basis_value(task, job, state, activation, selected, assessment, targets, pin)
    command = s.ReviewCommand(request_id=uuid4(), activation_id='a', expected_task_version=8,
        expected_job_version=5, basis_hash=basis['basis_hash'],
        acknowledged_missing_information=assessment['missing_information'],
        acknowledge_simulation_only=True, reason='验证模拟回执与归档，缺项仍待补充')
    return NS(db=db, p=p, task=task, job=job, pin=pin, command=command,
        basis=basis, inputs=inputs, producer=producer, transport=transport)


def test_review_preserves_answer_gaps_and_original_effect_attempt(scene):
    before = copy.deepcopy(scene.inputs)
    receipt = s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert receipt['queued'] and receipt['engineering_only']
    assert receipt['gaps_resolved'] is False and receipt['human_review_verified'] is False
    assert scene.inputs == before
    assert scene.job.status == 'queued' and scene.job.version == 6 and scene.job.attempts == 1
    assert scene.job.data['invocation_key'] == 'original-key' and scene.job.data['attempt'] == 1
    assert scene.job.lease_token is None and scene.job.lease_until is None
    assert 'invocation_id' not in scene.task.data['graph_jobs']['a']
    assert 'invocation_status' not in scene.task.data['graph_jobs']['a']
    record = s.historical_record(scene.db, scene.task, scene.command.request_id)
    assert record['prior_job']['status'] == 'failed'
    assert record['prior_job']['lease_token'] == 'original-expired-lease'
    assert record['prior_transport'] == scene.transport
    assert record['prior_transport']['invocation_id'] is None
    assert record['basis']['missing_information'] == before['reasoning_result']['analysis']['candidate_assessments'][0]['missing_information']


@pytest.mark.parametrize('projection', ['absent', 'null', 'original_unknown'])
def test_preflight_failure_keeps_canonical_unlinked_projection_and_real_original_reference(scene, projection):
    from generic_agents.workflow_graph import record_failure
    transport = scene.task.data['graph_jobs']['a']
    if projection == 'absent': transport.pop('invocation_id', None)
    if projection == 'null': transport.update(invocation_id=None, invocation_status=None)
    if projection == 'original_unknown':
        transport.update(invocation_id='original-unknown-parent', invocation_status='unknown')
    assert record_failure(scene.db, scene.p, scene.task, 'a', '派发前核对受阻') == 'blocked'
    after = scene.task.data['graph_jobs']['a']
    if projection == 'original_unknown':
        assert after['invocation_id'] == 'original-unknown-parent' and after['invocation_status'] == 'unknown'
    else:
        assert 'invocation_id' not in after and 'invocation_status' not in after


def test_failure_with_real_call_projects_only_that_original_call(scene):
    from generic_agents.workflow_graph import record_failure
    call = NS(id='actual-original-parent', status='unknown')
    record_failure(scene.db, scene.p, scene.task, 'a', '原调用结果未知', call)
    assert scene.task.data['graph_jobs']['a']['invocation_id'] == call.id
    assert scene.task.data['graph_jobs']['a']['invocation_status'] == 'unknown'


def test_failure_cannot_overwrite_nonempty_original_reference(scene):
    from generic_agents.workflow_graph import record_failure
    scene.task.data['graph_jobs']['a'].update(invocation_id='original-parent', invocation_status='unknown')
    before = copy.deepcopy(scene.task.data)
    with pytest.raises(HTTPException):
        record_failure(scene.db, scene.p, scene.task, 'a', '其他调用不可以覆盖', NS(id='other-parent', status='failed'))
    assert scene.task.data == before and not scene.db.events


def test_exact_request_replay_never_requeues_or_requires_live_context(scene, monkeypatch):
    first = s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    scene.job.status = 'succeeded'; scene.job.version = 99
    monkeypatch.setattr(s, '_context', lambda *_args: pytest.fail('historical replay used live gate'))
    replay = s.submit(scene.db, scene.p, scene.task, None, scene.command)
    assert replay == {**first, 'replayed': True}
    assert scene.job.status == 'succeeded' and scene.job.version == 99 and len(scene.db.events) == 1


@pytest.mark.parametrize('change', ['reason', 'actor', 'activation', 'missing'])
def test_replay_cannot_replace_original_body_or_actor(scene, change):
    s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    command = scene.command.model_copy(deep=True)
    if change == 'reason': command.reason = '更换原因'
    if change == 'actor': scene.p.user_id = 'another-admin'
    if change == 'activation': command.activation_id = 'other'
    if change == 'missing': command.acknowledged_missing_information = ['不同缺项']
    with pytest.raises(HTTPException): s.submit(scene.db, scene.p, scene.task, None, command)
    assert len(scene.db.events) == 1


@pytest.mark.parametrize('change', ['task_version', 'job_version', 'basis', 'missing_order', 'false_ack', 'live_lease'])
def test_stale_or_incomplete_review_cannot_queue(scene, change):
    if change == 'task_version': scene.task.version += 1
    if change == 'job_version': scene.job.version += 1
    if change == 'basis': scene.command.basis_hash = 'b' * 64
    if change == 'missing_order': scene.command.acknowledged_missing_information.reverse()
    if change == 'false_ack': scene.command.acknowledge_simulation_only = False
    if change == 'live_lease': scene.job.lease_until = AT + timedelta(seconds=5)
    with pytest.raises(HTTPException): s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert scene.job.status == 'failed' and not scene.db.events


@pytest.mark.parametrize('marker', ['dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'])
def test_even_false_dispatch_marker_blocks_new_queue(scene, marker):
    scene.task.data['graph_jobs']['a'][marker] = False
    with pytest.raises(HTTPException): s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert scene.job.status == 'failed' and not scene.db.events


def test_original_invocation_or_goal_reservation_cannot_be_bypassed(scene, monkeypatch):
    scene.db.call_id = 'existing-original'
    with pytest.raises(HTTPException): s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    scene.db.call_id = None
    from generic_agents import workflow_graph_goal_guard_v133
    monkeypatch.setattr(workflow_graph_goal_guard_v133, 'reservations',
        lambda _task: [{'invocation_key': 'original-key', 'job_id': scene.job.id}])
    with pytest.raises(HTTPException): s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert not scene.db.events and scene.job.status == 'failed'


def test_old_dispatch_audit_cannot_be_hidden_by_deleting_transport_markers(scene):
    scene.db.committed_id = 'original-dispatch-audit'
    with pytest.raises(HTTPException): s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert not scene.db.events and scene.job.status == 'failed'


def test_other_graph_request_family_cannot_reuse_review_nonce(scene):
    from generic_agents.workflow_graph_api import exclusive_nonce, nonce_records
    scene.task.data['graph_runtime']['commands'] = []
    s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert nonce_records(scene.task, str(scene.command.request_id))[0][0] == 'simulation_review'
    for family in ('graph', 'ability_retry', 'message_retry'):
        with pytest.raises(HTTPException): exclusive_nonce(scene.task, scene.command.request_id, family)


@pytest.mark.parametrize('change', ['delete_audit', 'tamper_audit', 'tamper_record', 'duplicate_audit', 'malformed_record'])
def test_saved_review_needs_exact_independent_audit(scene, change):
    s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    if change == 'delete_audit': scene.db.events.clear()
    if change == 'tamper_audit': scene.db.events[0].data = {**scene.db.events[0].data, 'record_hash': 'fake'}
    if change == 'tamper_record': scene.task.data[s.KEY][str(scene.command.request_id)]['receipt']['gaps_resolved'] = True
    if change == 'duplicate_audit': scene.db.events.append(copy.deepcopy(scene.db.events[0]))
    if change == 'malformed_record': scene.task.data[s.KEY][str(scene.command.request_id)]['receipt'] = None
    with pytest.raises(HTTPException): s.historical_record(scene.db, scene.task, scene.command.request_id)


def test_new_nonce_cannot_queue_same_activation_twice(scene):
    s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    other = scene.command.model_copy(update={'request_id': uuid4()})
    with pytest.raises(HTTPException): s.submit(scene.db, scene.p, scene.task, scene.job, other)
    assert len(scene.db.events) == 1


def test_historical_public_request_omits_internal_lease_proof_and_keeps_original_body(scene):
    s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    record = s.historical_record(scene.db, scene.task, scene.command.request_id)
    public = s.public_request(record)
    assert set(public) == {'actor_id', 'body_hash', 'body', 'receipt'}
    assert public['body'] == scene.command.model_dump(mode='json')
    assert s.public_receipts(scene.db, scene.task)['a']['missing_information'] == scene.command.acknowledged_missing_information


@pytest.mark.parametrize('value', ['true', 1, None, 'false'])
def test_simulation_acknowledgement_never_coerces(value, scene):
    body = scene.command.model_dump(mode='json'); body['acknowledge_simulation_only'] = value
    with pytest.raises(ValidationError): s.ReviewCommand.model_validate(body)


@pytest.mark.parametrize('change', ['unsupported', 'no_recommendation', 'no_evidence', 'conflict', 'no_gaps', 'ready_state'])
def test_missing_review_does_not_waive_selection_or_conflicts(change):
    value = result(); selected = value['analysis']['candidate_assessments'][0]
    if change == 'unsupported': selected['assessment'] = 'insufficient'
    if change == 'no_recommendation': value['analysis']['recommendation']['candidate_ref'] = 'other'
    if change == 'no_evidence': selected['evidence_refs'] = []
    if change == 'conflict': value['analysis']['candidate_assessments'][1]['assessment'] = 'conflicting'
    if change == 'no_gaps': selected['missing_information'] = []
    if change == 'ready_state': value['review_state'] = 'ready_for_human_review'
    with pytest.raises(HTTPException): s.selected_with_gaps(value)


def test_201_does_not_consult_review_until_normal_selection_rejects(monkeypatch):
    value = result(); value['analysis']['candidate_assessments'][0]['missing_information'] = []
    monkeypatch.setattr(s, 'checked_selection', lambda *_args: pytest.fail('complete choice used SIM review'))
    assert tools.eligible_candidate(None, None, None, None, {'reasoning_result': value}, {}) == 'supported'


def test_201_missing_choice_cannot_execute_without_saved_review(scene):
    with pytest.raises(HTTPException):
        tools.eligible_candidate(scene.db, scene.p, scene.task, scene.job, scene.inputs, scene.producer)


def test_non_admin_cannot_review(scene):
    scene.p.role = 'operator'
    with pytest.raises(HTTPException) as error:
        s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    assert error.value.status_code == 403 and not scene.db.events


def checked_context(scene, monkeypatch):
    from generic_agents import workflow_graph_scope, workflow_graph
    binding = {'binding_hash': 'binding', 'simulation_only': True}
    node = {'execution': {'capability_id': 'cap', 'capability_hash': 'cap-hash'}}
    activation = {'id': 'a', 'epoch': 0}
    monkeypatch.setattr(workflow_graph_scope, '_frozen', lambda *_args: (
        scene.task, None, None, None, scene.task.data['graph_runtime'], activation, node))
    monkeypatch.setattr(workflow_graph_scope, '_job_matches', lambda *_args: scene.task.data['graph_jobs']['a'])
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *_args: binding)
    caps = {'cap': NS(id='cap', content_hash='cap-hash'),
        'sim': NS(id='sim', content_hash='sim-hash', data={'specification': {'simulation_only': True}})}
    monkeypatch.setattr(s, 'published', lambda _db, _p, identity: caps[identity])
    calls = []
    monkeypatch.setattr(tools, '_check_candidates', lambda *_args: calls.append('all-current-targets') or True)
    return binding, caps, calls


def test_saved_review_eligibility_still_checks_current_targets(scene, monkeypatch):
    s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    _, _, calls = checked_context(scene, monkeypatch)
    assert s.checked_selection(scene.db, scene.p, scene.task, scene.job,
        scene.inputs, scene.producer) == 'supported'
    assert calls == ['all-current-targets']
    assert scene.inputs['reasoning_result']['review_state'] == 'needs_information'


@pytest.mark.parametrize('change', ['production_binding', 'target_not_sim', 'target_string_true',
    'target_hash', 'binding_hash', 'cancel', 'input', 'producer', 'missing', 'marker'])
def test_review_cannot_survive_source_answer_target_or_cancel_drift(scene, monkeypatch, change):
    s.submit(scene.db, scene.p, scene.task, scene.job, scene.command)
    binding, caps, _ = checked_context(scene, monkeypatch)
    inputs = copy.deepcopy(scene.inputs); producer = copy.deepcopy(scene.producer)
    if change == 'production_binding': binding['simulation_only'] = False
    if change == 'target_not_sim': caps['sim'].data['specification']['simulation_only'] = False
    if change == 'target_string_true': caps['sim'].data['specification']['simulation_only'] = 'true'
    if change == 'target_hash': caps['sim'].content_hash = 'different'
    if change == 'binding_hash': binding['binding_hash'] = 'different'
    if change == 'cancel': scene.task.data['graph_runtime']['cancel_epoch'] += 1
    if change == 'input': scene.job.data = {**scene.job.data, 'invocation_key': 'new-key'}
    if change == 'producer': producer['capture_hash'] = 'other-answer'
    if change == 'missing': inputs['reasoning_result']['analysis']['candidate_assessments'][0]['missing_information'] = ['替代缺项']
    if change == 'marker': scene.job.data['simulation_review_v202'] = 'missing-audit-proof'
    with pytest.raises(HTTPException):
        s.checked_selection(scene.db, scene.p, scene.task, scene.job, inputs, producer)


@pytest.mark.parametrize('read_mode', [True, False])
def test_real_review_context_uses_advisory_locks_only_for_get(scene, monkeypatch, read_mode):
    from generic_agents import workflow_graph, workflow_graph_scope, worker, native_goals
    from generic_agents import business_flow_nested_adoption_v195 as adoption
    state = scene.task.data['graph_runtime']
    state.update(status='waiting', eligible_ability_ids=['a'])
    activation = {'id': 'a', 'status': 'ready', 'epoch': 0}
    node = {'id': 'node', 'actor_id': 'executor', 'execution': {'adapter': 'workflow_service',
        'capability_id': 'cap', 'capability_hash': 'cap-hash'}}
    binding = {'simulation_only': True, 'binding_hash': 'binding', 'translation': {}}
    cap = NS(id='cap', content_hash='cap-hash', data={'execution': {'operation': tools.OP}, 'dependencies': []})
    scene.task.data['native_goal_context'] = {'explicit_test_seam': True}
    calls = []
    monkeypatch.setattr(workflow_graph_scope, '_frozen', lambda *_args: (
        scene.task, None, None, None, state, activation, node))
    monkeypatch.setattr(workflow_graph_scope, '_job_matches', lambda *_args: scene.transport)
    monkeypatch.setattr(worker, 'actor', lambda *_args: scene.p)
    monkeypatch.setattr(workflow_graph, 'live', lambda *_args, **kw: calls.append(('live', kw)))
    monkeypatch.setattr(native_goals, 'guard', lambda *_args, **kw: calls.append(('goal', kw)))
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *_args: binding)
    monkeypatch.setattr(s, 'published', lambda *_args: cap)
    monkeypatch.setattr(tools, 'validate_manifest', lambda *_args: None)
    monkeypatch.setattr(tools, 'producer_integrity_pin', lambda *_args: {'candidates': []})
    monkeypatch.setattr(tools, '_check_candidates', lambda *_args: True)
    monkeypatch.setattr(adoption, 'service_delegations', lambda *_args: {'node': {
        'operation': tools.OP, 'service_actor': 'execution_service', 'agent_role': 'business', 'side_effect': True}})
    REAL_REVIEW_CONTEXT(scene.db, scene.p, scene.task, 'a', scene.job, read_mode=read_mode)
    expected = {'lock_sources': False} if read_mode else {}
    assert calls == [('live', expected), ('goal', expected)]


@pytest.mark.parametrize('lock_sources', [True, False])
def test_real_live_check_retains_all_verification_and_threads_lock_policy(monkeypatch, lock_sources):
    from generic_agents import workflow_graph as graph
    from generic_agents import layered_approval_store_v175 as approval, signal_graph_routing_v134 as routing
    from generic_agents import instance_semantic_runtime_dispatch as dispatch, entity_release_compat as entities
    from generic_agents import instance_compiler as compiler, workspace_api as workspace
    from generic_agents import workflow_images as images, workflow_scenes as scenes
    from generic_agents import workflow_audio as audio, workflow_geo_pairs as pairs
    task = NS(data={'instance_id': 'i', 'material_snapshots': []})
    release = NS(data={'snapshot': {}}, package_id='package')
    calls = []
    def recorder(name):
        return lambda *_args, **kw: calls.append((name, kw))
    monkeypatch.setattr(approval, 'guard', recorder('approval'))
    monkeypatch.setattr(routing, 'guard_current', recorder('routing'))
    monkeypatch.setattr(dispatch, 'modern', lambda *_args: NS(verify_release=recorder('release')))
    monkeypatch.setattr(graph, 'scoped', lambda *_args: NS(status='partially_active', data={'enabled_business_ids': ['b']}))
    monkeypatch.setattr(graph, 'published', recorder('package'))
    monkeypatch.setattr(entities, 'verify_task_objects', recorder('objects'))
    monkeypatch.setattr(compiler, 'verify_material_snapshots', recorder('materials'))
    monkeypatch.setattr(workspace, 'verify_perception_evidence', recorder('perception'))
    monkeypatch.setattr(images, 'verify_masks', recorder('image_masks'))
    monkeypatch.setattr(scenes, 'verify_masks', recorder('scene_masks'))
    monkeypatch.setattr(images, 'verify', recorder('images'))
    monkeypatch.setattr(audio, 'verify_receipts', recorder('audio'))
    monkeypatch.setattr(pairs, 'verify_receipts', recorder('pairs'))
    graph.live(None, None, task, release, {'id': 'b', 'name': 'Business'}, lock_sources=lock_sources)
    policy = {} if lock_sources else {'lock_sources': False}
    assert calls == [('approval', policy), ('routing', {} if lock_sources else {'lock': False}),
        ('release', {'business_name': 'Business', **({} if lock_sources else {'live': False})}),
        ('package', {}), ('objects', policy), ('materials', {'allow_archived': True}),
        ('perception', policy), ('image_masks', {}), ('scene_masks', {}), ('images', {}),
        ('audio', {}), ('pairs', {})]
