"""Private unsent preparation; independent of paid understanding and cloud input."""
import copy
import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .contracts import Contract, digest
from .db import session
from .models import Capability, SetupMaterial, SetupUnderstandingPreparation, now, uid
from .security import Principal, authenticated, require
from .services import audit


router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
SCHEMA = 'understanding-preparation-save-v1'
EMPTY = {'note': '', 'mode': 'understand', 'model_id': '', 'material_refs': []}
RECEIPT_KEYS = {'schema', 'request_id', 'actor_id', 'workspace_id', 'request_hash',
                'accepted_revision', 'value_hash', 'action', 'receipt_hash'}


class MaterialRef(Contract):
    id: UUID
    version: int = Field(ge=1, strict=True)
    hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)


class PreparationValue(Contract):
    note: str = Field(max_length=4000, strict=True)
    mode: Literal['understand', 'edit']
    model_id: str = Field(strict=True)
    material_refs: list[MaterialRef] = Field(max_length=50)

    @field_validator('note')
    @classmethod
    def valid_text(cls, value):
        if '\x00' in value:
            raise ValueError('说明不可包含NUL字符')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('说明包含无效Unicode') from None
        return value

    @field_validator('model_id')
    @classmethod
    def valid_model(cls, value):
        return str(UUID(value)) if value else ''

    @model_validator(mode='after')
    def distinct_materials(self):
        if len({ref.id for ref in self.material_refs}) != len(self.material_refs):
            raise ValueError('资料清单不能重复')
        return self


class SavePreparation(Contract):
    revision: int = Field(ge=0, strict=True)
    request_id: UUID
    action: Literal['save', 'replace', 'clear', 'restore']
    value: PreparationValue

    @model_validator(mode='before')
    @classmethod
    def safe_request_text(cls, value):
        # A ValidationError echoes invalid input. Reject bad strings safely before
        # FastAPI's JSON error rendering can encounter an unpaired surrogate.
        def visit(item):
            if isinstance(item, str):
                try:
                    item.encode('utf-8')
                except UnicodeEncodeError:
                    raise HTTPException(422, '准备稿包含无效Unicode') from None
                if '\x00' in item:
                    raise HTTPException(422, '准备稿不可包含NUL字符')
            elif isinstance(item, dict):
                for key, child in item.items():
                    visit(key)
                    visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)
        visit(value)
        return value

    @model_validator(mode='after')
    def explicit_clear(self):
        if self.action == 'clear' and self.value.model_dump(mode='json') != EMPTY:
            raise ValueError('清空请求必须使用完整空稿')
        return self


def require_scope(actor_id, request, p):
    if str(actor_id) != p.user_id or request.headers.get('x-workspace-id') != p.workspace_id:
        raise HTTPException(403, '准备稿账号或显式工作区与当前会话不一致')


def load_preparation(db, p, *, lock=False, create=False):
    query = select(SetupUnderstandingPreparation).where(
        SetupUnderstandingPreparation.workspace_id == p.workspace_id,
        SetupUnderstandingPreparation.actor_id == p.user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None and create:
        db.execute(insert(SetupUnderstandingPreparation).values(id=uid(),
            workspace_id=p.workspace_id, actor_id=p.user_id, version=0, data={})
            .on_conflict_do_nothing(index_elements=['workspace_id', 'actor_id']))
        row = db.scalar(query)
    return row


def corrupt():
    raise HTTPException(409, {'code': 'preparation_integrity',
                             'message': '准备稿或恢复回执无法核对；原记录保留，未覆盖'})


def checked_value(value):
    try:
        normalized = PreparationValue.model_validate(value).model_dump(mode='json')
    except (ValueError, TypeError):
        corrupt()
    if normalized != value:
        corrupt()
    return normalized


def checked_state(row, p):
    if row.workspace_id != p.workspace_id or row.actor_id != p.user_id:
        raise HTTPException(404, '准备稿不存在或不属于当前账号工作区')
    if type(row.version) is not int or row.version < 0 or not isinstance(row.data, dict):
        corrupt()
    if row.version == 0:
        if row.data != {}:
            corrupt()
        return
    data = row.data
    if (set(data) != {'value', 'value_hash', 'updated_at', 'backup', 'receipts', 'receipt_order'}
            or digest(checked_value(data.get('value'))) != data.get('value_hash')
            or not isinstance(data.get('updated_at'), str)):
        corrupt()
    backup = data['backup']
    if backup is not None:
        if (not isinstance(backup, dict) or set(backup) != {'value', 'value_hash', 'revision', 'saved_at'}
                or type(backup.get('revision')) is not int or not 1 <= backup['revision'] < row.version
                or not isinstance(backup.get('saved_at'), str)
                or digest(checked_value(backup.get('value'))) != backup.get('value_hash')):
            corrupt()
    receipts, order = data['receipts'], data['receipt_order']
    if (not isinstance(receipts, dict) or not isinstance(order, list) or len(order) > 64
            or any(not isinstance(key, str) for key in order) or len(set(order)) != len(order)
            or set(order) != set(receipts) or not order):
        corrupt()
    prior = row.version - len(order)
    for key in order:
        receipt = receipts[key]
        if (not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS
                or receipt.get('schema') != SCHEMA or receipt.get('request_id') != key
                or receipt.get('actor_id') != p.user_id or receipt.get('workspace_id') != p.workspace_id
                or receipt.get('action') not in {'save', 'replace', 'clear', 'restore'}
                or type(receipt.get('accepted_revision')) is not int
                or receipt['accepted_revision'] != prior + 1
                or any(not isinstance(receipt.get(k), str) or not re.fullmatch(r'[a-f0-9]{64}', receipt[k])
                       for k in ['request_hash', 'value_hash', 'receipt_hash'])
                or receipt['receipt_hash'] != digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})):
            corrupt()
        try:
            if str(UUID(key)) != key:
                corrupt()
        except (ValueError, TypeError):
            corrupt()
        prior = receipt['accepted_revision']
    if prior != row.version or receipts[order[-1]]['value_hash'] != data['value_hash']:
        corrupt()


def material_rows(db, p, ids):
    if not ids:
        return {}
    rows = db.scalars(select(SetupMaterial).where(SetupMaterial.workspace_id == p.workspace_id,
                                                SetupMaterial.id.in_(ids))).all()
    return {row.id: row for row in rows if row.workspace_id == p.workspace_id and row.id in ids}


def preparation_problems(db, p, value):
    problems = []
    refs = value['material_refs']
    materials = material_rows(db, p, [r['id'] for r in refs])
    for ref in refs:
        material = materials.get(ref['id'])
        base = {'kind': 'material', 'id': ref['id'], 'saved_version': ref['version']}
        if material is None:
            problems.append({**base, 'code': 'missing'})
            continue
        current = {**base, 'current_version': material.version, 'current_hash': material.sha256}
        if material.version != ref['version'] or material.sha256 != ref['hash']:
            problems.append({**current, 'code': 'changed'})
        if material.archived:
            problems.append({**current, 'code': 'archived'})
        if material.status != 'parsed':
            problems.append({**current, 'code': 'not_ready', 'status': material.status})
    if value['model_id']:
        rows = db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
            Capability.id == value['model_id'], Capability.kind == 'foundation_model',
            Capability.status == 'published')).all()
        available = any(c.id == value['model_id'] and c.workspace_id == p.workspace_id
            and c.kind == 'foundation_model' and c.status == 'published'
            and c.data.get('execution', {}).get('provider') in {'qwen', 'deepseek'}
            and c.data.get('execution', {}).get('task') != 'embedding'
            and c.data.get('data_policy', {}).get('cloud_allowed') for c in rows)
        if not available:
            problems.append({'kind': 'model', 'id': value['model_id'], 'code': 'unavailable'})
    return problems


def preparation_view(db, p, row):
    if row is None:
        return {'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'revision': 0,
                'value': None, 'value_hash': None, 'updated_at': None, 'backup': None, 'problems': []}
    checked_state(row, p)
    if row.version == 0:
        return preparation_view(db, p, None)
    return {'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'revision': row.version,
            **{key: copy.deepcopy(row.data[key]) for key in ['value', 'value_hash', 'updated_at', 'backup']},
            'problems': preparation_problems(db, p, row.data['value'])}


def request_hash(p, body):
    return digest({'scope': {'actor_id': p.user_id, 'workspace_id': p.workspace_id},
                   'body': body.model_dump(mode='json')})


def receipt_reply(db, p, row, receipt):
    current = preparation_view(db, p, row)
    return {'state': 'saved', 'receipt': copy.deepcopy(receipt), 'current': current,
            'current_matches': current['revision'] == receipt['accepted_revision']
                and current['value_hash'] == receipt['value_hash']}


def conflict(db, p, row, code, message):
    raise HTTPException(409, {'code': code, 'message': message,
                              'current': preparation_view(db, p, row)})


def validate_material_ownership(db, p, row, value, *, restoring=False):
    old = row.data['value']['material_refs'] if row.version else []
    if restoring and row.data.get('backup'):
        old = [*old, *row.data['backup']['value']['material_refs']]
    new_refs = [ref for ref in value['material_refs'] if ref not in old]
    found = material_rows(db, p, [ref['id'] for ref in new_refs])
    if any(ref['id'] not in found for ref in new_refs):
        raise HTTPException(404, '新增资料不存在或不在当前工作区；原准备稿保留')


@router.get('/understanding-preparation/{actor_id}')
def get_preparation(actor_id: UUID, request: Request, p: P, db: DB):
    require_scope(actor_id, request, p)
    return preparation_view(db, p, load_preparation(db, p))


@router.get('/understanding-preparation/{actor_id}/requests/{request_id}')
def get_preparation_receipt(actor_id: UUID, request_id: UUID, request: Request, p: P, db: DB):
    require_scope(actor_id, request, p)
    row = load_preparation(db, p)
    if row is not None:
        checked_state(row, p)
        receipt = row.data.get('receipts', {}).get(str(request_id))
        if receipt is not None:
            return receipt_reply(db, p, row, receipt)
    raise HTTPException(404, '原准备稿保存回执不存在或已超出最近64次范围；未重发')


@router.put('/understanding-preparation/{actor_id}')
def put_preparation(actor_id: UUID, body: SavePreparation, request: Request, p: P, db: DB):
    require_scope(actor_id, request, p)
    require(p, 'admin', 'operator')
    row = load_preparation(db, p, lock=True, create=True)
    if row is None:
        corrupt()
    checked_state(row, p)
    key = str(body.request_id)
    prior = row.data.get('receipts', {}).get(key)
    command_hash = request_hash(p, body)
    if prior is not None:
        if prior['request_hash'] != command_hash:
            conflict(db, p, row, 'request_conflict', '原保存编号已用于不同内容；两份输入保留')
        answer = receipt_reply(db, p, row, prior)
        return answer
    if row.version != body.revision:
        conflict(db, p, row, 'revision_conflict', '准备稿已被另一页面保存；请保留两稿并明确选择')
    value = body.value.model_dump(mode='json')
    backup = copy.deepcopy(row.data.get('backup'))
    if body.action == 'restore' and (backup is None or value != backup['value']):
        conflict(db, p, row, 'restore_conflict', '恢复内容与当前保留的备份不一致；两稿保留')
    validate_material_ownership(db, p, row, value, restoring=body.action == 'restore')
    if row.version and body.action in {'replace', 'clear', 'restore'}:
        if body.action != 'clear' or row.data['value'] != EMPTY:
            backup = {'value': copy.deepcopy(row.data['value']), 'value_hash': row.data['value_hash'],
                      'revision': row.version, 'saved_at': row.data['updated_at']}
    version = row.version + 1
    value_hash = digest(value)
    receipt = {'schema': SCHEMA, 'request_id': key, 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'request_hash': command_hash,
        'accepted_revision': version, 'value_hash': value_hash, 'action': body.action}
    receipt['receipt_hash'] = digest(receipt)
    order = [*row.data.get('receipt_order', []), key][-64:]
    receipts = {**row.data.get('receipts', {}), key: receipt}
    row.version = version
    row.data = {'value': value, 'value_hash': value_hash, 'updated_at': now().isoformat(),
                'backup': backup, 'receipt_order': order, 'receipts': {k: receipts[k] for k in order}}
    audit(db, p, 'setup.understanding.preparation.' + body.action, row.id,
          {'request_id': key, 'revision': version, 'note_chars': len(value['note']),
           'material_count': len(value['material_refs']), 'action': body.action})
    answer = receipt_reply(db, p, row, receipt)
    db.commit()
    return answer
