"""Owned-PG concurrency acceptance; not run by the author.

Run only with the root's external --noconftest fixture plugin on port 55628,
or its explicitly fenced, disposable F11 database with the actual ownership mark.
The imported OCR fixture replaces rendering/recognition with synthetic bytes.
No real report, installed runtime, provider, or historical rows are used.
"""
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import material_ocr as o, material_ocr_api as api
from generic_agents import material_ocr_read_session as reads
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine, session as legacy_session
from generic_agents.models import Base, Evidence
from generic_agents.security import Principal, authenticated
from generic_agents.worker import process_once
from tests.test_material_ocr import (local_runtime, upload, scan_pdf, prefix,
                                     start, detail, save_body)


@pytest.fixture(autouse=True)
def owned_database_only():
    value = make_url(settings().database_url)
    if os.environ.get('GA_OCR_CONSISTENT_READ_OWNED') == '55628' and value.port == 55628:
        assert value.host in {'127.0.0.1', 'localhost'} and value.port == 55628
        assert value.database and value.database != 'postgres'
        return
    # The current schema's shared regression runner must explicitly opt in;
    # neither an arbitrary port nor an environment label alone proves ownership.
    assert os.environ.get('GA_READ_SNAPSHOT_CONSISTENCY_OWNED') == '1'
    marker = os.environ.get('GA_F11_DUAL_TESTS_OWNED', '')
    assert marker.isascii() and marker.isdecimal()
    port = int(marker)
    assert 1 <= port <= 65535
    assert value.host in {'127.0.0.1', 'localhost'} and value.port == port
    assert os.environ.get('GA_TEST_NETWORK_PG') == marker
    assert os.environ.get('GA_TEST_NETWORK_FENCE') == '1'
    assert value.database == 'ga_f11_dual_tests'
    with engine().connect() as connection:
        actual = connection.execute(text("SELECT current_database(), "
            "shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()")).one()
        assert tuple(actual) == ('ga_f11_dual_tests', 'owned-disposable-f11-dual')


def persisted_state():
    """Opaque logical equality of every table and synthetic evidence file."""
    # Additive migrations now register 127 tables; no historical/new table is omitted.
    expected = set(Base.metadata.tables)
    assert len(expected) == 127
    with engine().connect() as connection:
        actual = set(connection.scalars(text(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'")))
        assert actual == expected
        tables = {
            name: tuple(connection.scalars(text(
                'SELECT row_to_json(t)::text FROM "' + name + '" AS t ORDER BY t.id')))
            for name in sorted(expected)
        }
    storage = settings().storage_dir
    files = {str(p.relative_to(storage)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in storage.rglob('*') if p.is_file()}
    return tables, files


@pytest.fixture
def page_scene(client, local_runtime, monkeypatch):
    monkeypatch.setattr(o, 'low_memory_engine_status', lambda: {
        'id': 'explicit-disabled-synthetic-profile', 'name': '未调用真实运行环境',
        'ready': False, 'reason': '本测试仅使用显式替身',
        'manifest': None, 'manifest_hash': None})
    material = upload(client, scan_pdf(3))
    _, result = start(client, material)
    assert process_once(result['view']['run']['job_id'], deliver=False)
    root = client.get(prefix(material)).json()
    assert root['run']['progress']['prepared'] == 1
    with Session(engine()) as db:
        unit = db.scalar(select(o.Unit).where(o.Unit.run_id == root['run']['id']))
        return {'material': material, 'run_id': root['run']['id'], 'unit_id': unit.id,
                'unit_job_id': unit.data['job_id'], 'prepare_job_id': root['run']['job_id'],
                'runtime': local_runtime}


class Interleaving:
    """The writer commits while one actual GET is paused after its old head."""
    def __init__(self):
        self.barrier = threading.Barrier(2)
        self.committed = threading.Event()
        self.triggered = False
        self.reader_pid = None
        self.writer_pids = set()
        self.isolation = None
        self.read_only = None

    def pause(self, db):
        if db.info.get('ocr_read_race') is not self or self.triggered:
            return
        self.triggered = True
        self.reader_pid = db.scalar(text('SELECT pg_backend_pid()'))
        self.isolation = db.scalar(text('SHOW transaction_isolation'))
        self.read_only = db.scalar(text('SHOW transaction_read_only'))
        self.barrier.wait(timeout=15)
        assert self.committed.wait(timeout=30), 'writer did not finish its durable commit'

    def writer(self, db):
        self.writer_pids.add(db.scalar(text('SELECT pg_backend_pid()')))


@contextmanager
def read_mode(mode, race):
    # RC is a negative-control equivalent of the original dependency's snapshot
    # behavior, not a production fallback or an exception-to-200 adapter.
    def dependency(p: Principal = Depends(authenticated)):
        source = reads.read_session(p) if mode == 'repeatable_read' else legacy_session()
        try:
            db = next(source)
            db.info['ocr_read_race'] = race
            yield db
        finally:
            source.close()
    missing = object()
    previous = app.dependency_overrides.get(reads.read_session, missing)
    app.dependency_overrides[reads.read_session] = dependency
    try:
        yield
    finally:
        if previous is missing:
            app.dependency_overrides.pop(reads.read_session, None)
        else:
            app.dependency_overrides[reads.read_session] = previous


def reader_and_writer(client, path, writer, race, mode):
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.get, path)
        try:
            race.barrier.wait(timeout=15)
            writer()
            after_commit = persisted_state()
        finally:
            race.committed.set()
        response = pending.result(timeout=30)
    # The GET continued after the commit; it did not modify or repair any row.
    assert persisted_state() == after_commit
    assert race.triggered and race.writer_pids
    assert race.reader_pid not in race.writer_pids
    assert race.isolation == ('repeatable read' if mode == 'repeatable_read' else 'read committed')
    assert race.read_only == ('on' if mode == 'repeatable_read' else 'off')
    return response, after_commit


@pytest.mark.parametrize('mode', ['read_committed', 'repeatable_read'])
def test_overview_prepared_prefix_is_one_snapshot(client, page_scene, monkeypatch, mode):
    s = page_scene; race = Interleaving()
    checked_run, job_scope = o.checked_run, o._job_scope
    def after_run(*args, **kwargs):
        result = checked_run(*args, **kwargs)
        race.pause(args[0])
        return result
    def observe_writer(db, *args, **kwargs):
        result = job_scope(db, *args, **kwargs)
        race.writer(db)
        return result
    monkeypatch.setattr(o, 'checked_run', after_run)
    monkeypatch.setattr(o, '_job_scope', observe_writer)
    def commit_page_two():
        assert process_once(s['prepare_job_id'], deliver=False)
        with Session(engine()) as db:
            assert db.get(o.Run, s['run_id']).data['prepared'] == 2
    with read_mode(mode, race):
        response, state = reader_and_writer(client, prefix(s['material']), commit_page_two, race, mode)
        assert response.status_code == (200 if mode == 'repeatable_read' else 409), response.text
        if mode == 'repeatable_read':
            assert response.json()['run']['progress']['prepared'] == 1
            assert response.json()['run']['progress']['queued'] == 3
        fresh = client.get(prefix(s['material']))
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()['run']['progress']['prepared'] == 2
        assert persisted_state() == state
    assert s['runtime']['calls'] == 0


@pytest.mark.parametrize('transition,surface', [
    ('recognize', 'detail'), ('recognize', 'units'), ('human_save', 'detail')])
@pytest.mark.parametrize('mode', ['read_committed', 'repeatable_read'])
def test_unit_and_version_head_are_one_snapshot(client, page_scene, monkeypatch, transition, surface, mode):
    s = page_scene
    if transition == 'human_save':
        assert process_once(s['unit_job_id'], deliver=False)
        prior = detail(client, s['material'], s['run_id'], s['unit_id'])
        command = save_body(prior, '这是人工最后一个字：末')
    race = Interleaving()
    checked_versions, job_scope, commit_command = o.checked_versions, o._job_scope, api.commit_command
    def before_versions(db, *args, **kwargs):
        race.pause(db)
        return checked_versions(db, *args, **kwargs)
    def observe_job(db, *args, **kwargs):
        result = job_scope(db, *args, **kwargs)
        race.writer(db)
        return result
    def observe_save(db, *args, **kwargs):
        race.writer(db)
        return commit_command(db, *args, **kwargs)
    monkeypatch.setattr(o, 'checked_versions', before_versions)
    monkeypatch.setattr(o, '_job_scope', observe_job)
    monkeypatch.setattr(api, 'commit_command', observe_save)
    path = prefix(s['material']) + '/units'
    if surface == 'detail': path += '/' + s['unit_id']
    path += '?run_id=' + s['run_id']
    def commit_new_head():
        if transition == 'recognize':
            assert process_once(s['unit_job_id'], deliver=False)
        else:
            result = client.put(prefix(s['material']) + '/units/' + s['unit_id'], json=command)
            assert result.status_code == 200, result.text
        with Session(engine()) as db:
            unit = db.get(o.Unit, s['unit_id'])
            assert unit.data['raw_id'] and unit.data['last_hash']
            if transition == 'human_save': assert unit.data['draft_id']
    with read_mode(mode, race):
        response, state = reader_and_writer(client, path, commit_new_head, race, mode)
        assert response.status_code == (200 if mode == 'repeatable_read' else 409), response.text
        if mode == 'repeatable_read':
            before = response.json()
            if surface == 'units':
                assert before['items'][0]['draft_revision'] is None
                assert before['items'][0]['raw_revision'] is None
            else:
                assert before['draft'] is None
                assert (before['raw'] is None) == (transition == 'recognize')
        fresh = client.get(path)
        assert fresh.status_code == 200, fresh.text
        if surface == 'units': assert fresh.json()['items'][0]['raw_revision']['number'] == 1
        else: assert fresh.json()['raw']['number'] == 1
        if transition == 'human_save':
            assert fresh.json()['draft']['number'] == 2
            assert fresh.json()['draft']['plain_text'] == '这是人工最后一个字：末'
        assert persisted_state() == state
    assert s['runtime']['calls'] == 1


@pytest.mark.parametrize('damage', ['unit_head', 'raw_hash', 'image_bytes'])
def test_real_integrity_failure_stays_409_and_get_never_repairs(client, page_scene, damage):
    s = page_scene
    assert process_once(s['unit_job_id'], deliver=False)
    restore = None
    with Session(engine()) as db:
        unit = db.get(o.Unit, s['unit_id'])
        if damage == 'unit_head':
            unit.data = {**unit.data, 'last_hash': '0' * 64}
        elif damage == 'raw_hash':
            db.get(o.Version, unit.data['raw_id']).content_hash = '0' * 64
        else:
            evidence = db.get(Evidence, unit.evidence_id)
            path = settings().storage_dir / evidence.storage_key
            restore = path, path.read_bytes()
            path.write_bytes(b'explicit synthetic corrupted image')
        db.commit()
    before = persisted_state()
    try:
        response = client.get(prefix(s['material']) + '/units/' + s['unit_id'], params={'run_id': s['run_id']})
        assert response.status_code == 409, response.text
        assert persisted_state() == before
    finally:
        if restore: restore[0].write_bytes(restore[1])


def test_all_database_get_routes_have_new_dependency_and_writes_keep_original():
    expected = {'get_overview', 'units', 'get_unit', 'image', 'versions', 'version',
                'probe', 'confirmed_image', 'citation'}
    found = set()
    for route in api.router.routes:
        dependencies = {d.call for d in route.dependant.dependencies}
        if 'GET' in route.methods and route.endpoint.__name__ in expected:
            assert reads.read_session in dependencies
            assert legacy_session not in dependencies
            found.add(route.endpoint.__name__)
        elif route.methods.intersection({'POST', 'PUT'}):
            assert legacy_session in dependencies
            assert reads.read_session not in dependencies
    assert found == expected


def test_new_transaction_is_read_only_rollback_and_pool_default_stays_unchanged(client, page_scene):
    before = persisted_state()
    generator = reads.read_session(None)
    db = next(generator)
    try:
        assert db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        assert db.scalar(text('SHOW transaction_read_only')) == 'on'
        with pytest.raises(DBAPIError) as failure:
            # Even an accidental zero-row write is rejected at the DB boundary.
            db.execute(text('UPDATE material_ocr_units SET status=status WHERE false'))
        assert (getattr(failure.value.orig, 'sqlstate', None)
                or getattr(failure.value.orig, 'pgcode', None)) == '25006'
    finally:
        generator.close()
    with Session(engine()) as normal:
        assert normal.scalar(text('SHOW transaction_isolation')) == 'read committed'
        assert normal.scalar(text('SHOW transaction_read_only')) == 'off'
    assert persisted_state() == before


def test_authentication_and_workspace_scope_stay_enforced_without_writes(client, page_scene):
    before = persisted_state(); path = prefix(page_scene['material'])
    with TestClient(app) as guest:
        assert guest.get(path).status_code == 401
    denied = client.get(path, headers={'X-Workspace-ID': str(uuid4())})
    assert denied.status_code == 403
    assert persisted_state() == before
