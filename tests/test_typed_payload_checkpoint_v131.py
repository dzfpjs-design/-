"""Root-only real API/PG snapshot before synthetic fixture teardown.

This is an explicitly enabled durable capture, never a browser/model-quality
test. The inherited supplier/document seams are synthetic and cannot spend.
"""
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import pytest

from generic_agents.config import settings
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_typed_graph_goal_journey_v131 import (
    typed_graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    run_typed_graph_goal_journey,
)
from tests.test_observation_submission_checkpoint_v128 import (
    _owned_database, _dump_database, _tree_snapshot, _private_dir,
    _json_private, _file_sha, _json_response,
)


@pytest.mark.skipif(not os.environ.get('GA_V131_CAPTURE_DIR'), reason='Root-owned checkpoint only')
def test_capture_actual_typed_goal_and_experience(typed_graph_seed, tmp_path, tmp_path_factory, account):
    capture = Path(os.environ['GA_V131_CAPTURE_DIR'])
    assert capture.is_absolute() and capture.name == 'full-semantic-graph-v131' and capture.parent.name == '.runtime'
    _private_dir(capture); assert capture.resolve(strict=True) == capture
    temporary = tmp_path_factory.getbasetemp().resolve(strict=True)
    assert re.fullmatch(r'pytest-\d+', temporary.name) and temporary.parent == capture / 'source-runtime'
    marker = temporary.parent / 'ROOT_OWNED.json'
    assert marker.stat().st_mode & 0o777 == 0o600
    assert json.loads(marker.read_text()) == {'schema': 'v131-owned-source-runtime', 'synthetic_only': True, 'purpose': 'v131-durable-checkpoint'}
    url, comment = _owned_database()
    client = typed_graph_seed['client']
    journey = run_typed_graph_goal_journey(typed_graph_seed, tmp_path, number=-0.0)
    me = _json_response(client.get('/api/me')); journey['workspace_id'] = me['workspace_id']
    # Only original idempotent commands and saved read-only recovery probes.
    # Registration/validation/publication and live previews are not replayed.
    cold = []
    for item in [journey['creation'], journey['acceptance'], *journey['readonly_requests']]:
        status = 201 if item is journey['creation'] else 200
        response = client.post(item['path'], content=canonical_json(item['body']),
            headers={'Content-Type': 'application/json'})
        cold.append({'path': item['path'], 'body': item['body'], 'status': status,
            'response': _json_response(response, status=status)})
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
    value = {'schema': 'typed-payload-durable-checkpoint-v131', 'synthetic_only': True, 'actual_browser': False,
        'account': {'username': account[0], 'password': account[1], 'workspace_id': account[2]}, 'me': me,
        'source_db': {'database': url.database, 'host': url.host, 'port': url.port, 'role': url.username,
            'storage': str(storage), 'comment': comment},
        'journeys': [journey], 'database_snapshot': database, 'storage_snapshot': storage_snapshot,
        'external': {'original': str(temporary), 'backup': str(external_backup), 'snapshot': external_snapshot},
        'dump_path': str(dump), 'dump_sha256': _file_sha(dump), 'storage_backup': str(storage_backup),
        'real_provider_calls_delta': 0, 'frontend_numeric_input_verified': False}
    checkpoint = run / 'checkpoint.json'; _json_private(checkpoint, value)
    pointer = capture / os.environ.get('GA_V131_CAPTURE_POINTER', 'latest-v1.json')
    assert pointer.parent == capture and not pointer.exists()
    _json_private(pointer, {'schema': 'typed-payload-durable-pointer-v131', 'checkpoint': str(checkpoint),
        'checkpoint_sha256': _file_sha(checkpoint)})
