"""本地 OCR 持久流程：引擎为显式测试替身，不声明真实识别质量。"""
import hashlib
import io
import struct
import zlib
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Evidence, SetupMaterial, Job, Membership, User, CloudCall
from generic_agents.config import settings
from generic_agents.security import password_hash
from generic_agents.worker import process_once


def png(width=96, height=60):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\xff\xff' * width) * height)) + chunk(b'IEND', b''))


def scan_pdf(pages=2):
    from pypdf import PdfWriter
    out = io.BytesIO(); writer = PdfWriter()
    for _ in range(pages): writer.add_blank_page(width=96, height=60)
    writer.write(out)
    return out.getvalue()


def inline_image_pdf(annotation=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,ArrayObject,FloatObject,DecodedStreamObject
    out=io.BytesIO();writer=PdfWriter();page=writer.add_blank_page(width=96,height=60)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 10 Tf 1 40 Td (Native header only) Tj ET\nq 10 0 0 10 1 1 cm BI /W 1 /H 1 /BPC 8 /CS /RGB ID '+b'\xff\x00\x00'+b' EI Q')
    page[NameObject('/Contents')]=writer._add_object(stream)
    if annotation:
        page[NameObject('/Annots')]=ArrayObject([writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Annot'),
            NameObject('/Subtype'):NameObject('/Text'),NameObject('/Rect'):ArrayObject([FloatObject(x) for x in [1,1,10,10]])}))])
    writer.write(out);return out.getvalue()


def image_docx(external=False):
    out = io.BytesIO()
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
           'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
           '<w:body><w:p><w:r><w:t>正文不代表图片已读取</w:t></w:r></w:p>'
           '<w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p></w:body></w:document>')
    relation = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                + ('Target="https://invalid.example/image.png" TargetMode="External"' if external else 'Target="media/image1.png"')
                + '/></Relationships>')
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('word/document.xml', xml)
        archive.writestr('word/_rels/document.xml.rels', relation)
        archive.writestr('word/media/image1.png', png())
    return out.getvalue()


@pytest.fixture
def local_runtime(monkeypatch):
    from generic_agents import material_ocr_runtime as runtime
    manifest = {'contract_version': 1, 'data_policy': 'local', 'engine': 'test-only-cpu', 'weights': []}
    state = {'calls': 0, 'fail': False}
    def engine_status(*, engine_hash=None):
        if engine_hash is None:
            return {'id': 'test-local', 'name': '显式测试引擎', 'ready': True, 'reason': None,
                    'manifest': manifest, 'manifest_hash': digest(manifest)}
        if engine_hash == runtime.LOW_MEMORY_MANIFEST_HASH:
            return {'id': runtime.LOW_MEMORY_ENGINE_ID, 'name': '显式测试禁用低内存引擎',
                    'ready': False, 'reason': '本夹具未运行或授权低内存识别配置',
                    'manifest': None, 'manifest_hash': None}
        if engine_hash == runtime.LOW_MEMORY_V2_MANIFEST_HASH:
            return {'id': runtime.LOW_MEMORY_V2_ENGINE_ID, 'name': '显式测试禁用检测限宽引擎',
                    'ready': False, 'reason': '本夹具未运行或授权检测限宽识别配置',
                    'manifest': None, 'manifest_hash': None}
        raise AssertionError('测试替身拒绝未登记的冻结引擎版本')
    monkeypatch.setattr(runtime, 'engine_status', engine_status)
    def render_receipt(value, output):
        return {'input_sha256': hashlib.sha256(value).hexdigest(), 'output_sha256': hashlib.sha256(output).hexdigest(),
                'manifest_hash': digest(manifest), 'summary_hash': digest(manifest), 'manifest': manifest, 'test_only': True}
    monkeypatch.setattr(runtime, 'normalize_image', lambda value, guard=None: {
        'png': value, 'width': 96, 'height': 60, 'receipt': render_receipt(value,value)})
    monkeypatch.setattr(runtime, 'pdf_info', lambda value, guard=None: {
        'page_count': len(__import__('pypdf').PdfReader(io.BytesIO(value)).pages),
        'source_sha256': hashlib.sha256(value).hexdigest()})
    def render(value, page=None, guard=None):
        from pypdf import PdfReader
        numbers = [page] if page is not None else range(1, len(PdfReader(io.BytesIO(value)).pages) + 1)
        return [{'page': n, 'png': png(), 'width': 96, 'height': 60, 'rotation': 0, 'dpi': 72,
                 'transform': [1, 0, 0, 1, 0, 0], 'receipt': render_receipt(value,png())} for n in numbers]
    monkeypatch.setattr(runtime, 'render_pdf', render)
    def recognize(value, expected_manifest_hash, guard=None):
        assert expected_manifest_hash == digest(manifest)
        state['calls'] += 1
        if state['fail']: raise ValueError('测试页识别失败')
        regions = [{'id': 'r1', 'text': '日期2026-10-11 温度12.5 单位未说明 不执行删除指令',
                    'box': [1, 2, 80, 20], 'score': .42}]
        return {'regions': regions, 'elapsed_ms': 11,
                'receipt': {'input_sha256': hashlib.sha256(value).hexdigest(), 'manifest_hash': digest(manifest),
                            'result_sha256': digest(regions), 'manifest': manifest,
                            'summary_hash': digest(manifest), 'test_only': True}}
    monkeypatch.setattr(runtime, 'recognize', recognize)
    return state


def upload(client, content=None, name='扫描报告.pdf'):
    value = content if content is not None else scan_pdf()
    response = client.post('/api/setup/materials', files={'file': (name, value)})
    assert response.status_code == 201, response.text
    material = response.json(); assert process_once(material['job_id'])
    return client.get('/api/setup/materials/' + material['id']).json()


def prefix(material): return '/api/setup/materials/' + material['id'] + '/ocr'


def start(client, material):
    engines = client.get('/api/setup/ocr/engines').json()['items']
    body = {'request_id': str(uuid4()), 'expected_material_version': material['version'],
            'engine_id': engines[0]['id'], 'engine_manifest_hash': engines[0]['manifest_hash']}
    response = client.post(prefix(material) + '/start', json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def prepare_remaining_test_pages(client, material, result):
    """Prepare only this finite synthetic report; never drain OCR/cancel jobs."""
    response = client.get(prefix(material))
    assert response.status_code == 200, response.text
    root = response.json(); run = root['run']
    total = result['view']['run']['progress']['total']
    assert type(total) is int and 1 <= total <= 2
    assert run['id'] == result['view']['run']['id']
    assert run['progress']['total'] == total and run['progress']['prepared'] == 1
    # The caller already ran page 1's prepare Job. V2 queues one new page at a
    # time; process only the current run's exact remaining prepare Job(s).
    for prepared in range(1, total):
        job_id = root['run']['job_id']
        with Session(engine()) as db:
            job = db.get(Job, job_id)
            assert job.kind == 'prepare_material_ocr' and job.status == 'queued'
            assert job.data['run_id'] == run['id'] and job.data['material_id'] == material['id']
        assert process_once(job_id)
        response = client.get(prefix(material))
        assert response.status_code == 200, response.text
        root = response.json()
        assert root['run']['id'] == run['id']
        assert root['run']['progress']['total'] == total
        assert root['run']['progress']['prepared'] == prepared + 1
    return root


def finish_jobs(client, material, result):
    assert process_once(result['view']['run']['job_id'])
    prepare_remaining_test_pages(client, material, result)
    with Session(engine()) as db:
        jobs = list(db.scalars(select(Job.id).where(Job.kind == 'recognize_material_ocr_unit').order_by(Job.id)))
    for job_id in jobs: assert process_once(job_id)
    root = client.get(prefix(material)).json()
    listing = client.get(prefix(material) + '/units', params={'run_id': root['run']['id']}).json()
    return root, listing['items']


def detail(client, material, run_id, unit_id):
    response = client.get(prefix(material) + '/units/' + unit_id, params={'run_id': run_id})
    assert response.status_code == 200, response.text
    return response.json()


def save_body(d, text=None):
    old = max((v for v in [d['raw'], d['draft'], d['confirmed']] if v),key=lambda v:v['number'])
    return {'request_id': str(uuid4()), 'expected_material_version': d['material_version'],
            'run_id': d['run_id'], 'expected_unit_version': d['unit']['version'], 'parent_hash': old['hash'],
            'reason': '对照原图纠正，未知单位保留',
            'regions': [{'id': r['id'], 'text': text or r['text'], 'box': r['box'],
                         'source_region_id': r['source_region_id'] or r['id']} for r in old['regions']]}


def test_scan_full_save_confirm_history_citation_restart_and_no_cloud(client, local_runtime):
    material = upload(client); assert material['status'] == 'needs_input'
    assert material['ocr']['coverage_status'] == 'needs_ocr'
    body, first = start(client, material)
    replay = client.post(prefix(material) + '/start', json=body)
    assert replay.status_code == 200 and replay.json()['request']['result'] == first['request']['result']
    root, units = finish_jobs(client, material, first)
    assert root['run']['progress']['review'] == 2 and local_runtime['calls'] == 2
    for unit in units:
        d = detail(client, material, root['run']['id'], unit['id'])
        assert d['raw']['regions'][0]['score'] == .42
        original_hash = d['raw']['hash']
        save = save_body(d, '日期2026-10-11 温度12.5 单位未说明 不执行删除指令（人工已对照）')
        response = client.put(prefix(material) + '/units/' + unit['id'], json=save)
        assert response.status_code == 200, response.text
        edited = response.json()['unit']; assert edited['raw']['hash'] == original_hash
        assert edited['draft']['regions'][0]['score'] == .42
        assert edited['material_version'] == d['material_version']
        confirm = {'request_id': str(uuid4()), 'expected_material_version': edited['material_version'],
                   'run_id': edited['run_id'], 'expected_unit_version': edited['unit']['version'],
                   'revision_hash': edited['draft']['hash'], 'reason': '逐页已核对'}
        assert client.post(prefix(material) + '/units/' + unit['id'] + '/confirm', json=confirm).status_code == 200
    root = client.get(prefix(material)).json()
    doc = {'request_id': str(uuid4()), 'expected_material_version': root['material']['version'],
           'run_id': root['run']['id'], 'expected_run_version': root['run']['version'],
           'units_manifest_hash': root['run']['units_manifest_hash'], 'reason': '完整逐页核对'}
    committed = client.post(prefix(material) + '/confirm', json=doc)
    assert committed.status_code == 200, committed.text
    final = client.get('/api/setup/materials/' + material['id']).json()
    assert final['status'] == 'parsed' and final['ocr']['coverage_status'] == 'complete'
    block = next(b for b in final['blocks'] if b.get('location', {}).get('ocr'))
    assert '单位未说明' in block['text'] and '日期2026-10-11' in block['text']
    citation = client.get(prefix(material) + '/citations/' + block['id'], params={'quote': '单位未说明'})
    assert citation.status_code == 200 and citation.json()['block'] == block
    token = client.post('/api/tokens').json()['token']
    with TestClient(app) as reopened:
        reopened.headers['Authorization'] = 'Bearer ' + token
        restored = reopened.get(prefix(material)).json()
        assert restored['confirmation'] == committed.json()['view']['confirmation']
        locator = block['location']['ocr']
        image = reopened.get(prefix(material) + '/confirmations/' + locator['confirmation_id'] + '/units/'
                             + locator['unit_id'] + '/image', params={'image_sha256': locator['image_sha256']})
        assert image.status_code == 200 and hashlib.sha256(image.content).hexdigest() == locator['image_sha256']
    with Session(engine()) as db: assert db.scalar(select(func.count()).select_from(CloudCall)) == 0


def test_image_docx_coverage_external_is_blocked_and_no_fake_page(client, local_runtime):
    material = upload(client, image_docx(), '内嵌图.docx')
    assert material['status'] == 'needs_input' and material['ocr']['coverage_status'] == 'needs_ocr'
    _, result = start(client, material); root, units = finish_jobs(client, material, result)
    assert len(units) == 1 and units[0]['kind'] == 'docx_image'
    assert units[0]['page'] is None and units[0]['body_index'] == 2
    bad = upload(client, image_docx(external=True), '外部图.docx')
    assert bad['ocr']['coverage_status'] == 'blocked'
    assert client.get(prefix(bad)).json()['permissions']['start']['allowed'] is False


def test_inline_image_with_native_header_is_not_complete_and_annotations_block(client, local_runtime):
    material=upload(client,inline_image_pdf())
    assert material['blocks'][0]['text']=='Native header only'
    assert material['status']=='needs_input' and material['ocr']['coverage_status']=='needs_ocr'
    annotated=upload(client,inline_image_pdf(annotation=True),'含批注.pdf')
    root=client.get(prefix(annotated)).json()
    assert root['coverage']['status']=='blocked' and root['permissions']['start']['allowed'] is False


@pytest.mark.parametrize('element',[
    '<w:altChunk r:id="other"/>',
    '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>未覆盖公式</m:t></m:r></m:oMath>',
    '<a:xfrm rot="5400000"/>',
    '<a:srcRect l="5000"/>',
    '<v:shape xmlns:v="urn:schemas-microsoft-com:vml" style="rotation:90"><v:imagedata r:id="rId1"/></v:shape>',
    '<v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:imagedata r:id="rId1" cropleft="32768f"/></v:shape>',
    '<w:sdt><w:sdtContent><w:p><w:r><w:t>原生未覆盖业务正文</w:t></w:r></w:p></w:sdtContent></w:sdt>'])
def test_docx_unsupported_visible_content_is_explicitly_blocked(client,local_runtime,element):
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(image_docx())) as source,zipfile.ZipFile(out,'w') as target:
        for name in source.namelist():
            value=source.read(name)
            if name=='word/document.xml':value=value.replace(b'</w:body>',element.encode()+b'</w:body>')
            target.writestr(name,value)
    material=upload(client,out.getvalue(),'未覆盖结构.docx')
    root=client.get(prefix(material)).json()
    assert root['coverage']['status']=='blocked' and root['coverage']['gaps']
    assert root['permissions']['start']['allowed'] is False


def test_docx_header_relationship_with_arbitrary_part_name_is_blocked(client,local_runtime):
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(image_docx())) as source,zipfile.ZipFile(out,'w') as target:
        for name in source.namelist():
            value=source.read(name)
            if name=='word/document.xml':
                value=value.replace(b'</w:body>',b'<w:sectPr><w:headerReference r:id="rHeader"/></w:sectPr></w:body>')
            if name=='word/_rels/document.xml.rels':
                value=value.replace(b'</Relationships>',b'<Relationship Id="rHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="letterhead.xml"/></Relationships>')
            target.writestr(name,value)
        target.writestr('word/letterhead.xml','<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>页眉中的必要业务字段</w:t></w:r></w:p></w:hdr>')
    material=upload(client,out.getvalue(),'非默认页眉.docx')
    root=client.get(prefix(material)).json()
    assert root['coverage']['status']=='blocked' and root['coverage']['gaps']
    assert not root['permissions']['start']['allowed']


def test_unit_history_restore_cas_and_nonce_probe(client, local_runtime):
    material = upload(client, scan_pdf(1)); _, result = start(client, material)
    root, units = finish_jobs(client, material, result); unit_id = units[0]['id']
    d = detail(client, material, root['run']['id'], unit_id)
    first = save_body(d, '第一份人工纠错，未知单位')
    r1 = client.put(prefix(material) + '/units/' + unit_id, json=first).json()['unit']
    probe = client.get(prefix(material) + '/requests/' + first['request_id']).json()
    assert probe['state'] == 'saved' and probe['request']['result']['revision_hash'] == r1['draft']['hash']
    stale = {**first, 'request_id': str(uuid4()), 'regions': [{**first['regions'][0], 'text': '过期稿'}]}
    rejected = client.put(prefix(material) + '/units/' + unit_id, json=stale)
    assert rejected.status_code == 409 and '本次请求未提交' in rejected.json()['detail']
    second = save_body(r1, '第二份人工纠错')
    r2 = client.put(prefix(material) + '/units/' + unit_id, json=second).json()['unit']
    history = client.get(prefix(material) + '/units/' + unit_id + '/versions',
                         params={'run_id': root['run']['id'], 'limit': 1}).json()
    assert history['total'] == 3 and history['next_cursor']
    restore = {'request_id': str(uuid4()), 'expected_material_version': r2['material_version'],
               'run_id': r2['run_id'], 'expected_unit_version': r2['unit']['version'],
               'revision_id': r1['draft']['id'], 'revision_hash': r1['draft']['hash'], 'reason': '恢复先前纠错'}
    response = client.post(prefix(material) + '/units/' + unit_id + '/restore', json=restore)
    assert response.status_code == 200, response.text
    assert response.json()['unit']['draft']['plain_text'] == '第一份人工纠错，未知单位'
    assert response.json()['unit']['draft']['number'] > r2['draft']['number']
    assert client.post(prefix(material) + '/units/' + unit_id + '/restore', json=restore).json()['request'] == response.json()['request']


def test_cancel_fences_old_job_and_retry_preserves_saved_page(client, local_runtime):
    material = upload(client); _, result = start(client, material)
    assert process_once(result['view']['run']['job_id'])
    root = client.get(prefix(material)).json()
    cancel = {'request_id': str(uuid4()), 'expected_material_version': root['material']['version'],
              'run_id': root['run']['id'], 'expected_run_version': root['run']['version'], 'reason': '停止后台识别'}
    assert client.post(prefix(material) + '/cancel', json=cancel).status_code == 200
    with Session(engine()) as db:
        jobs = list(db.scalars(select(Job.id).where(Job.kind == 'recognize_material_ocr_unit')))
    for job_id in jobs: process_once(job_id)
    assert local_runtime['calls'] == 0
    root = client.get(prefix(material)).json()
    retry = {**cancel, 'request_id': str(uuid4()), 'expected_material_version': root['material']['version'],
             'expected_run_version': root['run']['version'], 'reason': '恢复未完成页'}
    response = client.post(prefix(material) + '/retry', json=retry)
    assert response.status_code == 200, response.text


def test_original_or_image_tamper_blocks_confirmation(client, local_runtime):
    material = upload(client, scan_pdf(1)); _, result = start(client, material)
    root, units = finish_jobs(client, material, result)
    d = detail(client, material, root['run']['id'], units[0]['id'])
    with Session(engine()) as db:
        m = db.get(SetupMaterial, material['id']); ev = db.get(Evidence, m.evidence_id)
        (settings().storage_dir / ev.storage_key).write_bytes(b'changed original')
    response = client.put(prefix(material) + '/units/' + units[0]['id'], json=save_body(d))
    assert response.status_code == 409


def test_viewer_reads_but_cannot_start_or_edit(client, account, local_runtime):
    material = upload(client, scan_pdf(1))
    username, password = 'viewer_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=account[2], role='viewer')); db.commit()
    with TestClient(app) as viewer:
        login = viewer.post('/api/login', json={'username': username, 'password': password})
        viewer.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': account[2]})
        response = viewer.get(prefix(material)); assert response.status_code == 200
        assert response.json()['permissions']['start']['allowed'] is False
        body = {'request_id': str(uuid4()), 'expected_material_version': material['version'],
                'engine_id': 'test-local', 'engine_manifest_hash': digest({'test': True})}
        assert viewer.post(prefix(material) + '/start', json=body).status_code == 403


def confirm_page(client, material, d):
    saved = client.put(prefix(material) + '/units/' + d['unit']['id'], json=save_body(d))
    assert saved.status_code == 200, saved.text
    d = saved.json()['unit']
    command = {'request_id': str(uuid4()), 'expected_material_version': d['material_version'],
               'run_id': d['run_id'], 'expected_unit_version': d['unit']['version'],
               'revision_hash': d['draft']['hash'], 'reason': '原页已逐项核对'}
    response = client.post(prefix(material) + '/units/' + d['unit']['id'] + '/confirm', json=command)
    assert response.status_code == 200, response.text
    return response.json()['unit']


def confirm_document(client, material):
    root = client.get(prefix(material)).json()
    command = {'request_id': str(uuid4()), 'expected_material_version': root['material']['version'],
               'run_id': root['run']['id'], 'expected_run_version': root['run']['version'],
               'units_manifest_hash': root['run']['units_manifest_hash'], 'reason': '整份原件均已核对'}
    response = client.post(prefix(material) + '/confirm', json=command)
    assert response.status_code == 200, response.text
    return response.json()


def test_old_confirmation_citation_remains_exact_after_new_human_revision(client, local_runtime):
    material = upload(client, scan_pdf(1)); _, first = start(client, material)
    root, units = finish_jobs(client, material, first)
    d = confirm_page(client, material, detail(client, material, root['run']['id'], units[0]['id']))
    original = confirm_document(client, material)
    old = client.get('/api/setup/materials/' + material['id']).json()['blocks'][0]
    d = detail(client, material, root['run']['id'], units[0]['id'])
    body = save_body(d, '新人工版本日期2026-10-12，单位仍未知')
    response = client.put(prefix(material) + '/units/' + units[0]['id'], json=body)
    assert response.status_code == 200
    assert response.json()['view']['coverage']['status'] == 'needs_ocr'
    d = confirm_page(client, material, response.json()['unit'])
    latest = confirm_document(client, material)
    assert latest['view']['confirmation']['hash'] != original['view']['confirmation']['hash']
    response = client.get(prefix(material) + '/citations/' + old['id'], params={'quote': '单位未说明'})
    assert response.status_code == 200 and response.json()['block'] == old
    assert client.get(prefix(material) + '/citations/' + old['id'], params={'quote': '新人工版本'}).status_code == 409


def test_abandoned_unknown_unit_request_cannot_arrive_late(client, local_runtime):
    material = upload(client, scan_pdf(1)); _, result = start(client, material)
    root, units = finish_jobs(client, material, result)
    d = detail(client, material, root['run']['id'], units[0]['id']); command = save_body(d)
    probe = client.get(prefix(material) + '/requests/' + command['request_id']).json()
    assert probe['state'] == 'not_found'
    abandon = {'action': 'save', 'target': units[0]['id'], 'original_request': command,
               'expected_material_version': d['material_version'], 'reason': '明确放弃未提交的原纠错请求'}
    response = client.post(prefix(material) + '/requests/' + command['request_id'] + '/abandon', json=abandon)
    assert response.status_code == 200 and response.json()['request']['state'] == 'abandoned'
    assert client.put(prefix(material) + '/units/' + units[0]['id'], json=command).status_code == 409
    assert client.get(prefix(material) + '/requests/' + command['request_id']).json()['state'] == 'abandoned'


def test_saved_unknown_request_abandon_returns_original_receipt(client, local_runtime):
    material = upload(client, scan_pdf(1)); _, result = start(client, material)
    root, units = finish_jobs(client, material, result)
    d = detail(client, material, root['run']['id'], units[0]['id']); command = save_body(d)
    saved = client.put(prefix(material) + '/units/' + units[0]['id'], json=command).json()
    abandon = {'action': 'save', 'target': units[0]['id'], 'original_request': command,
               'expected_material_version': d['material_version'], 'reason': '核对未知结果'}
    response = client.post(prefix(material) + '/requests/' + command['request_id'] + '/abandon', json=abandon)
    assert response.status_code == 200 and response.json()['request'] == saved['request']
    wrong = {**abandon, 'target': str(uuid4())}
    assert client.post(prefix(material) + '/requests/' + command['request_id'] + '/abandon', json=wrong).status_code == 409


def test_frozen_versions_cursor_excludes_later_edits_and_rejects_scope_swap(client, local_runtime):
    material = upload(client, scan_pdf(2)); _, result = start(client, material)
    root, units = finish_jobs(client, material, result)
    d = detail(client, material, root['run']['id'], units[0]['id'])
    d = client.put(prefix(material) + '/units/' + units[0]['id'], json=save_body(d)).json()['unit']
    url = prefix(material) + '/units/' + units[0]['id'] + '/versions'
    page = client.get(url, params={'run_id': root['run']['id'], 'limit': 1}).json()
    d = client.put(prefix(material) + '/units/' + units[0]['id'], json=save_body(d, '后来人工稿')).json()['unit']
    rest = client.get(url, params={'run_id': root['run']['id'], 'after': page['next_cursor']})
    assert rest.status_code == 200 and rest.json()['total'] == 2
    assert [r['number'] for r in rest.json()['items']] == [2]
    other = prefix(material) + '/units/' + units[1]['id'] + '/versions'
    assert client.get(other, params={'run_id': root['run']['id'], 'after': page['next_cursor']}).status_code == 409


@pytest.mark.parametrize('damage', ['raw_text', 'unit_image_sha', 'run_epoch', 'missing_version'])
def test_frozen_partial_tamper_rejected_before_human_work(client, local_runtime, damage):
    from generic_agents.models import MaterialOcrVersion, MaterialOcrRun, MaterialOcrUnit
    material = upload(client, scan_pdf(1)); _, result = start(client, material)
    root, units = finish_jobs(client, material, result)
    with Session(engine()) as db:
        unit = db.get(MaterialOcrUnit, units[0]['id'])
        if damage == 'raw_text':
            raw = db.get(MaterialOcrVersion, unit.data['raw_id']); raw.data = {**raw.data, 'plain_text': '篡改日期和单位'}
        elif damage == 'unit_image_sha':unit.data = {**unit.data, 'image_sha256': '0'*64}
        elif damage == 'run_epoch':
            run = db.get(MaterialOcrRun, root['run']['id']); run.data = {**run.data, 'epoch': 99}
        else:db.delete(db.get(MaterialOcrVersion, unit.data['raw_id']))
        db.commit()
    assert client.get(prefix(material) + '/units/' + units[0]['id'], params={'run_id': root['run']['id']}).status_code == 409


def test_actual_cancellation_guard_stops_inflight_result_without_publish(client, local_runtime, monkeypatch):
    from generic_agents import material_ocr_runtime
    material = upload(client, scan_pdf(1)); _, result = start(client, material)
    assert process_once(result['view']['run']['job_id'])
    with Session(engine()) as db: job_id = db.scalar(select(Job.id).where(Job.kind == 'recognize_material_ocr_unit'))
    original = material_ocr_runtime.recognize
    def cancel_inflight(value, expected_manifest_hash, guard):
        root = client.get(prefix(material)).json()
        response = client.post(prefix(material) + '/cancel', json={'request_id': str(uuid4()),
            'expected_material_version': root['material']['version'], 'run_id': root['run']['id'],
            'expected_run_version': root['run']['version'], 'reason': '识别进行中实际取消'})
        assert response.status_code == 200
        guard()
        pytest.fail('取消后guard必须拒绝继续运行')
    monkeypatch.setattr(material_ocr_runtime, 'recognize', cancel_inflight)
    assert process_once(job_id)
    root = client.get(prefix(material)).json()
    assert root['run']['status'] == 'cancelled'
    unit = client.get(prefix(material) + '/units', params={'run_id': root['run']['id']}).json()['items'][0]
    assert unit['raw_revision'] is None and local_runtime['calls'] == 0


def test_retry_unfinished_pages_keeps_completed_page_and_current_manual_draft(client, local_runtime):
    material = upload(client); _, result = start(client, material)
    assert process_once(result['view']['run']['job_id'])
    with Session(engine()) as db:
        jobs = list(db.scalars(select(Job.id).where(Job.kind == 'recognize_material_ocr_unit').order_by(Job.data['unit_id'].astext)))
    assert process_once(jobs[0])
    root = client.get(prefix(material)).json(); listed = client.get(prefix(material) + '/units', params={'run_id': root['run']['id']}).json()['items']
    finished = next(u for u in listed if u['raw_revision']); d = detail(client, material, root['run']['id'], finished['id'])
    saved = client.put(prefix(material) + '/units/' + finished['id'], json=save_body(d, '已保存的人工劳动')).json()['unit']
    # 第一页人工稿不应让仍未完成的另一页失去后台状态。
    with Session(engine()) as db:
        from generic_agents.models import MaterialOcrRun
        from generic_agents.material_ocr import seal
        run = db.get(MaterialOcrRun, root['run']['id']); run.status = 'failed'; run.version += 1
        run.data = {**run.data, 'error': '显式测试未完成页恢复'}; seal(run); db.commit()
    root = client.get(prefix(material)).json()
    command = {'request_id': str(uuid4()), 'expected_material_version': root['material']['version'],
               'run_id': root['run']['id'], 'expected_run_version': root['run']['version'], 'reason': '恢复未完成页'}
    retry = client.post(prefix(material) + '/retry', json=command)
    assert retry.status_code == 200, retry.text
    assert process_once(retry.json()['view']['run']['job_id'])
    with Session(engine()) as db:
        new_jobs = list(db.scalars(select(Job.id).where(Job.kind == 'recognize_material_ocr_unit', Job.status == 'queued')))
    for job_id in new_jobs: process_once(job_id)
    current = detail(client, material, root['run']['id'], finished['id'])
    assert current['draft']['hash'] == saved['draft']['hash'] and current['draft']['plain_text'] == '已保存的人工劳动'
    assert local_runtime['calls'] == 2


@pytest.mark.parametrize('kind',['embedded','scan','text'])
def test_legacy_new_v2_preview_rechecks_original_without_backfill_and_has_real_start(client, local_runtime, kind):
    import json
    from pathlib import Path
    from tests.test_vertical_slice import publish
    content,name=(image_docx(),'旧内嵌图.docx') if kind=='embedded' else (scan_pdf(1),'旧扫描.pdf') if kind=='scan' else ('纯正文完整。'.encode(),'旧文本.txt')
    material=upload(client,content,name)
    with Session(engine()) as db:
        row=db.get(SetupMaterial,material['id']);row.data={k:v for k,v in row.data.items() if not k.startswith('ocr_')}
        row.status='parsed';db.commit();saved_data=row.data.copy();saved_version=row.version
    cap=publish(client,json.loads(Path('examples/qwen.json').read_text()))
    draft=client.get('/api/setup/draft').json()
    from tests.test_setup import config
    response=client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':config()})
    body={'revision':response.json()['revision'],'model_id':cap['id'],'material_ids':[material['id']],
          'instruction':'','contract_version':2,'request_id':str(uuid4())}
    preview=client.post('/api/setup/understanding/preview',json=body)
    assert preview.status_code==(200 if kind=='text' else 409),preview.text
    with Session(engine()) as db:
        row=db.get(SetupMaterial,material['id']);assert row.version==saved_version and row.data==saved_data
    root=client.get(prefix(material)).json()
    if kind!='text':
        assert root['permissions']['start']['allowed'] is True
        material=client.get('/api/setup/materials/'+material['id']).json()
        _,started=start(client,material)
        assert started['view']['run']['status']=='queued'
    else:assert root['coverage']['status']=='complete' and root['run'] is None


@pytest.mark.parametrize('with_uuid',[False,True])
def test_paid_legacy_nonce_replay_uses_original_body_not_new_coverage(client, monkeypatch, with_uuid):
    from tests.test_setup_understanding import prepared, start as understanding_start, process
    from tests.test_setup_objects_runtime import TEXT, fake_v2
    from generic_agents.models import SetupAnalysis
    # The in-process fake reserves one local ledger entry; it performs no HTTP.
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 1)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000)
    body,material=prepared(client,TEXT);body['contract_version']=2
    if with_uuid:body['request_id']=str(uuid4())
    calls=fake_v2(monkeypatch);row,preview=understanding_start(client,body);process(row)
    command={**body,'preview_hash':preview['preview_hash'],'consent':True}
    with Session(engine()) as db:
        analysis=db.get(SetupAnalysis,row['id']);assert analysis.data.get('raw_responses') or analysis.data.get('receipts')
        analysis.data={k:v for k,v in analysis.data.items() if k!='start_command_hash'}
        m=db.get(SetupMaterial,material['id']);m.data={**m.data,'ocr_coverage':{'contract_version':1,'status':'pending','unit_count':0,'gaps':[],'reason':None}}
        db.commit()
    response=client.post('/api/setup/understanding',json=command)
    assert response.status_code==202 and response.json()['id']==row['id'],response.text
    assert len(calls)==1
    changed=client.post('/api/setup/understanding',json={**command,'instruction':'替换发送目的'})
    assert changed.status_code==409


def test_paid_nonce_other_author_and_current_role_revocation_cannot_replay(client, monkeypatch, account):
    from tests.test_setup_understanding import prepared, start as understanding_start, process
    from tests.test_setup_objects_runtime import TEXT, fake_v2
    body,material=prepared(client,TEXT);body.update(contract_version=2,request_id=str(uuid4()))
    calls=fake_v2(monkeypatch);row,preview=understanding_start(client,body);process(row)
    command={**body,'preview_hash':preview['preview_hash'],'consent':True}
    username,password='operator_'+uuid4().hex,uuid4().hex
    with Session(engine()) as db:
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=account[2],role='operator'));db.commit()
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':password})
        other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert other.get('/api/setup/understanding/'+row['id']).status_code==200
        assert other.post('/api/setup/understanding',json=command).status_code==403
    with Session(engine()) as db:
        owner=db.scalar(select(User).where(User.username==account[0]))
        member=db.scalar(select(Membership).where(Membership.user_id==owner.id,Membership.workspace_id==account[2]))
        member.role='viewer';db.commit()
    assert client.post('/api/setup/understanding',json=command).status_code==403
    assert len(calls)==1


def test_committed_page_then_job_tail_crash_replays_without_another_inference(client,local_runtime):
    from datetime import timedelta
    from generic_agents.models import now
    material=upload(client,scan_pdf(1));_,result=start(client,material)
    root,units=finish_jobs(client,material,result)
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.kind=='recognize_material_ocr_unit'))
        job.status='running';job.lease_token=str(uuid4());job.lease_until=now()-timedelta(seconds=1);job.attempts=3
        db.commit();job_id=job.id
    assert process_once(job_id)
    with Session(engine()) as db:assert db.get(Job,job_id).status=='succeeded'
    restored=detail(client,material,root['run']['id'],units[0]['id'])
    assert restored['raw']['hash']==units[0]['raw_revision']['hash'] and local_runtime['calls']==1


@pytest.mark.parametrize('dimension',[2,3])
def test_zero_area_edit_rejected_without_consuming_version_or_nonce(client,local_runtime,dimension):
    material=upload(client,scan_pdf(1));_,result=start(client,material)
    root,units=finish_jobs(client,material,result);d=detail(client,material,root['run']['id'],units[0]['id'])
    command=save_body(d);command['regions'][0]['box'][dimension]=0
    response=client.put(prefix(material)+'/units/'+units[0]['id'],json=command)
    assert response.status_code==409
    restored=detail(client,material,root['run']['id'],units[0]['id'])
    assert restored['unit']['version']==d['unit']['version'] and restored['draft'] is None
    assert client.get(prefix(material)+'/requests/'+command['request_id']).json()['state']=='not_found'


def test_native_reparse_fences_old_queued_ocr_and_keeps_saved_page(client,local_runtime):
    material=upload(client,scan_pdf(2));_,result=start(client,material)
    assert process_once(result['view']['run']['job_id'])
    prepare_remaining_test_pages(client, material, result)
    with Session(engine()) as db:
        jobs=list(db.scalars(select(Job).where(Job.kind=='recognize_material_ocr_unit').order_by(Job.data['unit_id'].as_string())))
        first_id,late_id=jobs[0].id,jobs[1].id
        first_unit=jobs[0].data['unit_id']
    assert process_once(first_id)
    d=detail(client,material,result['view']['run']['id'],first_unit)
    saved=client.put(prefix(material)+'/units/'+first_unit,json=save_body(d,'人工逐图核对保留的旧稿'))
    assert saved.status_code==200
    current=client.get('/api/setup/materials/'+material['id']).json()
    retried=client.post('/api/setup/materials/'+material['id']+'/retry',json={'version':current['version']})
    assert retried.status_code==200
    assert process_once(late_id) and local_runtime['calls']==1
    old=detail(client,material,result['view']['run']['id'],first_unit)
    assert old['draft']['hash']==saved.json()['unit']['draft']['hash']
    assert old['draft']['plain_text']=='人工逐图核对保留的旧稿' and not old['permissions']['save']['allowed']
    assert process_once(retried.json()['job_id'])
    assert client.get(prefix(material)).json()['run'] is None
    parsed=client.get('/api/setup/materials/'+material['id']).json()
    _,fresh=start(client,parsed)
    assert fresh['view']['run']['id']!=result['view']['run']['id'] and fresh['view']['run']['generation']==2


def test_native_reparse_preserves_frozen_confirmation_citation_and_original(client,local_runtime):
    material=upload(client,scan_pdf(1));_,result=start(client,material)
    root,units=finish_jobs(client,material,result)
    d=confirm_page(client,material,detail(client,material,root['run']['id'],units[0]['id']))
    confirm_document(client,material)
    old=client.get('/api/setup/materials/'+material['id']).json()['blocks'][0]
    d=detail(client,material,root['run']['id'],units[0]['id'])
    saved=client.put(prefix(material)+'/units/'+units[0]['id'],json=save_body(d,'再次修改，尚未重新确认'))
    assert saved.status_code==200
    current=client.get('/api/setup/materials/'+material['id']).json()
    retried=client.post('/api/setup/materials/'+material['id']+'/retry',json={'version':current['version']})
    assert retried.status_code==200 and process_once(retried.json()['job_id'])
    historical=client.get(prefix(material)+'/citations/'+old['id'],params={'quote':'单位未说明'})
    assert historical.status_code==200 and historical.json()['block']==old
    locator=old['location']['ocr']
    image=client.get(prefix(material)+'/confirmations/'+locator['confirmation_id']+'/units/'+locator['unit_id']+'/image',params={'image_sha256':locator['image_sha256']})
    assert image.status_code==200 and hashlib.sha256(image.content).hexdigest()==locator['image_sha256']
