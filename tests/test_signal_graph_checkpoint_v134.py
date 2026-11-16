"""Root-only frontend source bridge -> actual routing -> durable PG capture.

The first Task completes the real three-criterion formal Goal. The second is
issued by the actual frontend builder, executes a real local builtin, and keeps
its original human draft after the route is paused. This is a source encoder
and backend/storage proof, never a browser or professional-model quality claim.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.contracts import GoalSpec, digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Invocation, Job, Task
from generic_agents.worker import process_once
from tests.test_signal_graph_routing_v134 import (
    routing_scene, waiting_release, graph_seed, v7_scene, reading_scene,
    local_runtime, full_source_scene, run_signal_graph_journey, report, deliver,
    task_detail, pause, path, wire, reply, effects, human, negative_zero,
)
from tests.test_typed_payload_journey_v131 import _canonical
from tests.test_observation_submission_checkpoint_v128 import (
    _owned_database, _dump_database, _tree_snapshot, _private_dir,
    _json_private, _file_sha, _json_response, _database_snapshot,
)


def frontend(packet):
    """Only Root executes its explicit offline Node bridge against real source."""
    bridge = Path(os.environ['GA_V134_SIGNAL_BRIDGE'])
    node = Path(os.environ['GA_V115_NODE'])
    assert bridge.is_absolute() and bridge.is_file() and node.is_absolute() and node.is_file()
    environment = {**os.environ, 'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': '',
        'QWEN_TOKENPLAN_API_KEY': '', 'OPENAI_API_KEY': ''}
    completed = subprocess.run([str(node), str(bridge)], input=_canonical(packet),
        text=True, capture_output=True, timeout=30, env=environment)
    assert completed.returncode == 0, completed.stderr[-1200:]
    assert 0 < len(completed.stdout) < 2 * 1024 * 1024
    value = json.loads(completed.stdout)
    assert type(value) is dict and set(value) == {'exact_body', 'command'}
    assert type(value['exact_body']) is str and len(value['exact_body']) < 1024 * 1024
    original = json.loads(value['exact_body'])
    # command is a JSON-only display mirror; its 0 may stand for -0.0. The
    # original exact_body remains the sole request sent to the server.
    assert original == value['command']
    negative_zero(original['graph_inputs']['route_numeric'])
    assert '"route_numeric":-0.0' in value['exact_body']
    return value


def _scope(value, me):
    return {'actor_id': me['user_id'], 'workspace_id': me['workspace_id'],
        'instance_id': value['release']['instance_id']}


def _expect_task(value, identity, command, receipt):
    """Derive expected inputs/Goal from the issued command and real snapshots."""
    current = task_detail(value, identity)
    listing = wire(value['client'].get(path(value)))
    route = next(row for row in listing['routes'] if row['id'] == receipt['route_id'])
    with Session(engine()) as db:
        saved = db.get(Task, identity)
        assert saved.owner_id == value['actor_id']
        assert len(saved.data['entity_ids']) == 1
        trigger = saved.data['entity_ids'][0]
        assert trigger in command['entity_ids']
        records = [{'entity_id': obj['id'], 'type': obj['type_name'], 'version': obj['version'],
            'attributes': {field['label']: copy.deepcopy(obj['attributes'][field['key']])
                for field in obj['fields'] if field['key'] in obj['attributes']}}
            for obj in saved.data['object_snapshots']]
        expected_inputs = {**copy.deepcopy(command['graph_inputs']),
            **{field: trigger for field in command['entity_input_fields']}, 'object_records': records}
        goal = copy.deepcopy(command['goal_spec'])
        goal.update(entity_ids=[trigger], deadline=route['expires_at'])
        expected_goal = GoalSpec.model_validate(goal).model_dump()
        assert _canonical(saved.data['graph_runtime']['inputs']) == _canonical(expected_inputs)
        assert _canonical(saved.data['goal_spec']) == _canonical(expected_goal)
        assert saved.data['goal_hash'] == digest(expected_goal)
        assert saved.data['native_goal_context']['goal_snapshot']['spec'] == expected_goal
        negative_zero(saved.data['graph_runtime']['inputs']['route_numeric'])
        origin = copy.deepcopy(saved.data['signal_graph_origin_v134'])
    assert _canonical(current['graph_runtime']['inputs']) == _canonical(expected_inputs)
    negative_zero(current['graph_runtime']['inputs']['route_numeric'])
    return {'task_path': '/api/workspace/tasks/' + identity,
        'goal_path': '/api/workspace/tasks/' + identity + '/goal',
        'expected_inputs': expected_inputs, 'expected_goal': expected_goal,
        'goal_hash': digest(expected_goal), 'origin': origin, 'route_id': receipt['route_id']}


def _readonly_effects():
    """Cover every actual public table, including Delivery and Signal ledgers."""
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            snapshot = _database_snapshot(connection)
    assert {'signal_graph_deliveries_v134', 'perception_signals'} <= set(snapshot['tables'])
    return snapshot


@pytest.mark.skipif(not os.environ.get('GA_V134_CAPTURE_DIR'), reason='Root-owned checkpoint only')
def test_capture_actual_signal_graph_routing_and_frontend_original_request_v134(
        routing_scene, tmp_path_factory, account):
    capture = Path(os.environ['GA_V134_CAPTURE_DIR'])
    assert capture.is_absolute() and capture.name == 'full-semantic-graph-v134' and capture.parent.name == '.runtime'
    _private_dir(capture); assert capture.resolve(strict=True) == capture
    temporary = tmp_path_factory.getbasetemp().resolve(strict=True)
    assert re.fullmatch(r'pytest-\d+', temporary.name) and temporary.parent == capture / 'source-runtime'
    marker = temporary.parent / 'ROOT_OWNED.json'
    assert marker.stat().st_mode & 0o777 == 0o600
    assert json.loads(marker.read_text()) == {'schema': 'v134-owned-source-runtime',
        'synthetic_only': True, 'purpose': 'v134-durable-checkpoint'}
    url, comment = _owned_database()
    value = routing_scene; client = value['client']; me = _json_response(client.get('/api/me'))
    scope = _scope(value, me)
    with Session(engine()) as db: cloud_before = set(db.scalars(select(CloudCall.id)))

    first = run_signal_graph_journey(value)
    assert {item['kind'] for item in first['grant_command']['goal_spec']['criteria']} == {
        'evidence_sufficient', 'human_confirmation', 'capability_success'}
    assert first['acceptance']['response']['acceptance']['evaluation']['completed']
    first_pause_body, first_pause = pause(value, {'receipt': first['grant_receipt']})
    expected_first = _expect_task(value, first['task_id'], first['grant_command'], first['grant_receipt'])

    options_http = client.get(path(value) + '/options')
    options = wire(options_http)
    assert options['can_enable']
    business = next(row for row in options['businesses'] if row['id'] == value['business_id'])
    assert business['available'] and 'watched_entity' in business['entity_input_fields']
    assert 'route_numeric' in business['input_metadata']['user_input_schema']['properties']
    negative_zero(business['input_metadata']['input_schema']['properties']['route_numeric']['minimum'])
    field = first['grant_command']['goal_spec']['required_fields'][0]
    selections = {'source_id': value['source']['id'], 'extractor_id': value['extractor']['id'],
        'entity_ids': [value['object']['id']], 'business_id': value['business_id'],
        'fields': [field], 'entity_input_fields': ['watched_entity'],
        'numeric_field': 'route_numeric', 'numeric_raw': '-0.0',
        'purpose': '按明确原对象和完整图核对随后登记，保留人工验收及原数值',
        'max_steps': '12', 'max_tool_calls': '12', 'max_tasks': '10',
        'reason': '明确同意所显示本机完整流程，不能外发、补采或自动更新事实'}
    encoded_preview = frontend({'mode': 'preview', 'scope': scope,
        'options': options_http.json(), 'selections': selections})
    preview_command = json.loads(encoded_preview['exact_body'])
    assert preview_command['schema'] == 'signal-graph-routing-preview-command-v134'
    assert preview_command['entity_ids'] == selections['entity_ids']
    assert preview_command['entity_input_fields'] == ['watched_entity']
    assert preview_command['graph_inputs'].keys() == {'route_numeric'}
    assert preview_command['goal_spec']['observation_mode'] == 'existing_only'
    assert preview_command['goal_spec']['deadline'] is None
    actual_preview_http = client.post(path(value) + '/preview', content=encoded_preview['exact_body'],
        headers={'Content-Type': 'application/json'})
    actual_preview = wire(actual_preview_http)
    negative_zero(actual_preview['expanded'][0]['graph_inputs']['route_numeric'])
    request_id = str(uuid4())
    encoded_enable = frontend({'mode': 'enable', 'scope': scope,
        'preview': actual_preview_http.json(), 'original_command': encoded_preview['exact_body'],
        'request_id': request_id})
    enable_command = json.loads(encoded_enable['exact_body'])
    assert enable_command['schema'] == 'signal-graph-routing-enable-command-v134'
    assert enable_command['request_id'] == request_id
    assert enable_command['preview_hash'] == actual_preview['preview_hash']
    assert enable_command['confirm_graph_execution'] is True
    enabled_http = client.post(path(value) + '/enable', content=encoded_enable['exact_body'],
        headers={'Content-Type': 'application/json'})
    enabled = wire(enabled_http, 201)
    assert enabled['receipt']['request_id'] == request_id
    assert wire(client.get(path(value) + '/requests/' + request_id))['result']['receipt'] == enabled['receipt']

    second_observation, second_observation_body, second_processing_job, second_signal = report(value)
    second_delivery = deliver(value, second_signal)
    assert second_delivery['status'] == 'created' and second_delivery['task_id'] != first['task_id']
    second = task_detail(value, second_delivery['task_id'])
    negative_zero(second['graph_runtime']['inputs']['route_numeric'])
    graph_job = next(iter(second['graph_jobs'].values()))['job_id']
    assert process_once(graph_job, deliver=False)
    with Session(engine()) as db:
        job = db.get(Job, graph_job); assert job.status == 'succeeded'
        call = db.scalar(select(Invocation).where(Invocation.workspace_id == scope['workspace_id'],
            Invocation.idempotency_key == job.data['invocation_key']))
        assert call is not None and call.status == 'succeeded' and call.data['job_id'] == job.id
        second_invocation = call.id
        saved = db.get(Task, second['id'])
        receipt = next(row for row in saved.data['workflow_runtime']['receipts']
            if row['execution_mode'] == 'registered_graph_ability')
        assert receipt['invocation_id'] == call.id and receipt['output'] == call.data['output']
        assert call.data['output']['provenance'] == [{'entity_id': value['object']['id'],
            'version': value['object']['version']}]
    second_pause_body, second_pause = pause(value, enabled)
    second = task_detail(value, second['id'])
    assert second['signal_graph_route_v134']['status'] == 'paused'
    before_calls = effects()['invocations']
    second, draft = human(client, second, 'a', draft=True)
    assert second['signal_graph_route_v134']['status'] == 'paused'
    assert next(row for row in second['graph_pending_humans'] if row['node_id'] == 'a')['draft'] == {'ok': True}
    assert not second['completed'] and not second.get('native_goal_acceptance')
    assert effects()['invocations'] == before_calls
    expected_second = _expect_task(value, second['id'], enable_command, enabled['receipt'])
    draft_path = '/api/workspace/tasks/' + second['id'] + '/graph/requests/' + draft['request_id']
    recovered_draft = _json_response(client.get(draft_path))
    assert recovered_draft['schema'] == 'workflow-graph-request-recovery-v1'
    assert recovered_draft['found'] is True and recovered_draft['task']['id'] == second['id']
    assert recovered_draft['request'] == {'request_id': draft['request_id'], 'op': 'draft',
        'actor_id': scope['actor_id'], 'body_hash': digest({'activation_id': draft['activation_id'],
            'draft': draft['draft'], 'actor_id': scope['actor_id'],
            'expected_version': draft['version'], 'cancel_epoch': draft['cancel_epoch']})}

    journey = {'schema': 'signal-graph-checkpoint-journey-v134', 'workspace_id': scope['workspace_id'],
        'instance_id': value['release']['instance_id'], 'release_id': value['release']['id'],
        'task_id': first['task_id'], 'paused_draft_task_id': second['id'],
        'signal_graph_tasks_v134': [expected_first, expected_second],
        'formal_goal': first, 'frontend': {'scope': scope, 'selections': selections,
            'options': options_http.json(), 'preview_exact_body': encoded_preview['exact_body'],
            'preview_response': actual_preview_http.json(), 'enable_exact_body': encoded_enable['exact_body'],
            'enable_response': enabled_http.json()},
        'second_delivery': {'id': second_delivery['id'], 'signal_id': second_signal,
            'task_id': second['id'], 'observation_id': second_observation['id'],
            'observation_body': second_observation_body, 'processing_job_id': second_processing_job,
            'graph_job_id': graph_job, 'invocation_id': second_invocation, 'draft_command': draft},
        'readbacks': [], 'cold_requests': [], 'frontend_source_encoder_verified': True,
        'strong_original_goal_accepted': True, 'paused_original_draft_saved': True,
        'physical_state_verified': False, 'entity_state_updated': False, 'actual_browser': False}

    # Retain only saved original commands for cold recovery. Live preview,
    # source registration and graph execution never become restart actions.
    cold = [
        {'path': path(value) + '/enable', 'exact_body': _canonical(first['grant_command']), 'status': 201},
        {'path': path(value) + '/' + first['grant_receipt']['route_id'] + '/pause',
            'exact_body': _canonical(first_pause_body), 'status': 200},
        {'path': path(value) + '/enable', 'exact_body': encoded_enable['exact_body'], 'status': 201},
        {'path': path(value) + '/' + enabled['receipt']['route_id'] + '/pause',
            'exact_body': _canonical(second_pause_body), 'status': 200},
    ]
    original_acceptance = first['acceptance']
    cold.append({'path': '/api/workspace/tasks/' + first['task_id'] + '/goal/requests/recover',
        'exact_body': _canonical({'operation': 'accept', 'original_request': original_acceptance['body'],
            'expected_version': task_detail(value, first['task_id'])['version'],
            'reason': '只核对原正式验收是否已经保存，不发起新评估', 'action': 'probe'}), 'status': 200})
    unchanged = _readonly_effects()
    for request in cold:
        response = client.post(request['path'], content=request['exact_body'],
            headers={'Content-Type': 'application/json'})
        request['response'] = _json_response(response, status=request['status'])
        journey['cold_requests'].append(request)
    assert _readonly_effects() == unchanged
    assert journey['cold_requests'][-1]['response']['saved']
    read_paths = [path(value), path(value) + '/options',
        path(value) + '/requests/' + first['grant_command']['request_id'],
        path(value) + '/requests/' + first_pause_body['request_id'],
        path(value) + '/requests/' + request_id,
        path(value) + '/requests/' + second_pause_body['request_id'], draft_path,
        '/api/workspace/instances/' + value['release']['instance_id'] + '/signals',
        '/api/workspace/instances/' + value['release']['instance_id'] + '/signals/' + first['signal_id'] + '/export',
        '/api/workspace/instances/' + value['release']['instance_id'] + '/signals/' + second_signal + '/export']
    for task_record in journey['signal_graph_tasks_v134']:
        read_paths.extend([task_record['task_path'], task_record['goal_path'], task_record['goal_path'] + '/export'])
    for endpoint in read_paths:
        journey['readbacks'].append({'path': endpoint, 'response': _json_response(client.get(endpoint))})
    assert _readonly_effects() == unchanged
    with Session(engine()) as db:
        assert set(db.scalars(select(CloudCall.id))) == cloud_before
        assert len(list(db.scalars(select(Task).where(Task.workspace_id == scope['workspace_id'],
            Task.data['instance_id'].as_string() == value['release']['instance_id'])))) == 2

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
    checkpoint_value = {'schema': 'signal-graph-durable-checkpoint-v134', 'synthetic_only': True,
        'actual_browser': False, 'browser_verified': False,
        'account': {'username': account[0], 'password': account[1], 'workspace_id': account[2]}, 'me': me,
        'source_db': {'database': url.database, 'host': url.host, 'port': url.port, 'role': url.username,
            'storage': str(storage), 'comment': comment},
        'journeys': [journey], 'database_snapshot': database, 'storage_snapshot': storage_snapshot,
        'external': {'original': str(temporary), 'backup': str(external_backup), 'snapshot': external_snapshot},
        'dump_path': str(dump), 'dump_sha256': _file_sha(dump), 'storage_backup': str(storage_backup),
        'real_provider_calls_delta': 0, 'frontend_source_encoder_verified': True}
    checkpoint = run / 'checkpoint.json'; _json_private(checkpoint, checkpoint_value)
    pointer = capture / os.environ.get('GA_V134_CAPTURE_POINTER', 'latest-v1.json')
    assert pointer.parent == capture and not pointer.exists()
    _json_private(pointer, {'schema': 'signal-graph-durable-pointer-v134', 'checkpoint': str(checkpoint),
        'checkpoint_sha256': _file_sha(checkpoint)})
