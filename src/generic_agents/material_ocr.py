"""本地资料识别账本；原结果、人工版本与公开确认分别保存。"""
import base64
import copy
import hashlib
import io
import math
import re
import time
from functools import wraps
from urllib.parse import urlencode
from fastapi import HTTPException
from sqlalchemy import select, func, tuple_
from sqlalchemy.orm import Session
from PIL import Image
from .contracts import digest
from .models import (SetupMaterial, MaterialOcrRun as Run, MaterialOcrUnit as Unit,
                     MaterialOcrVersion as Version, MaterialOcrConfirmation as Confirmation,
                     MaterialOcrRequest as Request, Membership, User, Job, now, uid)
from .security import require
from .services import scoped, audit
from .evidence import evidence_bytes, save_immutable
from .document_parsers import parse_document, checked_zip
from .material_ocr_coverage import coverage, verify_coverage
from .document_copy import material_coverage, checked_provenance

CAS = '资料或识别版本已更新，本次请求未提交。请重新核对。'
BAD = '识别来源或版本完整性无法核对，原记录保留'
STATES = ('queued', 'running', 'review', 'confirmed', 'failed', 'blocked', 'cancelled')
MAX_VERSIONS = 100


def fail(detail=BAD): raise HTTPException(409, detail)


def integrity(function):
    @wraps(function)
    def checked(*args,**kwargs):
        try:return function(*args,**kwargs)
        except (KeyError,TypeError,ValueError,IndexError,AttributeError,OverflowError):fail()
    return checked


def engine_status():
    try:
        from .material_ocr_runtime import engine_status as status
        return status()
    except ImportError:
        return {'id': 'rapidocr-local-v1', 'name': '本地文字识别', 'ready': False,
                'reason': '本地识别运行环境尚未安装', 'manifest_hash': None, 'manifest': None}


def low_memory_engine_status():
    from . import material_ocr_runtime as runtime
    return runtime.engine_status(engine_hash=runtime.LOW_MEMORY_MANIFEST_HASH)


def bounded_engine_status():
    from . import material_ocr_runtime as runtime
    return runtime.engine_status(engine_hash=runtime.LOW_MEMORY_V2_MANIFEST_HASH)


def recognition_engine_status(engine_hash):
    """Resolve the saved/selected hash; never substitute the newest profile."""
    from . import material_ocr_runtime as runtime
    if engine_hash == runtime.LOW_MEMORY_MANIFEST_HASH:
        return low_memory_engine_status()
    if engine_hash == runtime.LOW_MEMORY_V2_MANIFEST_HASH:
        return bounded_engine_status()
    fail('所选识别引擎未登记，原识别授权和页面保留')


def permission(allowed, reason=None): return {'allowed': bool(allowed), 'reason': None if allowed else reason}


def current_actor(db, p):
    require(p, 'admin', 'operator')
    result = db.execute(select(User.id, User.username, Membership.role).join(Membership, Membership.user_id == User.id)
        .where(User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
               Membership.role.in_(['admin', 'operator']))
        .with_for_update(read=True, of=(User, Membership))).first()
    if not result: raise HTTPException(403, '当前资料管理权限已撤回')
    return result.username


def _head(row):
    value = {'id': row.id, 'workspace_id': row.workspace_id, 'material_id': row.material_id,
             'status': row.status, 'version': row.version,
             'data': {k:v for k,v in row.data.items() if k != 'head_hash'}}
    if isinstance(row, Run): value['generation'] = row.generation
    else: value.update(run_id=row.run_id, unit_index=row.unit_index, evidence_id=row.evidence_id)
    return digest(value)


def seal(row): row.data = {**row.data, 'head_hash': _head(row)}


@integrity
def checked_head(row):
    if row.status not in STATES or type(row.version) is not int or row.version < 1 or row.data.get('head_hash') != _head(row): fail()
    return row


def original(db, p, material):
    evidence, content = evidence_bytes(db, p, material.evidence_id)
    if evidence.sha256 != material.sha256 or evidence.size_bytes != len(content): fail()
    return content


@integrity
def source(db, p, material, run=None, *, rederive=False):
    content = original(db, p, material)
    checked_provenance(db,material)
    if run:
        scope = run.data['scope']
        if scope != {'workspace_id': p.workspace_id, 'material_id': material.id, 'evidence_id': material.evidence_id,
                     'source_sha256': material.sha256, 'name': material.name}:
            fail()
        declared=verify_coverage(run.data['coverage'])
        if declared['source_sha256']!=material.sha256 or run.data.get('native_blocks_hash')!=digest(run.data['native_blocks']): fail()
        if rederive and (declared != material_coverage(db,material,content) or run.data['native_blocks'] != parse_document(content, material.name)['blocks']): fail()
        engine = run.data['engine']
        if not engine.get('manifest') or engine.get('summary_hash')!=digest(engine['manifest']) or engine['manifest'].get('manifest_hash',engine['manifest_hash'])!=engine['manifest_hash']: fail()
    return content


def _checked_run_source(db, p, material, row):
    row = checked_head(row)
    if row.material_id != material.id: fail()
    if type(row.data.get('epoch')) is not int or row.data['epoch'] < 1: fail()
    if not isinstance(row.data.get('prepared'), int) or not 0 <= row.data['prepared'] <= row.data['coverage']['unit_count']: fail()
    from .material_ocr_preparation import enabled
    enabled(row)
    source(db, p, material, row)
    return row


@integrity
def checked_run(db, p, material, run_id, lock=False):
    row = _checked_run_source(db, p, material, scoped(db, Run, run_id, p, lock))
    from .material_ocr_recognition import check_run
    check_run(db, row)
    return row


@integrity
def _checked_run_for_freeze(db, p, material, run_id, lock=False):
    """Fresh original checks, with an inventory local to this freeze only."""
    row = _checked_run_source(db, p, material, scoped(db, Run, run_id, p, lock))
    from .material_ocr_recognition import _FreezeRecognition
    recognition = _FreezeRecognition(db, p, row)
    recognition.check_run()
    return row, recognition


@integrity
def _checked_unit_row(db, p, material, run, row, *, image=True):
    row = checked_head(row)
    if row.workspace_id != p.workspace_id or row.run_id != run.id or row.material_id != material.id: fail()
    descriptors = run.data['coverage']['units']
    if not 1 <= row.unit_index <= len(descriptors) or row.data['descriptor'] != descriptors[row.unit_index-1]: fail()
    render=row.data['render'];receipt=render.get('receipt',{});descriptor=row.data['descriptor']
    expected_input=run.data['scope']['source_sha256'] if descriptor['kind']=='pdf_page' else descriptor['embedded_sha256']
    if (receipt.get('input_sha256')!=expected_input or receipt.get('output_sha256')!=row.data['image_sha256']
        or receipt.get('manifest_hash')!=run.data['engine']['manifest_hash'] or receipt.get('summary_hash')!=run.data['engine']['summary_hash']
        or receipt.get('manifest')!=run.data['engine']['manifest'] or render.get('width')!=row.data['width'] or render.get('height')!=row.data['height']):fail()
    if descriptor['kind']=='pdf_page' and render.get('page')!=descriptor['page']:fail()
    if image:
        ev, content = evidence_bytes(db, p, row.evidence_id)
        if ev.media_type != 'image/png' or ev.sha256 != row.data['image_sha256']: fail()
        try:
            with Image.open(io.BytesIO(content)) as img:
                if img.format != 'PNG' or list(img.size) != [row.data['width'], row.data['height']]: fail()
                img.verify()
        except (OSError, ValueError): fail()
    return row


@integrity
def checked_unit(db, p, material, run, unit_id, lock=False, image=True):
    return _checked_unit_row(db, p, material, run, scoped(db, Unit, unit_id, p, lock), image=image)


def version_hash(row):
    return digest({'id': row.id, 'workspace_id': row.workspace_id, 'run_id': row.run_id,
                   'unit_id': row.unit_id, 'number': row.number, 'kind': row.kind, 'data': row.data})


@integrity
def _regions(regions, unit, raw=False):
    if not isinstance(regions, list) or len(regions) > 500 or sum(len(r.get('text', '')) for r in regions if isinstance(r, dict)) > 20000:
        fail('单页文字或区域超过上限，请拆分资料；原记录保留')
    ids = set()
    for r in regions:
        if not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id'] or r['id'] in ids or not isinstance(r.get('text'), str): fail()
        ids.add(r['id']); box = r.get('box')
        if not isinstance(box, list) or len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in box): fail()
        x,y,w,h = box
        if min(x,y) < 0 or w<=0 or h<=0 or x+w > unit.data['width']+.01 or y+h > unit.data['height']+.01: fail('文字区域必须有面积且在原图范围内')
        score = r.get('score')
        if score is not None and (type(score) not in (int,float) or not math.isfinite(score) or not 0 <= score <= 1): fail()
        if raw and r.get('source_region_id') is not None: fail()
    return regions


@integrity
def checked_versions(db, p, run, unit):
    from .material_ocr_recognition import effective_engine, check_target, target_fields
    engine = effective_engine(db, run, unit)
    recognition = target_fields(run, unit)
    rows = list(db.scalars(select(Version).where(Version.workspace_id == p.workspace_id,
                    Version.unit_id == unit.id).order_by(Version.number)))
    def request_row(actor_id, request_id):
        return db.scalar(select(Request).where(Request.workspace_id == p.workspace_id,
            Request.material_id == run.material_id, Request.actor_id == actor_id, Request.request_id == request_id))
    return _checked_version_rows(p, run, unit, rows, engine, recognition,
        lambda identity: scoped(db, Job, identity, p), request_row,
        lambda target: check_target(db, run, unit, target))


@integrity
def _checked_version_rows(p, run, unit, rows, engine, recognition, job_row, request_row, check_target):
    if len(rows) > MAX_VERSIONS: fail()
    previous = None
    for number, row in enumerate(rows, 1):
        if (row.workspace_id != p.workspace_id or row.unit_id != unit.id or row.number != number
                or row.run_id != run.id or row.kind not in {'raw','edited','confirmed'} or row.content_hash != version_hash(row)): fail()
        if row.data.get('parent_hash') != previous or row.data.get('plain_text') != '\n'.join(r['text'] for r in row.data.get('regions', [])): fail()
        if row.data.get('image_sha256') != unit.data['image_sha256'] or row.data.get('engine_manifest_hash') != engine['manifest_hash']: fail()
        if ({k:v for k,v in row.data.items() if k in {'recognition_contract','recognition_authorization'}}
                != ({'recognition_contract':recognition['recognition_contract'],
                     'recognition_authorization':recognition['recognition_authorization']} if recognition else {})): fail()
        _regions(row.data['regions'], unit, row.kind == 'raw')
        if number == 1:
            if row.kind != 'raw': fail()
            job = job_row(row.data['job_id'])
            if job is None or job.workspace_id != p.workspace_id: fail()
            if job.kind != 'recognize_material_ocr_unit' or job.data.get('target_hash') != row.data.get('job_target_hash') or job.data.get('target_hash') != digest(job.data.get('target')): fail()
            target = job.data['target']
            check_target(target)
            if target['unit_id'] != unit.id or target['run_id'] != run.id or target['material_id'] != run.material_id or target['image_sha256'] != unit.data['image_sha256']: fail()
            if any(target[k]!=v for k,v in {'workspace_id':p.workspace_id,'actor_id':row.data['actor_id'],'generation':run.generation,
                'source_sha256':run.data['scope']['source_sha256'],'engine_manifest_hash':run.data['engine']['manifest_hash']}.items()):fail()
            receipt = row.data.get('receipt', {})
            if receipt.get('input_sha256') != unit.data['image_sha256'] or receipt.get('manifest_hash') != engine['manifest_hash']: fail()
            result=row.data.get('engine_result',{})
            if receipt.get('result_sha256')!=digest(result.get('regions')) or receipt.get('summary_hash')!=engine['summary_hash'] or receipt.get('manifest')!=engine['manifest']:fail()
            projected=[{'id':r['id'],'text':r['text'],'box':r['box'],'score':r.get('score'),'source_region_id':None} for r in result['regions']]
            if projected!=row.data['regions']:fail()
        elif row.kind == 'raw': fail()
        else:
            raw_sources={r['id']:r for r in rows[0].data['regions']};seen=[]
            for region in row.data['regions']:
                source_id=region.get('source_region_id')
                if source_id not in raw_sources and source_id is not None:fail()
                if region.get('score')!=(raw_sources[source_id]['score'] if source_id else None):fail()
                if source_id:seen.append(source_id)
            if len(seen)!=len(set(seen)) or set(seen)!=set(raw_sources):fail()
            saved=request_row(row.data['actor_id'],row.data['request_id'])
            if (not saved or saved.state!='saved' or saved.data.get('target')!=unit.id
                or saved.data.get('result',{}).get('revision_id')!=row.id or saved.data['result'].get('revision_hash')!=row.content_hash
                or saved.data.get('record_hash')!=digest({k:v for k,v in saved.data.items() if k!='record_hash'})
                or saved.body_hash!=_request_hash(saved.action,unit.id,saved.data.get('body'))):fail()
        previous = row.content_hash
    by_id = {r.id:r for r in rows}
    for key, kind in [('raw_id','raw'), ('draft_id','edited'), ('confirmed_id','confirmed')]:
        ref = unit.data.get(key)
        if ref and (ref not in by_id or by_id[ref].kind != kind): fail()
    if rows and unit.data.get('raw_id') != rows[0].id: fail()
    if rows and unit.data.get('last_hash') != rows[-1].content_hash: fail()
    if not rows and any(unit.data.get(k) for k in ['raw_id','draft_id','confirmed_id','last_hash']): fail()
    if unit.status == 'confirmed' and not unit.data.get('confirmed_id'): fail()
    return rows


def _freeze_units(db, p, material, run, units, recognition):
    """Read real rows in bounded batches; preserve every page/version check.

    Private to the full-source freeze path. Material/run/unit originals have
    just been loaded under that operation's ordinary locks or read snapshot.
    The recognition inventory was built from this SAME current run. Nothing is
    stored in the Session, a ContextVar, a global cache, or the returned source.
    Full typed ORM rows are used; JSONB projections cannot replace their data.
    """
    if (recognition._db is not db or recognition._actor_id != p.user_id
            or recognition._workspace_id != p.workspace_id or recognition.run is not run
            or material.workspace_id != p.workspace_id
            or run.workspace_id != p.workspace_id or run.material_id != material.id
            or type(units) is not list or len({u.id for u in units}) != len(units)):
        fail()
    for offset in range(0, len(units), 32):
        batch = units[offset:offset + 32]
        by_unit = {unit.id: [] for unit in batch}
        rows = list(db.scalars(select(Version).where(Version.workspace_id == p.workspace_id,
            Version.unit_id.in_(list(by_unit))).order_by(Version.unit_id, Version.number)))
        # Do not filter by run_id or kind: such a row must be rejected, not hidden.
        seen = set()
        for row in rows:
            if (row.workspace_id != p.workspace_id or row.unit_id not in by_unit or row.id in seen
                    or row.kind not in {'raw', 'edited', 'confirmed'} or type(row.data) is not dict): fail()
            seen.add(row.id); by_unit[row.unit_id].append(row)
        job_values = [row.data.get('job_id') for row in rows if row.kind == 'raw']
        if any(type(identity) is not str for identity in job_values): fail()
        job_ids = set(job_values)
        jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.id.in_(sorted(job_ids))))) if job_ids else []
        by_job = {}
        for job in jobs:
            if job.workspace_id != p.workspace_id or job.id not in job_ids or job.id in by_job: fail()
            by_job[job.id] = job
        if set(by_job) != job_ids: fail()
        request_values = [(row.data.get('actor_id'), row.data.get('request_id')) for row in rows if row.kind != 'raw']
        if any(type(actor) is not str or type(request) is not str for actor, request in request_values): fail()
        request_keys = set(request_values)
        requests = list(db.scalars(select(Request).where(Request.workspace_id == p.workspace_id,
            Request.material_id == run.material_id,
            tuple_(Request.actor_id, Request.request_id).in_(sorted(request_keys))))) if request_keys else []
        by_request = {}
        for request in requests:
            key = (request.actor_id, request.request_id)
            if (request.workspace_id != p.workspace_id or request.material_id != run.material_id
                    or key not in request_keys or key in by_request): fail()
            by_request[key] = request
        if set(by_request) != request_keys: fail()
        for unit in batch:
            _checked_unit_row(db, p, material, run, unit, image=True)
            engine = recognition.effective_engine(unit)
            fields = recognition.target_fields(unit)
            versions = _checked_version_rows(p, run, unit, by_unit[unit.id], engine, fields,
                by_job.get, lambda actor, request: by_request.get((actor, request)),
                lambda target: recognition.check_target(unit, target))
            yield unit, versions


def refs(row):
    if not row: return None
    return {'id': row.id, 'number': row.number, 'hash': row.content_hash, 'kind': row.kind,
            'created_at': row.data['created_at']}


def version_view(row):
    return {**refs(row), **{k:copy.deepcopy(row.data[k]) for k in ['parent_hash','actor_label','reason',
        'regions','plain_text','engine_manifest_hash','elapsed_ms']}}


def unit_summary(db, p, run, unit):
    rows = checked_versions(db,p,run,unit); by_id = {r.id:r for r in rows}; d = unit.data['descriptor']
    return {'id': unit.id, 'index': unit.unit_index, 'title': f"第{d['page']}页" if d['kind']=='pdf_page' else f"第{unit.unit_index}张内嵌图片",
            'kind': d['kind'], 'page': d['page'], 'body_index': d['body_index'], 'status': unit.status,
            'version': unit.version, 'image_sha256': unit.data['image_sha256'], 'width': unit.data['width'], 'height': unit.data['height'],
            'raw_revision': refs(by_id.get(unit.data.get('raw_id'))), 'draft_revision': refs(by_id.get(unit.data.get('draft_id'))),
            'confirmed_revision': refs(by_id.get(unit.data.get('confirmed_id'))), 'error': unit.data.get('error')}


def unit_detail(db,p,material,run,unit):
    from .material_ocr_recognition import effective_engine
    rows=checked_versions(db,p,run,unit); by_id={r.id:r for r in rows}
    writable=p.role in {'admin','operator'} and not material.archived and material.data.get('ocr_run_id')==run.id and bool(unit.data.get('raw_id'))
    enough=len(rows)<MAX_VERSIONS
    reason='当前账号只可查看' if p.role=='viewer' else '原资料已归档、识别范围已更新或该页尚无原识别' if not writable else '单页已保留100个版本，原稿保留' if not enough else '请先保存人工核对稿'
    root=f'/api/setup/materials/{material.id}/ocr'
    return {'material_id':material.id,'material_version':material.version,'run_id':run.id,'run_version':run.version,'generation':run.generation,
            'unit':unit_summary(db,p,run,unit),
            'original':{'url':root+'/units/'+unit.id+'/image?'+urlencode({'run_id':run.id,'image_sha256':unit.data['image_sha256']}),
                        'sha256':unit.data['image_sha256'],'media_type':'image/png','width':unit.data['width'],'height':unit.data['height'],'coordinate_space':'pixel_top_left'},
            'raw':version_view(by_id[unit.data['raw_id']]) if unit.data.get('raw_id') else None,
            'draft':version_view(by_id[unit.data['draft_id']]) if unit.data.get('draft_id') else None,
            'confirmed':version_view(by_id[unit.data['confirmed_id']]) if unit.data.get('confirmed_id') else None,
            'recognition_engine':copy.deepcopy(effective_engine(db,run,unit)),
            'permissions':{'save':permission(writable and enough and bool(unit.data.get('raw_id')),reason),
                           'confirm':permission(writable and enough and unit.status=='review' and bool(unit.data.get('draft_id')),reason),
                           'restore':permission(writable and enough and bool(rows),reason)}}


def units_manifest(db,p,material,run, *, verify_images=False):
    units=list(db.scalars(select(Unit).where(Unit.run_id==run.id,Unit.workspace_id==p.workspace_id).order_by(Unit.unit_index)))
    entries=[]
    for unit in units:
        checked_head(unit); checked_unit(db,p,material,run,unit.id,image=verify_images)
        checked_versions(db,p,run,unit)
        entries.append({'unit_id':unit.id,'index':unit.unit_index,'version':unit.version,'status':unit.status,
                        'image_sha256':unit.data['image_sha256'],'confirmed_id':unit.data.get('confirmed_id'),'last_hash':unit.data.get('last_hash')})
    if len(units)!=run.data['prepared'] or [u.unit_index for u in units]!=list(range(1,len(units)+1)): fail()
    return units,digest({'run_id':run.id,'generation':run.generation,'expected':run.data['coverage']['unit_count'],'units':entries})


def confirmation_view(row):
    return {'id':row.id,'hash':row.content_hash,'run_id':row.run_id,'material_version':row.material_version,
            'blocks_hash':row.data['blocks_hash'],'created_at':row.data['created_at']}


@integrity
def checked_confirmation(db,p,material,confirmation_id):
    row=scoped(db,Confirmation,confirmation_id,p)
    if row.material_id!=material.id or row.content_hash!=digest(row.data['snapshot']) or row.data.get('record_hash')!=digest({k:v for k,v in row.data.items() if k!='record_hash'}): fail()
    run=checked_run(db,p,material,row.run_id)
    source(db,p,material,run,rederive=True)
    if row.data['snapshot']['scope']!=run.data['scope'] or row.data['snapshot']['coverage']!=run.data['coverage'] or row.data['snapshot']['native_blocks']!=run.data['native_blocks']: fail()
    for item in row.data['snapshot']['units']:
        unit=checked_unit(db,p,material,run,item['unit_id']); rows=checked_versions(db,p,run,unit)
        v=next((v for v in rows if v.id==item['revision_id']),None)
        if not v or v.kind!='confirmed' or v.content_hash!=item['revision_hash'] or unit.data['image_sha256']!=item['image_sha256']: fail()
    if len(row.data['snapshot']['units'])!=run.data['coverage']['unit_count'] or digest(row.data['blocks'])!=row.data['blocks_hash']: fail()
    expected=_blocks(db,p,run,row.id,row.content_hash,row.data['snapshot']['units'])
    if expected!=row.data['blocks']: fail()
    return row


def overview(db,p,material):
    cov=material.data.get('ocr_coverage')
    if cov:
        if cov.get('status')!='pending': verify_coverage(cov)
        cov={k:copy.deepcopy(cov.get(k)) for k in ['contract_version','status','unit_count','gaps','reason']}
    else:
        detected=material_coverage(db,material,original(db,p,material))
        cov={k:copy.deepcopy(detected.get(k)) for k in ['contract_version','status','unit_count','gaps','reason']}
    run=None; frozen=None
    if material.data.get('ocr_run_id'):
        run=checked_run(db,p,material,material.data['ocr_run_id']); units,manifest=units_manifest(db,p,material,run)
        progress={s:0 for s in STATES}; progress['total']=run.data['coverage']['unit_count']; progress['prepared']=run.data['prepared']
        for unit in units: progress[unit.status]+=1
        progress['blocked' if run.status in {'failed','blocked'} else 'cancelled' if run.status=='cancelled' else 'queued']+=progress['total']-len(units)
        run_dto={'id':run.id,'version':run.version,'generation':run.generation,'status':run.status,
                 'engine_id':run.data['engine']['id'],'engine_manifest_hash':run.data['engine']['manifest_hash'],
                 'source_sha256':material.sha256,'job_id':run.data['job_id'],'created_at':run.data['created_at'],
                 'error':run.data.get('error'),'units_manifest_hash':manifest,'progress':progress}
        if 'future_recognition_engine' in run.data:
            run_dto['recognition_engine']=copy.deepcopy(run.data['future_recognition_engine'])
        if material.data.get('ocr_confirmation_id'): frozen=checked_confirmation(db,p,material,material.data['ocr_confirmation_id'])
        if material.data.get('ocr_pending'): cov['status']='needs_ocr'
        elif frozen: cov['status']='complete'
    else: run_dto=None
    installed=engine_status(); low_memory=low_memory_engine_status(); bounded=bounded_engine_status(); allowed=p.role in {'admin','operator'} and not material.archived
    reason='当前账号只可查看' if p.role=='viewer' else '原资料已归档' if material.archived else None
    can_start=allowed and not run and cov['status']=='needs_ocr' and installed['ready']
    resume_profiles=[{'engine':engine,'permission':permission(allowed and run and run.status in {'failed','blocked','cancelled'} and engine['ready'],
        reason or engine.get('reason') or '请先核对已停止的识别与所选识别条件')} for engine in (low_memory,bounded)]
    return {'material':{'id':material.id,'name':material.name,'version':material.version,'hash':material.sha256,'status':material.status,'archived':material.archived},
            'coverage':cov,'engine':installed,'low_memory_engine':low_memory,'resume_profiles':resume_profiles,'run':run_dto,'confirmation':confirmation_view(frozen) if frozen else None,
            'permissions':{'start':permission(can_start,reason or cov['reason'] or installed.get('reason') or '请恢复已有识别，不覆盖人工稿'),
                           'retry':permission(allowed and run and run.status in {'failed','blocked','cancelled'} and installed['ready'],reason or installed.get('reason') or '没有需要恢复的识别'),
                           'resume_with_profile':permission(allowed and run and run.status in {'failed','blocked','cancelled'} and low_memory['ready'],reason or low_memory.get('reason') or '请先核对已停止的识别与低内存条件'),
                           'cancel':permission(allowed and run and run.status in {'queued','running'},reason or '后台识别已结束'),
                           'confirm':permission(allowed and run and run.status=='review' and progress['confirmed']==progress['total'] and progress['total']>0,reason or '请先保存并确认每个必要页面')}}


def material_summary(db,material):
    cov=material.data.get('ocr_coverage')
    provenance=checked_provenance(db,material)
    if not cov:return None
    run=db.get(Run,material.data.get('ocr_run_id')) if material.data.get('ocr_run_id') else None
    if run: checked_head(run)
    confirmed=db.scalar(select(func.count()).select_from(Unit).where(Unit.run_id==run.id,Unit.status=='confirmed')) if run else 0
    return {'coverage_status':'needs_ocr' if material.data.get('ocr_pending') else 'complete' if material.data.get('ocr_confirmation_id') else cov['status'],
            'coverage_profile':cov.get('profile',{}).get('kind'), 'coverage_contract_version':cov.get('contract_version'), 'force_all_pages':cov.get('force_all_pages',False),
            'run_id':run.id if run else None,'run_status':run.status if run else None,'total':cov.get('unit_count',0),'confirmed':confirmed,'reason':cov.get('reason') or (run.data.get('error') if run else None)}


@integrity
def ensure_publishable(db,p,material):
    if not material.data.get('ocr_coverage'):
        # 新V2预览可读检测旧原件，但不回填旧行或修改已付原答。
        declared=material_coverage(db,material,original(db,p,material))
        if declared['status']!='complete':fail('既往资料仍有扫描页、内嵌图片或覆盖缺口，请先重新解析并逐页核对，再生成新预览；旧结果保留')
        return
    if material.data['ocr_coverage'].get('status')=='pending':fail('资料尚在解析，请等待覆盖核对完成')
    cov=verify_coverage(material.data['ocr_coverage'])
    if cov != material_coverage(db, material, original(db,p,material)): fail()
    if cov['status']=='blocked' or material.data.get('ocr_pending') or cov['status']=='needs_ocr' and not material.data.get('ocr_confirmation_id'): fail('资料仍有未确认的页面或覆盖缺口，请逐页核对后重新生成预览')
    if material.data.get('ocr_confirmation_id'):
        confirmed=checked_confirmation(db,p,material,material.data['ocr_confirmation_id'])
        if material.version!=confirmed.material_version or material.data.get('blocks')!=confirmed.data['blocks']: fail()


def _request_hash(action,target,body): return digest({'action':action,'target':target,'body':body})


@integrity
def checked_request(db,p,material,request_id):
    row=db.scalar(select(Request).where(Request.workspace_id==p.workspace_id,Request.material_id==material.id,
                Request.actor_id==p.user_id,Request.request_id==request_id))
    if row and (row.state not in {'saved','abandoned'} or row.body_hash!=_request_hash(row.action,row.data.get('target'),row.data.get('body'))
                or row.data.get('record_hash')!=digest({k:v for k,v in row.data.items() if k!='record_hash'})): fail()
    return row


def request_view(row):
    return {'id':row.request_id,'action':row.action,'state':row.state,'body_hash':row.body_hash,'result':copy.deepcopy(row.data.get('result'))}


def reply(db,p,material,row):
    result=row.data.get('result') or {}; unit=None
    if result.get('unit_id'):
        run=checked_run(db,p,material,result['run_id']); u=checked_unit(db,p,material,run,result['unit_id'])
        unit=unit_detail(db,p,material,run,u)
    return {'request':request_view(row),'view':overview(db,p,material),'unit':unit}


def _write_request(db,p,material,action,target,body,state,result):
    d={'target':target,'body':body,'result':result}; d['record_hash']=digest(d)
    row=Request(workspace_id=p.workspace_id,material_id=material.id,actor_id=p.user_id,request_id=body['request_id'],
                action=action,state=state,body_hash=_request_hash(action,target,body),data=d)
    db.add(row);db.flush();return row


def _cas(row,expected):
    if row.version!=expected: raise HTTPException(409,CAS)


def _current(material,run):
    if material.archived or material.data.get('ocr_run_id')!=run.id: fail('原资料已归档或识别范围已更新')


def supersede_for_native_parse(db,p,material,parse_generation):
    """新解析解除旧识别的当前权限；原图、人工稿与确认版本仍为历史。"""
    run_id=material.data.get('ocr_run_id')
    if not run_id:return
    run=checked_run(db,p,material,run_id,True)
    for unit in db.scalars(select(Unit).where(Unit.run_id==run.id).order_by(Unit.unit_index).with_for_update()):
        checked_head(unit)
        if unit.status in {'queued','running'}:
            unit.status='cancelled';unit.version+=1;seal(unit)
    current_actor(db,p)
    run.status='cancelled';run.version+=1
    run.data={**run.data,'epoch':run.data['epoch']+1,'superseded_by_parse_generation':parse_generation};seal(run)
    material.data={k:v for k,v in material.data.items() if k not in {'ocr_run_id','ocr_confirmation_id','ocr_pending'}}


def execute(db,p,material_id,action,body,target=None):
    material=scoped(db,SetupMaterial,material_id,p,True); require(p,'admin','operator')
    value=body.model_dump(mode='json') if hasattr(body,'model_dump') else copy.deepcopy(body)
    old=checked_request(db,p,material,value['request_id'])
    if old:
        current_actor(db,p)
        if old.action!=action or old.body_hash!=_request_hash(action,target,value): fail('此请求编号已用于不同操作，原回执保留')
        if old.state=='abandoned': fail('此原请求已明确放弃，请重新核对')
        return reply(db,p,material,old)
    _cas(material,value['expected_material_version']);original(db,p,material)
    run=None;unit=None;revision=None;confirmation=None;saved_request=None
    if action=='start':
        current_actor(db,p)
        content=original(db,p,material)
        cov=verify_coverage(material.data['ocr_coverage']) if material.data.get('ocr_coverage') else material_coverage(db,material,content)
        if cov!=material_coverage(db,material,content):fail()
        if material.archived or material.data.get('ocr_run_id') or cov['status']!='needs_ocr' or not cov['unit_count']: fail('请恢复已有识别，或先核对资料覆盖缺口')
        runtime=engine_status()
        if not runtime['ready'] or runtime['id']!=value['engine_id'] or runtime['manifest_hash']!=value['engine_manifest_hash'] or not runtime.get('manifest'): fail(runtime.get('reason') or '本地引擎版本已变化，请刷新后重试')
        runtime={**runtime,'summary_hash':digest(runtime['manifest'])}
        generation=material.data.get('ocr_generation',0)+1
        run=Run(id=uid(),workspace_id=p.workspace_id,material_id=material.id,generation=generation,status='queued',version=1,data={
            'scope':{'workspace_id':p.workspace_id,'material_id':material.id,'evidence_id':material.evidence_id,'source_sha256':material.sha256,'name':material.name},
            'coverage':copy.deepcopy(cov),'native_blocks':parse_document(content,material.name)['blocks'],
            'native_blocks_hash':digest(parse_document(content,material.name)['blocks']),
            'engine':copy.deepcopy(runtime),'prepared':0,'epoch':1,'created_at':now().isoformat(),'error':None,
            'preparation':{'schema':'material-ocr-preparation-v2','batch_size':1}})
        db.add(run);db.flush();_queue_prepare(db,p,material,run)
        material.version+=1;material.status='needs_input';material.data={**material.data,'ocr_coverage':cov,'ocr_run_id':run.id,'ocr_generation':generation,'ocr_pending':True}
        seal(run)
    else:
        run=checked_run(db,p,material,value['run_id'],True);_current(material,run)
        if target:unit=checked_unit(db,p,material,run,target,True)
        current_actor(db,p)
        if unit:
            _cas(unit,value['expected_unit_version']); rows=checked_versions(db,p,run,unit)
            if not rows:fail('该页尚未产生可核对原结果')
            terminal=run.status in {'failed','blocked','cancelled'}
            if len(rows)>=MAX_VERSIONS:fail('单页已保留100个版本，原稿与请求均未改变')
            if action=='save':
                parent=rows[-1]
                if parent.content_hash!=value['parent_hash']:raise HTTPException(409,CAS)
                regions=_edit_regions(value['regions'],unit,rows[0],value['reason'])
                revision=_version(db,p,run,unit,'edited',regions,value['reason'],rows[-1].content_hash,request_id=value['request_id'])
                unit.data={**unit.data,'draft_id':revision.id,'confirmed_id':None};unit.status='review'
            elif action=='unit_confirm':
                if unit.status!='review':fail('该页已确认；修改后请先保存新人工稿')
                parent=next((r for r in rows if r.id==unit.data.get('draft_id')),None)
                if not parent:fail('请先保存人工核对稿，再确认该保存版本')
                if parent.content_hash!=value['revision_hash']:raise HTTPException(409,CAS)
                revision=_version(db,p,run,unit,'confirmed',copy.deepcopy(parent.data['regions']),value['reason'],rows[-1].content_hash,request_id=value['request_id'])
                unit.data={**unit.data,'confirmed_id':revision.id};unit.status='confirmed'
            elif action=='restore':
                old_version=next((r for r in rows if r.id==value['revision_id']),None)
                if not old_version or old_version.content_hash!=value['revision_hash']:fail('所选旧稿版本无法核对')
                restored=copy.deepcopy(old_version.data['regions'])
                if old_version.kind=='raw':
                    for region in restored:region['source_region_id']=region['id']
                revision=_version(db,p,run,unit,'edited',restored,value['reason'],rows[-1].content_hash,request_id=value['request_id'])
                unit.data={**unit.data,'draft_id':revision.id,'confirmed_id':None};unit.status='review'
            else:fail()
            unit.version+=1;seal(unit);run.version+=1
            if not terminal:
                pending=db.scalar(select(func.count()).select_from(Unit).where(Unit.run_id==run.id,Unit.status.in_(['queued','running'])))
                from .material_ocr_preparation import needs_preparation
                run.status='running' if pending or needs_preparation(run) else 'review'
            seal(run)
            material.status='needs_input';material.data={**material.data,'ocr_pending':True}
        elif action=='document_confirm':
            _cas(run,value['expected_run_version']);source(db,p,material,run,rederive=True)
            units,manifest=units_manifest(db,p,material,run,verify_images=True)
            if manifest!=value['units_manifest_hash']:raise HTTPException(409,CAS)
            if run.status!='review' or len(units)!=run.data['coverage']['unit_count'] or not units or any(u.status!='confirmed' for u in units) or run.data['coverage']['gaps']:fail('请先核对并确认全部必要页面')
            selected=[]
            for u in units:
                v=scoped(db,Version,u.data['confirmed_id'],p)
                selected.append({'unit_id':u.id,'index':u.unit_index,'revision_id':v.id,'revision_hash':v.content_hash,'image_sha256':u.data['image_sha256']})
            snapshot={'scope':run.data['scope'],'coverage':run.data['coverage'],'native_blocks':run.data['native_blocks'],'units':selected}
            confirmation=Confirmation(id=uid(),workspace_id=p.workspace_id,material_id=material.id,run_id=run.id,material_version=material.version+1,
                    content_hash=digest(snapshot),data={})
            blocks=_blocks(db,p,run,confirmation.id,confirmation.content_hash,selected)
            data={'snapshot':snapshot,'blocks':blocks,'blocks_hash':digest(blocks),'created_at':now().isoformat(),
                  'actor_id':p.user_id,'actor_label':current_actor(db,p),'reason':value['reason']}
            confirmation.data={**data,'record_hash':digest(data)};db.add(confirmation);db.flush()
            material.version+=1;material.status='parsed' if blocks else 'needs_input'
            material.data={**material.data,'ocr_confirmation_id':confirmation.id,'ocr_pending':False,'blocks':blocks}
            run.version+=1;run.status='confirmed';run.data={**run.data,'confirmation_id':confirmation.id};seal(run)
        elif action=='resume_with_profile':
            from .material_ocr_recognition import make_authorization
            from .material_ocr_preparation import prepared_units, resume
            _cas(run,value['expected_run_version'])
            if run.status not in {'failed','blocked','cancelled'}:fail('只有已停止的识别可以明确选择低内存恢复')
            source(db,p,material,run,rederive=True)
            prepared=prepared_units(db,run)
            for page in prepared:
                checked_unit(db,p,material,run,page.id,True);checked_versions(db,p,run,page)
            authorization=make_authorization(p,material,run,value,recognition_engine_status(value['engine_manifest_hash']),prepared)
            run.version+=1;material.version+=1;run.status='queued'
            run.data={**run.data,'epoch':authorization['epoch'],'error':None,
                'recognition_authorizations':[*run.data.get('recognition_authorizations',[]),authorization],
                'future_recognition_engine':copy.deepcopy(authorization['engine']),
                'future_recognition_authorization':authorization['authorization_hash']}
            seal(run)
            # Write the final receipt before dispatch validation, in this same
            # transaction. There is no "pending authorization" bypass.
            accepted={'material_version':material.version,'run_id':run.id,'run_version':run.version,
                'unit_id':None,'unit_version':None,'revision_id':None,'revision_hash':None,'confirmation_id':None,
                'recognition_authorization':authorization}
            saved_request=_write_request(db,p,material,action,None,value,'saved',accepted)
            for page in prepared:
                if page.data.get('raw_id'):continue
                page.data={**page.data,'recognition_engine':copy.deepcopy(authorization['engine']),
                    'recognition_authorization':authorization['authorization_hash']};seal(page)
            resume(db,p,material,run);seal(run)
        elif action=='cancel':
            _cas(run,value['expected_run_version'])
            if run.status not in {'queued','running'}:fail('后台识别已结束，不清除已保存人工稿')
            for u in db.scalars(select(Unit).where(Unit.run_id==run.id).order_by(Unit.unit_index).with_for_update()):
                checked_head(u)
                if u.status in {'queued','running'}:u.status='cancelled';u.version+=1;seal(u)
            run.status='cancelled';run.version+=1;run.data={**run.data,'epoch':run.data['epoch']+1};seal(run);material.version+=1
        elif action=='retry':
            _cas(run,value['expected_run_version'])
            if run.status not in {'failed','blocked','cancelled'}:fail('没有需要恢复的识别')
            installed=engine_status()
            if not installed['ready'] or installed['manifest_hash']!=run.data['engine']['manifest_hash']:fail(installed.get('reason') or '引擎版本变化，不能替换原识别授权')
            run.version+=1;run.status='queued';run.data={**run.data,'epoch':run.data['epoch']+1,'error':None};material.version+=1
            from .material_ocr_preparation import enabled, resume
            if enabled(run):resume(db,p,material,run)
            else:_queue_prepare(db,p,material,run)
            seal(run)
        else:fail()
    audit(db,p,'setup.ocr.'+action,material.id,{'run_id':run.id,'unit_id':unit.id if unit else None})
    result={'material_version':material.version,'run_id':run.id,'run_version':run.version,'unit_id':unit.id if unit else None,
            'unit_version':unit.version if unit else None,'revision_id':revision.id if revision else None,'revision_hash':revision.content_hash if revision else None,
            'confirmation_id':confirmation.id if confirmation else None}
    row=saved_request or _write_request(db,p,material,action,target,value,'saved',result);db.flush()
    return reply(db,p,material,row)


def _edit_regions(values,unit,raw,reason):
    sources={r['id']:r for r in raw.data['regions']}; seen=set(); regions=[]
    for value in values:
        r=copy.deepcopy(value); source_id=r.get('source_region_id')
        if source_id:
            if source_id not in sources or source_id in seen:fail('原识别区域需逐一保留，不能误删或重复')
            seen.add(source_id)
        r['score']=sources[source_id]['score'] if source_id else None
        if not r['text'].strip() and not reason.strip():fail('空白区域须说明核对理由')
        regions.append(r)
    if seen!=set(sources):fail('原识别区域需逐一保留，删除文字可留空并说明')
    return _regions(regions,unit)


def _version(db,p,run,unit,kind,regions,reason,parent_hash,**extra):
    from .material_ocr_recognition import effective_engine, target_fields
    engine=effective_engine(db,run,unit);recognition=target_fields(run,unit)
    number=db.scalar(select(func.count()).select_from(Version).where(Version.unit_id==unit.id))+1
    if number>MAX_VERSIONS:fail('单页已保留100个版本，原记录保留')
    data={'parent_hash':parent_hash,'created_at':now().isoformat(),'actor_id':p.user_id,'actor_label':current_actor(db,p),
          'reason':reason,'regions':regions,'plain_text':'\n'.join(r['text'] for r in regions),
          'engine_manifest_hash':engine['manifest_hash'],
          'image_sha256':unit.data['image_sha256'],'elapsed_ms':None,**extra}
    if recognition:
        data.update(recognition_contract=recognition['recognition_contract'],recognition_authorization=recognition['recognition_authorization'])
    row=Version(id=uid(),workspace_id=p.workspace_id,run_id=run.id,unit_id=unit.id,number=number,kind=kind,data=data,content_hash='')
    row.content_hash=version_hash(row);db.add(row);db.flush();unit.data={**unit.data,'last_hash':row.content_hash};return row


def _blocks(db,p,run,confirmation_id,confirmation_hash,selected):
    pdf_pages={u['page'] for u in run.data['coverage']['units'] if u['kind']=='pdf_page'}
    ordered=[]
    for index,block in enumerate(run.data['native_blocks']):
        if block.get('location',{}).get('page') in pdf_pages:continue
        ordered.append((block.get('location',{}).get('body_index',0),0,index,copy.deepcopy(block)))
    for item in selected:
        unit=scoped(db,Unit,item['unit_id'],p);version=scoped(db,Version,item['revision_id'],p);d=unit.data['descriptor']
        for n,region in enumerate(version.data['regions']):
            if not region['text'].strip():continue
            locator={'confirmation_id':confirmation_id,'confirmation_hash':confirmation_hash,'run_id':run.id,'unit_id':unit.id,
                     'revision_id':version.id,'revision_hash':version.content_hash,'image_sha256':unit.data['image_sha256'],'region_id':region['id'],'box':region['box']}
            location={'ocr':locator,**({'page':d['page']} if d['page'] else {'body_index':d['body_index']})}
            block={'id':f'ocr-{confirmation_id}-{unit.unit_index}-{n+1}','text':region['text'],'kind':'ocr_confirmed','location':location}
            ordered.append((d['page'] or d['body_index'],1,item['index']*501+n,block))
    return [x[3] for x in sorted(ordered,key=lambda x:x[:3])]


def _job_target(p,material,run,unit=None):
    value={'actor_id':p.user_id,'workspace_id':p.workspace_id,'material_id':material.id,'run_id':run.id,'generation':run.generation,
            'epoch':run.data['epoch'],'source_sha256':material.sha256,'engine_manifest_hash':run.data['engine']['manifest_hash'],
            'unit_id':unit.id if unit else None,'image_sha256':unit.data['image_sha256'] if unit else None}
    if unit:
        from .material_ocr_recognition import target_fields
        value.update(target_fields(run,unit))
    return value


def _queue_prepare(db,p,material,run):
    from .material_ocr_preparation import enabled, queue_prepare
    if enabled(run):return queue_prepare(db,p,material,run)
    target=_job_target(p,material,run)
    job=Job(workspace_id=p.workspace_id,kind='prepare_material_ocr',idempotency_key=f'ocr-prepare:{run.id}:{run.data["epoch"]}',
            data={'actor_id':p.user_id,'material_id':material.id,'run_id':run.id,'target':target,'target_hash':digest(target)})
    db.add(job);db.flush();run.data={**run.data,'job_id':job.id}


def _queue_unit(db,p,material,run,unit):
    from .material_ocr_preparation import enabled, queue_unit
    if enabled(run) or 'recognition_engine' in unit.data:return queue_unit(db,p,material,run,unit)
    target=_job_target(p,material,run,unit)
    job=Job(workspace_id=p.workspace_id,kind='recognize_material_ocr_unit',idempotency_key=f'ocr-unit:{unit.id}:{run.data["epoch"]}',
            data={'actor_id':p.user_id,'material_id':material.id,'run_id':run.id,'unit_id':unit.id,'target':target,'target_hash':digest(target)})
    db.add(job);db.flush();unit.data={**unit.data,'job_id':job.id,'job_target_hash':digest(target),'error':None};unit.status='queued';unit.version+=1;seal(unit)


def _job_scope(db,p,job):
    material=scoped(db,SetupMaterial,job.data['material_id'],p,True)
    run=checked_run(db,p,material,job.data['run_id'],True)
    target=job.data.get('target')
    if job.workspace_id!=p.workspace_id or job.data.get('target_hash')!=digest(target) or not isinstance(target,dict):fail()
    if any(target[k]!=v for k,v in {'workspace_id':p.workspace_id,'actor_id':p.user_id,'material_id':material.id,'run_id':run.id,
                                   'generation':run.generation,'source_sha256':material.sha256,'engine_manifest_hash':run.data['engine']['manifest_hash']}.items()):fail()
    from .material_ocr_preparation import validate_job
    validate_job(p,material,run,job)
    active=(material.data.get('ocr_run_id')==run.id and not material.archived and target['epoch']==run.data['epoch'] and run.status not in {'cancelled','blocked','failed'})
    if job.kind=='recognize_material_ocr_unit':
        from .material_ocr_recognition import check_target, check_historical_target
        page=checked_unit(db,p,material,run,job.data['unit_id'],True)
        if active:check_target(db,run,page,target)
        else:check_historical_target(db,run,page,target)
    elif any(k in target for k in ('recognition_contract','recognition_engine_id','recognition_engine_manifest_hash','recognition_authorization')):fail()
    return material,run,active


def runtime_guard(db,p,job):
    """子进程轮询只读当前授权；不持有工作事务或改变原件/任务。"""
    from .leases import ownership, LeaseLost
    lease=ownership(db); bind=db.get_bind(); target=copy.deepcopy(job.data['target']); last=[0.0]
    def check():
        # 引擎每100ms回调，PG最多每250ms读取；提交处仍立即严校。
        clock=time.monotonic()
        if clock-last[0]<.25:return
        last[0]=clock
        with Session(bind) as other:
            actor=other.execute(select(User.active,Membership.role).join(Membership,Membership.user_id==User.id)
                .where(User.id==p.user_id,Membership.workspace_id==p.workspace_id)).first()
            if not actor or not actor.active or actor.role!=p.role or actor.role not in {'admin','operator'}:
                raise HTTPException(403,'后台资料管理权限已撤回，本地处理已停止')
            current=other.execute(select(Run.status,Run.data,SetupMaterial.archived,SetupMaterial.data)
                .join(SetupMaterial,SetupMaterial.id==Run.material_id).where(Run.id==target['run_id'],Run.workspace_id==p.workspace_id)).first()
            if not current or current[0] in {'cancelled','failed','blocked'} or current[1].get('epoch')!=target['epoch'] or current[2] or current[3].get('ocr_run_id')!=target['run_id']:
                fail('识别已取消、范围已更新或原资料已归档，本地处理已停止')
            if 'recognition_contract' in target:
                from .material_ocr_recognition import check_target
                active_run=other.scalar(select(Run).where(Run.id==target['run_id'],Run.workspace_id==p.workspace_id))
                page=other.scalar(select(Unit).where(Unit.id==target['unit_id'],Unit.workspace_id==p.workspace_id))
                if not active_run or not page or page.data.get('job_id')!=job.id:fail('该页识别授权已更新，本地处理已停止')
                checked_head(active_run);checked_head(page);check_target(other,active_run,page,target)
            if lease:
                active=other.scalar(select(Job.id).where(Job.id==job.id,Job.status=='running',Job.lease_token==lease['lease_token'],Job.lease_until>func.clock_timestamp()))
                if not active:raise LeaseLost('识别任务租约已失效，本地处理已停止')
    return check


def process_prepare(db,p,job):
    from . import material_ocr_runtime as runtime
    material,run,active=_job_scope(db,p,job)
    if not active:return
    current_actor(db,p)
    from .material_ocr_preparation import enabled, process_batch
    if enabled(run):return process_batch(db,p,job,material,run)
    if run.data.get('prepared_job_id')==job.id:return
    if job.data.get('recovery_only'):fail('后台准备已达到恢复上限，请核对已保存页面并明确恢复')
    installed=engine_status()
    if not installed['ready'] or installed['manifest_hash']!=run.data['engine']['manifest_hash']:fail(installed.get('reason') or '固定本地引擎无法核对')
    content=source(db,p,material,run); expected=run.data['coverage']['units'];run.status='running';run.version+=1;seal(run);db.commit()
    guard=runtime_guard(db,p,job)
    # 渲染只在锁外执行。每页单独提交，重放时从已保存原图继续。
    for descriptor in expected:
        material,run,active=_job_scope(db,p,job)
        if not active:return
        existing=db.scalar(select(Unit).where(Unit.run_id==run.id,Unit.unit_index==descriptor['index']))
        if existing:
            checked_unit(db,p,material,run,existing.id);checked_versions(db,p,run,existing)
            if not existing.data.get('raw_id'):_queue_unit(db,p,material,run,existing)
            db.commit();continue
        db.commit()
        if descriptor['kind']=='pdf_page':
            info=runtime.pdf_info(content,guard=guard)
            if info['page_count']!=len(expected) or info['source_sha256']!=material.sha256:fail()
            rendered=runtime.render_pdf(content,page=descriptor['page'],guard=guard)
            if not isinstance(rendered,list) or len(rendered)!=1 or rendered[0]['page']!=descriptor['page']:fail()
            image=rendered[0]
        else:
            with checked_zip(content) as archive: embedded=archive.read(descriptor['archive_name'])
            if hashlib.sha256(embedded).hexdigest()!=descriptor['embedded_sha256']:fail()
            image=runtime.normalize_image(embedded,guard=guard)
        material,run,active=_job_scope(db,p,job)
        if not active:return
        current_actor(db,p);source(db,p,material,run)
        png=image['png']
        with Image.open(io.BytesIO(png)) as decoded:
            if decoded.format!='PNG' or list(decoded.size)!=[image['width'],image['height']] or decoded.width*decoded.height>24000000:fail()
            decoded.verify()
        if len(png)>50*1024*1024:fail('单页原图超过50MB，本地处理已停止')
        evidence=save_immutable(db,p,png,'image/png',{'modality':'document_ocr_page','source_material_id':material.id,'source_sha256':material.sha256})
        unit=Unit(id=uid(),workspace_id=p.workspace_id,material_id=material.id,run_id=run.id,unit_index=descriptor['index'],
            evidence_id=evidence.id,status='queued',version=1,data={'descriptor':descriptor,'image_sha256':evidence.sha256,
                'width':image['width'],'height':image['height'],'render':{k:v for k,v in image.items() if k!='png'},
                'raw_id':None,'draft_id':None,'confirmed_id':None,'last_hash':None,'error':None})
        from .material_ocr_recognition import initialize_unit
        initialize_unit(db,run,unit)
        db.add(unit);db.flush();_queue_unit(db,p,material,run,unit)
        run.version+=1;run.data={**run.data,'prepared':descriptor['index']};seal(run);db.commit()
    material,run,active=_job_scope(db,p,job)
    if not active:return
    run.version+=1;run.data={**run.data,'prepared_job_id':job.id};seal(run)
    pending=db.scalar(select(func.count()).select_from(Unit).where(Unit.run_id==run.id,Unit.status.in_(['queued','running'])))
    if pending==0:run.status='review';seal(run)



def _recognize_page(png, *, expected_manifest_hash, guard):
    """Only two fixed resource reasons may cross the worker's safe-error boundary."""
    from . import material_ocr_runtime as runtime
    try:
        return runtime.recognize(png,expected_manifest_hash=expected_manifest_hash,guard=guard)
    except HTTPException as error:
        allowed={
            '本地文字识别暂不可用：本地处理内存超过上限，可拆分页面后重试',
            '本地文字识别暂不可用：处理超时，可稍后重试或拆分页面',
        }
        if error.status_code==409 and type(error.detail) is str and error.detail in allowed:
            from .workflow_abilities import WorkflowBlocked
            raise WorkflowBlocked(error.detail) from None
        raise


def process_unit(db,p,job):
    from . import material_ocr_runtime as runtime
    from .material_ocr_recognition import effective_engine, check_target
    material,run,active=_job_scope(db,p,job);unit=checked_unit(db,p,material,run,job.data['unit_id'],True)
    rows=checked_versions(db,p,run,unit)
    if rows:
        if rows[0].data['job_id']!=job.id:fail('旧识别任务不可替换当前原结果')
        return  # 页结果已提交、job尾事务前崩溃，安全重放原页。
    if job.data.get('recovery_only'):fail('后台识别已达到恢复上限，请核对页面并明确恢复')
    if not active or unit.data.get('job_id')!=job.id or unit.status=='cancelled':return
    if job.data['target']!=_job_target(p,material,run,unit):fail()
    current_actor(db,p)
    engine=effective_engine(db,run,unit)
    installed=recognition_engine_status(engine['manifest_hash']) if 'recognition_engine' in unit.data else engine_status()
    if not installed['ready'] or installed['manifest_hash']!=engine['manifest_hash']:fail(installed.get('reason') or '固定本地识别引擎无法核对')
    if 'recognition_engine' in unit.data and (installed['id']!=engine['id'] or installed['manifest']!=engine['manifest'] or installed['summary_hash']!=engine['summary_hash']):fail('固定低内存识别版本无法核对，原结果保留')
    _,png=evidence_bytes(db,p,unit.evidence_id);unit.status='running';unit.version+=1;seal(unit);run.version+=1;seal(run);db.commit()
    result=_recognize_page(png,expected_manifest_hash=engine['manifest_hash'],guard=runtime_guard(db,p,job))
    material,run,active=_job_scope(db,p,job);unit=checked_unit(db,p,material,run,job.data['unit_id'],True)
    if not active or unit.data.get('job_id')!=job.id:return
    check_target(db,run,unit,job.data['target'])
    if effective_engine(db,run,unit)!=engine:fail()
    current_actor(db,p);regions=[]
    for r in result['regions']:
        regions.append({'id':r['id'],'text':r['text'],'box':r['box'],'score':r.get('score'),'source_region_id':None})
    _regions(regions,unit,True)
    receipt=result.get('receipt',{})
    if receipt.get('input_sha256')!=unit.data['image_sha256'] or receipt.get('manifest_hash')!=engine['manifest_hash']:fail('本地引擎回执与原图或固定版本不一致')
    if receipt.get('result_sha256')!=digest(result['regions']) or receipt.get('summary_hash')!=engine['summary_hash'] or receipt.get('manifest')!=engine['manifest']:fail('本地原答与实际引擎回执无法核对')
    elapsed=result.get('elapsed_ms')
    if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:fail()
    raw=_version(db,p,run,unit,'raw',regions,'本地原识别，尚待人工核对',None,
                 elapsed_ms=elapsed,receipt=receipt,engine_result={k:copy.deepcopy(v) for k,v in result.items() if k!='receipt'},job_id=job.id,job_target_hash=job.data['target_hash'])
    unit.status='review';unit.version+=1;unit.data={**unit.data,'raw_id':raw.id};seal(unit)
    run.version+=1
    count=db.scalar(select(func.count()).select_from(Unit).where(Unit.run_id==run.id,Unit.status.in_(['queued','running'])))
    if count==0 and run.data['prepared']==run.data['coverage']['unit_count']:run.status='review'
    seal(run);audit(db,p,'setup.ocr.page_ready',unit.id,{'run_id':run.id,'revision_hash':raw.content_hash})
    db.commit()


def fail_job(db,p,job,reason):
    material,run,active=_job_scope(db,p,job)
    if not active:return
    if job.kind=='recognize_material_ocr_unit':
        unit=checked_unit(db,p,material,run,job.data['unit_id'],True)
        if unit.data.get('raw_id'):return
        if unit.data.get('job_id')!=job.id:return
        unit.status='failed';unit.version+=1;unit.data={**unit.data,'error':reason};seal(unit)
    run.status='failed';run.version+=1;run.data={**run.data,'error':reason};seal(run)


def cursor_encode(value): return base64.urlsafe_b64encode(__import__('json').dumps({**value,'hash':digest(value)},sort_keys=True).encode()).decode().rstrip('=')


def cursor_decode(after,scope):
    try:
        value=__import__('json').loads(base64.urlsafe_b64decode(after+'='*(-len(after)%4)))
        plain={k:v for k,v in value.items() if k!='hash'}
        if value['hash']!=digest(plain) or any(value[k]!=v for k,v in scope.items()):fail('分页范围已改变，请重新读取')
        return plain
    except (ValueError,KeyError,TypeError):fail('分页凭据无法核对')


def abandon(db,p,material_id,request_id,body):
    material=scoped(db,SetupMaterial,material_id,p,True);current_actor(db,p)
    value=body.model_dump(mode='json');original_request=value['original_request']
    if original_request.get('request_id')!=request_id:fail('原请求编号无法核对')
    action=value['action'];target=value['target']
    old=checked_request(db,p,material,request_id)
    if old:
        if old.action!=action or old.body_hash!=_request_hash(action,target,original_request):fail('原请求正文或目标不一致')
        return reply(db,p,material,old)
    _cas(material,value['expected_material_version'])
    row=_write_request(db,p,material,action,target,original_request,'abandoned',None)
    audit(db,p,'setup.ocr.abandon',material.id,{'request_id':request_id,'reason':value['reason']})
    return reply(db,p,material,row)
