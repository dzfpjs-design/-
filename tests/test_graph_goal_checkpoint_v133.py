"""Root-only frontend encoder -> API/PG capture before synthetic fixture teardown.

This is an explicitly enabled durable capture, never a browser/model-quality
test. The inherited supplier/document seams are synthetic and cannot spend.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import GoalSpec, digest
from generic_agents.db import engine
from generic_agents.models import Invocation, Task, Job
from generic_agents.worker import process_once
from tests.test_typed_payload_journey_v131 import _canonical, _typed_number
from tests.test_frontend_typed_journey_v132 import (
    typed_graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    run_frontend_typed_journey, frontend,
)
from tests.test_observation_submission_checkpoint_v128 import (
    _owned_database, _dump_database, _tree_snapshot, _private_dir,
    _json_private, _file_sha, _json_response,
)


@pytest.mark.skipif(not os.environ.get('GA_V133_CAPTURE_DIR'), reason='Root-owned checkpoint only')
def test_capture_graph_goal_guard_durability_v133(typed_graph_seed, tmp_path, tmp_path_factory, account):
    capture = Path(os.environ['GA_V133_CAPTURE_DIR'])
    assert capture.is_absolute() and capture.name == 'full-semantic-graph-v133' and capture.parent.name == '.runtime'
    _private_dir(capture); assert capture.resolve(strict=True) == capture
    temporary = tmp_path_factory.getbasetemp().resolve(strict=True)
    assert re.fullmatch(r'pytest-\d+', temporary.name) and temporary.parent == capture / 'source-runtime'
    marker = temporary.parent / 'ROOT_OWNED.json'
    assert marker.stat().st_mode & 0o777 == 0o600
    assert json.loads(marker.read_text()) == {'schema': 'v133-owned-source-runtime', 'synthetic_only': True, 'purpose': 'v133-durable-checkpoint'}
    url, comment = _owned_database()
    client = typed_graph_seed['client']
    journey = run_frontend_typed_journey(typed_graph_seed, number=-0.0)
    me = _json_response(client.get('/api/me')); journey['workspace_id'] = me['workspace_id']
    journey['guarded_tasks_v133'] = []

    def original_goal_and_input(command, task_path):
        task_value = _json_response(client.get(task_path))
        goal_value = _json_response(client.get(task_path + '/goal'))
        expected = GoalSpec.model_validate(command['goal_spec']).model_dump()
        inputs = task_value['graph_runtime']['inputs']
        assert _canonical({key: inputs[key] for key in command['graph_inputs']}) == _canonical(command['graph_inputs'])
        assert _canonical(goal_value['goal']) == _canonical(expected)
        _typed_number(task_value['graph_runtime']['inputs']['typed_number'], -0.0)
        _typed_number(next(c for c in goal_value['goal']['criteria'] if c['id'] == 'value')['expected'], -0.0)
        with Session(engine()) as db:
            saved = db.get(Task, task_value['id'])
            assert _canonical(saved.data['goal_spec']) == _canonical(expected)
            assert saved.data['goal_hash'] == digest(expected)
            records = [{'entity_id': obj['id'], 'type': obj['type_name'], 'version': obj['version'],
                'attributes': {field['label']: obj['attributes'][field['key']]
                    for field in obj['fields'] if field['key'] in obj['attributes']}}
                for obj in saved.data['object_snapshots']]
        expected_inputs = {**command['graph_inputs'], 'object_records': records}
        assert _canonical(inputs) == _canonical(expected_inputs)
        journey['guarded_tasks_v133'].append({'task_path': task_path, 'goal_path': task_path + '/goal',
            'exact_creation': _canonical(command), 'expected_inputs': expected_inputs,
            'expected_goal': expected, 'goal_hash': digest(expected)})

    with Session(engine()) as db:
        completed = db.get(Task, journey['task_id'])
        assert completed.data.get('graph_goal_reservations_v133'), 'Actual graph call must retain its original reservation'
        before_calls = set(db.scalars(select(Invocation.id)))
    # A second real Task uses the same confirmed graph, objects and typed input,
    # but explicitly has no permission for a new ability call. This is a backend
    # command, not an additional frontend/browser quality claim.
    zero = json.loads(journey['exact_task'])
    zero['request_id'] = str(uuid4())
    zero['title'] = 'V133零调用额度原任务，持久保留阻塞与输入'
    zero['goal_spec']['max_tool_calls'] = 0
    zero_exact = _canonical(zero)
    with Session(engine()) as db:
        instance_id = db.get(Task, journey['task_id']).data['instance_id']
    create_path = '/api/workspace/instances/' + instance_id + '/tasks'
    created = client.post(create_path, content=zero_exact, headers={'Content-Type': 'application/json'})
    assert created.status_code == 201, created.text[:300]
    blocked_task = _json_response(created, status=201)
    blocked_path = '/api/workspace/tasks/' + blocked_task['id']
    original_goal_and_input(zero, blocked_path)
    job_id = next(iter(blocked_task['graph_jobs'].values()))['job_id']
    assert process_once(job_id, deliver=False)
    with Session(engine()) as db:
        assert set(db.scalars(select(Invocation.id))) == before_calls
        saved = db.get(Task, blocked_task['id'])
        assert not saved.data['workflow_runtime']['receipts']
        assert db.get(Job, job_id).status in {'failed', 'blocked', 'cancelled'}
        original_creation = {'request_id': zero['request_id'], 'actor_id': saved.owner_id,
            'instance_id': instance_id, 'release_id': zero['release_id'],
            'business_id': zero['business_id'], 'task_id': saved.id,
            'creation_hash': saved.data['creation_hash']}
        original_actor = saved.owner_id
    for path in (blocked_path, blocked_path + '/goal', blocked_path + '/goal/export'):
        journey['readbacks'].append({'path': path, 'response': _json_response(client.get(path))})
    lookup_path = '/api/workspace/instances/' + instance_id + '/task-requests/lookup'
    lookup = client.post(lookup_path, content=zero_exact, headers={'Content-Type': 'application/json'})
    assert lookup.status_code == 200, lookup.text[:300]
    lookup_value = _json_response(lookup)
    assert lookup_value == {'schema': 'workflow-task-request-recovery-v1', 'found': True,
        'request': original_creation, 'retirement': None}
    journey['readonly_requests'].append({'path': lookup_path, 'exact_body': zero_exact, 'response': lookup_value})
    journey['zero_budget_task_id'] = blocked_task['id']
    journey['zero_budget_no_new_invocation'] = True
    limited = json.loads(journey['exact_task'])
    limited['request_id'] = str(uuid4())
    limited['title'] = 'V133原目标步骤额度用尽后仍保存人工草稿'
    limited['goal_spec']['max_steps'] = 1
    created = client.post(create_path, content=_canonical(limited), headers={'Content-Type': 'application/json'})
    assert created.status_code == 201, created.text[:300]
    limited_task = _json_response(created, status=201)
    limited_path = '/api/workspace/tasks/' + limited_task['id']
    original_goal_and_input(limited, limited_path)
    assert process_once(next(iter(limited_task['graph_jobs'].values()))['job_id'], deliver=False)
    limited_task = _json_response(client.get(limited_path))
    assert limited_task['graph_goal_gate_v133']['reason'] == 'max_steps'
    human = next(row for row in limited_task['graph_pending_humans'] if row['node_id'] == 'a')
    fresh = {'activation_id': human['activation_id'], 'version': human['version'],
        'cancel_epoch': limited_task['graph_runtime']['cancel_epoch'], 'request_id': str(uuid4())}
    forbidden = frontend({'mode': 'human', 'schema': human['output_schema'], 'body': fresh, 'raw': '-0.0'})
    assert client.post(limited_path + '/graph/complete', content=forbidden,
        headers={'Content-Type': 'application/json'}).status_code == 409
    goal = _json_response(client.get(limited_path + '/goal'))
    stopped = client.post(limited_path + '/goal/stop', json={'request_id': str(uuid4()),
        'expected_version': goal['version'], 'reason': '停止原目标后保留并继续保存人工草稿'})
    assert stopped.status_code == 200, stopped.text[:300]
    # Draft save is intentionally available after the original goal is stopped.
    # No completion, ability dispatch or continuation is implied by saving it.
    draft = {**fresh, 'request_id': str(uuid4()), 'draft': '原步骤额度已用尽；这份人工说明必须在停止、重载和重启后仍保留。'}
    saved = client.put(limited_path + '/graph/draft', json=draft)
    assert saved.status_code == 200, saved.text[:300]
    limited_task = _json_response(saved)
    assert limited_task['graph_goal_gate_v133']['status'] == 'stopped'
    assert next(row for row in limited_task['graph_pending_humans'] if row['node_id'] == 'a')['draft'] == draft['draft']
    draft_path = limited_path + '/graph/requests/' + draft['request_id']
    recovered_draft = _json_response(client.get(draft_path))
    expected_draft_body = {'activation_id': draft['activation_id'], 'draft': draft['draft'],
        'actor_id': original_actor, 'expected_version': draft['version'], 'cancel_epoch': draft['cancel_epoch']}
    assert recovered_draft['schema'] == 'workflow-graph-request-recovery-v1'
    assert recovered_draft['found'] is True and recovered_draft['task']['id'] == limited_task['id']
    assert recovered_draft['request'] == {'request_id': draft['request_id'], 'op': 'draft',
        'actor_id': original_actor, 'body_hash': hashlib.sha256(_canonical(expected_draft_body).encode()).hexdigest()}
    for path in (limited_path, limited_path + '/goal', limited_path + '/goal/export',
            draft_path):
        journey['readbacks'].append({'path': path, 'response': _json_response(client.get(path))})
    journey['stopped_goal_draft_task_id'] = limited_task['id']
    journey['stopped_goal_draft_saved'] = True
    # Only original idempotent commands and saved read-only recovery probes.
    # Registration/validation/publication and live previews are not replayed.
    cold = []
    for item in journey['readonly_requests']:
        response = client.post(item['path'], content=item['exact_body'],
            headers={'Content-Type': 'application/json'})
        cold.append({'path': item['path'], 'exact_body': item['exact_body'], 'status': 200,
            'response': _json_response(response)})
    journey['cold_requests'] = cold
    for item in journey['readbacks']:
        item['response'] = _json_response(client.get(item['path']))
    run = capture / ('run-' + str(uuid4())); _private_dir(run, create=True)
    storage = settings().storage_dir.resolve(strict=True)
    assert not capture.is_relative_to(storage) and not storage.is_relative_to(capture)
    assert not capture.is_relative_to(temporary)
    storage_snapshot, external_snapshot = _tree_snapshot(storage), _tree_snapshot(temporary)
    storage_backup, external_backup = run / 'evidence', run / 'external-synthetic-runtime'
    shutil.copytree(storage, storage_backup, symlinks=True, copy_function=shutil.copy2)
    shutil.copytree(temporary, external_backup, symlinks=True, copy_function=shutil.copy2)
    assert _tree_snapshot(storage_backup) == storage_snapshot and _tree_snapshot(external_backup) == external_snapshot
    database, dump = _dump_database(run, url)
    assert _tree_snapshot(storage) == storage_snapshot and _tree_snapshot(temporary) == external_snapshot
    value = {'schema': 'typed-payload-durable-checkpoint-v133', 'synthetic_only': True, 'actual_browser': False,
        'account': {'username': account[0], 'password': account[1], 'workspace_id': account[2]}, 'me': me,
        'source_db': {'database': url.database, 'host': url.host, 'port': url.port, 'role': url.username,
            'storage': str(storage), 'comment': comment},
        'journeys': [journey], 'database_snapshot': database, 'storage_snapshot': storage_snapshot,
        'external': {'original': str(temporary), 'backup': str(external_backup), 'snapshot': external_snapshot},
        'dump_path': str(dump), 'dump_sha256': _file_sha(dump), 'storage_backup': str(storage_backup),
        'real_provider_calls_delta': 0, 'frontend_source_encoder_verified': True, 'browser_verified': False}
    checkpoint = run / 'checkpoint.json'; _json_private(checkpoint, value)
    pointer = capture / os.environ.get('GA_V133_CAPTURE_POINTER', 'latest-v1.json')
    assert pointer.parent == capture and not pointer.exists()
    _json_private(pointer, {'schema': 'typed-payload-durable-pointer-v133', 'checkpoint': str(checkpoint),
        'checkpoint_sha256': _file_sha(checkpoint)})
