"""Explicit author self-review of a saved flow, never execution authority."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import business_flow_review_source as source
from . import business_flow_review_store as drafts
from . import business_flow_nested_review_v195 as nested
from .contracts import digest
from .db import session
from .security import Principal, authenticated, require

router = APIRouter(prefix=drafts.PREFIX)
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def storage():
    from . import business_flow_qualification_store
    return business_flow_qualification_store


def saved_for_review(db, p, attempt_id):
    drafts.identity(attempt_id); drafts._iam(db, p)
    # Writers/previews take X from the first inventory probe. Never upgrade a
    # shared review actor lock after acquiring original paid-source locks.
    drafts._inventory(db, p, write=True, target_attempt=attempt_id)
    drafts._iam(db, p, write=True)
    saved = drafts.get(db, p, attempt_id)
    source.need(saved is not None and saved['version'] >= 1,
        '请先保存修订稿，再核对这一保存版本', 422)
    return saved


def preview(db, p, attempt_id):
    require(p, 'admin', 'operator')
    saved = saved_for_review(db, p, attempt_id)
    snapshot = source.current_review_source(db, p, attempt_id, saved)
    latest = saved['history'][-1]
    flows = saved['value']['business']['flows']
    return {'schema': 'business-flow-author-review-preview-v1', 'attempt_id': attempt_id,
        'expected_version': saved['version'], 'save_request_id': latest['request_id'],
        'value_hash': digest(saved['value']), 'source_snapshot_hash': digest(snapshot),
        'business_name': saved['value']['business']['name'],
        'flows': [{'flow_id': flow['flow_id'], 'name': flow['name'],
            'trigger': flow['trigger'], 'node_count': len(flow['nodes']),
            'edge_count': len(flow['edges']), 'deliverables': flow['deliverables']}
            for flow in flows], 'review_scope': 'author_self_review',
        'current_source_checked': True, **source.FALSE_FLAGS}


def qualify(db, p, attempt_id, raw, method, path):
    require(p, 'admin', 'operator'); drafts.identity(attempt_id)
    module = storage()
    try:
        module.parse_wire(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '核对记录格式、版本、说明或明确勾选不完整；原稿保留') from None
    original = module.replay(db, p, attempt_id, raw, method=method, path=path)
    if original is not None:
        return original
    try:
        saved = saved_for_review(db, p, attempt_id)
        snapshot = source.current_review_source(db, p, attempt_id, saved)
    except HTTPException as error:
        # replay has already proved absence under the exclusive original nonce
        # and complete inventories. These read-only preconditions cannot have
        # created a qualification. Do not catch create/flush/commit errors here.
        if error.status_code not in {409, 422}:
            raise
        raise HTTPException(error.status_code, {
            'code': 'business_flow_author_review_source_not_applied', 'not_applied': True,
            'message': '本次核对记录未提交，已有稿件和原请求保留；请处理来源或流程待核项'}) from None
    return module.create(db, p, attempt_id, raw, method=method, path=path,
        saved=saved, source_snapshot=snapshot)


def nested_preview(db, p, attempt_id, review_mode):
    require(p, 'admin', 'operator')
    try:
        mode = nested.review_mode(review_mode)
    except ValueError:
        raise HTTPException(422, nested.ERROR) from None
    saved = saved_for_review(db, p, attempt_id)
    snapshot = source.current_nested_review_source(db, p, attempt_id, saved, review_mode=mode)
    try:
        nested.source_snapshot(snapshot, attempt_id, saved['source_binding'], digest(saved['value']), mode=mode)
        flows = nested.flow_summaries(saved['value'], saved['source_binding'], mode=mode)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, nested.ERROR) from None
    latest = saved['history'][-1]
    return {'schema': nested.PREVIEW, 'attempt_id': attempt_id, 'expected_version': saved['version'],
        'save_request_id': latest['request_id'], 'value_hash': digest(saved['value']),
        'source_snapshot_hash': digest(snapshot), 'business_name': saved['value']['business']['name'],
        'value_schema': nested.mode_fields(mode)['value_schema'], 'review_mode': mode,
        'simulation_only': mode == 'engineering_simulation', 'flows': flows,
        'action_count': sum(flow['action_count'] for flow in flows),
        'open_question_count': len(saved['value']['business']['questions']),
        'current_source_checked': True, **source.FALSE_FLAGS}


def qualify_nested(db, p, attempt_id, raw, method, path):
    require(p, 'admin', 'operator'); drafts.identity(attempt_id)
    module = storage()
    try:
        body = module.parse_nested_wire(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, nested.ERROR) from None
    original = module.replay_nested(db, p, attempt_id, raw, method=method, path=path)
    if original is not None:
        return original
    try:
        saved = saved_for_review(db, p, attempt_id)
        snapshot = source.current_nested_review_source(db, p, attempt_id, saved, review_mode=body['review_mode'])
    except HTTPException as error:
        # Only this no-write fresh precheck is proven unapplied under the nonce
        # and complete inventories. SQL, IAM and create/commit errors are not.
        if error.status_code not in {409, 422}:
            raise
        raise HTTPException(error.status_code, {'code': 'nested_review_source_not_applied',
            'not_applied': True, 'message': '来源或保存版本待核对；本次递归记录未提交，原稿保留'}) from None
    return module.create_nested(db, p, attempt_id, raw, method=method, path=path,
        saved=saved, source_snapshot=snapshot)


def list_reviews(db, p, attempt_id, *, nested_family):
    module = storage()
    schema = nested.RESULT if nested_family else module.RESULT
    return [value for value in module.list_records(db, p, attempt_id=attempt_id) if value['schema'] == schema]


@router.get('/attempts/{attempt_id}/author-review-preview')
async def preview_route(attempt_id: str, p: P, db: DB):
    return await run_in_threadpool(preview, db, p, attempt_id)


@router.post('/attempts/{attempt_id}/qualify')
async def qualify_route(attempt_id: str, request: Request, p: P, db: DB):
    drafts.identity(attempt_id)
    source.need(request.method == 'POST' and request.url.path == drafts.PREFIX + '/attempts/' + attempt_id + '/qualify',
        '核对记录路径与原请求不同', 422)
    raw = await request.body()
    return await run_in_threadpool(qualify, db, p, attempt_id, raw, request.method, request.url.path)


@router.get('/author-review-requests/{request_id}')
async def original_route(request_id: str, request: Request, p: P, db: DB):
    require(p, 'admin', 'operator'); drafts.identity(request_id)
    source.need(set(request.query_params) == {'expected_sha256'}
        and len(request.query_params.getlist('expected_sha256')) == 1,
        '原请求查询条件不完整或重复', 422)
    sha = request.query_params['expected_sha256']
    source.need(drafts.SHA.fullmatch(sha) is not None, '原请求摘要格式不正确', 422)
    return await run_in_threadpool(storage().original_request, db, p, request_id, sha)


@router.get('/attempts/{attempt_id}/author-reviews')
async def list_route(attempt_id: str, p: P, db: DB):
    require(p, 'admin', 'operator'); drafts.identity(attempt_id)
    return await run_in_threadpool(list_reviews, db, p, attempt_id, nested_family=False)


@router.get('/attempts/{attempt_id}/nested-review-preview')
async def nested_preview_route(attempt_id: str, request: Request, p: P, db: DB):
    source.need(set(request.query_params) == {'review_mode'}
        and len(request.query_params.getlist('review_mode')) == 1, '请明确唯一递归核对模式', 422)
    return await run_in_threadpool(nested_preview, db, p, attempt_id, request.query_params['review_mode'])


@router.post('/attempts/{attempt_id}/qualify-nested')
async def nested_qualify_route(attempt_id: str, request: Request, p: P, db: DB):
    drafts.identity(attempt_id)
    source.need(request.method == 'POST' and request.url.path == storage().nested_qualify_path(attempt_id),
        '递归核对路径与原请求不同', 422)
    raw = await request.body()
    return await run_in_threadpool(qualify_nested, db, p, attempt_id, raw, request.method, request.url.path)


@router.get('/nested-review-requests/{request_id}')
async def nested_original_route(request_id: str, request: Request, p: P, db: DB):
    require(p, 'admin', 'operator'); drafts.identity(request_id)
    source.need(set(request.query_params) == {'expected_sha256'}
        and len(request.query_params.getlist('expected_sha256')) == 1, '原请求查询条件不完整或重复', 422)
    sha = request.query_params['expected_sha256']
    source.need(drafts.SHA.fullmatch(sha) is not None, '原请求摘要格式不正确', 422)
    return await run_in_threadpool(storage().original_nested_request, db, p, request_id, sha)


@router.get('/attempts/{attempt_id}/nested-reviews')
async def nested_list_route(attempt_id: str, p: P, db: DB):
    require(p, 'admin', 'operator'); drafts.identity(attempt_id)
    return await run_in_threadpool(list_reviews, db, p, attempt_id, nested_family=True)
