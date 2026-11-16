"""Real authorization/dispatch functions over explicit in-memory IO, not OCR."""
import copy
import hashlib

import pytest
from fastapi import HTTPException

from generic_agents import material_ocr as o, material_ocr_recognition as rec
from generic_agents import material_ocr_runtime as runtime
from generic_agents.contracts import digest
from tests.test_material_ocr_low_memory_resume import (
    scene, low, partial, accept, resume_body, row_bytes, assert_unchanged,
    jobs, units, command, body)


@pytest.fixture
def bounded(low, monkeypatch):
    s = low
    manifest = {'id': runtime.LOW_MEMORY_V2_ENGINE_ID,
                'manifest_hash': runtime.LOW_MEMORY_V2_MANIFEST_HASH,
                'synthetic': 'no runtime, model, DB or service'}
    s.bounded = {'id': runtime.LOW_MEMORY_V2_ENGINE_ID, 'name': '明确合成检测限宽引擎',
        'ready': True, 'reason': None, 'manifest_hash': runtime.LOW_MEMORY_V2_MANIFEST_HASH,
        'manifest': manifest, 'summary_hash': digest(manifest)}
    monkeypatch.setattr(o, 'bounded_engine_status', lambda: copy.deepcopy(s.bounded))
    s.selected_hashes = []
    def recognize(png, *, expected_manifest_hash, guard):
        guard(); s.state['recognitions'] += 1
        s.selected_hashes.append(expected_manifest_hash)
        if s.state['recognition_hook']: s.state['recognition_hook']()
        guard()
        engine = next((e for e in (s.installed, s.low, s.bounded)
                       if e['manifest_hash'] == expected_manifest_hash), None)
        assert engine is not None, 'fixture never falls back for an unknown profile'
        regions = [{'id': 'r1', 'text': '显式合成原字', 'box': [0, 0, 1, 1], 'score': 0.5}]
        return {'regions': regions, 'elapsed_ms': 1, 'peak_rss_bytes': 1, 'receipt': {
            'input_sha256': hashlib.sha256(png).hexdigest(), 'manifest_hash': engine['manifest_hash'],
            'manifest': engine['manifest'], 'summary_hash': engine['summary_hash'],
            'result_sha256': digest(regions)}}
    monkeypatch.setattr(runtime, 'recognize', recognize)
    return s


def v2_body(s):
    value = resume_body(s)
    return {**value, 'engine_id': s.bounded['id'], 'engine_manifest_hash': s.bounded['manifest_hash']}


def test_v1_unavailable_does_not_disable_v2_and_viewer_disables_each(bounded):
    s = bounded; partial(s)
    s.low.update(ready=False, reason='合成v1缺包')
    view = o.overview(s.db, s.p, s.m)
    assert view['low_memory_engine'] == s.low
    assert view['permissions']['resume_with_profile']['allowed'] is False
    assert [set(x) for x in view['resume_profiles']] == [{'engine', 'permission'}, {'engine', 'permission'}]
    assert [x['engine']['id'] for x in view['resume_profiles']] == [s.low['id'], s.bounded['id']]
    assert [x['permission']['allowed'] for x in view['resume_profiles']] == [False, True]
    s.p.role = 'viewer'
    assert all(not x['permission']['allowed'] for x in o.overview(s.db, s.p, s.m)['resume_profiles'])


def test_explicit_v2_changes_only_rawless_pages_not_renderer_raw_manual_or_old_jobs(bounded):
    s = bounded; kept, rawless, _ = partial(s)
    old = [(r, row_bytes(r)) for r in s.db.rows if isinstance(r, (o.Version, o.Job)) or r is kept]
    renderer = copy.deepcopy(s.run.data['engine']); coverage = copy.deepcopy(s.run.data['coverage'])
    value = v2_body(s)
    result = o.execute(s.db, s.p, s.m.id, 'resume_with_profile', body(value))
    assert result['request']['action'] == 'resume_with_profile'
    assert_unchanged(old)
    assert s.run.data['engine'] == renderer and s.run.data['coverage'] == coverage
    assert 'recognition_engine' not in kept.data
    assert rec.effective_engine(s.db, s.run, rawless) == s.bounded
    job = jobs(s, 'recognize_material_ocr_unit')[-1]
    assert job.data['target']['engine_manifest_hash'] == renderer['manifest_hash']
    assert job.data['target']['recognition_engine_manifest_hash'] == s.bounded['manifest_hash']
    o.process_unit(s.db, s.p, job)
    raw = next(v for v in s.db.rows if isinstance(v, o.Version) and v.unit_id == rawless.id)
    assert raw.data['engine_manifest_hash'] == s.bounded['manifest_hash']
    assert s.selected_hashes[-1] == s.bounded['manifest_hash']


def test_saved_v1_raw_and_nonce_remain_valid_after_new_v2_authorization(bounded):
    s = bounded; old, second, _ = partial(s)
    original, _ = accept(s)
    o.process_unit(s.db, s.p, jobs(s, 'recognize_material_ocr_unit')[-1])
    assert rec.effective_engine(s.db, s.run, second) == s.low
    o.process_prepare(s.db, s.p, jobs(s, 'prepare_material_ocr')[-1])
    third = units(s)[2]
    s.run.status = 'failed'; s.run.data = {**s.run.data, 'error': '明确合成资源失败'}; o.seal(s.run)
    kept = [(r, row_bytes(r)) for r in s.db.rows if isinstance(r, (o.Version, o.Job)) or r in (old, second)]
    o.execute(s.db, s.p, s.m.id, 'resume_with_profile', body(v2_body(s)))
    assert_unchanged(kept)
    assert rec.effective_engine(s.db, s.run, second) == s.low
    assert rec.effective_engine(s.db, s.run, third) == s.bounded
    for page in (old, second): o.checked_versions(s.db, s.p, s.run, page)
    before = [(r, row_bytes(r)) for r in s.db.rows]
    s.low['ready'] = False; s.bounded['ready'] = False
    o.execute(s.db, s.p, s.m.id, 'resume_with_profile', body(original))
    assert_unchanged(before)
    with pytest.raises(HTTPException):
        o.execute(s.db, s.p, s.m.id, 'resume_with_profile', body({**original,
            'engine_id': s.bounded['id'], 'engine_manifest_hash': s.bounded['manifest_hash']}))


@pytest.mark.parametrize('damage', ['mixed_id', 'mixed_hash', 'unknown', 'missing', 'viewer', 'revoked', 'cancelled_after_await'])
def test_profile_acceptance_and_dispatch_never_fall_back(bounded, damage):
    s = bounded; _, page, _ = partial(s); value = v2_body(s)
    if damage == 'mixed_id': value['engine_id'] = s.low['id']
    elif damage == 'mixed_hash': value['engine_manifest_hash'] = s.low['manifest_hash']
    elif damage == 'unknown': value['engine_manifest_hash'] = '0' * 64
    elif damage == 'missing': s.bounded.update(ready=False, reason='明确合成v2缺包')
    elif damage == 'viewer': s.p.role = 'viewer'
    elif damage == 'revoked': s.state['authorized'] = False
    if damage != 'cancelled_after_await':
        before = [(r, row_bytes(r)) for r in s.db.rows]
        with pytest.raises(HTTPException): o.execute(s.db, s.p, s.m.id, 'resume_with_profile', body(value))
        assert_unchanged(before)
        return
    o.execute(s.db, s.p, s.m.id, 'resume_with_profile', body(value))
    job = jobs(s, 'recognize_material_ocr_unit')[-1]
    def cancel():
        o.execute(s.db, s.p, s.m.id, 'cancel', body(command(s, expected_run_version=s.run.version, reason='明确停止')))
    s.state['recognition_hook'] = cancel
    o.process_unit(s.db, s.p, job)
    assert s.run.status == 'cancelled'
    assert page.data['raw_id'] is None
    assert not any(isinstance(v, o.Version) and v.unit_id == page.id for v in s.db.rows)


def test_retry_keeps_old_selected_profile_instead_of_latest_v2(bounded):
    s = bounded; _, page, _ = partial(s); accept(s)
    old_engine = copy.deepcopy(page.data['recognition_engine'])
    old_authorization = page.data['recognition_authorization']
    s.run.status = 'failed'; o.seal(s.run)
    o.execute(s.db, s.p, s.m.id, 'retry', body(command(s, expected_run_version=s.run.version, reason='按原授权恢复')))
    assert page.data['recognition_engine'] == old_engine == s.low
    assert page.data['recognition_authorization'] == old_authorization
    o.process_unit(s.db, s.p, jobs(s, 'recognize_material_ocr_unit')[-1])
    assert s.selected_hashes[-1] == s.low['manifest_hash']
