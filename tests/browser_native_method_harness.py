"""Opt-in real browser/service QA of native procedural experience.

All records are explicit synthetic public registrations in a disposable ga_test
database. No cloud, model download, field device or normal account is used.
Browser decisions are performed through CUA, not manufactured by this fixture.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (CloudCall, Evidence, Invocation, Job, Membership, Memory,
                                  Observation, Task, User)
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from tests.browser_mqtt_daily_harness import durable
from tests.test_instance_workspace import prepared, enable, complete
from tests.test_mqtt_supervisor_protocol import stop_child, wait_for
from tests.test_native_goals import define, drain
from tests.test_vertical_slice import manifest, publish

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/native-method-v10/browser'
ACCESS = ROOT / '.runtime/native-method-browser-access.json'
CONTROL = ROOT / '.runtime/native-method-browser-control.json'


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + '.json')).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def test_browser_native_method(client, account):
    if os.getenv('GA_NATIVE_METHOD_BROWSER') != '1':
        pytest.skip('Explicit disposable native-method browser verification only')
    assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
    release, _, _ = prepared(client)
    enable(client, release)
    kind = release['snapshot']['object_types'][0]
    field = kind['fields'][0]['key']
    source = publish(client, manifest('source', name='native_method_public_input',
        allowed_agent_roles=['perception'], execution={'adapter': 'upload'},
        specification={'timezone': 'Asia/Shanghai'}, data_policy={'cloud_allowed': False}))
    rule = publish(client, manifest('tool', name='native_method_local_rule',
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': [{'id': 'high', 'field': '公开登记值', 'operator': 'gt', 'threshold': 50}]},
        data_policy={'cloud_allowed': False}))
    tasks, objects, clocks = {}, {}, {}

    def intake(letter, number, clock):
        response = client.post('/api/workspace/instances/' + release['instance_id'] + '/reported-observations', json={
            'source_id': source['id'], 'source_record_id': 'native-method-' + letter + '-' + str(number),
            'entity_id': objects[letter]['id'], 'extractor_id': rule['id'], 'observed_at': clock.isoformat(),
            'business_day': str((clock + timedelta(hours=8)).date()), 'startup_mode': 'manual',
            'attributes': {field: objects[letter]['attributes'][field], '公开登记值': number,
                           '记录说明': '本地合成登记用于核对保存与方法复用，非现场测量'}})
        assert response.status_code == 201, response.text
        identity = response.json()['id']
        with Session(engine()) as db:
            job = db.scalar(select(Job.id).where(Job.idempotency_key == 'extract:' + identity))
        process_once(job, deliver=False)
        return identity

    for index, letter in enumerate('ABCD', 1):
        response = client.post('/api/instances/' + release['instance_id'] + '/objects', json={
            'release_id': release['id'], 'object_type': kind['key'],
            'attributes': {field: '独立公开登记' + letter}})
        assert response.status_code == 201, response.text
        objects[letter] = response.json()
        clocks[letter] = datetime.now(timezone.utc) - timedelta(seconds=3)
        observation_id = intake(letter, index, clocks[letter])
        response = client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
            'release_id': release['id'], 'business_id': release['available_business_ids'][0],
            'entity_ids': [objects[letter]['id']], 'title': '方法复用核查任务' + letter, 'request_id': str(uuid4())})
        assert response.status_code == 201, response.text
        task = response.json()
        spec = {'purpose': '核对本任务独立的公开登记与本地处理回执，保留当前依据（非现场验证）',
            'entity_ids': [objects[letter]['id']], 'required_fields': [field],
            'subgoals': [{'id': 'check', 'description': '完成当次资料核查', 'criterion_ids': ['enough', 'human']}],
            'criteria': [{'id': 'enough', 'kind': 'evidence_sufficient', 'description': '当次资料达到原要求'},
                         {'id': 'human', 'kind': 'human_confirmation', 'description': '正式人工验收'}],
            'evidence': {'source': 'observations', 'minimum_verification': 'confirmed',
                         'time': {'mode': 'latest', 'max_age_seconds': 86400, 'timezone': 'Asia/Shanghai'}},
            'observation_mode': 'existing_only'}
        base = '/api/workspace/tasks/' + task['id'] + '/goal'
        define(client, base, {'request_id': str(uuid4()), 'expected_version': task['version'],
                            'reason': '固定本地合成核查目标，不执行设备', 'goal_spec': spec})
        task = client.get('/api/workspace/tasks/' + task['id']).json()
        for _ in range(2):
            response = complete(client, task, note='已保存原流程资料核对；待浏览器核查当前登记并正式验收')
            assert response.status_code == 200, response.text
            task = response.json()
        drain(task)
        tasks[letter] = {'id': task['id'], 'title': task['title'], 'observation_id': observation_id}

    with Session(engine()) as db:
        originals = {}
        for letter, task in tasks.items():
            observation = db.get(Observation, task['observation_id'])
            assert observation.status == 'processed'
            # Human-reported JSON is frozen in the database, not fabricated as
            # a disk Evidence file. Verify that actual original and invocation.
            original = observation.data['original_request']
            content = json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
            assert sha256(content).hexdigest() == observation.data['input_hash'] == digest(original)
            invocation = db.get(Invocation, observation.data['invocation_id'])
            assert invocation.status == 'succeeded' and invocation.data['output'] == observation.data['extracted']
            originals[letter] = {'sha256': observation.data['input_hash'], 'bytes': len(content),
                'storage_kind': 'database_frozen_original_json', 'actual_invocation': invocation.id,
                'disk_evidence_files': len(observation.data.get('evidence_ids', []))}
            ids = observation.data.get('evidence_ids', [])
            for identity in ids:
                evidence = db.get(Evidence, identity)
                content = (settings().storage_dir / evidence.storage_key).read_bytes()
                assert sha256(content).hexdigest() == evidence.sha256
        assert len({row['sha256'] for row in originals.values()}) == 4
        viewer_name, viewer_password = 'viewer_' + uuid4().hex[:10], uuid4().hex
        viewer = User(username=viewer_name, password_hash=password_hash(viewer_password))
        db.add(viewer); db.flush()
        db.add(Membership(user_id=viewer.id, workspace_id=account[2], role='viewer')); db.commit()

    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); api_port = bound.getsockname()[1]
    url = 'http://localhost:' + str(api_port)
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'GA_ALLOWED_ORIGINS': url,
        'GA_COOKIE_SECURE': 'false', 'GA_DATABASE_URL': settings().database_url,
        'GA_STORAGE_DIR': str(settings().storage_dir),
        'GA_SETUP_BUILD_DIR': str(ROOT / '.runtime/builds/native-method'),
        'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': ''}
    children, logs = [], []

    def start(command):
        log = (ROOT / ('.runtime/native-method-browser-' + command + '.log')).open('ab')
        logs.append(log)
        args = [sys.executable, '-m', 'generic_agents.cli', command]
        if command == 'serve': args += ['--host', '127.0.0.1', '--port', str(api_port)]
        child = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log)
        children.append(child)
        if command == 'serve':
            def ready():
                try: return httpx.get(url + '/health', timeout=1, trust_env=False).is_success
                except httpx.HTTPError: return False
            wait_for(ready, [child], timeout=15, description='disposable native-method API')
        return child

    api = worker = None
    try:
        api, worker = start('serve'), start('setup-worker')
        with os.fdopen(os.open(ACCESS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
            json.dump({'url': url, 'username': account[0], 'password': account[1],
                'viewer_username': viewer_name, 'viewer_password': viewer_password,
                'workspace_id': account[2], 'instance_id': release['instance_id'], 'tasks': tasks}, file)
        write('prepared', {'actual_api_ready': True, 'disposable_database': True,
            'cookie_host_isolated_from_normal_app': True, 'instance_id': release['instance_id'],
            'tasks': tasks, 'independent_originals': originals, 'synthetic_local_registration': True,
            'field_devices': False, 'model_quality': 'not_evaluated'})
        sequence, api_offline = 0, False
        deadline = time.monotonic() + 5400
        while time.monotonic() < deadline:
            assert api_offline or api.poll() is None, 'disposable API exited unexpectedly'
            try: command = json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            except json.JSONDecodeError: time.sleep(.2); continue
            if command.get('sequence', 0) <= sequence: time.sleep(.2); continue
            sequence, action = command['sequence'], command['command']
            if action == 'offline':
                stop_child(worker); before = durable(); stop_child(api); api_offline = True
                write('offline', {'actual_api_stopped': True, 'before': before})
            elif action == 'restart':
                stop_child(worker); before = durable(); stop_child(api); api = start('serve'); api_offline = False
                after = durable(); assert after == before
                write('restarted', {'actual_api_restart': True, 'all_table_digests_identical': True,
                                    'before': before, 'after': after, 'sequence': sequence})
                worker = start('setup-worker')
            elif action == 'basis-change':
                identity = intake('C', 33, clocks['C'] + timedelta(milliseconds=500))
                def current_registration():
                    goal = client.get('/api/workspace/tasks/' + tasks['C']['id'] + '/goal').json()
                    return goal if any(r['id'] == identity for r in goal.get('reviewable_records', [])) else None
                wait_for(current_registration,
                    [api, worker], timeout=20, description='new independent C registration visible')
                write('new-current-input', {'observation_id': identity,
                    'old_observation_id': tasks['C']['observation_id'], 'awaiting_human_verification': True,
                    'minimum_verification_unchanged': 'confirmed', 'sequence': sequence})
            elif action == 'verify-basis-change':
                identity = json.loads((OUT / 'new-current-input.json').read_text())['observation_id']
                with Session(engine()) as db:
                    observation = db.get(Observation, identity)
                    assert observation.data['human_verification']['decision'] == 'confirmed', 'New input not yet actually human verified'
                experience = client.get('/api/workspace/tasks/' + tasks['C']['id'] + '/experience')
                assert experience.status_code == 200, experience.text
                use = experience.json()['use']
                assert use and not use['all_confirmed']
                assert any(item.get('confirmation', {}).get('stale') for item in use['items'] if item.get('confirmation'))
                write('basis-changed', {'observation_id': identity, 'old_observation_id': tasks['C']['observation_id'],
                    'new_current_input': True, 'old_confirmation_stale': True,
                    'use_hash': use['use_hash'], 'current_basis_hash': use['current_basis_hash'], 'sequence': sequence})
            elif action == 'finish':
                with Session(engine()) as db:
                    case = db.scalar(select(Memory).where(Memory.task_id == tasks['A']['id'],
                        Memory.data['type'].as_string() == 'native_procedure_case'))
                    assert case is not None and case.status == 'revoked'
                    for letter in ('A', 'B'):
                        task = db.get(Task, tasks[letter]['id'])
                        assert task.data.get('native_goal_acceptance'), letter + ' not formally accepted'
                    task = db.get(Task, tasks['B']['id'])
                    acceptance = task.data['native_goal_acceptance']
                    assert digest(acceptance) == task.data['native_goal_acceptance_hash']
                    proof = acceptance['experience_proof']
                    assert digest(proof) == acceptance['experience_proof_hash']
                    assert proof['case_id'] == case.id and proof['current_basis_hash'] == acceptance['basis_hash']
                    use = next(row for row in task.data['native_experience']['uses'] if row['id'] == proof['id'])
                    assert use['use_hash'] == proof['use_hash'] == digest(use['snapshot'])
                    assert proof['case_snapshot'] == use['snapshot']
                    assert proof['items'] and all(item['confirmation']['conclusion'] == 'confirmed' for item in proof['items'])
                    for item in proof['items']:
                        assert item['current_refs'] and all(ref['entity_id'] == objects['B']['id'] for ref in item['current_refs'])
                        assert item['confirmation']['basis_hash'] == proof['current_basis_hash']
                        assert item['confirmation']['use_hash'] == proof['use_hash']
                    receipt = db.get(Memory, task.data['native_goal_data_receipt_id'])
                    assert receipt.data['acceptance'] == acceptance
                    assert task.data['native_experience']['feedback'], 'B feedback not actually saved'
                    c = db.get(Task, tasks['C']['id'])
                    state = c.data['native_experience']
                    assert state['confirmations'] and state['disengagements']
                    assert (OUT / 'basis-changed.json').exists() and json.loads((OUT / 'basis-changed.json').read_text())['old_confirmation_stale']
                    d = db.get(Task, tasks['D']['id'])
                    assert not d.data.get('native_goal_acceptance') and not d.data.get('native_experience', {}).get('uses')
                    revoked_case = {'case_id': case.id, 'expected_case_version': case.version, 'content_hash': case.data['content_hash']}
                    assert not list(db.scalars(select(CloudCall)))
                d_view = client.get('/api/workspace/tasks/' + tasks['D']['id'] + '/experience')
                assert d_view.status_code == 200, d_view.text
                assert not d_view.json()['available_cases']
                refused = client.post('/api/workspace/tasks/' + tasks['D']['id'] + '/experience/adopt', json={
                    'request_id': str(uuid4()), 'expected_version': d_view.json()['version'],
                    'reason': '核对撤回方法不能用于新任务，不改变原标准',
                    'basis_hash': d_view.json()['current_basis_hash'], **revoked_case})
                assert refused.status_code == 409, refused.text
                write('withdrawal-new-task-refused', {'status': refused.status_code, 'detail': refused.json()['detail'],
                    'D_no_active_use': True, 'D_not_accepted': True})
                write('verification', {'classification': 'verified_progress', 'full_goal': 'active',
                    'A_B_formal_acceptance': True, 'case_revoked': True, 'cloud_calls': 0,
                    'B_formal_consumption_proof_bidirectional': True, 'C_stale_then_explicit_disengagement': True,
                    'B_feedback_saved_after_acceptance': True,
                    'D_new_adoption_refused': True, 'D_not_accepted': True,
                    'actual_api_and_worker_processes': True, 'browser_proof': 'separate_CUA_evidence_required',
                    'independent_originals': len(originals), 'original_storage': 'database_frozen_original_json',
                    'model_quality': False, 'production_ready': False})
                return
            else: raise AssertionError('Unknown fixture control command')
        raise AssertionError('Browser native-method verification timed out; no acceptance claimed')
    finally:
        for child in reversed(children): stop_child(child)
        for log in logs: log.close()
        ACCESS.unlink(missing_ok=True); CONTROL.unlink(missing_ok=True)
