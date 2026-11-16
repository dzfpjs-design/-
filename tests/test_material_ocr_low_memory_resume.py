"""Actual handlers over explicit synthetic IO; not OCR/model or PostgreSQL tests.

The root runs these in the isolated pure gate. The implementation author does
not execute this file or a runtime during this slice.
"""
import copy
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import material_ocr as o
from generic_agents import material_ocr_recognition as rec
from generic_agents import material_ocr_runtime as runtime
from generic_agents.contracts import digest
from generic_agents.leases import LeaseLost

_spec = importlib.util.spec_from_file_location('low_memory_synthetic_io', Path(__file__).with_name('test_material_ocr_incremental.py'))
_io = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_io)
scene = _io.scene
jobs, units, start_prepare, command, body = _io.jobs, _io.units, _io.start_prepare, _io.command, _io.body
REAL_GUARD = o.runtime_guard


def row_bytes(row):
    return copy.deepcopy({c.name:getattr(row,c.name) for c in row.__table__.columns})


@pytest.fixture
def low(scene, monkeypatch):
    s = scene
    manifest = {'id':runtime.LOW_MEMORY_ENGINE_ID, 'manifest_hash':runtime.LOW_MEMORY_MANIFEST_HASH,
                'synthetic':'no runtime/model execution'}
    s.low = {'id':runtime.LOW_MEMORY_ENGINE_ID, 'name':'合成低内存引擎', 'ready':True, 'reason':None,
             'manifest_hash':runtime.LOW_MEMORY_MANIFEST_HASH, 'manifest':manifest, 'summary_hash':digest(manifest)}
    monkeypatch.setattr(o, 'low_memory_engine_status', lambda:copy.deepcopy(s.low))
    s.state['recognition_hook'] = None
    def recognize(png, *, expected_manifest_hash, guard):
        guard(); s.state['recognitions'] += 1
        if s.state['recognition_hook']: s.state['recognition_hook']()
        guard()  # The real adapter fences a finished child before returning its result.
        engine = s.low if expected_manifest_hash == s.low['manifest_hash'] else s.installed
        regions = [{'id':'r1','text':'合成原字','box':[0,0,1,1],'score':0.5}]
        return {'regions':regions,'elapsed_ms':1,'receipt':{'input_sha256':hashlib.sha256(png).hexdigest(),
            'manifest_hash':engine['manifest_hash'],'manifest':engine['manifest'],
            'summary_hash':engine['summary_hash'],'result_sha256':digest(regions)}}
    monkeypatch.setattr(runtime, 'recognize', recognize)
    return s


def partial(s):
    first = start_prepare(s); o.process_prepare(s.db,s.p,first)
    o.process_unit(s.db,s.p,jobs(s,'recognize_material_ocr_unit')[0])
    raw = next(v for v in s.db.rows if isinstance(v,o.Version))
    page = units(s)[0]
    saved = command(s,expected_unit_version=page.version,parent_hash=raw.content_hash,reason='原人工稿末字',
        regions=[{'id':'r1','text':'人工末字','box':[0,0,1,1],'source_region_id':'r1'}])
    o.execute(s.db,s.p,s.m.id,'save',body(saved),page.id)
    edited = next(v for v in s.db.rows if isinstance(v,o.Version) and v.kind=='edited')
    o.execute(s.db,s.p,s.m.id,'unit_confirm',body(command(s,expected_unit_version=page.version,
        revision_hash=edited.content_hash,reason='原本页确认说明')),page.id)
    second = jobs(s,'prepare_material_ocr')[-1]; o.process_prepare(s.db,s.p,second)
    s.run.status='failed';s.run.data={**s.run.data,'error':'synthetic memory limit'};o.seal(s.run)
    return page, units(s)[1], second


def resume_body(s, **extra):
    return command(s,expected_run_version=s.run.version,reason='明确以较少内存继续，保存页不重跑',
                   engine_id=s.low['id'],engine_manifest_hash=s.low['manifest_hash'],**extra)


def accept(s):
    value = resume_body(s)
    reply = o.execute(s.db,s.p,s.m.id,'resume_with_profile',body(value))
    return value, reply


def assert_unchanged(rows):
    for row, before in rows: assert row_bytes(row)==before


def test_explicit_resume_preserves_whole_raw_manual_confirmed_and_old_jobs(low):
    s=low; kept, rawless, _=partial(s)
    protected=[(r,row_bytes(r)) for r in s.db.rows if isinstance(r,(o.Version,o.Job)) or r is kept]
    renderer=copy.deepcopy(s.run.data['engine']);coverage=copy.deepcopy(s.run.data['coverage']);prepared=s.run.data['prepared']
    old_epoch=s.run.data['epoch'];old_run_version=s.run.version;old_material_version=s.m.version
    value,reply=accept(s)
    assert_unchanged(protected)
    assert s.run.data['engine']==renderer and s.run.data['coverage']==coverage and s.run.data['prepared']==prepared
    assert s.run.data['epoch']==old_epoch+1 and s.run.version==old_run_version+1 and s.m.version==old_material_version+1
    assert s.run.status=='queued' and rawless.status=='queued'
    receipt=next(r for r in s.db.rows if isinstance(r,o.Request) and r.request_id==value['request_id'])
    auth=receipt.data['result']['recognition_authorization']
    assert auth['prepared_at']==prepared and auth['rawless_unit_ids']==[rawless.id]
    assert rec.authorization(s.db,s.run,auth['authorization_hash'])==auth
    assert rawless.data['recognition_engine']==s.low and 'recognition_engine' not in kept.data
    newjob=jobs(s,'recognize_material_ocr_unit')[-1]
    assert newjob.data['target']['engine_manifest_hash']==renderer['manifest_hash']
    assert newjob.data['target']['recognition_engine_manifest_hash']==s.low['manifest_hash']
    assert newjob.available_at<jobs(s,'prepare_material_ocr')[-1].available_at


def test_same_nonce_receipt_precedes_fresh_cas_and_uninstalled_profile(low):
    s=low;partial(s);value,reply=accept(s)
    before=[(r,row_bytes(r)) for r in s.db.rows]
    s.low['ready']=False
    assert o.execute(s.db,s.p,s.m.id,'resume_with_profile',body(value))['request']==reply['request']
    assert_unchanged(before)
    for key,replacement in [('reason','different'),('engine_manifest_hash','0'*64),('expected_run_version',999)]:
        changed={**value,key:replacement}
        with pytest.raises(HTTPException):o.execute(s.db,s.p,s.m.id,'resume_with_profile',body(changed))
    assert_unchanged(before)


@pytest.mark.parametrize('damage',['viewer','revoked','archive','superseded','source','run_cas','material_cas','ready','id','hash','reason_nul'])
def test_new_acceptance_retains_authority_source_cas_and_fixed_profile_guards(low,damage):
    s=low;partial(s);value=resume_body(s)
    if damage=='viewer':s.p.role='viewer'
    elif damage=='revoked':s.state['authorized']=False
    elif damage=='archive':s.m.archived=True
    elif damage=='superseded':s.m.data={**s.m.data,'ocr_run_id':str(uuid4())}
    elif damage=='source':s.m.sha256='0'*64
    elif damage=='run_cas':value['expected_run_version']+=1
    elif damage=='material_cas':value['expected_material_version']+=1
    elif damage=='ready':s.low['ready']=False
    elif damage=='id':value['engine_id']='unregistered'
    elif damage=='hash':value['engine_manifest_hash']='0'*64
    elif damage=='reason_nul':value['reason']='bad\x00reason'
    before=[(r,row_bytes(r)) for r in s.db.rows]
    with pytest.raises(HTTPException):o.execute(s.db,s.p,s.m.id,'resume_with_profile',body(value))
    assert_unchanged(before)


@pytest.mark.parametrize('terminal',['failed','blocked','cancelled'])
def test_three_terminal_states_are_explicitly_supported(low,terminal):
    s=low;partial(s);s.run.status=terminal;o.seal(s.run);accept(s)
    assert s.run.status=='queued'


def test_low_profile_raw_and_manual_versions_bind_new_engine_not_renderer(low):
    s=low;kept,page,_=partial(s);accept(s)
    job=jobs(s,'recognize_material_ocr_unit')[-1];o.process_unit(s.db,s.p,job)
    rows=o.checked_versions(s.db,s.p,s.run,page)
    assert rows[0].data['engine_manifest_hash']==s.low['manifest_hash']
    assert rows[0].data['recognition_contract']==rec.CONTRACT
    assert rows[0].data['receipt']['manifest_hash']==s.low['manifest_hash']
    assert page.data['render']['receipt']['manifest_hash']==s.installed['manifest_hash']
    old_engine=rec.effective_engine(s.db,s.run,kept)
    s.low['ready']=False  # Current installation availability is not saved-page integrity.
    detail=o.unit_detail(s.db,s.p,s.m,s.run,page)
    assert detail['permissions']['save']['allowed'] and detail['recognition_engine']['manifest_hash']==runtime.LOW_MEMORY_MANIFEST_HASH
    assert rec.effective_engine(s.db,s.run,kept)==old_engine==s.run.data['engine']
    raw=rows[0]
    save=command(s,expected_unit_version=page.version,parent_hash=raw.content_hash,reason='低内存页人工说明',
        regions=[{'id':'r1','text':'已核末字','box':[0,0,1,1],'source_region_id':'r1'}])
    o.execute(s.db,s.p,s.m.id,'save',body(save),page.id)
    assert all(v.data['engine_manifest_hash']==runtime.LOW_MEMORY_MANIFEST_HASH for v in o.checked_versions(s.db,s.p,s.run,page))


def test_future_new_page_inherits_fixed_choice_but_old_targets_and_raw_do_not(low):
    s=low;kept,page,oldprepare=partial(s)
    old_target=copy.deepcopy(oldprepare.data['target']);oldhash=oldprepare.data['target_hash'];accept(s)
    assert o._job_scope(s.db,s.p,oldprepare)[2] is False
    assert oldprepare.data['target']==old_target and oldprepare.data['target_hash']==oldhash
    prepare=jobs(s,'prepare_material_ocr')[-1]
    assert 'recognition_contract' not in prepare.data['target']
    o.process_prepare(s.db,s.p,prepare)
    newpage=units(s)[2]
    assert newpage.data['recognition_engine']==s.low and 'recognition_engine' not in kept.data
    assert rec.effective_engine(s.db,s.run,newpage)==s.low
    assert rec.effective_engine(s.db,s.run,kept)==s.run.data['engine']


@pytest.mark.parametrize('damage',['none','partial','unknown_profile','summary','auth','old_raw_rebind','receipt_removed'])
def test_present_override_corruption_never_falls_back_to_live_future(low,damage):
    s=low;old,page,_=partial(s);accept(s)
    if damage=='none':page.data={**page.data,'recognition_engine':None}
    elif damage=='partial':page.data={k:v for k,v in page.data.items() if k!='recognition_authorization'}
    elif damage=='unknown_profile':page.data['recognition_engine']={**page.data['recognition_engine'],'id':'unknown'}
    elif damage=='summary':page.data['recognition_engine']={**page.data['recognition_engine'],'summary_hash':'0'*64}
    elif damage=='auth':page.data['recognition_authorization']='0'*64
    elif damage=='old_raw_rebind':
        old.data={**old.data,'recognition_engine':copy.deepcopy(page.data['recognition_engine']),
                  'recognition_authorization':page.data['recognition_authorization']};page=old
    else:
        s.db.rows=[r for r in s.db.rows if not(isinstance(r,o.Request) and r.action=='resume_with_profile')]
    with pytest.raises(HTTPException):rec.effective_engine(s.db,s.run,page)


def test_old_retry_reuses_original_choice_and_old_auth_without_new_profile(low):
    s=low;kept,page,_=partial(s);accept(s)
    auth=page.data['recognition_authorization'];engine=copy.deepcopy(page.data['recognition_engine']);previous=jobs(s,'recognize_material_ocr_unit')[-1]
    s.run.status='failed';o.seal(s.run)
    o.execute(s.db,s.p,s.m.id,'retry',body(command(s,expected_run_version=s.run.version,reason='同原选择恢复')))
    assert page.data['recognition_engine']==engine and page.data['recognition_authorization']==auth
    assert len(s.run.data['recognition_authorizations'])==1
    assert o._job_scope(s.db,s.p,previous)[2] is False
    assert jobs(s,'recognize_material_ocr_unit')[-1].data['target']['epoch']>previous.data['target']['epoch']


@pytest.mark.parametrize('event',['cancel','actor','lease'])
def test_after_recognition_await_scope_and_lease_changes_do_not_save_raw(low,event):
    s=low;_,page,_=partial(s);accept(s);job=jobs(s,'recognize_material_ocr_unit')[-1]
    def change():
        if event=='cancel':s.run.status='cancelled';s.run.data={**s.run.data,'epoch':s.run.data['epoch']+1};o.seal(s.run)
        elif event=='actor':s.state['authorized']=False
        else:s.db.lease=False
    s.state['recognition_hook']=change
    if event=='cancel':o.process_unit(s.db,s.p,job)
    else:
        with pytest.raises((HTTPException,LeaseLost)):o.process_unit(s.db,s.p,job)
    assert not page.data.get('raw_id')


def test_actual_runtime_guard_rechecks_profile_job_scope_and_lease(low,monkeypatch):
    from generic_agents import leases
    s=low;_,page,_=partial(s);accept(s);job=jobs(s,'recognize_material_ocr_unit')[-1]
    monkeypatch.setattr(s.db,'get_bind',lambda:None,raising=False)
    monkeypatch.setattr(leases,'ownership',lambda db:{'lease_token':'synthetic'})
    class Other:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,stmt):
            text=str(stmt)
            first=(SimpleNamespace(active=s.state['authorized'],role=s.p.role) if 'users.active' in text
                   else (s.run.status,s.run.data,s.m.archived,s.m.data))
            return SimpleNamespace(first=lambda:first)
        def scalar(self,stmt):
            entity=stmt.column_descriptions[0]['entity']
            if entity is o.Run:return s.run
            if entity is o.Unit:return page
            if entity is o.Job:return job.id if s.db.lease else None
            return s.db.scalar(stmt)
    monkeypatch.setattr(o,'Session',Other)
    REAL_GUARD(s.db,s.p,job)()
    s.db.lease=False
    with pytest.raises(LeaseLost):REAL_GUARD(s.db,s.p,job)()
    s.db.lease=True;page.data={**page.data,'recognition_authorization':'0'*64};o.seal(page)
    with pytest.raises(HTTPException):REAL_GUARD(s.db,s.p,job)()


def test_typed_target_rehash_does_not_allow_extra_or_renderer_swap(low):
    s=low;_,page,_=partial(s);accept(s);job=jobs(s,'recognize_material_ocr_unit')[-1]
    frozen=copy.deepcopy(job.data)
    for field,value in [('engine_manifest_hash',s.low['manifest_hash']),('recognition_engine_manifest_hash',s.installed['manifest_hash']),('recognition_contract','unknown'),('unexpected',True)]:
        target={**frozen['target'],field:value}
        job.data={**frozen,'target':target,'target_hash':digest(target)}
        with pytest.raises(HTTPException):o.process_unit(s.db,s.p,job)
    assert not page.data.get('raw_id')


def test_legacy_absent_override_target_and_versions_remain_original(low):
    s=low;first=start_prepare(s);o.process_prepare(s.db,s.p,first);page=units(s)[0]
    target=o._job_target(s.p,s.m,s.run,page)
    assert set(target)==rec.BASE_TARGET_KEYS and rec.effective_engine(s.db,s.run,page)==s.run.data['engine']
    o.process_unit(s.db,s.p,jobs(s,'recognize_material_ocr_unit')[0])
    raw=o.checked_versions(s.db,s.p,s.run,page)[0]
    assert 'recognition_contract' not in raw.data and 'recognition_authorization' not in raw.data
