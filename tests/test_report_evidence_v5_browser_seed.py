"""Root-only small V5 browser seed in EMPTY owned PG64128.

This wraps the frozen integration success case, not a second implementation.
All source content and provider replies are public synthetic test data. The
normal API/DB/journal path is real; model quality and a browser user journey
are NOT established by creating this seed. Root alone runs the one case with
its owned-PG fixture plugin, persistent test storage and GA_PUBLIC_DTO_OUT.
The plugin truncates at case START only; this file never cleans retained rows,
source files, original Map/Reduce HTTP journals or saved human history at end.
"""
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Base, Membership, User, Workspace
from generic_agents.security import password_hash
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline_v5 import (
    BASE, capture, reply, v5_scene,
    test_v5_real_map_reduce_manual_cas_nonce_facts_and_independent_readback as _run_success,
)


# PUBLIC test-only credentials, never copied into locator/serializer output.
USERNAME = 'synthetic_v5_browser_root'
PASSWORD = 'V5_public_synthetic_browser_20261017'
WORKSPACE_NAME = '合成资料交互验收'


def _owned_64128(*, empty=False):
    fence()
    assert os.environ.get('GA_F11_DUAL_TESTS_OWNED') == '64128', 'browser_seed_owned_port'
    url = engine().url
    assert (url.host in {'127.0.0.1', 'localhost'} and url.port == 64128
        and url.database == 'ga_f11_dual_tests'), 'browser_seed_owned_database'
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actual = db.execute(text("SELECT current_database(),inet_server_port(),"
            "shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()" )).one()
        assert tuple(actual) == ('ga_f11_dual_tests', 64128, 'owned-disposable-f11-dual'), 'browser_seed_database_comment'
        if empty:
            assert len(Base.metadata.tables) == 106, 'browser_seed_frozen_table_count'
            assert all(db.scalar(select(func.count()).select_from(table)) == 0
                for table in Base.metadata.sorted_tables), 'browser_seed_requires_empty_database'


@pytest.fixture
def account():
    """Override ONLY this module's plugin account with a real durable admin."""
    _owned_64128(empty=True)
    with Session(engine()) as db:
        user = User(username=USERNAME, password_hash=password_hash(PASSWORD))
        workspace = Workspace(name=WORKSPACE_NAME)
        db.add_all([user, workspace]); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role='admin'))
        db.commit()
        workspace_id = workspace.id
    return USERNAME, PASSWORD, workspace_id


def _save_locator(value):
    """Only safe synthetic IDs/hashes; no account password, session or DB URL."""
    directory = os.environ.get('GA_PUBLIC_DTO_OUT')
    assert directory, 'browser_seed_requires_public_capture_directory'
    root = Path(directory)
    assert root.is_absolute() and root.is_dir() and not root.is_symlink(), 'browser_seed_capture_directory'
    path = root / 'v5-browser-seed-locator.json'
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    assert len(raw) <= 8192, 'browser_seed_locator_capacity'
    assert PASSWORD.encode('utf-8') not in raw and b'postgresql' not in raw, 'browser_seed_locator_secret'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw); output.flush(); os.fsync(output.fileno())
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def test_v5_browser_seed_retains_real_small_model_and_latest_private_human_review(v5_scene, monkeypatch, request):
    _owned_64128()
    # Do not duplicate or bypass any business, CAS, replay, fee/journal,
    # independent-login or formal-handoff-block assertion in the main case.
    _run_success(v5_scene, monkeypatch, request.getfixturevalue('account'))
    scene = v5_scene
    found = reply(scene['client'].get(BASE + '/by-request/' + scene['body']['request_id']))
    draft = found['draft']
    response = scene['client'].get(BASE + '/' + draft['id'])
    latest = reply(response)
    assert latest == draft and latest['schema'] == 'report-evidence-reading-draft-v3'
    assert latest['status'] == 'review' and len(latest['review_history']) == 2
    assert latest['review_value'] == latest['review_history'][-1]['value']
    assert latest['review_history'][-1]['receipt']['accepted_revision'] == latest['revision']
    assert latest['model_result']['schema'] == 'report-evidence-candidate-projection-v6'
    assert latest['can_apply'] is latest['can_publish'] is False
    capture(scene, 'v5-browser-saved-latest', response)
    me = reply(scene['client'].get('/api/me'))
    _save_locator({
        'schema': 'report-evidence-v5-browser-seed-v1', 'synthetic_empty_database': True,
        'model_quality_verified': False, 'browser_verified': False,
        'actor_id': me['user_id'], 'workspace_id': me['workspace_id'],
        'material_id': scene['original']['id'], 'copy_id': scene['copy']['id'],
        'model_id': scene['cap']['id'], 'draft_id': latest['id'],
        'request_id': latest['request_id'], 'draft_revision': latest['revision'],
        'review_count': len(latest['review_history']),
        'review_receipt_hash': latest['review_history'][-1]['receipt']['receipt_hash'],
        'model_result_hash': latest['model_result_hash'], 'source_hash': latest['preview']['source_hash'],
        'latest_serializer': scene['body']['request_id'] + '-v5-browser-saved-latest.json',
    })
    _owned_64128()
