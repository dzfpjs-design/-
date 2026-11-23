"""Pure DeepSeek no-ceiling quote, not a policy, preview grant or sender.

Price is optional. An unknown estimate is None, never zero. A supplied price
is checked against its own preview-time validity but is not authenticated as
an Audit here. The eventual DB preview/transport must authenticate all source,
policy and price records independently; no public route uses this module.
"""
import copy
from typing import Literal

from pydantic import StrictStr

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_money_v305 as old
from . import same_goal_advice_unlimited_policy_v2 as policy
from .contracts import digest

SCHEMA = 'same-goal-advice-unlimited-quote-v2'
FLAGS = ('can_dispatch', 'can_accept', 'permissions_verified',
    'source_authenticity_verified', 'current_outcome_verified',
    'goal_completion_verified', 'data_reception_verified',
    'database_price_authorization_verified', 'provider_bill_verified')


class Quote(c.Closed):
    schema: Literal['same-goal-advice-unlimited-quote-v2']
    provider: Literal['deepseek']
    model: Literal['deepseek-v4-pro']
    policy_ref: c.Ref
    policy_limits_hash: c.Hash
    model_pin: c.CapabilityPin
    model_snapshot_hash: c.Hash
    prepared_hash: c.Hash
    request_hash: c.Hash
    dispatch_options_hash: c.Hash
    input_token_bound: old.Count
    output_token_bound: old.Count
    total_token_bound: old.Count
    price_state: Literal['unknown', 'estimated']
    price_basis_ref: c.Ref | None
    price_record_hash: c.Hash | None
    price_expires_at: StrictStr | None
    estimate_minor_units: old.Count | None
    currency: Literal['CNY'] | None
    quoted_at: StrictStr
    can_dispatch: Literal[False]
    can_accept: Literal[False]
    permissions_verified: Literal[False]
    source_authenticity_verified: Literal[False]
    current_outcome_verified: Literal[False]
    goal_completion_verified: Literal[False]
    data_reception_verified: Literal[False]
    database_price_authorization_verified: Literal[False]
    provider_bill_verified: Literal[False]
    record_hash: c.Hash


def _derive(price, *, quoted_at, prepared, model_pin, model_snapshot, limits):
    # A strict v2 policy is required before the shared v1 wire validation.
    budget = c._parse(policy.Limits, limits)
    value, pin, model, budget, input_bound, output_bound = old._actual_request_with_validated_limit(
        prepared, model_pin, model_snapshot, budget)
    at = old._clock(quoted_at)
    if price is None:
        fields = {'price_state': 'unknown', 'price_basis_ref': None,
            'price_record_hash': None, 'price_expires_at': None,
            'estimate_minor_units': None, 'currency': None}
    else:
        validated = old.check_price(price, now=at)
        estimate = old.ceil_cost_minor(input_bound, output_bound,
            validated['input_cache_miss_minor_per_million'], validated['output_minor_per_million'])
        fields = {'price_state': 'estimated', 'price_basis_ref': validated['basis_ref'],
            'price_record_hash': validated['record_hash'], 'price_expires_at': validated['expires_at'],
            'estimate_minor_units': estimate, 'currency': validated['currency']}
    result = {'schema': SCHEMA, 'provider': 'deepseek', 'model': model['execution']['model'],
        'policy_ref': budget['policy_ref'], 'policy_limits_hash': digest(budget),
        'model_pin': pin, 'model_snapshot_hash': digest(model), 'prepared_hash': digest(value),
        'request_hash': value['request_hash'], 'dispatch_options_hash': value['dispatch_options_hash'],
        'input_token_bound': input_bound, 'output_token_bound': output_bound,
        'total_token_bound': input_bound + output_bound, **fields, 'quoted_at': at.isoformat(),
        **{key: False for key in FLAGS}}
    result['record_hash'] = c.record_hash(result)
    return c._parse(Quote, result)


def quote(price, *, now, prepared, model_pin, model_snapshot, limits):
    return copy.deepcopy(_derive(price, quoted_at=now, prepared=prepared,
        model_pin=model_pin, model_snapshot=model_snapshot, limits=limits))


def check_quote(raw, price, *, now, prepared, model_pin, model_snapshot, limits):
    """Recheck original calculation, not live permission or actual billing.

    Recompute at its original time so a price expiring after capture cannot
    destroy a saved record. This does not make an expired price current.
    """
    old.need(type(raw) is dict and all(type(raw.get(key)) is bool and raw[key] is False for key in FLAGS))
    value = c._parse(Quote, raw)
    old.need(value['record_hash'] == c.record_hash(value))
    at = old._instant(value['quoted_at'])
    old.need(at <= old._clock(now))
    expected = _derive(price, quoted_at=at, prepared=prepared,
        model_pin=model_pin, model_snapshot=model_snapshot, limits=limits)
    old.need(digest(value) == digest(expected))
    return copy.deepcopy(value)
