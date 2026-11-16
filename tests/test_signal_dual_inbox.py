"""Real-PG Inbox discovery, authored for the root's isolated runner only.

Reported numbers and workflows are synthetic. Accounts, workflow confirmation,
publication, activation, ACK and completion all use actual production paths.
These tests do not start a database/service, create a model call, or use a
historical database. Query timings are measurements, not a production SLA.
"""
import base64
import copy
import json
import os
from contextlib import ExitStack, contextmanager
from time import perf_counter
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.models import InstanceRelease, Task
from generic_agents.signal_dual_models import SignalDualHandoff
from tests.test_instance_workspace import OBJECTS, configuration, enable, object_row, save
from tests.test_signal_dual_handoffs import (
    dual_scene, ingest_observation, dispatch_slot, task_detail, complete_step,
    handoff_path, routing_path, require_reply, ack_body,
)
from tests.test_signal_dual_read_consistency import persisted_state
from tests.test_vertical_slice import manifest, publish


SCHEMA = 'signal-dual-handoff-inbox-v2'
ENVELOPE = {'schema', 'instance_id', 'mode', 'mine_only', 'snapshot_upper',
            'sort', 'items', 'next_cursor', 'integrity_failures'}
ASSIGNMENT = {'task_id', 'task_version', 'current_step', 'current_assignee',
              'actions', 'history_reasons'}


@pytest.fixture(autouse=True)
def owned_database_only():
    url = make_url(settings().database_url)
    marker = os.environ.get('GA_F11_DUAL_TESTS_OWNED', '')
    assert marker.isascii() and marker.isdecimal()
    port = int(marker)
    assert 56828 <= port <= 60028 and port % 100 == 28
    assert url.host in {'127.0.0.1', 'localhost'} and url.port == port
    assert url.database == 'ga_f11_dual_tests'


def inbox_url(scene):
    return '/api/workspace/instances/' + scene['release']['instance_id'] + '/signal-inbox'


def inbox(scene, *, actor=None, mode='pending', mine=True, limit=20, after=None):
    params = {'mode': mode, 'mine_only': str(mine).lower(), 'limit': limit}
    if after is not None:
        params['after'] = after
    value = require_reply((actor or scene['admin']).get(inbox_url(scene), params=params))
    assert set(value) == ENVELOPE and value['schema'] == SCHEMA
    assert value['instance_id'] == scene['release']['instance_id']
    assert value['mode'] == mode and value['mine_only'] is mine
    assert value['sort'] == 'created_desc'
    assert isinstance(value['snapshot_upper'], str) and value['snapshot_upper']
    assert value['next_cursor'] is None or isinstance(value['next_cursor'], str)
    for item in value['items']:
        assert set(item) == {'handoff', 'matched_slots', 'current_assignments'}
        assert item['matched_slots'] and len(item['matched_slots']) == len(set(item['matched_slots']))
        assert set(item['matched_slots']) <= {'response', 'verification'}
        assert set(item['current_assignments']) == {'response', 'verification'}
        for assignment in item['current_assignments'].values():
            assert set(assignment) == ASSIGNMENT
            for action in assignment['actions']:
                assert set(action) == {'kind', 'assignee'}
                assert action['kind'] in {'ack', 'step', 'dispatch_pending', 'blocked'}
                assert set(action['assignee']) == {'role', 'user_id', 'username'}
            assert set(assignment['history_reasons']) <= {
                'assigned_before', 'acknowledged', 'step_completed', 'note_saved', 'cancelled'}
    for failure in value['integrity_failures']:
        assert set(failure) == {'id', 'instance_id', 'code', 'message'}
        assert failure['instance_id'] == scene['release']['instance_id']
        assert failure['code'] and failure['message']
    return value


def items_by_id(value):
    return {item['handoff']['id']: item for item in value['items']}


def new_parent(scene):
    # V1 defaults to its first20 rows. Find the *new* production-created row by
    # its real Observation FK instead of treating that old page as all history.
    observation, _ = ingest_observation(scene)
    with Session(engine()) as db:
        identity = db.scalar(select(SignalDualHandoff.id).where(
            SignalDualHandoff.workspace_id == scene['workspace_id'],
            SignalDualHandoff.instance_id == scene['release']['instance_id'],
            SignalDualHandoff.observation_id == observation['id']))
        assert identity is not None
    return require_reply(scene['admin'].get(handoff_path(identity)))


def ack_response(scene, parent, *, actor=None):
    who = actor or scene['actors']['response']
    task = task_detail(scene, parent, 'response', actor=who)
    return require_reply(who.post(handoff_path(parent['id']) + '/response/ack',
                                  json=ack_body(parent, task)))['handoff']


def finish_response(scene, parent):
    parent = dispatch_slot(scene, parent, 'response')
    parent = ack_response(scene, parent)
    task = task_detail(scene, parent, 'response')
    task = require_reply(complete_step(scene['actors']['response'], task))
    task = require_reply(complete_step(scene['actors']['response'], task))
    assert task['status'] == 'workflow_completed'
    return require_reply(scene['admin'].get(handoff_path(parent['id'])))


def finish_pair(scene, parent):
    parent = finish_response(scene, parent)
    parent = dispatch_slot(scene, parent, 'verification')
    task = task_detail(scene, parent, 'verification')
    task = require_reply(complete_step(scene['actors']['verification'], task))
    task = require_reply(complete_step(scene['actors']['verification'], task, decision='uncertain'))
    assert task['status'] == 'workflow_completed'
    parent = require_reply(scene['admin'].get(handoff_path(parent['id'])))
    assert parent['slots']['verification']['result']['decision'] == 'uncertain'
    return parent


@contextmanager
def measured_reads():
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    target = engine()
    event.listen(target, 'before_cursor_execute', capture)
    started = perf_counter()
    result = {'statements': statements}
    try:
        yield result
    finally:
        result['seconds'] = perf_counter() - started
        event.remove(target, 'before_cursor_execute', capture)


def read_count(measurement):
    return sum(statement.lstrip().upper().startswith('SELECT') for statement in measurement['statements'])


def task_read_count(measurement):
    return sum('FROM TASKS' in statement.upper() for statement in measurement['statements'])


def assert_unlocked_reads(measurement):
    for statement in measurement['statements']:
        normalized = ' '.join(statement.upper().split())
        assert 'FOR UPDATE' not in normalized and 'FOR SHARE' not in normalized, normalized[:250]
        assert not normalized.startswith(('INSERT ', 'UPDATE ', 'DELETE ')), normalized[:250]


@pytest.mark.parametrize('dual_scene', [100], indirect=True)
def test_45_completed_histories_do_not_bury_pending_and_three_pages_are_exact(dual_scene, record_property):
    scene = dual_scene
    completed = [finish_pair(scene, new_parent(scene))['id']]
    pending = dispatch_slot(scene, new_parent(scene), 'response')
    actor = scene['actors']['response']
    with measured_reads() as small:
        head = inbox(scene, actor=actor, limit=1)
    assert list(items_by_id(head)) == [pending['id']]
    assert head['items'][0]['matched_slots'] == ['response']
    for _ in range(44):
        completed.append(finish_pair(scene, new_parent(scene))['id'])
    before = persisted_state()
    with measured_reads() as large:
        head = inbox(scene, actor=actor, limit=1)
    assert list(items_by_id(head)) == [pending['id']]
    assert head['next_cursor'] is None and head['integrity_failures'] == []
    # Same returned page, scope grows2->46. Constant/slim batch queries may
    # change a little, but one full Task SELECT per parent must fail this test.
    assert read_count(large) <= read_count(small) + 6
    assert task_read_count(large) <= task_read_count(small) + 2
    record_property('pending_scope_2_selects', read_count(small))
    record_property('pending_scope_46_selects', read_count(large))
    record_property('pending_scope_46_task_selects', task_read_count(large))
    record_property('pending_scope_46_seconds', large['seconds'])
    pages = []
    after = None
    with measured_reads() as history_reads:
        for _ in range(3):
            page = inbox(scene, actor=actor, mode='history', limit=20, after=after)
            pages.append(page)
            after = page['next_cursor']
    assert [len(page['items']) for page in pages] == [20, 20, 5]
    assert pages[0]['next_cursor'] and pages[1]['next_cursor'] and after is None
    assert len({page['snapshot_upper'] for page in pages}) == 1
    identities = [item['handoff']['id'] for page in pages for item in page['items']]
    assert len(identities) == len(set(identities)) == 45
    with Session(engine()) as db:
        expected = list(db.scalars(select(SignalDualHandoff.id).where(
            SignalDualHandoff.id.in_(completed)).order_by(
            SignalDualHandoff.created_at.desc(), SignalDualHandoff.id.desc())))
    assert identities == expected and set(identities) == set(completed)
    assert all(page['integrity_failures'] == [] for page in pages)
    assert all(item['matched_slots'] == ['response'] for page in pages for item in page['items'])
    record_property('history_three_pages_selects', read_count(history_reads))
    record_property('history_three_pages_seconds', history_reads['seconds'])
    assert_unlocked_reads(small)
    assert_unlocked_reads(large)
    assert_unlocked_reads(history_reads)
    assert persisted_state() == before


@pytest.fixture
def multi_role_scene(client, account):
    """Compile three actual operators into A->B->C, not a mutated release."""
    members, credentials = {}, {}
    for slot in ('response', 'next', 'future'):
        username, password = 'inbox_' + slot + '_' + uuid4().hex[:12], 'Synthetic-only-' + uuid4().hex
        members[slot] = require_reply(client.post('/api/users', json={
            'username': username, 'password': password, 'role': 'operator'}), 201)
        credentials[slot] = (username, password)
    admin = require_reply(client.get('/api/me'))
    members['verification'] = {'id': admin['user_id'], 'username': admin['username']}
    roles = {'response': '响应首接人', 'next': '响应复核人', 'future': '响应记录人', 'verification': '独立核验负责人'}
    config = configuration()
    template = copy.deepcopy(config['flows']['资料核验'])
    response_name, verification_name = '三人依次人工响应', '管理员独立人工核验'
    response = copy.deepcopy(template)
    response['steps'] = []
    for index, slot in enumerate(('response', 'next', 'future')):
        step = copy.deepcopy(template['steps'][0])
        step.update(id='response_' + str(index), name='人工响应第' + str(index + 1) + '步',
                    role=roles[slot], purpose='登记并转交人工响应，不判定现场物理异常',
                    condition='', nextId='end' if index == 2 else 'auto')
        response['steps'].append(step)
    verification = copy.deepcopy(template)
    for step in verification['steps']:
        step['role'] = roles['verification']
        step['purpose'] = '独立核验应用内登记，允许尚不能判断'
    config['businesses'] = [response_name, verification_name]
    config['flows'] = {response_name: response, verification_name: verification}
    config['automation'] = {'version': 1, 'policies': {
        name: {'rules': {'write': {'mode': '仅给出建议'}}} for name in config['businesses']}}
    for flow in config['flows'].values():
        flow['confirmedSignature'] = json.dumps(signature_value(config, flow), ensure_ascii=False)
    revision = save(client, config, review_automation=True)
    body = {'revision': revision, 'objects': OBJECTS,
            'role_bindings': {role: members[slot]['id'] for slot, role in roles.items()}}
    checked = require_reply(client.post('/api/instances/check', json=body))
    release = require_reply(client.post('/api/instances', json={**body,
        'confirmed_configuration_hash': checked['configuration_hash'],
        'confirm_configuration': True, 'generation_intent': 'new_instance',
        'request_id': str(uuid4())}), 201)
    assert len(release['available_business_ids']) == 2
    assert {release['snapshot']['roles'][roles[slot]]['user_id'] for slot in ('response', 'next', 'future')} == {
        members[slot]['id'] for slot in ('response', 'next', 'future')}
    enable(client, release)
    obj = object_row(client, release)
    source = publish(client, manifest('source', name='inbox_reported_source',
        allowed_agent_roles=['perception'], execution={'adapter': 'upload'},
        specification={'timezone': 'Asia/Shanghai'}, data_policy={'cloud_allowed': False}))
    extractor = publish(client, manifest('tool', name='inbox_local_numeric_rule',
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': [{'id': 'high', 'field': '公开登记值', 'operator': 'gt', 'threshold': 50}]},
        data_policy={'cloud_allowed': False}))
    options = require_reply(client.get(routing_path(release) + '/dual-options'))
    choices = {item['name']: item for item in options['businesses']}
    command = {'request_id': str(uuid4()), 'expected_version': options['instance_version'],
        'options_hash': options['options_hash'], 'release_id': release['id'],
        'source_id': source['id'], 'extractor_id': extractor['id'], 'entity_ids': [obj['id']],
        'response_business_id': choices[response_name]['id'],
        'verification_business_id': choices[verification_name]['id'], 'max_tasks': 10,
        'reason': '仅测试真实三角色应用内人工交接，不推断物理真值', 'confirm_human_review_only': True}
    enabled = require_reply(client.post(routing_path(release) + '/enable-dual', json=command), 201)
    with ExitStack() as stack:
        actors = {'verification': client}
        for slot, (username, password) in credentials.items():
            actor = stack.enter_context(TestClient(app))
            login = require_reply(actor.post('/api/login', json={'username': username, 'password': password}))
            actor.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': account[2]})
            actors[slot] = actor
        yield {'admin': client, 'workspace_id': account[2], 'release': release, 'object': obj,
            'source': source, 'extractor': extractor, 'members': members, 'actors': actors,
            'command': command, 'enabled': enabled}


def test_current_role_handoff_history_and_unacked_original_recipient_are_independent(multi_role_scene):
    scene = multi_role_scene
    parent = dispatch_slot(scene, new_parent(scene), 'response')
    first_recipient = copy.deepcopy(parent['slots']['response']['recipient'])
    a, b, c = (scene['actors'][slot] for slot in ('response', 'next', 'future'))
    row = items_by_id(inbox(scene, actor=a))[parent['id']]
    assert row['matched_slots'] == ['response']
    assert {action['kind'] for action in row['current_assignments']['response']['actions']} == {'ack', 'step'}
    assert inbox(scene, actor=b)['items'] == []
    assert inbox(scene, actor=c, mode='history')['items'] == []
    assert inbox(scene, actor=c)['items'] == []
    task = task_detail(scene, parent, 'response', actor=a)
    task = require_reply(complete_step(a, task))  # first step need not wait for ACK
    assert task['status'] == 'workflow_waiting' and task['workflow_runtime']['current_step_id'] == 'response_1'
    a_pending = items_by_id(inbox(scene, actor=a))[parent['id']]['current_assignments']['response']
    b_pending = items_by_id(inbox(scene, actor=b))[parent['id']]['current_assignments']['response']
    assert a_pending['current_assignee']['user_id'] == scene['members']['next']['id']
    assert {(v['kind'], v['assignee']['user_id']) for v in a_pending['actions']} == {
        ('ack', scene['members']['response']['id']), ('step', scene['members']['next']['id'])}
    assert b_pending['task_id'] == a_pending['task_id'] == task['id']
    assert b_pending['actions'] == a_pending['actions']
    a_history = items_by_id(inbox(scene, actor=a, mode='history'))[parent['id']]
    assert a_history['matched_slots'] == ['response']
    assert {'assigned_before', 'step_completed'} <= set(a_history['current_assignments']['response']['history_reasons'])
    assert inbox(scene, actor=c, mode='history')['items'] == []
    assert inbox(scene, actor=c)['items'] == []
    task = require_reply(complete_step(b, task))
    assert task['workflow_runtime']['current_step_id'] == 'response_2'
    assert parent['id'] in items_by_id(inbox(scene, actor=b, mode='history'))
    assert inbox(scene, actor=b)['items'] == []
    c_row = items_by_id(inbox(scene, actor=c))[parent['id']]
    assert c_row['current_assignments']['response']['current_assignee']['user_id'] == scene['members']['future']['id']
    assert inbox(scene, actor=c, mode='history')['items'] == []
    acknowledged = require_reply(a.post(handoff_path(parent['id']) + '/response/ack', json=ack_body(parent, task)))
    assert acknowledged['receipt']['ack_actor_id'] == first_recipient['user_id']
    assert acknowledged['handoff']['slots']['response']['recipient'] == first_recipient
    assert inbox(scene, actor=a)['items'] == []
    assert parent['id'] in items_by_id(inbox(scene, actor=a, mode='history'))
    assert items_by_id(inbox(scene, actor=c))[parent['id']]['current_assignments']['response']['current_assignee']['user_id'] == scene['members']['future']['id']


def test_completed_own_slot_is_history_even_while_other_slot_is_pending(dual_scene):
    scene = dual_scene
    parent = finish_response(scene, new_parent(scene))
    assert parent['slots']['verification']['state'] == 'pending'
    actor = scene['actors']['response']
    before = persisted_state()
    assert inbox(scene, actor=actor)['items'] == []
    history = items_by_id(inbox(scene, actor=actor, mode='history'))[parent['id']]
    assert history['matched_slots'] == ['response']
    assert 'acknowledged' in history['current_assignments']['response']['history_reasons']
    other = items_by_id(inbox(scene, actor=scene['actors']['verification']))[parent['id']]
    assert other['matched_slots'] == ['verification']
    assert other['current_assignments']['verification']['actions'][0]['kind'] == 'dispatch_pending'
    assert persisted_state() == before


def test_actual_note_participant_remains_history_after_draft_is_overwritten_and_step_transferred(multi_role_scene):
    scene = multi_role_scene
    parent = dispatch_slot(scene, new_parent(scene), 'response')
    task = task_detail(scene, parent, 'response')
    note_url = '/api/workspace/tasks/' + task['id'] + '/note'
    task = require_reply(scene['admin'].put(note_url, json={
        'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
        'note': '管理员仅协助记录交接背景，不代替现场核验', 'override_reason': '本次合成测试明确代录'}))
    assert task['workflow_runtime']['drafts']['response_0']['actor'] == scene['members']['verification']['id']
    task = require_reply(scene['actors']['response'].put(note_url, json={
        'version': task['version'], 'step_id': 'response_0', 'note': '首位响应人自己的更新说明'}))
    assert task['workflow_runtime']['drafts']['response_0']['actor'] == scene['members']['response']['id']
    task = require_reply(complete_step(scene['actors']['response'], task))
    assert task['workflow_runtime']['current_step_id'] == 'response_1'
    assert 'response_0' not in task['workflow_runtime']['drafts']
    before = persisted_state()
    history = items_by_id(inbox(scene, actor=scene['admin'], mode='history'))[parent['id']]
    assert history['matched_slots'] == ['response']
    assert 'note_saved' in history['current_assignments']['response']['history_reasons']
    assert inbox(scene, actor=scene['actors']['future'], mode='history')['items'] == []
    assert persisted_state() == before


@pytest.mark.parametrize('damage', ['recipient', 'slot_status', 'task_marker', 'missing_role'])
@pytest.mark.parametrize('mode', ['pending', 'history'])
def test_corruption_cannot_hide_behind_mine_or_mode_and_failure_cursor_advances(dual_scene, damage, mode):
    scene = dual_scene
    healthy = dispatch_slot(scene, new_parent(scene), 'response')
    damaged = dispatch_slot(scene, new_parent(scene), 'response')
    with Session(engine()) as db:
        parent = db.get(SignalDualHandoff, damaged['id'])
        if damage in {'recipient', 'slot_status'}:
            data = copy.deepcopy(parent.data)
            if damage == 'recipient':
                data['immutable']['businesses']['response']['recipient']['user_id'] = str(uuid4())
            else:
                data['slots']['response']['state'] = 'completed'
            parent.data = data
        elif damage == 'task_marker':
            task = db.get(Task, parent.response_task_id)
            data = copy.deepcopy(task.data)
            data.pop('dual_handoff')
            task.data = data
        else:
            release = db.get(InstanceRelease, parent.release_id)
            data = copy.deepcopy(release.data)
            data['snapshot']['roles'].pop(damaged['slots']['response']['recipient']['role'])
            release.data = data
        db.commit()
    before = persisted_state()
    first = inbox(scene, actor=scene['actors']['response'], mode=mode, limit=1)
    assert first['items'] == [] and [v['id'] for v in first['integrity_failures']] == [damaged['id']]
    if damage == 'missing_role':
        assert first['next_cursor']
        second = inbox(scene, actor=scene['actors']['response'], mode=mode, limit=1, after=first['next_cursor'])
        assert second['items'] == [] and [v['id'] for v in second['integrity_failures']] == [healthy['id']]
        assert second['next_cursor'] is None
    elif mode == 'pending':
        assert first['next_cursor']
        second = inbox(scene, actor=scene['actors']['response'], mode=mode, limit=1, after=first['next_cursor'])
        assert list(items_by_id(second)) == [healthy['id']] and second['integrity_failures'] == []
        assert second['next_cursor'] is None
    else:
        assert first['next_cursor'] is None  # healthy unvisited Task is not history
    assert persisted_state() == before


def decode_cursor(value):
    return json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))


def pack_cursor(value):
    return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True).encode()).decode().rstrip('=')


def fresh_instance(scene):
    revision = require_reply(scene['admin'].get('/api/setup/draft'))['revision']
    body = {'revision': revision, 'objects': OBJECTS,
        'role_bindings': {role: binding['user_id'] for role, binding in scene['release']['snapshot']['roles'].items()}}
    checked = require_reply(scene['admin'].post('/api/instances/check', json=body))
    release = require_reply(scene['admin'].post('/api/instances', json={**body,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': str(uuid4())}), 201)
    assert release['instance_id'] != scene['release']['instance_id']
    return release


def test_cursor_is_hmac_bound_to_real_session_actor_mode_mine_and_instance(dual_scene, account):
    scene = dual_scene
    for _ in range(2):
        dispatch_slot(scene, new_parent(scene), 'response')
    head = inbox(scene, mine=False, limit=1)
    token = head['next_cursor']
    assert token
    second_instance = fresh_instance(scene)
    before = persisted_state()
    original = decode_cursor(token)
    forged = copy.deepcopy(original)
    forged['last_id'] = str(uuid4())
    # A publicly computable legacy digest is not the new per-session HMAC.
    forged['hash'] = digest({k: v for k, v in forged.items() if k != 'hash'})
    assert scene['admin'].get(inbox_url(scene), params={
        'after': pack_cursor(forged), 'mine_only': 'false', 'limit': 1}).status_code == 409
    for params in ({'mode': 'history', 'mine_only': 'false'}, {'mode': 'pending', 'mine_only': 'true'}):
        assert scene['admin'].get(inbox_url(scene), params={**params, 'after': token, 'limit': 1}).status_code == 409
    assert scene['actors']['response'].get(inbox_url(scene), params={
        'after': token, 'mine_only': 'false', 'limit': 1}).status_code == 409
    other_url = '/api/workspace/instances/' + second_instance['instance_id'] + '/signal-inbox'
    assert scene['admin'].get(other_url, params={'after': token, 'mine_only': 'false', 'limit': 1}).status_code == 409
    assert persisted_state() == before
    with TestClient(app) as relogged:
        username, password, workspace_id = account
        login = require_reply(relogged.post('/api/login', json={'username': username, 'password': password}))
        relogged.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': workspace_id})
        fresh_before = persisted_state()
        assert relogged.get(inbox_url(scene), params={'after': token, 'mine_only': 'false', 'limit': 1}).status_code == 409
        assert inbox(scene, actor=relogged, mine=False, limit=1)['items']
        assert persisted_state() == fresh_before
    # Merely logging in a second session must not revoke the first cursor.
    restored = inbox(scene, mine=False, limit=1, after=token)
    assert restored['items'] and restored['snapshot_upper'] == head['snapshot_upper']


def test_cursor_upper_excludes_new_parent_until_explicit_head_refresh(dual_scene):
    scene = dual_scene
    old = [dispatch_slot(scene, new_parent(scene), 'response')['id'] for _ in range(3)]
    head = inbox(scene, actor=scene['actors']['response'], limit=1)
    assert head['next_cursor']
    newest = dispatch_slot(scene, new_parent(scene), 'response')['id']
    before = persisted_state()
    seen = list(items_by_id(head))
    after = head['next_cursor']
    while after:
        page = inbox(scene, actor=scene['actors']['response'], limit=1, after=after)
        assert page['snapshot_upper'] == head['snapshot_upper']
        seen.extend(items_by_id(page))
        after = page['next_cursor']
        assert len(seen) <= len(old), 'cursor did not advance'
    assert set(seen) == set(old) and len(seen) == len(set(seen))
    fresh = inbox(scene, actor=scene['actors']['response'], limit=1)
    assert list(items_by_id(fresh)) == [newest]
    assert persisted_state() == before
