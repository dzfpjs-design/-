"""Read-only, actor-scoped view of one saved full-report model response.

Never dispatch, settle, recover, adopt, or return the provider wire.  The
ordinary full-report source and paid-call checkpoints remain authoritative.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import CloudCall

SCHEMA = 'report-full-raw-view-v1'
MAX_TEXT_BYTES = 1024 * 1024
_FINISH = {'stop', 'length', 'content_filter', 'tool_calls', 'function_call'}
_BODY = {'complete', 'oversize', 'unsupported_encoding'}
_HEX = re.compile(r'^[0-9a-f]{64}$')
_RECEIPT_FIELDS = ('call_id', 'provider', 'model', 'usage', 'request_id', 'completion_guard',
                   'response_choice_count', 'finish_reason', 'response_body_status')


def _fail(code: str, message: str, status: int = 409) -> None:
    raise HTTPException(status, {'code':code, 'message':message},
                        headers={'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff'})


def _safe_text(raw: dict) -> tuple[str | None, str | None]:
    """Return exact safe text, or the original reversible ASCII representation."""
    content = raw.get('content')
    if type(content) is str:
        try:
            if not content or '\x00' in content or len(content.encode('utf-8', errors='strict')) > MAX_TEXT_BYTES:
                return None, None
        except UnicodeError:
            return None, None
        return content, 'plain'
    if raw.get('content_encoding') == 'json-ascii-string-v1':
        encoded = raw.get('content_json_ascii')
        if type(encoded) is not str or len(encoded) > MAX_TEXT_BYTES:
            return None, None
        try:
            encoded.encode('ascii', errors='strict')
            decoded = json.loads(encoded, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(decoded) is not str or not decoded:
                return None, None
        except (UnicodeError, ValueError, TypeError, RecursionError):
            return None, None
        # An escaped JSON string is intentionally not decoded: it may contain
        # lone surrogate codepoints or NUL, which are unsafe in a JSON body.
        return encoded, 'json-ascii-string-v1'
    return None, None


def _wire_binds_content(raw: dict, receipt: dict) -> bool:
    """Verify the saved first-choice text against the original provider bytes.

    Missing wire is a historical/unavailable proof, never proof of absence.
    A malformed or inconsistent saved wire is rejected by the caller.
    """
    encoded = raw.get('provider_wire_base64')
    if encoded is None:
        return False
    if (type(encoded) is not str or len(encoded) > ((MAX_TEXT_BYTES + 2) // 3) * 4
            or receipt.get('response_body_status') != 'complete'):
        _fail('full_raw_wire_invalid', '原响应与保存状态无法核对，原记录保留')
    try:
        wire = base64.b64decode(encoded, validate=True)
        if len(wire) > MAX_TEXT_BYTES:
            raise ValueError()
        response = json.loads(wire, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        choices = response.get('choices') if type(response) is dict else None
        if (type(choices) is not list or not choices or type(choices[0]) is not dict
                or type(choices[0].get('message')) is not dict
                or receipt.get('response_choice_count') != len(choices)
                or receipt.get('finish_reason') != choices[0].get('finish_reason')
                or receipt.get('usage') != response.get('usage', {})):
            raise ValueError()
        original = raw.get('content')
        if type(original) is not str and raw.get('content_encoding') == 'json-ascii-string-v1':
            original = json.loads(raw['content_json_ascii'], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(original) is not str or choices[0]['message'].get('content') != original:
            raise ValueError()
        # The checkpoint's content holds only the first choice. Displaying it
        # alone would silently hide other paid choices, so keep all in place
        # and report the bounded text view as unavailable.
        return len(choices) == 1
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        _fail('full_raw_wire_invalid', '原响应与保存内容不一致，原记录保留')


def raw_view(db: Any, principal: Any, identity: str, stage_key: str, revision: int) -> dict:
    """Read one checkpoint from a transaction the caller made READ ONLY.

    The caller must authenticate with full-reading's ReadP and apply no-store
    to successful responses as well as errors. Stage names come only from the
    frozen draft, never from a caller-supplied message or model identity.
    """
    from . import report_full_reading_pipeline as full

    if type(stage_key) is not str or not re.fullmatch(r'extract-[12]|merge', stage_key):
        _fail('full_raw_stage_not_found', '未找到本次读取阶段', 404)
    if type(revision) is not int or revision < 1:
        _fail('full_raw_revision_invalid', '请重新读取当前草案版本', 422)
    row = full.owned(db, principal, identity)
    if row.version != revision:
        _fail('full_raw_revision_changed', '全文读取记录已变化，请刷新后重试')

    if stage_key == 'merge':
        stage = row.data['merge']
        if not stage.get('preview') or not stage.get('start_receipt'):
            _fail('full_raw_stage_not_found', '尚未授权此合并阶段', 404)
        dispatch = stage['preview']
    else:
        matching = [stage for stage in row.data['extractions'] if stage.get('batch_id') == stage_key]
        batches = [item for item in row.data['preview']['batches'] if item.get('batch_id') == stage_key]
        if len(matching) != 1 or len(batches) != 1:
            _fail('full_raw_stage_not_found', '未找到本次读取阶段', 404)
        stage, dispatch = matching[0], batches[0]

    scope = {'workspace_id':row.workspace_id, 'actor_id':row.actor_id, 'draft_id':row.id,
             'revision':row.version, 'stage':stage_key,
             'preview_hash':dispatch.get('preview_hash') if stage_key == 'merge' else row.data['preview']['preview_hash'],
             'raw_hash':None}
    result = {'schema':SCHEMA, 'scope':scope, 'status':'not_saved', 'content':None,
              'content_encoding':None, 'finish_reason':None, 'response_body_status':None,
              'can_apply':False, 'can_publish':False}
    raw = stage.get('raw_response')
    if raw is None:
        return result
    if not isinstance(raw, dict) or not full.checked_hash(raw, 'hash') or not _HEX.fullmatch(raw.get('hash','')):
        _fail('full_raw_integrity', '已保存回答暂不能核对，原记录保留')
    receipt = raw.get('receipt')
    call_id = stage.get('reserved_call_id')
    if not isinstance(receipt,dict) or not isinstance(call_id,str) or receipt.get('call_id') != call_id:
        _fail('full_raw_receipt_invalid', '已保存回答的调用来源无法核对，原记录保留')

    expected = full._expected(row, stage_key)
    call = db.scalar(select(CloudCall).where(CloudCall.id == call_id, CloudCall.workspace_id == row.workspace_id))
    saved_receipt = call.data.get('receipt') if call and isinstance(call.data,dict) else None
    if (not call or call.status not in {'succeeded','failed'} or not isinstance(saved_receipt,dict)
            or call.workspace_id != expected['workspace_id'] or call.capability_id != expected['capability_id']
            or any(call.data.get(k) != expected[k] for k in ('actor_id','request_hash','dispatch_options_hash'))
            or any(receipt.get(k) != expected[k] for k in ('provider','model'))
            or any(saved_receipt.get(k) != receipt.get(k) for k in _RECEIPT_FIELDS)
            or call.data.get('input_policy_hash') != digest(row.data['preview']['generation_contract']['input_policy'])
            or type(call.reserved_tokens) is not int
            or call.reserved_tokens != dispatch['input_summary']['token_bound']):
        _fail('full_raw_call_mismatch', '已保存回答与原调用账本不一致，原记录保留')

    content, encoding = _safe_text(raw)
    if content is not None and not _wire_binds_content(raw, receipt):
        content, encoding = None, None
    finish, body = receipt.get('finish_reason'), receipt.get('response_body_status')
    result['scope']['raw_hash'] = raw['hash']
    result.update(status='available' if content is not None else 'unavailable', content=content,
                  content_encoding=encoding, finish_reason=finish if type(finish) is str and finish in _FINISH else None,
                  response_body_status=body if type(body) is str and body in _BODY else None)
    return result
