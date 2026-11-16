"""Real storage negative tests; no RTSP, model, SDK or successful DB fixture."""
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import wave

import pytest
from fastapi import HTTPException
from generic_agents import audio_streams as service
from generic_agents import models as m
from generic_agents.security import Principal


def wav_original():
    content = io.BytesIO()
    with wave.open(content, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
        target.writeframes(b'\x00\x00' * 8000)
    value = content.getvalue()
    return value, {'schema': 'audio-stream-capture-event-v1', 'kind': 'segment.saved',
        'index': 0, 'file': 'audio-000000.wav',
        'capture_representation': 'decoded_source_pcm16', 'codec': 'pcm_s16le',
        'source_encoding_preserved': False, 'source_clock_utc': 'unknown',
        'channel_mixing': False, 'sample_rate_conversion': False, 'sample_count': 8000,
        'sample_rate': 16000, 'channels': 1, 'duration_seconds': .5, 'complete': False,
        'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}


class CatalogOnlyDB:
    """Only the actual Evidence primitive can run before the new read-back gate."""
    def __init__(self): self.added = []; self.commits = 0
    def scalar(self, query): return None  # no pre-existing capture Observation
    def add(self, row): self.added.append(row)
    def flush(self):
        for row in self.added:
            if row.id is None: row.id = str(uuid4())
    def commit(self): self.commits += 1


@pytest.mark.parametrize('kind', ['damaged', 'symbolic'])
def test_current_saved_segment_rejects_bad_existing_blob_before_observation_or_queue(tmp_path, monkeypatch, kind):
    from generic_agents import config, leases
    workspace = str(uuid4()); actor = str(uuid4()); p = Principal(actor, workspace, 'admin', 'storage-negative')
    monkeypatch.setattr(config, 'settings', lambda: SimpleNamespace(storage_dir=tmp_path))
    # evidence.py imports this same settings function by value.
    from generic_agents import evidence
    monkeypatch.setattr(evidence, 'settings', lambda: SimpleNamespace(storage_dir=tmp_path))
    monkeypatch.setattr(leases, 'checkpoint', lambda db: None)
    content, segment = wav_original(); directory = tmp_path / workspace; directory.mkdir()
    path = directory / (segment['sha256'] + '.bin')
    if kind == 'damaged':
        path.write_bytes(b'X' * len(content)); before = path.read_bytes()
    else:
        target = tmp_path / 'separate-original.bin'; target.write_bytes(content); path.symlink_to(target)
        before = target.read_bytes()
    stream = SimpleNamespace(id=str(uuid4()), instance_id=str(uuid4()), status='recording',
        data={'generation': 1, 'capture_inventory': [], 'definition': {'start': {'max_segments': 1}}})
    db = CatalogOnlyDB(); job = SimpleNamespace(data={'generation': 1})
    with pytest.raises(HTTPException) as error:
        service._save_segment(db, p, job, stream, 'unused-connection', {}, {}, segment, content, None)
    assert error.value.status_code == 409
    assert db.commits == 0 and not any(isinstance(row, (m.Observation, m.Job)) for row in db.added)
    assert not any(isinstance(row, m.Audit) and row.action == service.CAPTURED for row in db.added)
    assert stream.data['capture_inventory'] == []
    if kind == 'damaged': assert path.read_bytes() == before
    else: assert path.is_symlink() and target.read_bytes() == before
