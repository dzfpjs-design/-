"""Durable, nonce-fenced review/apply/undo of typed configuration edits."""
import copy
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy.dialects.postgresql import insert

from .contracts import Contract, digest
from .models import SetupAnalysis, SetupRevision, uid, now
from .services import scoped, require, check_version, audit
from .setup_api import router, P, DB, draft, validate_configuration


class EditCommand(Contract):
    version: int = Field(ge=1, strict=True)
    request_id: UUID
    result_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class EditReview(EditCommand):
    operation_ids: list[str] = Field(max_length=80)

    @model_validator(mode='after')
    def unique_operations(self):
        if len(set(self.operation_ids)) != len(self.operation_ids):
            raise ValueError('同一项修改不能重复选择')
        return self


class EditApply(EditReview):
    revision: int = Field(ge=1, strict=True)


class EditUndo(EditCommand):
    revision: int = Field(ge=1, strict=True)


def command_record(row, action, body):
    """Look up the original command before CAS; never retry it as a new edit."""
    if row.data['request'].get('contract_version') != 6:
        raise HTTPException(409, '该记录不是逐项修改建议，请使用原版本审核入口')
    if (not row.data.get('result') or digest(row.data['result']) != row.data.get('result_hash')
            or body.result_hash != row.data['result_hash']):
        raise HTTPException(409, '修改建议版本已变化，原配置保留，请核对原记录')
    key = str(body.request_id)
    old = row.data.get('edit_commands', {}).get(key)
    if old:
        if old.get('action') != action or old.get('command_hash') != digest(body.model_dump(mode='json')):
            raise HTTPException(409, '此操作编号已用于不同请求，请先核对原操作回执')
        receipt = old.get('receipt')
        if not receipt or receipt not in row.data.get('edit_receipts', []):
            raise HTTPException(409, '原操作回执无法核对，不自动重复修改')
        return receipt
    if len(row.data.get('edit_commands', {})) >= 1000:
        raise HTTPException(409, '此建议已保存大量审核操作，请保留原记录并基于当前配置建立新建议')
    return None


def save_receipt(row, action, body, revision, *, status, actor_id, operation_ids=()):
    receipt = {'request_id': str(body.request_id), 'action': action, 'status': status,
               'revision': revision, 'result_hash': body.result_hash,
               'operation_ids': list(operation_ids)}
    key = str(body.request_id)
    row.data = {**row.data,
                'edit_receipts': [*row.data.get('edit_receipts', []), receipt],
                'edit_commands': {**row.data.get('edit_commands', {}), key: {
                    'action': action, 'command_hash': digest(body.model_dump(mode='json')),
                    'actor_id': actor_id, 'receipt': receipt}}}
    return receipt


def selected_operations(row, operation_ids):
    allowed = {op['id'] for op in row.data['result']['operations']}
    if len(operation_ids) != len(set(operation_ids)) or set(operation_ids) - allowed:
        raise HTTPException(422, '请选择原建议中实际存在的修改项')


def reply(db, p, row, receipt):
    from .setup_understanding import view
    # A replay may follow later human edits. Return the current draft, never the
    # old applied payload, so recovery cannot roll the UI back silently.
    config = draft(db, p, True)
    result = {'analysis': view(row), 'revision': config.version,
              'configuration': copy.deepcopy(config.data), 'receipt': receipt}
    db.commit()
    return result


@router.post('/understanding/{object_id}/edits/review')
def review_edits(object_id: str, body: EditReview, p: P, db: DB):
    from .setup_understanding import current, view
    require(p, 'admin', 'operator')
    row = scoped(db, SetupAnalysis, object_id, p, True)
    old = command_record(row, 'review', body)
    if old:
        db.commit()
        return view(row)
    check_version(row, body.version)
    if row.status != 'review':
        raise HTTPException(409, '此修改建议当前不能保存审核选择')
    row, config, _ = current(db, p, row)
    selected_operations(row, body.operation_ids)
    row.data = {**row.data, 'review_selection': {
        'operation_ids': body.operation_ids, 'result_hash': body.result_hash,
        'actor_id': p.user_id, 'saved_at': now().isoformat()}}
    save_receipt(row, 'review', body, config.version, status='review', actor_id=p.user_id, operation_ids=body.operation_ids)
    row.version += 1
    audit(db, p, 'setup.edits.review', row.id, {'operation_ids': body.operation_ids})
    db.commit()
    return view(row)


@router.post('/understanding/{object_id}/edits/apply')
def apply_edits(object_id: str, body: EditApply, p: P, db: DB):
    from .setup_edit_operations import apply_edits as patch, review_edits as explain
    from .setup_understanding import current
    require(p, 'admin', 'operator')
    row = scoped(db, SetupAnalysis, object_id, p, True)
    old = command_record(row, 'apply', body)
    if old:
        return reply(db, p, row, old)
    check_version(row, body.version)
    if row.status != 'review':
        raise HTTPException(409, '此修改建议当前不能应用')
    row, config, _ = current(db, p, row)
    check_version(config, body.revision)
    selected_operations(row, body.operation_ids)
    if not body.operation_ids:
        raise HTTPException(422, '请先选择需要应用的修改项')
    saved = row.data.get('review_selection', {})
    if saved.get('operation_ids') != body.operation_ids or saved.get('result_hash') != body.result_hash:
        raise HTTPException(409, '请先保存这份建议的逐项审核选择，再应用同一份选择')
    req = row.data['request']
    blocks = [b for chunk in req['chunks'] for b in chunk]
    before = copy.deepcopy(config.data)
    try:
        rows = explain(before, row.data['result'], req['edit_handles'], req['request_id'], blocks)
        if rows != row.data.get('edit_review'):
            raise HTTPException(409, '修改差异与保存的原建议不一致，不能应用')
        value, _ = patch(before, row.data['result'], req['edit_handles'], body.operation_ids, req['request_id'], blocks)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    from .setup_automation_instruction_guard import protect_automation_instruction_transition
    protect_automation_instruction_transition(config.data,value)
    validate_configuration(value)
    changed = digest(value) != digest(before)
    if changed:
        db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id,
            draft_id=config.id, version=config.version, data=before)
            .on_conflict_do_nothing(index_elements=['draft_id', 'version']))
        config.data = value
        config.version += 1
        db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=config.id,
                             version=config.version, data=value))
    row.data = {**row.data, 'before_configuration': before, 'applied_hash': digest(value),
                'applied_revision': config.version, 'edit_noop': not changed,
                'applied_operation_ids': body.operation_ids}
    row.status = 'applied'
    row.version += 1
    receipt = save_receipt(row, 'apply', body, config.version,
                           status='applied' if changed else 'noop', actor_id=p.user_id, operation_ids=body.operation_ids)
    audit(db, p, 'setup.edits.apply', row.id,
          {'revision': config.version, 'operation_ids': body.operation_ids, 'changed': changed})
    return reply(db, p, row, receipt)


@router.post('/understanding/{object_id}/edits/undo')
def undo_edits(object_id: str, body: EditUndo, p: P, db: DB):
    from .setup_automation import synchronize_reviews
    require(p, 'admin', 'operator')
    row = scoped(db, SetupAnalysis, object_id, p, True)
    old = command_record(row, 'undo', body)
    if old:
        return reply(db, p, row, old)
    check_version(row, body.version)
    config = draft(db, p, True)
    check_version(config, body.revision)
    if (row.status != 'applied' or row.data.get('edit_noop')
            or config.version != row.data.get('applied_revision')
            or digest(config.data) != row.data.get('applied_hash')):
        raise HTTPException(409, '应用后已有其他修改或本次没有实际变更，不能直接撤销覆盖；原版本保留在配置历史')
    value = synchronize_reviews(config.data, copy.deepcopy(row.data['before_configuration']))
    from .setup_automation_instruction_guard import protect_automation_instruction_transition
    protect_automation_instruction_transition(config.data,value)
    validate_configuration(value)
    config.data = value
    config.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=config.id,
                         version=config.version, data=value))
    row.status = 'undone'
    row.version += 1
    receipt = save_receipt(row, 'undo', body, config.version, status='undone', actor_id=p.user_id,
                           operation_ids=row.data.get('applied_operation_ids', []))
    audit(db, p, 'setup.edits.undo', row.id, {'revision': config.version})
    return reply(db, p, row, receipt)
