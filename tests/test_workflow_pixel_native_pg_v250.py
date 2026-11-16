"""Normal registration/Task/Invocation/native model/independent ACK vertical fixture.

The real prepared local checkpoint is required. Synthetic RGB originals prove
engineering behavior, never field quality; no model/Invocation/ACK substitute.
"""
import copy
import json
from types import SimpleNamespace
from uuid import uuid4
from contextlib import ExitStack
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import WorkflowPixelRun, Job, Invocation, Observation, Event, Memory, Membership, CloudCall
from generic_agents.worker import process_once, deliver_events
from generic_agents.security import Principal
from generic_agents.instance_compiler import signature_value
from generic_agents.contracts import digest
from tests.test_pixel_change_native_pg_v249 import native_scene, pytestmark
from tests.test_pixel_change_pg_v249 import create as create_reference, image
from tests.test_instance_workspace import configuration, prepared, object_row, complete
from tests.test_instance_workspace import save, OBJECTS
from tests.test_workflow_abilities import enable, run as run_ability


def good(response, status=200):
    assert response.status_code == status, response.text[:1000]
    return response.json()


def build_flow_scene(client, account, native_scene, *, expected_status='workflow_waiting'):
    sample = create_reference(native_scene)
    assert process_once(job_id=sample['job_id'], deliver=False)
    sample = good(client.get(native_scene.base + '/runs/' + sample['id']))
    assert sample['status'] == 'succeeded', sample['reason']
    registration_body = {'schema': 'workflow-pixel-register-v250', 'request_id': '登记/原号?250-' + uuid4().hex,
                         'version': sample['version']}
    registered = good(client.post(native_scene.base + '/runs/' + sample['id'] + '/register-business', json=registration_body))
    c = configuration(); flow = c['flows']['资料核验']
    flow['steps'][0].update(name='计算当次影像候选', agent='多模感知智能体', role='',
        execution={'capability_id': registered['capability']['id'], 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    c['automation']['policies']['资料核验']['rules']['observe'] = {'mode': '自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    release, _, check = prepared(client, c, instance_id=native_scene.instance['id'])
    assert release['available_business_ids'], check
    instance = enable(client, release); entity = object_row(client, release)
    a, raw_a = image(client, instance['id'], color=170)
    b, raw_b = image(client, instance['id'], color=220)
    assert {a['sha256'], b['sha256']}.isdisjoint({native_scene.before['sha256'], native_scene.after['sha256']})
    body = {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [entity['id']], 'title': '任务当次两图的本地候选核查', 'request_id': str(uuid4()),
        'pixel_pair': {'before_image_id': a['id'], 'after_image_id': b['id'],
            'pixel_correspondence_confirmed': True, 'full_image_present_confirmed': True}}
    base = '/api/workspace/instances/' + instance['id']
    task = good(client.post(base + '/tasks', json=body), 201)
    job_id = task['workflow_runtime']['automatic']['job_id']
    assert process_once(job_id=job_id, deliver=False)
    task = good(client.get('/api/workspace/tasks/' + task['id']))
    if expected_status is not None:assert task['status'] == expected_status, task.get('workflow_runtime')
    results = good(client.get('/api/workspace/tasks/' + task['id'] + '/pixel-results'))
    assert len(results['records']) == 1, results
    produced = results['records'][0]['run']; assert produced['status'] == 'succeeded', produced
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        p = Principal(member.user_id, member.workspace_id, member.role, 'workflow-pixel-test')
    return SimpleNamespace(client=client, p=p, workspace_id=account[2], instance_id=instance['id'], instance=instance,
        release=release, entity=entity, task_id=task['id'], task=task, run_id=produced['id'], job_id=job_id,
        base=base, body=body, source_before=a, source_after=b, raw_before=raw_a, raw_after=raw_b,
        sample=sample, registered=registered, registration_body=registration_body, resource=native_scene)


@pytest.fixture
def flow_scene(client, account, native_scene):
    return build_flow_scene(client, account, native_scene)


def ack(s):
    with Session(engine()) as db:
        observation = db.scalar(select(Observation).where(Observation.workspace_id == s.workspace_id,
            Observation.source_record_id.startswith('WorkflowPixelRun:' + s.run_id + ':')))
        assert observation is not None
        identity = observation.id
    deliver_events(workspace_id=s.workspace_id, observation_id=identity, event_kind='observation.data')
    return identity


def test_true_registration_fresh_task_inference_ack_and_independent_human_result(flow_scene):
    s = flow_scene; url = '/api/workspace/tasks/' + s.task_id + '/pixel-results'
    before = good(s.client.get(url)); entry = before['records'][0]
    assert entry['reception']['state'] == 'waiting_data' and not entry['data_values_available']
    assert entry['run']['sources']['before']['sha256'] == s.source_before['sha256']
    assert entry['run']['sources']['after']['sha256'] == s.source_after['sha256']
    assert s.task['image_snapshots'] == []
    assert complete(s.client, s.task).status_code == 409
    identity = ack(s)
    after = good(s.client.get(url)); entry = after['records'][0]
    assert entry['reception']['state'] == 'received' and entry['reception']['memory_id']
    assert entry['data_values_available'] and not entry['field_truth_verified']
    assert entry['reception']['receipt']['producer_status'] == 'succeeded'
    task = good(s.client.get('/api/workspace/tasks/' + s.task_id))
    finished = good(complete(s.client, task, note='已核对当次原图和候选；零候选不能认定现场无变化'))
    assert finished['status'] == 'workflow_completed'
    final = good(s.client.get(url))
    assert final['records'][0]['human_receipts'][0]['note'].startswith('已核对当次')
    with Session(engine()) as db:
        run = db.get(WorkflowPixelRun, s.run_id)
        assert run.job_id == s.job_id and run.data['sources']['before']['sha256'] == s.source_before['sha256']
        assert db.get(Invocation, run.invocation_id).status == 'succeeded'
        assert len(list(db.scalars(select(Invocation)))) == 1 and not list(db.scalars(select(CloudCall)))
        observation = db.get(Observation, identity)
        event = db.scalar(select(Event).where(Event.workspace_id == s.workspace_id, Event.idempotency_key == 'data:' + identity))
        assert observation.observed_at is None and event.status == 'received'
        assert len(list(db.scalars(select(Memory).where(Memory.workspace_id == s.workspace_id,
            Memory.data['event_id'].as_string() == event.id, Memory.data['type'].as_string() == 'observation_evidence')))) == 1


def test_original_registration_recovery_and_empty_task_input_hash_compatibility(flow_scene):
    s = flow_scene
    response = good(s.client.get(s.resource.base + '/registrations/requests', params={
        'request_id': s.registration_body['request_id'], 'expected_body_hash': digest(s.registration_body)}))
    assert response['found'] and response['result'] == s.registered
    from generic_agents.workspace_api import NewTask, creation_body_hash
    legacy = {k: v for k, v in s.body.items() if k != 'pixel_pair'}
    assert creation_body_hash(NewTask.model_validate(legacy)) == creation_body_hash(NewTask.model_validate({**legacy, 'pixel_pair': None}))
    assert creation_body_hash(NewTask.model_validate(s.body)) != creation_body_hash(NewTask.model_validate(legacy))
    assert s.client.post(s.resource.base + '/runs/' + s.sample['id'] + '/register-business',
        json={**s.registration_body, 'version': s.registration_body['version'] + 1}).status_code == 409


def test_real_finalize_failure_has_explicit_local_settlement_without_new_inference(client, account, native_scene, monkeypatch):
    import generic_agents.native_pixel_receipts_v250 as reception
    original_emit = reception.emit_completed
    def fail_before_handoff(*args, **kwargs):
        raise ValueError('显式测试故障：模型与步骤已保存，但父任务终结前交接失败')
    with monkeypatch.context() as patch:
        patch.setattr(reception, 'emit_completed', fail_before_handoff)
        s = build_flow_scene(client, account, native_scene)
    assert reception.emit_completed is original_emit
    path = '/api/workspace/tasks/' + s.task_id + '/pixel-results'
    entry = good(client.get(path))['records'][0]
    assert entry['run']['status'] == 'succeeded' and entry['reception']['state'] == 'producer_failed'
    assert entry['run']['settlement_action']['can_settle']
    with Session(engine()) as db:
        job = db.get(Job, s.job_id)
        assert job.status == 'failed'
        prior_job = (job.status, job.attempts, digest(job.data))
        counts = (len(list(db.scalars(select(Invocation)))), len(list(db.scalars(select(WorkflowPixelRun)))), len(list(db.scalars(select(Job)))))
    task = good(client.get('/api/workspace/tasks/' + s.task_id))
    command = {'schema': 'workflow-pixel-settlement-v250', 'request_id': '交接恢复/原号?250-' + uuid4().hex, 'version': task['version']}
    response = good(client.post(path + '/' + s.run_id + '/settle', json=command))
    assert response['settlement']['job_status'] == 'failed'
    assert good(client.post(path + '/' + s.run_id + '/settle', json=command)) == response
    assert good(client.get(path + '/settlement-requests', params={'request_id': command['request_id'],
        'expected_body_hash': digest(command)}))['result'] == response
    with Session(engine()) as db:
        job = db.get(Job, s.job_id)
        assert (job.status, job.attempts, digest(job.data)) == prior_job
        assert (len(list(db.scalars(select(Invocation)))), len(list(db.scalars(select(WorkflowPixelRun)))), len(list(db.scalars(select(Job))))) == counts
    assert good(client.get(path))['records'][0]['reception']['state'] == 'waiting_data'
    ack(s)
    received = good(client.get(path))['records'][0]['reception']
    assert received['state'] == 'received' and received['receipt']['producer_status'] == 'failed'
    assert received['receipt']['settlement'] == response['settlement']
    current = good(client.get('/api/workspace/tasks/' + s.task_id))
    assert good(complete(client, current))['status'] == 'workflow_completed'


def test_saved_model_without_step_completion_requires_real_new_invocation_on_explicit_retry(client, account, native_scene, monkeypatch):
    from fastapi import HTTPException
    import generic_agents.workflow_pixel_runs_v250 as runtime
    def stop_before_step_receipt(*args, **kwargs):
        raise HTTPException(409, '显式测试故障：模型已成功，但业务步骤原完成依据尚未保存')
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'local_result', stop_before_step_receipt)
        s = build_flow_scene(client, account, native_scene, expected_status=None)
    path = '/api/workspace/tasks/' + s.task_id
    before = good(client.get(path + '/pixel-results'))['records'][0]
    assert before['run']['status'] == 'succeeded'
    assert not before['run']['settlement_action']['can_settle']
    task = good(client.get(path))
    response = good(run_ability(client, task, retry=True))
    second_job = response['workflow_runtime']['automatic']['job_id']
    assert second_job != s.job_id and process_once(job_id=second_job, deliver=False)
    results = good(client.get(path + '/pixel-results'))['records']
    assert len(results) == 2
    current = next(v for v in results if v['run']['job_id'] == second_job)
    assert current['run']['id'] != s.run_id and current['run']['invocation_id'] != before['run']['invocation_id']
    assert current['reception']['state'] == 'waiting_data'
    with Session(engine()) as db:
        assert db.get(Job, s.job_id).status == 'failed'
        obs = db.scalar(select(Observation).where(Observation.source_record_id.startswith('WorkflowPixelRun:' + current['run']['id'] + ':')))
        identity = obs.id
    deliver_events(workspace_id=s.workspace_id, observation_id=identity, event_kind='observation.data')
    task = good(client.get(path))
    assert good(complete(client, task))['status'] == 'workflow_completed'


@pytest.fixture
def managed_scene(flow_scene):
    """Independent uploader/owner, executor, three approvers, human and acceptor."""
    from fastapi.testclient import TestClient
    s = flow_scene
    with ExitStack() as stack:
        def user(label):
            username, password = label + '_' + uuid4().hex[:12], uuid4().hex
            actor = good(s.client.post('/api/users', json={'username': username, 'password': password, 'role': 'operator'}), 201)
            view = stack.enter_context(TestClient(s.client.app))
            session = good(view.post('/api/login', json={'username': username, 'password': password}))
            view.headers.update({'X-CSRF-Token': session['csrf_token'], 'X-Workspace-ID': s.workspace_id})
            return actor, view
        executor, executor_client = user('pixel_executor')
        reviewer, reviewer_client = user('pixel_human')
        reviewers = [user('pixel_approver') for _ in range(3)]
        acceptor, acceptor_client = user('pixel_acceptor')
        c = configuration(); flow = c['flows']['资料核验']
        flow['steps'][0].update(name='分析任务当次像素候选', agent='多模感知智能体', role='',
            execution={'capability_id': s.registered['capability']['id'], 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
        c['automation']['policies']['资料核验']['rules']['observe'] = {'mode': '自动运行'}
        flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
        revision = save(s.client, c, review_automation=True)
        generation = {'revision': revision, 'objects': OBJECTS, 'role_bindings': {'核验负责人': reviewer['id']}}
        checked = good(s.client.post('/api/instances/check', json=generation))
        release = good(s.client.post('/api/instances', json={**generation, 'instance_id': s.instance_id,
            'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
        assert release['id'] != s.release['id']
        enable(s.client, release); entity = object_row(s.client, release)
        base = '/api/workspace/approval-settings-v175'; unit = str(uuid4())
        good(s.client.post(base + '/units', json={'request_id': str(uuid4()), 'unit_id': unit,
            'expected_version': 0, 'name': '像素成果三级独立核对', 'parent_id': None, 'enabled': True}))
        roles = [(a['id'], 'review_' + str(i)) for i, (a, _) in enumerate(reviewers)]
        roles += [(executor['id'], 'execute'), (reviewer['id'], 'execute'), (acceptor['id'], 'accept')]
        for actor_id, role in roles:
            good(s.client.post(base + '/roles', json={'request_id': str(uuid4()), 'grant_id': str(uuid4()),
                'expected_version': 0, 'unit_id': unit, 'user_id': actor_id, 'role_key': role, 'enabled': True}))
        stage = lambda name, role: {'name': name, 'role_key': role, 'scope_id': unit}
        good(s.client.post(base + '/policies', json={'request_id': str(uuid4()), 'instance_id': s.instance_id,
            'business_id': release['available_business_ids'][0], 'expected_version': 0, 'name': '三级核对后明确执行与独立验收',
            'scope_id': unit, 'stages': [stage('第' + str(i + 1) + '级', 'review_' + str(i)) for i in range(3)],
            'executor': stage('明确启动', 'execute'), 'acceptance': stage('独立验收', 'accept'), 'enabled': True}))
        body = {**s.body, 'release_id': release['id'], 'business_id': release['available_business_ids'][0],
            'entity_ids': [entity['id']], 'request_id': str(uuid4())}
        task = good(s.client.post(s.base + '/tasks', json=body), 201)
        assert task['status'] == 'approval_draft'
        yield SimpleNamespace(**{**vars(s), 'release': release, 'entity': entity, 'task_id': task['id'], 'task': task,
            'body': body, 'executor': executor, 'executor_client': executor_client, 'reviewer': reviewer,
            'reviewer_client': reviewer_client, 'reviewers': reviewers, 'acceptor': acceptor, 'acceptor_client': acceptor_client})


def approval_command(client, path, action, **fields):
    current = good(client.get(path))
    if action == 'decide':fields.setdefault('submission_hash', current['submission_hash'])
    if action == 'accept':
        assert current['acceptance_basis'] is not None, current
        fields.setdefault('basis_hash', current['acceptance_basis']['basis_hash'])
        fields.setdefault('decision', 'accept')
    return good(client.post(path + '/' + action, json={'request_id': str(uuid4()),
        'expected_version': current['version'], 'reason': '核对原图和本次业务范围后明确处理', **fields}))


def approved_start(s):
    path = '/api/workspace/tasks/' + s.task_id + '/approval-v175'
    approval_command(s.client, path, 'submit')
    for _, client in s.reviewers:
        approval_command(client, path, 'decide', decision='approve')
    approval_command(s.executor_client, path, 'start')
    return path


def test_multi_actor_three_approvals_originals_before_execution_and_independent_acceptance(managed_scene):
    s = managed_scene; path = '/api/workspace/tasks/' + s.task_id
    for _, viewer in s.reviewers:
        assert good(viewer.get(path + '/pixel-results'))['records'] == []
        assert viewer.get(path + '/pixel-inputs/before').content == s.raw_before
        assert viewer.get(path + '/pixel-inputs/after').content == s.raw_after
    approval = approved_start(s)
    task = good(s.executor_client.get(path)); job_id = task['workflow_runtime']['automatic']['job_id']
    assert process_once(job_id=job_id, deliver=False)
    entry = good(s.reviewer_client.get(path + '/pixel-results'))['records'][0]
    run_id = entry['run']['id']
    with Session(engine()) as db:
        run = db.get(WorkflowPixelRun, run_id)
        assert run.owner_id == s.executor['id'] != db.get(Job, s.sample['job_id']).data['actor_id']
        assert db.get(Job, job_id).data['actor_id'] == s.executor['id']
        obs = db.scalar(select(Observation).where(Observation.source_record_id.startswith('WorkflowPixelRun:' + run_id + ':')))
        observation_id = obs.id
    current = good(s.reviewer_client.get(path))
    assert complete(s.reviewer_client, current).status_code == 409
    deliver_events(workspace_id=s.workspace_id, observation_id=observation_id, event_kind='observation.data')
    current = good(s.reviewer_client.get(path))
    assert good(complete(s.reviewer_client, current, note='独立核对两张原图和机器候选；现场是否变化仍需专业核查'))['status'] == 'workflow_completed'
    accepted = approval_command(s.acceptor_client, approval, 'accept')
    assert accepted['status'] == 'accepted'
    final = good(s.acceptor_client.get(path + '/pixel-results'))['records'][0]
    assert final['human_receipts'][0]['actor_id'] == s.reviewer['id']
    assert final['reception']['receipt']['source_time_verified'] is False


def test_approval_context_rejects_replaced_ordered_pair_without_forging_a_new_signature(managed_scene):
    from generic_agents.models import Task
    s = managed_scene; path = '/api/workspace/tasks/' + s.task_id + '/approval-v175'
    approval_command(s.client, path, 'submit')
    for _, client in s.reviewers:
        approval_command(client, path, 'decide', decision='approve')
    with Session(engine()) as db:
        task = db.get(Task, s.task_id); value = copy.deepcopy(task.data); pair = value['pixel_pair_snapshot']
        pair['before'], pair['after'] = pair['after'], pair['before']
        pair['pair_hash'] = digest({k: v for k, v in pair.items() if k != 'pair_hash'})
        task.data = value; db.commit()
    # Explicit storage-corruption negative; the original V175 context/Audits
    # remain untouched. This test never repairs or claims a successful start.
    current = s.executor_client.get(path)
    if current.status_code == 200:
        response = s.executor_client.post(path + '/start', json={'request_id': str(uuid4()),
            'expected_version': current.json()['version'], 'reason': '验证原审批不能采用替换后的像素原图'})
        assert response.status_code == 409, response.text
    else:
        assert current.status_code == 409, current.text
