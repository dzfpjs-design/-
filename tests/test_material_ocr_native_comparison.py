"""Read-only native PDF/OCR comparisons; synthetic PDFs and OCR only."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence, MaterialOcrRun, MaterialOcrUnit, MaterialOcrVersion, Membership, SetupMaterial, User, Workspace
from generic_agents.worker import process_once
from tests.test_material_ocr import (local_runtime, upload, inline_image_pdf, scan_pdf,
                                     image_docx, start, finish_jobs, prefix, detail, save_body)


def path(material, unit):
    return prefix(material)+'/units/'+unit['id']+'/native-comparison'


def compare(client, material, run, unit):
    return client.get(path(material,unit),params={'run_id':run})


def snapshot(material, unit):
    with Session(engine()) as db:
        row=db.get(SetupMaterial,material['id']); item=db.get(MaterialOcrUnit,unit['id'])
        return (row.version,item.version,db.scalar(select(func.count()).select_from(MaterialOcrVersion)),
                db.scalar(select(func.count()).select_from(Audit)),db.scalar(select(func.count()).select_from(CloudCall)))


def scene(client, source=None, name='扫描报告.pdf'):
    material=upload(client,source or inline_image_pdf(),name)
    _,request=start(client,material)
    root,units=finish_jobs(client,material,request)
    return material,root['run']['id'],units[0]


def test_raw_native_difference_is_read_only_and_not_replaced_by_human_edits(client,local_runtime):
    material,run,unit=scene(client)
    before=snapshot(material,unit)
    response=compare(client,material,run,unit)
    assert response.status_code==200
    assert response.headers['cache-control']=='private, no-store'
    result=response.json()
    assert result['schema']=='ocr-native-comparison-v1' and result['status']=='different'
    assert 'Native header only' in result['native_text'] and '日期2026' in result['ocr_text']
    assert result['scope']['page']==1 and result['scope']['raw_revision_id']==unit['raw_revision']['id']
    assert snapshot(material,unit)==before
    d=detail(client,material,run,unit['id'])
    saved=client.put(prefix(material)+'/units/'+unit['id'],json=save_body(d,'Native header only'))
    assert saved.status_code==200
    result2=compare(client,material,run,unit).json()
    assert result2['status']=='different' and result2['ocr_text']==result['ocr_text']
    assert result2['scope']['raw_revision_hash']==result['scope']['raw_revision_hash']


def test_missing_ocr_and_empty_native_are_explicit_not_agreement(client,local_runtime):
    material=upload(client,scan_pdf(1));_,request=start(client,material)
    assert process_once(request['view']['run']['job_id'])
    root=client.get(prefix(material)).json();run=root['run']['id']
    unit=client.get(prefix(material)+'/units',params={'run_id':run}).json()['items'][0]
    result=compare(client,material,run,unit).json()
    assert result['status']=='ocr_unavailable' and result['native_text'] is None and result['ocr_text'] is None
    with Session(engine()) as db: job_id=db.get(MaterialOcrUnit,unit['id']).data['job_id']
    assert process_once(job_id)
    result=compare(client,material,run,unit).json()
    assert result['status']=='native_unavailable' and result['native_text'] is None and result['ocr_text'] is None


def test_unsupported_word_image_is_not_compared_as_pdf_page(client,local_runtime):
    material,run,unit=scene(client,image_docx(),'资料.docx')
    result=compare(client,material,run,unit).json()
    assert result['status']=='unsupported' and result['scope']['page'] is None
    assert result['native_text'] is None and result['ocr_text'] is None


def test_cross_workspace_access_is_rejected(client,account,local_runtime):
    material,run,unit=scene(client)
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0])); other=Workspace(name='隔离工作区')
        db.add(other);db.flush();db.add(Membership(user_id=user.id,workspace_id=other.id,role='admin'));db.commit();other_id=other.id
    client.headers['X-Workspace-ID']=other_id
    try: assert compare(client,material,run,unit).status_code==404
    finally: client.headers['X-Workspace-ID']=account[2]


def test_older_run_remains_bound_to_its_original_raw_revision(client,local_runtime):
    material,run,unit=scene(client)
    old=compare(client,material,run,unit).json()
    current=client.get('/api/setup/materials/'+material['id']).json()
    retry=client.post('/api/setup/materials/'+material['id']+'/retry',json={'version':current['version']})
    assert retry.status_code==200 and process_once(retry.json()['job_id'])
    old_again=compare(client,material,run,unit)
    assert old_again.status_code==200
    assert old_again.json()['scope']['run_id']==run
    assert old_again.json()['scope']['raw_revision_hash']==old['scope']['raw_revision_hash']
    assert old_again.json()['ocr_text']==old['ocr_text']


def test_limit_fails_closed_without_partial_page_text(client,local_runtime,monkeypatch):
    from generic_agents import material_ocr_native_comparison as module
    material,run,unit=scene(client)
    monkeypatch.setattr(module,'MAX_PAGE_CHARS',5)
    result=compare(client,material,run,unit).json()
    assert result['status']=='too_large'
    assert result['native_text'] is None and result['ocr_text'] is None


def test_native_snapshot_tamper_and_source_tamper_fail_closed(client,local_runtime):
    material,run,unit=scene(client)
    with Session(engine()) as db:
        row=db.get(MaterialOcrRun,run); prior=row.data
        row.data={**prior,'native_blocks':[]};db.commit()
    assert compare(client,material,run,unit).status_code==409
    with Session(engine()) as db:
        row=db.get(MaterialOcrRun,run);row.data=prior;db.commit()
        item=db.get(SetupMaterial,material['id']);evidence=db.get(Evidence,item.evidence_id)
        from generic_agents.config import settings
        (settings().storage_dir/evidence.storage_key).write_bytes(b'tampered')
    assert compare(client,material,run,unit).status_code==409


def test_comparison_limits_and_material_characters_are_not_ignored():
    from generic_agents.material_ocr_native_comparison import compare_text
    assert compare_text('温度 12.5 ℃','温度\n12.5\t℃')=='no_difference'
    for value in ('温度 12.6 ℃','温度 -12.5 ℃','温度 12.5 °F','温度 12,5 ℃'):
        assert compare_text('温度 12.5 ℃',value)=='different'
    assert compare_text('', 'abc')=='native_unavailable'
    assert compare_text('abc', '')=='ocr_unavailable'
    assert compare_text('a'*20001,'a')=='too_large'
