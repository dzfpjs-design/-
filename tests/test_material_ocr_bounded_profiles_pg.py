"""Owned PostgreSQL authorization/worker acceptance with explicit fake OCR.

Root only: external --noconftest plugin, isolated empty PG56428, no real model.
The long case uses a generated blank PDF and fabricated OCR text/peak values;
it proves persistence and page112 dispatch, not original-report OCR quality.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from generic_agents import material_ocr as o, material_ocr_runtime as r
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Evidence, Job, Membership
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime, upload, scan_pdf, prefix, start, detail, save_body

REAL_RECOGNIZE = r.recognize


@pytest.fixture(autouse=True)
def owned_pg_only():
    url = make_url(settings().database_url)
    assert os.environ.get('GA_OCR_BOUNDED_PROFILE_TESTS_OWNED') == '56428'
    assert url.host in {'localhost', '127.0.0.1'} and url.port == 56428
    assert url.database and url.database != 'postgres'


@pytest.fixture
def profiles(local_runtime, monkeypatch):
    original_status, original_recognize = r.engine_status, r.recognize
    statuses = {}
    for identity, hashed in r.recognition_profile_pairs():
        manifest = {'id': identity, 'manifest_hash': hashed, 'synthetic': 'no installed runtime/model'}
        statuses[hashed] = {'id': identity, 'name': '明确合成识别配置', 'ready': True, 'reason': None,
            'manifest_hash': hashed, 'manifest': manifest, 'summary_hash': digest(manifest)}
    state = {'statuses': statuses, 'peak': 1, 'hook': None, 'child_calls': [], 'legacy': local_runtime}
    def status(*, engine_hash=None):
        if engine_hash is None: return original_status()
        assert engine_hash in statuses, 'no unknown fixture profile fallback'
        return copy.deepcopy(statuses[engine_hash])
    monkeypatch.setattr(r, 'engine_status', status)
    def installation(*, _paths=None, **kwargs):
        selected = r._selected_profile(_paths.engine_hash)
        assert selected.manifest_hash in statuses
        return {'id': selected.engine_id}, selected.manifest_hash
    monkeypatch.setattr(r, '_installation', installation)
    monkeypatch.setattr(r, '_summary', lambda manifest, hashed: copy.deepcopy(statuses[hashed]['manifest']))
    class Completed:
        pid = 123; returncode = 0
        def poll(self): return 0
    def popen(command, **kwargs):
        request = json.loads(Path(command[-2]).read_bytes())
        base = Path(command[6]).parent.name
        hashed = {r._LOW_MEMORY_PROFILE.base_name: r.LOW_MEMORY_MANIFEST_HASH,
                  r._LOW_MEMORY_V2_PROFILE.base_name: r.LOW_MEMORY_V2_MANIFEST_HASH}[base]
        state['child_calls'].append(hashed)
        content = Path(request['input']).read_bytes()
        regions = [{'id': 'r1', 'text': '明确合成识别内容，绝非原报告', 'box': [1, 2, 80, 20], 'score': 0.42}]
        if state['hook']: state['hook']()
        value = {'regions': regions, 'elapsed_ms': 1, 'peak_rss_bytes': state['peak'], 'receipt': {
            'manifest_hash': hashed, 'input_sha256': hashlib.sha256(content).hexdigest(),
            'result_sha256': digest(regions)}}
        Path(command[-1]).write_text(json.dumps({'ok': True, 'result': value}))
        return Completed()
    monkeypatch.setattr(r.subprocess, 'Popen', popen)
    monkeypatch.setattr(r.subprocess, 'run', lambda *a, **kw: pytest.fail('real subprocess prohibited'))
    def recognize(content, *, expected_manifest_hash, guard):
        if expected_manifest_hash in statuses:
            return REAL_RECOGNIZE(content, expected_manifest_hash=expected_manifest_hash, guard=guard)
        return original_recognize(content, expected_manifest_hash=expected_manifest_hash, guard=guard)
    monkeypatch.setattr(r, 'recognize', recognize)
    return state


def current(client, material):
    response = client.get(prefix(material)); assert response.status_code == 200, response.text
    return response.json()


def dispatch_job(identity):
    assert process_once(identity, deliver=False)
    with Session(engine()) as db: return db.get(Job, identity).status


def page_job(material, run_id, index):
    with Session(engine()) as db:
        unit = db.scalar(select(o.Unit).where(o.Unit.material_id == material['id'],
            o.Unit.run_id == run_id, o.Unit.unit_index == index))
        return unit.id, unit.data['job_id']


def resume(client, material, hashed, *, request=None):
    view = current(client, material)
    identity = dict((h, i) for i, h in r.recognition_profile_pairs())[hashed]
    command = request or {'request_id': str(uuid4()), 'expected_material_version': view['material']['version'],
        'run_id': view['run']['id'], 'expected_run_version': view['run']['version'],
        'engine_id': identity, 'engine_manifest_hash': hashed, 'reason': '明确选择此固定方式，仅继续未完成页'}
    response = client.post(prefix(material) + '/resume_with_profile', json=command)
    return command, response


def row_value(row):
    return copy.deepcopy({c.name: getattr(row, c.name) for c in row.__table__.columns})


def protected_rows(run_id, rawless_id):
    with Session(engine()) as db:
        models = (o.Unit, o.Version, o.Request, Job, Evidence)
        return {(cls.__tablename__, row.id): row_value(row) for cls in models
                for row in db.scalars(select(cls)) if not (cls is o.Unit and row.id == rawless_id)}


def assert_kept(values):
    models = {c.__tablename__: c for c in (o.Unit, o.Version, o.Request, Job, Evidence)}
    with Session(engine()) as db:
        for (table, identity), before in values.items():
            assert row_value(db.get(models[table], identity)) == before, (table, identity)


def edit_confirm(client, material, run_id, unit_id, text):
    before = detail(client, material, run_id, unit_id)
    saved = client.put(prefix(material) + '/units/' + unit_id, json=save_body(before, text))
    assert saved.status_code == 200, saved.text
    value = saved.json()['unit']
    body = {'request_id': str(uuid4()), 'expected_material_version': value['material_version'],
        'run_id': run_id, 'expected_unit_version': value['unit']['version'],
        'revision_hash': value['draft']['hash'], 'reason': '显式合成逐页人工核对'}
    response = client.post(prefix(material) + '/units/' + unit_id + '/confirm', json=body)
    assert response.status_code == 200, response.text


def test_page112_new_v2_attempt_preserves_111_raw_two_manual_pages_and_original_renderer(client, profiles):
    material = upload(client, scan_pdf(235), '明确合成235页.pdf')
    _, started = start(client, material); run_id = started['view']['run']['id']
    first_v1_command = None
    for index in range(1, 113):
        with Session(engine()) as db: prepare_job_id = db.get(o.Run, run_id).data['job_id']
        assert dispatch_job(prepare_job_id) == 'succeeded'
        unit_id, job_id = page_job(material, run_id, index)
        if index == 9:
            # Explicit v1 acceptance, never an implicit choice for an existing run.
            profiles['legacy']['fail'] = True
            assert dispatch_job(job_id) == 'failed'
            profiles['legacy']['fail'] = False
            first_v1_command, response = resume(client, material, r.LOW_MEMORY_MANIFEST_HASH)
            assert response.status_code == 200, response.text
            unit_id, job_id = page_job(material, run_id, index)
        if index == 112: profiles['peak'] = 1073741825
        assert dispatch_job(job_id) == ('failed' if index == 112 else 'succeeded')
        if index in (4, 9): edit_confirm(client, material, run_id, unit_id, '合成人工保存末字-' + str(index))
    stopped = current(client, material)
    assert stopped['run']['status'] == 'failed'
    assert stopped['run']['progress']['prepared'] == 112
    assert stopped['run']['progress']['total'] == 235
    assert stopped['run']['progress']['confirmed'] == 2
    unit_id, failed_job_id = page_job(material, run_id, 112)
    assert detail(client, material, run_id, unit_id)['raw'] is None
    kept = protected_rows(run_id, unit_id)
    renderer = stopped['run']['engine_manifest_hash']
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(o.Version).where(o.Version.kind == 'raw')) == 111
        old_failed_job = row_value(db.get(Job, failed_job_id))
    files = {str(p.relative_to(settings().storage_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in settings().storage_dir.rglob('*') if p.is_file()}
    profiles['statuses'][r.LOW_MEMORY_MANIFEST_HASH].update(ready=False, reason='合成v1缺包')
    options = current(client, material)
    assert options['permissions']['resume_with_profile']['allowed'] is False
    assert [p['permission']['allowed'] for p in options['resume_profiles']] == [False, True]
    profiles['peak'] = 1
    command, response = resume(client, material, r.LOW_MEMORY_V2_MANIFEST_HASH)
    assert response.status_code == 200, response.text
    assert_kept(kept)
    _, new_job_id = page_job(material, run_id, 112)
    assert new_job_id != failed_job_id
    with Session(engine()) as db:
        job = db.get(Job, new_job_id)
        assert job.data['target']['engine_manifest_hash'] == renderer
        assert job.data['target']['recognition_engine_manifest_hash'] == r.LOW_MEMORY_V2_MANIFEST_HASH
        assert row_value(db.get(Job, failed_job_id)) == old_failed_job
    assert dispatch_job(new_job_id) == 'succeeded'
    page = detail(client, material, run_id, unit_id)
    assert page['raw']['engine_manifest_hash'] == r.LOW_MEMORY_V2_MANIFEST_HASH
    assert profiles['child_calls'][-2:] == [r.LOW_MEMORY_MANIFEST_HASH, r.LOW_MEMORY_V2_MANIFEST_HASH]
    assert_kept(kept)
    assert current(client, material)['run']['engine_manifest_hash'] == renderer
    assert files == {str(p.relative_to(settings().storage_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in settings().storage_dir.rglob('*') if p.is_file()}
    # Historical nonce/readback stays on its own v1 body even after v2 acceptance.
    replay = client.post(prefix(material) + '/resume_with_profile', json=first_v1_command)
    assert replay.status_code == 200, replay.text
    assert replay.json()['request']['result']['recognition_authorization']['engine']['manifest_hash'] == r.LOW_MEMORY_MANIFEST_HASH
    assert replay.json()['request']['body_hash'] == o._request_hash('resume_with_profile', None, first_v1_command)
    profiles['statuses'][r.LOW_MEMORY_V2_MANIFEST_HASH]['ready'] = False
    replay = client.post(prefix(material) + '/resume_with_profile', json=command)
    assert replay.status_code == 200, replay.text
    changed = client.post(prefix(material) + '/resume_with_profile', json={**command, 'reason': '不能重签'})
    assert changed.status_code == 409
    now = current(client, material)
    confirmation = client.post(prefix(material) + '/confirm', json={'request_id': str(uuid4()),
        'expected_material_version': now['material']['version'], 'run_id': run_id,
        'expected_run_version': now['run']['version'], 'units_manifest_hash': now['run']['units_manifest_hash'],
        'reason': '未核全235页必须拒绝'})
    assert confirmation.status_code == 409
    with Session(engine()) as db: assert db.scalar(select(func.count()).select_from(CloudCall)) == 0


@pytest.mark.parametrize('case', ['missing', 'viewer', 'mixed_pair', 'cancel_after_child'])
def test_v2_refusal_and_cancel_leave_rawless_page_without_new_result(client, profiles, case):
    material = upload(client, scan_pdf(2)); _, started = start(client, material)
    run_id = started['view']['run']['id']
    assert dispatch_job(started['view']['run']['job_id']) == 'succeeded'
    unit_id, job_id = page_job(material, run_id, 1)
    profiles['legacy']['fail'] = True
    assert dispatch_job(job_id) == 'failed'
    profiles['legacy']['fail'] = False
    before = current(client, material)
    if case == 'viewer':
        with Session(engine()) as db:
            membership = db.scalar(select(Membership).where(Membership.workspace_id == client.headers['X-Workspace-ID']))
            membership.role = 'viewer'; db.commit()
    elif case == 'missing':
        profiles['statuses'][r.LOW_MEMORY_V2_MANIFEST_HASH].update(ready=False, reason='合成v2缺包')
    request = {'request_id': str(uuid4()), 'expected_material_version': before['material']['version'],
        'run_id': run_id, 'expected_run_version': before['run']['version'],
        'engine_id': r.LOW_MEMORY_ENGINE_ID if case == 'mixed_pair' else r.LOW_MEMORY_V2_ENGINE_ID,
        'engine_manifest_hash': r.LOW_MEMORY_V2_MANIFEST_HASH, 'reason': '显式恢复'}
    _, response = resume(client, material, r.LOW_MEMORY_V2_MANIFEST_HASH, request=request)
    if case != 'cancel_after_child':
        assert response.status_code == (403 if case == 'viewer' else 409), response.text
        assert current(client, material)['run']['version'] == before['run']['version']
        assert not profiles['child_calls']
    else:
        assert response.status_code == 200, response.text
        def cancel():
            view = current(client, material)
            reply = client.post(prefix(material) + '/cancel', json={'request_id': str(uuid4()),
                'expected_material_version': view['material']['version'], 'run_id': run_id,
                'expected_run_version': view['run']['version'], 'reason': '模型返回前明确取消'})
            assert reply.status_code == 200, reply.text
        profiles['hook'] = cancel
        _, selected = page_job(material, run_id, 1)
        dispatch_job(selected)
        assert current(client, material)['run']['status'] == 'cancelled'
    assert detail(client, material, run_id, unit_id)['raw'] is None
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(o.Version)) == 0
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
