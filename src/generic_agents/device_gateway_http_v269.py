"""Managed device HTTP, with real caller scope and no legacy/env fallback.

Raw receipt capture follows bounded strict decoding and credential-echo checks.
Approval and the actual dispatch marker remain separate: a rejected preflight
does not claim that a device request was sent. No transaction spans network I/O.
"""
import json
import math
import time

import httpx
from fastapi import HTTPException
from sqlalchemy import select, func

from . import leases
from .gateway_credentials_v268 import open_secret
from .device_gateway_store_v268 import contains_secret

LIMIT = 1024 * 1024
INPUT_LIMIT = 64 * 1024
TIMEOUT = 10
ERROR = '设备服务响应尚不能核对，原请求保留；未更换原号或自动重发'


def need(condition, message=ERROR):
    if not condition:
        raise HTTPException(409, message)


def managed_execution(execution):
    return type(execution) is dict and (
        'credential_scope' in execution or 'device_gateway_connection_id' in execution)


def reject_legacy(execution):
    need(not managed_execution(execution),
        '此设备能力必须通过原业务计划与工作区授权调用，不能使用旧凭据通道')


def _check(db, p, cap, purpose, *, inputs=None, key=None, invocation=None, stage=None):
    need(leases.fenced(db), '设备调用缺少实际事务租约保护，未发送或核对外部回执')
    from .device_gateway_tools_v269 import validate_tool
    context = validate_tool(db, p, cap, purpose=purpose, inputs=inputs, key=key,
        invocation=invocation)
    owned = leases.ownership(db)
    actual = context['invocation']
    need(type(owned.get('job_id')) is str and type(owned.get('lease_token')) is str
        and actual is not None and actual.data.get('job_id') == owned['job_id'],
        '设备调用缺少原后台任务租约，未发送或核对外部回执')
    if purpose == 'dispatch':
        need(actual.status == 'running' and actual.data.get('lease_token') == owned['lease_token'])
    from .models import Job
    job = db.execute(select(Job.id).where(Job.id == owned['job_id'],
        Job.workspace_id == p.workspace_id, Job.status == 'running',
        Job.lease_token == owned['lease_token'], Job.lease_until > func.clock_timestamp(),
        Job.data['actor_id'].as_string() == p.user_id)).first()
    need(job is not None, '原后台任务租约已变化，未发送或核对外部回执')
    from .workflow_registered_tool_plan_v201 import authorize_device_transport
    authorize_device_transport(db, p, context['capability'], actual,
        stage=stage or ('receipt' if purpose == 'receipt' else 'preflight'))
    return context


def _secret(context):
    row = context['snapshot']
    return open_secret(row.workspace_id, row.connection_id,
        row.credential_revision, row.token_cipher)


def _strict(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def invalid(_):
        raise ValueError()
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)
        pending = [(value, 0)]; count = 0
        while pending:
            item, depth = pending.pop(); count += 1
            if depth > 128 or count > 50000 or type(item) is float and not math.isfinite(item):
                raise ValueError()
            if type(item) is dict:
                pending.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                pending.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(409, ERROR) from None


def _exchange(method, endpoint, secret, key, *, wire=None, on_response=None,
              before_capture=None):
    """Only server-derived numeric endpoints and already-scoped secrets enter."""
    started = time.monotonic()
    headers = {'Authorization': 'Bearer ' + secret, 'Idempotency-Key': key,
        'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    if wire is not None:
        headers['Content-Type'] = 'application/json'
    try:
        with httpx.Client(timeout=httpx.Timeout(TIMEOUT, connect=5),
                follow_redirects=False, trust_env=False) as client:
            with client.stream(method, endpoint, headers=headers, content=wire) as response:
                # 202 is deliberately no output, not a synchronous success.
                if method == 'POST' and response.status_code == 202:
                    return None
                need(response.status_code == 200)
                content_type = response.headers.get('content-type', '').split(';')[0]
                need(content_type == 'application/json'
                    and response.headers.get('content-encoding', 'identity').lower() == 'identity')
                raw = bytearray()
                for chunk in response.iter_raw():
                    need(len(raw) + len(chunk) <= LIMIT and time.monotonic() - started <= TIMEOUT)
                    raw.extend(chunk)
        raw = bytes(raw)
        need(raw and time.monotonic() - started <= TIMEOUT)
        value = _strict(raw)
        # Decode BEFORE capture: escaped bearer strings and keys must not enter
        # the existing original-HTTP receipt journal or any model/UI output.
        need(not contains_secret(value, secret) and secret.encode('ascii') not in raw,
            '设备响应包含访问凭据，已阻止保存或展示；原请求保留，请管理员核对设备服务')
        if before_capture is not None:
            before_capture()
        if on_response is not None:
            on_response(raw, {'status_code': 200, 'content_type': content_type})
        return value
    except httpx.HTTPError:
        # Never persist a remote body, credential-bearing URL or exception text.
        raise HTTPException(409, ERROR) from None


def call_device_tool(db, p, cap, inputs, key, *, invocation,
                     before_authorize, on_send):
    need(callable(before_authorize) and callable(on_send))
    context = _check(db, p, cap, 'dispatch', inputs=inputs, key=key, invocation=invocation)
    secret = _secret(context)  # Missing/corrupt key fails before dispatch marking.
    try:
        try:
            wire = json.dumps(inputs, sort_keys=True, ensure_ascii=False,
                allow_nan=False, separators=(',', ':')).encode('utf-8')
        except (TypeError, ValueError, OverflowError, UnicodeError):
            raise HTTPException(422, '设备采样请求格式无法核对，未提交') from None
        need(len(wire) <= INPUT_LIMIT and not contains_secret(inputs, secret))
        before_authorize()  # Original parent/approval/lease gate, no sent claim.
        current = _check(db, p, cap, 'dispatch', inputs=inputs, key=key, invocation=invocation, stage='send')
        need(current['snapshot'].id == context['snapshot'].id
            and current['endpoint'] == context['endpoint'])
        leases.checkpoint(db)
        on_send()  # Last step before the actual HTTP transport; no earlier flag.
        return _exchange('POST', current['endpoint'], secret, key, wire=wire)
    finally:
        secret = None


def query_device_receipt(db, p, cap, key, *, invocation, on_response=None):
    context = _check(db, p, cap, 'receipt', key=key, invocation=invocation)
    secret = _secret(context)
    try:
        leases.checkpoint(db)
        def before_capture():
            actual = _check(db, p, cap, 'receipt', key=key, invocation=invocation)
            need(actual['snapshot'].id == context['snapshot'].id
                and actual['receipt_endpoint'] == context['receipt_endpoint'])
        return _exchange('GET', context['receipt_endpoint'], secret, key,
            on_response=on_response, before_capture=before_capture)
    finally:
        secret = None
