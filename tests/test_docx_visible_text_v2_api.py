"""Authenticated comparison of synthetic immutable DOCX bytes; never models."""
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job, Membership, SetupReportDraft, Workspace
from generic_agents import map_access
from tests.test_material_ocr import upload
from tests.test_native_report_reading_pure import doc, p


def test_only_hyphen_insertions_are_aligned_even_with_existing_unicode():
    from generic_agents.docx_visible_text_v2_api import _only_added_hyphens
    assert _only_added_hyphens('旧‑词', '旧‑词‑新') is False
    assert _only_added_hyphens('旧‑词', '旧‑词‑')
    assert _only_added_hyphens('文字', ' ‑文字 ')
    assert not _only_added_hyphens('原文', '改文')


def test_api_recalculates_same_source_after_new_client_without_changing_any_state(client):
    word = doc(p('正常正文') + '<w:p><w:r><w:noBreakHyphen/><w:t>20℃</w:t></w:r></w:p>')
    material = upload(client, word, 'synthetic-hyphens.docx')
    assert material['status'] in {'parsed', 'needs_input'}
    route = '/api/setup/materials/' + material['id'] + '/docx-visible-text-v2'
    params = {'version': material['version'], 'source_hash': material['hash']}
    with Session(engine()) as db:
        before = (db.scalar(select(func.count()).select_from(Job)), db.scalar(select(func.count()).select_from(CloudCall)),
                  db.scalar(select(func.count()).select_from(SetupReportDraft)))
    assert before[2] == 0
    original = client.get('/api/setup/materials/' + material['id'] + '/content')
    assert original.status_code == 200 and original.content == word
    first = client.get(route, params=params)
    assert first.status_code == 200, first.text
    value = first.json(); assert first.headers['cache-control'] == 'private, no-store'
    assert value['material'] == {'id': material['id'], 'version': material['version'], 'hash': material['hash']}
    assert value['legacy_preserved'] is True and value['saved_new_view'] is False
    assert value['can_apply'] is False and value['can_send'] is False
    assert len(value['changes']) == 1 and value['changes'][0]['before'] == '20℃'
    assert value['changes'][0]['after'] == '\u201120℃'
    assert value['comparison_hash'] == digest({k: v for k, v in value.items() if k != 'comparison_hash'})
    with TestClient(app) as other:
        other.cookies.update(client.cookies)
        for key in ('X-Workspace-ID', 'X-CSRF-Token'):
            if key in client.headers: other.headers[key] = client.headers[key]
        again = other.get(route, params=params)
        assert again.status_code == 200 and again.json() == value
    assert client.get(route, params={**params, 'source_hash': '0' * 64}).status_code == 409
    assert client.get('/api/setup/materials/' + material['id'] + '/content').content == word
    with Session(engine()) as db:
        assert (db.scalar(select(func.count()).select_from(Job)), db.scalar(select(func.count()).select_from(CloudCall)),
                db.scalar(select(func.count()).select_from(SetupReportDraft))) == before


def test_enabled_policy_rejects_operator_without_exposing_text(client):
    material = upload(client, doc(p('合成') + '<w:p><w:r><w:noBreakHyphen/><w:t>20</w:t></w:r></w:p>'), 'synthetic.docx')
    me = client.get('/api/me').json()
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == me['workspace_id'], Membership.user_id == me['user_id']))
        member.role = 'operator'
        db.add(Job(workspace_id=me['workspace_id'], kind=map_access.KIND, status='internal',
                   idempotency_key=map_access.KEY, data={'businesses': {}}))
        db.commit()
    route = '/api/setup/materials/' + material['id'] + '/docx-visible-text-v2'
    response = client.get(route, params={'version': material['version'], 'source_hash': material['hash']})
    assert response.status_code == 403 and '合成' not in response.text


def test_other_workspace_cannot_read_material(client):
    material = upload(client, doc(p('仅本工作区') + '<w:p><w:r><w:noBreakHyphen/><w:t>2</w:t></w:r></w:p>'), 'synthetic.docx')
    me = client.get('/api/me').json()
    with Session(engine()) as db:
        workspace = Workspace(name='synthetic-other-workspace'); db.add(workspace); db.flush()
        db.add(Membership(workspace_id=workspace.id, user_id=me['user_id'], role='admin')); db.commit()
        other_id = workspace.id
    client.headers['X-Workspace-ID'] = other_id
    route = '/api/setup/materials/' + material['id'] + '/docx-visible-text-v2'
    response = client.get(route, params={'version': material['version'], 'source_hash': material['hash']})
    assert response.status_code == 404 and '仅本工作区' not in response.text
