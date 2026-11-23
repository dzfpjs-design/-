"""Pure calculation fixtures only; no Audit, source authorization or cloud IO."""
import copy
from datetime import timedelta

import pytest

from generic_agents import same_goal_advice_money_v305 as old
from generic_agents import same_goal_advice_unlimited_quote_v2 as v2
from generic_agents.contracts import digest
from tests.test_same_goal_advice_money_v305 import AT, scene


def limits(s):
    value = copy.deepcopy(s.budget)
    value['policy_ref'] = value.pop('quota_policy_ref')
    value.pop('max_cost_minor_units'); value.pop('currency')
    value.update(cost_policy='unlimited', allocation_basis='per_plan')
    return value


def calculate(s, price=None, **changes):
    args = {'now': AT, 'prepared': s.prepared, 'model_pin': s.pin,
        'model_snapshot': s.model, 'limits': limits(s)}
    args.update(changes)
    return v2.quote(price, **args)


def verify(s, value, price=None, **changes):
    args = {'now': AT, 'prepared': s.prepared, 'model_pin': s.pin,
        'model_snapshot': s.model, 'limits': limits(s)}
    args.update(changes)
    return v2.check_quote(value, price, **args)


def test_unknown_price_is_unknown_and_never_a_dispatch_grant(scene):
    value = calculate(scene)
    assert value['price_state'] == 'unknown'
    assert value['estimate_minor_units'] is value['currency'] is value['price_basis_ref'] is None
    assert not value['can_dispatch'] and not value['permissions_verified']
    assert not value['database_price_authorization_verified'] and not value['provider_bill_verified']
    assert verify(scene, value) == value


def test_large_optional_estimate_is_not_a_consumption_gate(scene):
    s = scene
    unsigned = copy.deepcopy(s.unsigned)
    unsigned['input_cache_miss_minor_per_million'] = 1_000_000_000
    unsigned['output_minor_per_million'] = 1_000_000_000
    price = old.seal_price(unsigned, now=AT)
    value = calculate(s, price)
    assert value['estimate_minor_units'] > s.budget['max_cost_minor_units']
    assert value['price_state'] == 'estimated' and value['currency'] == 'CNY'
    assert not value['can_dispatch'] and verify(s, value, price) == value
    # The separately preserved v1 contract still rejects the same price.
    with pytest.raises(ValueError):
        old.quote(price, now=AT, prepared=s.prepared, model_pin=s.pin,
            model_snapshot=s.model, budget=s.budget)


def test_historical_quote_remains_readable_after_price_expiry_without_claiming_current_price(scene):
    value = calculate(scene, scene.price)
    assert verify(scene, value, scene.price, now=AT + timedelta(hours=2)) == value
    with pytest.raises(ValueError):
        calculate(scene, scene.price, now=AT + timedelta(hours=2))


@pytest.mark.parametrize('fault', ['model', 'request', 'output', 'policy', 'ref', 'provider'])
def test_changed_frozen_inputs_cannot_be_hidden_by_rehashing_quote(scene, fault):
    s = scene
    value = calculate(s)
    args = {}
    if fault == 'model':
        args['model_snapshot'] = copy.deepcopy(s.model)
        args['model_snapshot']['execution']['max_tokens'] -= 1
    elif fault == 'request':
        args['prepared'] = copy.deepcopy(s.prepared)
        args['prepared']['messages'][0]['content'] += ' changed'
    elif fault == 'output':
        args['prepared'] = copy.deepcopy(s.prepared)
        args['prepared']['options']['max_tokens'] += 1
        args['prepared']['dispatch_options_hash'] = digest(args['prepared']['options'])
    elif fault == 'policy':
        args['limits'] = {**limits(s), 'allocation_basis': 'per_policy'}
    elif fault == 'ref':
        value = copy.deepcopy(value); value['policy_ref']['record_hash'] = 'a' * 64
        value['record_hash'] = v2.c.record_hash(value)
    else:
        args['model_snapshot'] = copy.deepcopy(s.model)
        args['model_snapshot']['execution']['provider'] = 'qwen'
    with pytest.raises((ValueError, TypeError)):
        verify(s, value, **args)


def test_false_flags_must_be_actual_booleans(scene):
    value = calculate(scene)
    value['can_dispatch'] = 0
    value['record_hash'] = v2.c.record_hash(value)
    with pytest.raises(ValueError):
        verify(scene, value)
