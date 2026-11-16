"""Real source, independent allowance, native HTTP and original-journal seam.

One activity permits one new call. Neither old Map consent nor an unknown
dispatch permits a new reservation. The trusted allowance is operator config,
separate from untrusted document text and the user's exact API consent command.
"""
import copy
import json
import math
import os
from contextlib import nullcontext
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from fastapi import HTTPException

from .contracts import digest
from .config import settings
from .services import published, require
from .relations import lock_current_actor
from . import report_design_saved_source_v174 as source
from . import report_saved_map_design_preparation_v176 as preparation
from . import report_design_grouped_native_v176 as native
from . import report_evidence_native_bridge_v1 as bridge
from . import report_evidence_http_capture_v1 as capture
from . import report_evidence_journal_v2 as journal
from . import report_design_profiles_v178 as profiles

ALLOWANCE = 'report-saved-map-design-allowance-v176'
_KEYS = {'schema','activity_id','workspace_id','actor_id','producer_id','model_id',
    'revision','valid_until','maximum_cost_usd','input_usd_per_million',
    'output_usd_per_million','pricing_basis_hash'}


def need(ok, message='新方案的真实来源、独立费用许可或原始回答无法核对'):
    if not ok:
        raise HTTPException(409, message)


def _allowance(p, producer_id, revision, model_id):
    path = Path(settings().storage_dir).absolute() / 'saved-map-design-allowance-v176.json'
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            data = os.read(fd, 16385)
            need(0 < len(data) <= 16384)
        finally:
            os.close(fd)
        def unique(values):
            result = {}
            for key, value in values:
                need(key not in result)
                result[key] = value
            return result
        value = json.loads(data, object_pairs_hook=unique)
        need(type(value) is dict and set(value) == _KEYS and value['schema'] == ALLOWANCE)
        need(str(UUID(value['activity_id'])) == value['activity_id'])
        need(value['workspace_id'] == p.workspace_id and value['actor_id'] == p.user_id
            and value['producer_id'] == producer_id and value['model_id'] == model_id
            and type(value['revision']) is int and value['revision'] == revision)
        expiry = datetime.fromisoformat(value['valid_until'])
        need(expiry.tzinfo is not None and datetime.now(timezone.utc) < expiry)
        for key in ('maximum_cost_usd','input_usd_per_million','output_usd_per_million'):
            need(type(value[key]) in (int,float) and math.isfinite(value[key]) and value[key] > 0)
        need(value['maximum_cost_usd'] <= 3 and value['input_usd_per_million'] >= 2
            and value['output_usd_per_million'] >= 6)
        need(type(value['pricing_basis_hash']) is str and len(value['pricing_basis_hash']) == 64
            and set(value['pricing_basis_hash']) <= set('0123456789abcdef'))
        return value
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(503, '本次新方案尚无有效的独立费用许可；未发送模型请求') from None


def _indexed_allowance(p, producer_id, revision, model_id, activity_id):
    """Normal one-activity UI consent, bounded by server and global budgets.

    This quote is not consent. The consumer only starts after saving the exact
    authenticated StartIndexedBody, including its explicit consent and nonce.
    Old V176 activities continue to require their original separate allowance.
    """
    need(type(activity_id) is str and str(UUID(activity_id)) == activity_id)
    maximum = settings().report_design_maximum_cost_usd
    need(type(maximum) in (int, float) and math.isfinite(maximum) and 0 < maximum <= 3,
        '管理员配置的单次方案费用上限须大于0且不超过3美元')
    need(settings().cloud_daily_call_limit > 0 and settings().cloud_daily_token_limit > 0,
        '当前云模型日预算未启用；请由管理员配置后再生成方案')
    pricing = {'schema': 'report-indexed-design-pricing-v178',
        'model': 'qwen3.8-max-0902', 'input_usd_per_million': 2,
        'output_usd_per_million': 6, 'cache_discount_assumed': False,
        'source_url': 'https://www.alibabacloud.com/help/en/model-studio/model-pricing',
        'retrieved_at': '2026-10-23T11:34:23.846211+00:00',
        'html_sha256': '7ba3d888dbe414abb3923c7c34a270ac44897567ec14dc2278fe00b6fbff9a47'}
    return {'schema': 'report-indexed-design-activity-v178', 'activity_id': activity_id,
        'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'producer_id': producer_id, 'model_id': model_id, 'revision': revision,
        'maximum_cost_usd': maximum, 'input_usd_per_million': 2,
        'output_usd_per_million': 6, 'pricing_basis_hash': digest(pricing)}


def admit(db, p, producer_id, revision, model_id, *, lock, consumer=None, note='',
          generation_profile=profiles.LEGACY, activity_id=None):
    require(p, 'admin', 'operator')
    lock_current_actor(db, p)
    need(generation_profile in {profiles.LEGACY, profiles.INDEXED, profiles.COMPATIBLE, profiles.CATALOGUE})
    indexed = generation_profile in {profiles.INDEXED, profiles.COMPATIBLE, profiles.CATALOGUE}
    selected = profiles.modules(generation_profile) if indexed else None
    prep_module = selected.preparation if indexed else preparation
    native_module = selected.native if indexed else native
    allowance = (_indexed_allowance(p, producer_id, revision, model_id, activity_id)
        if indexed else _allowance(p, producer_id, revision, model_id))
    draft = source.pipeline.legacy.workspace_draft(db, p, lock=lock)
    need(draft.version == revision, '配置版本已变化，请核对本次新方案范围')
    cap = published(db, p, model_id, 'foundation_model')
    need(cap.data['execution'].get('secret_ref') == 'DASHSCOPE_API_KEY'
        and cap.data['data_policy'].get('cloud_allowed') is True)
    native_module.input_policy(cap.data['execution'])
    from .report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
    if not indexed:
        grant = digest(allowance)
        consumed = db.scalar(select(Consumer).where(Consumer.workspace_id == p.workspace_id,
            Consumer.grant_hash == grant))
        need(consumed is None or consumer is not None and consumed.id == consumer.id,
            '本次独立许可已用于原方案；请查询原记录，不能另建调用')
    current_id = None
    if consumer is not None and consumer.reserved_call_id is not None:
        current_id = consumer.id
    db.flush()
    context = source.load_source_context(db, p, producer_id, lock=lock,
        current_consumer_id=current_id)
    prep = prep_module.build_preparation(cap, context, note=note)
    if indexed:
        need(prep['input_summary']['token_bound'] <= settings().cloud_daily_token_limit,
            '完整报告所需预算超过当前云模型日额度；未截断或发送，请由管理员调整日额度')
        allowance = {**allowance, 'source_snapshot_hash': context['snapshot']['snapshot_hash'],
            'request_hash': prep['request_hash'], 'note_hash': digest(note)}
        grant = digest(allowance)
        consumed = db.scalar(select(Consumer).where(Consumer.workspace_id == p.workspace_id,
            Consumer.grant_hash == grant))
        need(consumed is None or consumer is not None and consumed.id == consumer.id,
            '本次独立许可已用于原方案；请查询原记录，不能另建调用')
    count = prep['input_summary']['prepared_request_bytes'] + 1024
    output = prep['generation_contract']['dispatch_options']['max_tokens']
    cost = (Decimal(count)*Decimal(str(allowance['input_usd_per_million']))
        + Decimal(output)*Decimal(str(allowance['output_usd_per_million'])))/Decimal(1000000)
    conservative = float(cost.quantize(Decimal('0.000001'), rounding=ROUND_CEILING))
    need(conservative <= allowance['maximum_cost_usd'], '本次完整原生请求超过独立费用上限，未截断或发送')
    return {'source_context': context, 'capability': cap, 'revision': draft.version,
        'budget': {'schema':'report-saved-map-design-budget-v176','call_limit':1,
            'token_bound':prep['input_summary']['token_bound'],
            'maximum_cost_usd':allowance['maximum_cost_usd'],
            'pricing_basis_hash':allowance['pricing_basis_hash'],'grant_hash':grant}}


def admit_allocation(db, p, body, *, lock=False, consumer=None):
    """One new batch quote; current Consumer is internal leased-run input only."""
    from . import report_business_catalogue_allocation_source_v213 as allocation_source
    from .report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    need(body.generation_profile == profiles.ALLOCATION)
    current_id = None
    if consumer is not None:
        from .report_saved_map_design_consumer_v176 import PreviewAllocationBody
        pinned_body = PreviewAllocationBody.model_validate(consumer.data['preview']['body']).model_dump()
        requested_body = body.model_dump()
        need(consumer.workspace_id == p.workspace_id and consumer.actor_id == p.user_id
            and consumer.model_id == body.model_id
            and profiles.profile_id(consumer.data['preview']['preparation']) == profiles.ALLOCATION
            and pinned_body == {key: requested_body[key] for key in PreviewAllocationBody.model_fields})
        if consumer.reserved_call_id is not None:
            current_id = consumer.id
    actual = allocation_source.prepare_source(db, p, body.draft_id,
        expected_version=body.expected_draft_version, expected_document_hash=body.expected_document_hash,
        expected_catalogue_review_hash=body.catalogue_review.expected_catalogue_review_hash,
        selection=body.selection.model_dump(), max_batch_items=body.max_batch_items,
        write=lock, current_consumer_id=current_id)
    plan = actual['plan']
    need(plan['status'] == 'ready' and 0 <= body.batch_ordinal < len(plan['batches']),
        '当前已保存稿没有这个可发送批次，未新建调用')
    configuration = source.pipeline.legacy.workspace_draft(db, p, lock=lock)
    need(configuration.version == actual['revision'], '当前配置版本已变化，请重新核对原来源')
    cap = published(db, p, body.model_id, 'foundation_model')
    selected = profiles.modules(profiles.ALLOCATION)
    need(cap.data['execution'].get('secret_ref') == 'DASHSCOPE_API_KEY'
        and cap.data['data_policy'].get('cloud_allowed') is True)
    selected.native.input_policy(cap.data['execution'])
    prep = selected.preparation.build_preparation(cap, actual['source_context'],
        plan=plan, batch_ordinal=body.batch_ordinal, document=actual['document'],
        draft_ref=actual['draft_ref'], draft_origin=actual['draft_origin'],
        name_origin=actual['name_origin'], note=body.note)
    need(prep['input_summary']['token_bound'] <= settings().cloud_daily_token_limit,
        '本批完整请求超过当前云模型日额度，未裁剪或发送')
    allowance = _indexed_allowance(p, actual['producer_id'], actual['revision'], body.model_id, body.activity_id)
    allowance = {**allowance, 'generation_profile': profiles.ALLOCATION,
        'source_snapshot_hash': prep['source_snapshot_hash'], 'request_hash': prep['request_hash'],
        'plan_hash': prep['plan_hash'], 'batch_hash': prep['batch_hash'],
        'draft_ref_hash': digest(actual['draft_ref']), 'note_hash': digest(body.note)}
    grant = digest(allowance)
    consumed = db.scalar(select(Consumer).where(Consumer.workspace_id == p.workspace_id, Consumer.grant_hash == grant))
    need(consumed is None or consumer is not None and consumed.id == consumer.id,
        '本批许可已用于原活动，请核对原记录，不能重用额度')
    count = prep['input_summary']['prepared_request_bytes'] + 1024
    output = prep['generation_contract']['dispatch_options']['max_tokens']
    cost = (Decimal(count) * Decimal(str(allowance['input_usd_per_million']))
        + Decimal(output) * Decimal(str(allowance['output_usd_per_million']))) / Decimal(1000000)
    conservative = float(cost.quantize(Decimal('0.000001'), rounding=ROUND_CEILING))
    need(conservative <= allowance['maximum_cost_usd'], '本批完整原生请求超过单次费用上限，未发送')
    return {'allocation_source': actual, 'capability': cap, 'revision': configuration.version,
        'budget': {'schema': 'report-saved-map-design-budget-v176', 'call_limit': 1,
            'token_bound': prep['input_summary']['token_bound'], 'maximum_cost_usd': allowance['maximum_cost_usd'],
            'pricing_basis_hash': allowance['pricing_basis_hash'], 'grant_hash': grant}}


def admit_current_allocation(db, p, body, *, lock=False, consumer=None):
    from .catalogue_current_allocation_v260 import admit
    return admit(db, p, body, lock=lock, consumer=consumer)


def _context(row, binding=None):
    from .report_saved_map_design_consumer_v176 import journal_binding, _native_preparation
    prep = _native_preparation(row.data['preview']['preparation']); spec = prep['generation_contract']
    policy = copy.deepcopy(spec['input_policy']); need(policy.pop('single_batch') is True)
    return {'binding': binding or journal_binding(row), 'request_hash':prep['request_hash'],
        'endpoint':spec['endpoint'],'delivery':spec['delivery'],
        'schema_proof_hash':digest(spec['schema_proof']),'profile_hash':digest(policy),
        'token_bound':prep['input_summary']['token_bound'],'max_bytes':spec['max_response_bytes']}


def _journal_root():
    return Path(settings().storage_dir).absolute() / 'report-saved-map-design-journal-v176'


def dispatch(db, p, job, detached, *, request, spec, on_reserved):
    from .gateway import cloud_json
    from .report_saved_map_design_consumer_v176 import gateway_dispatch_identity
    cap = published(db, p, detached.model_id, 'foundation_model')
    need(cap.content_hash == detached.data['preview']['capability']['content_hash'])
    profile = profiles.profile_id(detached.data['preview']['preparation'])
    tool = profiles.modules(profile).native if profile != profiles.LEGACY else native
    ctx = None
    def reserved(call_id):
        nonlocal ctx
        binding = on_reserved(call_id)
        ctx = _context(detached, binding)
        return capture.prepare(bridge.root(), ctx)
    def received(content, receipt):
        need(ctx is not None)
        policy = copy.deepcopy(spec['input_policy']); need(policy.pop('single_batch') is True)
        wire, original_receipt, state = bridge.response_context(ctx, 'qwen', tool.MODEL,
            policy, tool.STAGE)
        need(content == wire and receipt == original_receipt)
        # Persist the original response before touching ORM state or lease.
        bridge.write_missing_journal(journal, _journal_root(), ctx['binding'], wire, original_receipt)
    extra = ({'evidence_catalogue': copy.deepcopy(detached.data['preview']['source_context']['catalog'])}
        if profile == profiles.CATALOGUE else {})
    allocation = profile in {profiles.ALLOCATION, profiles.CURRENT_ALLOCATION}
    if allocation:
        from .report_saved_map_design_consumer_v176 import _native_preparation
        extra = {'allocation_evidence': copy.deepcopy(_native_preparation(
            detached.data['preview']['preparation'])['allocation_evidence'])}
    scope = nullcontext()
    if profile == profiles.CURRENT_ALLOCATION:
        from .catalogue_current_allocation_v260 import dispatch_scope
        scope = dispatch_scope(db, p, job, detached, request)
    with scope:
        cloud_json(cap, request['messages'][1:], spec['output_schema'], p, full_report=not allocation,
            evidence_stage=tool.STAGE,on_reserved=reserved,on_response=received,
            dispatch_identity=gateway_dispatch_identity(detached), **extra)


def read_response(db, p, row, *, recover_saved):
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id)
    profile = profiles.profile_id(row.data['preview']['preparation'])
    tool = profiles.modules(profile).native if profile != profiles.LEGACY else native
    ctx = _context(row)
    from .report_saved_map_design_consumer_v176 import _native_preparation
    policy = copy.deepcopy(_native_preparation(row.data['preview']['preparation'])['generation_contract']['input_policy'])
    need(policy.pop('single_batch') is True)
    try:
        wire, receipt, state = bridge.response_context(ctx, 'qwen', tool.MODEL, policy, tool.STAGE)
    except (OSError, ValueError):
        return None
    if recover_saved:
        descriptor = bridge.write_missing_journal(journal, _journal_root(), ctx['binding'], wire, receipt)
    else:
        descriptor = journal.read_descriptor(_journal_root(), ctx['binding'])
    loaded = journal.read_response(_journal_root(), ctx['binding'], descriptor)
    need(loaded['content'] == wire and loaded['receipt'] == receipt)
    return {'body':wire.encode('utf-8',errors='surrogateescape'),'receipt':receipt,
        'capture':state,'descriptor':descriptor,'binding':ctx['binding']}
