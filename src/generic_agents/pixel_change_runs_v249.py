"""Durable pixel-only local comparisons; independent of geospatial/business grants."""
import copy
import hashlib
import json
from datetime import UTC
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, or_, and_, text
from .contracts import digest
from .models import PixelChangeRun, DomainInstance, InstanceRelease, Capability, SpecialistInstallation, Evidence, Audit, Job
from .services import scoped, require, check_version
from .relations import lock_current_actor
from .evidence import evidence_bytes, save_immutable
from .workflow_images import view as image_view
from .local_changer_install import installed, trace, PACK, RECIPE_HASH, PIPELINE_HASH, RECIPE, model_path
from .pixel_change_contracts_v249 import Create, Command
from . import pixel_change_codec_v249 as codec

KIND = 'compare_pixel_change_v249'
CREATED = 'pixel_change.created_v249'
FINISHED = 'pixel_change.attempt_finished_v249'
COMMAND = 'pixel_change.command_v249'
STATES = {'queued', 'running', 'succeeded', 'failed', 'cancelled'}
INTEGRITY = '无法核对原始图片、模型和分析记录，请检查相关资料；原记录保留'


def need(ok, message=INTEGRITY, status=409):
    if not ok:
        raise HTTPException(status, message)


def internal(db, cls, identity, p):
    row = db.scalar(select(cls).where(cls.id == identity, cls.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    need(row is not None)
    return row


def _audit(db, p, action, identity, data):
    row = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action, object_id=identity, data=copy.deepcopy(data))
    db.add(row); db.flush()
    return row.id


def _lock_nonce(db, p, request_id):
    number = int.from_bytes(hashlib.sha256(('pixel-change-v249\0' + p.workspace_id + '\0' + request_id).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': number})


def _images(db, p, instance_id, body):
    from .workflow_images import snapshots
    values = snapshots(db, p, instance_id, [body.before_image_id, body.after_image_id])
    before, after = values
    need(before['sha256'] == body.before_sha256 and after['sha256'] == body.after_sha256,
         '图片摘要与所选原图不一致，请重新核对')
    need(before['id'] != after['id'] and before['sha256'] != after['sha256'], '请选择两张不同的原图', 422)
    dimensions = []
    for item in values:
        row, raw = evidence_bytes(db, p, item['id'])
        need(row.media_type == 'image/png', '像素比较只接受RGB PNG原图', 422)
        proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'workflow.image_saved', Audit.object_id == row.id).limit(2)))
        need(len(proofs) == 1 and proofs[0].actor_id == row.data.get('uploaded_by')
             and digest(proofs[0].data) == digest({'instance': instance_id, 'sha256': row.sha256, 'cloud_calls': 0}),
             '无法核对图片的原上传记录，请检查原图资料')
        try:
            dimensions.append(codec.png_metadata(raw))
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from None
        need(dimensions[-1] == (item['width'], item['height']))
        need(len(raw) == item['size_bytes'])
    need(dimensions[0] == dimensions[1], '两张原图像素尺寸不同；不会自动配准或缩放', 422)
    return {'before': before, 'after': after}


def _model(db, p, identity, *, lock=False, physical=True):
    query = select(SpecialistInstallation).where(SpecialistInstallation.id == identity,
        SpecialistInstallation.workspace_id == p.workspace_id)
    if lock:
        query = query.with_for_update(read=True)
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None, '本地变化模型安装记录不存在', 404)
    need(type(row.data.get('generation')) is int and row.data['generation'] >= 1
         and type(row.data.get('smoke')) is dict, '本地模型安装资料不完整，请检查安装记录')
    need(row.pack_id == PACK and row.status == 'ready' and row.data.get('source') == 'prepared_local'
         and row.data.get('backend') == 'cpu' and row.data.get('recipe_hash') == RECIPE_HASH
         and row.data.get('pipeline_hash') == PIPELINE_HASH and row.data.get('smoke', {}).get('inference_performed') is True,
         '请先完成本地变化模型安装与加载检查')
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'changer.install.ready', Audit.object_id == row.id,
        Audit.data['generation'].as_integer() == row.data.get('generation')).limit(2)))
    need(len(proofs) == 1 and digest(proofs[0].data) == digest({'recipe_hash': RECIPE_HASH,
         'generation': row.data['generation'], 'engineering_probe_only': True}), '本地模型安装完成记录无法核对')
    job = internal(db, Job, row.data.get('job_id'), p)
    need(job.kind == 'install_specialist' and job.idempotency_key == f'specialist-install:{row.id}:{row.data["generation"]}'
         and job.data.get('installation_id') == row.id and type(job.data.get('generation')) is int
         and job.data['generation'] == row.data['generation'] and job.data.get('actor_id') == proofs[0].actor_id,
         '本地模型安装任务与完成记录无法核对')
    if physical:
        installed(db, p, identity)
    return row


def _release_pin(db, p, release):
    package = internal(db, Capability, release.package_id, p)
    need(type(release.data.get('snapshot')) is dict and type(package.data.get('specification')) is dict)
    need(digest(release.data['snapshot']) == release.configuration_hash and digest(package.data) == package.content_hash
         and package.namespace == internal(db, DomainInstance, release.instance_id, p).namespace
         and package.data.get('specification', {}).get('configuration_hash') == release.configuration_hash)
    return {'id': release.id, 'configuration_hash': release.configuration_hash,
            'package_id': package.id, 'package_hash': package.content_hash}


def _origin(row):
    data = row.data
    return {'schema': 'pixel-change-origin-v249', 'run_id': row.id, 'workspace_id': row.workspace_id,
            'instance_id': row.instance_id, 'release_id': row.release_id, 'owner_id': row.owner_id,
            'request_id': row.creation_request_id, 'body_hash': data['body_hash'],
            'sources_hash': data['sources_hash'], 'model_trace_hash': data['model_trace_hash'],
            'release_pin': data['release_pin'], 'runtime_hash': data['runtime_hash']}


def _job(db, p, row, attempt):
    job = internal(db, Job, attempt['job_id'], p)
    expected = {'run_id': row.id, 'generation': attempt['generation'], 'actor_id': row.owner_id,
                'body_hash': row.data['body_hash'], 'runtime_hash': row.data['runtime_hash']}
    need(job.kind == KIND and job.idempotency_key == f'pixel-change:{row.id}:{attempt["generation"]}'
         and all(digest(job.data.get(k)) == digest(v) for k, v in expected.items()))
    return job


def checked(db, p, row, *, physical=True):
    """Historical cold read: no active-installation/current-release requirement."""
    data = row.data
    try:
        body = Create.model_validate(data['request'])
        need(digest(body.model_dump()) == data['body_hash'] and body.request_id == row.creation_request_id
             and body.release_id == row.release_id and digest(data['sources']) == data['sources_hash']
             and digest(data['model_trace']) == data['model_trace_hash'])
        need(data['schema'] == 'pixel-change-record-v249' and row.status in STATES)
        need(body.before_image_id == data['sources']['before']['id'] and body.after_image_id == data['sources']['after']['id']
             and body.before_sha256 == data['sources']['before']['sha256'] and body.after_sha256 == data['sources']['after']['sha256'])
        saved_model = data['model_trace']
        need(saved_model['installation_id'] == body.model_installation_id and saved_model['generation'] == body.model_generation
             and saved_model['recipe_hash'] == body.model_recipe_hash and saved_model['pack_id'] == PACK
             and saved_model['backend'] == 'cpu')
        release = internal(db, InstanceRelease, row.release_id, p)
        need(release.instance_id == row.instance_id and digest(_release_pin(db, p, release)) == digest(data['release_pin']))
        proof = internal(db, Audit, data['creation_audit_id'], p)
        need(proof.action == CREATED and proof.object_id == row.id and proof.actor_id == row.owner_id
             and digest(proof.data) == digest(_origin(row)))
        proofs = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == CREATED, Audit.object_id == row.id).limit(2)))
        need(proofs == [proof.id])
        attempts = data['attempts']
        need(type(data['generation']) is int and data['generation'] >= 1 and len(attempts) == data['generation'])
        for index, attempt in enumerate(attempts, 1):
            need(type(attempt) is dict and set(attempt) == {'generation', 'job_id', 'status', 'error', 'result_hash', 'audit_id'}
                 and type(attempt['generation']) is int and attempt['generation'] == index and attempt['status'] in STATES)
            _job(db, p, row, attempt)
            if attempt['status'] in {'succeeded', 'failed', 'cancelled'}:
                receipt = internal(db, Audit, attempt['audit_id'], p)
                expected = {'schema': 'pixel-change-attempt-v249', 'body_hash': data['body_hash'],
                    'generation': index, 'job_id': attempt['job_id'], 'status': attempt['status'],
                    'error': attempt['error'], 'result_hash': attempt['result_hash']}
                need(receipt.action == FINISHED and receipt.object_id == row.id and receipt.actor_id == row.owner_id
                     and digest(receipt.data) == digest(expected))
            else:
                need(index == len(attempts) and attempt['audit_id'] is None and attempt['result_hash'] is None)
        jobs = list(db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
            Job.data['run_id'].as_string() == row.id)))
        need(set(jobs) == {a['job_id'] for a in attempts})
        terminal_proofs = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == FINISHED, Audit.object_id == row.id)))
        need(set(terminal_proofs) == {a['audit_id'] for a in attempts if a['audit_id'] is not None})
        need(attempts[-1]['status'] == row.status and attempts[-1]['job_id'] == data['job_id']
             and digest(attempts[-1]['error']) == digest(data['error']))
        command_ids = []
        for command in data['commands']:
            proof = internal(db, Audit, command['audit_id'], p)
            expected = {k: command[k] for k in ('request_id', 'operation', 'body_hash', 'response')}
            need(command['operation'] in {'cancel', 'retry'} and command['request_id'] not in command_ids
                 and proof.action == COMMAND and proof.object_id == row.id and proof.actor_id == row.owner_id
                 and digest(proof.data) == digest(expected))
            command_ids.append(command['request_id'])
        command_proofs = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == COMMAND, Audit.object_id == row.id)))
        need(set(command_proofs) == {c['audit_id'] for c in data['commands']})
        if physical:
            current = _images(db, p, row.instance_id, body)
            need(digest(current) == digest(data['sources']))
        if row.status == 'succeeded':
            result = data['result']
            need(type(result) is dict and digest(result) == data['result_hash'] == attempts[-1]['result_hash']
                 and set(result['artifacts']) == {*codec.FILES, 'manifest'})
            if physical:
                for kind in result['artifacts']:
                    _artifact(db, p, row, kind)
        else:
            need(data['result'] is None and data['result_hash'] is None)
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(409, INTEGRITY) from None
        raise
    except (KeyError, TypeError, ValueError, ValidationError):
        raise HTTPException(409, INTEGRITY) from None
    return row


def _artifact(db, p, row, kind):
    ref = row.data['result']['artifacts'][kind]
    ev, raw = evidence_bytes(db, p, ref['evidence_id'])
    need(ev.sha256 == ref['sha256'] and ev.media_type == ref['media_type'] and ev.size_bytes == ref['size_bytes'] == len(raw)
         and digest(ev.data) == digest({'purpose': 'pixel_change_output_v249', 'run_id': row.id,
             'generation': row.data['generation'], 'kind': kind, 'body_hash': row.data['body_hash'],
             'coordinate_space': 'pixel', 'cloud_model_ids': [], 'uploaded_by': row.owner_id}))
    return ev, raw


def get(db, p, instance_id, run_id, *, physical=True):
    scoped(db, DomainInstance, instance_id, p)
    row = scoped(db, PixelChangeRun, run_id, p)
    need(row.instance_id == instance_id, '记录不存在或不在当前实例', 404)
    return checked(db, p, row, physical=physical)


def view(row):
    data = row.data
    result = data['result']
    visible = None
    if result is not None:
        visible = {k: result[k] for k in ('schema', 'coordinate_space', 'georeference_available', 'candidate_only',
            'cloud_calls', 'object_state_updated', 'cloud_shadow_screening', 'width', 'height', 'total_pixels',
            'common_valid_pixels', 'unknown_pixels', 'candidate_pixels', 'threshold_text', 'inference_seconds_text')}
        visible['result_hash'] = data['result_hash']
        visible['artifacts'] = [{'kind': kind, **ref} for kind, ref in result['artifacts'].items()]
    reasons = {'queued': '已保存，等待本地分析', 'running': '正在本地分析两张影像',
               'succeeded': '分析完成，请核对变化候选', 'cancelled': '已停止本次分析，原图和记录保留'}
    return {'schema': 'pixel-change-run-v249', 'id': row.id, 'version': row.version,
        'instance_id': row.instance_id, 'release_id': row.release_id, 'owner_id': row.owner_id,
        'request_id': row.creation_request_id, 'body_hash': data['body_hash'], 'request': copy.deepcopy(data['request']),
        'status': row.status, 'reason': reasons.get(row.status, data.get('error') or '分析失败，请检查本地模型和原图后重试'),
        'generation': data['generation'], 'job_id': data['job_id'], 'created_at': row.created_at.astimezone(UTC).isoformat(),
        'sources': copy.deepcopy(data['sources']), 'model_trace': copy.deepcopy(data['model_trace']),
        'attempts': [{k: a[k] for k in ('generation', 'job_id', 'status', 'error', 'result_hash')} for a in data['attempts']],
        'result': visible}


def _queue(db, p, row):
    generation = row.data['generation']
    job = Job(workspace_id=p.workspace_id, kind=KIND, idempotency_key=f'pixel-change:{row.id}:{generation}',
        data={'run_id': row.id, 'generation': generation, 'actor_id': row.owner_id,
              'body_hash': row.data['body_hash'], 'runtime_hash': row.data['runtime_hash']})
    db.add(job); db.flush()
    attempt = {'generation': generation, 'job_id': job.id, 'status': 'queued', 'error': None, 'result_hash': None, 'audit_id': None}
    row.data = {**row.data, 'job_id': job.id, 'attempts': [*row.data['attempts'], attempt]}


def create(db, p, instance_id, body):
    require(p, 'admin', 'operator'); _lock_nonce(db, p, body.request_id)
    rows = list(db.scalars(select(PixelChangeRun).where(PixelChangeRun.workspace_id == p.workspace_id,
        PixelChangeRun.creation_request_id == body.request_id).limit(2)))
    if rows:
        need(len(rows) == 1); row = rows[0]
        need(row.owner_id == p.user_id and row.instance_id == instance_id, '原请求不属于此账号或实例', 404)
        need(digest(body.model_dump()) == row.data.get('body_hash'), '同一请求号不能更改原图、模型、范围或阈值')
        return checked(db, p, row)
    lock_current_actor(db, p)
    from .instance_api import active
    instance, release = active(db, p, instance_id, True)
    check_version(instance, body.instance_version); need(release.id == body.release_id, '实例启用版本已变化，请重新核对')
    sources = _images(db, p, instance_id, body)
    model = _model(db, p, body.model_installation_id, lock=True)
    model_trace = trace(model)
    need(body.model_generation == model_trace['generation'] and body.model_recipe_hash == model_trace['recipe_hash'],
         '本地模型安装代次已变化，请重新核对')
    row = PixelChangeRun(workspace_id=p.workspace_id, instance_id=instance.id, release_id=release.id,
        owner_id=p.user_id, creation_request_id=body.request_id, status='queued', data={
            'schema': 'pixel-change-record-v249', 'request': body.model_dump(), 'body_hash': digest(body.model_dump()),
            'sources': sources, 'sources_hash': digest(sources), 'model_trace': model_trace, 'model_trace_hash': digest(model_trace),
            'release_pin': _release_pin(db, p, release), 'runtime_hash': codec.RUNTIME_HASH,
            'generation': 1, 'attempts': [], 'result': None, 'result_hash': None, 'error': None, 'commands': []})
    db.add(row); db.flush(); _queue(db, p, row)
    proof_id = _audit(db, p, CREATED, row.id, _origin(row))
    row.data = {**row.data, 'creation_audit_id': proof_id}
    db.flush(); return row


def options(db, p, instance_id):
    instance = scoped(db, DomainInstance, instance_id, p)
    reasons = []
    if p.role not in {'admin', 'operator'}:
        reasons.append('当前账号没有提交分析的权限')
    if instance.status != 'partially_active' or not instance.active_release_id:
        reasons.append('实例尚未启用或已暂停；历史分析仍可查看')
    images = [image_view(row) for row in db.scalars(select(Evidence).where(Evidence.workspace_id == p.workspace_id,
        Evidence.data['purpose'].as_string() == 'workflow_input_image', Evidence.data['instance_id'].as_string() == instance_id,
        Evidence.media_type == 'image/png').order_by(Evidence.created_at.desc(), Evidence.id.desc()).limit(100))]
    installations = []
    for row in db.scalars(select(SpecialistInstallation).where(SpecialistInstallation.workspace_id == p.workspace_id,
            SpecialistInstallation.pack_id == PACK).order_by(SpecialistInstallation.created_at.desc())):
        need(type(row.data.get('generation')) is int and row.data['generation'] >= 1,
             '本地模型安装代次记录损坏，请检查安装记录')
        ready, reason, model_trace = False, '', None
        try:
            current = _model(db, p, row.id)
            ready, model_trace = True, trace(current)
        except HTTPException as error:
            reason = str(error.detail)
        installations.append({'id': row.id, 'version': row.version, 'status': row.status,
            'generation': row.data['generation'], 'pack_id': PACK, 'recipe_hash': row.data.get('recipe_hash'),
            'ready': ready, 'reason': reason, 'trace': model_trace})
    if not any(m['ready'] for m in installations):
        reasons.append('请先安装并核验本地变化模型')
    return {'schema': 'pixel-change-options-v249', 'instance': {'id': instance.id, 'version': instance.version,
        'status': instance.status, 'release_id': instance.active_release_id}, 'can_create': not reasons, 'reasons': reasons,
        'model_recipe_hash': RECIPE_HASH, 'limits': {'max_pixels': 16000000, 'min_side': 32, 'tile_pixels': 512,
            'stride_pixels': 448, 'max_tiles': 128, 'default_threshold_text': '0.5'}, 'images': images, 'installations': installations}


def recover(db, p, instance_id, request_id, expected_body_hash):
    scoped(db, DomainInstance, instance_id, p)
    rows = list(db.scalars(select(PixelChangeRun).where(PixelChangeRun.workspace_id == p.workspace_id,
        PixelChangeRun.creation_request_id == request_id).limit(2)))
    result = {'schema': 'pixel-change-recovery-v249', 'found': bool(rows), 'request_id': request_id,
              'expected_body_hash': expected_body_hash, 'state': 'found' if rows else 'unknown', 'run': None}
    need(len(rows) <= 1)
    if rows:
        row = rows[0]
        need(row.instance_id == instance_id and row.owner_id == p.user_id, '原请求不属于此账号或实例', 404)
        checked(db, p, row)
        need(row.data['body_hash'] == expected_body_hash, '原请求正文摘要不同，请核对本机保存的原申请')
        result['run'] = view(row)
    return result


def listing(db, p, instance_id, limit=20, before_id=None):
    scoped(db, DomainInstance, instance_id, p)
    query = select(PixelChangeRun).where(PixelChangeRun.workspace_id == p.workspace_id, PixelChangeRun.instance_id == instance_id)
    if before_id:
        cursor = scoped(db, PixelChangeRun, before_id, p)
        need(cursor.instance_id == instance_id, '列表位置不属于此实例', 404)
        query = query.where(or_(PixelChangeRun.created_at < cursor.created_at,
            and_(PixelChangeRun.created_at == cursor.created_at, PixelChangeRun.id < cursor.id)))
    rows = list(db.scalars(query.order_by(PixelChangeRun.created_at.desc(), PixelChangeRun.id.desc()).limit(limit + 1)))
    return {'schema': 'pixel-change-list-v249', 'runs': [view(checked(db, p, row)) for row in rows[:limit]],
            'next_cursor': rows[limit - 1].id if len(rows) > limit else None}


def _finish(db, p, row, status, error=None, result=None):
    result_hash = digest(result) if result is not None else None
    attempt = {**row.data['attempts'][-1], 'status': status, 'error': error, 'result_hash': result_hash}
    proof = {'schema': 'pixel-change-attempt-v249', 'body_hash': row.data['body_hash'],
             **{k: attempt[k] for k in ('generation', 'job_id', 'status', 'error', 'result_hash')}}
    attempt['audit_id'] = _audit(db, p, FINISHED, row.id, proof)
    row.status = status; row.version += 1
    row.data = {**row.data, 'error': error, 'result': result, 'result_hash': result_hash,
                'attempts': [*row.data['attempts'][:-1], attempt]}


def command(db, p, instance_id, run_id, operation, body):
    require(p, 'admin', 'operator'); _lock_nonce(db, p, body.request_id)
    row = get(db, p, instance_id, run_id)
    need(row.owner_id == p.user_id, '只有原提交人可以停止或重试此分析', 403)
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == COMMAND,
        Audit.data['request_id'].as_string() == body.request_id).limit(2)))
    need(len(proofs) <= 1 and (not proofs or (proofs[0].object_id == row.id and proofs[0].actor_id == p.user_id)),
         '此操作号已经用于另一条原申请，请核对原记录')
    matching = [c for c in row.data['commands'] if c['request_id'] == body.request_id]
    if matching:
        need(len(matching) == 1); saved = matching[0]; proof = internal(db, Audit, saved['audit_id'], p)
        need(saved['operation'] == operation and saved['body_hash'] == digest(body.model_dump()), '同一操作号不能更改原申请')
        expected = {k: saved[k] for k in ('request_id', 'operation', 'body_hash', 'response')}
        need(proof.actor_id == p.user_id and proof.action == COMMAND and proof.object_id == row.id
             and digest(proof.data) == digest(expected))
        return copy.deepcopy(saved['response'])
    need(not proofs, '操作记录与原申请不一致，请核对后恢复，不能再次执行')
    lock_current_actor(db, p)
    scoped(db, DomainInstance, instance_id, p, True)
    row = scoped(db, PixelChangeRun, run_id, p, True); checked(db, p, row); check_version(row, body.version)
    if operation == 'cancel':
        need(row.status in {'queued', 'running'}, '本次分析已经结束，原结果保留')
        _finish(db, p, row, 'cancelled')
    else:
        need(operation == 'retry' and row.status in {'failed', 'cancelled'}, '只有失败或停止的分析可以重试')
        _live(db, p, row, physical=True)
        row.status = 'queued'; row.version += 1
        row.data = {**row.data, 'generation': row.data['generation'] + 1, 'result': None, 'result_hash': None, 'error': None}
        _queue(db, p, row)
    response = {'schema': 'pixel-change-command-result-v249', 'request_id': body.request_id, 'operation': operation,
                'body_hash': digest(body.model_dump()), 'run': view(row)}
    saved = {'request_id': body.request_id, 'operation': operation, 'body_hash': digest(body.model_dump()), 'response': response}
    proof_id = _audit(db, p, COMMAND, row.id, saved)
    row.data = {**row.data, 'commands': [*row.data['commands'], {**saved, 'audit_id': proof_id}]}
    db.flush(); return response


def _live(db, p, row, *, physical=False):
    instance = internal(db, DomainInstance, row.instance_id, p)
    need(instance.status == 'partially_active' and instance.active_release_id == row.release_id,
         '实例已暂停或启用版本变化，本次分析停止；原图和记录保留')
    need(row.data['runtime_hash'] == codec.RUNTIME_HASH, '像素比较处理版本已变化，请保留原记录并提交新申请')
    model = _model(db, p, row.data['request']['model_installation_id'], lock=True, physical=physical)
    need(digest(trace(model)) == row.data['model_trace_hash'], '原本地模型安装代次已变化，不能替换原模型')
    return model


def _locked(db, p, identity, generation, *, physical=False):
    lock_current_actor(db, p)
    original = scoped(db, PixelChangeRun, identity, p)
    scoped(db, DomainInstance, original.instance_id, p, True)
    row = scoped(db, PixelChangeRun, identity, p, True)
    need(row.owner_id == p.user_id and row.data['generation'] == generation and row.status in {'queued', 'running'},
         '本次分析已停止或已由新尝试替代；原图和记录保留')
    checked(db, p, row, physical=physical); _live(db, p, row, physical=physical)
    return row


def process(db, p, job):
    """A real leased setup Job. Every attempt invokes only the pinned local runner."""
    identity, generation = job.data['run_id'], job.data['generation']
    try:
        require(p, 'admin', 'operator')
        from .leases import ownership
        lease = ownership(db)
        need(lease.get('job_id') == job.id and job.kind == KIND, '分析只能由原后台任务执行')
        original = scoped(db, PixelChangeRun, identity, p)
        if original.data.get('generation') != generation:
            return {'status': 'cancelled', 'error': '本次分析已由新的尝试替代'}
        if original.status in {'succeeded', 'cancelled', 'failed'}:
            checked(db, p, original)
            return {'status': original.status, 'error': original.data.get('error')}
        need(job.attempts == 1, '上次分析处理中断；请核对原记录后明确重试，不会自动重复推理')
        row = _locked(db, p, identity, generation, physical=True)
        need(row.data['job_id'] == job.id)
        row.status = 'running'; row.version += 1
        row.data = {**row.data, 'attempts': [*row.data['attempts'][:-1], {**row.data['attempts'][-1], 'status': 'running'}]}
        before = evidence_bytes(db, p, row.data['sources']['before']['id'])[1]
        after = evidence_bytes(db, p, row.data['sources']['after']['id'])[1]
        request = copy.deepcopy(row.data['request']); db.commit()
        def guard():
            db.expire_all(); _locked(db, p, identity, generation); db.commit()
        native, files = codec.call(before, after, request['threshold_text'], weights=model_path(), guard=guard)
        row = _locked(db, p, identity, generation, physical=True)
        artifacts = {}
        for kind, (raw, media) in files.items():
            artifacts[kind] = _save_output(db, p, row, kind, raw, media)
        manifest = {'schema': 'pixel-change-manifest-v249', 'coordinate_space': 'pixel', 'georeference_available': False,
            'candidate_only': True, 'object_state_updated': False, 'cloud_calls': 0, 'runtime_hash': codec.RUNTIME_HASH,
            'run_id': row.id, 'generation': generation, 'request': request, 'body_hash': row.data['body_hash'],
            'sources': row.data['sources'], 'model_trace': row.data['model_trace'],
            'full_image_present_confirmed': True, 'cloud_shadow_screening': 'not_applied',
            'native_result': native, 'artifacts': artifacts}
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        artifacts['manifest'] = _save_output(db, p, row, 'manifest', raw, 'application/json')
        result = {'schema': 'pixel-change-result-v249', 'coordinate_space': 'pixel', 'georeference_available': False,
            'candidate_only': True, 'cloud_calls': 0, 'object_state_updated': False, 'cloud_shadow_screening': 'not_applied',
            **{k: native[k] for k in ('width', 'height', 'total_pixels', 'common_valid_pixels', 'unknown_pixels', 'candidate_pixels')},
            'threshold_text': request['threshold_text'], 'inference_seconds_text': str(native['inference_seconds']), 'artifacts': artifacts}
        _finish(db, p, row, 'succeeded', result=result); db.commit()
        return {'status': 'succeeded', 'error': None}
    except (HTTPException, ValueError, OSError) as error:
        db.rollback()
        reason = str(error.detail) if isinstance(error, HTTPException) else str(error)
        if isinstance(error, OSError):
            reason = '本地文件或模型运行环境不可用，请检查存储和依赖后重试；原图保留，不会转云'
        # This only records the original attempt failure. It neither authorizes
        # inference nor revives cancelled/replaced work after revocation.
        row = _failure_target(db, p, job)
        if row is not None and row.data.get('generation') == generation and row.status in {'queued', 'running'}:
            _finish(db, p, row, 'failed', error=reason[:600]); db.commit()
        return {'status': 'cancelled' if row is not None and row.status == 'cancelled' else 'failed', 'error': reason[:600]}


def _save_output(db, p, row, kind, raw, media):
    ev = save_immutable(db, p, raw, media, {'purpose': 'pixel_change_output_v249', 'run_id': row.id,
        'generation': row.data['generation'], 'kind': kind, 'body_hash': row.data['body_hash'], 'coordinate_space': 'pixel'})
    saved, readback = evidence_bytes(db, p, ev.id)
    need(saved.size_bytes == len(raw) and readback == raw, '分析文件保存后核对失败，原图保留，请检查存储后重试')
    return {'evidence_id': ev.id, 'sha256': ev.sha256, 'size_bytes': ev.size_bytes, 'media_type': ev.media_type}


def fail(db, p, job, reason):
    """Record the original leased Job failure only; never resume inference."""
    row = _failure_target(db, p, job)
    if row is not None and row.data.get('generation') == job.data.get('generation') and row.status in {'queued', 'running'}:
        _finish(db, p, row, 'failed', error=reason[:600])


def _failure_target(db, p, job):
    """A damaged Job may fail itself, never a different run named in its payload."""
    row = db.scalar(select(PixelChangeRun).where(PixelChangeRun.workspace_id == p.workspace_id,
        PixelChangeRun.id == job.data.get('run_id')).with_for_update().execution_options(populate_existing=True))
    if row is None:
        return None
    try:
        from .leases import ownership
        need(ownership(db).get('job_id') == job.id and job.workspace_id == p.workspace_id
             and job.kind == KIND and row.owner_id == p.user_id == job.data.get('actor_id')
             and type(job.data.get('generation')) is int and row.data['generation'] == job.data['generation']
             and row.data['job_id'] == job.id and row.data['attempts'][-1]['job_id'] == job.id)
        need(_job(db, p, row, row.data['attempts'][-1]).id == job.id)
        proof = internal(db, Audit, row.data['creation_audit_id'], p)
        need(proof.action == CREATED and proof.object_id == row.id and proof.actor_id == row.owner_id
             and digest(proof.data) == digest(_origin(row)))
        return row
    except (HTTPException, KeyError, TypeError, ValueError):
        return None
