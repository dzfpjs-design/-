"""Real two-producer PG sources and independent source storage, not NL success.

Pixel/sound producers, native CPU, SDK, published release and ACK are real;
images and silence are synthetic engineering inputs, not domain-quality proof.
The DataRun+Audit fixture below stores ONLY a source snapshot. It is explicitly
not an accepted plan, completed natural-language query or successful query SDK.
Root alone executes this file in the prepared isolated native/test environment.
"""
import copy
import hashlib
import io
import json
import wave
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents import local_sound
from generic_agents import multimodal_query_facets_v262 as subject
from generic_agents import multimodal_query_contracts_v262 as contracts
from generic_agents.sound_models import EnvironmentSoundRun, SoundInput
from generic_agents.worker import process_once, deliver_events
from tests.test_workflow_pixel_native_pg_v250 import flow_scene, native_scene, pytestmark, good
from tests.test_instance_workspace import configuration, prepared, object_row
from tests.test_workflow_abilities import enable


@pytest.fixture()
def two_sources(flow_scene):
    s = flow_scene
    # Strict O_NOFOLLOW remains production behavior; normalize only the actual
    # disposable test root, as the existing native source fixtures already do.
    root = settings().storage_dir
    assert root.name.startswith('ga-test-evidence-') and root.is_dir()
    settings().storage_dir = root.resolve(strict=True)
    model = good(s.client.post('/api/specialists/installations', json={
        'pack_id': local_sound.PACK, 'source': 'official', 'backend': 'cpu'}), 201)
    assert process_once(job_id=model['job_id'], deliver=False)
    installed = next(value for value in good(s.client.get('/api/specialists/installations')) if value['id'] == model['id'])
    assert installed['status'] == 'ready', installed.get('error')
    sound_cap = good(s.client.post('/api/specialists/installations/' + installed['id'] + '/register',
        json={'version': installed['version']}))['capability_id']
    c = configuration(); flow = c['flows']['资料核验']
    first = flow['steps'][0]; human = flow['steps'][-1]
    first.update(agent='多模感知智能体', role='', nextId='sound_candidate',
        execution={'capability_id': s.registered['capability']['id'], 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    sound_step = copy.deepcopy(first)
    sound_step.update(id='sound_candidate', name='完整原音声类候选', nextId=human['id'],
        execution={'capability_id': sound_cap, 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    flow['steps'] = [first, sound_step, human]
    c['automation']['policies']['资料核验']['rules']['observe'] = {'mode': '自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    release, _, check = prepared(s.client, c, instance_id=s.instance_id)
    assert release['available_business_ids'], check
    enable(s.client, release); entity = object_row(s.client, release)
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000); stream.writeframes(b'\0\0' * 8000)
    raw = raw.getvalue()
    upload = {'schema': 'sound-upload-v1', 'request_id': str(uuid4()), 'name': '多模态来源合成静音.wav',
        'media_type': 'audio/wav', 'size_bytes': len(raw), 'input_sha256': hashlib.sha256(raw).hexdigest(),
        'configuration': {'channel': 'mono', 'threshold': .5}}
    original = good(s.client.post(s.base + '/input-sounds', data={'intent': json.dumps(upload, ensure_ascii=False)},
        files={'file': ('synthetic.wav', raw, 'audio/wav')}), 201)
    body = {**s.body, 'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [entity['id']], 'sound_ids': [original['id']], 'request_id': str(uuid4()),
        'title': '同实例真实双来源截面工程验收'}
    task = good(s.client.post(s.base + '/tasks', json=body), 201)
    for _ in range(2):
        assert process_once(job_id=task['workflow_runtime']['automatic']['job_id'], deliver=False)
        task = good(s.client.get('/api/workspace/tasks/' + task['id']))
    assert task['status'] == 'workflow_waiting'
    pixels = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/pixel-results'))['records'][0]
    sounds = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/sounds'))['records'][0]
    return SimpleNamespace(**vars(s), current_release=release, combined_task=task,
        pixel_entry=pixels, sound_entry=sounds, sound_input=original)


def capture(s, *, task_id=None):
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        transaction = db.get_transaction(); stamp = db.scalar(text('SELECT transaction_timestamp()'))
        value = subject.capture(db, s.p, s.instance_id, s.current_release['id'], task_id=task_id)
        assert db.get_transaction() is transaction and not db.new and not db.dirty and not db.deleted
        assert contracts.instant(value['as_of']) == stamp
        assert contracts.instant(value['facets']['pixel']['captured_at']) == stamp
        assert contracts.instant(value['facets']['task_sound']['as_of']) == stamp
        return value


def store_source(s, snapshot):
    # Real independent DB storage/receipt only; no fabricated NL plan or SDK.
    with Session(engine()) as db:
        row = m.DataRun(workspace_id=s.workspace_id, owner_id=s.p.user_id,
            question='explicit source-storage fixture, not an executed natural-language question', status='running', data={})
        db.add(row); db.flush()
        proof = m.Audit(workspace_id=s.workspace_id, actor_id=s.p.user_id, action=subject.SOURCE_ACTION,
            object_id=row.id, data=subject.source_audit_expected(row, snapshot))
        db.add(proof); db.flush()
        row.data = {contracts.FAMILY: {'source_snapshot': copy.deepcopy(snapshot), 'source_audit_id': proof.id}}
        identity = row.id; db.commit()
    return identity


def verify(s, query_id, snapshot):
    with Session(engine()) as db:
        subject.verify_saved_snapshot(db, s.p, db.get(m.DataRun, query_id), snapshot)
        assert not db.new and not db.dirty and not db.deleted


def receive(s, entry):
    identity = entry['reception']['observation_id']; assert identity
    deliver_events(workspace_id=s.workspace_id, observation_id=identity, event_kind='observation.data')


def test_same_real_transaction_two_facets_late_ack_and_review_never_rewrite_old_values(two_sources):
    s = two_sources
    pending = capture(s, task_id=s.combined_task['id']); frozen = copy.deepcopy(pending)
    assert set(pending['facets']) == {'pixel', 'task_sound'} and pending['record_count'] == 2
    assert pending['expected_sources_verified'] is False
    sound = pending['facets']['task_sound']['records'][0]
    assert sound['reception']['state'] == 'waiting_data' and sound['values'] is None
    old_id = store_source(s, pending); verify(s, old_id, pending)
    receive(s, s.pixel_entry); receive(s, s.sound_entry)
    received = capture(s, task_id=s.combined_task['id'])
    assert received['facets']['task_sound']['records'][0]['data_values_available'] is True
    new_id = store_source(s, received); verify(s, new_id, received); verify(s, old_id, pending)
    current = good(s.client.get('/api/workspace/tasks/' + s.combined_task['id'] + '/sounds'))['records'][0]
    good(s.client.post('/api/sounds/runs/' + current['run']['id'] + '/reviews', json={
        'schema': 'sound-review-v1', 'request_id': str(uuid4()), 'version': current['run']['version'],
        'verdict': 'uncertain', 'note': '合成工程资料不能作为声学或现场质量确认'}), 201)
    verify(s, new_id, received); verify(s, old_id, pending)
    assert pending == frozen and pending['facets']['task_sound']['records'][0]['values'] is None
    assert pending['snapshot_hash'] != received['snapshot_hash']


def test_history_uses_frozen_registry_after_legitimate_pause_not_current_capture_authority(two_sources):
    s = two_sources; value = capture(s); identity = store_source(s, value)
    instance = next(row for row in good(s.client.get('/api/instances')) if row['id'] == s.instance_id)
    good(s.client.post('/api/instances/' + s.instance_id + '/pause', json={'version': instance['version']}))
    verify(s, identity, value)
    with pytest.raises(HTTPException): capture(s)


@pytest.mark.parametrize('damage', ['source_audit', 'state_marker', 'memory', 'sound_original'])
def test_real_frozen_source_damage_fails_closed_without_repair_or_changing_saved_payload(two_sources, damage):
    s = two_sources; receive(s, s.pixel_entry); receive(s, s.sound_entry)
    value = capture(s, task_id=s.combined_task['id']); frozen = copy.deepcopy(value)
    identity = store_source(s, value); restore = None
    with Session(engine()) as db:
        row = db.get(m.DataRun, identity)
        if damage == 'source_audit': db.delete(db.get(m.Audit, row.data[contracts.FAMILY]['source_audit_id']))
        elif damage == 'state_marker': row.data = {}
        elif damage == 'memory':
            db.delete(db.get(m.Memory, value['facets']['task_sound']['records'][0]['reception']['memory']['id']))
        else:
            original = db.get(SoundInput, s.sound_input['id']); evidence = db.get(m.Evidence, original.evidence_id)
            path = settings().storage_dir / evidence.storage_key; content = path.read_bytes()
            path.write_bytes(b'explicit disposable original corruption'); restore = lambda: path.write_bytes(content)
        db.commit()
    try:
        with pytest.raises(HTTPException) as failure: verify(s, identity, value)
        assert failure.value.status_code == 409
        assert value == frozen
    finally:
        if restore is not None: restore()


def test_plain_self_hash_without_real_query_audit_is_not_saved_source_authority(two_sources):
    s = two_sources; value = capture(s)
    with Session(engine()) as db:
        row = m.DataRun(workspace_id=s.workspace_id, owner_id=s.p.user_id,
            question='no independent source proof', status='running', data={contracts.FAMILY: {
                'source_snapshot': value, 'source_audit_id': str(uuid4())}})
        db.add(row); db.commit(); identity = row.id
    with pytest.raises(HTTPException) as failure: verify(s, identity, value)
    assert failure.value.status_code == 409


def test_caller_rw_rc_and_foreign_scope_never_become_a_shared_snapshot(two_sources):
    s = two_sources
    for mode in ('READ COMMITTED, READ ONLY', 'REPEATABLE READ, READ WRITE'):
        with Session(engine()) as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + mode))
            with pytest.raises(HTTPException): subject.capture(db, s.p, s.instance_id, s.current_release['id'])
    with pytest.raises(HTTPException): capture(s, task_id=str(uuid4()))
    value = capture(s)
    assert value['record_count'] >= 3  # Prior real pixel Task plus this Task's two sources, all same instance.
