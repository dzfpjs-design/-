"""Analysis-scoped, actor-bound receipts for applying already-paid suggestions.

This module only normalizes and checks compact metadata. It never regenerates
suggestions, opens a gateway, or copies configuration bodies into command records.
"""
import copy
import re
from uuid import UUID

from fastapi import HTTPException

from .contracts import digest


SCHEMA = 'setup-understanding-apply-v1'
BODY_KEYS = {'version', 'revision', 'names', 'object_names', 'accept_domain',
             'request_id', 'result_hash', 'configuration_hash'}


def valid_stored_body(value, key):
    if not isinstance(value, dict) or set(value) != BODY_KEYS:
        return False
    if any(type(value.get(name)) is not int or value[name] < 1 for name in ('version', 'revision')):
        return False
    if type(value.get('accept_domain')) is not bool:
        return False
    for name, limit in [('names', 20), ('object_names', 40)]:
        items = value.get(name)
        if (not isinstance(items, list) or len(items) > limit
                or any(not isinstance(item, str) for item in items) or len(set(items)) != len(items)):
            return False
    if any(not isinstance(value.get(name), str) or not re.fullmatch(r'[a-f0-9]{64}', value[name])
           for name in ('result_hash', 'configuration_hash')):
        return False
    try:
        return value['request_id'] == key == str(UUID(value['request_id']))
    except (ValueError, TypeError, AttributeError):
        return False


def normalized_apply_body(body):
    value = body.model_dump(mode='json')
    return {key: copy.deepcopy(value[key]) for key in (
        'version', 'revision', 'names', 'object_names', 'accept_domain',
        'request_id', 'result_hash', 'configuration_hash')}


def apply_scope(row, principal):
    if row.workspace_id != principal.workspace_id:
        raise HTTPException(404, '原应用记录不在当前工作区')
    return {'workspace_id': principal.workspace_id, 'actor_id': principal.user_id,
            'analysis_id': row.id}


def command_hash(scope, body):
    return digest({'scope': scope, 'body': body})


def find_apply_receipt(row, principal, *, body=None, request_id=None):
    """Recover an existing command before checking a now-stale version/status."""
    key = str(body.request_id) if body is not None else str(request_id)
    scope = apply_scope(row, principal)
    commands = row.data.get('apply_commands', {})
    if not isinstance(commands, dict):
        raise HTTPException(409, '原应用回执索引无法核对，未重复修改')
    if key not in commands:
        return None
    saved = commands[key]
    if not isinstance(saved, dict):
        raise HTTPException(409, '原应用回执无法核对，未重复修改')
    original, receipt = saved.get('body'), saved.get('receipt')
    if (saved.get('scope') != scope or not valid_stored_body(original, key)
            or not isinstance(receipt, dict)):
        raise HTTPException(409, '此原应用编号的账号或记录范围无法核对，未重复修改')
    expected = command_hash(scope, original)
    guard = row.data.get('local_reuse_guard', row.data['request'])
    if (not isinstance(guard, dict) or receipt.get('schema') != SCHEMA or receipt.get('command_hash') != expected
            or receipt.get('receipt_hash') != digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
            or any(receipt.get(k) != value for k, value in scope.items())
            or receipt.get('request_id') != key or receipt.get('status') != 'applied'
            or receipt.get('original_version') != original.get('version')
            or receipt.get('original_revision') != original.get('revision')
            or receipt.get('configuration_hash') != original.get('configuration_hash')
            or original.get('configuration_hash') != guard.get('configuration_hash')
            or original.get('revision') != guard.get('revision')
            or receipt.get('result_hash') != original.get('result_hash')
            or receipt.get('result_hash') != row.data.get('result_hash')
            or digest(row.data.get('result')) != receipt.get('result_hash')
            or type(receipt.get('applied_revision')) is not int
            or receipt['applied_revision'] != original.get('revision', 0) + 1
            or not isinstance(receipt.get('draft_id'), str) or not receipt['draft_id']):
        raise HTTPException(409, '原应用回执或建议引用无法核对，未重复修改')
    if body is not None and normalized_apply_body(body) != original:
        raise HTTPException(409, '此原应用编号已用于不同内容，不能重签或重复应用')
    return copy.deepcopy(receipt)


def store_apply_receipt(row, principal, body, *, draft_id, applied_revision, applied_configuration_hash):
    scope, original = apply_scope(row, principal), normalized_apply_body(body)
    commands = row.data.get('apply_commands', {})
    if len(commands) >= 1000:
        raise HTTPException(409, '本记录应用回执已达上限，请保留历史并使用新的建议记录')
    if str(body.request_id) in commands:
        raise HTTPException(409, '原应用编号已经存在，请核对原回执')
    receipt = {'schema': SCHEMA, 'status': 'applied', **scope,
        'request_id': str(body.request_id), 'command_hash': command_hash(scope, original),
        'original_version': body.version, 'original_revision': body.revision,
        'result_hash': body.result_hash, 'configuration_hash': body.configuration_hash,
        'draft_id': draft_id, 'applied_revision': applied_revision,
        'applied_configuration_hash': applied_configuration_hash}
    receipt['receipt_hash'] = digest(receipt)
    row.data = {**row.data, 'apply_commands': {**commands, str(body.request_id): {
        'scope': scope, 'body': original, 'receipt': receipt}}}
    return copy.deepcopy(receipt)
