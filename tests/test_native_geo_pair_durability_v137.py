"""Root-owned real RGB pair, modern data, human work and recovery checkpoint.

The fixture creates tiny public synthetic GeoTIFFs. Raster processing, workflow,
PG, event reception and human APIs are real; no extractor/model stub is used.
"""
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.worker import process_once, deliver_events
from generic_agents.models import CloudCall, Entity, Event, GeoPair, Invocation, Job, Memory, Observation
from tests.test_native_receipts import setup_geo, bridge
from tests.test_geo_codec import scenes, pytestmark
from tests.test_geo_pairs import pairs
from tests.test_instance_workspace import complete


def test_real_engineering_pair_reception_human_draft_and_completion_persist(client, pairs, account):
    a, b, release, creation, base, task = setup_geo(client, pairs)
    with Session(engine()) as db:
        queued = db.scalar(select(Job).where(Job.kind == 'sync_native_receipts',
            Job.data['original_task_id'].as_string() == task['id']))
        assert queued is not None and queued.status == 'queued'
        queue_id = queued.id
    assert process_once(queue_id, deliver=False)
    deliver_events()
    with Session(engine()) as db:
        assert db.get(Job, queue_id).status == 'succeeded'
    listing = client.get(base + '/native-receipts').json()
    assert len(listing['records']) == 1 and listing['records'][0]['data_received']
    saved, listing = bridge(client, base)
    assert saved['created'] == 0 and saved['already_saved'] == 1 and saved['cloud_calls'] == 0
    row = listing['records'][0]
    assert row['data_received'] and not row['integrity_error']
    packet = row['record']
    assert packet['source_key'].startswith('GeoPair:')
    assert packet['candidate_only'] and packet['entity_state_updated'] is False
    assert packet['attributes']['差异候选像素数'] == 6
    assert packet['date_basis'] == 'A_original_source_request'
    reception = row['data_reception']
    assert reception['schema'] == 'instance-native-geo-pair-data-reception-v1'
    assert reception['declaration_status'] == 'not_declared' and reception['declared_outputs'] == []
    assert reception['original_declared_outputs'] and reception['candidate_only'] is True
    assert reception['learned_model'] is False
    assert reception['source_time_verified'] is False and reception['physical_state_verified'] is False
    assert reception['can_execute'] is False and reception['can_delete'] is False
    run_id = packet['source_key'].split(':', 1)[1]
    with Session(engine()) as db:
        native = db.get(Observation, row['id'])
        event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + native.id))
        memories = list(db.scalars(select(Memory).where(
            Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence')))
        assert event.status == 'received' and event.data['invocation_id'] is None
        assert native.observed_at is None and len(memories) == 1 and memories[0].status == 'candidate'
        assert memories[0].data['data_reception'] == reception
        assert len(list(db.scalars(select(Invocation)))) == 1
        assert not list(db.scalars(select(CloudCall)))
        run = db.get(GeoPair, run_id)
        assert run.data['result']['learned_model'] is False
        original_packet = json.dumps(native.data['native_packet'], sort_keys=True, ensure_ascii=False)
        original_result_hash = run.data['result_hash']
        event_id, memory_id = event.id, memories[0].id
    draft = {'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
        'note': '已核对A/B授权窗口，差异仅为工程候选，现场变化仍待查证。'}
    response = client.put('/api/workspace/tasks/' + task['id'] + '/note', json=draft)
    assert response.status_code == 200, response.text[:600]
    task = response.json()
    readback = client.get('/api/workspace/tasks/' + task['id']).json()
    assert readback['workflow_runtime']['drafts'][draft['step_id']]['note'] == draft['note']
    repeat, second = bridge(client, base)
    assert repeat['created'] == 0 and repeat['already_saved'] == 1
    assert second['records'][0]['record']['review_state'] == 'draft_saved'
    response = complete(client, task, note=draft['note'])
    assert response.status_code == 200, response.text[:600]
    task = response.json()
    assert task['status'] == 'workflow_completed'
    final_sync, final = bridge(client, base)
    assert final_sync['created'] == 0 and final_sync['already_saved'] == 1
    assert final['records'][0]['data_received'] and not final['records'][0]['integrity_error']
    assert final['records'][0]['record']['review_state'] == 'confirmed'
    export_path = base + '/native-receipts/' + row['id'] + '/export'
    response = client.get(export_path)
    assert response.status_code == 200, response.text[:600]
    with Session(engine()) as db:
        assert db.get(GeoPair, run_id).data['result_hash'] == original_result_hash
        assert json.dumps(db.get(Observation, row['id']).data['native_packet'], sort_keys=True, ensure_ascii=False) == original_packet
        assert len(list(db.scalars(select(Invocation)))) == 1 and not list(db.scalars(select(CloudCall)))
        assert db.get(Entity, creation['entity_ids'][0]).version == 1
        assert db.get(Memory, memory_id).data['data_reception'] == reception
    destination = os.environ.get('GA_V137_NATIVE_PROOF_OUT')
    if destination:
        from generic_agents.config import settings
        paths = ['/api/workspace/tasks/' + task['id'], base + '/native-receipts', export_path,
            '/api/workspace/tasks/' + task['id'] + '/comparisons/' + run_id + '/export']
        readbacks = []
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, response.text[:600]
            readbacks.append({'path': path, 'text': response.text})
        proof = {'schema': 'native-geo-pair-durable-checkpoint-v137', 'synthetic_only': True,
            'account': {'username': account[0], 'password': account[1], 'workspace_id': account[2]},
            'instance_id': release['instance_id'], 'release_id': release['id'], 'task_id': task['id'],
            'pair_id': run_id, 'observation_id': row['id'], 'event_id': event_id, 'memory_id': memory_id,
            'saved_draft': draft, 'automatic_native_sync_job_id': queue_id, 'automatic_native_sync_verified': True, 'result_hash': original_result_hash, 'readbacks': readbacks,
            'storage_dir': str(settings().storage_dir), 'real_provider_calls_delta': 0,
            'learned_model': False, 'actual_browser_verified': False, 'production_ready': False}
        path = Path(destination)
        assert path.name.endswith('.native-checkpoint.private.json') and not path.exists()
        path.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True)); path.chmod(0o600)
