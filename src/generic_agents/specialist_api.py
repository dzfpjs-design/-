"""Human-facing local pack installation, registration and persisted sample jobs."""
from typing import Literal
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import Field
from sqlalchemy import select
from fastapi.responses import Response
from urllib.parse import quote
from .setup_api import P, DB
from .contracts import Contract, Manifest, digest
from .models import SpecialistInstallation, SpecialistRun, Capability, Job, Membership
from .security import Principal
from .services import scoped, require, audit, check_version, register, validate_capability, publish
from .evidence import save_evidence, evidence_bytes
from .local_specialists import RECIPE, RECIPE_HASH, RECIPES, recipe_for, ready_install

router = APIRouter(prefix='/api/specialists')


def public_comparison():
    from .local_model_evaluations import read_asr_comparison
    try:
        return read_asr_comparison()
    except (ValueError, KeyError, OSError, TypeError) as error:
        raise HTTPException(503, '公开模型对照暂不可用，请重试；原件与结果需核对完整性') from error


@router.get('/evaluations/asr')
def asr_evaluation(p: P):
    return public_comparison()


@router.get('/evaluations/asr/export')
def asr_evaluation_export(p: P):
    from .local_model_evaluations import export_markdown
    import hashlib
    content = export_markdown(public_comparison())
    return Response(content, media_type='text/markdown; charset=utf-8', headers={
        'Content-Disposition': 'attachment; filename="local-asr-comparison.md"',
        'X-Content-SHA256': hashlib.sha256(content).hexdigest(), 'X-Content-Type-Options': 'nosniff'})


@router.get('/evaluations/asr/audio/{identity}')
def asr_evaluation_audio(identity: str, p: P, db: DB, workspace_id: str | None = None):
    if workspace_id and workspace_id != p.workspace_id:
        if not db.scalar(select(Membership).where(Membership.user_id == p.user_id, Membership.workspace_id == workspace_id)):
            raise HTTPException(403, '无此工作区访问权限')
    from .local_model_evaluations import original_audio
    try:
        content, case = original_audio(identity)
    except LookupError as error:
        raise HTTPException(404, '不在公开录音清单内') from error
    except (ValueError, KeyError, OSError, TypeError) as error:
        raise HTTPException(503, '公开原录音暂不可用，原件需核对完整性') from error
    return Response(content, media_type='audio/wav', headers={'X-Content-SHA256': case['input_sha256'],
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-cache'})


def view(row):
    return {'id': row.id, 'version': row.version, 'status': row.status,
            'created_at': row.created_at.isoformat(), **row.data,
            **({'pack_id': row.pack_id} if isinstance(row, SpecialistInstallation) else
               {'installation_id': row.installation_id, 'evidence_id': row.evidence_id})}


@router.get('/catalog')
def catalog(p: P):
    from .config import settings
    from .local_audio_paths import audio_paths, vad_paths
    from .local_sound import runtime_available as sound_runtime_available
    return {'packs': [r for r in RECIPES.values() if not r.get('hidden_in_setup_until_paired_workflow')], 'runtime_installed': settings().specialist_python.is_file(),
            'audio_runtime_installed': audio_paths().python.is_file(), 'vad_runtime_installed': vad_paths().python.is_file(),
            'sound_runtime_installed': sound_runtime_available(), 'default_deployment': 'local', 'cloud_fallback': False}


@router.get('/installations')
def installations(p: P, db: DB):
    return [view(r) for r in db.scalars(select(SpecialistInstallation).where(
        SpecialistInstallation.workspace_id == p.workspace_id).order_by(SpecialistInstallation.created_at.desc()))]


class InstallRequest(Contract):
    pack_id: Literal['rtdetr-v2-r18', 'sam2-1-tiny', 'sensevoice-small-v1', 'silero-vad-onnx-v1', 'changer-r18-binary-v1', 'yamnet-mediapipe-v1']
    source: Literal['official', 'mirror', 'prepared_local'] = 'official'
    backend: Literal['cpu', 'mps'] = 'cpu'
    version: int | None = Field(default=None, ge=1)


@router.post('/installations', status_code=201)
def install(body: InstallRequest, p: P, db: DB):
    require(p, 'admin')
    if body.pack_id=='changer-r18-binary-v1':
        if body.source!='prepared_local' or body.backend!='cpu':raise HTTPException(422,'变化候选包只接受已核验的本地safetensors与CPU，不下载或转云')
    elif body.source=='prepared_local':raise HTTPException(422,'此模型不接受变化模型的本地准备来源')
    if body.pack_id=='sensevoice-small-v1' and (body.source!='official' or body.backend!='cpu'):raise HTTPException(422,'当前语音包仅核验了ModelScope官方固定文件与CPU，不切换来源或后端')
    if body.pack_id=='silero-vad-onnx-v1' and (body.source!='official' or body.backend!='cpu'):raise HTTPException(422,'VAD仅核验官方PyPI固定包与CPU，不切换镜像或后端')
    if body.pack_id=='yamnet-mediapipe-v1' and (body.source!='official' or body.backend!='cpu'):
        raise HTTPException(422,'环境声音包只接受已固定的Google官方来源与本地CPU')
    from .setup_api import draft
    draft(db, p, True)  # Serialize the first installation in this workspace.
    row = db.scalar(select(SpecialistInstallation).where(SpecialistInstallation.workspace_id == p.workspace_id,
        SpecialistInstallation.pack_id == body.pack_id).with_for_update())
    if row:
        if body.version is None:
            raise HTTPException(409, '已有安装记录，请读取后重试')
        check_version(row, body.version)
        if row.status in {'queued', 'installing', 'ready'}:
            raise HTTPException(409, '该模型正在安装或已经就绪，现有记录保留')
        generation = row.data.get('generation', 0) + 1
        attempts = row.data.get('attempts', []) + [{'generation': row.data.get('generation'),
            'source': row.data.get('source'), 'backend': row.data.get('backend'), 'status': row.status,
            'error': row.data.get('error')}]
        row.version += 1
    else:
        row = SpecialistInstallation(workspace_id=p.workspace_id, pack_id=body.pack_id)
        db.add(row); db.flush(); generation = 1; attempts = []
    row.status = 'queued'
    recipe_hash = digest(RECIPES[body.pack_id])
    row.data = {'recipe_hash': recipe_hash, 'source': body.source, 'backend': body.backend,
        'generation': generation, 'stage': 'requested', 'attempts': attempts[-20:],
        'deployment': 'local', 'error': None, 'domain_evaluation': 'pending'}
    job = Job(workspace_id=p.workspace_id, kind='install_specialist',
        idempotency_key=f'specialist-install:{row.id}:{generation}',
        data={'installation_id': row.id, 'generation': generation, 'actor_id': p.user_id})
    db.add(job); db.flush(); row.data = {**row.data, 'job_id': job.id}
    queued_proof={'source':body.source,'recipe_hash':recipe_hash}
    if body.pack_id=='yamnet-mediapipe-v1':
        queued_proof.update(installation_id=row.id,generation=generation,job_id=job.id,backend='cpu')
    audit(db, p, 'specialist.install.queued', row.id, queued_proof)
    db.commit(); return view(row)


class Version(Contract):
    version: int = Field(ge=1)


@router.post('/installations/{object_id}/cancel')
def cancel_install(object_id: str, body: Version, p: P, db: DB):
    require(p, 'admin')
    row = scoped(db, SpecialistInstallation, object_id, p, True); check_version(row, body.version)
    if row.status not in {'queued', 'installing'}:
        raise HTTPException(409, '安装已结束，记录保留')
    row.status = 'cancelled'; row.version += 1
    audit(db, p, 'specialist.install.cancelled', row.id, {})
    db.commit(); return view(row)


def capability_manifest(row):
    if row.pack_id=='yamnet-mediapipe-v1':
        raise HTTPException(422,'环境声音包必须使用声音能力契约，不能登记为图像分割能力')
    recipe = recipe_for(row)
    output_key = 'detection' if recipe == RECIPE else 'segmentation'
    return Manifest(kind='specialist_model', namespace='platform_local', name=recipe['pack_id'].replace('-', '_'),
        version=f'1.0.{row.data["generation"]}', owner='平台本地专业模型包',
        description=recipe['title'] + '。' + recipe['limitations'],
        allowed_agent_roles=['perception', 'data'],
        input_schema={'type': 'object', 'required': ['evidence_ids'], 'additionalProperties': False,
            'properties': {'evidence_ids': {'type': 'array', 'minItems': 1, 'maxItems': 1,
                'items': {'type': 'string'}}, 'attributes': {'type': 'object'}}},
        output_schema={'type': 'object', 'required': ['attributes', 'anomalies', output_key],
            'additionalProperties': False, 'properties': {'attributes': {'type': 'object'},
                'anomalies': {'type': 'array', 'maxItems': 0}, output_key: {'type': 'object'}}},
        execution={'adapter': 'local_specialist', 'installation_id': row.id, 'generation': row.data['generation'],
            'recipe_hash': digest(recipe), 'backend': row.data['backend'], 'side_effect': False},
        data_policy={'cloud_allowed': False, 'external_allowed': False, 'deployment': 'local'},
        specification={'pack_id': recipe['pack_id'], 'domain_evaluation': 'pending',
            'automatic_domain_use': False, 'license': recipe['license'], 'license_evidence': recipe['license_evidence']})


@router.post('/installations/{object_id}/register')
def register_install(object_id: str, body: Version, p: P, db: DB):
    require(p, 'admin')
    row = scoped(db, SpecialistInstallation, object_id, p, True); check_version(row, body.version)
    ready_install(db, p, row.id)
    if row.pack_id=='changer-r18-binary-v1':raise HTTPException(409,'请从已保存的双影像分析结果登记业务能力；安装检查不授予单图识别权限')
    if row.data.get('capability_id'):
        return view(row)
    if row.pack_id=='yamnet-mediapipe-v1':
        from .workflow_sound import publish_sound_capability
        cap=publish_sound_capability(db,p)
    elif row.pack_id=='silero-vad-onnx-v1':
        from .local_vad_install import publish_capability
        cap=publish_capability(db,p,row)
    elif row.pack_id=='sensevoice-small-v1':
        from .workflow_audio import publish_audio_capability
        cap=publish_audio_capability(db,p)
    else:
        cap = register(db, p, capability_manifest(row))
    if row.pack_id not in {'sensevoice-small-v1','silero-vad-onnx-v1','yamnet-mediapipe-v1'}:
        validate_capability(db, p, cap.id, cap.version)
        publish(db, p, cap.id, cap.version, '只登记通用候选能力；领域自动化评测尚未通过')
    row.data = {**row.data, 'capability_id': cap.id}; row.version += 1
    audit(db, p, 'specialist.registered', row.id, {'capability_id': cap.id, 'domain_evaluation': 'pending'})
    db.commit(); return view(row)


@router.get('/runs')
def runs(p: P, db: DB):
    return [view(r) for r in db.scalars(select(SpecialistRun).where(SpecialistRun.workspace_id == p.workspace_id)
        .order_by(SpecialistRun.created_at.desc()).limit(100))]


@router.get('/runs/{object_id}/original')
def original(object_id: str, p: P, db: DB, workspace_id: str | None = None):
    # Browser attachment navigation cannot attach X-Workspace-ID. Validate the explicit
    # workspace membership here; it cannot confer membership or use another user's session.
    if workspace_id and workspace_id != p.workspace_id:
        membership = db.scalar(select(Membership).where(Membership.user_id == p.user_id,
                                                       Membership.workspace_id == workspace_id))
        if not membership:
            raise HTTPException(403, '没有此工作区的访问权限')
        p = Principal(p.user_id, membership.workspace_id, membership.role, p.session_id)
    run = scoped(db, SpecialistRun, object_id, p)
    ev, content = evidence_bytes(db, p, run.evidence_id)
    if ev.sha256 != run.data['input_sha256']:
        raise HTTPException(409, '原图摘要与识别记录不一致')
    return Response(content, media_type=ev.media_type, headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(run.data['name']),
        'X-Content-SHA256': ev.sha256, 'X-Content-Type-Options': 'nosniff'})


@router.post('/installations/{object_id}/samples', status_code=201)
async def sample(object_id: str, p: P, db: DB, file: UploadFile = File(), threshold: float = .5):
    require(p, 'admin', 'operator')
    if not .01 <= threshold <= .99:
        raise HTTPException(422, '置信度门槛需在0.01到0.99之间')
    installation = ready_install(db, p, object_id)
    if installation.pack_id=='changer-r18-binary-v1':raise HTTPException(422,'变化候选包需要双影像入口，不接受单图样例')
    if installation.pack_id in {'sensevoice-small-v1','silero-vad-onnx-v1','yamnet-mediapipe-v1'}:raise HTTPException(422,'语音包请使用音频上传入口，不能作为图像模型调用')
    content = await file.read(20 * 1024 * 1024 + 1)
    media = file.content_type or ''
    if media not in {'image/jpeg', 'image/png'}:
        raise HTTPException(415, '请选择PNG或JPEG图片')
    ev = save_evidence(db, p, content, media)
    row = SpecialistRun(workspace_id=p.workspace_id, installation_id=object_id, evidence_id=ev.id,
        data={'name': (file.filename or '样例图片').replace('\\', '/').split('/')[-1][:250],
            'input_sha256': ev.sha256, 'media_type': media, 'recipe_hash': installation.data['recipe_hash'], 'pack_id': installation.pack_id, 'generation': 1,
            'threshold': threshold, 'backend': installation.data['backend'], 'deployment': 'local', 'error': None})
    db.add(row); db.flush()
    job = Job(workspace_id=p.workspace_id, kind='run_specialist', idempotency_key='specialist-run:' + row.id,
        data={'run_id': row.id, 'generation': 1, 'actor_id': p.user_id})
    db.add(job); db.flush(); row.data = {**row.data, 'job_id': job.id}
    audit(db, p, 'specialist.sample.queued', row.id, {'cloud_calls': 0})
    db.commit(); return view(row)


@router.post('/runs/{object_id}/cancel')
def cancel_run(object_id: str, body: Version, p: P, db: DB):
    require(p, 'admin', 'operator')
    row = scoped(db, SpecialistRun, object_id, p, True); check_version(row, body.version)
    if row.status not in {'queued', 'running'}:
        raise HTTPException(409, '样例处理已结束，记录保留')
    row.status = 'cancelled'; row.version += 1
    audit(db, p, 'specialist.sample.cancelled', row.id, {})
    db.commit(); return view(row)


@router.post('/runs/{object_id}/retry', status_code=201)
def retry_run(object_id: str, body: Version, p: P, db: DB):
    require(p, 'admin', 'operator')
    old = scoped(db, SpecialistRun, object_id, p, True); check_version(old, body.version)
    if old.status not in {'failed', 'cancelled'}:
        raise HTTPException(409, '原样例不是失败或取消状态')
    ready_install(db, p, old.installation_id); evidence_bytes(db, p, old.evidence_id)
    row = SpecialistRun(workspace_id=p.workspace_id, installation_id=old.installation_id, evidence_id=old.evidence_id,
        data={k: old.data[k] for k in ['name', 'input_sha256', 'recipe_hash', 'threshold', 'backend', 'deployment']})
    row.data = {**row.data, 'pack_id': old.data.get('pack_id', RECIPE['pack_id']), 'media_type': old.data.get('media_type','image/png'), 'generation': 1, 'retry_of': old.id, 'error': None}
    db.add(row); db.flush()
    job = Job(workspace_id=p.workspace_id, kind='run_specialist', idempotency_key='specialist-run:' + row.id,
        data={'run_id': row.id, 'generation': 1, 'actor_id': p.user_id})
    db.add(job); db.flush(); row.data = {**row.data, 'job_id': job.id}
    audit(db, p, 'specialist.sample.retry', row.id, {'previous': old.id})
    db.commit(); return view(row)


@router.get('/runs/{object_id}/masks/{mask_id}')
def run_mask(object_id: str, mask_id: str, p: P, db: DB):
    from .segmentation import mask_bytes
    row = scoped(db, SpecialistRun, object_id, p)
    if row.status != 'succeeded':
        raise HTTPException(409, '实例掩膜结果尚未保存')
    result = row.data.get('result', {})
    if digest(result) != row.data.get('result_hash'):
        raise HTTPException(409, '保存结果摘要不一致，请恢复记录')
    mask, content = mask_bytes(db, p, result, mask_id, row.evidence_id)
    return Response(content, media_type='image/png', headers={
        'X-Content-SHA256': mask.sha256, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
