"""One bounded, independently captured business-analysis HTTP response.

This is an opt-in transport, not a report protocol, approval, USD grant or retry.
The gateway resolves the actual provider key and supplies the two durable hooks.
"""
import copy
import json
import math

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import digest
from .typed_payload_codec_v131 import canonical_json

STAGE = 'business-reasoning-v197'
MAX_TEXT_BYTES = 65536
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_OUTPUT_TOKENS = 8192
TIMEOUT_SECONDS = 180
ENDPOINTS = {'qwen': 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
    'deepseek': 'https://api.deepseek.com/chat/completions'}


def _blocked(message):
    from .gateway import ModelBlocked
    return ModelBlocked(message)


def strict_json(raw):
    """Reject duplicate fields, non-finite values and invalid Unicode."""
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result: raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed): raise ValueError('nonfinite_json_number')
        return parsed
    def invalid(_value): raise ValueError('nonfinite_json_constant')
    value = json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=invalid)
    canonical_json(value).encode('utf-8', errors='strict')
    return value


def prepare(capability, messages, schema, *, output_token_limit=None):
    from .gateway import prepare_cloud_json, cloud_json_options
    execution = capability.data['execution']
    if (execution.get('provider') not in {'qwen', 'deepseek'} or execution.get('task') == 'embedding'
            or capability.data['data_policy'].get('cloud_allowed') is not True):
        raise _blocked('业务分析模型、用途或云端数据授权不符合登记合同，未发送')
    maximum = execution.get('max_tokens', 2048)
    if type(maximum) is not int or not 128 <= maximum <= MAX_OUTPUT_TOKENS:
        raise _blocked('业务分析输出须明确限制在128至8192 token，未发送')
    if output_token_limit is not None:
        if type(output_token_limit) is not int or not 128 <= output_token_limit <= maximum:
            raise _blocked('业务分析本次输出上限须为128至原模型上限之间的整数，未发送')
        maximum = output_token_limit
    try:
        Draft202012Validator.check_schema(schema)
        canonical_json(schema).encode('utf-8', errors='strict')
        def refs(value):
            if type(value) is dict:
                for name, item in value.items():
                    if name in {'$ref', '$dynamicRef', '$recursiveRef'} and (type(item) is not str or not item.startswith('#')):
                        raise ValueError('external_schema_reference')
                    refs(item)
            elif type(value) is list:
                for item in value: refs(item)
        refs(schema)
    except (ValueError, TypeError, UnicodeError, RecursionError, SchemaError) as error:
        raise _blocked('业务分析输出合同无效或引用外部结构，未发送') from error
    prepared, text_bytes, images = prepare_cloud_json(capability, messages, schema)
    # Count the real system/schema prefix as text too, not only user messages.
    complete_text = sum(len(item['content'].encode('utf-8')) if type(item['content']) is str
        else sum(len(part['text'].encode('utf-8')) for part in item['content'] if part.get('type') == 'text')
        for item in prepared)
    if complete_text > MAX_TEXT_BYTES or images > 3:
        raise _blocked('完整业务分析资料超过64KiB文字或3张图像上限，未截断或发送')
    options = cloud_json_options(execution)
    if output_token_limit is not None:
        # Only this real wire limit changes; the original registered model,
        # credentials, profile and every other option remain untouched.
        options = {**options, 'max_tokens': maximum}
    return {'messages': copy.deepcopy(prepared), 'options': copy.deepcopy(options),
        'request_hash': digest(prepared), 'dispatch_options_hash': digest(options),
        'text_bytes': complete_text, 'image_count': images,
        'token_bound': complete_text + maximum + 1024 + images * 8192}


def response_receipt(raw, receipt):
    """Recover only proven usage from the saved bytes, never an invented zero."""
    from .gateway import _strict_evidence_usage
    result = copy.deepcopy(receipt)
    if type(raw) is not str or len(raw.encode('utf-8', errors='surrogateescape')) > MAX_RESPONSE_BYTES:
        return result
    try:
        envelope = strict_json(raw)
        if type(envelope) is dict:
            result.update(_strict_evidence_usage(envelope.get('usage'), present='usage' in envelope))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        pass
    return result


def parse_response(raw, schema, receipt):
    """Pure replay of a saved envelope. Never calls the provider or a ledger."""
    from .gateway import _strict_evidence_usage
    from .services import validate
    if type(receipt) is not dict or type(receipt.get('http_status')) is not int or receipt['http_status'] != 200:
        raise _blocked('原业务分析HTTP未成功，原回答保留且不重发')
    if type(raw) is not str or len(raw.encode('utf-8', errors='surrogateescape')) > MAX_RESPONSE_BYTES:
        raise _blocked('业务分析原HTTP回答超过安全读取容量；完整原答保留，未截断或重发')
    try:
        envelope = strict_json(raw)
        if type(envelope) is not dict: raise ValueError('response_not_object')
        choices = envelope.get('choices')
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ValueError('response_choices_not_unique')
        first = choices[0]
        if first.get('finish_reason') != 'stop': raise ValueError('response_incomplete')
        message = first.get('message')
        if type(message) is not dict or message.get('tool_calls') or message.get('function_call'):
            raise ValueError('response_not_content_only')
        content = message.get('content')
        if type(content) is not str: raise ValueError('response_content_missing')
        value = strict_json(content)
        validate(schema, value)
        usage = _strict_evidence_usage(envelope.get('usage'), present='usage' in envelope)
        result_receipt = {**copy.deepcopy(receipt), **usage,
            'finish_reason': 'stop', 'response_choice_count': 1}
        return value, result_receipt
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, HTTPException) as error:
        raise _blocked('业务分析原回答未通过完整结构检查；原回答与费用保留，未自动修复或重发') from error


def call(capability, messages, schema, principal, *, key, on_reserved, on_response,
        output_token_limit=None):
    """Exactly one POST; the original full HTTP bytes are captured before parse."""
    from .gateway import ModelBlocked
    from .cloud_dispatch_v130 import reserve_tracked, before_transport
    from .cloud_budget import reserve, finish
    from .db import engine
    from .models import CloudCall
    if not callable(on_reserved) or not callable(on_response) or type(key) is not str or not key:
        raise _blocked('业务分析缺少预算核对或原HTTP首答耐久回调，未发送')
    prepared = (prepare(capability, messages, schema) if output_token_limit is None
        else prepare(capability, messages, schema, output_token_limit=output_token_limit))
    execution = capability.data['execution']
    endpoint = ENDPOINTS[execution['provider']]
    call_id = reserve_tracked(reserve, principal, capability,
        prepared['token_bound'], prepared['request_hash'])
    receipt = {'provider': execution['provider'], 'model': execution['model'], 'call_id': call_id,
        'request_id': None, 'usage': {}, 'usage_unavailable': True,
        'usage_unavailable_reason': 'unparsed_original_http', 'finish_reason': None,
        'response_choice_count': None, 'stage': STAGE}
    try:
        with Session(engine()) as ledger_db:
            row = ledger_db.scalar(select(CloudCall).where(CloudCall.id == call_id).with_for_update())
            if row is None or row.workspace_id != principal.workspace_id or row.capability_id != capability.id:
                raise _blocked('业务分析实际预算预留与当前模型不一致，未发送')
            row.data = {**row.data, 'dispatch_options_hash': prepared['dispatch_options_hash']}
            ledger_db.commit()
        on_reserved(call_id)
        timeout = httpx.Timeout(TIMEOUT_SECONDS, connect=10, write=30, pool=10)
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            from .same_goal_advice_spending_v308 import before_transport as money_before_transport
            money_before_transport()
            before_transport()
            response = client.post(endpoint, headers={'Authorization': 'Bearer ' + key},
                json={**prepared['options'], 'messages': prepared['messages']})
        receipt['request_id'] = response.headers.get('x-request-id')
        receipt['http_status'] = response.status_code
        original = response.content.decode('utf-8', errors='surrogateescape')
        # No parsing, normalisation, truncation or schema check precedes this.
        on_response(original, copy.deepcopy(receipt))
        if response.status_code != 200:
            raise _blocked('业务分析HTTP未成功；原HTTP首答与费用保留，未自动重发')
        # Independent telemetry may settle a failed semantic answer, but never
        # creates zero-token usage when the provider omitted it.
        receipt = response_receipt(original, receipt)
        value, receipt = parse_response(original, schema, receipt)
        finish(call_id, receipt, 'succeeded')
        return value, receipt
    except BaseException as error:
        # Even a failed capture/schema/cancellation leaves the real reservation.
        # An unavailable usage total remains conservatively charged, never zero.
        # Only a trusted Python exception type is telemetry. Exception messages
        # may contain request URLs or credentials and must never enter a receipt.
        receipt = {**receipt, 'transport_error_type': type(error).__name__}
        finish(call_id, receipt, 'failed')
        raise
