"""Workspace isolation and immutable source checks for the read-only audit."""
import io
import zipfile
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Evidence, Membership, SetupMaterial, User, Workspace
from generic_agents.security import password_hash


def sample_docx():
    xml = b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Body</w:t></w:r></w:p>
      <w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/>
      </w:docPartObj></w:sdtPr><w:sdtContent><w:p><w:r><w:t>Contents entry</w:t>
      </w:r></w:p><w:p><w:r><w:noBreakHyphen/><w:t>20</w:t><w:softHyphen/><w:t>50</w:t>
      </w:r></w:p></w:sdtContent></w:sdt></w:body></w:document>'''
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return data.getvalue()


def test_audit_is_read_only_workspace_scoped_and_checks_source_integrity(client):
    original = sample_docx()
    response = client.post('/api/setup/materials', files={'file': ('sample.docx', original)})
    assert response.status_code == 201
    material = response.json()
    url = '/api/setup/materials/' + material['id']
    before = client.get(url).json()
    summary = client.get(url + '/docx-structure')
    assert summary.status_code == 200
    assert summary.headers['cache-control'] == 'private, no-store'
    assert 'candidates' not in summary.json()
    details = client.get(url + '/docx-structure', params={'include_candidates': 'true'})
    assert details.status_code == 200
    assert any(item['text'] == 'Contents entry' and item['review_required']
               for item in details.json()['candidates'])
    assert any(item['text'] == '\u201120\u00ad50' and item['review_required']
               for item in details.json()['candidates'])
    assert client.get(url + '/docx-structure', params={'include_candidates': 'true'}).json() == details.json()
    assert client.get(url).json() == before
    assert client.get(url + '/content').content == original

    username, password = uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password))
        workspace = Workspace(name='other')
        db.add_all([user, workspace]); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role='admin')); db.commit()
    with TestClient(app) as other:
        assert other.post('/api/login', json={'username': username, 'password': password}).status_code == 200
        assert other.get(url + '/docx-structure', params={'include_candidates': 'true'}).status_code == 404

    with Session(engine()) as db:
        record = db.get(SetupMaterial, material['id'])
        evidence = db.get(Evidence, record.evidence_id)
        (settings().storage_dir / evidence.storage_key).write_bytes(b'changed')
    assert client.get(url + '/docx-structure').status_code == 409


def test_non_docx_is_rejected(client):
    response = client.post('/api/setup/materials', files={'file': ('readme.txt', b'text')})
    assert response.status_code == 201
    assert client.get('/api/setup/materials/' + response.json()['id'] + '/docx-structure').status_code == 415
