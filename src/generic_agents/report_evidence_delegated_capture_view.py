"""Private, read-only inspection of one delegated evidence HTTP capture.

The original capture, report draft and cloud ledger are authoritative. This
module never repairs, settles, adopts, starts a task or sends a request.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select

from .contracts import digest
from .models import CloudCall, Job
from .services import scoped
from . import report_evidence_journal as journal
from . import report_evidence_pipeline as pipeline
from . import report_evidence_delegated_wire_v1 as delegated
from . import report_evidence_http_capture_v1 as capture


router = APIRouter()
_FINISH = {'stop', 'length', 'content_filter', 'tool_calls', 'function_call'}


def _fail(code, message, status=409):
    raise HTTPException(status, {'code': code, 'message': message},
                        headers={'Cache-Control': 'private, no-store',
                                 'X-Content-Type-Options': 'nosniff'})


def _scope(db, principal, identity, stage, revision):
    row = pipeline.owned(db, principal, identity)
    if row.version != revision:
        _fail('delegated_capture_revision_changed', '读取记录已变化，请刷新后重试')
    allowed = [item['chunk_id'] for item in row.data['maps']]
    if row.data['reduction']['preview'] is not None:
        allowed.append('reduce')
    if (not row.request_id.startswith('fl_') or not pipeline.v3.applies(row.data['preview'])
            or stage not in allowed):
        _fail('delegated_capture_not_found', '未找到本人的此批次原始响应', 404)
    item = pipeline._stage(row, stage)
    authority = pipeline._authorized(row, stage)
    scope = {'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
             'draft_id': row.id, 'revision': row.version, 'stage': stage,
             'source_hash': row.data['preview']['source_hash'],
             'preview_hash': row.data['preview']['preview_hash'],
             'messages_hash': authority['messages_hash'], 'call_id': item['reserved_call_id']}
    if not item['reserved_call_id']:
        return row, scope, None, None

    # Unlike the dispatch path, a private read cannot take FOR UPDATE locks.
    # The authenticated REPEATABLE READ transaction binds the ledger snapshot.
    call = scoped(db, CloudCall, item['reserved_call_id'], principal)
    expected = pipeline._expected(row, stage)
    spec = pipeline._spec(row, stage)
    if (not isinstance(call.data, dict) or call.status not in {'reserved', 'failed', 'succeeded'}
            or call.capability_id != expected['capability_id']
            or type(call.reserved_tokens) is not int or call.reserved_tokens < 0
            or type(call.charged_tokens) is not int or call.charged_tokens < 0
            or call.reserved_tokens != authority['input_summary']['token_bound']
            or any(call.data.get(k) != expected[k] for k in ('actor_id', 'request_hash', 'dispatch_options_hash'))
            or call.data.get('input_policy_hash') != digest(spec['input_policy'])):
        _fail('delegated_capture_ledger_mismatch', '原调用或费用记录无法核对，原记录保留')
    try:
        context = delegated.context(pipeline, row, stage)
    except (ValueError, TypeError, KeyError, OverflowError):
        _fail('delegated_capture_scope_mismatch', '原响应的冻结范围无法核对，原记录保留')
    return row, scope, call, context


def _outcome(state, context, call, expected):
    if state is None or state['state'] == 'missing':
        return 'not_recorded', None, None
    if state['reason'] in {'capacity_exceeded', 'chunk_limit'} or state['prefix_only']:
        return 'oversize', None, None
    if state['reason'] == 'unexpected_content_encoding' or state['content_encoding_safe'] is False:
        return 'unsupported_encoding', None, None
    if not state['response_complete']:
        return ('transport_interrupted' if state['reason'] in
                {'interrupted', 'transport_error', 'deadline', 'cancelled', 'io_error'} else 'partial'), None, None
    if state['http_status'] != 200:
        return 'http_error', None, None
    try:
        wire = capture.read_body(delegated.root(), context, state['capture_hash'])
    except (ValueError, OSError, UnicodeError, TypeError, RecursionError):
        _fail('delegated_capture_changed', '原响应状态已变化，请刷新后重试')
    try:
        outer = journal._loads(wire)
    except (ValueError, UnicodeError, TypeError, RecursionError, OverflowError):
        return 'invalid_envelope', None, None
    if type(outer) is not dict or type(outer.get('choices')) is not list:
        return 'invalid_envelope', None, None
    choices = outer['choices']
    count = len(choices)
    first = choices[0] if choices else None
    raw_finish = first.get('finish_reason') if type(first) is dict else None
    stored = call.data.get('receipt')
    if call.status in {'failed', 'succeeded'} and isinstance(stored, dict) and stored:
        if (stored.get('call_id') != call.id or stored.get('provider') != expected['provider']
                or stored.get('model') != expected['model'] or stored.get('usage', {}) != outer.get('usage', {})
                or stored.get('request_id') != state['request_id']
                or ('finish_reason' in stored and stored['finish_reason'] != raw_finish)
                or ('response_choice_count' in stored and stored['response_choice_count'] != count)):
            _fail('delegated_capture_receipt_mismatch', '原响应与调用账本不一致，原记录保留')
    if count != 1:
        return 'multiple_choices' if count > 1 else 'content_missing', None, count
    if type(first) is not dict or type(first.get('message')) is not dict or type(first['message'].get('content')) is not str:
        return 'content_missing', None, count
    finish = raw_finish if type(raw_finish) is str and raw_finish in _FINISH else None
    if finish == 'length':
        return 'length', finish, count
    if finish == 'stop':
        try:
            journal._loads(first['message']['content'].encode('utf-8', errors='strict'))
        except (ValueError, UnicodeError, TypeError, RecursionError, OverflowError):
            return 'invalid_content_json', finish, count
        return 'normal_end_unverified', finish, count
    return 'other_termination', finish, count


def _recovery_check(db, principal, row, requested_stage):
    """Conservative read-only preflight; this never authorizes recovery.

    The real recovery endpoint rechecks the same data under its own CAS/locks.
    Restrict this preview to one failed stage and an untouched suffix, matching
    the supported sequential worker rather than inferring unobserved history.
    """
    result = {'status': 'not_applicable', 'blocker': None, 'target_stage': None,
              'remaining_count': 0, 'basis_hash': None}
    if row.status not in {'failed', 'response_unknown'}:
        return result
    if principal.role not in {'admin', 'operator'}:
        return {**result, 'status': 'blocked', 'blocker': 'role_not_authorized'}
    job = db.scalar(select(Job).where(Job.id == row.data['job_id'], Job.workspace_id == row.workspace_id))
    if (not job or job.kind != 'read_report_draft' or job.status != 'failed' or job.lease_until is not None
            or not isinstance(job.data, dict) or job.data.get('actor_id') != row.actor_id
            or job.data.get('report_draft_id') != row.id):
        return {**result, 'status': 'blocked', 'blocker': 'original_job_unsettled'}

    if row.data['reduction']['preview'] is not None:
        if any(item['status'] != 'succeeded' for item in row.data['maps']):
            return {**result, 'status': 'blocked', 'blocker': 'stage_order_unverified'}
        pending = [('reduce', row.data['reduction'])]
    else:
        pending = [(item['chunk_id'], item) for item in row.data['maps'] if item['status'] != 'succeeded']
    targets = [(key, item) for key, item in pending if item['reserved_call_id'] is not None]
    if len(targets) != 1:
        return {**result, 'status': 'blocked', 'blocker': 'single_paid_stage_required'}
    key, target = targets[0]
    remaining = [(name, item) for name, item in pending if name != key]
    result = {**result, 'target_stage': key, 'remaining_count': len(remaining)}
    if key != requested_stage:
        return {**result, 'status': 'other_stage', 'blocker': 'inspect_target_stage'}
    if target['status'] not in {'failed', 'response_unknown'}:
        return {**result, 'status': 'blocked', 'blocker': 'paid_stage_unsettled'}
    if row.data['reduction']['preview'] is None:
        order = [item['chunk_id'] for item in row.data['maps']]
        index = order.index(key)
        if (any(item['status'] != 'succeeded' for item in row.data['maps'][:index])
                or any(item['status'] != 'pending' or item['reserved_call_id'] is not None
                       or item['inflight'] or item['journal'] is not None for item in row.data['maps'][index + 1:])):
            return {**result, 'status': 'blocked', 'blocker': 'stage_order_unverified'}
    try:
        from . import report_evidence_delegated_recovery_v1 as recovery
        call = pipeline._call_guard(db, principal, row, key, target['reserved_call_id'], lock=False)
        _, receipt, state, _ = recovery._preflight(pipeline, row, key)
        if call.status in {'failed', 'succeeded'}:
            saved = call.data.get('receipt')
            critical = ('call_id', 'provider', 'model', 'usage', 'request_id')
            if type(saved) is not dict or any(saved.get(field) != receipt.get(field) for field in critical):
                raise ValueError('receipt')
        elif call.status != 'reserved':
            raise ValueError('call')
    except (HTTPException, ValueError, OSError, UnicodeError, TypeError, KeyError, RecursionError, OverflowError):
        return {**result, 'status': 'blocked', 'blocker': 'saved_response_unverified'}
    return {**result, 'status': 'eligible', 'blocker': None,
            'basis_hash': digest({'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
                'draft_id': row.id, 'revision': row.version, 'stage': key,
                'call_id': target['reserved_call_id'], 'capture_hash': state['capture_hash'],
                'source_hash': row.data['preview']['source_hash'],
                'remaining': [name for name, _ in remaining]})}


def capture_view(db, principal, identity, stage, revision):
    row, scope, call, context = _scope(db, principal, identity, stage, revision)
    state = None
    if context is not None:
        try:
            state = capture.load(delegated.root(), context)
        except (ValueError, OSError, TypeError, KeyError, RecursionError):
            _fail('delegated_capture_integrity', '原响应保存状态无法核对，原记录保留')
    outcome, finish, count = _outcome(state, context, call,
                                       pipeline._expected(row, stage) if call is not None else None)
    summary = None if state is None else {key: state[key] for key in (
        'state', 'request_started', 'headers_saved', 'http_status',
        'content_encoding_safe', 'body_bytes', 'body_sha256', 'response_complete',
        'observed_bytes', 'omitted_bytes', 'prefix_only', 'reason', 'capture_hash')}
    if summary is not None:
        # The capture's existing flag says the header is syntactically safe,
        # not that the encoding is supported. Keep these two facts separate.
        encoding = state['http_content_encoding']
        summary['encoding_supported'] = (None if not state['headers_saved'] else
            bool(state['content_encoding_safe'] and (encoding is None or encoding.strip().lower() in ('', 'identity'))))
    ledger = None if call is None else {'status': call.status, 'reserved_tokens': call.reserved_tokens,
        'charged_tokens': call.charged_tokens,
        'usage_reported': isinstance(call.data.get('receipt'), dict) and
                          isinstance(call.data['receipt'].get('usage'), dict) and
                          type(call.data['receipt']['usage'].get('total_tokens')) is int and
                          call.data['receipt']['usage']['total_tokens'] >= 0}
    value = {'schema': 'report-delegated-http-capture-view-v1', 'scope': scope,
             'capture': summary, 'ledger': ledger, 'outcome': outcome,
             'finish_reason': finish, 'choice_count': count,
             'recovery': _recovery_check(db, principal, row, stage),
             # An upstream provider can echo arbitrary unknown secrets. A
             # regex or absence of request headers cannot prove bytes safe to
             # return; keep every original private until a reviewed release.
             'body_access': 'not_recorded' if state is None or state['state'] == 'missing' else 'download_disabled',
             'can_view_body': False,
             'can_apply': False, 'can_publish': False, 'can_resend': False}
    return {**value, 'view_hash': digest(value)}


@router.get('/report-evidence-readings/{identity}/delegated-captures/{stage}')
def capture_route(identity: UUID, stage: str, response: Response, p: pipeline.ReadP,
                  db: pipeline.DB, revision: int = Query(..., ge=1)):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return capture_view(db, p, str(identity), stage, revision)
