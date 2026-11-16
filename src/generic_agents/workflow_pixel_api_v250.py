"""Pixel extension registry and task evidence; no standalone execution bypass."""
import copy
from typing import Annotated, Literal
from fastapi import APIRouter, Query
from .setup_api import P, DB
from .models import InstanceRelease, WorkflowPixelRun
from .services import scoped
from .workflow_pixel_contracts_v250 import Registration, Settlement
from . import workflow_pixel_registry_v250 as registry
from . import workflow_pixel_runs_v250 as runs
from .workflow_pixel_sources_v250 import steps, legacy_images_allowed
from .pixel_change_api_v249 import response

router = APIRouter(prefix='/api/workspace')
BASE = '/instances/{instance_id}/pixel-change'
HASH = Annotated[str, Query(pattern=r'^[a-f0-9]{64}$')]
NONCE = Annotated[str, Query(min_length=12, max_length=100)]


@router.get(BASE + '/runs/{run_id}/registration-options')
def registration_options(instance_id: str, run_id: str, p: P, db: DB):
    return registry.options(db, p, instance_id, run_id)


@router.post(BASE + '/runs/{run_id}/register-business')
def registration(instance_id: str, run_id: str, body: Registration, p: P, db: DB):
    value = registry.create(db, p, instance_id, run_id, body); db.commit(); return value


@router.get(BASE + '/registrations/requests')
def registration_recovery(instance_id: str, request_id: NONCE, expected_body_hash: HASH, p: P, db: DB):
    return registry.recover(db, p, instance_id, request_id, expected_body_hash)


@router.get(BASE + '/task-options')
def task_options(instance_id: str, release_id: str, business_id: str, p: P, db: DB):
    from .models import DomainInstance
    scoped(db, DomainInstance, instance_id, p)
    release = scoped(db, InstanceRelease, release_id, p)
    runs.need(release.instance_id == instance_id)
    from .contracts import digest
    from .models import Capability
    package = scoped(db, Capability, release.package_id, p)
    runs.need(digest(release.data['snapshot']) == release.configuration_hash
        and digest(package.data) == package.content_hash
        and package.data.get('specification', {}).get('configuration_hash') == release.configuration_hash)
    business = next((b for b in release.data['snapshot']['businesses'] if b['id'] == business_id), None)
    runs.need(business is not None)
    selected = steps(release, business)
    capabilities = []
    from .services import published
    for step in selected:
        pin = step['execution']; cap = published(db, p, pin['capability_id'])
        runs.need(cap.content_hash == pin['capability_hash'])
        ex = cap.data['execution']
        capabilities.append({'id': cap.id, 'hash': cap.content_hash, 'threshold_text': ex['threshold_text'],
            'model_trace': copy.deepcopy(ex['model_trace']), 'runtime_hash': ex['runtime_hash']})
    return {'schema': 'workflow-pixel-task-options-v250', 'instance_id': instance_id, 'release_id': release_id,
        'business_id': business_id, 'pixel_pair_required': bool(selected), 'provider': 'task_pixel_pair_refs',
        'single_entity': True, 'execution_mode': business['execution_mode'], 'capabilities': capabilities,
        'legacy_images_allowed': legacy_images_allowed(release, business),
        'legacy_image_provider': 'task_image_ids' if legacy_images_allowed(release, business) else None}


@router.get('/tasks/{task_id}/pixel-results/settlement-requests')
def settlement_recovery(task_id: str, request_id: NONCE, expected_body_hash: HASH, p: P, db: DB):
    return runs.recover_settlement(db, p, task_id, request_id, expected_body_hash)


@router.post('/tasks/{task_id}/pixel-results/{run_id}/settle')
def settle(task_id: str, run_id: str, body: Settlement, p: P, db: DB):
    value = runs.settle(db, p, task_id, run_id, body); db.commit(); return value


@router.get('/tasks/{task_id}/pixel-results')
def task_results(task_id: str, p: P, db: DB):
    from .workspace_api import saved_execution
    from .native_pixel_provider_v250 import result
    task, release, business = saved_execution(db, p, task_id)
    records = runs.runs_for_task(db, p, task)
    return {'schema': 'workflow-pixel-results-v250', 'task_id': task.id, 'instance_id': release.instance_id,
        'release_id': release.id, 'business_id': business['id'], 'pixel_pair_snapshot': copy.deepcopy(task.data.get('pixel_pair_snapshot')),
        'records': [result(db, p, run) for run in records]}


@router.get('/tasks/{task_id}/pixel-inputs/{side}')
def task_input(task_id: str, side: Literal['before', 'after'], p: P, db: DB):
    from .workspace_api import saved_execution
    from .workflow_pixel_sources_v250 import verify_task
    from .evidence import evidence_bytes
    task, _, _ = saved_execution(db, p, task_id)
    pair = verify_task(db, p, task)
    if pair is None:
        from fastapi import HTTPException
        raise HTTPException(404, '原任务没有明确选择像素比较原图')
    ev, raw = evidence_bytes(db, p, pair[side]['id'])
    value = response(ev, raw, 'workflow-pixel-' + side + '.png')
    value.headers['Content-Disposition'] = 'inline; filename="workflow-pixel-' + side + '.png"'
    return value


def task_run(db, p, task_id, run_id):
    from .workspace_api import saved_execution
    task, _, _ = saved_execution(db, p, task_id)
    row = scoped(db, WorkflowPixelRun, run_id, p)
    if row.task_id != task.id:
        from fastapi import HTTPException
        raise HTTPException(404, '此任务没有该像素分析结果')
    runs.checked_saved_run(db, p, row)
    return row


@router.get('/tasks/{task_id}/pixel-results/{run_id}/originals/{side}')
def original(task_id: str, run_id: str, side: Literal['before', 'after'], p: P, db: DB):
    from .evidence import evidence_bytes
    row = task_run(db, p, task_id, run_id)
    ev, raw = evidence_bytes(db, p, row.data['sources'][side]['id'])
    return response(ev, raw, 'workflow-pixel-' + side + '.png')


@router.get('/tasks/{task_id}/pixel-results/{run_id}/artifacts/{kind}')
def artifact(task_id: str, run_id: str, kind: str, p: P, db: DB):
    row = task_run(db, p, task_id, run_id)
    runs.need(row.status == 'succeeded')
    if kind not in {*runs.codec.FILES, 'manifest'}:
        from fastapi import HTTPException
        raise HTTPException(404, '没有此类像素分析产物')
    ev, raw = runs.artifact_bytes(db, p, row, kind)
    ext = '.json' if kind == 'manifest' else '.npy' if kind == 'probability_data' else '.png'
    return response(ev, raw, 'workflow-pixel-' + kind + ext)
