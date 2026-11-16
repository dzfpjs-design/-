"""Separate one-activity DeepSeek quote; no implicit spending consent.

Use published peak, cache-miss rates even during off-peak hours. The exact
authenticated command and existing global budget gates still authorize work.
Historical Qwen quote and ledger fields are never reused or rewritten.
"""
import math
from uuid import UUID

from .config import settings
from .contracts import digest
from .report_scoped_design_runtime_v181 import need


def pricing_basis():
    return {'schema': 'report-business-flow-pricing-v187', 'provider': 'deepseek',
        'model': 'deepseek-v4-pro', 'input_usd_per_million': 1.32,
        'output_usd_per_million': 3.96, 'cache_discount_assumed': False,
        'off_peak_discount_assumed': False,
        'source_url': 'https://api-docs.deepseek.com/quick_start/pricing/',
        'retrieved_at': '2026-10-24T19:39:35.275350+00:00',
        'html_sha256': 'b856a6e93fa4793ecc8f3fd03a01bb7d8f7572f78a4739da8ce6dbdb09328d66'}


def allowance(p, producer_id, revision, model_id, activity_id):
    need(type(activity_id) is str and str(UUID(activity_id)) == activity_id)
    maximum = settings().report_design_maximum_cost_usd
    need(type(maximum) in (int, float) and math.isfinite(maximum) and 0 < maximum <= 3,
        '管理员配置的单次方案费用上限须大于0且不超过3美元')
    need(settings().cloud_daily_call_limit > 0 and settings().cloud_daily_token_limit > 0,
        '当前云模型日预算未启用；请由管理员配置后再生成方案')
    pricing = pricing_basis()
    return {'schema': 'report-business-flow-activity-v187', 'activity_id': activity_id,
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'producer_id': producer_id,
        'model_id': model_id, 'revision': revision, 'maximum_cost_usd': maximum,
        'input_usd_per_million': pricing['input_usd_per_million'],
        'output_usd_per_million': pricing['output_usd_per_million'],
        'pricing_basis_hash': digest(pricing)}
