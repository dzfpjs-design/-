"""Real-PG F11 regression cases; the root runner supplies an isolated database.

Reported numeric values and the two human workflows are synthetic test inputs.
Accounts, login, configuration review, compilation, activation, observations,
Tasks and receipts use the production paths.  This file never starts a service.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.models import CloudCall, Entity, Job, Membership, Observation, PerceptionSignal, Task
from generic_agents.worker import process_once
from tests.test_instance_workspace import OBJECTS, configuration, enable, object_row, save
from tests.test_vertical_slice import manifest, publish


def routing_path(release):
    return '/api/workspace/instances/' + release['instance_id'] + '/signal-routing'


def handoff_path(identity):
    return '/api/workspace/signal-handoffs/' + identity


def require_reply(reply, status=200):
    assert reply.status_code == status, reply.text
    return reply.json()


@pytest.fixture
def dual_scene(client, account, request):
    """Two real operators, two reviewed businesses, one genuinely published rule."""
    members = {}
    credentials = {}
    for slot in ('response', 'verification'):
        username = 'dual_' + slot + '_' + uuid4().hex[:12]
        password = 'Synthetic-only-' + uuid4().hex
        members[slot] = require_reply(client.post('/api/users', json={
            'username': username, 'password': password, 'role': 'operator'}), 201)
        credentials[slot] = (username, password)

    config = configuration()
    template = copy.deepcopy(config['flows']['资料核验'])
    names = {'response': '异常响应登记', 'verification': '独立异常核验'}
    roles = {'response': '响应责任人', 'verification': '核验责任人'}
    config['businesses'] = list(names.values())
    config['flows'] = {}
    config['automation'] = {'version': 1, 'policies': {}}
    for slot, name in names.items():
        flow = copy.deepcopy(template)
        flow['output'] = '响应处理记录' if slot == 'response' else '独立核验结论'
        for step in flow['steps']:
            step['role'] = roles[slot]
            step['purpose'] = ('记录应用内人工响应，不判定现场异常' if slot == 'response'
                               else '对照原依据独立核验，允许尚不能判断')
        config['flows'][name] = flow
        config['automation']['policies'][name] = {'rules': {'write': {'mode': '仅给出建议'}}}
    for flow in config['flows'].values():
        flow['confirmedSignature'] = json.dumps(signature_value(config, flow), ensure_ascii=False)

    revision = save(client, config, review_automation=True)
    compilation = {'revision': revision, 'objects': OBJECTS,
                   'role_bindings': {roles[slot]: members[slot]['id'] for slot in roles}}
    checked = require_reply(client.post('/api/instances/check', json=compilation))
    release = require_reply(client.post('/api/instances', json={**compilation,
        'confirmed_configuration_hash': checked['configuration_hash'],
        'confirm_configuration': True}), 201)
    assert len(release['available_business_ids']) == 2
    assert {release['snapshot']['roles'][role]['user_id'] for role in roles.values()} == {
        members['response']['id'], members['verification']['id']}
    enable(client, release)
    obj = object_row(client, release)
    source = publish(client, manifest('source', name='dual_reported_numeric_source',
        allowed_agent_roles=['perception'], execution={'adapter': 'upload'},
        specification={'timezone': 'Asia/Shanghai'}, data_policy={'cloud_allowed': False}))
    extractor = publish(client, manifest('tool', name='dual_local_numeric_rule',
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': [{'id': 'high', 'field': '公开登记值', 'operator': 'gt', 'threshold': 50}]},
        data_policy={'cloud_allowed': False}))
    options = require_reply(client.get(routing_path(release) + '/dual-options'))
    choices = {item['name']: item for item in options['businesses']}
    for slot in roles:
        assert choices[names[slot]]['recipient']['user_id'] == members[slot]['id']
    command = {'request_id': str(uuid4()), 'expected_version': options['instance_version'],
        'options_hash': options['options_hash'], 'release_id': release['id'],
        'source_id': source['id'], 'extractor_id': extractor['id'], 'entity_ids': [obj['id']],
        'response_business_id': choices[names['response']]['id'],
        'verification_business_id': choices[names['verification']]['id'],
        'max_tasks': getattr(request, 'param', 10),
        'reason': '仅为后续本地登记规则候选创建独立人工响应和核验待办',
        'confirm_human_review_only': True}
    enabled = require_reply(client.post(routing_path(release) + '/enable-dual', json=command), 201)
    with ExitStack() as stack:
        actors = {}
        from generic_agents.api import app
        for slot, (username, password) in credentials.items():
            actor = stack.enter_context(TestClient(app))
            login = require_reply(actor.post('/api/login', json={'username': username, 'password': password}))
            actor.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': account[2]})
            actors[slot] = actor
        yield {'admin': client, 'workspace_id': account[2], 'release': release, 'object': obj,
            'source': source, 'extractor': extractor, 'members': members, 'actors': actors,
            'command': command, 'enabled': enabled}


def ingest_observation(scene, *, record=None, processing_version=None):
    clock = datetime.now(timezone.utc) - timedelta(seconds=10)
    body = {'source_id': scene['source']['id'], 'source_record_id': record or str(uuid4()),
        'entity_id': scene['object']['id'], 'extractor_id': scene['extractor']['id'],
        'attributes': {'公开登记值': 65}, 'observed_at': clock.isoformat(),
        'business_day': str((clock + timedelta(hours=8)).date()), 'startup_mode': 'manual'}
    if processing_version is not None:
        body['processing_version'] = processing_version
    observation = require_reply(scene['admin'].post('/api/workspace/instances/' +
        scene['release']['instance_id'] + '/reported-observations', json=body), 201)
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.data['observation_id'].as_string() == observation['id']))
        assert job is not None and job.kind == 'process_reported_observation'
        job_id = job.id
    assert process_once(job_id, kinds=['process_reported_observation'], deliver=False)
    return observation, body


def handoffs(scene):
    listing = require_reply(scene['admin'].get('/api/workspace/instances/' +
        scene['release']['instance_id'] + '/signal-handoffs?mine_only=false'))
    return listing['items']


def receive_parent(scene, *, record=None, processing_version=None):
    observation, body = ingest_observation(scene, record=record, processing_version=processing_version)
    listing = {'items': handoffs(scene)}
    parent = next(item for item in listing['items'] if item['source']['source_record_id'] == body['source_record_id'])
    return observation, body, parent


def dispatch_slot(scene, parent, slot):
    from generic_agents.signal_dual_handoffs import dispatch_slot as dispatch
    with Session(engine()) as db:
        dispatch(db, scene['workspace_id'], parent['id'], slot)
        db.commit()
    return require_reply(scene['admin'].get(handoff_path(parent['id'])))


def task_detail(scene, parent, slot, actor=None):
    who = actor or scene['actors'][slot]
    return require_reply(who.get('/api/workspace/tasks/' + parent['slots'][slot]['task_id']))


def complete_step(actor, task, *, decision=None, request_id=None):
    body = {'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
        'note': '已保存应用内人工处理记录；不证明现场物理异常', 'request_id': request_id or str(uuid4())}
    if decision is not None:
        body['perception_decision'] = decision
    return actor.post('/api/workspace/tasks/' + task['id'] + '/complete-step', json=body)


def task_ids(scene):
    with Session(engine()) as db:
        return set(db.scalars(select(Task.id).where(Task.workspace_id == scene['workspace_id'],
            Task.data['instance_id'].as_string() == scene['release']['instance_id'])))


def current_route(scene):
    reply = require_reply(scene['admin'].get(routing_path(scene['release']) + '/dual'))
    route_id = scene['enabled']['receipt']['route_id']
    return reply, next(row for row in reply['routes'] if row['id'] == route_id)


def pause_route(scene):
    listing, route = current_route(scene)
    body = {'request_id': str(uuid4()), 'expected_version': listing['instance_version'],
            'reason': '明确暂停未建槽，不影响已经保存的人工作业'}
    url = routing_path(scene['release']) + '/dual/' + route['id'] + '/pause'
    return require_reply(scene['admin'].post(url, json=body)), body, url


def ack_body(parent, task):
    return {'request_id': str(uuid4()), 'expected_task_version': task['version'],
        'parent_hash': parent['immutable_hash'], 'note': '本人已在应用内接收，现场事实仍待核验',
        'override_reason': ''}


def test_real_recipients_acknowledge_and_response_finishes_without_verification(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    assert parent['slots']['response']['state'] == parent['slots']['verification']['state'] == 'pending'
    parent = dispatch_slot(scene, parent, 'response')
    assert parent['slots']['response']['state'] == 'created'
    assert parent['slots']['verification']['state'] == 'pending'
    assert not parent['source']['source_time_verified']
    recipient = scene['actors']['response']
    inbox = require_reply(recipient.get('/api/workspace/instances/' +
        scene['release']['instance_id'] + '/signal-handoffs?mine_only=true'))
    assert parent['id'] in {item['id'] for item in inbox['items']}
    task = task_detail(scene, parent, 'response')
    assert task['permissions']['can_save_note']
    assert complete_step(scene['actors']['verification'], task).status_code == 403
    assert complete_step(recipient, task, decision='abnormal').status_code == 422
    task = require_reply(complete_step(recipient, task))
    assert complete_step(recipient, task).status_code == 409
    ack = {'request_id': str(uuid4()), 'expected_task_version': task['version'],
        'parent_hash': parent['immutable_hash'], 'note': '责任人本人已在应用内接收，核验尚未完成',
        'override_reason': ''}
    url = handoff_path(parent['id']) + '/response/ack'
    assert scene['actors']['verification'].post(url, json=ack).status_code == 403
    accepted = require_reply(recipient.post(url, json=ack))
    assert accepted['receipt']['ack_actor_id'] == scene['members']['response']['id']
    assert accepted['handoff']['slots']['response']['ack']['by_recipient'] is True
    assert require_reply(recipient.post(url, json=ack))['receipt'] == accepted['receipt']
    restored = require_reply(recipient.get(url + '-requests/' + ack['request_id']))
    assert restored['receipt'] == accepted['receipt']
    assert recipient.post(url, json={**ack, 'note': '同nonce替换正文'}).status_code == 409
    task = task_detail(scene, accepted['handoff'], 'response')
    finished = require_reply(complete_step(recipient, task))
    assert finished['status'] == 'workflow_completed'
    parent = require_reply(scene['admin'].get(handoff_path(parent['id'])))
    assert parent['slots']['verification']['state'] == 'pending'
    assert parent['slots']['verification']['result'] is None
    with Session(engine()) as db:
        signal = db.get(PerceptionSignal, parent['signal_id'])
        assert not signal.data.get('business_review')
        before_entity = copy.deepcopy(db.get(Entity, scene['object']['id']).data)
        before_version = db.get(Entity, scene['object']['id']).version
        assert before_version == scene['object']['version']
    parent = dispatch_slot(scene, parent, 'verification')
    verify_task = task_detail(scene, parent, 'verification')
    verify_task = require_reply(complete_step(scene['actors']['verification'], verify_task))
    verify_task = require_reply(complete_step(scene['actors']['verification'], verify_task, decision='uncertain'))
    assert verify_task['status'] == 'workflow_completed'
    parent = require_reply(scene['admin'].get(handoff_path(parent['id'])))
    assert parent['slots']['verification']['result']['decision'] == 'uncertain'
    assert parent['slots']['response']['ack'] == accepted['handoff']['slots']['response']['ack']
    with Session(engine()) as db:
        entity = db.get(Entity, scene['object']['id'])
        assert (entity.version, entity.data) == (before_version, before_entity)
        assert not list(db.scalars(select(CloudCall.id)))


def test_revoked_verification_recipient_does_not_block_independent_response(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == scene['workspace_id'],
            Membership.user_id == scene['members']['verification']['id']))
        membership.role = 'viewer'
        db.commit()
    parent = dispatch_slot(scene, parent, 'response')
    response_id = parent['slots']['response']['task_id']
    assert parent['slots']['response']['state'] == 'created' and response_id
    parent = dispatch_slot(scene, parent, 'verification')
    assert parent['slots']['verification']['state'] == 'blocked'
    assert parent['slots']['verification']['task_id'] is None
    assert parent['slots']['response']['task_id'] == response_id
    assert task_detail(scene, parent, 'response')['permissions']['can_save_note']
    assert task_ids(scene) == {response_id}


def test_response_commit_survives_new_session_recovery_without_duplicate_task(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    parent = dispatch_slot(scene, parent, 'response')
    response_id = parent['slots']['response']['task_id']
    assert response_id and task_ids(scene) == {response_id}
    # dispatch_slot has committed and closed its Session. A later independent
    # transaction recovers only the pending sibling; no process-memory parent.
    parent = require_reply(scene['admin'].get(handoff_path(parent['id'])))
    parent = dispatch_slot(scene, parent, 'verification')
    verification_id = parent['slots']['verification']['task_id']
    assert verification_id and verification_id != response_id
    for slot in ('response', 'verification'):
        parent = dispatch_slot(scene, parent, slot)
    assert task_ids(scene) == {response_id, verification_id}
    response = task_detail(scene, parent, 'response', scene['admin'])
    require_reply(scene['admin'].post('/api/workspace/tasks/' + response_id + '/cancel', json={
        'version': response['version'], 'reason': '明确取消原响应任务，不派替代任务', 'request_id': str(uuid4())}))
    dispatch_slot(scene, parent, 'response')
    assert task_ids(scene) == {response_id, verification_id}


@pytest.mark.parametrize('dual_scene', [2], indirect=True)
def test_two_slot_quota_is_reserved_before_creation_and_cancel_does_not_refund(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    _, route = current_route(scene)
    assert route['max_tasks'] == route['reserved_tasks'] == 2
    assert route['created_tasks'] == 0 and not task_ids(scene)
    parent = dispatch_slot(scene, parent, 'response')
    response_id = parent['slots']['response']['task_id']
    task = task_detail(scene, parent, 'response', scene['admin'])
    require_reply(scene['admin'].post('/api/workspace/tasks/' + response_id + '/cancel', json={
        'version': task['version'], 'request_id': str(uuid4()), 'reason': '停止这项响应，不回收成新来源名额'}))
    ingest_observation(scene)
    assert len(handoffs(scene)) == 1
    _, route = current_route(scene)
    assert route['reserved_tasks'] == 2 and route['created_tasks'] == 1
    dispatch_slot(scene, parent, 'response')
    assert task_ids(scene) == {response_id}
    parent = dispatch_slot(scene, parent, 'verification')
    assert parent['slots']['verification']['task_id'] not in {None, response_id}
    _, route = current_route(scene)
    assert route['reserved_tasks'] == route['created_tasks'] == 2


def test_pause_blocks_only_uncreated_slot_and_preserves_created_response(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    parent = dispatch_slot(scene, parent, 'response')
    response_id = parent['slots']['response']['task_id']
    accepted, command, url = pause_route(scene)
    assert require_reply(scene['admin'].post(url, json=command))['receipt'] == accepted['receipt']
    assert scene['admin'].post(url, json={**command, 'reason': '同nonce替换暂停原因'}).status_code == 409
    parent = dispatch_slot(scene, parent, 'verification')
    assert parent['slots']['verification']['state'] == 'blocked'
    assert parent['slots']['verification']['task_id'] is None
    task = task_detail(scene, parent, 'response')
    ack = require_reply(scene['actors']['response'].post(handoff_path(parent['id']) +
        '/response/ack', json=ack_body(parent, task)))
    assert ack['receipt']['task_id'] == response_id
    task = task_detail(scene, ack['handoff'], 'response')
    require_reply(complete_step(scene['actors']['response'], task))
    ingest_observation(scene)
    assert len(handoffs(scene)) == 1 and task_ids(scene) == {response_id}
    _, route = current_route(scene)
    assert route['reserved_tasks'] == 2


def test_same_raw_source_reprocessing_reuses_parent_without_another_reservation(dual_scene):
    scene = dual_scene
    _, body, parent = receive_parent(scene)
    parent = dispatch_slot(scene, parent, 'response')
    parent = dispatch_slot(scene, parent, 'verification')
    before_ids = task_ids(scene)
    _, repeated_body = ingest_observation(scene, record=body['source_record_id'],
        processing_version='dual-reprocessing-v2')
    assert repeated_body['source_record_id'] == body['source_record_id']
    assert [row['id'] for row in handoffs(scene)] == [parent['id']]
    for slot in ('response', 'verification'):
        dispatch_slot(scene, parent, slot)
    _, route = current_route(scene)
    assert route['reserved_tasks'] == route['created_tasks'] == 2
    assert task_ids(scene) == before_ids and len(before_ids) == 2


def test_registration_before_new_grant_cannot_be_adopted_by_reprocessing(dual_scene):
    scene = dual_scene
    pause_route(scene)
    _, old = ingest_observation(scene)
    assert not handoffs(scene)
    options = require_reply(scene['admin'].get(routing_path(scene['release']) + '/dual-options'))
    command = {**scene['command'], 'request_id': str(uuid4()),
        'expected_version': options['instance_version'], 'options_hash': options['options_hash']}
    require_reply(scene['admin'].post(routing_path(scene['release']) + '/enable-dual', json=command), 201)
    ingest_observation(scene, record=old['source_record_id'], processing_version='after-grant-reprocessing-v2')
    assert not handoffs(scene) and not task_ids(scene)


def test_ack_cas_nonce_original_actor_and_revocation_are_separate(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    parent = dispatch_slot(scene, parent, 'response')
    task = task_detail(scene, parent, 'response')
    actor = scene['actors']['response']
    url = handoff_path(parent['id']) + '/response/ack'
    body = ack_body(parent, task)
    assert actor.post(url, json={**body, 'expected_task_version': task['version'] + 1}).status_code == 409
    assert actor.post(url, json={**body, 'parent_hash': '0' * 64}).status_code == 409
    assert scene['admin'].post(url, json=body).status_code == 403
    accepted = require_reply(actor.post(url, json=body))
    assert scene['actors']['verification'].get(url + '-requests/' + body['request_id']).status_code == 404
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == scene['workspace_id'],
            Membership.user_id == scene['members']['response']['id']))
        member.role = 'viewer'
        db.commit()
    # A valid authenticated original actor may recover the immutable accepted
    # receipt even after losing write authority; a new action remains forbidden.
    assert require_reply(actor.get(url + '-requests/' + body['request_id']))['receipt'] == accepted['receipt']
    assert require_reply(actor.post(url, json=body))['receipt'] == accepted['receipt']
    assert actor.post(url, json={**body, 'note': '改变已接受正文'}).status_code == 409
    assert actor.post(url, json={**body, 'request_id': str(uuid4())}).status_code == 403


def test_admin_ack_is_labeled_as_override_not_recipient_receipt(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    parent = dispatch_slot(scene, parent, 'response')
    task = task_detail(scene, parent, 'response', scene['admin'])
    body = {**ack_body(parent, task), 'override_reason': '责任人暂未上线，管理员明确代收并保留区别'}
    accepted = require_reply(scene['admin'].post(handoff_path(parent['id']) + '/response/ack', json=body))
    ack = accepted['handoff']['slots']['response']['ack']
    assert ack['by_recipient'] is False
    assert ack['actor_id'] != scene['members']['response']['id']
    assert ack['override_reason'] == body['override_reason']


def test_erased_task_marker_cannot_downgrade_to_legacy_on_any_task_mutation(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    parent = dispatch_slot(scene, parent, 'response')
    task = task_detail(scene, parent, 'response')
    with Session(engine()) as db:
        row = db.get(Task, task['id'])
        assert row.data['dual_handoff']['handoff_id'] == parent['id']
        row.data = {key: copy.deepcopy(value) for key, value in row.data.items() if key != 'dual_handoff'}
        tampered_data = copy.deepcopy(row.data)
        row_version = row.version
        db.commit()
    actor = scene['actors']['response']
    url = '/api/workspace/tasks/' + task['id']
    assert actor.get(url).status_code == 409
    assert actor.put(url + '/note', json={'version': task['version'],
        'step_id': task['workflow_runtime']['current_step_id'], 'note': '不得把坏标记降级成无来源手工任务'}).status_code == 409
    assert complete_step(actor, task).status_code == 409
    assert scene['admin'].post(url + '/cancel', json={'version': task['version'],
        'reason': '损坏来源不得绕过校验', 'request_id': str(uuid4())}).status_code == 409
    with Session(engine()) as db:
        from generic_agents.signal_dual_models import SignalDualHandoff
        row = db.get(Task, task['id'])
        assert row.version == row_version and row.data == tampered_data
        assert db.get(SignalDualHandoff, parent['id']).response_task_id == task['id']


def test_valid_marker_from_another_parent_is_rejected_by_sql_backreference(dual_scene):
    scene = dual_scene
    _, _, first = receive_parent(scene)
    first = dispatch_slot(scene, first, 'response')
    _, _, second = receive_parent(scene)
    second = dispatch_slot(scene, second, 'response')
    first_task_id = first['slots']['response']['task_id']
    second_task_id = second['slots']['response']['task_id']
    with Session(engine()) as db:
        original = db.get(Task, first_task_id)
        other = db.get(Task, second_task_id)
        original.data = {**copy.deepcopy(original.data), 'dual_handoff': copy.deepcopy(other.data['dual_handoff'])}
        db.commit()
    assert scene['actors']['response'].get('/api/workspace/tasks/' + first_task_id).status_code == 409
    assert scene['actors']['response'].get('/api/workspace/tasks/' + second_task_id).status_code == 200
    assert task_ids(scene) == {first_task_id, second_task_id}


def test_original_enable_receipt_is_recovered_without_resigning_changed_body(dual_scene):
    scene = dual_scene
    url = routing_path(scene['release'])
    command = scene['command']
    receipt = scene['enabled']['receipt']
    _, _, parent = receive_parent(scene)
    restored = require_reply(scene['admin'].get(url + '/dual/requests/' + command['request_id']))
    assert restored['receipt'] == receipt
    assert require_reply(scene['admin'].post(url + '/enable-dual', json=command), 201)['receipt'] == receipt
    assert scene['admin'].post(url + '/enable-dual', json={**command,
        'response_business_id': command['verification_business_id'],
        'verification_business_id': command['response_business_id']}).status_code == 409
    assert scene['actors']['response'].get(url + '/dual/requests/' + command['request_id']).status_code == 404
    assert [row['id'] for row in handoffs(scene)] == [parent['id']]


def test_changed_original_evidence_blocks_both_uncreated_slots_without_fake_tasks(dual_scene):
    scene = dual_scene
    observation, _, parent = receive_parent(scene)
    with Session(engine()) as db:
        row = db.get(Observation, observation['id'])
        row.data = {**copy.deepcopy(row.data), 'input_hash': '0' * 64}
        db.commit()
    for slot in ('response', 'verification'):
        parent = dispatch_slot(scene, parent, slot)
        assert parent['slots'][slot]['state'] == 'blocked'
        assert parent['slots'][slot]['task_id'] is None
        assert parent['slots'][slot]['result'] is None
    assert not task_ids(scene)
    _, route = current_route(scene)
    assert route['reserved_tasks'] == 2 and route['created_tasks'] == 0
    with Session(engine()) as db:
        assert db.get(Entity, scene['object']['id']).version == scene['object']['version']
        assert not list(db.scalars(select(CloudCall.id)))


def test_damaged_verification_marker_does_not_block_response_detail_ack_or_completion(dual_scene):
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    for slot in ('response', 'verification'):
        parent = dispatch_slot(scene, parent, slot)
    verification_id = parent['slots']['verification']['task_id']
    original_creation_receipt = copy.deepcopy(parent['slots']['verification']['receipt'])
    with Session(engine()) as db:
        row = db.get(Task, verification_id)
        row.data = {**copy.deepcopy(row.data), 'dual_handoff': {
            **copy.deepcopy(row.data['dual_handoff']), 'snapshot_hash': '0' * 64}}
        damaged = copy.deepcopy(row.data)
        damaged_version = row.version
        db.commit()
    assert scene['actors']['verification'].get('/api/workspace/tasks/' + verification_id).status_code == 409
    response = task_detail(scene, parent, 'response')
    visible = response['dual_handoff_view']
    assert visible['slots']['verification']['state'] == 'created'
    assert visible['slots']['verification']['receipt'] == original_creation_receipt
    assert visible['slots']['verification']['integrity_issue']
    assert visible['slots']['verification']['can_open_task'] is False
    assert visible['slots']['response']['task_id'] == response['id']
    assert visible['slots']['response']['can_open_task'] is True
    accepted = require_reply(scene['actors']['response'].post(handoff_path(parent['id']) +
        '/response/ack', json=ack_body(parent, response)))
    response = task_detail(scene, accepted['handoff'], 'response')
    response = require_reply(complete_step(scene['actors']['response'], response))
    response = require_reply(complete_step(scene['actors']['response'], response))
    assert response['status'] == 'workflow_completed'
    with Session(engine()) as db:
        row = db.get(Task, verification_id)
        assert (row.version, row.data) == (damaged_version, damaged)
        assert db.get(Entity, scene['object']['id']).version == scene['object']['version']


def test_corrupt_pending_parent_is_quarantined_without_starving_later_valid_parent(dual_scene):
    from generic_agents.signal_dual_handoffs import deliver_pending
    from generic_agents.signal_dual_models import SignalDualHandoff
    scene = dual_scene
    _, _, broken = receive_parent(scene)
    _, _, valid = receive_parent(scene)
    with Session(engine()) as db:
        row = db.get(SignalDualHandoff, broken['id'])
        row.data = {**copy.deepcopy(row.data), 'immutable_hash': '0' * 64}
        original_immutable = copy.deepcopy(row.data['immutable'])
        db.commit()
    deliver_pending(scene['workspace_id'])
    current = require_reply(scene['admin'].get(handoff_path(valid['id'])))
    good_ids = {current['slots'][slot]['task_id'] for slot in ('response', 'verification')}
    assert None not in good_ids and len(good_ids) == 2
    assert all(current['slots'][slot]['state'] == 'created' for slot in ('response', 'verification'))
    assert task_ids(scene) == good_ids
    inbox_path = '/api/workspace/instances/' + scene['release']['instance_id'] + '/signal-handoffs'
    first_page = require_reply(scene['admin'].get(inbox_path, params={'mine_only': 'false', 'limit': 1}))
    assert first_page['items'] == [] and first_page['next_cursor']
    assert len(first_page['integrity_failures']) == 1
    assert first_page['integrity_failures'] == [{'id': broken['id'],
        'instance_id': scene['release']['instance_id'], 'code': 'dual_parent_integrity',
        'message': first_page['integrity_failures'][0]['message']}]
    assert first_page['integrity_failures'][0]['message']
    second_page = require_reply(scene['admin'].get(inbox_path, params={
        'mine_only': 'false', 'limit': 1, 'after': first_page['next_cursor']}))
    assert [item['id'] for item in second_page['items']] == [valid['id']]
    assert second_page['integrity_failures'] == [] and second_page['next_cursor'] is None
    with Session(engine()) as db:
        row = db.get(SignalDualHandoff, broken['id'])
        assert row.data['immutable_hash'] == '0' * 64 and row.data['immutable'] == original_immutable
        assert row.response_task_id is None and row.verification_task_id is None
        assert row.data['dispatch_integrity_failure']['code'] == 'dual_parent_integrity'
        quarantined_version = row.version
    deliver_pending(scene['workspace_id'])
    assert task_ids(scene) == good_ids
    with Session(engine()) as db:
        assert db.get(SignalDualHandoff, broken['id']).version == quarantined_version
        assert not list(db.scalars(select(CloudCall.id)))


def test_two_physical_dispatch_transactions_create_only_one_task_per_slot(dual_scene):
    from generic_agents.signal_dual_handoffs import dispatch_slot as dispatch
    scene = dual_scene
    _, _, parent = receive_parent(scene)
    for slot in ('response', 'verification'):
        ready = Barrier(2)

        def attempt():
            with Session(engine()) as db:
                # This first command takes a connection before the barrier;
                # neither contender is merely a reused in-memory Session.
                db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                pid = db.scalar(text('SELECT pg_backend_pid()'))
                ready.wait(timeout=5)
                dispatch(db, scene['workspace_id'], parent['id'], slot)
                db.commit()
                return pid

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            assert len({future.result(timeout=12) for future in futures}) == 2
    parent = require_reply(scene['admin'].get(handoff_path(parent['id'])))
    ids = {parent['slots'][slot]['task_id'] for slot in ('response', 'verification')}
    assert len(ids) == 2 and None not in ids and task_ids(scene) == ids
    _, route = current_route(scene)
    assert route['reserved_tasks'] == route['created_tasks'] == 2


@pytest.mark.parametrize('dual_scene', [2], indirect=True)
def test_exhausted_raw_reprocessing_cannot_create_a_third_task_through_legacy_api(dual_scene):
    scene = dual_scene
    original, body, parent = receive_parent(scene)
    for slot in ('response', 'verification'):
        parent = dispatch_slot(scene, parent, slot)
    original_task_ids = task_ids(scene)
    assert len(original_task_ids) == 2
    _, route = current_route(scene)
    assert route['status'] == 'exhausted' and route['reserved_tasks'] == 2
    observation, _ = ingest_observation(scene, record=body['source_record_id'],
        processing_version='dual-exhausted-reprocessing-v2')
    assert observation['id'] != original['id']
    records = require_reply(scene['admin'].get('/api/workspace/instances/' +
        scene['release']['instance_id'] + '/signals'))['records']
    repeated = next(row for row in records if row['basis'] and
        row['basis']['observation_id'] == observation['id'])
    assert repeated['dual_handoff']['id'] == parent['id']
    legacy_body = {'release_id': scene['release']['id'],
        'business_id': scene['command']['response_business_id'],
        'entity_ids': [scene['object']['id']], 'title': '旧入口不能重复这项原始登记的双交接',
        'request_id': str(uuid4()), 'signal_id': repeated['id'],
        'signal_version': repeated['version'], 'signal_basis_hash': repeated['basis_hash']}
    rejected = scene['admin'].post('/api/workspace/instances/' +
        scene['release']['instance_id'] + '/tasks', json=legacy_body)
    assert rejected.status_code == 409, rejected.text
    assert task_ids(scene) == original_task_ids
    assert [item['id'] for item in handoffs(scene)] == [parent['id']]
    _, route = current_route(scene)
    assert route['reserved_tasks'] == route['created_tasks'] == 2


def test_quarantined_parent_does_not_prevent_explicit_pause_or_original_receipt_recovery(dual_scene):
    from generic_agents.signal_dual_handoffs import deliver_pending
    from generic_agents.signal_dual_models import SignalDualHandoff
    scene = dual_scene
    _, _, broken = receive_parent(scene)
    _, _, healthy = receive_parent(scene)
    with Session(engine()) as db:
        parent = db.get(SignalDualHandoff, broken['id'])
        parent.data = {**copy.deepcopy(parent.data), 'immutable_hash': '0' * 64}
        db.commit()
    deliver_pending(scene['workspace_id'])
    with Session(engine()) as db:
        frozen = {identity: (row.version, copy.deepcopy(row.data), row.response_task_id,
                            row.verification_task_id)
                  for identity in (broken['id'], healthy['id'])
                  for row in [db.get(SignalDualHandoff, identity)]}
        assert frozen[broken['id']][1]['dispatch_integrity_failure']['code'] == 'dual_parent_integrity'
    listing, route = current_route(scene)
    assert route['reserved_tasks'] == 4 and route['created_tasks'] == 2
    before_task_ids = task_ids(scene)
    assert len(before_task_ids) == 2
    accepted, body, url = pause_route(scene)
    assert accepted['receipt']['accepted_route_status'] == 'paused'
    restored = require_reply(scene['admin'].get(routing_path(scene['release']) +
        '/dual/requests/' + body['request_id']))
    assert restored['receipt'] == accepted['receipt']
    assert require_reply(scene['admin'].post(url, json=body))['receipt'] == accepted['receipt']
    assert scene['admin'].post(url, json={**body, 'reason': '不能用原nonce改写暂停原因'}).status_code == 409
    _, route = current_route(scene)
    assert route['status'] == 'paused' and route['reserved_tasks'] == 4 and route['created_tasks'] == 2
    assert task_ids(scene) == before_task_ids
    with Session(engine()) as db:
        for identity, expected in frozen.items():
            row = db.get(SignalDualHandoff, identity)
            assert (row.version, row.data, row.response_task_id, row.verification_task_id) == expected
