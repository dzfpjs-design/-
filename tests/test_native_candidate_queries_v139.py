"""Real received learned candidates, registered local query and human review.

Root alone executes this PostgreSQL/model suite. The inherited journey installs
through the public API and performs the resource and business neural runs. No
query/model/worker, published tool, successful call or authority is fabricated.
The small generated images prove engineering execution, not forestry accuracy.
"""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.worker import process_once, SETUP_KINDS
from tests.test_instance_workspace import complete
from tests.test_native_learned_geo_journey_v138 import actual_learned_journey, rasters, pytestmark


def good(response, status=200):
    assert response.status_code == status, response.text[:800]
    return response.json()


def query_body(value, *, field='search_text', text=None):
    return {'request_id': str(uuid4()), 'release_id': value['release']['id'],
        'entity_id': value['creation']['entity_ids'][0], 'field': field,
        'text': value['task']['title'] if text is None else text}


def query_root(value):
    return value['base'] + '/native-candidate-queries'


def original_state(value):
    """The query cannot revise its original model, business, Goal or objects."""
    with Session(engine()) as db:
        task = db.get(m.Task, value['task']['id'])
        pair = db.get(m.GeoPair, value['pair_id'])
        original_calls = list(db.scalars(select(m.Invocation).where(
            m.Invocation.capability_id == pair.data['workflow_capability_id'])))
        return {'task': digest({'title': task.title, 'status': task.status,
                'version': task.version, 'data': task.data}),
            'objects': {identity: digest({'version': db.get(m.Entity, identity).version,
                'data': db.get(m.Entity, identity).data}) for identity in value['creation']['entity_ids']},
            'pair': digest({'status': pair.status, 'version': pair.version, 'data': pair.data}),
            'model_calls': {call.id: digest({'capability_id': call.capability_id,
                'key': call.idempotency_key, 'status': call.status, 'version': call.version,
                'data': call.data}) for call in original_calls},
            'cloud': list(db.scalars(select(m.CloudCall.id)))}


def counts():
    with Session(engine()) as db:
        return {model.__name__: set(db.scalars(select(model.id)))
            for model in (m.DataRun, m.Job, m.Invocation, m.CloudCall, m.Capability)}


def checked_view(view, body):
    assert view['schema'] == 'native-candidate-query-view-v139'
    assert view['request_id'] == body['request_id'] and view['request'] == body
    assert view['not_authoritative'] is True and view['candidate_only'] is True
    assert view['truncated'] is False
    return view


def finish_query(client, value, body, queued, workspace_id):
    checked_view(queued, body)
    with Session(engine()) as db:
        row, job = db.get(m.DataRun, queued['id']), db.get(m.Job, queued['job_id'])
        assert row is not None and row.status == 'queued'
        assert job is not None and job.kind == 'data_query' and job.status == 'queued'
        assert job.data['query_id'] == row.id
        task = db.get(m.Task, value['task']['id'])
        package = db.get(m.Capability, task.package_id)
        pins = package.data['specification']['native_query_catalog']['tools']
        pin = next(item for item in pins if item['method'] == 'keyword')
        capability = db.get(m.Capability, pin['id'])
        assert capability.status == 'published' and capability.content_hash == pin['hash']
        assert capability.data['execution']['adapter'] == 'builtin'
        assert capability.data['execution']['handler'] == 'data_keyword_filter'
        cap_id, cap_hash = capability.id, capability.content_hash
    assert 'data_query' in SETUP_KINDS
    assert process_once(queued['job_id'], kinds=SETUP_KINDS, deliver=False, workspace_id=workspace_id)
    result = checked_view(good(client.get(query_root(value) + '/' + queued['id'])), body)
    assert result['status'] == 'sufficient', result
    assert result['integrity_error'] is None and result['source_changed'] is False
    with Session(engine()) as db:
        assert db.get(m.Job, queued['job_id']).status == 'succeeded'
        calls = list(db.scalars(select(m.Invocation).where(
            m.Invocation.idempotency_key == 'data-tool:' + queued['id'] + ':keyword')))
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        assert calls[0].capability_id == cap_id
        assert calls[0].data['capability_hash'] == cap_hash
        assert calls[0].data['output']['summary']['method'] == 'registered_field_keyword_filter'
        assert calls[0].data['output']['summary']['matched_records'] == 1
        assert not list(db.scalars(select(m.CloudCall)))
    return result


def assert_actual_record(result, value, review_state):
    assert len(result['records']) == 1
    row = result['records'][0]
    assert row['observation_id'] == value['observation']['id']
    assert row['entity_id'] == value['creation']['entity_ids'][0]
    assert row['task_id'] == value['task']['id'] and row['name'] == value['task']['title']
    assert row['attributes'] == value['observation']['record']['attributes']
    assert row['review_state'] == review_state
    assert row['source_time_verified'] is False and row['physical_state_verified'] is False
    with Session(engine()) as db:
        assert db.get(m.Observation, row['observation_id']).observed_at is None


def test_received_learned_candidate_query_is_once_and_review_change_requires_a_new_query(client, rasters, account):
    value = actual_learned_journey(client, rasters)
    root = query_root(value); body = query_body(value)
    original, before = original_state(value), counts()
    queued = checked_view(good(client.post(root, json=body), 201), body)
    same = checked_view(good(client.post(root, json=body), 201), body)
    assert same['id'] == queued['id'] and same['job_id'] == queued['job_id']
    assert client.post(root, json={**body, 'text': body['text'] + '改变原条件'}).status_code == 409
    lookup = good(client.get(root + '/requests/' + body['request_id'], params={'expected_hash': digest(body)}))
    assert lookup['found'] is True and checked_view(lookup['query'], body)['id'] == queued['id']
    assert client.get(root + '/requests/' + body['request_id'], params={'expected_hash': '0' * 64}).status_code == 409
    after_create = counts()
    assert after_create['DataRun'] - before['DataRun'] == {queued['id']}
    assert after_create['Job'] - before['Job'] == {queued['job_id']}
    assert after_create['Invocation'] == before['Invocation']
    result = finish_query(client, value, body, queued, account[2])
    assert_actual_record(result, value, 'waiting')
    assert original_state(value) == original
    after_run = counts()
    assert len(after_run['Invocation'] - before['Invocation']) == 1
    assert after_run['Capability'] == before['Capability'] and after_run['CloudCall'] == before['CloudCall']
    with Session(engine()) as db:
        stored_result_hash = digest(db.get(m.DataRun, queued['id']).data['result'])
    stable = counts()
    recovered = good(client.get(root + '/requests/' + body['request_id'], params={'expected_hash': digest(body)}))
    assert recovered['found'] is True and recovered['query'] == result
    assert checked_view(good(client.post(root, json=body), 201), body) == result
    assert counts() == stable, 'Original request recovery must not enqueue or invoke again'

    task = good(client.get('/api/workspace/tasks/' + value['task']['id']))
    completed = good(complete(client, task,
        note='已核对本地模型及A/B原件和候选范围；仅保存核查意见，不认定现场变化。'))
    assert completed['status'] == 'workflow_completed'
    stale = checked_view(good(client.get(root + '/' + queued['id'])), body)
    assert stale['source_changed'] is True and stale['records'] == []
    with Session(engine()) as db:
        assert digest(db.get(m.DataRun, queued['id']).data['result']) == stored_result_hash
    current = original_state(value)
    assert current['objects'] == original['objects'] and current['pair'] == original['pair']
    assert current['model_calls'] == original['model_calls'] and current['cloud'] == original['cloud']
    reviewed_body = query_body(value, field='review_status', text='已核查')
    reviewed_before = counts()
    reviewed = finish_query(client, value, reviewed_body,
        good(client.post(root, json=reviewed_body), 201), account[2])
    assert_actual_record(reviewed, value, 'confirmed')
    assert original_state(value) == current
    reviewed_after = counts()
    assert len(reviewed_after['Invocation'] - reviewed_before['Invocation']) == 1
    assert reviewed_after['Capability'] == before['Capability'] and not reviewed_after['CloudCall']
    history = good(client.get(root))
    assert history['schema'] == 'native-candidate-query-list-v139'
    assert {row['id'] for row in history['queries']} == {queued['id'], reviewed['id']}


@pytest.mark.parametrize('damaged', ['memory', 'original'])
def test_bad_received_candidate_source_blocks_creation_and_queued_tool_before_any_invocation(client, rasters, account, damaged):
    value = actual_learned_journey(client, rasters)
    body = query_body(value); root = query_root(value)
    queued = checked_view(good(client.post(root, json=body), 201), body)
    before, original = counts(), original_state(value)
    with Session(engine()) as db:
        observation = db.get(m.Observation, value['observation']['id'])
        event = db.scalar(select(m.Event).where(m.Event.idempotency_key == 'data:' + observation.id,
            m.Event.workspace_id == account[2]))
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == account[2],
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence')))
        assert len(memories) == 1 and memories[0].status == 'candidate'
        memory_id = memories[0].id
        memory_payload = {column.name: copy.deepcopy(getattr(memories[0], column.key))
            for column in m.Memory.__table__.columns
            if column.name == 'data' or column.info.get('typed_payload_internal')}
        pair = db.get(m.GeoPair, value['pair_id'])
        scene = db.get(m.GeoScene, pair.before_scene_id)
        evidence = db.get(m.Evidence, scene.evidence_id)
        path = settings().storage_dir / evidence.storage_key
        assert path.resolve().is_relative_to(settings().storage_dir.resolve())
    original_bytes = path.read_bytes()
    try:
        if damaged == 'memory':
            with Session(engine()) as db:
                memory = db.get(m.Memory, memory_id)
                changed = copy.deepcopy(memory.data)
                changed['native_receipt']['attributes']['差异候选像素数'] += 1
                memory.data = changed
                db.commit()
        else:
            path.write_bytes(b'owned synthetic V139 source corruption; not replacement evidence')
        # An invalid received family cannot be silently omitted or become an
        # empty answer. A new request is rejected atomically before queueing.
        bad_body = {**body, 'request_id': str(uuid4())}
        assert client.post(root, json=bad_body).status_code == 409
        assert counts() == before
        assert 'data_query' in SETUP_KINDS
        assert process_once(queued['job_id'], kinds=SETUP_KINDS, deliver=False, workspace_id=account[2])
        blocked = checked_view(good(client.get(root + '/' + queued['id'])), body)
        assert blocked['status'] == 'blocked_evidence'
        assert blocked['integrity_error'] and blocked['records'] == []
        after = counts()
        assert after['Invocation'] == before['Invocation'], 'Invalid original or Memory must not reach the keyword tool'
        assert after['Capability'] == before['Capability'] and after['CloudCall'] == before['CloudCall']
        assert original_state(value) == original
        with Session(engine()) as db:
            assert db.get(m.DataRun, queued['id']).status == 'blocked_evidence'
            assert db.get(m.Observation, value['observation']['id']).observed_at is None
        lookup = good(client.get(root + '/requests/' + body['request_id'], params={'expected_hash': digest(body)}))
        assert lookup['found'] is True and lookup['query'] == blocked
        assert counts() == after
    finally:
        if damaged == 'memory':
            with Session(engine()) as db:
                # Restore this owned fault byte-for-byte, including the
                # original private typed envelope, without re-signing proof.
                db.execute(m.Memory.__table__.update().where(m.Memory.id == memory_id)
                    .values(**copy.deepcopy(memory_payload)))
                db.commit()
        else:
            path.write_bytes(original_bytes)
    exported = good(client.get(value['base'] + '/native-receipts/' + value['observation']['id'] + '/export'))
    assert exported['observation']['data_received'] and exported['observation']['integrity_error'] is None
    assert original_state(value) == original
    # A fresh, explicit query after repair is permitted; the blocked original
    # is retained and is never silently replayed or changed into success.
    fresh_body = query_body(value)
    repaired = finish_query(client, value, fresh_body,
        good(client.post(root, json=fresh_body), 201), account[2])
    assert_actual_record(repaired, value, 'waiting')
    assert original_state(value) == original
    assert len(counts()['Invocation'] - before['Invocation']) == 1
    assert checked_view(good(client.get(root + '/' + queued['id'])), body)['status'] == 'blocked_evidence'

