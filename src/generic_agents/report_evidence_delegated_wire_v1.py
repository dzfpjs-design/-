"""Private HTTP capture contract for delegated DeepSeek evidence v3 only.

The capture is independent of the legacy message-content journal. It grants
no authority, creates no task and never sends a request. Existing ``fe_``
records retain their original journal and do not acquire this contract.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from .contracts import digest
from . import report_evidence_http_capture_v1 as capture
from . import report_evidence_journal as journal

DELIVERY = 'deepseek-delegated-evidence-json-v1'
ENDPOINT = 'https://api.deepseek.com/chat/completions'


def root():
    from .config import settings
    return Path(settings().storage_dir).absolute() / 'report-evidence-delegated-http-v1'


def context(pipeline, row, key):
    """Recreate the full wire binding from an already validated frozen row."""
    preview = row.data['preview']
    stage = pipeline._stage(row, key)
    if (not row.request_id.startswith('fl_') or not pipeline.v3.applies(preview)
            or preview['provider'] != 'deepseek' or not stage.get('reserved_call_id')):
        raise ValueError('受托证据原包缺少对应原预留')
    spec, authorized = pipeline._spec(row, key), pipeline._authorized(row, key)
    expected_stage = 'reduce-v2' if key == 'reduce' else 'map-v3'
    if spec.get('evidence_stage') != expected_stage:
        raise ValueError('证据原包的阶段不匹配')
    policy = copy.deepcopy(spec['input_policy'])
    if policy.pop('single_batch', None) is not True:
        raise ValueError('证据原包缺少单次请求约束')
    request = pipeline._request(row, key)
    body = {**copy.deepcopy(spec['dispatch_options']), 'messages': copy.deepcopy(request['messages'])}
    binding = copy.deepcopy(pipeline.journal_binding(row, key, stage['reserved_call_id']))
    # The capture layer has a separate 1 MiB reduce binding type. The original
    # v3 content journal remains v1; only this private capture uses v2.
    if key == 'reduce':
        binding['schema'] = 'report-evidence-journal-binding-v2'
    return {'binding': binding, 'request_hash': digest(body), 'endpoint': ENDPOINT,
        'delivery': DELIVERY, 'schema_proof_hash': digest(spec['output_schema']),
        'profile_hash': digest(policy), 'token_bound': authorized['input_summary']['token_bound'],
        'max_bytes': capture.REDUCE_LIMIT if key == 'reduce' else capture.MAP_LIMIT}


def load(pipeline, row, key):
    ctx = context(pipeline, row, key)
    return ctx, capture.load(root(), ctx)


def response(pipeline, row, key):
    """Reconstruct callback content/receipt only from a complete original wire."""
    ctx = context(pipeline, row, key)
    policy = copy.deepcopy(pipeline._spec(row, key)['input_policy']); policy.pop('single_batch')
    return response_context(ctx, row.data['preview']['provider'], row.data['preview']['model'], policy)


def response_context(ctx, provider, model, policy):
    """Use a frozen call context without accessing ORM state in a callback."""
    state = capture.load(root(), ctx)
    if (state['ready'] is not True or state['response_complete'] is not True
            or state['content_encoding_safe'] is not True or state['http_status'] != 200):
        raise ValueError('原 HTTP 回答未完整结束或未成功')
    raw = capture.read_body(root(), ctx, state['capture_hash'])
    # Duplicate JSON keys, nonfinite numbers, invalid UTF-8 and NUL all fail
    # closed. No parsed content is considered evidence before this check.
    value = journal._loads(raw)
    if type(value) is not dict or type(value.get('choices')) is not list or not value['choices']:
        raise ValueError('原 HTTP 回答缺少可核对的首选结果')
    first = value['choices'][0]
    message = first.get('message') if type(first) is dict else None
    content = message.get('content') if type(message) is dict else None
    if type(content) is not str:
        raise ValueError('原 HTTP 回答缺少可保存的文本')
    receipt = {'provider': provider, 'model': model,
        'usage': value.get('usage', {}), 'request_id': state['request_id'],
        'call_id': ctx['binding']['reserved_call_id'], 'evidence_profile': policy,
        'finish_reason': first.get('finish_reason'), 'response_choice_count': len(value['choices'])}
    return content, receipt, state


def verify_saved_response(pipeline, row, key, response_value, content_descriptor):
    """Bind the legacy parsed content and receipt to the original HTTP body."""
    content, receipt, _ = response(pipeline, row, key)
    if (receipt != response_value['receipt']
            or hashlib.sha256(content.encode('utf-8', errors='strict')).hexdigest() != content_descriptor['wire_sha256']):
        raise ValueError('已保存内容与原 HTTP 响应或费用回执不一致')
    parsed = journal._loads(content.encode('utf-8', errors='strict'))
    if parsed != response_value['value']:
        raise ValueError('已解析内容与原 HTTP 回答不一致')
