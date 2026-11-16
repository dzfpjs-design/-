"""Actual API/Worker/PG boundaries for a suggestion known not to be sent.

The exact model POST is synthetic; budget reserve/finish, policy authorization,
durable Jobs, suggestion journals and CloudCall receipts remain actual. One
explicit SQLAlchemy durability fault rejects the first raw-response flush.
These tests spend no real cloud funds and do not establish model usefulness.
"""
import copy
import json
import re
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Entity, Job, Memory, Task
from generic_agents.services import as_dict
from generic_agents.worker import process_once
from tests import test_native_method_suggestions as old
from tests.test_vertical_slice import manifest, publish


ENDPOINT = 'https://api.deepseek.com/chat/completions'


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _capacity(monkeypatch):
    # Scoped to the same synthetic transport. No environment budget override,
    # reservation mock, ledger rewrite, or production quota change is used.
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 25)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)


def _transport(monkeypatch, sent, *, timeout=False):
    _capacity(monkeypatch)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-v130-not-a-real-key')
    original = httpx.Client.post

    def post(client, target, **kwargs):
        if str(target) != ENDPOINT:
            return original(client, target, **kwargs)
        assert kwargs['headers']['Authorization'] == 'Bearer synthetic-v130-not-a-real-key'
        payload = copy.deepcopy(kwargs['json']); sent.append(payload)
        if timeout:
            raise httpx.ReadTimeout('synthetic response timeout after POST dispatch',
                request=httpx.Request('POST', ENDPOINT))
        context = json.loads(payload['messages'][-1]['content'])['input']
        assert context['contract_version'] == 'native-method-projection-v2'
        necessary = {(alias, 'field_verification') for alias in context['goal']['required_aliases']}
        necessary.update((field['alias'], 'units') for field in context['fields']
            if field['type'] in {'integer', 'number'} and field['flags']['units'] == 'unknown')
        if context['goal']['time_constraint'] == 'required':
            necessary.update((alias, 'source_time') for alias in context['goal']['required_aliases'])
        value = {'steps': [pair for pair in context['allowed_pairs']
            if (pair['alias'], pair['template']) in necessary]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}}],
            'usage': {'total_tokens': 91, 'prompt_tokens': 62, 'completion_tokens': 29}},
            headers={'x-request-id': 'synthetic-v130-' + str(len(sent))})

    monkeypatch.setattr(httpx.Client, 'post', post)


def _origin(task_id):
    with Session(engine()) as db:
        task = db.get(Task, task_id)
        return {'acceptance': copy.deepcopy(task.data['native_goal_acceptance']),
            'acceptance_hash': task.data['native_goal_acceptance_hash'],
            'workflow_runtime': copy.deepcopy(task.data['workflow_runtime']),
            'outcome': copy.deepcopy(as_dict(db.get(Memory, task.data['native_goal_data_receipt_id']))),
            'entities': {row.id: digest(as_dict(row)) for row in db.scalars(select(Entity))}}


def _inventory():
    with Session(engine()) as db:
        return {model.__tablename__: {row.id: digest(as_dict(row)) for row in db.scalars(select(model))}
            for model in (Job, CloudCall, Memory)}


def _rows(suggestion_id):
    with Session(engine()) as db:
        row = db.get(Memory, suggestion_id)
        return copy.deepcopy(as_dict(row)), copy.deepcopy(as_dict(db.get(Job, row.data['job_id'])))


def _clouds():
    with Session(engine()) as db:
        return [copy.deepcopy(as_dict(row)) for row in db.scalars(select(CloudCall))]


def _new_clouds(initial_ids):
    return [row for row in _clouds() if row['id'] not in initial_ids]


def _not_sent(view, reservation_state):
    assert view['status'] == 'failed' and not view['has_saved_response']
    assert view['call']['status'] == 'not_started'
    dispatch = view['call']['dispatch']
    assert set(dispatch) == {'status', 'reservation_state', 'proof_hash'}
    assert dispatch['status'] == 'not_sent' and dispatch['reservation_state'] == reservation_state
    assert re.fullmatch(r'[0-9a-f]{64}', dispatch['proof_hash'])
    row, job = _rows(view['id'])
    assert row['data']['inflight'] is False and job['status'] == 'failed'


def _unknown(view):
    assert view['status'] == 'response_unknown' and not view['has_saved_response']
    assert view['call']['status'] == 'unknown'
    dispatch = view['call'].get('dispatch')
    assert not (dispatch and dispatch.get('status') == 'not_sent')
    _, job = _rows(view['id']); assert job['status'] == 'failed'


def _accepted_scene(client):
    # Public issue/activate/object/task/goal/accept paths; a unique published
    # profile also permits two journeys in one actual checkpoint workspace.
    release, obj, task, path, definition = old.setup(client)
    old.define(client, path, definition); _ok(old.accept(client, task))
    cap = publish(client, manifest('foundation_model', namespace='native_not_sent_v130',
        name='model_' + uuid4().hex, allowed_agent_roles=['business'],
        execution={'provider': 'deepseek', 'model': 'deepseek-chat',
            'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 2048}, data_policy={'cloud_allowed': True}))
    return release, obj, task, definition['goal_spec'], cap


def _start(client, monkeypatch, *, timeout=False):
    initial_cloud_ids = {row['id'] for row in _clouds()}
    release, _, task, _, cap = _accepted_scene(client)
    original = _origin(task['id']); sent = []
    _transport(monkeypatch, sent, timeout=timeout)
    row, command, _ = old.start(client, task, cap)
    assert row['status'] == 'queued' and not sent and not _new_clouds(initial_cloud_ids)
    return release, task, cap, row, command, original, sent, initial_cloud_ids


def _saved_start_only(client, task, row, command, sent, expected_posts):
    before = _inventory()
    body = old.recovery_body(row['task_version'], operation='start', original_request=command, action='probe')
    recovery_path = old.url(task) + '/requests/recover'
    recovered = _ok(client.post(recovery_path, json=body))
    assert recovered['saved'] and not recovered['abandoned'] and recovered['suggestion']['id'] == row['id']
    replayed = _ok(client.post(old.url(task) + '/start', json=command))
    assert replayed['suggestion']['id'] == row['id']
    assert _inventory() == before and len(sent) == expected_posts
    return {'recovery': {'path': recovery_path, 'body': body, 'response': recovered},
        'replay': {'path': old.url(task) + '/start', 'body': command, 'response': replayed}}


def run_not_sent_retry_journey(client, monkeypatch, limit_kind='calls'):
    """Actual failed parent/successful child and exact public recovery proof."""
    assert limit_kind in {'calls', 'tokens'}
    _, task, cap, parent, start_body, original, sent, initial_cloud_ids = _start(client, monkeypatch)
    original_row, _ = _rows(parent['id'])
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit' if limit_kind == 'calls' else 'cloud_daily_token_limit', 0)
    old.run(parent)
    failed = old.get(client, task, parent)
    _not_sent(failed, 'denied')
    assert not sent and not _new_clouds(initial_cloud_ids) and _origin(task['id']) == original
    start_readback = _saved_start_only(client, task, failed, start_body, sent, 0)
    _capacity(monkeypatch)
    preview = old.preview(client, task, cap)
    failed = old.get(client, task, parent)
    retry_body = old.action(failed, model_profile_id=cap['id'], preview_hash=preview['preview_hash'],
        cloud_authorized=True, acknowledge_possible_charge=True)
    retry_path = old.url(task) + '/' + parent['id'] + '/retry'
    with Session(engine()) as db: previous_jobs = set(db.scalars(select(Job.id)))
    retry_response = _ok(client.post(retry_path, json=retry_body))
    child = retry_response['suggestion']
    assert child['id'] != parent['id'] and child['parent_id'] == parent['id'] and child['status'] == 'queued'
    with Session(engine()) as db:
        new_jobs = set(db.scalars(select(Job.id))) - previous_jobs
        assert new_jobs == {db.get(Memory, child['id']).data['job_id']}
    old.run(child); child = old.get(client, task, child)
    assert child['status'] == 'needs_review' and child['has_saved_response'] and len(sent) == 1
    clouds = _new_clouds(initial_cloud_ids); assert len(clouds) == 1 and clouds[0]['status'] == 'succeeded'
    assert clouds[0]['charged_tokens'] == 91 and _origin(task['id']) == original
    saved_parent, _ = _rows(parent['id'])
    assert saved_parent['data']['request'] == original_row['data']['request']
    assert saved_parent['data']['request_hash'] == original_row['data']['request_hash']
    _not_sent(old.get(client, task, parent), 'denied')
    before = _inventory()
    assert _ok(client.post(retry_path, json=retry_body))['suggestion']['id'] == child['id']
    recovery = old.recovery_body(child['task_version'], operation='retry', suggestion_id=parent['id'],
        original_request=retry_body, action='probe')
    recovery_path = old.url(task) + '/requests/recover'
    recovered = _ok(client.post(recovery_path, json=recovery))
    assert recovered['saved'] and recovered['suggestion']['id'] == child['id']
    assert _inventory() == before and len(sent) == 1 and _origin(task['id']) == original
    task_path = '/api/workspace/tasks/' + task['id']
    parent_path = old.url(task) + '/' + parent['id']; child_path = old.url(task) + '/' + child['id']
    readbacks = [{'path': path, 'response': _ok(client.get(path))}
        for path in (task_path, task_path + '/goal', parent_path, child_path)]
    assert _inventory() == before
    return {'schema': 'native-suggestion-not-sent-retry-proof-v130', 'task_id': task['id'],
        'task_path': task_path, 'parent_id': parent['id'], 'child_id': child['id'], 'limit_kind': limit_kind,
        'memory_ids': {'parent': parent['id'], 'child': child['id'], 'goal_outcome': original['outcome']['id']},
        'commands': [start_readback['replay'], {'path': retry_path, 'body': retry_body, 'response': retry_response}],
        'readonly_requests': [start_readback['recovery'], {'path': recovery_path, 'body': recovery, 'response': recovered}],
        'readbacks': readbacks, 'parent_request_hash': original_row['data']['request_hash'],
        'original_acceptance_hash': original['acceptance_hash'], 'cloud_call_id': clouds[0]['id'],
        'send_count': len(sent), 'real_paid_calls': 0}


@pytest.mark.parametrize('quota', ['calls', 'tokens'])
def test_actual_budget_denied_is_not_sent_and_only_explicit_retry_can_generate(client, monkeypatch, quota):
    proof = run_not_sent_retry_journey(client, monkeypatch, quota)
    assert proof['send_count'] == 1 and proof['parent_id'] != proof['child_id']


def test_actual_reservation_then_public_policy_withdrawal_is_confirmed_not_sent(client, monkeypatch):
    initial_cloud_ids = {row['id'] for row in _clouds()}
    release, _, source, spec, cap = _accepted_scene(client)
    sent = []; _transport(monkeypatch, sent)
    old.enable_policy(client, source, cap)
    _, task = old.another(client, release, spec)
    _ok(old.accept(client, task))
    listing = _ok(client.get(old.url(task))); assert len(listing['items']) == 1
    row = listing['items'][0]; original = _origin(task['id'])
    scheduling = {'call_id': None, 'disabled': False}
    marker = 'v130_actual_reservation_committed'

    def after_flush(session, context):
        for item in session.new:
            if isinstance(item, CloudCall) and item.capability_id == cap['id']:
                assert scheduling['call_id'] is None
                scheduling['call_id'] = item.id
                session.info[marker] = True

    def after_commit(session):
        if not session.info.pop(marker, False): return
        assert not scheduling['disabled']; scheduling['disabled'] = True
        current = _ok(client.get(old.url(source) + '/policy'))
        body = old.common(current['version'], enabled=False,
            expected_policy_version=current['policy']['version'], policy_record_hash=current['policy']['record_hash'])
        disabled = _ok(client.post(old.url(source) + '/policy', json=body))
        assert not disabled['policy']['enabled']

    event.listen(Session, 'after_flush', after_flush)
    event.listen(Session, 'after_commit', after_commit)
    try: old.run(row)
    finally:
        event.remove(Session, 'after_commit', after_commit)
        event.remove(Session, 'after_flush', after_flush)
    current = old.get(client, task, row)
    _not_sent(current, 'confirmed')
    assert scheduling['disabled'] and not sent and _origin(task['id']) == original
    clouds = _new_clouds(initial_cloud_ids); assert len(clouds) == 1
    call = clouds[0]
    assert call['id'] == scheduling['call_id'] and call['status'] == 'failed'
    stored, _ = _rows(row['id'])
    assert stored['data']['cloud_call_id'] == call['id'] and not stored['data'].get('dispatch_authorized')
    assert call['data']['native_suggestion']['suggestion_id'] == row['id']


def run_timeout_unknown_journey(client, monkeypatch):
    """Actual dispatched timeout stays unknown; original commands only recover."""
    _, task, _, row, command, original, sent, initial_cloud_ids = _start(client, monkeypatch, timeout=True)
    old.run(row); unknown = old.get(client, task, row)
    _unknown(unknown)
    clouds = _new_clouds(initial_cloud_ids); assert len(sent) == len(clouds) == 1
    assert clouds[0]['status'] == 'failed' and _origin(task['id']) == original
    saved = _saved_start_only(client, task, unknown, command, sent, 1)
    before = _inventory()
    denied = client.post(old.url(task) + '/' + row['id'] + '/retry', json=old.action(unknown))
    assert denied.status_code == 409 and _inventory() == before and len(sent) == 1
    _, job = _rows(row['id'])
    assert not process_once(job['id'], deliver=False)
    assert _inventory() == before and len(sent) == 1 and _origin(task['id']) == original
    task_path = '/api/workspace/tasks/' + task['id']; path = old.url(task) + '/' + row['id']
    readbacks = [{'path': target, 'response': _ok(client.get(target))}
        for target in (task_path, task_path + '/goal', path)]
    assert _inventory() == before
    return {'schema': 'native-suggestion-timeout-unknown-proof-v130', 'task_id': task['id'],
        'task_path': task_path, 'suggestion_id': row['id'], 'memory_ids': {'suggestion': row['id'],
            'goal_outcome': original['outcome']['id']}, 'commands': [saved['replay']],
        'readonly_requests': [saved['recovery']], 'readbacks': readbacks,
        'cloud_call_id': clouds[0]['id'], 'original_acceptance_hash': original['acceptance_hash'],
        'send_count': len(sent), 'real_paid_calls': 0}


def test_actual_http_timeout_remains_unknown_and_readonly_recovery_never_resends(client, monkeypatch):
    proof = run_timeout_unknown_journey(client, monkeypatch)
    assert proof['send_count'] == 1 and proof['readbacks'][-1]['response']['status'] == 'response_unknown'


def test_actual_response_flush_failure_keeps_unknown_without_automatic_repayment(client, monkeypatch):
    _, task, _, row, command, original, sent, initial_cloud_ids = _start(client, monkeypatch)
    fault = {'fired': False}

    def fail_first_raw_flush(session, context, instances):
        if fault['fired']: return
        for item in session.dirty:
            if isinstance(item, Memory) and item.id == row['id'] and item.data.get('raw_response'):
                fault['fired'] = True
                raise OSError('synthetic one-shot failure saving the actual provider response')

    event.listen(Session, 'before_flush', fail_first_raw_flush)
    try: old.run(row)
    finally: event.remove(Session, 'before_flush', fail_first_raw_flush)
    assert fault['fired'] and len(sent) == 1
    unknown = old.get(client, task, row); _unknown(unknown)
    clouds = _new_clouds(initial_cloud_ids); assert len(clouds) == 1 and clouds[0]['status'] == 'succeeded'
    assert clouds[0]['charged_tokens'] == 91 and _origin(task['id']) == original
    _saved_start_only(client, task, unknown, command, sent, 1)
    before = _inventory()
    denied = client.post(old.url(task) + '/' + row['id'] + '/retry', json=old.action(unknown))
    assert denied.status_code == 409 and _inventory() == before and len(sent) == 1
