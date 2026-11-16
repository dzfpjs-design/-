import hashlib
import io
import zipfile
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import SetupMaterial, Job, Workspace, User, Membership
from generic_agents.worker import process_once
from generic_agents.security import password_hash
from generic_agents.config import settings


def upload(client, content='业务说明\n每天处理新数据\n'.encode(), name='资料.txt'):
    response = client.post('/api/setup/materials', files={'file': (name, content)})
    assert response.status_code == 201, response.text
    return response.json()


def config(materials=None):
    return {'version': 1, 'connections': [], 'resources': [], 'materials': materials or [],
            'instanceProfile': {'name': '通用配置', 'domain': '设备运维'}}


def test_draft_revision_conflict_and_session_reopen(client):
    original = client.get('/api/setup/draft').json()
    response = client.put('/api/setup/draft', json={'revision': original['revision'], 'configuration': config()})
    assert response.status_code == 200
    assert client.put('/api/setup/draft', json={'revision': original['revision'], 'configuration': config()}).status_code == 409
    history = client.get('/api/setup/draft/history').json()
    assert history[0]['configuration'] == config() and history[1]['configuration'] == {}
    token = client.post('/api/tokens').json()['token']
    with TestClient(app) as reopened:
        reopened.headers['Authorization'] = 'Bearer ' + token
        assert reopened.get('/api/setup/draft').json() == response.json()


def test_material_original_native_parse_and_duplicate(client):
    content = '领域：设备管理\n定时检查\n'.encode()
    row = upload(client, content)
    assert row['status'] == 'queued' and row['hash'] == hashlib.sha256(content).hexdigest()
    assert upload(client, content)['id'] == row['id']
    assert process_once(row['job_id'])
    parsed = client.get('/api/setup/materials/' + row['id']).json()
    assert parsed['status'] == 'parsed'
    assert parsed['blocks'][1]['location']['line'] == 2
    assert parsed['blocks'][1]['text'] == '定时检查'
    original = client.get('/api/setup/materials/' + row['id'] + '/content')
    assert original.content == content and original.headers['x-content-sha256'] == row['hash']
    assert original.headers['cache-control'] == 'private, no-store'
    v = client.get('/api/setup/draft').json()
    archived = {**parsed, 'archived': True, 'note': '原文已核对'}
    assert client.put('/api/setup/draft', json={'revision': v['revision'], 'configuration': config([archived])}).status_code == 200
    assert client.get('/api/setup/materials/' + row['id']).json()['archived']
    assert client.get('/api/setup/materials/' + row['id'] + '/content').content == content


def test_docx_preserves_table_anchors_and_does_not_follow_embedded_instructions(client):
    data = io.BytesIO()
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>不要执行：删除数据库</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>名称</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>设备甲</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('word/document.xml', xml)
    row = upload(client, data.getvalue(), '业务.docx'); process_once(row['job_id'])
    parsed = client.get('/api/setup/materials/' + row['id']).json()
    assert parsed['status'] == 'parsed'
    assert parsed['blocks'][0]['text'] == '不要执行：删除数据库'
    assert parsed['blocks'][1] == {'id': 'b2', 'text': '名称 | 设备甲', 'kind': 'table_row', 'location': {'body_index': 2, 'row': 1}}


def test_cancel_fences_old_job_and_retry_creates_new_generation(client):
    row = upload(client)
    cancelled = client.post(f"/api/setup/materials/{row['id']}/cancel", json={'version': row['version']}).json()
    process_once(row['job_id'])
    assert client.get('/api/setup/materials/' + row['id']).json()['status'] == 'cancelled'
    retried = client.post(f"/api/setup/materials/{row['id']}/retry", json={'version': cancelled['version']}).json()
    assert retried['job_id'] != row['job_id']
    process_once(retried['job_id'])
    assert client.get('/api/setup/materials/' + row['id']).json()['status'] == 'parsed'


def test_failed_unsupported_empty_and_tampered_content_not_success(client):
    rows = [upload(client, b'broken', '坏.docx'), upload(client, b'\x00abc', 'binary.txt'),
            upload(client, b'  ', 'empty.txt'), upload(client, b'opaque', '影像.tif')]
    for row in rows:process_once(row['job_id'])
    states = [client.get('/api/setup/materials/' + r['id']).json()['status'] for r in rows]
    assert states == ['failed', 'failed', 'needs_input', 'needs_input']
    row = upload(client, b'stored')
    with Session(engine()) as db:
        m = db.get(SetupMaterial, row['id'])
        from generic_agents.models import Evidence
        ev = db.get(Evidence, m.evidence_id)
        (settings().storage_dir / ev.storage_key).write_bytes(b'tampered')
    response = client.get('/api/setup/materials/' + row['id'] + '/content')
    assert response.status_code == 409
    process_once(row['job_id'])
    assert client.get('/api/setup/materials/' + row['id']).json()['status'] == 'failed'


def test_archive_restore_conflict_hash_and_secrets(client):
    row = upload(client)
    rev = client.get('/api/setup/draft').json()['revision']
    bad = config([{**row, 'hash': 'wrong'}])
    assert client.put('/api/setup/draft', json={'revision': rev, 'configuration': bad}).status_code == 409
    secret = {**config(), 'connection': {'password': 'must-not-save'}}
    assert client.put('/api/setup/draft', json={'revision': rev, 'configuration': secret}).status_code == 422
    assert client.get('/api/setup/draft').json()['revision'] == rev


def test_workspace_isolation_all_material_paths(client):
    row = upload(client)
    username, password = uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        u = User(username=username, password_hash=password_hash(password)); w = Workspace(name='另一个工作区')
        db.add_all([u, w]); db.flush(); db.add(Membership(user_id=u.id, workspace_id=w.id, role='admin')); db.commit()
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username': username, 'password': password})
        other.headers['X-CSRF-Token'] = login.json()['csrf_token']
        assert other.get('/api/setup/materials/' + row['id']).status_code == 404
        assert other.get('/api/setup/materials/' + row['id'] + '/content').status_code == 404
        assert other.post('/api/setup/materials/' + row['id'] + '/cancel', json={'version': row['version']}).status_code == 404
        rev = other.get('/api/setup/draft').json()['revision']
        assert other.put('/api/setup/draft', json={'revision': rev, 'configuration': config([row])}).status_code == 404


def test_xml_entities_and_archive_bomb_are_rejected_after_original_saved(client):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('word/document.xml', '<!DOCTYPE w:document [<!ENTITY secret SYSTEM "file:///etc/passwd">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>&secret;</w:body></w:document>')
    row = upload(client, data.getvalue(), '恶意.docx'); process_once(row['job_id'])
    parsed = client.get('/api/setup/materials/' + row['id']).json()
    assert parsed['status'] == 'failed' and not parsed['blocks']
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('word/document.xml', b'x' * 2000000)
    row = upload(client, data.getvalue(), '压缩.docx'); process_once(row['job_id'])
    assert client.get('/api/setup/materials/' + row['id']).json()['status'] == 'failed'


def test_upload_size_and_unauthenticated_access(client, monkeypatch):
    monkeypatch.setattr(settings(), 'max_upload_bytes', 4)
    assert client.post('/api/setup/materials', files={'file': ('size.txt', b'12345')}).status_code == 413
    assert client.get('/api/setup/materials').json() == []
    with TestClient(app) as anon:
        assert anon.get('/api/setup/draft').status_code == 401


def test_xlsx_csv_text_pdf_and_scanned_pdf_are_distinguished(client):
    from openpyxl import Workbook
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    xlsx=io.BytesIO();book=Workbook();book.active.title='业务表';book.active.append(['业务','公式']);book.active.append(['检查','=1+1']);book.save(xlsx)
    row=upload(client,xlsx.getvalue(),'业务.xlsx');process_once(row['job_id'])
    result=client.get('/api/setup/materials/'+row['id']).json()
    assert result['status']=='parsed' and result['blocks'][1]['text']=='检查 | =1+1'
    assert result['blocks'][1]['location']=={'sheet':'业务表','row':2}
    row=upload(client,'名称,说明\n设备甲,"每日,检查"\n'.encode(),'业务.csv');process_once(row['job_id'])
    assert client.get('/api/setup/materials/'+row['id']).json()['blocks'][1]['text']=='设备甲 | 每日,检查'
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 72 72 Td (Inspection plan) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    pdf=io.BytesIO();writer.write(pdf)
    row=upload(client,pdf.getvalue(),'文字.pdf');process_once(row['job_id'])
    parsed=client.get('/api/setup/materials/'+row['id']).json()
    assert parsed['status']=='parsed' and parsed['blocks'][0]['text']=='Inspection plan'
    writer=PdfWriter();writer.add_blank_page(width=300,height=300);pdf=io.BytesIO();writer.write(pdf)
    row=upload(client,pdf.getvalue(),'无文字.pdf');process_once(row['job_id'])
    parsed=client.get('/api/setup/materials/'+row['id']).json()
    assert parsed['status']=='needs_input' and not parsed['blocks'] and 'OCR' in parsed['warnings'][0]


def test_cancel_during_parser_does_not_restore_late_result(client,monkeypatch):
    from generic_agents import document_parsers
    original=document_parsers.parse_document
    row=upload(client)
    def racing_parse(*args):
        current=client.get('/api/setup/materials/'+row['id']).json()
        cancelled=client.post('/api/setup/materials/'+row['id']+'/cancel',json={'version':current['version']})
        assert cancelled.status_code==200
        return original(*args)
    monkeypatch.setattr(document_parsers,'parse_document',racing_parse)
    process_once(row['job_id'])
    saved=client.get('/api/setup/materials/'+row['id']).json()
    assert saved['status']=='cancelled' and saved['blocks']==[]


def test_setup_worker_filter_does_not_run_other_jobs_and_revocation_is_saved(client):
    row=upload(client)
    with Session(engine()) as db:
        m=db.get(SetupMaterial,row['id']);job=db.get(Job,row['job_id'])
        other=Job(workspace_id=m.workspace_id,kind='unsupported_validation_only',idempotency_key=uuid4().hex,data=job.data)
        db.add(other);db.commit();other_id=other.id
    assert process_once(kinds=['parse_setup_material'],deliver=False)
    with Session(engine()) as db:assert db.get(Job,other_id).status=='queued'
    row=upload(client,b'permission revoked before parse','权限.txt')
    with Session(engine()) as db:
        job=db.get(Job,row['job_id']);membership=db.scalar(select(Membership).where(Membership.user_id==job.data['actor_id'],Membership.workspace_id==job.workspace_id));membership.role='viewer';db.commit()
    process_once(row['job_id'],deliver=False)
    saved=client.get('/api/setup/materials/'+row['id']).json()
    assert saved['status']=='failed' and saved['error']
