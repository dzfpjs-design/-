"""Dedicated synthetic PostgreSQL dump/restore guards; never real workspace data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4
import pytest
from sqlalchemy import text
from generic_agents.config import settings
from generic_agents.db import engine
from tests.synthetic_advice_checkpoint import pg_program
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def checkpoint(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','');monkeypatch.setenv('DASHSCOPE_API_KEY','')
    monkeypatch.setattr(settings(),'cloud_allowed_providers','')
    original_key=settings().device_gateway_key_file
    evidence_dir=tempfile.TemporaryDirectory(prefix='ga-test-evidence-');monkeypatch.setattr(settings(),'storage_dir',Path(evidence_dir.name))
    root=Path(tempfile.mkdtemp(prefix='advice-fresh-mature-checkpoint-')).resolve();os.chmod(root,0o700)
    try:
        with engine().begin() as db:
            db.execute(text('CREATE TABLE synthetic_checkpoint_probe (value integer NOT NULL)'))
            db.execute(text('INSERT INTO synthetic_checkpoint_probe VALUES (17)'))
        url=engine().url;dump=root/'synthetic-mature.pgcustom'
        subprocess.run([pg_program('pg_dump'),'-h','127.0.0.1','-p',str(url.port),'-U',url.username,
            '--format=custom','--no-owner','--no-privileges','--file',str(dump),url.database],
            check=True,timeout=60,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        with engine().begin() as db:db.execute(text('DELETE FROM synthetic_checkpoint_probe'))
        key=root/'synthetic-test-gateway.key';key.write_text('only-synthetic-test-material');os.chmod(key,0o600)
        source=root/'evidence';source.mkdir(mode=0o700);(source/'original.txt').write_text('synthetic evidence')
        task_id=str(uuid4());manifest={'schema':'isolated-synthetic-fresh-mature-checkpoint-v1','task_id':task_id,
            'dump_sha256':sha(dump),'key_sha256':sha(key),'files':{'original.txt':sha(source/'original.txt')}}
        (root/'manifest.json').write_text(json.dumps(manifest));os.chmod(root/'manifest.json',0o600)
        yield root,task_id
    finally:
        settings().device_gateway_key_file=original_key
        with engine().begin() as db:db.execute(text('DROP TABLE IF EXISTS synthetic_checkpoint_probe'))
        shutil.rmtree(root);evidence_dir.cleanup()


def test_restore_real_dump_and_synthetic_files(checkpoint):
    root,identity=checkpoint
    assert restore_to_disposable_test_db(root)==identity
    with engine().connect() as db:assert db.scalar(text('SELECT value FROM synthetic_checkpoint_probe'))==17
    assert (Path(settings().storage_dir)/'original.txt').read_text()=='synthetic evidence'


@pytest.mark.parametrize('fault',['tampered_dump','tampered_file','wrong_manifest','bad_cluster','cloud_credential','occupied_target','other_client','symlink','hardlink'])
def test_refuses_before_restore_on_invalid_or_shared_state(checkpoint,monkeypatch,fault):
    root,_=checkpoint
    if fault=='tampered_dump':(root/'synthetic-mature.pgcustom').write_bytes(b'broken')
    if fault=='tampered_file':(root/'evidence'/'original.txt').write_text('changed')
    if fault=='wrong_manifest':
        m=json.loads((root/'manifest.json').read_text());m['schema']='not-a-fresh-checkpoint';(root/'manifest.json').write_text(json.dumps(m))
    if fault=='bad_cluster':monkeypatch.setenv('GA_TEST_PG_SYSTEM_IDENTIFIER','0')
    if fault=='cloud_credential':monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-forbidden')
    if fault=='symlink':(root/'evidence'/'linked').symlink_to(root/'evidence'/'original.txt')
    if fault=='hardlink':os.link(root/'evidence'/'original.txt',root/'evidence'/'linked')
    if fault=='occupied_target':
        with engine().begin() as db:db.execute(text('INSERT INTO synthetic_checkpoint_probe VALUES (99)'))
    other=engine().connect() if fault=='other_client' else None
    try:
        with pytest.raises((ValueError,AssertionError,RuntimeError)):restore_to_disposable_test_db(root)
    finally:
        if other:other.close()
    with engine().connect() as db:
        assert db.scalar(text('SELECT count(*) FROM synthetic_checkpoint_probe')) == (1 if fault=='occupied_target' else 0)
    assert not any(Path(settings().storage_dir).iterdir())
