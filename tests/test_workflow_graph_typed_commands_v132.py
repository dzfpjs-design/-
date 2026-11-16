"""Real issued graph + read-only typed human preparation (Root executes PG).

The inherited source journey explicitly synthesizes its document/provider IO;
these tests do not patch release, permission, schema, worker or ledger gates.
"""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, Invocation, Job, Memory, Task
from tests.test_typed_graph_goal_journey_v131 import (
    typed_graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    _issue, _raw,
)
from tests.test_typed_payload_journey_v131 import _canonical, _typed_number
from tests.test_report_evidence_pipeline import reply


def _inventory(task_id):
    with Session(engine()) as db:
        task = db.get(Task, task_id)
        return {'task': digest({'version': task.version, 'status': task.status, 'data': task.data}),
            **{model.__name__: sorted(db.scalars(select(model.id)))
                for model in (Job, Invocation, Memory, Audit)}}


@pytest.mark.parametrize('number', [-0.0, 1e21], ids=['negative-zero', 'large-float'])
def test_actual_typed_human_preparation_is_read_only_and_original_completion_replays(typed_graph_seed, number):
    value = _issue(typed_graph_seed, number)
    client, release = value['client'], value['release']
    body = {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [value['object']['id']], 'title': '原数值人工输入只读核对',
        'request_id': str(uuid4()), 'graph_inputs': {'typed_number': number}}
    current = _raw(client, '/api/workspace/instances/' + release['instance_id'] + '/tasks', body, 201)
    path = '/api/workspace/tasks/' + current['id']
    current = reply(client.get(path))
    human = next(item for item in current['graph_pending_humans'] if item['node_id'] == 'a')
    command = {'request_id': str(uuid4()), 'activation_id': human['activation_id'],
        'version': human['version'], 'cancel_epoch': current['graph_runtime']['cancel_epoch'],
        'output': {'ok': True, 'reading': number}}
    preparation = {'schema': 'workflow-graph-command-hash-command-v1', 'operation': 'complete', 'command': command}
    before = _inventory(current['id'])
    expected = digest({'activation_id': command['activation_id'], 'output': command['output'],
        'actor_id': reply(client.get('/api/me'))['user_id'],
        'expected_version': command['version'], 'cancel_epoch': command['cancel_epoch']})
    for _ in range(2):
        response = _raw(client, path + '/graph/command-hash', preparation)
        checked = response['value']
        assert set(checked) == {'schema', 'actor_id', 'workspace_id', 'task_id', 'graph_hash', 'operation', 'command', 'body_hash'}
        assert checked['schema'] == 'workflow-graph-command-hash-v1'
        assert checked['body_hash'] == expected and checked['task_id'] == current['id']
        assert response['canonical'] == _canonical(checked)
        _typed_number(checked['command']['output']['reading'], number)
        assert _inventory(current['id']) == before

    invalid = []
    for field in ('version', 'cancel_epoch'):
        changed = copy.deepcopy(preparation); changed['command'][field] += 1; invalid.append(changed)
    for output in ({'ok': True}, {'ok': True, 'reading': '1e21'}, {'ok': True, 'reading': number, 'undeclared': True}):
        changed = copy.deepcopy(preparation); changed['command']['output'] = output; invalid.append(changed)
    changed = copy.deepcopy(preparation); changed['command']['activation_id'] = 'not-the-original-activation'; invalid.append(changed)
    ability = next(item for item in current['graph_runtime']['activations'].values() if item['node_id'] == 'b')
    changed = copy.deepcopy(preparation); changed['command']['activation_id'] = ability['id']; invalid.append(changed)
    for changed in invalid:
        result = client.post(path + '/graph/command-hash', content=_canonical(changed), headers={'Content-Type': 'application/json'})
        assert result.status_code in (403, 409, 422), result.text[:500]
        assert _inventory(current['id']) == before

    completed = _raw(client, path + '/graph/complete', command)
    _typed_number(completed['graph_runtime']['activations'][human['activation_id']]['output']['reading'], number)
    receipt_path = path + '/graph/requests/' + command['request_id']
    found = reply(client.get(receipt_path))
    assert found['found'] and found['request']['body_hash'] == expected
    after = _inventory(current['id'])
    assert _raw(client, path + '/graph/complete', command)['graph_runtime']['state_hash'] == completed['graph_runtime']['state_hash']
    assert _inventory(current['id']) == after
    # A saved nonce is recovered, never re-prepared into a different command.
    assert client.post(path + '/graph/command-hash', content=_canonical(preparation), headers={'Content-Type': 'application/json'}).status_code == 409
    assert _inventory(current['id']) == after
    with Session(engine()) as db:
        cold = db.get(Task, current['id'])
        _typed_number(cold.data['graph_runtime']['activations'][human['activation_id']]['output']['reading'], number)
