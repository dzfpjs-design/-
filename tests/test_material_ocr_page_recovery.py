"""Terminal local OCR does not strand verified human page work.

Actual execute/process_unit and hashes run over explicit in-memory IO adapters;
no DB/service/child process or actual recognition is invoked.
"""
import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import material_ocr as o
from generic_agents.contracts import digest
from generic_agents.leases import LeaseLost
from generic_agents.workflow_abilities import WorkflowBlocked
# Reuse explicit IO adapters without relying on pytest's import mode or cwd.
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('ocr_incremental_pure_adapter',Path(__file__).with_name('test_material_ocr_incremental.py'))
_adapter=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_adapter)
scene,start_prepare,units,jobs,command,body=(_adapter.scene,_adapter.start_prepare,_adapter.units,_adapter.jobs,_adapter.command,_adapter.body)

REAL_UNIT = o.checked_unit
REAL_SOURCE = o.source

MEMORY = '本地文字识别暂不可用：本地处理内存超过上限，可拆分页面后重试'
TIMEOUT = '本地文字识别暂不可用：处理超时，可稍后重试或拆分页面'


def ready(s, monkeypatch, terminal='failed'):
    first=start_prepare(s);o.process_prepare(s.db,s.p,first)
    raw_job=jobs(s,'recognize_material_ocr_unit')[0];o.process_unit(s.db,s.p,raw_job)
    unit=units(s)[0];raw=next(x for x in s.db.rows if isinstance(x,o.Version))
    # Use original source/image/region/version guards, not the prior scene's
    # checked_unit shortcut. The evidence adapter returns the exact synthetic PNG.
    _,png=o.evidence_bytes(s.db,s.p,unit.evidence_id)
    evidence=SimpleNamespace(media_type='image/png',sha256=unit.data['image_sha256'])
    monkeypatch.setattr(o,'evidence_bytes',lambda *args:(evidence,png))
    monkeypatch.setattr(o,'checked_unit',REAL_UNIT)
    s.run.data={**s.run.data,'coverage':{**s.run.data['coverage'],'source_sha256':s.m.sha256},
                'error':MEMORY,'epoch':2}
    s.m.data={**s.m.data,'ocr_coverage':s.run.data['coverage']}
    monkeypatch.setattr(o,'source',REAL_SOURCE)
    monkeypatch.setattr(o,'material_coverage',lambda *args:copy.deepcopy(s.run.data['coverage']))
    monkeypatch.setattr(o,'parse_document',lambda *args:{'blocks':copy.deepcopy(s.run.data['native_blocks'])})
    s.run.status=terminal;s.run.version+=1;o.seal(s.run)
    return unit,raw


def save_command(s,unit,raw):
    return command(s,expected_unit_version=unit.version,parent_hash=raw.content_hash,reason='对照原图保留末字',
        regions=[{'id':'r1','text':'本地原字已改正🙂','box':[0,0,1,1],'source_region_id':'r1'}])


def terminal_binding(s):
    return {k:copy.deepcopy(s.run.data.get(k)) for k in ['error','epoch','job_id','prepared','prepared_job_id','engine','coverage','scope']}


@pytest.mark.parametrize('terminal',['failed','blocked','cancelled'])
def test_terminal_raw_page_actual_save_confirm_restore_replay_keeps_terminal_and_jobs(scene,monkeypatch,terminal):
    s=scene;unit,raw=ready(s,monkeypatch,terminal)
    fixed=terminal_binding(s); version=s.run.version; unit_version=unit.version
    raw_bytes=copy.deepcopy((raw.id,raw.content_hash,raw.data));dispatches=copy.deepcopy([(x.id,x.data,x.status) for x in s.db.rows if isinstance(x,o.Job)])
    material=copy.deepcopy((s.m.version,s.m.status,s.m.data))
    save=save_command(s,unit,raw)
    reply=o.execute(s.db,s.p,s.m.id,'save',body(save),unit.id)
    assert s.run.status==terminal and terminal_binding(s)==fixed
    edited=next(x for x in s.db.rows if isinstance(x,o.Version) and x.kind=='edited')
    confirm=command(s,expected_unit_version=unit.version,revision_hash=edited.content_hash,reason='仅本页核对')
    o.execute(s.db,s.p,s.m.id,'unit_confirm',body(confirm),unit.id)
    assert unit.status=='confirmed' and s.run.status==terminal and terminal_binding(s)==fixed
    restore=command(s,expected_unit_version=unit.version,revision_id=raw.id,revision_hash=raw.content_hash,reason='显式恢复原识别')
    o.execute(s.db,s.p,s.m.id,'restore',body(restore),unit.id)
    assert unit.status=='review' and s.run.status==terminal and terminal_binding(s)==fixed
    assert s.run.version==version+3 and unit.version==unit_version+3
    before=copy.deepcopy([(x.id,getattr(x,'version',None),x.data) for x in s.db.rows])
    replay=o.execute(s.db,s.p,s.m.id,'save',body(save),unit.id)
    assert replay['request']==reply['request'] and before==[(x.id,getattr(x,'version',None),x.data) for x in s.db.rows]
    assert raw_bytes==(raw.id,raw.content_hash,raw.data)
    assert dispatches==[(x.id,x.data,x.status) for x in s.db.rows if isinstance(x,o.Job)]
    assert material==(s.m.version,s.m.status,s.m.data)
    assert s.state['recognitions']==1 and s.state['renders']==[1]
    assert len(o.checked_versions(s.db,s.p,s.run,unit))==4


@pytest.mark.parametrize('damage',['archive','superseded','viewer','revoked','rawhash','imagehash','imagebytes','source','evidence','parenthash'])
def test_terminal_human_write_retains_existing_authority_integrity_and_cas_guards(scene,monkeypatch,damage):
    s=scene;unit,raw=ready(s,monkeypatch);save=save_command(s,unit,raw)
    if damage=='archive':s.m.archived=True
    elif damage=='superseded':s.m.data={**s.m.data,'ocr_run_id':str(uuid4())}
    elif damage=='viewer':s.p.role='viewer'
    elif damage=='revoked':s.state['authorized']=False
    elif damage=='rawhash':raw.content_hash='0'*64
    elif damage=='imagehash':unit.data={**unit.data,'image_sha256':'0'*64};o.seal(unit)
    elif damage=='imagebytes':monkeypatch.setattr(o,'evidence_bytes',lambda *args:(SimpleNamespace(media_type='image/png',sha256=unit.data['image_sha256']),b'bad PNG'))
    elif damage=='source':s.m.sha256='0'*64
    elif damage=='evidence':s.m.evidence_id=str(uuid4())
    elif damage=='parenthash':save['parent_hash']='0'*64
    before=copy.deepcopy([(x.id,getattr(x,'version',None),x.data) for x in s.db.rows])
    with pytest.raises(HTTPException):o.execute(s.db,s.p,s.m.id,'save',body(save),unit.id)
    assert before==[(x.id,getattr(x,'version',None),x.data) for x in s.db.rows]
    assert s.run.status=='failed' and s.state['recognitions']==1


def test_failed_page_without_raw_is_not_editable_and_does_not_dispatch(scene):
    s=scene;o.process_prepare(s.db,s.p,start_prepare(s));unit=units(s)[0]
    s.run.status='failed';o.seal(s.run)
    with pytest.raises(HTTPException):
        o.execute(s.db,s.p,s.m.id,'save',body(command(s,expected_unit_version=unit.version,parent_hash='0'*64,reason='no original',regions=[])),unit.id)
    assert not any(isinstance(x,o.Version) for x in s.db.rows) and len(jobs(s,'recognize_material_ocr_unit'))==1


@pytest.mark.parametrize('terminal',['failed','blocked','cancelled'])
def test_terminal_run_never_document_confirms_even_all_units_prepared_and_confirmed(scene,monkeypatch,terminal):
    s=scene;unit,raw=ready(s,monkeypatch,terminal)
    # Explicit synthetic one-page full coverage still cannot promote a terminal run.
    s.run.data={**s.run.data,'coverage':{**s.run.data['coverage'],'unit_count':1,'units':s.run.data['coverage']['units'][:1],'gaps':[]}}
    o.seal(s.run)
    save=save_command(s,unit,raw);o.execute(s.db,s.p,s.m.id,'save',body(save),unit.id)
    edited=next(x for x in s.db.rows if isinstance(x,o.Version) and x.kind=='edited')
    o.execute(s.db,s.p,s.m.id,'unit_confirm',body(command(s,expected_unit_version=unit.version,revision_hash=edited.content_hash,reason='单页已核')),unit.id)
    assert unit.status=='confirmed' and s.run.data['prepared']==s.run.data['coverage']['unit_count']==1
    with pytest.raises(HTTPException):
        o.execute(s.db,s.p,s.m.id,'document_confirm',body(command(s,expected_run_version=s.run.version,units_manifest_hash='synthetic-manifest',reason='must stay stopped')))
    assert not any(isinstance(x,o.Confirmation) for x in s.db.rows) and s.run.status==terminal


@pytest.mark.parametrize('message',[MEMORY,TIMEOUT])
def test_actual_unit_resource_failure_is_safe_workflow_reason_without_raw_or_auto_retry(scene,monkeypatch,message):
    from generic_agents import material_ocr_runtime as runtime
    s=scene;o.process_prepare(s.db,s.p,start_prepare(s));job=jobs(s,'recognize_material_ocr_unit')[0]
    calls=[]
    def fail(*args,**kw):calls.append(1);raise HTTPException(409,message)
    monkeypatch.setattr(runtime,'recognize',fail)
    with pytest.raises(WorkflowBlocked) as error:o.process_unit(s.db,s.p,job)
    assert error.value.reason==message and calls==[1]
    assert not any(isinstance(x,o.Version) for x in s.db.rows)
    # Existing worker WorkflowBlocked handler may safely store only this reason.
    o.fail_job(s.db,s.p,job,error.value.reason)
    assert s.run.status=='failed' and s.run.data['error']==message
    assert units(s)[0].data['error']==message


@pytest.mark.parametrize('failure',[
    HTTPException(403,MEMORY),HTTPException(500,MEMORY),HTTPException(409,{'error':MEMORY}),
    HTTPException(409,MEMORY+' /private/path'),HTTPException(409,MEMORY.replace('：',':')),
    HTTPException(409,'unexpected /private/token'),LeaseLost('lease'),ValueError('private details')])
def test_nonallowlisted_errors_keep_original_exception_never_safe_reason(scene,monkeypatch,failure):
    from generic_agents import material_ocr_runtime as runtime
    s=scene;o.process_prepare(s.db,s.p,start_prepare(s));job=jobs(s,'recognize_material_ocr_unit')[0]
    def fail(*args,**kw):raise failure
    monkeypatch.setattr(runtime,'recognize',fail)
    with pytest.raises(type(failure)) as error:o.process_unit(s.db,s.p,job)
    assert error.value is failure and not isinstance(error.value,WorkflowBlocked)
    assert not any(isinstance(x,o.Version) for x in s.db.rows)


@pytest.mark.parametrize('terminal',['failed','blocked','cancelled'])
def test_actual_detail_terminal_verified_raw_is_editable_then_saved_draft_confirmable(scene,monkeypatch,terminal):
    s=scene;unit,raw=ready(s,monkeypatch,terminal)
    detail=o.unit_detail(s.db,s.p,s.m,s.run,unit)
    assert detail['permissions']['save']['allowed'] is True
    assert detail['permissions']['restore']['allowed'] is True
    assert detail['permissions']['confirm']['allowed'] is False
    o.execute(s.db,s.p,s.m.id,'save',body(save_command(s,unit,raw)),unit.id)
    detail=o.unit_detail(s.db,s.p,s.m,s.run,unit)
    assert all(value['allowed'] for value in detail['permissions'].values())
    assert s.run.status==terminal and detail['raw']['hash']==raw.content_hash


@pytest.mark.parametrize('denial',['rawNone','viewer','archive','superseded'])
def test_actual_detail_existing_authority_and_raw_required_even_terminal(scene,monkeypatch,denial):
    s=scene
    if denial=='rawNone':
        o.process_prepare(s.db,s.p,start_prepare(s));unit=units(s)[0]
        s.run.status='failed';o.seal(s.run)
    else:
        unit,raw=ready(s,monkeypatch)
        if denial=='viewer':s.p.role='viewer'
        elif denial=='archive':s.m.archived=True
        else:s.m.data={**s.m.data,'ocr_run_id':str(uuid4())}
    detail=o.unit_detail(s.db,s.p,s.m,s.run,unit)
    assert all(not value['allowed'] for value in detail['permissions'].values())
