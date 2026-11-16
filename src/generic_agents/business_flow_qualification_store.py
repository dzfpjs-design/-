"""Persist explicit author self-review, never adopting or running a flow.

Root's API exclusively owns the fresh current-source, complete wire/graph,
questions and full-quote checks. Its eight-key source_snapshot is trusted only
as that caller's checked input; this store cannot independently grant source
authority. It binds the immutable original save Command and its two Audits.
Historical reads use saved storage/current IAM, never provider/source files.
Call replay BEFORE fresh source loading: qualification nonce -> review actor
exclusive -> qualification actor/record locks -> original attempt/source locks.
No SQL error is swallowed and the caller owns rollback after any exception.
"""
import copy
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import or_, select

from . import business_flow_review_store as reviews
from . import business_flow_nested_review_v195 as nested
from . import models as m
from .business_flow_qualification_models import BusinessFlowQualification as Qualification

PREFIX = reviews.PREFIX
SCHEMA = 'business-flow-author-review-record-v1'
SNAPSHOT = 'business-flow-author-review-snapshot-v1'
COMMAND_SCHEMA = 'business-flow-author-review-command-v1'
RESULT = 'author-review-result-v1'
REQUEST = 'author-review-original-result-v1'
AUDIT = 'business_flow.author_qualified_v1'
MAX_WIRE_BYTES = 32 * 1024
MAX_RECORDS = 2000
FLAGS = dict(reviews.FLAGS)
SOURCE_KEYS = {'schema', 'attempt_id', 'source_binding_hash', 'value_hash', 'diagnostic_context_hash',
    'original_source_hash', 'current_source_hash', 'current_materials_hash'}
BODY_KEYS = {'request_id', 'expected_version', 'save_request_id', 'value_hash',
    'ack_scope', 'ack_inferences', 'reason', 'schema'}
RECORD_KEYS = {'schema', 'draft_version', 'save_request_id', 'save_command_hash', 'save_body_sha256',
    'review_record_hash', 'source_binding', 'baseline_hash', 'value', 'value_hash', 'source_snapshot',
    'source_snapshot_hash', 'ack_scope', 'ack_inferences', 'reason', 'human_review'}
ERROR = '作者核对记录、原保存命令或独立审计无法核对；原回答与已存人工劳动保留'


class InventoryError(HTTPException):
    def __init__(self): super().__init__(409, ERROR)


def need(ok, message=ERROR, code=409):
    if not ok: raise HTTPException(code, message)


def parse_wire(raw):
    """Exact eight-key original JSON; no inferred acknowledgements/defaults."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_WIRE_BYTES: raise ValueError(ERROR)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError(ERROR)
                result[key] = value
            return result
        def constant(_): raise ValueError(ERROR)
        body = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=constant)
        if type(body) is not dict or set(body) != BODY_KEYS or body['schema'] != COMMAND_SCHEMA: raise ValueError(ERROR)
        reviews.identity(body['request_id']); reviews.identity(body['save_request_id'])
        if type(body['expected_version']) is not int or body['expected_version'] < 1:
            raise ValueError(ERROR)
        if type(body['value_hash']) is not str or reviews.SHA.fullmatch(body['value_hash']) is None: raise ValueError(ERROR)
        if body['ack_scope'] is not True or body['ack_inferences'] is not True: raise ValueError(ERROR)
        if type(body['reason']) is not str or not 2 <= len(body['reason']) <= 12000 or len(body['reason'].strip()) < 2:
            raise ValueError(ERROR)
        reviews._bytes(body, MAX_WIRE_BYTES); return body
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('作者核对须明确两项确认、原因、原保存编号和严格版本；原稿保留') from None


def qualify_path(attempt_id): return PREFIX + '/attempts/' + reviews.identity(attempt_id) + '/qualify'


def nested_qualify_path(attempt_id): return PREFIX + '/attempts/' + reviews.identity(attempt_id) + '/qualify-nested'


def parse_nested_wire(raw): return nested.parse_wire(raw)


def _command(raw, nested_family):
    return parse_nested_wire(raw) if nested_family else parse_wire(raw)


def _path(attempt_id, nested_family):
    return nested_qualify_path(attempt_id) if nested_family else qualify_path(attempt_id)


def _family_value(value, source_binding, body, nested_family, *, code=409):
    need(type(source_binding) is dict, code=code)
    if nested_family:
        try:
            nested.check_value(value, source_binding, mode=body['review_mode'])
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
            need(False, nested.ERROR, code)
    else:
        need(source_binding.get('schema') not in nested.SOURCE_BINDINGS,
            '递归稿必须使用明确的递归核对合同；未解释为旧人审记录', code)


def _nonce(db, p, request_id, *, shared=False):
    reviews._lock(db, {'business_flow_qualification_nonce': reviews.identity(request_id),
        'workspace_id': p.workspace_id}, shared=shared)


def _source(value, attempt_id, source_binding, value_hash, *, review_mode=None):
    if review_mode is not None:
        try:
            return nested.source_snapshot(value, attempt_id, source_binding, value_hash, mode=review_mode)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
            need(False, nested.ERROR)
    need(type(value) is dict and set(value) == SOURCE_KEYS
        and value['schema'] == 'business-flow-author-review-source-v1')
    reviews._bytes(value, 8192); reviews.identity(value['attempt_id'])
    for key in SOURCE_KEYS - {'schema', 'attempt_id'}:
        need(type(value[key]) is str and reviews.SHA.fullmatch(value[key]) is not None)
    need(value['attempt_id'] == attempt_id and value['source_binding_hash'] == reviews.digest(source_binding)
        and value['value_hash'] == value_hash and value['original_source_hash'] == value['current_source_hash'])
    return copy.deepcopy(value)


def _pin(row, record):
    return {**{key: copy.deepcopy(getattr(row, key)) for key in ('id', 'workspace_id', 'actor_id',
        'attempt_id', 'draft_id', 'save_command_id', 'request_id', 'version', 'method', 'path',
        'exact_body', 'body_sha256', 'body_hash')}, 'saved_at': reviews._utc(row.created_at), 'record': record}


def _result(row, record):
    value = {'schema': RESULT, 'id': row.id, 'version': 1, 'status': 'qualified',
        'attempt_id': row.attempt_id, 'draft_id': row.draft_id, 'draft_version': record['draft_version'],
        'save_request_id': record['save_request_id'], 'request_id': row.request_id,
        'value_hash': record['value_hash'], 'qualification_hash': row.record_hash,
        'source_snapshot_hash': record['source_snapshot_hash'], 'review_record_hash': record['review_record_hash'],
        'human_review': 'author_self_reviewed', 'reviewed_by': row.actor_id, 'reviewed_at': reviews._utc(row.created_at),
        'value': copy.deepcopy(record['value']), 'source_binding': copy.deepcopy(record['source_binding']),
        'baseline_hash': record['baseline_hash'], 'source_snapshot': copy.deepcopy(record['source_snapshot']),
        'ack_scope': True, 'ack_inferences': True, 'reason': record['reason'], **FLAGS}
    if record['schema'] == nested.SNAPSHOT:
        value.update(schema=nested.RESULT, status=nested.status(record['review_mode']),
            **nested.mode_fields(record['review_mode']))
    return value


def _audit(row):
    is_nested = row.data['schema'] == nested.RECORD
    value = {'schema': nested.RECORD if is_nested else SCHEMA, 'qualification_id': row.id, 'actor_id': row.actor_id,
        'attempt_id': row.attempt_id, 'draft_id': row.draft_id, 'request_id': row.request_id,
        'save_command_id': row.save_command_id, 'record_hash': row.record_hash}
    if is_nested:
        value.update(**nested.mode_fields(row.data['record']['review_mode']))
    return value


def _check(row, events, p, drafts, commands):
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id
        and type(row.version) is int and row.version == 1
        and type(row.data) is dict and set(row.data) == {'schema', 'record', 'result'}
        and row.data['schema'] in {SCHEMA, nested.RECORD})
    is_nested = row.data['schema'] == nested.RECORD
    for key in ('id', 'workspace_id', 'actor_id', 'attempt_id', 'draft_id', 'save_command_id', 'request_id'):
        reviews.identity(getattr(row, key))
    need(row.method == 'POST' and row.path == _path(row.attempt_id, is_nested) and type(row.exact_body) is str)
    raw = row.exact_body.encode('utf-8', errors='strict'); body = _command(raw, is_nested)
    need(body['request_id'] == row.request_id and row.body_sha256 == hashlib.sha256(raw).hexdigest()
        and row.body_hash == reviews.digest(body))
    record = row.data['record']
    keys = RECORD_KEYS | nested.EXTRA_RECORD_KEYS if is_nested else RECORD_KEYS
    need(type(record) is dict and set(record) == keys
        and record['schema'] == (nested.SNAPSHOT if is_nested else SNAPSHOT)
        and type(record['draft_version']) is int)
    draft, command = drafts.get(row.draft_id), commands.get(row.save_command_id)
    need(draft is not None and command is not None and draft.attempt_id == row.attempt_id
        and command.draft_id == draft.id and command.attempt_id == row.attempt_id
        and command.request_id == body['save_request_id'] == record['save_request_id']
        and command.draft_version == body['expected_version'] == record['draft_version']
        and record['save_command_hash'] == command.command_hash and record['save_body_sha256'] == command.body_sha256
        and record['review_record_hash'] == reviews.digest(command.data['record_snapshot'])
        and reviews._same(record['value'], command.data['result']['value'])
        and record['value_hash'] == body['value_hash'] == reviews.digest(record['value'])
        and reviews._same(record['source_binding'], draft.data['source_binding'])
        and record['baseline_hash'] == draft.baseline_hash
        and record['ack_scope'] is record['ack_inferences'] is True
        and record['human_review'] == (nested.mode_fields(body['review_mode'])['human_review']
            if is_nested else 'author_self_reviewed') and record['reason'] == body['reason'])
    _family_value(record['value'], record['source_binding'], body, is_nested)
    if is_nested:
        expected = nested.mode_fields(body['review_mode'])
        need(all(type(record[key]) is type(item) and record[key] == item for key, item in expected.items()))
    source = _source(record['source_snapshot'], row.attempt_id, record['source_binding'], record['value_hash'],
        review_mode=body['review_mode'] if is_nested else None)
    need(record['source_snapshot_hash'] == reviews.digest(source)
        and row.record_hash == reviews.digest(_pin(row, record))
        and reviews._same(row.data['result'], _result(row, record))
        and len(events) == 1 and events[0].workspace_id == p.workspace_id and events[0].actor_id == p.user_id
        and events[0].action == AUDIT and events[0].object_id == row.id and reviews._same(events[0].data, _audit(row)))
    reviews._bytes(row.data); return copy.deepcopy(row.data['result'])


def _inventory(db, p, *, write=False, target_attempt=None):
    """Old review's complete three-way inventory always precedes this family."""
    drafts, commands, histories = reviews._inventory(db, p, write=write, target_attempt=target_attempt)
    try:
        reviews._lock(db, {'business_flow_qualification_actor': p.user_id,
            'workspace_id': p.workspace_id}, shared=not write)
        rows = list(db.scalars(select(Qualification).where(Qualification.workspace_id == p.workspace_id,
            Qualification.actor_id == p.user_id).execution_options(populate_existing=True)))
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == AUDIT,
            or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
        need(len(rows) <= MAX_RECORDS and len(events) <= MAX_RECORDS)
        ids = {reviews.identity(row.id) for row in rows}
        for event in events:
            need(type(event.data) is dict); ids.add(reviews.identity(event.data['qualification_id']))
        for value in sorted(ids): reviews._lock(db, {'business_flow_qualification_record': value,
            'workspace_id': p.workspace_id}, shared=not write)
        by_id = {row.id: row for row in rows}
        need(len(by_id) == len(rows) and set(by_id) == ids
            and len({row.request_id for row in rows}) == len(rows))
        values = {row.id: _check(row, [event for event in events if event.data['qualification_id'] == row.id],
            p, drafts, commands) for row in rows}
        return by_id, values, drafts, commands, histories
    except InventoryError: raise
    except HTTPException as error: raise InventoryError() from error
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise InventoryError() from None


def _nonce_owner(db, p, request_id):
    rows = list(db.scalars(select(Qualification).where(Qualification.workspace_id == p.workspace_id,
        Qualification.request_id == request_id).execution_options(populate_existing=True)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == AUDIT, m.Audit.data['request_id'].as_string() == request_id)))
    need(len(rows) <= 1 and all(row.actor_id == p.user_id for row in rows)
        and all(event.actor_id == p.user_id and type(event.data) is dict
            and event.data.get('actor_id') == p.user_id for event in events), '原核对编号已使用或不属于当前账号')
    need(not events or rows, '原核对有独立审计但缺少原记录；未重新建立资格')


def _replay(rows, values, attempt_id, body, raw, method, path):
    matching = [row for row in rows.values() if row.request_id == body['request_id']]
    need(len(matching) <= 1)
    if not matching: return None
    row = matching[0]
    need(row.attempt_id == attempt_id and row.method == method and row.path == path
        and row.exact_body == raw.decode('utf-8', errors='strict')
        and row.body_sha256 == hashlib.sha256(raw).hexdigest() and row.body_hash == reviews.digest(body),
        '同一核对编号的目标、路径或原文不同；原记录保留')
    return copy.deepcopy(values[row.id])


def _replay_command(db, p, attempt_id, raw, *, method, path, nested_family):
    reviews.identity(attempt_id); body = _command(raw, nested_family); reviews._iam(db, p)
    need(method == 'POST' and path == _path(attempt_id, nested_family), '核对路径或方法不符', 422)
    _nonce(db, p, body['request_id'])
    rows, values, _, _, _ = _inventory(db, p, write=True, target_attempt=attempt_id)
    _nonce_owner(db, p, body['request_id'])
    return _replay(rows, values, attempt_id, body, raw, method, path)


def replay(db, p, attempt_id, raw, *, method, path):
    return _replay_command(db, p, attempt_id, raw, method=method, path=path, nested_family=False)


def replay_nested(db, p, attempt_id, raw, *, method, path):
    return _replay_command(db, p, attempt_id, raw, method=method, path=path, nested_family=True)


def _create(db, p, attempt_id, raw, *, method, path, saved, source_snapshot, nested_family):
    """Fresh source eligibility is Root's responsibility, not this snapshot hash."""
    reviews.identity(attempt_id); body = _command(raw, nested_family); reviews._iam(db, p)
    need(method == 'POST' and path == _path(attempt_id, nested_family), '核对路径或方法不符', 422)
    _nonce(db, p, body['request_id'])
    rows, values, drafts, commands, histories = _inventory(db, p, write=True, target_attempt=attempt_id)
    _nonce_owner(db, p, body['request_id'])
    prior = _replay(rows, values, attempt_id, body, raw, method, path)
    if prior is not None: return prior
    reviews._iam(db, p, write=True)
    matching = [row for row in drafts.values() if row.attempt_id == attempt_id]; need(len(matching) <= 1)
    draft = matching[0] if matching else None
    current_version = draft.version if draft is not None else 0
    latest = histories[draft.id][-1] if draft is not None else None
    if (draft is None or body['expected_version'] != current_version
            or body['save_request_id'] != latest['request_id'] or body['value_hash'] != reviews.digest(draft.data['value'])):
        raise HTTPException(409, {'code': ('business_flow_nested_review_version_conflict' if nested_family
            else 'business_flow_author_review_version_conflict'),
            'not_applied': True, 'current_version': current_version})
    need(reviews._same(saved, reviews._view(draft, histories[draft.id])), '本次作者核对与真实服务器已保存稿不一致')
    original = [item for item in commands.values() if item.draft_id == draft.id
        and item.draft_version == current_version and item.request_id == body['save_request_id']]
    need(len(original) == 1 and len(rows) < MAX_RECORDS)
    command = original[0]
    _family_value(draft.data['value'], draft.data['source_binding'], body, nested_family, code=422)
    source = _source(source_snapshot, attempt_id, draft.data['source_binding'], body['value_hash'],
        review_mode=body['review_mode'] if nested_family else None)
    record = {'schema': nested.SNAPSHOT if nested_family else SNAPSHOT,
        'draft_version': current_version, 'save_request_id': command.request_id,
        'save_command_hash': command.command_hash, 'save_body_sha256': command.body_sha256,
        'review_record_hash': reviews.digest(command.data['record_snapshot']),
        'source_binding': copy.deepcopy(draft.data['source_binding']), 'baseline_hash': draft.baseline_hash,
        'value': copy.deepcopy(draft.data['value']), 'value_hash': body['value_hash'],
        'source_snapshot': source, 'source_snapshot_hash': reviews.digest(source),
        'ack_scope': True, 'ack_inferences': True, 'reason': body['reason'], 'human_review': 'author_self_reviewed'}
    if nested_family:
        record.update(**nested.mode_fields(body['review_mode']))
    row = Qualification(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id, attempt_id=attempt_id,
        draft_id=draft.id, save_command_id=command.id, request_id=body['request_id'], version=1,
        method=method, path=path, exact_body=raw.decode('utf-8', errors='strict'),
        body_sha256=hashlib.sha256(raw).hexdigest(), body_hash=reviews.digest(body), created_at=m.now())
    row.record_hash = reviews.digest(_pin(row, record))
    result = _result(row, record); row.data = {'schema': nested.RECORD if nested_family else SCHEMA,
        'record': record, 'result': result}
    reviews._bytes(row.data); db.add(row); db.flush()
    reviews.audit(db, p, AUDIT, row.id, _audit(row)); db.flush()
    _inventory(db, p, write=True, target_attempt=attempt_id)
    db.commit(); return copy.deepcopy(result)


def create(db, p, attempt_id, raw, *, method, path, saved, source_snapshot):
    return _create(db, p, attempt_id, raw, method=method, path=path, saved=saved,
        source_snapshot=source_snapshot, nested_family=False)


def create_nested(db, p, attempt_id, raw, *, method, path, saved, source_snapshot):
    return _create(db, p, attempt_id, raw, method=method, path=path, saved=saved,
        source_snapshot=source_snapshot, nested_family=True)


def _original_request(db, p, request_id, expected_sha256, *, nested_family):
    reviews.identity(request_id); reviews._iam(db, p)
    need(type(expected_sha256) is str and reviews.SHA.fullmatch(expected_sha256) is not None, '原核对摘要格式不符', 422)
    _nonce(db, p, request_id, shared=True)
    rows, values, _, _, _ = _inventory(db, p); _nonce_owner(db, p, request_id)
    matching = [row for row in rows.values() if row.request_id == request_id]; need(len(matching) <= 1)
    if matching:
        need(matching[0].body_sha256 == expected_sha256, '原核对正文摘要不同；原记录保留')
        need((matching[0].data['schema'] == nested.RECORD) is nested_family,
            '原核对编号属于另一明确合同；未更换或重建原记录')
    return {'schema': nested.REQUEST if nested_family else REQUEST,
        'request_id': request_id, 'found': bool(matching), 'body_sha256': expected_sha256,
        'result': copy.deepcopy(values[matching[0].id]) if matching else None}


def original_request(db, p, request_id, expected_sha256):
    return _original_request(db, p, request_id, expected_sha256, nested_family=False)


def original_nested_request(db, p, request_id, expected_sha256):
    return _original_request(db, p, request_id, expected_sha256, nested_family=True)


def get(db, p, qualification_id):
    reviews.identity(qualification_id); reviews._iam(db, p)
    _, values, _, _, _ = _inventory(db, p)
    need(qualification_id in values, '未找到当前账号的作者核对记录', 404)
    return copy.deepcopy(values[qualification_id])


def list_records(db, p, *, attempt_id=None):
    if attempt_id is not None: reviews.identity(attempt_id)
    reviews._iam(db, p); rows, values, _, _, _ = _inventory(db, p, target_attempt=attempt_id)
    return [copy.deepcopy(values[row.id]) for row in sorted(rows.values(), key=lambda row: row.id)
        if attempt_id is None or row.attempt_id == attempt_id]
