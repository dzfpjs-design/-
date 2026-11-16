"""Server-owned text limits; no client-supplied context claim expands a request.

Official sources checked 2026-10-14:
https://help.aliyun.com/zh/model-studio/qwen-plus
https://api-docs.deepseek.com/quick_start/pricing/

The byte bound is deliberately below the documented 1M-token windows. It is a
conservative token reservation, not a tokenizer or a promise of model quality.
"""
import json

QWEN38_NONTHINKING_PROFILE = 'qwen38-nonthinking-v1'
QWEN38_MODELS = {'qwen3.8-max', 'qwen3.8-max-0902'}


def validate_request_profile(execution):
    """An explicit fixed recipe only; absent means the original wire/policy."""
    if 'request_profile' not in execution:
        return None
    value = execution['request_profile']
    if (type(value) is not str or value != QWEN38_NONTHINKING_PROFILE
            or execution.get('provider') != 'qwen' or type(execution.get('model')) is not str
            or execution['model'] not in QWEN38_MODELS
            or execution.get('task') == 'embedding'):
        raise ValueError('request_profile仅接受明确登记的Qwen3.8非思考文本配方')
    return value


def input_policy(execution, *, full_report=False):
    profile = validate_request_profile(execution)
    provider, model = execution.get('provider'), execution.get('model')
    supported = (
        provider == 'qwen' and model in {'qwen-plus', 'qwen-plus-2025-12-01'}
        or provider == 'deepseek' and model in {'deepseek-flash', 'deepseek-v4-pro'}
        and execution.get('thinking_mode') == 'disabled'
    )
    long_text = bool(full_report and supported)
    nonthinking = bool(full_report and profile == QWEN38_NONTHINKING_PROFILE)
    return {
        'version': ('cloud-qwen38-nonthinking-v1' if nonthinking else
            'cloud-full-report-2026-10-14' if long_text else 'cloud-bounded-text-v1'),
        'max_input_bytes': 900000 if long_text else 50000,
        'transport_timeout_seconds': 180 if long_text or nonthinking else 60,
        'max_output_tokens': 8192,
        'max_images': 0 if full_report else 3,
        'context_token_bound': 1000000 if long_text else None,
        'full_report': bool(full_report),
        'long_context_verified': long_text,
    }


def request_options(execution, options, *, full_report=False):
    """Keep declared model/output and legacy thinking behavior unchanged."""
    result = dict(options)
    policy = input_policy(execution, full_report=full_report)
    if policy['long_context_verified'] and execution.get('provider') == 'qwen':
        # This explicit option is frozen and shown with the new request contract.
        result['enable_thinking'] = False
    return result


def measure_input(messages, policy, max_tokens):
    """Measure the actual prepared system/schema/user messages, without mutation."""
    if type(max_tokens) is not int or not 128 <= max_tokens <= policy['max_output_tokens']:
        raise ValueError('模型输出预算无效，未发送')
    if not isinstance(messages, list) or not messages or any(
        not isinstance(message, dict) or not isinstance(message.get('content'), str)
        for message in messages
    ):
        raise ValueError('完整资料理解只接受已经确认的纯文字消息，未发送原图')
    count = len(json.dumps(messages, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False).encode('utf-8'))
    if count > policy['max_input_bytes']:
        raise ValueError(f"完整消息共{count}字节，超过当前模型配置的{policy['max_input_bytes']}字节上限；全部资料未发送")
    # UTF-8 bytes bound byte-level tokens. Include message framing and output;
    # the budget ledger still rejects a reservation beyond the workspace limit.
    token_bound = count + max_tokens + 1024
    if policy['context_token_bound'] is not None and token_bound > policy['context_token_bound']:
        raise ValueError('完整输入与输出预留超过当前模型上下文上限，未发送')
    return {'prepared_messages_bytes': count, 'token_bound': token_bound,
            'max_input_bytes': policy['max_input_bytes']}


def measure_request_body(body, policy, max_tokens):
    """Measure the exact opt-in POST bytes, including tools and all options.

    The caller must build these bytes with the frozen transport's canonical
    preparation helper and send the same bytes with ``content=body``. Legacy
    message-only accounting above is deliberately unchanged.
    """
    if type(max_tokens) is not int or not 128 <= max_tokens <= policy['max_output_tokens']:
        raise ValueError('模型输出预算无效，未发送')
    if type(body) is not bytes or not body:
        raise ValueError('严格工具调用必须提供完整原请求字节，未发送')
    try:
        body.decode('utf-8', errors='strict')
    except UnicodeError:
        raise ValueError('严格工具调用请求不是有效UTF8，未发送') from None
    count = len(body)
    if count > policy['max_input_bytes']:
        raise ValueError('完整工具请求超过当前模型输入上限，未发送')
    token_bound = count + max_tokens + 1024
    if policy['context_token_bound'] is not None and token_bound > policy['context_token_bound']:
        raise ValueError('完整工具请求与输出预留超过当前模型上下文上限，未发送')
    return {'prepared_request_bytes': count, 'token_bound': token_bound,
            'max_input_bytes': policy['max_input_bytes']}
