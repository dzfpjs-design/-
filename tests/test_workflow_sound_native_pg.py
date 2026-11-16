"""Real local CPU/Task/Invocation/outbox/ACK; synthetic WAV is engineering only.

Root must prepare the fixed local environment and official cached artifacts.
The fixture does not mock model execution, installation, audits or reception.
"""
import copy
import hashlib
import io
import json
import wave
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import local_sound
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.models import Audit, Job, Invocation, Event, Observation, Memory, CloudCall, AudioRun
from generic_agents.sound_models import EnvironmentSoundRun
from generic_agents.worker import process_once, deliver_events
from generic_agents import native_sound_provenance as source
from tests.test_sound_inputs_pg import original_scene, upload, good
from tests.test_instance_workspace import configuration, prepared, enable, object_row, complete


@pytest.fixture()
def sound_scene(original_scene):
    s = original_scene
    local_sound.ready()  # A real required environment; never silently substitute/skip.
    model = good(s.client.post('/api/specialists/installations', json={
        'pack_id': local_sound.PACK, 'source': 'official', 'backend': 'cpu'}), 201)
    assert process_once(job_id=model['job_id'], deliver=False)
    models = good(s.client.get('/api/specialists/installations'))
    current = next(v for v in models if v['id'] == model['id'])
    assert current['status'] == 'ready', current.get('error')
    registered = good(s.client.post('/api/specialists/installations/' + current['id'] + '/register', json={'version': current['version']}))
    cap = next(v for v in good(s.client.get('/api/capabilities')) if v['id'] == registered['capability_id'])
    c = configuration(); flow = c['flows']['资料核验']
    flow['steps'][0].update(name='分析任务当次原音', agent='多模感知智能体', role='',
        execution={'capability_id': cap['id'], 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    c['automation']['policies']['资料核验']['rules']['observe'] = {'mode': '自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    release, _, check = prepared(s.client, c, instance_id=s.instance['id'])
    assert release['available_business_ids'], check
    current_instance = next(v for v in good(s.client.get('/api/instances')) if v['id'] == release['instance_id'])
    instance = good(s.client.post('/api/instances/' + current_instance['id'] + '/activate', json={
        'version': current_instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    entity = object_row(s.client, release)
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000); stream.writeframes(b'\0\0' * 8000)
    raw = raw.getvalue()
    body = {**s.body, 'request_id': str(uuid4()), 'size_bytes': len(raw), 'input_sha256': hashlib.sha256(raw).hexdigest(),
        'configuration': {'channel': 'mono', 'threshold': .5}}
    attached = good(upload(s, raw=raw, original=json.dumps(body, ensure_ascii=False)), 201)
    task_body = {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [entity['id']], 'sound_ids': [attached['id']], 'title': '合成原音本地任务工程验收', 'request_id': str(uuid4())}
    task = good(s.client.post('/api/workspace/instances/' + instance['id'] + '/tasks', json=task_body), 201)
    job_id = task['workflow_runtime']['automatic']['job_id']
    assert process_once(job_id=job_id, deliver=False)
    task = good(s.client.get('/api/workspace/tasks/' + task['id']))
    assert task['status'] == 'workflow_waiting', task.get('workflow_runtime', {}).get('automatic')
    view = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/sounds'))
    assert len(view['records']) == 1
    entry = view['records'][0]; run = entry['run']
    assert run['status'] == 'succeeded' and run['result']['cloud_calls'] == 0
    return SimpleNamespace(**vars(s), current_release=release, entity=entity, attached=attached,
        actual_raw=raw, task=task, run=run, entry=entry, job_id=job_id)


def ack(s):
    observation = s.entry['reception']['observation_id']; assert observation
    deliver_events(workspace_id=s.entry['run']['sound_snapshot']['workspace_id'],
        observation_id=observation, event_kind='observation.data')
    return good(s.client.get('/api/workspace/tasks/' + s.task['id'] + '/sounds'))['records'][0]


def test_real_task_keeps_scores_separate_from_independent_ack_and_candidate_human_review(sound_scene):
    s = sound_scene; result = s.run['result']; reception = s.entry['reception']
    assert reception['state'] == 'waiting_data' and reception['receipt'] is None
    assert result['classification']['all_samples_covered'] and not result['classification']['asr_used']
    assert not result['classification']['vad_used'] and len(result['classification']['windows']) == 1
    assert len(result['classification']['windows'][0]['scores']) == 521
    assert s.client.get(s.run['content_path']).content == s.actual_raw
    note = s.client.put('/api/workspace/tasks/' + s.task['id'] + '/note', json={
        'version': s.task['version'], 'step_id': 'record', 'note': 'ACK前候选说明必须保留，不能先验收。'})
    saved_task = good(note); assert complete(s.client, saved_task).status_code == 409
    review_body = {'schema': 'sound-review-v1', 'request_id': 'review-before-ack-' + uuid4().hex,
        'version': s.run['version'], 'verdict': 'uncertain', 'note': '仅核对模型窗口，不能认定现场异常。'}
    review_path = '/api/sounds/runs/' + s.run['id'] + '/reviews'
    review = good(s.client.post(review_path, json=review_body), 201)
    recovery = good(s.client.get(review_path + '/requests', params={'request_id': review_body['request_id'],
        'body_hash': digest(review_body), 'expected_version': review_body['version']}))
    assert recovery['found'] and recovery['receipt'] == review
    assert good(s.client.post(review_path, json=review_body)) == review
    received = ack(s); assert received['reception']['state'] == 'received'
    assert received['run']['result'] == result and received['run']['reviews'] == [review]
    current = good(s.client.get('/api/workspace/tasks/' + s.task['id']))
    finished = good(complete(s.client, current, note='已听原音并记录不确定；不将声音候选等同现场事实。'))
    assert finished['status'] == 'workflow_completed'
    with Session(engine()) as db:
        assert db.get(Job, s.job_id).status == 'succeeded'
        run = db.get(EnvironmentSoundRun, s.run['id']); call = db.get(Invocation, run.invocation_id)
        assert call.status == 'succeeded' and call.data['output']['sound_run_id'] == run.id
        assert len(db.scalars(select(EnvironmentSoundRun)).all()) == 1
        assert not db.scalars(select(AudioRun)).all() and not db.scalars(select(CloudCall)).all()
        event = db.get(Event, received['reception']['event_id'])
        assert event.status == 'received' and event.received_at is not None
        assert len(db.scalars(select(Memory).where(Memory.data['event_id'].as_string() == event.id)).all()) == 1


def test_unknown_review_is_only_superseded_by_a_real_monotonic_cas_advance(sound_scene):
    s = sound_scene; base = '/api/sounds/runs/' + s.run['id'] + '/reviews'
    first = {'schema': 'sound-review-v1', 'request_id': 'first-original-' + uuid4().hex,
        'version': s.run['version'], 'verdict': 'uncertain', 'note': '旧页草稿保留，不推断未保存。'}
    params = {'request_id': first['request_id'], 'body_hash': digest(first), 'expected_version': first['version']}
    unknown = good(s.client.get(base + '/requests', params=params))
    assert not unknown['found'] and not unknown['superseded'] and unknown['current_version'] == first['version']
    other = {**first, 'request_id': 'other-original-' + uuid4().hex, 'note': '另一页实际完成核查，原分数不改。'}
    receipt = good(s.client.post(base, json=other), 201)
    old = good(s.client.get(base + '/requests', params=params))
    assert not old['found'] and old['superseded'] and old['current_version'] == receipt['version']
    assert s.client.post(base, json=first).status_code == 409
    current = good(s.client.get('/api/sounds/runs/' + s.run['id']))
    assert current['reviews'] == [receipt] and current['result'] == s.run['result']


@pytest.mark.parametrize('damage', ['missing_memory', 'missing_issued_audit', 'removed_packet'])
def test_broken_independent_sound_reception_cannot_be_completed_or_silently_recreated(sound_scene, damage):
    s = sound_scene; received = ack(s); reception = received['reception']
    with Session(engine()) as db:
        observation = db.get(Observation, reception['observation_id'])
        if damage == 'missing_memory': db.delete(db.get(Memory, reception['memory_id']))
        elif damage == 'missing_issued_audit':
            db.delete(db.scalar(select(Audit).where(Audit.action == source.ISSUED_ACTION, Audit.object_id == observation.id)))
        else:
            observation.data = {k: v for k, v in observation.data.items() if k not in {source.PACKET, source.ISSUED}}
        db.commit()
    entry = good(s.client.get('/api/workspace/tasks/' + s.task['id'] + '/sounds'))['records'][0]
    assert entry['reception']['state'] == 'blocked_integrity' and entry['reception']['receipt'] is None
    current = good(s.client.get('/api/workspace/tasks/' + s.task['id']))
    assert complete(s.client, current).status_code == 409
    deliver_events(workspace_id=s.attached['workspace_id'], observation_id=reception['observation_id'], event_kind='observation.data')
    with Session(engine()) as db:
        assert len(db.scalars(select(EnvironmentSoundRun)).all()) == 1
        assert len(db.scalars(select(Observation).where(Observation.data['native_instance_id'].as_string() == s.instance['id'])).all()) == 1
