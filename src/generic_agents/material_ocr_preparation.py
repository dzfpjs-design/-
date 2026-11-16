"""Explicit v2 bounded OCR preparation; legacy run and unit targets stay intact."""
import copy
import hashlib
import io
from datetime import timedelta

from PIL import Image
from sqlalchemy import select, func

from .contracts import digest
from .models import Job, MaterialOcrUnit as Unit, now, uid

SCHEMA = 'material-ocr-preparation-v2'
POLICY = {'schema': SCHEMA, 'batch_size': 1}


def enabled(run):
    from . import material_ocr as o
    if 'preparation' not in run.data:
        return False
    policy = run.data['preparation']
    if (not isinstance(policy, dict) or set(policy) != {'schema', 'batch_size'}
            or policy.get('schema') != SCHEMA or type(policy.get('batch_size')) is not int
            or policy['batch_size'] != 1):
        o.fail('分批准备协议无法核对，原识别与人工稿保留')
    total = run.data['coverage']['unit_count']
    prepared = run.data['prepared']
    if (type(total) is not int or total < 1 or total != len(run.data['coverage']['units'])
            or type(prepared) is not int or not 0 <= prepared <= total):
        o.fail()
    return True


def prepare_target(p, material, run, cursor=None, epoch=None):
    from . import material_ocr as o
    if not enabled(run): o.fail()
    total = run.data['coverage']['unit_count']
    cursor = run.data['prepared'] if cursor is None else cursor
    epoch = run.data['epoch'] if epoch is None else epoch
    if type(cursor) is not int or not 0 <= cursor <= total or type(epoch) is not int or epoch < 1:
        o.fail('分批准备位置无法核对，原页面未改变')
    return {**o._job_target(p, material, run), 'epoch': epoch,
            'preparation_contract': SCHEMA, 'cursor': cursor, 'end': min(cursor + 1, total)}


def prepare_key(run, target):
    return f'ocr-prepare-v2:{run.id}:{target["epoch"]}:{target["cursor"]}:{target["end"]}'


def validate_job(p, material, run, job):
    """Old epochs remain readable but inactive; malformed v2 never becomes v1."""
    from . import material_ocr as o
    active_v2 = enabled(run)
    if job.kind != 'prepare_material_ocr': return active_v2
    target = job.data.get('target', {})
    if not active_v2:
        if any(k in target for k in ('preparation_contract', 'cursor', 'end')): o.fail()
        return False
    expected = prepare_target(p, material, run, target.get('cursor'), target.get('epoch'))
    # Missing cursor/epoch must not use the current default and silently re-sign.
    if (set(target) != set(expected) or target != expected
            or type(target.get('cursor')) is not int or type(target.get('end')) is not int
            or type(target.get('epoch')) is not int
            or job.idempotency_key != prepare_key(run, expected)):
        o.fail('分批任务与原授权位置不一致，原页面未改变')
    return True


def queue_prepare(db, p, material, run, after=None):
    from . import material_ocr as o
    target = prepare_target(p, material, run)
    key = prepare_key(run, target)
    job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == key))
    if job:
        if job.kind != 'prepare_material_ocr' or job.data.get('target') != target or job.data.get('target_hash') != digest(target): o.fail()
    else:
        eligible = now()
        if after is not None: eligible = max(eligible, after + timedelta(microseconds=1))
        job = Job(workspace_id=p.workspace_id, kind='prepare_material_ocr', idempotency_key=key, available_at=eligible,
                  data={'actor_id':p.user_id, 'material_id':material.id, 'run_id':run.id,
                        'target':target, 'target_hash':digest(target)})
        db.add(job); db.flush()
    if after is not None and job.available_at <= after: o.fail('分批队列顺序无法核对，原页面保留')
    run.data = {**run.data, 'job_id': job.id}
    return job


def queue_unit(db, p, material, run, unit):
    """One durable unit dispatch per epoch; persisted raw is never dispatched again."""
    from . import material_ocr as o
    if not enabled(run) and 'recognition_engine' not in unit.data: o.fail()
    rows = o.checked_versions(db, p, run, unit)
    if unit.data.get('raw_id'):
        if not rows or rows[0].id != unit.data['raw_id']: o.fail()
        return None
    target = o._job_target(p, material, run, unit)
    key = f'ocr-unit:{unit.id}:{run.data["epoch"]}'
    job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == key))
    if job:
        if (job.kind != 'recognize_material_ocr_unit' or job.data.get('target') != target
                or job.data.get('target_hash') != digest(target) or job.status not in {'queued', 'running'}
                or unit.data.get('job_id') != job.id or unit.data.get('job_target_hash') != digest(target)):
            o.fail('原页面识别任务无法核对，请明确恢复，原稿保留')
        return job
    job = Job(workspace_id=p.workspace_id, kind='recognize_material_ocr_unit', idempotency_key=key, available_at=now(),
              data={'actor_id':p.user_id,'material_id':material.id,'run_id':run.id,'unit_id':unit.id,
                    'target':target,'target_hash':digest(target)})
    db.add(job); db.flush()
    unit.data = {**unit.data, 'job_id':job.id, 'job_target_hash':digest(target), 'error':None}
    unit.status = 'queued'; unit.version += 1; o.seal(unit)
    return job


def prepared_units(db, run):
    from . import material_ocr as o
    rows = list(db.scalars(select(Unit).where(Unit.run_id == run.id).order_by(Unit.unit_index)))
    if [u.unit_index for u in rows] != list(range(1, run.data['prepared'] + 1)): o.fail('已保存页面与准备进度不一致，原记录保留')
    for unit in rows:
        o.checked_head(unit)
        if unit.data['descriptor'] != run.data['coverage']['units'][unit.unit_index - 1]: o.fail()
    return rows


def resume(db, p, material, run):
    from . import material_ocr as o
    after = None
    for unit in prepared_units(db, run):
        o.checked_unit(db, p, material, run, unit.id)
        job = queue_unit(db, p, material, run, unit)
        if job: after = max(after, job.available_at) if after else job.available_at
    if enabled(run): return queue_prepare(db, p, material, run, after)
    return o._queue_prepare(db, p, material, run)


def needs_preparation(run):
    return enabled(run) and run.data['prepared'] < run.data['coverage']['unit_count']


def process_batch(db, p, job, material, run):
    from . import material_ocr as o, material_ocr_runtime as runtime
    validate_job(p, material, run, job)
    cursor = job.data['target']['cursor']; total = run.data['coverage']['unit_count']
    prepared_units(db, run)
    if cursor > run.data['prepared']: o.fail('未来准备位置未获授权，原页面未改变')
    if cursor < run.data['prepared']:
        unit = db.scalar(select(Unit).where(Unit.run_id == run.id, Unit.unit_index == cursor + 1))
        o.checked_unit(db, p, material, run, unit.id); o.checked_versions(db, p, run, unit)
        return  # Page + dispatch + next cursor already committed, including a lost Job-tail response.
    if run.data.get('prepared_job_id') == job.id: return
    if job.data.get('recovery_only'): o.fail('后台准备已达到恢复上限，请核对已保存页面并明确恢复')
    installed = o.engine_status()
    if not installed['ready'] or installed['manifest_hash'] != run.data['engine']['manifest_hash']:
        o.fail(installed.get('reason') or '固定本地引擎无法核对')
    content = o.source(db, p, material, run)
    run.status = 'running'; run.version += 1; o.seal(run); db.commit()
    if cursor == total:
        material, run, active = o._job_scope(db, p, job)
        if not active: return
        o.current_actor(db, p)
        pending = db.scalar(select(func.count()).select_from(Unit).where(Unit.run_id == run.id, Unit.status.in_(['queued', 'running'])))
        run.version += 1; run.status = 'running' if pending else 'review'
        run.data = {**run.data, 'prepared_job_id':job.id}; o.seal(run); db.commit()
        return
    descriptor = run.data['coverage']['units'][cursor]
    guard = o.runtime_guard(db, p, job)
    if descriptor['kind'] == 'pdf_page':
        info = runtime.pdf_info(content, guard=guard)
        if info['page_count'] != total or info['source_sha256'] != material.sha256: o.fail()
        images = runtime.render_pdf(content, page=descriptor['page'], guard=guard)
        if not isinstance(images, list) or len(images) != 1 or images[0]['page'] != descriptor['page']: o.fail()
        image = images[0]
    else:
        with o.checked_zip(content) as archive: embedded = archive.read(descriptor['archive_name'])
        if hashlib.sha256(embedded).hexdigest() != descriptor['embedded_sha256']: o.fail()
        image = runtime.normalize_image(embedded, guard=guard)
    material, run, active = o._job_scope(db, p, job)
    if not active: return
    o.current_actor(db, p); o.source(db, p, material, run)
    # Another lease may already have atomically committed this exact page.
    if run.data['prepared'] != cursor:
        if run.data['prepared'] > cursor: return
        o.fail()
    png = image['png']
    with Image.open(io.BytesIO(png)) as decoded:
        if decoded.format != 'PNG' or list(decoded.size) != [image['width'],image['height']] or decoded.width * decoded.height > 24000000: o.fail()
        decoded.verify()
    if len(png) > 50 * 1024 * 1024: o.fail('单页原图超过50MB，本地处理已停止')
    evidence = o.save_immutable(db, p, png, 'image/png', {'modality':'document_ocr_page','source_material_id':material.id,'source_sha256':material.sha256})
    unit = Unit(id=uid(), workspace_id=p.workspace_id, material_id=material.id, run_id=run.id, unit_index=descriptor['index'],
                evidence_id=evidence.id, status='queued', version=1, data={'descriptor':copy.deepcopy(descriptor),
                'image_sha256':evidence.sha256,'width':image['width'],'height':image['height'],
                'render':{k:v for k,v in image.items() if k != 'png'},'raw_id':None,'draft_id':None,
                'confirmed_id':None,'last_hash':None,'error':None})
    from .material_ocr_recognition import initialize_unit
    initialize_unit(db, run, unit)
    db.add(unit); db.flush()
    unit_job = queue_unit(db, p, material, run, unit)
    run.version += 1
    run.data = {**run.data, 'prepared':descriptor['index'], 'prepared_job_id':job.id}
    if run.data['prepared'] < total: queue_prepare(db, p, material, run, unit_job.available_at)
    o.seal(run)
    db.commit()  # Existing worker lease + actor hooks fence this entire page/dispatch/cursor transaction.
