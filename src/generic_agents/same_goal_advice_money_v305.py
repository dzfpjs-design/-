"""Pure DeepSeek text price/quote consistency; no price or spending authority.

A basis_ref is a caller-supplied reference, not proof that its DB Audit exists.
Official bytes/rates, effective pricing and billable-token bounds must be
authenticated separately by the fixed runtime. These checks neither lock the
provider's future prices nor reserve money/tokens nor permit an HTTP request.
Historical paid replies retain their original quote; never reprice or resend.
"""
import copy
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr

from . import same_goal_advice_contracts_v302 as advice
from .contracts import Manifest, digest
from .workflow_goal_evaluation_contracts_v288 import FLAGS as EVIDENCE_FLAGS

PRICE = 'same-goal-advice-price-basis-v305'
QUOTE = 'same-goal-advice-money-quote-v305'
PROVIDER = 'deepseek'
MODEL = 'deepseek-v4-pro'
OFFICIAL_URL = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
UNIT = 1_000_000
FRAME_TOKENS = 1024
MAX_TEXT_BYTES = 65536
MAX_VALIDITY = timedelta(hours=24)
ALGORITHM = 'v197-text-utf8-plus-1024-output-limited-v305'
FLAGS = {**EVIDENCE_FLAGS, 'database_price_authorization_verified': False,
    'money_reserved': False, 'cloud_send_authorized': False}
Closed, ID, Hash = advice.Closed, advice.ID, advice.Hash
Count = Annotated[StrictInt, Field(ge=0, le=10**12)]


def need(value):
    if not value: raise ValueError('固定价格依据、实际文本请求上界、币种或单次费用上限无法核对')


def _parse(model, raw):
    advice.base._json(raw)
    value = model.model_validate(copy.deepcopy(raw)).model_dump(mode='json')
    need(digest(value) == digest(raw))
    return value


def record_hash(value):
    return digest({key: item for key, item in value.items() if key != 'record_hash'})


def _clock(value):
    need(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _instant(value):
    need(type(value) is str)
    return _clock(datetime.fromisoformat(value.replace('Z', '+00:00')))


class OfficialOriginal(Closed):
    url: Literal['https://api-docs.deepseek.com/zh-cn/quick_start/pricing/']
    sha256: Hash
    size_bytes: Annotated[StrictInt, Field(ge=1, le=32 * 1024 * 1024)]


class PriceBasis(Closed):
    schema: Literal['same-goal-advice-price-basis-v305']
    basis_ref: advice.Ref
    official_original: OfficialOriginal
    provider: Literal['deepseek']
    model: Literal['deepseek-v4-pro']
    currency: Literal['CNY']
    rate_unit_tokens: Literal[1000000]
    input_cache_miss_minor_per_million: Count
    output_minor_per_million: Count
    cache_discount_assumed: Literal[False]
    off_peak_discount_assumed: Literal[False]
    checked_at: StrictStr
    expires_at: StrictStr
    database_price_authorization_verified: Literal[False]
    provider_price_lock_guaranteed: Literal[False]
    record_hash: Hash


class TextPart(Closed):
    type: Literal['text']
    text: StrictStr


class Message(Closed):
    role: Literal['system', 'user', 'assistant']
    content: StrictStr | Annotated[list[TextPart], Field(min_length=1, max_length=16)]


class ResponseFormat(Closed):
    type: Literal['json_object']


class Thinking(Closed):
    type: Literal['disabled']


class Options(Closed):
    model: Literal['deepseek-v4-pro']
    max_tokens: Annotated[StrictInt, Field(ge=128, le=8192)]
    response_format: ResponseFormat
    thinking: Thinking


class Prepared(Closed):
    # Exact current transport.prepare payload, including its schema prefix.
    messages: Annotated[list[Message], Field(min_length=1, max_length=8)]
    options: Options
    request_hash: Hash
    dispatch_options_hash: Hash
    text_bytes: Count
    image_count: Annotated[StrictInt, Field(ge=0, le=0)]
    token_bound: Count


class MoneyQuote(Closed):
    schema: Literal['same-goal-advice-money-quote-v305']
    algorithm: Literal['v197-text-utf8-plus-1024-output-limited-v305']
    purpose: Literal['calculation-only-not-a-send-grant']
    price_basis_ref: advice.Ref
    price_record_hash: Hash
    model_pin: advice.CapabilityPin
    model_snapshot_hash: Hash
    quota_policy_ref: advice.Ref
    budget_hash: Hash
    provider: Literal['deepseek']
    model: Literal['deepseek-v4-pro']
    currency: Literal['CNY']
    rate_unit_tokens: Literal[1000000]
    input_cache_miss_minor_per_million: Count
    output_minor_per_million: Count
    prepared_hash: Hash
    request_hash: Hash
    dispatch_options_hash: Hash
    request_body_bytes: Count
    input_token_bound: Count
    output_token_bound: Count
    total_token_bound: Count
    maximum_cost_minor_units: Count
    configured_ceiling_minor_units: Annotated[StrictInt, Field(ge=1)]
    quoted_at: StrictStr
    price_expires_at: StrictStr
    can_dispatch: Literal[False]
    can_accept: Literal[False]
    permissions_verified: Literal[False]
    source_authenticity_verified: Literal[False]
    current_outcome_verified: Literal[False]
    goal_completion_verified: Literal[False]
    data_reception_verified: Literal[False]
    semantic_quality_verified: Literal[False]
    database_price_authorization_verified: Literal[False]
    money_reserved: Literal[False]
    cloud_send_authorized: Literal[False]
    record_hash: Hash


def check_price(raw, *, now):
    """Current consistency only; the caller must authenticate the basis_ref."""
    need(type(raw) is dict and all(type(raw.get(key)) is bool and raw[key] is False for key in
        ('cache_discount_assumed', 'off_peak_discount_assumed',
         'database_price_authorization_verified', 'provider_price_lock_guaranteed')))
    # Pydantic numeric Literal otherwise regards bool/int as equivalent.
    need(type(raw.get('rate_unit_tokens')) is int and raw['rate_unit_tokens'] == UNIT)
    value = _parse(PriceBasis, raw)
    checked, expires, current = _instant(value['checked_at']), _instant(value['expires_at']), _clock(now)
    need(value['record_hash'] == record_hash(value)
        and checked <= current < expires and checked < expires <= checked + MAX_VALIDITY)
    return copy.deepcopy(value)


def seal_price(unsigned, *, now):
    need(type(unsigned) is dict and 'record_hash' not in unsigned)
    return check_price({**copy.deepcopy(unsigned), 'record_hash': record_hash(unsigned)}, now=now)


def ceil_cost_minor(input_bound, output_bound, input_rate, output_rate):
    """Exact minor units per million tokens; no float, discount, FX or rounding down."""
    values = (input_bound, output_bound, input_rate, output_rate)
    need(all(type(item) is int and 0 <= item <= 10**12 for item in values))
    numerator = input_bound * input_rate + output_bound * output_rate
    return (numerator + UNIT - 1) // UNIT


def _actual_request(prepared, model_pin, model_snapshot, budget):
    limit = _parse(advice.Budget, budget)
    return _actual_request_with_validated_limit(prepared, model_pin, model_snapshot, limit)


def _actual_request_with_validated_limit(prepared, model_pin, model_snapshot, limit):
    """Shared wire validation; caller must first validate its exact policy type.

    The v1 entry above still requires its original closed Budget. New versions
    may reuse this only after independently parsing their own closed policy;
    this helper does not authorize a policy, a reservation, or a send.
    """
    value = _parse(Prepared, prepared)
    pin = _parse(advice.CapabilityPin, model_pin)
    model = _parse(Manifest, model_snapshot)
    execution = model['execution']
    need(model['kind'] == 'foundation_model' and 'business' in model['allowed_agent_roles']
        and model['data_policy'].get('cloud_allowed') is True
        and pin['content_hash'] == digest(model) and pin['release_version'] == model['version']
        and execution.get('provider') == PROVIDER and execution.get('model') == MODEL
        and execution.get('task') != 'embedding' and execution.get('report_evidence_output') is None
        and execution.get('thinking_mode') == 'disabled'
        and execution.get('request_profile') is None)
    maximum = execution.get('max_tokens')
    need(type(maximum) is int and maximum >= 128
        and value['options']['max_tokens'] <= min(maximum, limit['max_tokens']))
    # Only the actual server-limited output token count may differ from the
    # original model's ceiling. Every other wire option is fixed by that model.
    expected = {'model': execution['model'], 'max_tokens': value['options']['max_tokens'],
        'response_format': {'type': 'json_object'}, 'thinking': {'type': execution['thinking_mode']}}
    need(value['options'] == expected and limit['provider'] == PROVIDER
        and value['request_hash'] == digest(value['messages'])
        and value['dispatch_options_hash'] == digest(expected))
    complete_text = 0
    for message in value['messages']:
        content = message['content']
        complete_text += (len(content.encode('utf-8', errors='strict')) if type(content) is str
            else sum(len(part['text'].encode('utf-8', errors='strict')) for part in content))
    output_bound = expected['max_tokens']; input_bound = complete_text + FRAME_TOKENS
    wire_bytes = len(advice.base._json({**expected, 'messages': value['messages']}).encode('utf-8'))
    need(0 < complete_text <= MAX_TEXT_BYTES and value['text_bytes'] == complete_text
        and value['token_bound'] == input_bound + output_bound
        and wire_bytes <= limit['max_request_bytes'])
    # This binds all seven actual preparation fields, not just user text.
    return value, pin, model, limit, input_bound, output_bound


def _quote(price, *, now, quoted_at, prepared, model_pin, model_snapshot, budget):
    price = check_price(price, now=now); current = _clock(now); at = _clock(quoted_at)
    need(_instant(price['checked_at']) <= at <= current < _instant(price['expires_at']))
    value, pin, model, limit, input_bound, output_bound = _actual_request(
        prepared, model_pin, model_snapshot, budget)
    need(limit['currency'] == price['currency'])
    cost = ceil_cost_minor(input_bound, output_bound,
        price['input_cache_miss_minor_per_million'], price['output_minor_per_million'])
    need(cost <= limit['max_cost_minor_units'])
    result = {'schema': QUOTE, 'algorithm': ALGORITHM, 'purpose': 'calculation-only-not-a-send-grant',
        'price_basis_ref': price['basis_ref'], 'price_record_hash': price['record_hash'],
        'model_pin': pin, 'model_snapshot_hash': digest(model),
        'quota_policy_ref': limit['quota_policy_ref'], 'budget_hash': digest(limit),
        'provider': PROVIDER, 'model': MODEL, 'currency': price['currency'], 'rate_unit_tokens': UNIT,
        'input_cache_miss_minor_per_million': price['input_cache_miss_minor_per_million'],
        'output_minor_per_million': price['output_minor_per_million'],
        'prepared_hash': digest(value), 'request_hash': value['request_hash'],
        'dispatch_options_hash': value['dispatch_options_hash'],
        'request_body_bytes': len(advice.base._json({**value['options'], 'messages': value['messages']}).encode('utf-8')),
        'input_token_bound': input_bound, 'output_token_bound': output_bound,
        'total_token_bound': input_bound + output_bound, 'maximum_cost_minor_units': cost,
        'configured_ceiling_minor_units': limit['max_cost_minor_units'],
        'quoted_at': at.isoformat(), 'price_expires_at': price['expires_at'], **FLAGS}
    result['record_hash'] = record_hash(result)
    return _parse(MoneyQuote, result)


def quote(price, *, now, prepared, model_pin, model_snapshot, budget):
    return copy.deepcopy(_quote(price, now=now, quoted_at=now, prepared=prepared,
        model_pin=model_pin, model_snapshot=model_snapshot, budget=budget))


def check_quote(raw, price, *, now, prepared, model_pin, model_snapshot, budget):
    """Recompute an original quote; no reserve, send or paid-history repricing."""
    need(type(raw) is dict and all(type(raw.get(key)) is bool and raw[key] is False for key in FLAGS))
    value = _parse(MoneyQuote, raw)
    need(value['record_hash'] == record_hash(value))
    expected = _quote(price, now=now, quoted_at=_instant(value['quoted_at']), prepared=prepared,
        model_pin=model_pin, model_snapshot=model_snapshot, budget=budget)
    need(digest(value) == digest(expected))
    return copy.deepcopy(value)

