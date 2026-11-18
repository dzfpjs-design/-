"""Read-only list quarantine; every input and DB is isolated and synthetic."""
import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from generic_agents.api import app
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from generic_agents import report_evidence_pipeline as evidence
from generic_agents.db import engine
from generic_agents.models import Job, SetupReportDraft
from generic_agents.security import Principal
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_delegated_origin import authorized_launch, authorized_proposed


@pytest.fixture(autouse=True)
def fence(monkeypatch):
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    from generic_agents import report_evidence_delegated_budget_start as budget
    import tests.test_report_full_reading_source as source
    import tests.test_report_full_reading_pipeline as full
    def fenced():
        url = engine().url
        assert url.host == '127.0.0.1' and url.database.startswith('ga_test_')
    fenced()
    monkeypatch.setattr(source, 'fence', fenced)
    monkeypatch.setattr(full, 'fence', fenced)
    monkeypatch.setattr(gate, 'ready', lambda: True)
    monkeypatch.setattr(budget, '_enabled', lambda: True)
    if not inspect(engine()).has_table('report_full_provisional_candidates', schema='public'):
        from generic_agents.report_full_provisional_migration import install
        install(engine())


def test_valid_and_corrupt_origin_coexist_and_are_isolated(reading_scene, monkeypatch):
    scene = reading_scene
    provider, _, _, command, proposal = authorized_proposed(scene, monkeypatch)
    created = authorized_launch(scene, command, proposal)
    assert created.status_code == 202, created.text
    bad = created.json()['draft']
    # Build a synthetic legacy fe_ fixture without executing it. Suppress only
    # admission overlap while constructing this same-document test fixture.
    from generic_agents import report_unresolved_overlap as overlap
    original_guard = overlap.guard_new_paid_sources
    original_pending_guard = evidence.legacy.guard_existing_paid
    monkeypatch.setattr(evidence.legacy, 'guard_existing_paid', lambda *a, **k: None)
    monkeypatch.setattr(overlap, 'guard_new_paid_sources', lambda *a, **k: None)
    body = copy.deepcopy(scene['body']); body['request_id'] = 'fe_' + str(uuid4())
    preview = scene['client'].post('/api/setup/report-evidence-readings/preview', json=body)
    assert preview.status_code == 200, preview.text
    good = scene['client'].post('/api/setup/report-evidence-readings', json={
        **body, 'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert good.status_code == 202, good.text
    good_id = good.json()['draft']['id']
    monkeypatch.setattr(overlap, 'guard_new_paid_sources', original_guard)
    monkeypatch.setattr(evidence.legacy, 'guard_existing_paid', original_pending_guard)
    with Session(engine()) as db:
        marker = db.scalar(select(Job).where(Job.kind == 'report_evidence_origin_v1',
            Job.data['payload']['new_draft_id'].as_string() == bad['id']))
        assert marker
        marker.data = {**marker.data, 'origin_hash': '0' * 64}
        db.commit()
        before = copy.deepcopy(db.get(SetupReportDraft, bad['id']).data), copy.deepcopy(marker.data)
    path = '/api/setup/report-evidence-readings'
    v1 = scene['client'].get(path); assert v1.status_code == 200, v1.text
    assert set(v1.json()) == {'schema', 'actor_id', 'workspace_id', 'items', 'list_hash'}
    assert [x['id'] for x in v1.json()['items']] == [good_id]
    v2 = scene['client'].get(path + '/list-v2'); assert v2.status_code == 200, v2.text
    result = v2.json(); assert result['quarantined_count'] == 1
    assert result['list'] == v1.json()
    assert bad['id'] not in v2.text and bad['request_id'] not in v2.text
    blocked = scene['client'].get(path + '/' + bad['id'])
    assert blocked.status_code == 409
    assert 'preview' not in blocked.text and 'material_refs' not in blocked.text
    recovery = scene['client'].post(path + '/' + bad['id'] + '/recover-saved',
        json={'revision': bad['revision'], 'request_id': str(uuid4())})
    assert recovery.status_code == 409
    username, password = 'quarantine_' + uuid4().hex[:12], 'Synthetic-' + uuid4().hex
    new_user = scene['client'].post('/api/users', json={'username': username, 'password': password, 'role': 'admin'})
    assert new_user.status_code == 201, new_user.text
    with TestClient(app) as other:
        assert other.get(path + '/list-v2').status_code == 401
        logged = other.post('/api/login', json={'username': username, 'password': password})
        assert logged.status_code == 200
        other.headers.update({'X-CSRF-Token': logged.json()['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        hidden = other.get(path + '/list-v2')
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()['quarantined_count'] == 0 and hidden.json()['list']['items'] == []
        assert other.get(path + '/' + bad['id']).status_code == 404
    with Session(engine()) as db:
        after = (copy.deepcopy(db.get(SetupReportDraft, bad['id']).data),
                 copy.deepcopy(db.scalar(select(Job).where(Job.id == marker.id)).data))
    assert before == after
    assert len(provider.calls) == 1
    def read_for(p):
        with Session(engine()) as db:
            return evidence._list_checked(db, p)
    p = scene['p']
    other_actor = Principal(str(uuid4()), p.workspace_id, p.role, '')
    other_workspace = Principal(p.user_id, str(uuid4()), p.role, '')
    assert read_for(other_actor)[1] == 0 and read_for(other_actor)[0]['items'] == []
    assert read_for(other_workspace)[1] == 0 and read_for(other_workspace)[0]['items'] == []
    with ThreadPoolExecutor(max_workers=4) as pool:
        counts = list(pool.map(lambda _: read_for(p)[1], range(8)))
    assert counts == [1] * 8


def test_unexpected_auth_and_backend_failures_are_not_hidden(reading_scene, monkeypatch):
    scene = reading_scene
    _, _, _, command, proposal = authorized_proposed(scene, monkeypatch)
    result = authorized_launch(scene, command, proposal)
    assert result.status_code == 202, result.text
    from generic_agents import report_evidence_delegated_origin as origin
    def denied(*a, **kw):
        raise HTTPException(403, {'code': 'authority_changed'})
    monkeypatch.setattr(origin, 'verify_link', denied)
    reply = scene['client'].get('/api/setup/report-evidence-readings/list-v2')
    assert reply.status_code == 403
    def broken(*a, **kw):
        raise RuntimeError('synthetic backend failure')
    monkeypatch.setattr(origin, 'verify_link', broken)
    with Session(engine()) as db, pytest.raises(RuntimeError, match='synthetic backend'):
        evidence._list_checked(db, scene['p'])
