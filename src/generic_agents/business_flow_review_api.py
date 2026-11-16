"""Human incomplete drafts over immutable original paid flow answers.

Read/save/recover only. No generation, confirmation, adoption or execution.
The separate draft actor mutex precedes the original attempt actor mutex.
"""
import copy
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import business_flow_review_source as source
from . import business_flow_review_store as store
from . import report_scoped_design_consumer_v181 as attempts
from . import report_scoped_design_profiles_v182 as profiles
from .db import session
from .evidence_request_scope_v191 import catalog_read_scope
from .security import Principal, authenticated, require

PREFIX = '/api/setup/business-flow-reviews'
router = APIRouter(prefix=PREFIX)
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def query(request, keys):
    source.need(set(request.query_params) == set(keys)
        and all(len(request.query_params.getlist(key)) == 1 for key in keys),
        '查询条件不完整或重复，请保留原请求', 422)
    return [request.query_params[key] for key in keys]


def candidates(db, principal, producer_id):
    require(principal, 'admin', 'operator')
    attempts.route_identity(producer_id)
    # All review ownership/history is checked before original attempt locks.
    saved = {item['attempt_id']: item for item in store.list_saved(db, principal)}
    records, _, _ = attempts._inventory(db, principal)
    items = []
    for row in records.values():
        if row.producer_id != producer_id:
            continue
        prepared = row.data['preview']['preparation']
        family = profiles.selected(prepared)
        if not profiles.is_flow(family):
            continue
        extra = {'generation_profile': profiles.FLOW_REVISION, 'parent_attempt_id': row.data['preview']['body']['parent_attempt_id']} if family.id == profiles.FLOW_REVISION else {}
        items.append({'attempt_id': row.id, 'name': prepared['scope_binding']['name'], **extra,
            'status': row.status, 'saved_version': saved[row.id]['version'] if row.id in saved else 0})
    return {'schema': 'business-flow-review-candidates-v1', 'items': items,
        'producer_id': producer_id, **source.FALSE_FLAGS}


def detail(db, principal, attempt_id):
    # Lifetime starts inside the synchronous request worker. Copied thread or
    # asyncio contexts cannot reuse its pure directory checks. Every original
    # IAM/inventory/capture/diagnostic path below still runs on every GET.
    with catalog_read_scope(principal, attempt_id):
        return _detail(db, principal, attempt_id)


def _detail(db, principal, attempt_id):
    require(principal, 'admin', 'operator')
    attempts.route_identity(attempt_id)
    saved = store.get(db, principal, attempt_id)
    try:
        loaded = source.load(db, principal, attempt_id)
    except HTTPException as error:
        # The saved human work remains readable when historical files are
        # temporarily absent. This does not claim renewed source eligibility.
        if saved is None or error.status_code not in {404, 409}:
            raise
        return {**saved, 'name': '已保存的业务流程草稿', 'saved': True,
            'source_available': False, 'source_error': '原回答文件暂时无法核对，已保存稿件仍完整保留',
            'historical_source': True, 'current_source_checked': False, **source.FALSE_FLAGS}
    if saved is not None:
        source.need(saved['source_binding'] == loaded['source_binding']
            and store._same(saved['baseline_value'], loaded['baseline_value']),
            '历史来源与已保存稿件不同；两份记录均保留，未覆盖')
        value = copy.deepcopy(saved['value'])
        result = {**saved, 'diagnostics': source.diagnose(loaded, value)}
    else:
        value = copy.deepcopy(loaded['baseline_value'])
        result = {'schema': 'business-flow-review-unsaved-view-v1', 'attempt_id': attempt_id,
            'version': 0, 'value': value, 'note': '', 'baseline_value': copy.deepcopy(value),
            'source_binding': copy.deepcopy(loaded['source_binding']),
            'diagnostics': source.diagnose(loaded, value), 'history': [], 'status': 'incomplete'}
    extra = {'generation_review': copy.deepcopy(loaded['generation_review'])} if 'generation_review' in loaded else {}
    if 'generation_recovery' in loaded:
        extra['generation_recovery'] = copy.deepcopy(loaded['generation_recovery'])
    if '_causal_projection' in loaded:
        from .business_flow_causal_presentation_v190 import build_view
        extra['generation_causality'] = build_view(loaded['_causal_projection'], current_value=value)
    if '_nested_causal_projection' in loaded:
        from .business_flow_nested_causal_presentation_v193 import build_view
        extra['generation_causality'] = build_view(loaded['_nested_causal_projection'], current_value=value)
    return {**result, **extra, 'name': loaded['name'], 'saved': saved is not None,
        'source_available': True, 'source_error': None, 'historical_source': True,
        'current_source_checked': False, **source.FALSE_FLAGS}


def save(db, principal, attempt_id, raw, method, path):
    require(principal, 'admin', 'operator')
    attempts.route_identity(attempt_id)
    try:
        parsed = store.parse_wire(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '人工稿格式、原请求编号或版本不完整；原输入保留') from None
    # A repeated command is recovered before reading historical source files.
    original = store.replay(db, principal, attempt_id, raw, method=method, path=path)
    if original is not None:
        return original
    loaded = source.load(db, principal, attempt_id)
    diagnostics = source.diagnose(loaded, parsed['value'])
    source.need(diagnostics['available'] is True,
        '稿件或历史来源超出可安全保存范围；原输入保留', 422)
    return store.save(db, principal, attempt_id, raw, method=method, path=path,
        source_binding=loaded['source_binding'], baseline_value=loaded['baseline_value'],
        diagnostics=diagnostics)


@router.get('/candidates')
async def candidates_route(request: Request, principal: P, db: DB):
    producer_id, = query(request, ['producer_id'])
    return await run_in_threadpool(candidates, db, principal, producer_id)


@router.get('/requests/{request_id}')
async def original_route(request_id: str, request: Request, principal: P, db: DB):
    require(principal, 'admin', 'operator')
    attempts.route_identity(request_id)
    expected, = query(request, ['expected_sha256'])
    source.need(store.SHA.fullmatch(expected) is not None, '原请求摘要格式不正确', 422)
    return await run_in_threadpool(store.original_request, db, principal, request_id, expected)


@router.get('/attempts/{attempt_id}')
async def detail_route(attempt_id: str, principal: P, db: DB):
    return await run_in_threadpool(detail, db, principal, attempt_id)


@router.post('/attempts/{attempt_id}/save')
async def save_route(attempt_id: str, request: Request, principal: P, db: DB):
    attempts.route_identity(attempt_id)
    source.need(request.method == 'POST' and request.url.path == store.save_path(attempt_id),
        '保存路径与原请求不一致', 422)
    raw = await request.body()
    return await run_in_threadpool(save, db, principal, attempt_id, raw, request.method, request.url.path)
