"""Normal HTTP original storage, independent anchors and cold recovery on PG.

The WAV is a synthetic engineering fixture. No ASR or sound-model inference is
performed or claimed by these tests.
"""
import hashlib
import io
import json
import wave
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AudioRun, CloudCall, Evidence, Invocation, Job, DomainInstance
from generic_agents.sound_models import SoundInput, EnvironmentSoundRun
from generic_agents.sound_inputs import ACTION
from tests.test_instance_workspace import prepared, enable, configuration


def good(response, status=200):
    assert response.status_code == status, response.text[:700]
    return response.json()


@pytest.fixture()
def original_scene(client, monkeypatch):
    # conftest uses macOS's /var alias. Production O_NOFOLLOW stays strict;
    # normalize only the explicitly disposable existing test evidence root.
    root = settings().storage_dir
    assert root.name.startswith('ga-test-evidence-') and root.is_dir() and not root.is_symlink()
    monkeypatch.setattr(settings(), 'storage_dir', root.resolve(strict=True))
    release, _, _ = prepared(client, configuration()); instance = enable(client, release)
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\0\0' * 320)
    content = raw.getvalue()
    body = {'schema': 'sound-upload-v1', 'request_id': str(uuid4()), 'name': '合成静音原件.wav',
        'media_type': 'audio/wav', 'size_bytes': len(content), 'input_sha256': hashlib.sha256(content).hexdigest(),
        'configuration': {'channel': 'mono', 'threshold': 1e-7}}
    original = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
    with Session(engine()) as db:
        baseline = {kind.__name__: set(db.scalars(select(kind.id)).all())
            for kind in (AudioRun, EnvironmentSoundRun, Invocation, CloudCall, Job)}
    return SimpleNamespace(client=client, release=release, instance=instance, raw=content, body=body,
        original=original, baseline=baseline, base='/api/workspace/instances/' + instance['id'] + '/input-sounds')


def upload(s, raw=None, original=None):
    return s.client.post(s.base, data={'intent': s.original if original is None else original},
        files={'file': ('公开声音.wav', s.raw if raw is None else raw, 'audio/wav')})


def recover(s):
    return s.client.get(s.base + '/requests', params={'request_id': s.body['request_id'],
        'raw_sha256': hashlib.sha256(s.original.encode()).hexdigest(), 'input_sha256': s.body['input_sha256']})


def pause(s):
    current = next(v for v in good(s.client.get('/api/instances')) if v['id'] == s.instance['id'])
    response = s.client.post('/api/instances/' + current['id'] + '/pause', json={'version': current['version']})
    assert response.status_code == 200, response.text[:700]


def test_original_upload_and_readonly_recovery_need_no_asr_model_or_inference(original_scene):
    s = original_scene; options = good(s.client.get(s.base + '/options'))
    assert options['can_upload'] and not options['requires_model_for_upload'] and not options['analysis_after_upload']
    saved = good(upload(s), 201); assert saved['snapshot']['metadata']['status'] == 'not_decoded'
    assert saved['snapshot']['metadata']['duration_seconds'] is None
    assert good(upload(s)) == saved
    recovered = good(recover(s)); assert recovered['found'] and recovered['input'] == saved
    assert recovered['intent_raw'] == s.original and recovered['body_hash'] == digest(s.body)
    content = s.client.get(saved['content_path']); assert content.status_code == 200 and content.content == s.raw
    assert hashlib.sha256(content.content).hexdigest() == content.headers['x-content-sha256']
    assert good(s.client.get(s.base))['total'] == 1
    with Session(engine()) as db:
        assert {kind.__name__: set(db.scalars(select(kind.id)).all())
            for kind in (AudioRun, EnvironmentSoundRun, Invocation, CloudCall, Job)} == s.baseline


def test_paused_instance_only_allows_original_replay_and_history(original_scene):
    s = original_scene; saved = good(upload(s), 201); pause(s)
    assert not good(s.client.get(s.base + '/options'))['can_upload']
    assert good(upload(s)) == saved and good(recover(s))['found']
    assert s.client.get(saved['content_path']).content == s.raw
    changed = {**s.body, 'request_id': str(uuid4())}
    assert upload(s, original=json.dumps(changed, ensure_ascii=False)).status_code == 409
    with Session(engine()) as db:
        assert len(db.scalars(select(SoundInput)).all()) == 1


def test_same_nonce_cannot_replace_threshold_or_original_bytes(original_scene):
    s = original_scene; saved = good(upload(s), 201)
    body = {**s.body, 'configuration': {'channel': 'mix', 'threshold': .9}}
    assert upload(s, original=json.dumps(body, ensure_ascii=False)).status_code == 409
    assert upload(s, raw=s.raw + b'x').status_code == 422
    assert good(recover(s))['input'] == saved


@pytest.mark.parametrize('damage', ['missing_save_audit', 'orphan_same_nonce_audit', 'wrong_instance_column', 'bad_original_bytes'])
def test_damaged_original_or_independent_anchor_blocks_instead_of_recreating(original_scene, damage):
    s = original_scene; saved = good(upload(s), 201)
    with Session(engine()) as db:
        row = db.get(SoundInput, saved['id'])
        anchor = db.scalar(select(Audit).where(Audit.action == ACTION, Audit.object_id == row.id))
        if damage == 'missing_save_audit': db.delete(anchor)
        elif damage == 'orphan_same_nonce_audit':
            db.add(Audit(workspace_id=row.workspace_id, actor_id=row.owner_id, action=ACTION,
                object_id=str(uuid4()), data={**anchor.data, 'input_id': str(uuid4())}))
        elif damage == 'wrong_instance_column':
            other = DomainInstance(workspace_id=row.workspace_id, namespace='sound_other_' + uuid4().hex[:12], status='draft', data={})
            db.add(other); db.flush(); row.instance_id = other.id
        else:
            evidence = db.get(Evidence, row.evidence_id)
            (settings().storage_dir / evidence.storage_key).write_bytes(b'broken')
        db.commit()
    assert recover(s).status_code == 409
    assert upload(s).status_code == 409
    with Session(engine()) as db: assert len(db.scalars(select(SoundInput)).all()) == 1
