"""Private question drafts and immutable, explicitly authorized v5 followups.

Legacy understanding is imported only inside handlers. Saving or reading drafts
does not consult a model, reserve a budget, create a cloud call or dispatch work.
"""
import copy
import json
import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .contracts import Contract, digest
from .db import session
from .models import SetupAnalysis, SetupClarificationDraft, SetupClarificationFollowup, now, uid
from .security import Principal, authenticated, require
from .services import audit
from .understanding_preparation import MaterialRef, preparation_problems, material_rows, require_scope


router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
BASE = '/clarifications/{actor_id}/{analysis_id}/{result_hash}'
SAVE_SCHEMA = 'clarification-draft-save-v1'
MARKER_SCHEMA = 'clarification-input-v1'
HEX = r'^[a-f0-9]{64}$'


def legacy():
    from . import setup_understanding
    return setup_understanding


def fail(code, message, status=409, **extra):
    raise HTTPException(status, {'code': code, 'message': message, **extra})


def safe_strings(value):
    if isinstance(value, str):
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise HTTPException(422, '逐题草稿包含无效Unicode') from None
        if '\x00' in value:
            raise HTTPException(422, '逐题草稿不可包含NUL字符')
    elif isinstance(value, dict):
        for key, child in value.items():
            safe_strings(key)
            safe_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            safe_strings(child)
    return value


class SafeContract(Contract):
    @model_validator(mode='before')
    @classmethod
    def valid_strings(cls, value):
        return safe_strings(value)


class Answer(SafeContract):
    question_id: str = Field(pattern=HEX, strict=True)
    text: str = Field(max_length=4000, strict=True)
    include: bool = Field(strict=True)


class ClarificationValue(SafeContract):
    answers: list[Answer] = Field(max_length=30)
    note: str = Field(max_length=4000, strict=True)
    model_id: str = Field(strict=True)
    material_refs: list[MaterialRef] = Field(max_length=50)

    @field_validator('model_id')
    @classmethod
    def valid_model(cls, value):
        return str(UUID(value)) if value else ''

    @model_validator(mode='after')
    def unique_and_bounded(self):
        if len({r.id for r in self.material_refs}) != len(self.material_refs):
            raise ValueError('资料清单不能重复')
        if len(json.dumps(self.model_dump(mode='json'), ensure_ascii=False,
                          separators=(',', ':')).encode()) > 2 * 1024 * 1024:
            raise HTTPException(413, '逐题草稿超过2MiB；原稿保留，未截断')
        return self


class DraftCommand(SafeContract):
    revision: int = Field(ge=0, strict=True)
    request_id: UUID
    action: Literal['save', 'replace', 'clear', 'restore']
    value: ClarificationValue


class PreviewCommand(SafeContract):
    revision: int = Field(ge=1, strict=True)
    draft_revision: int = Field(ge=1, strict=True)
    value_hash: str = Field(pattern=HEX, strict=True)
    request_id: UUID


class StartCommand(SafeContract):
    preview_hash: str = Field(pattern=HEX, strict=True)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError('启动必须明确同意本次发送')
        return value


def scope(actor_id, analysis_id, result_hash, request, p):
    require_scope(actor_id, request, p)
    if not re.fullmatch(HEX, result_hash):
        raise HTTPException(422, '原分析结果摘要格式不正确')
    return str(analysis_id), result_hash


def source_scope(p, source):
    return {'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'source_hash': source['hash']}


def command_hash(p, source, body):
    return digest({'scope': source_scope(p, source), 'body': body.model_dump(mode='json')})


def start_hash(p, source, request_id, body):
    return digest({'kind': 'clarification', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
                   'source_hash': source['hash'], 'request_id': str(request_id),
                   'body': body.model_dump(mode='json')})


def empty_value(source):
    return {'answers': [{'question_id': q['id'], 'text': '', 'include': False}
                        for q in source['questions']], 'note': '', 'model_id': '', 'material_refs': []}


def check_source(source):
    safe_strings(source)
    if not isinstance(source, dict) or set(source) != {'analysis_id', 'result_hash', 'questions', 'hash'}:
        fail('source_integrity', '原问题来源结构无法核对；人稿保留')
    questions = source['questions']
    if not isinstance(questions, list) or len(questions) > 30:
        fail('source_integrity', '原问题数量无法核对；人稿保留')
    for ordinal, q in enumerate(questions):
        if (not isinstance(q, dict) or set(q) != {'id', 'ordinal', 'text'}
                or type(q.get('ordinal')) is not int or q['ordinal'] != ordinal or not isinstance(q.get('text'), str)
                or q.get('id') != digest({'analysis_id': source['analysis_id'], 'result_hash': source['result_hash'],
                                         'ordinal': ordinal, 'text': q['text']})):
            fail('source_integrity', '原问题顺序或摘要无法核对；人稿保留')
    if source['hash'] != digest({k: v for k, v in source.items() if k != 'hash'}):
        fail('source_integrity', '原问题来源摘要无法核对；人稿保留')
    return source


def current_source(db, p, analysis_id, result_hash):
    parent = db.scalar(select(SetupAnalysis).where(SetupAnalysis.id == analysis_id,
                                                 SetupAnalysis.workspace_id == p.workspace_id))
    if parent is None:
        fail('source_missing', '原分析不存在或不在当前工作区', 404)
    data = parent.data
    req = data.get('request') if isinstance(data, dict) else None
    if (parent.status in {'queued', 'running'} or not isinstance(req, dict)
            or type(req.get('contract_version', 1)) is not int
            or req.get('contract_version', 1) not in {1, 2, 3, 4, 5}):
        fail('source_unavailable', '原分析尚未结束或不是逐题补答来源；已有草稿保留')
    result = data.get('result')
    if not isinstance(result, dict) or data.get('result_hash') != result_hash:
        fail('source_changed', '原分析结果已变化或不能核对；已有草稿保留')
    try:
        actual_hash = digest(result)
    except (TypeError, ValueError, UnicodeError):
        fail('source_integrity', '原分析结果结构无法核对；已有草稿保留')
    if actual_hash != result_hash:
        fail('source_changed', '原分析结果已变化或不能核对；已有草稿保留')
    questions = result.get('questions')
    if not isinstance(questions, list) or len(questions) > 30 or any(not isinstance(q, str) for q in questions):
        fail('source_integrity', '原分析问题结构不完整；已有草稿保留')
    safe_strings(questions)
    source = {'analysis_id': analysis_id, 'result_hash': result_hash, 'questions': [
        {'id': digest({'analysis_id': analysis_id, 'result_hash': result_hash, 'ordinal': i, 'text': text}),
         'ordinal': i, 'text': text} for i, text in enumerate(questions)]}
    source['hash'] = digest(source)
    defaults = empty_value(source)
    try:
        defaults.update(model_id=req.get('model_id', ''), material_refs=[
            {k: ref[k] for k in ['id', 'version', 'hash']} for ref in req.get('materials', [])])
    except (KeyError, TypeError):
        fail('source_integrity', '原分析的模型或资料选择结构无法核对；已有草稿保留')
    return source, checked_value(defaults, source)


def checked_value(value, source):
    try:
        normalized = ClarificationValue.model_validate(value).model_dump(mode='json')
    except (ValueError, TypeError, KeyError):
        fail('value_integrity', '逐题草稿结构无法核对；原稿保留')
    if normalized != value or [a['question_id'] for a in value['answers']] != [q['id'] for q in source['questions']]:
        fail('question_binding', '回答必须完整保持原问题顺序；原稿保留')
    return normalized


def load_draft(db, p, analysis_id, result_hash, *, lock=False, create=False):
    query = select(SetupClarificationDraft).where(SetupClarificationDraft.workspace_id == p.workspace_id,
        SetupClarificationDraft.actor_id == p.user_id, SetupClarificationDraft.analysis_id == analysis_id,
        SetupClarificationDraft.result_hash == result_hash)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None and create:
        source, defaults = current_source(db, p, analysis_id, result_hash)
        db.execute(insert(SetupClarificationDraft).values(id=uid(), workspace_id=p.workspace_id,
            actor_id=p.user_id, analysis_id=analysis_id, result_hash=result_hash, version=0,
            data={'source': source, 'defaults': defaults}).on_conflict_do_nothing(
                index_elements=['workspace_id', 'actor_id', 'analysis_id', 'result_hash']))
        row = db.scalar(query)
    return row


def check_draft(row, p):
    if row.actor_id != p.user_id or row.workspace_id != p.workspace_id:
        fail('draft_missing', '本人的逐题草稿不存在', 404)
    data = row.data
    if type(row.version) is not int or row.version < 0 or not isinstance(data, dict):
        fail('draft_integrity', '逐题草稿或恢复索引无法核对；原稿保留')
    source = check_source(data.get('source'))
    if source['analysis_id'] != row.analysis_id or source['result_hash'] != row.result_hash:
        fail('source_integrity', '草稿绑定的原问题无法核对')
    checked_value(data.get('defaults'), source)
    if row.version == 0:
        if set(data) != {'source', 'defaults'}:
            fail('draft_integrity', '初始草稿结构无法核对')
        return source
    if (set(data) != {'source', 'defaults', 'value', 'value_hash', 'updated_at', 'backup', 'receipts', 'receipt_order'}
            or digest(checked_value(data.get('value'), source)) != data.get('value_hash')
            or not isinstance(data.get('updated_at'), str)):
        fail('draft_integrity', '已保存草稿摘要无法核对；原稿保留')
    backup = data['backup']
    if backup is not None and (not isinstance(backup, dict)
            or set(backup) != {'value', 'value_hash', 'revision', 'saved_at'}
            or type(backup.get('revision')) is not int or not 1 <= backup['revision'] < row.version
            or digest(checked_value(backup.get('value'), source)) != backup.get('value_hash')
            or not isinstance(backup.get('saved_at'), str)):
        fail('draft_integrity', '草稿备份无法核对；原稿保留')
    order, receipts = data['receipt_order'], data['receipts']
    if (not isinstance(order, list) or not isinstance(receipts, dict) or len(order) != min(row.version, 64)
            or any(not isinstance(k, str) for k in order) or len(set(order)) != len(order) or set(order) != set(receipts)):
        fail('draft_integrity', '草稿原保存回执索引无法核对')
    keys = {'schema', 'request_id', 'actor_id', 'workspace_id', 'source_hash', 'request_hash',
            'accepted_revision', 'value_hash', 'action', 'receipt_hash'}
    for revision, key in enumerate(order, row.version - len(order) + 1):
        r = receipts[key]
        if (not isinstance(r, dict) or set(r) != keys or r.get('schema') != SAVE_SCHEMA
                or r.get('request_id') != key or any(r.get(k) != val for k, val in source_scope(p, source).items())
                or type(r.get('accepted_revision')) is not int or r['accepted_revision'] != revision
                or r.get('action') not in {'save', 'replace', 'clear', 'restore'}
                or any(not isinstance(r.get(k), str) or not re.fullmatch(HEX, r[k])
                       for k in ['request_hash', 'value_hash', 'receipt_hash'])
                or r['receipt_hash'] != digest({k: val for k, val in r.items() if k != 'receipt_hash'})):
            fail('draft_integrity', '原保存回执内容无法核对；未覆盖')
    if receipts[order[-1]]['value_hash'] != data['value_hash']:
        fail('draft_integrity', '最后保存回执与草稿不一致')
    return source


def source_problems(db, p, source):
    try:
        current, _ = current_source(db, p, source['analysis_id'], source['result_hash'])
        if current != source:
            fail('source_changed', '原问题已变化')
    except HTTPException as error:
        code = error.detail.get('code', 'source_unavailable') if isinstance(error.detail, dict) else 'source_unavailable'
        return [{'kind': 'source', 'id': source['analysis_id'], 'code': code}]
    return []


def draft_view(db, p, row, source=None, defaults=None):
    if row is not None:
        source = check_draft(row, p)
        defaults = row.data['defaults']
    value = row.data['value'] if row is not None and row.version else None
    problems = source_problems(db, p, source)
    if value is not None:
        problems += preparation_problems(db, p, value)
    return {'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'source': copy.deepcopy(source),
        'revision': row.version if row is not None else 0, 'value': copy.deepcopy(value),
        'value_hash': row.data['value_hash'] if value is not None else None,
        'updated_at': row.data['updated_at'] if value is not None else None,
        'backup': copy.deepcopy(row.data['backup']) if value is not None else None,
        'problems': problems, 'defaults': copy.deepcopy(defaults)}


def draft_reply(db, p, row, receipt):
    current = draft_view(db, p, row)
    return {'state': 'saved', 'receipt': copy.deepcopy(receipt), 'current': current,
            'current_matches': current['revision'] == receipt['accepted_revision']
                and current['value_hash'] == receipt['value_hash']}


def draft_conflict(db, p, row, code, message):
    fail(code, message, current=draft_view(db, p, row))


def material_ownership(db, p, row, value, *, restoring=False):
    old = row.data['value']['material_refs'] if row.version else []
    if restoring and row.data.get('backup'):
        old = [*old, *row.data['backup']['value']['material_refs']]
    added = [ref for ref in value['material_refs'] if ref not in old]
    found = material_rows(db, p, [ref['id'] for ref in added])
    if any(ref['id'] not in found for ref in added):
        fail('material_missing', '新增资料不存在或不在当前工作区；原稿保留', 404)


@router.get(BASE)
def get_draft(actor_id: UUID, analysis_id: UUID, result_hash: str, request: Request, p: P, db: DB):
    analysis_id, result_hash = scope(actor_id, analysis_id, result_hash, request, p)
    row = load_draft(db, p, analysis_id, result_hash)
    if row is not None:
        return draft_view(db, p, row)
    source, defaults = current_source(db, p, analysis_id, result_hash)
    return draft_view(db, p, None, source, defaults)


@router.put(BASE)
def save_draft(actor_id: UUID, analysis_id: UUID, result_hash: str, body: DraftCommand,
               request: Request, p: P, db: DB):
    analysis_id, result_hash = scope(actor_id, analysis_id, result_hash, request, p)
    require(p, 'admin', 'operator')
    row = load_draft(db, p, analysis_id, result_hash, lock=True, create=True)
    if row is None:
        fail('draft_integrity', '初始草稿无法保存')
    source = check_draft(row, p)
    key, request_hash = str(body.request_id), command_hash(p, source, body)
    prior = row.data.get('receipts', {}).get(key)
    if prior is not None:
        if prior['request_hash'] != request_hash:
            draft_conflict(db, p, row, 'request_conflict', '此保存编号已用于不同内容；两稿保留')
        return draft_reply(db, p, row, prior)
    if row.version != body.revision:
        draft_conflict(db, p, row, 'revision_conflict', '另一页面已保存；请保留两稿并明确选择')
    value = checked_value(body.value.model_dump(mode='json'), source)
    backup = copy.deepcopy(row.data.get('backup'))
    if body.action == 'clear' and value != empty_value(source):
        fail('clear_conflict', '清空须完整保留原问题并清空本次输入')
    if body.action == 'restore' and (backup is None or value != backup['value']):
        draft_conflict(db, p, row, 'restore_conflict', '恢复内容与保留备份不一致')
    material_ownership(db, p, row, value, restoring=body.action == 'restore')
    if row.version and body.action in {'replace', 'clear', 'restore'}:
        if body.action != 'clear' or row.data['value'] != empty_value(source):
            backup = {'value': copy.deepcopy(row.data['value']), 'value_hash': row.data['value_hash'],
                      'revision': row.version, 'saved_at': row.data['updated_at']}
    revision = row.version + 1
    receipt = {'schema': SAVE_SCHEMA, 'request_id': key, **source_scope(p, source),
        'request_hash': request_hash, 'accepted_revision': revision, 'value_hash': digest(value), 'action': body.action}
    receipt['receipt_hash'] = digest(receipt)
    order = [*row.data.get('receipt_order', []), key][-64:]
    receipts = {**row.data.get('receipts', {}), key: receipt}
    row.version = revision
    row.data = {'source': source, 'defaults': row.data['defaults'], 'value': value, 'value_hash': digest(value),
        'updated_at': now().isoformat(), 'backup': backup, 'receipt_order': order,
        'receipts': {k: receipts[k] for k in order}}
    audit(db, p, 'setup.clarification.draft.' + body.action, row.id,
          {'request_id': key, 'revision': revision, 'answer_count': len(value['answers']),
           'included_count': sum(a['include'] for a in value['answers']), 'action': body.action})
    answer = draft_reply(db, p, row, receipt)
    db.commit()
    return answer


@router.get(BASE + '/requests/{request_id}')
def get_draft_receipt(actor_id: UUID, analysis_id: UUID, result_hash: str, request_id: UUID,
                      request: Request, p: P, db: DB):
    analysis_id, result_hash = scope(actor_id, analysis_id, result_hash, request, p)
    row = load_draft(db, p, analysis_id, result_hash)
    if row is not None:
        check_draft(row, p)
        receipt = row.data.get('receipts', {}).get(str(request_id))
        if receipt is not None:
            return draft_reply(db, p, row, receipt)
    raise HTTPException(404, '原草稿保存回执不存在或已超出最近64次范围；未重发')


def authorization_roots(db, p, request_id):
    rows = db.scalars(select(SetupAnalysis).where(SetupAnalysis.workspace_id == p.workspace_id,
        SetupAnalysis.data['request']['request_id'].astext == str(request_id))).all()
    roots = [row for row in rows if not row.data.get('reused_from')]
    if len(roots) > 1:
        fail('nonce_ambiguous', '原启动编号对应多个真实授权；未重发')
    return roots, rows


def lock_parent(db, p, analysis_id):
    # Legacy apply/reuse locks analysis before workspace draft. New authorization
    # uses that same order; recovered nonces never need fresh parent locks.
    parent = db.scalar(select(SetupAnalysis).where(SetupAnalysis.id == analysis_id,
        SetupAnalysis.workspace_id == p.workspace_id).with_for_update().execution_options(populate_existing=True))
    if parent is None:
        fail('source_missing', '原分析不存在；人稿和原发送快照保留', 404)


def followup_query(p, analysis_id=None, result_hash=None, request_id=None):
    query = select(SetupClarificationFollowup).where(SetupClarificationFollowup.workspace_id == p.workspace_id,
                                                   SetupClarificationFollowup.actor_id == p.user_id)
    if analysis_id is not None:
        query = query.where(SetupClarificationFollowup.analysis_id == analysis_id,
                            SetupClarificationFollowup.result_hash == result_hash)
    if request_id is not None:
        query = query.where(SetupClarificationFollowup.request_id == str(request_id))
    return query


def load_followup(db, p, analysis_id, result_hash, request_id, *, lock=False):
    query = followup_query(p, analysis_id, result_hash, request_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, '本人的逐题发送快照不存在')
    check_followup(row, p)
    return row


def check_followup(row, p):
    if row.actor_id != p.user_id or row.workspace_id != p.workspace_id:
        raise HTTPException(404, '本人的逐题发送快照不存在')
    data = row.data
    if not isinstance(data, dict) or set(data) != {'snapshot', 'snapshot_hash', 'preview_command', 'preview_command_hash', 'start'}:
        fail('followup_integrity', '逐题发送快照结构无法核对；未重发')
    snap = data['snapshot']
    if (not isinstance(snap, dict) or set(snap) != {'request_id', 'source', 'draft_revision', 'value_hash', 'value', 'cloud_answers', 'preview', 'body'}
            or not isinstance(snap.get('preview'), dict) or not isinstance(snap.get('body'), dict)
            or digest(snap) != data['snapshot_hash'] or snap.get('request_id') != row.request_id):
        fail('followup_integrity', '逐题发送快照摘要无法核对；未重发')
    source = check_source(snap['source'])
    if source['analysis_id'] != row.analysis_id or source['result_hash'] != row.result_hash:
        fail('followup_integrity', '发送快照的父源无法核对')
    checked_value(snap['value'], source)
    if digest(snap['value']) != snap['value_hash']:
        fail('followup_integrity', '发送快照的人稿摘要无法核对')
    try:
        command = PreviewCommand.model_validate(data['preview_command'])
    except (ValueError, TypeError):
        fail('followup_integrity', '原预览命令无法核对')
    if (command_hash(p, source, command) != data['preview_command_hash']
            or str(command.request_id) != row.request_id or command.draft_revision != snap['draft_revision']
            or command.value_hash != snap['value_hash']
            or snap['preview'].get('preview_hash') != digest({k: v for k, v in snap['preview'].items() if k != 'preview_hash'})):
        fail('followup_integrity', '原预览命令与快照不一致')
    expected_body = {'revision': command.revision, 'model_id': snap['value']['model_id'],
        'material_ids': [ref['id'] for ref in snap['value']['material_refs']], 'instruction': snap['value']['note'],
        'contract_version': 5, 'request_id': row.request_id}
    materials = snap['preview'].get('materials')
    if not isinstance(materials, list) or any(not isinstance(ref, dict) or not {'id', 'version', 'hash'} <= set(ref) for ref in materials):
        fail('followup_integrity', '原资料版本快照结构无法核对')
    selected = [{k: ref[k] for k in ['id', 'version', 'hash']} for ref in materials]
    if (snap['body'] != expected_body or snap['preview'].get('model_id') != snap['value']['model_id']
            or snap['preview'].get('revision') != command.revision or snap['preview'].get('contract_version') != 5
            or snap['preview'].get('request_id') != row.request_id or selected != snap['value']['material_refs']):
        fail('followup_integrity', '原命令、所选资料版本与发送快照不一致')
    selected_ordinals = [q['ordinal'] for q, a in zip(source['questions'], snap['value']['answers']) if a['include']]
    if (not isinstance(snap['cloud_answers'], list) or not selected_ordinals
            or any(not isinstance(a, dict) or set(a) != {'ordinal', 'question', 'answer'}
                   or type(a.get('ordinal')) is not int or not isinstance(a.get('question'), str)
                   or not isinstance(a.get('answer'), str) for a in snap['cloud_answers'])
            or [a['ordinal'] for a in snap['cloud_answers']] != selected_ordinals):
        fail('followup_integrity', '原问答选择与发送投影不一致')
    marker = snap['preview'].get('clarification_source')
    expected = {'schema': MARKER_SCHEMA, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'analysis_id': row.analysis_id, 'result_hash': row.result_hash, 'source_hash': source['hash'],
        'request_id': row.request_id, 'draft_revision': snap['draft_revision'], 'value_hash': snap['value_hash'],
        'answers_hash': digest(snap['value']['answers']), 'cloud_answers_hash': digest(snap['cloud_answers'])}
    if marker != expected or snap['preview'].get('context', {}).get('human_clarifications') != snap['cloud_answers']:
        fail('followup_integrity', '人答投影或父源标记无法核对')
    start = data['start']
    if type(row.version) is not int or row.version != (1 if row.status == 'prepared' else 2):
        fail('followup_integrity', '逐题发送快照版本无法核对')
    if row.status == 'prepared':
        if start is not None or row.child_id is not None:
            fail('followup_integrity', '未提交快照包含不一致的子任务')
    elif row.status == 'started':
        body = StartCommand(preview_hash=snap['preview']['preview_hash'], consent=True)
        if (not isinstance(start, dict) or set(start) != {'body', 'command_hash', 'analysis_id'}
                or start['body'] != body.model_dump(mode='json') or start['analysis_id'] != row.child_id
                or start['command_hash'] != start_hash(p, source, row.request_id, body) or not row.child_id):
            fail('followup_integrity', '已提交快照的启动证明无法核对')
    else:
        fail('followup_integrity', '逐题发送状态无法核对')
    return snap


def verified_child(db, p, row):
    snap = check_followup(row, p)
    child = db.scalar(select(SetupAnalysis).where(SetupAnalysis.id == row.child_id,
                                                SetupAnalysis.workspace_id == p.workspace_id))
    if (child is None or child.data.get('request') != snap['preview']
            or child.data.get('consent', {}).get('actor_id') != p.user_id
            or child.data['consent'].get('preview_hash') != snap['preview']['preview_hash']
            or child.data.get('start_command_hash') != row.data['start']['command_hash']
            or child.data.get('reused_from')):
        fail('child_integrity', '原真实子任务与发送快照不能对应；未重发')
    return child


def followup_view(db, p, row):
    snap = check_followup(row, p)
    body = StartCommand(preview_hash=snap['preview']['preview_hash'], consent=True)
    child = verified_child(db, p, row) if row.status == 'started' else None
    return {**copy.deepcopy(snap), 'status': row.status, 'snapshot_hash': row.data['snapshot_hash'],
        'preview_command_hash': row.data['preview_command_hash'],
        'start_command_hash': start_hash(p, snap['source'], row.request_id, body),
        'child': legacy().view(child) if child is not None else None}


def build_payload(db, p, source, draft_revision, value, configuration_revision, request_id):
    current, _ = current_source(db, p, source['analysis_id'], source['result_hash'])
    if current != source:
        fail('source_changed', '原问题来源发生变化；未发送，原人稿保留')
    checked_value(value, source)
    problems = preparation_problems(db, p, value)
    if problems:
        fail('selection_changed', '资料或模型选择需明确核对；原人稿保留', problems=problems)
    if not value['model_id']:
        fail('model_required', '请选择可用于本次理解的已发布模型；原人稿保留')
    old = legacy()
    answers = []
    for q, answer in zip(source['questions'], value['answers']):
        if answer['include']:
            if not answer['text'].strip():
                fail('answer_required', '选择本次发送的回答不能为空；草稿仍可保存', question_id=q['id'])
            answers.append({'ordinal': q['ordinal'], 'question': old.scrub(q['text']), 'answer': old.scrub(answer['text'])})
    if not answers:
        fail('answer_required', '请至少选择一项非空回答用于本次发送；草稿保留')
    body = {'revision': configuration_revision, 'model_id': value['model_id'],
        'material_ids': [ref['id'] for ref in value['material_refs']], 'instruction': value['note'],
        'contract_version': 5, 'request_id': str(request_id)}
    payload = copy.deepcopy(old.prepare(db, p, old.PreviewRequest(**body)))
    selected = [{k: ref[k] for k in ['id', 'version', 'hash']} for ref in payload.get('materials', [])]
    if selected != value['material_refs']:
        fail('selection_changed', '核对期间资料版本已变化；旧选择和草稿保留，未生成发送快照')
    payload.pop('preview_hash', None)
    payload['context'] = {**payload['context'], 'human_clarifications': answers}
    payload['clarification_source'] = {'schema': MARKER_SCHEMA, 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'analysis_id': source['analysis_id'], 'result_hash': source['result_hash'],
        'source_hash': source['hash'], 'request_id': str(request_id), 'draft_revision': draft_revision,
        'value_hash': digest(value), 'answers_hash': digest(value['answers']), 'cloud_answers_hash': digest(answers)}
    cap = old.model(db, p, value['model_id'])
    payload['input_summary'], payload['first_dispatch_messages_hash'] = old.input_summary(payload, cap)
    old.preview_budget(db, p, payload['input_summary']['token_bound'], cap=cap, detailed=True)
    payload['preview_hash'] = digest(payload)
    return payload, body, answers


@router.post(BASE + '/previews')
def preview_followup(actor_id: UUID, analysis_id: UUID, result_hash: str, body: PreviewCommand,
                     request: Request, p: P, db: DB):
    analysis_id, result_hash = scope(actor_id, analysis_id, result_hash, request, p)
    require(p, 'admin', 'operator')
    # Global nonce arbitration reads only an id. The private snapshot itself is
    # always loaded with actor + workspace + parent/result filters.
    query = select(SetupClarificationFollowup.id).where(
        SetupClarificationFollowup.workspace_id == p.workspace_id,
        SetupClarificationFollowup.request_id == str(body.request_id))
    def existing_followup(lock=False):
        if db.scalar(query) is None:
            return None
        owned = followup_query(p, analysis_id, result_hash, body.request_id)
        if lock:
            owned = owned.with_for_update().execution_options(populate_existing=True)
        found = db.scalar(owned)
        if found is None:
            fail('request_conflict', '此启动编号已绑定另一份请求；未重发')
        return found
    def recover(existing):
        if existing.actor_id != p.user_id or existing.analysis_id != analysis_id or existing.result_hash != result_hash:
            fail('request_conflict', '此启动编号已绑定另一份请求；未重发')
        snap = check_followup(existing, p)
        if command_hash(p, snap['source'], body) != existing.data['preview_command_hash']:
            fail('request_conflict', '此启动编号已用于不同预览命令；旧快照保留')
        return followup_view(db, p, existing)
    existing = existing_followup()
    if existing is not None:
        return recover(existing)
    lock_parent(db, p, analysis_id)
    legacy().draft(db, p, True)
    existing = existing_followup(lock=True)
    if existing is not None:
        return recover(existing)
    _, all_requests = authorization_roots(db, p, body.request_id)
    if all_requests:
        fail('request_conflict', '此工作区启动编号已有真实授权；请先恢复原请求，未新建理解')
    row = load_draft(db, p, analysis_id, result_hash, lock=True)
    if row is None:
        raise HTTPException(404, '请先保存本人的逐题草稿')
    source = check_draft(row, p)
    if row.version != body.draft_revision or row.data.get('value_hash') != body.value_hash:
        draft_conflict(db, p, row, 'revision_conflict', '草稿已变化；请保留输入并核对最新保存版本')
    value = copy.deepcopy(row.data['value'])
    payload, old_body, answers = build_payload(db, p, source, row.version, value, body.revision, body.request_id)
    from .report_unresolved_overlap import guard_new_paid_sources
    guard_new_paid_sources(db,p,payload['materials'])
    snapshot = {'request_id': str(body.request_id), 'source': copy.deepcopy(source), 'draft_revision': row.version,
        'value_hash': row.data['value_hash'], 'value': value, 'cloud_answers': answers, 'preview': payload, 'body': old_body}
    followup = SetupClarificationFollowup(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        analysis_id=analysis_id, result_hash=result_hash, draft_id=row.id, request_id=str(body.request_id),
        version=1, status='prepared', child_id=None, data={'snapshot': snapshot, 'snapshot_hash': digest(snapshot),
            'preview_command': body.model_dump(mode='json'), 'preview_command_hash': command_hash(p, source, body), 'start': None})
    db.add(followup)
    audit(db, p, 'setup.clarification.preview', followup.id,
          {'request_id': str(body.request_id), 'draft_revision': row.version, 'answer_count': len(answers), 'preview_hash': payload['preview_hash']})
    answer = followup_view(db, p, followup)
    db.commit()
    return answer


@router.get(BASE + '/followups')
def get_followups(actor_id: UUID, analysis_id: UUID, result_hash: str, request: Request, p: P, db: DB):
    analysis_id, result_hash = scope(actor_id, analysis_id, result_hash, request, p)
    rows = db.scalars(followup_query(p, analysis_id, result_hash).order_by(
        SetupClarificationFollowup.created_at, SetupClarificationFollowup.id)).all()
    return [followup_view(db, p, row) for row in rows]


@router.get(BASE + '/followups/{request_id}')
def get_followup(actor_id: UUID, analysis_id: UUID, result_hash: str, request_id: UUID,
                 request: Request, p: P, db: DB):
    analysis_id, result_hash = scope(actor_id, analysis_id, result_hash, request, p)
    return followup_view(db, p, load_followup(db, p, analysis_id, result_hash, request_id))


@router.post(BASE + '/followups/{request_id}/start', status_code=202)
def start_followup(actor_id: UUID, analysis_id: UUID, result_hash: str, request_id: UUID,
                   body: StartCommand, request: Request, p: P, db: DB):
    analysis_id, result_hash = scope(actor_id, analysis_id, result_hash, request, p)
    require(p, 'admin', 'operator')
    old = legacy()
    followup = load_followup(db, p, analysis_id, result_hash, request_id)
    snapshot = followup.data['snapshot']
    def recover_started():
        expected_body = StartCommand(preview_hash=followup.data['snapshot']['preview']['preview_hash'], consent=True)
        if body != expected_body:
            fail('request_conflict', '原启动正文已变化；原快照保留，未重发')
        roots, all_requests = authorization_roots(db, p, request_id)
        if followup.status != 'started':
            if all_requests:
                fail('request_conflict', '此工作区启动编号已有其他真实授权；未重发')
            return None
        child = verified_child(db, p, followup)
        if len(roots) != 1 or roots[0].id != child.id:
            fail('nonce_ambiguous', '原启动授权根与子任务不一致；未重发')
        return followup_view(db, p, followup)
    recovered = recover_started()
    if recovered is not None:
        return recovered
    lock_parent(db, p, analysis_id)
    old.draft(db, p, True)
    followup = load_followup(db, p, analysis_id, result_hash, request_id, lock=True)
    recovered = recover_started()
    if recovered is not None:
        return recovered
    snapshot = followup.data['snapshot']
    preview = snapshot['preview']
    regenerated, _, _ = build_payload(db, p, snapshot['source'], snapshot['draft_revision'], snapshot['value'],
                                       snapshot['body']['revision'], request_id)
    if regenerated != preview:
        fail('preview_changed', '配置、原资料、模型或发送投影已变化；请另行明确核对新预览，原快照保留')
    from .report_reading_drafts import guard_report_draft_pending
    guard_report_draft_pending(db, p)
    from .report_unresolved_overlap import guard_new_paid_sources
    guard_new_paid_sources(db,p,preview['materials'])
    active = db.scalar(select(SetupAnalysis.id).where(SetupAnalysis.workspace_id == p.workspace_id,
                                                   SetupAnalysis.status.in_(['queued', 'running'])))
    if active:
        fail('active_analysis', '此工作区已有理解任务；未另起理解')
    hashed = start_hash(p, snapshot['source'], request_id, body)
    child = SetupAnalysis(workspace_id=p.workspace_id, data={'request': copy.deepcopy(preview),
        'consent': {'actor_id': p.user_id, 'preview_hash': preview['preview_hash'], 'accepted_at': now().isoformat()},
        'start_command_hash': hashed, 'completed': [], 'error': None})
    db.add(child)
    db.flush()
    old.enqueue(db, p, child)
    followup.child_id, followup.status = child.id, 'started'
    followup.version += 1
    followup.data = {**followup.data, 'start': {'body': body.model_dump(mode='json'), 'command_hash': hashed, 'analysis_id': child.id}}
    audit(db, p, 'setup.clarification.start', child.id,
          {'request_id': str(request_id), 'preview_hash': preview['preview_hash'], 'parent_analysis_id': analysis_id})
    answer = followup_view(db, p, followup)
    db.commit()
    return answer


@router.get('/clarification-starts/{actor_id}/{request_id}')
def get_start_receipt(actor_id: UUID, request_id: UUID, request: Request, p: P, db: DB):
    require_scope(actor_id, request, p)
    roots, _ = authorization_roots(db, p, request_id)
    if not roots:
        raise HTTPException(404, '服务器未查到此原启动授权；未重发')
    row = roots[0]
    req = row.data.get('request', {})
    if row.data.get('consent', {}).get('actor_id') != p.user_id:
        raise HTTPException(404, '本人的原启动授权不存在')
    if (req.get('request_id') != str(request_id) or req.get('preview_hash') != digest({k: v for k, v in req.items() if k != 'preview_hash'})
            or row.data['consent'].get('preview_hash') != req.get('preview_hash')):
        fail('start_integrity', '原启动授权摘要无法核对；未重发')
    old = legacy()
    old.request_contract(req)
    if req.get('contract_version') in {3, 4, 5, 6}:
        old.checked_v3_request(req)
    marker = req.get('clarification_source')
    kind = 'normal'
    if marker is not None:
        followup = db.scalar(followup_query(p, request_id=request_id))
        if followup is None or followup.child_id != row.id or followup.status != 'started':
            fail('child_integrity', '原逐题子任务缺少对应本人快照；未重发')
        verified_child(db, p, followup)
        expected_hash, kind = followup.data['start']['command_hash'], 'clarification'
    else:
        try:
            original = {'revision': req['revision'], 'model_id': req['model_id'],
                'material_ids': [ref['id'] for ref in req['materials']], 'instruction': req['instruction'],
                'contract_version': req['contract_version'], 'request_id': req['request_id'],
                'preview_hash': req['preview_hash'], 'consent': True}
            expected_hash = digest(old.StartRequest.model_validate(original).model_dump(mode='json'))
        except (KeyError, TypeError, ValueError):
            fail('start_integrity', '原普通启动正文结构无法核对；未重发')
    stored = row.data.get('start_command_hash', expected_hash)
    if stored != expected_hash:
        fail('start_integrity', '原启动正文摘要无法核对；未重发')
    receipt = {'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'request_id': str(request_id),
        'analysis_id': row.id, 'preview_hash': req['preview_hash'], 'command_hash': stored, 'kind': kind}
    receipt['receipt_hash'] = digest(receipt)
    return receipt
