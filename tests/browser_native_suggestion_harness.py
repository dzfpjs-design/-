"""Opt-in disposable real cloud/worker/browser method-suggestion verification.

All inputs are independent synthetic archives. Source acceptance is preseeded
for A; B and C original workflows are ready, not accepted. Cloud, suggestion
decisions and subsequent acceptance must be separately evidenced through CUA.
"""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

from dotenv import dotenv_values
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (CloudCall, Entity, Job, Membership, Memory,
                                  Task, User)
from generic_agents.instance_compiler import signature_value
from generic_agents.security import password_hash
from tests.browser_mqtt_daily_harness import durable
from tests.test_instance_workspace import configuration, save, enable
from tests.test_mqtt_supervisor_protocol import stop_child, wait_for
from tests.test_native_cases import ready, accept
from tests.test_native_goals import define
from tests.test_vertical_slice import publish, manifest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/native-suggestion-v11/browser'
ACCESS = ROOT / '.runtime/native-suggestion-browser-access.json'
CONTROL = ROOT / '.runtime/native-suggestion-browser-control.json'


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + '.json')).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def seed(client):
    c = configuration()
    c['instanceProfile']['goal'] = 'NMS_PRIVATE_BUSINESS_NOTE_本地业务说明'
    for flow in c['flows'].values():
        flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    revision = save(client, c, review_automation=True)
    member = client.get('/api/instances/members').json()[0]
    body = {'revision': revision, 'role_bindings': {'核验负责人': member['id']},
            'objects': [{'name': 'NMS_PRIVATE_OBJECT_TYPE', 'fields': [
                {'label': 'NMS_PRIVATE_FIELD_LABEL', 'kind': '文本', 'required': True},
                {'label': 'NMS_PRIVATE_NUMBER_LABEL', 'kind': '整数', 'required': True}]}]}
    check = client.post('/api/instances/check', json=body)
    assert check.status_code == 200, check.text
    result = client.post('/api/instances', json={**body,
        'confirmed_configuration_hash': check.json()['configuration_hash'],
        'confirm_configuration': True})
    assert result.status_code == 201, result.text
    release = result.json(); enable(client, release)
    kind = release['snapshot']['object_types'][0]
    keys = [item['key'] for item in kind['fields']]
    tasks, objects = {}, {}
    for index, letter in enumerate('ABC', 1):
        response = client.post('/api/instances/' + release['instance_id'] + '/objects', json={
            'release_id': release['id'], 'object_type': kind['key'],
            'attributes': {keys[0]: 'NMS_PRIVATE_ATTRIBUTE_' + letter, keys[1]: index * 17}})
        assert response.status_code == 201, response.text
        obj = response.json(); objects[letter] = obj
        response = client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
            'release_id': release['id'], 'business_id': release['available_business_ids'][0],
            'entity_ids': [obj['id']], 'title': 'NMS_PRIVATE_TASK_TITLE_' + letter,
            'request_id': str(uuid4())})
        assert response.status_code == 201, response.text
        task = response.json()
        spec = {'purpose': 'NMS_PRIVATE_GOAL_TEXT_保留本地原档案', 'entity_ids': [obj['id']],
            'required_fields': keys,
            'subgoals': [{'id': 'check', 'description': 'NMS_PRIVATE_SUBGOAL_NOTE',
                         'criterion_ids': ['enough', 'human']}],
            'criteria': [{'id': 'enough', 'kind': 'evidence_sufficient',
                          'description': 'NMS_PRIVATE_CRITERION_TEXT'},
                         {'id': 'human', 'kind': 'human_confirmation',
                          'description': '正式人工验收'}],
            'evidence': {'source': 'archive', 'minimum_verification': 'recorded',
                         'time': {'mode': 'archive', 'timezone': 'Asia/Shanghai'}},
            'observation_mode': 'existing_only'}
        define(client, '/api/workspace/tasks/' + task['id'] + '/goal', {
            'request_id': str(uuid4()), 'expected_version': task['version'],
            'reason': '固定独立合成档案目标，不代表现场测量', 'goal_spec': spec})
        ready(client, task)
        if letter == 'A':
            response = accept(client, task)
            assert response.status_code == 200, response.text
        tasks[letter] = {'id': task['id'], 'entity_id': obj['id'], 'fields': keys}
    model = publish(client, manifest('foundation_model', name='native_method_qwen_qa',
        allowed_agent_roles=['business'],
        execution={'provider': 'qwen', 'model': 'qwen-plus',
                   'secret_ref': 'DASHSCOPE_API_KEY', 'max_tokens': 2048},
        data_policy={'cloud_allowed': True, 'redact_fields': []}))
    return release, tasks, objects, model


def test_browser_native_suggestion(client, account):
    if os.getenv('GA_NATIVE_SUGGESTION_BROWSER') != '1':
        pytest.skip('Explicit disposable actual-cloud/browser verification only')
    assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
    assert not list((OUT / 'cloud-http').glob('request-*.json')), 'Archive previous attempts first'
    key = dotenv_values('.env').get('DASHSCOPE_API_KEY')
    assert key, 'Actual authorized Qwen key must exist; no fake cloud fallback'
    release, tasks, objects, model = seed(client)
    with Session(engine()) as db:
        viewer_name, viewer_password = 'viewer_' + uuid4().hex[:10], uuid4().hex
        viewer = User(username=viewer_name, password_hash=password_hash(viewer_password))
        db.add(viewer); db.flush()
        db.add(Membership(user_id=viewer.id, workspace_id=account[2], role='viewer')); db.commit()
        # Freeze a fair, independent structural baseline before any cloud output.
        assert not db.scalar(select(CloudCall.id))
        archives = {letter: {'entity_hash': digest(db.get(Entity, task['entity_id']).data),
                              'task_id': task['id'], 'entity_id': task['entity_id']}
                    for letter, task in tasks.items()}
    freeze = {'classification': 'synthetic_structural_preregistration',
        'source_A': archives['A'], 'holdout_B_C': [archives[x] for x in 'BC'],
        'baseline': [{'kind': 'field_verification', 'field': tasks['A']['fields'][0]},
                     {'kind': 'field_verification', 'field': tasks['A']['fields'][1]},
                     {'kind': 'units', 'field': tasks['A']['fields'][1]}],
        'independent_truth': {'field_verification': 'both current archive values require checking',
            'units': 'numeric units were not declared; do not fabricate comparability or a measurement unit',
            'source_time': 'archive has no declared measurement time; do not fabricate freshness',
            'comparability': 'independent archive identity does not establish comparable measurements'},
        'equal_template_budget': 5, 'representative_data': False,
        'measured_business_benefit': False,
        'evaluation_rule': 'Report raw omission, unsupported addition, unknown incorrectly confirmed and current acceptance safety; edits separate from raw result.'}
    write('evaluation-freeze', {**freeze, 'freeze_hash': digest(freeze)})
    forbidden = [account[2], release['id'], release['instance_id'], model['id']]
    for task in tasks.values(): forbidden += [task['id'], task['entity_id']]
    forbidden += [row['entity_hash'] for row in archives.values()]
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
    url = 'http://localhost:' + str(port)
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'GA_ALLOWED_ORIGINS': url,
        'GA_COOKIE_SECURE': 'false', 'GA_DATABASE_URL': settings().database_url,
        'GA_STORAGE_DIR': str(settings().storage_dir),
        'GA_SETUP_BUILD_DIR': str(ROOT / '.runtime/builds/native-suggestion'),
        'DASHSCOPE_API_KEY': key, 'DEEPSEEK_API_KEY': '',
        'GA_CLOUD_DAILY_CALL_LIMIT': '3',
        'GA_NATIVE_SUGGESTION_BROWSER': '1',
        'GA_NATIVE_SUGGESTION_PROBE_DIR': str(OUT / 'cloud-http'),
        'GA_NATIVE_SUGGESTION_FORBIDDEN_IDS': json.dumps(forbidden)}
    children, logs = [], []

    def start(command):
        log = (ROOT / ('.runtime/native-suggestion-browser-' + command + '.log')).open('ab')
        logs.append(log)
        args = [sys.executable, '-m', 'tests.native_suggestion_runtime_probe', command]
        if command == 'serve': args += ['--host', '127.0.0.1', '--port', str(port)]
        child = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log)
        children.append(child)
        if command == 'serve':
            def alive():
                try: return httpx.get(url + '/health', timeout=1, trust_env=False).is_success
                except httpx.HTTPError: return False
            wait_for(alive, [child], timeout=15, description='disposable suggestion API')
        return child

    api = worker = None
    try:
        api, worker = start('serve'), start('setup-worker')
        with os.fdopen(os.open(ACCESS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
            json.dump({'url': url, 'username': account[0], 'password': account[1],
                       'viewer_username': viewer_name, 'viewer_password': viewer_password,
                       'workspace_id': account[2], 'instance_id': release['instance_id'],
                       'tasks': tasks, 'model_id': model['id']}, file)
        write('prepared', {'actual_api_ready': True, 'disposable_database': True,
            'source_A_original_acceptance_API_preseeded': True,
            'B_C_original_workflow_API_preseeded_not_accepted': True,
            'tasks': tasks, 'instance_id': release['instance_id'],
            'actual_cloud_call_limit': 3, 'representative_model_quality': False})
        sequence, offline = 0, False
        deadline = time.monotonic() + 5400
        while time.monotonic() < deadline:
            assert offline or api.poll() is None, 'Disposable API exited unexpectedly'
            assert offline or worker.poll() is None, 'Disposable worker exited unexpectedly'
            try: command = json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            except ValueError: time.sleep(.2); continue
            if command.get('sequence', 0) <= sequence: time.sleep(.2); continue
            sequence, action = command['sequence'], command['command']
            if action == 'offline':
                stop_child(worker); stop_child(api); offline = True
                write('offline', {'sequence': sequence, 'actual_processes_stopped': True})
            elif action == 'restart':
                if not offline: stop_child(worker); stop_child(api)
                before = durable(); api, worker = start('serve'), start('setup-worker'); offline = False
                after = durable(); assert before == after
                write('restarted', {'sequence': sequence, 'all_table_digests_identical': True,
                                    'before': before, 'after': after})
            elif action == 'finish':
                before = durable()
                http_proofs = [json.loads(path.read_text()) for path in sorted((OUT / 'cloud-http').glob('request-*.json'))]
                actual_successes = [value for value in http_proofs if value.get('sent_to_real_provider') and value.get('http_status') == 200]
                assert actual_successes, 'No completed real provider response; reserved tokens are not actual calls'
                with Session(engine()) as db:
                    calls = list(db.scalars(select(CloudCall)))
                    assert 1 <= len(calls) <= 3
                    assert all(row.capability_id == model['id'] for row in calls)
                    suggestions = list(db.scalars(select(Memory).where(
                        Memory.data['type'].as_string() == 'native_procedure_suggestion')))
                    assert suggestions, 'No durable actual suggestion'
                    write('server-final', {'calls': [{'id': row.id, 'status': row.status,
                        'charged_tokens': row.charged_tokens,
                        'receipt': row.data.get('receipt', {})} for row in calls],
                        'suggestions': [{'id': row.id, 'version': row.version, 'status': row.status,
                            'task_id': row.task_id, 'data': row.data} for row in suggestions],
                        'frozen_task_acceptance_hashes': {letter: db.get(Task, task['id']).data.get(
                            'native_goal_acceptance_hash') for letter, task in tasks.items()}})
                write('verification', {'classification': 'evidence_collected_not_yet_accepted',
                    'actual_API_worker_processes': True, 'before_shutdown_tables': before,
                    'actual_successful_provider_response_count': len(actual_successes),
                    'captured_provider_attempt_count': len(http_proofs),
                    'cloud_call_record_count': len(calls), 'representative_quality': False,
                    'business_benefit': False, 'production_ready': False,
                    'requires_root_cross_check_and_CUA_acceptance': True})
                return
            else: raise AssertionError('Unknown fixture command')
        raise AssertionError('Browser suggestion verification timed out; no acceptance claimed')
    finally:
        for child in reversed(children): stop_child(child)
        for log in logs: log.close()
        ACCESS.unlink(missing_ok=True); CONTROL.unlink(missing_ok=True)
