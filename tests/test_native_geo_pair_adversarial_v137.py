"""Real RGB engineering comparisons; adversarial reads never create authority.

Root alone executes this on its owned PostgreSQL/runtime. Both sibling calls
below are produced by the public Task API and actual worker. Only already
issued synthetic rows/files are corrupted, then restored byte for byte.
These tests make no learned-model, imagery-quality or physical-state claim.
"""
import copy
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.geo_pair_codec import RECIPE_HASH
from generic_agents.instance_data_receipts import observation_receipt
from generic_agents.native_geo_pair_provenance_v137 import checked_pair, checked_observation
from generic_agents.security import Principal
from tests.test_instance_workspace import complete
from tests.test_native_receipts import setup_geo, bridge, pairs, scenes, pytestmark
from tests.test_workflow_abilities import finish_job


def good(response, status=200):
    assert response.status_code == status, response.text[:800]
    return response.json()


def principal(client):
    me = good(client.get('/api/me'))
    return Principal(me['user_id'], me['workspace_id'], me['role'], 'v137-adversarial-history')


def inventory(workspace):
    with Session(engine()) as db:
        return {cls.__name__: [(row.id, getattr(row, 'status', None), getattr(row, 'version', None), digest(row.data))
            for row in db.scalars(select(cls).where(cls.workspace_id == workspace).order_by(cls.id))]
            for cls in (m.GeoPair, m.Job, m.Invocation, m.CloudCall, m.Audit)}


@contextmanager
def preserved(rows):
    """Restore the actual issued rows, never replace a failed check with success."""
    with Session(engine()) as db:
        originals = [(cls, identity, {key: copy.deepcopy(getattr(db.get(cls, identity), key)) for key in keys})
            for cls, identity, keys in rows]
    try:
        yield
    finally:
        with Session(engine()) as db:
            for cls, identity, values in originals:
                row = db.get(cls, identity)
                for key, value in values.items():
                    setattr(row, key, value)
            db.commit()


def scene(client, pairs):
    a, b, release, body, base, task = setup_geo(client, pairs)
    saved, listing = bridge(client, base)
    assert saved['created'] == 1 and saved['cloud_calls'] == 0
    receipt = task['workflow_runtime']['receipts'][0]
    pair_id = receipt['output']['geo_pair_id']
    item = next(row for row in listing['records'] if row.get('record', {}).get('source_key') == 'GeoPair:' + pair_id)
    assert item['data_received'] and not item['integrity_error']
    p = principal(client)
    with Session(engine()) as db:
        observation = db.get(m.Observation, item['id'])
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
            m.Event.idempotency_key == 'data:' + observation.id))
        assert event is not None and event.status == 'received'
        assert observation_receipt(db, p, observation)['data_reception'] == observation.data['data_reception']
        packet = copy.deepcopy(observation.data['native_packet'])
        original_entity_version = db.get(m.Entity, body['entity_ids'][0]).version
    return {'a': a, 'b': b, 'release': release, 'body': body, 'base': base, 'task': task,
        'pair_id': pair_id, 'observation_id': item['id'], 'event_id': event.id, 'packet': packet,
        'principal': p, 'entity_version': original_entity_version}


def cold(client, value):
    p = value['principal']
    with Session(engine()) as db:
        context = checked_pair(db, p, value['pair_id'])
        assert context['pair'].id == value['pair_id'] and context['task'].id == value['task']['id']
        observation = db.get(m.Observation, value['observation_id'])
        checked_observation(db, p, observation)
        assert observation.data['native_packet'] == value['packet']
        actual = observation_receipt(db, p, observation)
        assert actual['data_reception'] == observation.data['data_reception']
        assert db.get(m.Entity, value['body']['entity_ids'][0]).version == value['entity_version']
    exported = good(client.get(value['base'] + '/native-receipts/' + value['observation_id'] + '/export'))
    assert not exported['observation']['integrity_error'] and exported['observation']['data_received']
    assert exported['cloud_calls'] == 0 and exported['entity_state_updated'] is False


def broken(client, value, *, pair_invalid=True):
    p = value['principal']; before = inventory(p.workspace_id)
    with Session(engine()) as db:
        if pair_invalid:
            with pytest.raises(HTTPException) as caught:
                checked_pair(db, p, value['pair_id'])
            assert caught.value.status_code == 409
        with pytest.raises(HTTPException) as caught:
            checked_observation(db, p, db.get(m.Observation, value['observation_id']))
        assert caught.value.status_code == 409
    assert client.get(value['base'] + '/native-receipts/' + value['observation_id'] + '/export').status_code == 409
    assert inventory(p.workspace_id) == before


def reseal_packet(db, value, pair):
    """Adversarially re-seal mutable JSON; all independent Audits stay original."""
    observation = db.get(m.Observation, value['observation_id'])
    event = db.get(m.Event, value['event_id'])
    packet = copy.deepcopy(observation.data['native_packet'])
    machine = {column.name: getattr(pair, column.name) for column in pair.__table__.columns
        if column.name not in {'data', 'version', 'created_at'} and not column.info.get('typed_payload_internal')}
    machine['data'] = {key: item for key, item in pair.data.items()
        if key not in {'review', 'review_draft', 'review_history', 'reviewed_at'}}
    packet.update(source_key='GeoPair:' + pair.id, source_machine_hash=digest(machine),
        task_id=pair.data['workflow_task_id'], evidence_ids=[item['evidence_id'] for item in pair.data['sources']],
        source_sha256=[item['sha256'] for item in pair.data['sources']],
        source_times=[{'value': item['configuration'].get('observed_at'),
            'basis': 'user_declared_unverified' if item['configuration'].get('observed_at') else 'unknown'}
            for item in pair.data['sources']])
    observation.source_record_id = packet['source_key'] + ':' + observation.entity_id
    observation.processing_version = digest(packet)[:40]
    observation.data = {**observation.data, 'native_packet': packet, 'native_packet_hash': digest(packet),
        'extracted': {'attributes': packet['attributes']}, 'evidence_ids': packet['evidence_ids']}
    event.data = {**event.data, 'native_receipt': packet, 'extracted': observation.data['extracted'],
        'evidence_ids': packet['evidence_ids']}


def test_another_real_task_job_and_invocation_cannot_replace_an_issued_observation(client, pairs):
    value = scene(client, pairs); p = value['principal']
    body = {**value['body'], 'request_id': str(uuid4()), 'title': '第二次真实RGB工程对照，不替代原归集'}
    sibling = finish_job(client, good(client.post(value['base'] + '/tasks', json=body), 201))
    second = sibling['workflow_runtime']['receipts'][0]
    with Session(engine()) as db:
        pair = db.get(m.GeoPair, second['output']['geo_pair_id'])
        job = db.get(m.Job, pair.data['workflow_job_id'])
        call = db.get(m.Invocation, second['invocation_id'])
        assert pair.id != value['pair_id'] and pair.status == 'succeeded'
        assert job.status == 'succeeded' and job.data['task_id'] == sibling['id']
        assert call.status == 'succeeded' and call.data['output']['geo_pair_id'] == pair.id
        assert checked_pair(db, p, pair.id)['task'].id == sibling['id']
        sibling_pair = pair.id
    before = inventory(p.workspace_id)
    with preserved([(m.Observation, value['observation_id'], ('data', 'source_record_id', 'processing_version')),
                    (m.Event, value['event_id'], ('data',))]):
        with Session(engine()) as db:
            reseal_packet(db, value, db.get(m.GeoPair, sibling_pair))
            db.commit()
        # The complete alternative processing chain is genuine. Only the
        # original issuance cannot be reassigned to it by re-sealing JSON.
        broken(client, value, pair_invalid=False)
    assert inventory(p.workspace_id) == before
    cold(client, value)
    accepted = good(complete(client, value['task'], note='已核对原A/B工程结果，未确认现场变化'))
    assert accepted['status'] == 'workflow_completed'
    cold(client, value)


def test_changed_actual_invocation_lease_cannot_keep_a_valid_issued_receipt(client, pairs):
    value = scene(client, pairs); p = value['principal']
    with Session(engine()) as db:
        original = checked_pair(db, p, value['pair_id'])
        call_id = original['call'].id
        old_token = original['call'].data['lease_token']
        assert original['call'].status == 'succeeded' and old_token
    before = inventory(p.workspace_id)
    with preserved([(m.Invocation, call_id, ('data',))]):
        with Session(engine()) as db:
            call = db.get(m.Invocation, call_id)
            replacement = str(uuid4())
            assert replacement != old_token
            call.data = {**call.data, 'lease_token': replacement}
            db.commit()
        # Job, original output, packet and all independent Audits stay exact.
        # A merely nonempty token cannot authenticate the original dispatch.
        broken(client, value)
    assert inventory(p.workspace_id) == before
    cold(client, value)


@pytest.mark.parametrize('change', ['ordered_a_b', 'b_allowed_window', 'threshold_alignment_runtime'])
def test_jointly_resealed_input_parameters_do_not_replace_the_original_execution(client, pairs, change):
    value = scene(client, pairs); p = value['principal']
    with Session(engine()) as db:
        pair = db.get(m.GeoPair, value['pair_id'])
        artifacts = [item['evidence_id'] for item in pair.data['result']['artifacts'].values()]
    rows = [(m.GeoPair, value['pair_id'], ('data', 'before_scene_id', 'after_scene_id')),
        (m.Task, value['task']['id'], ('data',)),
        (m.Observation, value['observation_id'], ('data', 'source_record_id', 'processing_version')),
        (m.Event, value['event_id'], ('data',))] + [(m.Evidence, identity, ('data',)) for identity in artifacts]
    before = inventory(p.workspace_id)
    with preserved(rows):
        with Session(engine()) as db:
            pair = db.get(m.GeoPair, value['pair_id']); data = copy.deepcopy(pair.data)
            task = db.get(m.Task, value['task']['id']); saved = copy.deepcopy(task.data)
            if change == 'ordered_a_b':
                saved['comparison_scene_snapshots'].reverse()
                data['sources'].reverse(); data['input_refs'].reverse()
                pair.before_scene_id, pair.after_scene_id = pair.after_scene_id, pair.before_scene_id
                data['request']['roi'] = list(saved['comparison_scene_snapshots'][0]['snapshot']['roi'])
                data['after_allowed_window'] = list(saved['comparison_scene_snapshots'][1]['snapshot']['roi'])
            elif change == 'b_allowed_window':
                old = list(data['after_allowed_window'])
                data['after_allowed_window'] = list(saved['comparison_scene_snapshots'][0]['snapshot']['roi'])
                assert data['after_allowed_window'] != old  # The fixture actually restricts B.
            else:
                data['request']['threshold'] = 1 if data['request']['threshold'] != 1 else 2
                data['request']['alignment'] = ('same_grid' if data['request']['alignment'] != 'same_grid'
                    else 'reproject_after_to_before')
                assert data['runtime_hash'] != RECIPE_HASH
                data['runtime_hash'] = RECIPE_HASH  # A real different legacy recipe, not an invalid hash.
            data['sources_hash'] = digest(data['sources'])
            inputs = {'task_id': task.id, 'scene_refs': copy.deepcopy(data['input_refs'])}
            job = db.get(m.Job, data['workflow_job_id'])
            data['request_hash'] = digest(inputs)
            receipt = task.data['workflow_runtime']['receipts'][0]
            data['workflow_scope'] = digest({'scope': job.data['scope'],
                'capability_hash': receipt['capability_hash'], 'inputs': inputs})
            data['result']['sources_hash'] = data['sources_hash']
            data['result']['runtime_hash'] = data['runtime_hash']
            for key in ('threshold', 'alignment'):
                if key in data['result']: data['result'][key] = data['request'][key]
            data['result_hash'] = digest(data['result'])
            task.data = saved; pair.data = data
            for identity in artifacts:
                evidence = db.get(m.Evidence, identity)
                evidence.data = {**evidence.data, 'sources_hash': data['sources_hash']}
            reseal_packet(db, value, pair)
            db.commit()
        broken(client, value)
    assert inventory(p.workspace_id) == before
    cold(client, value)


@pytest.mark.parametrize('damaged', ['original', 'artifact'])
def test_actual_original_or_artifact_bytes_cannot_enter_a_cold_modern_receipt(client, pairs, damaged):
    value = scene(client, pairs); p = value['principal']
    with Session(engine()) as db:
        pair = db.get(m.GeoPair, value['pair_id'])
        identity = (value['a']['evidence_id'] if damaged == 'original'
            else pair.data['result']['artifacts']['candidate']['evidence_id'])
        evidence = db.get(m.Evidence, identity)
        path = settings().storage_dir / evidence.storage_key
    original = path.read_bytes(); before = inventory(p.workspace_id)
    try:
        path.write_bytes(b'owned synthetic v137 corruption; not a replacement result')
        broken(client, value)
    finally:
        path.write_bytes(original)
    assert inventory(p.workspace_id) == before
    cold(client, value)
