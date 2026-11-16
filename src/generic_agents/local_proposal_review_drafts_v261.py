"""Owner-private review authoring, separate from submission and paid analysis.

The caller owns commit/rollback. Read recovery checks the draft's own anchors,
not current raw/model/configuration availability. New writes require the same
original raw and request identity; a later analysis version alone is harmless.
Only the latest 64 save receipts are retained. An older stale save must fail
CAS rather than overwrite newer human input; absence is not a non-execution
proof. This module neither invokes a model nor validates/adopts a proposal.
"""
import copy
import json
import re
from datetime import timezone
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import Field, StrictInt, StrictStr, field_validator
from sqlalchemy import select, text

from .contracts import Contract, digest
from .local_proposal_review_models_v261 import LocalProposalReviewDraftV261 as Draft
from .media_daily_task_adapter import current_actor
from .models import Audit, SetupAnalysis, now
from .services import audit, require, scoped

SCHEMA = 'local-proposal-review-draft-v261'
SAVE_SCHEMA = 'local-proposal-review-draft-save-v261'
RECEIPT_SCHEMA = 'local-proposal-review-draft-save-receipt-v261'
STATE = 'local-proposal-review-draft-state-v261'
OPEN_ACTION = 'setup.local_review.draft_opened_v261'
SAVE_ACTION = 'setup.local_review.draft_saved_v261'
MAX_BYTES = 256 * 1024
MAX_COMMANDS = 64
ERROR = '私人审稿草稿或保存依据无法核对；已存人稿未被替换'
_SHA = re.compile(r'^[0-9a-f]{64}$')
_I = r'(?:0|[1-9][0-9]*)'
_DISCARD = re.compile(r'/(?:businesses|objects|relationships)/' + _I)
_CITATION = re.compile(r'/(?:businesses/' + _I + r'(?:/steps/' + _I
    + r')?|objects/' + _I + r'(?:/fields/' + _I + r')?|relationships/' + _I
    + r')/citations/' + _I)


def need(ok, message=ERROR, code=409):
    if not ok:
        raise HTTPException(code, message)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except ValueError:
        return False


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _draft_identity(p,request_id):
    # A stable lookup identity is not an authorization grant. Ownership is
    # always checked separately; residual audit rows prevent empty recreation.
    return str(uuid5(NAMESPACE_URL,'generic-agents/local-review-v261/'+p.workspace_id+'/'+p.user_id+'/'+request_id))


def _text(value, maximum=None):
    return (type(value) is str and (maximum is None or len(value) <= maximum)
        and '\x00' not in value and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))


def _json(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '草稿必须为有限且可保存的JSON内容') from None
    need(len(encoded) <= MAX_BYTES, '私人审稿草稿不得超过256KiB', 422)
    return encoded


def _value(value):
    need(type(value) is dict and set(value) == {'source', 'edits'}, '草稿只保存来源标识和编辑意图', 422)
    source = value['source']
    need(type(source) is dict and set(source) == {'analysis_id', 'source_version', 'raw_hash', 'contract_version'}
        and _uuid(source['analysis_id']) and type(source['source_version']) is int
        and source['source_version'] >= 1 and _sha(source['raw_hash'])
        and type(source['contract_version']) is int and source['contract_version'] in {4, 5},
        '草稿来源包装不符', 422)
    need(type(value['edits']) is list and len(value['edits']) <= 100, '草稿最多保存100项编辑意图', 422)
    for edit in value['edits']:
        need(type(edit) is dict and type(edit.get('op')) is str
            and edit['op'] in {'discard_item', 'replace_citation'}, '草稿编辑类型不符', 422)
        keys = {'op', 'path', 'reason'}
        if edit['op'] == 'replace_citation':
            keys |= {'material_id', 'block_id', 'start_text', 'end_text'}
        need(set(edit) == keys and _text(edit['reason'], 1000), '草稿编辑字段不符', 422)
        path = edit['path']; pattern = _DISCARD if edit['op'] == 'discard_item' else _CITATION
        need(type(path) is str and pattern.fullmatch(path) is not None
            and (source['contract_version'] == 5 or not path.startswith('/relationships/')),
            '草稿指针不在本版本允许范围', 422)
        if edit['op'] == 'replace_citation':
            need(all(edit[key] is None or _text(edit[key]) for key in ('material_id', 'block_id'))
                and all(_text(edit[key], 30) for key in ('start_text', 'end_text')),
                '引用选择可暂未填写，但不得存整段原文或未知字段', 422)
    _json(value)
    return value


class OpenInput(Contract):
    request_id: StrictStr
    source_version: StrictInt = Field(ge=1)
    raw_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')

    @field_validator('request_id')
    @classmethod
    def canonical_request(cls, value):
        if not _uuid(value):
            raise ValueError('request_id必须为标准UUID')
        return value


class SaveInput(Contract):
    version: StrictInt = Field(ge=1)
    request_id: StrictStr
    value: dict

    @field_validator('request_id')
    @classmethod
    def canonical_request(cls, value):
        if not _uuid(value):
            raise ValueError('request_id必须为标准UUID')
        return value

    @field_validator('value', mode='before')
    @classmethod
    def private_value(cls, value):
        return copy.deepcopy(_value(value))


def _body(cls, body):
    data = body.model_dump(mode='python') if isinstance(body, cls) else body
    result = cls.model_validate(data)
    _json(result.model_dump(mode='json'))
    return result


def _actor(db, p, *, write=False):
    current_actor(db, p, write=write)
    roles=('admin','operator') if write else ('admin','operator','viewer')
    require(p,*roles)


def _raw_source(db, p, source_id, *, lock=False):
    source = scoped(db, SetupAnalysis, source_id, p, lock)
    request = source.data.get('request'); raw = source.data.get('raw_response')
    need(type(request) is dict and type(request.get('contract_version')) is int
        and request['contract_version'] in {4, 5}, '来源不是已保存原答的V4/V5理解记录')
    need(type(raw) is dict and type(raw.get('value')) is dict and _sha(raw.get('hash')),
        '原理解回答尚未完整保存，不能另开或覆盖审稿草稿')
    try:
        valid = digest({key: item for key, item in raw.items() if key != 'hash'}) == raw['hash']
        request_hash = digest(request)
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        valid = False; request_hash = None
    need(valid, '原理解回答摘要无法核对，已存私人稿仍可只读恢复')
    return source, request, raw, request_hash


def _iso(value):
    need(value is not None and value.tzinfo is not None)
    return value.astimezone(timezone.utc).isoformat()


def _open_body(row):
    return {'request_id': row.open_request_id, 'source_version': row.source_version, 'raw_hash': row.raw_hash}


def _open_request_hash(p, source_id, body):
    return digest({'workspace_id': p.workspace_id, 'owner_id': p.user_id,
        'source_id': source_id, 'body': body})


def _opening(row):
    return {'draft_id': row.id, 'workspace_id': row.workspace_id, 'owner_id': row.owner_id,
        'source_id': row.source_id, 'source_version': row.source_version, 'raw_hash': row.raw_hash,
        'contract_version': row.data['value']['source']['contract_version'],
        'request_id': row.open_request_id, 'source_request_hash': row.data['source_request_hash'],
        'open_body_hash': row.data['open_body_hash'], 'open_request_hash': row.data['open_request_hash']}


def _own(db, p, draft_id, *, lock=False):
    need(_uuid(draft_id), '草稿标识不符', 422)
    query = select(Draft).where(Draft.id == draft_id, Draft.workspace_id == p.workspace_id,
        Draft.owner_id == p.user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    need(row is not None, '私人草稿不存在或不属于本人', 404)
    return row


def _checked(db, p, row):
    need(row.workspace_id == p.workspace_id and row.owner_id == p.user_id, '私人草稿不存在或不属于本人', 404)
    need(row.id==_draft_identity(p,row.open_request_id))
    data = row.data
    need(type(data) is dict and set(data) == {'schema', 'source_request_hash', 'open_body_hash',
        'open_request_hash', 'open_record_hash', 'value', 'value_hash', 'commands', 'updated_at'}
        and data['schema'] == STATE and row.status in {'editing', 'submitted'}
        and type(row.version) is int and row.version >= 1 and type(row.source_version) is int
        and row.source_version >= 1 and all(_uuid(item) for item in
            (row.id, row.workspace_id, row.owner_id, row.source_id, row.open_request_id))
        and all(_sha(data[key]) for key in ('source_request_hash', 'open_body_hash',
            'open_request_hash', 'open_record_hash', 'value_hash')) and _sha(row.raw_hash))
    _value(data['value'])
    need(data['value']['source'] == {'analysis_id': row.source_id, 'source_version': row.source_version,
        'raw_hash': row.raw_hash, 'contract_version': data['value']['source']['contract_version']}
        and digest(data['value']) == data['value_hash']
        and digest(_open_body(row)) == data['open_body_hash']
        and _open_request_hash(p, row.source_id, _open_body(row)) == data['open_request_hash']
        and digest(_opening(row)) == data['open_record_hash']
        and _iso(row.updated_at) == data['updated_at'])
    opened = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.action == OPEN_ACTION).limit(2)))
    need(len(opened) == 1 and opened[0].actor_id == row.owner_id and opened[0].data == {
        'draft_id': row.id, 'version': 1, 'hash': data['open_record_hash']})
    commands = data['commands']
    need(type(commands) is list and len(commands) <= MAX_COMMANDS)
    seen = set(); versions = []; receipts = {}
    receipt_keys = {'schema', 'draft_id', 'workspace_id', 'owner_id', 'request_id',
        'body_hash', 'version_before', 'version_after', 'value_hash', 'receipt_hash'}
    for command in commands:
        need(type(command) is dict and set(command) == {'request_id', 'body_hash', 'receipt'}
            and _uuid(command['request_id']) and command['request_id'] not in seen
            and _sha(command['body_hash']))
        receipt = command['receipt']
        need(type(receipt) is dict and set(receipt) == receipt_keys
            and receipt['schema'] == RECEIPT_SCHEMA and receipt['draft_id'] == row.id
            and receipt['workspace_id'] == row.workspace_id and receipt['owner_id'] == row.owner_id
            and receipt['request_id'] == command['request_id'] and receipt['body_hash'] == command['body_hash']
            and type(receipt['version_before']) is int and receipt['version_before'] >= 1
            and type(receipt['version_after']) is int and receipt['version_after'] == receipt['version_before'] + 1
            and receipt['version_after'] <= row.version and _sha(receipt['value_hash'])
            and digest({key: item for key, item in receipt.items() if key != 'receipt_hash'}) == receipt['receipt_hash'])
        seen.add(command['request_id']); versions.append(receipt['version_after'])
        receipts[receipt['version_after']] = receipt
    if commands:
        need(versions == list(range(versions[0], versions[-1] + 1))
            and (row.status == 'submitted' or versions[-1] == row.version)
            and commands[-1]['receipt']['value_hash'] == data['value_hash'])
        recent = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == row.id, Audit.action == SAVE_ACTION,
            Audit.data['version'].as_integer() <= row.version)
            .order_by(Audit.data['version'].as_integer().desc(), Audit.id.desc()).limit(MAX_COMMANDS + 1)))
        observed = {}
        for record in recent:
            details = record.data
            need(type(details) is dict and set(details) == {'draft_id', 'version', 'hash'}
                and details['draft_id'] == row.id and type(details['version']) is int
                and details['version'] >= 2 and _sha(details['hash']) and record.actor_id == row.owner_id)
            if details['version'] >= versions[0]:
                need(details['version'] in receipts and details['version'] not in observed
                    and details['hash'] == receipts[details['version']]['receipt_hash'])
                observed[details['version']] = details['hash']
        need(set(observed) == set(receipts))
    else:
        need(row.version == 1 and row.status == 'editing')
        need(db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == row.id, Audit.action == SAVE_ACTION).limit(1)) is None)
    return row


def _view(row):
    return copy.deepcopy({'schema': SCHEMA, 'id': row.id, 'version': row.version,
        'workspace_id': row.workspace_id, 'owner_id': row.owner_id, 'source_id': row.source_id,
        'source_version': row.source_version, 'raw_hash': row.raw_hash,
        'open_request_id': row.open_request_id, 'status': row.status,
        'created_at': _iso(row.created_at), 'updated_at': _iso(row.updated_at),
        'value': row.data['value'], 'value_hash': row.data['value_hash'],
        'source_state': 'not_revalidated', 'can_submit': False})


def _prior_open(db, p, request_id):
    row=db.scalar(select(Draft).where(Draft.workspace_id == p.workspace_id,
        Draft.owner_id == p.user_id, Draft.open_request_id == request_id))
    identity=_draft_identity(p,request_id)
    if row is None:
        residue=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
            Audit.object_id==identity).limit(1))
        need(residue is None,'原开稿申请仍有保存痕迹，但原人稿记录缺失；未重新创建空稿')
    else:need(row.id==identity)
    return row


def open_draft(db, p, source_id, body):
    body = _body(OpenInput, body); _actor(db, p, write=True)
    need(_uuid(source_id), '来源标识不符', 422)
    payload = body.model_dump(mode='json'); request_hash = _open_request_hash(p, source_id, payload)
    row = _prior_open(db, p, body.request_id)
    if row is not None:
        _checked(db, p, row)
        need(row.data['open_request_hash'] == request_hash, '同一开稿原号不能改成另一来源或正文')
        return _view(row)
    # The source-row lock below serializes the normal same-source case. This
    # owner-scoped nonce lock also serializes conflicting cross-source bodies.
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest({
        'local_review_open_v261': p.workspace_id, 'owner_id': p.user_id,
        'request_id': body.request_id})[:15], 16)})
    row = _prior_open(db, p, body.request_id)
    if row is not None:
        _checked(db, p, row)
        need(row.data['open_request_hash'] == request_hash, '同一开稿原号不能改成另一来源或正文')
        return _view(row)
    source, request, raw, source_request_hash = _raw_source(db, p, source_id, lock=True)
    row = _prior_open(db, p, body.request_id)
    if row is not None:
        _checked(db, p, row)
        need(row.data['open_request_hash'] == request_hash, '同一开稿原号不能改成另一来源或正文')
        return _view(row)
    need(source.version == body.source_version and raw['hash'] == body.raw_hash,
        '原理解版本或原答摘要已变化，请保留原开稿申请后核对')
    stamp = now()
    value = {'source': {'analysis_id': source.id, 'source_version': source.version,
        'raw_hash': raw['hash'], 'contract_version': request['contract_version']}, 'edits': []}
    row = Draft(id=_draft_identity(p,body.request_id),workspace_id=p.workspace_id, owner_id=p.user_id, source_id=source.id,
        source_version=source.version, raw_hash=raw['hash'], open_request_id=body.request_id,
        status='editing', version=1, created_at=stamp, updated_at=stamp, data={})
    db.add(row); db.flush()
    row.data = {'schema': STATE, 'source_request_hash': source_request_hash,
        'open_body_hash': digest(payload), 'open_request_hash': request_hash,
        'open_record_hash': '', 'value': value, 'value_hash': digest(value), 'commands': [],
        'updated_at': _iso(stamp)}
    row.data = {**row.data, 'open_record_hash': digest(_opening(row))}
    audit(db, p, OPEN_ACTION, row.id, {'draft_id': row.id, 'version': 1, 'hash': row.data['open_record_hash']})
    db.flush()
    return _view(_checked(db, p, row))


def get_draft(db, p, draft_id):
    _actor(db, p)
    return _view(_checked(db, p, _own(db, p, draft_id)))


def list_drafts(db, p, source_id, limit=20):
    _actor(db, p)
    need(_uuid(source_id) and type(limit) is int and 1 <= limit <= 20, '来源标识或列表上限不符', 422)
    rows = list(db.scalars(select(Draft).where(Draft.workspace_id == p.workspace_id,
        Draft.owner_id == p.user_id, Draft.source_id == source_id)
        .order_by(Draft.updated_at.desc(), Draft.id.desc()).limit(limit)))
    if not rows:
        need(db.scalar(select(SetupAnalysis.id).where(SetupAnalysis.workspace_id == p.workspace_id,
            SetupAnalysis.id == source_id)) is not None, '来源不存在或不在当前工作区', 404)
    return [_view(_checked(db, p, row)) for row in rows]


def _save_reply(row, receipt):
    return {'schema': SAVE_SCHEMA, 'save_receipt': copy.deepcopy(receipt), 'current_draft': _view(row),
        'current_matches': row.version == receipt['version_after'] and row.data['value_hash'] == receipt['value_hash']}


def save_draft(db, p, draft_id, body):
    body = _body(SaveInput, body); _actor(db, p, write=True)
    row = _checked(db, p, _own(db, p, draft_id, lock=True))
    payload = body.model_dump(mode='json'); body_hash = digest(payload)
    for command in row.data['commands']:
        if command['request_id'] == body.request_id:
            need(command['body_hash'] == body_hash, '同一保存原号不能更换正文')
            return _save_reply(row, command['receipt'])
    need(row.status == 'editing', '已提交草稿保留只读，请明确另开审稿')
    need(row.version == body.version, '草稿已更新或旧保存回执超出最近64次范围；未覆盖已存人稿')
    need(body.value['source'] == row.data['value']['source'], '不能把私人草稿换绑到另一原答')
    source, request, raw, request_hash = _raw_source(db, p, row.source_id)
    need(source.id == row.source_id and raw['hash'] == row.raw_hash
        and request_hash == row.data['source_request_hash']
        and request['contract_version'] == row.data['value']['source']['contract_version'],
        '原答或原请求身份已变化；私人旧稿仍可只读恢复')
    value = copy.deepcopy(body.value); value_hash = digest(value)
    receipt = {'schema': RECEIPT_SCHEMA, 'draft_id': row.id, 'workspace_id': p.workspace_id,
        'owner_id': p.user_id, 'request_id': body.request_id, 'body_hash': body_hash,
        'version_before': row.version, 'version_after': row.version + 1, 'value_hash': value_hash}
    receipt['receipt_hash'] = digest(receipt)
    row.version += 1; row.updated_at = now()
    row.data = {**row.data, 'value': value, 'value_hash': value_hash,
        'updated_at': _iso(row.updated_at), 'commands': [*row.data['commands'],
            {'request_id': body.request_id, 'body_hash': body_hash, 'receipt': receipt}][-MAX_COMMANDS:]}
    audit(db, p, SAVE_ACTION, row.id, {'draft_id': row.id, 'version': row.version, 'hash': receipt['receipt_hash']})
    db.flush()
    _checked(db, p, row)
    return _save_reply(row, receipt)


def lookup_open(db,p,source_id,request_id,expected_body_hash):
    _actor(db,p)
    need(_uuid(source_id) and _uuid(request_id) and _sha(expected_body_hash),'原申请查询格式不符',422)
    row=_prior_open(db,p,request_id)
    if row is not None:
        _checked(db,p,row)
        need(row.source_id==source_id and row.data['open_body_hash']==expected_body_hash,'原开稿申请的来源或正文不匹配')
    return {'schema':'local-proposal-review-open-lookup-v261','found':row is not None,
            'draft':_view(row) if row else None,'absence_is_non_execution':False}


def lookup_save(db,p,draft_id,request_id,expected_body_hash,expected_version):
    _actor(db,p)
    need(_uuid(request_id) and _sha(expected_body_hash) and type(expected_version) is int and expected_version>=1,
         '原保存申请查询格式不符',422)
    row=_checked(db,p,_own(db,p,draft_id,lock=True))
    command=next((x for x in row.data['commands'] if x['request_id']==request_id),None)
    if command is not None:
        need(command['body_hash']==expected_body_hash and command['receipt']['version_before']==expected_version,
             '原保存申请的版本或正文不匹配')
    return {'schema':'local-proposal-review-save-lookup-v261','found':command is not None,
        'save_reply':_save_reply(row,command['receipt']) if command else None,
        'current_draft':_view(row),'version_state':'same' if row.version==expected_version else
            'advanced' if row.version>expected_version else 'ahead','absence_is_non_execution':False}
