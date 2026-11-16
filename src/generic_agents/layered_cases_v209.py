"""Durable original requests, immutable revisions and independent case review."""
import copy
import hashlib
from typing import Annotated, Literal
from fastapi import HTTPException
from pydantic import Field, StrictInt, field_validator
from sqlalchemy import select, or_, text
from . import models as m, layered_case_origin_v209 as origin
from .contracts import Contract, digest
from .services import scoped, require, check_version
from .relations import lock_current_actor
from .layered_approval_contract_v175 import original_json
from .layered_case_models_v209 import LayeredCaseV209 as Case
from .layered_case_models_v209 import LayeredCaseRevisionV209 as Revision
from .layered_case_models_v209 import LayeredCaseCommandV209 as Command

PREFIX = '/api/workspace/layered-cases-v209'
COMMAND_AUDIT = 'layered_case.command_v209'
REVISION_AUDIT = 'layered_case.revision_v209'
STATES = {'draft', 'in_review', 'returned', 'withdrawn', 'approved'}
EDITABLE = {'draft', 'returned', 'withdrawn'}
ID = Annotated[str, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Positive = Annotated[StrictInt, Field(ge=1)]
need = origin.need


class Value(Contract):
    title: str = Field(strict=True, min_length=1, max_length=200)
    outcome: str = Field(strict=True, min_length=1, max_length=6000)
    experience: str = Field(strict=True, min_length=1, max_length=6000)
    applicable_scope: str = Field(strict=True, min_length=1, max_length=3000)
    caveats: str = Field(strict=True, min_length=1, max_length=3000)

    @field_validator('*')
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or '\x00' in value: raise ValueError('请填写有效的业务案例内容')
        return value


class Create(Contract):
    request_id: ID
    expected_source_task_version: Positive
    source_fingerprint: Hash
    value: Value


class Change(Contract):
    request_id: ID
    expected_case_version: Positive
    revision_id: ID
    revision_hash: Hash
    reason: str = Field(strict=True, min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or '\x00' in value: raise ValueError('请填写本次操作依据')
        return value


class Revise(Change):
    value: Value


class Review(Change):
    decision: Literal['approve', 'return']


CONTRACTS = {'create': Create, 'revise': Revise, 'submit': Change, 'withdraw': Change, 'review': Review}


def parse(raw, kind):
    need(type(raw) is bytes and 0 < len(raw) <= 65536, '案例请求超过64KiB或为空；原输入保留', 413)
    try: return original_json(raw, CONTRACTS[kind])
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '案例请求、原版本或人工值不符合固定合同；未截断或改写原请求') from None


def seal(row, data):
    value = copy.deepcopy(data)
    row.data = {**value, 'record_hash': digest(value)}


def checked(row):
    need(row is not None and type(row.data) is dict)
    value = {key: value for key, value in row.data.items() if key != 'record_hash'}
    need(digest(value) == row.data.get('record_hash'))
    return copy.deepcopy(value)


def authority(row):
    value = checked(row)
    return {'id': row.id, 'workspace_id': row.workspace_id, 'task_id': row.task_id,
        'memory_id': row.memory_id, 'author_id': row.author_id, 'source_fingerprint': row.source_fingerprint,
        'head_revision_id': row.head_revision_id, 'status': row.status, 'version': row.version,
        'creation_request_id': row.creation_request_id,
        'created_at': origin.utc(row.created_at), 'data': {k: v for k, v in value.items() if k != 'command_ids'}}


def revision_proof(row):
    return {'id': row.id, 'workspace_id': row.workspace_id, 'case_id': row.case_id,
        'actor_id': row.actor_id, 'ordinal': row.ordinal, 'parent_revision_id': row.parent_revision_id,
        'parent_revision_hash': row.parent_revision_hash, 'request_id': row.request_id,
        'created_at': origin.utc(row.created_at), 'data': copy.deepcopy(row.data)}


def event(db, p, action, identity, value, actor):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity)))
    need(len(rows) == 1 and rows[0].actor_id == actor and rows[0].data == value)
    return rows[0]


def command_audit(row):
    return {'command_id': row.id, 'case_id': row.case_id, 'request_id': row.request_id,
        'task_id': row.data['state']['task_id'],
        'operation': row.operation, 'path': row.path, 'body_sha256': row.body_sha256,
        'command_data_hash': digest(row.data)}


def checked_command(db, p, row):
    value = checked(row)
    need(row.workspace_id == p.workspace_id and row.version == 1 and row.operation in CONTRACTS
        and set(value) == {'schema', 'result', 'result_hash', 'previous_state_hash', 'state', 'state_hash'}
        and value['schema'] == 'layered-case-command-v209'
        and value['result_hash'] == digest(value['result'])
        and value['state_hash'] == digest(value['state']))
    body, _, hashed = parse(row.exact_body.encode('utf-8'), row.operation)
    need(hashed == row.body_sha256 and body.request_id == row.request_id
        and value['result']['request_id'] == row.request_id and value['result']['operation'] == row.operation
        and value['result']['case_id'] == row.case_id
        and value['result']['case_version'] == value['state']['version']
        and value['result']['status'] == value['state']['status'])
    path = (PREFIX + '/tasks/' + value['state']['task_id'] + '/cases' if row.operation == 'create'
        else PREFIX + '/cases/' + row.case_id + '/' + row.operation)
    need(row.path == path)
    event(db, p, COMMAND_AUDIT, row.id, command_audit(row), row.actor_id)
    return value


def checked_revision(db, p, row):
    need(row is not None and row.workspace_id == p.workspace_id and row.version == 1
        and row.revision_hash == digest(revision_proof(row))
        and set(row.data) == {'schema', 'value', 'source', 'source_fingerprint', 'reason'}
        and row.data['schema'] == 'layered-case-revision-v209'
        and Value.model_validate(row.data['value']).model_dump() == row.data['value']
        and row.data['source']['source_fingerprint'] == row.data['source_fingerprint'])
    event(db, p, REVISION_AUDIT, row.id, {'case_id': row.case_id, 'revision_id': row.id,
        'task_id': row.data['source']['task_id'], 'revision_hash': row.revision_hash, 'request_id': row.request_id}, row.actor_id)
    return row


def inventory(db, p, row, *, verify_source=True):
    data = checked(row)
    need(row.status in STATES and data.get('schema') == 'layered-case-state-v209'
        and data.get('status') == row.status and data.get('source_fingerprint') == row.source_fingerprint
        and data.get('head_revision_id') == row.head_revision_id
        and type(data.get('command_ids')) is list and len(set(data['command_ids'])) == len(data['command_ids']))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id, Command.case_id == row.id)))
    need(data['command_ids'] and set(data['command_ids']) == {item.id for item in commands})
    command_events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND_AUDIT, m.Audit.data['case_id'].as_string() == row.id)))
    need(len(command_events) == len(commands) and {event.object_id for event in command_events} == {item.id for item in commands},
        '案例独立操作库存与原命令不一致；未回退到旧成功状态')
    by_id = {item.id: item for item in commands}
    previous, results = None, []
    for version, identity in enumerate(data['command_ids'], 1):
        command = by_id[identity]; value = checked_command(db, p, command)
        need(value['state']['version'] == version and value['state']['id'] == row.id
            and value['state']['workspace_id'] == p.workspace_id
            and value['state']['task_id'] == row.task_id and value['state']['author_id'] == row.author_id
            and value['state']['memory_id'] == row.memory_id and value['state']['source_fingerprint'] == row.source_fingerprint
            and value['previous_state_hash'] == (digest(previous) if previous is not None else None)
            and (command.operation == 'create') is (version == 1))
        body, _, _ = parse(command.exact_body.encode(), command.operation)
        if previous is None:
            need(command.actor_id == row.author_id and command.request_id == row.creation_request_id
                and value['state']['status'] == 'draft')
        else:
            old_data, new_data = previous['data'], value['state']['data']
            need(body.expected_case_version == previous['version']
                and body.revision_id == previous['head_revision_id']
                and body.revision_hash == results[-1]['revision_hash'])
            if command.operation == 'review':
                need(command.actor_id != row.author_id and previous['status'] == 'in_review'
                    and value['state']['status'] == ('approved' if body.decision == 'approve' else 'returned')
                    and new_data['review']['actor']['id'] == command.actor_id
                    and new_data['review']['decision'] == body.decision
                    and new_data['review']['reason'] == body.reason
                    and new_data['submitted_revision_id'] == body.revision_id
                    and new_data['submitted_revision_hash'] == body.revision_hash)
            else:
                need(command.actor_id == row.author_id)
                expected = {'revise': ('draft', EDITABLE), 'submit': ('in_review', EDITABLE),
                    'withdraw': ('withdrawn', {'in_review'})}[command.operation]
                need(previous['status'] in expected[1] and value['state']['status'] == expected[0])
            if command.operation != 'revise':
                need(new_data['revision_ids'] == old_data['revision_ids']
                    and value['state']['head_revision_id'] == previous['head_revision_id'])
            if command.operation == 'submit':
                need(new_data['submitted_revision_id'] == body.revision_id
                    and new_data['submitted_revision_hash'] == body.revision_hash)
        previous = value['state']; results.append(value['result'])
    need(previous == authority(row))
    revisions = list(db.scalars(select(Revision).where(Revision.workspace_id == p.workspace_id,
        Revision.case_id == row.id).order_by(Revision.ordinal)))
    need(revisions and data.get('revision_ids') == [item.id for item in revisions])
    revision_events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == REVISION_AUDIT, m.Audit.data['case_id'].as_string() == row.id)))
    need(len(revision_events) == len(revisions) and {event.object_id for event in revision_events} == {item.id for item in revisions},
        '案例不可变修订与独立原库存不一致；未隐藏历史修订')
    parent = None
    for ordinal, revision in enumerate(revisions, 1):
        checked_revision(db, p, revision)
        need(revision.ordinal == ordinal and revision.actor_id == row.author_id
            and revision.parent_revision_id == (parent.id if parent else None)
            and revision.parent_revision_hash == (parent.revision_hash if parent else None)
            and revision.data['source_fingerprint'] == row.source_fingerprint)
        makers = [item for item in commands if item.request_id == revision.request_id]
        need(len(makers) == 1 and makers[0].operation == ('create' if ordinal == 1 else 'revise'))
        result = checked_command(db, p, makers[0])['result']
        body, _, _ = parse(makers[0].exact_body.encode(), makers[0].operation)
        need(result['revision_id'] == revision.id and result['revision_hash'] == revision.revision_hash
            and body.value.model_dump() == revision.data['value'])
        parent = revision
    need(row.head_revision_id == revisions[-1].id and len(revisions) <= 256)
    if verify_source:
        _, _, _, _, source = origin.accepted_source(db, p, row.task_id)
        need(source['source_fingerprint'] == row.source_fingerprint and source['memory_id'] == row.memory_id,
            '原已验收来源已发生变化，案例历史保留且未替换原依据')
    return revisions[-1], revisions


def negative(db, p, request_id):
    need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, REVISION_AUDIT]),
        m.Audit.data['request_id'].as_string() == request_id)) is None
        and db.scalar(select(Case.id).where(Case.workspace_id == p.workspace_id,
            Case.creation_request_id == request_id)) is None
        and db.scalar(select(Revision.id).where(Revision.workspace_id == p.workspace_id,
            Revision.request_id == request_id)) is None,
        '原案例请求已有独立保存痕迹但主记录缺失；未重建或换用新请求')


def lock_nonce(db, p, identity):
    key = int(digest(['layered-case-v209', p.workspace_id, identity])[:16], 16)
    if key >= 2 ** 63: key -= 2 ** 64
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def prior(db, p, request_id, raw=None, hashed=None, path=None):
    row = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id, Command.request_id == request_id))
    if row is None:
        negative(db, p, request_id); return None
    need(row.actor_id == p.user_id, '请由原操作账号核对这项案例请求', 403)
    need((hashed is None or row.body_sha256 == hashed) and (raw is None or row.exact_body == raw)
        and (path is None or row.path == path), '原案例请求不能更换正文、操作或账号')
    value = checked_command(db, p, row)
    case = db.scalar(select(Case).where(Case.workspace_id == p.workspace_id, Case.id == row.case_id))
    need(case is not None, '原案例命令已存在但案例主记录缺失；未重建')
    inventory(db, p, case)
    return copy.deepcopy(value['result'])


def permissions(db, p, row, managed, spec):
    allowed, reasons = {}, {}
    for action in ('revise', 'submit', 'withdraw', 'review'):
        try:
            require(p, 'admin', 'operator')
            origin.a.real_member(db, p, p.user_id, lock_sources=False)
            if action in {'revise', 'submit'}:
                need(p.user_id == row.author_id and row.status in EDITABLE, '请由案例作者在草稿、退回或撤回后办理', 403)
            if action == 'withdraw':
                need(p.user_id == row.author_id and row.status == 'in_review', '只有案例作者可撤回当前待审版本', 403)
            if action == 'submit':
                policy = origin.a.current_policy(db, p, managed.policy_id, lock_sources=False)
                _, current = origin.a.policy_value(db, p, policy, current=True)
                need(current['scope_id'] == spec['scope_id'], '原业务组织范围已改变，不能提交旧来源案例', 403)
            if action == 'review':
                need(row.status == 'in_review', '当前案例没有待审提交', 403)
                need(p.user_id != row.author_id, '案例审核必须由独立责任人员办理，管理员也不能自审', 403)
                origin.reviewer(db, p, managed, spec)
            allowed[action] = True
        except HTTPException as error:
            allowed[action] = False; reasons[action] = str(error.detail)
    return allowed, reasons


def revision_view(db, row):
    return {'id': row.id, 'ordinal': row.ordinal, 'revision_hash': row.revision_hash,
        'parent_revision_id': row.parent_revision_id, 'parent_revision_hash': row.parent_revision_hash,
        'value': copy.deepcopy(row.data['value']), 'source_fingerprint': row.data['source_fingerprint'],
        'created_at': origin.utc(row.created_at), 'created_by': origin.person(db, row.actor_id), 'reason': row.data['reason']}


def summary(db, row, revision):
    return {'id': row.id, 'task_id': row.task_id, 'title': revision.data['value']['title'],
        'version': row.version, 'status': row.status, 'author': origin.person(db, row.author_id),
        'created_at': origin.utc(row.created_at), 'revision_id': revision.id, 'revision_hash': revision.revision_hash}


def view(db, p, row, *, checked_inventory=False):
    if checked_inventory:
        head = scoped(db, Revision, row.head_revision_id, p)
    else: head, _ = inventory(db, p, row)
    _, managed, spec, _, _ = origin.accepted_source(db, p, row.task_id)
    data = checked(row); allowed, reasons = permissions(db, p, row, managed, spec)
    return {'schema': 'layered-case-view-v209', 'id': row.id, 'task_id': row.task_id,
        'version': row.version, 'status': row.status, 'author': origin.person(db, row.author_id),
        'created_at': origin.utc(row.created_at), 'source': copy.deepcopy(head.data['source']),
        'revision': revision_view(db, head), 'submitted_revision_id': data['submitted_revision_id'],
        'submitted_revision_hash': data['submitted_revision_hash'], 'review': copy.deepcopy(data['review']),
        'permissions': allowed, 'blocked_reasons': reasons, 'current_user_id': p.user_id, 'system_role': p.role}


def reverse_inventory(db, p, task_id=None):
    """A vanished Case/Command/Revision cannot become a new or empty list."""
    heads = select(Case.id).outerjoin(Revision, Revision.id == Case.head_revision_id).where(
        Case.workspace_id == p.workspace_id, or_(Revision.id.is_(None),
            Revision.case_id != Case.id, Revision.workspace_id != p.workspace_id))
    if task_id is not None: heads = heads.where(Case.task_id == task_id)
    need(db.scalar(heads.limit(1)) is None,
        '案例当前修订缺失或不属于原案例；未借用其他内容或从检索中隐藏')
    cases = select(Case.id).where(Case.workspace_id == p.workspace_id)
    command_ids = select(Command.id).where(Command.workspace_id == p.workspace_id)
    revision_ids = select(Revision.id).where(Revision.workspace_id == p.workspace_id)
    query = select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, REVISION_AUDIT]), or_(
            m.Audit.data['case_id'].as_string().not_in(cases),
            (m.Audit.action == COMMAND_AUDIT) & m.Audit.object_id.not_in(command_ids),
            (m.Audit.action == REVISION_AUDIT) & m.Audit.object_id.not_in(revision_ids)))
    if task_id is not None: query = query.where(m.Audit.data['task_id'].as_string() == task_id)
    need(db.scalar(query.limit(1)) is None, '已有案例独立保存痕迹但原库存缺失；未当作空列表或重新整理')


def context(db, p, task_id):
    require(p, 'admin', 'operator', 'viewer')
    task = scoped(db, m.Task, task_id, p)
    reverse_inventory(db, p, task.id)
    # An independent surviving Managed marker prevents a stripped Task marker
    # from masquerading as an unsupported old family.
    managed = origin.a.managed(db, p, task)
    if managed is not None:
        need(task.data.get('layered_approval_v175') == managed.id,
            '原任务永久审批标记与独立登记不一致；未降级为不支持来源')
    else:
        need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.object_id == task.id, m.Audit.action == 'layered_approval.registered')) is None,
            '原永久审批登记已有独立痕迹但主行缺失；未降级为不支持来源')
    reason, source = origin.support_reason(task), None
    if reason is None:
        try: _, _, _, _, source = origin.accepted_source(db, p, task.id)
        except HTTPException as error:
            if error.status_code != 422: raise
            reason = str(error.detail)
    rows = list(db.scalars(select(Case).where(Case.workspace_id == p.workspace_id, Case.task_id == task.id)
        .order_by(Case.created_at.desc(), Case.id.desc()).limit(257)))
    need(len(rows) <= 256, '本任务案例已达容量，请先核对已有案例，未隐藏历史')
    records = []
    for row in rows:
        head, _ = inventory(db, p, row); records.append(summary(db, row, head))
    can_create = False
    if source is not None:
        try:
            require(p, 'admin', 'operator'); origin.a.real_member(db, p, p.user_id, lock_sources=False)
            need(len(rows) < 256, '本任务案例已达容量', 403)
            can_create = True; reason = None
        except HTTPException as error: reason = str(error.detail)
    return {'schema': 'layered-case-context-v209', 'task_id': task.id, 'task_version': task.version,
        'source': source, 'source_fingerprint': source['source_fingerprint'] if source else None,
        'can_create': can_create, 'reason': reason, 'cases': records}


def new_revision(db, p, row, value, source, reason, request_id, prior=None):
    need(prior is None or prior.ordinal < 256, '案例修订已达容量，请保留原历史')
    revision = Revision(id=m.uid(), workspace_id=p.workspace_id, case_id=row.id, actor_id=p.user_id,
        ordinal=prior.ordinal + 1 if prior else 1, parent_revision_id=prior.id if prior else None,
        parent_revision_hash=prior.revision_hash if prior else None, request_id=request_id,
        created_at=m.now(), version=1, data={'schema': 'layered-case-revision-v209', 'value': value,
            'source': copy.deepcopy(source), 'source_fingerprint': row.source_fingerprint, 'reason': reason})
    revision.revision_hash = digest(revision_proof(revision)); db.add(revision)
    db.add(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=REVISION_AUDIT,
        object_id=revision.id, data={'case_id': row.id, 'revision_id': revision.id,
            'task_id': row.task_id, 'revision_hash': revision.revision_hash, 'request_id': request_id}))
    return revision


def record(db, p, row, revision, body, raw, hashed, operation, path, previous):
    command = Command(id=m.uid(), workspace_id=p.workspace_id, case_id=row.id, actor_id=p.user_id,
        request_id=body.request_id, path=path, operation=operation, exact_body=raw, body_sha256=hashed, version=1)
    data = checked(row); data['command_ids'].append(command.id); seal(row, data)
    state = authority(row)
    # Do not run reverse inventory while this same original transaction has
    # not yet saved its independent command.
    result = {'schema': 'layered-case-command-result-v209', 'request_id': body.request_id,
        'operation': operation, 'case_id': row.id, 'case_version': row.version,
        'revision_id': revision.id, 'revision_hash': revision.revision_hash, 'status': row.status,
        'case': view(db, p, row, checked_inventory=True)}
    seal(command, {'schema': 'layered-case-command-v209', 'result': result, 'result_hash': digest(result),
        'previous_state_hash': digest(previous) if previous is not None else None,
        'state': state, 'state_hash': digest(state)})
    db.add(command); db.flush()
    db.add(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=COMMAND_AUDIT,
        object_id=command.id, data=command_audit(command)))
    db.flush()
    return copy.deepcopy(result)


def create(db, p, task_id, raw_body):
    body, raw, hashed = parse(raw_body, 'create'); path = PREFIX + '/tasks/' + task_id + '/cases'
    lock_nonce(db, p, body.request_id)
    previous = prior(db, p, body.request_id, raw, hashed, path)
    if previous is not None: return previous
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    task = scoped(db, m.Task, task_id, p, True)
    reverse_inventory(db, p, task.id)
    task, _, _, _, source = origin.accepted_source(db, p, task.id)
    need(task.version == body.expected_source_task_version and source['source_fingerprint'] == body.source_fingerprint,
        '原任务版本或资料依据已变化，请保留输入并重新核对')
    count = len(list(db.scalars(select(Case.id).where(Case.workspace_id == p.workspace_id, Case.task_id == task.id))))
    need(count < 256, '本任务案例已达容量，未另建隐藏案例')
    row = Case(id=m.uid(), workspace_id=p.workspace_id, task_id=task.id, memory_id=source['memory_id'],
        author_id=p.user_id, source_fingerprint=body.source_fingerprint, head_revision_id=m.uid(),
        status='draft', creation_request_id=body.request_id, version=1, created_at=m.now())
    seal(row, {'schema': 'layered-case-state-v209', 'status': 'draft', 'source_fingerprint': body.source_fingerprint,
        'head_revision_id': row.head_revision_id, 'revision_ids': [], 'command_ids': [],
        'submitted_revision_id': None, 'submitted_revision_hash': None, 'review': None})
    db.add(row); db.flush()
    revision = new_revision(db, p, row, body.value.model_dump(), source, '作者整理原独立验收任务，尚待审核', body.request_id)
    row.head_revision_id = revision.id
    data = checked(row); data['head_revision_id'] = revision.id; data['revision_ids'] = [revision.id]; seal(row, data)
    db.flush()
    return record(db, p, row, revision, body, raw, hashed, 'create', path, None)


def perform(db, p, case_id, operation, raw_body):
    body, raw, hashed = parse(raw_body, operation); path = PREFIX + '/cases/' + case_id + '/' + operation
    lock_nonce(db, p, body.request_id)
    previous_result = prior(db, p, body.request_id, raw, hashed, path)
    if previous_result is not None: return previous_result
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    row = scoped(db, Case, case_id, p, True); head, _ = inventory(db, p, row)
    check_version(row, body.expected_case_version)
    need(head.id == body.revision_id and head.revision_hash == body.revision_hash,
        '当前案例修订已变化，请保留输入并重新核对原版本')
    _, managed, spec, _, _ = origin.accepted_source(db, p, row.task_id)
    allowed, reasons = permissions(db, p, row, managed, spec)
    need(allowed[operation], reasons.get(operation, '当前状态或岗位不允许此操作'), 403)
    previous = authority(row); data = checked(row)
    if operation == 'revise':
        head = new_revision(db, p, row, body.value.model_dump(), head.data['source'], body.reason, body.request_id, head)
        row.head_revision_id = head.id; row.status = 'draft'
        data['head_revision_id'] = head.id; data['revision_ids'].append(head.id)
        data['submitted_revision_id'] = data['submitted_revision_hash'] = None
        data['review'] = None
    elif operation == 'submit':
        policy = origin.a.current_policy(db, p, managed.policy_id, lock_sources=True)
        _, current = origin.a.policy_value(db, p, policy, current=True)
        need(current['scope_id'] == spec['scope_id'], '原业务组织范围已改变，不能提交旧来源案例', 403)
        row.status = 'in_review'; data['submitted_revision_id'] = head.id
        data['submitted_revision_hash'] = head.revision_hash; data['review'] = None
    elif operation == 'withdraw':
        row.status = 'withdrawn'
    elif operation == 'review':
        origin.reviewer(db, p, managed, spec, lock=True)
        need(data['submitted_revision_id'] == head.id and data['submitted_revision_hash'] == head.revision_hash,
            '待审内容与原提交版本不一致，不能替换审核依据')
        row.status = 'approved' if body.decision == 'approve' else 'returned'
        data['review'] = {'decision': body.decision, 'reason': body.reason, 'actor': origin.person(db, p.user_id),
            'at': origin.utc(m.now()), 'revision_id': head.id, 'revision_hash': head.revision_hash}
    else: need(False, '未知案例操作', 422)
    row.version += 1; data['status'] = row.status; seal(row, data); db.flush()
    return record(db, p, row, head, body, raw, hashed, operation, path, previous)


def recover(db, p, request_id, expected_sha256):
    require(p, 'admin', 'operator', 'viewer')
    result = prior(db, p, request_id, hashed=expected_sha256)
    return {'schema': 'layered-case-request-recovery-v209', 'found': result is not None,
        'request_id': request_id, 'body_sha256': expected_sha256, 'result': result}


def get_case(db, p, case_id):
    require(p, 'admin', 'operator', 'viewer')
    row = db.scalar(select(Case).where(Case.id == case_id, Case.workspace_id == p.workspace_id))
    if row is None:
        need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action.in_([COMMAND_AUDIT, REVISION_AUDIT]),
            m.Audit.data['case_id'].as_string() == case_id)) is None,
            '案例已有独立保存痕迹但主记录缺失；未当作新案例')
        raise HTTPException(404, '案例不存在或不在当前工作区')
    return view(db, p, row)


def revisions(db, p, case_id, offset):
    require(p, 'admin', 'operator', 'viewer')
    _, records = inventory(db, p, scoped(db, Case, case_id, p))
    records.reverse(); page = records[offset:offset + 21]
    return {'records': [revision_view(db, row) for row in page[:20]], 'offset': offset, 'has_more': len(page) > 20}


def search(db, p, q, offset):
    require(p, 'admin', 'operator', 'viewer')
    reverse_inventory(db, p)
    # SQL LIKE metacharacters are literal user text, never a query language.
    escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    query = select(Case).outerjoin(Revision, Revision.id == Case.head_revision_id).where(
        Case.workspace_id == p.workspace_id, Case.status == 'approved')
    if q:
        query = query.where(or_(Revision.id.is_(None), *[Revision.data['value'][field].as_string().ilike('%' + escaped + '%', escape='\\')
            for field in Value.model_fields]))
    rows = list(db.scalars(query.order_by(Case.created_at.desc(), Case.id.desc()).offset(offset).limit(21)))
    records = []
    for row in rows[:20]:
        head, _ = inventory(db, p, row); records.append(summary(db, row, head))
    return {'records': records, 'offset': offset, 'has_more': len(rows) > 20}
