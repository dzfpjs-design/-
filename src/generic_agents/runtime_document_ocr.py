"""Independent, source-bound local OCR; native V205 records are read-only."""
import copy
import math
from pathlib import PurePath
import time
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import models as m, runtime_documents as native
from .runtime_document_models import RuntimeDocument as Document, RuntimeDocumentVersion as Version
from .runtime_document_ocr_models import RuntimeDocumentOcrRun as Run, RuntimeDocumentOcrPage as Page
from .runtime_document_ocr_models import RuntimeDocumentOcrResult as Result, RuntimeDocumentOcrSearch as Search
from .contracts import digest
from .db import engine
from .evidence import evidence_bytes, save_immutable
from .services import scoped, audit, check_version
from .security import Principal

COMMAND = 'runtime_document_ocr.command_v206'
RUN = 'runtime_document_ocr.run_created_v206'
PLAN = 'runtime_document_ocr.plan_v206'
PAGE = 'runtime_document_ocr.page_queued_v206'
START = 'runtime_document_ocr.result_started_v206'
FINISH = 'runtime_document_ocr.result_saved_v206'
IMAGE = 'runtime_document_ocr.image_saved_v206'
AUTH = 'runtime_document_ocr.job_authorized_v206'
SEARCH = 'runtime_document_ocr.search_created_v206'
HITS = 'runtime_document_ocr.search_result_v206'
PROFILE = '54e499dbd267250c20a8b6a0ccc1174fed792030283a3459b3d2c15084459eb8'
TERMINAL = {'text', 'no_text', 'failed', 'cancelled'}
KINDS = {'prepare_runtime_document_ocr', 'recognize_runtime_document_ocr_page', 'search_runtime_document_ocr'}
need, same, anchored = native.need, native.same, native.anchored
_catalog = None


class Cancelled(Exception): pass


def profile(*, cached=False):
    global _catalog
    if cached and _catalog and time.monotonic() - _catalog[0] < 15: return copy.deepcopy(_catalog[1])
    from .material_ocr_runtime import engine_status
    value = engine_status(engine_hash=PROFILE)
    if cached: _catalog = (time.monotonic(), copy.deepcopy(value))
    return value


def prior(db, p, body, scope, *, lock=True):
    identity = str(UUID(body['request_id']))
    if lock: db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest({'runtime_document_ocr': p.workspace_id, 'nonce': identity})[:15], 16)})
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND, m.Audit.object_id == identity)))
    need(len(rows) <= 1)
    if not rows: return None
    row = rows[0]
    need(row.actor_id == p.user_id and row.data['scope'] == scope and same(row.data['body'], body)
        and row.data['request_hash'] == digest(body), '原OCR请求不能更换内容、路径或发起人')
    return copy.deepcopy(row.data['result'])


def saved(db, p, body, scope, value):
    audit(db, p, COMMAND, body['request_id'], {'scope': scope, 'body': copy.deepcopy(body),
        'request_hash': digest(body), 'result': copy.deepcopy(value)})
    return value


def recover(db, p, identity, expected_hash=None):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND, m.Audit.object_id == identity)))
    need(len(rows) <= 1)
    if not rows: return {'schema': 'runtime-document-ocr-command-v206', 'found': False, 'body': None, 'result': None}
    row = rows[0]
    need(row.actor_id == p.user_id and row.data['request_hash'] == digest(row.data['body'])
        and (expected_hash is None or expected_hash == row.data['request_hash']))
    return {'schema': 'runtime-document-ocr-command-v206', 'found': True,
        'body': copy.deepcopy(row.data['body']), 'result': copy.deepcopy(row.data['result'])}


def queue(db, p, kind, subject, key):
    row = m.Job(workspace_id=p.workspace_id, kind=kind, idempotency_key=key,
        data={'actor_id': p.user_id, 'runtime_document_ocr_id': subject})
    db.add(row); db.flush(); return row


def context(db, p, identity):
    version = scoped(db, Version, identity, p); current = native.context(db, p, version.instance_id)
    runs = list(db.scalars(select(Run).where(Run.workspace_id == p.workspace_id,
        Run.document_version_id == identity).order_by(Run.created_at.desc(), Run.id.desc()).limit(100)))
    page_count = next((r.data['plan']['page_count'] for r in runs if r.data.get('plan')), None)
    allowed = current['can_upload'] and PurePath(version.name).suffix.lower() == '.pdf'
    return {'document_version_id': version.id, 'document_id': version.document_id, 'instance_id': version.instance_id,
        'name': version.name, 'upload_release_id': version.release_id, 'release_id': current['release_id'],
        'can_start': bool(allowed), 'reason': None if allowed else current['reason'] or '此入口仅处理原PDF页面',
        'page_count': page_count, 'runs_available': len(runs)}


def run_source(db, p, run, *, raw=True):
    origin = run.data['origin']
    need(anchored(db, p, RUN, run.id, origin).actor_id == run.actor_id
        and origin['run_id'] == run.id and origin['document_version_id'] == run.document_version_id
        and origin['document_id'] == run.document_id and origin['instance_id'] == run.instance_id
        and origin['upload_release_id'] == run.upload_release_id and origin['execution_release_id'] == run.execution_release_id
        and origin['actor_id'] == run.actor_id and origin['profile']['manifest_hash'] == PROFILE)
    command = scoped(db, m.Audit, origin['command_audit_id'], p)
    need(command.action == COMMAND and command.object_id == origin['request']['request_id']
        and command.actor_id == run.actor_id and command.data['scope'] == 'start:' + run.document_version_id
        and same(command.data['body'], origin['request']) and command.data['request_hash'] == digest(origin['request'])
        and command.data['request_hash'] == origin['request_hash']
        and command.data['result']['id'] == run.id and command.data['result']['document_version_id'] == run.document_version_id
        and command.data['result']['instance_id'] == run.instance_id
        and command.data['result']['profile']['manifest_hash'] == PROFILE)
    version = scoped(db, Version, run.document_version_id, p)
    need(version.document_id == run.document_id and version.instance_id == run.instance_id
        and version.release_id == run.upload_release_id and digest(version.data['origin']) == origin['version_origin_hash']
        and version.data['origin']['raw_sha256'] == origin['raw_sha256']
        and version.data['origin']['sequence'] == version.sequence
        and version.data['origin']['name'] == version.name == origin['name']
        and PurePath(version.name).suffix.lower() == '.pdf')
    if raw: return native.checked_version(db, p, version)
    need(anchored(db, p, native.UPLOAD, version.id, version.data['origin']).actor_id == version.actor_id)
    return version


def execution(db, p, run):
    native.current_actor(db, p)
    _, release = native.active(db, p, run.instance_id, run.execution_release_id)
    need(release.configuration_hash == run.data['origin']['execution_configuration_hash'], '原OCR执行版本依据已变化')


def start(db, p, identity, body):
    scope = 'start:' + identity
    old = prior(db, p, body, scope, lock=False)
    if old is not None: return old
    installed = profile(); need(installed['ready'] and installed['manifest_hash'] == body['profile_hash'] == PROFILE,
        installed.get('reason') or '固定本地OCR引擎尚未准备好')
    old = prior(db, p, body, scope)
    if old is not None: return old
    version = scoped(db, Version, identity, p)
    need(PurePath(version.name).suffix.lower() == '.pdf', '运行期OCR只处理原PDF，不转换配置资料')
    native.current_actor(db, p); _, release = native.active(db, p, version.instance_id, body['release_id'])
    native.checked_version(db, p, version)
    need(body['range_mode'] in {'all', 'missing_text', 'pages'} and type(body['pages']) is list
        and len(body['pages']) <= 100 and all(type(n) is int and 1 <= n <= 500 for n in body['pages'])
        and len(set(body['pages'])) == len(body['pages'])
        and (bool(body['pages']) if body['range_mode'] == 'pages' else not body['pages']), '请明确所需页范围，最多100页')
    run = Run(workspace_id=p.workspace_id, document_version_id=version.id, document_id=version.document_id,
        instance_id=version.instance_id, upload_release_id=version.release_id, execution_release_id=release.id,
        actor_id=p.user_id, data={})
    db.add(run); db.flush()
    job = queue(db, p, 'prepare_runtime_document_ocr', run.id, 'runtime-document-ocr-prepare:' + run.id)
    origin = {'run_id': run.id, 'document_version_id': version.id, 'document_id': version.document_id,
        'instance_id': version.instance_id, 'upload_release_id': version.release_id, 'execution_release_id': release.id,
        'execution_configuration_hash': release.configuration_hash, 'actor_id': p.user_id,
        'raw_sha256': version.data['origin']['raw_sha256'], 'version_origin_hash': digest(version.data['origin']),
        'name': version.name, 'profile': {'manifest_hash': PROFILE, 'name': installed['name']},
        'engine': copy.deepcopy(installed), 'range_mode': body['range_mode'], 'requested_pages': copy.deepcopy(body['pages']),
        'prepare_job_id': job.id, 'request_hash': digest(body), 'request': copy.deepcopy(body)}
    run.data = {'origin': origin, 'plan': None, 'error': None, 'previous_errors': []}
    # The independent command has its own immutable ID, not merely a self hash.
    value = run_view(db, p, run, initial=True)
    command = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=COMMAND, object_id=body['request_id'],
        data={'scope': scope, 'body': copy.deepcopy(body), 'request_hash': digest(body), 'result': copy.deepcopy(value)})
    db.add(command); db.flush()
    run.data = {**run.data, 'origin': {**origin, 'command_audit_id': command.id}}
    audit(db, p, RUN, run.id, run.data['origin'])
    return value


def page_origin(page): return {'page_id': page.id, 'run_id': page.run_id,
    'page_number': page.page_number, **page.data['origin']}


def checked_page(db, p, page, *, raw=True):
    run = scoped(db, Run, page.run_id, p); run_source(db, p, run, raw=raw)
    value = page_origin(page)
    need(anchored(db, p, PAGE, page.id, value).actor_id == page.data['origin']['actor_id'])
    checked_plan(db, p, run)
    need(page.page_number in run.data['plan']['selected_pages'] and value['plan_hash'] == digest(run.data['plan']))
    return run


def checked_plan(db, p, run):
    plan = run.data['plan']; need(type(plan) is dict and plan['run_id'] == run.id)
    anchored(db, p, PLAN, run.id, plan)
    numbers = list(db.scalars(select(Page.page_number).where(Page.workspace_id == p.workspace_id, Page.run_id == run.id).order_by(Page.page_number)))
    need(numbers == plan['selected_pages'])
    return plan


def auth(db, p, job, original_actor, run_id):
    identity = job.data.get('runtime_document_ocr_authorization_id')
    if identity is None: need(job.data.get('actor_id') == original_actor); return
    record = scoped(db, m.Audit, identity, p); value = record.data
    need(record.action == AUTH and record.object_id == job.id and record.actor_id == job.data['actor_id']
        and value['job_id'] == job.id and value['kind'] == job.kind and value['subject_id'] == job.data['runtime_document_ocr_id']
        and value['run_id'] == run_id and value['actor_id'] == job.data['actor_id']
        and value['body_hash'] == digest(value['body']))
    command = anchored(db, p, COMMAND, value['body']['request_id'], {'scope': value['scope'], 'body': value['body'],
        'request_hash': value['body_hash'], 'result': value['command_result']})
    allowed = {'retry_run:' + run_id}
    if job.kind == 'recognize_runtime_document_ocr_page': allowed.add('retry_page:' + value['subject_id'])
    need(value['scope'] in allowed and command.actor_id == record.actor_id)


def worker_job(db, p, job, kind, subject, original_actor, run_id):
    from .leases import ownership
    lease = ownership(db)
    need(job.kind == kind and job.workspace_id == p.workspace_id and job.status == 'running'
        and job.data.get('runtime_document_ocr_id') == subject and job.data.get('actor_id') == p.user_id
        and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token, '原OCR任务或租约无法核对')
    auth(db, p, job, original_actor, run_id)


def guard(p, job_id, token, subject, *, kind, run_id=None, search_id=None):
    from .leases import LeaseLost, ExecutionUnauthorized
    with Session(engine()) as db:
        db.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        if not db.scalar(select(m.Job.id).where(m.Job.id == job_id, m.Job.workspace_id == p.workspace_id,
            m.Job.status == 'running', m.Job.lease_token == token, m.Job.lease_until > func.clock_timestamp(),
            m.Job.kind == kind,
            m.Job.data['runtime_document_ocr_id'].as_string() == subject,
            m.Job.data['actor_id'].as_string() == p.user_id)):
            raise LeaseLost('原OCR后台任务租约已变化')
        if run_id:
            run = scoped(db, Run, run_id, p)
            if run.status == 'cancelled': raise Cancelled('原OCR批次已取消')
            instance_id, release_id = run.instance_id, run.execution_release_id
            expected = run.data['origin']['execution_configuration_hash']
        else:
            search = scoped(db, Search, search_id, p)
            instance_id, release_id, expected = search.instance_id, search.release_id, search.data['origin']['configuration_hash']
        instance = scoped(db, m.DomainInstance, instance_id, p); release = scoped(db, m.InstanceRelease, release_id, p)
        package = scoped(db, m.Capability, release.package_id, p)
        user = db.get(m.User, p.user_id)
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == p.workspace_id, m.Membership.user_id == p.user_id))
        if (not user or not user.active or not member or member.role not in {'admin', 'operator'} or member.role != p.role
                or instance.status != 'partially_active' or instance.active_release_id != release_id
                or release.instance_id != instance.id or release.configuration_hash != expected
                or digest(release.data['snapshot']) != expected or package.status != 'published' or package.kind != 'domain_package'
                or package.content_hash != package.validated_hash or digest(package.data) != package.content_hash):
            raise ExecutionUnauthorized('原OCR执行批准或实例发布版本已变化，未迁移旧批准')


def select_pages(examined, mode, requested):
    count = examined['page_count']
    if mode == 'all': numbers = list(range(1, count + 1))
    elif mode == 'pages':
        need(all(1 <= n <= count for n in requested), '指定页超出已核对原PDF页数')
        numbers = sorted(requested)
    else:
        numbers = [page['page_number'] for page in examined['pages']
            if not page['has_native_text'] or page['has_image'] or page['has_annotations']]
    need(len(numbers) <= 100, '本次范围超过100页，请明确选择较小页范围；未自动截断')
    return numbers


def process_prepare(db, p, job):
    run = scoped(db, Run, job.data['runtime_document_ocr_id'], p)
    need(run.data['origin']['prepare_job_id'] == job.id and job.idempotency_key == 'runtime-document-ocr-prepare:' + run.id)
    run_source(db, p, run, raw=False)
    worker_job(db, p, job, 'prepare_runtime_document_ocr', run.id, run.actor_id, run.id)
    if run.status == 'cancelled': return {'status': 'cancelled'}
    if run.data.get('plan'):
        checked_plan(db, p, run); refresh(db, p, run); return {'status': run.status}
    execution(db, p, run); raw = run_source(db, p, run)
    run = scoped(db, Run, run.id, p, True); run.status = 'preparing'; run.version += 1
    identity, token, job_id = run.id, job.lease_token, job.id
    db.commit()
    from .runtime_document_ocr_coverage import examine_pdf
    try: examined = examine_pdf(raw, guard=lambda: guard(p, job_id, token, identity, kind='prepare_runtime_document_ocr', run_id=identity))
    except Cancelled: return {'status': 'cancelled'}
    execution(db, p, run); run = scoped(db, Run, identity, p, True)
    if run.status == 'cancelled': return {'status': 'cancelled'}
    need(run.data.get('plan') is None)
    numbers = select_pages(examined, run.data['origin']['range_mode'], run.data['origin']['requested_pages'])
    plan = {'run_id': run.id, 'raw_sha256': run.data['origin']['raw_sha256'], 'page_count': examined['page_count'],
        'selected_pages': numbers, 'warnings': examined['warnings'], 'pages': examined['pages']}
    for number in numbers:
        page = Page(workspace_id=p.workspace_id, run_id=run.id, page_number=number, data={})
        db.add(page); db.flush()
        page_job = queue(db, p, 'recognize_runtime_document_ocr_page', page.id, 'runtime-document-ocr-page:' + page.id)
        page.data = {'origin': {'actor_id': p.user_id, 'job_id': page_job.id, 'plan_hash': digest(plan)},
            'latest_result_id': None, 'image': None, 'error': None}
        audit(db, p, PAGE, page.id, page_origin(page))
    run.data = {**run.data, 'plan': plan, 'error': None}; run.version += 1
    audit(db, p, PLAN, run.id, plan)
    refresh(db, p, run)
    return {'status': run.status}


def normalize_regions(values, width, height):
    need(type(width) is int and type(height) is int and width > 0 and height > 0
        and width * height <= 24000000, '原OCR页图尺寸不在明确范围内')
    need(type(values) is list and len(values) <= 2000, '单页OCR区域超过2000，未截断为成功')
    regions = []; characters = 0
    for index, value in enumerate(values, 1):
        need(type(value) is dict and type(value.get('text')) is str, '原OCR区域文字格式无效')
        box = value.get('box'); score = value.get('score')
        need(type(box) is list and len(box) == 4
            and all(type(x) in (int, float) and math.isfinite(x) for x in box), '原OCR区域框不是有限像素坐标')
        x, y, w, h = box
        need(x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= width + .01 and y + h <= height + .01,
            '原OCR区域超出精确页图范围')
        need(score is None or type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1,
            '引擎分数不在范围内；未当作业务可信度')
        characters += len(value['text']); need(characters <= 2000000, '本次OCR文字超过上限，未截断为成功')
        regions.append({'id': 'r' + str(index), 'text': value['text'], 'box': copy.deepcopy(box), 'score': score, 'unreviewed': True})
    return regions


def checked_image(db, p, page):
    image = page.data['image']; need(type(image) is dict)
    anchored(db, p, IMAGE, page.id, image)
    run = scoped(db, Run, page.run_id, p)
    evidence, raw = evidence_bytes(db, p, image['evidence_id'])
    need(image['page_number'] == page.page_number and image['render_receipt']['output_sha256'] == image['sha256']
        and image['render_receipt']['manifest_hash'] == PROFILE
        and image['render_receipt']['input_sha256'] == run.data['origin']['raw_sha256']
        and same(image['render_receipt']['manifest'], run.data['origin']['engine']['manifest'])
        and image['render_receipt']['summary_hash'] == run.data['origin']['engine']['summary_hash']
        and evidence.sha256 == image['sha256'] and evidence.size_bytes == len(raw) and evidence.media_type == 'image/png'
        and evidence.data.get('ocr_page_id') == page.id and evidence.data.get('ocr_run_id') == page.run_id)
    from PIL import Image as PillowImage
    import io
    with PillowImage.open(io.BytesIO(raw)) as actual:
        need(actual.format == 'PNG' and actual.size == (image['width'], image['height']))
        actual.verify()
    need(type(image['width']) is int and type(image['height']) is int and image['width'] > 0
        and image['height'] > 0 and image['width'] * image['height'] <= 24000000)
    return raw


def result_start(result):
    return {'result_id': result.id, 'page_id': result.page_id, 'job_id': result.job_id,
        'actor_id': result.actor_id, 'ordinal': result.ordinal, **result.data['pin']}


def checked_result(db, p, result, *, full=True):
    cache = db.info.get('runtime_document_checked') if full else None
    key = ('ocr-result', db.get_transaction(), result.id, result.version)
    old = cache.get(key) if cache is not None else None
    if old is not None and old['data'] is result.data: return old['checked']
    page = scoped(db, Page, result.page_id, p); run = checked_page(db, p, page, raw=full)
    need(result.job_id == page.data['origin']['job_id'] and result.data['pin']['run_id'] == run.id
        and result.data['pin']['origin_hash'] == digest(run.data['origin'])
        and result.data['pin']['page_hash'] == digest(page_origin(page))
        and anchored(db, p, START, result.id, result_start(result)).actor_id == result.actor_id)
    need(result.status in TERMINAL and result.data['outcome']['status'] == result.status)
    value = {'start': result_start(result), 'outcome': result.data['outcome'], 'event_id': result.data['event_id']}
    need(anchored(db, p, FINISH, result.id, value).actor_id == result.actor_id
        and result.data['result_hash'] == digest(result.data['outcome']))
    outcome = result.data['outcome']
    if outcome['image']:
        need(same(outcome['image'], page.data['image']))
        if full: checked_image(db, p, page)
    if result.status in {'text', 'no_text'}:
        engine_result = outcome['engine_result']; image = outcome['image']; receipt = engine_result['receipt']
        need(receipt['input_sha256'] == image['sha256'] and receipt['manifest_hash'] == PROFILE
            and receipt['manifest'] == run.data['origin']['engine']['manifest']
            and receipt['summary_hash'] == run.data['origin']['engine']['summary_hash']
            and receipt['result_sha256'] == digest(engine_result['regions'])
            and same(normalize_regions(engine_result['regions'], image['width'], image['height']), outcome['regions'])
            and bool(outcome['regions']) == (result.status == 'text'))
    else: need(outcome['regions'] == [])
    checked = (run, page, outcome)
    if cache is not None:
        cache[key] = {'data': result.data, 'result': result, 'checked': checked}
    return checked


def finish(db, p, run, page, result, status, regions, engine_result=None, error=None):
    need(result.status == 'processing' and status in TERMINAL)
    need(result.page_id == page.id and page.run_id == run.id and result.job_id == page.data['origin']['job_id']
        and result.data['pin']['origin_hash'] == digest(run.data['origin'])
        and result.data['pin']['page_hash'] == digest(page_origin(page))
        and anchored(db, p, START, result.id, result_start(result)).actor_id == result.actor_id)
    outcome = {'status': status, 'regions': copy.deepcopy(regions), 'engine_result': copy.deepcopy(engine_result),
        'image': copy.deepcopy(page.data['image']), 'error': error, 'unreviewed': True}
    result.status = status; result.version += 1
    result.data = {**result.data, 'outcome': outcome, 'result_hash': digest(outcome), 'finished_at': m.now().isoformat()}
    event = m.Event(workspace_id=p.workspace_id, kind='runtime_document.ocr_result',
        idempotency_key='runtime-document-ocr-result:' + result.id, data={'receipt': receipt(run, page, result)})
    db.add(event); db.flush(); result.data = {**result.data, 'event_id': event.id}
    audit(db, Principal(result.actor_id, p.workspace_id, 'viewer', 'ocr-result'), FINISH, result.id,
        {'start': result_start(result), 'outcome': outcome, 'event_id': event.id})
    page.status = status; page.version += 1; page.data = {**page.data, 'latest_result_id': result.id, 'error': error}
    if error:
        run.data = {**run.data, 'previous_errors': [*run.data.get('previous_errors', []), error][-100:]}
    refresh(db, p, run)


def receipt(run, page, result):
    return {'schema': 'runtime-document-ocr-receipt-v206', 'run_id': run.id, 'page_id': page.id,
        'page_number': page.page_number, 'result_id': result.id, 'document_version_id': run.document_version_id,
        'instance_id': run.instance_id, 'actor_id': result.actor_id, 'raw_sha256': run.data['origin']['raw_sha256'],
        'origin_hash': digest(run.data['origin']), 'plan_hash': digest(run.data['plan']), 'profile_hash': PROFILE,
        'page_hash': digest(page_origin(page)), 'result_hash': result.data['result_hash'],
        'image_sha256': (result.data['outcome']['image'] or {}).get('sha256'),
        'regions_hash': digest(result.data['outcome']['regions']), 'status': result.status, 'unreviewed': True}


def refresh(db, p, run):
    if run.status == 'cancelled': return
    pages = list(db.scalars(select(Page).where(Page.workspace_id == p.workspace_id, Page.run_id == run.id)))
    if not run.data.get('plan'): return
    statuses = [page.status for page in pages]
    if not statuses or all(value in {'text', 'no_text'} for value in statuses): run.status = 'completed'
    elif any(value in {'queued', 'running'} for value in statuses): run.status = 'running'
    elif all(value == 'failed' for value in statuses): run.status = 'failed'
    else: run.status = 'partial'


def process_page(db, p, job):
    page = scoped(db, Page, job.data['runtime_document_ocr_id'], p)
    run = checked_page(db, p, page, raw=False)
    need(page.data['origin']['job_id'] == job.id and job.idempotency_key == 'runtime-document-ocr-page:' + page.id)
    worker_job(db, p, job, 'recognize_runtime_document_ocr_page', page.id, page.data['origin']['actor_id'], run.id)
    if run.status == 'cancelled' or page.status == 'cancelled': return {'status': 'cancelled'}
    if page.status in {'text', 'no_text', 'failed'}:
        result = scoped(db, Result, page.data['latest_result_id'], p)
        need(result.page_id == page.id and result.status == page.status)
        checked_result(db, p, result); return {'status': page.status}
    execution(db, p, run); run_source(db, p, run)
    run = scoped(db, Run, run.id, p, True); page = scoped(db, Page, page.id, p, True)
    if run.status == 'cancelled': return {'status': 'cancelled'}
    old = scoped(db, Result, page.data['latest_result_id'], p, True) if page.data.get('latest_result_id') else None
    if old and old.status == 'processing': finish(db, p, run, page, old, 'failed', [], error='上次处理未保存结果，已保留中断记录并沿原任务继续')
    ordinal = (db.scalar(select(func.max(Result.ordinal)).where(Result.page_id == page.id)) or 0) + 1
    result = Result(workspace_id=p.workspace_id, page_id=page.id, job_id=job.id, actor_id=p.user_id, ordinal=ordinal,
        data={'pin': {'run_id': run.id, 'origin_hash': digest(run.data['origin']), 'page_hash': digest(page_origin(page)),
            'lease_token': job.lease_token, 'job_attempt': job.attempts}})
    db.add(result); db.flush(); audit(db, p, START, result.id, result_start(result))
    page.status = 'running'; page.version += 1; page.data = {**page.data, 'latest_result_id': result.id, 'error': None}
    run.status = 'running'; run.version += 1
    run_id, page_id, result_id, token, job_id = run.id, page.id, result.id, job.lease_token, job.id
    raw = run_source(db, p, run); png = checked_image(db, p, page) if page.data.get('image') else None
    page_number = page.page_number
    db.commit()
    check = lambda: guard(p, job_id, token, page_id, kind='recognize_runtime_document_ocr_page', run_id=run_id)
    from . import material_ocr_runtime as runtime
    from .leases import LeaseLost, ExecutionUnauthorized
    try:
        if png is None:
            rendered = runtime.render_pdf(raw, page=page_number, expected_manifest_hash=PROFILE, guard=check)[0]
            png = rendered['png']
            execution(db, p, run); run = scoped(db, Run, run_id, p, True); page = scoped(db, Page, page_id, p, True)
            if run.status == 'cancelled': return {'status': 'cancelled'}
            need(page.data['image'] is None and rendered.get('page') == page_number
                and rendered['receipt']['input_sha256'] == run.data['origin']['raw_sha256']
                and rendered['receipt']['manifest_hash'] == PROFILE)
            evidence = save_immutable(db, p, png, 'image/png', {'ocr_page_id': page.id, 'ocr_run_id': run.id, 'modality': 'image'})
            page.data = {**page.data, 'image': {'evidence_id': evidence.id, 'sha256': evidence.sha256,
                'width': rendered['width'], 'height': rendered['height'], 'page_number': page_number,
                'render_receipt': copy.deepcopy(rendered['receipt'])}}
            audit(db, p, IMAGE, page.id, page.data['image']); checked_image(db, p, page); db.commit()
        answer = runtime.recognize(png, expected_manifest_hash=PROFILE, guard=check)
        regions = normalize_regions(answer['regions'], page.data['image']['width'], page.data['image']['height'])
        rec = answer['receipt']
        need(rec['input_sha256'] == page.data['image']['sha256'] and rec['manifest_hash'] == PROFILE
            and rec['result_sha256'] == digest(answer['regions']) and rec['manifest'] == run.data['origin']['engine']['manifest']
            and rec['summary_hash'] == run.data['origin']['engine']['summary_hash'], '本地识别回执与原图或固定引擎不符')
        status, error = ('text' if regions else 'no_text'), None
    except Cancelled: return {'status': 'cancelled'}
    except (LeaseLost, ExecutionUnauthorized): raise
    except Exception as failure:
        status, regions, answer = 'failed', [], None
        error = str(failure.detail)[:1000] if isinstance(failure, HTTPException) else '本页渲染或识别未完成；原件及已保存页图保留，可明确重试'
    execution(db, p, run); run = scoped(db, Run, run_id, p, True); page = scoped(db, Page, page_id, p, True)
    result = scoped(db, Result, result_id, p, True)
    if run.status == 'cancelled' or result.status == 'cancelled': return {'status': 'cancelled'}
    need(page.data['latest_result_id'] == result.id and result.data['pin']['lease_token'] == token)
    if status == 'text':
        used = sum(sum(len(r['text']) for r in row.data.get('outcome', {}).get('regions', [])) for row in db.scalars(
            select(Result).join(Page, Page.id == Result.page_id).where(Result.workspace_id == p.workspace_id,
                Page.run_id == run.id, Result.status == 'text')))
        if used + sum(len(r['text']) for r in regions) > 2000000:
            status, regions, answer, error = 'failed', [], None, '本批OCR文字超过2m上限；未截断为完整结果，请缩小页范围'
    finish(db, p, run, page, result, status, regions, answer, error)
    return {'status': status, 'error': error}


def eligible(db, p, run):
    current = native.context(db, p, run.instance_id)
    return bool(current['can_upload'] and current['release_id'] == run.execution_release_id)


def projected_job_states(db, p, expected):
    """Read actual original Jobs only; never release, reset or reauthorize one."""
    identities = {identity for identity, _, _, _ in expected}
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.id.in_(identities))))
    by_id = {job.id: job for job in jobs}
    complete = len(by_id) == len(identities) and all(identity in by_id
        and by_id[identity].kind == kind and by_id[identity].idempotency_key == key
        and type(by_id[identity].data) is dict and by_id[identity].data.get('runtime_document_ocr_id') == subject
        for identity, kind, subject, key in expected)
    return complete, any(job.status == 'running' for job in jobs)


def run_view(db, p, run, *, initial=False):
    bad = False
    if not initial:
        try:
            run_source(db, p, run, raw=False)
            if run.data.get('plan'): checked_plan(db, p, run)
        except (HTTPException, KeyError, ValueError, TypeError, AttributeError): bad = True
    pages = list(db.scalars(select(Page).where(Page.workspace_id == p.workspace_id, Page.run_id == run.id)))
    counts = {key: 0 for key in ('queued', 'running', 'text', 'no_text', 'failed', 'cancelled')}
    for page in pages:
        status = page.status
        if status in {'text', 'no_text'}:
            try:
                result = scoped(db, Result, page.data['latest_result_id'], p)
                need(result.page_id == page.id and result.status == status)
                checked_result(db, p, result, full=False)
            except (HTTPException, KeyError, ValueError, TypeError, AttributeError): status = 'failed'
        counts[status if status in counts else 'failed'] += 1
    plan, origin = run.data.get('plan'), run.data['origin']; allowed = eligible(db, p, run)
    expected = [(origin['prepare_job_id'], 'prepare_runtime_document_ocr', run.id, 'runtime-document-ocr-prepare:' + run.id)]
    expected.extend((page.data['origin']['job_id'], 'recognize_runtime_document_ocr_page', page.id,
        'runtime-document-ocr-page:' + page.id) for page in pages)
    complete, running = projected_job_states(db, p, expected)
    total = len(plan['selected_pages']) if plan else 0
    return {'id': run.id, 'version': run.version, 'document_version_id': run.document_version_id,
        'instance_id': run.instance_id, 'name': origin['name'], 'profile': copy.deepcopy(origin['profile']),
        'range_mode': origin['range_mode'], 'requested_pages': origin['requested_pages'],
        'status': 'failed' if bad else run.status, 'page_count': plan['page_count'] if plan else None,
        'selected_count': total, 'counts': counts, 'error': '原OCR来源或页计划无法核对' if bad else run.data.get('error'),
        'previous_errors': run.data.get('previous_errors', []),
        'stopping': bool(run.status == 'cancelled' and running),
        'can_retry': bool(not bad and allowed and complete and not running and run.status in {'failed', 'partial', 'cancelled'}),
        'can_cancel': bool(not bad and allowed and run.status in {'queued', 'preparing', 'running'}),
        'coverage': {'unselected_count': plan['page_count'] - total if plan else None,
            'selected_processing_complete': bool(plan and not counts['queued'] and not counts['running']),
            'whole_document_text_verified': False, 'warnings': plan['warnings'] if plan else []},
        'created_at': run.created_at.isoformat()}


def result_view(db, p, result):
    from .runtime_document_ocr_receipts import delivery_view
    outcome = result.data.get('outcome', {}); image = outcome.get('image') or {}
    return {'id': result.id, 'ordinal': result.ordinal, 'status': result.status,
        'text_excerpt': '\n'.join(region['text'] for region in outcome.get('regions', []))[:1200],
        'regions_count': len(outcome.get('regions', [])), 'image_url': '/api/runtime-document-ocr/results/' + result.id + '/image' if image else None,
        'image_sha256': image.get('sha256'), 'image_width': image.get('width'), 'image_height': image.get('height'),
        'result_hash': result.data.get('result_hash'), 'delivery': delivery_view(db, p, result) if result.status in TERMINAL else None,
        'error': outcome.get('error'), 'created_at': result.created_at.isoformat()}


def page_view(db, p, page):
    run = scoped(db, Run, page.run_id, p)
    complete, running = projected_job_states(db, p, [(page.data['origin']['job_id'],
        'recognize_runtime_document_ocr_page', page.id, 'runtime-document-ocr-page:' + page.id)])
    rows = list(db.scalars(select(Result).where(Result.workspace_id == p.workspace_id, Result.page_id == page.id)
        .order_by(Result.ordinal.desc()).limit(101)))
    return {'id': page.id, 'version': page.version, 'run_id': page.run_id, 'page_number': page.page_number,
        'status': page.status, 'can_retry': bool(eligible(db, p, run) and complete and not running and page.status in {'failed', 'cancelled'}),
        'error': page.data.get('error'), 'latest_result_id': page.data.get('latest_result_id'),
        'results': [result_view(db, p, row) for row in rows[:100]], 'results_has_more': len(rows) > 100}


def reset_job(job, p):
    need(job.status != 'running', '原后台任务正在停止，请查回原结果后继续')
    job.status = 'queued'; job.attempts = 0; job.available_at = m.now(); job.lease_token = None; job.lease_until = None
    job.data = {**job.data, 'actor_id': p.user_id}


def authorize(db, p, jobs, run, body, scope, value):
    for job in jobs:
        row = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=AUTH, object_id=job.id,
            data={'job_id': job.id, 'kind': job.kind, 'subject_id': job.data['runtime_document_ocr_id'], 'run_id': run.id,
                'actor_id': p.user_id, 'scope': scope, 'body': copy.deepcopy(body), 'body_hash': digest(body),
                'command_result': copy.deepcopy(value)})
        db.add(row); db.flush(); job.data = {**job.data, 'runtime_document_ocr_authorization_id': row.id}


def control(db, p, identity, body, action, *, page_only=False):
    scope = ('retry_page:' if page_only else action + '_run:') + identity
    old = prior(db, p, body, scope)
    if old is not None: return old
    selected = scoped(db, Page, identity, p) if page_only else None
    run = scoped(db, Run, selected.run_id if selected else identity, p)
    pages = [selected] if selected else list(db.scalars(select(Page).where(Page.workspace_id == p.workspace_id, Page.run_id == run.id).order_by(Page.id)))
    ids = {page.data['origin']['job_id'] for page in pages}
    ids.add(run.data['origin']['prepare_job_id'])
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.id.in_(ids))
        .order_by(m.Job.id).with_for_update().execution_options(populate_existing=True)))
    need(len(jobs) == len(ids)); by_id = {job.id: job for job in jobs}
    execution(db, p, run); run = scoped(db, Run, run.id, p, True); run_source(db, p, run, raw=action != 'cancel')
    check_version(scoped(db, Page, identity, p, True) if selected else run, body['expected_version'])
    changed = []
    if action == 'cancel':
        need(run.status in {'queued', 'preparing', 'running', 'partial', 'failed'}, '已结束的成功结果不能取消覆盖')
        run.status = 'cancelled'; run.version += 1
        for original in pages:
            page = scoped(db, Page, original.id, p, True); checked_page(db, p, page, raw=False)
            if page.status in {'text', 'no_text'}: continue
            current = scoped(db, Result, page.data['latest_result_id'], p, True) if page.data.get('latest_result_id') else None
            if current and current.status == 'processing': finish(db, p, run, page, current, 'cancelled', [], error='用户已取消原处理，原件与已保存结果保留')
            else:
                page.status = 'cancelled'; page.version += 1
    else:
        need(run.status in {'failed', 'partial', 'cancelled'} or selected and selected.status in {'failed', 'cancelled'}, '没有需要明确恢复的失败或取消页')
        if run.data.get('plan') is None:
            job = by_id[run.data['origin']['prepare_job_id']]
            need(job.kind == 'prepare_runtime_document_ocr' and job.data['runtime_document_ocr_id'] == run.id)
            reset_job(job, p); changed.append(job); run.status = 'queued'
        else:
            checked_plan(db, p, run)
            for original in pages:
                page = scoped(db, Page, original.id, p, True); checked_page(db, p, page, raw=False)
                if page.status in {'text', 'no_text'}: continue
                job = by_id[page.data['origin']['job_id']]
                need(job.kind == 'recognize_runtime_document_ocr_page' and job.data['runtime_document_ocr_id'] == page.id)
                if page.data.get('latest_result_id'):
                    result = scoped(db, Result, page.data['latest_result_id'], p)
                    if result.status in TERMINAL: checked_result(db, p, result)
                reset_job(job, p); changed.append(job)
                page.status = 'queued'; page.version += 1; page.data = {**page.data, 'error': None}
            need(bool(changed), '所选页已有成功原结果，不重复识别')
            run.status = 'running'
        run.version += 1; run.data = {**run.data, 'error': None}
    audit(db, p, 'runtime_document_ocr.' + action + '_requested_v206', run.id, {'reason': body['reason']})
    value = page_view(db, p, scoped(db, Page, identity, p)) if selected else run_view(db, p, run)
    if changed: authorize(db, p, changed, run, body, scope, value)
    return saved(db, p, body, scope, value)


def citation(db, p, result, region_id, expected_hash):
    run, page, outcome = checked_result(db, p, result)
    version = scoped(db, Version, run.document_version_id, p)
    need(result.data['result_hash'] == expected_hash, '原OCR结果摘要不一致，未替换为当前结果')
    region = next((item for item in outcome['regions'] if item['id'] == region_id), None)
    if region is None: raise HTTPException(404, '原OCR结果中没有此区域')
    image = outcome['image']; query = urlencode({'expected_result_hash': expected_hash})
    return {'document_version_id': run.document_version_id, 'document_id': run.document_id, 'instance_id': run.instance_id,
        'source_name': version.data['origin']['name'], 'version_sequence': version.sequence,
        'run_id': run.id, 'page_id': page.id, 'page_number': page.page_number, 'result_id': result.id,
        'region_id': region_id, 'text': region['text'], 'box': copy.deepcopy(region['box']), 'score': region['score'],
        'profile': copy.deepcopy(run.data['origin']['profile']), 'raw_sha256': run.data['origin']['raw_sha256'],
        'image_sha256': image['sha256'], 'image_width': image['width'], 'image_height': image['height'],
        'image_url': '/api/runtime-document-ocr/results/' + result.id + '/image', 'result_hash': expected_hash,
        'unreviewed': True, 'citation_url': '/api/runtime-document-ocr/results/' + result.id + '/citations/' + region_id + '?' + query}


def search_origin(db, p, search):
    value = search.data['origin']
    need(anchored(db, p, SEARCH, search.id, value).actor_id == search.actor_id
        and value['search_id'] == search.id and value['instance_id'] == search.instance_id
        and value['release_id'] == search.release_id and value['actor_id'] == search.actor_id and value['keyword'] == search.keyword)
    command = scoped(db, m.Audit, value['command_audit_id'], p)
    need(command.action == COMMAND and command.actor_id == search.actor_id
        and command.object_id == value['request']['request_id'] and command.data['scope'] == 'search:' + search.instance_id
        and same(command.data['body'], value['request']) and command.data['request_hash'] == digest(value['request'])
        and command.data['result']['id'] == search.id and command.data['result']['instance_id'] == search.instance_id)
    return value


def start_search(db, p, identity, body):
    scope = 'search:' + identity
    old = prior(db, p, body, scope)
    if old is not None: return old
    native.current_actor(db, p); _, release = native.active(db, p, identity, body['release_id'])
    need(type(body['keyword']) is str and 0 < len(body['keyword'].strip()) <= 200)
    documents = list(db.scalars(select(Document).where(Document.workspace_id == p.workspace_id,
        Document.instance_id == identity).order_by(Document.created_at.desc(), Document.id.desc()).limit(101)))
    inventory = []; unavailable = 0; limited = len(documents) > 100; pending_plans = failed_plans = 0
    from .runtime_document_ocr_receipts import delivery_view
    with native.verification_scope(db):
        for document in documents[:100]:
            runs = list(db.scalars(select(Run).where(Run.workspace_id == p.workspace_id,
                Run.document_version_id == document.latest_version_id).order_by(Run.created_at.desc(), Run.id.desc()).limit(101)))
            if len(runs) > 100: limited = True
            seen = set()
            for run in runs[:100]:
                run_source(db, p, run, raw=False)
                if not run.data.get('plan'):
                    if run.status == 'failed': failed_plans += 1
                    else: pending_plans += 1
                    intent = run.data['origin']
                    numbers = intent['requested_pages'] if intent['range_mode'] == 'pages' else [None]
                    for number in numbers:
                        if number in seen: continue
                        seen.add(number)
                        if len(inventory) >= 500: limited = True; break
                        inventory.append({'document_id': document.id, 'document_version_id': document.latest_version_id,
                            'run_id': run.id, 'page_id': None, 'page_number': number, 'result_id': None,
                            'result_hash': None, 'eligible': False, 'reason': '页范围尚未冻结，不借旧结果'})
                        if number is not None: unavailable += 1
                    if limited and len(inventory) >= 500 or intent['range_mode'] != 'pages': break
                    continue
                checked_plan(db, p, run)
                for page in db.scalars(select(Page).where(Page.workspace_id == p.workspace_id, Page.run_id == run.id).order_by(Page.page_number)):
                    if page.page_number in seen: continue
                    seen.add(page.page_number)
                    if len(inventory) >= 500: limited = True; break
                    item = {'document_id': document.id, 'document_version_id': document.latest_version_id,
                        'run_id': run.id, 'page_id': page.id, 'page_number': page.page_number,
                        'result_id': page.data.get('latest_result_id'), 'result_hash': None, 'eligible': False}
                    if page.status == 'text' and item['result_id']:
                        result = scoped(db, Result, item['result_id'], p)
                        need(result.page_id == page.id)
                        checked_result(db, p, result)
                        if result.status == 'text' and delivery_view(db, p, result)['verified']:
                            item.update(result_hash=result.data['result_hash'], eligible=True)
                    if not item['eligible']: unavailable += 1
                    inventory.append(item)
                if len(inventory) >= 500: break
            if not seen: unavailable += 1
            if len(inventory) >= 500:
                limited = True
                break
    coverage = {'selected_documents': min(len(documents), 100), 'eligible_results': sum(item['eligible'] for item in inventory),
        'unavailable_results': unavailable, 'limited': limited, 'document_limit': 100, 'result_limit': 500,
        'region_limit': 2000, 'hit_limit': 100, 'excerpt_limit': 1200,
        'pending_plans': pending_plans, 'failed_plans': failed_plans,
        'notice': '存在页范围待核对/失败或有界选择；未借旧结果，未表示全文或识别准确性已确认。'
            if limited or pending_plans or failed_plans else None}
    search = Search(workspace_id=p.workspace_id, instance_id=identity, release_id=release.id, actor_id=p.user_id,
        keyword=body['keyword'], data={'error': None})
    db.add(search); db.flush()
    job = queue(db, p, 'search_runtime_document_ocr', search.id, 'runtime-document-ocr-search:' + search.id)
    origin = {'search_id': search.id, 'instance_id': identity, 'release_id': release.id, 'configuration_hash': release.configuration_hash,
        'actor_id': p.user_id, 'keyword': body['keyword'], 'inventory': inventory, 'coverage': coverage,
        'request': copy.deepcopy(body), 'job_id': job.id}
    search.data = {'origin': origin, 'error': None}
    value = search_view(db, p, search, initial=True)
    command = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=COMMAND, object_id=body['request_id'],
        data={'scope': scope, 'body': copy.deepcopy(body), 'request_hash': digest(body), 'result': copy.deepcopy(value)})
    db.add(command); db.flush(); search.data = {**search.data, 'origin': {**origin, 'command_audit_id': command.id}}
    audit(db, p, SEARCH, search.id, search.data['origin'])
    return value


def process_search(db, p, job):
    search = scoped(db, Search, job.data['runtime_document_ocr_id'], p); origin = search_origin(db, p, search)
    need(origin['job_id'] == job.id and job.idempotency_key == 'runtime-document-ocr-search:' + search.id)
    worker_job(db, p, job, 'search_runtime_document_ocr', search.id, search.actor_id, search.id)
    if search.status == 'completed': checked_search(db, p, search); return {'status': 'completed'}
    native.current_actor(db, p); native.active(db, p, search.instance_id, search.release_id)
    search.status = 'searching'; identity, token, job_id = search.id, job.lease_token, job.id; db.commit()
    hits = []; scanned_results = scanned_regions = scanned_characters = 0; limited = origin['coverage']['limited']; stop = False
    from .runtime_document_ocr_receipts import delivery_view
    with native.verification_scope(db):
        for item in origin['inventory']:
            if not item['eligible']: continue
            guard(p, job_id, token, identity, kind='search_runtime_document_ocr', search_id=identity)
            result = scoped(db, Result, item['result_id'], p); run, page, outcome = checked_result(db, p, result)
            need(run.instance_id == search.instance_id and run.document_version_id == item['document_version_id']
                and page.id == item['page_id'] and run.id == item['run_id'] and result.data['result_hash'] == item['result_hash']
                and result.status == 'text' and delivery_view(db, p, result)['verified'])
            scanned_results += 1
            for region in outcome['regions']:
                if scanned_regions >= 2000 or scanned_characters + len(region['text']) > 2000000:
                    limited = stop = True; break
                scanned_regions += 1; scanned_characters += len(region['text'])
                if search.keyword.casefold() not in region['text'].casefold(): continue
                full = citation(db, p, result, region['id'], result.data['result_hash'])
                hits.append(native.search_hit(full, search.keyword))
                if len(hits) >= 100: limited = stop = True; break
            if stop: break
    native.current_actor(db, p); native.active(db, p, search.instance_id, search.release_id)
    search = scoped(db, Search, identity, p, True); search_origin(db, p, search)
    coverage = {**origin['coverage'], 'limited': limited, 'scanned_results': scanned_results, 'scanned_regions': scanned_regions,
        'scanned_characters': scanned_characters, 'scan_character_limit': 2000000,
        'excerpted_hits': sum(hit['text_is_excerpt'] for hit in hits)}
    if limited: coverage['notice'] = '只显示有界扫描与前100处匹配；未确认整份资料完整或识别准确。'
    value = {'inventory_hash': digest(origin['inventory']), 'hits': hits, 'coverage': coverage}
    search.status = 'completed'; search.version += 1; search.data = {**search.data, 'result': value, 'error': None}
    audit(db, p, HITS, search.id, value)
    return {'status': 'completed'}


def checked_search(db, p, search):
    origin = search_origin(db, p, search); value = search.data['result']; anchored(db, p, HITS, search.id, value)
    need(value['inventory_hash'] == digest(origin['inventory']))
    from .runtime_document_ocr_receipts import delivery_view
    with native.verification_scope(db):
        for hit in value['hits']:
            item = next((item for item in origin['inventory'] if item['eligible'] and item['result_id'] == hit['result_id']
                and item['result_hash'] == hit['result_hash']), None)
            need(item is not None)
            result = scoped(db, Result, hit['result_id'], p)
            full = citation(db, p, result, hit['region_id'], hit['result_hash'])
            need(full['instance_id'] == search.instance_id and full['document_version_id'] == item['document_version_id']
                and same(native.search_hit(full, search.keyword), hit) and delivery_view(db, p, result)['verified'])
    return value


def search_view(db, p, search, *, initial=False):
    status, error, hits = search.status, search.data.get('error'), []
    coverage = search.data['origin']['coverage']
    if status == 'completed':
        try:
            value = checked_search(db, p, search); coverage, hits = value['coverage'], value['hits']
        except (HTTPException, KeyError, TypeError, ValueError, AttributeError, OSError):
            status, error = 'failed', '原OCR搜索库存、识别结果或接收依据无法核对；未借用当前结果'
    elif not initial: search_origin(db, p, search)
    return {'id': search.id, 'version': search.version, 'instance_id': search.instance_id,
        'keyword': search.keyword, 'status': status, 'error': error, 'coverage': coverage,
        'hits': copy.deepcopy(hits), 'created_at': search.created_at.isoformat()}


def recover_job(db, p, job, reason):
    try:
        need(isinstance(job, m.Job) and job.workspace_id == p.workspace_id and job.kind in KINDS
            and job.data.get('actor_id') == p.user_id)
        identity = job.data.get('runtime_document_ocr_id'); need(type(identity) is str and str(UUID(identity)) == identity)
        if job.kind == 'search_runtime_document_ocr':
            search = scoped(db, Search, identity, p, True); origin = search_origin(db, p, search)
            need(origin['job_id'] == job.id and job.idempotency_key == 'runtime-document-ocr-search:' + search.id)
            auth(db, p, job, search.actor_id, search.id)
        elif job.kind == 'prepare_runtime_document_ocr':
            run = scoped(db, Run, identity, p, True); run_source(db, p, run, raw=False)
            need(run.data['origin']['prepare_job_id'] == job.id and job.idempotency_key == 'runtime-document-ocr-prepare:' + run.id)
            auth(db, p, job, run.actor_id, run.id)
        else:
            page = scoped(db, Page, identity, p)
            run = scoped(db, Run, page.run_id, p, True)
            page = scoped(db, Page, identity, p, True); checked_page(db, p, page, raw=False)
            need(page.data['origin']['job_id'] == job.id and job.idempotency_key == 'runtime-document-ocr-page:' + page.id)
            auth(db, p, job, page.data['origin']['actor_id'], run.id)
            result = scoped(db, Result, page.data['latest_result_id'], p, True) if page.data.get('latest_result_id') else None
            if result and result.status == 'processing':
                need(result.page_id == page.id and result.job_id == job.id
                    and anchored(db, p, START, result.id, result_start(result)).actor_id == result.actor_id)
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError):
        if isinstance(job, m.Job) and job.workspace_id == p.workspace_id and db.get(m.User, p.user_id):
            audit(db, p, 'runtime_document_ocr.recovery_refused_v206', job.id,
                {'reason': '原OCR任务与目标来源无法核对；未变更目标记录'})
        return {'refused': True}
    error = '原OCR后台处理未完成或执行批准已变化；原件和已提交结果保留，可核对后明确继续'
    if job.kind == 'search_runtime_document_ocr':
        if search.status != 'completed':
            search.status = 'failed'; search.version += 1; search.data = {**search.data, 'error': error}
    elif job.kind == 'prepare_runtime_document_ocr':
        if run.status not in {'completed', 'cancelled'} and run.data.get('plan') is None:
            run.status = 'failed'; run.version += 1
            run.data = {**run.data, 'error': error, 'previous_errors': [*run.data.get('previous_errors', []), error][-100:]}
            audit(db, p, 'runtime_document_ocr.worker_interrupted_v206', run.id, {'error': error})
    elif run.status != 'cancelled' and page.status not in {'text', 'no_text', 'cancelled'}:
        run = scoped(db, Run, run.id, p, True)
        if result and result.status == 'processing': finish(db, p, run, page, result, 'failed', [], error=error)
        else:
            page.status = 'failed'; page.version += 1; page.data = {**page.data, 'error': error}
            run.data = {**run.data, 'previous_errors': [*run.data.get('previous_errors', []), error][-100:]}; refresh(db, p, run)
        audit(db, p, 'runtime_document_ocr.worker_interrupted_v206', page.id, {'error': error})
